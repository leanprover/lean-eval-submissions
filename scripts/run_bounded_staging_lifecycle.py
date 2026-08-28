#!/usr/bin/env python3
"""Run the temporary, bounded staging lifecycle acceptance.

This is deliberately an operator-side, two-phase driver.  It has no stored
credentials, never logs the signed agent challenge, and is retired with its
fixture and watchdog after one accepted run.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "configuration" / "staging-lifecycle-smoke-v1.json"
EXPECTED_FIXTURE_SHA256 = (
    "bf3941a2641a5b599c2395e9250031559678709686990e708fa0c7cb0a3e985e"
)
UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
RESULT = re.compile(r"r2_[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
GIST = re.compile(r"[0-9a-f]{20,64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
MAX_GIST_FILES = 16
MAX_GIST_BYTES = 1024 * 1024
RELEASE_JOB_NAMES = frozenset({"authorize-manual", "prepare-one", "unwrap-one"})


class AcceptanceError(RuntimeError):
    pass


class SubmissionTerminalError(AcceptanceError):
    pass


def closed_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AcceptanceError("fixture must be a JSON object")
    return value


def require_match(pattern: re.Pattern[str], value: str, label: str) -> str:
    if pattern.fullmatch(value) is None:
        raise AcceptanceError(f"{label} is not canonical")
    return value


def run(
    command: list[str],
    *,
    capture: bool = True,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        raise AcceptanceError(f"command failed: {command[0]}: {detail}")
    return (completed.stdout or "").strip()


def gh_json(
    args: list[str], *, method: str = "GET", fields: dict[str, Any] | None = None
) -> Any:
    command = ["gh", "api", "--method", method, *args]
    if fields is not None:
        command.extend(["--input", "-"])
        completed = subprocess.run(
            command,
            input=json.dumps(fields, separators=(",", ":")),
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AcceptanceError(
                f"GitHub API request failed: {completed.stderr.strip()}"
            )
        output = completed.stdout
    else:
        output = run(command)
    return json.loads(output) if output else None


class Api:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        bearer: str | None = None,
        idempotency_key: str | None = None,
        expected: int | tuple[int, ...] = (200, 201),
    ) -> dict[str, Any]:
        data = (
            None if body is None else json.dumps(body, separators=(",", ":")).encode()
        )
        headers = {
            "accept": "application/json",
            "user-agent": "lean-eval-bounded-staging-acceptance",
        }
        if data is not None:
            headers["content-type"] = "application/json"
        if bearer is not None:
            headers["authorization"] = f"Bearer {bearer}"
        if idempotency_key is not None:
            headers["idempotency-key"] = idempotency_key
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status = response.status
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            status = error.code
            payload = json.load(error)
        accepted = (expected,) if isinstance(expected, int) else expected
        if status not in accepted:
            raise AcceptanceError(
                f"{method} {path}: expected HTTP {accepted}, got {status}; response redacted"
            )
        if not isinstance(payload, dict):
            raise AcceptanceError(f"{method} {path}: response was not an object")
        return payload


def event_id() -> str:
    # UUIDv7 layout, sufficient for an idempotency identity allocated locally.
    timestamp = int(time.time() * 1000)
    raw = bytearray(timestamp.to_bytes(6, "big") + os.urandom(10))
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def health(api: Api, commit: str, enabled: bool | None) -> bool:
    body = api.request("GET", "/healthz")
    if body.get("deployed_commit") != commit or body.get("environment") != "staging":
        raise AcceptanceError("staging health is not bound to the expected commit")
    fields = (
        "intake_configured_enabled",
        "intake_effective_enabled",
        "intake_enabled",
        "legacy_result_owner_api_enabled",
        "result_amendment_owner_api_enabled",
        "result_amendment_maintainer_api_enabled",
        "model_identity_owner_api_enabled",
        "model_identity_maintainer_api_enabled",
        "release_opt_out_api_enabled",
    )
    values = [body.get(field) for field in fields]
    if enabled is None:
        if any(value is not values[0] for value in values) or values[0] not in {
            True,
            False,
        }:
            raise AcceptanceError("staging launch gates are in a partial state")
        enabled = values[0]
    elif any(value is not enabled for value in values):
        raise AcceptanceError("staging launch gates do not match the expected phase")
    if body.get("model_identity_consolidation_api_enabled") is not False:
        raise AcceptanceError("model consolidation must remain disabled")
    return enabled


def verify_candidate_checkout(expected_commit: str, fixture: dict[str, Any]) -> None:
    if run(["git", "rev-parse", "HEAD"], cwd=ROOT) != expected_commit:
        raise AcceptanceError("driver checkout is not the exact expected candidate")
    if run(["git", "status", "--porcelain"], cwd=ROOT):
        raise AcceptanceError("driver checkout is not clean")
    origin = run(["git", "remote", "get-url", "origin"], cwd=ROOT)
    if origin not in {
        "https://github.com/leanprover/lean-eval-submissions",
        "https://github.com/leanprover/lean-eval-submissions.git",
        "git@github.com:leanprover/lean-eval-submissions.git",
    }:
        raise AcceptanceError("driver checkout origin is not canonical")
    tag = f"refs/tags/lean-eval-dispatch/{expected_commit}^{{commit}}"
    if run(["git", "rev-parse", tag], cwd=ROOT) != expected_commit:
        raise AcceptanceError(
            "exact immutable dispatch tag is absent from the checkout"
        )
    raw = DEFAULT_FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_FIXTURE_SHA256:
        raise AcceptanceError("canonical staging fixture digest drifted")
    canonical = (
        json.dumps(fixture, ensure_ascii=True, indent=2, sort_keys=False) + "\n"
    ).encode()
    if raw != canonical:
        raise AcceptanceError("staging fixture bytes are not canonical JSON")


def fixture_preflight(
    fixture: dict[str, Any], gist_id: str, expected_commit: str
) -> None:
    source = fixture["source"]
    bounded = fixture["bounded_acceptance"]
    verify_candidate_checkout(expected_commit, fixture)
    if source != {
        "repository": "kim-em/lean-eval-intake-fixture",
        "commit": "c1e504a3acce2da2ee77054ca45e6c251b143545",
        "owner_login": "kim-em",
    }:
        raise AcceptanceError("fixture source authority drifted")
    if bounded["retire_after"] != "one accepted bounded staging lifecycle run":
        raise AcceptanceError("retirement condition drifted")
    expected_bounded = {
        "submission_base_url": "https://lean-eval-submission-server-staging.lean-eval.workers.dev",
        "state_repository": "leanprover/lean-eval-state-staging",
        "state_branch": "main",
        "state_contract_commit": "23852beaeb059c88caf043d22dad19b211c377b2",
        "results_repository": "leanprover/lean-eval-submissions",
        "results_branch": "staging-results",
        "release_repository": "leanprover/lean-eval-releases",
        "release_workflow": "credentialed-release-staging-smoke.yml",
        "release_ref": "main",
        "release_commit": "41a65cc7db88d5742fd804648ba9d550b8e86edb",
        "release_run_name_prefix": "Reconstruct staging submission ",
        "fixture_gist_file": "lean-eval-proof.txt",
        "retire_after": "one accepted bounded staging lifecycle run",
    }
    if set(bounded) != {*expected_bounded, "state_script_sha256"} or any(
        bounded.get(key) != value for key, value in expected_bounded.items()
    ):
        raise AcceptanceError("bounded staging acceptance authority drifted")
    scripts = bounded["state_script_sha256"]
    if (
        not isinstance(scripts, dict)
        or set(scripts)
        != {
            "scripts/materialize_state.py",
            "scripts/model_identity.py",
            "scripts/probe_performance_counters.py",
            "scripts/public_projection.py",
            "scripts/result_amendments.py",
            "scripts/result_effective_identities.py",
            "scripts/result_owner_indexes.py",
            "scripts/result_release_status.py",
            "scripts/state.py",
            "scripts/validate_state.py",
        }
        or any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None for value in scripts.values()
        )
    ):
        raise AcceptanceError("reviewed State script digest set drifted")
    user = gh_json(["user"])
    if user.get("login", "").lower() != source["owner_login"]:
        raise AcceptanceError("gh must be authenticated as the exact fixture owner")
    commit = gh_json([f"repos/{source['repository']}/commits/{source['commit']}"])
    if commit.get("sha") != source["commit"]:
        raise AcceptanceError("fixture commit is unavailable")
    gist = gh_json([f"gists/{gist_id}"])
    if gist.get("owner", {}).get("login", "").lower() != source["owner_login"]:
        raise AcceptanceError("gist is not owned by the exact fixture owner")
    if gist.get("public") is not False:
        raise AcceptanceError("proof gist must be secret")
    gist_git_snapshot(gist, gist_id, bounded["fixture_gist_file"])
    release = gh_json(
        [f"repos/{bounded['release_repository']}/commits/{bounded['release_ref']}"]
    )
    if release.get("sha") != bounded["release_commit"]:
        raise AcceptanceError("publication-disabled release workflow commit drifted")
    for repository_key, branch_key in (
        ("state_repository", "state_branch"),
        ("results_repository", "results_branch"),
        ("release_repository", "release_ref"),
    ):
        branch = gh_json(
            [f"repos/{bounded[repository_key]}/branches/{bounded[branch_key]}"]
        )
        if branch.get("protected") is not True:
            raise AcceptanceError(f"{bounded[repository_key]} branch is not protected")
    health(Api(bounded["submission_base_url"]), expected_commit, None)


def gh_json_or_authenticated_404(args: list[str]) -> Any | None:
    completed = subprocess.run(
        ["gh", "api", "--method", "GET", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise AcceptanceError("GitHub API success was not JSON") from error
    diagnostic = completed.stderr.strip()
    if diagnostic == "gh: Not Found (HTTP 404)":
        return None
    raise AcceptanceError(f"GitHub API lookup did not return exact 404: {diagnostic}")


def verify_exact_tag_response(
    value: Any, repository: str, tag: str, commit: str
) -> None:
    if not isinstance(value, dict) or set(value) != {"ref", "node_id", "url", "object"}:
        raise AcceptanceError(
            f"GitHub returned an inexact tag response for {repository}"
        )
    target = value.get("object")
    if (
        value.get("ref") != f"refs/tags/{tag}"
        or not isinstance(value.get("node_id"), str)
        or not isinstance(value.get("url"), str)
        or not isinstance(target, dict)
        or set(target) != {"sha", "type", "url"}
        or target.get("sha") != commit
        or target.get("type") != "commit"
        or not isinstance(target.get("url"), str)
    ):
        raise AcceptanceError(
            f"GitHub returned an inexact tag response for {repository}"
        )


def gist_file_content(
    value: Any, gist_id: str, filename: str
) -> tuple[bool, str | None]:
    if (
        not isinstance(value, dict)
        or value.get("id") != gist_id
        or value.get("public") is not False
        or value.get("owner", {}).get("login", "").lower() != "kim-em"
        or not isinstance(value.get("files"), dict)
    ):
        raise AcceptanceError("secret gist response identity drifted")
    files = value["files"]
    if len(files) > MAX_GIST_FILES:
        raise AcceptanceError("secret gist exceeds the bounded file count")
    total_bytes = 0
    for name, candidate in files.items():
        if (
            not isinstance(name, str)
            or pathlib.PurePosixPath(name).name != name
            or not isinstance(candidate, dict)
            or candidate.get("truncated") is not False
            or not isinstance(candidate.get("content"), str)
        ):
            raise AcceptanceError("secret gist contains an unsupported file")
        total_bytes += len(candidate["content"].encode())
        if total_bytes > MAX_GIST_BYTES:
            raise AcceptanceError("secret gist exceeds the bounded content size")
    file = files.get(filename)
    if file is None:
        return False, None
    return True, file["content"]


def gist_git_snapshot(
    value: Any, gist_id: str, filename: str
) -> tuple[str, bool, str | None]:
    present, content = gist_file_content(value, gist_id, filename)
    if value.get("git_pull_url") != f"https://gist.github.com/{gist_id}.git":
        raise AcceptanceError("secret gist Git authority drifted")
    history = value.get("history")
    if (
        not isinstance(history, list)
        or not history
        or not isinstance(history[0], dict)
        or not isinstance(history[0].get("version"), str)
    ):
        raise AcceptanceError("secret gist Git head is absent")
    head = require_match(COMMIT, history[0]["version"], "secret gist Git head")
    return head, present, content


def secret_git_environment(temporary: pathlib.Path) -> dict[str, str]:
    gh_config = pathlib.Path(
        os.environ.get("GH_CONFIG_DIR", pathlib.Path.home() / ".config" / "gh")
    ).resolve()
    if not gh_config.is_dir():
        raise AcceptanceError("gh configuration directory is unavailable")
    isolated_home = temporary / "home"
    isolated_home.mkdir(mode=0o700)
    # This closed environment intentionally excludes GH_DEBUG, GIT_TRACE,
    # GIT_CURL_VERBOSE, shell tracing, cloud credentials, and agent sockets.
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(isolated_home),
        "GH_CONFIG_DIR": str(gh_config),
        "GH_PROMPT_DISABLED": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
    }


def secret_git(
    args: list[str], *, cwd: pathlib.Path, env: dict[str, str], label: str
) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise AcceptanceError(
            f"secret Gist Git {label} timed out; diagnostics redacted"
        ) from None
    if completed.returncode != 0:
        raise AcceptanceError(f"secret Gist Git {label} failed; diagnostics redacted")
    return completed.stdout.strip()


def prepare_gist_git(
    gist_id: str, expected_head: str, *, depth: int
) -> tuple[tempfile.TemporaryDirectory, pathlib.Path, dict[str, str]]:
    if (
        run(["findmnt", "--noheadings", "--output", "FSTYPE", "--target", "/dev/shm"])
        != "tmpfs"
    ):
        raise AcceptanceError("secret Gist Git transport requires /dev/shm tmpfs")
    temporary = tempfile.TemporaryDirectory(
        prefix="lean-eval-gist-cas-", dir="/dev/shm"
    )
    root = pathlib.Path(temporary.name)
    repository = root / "gist"
    repository.mkdir(mode=0o700)
    env = secret_git_environment(root)
    try:
        secret_git(["init", "--quiet"], cwd=repository, env=env, label="initialization")
        for key, value in (
            ("credential.helper", "!gh auth git-credential"),
            ("credential.useHttpPath", "true"),
            ("core.hooksPath", "/dev/null"),
            ("user.name", "LeanEval bounded staging operator"),
            ("user.email", "lean-eval-staging@users.noreply.github.com"),
        ):
            secret_git(
                ["config", "--local", key, value],
                cwd=repository,
                env=env,
                label="configuration",
            )
        secret_git(
            ["remote", "add", "origin", f"https://gist.github.com/{gist_id}.git"],
            cwd=repository,
            env=env,
            label="remote binding",
        )
        secret_git(
            [
                "fetch",
                "--quiet",
                "--no-tags",
                f"--depth={depth}",
                "origin",
                "+refs/heads/master:refs/remotes/origin/master",
            ],
            cwd=repository,
            env=env,
            label="fetch",
        )
        actual = secret_git(
            ["rev-parse", "refs/remotes/origin/master"],
            cwd=repository,
            env=env,
            label="head verification",
        )
        if actual != expected_head:
            raise AcceptanceError("secret gist changed before atomic update")
        secret_git(
            ["checkout", "--quiet", "--detach", expected_head],
            cwd=repository,
            env=env,
            label="checkout",
        )
        return temporary, repository, env
    except BaseException:
        temporary.cleanup()
        raise


def verify_gist_worktree_file(
    repository: pathlib.Path,
    filename: str,
    expected_present: bool,
    expected_content: str | None,
) -> None:
    if pathlib.PurePosixPath(filename).name != filename:
        raise AcceptanceError("secret gist proof filename is not a basename")
    path = repository / filename
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if expected_present:
            raise AcceptanceError("secret gist proof file disappeared") from None
        return
    if not expected_present:
        raise AcceptanceError("secret gist proof file appeared unexpectedly")
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise AcceptanceError("secret gist proof file is not a regular file")
    if path.read_text(encoding="utf-8") != expected_content:
        raise AcceptanceError("secret gist proof file content drifted")


@dataclass
class FixtureMutation:
    gist_id: str
    filename: str
    repository: str
    commit: str
    tag: str
    prior_file_present: bool
    prior_file_content: str | None
    prior_gist_head: str | None = None
    written_gist_head: str | None = None
    written_file_content: str | None = None
    written_file_sha256: str | None = None
    gist_changed: bool = False
    gist_restore_started: bool = False
    tag_created: bool = False

    @property
    def tag_endpoint(self) -> str:
        return f"repos/{self.repository}/git/ref/tags/{self.tag}"

    def describe_targets(self) -> str:
        return (
            f"gist={self.gist_id} file={self.filename} "
            f"head={self.prior_gist_head}; "
            f"repository={self.repository} tag=refs/tags/{self.tag} commit={self.commit}"
        )


def require_target_bound_approval(mutation: FixtureMutation) -> None:
    confirmation = (
        f"APPROVE GIST {mutation.gist_id}/{mutation.filename}@{mutation.prior_gist_head} AND TAG "
        f"{mutation.repository}/refs/tags/{mutation.tag}@{mutation.commit} WITH EXACT CLEANUP"
    )
    print("\nExternal fixture approval is now required.")
    print(f"Exact temporary targets: {mutation.describe_targets()}")
    print("The proof value is intentionally not displayed.")
    print("After obtaining explicit user approval, type this exact nonsecret line:")
    print(confirmation)
    supplied = input("> ")
    if supplied != confirmation:
        raise AcceptanceError("target-bound external approval was not supplied")


def gist_cas_write(mutation: FixtureMutation) -> None:
    if (
        mutation.prior_gist_head is None
        or mutation.written_file_content is None
        or mutation.written_file_sha256 is None
    ):
        raise AcceptanceError("secret gist CAS write identity is incomplete")
    temporary, repository, env = prepare_gist_git(
        mutation.gist_id, mutation.prior_gist_head, depth=1
    )
    try:
        verify_gist_worktree_file(
            repository,
            mutation.filename,
            mutation.prior_file_present,
            mutation.prior_file_content,
        )
        path = repository / mutation.filename
        path.write_text(mutation.written_file_content, encoding="utf-8")
        path.chmod(0o600)
        secret_git(
            ["add", "--", mutation.filename],
            cwd=repository,
            env=env,
            label="proof staging",
        )
        secret_git(
            ["commit", "--quiet", "-m", "Temporary LeanEval agent proof"],
            cwd=repository,
            env=env,
            label="proof commit",
        )
        written_head = secret_git(
            ["rev-parse", "HEAD"],
            cwd=repository,
            env=env,
            label="proof identity",
        )
        mutation.written_gist_head = require_match(
            COMMIT, written_head, "written secret gist head"
        )
        secret_git(
            [
                "push",
                "--quiet",
                f"--force-with-lease=refs/heads/master:{mutation.prior_gist_head}",
                "origin",
                f"{mutation.written_gist_head}:refs/heads/master",
            ],
            cwd=repository,
            env=env,
            label="compare-and-swap write",
        )
        mutation.gist_changed = True
    finally:
        temporary.cleanup()
    current = gh_json([f"gists/{mutation.gist_id}"])
    head, present, content = gist_git_snapshot(
        current, mutation.gist_id, mutation.filename
    )
    if (
        head != mutation.written_gist_head
        or not present
        or content != mutation.written_file_content
        or hashlib.sha256(content.encode()).hexdigest() != mutation.written_file_sha256
    ):
        raise AcceptanceError("secret gist CAS write did not verify exactly")


def restore_gist(mutation: FixtureMutation) -> None:
    if not mutation.gist_changed:
        return
    if (
        mutation.prior_gist_head is None
        or mutation.written_gist_head is None
        or mutation.written_file_content is None
        or mutation.written_file_sha256 is None
    ):
        raise AcceptanceError("secret gist CAS restore identity is incomplete")
    current = gh_json([f"gists/{mutation.gist_id}"])
    head, present, content = gist_git_snapshot(
        current, mutation.gist_id, mutation.filename
    )
    if (
        mutation.gist_restore_started
        and head == mutation.prior_gist_head
        and present == mutation.prior_file_present
        and content == mutation.prior_file_content
    ):
        mutation.gist_changed = False
        mutation.gist_restore_started = False
        mutation.written_file_content = None
        mutation.written_file_sha256 = None
        return
    if (
        head != mutation.written_gist_head
        or not present
        or content != mutation.written_file_content
        or hashlib.sha256(content.encode()).hexdigest() != mutation.written_file_sha256
    ):
        raise AcceptanceError(
            "secret gist changed after this run's write; refusing CAS restore"
        )
    temporary, repository, env = prepare_gist_git(
        mutation.gist_id, mutation.written_gist_head, depth=2
    )
    try:
        verify_gist_worktree_file(
            repository,
            mutation.filename,
            True,
            mutation.written_file_content,
        )
        parent = secret_git(
            ["rev-parse", f"{mutation.written_gist_head}^"],
            cwd=repository,
            env=env,
            label="prior head verification",
        )
        if parent != mutation.prior_gist_head:
            raise AcceptanceError("secret gist proof commit parent drifted")
        mutation.gist_restore_started = True
        secret_git(
            [
                "push",
                "--quiet",
                f"--force-with-lease=refs/heads/master:{mutation.written_gist_head}",
                "origin",
                f"{mutation.prior_gist_head}:refs/heads/master",
            ],
            cwd=repository,
            env=env,
            label="compare-and-swap restore",
        )
    finally:
        temporary.cleanup()
    restored = gh_json([f"gists/{mutation.gist_id}"])
    restored_head, restored_present, restored_content = gist_git_snapshot(
        restored, mutation.gist_id, mutation.filename
    )
    if (
        restored_head != mutation.prior_gist_head
        or restored_present != mutation.prior_file_present
        or restored_content != mutation.prior_file_content
    ):
        raise AcceptanceError("secret gist CAS restoration did not verify")
    mutation.gist_changed = False
    mutation.gist_restore_started = False
    mutation.written_file_content = None
    mutation.written_file_sha256 = None


def remove_created_tag(mutation: FixtureMutation) -> None:
    if not mutation.tag_created:
        return
    current = gh_json_or_authenticated_404([mutation.tag_endpoint])
    if current is None:
        mutation.tag_created = False
        return
    verify_exact_tag_response(
        current, mutation.repository, mutation.tag, mutation.commit
    )
    response = gh_json([mutation.tag_endpoint], method="DELETE")
    if response is not None:
        raise AcceptanceError(
            "generated fixture tag deletion returned an inexact response"
        )
    if gh_json_or_authenticated_404([mutation.tag_endpoint]) is not None:
        raise AcceptanceError("generated fixture tag deletion did not verify")
    mutation.tag_created = False


def cleanup_fixture_mutation(mutation: FixtureMutation, *, remove_tag: bool) -> None:
    failures: list[str] = []
    try:
        restore_gist(mutation)
    except BaseException as error:  # noqa: BLE001 -- cleanup must survive interrupts
        failures.append(f"gist restoration: {error}")
    if remove_tag:
        try:
            remove_created_tag(mutation)
        except BaseException as error:  # noqa: BLE001 -- attempt both exact rollbacks
            failures.append(f"tag removal: {error}")
    if failures:
        raise AcceptanceError("; ".join(failures))


def fixture_cleanup_is_proved_safe(
    *, headless_post_started: bool, headless_terminal: bool
) -> bool:
    return not headless_post_started or headless_terminal


def apply_exact_proof_and_tag(
    fixture: dict[str, Any], gist_id: str, challenge: str, tag: str
) -> FixtureMutation:
    bounded = fixture["bounded_acceptance"]
    source = fixture["source"]
    gist = gh_json([f"gists/{gist_id}"])
    prior_head, prior_present, prior_content = gist_git_snapshot(
        gist, gist_id, bounded["fixture_gist_file"]
    )
    mutation = FixtureMutation(
        gist_id=gist_id,
        filename=bounded["fixture_gist_file"],
        repository=source["repository"],
        commit=source["commit"],
        tag=tag,
        prior_file_present=prior_present,
        prior_file_content=prior_content,
        prior_gist_head=prior_head,
        written_file_content=challenge,
        written_file_sha256=hashlib.sha256(challenge.encode()).hexdigest(),
    )
    if gh_json_or_authenticated_404([mutation.tag_endpoint]) is not None:
        raise AcceptanceError(
            "generated source tag already exists; refusing to move or reuse it"
        )
    require_target_bound_approval(mutation)
    print(f"Interruption rollback targets: {mutation.describe_targets()}")
    try:
        gist_cas_write(mutation)
        tag_response = gh_json(
            [f"repos/{source['repository']}/git/refs"],
            method="POST",
            fields={"ref": f"refs/tags/{tag}", "sha": source["commit"]},
        )
        verify_exact_tag_response(
            tag_response, source["repository"], tag, source["commit"]
        )
        mutation.tag_created = True
        verify_exact_tag_response(
            gh_json_or_authenticated_404([mutation.tag_endpoint]),
            source["repository"],
            tag,
            source["commit"],
        )
        return mutation
    except BaseException:
        try:
            ambiguous_tag = False
            current_gist = gh_json([f"gists/{gist_id}"])
            current_head, current_present, current_content = gist_git_snapshot(
                current_gist, gist_id, mutation.filename
            )
            if (
                mutation.written_gist_head is not None
                and current_head == mutation.written_gist_head
                and current_present
                and current_content == challenge
                and hashlib.sha256(current_content.encode()).hexdigest()
                == mutation.written_file_sha256
            ):
                mutation.gist_changed = True
            elif not (
                current_head == mutation.prior_gist_head
                and current_present == mutation.prior_file_present
                and current_content == mutation.prior_file_content
            ):
                raise AcceptanceError(
                    "proof gist changed to an unrecognized value; refusing broad cleanup"
                )
            current = gh_json_or_authenticated_404([mutation.tag_endpoint])
            if current is not None:
                verify_exact_tag_response(
                    current, mutation.repository, mutation.tag, mutation.commit
                )
                if not mutation.tag_created:
                    ambiguous_tag = True
            cleanup_fixture_mutation(mutation, remove_tag=True)
            if ambiguous_tag:
                raise AcceptanceError(
                    "generated tag creation outcome is ambiguous; refusing unproved deletion"
                )
        except BaseException as cleanup_error:  # noqa: BLE001 -- preserve original failure
            print(
                f"External cleanup still required: {mutation.describe_targets()} ({cleanup_error})",
                file=sys.stderr,
            )
        raise


def assert_state_event_absent(fixture: dict[str, Any], identifier: str) -> None:
    bounded = fixture["bounded_acceptance"]
    value = gh_json_or_authenticated_404(
        [
            f"repos/{bounded['state_repository']}/contents/events/{identifier[:2]}/{identifier}.json",
            "-f",
            f"ref={bounded['state_branch']}",
        ]
    )
    if value is not None:
        raise AcceptanceError(f"denied operation created State event {identifier}")


def state_branch_commit(fixture: dict[str, Any]) -> str:
    bounded = fixture["bounded_acceptance"]
    branch = gh_json(
        [f"repos/{bounded['state_repository']}/branches/{bounded['state_branch']}"]
    )
    if not isinstance(branch, dict) or not isinstance(branch.get("commit"), dict):
        raise AcceptanceError("staging State branch response drifted")
    return require_match(
        COMMIT, branch["commit"].get("sha", ""), "staging State branch commit"
    )


def mutation(
    api: Api,
    token: str,
    method: str,
    path: str,
    body: dict[str, Any],
    expected: int | tuple[int, ...] = (200, 201),
    *,
    denial_fixture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identifier = event_id()
    response = api.request(
        method, path, body, bearer=token, idempotency_key=identifier, expected=expected
    )
    if denial_fixture is not None:
        assert_state_event_absent(denial_fixture, identifier)
    return response


def workflow_run_ids(repository: str, workflow: str) -> set[int]:
    runs = gh_json(
        [
            f"repos/{repository}/actions/workflows/{workflow}/runs",
            "-f",
            "event=workflow_dispatch",
            "-f",
            "per_page=30",
        ]
    )
    return {item["id"] for item in runs.get("workflow_runs", [])}


def dispatch_gate_state(
    expected_commit: str, fixture: dict[str, Any], success: bool
) -> set[int]:
    profiles = fixture["maintainer_profiles"]
    identities = (
        profiles["success"]
        if success
        else profiles["authenticated_non_maintainer_denial"]
    )
    previous = workflow_run_ids(
        "leanprover/lean-eval-submissions", "set-staging-lifecycle-smoke.yml"
    )
    run(
        [
            "gh",
            "workflow",
            "run",
            "set-staging-lifecycle-smoke.yml",
            "--repo",
            "leanprover/lean-eval-submissions",
            "--ref",
            f"lean-eval-dispatch/{expected_commit}",
            "-f",
            "state=enabled",
            "-f",
            f"expected_commit={expected_commit}",
            "-f",
            f"result_amendment_maintainers={json.dumps(identities, separators=(',', ':'))}",
            "-f",
            f"model_identity_maintainers={json.dumps(identities, separators=(',', ':'))}",
            "-f",
            "confirm_staging_lifecycle_smoke=true",
        ]
    )
    return previous


def dispatch_disable(expected_commit: str) -> set[int]:
    previous = workflow_run_ids(
        "leanprover/lean-eval-submissions", "set-staging-lifecycle-smoke.yml"
    )
    run(
        [
            "gh",
            "workflow",
            "run",
            "set-staging-lifecycle-smoke.yml",
            "--repo",
            "leanprover/lean-eval-submissions",
            "--ref",
            f"lean-eval-dispatch/{expected_commit}",
            "-f",
            "state=disabled",
            "-f",
            f"expected_commit={expected_commit}",
            "-f",
            "result_amendment_maintainers=[]",
            "-f",
            "model_identity_maintainers=[]",
            "-f",
            "confirm_staging_lifecycle_smoke=true",
        ]
    )
    return previous


def wait_for_workflow_run(
    repository: str,
    workflow: str,
    expected_commit: str,
    previous: set[int],
    expected_title: str | None = None,
    timeout: int = 1800,
) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = gh_json(
            [
                f"repos/{repository}/actions/workflows/{workflow}/runs",
                "-f",
                "event=workflow_dispatch",
                "-f",
                "per_page=10",
            ]
        )
        candidates = [
            item
            for item in runs.get("workflow_runs", [])
            if item.get("head_sha") == expected_commit
            and item.get("id") not in previous
            and (expected_title is None or item.get("display_title") == expected_title)
        ]
        if candidates:
            latest = max(candidates, key=lambda item: item["created_at"])
            if latest.get("status") == "completed":
                if latest.get("conclusion") != "success":
                    raise AcceptanceError(f"{repository} {workflow} failed")
                return latest["id"]
        time.sleep(5)
    raise AcceptanceError(f"timed out waiting for {repository} {workflow}")


def verify_exact_release_jobs(
    repository: str, run_id: int, expected_commit: str
) -> None:
    if repository != "leanprover/lean-eval-releases":
        raise AcceptanceError("release job repository is not canonical")
    if type(run_id) is not int or run_id < 1:
        raise AcceptanceError("release workflow run id is not canonical")
    require_match(COMMIT, expected_commit, "release workflow commit")
    response = gh_json(
        [
            f"repos/{repository}/actions/runs/{run_id}/jobs",
            "-f",
            "filter=latest",
            "-f",
            "per_page=100",
            "-f",
            "page=1",
        ]
    )
    if not isinstance(response, dict) or set(response) != {"total_count", "jobs"}:
        raise AcceptanceError("release workflow jobs response fields drifted")
    jobs = response["jobs"]
    total = response["total_count"]
    if (
        type(total) is not int
        or total != len(RELEASE_JOB_NAMES)
        or not isinstance(jobs, list)
        or len(jobs) != total
    ):
        raise AcceptanceError("release workflow jobs are truncated or paginated")
    by_name: dict[str, dict[str, Any]] = {}
    for value in jobs:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            raise AcceptanceError("release workflow job identity is malformed")
        name = value["name"]
        if name in by_name:
            raise AcceptanceError("release workflow job identity is ambiguous")
        by_name[name] = value
    if set(by_name) != RELEASE_JOB_NAMES:
        raise AcceptanceError("release workflow job set differs from the reviewed lane")
    job_ids: set[int] = set()
    for name, job in by_name.items():
        job_id = job.get("id")
        if (
            type(job_id) is not int
            or job_id < 1
            or job_id in job_ids
            or job.get("run_id") != run_id
            or job.get("run_attempt") != 1
            or job.get("head_sha") != expected_commit
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"
        ):
            raise AcceptanceError(f"release workflow job did not succeed: {name}")
        job_ids.add(job_id)


def validate_submission_binding(
    body: dict[str, Any],
    submission_id: str,
    expected_submission: dict[str, Any],
    expected_commit: str,
) -> None:
    if set(body) != {
        "submission_id",
        "owner",
        "received_at",
        "submission",
        "production_metadata",
        "publication_choice",
        "archive",
        "evaluation",
        "result_id",
        "dispatch",
    }:
        raise AcceptanceError("submission status response fields drifted")
    if body.get("submission_id") != submission_id or body.get("owner") != "kim-em":
        raise AcceptanceError("submission status owner binding drifted")
    if body.get("submission") != expected_submission:
        raise AcceptanceError("submission status payload/source binding drifted")
    if body.get("production_metadata") != expected_submission["production_metadata"]:
        raise AcceptanceError("submission production metadata binding drifted")
    if body.get("publication_choice") != expected_submission["publication_choice"]:
        raise AcceptanceError("submission publication choice binding drifted")
    dispatch = body.get("dispatch")
    if (
        not isinstance(dispatch, dict)
        or dispatch.get("status") != "succeeded"
        or dispatch.get("workflow_ref") != f"lean-eval-dispatch/{expected_commit}"
    ):
        raise AcceptanceError("submission dispatch is not bound to the exact candidate")
    received = body.get("received_at")
    if not isinstance(received, str):
        raise AcceptanceError("submission received_at is absent")
    try:
        received_at = datetime.datetime.fromisoformat(received.replace("Z", "+00:00"))
    except ValueError as error:
        raise AcceptanceError("submission received_at is not canonical") from error
    now = datetime.datetime.now(datetime.timezone.utc)
    if (
        received_at.tzinfo is None
        or not datetime.timedelta() <= now - received_at <= datetime.timedelta(hours=2)
    ):
        raise AcceptanceError("submission is not attributable to this bounded run")
    uuid_timestamp = int(submission_id[:8] + submission_id[9:13], 16) / 1000
    if abs(received_at.timestamp() - uuid_timestamp) > 300:
        raise AcceptanceError("submission UUID and received_at are not coherent")


def validate_new_submission(
    body: dict[str, Any],
    submission_id: str,
    expected_submission: dict[str, Any],
    expected_commit: str,
) -> str:
    validate_submission_binding(
        body, submission_id, expected_submission, expected_commit
    )
    result_id = body.get("result_id")
    if not isinstance(result_id, str):
        raise AcceptanceError("accepted submission lacks a Result")
    return require_match(RESULT, result_id, "submission Result id")


def wait_submission(
    api: Api,
    token: str,
    submission_id: str,
    expected_submission: dict[str, Any],
    expected_commit: str,
    timeout: int = 1800,
) -> tuple[dict[str, Any], str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = api.request("GET", f"/api/v1/submissions/{submission_id}", bearer=token)
        archive = body.get("archive") or {}
        evaluation = body.get("evaluation") or {}
        if (
            archive.get("status") == "completed"
            and evaluation.get("status") == "accepted"
        ):
            result_id = validate_new_submission(
                body, submission_id, expected_submission, expected_commit
            )
            return body, result_id
        if archive.get("status") == "failed" or evaluation.get("status") in {
            "rejected",
            "failed",
        }:
            validate_submission_binding(
                body, submission_id, expected_submission, expected_commit
            )
            raise SubmissionTerminalError(
                f"submission {submission_id} terminated without acceptance"
            )
        time.sleep(10)
    raise AcceptanceError(f"timed out waiting for {submission_id}")


def clone_and_assert_state(
    fixture: dict[str, Any],
    browser_id: str,
    browser_result: str,
    headless_id: str,
    headless_result: str,
) -> str:
    bounded = fixture["bounded_acceptance"]
    with tempfile.TemporaryDirectory(prefix="lean-eval-staging-state-") as temporary:
        root = pathlib.Path(temporary) / "state"
        run(
            [
                "gh",
                "repo",
                "clone",
                bounded["state_repository"],
                str(root),
                "--",
                "--branch",
                bounded["state_branch"],
            ]
        )
        if run(["git", "remote", "get-url", "origin"], cwd=root) not in {
            "https://github.com/leanprover/lean-eval-state-staging.git",
            "https://github.com/leanprover/lean-eval-state-staging",
            "git@github.com:leanprover/lean-eval-state-staging.git",
        }:
            raise AcceptanceError("staging State checkout origin is not canonical")
        if run(["git", "status", "--porcelain"], cwd=root):
            raise AcceptanceError("staging State checkout is not clean")
        state_commit = run(["git", "rev-parse", "HEAD"], cwd=root)
        require_match(COMMIT, state_commit, "staging State commit")
        run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                bounded["state_contract_commit"],
                state_commit,
            ],
            cwd=root,
        )
        scripts = {
            path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in (root / "scripts").glob("*.py")
            if path.is_file() and not path.is_symlink()
        }
        if scripts != bounded["state_script_sha256"]:
            raise AcceptanceError("staging State reviewed script identity drifted")
        operator_free_home = pathlib.Path(temporary) / "operator-free-home"
        operator_free_home.mkdir(mode=0o700)
        closed_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(operator_free_home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        output = pathlib.Path(temporary) / "materialized"
        run(
            [
                sys.executable,
                "-I",
                "scripts/state.py",
                "--root",
                str(root),
                "--protected-main-commit",
                state_commit,
                "validate",
            ],
            cwd=root,
            env=closed_env,
        )
        run(
            [
                sys.executable,
                "-I",
                "scripts/state.py",
                "--root",
                str(root),
                "--protected-main-commit",
                state_commit,
                "materialize",
                "--output",
                str(output),
            ],
            cwd=root,
            env=closed_env,
        )
        domain = closed_json(output / "domain.json")
        submissions = {item["submission_id"]: item for item in domain["submissions"]}
        if submissions[browser_id]["publication_choice"] != "withheld":
            raise AcceptanceError("browser opt-out is absent from materialized State")
        if submissions[headless_id].get("result_id") != headless_result:
            raise AcceptanceError("headless Result is absent from materialized State")
        queue = closed_json(output / "release-queue.json")["tasks"]
        if any(task["submission_id"] == browser_id for task in queue):
            raise AcceptanceError("withheld browser submission was scheduled")
        if not any(
            task["submission_id"] == headless_id
            and task["result_id"] == headless_result
            for task in queue
        ):
            raise AcceptanceError(
                "scheduled headless Result is absent from the release queue"
            )
        projection_path = pathlib.Path(temporary) / "public-state-v4.json"
        run(
            [
                sys.executable,
                "-I",
                "scripts/public_projection.py",
                "--root",
                str(root),
                "--state-commit",
                state_commit,
                "--protected-main-commit",
                state_commit,
                "--schema-version",
                "4",
                "--output",
                str(projection_path),
            ],
            cwd=root,
            env=closed_env,
        )
        run(
            [
                sys.executable,
                "-I",
                "scripts/public_projection.py",
                "--validate",
                str(projection_path),
            ],
            cwd=root,
            env=closed_env,
        )
        projection = closed_json(projection_path)
        projected = {item["result_id"]: item for item in projection["results"]}
        if browser_result not in projected or headless_result not in projected:
            raise AcceptanceError(
                "accepted Results are absent from the redacted leaderboard projection"
            )
        if projected[browser_result]["release"] is not None:
            raise AcceptanceError(
                "withheld browser Result has a leaderboard release entry"
            )
        if (projected[headless_result]["release"] or {}).get("status") != "scheduled":
            raise AcceptanceError(
                "headless Result is not scheduled in the leaderboard projection"
            )
        redacted = projection_path.read_text(encoding="utf-8")
        if "source/Submission" in redacted or "theorem" in redacted:
            raise AcceptanceError("redacted projection contains source material")
        if run(["git", "status", "--porcelain"], cwd=root):
            raise AcceptanceError("State assertion scripts modified their checkout")
        return state_commit


def assert_results(fixture: dict[str, Any], result_ids: set[str]) -> str:
    bounded = fixture["bounded_acceptance"]
    branch = gh_json(
        [f"repos/{bounded['results_repository']}/branches/{bounded['results_branch']}"]
    )
    commit = branch["commit"]["sha"]
    require_match(COMMIT, commit, "staging Results commit")
    tree = gh_json(
        [
            f"repos/{bounded['results_repository']}/git/trees/{commit}",
            "-f",
            "recursive=1",
        ]
    )
    if tree.get("truncated") is not False:
        raise AcceptanceError("staging Results tree response was truncated")
    paths = [
        item["path"]
        for item in tree["tree"]
        if item.get("type") == "blob" and item["path"].startswith("results/")
    ]
    found: set[str] = set()
    for path in paths:
        content = gh_json(
            [
                f"repos/{bounded['results_repository']}/contents/{path}",
                "-f",
                f"ref={commit}",
            ]
        )
        document = json.loads(base64.b64decode(content["content"]))
        for record in document.get("results", []):
            if record.get("result_id") in result_ids:
                found.add(record["result_id"])
                serialized = json.dumps(record, sort_keys=True)
                if "source/Submission" in serialized or "private-note" in serialized:
                    raise AcceptanceError("staging Result leaked source material")
    if found != result_ids:
        raise AcceptanceError(f"staging Results missing {sorted(result_ids - found)}")
    return commit


def assert_archive_sidecar(submission: dict[str, Any]) -> None:
    submission_id = require_match(
        UUID7, submission["submission_id"], "archive submission id"
    )
    archive = submission["archive"]
    if archive.get("archive_repository") != "leanprover/lean-eval-audit":
        raise AcceptanceError("archive repository is not canonical")
    archive_commit = require_match(COMMIT, archive["archive_commit"], "archive commit")
    ciphertext = require_match(
        re.compile(r"[0-9a-f]{64}"),
        archive["archive_ciphertext_sha256"],
        "archive ciphertext digest",
    )
    sidecar_path = archive["archive_path"].removesuffix(".tar.age") + ".json"
    content = gh_json(
        [
            f"repos/{archive['archive_repository']}/contents/{sidecar_path}",
            "-f",
            f"ref={archive_commit}",
        ]
    )
    raw = base64.b64decode(content["content"])
    sidecar = json.loads(raw)
    if (
        sidecar.get("schema_version") != 3
        or sidecar.get("submission_id") != submission_id
    ):
        raise AcceptanceError(
            "archive sidecar lacks exact schema-v3 submission binding"
        )
    envelope = sidecar.get("key_envelope") or {}
    if envelope.get("archive_ciphertext_sha256") != ciphertext:
        raise AcceptanceError("archive sidecar envelope is not ciphertext-bound")
    if sidecar.get("sha256_ciphertext") not in {None, ciphertext}:
        raise AcceptanceError("archive sidecar ciphertext digest disagrees with State")
    if hashlib.sha256(raw).hexdigest() == ciphertext:
        raise AcceptanceError("sidecar and ciphertext identities were conflated")


def assert_disabled_routes(
    api: Api,
    token: str,
    fixture: dict[str, Any],
    browser_submission_id: str,
    browser_result: str,
    model_id: str,
) -> None:
    source = fixture["source"]
    state_before_intake_probe = state_branch_commit(fixture)
    intake = api.request(
        "POST",
        "/api/v1/agent/challenges",
        {
            "login": source["owner_login"],
            "gist_id": "0" * 20,
            "source_repository": source["repository"],
            "source_commit": source["commit"],
        },
        expected=503,
    )
    if intake != {"error": "intake_disabled"}:
        raise AcceptanceError("disabled intake route did not fail closed")
    if state_branch_commit(fixture) != state_before_intake_probe:
        raise AcceptanceError("disabled intake probe changed staging State")
    cases = (
        ("POST", "/api/v1/model-identities", {"display_name": "must remain disabled"}),
        (
            "POST",
            f"/api/v1/model-identities/{model_id}/decisions",
            {"decision": "approve"},
        ),
        (
            "POST",
            f"/api/v1/results/{browser_result}/problem-repairs",
            fixture["lifecycle_cases"]["problem_repair"]["success_request"],
        ),
        (
            "POST",
            f"/api/v1/results/{browser_result}/problem-repairs/decisions",
            fixture["lifecycle_cases"]["problem_repair"]["maintainer_decision"],
        ),
        (
            "PATCH",
            f"/api/v1/results/{browser_result}/metadata",
            {"production_metadata": {"notes": "must remain disabled"}},
        ),
        (
            "PUT",
            f"/api/v1/submissions/{browser_submission_id}/publication",
            {"publication_choice": "withheld"},
        ),
    )
    for method, path, body in cases:
        denied = mutation(
            api,
            token,
            method,
            path,
            body,
            expected=404,
            denial_fixture=fixture,
        )
        if denied != {"error": "not_found"}:
            raise AcceptanceError(f"disabled route did not fail closed: {path}")


def execute(args: argparse.Namespace, fixture: dict[str, Any]) -> None:
    bounded = fixture["bounded_acceptance"]
    api = Api(bounded["submission_base_url"])
    fixture_preflight(fixture, args.gist_id, args.expected_commit)
    health(api, args.expected_commit, True)
    source = fixture["source"]
    mismatch = fixture["headless_source_mismatch"]
    mismatch_challenge = api.request(
        "POST",
        "/api/v1/agent/challenges",
        {
            "login": source["owner_login"],
            "gist_id": args.gist_id,
            "source_repository": source["repository"],
            "source_commit": source["commit"],
        },
        expected=201,
    )
    if (
        set(mismatch_challenge)
        != {
            "challenge",
            "expires_at",
            "gist_id",
            "gist_file",
            "gist_content",
            "submission_id",
            "tag",
        }
        or mismatch_challenge.get("gist_id") != args.gist_id
        or mismatch_challenge.get("gist_file") != bounded["fixture_gist_file"]
        or mismatch_challenge.get("gist_content") != mismatch_challenge.get("challenge")
        or mismatch_challenge.get("tag")
        != f"lean-eval/{mismatch_challenge.get('submission_id')}"
    ):
        raise AcceptanceError("source-mismatch challenge bindings are not exact")
    mismatch_id = require_match(
        UUID7, mismatch_challenge["submission_id"], "mismatch submission id"
    )
    denied_submission = {
        **fixture["headless_submission"],
        "source_repository": source["repository"],
        "source_commit": mismatch["submitted_source_commit"],
    }
    denied = api.request(
        "POST",
        "/api/v1/agent/submissions",
        {"challenge": mismatch_challenge["challenge"], "submission": denied_submission},
        expected=mismatch["expected_http_status"],
    )
    if denied != {"error": mismatch["expected_error"]}:
        raise AcceptanceError("source mismatch did not fail closed")
    mismatch_challenge.clear()
    assert_state_event_absent(fixture, mismatch_id)

    challenge = api.request(
        "POST",
        "/api/v1/agent/challenges",
        {
            "login": source["owner_login"],
            "gist_id": args.gist_id,
            "source_repository": source["repository"],
            "source_commit": source["commit"],
        },
        expected=201,
    )
    if set(challenge) != {
        "challenge",
        "expires_at",
        "gist_id",
        "gist_file",
        "gist_content",
        "submission_id",
        "tag",
    }:
        raise AcceptanceError("agent challenge response fields drifted")
    require_match(UUID7, challenge["submission_id"], "headless submission id")
    if (
        challenge["gist_id"] != args.gist_id
        or challenge["gist_file"] != bounded["fixture_gist_file"]
        or challenge["gist_content"] != challenge["challenge"]
        or challenge["tag"] != f"lean-eval/{challenge['submission_id']}"
    ):
        raise AcceptanceError("agent challenge bindings are not exact")
    signed_challenge = challenge["challenge"]
    lease = apply_exact_proof_and_tag(
        fixture, args.gist_id, signed_challenge, challenge["tag"]
    )
    accepted_submission = {
        **fixture["headless_submission"],
        "source_repository": source["repository"],
        "source_commit": source["commit"],
    }
    headless_id = challenge["submission_id"]
    headless_terminal = False
    headless_post_started = False
    fixture_cleaned = False
    try:
        # From this point until a terminal status is observed, transport failure
        # is an unknown acceptance outcome. The server may have committed and
        # dispatched even when this process receives no response.
        headless_post_started = True
        accepted = api.request(
            "POST",
            "/api/v1/agent/submissions",
            {"challenge": signed_challenge, "submission": accepted_submission},
            expected=202,
        )
        if (
            set(accepted)
            != {"submission_id", "status", "dispatch_status", "session_token"}
            or accepted.get("submission_id") != headless_id
            or accepted.get("status") != "queued"
            or accepted.get("dispatch_status") != "succeeded"
            or not isinstance(accepted.get("session_token"), str)
        ):
            raise AcceptanceError("headless acceptance response is not exact")
        token = accepted["session_token"]
        signed_challenge = ""
        challenge.clear()
        browser_expected = {
            **fixture["browser_submission"],
            "source_repository": source["repository"],
            "source_commit": source["commit"],
            "publication_choice": "withheld",
        }
        try:
            headless, headless_result = wait_submission(
                api,
                token,
                headless_id,
                accepted_submission,
                args.expected_commit,
                timeout=args.evaluation_timeout,
            )
            headless_terminal = True
            cleanup_fixture_mutation(lease, remove_tag=True)
            fixture_cleaned = True
        except SubmissionTerminalError:
            headless_terminal = True
            raise
        browser, browser_result = wait_submission(
            api,
            token,
            args.browser_submission_id,
            browser_expected,
            args.expected_commit,
            timeout=args.evaluation_timeout,
        )
    finally:
        if fixture_cleaned:
            pass
        elif not fixture_cleanup_is_proved_safe(
            headless_post_started=headless_post_started,
            headless_terminal=headless_terminal,
        ):
            print(
                f"Headless acceptance outcome may be committed; external proof and tag must remain until terminal evaluation is reconciled, then be restored/removed: {lease.describe_targets()}",
                file=sys.stderr,
            )
        else:
            try:
                cleanup_fixture_mutation(lease, remove_tag=True)
            except BaseException as cleanup_error:
                print(
                    f"External cleanup still required: {lease.describe_targets()} ({cleanup_error})",
                    file=sys.stderr,
                )
                raise

    assert_archive_sidecar(browser)
    assert_archive_sidecar(headless)

    invalid = fixture["lifecycle_cases"]["problem_repair"]
    mutation(
        api,
        token,
        "POST",
        f"/api/v1/results/{browser_result}/problem-repairs",
        invalid["denial_request"],
        expected=invalid["denial_http_status"],
        denial_fixture=fixture,
    )
    requested = mutation(
        api,
        token,
        "POST",
        f"/api/v1/results/{browser_result}/problem-repairs",
        invalid["success_request"],
    )
    if requested.get("status") not in {
        "problem_repair_requested",
        "problem_repair_already_requested",
    }:
        raise AcceptanceError("problem repair was not requested")

    models = fixture["lifecycle_cases"]["model_alias_and_rename"]
    primary = mutation(
        api,
        token,
        "POST",
        "/api/v1/model-identities",
        {"display_name": models["primary_display_name"]},
    )["model_id"]
    decision_path = f"/api/v1/model-identities/{primary}/decisions"
    denied = mutation(
        api,
        token,
        "POST",
        decision_path,
        {"decision": "approve"},
        expected=fixture["maintainer_profiles"]["denial_http_status"],
        denial_fixture=fixture,
    )
    if denied != {"error": "not_found"}:
        raise AcceptanceError("model non-maintainer denial was not owner-hiding")
    denied = mutation(
        api,
        token,
        "POST",
        f"/api/v1/results/{browser_result}/problem-repairs/decisions",
        invalid["maintainer_decision"],
        expected=fixture["maintainer_profiles"]["denial_http_status"],
        denial_fixture=fixture,
    )
    if denied != {"error": "not_found"}:
        raise AcceptanceError("result non-maintainer denial was not owner-hiding")

    previous_gate_runs = dispatch_gate_state(args.expected_commit, fixture, True)
    wait_for_workflow_run(
        "leanprover/lean-eval-submissions",
        "set-staging-lifecycle-smoke.yml",
        args.expected_commit,
        previous_gate_runs,
    )
    health(api, args.expected_commit, True)

    mutation(api, token, "POST", decision_path, {"decision": "approve"})
    mutation(
        api,
        token,
        "POST",
        f"/api/v1/model-identities/{primary}/aliases",
        {"alias": models["alias"]},
    )
    mutation(
        api,
        token,
        "PUT",
        f"/api/v1/model-identities/{primary}/name",
        {"display_name": models["renamed_display_name"]},
    )
    secondary = mutation(
        api,
        token,
        "POST",
        "/api/v1/model-identities",
        {"display_name": models["secondary_display_name"]},
    )["model_id"]
    mutation(
        api,
        token,
        "POST",
        f"/api/v1/model-identities/{secondary}/decisions",
        {"decision": "approve"},
    )
    mutation(
        api,
        token,
        "POST",
        f"/api/v1/model-identities/{secondary}/aliases",
        {"alias": models["alias"]},
        expected=models["collision_http_status"],
        denial_fixture=fixture,
    )
    mutation(
        api,
        token,
        "POST",
        f"/api/v1/results/{browser_result}/problem-repairs/decisions",
        invalid["maintainer_decision"],
    )

    results_commit = gh_json(
        [f"repos/{bounded['results_repository']}/branches/{bounded['results_branch']}"]
    )["commit"]["sha"]
    backfill = fixture["lifecycle_cases"]["metadata_backfill"]
    mutation(
        api,
        token,
        "POST",
        "/api/v1/results/claims",
        {"result_id": backfill["claim"]["result_id"], "results_commit": results_commit},
    )
    mutation(
        api,
        token,
        "PATCH",
        f"/api/v1/results/{backfill['claim']['result_id']}/metadata",
        backfill["success_patch"],
    )
    nonowner = backfill["non_owner_denial"]
    denied = mutation(
        api,
        token,
        nonowner["method"],
        nonowner["path"],
        nonowner["patch"],
        expected=nonowner["expected_http_status"],
        denial_fixture=fixture,
    )
    if denied != {"error": nonowner["expected_error"]}:
        raise AcceptanceError("metadata non-owner denial was not owner-hiding")

    if browser.get("publication_choice") != "withheld":
        raise AcceptanceError("browser submission was not visibly opted out")
    assert_results(fixture, {browser_result, headless_result})
    clone_and_assert_state(
        fixture,
        args.browser_submission_id,
        browser_result,
        headless_id,
        headless_result,
    )

    previous_release_runs = workflow_run_ids(
        bounded["release_repository"], bounded["release_workflow"]
    )
    run(
        [
            "gh",
            "workflow",
            "run",
            bounded["release_workflow"],
            "--repo",
            bounded["release_repository"],
            "--ref",
            bounded["release_ref"],
            "-f",
            f"submission_id={headless_id}",
            "-f",
            f"expected_release_commit={bounded['release_commit']}",
            "-f",
            "confirm_staging_smoke=true",
        ]
    )
    previous_disable_runs = dispatch_disable(args.expected_commit)
    wait_for_workflow_run(
        "leanprover/lean-eval-submissions",
        "set-staging-lifecycle-smoke.yml",
        args.expected_commit,
        previous_disable_runs,
    )
    health(api, args.expected_commit, False)
    args.disabled = True
    assert_disabled_routes(
        api, token, fixture, args.browser_submission_id, browser_result, primary
    )
    release_run_id = wait_for_workflow_run(
        bounded["release_repository"],
        bounded["release_workflow"],
        bounded["release_commit"],
        previous_release_runs,
        expected_title=bounded["release_run_name_prefix"] + headless_id,
        timeout=2400,
    )
    verify_exact_release_jobs(
        bounded["release_repository"], release_run_id, bounded["release_commit"]
    )
    print(
        json.dumps(
            {
                "status": "bounded_staging_acceptance_complete",
                "browser_submission_id": args.browser_submission_id,
                "browser_result_id": browser_result,
                "headless_submission_id": headless_id,
                "headless_result_id": headless_result,
                "release_reconstruction": "completed_publication_disabled",
                "release_workflow_run_id": release_run_id,
            },
            indent=2,
        )
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("preflight", "run"):
        command = sub.add_parser(name)
        command.add_argument("--expected-commit", required=True)
        command.add_argument("--gist-id", required=True)
        if name == "run":
            command.add_argument("--browser-submission-id", required=True)
            command.add_argument("--evaluation-timeout", type=int, default=1800)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        fixture = closed_json(DEFAULT_FIXTURE)
        require_match(COMMIT, args.expected_commit, "expected staging commit")
        require_match(GIST, args.gist_id, "secret gist id")
        if args.command == "preflight":
            fixture_preflight(fixture, args.gist_id, args.expected_commit)
            print("bounded staging acceptance preflight: ok (zero writes)")
            return 0
        require_match(UUID7, args.browser_submission_id, "browser submission id")
        args.disabled = False
        try:
            execute(args, fixture)
        finally:
            # The Actions watchdog is the independent backstop.  This exact-tag
            # dispatch is the prompt normal close and never enables authority.
            if not args.disabled:
                previous_disable_runs = dispatch_disable(args.expected_commit)
                wait_for_workflow_run(
                    "leanprover/lean-eval-submissions",
                    "set-staging-lifecycle-smoke.yml",
                    args.expected_commit,
                    previous_disable_runs,
                )
            health(
                Api(fixture["bounded_acceptance"]["submission_base_url"]),
                args.expected_commit,
                False,
            )
        return 0
    except (AcceptanceError, KeyError, TypeError, ValueError) as error:
        print(f"bounded staging acceptance failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
