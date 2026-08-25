"""Hostile structural tests for the one-shot staging workflow."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github/workflows/model-identity-staging-qualification.yml"
).read_text(encoding="utf-8")
RUNNER = (ROOT / "scripts/run_model_identity_staging_qualification.py").read_text(
    encoding="utf-8"
)
DOCUMENTATION = (ROOT / "docs/model-identity-staging-qualification.md").read_text(
    encoding="utf-8"
)
WRANGLER = json.loads((ROOT / "server/wrangler.jsonc").read_text(encoding="utf-8"))


class ModelIdentityStagingQualificationWorkflowTests(unittest.TestCase):
    def test_is_manual_exact_protected_main_and_staging_only(self) -> None:
        self.assertIn("  workflow_dispatch:", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)
        self.assertIn(
            'test "$GITHUB_REPOSITORY" = "leanprover/lean-eval-submissions"', WORKFLOW
        )
        self.assertIn('test "$GITHUB_ACTOR" = "kim-em"', WORKFLOW)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', WORKFLOW)
        self.assertIn('test "$GITHUB_SHA" = "$EXPECTED_COMMIT"', WORKFLOW)
        self.assertIn("environment: cloudflare-staging", WORKFLOW)
        self.assertNotIn("cloudflare-production", WORKFLOW)
        self.assertNotIn("lean-eval-submission-server.lean-eval", WORKFLOW)

    def test_does_not_deploy_or_mutate_any_external_configuration(self) -> None:
        lowered = WORKFLOW.lower()
        for forbidden in (
            "wrangler",
            "aws ",
            "gh api",
            "gh variable",
            "gh secret",
            "cloudflare api",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)
        self.assertNotIn("GITHUB_TOKEN", WORKFLOW)
        self.assertIn("permissions: {}", WORKFLOW)
        self.assertIn("persist-credentials: false", WORKFLOW)

    def test_uses_only_closed_ephemeral_staging_credentials(self) -> None:
        self.assertEqual(
            sorted(set(re.findall(r"secrets\.([A-Z0-9_]+)", WORKFLOW))),
            [
                "MODEL_IDENTITY_AGENT_SESSION",
                "MODEL_IDENTITY_CROSS_OWNER_SESSION",
                "MODEL_IDENTITY_OAUTH_SESSION",
                "MODEL_IDENTITY_QUALIFICATION_TOKEN",
            ],
        )
        production_secrets = WRANGLER["env"]["production"]["secrets"]["required"]
        for secret in (
            "MODEL_IDENTITY_AGENT_SESSION",
            "MODEL_IDENTITY_CROSS_OWNER_SESSION",
            "MODEL_IDENTITY_OAUTH_SESSION",
            "MODEL_IDENTITY_QUALIFICATION_TOKEN",
        ):
            self.assertNotIn(secret, production_secrets)

    def test_both_tracked_api_flags_remain_false_in_both_environments(self) -> None:
        for environment in ("staging", "production"):
            variables = WRANGLER["env"][environment]["vars"]
            self.assertEqual(variables["MODEL_IDENTITY_OWNER_API_ENABLED"], "false")
            self.assertEqual(
                variables["MODEL_IDENTITY_MAINTAINER_API_ENABLED"],
                "false",
            )
        self.assertNotIn("MODEL_IDENTITY_OWNER_API_ENABLED=true", WORKFLOW)
        self.assertNotIn("MODEL_IDENTITY_MAINTAINER_API_ENABLED=true", WORKFLOW)
        self.assertGreaterEqual(
            RUNNER.count('body["model_identity_owner_api_enabled"] is not False'),
            1,
        )
        self.assertGreaterEqual(
            RUNNER.count('body["model_identity_maintainer_api_enabled"] is not False'),
            1,
        )

    def test_restoration_and_live_only_gaps_cannot_be_mistaken_for_qualification(
        self,
    ) -> None:
        self.assertIn("finally:", RUNNER)
        self.assertIn('"operation": "restore"', RUNNER)
        self.assertIn("mandatory qualification restoration failed", RUNNER)
        self.assertIn(
            "The privileged internal harness is intentionally **not** implemented",
            DOCUMENTATION,
        )
        self.assertIn("It has\nnot been dispatched", DOCUMENTATION)
        self.assertIn(
            "Do not mark any dark gate complete from source tests alone", DOCUMENTATION
        )
        self.assertIn("regenerate rollback qualification", DOCUMENTATION)
        self.assertIn("rotate/delete all four ephemeral credentials", DOCUMENTATION)

    def test_every_owner_contract_dark_proof_is_closed_in_source(self) -> None:
        for proof in (
            "oauth_session_identity",
            "agent_session_identity",
            "owner_request",
            "maintainer_approve",
            "maintainer_reject",
            "alias_assignment",
            "identity_rename",
            "complete_graph_consolidation",
            "chained_terminal_retry",
            "component_cap_refusal",
            "idempotent_retry",
            "cross_route_event_collision",
            "cross_owner_denial",
            "maximal_contention_measurement",
        ):
            self.assertIn(f'"{proof}"', RUNNER)
        self.assertIn("attempts != 8", RUNNER)
        self.assertIn("MAX_MODEL_IDENTITY_SUBREQUESTS", RUNNER)


if __name__ == "__main__":
    unittest.main()
