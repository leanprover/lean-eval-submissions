#!/usr/bin/env python3
"""Prepare the bounded private-replay image repair and its State event batch.

The two commands deliberately split the reviewed decision from State mutation.
``prepare-decision`` selects only archives made by the historical backfill
script and emits one content-addressed, source-free profile-replacement
artifact. After that artifact and its replacement profiles land on protected
main, ``render-state-batch`` rechecks current protected State and emits the
exact CAS-bound event tree. Its publisher may create one non-force State review
branch, but it never writes either protected branch or mutates submissions.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

SCRIPT_DIRECTORY = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

import historical_private_replay_controller as controller
from historical_replay_controller import _parse_timestamp, _uuid7

SOURCE_FIX_COMMIT = "bee734f901a050800624ceb058a825cf17064fc4"
BACKFILL_ARCHIVER = (
    "https://github.com/leanprover/lean-eval-submissions/blob/main/"
    "scripts/backfill_audit.py"
)
UUID_DOMAIN = b"lean-eval-private-profile-reconfiguration-v1\0"
EVENT_PATH = re.compile(r"events/[0-9a-f]{2}/[0-9a-f-]{36}\.json\Z")
STATE_BRANCH = re.compile(
    r"historical-private-reconfiguration-(failed-canary|queued-remainder)-[0-9a-f]{12}\Z"
)
MAX_STATE_EVENTS = 20_000
STATE_BATCH_FIELDS = {
    "schema_version", "kind", "selection", "state_repository", "expected_head",
    "reconfiguration_repository", "reconfiguration_commit", "reconfiguration_path",
    "reconfiguration_sha256", "first_occurred_at", "event_id_seed", "task_count",
    "event_count", "events",
}


class ReconfigurationPreparationError(ValueError):
    """A replacement decision or State batch is outside the bounded repair."""


def _error(message: str, error: Exception | None = None) -> None:
    if error is None:
        raise ReconfigurationPreparationError(message)
    raise ReconfigurationPreparationError(message) from error


def _git(root: pathlib.Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        _error("exact Git history is unavailable", error)


def _load_profile_at_main(
    repository_root: pathlib.Path, execution_digest: str
) -> tuple[dict[str, Any], bytes, dict[str, str]]:
    controller._match(
        controller.DIGEST, execution_digest, "replacement execution profile digest"
    )
    path = f"evidence/private-replay/profiles/{execution_digest}.json"
    head = _git(repository_root, "rev-parse", "HEAD")
    raw = controller._git_blob(repository_root, head, path)
    profile = controller._parse_canonical(raw, "replacement private profile")
    introductions = _git(
        repository_root,
        "log",
        "--format=%H",
        "--diff-filter=A",
        "--",
        path,
    ).splitlines()
    if len(introductions) != 1:
        _error("replacement private profile does not have one introduction commit")
    commit = controller._match(
        controller.COMMIT,
        introductions[0],
        "replacement private profile introduction commit",
    )
    if controller._git_blob(repository_root, commit, path) != raw:
        _error("replacement private profile changed after its introduction")
    return profile, raw, {
        "execution_profile_digest": execution_digest,
        "repository": "leanprover/lean-eval-submissions",
        "commit": commit,
        "path": path,
        "sha256": controller.sha256_bytes(raw),
    }


def _is_backfill_archive(
    audit_root: pathlib.Path, task: dict[str, Any]
) -> bool:
    controller._verify_ancestor_of_upstream(
        audit_root, task["archive_commit"], "archive_commit"
    )
    raw = controller._git_blob(
        audit_root, task["archive_commit"], task["archive_sidecar_path"]
    )
    if controller.sha256_bytes(raw) != task["archive_sidecar_sha256"]:
        _error("historical archive sidecar differs from its protected queue binding")
    sidecar = controller._parse_json_object(raw, "historical archive sidecar")
    return (
        sidecar.get("submission_id") == task["archive_submission_id"]
        and sidecar.get("archiver_workflow_run") == BACKFILL_ARCHIVER
    )


def _replacement_task(
    task: dict[str, Any], locator: dict[str, str]
) -> dict[str, Any]:
    replacement = copy.deepcopy(task)
    replacement.update(
        execution_profile_digest=locator["execution_profile_digest"],
        qualification_repository=locator["repository"],
        qualification_commit=locator["commit"],
        qualification_path=locator["path"],
        qualification_sha256=locator["sha256"],
        superseded_qualification_event_id=task["qualification_event_id"],
    )
    return replacement


def _validate_reviewed_entry_against_task(
    entry: dict[str, Any], task: dict[str, Any]
) -> dict[str, Any]:
    future_task = _replacement_task(task, entry["replacement_qualification"])
    if entry != controller._expected_reconfiguration_entry(future_task, task):
        _error("reviewed replacement entry differs from the exact current task")
    return future_task


def prepare_decision(args: argparse.Namespace) -> pathlib.Path:
    repository_root = args.repository_root.resolve()
    state_root = args.state_root.resolve()
    audit_root = args.audit_root.resolve()
    repository_head = controller._verify_checkout(
        repository_root, "leanprover/lean-eval-submissions"
    )
    controller._verify_checkout(audit_root, "leanprover/lean-eval-audit")
    controller._verify_ancestor(
        repository_root, SOURCE_FIX_COMMIT, repository_head
    )
    queue, _, _ = controller.load_state_queue(state_root)

    requested = sorted(set(args.replacement_profile))
    if len(requested) != len(args.replacement_profile) or not 1 <= len(requested) <= 6:
        _error("replacement profile selection is not unique and bounded")
    replacements: dict[str, tuple[dict[str, Any], bytes, dict[str, str]]] = {}
    for digest in requested:
        profile, raw, locator = _load_profile_at_main(repository_root, digest)
        benchmark = controller._match(
            controller.COMMIT,
            profile.get("benchmark_commit"),
            "replacement benchmark commit",
        )
        if benchmark in replacements:
            _error("replacement profiles duplicate one benchmark")
        controller._verify_ancestor(
            repository_root, SOURCE_FIX_COMMIT, profile["image_source_commit"]
        )
        replacements[benchmark] = (profile, raw, locator)

    selected: list[dict[str, Any]] = []
    expected_status = "failed" if args.selection == "failed_canary" else "queued"
    for task in queue["tasks"]:
        if task["status"] != expected_status or not _is_backfill_archive(
            audit_root, task
        ):
            continue
        replacement_tuple = replacements.get(task["benchmark_commit"])
        if replacement_tuple is None:
            continue
        old_authority, _, old_profile, _ = controller.load_reviewed_inputs(
            repository_root, task
        )
        replacement_profile, _, locator = replacement_tuple
        future_task = _replacement_task(task, locator)
        replacement_profile = controller._validate_profile(
            replacement_profile, future_task
        )
        controller._verify_profile_provenance(
            repository_root,
            future_task,
            replacement_profile,
            upper_bound_commit=repository_head,
        )
        controller._validate_profile_replacement(old_profile, replacement_profile)
        controller._validate_authority(old_authority, task, old_profile)
        selected.append(
            controller._expected_reconfiguration_entry(future_task, task)
        )

    selected.sort(key=lambda entry: entry["replay_task_id"])
    expected_count = 1 if args.selection == "failed_canary" else 46
    if len(selected) != expected_count:
        _error(
            f"{args.selection} selects {len(selected)} tasks instead of {expected_count}"
        )
    if args.selection == "failed_canary":
        live = queue["tasks"]
        matching = [
            task for task in live if task["replay_task_id"] == selected[0]["replay_task_id"]
        ]
        if (
            len(requested) != 1
            or len(matching) != 1
            or matching[0].get("attempt") != 3
            or matching[0].get("reason_code") != "runner_lost"
            or matching[0].get("retryable") is not True
        ):
            _error("failed canary is not the one retryable attempt-three task")

    artifact = {
        "schema_version": 1,
        "kind": controller.RECONFIGURATION_KIND,
        "reason_code": "profile_execution_unavailable",
        "selection": args.selection,
        "task_count": len(selected),
        "entries": selected,
    }
    controller.validate_reconfiguration(artifact)
    raw = controller.canonical_bytes(artifact)
    digest = controller.sha256_bytes(raw)
    output_root = args.output_directory.resolve()
    if output_root.exists() or output_root.is_symlink():
        _error("decision output directory already exists")
    destination = (
        output_root
        / "evidence/private-replay/reconfigurations"
        / f"{digest}.json"
    )
    destination.parent.mkdir(parents=True, mode=0o700)
    controller._write(destination, artifact)
    return destination


def _load_state_events(state_root: pathlib.Path) -> list[dict[str, Any]]:
    paths = sorted((state_root / "events").glob("*/*.json"))
    if not paths or len(paths) > MAX_STATE_EVENTS:
        _error("protected State event inventory is not bounded")
    events: list[dict[str, Any]] = []
    for path in paths:
        relative = path.relative_to(state_root).as_posix()
        if EVENT_PATH.fullmatch(relative) is None or path.is_symlink():
            _error("protected State event path is invalid")
        event, _ = controller._load_state_canonical(path, "protected State event")
        event_id = controller._match(
            controller.UUID7, event.get("event_id"), "protected State event_id"
        )
        if relative != f"events/{event_id[:2]}/{event_id}.json":
            _error("protected State event path differs from its identity")
        events.append(event)
    return events


def _superseded_enqueue(
    events: list[dict[str, Any]], task: dict[str, Any]
) -> dict[str, Any]:
    selected = [
        event
        for event in events
        if event.get("event_type") == "replay.enqueued"
        and event.get("subject_id") == task["replay_task_id"]
        and event.get("causation_event_id") == task["qualification_event_id"]
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("result_id") == task["result_id"]
        and event["payload"].get("execution_profile_digest")
        == task["execution_profile_digest"]
    ]
    if len(selected) != 1:
        _error("superseded replay enqueue is not uniquely derivable")
    return selected[0]


def _deterministic_uuid7(
    timestamp: dt.datetime, seed: str, task_id: str, event_type: str
) -> str:
    randomness = hashlib.sha256(
        UUID_DOMAIN
        + bytes.fromhex(seed)
        + b"\0"
        + task_id.encode("ascii")
        + b"\0"
        + event_type.encode("ascii")
    ).digest()[:10]
    return _uuid7(timestamp, randomness)


def _event(
    *,
    timestamp: dt.datetime,
    seed: str,
    task_id: str,
    event_type: str,
    subject_id: str,
    cause: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": _deterministic_uuid7(timestamp, seed, task_id, event_type),
        "event_type": event_type,
        "occurred_at": timestamp.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        ),
        "subject_id": subject_id,
        "causation_event_id": cause,
        "actor": {"kind": "system"},
        "payload": payload,
    }


def _write_and_validate_overlay(
    state_root: pathlib.Path,
    state_head: str,
    events: list[dict[str, Any]],
    expected: dict[str, tuple[int, str, str]],
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory) / "state"
        root.mkdir()
        controller._export_exact_commit(state_root, state_head, root)
        for event in events:
            destination = (
                root / "events" / event["event_id"][:2] / f"{event['event_id']}.json"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            controller._write(destination, event, serializer=controller.state_canonical_bytes)
        views = pathlib.Path(directory) / "views"
        commands = (
            [sys.executable, str(root / "scripts/state.py"), "--root", str(root), "validate"],
            [
                sys.executable,
                str(root / "scripts/state.py"),
                "--root",
                str(root),
                "materialize",
                "--output",
                str(views),
            ],
        )
        try:
            for command in commands:
                subprocess.run(
                    command,
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=300,
                )
        except (OSError, subprocess.SubprocessError) as error:
            _error("candidate event batch fails exact State validation", error)
        queue, _ = controller._load_state_canonical(
            views / "historical-private-replay-queue.json",
            "candidate historical private queue",
        )
        tasks = {
            task["replay_task_id"]: task
            for task in controller.validate_queue(queue)["tasks"]
        }
        for task_id, (attempt, profile, reconfiguration_event_id) in expected.items():
            task = tasks.get(task_id)
            if (
                task is None
                or task["status"] != "queued"
                or task["attempt"] != attempt
                or task["execution_profile_digest"] != profile
                or task.get("reconfiguration_event_id") != reconfiguration_event_id
            ):
                _error("candidate State queue differs from the bounded replacement")


def render_state_batch(args: argparse.Namespace) -> pathlib.Path:
    repository_root = args.repository_root.resolve()
    state_root = args.state_root.resolve()
    controller._verify_checkout(repository_root, "leanprover/lean-eval-submissions")
    queue, _, state_head = controller.load_state_queue(state_root)
    reconfiguration_commit = controller._match(
        controller.COMMIT,
        args.reconfiguration_commit,
        "reconfiguration commit",
    )
    controller._verify_ancestor_of_upstream(
        repository_root, reconfiguration_commit, "reconfiguration commit"
    )
    path = args.reconfiguration_path
    digest_match = controller.RECONFIGURATION_PATH.fullmatch(path)
    if digest_match is None:
        _error("reconfiguration path is invalid")
    raw = controller._git_blob(repository_root, reconfiguration_commit, path)
    digest = controller.sha256_bytes(raw)
    if path != f"evidence/private-replay/reconfigurations/{digest}.json":
        _error("reconfiguration path is not content addressed")
    artifact = controller.validate_reconfiguration(
        controller._parse_canonical(raw, "exact private reconfiguration Git blob")
    )
    state_events = _load_state_events(state_root)
    tasks = {task["replay_task_id"]: task for task in queue["tasks"]}
    first = _parse_timestamp(args.first_occurred_at, "first occurred_at")
    controller._match(controller.DIGEST, args.event_id_seed, "event id seed")
    output_events: list[dict[str, Any]] = []
    expected: dict[str, tuple[int, str, str]] = {}

    for entry in artifact["entries"]:
        task = tasks.get(entry["replay_task_id"])
        if task is None or "reconfiguration_event_id" in task:
            _error("reviewed replacement task is no longer in its original queue state")
        expected_status = (
            "failed" if artifact["selection"] == "failed_canary" else "queued"
        )
        if (
            task["status"] != expected_status
            or task["attempt"] != entry["attempt"]
            or task["execution_profile_digest"]
            != entry["superseded_qualification"]["execution_profile_digest"]
            or task["qualification_event_id"]
            != entry["superseded_qualification"]["event_id"]
            or controller._profile_qualification_binding(task)
            != {
                key: value
                for key, value in entry["superseded_qualification"].items()
                if key != "event_id"
            }
        ):
            _error("live task differs from the reviewed superseded state")
        if expected_status == "failed" and (
            task.get("attempt") != 3
            or task.get("reason_code") != "runner_lost"
            or task.get("retryable") is not True
        ):
            _error("failed canary no longer has its final bounded retry")
        replacement = entry["replacement_qualification"]
        future_task = _validate_reviewed_entry_against_task(entry, task)
        replacement_raw = controller._git_blob(
            repository_root, replacement["commit"], replacement["path"]
        )
        if controller.sha256_bytes(replacement_raw) != replacement["sha256"]:
            _error("replacement profile Git blob differs from reviewed decision")
        replacement_profile = controller._validate_profile(
            controller._parse_canonical(
                replacement_raw, "exact replacement private profile Git blob"
            ),
            future_task,
        )
        _, _, old_profile, _ = controller.load_reviewed_inputs(
            repository_root, task
        )
        controller._validate_profile_replacement(old_profile, replacement_profile)
        enqueue = _superseded_enqueue(state_events, task)
        latest_cause = max(
            _parse_timestamp(task["occurred_at"], "task occurred_at"),
            _parse_timestamp(enqueue["occurred_at"], "enqueue occurred_at"),
        )
        if first <= latest_cause:
            _error("first event timestamp does not follow every selected task")

        offset = len(output_events)
        unavailable = _event(
            timestamp=first + dt.timedelta(milliseconds=offset),
            seed=args.event_id_seed,
            task_id=task["replay_task_id"],
            event_type="replay.unavailable",
            subject_id=task["replay_task_id"],
            cause=task["event_id"],
            payload={
                "reason_code": "execution_profile_permanently_unavailable",
                "evidence_repository": "leanprover/lean-eval-submissions",
                "evidence_commit": reconfiguration_commit,
                "evidence_path": path,
                "evidence_sha256": digest,
            },
        )
        qualification = _event(
            timestamp=first + dt.timedelta(milliseconds=offset + 1),
            seed=args.event_id_seed,
            task_id=task["replay_task_id"],
            event_type="historical_archive_result.replay_profile_qualified",
            subject_id=task["result_id"],
            cause=task["authority_event_id"],
            payload={
                "toolchain": task["toolchain"],
                "benchmark_commit": task["benchmark_commit"],
                "measurement_config_digest": task["measurement_config_digest"],
                "execution_profile_digest": replacement["execution_profile_digest"],
                "checker": task["checker"],
                "qualification_repository": replacement["repository"],
                "qualification_commit": replacement["commit"],
                "qualification_path": replacement["path"],
                "qualification_sha256": replacement["sha256"],
            },
        )
        reconfigured = _event(
            timestamp=first + dt.timedelta(milliseconds=offset + 2),
            seed=args.event_id_seed,
            task_id=task["replay_task_id"],
            event_type="historical_archive_result.replay_reconfigured",
            subject_id=task["result_id"],
            cause=unavailable["event_id"],
            payload={
                "replay_task_id": task["replay_task_id"],
                "measurement_config_digest": task["measurement_config_digest"],
                "checker": task["checker"],
                "superseded_enqueue_event_id": enqueue["event_id"],
                "superseded_qualification_event_id": task["qualification_event_id"],
                "superseded_execution_profile_digest": task[
                    "execution_profile_digest"
                ],
                "replacement_qualification_event_id": qualification["event_id"],
                "replacement_execution_profile_digest": replacement[
                    "execution_profile_digest"
                ],
                "reason_code": "profile_execution_unavailable",
                "reconfiguration_repository": "leanprover/lean-eval-submissions",
                "reconfiguration_commit": reconfiguration_commit,
                "reconfiguration_path": path,
                "reconfiguration_sha256": digest,
            },
        )
        enqueued = _event(
            timestamp=first + dt.timedelta(milliseconds=offset + 3),
            seed=args.event_id_seed,
            task_id=task["replay_task_id"],
            event_type="replay.enqueued",
            subject_id=task["replay_task_id"],
            cause=reconfigured["event_id"],
            payload={
                "result_id": task["result_id"],
                "measurement_config_digest": task["measurement_config_digest"],
                "execution_profile_digest": replacement["execution_profile_digest"],
                "checker": task["checker"],
                "benchmark_commit": task["benchmark_commit"],
            },
        )
        output_events.extend((unavailable, qualification, reconfigured, enqueued))
        expected[task["replay_task_id"]] = (
            task["attempt"],
            replacement["execution_profile_digest"],
            reconfigured["event_id"],
        )

    event_ids = [event["event_id"] for event in output_events]
    if (
        len(output_events) != artifact["task_count"] * 4
        or event_ids != sorted(event_ids)
        or len(event_ids) != len(set(event_ids))
    ):
        _error("candidate event identities are not exact, increasing, and unique")
    _write_and_validate_overlay(state_root, state_head, output_events, expected)

    output_root = args.output_directory.resolve()
    if output_root.exists() or output_root.is_symlink():
        _error("State batch output directory already exists")
    events_root = output_root / "events"
    events_root.mkdir(parents=True, mode=0o700)
    descriptors: list[dict[str, str]] = []
    for event in output_events:
        relative = f"events/{event['event_id'][:2]}/{event['event_id']}.json"
        destination = output_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        controller._write(
            destination, event, serializer=controller.state_canonical_bytes
        )
        descriptors.append(
            {
                "path": relative,
                "sha256": controller.sha256_bytes(
                    controller.state_canonical_bytes(event)
                ),
            }
        )
    manifest = {
        "schema_version": 1,
        "kind": "historical_private_reconfiguration_state_append_candidate",
        "selection": artifact["selection"],
        "state_repository": controller._state_repository(queue["environment"]),
        "expected_head": state_head,
        "reconfiguration_repository": "leanprover/lean-eval-submissions",
        "reconfiguration_commit": reconfiguration_commit,
        "reconfiguration_path": path,
        "reconfiguration_sha256": digest,
        "first_occurred_at": args.first_occurred_at,
        "event_id_seed": args.event_id_seed,
        "task_count": artifact["task_count"],
        "event_count": len(output_events),
        "events": descriptors,
    }
    manifest_path = output_root / "state-append-candidate.json"
    controller._write(manifest_path, manifest)
    return manifest_path


def _validate_state_batch_manifest(
    value: Any,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    manifest = controller._object(value, "private reconfiguration State manifest")
    controller._fields(
        manifest, STATE_BATCH_FIELDS, "private reconfiguration State manifest"
    )
    selection = manifest["selection"]
    task_count = 1 if selection == "failed_canary" else 46
    if (
        manifest["schema_version"] != 1
        or manifest["kind"]
        != "historical_private_reconfiguration_state_append_candidate"
        or selection not in {"failed_canary", "queued_remainder"}
        or manifest["state_repository"] != "leanprover/lean-eval-state"
        or manifest["reconfiguration_repository"]
        != "leanprover/lean-eval-submissions"
        or type(manifest["task_count"]) is not int
        or type(manifest["event_count"]) is not int
        or manifest["task_count"] != task_count
        or manifest["event_count"] != task_count * 4
        or not isinstance(manifest["events"], list)
        or len(manifest["events"]) != manifest["event_count"]
    ):
        _error("private reconfiguration State manifest identity is invalid")
    controller._match(
        controller.COMMIT, manifest["expected_head"], "manifest expected State head"
    )
    controller._match(
        controller.COMMIT,
        manifest["reconfiguration_commit"],
        "manifest reconfiguration commit",
    )
    digest = controller._match(
        controller.DIGEST,
        manifest["reconfiguration_sha256"],
        "manifest reconfiguration sha256",
    )
    if manifest["reconfiguration_path"] != (
        f"evidence/private-replay/reconfigurations/{digest}.json"
    ):
        _error("private reconfiguration State manifest locator is invalid")
    first = _parse_timestamp(manifest["first_occurred_at"], "manifest first occurred_at")
    if manifest["first_occurred_at"] != first.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z"):
        _error("private reconfiguration State manifest timestamp is not canonical")
    controller._match(
        controller.DIGEST, manifest["event_id_seed"], "manifest event id seed"
    )
    descriptors: list[dict[str, str]] = []
    for index, descriptor_value in enumerate(manifest["events"]):
        descriptor = controller._object(
            descriptor_value, f"State event descriptor[{index}]"
        )
        controller._fields(
            descriptor, {"path", "sha256"}, f"State event descriptor[{index}]"
        )
        if EVENT_PATH.fullmatch(str(descriptor["path"])) is None:
            _error("State event descriptor path is invalid")
        controller._match(
            controller.DIGEST,
            descriptor["sha256"],
            f"State event descriptor[{index}].sha256",
        )
        descriptors.append(descriptor)
    paths = [descriptor["path"] for descriptor in descriptors]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        _error("State event descriptors are not unique and sorted")
    return manifest, descriptors


def _load_exact_state_batch(
    manifest_path: pathlib.Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[bytes]]:
    if manifest_path.name != "state-append-candidate.json":
        _error("State batch manifest filename is invalid")
    manifest, _ = controller._load_canonical(
        manifest_path, "private reconfiguration State manifest"
    )
    manifest, descriptors = _validate_state_batch_manifest(manifest)
    root = manifest_path.parent
    expected_files = {"state-append-candidate.json"}.union(
        descriptor["path"] for descriptor in descriptors
    )
    expected_directories = {"events"}
    for relative in expected_files:
        parent = pathlib.PurePosixPath(relative).parent
        while parent != pathlib.PurePosixPath("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    observed_files: set[str] = set()
    for item in root.rglob("*"):
        relative = item.relative_to(root).as_posix()
        if item.is_symlink():
            _error("State batch contains a symlink")
        if item.is_file():
            observed_files.add(relative)
        elif not item.is_dir() or relative not in expected_directories:
            _error("State batch contains an undeclared filesystem entry")
    if observed_files != expected_files:
        _error("State batch files differ from the closed manifest")
    events: list[dict[str, Any]] = []
    event_bytes: list[bytes] = []
    for descriptor in descriptors:
        event, raw = controller._load_state_canonical(
            root / descriptor["path"], "manifest-bound State event"
        )
        if controller.sha256_bytes(raw) != descriptor["sha256"]:
            _error("manifest-bound State event digest differs")
        event_id = controller._match(
            controller.UUID7, event.get("event_id"), "manifest-bound State event_id"
        )
        if descriptor["path"] != f"events/{event_id[:2]}/{event_id}.json":
            _error("manifest-bound State event path differs from its identity")
        events.append(event)
        event_bytes.append(raw)
    return manifest, events, event_bytes


def _fetch_exact_state_main(state_root: pathlib.Path, expected_head: str) -> None:
    try:
        subprocess.run(
            [
                "git", "-C", str(state_root), "fetch", "--no-tags", "origin",
                "refs/heads/main:refs/remotes/origin/main",
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _error("protected State main could not be refreshed", error)
    remote_head = _git(state_root, "rev-parse", "refs/remotes/origin/main")
    local_head = _git(state_root, "rev-parse", "HEAD")
    if remote_head != expected_head or local_head != expected_head:
        _error("protected State main no longer equals the manifest parent")


def _rederive_exact_state_batch(
    *,
    repository_root: pathlib.Path,
    state_root: pathlib.Path,
    manifest_path: pathlib.Path,
    manifest: dict[str, Any],
    event_bytes: list[bytes],
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = pathlib.Path(directory) / "derived"
        derived_manifest_path = render_state_batch(
            argparse.Namespace(
                state_root=state_root,
                repository_root=repository_root,
                reconfiguration_commit=manifest["reconfiguration_commit"],
                reconfiguration_path=manifest["reconfiguration_path"],
                first_occurred_at=manifest["first_occurred_at"],
                event_id_seed=manifest["event_id_seed"],
                output_directory=output,
            )
        )
        _, _, derived_event_bytes = _load_exact_state_batch(derived_manifest_path)
        if (
            manifest_path.read_bytes() != derived_manifest_path.read_bytes()
            or event_bytes != derived_event_bytes
        ):
            _error("State batch differs from its independently derived exact batch")


def publish_state_branch(args: argparse.Namespace) -> dict[str, Any]:
    """Publish only a new review branch containing one exact validated batch."""

    state_root = args.state_root.resolve()
    repository_root = args.repository_root.resolve()
    manifest_path = args.manifest.resolve()
    manifest, events, event_bytes = _load_exact_state_batch(manifest_path)
    expected_head = manifest["expected_head"]
    controller._verify_checkout(
        state_root,
        manifest["state_repository"],
        minimum_commit=controller.STATE_MINIMUM_COMMITS["production"],
    )
    if controller._state_environment(state_root) != "production":
        _error("State batch publisher is production-only")
    _fetch_exact_state_main(state_root, expected_head)
    controller._verify_checkout(
        repository_root, manifest["reconfiguration_repository"]
    )
    _rederive_exact_state_batch(
        repository_root=repository_root,
        state_root=state_root,
        manifest_path=manifest_path,
        manifest=manifest,
        event_bytes=event_bytes,
    )
    _write_and_validate_overlay(state_root, expected_head, events, {})
    branch = (
        "historical-private-reconfiguration-"
        f"{manifest['selection'].replace('_', '-')}-"
        f"{manifest['reconfiguration_sha256'][:12]}"
    )
    if STATE_BRANCH.fullmatch(branch) is None:
        _error("derived State review branch is invalid")
    branch_ref = f"refs/heads/{branch}"

    probe = subprocess.run(
        ["git", "-C", str(state_root), "ls-remote", "--exit-code", "--heads", "origin", branch_ref],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=120,
        check=False,
    )
    if probe.returncode == 0:
        _error("derived State review branch already exists")
    if probe.returncode != 2:
        _error("derived State review branch absence could not be proved")

    with tempfile.TemporaryDirectory() as directory:
        worktree = pathlib.Path(directory) / "state"
        added = False
        try:
            subprocess.run(
                [
                    "git", "-C", str(state_root), "worktree", "add", "--detach",
                    str(worktree), expected_head,
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            added = True
            paths: list[str] = []
            for event, raw in zip(events, event_bytes, strict=True):
                relative = f"events/{event['event_id'][:2]}/{event['event_id']}.json"
                destination = worktree / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                controller._write_bytes(destination, raw)
                paths.append(relative)
            subprocess.run(
                [sys.executable, str(worktree / "scripts/state.py"), "--root", str(worktree), "validate"],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=300,
            )
            subprocess.run(
                ["git", "-C", str(worktree), "add", "--", *paths], check=True
            )
            changes = _git(worktree, "diff", "--cached", "--name-status").splitlines()
            expected_changes = [f"A\t{path}" for path in paths]
            if changes != expected_changes:
                _error("State review commit contains files outside the manifest")
            subprocess.run(
                [
                    "git", "-C", str(worktree),
                    "-c", "user.name=lean-eval-replay-controller",
                    "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit", "--no-gpg-sign", "--message",
                    f"Reconfigure historical private replay {manifest['selection']}",
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            commit = controller._match(
                controller.COMMIT, _git(worktree, "rev-parse", "HEAD"),
                "State review commit",
            )
            if _git(worktree, "rev-parse", "HEAD^") != expected_head:
                _error("State review commit is not rooted at the manifest parent")
            _fetch_exact_state_main(state_root, expected_head)
            push = subprocess.run(
                [
                    "git", "-C", str(worktree), "push", "--porcelain", "origin",
                    f"HEAD:{branch_ref}",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if push.returncode != 0 or "[new branch]" not in push.stdout:
                _error("new isolated State review branch was not created")
            readback = subprocess.run(
                ["git", "-C", str(state_root), "ls-remote", "--heads", "origin", branch_ref],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=120,
            ).stdout.split()
            if readback != [commit, branch_ref]:
                _error("State review branch readback differs from the exact commit")
        except (OSError, subprocess.SubprocessError) as error:
            _error("State review branch publication failed", error)
        finally:
            if added:
                subprocess.run(
                    ["git", "-C", str(state_root), "worktree", "remove", "--force", str(worktree)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
    repository_name = manifest["state_repository"].split("/", 1)[1]
    return {
        "schema_version": 1,
        "state_repository": manifest["state_repository"],
        "expected_head": expected_head,
        "branch": branch,
        "commit": commit,
        "compare_url": (
            f"https://github.com/leanprover/{repository_name}/compare/"
            f"main...{branch}?expand=1"
        ),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    decision = commands.add_parser("prepare-decision")
    decision.add_argument("--state-root", required=True, type=pathlib.Path)
    decision.add_argument("--repository-root", required=True, type=pathlib.Path)
    decision.add_argument("--audit-root", required=True, type=pathlib.Path)
    decision.add_argument(
        "--selection",
        required=True,
        choices=("failed_canary", "queued_remainder"),
    )
    decision.add_argument(
        "--replacement-profile", required=True, action="append"
    )
    decision.add_argument("--output-directory", required=True, type=pathlib.Path)

    batch = commands.add_parser("render-state-batch")
    batch.add_argument("--state-root", required=True, type=pathlib.Path)
    batch.add_argument("--repository-root", required=True, type=pathlib.Path)
    batch.add_argument("--reconfiguration-commit", required=True)
    batch.add_argument("--reconfiguration-path", required=True)
    batch.add_argument("--first-occurred-at", required=True)
    batch.add_argument("--event-id-seed", required=True)
    batch.add_argument("--output-directory", required=True, type=pathlib.Path)

    publish = commands.add_parser("publish-state-branch")
    publish.add_argument("--state-root", required=True, type=pathlib.Path)
    publish.add_argument("--repository-root", required=True, type=pathlib.Path)
    publish.add_argument("--manifest", required=True, type=pathlib.Path)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "prepare-decision":
            output: pathlib.Path | dict[str, Any] = prepare_decision(args)
        elif args.command == "render-state-batch":
            output = render_state_batch(args)
        else:
            output = publish_state_branch(args)
    except (controller.HistoricalPrivateReplayControllerError, ReconfigurationPreparationError) as error:
        print(f"historical-private-reconfiguration: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    if isinstance(output, pathlib.Path):
        print(output)
    else:
        sys.stdout.buffer.write(controller.canonical_bytes(output))


if __name__ == "__main__":
    main()
