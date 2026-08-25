from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github/workflows/historical-authoritative-replay.yml"
).read_text(encoding="utf-8")


class HistoricalAuthoritativeReplayWorkflowTests(unittest.TestCase):
    def test_lane_is_manual_serialized_and_still_dark(self) -> None:
        self.assertIn("workflow_dispatch:", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertIn("environment: replay-production", WORKFLOW)
        self.assertIn(
            "vars.HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED == 'true'",
            WORKFLOW,
        )
        self.assertNotIn("schedule:", WORKFLOW)

    def test_recovery_and_exact_state_cas_precede_execution(self) -> None:
        validation = WORKFLOW.index("state/scripts/state.py --root state validate")
        recovery = WORKFLOW.index("historical_replay_controller.py recover")
        planning = WORKFLOW.index("Plan the exact next qualified public task")
        started = WORKFLOW.index("state-event started")
        executor = WORKFLOW.index("build-executor-request")
        terminal = WORKFLOW.index("terminal-event")
        self.assertLess(validation, recovery)
        self.assertLess(recovery, planning)
        self.assertLess(planning, started)
        self.assertLess(started, executor)
        self.assertLess(executor, terminal)
        self.assertIn("--environment production", WORKFLOW)
        self.assertIn("--expected-head", WORKFLOW)
        self.assertIn("PRODUCTION_STATE_READ_KEY", WORKFLOW)
        self.assertIn("PRODUCTION_STATE_WRITE_KEY", WORKFLOW)

    def test_stale_recovery_is_gated_by_exact_durable_destruction(self) -> None:
        preflight = WORKFLOW.split(
            "Validate State and identify only one stale running attempt", 1
        )[1].split("Confirm exact stale sandbox destruction", 1)[0]
        cleanup = WORKFLOW.split(
            "Confirm exact stale sandbox destruction without State write authority", 1
        )[1].split("Publish one destruction-confirmed stale-runner recovery", 1)[0]
        publish = WORKFLOW.split(
            "Publish one destruction-confirmed stale-runner recovery and stop", 1
        )[1].split("Report a still-running controller", 1)[0]
        self.assertIn("cleanup_required", cleanup)
        self.assertIn("/historical-public-replay/cleanup", cleanup)
        self.assertIn("destruction: \"confirmed\"", cleanup)
        self.assertIn("--cleanup-confirmation", cleanup)
        self.assertNotIn("STATE_WRITE_KEY: ${{ secrets", cleanup)
        self.assertNotIn("CLOUDFLARE_API_TOKEN: ${{ secrets", cleanup)
        self.assertNotIn("jq .event", preflight)
        self.assertIn("steps.cleanup.outputs.kind == 'failed'", publish)

    def test_exact_profile_image_and_health_are_required_before_started(self) -> None:
        deploy = WORKFLOW.index("render-executor-config")
        health = WORKFLOW.index("historical executor health differs from the exact plan")
        started = WORKFLOW.index("state-event started")
        self.assertLess(deploy, health)
        self.assertLess(health, started)
        self.assertIn("historical_public_replay_enabled\": True", WORKFLOW)
        self.assertIn("replay_enabled\": False", WORKFLOW)
        self.assertIn("reviewed_execution_profile_digest", WORKFLOW)
        self.assertIn("reviewed_measurement_config_digest", WORKFLOW)
        self.assertIn("reviewed_vm_image_digest", WORKFLOW)

    def test_repository_and_gist_sources_use_distinct_exact_adapters(self) -> None:
        self.assertIn("https://github.com/$source_repository.git", WORKFLOW)
        self.assertIn("https://gist.github.com/$owner/$gist_id.git", WORKFLOW)
        self.assertIn("historical_public_runner.py prepare", WORKFLOW)
        self.assertIn("historical_public_gist_source_adapter.py prepare", WORKFLOW)
        self.assertIn("git show \"$authority_commit:$authority_path\"", WORKFLOW)
        self.assertIn("git show \"$qualification_commit:$qualification_path\"", WORKFLOW)

    def test_credential_scopes_are_separated_and_aws_is_absent(self) -> None:
        deployment = WORKFLOW.split(
            "Render and deploy only the exact qualified historical executor", 1
        )[1].split("Require the exact enabled historical executor", 1)[0]
        started = WORKFLOW.split(
            "Append replay.started before fetching public source", 1
        )[1].split("Fetch exact public source", 1)[0]
        executor = WORKFLOW.split(
            "Invoke the exact executor without Cloudflare or State write authority", 1
        )[1].split("Append the exact reported historical terminal outcome", 1)[0]
        self.assertIn("CLOUDFLARE_API_TOKEN", deployment)
        self.assertNotIn("STATE_WRITE_KEY", deployment)
        self.assertIn("STATE_WRITE_KEY", started)
        self.assertNotIn("CLOUDFLARE_API_TOKEN: ${{ secrets", started)
        self.assertIn('test -z "${STATE_WRITE_KEY:-}"', executor)
        self.assertIn('test -z "${CLOUDFLARE_API_TOKEN:-}"', executor)
        self.assertIn("id-token: write", WORKFLOW)
        self.assertNotIn("AWS_", WORKFLOW)

    def test_failure_and_cancellation_paths_are_bounded(self) -> None:
        self.assertIn("source_fetch_failed", WORKFLOW)
        self.assertIn("runner_start_failed", WORKFLOW)
        self.assertIn("runner_lost", WORKFLOW)
        self.assertIn("verdict_invalid", WORKFLOW)
        failure_step = WORKFLOW.split(
            "Fail a started attempt explicitly if orchestration did not finish", 1
        )[1].split("Confirm credentials remained separated", 1)[0]
        self.assertIn(
            "${{ !cancelled() && steps.started.outputs.appended == 'true' "
            "&& steps.terminal.outputs.appended != 'true' }}",
            failure_step,
        )
        self.assertNotIn("always()", failure_step)
        self.assertIn("timeout-minutes: 360", WORKFLOW)
        self.assertNotIn("actions/upload-artifact", WORKFLOW)
        self.assertIn("runner-loss-cleanup-confirmed", failure_step)

    def test_start_failure_phase_changes_only_after_exact_running_receipt(self) -> None:
        start_failed = WORKFLOW.index('echo runner_start_failed > "$RUNNER_TEMP/failure-reason"')
        status_accepted = WORKFLOW.index('test "$start_status" = 202')
        receipt_validated = WORKFLOW.index(
            '. == {schema_version: 1, replay_task_id: $task, attempt: $attempt, '
            'status: "running"}'
        )
        runner_lost = WORKFLOW.index('echo runner_lost > "$RUNNER_TEMP/failure-reason"')
        polling = WORKFLOW.index('while [ "$(date +%s)" -lt "$deadline" ]')
        self.assertLess(start_failed, status_accepted)
        self.assertLess(status_accepted, receipt_validated)
        self.assertLess(receipt_validated, runner_lost)
        self.assertLess(runner_lost, polling)

    def test_protected_main_oidc_is_bound_to_the_exact_historical_deployment(self) -> None:
        self.assertIn("test \"$GITHUB_REPOSITORY\" = leanprover/lean-eval-submissions", WORKFLOW)
        self.assertIn("test \"$GITHUB_REF\" = refs/heads/main", WORKFLOW)
        self.assertIn("test \"$GITHUB_REF_PROTECTED\" = true", WORKFLOW)
        self.assertIn('test "$branch" = "$GITHUB_SHA"', WORKFLOW)
        self.assertIn('--source-commit "$GITHUB_SHA"', WORKFLOW)
        self.assertIn("environment: replay-production", WORKFLOW)
        self.assertIn(
            "audience=lean-eval-historical-public-replay-production",
            WORKFLOW,
        )
        self.assertIn("$HISTORICAL_EXECUTOR_URL/status", WORKFLOW)

    def test_executor_uses_idempotent_start_and_status_polling(self) -> None:
        executor = WORKFLOW.split(
            "Invoke the exact executor without Cloudflare or State write authority", 1
        )[1].split("Append the exact reported historical terminal outcome", 1)[0]
        self.assertIn("executor-status-request.json", executor)
        self.assertIn('test "$start_status" = 202', executor)
        self.assertIn('$HISTORICAL_EXECUTOR_URL/status', executor)
        self.assertIn('if [ "$poll_status" = 202 ]', executor)
        self.assertIn("sandbox_destroy_failed", executor)
        self.assertIn("mint_oidc", executor)
        self.assertIn("HISTORICAL_REPLAY_JOB_STARTED_EPOCH", WORKFLOW)
        self.assertNotIn("--max-time 20400", executor)

    def test_actions_are_commit_pinned(self) -> None:
        pins = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", WORKFLOW)
        self.assertTrue(pins)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in pins))


if __name__ == "__main__":
    unittest.main()
