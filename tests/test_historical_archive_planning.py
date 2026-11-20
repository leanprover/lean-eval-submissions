from __future__ import annotations

import copy
import pathlib
import unittest
from unittest import mock

import test_historical_replay_controller as public
import test_historical_private_replay_controller as private
from test_historical_archive_evidence import evidence
from scripts import historical_private_replay_controller as controller
from scripts.historical_archive_recovery import expected_recovery_entry, recovery_entry_sha256, task_id_set_sha256
from scripts.historical_replay_controller import canonical_bytes, sha256_bytes, state_canonical_bytes
from scripts.replay_orchestrator import config_digest


def planning_fixture():
    original = public.Fixture()
    legacy = private.Fixture()
    task, _, _ = evidence()
    authority = copy.deepcopy(original.authority)
    # The original identity is independent of the replacement image. Keep the
    # real legacy authority/profile schemas in this wholly synthetic binding.
    identity = (
        "result_id", "request_id", "historical_accepted_at", "owner_login", "declared_model",
        "problem_id", "statement_revision", "results_repository", "results_commit",
        "results_path", "result_file_sha256", "result_tree_digest", "source_kind",
        "source_repository", "source_commit", "source_visibility", "benchmark_repository",
        "benchmark_commit", "toolchain", "lean_toolchain_blob_sha256",
        "workflow_run_identity_sha256", "authority_path", "authority_sha256",
        "replay_task_id", "measurement_config_digest",
    )
    task.update({key: original.task[key] for key in identity})
    profile = copy.deepcopy(legacy.profile)
    profile.update(schema_version=2, kind="historical_archive_replay_profile_qualification", image_family="lean-eval-authoritative-archive-replay-v2")
    for key in ("benchmark_commit", "toolchain", "lean_toolchain_blob_sha256"):
        profile[key] = task[key]
    profile["execution_profile"].update(schema_version=2, archive_capacity_class="legacy_10mib", toolchain=task["toolchain"])
    profile["qualification"]["offline_image_inspection"]["archive_expectation_schema_version"] = 3
    for key, path in {
        "dockerfile": "Dockerfile.historical-archive-replay-v2",
        "dockerignore": "Dockerfile.historical-archive-replay-v2.dockerignore",
        "profile_matrix": "configuration/historical-archive-replay-image-matrix-v2.json",
    }.items():
        profile["source_blobs"][key]["path"] = path
    digest = config_digest("lean-eval-replay-execution-profile-v1", profile["execution_profile"])
    profile["execution_profile_digest"] = digest
    task.update(execution_profile_digest=digest, qualification_path="evidence/archive-replay/profiles/" + digest + ".json", qualification_sha256=sha256_bytes(canonical_bytes(profile)), measurement_config_digest=profile["measurement_config_digest"])
    task["replay_task_id"] = controller.replay_task_id(task["result_id"], task["measurement_config_digest"])
    entry = expected_recovery_entry(task)
    manifest = {"schema_version": 2, "kind": "historical_public_archive_recovery", "task_count": 1, "task_id_set_sha256": task_id_set_sha256([task["replay_task_id"]]), "entries": [entry]}
    task.update(reconfiguration_entry_sha256=recovery_entry_sha256(entry), reconfiguration_sha256=sha256_bytes(canonical_bytes(manifest)))
    task["reconfiguration_path"] = "evidence/historical-replay/source-recoveries/" + task["reconfiguration_sha256"] + ".json"
    queue = copy.deepcopy(legacy.queue)
    queue.update(schema_version=2, tasks=[task])
    binding = {key: task["archive_" + key] for key in ("repository", "commit", "path", "sidecar_path", "ciphertext_sha256", "sidecar_sha256", "key_envelope_sha256", "plaintext_tar_sha256", "plaintext_tar_size")}
    binding["archive_path"] = binding.pop("path")
    legacy.close()
    return queue, authority, profile, binding, manifest


class HistoricalArchivePlanningTests(unittest.TestCase):
    def plan(self, *, omit_manifest=False, change_authority=False):
        queue, authority, profile, binding, manifest = planning_fixture()
        if change_authority:
            queue["tasks"][0]["source_commit"] = "e" * 40
        return controller._plan_next(
            queue, state_canonical_bytes(queue), "1" * 40,
            authority, canonical_bytes(authority), profile, canonical_bytes(profile), binding,
            recovery_manifest_value=None if omit_manifest else manifest,
            recovery_manifest_raw=None if omit_manifest else canonical_bytes(manifest),
        )

    def test_original_public_authority_plans_archive_without_private_authority(self):
        plan = self.plan()
        request = plan["execution_plan"]["request"]
        self.assertEqual((2, "public", "archive"), (request["schema_version"], request["source"]["visibility"], request["source"]["fetch_method"]))
        self.assertEqual(1, request["attempt"])
        self.assertEqual(plan, controller.validate_execution_plan(plan))
        _, _, profile, _, _ = planning_fixture()
        with (mock.patch.object(controller, "load_reviewed_inputs", return_value=(None, None, profile, None)), mock.patch.object(controller, "_verify_ancestor_of_upstream")):
            rendered = controller.render_executor_config(plan, pathlib.Path("/synthetic"), "a" * 32, "2" * 40)
        self.assertEqual("legacy_10mib", rendered["vars"]["REVIEWED_ARCHIVE_CAPACITY_CLASS"])

    def test_manifest_and_original_public_identity_remain_required(self):
        with self.assertRaisesRegex(controller.HistoricalPrivateReplayControllerError, "manifest"):
            self.plan(omit_manifest=True)
        with self.assertRaisesRegex(controller.HistoricalReplayControllerError, "source_commit"):
            self.plan(change_authority=True)
