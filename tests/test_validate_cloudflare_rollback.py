import argparse
import hashlib
import json
import pathlib
import shutil
import tempfile
import unittest

from scripts import validate_cloudflare_rollback as rollback

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
PROTECTED_STATE_COMMIT = "9cf3b4999bae2b6faaa32ff1bf5f040c5e6f787f"
STATE_SCHEMA_SHA256 = "2d19515da1b0798f00dd3e9809c3a2770fee8b27ce6323ac9b9e827db4c7ea27"
INTAKE_VERSION = "11111111-1111-1111-1111-111111111111"
BROKER_VERSION = "22222222-2222-2222-2222-222222222222"
REPLAY_VERSION = "33333333-3333-3333-3333-333333333333"


class RollbackContractCoverageTests(unittest.TestCase):
    def test_qualifies_result_owner_runtime(self) -> None:
        self.assertIn("server/src/result-owner.ts", rollback.CALLBACK_CONTRACT_FILES)

    def test_qualifies_results_provider_verifier(self) -> None:
        self.assertIn("server/src/github-provider.ts", rollback.CALLBACK_CONTRACT_FILES)

    def test_qualifies_owner_request_decoder(self) -> None:
        self.assertIn("server/src/api-contract.ts", rollback.CALLBACK_CONTRACT_FILES)

    def test_qualifies_broker_client_boundary(self) -> None:
        self.assertIn(
            "server/src/github-broker-client.ts", rollback.CALLBACK_CONTRACT_FILES
        )

    def test_qualifies_broker_allowlist(self) -> None:
        self.assertIn("server/src/github-broker.ts", rollback.CALLBACK_CONTRACT_FILES)

    def test_qualifies_owner_authentication_boundary(self) -> None:
        self.assertIn("server/src/auth.ts", rollback.CALLBACK_CONTRACT_FILES)

    def test_qualifies_maintainer_authentication_boundary(self) -> None:
        self.assertIn("server/src/maintainer.ts", rollback.CALLBACK_CONTRACT_FILES)

    def test_qualifies_scheduled_subrequest_guard(self) -> None:
        self.assertIn(
            "server/src/scheduled-subrequest-budget.ts",
            rollback.CALLBACK_CONTRACT_FILES,
        )

    def test_qualification_paths_are_deterministically_sorted(self) -> None:
        self.assertEqual(
            rollback.CALLBACK_CONTRACT_FILES,
            sorted(rollback.CALLBACK_CONTRACT_FILES),
        )

    def test_qualification_paths_are_unique(self) -> None:
        self.assertEqual(
            len(rollback.CALLBACK_CONTRACT_FILES),
            len(set(rollback.CALLBACK_CONTRACT_FILES)),
        )

    def test_qualification_paths_are_bounded_typescript_sources(self) -> None:
        for relative in rollback.CALLBACK_CONTRACT_FILES:
            self.assertTrue(relative.startswith("server/src/"))
            self.assertTrue(relative.endswith(".ts"))

    def test_every_qualified_source_is_nonempty(self) -> None:
        for relative in rollback.CALLBACK_CONTRACT_FILES:
            self.assertGreater((ROOT / relative).stat().st_size, 0)

    def test_removed_canary_is_not_part_of_the_current_contract(self) -> None:
        self.assertNotIn(
            "server/src/staging-amendment-canary.ts",
            rollback.CALLBACK_CONTRACT_FILES,
        )

    def test_canary_qualified_file_set_remains_supported_for_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = pathlib.Path(temporary)
            for relative in [
                *rollback.CALLBACK_CONTRACT_FILES,
                "server/package.json",
                "server/package-lock.json",
            ]:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            canary = target / "server/src/staging-amendment-canary.ts"
            canary.write_text("export const rollbackCanaryFixture = true;\n", encoding="utf-8")
            qualification = json.loads(
                (ROOT / ".audit" / "cloudflare-rollback-qualification-v1.json").read_text(
                    encoding="utf-8"
                )
            )
            qualification["lifecycle_callback_contract_files"] = (
                rollback.CANARY_CALLBACK_CONTRACT_FILES
            )
            qualification["lifecycle_callback_contract_sha256"] = (
                rollback._callback_contract_digest(
                    target, rollback.CANARY_CALLBACK_CONTRACT_FILES
                )
            )
            self.assertEqual(
                rollback._validate_qualification_header(qualification, target),
                qualification["lifecycle_callback_contract_sha256"],
            )

    def test_canary_target_cannot_omit_canary_callback_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = pathlib.Path(temporary)
            for relative in [
                *rollback.CALLBACK_CONTRACT_FILES,
                "server/package.json",
                "server/package-lock.json",
            ]:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            canary = target / "server/src/staging-amendment-canary.ts"
            canary.write_text("export const rollbackCanaryFixture = true;\n", encoding="utf-8")
            qualification = json.loads(
                (ROOT / ".audit" / "cloudflare-rollback-qualification-v1.json").read_text(
                    encoding="utf-8"
                )
            )
            qualification["lifecycle_callback_contract_sha256"] = (
                rollback._callback_contract_digest(target)
            )
            with self.assertRaisesRegex(
                rollback.RollbackValidationError,
                "does not cover its exact callback contract files",
            ):
                rollback._validate_qualification_header(qualification, target)

    def test_current_target_cannot_claim_canary_callback_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = pathlib.Path(temporary)
            for relative in [
                *rollback.CALLBACK_CONTRACT_FILES,
                "server/package.json",
                "server/package-lock.json",
            ]:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            qualification = json.loads(
                (ROOT / ".audit" / "cloudflare-rollback-qualification-v1.json").read_text(
                    encoding="utf-8"
                )
            )
            qualification["lifecycle_callback_contract_files"] = (
                rollback.CANARY_CALLBACK_CONTRACT_FILES
            )
            with self.assertRaisesRegex(
                rollback.RollbackValidationError,
                "does not cover its exact callback contract files",
            ):
                rollback._validate_qualification_header(qualification, target)


