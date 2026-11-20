from __future__ import annotations

import copy
import datetime as dt
import pathlib
import tempfile
import unittest

import test_replay_authoritative as image_tests

import test_replay_controller as legacy
from scripts.replay_controller import ReplayControllerError, prepare_unwrap, build_executor_request
from scripts.replay_orchestrator import (
    ReplayError,
    config_digest,
    validate_execution_profile,
    validate_execution_request,
)


def archived_plan():
    _, _, _, plan = legacy.inputs()
    plan = copy.deepcopy(plan)
    request = plan["request"]
    request["schema_version"] = 2
    request["source"].update(visibility="public", fetch_method="archive")
    request["execution_profile"].update(schema_version=2, archive_capacity_class="legacy_10mib")
    request["execution_profile_digest"] = config_digest(
        "lean-eval-replay-execution-profile-v1", request["execution_profile"]
    )
    return plan


class ArchivedExecutionContractTests(unittest.TestCase):
    def test_version_two_preserves_public_visibility_without_git_source(self):
        request = archived_plan()["request"]
        self.assertEqual(request, validate_execution_request(request))
        self.assertEqual("public", request["source"]["visibility"])
        self.assertEqual("archive", request["source"]["fetch_method"])
        self.assertNotIn("repository", request["source"])
        private = copy.deepcopy(request)
        private["source"]["visibility"] = "private"
        validate_execution_request(private)

    def test_file_key_expectation_preserves_visibility_without_changing_envelope(self):
        plan = archived_plan()
        sidecar = legacy.file_key_sidecar()
        sidecar["submission_public"] = True
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            ciphertext = root / "archive.age"
            ciphertext.write_bytes(legacy.CIPHERTEXT)
            key = root / "file-key"
            key.write_bytes(b"0123456789abcdef")
            unwrap = prepare_unwrap(plan, sidecar, ciphertext, now)
            request = build_executor_request(plan, sidecar, ciphertext, unwrap, key)
            self.assertEqual(2, unwrap["envelope"]["schema_version"])
            self.assertEqual(3, request["schema_version"])
            expectation = request["archive_expectation"]
            self.assertEqual((3, "public", "legacy_10mib"), (expectation["schema_version"], expectation["source_visibility"], expectation["archive_capacity_class"]))
            image_tests.authoritative.validate_expectation(expectation, plan["request"])
            changed = copy.deepcopy(expectation)
            changed["source_visibility"] = "private"
            with self.assertRaises(image_tests.authoritative.AuthoritativeReplayError):
                image_tests.authoritative.validate_expectation(changed, plan["request"])
            sidecar["submission_public"] = False
            with self.assertRaises(ReplayControllerError):
                prepare_unwrap(plan, sidecar, ciphertext, now)

    def test_legacy_profile_shape_remains_closed(self):
        _, profile, _, plan = legacy.inputs()
        validate_execution_profile(profile)
        validate_execution_request(plan["request"])
        profile["archive_capacity_class"] = "legacy_10mib"
        with self.assertRaises(ReplayError):
            validate_execution_profile(profile)

    def test_version_markers_and_unqualified_capacity_are_rejected(self):
        for marker in (True, "2", 2.0, 3):
            request = archived_plan()["request"]
            request["schema_version"] = marker
            with self.subTest(marker=marker), self.assertRaises(ReplayError):
                validate_execution_request(request)
        for value in ("archive_single_blob_30m", "unknown", None):
            profile = archived_plan()["request"]["execution_profile"]
            profile["archive_capacity_class"] = value
            with self.subTest(capacity=value), self.assertRaises(ReplayError):
                validate_execution_profile(profile)

    def test_request_cannot_fall_back_or_mix_profile_versions(self):
        cases = (
            lambda r: r["source"].__setitem__("fetch_method", "git"),
            lambda r: r["source"].__setitem__("repository", "example/source"),
            lambda r: r["source"].__setitem__("visibility", "unknown"),
            lambda r: r["source"]["archive"].__setitem__("submission_id", "0198abcd-0000-7000-8000-000000000002"),
            lambda r: r["execution_profile"].__setitem__("schema_version", 1),
            lambda r: r.__setitem__("schema_version", 1),
        )
        for mutation in cases:
            request = archived_plan()["request"]
            mutation(request)
            with self.subTest(mutation=mutation), self.assertRaises(ReplayError):
                validate_execution_request(request)
