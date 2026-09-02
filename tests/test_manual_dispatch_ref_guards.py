from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def job(source: str, name: str) -> str:
    marker = f"  {name}:\n"
    start = source.index(marker) + len(marker)
    next_job = re.search(r"^  [A-Za-z0-9_-]+:\n", source[start:], re.MULTILINE)
    end = -1 if next_job is None else start + next_job.start()
    return source[start:] if end == -1 else source[start:end]


class ManualDispatchRefGuardTests(unittest.TestCase):
    def assert_permission_free_main_guard(self, source: str) -> None:
        authorization = job(source, "authorize-manual")
        self.assertIn("permissions: {}", authorization)
        self.assertIn("timeout-minutes: 1", authorization)
        self.assertIn(
            'test "$EVENT_REPOSITORY" = leanprover/lean-eval-submissions',
            authorization,
        )
        self.assertIn('test "$EVENT_REF" = refs/heads/main', authorization)
        self.assertNotIn("uses:", authorization)
        self.assertNotIn("secrets.", authorization)
        self.assertNotIn("environment:", authorization)

    def test_archive_migration_wrong_ref_fails_before_protected_job(self) -> None:
        source = workflow("migrate-archive-envelopes.yml")
        authorization = job(source, "authorize-manual")
        self.assertIn("contents: read", authorization)
        self.assertIn('test "$EVENT_REF" = refs/heads/main', authorization)
        self.assertIn('test "$EVENT_REF_PROTECTED" = true', authorization)
        self.assertNotIn(
            'test "$EXPECTED_WORKFLOW_COMMIT" = "$EVENT_SHA"', authorization
        )
        self.assertIn('[[ "$EXPECTED_WORKFLOW_COMMIT" =~', authorization)
        self.assertIn(
            "gh api repos/leanprover/lean-eval-submissions/branches/main",
            authorization,
        )
        self.assertNotIn("secrets.", authorization)
        self.assertNotIn("environment:", authorization)
        migration = job(source, "migrate")
        self.assertIn("needs: authorize-manual", migration)
        self.assertNotIn("if: github.ref == 'refs/heads/main'", migration)
        self.assertIn("environment: archive-migration-production", migration)

    def test_readiness_manual_wrong_ref_fails_without_blocking_schedule(self) -> None:
        source = workflow("lifecycle-readiness-monitor.yml")
        self.assert_permission_free_main_guard(source)
        authorization = job(source, "authorize-manual")
        self.assertIn("if: github.event_name == 'workflow_dispatch'", authorization)

        for name in ("verify", "alert"):
            guarded = job(source, name)
            self.assertIn("needs.authorize-manual.result == 'success'", guarded)
            self.assertIn("github.event_name == 'schedule'", guarded)
            self.assertIn("github.ref == 'refs/heads/main'", guarded)

        self.assertIn("needs: authorize-manual", job(source, "verify"))
        self.assertIn(
            "needs: [authorize-manual, verify]", job(source, "alert")
        )

    def test_no_manually_dispatchable_job_uses_a_silent_main_skip(self) -> None:
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            source = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^\s+workflow_dispatch:\s*$", source) is None:
                continue
            for match in re.finditer(
                r"(?m)^  ([A-Za-z0-9_-]+):\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
                source,
                re.DOTALL,
            ):
                if re.search(
                    r"(?m)^    if: github\.ref == 'refs/heads/main'\s*$",
                    match.group("body"),
                ):
                    offenders.append(f"{path.name}:{match.group(1)}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
