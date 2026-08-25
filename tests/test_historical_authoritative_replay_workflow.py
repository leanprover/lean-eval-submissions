from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github/workflows/historical-authoritative-replay.yml"
).read_text(encoding="utf-8")


class HistoricalAuthoritativeReplayWorkflowTests(unittest.TestCase):
    def test_lane_is_manual_serialized_and_explicitly_dark(self) -> None:
        self.assertIn("workflow_dispatch:", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertIn("environment: replay-production", WORKFLOW)
        self.assertIn(
            "vars.HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED == 'true'", WORKFLOW
        )
        self.assertNotIn("schedule:", WORKFLOW)

    def test_lane_has_only_read_permissions_and_no_external_authority(self) -> None:
        self.assertNotIn("contents: write", WORKFLOW)
        self.assertNotIn("id-token: write", WORKFLOW)
        self.assertNotIn("AWS_", WORKFLOW)
        self.assertNotIn("CLOUDFLARE_", WORKFLOW)
        self.assertNotIn("STATE_WRITE", WORKFLOW)
        self.assertNotIn("publish_replay_state_event", WORKFLOW)
        self.assertIn("PRODUCTION_STATE_READ_KEY", WORKFLOW)

    def test_recovery_precedes_planning_and_never_appends(self) -> None:
        validation = WORKFLOW.index("state/scripts/state.py --root state validate")
        recovery = WORKFLOW.index("Refuse to plan around a running historical attempt")
        planning = WORKFLOW.index("Produce only the source-free transport-blocked plan")
        self.assertLess(validation, recovery)
        self.assertLess(recovery, planning)
        self.assertIn("historical_replay_controller.py recover", WORKFLOW)
        self.assertIn("--state-validated", WORKFLOW)
        self.assertIn("steps.recovery.outputs.kind == 'none'", WORKFLOW)
        self.assertNotIn("runner_lost", WORKFLOW)
        self.assertNotIn("already running", WORKFLOW)

    def test_exact_production_state_and_reviewed_blobs_are_bound(self) -> None:
        self.assertIn("repository: leanprover/lean-eval-state", WORKFLOW)
        self.assertIn("state/scripts/state.py --root state validate", WORKFLOW)
        self.assertIn("historical-public-replay-queue.json", WORKFLOW)
        self.assertIn("--repository-root .", WORKFLOW)
        self.assertNotIn(".tasks[0].authority_path", WORKFLOW)
        self.assertNotIn(".tasks[0].qualification_path", WORKFLOW)
        self.assertNotIn("--authority-plan", WORKFLOW)
        self.assertNotIn("--qualification-profile", WORKFLOW)

    def test_transport_blocker_is_required_and_no_plan_is_uploaded(self) -> None:
        blocker = WORKFLOW.index("historical_public_executor_not_implemented")
        confirmation = WORKFLOW.index(
            "Confirm the lane remained read-only and transport-blocked"
        )
        self.assertLess(blocker, confirmation)
        self.assertIn(".transport.status", WORKFLOW)
        self.assertIn('"$kind" != blocked', WORKFLOW)
        self.assertIn(".blocker.status", WORKFLOW)
        self.assertNotIn("actions/upload-artifact", WORKFLOW)
        self.assertNotIn("state-event started", WORKFLOW)
        self.assertNotIn("state-event terminal", WORKFLOW)
        self.assertNotIn("/api/v1/", WORKFLOW)

    def test_actions_are_commit_pinned(self) -> None:
        pins = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", WORKFLOW)
        self.assertTrue(pins)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in pins))


if __name__ == "__main__":
    unittest.main()
