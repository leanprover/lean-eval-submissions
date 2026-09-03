#!/usr/bin/env python3
"""Select and bind only legacy archives required by one final-delta packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

from migrate_archive_envelopes import (
    FINAL_DELTA_AUDIT_REVIEW_BRANCH,
    PLAN_ENTRY_DIGEST_DOMAIN,
    MigrationError,
    _canonical_bytes,
    _load_plan,
)

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
PREPARATION_KIND = "historical_final_delta_preparation_packet"
BINDING_KIND = "historical_final_delta_archive_migration_binding"
SUBMISSIONS_REPOSITORY = "leanprover/lean-eval-submissions"
AUDIT_REPOSITORY = "leanprover/lean-eval-audit"
MAX_BYTES = 16 * 1024 * 1024


class SelectionError(ValueError):
    """The final-delta archive selection is incomplete or not immutable."""


def canonical(value: Any) -> bytes:
    try:
        raw = (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()
    except (TypeError, ValueError, UnicodeError) as error:
        raise SelectionError("value is not canonicalizable JSON") from error
    if not 0 < len(raw) <= MAX_BYTES:
        raise SelectionError("canonical JSON exceeds its bound")
    return raw


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_canonical(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise SelectionError(f"{label} must be one regular file")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SelectionError(f"cannot read {label}") from error
    if not isinstance(value, dict) or canonical(value) != raw:
        raise SelectionError(f"{label} is not canonical JSON")
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
        raise SelectionError("exact Git binding failed") from error


def verify_preparation_checkout(
    root: pathlib.Path, commit: str, path: str, expected_sha256: str
) -> pathlib.Path:
    if COMMIT.fullmatch(commit) is None or DIGEST.fullmatch(expected_sha256) is None:
        raise SelectionError("preparation Git identity is invalid")
    if (
        pathlib.Path(git(root, "rev-parse", "--show-toplevel")).resolve()
        != root.resolve()
    ):
        raise SelectionError("preparation checkout is not the repository root")
    head = git(root, "rev-parse", "HEAD")
    if git(root, "status", "--porcelain"):
        raise SelectionError("preparation checkout is not clean")
    try:
        git(root, "merge-base", "--is-ancestor", commit, head)
    except SelectionError as error:
        raise SelectionError("preparation commit is not an ancestor") from error
    if git(root, "remote", "get-url", "origin") not in {
        "https://github.com/leanprover/lean-eval-submissions",
        "https://github.com/leanprover/lean-eval-submissions.git",
    }:
        raise SelectionError("preparation checkout remote is not canonical")
    if (
        path
        != f"evidence/historical-replay/final-delta-preparations/{expected_sha256}.json"
    ):
        raise SelectionError("preparation path is not content addressed")
    selected = root.joinpath(*path.split("/"))
    if git(root, "ls-files", "--error-unmatch", "--", path) != path:
        raise SelectionError("preparation is not tracked")
    if git(root, "hash-object", path) != git(root, "rev-parse", f"{commit}:{path}"):
        raise SelectionError("preparation differs from its committed blob")
    return selected


def select(
    *,
    preparation: dict[str, Any],
    preparation_raw: bytes,
    preparation_commit: str,
    preparation_path: str,
    full_plan: dict[str, Any],
    audit_tree: str,
    crosswalk_path: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        preparation.get("schema_version") != 1
        or preparation.get("kind") != PREPARATION_KIND
        or preparation.get("activation_status")
        != "blocked_pending_exact_profiles_and_state_append"
        or preparation.get("source_repository") != SUBMISSIONS_REPOSITORY
        or not isinstance(preparation.get("entries"), list)
    ):
        raise SelectionError("preparation packet identity is invalid")
    crosswalk = preparation.get("classification_inputs", {}).get(
        "private_crosswalk", {}
    )
    if (
        crosswalk.get("repository") != SUBMISSIONS_REPOSITORY
        or crosswalk.get("commit") != preparation_commit
        or crosswalk.get("audit_repository") != AUDIT_REPOSITORY
        or crosswalk.get("audit_commit") != full_plan.get("source_commit")
        or crosswalk_path != crosswalk.get("path")
        or crosswalk_path
        != f"evidence/historical-replay/private-crosswalks/{crosswalk.get('sha256')}.json"
        or full_plan.get("source_repository") != AUDIT_REPOSITORY
        or COMMIT.fullmatch(audit_tree) is None
    ):
        raise SelectionError("packet, crosswalk, and audit plan identities differ")
    wanted: set[str] = set()
    legacy_submission_ids: set[str] = set()
    for entry in preparation["entries"]:
        if not isinstance(entry, dict):
            raise SelectionError("preparation entry is invalid")
        if (
            entry.get("source_visibility") != "private"
            or entry.get("disposition") != "replayable"
        ):
            continue
        archive = entry.get("archive")
        if not isinstance(archive, dict):
            raise SelectionError("private replayable entry lacks an archive")
        version = archive.get("archive_schema_version")
        if version in {1, 2}:
            digest = archive.get("archive_plan_entry_sha256")
            submission_id = archive.get("submission_id")
            if DIGEST.fullmatch(str(digest)) is None or not isinstance(
                submission_id, str
            ):
                raise SelectionError("legacy archive binding is invalid")
            wanted.add(digest)
            legacy_submission_ids.add(submission_id)
        elif version != 3:
            raise SelectionError("private archive schema version is invalid")
    if len(wanted) != preparation.get("archive_migration", {}).get(
        "legacy_unique_archive_count"
    ) or len(legacy_submission_ids) != len(wanted):
        raise SelectionError("preparation legacy archive count is not exact")
    selected: list[dict[str, Any]] = []
    matched: set[str] = set()
    for entry in full_plan.get("entries", []):
        digest = sha256(PLAN_ENTRY_DIGEST_DOMAIN + _canonical_bytes(entry))
        if digest in wanted:
            if entry.get("source_schema_version") not in {1, 2}:
                raise SelectionError("selected archive is not legacy")
            selected.append(entry)
            matched.add(digest)
    if matched != wanted or len(selected) != len(wanted):
        raise SelectionError("legacy packet archives do not resolve uniquely")
    core = {
        "source_repository": AUDIT_REPOSITORY,
        "source_commit": full_plan["source_commit"],
        "entries": selected,
        "retained": [],
    }
    plan = {
        "schema_version": 1,
        **core,
        "migration_count": len(selected),
        "retained_count": 0,
        "inventory_digest": sha256(
            b"lean-eval-archive-envelope-migration-v1\0" + _canonical_bytes(core)
        ),
    }
    plan_raw = canonical(plan)
    preparation_sha = sha256(preparation_raw)
    binding = {
        "schema_version": 1,
        "kind": BINDING_KIND,
        "preparation_repository": SUBMISSIONS_REPOSITORY,
        "preparation_commit": preparation_commit,
        "preparation_path": preparation_path,
        "preparation_sha256": preparation_sha,
        "source_audit_commit": full_plan["source_commit"],
        "source_audit_tree": audit_tree,
        "crosswalk_path": crosswalk_path,
        "crosswalk_sha256": crosswalk["sha256"],
        "migration_plan_sha256": sha256(plan_raw),
        "migration_inventory_digest": plan["inventory_digest"],
        "migration_count": len(selected),
        "review_branch": FINAL_DELTA_AUDIT_REVIEW_BRANCH.removeprefix("refs/heads/"),
    }
    return plan, binding


def write_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise SelectionError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submissions-root", required=True, type=pathlib.Path)
    parser.add_argument("--preparation-commit", required=True)
    parser.add_argument("--preparation-path", required=True)
    parser.add_argument("--preparation-sha256", required=True)
    parser.add_argument("--crosswalk-path", required=True)
    parser.add_argument("--full-migration-plan", required=True, type=pathlib.Path)
    parser.add_argument("--audit-tree", required=True)
    parser.add_argument("--output-plan", required=True, type=pathlib.Path)
    parser.add_argument("--output-binding", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        root = args.submissions_root.resolve()
        preparation_path = verify_preparation_checkout(
            root,
            args.preparation_commit,
            args.preparation_path,
            args.preparation_sha256,
        )
        preparation, raw = read_canonical(preparation_path, "preparation packet")
        if sha256(raw) != args.preparation_sha256:
            raise SelectionError("preparation digest differs")
        plan, binding = select(
            preparation=preparation,
            preparation_raw=raw,
            preparation_commit=args.preparation_commit,
            preparation_path=args.preparation_path,
            full_plan=_load_plan(args.full_migration_plan.resolve()),
            audit_tree=args.audit_tree,
            crosswalk_path=args.crosswalk_path,
        )
        write_exclusive(args.output_plan, plan)
        write_exclusive(args.output_binding, binding)
    except (SelectionError, MigrationError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
