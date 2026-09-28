from __future__ import annotations

import base64
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts import reserve_historical_public_cleanup as reservation


SHA = "8" * 40
TASK = "rt1_" + "2" * 64
REQUEST = {"schema_version": 1, "replay_task_id": TASK, "attempt": 2}
PLAN = {
    "started_transition": {
        "subject_id": TASK,
        "payload": {"attempt": 2},
    },
    "task": {
        "execution_profile_digest": "3" * 64,
        "measurement_config_digest": "4" * 64,
    },
    "execution_profile": {"vm_image_digest": "sha256:" + "5" * 64},
}
OIDC_URL = "https://pipelines.actions.githubusercontent.com/token?api-version=1"
WORKER = "https://lean-eval-historical-public-replay.lean-eval.workers.dev"
RESERVATION_URL = WORKER + "/api/v1/historical-public-replay/cleanup-reservation"
HEALTH_URL = WORKER + "/healthz"


def encoded(value: object) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def token(now: int, nonce: str = "one", **overrides: object) -> str:
    claims = {
        "iss": reservation.GITHUB_ISSUER,
        "aud": reservation.AUDIENCE,
        "sub": (
            f"repo:{reservation.GITHUB_REPOSITORY}:"
            f"environment:{reservation.ENVIRONMENT}"
        ),
        "repository": reservation.GITHUB_REPOSITORY,
        "repository_id": reservation.GITHUB_REPOSITORY_ID,
        "repository_owner_id": reservation.GITHUB_OWNER_ID,
        "environment": reservation.ENVIRONMENT,
        "ref": "refs/heads/main",
        "ref_protected": "true",
        "sha": SHA,
        "workflow_ref": reservation.WORKFLOW_REF,
        "workflow_sha": SHA,
        "event_name": "workflow_dispatch",
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "nonce": nonce,
    }
    claims.update(overrides)
    return ".".join(
        (
            encoded({"alg": "RS256", "typ": "JWT", "kid": "test-key"}),
            encoded(claims),
            encoded({"signature": nonce}),
        )
    )


def response(status: int, value: object) -> reservation.HttpResponse:
    return reservation.HttpResponse(
        status,
        json.dumps(value, separators=(",", ":")).encode(),
    )


def oidc_response(value: str) -> reservation.HttpResponse:
    return response(200, {"value": value})


def success_response() -> reservation.HttpResponse:
    return response(200, {**REQUEST, "status": "reserved"})


def health_response() -> reservation.HttpResponse:
    return response(200, reservation.expected_health(PLAN, SHA))


