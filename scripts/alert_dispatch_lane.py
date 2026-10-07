#!/usr/bin/env python3
"""Open, update or close the incident issue for server-dispatched evaluation failures.

The issue lane never posts a completion to the Worker, so its runs stay green
while the dispatch lane fails; between 2026-09-30 and 2026-10-07 every
server-dispatched evaluation failed in `evaluation_state` and nobody was
paged. This runs after every completed Submission workflow run that GitHub
reports as a `workflow_dispatch` event (the Worker's lane):

- a failed run opens the alert issue, or appends the run to the open one;
- a successful run closes the open alert issue with the recovering run.

Only `gh` is used, with the workflow's own token. No submission content is
read; the issue carries the run URL, the failed job and step names, and the
owner mention.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

ISSUE_TITLE = "[monitor] LeanEval dispatch-lane evaluation failure"
MAX_RUNS_IN_BODY = 50


def gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def failed_steps(repository: str, run_id: str) -> list[str]:
    jobs = json.loads(gh(["api", f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100"]))
    lines: list[str] = []
    for job in jobs.get("jobs", []):
        if job.get("conclusion") != "failure":
            continue
        steps = [step["name"] for step in job.get("steps", []) if step.get("conclusion") == "failure"]
        lines.append(f"`{job['name']}`: " + (", ".join(f"`{s}`" for s in steps) or "no failed step recorded"))
    return lines


def open_alert(repository: str) -> dict | None:
    issues = json.loads(gh([
        "issue", "list", "--repo", repository, "--state", "open",
        "--search", f'in:title "{ISSUE_TITLE}"', "--json", "number,title,body", "--limit", "20",
    ]))
    for issue in issues:
        if issue["title"] == ISSUE_TITLE:
            return issue
    return None


def run_line(run_url: str, steps: list[str]) -> str:
    detail = "; ".join(steps) if steps else "no failed job recorded"
    return f"- {run_url} ({detail})"


def report_failure(repository: str, run_id: str, run_url: str, owner: str) -> str:
    steps = failed_steps(repository, run_id)
    line = run_line(run_url, steps)
    existing = open_alert(repository)
    if existing is None:
        body = "\n".join([
            f"{owner} server-dispatched evaluations are failing. The issue lane does not post",
            "completions to the Worker, so its runs stay green while this lane is broken.",
            "",
            "Failed runs:",
            line,
            "",
            "This issue closes itself when a server-dispatched run succeeds.",
        ])
        gh(["issue", "create", "--repo", repository, "--title", ISSUE_TITLE, "--body", body])
        return "opened"
    if run_url in existing["body"]:
        return "already_recorded"
    if existing["body"].count("\n- https://") >= MAX_RUNS_IN_BODY:
        gh(["issue", "comment", str(existing["number"]), "--repo", repository, "--body", line])
        return "commented"
    body = existing["body"].replace("\n\nThis issue closes itself", f"\n{line}\n\nThis issue closes itself", 1)
    if body == existing["body"]:
        body = existing["body"].rstrip() + "\n" + line
    gh(["issue", "edit", str(existing["number"]), "--repo", repository, "--body", body])
    return "updated"


def report_success(repository: str, run_url: str) -> str:
    existing = open_alert(repository)
    if existing is None:
        return "nothing_open"
    gh(["issue", "close", str(existing["number"]), "--repo", repository,
        "--comment", f"A server-dispatched evaluation succeeded again: {run_url}"])
    return "closed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--conclusion", required=True)
    parser.add_argument("--owner", default="@kim-em")
    args = parser.parse_args(argv)
    if not args.run_id.isdigit() or not args.run_url.startswith("https://github.com/"):
        print("run id and url are not canonical", file=sys.stderr)
        return 2
    if args.conclusion == "success":
        outcome = report_success(args.repository, args.run_url)
    elif args.conclusion in {"failure", "timed_out"}:
        outcome = report_failure(args.repository, args.run_id, args.run_url, args.owner)
    else:
        outcome = f"ignored_{args.conclusion}"
    print(outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
