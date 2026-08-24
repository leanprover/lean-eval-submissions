#!/usr/bin/env python3
"""Read one reviewed Worker environment's tracked intake state.

The tracked ``wrangler.jsonc`` is deliberately restricted to strict JSON so
the reviewed value and the value used by deployment automation have one small,
fail-closed parser contract.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

MAX_CONFIG_BYTES = 1024 * 1024
ENVIRONMENTS = {"staging", "production"}
LEASE_VARIABLES = {
    "INTAKE_LEASE_CONTROLLER_COMMIT",
    "INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT",
    "INTAKE_LEASE_CONTROLLER_RUN_ID",
    "INTAKE_LEASE_EVENT_ID",
    "INTAKE_LEASE_EXPIRES_AT",
    "INTAKE_LEASE_ISSUED_AT",
    "INTAKE_LEASE_NONCE_DIGEST",
    "INTAKE_LEASE_STATE_COMMIT",
    "INTAKE_LEASE_TARGET_COMMIT",
}


class IntakeConfigurationError(ValueError):
    """The tracked Worker configuration cannot define an expected state."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise IntakeConfigurationError(
                f"configuration contains duplicate key {name!r}"
            )
        result[name] = value
    return result


def _reject_nonstandard_number(value: str) -> None:
    raise IntakeConfigurationError(
        f"configuration contains non-standard number {value!r}"
    )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IntakeConfigurationError(f"{label} must be an object")
    return value


def read_intake_state(path: pathlib.Path, environment: str) -> str:
    if environment not in ENVIRONMENTS:
        raise IntakeConfigurationError("environment is not registered")
    if not path.is_file() or path.is_symlink():
        raise IntakeConfigurationError("configuration is not one regular file")
    try:
        raw = path.read_bytes()
        if not raw:
            raise IntakeConfigurationError("configuration is empty")
        if len(raw) > MAX_CONFIG_BYTES:
            raise IntakeConfigurationError("configuration exceeds its size limit")
        configuration = _object(
            json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_nonstandard_number,
            ),
            "configuration",
        )
    except IntakeConfigurationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IntakeConfigurationError("configuration is not valid UTF-8 JSON") from error

    environments = _object(configuration.get("env"), "configuration env")
    selected = _object(
        environments.get(environment),
        f"configuration env {environment}",
    )
    variables = _object(selected.get("vars"), f"configuration env {environment} vars")
    if variables.get("DEPLOYMENT_ENVIRONMENT") != environment:
        raise IntakeConfigurationError("deployment environment binding does not match")
    state = variables.get("INTAKE_ENABLED")
    if not isinstance(state, str) or state not in {"false", "true"}:
        raise IntakeConfigurationError("INTAKE_ENABLED must be the string false or true")
    expected_mode = "durable" if state == "true" else "disabled"
    if variables.get("INTAKE_ENABLEMENT_MODE") != expected_mode:
        raise IntakeConfigurationError(
            f"INTAKE_ENABLEMENT_MODE must be {expected_mode} when INTAKE_ENABLED is {state}"
        )
    tracked_lease_variables = sorted(LEASE_VARIABLES.intersection(variables))
    if tracked_lease_variables:
        raise IntakeConfigurationError(
            "tracked configuration must not contain intake lease variables: "
            + ", ".join(tracked_lease_variables)
        )
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--environment", required=True, choices=sorted(ENVIRONMENTS))
    args = parser.parse_args()
    try:
        print(read_intake_state(args.config, args.environment))
    except IntakeConfigurationError as error:
        print(f"worker-intake-configuration: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