class FakeHttp:
    def __init__(self, outcomes: list[reservation.HttpResponse | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        _timeout: float,
    ) -> reservation.HttpResponse:
        self.calls.append((method, url, headers, body))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class HistoricalPublicCleanupReservationTests(unittest.TestCase):
    def run_reservation(
        self,
        fake: FakeHttp,
        *,
        attempts: int = 3,
    ) -> tuple[pathlib.Path, list[float]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = pathlib.Path(temporary.name) / "confirmation.json"
        sleeps: list[float] = []
        reservation.reserve_cleanup(
            request=REQUEST,
            plan=PLAN,
            confirmation_output=output,
            github_sha=SHA,
            oidc_request_url=OIDC_URL,
            oidc_request_token="actions-token",
            reservation_url=RESERVATION_URL,
            health_url=HEALTH_URL,
            attempts=attempts,
            backoff_seconds=0.25,
            http_request=fake,
            sleep=sleeps.append,
            now=lambda: 1_000,
        )
        return output, sleeps

    def test_exact_401_rechecks_health_and_mints_fresh_token(self) -> None:
        first = token(1_000, "first")
        second = token(1_000, "second")
        fake = FakeHttp(
            [
                oidc_response(first),
                response(401, {"error": "unauthorized"}),
                health_response(),
                oidc_response(second),
                success_response(),
            ]
        )
        output, sleeps = self.run_reservation(fake)
        self.assertEqual(json.loads(output.read_text()), {**REQUEST, "status": "reserved"})
        self.assertEqual(sleeps, [0.25])
        posts = [call for call in fake.calls if call[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0][2]["Authorization"], f"Bearer {first}")
        self.assertEqual(posts[1][2]["Authorization"], f"Bearer {second}")
        self.assertEqual(posts[0][3], posts[1][3])

    def test_persistent_exact_401_exhausts(self) -> None:
        fake = FakeHttp(
            [
                oidc_response(token(1_000, "one")),
                response(401, {"error": "unauthorized"}),
                health_response(),
                oidc_response(token(1_000, "two")),
                response(401, {"error": "unauthorized"}),
                health_response(),
                oidc_response(token(1_000, "three")),
                response(401, {"error": "unauthorized"}),
            ]
        )
        with self.assertRaisesRegex(reservation.ReservationError, "exhausted"):
            self.run_reservation(fake)
        self.assertEqual(len([call for call in fake.calls if call[0] == "POST"]), 3)

    def test_transport_ambiguity_retries_same_request_after_health(self) -> None:
        fake = FakeHttp(
            [
                oidc_response(token(1_000, "one")),
                reservation.TransportError("hidden"),
                health_response(),
                oidc_response(token(1_000, "two")),
                success_response(),
            ]
        )
        output, _sleeps = self.run_reservation(fake)
        self.assertTrue(output.is_file())
        posts = [call for call in fake.calls if call[0] == "POST"]
        self.assertEqual(posts[0][3], posts[1][3])
        self.assertNotEqual(
            posts[0][2]["Authorization"],
            posts[1][2]["Authorization"],
        )

    def test_malformed_or_extra_key_401_fails_without_retry(self) -> None:
        for body in ({"error": "unauthorized", "detail": "hidden"}, []):
            with self.subTest(body=body):
                fake = FakeHttp(
                    [oidc_response(token(1_000)), response(401, body)]
                )
                with self.assertRaisesRegex(reservation.ReservationError, "redacted"):
                    self.run_reservation(fake)
                self.assertEqual(len(fake.calls), 2)

    def test_400_fails_without_retry(self) -> None:
        fake = FakeHttp(
            [
                oidc_response(token(1_000)),
                response(400, {"error": "invalid_request"}),
            ]
        )
        with self.assertRaisesRegex(reservation.ReservationError, "http_status=400"):
            self.run_reservation(fake)
        self.assertEqual(len(fake.calls), 2)

    def test_malformed_200_fails_without_retry(self) -> None:
        fake = FakeHttp(
            [
                oidc_response(token(1_000)),
                response(200, {**REQUEST, "status": "reserved", "extra": True}),
            ]
        )
        with self.assertRaisesRegex(reservation.ReservationError, "body is invalid"):
            self.run_reservation(fake)
        self.assertEqual(len(fake.calls), 2)

    def test_claim_mismatch_fails_before_post(self) -> None:
        fake = FakeHttp([oidc_response(token(1_000, sha="7" * 40))])
        with self.assertRaisesRegex(reservation.ReservationError, "claims differ"):
            self.run_reservation(fake)
        self.assertEqual([call[0] for call in fake.calls], ["GET"])

    def test_request_mismatch_fails_before_network(self) -> None:
        fake = FakeHttp([])
        with self.assertRaisesRegex(reservation.ReservationError, "exact replay plan"):
            reservation.reserve_cleanup(
                request={**REQUEST, "attempt": 3},
                plan=PLAN,
                confirmation_output=pathlib.Path("unused"),
                github_sha=SHA,
                oidc_request_url=OIDC_URL,
                oidc_request_token="actions-token",
                reservation_url=RESERVATION_URL,
                health_url=HEALTH_URL,
                attempts=3,
                backoff_seconds=0,
                http_request=fake,
            )
        self.assertEqual(fake.calls, [])

    def test_fourth_attempt_is_valid_and_fifth_is_rejected(self) -> None:
        fourth = {**REQUEST, "attempt": 4}
        self.assertEqual(reservation.validate_request(fourth), fourth)
        with self.assertRaisesRegex(reservation.ReservationError, "request is invalid"):
            reservation.validate_request({**REQUEST, "attempt": 5})

    def test_redirect_handler_refuses_all_redirects(self) -> None:
        handler = reservation._NoRedirect()
        request = reservation.urllib.request.Request(OIDC_URL)
        self.assertIsNone(
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://attacker.example/token",
            )
        )

    def test_http_client_uses_fixed_non_browser_user_agent(self) -> None:
        class Response:
            status = 200

            def read(self, _limit: int) -> bytes:
                return b"{}"

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                pass

        opener = mock.Mock()
        opener.open.return_value = Response()
        with mock.patch.object(
            reservation.urllib.request,
            "build_opener",
            return_value=opener,
        ):
            reservation.request_http(
                "POST",
                RESERVATION_URL,
                {"User-Agent": "caller-controlled"},
                b"{}",
                1,
            )
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), reservation.USER_AGENT)

    def test_http_error_body_transport_failure_is_ambiguous(self) -> None:
        class BrokenBody:
            def read(self, _limit: int) -> bytes:
                raise OSError("hidden transport detail")

            def close(self) -> None:
                pass

        error = reservation.urllib.error.HTTPError(
            RESERVATION_URL,
            401,
            "Unauthorized",
            {},
            BrokenBody(),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(
            reservation.urllib.request,
            "build_opener",
            return_value=opener,
        ):
            with self.assertRaises(reservation.TransportError):
                reservation.request_http(
                    "POST",
                    RESERVATION_URL,
                    {},
                    b"{}",
                    1,
                )

    def test_unavailable_retry_health_never_posts_again(self) -> None:
        fake = FakeHttp(
            [
                oidc_response(token(1_000)),
                response(401, {"error": "unauthorized"}),
                response(200, {"status": "old deployment"}),
                response(200, {"status": "old deployment"}),
            ]
        )
        with self.assertRaisesRegex(reservation.ReservationError, "executor health"):
            self.run_reservation(fake)
        self.assertEqual(len([call for call in fake.calls if call[0] == "POST"]), 1)


if __name__ == "__main__":
    unittest.main()
