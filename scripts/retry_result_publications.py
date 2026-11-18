#!/usr/bin/env python3
"""Retry completed submission publication jobs without reevaluating proofs.

The hourly reconciler scans up to 1,000 runs from the last seven days. Artifacts
remain in Actions; no callback credential or submission source is read here.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from collections.abc import Callable
from typing import Any

REPOSITORY = "leanprover/lean-eval-submissions"
PUBLICATION_JOBS = {"measurement_state", "leaderboard_refresh"}
MAX_PAGES = 10
MAX_RERUNS = 25
MAX_ATTEMPTS = 168


def gh_api(path: str, method: str = "GET") -> Any:
    result = subprocess.run(
        ["gh", "api", "--method", method, path],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"GitHub publication retry API failed ({method})")
    return json.loads(result.stdout) if result.stdout.strip() else None


def positive_id(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("GitHub returned an invalid run or job ID")
    return value


def reconcile(
    repository: str,
    now: dt.datetime,
    api: Callable[..., Any] = gh_api,
) -> list[int]:
    if repository != REPOSITORY:
        raise ValueError("publication retries require the upstream submissions repository")
    cutoff = (now - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    retried: list[int] = []
    for page in range(1, MAX_PAGES + 1):
        response = api(
            f"repos/{repository}/actions/workflows/submission.yml/runs"
            f"?created=%3E%3D{cutoff}&per_page=100&page={page}"
        )
        if response["total_count"] > MAX_PAGES * 100:
            raise RuntimeError("publication retry scan exceeds 1,000 runs; operator recovery required")
        runs = response["workflow_runs"]
        for run in runs:
            if (run["status"] != "completed" or
                run["event"] not in {"issues", "workflow_dispatch"} or
                run["path"].split("@", 1)[0] != ".github/workflows/submission.yml"):
                continue
            run_id = positive_id(run["id"])
            jobs = api(f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
            if jobs["total_count"] > 100:
                raise RuntimeError("unexpected submission job count; refusing a partial retry scan")
            for job in jobs["jobs"]:
                if (job["name"] not in PUBLICATION_JOBS or
                    job["status"] != "completed" or
                    job["conclusion"] not in {"failure", "timed_out"}):
                    continue
                if run["run_attempt"] >= MAX_ATTEMPTS:
                    raise RuntimeError(f"publication run {run_id} exhausted its retry budget")
                job_id = positive_id(job["id"])
                api(f"repos/{repository}/actions/jobs/{job_id}/rerun", "POST")
                retried.append(job_id)
                print(f"Retrying publication job {job_id} in run {run_id}")
                # Rerunning one job makes the run active. Pick up another
                # failed publication job on the next hourly scan.
                break
            if len(retried) >= MAX_RERUNS:
                return retried
        if len(runs) < 100:
            break
    return retried


if __name__ == "__main__":
    reconcile(os.environ["REPO"], dt.datetime.now(dt.timezone.utc))
