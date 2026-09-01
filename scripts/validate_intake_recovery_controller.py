#!/usr/bin/env python3
"""Validate the exact controller eligible for production intake recovery."""

from __future__ import annotations

import argparse
import re


COMMIT = re.compile(r"^[0-9a-f]{40}$")
POSITIVE = re.compile(r"^[1-9][0-9]*$")
FAILED = {"cancelled", "failure", "startup_failure", "timed_out"}
TERMINAL = FAILED | {"success"}


def validate(args: argparse.Namespace) -> None:
    if not COMMIT.fullmatch(args.live_commit):
        raise ValueError("live commit is malformed")
    if not POSITIVE.fullmatch(args.controller_run_id):
        raise ValueError("controller run ID is malformed")
    if not POSITIVE.fullmatch(args.controller_run_attempt):
        raise ValueError("controller run attempt is malformed")
    if args.controller_commit != args.live_commit:
        raise ValueError("controller is not bound to the exact live intake commit")
    if args.controller_status != "completed" or args.controller_conclusion not in TERMINAL:
        raise ValueError("controller is not an eligible terminal deployment run")

    trigger = (
        args.trigger_run_id,
        args.trigger_run_attempt,
        args.trigger_commit,
        args.trigger_conclusion,
    )
    latest = (
        args.latest_run_id,
        args.latest_run_attempt,
        args.latest_commit,
        args.latest_status,
        args.latest_conclusion,
    )
    if args.event_name == "workflow_dispatch":
        if any(value is not None for value in (*trigger, *latest)):
            raise ValueError("manual recovery must not carry automatic run identity")
        return

    if any(value is None for value in (*trigger, *latest)):
        raise ValueError("automatic recovery run identity is incomplete")
    if not POSITIVE.fullmatch(args.trigger_run_id):
        raise ValueError("trigger run ID is malformed")
    if not POSITIVE.fullmatch(args.trigger_run_attempt):
        raise ValueError("trigger run attempt is malformed")
    if not COMMIT.fullmatch(args.trigger_commit):
        raise ValueError("trigger commit is malformed")
    if args.trigger_conclusion not in FAILED:
        raise ValueError("automatic trigger is not a failed deployment")
    if not POSITIVE.fullmatch(args.latest_run_id):
        raise ValueError("latest run ID is malformed")
    if not POSITIVE.fullmatch(args.latest_run_attempt):
        raise ValueError("latest run attempt is malformed")
    if not COMMIT.fullmatch(args.latest_commit):
        raise ValueError("latest run commit is malformed")
    if args.latest_status != "completed" or args.latest_conclusion not in TERMINAL:
        raise ValueError("globally latest deployment is not terminal")
    if (
        args.latest_run_id,
        args.latest_run_attempt,
        args.latest_commit,
        args.latest_conclusion,
    ) != trigger:
        raise ValueError(
            "globally latest deployment is not the exact failed workflow_run trigger"
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--event-name", choices=("workflow_dispatch", "workflow_run"), required=True)
    result.add_argument("--live-commit", required=True)
    result.add_argument("--controller-run-id", required=True)
    result.add_argument("--controller-run-attempt", required=True)
    result.add_argument("--controller-commit", required=True)
    result.add_argument("--controller-status", required=True)
    result.add_argument("--controller-conclusion", required=True)
    result.add_argument("--latest-run-id")
    result.add_argument("--latest-run-attempt")
    result.add_argument("--latest-commit")
    result.add_argument("--latest-status")
    result.add_argument("--latest-conclusion")
    result.add_argument("--trigger-run-id")
    result.add_argument("--trigger-run-attempt")
    result.add_argument("--trigger-commit")
    result.add_argument("--trigger-conclusion")
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        validate(args)
    except ValueError as error:
        raise SystemExit(f"intake recovery controller validation failed: {error}") from error


if __name__ == "__main__":
    main()
