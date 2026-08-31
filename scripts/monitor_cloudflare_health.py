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
from collections.abc import Callable
from typing import Any

MAX_RESPONSE_BYTES = 64 * 1024
FULL_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
FULL_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
VM_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
WORKER_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
POSITIVE_DECIMAL = re.compile(r"[1-9][0-9]{0,15}\Z")
WORKERS_DEV_DOMAIN = "lean-eval.workers.dev"
MAX_SAFE_INTEGER = 9_007_199_254_740_991


class MonitorError(ValueError):
    """The public deployment is unavailable or not commit-coherent."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


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
        opener = urllib.request.build_opener(_RejectRedirects)
        with opener.open(request, timeout=timeout) as response:
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


def _environment_config(
    config: dict[str, Any], environment: str, label: str
) -> tuple[dict[str, Any], str, str]:
    try:
        service = config["name"]
        selected = config["env"][environment]
        worker = selected["name"]
        workers_dev = selected["workers_dev"]
        variables = selected["vars"]
    except (KeyError, TypeError) as error:
        raise MonitorError(f"tracked {environment} {label} endpoint is invalid") from error
    if (
        not isinstance(service, str)
        or WORKER_NAME.fullmatch(service) is None
        or not isinstance(worker, str)
        or WORKER_NAME.fullmatch(worker) is None
        or workers_dev is not True
        or not isinstance(variables, dict)
        or variables.get("DEPLOYMENT_ENVIRONMENT") != environment
    ):
        raise MonitorError(f"tracked {environment} {label} endpoint is invalid")
    return variables, service, f"https://{worker}.{WORKERS_DEV_DOMAIN}/healthz"


def _boolean_variable(variables: dict[str, Any], name: str, label: str) -> bool:
    value = variables.get(name)
    if not isinstance(value, str) or value not in {"true", "false"}:
        raise MonitorError(f"tracked {label} {name} is not canonical boolean text")
    return value == "true"


def _positive_integer_variable(
    variables: dict[str, Any], name: str, label: str
) -> int:
    value = variables.get(name)
    if not isinstance(value, str) or POSITIVE_DECIMAL.fullmatch(value) is None:
        raise MonitorError(f"tracked {label} {name} is not a positive decimal")
    number = int(value)
    if number > MAX_SAFE_INTEGER:
        raise MonitorError(f"tracked {label} {name} exceeds the safe integer range")
    return number


def _digest_variable(
    variables: dict[str, Any], name: str, pattern: re.Pattern[str], label: str
) -> str:
    value = variables.get(name)
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise MonitorError(f"tracked {label} {name} is not a canonical digest")
    return value


def expected_health(
    intake_config: dict[str, Any], replay_config: dict[str, Any], environment: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        intake_vars, intake_service, _ = _environment_config(
            intake_config, environment, "intake"
        )
        replay_vars, replay_service, _ = _environment_config(
            replay_config, environment, "replay"
        )
    except MonitorError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise MonitorError(f"tracked {environment} health contract is invalid") from error
    intake_enabled = _boolean_variable(intake_vars, "INTAKE_ENABLED", environment)
    lifecycle_enabled = {
        name: _boolean_variable(intake_vars, name, environment)
        for name in (
            "LEGACY_RESULT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_MAINTAINER_API_ENABLED",
            "MODEL_IDENTITY_OWNER_API_ENABLED",
            "MODEL_IDENTITY_MAINTAINER_API_ENABLED",
            "RELEASE_OPT_IN_API_ENABLED",
            "RELEASE_OPT_OUT_API_ENABLED",
        )
    }
    model_consolidation_enabled = _boolean_variable(
        intake_vars, "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED", environment
    )
    if model_consolidation_enabled:
        raise MonitorError(
            f"tracked {environment} model consolidation must remain disabled"
        )
    promotion_canary_enabled = _boolean_variable(
        intake_vars, "PROMOTION_CANARY_ENABLED", environment
    )
    intake_mode = intake_vars.get("INTAKE_ENABLEMENT_MODE")
    expected_intake_mode = "durable" if intake_enabled else "disabled"
    if intake_mode != expected_intake_mode:
        raise MonitorError(
            f"tracked {environment} INTAKE_ENABLEMENT_MODE must be {expected_intake_mode}"
        )
    if any(name.startswith("INTAKE_LEASE_") for name in intake_vars):
        raise MonitorError(f"tracked {environment} configuration contains intake lease material")
    replay_enabled = _boolean_variable(replay_vars, "REPLAY_ENABLED", environment)
    staging_enabled = _boolean_variable(
        replay_vars, "STAGING_ACCEPTANCE_ENABLED", environment
    )
    memory = _positive_integer_variable(
        replay_vars, "STAGING_MEMORY_LIMIT_BYTES", environment
    )
    gate = _positive_integer_variable(
        replay_vars, "PRODUCTION_MEMORY_GATE_BYTES", environment
    )
    intake = {
        "status": "ok",
        "service": intake_service,
        "environment": environment,
        "intake_configured_enabled": intake_enabled,
        "intake_effective_enabled": intake_enabled,
        "intake_enabled": intake_enabled,
        "promotion_canary_configured_enabled": promotion_canary_enabled,
        "promotion_canary_enabled": (
            environment == "staging" and promotion_canary_enabled
        ),
        "intake_enablement_mode": intake_mode,
        "intake_lease_expires_at": None,
        "legacy_result_owner_api_enabled": lifecycle_enabled[
            "LEGACY_RESULT_OWNER_API_ENABLED"
        ],
        "result_amendment_owner_api_enabled": lifecycle_enabled[
            "RESULT_AMENDMENT_OWNER_API_ENABLED"
        ],
        "result_amendment_maintainer_api_enabled": lifecycle_enabled[
            "RESULT_AMENDMENT_MAINTAINER_API_ENABLED"
        ],
        "model_identity_owner_api_enabled": lifecycle_enabled[
            "MODEL_IDENTITY_OWNER_API_ENABLED"
        ],
        "model_identity_maintainer_api_enabled": lifecycle_enabled[
            "MODEL_IDENTITY_MAINTAINER_API_ENABLED"
        ],
        "model_identity_consolidation_api_enabled": False,
        "model_identity_write_max_subrequests": 400,
        "model_identity_consolidation_api": "atomic_reverse_impact_v1",
        "release_opt_in_api_enabled": lifecycle_enabled[
            "RELEASE_OPT_IN_API_ENABLED"
        ],
        "release_opt_out_api_enabled": lifecycle_enabled[
            "RELEASE_OPT_OUT_API_ENABLED"
        ],
    }
    replay = {
        "status": "ok",
        "service": replay_service,
        "environment": environment,
        "replay_enabled": replay_enabled,
        "historical_public_replay_enabled": _boolean_variable(
            replay_vars, "HISTORICAL_PUBLIC_REPLAY_ENABLED", environment
        ),
        "staging_acceptance_enabled": staging_enabled,
        "staging_memory_limit_bytes": memory,
        "production_memory_gate_bytes": gate,
        "reviewed_execution_profile_digest": _digest_variable(
            replay_vars,
            "REVIEWED_EXECUTION_PROFILE_DIGEST",
            FULL_DIGEST,
            environment,
        ),
        "reviewed_measurement_config_digest": _digest_variable(
            replay_vars,
            "REVIEWED_MEASUREMENT_CONFIG_DIGEST",
            FULL_DIGEST,
            environment,
        ),
        "reviewed_vm_image_digest": _digest_variable(
            replay_vars, "REVIEWED_VM_IMAGE_DIGEST", VM_DIGEST, environment
        ),
    }
    return intake, replay


def tracked_endpoints(
    intake_config: dict[str, Any], replay_config: dict[str, Any]
) -> dict[str, dict[str, str]]:
    endpoints: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for environment in ("staging", "production"):
        _, _, intake_url = _environment_config(
            intake_config, environment, "intake"
        )
        _, _, replay_url = _environment_config(
            replay_config, environment, "replay"
        )
        endpoints[environment] = {"intake": intake_url, "replay": replay_url}
        seen.update((intake_url, replay_url))
    if len(seen) != 4:
        raise MonitorError("tracked Worker health endpoints are not unique")
    return endpoints


def verify_snapshot(
    intake_config: dict[str, Any],
    replay_config: dict[str, Any],
    fetcher: Callable[[str], dict[str, Any]] = fetch_health,
) -> dict[str, Any]:
    observations: dict[str, Any] = {}
    commits: set[str] = set()
    endpoints = tracked_endpoints(intake_config, replay_config)
    for environment in ("staging", "production"):
        expected_intake, expected_replay = expected_health(
            intake_config, replay_config, environment
        )
        observations[environment] = {}
        for component, expected in (
            ("intake", expected_intake),
            ("replay", expected_replay),
        ):
            health = fetcher(endpoints[environment][component])
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
        if environment == "production":
            observations[environment]["capabilities"] = {
                "historical_public_replay_enabled": expected_replay[
                    "historical_public_replay_enabled"
                ],
                "intake_enabled": expected_intake["intake_enabled"],
                "legacy_result_owner_api_enabled": expected_intake[
                    "legacy_result_owner_api_enabled"
                ],
                "model_identity_consolidation_api_enabled": expected_intake[
                    "model_identity_consolidation_api_enabled"
                ],
                "model_identity_maintainer_api_enabled": expected_intake[
                    "model_identity_maintainer_api_enabled"
                ],
                "model_identity_owner_api_enabled": expected_intake[
                    "model_identity_owner_api_enabled"
                ],
                "promotion_canary_enabled": expected_intake["promotion_canary_enabled"],
                "release_opt_in_api_enabled": expected_intake[
                    "release_opt_in_api_enabled"
                ],
                "release_opt_out_api_enabled": expected_intake[
                    "release_opt_out_api_enabled"
                ],
                "replay_enabled": expected_replay["replay_enabled"],
                "result_amendment_maintainer_api_enabled": expected_intake[
                    "result_amendment_maintainer_api_enabled"
                ],
                "result_amendment_owner_api_enabled": expected_intake[
                    "result_amendment_owner_api_enabled"
                ],
                "staging_acceptance_enabled": expected_replay[
                    "staging_acceptance_enabled"
                ],
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
