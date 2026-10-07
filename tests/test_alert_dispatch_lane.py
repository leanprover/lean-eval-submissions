import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import alert_dispatch_lane as alert  # noqa: E402

REPO = "leanprover/lean-eval-submissions"
BOT = {"login": alert.BOT_LOGIN}


def run(run_id: int, conclusion: str, started: str, attempt: int = 1) -> dict:
    return {"id": run_id, "run_attempt": attempt, "conclusion": conclusion, "run_started_at": started}


def issue(number: int, body: str, login: str = alert.BOT_LOGIN, title: str = alert.ISSUE_TITLE) -> dict:
    return {"number": number, "title": title, "body": body, "user": {"login": login}}


class FakeGh:
    def __init__(self, runs: list[dict], issues: list[dict] | None = None, comments=None, jobs_fail=False) -> None:
        self.runs, self.issues, self.comments, self.jobs_fail = runs, issues or [], comments or {}, jobs_fail
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        target = args[-1]
        if args[0] == "api" and "/actions/workflows/" in target:
            return json.dumps({"workflow_runs": self.runs})
        if args[0] == "api" and "/attempts/" in target:
            if self.jobs_fail:
                raise subprocess.CalledProcessError(1, args)
            return json.dumps({"jobs": [
                {"name": "evaluate", "conclusion": "success", "steps": []},
                {"name": "evaluation_state", "conclusion": "failure",
                 "steps": [{"name": "Build trusted evaluation completion", "conclusion": "failure"}]},
            ]})
        if args[0] == "api" and target.startswith(f"repos/{REPO}/issues?"):
            return json.dumps([self.issues])
        if args[0] == "api" and "/comments" in target:
            number = int(target.split("/issues/")[1].split("/")[0])
            return json.dumps([self.comments.get(number, [])])
        return ""

    def created_body(self) -> str:
        create = next(c for c in self.calls if c[:2] == ["issue", "create"])
        return create[create.index("--body") + 1]


class AlertDispatchLaneTests(unittest.TestCase):
    def test_latest_failure_opens_a_marked_issue_listing_runs_since_last_success(self) -> None:
        fake = FakeGh([run(3, "failure", "2026-10-07T03:00:00Z"), run(2, "failure", "2026-10-07T02:00:00Z"),
                       run(1, "success", "2026-10-07T01:00:00Z"), run(0, "failure", "2026-10-07T00:00:00Z")])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.reconcile(REPO, "@kim-em"), "opened")
        body = fake.created_body()
        self.assertTrue(body.startswith(alert.ISSUE_MARKER))
        self.assertIn("@kim-em", body)
        self.assertIn(alert.run_marker(3, 1), body)
        self.assertIn(alert.run_marker(2, 1), body)
        self.assertNotIn(alert.run_marker(0, 1), body)
        self.assertIn("`evaluation_state`: `Build trusted evaluation completion`", body)
        self.assertIn(f"https://github.com/{REPO}/actions/runs/3/attempts/1", body)

    def test_missing_job_details_do_not_block_the_alert(self) -> None:
        fake = FakeGh([run(3, "timed_out", "2026-10-07T03:00:00Z")], jobs_fail=True)
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.reconcile(REPO, "@kim-em"), "opened")
        self.assertIn("job details unavailable", fake.created_body())

    def test_reconcile_is_idempotent_and_records_new_failures_once(self) -> None:
        existing = issue(7, f"{alert.ISSUE_MARKER}\nFailed runs:\n{alert.run_marker(2, 1)}\n- x")
        runs = [run(3, "failure", "2026-10-07T03:00:00Z"), run(2, "failure", "2026-10-07T02:00:00Z")]
        fake = FakeGh(runs, [existing])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.reconcile(REPO, "@kim-em"), "recorded_1")
        comment = next(c for c in fake.calls if c[:2] == ["issue", "comment"])
        self.assertIn(alert.run_marker(3, 1), comment[comment.index("--body") + 1])
        fake = FakeGh(runs, [existing], comments={7: [{"user": BOT, "body": alert.run_marker(3, 1)}]})
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.reconcile(REPO, "@kim-em"), "recorded_0")
        self.assertFalse([c for c in fake.calls if c[:2] in (["issue", "comment"], ["issue", "create"])])

    def test_success_closes_only_owned_incidents_and_duplicates_collapse(self) -> None:
        human = issue(5, "I typed this title myself", login="alice")
        unmarked_bot = issue(6, "no marker")
        owned = [issue(8, alert.ISSUE_MARKER), issue(7, alert.ISSUE_MARKER)]
        fake = FakeGh([run(4, "success", "2026-10-07T04:00:00Z")], [human, unmarked_bot, *owned])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.reconcile(REPO, "@kim-em"), "closed")
        closed = sorted(c[2] for c in fake.calls if c[:2] == ["issue", "close"])
        self.assertEqual(closed, ["7", "8"])
        fake = FakeGh([run(4, "failure", "2026-10-07T04:00:00Z")], [human, *owned])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.reconcile(REPO, "@kim-em"), "recorded_1")
        closed = [c for c in fake.calls if c[:2] == ["issue", "close"]]
        self.assertEqual([c[2] for c in closed], ["8"])
        self.assertIn("Duplicate of #7", closed[0][closed[0].index("--comment") + 1])

    def test_healthy_lane_and_cancellations_change_nothing(self) -> None:
        fake = FakeGh([run(4, "success", "2026-10-07T04:00:00Z")])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.reconcile(REPO, "@kim-em"), "healthy")
        fake = FakeGh([run(5, "cancelled", "2026-10-07T05:00:00Z"), run(4, "success", "2026-10-07T04:00:00Z")])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.reconcile(REPO, "@kim-em"), "latest_cancelled")
        self.assertFalse([c for c in fake.calls if c[0] == "issue"])

    def test_main_validates_the_repository(self) -> None:
        self.assertEqual(alert.main(["--repository", "not a repo"]), 2)
        with mock.patch.object(alert, "gh", FakeGh([])):
            self.assertEqual(alert.main(["--repository", REPO]), 0)


if __name__ == "__main__":
    unittest.main()
