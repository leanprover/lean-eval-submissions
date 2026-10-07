import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import alert_dispatch_lane as alert  # noqa: E402

RUN_URL = "https://github.com/leanprover/lean-eval-submissions/actions/runs/36738416810"
JOBS = {"jobs": [
    {"name": "evaluate", "conclusion": "success", "steps": []},
    {"name": "evaluation_state", "conclusion": "failure",
     "steps": [{"name": "Build trusted evaluation completion", "conclusion": "failure"}]},
]}


class FakeGh:
    def __init__(self, open_issues: list[dict]) -> None:
        self.open_issues = open_issues
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["api", f"repos/r/actions/runs/36738416810/jobs?per_page=100"]:
            return json.dumps(JOBS)
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.open_issues)
        return ""


class AlertDispatchLaneTests(unittest.TestCase):
    def test_failure_opens_an_issue_naming_the_failed_step(self) -> None:
        fake = FakeGh([])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.report_failure("r", "36738416810", RUN_URL, "@kim-em"), "opened")
        create = next(c for c in fake.calls if c[:2] == ["issue", "create"])
        body = create[create.index("--body") + 1]
        self.assertIn("@kim-em", body)
        self.assertIn("`evaluation_state`: `Build trusted evaluation completion`", body)
        self.assertIn(RUN_URL, body)

    def test_repeated_failure_appends_once(self) -> None:
        body = f"intro\n\nFailed runs:\n- {RUN_URL} (x)\n\nThis issue closes itself when a server-dispatched run succeeds.\n"
        fake = FakeGh([{"number": 7, "title": alert.ISSUE_TITLE, "body": body}])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.report_failure("r", "36738416810", RUN_URL, "@kim-em"), "already_recorded")
            other = RUN_URL.replace("36738416810", "37104239065")
            fake_jobs = dict(JOBS)
            with mock.patch.object(alert, "failed_steps", return_value=["`evaluation_state`: `x`"]):
                self.assertEqual(alert.report_failure("r", "37104239065", other, "@kim-em"), "updated")
        edit = next(c for c in fake.calls if c[:2] == ["issue", "edit"])
        new_body = edit[edit.index("--body") + 1]
        self.assertEqual(new_body.count("- https://"), 2)
        self.assertTrue(new_body.index(other) < new_body.index("This issue closes itself"))
        del fake_jobs

    def test_success_closes_the_open_issue_only(self) -> None:
        fake = FakeGh([])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.report_success("r", RUN_URL), "nothing_open")
        fake = FakeGh([{"number": 7, "title": alert.ISSUE_TITLE, "body": "b"}])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.report_success("r", RUN_URL), "closed")
        close = next(c for c in fake.calls if c[:2] == ["issue", "close"])
        self.assertEqual(close[2], "7")
        self.assertIn(RUN_URL, close[close.index("--comment") + 1])

    def test_other_titles_are_not_mistaken_for_the_alert(self) -> None:
        fake = FakeGh([{"number": 9, "title": "[monitor] LeanEval lifecycle readiness failure", "body": "b"}])
        with mock.patch.object(alert, "gh", fake):
            self.assertEqual(alert.report_success("r", RUN_URL), "nothing_open")

    def test_main_rejects_non_canonical_run_identity_and_ignores_cancellations(self) -> None:
        self.assertEqual(alert.main(["--repository", "r", "--run-id", "x", "--run-url", RUN_URL, "--conclusion", "failure"]), 2)
        with mock.patch.object(alert, "gh", FakeGh([])):
            self.assertEqual(alert.main(["--repository", "r", "--run-id", "1", "--run-url", RUN_URL, "--conclusion", "cancelled"]), 0)


if __name__ == "__main__":
    unittest.main()
