#!/usr/bin/env python3
"""
Backfill the lean-eval-audit archive from the existing leaderboard.

For each `(issue, submission_repo, submission_ref)` triple recorded in
`results/*.json`, this script:

1. Skips it if an audit entry for that issue already exists in
   `leanprover/lean-eval-audit` (checks via the Git Trees API).
2. Otherwise: re-fetches the submission source via the `lean-eval-bot`
   App installation token, encrypts the source.tar.gz with `age` against
   `.audit/recipients.txt`, and uploads ciphertext + sidecar to the audit
   repo via the same `archive_submission.py push` path the live workflow
   uses.

Authentication:
  $LEAN_EVAL_BOT_APP_ID       App ID of lean-eval-bot.
  $LEAN_EVAL_BOT_PRIVATE_KEY  PEM contents of a lean-eval-bot private key.
  $LEAN_EVAL_ARCHIVER_APP_ID       App ID of lean-eval-archiver.
  $LEAN_EVAL_ARCHIVER_PRIVATE_KEY  PEM contents of a lean-eval-archiver private key.

Note: this is a one-shot operator script. It is idempotent — entries
that already exist in the audit repo are skipped, not re-uploaded —
so a re-run after a partial failure is safe.

Best-effort on the read side: if a submitter has deleted their repo or
the lean-eval-bot App has been uninstalled, that entry is logged as
"unreachable" and skipped; the rest of the backfill continues.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

import jwt  # PyJWT


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import fetch_submission as fetch_mod  # noqa: E402
import archive_submission as arch  # noqa: E402


SUBMISSIONS_REPO = "leanprover/lean-eval-submissions"
AUDIT_REPO = "leanprover/lean-eval-audit"
RECIPIENTS_PATH = REPO_ROOT / ".audit" / "recipients.txt"
JWT_EXPIRY_SECONDS = 540  # under the 10-minute App-JWT cap.


def _mint_jwt(*, app_id: str, key_pem: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 30, "exp": now + JWT_EXPIRY_SECONDS, "iss": str(app_id)},
        key_pem,
        algorithm="RS256",
    )


def _api(*, url: str, token: str, method: str = "GET",
         body: dict | None = None, accept: str = "application/vnd.github+json") -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, method=method, data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "lean-eval-backfill",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    if not raw:
        return {}
    return json.loads(raw)


def _mint_installation_token(*, app_jwt: str, installation_id: int,
                             repositories: list[str] | None = None,
                             permissions: dict[str, str] | None = None) -> str:
    body: dict = {}
    if repositories is not None:
        body["repositories"] = repositories
    if permissions is not None:
        body["permissions"] = permissions
    resp = _api(
        url=f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        token=app_jwt, method="POST", body=body or None,
    )
    return resp["token"]


def _find_installation_for_account(*, app_jwt: str, account: str) -> int | None:
    """Resolve the App's installation id for a given account login (case-insensitive)."""
    # /users/{username}/installation and /orgs/{org}/installation both work
    # for the right account type, but we don't know which without an extra
    # lookup. /app/installations enumerates everything the App is installed
    # on, which is what we want.
    cursor: str | None = None
    while True:
        url = "https://api.github.com/app/installations?per_page=100"
        if cursor:
            url += f"&page={cursor}"
        try:
            installations = _api(url=url, token=app_jwt)
        except urllib.error.HTTPError as exc:
            sys.exit(f"failed to list App installations: HTTP {exc.code}\n{exc.read().decode('utf-8', errors='replace')}")
        if not isinstance(installations, list):
            sys.exit(f"unexpected response shape from /app/installations: {type(installations).__name__}")
        for inst in installations:
            if inst.get("account", {}).get("login", "").lower() == account.lower():
                return int(inst["id"])
        # No pagination cursor needed; if /app/installations returns < 100
        # we're done. If > 100, GitHub's Link header would tell us, but the
        # archive currently has fewer than that.
        if len(installations) < 100:
            return None
        cursor = (int(cursor) + 1) if cursor else 2


def _list_existing_audit_entries(*, archiver_token: str) -> set[tuple[str, int, str]]:
    """Return the set of `(submitter, issue, ref8)` triples already archived."""
    # Trees API gives us the full file list cheaply (one request) up to
    # 100k entries. We extract every
    # `audit/YYYY/MM/{submitter}-{issue}-{ref8}.tar.age` filename and
    # remember the triple.
    branch = _api(
        url=f"https://api.github.com/repos/{AUDIT_REPO}/branches/main",
        token=archiver_token,
    )
    tree_sha = branch["commit"]["commit"]["tree"]["sha"]
    tree = _api(
        url=f"https://api.github.com/repos/{AUDIT_REPO}/git/trees/{tree_sha}?recursive=1",
        token=archiver_token,
    )
    if tree.get("truncated"):
        sys.exit("audit repo tree response is truncated; this script needs paging")
    existing: set[tuple[str, int, str]] = set()
    for entry in tree.get("tree") or []:
        path = entry.get("path") or ""
        if not path.startswith("audit/") or not path.endswith(".tar.age"):
            continue
        stem = pathlib.PurePosixPath(path).stem
        if stem.endswith(".tar"):
            stem = stem[: -len(".tar")]
        # Path is {submitter}-{issue}-{ref8}; submitter and ref8 are
        # alphanumeric, issue is digits. Split from the right.
        parts = stem.rsplit("-", 2)
        if len(parts) != 3:
            continue
        submitter, issue_str, ref8 = parts
        try:
            existing.add((submitter, int(issue_str), ref8))
        except ValueError:
            continue
    return existing


