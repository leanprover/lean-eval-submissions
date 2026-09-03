#!/usr/bin/env python3
"""Consume and prove the production intake lease without spending its safety margin."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import pathlib
import re
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Callable


PRODUCTION_ORIGIN = "https://lean-eval-submission-server.lean-eval.workers.dev"
FINALIZATION_MARGIN_SECONDS = 240
REQUEST_TIMEOUT_SECONDS = 20
DEFAULT_POLL_SECONDS = 5.0
MAX_RESPONSE_BYTES = 16 * 1024
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SMOKE_FIELDS = {
    "environment",
    "intake_configured_enabled",
    "intake_effective_enabled",
    "intake_enablement_mode",
    "intake_lease_expires_at",
    "state_commit",
    "status",
}
READINESS_FIELDS = {
    "environment",
    "intake_configured_enabled",
    "intake_effective_enabled",
    "intake_enabled",
    "intake_enablement_mode",
    "intake_lease_expires_at",
    "state_branch_protected",
    "state_commit",
    "state_contract_commit",
    "state_contract_verified",
    "state_event_schema_sha256",
    "status",
}
REQUEST_FIELDS = {
    "controller_commit",
    "controller_run_attempt",
    "controller_run_id",
    "environment",
    "event_id",
    "expires_at",
    "issued_at",
    "nonce",
    "schema_version",
    "state_commit",
    "target_commit",
}


class LeaseSmokeFailure(RuntimeError):
    """The bounded lease proof cannot safely continue."""


class LeaseSmokeRetryable(LeaseSmokeFailure):
    """One source-free transient response may be retried inside the lease."""


class RequestDeadlineExpired(TimeoutError):
    """The total open-and-read deadline expired."""


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward the readiness credential across a redirect."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        RejectRedirects(),
    )


def closed_json(encoded: bytes) -> object:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value

    def reject_constant(_value: str) -> object:
        raise ValueError("non-finite JSON number")

    return json.loads(
        encoded,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )


def object_file(path: pathlib.Path, label: str) -> dict[str, object]:
    try:
        encoded = path.read_bytes()
        if not encoded or len(encoded) > MAX_RESPONSE_BYTES:
            raise LeaseSmokeFailure(f"{label} is empty or oversized")
        value = closed_json(encoded)
    except LeaseSmokeFailure:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise LeaseSmokeFailure(f"{label} is not readable canonical JSON") from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise LeaseSmokeFailure(f"{label} is not a JSON object")
    return value


def request_json(
    client: urllib.request.OpenerDirector,
    path: str,
    token: str,
    payload: dict[str, object] | None,
    *,
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[int, object]:
    encoded_payload = None if payload is None else json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()
    request = urllib.request.Request(
        f"{PRODUCTION_ORIGIN}{path}",
        data=encoded_payload if encoded_payload is not None else b"",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "lean-eval-production-intake-lease-smoke/1",
        },
    )
    if not 0 < timeout_seconds <= REQUEST_TIMEOUT_SECONDS:
        raise LeaseSmokeFailure(f"{path} total request deadline was invalid")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def expire(_signum: int, _frame: object) -> None:
        raise RequestDeadlineExpired()

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        try:
            with client.open(request, timeout=timeout_seconds) as response:
                status = response.status
                encoded = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            encoded = error.read(MAX_RESPONSE_BYTES + 1)
    except (
        RequestDeadlineExpired,
        OSError,
        TimeoutError,
        urllib.error.URLError,
        http.client.HTTPException,
    ) as error:
        raise LeaseSmokeRetryable(
            f"{path} transport was temporarily unavailable ({type(error).__name__})"
        ) from None
    except Exception as error:
        raise LeaseSmokeFailure(
            f"{path} transport failed closed ({type(error).__name__})"
        ) from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise LeaseSmokeFailure(f"{path} response exceeded its hard bound")
    try:
        return status, closed_json(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise LeaseSmokeFailure(f"{path} did not return closed JSON") from None


def require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise LeaseSmokeFailure(f"{label} was not a JSON object")
    return value


def validate_request(value: dict[str, object], expected_expires_at: int) -> None:
    if set(value) != REQUEST_FIELDS:
        raise LeaseSmokeFailure("lease smoke request was not a closed document")
    if (
        value.get("schema_version") != 1
        or value.get("environment") != "production"
        or type(value.get("expires_at")) is not int
        or value.get("expires_at") != expected_expires_at
    ):
        raise LeaseSmokeFailure("lease smoke request did not bind the expected lease")


def classify_smoke(
    status: int,
    value: object,
    expected_expires_at: int,
) -> tuple[dict[str, object], str]:
    body = require_object(value, "lease smoke response")
    if status == 503:
        if set(body) == {"error"} and body.get("error") == "state_unavailable":
            raise LeaseSmokeRetryable("State append is temporarily unavailable")
        raise LeaseSmokeFailure("lease smoke 503 was not the closed retryable response")
    if status != 200 or set(body) != SMOKE_FIELDS:
        raise LeaseSmokeFailure(
            f"lease smoke returned a non-retryable HTTP response ({status})"
        )
    state_commit = body.get("state_commit")
    if (
        body.get("status")
        not in {"lease_smoke_consumed", "lease_smoke_already_consumed"}
        or body.get("environment") != "production"
        or body.get("intake_configured_enabled") is not True
        or body.get("intake_effective_enabled") is not True
        or body.get("intake_enablement_mode") != "leased"
        or body.get("intake_lease_expires_at") != expected_expires_at
        or not isinstance(state_commit, str)
        or SHA.fullmatch(state_commit) is None
    ):
        raise LeaseSmokeFailure("lease smoke success response was not the exact proof")
    return body, state_commit


def classify_readiness(
    status: int,
    value: object,
    expected_expires_at: int,
    expected_state_commit: str,
    expected_contract: str,
    expected_schema: str,
) -> dict[str, object]:
    body = require_object(value, "post-smoke State readiness response")
    if status == 503:
        if (
            set(body) == {"reason", "status"}
            and body.get("status") == "not_ready"
            and body.get("reason") == "state_unavailable"
        ):
            raise LeaseSmokeRetryable("post-smoke State check is temporarily unavailable")
        raise LeaseSmokeFailure("post-smoke readiness 503 was not the closed retryable response")
    if status != 200 or set(body) != READINESS_FIELDS:
        raise LeaseSmokeFailure(
            f"post-smoke readiness returned a non-retryable HTTP response ({status})"
        )
    state_commit = body.get("state_commit")
    exact_except_commit = (
        body.get("status") == "state_writer_ready"
        and body.get("environment") == "production"
        and body.get("intake_configured_enabled") is True
        and body.get("intake_effective_enabled") is True
        and body.get("intake_enabled") is True
        and body.get("intake_enablement_mode") == "leased"
        and body.get("intake_lease_expires_at") == expected_expires_at
        and body.get("state_branch_protected") is True
        and body.get("state_contract_verified") is True
        and body.get("state_contract_commit") == expected_contract
        and body.get("state_event_schema_sha256") == expected_schema
        and isinstance(state_commit, str)
        and SHA.fullmatch(state_commit) is not None
    )
    if not exact_except_commit:
        raise LeaseSmokeFailure("post-smoke readiness was not the exact protected-State proof")
    if state_commit != expected_state_commit:
        raise LeaseSmokeRetryable("post-smoke State head has not converged")
    return body


def require_attempt_window(now: float, deadline: int) -> None:
    if now + REQUEST_TIMEOUT_SECONDS > deadline:
        raise LeaseSmokeFailure(
            "lease smoke cannot continue without preserving the finalization margin"
        )


def retry_pause(
    *,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
    deadline: int,
    poll_seconds: float,
    phase: str,
) -> None:
    if clock() + poll_seconds + REQUEST_TIMEOUT_SECONDS > deadline:
        raise LeaseSmokeFailure(
            f"{phase} did not converge before the lease finalization margin"
        )
    print(f"{phase} retryable; waiting inside the bounded lease", file=sys.stderr)
    sleeper(poll_seconds)


def run(
    args: argparse.Namespace,
    *,
    client: urllib.request.OpenerDirector | None = None,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, object], dict[str, object]]:
    if (
        type(args.expected_expires_at) is not int
        or args.expected_expires_at <= 0
        or SHA.fullmatch(args.expected_contract) is None
        or DIGEST.fullmatch(args.expected_schema) is None
        or not 1 <= args.poll_seconds <= 30
    ):
        raise LeaseSmokeFailure("lease smoke expectations are invalid")
    request = object_file(args.request, "lease smoke request")
    validate_request(request, args.expected_expires_at)
    for path, label in (
        (args.response_output, "lease smoke response output"),
        (args.state_proof_output, "post-smoke State proof output"),
    ):
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise LeaseSmokeFailure(f"could not inspect {label}") from error
        else:
            raise LeaseSmokeFailure(f"could not create {label}")
    token = os.environ.get("READINESS_TOKEN", "")
    if not 32 <= len(token.encode()) <= 4096:
        raise LeaseSmokeFailure("READINESS_TOKEN length is outside the closed bound")
    deadline = args.expected_expires_at - FINALIZATION_MARGIN_SECONDS
    transport = opener() if client is None else client

    while True:
        require_attempt_window(clock(), deadline)
        try:
            status, response = request_json(
                transport,
                "/internal/v1/intake-lease-smoke",
                token,
                request,
                timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            )
            smoke, state_commit = classify_smoke(
                status, response, args.expected_expires_at
            )
        except LeaseSmokeRetryable:
            retry_pause(
                clock=clock,
                sleeper=sleeper,
                deadline=deadline,
                poll_seconds=args.poll_seconds,
                phase="production intake lease smoke",
            )
            continue
        require_attempt_window(clock(), deadline)
        break

    while True:
        require_attempt_window(clock(), deadline)
        try:
            status, response = request_json(
                transport,
                "/readyz",
                token,
                None,
                timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            )
            proof = classify_readiness(
                status,
                response,
                args.expected_expires_at,
                state_commit,
                args.expected_contract,
                args.expected_schema,
            )
        except LeaseSmokeRetryable:
            retry_pause(
                clock=clock,
                sleeper=sleeper,
                deadline=deadline,
                poll_seconds=args.poll_seconds,
                phase="post-smoke State readiness",
            )
            continue
        require_attempt_window(clock(), deadline)
        break

    for path, value, label in (
        (args.response_output, smoke, "lease smoke response output"),
        (args.state_proof_output, proof, "post-smoke State proof output"),
    ):
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2, sort_keys=True)
                stream.write("\n")
        except OSError as error:
            raise LeaseSmokeFailure(f"could not create {label}") from error
    return smoke, proof


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=pathlib.Path, required=True)
    parser.add_argument("--response-output", type=pathlib.Path, required=True)
    parser.add_argument("--state-proof-output", type=pathlib.Path, required=True)
    parser.add_argument("--expected-expires-at", type=int, required=True)
    parser.add_argument("--expected-contract", required=True)
    parser.add_argument("--expected-schema", required=True)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    return parser.parse_args()


def main() -> int:
    try:
        run(parse_args())
    except LeaseSmokeFailure as error:
        print(f"production intake lease smoke failed: {error}", file=sys.stderr)
        return 1
    print("production intake lease smoke and State proof converged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
