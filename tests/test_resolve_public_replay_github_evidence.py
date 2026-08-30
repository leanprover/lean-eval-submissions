import base64
import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from resolve_public_replay_github_evidence import (  # noqa: E402
    MAX_PUBLIC_GIST_API_REQUESTS_PER_SHARD,
    EvidenceError,
    GitHubClient,
    ProbeIndeterminate,
    _legacy_candidate,
    _legacy_candidate_projection,
    _read_bounded,
    _RejectRedirects,
    _require_public_gist_probe_budget,
    _validate_historical_results,
    _workflow_binding,
    _write_exclusive,
    canonical_bytes,
    canonical_document_bytes,
    shard_requests,
    validate_legacy_adjudication_registry,
    validate_requests,
    validate_workflow_registry,
)
from resolve_public_replay_github_evidence import resolve as _resolve  # noqa: E402
from resolve_public_replay_github_evidence import (  # noqa: E402
    validate_evidence as _validate_evidence,
)

BENCHMARK = "1" * 40
SOURCE = "2" * 40


def registry_bytes(value: dict | None = None) -> tuple[dict, str]:
    if value is None:
        path = ROOT / "configuration/public-replay-workflow-definitions-v1.json"
        raw = path.read_bytes()
        value = json.loads(raw)
    else:
        raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    return value, hashlib.sha256(raw).hexdigest()


def adjudication_bytes(value: dict | None = None) -> tuple[dict, str]:
    if value is None:
        path = ROOT / "configuration/public-replay-legacy-adjudications-v1.json"
        raw = path.read_bytes()
        value = json.loads(raw)
    else:
        raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    return value, hashlib.sha256(raw).hexdigest()


def resolve(value, digest, client, workflow_registry=None, registry_digest=None, **kwargs):
    workflow_registry, computed = registry_bytes(workflow_registry)
    if digest == "8" * 64:
        digest = hashlib.sha256(canonical_document_bytes(value)).hexdigest()
    return _resolve(
        value,
        digest,
        client,
        workflow_registry,
        registry_digest or computed,
        **kwargs,
    )


def validate_evidence(value, requests, workflow_registry=None, registry_digest=None):
    workflow_registry, computed = registry_bytes(workflow_registry)
    return _validate_evidence(
        value,
        requests,
        workflow_registry,
        registry_digest or computed,
    )


def refresh_request_id(value: dict) -> None:
    request = value["requests"][0]
    identity = {
        key: request[key]
        for key in (
            "owner",
            "issue_number",
            "accepted_at",
            "declared_model",
            "source",
            "benchmark",
        )
    }
    request["request_id"] = (
        "prr_" + hashlib.sha256(canonical_bytes(identity)).hexdigest()
    )


def request_value() -> dict:
    value = {
        "schema_version": 1,
        "kind": "historical_public_replay_resolution_requests",
        "source_repository": "leanprover/lean-eval-submissions",
        "source_commit": "4" * 40,
        "inventory_sha256": "5" * 64,
        "request_count": 1,
        "result_count": 2,
        "requests": [
            {
                "request_id": "",
                "owner": "A-M-Berns",
                "issue_number": 144,
                "accepted_at": "2026-05-07T07:05:49Z",
                "declared_model": "GPT-5.5 Codex",
                "source": {
                    "kind": "github_repo",
                    "repository": "A-M-Berns/lean-eval-submissions",
                    "commit": SOURCE,
                    "visibility": "public",
                },
                "benchmark": {
                    "repository": "leanprover/lean-eval",
                    "commit": BENCHMARK,
                },
                "candidate_issue_repositories": [
                    "leanprover/lean-eval",
                    "leanprover/lean-eval-submissions",
                ],
                "expected_workflow": {"event": "issues", "name": "Submission"},
                "readiness": "github_evidence_fetch_pending",
                "results": [
                    {
                        "result_id": "r2_" + "6" * 64,
                        "owner": "A-M-Berns",
                        "problem_id": "sturm_separation",
                        "statement_revision": 1,
                    },
                    {
                        "result_id": "r2_" + "7" * 64,
                        "owner": "A-M-Berns",
                        "problem_id": "bvp_comparison",
                        "statement_revision": 1,
                    },
                ],
            }
        ],
    }
    refresh_request_id(value)
    return value


def issue() -> dict:
    return {
        "number": 144,
        "title": "[submission] BVP and Sturm try 3",
        "body": """### Submission URL

https://github.com/A-M-Berns/lean-eval-submissions

### Model

GPT-5.5 Codex

### How this solution was produced (optional)

_No response_
""",
        "user": {"login": "A-M-Berns"},
        "created_at": "2026-05-07T06:57:46Z",
        "closed_at": "2026-05-07T07:05:50Z",
        "state": "closed",
        "html_url": "https://github.com/leanprover/lean-eval/issues/144",
    }


def run() -> dict:
    return {
        "id": 25480965896,
        "name": "Submission",
        "display_title": "[submission] BVP and Sturm try 3",
        "event": "issues",
        "conclusion": "success",
        "status": "completed",
        "run_attempt": 1,
        "created_at": "2026-05-07T06:57:49Z",
        "updated_at": "2026-05-07T07:05:54Z",
        "head_sha": BENCHMARK,
        "path": ".github/workflows/submission.yml",
        "head_branch": "main",
        "repository": {"full_name": "leanprover/lean-eval"},
        "head_repository": {"full_name": "leanprover/lean-eval"},
        "actor": {"login": "A-M-Berns"},
        "html_url": "https://github.com/leanprover/lean-eval/actions/runs/25480965896",
    }


def comment() -> dict:
    return {
        "id": 1,
        "user": {"login": "github-actions[bot]"},
        "created_at": "2026-05-07T07:05:49Z",
        "body": """## Submission result

✅ Newly-solved problems: sturm_separation, bvp_comparison

### Per-problem
- `sturm_separation`: pass
- `bvp_comparison`: pass
""",
        "html_url": "https://github.com/leanprover/lean-eval/issues/144#issuecomment-1",
    }


class FakeClient:
    def __init__(self, second_match: bool = False, source_available: bool = True):
        self.second_match = second_match
        self.source_available = source_available

    def get(self, path: str):
        if path in {
            "/repos/leanprover/lean-eval",
            "/repos/leanprover/lean-eval-submissions",
        }:
            return {
                "full_name": path.removeprefix("/repos/"),
                "private": False,
                "visibility": "public",
            }, 200
        if path == "/repos/leanprover/lean-eval/issues/144":
            return issue(), 200
        if path == "/repos/leanprover/lean-eval-submissions/issues/144":
            if not self.second_match:
                return None, 404
            value = issue()
            value["html_url"] = (
                "https://github.com/leanprover/lean-eval-submissions/issues/144"
            )
            return value, 200
        if path == (
            "/repos/A-M-Berns/lean-eval-submissions"
        ):
            return (
                {
                    "full_name": "A-M-Berns/lean-eval-submissions",
                    "private": False,
                    "visibility": "public",
                },
                200,
            )
        if path == (
            "/repos/A-M-Berns/lean-eval-submissions/git/commits/" + SOURCE
        ):
            return ({"sha": SOURCE}, 200) if self.source_available else (None, 404)
        if path.startswith("/repos/leanprover/") and (
            "/contents/.github/workflows/submission.yml?ref=" in path
        ):
            workflow = "\n".join(
                (
                    "repository: leanprover/lean-eval",
                    "ref: main",
                    "id: benchmark",
                    'echo "sha=$(git -C lean-eval rev-parse HEAD)" >> "$GITHUB_OUTPUT"',
                    "benchmark_commit: ${{ steps.benchmark.outputs.sha }}",
                    "BENCHMARK_COMMIT: ${{ needs.evaluate.outputs.benchmark_commit }}",
                    '--benchmark-commit "$BENCHMARK_COMMIT"',
                )
            ).encode()
            return (
                {
                    "encoding": "base64",
                    "content": base64.b64encode(workflow).decode(),
                    "size": len(workflow),
                },
                200,
            )
        raise AssertionError(path)

    def pages(self, path: str, item_key=None):
        if "/actions/runs?" in path:
            value = run()
            if "/leanprover/lean-eval-submissions/" in path:
                value["repository"]["full_name"] = "leanprover/lean-eval-submissions"
                value["head_repository"]["full_name"] = (
                    "leanprover/lean-eval-submissions"
                )
                value["html_url"] = (
                    "https://github.com/leanprover/lean-eval-submissions/actions/runs/"
                    "25480965896"
                )
            return [value]
        if path.endswith("/issues/144/comments"):
            value = comment()
            if "/leanprover/lean-eval-submissions/" in path:
                value["html_url"] = (
                    "https://github.com/leanprover/lean-eval-submissions/issues/"
                    "144#issuecomment-1"
                )
            return [value]
        raise AssertionError((path, item_key))


