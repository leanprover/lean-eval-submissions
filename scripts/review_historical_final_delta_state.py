#!/usr/bin/env python3
"""Stage or revalidate the separate final-delta State candidate."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import review_historical_baseline_state_batch as review
from prepare_historical_final_delta_state import (
    final_lane_inventory,
    load_final_expectation,
)

review.REVIEW_BRANCH = "historical-final-delta-state-v1"
review.SUMMARY_KIND = "historical_final_delta_state_promotion_binding"
review.IMPLEMENTATION_PATHS = (
    ".github/workflows/historical-final-delta-state.yml",
    "configuration/historical-final-delta-state-batch-v1.json",
    "requirements-jsonschema-workflow.txt",
    "scripts/build_result_receipt.py",
    "scripts/classify_historical_private_archives.py",
    "scripts/historical_replay_controller.py",
    "scripts/inventory_historical_replay.py",
    "scripts/key_capability_contract.py",
    "scripts/prepare_historical_baseline_state_batch.py",
    "scripts/prepare_historical_final_delta_state.py",
    "scripts/prepare_historical_private_replay.py",
    "scripts/replay_orchestrator.py",
    "scripts/results_schema.py",
    "scripts/review_historical_baseline_state_batch.py",
    "scripts/review_historical_final_delta_state.py",
    "schemas/historical-private-profile-qualification-v1.schema.json",
    "schemas/historical-private-replay-plan-v1.schema.json",
    "schemas/replay-execution-profile-v1.schema.json",
)
review.load_expectation = load_final_expectation
review.lane_inventory = final_lane_inventory


def copy_candidate(
    state_root: pathlib.Path,
    candidate_root: pathlib.Path,
    manifest: dict,
) -> list[str]:
    descriptors = manifest.get("event_files")
    if not isinstance(descriptors, list) or not descriptors:
        raise review.BaselineBatchError("candidate manifest event inventory is invalid")
    paths: list[str] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
            raise review.BaselineBatchError("candidate event descriptor is invalid")
        relative = descriptor["path"]
        if review.EVENT_PATH.fullmatch(relative) is None:
            raise review.BaselineBatchError("candidate event path is invalid")
        _, raw = review.load_canonical(candidate_root / relative, "candidate event")
        if review.sha256(raw) != descriptor["sha256"]:
            raise review.BaselineBatchError("candidate event digest changed")
        target = state_root / relative
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            fd = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o644,
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
        except FileExistsError as error:
            raise review.BaselineBatchError("candidate event already exists") from error
        paths.append(relative)
    actual = sorted(
        path.relative_to(candidate_root).as_posix()
        for path in candidate_root.rglob("*")
        if path.is_file()
        and path.name != "historical-final-delta-state-append-candidate.json"
    )
    if sorted(paths) != actual or len(set(paths)) != len(paths):
        raise review.BaselineBatchError(
            "candidate tree contains missing or extra files"
        )
    return sorted(paths)


def stage(args) -> None:
    state_root = args.state_root.resolve()
    submissions_root = args.submissions_root.resolve()
    review.verify_state_checkout(state_root, args.state_parent)
    manifest, _ = review.load_canonical(args.manifest.resolve(), "candidate manifest")
    if (
        manifest.get("kind") != "historical_final_delta_state_append_candidate"
        or manifest.get("state", {}).get("expected_head") != args.state_parent
        or manifest.get("audit")
        != {
            "repository": review.AUDIT_REPOSITORY,
            "expected_head": args.audit_head,
            "expected_tree": args.audit_tree,
        }
    ):
        raise review.BaselineBatchError("candidate manifest binding differs")
    event_paths = copy_candidate(state_root, args.candidate_root.resolve(), manifest)
    additions = review.write_operational_view_additions(state_root, args.state_parent)
    review.git(state_root, "add", "-A", "--", "events", "views")
    subprocess.run(
        [
            sys.executable,
            str(state_root / "scripts/state.py"),
            "--root",
            str(state_root),
            "validate",
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    expected = sorted([*event_paths, *(item["path"] for item in additions)])
    if sorted(
        review.git(state_root, "diff", "--cached", "--name-only").splitlines()
    ) != expected or sorted(
        review.git(state_root, "diff", "--cached", "--name-status").splitlines()
    ) != sorted(f"A\t{path}" for path in expected):
        raise review.BaselineBatchError("State candidate is not create-only")
    review.git(state_root, "config", "user.name", "lean-eval-replay-controller")
    review.git(
        state_root,
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    review.git(
        state_root,
        "commit",
        "--no-gpg-sign",
        "--message",
        "Stage reviewed final historical delta",
    )
    candidate = review.git(state_root, "rev-parse", "HEAD")
    if review.git(
        state_root, "rev-list", "--parents", "-n", "1", candidate
    ).split() != [
        candidate,
        args.state_parent,
    ]:
        raise review.BaselineBatchError("candidate does not have the exact parent")
    summary = review.summarize(
        submissions_root,
        state_root,
        args.expectation.resolve(),
        args.implementation_commit,
        args.audit_head,
        args.audit_tree,
        args.state_parent,
        candidate,
        event_paths,
        require_implementation_head=True,
    )
    raw = review.canonical(summary)
    fd = os.open(
        args.output.resolve(),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def verify(args) -> None:
    state_root = args.state_root.resolve()
    submissions_root = args.submissions_root.resolve()
    binding, implementation = review.load_promotion_binding(args.binding)
    state = binding["state"]
    audit = binding["audit"]
    review.verify_implementation(submissions_root, implementation)
    candidate = state["candidate_commit"]
    parent = state["parent"]
    if (
        review.git(state_root, "rev-parse", "HEAD") != candidate
        or review.git(state_root, "status", "--porcelain")
        or review.git(state_root, "rev-list", "--parents", "-n", "1", candidate).split()
        != [candidate, parent]
    ):
        raise review.BaselineBatchError("staged State candidate differs")
    status = review.git(
        state_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "--no-renames",
        "-r",
        parent,
        candidate,
    ).splitlines()
    paths = [line.split("\t", 1)[1] for line in status if line.startswith("A\tevents/")]
    actual = review.summarize(
        submissions_root,
        state_root,
        submissions_root / "configuration/historical-final-delta-state-batch-v1.json",
        implementation["commit"],
        audit["head"],
        audit["tree"],
        parent,
        candidate,
        paths,
        require_implementation_head=False,
    )
    if actual != binding:
        raise review.BaselineBatchError(
            "staged candidate differs from promotion binding"
        )


review.copy_candidate = copy_candidate
review.stage = stage
review.verify = verify


if __name__ == "__main__":
    raise SystemExit(review.main())
