from __future__ import annotations

import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import historical_private_phase as phase
import historical_private_replay_controller as c
from test_historical_private_replay_controller import Fixture


class PrivatePhaseTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)
        self.lane = c.private_replay_lane(self.f.task["replay_task_id"], 4)
        self.workflow = c._git_text(self.f.repository, "rev-parse", "HEAD")

    def plan(self):
        return phase.plan_at_commit(self.f.state, self.f.repository, self.f.audit,
                                    self.f.state_head, self.lane)

    def start(self):
        plan = self.plan()
        candidate = c.started_candidate(plan, self.f.state, "2026-10-21T07:00:00.000Z",
                                        lane_index=self.lane, lane_count=4)
        status = c.prepare_prewarm_request(plan, runner_nonce="d" * 64)
        envelope = phase.export(plan, candidate, status, workflow=self.workflow,
                                run_id=12, run_attempt=1, prewarm=100)
        self.f.commit_state_event(candidate["event"])
        return plan, candidate, envelope

    def resume(self, envelope, **changes):
        args = dict(workflow=self.workflow, run_id=12, run_attempt=1,
                    accepted_epoch=200, now=300, lane=self.lane,
                    state=self.f.state, repository=self.f.repository, audit=self.f.audit)
        args.update(changes)
        return phase.resume(envelope, **args)

    def test_rebuild_matches_after_n_unrelated_state_rebindings(self):
        plan = self.plan()
        for i in range(3):
            queue = copy.deepcopy(self.f.queue)
            queue["source_event_count"] += i + 1
            queue["source_digest"] = str(i + 1) * 64
            self.f.update_state_queue(queue)
            plan = c.rebind_plan_to_current_state(plan, self.f.state,
                                                  lane_index=self.lane, lane_count=4)
            self.assertEqual(plan, self.plan())

    def test_resume_reconstructs_exact_plan_candidate_and_status_across_state_advance(self):
        plan, candidate, envelope = self.start()
        (self.f.state / "unrelated.txt").write_text("other append\n")
        self.f.state_head = self.f.commit_in(self.f.state, "Unrelated State advance")
        self.f.set_state_upstream()
        result = self.resume(envelope)
        self.assertEqual(result["plan"], plan)
        self.assertEqual(result["candidate"], candidate)
        self.assertEqual(result["status"], envelope["status"])
        self.assertEqual(result["poll_deadline_epoch"], 200 + 20100 + 120)
        forbidden = {"archive_path", "archive_binding", "source", "file_key", "unwrap", "token"}
        self.assertTrue(forbidden.isdisjoint(envelope))

    def test_wrong_handoff_identity_or_digest_is_refused(self):
        _, _, original = self.start()
        for field, replacement in [
            ("workflow_sha", "0" * 40), ("run_id", 13), ("run_attempt", 2),
            ("lane_index", (self.lane + 1) % 4), ("plan_sha256", "0" * 64),
            ("started_candidate_sha256", "0" * 64),
            ("prestart_state_commit", "0" * 40), ("status_sha256", "0" * 64),
        ]:
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed[field] = replacement
                with self.assertRaises((ValueError, c.HistoricalPrivateReplayControllerError)):
                    self.resume(changed)
        changed = copy.deepcopy(original)
        changed["status"]["attempt"] += 1
        with self.assertRaises(ValueError):
            self.resume(changed)

    def test_rerun_missing_or_extra_handoff_and_age_are_refused(self):
        _, _, envelope = self.start()
        for kwargs in [{"run_attempt": 2}, {"accepted_epoch": 4000}, {"now": 199}]:
            with self.assertRaises(ValueError):
                self.resume(envelope, **kwargs)
        for changed in [{}, {**envelope, "source": "forbidden"}]:
            with self.assertRaises((ValueError, c.HistoricalPrivateReplayControllerError)):
                self.resume(changed)

    def test_terminal_or_changed_attempt_does_not_resume(self):
        _, candidate, envelope = self.start()
        failed = c.terminal_candidate(
            self.plan_before(envelope), candidate,
            {"schema_version": 1, "replay_task_id": self.f.task["replay_task_id"],
             "attempt": 1, "execution_outcome": "failed", "checker_outcome": None,
             "failure_reason": "runner_lost", "statistics": None},
            self.f.state, "2026-10-21T07:01:00.000Z", lane_index=self.lane, lane_count=4,
        )
        self.f.commit_state_event(failed["event"])
        with self.assertRaises(ValueError):
            self.resume(envelope)

    def plan_before(self, envelope):
        return phase.plan_at_commit(self.f.state, self.f.repository, self.f.audit,
                                    envelope["prestart_state_commit"], self.lane)

    def test_budget_preserves_post_and_cleanup_at_exact_boundaries(self):
        phase.budget(100, 3090, 3090, admission=True)
        with self.assertRaises(ValueError):
            phase.budget(100, 3091, 3091, admission=True)
        phase.budget(100, 200, 2900)
        with self.assertRaises(ValueError):
            phase.budget(100, 200, 2901)
        self.assertEqual(phase.SERVER_PROCESS_SECONDS, 330 * 60 + 300)
        self.assertLess(phase.SERVER_PROCESS_SECONDS + phase.RECEIPT_GRACE_SECONDS
                        + phase.CLEANUP_SECONDS, phase.OBSERVER_SECONDS)


if __name__ == "__main__":
    unittest.main()
