from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from inventory_historical_replay import canonical_inventory_bytes, inventory
from prepare_historical_final_delta_packet import (
    FinalDeltaError,
    build_packet,
    canonical,
    entry_sha256,
    write_exclusive,
)
from prepare_historical_final_delta_public_decisions import (
    PublicDecisionError,
    build_decisions,
)
from reconcile_historical_replay_inventory_delta import canonical_delta_bytes, reconcile
from results_schema import canonical_file_bytes, result_id

INVENTORY_SCHEMA = json.loads(
    (ROOT / "schemas" / "historical-replay-inventory-v1.schema.json").read_text(
        encoding="utf-8"
    )
)
PACKET_SCHEMA = json.loads(
    (ROOT / "schemas" / "historical-final-delta-preparation-v1.schema.json").read_text(
        encoding="utf-8"
    )
)


def record(
    owner: str,
    suffix: str,
    *,
    public: bool,
    benchmark: str,
) -> dict:
    problem = f"problem_{suffix}"
    return {
        "result_id": result_id(owner, f"Model {suffix}", problem, 1),
        "problem_id": problem,
        "statement_revision": 1,
        "declared_model": f"Model {suffix}",
        "accepted_at": "2026-09-30T00:00:00Z",
        "benchmark_commit": benchmark,
        "intake": {"kind": "issue", "issue_number": 1},
        "submission": {
            "kind": "github_repo",
            "repo": f"{owner}/source-{suffix}",
            "ref": (suffix[0] * 40),
            "public": public,
        },
        "production_metadata": {},
    }


def document(owner: str, records: list[dict]) -> dict:
    return {"schema_version": 2, "user": owner, "results": records}


