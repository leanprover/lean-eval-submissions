from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import unittest
from unittest import mock

import test_replay_controller as legacy
from scripts import historical_private_replay_controller as controller
from scripts.historical_archive_recovery import expected_crosswalk_entry, crosswalk_entry_sha256
from scripts.historical_replay_controller import canonical_bytes, sha256_bytes
from scripts.key_capability_contract import archive_file_key_id
from scripts.results_schema import canonical_file_bytes, canonical_store_digest
from scripts.build_result_receipt import result_tree_digest
import migrate_archive_envelopes as migration

ROOT = pathlib.Path(__file__).parent / "fixtures"
REPOSITORY = pathlib.Path("/synthetic/submissions")
AUDIT = pathlib.Path("/synthetic/audit")


def fixture(*, original_public=True, issue=777, modern=False, altered_target=False):
    task = copy.deepcopy(json.loads((ROOT / "historical-public-archive-recovery-queue-v2.json").read_text())["tasks"][0])
    original = legacy.sidecar()
    original.update(schema_version=1, issue=issue, submission_repo=task["source_repository"], submission_ref=task["source_commit"], submission_kind=task["source_kind"], submission_public=original_public, submitter=task["owner_login"], model=task["declared_model"], benchmark_commit=task["benchmark_commit"], problem_ids=[task["problem_id"]], evaluator_verdict={task["problem_id"]: "pass"})
    del original["submission_id"]
    del original["key_envelope"]
    source_path = "audit/2026/08/synthetic.tar.age"
    archive_id = migration.historical_submission_id(source_path, sha256_bytes(legacy.CIPHERTEXT), original["archived_at"])
    task.update(archive_submission_id=archive_id, archive_path=migration.canonical_archive_path(archive_id), archive_sidecar_path=migration.canonical_archive_path(archive_id).removesuffix(".tar.age") + ".json")
    original_raw = canonical_bytes(original)
    planned = {"source_path": source_path, "source_schema_version": 1, "source_ciphertext_sha256": sha256_bytes(legacy.CIPHERTEXT), "source_sidecar_sha256": sha256_bytes(original_raw), "plaintext_sha256": sha256_bytes(legacy.PLAINTEXT), "plaintext_size_bytes": len(legacy.PLAINTEXT), "submission_id": archive_id, "target_path": task["archive_path"]}
    core = {"source_repository": "leanprover/lean-eval-audit", "source_commit": "6" * 40, "entries": [planned], "retained": []}
    plan = {"schema_version": 1, **core, "migration_count": 1, "retained_count": 0, "inventory_digest": hashlib.sha256(b"lean-eval-archive-envelope-migration-v1\0" + migration._canonical_bytes(core)).hexdigest()}
    plan_raw = canonical_bytes(plan)
    plan_sha = sha256_bytes(plan_raw)
    envelope = legacy.file_key_sidecar()["key_envelope"]
    envelope.update(submission_id=archive_id, data_key_id=archive_file_key_id(archive_id, sha256_bytes(legacy.CIPHERTEXT)))
    target = {key: original[key] for key in migration.PRESERVED_FIELDS if key in original}
    target.update(schema_version=3, submission_id=archive_id, sha256_ciphertext=sha256_bytes(legacy.CIPHERTEXT), size_bytes_ciphertext=len(legacy.CIPHERTEXT), key_envelope=envelope)
    if altered_target:
        target["model"] = "changed target"
    target_raw = canonical_bytes(target)
    task.update(archive_plan_entry_sha256=sha256_bytes(migration.PLAN_ENTRY_DIGEST_DOMAIN + migration._canonical_bytes(planned)), archive_ciphertext_sha256=sha256_bytes(legacy.CIPHERTEXT), archive_ciphertext_size=len(legacy.CIPHERTEXT), archive_plaintext_tar_sha256=sha256_bytes(legacy.PLAINTEXT), archive_plaintext_tar_size=len(legacy.PLAINTEXT), archive_sidecar_sha256=sha256_bytes(target_raw), archive_key_envelope_sha256=sha256_bytes(migration._canonical_bytes(envelope)))
    record = {"result_id": task["result_id"], "problem_id": task["problem_id"], "statement_revision": task["statement_revision"], "declared_model": task["declared_model"], "accepted_at": task["historical_accepted_at"], "benchmark_commit": task["benchmark_commit"], "intake": {"kind": "server", "submission_id": archive_id} if modern else {"kind": "issue", "issue_number": 777}, "submission": {"kind": task["source_kind"], "repo": task["source_repository"], "ref": task["source_commit"], "public": True}, "production_metadata": {}}
    results = {"schema_version": 2, "user": task["owner_login"], "results": [record]}
    results_raw = canonical_file_bytes(results)
    task.update(result_file_sha256=sha256_bytes(results_raw), result_tree_digest=result_tree_digest(task["results_path"], results_raw))
    entry = expected_crosswalk_entry(task, 1)
    task["crosswalk_entry_sha256"] = crosswalk_entry_sha256(entry)
    plan_path = "evidence/historical-replay/archive-migration-plans/" + plan_sha + ".json"
    crosswalk = {"schema_version": 2, "kind": "historical_archive_crosswalk", "results_repository": task["results_repository"], "results_commit": task["results_commit"], "results_store_sha256": canonical_store_digest([(task["results_path"], results)]), "audit_repository": task["archive_repository"], "audit_commit": task["archive_commit"], "migration_plan": {"repository": "leanprover/lean-eval-submissions", "commit": "2" * 40, "path": plan_path, "sha256": plan_sha, "inventory_digest": plan["inventory_digest"]}, "result_count": 1, "archive_count": 1, "entries": [entry]}
    crosswalk_raw = canonical_bytes(crosswalk)
    task["crosswalk_sha256"] = sha256_bytes(crosswalk_raw)
    task["crosswalk_path"] = "evidence/historical-replay/archive-crosswalks/" + task["crosswalk_sha256"] + ".json"
    blobs = {
        (REPOSITORY, task["crosswalk_commit"], task["crosswalk_path"]): crosswalk_raw,
        (REPOSITORY, "2" * 40, plan_path): plan_raw,
        (REPOSITORY, task["results_commit"], task["results_path"]): results_raw,
        (AUDIT, "6" * 40, source_path): legacy.CIPHERTEXT,
        (AUDIT, "6" * 40, source_path.removesuffix(".tar.age") + ".json"): original_raw,
        (AUDIT, task["archive_commit"], task["archive_path"]): legacy.CIPHERTEXT,
        (AUDIT, task["archive_commit"], task["archive_sidecar_path"]): target_raw,
    }
    return task, blobs


