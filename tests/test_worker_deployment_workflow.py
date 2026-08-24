"""Structural guards for immutable dispatch-ref promotion and deployment."""

from __future__ import annotations

import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = (ROOT / ".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
PROMOTION_CANARY = (ROOT / ".github/workflows/promotion-canary.yml").read_text(
    encoding="utf-8"
)

ROLLBACK = (ROOT / ".github/workflows/rollback-worker.yml").read_text(encoding="utf-8")
STATE_WRITER_PREFLIGHT = (
    ROOT / ".github/workflows/verify-state-writer.yml"
).read_text(encoding="utf-8")
WRANGLER = json.loads((ROOT / "server/wrangler.jsonc").read_text(encoding="utf-8"))
BROKER_WRANGLER = json.loads(
    (ROOT / "server/wrangler.broker.jsonc").read_text(encoding="utf-8")
)
REPLAY_WRANGLER = json.loads(
    (ROOT / "server/wrangler.replay.jsonc").read_text(encoding="utf-8")
)
PACKAGE = json.loads((ROOT / "server/package.json").read_text(encoding="utf-8"))
WORKER_APP = (ROOT / "server/src/app.ts").read_text(encoding="utf-8")
WORKER_ENTRYPOINT = (ROOT / "server/src/index.ts").read_text(encoding="utf-8")
REPLAY_ENTRYPOINT = (ROOT / "server/src/replay-entry.ts").read_text(encoding="utf-8")
REPLAY_APP = (ROOT / "server/src/replay-app.ts").read_text(encoding="utf-8")
REPLAY_RECEIPT = (ROOT / "server/src/replay-terminal-receipt.ts").read_text(
    encoding="utf-8"
)
REPLAY_DOCKERFILE = (ROOT / "server/Dockerfile.replay").read_text(encoding="utf-8")
ROLLBACK_VALIDATOR = (ROOT / "scripts/validate_cloudflare_rollback.py").read_text(
    encoding="utf-8"
)


class WorkerDeploymentWorkflowTests(unittest.TestCase):
    def test_smoke_checks_use_approved_memory_limit_for_both_environments(self) -> None:
        self.assertEqual(DEPLOY.count('"staging_memory_limit_bytes": 12 * 1024**3'), 2)
        self.assertEqual(DEPLOY.count('"production_memory_gate_bytes": 12 * 1024**3'), 2)
        self.assertNotIn('"production_memory_gate_bytes": 16 * 1024**3', DEPLOY)

    def test_state_writer_preflight_is_protected_and_intake_safe(self) -> None:
        self.assertIn("environment: cloudflare-${{ inputs.target_environment }}", STATE_WRITER_PREFLIGHT)
        self.assertIn("READINESS_TOKEN: ${{ secrets.READINESS_TOKEN }}", STATE_WRITER_PREFLIGHT)
        self.assertIn("--request POST", STATE_WRITER_PREFLIGHT)
        self.assertIn('payload["status"] != "state_writer_ready"', STATE_WRITER_PREFLIGHT)
        self.assertIn('payload["intake_enabled"] is not False', STATE_WRITER_PREFLIGHT)
        self.assertNotIn("GITHUB_STATE_TOKEN", STATE_WRITER_PREFLIGHT)

    def test_infrastructure_only_merge_does_not_redeploy(self) -> None:
        pull_request = DEPLOY.split("  pull_request:", 1)[1].split("  push:", 1)[0]
        push = DEPLOY.split("  push:", 1)[1].split("  workflow_dispatch:", 1)[0]
        self.assertIn("'INFRASTRUCTURE.md'", pull_request)
        self.assertNotIn("'INFRASTRUCTURE.md'", push)

    def test_documentation_only_merge_does_not_redeploy(self) -> None:
        push = DEPLOY.split("  push:", 1)[1].split("  workflow_dispatch:", 1)[0]
        self.assertIn("'server/**'", push)
        self.assertIn("'!server/*.md'", push)
        self.assertIn("'!server/**/*.md'", push)

    def test_dispatch_dependency_changes_promote_and_deploy_exact_ref(self) -> None:
        pull_request = DEPLOY.split("  pull_request:", 1)[1].split("  push:", 1)[0]
        push = DEPLOY.split("  push:", 1)[1].split("  workflow_dispatch:", 1)[0]
        for trigger in (pull_request, push):
            self.assertIn("'.github/workflows/submission.yml'", trigger)
            self.assertIn("'.github/workflows/promotion-canary.yml'", trigger)
            self.assertIn("'.audit/**'", trigger)
            self.assertIn("'scripts/**'", trigger)

    def test_smoke_retries_structured_payload_propagation(self) -> None:
        self.assertEqual(DEPLOY.count("for attempt in $(seq 1 13); do"), 2)
        self.assertEqual(DEPLOY.count("for attempt in $(seq 1 25); do"), 2)
        self.assertEqual(DEPLOY.count('echo "health payload did not converge'), 2)
        self.assertEqual(DEPLOY.count('echo "replay health payload did not converge'), 2)
        self.assertNotIn("curl --fail --retry", DEPLOY)

    def test_replay_executor_is_isolated_capacity_bounded_and_disabled(self) -> None:
        expected_urls = {
            "staging": "https://lean-eval-replay-executor-staging.lean-eval.workers.dev/healthz",
            "production": "https://lean-eval-replay-executor.lean-eval.workers.dev/healthz",
        }
        for environment in ("staging", "production"):
            with self.subTest(environment=environment):
                configuration = REPLAY_WRANGLER["env"][environment]
                container = configuration["containers"][0]
                variables = configuration["vars"]
                bindings = configuration["durable_objects"]["bindings"]
                self.assertEqual(container["class_name"], "ReplaySandbox")
                self.assertEqual(container["instance_type"], "standard-4")
                self.assertEqual(container["max_instances"], 1)
                self.assertEqual(container["ssh"], {"enabled": False})
                self.assertIs(configuration["workers_dev"], True)
                self.assertIs(configuration["preview_urls"], False)
                self.assertEqual(variables["REPLAY_ENABLED"], "false")
                self.assertEqual(
                    variables["STAGING_ACCEPTANCE_ENABLED"],
                    "true" if environment == "staging" else "false",
                )
                self.assertEqual(variables["STAGING_MEMORY_LIMIT_BYTES"], "12884901888")
                self.assertEqual(variables["PRODUCTION_MEMORY_GATE_BYTES"], "12884901888")
                self.assertEqual(
                    bindings,
                    [
                        {"name": "REPLAY_SANDBOX", "class_name": "ReplaySandbox"},
                        {
                            "name": "REPLAY_TERMINAL_RECEIPT",
                            "class_name": "ReplayTerminalReceipt",
                        },
                    ],
                )
                self.assertEqual(
                    configuration["migrations"][-1],
                    {"tag": "v2", "new_sqlite_classes": ["ReplayTerminalReceipt"]},
                )
                self.assertIn(expected_urls[environment], DEPLOY)

        self.assertIn("override enableInternet = false", REPLAY_ENTRYPOINT)
        self.assertIn("`r-${runnerNonce.slice(0, 61)}`", REPLAY_ENTRYPOINT)
        self.assertIn("await sandbox.destroy()", REPLAY_APP)
        self.assertIn("AUTHORITATIVE_TERMINAL_RECEIPT_RETENTION_MS", REPLAY_APP)
        self.assertIn("claimBinding(binding: unknown)", REPLAY_RECEIPT)
        self.assertIn("prepareReceipt(receipt: unknown)", REPLAY_RECEIPT)
        self.assertIn("confirmReceipt()", REPLAY_RECEIPT)
        self.assertGreaterEqual(REPLAY_RECEIPT.count("storage.transaction"), 3)
        self.assertGreaterEqual(REPLAY_RECEIPT.count("transaction.setAlarm"), 3)
        self.assertIn("override async alarm()", REPLAY_RECEIPT)
        self.assertIn("/api/v1/staging-archive-acceptance", REPLAY_APP)
        self.assertIn("/opt/lean-eval/replay-archive-acceptance", REPLAY_DOCKERFILE)
        self.assertIn("/opt/lean-eval/replay-measure", REPLAY_DOCKERFILE)
        self.assertIn("ca-certificates curl python3", REPLAY_DOCKERFILE)
        self.assertIn("test \"$(age --version)\" = 'v1.3.1'", REPLAY_DOCKERFILE)

    def test_replay_container_auto_deploys_but_dry_run_skips_local_rollout(self) -> None:
        self.assertEqual(
            DEPLOY.count("npx wrangler deploy --config wrangler.replay.jsonc --env"),
            2,
        )
        self.assertNotIn("--containers-rollout=none", DEPLOY)
        dry_run = PACKAGE["scripts"]["deploy:dry-run"]
        self.assertEqual(dry_run.count("--containers-rollout=none"), 2)

    def test_deployment_waits_for_each_exact_container_rollout(self) -> None:
        self.assertEqual(DEPLOY.count("wait_replay_container_rollout.py"), 2)
        for environment, application in (
            (
                "staging",
                "lean-eval-replay-executor-staging-replaysandbox-staging",
            ),
            (
                "production",
                "lean-eval-replay-executor-replaysandbox-production",
            ),
        ):
            deployment = DEPLOY.index(
                f"wrangler deploy --config wrangler.replay.jsonc --env {environment}"
            )
            wait = DEPLOY.index(f"--application {application}")
            smoke = DEPLOY.index(
                f"Smoke-test disabled {environment} replay executor"
            )
            self.assertLess(deployment, wait)
            self.assertLess(wait, smoke)

    def test_reviewed_promotion_uses_only_contents_write(self) -> None:
        block = DEPLOY.split("\n  promote-dispatch-ref:", 1)[1].split(
            "\n  deploy-staging:", 1
        )[0]
        self.assertIn("environment: submission-dispatch-promotion", block)
        self.assertIn("permissions:\n      contents: write", block)
        self.assertIn("defaults:\n      run:\n        working-directory: .", block)
        self.assertEqual(block.count("secrets."), 1)
        self.assertIn(
            "APPROVAL_GUARD: ${{ secrets.DISPATCH_PROMOTION_APPROVAL_GUARD }}",
            block,
        )
        self.assertIn("reviewed dispatch-promotion environment is not configured", block)
        self.assertIn('tag="lean-eval-dispatch/$WORKFLOW_COMMIT"', block)
        self.assertIn("compare/$WORKFLOW_COMMIT...main", block)
        self.assertIn("contents/.github/workflows/submission.yml?ref=$WORKFLOW_COMMIT", block)
        self.assertIn("contents/.github/workflows/promotion-canary.yml?ref=$WORKFLOW_COMMIT", block)

    def test_promotion_is_idempotent_and_collision_safe(self) -> None:
        self.assertIn('if [ "$existing" != "$WORKFLOW_COMMIT" ]; then', DEPLOY)
        self.assertIn("dispatch tag collision", DEPLOY)
        self.assertIn("dispatch tag read-back did not resolve", DEPLOY)
        self.assertIn('-f "ref=refs/tags/$tag"', DEPLOY)
        self.assertIn('-f "sha=$WORKFLOW_COMMIT"', DEPLOY)

    def test_both_deployments_receive_promoted_ref(self) -> None:
        self.assertEqual(
            DEPLOY.count("DISPATCH_WORKFLOW_REF: ${{ needs.promote-dispatch-ref.outputs.ref }}"),
            3,
        )
        self.assertEqual(
            DEPLOY.count('--var "DISPATCH_WORKFLOW_REF:$DISPATCH_WORKFLOW_REF"'),
            2,
        )
        self.assertIn("needs: promote-dispatch-ref", DEPLOY)
        self.assertIn("needs: [staging-promotion-canary, promote-dispatch-ref]", DEPLOY)

    def test_production_is_blocked_on_the_exact_staging_promotion_canary(self) -> None:
        canary = DEPLOY.split("\n  staging-promotion-canary:", 1)[1].split(
            "\n  deploy-production:", 1
        )[0]
        self.assertIn("needs: [deploy-staging, promote-dispatch-ref]", canary)
        self.assertIn("environment: cloudflare-staging", canary)
        self.assertIn("runs-on: ubuntu-24.04", canary)
        self.assertIn("READINESS_TOKEN: ${{ secrets.READINESS_TOKEN }}", canary)
        self.assertIn("run_staging_promotion_canary.py", canary)
        self.assertIn('--commit "$GITHUB_SHA"', canary)
        self.assertIn('--dispatch-ref "$DISPATCH_WORKFLOW_REF"', canary)
        self.assertIn('--run-id "$GITHUB_RUN_ID"', canary)
        self.assertIn('--run-attempt "$GITHUB_RUN_ATTEMPT"', canary)
        self.assertIn("actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97", canary)
        self.assertIn("python-version: '3.11.10'", canary)
        self.assertIn("timeout-minutes: 12", canary)
        self.assertIn("--timeout-seconds 480", canary)
        self.assertNotIn("GITHUB_STATE_TOKEN", canary)
        self.assertNotIn("GITHUB_DISPATCH_TOKEN", canary)
        self.assertNotIn("upload-artifact", canary)
        production = DEPLOY.split("\n  deploy-production:", 1)[1]
        self.assertIn(
            "needs: [staging-promotion-canary, promote-dispatch-ref]",
            production,
        )

    def test_promotion_dispatch_target_is_dedicated_source_free_and_no_op(self) -> None:
        self.assertIn("permissions: {}", PROMOTION_CANARY)
        self.assertIn("workflow_dispatch:", PROMOTION_CANARY)
        self.assertIn("source-free-no-op:", PROMOTION_CANARY)
        self.assertIn("runs-on: ubuntu-24.04", PROMOTION_CANARY)
        self.assertIn('echo \'{"status":"source_free_no_op_verified"}\'', PROMOTION_CANARY)
        self.assertIn("$GITHUB_SHA", PROMOTION_CANARY)
        self.assertIn("$GITHUB_REF", PROMOTION_CANARY)
        for forbidden in (
            "actions/checkout",
            "secrets.",
            "submission.yml",
            "archive",
            "evaluation",
            "lean-eval-audit",
            "results/",
            "upload-artifact",
        ):
            self.assertNotIn(forbidden, PROMOTION_CANARY)

    def test_rollback_validates_all_targets_before_mutation(self) -> None:
        self.assertIn('dispatch_ref="lean-eval-dispatch/$EXPECTED_COMMIT"', ROLLBACK)
        self.assertIn("rollback dispatch tag does not resolve", ROLLBACK)
        self.assertIn("repository main is not protected", ROLLBACK)
        self.assertIn("rollback commit is not reachable from protected main", ROLLBACK)
        validation = ROLLBACK.index("validate_cloudflare_rollback.py plan")
        for component in ("intake", "broker", "replay"):
            self.assertIn(f"--{component}-version-id", ROLLBACK)
            self.assertIn(f"--{component}-config", ROLLBACK)
        self.assertLess(
            validation,
            ROLLBACK.index("Pause intake by deploying exact target code with current secrets"),
        )
        self.assertIn("--require-disabled", ROLLBACK)
        self.assertIn('"promotion_canary_enabled"', ROLLBACK_VALIDATOR)
        self.assertIn('"PROMOTION_CANARY_ENABLED"', ROLLBACK_VALIDATOR)
        self.assertIn("cloudflare-rollback-qualification-v1.json", ROLLBACK)
        self.assertEqual(ROLLBACK.count("compatible-capabilities"), 1)
        self.assertIn("Preserve source-free pre-mutation recovery state", ROLLBACK)
        self.assertIn("--state-main", ROLLBACK)
        self.assertIn("--state-schema", ROLLBACK)
        self.assertIn("protected State main moved after rollback qualification", ROLLBACK)
        self.assertIn("validate_cloudflare_rollback.py prestate", ROLLBACK)
        artifact = ROLLBACK.split(
            "- name: Preserve source-free pre-mutation recovery state", 1
        )[1].split("- name: Pause intake", 1)[0]
        self.assertIn("prestate.json", artifact)
        self.assertNotIn("original-*-version.json", artifact)
        self.assertNotIn("original-container-info.json", artifact)

    def test_rollback_is_dependency_ordered_and_exactly_verified(self) -> None:
        intake = ROLLBACK.index("Pause intake by deploying exact target code with current secrets")
        broker = ROLLBACK.index("Deploy exact target broker code with current secrets")
        replay = ROLLBACK.index("Deploy the exact target replay Worker and container")
        wait = ROLLBACK.index("Wait for the exact production replay container")
        self.assertLess(intake, broker)
        self.assertLess(broker, replay)
        self.assertLess(replay, wait)
        self.assertEqual(ROLLBACK.count("deployments status"), 6)
        self.assertEqual(ROLLBACK.count("validate_cloudflare_rollback.py status"), 0)
        self.assertEqual(ROLLBACK.count("active-version"), 6)
        self.assertEqual(ROLLBACK.count("validate_cloudflare_rollback.py health"), 2)
        self.assertNotIn("wrangler rollback", ROLLBACK)
        self.assertNotIn("--yes", ROLLBACK)
        self.assertNotIn("--secrets-file", ROLLBACK)
        self.assertGreaterEqual(ROLLBACK.count("--keep-vars"), 6)
        self.assertIn("--containers-rollout=immediate", ROLLBACK)
        self.assertIn("verify_authoritative_replay_image_reference", ROLLBACK)
        self.assertIn("deploy --dry-run", ROLLBACK)
        self.assertIn("--command-timeout-seconds 15", ROLLBACK)
        self.assertEqual(ROLLBACK.count("--max-time 10"), 2)
        self.assertIn("if: always()", ROLLBACK)

    def test_rate_limits_and_reconciliation_are_distinct_and_declarative(self) -> None:
        staging = WRANGLER["env"]["staging"]
        production = WRANGLER["env"]["production"]
        staging_limit = staging["ratelimits"][0]
        production_limit = production["ratelimits"][0]
        self.assertEqual(staging_limit["name"], "API_RATE_LIMITER")
        self.assertEqual(production_limit["name"], "API_RATE_LIMITER")
        self.assertNotEqual(staging_limit["namespace_id"], production_limit["namespace_id"])
        self.assertEqual(staging_limit["simple"], {"limit": 30, "period": 60})
        self.assertEqual(production_limit["simple"], {"limit": 30, "period": 60})
        self.assertEqual(staging["triggers"]["crons"], ["* * * * *"])
        self.assertEqual(production["triggers"]["crons"], ["* * * * *"])
        self.assertEqual(staging["vars"]["PROMOTION_CANARY_ENABLED"], "true")
        self.assertEqual(production["vars"]["PROMOTION_CANARY_ENABLED"], "false")
        self.assertIn("env.API_RATE_LIMITER.limit({ key })", WORKER_APP)
        self.assertIn("handleScheduled(env, controller.scheduledTime)", WORKER_ENTRYPOINT)
        self.assertIn("if (!intakeEnabled(env)) return;", WORKER_APP)
        self.assertIn("reconcilePromotionCanariesScheduled", WORKER_APP)
        self.assertIn('env.DEPLOYMENT_ENVIRONMENT === "staging"', WORKER_APP)
        self.assertEqual(production["vars"]["PROMOTION_CANARY_ENABLED"], "false")

    def test_private_brokers_are_bound_and_deployed_before_intake_workers(self) -> None:
        for environment in ("staging", "production"):
            with self.subTest(environment=environment):
                service = WRANGLER["env"][environment]["services"]
                self.assertEqual(
                    service,
                    [
                        {
                            "binding": "GITHUB_BROKER",
                            "service": f"lean-eval-github-broker-{environment}",
                        }
                    ],
                )
                broker = BROKER_WRANGLER["env"][environment]
                self.assertIs(broker["workers_dev"], False)
                self.assertIs(broker["preview_urls"], False)
                self.assertNotIn("routes", broker)
                self.assertNotIn("SOURCE_APP_PRIVATE_KEY", broker["vars"])
                self.assertNotIn("DISPATCH_APP_PRIVATE_KEY", broker["vars"])
                deploy_block = DEPLOY.split(
                    f"- name: Deploy {environment} GitHub broker", 1
                )[1].split(f"- name: Deploy {environment} submission Worker", 1)[0]
                self.assertIn("wrangler.broker.jsonc", deploy_block)
                self.assertIn(f"--env {environment}", deploy_block)

    def test_secret_contracts_are_explicit_in_each_deployment_environment(self) -> None:
        intake_secrets = {
            "AUTH_TOKEN_SECRET",
            "GITHUB_OAUTH_CLIENT_ID",
            "GITHUB_OAUTH_CLIENT_SECRET",
            "GITHUB_STATE_TOKEN",
            "LIFECYCLE_CALLBACK_TOKEN",
            "READINESS_TOKEN",
        }
        broker_secrets = {
            "DISPATCH_APP_ID",
            "DISPATCH_APP_PRIVATE_KEY",
            "SOURCE_APP_ID",
            "SOURCE_APP_PRIVATE_KEY",
        }
        for environment in ("staging", "production"):
            self.assertEqual(
                set(WRANGLER["env"][environment]["secrets"]["required"]),
                intake_secrets,
            )
            self.assertEqual(
                set(BROKER_WRANGLER["env"][environment]["secrets"]["required"]),
                broker_secrets,
            )

    def test_temporary_workers_dev_routes_are_exact_and_intake_disabled(self) -> None:
        staging = WRANGLER["env"]["staging"]
        production = WRANGLER["env"]["production"]
        expected = {
            "staging": (
                staging,
                "https://lean-eval-submission-server-staging.lean-eval.workers.dev",
            ),
            "production": (
                production,
                "https://lean-eval-submission-server.lean-eval.workers.dev",
            ),
        }
        for environment, (configuration, base_url) in expected.items():
            with self.subTest(environment=environment):
                self.assertIs(configuration["workers_dev"], True)
                self.assertIs(configuration["preview_urls"], False)
                self.assertNotIn("routes", configuration)
                self.assertEqual(configuration["vars"]["INTAKE_ENABLED"], "false")
                self.assertEqual(
                    configuration["vars"]["OAUTH_CALLBACK_URL"],
                    base_url + "/api/v1/oauth/callback",
                )
                self.assertIn(base_url + "/healthz", DEPLOY)
        self.assertIn(
            "https://lean-eval-submission-server.lean-eval.workers.dev/healthz",
            ROLLBACK,
        )
        self.assertNotIn("eval-submit-staging.lean-lang.org", DEPLOY)
        self.assertNotIn("eval-submit.lean-lang.org", DEPLOY)
        self.assertNotIn("eval-submit.lean-lang.org", ROLLBACK)

    def test_owner_routes_have_no_full_ledger_scan(self) -> None:
        self.assertNotIn("readEvents", WORKER_APP)
        self.assertIn("ledger.readSubmission(match[1])", WORKER_APP)


if __name__ == "__main__":
    unittest.main()
