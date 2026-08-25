"""Hostile structural tests for the source-disabled staging workflows."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github/workflows/model-identity-staging-qualification.yml"
).read_text(encoding="utf-8")
RECOVERY = (ROOT / ".github/workflows/model-identity-staging-recovery.yml").read_text(
    encoding="utf-8"
)
RUNNER = (ROOT / "scripts/run_model_identity_staging_qualification.py").read_text(
    encoding="utf-8"
)
DOCUMENTATION = (ROOT / "docs/model-identity-staging-qualification.md").read_text(
    encoding="utf-8"
)
WRANGLER = json.loads((ROOT / "server/wrangler.jsonc").read_text(encoding="utf-8"))


class ModelIdentityStagingQualificationWorkflowTests(unittest.TestCase):
    def test_every_job_is_immutably_source_disabled(self) -> None:
        impossible = "github.ref == 'refs/source-disabled/model-identity-qualification'"
        self.assertEqual(WORKFLOW.count(impossible), 2)
        self.assertEqual(RECOVERY.count(impossible), 1)
        self.assertIn("IMMUTABLE SOURCE SAFETY GATE", WORKFLOW)
        self.assertIn("IMMUTABLE SOURCE SAFETY GATE", RECOVERY)
        self.assertIn("source-disabled", WORKFLOW.splitlines()[0])
        self.assertIn("source-disabled", RECOVERY.splitlines()[0])

    def test_exact_first_attempt_stable_operator_authorization(self) -> None:
        for text in (WORKFLOW, RECOVERY):
            self.assertIn('test "$GITHUB_RUN_ATTEMPT" = "1"', text)
            self.assertIn('test "$ACTOR_ID" = "477956"', text)
            self.assertIn("TRIGGERING_ACTOR: ${{ github.triggering_actor }}", text)
        self.assertIn('test "$GITHUB_ACTOR" = "kim-em"', WORKFLOW)
        self.assertIn('test "$TRIGGERING_ACTOR" = "kim-em"', WORKFLOW)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', WORKFLOW)
        self.assertIn('test "$GITHUB_SHA" = "$EXPECTED_COMMIT"', WORKFLOW)

    def test_secrets_are_only_on_authored_controller_steps_after_checkout(self) -> None:
        secrets = {
            "MODEL_IDENTITY_AGENT_SESSION",
            "MODEL_IDENTITY_CROSS_OWNER_SESSION",
            "MODEL_IDENTITY_MAINTAINER_SESSION",
            "MODEL_IDENTITY_OAUTH_SESSION",
            "MODEL_IDENTITY_QUALIFICATION_TOKEN",
        }
        self.assertEqual(set(re.findall(r"secrets\.([A-Z0-9_]+)", WORKFLOW)), secrets)
        self.assertEqual(
            set(re.findall(r"secrets\.([A-Z0-9_]+)", RECOVERY)),
            {"MODEL_IDENTITY_QUALIFICATION_TOKEN"},
        )
        first_secret = WORKFLOW.index("secrets.MODEL_IDENTITY")
        self.assertLess(WORKFLOW.index("actions/checkout@"), first_secret)
        self.assertLess(WORKFLOW.index("Run journaled dark lifecycle"), first_secret)
        self.assertNotIn(
            "      MODEL_IDENTITY_",
            WORKFLOW[
                : WORKFLOW.index("steps:", WORKFLOW.index("qualify-and-restore:"))
            ],
        )
        for secret in secrets:
            self.assertNotIn(
                secret, WRANGLER["env"]["production"]["secrets"]["required"]
            )

    def test_recovery_is_separate_automatic_and_manual(self) -> None:
        self.assertIn("  workflow_run:", RECOVERY)
        self.assertIn("  workflow_dispatch:", RECOVERY)
        self.assertIn("types: [completed]", RECOVERY)
        self.assertIn("RESTORE_MODEL_IDENTITY_STAGING_JOURNAL", RECOVERY)
        self.assertIn("ORIGINAL_HEAD_REPOSITORY", RECOVERY)
        self.assertIn("model-identity-staging-recovery-", RECOVERY)
        self.assertNotIn("MODEL_IDENTITY_OAUTH_SESSION", RECOVERY)
        self.assertNotIn("MODEL_IDENTITY_AGENT_SESSION", RECOVERY)

    def test_automatic_recovery_rejects_reruns_and_a_different_operator(self) -> None:
        automatic = RECOVERY.split(
            'if test "$EVENT_NAME" = workflow_run; then', maxsplit=1
        )[1].split("          else", maxsplit=1)[0]
        self.assertIn('test "$ORIGINAL_ACTOR_ID" = "477956"', automatic)
        self.assertIn('test "$ORIGINAL_RUN_ATTEMPT" = "1"', automatic)
        self.assertIn(
            'test "$ORIGINAL_TRIGGERING_ACTOR_ID" = "477956"', automatic
        )
        self.assertIn(
            'test "$ORIGINAL_TRIGGERING_ACTOR_LOGIN" = kim-em', automatic
        )
        self.assertIn(
            "ORIGINAL_RUN_ATTEMPT: ${{ github.event.workflow_run.run_attempt }}",
            RECOVERY,
        )
        self.assertIn(
            "ORIGINAL_TRIGGERING_ACTOR_ID: ${{ github.event.workflow_run.triggering_actor.id }}",
            RECOVERY,
        )
        self.assertIn(
            "ORIGINAL_TRIGGERING_ACTOR_LOGIN: ${{ github.event.workflow_run.triggering_actor.login }}",
            RECOVERY,
        )

    def test_durable_source_free_evidence_is_always_uploaded(self) -> None:
        for text in (WORKFLOW, RECOVERY):
            self.assertIn("if: ${{ always() }}", text)
            self.assertIn(
                "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
                text,
            )
            self.assertIn("if-no-files-found: error", text)
        self.assertIn('"proofs": []', RUNNER)
        self.assertIn('"restoration": None', RUNNER)
        self.assertIn('"final_health": None', RUNNER)

    def test_closed_state_and_evidence_contract_is_in_source(self) -> None:
        self.assertIn("state_commit == expected_head", RUNNER)
        self.assertIn("state_tree == expected_tree", RUNNER)
        self.assertIn('"restoration_parent_commit"', RUNNER)
        self.assertIn('"restoration_parent_tree"', RUNNER)
        self.assertIn('"fast_forward"', RUNNER)
        self.assertIn('"tree_equal"', RUNNER)
        self.assertIn("recover_journal(", RUNNER)
        self.assertIn("finally:", RUNNER)
        self.assertIn("final disabled health verification", RUNNER)
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

    def test_production_and_public_gates_remain_unreachable(self) -> None:
        for text in (WORKFLOW, RECOVERY, RUNNER):
            self.assertNotIn("cloudflare-production", text)
            self.assertNotIn("lean-eval-submission-server.lean-eval", text)
            self.assertNotIn("MODEL_IDENTITY_OWNER_API_ENABLED=true", text)
            self.assertNotIn("MODEL_IDENTITY_MAINTAINER_API_ENABLED=true", text)
        for environment in ("staging", "production"):
            variables = WRANGLER["env"][environment]["vars"]
            self.assertEqual(variables["MODEL_IDENTITY_OWNER_API_ENABLED"], "false")
            self.assertEqual(
                variables["MODEL_IDENTITY_MAINTAINER_API_ENABLED"], "false"
            )

    def test_documentation_keeps_live_harness_as_a_blocker(self) -> None:
        self.assertIn(
            "The privileged internal harness is intentionally **not** implemented",
            DOCUMENTATION,
        )
        self.assertIn("Do not mark any dark gate complete", DOCUMENTATION)


if __name__ == "__main__":
    unittest.main()
