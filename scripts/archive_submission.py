#!/usr/bin/env python3
"""
Audit-archive a lean-eval submission.

Two subcommands, run from different workflow jobs:

  encrypt  Runs in the `evaluate` job alongside fetch. Reads the
           plaintext source tarball, encrypts it with `age` to every
           recipient in `.audit/recipients.txt`, writes the ciphertext
           and a partial sidecar JSON. Failing here fails the
           submission (see SECURITY.md).

  push     Runs in the `archive` job on a fresh runner. Takes the
           ciphertext and partial sidecar from `encrypt`, merges in
           per-problem evaluator verdict from summary.json, computes
           the ciphertext digest, and commits both objects atomically
           to leanprover/lean-eval-audit through Git using the
           `lean-eval-archiver` App's installation token.

The split is intentional: only the `evaluate` job has the plaintext;
only the `archive` job has the archiver-App token. Neither job sees
both at the same time. See docs/audit-archive.md for the design.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from key_capability_contract import ContractError, validate_envelope


# 100 MB (decimal). Matches the workflow. Sits below GitHub's 100 MiB per-file
# limit with room for the age envelope (~16 bytes per 64 KiB chunk plus header).
SIZE_CAP_BYTES = 100 * 1000 * 1000
SIDECAR_SCHEMA_VERSION = 1
SERVER_SIDECAR_SCHEMA_VERSION = 2
ENVELOPE_SIDECAR_SCHEMA_VERSION = 3
ARCHIVE_LOCATOR_SCHEMA_VERSION = 1
DEFAULT_AUDIT_REPO = "leanprover/lean-eval-audit"
PUSH_RETRY_ATTEMPTS = 5

# Submission refs are full 40-char lowercase hex SHAs (enforced at fetch
# time, see fetch_submission.py:SHA_RE). The audit-side `push` re-checks
# this — it's the privileged boundary that holds the audit-repo write
# token, and `submission_ref` is interpolated into the upload path.
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
# `owner/name` for github_repo, `user/gist-id` for gist. Matches the
# shape produced by fetch_submission.py:submission_repo_identifier.
REPO_IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
ALLOWED_SUBMISSION_KINDS = ("github_repo", "gist")
ALLOWED_SOLUTION_PUBLICATION_STATUSES = ("private", "planned", "published")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UUIDV7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _sha256_of_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_blob_sha(content: bytes) -> str:
    """Compute the Git blob SHA-1 of `content`.

    Git stores files as objects with the header `blob <length>\\0<content>`.
    This is the value the GitHub Contents API returns in the `sha` field
    of a file, and it lets us compare existing-vs-local bytes without
    downloading the full file body.
    """
    h = hashlib.sha1()
    h.update(b"blob " + str(len(content)).encode("ascii") + b"\0")
    h.update(content)
    return h.hexdigest()


def _read_json(path: pathlib.Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        sys.exit(f"JSON root must be an object: {path}")
    return value


def _short_ref(sha: str) -> str:
    return sha[:8]


SUBMITTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def _audit_path(sidecar: dict, archived_at: dt.datetime) -> str:
    if sidecar.get("schema_version") in (
        SERVER_SIDECAR_SCHEMA_VERSION,
        ENVELOPE_SIDECAR_SCHEMA_VERSION,
    ):
        submission_id = str(sidecar["submission_id"])
        prefix = submission_id.replace("-", "")[:2]
        return f"archives/{prefix}/{submission_id}"

    # Path layout: audit/YYYY/MM/{submitter}-{issue}-{ref8}.{tar.age,json}.
    # Including the submitter login is what guarantees uniqueness: backfilled
    # records carry their original `leanprover/lean-eval` issue numbers (see
    # the submissions repo README's issue_number-provenance note), so the
    # same integer issue can refer to two unrelated submissions from
    # different submitters. Live records all come from this repo and have
    # globally-unique issues, but treating them the same way means there is
    # one path schema, not two.
    submitter = str(sidecar["submitter"])
    if not SUBMITTER_RE.fullmatch(submitter):
        sys.exit(f"sidecar.submitter has unexpected shape: {submitter!r}")
    issue = int(sidecar["issue"])
    ref8 = _short_ref(str(sidecar["submission_ref"]))
    return f"audit/{archived_at:%Y/%m}/{submitter}-{issue}-{ref8}"


# ---------------------------------------------------------------------------
# encrypt subcommand
# ---------------------------------------------------------------------------


def _require_type(metadata: dict, key: str, expected: type | tuple[type, ...]) -> object:
    """Strict isinstance check. Refuses to coerce.

    Without this, `bool(metadata["submission_public"])` would turn the
    string `"false"` into `True` — a silently wrong sidecar field.
    """
    value = metadata[key]
    accepts_int = expected is int or (
        isinstance(expected, tuple) and int in expected
    )
    if not isinstance(value, expected) or (accepts_int and isinstance(value, bool)):
        type_name = (
            expected.__name__ if isinstance(expected, type)
            else "/".join(t.__name__ for t in expected)
        )
        sys.exit(f"metadata.json field {key!r} must be {type_name}, got {type(value).__name__}: {value!r}")
    if isinstance(value, str) and not value.strip():
        sys.exit(f"metadata.json field {key!r} is empty/whitespace")
    return value


def _encrypt(args: argparse.Namespace) -> int:
    source_tar = args.source_tar
    if not source_tar.is_file():
        sys.exit(f"source tar not found: {source_tar}")

    size_bytes = source_tar.stat().st_size
    if size_bytes > SIZE_CAP_BYTES:
        # Workflow checks this first and exits before invoking us; this is
        # belt-and-braces so a misconfigured caller cannot bypass the cap.
        sys.exit(
            f"source tarball is {size_bytes} bytes, over the {SIZE_CAP_BYTES}-byte "
            f"audit cap. The submission must be rejected."
        )

    recipients = args.recipients
    if not recipients.is_file():
        sys.exit(f"recipients file not found: {recipients}")
    if not any(_recipient_lines(recipients)):
        sys.exit(f"recipients file is empty: {recipients}")

    metadata = _read_json(args.metadata)
    required = ("submission_ref", "submission_repo", "submission_kind",
                "submission_public", "submitted_by", "model")
    missing = [key for key in required if key not in metadata]
    if missing:
        sys.exit(f"metadata.json missing required fields: {missing!r}")

    # Strict typing — the sidecar is later read by `push` and (eventually)
    # by people reading the archive; loose-typed fields here silently
    # produce wrong sidecars (e.g. `bool("false") is True`).
    submission_ref = _require_type(metadata, "submission_ref", str)
    submission_repo = _require_type(metadata, "submission_repo", str)
    submission_kind = _require_type(metadata, "submission_kind", str)
    submission_public = _require_type(metadata, "submission_public", bool)
    submitted_by = _require_type(metadata, "submitted_by", str)
    model = _require_type(metadata, "model", str)
    if not SHA40_RE.fullmatch(submission_ref):
        sys.exit(f"submission_ref must be a 40-char lowercase hex SHA, got {submission_ref!r}")
    if not REPO_IDENT_RE.fullmatch(submission_repo):
        sys.exit(f"submission_repo has unexpected shape: {submission_repo!r}")
    if submission_kind not in ALLOWED_SUBMISSION_KINDS:
        sys.exit(f"submission_kind must be one of {ALLOWED_SUBMISSION_KINDS!r}, got {submission_kind!r}")
    submission_id = metadata.get("submission_id")
    if submission_id is None:
        if "issue_number" not in metadata:
            sys.exit("metadata.json missing required legacy field: 'issue_number'")
        issue_number = _require_type(metadata, "issue_number", int)
        if issue_number <= 0:
            sys.exit(f"issue_number must be a positive integer, got {issue_number!r}")
    else:
        if "issue_number" in metadata:
            sys.exit("metadata.json must not contain both submission_id and issue_number")
        if not isinstance(submission_id, str) or not UUIDV7_RE.fullmatch(submission_id):
            sys.exit(
                "submission_id must be a canonical lowercase UUIDv7, got "
                f"{submission_id!r}"
            )

    publication_status = metadata.get("solution_publication_status")
    publication_date = metadata.get("solution_publication_date")
    if publication_status is not None:
        if not isinstance(publication_status, str):
            sys.exit(
                "metadata.json field 'solution_publication_status' must be a "
                "string when present"
            )
        if publication_status not in ALLOWED_SOLUTION_PUBLICATION_STATUSES:
            sys.exit(
                "metadata.json field 'solution_publication_status' must be one "
                f"of {ALLOWED_SOLUTION_PUBLICATION_STATUSES!r}"
            )
        if publication_status == "published" and not submission_public:
            sys.exit(
                "metadata.json published solution status requires a public submission"
            )
        if publication_status in {"private", "planned"} and submission_public:
            sys.exit(
                "metadata.json private/planned solution status requires a private "
                "submission"
            )
    if publication_date is not None:
        if publication_status not in {"planned", "published"}:
            sys.exit(
                "metadata.json field 'solution_publication_date' requires a "
                "planned or published status"
            )
        if not isinstance(publication_date, str) or not DATE_RE.fullmatch(
            publication_date
        ):
            sys.exit(
                "metadata.json field 'solution_publication_date' must use "
                "YYYY-MM-DD format"
            )
        try:
            dt.date.fromisoformat(publication_date)
        except ValueError:
            sys.exit(
                "metadata.json field 'solution_publication_date' is not a valid "
                f"calendar date: {publication_date!r}"
            )
    elif publication_status in {"planned", "published"}:
        sys.exit(
            "metadata.json field 'solution_publication_date' is required for "
            f"status {publication_status!r}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ciphertext = args.output_dir / "source.tar.gz.age"
    partial_sidecar = args.output_dir / "sidecar.partial.json"

    plaintext_sha = _sha256_of_file(source_tar)

    # Encrypt with `age`. The recipients file is read by age; we never load
    # private keys here. Output goes to a fresh file so a half-written
    # ciphertext from an interrupted run cannot be confused with a real one.
    proc = subprocess.run(
        ["age", "--recipients-file", str(recipients),
         "--output", str(ciphertext), str(source_tar)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        ciphertext.unlink(missing_ok=True)
        sys.exit(f"age encryption failed (exit {proc.returncode}):\n{proc.stderr}")

    # Sanity check the output before we trust it. Age format-version-1
    # ciphertexts start
    # with `age-encryption.org/v1\n`; reject anything else so a misbehaving
    # binary cannot silently produce a zero-length or plaintext file.
    with ciphertext.open("rb") as fh:
        header = fh.read(32)
    if not header.startswith(b"age-encryption.org/v1\n"):
        ciphertext.unlink(missing_ok=True)
        sys.exit(
            "age output does not have the expected format-version-1 header: "
            f"{header!r}"
        )

    sidecar = {
        "schema_version": (
            SERVER_SIDECAR_SCHEMA_VERSION
            if submission_id is not None
            else SIDECAR_SCHEMA_VERSION
        ),
        "submission_repo": submission_repo,
        "submission_ref": submission_ref,
        "submission_kind": submission_kind,
        "submission_public": submission_public,
        "submitter": submitted_by,
        "model": model,
        "size_bytes_plaintext_tar": size_bytes,
        "sha256_plaintext_tar": plaintext_sha,
    }
    if submission_id is not None:
        sidecar["submission_id"] = submission_id
    else:
        sidecar["issue"] = issue_number
    production_description = metadata.get("production_description")
    if production_description:
        if not isinstance(production_description, str):
            sys.exit("metadata.json field 'production_description' must be a string when present")
        sidecar["production_description"] = production_description
    if publication_status is not None:
        sidecar["solution_publication_status"] = publication_status
        if publication_date is not None:
            sidecar["solution_publication_date"] = publication_date

    partial_sidecar.write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"encrypted: {ciphertext} ({ciphertext.stat().st_size} bytes)")
    print(f"sidecar:   {partial_sidecar}")
    return 0


def _recipient_lines(path: pathlib.Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def _prepare_envelope_sidecar(args: argparse.Namespace) -> int:
    """Bind validated server metadata to one provider-neutral key envelope."""
    source_tar = args.source_tar
    ciphertext = args.ciphertext
    if not source_tar.is_file() or source_tar.is_symlink():
        sys.exit(f"source tar not found or not a regular file: {source_tar}")
    if not ciphertext.is_file() or ciphertext.is_symlink():
        sys.exit(f"ciphertext not found or not a regular file: {ciphertext}")
    source_size = source_tar.stat().st_size
    if source_size > SIZE_CAP_BYTES:
        sys.exit(
            f"source tarball is {source_size} bytes, over the {SIZE_CAP_BYTES}-byte "
            "audit cap. The submission must be rejected."
        )

    metadata = _read_json(args.metadata)
    required = (
        "submission_id",
        "submission_ref",
        "submission_repo",
        "submission_kind",
        "submission_public",
        "submitted_by",
        "model",
    )
    missing = [key for key in required if key not in metadata]
    if missing:
        sys.exit(f"metadata.json missing required fields: {missing!r}")
    if "issue_number" in metadata:
        sys.exit("server metadata must not contain issue_number")

    try:
        envelope = validate_envelope(_read_json(args.envelope))
    except ContractError as error:
        sys.exit(f"invalid archive key envelope: {error}")
    submission_id = metadata["submission_id"]
    if envelope["submission_id"] != submission_id:
        sys.exit("archive key envelope belongs to a different submission")
    ciphertext_digest = _sha256_of_file(ciphertext)
    if envelope["archive_ciphertext_sha256"] != ciphertext_digest:
        sys.exit("archive key envelope digest does not match ciphertext")

    sidecar = {
        "schema_version": ENVELOPE_SIDECAR_SCHEMA_VERSION,
        "submission_id": submission_id,
        "submission_repo": metadata["submission_repo"],
        "submission_ref": metadata["submission_ref"],
        "submission_kind": metadata["submission_kind"],
        "submission_public": metadata["submission_public"],
        "submitter": metadata["submitted_by"],
        "model": metadata["model"],
        "size_bytes_plaintext_tar": source_size,
        "sha256_plaintext_tar": _sha256_of_file(source_tar),
        "key_envelope": envelope,
    }
    _validate_sidecar(sidecar)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"sidecar:   {args.output}")
    return 0


# ---------------------------------------------------------------------------
# push subcommand
# ---------------------------------------------------------------------------


_COMMON_SIDECAR_FIELDS = {
    "schema_version",
    "submission_repo",
    "submission_ref",
    "submission_kind",
    "submission_public",
    "submitter",
    "model",
    "size_bytes_plaintext_tar",
    "sha256_plaintext_tar",
    "production_description",
    "solution_publication_status",
    "solution_publication_date",
    "key_envelope",
}
_FINAL_SIDECAR_FIELDS = {
    "sha256_ciphertext",
    "size_bytes_ciphertext",
    "archived_at",
    "benchmark_commit",
    "archiver_workflow_run",
    "problem_ids",
    "evaluator_verdict",
}


def _validate_sidecar(sidecar: dict, *, finalized: bool = False) -> None:
    """Strict schema check on the partial sidecar read by `push`.

    `push` interpolates `submission_ref` into the upload path and trusts
    the rest for the sidecar JSON committed to the audit repo, so a
    malformed sidecar would either produce an unexpected path or commit
    junk metadata. Re-validating here is defense in depth against a
    corrupted artifact or a future caller that bypasses `encrypt`.
    """
    schema_version = sidecar.get("schema_version")
    if type(schema_version) is not int or schema_version not in (
        SIDECAR_SCHEMA_VERSION,
        SERVER_SIDECAR_SCHEMA_VERSION,
        ENVELOPE_SIDECAR_SCHEMA_VERSION,
    ):
        sys.exit(
            "sidecar schema_version must be "
            f"{SIDECAR_SCHEMA_VERSION}, {SERVER_SIDECAR_SCHEMA_VERSION}, or "
            f"{ENVELOPE_SIDECAR_SCHEMA_VERSION}, "
            f"got {schema_version!r}"
        )
    identity_field = (
        "submission_id"
        if schema_version in (
            SERVER_SIDECAR_SCHEMA_VERSION,
            ENVELOPE_SIDECAR_SCHEMA_VERSION,
        )
        else "issue"
    )
    allowed_fields = _COMMON_SIDECAR_FIELDS | {identity_field}
    if finalized:
        allowed_fields |= _FINAL_SIDECAR_FIELDS
    unknown_fields = sorted(set(sidecar) - allowed_fields)
    if unknown_fields:
        sys.exit(f"sidecar has unknown fields: {unknown_fields!r}")
    if schema_version == SIDECAR_SCHEMA_VERSION:
        issue = sidecar.get("issue")
        if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
            sys.exit(f"sidecar.issue must be a positive integer, got {issue!r}")
        if "submission_id" in sidecar:
            sys.exit("legacy sidecar must not contain submission_id")
    else:
        submission_id = sidecar.get("submission_id")
        if not isinstance(submission_id, str) or not UUIDV7_RE.fullmatch(submission_id):
            sys.exit(
                "sidecar.submission_id must be a canonical lowercase UUIDv7, got "
                f"{submission_id!r}"
            )
        if "issue" in sidecar:
            sys.exit("server sidecar must not contain issue")
    key_envelope = sidecar.get("key_envelope")
    if schema_version == ENVELOPE_SIDECAR_SCHEMA_VERSION:
        try:
            envelope = validate_envelope(key_envelope)
        except ContractError as error:
            sys.exit(f"sidecar.key_envelope is invalid: {error}")
        if envelope["submission_id"] != sidecar["submission_id"]:
            sys.exit("sidecar.key_envelope belongs to a different submission")
    elif key_envelope is not None:
        sys.exit("only a schema-version-3 sidecar may contain key_envelope")
    submission_ref = sidecar.get("submission_ref")
    if not isinstance(submission_ref, str) or not SHA40_RE.fullmatch(submission_ref):
        sys.exit(f"sidecar.submission_ref must be a 40-char lowercase hex SHA, got {submission_ref!r}")
    submission_repo = sidecar.get("submission_repo")
    if not isinstance(submission_repo, str) or not REPO_IDENT_RE.fullmatch(submission_repo):
        sys.exit(f"sidecar.submission_repo has unexpected shape: {submission_repo!r}")
    submission_kind = sidecar.get("submission_kind")
    if submission_kind not in ALLOWED_SUBMISSION_KINDS:
        sys.exit(f"sidecar.submission_kind must be one of {ALLOWED_SUBMISSION_KINDS!r}, got {submission_kind!r}")
    if not isinstance(sidecar.get("submission_public"), bool):
        sys.exit(f"sidecar.submission_public must be bool, got {type(sidecar.get('submission_public')).__name__}")
    for str_key in ("submitter", "model"):
        v = sidecar.get(str_key)
        if not isinstance(v, str) or not v.strip():
            sys.exit(f"sidecar.{str_key} must be a non-empty string, got {v!r}")
    plain_sha = sidecar.get("sha256_plaintext_tar")
    if not isinstance(plain_sha, str) or not SHA256_HEX_RE.fullmatch(plain_sha):
        sys.exit(f"sidecar.sha256_plaintext_tar must be 64-char lowercase hex, got {plain_sha!r}")
    size_plain = sidecar.get("size_bytes_plaintext_tar")
    if not isinstance(size_plain, int) or isinstance(size_plain, bool) or size_plain < 0:
        sys.exit(f"sidecar.size_bytes_plaintext_tar must be a non-negative integer, got {size_plain!r}")
    production_description = sidecar.get("production_description")
    if production_description is not None and (
        not isinstance(production_description, str)
        or not production_description.strip()
    ):
        sys.exit("sidecar.production_description must be a non-empty string")
    publication_status = sidecar.get("solution_publication_status")
    publication_date = sidecar.get("solution_publication_date")
    submission_public = sidecar["submission_public"]
    if publication_status is not None:
        if publication_status not in ALLOWED_SOLUTION_PUBLICATION_STATUSES:
            sys.exit(
                "sidecar.solution_publication_status must be one of "
                f"{ALLOWED_SOLUTION_PUBLICATION_STATUSES!r}, got "
                f"{publication_status!r}"
            )
        if publication_status == "published" and not submission_public:
            sys.exit("sidecar published solution status requires a public submission")
        if publication_status in {"private", "planned"} and submission_public:
            sys.exit(
                "sidecar private/planned solution status requires a private submission"
            )
    if publication_date is not None:
        if publication_status not in {"planned", "published"}:
            sys.exit(
                "sidecar.solution_publication_date requires a planned or "
                "published status"
            )
        if not isinstance(publication_date, str) or not DATE_RE.fullmatch(
            publication_date
        ):
            sys.exit(
                "sidecar.solution_publication_date must use YYYY-MM-DD format"
            )
        try:
            dt.date.fromisoformat(publication_date)
        except ValueError:
            sys.exit(
                "sidecar.solution_publication_date is not a valid calendar date: "
                f"{publication_date!r}"
            )
    elif publication_status in {"planned", "published"}:
        sys.exit(
            "sidecar.solution_publication_date is required for status "
            f"{publication_status!r}"
        )
    if finalized:
        ciphertext_sha = sidecar.get("sha256_ciphertext")
        if not isinstance(ciphertext_sha, str) or not SHA256_HEX_RE.fullmatch(
            ciphertext_sha
        ):
            sys.exit(
                "final sidecar.sha256_ciphertext must be 64-char lowercase hex"
            )
        ciphertext_size = sidecar.get("size_bytes_ciphertext")
        if (
            not isinstance(ciphertext_size, int)
            or isinstance(ciphertext_size, bool)
            or ciphertext_size <= 0
        ):
            sys.exit("final sidecar.size_bytes_ciphertext must be a positive integer")
        if schema_version == ENVELOPE_SIDECAR_SCHEMA_VERSION:
            if envelope["archive_ciphertext_sha256"] != ciphertext_sha:
                sys.exit(
                    "final sidecar ciphertext digest does not match key_envelope"
                )
        archived_at = sidecar.get("archived_at")
        if not isinstance(archived_at, str) or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", archived_at
        ) is None:
            sys.exit("final sidecar.archived_at must use canonical UTC seconds")
        benchmark_commit = sidecar.get("benchmark_commit")
        if benchmark_commit is not None and (
            not isinstance(benchmark_commit, str)
            or not SHA40_RE.fullmatch(benchmark_commit)
        ):
            sys.exit("final sidecar.benchmark_commit must be a lowercase commit SHA")
        workflow_run = sidecar.get("archiver_workflow_run")
        if workflow_run is not None and (
            not isinstance(workflow_run, str) or not workflow_run.strip()
        ):
            sys.exit("final sidecar.archiver_workflow_run must be a non-empty string")
        problem_ids = sidecar.get("problem_ids")
        if problem_ids is not None and (
            not isinstance(problem_ids, list)
            or not all(isinstance(value, str) and value for value in problem_ids)
            or problem_ids != sorted(set(problem_ids))
        ):
            sys.exit("final sidecar.problem_ids must be sorted unique strings")
        verdict = sidecar.get("evaluator_verdict")
        if verdict is not None and (
            not isinstance(verdict, dict)
            or not all(
                isinstance(key, str)
                and key
                and value in {"pass", "fail", "skipped"}
                for key, value in verdict.items()
            )
        ):
            sys.exit("final sidecar.evaluator_verdict has invalid entries")


# The stable identity of an archived submission. Two sidecars describe the
# same source iff every one of these fields matches. `submission_ref` is a
# 40-char git SHA, which immutably pins the source tree content, so
# (submitter, issue, repo, ref) is a complete and stable identity. The
# plaintext-tar digest is deliberately NOT part of it: gzip/tar packaging is
# not byte-reproducible, so re-fetching the same ref yields a different
# `sha256_plaintext_tar` for identical content — including it would
# misclassify a legitimate re-evaluation as a colliding archive.
_LEGACY_IDENTITY_FIELDS = (
    "submitter",
    "issue",
    "submission_repo",
    "submission_ref",
)
_SERVER_IDENTITY_FIELDS = (
    "submission_id",
    "submitter",
    "submission_repo",
    "submission_ref",
)


def _identity_fields(sidecar: dict) -> tuple[str, ...]:
    if sidecar.get("schema_version") in (
        SERVER_SIDECAR_SCHEMA_VERSION,
        ENVELOPE_SIDECAR_SCHEMA_VERSION,
    ):
        return _SERVER_IDENTITY_FIELDS
    return _LEGACY_IDENTITY_FIELDS


def _same_source(existing: dict, ours: dict) -> bool:
    fields = _identity_fields(ours)
    if existing.get("schema_version") != ours.get("schema_version"):
        return False
    return all(
        existing.get(f) is not None and existing.get(f) == ours.get(f)
        for f in fields
    )


def _write_locator(
    path: pathlib.Path,
    *,
    sidecar: dict,
    audit_repo: str,
    archive_commit: str,
    archive_path: str,
) -> dict:
    if not SHA40_RE.fullmatch(archive_commit):
        sys.exit(
            "GitHub did not return a 40-char lowercase commit SHA for the "
            f"archive write: {archive_commit!r}"
        )
    digest = sidecar.get("sha256_ciphertext")
    if not isinstance(digest, str) or not SHA256_HEX_RE.fullmatch(digest):
        sys.exit(
            "archived sidecar has no valid sha256_ciphertext; cannot emit State locator"
        )
    locator = {
        "schema_version": ARCHIVE_LOCATOR_SCHEMA_VERSION,
        "submission_id": sidecar["submission_id"],
        "archive_repository": audit_repo,
        "archive_commit": archive_commit,
        "archive_path": archive_path,
        "archive_ciphertext_sha256": digest,
        "encrypted": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(locator, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return locator


def _write_archive_completion(
    path: pathlib.Path,
    *,
    sidecar: dict,
    locator: dict,
) -> None:
    archived_at = sidecar.get("archived_at")
    if not isinstance(archived_at, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", archived_at
    ) is None:
        sys.exit("final sidecar has no canonical archived_at for State completion")
    completion = {
        "schema_version": 1,
        "occurred_at": archived_at[:-1] + ".000Z",
        "locator": locator,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(completion, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git_auth_environment(token: str, root: pathlib.Path) -> dict[str, str]:
    """Return a non-interactive Git environment without putting the token in argv."""
    askpass = root / "git-askpass.sh"
    askpass.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  *Username*) printf '%s\\n' x-access-token ;;\n"
        "  *Password*) printf '%s\\n' \"$LEAN_EVAL_GIT_TOKEN\" ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    env = os.environ.copy()
    env.update(
        {
            "GIT_ASKPASS": str(askpass),
            "GIT_TERMINAL_PROMPT": "0",
            "LEAN_EVAL_GIT_TOKEN": token,
        }
    )
    return env


def _audit_git_url(audit_repo: str) -> str:
    return f"https://github.com/{audit_repo}.git"


def _run_git(
    args: list[str],
    *,
    env: dict[str, str],
    input_text: str | None = None,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            env=env,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        sys.exit("git executable is required to persist the audit archive")
    except subprocess.TimeoutExpired:
        sys.exit(f"git {' '.join(args[:3])} timed out after {timeout} seconds")
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        sys.exit(f"git {' '.join(args[:3])} failed: {detail}")
    return result


def _git_commit_archive(
    *,
    repo_dir: pathlib.Path,
    env: dict[str, str],
    parent: str,
    index_path: pathlib.Path,
    ciphertext_blob: str,
    ciphertext_remote: str,
    sidecar_blob: str,
    sidecar_remote: str,
    message: str,
) -> str:
    index_path.unlink(missing_ok=True)
    index_env = env.copy()
    index_env["GIT_INDEX_FILE"] = str(index_path)
    _run_git(["-C", str(repo_dir), "read-tree", parent], env=index_env)
    for blob, path in (
        (ciphertext_blob, ciphertext_remote),
        (sidecar_blob, sidecar_remote),
    ):
        _run_git(
            [
                "-C",
                str(repo_dir),
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob},{path}",
            ],
            env=index_env,
        )
    tree = _run_git(
        ["-C", str(repo_dir), "write-tree"],
        env=index_env,
    ).stdout.strip()
    commit_env = index_env.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_NAME": "Lean Eval Archiver",
            "GIT_AUTHOR_EMAIL": "lean-eval-archiver@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "Lean Eval Archiver",
            "GIT_COMMITTER_EMAIL": "lean-eval-archiver@users.noreply.github.com",
        }
    )
    commit = _run_git(
        ["-C", str(repo_dir), "commit-tree", tree, "-p", parent],
        env=commit_env,
        input_text=message + "\n",
    ).stdout.strip()
    if not SHA40_RE.fullmatch(commit):
        sys.exit(f"git commit-tree returned an invalid commit: {commit!r}")
    return commit


def _push_git_archive(
    *,
    audit_repo: str,
    token: str,
    ciphertext: pathlib.Path,
    sidecar_bytes: bytes,
    ciphertext_remote: str,
    sidecar_remote: str,
    sidecar: dict,
    message: str,
) -> str:
    """Atomically append ciphertext and sidecar with a native Git push.

    Native Git transfers the raw blob rather than embedding it in a base64 JSON
    request. Each retry rebuilds the commit on the latest `main` tree. A
    concurrent writer that archived this same source wins idempotently; an
    identity collision remains a hard failure.
    """
    with tempfile.TemporaryDirectory(prefix="lean-eval-archive-push-") as td:
        root = pathlib.Path(td)
        repo_dir = root / "repo"
        sidecar_file = root / "sidecar.json"
        sidecar_file.write_bytes(sidecar_bytes)
        env = _git_auth_environment(token, root)
        _run_git(["init", "--quiet", str(repo_dir)], env=env)
        _run_git(
            [
                "-C",
                str(repo_dir),
                "remote",
                "add",
                "origin",
                _audit_git_url(audit_repo),
            ],
            env=env,
        )
        ciphertext_blob = _run_git(
            ["-C", str(repo_dir), "hash-object", "-w", str(ciphertext)],
            env=env,
            timeout=300,
        ).stdout.strip()
        sidecar_blob = _run_git(
            ["-C", str(repo_dir), "hash-object", "-w", str(sidecar_file)],
            env=env,
        ).stdout.strip()
        for label, blob in (
            ("ciphertext", ciphertext_blob),
            ("sidecar", sidecar_blob),
        ):
            if not SHA40_RE.fullmatch(blob):
                sys.exit(f"git hash-object returned an invalid {label} blob: {blob!r}")

        last_error = ""
        for attempt in range(1, PUSH_RETRY_ATTEMPTS + 1):
            fetched = _run_git(
                [
                    "-C",
                    str(repo_dir),
                    "fetch",
                    "--quiet",
                    "--depth=1",
                    "--filter=blob:none",
                    "origin",
                    "refs/heads/main",
                ],
                env=env,
                check=False,
                timeout=300,
            )
            if fetched.returncode != 0:
                last_error = (fetched.stderr or fetched.stdout).strip()
                if attempt < PUSH_RETRY_ATTEMPTS:
                    time.sleep(
                        min(30.0, 2.0 ** (attempt - 1))
                        * random.uniform(0.5, 1.5)
                    )
                    continue
                break
            parent = _run_git(
                ["-C", str(repo_dir), "rev-parse", "FETCH_HEAD"],
                env=env,
            ).stdout.strip()
            if not SHA40_RE.fullmatch(parent):
                sys.exit(f"git fetch returned an invalid audit head: {parent!r}")
            commit = _git_commit_archive(
                repo_dir=repo_dir,
                env=env,
                parent=parent,
                index_path=root / "archive.index",
                ciphertext_blob=ciphertext_blob,
                ciphertext_remote=ciphertext_remote,
                sidecar_blob=sidecar_blob,
                sidecar_remote=sidecar_remote,
                message=message,
            )
            pushed = _run_git(
                [
                    "-C",
                    str(repo_dir),
                    "push",
                    "--porcelain",
                    "origin",
                    f"{commit}:refs/heads/main",
                ],
                env=env,
                check=False,
                timeout=300,
            )
            if pushed.returncode == 0:
                return commit

            last_error = (pushed.stderr or pushed.stdout).strip()
            existing = _get_remote_sidecar(
                audit_repo=audit_repo,
                token=token,
                path=sidecar_remote,
            )
            if existing is not None:
                if _same_source(existing, sidecar):
                    print(
                        "archive: concurrent writer already committed this source; "
                        "idempotent no-op",
                        file=sys.stderr,
                    )
                    return _latest_path_commit(
                        audit_repo=audit_repo,
                        token=token,
                        path=sidecar_remote,
                    )
                identity_fields = _identity_fields(sidecar)
                existing_identity = {
                    field: existing.get(field) for field in identity_fields
                }
                ours_identity = {
                    field: sidecar.get(field) for field in identity_fields
                }
                sys.exit(
                    f"audit path already exists in {audit_repo} for a different "
                    f"source (existing {existing_identity} vs ours {ours_identity})"
                )
            if attempt < PUSH_RETRY_ATTEMPTS:
                time.sleep(
                    min(30.0, 2.0 ** (attempt - 1))
                    * random.uniform(0.5, 1.5)
                )
        sys.exit(
            "native Git push to lean-eval-audit failed after "
            f"{PUSH_RETRY_ATTEMPTS} attempts: {last_error}"
        )


def _verify_ciphertext_at_commit(
    *,
    audit_repo: str,
    token: str,
    archive_commit: str,
    archive_path: str,
    expected_sha256: str,
) -> None:
    """Prove the immutable commit named in State contains the expected bytes.

    Fetch through Git rather than the JSON Git Blobs API. The latter base64
    expands an 86 MB ciphertext beyond 100 MB even though the Git blob itself
    is within GitHub's per-object limit.
    """
    if not SHA40_RE.fullmatch(archive_commit):
        sys.exit(f"archive commit has unexpected shape: {archive_commit!r}")
    with tempfile.TemporaryDirectory(prefix="lean-eval-archive-verify-") as td:
        repo_dir = pathlib.Path(td) / "repo"
        env = _git_auth_environment(token, pathlib.Path(td))
        _run_git(["init", "--quiet", str(repo_dir)], env=env)
        _run_git(
            [
                "-C",
                str(repo_dir),
                "remote",
                "add",
                "origin",
                _audit_git_url(audit_repo),
            ],
            env=env,
        )
        _run_git(
            [
                "-C",
                str(repo_dir),
                "fetch",
                "--quiet",
                "--depth=1",
                "--filter=blob:none",
                "origin",
                archive_commit,
            ],
            env=env,
            timeout=300,
        )
        proc = subprocess.Popen(
            [
                "git",
                "-C",
                str(repo_dir),
                "cat-file",
                "blob",
                f"FETCH_HEAD:{archive_path}",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        digest = hashlib.sha256()
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
            digest.update(chunk)
        proc.stdout.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()
        returncode = proc.wait(timeout=300)
        if returncode != 0:
            sys.exit(
                "could not stream archived ciphertext from "
                f"{archive_commit}:{archive_path}: {stderr.strip()}"
            )
        actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        sys.exit(
            "archive commit does not contain the ciphertext recorded by its sidecar: "
            f"expected {expected_sha256}, got {actual_sha256} at "
            f"{archive_commit}:{archive_path}"
        )


def _push(args: argparse.Namespace) -> int:
    token = os.environ.get("ARCHIVER_TOKEN") or ""
    if not token:
        sys.exit("ARCHIVER_TOKEN env var is empty or missing")

    ciphertext = args.ciphertext
    if not ciphertext.is_file():
        sys.exit(f"ciphertext not found: {ciphertext}")
    sidecar_path = args.sidecar
    if not sidecar_path.is_file():
        sys.exit(f"partial sidecar not found: {sidecar_path}")
    sidecar = _read_json(sidecar_path)

    _validate_sidecar(sidecar)

    is_server_submission = sidecar["schema_version"] in (
        SERVER_SIDECAR_SCHEMA_VERSION,
        ENVELOPE_SIDECAR_SCHEMA_VERSION,
    )
    if is_server_submission and (
        args.locator_output is None or args.completion_output is None
    ):
        sys.exit(
            "--locator-output and --completion-output are required for a "
            "server-submission archive"
        )

    sidecar["sha256_ciphertext"] = _sha256_of_file(ciphertext)
    sidecar["size_bytes_ciphertext"] = ciphertext.stat().st_size

    archived_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    sidecar["archived_at"] = archived_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.benchmark_commit:
        if not SHA40_RE.fullmatch(args.benchmark_commit):
            sys.exit(f"--benchmark-commit must be a 40-char lowercase hex SHA, got {args.benchmark_commit!r}")
        sidecar["benchmark_commit"] = args.benchmark_commit
    if args.workflow_run_url:
        sidecar["archiver_workflow_run"] = args.workflow_run_url

    # Merge evaluator verdict if summary.json is available. `evaluate`
    # may have failed without writing summary.json; archive must succeed
    # regardless. In that case `evaluator_verdict` is omitted and the
    # sidecar records only archival-time facts.
    #
    # Per-problem records live at summary["run_eval"]["problems"] — the
    # raw output of `lake exe lean-eval run-eval --json` — NOT at the
    # top level of results.json (which is just {"passed": [ids]}).
    if args.summary and args.summary.is_file():
        try:
            summary = _read_json(args.summary)
            run_eval = summary.get("run_eval") or {}
            problems = run_eval.get("problems") or []
            verdict: dict[str, str] = {}
            problem_ids: list[str] = []
            for entry in problems if isinstance(problems, list) else []:
                if not isinstance(entry, dict):
                    continue
                pid = entry.get("id")
                if not isinstance(pid, str) or not pid:
                    continue
                problem_ids.append(pid)
                if entry.get("succeeded") is True:
                    verdict[pid] = "pass"
                elif entry.get("attempted") is True:
                    verdict[pid] = "fail"
                else:
                    verdict[pid] = "skipped"
            if problem_ids:
                sidecar["problem_ids"] = sorted(set(problem_ids))
            if verdict:
                sidecar["evaluator_verdict"] = verdict
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warning: could not parse summary.json: {exc}", file=sys.stderr)

    _validate_sidecar(sidecar, finalized=True)

    base_path = _audit_path(sidecar, archived_at)
    ciphertext_remote = f"{base_path}.tar.age"
    sidecar_remote = f"{base_path}.json"

    sidecar_bytes = (json.dumps(sidecar, indent=2, sort_keys=True) + "\n").encode("utf-8")

    audit_repo = args.audit_repo
    if not REPO_IDENT_RE.fullmatch(audit_repo):
        sys.exit(f"--audit-repo has unexpected shape: {audit_repo!r}")
    identity_label = (
        f"submission {sidecar['submission_id']}"
        if is_server_submission
        else f"issue {sidecar['issue']}"
    )
    commit_message = (
        f"archive: {identity_label} "
        f"({sidecar['submission_repo']}@{_short_ref(sidecar['submission_ref'])})"
    )

    # Re-evaluation safety. The audit path is keyed on the submission's
    # identity (submitter, issue, source ref). Neither the ciphertext nor the
    # plaintext tar is reproducible: age picks a fresh file key per run, and
    # gzip/tar packaging varies, so re-archiving the same source yields
    # different bytes at the same path. Decide by the immutable submission
    # identity — the git ref pins the source tree content — not by any digest.
    # A matching identity means this exact source is already archived (no-op);
    # a path collision with a different identity is a genuine collision for an
    # operator to investigate. Doing this here keeps the immutable first
    # ciphertext in place rather than overwriting it.
    existing_sidecar = _get_remote_sidecar(
        audit_repo=audit_repo, token=token, path=sidecar_remote
    )
    if existing_sidecar is not None:
        if _same_source(existing_sidecar, sidecar):
            print(
                f"archive: {sidecar_remote} already records this source "
                f"({sidecar['submission_repo']}@{_short_ref(sidecar['submission_ref'])}); "
                f"idempotent no-op"
            )
            if is_server_submission:
                _validate_sidecar(existing_sidecar, finalized=True)
                archive_commit = _latest_path_commit(
                    audit_repo=audit_repo,
                    token=token,
                    path=sidecar_remote,
                )
                archived_digest = existing_sidecar.get("sha256_ciphertext")
                if not isinstance(archived_digest, str) or not SHA256_HEX_RE.fullmatch(
                    archived_digest
                ):
                    sys.exit(
                        "existing server sidecar has no valid sha256_ciphertext"
                    )
                _verify_ciphertext_at_commit(
                    audit_repo=audit_repo,
                    token=token,
                    archive_commit=archive_commit,
                    archive_path=ciphertext_remote,
                    expected_sha256=archived_digest,
                )
                locator = _write_locator(
                    args.locator_output,
                    sidecar=existing_sidecar,
                    audit_repo=audit_repo,
                    archive_commit=archive_commit,
                    archive_path=ciphertext_remote,
                )
                _write_archive_completion(
                    args.completion_output,
                    sidecar=existing_sidecar,
                    locator=locator,
                )
            print(f"archived: {audit_repo}:{ciphertext_remote}")
            print(f"          {audit_repo}:{sidecar_remote}")
            return 0
        identity_fields = _identity_fields(sidecar)
        existing_identity = {f: existing_sidecar.get(f) for f in identity_fields}
        ours_identity = {f: sidecar.get(f) for f in identity_fields}
        sys.exit(
            f"audit path {base_path!r} already exists in {audit_repo} for a "
            f"different source (existing {existing_identity} vs ours "
            f"{ours_identity}). This indicates a colliding archive — "
            f"investigate before retrying."
        )

    # Commit ciphertext and sidecar atomically. Native Git keeps the raw
    # ciphertext below GitHub's per-object limit; the Contents API would
    # base64-expand it beyond the request-size limit.
    archive_commit = _push_git_archive(
        audit_repo=audit_repo,
        token=token,
        ciphertext=ciphertext,
        sidecar_bytes=sidecar_bytes,
        ciphertext_remote=ciphertext_remote,
        sidecar_remote=sidecar_remote,
        sidecar=sidecar,
        message=commit_message,
    )

    if is_server_submission:
        _verify_ciphertext_at_commit(
            audit_repo=audit_repo,
            token=token,
            archive_commit=archive_commit,
            archive_path=ciphertext_remote,
            expected_sha256=sidecar["sha256_ciphertext"],
        )
        locator = _write_locator(
            args.locator_output,
            sidecar=sidecar,
            audit_repo=audit_repo,
            archive_commit=archive_commit,
            archive_path=ciphertext_remote,
        )
        _write_archive_completion(
            args.completion_output,
            sidecar=sidecar,
            locator=locator,
        )

    print(f"archived: {audit_repo}:{ciphertext_remote}")
    print(f"          {audit_repo}:{sidecar_remote}")
    return 0


def _api_get(*, audit_repo: str, token: str, path: str) -> dict | None:
    """GET the file metadata at `path` in `audit_repo`. None on 404."""
    api_url = f"https://api.github.com/repos/{audit_repo}/contents/{path}"
    req = urllib.request.Request(
        api_url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lean-eval-archiver",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            if exc.code == 404:
                return None
            body = exc.read().decode("utf-8", errors="replace")
        finally:
            exc.close()
        sys.exit(f"Contents API GET {path} failed ({exc.code}):\n{body}")


def _latest_path_commit(*, audit_repo: str, token: str, path: str) -> str:
    api_url = (
        f"https://api.github.com/repos/{audit_repo}/commits"
        f"?path={urllib.parse.quote(path, safe='')}&per_page=1"
    )
    req = urllib.request.Request(
        api_url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lean-eval-archiver",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        finally:
            exc.close()
        sys.exit(
            f"Commits API GET for {path} failed ({exc.code}): {body}"
        )
    except urllib.error.URLError as exc:
        sys.exit(f"Commits API GET for {path} failed: {exc}")
    if not isinstance(payload, list) or not payload:
        sys.exit(f"Commits API returned no commit for archived path {path!r}")
    commit_sha = payload[0].get("sha") if isinstance(payload[0], dict) else None
    if not isinstance(commit_sha, str) or not SHA40_RE.fullmatch(commit_sha):
        sys.exit(f"Commits API returned an invalid commit SHA for {path!r}: {commit_sha!r}")
    return commit_sha


def _get_remote_sidecar(*, audit_repo: str, token: str, path: str) -> dict | None:
    """Fetch and decode the JSON sidecar at `path`. None if it doesn't exist.

    The Contents API returns small files inline as newline-wrapped base64;
    `base64.b64decode` ignores the newlines. Sidecars are well under the 1 MB
    inline limit, so a missing/`none` encoding is unexpected and treated as a
    hard error rather than silently skipped (skipping would defeat the
    re-archive collision check in `_push`).
    """
    meta = _api_get(audit_repo=audit_repo, token=token, path=path)
    if meta is None:
        return None
    content = meta.get("content")
    if not isinstance(content, str) or meta.get("encoding") != "base64":
        sys.exit(
            f"existing sidecar {path} in {audit_repo} has unexpected encoding "
            f"{meta.get('encoding')!r}; cannot verify re-archive safety"
        )
    try:
        return json.loads(base64.b64decode(content).decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"could not decode existing sidecar {path} in {audit_repo}: {exc}")


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enc = sub.add_parser("encrypt", help="Encrypt source.tar.gz; emit ciphertext + partial sidecar.")
    p_enc.add_argument("--source-tar", type=pathlib.Path, required=True)
    p_enc.add_argument("--metadata", type=pathlib.Path, required=True)
    p_enc.add_argument("--recipients", type=pathlib.Path, required=True)
    p_enc.add_argument("--output-dir", type=pathlib.Path, required=True)
    p_enc.set_defaults(func=_encrypt)

    p_sidecar = sub.add_parser(
        "prepare-envelope-sidecar",
        help="Bind server metadata to an existing per-submission ciphertext envelope.",
    )
    p_sidecar.add_argument("--source-tar", type=pathlib.Path, required=True)
    p_sidecar.add_argument("--metadata", type=pathlib.Path, required=True)
    p_sidecar.add_argument("--ciphertext", type=pathlib.Path, required=True)
    p_sidecar.add_argument("--envelope", type=pathlib.Path, required=True)
    p_sidecar.add_argument("--output", type=pathlib.Path, required=True)
    p_sidecar.set_defaults(func=_prepare_envelope_sidecar)

    p_push = sub.add_parser("push", help="Push ciphertext + sidecar to lean-eval-audit.")
    p_push.add_argument("--ciphertext", type=pathlib.Path, required=True)
    p_push.add_argument("--sidecar", type=pathlib.Path, required=True)
    p_push.add_argument("--summary", type=pathlib.Path, default=None,
                        help="summary.json from evaluate (optional; "
                             "per-problem verdict goes into the sidecar).")
    p_push.add_argument("--benchmark-commit", default="")
    p_push.add_argument("--workflow-run-url", default="")
    p_push.add_argument("--audit-repo", default=DEFAULT_AUDIT_REPO)
    p_push.add_argument(
        "--locator-output",
        type=pathlib.Path,
        default=None,
        help=(
            "Write the immutable State archive locator (required for "
            "UUIDv7 server submissions)."
        ),
    )
    p_push.add_argument(
        "--completion-output",
        type=pathlib.Path,
        default=None,
        help=(
            "Write the authenticated Worker callback payload (required for "
            "UUIDv7 server submissions)."
        ),
    )
    p_push.set_defaults(func=_push)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
