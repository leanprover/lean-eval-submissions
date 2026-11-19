from __future__ import annotations

import unittest
import json
import pathlib
import subprocess
import sys
import tempfile
import time

from tests.test_historical_replay_controller import Fixture

from scripts.historical_replay_job_budget import capacity


class HistoricalReplayJobBudgetTests(unittest.TestCase):
    def budget(self, elapsed: int, phase: str = "before-start", wall: int = 19_800_000):
        return capacity(job_started_epoch=1000, now_epoch=1000 + elapsed,
                        wall_time_limit_ms=wall, phase=phase)

    def test_exact_capacity_keeps_full_runner_grace_start_and_cleanup(self) -> None:
        # 480 setup + 300 startup + 19800 execution + 300 grace + 720 cleanup
        # exactly fills the existing 21600-second hosted job.
        result = self.budget(480)
        self.assertTrue(result["fits"])
        self.assertEqual(result["wall_time_limit_ms"], 19_800_000)
        self.assertEqual(result["required_seconds"], 20_400)
        self.assertEqual(result["remaining_seconds"], 20_400)
        self.assertEqual(result["cleanup_seconds"], 720)
        self.assertEqual(result["poll_deadline_epoch"], 1000 + 20_880)

    def test_one_second_over_capacity_stops_before_start(self) -> None:
        self.assertFalse(self.budget(481)["fits"])
        self.assertFalse(self.budget(20 * 60)["fits"])

    def test_start_allowance_can_be_consumed_without_truncating_execution(self) -> None:
        self.assertTrue(self.budget(480)["fits"])
        result = self.budget(480 + 300, "before-execute")
        self.assertTrue(result["fits"])
        self.assertEqual(result["required_seconds"], 19_800 + 300)
        self.assertFalse(self.budget(781, "before-execute")["fits"])

    def test_unusually_slow_start_is_refused_before_executor(self) -> None:
        self.assertTrue(self.budget(479)["fits"])
        self.assertFalse(self.budget(479 + 302, "before-execute")["fits"])

    def test_uses_full_reviewed_limit_including_fractional_second(self) -> None:
        self.assertFalse(self.budget(480, wall=19_800_001)["fits"])
        self.assertTrue(self.budget(479, wall=19_800_001)["fits"])
        self.assertFalse(self.budget(0, wall=21_600_000)["fits"])

    def test_cli_reports_full_limit_and_fails_when_setup_consumed_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "plan.json"
            path.write_text(json.dumps(Fixture().plan()))
            for elapsed, expected_returncode in ((0, 0), (1000, 1)):
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
