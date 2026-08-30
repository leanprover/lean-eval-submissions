"""Contract tests for the source-free staging promotion runner."""

from __future__ import annotations

import importlib.util
import pathlib
import types
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


class StagingPromotionCanaryReadinessTests(unittest.TestCase):
    def readiness(self) -> dict[str, object]:
        return {
            "environment": "staging",
            "intake_configured_enabled": False,
            "intake_effective_enabled": False,
            "intake_enabled": False,
            "intake_enablement_mode": "disabled",
            "intake_lease_expires_at": None,
            "state_commit": "a" * 40,
            "status": "state_writer_ready",
        }

    def test_accepts_exact_disabled_finite_lease_readiness_contract(self) -> None:
        CANARY.validate_readiness(200, self.readiness())

    def test_rejects_missing_extra_or_enabled_lease_fields(self) -> None:
        missing = self.readiness()
        del missing["intake_enablement_mode"]
        extra = {**self.readiness(), "unexpected": False}
        enabled = {
            **self.readiness(),
            "intake_configured_enabled": True,
            "intake_effective_enabled": True,
            "intake_enabled": True,
            "intake_enablement_mode": "durable",
        }
        leased = {
            **self.readiness(),
            "intake_enablement_mode": "leased",
            "intake_lease_expires_at": 1_777_778_000,
        }
        for body in (missing, extra, enabled, leased):
            with self.subTest(body=body):
                with self.assertRaises(CANARY.CanaryFailure):
                    CANARY.validate_readiness(200, body)


