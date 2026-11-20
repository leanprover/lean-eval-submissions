#!/usr/bin/env python3
"""Closed, source-free handoff for the private replay's two trusted jobs."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
from typing import Any

import historical_private_replay_controller as c

START_JOB_SECONDS = 120 * 60
POST_SECONDS = 790  # 780s outer timeout + 5s forced kill + clock rounding
CLEANUP_SECONDS = 720
PREWARM_MAX_AGE_SECONDS = 45 * 60
SERVER_PROCESS_SECONDS = 20100
RECEIPT_GRACE_SECONDS = 120
OBSERVER_SECONDS = 360 * 60


def hash_value(value: Any) -> str:
    return c.sha256_bytes(c.canonical_bytes(value))


def budget(started: int, prewarm: int, now: int, *, admission: bool = False) -> None:
    if not 0 < started <= prewarm <= now:
        raise ValueError("private phase clock is invalid")
    # Before State admission leave 45 minutes for all remaining private setup,
    # the complete POST and a destruction/State tail. Before POST enforce the
    # prewarm age and job deadline again. No process limit is shortened.
    remaining = PREWARM_MAX_AGE_SECONDS if admission else 0
    if now + remaining + POST_SECONDS + CLEANUP_SECONDS > started + START_JOB_SECONDS:
        raise ValueError("private start phase has insufficient cleanup capacity")
    if now - prewarm > PREWARM_MAX_AGE_SECONDS:
        raise ValueError("private prewarm admission is too old")


def plan_at_commit(state: pathlib.Path, repository: pathlib.Path,
                   audit: pathlib.Path, commit: str, lane: int) -> dict[str, Any]:
    queue, raw, _ = c.load_state_queue_at_commit(
        state, commit, "leanprover/lean-eval-state",
    )
    tasks = [t for t in queue["tasks"] if c._task_is_in_lane(t, lane, 4)]
    if not tasks:
        raise ValueError("historical private lane was empty")
    task = tasks[0]
    authority, ar, profile, pr, _, ap = c._load_reviewed_input_context(repository, task)
    crosswalk = c.load_reviewed_crosswalk_entry(repository, audit, task)
    _, _, _, binding = c.load_archive_inputs(audit, task, crosswalk)
    return c._plan_next(queue, raw, commit, authority, ar, profile, pr, binding, ap,
                        lane_index=lane, lane_count=4)


def export(plan: dict[str, Any], candidate: dict[str, Any], status: dict[str, Any],
           *, workflow: str, run_id: int, run_attempt: int, prewarm: int) -> dict[str, Any]:
    plan = c.validate_execution_plan(plan)
    if run_attempt != 1 or type(run_id) is not int or run_id < 1 or prewarm < 1:
        raise ValueError("private handoff requires a first-attempt workflow")
    c._match(c.COMMIT, workflow, "handoff workflow SHA")
    c.validate_prewarm_request(plan, status)
    return {
        "schema_version": 1, "kind": "historical_private_observer_handoff",
        "workflow_sha": workflow, "run_id": run_id, "run_attempt": run_attempt,
        "lane_index": plan["state"]["lane_index"], "lane_count": 4,
        "prestart_state_commit": plan["state"]["expected_head"],
        "started_event_id": candidate["event"]["event_id"],
        "plan_sha256": hash_value(plan), "started_candidate_sha256": hash_value(candidate),
        "status": status, "status_sha256": hash_value(status),
        "prewarm_epoch": prewarm,
    }


def resume(envelope: Any, *, workflow: str, run_id: int, run_attempt: int,
           accepted_epoch: int, now: int, lane: int, state: pathlib.Path,
           repository: pathlib.Path, audit: pathlib.Path) -> dict[str, Any]:
    value = c._object(envelope, "private handoff")
    c._fields(value, {"schema_version", "kind", "workflow_sha", "run_id", "run_attempt",
        "lane_index", "lane_count", "prestart_state_commit", "started_event_id",
        "plan_sha256", "started_candidate_sha256", "status", "status_sha256",
        "prewarm_epoch"}, "private handoff")
    if (value["schema_version"] != 1 or value["kind"] != "historical_private_observer_handoff"
        or value["workflow_sha"] != workflow or value["run_id"] != run_id
        or value["run_attempt"] != run_attempt or run_attempt != 1
        or value["lane_index"] != lane or value["lane_count"] != 4
        or type(value["prewarm_epoch"]) is not int
        or type(accepted_epoch) is not int
        or not value["prewarm_epoch"] <= accepted_epoch <= now
        or accepted_epoch - value["prewarm_epoch"] > PREWARM_MAX_AGE_SECONDS + POST_SECONDS):
        raise ValueError("private handoff workflow or lifetime differs")
    c._match(c.COMMIT, workflow, "observer workflow SHA")
    c._verify_ancestor_of_upstream(repository, workflow, "observer workflow SHA")
    c.load_state_queue(state)  # Executes the exact protected State validator.
    running = c.current_historical_running(state / "events", state_validated=True,
                                          authority_event_type=c.AUTHORITY_EVENT_TYPE)
    selected = [t for t in running if c.private_replay_lane(t["replay_task_id"], 4) == lane]
    if len(selected) != 1:
        raise ValueError("observer requires the unique running private lane")
    event = selected[0]["event"]
    event_id = c._match(c.UUID7, event["event_id"], "running event ID")
    path = f"events/{event_id[:2]}/{event_id}.json"
    commits = c._git_text(state, "log", "--diff-filter=A", "--format=%H", "--", path).splitlines()
    if len(commits) != 1:
        raise ValueError("started event addition is ambiguous")
    parents = c._git_text(state, "rev-list", "--parents", "-n", "1", commits[0]).split()
    if len(parents) != 2:
        raise ValueError("started event is not a single-parent append")
    prestart = parents[1]
    if prestart != value["prestart_state_commit"] or event_id != value["started_event_id"]:
        raise ValueError("private handoff differs from protected started history")
    plan = plan_at_commit(state, repository, audit, prestart, lane)
    candidate = c._state_append_candidate(event, "production", prestart)
    c.validate_started_history(plan, candidate, state, lane, 4)
    status = c.prepare_prewarm_request(plan, runner_nonce=value["status"]["runner_nonce"])
    if (value["status"] != status or hash_value(status) != value["status_sha256"]
        or hash_value(plan) != value["plan_sha256"]
        or hash_value(candidate) != value["started_candidate_sha256"]):
        raise ValueError("reconstructed private handoff digests differ")
    deadline = accepted_epoch + SERVER_PROCESS_SECONDS + RECEIPT_GRACE_SECONDS
    # Queue time has already consumed remote runtime; never restart its clock.
    if max(0, deadline - now) + CLEANUP_SECONDS > OBSERVER_SECONDS:
        raise ValueError("observer cannot reserve full terminal cleanup tail")
    return {"plan": plan, "candidate": candidate, "status": status,
            "started_state_commit": commits[0], "poll_deadline_epoch": deadline}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("export", "resume", "admission", "before-post"))
    parser.add_argument("--state-root", type=pathlib.Path)
    parser.add_argument("--repository-root", type=pathlib.Path)
    parser.add_argument("--audit-root", type=pathlib.Path)
    args = parser.parse_args()
    temporary = pathlib.Path(os.environ["RUNNER_TEMP"])
    read = lambda name: json.loads((temporary / name).read_bytes())
    now = int(time.time())
    if args.command in {"admission", "before-post"}:
        started = int(os.environ["REPLAY_JOB_STARTED_MONOTONIC"])
        prewarm = int(os.environ["PRIVATE_PREWARM_MONOTONIC"])
        monotonic_now = int(time.monotonic())
        budget(started, prewarm, monotonic_now, admission=args.command == "admission")
        print(json.dumps({"phase": args.command,
                          "elapsed_seconds": monotonic_now - started,
                          "prewarm_age_seconds": monotonic_now - prewarm,
                          "job_remaining_seconds": started + START_JOB_SECONDS - monotonic_now,
                          "post_reserve_seconds": POST_SECONDS,
                          "cleanup_reserve_seconds": CLEANUP_SECONDS}, sort_keys=True))
    elif args.command == "export":
        value = export(read("private-replay-plan.json"), read("started-candidate.json"),
                       read("executor-status-request.json"), workflow=os.environ["GITHUB_SHA"],
                       run_id=int(os.environ["GITHUB_RUN_ID"]),
                       run_attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]),
                       prewarm=int(os.environ["PRIVATE_PREWARM_EPOCH"]))
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("handoff=" + json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    else:
        result = resume(json.loads(os.environ["PRIVATE_HANDOFF"]), workflow=os.environ["GITHUB_SHA"],
                        run_id=int(os.environ["GITHUB_RUN_ID"]),
                        run_attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]),
                        accepted_epoch=int(os.environ["PRIVATE_ACCEPTED_EPOCH"]), now=now,
                        lane=int(os.environ["PRIVATE_REPLAY_LANE_INDEX"]), state=args.state_root,
                        repository=args.repository_root, audit=args.audit_root)
        for name, value in [("private-replay-plan.json", result["plan"]),
                            ("started-candidate.json", result["candidate"]),
                            ("started-event.json", result["candidate"]["event"]),
                            ("executor-status-request.json", result["status"]),
                            ("cleanup-request.json", {"schema_version": 1,
                             "replay_task_id": result["status"]["replay_task_id"],
                             "attempt": result["status"]["attempt"]})]:
            c._write(temporary / name, value)
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            # The step timeout is rounded DOWN so even stuck running proofs
            # cannot consume the observer's separate twelve-minute tail.
            remaining = int(os.environ["REPLAY_JOB_STARTED_MONOTONIC"]) + OBSERVER_SECONDS - CLEANUP_SECONDS - int(time.monotonic())
            minutes = min(338, remaining // 60)
            if minutes < 1:
                raise ValueError("observer setup exhausted its cleanup reserve")
            output.write(f"appended=true\nstate_head={result['started_state_commit']}\npoll_timeout_minutes={minutes}\n")
        with open(os.environ["GITHUB_ENV"], "a") as output:
            output.write(f"PRIVATE_POLL_DEADLINE={result['poll_deadline_epoch']}\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, c.HistoricalPrivateReplayControllerError) as error:
        raise SystemExit(f"private phase refused: {type(error).__name__}") from None
