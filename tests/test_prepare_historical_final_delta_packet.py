from __future__ import annotations

import json
import os
import subprocess
import textwrap
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
        public_unavailable = record(
            "public", "c", public=True, benchmark="4" * 40
        )
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
            canonical_file_bytes(document("public", [public_available, public_unavailable]))
        )
        (self.results / "private.json").write_bytes(
            canonical_file_bytes(document("private", [private_legacy, private_migrated]))
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
                        "source_repository": public_available["submission"]["repo"],
                        "source_commit": public_available["submission"]["ref"],
                        "source_tree": "5" * 40,
                        "classification": "available",
                    },
                    {
                        "result_id": public_unavailable["result_id"],
                        "source_repository": public_unavailable["submission"]["repo"],
                        "source_commit": public_unavailable["submission"]["ref"],
                        "classification": "source_ref_permanently_unavailable",
                        "review_status": "reviewed",
                        "evidence_sha256": "6" * 64,
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

    def add_modern_server_results(self) -> set[str]:
        records = [
            record("modern", "f", public=True, benchmark="5" * 40),
            record("modern", "0", public=False, benchmark="6" * 40),
        ]
        for number, item in enumerate(records):
            item["intake"] = {"kind": "server", "submission_id": f"018f1f5e-7b2a-7abc-8def-0123456789a{number}"}
        (self.results / "modern.json").write_bytes(canonical_file_bytes(document("modern", records)))
        baseline = json.loads(self.baseline_path.read_bytes())
        current = inventory(self.results, self.source_commit)
        current_raw = canonical_inventory_bytes(current)
        delta = reconcile(baseline, self.baseline_path.read_bytes(), current, current_raw, INVENTORY_SCHEMA)
        delta_raw = canonical_delta_bytes(delta)
        self.current_path.write_bytes(current_raw)
        self.delta_path.write_bytes(delta_raw)
        decisions = json.loads(self.decisions_path.read_bytes())
        decisions["results_store_sha256"] = current["results_store_sha256"]
        decisions["delta_sha256"] = __import__("hashlib").sha256(delta_raw).hexdigest()
        self.decisions_path.write_bytes(canonical(decisions))
        crosswalk = json.loads(self.crosswalk_path.read_bytes())
        crosswalk["results_store_sha256"] = current["results_store_sha256"]
        crosswalk["private_result_count"] += 1
        crosswalk["classification_counts"]["archive_identity_ambiguous"] += 1
        crosswalk["entries"].append({"result_id": records[1]["result_id"], "classification": "archive_identity_ambiguous", "candidate_count": 2})
        crosswalk["entries"].sort(key=lambda item: item["result_id"])
        self.crosswalk_path.write_bytes(canonical(crosswalk))
        return {item["result_id"] for item in records}

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
            verify_git=False,
        )


