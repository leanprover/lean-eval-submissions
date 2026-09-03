from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github/workflows/historical-private-replay.yml"
).read_text(encoding="utf-8")
ENTRY = (
    ROOT / "server/src/historical-private-replay-entry.ts"
).read_text(encoding="utf-8")
CONTROLLER = (
    ROOT / "scripts/historical_private_replay_controller.py"
).read_text(encoding="utf-8")
RESOURCE_DELETE = (
    ROOT / "scripts/delete_historical_private_executor"
).read_text(encoding="utf-8")
OWNERSHIP = (
    ROOT / "scripts/verify_historical_private_executor_ownership.py"
).read_text(encoding="utf-8")


def step(name: str, following_name: str) -> str:
    return WORKFLOW.split(f"- name: {name}", 1)[1].split(
        f"- name: {following_name}", 1
    )[0]


class HistoricalPrivateReplayWorkflowTests(unittest.TestCase):
    def test_lane_is_manual_dark_serialized_and_temporary(self) -> None:
        self.assertIn("workflow_dispatch:", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertIn("environment: replay-production", WORKFLOW)
        self.assertIn(
            "vars.HISTORICAL_PRIVATE_REPLAY_CONTROLLER_ENABLED == 'true'",
            WORKFLOW,
        )
        self.assertIn("Temporary migration machinery. Delete this workflow", WORKFLOW)
        self.assertIn("queue is empty, State proves", WORKFLOW)
        self.assertIn("no `hpr-` Worker or `le-hpr-` application remains", WORKFLOW)

    def test_replenishment_is_bounded_and_has_separate_permissions(self) -> None:
        replay, replenish = WORKFLOW.split("  replenish-private-lane:", 1)
        self.assertIn("permissions:\n      contents: read\n      id-token: write", replay)
        self.assertNotIn("actions: write", replay)
        self.assertIn("permissions:\n      actions: write", replenish)
        self.assertIn("contents: read", replenish)
        self.assertNotIn("environment: replay-production", replenish)
        self.assertNotIn("secrets.", replenish)
        self.assertNotIn("actions/checkout", replenish)
        self.assertIn("needs.replay-one.result == 'success'", replenish)
        self.assertIn("!cancelled()", replenish)
        self.assertIn("inputs.remaining_runs > 1", replenish)
        self.assertIn('test "$REMAINING_RUNS" -le 1024', replenish)
        self.assertIn("needs.replay-one.outputs.preflight_kind != 'busy'", replenish)
        self.assertIn("needs.replay-one.outputs.plan_kind != 'empty'", replenish)
        self.assertIn("REVIEWED_IMPLEMENTATION_COMMIT", replenish)
        self.assertIn(".merge_base_commit.sha == $base", replenish)
        self.assertIn(".ahead_by == (.commits | length)", replenish)
        self.assertIn("(.files | length) < 300", replenish)
        self.assertIn('test("^results/', replenish)
        self.assertEqual(replenish.count("actions/workflows/"), 1)
        self.assertIn(
            "actions/workflows/historical-private-replay.yml/dispatches",
            replenish,
        )

    def test_only_exact_protected_main_can_run(self) -> None:
        preflight = step(
            "Require exact protected main and the explicit dark gate",
            "Validate protected checkouts and identify one running attempt",
        )
        self.assertIn(
            'test "$GITHUB_REPOSITORY" = leanprover/lean-eval-submissions',
            preflight,
        )
        self.assertIn('test "$GITHUB_REF" = refs/heads/main', preflight)
        self.assertIn('test "$GITHUB_REF_PROTECTED" = true', preflight)
        self.assertIn('test "$branch" = "$GITHUB_SHA"', preflight)
        self.assertIn("fetch-depth: 0", WORKFLOW)
        self.assertIn("persist-credentials: false", WORKFLOW)

    def test_root_cleanliness_excludes_only_validated_nested_checkouts(self) -> None:
        validate = step(
            "Validate protected checkouts and identify one running attempt",
            "Confirm destruction before recovering an abandoned attempt",
        )
        self.assertIn("$'?? audit/\\n?? state/'", validate)
        self.assertIn("git rev-parse --git-path info/exclude", validate)
        self.assertIn("test ! -L", validate)
        self.assertIn("printf '%s\\n' /audit/ /state/", validate)
        self.assertIn("--untracked-files=all", validate)
        self.assertIn('test -z "$(git -C state status --short)"', validate)
        self.assertIn('test -z "$(git -C audit status --short)"', validate)
        self.assertNotIn('test -z "$(git status --short)"', validate)
        self.assertNotIn("--untracked-files=no", validate)

    def test_state_is_validated_materialized_and_cas_published(self) -> None:
        validate = WORKFLOW.index("state/scripts/state.py --root state validate")
        materialize = WORKFLOW.index("state/scripts/state.py --root state materialize")
        plan = WORKFLOW.index("Plan exactly one qualified private task")
        reserve = WORKFLOW.index("Reserve cleanup before State can become running")
        started = WORKFLOW.index("Append the exact replay.started candidate with CAS")
        self.assertLess(validate, materialize)
        self.assertLess(materialize, plan)
        self.assertLess(plan, reserve)
        self.assertLess(reserve, started)
        self.assertGreaterEqual(WORKFLOW.count("--expected-head"), 4)
        self.assertGreaterEqual(WORKFLOW.count("state_head=$(scripts/publish_replay_state_event"), 4)
        self.assertGreaterEqual(WORKFLOW.count("git clone --quiet --no-hardlinks state"), 4)
        self.assertGreaterEqual(WORKFLOW.count("for cas_attempt in $(seq 1 4)"), 4)
        self.assertGreaterEqual(WORKFLOW.count("refresh-prove-running"), 8)
        self.assertNotIn("EXPECTED_STATE_HEAD", WORKFLOW)
        self.assertNotIn("STARTED_STATE_HEAD", WORKFLOW)

    def test_each_append_refreshes_and_proves_its_exact_event_before_teardown(self) -> None:
        recovery = step(
            "Publish only a destruction-confirmed abandoned-run recovery",
            "Delete a recovered task's temporary executor",
        )
        started = step(
            "Append the exact replay.started candidate with CAS",
            "Use the exact one-use KMS unwrap capability",
        )
        terminal = step(
            "Append the exact reported terminal outcome",
            "Attempt an explicit failure terminal after safe cleanup",
        )
        failure = step(
            "Attempt an explicit failure terminal after safe cleanup",
            "Write one redacted source-free terminal artifact",
        )
        for append_step, proof_command in (
            (recovery, "refresh-verify-recovery"),
            (started, "refresh-prove-running"),
            (terminal, "refresh-verify-terminal"),
            (failure, "refresh-verify-terminal"),
        ):
            publish = append_step.index("publish_replay_state_event")
            proof = append_step.index(proof_command, publish)
            ancestry = append_step.index("merge-base --is-ancestor", proof)
            self.assertLess(publish, proof)
            self.assertLess(proof, ancestry)
        recovered_delete = WORKFLOW.index(
            "Delete a recovered task's temporary executor"
        )
        self.assertLess(
            WORKFLOW.index("recovery-committed-proof.json"), recovered_delete
        )

    def test_abandoned_recovery_requires_exact_destruction_first(self) -> None:
        cleanup = WORKFLOW.index(
            "Confirm destruction before recovering an abandoned attempt"
        )
        publish = WORKFLOW.index(
            "Publish only a destruction-confirmed abandoned-run recovery"
        )
        self.assertLess(cleanup, publish)
        cleanup_step = step(
            "Confirm destruction before recovering an abandoned attempt",
            "Publish only a destruction-confirmed abandoned-run recovery",
        )
        publish_step = step(
            "Publish only a destruction-confirmed abandoned-run recovery",
            "Delete a recovered task's temporary executor",
        )
        self.assertIn('destruction:"confirmed"', cleanup_step)
        self.assertNotIn("STATE_WRITE_KEY: ${{ secrets", cleanup_step)
        self.assertNotIn("CLOUDFLARE_API_TOKEN: ${{ secrets", cleanup_step)
        self.assertIn("--cleanup-confirmation", publish_step)

    def test_exact_qualified_digest_only_executor_precedes_start(self) -> None:
        render = WORKFLOW.index("render-executor-config")
        deploy = WORKFLOW.index("wrangler deploy --config")
        deployment = step(
            "Deploy only the preflight-cleared exact task executor",
            "Reserve cleanup before State can become running",
        )
        health = WORKFLOW.index(
            "reviewed_vm_image_digest == $image",
            WORKFLOW.index("Deploy only the preflight-cleared exact task executor"),
        )
        reserve = WORKFLOW.index("Reserve cleanup before State can become running")
        started = WORKFLOW.index("Append the exact replay.started candidate with CAS")
        self.assertLess(render, deploy)
        self.assertLess(deploy, health)
        self.assertLess(health, reserve)
        self.assertLess(reserve, started)
        self.assertIn('profile["registry_repository"] != "lean-eval-authoritative"', CONTROLLER)
        self.assertIn('f"{profile[\'registry_repository\']}@{manifest}"', CONTROLLER)
        self.assertIn('profile["qualification_status"] != "qualified"', CONTROLLER)
        preflight = step(
            "Render and preflight only the exact qualified task executor",
            "Deploy only the preflight-cleared exact task executor",
        )
        self.assertIn("worker-preflight.json", preflight)
        self.assertIn("container-applications-before.json", preflight)
        self.assertIn(".containers[0].name", preflight)
        self.assertNotIn("wrangler deploy", preflight)
        self.assertIn("steps.resource_preflight.outputs.cleared == 'true'", deployment)
        self.assertLess(
            deployment.index("executor-deploy-attempted"),
            deployment.index("wrangler deploy"),
        )

    def test_only_official_lean_and_nanoda_checker_profile_is_accepted(self) -> None:
        planning = step(
            "Plan exactly one qualified private task",
            "Render and preflight only the exact qualified task executor",
        )
        self.assertIn(".task.checker", planning)
        self.assertIn("^leanprover/lean4:v[0-9]", planning)
        self.assertIn(
            '["comparator", "landrun", "lean4export", "nanoda"]',
            planning,
        )

    def test_server_requires_reservation_and_exact_task_attempt(self) -> None:
        self.assertIn("override enableInternet = false", ENTRY)
        self.assertIn("claimReservedBinding", ENTRY)
        self.assertIn("EXPECTED_REPLAY_TASK_ID", ENTRY)
        self.assertIn("EXPECTED_REPLAY_ATTEMPT", ENTRY)
        self.assertIn("(identity.attempt as number) > 4", ENTRY)
        reserve = ENTRY.index('key.endsWith("/reserve")')
        start = ENTRY.index("return handleReplayRequest")
        self.assertLess(reserve, start)

    def test_every_executor_boundary_is_surrounded_by_running_proofs(self) -> None:
        execute = step(
            "Invoke and poll the exact blocked-network executor",
            "Confirm exact sandbox destruction after every attempted start",
        )
        self.assertIn("prove_running\n          mint_oidc", execute)
        self.assertIn("test \"$status\" = 202", execute)
        self.assertGreaterEqual(execute.count("prove_running"), 6)
        self.assertRegex(
            execute,
            r"status=\$\(curl[\s\S]+?unset token\n\s+prove_running\n\s+if \[ \"\$status\" = 202 \]",
        )
        cleanup = step(
            "Confirm exact sandbox destruction after every attempted start",
            "Derive the exact terminal candidate after destruction",
        )
        self.assertIn("running-before-cleanup.json", cleanup)
        self.assertIn("running-after-cleanup.json", cleanup)

    def test_kms_capability_is_exact_one_use_and_dropped_before_executor(self) -> None:
        preflight = step(
            "Require exact protected main and the explicit dark gate",
            "Validate protected checkouts and identify one running attempt",
        )
        self.assertIn(
            "arn:aws:iam::161072922960:role/lean-eval-replay-unwrap-invoker-production",
            preflight,
        )
        self.assertIn("capability.max_uses", WORKFLOW)
        self.assertEqual(
            WORKFLOW.count("aws lambda invoke --function-name lean-eval-archive-unwrap-production"),
            2,
        )
        self.assertIn("validate-reuse-failure", WORKFLOW)
        build = step(
            "Build the credential-free exact executor request",
            "Invoke and poll the exact blocked-network executor",
        )
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            self.assertIn(f'test -z "${{{name}:-}}"', build)
        execute = step(
            "Invoke and poll the exact blocked-network executor",
            "Confirm exact sandbox destruction after every attempted start",
        )
        self.assertNotIn("secrets.AWS", execute)
        self.assertNotIn("AWS_REPLAY_ROLE", execute)

    def test_cleanup_precedes_all_terminal_state_and_terminal_is_always_attempted(self) -> None:
        cleanup = WORKFLOW.index(
            "Confirm exact sandbox destruction after every attempted start"
        )
        success = WORKFLOW.index("Append the exact reported terminal outcome")
        failure = WORKFLOW.index("Attempt an explicit failure terminal after safe cleanup")
        self.assertLess(cleanup, success)
        self.assertLess(cleanup, failure)
        self.assertIn("if: always() && steps.started.outputs.appended == 'true'", WORKFLOW)
        failure_step = step(
            "Attempt an explicit failure terminal after safe cleanup",
            "Write one redacted source-free terminal artifact",
        )
        self.assertIn("steps.cleanup.outputs.confirmed == 'true'", failure_step)
        self.assertIn("terminal-candidate", failure_step)
        self.assertIn("publish_replay_state_event", failure_step)

    def test_private_material_and_temporary_refs_are_destroyed(self) -> None:
        cleanup = WORKFLOW.split(
            "- name: Destroy all private material, credentials, and temporary refs",
            1,
        )[1]
        for value in (
            "archive-file-key",
            "unwrap-request.json",
            "unwrap-response.json",
            "unwrap-metadata.json",
            "executor-request.json",
        ):
            self.assertIn(value, cleanup)
        self.assertIn("shred --remove", cleanup)
        self.assertIn("historical-private-state-reader", cleanup)
        self.assertIn("historical-private-state-known-hosts", cleanup)
        self.assertIn("config --unset-all core.sshCommand", cleanup)
        self.assertIn("rm -rf audit state", cleanup)
        self.assertIn("test ! -e \"$RUNNER_TEMP/archive-plaintext.tar\"", cleanup)
        build = step(
            "Build the credential-free exact executor request",
            "Invoke and poll the exact blocked-network executor",
        )
        self.assertGreaterEqual(build.count("shred --remove"), 2)
        execute = step(
            "Invoke and poll the exact blocked-network executor",
            "Confirm exact sandbox destruction after every attempted start",
        )
        self.assertIn(
            'shred --remove "$RUNNER_TEMP/executor-request.json"',
            execute,
        )

    def test_worker_and_container_app_are_verified_absent_after_safe_delete(self) -> None:
        self.assertIn("wrangler delete", RESOURCE_DELETE)
        self.assertIn("workers/services/$worker", RESOURCE_DELETE)
        self.assertIn("wrangler containers list --json", RESOURCE_DELETE)
        self.assertIn("wrangler containers delete", RESOURCE_DELETE)
        self.assertIn('test "$worker_absent" = true', RESOURCE_DELETE)
        self.assertIn('test "$application_absent" = true', RESOURCE_DELETE)
        terminal_delete = step(
            "Delete the terminal task's temporary executor",
            "Delete an executor whose State start never committed",
        )
        self.assertIn("always()", terminal_delete)
        self.assertIn("delete_historical_private_executor", terminal_delete)
        self.assertIn("steps.cleanup.outputs.confirmed", WORKFLOW[: WORKFLOW.index(
            "Delete the terminal task's temporary executor"
        )])

    def test_only_exact_owned_orphans_and_this_runs_deploy_are_deleted(self) -> None:
        preflight = step(
            "Render and preflight only the exact qualified task executor",
            "Deploy only the preflight-cleared exact task executor",
        )
        deployment = step(
            "Deploy only the preflight-cleared exact task executor",
            "Reserve cleanup before State can become running",
        )
        unused_delete = step(
            "Delete an executor whose State start never committed",
            "Destroy all private material, credentials, and temporary refs",
        )
        self.assertIn("verified-orphan-collision", preflight)
        self.assertIn("worker-settings.json", preflight)
        self.assertIn("worker-deployments.json", preflight)
        self.assertIn("worker-version.json", preflight)
        self.assertIn("verify_historical_private_executor_ownership.py", preflight)
        self.assertIn("Refusing to delete an application-only collision", preflight)
        self.assertIn("settings_inventory != expected_inventory", OWNERSHIP)
        self.assertIn("version_inventory != expected_inventory", OWNERSHIP)
        self.assertIn("health != expected_health", OWNERSHIP)
        self.assertIn("configuration != expected_configuration", OWNERSHIP)
        ownership = preflight.index("verified-orphan-collision")
        orphan_delete = preflight.index("delete_historical_private_executor")
        self.assertLess(ownership, orphan_delete)
        self.assertNotIn("executor-deploy-attempted", preflight)
        self.assertIn(": > \"$RUNNER_TEMP/executor-deploy-attempted\"", deployment)
        self.assertIn("steps.resource_preflight.outputs.cleared == 'true'", unused_delete)
        self.assertIn("steps.deploy.outcome != 'skipped'", unused_delete)
        marker = unused_delete.index("test ! -f")
        deletion = unused_delete.index("delete_historical_private_executor")
        self.assertLess(marker, deletion)

    def test_artifact_is_redacted_source_free_and_not_modern_evidence(self) -> None:
        artifact = step(
            "Write one redacted source-free terminal artifact",
            "Delete the terminal task's temporary executor",
        )
        self.assertIn("historical_private_replay_redacted_terminal", artifact)
        self.assertNotIn("ciphertext_base64", artifact)
        self.assertNotIn("plaintext_key_material_base64", artifact)
        self.assertNotIn("source.tar", artifact)
        for forbidden in ("release", "published", "lifecycle", "accepted_at"):
            self.assertNotIn(forbidden, artifact.lower())

    def test_actions_are_commit_pinned(self) -> None:
        pins = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", WORKFLOW)
        self.assertTrue(pins)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in pins))


if __name__ == "__main__":
    unittest.main()