class Fixture:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.results = root / "results"
        self.results.mkdir()
        self.baseline_results = root / "baseline-results"
        self.baseline_results.mkdir()
        self.baseline_path = root / "baseline.json"
        self.current_path = root / "current.json"
        self.delta_path = root / "delta.json"
        self.decisions_path = root / "decisions.json"
        self.crosswalk_path = root / "crosswalk.json"
        self.source_commit = "d" * 40
        self.audit_commit = "e" * 40
        self.public_benchmark = "1" * 40
        self.private_benchmark = "2" * 40

        old = record("old", "a", public=True, benchmark="3" * 40)
        public_available = record(
            "public", "b", public=True, benchmark=self.public_benchmark
        )
        public_unavailable = record("public", "c", public=True, benchmark="4" * 40)
        private_legacy = record(
            "private", "d", public=False, benchmark=self.private_benchmark
        )
        private_migrated = record(
            "private", "e", public=False, benchmark=self.private_benchmark
        )
        self.ids = {
            "public_available": public_available["result_id"],
            "public_unavailable": public_unavailable["result_id"],
            "private_legacy": private_legacy["result_id"],
            "private_migrated": private_migrated["result_id"],
        }
        (self.baseline_results / "old.json").write_bytes(
            canonical_file_bytes(document("old", [old]))
        )
        (self.results / "old.json").write_bytes(
            canonical_file_bytes(document("old", [old]))
        )
        (self.results / "public.json").write_bytes(
            canonical_file_bytes(
                document("public", [public_available, public_unavailable])
            )
        )
        (self.results / "private.json").write_bytes(
            canonical_file_bytes(
                document("private", [private_legacy, private_migrated])
            )
        )
        baseline = inventory(self.baseline_results, "a" * 40)
        current = inventory(self.results, self.source_commit)
        baseline_raw = canonical_inventory_bytes(baseline)
        current_raw = canonical_inventory_bytes(current)
        delta = reconcile(
            baseline, baseline_raw, current, current_raw, INVENTORY_SCHEMA
        )
        delta_raw = canonical_delta_bytes(delta)
        self.baseline_path.write_bytes(baseline_raw)
        self.current_path.write_bytes(current_raw)
        self.delta_path.write_bytes(delta_raw)

        entries = {entry["result_id"]: entry for entry in delta["entries"]}
        decisions = {
            "schema_version": 1,
            "kind": "historical_final_delta_public_source_decisions",
            "source_repository": "leanprover/lean-eval-submissions",
            "source_commit": self.source_commit,
            "results_store_sha256": current["results_store_sha256"],
            "delta_sha256": __import__("hashlib").sha256(delta_raw).hexdigest(),
            "entries": sorted(
                [
                    {
                        "result_id": public_available["result_id"],
                        "request_id": "prr_" + "1" * 64,
                        "workflow_run_identity_sha256": "2" * 64,
                        "source_kind": "github_repo",
                        "source_repository": public_available["submission"]["repo"],
                        "source_commit": public_available["submission"]["ref"],
                        "source_tree": "5" * 40,
                        "classification": "available",
                    },
                    {
                        "result_id": public_unavailable["result_id"],
                        "request_id": "prr_" + "3" * 64,
                        "workflow_run_identity_sha256": "4" * 64,
                        "source_kind": "github_repo",
                        "source_repository": public_unavailable["submission"]["repo"],
                        "source_commit": public_unavailable["submission"]["ref"],
                        "classification": "source_ref_permanently_unavailable",
                        "review_status": "reviewed",
                        "candidate_entry_sha256": "6" * 64,
                        "disposition_path": "evidence/public-replay/unavailability-dispositions-v1/"
                        + "7" * 64
                        + ".json",
                        "disposition_sha256": "7" * 64,
                        "reason_code": "source_ref_permanently_unavailable",
                        "rationale_code": "accepted_immutable_source_ref_unavailable_without_archive",
                    },
                ],
                key=lambda item: item["result_id"],
            ),
        }
        assert set(entries) >= set(self.ids.values())
        self.decisions_path.write_bytes(canonical(decisions))

        crosswalk_entries = sorted(
            [
                {
                    "result_id": private_legacy["result_id"],
                    "classification": "bound",
                    "submission_id": "018f1f5e-7b2a-7abc-8def-0123456789ab",
                    "archive_plan_entry_sha256": "7" * 64,
                    "archive_schema_version": 1,
                    "archive_result_evidence": "legacy_unrecorded",
                    "benchmark_relation": "same",
                },
                {
                    "result_id": private_migrated["result_id"],
                    "classification": "bound",
                    "submission_id": "018f1f5e-7b2a-7abc-8def-0123456789ac",
                    "archive_plan_entry_sha256": "8" * 64,
                    "archive_schema_version": 3,
                    "archive_result_evidence": "confirmed_pass",
                    "benchmark_relation": "same",
                },
            ],
            key=lambda item: item["result_id"],
        )
        crosswalk = {
            "schema_version": 1,
            "results_repository": "leanprover/lean-eval-submissions",
            "results_commit": self.source_commit,
            "results_store_sha256": current["results_store_sha256"],
            "private_result_count": 2,
            "audit_repository": "leanprover/lean-eval-audit",
            "audit_commit": self.audit_commit,
            "archive_inventory_digest": "9" * 64,
            "archive_count": 2,
            "classification_counts": {
                "bound": 2,
                "archive_not_found": 0,
                "archive_identity_ambiguous": 0,
                "archive_metadata_conflict": 0,
            },
            "entries": crosswalk_entries,
        }
        self.crosswalk_path.write_bytes(canonical(crosswalk))

    def build(self) -> dict:
        return build_packet(
            baseline_path=self.baseline_path,
            current_path=self.current_path,
            delta_path=self.delta_path,
            inventory_schema_path=ROOT
            / "schemas"
            / "historical-replay-inventory-v1.schema.json",
            results_root=self.results,
            public_decisions_path=self.decisions_path,
            private_crosswalk_path=self.crosswalk_path,
            private_crosswalk_schema_path=ROOT
            / "schemas"
            / "historical-private-archive-crosswalk-v1.schema.json",
            authority_commit=self.source_commit,
            verify_git=False,
        )


