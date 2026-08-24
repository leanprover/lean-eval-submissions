#!/usr/bin/env python3
"""Resolve historical public replay requests from bounded public GitHub evidence."""

from __future__ import annotations

import argparse
import base64
import collections
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fetch_submission import FetchError, parse_issue_body, parse_source_url


COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_ID = re.compile(r"prr_[0-9a-f]{64}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PROBLEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
PASS_LINE = re.compile(r"^- `([^`]+)`: pass\s*$", re.MULTILINE)
API_ROOT = "https://api.github.com"
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PAGES = 10
MAX_WORKFLOW_BYTES = 512 * 1024
MAX_REPORTED_PASSES = 4096
COMMENT_ACCEPTANCE_LAG = datetime.timedelta(seconds=10)
RUN_COMPLETION_LAG = datetime.timedelta(minutes=5)
CANDIDATE_REPOSITORIES = [
    "leanprover/lean-eval",
    "leanprover/lean-eval-submissions",
]
SPLIT_WORKFLOW_CONTRACT = "split_repository_recorded_benchmark_v1"


class EvidenceError(ValueError):
    """Evidence is malformed, ambiguous, or unavailable."""


class ResponseTooLarge(EvidenceError):
    """One public source probe cannot be processed within its byte cap."""


class ProbeIndeterminate(EvidenceError):
    """A GitHub probe failed without proving that evidence is absent."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class IntegrityError(EvidenceError):
    """Reviewed local authority conflicts with the fetched public bytes."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward the repository token across an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_workflow_registry(value: Any) -> dict[str, dict[str, str]]:
    expected = {
        "schema_version",
        "kind",
        "repository",
        "workflow_path",
        "contracts",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise EvidenceError("workflow definition registry fields are not closed")
    if (
        value["schema_version"] != 1
        or value["kind"]
        != "historical_public_replay_workflow_definition_registry"
        or value["repository"] != "leanprover/lean-eval-submissions"
        or value["workflow_path"] != ".github/workflows/submission.yml"
        or not isinstance(value["contracts"], list)
    ):
        raise EvidenceError("workflow definition registry identity is invalid")
    result: dict[str, dict[str, str]] = {}
    ordered: list[str] = []
    for entry in value["contracts"]:
        if not isinstance(entry, dict) or set(entry) != {
            "evaluator_commit",
            "definition_sha256",
            "contract",
        }:
            raise EvidenceError("workflow definition registry entry is not closed")
        commit = entry["evaluator_commit"]
        digest = entry["definition_sha256"]
        if (
            not isinstance(commit, str)
            or COMMIT.fullmatch(commit) is None
            or not isinstance(digest, str)
            or DIGEST.fullmatch(digest) is None
            or entry["contract"] != SPLIT_WORKFLOW_CONTRACT
            or commit in result
        ):
            raise EvidenceError("workflow definition registry entry is invalid")
        result[commit] = entry
        ordered.append(commit)
    if ordered != sorted(ordered):
        raise EvidenceError("workflow definition registry is not sorted")
    return result


def timestamp(value: Any, field: str) -> datetime.datetime:
    if not isinstance(value, str) or TIMESTAMP.fullmatch(value) is None:
        raise EvidenceError(f"{field} is not a timestamp")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceError(f"{field} is not a timestamp") from error
    if parsed.tzinfo != datetime.timezone.utc:
        raise EvidenceError(f"{field} is not a UTC timestamp")
    return parsed


class GitHubClient:
    def __init__(self, token: str) -> None:
        if not token or token != token.strip() or any(ord(char) < 32 for char in token):
            raise EvidenceError("GITHUB_TOKEN is required")
        self.token = token
        self._opener = urllib.request.build_opener(_RejectRedirects())
        self._get_cache: dict[str, tuple[Any | None, int]] = {}
        self._page_cache: dict[tuple[str, str | None], list[Any]] = {}

    def get(self, path: str) -> tuple[Any | None, int]:
        parsed = urllib.parse.urlsplit(path)
        if (
            not path.startswith("/")
            or path.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
        ):
            raise EvidenceError("GitHub API path is not absolute")
        cacheable = not path.startswith("/gists/")
        if cacheable and path in self._get_cache:
            return self._get_cache[path]
        url = API_ROOT + path
        for attempt in range(4):
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "lean-eval-historical-public-replay",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                with self._opener.open(request, timeout=30) as response:
                    if response.status != 200:
                        raise EvidenceError(
                            f"GitHub API returned unexpected HTTP {response.status}"
                        )
                    declared = response.headers.get("Content-Length")
                    if declared is not None:
                        try:
                            declared_size = int(declared)
                        except ValueError as error:
                            raise EvidenceError(
                                "GitHub API response has invalid Content-Length"
                            ) from error
                        if declared_size < 0 or declared_size > MAX_RESPONSE_BYTES:
                            raise ResponseTooLarge(
                                "GitHub API response exceeds the size limit"
                            )
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ResponseTooLarge(
                            "GitHub API response exceeds the size limit"
                        )
                    result = (json.loads(raw), response.status)
                    if cacheable:
                        self._get_cache[path] = result
                    return result
            except urllib.error.HTTPError as error:
                try:
                    if error.code == 404:
                        result = (None, 404)
                        if cacheable:
                            self._get_cache[path] = result
                        return result
                    if error.code in {301, 302, 303, 307, 308}:
                        raise ProbeIndeterminate("github_redirect_refused") from error
                    if error.code == 451:
                        raise ProbeIndeterminate("github_legal_restriction") from error
                    if error.code == 403:
                        retry_after = error.headers.get("Retry-After")
                        if retry_after is not None and retry_after.isdigit():
                            delay = int(retry_after)
                            if delay <= 30 and attempt < 3:
                                time.sleep(delay)
                                continue
                        raise ProbeIndeterminate(
                            "github_rate_or_permission_boundary"
                        ) from error
                    if error.code not in {429, 500, 502, 503, 504} or attempt == 3:
                        raise ProbeIndeterminate("github_http_error") from error
                finally:
                    error.close()
            except (OSError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
                if attempt == 3:
                    raise ProbeIndeterminate("github_request_failed") from error
            time.sleep(2**attempt)
        raise AssertionError("unreachable")

    def pages(self, path: str, item_key: str | None = None) -> list[Any]:
        cache_key = (path, item_key)
        if cache_key in self._page_cache:
            return self._page_cache[cache_key]
        separator = "&" if "?" in path else "?"
        output: list[Any] = []
        for page in range(1, MAX_PAGES + 1):
            value, status = self.get(f"{path}{separator}per_page=100&page={page}")
            if status != 200:
                raise EvidenceError("paginated GitHub resource was not found")
            if item_key is not None:
                if not isinstance(value, dict) or not isinstance(
                    value.get(item_key), list
                ):
                    raise EvidenceError("GitHub page has an invalid collection")
                items = value[item_key]
            else:
                if not isinstance(value, list):
                    raise EvidenceError("GitHub page is not a collection")
                items = value
            output.extend(items)
            if len(items) < 100:
                self._page_cache[cache_key] = output
                return output
        raise EvidenceError("GitHub evidence exceeds the pagination limit")


def _source_identity(body: str) -> tuple[str, str, str, str | None]:
    try:
        fields = parse_issue_body(body)
        descriptor = parse_source_url(str(fields["source_url"]))
    except FetchError as error:
        raise EvidenceError(str(error)) from error
    return (
        str(fields["model"]),
        descriptor.kind,
        f"{descriptor.owner}/{descriptor.name}",
        descriptor.ref,
    )


def _canonical_repository(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or REPOSITORY.fullmatch(value) is None
        or any(segment in {".", ".."} for segment in value.split("/"))
    ):
        raise EvidenceError(f"{label} is not canonical")
    return value


def _preflight_evidence_repository(client: GitHubClient, repository: str) -> None:
    owner, name = repository.split("/", 1)
    path = "/repos/{}/{}".format(
        urllib.parse.quote(owner, safe=""), urllib.parse.quote(name, safe="")
    )
    value, status = client.get(path)
    if status != 200 or not isinstance(value, dict):
        raise ProbeIndeterminate("evidence_repository_not_readable")
    full_name = value.get("full_name")
    if not isinstance(full_name, str) or full_name.casefold() != repository.casefold():
        raise ProbeIndeterminate("evidence_repository_identity_changed")
    if value.get("private") is not False or value.get("visibility", "public") != "public":
        raise ProbeIndeterminate("evidence_repository_not_public")


def _source_url(source: dict[str, Any]) -> str:
    repository = source["repository"]
    commit = source["commit"]
    if source["kind"] == "github_repo":
        return f"https://github.com/{repository}/commit/{commit}"
    owner, name = repository.split("/", 1)
    return f"https://gist.github.com/{owner}/{name}/{commit}"


def _probe_source(client: GitHubClient, request: dict[str, Any]) -> dict[str, Any]:
    source = request["source"]
    repository = source["repository"]
    commit = source["commit"]
    owner, name = repository.split("/", 1)
    url = _source_url(source)
    try:
        if source["kind"] == "github_repo":
            repository_path = "/repos/{}/{}".format(
                urllib.parse.quote(owner, safe=""),
                urllib.parse.quote(name, safe=""),
            )
            repository_value, repository_status = client.get(repository_path)
            if repository_status == 200 and isinstance(repository_value, dict):
                full_name = repository_value.get("full_name")
                if not isinstance(full_name, str) or full_name.casefold() != repository.casefold():
                    return {
                        "status": "indeterminate",
                        "reason_code": "source_repository_identity_changed",
                        "commit_url": url,
                    }
            publicly_visible = (
                repository_status == 200
                and isinstance(repository_value, dict)
                and repository_value.get("private") is False
                and repository_value.get("visibility", "public") == "public"
            )
            value, status = client.get(repository_path + "/git/commits/" + commit)
            available = (
                publicly_visible
                and status == 200
                and isinstance(value, dict)
                and value.get("sha") == commit
            )
        else:
            # No content-free REST metadata endpoint exists for a gist revision.
            # Parse a bounded public response transiently and project no content.
            value, status = client.get(
                "/gists/{}/{}".format(urllib.parse.quote(name, safe=""), commit)
            )
            history = value.get("history", []) if isinstance(value, dict) else []
            owner_value = value.get("owner") if isinstance(value, dict) else None
            actual_owner = (
                owner_value.get("login") if isinstance(owner_value, dict) else None
            )
            available = (
                status == 200
                and isinstance(value, dict)
                and value.get("id") == name
                and value.get("public") is True
                and isinstance(actual_owner, str)
                and actual_owner.casefold() == owner.casefold()
                and any(
                    isinstance(item, dict) and item.get("version") == commit
                    for item in history
                )
            )
    except ResponseTooLarge:
        return {
            "status": "indeterminate",
            "reason_code": "source_probe_response_too_large",
            "commit_url": url,
        }
    except ProbeIndeterminate as error:
        return {
            "status": "indeterminate",
            "reason_code": error.reason_code,
            "commit_url": url,
        }
    return {"status": "available" if available else "unavailable", "commit_url": url}


def _workflow_definition(
    client: GitHubClient, repository: str, workflow_path: str, commit: str
) -> str | None:
    api_path = "/repos/{}/contents/{}?ref={}".format(
        repository,
        urllib.parse.quote(workflow_path, safe="/"),
        commit,
    )
    value, status = client.get(api_path)
    if (
        status != 200
        or not isinstance(value, dict)
        or value.get("encoding") != "base64"
        or not isinstance(value.get("content"), str)
        or type(value.get("size")) is not int
        or not 0 < value["size"] <= MAX_WORKFLOW_BYTES
    ):
        return None
    try:
        encoded = "".join(value["content"].split())
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    if len(raw) != value["size"] or len(raw) > MAX_WORKFLOW_BYTES:
        return None
    return hashlib.sha256(raw).hexdigest()


def _workflow_binding(
    client: GitHubClient,
    request: dict[str, Any],
    issue_repository: str,
    run: dict[str, Any],
    registry: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    head_sha = run.get("head_sha")
    if (
        not isinstance(head_sha, str)
        or COMMIT.fullmatch(head_sha) is None
        or run.get("path") != ".github/workflows/submission.yml"
    ):
        return None
    if issue_repository == "leanprover/lean-eval":
        if head_sha != request["benchmark"]["commit"]:
            return None
        digest = _workflow_definition(client, issue_repository, run["path"], head_sha)
        if digest is None:
            return None
        return {
            "contract": "benchmark_repository_head",
            "repository_commit": head_sha,
            "definition_sha256": digest,
            "reviewed": True,
        }
    if issue_repository != "leanprover/lean-eval-submissions":
        return None
    digest = _workflow_definition(client, issue_repository, run["path"], head_sha)
    if digest is None:
        return None
    reviewed = registry.get(head_sha)
    if reviewed is not None and (
        reviewed["definition_sha256"] != digest
        or reviewed["contract"] != SPLIT_WORKFLOW_CONTRACT
    ):
        raise IntegrityError("reviewed workflow definition bytes do not match")
    return {
        "contract": SPLIT_WORKFLOW_CONTRACT,
        "repository_commit": head_sha,
        "definition_sha256": digest,
        "reviewed": reviewed is not None,
    }


def _candidate(
    client: GitHubClient,
    request: dict[str, Any],
    repository: str,
    registry: dict[str, dict[str, str]],
) -> dict[str, Any]:
    _preflight_evidence_repository(client, repository)
    issue_number = request["issue_number"]
    issue, status = client.get(f"/repos/{repository}/issues/{issue_number}")
    if status == 404:
        return {"issue_repository": repository, "status": "issue_not_found"}
    if not isinstance(issue, dict) or "pull_request" in issue:
        return {
            "issue_repository": repository,
            "status": "issue_invalid",
            "reason_code": "candidate_not_issue",
        }
    try:
        title = issue["title"]
        body = issue["body"]
        author = issue["user"]["login"]
        created = timestamp(issue["created_at"], "issue.created_at")
        closed = timestamp(issue["closed_at"], "issue.closed_at")
        if not all(isinstance(value, str) for value in (title, body, author)):
            raise EvidenceError("issue identity fields are invalid")
        model, source_kind, source_repository, source_ref = _source_identity(body)
    except (KeyError, TypeError, EvidenceError):
        return {
            "issue_repository": repository,
            "status": "issue_invalid",
            "reason_code": "issue_body_invalid",
        }
    expected_issue_url = f"https://github.com/{repository}/issues/{issue_number}"
    if (
        issue.get("number") != issue_number
        or issue.get("state") != "closed"
        or issue.get("html_url") != expected_issue_url
    ):
        return {
            "issue_repository": repository,
            "status": "probe_indeterminate",
            "reason_code": "github_identity_changed",
        }
    expected_source = request["source"]
    if model != request["declared_model"]:
        return {"issue_repository": repository, "status": "model_mismatch"}
    if author.casefold() != request["owner"].casefold():
        return {"issue_repository": repository, "status": "owner_mismatch"}
    if (
        source_kind != expected_source["kind"]
        or source_repository.casefold() != expected_source["repository"].casefold()
    ):
        return {"issue_repository": repository, "status": "source_mismatch"}
    if (
        source_ref is not None
        and COMMIT.fullmatch(source_ref)
        and source_ref != expected_source["commit"]
    ):
        return {"issue_repository": repository, "status": "source_mismatch"}

    accepted = timestamp(request["accepted_at"], "request.accepted_at")
    if closed < created or closed > accepted + datetime.timedelta(minutes=2):
        return {"issue_repository": repository, "status": "acceptance_time_mismatch"}

    next_day = (created + datetime.timedelta(days=1)).date().isoformat()
    created_range = urllib.parse.quote(
        f"{created.date().isoformat()}..{next_day}", safe="."
    )
    runs = client.pages(
        f"/repos/{repository}/actions/runs?event=issues&created={created_range}",
        "workflow_runs",
    )
    matching_runs: list[dict[str, Any]] = []
    outside_window_runs: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        try:
            run_created = timestamp(run.get("created_at"), "run.created_at")
            run_updated = timestamp(run.get("updated_at"), "run.updated_at")
        except EvidenceError:
            continue
        run_id = run.get("id")
        actor_value = run.get("actor")
        repository_value = run.get("repository")
        head_repository_value = run.get("head_repository")
        identity_matches = (
            type(run_id) is int
            and run_id > 0
            and run.get("name") == request["expected_workflow"]["name"]
            and run.get("event") == request["expected_workflow"]["event"]
            and run.get("conclusion") == "success"
            and run.get("status") == "completed"
            and type(run.get("run_attempt")) is int
            and run["run_attempt"] > 0
            and run.get("display_title") == title
            and isinstance(actor_value, dict)
            and isinstance(actor_value.get("login"), str)
            and actor_value["login"].casefold() == author.casefold()
            and isinstance(repository_value, dict)
            and repository_value.get("full_name") == repository
            and isinstance(head_repository_value, dict)
            and head_repository_value.get("full_name") == repository
            and run.get("head_branch") == "main"
            and run.get("html_url")
            == f"https://github.com/{repository}/actions/runs/{run_id}"
        )
        if identity_matches:
            if (
                created <= run_created <= created + datetime.timedelta(seconds=30)
                and accepted <= run_updated <= accepted + RUN_COMPLETION_LAG
            ):
                matching_runs.append(run)
            else:
                outside_window_runs.append(run)
    if len(matching_runs) != 1:
        if not matching_runs and len(outside_window_runs) == 1:
            observed = outside_window_runs[0]
            return {
                "issue_repository": repository,
                "status": "timing_indeterminate",
                "reason_code": "workflow_run_outside_window",
                "issue_url": expected_issue_url,
                "issue_created_at": issue["created_at"],
                "issue_closed_at": issue["closed_at"],
                "issue_title_sha256": text_digest(title),
                "issue_body_sha256": text_digest(body),
                "evidence_url": observed["html_url"],
                "evidence_created_at": observed["created_at"],
                "evidence_updated_at": observed["updated_at"],
                "evidence_identity_sha256": hashlib.sha256(
                    canonical_bytes(observed)
                ).hexdigest(),
            }
        return {
            "issue_repository": repository,
            "status": (
                "workflow_run_missing"
                if not matching_runs
                else "workflow_run_ambiguous"
            ),
        }
    run = matching_runs[0]
    workflow = _workflow_binding(client, request, repository, run, registry)
    if workflow is None:
        return {"issue_repository": repository, "status": "workflow_contract_mismatch"}
    if not workflow["reviewed"]:
        return {
            "issue_repository": repository,
            "status": "workflow_contract_unreviewed",
            "workflow_repository_commit": workflow["repository_commit"],
            "workflow_definition_sha256": workflow["definition_sha256"],
        }

    comments = client.pages(f"/repos/{repository}/issues/{issue_number}/comments")
    expected_problems = {item["problem_id"] for item in request["results"]}
    matching_comments: list[dict[str, Any]] = []
    outside_window_comments: list[dict[str, Any]] = []
    projection_too_large = False
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        try:
            comment_at = timestamp(comment.get("created_at"), "comment.created_at")
        except EvidenceError:
            continue
        body_value = comment.get("body")
        pass_lines = PASS_LINE.findall(body_value) if isinstance(body_value, str) else []
        pass_counts = (
            collections.Counter(pass_lines)
            if isinstance(body_value, str)
            else collections.Counter()
        )
        comment_id = comment.get("id")
        comment_user = comment.get("user")
        identity_matches = (
            type(comment_id) is int
            and comment_id > 0
            and isinstance(comment_user, dict)
            and comment_user.get("login") == "github-actions[bot]"
            and all(pass_counts[problem] == 1 for problem in expected_problems)
            and comment.get("html_url")
            == f"{expected_issue_url}#issuecomment-{comment_id}"
        )
        if identity_matches:
            projected = sorted(
                problem for problem in pass_lines if PROBLEM_ID.fullmatch(problem)
            )
            if len(projected) > MAX_REPORTED_PASSES:
                projection_too_large = True
            elif not accepted <= comment_at <= accepted + COMMENT_ACCEPTANCE_LAG:
                selected = dict(comment)
                selected["_reported_pass_problem_ids"] = projected
                outside_window_comments.append(selected)
            else:
                comment = dict(comment)
                comment["_reported_pass_problem_ids"] = projected
                matching_comments.append(comment)
    if len(matching_comments) != 1:
        if not matching_comments and len(outside_window_comments) == 1:
            observed = outside_window_comments[0]
            return {
                "issue_repository": repository,
                "status": "timing_indeterminate",
                "reason_code": "result_comment_outside_window",
                "issue_url": expected_issue_url,
                "issue_created_at": issue["created_at"],
                "issue_closed_at": issue["closed_at"],
                "issue_title_sha256": text_digest(title),
                "issue_body_sha256": text_digest(body),
                "evidence_url": observed["html_url"],
                "evidence_created_at": observed["created_at"],
                "evidence_updated_at": None,
                "evidence_identity_sha256": text_digest(observed["body"]),
            }
        if projection_too_large and not matching_comments:
            return {
                "issue_repository": repository,
                "status": "result_comment_projection_too_large",
            }
        return {
            "issue_repository": repository,
            "status": (
                "result_comment_missing"
                if not matching_comments
                else "result_comment_ambiguous"
            ),
        }

    source_probe = _probe_source(client, request)
    source_status = source_probe["status"]
    comment = matching_comments[0]
    comment_body = comment["body"]
    run_identity = {
        "id": run["id"],
        "name": run["name"],
        "event": run["event"],
        "attempt": run["run_attempt"],
        "actor": run["actor"]["login"].casefold(),
        "repository": run["repository"]["full_name"],
        "head_repository": run["head_repository"]["full_name"],
        "head_branch": run["head_branch"],
        "head_sha": run["head_sha"],
        "path": run["path"],
        "display_title_sha256": text_digest(run["display_title"]),
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "status": run["status"],
        "conclusion": run["conclusion"],
        "html_url": run["html_url"],
    }
    issue_identity = {
        "declared_model": model,
        "source_kind": source_kind,
        "source_repository": source_repository.casefold(),
    }
    return {
        "issue_repository": repository,
        "status": {
            "available": "matched_source_available",
            "unavailable": "matched_source_unavailable",
            "indeterminate": "matched_source_indeterminate",
        }[source_status],
        "issue_url": expected_issue_url,
        "issue_author": author,
        "issue_created_at": issue["created_at"],
        "issue_closed_at": issue["closed_at"],
        "issue_title_sha256": text_digest(title),
        "issue_body_sha256": text_digest(body),
        "issue_identity_sha256": hashlib.sha256(
            canonical_bytes(issue_identity)
        ).hexdigest(),
        "issue_source_ref_sha256": hashlib.sha256(
            canonical_bytes(source_ref)
        ).hexdigest(),
        "issue_source_reference_binding": (
            "exact_commit" if source_ref == expected_source["commit"] else "unpinned"
        ),
        "workflow_run_id": run["id"],
        "workflow_run_url": run["html_url"],
        "workflow_run_created_at": run["created_at"],
        "workflow_run_updated_at": run["updated_at"],
        "workflow_run_attempt": run["run_attempt"],
        "workflow_run_actor": run["actor"]["login"],
        "workflow_run_display_title_sha256": text_digest(run["display_title"]),
        "workflow_run_identity_sha256": hashlib.sha256(
            canonical_bytes(run_identity)
        ).hexdigest(),
        "workflow_contract": workflow["contract"],
        "workflow_repository_commit": workflow["repository_commit"],
        "workflow_definition_sha256": workflow["definition_sha256"],
        "result_comment_url": comment["html_url"],
        "result_comment_created_at": comment["created_at"],
        "result_comment_author": comment["user"]["login"],
        "result_comment_body_sha256": text_digest(comment_body),
        "reported_pass_problem_ids": comment["_reported_pass_problem_ids"],
        "source_commit_url": source_probe["commit_url"],
        **(
            {"source_probe_reason_code": source_probe["reason_code"]}
            if source_status == "indeterminate"
            else {}
        ),
    }


def _safe_candidate(
    client: GitHubClient,
    request: dict[str, Any],
    repository: str,
    registry: dict[str, dict[str, str]],
) -> dict[str, Any]:
    try:
        return _candidate(client, request, repository, registry)
    except IntegrityError:
        raise
    except ResponseTooLarge:
        reason = "github_response_too_large"
    except ProbeIndeterminate as error:
        reason = error.reason_code
    except EvidenceError:
        reason = "github_response_invalid"
    return {
        "issue_repository": repository,
        "status": "probe_indeterminate",
        "reason_code": reason,
    }


def validate_requests(value: Any) -> None:
    top_fields = {
        "schema_version",
        "kind",
        "source_repository",
        "source_commit",
        "inventory_sha256",
        "request_count",
        "result_count",
        "requests",
    }
    if not isinstance(value, dict) or set(value) != top_fields:
        raise EvidenceError("resolution request fields are not closed")
    if value["schema_version"] != 1:
        raise EvidenceError("resolution requests are not schema version 1")
    if value["kind"] != "historical_public_replay_resolution_requests":
        raise EvidenceError("resolution requests have the wrong kind")
    if (
        value["source_repository"] != "leanprover/lean-eval-submissions"
        or not isinstance(value["source_commit"], str)
        or COMMIT.fullmatch(value["source_commit"]) is None
        or not isinstance(value["inventory_sha256"], str)
        or DIGEST.fullmatch(value["inventory_sha256"]) is None
    ):
        raise EvidenceError("resolution request source identity is invalid")
    requests = value["requests"]
    if (
        type(value["request_count"]) is not int
        or value["request_count"] <= 0
        or not isinstance(requests, list)
        or len(requests) != value["request_count"]
    ):
        raise EvidenceError("resolution request count is inconsistent")
    if type(value["result_count"]) is not int or value["result_count"] <= 0:
        raise EvidenceError("resolution result count is invalid")

    seen_requests: set[str] = set()
    seen_results: set[str] = set()
    result_count = 0
    request_fields = {
        "request_id",
        "owner",
        "issue_number",
        "accepted_at",
        "declared_model",
        "source",
        "benchmark",
        "candidate_issue_repositories",
        "expected_workflow",
        "readiness",
        "results",
    }
    for request in requests:
        if not isinstance(request, dict) or set(request) != request_fields:
            raise EvidenceError("resolution request fields are not closed")
        request_id = request["request_id"]
        if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
            raise EvidenceError("resolution request_id is invalid")
        if request_id in seen_requests:
            raise EvidenceError("resolution request_id is duplicated")
        seen_requests.add(request_id)
        owner = request["owner"]
        if not isinstance(owner, str) or LOGIN.fullmatch(owner) is None:
            raise EvidenceError("resolution owner is invalid")
        if type(request["issue_number"]) is not int or request["issue_number"] <= 0:
            raise EvidenceError("resolution issue number is invalid")
        accepted_at = request["accepted_at"]
        timestamp(accepted_at, "request.accepted_at")
        model = request["declared_model"]
        if (
            not isinstance(model, str)
            or not 1 <= len(model) <= 256
            or any(ord(char) < 32 for char in model)
        ):
            raise EvidenceError("resolution declared_model is invalid")
        if request["candidate_issue_repositories"] != CANDIDATE_REPOSITORIES:
            raise EvidenceError("candidate issue repositories are invalid")
        if request["expected_workflow"] != {"event": "issues", "name": "Submission"}:
            raise EvidenceError("expected workflow is invalid")
        if request["readiness"] != "github_evidence_fetch_pending":
            raise EvidenceError("request readiness is invalid")

        source = request["source"]
        if (
            not isinstance(source, dict)
            or set(source) != {"kind", "repository", "commit", "visibility"}
            or source.get("kind") not in {"github_repo", "gist"}
            or not isinstance(source.get("repository"), str)
            or not isinstance(source.get("commit"), str)
            or COMMIT.fullmatch(source["commit"]) is None
            or source.get("visibility") != "public"
        ):
            raise EvidenceError("request source identity is invalid")
        _canonical_repository(source["repository"], "request source repository")
        benchmark = request["benchmark"]
        if (
            not isinstance(benchmark, dict)
            or set(benchmark) != {"repository", "commit"}
            or benchmark.get("repository") != "leanprover/lean-eval"
            or not isinstance(benchmark.get("commit"), str)
            or COMMIT.fullmatch(benchmark["commit"]) is None
        ):
            raise EvidenceError("request benchmark identity is invalid")
        results = request["results"]
        if not isinstance(results, list) or not results:
            raise EvidenceError("request results are invalid")
        request_results: list[str] = []
        request_problems: set[tuple[str, int]] = set()
        for result in results:
            if not isinstance(result, dict) or set(result) != {
                "result_id",
                "owner",
                "problem_id",
                "statement_revision",
            }:
                raise EvidenceError("request result fields are not closed")
            result_id = result["result_id"]
            problem_id = result["problem_id"]
            if (
                not isinstance(result_id, str)
                or RESULT_ID.fullmatch(result_id) is None
                or result_id in seen_results
                or result["owner"] != owner
                or not isinstance(problem_id, str)
                or PROBLEM_ID.fullmatch(problem_id) is None
                or type(result["statement_revision"]) is not int
                or result["statement_revision"] <= 0
            ):
                raise EvidenceError("request result identity is invalid")
            seen_results.add(result_id)
            request_results.append(result_id)
            problem_identity = (problem_id, result["statement_revision"])
            if problem_identity in request_problems:
                raise EvidenceError("request problem identity is duplicated")
            request_problems.add(problem_identity)
        if request_results != sorted(set(request_results)):
            raise EvidenceError("request results are not unique and sorted")
        identity = {
            "owner": owner,
            "issue_number": request["issue_number"],
            "accepted_at": accepted_at,
            "declared_model": model,
            "source": source,
            "benchmark": benchmark,
        }
        expected_id = "prr_" + hashlib.sha256(canonical_bytes(identity)).hexdigest()
        if request_id != expected_id:
            raise EvidenceError("resolution request_id does not bind its identity")
        result_count += len(results)
    if result_count != value["result_count"]:
        raise EvidenceError("resolution result count is inconsistent")
    if [request["request_id"] for request in requests] != sorted(seen_requests):
        raise EvidenceError("resolution requests are not sorted")


def _classify_candidates(candidates: list[dict[str, Any]]) -> tuple[str, str | None]:
    available = [
        item for item in candidates if item.get("status") == "matched_source_available"
    ]
    unavailable = [
        item
        for item in candidates
        if item.get("status") == "matched_source_unavailable"
    ]
    indeterminate = [
        item
        for item in candidates
        if item.get("status") == "matched_source_indeterminate"
    ]
    unreviewed = [
        item
        for item in candidates
        if item.get("status") == "workflow_contract_unreviewed"
    ]
    probe_indeterminate = [
        item for item in candidates if item.get("status") == "probe_indeterminate"
    ]
    timing_indeterminate = [
        item for item in candidates if item.get("status") == "timing_indeterminate"
    ]
    matched = available + unavailable + indeterminate
    if unreviewed:
        if matched or probe_indeterminate or timing_indeterminate or len(unreviewed) != 1:
            return "ambiguous", None
        return "workflow_contract_unreviewed", unreviewed[0]["issue_repository"]
    if probe_indeterminate:
        if matched or timing_indeterminate or len(probe_indeterminate) != 1:
            return "ambiguous", None
        return "probe_indeterminate", probe_indeterminate[0]["issue_repository"]
    if timing_indeterminate:
        if matched or len(timing_indeterminate) != 1:
            return "ambiguous", None
        return "timing_indeterminate", timing_indeterminate[0]["issue_repository"]
    if len(available) == 1 and not unavailable and not indeterminate:
        return "resolved", available[0]["issue_repository"]
    if not available and len(unavailable) == 1 and not indeterminate:
        return "source_unavailable", unavailable[0]["issue_repository"]
    if not available and not unavailable and len(indeterminate) == 1:
        return "source_probe_indeterminate", indeterminate[0]["issue_repository"]
    if len(available) + len(unavailable) + len(indeterminate) > 1:
        return "ambiguous", None
    return "evidence_missing", None


def resolve(
    value: dict[str, Any],
    raw_sha256: str,
    client: GitHubClient,
    workflow_registry: dict[str, Any] | None = None,
    workflow_registry_sha256: str | None = None,
    shard_index: int = 0,
    shard_count: int = 1,
) -> dict[str, Any]:
    validate_requests(value)
    if workflow_registry is None or workflow_registry_sha256 is None:
        raise EvidenceError("exact raw workflow registry and digest are required")
    registry = validate_workflow_registry(workflow_registry)
    if DIGEST.fullmatch(workflow_registry_sha256) is None:
        raise EvidenceError("workflow definition registry digest is invalid")
    if not isinstance(raw_sha256, str) or DIGEST.fullmatch(raw_sha256) is None:
        raise EvidenceError("resolution request digest is invalid")
    if (
        type(shard_index) is not int
        or type(shard_count) is not int
        or not 1 <= shard_count <= 64
        or not 0 <= shard_index < shard_count
    ):
        raise EvidenceError("shard index/count are invalid")
    selected_requests = [
        request
        for request in value["requests"]
        if int(request["request_id"].removeprefix("prr_"), 16) % shard_count
        == shard_index
    ]
    resolutions: list[dict[str, Any]] = []
    counts: collections.Counter[str] = collections.Counter()
    for request in selected_requests:
        candidates = [
            _safe_candidate(client, request, repository, registry)
            for repository in request["candidate_issue_repositories"]
        ]
        status, selected_repository = _classify_candidates(candidates)
        counts[status] += 1
        resolutions.append(
            {
                "request_id": request["request_id"],
                "status": status,
                "selected_issue_repository": selected_repository,
                "candidates": candidates,
            }
        )
    return {
        "schema_version": 1,
        "kind": "historical_public_replay_github_evidence",
        "source_repository": value["source_repository"],
        "source_commit": value["source_commit"],
        "inventory_sha256": value["inventory_sha256"],
        "resolution_requests_sha256": raw_sha256,
        "workflow_definition_registry_sha256": workflow_registry_sha256,
        "request_count": value["request_count"],
        "result_count": value["result_count"],
        "shard_index": shard_index,
        "shard_count": shard_count,
        "shard_request_count": len(selected_requests),
        "shard_result_count": sum(
            len(request["results"]) for request in selected_requests
        ),
        "resolved_count": counts["resolved"],
        "source_unavailable_count": counts["source_unavailable"],
        "source_indeterminate_count": counts["source_probe_indeterminate"],
        "probe_indeterminate_count": counts["probe_indeterminate"],
        "timing_indeterminate_count": counts["timing_indeterminate"],
        "workflow_contract_unreviewed_count": counts[
            "workflow_contract_unreviewed"
        ],
        "pending_count": len(selected_requests) - counts["resolved"],
        "resolutions": resolutions,
    }


def _https_url(value: Any, label: str) -> urllib.parse.SplitResult:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} is not a URL")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"github.com", "gist.github.com"}
        or parsed.netloc != parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
    ):
        raise EvidenceError(f"{label} is not a canonical HTTPS URL")
    return parsed


def validate_evidence(
    value: Any,
    requests_value: Any,
    workflow_registry: dict[str, Any] | None = None,
    workflow_registry_sha256: str | None = None,
) -> None:
    if not isinstance(value, dict):
        raise EvidenceError("GitHub evidence is not an object")
    expected_top = {
        "schema_version",
        "kind",
        "source_repository",
        "source_commit",
        "inventory_sha256",
        "resolution_requests_sha256",
        "workflow_definition_registry_sha256",
        "request_count",
        "result_count",
        "shard_index",
        "shard_count",
        "shard_request_count",
        "shard_result_count",
        "resolved_count",
        "source_unavailable_count",
        "source_indeterminate_count",
        "probe_indeterminate_count",
        "timing_indeterminate_count",
        "workflow_contract_unreviewed_count",
        "pending_count",
        "resolutions",
    }
    if set(value) != expected_top:
        raise EvidenceError("GitHub evidence top-level fields are not closed")
    if (
        value["schema_version"] != 1
        or value["kind"] != "historical_public_replay_github_evidence"
        or value["source_repository"] != "leanprover/lean-eval-submissions"
        or not isinstance(value["source_commit"], str)
        or COMMIT.fullmatch(value["source_commit"]) is None
    ):
        raise EvidenceError("GitHub evidence identity is invalid")
    for field in (
        "inventory_sha256",
        "resolution_requests_sha256",
        "workflow_definition_registry_sha256",
    ):
        if not isinstance(value[field], str) or DIGEST.fullmatch(value[field]) is None:
            raise EvidenceError(f"GitHub evidence {field} is invalid")
    positive_fields = {
        "request_count",
        "result_count",
        "shard_count",
    }
    count_fields = positive_fields | {
        "shard_request_count",
        "shard_result_count",
        "resolved_count",
        "source_unavailable_count",
        "source_indeterminate_count",
        "probe_indeterminate_count",
        "timing_indeterminate_count",
        "workflow_contract_unreviewed_count",
        "pending_count",
    }
    for field in count_fields:
        if (
            type(value[field]) is not int
            or value[field] < 0
            or (field in positive_fields and value[field] == 0)
        ):
            raise EvidenceError(f"GitHub evidence {field} is invalid")
    if (
        not 1 <= value["shard_count"] <= 64
        or type(value["shard_index"]) is not int
        or not 0 <= value["shard_index"] < value["shard_count"]
    ):
        raise EvidenceError("GitHub evidence shard identity is invalid")
    resolutions = value["resolutions"]
    if (
        not isinstance(resolutions, list)
        or len(resolutions) != value["shard_request_count"]
    ):
        raise EvidenceError("GitHub evidence resolution count is inconsistent")
    resolution_ids = [
        item.get("request_id") if isinstance(item, dict) else None
        for item in resolutions
    ]
    if not all(
        isinstance(item, str) for item in resolution_ids
    ) or resolution_ids != sorted(set(resolution_ids)):
        raise EvidenceError("GitHub evidence resolutions are not unique and sorted")

    validate_requests(requests_value)
    if workflow_registry is None or workflow_registry_sha256 is None:
        raise EvidenceError("exact raw workflow registry and digest are required")
    registry = validate_workflow_registry(workflow_registry)
    if not isinstance(workflow_registry_sha256, str) or DIGEST.fullmatch(
        workflow_registry_sha256
    ) is None:
        raise EvidenceError("workflow registry raw digest is invalid")
    if value["workflow_definition_registry_sha256"] != workflow_registry_sha256:
        raise EvidenceError("GitHub evidence does not bind its workflow registry")
    request_by_id = {
        request["request_id"]: request for request in requests_value["requests"]
    }
    selected_requests = [
        request
        for request in requests_value["requests"]
        if int(request["request_id"].removeprefix("prr_"), 16)
        % value["shard_count"]
        == value["shard_index"]
    ]
    if (
        value["source_repository"] != requests_value["source_repository"]
        or value["source_commit"] != requests_value["source_commit"]
        or value["inventory_sha256"] != requests_value["inventory_sha256"]
        or value["request_count"] != requests_value["request_count"]
        or value["result_count"] != requests_value["result_count"]
        or value["shard_request_count"] != len(selected_requests)
        or value["shard_result_count"]
        != sum(len(request["results"]) for request in selected_requests)
        or resolution_ids != [request["request_id"] for request in selected_requests]
    ):
        raise EvidenceError("GitHub evidence does not bind its resolution requests")

    simple_statuses = {
        "issue_not_found",
        "github_identity_mismatch",
        "model_mismatch",
        "owner_mismatch",
        "source_mismatch",
        "acceptance_time_mismatch",
        "workflow_run_missing",
        "workflow_run_ambiguous",
        "workflow_contract_mismatch",
        "result_comment_missing",
        "result_comment_ambiguous",
        "result_comment_projection_too_large",
    }
    matched_statuses = {
        "matched_source_available",
        "matched_source_unavailable",
        "matched_source_indeterminate",
    }
    resolution_statuses = {
        "resolved",
        "source_unavailable",
        "source_probe_indeterminate",
        "probe_indeterminate",
        "timing_indeterminate",
        "workflow_contract_unreviewed",
        "ambiguous",
        "evidence_missing",
    }
    for index, resolution in enumerate(resolutions):
        label = f"resolutions[{index}]"
        if not isinstance(resolution, dict) or set(resolution) != {
            "request_id",
            "status",
            "selected_issue_repository",
            "candidates",
        }:
            raise EvidenceError(f"{label} fields are not closed")
        request_id = resolution["request_id"]
        if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
            raise EvidenceError(f"{label}.request_id is invalid")
        if (
            int(request_id.removeprefix("prr_"), 16) % value["shard_count"]
            != value["shard_index"]
        ):
            raise EvidenceError(f"{label} belongs to another shard")
        request = request_by_id.get(request_id)
        candidates = resolution["candidates"]
        if (
            not isinstance(candidates, list)
            or len(candidates) != len(CANDIDATE_REPOSITORIES)
            or not all(isinstance(item, dict) for item in candidates)
            or [item.get("issue_repository") for item in candidates]
            != CANDIDATE_REPOSITORIES
        ):
            raise EvidenceError(f"{label}.candidates are not canonical")
        for candidate_index, candidate in enumerate(candidates):
            candidate_label = f"{label}.candidates[{candidate_index}]"
            status = candidate.get("status")
            base = {"issue_repository", "status"}
            if status in simple_statuses:
                if set(candidate) != base:
                    raise EvidenceError(f"{candidate_label} fields are not closed")
                continue
            if status == "issue_invalid":
                if set(candidate) != base | {"reason_code"} or candidate.get(
                    "reason_code"
                ) not in {"candidate_not_issue", "issue_body_invalid"}:
                    raise EvidenceError(f"{candidate_label} issue reason is invalid")
                continue
            if status == "probe_indeterminate":
                allowed = {
                    "github_redirect_refused",
                    "github_legal_restriction",
                    "github_rate_or_permission_boundary",
                    "github_http_error",
                    "github_request_failed",
                    "github_response_too_large",
                    "github_response_invalid",
                    "github_identity_changed",
                    "evidence_repository_not_readable",
                    "evidence_repository_identity_changed",
                    "evidence_repository_not_public",
                }
                if set(candidate) != base | {"reason_code"} or candidate.get(
                    "reason_code"
                ) not in allowed:
                    raise EvidenceError(f"{candidate_label} probe reason is invalid")
                continue
            if status == "timing_indeterminate":
                timing_fields = base | {
                    "reason_code",
                    "issue_url",
                    "issue_created_at",
                    "issue_closed_at",
                    "issue_title_sha256",
                    "issue_body_sha256",
                    "evidence_url",
                    "evidence_created_at",
                    "evidence_updated_at",
                    "evidence_identity_sha256",
                }
                if set(candidate) != timing_fields or candidate.get(
                    "reason_code"
                ) not in {
                    "workflow_run_outside_window",
                    "result_comment_outside_window",
                }:
                    raise EvidenceError(f"{candidate_label} timing evidence is invalid")
                for field in ("issue_url", "evidence_url"):
                    _https_url(candidate[field], f"{candidate_label}.{field}")
                for field in (
                    "issue_created_at",
                    "issue_closed_at",
                    "evidence_created_at",
                ):
                    timestamp(candidate[field], f"{candidate_label}.{field}")
                updated = candidate["evidence_updated_at"]
                if updated is not None:
                    timestamp(updated, f"{candidate_label}.evidence_updated_at")
                for field in (
                    "issue_title_sha256",
                    "issue_body_sha256",
                    "evidence_identity_sha256",
                ):
                    if not isinstance(candidate[field], str) or DIGEST.fullmatch(
                        candidate[field]
                    ) is None:
                        raise EvidenceError(f"{candidate_label} digest is invalid")
                expected_issue_url = (
                    f"https://github.com/{candidate['issue_repository']}/issues/"
                    f"{request['issue_number']}"
                )
                issue_created = timestamp(candidate["issue_created_at"], "issue.created_at")
                issue_closed = timestamp(candidate["issue_closed_at"], "issue.closed_at")
                observed_created = timestamp(
                    candidate["evidence_created_at"], "evidence.created_at"
                )
                accepted = timestamp(request["accepted_at"], "request.accepted_at")
                if candidate["issue_url"] != expected_issue_url or issue_closed < issue_created:
                    raise EvidenceError(f"{candidate_label} timing issue is not cross-bound")
                if candidate["reason_code"] == "workflow_run_outside_window":
                    if updated is None:
                        raise EvidenceError(f"{candidate_label} run update is missing")
                    observed_updated = timestamp(updated, "evidence.updated_at")
                    run_prefix = (
                        f"https://github.com/{candidate['issue_repository']}/actions/runs/"
                    )
                    if (
                        not candidate["evidence_url"].startswith(run_prefix)
                        or not candidate["evidence_url"].removeprefix(run_prefix).isdigit()
                        or (
                            issue_created
                            <= observed_created
                            <= issue_created + datetime.timedelta(seconds=30)
                            and accepted
                            <= observed_updated
                            <= accepted + RUN_COMPLETION_LAG
                        )
                    ):
                        raise EvidenceError(
                            f"{candidate_label} run timing is not adjudicable"
                        )
                elif (
                    updated is not None
                    or not candidate["evidence_url"].startswith(
                        expected_issue_url + "#issuecomment-"
                    )
                    or not candidate["evidence_url"].removeprefix(
                        expected_issue_url + "#issuecomment-"
                    ).isdigit()
                    or accepted
                    <= observed_created
                    <= accepted + COMMENT_ACCEPTANCE_LAG
                ):
                    raise EvidenceError(
                        f"{candidate_label} comment timing is not adjudicable"
                    )
                continue
            if status == "workflow_contract_unreviewed":
                if set(candidate) != base | {
                    "workflow_repository_commit",
                    "workflow_definition_sha256",
                }:
                    raise EvidenceError(
                        f"{candidate_label} unreviewed workflow fields are not closed"
                    )
                commit = candidate["workflow_repository_commit"]
                digest = candidate["workflow_definition_sha256"]
                if (
                    not isinstance(commit, str)
                    or COMMIT.fullmatch(commit) is None
                    or not isinstance(digest, str)
                    or DIGEST.fullmatch(digest) is None
                    or commit in registry
                ):
                    raise EvidenceError(
                        f"{candidate_label} unreviewed workflow identity is invalid"
                    )
                continue
            if status not in matched_statuses:
                raise EvidenceError(f"{candidate_label}.status is invalid")
            matched_fields = base | {
                "issue_url",
                "issue_author",
                "issue_created_at",
                "issue_closed_at",
                "issue_title_sha256",
                "issue_body_sha256",
                "issue_identity_sha256",
                "issue_source_ref_sha256",
                "issue_source_reference_binding",
                "workflow_run_id",
                "workflow_run_url",
                "workflow_run_created_at",
                "workflow_run_updated_at",
                "workflow_run_attempt",
                "workflow_run_actor",
                "workflow_run_display_title_sha256",
                "workflow_run_identity_sha256",
                "workflow_contract",
                "workflow_repository_commit",
                "workflow_definition_sha256",
                "result_comment_url",
                "result_comment_created_at",
                "result_comment_author",
                "result_comment_body_sha256",
                "reported_pass_problem_ids",
                "source_commit_url",
            }
            if status == "matched_source_indeterminate":
                matched_fields.add("source_probe_reason_code")
            if set(candidate) != matched_fields:
                raise EvidenceError(f"{candidate_label} fields are not closed")
            for url_field in (
                "issue_url",
                "workflow_run_url",
                "result_comment_url",
                "source_commit_url",
            ):
                _https_url(candidate[url_field], f"{candidate_label}.{url_field}")
            issue_created = timestamp(
                candidate["issue_created_at"], f"{candidate_label}.issue_created_at"
            )
            issue_closed = timestamp(
                candidate["issue_closed_at"], f"{candidate_label}.issue_closed_at"
            )
            run_created = timestamp(
                candidate["workflow_run_created_at"],
                f"{candidate_label}.workflow_run_created_at",
            )
            run_updated = timestamp(
                candidate["workflow_run_updated_at"],
                f"{candidate_label}.workflow_run_updated_at",
            )
            comment_created = timestamp(
                candidate["result_comment_created_at"],
                f"{candidate_label}.result_comment_created_at",
            )
            accepted = timestamp(request["accepted_at"], "request.accepted_at")
            if not isinstance(candidate["issue_author"], str) or LOGIN.fullmatch(
                candidate["issue_author"]
            ) is None:
                raise EvidenceError(f"{candidate_label}.issue_author is invalid")
            run_id = candidate["workflow_run_id"]
            if type(run_id) is not int or run_id <= 0:
                raise EvidenceError(f"{candidate_label}.workflow_run_id is invalid")
            if (
                type(candidate["workflow_run_attempt"]) is not int
                or candidate["workflow_run_attempt"] <= 0
                or issue_closed < issue_created
                or issue_closed > accepted + datetime.timedelta(minutes=2)
                or not issue_created
                <= run_created
                <= issue_created + datetime.timedelta(seconds=30)
                or not accepted <= run_updated <= accepted + RUN_COMPLETION_LAG
                or not accepted
                <= comment_created
                <= accepted + COMMENT_ACCEPTANCE_LAG
            ):
                raise EvidenceError(f"{candidate_label} timing identity is invalid")
            if (
                not isinstance(candidate["workflow_run_actor"], str)
                or LOGIN.fullmatch(candidate["workflow_run_actor"]) is None
                or candidate["workflow_run_actor"].casefold()
                != candidate["issue_author"].casefold()
                or candidate["workflow_run_display_title_sha256"]
                != candidate["issue_title_sha256"]
                or candidate["result_comment_author"] != "github-actions[bot]"
            ):
                raise EvidenceError(f"{candidate_label} actor/title identity is invalid")
            for digest_field in (
                "issue_title_sha256",
                "issue_body_sha256",
                "issue_identity_sha256",
                "issue_source_ref_sha256",
                "workflow_run_identity_sha256",
                "workflow_run_display_title_sha256",
                "result_comment_body_sha256",
            ):
                digest_value = candidate[digest_field]
                if (
                    not isinstance(digest_value, str)
                    or DIGEST.fullmatch(digest_value) is None
                ):
                    raise EvidenceError(f"{candidate_label} digest is invalid")
            reference_binding = candidate["issue_source_reference_binding"]
            if reference_binding not in {"exact_commit", "unpinned"}:
                raise EvidenceError(
                    f"{candidate_label} source reference binding is invalid"
                )
            expected_issue_identity = {
                "declared_model": request["declared_model"],
                "source_kind": request["source"]["kind"],
                "source_repository": request["source"]["repository"].casefold(),
            }
            if candidate["issue_identity_sha256"] != hashlib.sha256(
                canonical_bytes(expected_issue_identity)
            ).hexdigest():
                raise EvidenceError(f"{candidate_label} issue identity is not cross-bound")
            if reference_binding == "exact_commit" and candidate[
                "issue_source_ref_sha256"
            ] != hashlib.sha256(
                canonical_bytes(request["source"]["commit"])
            ).hexdigest():
                raise EvidenceError(f"{candidate_label} source ref is not cross-bound")
            passes = candidate["reported_pass_problem_ids"]
            if (
                not isinstance(passes, list)
                or len(passes) > MAX_REPORTED_PASSES
                or passes != sorted(passes)
                or not all(
                    isinstance(problem, str)
                    and PROBLEM_ID.fullmatch(problem) is not None
                    for problem in passes
                )
            ):
                raise EvidenceError(f"{candidate_label} pass projection is invalid")
            expected_problems = {item["problem_id"] for item in request["results"]}
            pass_counts = collections.Counter(passes)
            if not all(pass_counts[problem] == 1 for problem in expected_problems):
                raise EvidenceError(f"{candidate_label} pass projection is incomplete")
            workflow_commit = candidate["workflow_repository_commit"]
            if (
                not isinstance(workflow_commit, str)
                or COMMIT.fullmatch(workflow_commit) is None
            ):
                raise EvidenceError(f"{candidate_label} workflow commit is invalid")
            contract = candidate["workflow_contract"]
            definition = candidate["workflow_definition_sha256"]
            if contract == "benchmark_repository_head":
                if not isinstance(definition, str) or DIGEST.fullmatch(definition) is None:
                    raise EvidenceError(f"{candidate_label} workflow digest is invalid")
            elif contract == "split_repository_recorded_benchmark_v1":
                if (
                    not isinstance(definition, str)
                    or DIGEST.fullmatch(definition) is None
                ):
                    raise EvidenceError(f"{candidate_label} workflow digest is invalid")
                reviewed = registry.get(workflow_commit)
                if reviewed != {
                    "evaluator_commit": workflow_commit,
                    "definition_sha256": definition,
                    "contract": SPLIT_WORKFLOW_CONTRACT,
                }:
                    raise EvidenceError(
                        f"{candidate_label} workflow definition is not reviewed"
                    )
            else:
                raise EvidenceError(f"{candidate_label} workflow contract is invalid")
            if status == "matched_source_indeterminate" and candidate.get(
                "source_probe_reason_code"
            ) not in {
                "source_probe_response_too_large",
                "source_repository_identity_changed",
                "github_redirect_refused",
                "github_legal_restriction",
                "github_rate_or_permission_boundary",
                "github_http_error",
                "github_request_failed",
            }:
                raise EvidenceError(f"{candidate_label} source probe reason is invalid")

            repository = candidate["issue_repository"]
            issue_path_prefix = f"https://github.com/{repository}/issues/"
            expected_issue_url = issue_path_prefix + str(request["issue_number"])
            expected_contract = (
                "benchmark_repository_head"
                if repository == "leanprover/lean-eval"
                else "split_repository_recorded_benchmark_v1"
            )
            if (
                candidate["issue_url"] != expected_issue_url
                or candidate["issue_author"].casefold()
                != request["owner"].casefold()
                or candidate["workflow_run_url"]
                != f"https://github.com/{repository}/actions/runs/{run_id}"
                or not candidate["result_comment_url"].startswith(
                    expected_issue_url + "#issuecomment-"
                )
                or not candidate["result_comment_url"].removeprefix(
                    expected_issue_url + "#issuecomment-"
                ).isdigit()
                or candidate["source_commit_url"] != _source_url(request["source"])
                or contract != expected_contract
                or (
                    contract == "benchmark_repository_head"
                    and workflow_commit != request["benchmark"]["commit"]
                )
            ):
                raise EvidenceError(f"{candidate_label} is not cross-bound")

        if resolution["status"] not in resolution_statuses:
            raise EvidenceError(f"{label}.status is invalid")
        expected_status, expected_selected = _classify_candidates(candidates)
        if (
            resolution["status"] != expected_status
            or resolution["selected_issue_repository"] != expected_selected
        ):
            raise EvidenceError(f"{label} classification is inconsistent")

    status_counts = collections.Counter(item["status"] for item in resolutions)
    if (
        value["resolved_count"] != status_counts["resolved"]
        or value["source_unavailable_count"] != status_counts["source_unavailable"]
        or value["source_indeterminate_count"]
        != status_counts["source_probe_indeterminate"]
        or value["probe_indeterminate_count"] != status_counts["probe_indeterminate"]
        or value["timing_indeterminate_count"]
        != status_counts["timing_indeterminate"]
        or value["workflow_contract_unreviewed_count"]
        != status_counts["workflow_contract_unreviewed"]
        or value["pending_count"]
        != value["shard_request_count"] - status_counts["resolved"]
    ):
        raise EvidenceError("GitHub evidence status counts are inconsistent")


def _read_bounded(path: pathlib.Path, limit: int, label: str = "input") -> bytes:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise EvidenceError(f"{label} is not readable") from error
    if size <= 0 or size > limit:
        raise EvidenceError(f"{label} exceeds the size limit")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) != size or len(raw) > limit:
        raise EvidenceError(f"{label} changed while being read")
    return raw


def _write_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", required=True, type=pathlib.Path)
    parser.add_argument("--workflow-registry", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--shard-index", required=True, type=int)
    parser.add_argument("--shard-count", required=True, type=int)
    args = parser.parse_args()
    try:
        raw = _read_bounded(args.requests, MAX_REQUEST_BYTES, "resolution requests")
        value = json.loads(raw)
        registry_raw = _read_bounded(
            args.workflow_registry, MAX_WORKFLOW_BYTES, "workflow definition registry"
        )
        registry_value = json.loads(registry_raw)
        validate_workflow_registry(registry_value)
        registry_sha256 = hashlib.sha256(registry_raw).hexdigest()
        client = GitHubClient(os.environ.get("GITHUB_TOKEN", ""))
        output = resolve(
            value,
            hashlib.sha256(raw).hexdigest(),
            client,
            registry_value,
            registry_sha256,
            args.shard_index,
            args.shard_count,
        )
        validate_evidence(output, value, registry_value, registry_sha256)
        _write_exclusive(args.output, output)
    except (OSError, UnicodeError, json.JSONDecodeError, EvidenceError) as error:
        print(f"public-replay-github-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
