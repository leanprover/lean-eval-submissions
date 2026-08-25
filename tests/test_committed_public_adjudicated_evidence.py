from __future__ import annotations

import hashlib
import json
import pathlib
import stat
import sys
import tempfile
import unittest
from collections import Counter

import jsonschema
from referencing import Registry, Resource

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from tests.frozen_results_tree import materialize_results_tree
from inventory_historical_replay import canonical_inventory_bytes, inventory
from prepare_public_replay_resolution import prepare

EVIDENCE = ROOT / "evidence" / "historical-public-replay-github-evidence-ba5f578.json"
EXPECTED_SHA256 = "ba816b52558cf77bd202618f820ffa6294ca2167698c94ab1096a39375c50212"
SOURCE_COMMIT = "ba5f5784427621f8b9be7396dd45a0938792707d"


class CommittedPublicAdjudicatedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = EVIDENCE.read_bytes()
        cls.value = json.loads(cls.raw)
        schemas = [
            json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            for name in (
                "public-replay-github-evidence-v1.schema.json",
                "public-replay-github-evidence-aggregate-v1.schema.json",
            )
        ]
        cls.schema = schemas[1]
        cls.registry = Registry().with_resources(
            [(schema["$id"], Resource.from_contents(schema)) for schema in schemas]
        )

    def test_asset_is_regular_source_free_schema_valid_json(self) -> None:
        self.assertTrue(stat.S_ISREG(EVIDENCE.stat(follow_symlinks=False).st_mode))
        self.assertLess(len(self.raw), 1_000_000)
        jsonschema.Draft202012Validator(self.schema, registry=self.registry).validate(
            self.value
        )

    def test_reviewed_bytes_and_inputs_are_immutable(self) -> None:
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), EXPECTED_SHA256)
        self.assertEqual(self.value["source_commit"], SOURCE_COMMIT)
        self.assertEqual(
            self.value["inventory_sha256"],
            "1a747133bba3c9ce09852967b4f3b4707bad64506890e4581bbf6f90a9be330c",
        )
        self.assertEqual(
            self.value["resolution_requests_sha256"],
            "bf78ab88b8612c3aa1d627eb9efdda4c0989ef4d55451e706f825108e22f37de",
        )
        self.assertEqual(
            self.value["workflow_definition_registry_sha256"],
            "b9004ee87f0ff032e78198e251b87fe1bb1d0baaf77d6ea853335dd1f5487108",
        )
        self.assertEqual(
            self.value["legacy_adjudication_registry_sha256"],
            "4df6682b0e8b0ff129235c286aebf3322f37b002c846cc9fc8b14c054acf4ed1",
        )

    def test_all_requests_and_shards_have_final_classifications(self) -> None:
        counts = Counter(item["status"] for item in self.value["resolutions"])
        self.assertEqual(counts, {"resolved": 128, "source_unavailable": 187})
        self.assertEqual(self.value["request_count"], 315)
        self.assertEqual(self.value["result_count"], 633)
        self.assertEqual(self.value["resolved_count"], 128)
        self.assertEqual(self.value["pending_count"], 187)
        for field in (
            "ambiguous_count",
            "evidence_missing_count",
            "probe_indeterminate_count",
            "source_indeterminate_count",
            "timing_indeterminate_count",
            "workflow_contract_unreviewed_count",
        ):
            self.assertEqual(self.value[field], 0)
        self.assertEqual(
            [item["shard_index"] for item in self.value["shards"]],
            list(range(16)),
        )

    def test_result_classification_counts_join_exact_recomputed_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results_root = materialize_results_tree(
                SOURCE_COMMIT, pathlib.Path(directory)
            )
            inventory_value = inventory(results_root, SOURCE_COMMIT)
            inventory_raw = canonical_inventory_bytes(inventory_value)
            inventory_sha256 = hashlib.sha256(inventory_raw).hexdigest()
            self.assertEqual(inventory_sha256, self.value["inventory_sha256"])

            requests = prepare(inventory_value, inventory_sha256, results_root)
            requests_raw = (
                json.dumps(requests, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(requests_raw).hexdigest(),
            self.value["resolution_requests_sha256"],
        )
        result_counts = {
            request["request_id"]: len(request["results"])
            for request in requests["requests"]
        }
        request_totals = Counter()
        result_totals = Counter()
        for resolution in self.value["resolutions"]:
            status = resolution["status"]
            request_totals[status] += 1
            result_totals[status] += result_counts[resolution["request_id"]]
        self.assertEqual(
            dict(request_totals), {"resolved": 128, "source_unavailable": 187}
        )
        self.assertEqual(
            dict(result_totals), {"resolved": 194, "source_unavailable": 439}
        )


if __name__ == "__main__":
    unittest.main()