def synthetic_legacy_candidate(reason: str) -> dict:
    requests = request_value()
    request = requests["requests"][0]
    accepted_body = issue()["body"]
    current_body = accepted_body
    accepted_model = request["declared_model"]
    rename = None
    edits = []
    if reason == "historical_issue_body_edit":
        current_body = accepted_body + "\nEdited after acceptance.\n"
        edits = [
            {
                "editedAt": "2026-05-07T06:57:46Z",
                "deletedAt": None,
                "diff": accepted_body,
                "editor": {"login": request["owner"]},
            },
            {
                "editedAt": "2026-05-07T08:00:00Z",
                "deletedAt": None,
                "diff": current_body,
                "editor": {"login": request["owner"]},
            },
        ]
    elif reason == "historical_model_rename":
        accepted_model = "GPT-5.5 Codex Historical"
        accepted_body = accepted_body.replace(request["declared_model"], accepted_model)
        current_body = accepted_body
        rename = {
            "repository": "leanprover/lean-eval-submissions",
            "commit": "d" * 40,
            "parent": "c" * 40,
            "path": "results/a-m-berns.json",
            "before_blob_sha": "e" * 40,
            "after_blob_sha": "f" * 40,
            "renamed_from": accepted_model,
            "renamed_to": request["declared_model"],
        }
    elif reason != "truncated_result_comment":
        raise AssertionError(reason)

    issue_value = issue()
    issue_value["body"] = current_body
    run_value = run()
    run_value["triggering_actor"] = {"login": request["owner"]}
    comment_value = comment()
    comment_value["updated_at"] = comment_value["created_at"]
    result_document = {
        "schema_version": 2,
        "solved": {
            accepted_model: {
                item["problem_id"]: {
                    "benchmark_commit": request["benchmark"]["commit"],
                    "issue_number": request["issue_number"],
                    "solved_at": request["accepted_at"],
                    "submission_public": True,
                    "submission_ref": request["source"]["commit"],
                    "submission_repo": request["source"]["repository"],
                    "submission_kind": request["source"]["kind"],
                }
                for item in request["results"]
            }
        },
        "user": request["owner"],
    }
    result_raw = json.dumps(result_document).encode()
    renamed_document = copy.deepcopy(result_document)
    if rename is not None:
        renamed_document["solved"][request["declared_model"]] = renamed_document[
            "solved"
        ].pop(accepted_model)
    renamed_raw = json.dumps(renamed_document).encode()
    entry = {
        "request_id": request["request_id"],
        "reason_code": reason,
        "source": {
            field: request["source"][field]
            for field in ("kind", "repository", "commit")
        },
        "issue": {
            "repository": "leanprover/lean-eval",
            "number": request["issue_number"],
            "author": request["owner"],
            "created_at": issue_value["created_at"],
            "closed_at": issue_value["closed_at"],
            "title_sha256": hashlib.sha256(issue_value["title"].encode()).hexdigest(),
            "body_binding": (
                {
                    "kind": "historical_edit",
                    "accepted_body_sha256": hashlib.sha256(
                        accepted_body.encode()
                    ).hexdigest(),
                    "current_body_sha256": hashlib.sha256(
                        current_body.encode()
                    ).hexdigest(),
                    "edit_count": len(edits),
                    "edits": [
                        {
                            "edited_at": item["editedAt"],
                            "editor": item["editor"]["login"],
                            "body_sha256": hashlib.sha256(
                                item["diff"].encode()
                            ).hexdigest(),
                        }
                        for item in edits
                    ],
                    "source_reference_binding": "unpinned",
                }
                if edits
                else {
                    "kind": "current",
                    "accepted_body_sha256": hashlib.sha256(
                        accepted_body.encode()
                    ).hexdigest(),
                    "source_reference_binding": "unpinned",
                }
            ),
        },
        "comment": {
            "id": comment_value["id"],
            "author": "github-actions[bot]",
            "created_at": comment_value["created_at"],
            "body_sha256": hashlib.sha256(
                comment_value["body"].encode()
            ).hexdigest(),
            "projection": (
                "newly_solved"
                if reason == "truncated_result_comment"
                else "pass_lines"
            ),
        },
        "workflow_run": {
            "id": run_value["id"],
            "name": run_value["name"],
            "event": run_value["event"],
            "attempt": run_value["run_attempt"],
            "actor": run_value["actor"]["login"],
            "triggering_actor": run_value["triggering_actor"]["login"],
            "head_sha": run_value["head_sha"],
            "head_branch": run_value["head_branch"],
            "path": run_value["path"],
            "created_at": run_value["created_at"],
            "updated_at": run_value["updated_at"],
            "display_title_sha256": hashlib.sha256(
                run_value["display_title"].encode()
            ).hexdigest(),
            "definition_sha256": "a" * 64,
        },
        "record_job": {
            "id": 12345,
            "name": "record",
            "started_at": "2026-05-07T07:05:40Z",
            "completed_at": "2026-05-07T07:05:55Z",
        },
        "result_commit": {
            "repository": "leanprover/lean-eval-submissions",
            "commit": "c" * 40,
            "parent": "b" * 40,
            "path": "results/a-m-berns.json",
            "blob_sha": "e" * 40,
            "committed_at": "2026-05-07T07:05:49Z",
        },
        "model_rename": rename,
    }

    class LegacyClient:
        def get(self, path):
            repository = entry["issue"]["repository"]
            if path == f"/repos/{repository}":
                return {
                    "full_name": repository,
                    "private": False,
                    "visibility": "public",
                }, 200
            if path == f"/repos/{repository}/issues/{request['issue_number']}":
                return issue_value, 200
            if path == f"/repos/{repository}/actions/runs/{run_value['id']}":
                return run_value, 200
            if path == f"/repos/{repository}/issues/comments/{comment_value['id']}":
                return comment_value, 200
            if path == f"/repos/{repository}/actions/jobs/12345":
                return {
                    **entry["record_job"],
                    "status": "completed",
                    "conclusion": "success",
                    "run_url": (
                        f"https://api.github.com/repos/{repository}/actions/runs/"
                        f"{run_value['id']}"
                    ),
                }, 200
            result_commit = entry["result_commit"]
            if path == (
                f"/repos/{result_commit['repository']}/commits/"
                f"{result_commit['commit']}"
            ):
                identity = {
                    "name": "lean-eval-bot",
                    "email": "lean-eval-bot@users.noreply.github.com",
                    "date": result_commit["committed_at"],
                }
                return {
                    "sha": result_commit["commit"],
                    "parents": [{"sha": result_commit["parent"]}],
                    "commit": {"author": identity, "committer": identity},
                    "files": [
                        {
                            "filename": result_commit["path"],
                            "sha": result_commit["blob_sha"],
                        }
                    ],
                }, 200
            if rename is not None and path == (
                f"/repos/{rename['repository']}/commits/{rename['commit']}"
            ):
                return {
                    "sha": rename["commit"],
                    "parents": [{"sha": rename["parent"]}],
                    "files": [
                        {"filename": rename["path"], "sha": rename["after_blob_sha"]}
                    ],
                }, 200
            if path == (
                f"/repos/{result_commit['repository']}/contents/{result_commit['path']}"
                f"?ref={result_commit['commit']}"
            ):
                return {
                    "sha": result_commit["blob_sha"],
                    "encoding": "base64",
                    "content": base64.b64encode(result_raw).decode(),
                    "size": len(result_raw),
                }, 200
            if rename is not None and path == (
                f"/repos/{rename['repository']}/contents/{rename['path']}"
                f"?ref={rename['commit']}"
            ):
                return {
                    "sha": rename["after_blob_sha"],
                    "encoding": "base64",
                    "content": base64.b64encode(renamed_raw).decode(),
                    "size": len(renamed_raw),
                }, 200
            if path == f"/repos/{request['source']['repository']}":
                return {
                    "full_name": request["source"]["repository"],
                    "private": False,
                    "visibility": "public",
                }, 200
            if path == (
                f"/repos/{request['source']['repository']}/git/commits/"
                f"{request['source']['commit']}"
            ):
                return {"sha": request["source"]["commit"]}, 200
            raise AssertionError(path)

        def issue_body_edits(self, repository, issue_number):
            if repository != entry["issue"]["repository"]:
                raise AssertionError(repository)
            if issue_number != request["issue_number"]:
                raise AssertionError(issue_number)
            return edits

    workflow = {
        "contract": "benchmark_repository_head",
        "repository_commit": request["benchmark"]["commit"],
        "definition_sha256": entry["workflow_run"]["definition_sha256"],
        "reviewed": True,
    }
    with mock.patch(
        "resolve_public_replay_github_evidence._workflow_binding",
        return_value=workflow,
    ):
        return _legacy_candidate(
            LegacyClient(), request, entry["issue"]["repository"], {}, entry
        )


