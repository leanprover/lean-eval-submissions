from __future__ import annotations

import hashlib
import json
import pathlib
import stat
import sys
import tempfile
import unittest
from collections import Counter
from typing import Any

import jsonschema

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from classify_historical_private_archives import canonical_output_bytes
from inventory_historical_replay import inventory
from tests.frozen_results_tree import materialize_results_tree

EXPECTED_SHA256 = (
    "dfdcbc0da3a3526f8a26e6a69cefa41cbcd92de7608752193b742fcd92b00a67"
)
CROSSWALK = (
    ROOT
    / "evidence"
    / "historical-replay"
    / "private-crosswalks"
    / f"{EXPECTED_SHA256}.json"
)
RESULTS_COMMIT = "7fb2e762e5470ae1929dbe069dbcd0c8488b51d7"
RESULTS_STORE_SHA256 = (
    "9e998ab47ae719484e2ea283271086d2c66c95051837231014fd74392f4fb1c0"
)
AUDIT_COMMIT = "ad356e7bc5a2d650d9902ac3f6d352a0164360bc"
ARCHIVE_INVENTORY_DIGEST = (
    "6b8867f41a13c3ba323746988058886e5dc73da7b509deaf01ccf9c36fe8d5d4"
)
ALLOWED_KEYS = {
    "archive_count",
    "archive_identity_ambiguous",
    "archive_inventory_digest",
    "archive_metadata_conflict",
    "archive_not_found",
    "archive_plan_entry_sha256",
    "archive_result_evidence",
    "archive_schema_version",
    "audit_commit",
    "audit_repository",
    "benchmark_relation",
    "bound",
    "classification",
    "classification_counts",
    "entries",
    "private_result_count",
    "result_id",
    "results_commit",
    "results_repository",
    "results_store_sha256",
    "schema_version",
    "submission_id",
}
FORBIDDEN_KEYS = {
    "archive_path",
    "ciphertext_sha256",
    "declared_model",
    "issue_number",
    "problem_id",
    "sha256_ciphertext",
    "sidecar_sha256",
    "source_path",
    "submission_ref",
    "submission_repo",
    "submitter",
}


def recursive_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(
            *(recursive_keys(item) for item in value.values())
        )
    if isinstance(value, list):
        return set().union(*(recursive_keys(item) for item in value))
    return set()


class CommittedPrivateArchiveCrosswalkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = CROSSWALK.read_bytes()
        cls.value = json.loads(cls.raw)
        cls.schema = json.loads(
            (
                ROOT
                / "schemas"
                / "historical-private-archive-crosswalk-v1.schema.json"
            ).read_text(encoding="utf-8")
        )

    def test_asset_is_canonical_regular_schema_valid_json(self) -> None:
        self.assertTrue(stat.S_ISREG(CROSSWALK.stat(follow_symlinks=False).st_mode))
        self.assertLess(len(self.raw), 1_000_000)
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), EXPECTED_SHA256)
        self.assertEqual(self.raw, canonical_output_bytes(self.value))
        jsonschema.Draft202012Validator(self.schema).validate(self.value)

    def test_reviewed_inputs_and_counts_are_immutable(self) -> None:
        self.assertEqual(
            self.value["results_repository"], "leanprover/lean-eval-submissions"
        )
        self.assertEqual(self.value["results_commit"], RESULTS_COMMIT)
        self.assertEqual(self.value["results_store_sha256"], RESULTS_STORE_SHA256)
        self.assertEqual(self.value["private_result_count"], 668)
        self.assertEqual(
            self.value["audit_repository"], "leanprover/lean-eval-audit"
        )
        self.assertEqual(self.value["audit_commit"], AUDIT_COMMIT)
        self.assertEqual(
            self.value["archive_inventory_digest"], ARCHIVE_INVENTORY_DIGEST
        )
        self.assertEqual(self.value["archive_count"], 1045)
        self.assertEqual(
            self.value["classification_counts"],
            {
                "archive_identity_ambiguous": 0,
                "archive_metadata_conflict": 0,
                "archive_not_found": 29,
                "bound": 639,
            },
        )

    def test_entries_are_complete_sorted_and_unique(self) -> None:
        entries = self.value["entries"]
        result_ids = [entry["result_id"] for entry in entries]
        self.assertEqual(result_ids, sorted(result_ids))
        self.assertEqual(len(result_ids), len(set(result_ids)))
        self.assertEqual(
            Counter(entry["classification"] for entry in entries),
            Counter(self.value["classification_counts"]),
        )

        with tempfile.TemporaryDirectory() as directory:
            results_root = materialize_results_tree(
                RESULTS_COMMIT, pathlib.Path(directory)
            )
            inventory_value = inventory(results_root, RESULTS_COMMIT)
        self.assertEqual(
            inventory_value["results_store_sha256"], RESULTS_STORE_SHA256
        )
        self.assertEqual(inventory_value["result_count"], 1304)
        private_result_ids = sorted(
            entry["result_id"]
            for entry in inventory_value["entries"]
            if entry["source"]["visibility"] == "private"
        )
        self.assertEqual(result_ids, private_result_ids)

    def test_entries_have_only_the_reviewed_source_free_shape(self) -> None:
        self.assertEqual(recursive_keys(self.value), ALLOWED_KEYS)
        self.assertTrue(recursive_keys(self.value).isdisjoint(FORBIDDEN_KEYS))

        bound_entries = [
            entry
            for entry in self.value["entries"]
            if entry["classification"] == "bound"
        ]
        not_found_entries = [
            entry
            for entry in self.value["entries"]
            if entry["classification"] == "archive_not_found"
        ]
        self.assertTrue(
            all(
                set(entry)
                == {
                    "archive_plan_entry_sha256",
                    "archive_result_evidence",
                    "archive_schema_version",
                    "benchmark_relation",
                    "classification",
                    "result_id",
                    "submission_id",
                }
                for entry in bound_entries
            )
        )
        self.assertTrue(
            all(
                set(entry) == {"classification", "result_id"}
                for entry in not_found_entries
            )
        )
        self.assertEqual(
            Counter(entry["archive_schema_version"] for entry in bound_entries),
            {1: 639},
        )
        self.assertEqual(
            Counter(entry["archive_result_evidence"] for entry in bound_entries),
            {"confirmed_pass": 576, "legacy_unrecorded": 63},
        )
        self.assertEqual(
            Counter(entry["benchmark_relation"] for entry in bound_entries),
            {"archive_recorded_different": 8, "same": 631},
        )


if __name__ == "__main__":
    unittest.main()
