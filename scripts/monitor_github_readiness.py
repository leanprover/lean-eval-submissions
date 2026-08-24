#!/usr/bin/env python3
"""Bind lifecycle health to protected deployment evidence and reconcile its alert."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
BOT_LOGIN = "github-actions[bot]"
ISSUE_MARKER = "<!-- lean-eval-lifecycle-monitor-v1 -->"
ISSUE_TITLE = "[monitor] LeanEval lifecycle readiness failure"
FULL_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
ACTIVE_STATUSES = {"queued", "in_progress", "pending", "requested", "waiting"}
DEPLOY_EVENTS = {"push", "workflow_dispatch"}
MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WORKFLOW_PAGES = 10
MAX_ISSUE_PAGES = 100


class GitHubError(RuntimeError):
    """A bounded GitHub API operation failed without exposing its response body."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        attempts: int = 4,
        timeout: float = 15,
        budget_seconds: float = 240,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not token or any(ord(character) < 33 for character in token):
            raise GitHubError("GitHub token is missing or invalid")
        if (
            not 1 <= attempts <= 6
            or not 1 <= timeout <= 30
            or not 30 <= budget_seconds <= 480
        ):
            raise GitHubError("GitHub retry settings are invalid")
        self.token = token
        self.attempts = attempts
        self.timeout = timeout
        self.sleeper = sleeper
        self.clock = clock
        self.deadline = clock() + budget_seconds
        self.opener = urllib.request.build_opener(_RejectRedirects)

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        retry_safe: bool | None = None,
    ) -> Any:
        if not path.startswith("/"):
            raise GitHubError("GitHub API path is invalid")
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if retry_safe is None:
            retry_safe = method in {"GET", "PATCH"}
        attempts = self.attempts if retry_safe else 1
        for attempt in range(1, attempts + 1):
            remaining = self.deadline - self.clock()
            if remaining <= 0:
                raise GitHubError("GitHub API time budget is exhausted")
            request = urllib.request.Request(
                API_ROOT + path,
                data=body,
                method=method,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "lean-eval-lifecycle-readiness-monitor/1",
                    "X-GitHub-Api-Version": API_VERSION,
                },
            )
            retryable = False
            retry_delay: float | None = None
            try:
                with self.opener.open(
                    request, timeout=min(self.timeout, remaining)
                ) as response:
                    raw = response.read(MAX_API_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_API_RESPONSE_BYTES:
                        raise GitHubError("GitHub API response exceeds its size limit")
                    if not raw:
                        return None
                    return json.loads(raw)
            except urllib.error.HTTPError as error:
                retryable = error.code in {408, 403, 429, 500, 502, 503, 504}
                if error.code in {403, 429}:
                    retry_after = error.headers.get("Retry-After")
                    reset = error.headers.get("X-RateLimit-Reset")
                    if isinstance(retry_after, str) and retry_after.isdigit():
                        retry_delay = max(1, int(retry_after))
                    elif isinstance(reset, str) and reset.isdigit():
                        retry_delay = max(1, int(reset) - int(time.time()) + 1)
                    else:
                        # GitHub directs clients that receive a secondary-limit
                        # response without a header to wait at least one minute.
                        retry_delay = 60
                error.close()
            except (OSError, TimeoutError, urllib.error.URLError):
                retryable = True
            except (UnicodeError, json.JSONDecodeError) as error:
                raise GitHubError("GitHub API returned invalid JSON") from error
            if not retry_safe or not retryable or attempt == attempts:
                raise GitHubError("GitHub API request failed", retryable=retryable)
            delay = retry_delay or min(2 ** (attempt - 1), 8)
            if self.clock() + delay >= self.deadline:
                raise GitHubError("GitHub API time budget is exhausted")
            self.sleeper(delay)
        raise AssertionError("unreachable GitHub retry loop")

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload, retry_safe=False)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("PATCH", path, payload, retry_safe=True)


def _repository(value: str) -> str:
    if REPOSITORY.fullmatch(value) is None:
        raise GitHubError("repository is not canonical")
    return value


def _commit(value: str) -> str:
    if FULL_COMMIT.fullmatch(value) is None:
        raise GitHubError("deployed commit is not canonical")
    return value