class ProductionConvergenceClassificationTests(unittest.TestCase):
    def readiness(self) -> dict[str, object]:
        return {
            "environment": "production",
            "intake_configured_enabled": False,
            "intake_effective_enabled": False,
            "intake_enabled": False,
            "intake_enablement_mode": "disabled",
            "intake_lease_expires_at": None,
            "state_branch_protected": True,
            "state_commit": PROTECTED_STATE_COMMIT,
            "state_contract_commit": PROTECTED_STATE_COMMIT,
            "state_contract_verified": True,
            "state_event_schema_sha256": STATE_SCHEMA_SHA256,
            "status": "state_writer_ready",
        }

    def health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "service": "lean-eval-submission",
            "deployed_commit": COMMIT,
            "environment": "production",
            "intake_configured_enabled": False,
            "intake_effective_enabled": False,
            "intake_enabled": False,
            "intake_enablement_mode": "disabled",
            "intake_lease_expires_at": None,
            "legacy_result_owner_api_enabled": False,
            "result_amendment_owner_api_enabled": False,
            "result_amendment_maintainer_api_enabled": False,
            "model_identity_owner_api_enabled": False,
            "model_identity_maintainer_api_enabled": False,
            "model_identity_consolidation_api_enabled": False,
            "model_identity_write_max_subrequests": 400,
            "model_identity_consolidation_api": "atomic_reverse_impact_v1",
            "release_opt_in_api_enabled": False,
            "release_opt_out_api_enabled": False,
            "promotion_canary_configured_enabled": False,
            "promotion_canary_enabled": False,
        }

    def test_accepts_only_the_exact_closed_state_readiness_proof(self) -> None:
        self.assertEqual(
            rollback.classify_production_state_readiness(
                self.readiness(),
                200,
                PROTECTED_STATE_COMMIT,
                STATE_SCHEMA_SHA256,
            ),
            PROTECTED_STATE_COMMIT,
        )
        malformed = self.readiness()
        malformed["unexpected"] = True
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "not a closed document"
        ):
            rollback.classify_production_state_readiness(
                malformed,
                200,
                PROTECTED_STATE_COMMIT,
                STATE_SCHEMA_SHA256,
            )

    def test_retries_only_closed_state_unavailable(self) -> None:
        self.assertIsNone(
            rollback.classify_production_state_readiness(
                {"status": "not_ready", "reason": "state_unavailable"},
                503,
                PROTECTED_STATE_COMMIT,
                STATE_SCHEMA_SHA256,
            )
        )
        fatal_responses = [
            (503, {"status": "not_ready", "reason": "state_credential_missing"}),
            (
                503,
                {
                    "status": "not_ready",
                    "reason": "state_unavailable",
                    "detail": "private",
                },
            ),
            (503, {"status": "not_ready", "reason": "unexpected"}),
            (404, {"error": "not_found"}),
            (500, {"status": "not_ready", "reason": "state_unavailable"}),
        ]
        for status, response in fatal_responses:
            with self.subTest(status=status, response=response), self.assertRaises(
                rollback.RollbackValidationError
            ):
                rollback.classify_production_state_readiness(
                    response,
                    status,
                    PROTECTED_STATE_COMMIT,
                    STATE_SCHEMA_SHA256,
                )

    def test_accepts_exact_all_false_health_and_retries_valid_stale_health(self) -> None:
        self.assertTrue(
            rollback.classify_all_false_production_health(
                self.health(), 200, COMMIT
            )
        )
        stale = self.health()
        stale["legacy_result_owner_api_enabled"] = True
        self.assertFalse(
            rollback.classify_all_false_production_health(stale, 200, COMMIT)
        )
        stale_commit = self.health()
        stale_commit["deployed_commit"] = "b" * 40
        self.assertFalse(
            rollback.classify_all_false_production_health(
                stale_commit, 200, COMMIT
            )
        )
        durable = self.health()
        durable["intake_configured_enabled"] = True
        durable["intake_effective_enabled"] = True
        durable["intake_enabled"] = True
        durable["intake_enablement_mode"] = "durable"
        self.assertFalse(
            rollback.classify_all_false_production_health(durable, 200, COMMIT)
        )

    def test_rejects_malformed_or_non_200_health_without_retry(self) -> None:
        malformed = self.health()
        malformed["unexpected"] = True
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "not a closed document"
        ):
            rollback.classify_all_false_production_health(malformed, 200, COMMIT)
        wrong_type = self.health()
        wrong_type["release_opt_in_api_enabled"] = "false"
        with self.assertRaisesRegex(rollback.RollbackValidationError, "malformed"):
            rollback.classify_all_false_production_health(wrong_type, 200, COMMIT)
        invalid_mode = self.health()
        invalid_mode["intake_enablement_mode"] = "invalid"
        with self.assertRaisesRegex(rollback.RollbackValidationError, "malformed"):
            rollback.classify_all_false_production_health(invalid_mode, 200, COMMIT)
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "unexpected HTTP status"
        ):
            rollback.classify_all_false_production_health(
                {"status": "not_ready", "reason": "state_unavailable"},
                503,
                COMMIT,
            )

