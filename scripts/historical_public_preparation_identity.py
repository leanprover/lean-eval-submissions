#!/usr/bin/env python3
"""Bind prepared PUBLIC executor inputs while permitting unrelated State appends."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import sys

from historical_replay_controller import canonical_bytes, validate_execution_plan


def execution_identity(value: object) -> str:
    plan = copy.deepcopy(validate_execution_plan(value))
    # The selected task/profile/attempt and every other binding remain exact.
    # Only unrelated whole-State append metadata may change between jobs.
    del plan["queue"]["queue_source_event_count"]
    del plan["queue"]["queue_source_digest"]
    return hashlib.sha256(b"lean-eval-public-prepared-execution-v1\0" + canonical_bytes(plan)).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        print(execution_identity(json.loads(args.plan.read_bytes())))
    except (ValueError, OSError) as error:
        print(f"public executor preparation: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