class StagingPromotionCanaryTests(unittest.TestCase):
    def test_http_error_classification_is_closed_and_source_free(self) -> None:
        for detail, expected in CANARY.CANARY_INVALID_REQUEST_REASONS.items():
            encoded = CANARY.json.dumps(
                {"error": "invalid_request", "detail": detail},
                separators=(",", ":"),
            ).encode()
            self.assertEqual(CANARY.http_failure_reason(400, encoded), expected)
        self.assertEqual(
            CANARY.http_failure_reason(
                400,
                b'{"error":"invalid_request","detail":"private upstream text"}',
            ),
            "invalid_request_other",
        )
        self.assertEqual(
            CANARY.http_failure_reason(500, b'{"detail":"must not surface"}'),
            "http_500",
        )
        self.assertEqual(CANARY.http_failure_reason(400, b"not-json"), "http_400")

    def test_only_exact_deployment_binding_failures_are_retryable(self) -> None:
        binding = CANARY.http_failure(
            "/internal/v1/promotion-canary",
            400,
            CANARY.json.dumps(
                {
                    "error": "invalid_request",
                    "detail": "promotion canary request does not bind this exact deployment",
                },
                separators=(",", ":"),
            ).encode(),
        )
        self.assertIsInstance(binding, CANARY.CanaryDeploymentBindingMismatch)
        self.assertEqual(
            str(binding),
            "/internal/v1/promotion-canary returned HTTP 400 (deployment_binding_mismatch)",
        )

        hostile = {
            "request_not_canonical": "promotion canary request is not canonical",
            "invalid_request_other": "private upstream detail must never be emitted",
        }
        for expected, detail in hostile.items():
            with self.subTest(expected=expected):
                failure = CANARY.http_failure(
                    "/internal/v1/promotion-canary",
                    400,
                    CANARY.json.dumps(
                        {"error": "invalid_request", "detail": detail},
                        separators=(",", ":"),
                    ).encode(),
                )
                self.assertIs(type(failure), CANARY.CanaryFailure)
                self.assertEqual(
                    str(failure),
                    f"/internal/v1/promotion-canary returned HTTP 400 ({expected})",
                )
                self.assertNotIn(detail, str(failure))

    def test_only_bounded_gateway_unavailability_is_transport_retryable(self) -> None:
        for status in (502, 503, 504):
            with self.subTest(status=status):
                failure = CANARY.http_failure(
                    "/internal/v1/promotion-canary",
                    status,
                    b'{"detail":"must not surface"}',
                )
                self.assertIsInstance(failure, CANARY.CanaryConnectivityFailure)
                self.assertEqual(
                    str(failure),
                    "/internal/v1/promotion-canary returned "
                    f"HTTP {status} (http_{status})",
                )
                self.assertNotIn("must not surface", str(failure))

        for status in (500, 501, 505):
            with self.subTest(status=status):
                failure = CANARY.http_failure(
                    "/internal/v1/promotion-canary",
                    status,
                    b'{"detail":"must not surface"}',
                )
                self.assertIs(type(failure), CANARY.CanaryFailure)

    def test_main_retries_a_bounded_canary_transport_timeout(self) -> None:
        commit = "c" * 40
        args = types.SimpleNamespace(
            commit=commit,
            dispatch_ref=f"lean-eval-dispatch/{commit}",
            run_id="32712345678",
            run_attempt="1",
            timeout_seconds=480,
            poll_seconds=5,
        )
        readiness = {
            "environment": "staging",
            "intake_configured_enabled": False,
            "intake_effective_enabled": False,
            "intake_enabled": False,
            "intake_enablement_mode": "disabled",
            "intake_lease_expires_at": None,
            "state_commit": "a" * 40,
            "status": "state_writer_ready",
        }
        request_json = mock.Mock(
            side_effect=[
                (200, readiness),
                CANARY.CanaryConnectivityFailure("timed out"),
                (200, {}),
            ]
        )
        with (
            mock.patch.object(CANARY, "parse_args", return_value=args),
            mock.patch.object(CANARY, "opener", return_value=mock.sentinel.client),
            mock.patch.object(CANARY, "request_json", request_json),
            mock.patch.object(
                CANARY,
                "validate_canary",
                return_value=("0198abcd-1111-7000-8000-0000000000ca", True),
            ),
            mock.patch.object(CANARY.time, "monotonic", side_effect=[100, 101]),
            mock.patch.object(CANARY.time, "sleep") as sleep,
            mock.patch("builtins.print"),
            mock.patch.dict(CANARY.os.environ, {"READINESS_TOKEN": "x" * 32}),
        ):
            self.assertEqual(CANARY.main(), 0)
        self.assertEqual(request_json.call_count, 3)
        self.assertEqual(
            request_json.call_args_list[1].kwargs,
            {"timeout_seconds": 30},
        )
        sleep.assert_called_once_with(5)

    def test_main_retries_only_a_bounded_deployment_binding_mismatch(self) -> None:
        commit = "c" * 40
        args = types.SimpleNamespace(
            commit=commit,
            dispatch_ref=f"lean-eval-dispatch/{commit}",
            run_id="32712345678",
            run_attempt="1",
            timeout_seconds=480,
            poll_seconds=5,
        )
        readiness = {
            "environment": "staging",
            "intake_configured_enabled": False,
            "intake_effective_enabled": False,
            "intake_enabled": False,
            "intake_enablement_mode": "disabled",
            "intake_lease_expires_at": None,
            "state_commit": "a" * 40,
            "status": "state_writer_ready",
        }
        mismatch = CANARY.CanaryDeploymentBindingMismatch(
            "/internal/v1/promotion-canary returned HTTP 400 "
            "(deployment_binding_mismatch)"
        )
        request_json = mock.Mock(
            side_effect=[
                (200, readiness),
                mismatch,
                (200, {}),
            ]
        )
        with (
            mock.patch.object(CANARY, "parse_args", return_value=args),
            mock.patch.object(CANARY, "opener", return_value=mock.sentinel.client),
            mock.patch.object(CANARY, "request_json", request_json),
            mock.patch.object(
                CANARY,
                "validate_canary",
                return_value=("0198abcd-1111-7000-8000-0000000000ca", True),
            ),
            mock.patch.object(CANARY.time, "monotonic", side_effect=[100, 101]),
            mock.patch.object(CANARY.time, "sleep") as sleep,
            mock.patch("builtins.print"),
            mock.patch.dict(CANARY.os.environ, {"READINESS_TOKEN": "x" * 32}),
        ):
            self.assertEqual(CANARY.main(), 0)
        self.assertEqual(request_json.call_count, 3)
        sleep.assert_called_once_with(5)

    def test_main_never_retries_other_invalid_requests(self) -> None:
        commit = "c" * 40
        args = types.SimpleNamespace(
            commit=commit,
            dispatch_ref=f"lean-eval-dispatch/{commit}",
            run_id="32712345678",
            run_attempt="1",
            timeout_seconds=480,
            poll_seconds=5,
        )
        readiness = {
            "environment": "staging",
            "intake_configured_enabled": False,
            "intake_effective_enabled": False,
            "intake_enabled": False,
            "intake_enablement_mode": "disabled",
            "intake_lease_expires_at": None,
            "state_commit": "a" * 40,
            "status": "state_writer_ready",
        }
        hostile = {
            "request_not_canonical": "promotion canary request is not canonical",
            "invalid_request_other": "private arbitrary detail must not escape",
        }
        for reason, detail in hostile.items():
            with self.subTest(reason=reason):
                failure = CANARY.http_failure(
                    "/internal/v1/promotion-canary",
                    400,
                    CANARY.json.dumps(
                        {"error": "invalid_request", "detail": detail},
                        separators=(",", ":"),
                    ).encode(),
                )
                self.assertIs(type(failure), CANARY.CanaryFailure)
                self.assertNotIn(detail, str(failure))
                request_json = mock.Mock(
                    side_effect=[
                        (200, readiness),
                        failure,
                    ]
                )
                with (
                    mock.patch.object(CANARY, "parse_args", return_value=args),
                    mock.patch.object(
                        CANARY,
                        "opener",
                        return_value=mock.sentinel.client,
                    ),
                    mock.patch.object(CANARY, "request_json", request_json),
                    mock.patch.object(CANARY.time, "monotonic", return_value=100),
                    mock.patch.object(CANARY.time, "sleep") as sleep,
                    mock.patch.dict(
                        CANARY.os.environ,
                        {"READINESS_TOKEN": "x" * 32},
                    ),
                ):
                    with self.assertRaises(CANARY.CanaryFailure):
                        CANARY.main()
                self.assertEqual(request_json.call_count, 2)
                sleep.assert_not_called()

    def test_deployment_binding_retry_stops_at_the_existing_deadline(self) -> None:
        commit = "c" * 40
        args = types.SimpleNamespace(
            commit=commit,
            dispatch_ref=f"lean-eval-dispatch/{commit}",
            run_id="32712345678",
            run_attempt="1",
            timeout_seconds=480,
            poll_seconds=5,
        )
        readiness = {
            "environment": "staging",
            "intake_configured_enabled": False,
            "intake_effective_enabled": False,
            "intake_enabled": False,
            "intake_enablement_mode": "disabled",
            "intake_lease_expires_at": None,
            "state_commit": "a" * 40,
            "status": "state_writer_ready",
        }
        request_json = mock.Mock(
            side_effect=[
                (200, readiness),
                CANARY.CanaryDeploymentBindingMismatch("closed reason"),
            ]
        )
        with (
            mock.patch.object(CANARY, "parse_args", return_value=args),
            mock.patch.object(CANARY, "opener", return_value=mock.sentinel.client),
            mock.patch.object(CANARY, "request_json", request_json),
            mock.patch.object(CANARY.time, "monotonic", side_effect=[100, 576]),
            mock.patch.object(CANARY.time, "sleep") as sleep,
            mock.patch.dict(CANARY.os.environ, {"READINESS_TOKEN": "x" * 32}),
        ):
            with self.assertRaisesRegex(
                CANARY.CanaryFailure,
                "deployment binding did not converge",
            ):
                CANARY.main()
        self.assertEqual(request_json.call_count, 2)
        sleep.assert_not_called()

    def test_accepts_only_the_source_free_exact_success_contract(self) -> None:
        commit = "c" * 40
        dispatch_ref = f"lean-eval-dispatch/{commit}"
        submission_id = "0198abcd-1111-7000-8000-0000000000ca"
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
            "cas_contention": "idempotent_prior_collision_and_retry_proof",
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
            "submission_id": "0198abcd-1111-7000-8000-0000000000ca",
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
                {**response, "submission_id": "0198abcd-1111-7000-8000-0000000001ca"},
                commit,
                dispatch_ref,
                "32712345678",
                "1",
                response["submission_id"],
            )
        with self.assertRaisesRegex(CANARY.CanaryFailure, "non-canonical submission"):
            CANARY.validate_canary(
                202,
                {**response, "submission_id": "0198abcd-1111-7000-8000-000000000001"},
                commit,
                dispatch_ref,
                "32712345678",
                "1",
                None,
            )
        with self.assertRaisesRegex(CANARY.CanaryFailure, "proof disagree"):
            CANARY.validate_canary(
                202,
                {**response, "synthetic_intake": "idempotent"},
                commit,
                dispatch_ref,
                "32712345678",
                "1",
                None,
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
