"""Executable contract tests for the bounded production intake lease smoke."""

from __future__ import annotations

import importlib.util
import http.client
import io
import json
import pathlib
import tempfile
import time
import types
import unittest
import urllib.error
from contextlib import redirect_stderr
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_production_intake_lease_smoke.py"
SPEC = importlib.util.spec_from_file_location("production_intake_lease_smoke", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load production intake lease smoke module")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)

CONTRACT = "a" * 40
SCHEMA = "b" * 64
STATE_COMMIT = "c" * 40
EXPIRES_AT = 2_000
TOKEN = "readiness-token-with-at-least-thirty-two-bytes"


class ProductionIntakeLeaseSmokeTests(unittest.TestCase):
    def request(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "environment": "production",
            "controller_commit": "d" * 40,
            "controller_run_attempt": "1",
            "controller_run_id": "33714130157",
            "event_id": "019debcf-f258-7000-8000-000000000001",
            "expires_at": EXPIRES_AT,
            "issued_at": EXPIRES_AT - 900,
            "nonce": "private-nonce-that-must-never-appear-in-errors",
            "state_commit": "e" * 40,
            "target_commit": "d" * 40,
        }

    def smoke(self, *, status: str = "lease_smoke_consumed") -> dict[str, object]:
        return {
            "status": status,
            "environment": "production",
            "intake_configured_enabled": True,
            "intake_effective_enabled": True,
            "intake_enablement_mode": "leased",
            "intake_lease_expires_at": EXPIRES_AT,
            "state_commit": STATE_COMMIT,
        }

    def readiness(self, *, state_commit: str = STATE_COMMIT) -> dict[str, object]:
        return {
            "status": "state_writer_ready",
            "environment": "production",
            "intake_configured_enabled": True,
            "intake_effective_enabled": True,
            "intake_enabled": True,
            "intake_enablement_mode": "leased",
            "intake_lease_expires_at": EXPIRES_AT,
            "state_branch_protected": True,
            "state_commit": state_commit,
            "state_contract_commit": CONTRACT,
            "state_contract_verified": True,
            "state_event_schema_sha256": SCHEMA,
        }

    def args(self, directory: pathlib.Path) -> types.SimpleNamespace:
        request = directory / "request.json"
        request.write_text(json.dumps(self.request()), encoding="utf-8")
        return types.SimpleNamespace(
            request=request,
            response_output=directory / "response.json",
            state_proof_output=directory / "proof.json",
            expected_expires_at=EXPIRES_AT,
            expected_contract=CONTRACT,
            expected_schema=SCHEMA,
            poll_seconds=5.0,
        )

    def test_retries_only_closed_state_unavailability_and_stale_check_lag(self) -> None:
        responses = [
            (503, {"error": "state_unavailable"}),
            (200, self.smoke(status="lease_smoke_already_consumed")),
            (200, self.readiness(state_commit="f" * 40)),
            (503, {"status": "not_ready", "reason": "state_unavailable"}),
            (200, self.readiness()),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            args = self.args(pathlib.Path(temporary))
            with (
                mock.patch.object(SMOKE, "request_json", side_effect=responses) as request,
                mock.patch.dict(SMOKE.os.environ, {"READINESS_TOKEN": TOKEN}),
                mock.patch.object(SMOKE.time, "sleep") as sleep,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                consumed, proof = SMOKE.run(
                    args,
                    client=mock.sentinel.client,
                    clock=lambda: 1_000,
                    sleeper=sleep,
                )
            self.assertEqual(consumed, self.smoke(status="lease_smoke_already_consumed"))
            self.assertEqual(proof, self.readiness())
            self.assertEqual(request.call_count, 5)
            self.assertEqual(
                [call.args[1] for call in request.call_args_list],
                [
                    "/internal/v1/intake-lease-smoke",
                    "/internal/v1/intake-lease-smoke",
                    "/readyz",
                    "/readyz",
                    "/readyz",
                ],
            )
            self.assertEqual(sleep.call_count, 3)
            self.assertNotIn(TOKEN, stderr.getvalue())
            self.assertNotIn(str(self.request()["nonce"]), stderr.getvalue())
            self.assertEqual(
                json.loads(args.response_output.read_text(encoding="utf-8")),
                consumed,
            )
            self.assertEqual(
                json.loads(args.state_proof_output.read_text(encoding="utf-8")),
                proof,
            )

    def test_wire_json_rejects_duplicate_members_before_classification(self) -> None:
        class Response:
            status = 503

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit: int) -> bytes:
                return b'{"error":"fatal","error":"state_unavailable"}'

        client = mock.Mock()
        client.open.return_value = Response()
        with self.assertRaisesRegex(SMOKE.LeaseSmokeFailure, "closed JSON"):
            SMOKE.request_json(
                client,
                "/internal/v1/intake-lease-smoke",
                TOKEN,
                None,
            )

        with tempfile.TemporaryDirectory() as temporary:
            args = self.args(pathlib.Path(temporary))
            args.request.write_text(
                '{"schema_version":2,"schema_version":1}', encoding="utf-8"
            )
            with (
                mock.patch.object(SMOKE, "request_json") as request,
                mock.patch.dict(SMOKE.os.environ, {"READINESS_TOKEN": TOKEN}),
            ):
                with self.assertRaisesRegex(
                    SMOKE.LeaseSmokeFailure,
                    "not readable canonical JSON",
                ):
                    SMOKE.run(args, client=mock.sentinel.client, clock=lambda: 1_000)
            request.assert_not_called()

    def test_total_deadline_interrupts_a_trickling_response(self) -> None:
        class TricklingResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit: int) -> bytes:
                for _ in range(100):
                    time.sleep(0.01)
                return b"{}"

        client = mock.Mock()
        client.open.return_value = TricklingResponse()
        started = time.monotonic()
        with self.assertRaisesRegex(
            SMOKE.LeaseSmokeRetryable,
            "RequestDeadlineExpired",
        ):
            SMOKE.request_json(
                client,
                "/readyz",
                TOKEN,
                None,
                timeout_seconds=0.03,
            )
        self.assertLess(time.monotonic() - started, 0.3)

    def test_protocol_and_http_error_body_failures_are_source_free(self) -> None:
        private = "private-upstream-status-line-must-not-appear"
        protocol_client = mock.Mock()
        protocol_client.open.side_effect = http.client.BadStatusLine(private)

        class BrokenHttpError(urllib.error.HTTPError):
            def __init__(self) -> None:
                super().__init__(
                    "https://fixed.test",
                    503,
                    "Service Unavailable",
                    {},
                    None,
                )

            def read(self, _limit: int) -> bytes:
                raise http.client.IncompleteRead(private.encode())

        body_client = mock.Mock()
        broken_http_error = BrokenHttpError()
        body_client.open.side_effect = broken_http_error
        for client in (protocol_client, body_client):
            with self.subTest(client=client), self.assertRaises(
                SMOKE.LeaseSmokeRetryable
            ) as caught:
                SMOKE.request_json(client, "/readyz", TOKEN, None)
            self.assertNotIn(private, str(caught.exception))
        broken_http_error.close()

    def test_transport_failure_is_bounded_and_idempotently_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.args(pathlib.Path(temporary))
            responses = [
                SMOKE.LeaseSmokeRetryable("temporary transport failure"),
                (200, self.smoke()),
                (200, self.readiness()),
            ]
            with (
                mock.patch.object(SMOKE, "request_json", side_effect=responses) as request,
                mock.patch.dict(SMOKE.os.environ, {"READINESS_TOKEN": TOKEN}),
                mock.patch.object(SMOKE.time, "sleep") as sleep,
                redirect_stderr(io.StringIO()),
            ):
                SMOKE.run(
                    args,
                    client=mock.sentinel.client,
                    clock=lambda: 1_000,
                    sleeper=sleep,
                )
            self.assertEqual(request.call_count, 3)
            sleep.assert_called_once_with(5.0)

    def test_nontransient_or_nonclosed_responses_fail_once_without_leaking_body(self) -> None:
        hostile = {"error": "state_unavailable", "detail": "private upstream detail"}
        for status, body in ((503, hostile), (409, {"error": "lease_binding_mismatch"})):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                args = self.args(pathlib.Path(temporary))
                with (
                    mock.patch.object(SMOKE, "request_json", return_value=(status, body)) as request,
                    mock.patch.dict(SMOKE.os.environ, {"READINESS_TOKEN": TOKEN}),
                    mock.patch.object(SMOKE.time, "sleep") as sleep,
                    redirect_stderr(io.StringIO()) as stderr,
                ):
                    with self.assertRaises(SMOKE.LeaseSmokeFailure):
                        SMOKE.run(args, client=mock.sentinel.client, clock=lambda: 1_000)
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()
                self.assertNotIn("private upstream detail", stderr.getvalue())
                self.assertFalse(args.response_output.exists())
                self.assertFalse(args.state_proof_output.exists())

    def test_stops_before_an_http_attempt_can_spend_the_240_second_margin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.args(pathlib.Path(temporary))
            deadline = EXPIRES_AT - SMOKE.FINALIZATION_MARGIN_SECONDS
            with (
                mock.patch.object(SMOKE, "request_json") as request,
                mock.patch.dict(SMOKE.os.environ, {"READINESS_TOKEN": TOKEN}),
            ):
                with self.assertRaisesRegex(
                    SMOKE.LeaseSmokeFailure,
                    "preserving the finalization margin",
                ):
                    SMOKE.run(
                        args,
                        client=mock.sentinel.client,
                        clock=lambda: deadline - SMOKE.REQUEST_TIMEOUT_SECONDS + 0.1,
                    )
            request.assert_not_called()

    def test_retry_stops_before_sleep_can_spend_the_240_second_margin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.args(pathlib.Path(temporary))
            deadline = EXPIRES_AT - SMOKE.FINALIZATION_MARGIN_SECONDS
            with (
                mock.patch.object(
                    SMOKE,
                    "request_json",
                    return_value=(503, {"error": "state_unavailable"}),
                ) as request,
                mock.patch.dict(SMOKE.os.environ, {"READINESS_TOKEN": TOKEN}),
                mock.patch.object(SMOKE.time, "sleep") as sleep,
            ):
                with self.assertRaisesRegex(
                    SMOKE.LeaseSmokeFailure,
                    "did not converge before the lease finalization margin",
                ):
                    SMOKE.run(
                        args,
                        client=mock.sentinel.client,
                        clock=mock.Mock(
                            side_effect=[
                                1_000,
                                deadline - SMOKE.REQUEST_TIMEOUT_SECONDS - 4,
                            ]
                        ),
                        sleeper=sleep,
                    )
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()

    def test_output_files_are_create_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.args(pathlib.Path(temporary))
            args.response_output.write_text("retained\n", encoding="utf-8")
            responses = [(200, self.smoke()), (200, self.readiness())]
            with (
                mock.patch.object(SMOKE, "request_json", side_effect=responses) as request,
                mock.patch.dict(SMOKE.os.environ, {"READINESS_TOKEN": TOKEN}),
            ):
                with self.assertRaisesRegex(
                    SMOKE.LeaseSmokeFailure,
                    "could not create lease smoke response output",
                ):
                    SMOKE.run(args, client=mock.sentinel.client, clock=lambda: 1_000)
            request.assert_not_called()
            self.assertEqual(args.response_output.read_text(encoding="utf-8"), "retained\n")


if __name__ == "__main__":
    unittest.main()
