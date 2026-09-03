from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]


class FinalDeltaWorkflowTests(unittest.TestCase):
    def text(self, name: str) -> str:
        return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_archive_lane_is_separate_and_binds_the_audit_companion(self) -> None:
        stage = self.text("historical-final-delta-archive-migration.yml")
        promote = self.text("promote-historical-final-delta-archive-migration.yml")
        self.assertIn("historical-final-delta-archive-rewrap-v1", stage)
        self.assertNotIn('test "$actual_count" = 439', stage)
        self.assertIn("--selection-binding", stage)
        self.assertIn("d20953db8bf7d702b5f58ec092d75f954c1fab51", promote)
        self.assertIn("4e6cdda3f4245e57bdf0d5913cd9fbc24a9b02d9", promote)
        boundary = stage.index(
            "Prove decrypt and AWS authorities are gone before audit write authority"
        )
        writer = stage.index("Mint audit-only writer")
        self.assertLess(boundary, writer)
        self.assertIn("echo 'AWS_ACCESS_KEY_ID='", stage[:boundary])
        self.assertIn('AWS_ACCESS_KEY_ID: ""', stage[boundary:])
        self.assertIn("echo 'ACTIONS_ID_TOKEN_REQUEST_TOKEN='", stage[:boundary])
        self.assertIn("echo 'ACTIONS_ID_TOKEN_REQUEST_URL='", stage[:boundary])
        post_aws = stage[boundary:]
        self.assertGreaterEqual(
            post_aws.count('ACTIONS_ID_TOKEN_REQUEST_TOKEN: ""'), 5
        )
        self.assertGreaterEqual(
            post_aws.count('ACTIONS_ID_TOKEN_REQUEST_URL: ""'), 5
        )
        self.assertIn("rm -rf -- audit", stage[boundary:writer])

    def test_archive_plan_remains_private_and_only_binding_is_committed(self) -> None:
        stage = self.text("historical-final-delta-archive-migration.yml")
        docs = (ROOT / "docs" / "historical-final-delta.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("final-delta-archive-plans/", stage)
        self.assertNotIn("final-delta-archive-plans/", docs)
        self.assertIn("$RUNNER_TEMP/plan-one.json", stage)
        self.assertIn("final-delta-archive-migrations/", stage)

    def test_state_lane_is_separate_and_has_dynamic_counts(self) -> None:
        workflow = self.text("historical-final-delta-state.yml")
        self.assertIn("historical-final-delta-state-v1", workflow)
        self.assertIn("historical-final-delta-state-batch-v1.json", workflow)
        self.assertNotIn("2439", workflow)
        self.assertNotIn("historical-baseline-state-v1", workflow)
        self.assertIn("activation_sha256:", workflow)
        self.assertIn(
            'activation="evidence/historical-replay/final-delta-activations/'
            '$ACTIVATION_SHA256.json"',
            workflow,
        )
        self.assertIn(
            "schemas/historical-final-delta-activation-v1.schema.json", workflow
        )
        self.assertIn(".state_promotion.sha256", workflow)
        self.assertIn(".state.candidate_commit", workflow)

    def test_qualification_is_only_a_blocked_one_shot_output(self) -> None:
        script = (
            ROOT / "scripts" / "prepare_historical_final_delta_activation.py"
        ).read_text(encoding="utf-8")
        self.assertIn("run one-shot qualification only", script)
        self.assertNotIn("Durable Object", script)
        self.assertNotIn("schedule:", script)

    def test_closure_is_authenticated_and_does_not_retire_reusable_controllers(
        self,
    ) -> None:
        workflow = self.text("historical-final-delta-activation.yml")
        permissions = workflow.split("permissions:", 1)[1].split("concurrency:", 1)[0]
        self.assertNotIn("id-token", permissions)
        self.assertIn("HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED", workflow)
        self.assertIn("workers/services?per_page=1000", workflow)
        self.assertIn("historical-final-delta-state-v1", workflow)
        self.assertIn("historical-final-delta-archive-rewrap-v1", workflow)
        self.assertIn("prepare_historical_final_delta_closure.py terminal", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("actions/runs/$run_id", workflow)
        self.assertIn('gh run download "$run_id"', workflow)
        self.assertIn('cmp "$artifact" "$proof"', workflow)
        self.assertIn("Reverify the exact create-only staged State candidate", workflow)
        self.assertIn(
            "inputs.operation == 'absence' || inputs.operation == 'terminal'",
            workflow,
        )
        self.assertIn("historical_final_delta_terminal_live_readback", workflow)
        self.assertIn('--terminal-readback "$RUNNER_TEMP/', workflow)
        authenticated = workflow.index(
            "Authenticate the committed absence proof before terminal readback"
        )
        live_readback = workflow.index(
            "Read back current disabled controllers, absent refs, queues, and executors"
        )
        terminal = workflow.index(
            "Build the independently reconciled terminal binding twice"
        )
        self.assertLess(authenticated, live_readback)
        self.assertLess(live_readback, terminal)
        retirement = workflow.split("temporary_workflows:", 1)[1]
        self.assertNotIn("historical-authoritative-replay.yml", retirement)
        self.assertNotIn("historical-private-replay.yml", retirement)


if __name__ == "__main__":
    unittest.main()
