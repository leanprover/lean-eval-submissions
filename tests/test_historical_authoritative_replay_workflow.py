from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github/workflows/historical-authoritative-replay.yml"
).read_text(encoding="utf-8")
PRIVATE_WORKFLOW = (
    ROOT / ".github/workflows/historical-private-replay.yml"
).read_text(encoding="utf-8")


class HistoricalAuthoritativeReplayWorkflowTests(unittest.TestCase):
    def test_lane_is_manual_serialized_and_still_dark(self) -> None:
        self.assertIn("workflow_dispatch:", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertIn("group: lean-eval-historical-replay-controller", WORKFLOW)
        self.assertIn("group: lean-eval-historical-replay-controller", PRIVATE_WORKFLOW)
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
        reservation = WORKFLOW.index(
            "Reserve exact cleanup identity before State can become running"
        )
        started = WORKFLOW.index("state-event started")
        self.assertLess(deploy, health)
        self.assertLess(health, reservation)
        self.assertLess(reservation, started)
        self.assertIn("/historical-public-replay/cleanup-reservation", WORKFLOW)
        self.assertIn('steps.reserve.outputs.reserved == \'true\'', WORKFLOW)
        self.assertIn("historical_public_replay_enabled\": True", WORKFLOW)
        self.assertIn("replay_enabled\": False", WORKFLOW)
        self.assertIn("reviewed_execution_profile_digest", WORKFLOW)
        self.assertIn("reviewed_measurement_config_digest", WORKFLOW)
        self.assertIn("reviewed_vm_image_digest", WORKFLOW)

    def test_production_rollout_uses_the_replay_owned_strict_helper(self) -> None:
        deployment = WORKFLOW.split(
            "Render and deploy only the exact qualified historical executor", 1
        )[1].split("Require the exact enabled historical executor", 1)[0]
        self.assertIn("scripts/wait_replay_container_rollout.py", deployment)
        self.assertIn("--environment production", deployment)
        self.assertIn("--image-family historical-public", deployment)
        self.assertIn(
            "--application "
            "lean-eval-historical-public-replay-replaysandbox-production",
            deployment,
        )
        self.assertNotIn("historical-public-qualification", deployment)

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
        reservation = WORKFLOW.split(
            "Reserve exact cleanup identity before State can become running", 1
        )[1].split("Append replay.started before fetching public source", 1)[0]
        started = WORKFLOW.split(
            "Append replay.started before fetching public source", 1
        )[1].split("Fetch exact public source", 1)[0]
        executor = WORKFLOW.split(
            "Invoke the exact executor without Cloudflare or State write authority", 1
        )[1].split("Append the exact reported historical terminal outcome", 1)[0]
        self.assertIn("CLOUDFLARE_API_TOKEN", deployment)
        self.assertNotIn("STATE_WRITE_KEY", deployment)
        self.assertIn('test -z "${STATE_WRITE_KEY:-}"', reservation)
        self.assertNotIn("STATE_WRITE_KEY: ${{ secrets", reservation)
        self.assertNotIn("CLOUDFLARE_API_TOKEN: ${{ secrets", reservation)
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
        self.assertIn("!cancelled()", failure_step)
        self.assertIn("steps.started.outputs.appended == 'true'", failure_step)
        self.assertIn("steps.execute.outputs.ready != 'true'", failure_step)
        self.assertIn("steps.terminal.outputs.appended != 'true'", failure_step)
        self.assertNotIn("always()", failure_step)
        self.assertIn("timeout-minutes: 360", WORKFLOW)
        self.assertNotIn("actions/upload-artifact", WORKFLOW)
        self.assertIn("runner-loss-cleanup-confirmed", failure_step)

    def test_valid_verdict_cas_exhaustion_is_left_for_retryable_recovery(self) -> None:
        terminal = WORKFLOW.split(
            "Append the exact reported historical terminal outcome", 1
        )[1].split(
            "Fail a started attempt explicitly if orchestration did not finish", 1
        )[0]
        failure = WORKFLOW.split(
            "Fail a started attempt explicitly if orchestration did not finish", 1
        )[1].split("Confirm credentials remained separated", 1)[0]
        self.assertIn('test "$published" = true', terminal)
        self.assertIn("steps.execute.outputs.ready != 'true'", failure)
        self.assertIn('failure_reason=$(cat "$RUNNER_TEMP/failure-reason")', failure)
        self.assertNotIn("steps.execute.outputs.ready == 'true'", failure)

    def test_terminal_appends_refresh_and_retry_exact_current_state(self) -> None:
        terminal = WORKFLOW.split(
            "Append the exact reported historical terminal outcome", 1
        )[1].split(
            "Fail a started attempt explicitly if orchestration did not finish", 1
        )[0]
        failure = WORKFLOW.split(
            "Fail a started attempt explicitly if orchestration did not finish", 1
        )[1].split("Confirm credentials remained separated", 1)[0]
        for append_step, label in ((terminal, "terminal"), (failure, "failure")):
            self.assertIn("for cas_attempt in $(seq 1 4)", append_step)
            self.assertIn("git -C state fetch --no-tags origin", append_step)
            self.assertIn("git -C state merge --ff-only", append_step)
            self.assertIn("state/scripts/state.py --root state validate", append_step)
            self.assertIn("historical_replay_controller.py recover", append_step)
            self.assertIn("started_event_id: $started", append_step)
            self.assertIn("cmp \"$RUNNER_TEMP/started-event.json\"", append_step)
            self.assertIn(
                f'historical-{label}-state-publish-$cas_attempt', append_step
            )
            self.assertIn("git clone --quiet --no-hardlinks state", append_step)
            self.assertIn('--expected-head "$expected"', append_step)
            self.assertIn("cmp -s \"$last_event\"", append_step)
            self.assertGreaterEqual(
                append_step.count("state/scripts/state.py --root state validate"), 2
            )
            self.assertIn('merge-base --is-ancestor "$state_head"', append_step)
            self.assertIn('test "$(jq -er .kind "$committed")" = none', append_step)
            self.assertNotIn("EXPECTED_STATE_HEAD", append_step)

    def test_public_state_refresh_identity_is_temporary_and_read_only(self) -> None:
        install = WORKFLOW.split(
            "Install one temporary read-only State refresh identity", 1
        )[1].split("actions/setup-node", 1)[0]
        cleanup = WORKFLOW.split(
            "Confirm credentials remained separated and remove scratch", 1
        )[1]
        self.assertIn("PRODUCTION_STATE_READ_KEY", install)
        self.assertNotIn("PRODUCTION_STATE_WRITE_KEY", install)
        self.assertIn("historical-public-state-reader", cleanup)
        self.assertIn("historical-public-state-known-hosts", cleanup)
        self.assertIn("config --unset-all core.sshCommand", cleanup)

    def test_start_becomes_cleanup_required_before_the_request_can_escape(self) -> None:
        start_failed = WORKFLOW.index(
            'echo runner_start_failed > "$RUNNER_TEMP/failure-reason"'
        )
        runner_lost = WORKFLOW.index('echo runner_lost > "$RUNNER_TEMP/failure-reason"')
        attempted = WORKFLOW.index('touch "$RUNNER_TEMP/executor-start-attempted"')
        start_request = WORKFLOW.index("start_status=$(curl")
        status_accepted = WORKFLOW.index('test "$start_status" = 202')
        receipt_validated = WORKFLOW.index(
            '. == {schema_version: 1, replay_task_id: $task, attempt: $attempt, '
            'status: "running"}'
        )
        polling = WORKFLOW.index('while [ "$(date +%s)" -lt "$deadline" ]')
        self.assertLess(start_failed, runner_lost)
        self.assertLess(runner_lost, attempted)
        self.assertLess(attempted, start_request)
        self.assertLess(start_request, status_accepted)
        self.assertLess(status_accepted, receipt_validated)
        self.assertLess(receipt_validated, polling)

    def test_lost_start_response_requires_cleanup_before_state_failure(self) -> None:
        attempted = WORKFLOW.index('touch "$RUNNER_TEMP/executor-start-attempted"')
        start_request = WORKFLOW.index("start_status=$(curl")
        cleanup = WORKFLOW.index(
            "Confirm exact cleanup after any ambiguous executor start"
        )
        state_failure = WORKFLOW.index(
            "Fail a started attempt explicitly if orchestration did not finish"
        )
        self.assertLess(attempted, start_request)
        self.assertLess(start_request, cleanup)
        self.assertLess(cleanup, state_failure)
        cleanup_step = WORKFLOW.split(
            "Confirm exact cleanup after any ambiguous executor start", 1
        )[1].split("Append the exact reported historical terminal outcome", 1)[0]
        self.assertIn("steps.execute.outputs.ready != 'true'", cleanup_step)
        self.assertIn("runner-loss-cleanup-confirmed", cleanup_step)
        self.assertIn('destruction: "confirmed"', cleanup_step)
        self.assertNotIn("STATE_WRITE_KEY: ${{ secrets", cleanup_step)

    def test_timed_out_or_rejected_start_cannot_publish_without_cleanup(self) -> None:
        start_request = WORKFLOW.index("start_status=$(curl")
        status_rejected = WORKFLOW.index('test "$start_status" = 202')
        cleanup = WORKFLOW.index(
            "Confirm exact cleanup after any ambiguous executor start"
        )
        self.assertLess(start_request, status_rejected)
        self.assertLess(status_rejected, cleanup)
        failure_step = WORKFLOW.split(
            "Fail a started attempt explicitly if orchestration did not finish", 1
        )[1].split("Confirm credentials remained separated", 1)[0]
        self.assertIn(
            'if [ -f "$RUNNER_TEMP/executor-start-attempted" ]', failure_step
        )
        self.assertIn('test "$AMBIGUOUS_CLEANUP_KIND" = confirmed', failure_step)
        self.assertIn(
            'test -f "$RUNNER_TEMP/runner-loss-cleanup-confirmed"', failure_step
        )

    def test_malformed_start_receipt_cannot_bypass_cleanup(self) -> None:
        status_accepted = WORKFLOW.index('test "$start_status" = 202')
        receipt_validated = WORKFLOW.index(
            '. == {schema_version: 1, replay_task_id: $task, attempt: $attempt, '
            'status: "running"}'
        )
        cleanup = WORKFLOW.index(
            "Confirm exact cleanup after any ambiguous executor start"
        )
        self.assertLess(status_accepted, receipt_validated)
        self.assertLess(receipt_validated, cleanup)
        self.assertIn(
            '> "$RUNNER_TEMP/runner-loss-cleanup-request.json"',
            WORKFLOW[:status_accepted],
        )

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
