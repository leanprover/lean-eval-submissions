from __future__ import annotations

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/append-historical-baseline-state.yml").read_text(
    encoding="utf-8"
)
WRITER = (ROOT / "scripts/publish_replay_state_batch").read_text(encoding="utf-8")


class HistoricalBaselineStateWorkflowTests(unittest.TestCase):
    def test_lane_is_manual_serialized_one_shot_and_unmerged_by_default(self) -> None:
        self.assertIn("workflow_dispatch:", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertIn("environment: replay-production", WORKFLOW)
        self.assertIn("append-reviewed-historical-baseline", WORKFLOW)
        self.assertIn("Merge only after production launch", WORKFLOW)

    def test_one_current_state_parent_closes_both_candidates_before_one_append(self) -> None:
        current = WORKFLOW.index("ref: ${{ inputs.expected_state_head }}")
        public = WORKFLOW.index("prepare_historical_public_authority.py finalize-batch")
        private = WORKFLOW.index("prepare_historical_private_replay.py state-events")
        combined = WORKFLOW.index("prepare_historical_baseline_state_batch.py")
        append = WORKFLOW.index("publish_replay_state_batch")
        self.assertLess(current, public)
        self.assertLess(public, private)
        self.assertLess(private, combined)
        self.assertLess(combined, append)
        self.assertEqual(WORKFLOW.count("publish_replay_state_batch"), 1)
        self.assertIn('--state-head "$EXPECTED_STATE_HEAD"', WORKFLOW)
        self.assertIn('--state-commit "$EXPECTED_STATE_HEAD"', WORKFLOW)
        self.assertGreaterEqual(
            WORKFLOW.count("81e94fe2f4fc819300fd7d4e036f00124166784f"), 2
        )

    def test_no_controller_or_external_mutation_is_enabled(self) -> None:
        self.assertNotIn("HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED", WORKFLOW)
        self.assertNotIn("HISTORICAL_PRIVATE_REPLAY_CONTROLLER_ENABLED", WORKFLOW)
        self.assertNotIn("wrangler", WORKFLOW)
        self.assertNotIn("aws ", WORKFLOW)
        self.assertNotIn("upload-artifact", WORKFLOW)
        self.assertNotIn("LEGACY_ARCHIVE_IDENTITY: ${{ secrets", WORKFLOW)

    def test_counts_live_in_the_packet_input_not_the_workflow(self) -> None:
        expectation = json.loads(
            (ROOT / "configuration/historical-baseline-state-batch-v1.json").read_text()
        )
        self.assertEqual(expectation["total_event_count"], 2439)
        self.assertEqual(expectation["total_task_count"], 813)
        self.assertNotIn("2439", WORKFLOW)
        self.assertNotIn("813", WORKFLOW)

    def test_writer_is_create_only_non_force_and_reconciles_unknown_push(self) -> None:
        self.assertIn("diff --cached --name-status", WRITER)
        self.assertIn("!= A", WRITER)
        self.assertIn("HEAD:refs/heads/main", WRITER)
        self.assertNotIn("--force", WRITER)
        self.assertNotIn("+HEAD:", WRITER)
        self.assertIn('merge-base --is-ancestor "$new_commit" "$remote_head"', WRITER)
        self.assertIn("candidate event tree contains missing or extra files", WRITER)
        self.assertIn("candidate event digest changed", WRITER)


if __name__ == "__main__":
    unittest.main()
