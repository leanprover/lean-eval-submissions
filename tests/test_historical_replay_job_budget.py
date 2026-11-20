from __future__ import annotations

import unittest
import json
import pathlib
import subprocess
import sys
import tempfile
import time

from tests.test_historical_replay_controller import Fixture

from scripts import historical_replay_job_budget as budget
from scripts.historical_replay_job_budget import capacity


class HistoricalReplayJobBudgetTests(unittest.TestCase):
    def budget(self, elapsed: int, phase: str = "before-start", wall: int = 19_800_000):
        return capacity(job_started_epoch=1000, now_epoch=1000 + elapsed,
                        wall_time_limit_ms=wall, phase=phase)

    def test_exact_capacity_keeps_full_runner_grace_start_and_cleanup(self) -> None:
        # 410 setup + 370 startup + 19800 execution + 300 grace + 720 cleanup
        # exactly fills the existing 21600-second hosted job.
        result = self.budget(410)
        self.assertTrue(result["fits"])
        self.assertEqual(result["wall_time_limit_ms"], 19_800_000)
        self.assertEqual(result["required_seconds"], 20_470)
        self.assertEqual(result["remaining_seconds"], 20_470)
        self.assertEqual(result["cleanup_seconds"], 720)
        self.assertEqual(result["poll_deadline_epoch"], 1000 + 20_880)

    def test_one_second_over_capacity_stops_before_start(self) -> None:
        self.assertFalse(self.budget(411)["fits"])
        self.assertFalse(self.budget(20 * 60)["fits"])

    def test_start_allowance_can_be_consumed_without_truncating_execution(self) -> None:
        self.assertTrue(self.budget(410)["fits"])
        result = self.budget(410 + 60, "before-execute")
        self.assertTrue(result["fits"])
        self.assertEqual(result["required_seconds"], 19_800 + 300 + 310)
        self.assertFalse(self.budget(471, "before-execute")["fits"])

    def test_unusually_slow_start_is_refused_before_executor(self) -> None:
        self.assertTrue(self.budget(409)["fits"])
        self.assertFalse(self.budget(409 + 62, "before-execute")["fits"])

    def test_uses_full_reviewed_limit_including_fractional_second(self) -> None:
        self.assertFalse(self.budget(410, wall=19_800_001)["fits"])
        self.assertTrue(self.budget(409, wall=19_800_001)["fits"])
        self.assertFalse(self.budget(0, wall=21_600_000)["fits"])

    def test_cli_reports_full_limit_and_fails_when_setup_consumed_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "plan.json"
            path.write_text(json.dumps(Fixture().plan()))
            for elapsed, expected_returncode in ((0, 0), (1000, budget.DEFERRED_EXIT_STATUS)):
                with self.subTest(elapsed=elapsed):
                    completed = subprocess.run(
                        [sys.executable, "scripts/historical_replay_job_budget.py",
                         "--plan", str(path), "--phase", "before-start",
                         "--job-started-epoch", str(int(time.time()) - elapsed)],
                        text=True, capture_output=True, check=False,
                        cwd=pathlib.Path(__file__).resolve().parents[1],
                    )
                    self.assertEqual(completed.returncode, expected_returncode, completed.stderr)
                    result = json.loads(completed.stdout)
                    self.assertEqual(result["wall_time_limit_ms"], 19_800_000)
                    self.assertEqual(result["fits"], expected_returncode == 0)

    def test_workflow_and_server_timeout_constants_match_budget(self) -> None:
        import re
        root = pathlib.Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/historical-authoritative-replay.yml").read_text()
        server = (root / "server/src/replay-app.ts").read_text()
        job = workflow.split("  replay-one:\n", 1)[1].split("    steps:\n", 1)[0]
        job_minutes = int(re.search(r"^    timeout-minutes: (\d+)$", job, re.M).group(1))
        process_seconds = int(re.search(r"process_deadline=.*?\+ (\d+)", workflow).group(1))
        job_seconds = int(re.search(r"job_deadline=.*?\+ (\d+)", workflow).group(1))
        server_timeout_ms = int(re.search(r"AUTHORITATIVE_TIMEOUT_MS = ([\d_]+)", server).group(1).replace("_", ""))
        kill_grace, post_timeout = map(int, re.search(
            r"start_status=\$\(timeout --signal=TERM --kill-after=(\d+)s (\d+)s curl", workflow,
        ).groups())
        self.assertEqual(job_minutes * 60, budget.JOB_TIMEOUT_SECONDS)
        deferred_status = int(re.search(r'budget_status\" -ne (\d+)', workflow).group(1))
        self.assertEqual(deferred_status, budget.DEFERRED_EXIT_STATUS)
        self.assertEqual(job_seconds, budget.JOB_TIMEOUT_SECONDS - budget.CLEANUP_SECONDS)
        self.assertEqual(process_seconds, 19_800 + budget.POLL_GRACE_SECONDS)
        self.assertEqual(server_timeout_ms, process_seconds * 1000)
        walls = {
            json.loads(path.read_text())["measurement_config"]["wall_time_limit_ms"]
            for path in (root / "evidence/public-replay/profiles").glob("*.json")
        }
        self.assertEqual(walls, {19_800_000})
        self.assertEqual(post_timeout, budget.POST_TIMEOUT_SECONDS)
        self.assertEqual(kill_grace, budget.POST_KILL_GRACE_SECONDS)
        self.assertGreaterEqual(budget.POST_ALLOWANCE_SECONDS, post_timeout + kill_grace)
        self.assertIn('if [ "$job_deadline" -lt "$deadline" ]; then\n            echo \'::warning', workflow)

    def test_clock_rollback_invalid_limit_and_unknown_phase_fail_closed(self) -> None:
        for kwargs in (
            dict(job_started_epoch=1000, now_epoch=999, wall_time_limit_ms=19_800_000, phase="before-start"),
            dict(job_started_epoch=1000, now_epoch=1000, wall_time_limit_ms=0, phase="before-start"),
            dict(job_started_epoch=1000, now_epoch=1000, wall_time_limit_ms=True, phase="before-start"),
            dict(job_started_epoch=1000, now_epoch=1000, wall_time_limit_ms=19_800_000, phase="unknown"),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                capacity(**kwargs)


if __name__ == "__main__":
    unittest.main()
