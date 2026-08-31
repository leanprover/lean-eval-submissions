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
        recovery = job("disable-production-launch-gates")
        self.assertIn("needs: authorize-manual", recovery)
        self.assertIn("always() &&", recovery)
        self.assertIn(
            "github.event_name == 'workflow_dispatch' &&\n"
            "      needs.authorize-manual.result == 'success'",
            recovery,
        )
        self.assertNotIn("github.ref == 'refs/heads/main'", recovery)

    def test_failed_automatic_controller_bypasses_manual_authorization(self) -> None:
        recovery = job("disable-production-launch-gates")
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
        for field in ("run_attempt", "head_sha", "conclusion", "id"):
            with self.subTest(field=field):
                self.assertIn(f"github.event.workflow_run.{field}", recovery)
        self.assertIn(
            'actions/runs/$EVENT_RUN_ID/attempts/$EVENT_ATTEMPT', recovery
        )
        self.assertIn("automatic recovery trigger read-back differs", recovery)

    def test_recovery_is_bound_to_protected_main_exact_controller_and_tag(self) -> None:
        recovery = job("disable-production-launch-gates")
        self.assertIn(
            "runs?branch=main&head_sha=$LIVE_COMMIT&per_page=100", recovery
        )
        self.assertIn("launch-recovery-source", recovery)
        self.assertIn("--expected-commit \"$TARGET_COMMIT\"", recovery)
        self.assertIn('cmp --silent "$plan_dir/source.json"', recovery)
        self.assertIn("controller commit is not reachable from protected main", recovery)
        self.assertIn(
            'git/ref/tags/lean-eval-dispatch/$commit', recovery
        )
        self.assertIn('actions/runs/$run_id/attempts/$run_attempt', recovery)
        self.assertIn("controller dispatch tag does not resolve exactly", recovery)
        self.assertIn('[ "$commit" != "$LIVE_COMMIT" ]', recovery)

    def test_recovery_remains_production_all_launch_gates_disable_only(self) -> None:
        recovery = job("disable-production-launch-gates")
        self.assertIn("environment: cloudflare-production", recovery)
        self.assertIn("ref: main", recovery)
        self.assertIn('--var "INTAKE_ENABLED:false"', recovery)
        self.assertIn('--var "INTAKE_ENABLEMENT_MODE:disabled"', recovery)
        self.assertIn("--require-intake-disabled", recovery)
        self.assertIn("--require-launch-gates-disabled", recovery)
        for variable in (
            "LEGACY_RESULT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_MAINTAINER_API_ENABLED",
            "MODEL_IDENTITY_OWNER_API_ENABLED",
            "MODEL_IDENTITY_MAINTAINER_API_ENABLED",
            "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED",
            "RELEASE_OPT_IN_API_ENABLED",
            "RELEASE_OPT_OUT_API_ENABLED",
            "PROMOTION_CANARY_ENABLED",
        ):
            with self.subTest(variable=variable):
                self.assertIn(f'--var "{variable}:false"', recovery)
        self.assertIn("--var 'RESULT_AMENDMENT_MAINTAINERS:[]'", recovery)
        self.assertIn("--var 'MODEL_IDENTITY_MAINTAINERS:[]'", recovery)
        self.assertNotIn("INTAKE_ENABLED:true", recovery)
        self.assertNotRegex(recovery, r"_API_ENABLED:true")

    def test_recovery_arms_from_active_version_when_health_is_unavailable(self) -> None:
        recovery = job("disable-production-launch-gates")
        self.assertIn("worker_intake_configuration.py", recovery)
        self.assertIn("worker_lifecycle_configuration.py", recovery)
        self.assertGreaterEqual(recovery.count("deployments status"), 3)
        self.assertGreaterEqual(recovery.count("versions view"), 2)
        self.assertEqual(recovery.count("launch-recovery-source"), 2)
        self.assertIn("pre-mutation health unavailable", recovery)
        unavailable = recovery.index("pre-mutation health unavailable")
        self.assertIn("needed=true", recovery[unavailable:unavailable + 180])
        invalid = recovery.index("pre-mutation health was not exact")
        self.assertIn("needed=true", recovery[invalid:invalid + 180])
        self.assertNotIn("Prove and inspect this controller", recovery)
        mutation = recovery.index(
            "Force the exact controller code to all-false launch mode"
        )
        self.assertIn("if: steps.active.outputs.needed == 'true'", recovery[mutation:])
        self.assertIn("--target-version \"$EXPECTED_ACTIVE_VERSION\"", recovery[mutation:])
        self.assertNotIn("steps.target.outputs.needed", recovery)
        self.assertNotIn("steps.live.outputs.needed", recovery)


if __name__ == "__main__":
    unittest.main()
