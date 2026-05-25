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
           the ciphertext digest, and uploads both objects to
           leanprover/lean-eval-audit via the GitHub Contents API
           using the `lean-eval-archiver` App's installation token.

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
import time
import urllib.error
import urllib.request


SIZE_CAP_BYTES = 10 * 1024 * 1024  # 10 MiB. Matches the workflow.
SIDECAR_SCHEMA_VERSION = 1
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
    return json.loads(path.read_text(encoding="utf-8"))


def _short_ref(sha: str) -> str:
    return sha[:8]


SUBMITTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def _audit_path(sidecar: dict, archived_at: dt.datetime) -> str:
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
    if not isinstance(value, expected):
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
    required = ("issue_number", "submission_ref", "submission_repo",
                "submission_kind", "submission_public", "submitted_by", "model")
    missing = [key for key in required if key not in metadata]
    if missing:
        sys.exit(f"metadata.json missing required fields: {missing!r}")

    # Strict typing — the sidecar is later read by `push` and (eventually)
    # by people reading the archive; loose-typed fields here silently
    # produce wrong sidecars (e.g. `bool("false") is True`).
    issue_number = _require_type(metadata, "issue_number", int)
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
    if issue_number <= 0:
        sys.exit(f"issue_number must be a positive integer, got {issue_number!r}")

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

    # Sanity check the output before we trust it. age v1 ciphertexts start
    # with `age-encryption.org/v1\n`; reject anything else so a misbehaving
    # binary cannot silently produce a zero-length or plaintext file.
    with ciphertext.open("rb") as fh:
        header = fh.read(32)
    if not header.startswith(b"age-encryption.org/v1\n"):
        ciphertext.unlink(missing_ok=True)
        sys.exit(f"age output does not have the expected v1 header: {header!r}")

    sidecar = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "issue": issue_number,
        "submission_repo": submission_repo,
        "submission_ref": submission_ref,
        "submission_kind": submission_kind,
        "submission_public": submission_public,
        "submitter": submitted_by,
        "model": model,
        "size_bytes_plaintext_tar": size_bytes,
        "sha256_plaintext_tar": plaintext_sha,
    }
    production_description = metadata.get("production_description")
    if production_description:
        if not isinstance(production_description, str):
            sys.exit("metadata.json field 'production_description' must be a string when present")
        sidecar["production_description"] = production_description

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


# ---------------------------------------------------------------------------
# push subcommand
# ---------------------------------------------------------------------------


