from __future__ import annotations

import json
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "set-staging-lifecycle-smoke.yml"
FIXTURE = ROOT / "configuration" / "staging-lifecycle-smoke-v1.json"


class StagingLifecycleSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_controller_is_manual_exact_tag_bound_and_staging_only(self) -> None:
        self.assertIn("workflow_dispatch", self.text)
        jobs = self.text.split("\njobs:\n", 1)[1]
        self.assertEqual(
            re.findall(r"^  ([A-Za-z0-9_-]+):$", jobs, re.MULTILINE),
            ["set-staging-lifecycle-smoke"],
        )
        self.assertIn("    environment: cloudflare-staging", jobs)
        self.assertNotIn("cloudflare-production", self.text)
        self.assertIn('"$EXPECTED_COMMIT" != "$GITHUB_SHA"', self.text)
        self.assertIn(
            '"$GITHUB_REF" != "refs/tags/lean-eval-dispatch/$EXPECTED_COMMIT"',
            self.text,
        )
        self.assertIn("confirm_staging_lifecycle_smoke", self.text)
        self.assertIn("selected dispatch tag is not the exact live staging deployment", self.text)

    def test_controller_enables_only_launch_surface_and_has_all_false_recovery(self) -> None:
        enabled_vars = {
            "INTAKE_ENABLED",
            "LEGACY_RESULT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_MAINTAINER_API_ENABLED",
            "MODEL_IDENTITY_OWNER_API_ENABLED",
            "MODEL_IDENTITY_MAINTAINER_API_ENABLED",
            "RELEASE_OPT_IN_API_ENABLED",
        }
        for variable in enabled_vars:
            with self.subTest(variable=variable):
                self.assertIn(f'--var "{variable}:$enabled"', self.text)
                self.assertIn(f'--var "{variable}:false"', self.text)
        self.assertGreaterEqual(
            self.text.count('--var "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED:false"'),
            2,
        )
        self.assertIn("Restore all launch gates to disabled after a failed mutation", self.text)
        self.assertIn("if: failure() && steps.armed.outputs.ready == 'true'", self.text)
        self.assertIn("failed staging lifecycle mutation could not verify all-false recovery", self.text)
        self.assertNotIn("MODEL_IDENTITY_CONSOLIDATION_API_ENABLED:$enabled", self.text)
        self.assertNotIn("AWS_", self.text)

    def test_controller_validates_closed_maintainers_and_effective_health(self) -> None:
        self.assertIn('expected_length = 1 if desired == "enabled" else 0', self.text)
        self.assertIn('set(identity) != {"github_id", "login"}', self.text)
        self.assertIn("9_007_199_254_740_991", self.text)
        for field in (
            "intake_configured_enabled",
            "intake_effective_enabled",
            "legacy_result_owner_api_enabled",
            "result_amendment_owner_api_enabled",
            "result_amendment_maintainer_api_enabled",
            "model_identity_owner_api_enabled",
            "model_identity_maintainer_api_enabled",
            "release_opt_in_api_enabled",
        ):
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', self.text)
        self.assertIn('body["model_identity_consolidation_api_enabled"] is False', self.text)
        self.assertIn('body["release_opt_out_api_enabled"] is False', self.text)

    def test_controller_runs_the_exact_unauthenticated_browser_case(self) -> None:
        case = self.fixture["browser_unauthenticated_request"]
        self.assertEqual(
            case,
            {
                "method": "POST",
                "path": "/api/v1/browser/submission-grants",
                "authentication": "none",
                "expected_http_status": 401,
                "expected_error": "authentication_failed",
            },
        )
        self.assertIn("Verify unauthenticated browser mutation is denied", self.text)
        self.assertIn("if: inputs.state == 'enabled'", self.text)
        self.assertIn(
            "https://lean-eval-submission-server-staging.lean-eval.workers.dev"
            "/api/v1/browser/submission-grants",
            self.text,
        )
        self.assertIn('case = fixture["browser_unauthenticated_request"]', self.text)
        self.assertIn('body == {"error": case["expected_error"]}', self.text)
        self.assertIn(
            '--user-agent "lean-eval-staging-denial-probe/${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"',
            self.text,
        )
        denial_step = self.text.split(
            "      - name: Verify unauthenticated browser mutation is denied\n", 1
        )[1].split("\n      - name:", 1)[0]
        self.assertNotIn("authorization", denial_step.lower())
        self.assertNotIn("cookie", denial_step.lower())

    def test_fixture_is_bounded_and_freezes_only_preexisting_inputs(self) -> None:
        fixture = self.fixture
        self.assertEqual(fixture["schema_version"], 1)
        self.assertEqual(fixture["environment"], "staging")
        source = fixture["source"]
        self.assertEqual(source["repository"], "leanprover/lean-eval-state-staging")
        self.assertEqual(source["fixture_branch"], "staging-source-fixture-v1")
        self.assertEqual(
            source["commit"], "34357f2f94f39e293b1d2b127b7f298654d39cf7"
        )
        self.assertEqual(source["owner_login"], "kim-em")
        for case in ("browser_submission", "headless_submission"):
            submission = fixture[case]
            self.assertEqual(submission["problem_group"], "formalization-evaluation")
            self.assertEqual(submission["problem_id"], "two_plus_two")
            self.assertEqual(submission["statement_revision"], 1)
            self.assertEqual(submission["source_visibility"], "private")
        self.assertEqual(
            fixture["browser_submission"]["publication_choice"], "withheld"
        )
        self.assertEqual(
            fixture["headless_submission"]["publication_choice"], "scheduled"
        )
        identity_nonce = "4aee1224a07f4eb985e752861a117001"
        self.assertIn(
            identity_nonce, fixture["browser_submission"]["declared_model"]
        )
        self.assertIn(
            identity_nonce, fixture["headless_submission"]["declared_model"]
        )
        self.assertNotEqual(
            fixture["browser_submission"]["declared_model"],
            fixture["headless_submission"]["declared_model"],
        )
        mismatch = fixture["headless_source_mismatch"]
        self.assertEqual(mismatch["challenge_source_commit"], source["commit"])
        self.assertNotEqual(mismatch["submitted_source_commit"], source["commit"])
        self.assertEqual(mismatch["expected_http_status"], 401)
        self.assertEqual(
            set(fixture["lifecycle_cases"]),
            {
                "metadata_backfill",
                "problem_repair",
                "publication_opt_in",
                "model_alias_and_rename",
            },
        )
        publication = fixture["lifecycle_cases"]["publication_opt_in"]
        self.assertEqual(publication["target"], "browser_submission")
        self.assertEqual(
            publication["transition"],
            {"from": "withheld", "to": "scheduled"},
        )
        self.assertEqual(publication["expected_release_status"], "scheduled")
        repair_denial = fixture["lifecycle_cases"]["problem_repair"]["denial_request"]
        self.assertNotRegex(
            repair_denial["corrected_problem_id"],
            r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
        )
        self.assertEqual(
            fixture["lifecycle_cases"]["problem_repair"]["denial_http_status"],
            400,
        )
        self.assertEqual(
            fixture["lifecycle_cases"]["model_alias_and_rename"]["alias"],
            "lean-eval-launch-final-staging-alias-v2",
        )
        backfill = fixture["lifecycle_cases"]["metadata_backfill"]
        self.assertEqual(backfill["target"], "claimed_legacy_result")
        self.assertRegex(backfill["claim"]["result_id"], r"^r2_[0-9a-f]{64}$")
        self.assertEqual(
            backfill["claim"]["results_commit"], "runtime_staging_results_commit"
        )
        denial = backfill["non_owner_denial"]
        self.assertEqual(denial["target"], "stable_other_owned_result")
        self.assertEqual(denial["method"], "PATCH")
        self.assertEqual(denial["authenticated_login"], source["owner_login"])
        self.assertNotEqual(denial["authenticated_login"], denial["owner_login"])
        self.assertEqual(
            denial["path"], f'/api/v1/results/{denial["result_id"]}/metadata'
        )
        self.assertEqual(denial["expected_http_status"], 404)
        self.assertEqual(denial["expected_error"], "not_found")
        self.assertEqual(denial["expected_state_event_delta"], 0)
        self.assertTrue(denial["patch"]["production_metadata"])
        records = json.loads((ROOT / denial["results_path"]).read_text(encoding="utf-8"))
        self.assertEqual(records["schema_version"], 2)
        matching = [
            record
            for record in records["results"]
            if record["result_id"] == denial["result_id"]
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(denial["owner_login"], "eohjelle")
        self.assertEqual(
            fixture["maintainer_profiles"]["success"],
            [{"github_id": 477956, "login": "kim-em"}],
        )
        self.assertEqual(fixture["maintainer_profiles"]["denial_http_status"], 404)
        boundary = fixture["credentialed_release_boundary"]
        self.assertRegex(boundary["submission_id"], r"^[0-9a-f-]{36}$")
        self.assertRegex(boundary["result_id"], r"^r2_[0-9a-f]{64}$")
        self.assertRegex(boundary["archive_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(boundary["archive_ciphertext_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            set(fixture["runtime_allocated"]),
            {
                "browser_submission_id",
                "browser_result_id",
                "headless_challenge",
                "headless_gist_id",
                "headless_submission_id",
                "headless_tag",
                "headless_result_id",
                "idempotency_event_ids",
                "model_identity_ids",
                "staging_results_commit",
            },
        )


if __name__ == "__main__":
    unittest.main()
