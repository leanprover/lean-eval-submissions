import copy
import hashlib
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from inventory_historical_replay import inventory  # noqa: E402
from prepare_public_replay_resolution import ResolutionError, prepare  # noqa: E402


SOURCE_COMMIT = "ae0ca77efab2281b90b20a194e2122d413f80313"


class PublicReplayResolutionPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = inventory(ROOT / "results", SOURCE_COMMIT)
        cls.inventory_bytes = (
            json.dumps(cls.inventory, indent=2, sort_keys=True) + "\n"
        ).encode()
        cls.output = prepare(
            cls.inventory,
            hashlib.sha256(cls.inventory_bytes).hexdigest(),
            ROOT / "results",
        )

    def test_covers_every_public_result_once_deterministically(self) -> None:
        self.assertEqual(self.output["request_count"], 315)
        self.assertEqual(self.output["result_count"], 633)
        result_ids = [
            result["result_id"]
            for request in self.output["requests"]
            for result in request["results"]
        ]
        self.assertEqual(len(result_ids), len(set(result_ids)))
        self.assertEqual(
            [request["request_id"] for request in self.output["requests"]],
            sorted(request["request_id"] for request in self.output["requests"]),
        )
        for request in self.output["requests"]:
            self.assertRegex(request["owner"], r"^[A-Za-z0-9-]+$")
            self.assertEqual(
                {result["owner"] for result in request["results"]},
                {request["owner"]},
            )
            self.assertEqual(
                [result["result_id"] for result in request["results"]],
                sorted(result["result_id"] for result in request["results"]),
            )

    def test_keeps_both_issue_repositories_until_evidence_resolves_one(self) -> None:
        request = next(
            item
            for item in self.output["requests"]
            if item["issue_number"] == 144
            and item["declared_model"] == "GPT-5.5 Codex"
        )
        self.assertEqual(
            request["candidate_issue_repositories"],
            ["leanprover/lean-eval", "leanprover/lean-eval-submissions"],
        )
        self.assertEqual(
            request["benchmark"]["commit"],
            "11081d345a580a0f3c46699240f28e4f41fbf9fe",
        )
        self.assertEqual(len(request["results"]), 2)

    def test_groups_shared_submission_without_losing_large_result_sets(self) -> None:
        largest = max(self.output["requests"], key=lambda value: len(value["results"]))
        self.assertEqual(len(largest["results"]), 138)
        self.assertEqual(len({item["result_id"] for item in largest["results"]}), 138)

    def test_rejects_inventory_not_exactly_recomputed_from_results(self) -> None:
        changed = copy.deepcopy(self.inventory)
        public = next(
            entry
            for entry in changed["entries"]
            if entry["source"]["visibility"] == "public"
        )
        public["accepted_at"] = "2026-01-01T00:00:00Z"
        with self.assertRaisesRegex(ResolutionError, "does not equal"):
            prepare(changed, "0" * 64, ROOT / "results")

    def test_output_contains_no_private_inventory_entry(self) -> None:
        public_ids = {
            entry["result_id"]
            for entry in self.inventory["entries"]
            if entry["source"]["visibility"] == "public"
        }
        output_ids = {
            result["result_id"]
            for request in self.output["requests"]
            for result in request["results"]
        }
        self.assertEqual(output_ids, public_ids)
        self.assertTrue(
            all(
                request["source"]["visibility"] == "public"
                for request in self.output["requests"]
            )
        )

    def test_published_schema_is_strict_draft_2020_json(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "schemas"
                / "public-replay-resolution-requests-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIs(schema["additionalProperties"], False)
        self.assertIs(
            schema["$defs"]["request"]["additionalProperties"], False
        )