def _validate_sidecar(sidecar: dict) -> None:
    """Strict schema check on the partial sidecar read by `push`.

    `push` interpolates `submission_ref` into the upload path and trusts
    the rest for the sidecar JSON committed to the audit repo, so a
    malformed sidecar would either produce an unexpected path or commit
    junk metadata. Re-validating here is defense in depth against a
    corrupted artifact or a future caller that bypasses `encrypt`.
    """
    if sidecar.get("schema_version") != SIDECAR_SCHEMA_VERSION:
        sys.exit(
            f"sidecar schema_version must be {SIDECAR_SCHEMA_VERSION}, "
            f"got {sidecar.get('schema_version')!r}"
        )
    issue = sidecar.get("issue")
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        sys.exit(f"sidecar.issue must be a positive integer, got {issue!r}")
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

    base_path = _audit_path(sidecar, archived_at)
    ciphertext_remote = f"{base_path}.tar.age"
    sidecar_remote = f"{base_path}.json"

    sidecar_bytes = (json.dumps(sidecar, indent=2, sort_keys=True) + "\n").encode("utf-8")
    ciphertext_bytes = ciphertext.read_bytes()

    audit_repo = args.audit_repo
    commit_message = (
        f"archive: issue {sidecar['issue']} "
        f"({sidecar['submission_repo']}@{_short_ref(sidecar['submission_ref'])})"
    )

    # Upload ciphertext before sidecar. If the workflow is killed between
    # the two, a rerun on the same submission encounters the existing
    # ciphertext at the predicted path; _put_contents handles that
    # idempotently (returns success if existing bytes match, fails hard
    # if they differ). The sidecar-first ordering would risk publishing
    # an archive entry whose ciphertext was never uploaded — much worse.
    _put_contents(
        audit_repo=audit_repo,
        token=token,
        path=ciphertext_remote,
        content=ciphertext_bytes,
        message=commit_message,
    )
    _put_contents(
        audit_repo=audit_repo,
        token=token,
        path=sidecar_remote,
        content=sidecar_bytes,
        message=commit_message + " (sidecar)",
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
        if exc.code == 404:
            return None
        body = exc.read().decode("utf-8", errors="replace")
        sys.exit(f"Contents API GET {path} failed ({exc.code}):\n{body}")


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    """Parse a Retry-After response header in seconds. None if absent/unusable."""
    ra = exc.headers.get("Retry-After") if exc.headers else None
    if not ra:
        return None
    try:
        return max(0.0, float(ra))
    except ValueError:
        return None


def _put_contents(
    *,
    audit_repo: str,
    token: str,
    path: str,
    content: bytes,
    message: str,
) -> None:
    """Upload a single file to the audit repo via the Contents API.

    Idempotent: if a file already exists at `path` whose Git blob SHA
    matches `_git_blob_sha(content)`, treats the operation as a no-op
    success. If a file exists but its content differs, fails hard —
    that's an operator-investigatable collision, not something we
    silently overwrite. Retries transient transport / 5xx / rate-limit
    failures with exponential backoff and jitter, honoring `Retry-After`
    when present.
    """
    api_url = f"https://api.github.com/repos/{audit_repo}/contents/{path}"
    expected_sha = _git_blob_sha(content)
    body = json.dumps({
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
    }).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(1, PUSH_RETRY_ATTEMPTS + 1):
        req = urllib.request.Request(
            api_url,
            method="PUT",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "lean-eval-archiver",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
            return
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 422 and "already exists" in err_body:
                # Idempotency path: confirm the existing bytes are ours.
                # If they are, this is a workflow rerun after the prior
                # attempt got partway through; treat it as success. If
                # they aren't, two different submissions are racing into
                # the same audit path — fail and let an operator look.
                existing = _api_get(audit_repo=audit_repo, token=token, path=path)
                if existing is None:
                    # The file disappeared between the PUT and the GET.
                    # Retry as a normal transient.
                    last_err = exc
                elif existing.get("sha") == expected_sha:
                    print(
                        f"archive: {path} already exists with matching content; idempotent no-op",
                        file=sys.stderr,
                    )
                    return
                else:
                    sys.exit(
                        f"audit path {path!r} already exists in {audit_repo} "
                        f"with different content (existing sha {existing.get('sha')!r} "
                        f"vs expected {expected_sha!r}). This indicates a colliding "
                        f"archive — investigate before retrying."
                    )
            elif exc.code in (409, 429, 500, 502, 503, 504) and attempt < PUSH_RETRY_ATTEMPTS:
                last_err = exc
            else:
                sys.exit(f"Contents API PUT {path} failed ({exc.code}):\n{err_body}")
            # Backoff: honor Retry-After if present, otherwise exponential
            # (capped) with full jitter. The jitter spreads sibling-job
            # races so they don't synchronize on the next retry slot.
            sleep_s = _retry_after_seconds(exc)
            if sleep_s is None:
                sleep_s = min(30.0, (2.0 ** (attempt - 1))) * random.uniform(0.5, 1.5)
            time.sleep(sleep_s)
            continue
        except urllib.error.URLError as exc:
            if attempt < PUSH_RETRY_ATTEMPTS:
                last_err = exc
                time.sleep(min(30.0, 2.0 ** (attempt - 1)) * random.uniform(0.5, 1.5))
                continue
            sys.exit(f"Contents API PUT {path} transport error: {exc}")
    sys.exit(f"Contents API PUT {path} failed after {PUSH_RETRY_ATTEMPTS} attempts: {last_err}")


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

    p_push = sub.add_parser("push", help="Push ciphertext + sidecar to lean-eval-audit.")
    p_push.add_argument("--ciphertext", type=pathlib.Path, required=True)
    p_push.add_argument("--sidecar", type=pathlib.Path, required=True)
    p_push.add_argument("--summary", type=pathlib.Path, default=None,
                        help="summary.json from evaluate (optional; "
                             "per-problem verdict goes into the sidecar).")
    p_push.add_argument("--benchmark-commit", default="")
    p_push.add_argument("--workflow-run-url", default="")
    p_push.add_argument("--audit-repo", default=DEFAULT_AUDIT_REPO)
    p_push.set_defaults(func=_push)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
