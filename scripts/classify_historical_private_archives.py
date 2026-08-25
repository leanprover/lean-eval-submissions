#!/usr/bin/env python3
"""Build a source-free private-result to historical-archive crosswalk.

The private source locator is used only as an in-memory equality key.  It is
never copied to the output.  Every input is independently revalidated and
bound to an operator-reviewed digest before a classification is emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from collections import Counter, defaultdict
from typing import Any

import migrate_archive_envelopes as archive_migration
from inventory_historical_replay import inventory as replay_inventory
from results_schema import (
    LOGIN_RE,
    OWNER_NAME_RE,
    ResultsSchemaError,
    read_results_file,
)

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_PRIVATE_RESULTS = 10_000
MAX_ARCHIVES = 10_000
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
ENTRY_DIGEST_DOMAIN = b"lean-eval-private-archive-crosswalk-entry-v1\0"
ALLOWED_SIDECAR_FIELDS = archive_migration.PRESERVED_FIELDS | {
    "schema_version",
    "issue",
    "submission_id",
    "sha256_ciphertext",
    "size_bytes_ciphertext",
    "key_envelope",
}
CLASSIFICATIONS = (
    "bound",
    "archive_not_found",
    "archive_identity_ambiguous",
    "archive_metadata_conflict",
)


class CrosswalkError(ValueError):
    """The closed inputs cannot produce a trustworthy source-free crosswalk."""


def _canonical_compact(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise CrosswalkError("input is not canonically encodable") from error


def canonical_output_bytes(value: Any) -> bytes:
    try:
        encoded = (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise CrosswalkError("crosswalk is not canonically encodable") from error
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise CrosswalkError("crosswalk exceeds the output size limit")
    return encoded


def _require_digest(value: str, label: str) -> None:
    if DIGEST.fullmatch(value) is None:
        raise CrosswalkError(f"{label} must be one lowercase SHA-256 digest")


def _require_git_checkout(
    selected_root: pathlib.Path,
    expected_commit: str,
    label: str,
    *,
    require_repository_root: bool,
) -> None:
    def run(*arguments: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(selected_root), *arguments],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=30,
                env={"PATH": os.environ.get("PATH", "")},
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CrosswalkError(f"cannot verify the {label} Git checkout") from error
        if completed.returncode != 0:
            raise CrosswalkError(f"cannot verify the {label} Git checkout")
        return completed.stdout.strip()

    checkout_root_text = run("rev-parse", "--show-toplevel")
    checkout_root = pathlib.Path(checkout_root_text).resolve()
    if require_repository_root and checkout_root != selected_root.resolve():
        raise CrosswalkError(f"{label} root is not the exact checkout root")
    try:
        relative = selected_root.resolve().relative_to(checkout_root).as_posix()
    except ValueError as error:
        raise CrosswalkError(f"{label} root is outside its Git checkout") from error
    if run("rev-parse", "HEAD") != expected_commit:
        raise CrosswalkError(f"{label} checkout is not at the selected commit")
    status_arguments = ["status", "--porcelain=v1", "--untracked-files=all"]
    if relative != ".":
        status_arguments.extend(["--", relative])
    if run(*status_arguments):
        raise CrosswalkError(f"{label} checkout input tree is not clean")


def _load_private_results(
    results_root: pathlib.Path,
    source_commit: str,
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """Validate twice around the full-record read to reject a changing store."""
    try:
        before = replay_inventory(results_root, source_commit)
    except (OSError, UnicodeError, ResultsSchemaError, ValueError) as error:
        raise CrosswalkError(f"results inventory is invalid: {error}") from error

    private: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(results_root.iterdir(), key=lambda item: item.name):
        if path.name == ".gitkeep":
            continue
        try:
            data, version = read_results_file(path)
        except (OSError, UnicodeError, ResultsSchemaError) as error:
            raise CrosswalkError(
                f"results store changed during validation: {error}"
            ) from error
        if version != 2:
            raise CrosswalkError("private crosswalk requires schema-version-2 results")
        for record in data["results"]:
            if record["submission"]["public"] is False:
                private.append((data["user"], record))

    try:
        after = replay_inventory(results_root, source_commit)
    except (OSError, UnicodeError, ResultsSchemaError, ValueError) as error:
        raise CrosswalkError(
            f"results inventory changed during validation: {error}"
        ) from error
    if before != after:
        raise CrosswalkError("results store changed during validation")
    expected_private_ids = {
        entry["result_id"]
        for entry in before["entries"]
        if entry["source"]["visibility"] == "private"
    }
    actual_private_ids = {record["result_id"] for _, record in private}
    if (
        len(actual_private_ids) != len(private)
        or actual_private_ids != expected_private_ids
    ):
        raise CrosswalkError(
            "private result records disagree with the replay inventory"
        )
    if not private or len(private) > MAX_PRIVATE_RESULTS:
        raise CrosswalkError("private result count is outside the closed limit")
    private.sort(key=lambda item: item[1]["result_id"])
    return before, private


def _read_json_object(path: pathlib.Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CrosswalkError(f"{label} must be one regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CrosswalkError(f"{label} must be one UTF-8 JSON object") from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CrosswalkError(f"{label} must be one object with string keys")
    return value


def _validate_sidecar_metadata(
    sidecar: dict[str, Any],
    plan_entry: dict[str, Any],
) -> dict[str, Any]:
    unknown = set(sidecar) - ALLOWED_SIDECAR_FIELDS
    if unknown:
        raise CrosswalkError(f"archive sidecar has unknown fields: {sorted(unknown)}")
    schema = sidecar.get("schema_version")
    if type(schema) is not int or schema not in {1, 2, 3}:
        raise CrosswalkError("archive sidecar schema_version is unsupported")
    submitter = sidecar.get("submitter")
    repository = sidecar.get("submission_repo")
    source_ref = sidecar.get("submission_ref")
    model = sidecar.get("model")
    if (
        not isinstance(submitter, str)
        or len(submitter) > 100
        or LOGIN_RE.fullmatch(submitter) is None
    ):
        raise CrosswalkError("archive sidecar submitter is invalid")
    if (
        not isinstance(repository, str)
        or len(repository) > 201
        or OWNER_NAME_RE.fullmatch(repository) is None
    ):
        raise CrosswalkError("archive sidecar repository is invalid")
    if not isinstance(source_ref, str) or COMMIT.fullmatch(source_ref) is None:
        raise CrosswalkError("archive sidecar source ref is invalid")
    if sidecar.get("submission_kind") not in {"github_repo", "gist"}:
        raise CrosswalkError("archive sidecar source kind is invalid")
    if not isinstance(sidecar.get("submission_public"), bool):
        raise CrosswalkError("archive sidecar source visibility is invalid")
    if not isinstance(model, str) or not model or len(model) > 500:
        raise CrosswalkError("archive sidecar model is invalid")
    benchmark = sidecar.get("benchmark_commit")
    if not isinstance(benchmark, str) or COMMIT.fullmatch(benchmark) is None:
        raise CrosswalkError("archive sidecar benchmark commit is invalid")

    issue = sidecar.get("issue")
    submission_id = sidecar.get("submission_id")
    if schema == 1:
        if type(issue) is not int or issue <= 0 or "submission_id" in sidecar:
            raise CrosswalkError("legacy archive issue identity is invalid")
    else:
        if "issue" in sidecar or not isinstance(submission_id, str):
            raise CrosswalkError("server archive submission identity is invalid")
        if archive_migration.UUID7.fullmatch(submission_id) is None:
            raise CrosswalkError("server archive submission identity is invalid")

    problems = sidecar.get("problem_ids")
    verdict = sidecar.get("evaluator_verdict")
    if (problems is None) != (verdict is None):
        raise CrosswalkError(
            "archive problem and verdict evidence must appear together"
        )
    if problems is not None:
        if (
            not isinstance(problems, list)
            or not problems
            or len(problems) > 1000
            or any(
                not isinstance(problem, str) or not problem or len(problem) > 256
                for problem in problems
            )
            or len(set(problems)) != len(problems)
        ):
            raise CrosswalkError("archive problem inventory is invalid")
        if (
            not isinstance(verdict, dict)
            or not all(
                isinstance(problem, str) and status in {"pass", "fail"}
                for problem, status in verdict.items()
            )
            or set(verdict) != set(problems)
        ):
            raise CrosswalkError("archive evaluator verdict is invalid")

    expected_submission_id = plan_entry["submission_id"]
    if schema > 1 and submission_id != expected_submission_id:
        raise CrosswalkError(
            "archive sidecar submission ID disagrees with migration plan"
        )
    return {
        "schema_version": schema,
        "submission_id": expected_submission_id,
        "submitter": submitter,
        "issue": issue if schema == 1 else None,
        "submission_kind": sidecar["submission_kind"],
        "submission_repo": repository,
        "submission_ref": source_ref,
        "submission_public": sidecar["submission_public"],
        "model": model,
        "benchmark_commit": benchmark,
        "problem_ids": problems,
        "evaluator_verdict": verdict,
        "archive_plan_entry_sha256": hashlib.sha256(
            ENTRY_DIGEST_DOMAIN + _canonical_compact(plan_entry)
        ).hexdigest(),
    }


def _load_archives(
    audit_root: pathlib.Path,
    audit_commit: str,
    plan_path: pathlib.Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if audit_root.is_symlink() or not audit_root.is_dir():
        raise CrosswalkError("audit root must be one real directory")
    try:
        supplied_plan = archive_migration._load_plan(plan_path)
        before = archive_migration.build_plan(audit_root, audit_commit)
    except (OSError, UnicodeError, archive_migration.MigrationError) as error:
        raise CrosswalkError(f"archive migration plan is invalid: {error}") from error
    if supplied_plan != before:
        raise CrosswalkError("supplied archive plan is stale or was not rederived")
    if before["source_commit"] != audit_commit:
        raise CrosswalkError("archive plan source commit is stale")

    entries_by_path: dict[str, dict[str, Any]] = {}
    for entry in before["entries"]:
        entries_by_path[entry["source_path"]] = entry
    for retained in before["retained"]:
        entries_by_path[retained["source_path"]] = retained
    if not entries_by_path or len(entries_by_path) > MAX_ARCHIVES:
        raise CrosswalkError("archive count is outside the closed limit")

    expected_sidecars = {
        source_path.removesuffix(".tar.age") + ".json"
        for source_path in entries_by_path
    }
    actual_sidecars = {
        path.relative_to(audit_root).as_posix()
        for path in audit_root.rglob("*.json")
        if ".git" not in path.parts
    }
    if actual_sidecars != expected_sidecars:
        raise CrosswalkError("audit JSON inventory has missing or unrelated files")

    archives: list[dict[str, Any]] = []
    for source_path in sorted(entries_by_path):
        plan_entry = entries_by_path[source_path]
        sidecar_path = audit_root.joinpath(
            *source_path.removesuffix(".tar.age").split("/")
        ).with_suffix(".json")
        sidecar = _read_json_object(sidecar_path, "archive sidecar")
        expected_sidecar_digest = plan_entry.get(
            "source_sidecar_sha256", plan_entry.get("sidecar_sha256")
        )
        if (
            hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
            != expected_sidecar_digest
        ):
            raise CrosswalkError("archive sidecar changed after the migration plan")
        archives.append(_validate_sidecar_metadata(sidecar, plan_entry))

    try:
        after = archive_migration.build_plan(audit_root, audit_commit)
    except (OSError, UnicodeError, archive_migration.MigrationError) as error:
        raise CrosswalkError(
            f"archive inventory changed during validation: {error}"
        ) from error
    if before != after:
        raise CrosswalkError("archive inventory changed during validation")
    return before, archives


def _result_key(user: str, record: dict[str, Any]) -> tuple[Any, ...]:
    submission = record["submission"]
    intake = record["intake"]
    intake_identity: tuple[str, Any]
    if intake["kind"] == "issue":
        intake_identity = ("issue", intake["issue_number"])
    else:
        intake_identity = ("server", intake["submission_id"])
    return (
        user.casefold(),
        *intake_identity,
        submission["kind"],
        submission["repo"].casefold(),
        submission["ref"],
    )


def _archive_key(archive: dict[str, Any]) -> tuple[Any, ...]:
    intake_identity = (
        ("issue", archive["issue"])
        if archive["schema_version"] == 1
        else ("server", archive["submission_id"])
    )
    return (
        archive["submitter"].casefold(),
        *intake_identity,
        archive["submission_kind"],
        archive["submission_repo"].casefold(),
        archive["submission_ref"],
    )


def _classify_result(
    user: str,
    record: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    result_id = record["result_id"]
    if not candidates:
        return {"result_id": result_id, "classification": "archive_not_found"}
    if len(candidates) > 1:
        return {
            "result_id": result_id,
            "classification": "archive_identity_ambiguous",
            "candidate_count": len(candidates),
        }
    archive = candidates[0]
    reason: str | None = None
    if archive["submission_public"] is not False:
        reason = "archive_visibility_not_private"
    elif archive["model"] != record["declared_model"]:
        reason = "declared_model_mismatch"
    elif (
        archive["problem_ids"] is not None
        and record["problem_id"] not in archive["problem_ids"]
    ):
        reason = "problem_not_archived"
    elif (
        archive["evaluator_verdict"] is not None
        and archive["evaluator_verdict"].get(record["problem_id"]) != "pass"
    ):
        reason = "evaluator_verdict_not_pass"
    if reason is not None:
        return {
            "result_id": result_id,
            "classification": "archive_metadata_conflict",
            "reason": reason,
        }
    return {
        "result_id": result_id,
        "classification": "bound",
        "submission_id": archive["submission_id"],
        "archive_plan_entry_sha256": archive["archive_plan_entry_sha256"],
        "archive_schema_version": archive["schema_version"],
        "archive_result_evidence": (
            "confirmed_pass"
            if archive["evaluator_verdict"] is not None
            else "legacy_unrecorded"
        ),
        "benchmark_relation": (
            "same"
            if archive["benchmark_commit"] == record["benchmark_commit"]
            else "archive_recorded_different"
        ),
    }


def build_crosswalk(
    *,
    results_root: pathlib.Path,
    results_commit: str,
    expected_results_store_sha256: str,
    expected_private_result_count: int,
    audit_root: pathlib.Path,
    audit_commit: str,
    archive_plan: pathlib.Path,
    expected_archive_inventory_digest: str,
    verify_git_checkouts: bool = True,
) -> dict[str, Any]:
    if (
        COMMIT.fullmatch(results_commit) is None
        or COMMIT.fullmatch(audit_commit) is None
    ):
        raise CrosswalkError("source commits must be full lowercase Git SHAs")
    _require_digest(expected_results_store_sha256, "expected results-store digest")
    _require_digest(
        expected_archive_inventory_digest, "expected archive inventory digest"
    )
    if (
        type(expected_private_result_count) is not int
        or not 0 < expected_private_result_count <= MAX_PRIVATE_RESULTS
    ):
        raise CrosswalkError(
            "expected private result count is outside the closed limit"
        )
    if type(verify_git_checkouts) is not bool:
        raise CrosswalkError("Git checkout verification mode must be boolean")
    if verify_git_checkouts:
        _require_git_checkout(
            results_root,
            results_commit,
            "results",
            require_repository_root=False,
        )
        _require_git_checkout(
            audit_root,
            audit_commit,
            "audit",
            require_repository_root=True,
        )

    inventory, private_results = _load_private_results(results_root, results_commit)
    if inventory["results_store_sha256"] != expected_results_store_sha256:
        raise CrosswalkError("results-store digest disagrees with the reviewed input")
    if len(private_results) != expected_private_result_count:
        raise CrosswalkError("private result count disagrees with the reviewed input")

    plan, archives = _load_archives(audit_root, audit_commit, archive_plan)
    if plan["inventory_digest"] != expected_archive_inventory_digest:
        raise CrosswalkError(
            "archive inventory digest disagrees with the reviewed input"
        )

    by_identity: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for archive in archives:
        by_identity[_archive_key(archive)].append(archive)
    entries = [
        _classify_result(user, record, by_identity[_result_key(user, record)])
        for user, record in private_results
    ]
    counts = Counter(entry["classification"] for entry in entries)
    classification_counts = {
        classification: counts[classification] for classification in CLASSIFICATIONS
    }
    if sum(classification_counts.values()) != len(private_results):
        raise CrosswalkError("crosswalk classification coverage is incomplete")
    return {
        "schema_version": 1,
        "results_repository": "leanprover/lean-eval-submissions",
        "results_commit": results_commit,
        "results_store_sha256": expected_results_store_sha256,
        "private_result_count": len(private_results),
        "audit_repository": "leanprover/lean-eval-audit",
        "audit_commit": audit_commit,
        "archive_inventory_digest": expected_archive_inventory_digest,
        "archive_count": len(archives),
        "classification_counts": classification_counts,
        "entries": entries,
    }


def write_exclusive(path: pathlib.Path, value: Any) -> None:
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise CrosswalkError("output parent must be one existing real directory")
    with path.open("xb") as stream:
        stream.write(canonical_output_bytes(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", required=True, type=pathlib.Path)
    parser.add_argument("--results-commit", required=True)
    parser.add_argument("--expected-results-store-sha256", required=True)
    parser.add_argument("--expected-private-result-count", required=True, type=int)
    parser.add_argument("--audit-root", required=True, type=pathlib.Path)
    parser.add_argument("--audit-commit", required=True)
    parser.add_argument("--archive-plan", required=True, type=pathlib.Path)
    parser.add_argument("--expected-archive-inventory-digest", required=True)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        crosswalk = build_crosswalk(
            results_root=args.results_root.resolve(),
            results_commit=args.results_commit,
            expected_results_store_sha256=args.expected_results_store_sha256,
            expected_private_result_count=args.expected_private_result_count,
            audit_root=args.audit_root.resolve(),
            audit_commit=args.audit_commit,
            archive_plan=args.archive_plan.resolve(),
            expected_archive_inventory_digest=args.expected_archive_inventory_digest,
        )
        write_exclusive(args.output, crosswalk)
    except (CrosswalkError, OSError, UnicodeError, ValueError) as error:
        print(f"historical-private-archive-crosswalk: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
