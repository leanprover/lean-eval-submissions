"""Structural guard for `.github/workflows/submission.yml`.

The submission workflow's security and correctness rest on a handful of
structural invariants that a well-meaning refactor can silently break.
Unit-testing the Python scripts does not cover the workflow YAML, so this
test asserts those invariants directly against the file text.

It deliberately uses plain text/line assertions rather than a YAML parser
so it needs no third-party dependency and so a reviewer can map each
assertion to a literal line of the workflow.

See SECURITY.md for why each invariant matters.
"""

from __future__ import annotations

import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "submission.yml"


class SubmissionWorkflowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.lines = cls.text.splitlines()

    def test_workflow_file_exists(self) -> None:
        self.assertTrue(WORKFLOW.is_file(), f"missing {WORKFLOW}")

    def test_fetch_and_evaluate_share_one_job(self) -> None:
        # A previous design split fetch into its own job and shipped the
        # cloned source as an artifact, which leaked private submissions
        # on a public repo. Fetch + evaluate must stay in one job.
        self.assertIn("python scripts/fetch_submission.py", self.text)
        self.assertIn("python scripts/evaluate_submission.py", self.text)
        # Job headers are the 2-space-indented keys after the top-level
        # `jobs:` line. Slice from there so `on:`/`concurrency:` sub-keys
        # (also 2-indented) are not mistaken for jobs.
        jobs_idx = self.lines.index("jobs:")
        job_headers = [
            ln
            for ln in self.lines[jobs_idx + 1 :]
            if re.match(r"^  \w[\w-]*:$", ln)
        ]
        self.assertEqual(
            job_headers,
            ["  evaluate:", "  record:", "  notify:"],
            "expected exactly the evaluate/record/notify jobs",
        )
        self.assertNotIn(
            "name: submission-source",
            self.text,
            "the submission source must never be uploaded as an artifact",
        )

    def test_both_checkouts_disable_persisted_credentials(self) -> None:
        # The submissions checkout and the lean-eval checkout share the
        # runner with the untrusted build; neither may leave an
        # authenticated remote in .git/config.
        checkout_uses = self.text.count(
            "uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
        )
        self.assertGreaterEqual(checkout_uses, 2, "expected >=2 checkout steps")
        # The two evaluate-job checkouts must each set persist-credentials:false.
        self.assertGreaterEqual(
            self.text.count("persist-credentials: false"),
            2,
            "both evaluate-job checkouts must set persist-credentials: false",
        )

    def test_git_state_stripped_from_both_checkouts(self) -> None:
        self.assertIn(
            "rm -rf .git lean-eval/.git",
            self.text,
            "must strip .git from BOTH the submissions and lean-eval checkouts",
        )

    def test_benchmark_commit_comes_from_lean_eval_head(self) -> None:
        self.assertIn(
            'echo "sha=$(git -C lean-eval rev-parse HEAD)" >> "$GITHUB_OUTPUT"',
            self.text,
            "benchmark_commit must be the resolved leanprover/lean-eval@main HEAD",
        )
        self.assertNotRegex(
            self.text,
            r"--benchmark-commit\s+.*github\.sha",
            "benchmark_commit must NOT be github.sha (that is the submissions repo)",
        )

    def test_lean_eval_checked_out_at_main(self) -> None:
        self.assertIn("repository: leanprover/lean-eval", self.text)
        self.assertIn("path: lean-eval", self.text)

    def test_probes_run_from_the_lean_eval_checkout(self) -> None:
        self.assertIn(
            "python lean-eval/scripts/sandbox_engaged_probe.py", self.text
        )
        self.assertIn(
            "python lean-eval/scripts/security_probes/env_dump_probe.py", self.text
        )

    def test_app_token_is_step_scoped(self) -> None:
        # APP_INSTALLATION_TOKEN must appear only in the Fetch submission
        # step's env, never in a job-level env/secrets block.
        self.assertEqual(
            self.text.count("APP_INSTALLATION_TOKEN:"),
            1,
            "the app token must be set in exactly one (step-scoped) env block",
        )

    def test_record_uses_a_separate_results_store_checkout(self) -> None:
        # The push-retry loop resets origin/main; it must operate on a
        # checkout (`results-store/`) distinct from the one holding
        # update_leaderboard.py (`code/`), so the loop cannot reset the
        # running script out from under itself.
        self.assertIn("path: code", self.text)
        self.assertIn("path: results-store", self.text)
        self.assertIn(
            "python code/scripts/update_leaderboard.py", self.text
        )
        self.assertIn("--leaderboard-dir results-store", self.text)
        self.assertRegex(
            self.text,
            r"git -C results-store reset --hard origin/main",
            "the reset/push loop must target the results-store checkout",
        )

    def test_record_dispatches_leaderboard_redeploy(self) -> None:
        self.assertIn("event_type=results-advanced", self.text)
        self.assertIn(
            "repos/leanprover/lean-eval-leaderboard/dispatches", self.text
        )


if __name__ == "__main__":
    unittest.main()
