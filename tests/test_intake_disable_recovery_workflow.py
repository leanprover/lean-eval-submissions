import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "intake-disable-recovery.yml"
).read_text(encoding="utf-8")


def job(name: str) -> str:
    marker = f"  {name}:\n"
    start = WORKFLOW.index(marker) + len(marker)
    next_job = re.search(r"^  [A-Za-z0-9_-]+:\n", WORKFLOW[start:], re.MULTILINE)
    end = -1 if next_job is None else start + next_job.start()
    return WORKFLOW[start:] if end == -1 else WORKFLOW[start:end]


class IntakeDisableRecoveryWorkflowTests(unittest.TestCase):
    def test_manual_authorization_runs_without_privileges_or_secrets(self) -> None:
        authorization = job("authorize-manual")
        self.assertIn("if: github.event_name == 'workflow_dispatch'", authorization)
        self.assertIn("permissions: {}", authorization)
        self.assertIn("timeout-minutes: 1", authorization)
        self.assertIn("working-directory: .", authorization)
        self.assertIn(
            'test "$EVENT_REPOSITORY" = leanprover/lean-eval-submissions',
            authorization,
        )
        self.assertIn('test "$EVENT_REF" = refs/heads/main', authorization)
        self.assertNotIn("uses:", authorization)
        self.assertNotIn("secrets.", authorization)

    def test_manual_wrong_ref_fails_before_recovery_can_run(self) -> None:
        recovery = job("disable-production-intake")
        self.assertIn("needs: authorize-manual", recovery)
        self.assertIn("always() &&", recovery)
        self.assertIn(
            "github.event_name == 'workflow_dispatch' &&\n"
            "      needs.authorize-manual.result == 'success'",
            recovery,
        )
        self.assertNotIn("github.ref == 'refs/heads/main'", recovery)

    def test_failed_automatic_controller_bypasses_manual_authorization(self) -> None:
        recovery = job("disable-production-intake")
        self.assertIn("github.event_name == 'workflow_run'", recovery)
        self.assertIn(
            "github.event.workflow_run.conclusion != 'success'", recovery
        )
        self.assertIn("github.event.workflow_run.head_branch == 'main'", recovery)
        self.assertIn(
            "github.event.workflow_run.head_repository.full_name == github.repository",
            recovery,
        )
        self.assertIn("github.event.workflow_run.event == 'push'", recovery)
        self.assertIn(
            "github.event.workflow_run.event == 'workflow_dispatch'", recovery
        )

    def test_recovery_remains_production_disable_only(self) -> None:
        recovery = job("disable-production-intake")
        self.assertIn("environment: cloudflare-production", recovery)
        self.assertIn("ref: main", recovery)
        self.assertIn('--var "INTAKE_ENABLED:false"', recovery)
        self.assertIn('--var "INTAKE_ENABLEMENT_MODE:disabled"', recovery)
        self.assertIn("--require-intake-disabled", recovery)
        self.assertNotIn("INTAKE_ENABLED:true", recovery)


if __name__ == "__main__":
    unittest.main()