class HistoricalFinalDeltaPacketTests(unittest.TestCase):
    def test_public_decision_producer_closes_exact_reviewed_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            reviewed = json.loads(fixture.decisions_path.read_text())
            reviewed["kind"] = "historical_final_delta_public_authority"
            reviewed_path = fixture.root / "reviewed-public-authority.json"
            reviewed_path.write_bytes(canonical(reviewed))
            produced = build_decisions(fixture.delta_path, reviewed_path)
            self.assertEqual(
                produced["kind"], "historical_final_delta_public_source_decisions"
            )
            self.assertEqual(produced["entries"], reviewed["entries"])

            reviewed["entries"] = reviewed["entries"][:-1]
            reviewed_path.write_bytes(canonical(reviewed))
            with self.assertRaisesRegex(PublicDecisionError, "exactly cover"):
                build_decisions(fixture.delta_path, reviewed_path)

    def test_packet_closes_delta_and_reports_only_required_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            packet = fixture.build()
        self.assertEqual(
            packet["classification_counts"],
            {
                "public": {"replayable": 1, "unavailable": 1, "total": 2},
                "private": {"replayable": 2, "unavailable": 0, "total": 2},
            },
        )
        self.assertEqual(packet["archive_migration"]["legacy_unique_archive_count"], 1)
        self.assertEqual(
            packet["archive_migration"]["migrated_unique_archive_count"], 1
        )
        self.assertEqual(
            packet["image_requirements"],
            [
                {
                    "source_visibility": "private",
                    "benchmark_repository": "leanprover/lean-eval",
                    "benchmark_commit": fixture.private_benchmark,
                    "result_count": 2,
                },
                {
                    "source_visibility": "public",
                    "benchmark_repository": "leanprover/lean-eval",
                    "benchmark_commit": fixture.public_benchmark,
                    "result_count": 1,
                },
            ],
        )
        self.assertEqual(packet["image_requirement_count"], 2)
        self.assertEqual(
            packet["activation_status"],
            "blocked_pending_exact_profiles_and_state_append",
        )
        for entry in packet["entries"]:
            expected = dict(entry)
            recorded = expected.pop("packet_entry_sha256")
            self.assertEqual(recorded, entry_sha256(expected))
            if entry["source_visibility"] == "private":
                self.assertNotIn("source", entry)
        validator = Draft202012Validator(PACKET_SCHEMA, format_checker=FormatChecker())
        errors = sorted(
            validator.iter_errors(packet), key=lambda error: list(error.path)
        )
        self.assertEqual(errors, [])

    def test_changed_delta_fails_exact_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            changed = json.loads(fixture.delta_path.read_bytes())
            changed["entries"][0]["problem_id"] = "changed"
            fixture.delta_path.write_bytes(canonical(changed))
            with self.assertRaisesRegex(
                FinalDeltaError, "exact append-only reconciliation"
            ):
                fixture.build()

    def test_public_decisions_must_be_complete_and_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            decisions = json.loads(fixture.decisions_path.read_bytes())
            decisions["entries"] = decisions["entries"][:-1]
            fixture.decisions_path.write_bytes(canonical(decisions))
            with self.assertRaisesRegex(FinalDeltaError, "exactly cover"):
                fixture.build()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            decisions = json.loads(fixture.decisions_path.read_bytes())
            unavailable = next(
                item
                for item in decisions["entries"]
                if item["classification"] != "available"
            )
            unavailable["review_status"] = "pending"
            fixture.decisions_path.write_bytes(canonical(decisions))
            with self.assertRaisesRegex(FinalDeltaError, "not reviewed"):
                fixture.build()

    def test_unresolved_private_crosswalk_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            crosswalk = json.loads(fixture.crosswalk_path.read_bytes())
            entry = crosswalk["entries"][0]
            unresolved_result_id = entry["result_id"]
            entry.clear()
            entry.update(
                {
                    "result_id": unresolved_result_id,
                    "classification": "archive_identity_ambiguous",
                    "candidate_count": 2,
                }
            )
            crosswalk["entries"].sort(key=lambda item: item["result_id"])
            crosswalk["classification_counts"] = {
                "bound": 1,
                "archive_not_found": 0,
                "archive_identity_ambiguous": 1,
                "archive_metadata_conflict": 0,
            }
            fixture.crosswalk_path.write_bytes(canonical(crosswalk))
            with self.assertRaisesRegex(
                FinalDeltaError, "unresolved archive classification"
            ):
                fixture.build()

    def test_crosswalk_must_cover_all_current_private_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            crosswalk = json.loads(fixture.crosswalk_path.read_bytes())
            crosswalk["entries"] = crosswalk["entries"][:-1]
            crosswalk["private_result_count"] = 1
            crosswalk["classification_counts"]["bound"] = 1
            fixture.crosswalk_path.write_bytes(canonical(crosswalk))
            with self.assertRaisesRegex(
                FinalDeltaError, "identity is invalid|exactly cover"
            ):
                fixture.build()

    def test_output_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "packet.json"
            write_exclusive(path, {"schema_version": 1})
            self.assertEqual(path.read_bytes(), canonical({"schema_version": 1}))
            with self.assertRaisesRegex(FinalDeltaError, "overwrite"):
                write_exclusive(path, {"schema_version": 1})


if __name__ == "__main__":
    unittest.main()
