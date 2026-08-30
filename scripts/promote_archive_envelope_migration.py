#!/usr/bin/env python3
"""Prepare and read back the one fixed historical archive migration promotion.

Delete this one-shot helper and its focused test after final-delta promotion and
readback. It is not persistent replay or qualification machinery.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
from typing import Any

import migrate_archive_envelopes as migration


SOURCE_COMMIT = migration.CANONICAL_AUDIT_COMMIT
INVENTORY_DIGEST = migration.CANONICAL_SELECTED_INVENTORY_DIGEST
MIGRATION_COUNT = migration.CANONICAL_BOUND_ARCHIVE_COUNT
REVIEW_REF = migration.AUDIT_REVIEW_BRANCH
MAIN_REF = "refs/heads/main"
PROMOTION_BRANCH = "archive-file-key-rewrap-v1-promotion"
PROMOTION_REF = f"refs/heads/{PROMOTION_BRANCH}"
COMMIT = migration.COMMIT
DIGEST = migration.DIGEST
TIMESTAMP = re.compile(r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


class PromotionError(ValueError):
    """The staged migration cannot be promoted under the fixed contract."""


def _git(
    root: pathlib.Path,
    *args: str,
    check: bool = True,
    input_bytes: bytes | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    environment = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"}
    if extra_env:
        environment.update(extra_env)
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        input=input_bytes,
        capture_output=True,
        env=environment,
    )
    if check and completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PromotionError(f"git {args[0]} failed: {diagnostic}")
    return completed


def _object_id(value: str, label: str) -> str:
    if not isinstance(value, str) or COMMIT.fullmatch(value) is None:
        raise PromotionError(f"{label} must be one lowercase full object ID")
    return value


def _digest(value: str, label: str) -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise PromotionError(f"{label} must be one lowercase SHA-256")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise PromotionError(f"cannot hash {path}") from error
    return digest.hexdigest()


def _files_equal(first: pathlib.Path, second: pathlib.Path) -> bool:
    try:
        if first.stat().st_size != second.stat().st_size:
            return False
        with first.open("rb") as left, second.open("rb") as right:
            while True:
                left_chunk = left.read(1024 * 1024)
                right_chunk = right.read(1024 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError as error:
        raise PromotionError("cannot compare promotion patch files") from error


def _write_bytes(path: pathlib.Path, value: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise PromotionError(f"refusing to overwrite {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as destination:
            destination.write(value)
    except OSError as error:
        raise PromotionError(f"cannot write {path}") from error


def _write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    _write_bytes(
        path,
        (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(),
    )


def _read_json(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PromotionError(f"{label} is not one UTF-8 JSON object") from error
    if not isinstance(value, dict):
        raise PromotionError(f"{label} must be one JSON object")
    return value


def _validate_repository(root: pathlib.Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise PromotionError("audit root must be one real directory")
    if _git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() != b"true":
        raise PromotionError("audit root must be one non-bare worktree")
    origin = _git(root, "remote", "get-url", "origin").stdout.decode().strip()
    if origin not in migration.AUDIT_ORIGINS:
        raise PromotionError("audit checkout origin is not canonical")
    if _git(root, "status", "--porcelain").stdout:
        raise PromotionError("audit checkout is not clean")
    if _git(root, "replace", "-l").stdout:
        raise PromotionError("audit checkout contains replacement objects")


def _remote_ref(root: pathlib.Path, ref: str) -> str:
    result = _git(root, "ls-remote", "--refs", "origin", ref, check=False)
    if result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise PromotionError(f"could not resolve remote {ref}: {diagnostic}")
    lines = [line for line in result.stdout.decode().splitlines() if line]
    if len(lines) != 1:
        raise PromotionError(f"remote {ref} did not resolve exactly once")
    fields = lines[0].split("\t")
    if len(fields) != 2 or fields[1] != ref or COMMIT.fullmatch(fields[0]) is None:
        raise PromotionError(f"remote {ref} response is invalid")
    return fields[0]


def _require_remote_ref_absent(root: pathlib.Path, ref: str) -> None:
    result = _git(root, "ls-remote", "--exit-code", "--refs", "origin", ref, check=False)
    if result.returncode == 0:
        raise PromotionError(f"remote {ref} already exists")
    if result.returncode != 2 or result.stdout:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise PromotionError(f"could not prove remote {ref} absent: {diagnostic}")


def _fetch_exact_ref(
    root: pathlib.Path, remote_ref: str, local_ref: str, expected: str
) -> None:
    if _remote_ref(root, remote_ref) != expected:
        raise PromotionError(f"remote {remote_ref} changed from its exact binding")
    _git(root, "fetch", "--no-tags", "--force", "origin", f"+{remote_ref}:{local_ref}")
    fetched = _git(root, "rev-parse", "--verify", local_ref).stdout.decode().strip()
    if fetched != expected or _remote_ref(root, remote_ref) != expected:
        raise PromotionError(f"remote {remote_ref} changed while it was fetched")


def _changed_paths(root: pathlib.Path, first: str, second: str) -> set[str]:
    raw = _git(
        root,
        "diff",
        "--no-renames",
        "--name-only",
        "-z",
        first,
        second,
        "--",
    ).stdout
    try:
        return {item.decode("utf-8") for item in raw.split(b"\0") if item}
    except UnicodeError as error:
        raise PromotionError("audit changed paths are not UTF-8") from error


def _write_patch(
    root: pathlib.Path, staged_commit: str, output_path: pathlib.Path
) -> tuple[str, int]:
    if output_path.exists() or output_path.is_symlink():
        raise PromotionError("promotion patch output already exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with output_path.open("xb") as destination:
            created = True
            completed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-renames",
                    SOURCE_COMMIT,
                    staged_commit,
                    "--",
                ],
                check=False,
                stdout=destination,
                stderr=subprocess.PIPE,
                env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
            )
        if completed.returncode != 0:
            diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()
            raise PromotionError(f"git diff failed: {diagnostic}")
        size = output_path.stat().st_size
        if size == 0:
            raise PromotionError("staged binary patch is empty")
        return _sha256_file(output_path), size
    except Exception:
        if created:
            output_path.unlink(missing_ok=True)
        raise


def _result_tree(root: pathlib.Path, main_commit: str, patch: pathlib.Path) -> str:
    descriptor, index_name = tempfile.mkstemp(prefix="lean-eval-migration-index-")
    os.close(descriptor)
    index = pathlib.Path(index_name)
    index.unlink()
    try:
        environment = {"GIT_INDEX_FILE": str(index)}
        _git(root, "read-tree", main_commit, extra_env=environment)
        _git(
            root,
            "apply",
            "--cached",
            "--binary",
            str(patch),
            extra_env=environment,
        )
        tree = _git(root, "write-tree", extra_env=environment).stdout.decode().strip()
        return _object_id(tree, "result tree")
    finally:
        index.unlink(missing_ok=True)


def derive_binding(
    audit_root: pathlib.Path,
    plan: dict[str, Any],
    staged_commit: str,
    expected_staged_tree: str,
    expected_patch_sha256: str,
    current_main_commit: str,
    output_patch: pathlib.Path,
) -> dict[str, Any]:
    _validate_repository(audit_root)
    migration._require_canonical_selection(plan)
    staged_commit = _object_id(staged_commit, "staged commit")
    expected_staged_tree = _object_id(expected_staged_tree, "staged tree")
    expected_patch_sha256 = _digest(expected_patch_sha256, "patch digest")
    current_main_commit = _object_id(current_main_commit, "current audit main")

    review_local = "refs/lean-eval-promotion/review"
    main_local = "refs/lean-eval-promotion/main"
    _fetch_exact_ref(audit_root, REVIEW_REF, review_local, staged_commit)
    _fetch_exact_ref(audit_root, MAIN_REF, main_local, current_main_commit)
    if (
        _git(audit_root, "rev-parse", f"{staged_commit}^").stdout.decode().strip()
        != SOURCE_COMMIT
    ):
        raise PromotionError("staged migration is not one direct child of the pinned source")
    parents = _git(
        audit_root, "rev-list", "--parents", "-n", "1", staged_commit
    ).stdout.decode().split()
    if parents != [staged_commit, SOURCE_COMMIT]:
        raise PromotionError("staged migration must have exactly the pinned source as parent")
    staged_tree = _git(
        audit_root, "rev-parse", f"{staged_commit}^{{tree}}"
    ).stdout.decode().strip()
    if staged_tree != expected_staged_tree:
        raise PromotionError("staged migration tree changed from its exact binding")

    touched = migration._migration_touched_paths(plan)
    staged_changed = _changed_paths(audit_root, SOURCE_COMMIT, staged_commit)
    if staged_changed != touched:
        raise PromotionError(
            "staged migration does not change exactly the migration-touched paths"
        )

    ancestor = _git(
        audit_root,
        "merge-base",
        "--is-ancestor",
        SOURCE_COMMIT,
        current_main_commit,
        check=False,
    )
    if ancestor.returncode == 1:
        raise PromotionError("pinned source is not an ancestor of current audit main")
    if ancestor.returncode != 0:
        raise PromotionError("could not prove pinned-source ancestry")
    intervening = _changed_paths(audit_root, SOURCE_COMMIT, current_main_commit)
    overlap = intervening & touched
    if overlap:
        overlap_digest = _sha256_bytes(
            b"\0".join(path.encode() for path in sorted(overlap))
        )
        raise PromotionError(
            f"current audit main overlaps {len(overlap)} migration paths "
            f"(path-set digest {overlap_digest})"
        )
    patch_sha256, patch_size = _write_patch(
        audit_root, staged_commit, output_patch
    )
    if patch_sha256 != expected_patch_sha256:
        output_patch.unlink(missing_ok=True)
        raise PromotionError("staged binary patch changed from its exact binding")
    try:
        result_tree = _result_tree(audit_root, current_main_commit, output_patch)
    except Exception:
        output_patch.unlink(missing_ok=True)
        raise
    if _changed_paths(audit_root, current_main_commit, result_tree) != touched:
        output_patch.unlink(missing_ok=True)
        raise PromotionError(
            "rebased result does not change exactly the migration-touched paths"
        )
    if (
        _remote_ref(audit_root, REVIEW_REF) != staged_commit
        or _remote_ref(audit_root, MAIN_REF) != current_main_commit
    ):
        output_patch.unlink(missing_ok=True)
        raise PromotionError("a bound remote ref moved while promotion was planned")
    report = {
        "schema_version": 1,
        "kind": "historical_archive_envelope_promotion_binding",
        "source_commit": SOURCE_COMMIT,
        "inventory_digest": INVENTORY_DIGEST,
        "migration_count": MIGRATION_COUNT,
        "review_ref": REVIEW_REF,
        "staged_commit": staged_commit,
        "staged_tree": staged_tree,
        "patch_sha256": patch_sha256,
        "patch_size_bytes": patch_size,
        "audit_main_commit": current_main_commit,
        "intervening_changed_path_count": len(intervening),
        "migration_touched_path_count": len(touched),
        "overlap_count": 0,
        "result_tree": result_tree,
    }
    return report


def _load_binding(path: pathlib.Path, expected_sha256: str) -> dict[str, Any]:
    if _sha256_file(path) != _digest(expected_sha256, "binding digest"):
        raise PromotionError("promotion binding digest changed")
    binding = _read_json(path, "promotion binding")
    required = {
        "schema_version",
        "kind",
        "source_commit",
        "inventory_digest",
        "migration_count",
        "review_ref",
        "staged_commit",
        "staged_tree",
        "patch_sha256",
        "patch_size_bytes",
        "audit_main_commit",
        "intervening_changed_path_count",
        "migration_touched_path_count",
        "overlap_count",
        "result_tree",
    }
    if set(binding) != required:
        raise PromotionError("promotion binding fields are not canonical")
    if (
        binding["schema_version"] != 1
        or binding["kind"] != "historical_archive_envelope_promotion_binding"
        or binding["source_commit"] != SOURCE_COMMIT
        or binding["inventory_digest"] != INVENTORY_DIGEST
        or binding["migration_count"] != MIGRATION_COUNT
        or binding["review_ref"] != REVIEW_REF
        or binding["overlap_count"] != 0
    ):
        raise PromotionError("promotion binding is not the fixed migration contract")
    return binding


def prepare_candidate(
    audit_root: pathlib.Path,
    plan: dict[str, Any],
    binding_path: pathlib.Path,
    expected_binding_sha256: str,
    patch_path: pathlib.Path,
    output_worktree: pathlib.Path,
    commit_timestamp: str,
) -> dict[str, Any]:
    binding = _load_binding(binding_path, expected_binding_sha256)
    if TIMESTAMP.fullmatch(commit_timestamp) is None:
        raise PromotionError("commit timestamp must use canonical UTC seconds")
    try:
        dt.datetime.strptime(commit_timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise PromotionError("commit timestamp is not a real UTC time") from error
    if (
        patch_path.stat().st_size != binding["patch_size_bytes"]
        or _sha256_file(patch_path) != binding["patch_sha256"]
    ):
        raise PromotionError("promotion patch changed from its exact binding")
    descriptor, recomputed_name = tempfile.mkstemp(
        prefix="lean-eval-migration-patch-"
    )
    os.close(descriptor)
    recomputed_patch = pathlib.Path(recomputed_name)
    recomputed_patch.unlink()
    try:
        current = derive_binding(
            audit_root,
            plan,
            binding["staged_commit"],
            binding["staged_tree"],
            binding["patch_sha256"],
            binding["audit_main_commit"],
            recomputed_patch,
        )
        if current != binding or not _files_equal(recomputed_patch, patch_path):
            raise PromotionError("promotion inputs changed after binding")
    finally:
        recomputed_patch.unlink(missing_ok=True)
    if output_worktree.exists() or output_worktree.is_symlink():
        raise PromotionError("promotion output worktree already exists")
    if not output_worktree.parent.is_dir() or output_worktree.parent.is_symlink():
        raise PromotionError("promotion output parent must be one real directory")
    local_branch = _git(
        audit_root, "show-ref", "--verify", "--quiet", PROMOTION_REF, check=False
    )
    if local_branch.returncode == 0:
        raise PromotionError("local promotion branch already exists")
    if local_branch.returncode != 1:
        raise PromotionError("could not prove local promotion branch absent")
    _require_remote_ref_absent(audit_root, PROMOTION_REF)
    if (
        _remote_ref(audit_root, REVIEW_REF) != binding["staged_commit"]
        or _remote_ref(audit_root, MAIN_REF) != binding["audit_main_commit"]
    ):
        raise PromotionError("a bound remote ref moved before candidate preparation")
    created = False
    try:
        _git(
            audit_root,
            "worktree",
            "add",
            "-b",
            PROMOTION_BRANCH,
            str(output_worktree),
            binding["audit_main_commit"],
        )
        created = True
        _git(
            output_worktree,
            "apply",
            "--index",
            "--binary",
            str(patch_path),
        )
        tree = _git(output_worktree, "write-tree").stdout.decode().strip()
        if tree != binding["result_tree"]:
            raise PromotionError("prepared promotion tree differs from the bound result")
        environment = {
            "GIT_AUTHOR_DATE": commit_timestamp,
            "GIT_COMMITTER_DATE": commit_timestamp,
        }
        _git(
            output_worktree,
            "-c",
            "user.name=lean-eval-archiver",
            "-c",
            "user.email=lean-eval-archiver@users.noreply.github.com",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "-m",
            "migration: promote rewrapped historical archive file keys",
            extra_env=environment,
        )
        candidate = _git(output_worktree, "rev-parse", "HEAD").stdout.decode().strip()
        parent = _git(output_worktree, "rev-parse", "HEAD^").stdout.decode().strip()
        candidate_tree = _git(output_worktree, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
        if parent != binding["audit_main_commit"] or candidate_tree != binding["result_tree"]:
            raise PromotionError("promotion commit does not have the bound parent and tree")
        if _git(output_worktree, "status", "--porcelain").stdout:
            raise PromotionError("promotion worktree is not clean after commit")
        if (
            _remote_ref(audit_root, REVIEW_REF) != binding["staged_commit"]
            or _remote_ref(audit_root, MAIN_REF) != binding["audit_main_commit"]
        ):
            raise PromotionError("a bound remote ref moved during candidate preparation")
        return {
            "schema_version": 1,
            "kind": "historical_archive_envelope_promotion_candidate",
            "binding_sha256": expected_binding_sha256,
            "promotion_branch": PROMOTION_BRANCH,
            "promotion_commit": candidate,
            "promotion_parent": parent,
            "promotion_tree": candidate_tree,
            "commit_timestamp": commit_timestamp,
        }
    except Exception:
        if created:
            _git(audit_root, "worktree", "remove", "--force", str(output_worktree), check=False)
        _git(audit_root, "branch", "-D", PROMOTION_BRANCH, check=False)
        raise


def _load_candidate(path: pathlib.Path, expected_sha256: str) -> dict[str, Any]:
    if _sha256_file(path) != _digest(expected_sha256, "candidate digest"):
        raise PromotionError("promotion candidate digest changed")
    candidate = _read_json(path, "promotion candidate")
    required = {
        "schema_version",
        "kind",
        "binding_sha256",
        "promotion_branch",
        "promotion_commit",
        "promotion_parent",
        "promotion_tree",
        "commit_timestamp",
    }
    if set(candidate) != required:
        raise PromotionError("promotion candidate fields are not canonical")
    if (
        candidate["schema_version"] != 1
        or candidate["kind"]
        != "historical_archive_envelope_promotion_candidate"
        or candidate["promotion_branch"] != PROMOTION_BRANCH
        or not isinstance(candidate["binding_sha256"], str)
        or DIGEST.fullmatch(candidate["binding_sha256"]) is None
        or not isinstance(candidate["promotion_commit"], str)
        or COMMIT.fullmatch(candidate["promotion_commit"]) is None
        or not isinstance(candidate["promotion_parent"], str)
        or COMMIT.fullmatch(candidate["promotion_parent"]) is None
        or not isinstance(candidate["promotion_tree"], str)
        or COMMIT.fullmatch(candidate["promotion_tree"]) is None
        or not isinstance(candidate["commit_timestamp"], str)
        or TIMESTAMP.fullmatch(candidate["commit_timestamp"]) is None
    ):
        raise PromotionError("promotion candidate is not canonical")
    return candidate


def readback(
    audit_root: pathlib.Path,
    candidate_path: pathlib.Path,
    expected_candidate_sha256: str,
    expected_main_commit: str,
) -> dict[str, Any]:
    _validate_repository(audit_root)
    candidate = _load_candidate(candidate_path, expected_candidate_sha256)
    expected_main_commit = _object_id(expected_main_commit, "merged audit main")
    _fetch_exact_ref(
        audit_root,
        MAIN_REF,
        "refs/lean-eval-promotion/readback-main",
        expected_main_commit,
    )
    tree = _git(audit_root, "rev-parse", f"{expected_main_commit}^{{tree}}").stdout.decode().strip()
    if tree != candidate["promotion_tree"]:
        raise PromotionError("merged audit main tree differs from the promoted result")
    ancestor = _git(
        audit_root,
        "merge-base",
        "--is-ancestor",
        candidate["promotion_commit"],
        expected_main_commit,
        check=False,
    )
    if ancestor.returncode != 0:
        raise PromotionError("promotion candidate is not an ancestor of merged audit main")
    return {
        "schema_version": 1,
        "kind": "historical_archive_envelope_promotion_readback",
        "candidate_sha256": expected_candidate_sha256,
        "promotion_commit": candidate["promotion_commit"],
        "audit_main_commit": expected_main_commit,
        "audit_main_tree": tree,
        "matches_promoted_result_tree": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_command = commands.add_parser("plan")
    plan_command.add_argument("--audit-root", required=True, type=pathlib.Path)
    plan_command.add_argument("--migration-plan", required=True, type=pathlib.Path)
    plan_command.add_argument("--staged-commit", required=True)
    plan_command.add_argument("--expected-staged-tree", required=True)
    plan_command.add_argument("--expected-patch-sha256", required=True)
    plan_command.add_argument("--current-main-commit", required=True)
    plan_command.add_argument("--output-patch", required=True, type=pathlib.Path)
    plan_command.add_argument("--output-binding", required=True, type=pathlib.Path)

    prepare_command = commands.add_parser("prepare")
    prepare_command.add_argument("--audit-root", required=True, type=pathlib.Path)
    prepare_command.add_argument("--migration-plan", required=True, type=pathlib.Path)
    prepare_command.add_argument("--binding", required=True, type=pathlib.Path)
    prepare_command.add_argument("--expected-binding-sha256", required=True)
    prepare_command.add_argument("--patch", required=True, type=pathlib.Path)
    prepare_command.add_argument("--output-worktree", required=True, type=pathlib.Path)
    prepare_command.add_argument("--commit-timestamp", required=True)
    prepare_command.add_argument("--output", required=True, type=pathlib.Path)

    readback_command = commands.add_parser("readback")
    readback_command.add_argument("--audit-root", required=True, type=pathlib.Path)
    readback_command.add_argument("--candidate", required=True, type=pathlib.Path)
    readback_command.add_argument("--expected-candidate-sha256", required=True)
    readback_command.add_argument("--expected-main-commit", required=True)
    readback_command.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            output_patch = args.output_patch.resolve()
            output_binding = args.output_binding.resolve()
            if output_patch == output_binding:
                raise PromotionError("patch and binding outputs must be distinct")
            if output_binding.exists() or output_binding.is_symlink():
                raise PromotionError("promotion binding output already exists")
            report = derive_binding(
                args.audit_root.resolve(),
                migration._load_plan(args.migration_plan.resolve()),
                args.staged_commit,
                args.expected_staged_tree,
                args.expected_patch_sha256,
                args.current_main_commit,
                output_patch,
            )
            try:
                _write_json(output_binding, report)
            except Exception:
                output_patch.unlink(missing_ok=True)
                raise
        elif args.command == "prepare":
            output = args.output.resolve()
            if output.exists() or output.is_symlink():
                raise PromotionError("promotion candidate output already exists")
            report = prepare_candidate(
                args.audit_root.resolve(),
                migration._load_plan(args.migration_plan.resolve()),
                args.binding.resolve(),
                args.expected_binding_sha256,
                args.patch.resolve(),
                args.output_worktree.resolve(),
                args.commit_timestamp,
            )
            _write_json(output, report)
        else:
            output = args.output.resolve()
            if output.exists() or output.is_symlink():
                raise PromotionError("promotion readback output already exists")
            _write_json(
                output,
                readback(
                    args.audit_root.resolve(),
                    args.candidate.resolve(),
                    args.expected_candidate_sha256,
                    args.expected_main_commit,
                ),
            )
    except (OSError, PromotionError, migration.MigrationError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
