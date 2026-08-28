from __future__ import annotations

import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts" / "run_bounded_staging_lifecycle.py"
WATCHDOG = ROOT / ".github" / "workflows" / "bounded-staging-lifecycle-watchdog.yml"
FIXTURE = ROOT / "configuration" / "staging-lifecycle-smoke-v1.json"
RUNBOOK = ROOT / "docs" / "overhaul-rollout-runbook.md"


def load_driver():
    spec = importlib.util.spec_from_file_location("bounded_staging_driver", DRIVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BoundedStagingLifecycleAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.driver_text = DRIVER.read_text(encoding="utf-8")
        cls.watchdog = WATCHDOG.read_text(encoding="utf-8")
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.runbook = RUNBOOK.read_text(encoding="utf-8")
        cls.driver = load_driver()

    def mutation(self):
        bounded = self.fixture["bounded_acceptance"]
        source = self.fixture["source"]
        return self.driver.FixtureMutation(
            gist_id="a" * 20,
            filename=bounded["fixture_gist_file"],
            repository=source["repository"],
            commit=source["commit"],
            tag="lean-eval/019debcf-cb48-7000-8000-000000000001",
            prior_file_present=False,
            prior_file_content=None,
        )

    def test_watchdog_arms_from_all_false_before_enable_and_has_cleanup_margin(self) -> None:
        self.assertIn("environment: cloudflare-staging", self.watchdog)
        self.assertNotIn("cloudflare-production", self.watchdog)
        self.assertIn("timeout-minutes: 125", self.watchdog)
        initial = self.watchdog.index("Verify exact tag and initial all-false")
        install = self.watchdog.index("npm ci")
        armed = self.watchdog.index("Arm all-false recovery")
        enabled = self.watchdog.index("Wait for the separately approved enabled state")
        hold = self.watchdog.index("Hold the bounded acceptance window")
        self.assertLess(initial, install)
        self.assertLess(install, armed)
        self.assertLess(armed, enabled)
        self.assertLess(enabled, hold)
        self.assertIn("if: always() && steps.armed.outputs.ready == 'true'", self.watchdog)
        self.assertIn("activation_timeout_minutes must be an integer from 5 through 15", self.watchdog)
        self.assertIn("window_minutes must be an integer from 15 through 90", self.watchdog)
        for variable in (
            "INTAKE_ENABLED", "LEGACY_RESULT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_MAINTAINER_API_ENABLED",
            "MODEL_IDENTITY_OWNER_API_ENABLED",
            "MODEL_IDENTITY_MAINTAINER_API_ENABLED",
            "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED",
            "RELEASE_OPT_OUT_API_ENABLED",
        ):
            self.assertIn(f'--var "{variable}:false"', self.watchdog)

    def test_fixture_is_canonical_closed_and_hash_pinned(self) -> None:
        bounded = self.fixture["bounded_acceptance"]
        self.assertEqual(
            set(bounded),
            {
                "submission_base_url", "state_repository", "state_branch",
                "state_contract_commit", "state_script_sha256",
                "results_repository", "results_branch", "release_repository",
                "release_workflow", "release_ref", "release_commit",
                "release_run_name_prefix", "fixture_gist_file", "retire_after",
            },
        )
        self.assertEqual(bounded["release_commit"], "c27928a56fb3fb6ec0506ac4de78fa6e732ccc02")
        self.assertEqual(
            bounded["release_ref"],
            "lean-eval-staging-smoke/c27928a56fb3fb6ec0506ac4de78fa6e732ccc02",
        )
        self.assertEqual(bounded["state_contract_commit"], "23852beaeb059c88caf043d22dad19b211c377b2")
        self.assertEqual(len(bounded["state_script_sha256"]), 10)
        self.assertEqual(
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            self.driver.EXPECTED_FIXTURE_SHA256,
        )
        self.assertEqual(
            FIXTURE.read_bytes(),
            (json.dumps(self.fixture, indent=2, sort_keys=False) + "\n").encode(),
        )

    def test_cli_accepts_only_visible_browser_submission_identity(self) -> None:
        help_text = self.driver.parser().format_help()
        self.assertNotIn("--fixture", help_text)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.driver.parser().parse_args(
                [
                    "run", "--expected-commit", "b" * 40, "--gist-id", "a" * 20,
                    "--browser-submission-id", "019debcf-cb48-7000-8000-000000000001",
                    "--browser-result-id", "x",
                ]
            )
        self.assertNotIn("args.browser_result_id", self.driver_text)
        self.assertNotIn("confirm-external-mutations", self.driver_text)
        self.assertIn("browser, browser_result = wait_submission", self.driver_text)

    def test_target_bound_pause_never_prints_challenge(self) -> None:
        mutation = self.mutation()
        output = io.StringIO()
        phrase = (
            f"APPROVE GIST {mutation.gist_id}/{mutation.filename} AND TAG "
            f"{mutation.repository}/refs/tags/{mutation.tag}@{mutation.commit} WITH EXACT CLEANUP"
        )
        with mock.patch("builtins.input", return_value=phrase), contextlib.redirect_stdout(output):
            self.driver.require_target_bound_approval(mutation)
        self.assertIn(mutation.describe_targets(), output.getvalue())
        self.assertNotIn("signed-secret", output.getvalue())
        with mock.patch("builtins.input", return_value="NO"), contextlib.redirect_stdout(
            io.StringIO()
        ), self.assertRaisesRegex(self.driver.AcceptanceError, "approval"):
            self.driver.require_target_bound_approval(mutation)

    def test_external_404_must_be_exact_and_authenticated(self) -> None:
        def completed(returncode, stdout="", stderr=""):
            return subprocess.CompletedProcess([], returncode, stdout, stderr)

        with mock.patch.object(
            self.driver.subprocess, "run",
            return_value=completed(1, stderr="gh: Not Found (HTTP 404)\n"),
        ):
            self.assertIsNone(self.driver.gh_json_or_authenticated_404(["target"]))
        for diagnostic in ("HTTP 404", "gh: Forbidden (HTTP 403)", "network down"):
            with mock.patch.object(
                self.driver.subprocess, "run",
                return_value=completed(1, stderr=diagnostic),
            ), self.assertRaises(self.driver.AcceptanceError):
                self.driver.gh_json_or_authenticated_404(["target"])

    def test_unexpected_api_status_never_echoes_secret_response_payload(self) -> None:
        secret = "signed-session-token-must-not-leak"
        response = io.BytesIO(
            json.dumps({"session_token": secret, "challenge": "also-secret"}).encode()
        )
        response.status = 200
        with (
            mock.patch.object(self.driver.urllib.request, "urlopen", return_value=response),
            self.assertRaises(self.driver.AcceptanceError) as raised,
        ):
            self.driver.Api("https://submit.test").request(
                "POST", "/api/v1/agent/submissions", {}, expected=202
            )
        diagnostic = str(raised.exception)
        self.assertNotIn(secret, diagnostic)
        self.assertNotIn("also-secret", diagnostic)
        self.assertIn("response redacted", diagnostic)

    def test_exact_tag_response_and_idempotent_owned_cleanup(self) -> None:
        mutation = self.mutation()
        response = {
            "ref": f"refs/tags/{mutation.tag}", "node_id": "node",
            "url": "https://api.github.test/ref",
            "object": {"sha": mutation.commit, "type": "commit", "url": "https://api.github.test/commit"},
        }
        self.driver.verify_exact_tag_response(response, mutation.repository, mutation.tag, mutation.commit)
        with self.assertRaises(self.driver.AcceptanceError):
            self.driver.verify_exact_tag_response(
                {**response, "unexpected": True}, mutation.repository, mutation.tag, mutation.commit
            )
        with mock.patch.object(self.driver, "restore_gist") as restore, mock.patch.object(
            self.driver, "remove_created_tag"
        ) as remove:
            self.driver.cleanup_fixture_mutation(mutation, remove_tag=False)
            restore.assert_called_once_with(mutation)
            remove.assert_not_called()
        with mock.patch.object(self.driver, "gh_json") as github:
            self.driver.restore_gist(mutation)
            self.driver.remove_created_tag(mutation)
        github.assert_not_called()

    def test_partial_patch_response_loss_restores_only_captured_gist_state(self) -> None:
        mutation = self.mutation()
        challenge = "signed-secret"
        initial = {
            "id": mutation.gist_id, "public": False,
            "owner": {"login": "kim-em"}, "files": {},
        }
        changed = {
            **initial,
            "files": {mutation.filename: {"truncated": False, "content": challenge}},
        }
        calls = 0

        def github(args, *, method="GET", fields=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                return initial
            if calls == 2:
                raise self.driver.AcceptanceError("lost PATCH response")
            if calls == 3:
                return changed
            if calls == 4:
                return changed
            if calls == 5:
                self.assertEqual(method, "PATCH")
                self.assertEqual(fields, {"files": {mutation.filename: None}})
                return initial
            self.fail((args, method, fields))

        with (
            mock.patch.object(self.driver, "gh_json", side_effect=github),
            mock.patch.object(
                self.driver, "gh_json_or_authenticated_404", return_value=None
            ),
            mock.patch.object(self.driver, "require_target_bound_approval"),
            self.assertRaisesRegex(self.driver.AcceptanceError, "lost PATCH"),
        ):
            self.driver.apply_exact_proof_and_tag(
                self.fixture, mutation.gist_id, challenge, mutation.tag
            )
        self.assertEqual(calls, 5)

    def test_cleanup_attempts_exact_tag_removal_even_if_gist_restore_fails(self) -> None:
        mutation = self.mutation()
        mutation.gist_changed = True
        mutation.tag_created = True
        with (
            mock.patch.object(
                self.driver, "restore_gist",
                side_effect=self.driver.AcceptanceError("restore failed"),
            ),
            mock.patch.object(self.driver, "remove_created_tag") as remove,
            self.assertRaisesRegex(self.driver.AcceptanceError, "restore failed"),
        ):
            self.driver.cleanup_fixture_mutation(mutation, remove_tag=True)
        remove.assert_called_once_with(mutation)

    def test_partial_tag_response_loss_restores_gist_but_refuses_unproved_deletion(self) -> None:
        mutation = self.mutation()
        challenge = "signed-secret"
        initial = {
            "id": mutation.gist_id, "public": False,
            "owner": {"login": "kim-em"}, "files": {},
        }
        changed = {
            **initial,
            "files": {mutation.filename: {"truncated": False, "content": challenge}},
        }
        tag_response = {
            "ref": f"refs/tags/{mutation.tag}", "node_id": "node",
            "url": "https://api.github.test/ref",
            "object": {"sha": mutation.commit, "type": "commit", "url": "https://api.github.test/commit"},
        }
        api_calls = 0

        def github(args, *, method="GET", fields=None):
            nonlocal api_calls
            api_calls += 1
            if api_calls == 1:
                return initial
            if api_calls == 2:
                return changed
            if api_calls == 3:
                raise self.driver.AcceptanceError("lost POST response")
            if api_calls == 4:
                return changed
            if api_calls == 5:
                return changed
            if api_calls == 6:
                self.assertEqual(method, "PATCH")
                return initial
            self.fail((args, method, fields))

        tag_reads = iter((None, tag_response))
        diagnostics = io.StringIO()
        with (
            mock.patch.object(self.driver, "gh_json", side_effect=github),
            mock.patch.object(
                self.driver, "gh_json_or_authenticated_404",
                side_effect=lambda _args: next(tag_reads),
            ),
            mock.patch.object(self.driver, "require_target_bound_approval"),
            contextlib.redirect_stderr(diagnostics),
            self.assertRaisesRegex(self.driver.AcceptanceError, "lost POST"),
        ):
            self.driver.apply_exact_proof_and_tag(
                self.fixture, mutation.gist_id, challenge, mutation.tag
            )
        self.assertEqual(api_calls, 6)
        self.assertIn("refusing unproved deletion", diagnostics.getvalue())
        self.assertIn(mutation.describe_targets(), diagnostics.getvalue())

    def test_gist_restore_refuses_intervening_edit_or_deletion(self) -> None:
        mutation = self.mutation()
        mutation.gist_changed = True
        mutation.written_file_content = "signed-secret"
        mutation.written_file_sha256 = hashlib.sha256(b"signed-secret").hexdigest()
        intervening = {
            "id": mutation.gist_id, "public": False,
            "owner": {"login": "kim-em"},
            "files": {
                mutation.filename: {"truncated": False, "content": "operator edit"}
            },
        }
        with (
            mock.patch.object(self.driver, "gh_json", return_value=intervening) as github,
            self.assertRaisesRegex(self.driver.AcceptanceError, "refusing overwrite"),
        ):
            self.driver.restore_gist(mutation)
        github.assert_called_once_with([f"gists/{mutation.gist_id}"])

    def test_lost_restore_response_reconciles_only_exact_prior_state(self) -> None:
        mutation = self.mutation()
        mutation.gist_changed = True
        mutation.written_file_content = "signed-secret"
        mutation.written_file_sha256 = hashlib.sha256(b"signed-secret").hexdigest()
        written = {
            "id": mutation.gist_id, "public": False,
            "owner": {"login": "kim-em"},
            "files": {
                mutation.filename: {"truncated": False, "content": "signed-secret"}
            },
        }
        prior = {
            "id": mutation.gist_id, "public": False,
            "owner": {"login": "kim-em"}, "files": {},
        }
        with (
            mock.patch.object(
                self.driver, "gh_json",
                side_effect=(written, self.driver.AcceptanceError("lost response")),
            ),
            self.assertRaisesRegex(self.driver.AcceptanceError, "lost response"),
        ):
            self.driver.restore_gist(mutation)
        self.assertTrue(mutation.gist_restore_started)
        with mock.patch.object(self.driver, "gh_json", return_value=prior) as github:
            self.driver.restore_gist(mutation)
        github.assert_called_once_with([f"gists/{mutation.gist_id}"])
        self.assertFalse(mutation.gist_changed)
        self.assertIsNone(mutation.written_file_content)

    def test_unknown_headless_post_outcome_never_permits_cleanup(self) -> None:
        self.assertTrue(
            self.driver.fixture_cleanup_is_proved_safe(
                headless_post_started=False, headless_terminal=False
            )
        )
        self.assertFalse(
            self.driver.fixture_cleanup_is_proved_safe(
                headless_post_started=True, headless_terminal=False
            )
        )
        self.assertTrue(
            self.driver.fixture_cleanup_is_proved_safe(
                headless_post_started=True, headless_terminal=True
            )
        )

    def test_terminal_status_derives_result_and_rejects_wrong_dispatch(self) -> None:
        submission_id = self.driver.event_id()
        expected = {
            **self.fixture["headless_submission"],
            "source_repository": self.fixture["source"]["repository"],
            "source_commit": self.fixture["source"]["commit"],
        }
        result_id = "r2_" + "d" * 64
        body = {
            "submission_id": submission_id,
            "owner": "kim-em",
            "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "submission": expected,
            "production_metadata": expected["production_metadata"],
            "publication_choice": expected["publication_choice"],
            "archive": {"status": "completed"},
            "evaluation": {"status": "accepted"},
            "result_id": result_id,
            "dispatch": {
                "status": "succeeded",
                "workflow_ref": "lean-eval-dispatch/" + "b" * 40,
            },
        }
        self.assertEqual(
            self.driver.validate_new_submission(
                body, submission_id, expected, "b" * 40
            ),
            result_id,
        )
        body["dispatch"]["workflow_ref"] = "lean-eval-dispatch/" + "c" * 40
        with self.assertRaisesRegex(self.driver.AcceptanceError, "exact candidate"):
            self.driver.validate_new_submission(
                body, submission_id, expected, "b" * 40
            )

    def test_preflight_is_read_only_and_checks_candidate_identity(self) -> None:
        bounded = self.fixture["bounded_acceptance"]

        def github_response(args, *, method="GET", fields=None):
            self.assertEqual(method, "GET")
            self.assertIsNone(fields)
            endpoint = args[0]
            if endpoint == "user":
                return {"login": "kim-em"}
            if endpoint.startswith("gists/"):
                return {"owner": {"login": "kim-em"}, "public": False}
            if endpoint.endswith("/commits/" + self.fixture["source"]["commit"]):
                return {"sha": self.fixture["source"]["commit"]}
            if "/commits/" + bounded["release_ref"] in endpoint:
                return {"sha": bounded["release_commit"]}
            if "/git/ref/tags/" + bounded["release_ref"] in endpoint:
                return {
                    "ref": "refs/tags/" + bounded["release_ref"],
                    "node_id": "node",
                    "url": "https://api.github.test/ref",
                    "object": {
                        "sha": bounded["release_commit"],
                        "type": "commit",
                        "url": "https://api.github.test/commit",
                    },
                }
            if "/branches/" in endpoint:
                return {"protected": True}
            self.fail(endpoint)

        with mock.patch.object(self.driver, "verify_candidate_checkout") as checkout, mock.patch.object(
            self.driver, "gh_json", side_effect=github_response
        ), mock.patch.object(self.driver, "health", return_value=False):
            self.driver.fixture_preflight(self.fixture, "a" * 20, "b" * 40)
        checkout.assert_called_once_with("b" * 40, self.fixture)

    def test_driver_binds_payload_status_dispatch_and_unique_release_run(self) -> None:
        for required in (
            'evaluation.get("status") == "accepted"',
            'dispatch.get("workflow_ref") != f"lean-eval-dispatch/{expected_commit}"',
            'body.get("submission") != expected_submission',
            'expected_title=bounded["release_run_name_prefix"] + headless_id',
            'item.get("display_title") == expected_title',
            'fixture_cleanup_is_proved_safe(',
            'cleanup_fixture_mutation(lease, remove_tag=True)',
            'Headless acceptance outcome may be committed',
        ):
            self.assertIn(required, self.driver_text)
        self.assertNotIn('evaluation.get("status") in {"accepted", "rejected"}', self.driver_text)

    def test_state_assertions_use_reviewed_hashes_and_closed_environment(self) -> None:
        for required in (
            '"merge-base",', '"--is-ancestor",',
            'scripts != bounded["state_script_sha256"]',
            '"PYTHONDONTWRITEBYTECODE": "1"', '"PYTHONHASHSEED": "0"',
            '"-I",', 'operator-free-home',
            'State assertion scripts modified their checkout',
        ):
            self.assertIn(required, self.driver_text)
        self.assertNotIn('"--depth=1"', self.driver_text)

    def test_disabled_route_evidence_and_retirement_are_explicit(self) -> None:
        self.assertIn("assert_disabled_routes(", self.driver_text)
        self.assertIn("denial_fixture=fixture", self.driver_text)
        self.assertIn('"/api/v1/agent/challenges"', self.driver_text)
        self.assertIn('f"/api/v1/model-identities/{model_id}/decisions"', self.driver_text)
        self.assertIn('f"/api/v1/results/{browser_result}/problem-repairs/decisions"', self.driver_text)
        self.assertIn("does not verify the public entry page", self.runbook)
        self.assertIn("ordinary process\nmemory", self.runbook)
        self.assertIn("nonsecret rollback targets", self.runbook)
        self.assertIn("must be deleted with the staging fixture after one accepted\nrun", self.runbook)
        self.assertEqual(
            self.fixture["bounded_acceptance"]["retire_after"],
            "one accepted bounded staging lifecycle run",
        )

    def test_local_uuid7_idempotency_keys_are_canonical_and_not_future_dated(self) -> None:
        import time

        before = int(time.time() * 1000)
        value = self.driver.event_id()
        after = int(time.time() * 1000)
        self.assertRegex(value, self.driver.UUID7)
        timestamp = int(value[:8] + value[9:13], 16)
        self.assertGreaterEqual(timestamp, before)
        self.assertLessEqual(timestamp, after)


if __name__ == "__main__":
    unittest.main()
