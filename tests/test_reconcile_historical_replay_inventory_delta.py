from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import reconcile_historical_replay_inventory_delta as delta_module
from inventory_historical_replay import canonical_inventory_bytes, inventory
from reconcile_historical_replay_inventory_delta import (
    InventoryDeltaError,
    canonical_delta_bytes,
    reconcile,
    write_exclusive,
)
from results_schema import canonical_file_bytes, result_id

INVENTORY_SCHEMA = json.loads(
    (ROOT / "schemas" / "historical-replay-inventory-v1.schema.json").read_text(
        encoding="utf-8"
    )
)


def document(user: str, *, public: bool, suffix: str) -> dict:
    return {
        "schema_version": 2,
        "user": user,
        "results": [
            {
                "result_id": result_id(user, "Model", f"problem_{suffix}", 1),
                "problem_id": f"problem_{suffix}",
                "statement_revision": 1,
                "declared_model": "Model",
                "accepted_at": "2026-08-25T00:00:00Z",
                "benchmark_commit": "b" * 40,
                "intake": {"kind": "issue", "issue_number": 1},
                "submission": {
                    "kind": "github_repo",
                    "repo": f"{user}/source",
                    "ref": "c" * 40,
                    "public": public,
                },
                "production_metadata": {},
            }
        ],
    }


def inventories() -> tuple[dict, bytes, dict, bytes]:
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        (root / "owner.json").write_bytes(
            canonical_file_bytes(document("Owner", public=True, suffix="old"))
        )
        baseline = inventory(root, "a" * 40)
        (root / "private.json").write_bytes(
            canonical_file_bytes(document("Private", public=False, suffix="new"))
        )
        (root / "public.json").write_bytes(
            canonical_file_bytes(document("Public", public=True, suffix="new"))
        )
        current = inventory(root, "d" * 40)
    return (
        baseline,
        canonical_inventory_bytes(baseline),
        current,
        canonical_inventory_bytes(current),
    )


class HistoricalReplayInventoryDeltaTests(unittest.TestCase):
    def test_append_only_delta_is_sorted_and_fully_bound(self) -> None:
        baseline, baseline_raw, current, current_raw = inventories()
        result = reconcile(
            baseline, baseline_raw, current, current_raw, INVENTORY_SCHEMA
        )
        self.assertEqual(result["delta_counts"]["result_count"], 2)
        self.assertEqual(
            result["delta_counts"]["public_source_probe_pending"], 1
        )
        self.assertEqual(
            result["delta_counts"]["private_archive_migration_pending"], 1
        )
        identities = [entry["result_id"] for entry in result["entries"]]
        self.assertEqual(identities, sorted(identities))
        self.assertEqual(
            result["baseline"]["inventory_sha256"],
            hashlib.sha256(baseline_raw).hexdigest(),
        )
        self.assertEqual(
            result["current"]["inventory_sha256"],
            hashlib.sha256(current_raw).hexdigest(),
        )
        self.assertEqual(result, json.loads(canonical_delta_bytes(result)))

    def test_removed_or_changed_baseline_result_fails_closed(self) -> None:
        baseline, baseline_raw, current, _ = inventories()
        baseline_id = baseline["entries"][0]["result_id"]

        removed = copy.deepcopy(current)
        removed["entries"] = [
            entry for entry in removed["entries"] if entry["result_id"] != baseline_id
        ]
        removed["result_count"] -= 1
        removed["classification_counts"]["public_source_probe_pending"] -= 1
        removed_raw = canonical_inventory_bytes(removed)
        with self.assertRaisesRegex(InventoryDeltaError, "removed baseline result"):
            reconcile(
                baseline, baseline_raw, removed, removed_raw, INVENTORY_SCHEMA
            )

        changed = copy.deepcopy(current)
        next(
            entry for entry in changed["entries"] if entry["result_id"] == baseline_id
        )["problem_id"] = "changed_problem"
        changed_raw = canonical_inventory_bytes(changed)
        with self.assertRaisesRegex(InventoryDeltaError, "changed baseline result"):
            reconcile(
                baseline, baseline_raw, changed, changed_raw, INVENTORY_SCHEMA
            )

    def test_unsorted_or_miscounted_inventory_fails_closed(self) -> None:
        baseline, baseline_raw, current, _ = inventories()
        current["entries"].reverse()
        current_raw = canonical_inventory_bytes(current)
        with self.assertRaisesRegex(InventoryDeltaError, "uniquely sorted"):
            reconcile(
                baseline, baseline_raw, current, current_raw, INVENTORY_SCHEMA
            )

        _, _, current, _ = inventories()
        current["classification_counts"]["public_source_probe_pending"] += 1
        current_raw = canonical_inventory_bytes(current)
        with self.assertRaisesRegex(InventoryDeltaError, "classification counts"):
            reconcile(
                baseline, baseline_raw, current, current_raw, INVENTORY_SCHEMA
            )

    def test_output_bound_and_exclusive_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            output = root / "delta.json"
            with (
                mock.patch.object(delta_module, "MAX_DELTA_BYTES", 1),
                self.assertRaisesRegex(InventoryDeltaError, "output size limit"),
            ):
                write_exclusive(output, {"large": "value"})
            self.assertFalse(output.exists())
            oversized = root / "oversized.json"
            oversized.write_text('{}\n', encoding="utf-8")
            with (
                mock.patch.object(delta_module, "MAX_DELTA_BYTES", 1),
                self.assertRaisesRegex(InventoryDeltaError, "input size limit"),
            ):
                delta_module._read_canonical_json(oversized, "inventory")

    def test_real_store_retains_known_results_beyond_frozen_baseline(self) -> None:
        baseline_path = (
            ROOT
            / "evidence"
            / "historical-replay"
            / "inventories"
            / "bb405fbabe084e106ad5500b455a05ba1e1d54175d1964db3aebcc3b6ea3fce3.json"
        )
        baseline_raw = baseline_path.read_bytes()
        baseline = json.loads(baseline_raw)
        current = inventory(ROOT / "results", "d" * 40)
        current_raw = canonical_inventory_bytes(current)
        result = reconcile(
            baseline, baseline_raw, current, current_raw, INVENTORY_SCHEMA
        )
        entries = {entry["result_id"]: entry for entry in result["entries"]}
        known_public_result_ids = {
            "r2_4139cfac63c01798ba59dca8768653d54173cd1bb6222283bcf9f63cdaf40c64",
            "r2_47d82df35938dfadf6d62e9db1412dc5a9a105136480118280c0bea4d42dd094",
            "r2_b972b53988ff4fdb1630cd09f5e337d6617d1d4c8c7afec2b0b11f29743814c9",
        }
        self.assertLessEqual(known_public_result_ids, entries.keys())
        self.assertTrue(
            all(
                entries[result_id]["source"]["readiness"]
                == "public_source_probe_pending"
                for result_id in known_public_result_ids
            )
        )
        self.assertEqual(result["delta_counts"]["result_count"], len(entries))


if __name__ == "__main__":
    unittest.main()