class ArchiveGitEvidenceTests(unittest.TestCase):
    def run_loader(self, task, blobs):
        def git_blob(root, commit, path):
            return blobs[(root, commit, path)]
        def git_text(root, *arguments):
            if arguments[:2] == ("ls-tree", "-r"):
                return task["results_path"]
            self.assertEqual(("cat-file", "-s"), arguments[:2])
            commit, path = arguments[2].split(":", 1)
            return str(len(git_blob(root, commit, path)))
        with (
            mock.patch.object(controller, "_verify_checkout"),
            mock.patch.object(controller, "_verify_ancestor_of_upstream"),
            mock.patch.object(controller, "_verify_ancestor"),
            mock.patch.object(controller, "_git_blob", side_effect=git_blob),
            mock.patch.object(controller, "_git_text", side_effect=git_text),
        ):
            return controller._load_recovered_crosswalk_entry(REPOSITORY, AUDIT, task)

    def test_original_issue_result_and_migrated_snapshot_are_bound_without_decryption(self):
        task, blobs = fixture()
        entry = self.run_loader(task, blobs)
        self.assertEqual(task["result_id"], entry["result_id"])
        self.assertEqual("public", entry["source_visibility"])
        self.assertEqual("confirmed_pass", entry["archive_result_evidence"])

    def test_original_issue_visibility_and_preserved_target_are_not_coerced(self):
        for changes in ({"issue": 778}, {"original_public": False}, {"modern": True}, {"altered_target": True}):
            task, blobs = fixture(**changes)
            with self.subTest(changes=changes), self.assertRaises(controller.HistoricalPrivateReplayControllerError):
                self.run_loader(task, blobs)

    def test_target_ciphertext_tampering_is_rejected(self):
        task, blobs = fixture()
        blobs[(AUDIT, task["archive_commit"], task["archive_path"])] += b"tampered"
        with self.assertRaises(controller.HistoricalPrivateReplayControllerError):
            self.run_loader(task, blobs)
