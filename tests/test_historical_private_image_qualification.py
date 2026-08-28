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

import historical_private_image_qualification as qualification  # noqa: E402
from prepare_historical_private_replay import canonical  # noqa: E402
from replay_orchestrator import config_digest  # noqa: E402


MATRIX = ROOT / "configuration/historical-private-replay-image-matrix-v1.json"
SCHEMA = ROOT / "schemas/historical-private-profile-qualification-v1.schema.json"
WORKFLOW = ROOT / ".github/workflows/historical-private-image-qualification.yml"


class HistoricalPrivateImageQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix_raw = MATRIX.read_bytes()
        cls.matrix = json.loads(cls.matrix_raw)
        cls.entry = cls.matrix["images"][0]
        cls.source_commit = "1" * 40
        cls.candidate = qualification.select_candidate(
            MATRIX,
            cls.entry["benchmark_commit"],
            ROOT,
            cls.source_commit,
        )

    def receipt(self, manifest: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": qualification.RECEIPT_KIND,
            "registry_manifest_digest": manifest,
            "benchmark_commit": self.entry["benchmark_commit"],
            "runner_entrypoint": "/opt/lean-eval/replay-authoritative",
            "archive_expectation_schema_version": 2,
            "key_material_type": "age-file-key-v1",
            "network_probe": "blocked",
            "status": "passed",
            "architecture": "x86_64",
            "kernel_release": "6.18.36-cloudflare-firecracker-2026.6.17",
            "cpu_model": "AMD EPYC",
        }

    def test_selector_accepts_exactly_the_closed_63_entry_matrix(self) -> None:
        images = qualification.validate_matrix(self.matrix, self.matrix_raw)
        self.assertEqual(len(images), 63)
        self.assertEqual(
            hashlib.sha256(self.matrix_raw).hexdigest(), qualification.MATRIX_SHA256
        )
        self.assertEqual(self.candidate["benchmark_commit"], self.entry["benchmark_commit"])
        self.assertEqual(
            self.candidate["matrix_entry_sha256"],
            hashlib.sha256(canonical(self.entry)).hexdigest(),
        )
        self.assertEqual(
            self.candidate["profile_lock"]["runner_profile"],
            "cloudflare-sandbox-standard-4-v1",
        )

    def test_selector_binds_the_complete_final_source_blob_closure(self) -> None:
        self.assertEqual(
            set(self.candidate["source_blobs"]), set(qualification.SOURCE_PATHS)
        )
        for name, relative in qualification.SOURCE_PATHS.items():
            blob = self.candidate["source_blobs"][name]
            self.assertEqual(blob["path"], relative)
            self.assertEqual(
                blob["sha256"], hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            )

    def test_matrix_change_or_unknown_commit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            changed = copy.deepcopy(self.matrix)
            changed["images"][0]["workspace_count"] += 1
            path = pathlib.Path(raw) / "matrix.json"
            path.write_bytes(canonical(changed))
            with self.assertRaisesRegex(
                qualification.QualificationError, "matrix identity changed"
            ):
                qualification.select_candidate(
                    path, self.entry["benchmark_commit"], ROOT, self.source_commit
                )
        with self.assertRaisesRegex(
            qualification.QualificationError, "does not select exactly one"
        ):
            qualification.select_candidate(MATRIX, "0" * 40, ROOT, self.source_commit)

    def test_renderer_produces_the_final_private_profile_schema_shape(self) -> None:
        manifest = "sha256:" + "2" * 64
        workflow_sha = hashlib.sha256(WORKFLOW.read_bytes()).hexdigest()
        execution_digest, profile = qualification.render_qualification(
            self.candidate,
            self.receipt(manifest),
            manifest,
            "3" * 40,
            workflow_sha,
            1234,
            2,
        )
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        plan_schema = json.loads(
            (ROOT / "schemas/historical-private-replay-plan-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        registry = Registry().with_resources(
            [
                (value["$id"], Resource.from_contents(value))
                for value in (schema, plan_schema)
            ]
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema, registry=registry).validate(profile)
        self.assertEqual(set(profile), set(schema["required"]))
        self.assertEqual(profile["qualification_status"], "qualified")
        self.assertEqual(profile["registry_manifest_digest"], manifest)
        self.assertEqual(profile["execution_profile"]["vm_image_digest"], manifest)
        self.assertEqual(
            profile["qualification"]["private_archive_probe"],
            {
                "archive_expectation_schema_version": 2,
                "key_material_type": "age-file-key-v1",
                "runner_entrypoint": "/opt/lean-eval/replay-authoritative",
                "status": "passed",
            },
        )
        self.assertEqual(profile["qualification"]["network_probe"], "blocked")
        self.assertEqual(
            execution_digest,
            config_digest(
                "lean-eval-replay-execution-profile-v1",
                profile["execution_profile"],
            ),
        )
        self.assertEqual(profile["execution_profile_digest"], execution_digest)
        self.assertEqual(
            profile["measurement_config_digest"],
            config_digest(
                "lean-eval-replay-measurement-config-v1",
                profile["measurement_config"],
            ),
        )
        self.assertEqual(canonical(profile), canonical(json.loads(canonical(profile))))

    def test_renderer_refuses_non_v2_or_non_blocked_receipts(self) -> None:
        manifest = "sha256:" + "2" * 64
        for field, value in (
            ("archive_expectation_schema_version", 1),
            ("key_material_type", "age-identity-v1"),
            ("network_probe", "available"),
            ("status", "failed"),
        ):
            with self.subTest(field=field):
                receipt = self.receipt(manifest)
                receipt[field] = value
                with self.assertRaisesRegex(
                    qualification.QualificationError, "passing exact probe"
                ):
                    qualification.render_qualification(
                        self.candidate,
                        receipt,
                        manifest,
                        "3" * 40,
                        "4" * 64,
                        1234,
                        1,
                    )

    def test_workflow_is_manual_single_entry_and_fails_before_publication(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertNotIn("strategy:", workflow)
        self.assertIn("environment: cloudflare-production", workflow)
        self.assertIn("confirm_registry_publication_and_qualification", workflow)
        self.assertIn("publish-and-qualify-one-private-image", workflow)
        self.assertIn("benchmark commit does not select exactly one of 63 images", (
            ROOT / "scripts/historical_private_image_qualification.py"
        ).read_text(encoding="utf-8"))
        gate = workflow.index("if [ ! -x \"$EXECUTOR\" ]")
        build = workflow.index("docker build --progress=plain")
        publish = workflow.index("wrangler containers push")
        self.assertLess(gate, build)
        self.assertLess(gate, publish)
        self.assertFalse((ROOT / "scripts/run_historical_private_cloudflare_probe").exists())

    def test_workflow_scopes_cloudflare_and_review_write_authority(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(workflow.count("CLOUDFLARE_ACCOUNT_ID: ${{ secrets."), 1)
        self.assertEqual(workflow.count("CLOUDFLARE_API_TOKEN: ${{ secrets."), 1)
        self.assertIn('test -z "${CLOUDFLARE_ACCOUNT_ID:-}"', workflow)
        self.assertIn('test -z "${CLOUDFLARE_API_TOKEN:-}"', workflow)
        self.assertIn("archive-expectation-schema-version 2", workflow)
        self.assertIn("key-material-type age-file-key-v1", workflow)
        self.assertIn("--network disabled", workflow)
        self.assertIn("stage-isolated-review-branch:", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("historical-private-profile-review-${{ github.sha }}", workflow)
        self.assertIn(
            'git push origin "HEAD:refs/heads/$REVIEW_BRANCH"', workflow
        )
        self.assertNotIn("lean-eval-state", workflow)


if __name__ == "__main__":
    unittest.main()
