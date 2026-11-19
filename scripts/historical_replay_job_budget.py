#!/usr/bin/env python3
"""Fail closed before starting public replay without its full execution budget."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

# Keep the existing six-hour job, twelve-minute cleanup/State tail, and
# five-minute executor delivery/poll grace. No measurement limit is shortened.
JOB_TIMEOUT_SECONDS = 360 * 60
CLEANUP_SECONDS = 12 * 60
POLL_GRACE_SECONDS = 5 * 60
# State publication/request/OIDC time is a conservative allowance rather than
# a guarantee against arbitrary network stalls. POST time is separately
# bounded by the workflow's outer timeout and forced-kill grace.
STATE_START_ALLOWANCE_SECONDS = 60
POST_TIMEOUT_SECONDS = 300
POST_KILL_GRACE_SECONDS = 5
POST_ALLOWANCE_SECONDS = 310
DEFERRED_EXIT_STATUS = 3


def capacity(
    *, job_started_epoch: int, now_epoch: int,
    wall_time_limit_ms: int, phase: str,
) -> dict[str, int | bool | str]:
    if phase not in {"before-start", "before-execute"}:
        raise ValueError("unknown replay budget phase")
    if (
        type(job_started_epoch) is not int or job_started_epoch < 0
        or type(now_epoch) is not int or now_epoch < job_started_epoch
        or type(wall_time_limit_ms) is not int or wall_time_limit_ms <= 0
    ):
        raise ValueError("invalid replay budget clock or wall time")
    wall_seconds = (wall_time_limit_ms + 999) // 1000
    startup_seconds = POST_ALLOWANCE_SECONDS
    if phase == "before-start":
        startup_seconds += STATE_START_ALLOWANCE_SECONDS
    poll_deadline = job_started_epoch + JOB_TIMEOUT_SECONDS - CLEANUP_SECONDS
    required = wall_seconds + POLL_GRACE_SECONDS + startup_seconds
    remaining = poll_deadline - now_epoch
    return {
        "phase": phase,
        "setup_elapsed_seconds": now_epoch - job_started_epoch,
        "wall_time_limit_ms": wall_time_limit_ms,
        "poll_grace_seconds": POLL_GRACE_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS,
        "start_allowance_seconds": startup_seconds,
        "poll_deadline_epoch": poll_deadline,
        "required_seconds": required,
        "remaining_seconds": remaining,
        "fits": remaining >= required,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=pathlib.Path, required=True)
    parser.add_argument("--job-started-epoch", type=int, required=True)
    parser.add_argument("--phase", choices=("before-start", "before-execute"), required=True)
    args = parser.parse_args()
    # This is the already validated execution plan, never a replacement limit.
    from historical_replay_controller import validate_execution_plan

    try:
        plan = validate_execution_plan(json.loads(args.plan.read_bytes()))
        result = capacity(
            job_started_epoch=args.job_started_epoch,
            now_epoch=int(time.time()),
            wall_time_limit_ms=plan["measurement_config"]["wall_time_limit_ms"],
            phase=args.phase,
        )
    except (ValueError, OSError) as error:
        print(f"historical replay budget: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    if not result["fits"]:
        print("historical replay budget: full reviewed execution budget no longer fits", file=sys.stderr)
        return DEFERRED_EXIT_STATUS
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