class ResolvePublicReplayGitHubEvidenceTests(unittest.TestCase):
    def test_all_non_gist_legacy_paths_have_full_fake_client_coverage(self) -> None:
        for reason in (
            "historical_issue_body_edit",
            "historical_model_rename",
            "truncated_result_comment",
        ):
            with self.subTest(reason=reason):
                candidate = synthetic_legacy_candidate(reason)
                self.assertEqual(candidate["status"], "matched_source_available")
                self.assertEqual(candidate["legacy_reason_code"], reason)

    def test_resolves_one_exact_candidate_and_emits_no_issue_body(self) -> None:
        value = request_value()
        output = resolve(value, "8" * 64, FakeClient())
        validate_evidence(output, value)
        self.assertEqual(output["resolved_count"], 1)
        self.assertEqual(output["pending_count"], 0)
        self.assertEqual(output["shard_request_count"], 1)
        selected = output["resolutions"][0]
        self.assertEqual(selected["status"], "resolved")
        self.assertEqual(selected["selected_issue_repository"], "leanprover/lean-eval")
        encoded = json.dumps(output)
        self.assertNotIn("Submission URL", encoded)
        self.assertNotIn("Newly-solved", encoded)
        candidate = selected["candidates"][0]
        for field in (
            "issue_title_sha256",
            "issue_body_sha256",
            "issue_identity_sha256",
            "workflow_run_identity_sha256",
            "result_comment_body_sha256",
        ):
            self.assertRegex(candidate[field], r"^[0-9a-f]{64}$")
        self.assertEqual(
            candidate["reported_pass_problem_ids"],
            ["bvp_comparison", "sturm_separation"],
        )
        self.assertRegex(candidate["workflow_definition_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            candidate["issue_source_ref_sha256"],
            hashlib.sha256(canonical_bytes(None)).hexdigest(),
        )

    def test_unrelated_open_sibling_issue_does_not_strand_exact_match(self) -> None:
        class OpenSiblingClient(FakeClient):
            def get(self, path: str):
                if path == "/repos/leanprover/lean-eval-submissions/issues/144":
                    value = issue()
                    value["state"] = "open"
                    value["html_url"] = (
                        "https://github.com/leanprover/lean-eval-submissions/issues/144"
                    )
                    return value, 200
                return super().get(path)

        value = request_value()
        output = resolve(value, "8" * 64, OpenSiblingClient())
        validate_evidence(output, value)
        resolution = output["resolutions"][0]
        self.assertEqual(resolution["status"], "resolved")
        self.assertEqual(
            resolution["candidates"][1],
            {
                "issue_repository": "leanprover/lean-eval-submissions",
                "status": "issue_invalid",
                "reason_code": "issue_not_closed",
            },
        )

    def test_changed_github_issue_identity_remains_indeterminate(self) -> None:
        class ChangedIdentityClient(FakeClient):
            def get(self, path: str):
                value, status = super().get(path)
                if path == "/repos/leanprover/lean-eval/issues/144":
                    value = dict(value)
                    value["html_url"] = "https://github.com/attacker/issues/144"
                return value, status

        value = request_value()
        output = resolve(value, "8" * 64, ChangedIdentityClient())
        validate_evidence(output, value)
        candidate = output["resolutions"][0]["candidates"][0]
        self.assertEqual(candidate["status"], "probe_indeterminate")
        self.assertEqual(candidate["reason_code"], "github_identity_changed")

    def test_oversized_identity_matching_comment_makes_candidate_ambiguous(self) -> None:
        class OversizedCommentClient(FakeClient):
            def pages(self, path: str, item_key=None):
                values = super().pages(path, item_key)
                if path == "/repos/leanprover/lean-eval/issues/144/comments":
                    oversized = comment()
                    oversized["id"] = 2
                    oversized["html_url"] = (
                        "https://github.com/leanprover/lean-eval/issues/144#issuecomment-2"
                    )
                    extras = "\n".join(
                        f"- `extra_{index:04d}`: pass" for index in range(4097)
                    )
                    oversized["body"] = f"{oversized['body']}\n{extras}\n"
                    return [oversized, *values]
                return values

        value = request_value()
        output = resolve(value, "8" * 64, OversizedCommentClient())
        validate_evidence(output, value)
        resolution = output["resolutions"][0]
        self.assertEqual(resolution["status"], "ambiguous")
        self.assertEqual(
            resolution["candidates"][0]["status"],
            "result_comment_projection_too_large",
        )

    def test_unpinned_issue_identity_is_always_recomputable(self) -> None:
        value = request_value()
        output = resolve(value, "8" * 64, FakeClient())
        candidate = output["resolutions"][0]["candidates"][0]
        expected = {
            "declared_model": value["requests"][0]["declared_model"],
            "source_kind": value["requests"][0]["source"]["kind"],
            "source_repository": value["requests"][0]["source"][
                "repository"
            ].casefold(),
        }
        self.assertEqual(
            candidate["issue_identity_sha256"],
            hashlib.sha256(canonical_bytes(expected)).hexdigest(),
        )
        changed = copy.deepcopy(output)
        changed["resolutions"][0]["candidates"][0]["issue_identity_sha256"] = "9" * 64
        with self.assertRaisesRegex(EvidenceError, "issue identity"):
            validate_evidence(changed, value)

    def test_issue_author_must_equal_canonical_owner(self) -> None:
        value = request_value()
        value["requests"][0]["owner"] = "DifferentOwner"
        for result in value["requests"][0]["results"]:
            result["owner"] = "DifferentOwner"
        refresh_request_id(value)
        output = resolve(value, "8" * 64, FakeClient())
        self.assertEqual(output["resolutions"][0]["status"], "evidence_missing")
        self.assertEqual(
            output["resolutions"][0]["candidates"][0]["status"],
            "owner_mismatch",
        )

    def test_does_not_guess_between_two_matching_issue_repositories(self) -> None:
        output = resolve(request_value(), "8" * 64, FakeClient(second_match=True))
        self.assertEqual(output["resolved_count"], 0)
        self.assertEqual(output["resolutions"][0]["status"], "ambiguous")

    def test_source_unavailable_is_pending_not_permanent_verdict(self) -> None:
        output = resolve(request_value(), "8" * 64, FakeClient(source_available=False))
        self.assertEqual(output["source_unavailable_count"], 1)
        self.assertEqual(output["pending_count"], 1)
        self.assertEqual(output["resolutions"][0]["status"], "source_unavailable")

    def test_auditable_timing_passes_and_registry_digest_are_revalidated(self) -> None:
        value = request_value()
        output = resolve(value, "8" * 64, FakeClient())
        for field, replacement, message in (
            ("result_comment_created_at", "2026-05-07T07:06:49Z", "timing"),
            ("reported_pass_problem_ids", ["sturm_separation"], "pass projection"),
            ("issue_body_sha256", "not-a-digest", "digest"),
        ):
            changed = copy.deepcopy(output)
            changed["resolutions"][0]["candidates"][0][field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(
                EvidenceError, message
            ):
                validate_evidence(changed, value)
        changed = copy.deepcopy(output)
        changed["workflow_definition_registry_sha256"] = "9" * 64
        with self.assertRaisesRegex(EvidenceError, "workflow registry"):
            validate_evidence(changed, value)

    def test_rejects_workflow_on_wrong_benchmark_commit(self) -> None:
        client = FakeClient()
        original_pages = client.pages

        def pages(path, item_key=None):
            values = original_pages(path, item_key)
            if "/actions/runs?" in path:
                values[0]["head_sha"] = "9" * 40
            return values

        client.pages = pages
        output = resolve(request_value(), "8" * 64, client)
        candidate = output["resolutions"][0]["candidates"][0]
        self.assertEqual(candidate["status"], "workflow_contract_mismatch")

    def test_rejects_when_an_expected_problem_is_not_passing(self) -> None:
        client = FakeClient()
        original_pages = client.pages

        def pages(path, item_key=None):
            values = original_pages(path, item_key)
            if path.endswith("/issues/144/comments"):
                values[0]["body"] = values[0]["body"].replace(
                    "- `bvp_comparison`: pass\n", ""
                )
            return values

        client.pages = pages
        output = resolve(request_value(), "8" * 64, client)
        candidate = output["resolutions"][0]["candidates"][0]
        self.assertEqual(candidate["status"], "result_comment_missing")

    def test_exact_pinned_issue_ref_must_equal_recorded_source_commit(self) -> None:
        client = FakeClient()
        original_get = client.get

        def get(path):
            value, status = original_get(path)
            if path == "/repos/leanprover/lean-eval/issues/144":
                value["body"] = value["body"].replace(
                    "lean-eval-submissions",
                    "lean-eval-submissions/tree/" + "9" * 40,
                )
            return value, status

        client.get = get
        output = resolve(request_value(), "8" * 64, client)
        candidate = output["resolutions"][0]["candidates"][0]
        self.assertEqual(candidate["status"], "source_mismatch")

    def test_rejects_inconsistent_request_counts(self) -> None:
        value = request_value()
        value["result_count"] = 3
        with self.assertRaisesRegex(EvidenceError, "result count"):
            validate_requests(value)

    def test_request_id_is_recomputed_from_the_closed_identity(self) -> None:
        value = request_value()
        value["requests"][0]["declared_model"] = "forged"
        with self.assertRaisesRegex(EvidenceError, "does not bind"):
            validate_requests(value)

    def test_request_rejects_dot_repository_segments(self) -> None:
        for repository in ("./source", "owner/.."):
            with self.subTest(repository=repository):
                value = request_value()
                value["requests"][0]["source"]["repository"] = repository
                refresh_request_id(value)
                with self.assertRaisesRegex(EvidenceError, "repository"):
                    validate_requests(value)

    def test_request_contract_rejects_unknown_fields(self) -> None:
        value = request_value()
        value["requests"][0]["source_bytes"] = "must never appear"
        with self.assertRaisesRegex(EvidenceError, "fields are not closed"):
            validate_requests(value)

    def test_request_contract_rejects_duplicate_problem_identity(self) -> None:
        value = request_value()
        value["requests"][0]["results"][1]["problem_id"] = "sturm_separation"
        with self.assertRaisesRegex(EvidenceError, "problem identity is duplicated"):
            validate_requests(value)

    def test_evidence_recomputes_status_and_counters(self) -> None:
        value = request_value()
        output = resolve(value, "8" * 64, FakeClient())
        output["resolved_count"] = 0
        output["pending_count"] = 1
        with self.assertRaisesRegex(EvidenceError, "status counts"):
            validate_evidence(output, value)

        output = resolve(value, "8" * 64, FakeClient())
        output["resolutions"][0]["status"] = "source_unavailable"
        with self.assertRaisesRegex(EvidenceError, "classification"):
            validate_evidence(output, value)

    def test_evidence_urls_are_exactly_cross_bound(self) -> None:
        value = request_value()
        output = resolve(value, "8" * 64, FakeClient())
        output["resolutions"][0]["candidates"][0]["issue_url"] = (
            "https://github.com/leanprover/lean-eval/issues/145"
        )
        with self.assertRaisesRegex(EvidenceError, "cross-bound"):
            validate_evidence(output, value)

    def test_rejects_run_with_wrong_repository_or_workflow_path(self) -> None:
        for field, replacement, expected in (
            ("head_branch", "feature", "workflow_run_missing"),
            ("path", ".github/workflows/decoy.yml", "workflow_contract_mismatch"),
            (
                "html_url",
                "https://github.com/leanprover/lean-eval/actions/runs/1",
                "workflow_run_missing",
            ),
        ):
            with self.subTest(field=field):
                client = FakeClient()
                original_pages = client.pages

                def pages(path, item_key=None, *, selected=field, value=replacement):
                    items = original_pages(path, item_key)
                    if "/actions/runs?" in path:
                        items[0][selected] = value
                    return items

                client.pages = pages
                output = resolve(request_value(), "8" * 64, client)
                self.assertEqual(
                    output["resolutions"][0]["candidates"][0]["status"],
                    expected,
                )

    def test_duplicate_expected_pass_line_is_not_accepted(self) -> None:
        client = FakeClient()
        original_pages = client.pages

        def pages(path, item_key=None):
            items = original_pages(path, item_key)
            if path.endswith("/issues/144/comments"):
                items[0]["body"] += "- `sturm_separation`: pass\n"
            return items

        client.pages = pages
        output = resolve(request_value(), "8" * 64, client)
        self.assertEqual(
            output["resolutions"][0]["candidates"][0]["status"],
            "result_comment_missing",
        )

    def test_redirect_handler_never_forwards_authorization(self) -> None:
        self.assertIsNone(
            _RejectRedirects().redirect_request(
                None, None, 302, "moved", {}, "https://evil"
            )
        )
        client = GitHubClient("token")
        proxy_handlers = [
            handler
            for handler in client._opener.handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertEqual(proxy_handlers, [])

    def test_response_caches_are_lru_bounded(self) -> None:
        client = GitHubClient("token")
        for index in range(600):
            client._cache_put(
                client._get_cache, (str(index), True), (index, 200), 512
            )
        self.assertEqual(len(client._get_cache), 512)
        self.assertNotIn(("0", True), client._get_cache)
        for index in range(80):
            client._cache_put(client._page_cache, (str(index), None), [], 64)
        self.assertEqual(len(client._page_cache), 64)
        self.assertNotIn(("0", None), client._page_cache)

    def test_gist_bodies_are_never_cached(self) -> None:
        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _limit):
                return b'{"id":"fixture"}'

        client = GitHubClient("token")
        client._opener.open = mock.Mock(
            side_effect=[Response(), Response(), Response()]
        )
        gist_id = "a" * 32
        commit = "b" * 40
        path = f"/gists/{gist_id}/{commit}"
        self.assertEqual(client.get_public_gist(gist_id, commit)[1], 200)
        self.assertEqual(client.get_public_gist(gist_id, commit)[1], 200)
        self.assertEqual(client._opener.open.call_count, 2)
        self.assertNotIn(path, client._get_cache)

        for call in client._opener.open.call_args_list:
            request = call.args[0]
            self.assertIsNone(request.get_header("Authorization"))

        self.assertEqual(client.get("/repos/leanprover/lean-eval")[1], 200)
        authenticated = client._opener.open.call_args_list[-1].args[0]
        self.assertEqual(authenticated.get_header("Authorization"), "Bearer token")

        client._public_gist_api_requests = MAX_PUBLIC_GIST_API_REQUESTS_PER_SHARD
        with self.assertRaisesRegex(
            ProbeIndeterminate, "public_gist_budget_exhausted"
        ):
            client.get_public_gist(gist_id, commit)
        self.assertEqual(client._opener.open.call_count, 3)

        transient = GitHubClient("token")
        transient._opener.open = mock.Mock(side_effect=TimeoutError())
        with self.assertRaisesRegex(ProbeIndeterminate, "github_request_failed"):
            transient.get_public_gist(gist_id, commit)
        self.assertEqual(transient._opener.open.call_count, 1)
        self.assertEqual(transient._public_gist_api_requests, 1)

    def test_public_gist_probe_rejects_noncanonical_identity(self) -> None:
        client = GitHubClient("token")
        for gist_id, commit in (
            ("", "b" * 40),
            ("g" * 20, "b" * 40),
            ("A" * 32, "b" * 40),
            ("a" * 32, "b" * 39),
            ("a" * 32, "B" * 40),
        ):
            with self.subTest(gist_id=gist_id, commit=commit):
                with self.assertRaisesRegex(EvidenceError, "Gist probe identity"):
                    client.get_public_gist(gist_id, commit)

        client._opener.open = mock.MagicMock()
        client._opener.open.return_value.__enter__.return_value = mock.Mock(
            status=200,
            headers={},
            read=mock.Mock(return_value=b'{"id":"fixture"}'),
        )
        self.assertEqual(client.get_public_gist("a" * 20, "b" * 40)[1], 200)

    def test_gist_resolution_uses_the_public_token_free_probe(self) -> None:
        gist_id = "a" * 32
        value = request_value()
        value["requests"][0]["source"] = {
            "kind": "gist",
            "repository": f"A-M-Berns/{gist_id}",
            "commit": SOURCE,
            "visibility": "public",
        }
        refresh_request_id(value)

        class GistClient(FakeClient):
            public_probe = None

            def __init__(self, response, status=200):
                super().__init__()
                self.response = response
                self.status = status

            def get(self, path):
                if path == "/repos/leanprover/lean-eval/issues/144":
                    value = issue()
                    value["body"] = """### Submission URL

https://gist.github.com/A-M-Berns/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/2222222222222222222222222222222222222222

### Model

GPT-5.5 Codex
"""
                    return value, 200
                return super().get(path)

            def get_public_gist(self, selected_gist_id, commit):
                self.public_probe = (selected_gist_id, commit)
                return self.response, self.status

        available = {
            "id": gist_id,
            "public": True,
            "owner": {"login": "A-M-Berns"},
            "history": [{"version": SOURCE}],
        }
        client = GistClient(available)
        output = resolve(value, "8" * 64, client)
        self.assertEqual(output["resolutions"][0]["status"], "resolved")
        self.assertEqual(client.public_probe, (gist_id, SOURCE))

        cases = (
            (
                {**available, "id": "b" * 32},
                200,
                "source_probe_indeterminate",
                "source_repository_identity_changed",
            ),
            (
                {**available, "id": "b" * 32, "public": False},
                200,
                "source_probe_indeterminate",
                "source_repository_identity_changed",
            ),
            (
                {**available, "owner": {"login": "renamed"}},
                200,
                "source_probe_indeterminate",
                "source_repository_identity_changed",
            ),
            (
                {**available, "history": []},
                200,
                "source_probe_indeterminate",
                "source_probe_response_invalid",
            ),
            (
                {key: item for key, item in available.items() if key != "public"},
                200,
                "source_probe_indeterminate",
                "source_probe_response_invalid",
            ),
            (
                {**available, "public": False},
                200,
                "source_unavailable",
                None,
            ),
            (None, 404, "source_unavailable", None),
        )
        for response, status, expected, reason in cases:
            with self.subTest(expected=expected, reason=reason):
                output = resolve(value, "8" * 64, GistClient(response, status))
                resolution = output["resolutions"][0]
                self.assertEqual(resolution["status"], expected)
                candidate = resolution["candidates"][0]
                if reason is not None:
                    self.assertEqual(candidate["source_probe_reason_code"], reason)
                validate_evidence(output, value)

    def test_public_gist_probe_shard_has_an_anonymous_api_budget(self) -> None:
        template = request_value()
        requests = []
        for index in range(21):
            item_value = copy.deepcopy(template)
            item = item_value["requests"][0]
            item["issue_number"] = 1000 + index
            item["source"] = {
                "kind": "gist",
                "repository": f"A-M-Berns/{index + 1:032x}",
                "commit": SOURCE,
                "visibility": "public",
            }
            item["results"] = [
                {
                    "owner": "A-M-Berns",
                    "problem_id": f"problem_{index}",
                    "result_id": "r2_" + f"{index:064x}",
                    "statement_revision": 1,
                }
            ]
            refresh_request_id(item_value)
            requests.append(item)
        template["requests"] = sorted(requests, key=lambda item: item["request_id"])
        template["request_count"] = len(requests)
        template["result_count"] = len(requests)
        _require_public_gist_probe_budget(template["requests"][:20])
        with self.assertRaisesRegex(EvidenceError, "anonymous API budget"):
            resolve(template, "8" * 64, FakeClient())

    def test_bounded_input_and_exclusive_output_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            oversized = root / "oversized.json"
            oversized.write_bytes(b"12345")
            with self.assertRaisesRegex(EvidenceError, "size limit"):
                _read_bounded(oversized, 4)

            target = root / "target.json"
            target.write_text("unchanged", encoding="utf-8")
            output = root / "output.json"
            output.symlink_to(target)
            with self.assertRaises(FileExistsError):
                _write_exclusive(output, {"secret": "never"})
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")

    def test_canonical_requests_hash_is_carried(self) -> None:
        value = request_value()
        raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        output = resolve(value, hashlib.sha256(raw).hexdigest(), FakeClient())
        self.assertEqual(
            output["resolution_requests_sha256"], hashlib.sha256(raw).hexdigest()
        )

    def test_acceptance_comment_may_follow_record_by_narrow_bounded_lag(self) -> None:
        value = request_value()
        value["requests"][0]["accepted_at"] = "2026-05-07T07:05:48Z"
        refresh_request_id(value)
        output = resolve(value, "8" * 64, FakeClient())
        self.assertEqual(output["resolutions"][0]["status"], "resolved")

    def test_just_outside_run_or_comment_window_requires_adjudication(self) -> None:
        for target in ("run", "comment"):
            with self.subTest(target=target):
                client = FakeClient()
                original_pages = client.pages

                def pages(path, item_key=None, *, selected=target):
                    values = original_pages(path, item_key)
                    if selected == "run" and "/actions/runs?" in path:
                        values[0]["updated_at"] = "2026-05-07T07:10:50Z"
                    if selected == "comment" and path.endswith("/comments"):
                        values[0]["created_at"] = "2026-05-07T07:06:00Z"
                    return values

                client.pages = pages
                output = resolve(request_value(), "8" * 64, client)
                self.assertEqual(
                    output["resolutions"][0]["status"], "timing_indeterminate"
                )
                candidate = output["resolutions"][0]["candidates"][0]
                self.assertEqual(candidate["status"], "timing_indeterminate")
                self.assertRegex(candidate["evidence_identity_sha256"], r"^[0-9a-f]{64}$")
                validate_evidence(output, request_value())

    def test_strong_issue_identity_with_ambiguous_run_blocks_other_match(self) -> None:
        client = FakeClient(second_match=True)
        original_pages = client.pages

        def pages(path, item_key=None):
            values = original_pages(path, item_key)
            if path.startswith("/repos/leanprover/lean-eval/actions/runs?"):
                values.append(copy.deepcopy(values[0]))
                values[-1]["id"] += 1
                values[-1]["html_url"] = (
                    "https://github.com/leanprover/lean-eval/actions/runs/"
                    + str(values[-1]["id"])
                )
            return values

        client.pages = pages
        output = resolve(request_value(), "8" * 64, client)
        self.assertEqual(output["resolutions"][0]["status"], "ambiguous")
        self.assertEqual(
            output["resolutions"][0]["candidates"][0]["status"],
            "workflow_run_ambiguous",
        )
        validate_evidence(output, request_value())

    def test_per_candidate_probe_errors_and_repository_renames_stay_pending(self) -> None:
        class ProbeClient(FakeClient):
            def get(self, path):
                if path == "/repos/leanprover/lean-eval/issues/144":
                    raise ProbeIndeterminate("github_legal_restriction")
                return super().get(path)

        output = resolve(request_value(), "8" * 64, ProbeClient())
        self.assertEqual(output["resolutions"][0]["status"], "probe_indeterminate")
        self.assertEqual(output["probe_indeterminate_count"], 1)
        validate_evidence(output, request_value())

        class RenamedSourceClient(FakeClient):
            def get(self, path):
                value, status = super().get(path)
                if path == "/repos/A-M-Berns/lean-eval-submissions":
                    value = dict(value)
                    value["full_name"] = "A-M-Berns/renamed"
                return value, status

        renamed = resolve(request_value(), "8" * 64, RenamedSourceClient())
        self.assertEqual(
            renamed["resolutions"][0]["status"], "source_probe_indeterminate"
        )
        self.assertEqual(
            renamed["resolutions"][0]["candidates"][0]["source_probe_reason_code"],
            "source_repository_identity_changed",
        )

    def test_evidence_repository_preflight_requires_public_exact_identity(self) -> None:
        class PrivateClient(FakeClient):
            def get(self, path):
                value, status = super().get(path)
                if path == "/repos/leanprover/lean-eval":
                    value = dict(value)
                    value["private"] = True
                return value, status

        output = resolve(request_value(), "8" * 64, PrivateClient())
        candidate = output["resolutions"][0]["candidates"][0]
        self.assertEqual(candidate["status"], "probe_indeterminate")
        self.assertEqual(candidate["reason_code"], "evidence_repository_not_public")

    def test_registry_value_and_exact_raw_digest_are_mandatory(self) -> None:
        value = request_value()
        requests_digest = hashlib.sha256(
            canonical_document_bytes(value)
        ).hexdigest()
        with self.assertRaisesRegex(EvidenceError, "raw workflow registry"):
            _resolve(value, requests_digest, FakeClient())
        registry, _ = registry_bytes()
        with self.assertRaisesRegex(EvidenceError, "raw workflow registry"):
            _resolve(value, requests_digest, FakeClient(), registry)
        with self.assertRaisesRegex(EvidenceError, "canonical or digest-bound"):
            _resolve(value, requests_digest, FakeClient(), registry, 7)

    def test_resolver_recomputes_both_canonical_input_digests(self) -> None:
        value = request_value()
        requests_digest = hashlib.sha256(
            canonical_document_bytes(value)
        ).hexdigest()
        workflow_registry, workflow_digest = registry_bytes()
        with self.assertRaisesRegex(
            EvidenceError, "resolution requests are not canonical or digest-bound"
        ):
            _resolve(
                value,
                "f" * 64,
                FakeClient(),
                workflow_registry,
                workflow_digest,
            )
        with self.assertRaisesRegex(
            EvidenceError, "workflow definition registry is not canonical or digest-bound"
        ):
            _resolve(
                value,
                requests_digest,
                FakeClient(),
                workflow_registry,
                "f" * 64,
            )

    def test_evidence_validator_recomputes_both_canonical_input_digests(self) -> None:
        requests = request_value()
        requests_digest = hashlib.sha256(
            canonical_document_bytes(requests)
        ).hexdigest()
        workflow_registry, workflow_digest = registry_bytes()
        evidence = _resolve(
            requests,
            requests_digest,
            FakeClient(),
            workflow_registry,
            workflow_digest,
        )

        stale_requests = copy.deepcopy(evidence)
        stale_requests["resolution_requests_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            EvidenceError, "canonical resolution requests"
        ):
            _validate_evidence(
                stale_requests,
                requests,
                workflow_registry,
                workflow_digest,
            )

        stale_workflow = copy.deepcopy(evidence)
        stale_workflow["workflow_definition_registry_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            EvidenceError, "workflow registry is not canonical or digest-bound"
        ):
            _validate_evidence(
                stale_workflow,
                requests,
                workflow_registry,
                "f" * 64,
            )

    def test_split_workflow_binds_evaluator_separately_from_benchmark(self) -> None:
        class SplitClient(FakeClient):
            def get(self, path):
                if path == "/repos/leanprover/lean-eval/issues/144":
                    return None, 404
                if path == "/repos/leanprover/lean-eval-submissions/issues/144":
                    value = issue()
                    value["html_url"] = (
                        "https://github.com/leanprover/lean-eval-submissions/issues/144"
                    )
                    return value, 200
                return super().get(path)

            def pages(self, path, item_key=None):
                values = super().pages(path, item_key)
                if "/actions/runs?" in path:
                    values[0]["head_sha"] = "a" * 40
                    values[0]["repository"]["full_name"] = (
                        "leanprover/lean-eval-submissions"
                    )
                    values[0]["head_repository"]["full_name"] = (
                        "leanprover/lean-eval-submissions"
                    )
                return values

        client = SplitClient()
        workflow_value, _ = client.get(
            "/repos/leanprover/lean-eval-submissions/contents/"
            ".github/workflows/submission.yml?ref=" + "a" * 40
        )
        workflow = base64.b64decode(workflow_value["content"])
        registry = {
            "schema_version": 1,
            "kind": "historical_public_replay_workflow_definition_registry",
            "repository": "leanprover/lean-eval-submissions",
            "workflow_path": ".github/workflows/submission.yml",
            "contracts": [
                {
                    "evaluator_commit": "a" * 40,
                    "definition_sha256": hashlib.sha256(workflow).hexdigest(),
                    "contract": "split_repository_recorded_benchmark_v1",
                }
            ],
        }
        output = resolve(request_value(), "8" * 64, client, registry)
        self.assertEqual(output["resolutions"][0]["status"], "resolved")
        candidate = output["resolutions"][0]["candidates"][1]
        self.assertEqual(
            candidate["workflow_contract"],
            "split_repository_recorded_benchmark_v1",
        )
        self.assertEqual(candidate["workflow_repository_commit"], "a" * 40)
        self.assertNotEqual(candidate["workflow_repository_commit"], BENCHMARK)

    def test_unreviewed_or_decoy_split_workflow_never_resolves(self) -> None:
        class SplitClient(FakeClient):
            def get(self, path):
                if path == "/repos/leanprover/lean-eval/issues/144":
                    return None, 404
                if path == "/repos/leanprover/lean-eval-submissions/issues/144":
                    value = issue()
                    value["html_url"] = (
                        "https://github.com/leanprover/lean-eval-submissions/issues/144"
                    )
                    return value, 200
                return super().get(path)

            def pages(self, path, item_key=None):
                values = super().pages(path, item_key)
                if "/actions/runs?" in path:
                    values[0]["head_sha"] = "a" * 40
                    values[0]["repository"]["full_name"] = (
                        "leanprover/lean-eval-submissions"
                    )
                    values[0]["head_repository"]["full_name"] = (
                        "leanprover/lean-eval-submissions"
                    )
                return values

        output = resolve(request_value(), "8" * 64, SplitClient())
        self.assertEqual(
            output["resolutions"][0]["status"], "workflow_contract_unreviewed"
        )
        candidate = output["resolutions"][0]["candidates"][1]
        self.assertEqual(candidate["status"], "workflow_contract_unreviewed")
        # The fixture bytes consist only of the old recognizer's fragments.
        # Their presence is no longer authority to assert the contract.
        self.assertRegex(candidate["workflow_definition_sha256"], r"^[0-9a-f]{64}$")

    def test_registered_definition_rejects_comment_or_conflicting_step_change(self) -> None:
        commit = "a" * 40
        path = (
            "/repos/leanprover/lean-eval-submissions/contents/"
            ".github/workflows/submission.yml?ref=" + commit
        )
        baseline = FakeClient()
        value, _ = baseline.get(path)
        raw = base64.b64decode(value["content"])
        registry = {
            commit: {
                "evaluator_commit": commit,
                "definition_sha256": hashlib.sha256(raw).hexdigest(),
                "contract": "split_repository_recorded_benchmark_v1",
            }
        }

        class ChangedClient(FakeClient):
            def get(self, requested):
                value, status = super().get(requested)
                if requested == path:
                    changed = base64.b64decode(value["content"])
                    changed += b"\n# reviewed fragments above are dead\nref: attacker\n"
                    value = dict(value)
                    value["content"] = base64.b64encode(changed).decode()
                    value["size"] = len(changed)
                return value, status

        run_value = run()
        run_value["head_sha"] = commit
        with self.assertRaisesRegex(EvidenceError, "bytes do not match"):
            _workflow_binding(
                ChangedClient(),
                request_value()["requests"][0],
                "leanprover/lean-eval-submissions",
                run_value,
                registry,
            )

    def test_deterministic_date_local_shard_contains_exact_request(self) -> None:
        value = request_value()
        request_id = value["requests"][0]["request_id"]
        expected_index = next(
            index
            for index in range(16)
            if any(
                request["request_id"] == request_id
                for request in shard_requests(value["requests"], index, 16)
            )
        )
        output = resolve(
            value, "8" * 64, FakeClient(), shard_index=expected_index, shard_count=16
        )
        validate_evidence(output, value)
        self.assertEqual(output["shard_index"], expected_index)
        self.assertEqual(output["shard_count"], 16)
        empty = resolve(
            value,
            "8" * 64,
            FakeClient(),
            shard_index=(expected_index + 1) % 16,
            shard_count=16,
        )
        validate_evidence(empty, value)
        self.assertEqual(empty["shard_request_count"], 0)
        self.assertEqual(empty["shard_result_count"], 0)
        self.assertEqual(empty["resolutions"], [])

    def test_published_evidence_schema_is_strict(self) -> None:
        schema = json.loads(
            (
                ROOT / "schemas" / "public-replay-github-evidence-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertIs(schema["additionalProperties"], False)
        self.assertIs(schema["$defs"]["resolution"]["additionalProperties"], False)
        for candidate in schema["$defs"]["candidate"]["oneOf"]:
            self.assertIs(candidate["additionalProperties"], False)

    def test_reviewed_workflow_registry_snapshot_and_schema_are_strict(self) -> None:
        registry_path = (
            ROOT / "configuration/public-replay-workflow-definitions-v1.json"
        )
        registry_raw = registry_path.read_bytes()
        registry = json.loads(registry_raw)
        reviewed = validate_workflow_registry(registry)
        self.assertEqual(len(reviewed), 133)
        self.assertEqual(
            len({entry["definition_sha256"] for entry in reviewed.values()}),
            14,
        )
        self.assertEqual(
            hashlib.sha256(registry_raw).hexdigest(),
            "24a4ed157b5b62fc52a58c530cc7a72073108135d2554684a32664174524637a",
        )
        schema = json.loads(
            (
                ROOT / "schemas/public-replay-workflow-definitions-v1.schema.json"
            ).read_text()
        )
        self.assertIs(schema["additionalProperties"], False)
        self.assertIs(schema["properties"]["contracts"]["items"]["additionalProperties"], False)

    def test_legacy_adjudication_registry_is_exact_closed_and_schema_valid(self) -> None:
        path = ROOT / "configuration/public-replay-legacy-adjudications-v1.json"
        raw = path.read_bytes()
        value = json.loads(raw)
        self.assertEqual(raw, canonical_document_bytes(value))
        reviewed = validate_legacy_adjudication_registry(value)
        self.assertEqual(len(reviewed), 5)
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "4df6682b0e8b0ff129235c286aebf3322f37b002c846cc9fc8b14c054acf4ed1",
        )
        schema = json.loads(
            (ROOT / "schemas/public-replay-legacy-adjudications-v1.schema.json").read_text()
        )
        self.assertIs(schema["additionalProperties"], False)
        self.assertIs(
            schema["$defs"]["adjudication"]["additionalProperties"], False
        )
        changed = copy.deepcopy(value)
        changed["adjudications"][0]["record_job"]["completed_at"] = (
            "2026-07-28T15:22:40Z"
        )
        with self.assertRaisesRegex(EvidenceError, "outside its record job"):
            validate_legacy_adjudication_registry(changed)
        changed = copy.deepcopy(value)
        changed["adjudications"][0]["unexpected"] = True
        with self.assertRaisesRegex(EvidenceError, "fields are not closed"):
            validate_legacy_adjudication_registry(changed)
        changed = copy.deepcopy(value)
        changed["adjudications"].pop()
        with self.assertRaisesRegex(EvidenceError, "request set is not exact"):
            validate_legacy_adjudication_registry(changed)

    def test_legacy_candidate_digest_cannot_be_attached_to_another_request(self) -> None:
        requests = request_value()
        output = resolve(requests, "8" * 64, FakeClient())
        adjudications, adjudication_digest = adjudication_bytes()
        output["legacy_adjudication_registry_sha256"] = adjudication_digest
        output["resolutions"][0]["candidates"][0][
            "legacy_adjudication_sha256"
        ] = hashlib.sha256(
            canonical_bytes(adjudications["adjudications"][0])
        ).hexdigest()
        output["resolutions"][0]["candidates"][0].update(
            _legacy_candidate_projection(
                adjudications["adjudications"][0], requests["requests"][0]
            )
        )
        with self.assertRaisesRegex(EvidenceError, "legacy adjudication mode is invalid"):
            workflow_registry, workflow_digest = registry_bytes()
            _validate_evidence(
                output,
                requests,
                workflow_registry,
                workflow_digest,
                adjudications,
                adjudication_digest,
            )

    def test_unpinned_legacy_source_commit_cannot_be_laundered(self) -> None:
        fixture = json.loads(
            (ROOT / "tests/fixtures/legacy-gist-result-records-v1.json").read_text()
        )[0]
        request = fixture["request"]
        requests = {
            "schema_version": 1,
            "kind": "historical_public_replay_resolution_requests",
            "source_repository": "leanprover/lean-eval-submissions",
            "source_commit": "a" * 40,
            "inventory_sha256": "b" * 64,
            "request_count": 1,
            "result_count": len(request["results"]),
            "requests": [request],
        }
        requests_digest = hashlib.sha256(
            canonical_document_bytes(requests)
        ).hexdigest()
        workflow_registry, workflow_digest = registry_bytes()
        adjudications, adjudication_digest = adjudication_bytes()
        entry = next(
            item
            for item in adjudications["adjudications"]
            if item["request_id"] == request["request_id"]
        )
        self.assertEqual(
            entry["issue"]["body_binding"]["source_reference_binding"],
            "unpinned",
        )
        legacy_candidate = {
            "issue_repository": entry["issue"]["repository"],
            "status": "matched_source_available",
            **_legacy_candidate_projection(entry, request),
        }
        candidates = [
            legacy_candidate
            if repository == entry["issue"]["repository"]
            else {"issue_repository": repository, "status": "issue_not_found"}
            for repository in request["candidate_issue_repositories"]
        ]
        evidence = {
            "schema_version": 1,
            "kind": "historical_public_replay_github_evidence",
            "source_repository": requests["source_repository"],
            "source_commit": requests["source_commit"],
            "inventory_sha256": requests["inventory_sha256"],
            "resolution_requests_sha256": requests_digest,
            "workflow_definition_registry_sha256": workflow_digest,
            "legacy_adjudication_registry_sha256": adjudication_digest,
            "request_count": 1,
            "result_count": len(request["results"]),
            "shard_index": 0,
            "shard_count": 1,
            "shard_request_count": 1,
            "shard_result_count": len(request["results"]),
            "resolved_count": 1,
            "source_unavailable_count": 0,
            "source_indeterminate_count": 0,
            "probe_indeterminate_count": 0,
            "timing_indeterminate_count": 0,
            "workflow_contract_unreviewed_count": 0,
            "pending_count": 0,
            "resolutions": [
                {
                    "request_id": request["request_id"],
                    "status": "resolved",
                    "selected_issue_repository": entry["issue"]["repository"],
                    "candidates": candidates,
                }
            ],
        }
        _validate_evidence(
            evidence,
            requests,
            workflow_registry,
            workflow_digest,
            adjudications,
            adjudication_digest,
        )

        changed_registry = copy.deepcopy(adjudications)
        changed_entry = next(
            item
            for item in changed_registry["adjudications"]
            if item["request_id"] == request["request_id"]
        )
        changed_entry["source"]["commit"] = "f" * 40
        changed_digest = hashlib.sha256(
            canonical_document_bytes(changed_registry)
        ).hexdigest()
        laundered = copy.deepcopy(evidence)
        laundered["legacy_adjudication_registry_sha256"] = changed_digest
        laundered_candidate = next(
            item
            for item in laundered["resolutions"][0]["candidates"]
            if item["issue_repository"] == changed_entry["issue"]["repository"]
        )
        laundered_candidate.update(
            _legacy_candidate_projection(changed_entry, request)
        )
        with self.assertRaisesRegex(EvidenceError, "not cross-bound to its request"):
            _validate_evidence(
                laundered,
                requests,
                workflow_registry,
                workflow_digest,
                changed_registry,
                changed_digest,
            )

    def test_historical_result_binding_rejects_extra_or_changed_records(self) -> None:
        request = request_value()["requests"][0]
        record = {
            "benchmark_commit": request["benchmark"]["commit"],
            "issue_number": request["issue_number"],
            "model": request["declared_model"],
            "solved_at": request["accepted_at"],
            "submission_public": True,
            "submission_ref": request["source"]["commit"],
            "submission_repo": request["source"]["repository"],
        }
        document = {
            "schema_version": 1,
            "solved": {
                item["problem_id"]: dict(record) for item in request["results"]
            },
            "user": request["owner"],
        }
        _validate_historical_results(document, request, request["declared_model"])
        changed = copy.deepcopy(document)
        changed["solved"]["decoy_problem"] = dict(record)
        with self.assertRaisesRegex(EvidenceError, "problem set"):
            _validate_historical_results(
                changed, request, request["declared_model"]
            )
        changed = copy.deepcopy(document)
        changed["solved"][request["results"][0]["problem_id"]][
            "submission_ref"
        ] = "f" * 40
        with self.assertRaisesRegex(EvidenceError, "not cross-bound"):
            _validate_historical_results(
                changed, request, request["declared_model"]
            )

    def test_live_shaped_schema_v2_gists_resolve_and_projection_is_closed(self) -> None:
        fixtures = json.loads(
            (ROOT / "tests/fixtures/legacy-gist-result-records-v1.json").read_text()
        )
        adjudication_value, adjudication_digest = adjudication_bytes()
        adjudications = validate_legacy_adjudication_registry(adjudication_value)
        workflow_registry, workflow_digest = registry_bytes()

        for fixture in fixtures:
            request = fixture["request"]
            entry = adjudications[request["request_id"]]
            repository = entry["issue"]["repository"]
            issue_url = f"https://github.com/{repository}/issues/{request['issue_number']}"
            workflow_url = (
                f"https://github.com/{repository}/actions/runs/"
                f"{entry['workflow_run']['id']}"
            )
            result_raw = json.dumps(fixture["result_document"]).encode()
            case = self

            class LegacyGistClient:
                def get(self, path):
                    if path == f"/repos/{repository}":
                        return {
                            "full_name": repository,
                            "private": False,
                            "visibility": "public",
                        }, 200
                    if path == f"/repos/{repository}/issues/{request['issue_number']}":
                        return {
                            "number": request["issue_number"],
                            "html_url": issue_url,
                            "state": "closed",
                            "user": {"login": entry["issue"]["author"]},
                            "created_at": entry["issue"]["created_at"],
                            "closed_at": entry["issue"]["closed_at"],
                            "title": {
                                46: "[submission] two_plus_two via Grok 4.3",
                                47: "[submission] two_plus_two via Qwen3.6 Max",
                            }[request["issue_number"]],
                            "body": fixture["issue_body"],
                        }, 200
                    if path == (
                        f"/repos/{repository}/actions/runs/"
                        f"{entry['workflow_run']['id']}"
                    ):
                        run = entry["workflow_run"]
                        return {
                            "id": run["id"],
                            "name": run["name"],
                            "event": run["event"],
                            "run_attempt": run["attempt"],
                            "actor": {"login": run["actor"]},
                            "triggering_actor": {"login": run["triggering_actor"]},
                            "head_sha": run["head_sha"],
                            "head_branch": run["head_branch"],
                            "path": run["path"],
                            "created_at": run["created_at"],
                            "updated_at": run["updated_at"],
                            "display_title": {
                                46: "[submission] two_plus_two via Grok 4.3",
                                47: "[submission] two_plus_two via Qwen3.6 Max",
                            }[request["issue_number"]],
                            "status": "completed",
                            "conclusion": "success",
                            "repository": {"full_name": repository},
                            "head_repository": {"full_name": repository},
                            "html_url": workflow_url,
                        }, 200
                    if path == (
                        f"/repos/{repository}/issues/comments/{entry['comment']['id']}"
                    ):
                        comment = entry["comment"]
                        return {
                            "id": comment["id"],
                            "user": {"login": comment["author"]},
                            "created_at": comment["created_at"],
                            "updated_at": comment["created_at"],
                            "html_url": f"{issue_url}#issuecomment-{comment['id']}",
                            "body": fixture["comment_body"],
                        }, 200
                    if path == (
                        f"/repos/{repository}/actions/jobs/{entry['record_job']['id']}"
                    ):
                        job = entry["record_job"]
                        return {
                            **job,
                            "status": "completed",
                            "conclusion": "success",
                            "run_url": (
                                f"https://api.github.com/repos/{repository}/actions/"
                                f"runs/{entry['workflow_run']['id']}"
                            ),
                        }, 200
                    commit = entry["result_commit"]
                    if path == (
                        f"/repos/{commit['repository']}/commits/{commit['commit']}"
                    ):
                        identity = {
                            "name": "lean-eval-bot",
                            "email": "lean-eval-bot@users.noreply.github.com",
                            "date": commit["committed_at"],
                        }
                        return {
                            "sha": commit["commit"],
                            "parents": [{"sha": commit["parent"]}],
                            "commit": {"author": identity, "committer": identity},
                            "files": [
                                {"filename": commit["path"], "sha": commit["blob_sha"]}
                            ],
                        }, 200
                    if path == (
                        f"/repos/{commit['repository']}/contents/{commit['path']}"
                        f"?ref={commit['commit']}"
                    ):
                        return {
                            "sha": commit["blob_sha"],
                            "encoding": "base64",
                            "content": base64.b64encode(result_raw).decode(),
                            "size": len(result_raw),
                        }, 200
                    raise AssertionError(path)

                def get_public_gist(self, gist_id, commit):
                    owner, expected_id = request["source"]["repository"].split("/")
                    case.assertEqual(gist_id, expected_id)
                    case.assertEqual(commit, request["source"]["commit"])
                    return {
                        "id": expected_id,
                        "owner": {"login": owner},
                        "public": True,
                        "history": [{"version": commit}],
                    }, 200

            workflow = {
                "contract": "benchmark_repository_head",
                "repository_commit": entry["workflow_run"]["head_sha"],
                "definition_sha256": entry["workflow_run"]["definition_sha256"],
                "reviewed": True,
            }
            with mock.patch(
                "resolve_public_replay_github_evidence._workflow_binding",
                return_value=workflow,
            ):
                candidate = _legacy_candidate(
                    LegacyGistClient(), request, repository, workflow_registry, entry
                )
            self.assertEqual(candidate["status"], "matched_source_available")
            result_record = fixture["result_document"]["solved"][
                request["declared_model"]
            ]["two_plus_two"]
            self.assertNotIn("submission_kind", result_record)
            conflicting = copy.deepcopy(fixture["result_document"])
            conflicting["solved"][request["declared_model"]]["two_plus_two"][
                "submission_kind"
            ] = "github_repo"
            with self.assertRaisesRegex(EvidenceError, "source kind does not match"):
                _validate_historical_results(
                    conflicting, request, request["declared_model"]
                )

            requests = {
                "schema_version": 1,
                "kind": "historical_public_replay_resolution_requests",
                "source_repository": "leanprover/lean-eval-submissions",
                "source_commit": "a" * 40,
                "inventory_sha256": "b" * 64,
                "request_count": 1,
                "result_count": 1,
                "requests": [request],
            }
            evidence = {
                "schema_version": 1,
                "kind": "historical_public_replay_github_evidence",
                "source_repository": requests["source_repository"],
                "source_commit": requests["source_commit"],
                "inventory_sha256": requests["inventory_sha256"],
                "resolution_requests_sha256": hashlib.sha256(
                    canonical_document_bytes(requests)
                ).hexdigest(),
                "workflow_definition_registry_sha256": workflow_digest,
                "legacy_adjudication_registry_sha256": adjudication_digest,
                "request_count": 1,
                "result_count": 1,
                "shard_index": 0,
                "shard_count": 1,
                "shard_request_count": 1,
                "shard_result_count": 1,
                "resolved_count": 1,
                "source_unavailable_count": 0,
                "source_indeterminate_count": 0,
                "probe_indeterminate_count": 0,
                "timing_indeterminate_count": 0,
                "workflow_contract_unreviewed_count": 0,
                "pending_count": 0,
                "resolutions": [
                    {
                        "request_id": request["request_id"],
                        "status": "resolved",
                        "selected_issue_repository": repository,
                        "candidates": [
                            candidate,
                            {
                                "issue_repository": "leanprover/lean-eval-submissions",
                                "status": "issue_not_found",
                            },
                        ],
                    }
                ],
            }
            _validate_evidence(
                evidence,
                requests,
                workflow_registry,
                workflow_digest,
                adjudication_value,
                adjudication_digest,
            )
            probe_indeterminate = copy.deepcopy(evidence)
            probe_indeterminate["resolved_count"] = 0
            probe_indeterminate["probe_indeterminate_count"] = 1
            probe_indeterminate["pending_count"] = 1
            resolution = probe_indeterminate["resolutions"][0]
            resolution["status"] = "probe_indeterminate"
            resolution["selected_issue_repository"] = repository
            resolution["candidates"][0] = {
                "issue_repository": repository,
                "status": "probe_indeterminate",
                "reason_code": "github_request_failed",
            }
            _validate_evidence(
                probe_indeterminate,
                requests,
                workflow_registry,
                workflow_digest,
                adjudication_value,
                adjudication_digest,
            )
            downgraded = copy.deepcopy(evidence)
            downgraded_candidate = downgraded["resolutions"][0]["candidates"][0]
            for field in (
                "legacy_adjudication_sha256",
                "legacy_reason_code",
                "workflow_run_triggering_actor",
                "record_job_id",
                "record_job_started_at",
                "record_job_completed_at",
                "result_commit_sha",
                "result_blob_sha",
                "model_rename_sha256",
            ):
                downgraded_candidate.pop(field)
            with self.assertRaisesRegex(EvidenceError, "mode is invalid"):
                _validate_evidence(
                    downgraded,
                    requests,
                    workflow_registry,
                    workflow_digest,
                    adjudication_value,
                    adjudication_digest,
                )
            stripped = copy.deepcopy(evidence)
            stripped.pop("legacy_adjudication_registry_sha256")
            with self.assertRaisesRegex(EvidenceError, "mode does not match"):
                _validate_evidence(
                    stripped,
                    requests,
                    workflow_registry,
                    workflow_digest,
                    adjudication_value,
                    adjudication_digest,
                )
            hostile = {
                "legacy_adjudication_sha256": "f" * 64,
                "legacy_reason_code": "historical_issue_body_edit",
                "issue_url": f"https://github.com/{repository}/issues/99999",
                "issue_author": "attacker",
                "issue_created_at": "2026-05-01T03:53:00Z",
                "issue_closed_at": "2026-05-01T03:56:00Z",
                "issue_title_sha256": "f" * 64,
                "issue_body_sha256": "f" * 64,
                "issue_identity_sha256": "f" * 64,
                "issue_source_ref_sha256": "f" * 64,
                "issue_source_reference_binding": "exact_commit",
                "workflow_run_id": entry["workflow_run"]["id"] + 1,
                "workflow_run_url": f"https://github.com/{repository}/actions/runs/1",
                "workflow_run_created_at": "2026-05-01T03:54:00Z",
                "workflow_run_updated_at": "2026-05-01T03:56:00Z",
                "workflow_run_attempt": entry["workflow_run"]["attempt"] + 1,
                "workflow_run_actor": "attacker",
                "workflow_run_triggering_actor": "attacker",
                "workflow_run_display_title_sha256": "f" * 64,
                "workflow_run_identity_sha256": "f" * 64,
                "workflow_contract": "split_repository_recorded_benchmark_v1",
                "workflow_repository_commit": "f" * 40,
                "record_job_id": entry["record_job"]["id"] + 1,
                "record_job_started_at": "2026-05-01T03:54:54Z",
                "record_job_completed_at": "2026-05-01T03:55:01Z",
                "result_commit_sha": "f" * 40,
                "result_blob_sha": "f" * 40,
                "result_comment_url": f"{issue_url}#issuecomment-1",
                "result_comment_created_at": "2026-05-01T03:56:00Z",
                "result_comment_author": "attacker",
                "result_comment_body_sha256": "f" * 64,
                "workflow_definition_sha256": "f" * 64,
                "model_rename_sha256": "f" * 64,
                "reported_pass_problem_ids": [],
                "source_commit_url": "https://gist.github.com/kim-em/abc/f" + "f" * 39,
            }
            for field, replacement in hostile.items():
                changed = copy.deepcopy(evidence)
                changed["resolutions"][0]["candidates"][0][field] = replacement
                with self.subTest(request=request["request_id"], field=field):
                    with self.assertRaisesRegex(EvidenceError, "registry-bound"):
                        _validate_evidence(
                            changed,
                            requests,
                            workflow_registry,
                            workflow_digest,
                            adjudication_value,
                            adjudication_digest,
                        )
if __name__ == "__main__":
    unittest.main()
