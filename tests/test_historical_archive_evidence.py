from __future__ import annotations

import copy
import json
import pathlib
import unittest

from scripts.historical_replay_controller import canonical_bytes, sha256_bytes
from scripts.historical_archive_recovery import (
    HistoricalReplayControllerError,
    expected_recovery_entry, recovery_entry_sha256, task_id_set_sha256,
    expected_crosswalk_entry, crosswalk_entry_sha256,
    validate_recovery_manifest, validate_archive_crosswalk,
)
from scripts.classify_historical_private_archives import classify_archive_for_visibility, _classify_result

ROOT = pathlib.Path(__file__).parent / "fixtures"


def evidence():
    task = copy.deepcopy(json.loads((ROOT / "historical-public-archive-recovery-queue-v2.json").read_text())["tasks"][0])
    entry = expected_crosswalk_entry(task, 1)
    task["crosswalk_entry_sha256"] = crosswalk_entry_sha256(entry)
    crosswalk = {
        "schema_version": 2,
        "kind": "historical_archive_crosswalk",
        "results_repository": task["results_repository"],
        "results_commit": task["results_commit"],
        "results_store_sha256": "1" * 64,
        "audit_repository": task["archive_repository"],
        "audit_commit": task["archive_commit"],
        "migration_plan": {
            "repository": "leanprover/lean-eval-submissions", "commit": "2" * 40,
            "path": "evidence/historical-replay/archive-migration-plans/" + "3" * 64 + ".json",
            "sha256": "3" * 64, "inventory_digest": "4" * 64,
        },
        "result_count": 1, "archive_count": 1, "entries": [entry],
    }
    raw = canonical_bytes(crosswalk)
    task["crosswalk_sha256"] = sha256_bytes(raw)
    task["crosswalk_path"] = "evidence/historical-replay/archive-crosswalks/" + task["crosswalk_sha256"] + ".json"
    entry = expected_recovery_entry(task)
    manifest = {
        "schema_version": 2, "kind": "historical_public_archive_recovery",
        "task_count": 1, "task_id_set_sha256": task_id_set_sha256([task["replay_task_id"]]),
        "entries": [entry],
    }
    task["reconfiguration_entry_sha256"] = recovery_entry_sha256(entry)
    raw_manifest = canonical_bytes(manifest)
    task["reconfiguration_sha256"] = sha256_bytes(raw_manifest)
    task["reconfiguration_path"] = "evidence/historical-replay/source-recoveries/" + task["reconfiguration_sha256"] + ".json"
    return task, crosswalk, manifest


class ArchiveEvidenceTests(unittest.TestCase):
    def test_exact_artifact_entries_reproduce_state_bindings(self):
        task, crosswalk, manifest = evidence()
        self.assertEqual(crosswalk["entries"][0], validate_archive_crosswalk(crosswalk, canonical_bytes(crosswalk), task))
        self.assertEqual(manifest["entries"][0], validate_recovery_manifest(manifest, canonical_bytes(manifest), task))
        self.assertEqual("public", manifest["entries"][0]["source_visibility"])
        self.assertNotIn("reconfiguration_sha256", manifest["entries"][0])
        self.assertNotIn("replacement_qualification_event_id", manifest["entries"][0])

    def test_manifest_cannot_rebind_original_authority_or_candidate(self):
        for field, value in (
            ("original_authority_event_id", "0198abcd-0000-7000-8000-000000000099"),
            ("candidate_qualification_sha256", "f" * 64),
            ("archive_ciphertext_sha256", "f" * 64),
            ("source_visibility", "private"),
            ("archive_capacity_class", "archive_single_blob_30m"),
        ):
            task, _, manifest = evidence()
            manifest["entries"][0][field] = value
            raw = canonical_bytes(manifest)
            task["reconfiguration_sha256"] = sha256_bytes(raw)
            with self.subTest(field=field), self.assertRaises(HistoricalReplayControllerError):
                validate_recovery_manifest(manifest, raw, task)

    def test_crosswalk_cannot_lose_hash_evidence_visibility_or_selection(self):
        for field, value in (
            ("source_visibility", "private"), ("archive_sidecar_sha256", "f" * 64),
            ("archive_key_envelope_sha256", "f" * 64), ("archive_ciphertext_size", 9999),
            ("benchmark_relation", "archive_recorded_different"),
        ):
            task, crosswalk, _ = evidence()
            crosswalk["entries"][0][field] = value
            raw = canonical_bytes(crosswalk)
            task["crosswalk_sha256"] = sha256_bytes(raw)
            with self.subTest(field=field), self.assertRaises(HistoricalReplayControllerError):
                validate_archive_crosswalk(crosswalk, raw, task)
        task, crosswalk, _ = evidence()
        crosswalk["archive_count"] = 2
        raw = canonical_bytes(crosswalk)
        task["crosswalk_sha256"] = sha256_bytes(raw)
        with self.assertRaises(HistoricalReplayControllerError):
            validate_archive_crosswalk(crosswalk, raw, task)

    def test_public_archive_classification_preserves_flag(self):
        record = {"result_id": "r2_" + "1" * 64, "declared_model": "model", "problem_id": "p", "benchmark_commit": "a" * 40}
        archive = {"submission_public": True, "model": "model", "problem_ids": ["p"], "evaluator_verdict": {"p": "pass"}, "submission_id": "0198abcd-0000-7000-8000-000000000001", "archive_plan_entry_sha256": "2" * 64, "schema_version": 1, "benchmark_commit": "a" * 40}
        classified = classify_archive_for_visibility("owner", record, [archive], source_public=True)
        self.assertEqual("bound", classified["classification"])
        self.assertEqual("confirmed_pass", classified["archive_result_evidence"])
        self.assertEqual("archive_metadata_conflict", _classify_result("owner", record, [archive])["classification"])
        self.assertIs(True, archive["submission_public"])
