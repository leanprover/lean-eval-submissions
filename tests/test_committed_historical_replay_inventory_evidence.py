from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

import jsonschema

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_historical_replay_inventory_evidence import EvidenceError, validate

EVIDENCE = (
    ROOT
    / "evidence"
    / "historical-replay"
    / "inventory-evidence"
    / "run-32790927560-attempt-1.json"
)
EVIDENCE_SHA256 = "750cde40241f2a70783d486001af7856d9680bfc7cd8a2899421e45c635aaa16"
INVENTORY_SHA256 = "bb405fbabe084e106ad5500b455a05ba1e1d54175d1964db3aebcc3b6ea3fce3"
SOURCE_COMMIT = "ae1a9714c5433b4c195b8fdfb5643893ecac8019"
STORE_SHA256 = "14e8c8682e5183d85fee32aafcf06eedb20d7cd8aa91d666d50753d516da7d43"


class CommittedHistoricalReplayInventoryEvidenceTests(unittest.TestCase):
    def test_reviewed_inventory_and_closed_run_bindings(self) -> None:
        self.assertEqual(
            hashlib.sha256(EVIDENCE.read_bytes()).hexdigest(), EVIDENCE_SHA256
        )
        evidence = validate(EVIDENCE, ROOT)
        self.assertEqual(evidence["workflow_run_id"], 32790927560)
        self.assertEqual(evidence["workflow_run_attempt"], 1)
        self.assertEqual(evidence["workflow_conclusion"], "success")
        self.assertEqual(evidence["source_commit"], SOURCE_COMMIT)
        self.assertEqual(evidence["workflow_head_sha"], SOURCE_COMMIT)
        self.assertEqual(evidence["inventory_sha256"], INVENTORY_SHA256)
        self.assertEqual(evidence["results_store_sha256"], STORE_SHA256)
        self.assertEqual(evidence["result_count"], 1301)
        self.assertEqual(
            evidence["classification_counts"],
            {
                "private_archive_migration_pending": 668,
                "public_source_probe_pending": 633,
            },
        )
        self.assertEqual(evidence["evidence_status"], "closed")
        self.assertEqual(evidence["scope"], "contract_only_inventory")
        self.assertEqual(set(evidence["claims"].values()), {False})

    def test_reviewed_producer_and_transport_identities_are_exact(self) -> None:
        evidence = json.loads(EVIDENCE.read_bytes())
        self.assertEqual(
            evidence["producer"],
            {
                "inventory_generator_sha256": "acf0709f02b1644d065c00b450977b59756d60097a97a8ddc0a0f2eb23d3a5f3",
                "inventory_schema_sha256": "fe8bd9d2c0029f5e1a2687f7389ad40123c739d464ec60c1a2fa8e9462306b2b",
                "workflow_definition_sha256": "1d63e3bd5b0ce5c691e12967fdeea6f83d85efb4b2f827bdfc8fc8a9fca41df6",
            },
        )
        self.assertEqual(evidence["transport_artifact"]["artifact_id"], 9543093515)
        self.assertEqual(
            evidence["transport_artifact"]["archive_sha256"],
            "74e41292124b41d5dad62e9e83af0a4ee88b2050117507345a3b0c565d6199c1",
        )

    def test_laundered_inventory_bytes_and_stale_metadata_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            shutil.copytree(ROOT / "schemas", root / "schemas")
            evidence_path = root / EVIDENCE.relative_to(ROOT)
            inventory_path = (
                root
                / "evidence"
                / "historical-replay"
                / "inventories"
                / f"{INVENTORY_SHA256}.json"
            )
            evidence_path.parent.mkdir(parents=True)
            inventory_path.parent.mkdir(parents=True)
            shutil.copyfile(EVIDENCE, evidence_path)
            shutil.copyfile(ROOT / evidence_value()["inventory_path"], inventory_path)

            inventory_path.write_bytes(inventory_path.read_bytes() + b" ")
            with self.assertRaisesRegex(EvidenceError, "SHA-256"):
                validate(evidence_path, root)

            shutil.copyfile(ROOT / evidence_value()["inventory_path"], inventory_path)
            stale = copy.deepcopy(evidence_value())
            stale["source_commit"] = "f" * 40
            evidence_path.write_bytes(canonical_bytes(stale))
            with self.assertRaisesRegex(EvidenceError, "source_commit"):
                validate(evidence_path, root)

    def test_schema_rejects_claiming_replay_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            shutil.copytree(ROOT / "schemas", root / "schemas")
            evidence_path = root / EVIDENCE.relative_to(ROOT)
            inventory_path = root / evidence_value()["inventory_path"]
            evidence_path.parent.mkdir(parents=True)
            inventory_path.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / evidence_value()["inventory_path"], inventory_path)
            overclaim = copy.deepcopy(evidence_value())
            overclaim["claims"]["corpus_replay_qualified"] = True
            evidence_path.write_bytes(canonical_bytes(overclaim))
            with self.assertRaises(jsonschema.ValidationError):
                validate(evidence_path, root)


def evidence_value() -> dict:
    return json.loads(EVIDENCE.read_bytes())


def canonical_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


if __name__ == "__main__":
    unittest.main()
