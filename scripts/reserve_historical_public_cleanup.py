#!/usr/bin/env python3
"""Reserve one historical public sandbox identity before State becomes running."""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


AUDIENCE = "lean-eval-historical-public-replay-production"
ENVIRONMENT = "replay-production"
GITHUB_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_REPOSITORY = "leanprover/lean-eval-submissions"
GITHUB_REPOSITORY_ID = "1243533004"
GITHUB_OWNER_ID = "7233018"
WORKFLOW_REF = (
    "leanprover/lean-eval-submissions/.github/workflows/"
    "historical-authoritative-replay.yml@refs/heads/main"
)
COMMIT = re.compile(r"[0-9a-f]{40}")
REPLAY_TASK_ID = re.compile(r"rt1_[0-9a-f]{64}")
BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
MAX_RESPONSE_BYTES = 64 * 1024
MAX_TOKEN_BYTES = 8192
USER_AGENT = "lean-eval-historical-public-replay/1"


class ReservationError(ValueError):
    """The exact pre-State reservation could not be established."""


class TransportError(OSError):
    """An HTTP request ended without an authoritative response."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


HttpRequest = Callable[[str, str, dict[str, str], bytes | None, float], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _read_response(response: Any) -> bytes:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ReservationError("HTTP response exceeds the closed size limit")
    return body


def request_http(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout_seconds: float,
) -> HttpResponse:
    headers = {
        key: value for key, value in headers.items() if key.lower() != "user-agent"
    }
    headers["User-Agent"] = USER_AGENT
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(request, timeout=timeout_seconds) as response:
            return HttpResponse(response.status, _read_response(response))
    except urllib.error.HTTPError as error:
        try:
            try:
                body = _read_response(error)
            except ReservationError:
                raise
            except (OSError, TimeoutError) as body_error:
                raise TransportError(
                    "HTTP transport did not return a complete response"
                ) from body_error
        finally:
            error.close()
        return HttpResponse(error.code, body)
    except ReservationError:
        raise
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        raise TransportError("HTTP transport did not return a response") from error


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReservationError(f"{label} is not canonical JSON") from error
    if not isinstance(value, dict):
        raise ReservationError(f"{label} is not a JSON object")
    return value


def _decode_jwt_object(encoded: str, label: str) -> dict[str, Any]:
    if BASE64URL.fullmatch(encoded) is None:
        raise ReservationError(f"OIDC {label} encoding is invalid")
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, base64.binascii.Error) as error:
        raise ReservationError(f"OIDC {label} encoding is invalid") from error
    return _json_object(raw, f"OIDC {label}")


def validate_oidc_token(token: str, github_sha: str, now_seconds: int) -> None:
    if not isinstance(token, str) or not token or len(token.encode()) > MAX_TOKEN_BYTES:
        raise ReservationError("OIDC token shape is invalid")
    parts = token.split(".")
    if len(parts) != 3 or any(BASE64URL.fullmatch(part) is None for part in parts):
        raise ReservationError("OIDC token shape is invalid")
    header = _decode_jwt_object(parts[0], "header")
    claims = _decode_jwt_object(parts[1], "claims")
    if (
        header.get("alg") != "RS256"
        or header.get("typ") != "JWT"
        or not isinstance(header.get("kid"), str)
        or not header["kid"]
        or len(header["kid"]) > 256
    ):
        raise ReservationError("OIDC token header is not allowed")
    expected = {
        "iss": GITHUB_ISSUER,
        "aud": AUDIENCE,
        "sub": f"repo:{GITHUB_REPOSITORY}:environment:{ENVIRONMENT}",
        "repository": GITHUB_REPOSITORY,
        "repository_id": GITHUB_REPOSITORY_ID,
        "repository_owner_id": GITHUB_OWNER_ID,
        "environment": ENVIRONMENT,
        "ref": "refs/heads/main",
        "ref_protected": "true",
        "sha": github_sha,
        "workflow_ref": WORKFLOW_REF,
        "workflow_sha": github_sha,
        "event_name": "workflow_dispatch",
    }
    if any(claims.get(key) != value for key, value in expected.items()):
        raise ReservationError("OIDC token claims differ from the exact replay binding")
    timing = [claims.get(key) for key in ("iat", "nbf", "exp")]
    if any(type(value) is not int for value in timing):
        raise ReservationError("OIDC token timestamps are invalid")
    issued_at, not_before, expires_at = timing
    if (
        not_before > now_seconds + 30
        or expires_at < now_seconds - 30
        or issued_at > now_seconds + 30
        or expires_at - issued_at < 1
        or expires_at - issued_at > 600
    ):
        raise ReservationError("OIDC token is not currently valid")


def validate_request(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "replay_task_id", "attempt"}
        or value.get("schema_version") != 1
        or not isinstance(value.get("replay_task_id"), str)
        or REPLAY_TASK_ID.fullmatch(value["replay_task_id"]) is None
        or type(value.get("attempt")) is not int
        or value["attempt"] < 1
        or value["attempt"] > 4
    ):
        raise ReservationError("cleanup reservation request is invalid")
    return value


def expected_health(plan: dict[str, Any], github_sha: str) -> dict[str, Any]:
    try:
        task = plan["task"]
        profile = plan["execution_profile"]
        return {
            "status": "ok",
            "service": "lean-eval-replay-executor",
            "environment": "production",
            "deployed_commit": github_sha,
            "replay_enabled": False,
            "historical_public_replay_enabled": True,
            "staging_acceptance_enabled": False,
            "staging_memory_limit_bytes": 12 * 1024**3,
            "production_memory_gate_bytes": 12 * 1024**3,
            "reviewed_execution_profile_digest": task["execution_profile_digest"],
            "reviewed_measurement_config_digest": task["measurement_config_digest"],
            "reviewed_vm_image_digest": profile["vm_image_digest"],
        }
    except (KeyError, TypeError) as error:
        raise ReservationError("historical replay plan lacks health bindings") from error


def expected_request(plan: dict[str, Any]) -> dict[str, Any]:
    try:
        transition = plan["started_transition"]
        return validate_request(
            {
                "schema_version": 1,
                "replay_task_id": transition["subject_id"],
                "attempt": transition["payload"]["attempt"],
            }
        )
    except (KeyError, TypeError) as error:
        raise ReservationError(
            "historical replay plan lacks cleanup reservation bindings"
        ) from error


def validate_health(response: HttpResponse, expected: dict[str, Any]) -> None:
    if response.status != 200:
        raise ReservationError("exact historical executor health is unavailable")
    if _json_object(response.body, "historical executor health") != expected:
        raise ReservationError("historical executor health differs from the exact plan")


def classify_reservation(
    response: HttpResponse,
    request: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    if response.status == 200:
        value = _json_object(response.body, "cleanup reservation confirmation")
        expected = {
            **request,
            "status": "reserved",
        }
        if value != expected:
            raise ReservationError("successful cleanup reservation body is invalid")
        return "success", value
    try:
        value = _json_object(response.body, "cleanup reservation rejection")
    except ReservationError:
        value = None
    if response.status == 401 and value == {"error": "unauthorized"}:
        return "retry", None
    safe_reason: str | None = None
    if value is not None and set(value) == {"error"} and value.get("error") in {
        "unauthorized",
        "invalid_request",
        "historical_public_replay_disabled",
    }:
        safe_reason = f"error={value['error']} reason=none"
    if (
        value is not None
        and set(value) == {"error", "reason"}
        and value.get("error") == "executor_failed"
        and value.get("reason")
        in {
            "input_transfer_failed",
            "command_rpc_failed",
            "command_failed",
            "command_output_invalid",
            "sandbox_destroy_failed",
            "unexpected_failure",
        }
    ):
        safe_reason = f"error=executor_failed reason={value['reason']}"
    suffix = safe_reason if safe_reason is not None else "body=redacted"
    raise ReservationError(
        f"cleanup reservation rejected (http_status={response.status} {suffix})"
    )


def _oidc_url(base: str) -> str:
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}audience={AUDIENCE}"


def _validate_https_url(value: str, *, host: str | None = None) -> None:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (host is not None and parsed.hostname != host)
    ):
        raise ReservationError("HTTP endpoint authority is invalid")


def reserve_cleanup(
    *,
    request: dict[str, Any],
    plan: dict[str, Any],
    confirmation_output: pathlib.Path,
    github_sha: str,
    oidc_request_url: str,
    oidc_request_token: str,
    reservation_url: str,
    health_url: str,
    attempts: int,
    backoff_seconds: float,
    http_request: HttpRequest = request_http,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> None:
    request = validate_request(request)
    if request != expected_request(plan):
        raise ReservationError(
            "cleanup reservation request differs from the exact replay plan"
        )
    if COMMIT.fullmatch(github_sha) is None:
        raise ReservationError("GitHub workflow commit is invalid")
    _validate_https_url(oidc_request_url)
    oidc_host = urllib.parse.urlsplit(oidc_request_url).hostname
    if (
        oidc_host is None
        or not oidc_host.endswith(".actions.githubusercontent.com")
        or not oidc_request_token
    ):
        raise ReservationError("GitHub OIDC request authority is unavailable")
    worker_host = "lean-eval-historical-public-replay.lean-eval.workers.dev"
    _validate_https_url(reservation_url, host=worker_host)
    _validate_https_url(health_url, host=worker_host)
    if attempts < 1 or attempts > 20 or backoff_seconds < 0 or backoff_seconds > 30:
        raise ReservationError("reservation retry bound is invalid")
    health = expected_health(plan, github_sha)
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            try:
                validate_health(
                    http_request("GET", health_url, {}, None, 15),
                    health,
                )
            except (ReservationError, TransportError):
                if attempt == attempts:
                    raise ReservationError(
                        "cleanup reservation exhausted while awaiting exact executor health"
                    )
                print(
                    "cleanup reservation retry waiting for exact executor health "
                    f"(attempt={attempt}/{attempts})",
                    file=sys.stderr,
                )
                sleep(backoff_seconds)
                continue
        try:
            oidc_response = http_request(
                "GET",
                _oidc_url(oidc_request_url),
                {"Authorization": f"bearer {oidc_request_token}"},
                None,
                15,
            )
        except TransportError:
            if attempt == attempts:
                raise ReservationError("GitHub OIDC request exhausted its retry bound")
            print(
                f"GitHub OIDC transport retry (attempt={attempt}/{attempts})",
                file=sys.stderr,
            )
            sleep(backoff_seconds)
            continue
        if oidc_response.status != 200:
            raise ReservationError(
                f"GitHub OIDC request failed (http_status={oidc_response.status})"
            )
        oidc_body = _json_object(oidc_response.body, "GitHub OIDC response")
        if set(oidc_body) != {"value"} or not isinstance(oidc_body["value"], str):
            raise ReservationError("GitHub OIDC response shape is invalid")
        token = oidc_body["value"]
        validate_oidc_token(token, github_sha, int(now()))
        try:
            response = http_request(
                "POST",
                reservation_url,
                {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
                + b"\n",
                30,
            )
        except TransportError:
            if attempt == attempts:
                raise ReservationError(
                    "cleanup reservation transport ambiguity exhausted its retry bound"
                )
            print(
                "cleanup reservation transport ambiguity; retrying the exact identity "
                f"(attempt={attempt}/{attempts})",
                file=sys.stderr,
            )
            sleep(backoff_seconds)
            continue
        outcome, confirmation = classify_reservation(response, request)
        if outcome == "success" and confirmation is not None:
            raw = json.dumps(
                confirmation,
                sort_keys=True,
                separators=(",", ":"),
            ).encode() + b"\n"
            temporary = confirmation_output.with_suffix(
                confirmation_output.suffix + ".tmp"
            )
            temporary.write_bytes(raw)
            temporary.replace(confirmation_output)
            return
        if attempt == attempts:
            raise ReservationError(
                "cleanup reservation unauthorized response exhausted its retry bound"
            )
        print(
            "cleanup reservation temporarily unauthorized; retrying with a fresh token "
            f"(http_status=401 attempt={attempt}/{attempts})",
            file=sys.stderr,
        )
        sleep(backoff_seconds)
    raise ReservationError("cleanup reservation exhausted its retry bound")


def _load_object(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ReservationError(f"{label} is unavailable") from error
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise ReservationError(f"{label} exceeds its closed size limit")
    return _json_object(raw, label)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=pathlib.Path)
    parser.add_argument("--plan", required=True, type=pathlib.Path)
    parser.add_argument("--confirmation-output", required=True, type=pathlib.Path)
    parser.add_argument("--github-sha", required=True)
    parser.add_argument("--reservation-url", required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--backoff-seconds", type=float, default=5)
    args = parser.parse_args(argv)
    try:
        reserve_cleanup(
            request=_load_object(args.request, "cleanup reservation request"),
            plan=_load_object(args.plan, "historical replay plan"),
            confirmation_output=args.confirmation_output,
            github_sha=args.github_sha,
            oidc_request_url=os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", ""),
            oidc_request_token=os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", ""),
            reservation_url=args.reservation_url,
            health_url=args.health_url,
            attempts=args.attempts,
            backoff_seconds=args.backoff_seconds,
        )
    except (OSError, ReservationError, TransportError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
