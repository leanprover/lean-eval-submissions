#!/usr/bin/env python3
"""Verify the public Cloudflare lifecycle deployment as one coherent unit."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable


MAX_RESPONSE_BYTES = 64 * 1024
FULL_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
ENDPOINTS = {
    "staging": {
        "intake": "https://lean-eval-submission-server-staging.lean-eval.workers.dev/healthz",
        "replay": "https://lean-eval-replay-executor-staging.lean-eval.workers.dev/healthz",
    },
    "production": {
        "intake": "https://lean-eval-submission-server.lean-eval.workers.dev/healthz",
        "replay": "https://lean-eval-replay-executor.lean-eval.workers.dev/healthz",
    },
}


class MonitorError(ValueError):
    """The public deployment is unavailable or not commit-coherent."""


def load_object(path: pathlib.Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            raise MonitorError(f"configuration {path} has an invalid size")
        value = json.loads(raw)
    except MonitorError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise MonitorError(f"configuration {path} is invalid") from error
    if not isinstance(value, dict):
        raise MonitorError(f"configuration {path} is not an object")
    return value


def fetch_health(url: str, timeout: float = 10) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "lean-eval-lifecycle-readiness-monitor/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_RESPONSE_BYTES:
                raise MonitorError("health response exceeds its size limit")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise MonitorError("health endpoint request failed") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise MonitorError("health response exceeds its size limit")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise MonitorError("health endpoint did not return JSON") from error
    if not isinstance(value, dict):
        raise MonitorError("health endpoint did not return an object")
    return value


def expected_health(
    intake_config: dict[str, Any], replay_config: dict[str, Any], environment: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        intake_vars = intake_config["env"][environment]["vars"]
        replay_vars = replay_config["env"][environment]["vars"]
        intake_enabled = json.loads(intake_vars["INTAKE_ENABLED"])
        replay_enabled = json.loads(replay_vars["REPLAY_ENABLED"])
        staging_enabled = json.loads(replay_vars["STAGING_ACCEPTANCE_ENABLED"])
        memory = int(replay_vars["STAGING_MEMORY_LIMIT_BYTES"])
        gate = int(replay_vars["PRODUCTION_MEMORY_GATE_BYTES"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise MonitorError(f"tracked {environment} health contract is invalid") from error
    if not all(type(value) is bool for value in (intake_enabled, replay_enabled, staging_enabled)):
        raise MonitorError(f"tracked {environment} enablement is not boolean")
    intake = {
        "status": "ok",
        "environment": environment,
        "intake_enabled": intake_enabled,
    }
    replay = {
        "status": "ok",
        "service": "lean-eval-replay-executor",
        "environment": environment,
        "replay_enabled": replay_enabled,
        "staging_acceptance_enabled": staging_enabled,
        "staging_memory_limit_bytes": memory,
        "production_memory_gate_bytes": gate,
        "reviewed_execution_profile_digest": replay_vars[
            "REVIEWED_EXECUTION_PROFILE_DIGEST"
        ],
        "reviewed_measurement_config_digest": replay_vars[
            "REVIEWED_MEASUREMENT_CONFIG_DIGEST"
        ],
        "reviewed_vm_image_digest": replay_vars["REVIEWED_VM_IMAGE_DIGEST"],
    }
    return intake, replay


def verify_snapshot(
    intake_config: dict[str, Any],
    replay_config: dict[str, Any],
    fetcher: Callable[[str], dict[str, Any]] = fetch_health,
) -> dict[str, Any]:
    observations: dict[str, Any] = {}
    commits: set[str] = set()
    for environment in ("staging", "production"):
        expected_intake, expected_replay = expected_health(
            intake_config, replay_config, environment
        )
        observations[environment] = {}
        for component, expected in (
            ("intake", expected_intake),
            ("replay", expected_replay),
        ):
            health = fetcher(ENDPOINTS[environment][component])
            wrong = {
                key: {"expected": value, "actual": health.get(key)}
                for key, value in expected.items()
                if health.get(key) != value
            }
            commit = health.get("deployed_commit")
            if not isinstance(commit, str) or FULL_COMMIT.fullmatch(commit) is None:
                wrong["deployed_commit"] = {
                    "expected": "full lowercase Git SHA",
                    "actual": "invalid",
                }
            if wrong:
                raise MonitorError(
                    f"{environment} {component} health differs: "
                    + json.dumps(wrong, sort_keys=True)
                )
            commits.add(commit)
            observations[environment][component] = {
                "deployed_commit": commit,
                "enabled": expected.get(
                    "intake_enabled", expected.get("replay_enabled")
                ),
            }
    if len(commits) != 1:
        raise MonitorError("Cloudflare components do not share one deployed commit")
    return {
        "schema_version": 1,
        "status": "ready",
        "deployed_commit": next(iter(commits)),
        "observations": observations,
    }


def write_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intake-config", required=True, type=pathlib.Path)
    parser.add_argument("--replay-config", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--interval-seconds", type=float, default=10)
    args = parser.parse_args()
    if not 1 <= args.attempts <= 12 or not 0 <= args.interval_seconds <= 30:
        print("monitor: retry settings are invalid", file=sys.stderr)
        return 1
    try:
        intake = load_object(args.intake_config)
        replay = load_object(args.replay_config)
    except MonitorError as error:
        print(f"monitor: {error}", file=sys.stderr)
        return 1
    last_error = "monitor did not run"
    for attempt in range(1, args.attempts + 1):
        try:
            report = verify_snapshot(intake, replay)
            write_exclusive(args.output, report)
            return 0
        except MonitorError as error:
            last_error = str(error)
            if attempt < args.attempts:
                time.sleep(args.interval_seconds)
    try:
        write_exclusive(
            args.output,
            {"schema_version": 1, "status": "failed", "reason": last_error},
        )
    except OSError:
        pass
    print(f"monitor: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
