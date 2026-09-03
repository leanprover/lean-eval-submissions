#!/usr/bin/env python3
"""Stage or revalidate one exact historical-baseline State candidate."""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
from typing import Any

from prepare_historical_baseline_state_batch import (
    BaselineBatchError,
    COMMIT,
    EVENT_PATH,
    canonical,
    lane_inventory,
    load_canonical,
    load_event_tree,
    load_expectation,
    sha256,
    state_modules,
    verify_state_checkout,
)


REVIEW_BRANCH = "historical-baseline-state-v1"
STATE_REPOSITORY = "leanprover/lean-eval-state"
AUDIT_REPOSITORY = "leanprover/lean-eval-audit"
SUBMISSIONS_REPOSITORY = "leanprover/lean-eval-submissions"
SUMMARY_KIND = "historical_baseline_state_promotion_binding"
IMPLEMENTATION_PATHS = (
    ".github/workflows/append-historical-baseline-state.yml",
    "configuration/historical-baseline-state-batch-v1.json",
    "requirements-jsonschema-workflow.txt",
    "scripts/build_result_receipt.py",
    "scripts/classify_historical_private_archives.py",
    "scripts/historical_public_runner.py",
    "scripts/historical_replay_controller.py",
    "scripts/inventory_historical_replay.py",
    "scripts/key_capability_contract.py",
    "scripts/migrate_archive_envelopes.py",
    "scripts/prepare_historical_baseline_state_batch.py",
    "scripts/prepare_historical_private_replay.py",
    "scripts/prepare_historical_public_authority.py",
    "scripts/replay_orchestrator.py",
    "scripts/results_schema.py",
    "scripts/review_historical_baseline_state_batch.py",
    "schemas/historical-private-profile-qualification-v1.schema.json",
    "schemas/historical-private-replay-plan-v1.schema.json",
    "schemas/historical-public-profile-qualification-v1.schema.json",
    "schemas/replay-execution-profile-v1.schema.json",
)
MAX_SUMMARY_BYTES = 128 * 1024


def git(root: pathlib.Path, *arguments: str, maximum: int = 32 * 1024 * 1024) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BaselineBatchError("exact Git proof failed") from error
    if len(result.stdout.encode("utf-8")) > maximum:
        raise BaselineBatchError("exact Git proof exceeded its bound")
    return result.stdout.strip()


