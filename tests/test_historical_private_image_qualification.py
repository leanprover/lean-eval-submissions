from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

import jsonschema
from referencing import Registry, Resource

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import historical_private_image_qualification as qualification
from prepare_historical_private_replay import canonical
from replay_orchestrator import config_digest

MATRIX = ROOT / "configuration/historical-private-replay-image-matrix-v1.json"
SCHEMA = ROOT / "schemas/historical-private-profile-qualification-v1.schema.json"
WORKFLOW = ROOT / ".github/workflows/historical-private-image-qualification.yml"
PUBLIC_PROFILES = ROOT / "evidence/public-replay/profiles"


class HistoricalPrivateImageQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix_raw = MATRIX.read_bytes()
        cls.matrix = json.loads(cls.matrix_raw)
        cls.entry = cls.matrix["images"][0]
        cls.source_commit = "1" * 40
        cls.candidate = qualification.select_candidate(
            MATRIX, cls.entry["benchmark_commit"], ROOT, cls.source_commit
        )

    def receipt(self, manifest: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": qualification.RECEIPT_KIND,
            "registry_manifest_digest": manifest,
            "benchmark_commit": self.entry["benchmark_commit"],
            "runner_entrypoint": qualification.RUNNER_ENTRYPOINT,
            "archive_expectation_schema_version": 2,
            "key_material_type": "age-file-key-v1",
            "network_probe": "blocked",
            "status": "passed",
            "architecture": qualification.EXPECTED_ARCHITECTURE,
            "kernel_release": qualification.EXPECTED_KERNEL_RELEASE,
            "cpu_model": qualification.EXPECTED_CPU_MODEL,
        }

    def test_selector_accepts_only_the_closed_matrix_and_source_closure(self) -> None:
        images = qualification.validate_matrix(self.matrix, self.matrix_raw)
        self.assertEqual(len(images), 63)
        self.assertEqual(hashlib.sha256(self.matrix_raw).hexdigest(), qualification.MATRIX_SHA256)
        self.assertEqual(self.candidate["benchmark_commit"], self.entry["benchmark_commit"])
        self.assertEqual(set(self.candidate["source_blobs"]), set(qualification.SOURCE_PATHS))
        changed = copy.deepcopy(self.matrix)
        changed["images"][0]["workspace_count"] += 1
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "matrix.json"
            path.write_bytes(canonical(changed))
            with self.assertRaisesRegex(qualification.QualificationError, "identity changed"):
                qualification.select_candidate(
                    path, self.entry["benchmark_commit"], ROOT, self.source_commit
                )

    def test_existing_tag_must_match_exact_manifest_config_and_labels(self) -> None:
        labels = {
            "org.lean-eval.image-family": qualification.IMAGE_FAMILY,
            "org.lean-eval.image-matrix-sha256": qualification.MATRIX_SHA256,
            "org.lean-eval.image-source-commit": self.source_commit,
            "org.lean-eval.benchmark-commit": self.entry["benchmark_commit"],
            "org.lean-eval.image-source-closure-sha256": hashlib.sha256(
                canonical(self.candidate)
            ).hexdigest(),
        }
        config_raw = json.dumps({"config": {"Labels": labels}}, separators=(",", ":")).encode()
        manifest_raw = json.dumps({
            "schemaVersion": 2,
            "config": {"digest": "sha256:" + hashlib.sha256(config_raw).hexdigest()},
            "layers": [],
        }, separators=(",", ":")).encode()
        digest = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            manifest = root / "manifest.json"
            config = root / "config.json"
            manifest.write_bytes(manifest_raw)
            config.write_bytes(config_raw)
            self.assertEqual(
                qualification.validate_registry_image(self.candidate, digest, manifest, config),
                digest,
            )
            config.write_bytes(b"{}")
            with self.assertRaises(qualification.QualificationError):
                qualification.validate_registry_image(self.candidate, digest, manifest, config)

    def test_pulled_inspection_image_must_match_exact_remote_digest(self) -> None:
        digest = "sha256:" + "2" * 64
        expected = (
            f"{qualification.CLOUDFLARE_REGISTRY}/"
            f"{qualification.CLOUDFLARE_ACCOUNT_ID}/"
            f"{qualification.IMAGE_REPOSITORY}@{digest}"
        )
        with tempfile.TemporaryDirectory() as raw:
            repo_digests = pathlib.Path(raw) / "repo-digests.json"
            repo_digests.write_text(json.dumps([expected]), encoding="utf-8")
            self.assertEqual(
                qualification.validate_pulled_image_reference(digest, repo_digests),
                expected,
            )
            repo_digests.write_text(
                json.dumps([expected.rsplit("@", 1)[0] + "@sha256:" + "3" * 64]),
                encoding="utf-8",
            )
            self.assertRaisesRegex(
                qualification.QualificationError,
                "not bound",
                qualification.validate_pulled_image_reference,
                digest,
                repo_digests,
            )

    def test_frozen_public_profiles_support_every_private_toolchain_and_one_runtime(self) -> None:
        for entry in self.matrix["images"]:
            candidate = qualification.select_candidate(
                MATRIX, entry["benchmark_commit"], ROOT, self.source_commit
            )
            self.assertEqual(
                qualification.target_runtime_from_public_profiles(PUBLIC_PROFILES, candidate),
                {
                    "architecture": qualification.EXPECTED_ARCHITECTURE,
                    "kernel_release": qualification.EXPECTED_KERNEL_RELEASE,
                    "cpu_model": qualification.EXPECTED_CPU_MODEL,
                },
            )
        with tempfile.TemporaryDirectory() as raw:
            self.assertRaisesRegex(
                qualification.QualificationError,
                "incomplete",
                qualification.target_runtime_from_public_profiles,
                pathlib.Path(raw),
                self.candidate,
            )

    def test_offline_probe_keeps_local_runtime_out_of_target_profile(self) -> None:
        manifest = "sha256:" + "2" * 64
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            plaintext = root / "source.tar.gz"
            ciphertext = root / "source.tar.gz.age"
            key = root / "file-key"
            probe = root / "probe"
            probe.mkdir()
            plaintext.write_bytes(b"plaintext")
            ciphertext.write_bytes(b"ciphertext")
            key.write_bytes(b"0" * 16)
            qualification.prepare_offline_probe_inputs(
                self.candidate, plaintext, ciphertext, key, manifest, 123, 1,
                "x86_64", "local-kernel", "local-cpu", probe,
            )
            request = json.loads((probe / "replay-request.json").read_bytes())
            self.assertEqual(request["execution_profile"]["kernel_release"], "local-kernel")
            (probe / "archive.tar.gz.age.b64").unlink()
            (probe / "key-material.b64").unlink()
            verdict = json.loads(
                (ROOT / "tests/fixtures/replay-verdict-accepted-v1.json").read_text()
            )
            verdict["replay_task_id"] = request["replay_task_id"]
            verdict_path = root / "verdict.json"
            verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
            receipt = qualification.render_offline_receipt(
                self.candidate, manifest, probe, verdict_path, PUBLIC_PROFILES
            )
            self.assertEqual(receipt["architecture"], qualification.EXPECTED_ARCHITECTURE)
            self.assertNotEqual(receipt["kernel_release"], "local-kernel")
            for residue in (
                "archive.tar.gz.age.b64",
                "key-material.b64",
                "accepted-source.tar.gz.age",
                "accepted-source.file-key",
                "accepted-source.tar.gz",
                "authoritative",
                "replay-output",
                "metrics.json",
            ):
                with self.subTest(residue=residue):
                    residue_path = probe / residue
                    residue_path.write_bytes(b"sensitive execution residue")
                    self.assertRaisesRegex(
                        qualification.QualificationError,
                        "cleanup is incomplete",
                        qualification.render_offline_receipt,
                        self.candidate,
                        manifest,
                        probe,
                        verdict_path,
                        PUBLIC_PROFILES,
                    )
                    residue_path.unlink()

    def test_renderer_preserves_schema_and_records_deferred_live_canary(self) -> None:
        manifest = "sha256:" + "2" * 64
        execution_digest, profile = qualification.render_qualification(
            self.candidate,
            self.receipt(manifest),
            PUBLIC_PROFILES,
            manifest,
            "3" * 40,
            hashlib.sha256(WORKFLOW.read_bytes()).hexdigest(),
            1234,
            2,
        )
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        plan_schema = json.loads(
            (ROOT / "schemas/historical-private-replay-plan-v1.schema.json").read_text()
        )
        registry = Registry().with_resources([
            (value["$id"], Resource.from_contents(value)) for value in (schema, plan_schema)
        ])
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema, registry=registry).validate(profile)
        proof = profile["qualification"]
        self.assertEqual(proof["offline_image_inspection"], {
            "archive_expectation_schema_version": 2,
            "key_material_type": "age-file-key-v1",
            "runner_entrypoint": qualification.RUNNER_ENTRYPOINT,
            "official_entrypoint": "passed",
            "network": "blocked",
            "root_filesystem": "read_only",
            "registry_manifest": "validated",
            "source_closure": "validated",
        })
        self.assertEqual(
            proof["cloudflare_runtime_validation"], "deferred_to_first_historical_replay"
        )
        self.assertEqual(
            execution_digest,
            config_digest("lean-eval-replay-execution-profile-v1", profile["execution_profile"]),
        )

    def test_workflow_has_no_disposable_cloudflare_runtime_and_unique_review_branch(self) -> None:
        raw = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("prepare-offline-probe", raw)
        self.assertIn("--network none --read-only --cpus 4 --memory 12g", raw)
        self.assertIn(
            "--tmpfs /opt/lean-eval/benchmark/.replay-workspaces:"
            "rw,exec,nosuid,nodev,size=4g,mode=0700",
            raw,
        )
        self.assertIn("historical-private-image-build-${{ inputs.benchmark_commit }}", raw)
        self.assertIn(
            "historical-private-profile-review-${{ github.sha }}-${{ github.run_id }}", raw
        )
        self.assertNotIn("confirm_temporary_cloudflare_resource_creation", raw)
        self.assertNotIn("confirm_registry_publication_and_qualification", raw)
        self.assertNotIn("$EXECUTOR", raw)
        self.assertNotIn("wrangler deploy", raw)
        self.assertNotIn("id-token: write", raw)
        self.assertFalse((ROOT / "scripts/run_historical_private_cloudflare_probe").exists())
        self.assertFalse((ROOT / "server/src/private-qualification-entry.ts").exists())

    def test_read_only_inspection_does_not_invoke_lake_config_loader(self) -> None:
        raw = WORKFLOW.read_text(encoding="utf-8")
        inspection = raw.split(
            "      - name: Build and inspect only the selected dedicated image", 1
        )[1].split(
            "      - name: Publish once and resolve the immutable registry digest", 1
        )[0]
        self.assertIn("docker run --rm --network none --read-only", inspection)
        self.assertIn(
            ".lake/build/bin/lean-eval validate-manifest >/dev/null", inspection
        )
        self.assertIn("test -x .lake/build/bin/extract_theorem", inspection)
        self.assertNotIn("lake exe", inspection)
        self.assertNotIn("lake --no-build", inspection)
        self.assertNotIn("--tmpfs", inspection)

    def test_existing_tag_resume_inspects_only_the_exact_remote_digest(self) -> None:
        raw = WORKFLOW.read_text(encoding="utf-8")
        registry = raw.split(
            "      - name: Publish once and resolve the immutable registry digest", 1
        )[1].split(
            "      - name: Run one offline official-entrypoint schema-v2 probe", 1
        )[0]
        resume_branch = registry.index('if test "$manifest_status" = 200; then')
        branch_end = registry.index("\n          fi\n", resume_branch)
        pull = registry.index('docker pull "$remote_image"')
        self.assertLess(branch_end, pull)
        self.assertIn(
            'remote_image="registry.cloudflare.com/$CLOUDFLARE_ACCOUNT_ID/'
            '$IMAGE_REPOSITORY@$image_digest"',
            registry,
        )
        probe = raw.split(
            "      - name: Run one offline official-entrypoint schema-v2 probe", 1
        )[1].split(
            "      - name: Render one canonical content-addressed qualification profile", 1
        )[0]
        self.assertEqual(probe.count('"$INSPECTION_IMAGE"'), 4)
        self.assertNotIn('"$IMAGE"', probe)
        self.assertIn("refresh_registry_credentials 30", registry)
        self.assertIn('timeout 1500s docker pull "$remote_image"', registry)
        self.assertIn("docker logout registry.cloudflare.com", registry)
        self.assertIn(
            '"$RUNNER_TEMP/registry-pulled-repo-digests.json"', registry
        )


if __name__ == "__main__":
    unittest.main()
