from __future__ import annotations

import copy
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_historical_private_executor_ownership as ownership  # noqa: E402


class HistoricalPrivateExecutorOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        digest_a = "sha256:" + "a" * 64
        digest_b = "sha256:" + "b" * 64
        worker = "hpr-" + "1" * 56 + "-1"
        application = "le-hpr-" + "2" * 22 + "-1"
        variables = {
            "DEPLOYED_COMMIT": "c" * 40,
            "DEPLOYMENT_ENVIRONMENT": "historical-private-replay",
            "REPLAY_ENABLED": "true",
            "HISTORICAL_PUBLIC_REPLAY_ENABLED": "false",
            "STAGING_ACCEPTANCE_ENABLED": "false",
            "GITHUB_OIDC_AUDIENCE": "lean-eval-historical-private-replay",
            "GITHUB_OIDC_ENVIRONMENT": "replay-production",
            "STAGING_MEMORY_LIMIT_BYTES": str(12 * 1024**3),
            "PRODUCTION_MEMORY_GATE_BYTES": str(12 * 1024**3),
            "REVIEWED_EXECUTION_PROFILE_DIGEST": digest_a,
            "REVIEWED_MEASUREMENT_CONFIG_DIGEST": digest_b,
            "REVIEWED_VM_IMAGE_DIGEST": digest_a,
            "EXPECTED_REPLAY_TASK_ID": "rt1_" + "d" * 64,
            "EXPECTED_REPLAY_ATTEMPT": "1",
            "EXECUTOR_OWNERSHIP_TAG": "e" * 64,
            "SANDBOX_TRANSPORT": "rpc",
        }
        self.config = {
            "name": worker,
            "compatibility_date": "2026-08-22",
            "compatibility_flags": ["nodejs_compat"],
            "containers": [
                {
                    "name": application,
                    "class_name": "ReplaySandbox",
                    "image": (
                        "registry.cloudflare.com/"
                        + "f" * 32
                        + "/lean-eval-authoritative@"
                        + digest_a
                    ),
                    "instance_type": "standard-4",
                    "max_instances": 1,
                    "ssh": {"enabled": False},
                }
            ],
            "durable_objects": {
                "bindings": [
                    {"name": "REPLAY_SANDBOX", "class_name": "ReplaySandbox"},
                    {
                        "name": "REPLAY_TERMINAL_RECEIPT",
                        "class_name": "ReplayTerminalReceipt",
                    },
                ]
            },
            "migrations": [
                {
                    "tag": "v1",
                    "new_sqlite_classes": [
                        "ReplaySandbox",
                        "ReplayTerminalReceipt",
                    ],
                }
            ],
            "vars": variables,
        }
        bindings = [
            {"type": "plain_text", "name": name, "text": value}
            for name, value in variables.items()
        ] + [
            {
                "type": "durable_object_namespace",
                "name": "REPLAY_SANDBOX",
                "class_name": "ReplaySandbox",
                "namespace_id": "1" * 32,
            },
            {
                "type": "durable_object_namespace",
                "name": "REPLAY_TERMINAL_RECEIPT",
                "class_name": "ReplayTerminalReceipt",
                "namespace_id": "2" * 32,
            },
        ]
        version_id = "01900000-0000-7000-8000-000000000001"
        self.service = {
            "success": True,
            "result": {
                "id": worker,
                "default_environment": {
                    "environment": "production",
                    "script": {"tag": "owned-script-tag"},
                },
            },
        }
        self.settings = {
            "success": True,
            "result": {
                "compatibility_date": "2026-08-22",
                "compatibility_flags": ["nodejs_compat"],
                "bindings": copy.deepcopy(bindings),
            },
        }
        self.deployments = {
            "success": True,
            "result": {
                "deployments": [
                    {"versions": [{"version_id": version_id, "percentage": 100}]}
                ]
            },
        }
        self.version = {
            "success": True,
            "result": {
                "id": version_id,
                "metadata": {"created_on": "2026-08-27T12:00:00.000Z"},
                "annotations": {"workers/tag": "owned-script-tag"},
                "resources": {
                    "bindings": copy.deepcopy(bindings),
                    "script_runtime": {
                        "migration_tag": "v1",
                        "containers": [
                            {"class_name": "ReplaySandbox", "name": application}
                        ],
                    },
                },
            },
        }
        self.health = {
            "status": "ok",
            "service": "lean-eval-replay-executor",
            "environment": "historical-private-replay",
            "deployed_commit": "c" * 40,
            "replay_enabled": True,
            "historical_public_replay_enabled": False,
            "staging_acceptance_enabled": False,
            "staging_memory_limit_bytes": 12 * 1024**3,
            "production_memory_gate_bytes": 12 * 1024**3,
            "reviewed_execution_profile_digest": digest_a,
            "reviewed_measurement_config_digest": digest_b,
            "reviewed_vm_image_digest": digest_a,
            "executor_ownership_tag": "e" * 64,
            "expected_replay_task_id": "rt1_" + "d" * 64,
            "expected_replay_attempt": "1",
        }
        self.application = {
            "id": "01900000-0000-7000-8000-000000000002",
            "name": application,
            "version": 3,
            "created_at": "2026-08-27T12:00:01.000Z",
            "max_instances": 1,
            "configuration": {
                "image": self.config["containers"][0]["image"],
                "wrangler_ssh": {"enabled": False},
                "vcpu": 4,
                "memory_mib": 12_288,
                "disk": {"size_mb": 20_000},
                "network": {
                    "assign_ipv6": "none",
                    "assign_ipv4": "none",
                    "mode": "private",
                },
            },
        }

    def verify(self, applications: list[object] | None = None) -> dict[str, object]:
        return ownership.verify(
            self.config,
            self.service,
            self.settings,
            self.deployments,
            self.version,
            self.health,
            [self.application] if applications is None else applications,
        )

    def test_exact_worker_and_application_are_owned(self) -> None:
        proof = self.verify()
        self.assertTrue(proof["worker_owned"])
        self.assertTrue(proof["application_owned"])
        self.assertEqual(proof["application_version"], 3)
        self.assertEqual(proof["application_id"], self.application["id"])

    def test_exact_worker_without_application_is_owned(self) -> None:
        proof = self.verify([])
        self.assertTrue(proof["worker_owned"])
        self.assertFalse(proof["application_owned"])

    def test_extra_binding_is_rejected(self) -> None:
        self.settings["result"]["bindings"].append(  # type: ignore[index]
            {"type": "plain_text", "name": "ESCAPED", "text": "true"}
        )
        with self.assertRaisesRegex(ownership.OwnershipError, "settings differ"):
            self.verify()

    def test_wrong_settings_are_rejected(self) -> None:
        self.settings["result"]["compatibility_date"] = "2026-08-21"  # type: ignore[index]
        with self.assertRaisesRegex(ownership.OwnershipError, "settings differ"):
            self.verify()

    def test_wrong_active_version_is_rejected(self) -> None:
        runtime = self.version["result"]["resources"]["script_runtime"]  # type: ignore[index]
        runtime["migration_tag"] = "v2"  # type: ignore[index]
        with self.assertRaisesRegex(ownership.OwnershipError, "version differs"):
            self.verify()

    def test_wrong_or_extended_health_identity_is_rejected(self) -> None:
        self.health["unexpected"] = "leak"
        with self.assertRaisesRegex(ownership.OwnershipError, "health identity"):
            self.verify()

    def test_wrong_application_version_or_settings_are_rejected(self) -> None:
        self.application["version"] = 0
        with self.assertRaisesRegex(ownership.OwnershipError, "application differs"):
            self.verify()
        self.application["version"] = 3
        self.application["configuration"]["network"]["mode"] = "public"  # type: ignore[index]
        with self.assertRaisesRegex(ownership.OwnershipError, "application differs"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
