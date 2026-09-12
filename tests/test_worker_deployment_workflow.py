"""Structural guards for immutable dispatch-ref promotion and deployment."""

from __future__ import annotations

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = (ROOT / ".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
WORKFLOW_PROMOTION = (
    ROOT / ".github/workflows/promote-workflow-dispatch-ref.yml"
).read_text(encoding="utf-8")
PROMOTION_CANARY = (ROOT / ".github/workflows/promotion-canary.yml").read_text(
    encoding="utf-8"
)

ROLLBACK = (ROOT / ".github/workflows/rollback-worker.yml").read_text(encoding="utf-8")
RECOVERY = (
    ROOT / ".github/workflows/intake-disable-recovery.yml"
).read_text(encoding="utf-8")
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
LEASE_SMOKE = (ROOT / "scripts/run_production_intake_lease_smoke.py").read_text(
    encoding="utf-8"
)
PACKAGE = json.loads((ROOT / "server/package.json").read_text(encoding="utf-8"))
QUALIFICATION = json.loads(
    (ROOT / ".audit/cloudflare-rollback-qualification-v1.json").read_text(
        encoding="utf-8"
    )
)
WORKER_APP = (ROOT / "server/src/app.ts").read_text(encoding="utf-8")
WORKER_PROVIDER = (ROOT / "server/src/github-provider.ts").read_text(
    encoding="utf-8"
)
WORKER_BROKER = (ROOT / "server/src/github-broker.ts").read_text(encoding="utf-8")
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
HISTORICAL_AUTHORITY_PREPARATION = (
    ROOT / "scripts/prepare_historical_public_authority.py"
).read_text(encoding="utf-8")


class WorkerDeploymentWorkflowTests(unittest.TestCase):
    def test_pull_request_checks_cannot_cancel_the_protected_main_deployment(self) -> None:
        self.assertIn(
            "format('submission-worker-pr-{0}', github.event.pull_request.number)",
            DEPLOY,
        )
        self.assertIn("'submission-worker-protected-main'", DEPLOY)
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            DEPLOY,
        )

    def test_deploy_and_rollback_bind_current_state_and_atomic_model_health(self) -> None:
        expected = "235a96c96462438c7680e6fb90fa0e6044ec1774"
        self.assertEqual(QUALIFICATION["state_main_commit"], expected)
        self.assertGreaterEqual(DEPLOY.count(expected), 2)
        self.assertEqual(
            DEPLOY.count('body["model_identity_write_max_subrequests"] == 400'),
            4,
        )
        self.assertEqual(
            DEPLOY.count(
                'body["model_identity_consolidation_api"] == "atomic_reverse_impact_v1"'
            ),
            4,
        )
        self.assertEqual(
            DEPLOY.count('body["model_identity_consolidation_api_enabled"] is False'),
            4,
        )
        self.assertEqual(
            DEPLOY.count('"release_opt_in_api_enabled",'),
            3,
        )
        self.assertIn('body["release_opt_out_api_enabled"] is False', DEPLOY)
        self.assertIn("state-comparison.json", ROLLBACK)
        self.assertIn('"RESULT_OWNER_STATE_CONTRACT_COMMIT"', ROLLBACK_VALIDATOR)

    def test_rollback_uses_independent_public_state_proof_before_mutation(self) -> None:
        self.assertNotIn("repos/leanprover/lean-eval-state", DEPLOY)
        self.assertIn("repos/$state_repository/branches/main", ROLLBACK)
        self.assertIn("repos/$state_repository/compare/$state_contract...$state_live", ROLLBACK)
        self.assertIn("repos/$state_repository/git/commits/$state_contract", ROLLBACK)
        self.assertIn("repos/$state_repository/git/commits/$state_live", ROLLBACK)
        self.assertIn("repos/$state_repository/git/trees/$state_contract_tree", ROLLBACK)
        self.assertIn("repos/$state_repository/git/trees/$state_live_tree", ROLLBACK)
        self.assertIn("--jq '{content,encoding,path,sha,type,url}'", ROLLBACK)
        self.assertIn("base64.b64decode", ROLLBACK_VALIDATOR)
        self.assertIn("GH_TOKEN: ${{ github.token }}", ROLLBACK)
        self.assertNotIn("GITHUB_STATE_TOKEN", ROLLBACK)
        self.assertIn("state_contract_verified", LEASE_SMOKE)

    def test_staging_deploy_proves_the_results_branch_is_protected(self) -> None:
        staging = DEPLOY.split("  deploy-staging:", 1)[1].split(
            "  deploy-production:", 1
        )[0]
        self.assertIn("branches/staging-results", staging)
        self.assertIn('.protected == true', staging)
        self.assertIn('[[ ! "$commit" =~ ^[0-9a-f]{40}$ ]]', staging)
        self.assertIn("GH_TOKEN: ${{ github.token }}", staging)

    def test_smoke_checks_use_approved_memory_limit_for_both_environments(self) -> None:
        self.assertEqual(DEPLOY.count('"staging_memory_limit_bytes": 12 * 1024**3'), 2)
        self.assertEqual(DEPLOY.count('"production_memory_gate_bytes": 12 * 1024**3'), 2)
        self.assertNotIn('"production_memory_gate_bytes": 16 * 1024**3', DEPLOY)

    def test_state_writer_preflight_is_protected_and_reports_intake_state(self) -> None:
        self.assertIn("environment: cloudflare-${{ inputs.target_environment }}", STATE_WRITER_PREFLIGHT)
        self.assertIn("READINESS_TOKEN: ${{ secrets.READINESS_TOKEN }}", STATE_WRITER_PREFLIGHT)
        self.assertIn("--request POST", STATE_WRITER_PREFLIGHT)
        self.assertIn('payload["status"] != "state_writer_ready"', STATE_WRITER_PREFLIGHT)
        self.assertIn(
            'payload["environment"] != os.environ["TARGET_ENVIRONMENT"]',
            STATE_WRITER_PREFLIGHT,
        )
        self.assertIn(
            're.fullmatch(r"[0-9a-f]{40}", payload["state_commit"]) is None',
            STATE_WRITER_PREFLIGHT,
        )
        self.assertIn(
            'type(payload["intake_enabled"]) is not bool',
            STATE_WRITER_PREFLIGHT,
        )
        self.assertIn(
            '"enabled" if payload["intake_enabled"] else "disabled"',
            STATE_WRITER_PREFLIGHT,
        )
        self.assertNotIn("requires intake to remain disabled", STATE_WRITER_PREFLIGHT)
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

    def test_one_shot_amendment_canary_is_retired(self) -> None:
        retired_paths = {
            ".github/workflows/staging-amendment-canary.yml",
            "docs/staging-amendment-canary.md",
            "server/src/staging-amendment-canary.ts",
            "server/test/staging-amendment-canary.test.ts",
            "tests/test_staging_amendment_canary_workflow.py",
        }
        for path in retired_paths:
            with self.subTest(path=path):
                self.assertFalse((ROOT / path).exists())
        for path in [
            "server/src/app.ts",
            "server/vitest.config.ts",
            "server/worker-configuration.d.ts",
            "server/wrangler.jsonc",
        ]:
            with self.subTest(path=path):
                self.assertNotIn(
                    "STAGING_AMENDMENT_CANARY_TOKEN",
                    (ROOT / path).read_text(encoding="utf-8"),
                )
        self.assertNotIn("/internal/v1/staging-amendment-canary", WORKER_APP)

    def test_historical_public_qualifier_is_retired(self) -> None:
        retired_paths = {
            ".github/workflows/historical-public-authority-preparation.yml",
            ".github/workflows/historical-public-image-qualification.yml",
            "docs/historical-public-authority-preparation.md",
            "historical-public-qualification/contract-v1.json",
            "historical-public-qualification/qualification.py",
            "historical-public-qualification/wait_rollout.py",
            "schemas/historical-public-authority-preparation-v1.schema.json",
            "schemas/historical-public-authority-preparation-v2.schema.json",
            "tests/test_historical_public_image_qualification.py",
        }
        for path in retired_paths:
            with self.subTest(path=path):
                self.assertFalse((ROOT / path).exists())

    def test_offline_evidence_scripts_do_not_redeploy_workers(self) -> None:
        pull_request = DEPLOY.split("  pull_request:", 1)[1].split("  push:", 1)[0]
        push = DEPLOY.split("  push:", 1)[1].split("  workflow_dispatch:", 1)[0]
        offline_evidence = {
            "scripts/aggregate_public_replay_github_evidence.py",
            "scripts/build_public_replay_toolchain_registry.py",
            "scripts/classify_historical_private_archives.py",
            "scripts/historical_replay_controller.py",
            "scripts/inventory_historical_replay.py",
            "scripts/prepare_historical_final_delta_packet.py",
            "scripts/prepare_historical_public_authority.py",
            "scripts/prepare_public_replay_plan.py",
            "scripts/reconcile_historical_replay_inventory_delta.py",
            "scripts/resolve_public_replay_github_evidence.py",
        }
        runtime_scripts = {
            "scripts/prepare_intake_enablement_lease.py",
            "scripts/validate_cloudflare_rollback.py",
            "scripts/wait_replay_container_rollout.py",
            "scripts/worker_intake_configuration.py",
            "scripts/worker_lifecycle_configuration.py",
        }
        for trigger in (pull_request, push):
            self.assertIn("'scripts/**'", trigger)
            excluded_scripts = set(re.findall(r"'!(scripts/[^']+)'", trigger))
            self.assertEqual(excluded_scripts, offline_evidence)
            for path in offline_evidence:
                with self.subTest(trigger=trigger[:20], path=path):
                    self.assertTrue((ROOT / path).is_file())
                    self.assertIn(f"'!{path}'", trigger)
            for path in runtime_scripts:
                with self.subTest(trigger=trigger[:20], path=path):
                    self.assertTrue((ROOT / path).is_file())
                    self.assertNotIn(f"'!{path}'", trigger)

    def test_dispatch_dependencies_are_promoted_without_unearned_deploys(self) -> None:
        pull_request = DEPLOY.split("  pull_request:", 1)[1].split("  push:", 1)[0]
        push = DEPLOY.split("  push:", 1)[1].split("  workflow_dispatch:", 1)[0]
        workflow_directory = ROOT / ".github/workflows"
        tag_mentions = {
            path.name
            for path in workflow_directory.glob("*.y*ml")
            if "lean-eval-dispatch/" in path.read_text(encoding="utf-8")
        }
        historical_target_or_minter = {
            "deploy-worker.yml",
            "intake-disable-recovery.yml",
            "promote-workflow-dispatch-ref.yml",
            "rollback-worker.yml",
        }
        dispatch_dependencies = (tag_mentions - historical_target_or_minter) | {
            # The submission workflow receives the immutable tag through its
            # Worker-supplied workflow_commit rather than spelling out the ref.
            "submission.yml",
        }
        self.assertEqual(
            dispatch_dependencies,
            {
                "accepted-archive-replay-staging.yml",
                "authoritative-replay-staging.yml",
                "aws-key-adapter-staging-smoke.yml",
                "aws-production-wrap-preflight.yml",
                "historical-final-delta-packet.yml",
                "historical-public-replay-plan.yml",
                "historical-public-runner-contract.yml",
                "historical-replay-inventory.yml",
                "public-replay-github-evidence.yml",
                "server-archive.yml",
                "set-staging-intake.yml",
                "submission.yml",
                "promotion-canary.yml",
            },
        )
        deployed_dependencies = set(
            re.findall(
                r"actions/workflows/([A-Za-z0-9_.-]+\.ya?ml)/dispatches",
                WORKER_PROVIDER,
            )
        )
        default_submission = re.search(
            r'env\.DISPATCH_WORKFLOW \?\? "([A-Za-z0-9_.-]+\.ya?ml)"',
            WORKER_APP,
        )
        self.assertIsNotNone(default_submission)
        deployed_dependencies.add(default_submission.group(1))
        pending = list(deployed_dependencies)
        while pending:
            workflow = pending.pop()
            workflow_text = (workflow_directory / workflow).read_text(encoding="utf-8")
            for callee in re.findall(
                r"uses:\s+\./\.github/workflows/([A-Za-z0-9_.-]+\.ya?ml)",
                workflow_text,
            ):
                if callee not in deployed_dependencies:
                    deployed_dependencies.add(callee)
                    pending.append(callee)
        self.assertEqual(
            deployed_dependencies,
            {"promotion-canary.yml", "server-archive.yml", "submission.yml"},
        )
        # These manual workflows are not dispatched by the Worker, but their
        # deployed_commit preconditions bind them to the live staging runtime.
        # Changing them therefore requires the ordinary runtime deployment lane.
        runtime_bound_dependencies = deployed_dependencies | {
            "accepted-archive-replay-staging.yml",
            "authoritative-replay-staging.yml",
            "set-staging-intake.yml",
        }
        promotion_push = WORKFLOW_PROMOTION.split("  push:", 1)[1].split(
            "permissions:", 1
        )[0]
        promotion_paths = set(
            re.findall(r"'\.github/workflows/([^']+)'", promotion_push)
        )
        self.assertEqual(
            promotion_paths,
            (dispatch_dependencies - runtime_bound_dependencies)
            | {"promote-workflow-dispatch-ref.yml"},
        )
        for workflow in dispatch_dependencies:
            path = f"'.github/workflows/{workflow}'"
            with self.subTest(workflow=workflow):
                if workflow in runtime_bound_dependencies:
                    self.assertIn(path, pull_request)
                    self.assertIn(path, push)
                    self.assertNotIn(path, promotion_push)
                else:
                    self.assertNotIn(path, pull_request)
                    self.assertNotIn(path, push)
                    self.assertIn(path, promotion_push)
        self.assertIn(
            "'.github/workflows/promote-workflow-dispatch-ref.yml'",
            promotion_push,
        )
        for trigger in (pull_request, push):
            self.assertIn("'.audit/**'", trigger)
            self.assertIn("'scripts/**'", trigger)

        offline_evidence_scripts = {
            "aggregate_public_replay_github_evidence.py",
            "build_public_replay_toolchain_registry.py",
            "classify_historical_private_archives.py",
            "historical_replay_controller.py",
            "inventory_historical_replay.py",
            "prepare_historical_final_delta_packet.py",
            "prepare_historical_public_authority.py",
            "prepare_public_replay_plan.py",
            "reconcile_historical_replay_inventory_delta.py",
            "resolve_public_replay_github_evidence.py",
        }
        for trigger in (pull_request, push):
            self.assertEqual(
                set(re.findall(r"'!scripts/([^']+)'", trigger)),
                offline_evidence_scripts,
            )
        promoted_runtime_scripts = set(
            re.findall(r"'scripts/([^']+)'", promotion_push)
        )
        self.assertEqual(
            promoted_runtime_scripts,
            offline_evidence_scripts
            - {
                "classify_historical_private_archives.py",
                "historical_replay_controller.py",
            },
        )
        self.assertEqual(
            set(re.findall(r"'configuration/([^']+)'", promotion_push)),
            {
                "public-replay-legacy-adjudications-v1.json",
                "public-replay-workflow-definitions-v1.json",
            },
        )

    def test_exact_main_ci_trigger_cannot_be_path_filtered(self) -> None:
        ci_push = CI.split("  push:", 1)[1].split("  pull_request:", 1)[0]
        self.assertIn("branches: [main]", ci_push)
        self.assertNotIn("paths:", ci_push)
        self.assertNotIn("paths-ignore:", ci_push)

    def test_results_only_cutoff_uses_deployment_free_promotion(self) -> None:
        deploy_pull_request = DEPLOY.split("  pull_request:", 1)[1].split(
            "  push:", 1
        )[0]
        deploy_push = DEPLOY.split("  push:", 1)[1].split(
            "  workflow_dispatch:", 1
        )[0]
        promotion_push = WORKFLOW_PROMOTION.split("  push:", 1)[1].split(
            "permissions:", 1
        )[0]
        self.assertIn("'results/**'", promotion_push)
        self.assertNotIn("'results/**'", deploy_pull_request)
        self.assertNotIn("'results/**'", deploy_push)

    def test_workflow_only_promotion_is_protected_and_deployment_free(self) -> None:
        self.assertIn("environment: submission-dispatch-promotion", WORKFLOW_PROMOTION)
        self.assertIn(
            "group: workflow-dispatch-ref-promotion-${{ github.sha }}",
            WORKFLOW_PROMOTION,
        )
        self.assertIn("actions: read", WORKFLOW_PROMOTION)
        self.assertIn("contents: write", WORKFLOW_PROMOTION)
        self.assertIn("secrets.DISPATCH_PROMOTION_APPROVAL_GUARD", WORKFLOW_PROMOTION)
        self.assertIn("workflow commit is not reachable from protected main", WORKFLOW_PROMOTION)
        self.assertIn("dispatch tag collision", WORKFLOW_PROMOTION)
        self.assertIn("dispatch tag read-back did not resolve", WORKFLOW_PROMOTION)
        self.assertIn(
            "exact protected-main CI did not succeed before promotion",
            WORKFLOW_PROMOTION,
        )
        self.assertIn("actions/workflows/ci.yml/runs", WORKFLOW_PROMOTION)
        self.assertIn("timeout-minutes: 15", WORKFLOW_PROMOTION)
        self.assertIn("ci_deadline=$((SECONDS + 600))", WORKFLOW_PROMOTION)
        self.assertIn("head_sha=$WORKFLOW_COMMIT", WORKFLOW_PROMOTION)
        self.assertIn('if ! ci_conclusion=$(timeout 20s gh api', WORKFLOW_PROMOTION)
        self.assertIn("startup_failure|stale|neutral|skipped)", WORKFLOW_PROMOTION)
        self.assertEqual(
            WORKFLOW_PROMOTION.count("gh api"),
            WORKFLOW_PROMOTION.count("timeout 20s gh api"),
        )
        self.assertLess(
            WORKFLOW_PROMOTION.index(
                "exact protected-main CI did not succeed before promotion"
            ),
            WORKFLOW_PROMOTION.index('tag="lean-eval-dispatch/$WORKFLOW_COMMIT"'),
        )
        self.assertIn("read-back below distinguishes", WORKFLOW_PROMOTION)
        self.assertIn("read-back below distinguishes", DEPLOY)
        self.assertNotIn("wrangler", WORKFLOW_PROMOTION)
        self.assertNotIn("deploy-staging", WORKFLOW_PROMOTION)
        self.assertNotIn("deploy-production", WORKFLOW_PROMOTION)
        self.assertNotIn("api.cloudflare.com", WORKFLOW_PROMOTION)
        self.assertNotIn("/dispatches", WORKFLOW_PROMOTION)
        self.assertNotIn("gh workflow run", WORKFLOW_PROMOTION)

    def test_smoke_retries_structured_payload_propagation(self) -> None:
        self.assertEqual(DEPLOY.count("for attempt in $(seq 1 13); do"), 3)
        self.assertEqual(DEPLOY.count("for attempt in $(seq 1 25); do"), 2)
        self.assertEqual(
            DEPLOY.count('"historical_public_replay_enabled": False'), 2
        )
        self.assertEqual(DEPLOY.count('echo "health payload did not converge'), 1)
        self.assertEqual(DEPLOY.count('echo "replay health payload did not converge'), 1)
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
                    variables["HISTORICAL_PUBLIC_REPLAY_ENABLED"], "false"
                )
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
            smoke_name = (
                f"Smoke-test disabled {environment} replay executor"
                if environment == "staging"
                else "Verify exact disabled production replay dependency"
            )
            smoke = DEPLOY.index(smoke_name)
            self.assertLess(deployment, wait)
            self.assertLess(wait, smoke)

    def test_reviewed_runtime_promotion_is_exact_ci_gated_and_least_privilege(self) -> None:
        block = DEPLOY.split("\n  promote-dispatch-ref:", 1)[1].split(
            "\n  deploy-staging:", 1
        )[0]
        self.assertIn("environment: submission-dispatch-promotion", block)
        self.assertIn("permissions:\n      actions: read\n      contents: write", block)
        self.assertIn("defaults:\n      run:\n        working-directory: .", block)
        self.assertEqual(block.count("secrets."), 1)
        self.assertIn(
            "APPROVAL_GUARD: ${{ secrets.DISPATCH_PROMOTION_APPROVAL_GUARD }}",
            block,
        )
        self.assertIn("reviewed dispatch-promotion environment is not configured", block)
        self.assertIn('tag="lean-eval-dispatch/$WORKFLOW_COMMIT"', block)
        self.assertIn("compare/$WORKFLOW_COMMIT...main", block)
        self.assertIn("actions/workflows/ci.yml/runs", block)
        self.assertIn("timeout-minutes: 15", block)
        self.assertIn("ci_deadline=$((SECONDS + 600))", block)
        self.assertIn("head_sha=$WORKFLOW_COMMIT", block)
        self.assertIn('if ! ci_conclusion=$(timeout 20s gh api', block)
        self.assertIn("startup_failure|stale|neutral|skipped)", block)
        self.assertIn(
            "exact protected-main CI did not succeed before promotion",
            block,
        )
        self.assertLess(
            block.index("exact protected-main CI did not succeed before promotion"),
            block.index('tag="lean-eval-dispatch/$WORKFLOW_COMMIT"'),
        )
        self.assertEqual(block.count("gh api"), block.count("timeout 20s gh api"))
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
            5,
        )
        self.assertEqual(
            DEPLOY.count('--var "DISPATCH_WORKFLOW_REF:$DISPATCH_WORKFLOW_REF"'),
            4,
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
        self.assertNotIn("--require-disabled", ROLLBACK)
        self.assertIn('"promotion_canary_enabled"', ROLLBACK_VALIDATOR)
        self.assertIn('"PROMOTION_CANARY_ENABLED"', ROLLBACK_VALIDATOR)
        self.assertIn("--require-replay-disabled", ROLLBACK)
        self.assertIn("--require-intake-disabled", ROLLBACK)
        self.assertIn('"LEGACY_RESULT_OWNER_API_ENABLED"', ROLLBACK_VALIDATOR)
        self.assertIn('"RESULT_OWNER_STATE_CONTRACT_COMMIT"', ROLLBACK_VALIDATOR)
        self.assertIn("cloudflare-rollback-qualification-v1.json", ROLLBACK)
        self.assertEqual(ROLLBACK.count("compatible-capabilities"), 1)
        self.assertIn("Preserve source-free pre-mutation recovery state", ROLLBACK)
        self.assertNotIn("--state-proof", ROLLBACK)
        for argument in (
            "--state-main",
            "--state-comparison",
            "--state-contract-commit",
            "--state-live-commit",
            "--state-contract-tree",
            "--state-live-tree",
            "--state-schema",
        ):
            self.assertIn(argument, ROLLBACK)
        self.assertIn(".state_contract.contract_commit", ROLLBACK)
        self.assertNotIn("active-intake-attestation", ROLLBACK)
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
        final_intake = ROLLBACK.index(
            "Verify intake remains disabled and protected State remains valid"
        )
        self.assertLess(intake, broker)
        self.assertLess(broker, replay)
        self.assertLess(replay, wait)
        self.assertLess(wait, final_intake)
        self.assertEqual(ROLLBACK.count("deployments status"), 7)
        self.assertEqual(ROLLBACK.count("validate_cloudflare_rollback.py status"), 0)
        self.assertEqual(ROLLBACK.count("active-version"), 7)
        self.assertEqual(ROLLBACK.count("validate_cloudflare_rollback.py health"), 3)
        self.assertNotIn("wrangler rollback", ROLLBACK)
        self.assertNotIn("--yes", ROLLBACK)
        self.assertNotIn("--secrets-file", ROLLBACK)
        self.assertGreaterEqual(ROLLBACK.count("--keep-vars"), 4)
        self.assertIn("--containers-rollout=immediate", ROLLBACK)
        self.assertIn("verify_authoritative_replay_image_reference", ROLLBACK)
        self.assertIn("deploy --dry-run", ROLLBACK)
        self.assertIn("--command-timeout-seconds 15", ROLLBACK)
        self.assertEqual(ROLLBACK.count("--max-time 10"), 3)
        self.assertIn("if: always()", ROLLBACK)
        pause = ROLLBACK.split(
            "Pause intake by deploying exact target code with current secrets", 1
        )[1].split("Verify disabled intake before dependency mutation", 1)[0]
        self.assertIn('--var "INTAKE_ENABLED:false"', pause)
        self.assertNotIn("--keep-vars", pause)
        paused_verification = ROLLBACK.split(
            "Verify disabled intake before dependency mutation", 1
        )[1].split("Deploy exact target broker code", 1)[0]
        self.assertEqual(paused_verification.count("--require-intake-disabled"), 2)
        self.assertIn("production-state-readiness", paused_verification)
        self.assertIn("for attempt in $(seq 1 6)", paused_verification)
        self.assertIn("target-state-proof.json", paused_verification)
        self.assertNotIn("active-intake-attestation", ROLLBACK[:intake])
        final = ROLLBACK[final_intake:]
        self.assertIn("production-state-readiness", final)
        self.assertIn("for attempt in $(seq 1 6)", final)
        self.assertNotIn("unchanged protected State", final)
        self.assertNotIn("INTAKE_ENABLED:true", ROLLBACK)
        self.assertNotIn("intake-finalization-failsafe", ROLLBACK)
        self.assertIn("--require-intake-disabled", ROLLBACK[final_intake:])
        plan_step = ROLLBACK.split(
            "Validate and prepare the complete replay-disabled rollback unit", 1
        )[1].split("Preserve source-free pre-mutation recovery state", 1)[0]
        self.assertEqual(plan_step.count("--require-intake-disabled"), 1)

    def test_production_smoke_uses_the_reviewed_intake_configuration(self) -> None:
        production = DEPLOY.split("\n  deploy-production:", 1)[1]
        staging = DEPLOY.split("\n  deploy-staging:", 1)[1].split(
            "\n  deploy-production:", 1
        )[0]
        state_step = production.split(
            "- name: Read reviewed production intake state", 1
        )[1].split("\n      - name:", 1)[0]
        self.assertIn("python ../scripts/worker_intake_configuration.py", state_step)
        self.assertIn("--config wrangler.jsonc", state_step)
        self.assertIn("--environment production", state_step)
        self.assertIn("intake_configured_enabled", production)
        self.assertIn("intake_effective_enabled", production)
        provisional = production.split(
            "Provisionally deploy production intake disabled", 1
        )[1].split("Verify exact production broker version", 1)[0]
        self.assertIn('--var "INTAKE_ENABLED:false"', provisional)
        self.assertIn('body["intake_enabled"] is False', provisional)
        self.assertIn('body["intake_enabled"] is False', staging)

    def test_production_lifecycle_uses_one_reviewed_tracked_state(self) -> None:
        production = DEPLOY.split("\n  deploy-production:", 1)[1]
        state_step = production.split(
            "- name: Read reviewed production lifecycle state", 1
        )[1].split("\n      - name:", 1)[0]
        self.assertIn("python ../scripts/worker_lifecycle_configuration.py", state_step)
        self.assertIn("--config wrangler.jsonc", state_step)
        self.assertIn("--environment production", state_step)
        self.assertEqual(
            production.count(
                "EXPECTED_LIFECYCLE_ENABLED: ${{ steps.production-lifecycle.outputs.enabled }}"
            ),
            3,
        )
        for field in (
            "legacy_result_owner_api_enabled",
            "result_amendment_owner_api_enabled",
            "result_amendment_maintainer_api_enabled",
            "model_identity_owner_api_enabled",
            "model_identity_maintainer_api_enabled",
            "release_opt_in_api_enabled",
        ):
            with self.subTest(field=field):
                self.assertEqual(production.count(f'"{field}",'), 3)
        self.assertEqual(
            production.count('body["model_identity_consolidation_api_enabled"] is False'),
            3,
        )

    def test_production_version_reads_wait_for_cloudflare_convergence(self) -> None:
        production = DEPLOY.split("\n  deploy-production:", 1)[1]
        self.assertEqual(
            production.count("../scripts/read_cloudflare_worker_version"),
            4,
        )
        self.assertNotIn("npx wrangler versions view", production)
        self.assertIn(
            "'scripts/**'",
            DEPLOY.split("  pull_request:", 1)[1].split("  push:", 1)[0],
        )

    def test_deployments_require_the_dark_maintainer_gate_without_exposing_allowlist(
        self,
    ) -> None:
        self.assertIn(
            'body["result_amendment_maintainer_api_enabled"] is False', DEPLOY
        )
        self.assertEqual(
            DEPLOY.count('"result_amendment_maintainer_api_enabled",'), 3
        )
        self.assertNotIn('body["RESULT_AMENDMENT_MAINTAINERS"]', DEPLOY)
        self.assertNotIn('body["result_amendment_maintainers"]', DEPLOY)

    def test_production_enablement_is_provisional_and_fail_closed(self) -> None:
        production = DEPLOY.split("\n  deploy-production:", 1)[1]
        provisional = production.index("Provisionally deploy production intake disabled")
        provisional_health = production.index(
            "Verify exact disabled provisional production intake"
        )
        replay_deploy = production.index(
            "Deploy disabled production replay executor and container"
        )
        broker_deploy = production.index("Deploy production GitHub broker")
        broker = production.index("Verify exact production broker version")
        replay = production.index("Verify exact disabled production replay dependency")
        state = production.index("Verify exact protected State before production finalization")
        lease = production.index("Prepare the exact Worker-enforced intake lease")
        validate_lease = production.index("Validate the closed lease before any enabled mutation")
        enabled = production.index(
            "Deploy the exact self-expiring production intake lease"
        )
        smoke = production.index("Verify and consume the exact leased intake smoke")
        final = production.index("Atomically make the reviewed production intake durable")
        self.assertLess(provisional, provisional_health)
        self.assertLess(provisional_health, replay_deploy)
        self.assertLess(replay_deploy, broker_deploy)
        self.assertLess(broker_deploy, broker)
        self.assertLess(broker, replay)
        self.assertLess(replay, state)
        self.assertLess(state, lease)
        self.assertLess(lease, validate_lease)
        self.assertLess(validate_lease, enabled)
        self.assertLess(enabled, smoke)
        self.assertLess(smoke, final)
        self.assertIn('if: steps.production-intake.outputs.enabled == \'true\'', production)
        self.assertNotIn("actions: write", production)
        self.assertIn("INTAKE_LEASE_EXPIRES_AT", production)
        self.assertIn("intake-lease-bindings.env", production)
        self.assertIn('arguments+=(--var "$name:$value")', production)
        self.assertIn("intake-lease-smoke.json", production)
        self.assertIn("intake_lease", production)
        self.assertIn("run_production_intake_lease_smoke.py", production)
        self.assertIn('--expected-expires-at "$INTAKE_LEASE_EXPIRES_AT"', production)
        self.assertIn('--state-proof-output "$plan_dir/post-smoke-state-proof.json"', production)
        self.assertNotIn("--retry 2 --retry-all-errors --retry-max-time 45", production)
        self.assertIn(QUALIFICATION["state_main_commit"], production)
        self.assertIn(QUALIFICATION["state_event_schema_sha256"], production)
        self.assertIn("state_contract_verified", LEASE_SMOKE)
        self.assertIn("timeout --signal=TERM --kill-after=10s 150s", production)
        self.assertIn('INTAKE_LEASE_EXPIRES_AT - $(date +%s)))" -lt 240', production)
        self.assertNotIn("\n      - name:", production[final:].split("run: |", 1)[1])
        self.assertIn('--var "INTAKE_ENABLEMENT_MODE:durable"', production[final:])

    def test_production_state_gate_matches_the_reviewed_qualification(self) -> None:
        production = DEPLOY.split("\n  deploy-production:", 1)[1]
        state_gate = production.split(
            "- name: Verify exact protected State before production finalization",
            1,
        )[1].split("- name: Prepare the exact Worker-enforced intake lease", 1)[0]
        self.assertIn(QUALIFICATION["state_main_commit"], state_gate)
        self.assertIn(QUALIFICATION["state_event_schema_sha256"], state_gate)
        self.assertIn("production-state-readiness", state_gate)
        self.assertIn("--request POST", state_gate)
        self.assertIn("/readyz", state_gate)
        self.assertIn("for attempt in $(seq 1 6)", state_gate)
        self.assertIn("--write-out '%{http_code}'", state_gate)
        self.assertIn('classification" -ne 75', state_gate)
        self.assertNotIn("--fail-with-body", state_gate)
        self.assertNotIn("cat \"$proof\"", state_gate)
        self.assertIn("PRODUCTION_STATE_READINESS_FIELDS", ROLLBACK_VALIDATOR)
        self.assertIn("state_credential_missing", ROLLBACK_VALIDATOR)
        self.assertNotIn("lean-eval-state/", state_gate)
        self.assertNotIn("github.token", state_gate)

    def test_runtime_and_historical_finalizer_bind_distinct_state_views(self) -> None:
        state_commit = "235a96c96462438c7680e6fb90fa0e6044ec1774"
        historical_state_commit = "0c943edde8a247b8670e10339b80fc65be6c0f33"
        runtime_schema = QUALIFICATION["state_event_schema_sha256"]
        complete_ledger_schema = (
            "2d19515da1b0798f00dd3e9809c3a2770fee8b27ce6323ac9b9e827db4c7ea27"
        )

        # The deployed owner APIs remain rollback-qualified against their
        # unchanged runtime projection, now read from the current State commit.
        self.assertEqual(QUALIFICATION["state_main_commit"], state_commit)
        self.assertEqual(WORKER_APP.count(f'"{runtime_schema}"'), 1)
        self.assertEqual(DEPLOY.count(runtime_schema), 2)

        # The offline finalizer remains bound to the reviewed historical
        # snapshot. Its commit is distinct from the rollback-qualified runtime
        # projection even when both commits retain the same event schema.
        self.assertNotEqual(state_commit, historical_state_commit)
        self.assertIn(
            f'STATE_COMMIT = "{historical_state_commit}"',
            HISTORICAL_AUTHORITY_PREPARATION,
        )
        self.assertIn(
            f'STATE_EVENT_SCHEMA_SHA256 = "{complete_ledger_schema}"',
            HISTORICAL_AUTHORITY_PREPARATION,
        )

    def test_disable_recovery_is_derived_protected_and_can_never_enable(self) -> None:
        self.assertIn("workflow_run:", RECOVERY)
        self.assertIn("workflow_dispatch:", RECOVERY)
        self.assertIn('test "$EVENT_REF" = refs/heads/main', RECOVERY)
        self.assertIn("github.event.workflow_run.event == 'push'", RECOVERY)
        self.assertIn("github.event.workflow_run.event == 'workflow_dispatch'", RECOVERY)
        self.assertIn(
            "github.event.workflow_run.head_repository.full_name == github.repository",
            RECOVERY,
        )
        self.assertIn("environment: cloudflare-production", RECOVERY)
        self.assertIn("runs-on: ubuntu-24.04", RECOVERY)
        self.assertIn("actions: read", RECOVERY)
        self.assertIn("contents: read", RECOVERY)
        self.assertNotIn("actions: write", RECOVERY)
        self.assertNotIn("contents: write", RECOVERY)
        self.assertIn("max_by(.run_number)", RECOVERY)
        self.assertIn("head_sha=$LIVE_COMMIT", RECOVERY)
        self.assertIn("github.event.workflow_run.run_attempt", RECOVERY)
        self.assertIn("github.event.workflow_run.head_sha", RECOVERY)
        self.assertIn("github.event.workflow_run.conclusion", RECOVERY)
        self.assertIn("github.event.workflow_run.id", RECOVERY)
        self.assertIn("automatic recovery trigger read-back differs", RECOVERY)
        self.assertIn("branches/main", RECOVERY)
        self.assertIn("protected main", RECOVERY)
        self.assertIn("controller dispatch tag does not resolve exactly", RECOVERY)
        active = RECOVERY.index("Resolve the exact active production intake version")
        mutation = RECOVERY.index(
            "Force the exact controller code to all-false launch mode"
        )
        self.assertLess(active, mutation)
        self.assertEqual(RECOVERY.count("launch-recovery-source"), 2)
        self.assertIn("pre-mutation health unavailable", RECOVERY)
        self.assertIn("steps.active.outputs.needed == 'true'", RECOVERY[mutation:])
        self.assertIn("--target-version \"$EXPECTED_ACTIVE_VERSION\"", RECOVERY[mutation:])
        self.assertNotIn("steps.live.outputs.needed", RECOVERY)
        self.assertNotIn("steps.target.outputs.needed", RECOVERY)
        self.assertIn("success|cancelled|failure|startup_failure|timed_out", RECOVERY)
        self.assertIn('--var "INTAKE_ENABLED:false"', RECOVERY)
        self.assertIn('--var "INTAKE_ENABLEMENT_MODE:disabled"', RECOVERY)
        self.assertIn("--require-intake-disabled", RECOVERY)
        self.assertIn("--require-launch-gates-disabled", RECOVERY)
        self.assertIn("worker_lifecycle_configuration.py", RECOVERY)
        self.assertIn('--var "RELEASE_OPT_IN_API_ENABLED:false"', RECOVERY)
        self.assertIn('--var "RELEASE_OPT_OUT_API_ENABLED:false"', RECOVERY)
        self.assertIn('--var "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED:false"', RECOVERY)
        self.assertIn("all-false-production-health", RECOVERY)
        self.assertIn("for attempt in $(seq 1 13)", RECOVERY)
        self.assertIn('classification" -ne 75', RECOVERY)
        self.assertIn("PRODUCTION_HEALTH_FIELDS", ROLLBACK_VALIDATOR)
        self.assertIn('"release_opt_in_api_enabled": False', ROLLBACK_VALIDATOR)
        self.assertNotIn('cat "$response"', RECOVERY)
        self.assertNotIn("INTAKE_ENABLED:true", RECOVERY)
        self.assertNotIn("intake-finalization-failsafe", RECOVERY)

    def test_old_actions_watchdog_is_fully_retired(self) -> None:
        for workflow in (DEPLOY, ROLLBACK, RECOVERY):
            self.assertNotIn("intake-finalization-failsafe", workflow)

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
        self.assertIn("const intake = currentIntake(env, dependencies);", WORKER_APP)
        self.assertIn("if (!intake.effective) return;", WORKER_APP)
        self.assertIn("new ScheduledSubrequestBudget()", WORKER_APP)
        self.assertIn("DISPATCH_OUTBOX_SCAN_LIMIT", WORKER_APP)
        self.assertEqual(staging["limits"], {"subrequests": 400})
        self.assertEqual(production["limits"], {"subrequests": 400})
        self.assertIn("reconcilePromotionCanariesScheduled", WORKER_APP)
        self.assertIn('env.DEPLOYMENT_ENVIRONMENT === "staging"', WORKER_APP)
        self.assertEqual(production["vars"]["PROMOTION_CANARY_ENABLED"], "false")

    def test_private_brokers_are_bound_and_deployed_before_intake_workers(self) -> None:
        for environment in ("staging", "production"):
            with self.subTest(environment=environment):
                service = WRANGLER["env"][environment]["services"]
                expected_services = [
                    {
                        "binding": "GITHUB_BROKER",
                        "service": f"lean-eval-github-broker-{environment}",
                    }
                ]
                self.assertEqual(service, expected_services)
                broker = BROKER_WRANGLER["env"][environment]
                self.assertIs(broker["workers_dev"], False)
                self.assertIs(broker["preview_urls"], False)
                self.assertNotIn("routes", broker)
                self.assertNotIn("SOURCE_APP_PRIVATE_KEY", broker["vars"])
                self.assertNotIn("DISPATCH_APP_PRIVATE_KEY", broker["vars"])
                self.assertNotIn("LEGACY_SOURCE_APP_PRIVATE_KEY", broker["vars"])
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
            "LEGACY_SOURCE_APP_ID",
            "LEGACY_SOURCE_APP_PRIVATE_KEY",
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

    def test_temporary_workers_dev_routes_have_reviewed_intake_state(self) -> None:
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
                expected_lifecycle = (
                    "true" if environment == "production" else "false"
                )
                expected_maintainers = (
                    '[{"github_id":477956,"login":"kim-em"}]'
                    if environment == "production"
                    else "[]"
                )
                self.assertIs(configuration["workers_dev"], True)
                self.assertIs(configuration["preview_urls"], False)
                self.assertNotIn("routes", configuration)
                expected_intake = "true" if environment == "production" else "false"
                self.assertEqual(
                    configuration["vars"]["INTAKE_ENABLED"], expected_intake
                )
                self.assertEqual(
                    configuration["vars"]["LEGACY_RESULT_OWNER_API_ENABLED"],
                    expected_lifecycle,
                )
                self.assertEqual(
                    configuration["vars"]["RESULT_AMENDMENT_OWNER_API_ENABLED"],
                    expected_lifecycle,
                )
                self.assertEqual(
                    configuration["vars"]["RESULT_AMENDMENT_MAINTAINER_API_ENABLED"],
                    expected_lifecycle,
                )
                self.assertEqual(
                    configuration["vars"]["RESULT_AMENDMENT_MAINTAINERS"],
                    expected_maintainers,
                )
                self.assertEqual(
                    configuration["vars"]["MODEL_IDENTITY_OWNER_API_ENABLED"],
                    expected_lifecycle,
                )
                self.assertEqual(
                    configuration["vars"]["MODEL_IDENTITY_MAINTAINER_API_ENABLED"],
                    expected_lifecycle,
                )
                self.assertEqual(
                    configuration["vars"]["MODEL_IDENTITY_MAINTAINERS"],
                    expected_maintainers,
                )
                self.assertEqual(
                    configuration["vars"]["MODEL_IDENTITY_CONSOLIDATION_API_ENABLED"],
                    "false",
                )
                self.assertEqual(
                    configuration["vars"]["RELEASE_OPT_IN_API_ENABLED"],
                    expected_lifecycle,
                )
                self.assertEqual(
                    configuration["vars"]["RELEASE_OPT_OUT_API_ENABLED"],
                    "false",
                )
                expected_contract = (
                    "6105a6255ec40409bcce66c6cf6b6764e0e93ed4"
                    if environment == "staging"
                    else "235a96c96462438c7680e6fb90fa0e6044ec1774"
                )
                self.assertEqual(
                    configuration["vars"]["RESULT_OWNER_STATE_CONTRACT_COMMIT"],
                    expected_contract,
                )
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

    def test_protected_deploy_converges_legacy_source_credentials_without_logging_values(
        self,
    ) -> None:
        for environment in ("staging", "production"):
            with self.subTest(environment=environment):
                name = f"Converge {environment} legacy-source broker credentials"
                self.assertEqual(DEPLOY.count(name), 1)
                block = DEPLOY.split(f"- name: {name}", 1)[1].split(
                    f"- name: Deploy {environment} GitHub broker", 1
                )[0]
                self.assertIn(
                    "LEGACY_SOURCE_APP_ID: ${{ secrets.LEAN_EVAL_BOT_CLIENT_ID }}",
                    block,
                )
                self.assertIn(
                    "LEGACY_SOURCE_APP_PRIVATE_KEY: ${{ secrets.LEAN_EVAL_BOT_PRIVATE_KEY }}",
                    block,
                )
                self.assertIn(
                    'if [ "$LEGACY_SOURCE_APP_ID" != "Iv23liLATwL7VxAK37uX" ]',
                    block,
                )
                self.assertIn(
                    "printf '%s' \"$LEGACY_SOURCE_APP_ID\" |", block
                )
                self.assertIn(
                    "printf '%s' \"$LEGACY_SOURCE_APP_PRIVATE_KEY\" |", block
                )
                self.assertIn("wrangler secret put", block)
                self.assertIn("--config wrangler.broker.jsonc", block)
                self.assertIn(f"--env {environment}", block)
                self.assertNotIn("set -x", block)
                self.assertNotIn("echo \"$LEGACY_SOURCE", block)
        production = DEPLOY.split("\n  deploy-production:", 1)[1]
        self.assertLess(
            production.index("Provisionally deploy production intake disabled"),
            production.index("Converge production legacy-source broker credentials"),
        )

    def test_intake_and_canary_share_dual_app_exact_commit_admission(self) -> None:
        self.assertEqual(WORKER_APP.count(".submissionRepository("), 2)
        self.assertIn(
            'githubBrokerFetch(env.GITHUB_BROKER, "source")', WORKER_APP
        )
        self.assertIn(
            'githubBrokerFetch(env.GITHUB_BROKER, "legacy_source")', WORKER_APP
        )
        self.assertIn("PROMOTION_CANARY_SOURCE_COMMIT", WORKER_APP)
        self.assertIn(
            'const PROMOTION_CANARY_REPOSITORY = "kim-em/lean-eval-intake-fixture"',
            WORKER_APP,
        )
        self.assertIn(
            'const PROMOTION_CANARY_SOURCE_COMMIT = "ae38f4d3e4ad2991212135435f54e6640bcc89e7"',
            WORKER_APP,
        )
        self.assertIn('/git/commits/${expectedCommit}', WORKER_PROVIDER)
        self.assertIn('type Authority = "source" | "legacy_source"', WORKER_BROKER)
        self.assertIn('const SOURCE_APP_ID = "4666604"', WORKER_BROKER)
        self.assertIn(
            'const LEGACY_SOURCE_APP_ID = "Iv23liLATwL7VxAK37uX"',
            WORKER_BROKER,
        )
        self.assertIn('const DISPATCH_APP_ID = "4666633"', WORKER_BROKER)
        self.assertIn(
            'request.authority === "legacy_source" && (tagRef || annotatedTag)',
            WORKER_BROKER,
        )

    def test_owner_routes_have_no_full_ledger_scan(self) -> None:
        self.assertNotIn("readEvents", WORKER_APP)
        self.assertIn("ledger.readSubmission(match[1])", WORKER_APP)


if __name__ == "__main__":
    unittest.main()