class CloudflareRollbackValidationTests(unittest.TestCase):
    def test_current_protected_state_contract_is_coherent_across_rollback_inputs(
        self,
    ) -> None:
        expected = PROTECTED_STATE_COMMIT
        qualification = json.loads(
            (ROOT / ".audit" / "cloudflare-rollback-qualification-v1.json").read_text(
                encoding="utf-8"
            )
        )
        intake = json.loads(
            (ROOT / "server" / "wrangler.jsonc").read_text(encoding="utf-8")
        )["env"]["production"]["vars"]
        self.assertEqual(qualification["state_main_commit"], expected)
        self.assertEqual(intake["RESULT_OWNER_STATE_CONTRACT_COMMIT"], expected)
        self.assertEqual(intake["MODEL_IDENTITY_STATE_CONTRACT_COMMIT"], expected)

        self.configs["intake"]["env"]["production"]["vars"][
            "RESULT_OWNER_STATE_CONTRACT_COMMIT"
        ] = "e" * 40
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "RESULT_OWNER_STATE_CONTRACT_COMMIT":
                binding["text"] = "e" * 40
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError,
            "result owner API contract is not bound",
        ):
            rollback.build_plan(self._arguments())

        variables = self.configs["intake"]["env"]["production"]["vars"]
        variables["RESULT_OWNER_STATE_CONTRACT_COMMIT"] = expected
        variables["MODEL_IDENTITY_STATE_CONTRACT_COMMIT"] = "e" * 40
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "RESULT_OWNER_STATE_CONTRACT_COMMIT":
                binding["text"] = expected
            if binding.get("name") == "MODEL_IDENTITY_STATE_CONTRACT_COMMIT":
                binding["text"] = "e" * 40
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError,
            "model identity API contract is not bound",
        ):
            rollback.build_plan(self._arguments())

    def test_repository_qualification_matches_runtime_schema_and_pause_guard(
        self,
    ) -> None:
        qualification = json.loads(
            (ROOT / ".audit" / "cloudflare-rollback-qualification-v1.json").read_text(
                encoding="utf-8"
            )
        )
        for name, expected in rollback.QUALIFICATION_FIXED.items():
            self.assertEqual(qualification[name], expected)
        self.assertEqual(set(qualification), rollback.QUALIFICATION_FIELDS)
        self.assertEqual(
            qualification["lifecycle_callback_contract_files"],
            rollback.CALLBACK_CONTRACT_FILES,
        )
        self.assertEqual(
            qualification["lifecycle_callback_contract_sha256"],
            rollback._callback_contract_digest(ROOT),
        )
        state_event = (ROOT / "server" / "src" / "state-event.ts").read_text(
            encoding="utf-8"
        )
        app = (ROOT / "server" / "src" / "app.ts").read_text(encoding="utf-8")
        self.assertIn("STATE_EVENT_SCHEMA_VERSION = 1 as const", state_event)
        self.assertIn("const intake = currentIntake(env, dependencies);", app)
        self.assertIn("if (!intake.effective) return;", app)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = pathlib.Path(self.temporary.name)
        self.configs = {
            name: json.loads((ROOT / "server" / filename).read_text(encoding="utf-8"))
            for name, filename in {
                "intake": "wrangler.jsonc",
                "broker": "wrangler.broker.jsonc",
                "replay": "wrangler.replay.jsonc",
            }.items()
        }
        # These fixtures model an emergency rollback target, independently of
        # the capabilities enabled in the repository's current launch config.
        self.configs["intake"]["env"]["production"]["vars"].update(
            {
                "INTAKE_ENABLED": "false",
                "INTAKE_ENABLEMENT_MODE": "disabled",
                **rollback.DISABLED_LAUNCH_GATE_BINDINGS,
            }
        )
        self.paths: dict[str, pathlib.Path] = {}
        for name, config in self.configs.items():
            path = self.directory / f"{name}-config.json"
            self._write(path, config)
            self.paths[f"{name}_config"] = path

        qualification_value = json.loads(
            (ROOT / ".audit" / "cloudflare-rollback-qualification-v1.json").read_text(
                encoding="utf-8"
            )
        )
        state_schema_raw = b'{"fixture":"exact State schema"}\n'
        state_commit = PROTECTED_STATE_COMMIT
        qualification_value["state_main_commit"] = state_commit
        qualification_value["state_event_schema_sha256"] = hashlib.sha256(
            state_schema_raw
        ).hexdigest()
        qualification_value["lifecycle_callback_contract_sha256"] = (
            rollback._callback_contract_digest(ROOT)
        )
        qualification = self.directory / "qualification.json"
        self._write(qualification, qualification_value)
        self.paths["qualification"] = qualification
        state_main = self.directory / "state-main.json"
        self._write(state_main, {"commit": state_commit, "protected": True})
        self.paths["state_main"] = state_main
        state_schema = self.directory / "state-event-schema.json"
        state_schema.write_bytes(state_schema_raw)
        self.paths["state_schema"] = state_schema
        state_proof = self.directory / "state-proof.json"
        self._write(
            state_proof,
            {
                "environment": "production",
                "intake_configured_enabled": False,
                "intake_effective_enabled": False,
                "intake_enabled": False,
                "intake_enablement_mode": "disabled",
                "intake_lease_expires_at": None,
                "state_branch_protected": True,
                "state_commit": state_commit,
                "state_contract_commit": state_commit,
                "state_contract_verified": True,
                "state_event_schema_sha256": qualification_value[
                    "state_event_schema_sha256"
                ],
                "status": "state_writer_ready",
            },
        )
        self.paths["state_proof"] = state_proof

        intake_bindings = self._variable_bindings("intake")
        intake_bindings.append(
            {
                "type": "service",
                "name": "GITHUB_BROKER",
                "service": "lean-eval-github-broker-production",
                "environment": "production",
            }
        )
        intake = self.configs["intake"]["env"]["production"]
        for rate in intake["ratelimits"]:
            intake_bindings.append(
                {
                    "type": "ratelimit",
                    "name": rate["name"],
                    "namespace_id": rate["namespace_id"],
                    "simple": rate["simple"],
                }
            )
        intake_bindings.extend(
            {"type": "secret_text", "name": name}
            for name in rollback.SECRET_BINDINGS["intake"]
        )
        replay_bindings = self._variable_bindings("replay")
        replay_bindings.extend(
            {
                "type": "durable_object_namespace",
                "name": binding["name"],
                "class_name": binding["class_name"],
                "namespace_id": f"{index + 1:032x}",
            }
            for index, binding in enumerate(
                self.configs["replay"]["env"]["production"]["durable_objects"][
                    "bindings"
                ]
            )
        )
        broker_bindings = self._variable_bindings("broker")
        broker_bindings.extend(
            {"type": "secret_text", "name": name}
            for name in rollback.SECRET_BINDINGS["broker"]
        )
        self.versions = {
            "intake": {
                "id": INTAKE_VERSION,
                "resources": {"bindings": intake_bindings},
            },
            "broker": {
                "id": BROKER_VERSION,
                "resources": {"bindings": broker_bindings},
            },
            "replay": {
                "id": REPLAY_VERSION,
                "resources": {
                    "bindings": replay_bindings,
                    "script_runtime": {
                        "migration_tag": "v2",
                        "containers": [
                            {
                                "class_name": "ReplaySandbox",
                                "name": (
                                    "lean-eval-replay-executor-replaysandbox-production"
                                ),
                            }
                        ],
                    },
                },
            },
        }
        for name, version in self.versions.items():
            path = self.directory / f"{name}-version.json"
            self._write(path, version)
            self.paths[f"{name}_version"] = path

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write(path: pathlib.Path, value: object) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def _variable_bindings(self, component: str) -> list[dict[str, str]]:
        variables = dict(self.configs[component]["env"]["production"]["vars"])
        variables["DEPLOYED_COMMIT"] = COMMIT
        if component == "intake":
            variables["DISPATCH_WORKFLOW_REF"] = f"lean-eval-dispatch/{COMMIT}"
        return [
            {"type": "plain_text", "name": name, "text": value}
            for name, value in variables.items()
        ]

    def _arguments(self) -> argparse.Namespace:
        return argparse.Namespace(
            expected_commit=COMMIT,
            environment="production",
            intake_config=self.paths["intake_config"],
            broker_config=self.paths["broker_config"],
            replay_config=self.paths["replay_config"],
            intake_version=self.paths["intake_version"],
            broker_version=self.paths["broker_version"],
            replay_version=self.paths["replay_version"],
            intake_version_id=INTAKE_VERSION,
            broker_version_id=BROKER_VERSION,
            replay_version_id=REPLAY_VERSION,
            require_intake_disabled=False,
            require_replay_disabled=True,
            current_replay_config=self.paths["replay_config"],
            qualification=self.paths["qualification"],
            target_root=ROOT,
            state_main=self.paths["state_main"],
            state_schema=self.paths["state_schema"],
            state_proof=None,
        )

    def _proof_arguments(self) -> argparse.Namespace:
        arguments = self._arguments()
        arguments.state_main = None
        arguments.state_schema = None
        arguments.state_proof = self.paths["state_proof"]
        return arguments

    @staticmethod
    def _active_intake_status() -> dict[str, object]:
        return {
            "versions": [
                {"version_id": INTAKE_VERSION, "percentage": 100}
            ]
        }

    def test_launch_recovery_source_is_exact_and_all_false(self) -> None:
        source = rollback.launch_recovery_source(
            self._active_intake_status(),
            self.versions["intake"],
            config=self.configs["intake"],
            expected_commit=COMMIT,
        )
        self.assertEqual(
            source,
            {
                "schema_version": 1,
                "version_id": INTAKE_VERSION,
                "deployed_commit": COMMIT,
                "needed": False,
            },
        )

    def test_launch_recovery_source_arms_for_drift_or_residual_lease(self) -> None:
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED":
                binding["text"] = "true"
        source = rollback.launch_recovery_source(
            self._active_intake_status(), self.versions["intake"]
        )
        self.assertIs(source["needed"], True)

        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED":
                binding["text"] = "false"
        self.versions["intake"]["resources"]["bindings"].append(
            {
                "type": "plain_text",
                "name": "INTAKE_LEASE_EXPIRES_AT",
                "text": "1787710000",
            }
        )
        source = rollback.launch_recovery_source(
            self._active_intake_status(), self.versions["intake"]
        )
        self.assertIs(source["needed"], True)

    def test_launch_recovery_source_rejects_ambiguous_or_mixed_versions(self) -> None:
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "one active version"
        ):
            rollback.launch_recovery_source(
                {
                    "versions": [
                        {"version_id": INTAKE_VERSION, "percentage": 50},
                        {"version_id": BROKER_VERSION, "percentage": 50},
                    ]
                },
                self.versions["intake"],
            )
        self.versions["intake"]["id"] = BROKER_VERSION
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "version payload differ"
        ):
            rollback.launch_recovery_source(
                self._active_intake_status(), self.versions["intake"]
            )

    def test_launch_recovery_source_requires_exact_commit_ref_and_capabilities(
        self,
    ) -> None:
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "DISPATCH_WORKFLOW_REF":
                binding["text"] = f"lean-eval-dispatch/{'b' * 40}"
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "exact dispatch ref"
        ):
            rollback.launch_recovery_source(
                self._active_intake_status(), self.versions["intake"]
            )
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "DISPATCH_WORKFLOW_REF":
                binding["text"] = f"lean-eval-dispatch/{COMMIT}"
            if binding.get("type") == "service":
                binding["service"] = "wrong-broker"
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "service bindings differ"
        ):
            rollback.launch_recovery_source(
                self._active_intake_status(),
                self.versions["intake"],
                config=self.configs["intake"],
                expected_commit=COMMIT,
            )

    def test_builds_exact_commit_coherent_plan(self) -> None:
        plan = rollback.build_plan(self._arguments())
        self.assertEqual(plan["expected_commit"], COMMIT)
        self.assertEqual(
            plan["version_ids"],
            {
                "intake": INTAKE_VERSION,
                "broker": BROKER_VERSION,
                "replay": REPLAY_VERSION,
            },
        )
        self.assertIs(plan["intake_enabled"], False)
        self.assertIs(plan["legacy_result_owner_api_contract_supported"], True)
        self.assertIs(plan["legacy_result_owner_api_enabled"], False)
        self.assertIs(plan["result_amendment_owner_api_contract_supported"], True)
        self.assertIs(plan["result_amendment_owner_api_enabled"], False)
        self.assertIs(plan["result_amendment_maintainer_api_contract_supported"], True)
        self.assertIs(plan["result_amendment_maintainer_api_enabled"], False)
        self.assertIs(plan["model_identity_owner_api_contract_supported"], True)
        self.assertIs(plan["model_identity_owner_api_enabled"], False)
        self.assertIs(plan["model_identity_maintainer_api_contract_supported"], True)
        self.assertIs(plan["model_identity_maintainer_api_enabled"], False)
        self.assertIs(plan["model_identity_consolidation_api_contract_supported"], True)
        self.assertIs(plan["model_identity_consolidation_api_enabled"], False)
        self.assertIs(plan["release_opt_in_api_contract_supported"], True)
        self.assertIs(plan["release_opt_in_api_enabled"], False)
        self.assertIs(plan["release_opt_out_api_contract_supported"], True)
        self.assertIs(plan["release_opt_out_api_enabled"], False)
        self.assertEqual(plan["model_identity_state_contract_commit"], PROTECTED_STATE_COMMIT)
        self.assertNotIn("MODEL_IDENTITY_MAINTAINERS", plan)
        self.assertNotIn("RESULT_AMENDMENT_MAINTAINERS", plan)
        self.assertEqual(
            plan["result_owner_state_contract_commit"],
            PROTECTED_STATE_COMMIT,
        )
        self.assertIs(plan["promotion_canary_enabled"], False)
        self.assertIs(plan["replay_enabled"], False)

    def test_builds_the_same_plan_from_a_closed_worker_state_proof(self) -> None:
        legacy = rollback.build_plan(self._arguments())
        proof = rollback.build_plan(self._proof_arguments())
        self.assertEqual(proof, legacy)

    def test_model_identity_gate_requires_closed_identity_pair_pin_and_limit(self) -> None:
        variables = self.configs["intake"]["env"]["production"]["vars"]
        del variables["MODEL_IDENTITY_MAINTAINERS"]
        self._write(self.paths["intake_config"], self.configs["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError,
            "intake plain-text bindings differ",
        ):
            rollback.build_plan(self._arguments())

        variables["MODEL_IDENTITY_MAINTAINERS"] = "[]"
        self.configs["intake"]["env"]["production"]["limits"] = {
            "subrequests": 399
        }
        self._write(self.paths["intake_config"], self.configs["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "exact 400-subrequest Worker limit"
        ):
            rollback.build_plan(self._arguments())

    def test_worker_state_proof_rejects_drift_enablement_and_extra_fields(self) -> None:
        original = rollback._object(self.paths["state_proof"])
        hostile = [
            {**original, "state_branch_protected": False},
            {**original, "state_contract_verified": False},
            {**original, "state_commit": "e" * 40},
            {**original, "state_event_schema_sha256": "e" * 64},
            {**original, "intake_effective_enabled": True},
            {**original, "private_detail": "must not be accepted"},
        ]
        for proof in hostile:
            with self.subTest(proof=proof):
                self._write(self.paths["state_proof"], proof)
                with self.assertRaises(rollback.RollbackValidationError):
                    rollback.build_plan(self._proof_arguments())
        self._write(self.paths["state_proof"], original)

    def test_rejects_production_rollback_with_promotion_canary_enabled(self) -> None:
        self.configs["intake"]["env"]["production"]["vars"][
            "PROMOTION_CANARY_ENABLED"
        ] = "true"
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "PROMOTION_CANARY_ENABLED":
                binding["text"] = "true"
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError,
            "must disable result owner APIs, promotion canary",
        ):
            rollback.build_plan(self._arguments())

    def test_rejects_production_rollback_with_owner_api_enabled(self) -> None:
        self.configs["intake"]["env"]["production"]["vars"][
            "LEGACY_RESULT_OWNER_API_ENABLED"
        ] = "true"
        self.configs["intake"]["env"]["production"]["vars"][
            "RESULT_OWNER_STATE_CONTRACT_COMMIT"
        ] = PROTECTED_STATE_COMMIT
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "LEGACY_RESULT_OWNER_API_ENABLED":
                binding["text"] = "true"
            if binding.get("name") == "RESULT_OWNER_STATE_CONTRACT_COMMIT":
                binding["text"] = PROTECTED_STATE_COMMIT
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "must disable result owner APIs"
        ):
            rollback.build_plan(self._arguments())

    def test_rejects_production_rollback_with_amendment_owner_api_enabled(self) -> None:
        self.configs["intake"]["env"]["production"]["vars"][
            "RESULT_AMENDMENT_OWNER_API_ENABLED"
        ] = "true"
        self.configs["intake"]["env"]["production"]["vars"][
            "RESULT_OWNER_STATE_CONTRACT_COMMIT"
        ] = PROTECTED_STATE_COMMIT
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "RESULT_AMENDMENT_OWNER_API_ENABLED":
                binding["text"] = "true"
            if binding.get("name") == "RESULT_OWNER_STATE_CONTRACT_COMMIT":
                binding["text"] = PROTECTED_STATE_COMMIT
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "must disable result owner APIs"
        ):
            rollback.build_plan(self._arguments())

    def test_rejects_production_rollback_with_amendment_maintainer_api_enabled(
        self,
    ) -> None:
        self.configs["intake"]["env"]["production"]["vars"][
            "RESULT_AMENDMENT_MAINTAINER_API_ENABLED"
        ] = "true"
        self.configs["intake"]["env"]["production"]["vars"][
            "RESULT_OWNER_STATE_CONTRACT_COMMIT"
        ] = PROTECTED_STATE_COMMIT
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "RESULT_AMENDMENT_MAINTAINER_API_ENABLED":
                binding["text"] = "true"
            if binding.get("name") == "RESULT_OWNER_STATE_CONTRACT_COMMIT":
                binding["text"] = PROTECTED_STATE_COMMIT
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "must disable result owner APIs"
        ):
            rollback.build_plan(self._arguments())

    def test_maintainer_gate_is_closed_and_identities_fail_closed(self) -> None:
        variables = self.configs["intake"]["env"]["production"]["vars"]
        del variables["RESULT_AMENDMENT_MAINTAINERS"]
        self._write(self.paths["intake_config"], self.configs["intake"])
        self.versions["intake"]["resources"]["bindings"] = [
            binding
            for binding in self.versions["intake"]["resources"]["bindings"]
            if binding.get("name") != "RESULT_AMENDMENT_MAINTAINERS"
        ]
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "gate, identities, and State contract pin"
        ):
            rollback.build_plan(self._arguments())

    def test_malformed_maintainer_identities_fail_closed(self) -> None:
        for malformed in (
            "not-json",
            '[{"github_id":1,"login":"Maintainer"}]',
            '[{"github_id":1,"login":"maintainer-١"}]',
            '[{"github_id":1,"login":"maintainer","role":"admin"}]',
            '[{"github_id":1,"login":"maintainer"},{"github_id":1,"login":"other"}]',
        ):
            with self.subTest(malformed=malformed):
                self.configs["intake"]["env"]["production"]["vars"][
                    "RESULT_AMENDMENT_MAINTAINERS"
                ] = malformed
                self._write(self.paths["intake_config"], self.configs["intake"])
                for binding in self.versions["intake"]["resources"]["bindings"]:
                    if binding.get("name") == "RESULT_AMENDMENT_MAINTAINERS":
                        binding["text"] = malformed
                self._write(self.paths["intake_version"], self.versions["intake"])
                with self.assertRaisesRegex(
                    rollback.RollbackValidationError,
                    "RESULT_AMENDMENT_MAINTAINERS",
                ):
                    rollback.build_plan(self._arguments())

    def test_plan_validates_but_does_not_expose_maintainer_identities(self) -> None:
        configured = '[{"github_id":477956,"login":"kim-em"}]'
        self.configs["intake"]["env"]["production"]["vars"][
            "RESULT_AMENDMENT_MAINTAINERS"
        ] = configured
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "RESULT_AMENDMENT_MAINTAINERS":
                binding["text"] = configured
        self._write(self.paths["intake_version"], self.versions["intake"])

        encoded = json.dumps(rollback.build_plan(self._arguments()), sort_keys=True)
        self.assertNotIn("RESULT_AMENDMENT_MAINTAINERS", encoded)
        self.assertNotIn("kim-em", encoded)
        self.assertNotIn("477956", encoded)

    def test_older_target_without_owner_gate_is_safely_disabled(self) -> None:
        for name in (
            "LEGACY_RESULT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_MAINTAINER_API_ENABLED",
            "RESULT_AMENDMENT_MAINTAINERS",
            "RESULT_OWNER_STATE_CONTRACT_COMMIT",
        ):
            del self.configs["intake"]["env"]["production"]["vars"][name]
        self._write(self.paths["intake_config"], self.configs["intake"])
        self.versions["intake"]["resources"]["bindings"] = [
            binding
            for binding in self.versions["intake"]["resources"]["bindings"]
            if binding.get("name")
            not in {
                "LEGACY_RESULT_OWNER_API_ENABLED",
                "RESULT_AMENDMENT_OWNER_API_ENABLED",
                "RESULT_AMENDMENT_MAINTAINER_API_ENABLED",
                "RESULT_AMENDMENT_MAINTAINERS",
                "RESULT_OWNER_STATE_CONTRACT_COMMIT",
            }
        ]
        self._write(self.paths["intake_version"], self.versions["intake"])
        plan = rollback.build_plan(self._arguments())
        self.assertIs(plan["legacy_result_owner_api_contract_supported"], False)
        self.assertIs(plan["legacy_result_owner_api_enabled"], False)
        self.assertIs(plan["result_amendment_owner_api_contract_supported"], False)
        self.assertIs(plan["result_amendment_owner_api_enabled"], False)
        self.assertIs(plan["result_amendment_maintainer_api_contract_supported"], False)
        self.assertIs(plan["result_amendment_maintainer_api_enabled"], False)
        self.assertIsNone(plan["result_owner_state_contract_commit"])
        rollback.validate_health(
            plan,
            "intake",
            {
                "status": "ok",
                "environment": "production",
                "deployed_commit": COMMIT,
                "intake_enabled": False,
                "intake_configured_enabled": False,
                "intake_effective_enabled": False,
                "intake_enablement_mode": "disabled",
                "intake_lease_expires_at": None,
                "model_identity_owner_api_enabled": False,
                "model_identity_maintainer_api_enabled": False,
                "model_identity_consolidation_api_enabled": False,
                "model_identity_write_max_subrequests": 400,
                "model_identity_consolidation_api": "atomic_reverse_impact_v1",
                "release_opt_in_api_enabled": False,
                "release_opt_out_api_enabled": False,
                "promotion_canary_configured_enabled": False,
                "promotion_canary_enabled": False,
            },
        )

    def test_older_target_without_canary_binding_is_safely_disabled(self) -> None:
        del self.configs["intake"]["env"]["production"]["vars"][
            "PROMOTION_CANARY_ENABLED"
        ]
        self._write(self.paths["intake_config"], self.configs["intake"])
        self.versions["intake"]["resources"]["bindings"] = [
            binding
            for binding in self.versions["intake"]["resources"]["bindings"]
            if binding.get("name") != "PROMOTION_CANARY_ENABLED"
        ]
        self._write(self.paths["intake_version"], self.versions["intake"])
        plan = rollback.build_plan(self._arguments())
        self.assertIs(plan["promotion_canary_enabled"], False)
        self.assertIs(plan["promotion_canary_contract_supported"], False)
        rollback.validate_health(
            plan,
            "intake",
            {
                "status": "ok",
                "environment": "production",
                "deployed_commit": COMMIT,
                "intake_configured_enabled": False,
                "intake_effective_enabled": False,
                "intake_enabled": False,
                "intake_enablement_mode": "disabled",
                "intake_lease_expires_at": None,
                "legacy_result_owner_api_enabled": False,
                "result_amendment_owner_api_enabled": False,
                "result_amendment_maintainer_api_enabled": False,
                "model_identity_owner_api_enabled": False,
                "model_identity_maintainer_api_enabled": False,
                "model_identity_consolidation_api_enabled": False,
                "model_identity_write_max_subrequests": 400,
                "model_identity_consolidation_api": "atomic_reverse_impact_v1",
                "release_opt_in_api_enabled": False,
                "release_opt_out_api_enabled": False,
            },
            require_intake_disabled=True,
        )

    def test_legacy_disabled_target_without_enablement_contract_remains_rollbackable(
        self,
    ) -> None:
        del self.configs["intake"]["env"]["production"]["vars"][
            "INTAKE_ENABLEMENT_MODE"
        ]
        self._write(self.paths["intake_config"], self.configs["intake"])
        self.versions["intake"]["resources"]["bindings"] = [
            binding
            for binding in self.versions["intake"]["resources"]["bindings"]
            if binding.get("name") != "INTAKE_ENABLEMENT_MODE"
        ]
        self._write(self.paths["intake_version"], self.versions["intake"])

        plan = rollback.build_plan(self._arguments())
        self.assertIs(plan["intake_enabled"], False)
        self.assertIs(plan["intake_enablement_contract_supported"], False)
        self.assertEqual(plan["intake_enablement_mode"], "disabled")
        rollback.validate_health(
            plan,
            "intake",
            {
                "status": "ok",
                "environment": "production",
                "deployed_commit": COMMIT,
                "intake_enabled": False,
                "legacy_result_owner_api_enabled": False,
                "result_amendment_owner_api_enabled": False,
                "result_amendment_maintainer_api_enabled": False,
                "model_identity_owner_api_enabled": False,
                "model_identity_maintainer_api_enabled": False,
                "model_identity_consolidation_api_enabled": False,
                "model_identity_write_max_subrequests": 400,
                "model_identity_consolidation_api": "atomic_reverse_impact_v1",
                "release_opt_in_api_enabled": False,
                "release_opt_out_api_enabled": False,
                "promotion_canary_configured_enabled": False,
                "promotion_canary_enabled": False,
            },
            require_intake_disabled=True,
        )

    def test_legacy_target_cannot_be_tracked_enabled(self) -> None:
        variables = self.configs["intake"]["env"]["production"]["vars"]
        variables["INTAKE_ENABLED"] = "true"
        del variables["INTAKE_ENABLEMENT_MODE"]
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "INTAKE_ENABLED":
                binding["text"] = "true"
        self.versions["intake"]["resources"]["bindings"] = [
            binding
            for binding in self.versions["intake"]["resources"]["bindings"]
            if binding.get("name") != "INTAKE_ENABLEMENT_MODE"
        ]
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError,
            "legacy intake target cannot be tracked enabled",
        ):
            rollback.build_plan(self._arguments())

    def test_present_malformed_intake_enablement_mode_fails_closed(self) -> None:
        self.configs["intake"]["env"]["production"]["vars"][
            "INTAKE_ENABLEMENT_MODE"
        ] = "Disabled"
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "INTAKE_ENABLEMENT_MODE":
                binding["text"] = "Disabled"
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError,
            "tracked intake enablement state is not closed",
        ):
            rollback.build_plan(self._arguments())

    def test_present_malformed_canary_binding_still_fails_closed(self) -> None:
        self.configs["intake"]["env"]["production"]["vars"][
            "PROMOTION_CANARY_ENABLED"
        ] = "False"
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "PROMOTION_CANARY_ENABLED":
                binding["text"] = "False"
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "PROMOTION_CANARY_ENABLED"
        ):
            rollback.build_plan(self._arguments())

    def test_rejects_one_component_from_a_different_commit(self) -> None:
        self.versions["broker"]["resources"]["bindings"][0]["text"] = "b" * 40
        self._write(self.paths["broker_version"], self.versions["broker"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "broker plain-text bindings differ"
        ):
            rollback.build_plan(self._arguments())

    def test_rejects_extra_plain_binding_and_wrong_private_service(self) -> None:
        clean = json.loads(json.dumps(self.versions["intake"]))
        self.versions["intake"]["resources"]["bindings"].append(
            {"type": "plain_text", "name": "UNREVIEWED", "text": "yes"}
        )
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(rollback.RollbackValidationError, "extra"):
            rollback.build_plan(self._arguments())

        self.versions["intake"] = clean
        service = next(
            binding
            for binding in self.versions["intake"]["resources"]["bindings"]
            if binding.get("type") == "service"
        )
        service["service"] = "some-other-broker"
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "intake service bindings differ"
        ):
            rollback.build_plan(self._arguments())

    def test_requires_one_hundred_percent_exact_deployment(self) -> None:
        rollback.validate_status(
            {"versions": [{"version_id": INTAKE_VERSION, "percentage": 100}]},
            INTAKE_VERSION,
        )
        with self.assertRaisesRegex(rollback.RollbackValidationError, "not 100%"):
            rollback.validate_status(
                {
                    "versions": [
                        {"version_id": INTAKE_VERSION, "percentage": 90},
                        {"version_id": BROKER_VERSION, "percentage": 10},
                    ]
                },
                INTAKE_VERSION,
            )
        self.assertEqual(
            rollback.active_version(
                {"versions": [{"version_id": INTAKE_VERSION, "percentage": 100}]}
            ),
            INTAKE_VERSION,
        )

    def test_leased_component_requires_exact_commit_and_uuidv7_variant(self) -> None:
        lease = {
            "INTAKE_ENABLED": "true",
            "INTAKE_ENABLEMENT_MODE": "leased",
            "INTAKE_LEASE_CONTROLLER_COMMIT": COMMIT,
            "INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT": "2",
            "INTAKE_LEASE_CONTROLLER_RUN_ID": "123",
            "INTAKE_LEASE_EVENT_ID": "0198abcd-1111-7000-8000-000000000001",
            "INTAKE_LEASE_EXPIRES_AT": "1800000900",
            "INTAKE_LEASE_ISSUED_AT": "1800000000",
            "INTAKE_LEASE_NONCE_DIGEST": "b" * 64,
            "INTAKE_LEASE_STATE_COMMIT": "c" * 40,
            "INTAKE_LEASE_TARGET_COMMIT": COMMIT,
        }
        lease_path = self.directory / "intake-lease.env"
        lease_path.write_text(
            "".join(f"{name}={value}\n" for name, value in lease.items()),
            encoding="utf-8",
        )
        version = self.versions["intake"]
        plain = {
            binding["name"]: binding
            for binding in version["resources"]["bindings"]
            if binding.get("type") == "plain_text"
        }
        plain["INTAKE_ENABLED"]["text"] = "true"
        plain["INTAKE_ENABLEMENT_MODE"]["text"] = "leased"
        for name, value in lease.items():
            if name not in plain:
                version["resources"]["bindings"].append(
                    {"type": "plain_text", "name": name, "text": value}
                )
        self._write(self.paths["intake_version"], version)
        arguments = argparse.Namespace(
            component="intake",
            expected_commit=COMMIT,
            environment="production",
            config=self.paths["intake_config"],
            version=self.paths["intake_version"],
            version_id=INTAKE_VERSION,
            require_intake_disabled=False,
            intake_lease_bindings=lease_path,
        )
        rollback.validate_component(arguments)

        lease["INTAKE_LEASE_TARGET_COMMIT"] = "d" * 40
        lease["INTAKE_LEASE_CONTROLLER_COMMIT"] = "d" * 40
        lease_path.write_text(
            "".join(f"{name}={value}\n" for name, value in lease.items()),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "exact deployed commit"
        ):
            rollback.validate_component(arguments)

        lease["INTAKE_LEASE_TARGET_COMMIT"] = COMMIT
        lease["INTAKE_LEASE_CONTROLLER_COMMIT"] = COMMIT
        lease["INTAKE_LEASE_EVENT_ID"] = "0198abcd-1111-7000-7000-000000000001"
        lease_path.write_text(
            "".join(f"{name}={value}\n" for name, value in lease.items()),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(rollback.RollbackValidationError, "UUIDv7"):
            rollback.validate_component(arguments)

    def test_all_false_launch_recovery_overrides_enabled_tracked_state(self) -> None:
        intake = self.configs["intake"]
        variables = intake["env"]["production"]["vars"]
        variables["INTAKE_ENABLED"] = "true"
        variables["INTAKE_ENABLEMENT_MODE"] = "durable"
        for name in rollback.DISABLED_LAUNCH_GATE_BINDINGS:
            if name == "PROMOTION_CANARY_ENABLED":
                variables[name] = "false"
            elif name.endswith("_MAINTAINERS"):
                variables[name] = '[{"github_id":477956,"login":"kim-em"}]'
            elif name == "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED":
                variables[name] = "false"
            else:
                variables[name] = "true"
        self._write(self.paths["intake_config"], intake)

        arguments = argparse.Namespace(
            component="intake",
            expected_commit=COMMIT,
            environment="production",
            config=self.paths["intake_config"],
            version=self.paths["intake_version"],
            version_id=INTAKE_VERSION,
            require_intake_disabled=True,
            require_launch_gates_disabled=True,
            intake_lease_bindings=None,
        )
        rollback.validate_component(arguments)

        arguments.require_intake_disabled = False
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "must also require disabled intake"
        ):
            rollback.validate_component(arguments)

        arguments.require_intake_disabled = True
        version = self.versions["intake"]
        for binding in version["resources"]["bindings"]:
            if binding.get("name") == "RELEASE_OPT_OUT_API_ENABLED":
                binding["text"] = "true"
        self._write(self.paths["intake_version"], version)
        with self.assertRaisesRegex(rollback.RollbackValidationError, "bindings differ"):
            rollback.validate_component(arguments)

    def test_emergency_target_may_track_durable_intake_but_must_disable_replay(
        self,
    ) -> None:
        intake = self.configs["intake"]
        intake["env"]["production"]["vars"]["INTAKE_ENABLED"] = "true"
        intake["env"]["production"]["vars"]["INTAKE_ENABLEMENT_MODE"] = "durable"
        self._write(self.paths["intake_config"], intake)
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "INTAKE_ENABLED":
                binding["text"] = "true"
            if binding.get("name") == "INTAKE_ENABLEMENT_MODE":
                binding["text"] = "durable"
        self._write(self.paths["intake_version"], self.versions["intake"])
        arguments = self._arguments()
        arguments.require_intake_disabled = False
        enabled_plan = rollback.build_plan(arguments)
        self.assertIs(enabled_plan["intake_enabled"], True)
        self.assertEqual(enabled_plan["intake_enablement_mode"], "durable")

        intake["env"]["production"]["vars"]["INTAKE_ENABLED"] = "false"
        intake["env"]["production"]["vars"]["INTAKE_ENABLEMENT_MODE"] = "disabled"
        self._write(self.paths["intake_config"], intake)
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "INTAKE_ENABLED":
                binding["text"] = "false"
            if binding.get("name") == "INTAKE_ENABLEMENT_MODE":
                binding["text"] = "disabled"
        self._write(self.paths["intake_version"], self.versions["intake"])
        replay = self.configs["replay"]
        replay["env"]["production"]["vars"]["REPLAY_ENABLED"] = "true"
        self._write(self.paths["replay_config"], replay)
        for binding in self.versions["replay"]["resources"]["bindings"]:
            if binding.get("name") == "REPLAY_ENABLED":
                binding["text"] = "true"
        self._write(self.paths["replay_version"], self.versions["replay"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError,
            "must disable result owner APIs, promotion canary",
        ):
            rollback.build_plan(arguments)

    def test_rejects_missing_secret_or_changed_durable_object_capability(self) -> None:
        self.versions["broker"]["resources"]["bindings"] = [
            binding
            for binding in self.versions["broker"]["resources"]["bindings"]
            if binding.get("name") != "SOURCE_APP_PRIVATE_KEY"
        ]
        self._write(self.paths["broker_version"], self.versions["broker"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "broker capability bindings differ"
        ):
            rollback.build_plan(self._arguments())

    def test_rejects_unqualified_or_older_durable_object_epoch(self) -> None:
        self._write(self.paths["qualification"], {"schema_version": 1})
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "closed rollback qualification"
        ):
            rollback.build_plan(self._arguments())

    def test_rejects_active_replay_in_a_different_migration_epoch(self) -> None:
        current = json.loads(json.dumps(self.versions["replay"]))
        current["resources"]["script_runtime"]["migration_tag"] = "v3"
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "active replay migration tags differ"
        ):
            rollback.validate_compatible_capabilities(
                "replay", self.versions["replay"], current
            )

    def test_rejects_stale_or_unprotected_live_state_contract(self) -> None:
        state_main = {"commit": "e" * 40, "protected": True}
        self._write(self.paths["state_main"], state_main)
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "current protected State main"
        ):
            rollback.build_plan(self._arguments())

        state_main["commit"] = PROTECTED_STATE_COMMIT
        state_main["protected"] = False
        self._write(self.paths["state_main"], state_main)
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "not an exact protected commit"
        ):
            rollback.build_plan(self._arguments())

    def test_rejects_changed_callback_contract_or_deployment_toolchain(self) -> None:
        target = self.directory / "target"
        for relative in rollback.CALLBACK_CONTRACT_FILES:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        for relative in ("server/package.json", "server/package-lock.json"):
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)

        qualification = rollback._object(self.paths["qualification"])
        state_main = rollback._object(self.paths["state_main"])
        rollback._validate_qualification(
            qualification, target, state_main, self.paths["state_schema"]
        )

        callback = target / rollback.CALLBACK_CONTRACT_FILES[0]
        callback.write_text(
            callback.read_text(encoding="utf-8") + "\n// unqualified change\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "callback implementation differs"
        ):
            rollback._validate_qualification(
                qualification, target, state_main, self.paths["state_schema"]
            )

        shutil.copy2(ROOT / rollback.CALLBACK_CONTRACT_FILES[0], callback)
        package = rollback._object(target / "server/package.json")
        package["devDependencies"]["wrangler"] = "4.123.0"
        self._write(target / "server/package.json", package)
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "deployment toolchain differs"
        ):
            rollback._validate_qualification(
                qualification, target, state_main, self.paths["state_schema"]
            )

    def test_health_uses_target_commit_configuration(self) -> None:
        plan = rollback.build_plan(self._arguments())
        rollback.validate_health(
            plan,
            "intake",
            {
                "status": "ok",
                "environment": "production",
                "deployed_commit": COMMIT,
                "intake_configured_enabled": False,
                "intake_effective_enabled": False,
                "intake_enabled": False,
                "intake_enablement_mode": "disabled",
                "intake_lease_expires_at": None,
                "legacy_result_owner_api_enabled": False,
                "result_amendment_owner_api_enabled": False,
                "result_amendment_maintainer_api_enabled": False,
                "model_identity_owner_api_enabled": False,
                "model_identity_maintainer_api_enabled": False,
                "model_identity_consolidation_api_enabled": False,
                "model_identity_write_max_subrequests": 400,
                "model_identity_consolidation_api": "atomic_reverse_impact_v1",
                "release_opt_in_api_enabled": False,
                "release_opt_out_api_enabled": False,
                "promotion_canary_configured_enabled": False,
                "promotion_canary_enabled": False,
            },
        )
        with self.assertRaisesRegex(rollback.RollbackValidationError, "health exposes"):
            rollback.validate_health(
                plan,
                "intake",
                {
                    "status": "ok",
                    "environment": "production",
                    "deployed_commit": COMMIT,
                    "intake_configured_enabled": False,
                    "intake_effective_enabled": False,
                    "intake_enabled": False,
                    "intake_enablement_mode": "disabled",
                    "intake_lease_expires_at": None,
                    "legacy_result_owner_api_enabled": False,
                    "result_amendment_owner_api_enabled": False,
                    "result_amendment_maintainer_api_enabled": False,
                    "model_identity_owner_api_enabled": False,
                    "model_identity_maintainer_api_enabled": False,
                    "model_identity_consolidation_api_enabled": False,
                    "model_identity_write_max_subrequests": 400,
                    "model_identity_consolidation_api": "atomic_reverse_impact_v1",
                    "release_opt_in_api_enabled": False,
                    "release_opt_out_api_enabled": False,
                    "result_amendment_maintainers": [],
                    "promotion_canary_configured_enabled": False,
                    "promotion_canary_enabled": False,
                },
            )
        with self.assertRaisesRegex(rollback.RollbackValidationError, "health differs"):
            rollback.validate_health(
                plan,
                "intake",
                {
                    "status": "ok",
                    "environment": "production",
                    "deployed_commit": COMMIT,
                    "intake_configured_enabled": True,
                    "intake_effective_enabled": True,
                    "intake_enabled": True,
                    "intake_enablement_mode": "durable",
                    "intake_lease_expires_at": None,
                    "legacy_result_owner_api_enabled": False,
                    "result_amendment_owner_api_enabled": False,
                    "result_amendment_maintainer_api_enabled": False,
                    "model_identity_owner_api_enabled": False,
                    "model_identity_maintainer_api_enabled": False,
                    "model_identity_consolidation_api_enabled": False,
                    "model_identity_write_max_subrequests": 400,
                    "model_identity_consolidation_api": "atomic_reverse_impact_v1",
                    "release_opt_in_api_enabled": False,
                    "release_opt_out_api_enabled": False,
                },
            )

        enabled_plan = dict(plan)
        enabled_plan["intake_enabled"] = True
        enabled_plan["intake_enablement_mode"] = "durable"
        rollback.validate_health(
            enabled_plan,
            "intake",
            {
                "status": "ok",
                "environment": "production",
                "deployed_commit": COMMIT,
                "intake_configured_enabled": False,
                "intake_effective_enabled": False,
                "intake_enabled": False,
                "intake_enablement_mode": "disabled",
                "intake_lease_expires_at": None,
                "legacy_result_owner_api_enabled": False,
                "result_amendment_owner_api_enabled": False,
                "result_amendment_maintainer_api_enabled": False,
                "model_identity_owner_api_enabled": False,
                "model_identity_maintainer_api_enabled": False,
                "model_identity_consolidation_api_enabled": False,
                "model_identity_write_max_subrequests": 400,
                "model_identity_consolidation_api": "atomic_reverse_impact_v1",
                "release_opt_in_api_enabled": False,
                "release_opt_out_api_enabled": False,
                "promotion_canary_configured_enabled": False,
                "promotion_canary_enabled": False,
            },
            require_intake_disabled=True,
        )
        rollback.validate_health(
            enabled_plan,
            "intake",
            {
                "status": "ok",
                "environment": "production",
                "deployed_commit": COMMIT,
                "intake_configured_enabled": True,
                "intake_effective_enabled": True,
                "intake_enabled": True,
                "intake_enablement_mode": "durable",
                "intake_lease_expires_at": None,
                "legacy_result_owner_api_enabled": False,
                "result_amendment_owner_api_enabled": False,
                "result_amendment_maintainer_api_enabled": False,
                "model_identity_owner_api_enabled": False,
                "model_identity_maintainer_api_enabled": False,
                "model_identity_consolidation_api_enabled": False,
                "model_identity_write_max_subrequests": 400,
                "model_identity_consolidation_api": "atomic_reverse_impact_v1",
                "release_opt_in_api_enabled": False,
                "release_opt_out_api_enabled": False,
                "promotion_canary_configured_enabled": False,
                "promotion_canary_enabled": False,
            },
        )

    def test_prestate_is_closed_sanitized_and_exactly_recoverable(self) -> None:
        plan = rollback.build_plan(self._arguments())
        plan_path = self.directory / "plan.json"
        self._write(plan_path, plan)
        prestate_args = argparse.Namespace(plan=plan_path)
        for component, version_id in (
            ("intake", INTAKE_VERSION),
            ("broker", BROKER_VERSION),
            ("replay", REPLAY_VERSION),
        ):
            status_path = self.directory / f"original-{component}-status.json"
            self._write(
                status_path,
                {"versions": [{"version_id": version_id, "percentage": 100}]},
            )
            setattr(prestate_args, f"{component}_status", status_path)
            setattr(
                prestate_args,
                f"{component}_version",
                self.paths[f"{component}_version"],
            )
        container_path = self.directory / "container.json"
        container_value = {
            "id": "44444444-4444-4444-4444-444444444444",
            "name": "lean-eval-replay-executor-replaysandbox-production",
            "version": 7,
            "max_instances": 1,
            "configuration": {
                "image": (
                    "registry.cloudflare.com/"
                    + "a" * 32
                    + "/lean-eval-authoritative:"
                    + "b" * 40
                ),
                "vcpu": 4,
                "memory_mib": 12288,
                "disk": {"size_mb": 20000},
                "network": {
                    "mode": "private",
                    "assign_ipv4": "none",
                    "assign_ipv6": "none",
                },
                "wrangler_ssh": {"enabled": False},
                "provider_future_secret": "must-not-leak",
            },
            "provider_future_field": "must-not-leak",
        }
        self._write(container_path, container_value)
        prestate_args.container_info = container_path
        prestate = rollback.build_prestate(prestate_args)
        encoded = json.dumps(prestate, sort_keys=True)
        self.assertEqual(prestate["original_version_ids"]["intake"], INTAKE_VERSION)
        self.assertEqual(prestate["original_replay_migration_tag"], "v2")
        self.assertEqual(
            prestate["rollback_feature_gates"],
            {
                "intake_enabled": False,
                "intake_enablement_contract_supported": True,
                "intake_enablement_mode": "disabled",
                "legacy_result_owner_api_contract_supported": True,
                "legacy_result_owner_api_enabled": False,
                "result_amendment_owner_api_contract_supported": True,
                "result_amendment_owner_api_enabled": False,
                "result_amendment_maintainer_api_enabled": False,
                "result_amendment_maintainer_api_contract_supported": True,
                "model_identity_owner_api_contract_supported": True,
                "model_identity_owner_api_enabled": False,
                "model_identity_maintainer_api_contract_supported": True,
                "model_identity_maintainer_api_enabled": False,
                "model_identity_consolidation_api_contract_supported": True,
                "model_identity_consolidation_api_enabled": False,
                "release_opt_in_api_contract_supported": True,
                "release_opt_in_api_enabled": False,
                "release_opt_out_api_contract_supported": True,
                "release_opt_out_api_enabled": False,
                "promotion_canary_enabled": False,
                "promotion_canary_contract_supported": True,
                "replay_enabled": False,
                "staging_acceptance_enabled": False,
                "result_owner_state_contract_commit": (
                    PROTECTED_STATE_COMMIT
                ),
                "model_identity_state_contract_commit": (
                    PROTECTED_STATE_COMMIT
                ),
            },
        )
        self.assertNotIn("provider_future", encoded)
        self.assertNotIn("secret_text", encoded)
        self.assertNotIn("RESULT_AMENDMENT_MAINTAINERS", encoded)
        self.assertIs(prestate["contains_secret_values"], False)

        container_value["configuration"]["vcpu"] = float("nan")
        self._write(container_path, container_value)
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "recovery state is malformed"
        ):
            rollback.build_prestate(prestate_args)
