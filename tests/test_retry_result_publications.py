"""Only retry optional publication jobs from completed upstream submissions."""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from retry_result_publications import REPOSITORY, reconcile


class PublicationRetryTests(unittest.TestCase):
    def exercise(self, *, status="completed", event="issues", attempt=1, jobs=None):
        calls = []
        def api(path, method="GET"):
            calls.append((path, method))
            if "workflows/submission.yml/runs?" in path:
                return {"total_count": 1, "workflow_runs": [{
                    "id": 123, "status": status, "event": event,
                    "path": ".github/workflows/submission.yml", "run_attempt": attempt,
                }]}
            if "/runs/123/jobs?" in path:
                return {"total_count": len(jobs), "jobs": jobs}
            self.assertEqual((path, method), (f"repos/{REPOSITORY}/actions/jobs/456/rerun", "POST"))
            return None
        result = reconcile(REPOSITORY, dt.datetime(2026, 11, 16, tzinfo=dt.timezone.utc), api)
        return result, calls

    def test_only_failed_publication_is_retried(self):
        jobs = [
            {"id": 455, "name": "evaluate", "status": "completed", "conclusion": "failure"},
            {"id": 456, "name": "measurement_state", "status": "completed", "conclusion": "failure"},
            {"id": 457, "name": "leaderboard_refresh", "status": "completed", "conclusion": "failure"},
        ]
        result, calls = self.exercise(jobs=jobs)
        self.assertEqual(result, [456])
        self.assertEqual(sum(method == "POST" for _, method in calls), 1)

    def test_successful_or_skipped_publication_is_not_retried(self):
        for conclusion in ("success", "skipped", "cancelled"):
            with self.subTest(conclusion=conclusion):
                result, _ = self.exercise(jobs=[{
                    "id": 456, "name": "measurement_state", "status": "completed", "conclusion": conclusion,
                }])
                self.assertEqual(result, [])

    def test_live_and_pull_request_runs_are_ignored(self):
        for status, event in (("in_progress", "issues"), ("queued", "workflow_dispatch"), ("completed", "pull_request")):
            result, calls = self.exercise(status=status, event=event)
            self.assertEqual(result, [])
            self.assertEqual(len(calls), 1)

    def test_wrong_repository_is_rejected_before_api_calls(self):
        with self.assertRaises(ValueError):
            reconcile("attacker/submissions", dt.datetime.now(dt.timezone.utc), lambda *_: self.fail())

    def test_retry_budget_exhaustion_is_visible(self):
        with self.assertRaisesRegex(RuntimeError, "exhausted"):
            self.exercise(attempt=168, jobs=[{
                "id": 456, "name": "leaderboard_refresh", "status": "completed", "conclusion": "failure",
            }])
