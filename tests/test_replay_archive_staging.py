from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import tempfile
import unittest

from scripts.key_capability_contract import archive_key_id
from scripts.replay_archive_staging import (
    StagingReplayError,
    build_executor_request,
    build_plan,
    prepare_unwrap,
    validate_response,
)


SUBMISSION_ID = "01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584"
RESULT_ID = "r2_" + "1" * 64
CIPHERTEXT = b"age-encryption.org/v1\nfixture"
PLAINTEXT = b"fixture tar bytes"
RECIPIENT = "age1" + "q" * 40


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: pathlib.Path, value: object) -> pathlib.Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def domain() -> dict[str, object]:
    return {
        "schema_version": 1,
        "environment": "staging",
        "submissions": [{
            "submission_id": SUBMISSION_ID,
            "source_visibility": "private",
            "result_id": RESULT_ID,
            "archive": {
                "status": "completed",
                "archive_repository": "leanprover/lean-eval-audit",
                "archive_commit": "a" * 40,
                "archive_path": f"archives/01/{SUBMISSION_ID}.tar.age",
                "archive_ciphertext_sha256": digest(CIPHERTEXT),
                "encrypted": True,
            },
            "evaluation": {"status": "accepted"},
        }],
        "results": [{"submission_id": SUBMISSION_ID, "result_id": RESULT_ID}],
    }


def sidecar() -> dict[str, object]:
    return {
        "schema_version": 3,
        "submission_id": SUBMISSION_ID,
        "submission_repo": "example/private-source",
        "submission_ref": "b" * 40,
        "submission_kind": "github_repo",
        "submission_public": False,
        "submitter": "example",
        "model": "Example Model",
        "size_bytes_plaintext_tar": len(PLAINTEXT),
        "sha256_plaintext_tar": digest(PLAINTEXT),
        "size_bytes_ciphertext": len(CIPHERTEXT),
        "sha256_ciphertext": digest(CIPHERTEXT),
        "archived_at": "2026-08-23T03:40:44Z",
        "benchmark_commit": "c" * 40,
        "archiver_workflow_run": (
            "https://github.com/leanprover/lean-eval-submissions/actions/runs/123"
        ),
        "key_envelope": {
            "schema_version": 1,
            "submission_id": SUBMISSION_ID,
            "archive_ciphertext_sha256": digest(CIPHERTEXT),
            "data_key_id": archive_key_id(SUBMISSION_ID, RECIPIENT),
            "age_recipient": RECIPIENT,
            "adapter": "aws-kms-v1",
            "wrapped_identity": "cHJvdmlkZXItd3JhcHBlZC1pZGVudGl0eQ==",
        },
    }


class ReplayArchiveStagingTests(unittest.TestCase):
    def fixture(self, root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        domain_path = write_json(root / "domain.json", domain())
        sidecar_path = write_json(root / "archive.json", sidecar())
        ciphertext_path = root / "archive.tar.age"
        ciphertext_path.write_bytes(CIPHERTEXT)
        return domain_path, sidecar_path, ciphertext_path

    def test_plans_only_one_accepted_private_archive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            domain_path, _, _ = self.fixture(root)
            output = root / "plan.json"
            build_plan(domain_path, SUBMISSION_ID, output)
            plan = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(plan["submission_id"], SUBMISSION_ID)
            self.assertEqual(plan["archive_ciphertext_sha256"], digest(CIPHERTEXT))

            changed = domain()
            changed["submissions"][0]["evaluation"]["status"] = "rejected"  # type: ignore[index]
            write_json(root / "rejected.json", changed)
            with self.assertRaisesRegex(StagingReplayError, "not one accepted"):
                build_plan(root / "rejected.json", SUBMISSION_ID, root / "unused.json")

    def test_builds_one_bound_unwrap_and_executor_request(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            domain_path, sidecar_path, ciphertext_path = self.fixture(root)
            plan_path = root / "plan.json"
            unwrap_path = root / "unwrap.json"
            build_plan(domain_path, SUBMISSION_ID, plan_path)
            prepare_unwrap(
                plan_path,
                sidecar_path,
                ciphertext_path,
                "2026-08-23T04:00:00.000Z",
                unwrap_path,
            )
            unwrap = json.loads(unwrap_path.read_text(encoding="utf-8"))
            self.assertEqual(unwrap["capability"]["submission_id"], SUBMISSION_ID)
            self.assertEqual(unwrap["capability"]["max_uses"], 1)

            # Replace the expired fixture interval with a current one before
            # building the handoff; prepare_unwrap itself is tested against the
            # exact operator-supplied clock above.
            current = dt.datetime.now(dt.timezone.utc)
            unwrap["capability"]["issued_at"] = current.isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")
            unwrap["capability"]["expires_at"] = (
                current + dt.timedelta(minutes=5)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            unwrap_path.write_text(json.dumps(unwrap), encoding="utf-8")
            identity_path = root / "identity.age"
            identity_path.write_bytes(b"AGE-SECRET-KEY-1FIXTURE\n")
            executor_path = root / "executor.json"
            build_executor_request(
                plan_path,
                sidecar_path,
                ciphertext_path,
                unwrap_path,
                identity_path,
                executor_path,
            )
            executor = json.loads(executor_path.read_text(encoding="utf-8"))
            self.assertEqual(executor["submission_id"], SUBMISSION_ID)
            self.assertEqual(executor["plaintext_tar_sha256"], digest(PLAINTEXT))
            self.assertNotIn("wrapped_identity", executor)

    def test_response_requires_destroyed_source_free_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            request = {
                "request_id": "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
                "runner_nonce": "1" * 64,
                "submission_id": SUBMISSION_ID,
                "archive_ciphertext_sha256": digest(CIPHERTEXT),
                "plaintext_tar_sha256": digest(PLAINTEXT),
                "plaintext_tar_size": len(PLAINTEXT),
            }
            request_path = write_json(root / "request.json", request)
            response = {
                "schema_version": 1,
                "service": "lean-eval-replay-executor",
                "environment": "staging",
                **request,
                "network_policy": "disabled",
                "network_probe": "blocked",
                "destruction": "confirmed",
                "architecture": "x86_64",
                "kernel_release": "fixture-kernel",
                "cpu_model": "fixture-cpu",
                "staging_memory_limit_bytes": 12_884_901_888,
                "production_memory_gate_bytes": 12_884_901_888,
            }
            response_path = write_json(root / "response.json", response)
            validate_response(request_path, response_path)
            response["destruction"] = "unknown"
            response_path.write_text(json.dumps(response), encoding="utf-8")
            with self.assertRaisesRegex(StagingReplayError, "not bound"):
                validate_response(request_path, response_path)


if __name__ == "__main__":
    unittest.main()