def _collect_submissions(results_dir: pathlib.Path) -> list[dict]:
    """Build a deduplicated list of (issue, repo, ref, ...) records.

    Multiple problems within one submission share an issue number, repo,
    and ref. We dedupe on (issue_number, submission_repo, submission_ref)
    — NOT just issue_number — because records pre-dating this repo's
    pipeline carry over their original `leanprover/lean-eval` issue
    numbers (see README's `issue_number` provenance note), so the same
    integer can refer to two unrelated submissions.

    The audit path embeds {issue}-{ref8}, so collisions in the audit
    repo are still possible if two old-style records share an issue
    AND the first 8 hex chars of their refs — vanishingly unlikely but
    we'd notice via the push-side mismatch check rather than silently
    overwriting.
    """
    seen: dict[tuple[int, str, str], dict] = {}
    for path in sorted(results_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        user = data["user"]
        for model, problems in (data.get("solved") or {}).items():
            for problem_id, record in (problems or {}).items():
                issue = int(record["issue_number"])
                key = (issue, record["submission_repo"], record["submission_ref"])
                entry = seen.setdefault(key, {
                    "issue_number": issue,
                    "submission_repo": record["submission_repo"],
                    "submission_ref": record["submission_ref"],
                    "submission_kind": record["submission_kind"],
                    "submission_public": bool(record["submission_public"]),
                    "submitter": user,
                    "model": model,
                    "production_description": record.get("production_description"),
                    "benchmark_commit": record["benchmark_commit"],
                    "problem_ids": [],
                })
                entry["problem_ids"].append(problem_id)
    return sorted(seen.values(), key=lambda e: (e["issue_number"], e["submission_repo"]))


def _backfill_one(
    *,
    submission: dict,
    bot_jwt: str,
    archiver_token: str,
    recipients: pathlib.Path,
    workdir: pathlib.Path,
) -> str:
    """Returns one of: 'archived', 'unreachable', 'failed'."""
    issue = submission["issue_number"]
    repo = submission["submission_repo"]
    ref = submission["submission_ref"]

    descriptor = fetch_mod.SourceDescriptor(
        kind=submission["submission_kind"],
        owner=repo.split("/", 1)[0],
        name=repo.split("/", 1)[1],
        ref=ref,
    )

    # Mint a fresh installation token scoped to the submission's owner.
    # If the bot isn't installed on this account anymore, mark unreachable.
    bot_token: str | None = None
    if not submission["submission_public"] and descriptor.kind == "github_repo":
        installation_id = _find_installation_for_account(
            app_jwt=bot_jwt, account=descriptor.owner,
        )
        if installation_id is None:
            print(f"  issue {issue}: lean-eval-bot not installed on {descriptor.owner}; unreachable")
            return "unreachable"
        try:
            bot_token = _mint_installation_token(
                app_jwt=bot_jwt, installation_id=installation_id,
                repositories=[descriptor.name],
                permissions={"contents": "read", "metadata": "read"},
            )
        except urllib.error.HTTPError as exc:
            print(f"  issue {issue}: could not mint bot token for {descriptor.owner}: {exc.code}")
            return "unreachable"

    clone_url = fetch_mod.clone_url_for(descriptor, bot_token)

    # Re-fetch at the recorded ref. Failures (missing repo, missing ref)
    # are logged as 'unreachable' so the backfill continues.
    src_dir = workdir / "src"
    if src_dir.exists():
        shutil.rmtree(src_dir)
    try:
        fetch_mod.clone_at_sha(clone_url, ref, src_dir)
        shutil.rmtree(src_dir / ".git", ignore_errors=True)
        fetch_mod.guard_no_path_escape(src_dir)
    except fetch_mod.FetchError as exc:
        print(f"  issue {issue}: fetch failed: {exc}")
        return "unreachable"

    source_tar = workdir / "source.tar.gz"
    fetch_mod.tar_source(src_dir, source_tar)
    if source_tar.stat().st_size > arch.SIZE_CAP_BYTES:
        print(f"  issue {issue}: source is {source_tar.stat().st_size} bytes, over the 10 MiB cap; SKIPPED")
        return "failed"

    # Build the metadata.json shape that archive_submission.py encrypt expects.
    metadata = {
        "issue_number": issue,
        "submission_ref": ref,
        "submission_repo": repo,
        "submission_kind": submission["submission_kind"],
        "submission_public": submission["submission_public"],
        "submitted_by": submission["submitter"],
        "model": submission["model"],
    }
    if submission.get("production_description"):
        metadata["production_description"] = submission["production_description"]
    metadata_path = workdir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True))

    # Encrypt.
    encrypt_out = workdir / "encrypt-out"
    if encrypt_out.exists():
        shutil.rmtree(encrypt_out)
    rc = arch.main([
        "encrypt",
        "--source-tar", str(source_tar),
        "--metadata", str(metadata_path),
        "--recipients", str(recipients),
        "--output-dir", str(encrypt_out),
    ])
    if rc != 0:
        return "failed"

    # Push via the archive subcommand. This honours the same
    # idempotency / mismatch semantics as the live workflow.
    os.environ["ARCHIVER_TOKEN"] = archiver_token
    rc = arch.main([
        "push",
        "--ciphertext", str(encrypt_out / "source.tar.gz.age"),
        "--sidecar", str(encrypt_out / "sidecar.partial.json"),
        "--benchmark-commit", submission["benchmark_commit"],
        "--workflow-run-url", "https://github.com/leanprover/lean-eval-submissions/blob/main/scripts/backfill_audit.py",
    ])
    if rc != 0:
        return "failed"
    return "archived"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=pathlib.Path,
                        default=REPO_ROOT / "results",
                        help="Directory holding the per-submitter results JSONs.")
    parser.add_argument("--recipients", type=pathlib.Path, default=RECIPIENTS_PATH)
    parser.add_argument("--summary-out", type=pathlib.Path, default=None,
                        help="If set, write a per-issue JSON summary of the backfill.")
    parser.add_argument("--limit", type=int, default=None,
                        help="If set, stop after this many unique submissions (testing/staging).")
    args = parser.parse_args(argv)

    bot_app_id = os.environ.get("LEAN_EVAL_BOT_APP_ID") or ""
    bot_key = os.environ.get("LEAN_EVAL_BOT_PRIVATE_KEY") or ""
    arch_app_id = os.environ.get("LEAN_EVAL_ARCHIVER_APP_ID") or ""
    arch_key = os.environ.get("LEAN_EVAL_ARCHIVER_PRIVATE_KEY") or ""
    missing = [k for k, v in {
        "LEAN_EVAL_BOT_APP_ID": bot_app_id,
        "LEAN_EVAL_BOT_PRIVATE_KEY": bot_key,
        "LEAN_EVAL_ARCHIVER_APP_ID": arch_app_id,
        "LEAN_EVAL_ARCHIVER_PRIVATE_KEY": arch_key,
    }.items() if not v]
    if missing:
        sys.exit(f"missing env vars: {missing}")

    bot_jwt = _mint_jwt(app_id=bot_app_id, key_pem=bot_key)
    arch_jwt = _mint_jwt(app_id=arch_app_id, key_pem=arch_key)
    archiver_installation_id = _find_installation_for_account(
        app_jwt=arch_jwt, account="leanprover",
    )
    if archiver_installation_id is None:
        sys.exit("lean-eval-archiver App is not installed on the leanprover org")
    archiver_token = _mint_installation_token(
        app_jwt=arch_jwt, installation_id=archiver_installation_id,
        repositories=["lean-eval-audit"],
        permissions={"contents": "write", "metadata": "read"},
    )

    print("listing existing audit entries...")
    already_archived = _list_existing_audit_entries(archiver_token=archiver_token)
    print(f"  {len(already_archived)} entries already archived")

    submissions = _collect_submissions(args.results_dir)
    print(f"found {len(submissions)} unique submissions in {args.results_dir}")

    summary = {"archived": [], "skipped_already": [], "unreachable": [], "failed": []}
    with tempfile.TemporaryDirectory(prefix="lean-eval-backfill-") as td:
        workdir = pathlib.Path(td)
        processed = 0
        for s in submissions:
            if args.limit is not None and processed >= args.limit:
                break
            key = (s["submitter"], s["issue_number"], s["submission_ref"][:8])
            label = f"{s['submitter']}/{s['issue_number']}/{s['submission_ref'][:8]}"
            if key in already_archived:
                summary["skipped_already"].append(label)
                continue
            processed += 1
            print(f"[{processed}] {label}: {s['submission_repo']}@{s['submission_ref'][:8]} "
                  f"({'public' if s['submission_public'] else 'private'}, model={s['model']!r})")
            outcome = _backfill_one(
                submission=s,
                bot_jwt=bot_jwt,
                archiver_token=archiver_token,
                recipients=args.recipients,
                workdir=workdir,
            )
            summary.setdefault(outcome, []).append(label)
            # Be polite to the API; rate-limiting will throttle us anyway.
            time.sleep(0.5)

    print()
    print("=== backfill summary ===")
    for key in ("archived", "skipped_already", "unreachable", "failed"):
        ids = summary.get(key) or []
        print(f"  {key:18s}: {len(ids):4d}    {ids if len(ids) < 30 else f'(first 30: {ids[:30]})'}")
    if args.summary_out:
        args.summary_out.write_text(
            json.dumps({
                "archived_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "results": summary,
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0 if not summary.get("failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