def _timestamp(value: Any) -> dt.datetime:
    if not isinstance(value, str):
        raise GitHubError("deployment run timestamp is invalid")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise GitHubError("deployment run timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise GitHubError("deployment run timestamp is not timezone-aware")
    return parsed.astimezone(dt.timezone.utc)


def workflow_runs(client: Any, repository: str) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    encoded = urllib.parse.quote("deploy-worker.yml", safe="")
    exhausted = True
    for page in range(1, MAX_WORKFLOW_PAGES + 1):
        query = urllib.parse.urlencode({"branch": "main", "per_page": 100, "page": page})
        value = client.get(
            f"/repos/{repository}/actions/workflows/{encoded}/runs?{query}"
        )
        if not isinstance(value, dict) or not isinstance(value.get("workflow_runs"), list):
            raise GitHubError("deployment workflow response is invalid")
        page_runs = value["workflow_runs"]
        if not all(isinstance(run, dict) for run in page_runs):
            raise GitHubError("deployment workflow run is invalid")
        runs.extend(page_runs)
        if len(page_runs) < 100:
            exhausted = False
            break
    if exhausted:
        raise GitHubError("deployment workflow history exceeds its page bound")
    return runs


def classify_deployment(
    client: Any,
    repository: str,
    deployed_commit: str,
    *,
    now: dt.datetime,
    max_active_age_seconds: int,
) -> dict[str, Any]:
    repository = _repository(repository)
    deployed_commit = _commit(deployed_commit)
    if not 60 <= max_active_age_seconds <= 7200:
        raise GitHubError("deployment suppression bound is invalid")
    runs = workflow_runs(client, repository)
    relevant = [
        run
        for run in runs
        if run.get("head_branch") == "main" and run.get("event") in DEPLOY_EVENTS
    ]
    successful = [
        run
        for run in relevant
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    active: list[tuple[int, dict[str, Any]]] = []
    for run in relevant:
        if run.get("status") not in ACTIVE_STATUSES:
            continue
        created = _timestamp(run.get("created_at"))
        age = max(0, int((now - created).total_seconds()))
        active.append((age, run))
    reason = "deployment_evidence_missing"
    expected_commit = None
    if active and max(age for age, _ in active) > max_active_age_seconds:
        return {
            "schema_version": 1,
            "status": "failed",
            "reason": "deployment_rollout_stuck",
            "deployed_commit": deployed_commit,
            "expected_commit": None,
            "active_deployment": None,
        }
    deployment_ready = False
    if successful:
        completed: list[tuple[dt.datetime, dict[str, Any]]] = []
        for run in successful:
            completed.append((_timestamp(run.get("updated_at")), run))
        newest_completion = max(completion for completion, _ in completed)
        newest = [run for completion, run in completed if completion == newest_completion]
        if len(newest) != 1:
            raise GitHubError("latest successful deployment is ambiguous")
        expected_commit = newest[0].get("head_sha")
        if not isinstance(expected_commit, str) or FULL_COMMIT.fullmatch(expected_commit) is None:
            raise GitHubError("successful deployment commit is invalid")
        if deployed_commit != expected_commit:
            reason = "deployment_commit_not_latest_success"
        else:
            tag = f"lean-eval-dispatch/{deployed_commit}"
            encoded_tag = urllib.parse.quote(tag, safe="/")
            reference = client.get(f"/repos/{repository}/git/ref/tags/{encoded_tag}")
            if (
                not isinstance(reference, dict)
                or not isinstance(reference.get("object"), dict)
                or reference["object"].get("type") != "commit"
                or reference["object"].get("sha") != deployed_commit
            ):
                reason = "deployment_tag_mismatch"
            else:
                branch = client.get(f"/repos/{repository}/branches/main")
                branch_commit = branch.get("commit") if isinstance(branch, dict) else None
                if (
                    not isinstance(branch, dict)
                    or branch.get("name") != "main"
                    or branch.get("protected") is not True
                    or not isinstance(branch_commit, dict)
                    or not isinstance(branch_commit.get("sha"), str)
                    or FULL_COMMIT.fullmatch(branch_commit["sha"]) is None
                ):
                    reason = "deployment_main_not_protected"
                else:
                    comparison = client.get(
                        f"/repos/{repository}/compare/{deployed_commit}...main"
                    )
                    if (
                        not isinstance(comparison, dict)
                        or comparison.get("status") not in {"ahead", "identical"}
                    ):
                        reason = "deployment_not_on_protected_main"
                    else:
                        deployment_ready = True

    if active:
        oldest_age, oldest = max(active, key=lambda item: item[0])
        run_id = oldest.get("id")
        run_url = oldest.get("html_url")
        expected_url = (
            f"https://github.com/{repository}/actions/runs/{run_id}"
            if type(run_id) is int
            else None
        )
        if type(run_id) is not int or run_url != expected_url:
            raise GitHubError("active deployment identity is invalid")
        return {
            "schema_version": 1,
            "status": "suppressed",
            "reason": "deployment_rollout_active" if deployment_ready else reason,
            "deployed_commit": deployed_commit,
            "expected_commit": expected_commit,
            "active_deployment": {
                "run_id": run_id,
                "run_url": run_url,
                "age_seconds": oldest_age,
                "maximum_age_seconds": max_active_age_seconds,
            },
        }
    if deployment_ready:
        return {
            "schema_version": 1,
            "status": "ready",
            "reason": None,
            "deployed_commit": deployed_commit,
            "expected_commit": expected_commit,
            "active_deployment": None,
        }
    return {
        "schema_version": 1,
        "status": "failed",
        "reason": reason,
        "deployed_commit": deployed_commit,
        "expected_commit": expected_commit,
        "active_deployment": None,
    }


def matching_issues(client: Any, repository: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    exhausted = True
    for page in range(1, MAX_ISSUE_PAGES + 1):
        query = urllib.parse.urlencode(
            {
                "state": "all",
                "creator": BOT_LOGIN,
                "per_page": 100,
                "page": page,
            }
        )
        value = client.get(f"/repos/{repository}/issues?{query}")
        if not isinstance(value, list) or not all(isinstance(issue, dict) for issue in value):
            raise GitHubError("issue listing response is invalid")
        for issue in value:
            user = issue.get("user")
            body = issue.get("body")
            if (
                "pull_request" not in issue
                and issue.get("title") == ISSUE_TITLE
                and isinstance(user, dict)
                and user.get("login") == BOT_LOGIN
                and isinstance(body, str)
                and ISSUE_MARKER in body
            ):
                matches.append(issue)
        if len(value) < 100:
            exhausted = False
            break
    if exhausted:
        raise GitHubError("monitor issue history exceeds its page bound")
    for issue in matches:
        _issue_number(issue)
    return sorted(matches, key=_issue_number)


def _issue_number(issue: dict[str, Any]) -> int:
    number = issue.get("number")
    if type(number) is not int or number <= 0:
        raise GitHubError("monitor issue number is invalid")
    return number


def _ensure_state(
    client: Any, repository: str, issue: dict[str, Any], state: str
) -> None:
    number = _issue_number(issue)
    if issue.get("state") == state:
        return
    payload: dict[str, Any] = {"state": state}
    if state == "closed":
        payload["state_reason"] = "completed"
    client.patch(f"/repos/{repository}/issues/{number}", payload)
    issue["state"] = state


def _comments(client: Any, repository: str, number: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    exhausted = True
    for page in range(1, MAX_ISSUE_PAGES + 1):
        query = urllib.parse.urlencode({"per_page": 100, "page": page})
        value = client.get(f"/repos/{repository}/issues/{number}/comments?{query}")
        if not isinstance(value, list) or not all(isinstance(comment, dict) for comment in value):
            raise GitHubError("issue comment response is invalid")
        result.extend(value)
        if len(value) < 100:
            exhausted = False
            break
    if exhausted:
        raise GitHubError("monitor issue comments exceed their page bound")
    return result


def _ensure_comment(
    client: Any,
    repository: str,
    number: int,
    marker: str,
    body: str,
) -> None:
    def exists() -> bool:
        return any(
            isinstance(comment.get("user"), dict)
            and comment["user"].get("login") == BOT_LOGIN
            and isinstance(comment.get("body"), str)
            and marker in comment["body"]
            for comment in _comments(client, repository, number)
        )

    if exists():
        return
    try:
        client.post(
            f"/repos/{repository}/issues/{number}/comments",
            {"body": f"{marker}\n{body}"},
        )
        return
    except GitHubError as error:
        if not error.retryable:
            raise
        for scan in range(1, 5):
            if exists():
                return
            if scan < 4:
                time.sleep(min(2 ** (scan - 1), 8))
        # Never repeat a response-unknown POST in this run. If visibility is
        # delayed beyond the bound, the next scheduled run reconciles it.
        raise


def _create_issue(
    client: Any, repository: str, run_url: str, owner: str
) -> list[dict[str, Any]]:
    body = "\n".join(
        (
            ISSUE_MARKER,
            "The public Cloudflare lifecycle deployment failed its commit-coherent readiness check.",
            "",
            f"- Failed run: {run_url}",
            f"- Severity owner and emergency intake-pause owner: {owner}",
            "- Support channel: this repository issue",
            "- Immediate response: confirm production intake/replay state, pause intake if it is enabled, preserve evidence, and forward-deploy or complete the reviewed rollback unit.",
            "",
            "No secret values, source bytes, or raw endpoint bodies are included in this alert.",
        )
    )
    try:
        created = client.post(
            f"/repos/{repository}/issues",
            {"title": ISSUE_TITLE, "body": body},
        )
    except GitHubError as error:
        if not error.retryable:
            raise
        for scan in range(1, 5):
            found = matching_issues(client, repository)
            if found:
                return found
            if scan < 4:
                time.sleep(min(2 ** (scan - 1), 8))
        # Never repeat a response-unknown POST in this run. If visibility is
        # delayed beyond the bound, the next scheduled run reconciles it.
        raise
    if (
        isinstance(created, dict)
        and created.get("title") == ISSUE_TITLE
        and created.get("body") == body
        and isinstance(created.get("user"), dict)
        and created["user"].get("login") == BOT_LOGIN
    ):
        _issue_number(created)
        refreshed = matching_issues(client, repository)
        by_number = {_issue_number(issue): issue for issue in refreshed}
        by_number[_issue_number(created)] = created
        return [by_number[number] for number in sorted(by_number)]
    raise GitHubError("created monitor issue response is invalid")


def reconcile_issue(
    client: Any,
    repository: str,
    status: str,
    run_url: str,
    owner: str,
    run_id: str,
) -> dict[str, Any]:
    repository = _repository(repository)
    if status not in {"ready", "failed"}:
        raise GitHubError("only ready or failed monitor states can reconcile alerts")
    run_prefix = f"https://github.com/{repository}/actions/runs/"
    if (
        not run_url.startswith(run_prefix)
        or not run_url.removeprefix(run_prefix).isdigit()
        or run_url.removeprefix(run_prefix) != run_id
    ):
        raise GitHubError("monitor run URL is invalid")
    if re.fullmatch(r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner) is None:
        raise GitHubError("monitor owner is invalid")
    if re.fullmatch(r"[0-9]+", run_id) is None:
        raise GitHubError("monitor run ID is invalid")
    issues = matching_issues(client, repository)
    if status == "failed" and not issues:
        issues = _create_issue(client, repository, run_url, owner)
    if not issues:
        return {"status": status, "canonical_issue": None, "duplicates_closed": 0}
    canonical = issues[0]
    canonical_number = _issue_number(canonical)
    duplicates_closed = 0
    for duplicate in issues[1:]:
        duplicate_number = _issue_number(duplicate)
        if duplicate.get("state") == "open":
            _ensure_comment(
                client,
                repository,
                duplicate_number,
                f"<!-- lean-eval-lifecycle-monitor-duplicate-{canonical_number} -->",
                f"Superseded by canonical monitor issue #{canonical_number}.",
            )
            _ensure_state(client, repository, duplicate, "closed")
            duplicates_closed += 1
    if status == "failed":
        was_closed = canonical.get("state") != "open"
        if was_closed:
            _ensure_comment(
                client,
                repository,
                canonical_number,
                f"<!-- lean-eval-lifecycle-monitor-run-{run_id}-failed -->",
                f"Readiness failed again: {run_url}",
            )
        _ensure_state(client, repository, canonical, "open")
    elif canonical.get("state") == "open":
        _ensure_comment(
            client,
            repository,
            canonical_number,
            f"<!-- lean-eval-lifecycle-monitor-run-{run_id}-recovered -->",
            f"Readiness recovered: {run_url}",
        )
        _ensure_state(client, repository, canonical, "closed")
    return {
        "status": status,
        "canonical_issue": canonical_number,
        "duplicates_closed": duplicates_closed,
    }


def write_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-deployment")
    verify.add_argument("--repository", required=True)
    verify.add_argument("--deployed-commit", required=True)
    verify.add_argument("--max-active-age-seconds", type=int, default=2700)
    verify.add_argument("--output", required=True, type=pathlib.Path)
    reconcile = subparsers.add_parser("reconcile-issue")
    reconcile.add_argument("--repository", required=True)
    reconcile.add_argument("--status", required=True, choices=("ready", "failed"))
    reconcile.add_argument("--run-url", required=True)
    reconcile.add_argument("--run-id", required=True)
    reconcile.add_argument("--owner", default="@kim-em")
    args = parser.parse_args(argv)
    try:
        client = GitHubClient(os.environ.get("GH_TOKEN", ""))
        if args.command == "verify-deployment":
            result = classify_deployment(
                client,
                args.repository,
                args.deployed_commit,
                now=dt.datetime.now(dt.timezone.utc),
                max_active_age_seconds=args.max_active_age_seconds,
            )
            write_exclusive(args.output, result)
        else:
            result = reconcile_issue(
                client,
                args.repository,
                args.status,
                args.run_url,
                args.owner,
                args.run_id,
            )
            print(json.dumps(result, sort_keys=True))
    except (GitHubError, OSError) as error:
        print(f"GitHub readiness monitor: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
