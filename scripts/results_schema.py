#!/usr/bin/env python3
"""Results-store schema-version-1/2 validation and deterministic conversion.

Results schema version 1 remains readable during the migration window. All
newly written files use the flat schema-version-2 representation described in
``docs/results-schema-v2.md``.
This module deliberately has no third-party dependencies so the recording and
migration workflows can use the same implementation.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import re
from collections.abc import Iterable
from typing import Any

SCHEMA_VERSION = 2
RESULT_ID_PREFIX = "r2_"
RESULT_ID_DOMAIN = b"lean-eval-result-v2\0"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RESULT_ID_RE = re.compile(r"^r2_[0-9a-f]{64}$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
OWNER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9._-]+$")
UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
UUIDV7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
V1_TOP_LEVEL_FIELDS = frozenset({"schema_version", "user", "solved"})
V1_RECORD_FIELDS = frozenset(
    {
        "solved_at",
        "benchmark_commit",
        "submission_kind",
        "submission_repo",
        "submission_ref",
        "submission_public",
        "issue_number",
        "production_description",
        "solution_publication_status",
        "solution_publication_date",
    }
)


class ResultsSchemaError(ValueError):
    """A results file cannot be interpreted without losing information."""


def _canonical_json(value: Any) -> bytes:
    """Return RFC 8785 JSON for the identifier contract's restricted input.

    Result identifiers canonicalize an array containing strings and one
    positive integer.  For that subset, compact JSON with UTF-8 characters
    unescaped is exactly RFC 8785: there are no object-key ordering or
    floating-point serialization cases.  Encoding also rejects unpaired
    Unicode surrogates, as RFC 8785 requires.
    """

    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ResultsSchemaError(
            f"value is not RFC 8785 canonicalizable: {exc}"
        ) from exc


def result_id(
    user: str,
    declared_model: str,
    problem_id: str,
    statement_revision: int,
) -> str:
    """Compute the public, stable schema-version-2 identifier for one result."""

    if not isinstance(user, str) or not user:
        raise ResultsSchemaError("user must be a non-empty string")
    if not isinstance(declared_model, str) or not declared_model:
        raise ResultsSchemaError("declared_model must be a non-empty string")
    if not isinstance(problem_id, str) or not problem_id:
        raise ResultsSchemaError("problem_id must be a non-empty string")
    if (
        not isinstance(statement_revision, int)
        or isinstance(statement_revision, bool)
        or statement_revision <= 0
    ):
        raise ResultsSchemaError("statement_revision must be a positive integer")
    identity = [user.lower(), declared_model, problem_id, statement_revision]
    digest = hashlib.sha256(RESULT_ID_DOMAIN + _canonical_json(identity)).hexdigest()
    return RESULT_ID_PREFIX + digest


def _require_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ResultsSchemaError(f"{context} must be a JSON object")
    return value


def _require_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResultsSchemaError(f"{context} must be a non-empty string")
    return value


def _require_positive_int(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ResultsSchemaError(f"{context} must be a positive integer")
    return value


def _convert_v1_record(
    *,
    user: str,
    declared_model: str,
    problem_id: str,
    record: Any,
) -> dict[str, Any]:
    label = f"schema-version-1 record {declared_model!r}/{problem_id!r}"
    old = _require_object(record, label)
    unknown = set(old) - V1_RECORD_FIELDS
    if unknown:
        raise ResultsSchemaError(
            f"{label} has unknown fields: "
            + ", ".join(sorted(unknown))
        )
    required = V1_RECORD_FIELDS - {
        "production_description",
        "solution_publication_status",
        "solution_publication_date",
    }
    missing = required - set(old)
    if missing:
        raise ResultsSchemaError(
            f"{label} is missing fields: "
            + ", ".join(sorted(missing))
        )
    statement_revision = 1
    production_metadata = {
        key: old[key]
        for key in (
            "production_description",
            "solution_publication_status",
            "solution_publication_date",
        )
        if key in old
    }
    return {
        "result_id": result_id(user, declared_model, problem_id, statement_revision),
        "problem_id": problem_id,
        "statement_revision": statement_revision,
        "declared_model": declared_model,
        "accepted_at": old["solved_at"],
        "benchmark_commit": old["benchmark_commit"],
        "intake": {"kind": "issue", "issue_number": old["issue_number"]},
        "submission": {
            "kind": old["submission_kind"],
            "repo": old["submission_repo"],
            "ref": old["submission_ref"],
            "public": old["submission_public"],
        },
        "production_metadata": production_metadata,
    }


def convert_v1(data: Any, *, context: str = "results file") -> dict[str, Any]:
    """Convert one validated schema-version-1 file to version 2 losslessly."""

    old = _require_object(data, context)
    unknown = set(old) - V1_TOP_LEVEL_FIELDS
    if unknown:
        raise ResultsSchemaError(
            f"{context} schema version 1 has unknown top-level fields: "
            + ", ".join(sorted(unknown))
        )
    if old.get("schema_version") != 1:
        raise ResultsSchemaError(f"{context} is not schema version 1")
    user = _require_string(old.get("user"), f"{context}.user")
    solved = _require_object(old.get("solved"), f"{context}.solved")
    records: list[dict[str, Any]] = []
    for declared_model, problems_value in solved.items():
        _require_string(declared_model, f"{context}.solved model key")
        problems = _require_object(
            problems_value, f"{context}.solved[{declared_model!r}]"
        )
        for problem_id, record in problems.items():
            _require_string(problem_id, f"{context} problem id")
            records.append(
                _convert_v1_record(
                    user=user,
                    declared_model=declared_model,
                    problem_id=problem_id,
                    record=record,
                )
            )
    records.sort(key=lambda record: record["result_id"])
    converted = {"schema_version": SCHEMA_VERSION, "user": user, "results": records}
    validate_v2(converted, context=context)
    return converted


def validate_v2(data: Any, *, context: str = "results file") -> dict[str, Any]:
    """Validate and return a schema-version-2 file.

    The validator is intentionally strict about the base envelope and stable
    identifier while allowing the structured production metadata object to
    grow compatibly.
    """

    doc = _require_object(data, context)
    if set(doc) != {"schema_version", "user", "results"}:
        raise ResultsSchemaError(
            f"{context} schema version 2 must contain only schema_version, user, results"
        )
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ResultsSchemaError(
            f"{context} has schema_version {doc.get('schema_version')!r}; "
            f"supported versions are 1 and {SCHEMA_VERSION}"
        )
    user = _require_string(doc.get("user"), f"{context}.user")
    if not LOGIN_RE.fullmatch(user):
        raise ResultsSchemaError(f"{context}.user is not a valid GitHub login")
    records = doc.get("results")
    if not isinstance(records, list):
        raise ResultsSchemaError(f"{context}.results must be an array")
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str, int]] = set()
    required_fields = {
        "result_id",
        "problem_id",
        "statement_revision",
        "declared_model",
        "accepted_at",
        "benchmark_commit",
        "intake",
        "submission",
        "production_metadata",
    }
    for index, value in enumerate(records):
        record = _require_object(value, f"{context}.results[{index}]")
        if set(record) != required_fields:
            missing = required_fields - set(record)
            extra = set(record) - required_fields
            raise ResultsSchemaError(
                f"{context}.results[{index}] fields differ; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        problem_id = _require_string(record["problem_id"], f"record {index}.problem_id")
        declared_model = _require_string(
            record["declared_model"], f"record {index}.declared_model"
        )
        revision = _require_positive_int(
            record["statement_revision"], f"record {index}.statement_revision"
        )
        identifier = _require_string(record["result_id"], f"record {index}.result_id")
        if not RESULT_ID_RE.fullmatch(identifier):
            raise ResultsSchemaError(f"record {index}.result_id has invalid syntax")
        expected = result_id(user, declared_model, problem_id, revision)
        if identifier != expected:
            raise ResultsSchemaError(
                f"record {index}.result_id does not match its identity fields"
            )
        if identifier in seen_ids:
            raise ResultsSchemaError(f"duplicate result_id {identifier}")
        seen_ids.add(identifier)
        sticky_key = (declared_model, problem_id, revision)
        if sticky_key in seen_keys:
            raise ResultsSchemaError(f"duplicate sticky result key {sticky_key!r}")
        seen_keys.add(sticky_key)
        accepted_at = _require_string(
            record["accepted_at"], f"record {index}.accepted_at"
        )
        if not UTC_TIMESTAMP_RE.fullmatch(accepted_at):
            raise ResultsSchemaError(
                f"record {index}.accepted_at must be second-precision UTC"
            )
        try:
            datetime.datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ResultsSchemaError(
                f"record {index}.accepted_at is not a valid timestamp"
            ) from exc
        benchmark_commit = _require_string(
            record["benchmark_commit"], f"record {index}.benchmark_commit"
        )
        if not SHA_RE.fullmatch(benchmark_commit):
            raise ResultsSchemaError(f"record {index}.benchmark_commit must be a SHA")
        intake = _require_object(record["intake"], f"record {index}.intake")
        if intake.get("kind") == "issue":
            if set(intake) != {"kind", "issue_number"}:
                raise ResultsSchemaError(
                    f"record {index} issue intake fields are invalid"
                )
            _require_positive_int(
                intake["issue_number"], f"record {index}.issue_number"
            )
        elif intake.get("kind") == "server":
            if set(intake) != {"kind", "submission_id"}:
                raise ResultsSchemaError(
                    f"record {index} server intake fields are invalid"
                )
            submission_id = _require_string(
                intake["submission_id"], f"record {index}.submission_id"
            )
            if not UUIDV7_RE.fullmatch(submission_id):
                raise ResultsSchemaError(
                    f"record {index}.submission_id must be a canonical lowercase UUIDv7"
                )
        else:
            raise ResultsSchemaError(f"record {index}.intake.kind is unsupported")
        submission = _require_object(
            record["submission"], f"record {index}.submission"
        )
        if set(submission) != {"kind", "repo", "ref", "public"}:
            raise ResultsSchemaError(f"record {index}.submission fields are invalid")
        if submission["kind"] not in {"github_repo", "gist"}:
            raise ResultsSchemaError(f"record {index}.submission.kind is unsupported")
        submission_repo = _require_string(
            submission["repo"], f"record {index}.submission.repo"
        )
        if not OWNER_NAME_RE.fullmatch(submission_repo):
            raise ResultsSchemaError(
                f"record {index}.submission.repo must look like owner/name"
            )
        submission_ref = _require_string(
            submission["ref"], f"record {index}.submission.ref"
        )
        if not SHA_RE.fullmatch(submission_ref):
            raise ResultsSchemaError(f"record {index}.submission.ref must be a SHA")
        if not isinstance(submission["public"], bool):
            raise ResultsSchemaError(
                f"record {index}.submission.public must be boolean"
            )
        production_metadata = _require_object(
            record["production_metadata"], f"record {index}.production_metadata"
        )
        description = production_metadata.get("production_description")
        if description is not None and (
            not isinstance(description, str)
            or not description.strip()
            or "\x00" in description
            or len(description) > 4000
        ):
            raise ResultsSchemaError(
                f"record {index}.production_description is invalid"
            )
        publication_status = production_metadata.get("solution_publication_status")
        publication_date = production_metadata.get("solution_publication_date")
        if publication_status is not None:
            if publication_status not in {"private", "planned", "published"}:
                raise ResultsSchemaError(
                    f"record {index}.solution_publication_status is invalid"
                )
            if publication_status == "published" and not submission["public"]:
                raise ResultsSchemaError(
                    f"record {index} published solution must have public source"
                )
            if publication_status in {"private", "planned"} and submission["public"]:
                raise ResultsSchemaError(
                    f"record {index} {publication_status} solution must have "
                    "private source"
                )
        if publication_status in {"planned", "published"}:
            if not isinstance(publication_date, str):
                raise ResultsSchemaError(
                    f"record {index}.solution_publication_date is required"
                )
            try:
                datetime.date.fromisoformat(publication_date)
            except ValueError as exc:
                raise ResultsSchemaError(
                    f"record {index}.solution_publication_date is invalid"
                ) from exc
        elif publication_date is not None:
            raise ResultsSchemaError(
                f"record {index}.solution_publication_date is not allowed"
            )
    return doc


def read_results_file(path: pathlib.Path) -> tuple[dict[str, Any], int]:
    """Read schema version 1 or 2 and return a validated version-2 view."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResultsSchemaError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ResultsSchemaError(f"{path} must contain a JSON object")
    version = data.get("schema_version")
    if version == 1:
        return convert_v1(data, context=str(path)), 1
    if version == SCHEMA_VERSION:
        return validate_v2(data, context=str(path)), SCHEMA_VERSION
    raise ResultsSchemaError(
        f"{path} has schema_version {version!r}; supported versions are "
        f"1 and {SCHEMA_VERSION}"
    )


def canonical_file_bytes(data: dict[str, Any]) -> bytes:
    """Stable pretty-printed on-disk representation for schema-version-2 files."""

    validate_v2(data)
    rendered = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
    return (rendered + "\n").encode("utf-8")


def canonical_store_digest(files: Iterable[tuple[str, dict[str, Any]]]) -> str:
    """Digest a path-sorted set of canonical schema-version-2 files."""

    digest = hashlib.sha256()
    for relative_path, data in sorted(files, key=lambda item: item[0]):
        encoded_path = relative_path.encode("utf-8")
        contents = canonical_file_bytes(data)
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()
