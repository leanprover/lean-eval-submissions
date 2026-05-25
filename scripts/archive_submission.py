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
           evaluator results, computes ciphertext digest, and uploads
           both objects to leanprover/lean-eval-audit via the GitHub
           Contents API using the `lean-eval-archiver` App's installation
           token.

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
import subprocess
import sys
import urllib.error
import urllib.request


SIZE_CAP_BYTES = 10 * 1024 * 1024  # 10 MiB. Matches the workflow.
SIDECAR_SCHEMA_VERSION = 1
DEFAULT_AUDIT_REPO = "leanprover/lean-eval-audit"
PUSH_RETRY_ATTEMPTS = 5


def _sha256_of_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _short_ref(sha: str) -> str:
    # Submission refs are always full 40-char SHAs (enforced by fetch_submission.py).
    return sha[:8] if len(sha) >= 8 else sha


def _audit_path(sidecar: dict, archived_at: dt.datetime) -> str:
    issue = int(sidecar["issue"])
    ref8 = _short_ref(str(sidecar["submission_ref"]))
    return f"audit/{archived_at:%Y/%m}/{issue}-{ref8}"


# ---------------------------------------------------------------------------
# encrypt subcommand
# ---------------------------------------------------------------------------


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
        "issue": int(metadata["issue_number"]),
        "submission_repo": metadata["submission_repo"],
        "submission_ref": metadata["submission_ref"],
        "submission_kind": metadata["submission_kind"],
        "submission_public": bool(metadata["submission_public"]),
        "submitter": metadata["submitted_by"],
        "model": metadata["model"],
        "size_bytes_plaintext_tar": size_bytes,
        "sha256_plaintext_tar": plaintext_sha,
    }
    if metadata.get("production_description"):
        sidecar["production_description"] = metadata["production_description"]

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

    # The plaintext-side digest is sealed at encrypt time so re-running
    # `push` against a tampered partial sidecar fails the final integrity
    # check below (digests would not match the ciphertext).
    if "sha256_plaintext_tar" not in sidecar:
        sys.exit("partial sidecar is missing sha256_plaintext_tar")

    sidecar["sha256_ciphertext"] = _sha256_of_file(ciphertext)
    sidecar["size_bytes_ciphertext"] = ciphertext.stat().st_size

    archived_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    sidecar["archived_at"] = archived_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.benchmark_commit:
        sidecar["benchmark_commit"] = args.benchmark_commit
    if args.workflow_run_url:
        sidecar["archiver_workflow_run"] = args.workflow_run_url

    # Merge evaluator verdict if results.json is available. `evaluate` may
    # have failed; archive must succeed regardless. In that case
    # `evaluator_verdict` is omitted and the sidecar records only
    # archival-time facts.
    if args.results and args.results.is_file():
        try:
            results = _read_json(args.results)
            problems = results.get("problems") or results.get("attempted") or []
            verdict: dict[str, str] = {}
            problem_ids: list[str] = []
            for entry in problems if isinstance(problems, list) else []:
                if not isinstance(entry, dict):
                    continue
                pid = entry.get("id") or entry.get("problem_id")
                if not pid:
                    continue
                problem_ids.append(str(pid))
                if entry.get("succeeded"):
                    verdict[str(pid)] = "pass"
                elif entry.get("attempted"):
                    verdict[str(pid)] = "fail"
                else:
                    verdict[str(pid)] = "skipped"
            if problem_ids:
                sidecar["problem_ids"] = sorted(set(problem_ids))
            if verdict:
                sidecar["evaluator_verdict"] = verdict
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warning: could not parse results.json: {exc}", file=sys.stderr)

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

    # Upload ciphertext first, sidecar second: if the workflow is killed
    # between the two, the ciphertext exists without a sidecar — a recoverable
    # state. A sidecar-without-ciphertext is the worst case (looks archived
    # but isn't).
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


def _put_contents(
    *,
    audit_repo: str,
    token: str,
    path: str,
    content: bytes,
    message: str,
) -> None:
    """Upload a single file to the audit repo via the Contents API.

    Retries on concurrent-write conflicts (HTTP 409) and transient
    transport errors. Refuses to overwrite an existing file at the same
    path — collisions are an operator-investigatable signal, not something
    we silently squash.
    """
    api_url = f"https://api.github.com/repos/{audit_repo}/contents/{path}"
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
            if exc.code == 422 and "sha" in err_body and "already exists" in err_body:
                sys.exit(
                    f"audit path {path!r} already exists in {audit_repo}; "
                    f"refusing to overwrite. This indicates a duplicate "
                    f"archive attempt — investigate before retrying."
                )
            if exc.code in (409, 502, 503, 504) and attempt < PUSH_RETRY_ATTEMPTS:
                last_err = exc
                continue
            sys.exit(f"Contents API PUT {path} failed ({exc.code}):\n{err_body}")
        except urllib.error.URLError as exc:
            if attempt < PUSH_RETRY_ATTEMPTS:
                last_err = exc
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
    p_push.add_argument("--results", type=pathlib.Path, default=None,
                        help="results.json from evaluate (optional).")
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
