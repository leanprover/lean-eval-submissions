#!/usr/bin/env python3
"""Read one reviewed Worker's tracked lifecycle launch state.

The launch families are independently enforced by the Worker, but production
rollout deliberately changes them as one bounded capability set.  This parser
keeps that tracked rollout state closed and keeps model consolidation off.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any


MAX_CONFIG_BYTES = 1024 * 1024
ENVIRONMENTS = {"staging", "production"}
LAUNCH_FLAGS = (
    "LEGACY_RESULT_OWNER_API_ENABLED",
    "RESULT_AMENDMENT_OWNER_API_ENABLED",
    "RESULT_AMENDMENT_MAINTAINER_API_ENABLED",
    "MODEL_IDENTITY_OWNER_API_ENABLED",
    "MODEL_IDENTITY_MAINTAINER_API_ENABLED",
    "RELEASE_OPT_IN_API_ENABLED",
)
MAINTAINER_VARIABLES = (
    "RESULT_AMENDMENT_MAINTAINERS",
    "MODEL_IDENTITY_MAINTAINERS",
)
LOGIN = re.compile(r"[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?\Z")
MAX_SAFE_INTEGER = 9_007_199_254_740_991


class LifecycleConfigurationError(ValueError):
    """The tracked Worker configuration cannot define a launch state."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise LifecycleConfigurationError(
                f"configuration contains duplicate key {name!r}"
            )
        result[name] = value
    return result


def _reject_nonstandard_number(value: str) -> None:
    raise LifecycleConfigurationError(
        f"configuration contains non-standard number {value!r}"
    )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LifecycleConfigurationError(f"{label} must be an object")
    return value


def _maintainers(raw: object, expected_length: int, label: str) -> None:
    if not isinstance(raw, str):
        raise LifecycleConfigurationError(f"{label} must be canonical JSON text")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonstandard_number,
        )
    except LifecycleConfigurationError:
        raise
    except json.JSONDecodeError as error:
        raise LifecycleConfigurationError(f"{label} is not valid JSON") from error
    if not isinstance(value, list) or len(value) != expected_length:
        raise LifecycleConfigurationError(
            f"{label} must contain exactly {expected_length} maintainer identities"
        )
    for identity in value:
        if not isinstance(identity, dict) or set(identity) != {"github_id", "login"}:
            raise LifecycleConfigurationError(f"{label} is not a closed maintainer array")
        github_id = identity["github_id"]
        login = identity["login"]
        if (
            isinstance(github_id, bool)
            or not isinstance(github_id, int)
            or not 1 <= github_id <= MAX_SAFE_INTEGER
            or not isinstance(login, str)
            or LOGIN.fullmatch(login) is None
        ):
            raise LifecycleConfigurationError(
                f"{label} contains an invalid maintainer identity"
            )
    canonical = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
    if raw != canonical:
        raise LifecycleConfigurationError(f"{label} must use canonical JSON text")


def read_lifecycle_state(path: pathlib.Path, environment: str) -> str:
    if environment not in ENVIRONMENTS:
        raise LifecycleConfigurationError("environment is not registered")
    if not path.is_file() or path.is_symlink():
        raise LifecycleConfigurationError("configuration is not one regular file")
    try:
        raw = path.read_bytes()
        if not raw:
            raise LifecycleConfigurationError("configuration is empty")
        if len(raw) > MAX_CONFIG_BYTES:
            raise LifecycleConfigurationError("configuration exceeds its size limit")
        configuration = _object(
            json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_nonstandard_number,
            ),
            "configuration",
        )
    except LifecycleConfigurationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LifecycleConfigurationError(
            "configuration is not valid UTF-8 JSON"
        ) from error

    environments = _object(configuration.get("env"), "configuration env")
    selected = _object(
        environments.get(environment), f"configuration env {environment}"
    )
    variables = _object(selected.get("vars"), f"configuration env {environment} vars")
    if variables.get("DEPLOYMENT_ENVIRONMENT") != environment:
        raise LifecycleConfigurationError("deployment environment binding does not match")

    states = {variables.get(name) for name in LAUNCH_FLAGS}
    if len(states) != 1 or not states.issubset({"false", "true"}):
        raise LifecycleConfigurationError(
            "launch lifecycle flags must all be the same canonical boolean text"
        )
    state = states.pop()
    if not isinstance(state, str):
        raise LifecycleConfigurationError("launch lifecycle state is invalid")
    if variables.get("MODEL_IDENTITY_CONSOLIDATION_API_ENABLED") != "false":
        raise LifecycleConfigurationError(
            "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED must remain false"
        )
    if variables.get("RELEASE_OPT_OUT_API_ENABLED") != "false":
        raise LifecycleConfigurationError(
            "RELEASE_OPT_OUT_API_ENABLED must remain false"
        )
    expected_maintainers = 1 if state == "true" else 0
    for name in MAINTAINER_VARIABLES:
        _maintainers(variables.get(name), expected_maintainers, name)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--environment", required=True, choices=sorted(ENVIRONMENTS))
    args = parser.parse_args()
    try:
        print(read_lifecycle_state(args.config, args.environment))
    except LifecycleConfigurationError as error:
        print(f"worker-lifecycle-configuration: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
