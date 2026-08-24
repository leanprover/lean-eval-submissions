from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/staging-amendment-canary.yml").read_text()
APP = (ROOT / "server/src/app.ts").read_text()
CONTRACT = (ROOT / "server/src/staging-amendment-canary.ts").read_text()
DOCUMENTATION = (ROOT / "docs/staging-amendment-canary.md").read_text()


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

    def test_uses_only_the_readiness_secret_and_no_external_action(self) -> None:
        self.assertEqual(re.findall(r"secrets\.([A-Z0-9_]+)", WORKFLOW), ["READINESS_TOKEN"])
        self.assertNotIn("uses:", WORKFLOW)
        self.assertIn("permissions: {}", WORKFLOW)
        self.assertNotIn("GITHUB_TOKEN", WORKFLOW)
        self.assertNotIn("aws ", WORKFLOW.lower())
        self.assertNotIn("wrangler", WORKFLOW.lower())

    def test_binds_exact_operator_intent_and_closed_identities(self) -> None:
        self.assertIn("APPLY_AND_REJECT_STAGING_FIXTURES", WORKFLOW)
        for name in (
            "apply_request_event_id",
            "apply_decision_event_id",
            "reject_request_event_id",
            "reject_decision_event_id",
        ):
            self.assertIn(name, WORKFLOW)
        self.assertIn("972178d59e2b3c5300baa728a1356f0d49dafb87", CONTRACT)
        self.assertIn("list_append_singleton_length", CONTRACT)
        self.assertIn("STAGING_CANARY_TARGETS", APP)
        self.assertIn("It has not been dispatched", DOCUMENTATION)
        self.assertIn("Do not mark the tracker gate complete", DOCUMENTATION)

    def test_proves_mutation_gates_off_before_and_after(self) -> None:
        self.assertGreaterEqual(WORKFLOW.count('body["intake_enabled"] is False'), 2)
        self.assertGreaterEqual(
            WORKFLOW.count('body["result_amendment_owner_api_enabled"] is False'), 2
        )
        self.assertGreaterEqual(
            WORKFLOW.count('body["result_amendment_maintainer_api_enabled"] is False'), 2
        )
        self.assertIn('env.DEPLOYMENT_ENVIRONMENT === "staging"', APP)
        self.assertIn('env.STATE_REPOSITORY === STAGING_CANARY_STATE_REPOSITORY', APP)
        self.assertIn('env.RESULT_AMENDMENT_MAINTAINERS === "[]"', APP)


if __name__ == "__main__":
    unittest.main()
