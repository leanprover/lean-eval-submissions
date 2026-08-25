from __future__ import annotations

import io
import contextlib
import json
import pathlib
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import classify_evaluate_failure as classify  # noqa: E402


def steps(*pairs: tuple[str, str]) -> list[dict]:
    return [{"name": name, "conclusion": conclusion} for name, conclusion in pairs]


# Representative log from a build whose runner was killed after exhausting
# memory near the end of the build.
OOM_LOG = """\
2026-08-16T18:28:27.2676Z ✔ [8761/9423] Built Submission.E8.RootSystem.Data.Band03_05 (55s)
2026-08-16T19:56:17.3844Z ✔ [8762/9423] Built Submission.E8
2026-08-16T19:56:17.4022Z ##[error]The runner has received a shutdown signal. \
This can happen when the runner service is stopped, or a manually started runner is canceled.
2026-08-16T19:56:17.4025Z ##[error]Process completed with exit code 143.
"""

# Representative log from a preflight where Landlock confinement did not
# engage, so the probe refused to continue.
PROBE_LOG = """\
2026-08-20T01:12:16.1189Z sandbox_engaged_probe: FAIL — sandbox is NOT engaged as expected.
2026-08-20T01:12:16.1190Z   - tmp: expected DENIED, got ALLOWED.
2026-08-20T01:12:16.1263Z ##[error]Process completed with exit code 1.
"""


class ClassifyTests(unittest.TestCase):
    def test_signal_death_is_reported_as_a_memory_kill(self) -> None:
        category, message, blames = classify.classify(
            steps(("Run evaluate_submission.py", "failure")), OOM_LOG
        )
        self.assertEqual(category, "runner-killed")
        self.assertFalse(blames)
        self.assertIn("exhausting", message)
        # The load-bearing correction: the old comment sent submitters to
        # look for a compile error that was never there.
        self.assertIn("did not fail to compile", message)

    def test_sigkill_counts_as_a_memory_kill_too(self) -> None:
        category, _, _ = classify.classify(
            steps(("Run evaluate_submission.py", "failure")),
            "##[error]Process completed with exit code 137.",
        )
        self.assertEqual(category, "runner-killed")

    def test_an_exit_code_that_merely_contains_143_is_not_a_signal(self) -> None:
        category, _, _ = classify.classify(
            steps(("Run evaluate_submission.py", "failure")),
            "##[error]Process completed with exit code 1437.",
        )
        self.assertEqual(category, "harness")

    def test_preflight_failure_names_the_step_and_absolves_the_submission(self) -> None:
        category, message, blames = classify.classify(
            steps(
                ("Set up job", "success"),
                ("Probe sandbox is engaged", "failure"),
                ("Run evaluate_submission.py", "skipped"),
            ),
            PROBE_LOG,
        )
        self.assertEqual(category, "preflight")
        self.assertFalse(blames)
        self.assertIn("`Probe sandbox is engaged`", message)
        self.assertIn("nothing to do with your proof", message)

    def test_harness_failure_is_the_one_case_the_submission_may_own(self) -> None:
        category, message, blames = classify.classify(
            steps(("Run evaluate_submission.py", "failure")), "no signal here"
        )
        self.assertEqual(category, "harness")
        self.assertTrue(blames)
        self.assertIn("overlays", message)

    def test_a_cancelled_step_is_treated_as_the_failure_point(self) -> None:
        # A killed job marks the running step cancelled, not failed.
        category, message, _ = classify.classify(
            steps(("Install landrun", "cancelled")), None
        )
        self.assertEqual(category, "preflight")
        self.assertIn("`Install landrun`", message)

    def test_missing_log_still_classifies_from_steps(self) -> None:
        # The logs endpoint 404s while a job is still finalizing, which is
        # exactly when notify runs.
        category, _, _ = classify.classify(
            steps(("Probe sandbox is engaged", "failure")), None
        )
        self.assertEqual(category, "preflight")

    def test_no_failed_step_falls_back_without_blaming_anyone(self) -> None:
        category, message, blames = classify.classify(
            steps(("Set up job", "success")), None, job_conclusion="cancelled"
        )
        self.assertEqual(category, "unknown")
        self.assertFalse(blames)
        self.assertIn("cancelled", message)


class MainTests(unittest.TestCase):
    def _run(self, payload: dict, log: str | None) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            jobs_path = root / "jobs.json"
            jobs_path.write_text(json.dumps(payload))
            argv = ["--jobs", str(jobs_path)]
            if log is not None:
                log_path = root / "job.log"
                log_path.write_text(log)
                argv += ["--log", str(log_path)]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(classify.main(argv), 0)
            return buf.getvalue()

    def test_emits_github_output_syntax(self) -> None:
        out = self._run(
            {
                "jobs": [
                    {"name": "intake", "conclusion": "success", "steps": []},
                    {
                        "name": "evaluate",
                        "conclusion": "failure",
                        "steps": steps(("Run evaluate_submission.py", "failure")),
                    },
                ]
            },
            OOM_LOG,
        )
        self.assertIn("category=runner-killed", out)
        self.assertIn("blames_submission=false", out)
        # Multi-line values need heredoc syntax or they corrupt $GITHUB_OUTPUT.
        self.assertIn("message<<CLASSIFY_EOF", out)
        self.assertIn("\nCLASSIFY_EOF", out)

    def test_absent_log_file_is_not_fatal(self) -> None:
        out = self._run(
            {
                "jobs": [
                    {
                        "name": "evaluate",
                        "conclusion": "failure",
                        "steps": steps(("Install age (audit encryption)", "failure")),
                    }
                ]
            },
            None,
        )
        self.assertIn("category=preflight", out)

    def test_missing_evaluate_job_does_not_raise(self) -> None:
        out = self._run({"jobs": [{"name": "intake", "conclusion": "success"}]}, None)
        self.assertIn("category=unknown", out)


if __name__ == "__main__":
    unittest.main()
