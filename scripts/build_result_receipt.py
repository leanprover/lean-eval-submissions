#!/usr/bin/env python3
"""Build a source-free receipt for one accepted server result at an exact commit."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys

from results_schema import (
    SCHEMA_VERSION,
    ResultsSchemaError,
    canonical_file_bytes,
    read_results_file,
    result_id,
)


COMMIT = re.compile(r"^[0-9a-f]{40}$")
LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
PROBLEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
UUID7 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
BRANCHES = {"main", "staging-results"}
RESULT_REPOSITORY = "leanprover/lean-eval-submissions"
TREE_DOMAIN = b"lean-eval-result-tree-v1\0"


class ReceiptError(ValueError):
    """The exact result cannot produce a trusted lifecycle receipt."""


def _canonical_timestamp(value: str) -> str:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReceiptError("occurred-at is not a real timestamp") from error
    if parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") != value:
        raise ReceiptError("occurred-at must be canonical UTC milliseconds")
    return value


def result_tree_digest(path: str, contents: bytes) -> str:
    """Digest the one exact Results file using the cross-runtime v1 contract."""
    entry = [{
        "path": path,
        "sha256": hashlib.sha256(contents).hexdigest(),
        "size": len(contents),
    }]
    canonical = json.dumps(
        entry,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(TREE_DOMAIN + canonical).hexdigest()


def build_receipt(
    *,
    results_file: pathlib.Path,
    results_root: pathlib.Path,
    submission_id: str,
    user: str,
    declared_model: str,
    problem_id: str,
    statement_revision: int,
    result_branch: str,
    result_commit: str,
    occurred_at: str,
) -> dict[str, object]:
    if not UUID7.fullmatch(submission_id):
        raise ReceiptError("submission-id must be a canonical lowercase UUIDv7")
    if not LOGIN.fullmatch(user):
        raise ReceiptError("user must be a canonical GitHub login")
    if not declared_model:
        raise ReceiptError("declared-model must not be empty")
    if not PROBLEM.fullmatch(problem_id):
        raise ReceiptError("problem-id is invalid")
    if statement_revision < 1:
        raise ReceiptError("statement-revision must be positive")
    if result_branch not in BRANCHES:
        raise ReceiptError("result-branch is not registered")
    if not COMMIT.fullmatch(result_commit):
        raise ReceiptError("result-commit must be a lowercase commit SHA")
    _canonical_timestamp(occurred_at)
    try:
        relative = results_file.resolve(strict=True).relative_to(
            results_root.resolve(strict=True)
        )
    except (OSError, ValueError) as error:
        raise ReceiptError("results-file must be inside results-root") from error
    result_path = relative.as_posix()
    expected_path = f"results/{user.lower()}.json"
    if result_path != expected_path:
        raise ReceiptError(f"result path must be {expected_path!r}")
    try:
        document, source_version = read_results_file(results_file)
    except ResultsSchemaError as error:
        raise ReceiptError(str(error)) from error
    if source_version != SCHEMA_VERSION:
        raise ReceiptError("new result receipts require a schema-version-2 file")
    contents = results_file.read_bytes()
    if canonical_file_bytes(document) != contents:
        raise ReceiptError("result file is not in canonical schema-version-2 form")
    if document["user"].lower() != user.lower():
        raise ReceiptError("result file belongs to a different user")
    try:
        identifier = result_id(user, declared_model, problem_id, statement_revision)
    except ResultsSchemaError as error:
        raise ReceiptError(str(error)) from error
    matches = [record for record in document["results"] if record["result_id"] == identifier]
    if len(matches) != 1:
        raise ReceiptError("result file does not contain exactly one expected result")
    record = matches[0]
    if (
        record["problem_id"] != problem_id
        or record["statement_revision"] != statement_revision
        or record["declared_model"] != declared_model
    ):
        raise ReceiptError("record identity disagrees with the accepted result")
    return {
        "schema_version": 1,
        "submission_id": submission_id,
        "occurred_at": occurred_at,
        "result_id": identifier,
        "problem_id": problem_id,
        "statement_revision": statement_revision,
        "result_repository": RESULT_REPOSITORY,
        "result_branch": result_branch,
        "result_commit": result_commit,
        "result_path": result_path,
        "result_tree_digest": result_tree_digest(result_path, contents),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-file", required=True, type=pathlib.Path)
    parser.add_argument("--results-root", required=True, type=pathlib.Path)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--declared-model", required=True)
    parser.add_argument("--problem-id", required=True)
    parser.add_argument("--statement-revision", required=True, type=int)
    parser.add_argument("--result-branch", required=True)
    parser.add_argument("--result-commit", required=True)
    parser.add_argument("--occurred-at", required=True)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        receipt = build_receipt(
            results_file=args.results_file,
            results_root=args.results_root,
            submission_id=args.submission_id,
            user=args.user,
            declared_model=args.declared_model,
            problem_id=args.problem_id,
            statement_revision=args.statement_revision,
            result_branch=args.result_branch,
            result_commit=args.result_commit,
            occurred_at=args.occurred_at,
        )
    except ReceiptError as error:
        print(str(error), file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