def commit_blob(root: pathlib.Path, commit: str, relative: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{relative}"],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BaselineBatchError("reviewed implementation blob is unavailable") from error
    if len(result.stdout) > 16 * 1024 * 1024:
        raise BaselineBatchError("reviewed implementation blob exceeds its bound")
    return result.stdout


def event_descriptors(root: pathlib.Path, relative_paths: list[str]) -> list[dict[str, str]]:
    descriptors: list[dict[str, str]] = []
    for relative in relative_paths:
        if EVENT_PATH.fullmatch(relative) is None:
            raise BaselineBatchError("candidate contains a non-event path")
        path = root / relative
        value, raw = load_canonical(path, "candidate event")
        event_id = value.get("event_id")
        if relative != f"events/{str(event_id).replace('-', '')[:2]}/{event_id}.json":
            raise BaselineBatchError("candidate event identity and path differ")
        descriptors.append({"path": relative, "sha256": sha256(raw)})
    return sorted(descriptors, key=lambda item: item["path"])


def queue_task_ids(queue: Any, label: str) -> list[str]:
    if not isinstance(queue, dict) or not isinstance(queue.get("tasks"), list):
        raise BaselineBatchError(f"{label} is invalid")
    identities = [item.get("replay_task_id") for item in queue["tasks"] if isinstance(item, dict)]
    if len(identities) != len(queue["tasks"]) or not all(isinstance(item, str) for item in identities):
        raise BaselineBatchError(f"{label} task identities are invalid")
    return sorted(identities)


def implementation_binding(
    root: pathlib.Path, commit: str, *, require_head: bool = False
) -> dict[str, Any]:
    if COMMIT.fullmatch(commit) is None or (
        require_head and git(root, "rev-parse", "HEAD") != commit
    ):
        raise BaselineBatchError("implementation checkout is not the exact stage commit")
    if pathlib.Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise BaselineBatchError("implementation root is not the checkout root")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise BaselineBatchError("implementation checkout is not clean")
    if git(root, "remote", "get-url", "origin") not in {
        "https://github.com/leanprover/lean-eval-submissions",
        "https://github.com/leanprover/lean-eval-submissions.git",
    }:
        raise BaselineBatchError("implementation checkout remote is not canonical")
    return {
        "repository": SUBMISSIONS_REPOSITORY,
        "commit": commit,
        "blobs": [
            {"path": path, "sha256": sha256(commit_blob(root, commit, path))}
            for path in IMPLEMENTATION_PATHS
        ],
    }


def load_promotion_binding(path: pathlib.Path) -> tuple[dict[str, Any], dict[str, Any]]:
    binding, raw = load_canonical(path.resolve(), "promotion binding")
    if len(raw) > MAX_SUMMARY_BYTES:
        raise BaselineBatchError("promotion binding exceeds its bound")
    state = binding.get("state")
    audit = binding.get("audit")
    implementation = binding.get("implementation")
    if (
        binding.get("schema_version") != 1
        or binding.get("kind") != SUMMARY_KIND
        or binding.get("environment") != "production"
        or binding.get("review_status") != "staged_for_review"
        or binding.get("review_branch") != REVIEW_BRANCH
        or not isinstance(state, dict)
        or not isinstance(audit, dict)
        or not isinstance(implementation, dict)
    ):
        raise BaselineBatchError("promotion binding identity is invalid")
    return binding, implementation


def verify_implementation(
    submissions_root: pathlib.Path,
    implementation: dict[str, Any],
) -> None:
    implementation_commit = implementation.get("commit")
    if not isinstance(implementation_commit, str) or COMMIT.fullmatch(implementation_commit) is None:
        raise BaselineBatchError("promotion implementation commit is invalid")
    expected = implementation_binding(submissions_root, implementation_commit)
    if implementation != expected:
        raise BaselineBatchError("reviewed implementation binding differs")
    git(submissions_root, "merge-base", "--is-ancestor", implementation_commit, "HEAD")
    for blob in expected["blobs"]:
        current = submissions_root / blob["path"]
        try:
            raw_current = current.read_bytes()
        except OSError as error:
            raise BaselineBatchError("current implementation blob is unavailable") from error
        if len(raw_current) > 16 * 1024 * 1024 or sha256(raw_current) != blob["sha256"]:
            raise BaselineBatchError("promotion implementation changed after staging")


def copy_candidate(
    state_root: pathlib.Path,
    candidate_root: pathlib.Path,
    manifest: dict[str, Any],
) -> list[str]:
    descriptors = manifest.get("event_files")
    if not isinstance(descriptors, list) or not descriptors:
        raise BaselineBatchError("candidate manifest event inventory is invalid")
    expected_paths: list[str] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
            raise BaselineBatchError("candidate event descriptor is invalid")
        relative = descriptor["path"]
        digest = descriptor["sha256"]
        if not isinstance(relative, str) or EVENT_PATH.fullmatch(relative) is None:
            raise BaselineBatchError("candidate event path is invalid")
        source = candidate_root / relative
        _, raw = load_canonical(source, "candidate event")
        if sha256(raw) != digest:
            raise BaselineBatchError("candidate event digest changed")
        target = state_root / relative
        if target.parent.is_symlink():
            raise BaselineBatchError("candidate State shard is a symlink")
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor_fd = os.open(target, flags, 0o644)
            with os.fdopen(descriptor_fd, "wb") as stream:
                stream.write(raw)
        except FileExistsError as error:
            raise BaselineBatchError("candidate event already exists in State") from error
        expected_paths.append(relative)
    actual = sorted(
        path.relative_to(candidate_root).as_posix()
        for path in candidate_root.rglob("*")
        if path.is_file() and path.name != "historical-baseline-state-append-candidate.json"
    )
    if sorted(expected_paths) != actual or len(set(expected_paths)) != len(expected_paths):
        raise BaselineBatchError("candidate tree contains missing or extra files")
    return sorted(expected_paths)


def summarize(
    submissions_root: pathlib.Path,
    state_root: pathlib.Path,
    expectation_path: pathlib.Path,
    implementation_commit: str,
    audit_head: str,
    audit_tree: str,
    parent: str,
    candidate_commit: str,
    event_paths: list[str],
    *,
    require_implementation_head: bool,
) -> dict[str, Any]:
    expectation = load_expectation(expectation_path)
    events = load_event_tree(state_root, "staged State")
    candidate_path_set = set(event_paths)
    candidate_events = [
        event
        for event in events
        if f"events/{event['event_id'].replace('-', '')[:2]}/{event['event_id']}.json"
        in candidate_path_set
    ]
    if (
        len(candidate_events) != expectation["total_event_count"]
        or len(candidate_events) != len(event_paths)
        or len(candidate_path_set) != len(event_paths)
    ):
        raise BaselineBatchError("candidate event set does not equal the closed expectation")
    lane_events: dict[str, list[dict[str, Any]]] = {}
    # Enqueue events share one type. Partition them by their qualification causation edge.
    for lane in ("public", "private"):
        qualification_ids = {
            event["event_id"]
            for event in candidate_events
            if event.get("event_type") == expectation["lanes"][lane]["qualification_event_type"]
        }
        lane_events[lane] = [
            event
            for event in candidate_events
            if event.get("event_type")
            in {
                expectation["lanes"][lane]["authority_event_type"],
                expectation["lanes"][lane]["qualification_event_type"],
            }
            or (
                event.get("event_type") == expectation["lanes"][lane]["enqueue_event_type"]
                and event.get("causation_event_id") in qualification_ids
            )
        ]
    inventories = {
        lane: lane_inventory(lane, lane_events[lane], expectation)
        for lane in ("public", "private")
    }
    lane_ids = {
        lane: {event["event_id"] for event in lane_events[lane]}
        for lane in ("public", "private")
    }
    candidate_ids = {event["event_id"] for event in candidate_events}
    if (
        lane_ids["public"].intersection(lane_ids["private"])
        or lane_ids["public"].union(lane_ids["private"]) != candidate_ids
        or set(inventories["public"]["task_ids"]).intersection(
            inventories["private"]["task_ids"]
        )
        or set(inventories["public"]["result_ids"]).intersection(
            inventories["private"]["result_ids"]
        )
        or sum(inventory["event_count"] for inventory in inventories.values())
        != expectation["total_event_count"]
        or sum(inventory["task_count"] for inventory in inventories.values())
        != expectation["total_task_count"]
    ):
        raise BaselineBatchError("candidate lanes do not exactly partition the event set")
    with state_modules(state_root) as (validator, materializer, projection):
        environment, combined = validator.load_tree(state_root)
        if environment != "production":
            raise BaselineBatchError("staged State environment is not production")
        validator.validate_semantics(combined, environment)
        views = materializer.materialize(environment, combined)
        queues: dict[str, Any] = {}
        for lane, name in {
            "public": "historical-public-replay-queue.json",
            "private": "historical-private-replay-queue.json",
        }.items():
            queue = views[name]
            task_ids = queue_task_ids(queue, name)
            if task_ids != inventories[lane]["task_ids"]:
                raise BaselineBatchError("staged materialized queue differs from candidate")
            queues[lane] = {
                "path": name,
                "sha256": sha256(canonical(queue)),
                "task_count": len(task_ids),
                "task_id_set_sha256": sha256(canonical(task_ids)),
            }
        public = projection.project_public_state_v6(environment, combined, candidate_commit)
        series = public["historical_replay_series"]
        if not isinstance(series, list) or not all(
            isinstance(item, dict) and isinstance(item.get("replay_task_id"), str)
            for item in series
        ):
            raise BaselineBatchError("redacted replay projection is invalid")
        projected_task_ids = sorted(item["replay_task_id"] for item in series)
        expected_task_ids = sorted(
            [*inventories["public"]["task_ids"], *inventories["private"]["task_ids"]]
        )
        if projected_task_ids != expected_task_ids:
            raise BaselineBatchError("redacted projection does not contain the exact task set")
        unavailable = public["historical_replay_unavailability"]
        if not isinstance(unavailable, list) or not all(
            isinstance(item, dict) and item.get("source_visibility") in {"public", "private"}
            for item in unavailable
        ):
            raise BaselineBatchError("redacted unavailability projection is invalid")
        unavailable_counts = {
            lane: sum(item["source_visibility"] == lane for item in unavailable)
            for lane in ("public", "private")
        }
        unavailable_counts["total"] = len(unavailable)
        if unavailable_counts != expectation["reviewed_unavailability_counts"]:
            raise BaselineBatchError("reviewed unavailability corpus changed")
        historical = {
            "historical_replay_series": series,
            "historical_replay_unavailability": unavailable,
        }
        view_descriptors = [
            {"path": path, "sha256": sha256(canonical(value))}
            for path, value in sorted(views.items())
        ]
    event_files = event_descriptors(state_root, event_paths)
    occurred = sorted(event["occurred_at"] for event in candidate_events)
    lane_summary = {
        lane: {
            key: inventories[lane][key]
            for key in (
                "event_count",
                "task_count",
                "event_set_sha256",
                "event_id_set_sha256",
                "task_id_set_sha256",
                "result_id_set_sha256",
            )
        }
        for lane in ("public", "private")
    }
    parent_paths = git(state_root, "ls-tree", "-r", "--name-only", parent, "events").splitlines()
    if any(EVENT_PATH.fullmatch(path) is None for path in parent_paths):
        raise BaselineBatchError("parent State event inventory is invalid")
    parent_event_ids = sorted(pathlib.PurePosixPath(path).stem for path in parent_paths)
    if (
        len(parent_event_ids) != len(set(parent_event_ids))
        or set(parent_event_ids).intersection(candidate_ids)
        or len(events) != len(parent_event_ids) + len(candidate_events)
    ):
        raise BaselineBatchError("combined State event inventory is not an exact append")
    return {
        "schema_version": 1,
        "kind": SUMMARY_KIND,
        "environment": "production",
        "review_status": "staged_for_review",
        "review_branch": REVIEW_BRANCH,
        "implementation": implementation_binding(
            submissions_root,
            implementation_commit,
            require_head=require_implementation_head,
        ),
        "audit": {
            "repository": AUDIT_REPOSITORY,
            "head": audit_head,
            "tree": audit_tree,
        },
        "state": {
            "repository": STATE_REPOSITORY,
            "parent": parent,
            "parent_tree": git(state_root, "rev-parse", f"{parent}^{{tree}}"),
            "candidate_commit": candidate_commit,
            "candidate_tree": git(state_root, "rev-parse", f"{candidate_commit}^{{tree}}"),
            "base_event_count": len(parent_event_ids),
            "base_event_id_set_sha256": sha256(canonical(parent_event_ids)),
            "combined_event_count": len(events),
        },
        "candidate": {
            "event_count": len(event_paths),
            "event_set_sha256": sha256(canonical(event_files)),
            "first_occurred_at": occurred[0],
            "last_occurred_at": occurred[-1],
            "lanes": lane_summary,
            "queues": queues,
            "materialized_views_sha256": sha256(canonical(view_descriptors)),
            "redacted_historical_projection_sha256": sha256(canonical(historical)),
            "redacted_historical_series_sha256": sha256(
                canonical(series)
            ),
            "reviewed_unavailability_counts": unavailable_counts,
            "reviewed_unavailability_sha256": sha256(
                canonical(unavailable)
            ),
        },
    }


def stage(args: argparse.Namespace) -> None:
    state_root = args.state_root.resolve()
    submissions_root = args.submissions_root.resolve()
    verify_state_checkout(state_root, args.state_parent)
    manifest, _ = load_canonical(args.manifest.resolve(), "candidate manifest")
    if (
        manifest.get("kind") != "historical_baseline_state_append_candidate"
        or manifest.get("state", {}).get("expected_head") != args.state_parent
        or manifest.get("audit")
        != {
            "repository": AUDIT_REPOSITORY,
            "expected_head": args.audit_head,
            "expected_tree": args.audit_tree,
        }
    ):
        raise BaselineBatchError("candidate manifest binding differs")
    event_paths = copy_candidate(state_root, args.candidate_root.resolve(), manifest)
    subprocess.run(
        [sys.executable, str(state_root / "scripts/state.py"), "--root", str(state_root), "validate"],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    git(state_root, "add", "--", "events")
    staged_paths = git(state_root, "diff", "--cached", "--name-only").splitlines()
    status_lines = git(state_root, "diff", "--cached", "--name-status").splitlines()
    if sorted(staged_paths) != event_paths or any(
        not line.startswith("A\t") for line in status_lines
    ):
        raise BaselineBatchError("State candidate is not the exact create-only event set")
    git(state_root, "config", "user.name", "lean-eval-replay-controller")
    git(state_root, "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git(state_root, "commit", "--no-gpg-sign", "--message", "Stage reviewed historical replay baseline")
    candidate_commit = git(state_root, "rev-parse", "HEAD")
    if git(state_root, "rev-list", "--parents", "-n", "1", candidate_commit).split() != [candidate_commit, args.state_parent]:
        raise BaselineBatchError("staged State candidate does not have the exact parent")
    summary = summarize(
        submissions_root,
        state_root,
        args.expectation.resolve(),
        args.implementation_commit,
        args.audit_head,
        args.audit_tree,
        args.state_parent,
        candidate_commit,
        event_paths,
        require_implementation_head=True,
    )
    raw = canonical(summary)
    if len(raw) > MAX_SUMMARY_BYTES:
        raise BaselineBatchError("source-free promotion binding exceeds its bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(args.output.resolve(), flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def verify(args: argparse.Namespace) -> None:
    state_root = args.state_root.resolve()
    submissions_root = args.submissions_root.resolve()
    binding, implementation = load_promotion_binding(args.binding)
    state = binding["state"]
    audit = binding["audit"]
    implementation_commit = implementation.get("commit")
    candidate_commit = state.get("candidate_commit")
    parent = state.get("parent")
    if not all(
        isinstance(value, str) and COMMIT.fullmatch(value)
        for value in (implementation_commit, candidate_commit, parent, audit.get("head"), audit.get("tree"))
    ):
        raise BaselineBatchError("promotion binding commits are invalid")
    verify_implementation(submissions_root, implementation)
    if git(state_root, "rev-parse", "HEAD") != candidate_commit:
        raise BaselineBatchError("State checkout is not the staged candidate")
    parents = git(state_root, "rev-list", "--parents", "-n", "1", candidate_commit).split()
    if parents != [candidate_commit, parent] or git(state_root, "rev-parse", f"{candidate_commit}^{{tree}}") != state.get("candidate_tree"):
        raise BaselineBatchError("staged candidate commit or tree differs")
    status_lines = git(state_root, "diff-tree", "--no-commit-id", "--name-status", "-r", parent, candidate_commit).splitlines()
    if not status_lines or any(not line.startswith("A\t") for line in status_lines):
        raise BaselineBatchError("staged candidate is not create-only")
    event_paths = [line.split("\t", 1)[1] for line in status_lines]
    actual = summarize(
        submissions_root,
        state_root,
        submissions_root / "configuration/historical-baseline-state-batch-v1.json",
        implementation_commit,
        audit["head"],
        audit["tree"],
        parent,
        candidate_commit,
        event_paths,
        require_implementation_head=False,
    )
    if actual != binding:
        raise BaselineBatchError("staged candidate differs from the committed promotion binding")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--submissions-root", required=True, type=pathlib.Path)
    stage_parser.add_argument("--state-root", required=True, type=pathlib.Path)
    stage_parser.add_argument("--state-parent", required=True)
    stage_parser.add_argument("--audit-head", required=True)
    stage_parser.add_argument("--audit-tree", required=True)
    stage_parser.add_argument("--implementation-commit", required=True)
    stage_parser.add_argument("--expectation", required=True, type=pathlib.Path)
    stage_parser.add_argument("--candidate-root", required=True, type=pathlib.Path)
    stage_parser.add_argument("--manifest", required=True, type=pathlib.Path)
    stage_parser.add_argument("--output", required=True, type=pathlib.Path)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--submissions-root", required=True, type=pathlib.Path)
    verify_parser.add_argument("--state-root", required=True, type=pathlib.Path)
    verify_parser.add_argument("--binding", required=True, type=pathlib.Path)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        for name in ("state_parent", "audit_head", "audit_tree", "implementation_commit"):
            value = getattr(args, name, None)
            if value is not None and COMMIT.fullmatch(value) is None:
                raise BaselineBatchError(f"{name.replace('_', ' ')} is invalid")
        if args.command == "stage":
            stage(args)
        else:
            verify(args)
        return 0
    except BaselineBatchError as error:
        print(f"historical-baseline-state-review: {error}", file=sys.stderr)
        return 1
    except (OSError, TypeError, UnicodeError, ValueError, subprocess.CalledProcessError):
        print("historical-baseline-state-review: validation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
