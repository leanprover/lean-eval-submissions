#!/usr/bin/env python3
"""Create public State events and a dynamic two-lane final-delta expectation."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

from prepare_historical_baseline_state_batch import (
    REPLAY_TASK_ID,
    BaselineBatchError,
    load_event_tree,
    validate_combined,
    verify_state_checkout,
)
from prepare_historical_private_replay import (
    PrivateReplayPlanError,
    _closed_crosswalk,
    _event_identity,
    _require_ancestor,
    build_bound_events,
    build_unavailable_event,
    canonical_compact,
    load_archive_binding,
    load_json,
    replay_task_id,
    validate_embedded_private_profiles,
    validate_state_candidates,
    verify_blob_at_commit,
    verify_checkout,
)
from prepare_historical_private_replay import (
    sha256 as private_sha256,
)
from prepare_historical_private_replay import (
    validate_plan as validate_private_plan,
)

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z"
)
REPOSITORY = "leanprover/lean-eval-submissions"
MAX_BYTES = 16 * 1024 * 1024


class StatePreparationError(ValueError):
    """The final-delta State candidate is not exactly bound."""


def canonical(value: Any, *, state_event: bool = False) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                indent=2 if state_event else None,
                separators=None if state_event else (",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()
    except (TypeError, ValueError, UnicodeError) as error:
        raise StatePreparationError("value is not canonicalizable JSON") from error


def document_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_document(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise StatePreparationError(f"{label} must be one regular file")
    raw = path.read_bytes()
    if not 0 < len(raw) <= MAX_BYTES:
        raise StatePreparationError(f"{label} exceeds its bound")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise StatePreparationError(f"{label} is not JSON") from error
    if not isinstance(value, dict) or document_bytes(value) != raw:
        raise StatePreparationError(f"{label} is not canonical JSON")
    return value, raw


def git(root: pathlib.Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise StatePreparationError("exact Git authority proof failed") from error


def verify_plan(
    path: pathlib.Path, authority_commit: str, prefix: str
) -> tuple[dict[str, Any], bytes, str]:
    value, raw = read_document(path, "final-delta replay plan")
    digest = sha256(raw)
    root = pathlib.Path(git(path.parent, "rev-parse", "--show-toplevel")).resolve()
    relative = path.resolve().relative_to(root).as_posix()
    if (
        COMMIT.fullmatch(authority_commit) is None
        or relative != f"{prefix}/{digest}.json"
        or git(root, "hash-object", relative)
        != git(root, "rev-parse", f"{authority_commit}:{relative}")
        or git(root, "remote", "get-url", "origin")
        not in {
            "https://github.com/leanprover/lean-eval-submissions",
            "https://github.com/leanprover/lean-eval-submissions.git",
        }
    ):
        raise StatePreparationError("replay plan Git locator is not exact")
    return value, raw, relative


def git_blob(root: pathlib.Path, commit: str, relative: str) -> bytes:
    try:
        raw = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{relative}"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise StatePreparationError(
            "committed authority blob is unavailable"
        ) from error
    if not 0 < len(raw) <= MAX_BYTES:
        raise StatePreparationError("committed authority blob exceeds its bound")
    return raw


def verify_public_locators(
    plan: dict[str, Any], plan_path: pathlib.Path, authority_commit: str
) -> None:
    root = pathlib.Path(git(plan_path.parent, "rev-parse", "--show-toplevel")).resolve()
    for entry in plan.get("entries", []):
        if entry.get("disposition") == "replayable":
            locator = entry.get("qualification")
            digest = entry.get("execution_profile_digest")
            if (
                not isinstance(locator, dict)
                or set(locator) != {"repository", "commit", "path", "sha256"}
                or locator.get("repository") != REPOSITORY
                or locator.get("path")
                != f"evidence/public-replay/profiles/{digest}.json"
                or COMMIT.fullmatch(str(locator.get("commit"))) is None
                or DIGEST.fullmatch(str(locator.get("sha256"))) is None
                or sha256(git_blob(root, locator["commit"], locator["path"]))
                != locator["sha256"]
            ):
                raise StatePreparationError("public qualification locator is not exact")
            git(
                root, "merge-base", "--is-ancestor", locator["commit"], authority_commit
            )
        elif entry.get("disposition") == "unavailable":
            unavailable = entry.get("unavailability", {})
            if (
                COMMIT.fullmatch(str(unavailable.get("disposition_commit"))) is None
                or DIGEST.fullmatch(str(unavailable.get("disposition_sha256"))) is None
                or sha256(
                    git_blob(
                        root,
                        unavailable["disposition_commit"],
                        unavailable["disposition_path"],
                    )
                )
                != unavailable["disposition_sha256"]
            ):
                raise StatePreparationError("public disposition locator is not exact")
            git(
                root,
                "merge-base",
                "--is-ancestor",
                unavailable["disposition_commit"],
                authority_commit,
            )
        else:
            raise StatePreparationError("public plan disposition is invalid")


def parse_time(value: str) -> dt.datetime:
    if TIMESTAMP.fullmatch(value) is None:
        raise StatePreparationError("first event time must use UTC milliseconds")
    return dt.datetime.fromisoformat(value[:-1] + "+00:00")


def event(
    *,
    occurred_at: dt.datetime,
    result_id: str,
    event_type: str,
    subject_id: str,
    payload: dict[str, Any],
    parent: str | None,
) -> dict[str, Any]:
    timestamp = occurred_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {
        "schema_version": 1,
        "event_id": _event_identity(occurred_at, result_id, event_type),
        "event_type": event_type,
        "occurred_at": timestamp,
        "subject_id": subject_id,
        "causation_event_id": parent,
        "actor": {"kind": "system"},
        "payload": payload,
    }


def build_public_events(
    plan: dict[str, Any],
    plan_commit: str,
    plan_path: str,
    plan_sha: str,
    first: dt.datetime,
) -> list[dict[str, Any]]:
    if (
        set(plan)
        != {
            "schema_version",
            "kind",
            "activation_status",
            "preparation",
            "results",
            "entries",
        }
        or plan.get("schema_version") != 1
        or plan.get("kind") != "historical_final_delta_public_replay_plan"
        or plan.get("activation_status") != "ready"
        or not isinstance(plan.get("entries"), list)
    ):
        raise StatePreparationError("public plan identity is invalid")
    events: list[dict[str, Any]] = []
    result_ids: set[str] = set()
    for entry in plan["entries"]:
        result_id = entry.get("result_id")
        if not isinstance(result_id, str) or result_id in result_ids:
            raise StatePreparationError("public plan result identity is invalid")
        result_ids.add(result_id)
        source = entry["source"]
        authority = entry["public_authority"]
        result_fields = {
            key: entry[key]
            for key in (
                "historical_accepted_at",
                "owner_login",
                "declared_model",
                "problem_id",
                "statement_revision",
                "results_path",
                "result_file_sha256",
                "result_tree_digest",
            )
        }
        common = {
            "request_id": authority["request_id"],
            **result_fields,
            "results_repository": REPOSITORY,
            "results_commit": plan["results"]["commit"],
            "source_kind": source["kind"],
            "source_repository": source["repository"],
            "source_commit": source["commit"],
            "source_visibility": "public",
            "benchmark_repository": entry["benchmark_repository"],
            "benchmark_commit": entry["benchmark_commit"],
            "workflow_run_identity_sha256": authority["workflow_run_identity_sha256"],
        }
        start = first + dt.timedelta(milliseconds=len(events))
        if entry["disposition"] == "unavailable":
            unavailable = entry["unavailability"]
            payload = {
                **common,
                "disposition_repository": REPOSITORY,
                "disposition_commit": unavailable["disposition_commit"],
                **{
                    key: unavailable[key]
                    for key in (
                        "disposition_path",
                        "disposition_sha256",
                        "candidate_entry_sha256",
                        "reason_code",
                        "rationale_code",
                    )
                },
            }
            events.append(
                event(
                    occurred_at=start,
                    result_id=result_id,
                    event_type="historical_result.replay_unavailable",
                    subject_id=result_id,
                    payload=payload,
                    parent=None,
                )
            )
            continue
        payload = {
            **common,
            "toolchain": entry["toolchain"],
            "lean_toolchain_blob_sha256": entry["lean_toolchain_blob_sha256"],
            "authority_repository": REPOSITORY,
            "authority_commit": plan_commit,
            "authority_path": plan_path,
            "authority_sha256": plan_sha,
        }
        authorized = event(
            occurred_at=start,
            result_id=result_id,
            event_type="historical_result.replay_authorized",
            subject_id=result_id,
            payload=payload,
            parent=None,
        )
        locator = entry["qualification"]
        qualified = event(
            occurred_at=start + dt.timedelta(milliseconds=1),
            result_id=result_id,
            event_type="historical_result.replay_profile_qualified",
            subject_id=result_id,
            payload={
                "toolchain": entry["toolchain"],
                "benchmark_commit": entry["benchmark_commit"],
                "measurement_config_digest": entry["measurement_config_digest"],
                "execution_profile_digest": entry["execution_profile_digest"],
                "checker": "nanoda",
                "qualification_repository": locator["repository"],
                "qualification_commit": locator["commit"],
                "qualification_path": locator["path"],
                "qualification_sha256": locator["sha256"],
            },
            parent=authorized["event_id"],
        )
        task_id = replay_task_id(result_id, entry["measurement_config_digest"])
        enqueued = event(
            occurred_at=start + dt.timedelta(milliseconds=2),
            result_id=result_id,
            event_type="replay.enqueued",
            subject_id=task_id,
            payload={
                "result_id": result_id,
                "measurement_config_digest": entry["measurement_config_digest"],
                "execution_profile_digest": entry["execution_profile_digest"],
                "checker": "nanoda",
                "benchmark_commit": entry["benchmark_commit"],
            },
            parent=qualified["event_id"],
        )
        events.extend((authorized, qualified, enqueued))
    return events


def build_private_events(
    *,
    plan_path: pathlib.Path,
    authority_commit: str,
    audit_root: pathlib.Path,
    audit_commit: str,
    state_root: pathlib.Path,
    state_commit: str,
    first: dt.datetime,
) -> list[dict[str, Any]]:
    plan, plan_raw, relative = verify_plan(
        plan_path, authority_commit, "evidence/private-replay/plans"
    )
    validate_private_plan(plan)
    authority_root = pathlib.Path(
        git(plan_path.parent, "rev-parse", "--show-toplevel")
    ).resolve()
    verify_checkout(authority_root, authority_commit, "submissions")
    validate_embedded_private_profiles(plan, authority_root, authority_commit)
    verify_checkout(audit_root, audit_commit, "audit")
    locator = plan["crosswalk"]
    crosswalk_path = authority_root.joinpath(*locator["path"].split("/"))
    crosswalk, crosswalk_raw = load_json(crosswalk_path, "private archive crosswalk")
    verify_blob_at_commit(
        crosswalk_path, crosswalk_raw, locator["commit"], "private archive crosswalk"
    )
    all_entries = _closed_crosswalk(
        crosswalk, crosswalk_raw, locator["commit"], crosswalk_path
    )
    _require_ancestor(
        authority_root, locator["commit"], authority_commit, "crosswalk commit"
    )
    _require_ancestor(
        audit_root, crosswalk["audit_commit"], audit_commit, "crosswalk audit commit"
    )
    selected: dict[str, dict[str, Any]] = {}
    for source in all_entries:
        result_id = source["result_id"]
        if result_id in {entry["result_id"] for entry in plan["entries"]}:
            selected[result_id] = source
    if set(selected) != {entry["result_id"] for entry in plan["entries"]}:
        raise StatePreparationError("private plan is not a closed crosswalk subset")
    events: list[dict[str, Any]] = []
    archive_cache: dict[str, tuple[str, str, dict[str, Any]]] = {}
    plan_sha = sha256(plan_raw)
    for entry in plan["entries"]:
        source = selected[entry["result_id"]]
        if private_sha256(canonical_compact(source)) != entry["crosswalk_entry_sha256"]:
            raise StatePreparationError("private crosswalk entry digest changed")
        occurred = first + dt.timedelta(milliseconds=len(events))
        if entry["classification"] == "archive_not_found":
            events.append(
                build_unavailable_event(
                    entry=entry,
                    plan_commit=authority_commit,
                    plan_path=relative,
                    plan_sha256=plan_sha,
                    results_commit=plan["results"]["commit"],
                    crosswalk=locator,
                    occurred_at=occurred,
                )
            )
            continue
        if (
            source.get("classification") != "bound"
            or source.get("submission_id") != entry["archive_submission_id"]
            or source.get("archive_plan_entry_sha256")
            != entry["archive_plan_entry_sha256"]
        ):
            raise StatePreparationError("private plan archive binding changed")
        submission_id = entry["archive_submission_id"]
        cached = archive_cache.get(submission_id)
        if cached is None:
            archive, archived_benchmark = load_archive_binding(
                audit_root, audit_commit, entry, source
            )
            archive_cache[submission_id] = (
                entry["benchmark_commit"],
                archived_benchmark,
                archive,
            )
        else:
            first_benchmark, archived_benchmark, archive = cached
            from prepare_historical_private_replay import (
                validate_cached_archive_binding,
            )

            validate_cached_archive_binding(
                first_benchmark, archived_benchmark, entry, source
            )
        events.extend(
            build_bound_events(
                entry=entry,
                profile=plan["profiles"][entry["execution_profile_digest"]],
                archive=archive,
                plan_commit=authority_commit,
                plan_path=relative,
                plan_sha256=plan_sha,
                results_commit=plan["results"]["commit"],
                crosswalk=locator,
                occurred_at=occurred,
            )
        )
    validate_state_candidates(
        state_root=state_root,
        state_commit=state_commit,
        candidates=events,
        append_ready=True,
    )
    return events


def load_final_expectation(path: pathlib.Path) -> dict[str, Any]:
    value, _ = read_document(path, "final-delta expectation")
    if (
        set(value)
        != {
            "schema_version",
            "kind",
            "environment",
            "lanes",
            "reviewed_unavailability_counts",
            "total_event_count",
            "total_task_count",
        }
        or value.get("schema_version") != 1
        or value.get("kind") != "historical_final_delta_state_batch_expectation"
        or value.get("environment") != "production"
    ):
        raise StatePreparationError("final-delta expectation identity is invalid")
    unavailable = value["reviewed_unavailability_counts"]
    if (
        set(unavailable) != {"public", "private", "total"}
        or unavailable["total"] != unavailable["public"] + unavailable["private"]
    ):
        raise StatePreparationError("final-delta unavailability counts are invalid")
    for lane in value["lanes"].values():
        if (
            set(lane)
            != {
                "authority_event_type",
                "qualification_event_type",
                "enqueue_event_type",
                "unavailable_event_type",
                "event_count",
                "task_count",
                "unavailable_count",
            }
            or lane["event_count"] != 3 * lane["task_count"] + lane["unavailable_count"]
            or lane["event_count"] < 1
        ):
            raise StatePreparationError("final-delta lane expectation is invalid")
    if value["total_event_count"] != sum(
        lane["event_count"] for lane in value["lanes"].values()
    ) or value["total_task_count"] != sum(
        lane["task_count"] for lane in value["lanes"].values()
    ):
        raise StatePreparationError("final-delta expectation totals differ")
    return value


def final_lane_inventory(
    name: str, events: list[dict[str, Any]], expected: dict[str, Any]
) -> dict[str, Any]:
    lane = expected["lanes"][name]
    types = {
        lane["authority_event_type"]: lane["task_count"],
        lane["qualification_event_type"]: lane["task_count"],
        lane["enqueue_event_type"]: lane["task_count"],
        lane["unavailable_event_type"]: lane["unavailable_count"],
    }
    counts = {kind: 0 for kind in types}
    event_ids: list[str] = []
    task_ids: list[str] = []
    result_ids: list[str] = []
    descriptors: list[dict[str, str]] = []
    for item in events:
        kind = item.get("event_type")
        if kind not in types:
            raise StatePreparationError(f"{name} lane has an unexpected event")
        counts[kind] += 1
        event_id = item["event_id"]
        event_ids.append(event_id)
        descriptors.append(
            {
                "path": f"events/{event_id.replace('-', '')[:2]}/{event_id}.json",
                "sha256": sha256(document_bytes(item)),
            }
        )
        if kind == lane["enqueue_event_type"]:
            task_id = item["subject_id"]
            if REPLAY_TASK_ID.fullmatch(task_id) is None:
                raise StatePreparationError("final-delta replay task ID is invalid")
            task_ids.append(task_id)
            result_ids.append(item["payload"]["result_id"])
    if (
        len(events) != lane["event_count"]
        or counts != types
        or len(set(event_ids)) != len(event_ids)
        or len(set(task_ids)) != len(task_ids)
        or len(set(result_ids)) != len(result_ids)
    ):
        raise StatePreparationError(f"{name} lane count or identity differs")
    descriptors.sort(key=lambda item: item["path"])
    digest = lambda value: sha256(document_bytes(value))
    return {
        "event_count": len(events),
        "task_count": len(task_ids),
        "event_ids": sorted(event_ids),
        "task_ids": sorted(task_ids),
        "result_ids": sorted(result_ids),
        "event_files": descriptors,
        "event_set_sha256": digest(descriptors),
        "event_id_set_sha256": digest(sorted(event_ids)),
        "task_id_set_sha256": digest(sorted(task_ids)),
        "result_id_set_sha256": digest(sorted(result_ids)),
    }


def write_events(root: pathlib.Path, events: list[dict[str, Any]]) -> None:
    if root.exists() or root.is_symlink():
        raise StatePreparationError("refusing to overwrite candidate directory")
    root.mkdir(mode=0o700)
    for item in events:
        event_id = item["event_id"]
        path = root / "events" / event_id.replace("-", "")[:2] / f"{event_id}.json"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(document_bytes(item))


def combine(
    *,
    state_root: pathlib.Path,
    state_head: str,
    audit_head: str,
    audit_tree: str,
    expectation_path: pathlib.Path,
    public_root: pathlib.Path,
    private_root: pathlib.Path,
    output: pathlib.Path,
) -> None:
    verify_state_checkout(state_root, state_head)
    expected = load_final_expectation(expectation_path)
    lane_roots = {"public": public_root, "private": private_root}
    events = {name: load_event_tree(root, name) for name, root in lane_roots.items()}
    inventories = {
        name: final_lane_inventory(name, events[name], expected) for name in lane_roots
    }
    projection = validate_combined(
        state_root, state_head, events["public"], events["private"], inventories
    )
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise StatePreparationError("combined candidate output is unsafe")
    output.mkdir(mode=0o700)
    for name in ("public", "private"):
        for descriptor in inventories[name]["event_files"]:
            source = lane_roots[name] / descriptor["path"]
            target = output / descriptor["path"]
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            raw = source.read_bytes()
            if sha256(raw) != descriptor["sha256"] or target.exists():
                raise StatePreparationError("candidate event changed after validation")
            target.write_bytes(raw)
    event_files = sorted(
        [*inventories["public"]["event_files"], *inventories["private"]["event_files"]],
        key=lambda item: item["path"],
    )
    manifest = {
        "schema_version": 1,
        "kind": "historical_final_delta_state_append_candidate",
        "activation_status": "reviewed_but_not_appended",
        "state": {
            "repository": "leanprover/lean-eval-state",
            "expected_head": state_head,
            "expected_tree": git(state_root, "rev-parse", f"{state_head}^{{tree}}"),
            **projection,
        },
        "audit": {
            "repository": "leanprover/lean-eval-audit",
            "expected_head": audit_head,
            "expected_tree": audit_tree,
        },
        "expectation": {
            "path": expectation_path.name,
            "sha256": sha256(expectation_path.read_bytes()),
            "total_event_count": expected["total_event_count"],
            "total_task_count": expected["total_task_count"],
        },
        "lanes": inventories,
        "event_files": event_files,
        "event_set_sha256": sha256(document_bytes(event_files)),
    }
    (output / "historical-final-delta-state-append-candidate.json").write_bytes(
        document_bytes(manifest)
    )


def expectation(
    public_plan: dict[str, Any], private_plan: dict[str, Any]
) -> dict[str, Any]:
    public_tasks = sum(
        entry.get("disposition") == "replayable" for entry in public_plan["entries"]
    )
    public_unavailable = len(public_plan["entries"]) - public_tasks
    private_tasks = sum(
        entry.get("classification") == "bound" for entry in private_plan["entries"]
    )
    private_unavailable = len(private_plan["entries"]) - private_tasks

    def lane(prefix: str, tasks: int, unavailable: int) -> dict[str, Any]:
        return {
            "authority_event_type": f"{prefix}.replay_authorized",
            "qualification_event_type": f"{prefix}.replay_profile_qualified",
            "enqueue_event_type": "replay.enqueued",
            "unavailable_event_type": f"{prefix}.replay_unavailable",
            "event_count": 3 * tasks + unavailable,
            "task_count": tasks,
            "unavailable_count": unavailable,
        }

    return {
        "schema_version": 1,
        "kind": "historical_final_delta_state_batch_expectation",
        "environment": "production",
        "lanes": {
            "public": lane("historical_result", public_tasks, public_unavailable),
            "private": lane(
                "historical_archive_result", private_tasks, private_unavailable
            ),
        },
        "reviewed_unavailability_counts": {
            "public": public_unavailable,
            "private": private_unavailable,
            "total": public_unavailable + private_unavailable,
        },
        "total_event_count": 3 * (public_tasks + private_tasks)
        + public_unavailable
        + private_unavailable,
        "total_task_count": public_tasks + private_tasks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    public = commands.add_parser("public-events")
    public.add_argument("--plan", required=True, type=pathlib.Path)
    public.add_argument("--authority-commit", required=True)
    public.add_argument("--state-root", required=True, type=pathlib.Path)
    public.add_argument("--state-commit", required=True)
    public.add_argument("--first-occurred-at", required=True)
    public.add_argument("--output-directory", required=True, type=pathlib.Path)
    private = commands.add_parser("private-events")
    private.add_argument("--plan", required=True, type=pathlib.Path)
    private.add_argument("--authority-commit", required=True)
    private.add_argument("--audit-root", required=True, type=pathlib.Path)
    private.add_argument("--audit-commit", required=True)
    private.add_argument("--state-root", required=True, type=pathlib.Path)
    private.add_argument("--state-commit", required=True)
    private.add_argument("--first-occurred-at", required=True)
    private.add_argument("--output-directory", required=True, type=pathlib.Path)
    combined = commands.add_parser("combine")
    combined.add_argument("--state-root", required=True, type=pathlib.Path)
    combined.add_argument("--state-head", required=True)
    combined.add_argument("--audit-head", required=True)
    combined.add_argument("--audit-tree", required=True)
    combined.add_argument("--expectation", required=True, type=pathlib.Path)
    combined.add_argument("--public-candidate-root", required=True, type=pathlib.Path)
    combined.add_argument("--private-candidate-root", required=True, type=pathlib.Path)
    combined.add_argument("--output-directory", required=True, type=pathlib.Path)
    expected = commands.add_parser("expectation")
    expected.add_argument("--public-plan", required=True, type=pathlib.Path)
    expected.add_argument("--private-plan", required=True, type=pathlib.Path)
    expected.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "expectation":
            public_plan, _ = read_document(args.public_plan, "public plan")
            private_plan, _ = read_document(args.private_plan, "private plan")
            if args.output.exists() or args.output.is_symlink():
                raise StatePreparationError("refusing to overwrite expectation")
            args.output.write_bytes(
                document_bytes(expectation(public_plan, private_plan))
            )
            return 0
        if args.command == "combine":
            combine(
                state_root=args.state_root.resolve(),
                state_head=args.state_head,
                audit_head=args.audit_head,
                audit_tree=args.audit_tree,
                expectation_path=args.expectation.resolve(),
                public_root=args.public_candidate_root.resolve(),
                private_root=args.private_candidate_root.resolve(),
                output=args.output_directory.resolve(),
            )
            return 0
        if args.command == "private-events":
            events = build_private_events(
                plan_path=args.plan.resolve(),
                authority_commit=args.authority_commit,
                audit_root=args.audit_root.resolve(),
                audit_commit=args.audit_commit,
                state_root=args.state_root.resolve(),
                state_commit=args.state_commit,
                first=parse_time(args.first_occurred_at),
            )
            write_events(args.output_directory.resolve(), events)
            return 0
        plan, raw, relative = verify_plan(
            args.plan.resolve(), args.authority_commit, "evidence/public-replay/plans"
        )
        verify_public_locators(plan, args.plan.resolve(), args.authority_commit)
        events = build_public_events(
            plan,
            args.authority_commit,
            relative,
            sha256(raw),
            parse_time(args.first_occurred_at),
        )
        validate_state_candidates(
            state_root=args.state_root.resolve(),
            state_commit=args.state_commit,
            candidates=events,
            append_ready=True,
        )
        write_events(args.output_directory.resolve(), events)
        return 0
    except (
        StatePreparationError,
        PrivateReplayPlanError,
        BaselineBatchError,
        OSError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
