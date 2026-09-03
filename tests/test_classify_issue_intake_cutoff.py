from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from classify_issue_intake_cutoff import CutoffError, classify, timestamp


class IssueIntakeCutoffTests(unittest.TestCase):
    def test_pre_cutoff_run_is_admitted(self) -> None:
        self.assertEqual(
            classify("2026-09-30T06:57:09Z", "2026-09-30T06:57:10Z"),
            (True, "before_cutoff"),
        )

    def test_run_at_cutoff_is_frozen(self) -> None:
        self.assertEqual(
            classify("2026-09-30T06:57:10Z", "2026-09-30T06:57:10Z"),
            (False, "at_or_after_cutoff"),
        )

    def test_post_cutoff_run_is_frozen(self) -> None:
        self.assertEqual(
            classify("2026-09-30T06:57:11Z", "2026-09-30T06:57:10Z"),
            (False, "at_or_after_cutoff"),
        )

    def test_invalid_or_noncanonical_timestamps_fail_closed(self) -> None:
        for value in (
            "",
            "2026-09-30T06:57:10+00:00",
            "2026-09-30T06:57:10.000Z",
            "2026-09-31T06:57:10Z",
        ):
            with self.subTest(value=value), self.assertRaises(CutoffError):
                timestamp(value, "test timestamp")

    def test_cli_emits_only_bounded_outputs(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "classify_issue_intake_cutoff.py"),
                "--run-created-at",
                "2026-09-30T06:57:10Z",
                "--cutoff",
                "2026-09-30T06:57:10Z",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.stdout,
            "allowed=false\nreason=at_or_after_cutoff\n",
        )
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
