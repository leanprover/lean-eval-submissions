#!/usr/bin/env python3
"""
Fetch a submission for the lean-eval benchmark.

Parses an issue-event payload to extract the submitter's URL and model,
normalizes the URL, resolves it to a concrete commit or gist revision,
clones the content to a local directory, and emits frozen metadata for
downstream workflow jobs.

This script is the sole owner of issue-body parsing in the submission
workflow; downstream jobs must only consume metadata.json, never
re-parse the issue body.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UUIDV7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")
PROBLEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
OWNER_RE = r"[A-Za-z0-9][A-Za-z0-9-]*"
REPO_RE = r"[A-Za-z0-9._-]+"
GIST_USER_RE = r"[A-Za-z0-9-]+"
GIST_ID_RE = r"[0-9a-f]+"
REF_RE = r"[A-Za-z0-9._/-]+"


class FetchError(Exception):
    """Raised for any submission-fetch failure. Message is user-facing."""


@dataclass(frozen=True)
class SourceDescriptor:
    kind: Literal["github_repo", "gist"]
    owner: str
    name: str
    ref: str | None


PRODUCTION_DESCRIPTION_HEADING = "How this solution was produced (optional)"
PRODUCTION_DESCRIPTION_MAX_LEN = 4000
PUBLICATION_STATUS_HEADING = "Exact solution publication status"
PUBLICATION_DATE_HEADING = "Publication date (if public)"
INTENDED_PUBLICATION_DATE_HEADING = "Intended publication date (if planned)"
PUBLICATION_STATUS_VALUES = {
    "Public": "published",
    "Private, but publication is planned": "planned",
    "Private, with no current publication plan": "private",
}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def find_issue_section(body_text: str, heading: str) -> str | None:
    pattern = re.compile(
        rf"^###\s+{re.escape(heading)}\s*\n+(?P<value>.+?)(?=\n+###\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(body_text)
    if match is None:
        return None
    return match.group("value").strip()


def _optional_section(body_text: str, heading: str) -> str | None:
    value = find_issue_section(body_text, heading)
    if value is None or not value or value.startswith("_No response_"):
        return None
    return value


def _validate_date(value: str, heading: str) -> None:
    if not DATE_RE.fullmatch(value):
        raise FetchError(f"`{heading}` must use YYYY-MM-DD format.")
    try:
        datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise FetchError(f"`{heading}` is not a valid calendar date: {value!r}.") from exc


def _parse_publication_fields(
    body_text: str, fields: dict[str, str | None]
) -> None:
    status_value = _optional_section(body_text, PUBLICATION_STATUS_HEADING)
    publication_date = _optional_section(body_text, PUBLICATION_DATE_HEADING)
    intended_date = _optional_section(body_text, INTENDED_PUBLICATION_DATE_HEADING)

    # Issues opened before these fields were deployed must remain replayable.
    if status_value is None:
        if publication_date is not None or intended_date is not None:
            raise FetchError(
                f"`{PUBLICATION_STATUS_HEADING}` is required when a publication "
                "date is supplied."
            )
        fields["solution_publication_status"] = None
        fields["solution_publication_date"] = None
        return

    status = PUBLICATION_STATUS_VALUES.get(status_value)
    if status is None:
        raise FetchError(
            f"`{PUBLICATION_STATUS_HEADING}` has an unrecognized value: "
            f"{status_value!r}."
        )
    if publication_date is not None:
        _validate_date(publication_date, PUBLICATION_DATE_HEADING)
    if intended_date is not None:
        _validate_date(intended_date, INTENDED_PUBLICATION_DATE_HEADING)

    if status == "published":
        if publication_date is None:
            raise FetchError(
                f"`{PUBLICATION_DATE_HEADING}` is required when exact solutions "
                "are public."
            )
        if intended_date is not None:
            raise FetchError(
                f"Leave `{INTENDED_PUBLICATION_DATE_HEADING}` blank when exact "
                "solutions are already public."
            )
        selected_date = publication_date
    elif status == "planned":
        if intended_date is None:
            raise FetchError(
                f"`{INTENDED_PUBLICATION_DATE_HEADING}` is required when "
                "publication is planned."
            )
        if publication_date is not None:
            raise FetchError(
                f"Leave `{PUBLICATION_DATE_HEADING}` blank when publication is "
                "only planned."
            )
        selected_date = intended_date
    else:
        if publication_date is not None or intended_date is not None:
            raise FetchError(
                "Leave both publication-date fields blank when there is no "
                "current publication plan."
            )
        selected_date = None

    fields["solution_publication_status"] = status
    fields["solution_publication_date"] = selected_date


def parse_issue_body(body_text: str) -> dict[str, str | None]:
    """Extract submission fields from a GitHub Issue Form's rendered body.

    Issue Forms render as markdown with section headers like
    `### Submission URL\\n\\n<value>\\n\\n### Model\\n\\n<value>`.
    `source_url` and `model` are required; missing or empty values raise
    FetchError. Publication fields are validated when present, but are absent
    on legacy issues. `production_description` is optional and may be `None`.
    """
    fields: dict[str, str | None] = {}
    for field_key, heading in (("source_url", "Submission URL"), ("model", "Model")):
        value = find_issue_section(body_text, heading)
        if value is None:
            raise FetchError(
                f"Could not find `{heading}` section in issue body. "
                "Make sure you submitted via the `Submit benchmark solution` Issue Form."
            )
        if not value or value.startswith("_No response_"):
            raise FetchError(f"`{heading}` field is empty.")
        fields[field_key] = value

    description = find_issue_section(body_text, PRODUCTION_DESCRIPTION_HEADING)
    if description is None or not description or description.startswith("_No response_"):
        fields["production_description"] = None
    else:
        if len(description) > PRODUCTION_DESCRIPTION_MAX_LEN:
            raise FetchError(
                f"`{PRODUCTION_DESCRIPTION_HEADING}` field is longer than "
                f"{PRODUCTION_DESCRIPTION_MAX_LEN} characters."
            )
        fields["production_description"] = description
    _parse_publication_fields(body_text, fields)
    return fields


def parse_source_url(url: str) -> SourceDescriptor:
    """Normalize and validate a submission URL.

    Raises FetchError with a user-friendly message on any reject.
    """
    url = url.strip()
    if "?" in url or "#" in url:
        raise FetchError(
            f"Submission URL must not contain `?` or `#`: {url!r}. "
            "Provide a clean URL without query strings or fragments."
        )
    if not url.startswith("https://"):
        raise FetchError(
            f"Submission URL must use https://: {url!r}. "
            "Accepted forms: https://github.com/owner/repo, "
            "https://github.com/owner/repo/tree/<sha>, "
            "https://github.com/owner/repo/commit/<sha>, "
            "https://gist.github.com/user/<id>."
        )
    rest = url[len("https://") :]
    if rest.startswith("github.com/"):
        return _parse_github_repo_url(rest[len("github.com/") :], url)
    if rest.startswith("gist.github.com/"):
        return _parse_gist_url(rest[len("gist.github.com/") :], url)
    raise FetchError(
        f"Unsupported host in submission URL: {url!r}. "
        "Only github.com and gist.github.com are accepted."
    )


def _parse_github_repo_url(path: str, original: str) -> SourceDescriptor:
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    root_match = re.fullmatch(rf"(?P<owner>{OWNER_RE})/(?P<repo>{REPO_RE})", path)
    if root_match is not None:
        return SourceDescriptor(
            kind="github_repo",
            owner=root_match.group("owner"),
            name=root_match.group("repo"),
            ref=None,
        )
    tree_match = re.fullmatch(
        rf"(?P<owner>{OWNER_RE})/(?P<repo>{REPO_RE})/(?:tree|commit)/(?P<ref>{REF_RE})",
        path,
    )
    if tree_match is not None:
        return SourceDescriptor(
            kind="github_repo",
            owner=tree_match.group("owner"),
            name=tree_match.group("repo"),
            ref=tree_match.group("ref"),
        )
    raise FetchError(
        f"GitHub URL has unsupported shape: {original!r}. "
        "Accepted forms: /owner/repo, /owner/repo/tree/<ref>, "
        "/owner/repo/commit/<sha>."
    )


def _parse_gist_url(path: str, original: str) -> SourceDescriptor:
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    bare_match = re.fullmatch(
        rf"(?P<user>{GIST_USER_RE})/(?P<gid>{GIST_ID_RE})",
        path,
    )
    if bare_match is not None:
        return SourceDescriptor(
            kind="gist",
            owner=bare_match.group("user"),
            name=bare_match.group("gid"),
            ref=None,
        )
    rev_match = re.fullmatch(
        rf"(?P<user>{GIST_USER_RE})/(?P<gid>{GIST_ID_RE})/(?P<rev>{GIST_ID_RE})",
        path,
    )
    if rev_match is not None:
        return SourceDescriptor(
            kind="gist",
            owner=rev_match.group("user"),
            name=rev_match.group("gid"),
            ref=rev_match.group("rev"),
        )
    raise FetchError(
        f"Gist URL has unsupported shape: {original!r}. "
        "Accepted forms: https://gist.github.com/user/<id> or "
        "https://gist.github.com/user/<id>/<revision>."
    )


def _api_get(url: str, token: str | None) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "lean-eval-submission-fetcher")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise FetchError(
            f"GitHub API returned {exc.code} for {url}. "
            "If this is a private repository, install the `lean-eval-bot` GitHub App on it."
        ) from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"Failed to reach GitHub API at {url}: {exc.reason}") from exc


def resolve_repo_visibility(
    descriptor: SourceDescriptor, token: str | None
) -> bool:
    """Return True if the content is public, False if private/secret.

    Secret gists are treated as private and rejected by the caller.
    """
    if descriptor.kind == "github_repo":
        data = _api_get(
            f"https://api.github.com/repos/{descriptor.owner}/{descriptor.name}",
            token,
        )
        if not isinstance(data.get("private"), bool):
            raise FetchError(
                f"GitHub API response for {descriptor.owner}/{descriptor.name} "
                "did not include a boolean `private` field."
            )
        return not data["private"]
    if descriptor.kind == "gist":
        data = _api_get(
            f"https://api.github.com/gists/{descriptor.name}",
            token,
        )
        if not isinstance(data.get("public"), bool):
            raise FetchError(
                f"GitHub API response for gist {descriptor.name} "
                "did not include a boolean `public` field."
            )
        return data["public"]
    raise FetchError(f"Unknown descriptor kind: {descriptor.kind}")


def _run_git(args: list[str], *, cwd: pathlib.Path | None = None) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = "\n".join(part for part in [stderr, stdout] if part)
        raise FetchError(f"git {' '.join(args)} failed:\n{details}")


def clone_url_for(descriptor: SourceDescriptor, token: str | None) -> str:
    """Build the HTTPS clone URL, injecting the App token for private repos only."""
    if descriptor.kind == "github_repo":
        if token:
            return (
                f"https://x-access-token:{token}@github.com/"
                f"{descriptor.owner}/{descriptor.name}.git"
            )
        return f"https://github.com/{descriptor.owner}/{descriptor.name}.git"
    if descriptor.kind == "gist":
        # Gists do not need authentication; even secret gists are clonable with the URL.
        return f"https://gist.github.com/{descriptor.owner}/{descriptor.name}.git"
    raise FetchError(f"Unknown descriptor kind: {descriptor.kind}")


def resolve_ref(
    descriptor: SourceDescriptor, clone_url: str
) -> str:
    """Resolve a descriptor to a concrete 40-char SHA.

    For commit-SHA refs, passes them through unchanged after format-checking.
    For branch/tag refs, uses `git ls-remote`.
    For refs that are `None`, uses HEAD.
    """
    ref = descriptor.ref
    if ref is not None and SHA_RE.fullmatch(ref):
        return ref
    lookup_ref = ref or "HEAD"
    result = subprocess.run(
        ["git", "ls-remote", clone_url, lookup_ref],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise FetchError(
            f"git ls-remote {clone_url} {lookup_ref} failed:\n{stderr}"
        )
    output = (result.stdout or "").strip()
    if not output:
        raise FetchError(
            f"Ref {lookup_ref!r} not found in {descriptor.owner}/{descriptor.name}."
        )
    first_line = output.splitlines()[0]
    sha, _, _ = first_line.partition("\t")
    sha = sha.strip()
    if not SHA_RE.fullmatch(sha):
        raise FetchError(
            f"git ls-remote returned unexpected SHA {sha!r} for {lookup_ref}."
        )
    return sha


def clone_at_sha(
    clone_url: str, sha: str, destination: pathlib.Path
) -> None:
    """Clone a specific commit into `destination` using the fetch-by-sha pattern.

    Avoids shallow-clone-plus-checkout which fails when the target commit is
    not reachable from the default branch in a shallow fetch.
    """
    destination.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "--quiet"], cwd=destination)
    _run_git(["remote", "add", "origin", clone_url], cwd=destination)
    _run_git(["fetch", "--depth=1", "origin", sha], cwd=destination)
    _run_git(["checkout", "--quiet", "FETCH_HEAD"], cwd=destination)


def guard_no_path_escape(root: pathlib.Path) -> None:
    """Reject if any file inside `root` resolves outside `root`."""
    resolved_root = root.resolve()
    for path in root.rglob("*"):
        try:
            resolved = path.resolve(strict=False)
        except OSError as exc:
            raise FetchError(f"Failed to resolve {path}: {exc}") from exc
        try:
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise FetchError(
                f"Path escape detected: {path} resolves to {resolved}, "
                f"outside {resolved_root}."
            ) from exc


def tar_source(source_dir: pathlib.Path, tar_path: pathlib.Path) -> None:
    tar_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(source_dir, arcname=source_dir.name)


def submission_repo_identifier(descriptor: SourceDescriptor) -> str:
    if descriptor.kind == "github_repo":
        return f"{descriptor.owner}/{descriptor.name}"
    if descriptor.kind == "gist":
        return f"{descriptor.owner}/{descriptor.name}"
    raise FetchError(f"Unknown descriptor kind: {descriptor.kind}")


def fetch_submission(
    *,
    event_payload: dict,
    output_dir: pathlib.Path,
    app_token: str | None,
    skip_clone: bool = False,
) -> dict:
    issue = event_payload.get("issue")
    if not isinstance(issue, dict):
        raise FetchError("Event payload is missing the `issue` object.")
    body = issue.get("body")
    if not isinstance(body, str) or not body.strip():
        raise FetchError("Issue body is empty.")
    issue_number = issue.get("number")
    if not isinstance(issue_number, int):
        raise FetchError("Event payload is missing `issue.number`.")
    user = issue.get("user")
    if not isinstance(user, dict):
        raise FetchError("Event payload is missing `issue.user`.")
    submitted_by = user.get("login")
    if not isinstance(submitted_by, str) or not submitted_by:
        raise FetchError("Event payload is missing `issue.user.login`.")

    fields = parse_issue_body(body)
    descriptor = parse_source_url(fields["source_url"])
    clone_url = clone_url_for(descriptor, app_token)
    sha = resolve_ref(descriptor, clone_url)

    submission_public = resolve_repo_visibility(descriptor, app_token)
    if descriptor.kind == "gist" and not submission_public:
        raise FetchError(
            "Secret (unlisted) gists are rejected in v1. "
            "Make your gist public, or host your proof in a public or "
            "App-accessible private GitHub repository."
        )

    publication_status = fields["solution_publication_status"]
    if publication_status == "published" and not submission_public:
        raise FetchError(
            "The submission source is private, but its publication status is "
            "`Public`. Choose a private publication status, or make the source "
            "public before submitting."
        )
    if publication_status in {"planned", "private"} and submission_public:
        raise FetchError(
            "The submission source is public, but its publication status is "
            "private. Choose `Public` and provide its publication date, or make "
            "the source private before submitting."
        )

    source_dir = output_dir / "source"
    if not skip_clone:
        clone_at_sha(clone_url, sha, source_dir)
        # For private-repo submissions, `clone_url` embeds the
        # `lean-eval-bot` App installation token in the `origin` remote
        # URL, which `git remote add` persists into `.git/config`.
        # Comparator's landrun policy is `--ro /`, so anything left on
        # the runner under a path the sandbox can stat is readable by
        # the untrusted Lean elaborator; dropping `.git` here keeps the
        # token (and any other VCS metadata) out of `source.tar.gz` and
        # out of the workspace the elaborator overlays from. If the
        # auth-injection in `clone_url_for` ever moves to
        # `http.extraheader` / credential helper, this strip becomes
        # belt-and-braces but can stay.
        shutil.rmtree(source_dir / ".git")
        guard_no_path_escape(source_dir)
        tar_source(source_dir, output_dir / "source.tar.gz")

    metadata = {
        "source_url": fields["source_url"],
        "submission_kind": descriptor.kind,
        "submission_repo": submission_repo_identifier(descriptor),
        "submission_ref": sha,
        "submission_public": submission_public,
        "model": fields["model"],
        "submitted_by": submitted_by,
        "issue_number": issue_number,
    }
    if fields["production_description"] is not None:
        metadata["production_description"] = fields["production_description"]
    if publication_status is not None:
        metadata["solution_publication_status"] = publication_status
        if fields["solution_publication_date"] is not None:
            metadata["solution_publication_date"] = fields[
                "solution_publication_date"
            ]
    metadata_path = output_dir / "metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


SERVER_INPUT_FIELDS = {
    "archive_locator_required",
    "archive_sidecar_schema",
    "declared_model",
    "problem_group",
    "problem_id",
    "production_metadata_json",
    "publication_choice",
    "source_commit",
    "source_repository",
    "source_visibility",
    "statement_revision",
    "submission_id",
    "submitted_by",
    "workflow_commit",
}
SERVER_METADATA_FIELDS = {
    "billing_mode",
    "component_models",
    "cost_usd",
    "credit_identity",
    "harness",
    "human_involvement",
    "input_tokens",
    "notes",
    "output_tokens",
    "prompt",
    "wall_time_seconds",
    "web_access",
}


def _server_text(inputs: dict, field: str, maximum: int) -> str:
    value = inputs.get(field)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) <= 31 or ord(character) == 127 for character in value)
    ):
        raise FetchError(f"server input {field!r} is invalid")
    return value


def _validate_server_metadata(metadata: dict) -> None:
    if not set(metadata) <= SERVER_METADATA_FIELDS:
        raise FetchError("server production metadata has unknown fields")
    text_limits = {
        "credit_identity": 256,
        "harness": 1024,
        "human_involvement": 1024,
        "prompt": 8192,
        "notes": 4096,
    }
    for field, maximum in text_limits.items():
        if field in metadata:
            _server_text(metadata, field, maximum)
    components = metadata.get("component_models")
    if "component_models" in metadata:
        if not isinstance(components, list) or len(components) > 16:
            raise FetchError("server component_models is invalid")
        for index, value in enumerate(components):
            _server_text({str(index): value}, str(index), 256)
    if "web_access" in metadata and not isinstance(metadata["web_access"], bool):
        raise FetchError("server web_access is invalid")
    for field in ("input_tokens", "output_tokens"):
        value = metadata.get(field)
        if field in metadata and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > 9_007_199_254_740_991
        ):
            raise FetchError(f"server {field} is invalid")
    for field, maximum in (("wall_time_seconds", 31_536_000), ("cost_usd", 1_000_000)):
        value = metadata.get(field)
        if field in metadata and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or value > maximum
        ):
            raise FetchError(f"server {field} is invalid")
    if "billing_mode" in metadata and metadata.get("billing_mode") not in {"api", "subscription", "unknown"}:
        raise FetchError("server billing_mode is invalid")


def fetch_server_submission(
    *,
    inputs: dict,
    output_dir: pathlib.Path,
    app_token: str | None,
    skip_clone: bool = False,
) -> dict:
    """Strictly decode and fetch one Worker-originated exact-ref submission."""
    if set(inputs) != SERVER_INPUT_FIELDS:
        raise FetchError("server dispatch has unknown or missing input fields")
    if inputs.get("archive_locator_required") != "true" or inputs.get("archive_sidecar_schema") != "2":
        raise FetchError("server dispatch does not require the UUID archive locator contract")
    submission_id = _server_text(inputs, "submission_id", 36)
    submitted_by = _server_text(inputs, "submitted_by", 39)
    source_repository = _server_text(inputs, "source_repository", 200)
    source_commit = _server_text(inputs, "source_commit", 40)
    source_visibility = _server_text(inputs, "source_visibility", 7)
    problem_id = _server_text(inputs, "problem_id", 128)
    problem_group = _server_text(inputs, "problem_group", 64)
    declared_model = _server_text(inputs, "declared_model", 256)
    publication_choice = _server_text(inputs, "publication_choice", 16)
    workflow_commit = _server_text(inputs, "workflow_commit", 40)
    if not UUIDV7_RE.fullmatch(submission_id):
        raise FetchError("server submission_id is not a canonical lowercase UUIDv7")
    if not LOGIN_RE.fullmatch(submitted_by):
        raise FetchError("server submitted_by is not a canonical lowercase GitHub login")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source_repository):
        raise FetchError("server source_repository is not canonical")
    if not SHA_RE.fullmatch(source_commit):
        raise FetchError("server source_commit is not an exact lowercase commit")
    if not PROBLEM_RE.fullmatch(problem_id):
        raise FetchError("server problem_id is not canonical")
    if problem_group not in {
        "formalization-evaluation",
        "software-verification",
        "open-conjectures",
    }:
        raise FetchError("server problem_group is invalid")
    if source_visibility not in {"private", "public"}:
        raise FetchError("server source_visibility is invalid")
    if publication_choice not in {"scheduled", "withheld"}:
        raise FetchError("server publication_choice is invalid")
    if not SHA_RE.fullmatch(workflow_commit):
        raise FetchError("server workflow_commit is not an exact lowercase commit")
    try:
        statement_revision = int(_server_text(inputs, "statement_revision", 16))
    except ValueError as error:
        raise FetchError("server statement_revision is invalid") from error
    if statement_revision < 1 or statement_revision > 9_007_199_254_740_991:
        raise FetchError("server statement_revision is invalid")
    try:
        production_metadata = json.loads(
            _server_text(inputs, "production_metadata_json", 12_000)
        )
    except json.JSONDecodeError as error:
        raise FetchError("server production_metadata_json is invalid") from error
    if not isinstance(production_metadata, dict):
        raise FetchError("server production_metadata_json must decode to an object")
    _validate_server_metadata(production_metadata)

    descriptor = parse_source_url(
        f"https://github.com/{source_repository}/commit/{source_commit}"
    )
    clone_url = clone_url_for(descriptor, app_token)
    sha = resolve_ref(descriptor, clone_url)
    if sha != source_commit:
        raise FetchError("server source did not resolve to the exact requested commit")
    submission_public = resolve_repo_visibility(descriptor, app_token)
    if submission_public != (source_visibility == "public"):
        raise FetchError("server source visibility changed after Worker verification")
    required_visibility = "public" if problem_group == "open-conjectures" else "private"
    if source_visibility != required_visibility:
        raise FetchError(
            f"server {problem_group} submission requires {required_visibility} source"
        )

    source_dir = output_dir / "source"
    if not skip_clone:
        clone_at_sha(clone_url, sha, source_dir)
        shutil.rmtree(source_dir / ".git")
        guard_no_path_escape(source_dir)
        tar_source(source_dir, output_dir / "source.tar.gz")
    metadata = {
        "schema_version": 2,
        "submission_id": submission_id,
        "source_url": f"https://github.com/{source_repository}/commit/{source_commit}",
        "submission_kind": "github_repo",
        "submission_repo": source_repository,
        "submission_ref": source_commit,
        "submission_public": submission_public,
        "model": declared_model,
        "submitted_by": submitted_by,
        "problem_id": problem_id,
        "problem_group": problem_group,
        "statement_revision": statement_revision,
        "publication_choice": publication_choice,
        "production_metadata": production_metadata,
    }
    metadata_path = output_dir / "metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-path",
        type=pathlib.Path,
        default=None,
        help="Path to the GitHub issue event payload JSON. "
        "Defaults to $GITHUB_EVENT_PATH.",
    )
    parser.add_argument(
        "--server-dispatch",
        action="store_true",
        help="Decode the strict workflow_dispatch inputs contract instead of an issue.",
    )
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        required=True,
        help="Directory to write source.tar.gz and metadata.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the clone; only parse the URL and emit metadata.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        event_path = args.event_path or pathlib.Path(
            os.environ.get("GITHUB_EVENT_PATH", "")
        )
        if not event_path or not event_path.is_file():
            raise FetchError(
                "No event payload path provided. Set $GITHUB_EVENT_PATH or pass --event-path."
            )
        event_payload = json.loads(event_path.read_text(encoding="utf-8"))
        app_token = os.environ.get("APP_INSTALLATION_TOKEN") or None
        if args.server_dispatch:
            inputs = event_payload.get("inputs")
            if not isinstance(inputs, dict):
                raise FetchError("server dispatch payload is missing inputs")
            metadata = fetch_server_submission(
                inputs=inputs,
                output_dir=args.output_dir,
                app_token=app_token,
                skip_clone=args.dry_run,
            )
        else:
            metadata = fetch_submission(
                event_payload=event_payload,
                output_dir=args.output_dir,
                app_token=app_token,
                skip_clone=args.dry_run,
            )
    except FetchError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
