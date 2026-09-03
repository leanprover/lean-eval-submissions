from __future__ import annotations

import hashlib
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from migrate_archive_envelopes import (
    PLAN_ENTRY_DIGEST_DOMAIN,
    _canonical_bytes,
    _require_canonical_selection,
)
from prepare_historical_final_delta_archive_migration import (
    SelectionError,
    canonical,
    select,
)
from test_prepare_historical_final_delta_packet import Fixture


class FinalDeltaArchiveSelectionTests(unittest.TestCase):
    def test_canonical_rejects_nonfinite_numbers(self) -> None:
        with self.assertRaisesRegex(SelectionError, "canonicalizable"):
            canonical({"bad": float("nan")})

    def test_selects_only_unique_legacy_delta_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            packet = fixture.build()
            source = {
                "source_path": "archives/legacy.tar.age",
                "source_schema_version": 1,
                "source_ciphertext_sha256": "1" * 64,
                "source_sidecar_sha256": "2" * 64,
                "plaintext_sha256": "3" * 64,
                "plaintext_size_bytes": 123,
                "submission_id": "018f1f5e-7b2a-7abc-8def-0123456789ab",
                "target_path": "archives/01/018f1f5e-7b2a-7abc-8def-0123456789ab.tar.age",
            }
            digest = hashlib.sha256(
                PLAN_ENTRY_DIGEST_DOMAIN + _canonical_bytes(source)
            ).hexdigest()
            legacy = next(
                item
                for item in packet["entries"]
                if item["result_id"] == fixture.ids["private_legacy"]
            )
            legacy["archive"]["archive_plan_entry_sha256"] = digest
            legacy["packet_entry_sha256"] = "0" * 64
            packet_raw = canonical(packet)
            full_plan = {
                "schema_version": 1,
                "source_repository": "leanprover/lean-eval-audit",
                "source_commit": fixture.audit_commit,
                "entries": [source],
                "retained": [],
                "migration_count": 1,
                "retained_count": 0,
                "inventory_digest": "4" * 64,
            }
            plan, binding = select(
                preparation=packet,
                preparation_raw=packet_raw,
                preparation_commit=packet["classification_inputs"][
                    "private_crosswalk"
                ]["commit"],
                preparation_path="evidence/historical-replay/final-delta-preparations/"
                + hashlib.sha256(packet_raw).hexdigest()
                + ".json",
                full_plan=full_plan,
                audit_tree="b" * 40,
                crosswalk_path="evidence/historical-replay/private-crosswalks/"
                + packet["classification_inputs"]["private_crosswalk"]["sha256"]
                + ".json",
            )
        self.assertEqual(plan["entries"], [source])
        self.assertEqual(plan["migration_count"], 1)
        self.assertEqual(plan["retained"], [])
        self.assertEqual(
            binding["review_branch"], "historical-final-delta-archive-rewrap-v1"
        )
        _require_canonical_selection(plan, binding)

    def test_rejects_missing_archive_plan_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            packet = fixture.build()
            raw = canonical(packet)
            with self.assertRaisesRegex(SelectionError, "resolve uniquely"):
                select(
                    preparation=packet,
                    preparation_raw=raw,
                    preparation_commit=packet["classification_inputs"][
                        "private_crosswalk"
                    ]["commit"],
                    preparation_path="evidence/historical-replay/final-delta-preparations/"
                    + hashlib.sha256(raw).hexdigest()
                    + ".json",
                    full_plan={
                        "source_repository": "leanprover/lean-eval-audit",
                        "source_commit": fixture.audit_commit,
                        "entries": [],
                    },
                    audit_tree="b" * 40,
                    crosswalk_path="evidence/historical-replay/private-crosswalks/"
                    + packet["classification_inputs"]["private_crosswalk"]["sha256"]
                    + ".json",
                )


if __name__ == "__main__":
    unittest.main()
