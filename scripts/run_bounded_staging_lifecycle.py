#!/usr/bin/env python3
"""Run the temporary, bounded staging lifecycle acceptance.

This is deliberately an operator-side, two-phase driver.  It has no stored
credentials, never logs the signed agent challenge, and is retired with its
fixture and watchdog after one accepted run.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "configuration" / "staging-lifecycle-smoke-v1.json"
UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
RESULT = re.compile(r"r2_[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
GIST = re.compile(r"[0-9a-f]{20,64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class AcceptanceError(RuntimeError):
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
    command: list[str], *, capture: bool = True, cwd: pathlib.Path | None = None
) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
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
                f"{method} {path}: expected {accepted}, got {status}: {payload}"
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


def fixture_preflight(
    fixture: dict[str, Any], gist_id: str, expected_commit: str
) -> None:
    source = fixture["source"]
    bounded = fixture["bounded_acceptance"]
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
        "results_repository": "leanprover/lean-eval-submissions",
        "results_branch": "staging-results",
        "release_repository": "leanprover/lean-eval-releases",
        "release_workflow": "credentialed-release-staging-smoke.yml",
        "release_ref": "main",
        "fixture_gist_file": "lean-eval-proof.txt",
        "external_mutation_confirmation": "APPROVE_EXACT_STAGING_FIXTURE_GIST_AND_TAG",
        "retire_after": "one accepted bounded staging lifecycle run",
    }
    if set(bounded) != {*expected_bounded, "release_commit"} or any(
        bounded.get(key) != value for key, value in expected_bounded.items()
    ):
        raise AcceptanceError("bounded staging acceptance authority drifted")
    require_match(COMMIT, bounded["release_commit"], "release workflow commit")
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


def update_exact_proof_and_tag(
    fixture: dict[str, Any],
    gist_id: str,
    challenge: str,
    tag: str,
    confirmation: str,
) -> None:
    bounded = fixture["bounded_acceptance"]
    if confirmation != bounded["external_mutation_confirmation"]:
        raise AcceptanceError("exact external-mutation confirmation was not supplied")
    source = fixture["source"]
    tag_path = f"repos/{source['repository']}/git/ref/tags/{tag}"
    try:
        gh_json([tag_path])
    except AcceptanceError:
        pass
    else:
        raise AcceptanceError(
            "generated source tag already exists; refusing to move or reuse it"
        )
    gh_json(
        [f"gists/{gist_id}"],
        method="PATCH",
        fields={"files": {bounded["fixture_gist_file"]: {"content": challenge}}},
    )
    gh_json(
        [f"repos/{source['repository']}/git/refs"],
        method="POST",
        fields={"ref": f"refs/tags/{tag}", "sha": source["commit"]},
    )


def assert_state_event_absent(fixture: dict[str, Any], identifier: str) -> None:
    bounded = fixture["bounded_acceptance"]
    command = [
        "gh",
        "api",
        "--method",
        "GET",
        f"repos/{bounded['state_repository']}/contents/events/{identifier[:2]}/{identifier}.json",
        "-f",
        f"ref={bounded['state_branch']}",
    ]
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode == 0:
        raise AcceptanceError(f"denied operation created State event {identifier}")
    if "HTTP 404" not in completed.stderr:
        raise AcceptanceError(
            f"could not prove State event absence: {completed.stderr.strip()}"
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
        ]
        if candidates:
            latest = max(candidates, key=lambda item: item["created_at"])
            if latest.get("status") == "completed":
                if latest.get("conclusion") != "success":
                    raise AcceptanceError(f"{repository} {workflow} failed")
                return latest["id"]
        time.sleep(5)
    raise AcceptanceError(f"timed out waiting for {repository} {workflow}")


def wait_submission(
    api: Api, token: str, submission_id: str, result_id: str, timeout: int = 1800
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = api.request("GET", f"/api/v1/submissions/{submission_id}", bearer=token)
        archive = body.get("archive") or {}
        evaluation = body.get("evaluation") or {}
        if (
            body.get("result_id") == result_id
            and archive.get("status") == "completed"
            and evaluation.get("status") in {"accepted", "rejected"}
        ):
            return body
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
                "--depth=1",
                "--branch",
                bounded["state_branch"],
            ]
        )
        state_commit = run(["git", "rev-parse", "HEAD"], cwd=root)
        require_match(COMMIT, state_commit, "staging State commit")
        output = pathlib.Path(temporary) / "materialized"
        run(
            [
                sys.executable,
                "scripts/state.py",
                "--root",
                str(root),
                "--protected-main-commit",
                state_commit,
                "validate",
            ],
            cwd=root,
        )
        run(
            [
                sys.executable,
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
        )
        run(
            [
                sys.executable,
                "scripts/public_projection.py",
                "--validate",
                str(projection_path),
            ],
            cwd=root,
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
        import base64

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
    import base64
    import hashlib

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
    assert_state_event_absent(fixture, mismatch_challenge["submission_id"])

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
    require_match(UUID7, challenge["submission_id"], "headless submission id")
    if (
        challenge["gist_id"] != args.gist_id
        or challenge["tag"] != f"lean-eval/{challenge['submission_id']}"
    ):
        raise AcceptanceError("agent challenge bindings are not exact")
    update_exact_proof_and_tag(
        fixture,
        args.gist_id,
        challenge["challenge"],
        challenge["tag"],
        args.confirm_external_mutations,
    )
    accepted_submission = {
        **fixture["headless_submission"],
        "source_repository": source["repository"],
        "source_commit": source["commit"],
    }
    accepted = api.request(
        "POST",
        "/api/v1/agent/submissions",
        {"challenge": challenge["challenge"], "submission": accepted_submission},
        expected=(200, 201, 202),
    )
    token = accepted.pop("session_token")
    headless_id = challenge["submission_id"]

    invalid = fixture["lifecycle_cases"]["problem_repair"]
    mutation(
        api,
        token,
        "POST",
        f"/api/v1/results/{args.browser_result_id}/problem-repairs",
        invalid["denial_request"],
        expected=invalid["denial_http_status"],
        denial_fixture=fixture,
    )
    requested = mutation(
        api,
        token,
        "POST",
        f"/api/v1/results/{args.browser_result_id}/problem-repairs",
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
        f"/api/v1/results/{args.browser_result_id}/problem-repairs/decisions",
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
        f"/api/v1/results/{args.browser_result_id}/problem-repairs/decisions",
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

    browser = wait_submission(
        api, token, args.browser_submission_id, args.browser_result_id
    )
    if browser.get("publication_choice") != "withheld":
        raise AcceptanceError("browser submission was not visibly opted out")
    deadline = time.monotonic() + args.evaluation_timeout
    headless_result = ""
    while time.monotonic() < deadline:
        current = api.request("GET", f"/api/v1/submissions/{headless_id}", bearer=token)
        if isinstance(current.get("result_id"), str):
            headless_result = require_match(
                RESULT, current["result_id"], "headless Result id"
            )
            break
        time.sleep(10)
    if not headless_result:
        raise AcceptanceError("headless submission did not produce a Result")
    headless = wait_submission(api, token, headless_id, headless_result)
    assert_archive_sidecar(browser)
    assert_archive_sidecar(headless)
    assert_results(fixture, {args.browser_result_id, headless_result})
    clone_and_assert_state(
        fixture,
        args.browser_submission_id,
        args.browser_result_id,
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
    release_run_id = wait_for_workflow_run(
        bounded["release_repository"],
        bounded["release_workflow"],
        bounded["release_commit"],
        previous_release_runs,
        timeout=2400,
    )
    print(
        json.dumps(
            {
                "status": "bounded_staging_acceptance_complete",
                "browser_submission_id": args.browser_submission_id,
                "browser_result_id": args.browser_result_id,
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
    result.add_argument("--fixture", type=pathlib.Path, default=DEFAULT_FIXTURE)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("preflight", "run"):
        command = sub.add_parser(name)
        command.add_argument("--expected-commit", required=True)
        command.add_argument("--gist-id", required=True)
        if name == "run":
            command.add_argument("--browser-submission-id", required=True)
            command.add_argument("--browser-result-id", required=True)
            command.add_argument("--confirm-external-mutations", required=True)
            command.add_argument("--evaluation-timeout", type=int, default=1800)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        fixture = closed_json(args.fixture)
        require_match(COMMIT, args.expected_commit, "expected staging commit")
        require_match(GIST, args.gist_id, "secret gist id")
        if args.command == "preflight":
            fixture_preflight(fixture, args.gist_id, args.expected_commit)
            print("bounded staging acceptance preflight: ok (zero writes)")
            return 0
        require_match(UUID7, args.browser_submission_id, "browser submission id")
        require_match(RESULT, args.browser_result_id, "browser Result id")
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
