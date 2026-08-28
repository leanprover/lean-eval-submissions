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

from aggregate_public_replay_github_evidence import validate_aggregate
from inventory_historical_replay import canonical_inventory_bytes, inventory
from prepare_public_replay_resolution import prepare
from tests.frozen_results_tree import materialize_results_tree

EVIDENCE = ROOT / "evidence" / "historical-public-replay-github-evidence-current.json"
EXPECTED_SHA256 = "7c10dfc3e3d66f6f9ae0107ef2ed94b8f731d7f8410741ed3f5978dc55e149e5"
SOURCE_COMMIT = "844ade95c0a432e63a84798f84969b8d9f2f53a3"
INVENTORY_SHA256 = "b17c24071e3945ceb1b0e8fe492b90e868a89a064d8ae2cd033b7f787ec27780"
REQUESTS_SHA256 = "b12d436e03ed6fe2af29f9ac04b05498570ce117610302dd10aa890183c56840"
LORENZO_RESULT_COUNTS = {
    "prr_10c3aa786395e4843b78b4487e980f20a38fff4635596b62e65bfe3bb2f38edf": 1,
    "prr_4d6e17c3a6863434b74552796c6d73f2f80721cca6c9a4c324927fd10a8ddfe2": 1,
    "prr_526c2ad9ff47cbcbcfa2caf7de9c66bc1edf70d559b0d3ea8e85a713c04227e7": 1,
    "prr_7d339b86f91735d0e873978c01a5164022ad634273d6b83bbcd61ba4fc4c2fa4": 1,
    "prr_bde8cdbf5a23594049f0435ab0d897f2ff51a32570e0a6594d88c02a4aae3fa5": 8,
    "prr_dd473278fd5efe1ec2b22bab0304287605e894e470afa3652de7bd358b02664a": 1,
    "prr_ee2ae925f65eeccb22b2fff2d85d8513d0d5b879c758d09dcbeea2e0b27b26f9": 6,
    "prr_fe48499d222f9fc329f7e75265fbd6d28cbafad49a3cfa99ee712f41d672bb9a": 1,
}


class CommittedCurrentPublicReplayEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = EVIDENCE.read_bytes()
        cls.value = json.loads(cls.raw)
        cls.workflow_raw = (
            ROOT / "configuration" / "public-replay-workflow-definitions-v1.json"
        ).read_bytes()
        cls.workflow = json.loads(cls.workflow_raw)
        cls.adjudication_raw = (
            ROOT / "configuration" / "public-replay-legacy-adjudications-v1.json"
        ).read_bytes()
        cls.adjudications = json.loads(cls.adjudication_raw)
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
        jsonschema.Draft202012Validator(
            self.schema, registry=self.registry
        ).validate(self.value)

    def test_exact_current_input_identity_is_frozen(self) -> None:
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), EXPECTED_SHA256)
        self.assertEqual(self.value["source_commit"], SOURCE_COMMIT)
        self.assertEqual(self.value["inventory_sha256"], INVENTORY_SHA256)
        self.assertEqual(self.value["resolution_requests_sha256"], REQUESTS_SHA256)
        self.assertEqual(
            self.value["workflow_definition_registry_sha256"],
            hashlib.sha256(self.workflow_raw).hexdigest(),
        )
        self.assertEqual(
            self.value["legacy_adjudication_registry_sha256"],
            hashlib.sha256(self.adjudication_raw).hexdigest(),
        )

    def test_current_statuses_remain_nonterminal_plan_inputs(self) -> None:
        self.assertEqual(
            Counter(item["status"] for item in self.value["resolutions"]),
            {"resolved": 123, "source_unavailable": 195},
        )
        self.assertEqual(self.value["request_count"], 318)
        self.assertEqual(self.value["result_count"], 636)
        self.assertEqual(self.value["resolved_count"], 123)
        self.assertEqual(self.value["pending_count"], 195)
        for field in (
            "ambiguous_count",
            "evidence_missing_count",
            "probe_indeterminate_count",
            "source_indeterminate_count",
            "timing_indeterminate_count",
            "workflow_contract_unreviewed_count",
        ):
            self.assertEqual(self.value[field], 0)

    def test_producer_validation_joins_the_exact_results_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results_root = materialize_results_tree(
                SOURCE_COMMIT, pathlib.Path(directory)
            )
            inventory_value = inventory(results_root, SOURCE_COMMIT)
            inventory_raw = canonical_inventory_bytes(inventory_value)
            self.assertEqual(hashlib.sha256(inventory_raw).hexdigest(), INVENTORY_SHA256)
            requests = prepare(inventory_value, INVENTORY_SHA256, results_root)
            requests_raw = (
                json.dumps(requests, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8")
        self.assertEqual(hashlib.sha256(requests_raw).hexdigest(), REQUESTS_SHA256)
        validate_aggregate(
            self.value,
            requests,
            REQUESTS_SHA256,
            self.workflow,
            hashlib.sha256(self.workflow_raw).hexdigest(),
            self.adjudications,
            hashlib.sha256(self.adjudication_raw).hexdigest(),
        )
        result_counts = {
            request["request_id"]: len(request["results"])
            for request in requests["requests"]
        }
        totals: Counter[str] = Counter()
        statuses = {
            resolution["request_id"]: resolution["status"]
            for resolution in self.value["resolutions"]
        }
        for request_id, status in statuses.items():
            totals[status] += result_counts[request_id]
        self.assertEqual(
            totals,
            {"resolved": 177, "source_unavailable": 459},
        )
        self.assertEqual(
            {request_id: result_counts[request_id] for request_id in LORENZO_RESULT_COUNTS},
            LORENZO_RESULT_COUNTS,
        )
        self.assertEqual(sum(LORENZO_RESULT_COUNTS.values()), 20)
        self.assertTrue(
            all(
                statuses[request_id] == "source_unavailable"
                for request_id in LORENZO_RESULT_COUNTS
            )
        )


if __name__ == "__main__":
    unittest.main()
