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
            "RELEASE_OPT_OUT_API_ENABLED",
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
            "release_opt_out_api_enabled",
        ):
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', self.text)
        self.assertIn('body["model_identity_consolidation_api_enabled"] is False', self.text)

    def test_fixture_is_bounded_and_freezes_only_preexisting_inputs(self) -> None:
        fixture = self.fixture
        self.assertEqual(fixture["schema_version"], 1)
        self.assertEqual(fixture["environment"], "staging")
        source = fixture["source"]
        self.assertEqual(source["repository"], "kim-em/lean-eval-intake-fixture")
        self.assertRegex(source["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(source["owner_login"], "kim-em")
        for case in ("browser_submission", "headless_submission"):
            submission = fixture[case]
            self.assertEqual(submission["problem_group"], "formalization-evaluation")
            self.assertEqual(submission["problem_id"], "two_plus_two")
            self.assertEqual(submission["statement_revision"], 1)
            self.assertEqual(submission["source_visibility"], "private")
            self.assertEqual(submission["publication_choice"], "scheduled")
        mismatch = fixture["headless_source_mismatch"]
        self.assertEqual(mismatch["challenge_source_commit"], source["commit"])
        self.assertNotEqual(mismatch["submitted_source_commit"], source["commit"])
        self.assertEqual(mismatch["expected_http_status"], 401)
        self.assertEqual(
            set(fixture["lifecycle_cases"]),
            {
                "metadata_backfill",
                "problem_repair",
                "release_opt_out",
                "model_alias_and_rename",
            },
        )
        backfill = fixture["lifecycle_cases"]["metadata_backfill"]
        self.assertEqual(backfill["target"], "claimed_legacy_result")
        self.assertRegex(backfill["claim"]["result_id"], r"^r2_[0-9a-f]{64}$")
        self.assertEqual(
            backfill["claim"]["results_commit"], "runtime_staging_results_commit"
        )
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
