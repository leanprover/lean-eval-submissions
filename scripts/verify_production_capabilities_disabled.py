#!/usr/bin/env python3
"""Materialize and verify disabled production configs from one exact Git commit."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

FULL_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
MAX_CONFIG_BYTES = 256 * 1024
INTAKE_PATH = "server/wrangler.jsonc"
REPLAY_PATH = "server/wrangler.replay.jsonc"

INTAKE_CAPABILITIES = {
    "INTAKE_ENABLED": "false",
    "INTAKE_ENABLEMENT_MODE": "disabled",
    "PROMOTION_CANARY_ENABLED": "false",
    "LEGACY_RESULT_OWNER_API_ENABLED": "false",
    "RESULT_AMENDMENT_OWNER_API_ENABLED": "false",
    "RESULT_AMENDMENT_MAINTAINER_API_ENABLED": "false",
    "MODEL_IDENTITY_OWNER_API_ENABLED": "false",
    "MODEL_IDENTITY_MAINTAINER_API_ENABLED": "false",
    "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED": "false",
    "RELEASE_OPT_OUT_API_ENABLED": "false",
}
REPLAY_CAPABILITIES = {
    "REPLAY_ENABLED": "false",
    "HISTORICAL_PUBLIC_REPLAY_ENABLED": "false",
    "STAGING_ACCEPTANCE_ENABLED": "false",
}


class VerificationError(ValueError):
    """The immutable production configuration is not exactly disabled."""


def _git(repository: pathlib.Path, *arguments: str) -> bytes:
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise VerificationError("cannot read the exact Git object") from error
    if len(result.stdout) > MAX_CONFIG_BYTES:
        raise VerificationError("exact Git object exceeds the size limit")
    return result.stdout


def read_exact_configs(
    repository: pathlib.Path, expected_commit: str
) -> tuple[bytes, bytes]:
    if FULL_COMMIT.fullmatch(expected_commit) is None:
        raise VerificationError("expected commit is not a full lowercase Git SHA")
    resolved = _git(
        repository, "rev-parse", "--verify", f"{expected_commit}^{{commit}}"
    )
    if resolved.decode("ascii", errors="strict").strip() != expected_commit:
        raise VerificationError("expected commit did not resolve exactly")
    intake = _git(repository, "cat-file", "blob", f"{expected_commit}:{INTAKE_PATH}")
    replay = _git(repository, "cat-file", "blob", f"{expected_commit}:{REPLAY_PATH}")
    return intake, replay


def _load_config(raw: bytes, label: str) -> dict[str, Any]:
    if not raw:
        raise VerificationError(f"{label} config is empty")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"{label} config is not valid JSON") from error
    if not isinstance(value, dict):
        raise VerificationError(f"{label} config is not an object")
    return value


def _production_vars(config: dict[str, Any], label: str) -> dict[str, Any]:
    try:
        production = config["env"]["production"]
        variables = production["vars"]
    except (KeyError, TypeError) as error:
        raise VerificationError(f"{label} production vars are missing") from error
    if not isinstance(production, dict) or not isinstance(variables, dict):
        raise VerificationError(f"{label} production vars are invalid")
    if variables.get("DEPLOYMENT_ENVIRONMENT") != "production":
        raise VerificationError(f"{label} production environment is not exact")
    return variables


def _capability_subset(variables: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value
        for name, value in variables.items()
        if isinstance(name, str) and name.endswith(("_ENABLED", "_ENABLEMENT_MODE"))
    }


def verify_disabled_configs(
    intake_config: dict[str, Any], replay_config: dict[str, Any]
) -> dict[str, dict[str, str]]:
    intake_vars = _production_vars(intake_config, "intake")
    replay_vars = _production_vars(replay_config, "replay")
    if _capability_subset(intake_vars) != INTAKE_CAPABILITIES:
        raise VerificationError(
            "intake production capabilities are not exactly disabled"
        )
    if _capability_subset(replay_vars) != REPLAY_CAPABILITIES:
        raise VerificationError(
            "replay production capabilities are not exactly disabled"
        )
    if any(
        isinstance(name, str) and name.startswith("INTAKE_LEASE_")
        for name in intake_vars
    ):
        raise VerificationError("intake production contains lease capability material")
    return {
        "intake": dict(INTAKE_CAPABILITIES),
        "replay": dict(REPLAY_CAPABILITIES),
    }


def _write_exclusive(path: pathlib.Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def materialize(
    repository: pathlib.Path, expected_commit: str, output_directory: pathlib.Path
) -> dict[str, Any]:
    if not output_directory.is_dir() or output_directory.is_symlink():
        raise VerificationError("output directory must be an existing real directory")
    intake_raw, replay_raw = read_exact_configs(repository, expected_commit)
    capabilities = verify_disabled_configs(
        _load_config(intake_raw, "intake"), _load_config(replay_raw, "replay")
    )
    _write_exclusive(output_directory / "intake.jsonc", intake_raw)
    _write_exclusive(output_directory / "replay.jsonc", replay_raw)
    report = {
        "schema_version": 1,
        "status": "production-disabled",
        "expected_commit": expected_commit,
        "capabilities": capabilities,
    }
    _write_exclusive(
        output_directory / "production-disabled.json",
        (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=pathlib.Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output-directory", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        report = materialize(
            args.repository.resolve(), args.expected_commit, args.output_directory
        )
    except (OSError, UnicodeError, VerificationError) as error:
        print(f"production-disabled: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
