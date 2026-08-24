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
    EvidenceError,
    GitHubClient,
    ProbeIndeterminate,
    _RejectRedirects,
    _read_bounded,
    _workflow_binding,
    _write_exclusive,
    canonical_bytes,
    resolve as _resolve,
    shard_requests,
    validate_evidence as _validate_evidence,
    validate_requests,
    validate_workflow_registry,
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


def resolve(value, digest, client, workflow_registry=None, registry_digest=None, **kwargs):
    workflow_registry, computed = registry_bytes(workflow_registry)
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
        value, requests, workflow_registry, registry_digest or computed
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


class ResolvePublicReplayGitHubEvidenceTests(unittest.TestCase):
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
            client._cache_put(client._get_cache, str(index), (index, 200), 512)
        self.assertEqual(len(client._get_cache), 512)
        self.assertNotIn("0", client._get_cache)
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
        client._opener.open = mock.Mock(side_effect=[Response(), Response()])
        path = "/gists/fixture/" + "a" * 40
        self.assertEqual(client.get(path)[1], 200)
        self.assertEqual(client.get(path)[1], 200)
        self.assertEqual(client._opener.open.call_count, 2)
        self.assertNotIn(path, client._get_cache)

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
        with self.assertRaisesRegex(EvidenceError, "raw workflow registry"):
            _resolve(value, "8" * 64, FakeClient())
        registry, _ = registry_bytes()
        with self.assertRaisesRegex(EvidenceError, "raw workflow registry"):
            _resolve(value, "8" * 64, FakeClient(), registry)
        with self.assertRaisesRegex(EvidenceError, "digest is invalid"):
            _resolve(value, "8" * 64, FakeClient(), registry, 7)

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
        self.assertEqual(len(reviewed), 119)
        self.assertEqual(
            len({entry["definition_sha256"] for entry in reviewed.values()}),
            12,
        )
        self.assertEqual(
            hashlib.sha256(registry_raw).hexdigest(),
            "82eff4dce70c2fcb7f480522f4de1fb16884534ce5f9452032908bb299c12196",
        )
        schema = json.loads(
            (
                ROOT / "schemas/public-replay-workflow-definitions-v1.schema.json"
            ).read_text()
        )
        self.assertIs(schema["additionalProperties"], False)
        self.assertIs(schema["properties"]["contracts"]["items"]["additionalProperties"], False)


if __name__ == "__main__":
    unittest.main()
