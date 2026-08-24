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
        self.assertIn("server/src/github-broker-client.ts", rollback.CALLBACK_CONTRACT_FILES)

    def test_qualifies_broker_allowlist(self) -> None:
        self.assertIn("server/src/github-broker.ts", rollback.CALLBACK_CONTRACT_FILES)

    def test_qualifies_owner_authentication_boundary(self) -> None:
        self.assertIn("server/src/auth.ts", rollback.CALLBACK_CONTRACT_FILES)

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


class CloudflareRollbackValidationTests(unittest.TestCase):
    def test_repository_qualification_matches_runtime_schema_and_pause_guard(self) -> None:
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
        self.assertIn("if (!currentIntake(env, dependencies).effective) return;", app)

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
        state_commit = "d" * 40
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
                                    "lean-eval-replay-executor-"
                                    "replaysandbox-production"
                                ),
                            }
                        ]
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
        self.assertEqual(
            plan["result_owner_state_contract_commit"],
            "889e07e3b8cf38ad147d8a23b7d1b35826de740f",
        )
        self.assertIs(plan["promotion_canary_enabled"], False)
        self.assertIs(plan["replay_enabled"], False)

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
            "must disable legacy result owner API, promotion canary",
        ):
            rollback.build_plan(self._arguments())

    def test_rejects_production_rollback_with_owner_api_enabled(self) -> None:
        self.configs["intake"]["env"]["production"]["vars"][
            "LEGACY_RESULT_OWNER_API_ENABLED"
        ] = "true"
        self.configs["intake"]["env"]["production"]["vars"][
            "RESULT_OWNER_STATE_CONTRACT_COMMIT"
        ] = "d" * 40
        self._write(self.paths["intake_config"], self.configs["intake"])
        for binding in self.versions["intake"]["resources"]["bindings"]:
            if binding.get("name") == "LEGACY_RESULT_OWNER_API_ENABLED":
                binding["text"] = "true"
            if binding.get("name") == "RESULT_OWNER_STATE_CONTRACT_COMMIT":
                binding["text"] = "d" * 40
        self._write(self.paths["intake_version"], self.versions["intake"])
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "must disable legacy result owner API"
        ):
            rollback.build_plan(self._arguments())

    def test_older_target_without_owner_gate_is_safely_disabled(self) -> None:
        for name in (
            "LEGACY_RESULT_OWNER_API_ENABLED",
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
                "RESULT_OWNER_STATE_CONTRACT_COMMIT",
            }
        ]
        self._write(self.paths["intake_version"], self.versions["intake"])
        plan = rollback.build_plan(self._arguments())
        self.assertIs(plan["legacy_result_owner_api_contract_supported"], False)
        self.assertIs(plan["legacy_result_owner_api_enabled"], False)
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
        with self.assertRaisesRegex(rollback.RollbackValidationError, "exact deployed commit"):
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

    def test_emergency_target_may_track_durable_intake_but_must_disable_replay(self) -> None:
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
            "must disable legacy result owner API, promotion canary",
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

        state_main["commit"] = "d" * 40
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
        self.assertEqual(
            prestate["original_version_ids"]["intake"], INTAKE_VERSION
        )
        self.assertEqual(prestate["original_replay_migration_tag"], "v2")
        self.assertEqual(
            prestate["rollback_feature_gates"],
            {
                "intake_enabled": False,
                "intake_enablement_contract_supported": True,
                "intake_enablement_mode": "disabled",
                "legacy_result_owner_api_contract_supported": True,
                "legacy_result_owner_api_enabled": False,
                "promotion_canary_enabled": False,
                "promotion_canary_contract_supported": True,
                "replay_enabled": False,
                "staging_acceptance_enabled": False,
                "result_owner_state_contract_commit": (
                    "889e07e3b8cf38ad147d8a23b7d1b35826de740f"
                ),
            },
        )
        self.assertNotIn("provider_future", encoded)
        self.assertNotIn("secret_text", encoded)
        self.assertIs(prestate["contains_secret_values"], False)

        container_value["configuration"]["vcpu"] = float("nan")
        self._write(container_path, container_value)
        with self.assertRaisesRegex(
            rollback.RollbackValidationError, "recovery state is malformed"
        ):
            rollback.build_prestate(prestate_args)
