from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/staging-amendment-canary.yml").read_text()
APP = (ROOT / "server/src/app.ts").read_text()
CONTRACT = (ROOT / "server/src/staging-amendment-canary.ts").read_text()
DOCUMENTATION = (ROOT / "docs/staging-amendment-canary.md").read_text()
WRANGLER = json.loads((ROOT / "server/wrangler.jsonc").read_text())


class StagingAmendmentCanaryWorkflowTests(unittest.TestCase):
    def test_is_manual_protected_main_and_staging_environment_only(self) -> None:
        self.assertIn("  workflow_dispatch:", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', WORKFLOW)
        self.assertIn('test "$GITHUB_ACTOR" = "kim-em"', WORKFLOW)
        self.assertIn('test "$GITHUB_SHA" = "$EXPECTED_COMMIT"', WORKFLOW)
        self.assertIn("environment: cloudflare-staging", WORKFLOW)
        self.assertNotIn("cloudflare-production", WORKFLOW)
        self.assertNotIn("lean-eval-submission-server.lean-eval", WORKFLOW)

    def test_uses_only_the_dedicated_secret_and_no_external_action(self) -> None:
        self.assertEqual(
            re.findall(r"secrets\.([A-Z0-9_]+)", WORKFLOW),
            ["STAGING_AMENDMENT_CANARY_TOKEN"],
        )
        self.assertNotIn("READINESS_TOKEN", WORKFLOW)
        self.assertNotIn("uses:", WORKFLOW)
        self.assertIn("permissions: {}", WORKFLOW)
        self.assertNotIn("GITHUB_TOKEN", WORKFLOW)
        self.assertNotIn("aws ", WORKFLOW.lower())
        self.assertNotIn("wrangler", WORKFLOW.lower())
        self.assertIn(
            "STAGING_AMENDMENT_CANARY_TOKEN",
            WRANGLER["env"]["staging"]["secrets"]["required"],
        )
        self.assertNotIn(
            "STAGING_AMENDMENT_CANARY_TOKEN",
            WRANGLER["env"]["production"]["secrets"]["required"],
        )

    def test_binds_exact_operator_intent_and_closed_identities(self) -> None:
        self.assertIn("APPLY_AND_REJECT_STAGING_FIXTURES", WORKFLOW)
        for event_id in (
            "01a035b4-d6ce-7213-8dc6-6e140474e02e",
            "01a035b4-d6cf-718a-b5af-c903c1b66336",
            "01a035b4-d6d0-786d-bd03-5018f6ea4de6",
            "01a035b4-d6d1-7f6f-b93f-29306171a7cf",
        ):
            self.assertIn(event_id, WORKFLOW)
            self.assertIn(event_id, CONTRACT)
        self.assertNotIn("apply_request_event_id:", WORKFLOW)
        for identity in (
            "r2_99df81809318fd2673d82da042b451f77b55606c6b506beb4526828ee1e7079e",
            "r2_3f28ce10fd9bad352dc29394254ec7c414b57269757c3488cd108bd544186423",
            "eri1_362e69696a5c468d0482086b6eb3f24d68dea6b4795284a017096b092a800775",
            "eri1_b1f3167cd78dcdcef990d5b09ae447bdf3e470f60236c6a2be2009a260a6127a",
        ):
            self.assertIn(identity, WORKFLOW)
            self.assertIn(identity, CONTRACT)
        self.assertIn("972178d59e2b3c5300baa728a1356f0d49dafb87", CONTRACT)
        self.assertIn("list_append_singleton_length", CONTRACT)
        self.assertIn("STAGING_CANARY_TARGETS", APP)
        self.assertIn("It has not been dispatched", DOCUMENTATION)
        self.assertIn("Do not mark the tracker gate complete", DOCUMENTATION)

    def test_proves_mutation_gates_off_before_and_after(self) -> None:
        self.assertGreaterEqual(WORKFLOW.count('body["intake_enabled"] is False'), 2)
        self.assertGreaterEqual(
            WORKFLOW.count('body["legacy_result_owner_api_enabled"] is False'), 2
        )
        self.assertGreaterEqual(
            WORKFLOW.count('body["intake_enablement_mode"] == "disabled"'), 2
        )
        self.assertGreaterEqual(
            WORKFLOW.count('body["result_amendment_owner_api_enabled"] is False'), 2
        )
        self.assertGreaterEqual(
            WORKFLOW.count('body["result_amendment_maintainer_api_enabled"] is False'), 2
        )
        self.assertIn('env.DEPLOYMENT_ENVIRONMENT === "staging"', APP)
        self.assertIn('env.STATE_REPOSITORY === STAGING_CANARY_STATE_REPOSITORY', APP)
        self.assertIn('env.LEGACY_RESULT_OWNER_API_ENABLED === "false"', APP)
        self.assertIn('env.INTAKE_ENABLEMENT_MODE === "disabled"', APP)
        self.assertIn('env.RESULT_AMENDMENT_MAINTAINERS === "[]"', APP)


if __name__ == "__main__":
    unittest.main()
