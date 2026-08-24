from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from inventory_historical_replay import InventoryError, inventory  # noqa: E402
from results_schema import canonical_file_bytes, result_id  # noqa: E402


SOURCE_COMMIT = "a" * 40


def document(user: str, *, public: bool, suffix: str) -> dict:
    record = {
        "result_id": result_id(user, "Model", f"problem_{suffix}", 1),
        "problem_id": f"problem_{suffix}",
        "statement_revision": 1,
        "declared_model": "Model",
        "accepted_at": "2026-08-24T00:00:00Z",
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
    return {"schema_version": 2, "user": user, "results": [record]}


class HistoricalReplayInventoryTests(unittest.TestCase):
    def write(self, root: pathlib.Path, name: str, value: dict) -> None:
        (root / name).write_bytes(canonical_file_bytes(value))

    def test_inventory_is_sorted_complete_and_source_minimized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            private = document("PrivateOwner", public=False, suffix="private")
            public = document("PublicOwner", public=True, suffix="public")
            self.write(root, "privateowner.json", private)
            self.write(root, "publicowner.json", public)

            first = inventory(root, SOURCE_COMMIT)
            second = inventory(root, SOURCE_COMMIT)
            self.assertEqual(first, second)
            self.assertEqual(first["result_count"], 2)
            self.assertEqual(
                first["classification_counts"],
                {
                    "public_source_probe_pending": 1,
                    "private_archive_migration_pending": 1,
                },
            )
            self.assertEqual(
                [entry["result_id"] for entry in first["entries"]],
                sorted(entry["result_id"] for entry in first["entries"]),
            )
            by_visibility = {
                entry["source"]["visibility"]: entry for entry in first["entries"]
            }
            self.assertEqual(
                by_visibility["public"]["source"]["repository"],
                "PublicOwner/source",
            )
            self.assertEqual(by_visibility["public"]["source"]["commit"], "c" * 40)
            self.assertNotIn("repository", by_visibility["private"]["source"])
            self.assertNotIn("commit", by_visibility["private"]["source"])
            serialized_private = json.dumps(by_visibility["private"], sort_keys=True)
            self.assertNotIn("PrivateOwner/source", serialized_private)

    def test_duplicate_result_id_and_noncanonical_commit_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            original = document("Owner", public=True, suffix="same")
            duplicate = copy.deepcopy(original)
            self.write(root, "owner.json", original)
            self.write(root, "Owner.json", duplicate)
            with self.assertRaisesRegex(InventoryError, "duplicate result_id"):
                inventory(root, SOURCE_COMMIT)
            with self.assertRaisesRegex(InventoryError, "full lowercase Git SHA"):
                inventory(root, "A" * 40)

    def test_filename_stem_and_repository_segments_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write(root, "different.json", document("Owner", public=True, suffix="one"))
            with self.assertRaisesRegex(InventoryError, "filename stem"):
                inventory(root, SOURCE_COMMIT)

        for repository in ("./source", "owner/.."):
            with self.subTest(repository=repository), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                value = document("Owner", public=True, suffix="one")
                value["results"][0]["submission"]["repo"] = repository
                (root / "owner.json").write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(InventoryError, "repo"):
                    inventory(root, SOURCE_COMMIT)

    def test_non_json_or_nested_results_entry_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write(root, "owner.json", document("Owner", public=True, suffix="one"))
            (root / "ignored.txt").write_text("must not be ignored", encoding="utf-8")
            with self.assertRaisesRegex(InventoryError, "canonical JSON file"):
                inventory(root, SOURCE_COMMIT)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write(root, "owner.json", document("Owner", public=True, suffix="one"))
            (root / "nested").mkdir()
            with self.assertRaisesRegex(InventoryError, "canonical JSON file"):
                inventory(root, SOURCE_COMMIT)

    def test_empty_results_store_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.write(
                root,
                "owner.json",
                {"schema_version": 2, "user": "Owner", "results": []},
            )
            with self.assertRaisesRegex(InventoryError, "no accepted results"):
                inventory(root, SOURCE_COMMIT)

    def test_real_store_is_fully_accounted_without_private_locators(self) -> None:
        result = inventory(ROOT / "results", SOURCE_COMMIT)
        self.assertEqual(result["result_count"], len(result["entries"]))
        self.assertEqual(
            result["result_count"],
            sum(result["classification_counts"].values()),
        )
        identifiers = [entry["result_id"] for entry in result["entries"]]
        self.assertEqual(identifiers, sorted(set(identifiers)))
        for entry in result["entries"]:
            if entry["source"]["visibility"] == "private":
                self.assertEqual(
                    set(entry["source"]),
                    {"kind", "visibility", "readiness"},
                )


if __name__ == "__main__":
    unittest.main()
