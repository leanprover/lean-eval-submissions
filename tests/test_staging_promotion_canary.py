"""Contract tests for the source-free staging promotion runner."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest
import urllib.request
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_staging_promotion_canary.py"
TEXT = SCRIPT.read_text(encoding="utf-8")
SPEC = importlib.util.spec_from_file_location("staging_promotion_canary", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load staging promotion canary module")
CANARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CANARY)


class StagingPromotionCanaryTests(unittest.TestCase):
    def test_accepts_only_the_source_free_exact_success_contract(self) -> None:
        commit = "c" * 40
        dispatch_ref = f"lean-eval-dispatch/{commit}"
        submission_id = "0198abcd-1111-7000-8000-000000000001"
        response = {
            "status": "passed",
            "environment": "staging",
            "deployed_commit": commit,
            "dispatch_ref": dispatch_ref,
            "controller_run_id": "32712345678",
            "controller_run_attempt": "1",
            "submission_id": submission_id,
            "github_connectivity": "verified",
            "synthetic_intake": "idempotent",
            "cas_contention": "collision_observed_and_retry_applied",
            "dispatch_state": "succeeded",
            "scheduled_reconciliation": "completed",
            "workflow_dispatch": "accepted_by_github",
        }
        self.assertEqual(
            CANARY.validate_canary(
                200,
                response,
                commit,
                dispatch_ref,
                "32712345678",
                "1",
                submission_id,
            ),
            (submission_id, True),
        )
        with self.assertRaises(CANARY.CanaryFailure):
            CANARY.validate_canary(
                200,
                {**response, "source_repository": "must-not-be-returned"},
                commit,
                dispatch_ref,
                "32712345678",
                "1",
                submission_id,
            )

    def test_rejects_inconsistent_or_non_idempotent_progress(self) -> None:
        commit = "c" * 40
        dispatch_ref = f"lean-eval-dispatch/{commit}"
        response = {
            "status": "awaiting_scheduled_reconciliation",
            "environment": "staging",
            "deployed_commit": commit,
            "dispatch_ref": dispatch_ref,
            "controller_run_id": "32712345678",
            "controller_run_attempt": "1",
            "submission_id": "0198abcd-1111-7000-8000-000000000001",
            "github_connectivity": "verified",
            "synthetic_intake": "created",
            "cas_contention": "collision_observed_and_retry_applied",
            "dispatch_state": "pending",
            "scheduled_reconciliation": "pending",
            "workflow_dispatch": "pending",
        }
        self.assertEqual(
            CANARY.validate_canary(
                202, response, commit, dispatch_ref, "32712345678", "1", None
            ),
            (response["submission_id"], False),
        )
        with self.assertRaises(CANARY.CanaryFailure):
            CANARY.validate_canary(
                202,
                {**response, "submission_id": "0198abcd-1111-7000-8000-000000000002"},
                commit,
                dispatch_ref,
                "32712345678",
                "1",
                response["submission_id"],
            )
        with self.assertRaises(CANARY.CanaryFailure):
            CANARY.validate_canary(
                200,
                response,
                commit,
                dispatch_ref,
                "32712345678",
                "1",
                response["submission_id"],
            )

        with self.assertRaisesRegex(CANARY.CanaryFailure, "failed retry"):
            CANARY.validate_canary(
                202,
                {
                    **response,
                    "status": "dispatch_failed",
                    "dispatch_state": "failed",
                    "scheduled_reconciliation": "retry_pending",
                    "workflow_dispatch": "retry_pending",
                },
                commit,
                dispatch_ref,
                "32712345678",
                "1",
                response["submission_id"],
            )

    def test_rejects_redirects_before_forwarding_the_readiness_token(self) -> None:
        handler = CANARY.RejectRedirects()
        request = urllib.request.Request(
            "https://lean-eval-submission-server-staging.lean-eval.workers.dev/readyz",
            headers={"Authorization": "Bearer " + "x" * 32},
        )
        self.assertIsNone(
            handler.redirect_request(
                request,
                mock.Mock(),
                302,
                "Found",
                {},
                "https://attacker.invalid/collect",
            )
        )

    def test_runner_has_no_fixture_source_or_artifact_channel(self) -> None:
        self.assertNotIn("lean-eval-intake-fixture", TEXT)
        self.assertNotIn("ae38f4d3e4ad2991212135435f54e6640bcc89e7", TEXT)
        self.assertNotIn("upload", TEXT.lower())
        self.assertNotIn("print(token", TEXT)
        self.assertIn("STAGING_ORIGIN", TEXT)
        self.assertIn("READINESS_TOKEN", TEXT)
        self.assertIn("RejectRedirects", TEXT)
        self.assertNotIn("urlopen", TEXT)


if __name__ == "__main__":
    unittest.main()
