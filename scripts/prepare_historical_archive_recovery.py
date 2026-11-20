#!/usr/bin/env python3
"""Create local recovery artifacts/candidates; never append, unwrap, or execute.

Crosswalk and manifest preparation is prospective. Only the events command
proves committed artifacts against the protected original queue. Its output
still requires independent review, one atomic State Git commit and promotion.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

import historical_private_replay_controller as archive
import historical_replay_controller as public
from historical_archive_recovery import (
    expected_crosswalk_entry,
    expected_recovery_entry, task_id_set_sha256,
)

SELECTION_SHA256 = "47a6d80ac52799d2eb55be4846b56869d9bea7c1cf92aac2814bdbc3c0827d79"
SELECTION_PATH = "evidence/historical-replay/archive-migration-selections/" + SELECTION_SHA256 + ".json"


def proposed_tasks(value: Any) -> list[dict[str, Any]]:
    queue = archive.validate_queue(value)
    tasks = queue["tasks"]
    if not tasks or len(tasks) > 5 or any(
        task.get("source_recovery_version") != 2 or task["status"] != "queued"
        or task["attempt"] != 0 for task in tasks
    ):
        raise public.HistoricalReplayControllerError("recovery preparation requires at most five queued original public tasks")
    if len({task["result_id"] for task in tasks}) != len(tasks):
        raise public.HistoricalReplayControllerError("recovery preparation repeats a Result")
    return sorted(tasks, key=lambda task: task["replay_task_id"])


def prepare_crosswalk(tasks: list[dict[str, Any]], store_digest: str, locator: dict[str, Any]) -> dict[str, Any]:
    first = tasks[0]
    roots = ("results_repository", "results_commit", "archive_repository", "archive_commit")
    if any(any(task[key] != first[key] for key in roots) for task in tasks):
        raise public.HistoricalReplayControllerError("recovery crosswalk mixes source snapshots")
    value = {
        "schema_version": 2, "kind": "historical_archive_crosswalk",
        "results_repository": first["results_repository"], "results_commit": first["results_commit"],
        "results_store_sha256": store_digest,
        "audit_repository": first["archive_repository"], "audit_commit": first["archive_commit"],
        "migration_plan": locator, "result_count": len(tasks),
        "archive_count": len({task["archive_submission_id"] for task in tasks}),
        "entries": sorted([expected_crosswalk_entry(task, 1) for task in tasks], key=lambda entry: entry["result_id"]),
    }
    public._validate_schema(value, "historical-archive-crosswalk-v2.schema.json", "prospective archive crosswalk")
    return value


def prepare_manifest(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    identifiers = [task["replay_task_id"] for task in tasks]
    if identifiers != sorted(set(identifiers)):
        raise public.HistoricalReplayControllerError("recovery task selection is not sorted and unique")
    value = {
        "schema_version": 2, "kind": "historical_public_archive_recovery",
        "task_count": len(tasks), "task_id_set_sha256": task_id_set_sha256(identifiers),
        "entries": [expected_recovery_entry(task) for task in tasks],
    }
    public._validate_schema(value, "historical-public-archive-recovery-v2.schema.json", "prospective recovery manifest")
    return value


def recovery_events(originals: dict[str, dict[str, Any]], tasks: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    events = []
    prior_time = now
    prior_id = max(task["event_id"] for task in originals.values())
    def event(kind, subject, cause, payload, cause_time):
        nonlocal prior_time, prior_id
        instant = public._event_time(prior_time, cause_time)
        identifier = public._causal_uuid7(instant, max(prior_id, cause))
        prior_time = instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        prior_id = identifier
        return {"schema_version": 1, "event_id": identifier, "event_type": kind,
                "occurred_at": prior_time, "subject_id": subject, "causation_event_id": cause,
                "actor": {"kind": "system"}, "payload": payload}
    for task in tasks:
        original = originals.get(task["replay_task_id"])
        if original is None or original["status"] != "queued" or original["attempt"] != 0:
            raise public.HistoricalReplayControllerError("recovery requires the exact unexecuted original queue task")
        changes = {
            "execution_profile_digest": task["superseded_execution_profile_digest"],
            "qualification_event_id": task["superseded_qualification_event_id"],
            "event_id": task["superseded_enqueue_event_id"],
        }
        identity = (
            "result_id", "replay_task_id", "authority_event_id", "authority_repository", "authority_commit",
            "authority_path", "authority_sha256", "measurement_config_digest", "checker", "source_visibility",
            "request_id", "historical_accepted_at", "owner_login", "declared_model", "problem_id",
            "statement_revision", "results_repository", "results_commit", "results_path", "result_file_sha256",
            "result_tree_digest", "source_kind", "source_repository", "source_commit", "benchmark_repository",
            "benchmark_commit", "toolchain", "lean_toolchain_blob_sha256", "workflow_run_identity_sha256",
        )
        if any(original[key] != task[key] for key in identity) or any(original[key] != value for key, value in changes.items()):
            raise public.HistoricalReplayControllerError("recovery changes original identity or superseded queue binding")
        payload = expected_recovery_entry(task)
        payload.pop("result_id")
        payload.update({key: task[key] for key in (
            "reconfiguration_repository", "reconfiguration_commit", "reconfiguration_path",
            "reconfiguration_sha256", "reconfiguration_entry_sha256",
        )})
        recovered = event("historical_result.replay_reconfigured", task["result_id"], original["event_id"], payload, original["occurred_at"])
        events.append(recovered)
        qualification = {key: task[key] for key in (
            "benchmark_commit", "checker", "execution_profile_digest", "measurement_config_digest",
            "qualification_repository", "qualification_commit", "qualification_path", "qualification_sha256", "toolchain",
        )}
        events.append(event("historical_archive_result.replay_profile_qualified", task["result_id"], recovered["event_id"], qualification, recovered["occurred_at"]))
        enqueue = {key: task[key] for key in ("benchmark_commit", "checker", "execution_profile_digest", "measurement_config_digest", "result_id")}
        events.append(event("replay.enqueued", task["replay_task_id"], recovered["event_id"], enqueue, events[-1]["occurred_at"]))
    return events


def prepare_event_candidates(repository: pathlib.Path, audit: pathlib.Path, state: pathlib.Path,
                             tasks: list[dict[str, Any]], selection_commit: str, now: str) -> dict[str, Any]:
    queue, _, head = archive.load_state_queue(state)
    archive._prove_archive_state_history(state, state, head)
    environment = queue["environment"]
    all_events = [public._load_validated_state_event(path, "protected recovery history")[0] for path in sorted((state / "events").glob("*/*.json"))]
    if public.current_historical_running(state / "events", state_validated=True, authority_event_type=archive.AUTHORITY_EVENT_TYPE) or public.current_historical_running(state / "events", state_validated=True):
        raise public.HistoricalReplayControllerError("recovery promotion requires zero running historical tasks")
    archive._verify_checkout(repository, "leanprover/lean-eval-submissions")
    archive._verify_ancestor_of_upstream(repository, selection_commit, "finite recovery selection")
    raw = public._git_blob(repository, selection_commit, SELECTION_PATH)
    if public.sha256_bytes(raw) != SELECTION_SHA256:
        raise public.HistoricalReplayControllerError("finite recovery selection differs")
    selection = public._parse_canonical(raw, "finite recovery selection")
    bindings = selection["result_bindings"]
    expected = {binding["original_authority_task"]["result_id"]: binding for binding in bindings}
    if len(tasks) != 5 or len(expected) != 5 or {task["result_id"] for task in tasks} != set(expected) or len({task["archive_submission_id"] for task in tasks}) != 4:
        raise public.HistoricalReplayControllerError("recovery differs from the reviewed five-Result/four-archive selection")
    for task in tasks:
        bound = expected[task["result_id"]]
        if task["archive_submission_id"] != bound["archive_submission_id"] or any(task[key] != value for key, value in bound["original_authority_task"].items()):
            raise public.HistoricalReplayControllerError("recovery differs from the reviewed original public association")
        archive._load_reviewed_input_context(repository, task)
        crosswalk_raw = public._git_blob(repository, task["crosswalk_commit"], task["crosswalk_path"])
        crosswalk = public._parse_canonical(crosswalk_raw, "finite recovery crosswalk")
        if any(crosswalk["migration_plan"][field] != selection["migration_plan_" + field] for field in ("path", "sha256")):
            raise public.HistoricalReplayControllerError("recovery uses a different migration producer plan")
        crosswalk_entry = archive.load_reviewed_crosswalk_entry(repository, audit, task)
        archive.load_archive_inputs(audit, task, crosswalk_entry)
    with tempfile.TemporaryDirectory() as directory:
        output = pathlib.Path(directory) / "materialized"
        subprocess.run([sys.executable, str(state / "scripts/state.py"), "--root", str(state), "materialize", "--output", str(output)], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        original_queue, _ = public._load_state_canonical(output / "historical-public-replay-queue.json", "protected original public queue")
        public.validate_queue(original_queue)
    originals = {task["replay_task_id"]: task for task in original_queue["tasks"]}
    candidates = recovery_events(originals, tasks, now)
    # Validate the entire prospective canonical graph using the exact reviewed
    # State program. Git atomicity is proved after the reviewed candidate commit.
    probe = "import json,pathlib,sys;sys.path.insert(0,str(pathlib.Path(sys.argv[1])/'scripts'));from validate_state import validate_event_data,validate_semantics,validate_source_recovery_closures;v=json.load(sys.stdin);[validate_event_data(e) for e in v];validate_semantics(v,sys.argv[2]);validate_source_recovery_closures(v)"
    subprocess.run([sys.executable, "-c", probe, str(state), environment], input=json.dumps(all_events + candidates).encode(), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"schema_version": 1, "kind": "historical_public_archive_recovery_candidates", "state_repository": archive._state_repository(environment), "expected_head": head, "events": candidates}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-proposals", type=pathlib.Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    crosswalk = commands.add_parser("crosswalk")
    crosswalk.add_argument("--results-store-sha256", required=True)
    crosswalk.add_argument("--migration-plan-locator", type=pathlib.Path, required=True)
    manifest = commands.add_parser("manifest")
    events = commands.add_parser("events")
    for command in (crosswalk, manifest, events):
        command.add_argument("--output", type=pathlib.Path, required=True)
    for field in ("repository", "audit", "state"):
        events.add_argument("--" + field + "-root", type=pathlib.Path, required=True)
    events.add_argument("--selection-commit", required=True)
    events.add_argument("--trusted-now", required=True)
    args = parser.parse_args()
    value, _ = public._load_state_canonical(args.task_proposals, "prospective recovered task rows")
    tasks = proposed_tasks(value)
    if args.command == "crosswalk":
        locator, _ = public._load_canonical(args.migration_plan_locator, "immutable migration producer locator")
        result = prepare_crosswalk(tasks, args.results_store_sha256, locator)
    elif args.command == "manifest":
        result = prepare_manifest(tasks)
    else:
        result = prepare_event_candidates(args.repository_root, args.audit_root, args.state_root, tasks, args.selection_commit, args.trusted_now)
    public._write(args.output, result)
    print("Created local recovery preparation artifact; no State, archive, or infrastructure mutation.")


if __name__ == "__main__":
    main()
