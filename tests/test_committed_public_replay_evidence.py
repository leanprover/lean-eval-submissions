from __future__ import annotations

import hashlib
import json
import pathlib
import stat
import unittest
from collections import Counter

import jsonschema
from referencing import Registry, Resource

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "historical-public-replay-github-evidence-5746f90.json"
EXPECTED_SHA256 = "13a0d95bd00cda236198d49c830159cb5790c9352b2fb1c6e94e07ec42787ecf"
SOURCE_COMMIT = "5746f90e72e863d96d992938aea0609978d1560c"


class CommittedPublicReplayEvidenceTests(unittest.TestCase):
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
            "96f9b9f4950af3836c3cd10639c18c3a320348cf77b080e74daef2c0d30c2a10",
        )
        self.assertEqual(
            self.value["resolution_requests_sha256"],
            "9eb418273c129781755a16cc28964391931a9f4203a0a8487ff246902c512656",
        )
        self.assertEqual(
            self.value["workflow_definition_registry_sha256"],
            "82eff4dce70c2fcb7f480522f4de1fb16884534ce5f9452032908bb299c12196",
        )

    def test_all_requests_and_shards_have_reviewed_classifications(self) -> None:
        counts = Counter(item["status"] for item in self.value["resolutions"])
        self.assertEqual(
            counts,
            {
                "resolved": 69,
                "source_unavailable": 184,
                "source_probe_indeterminate": 57,
                "timing_indeterminate": 2,
                "evidence_missing": 3,
            },
        )
        self.assertEqual(self.value["request_count"], 315)
        self.assertEqual(self.value["result_count"], 633)
        self.assertEqual(self.value["pending_count"], 246)
        self.assertEqual(
            [item["shard_index"] for item in self.value["shards"]],
            list(range(16)),
        )


if __name__ == "__main__":
    unittest.main()
