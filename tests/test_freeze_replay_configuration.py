from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import unittest

from scripts.freeze_replay_configuration import (
    DOCKERFILE,
    IMAGE_LIMIT_BYTES,
    MEMORY_LIMIT_BYTES,
    PROFILE_LOCK,
    WALL_TIME_LIMIT_MS,
    FreezeError,
    freeze,
)


ROOT = pathlib.Path(__file__).parents[1]
IMAGE_DIGEST = "sha256:" + "9" * 64
SOURCE_COMMIT = "a" * 40
WORKER_COMMIT = "b" * 40


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publication() -> dict:
    lock = json.loads(PROFILE_LOCK.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "source_commit": SOURCE_COMMIT,
        "registry_repository": "lean-eval-authoritative",
        "registry_tag": SOURCE_COMMIT,
        "registry_manifest_digest": IMAGE_DIGEST,
        "image_size_bytes": 8 * 1024**3,
        "dockerfile_sha256": sha256(DOCKERFILE),
        "profile_lock_sha256": sha256(PROFILE_LOCK),
        "benchmark_commit": lock["benchmark_commit"],
        "workspace_manifest_count": 309,
        "cloudflare_image_limit_bytes": IMAGE_LIMIT_BYTES,
    }


def runtime() -> dict:
    return {
        "schema_version": 1,
        "health": {
            "status": "ok",
            "service": "lean-eval-replay-executor",
            "environment": "staging",
            "deployed_commit": WORKER_COMMIT,
            "replay_enabled": False,
            "staging_acceptance_enabled": True,
            "staging_memory_limit_bytes": MEMORY_LIMIT_BYTES,
            "production_memory_gate_bytes": MEMORY_LIMIT_BYTES,
            "reviewed_execution_profile_digest": "0" * 64,
            "reviewed_measurement_config_digest": "0" * 64,
            "reviewed_vm_image_digest": IMAGE_DIGEST,
        },
        "probe": {
            "schema_version": 1,
            "service": "lean-eval-replay-executor",
            "environment": "staging",
            "request_id": "0198abcd-0000-7000-8000-000000000001",
            "runner_nonce": "1" * 64,
            "submission_id": "0198abcd-0000-7000-8000-000000000002",
            "archive_ciphertext_sha256": "2" * 64,
            "plaintext_tar_sha256": "3" * 64,
            "plaintext_tar_size": 1234,
            "network_policy": "disabled",
            "network_probe": "blocked",
            "destruction": "confirmed",
            "architecture": "x86_64",
            "kernel_release": "6.18.36-cloudflare-firecracker-2026.6.17",
            "cpu_model": "AMD EPYC",
            "staging_memory_limit_bytes": MEMORY_LIMIT_BYTES,
            "production_memory_gate_bytes": MEMORY_LIMIT_BYTES,
        },
    }


class FreezeReplayConfigurationTests(unittest.TestCase):
    def test_freezes_exact_profile_and_domain_separated_digests(self) -> None:
        lock = json.loads(PROFILE_LOCK.read_text(encoding="utf-8"))
        result = freeze(publication(), runtime(), lock)
        self.assertEqual(result["registry_manifest_digest"], IMAGE_DIGEST)
        self.assertEqual(result["execution_profile"]["components"], lock["components"])
        self.assertEqual(result["execution_profile"]["cpu_model"], "AMD EPYC")
        self.assertEqual(
            result["measurement_config"]["memory_limit_bytes"], MEMORY_LIMIT_BYTES
        )
        self.assertEqual(
            result["measurement_config"]["wall_time_limit_ms"], WALL_TIME_LIMIT_MS
        )
        self.assertEqual(WALL_TIME_LIMIT_MS, 19_800_000)
        self.assertRegex(result["execution_profile_digest"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["measurement_config_digest"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(
            result["execution_profile_digest"], result["measurement_config_digest"]
        )

    def test_rejects_unbound_or_unsafe_evidence(self) -> None:
        lock = json.loads(PROFILE_LOCK.read_text(encoding="utf-8"))
        for mutation in ("manifest", "enabled", "network", "memory", "input"):
            with self.subTest(mutation=mutation):
                published = publication()
                observed = runtime()
                if mutation == "manifest":
                    observed["health"]["reviewed_vm_image_digest"] = "sha256:" + "8" * 64
                elif mutation == "enabled":
                    observed["health"]["replay_enabled"] = True
                elif mutation == "network":
                    observed["probe"]["network_probe"] = "allowed"
                elif mutation == "memory":
                    observed["probe"]["staging_memory_limit_bytes"] += 1
                else:
                    published["dockerfile_sha256"] = "7" * 64
                with self.assertRaises(FreezeError):
                    freeze(published, observed, lock)

    def test_rejects_runtime_evidence_with_unknown_fields(self) -> None:
        lock = json.loads(PROFILE_LOCK.read_text(encoding="utf-8"))
        observed = copy.deepcopy(runtime())
        observed["probe"]["untrusted_output"] = "must not be accepted"
        with self.assertRaisesRegex(FreezeError, "fields are not canonical"):
            freeze(publication(), observed, lock)


if __name__ == "__main__":
    unittest.main()
