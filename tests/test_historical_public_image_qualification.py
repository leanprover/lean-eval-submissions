from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
MATRIX = ROOT / "configuration/historical-public-replay-profile-matrix-v1.json"
CONTRACT = ROOT / "historical-public-qualification/contract-v1.json"
MODULE_PATH = ROOT / "historical-public-qualification/qualification.py"
SPEC = importlib.util.spec_from_file_location("historical_qualification", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)


class HistoricalPublicImageQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix = json.loads(MATRIX.read_text())
        cls.entry = cls.matrix["images"][0]
        cls.commit = cls.entry["benchmark_commit"]

    def test_all_25_profiles_remain_unqualified_and_select_exactly(self) -> None:
        self.assertEqual(self.matrix["image_count"], 25)
        self.assertEqual(len(self.matrix["images"]), 25)
        self.assertEqual(self.matrix["qualification_status"], "unqualified")
        self.assertEqual({item["qualification_status"] for item in self.matrix["images"]}, {"unqualified"})
        candidates = [
            qualification.candidate(MATRIX, CONTRACT, entry["benchmark_commit"])
            for entry in self.matrix["images"]
        ]
        self.assertEqual(len({item["benchmark_commit"] for item in candidates}), 25)
        self.assertEqual({item["qualification_status"] for item in candidates}, {"unqualified"})
        candidate = candidates[0]
        self.assertEqual(candidate["benchmark_tree"], self.entry["benchmark_tree"])
        self.assertEqual(candidate["toolchain"], self.entry["toolchain"])
        self.assertEqual(candidate["workspace_count"], self.entry["workspace_count"])
        self.assertEqual(candidate["profile_lock_sha256"], qualification.sha256_bytes(qualification.canonical(self.entry["profile_lock"])))
        self.assertEqual(candidate["qualification_status"], "unqualified")

    def test_unknown_or_mutated_matrix_is_rejected(self) -> None:
        with self.assertRaisesRegex(qualification.QualificationError, "does not select"):
            qualification.candidate(MATRIX, CONTRACT, "0" * 40)
        changed = json.loads(MATRIX.read_text())
        changed["images"][0]["qualification_status"] = "qualified"
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "matrix.json"
            path.write_text(json.dumps(changed, indent=2, sort_keys=True) + "\n")
            with self.assertRaisesRegex(qualification.QualificationError, "digest changed"):
                qualification.candidate(path, CONTRACT, self.commit)

    def test_rendered_worker_is_dedicated_replay_disabled_and_exactly_bound(self) -> None:
        config = qualification.render_config(
            MATRIX, CONTRACT, self.commit, "1" * 32, "sha256:" + "2" * 64, "3" * 40,
        )
        staging = config["env"]["staging"]
        self.assertEqual(staging["name"], "lean-eval-historical-qualifier-staging")
        self.assertEqual(staging["vars"]["REPLAY_ENABLED"], "false")
        self.assertEqual(staging["vars"]["STAGING_ACCEPTANCE_ENABLED"], "true")
        self.assertEqual(staging["vars"]["REVIEWED_EXECUTION_PROFILE_DIGEST"], "0" * 64)
        self.assertEqual(staging["vars"]["REVIEWED_MEASUREMENT_CONFIG_DIGEST"], "0" * 64)
        self.assertEqual(staging["vars"]["GITHUB_OIDC_ENVIRONMENT"], "replay-staging")
        self.assertEqual(staging["containers"][0]["instance_type"], "standard-4")
        self.assertEqual(staging["containers"][0]["max_instances"], 1)
        self.assertEqual(staging["containers"][0]["ssh"], {"enabled": False})
        self.assertTrue(staging["containers"][0]["image"].endswith("/lean-eval-historical-public-v1:" + self.commit + "-" + "3" * 40))

    def test_health_and_two_same_nonce_probe_responses_validate_but_stay_unqualified(self) -> None:
        config = qualification.render_config(
            MATRIX, CONTRACT, self.commit, "1" * 32, "sha256:" + "2" * 64, "3" * 40,
        )
        binding = {"vars": config["env"]["staging"]["vars"]}
        variables = binding["vars"]
        health = {
            "status": "ok", "service": "lean-eval-replay-executor", "environment": "staging",
            "deployed_commit": variables["DEPLOYED_COMMIT"], "replay_enabled": False,
            "staging_acceptance_enabled": True,
            "staging_memory_limit_bytes": 12_884_901_888,
            "production_memory_gate_bytes": 12_884_901_888,
            "reviewed_execution_profile_digest": variables["REVIEWED_EXECUTION_PROFILE_DIGEST"],
            "reviewed_measurement_config_digest": variables["REVIEWED_MEASUREMENT_CONFIG_DIGEST"],
            "reviewed_vm_image_digest": variables["REVIEWED_VM_IMAGE_DIGEST"],
        }
        self.assertEqual(qualification.validate_health(health, binding), health)
        contract = qualification.load(CONTRACT, qualification.CONTRACT_SHA256)
        for number in (1, 2):
            request = {
                "request_id": f"01234567-89ab-7cde-8fab-{number:012d}",
                "runner_nonce": "4" * 64,
                "archive_ciphertext_sha256": str(number) * 64,
                "marker_sha256": str(number + 2) * 64,
            }
            response = {
                "schema_version": 1, "service": "lean-eval-replay-executor", "environment": "staging",
                "request_id": request["request_id"], "runner_nonce": request["runner_nonce"],
                "archive_ciphertext_sha256": request["archive_ciphertext_sha256"],
                "marker_sha256": request["marker_sha256"], "network_policy": "disabled",
                "network_probe": "blocked", "destruction": "confirmed", "architecture": "x86_64",
                "kernel_release": "fixture-kernel", "cpu_model": "fixture-cpu",
                "staging_memory_limit_bytes": 12_884_901_888,
                "production_memory_gate_bytes": 12_884_901_888,
            }
            self.assertEqual(qualification.validate_probe(response, request, contract), response)

    def test_compact_remote_json_and_account_free_rollout_validate(self) -> None:
        compact = {"status": "ok", "nested": {"value": 1}}
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "response.json"
            path.write_text(json.dumps(compact, separators=(",", ":")), encoding="utf-8")
            self.assertEqual(qualification.load_external(path), compact)
            with self.assertRaisesRegex(qualification.QualificationError, "not canonical"):
                qualification.load(path)
        contract = qualification.load(CONTRACT, qualification.CONTRACT_SHA256)
        rollout = {
            "schema_version": 1,
            "kind": "historical_public_qualification_rollout",
            "qualification_status": "unqualified",
            "name": contract["container_application"],
            "version": 1,
            "max_instances": 1,
            "image_repository": contract["registry_repository"],
            "image_tag": self.commit + "-" + "3" * 40,
            "runtime_boundary": {
                "vcpu": 4,
                "memory_mib": 12 * 1024,
                "disk_size_mb": 20_000,
                "network": {"assign_ipv6": "none", "assign_ipv4": "none", "mode": "private"},
                "ssh": {"enabled": False},
            },
            "health": {"errors": [], "instances": {"healthy": 1, "failed": 0, "starting": 0, "scheduling": 0}},
        }
        self.assertEqual(qualification.validate_rollout(rollout, contract, self.commit, "3" * 40), rollout)
        self.assertNotIn("account", json.dumps(rollout))

    def test_uuid7_shape(self) -> None:
        self.assertRegex(qualification.uuid7(), r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

    def test_workflow_and_dockerfile_are_manual_create_only_contracts(self) -> None:
        workflow = (ROOT / ".github/workflows/historical-public-image-qualification.yml").read_text()
        dockerfile = (ROOT / "Dockerfile.historical-public-replay").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("immutable registry tag already exists; refusing overwrite", workflow)
        self.assertIn("REPLAY_ENABLED\": \"false", MODULE_PATH.read_text())
        self.assertIn("qualification_status\": \"unqualified", MODULE_PATH.read_text())
        self.assertIn(qualification.MATRIX_SHA256, dockerfile)
        self.assertIn('matrix["image_count"] == len(matrix["images"]) == 25', dockerfile)
        self.assertNotIn("containers push", dockerfile)


if __name__ == "__main__":
    unittest.main()
