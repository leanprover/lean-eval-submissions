#!/usr/bin/env python3
"""Retry missing measurement publication from trusted recorder artifacts only."""

from __future__ import annotations
import argparse
import datetime as dt
import io
import json
import os
import pathlib
import re
import subprocess
import urllib.request
import zipfile
from acceptance_measurement import validate_measurement
from compute_reporting_coverage import coverage, load_records

REPOSITORY = "leanprover/lean-eval-submissions"


def gh(path):
    return subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, timeout=45
    ).stdout


def receipts(archive, run_id, commit):
    if len(archive) > 8 * 1024 * 1024:
        raise ValueError("oversize recorder artifact")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries = zipped.infolist()
        if len(entries) > 256 or sum(x.file_size for x in entries) > 4 * 1024 * 1024:
            raise ValueError("oversize recorder artifact contents")
        for entry in entries:
            if (
                not re.fullmatch(r"r2_[0-9a-f]{64}\.json", entry.filename)
                or entry.file_size > 16384
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("unexpected recorder artifact entry")
            m = validate_measurement(json.loads(zipped.read(entry)))
            if (
                entry.filename != m["result_id"] + ".json"
                or m["workflow_run_id"] != run_id
                or m["workflow_commit"] != commit
            ):
                raise ValueError("recorder artifact identity disagrees")
            yield m


def recovery_runs(report, state, verification_root, since):
    published = {
        (m["result_id"], m["workflow_run_id"])
        for m in state.get("acceptance_measurements", [])
    }
    by_run = {}
    for row in report["results"]:
        if (
            row["status"] == "measured"
            or dt.datetime.fromisoformat(row["accepted_at"].replace("Z", "+00:00"))
            < since
        ):
            continue
        rid = row["result_id"]
        path = verification_root / (rid + ".json")
        if path.exists():
            run_id = json.loads(path.read_text())["run_id"]
            # A published failure is accounted for. Reposting it every cycle
            # would consume the bounded batch and starve older lost receipts.
            if (rid, run_id) not in published:
                by_run.setdefault(run_id, set()).add(rid)
    return by_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=pathlib.Path, required=True)
    parser.add_argument("--public-state", type=pathlib.Path, required=True)
    parser.add_argument("--since", default="2026-10-22T00:00:00Z")
    parser.add_argument(
        "--endpoint",
        default="https://lean-eval-submission-server.lean-eval.workers.dev",
    )
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    if args.endpoint != "https://lean-eval-submission-server.lean-eval.workers.dev":
        raise ValueError("only the production measurement endpoint is allowed")
    if not 1 <= args.limit <= 100:
        raise ValueError("limit must be 1..100")
    since = dt.datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    if since.tzinfo != dt.timezone.utc:
        raise ValueError("since must be UTC")
    state = json.loads(args.public_state.read_text())
    report = coverage(load_records(args.results_root), state)
    by_run = recovery_runs(report, state, args.results_root / "verification", since)
    posted = 0
    unresolved = 0
    for run_id, wanted in sorted(by_run.items(), reverse=True)[: args.limit]:
        run = json.loads(gh(f"repos/{REPOSITORY}/actions/runs/{run_id}"))
        if (
            run["head_repository"]["full_name"] != REPOSITORY
            or run["path"] != ".github/workflows/submission.yml"
            or run["event"] not in ("issues", "workflow_dispatch")
        ):
            raise ValueError("measurement workflow authority disagrees")
        artifacts = json.loads(
            gh(f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100")
        )["artifacts"]
        selected = [
            a
            for a in artifacts
            if a["name"] == "acceptance-measurements" and not a["expired"]
        ]
        if not selected:
            unresolved += len(wanted)
            continue
        if len(selected) != 1:
            raise ValueError("ambiguous recorder artifacts")
        for m in receipts(
            gh(f"repos/{REPOSITORY}/actions/artifacts/{selected[0]['id']}/zip"),
            run_id,
            run["head_sha"],
        ):
            if m["result_id"] not in wanted:
                continue
            request = urllib.request.Request(
                args.endpoint + "/api/v1/results/" + m["result_id"] + "/measurements",
                data=json.dumps(m).encode(),
                headers={
                    "Authorization": "Bearer " + os.environ["LIFECYCLE_CALLBACK_TOKEN"],
                    "Content-Type": "application/json",
                },
                method="POST",
            )

            # The endpoint is fixed and accepts no redirects to another host.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *_args, **_kwargs):
                    return None

            with urllib.request.build_opener(NoRedirect).open(
                request, timeout=30
            ) as response:
                outcome = json.load(response)
            if outcome["status"] not in ("recorded", "already_recorded"):
                raise ValueError("measurement publication did not acknowledge")
            posted += 1
    print(
        json.dumps(
            {
                "retried": posted,
                "missing_artifacts": unresolved,
                "remaining_runs": max(0, len(by_run) - args.limit),
            },
            sort_keys=True,
        )
    )
    if unresolved:
        print(
            "::warning::Some accepted results lack retained measurement receipts; include them in historical backfill."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
