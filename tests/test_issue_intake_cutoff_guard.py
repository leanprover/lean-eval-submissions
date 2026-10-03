from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "issue_intake_cutoff_guard.sh"


class IssueIntakeCutoffGuardTests(unittest.TestCase):
    def run_guard(
        self,
        payload: dict[str, object],
        *,
        cutoff: str,
        attempt: str,
        require_allowed: str = "true",
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            fake_gh = pathlib.Path(directory) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "print(os.environ['FAKE_RUN_JSON'])\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o700)
            env = os.environ.copy()
            env.update(
                {
                    "FAKE_RUN_JSON": json.dumps(payload),
                    "GH_TOKEN": "test-token",
                    "ISSUE_INTAKE_CUTOFF": cutoff,
                    "ISSUE_INTAKE_REQUIRE_ALLOWED": require_allowed,
                    "PATH": f"{directory}:{env['PATH']}",
                    "REPOSITORY": "leanprover/lean-eval-submissions",
                    "RUN_ATTEMPT": attempt,
                    "RUN_ID": "1234",
                }
            )
            return subprocess.run(
                ["bash", str(GUARD)],
                check=False,
                capture_output=True,
                env=env,
                text=True,
            )

    @staticmethod
    def run_payload(*, attempt: int, started_at: str) -> dict[str, object]:
        return {
            "id": 1234,
            "repository": {"full_name": "leanprover/lean-eval-submissions"},
            "event": "issues",
            "run_attempt": attempt,
            "created_at": "2026-09-30T06:57:09Z",
            "run_started_at": started_at,
        }

    def test_post_cutoff_rerun_is_rejected_by_effect_guard(self) -> None:
        completed = self.run_guard(
            self.run_payload(attempt=2, started_at="2026-09-30T06:57:11Z"),
            cutoff="2026-09-30T06:57:10Z",
            attempt="2",
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            completed.stdout,
            "allowed=false\nreason=at_or_after_cutoff\n",
        )
        self.assertIn("at or after the selected cutoff", completed.stderr)

    def test_pre_cutoff_rerun_remains_allowed(self) -> None:
        completed = self.run_guard(
            self.run_payload(attempt=2, started_at="2026-09-30T06:57:09Z"),
            cutoff="2026-09-30T06:57:10Z",
            attempt="2",
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout,
            "allowed=true\nreason=before_cutoff\n",
        )

    def test_admission_can_report_denial_without_processing(self) -> None:
        completed = self.run_guard(
            self.run_payload(attempt=2, started_at="2026-09-30T06:57:11Z"),
            cutoff="2026-09-30T06:57:10Z",
            attempt="2",
            require_allowed="false",
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout,
            "allowed=false\nreason=at_or_after_cutoff\n",
        )

    def test_api_attempt_must_match_current_context(self) -> None:
        completed = self.run_guard(
            self.run_payload(attempt=1, started_at="2026-09-30T06:57:09Z"),
            cutoff="2026-09-30T06:57:10Z",
            attempt="2",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")

    def test_absent_cutoff_admits_without_run_api(self) -> None:
        completed = self.run_guard(
            {},
            cutoff="",
            attempt="1",
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout,
            "allowed=true\nreason=cutoff_absent\n",
        )


if __name__ == "__main__":
    unittest.main()