class HistoricalFinalDeltaPacketTests(unittest.TestCase):
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
        self.assertEqual(packet["archive_migration"]["migrated_unique_archive_count"], 1)
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
        self.assertEqual(packet["activation_status"], "blocked_pending_exact_profiles_and_state_append")
        for entry in packet["entries"]:
            expected = dict(entry)
            recorded = expected.pop("packet_entry_sha256")
            self.assertEqual(recorded, entry_sha256(expected))
            if entry["source_visibility"] == "private":
                self.assertNotIn("source", entry)
        validator = Draft202012Validator(
            PACKET_SCHEMA, format_checker=FormatChecker()
        )
        errors = sorted(validator.iter_errors(packet), key=lambda error: list(error.path))
        self.assertEqual(errors, [])

    def test_modern_server_results_are_explicitly_excluded_and_counts_conserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            servers = fixture.add_modern_server_results()
            packet = fixture.build()
        scope = packet["intake_scope"]
        self.assertEqual(scope["legacy_issue_result_count"], 4)
        self.assertEqual(scope["excluded_server_result_count"], 2)
        self.assertEqual(scope["excluded_server_counts"], {"public": 1, "private": 1})
        self.assertEqual({item["result_id"] for item in scope["excluded_server_results"]}, servers)
        self.assertTrue(servers.isdisjoint(item["result_id"] for item in packet["entries"]))
        self.assertEqual(packet["cutoff"]["delta_counts"]["result_count"], 6)
        for visibility, key in (("public", "public_source_probe_pending"), ("private", "private_archive_migration_pending")):
            self.assertEqual(packet["classification_counts"][visibility]["total"] + scope["excluded_server_counts"][visibility], packet["cutoff"]["delta_counts"][key])
        self.assertEqual(packet["archive_migration"]["legacy_unique_archive_count"], 1)
        self.assertEqual(packet["archive_migration"]["migrated_unique_archive_count"], 1)
        self.assertEqual(packet["image_requirement_count"], 2)
        self.assertTrue(all(row["benchmark_commit"] not in {"5" * 40, "6" * 40} for row in packet["image_requirements"]))
        self.assertEqual(list(Draft202012Validator(PACKET_SCHEMA).iter_errors(packet)), [])
        self.assertEqual(packet["activation_status"], "blocked_pending_exact_profiles_and_state_append")

    def test_intake_kind_change_cannot_bypass_exact_results_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            fixture.add_modern_server_results()
            path = fixture.results / "modern.json"
            value = json.loads(path.read_bytes())
            value["results"][0]["intake"] = {"kind": "issue", "issue_number": 123}
            path.write_bytes(canonical_file_bytes(value))
            with self.assertRaisesRegex(FinalDeltaError, "digest differs"):
                fixture.build()

    def test_all_server_delta_has_no_historical_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            (fixture.results / "public.json").unlink()
            (fixture.results / "private.json").unlink()
            decisions = json.loads(fixture.decisions_path.read_bytes())
            decisions["entries"] = []
            fixture.decisions_path.write_bytes(canonical(decisions))
            crosswalk = json.loads(fixture.crosswalk_path.read_bytes())
            crosswalk["entries"] = []
            crosswalk["private_result_count"] = 0
            crosswalk["classification_counts"]["bound"] = 0
            fixture.crosswalk_path.write_bytes(canonical(crosswalk))
            fixture.add_modern_server_results()
            packet = fixture.build()
        self.assertEqual(packet["cutoff"]["delta_counts"]["result_count"], 2)
        self.assertEqual(packet["intake_scope"]["legacy_issue_result_count"], 0)
        self.assertEqual(packet["intake_scope"]["excluded_server_result_count"], 2)
        self.assertEqual(packet["entries"], [])
        self.assertEqual(packet["image_requirements"], [])
        self.assertEqual(packet["archive_migration"]["legacy_unique_archive_count"], 0)
        self.assertEqual(list(Draft202012Validator(PACKET_SCHEMA).iter_errors(packet)), [])

    def test_actual_workflow_verifier_rejects_partition_loss_overlap_and_bad_counts(self):
        workflow = (ROOT / ".github/workflows/historical-final-delta-packet.yml").read_text()
        start = workflow.index('          packet_path = pathlib.Path(os.environ["PACKET"])')
        end = workflow.index("\n          PY", start)
        script = "import json, os, pathlib\nfrom jsonschema import Draft202012Validator, FormatChecker\n" + textwrap.dedent(workflow[start:end])
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            fixture.add_modern_server_results()
            packet = fixture.build()
            path = fixture.root / "packet.json"
            environment = {**os.environ, "PACKET": str(path), "PACKET_SCHEMA": str(ROOT / "schemas/historical-final-delta-preparation-v1.schema.json"), "EXPECTED_COUNT": "6", "EXPECTED_PUBLIC": "3", "EXPECTED_PRIVATE": "3"}
            path.write_bytes(canonical(packet))
            good = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            for mutation in ("omit_server", "overlap", "count", "visibility"):
                with self.subTest(mutation=mutation):
                    changed = json.loads(json.dumps(packet))
                    scope = changed["intake_scope"]
                    if mutation == "omit_server":
                        scope["excluded_server_results"].pop()
                    elif mutation == "overlap":
                        scope["excluded_server_results"][0]["result_id"] = changed["entries"][0]["result_id"]
                    elif mutation == "count":
                        scope["excluded_server_result_count"] += 1
                    else:
                        scope["excluded_server_counts"]["public"] += 1
                    path.write_bytes(canonical(changed))
                    bad = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True)
                    self.assertNotEqual(bad.returncode, 0)

    def test_inventory_cannot_forge_an_excluded_servers_visibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            servers = fixture.add_modern_server_results()
            current = json.loads(fixture.current_path.read_bytes())
            target = next(item for item in current["entries"] if item["result_id"] in servers and item["source"]["visibility"] == "public")
            target["source"] = {"kind": "github_repo", "visibility": "private", "readiness": "private_archive_migration_pending"}
            current["classification_counts"]["public_source_probe_pending"] -= 1
            current["classification_counts"]["private_archive_migration_pending"] += 1
            raw = canonical_inventory_bytes(current)
            baseline = json.loads(fixture.baseline_path.read_bytes())
            delta = reconcile(baseline, fixture.baseline_path.read_bytes(), current, raw, INVENTORY_SCHEMA)
            fixture.current_path.write_bytes(raw)
            fixture.delta_path.write_bytes(canonical_delta_bytes(delta))
            with self.assertRaisesRegex(FinalDeltaError, "differs from exact canonical Results"):
                fixture.build()

    def test_changed_delta_fails_exact_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            changed = json.loads(fixture.delta_path.read_bytes())
            changed["entries"][0]["problem_id"] = "changed"
            fixture.delta_path.write_bytes(canonical(changed))
            with self.assertRaisesRegex(FinalDeltaError, "exact append-only reconciliation"):
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
            with self.assertRaisesRegex(FinalDeltaError, "unresolved archive classification"):
                fixture.build()

    def test_crosswalk_must_cover_all_current_private_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            crosswalk = json.loads(fixture.crosswalk_path.read_bytes())
            crosswalk["entries"] = crosswalk["entries"][:-1]
            crosswalk["private_result_count"] = 1
            crosswalk["classification_counts"]["bound"] = 1
            fixture.crosswalk_path.write_bytes(canonical(crosswalk))
            with self.assertRaisesRegex(FinalDeltaError, "identity is invalid|exactly cover"):
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
