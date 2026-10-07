#!/usr/bin/env python3
"""Keep one incident issue in step with the server-dispatched evaluation lane.

The issue lane never posts a completion to the Worker, so its runs stay green
while the dispatch lane fails; between 2026-09-30 and 2026-10-07 every
server-dispatched evaluation failed in `evaluation_state` and nobody was
paged. The listener workflow runs after each completed Submission run that
GitHub reports as `workflow_dispatch`, but GitHub's concurrency queue can drop
events, so each run reconciles against the authoritative recent history
instead of trusting the single triggering event:

- the newest completed server-dispatched run decides whether the lane is
  failing;
- a failing lane keeps exactly one bot-owned, marker-tagged incident issue
  open, listing every failed run newer than the last success, each once;
- a healthy lane closes any open incident with the recovering run.

Only `gh` is used, with the workflow's own token, and no submission content
is read: the issue carries run URLs, failed job and step names, and the owner
mention.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

ISSUE_TITLE = "[monitor] LeanEval dispatch-lane evaluation failure"
ISSUE_MARKER = "<!-- lean-eval-dispatch-lane-alert -->"
BOT_LOGIN = "github-actions[bot]"
WORKFLOW = "submission.yml"
RECENT_RUNS = 30
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def run_marker(run_id: int, attempt: int) -> str:
    return f"<!-- dispatch-lane-run:{run_id}:{attempt} -->"


def run_url(repository: str, run_id: int, attempt: int) -> str:
    return f"https://github.com/{repository}/actions/runs/{run_id}/attempts/{attempt}"


def recent_dispatch_runs(repository: str) -> list[dict]:
    """Completed server-dispatched runs, newest first, as the lane's authoritative history."""
    document = json.loads(gh([
        "api", f"repos/{repository}/actions/workflows/{WORKFLOW}/runs"
        f"?event=workflow_dispatch&status=completed&per_page={RECENT_RUNS}",
    ]))
    runs = [
        {"id": int(run["id"]), "attempt": int(run.get("run_attempt") or 1),
         "conclusion": run.get("conclusion"), "started": run.get("run_started_at") or ""}
        for run in document.get("workflow_runs", [])
    ]
    runs.sort(key=lambda run: (run["started"], run["id"]), reverse=True)
    return runs


def failed_steps(repository: str, run_id: int, attempt: int) -> str:
    """Best-effort job and step names for the issue text; never blocks the alert."""
    try:
        jobs = json.loads(gh([
            "api", f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100",
        ]))
    except (subprocess.CalledProcessError, ValueError):
        return "job details unavailable"
    lines: list[str] = []
    for job in jobs.get("jobs", []):
        if job.get("conclusion") not in {"failure", "timed_out"}:
            continue
        steps = [str(step["name"]) for step in job.get("steps", []) if step.get("conclusion") in {"failure", "timed_out"}]
        detail = ", ".join(f"`{s}`" for s in steps) or job.get("conclusion", "failed")
        lines.append(f"`{job['name']}`: {detail}")
    return "; ".join(lines) or "no failed job recorded"


def open_incidents(repository: str) -> list[dict]:
    """Open issues this script owns: bot-authored, exact title, body marker, not a PR."""
    issues = json.loads(gh([
        "api", "--paginate", "--slurp", f"repos/{repository}/issues?state=open&creator={BOT_LOGIN}&per_page=100",
    ]))
    owned = [
        issue for page in issues for issue in page
        if "pull_request" not in issue
        and issue.get("title") == ISSUE_TITLE
        and ISSUE_MARKER in (issue.get("body") or "")
        and (issue.get("user") or {}).get("login") == BOT_LOGIN
    ]
    owned.sort(key=lambda issue: int(issue["number"]))
    return owned


def recorded_markers(repository: str, issue: dict) -> set[str]:
    comments = json.loads(gh([
        "api", "--paginate", "--slurp", f"repos/{repository}/issues/{issue['number']}/comments?per_page=100",
    ]))
    text = [issue.get("body") or ""]
    text.extend(
        comment.get("body") or "" for page in comments for comment in page
        if (comment.get("user") or {}).get("login") == BOT_LOGIN
    )
    return set(re.findall(r"<!-- dispatch-lane-run:[0-9]+:[0-9]+ -->", "\n".join(text)))


def failure_line(repository: str, run: dict) -> str:
    detail = failed_steps(repository, run["id"], run["attempt"])
    return f"{run_marker(run['id'], run['attempt'])}\n- {run_url(repository, run['id'], run['attempt'])} ({detail})"


def reconcile(repository: str, owner: str) -> str:
    runs = recent_dispatch_runs(repository)
    if not runs:
        return "no_dispatch_runs"
    latest = runs[0]
    incidents = open_incidents(repository)
    if latest["conclusion"] == "success":
        for issue in incidents:
            gh(["issue", "close", str(issue["number"]), "--repo", repository,
                "--comment", f"{run_marker(latest['id'], latest['attempt'])}\nA server-dispatched evaluation "
                             f"succeeded again: {run_url(repository, latest['id'], latest['attempt'])}"])
        return "closed" if incidents else "healthy"
    failing: list[dict] = []
    for run in runs:
        if run["conclusion"] == "success":
            break
        if run["conclusion"] in {"failure", "timed_out"}:
            failing.append(run)
    if not failing:
        return f"latest_{latest['conclusion']}"
    if not incidents:
        body = "\n".join([
            ISSUE_MARKER,
            f"{owner} server-dispatched evaluations are failing. The issue lane does not post",
            "completions to the Worker, so its runs stay green while this lane is broken.",
            "This issue closes itself when a server-dispatched run succeeds.",
            "",
            "Failed runs:",
            *(failure_line(repository, run) for run in failing),
        ])
        gh(["issue", "create", "--repo", repository, "--title", ISSUE_TITLE, "--body", body])
        return "opened"
    canonical, *duplicates = incidents
    for issue in duplicates:
        gh(["issue", "close", str(issue["number"]), "--repo", repository,
            "--comment", f"Duplicate of #{canonical['number']}."])
    recorded = recorded_markers(repository, canonical)
    added = 0
    for run in failing:
        if run_marker(run["id"], run["attempt"]) in recorded:
            continue
        gh(["issue", "comment", str(canonical["number"]), "--repo", repository,
            "--body", failure_line(repository, run)])
        added += 1
    return f"recorded_{added}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--owner", default="@kim-em")
    args = parser.parse_args(argv)
    if REPOSITORY.fullmatch(args.repository) is None:
        print("repository is not canonical", file=sys.stderr)
        return 2
    print(reconcile(args.repository, args.owner))
    return 0


if __name__ == "__main__":
    sys.exit(main())
