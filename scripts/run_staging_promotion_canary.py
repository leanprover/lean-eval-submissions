#!/usr/bin/env python3
"""Run the source-free, readiness-authenticated staging promotion gate."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request


STAGING_ORIGIN = "https://lean-eval-submission-server-staging.lean-eval.workers.dev"
SHA = re.compile(r"[0-9a-f]{40}\Z")
UUID_V7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{10}ca\Z"
)
CANARY_FIELDS = {
    "cas_contention",
    "controller_run_attempt",
    "controller_run_id",
    "deployed_commit",
    "dispatch_state",
    "dispatch_ref",
    "environment",
    "github_connectivity",
    "scheduled_reconciliation",
    "status",
    "submission_id",
    "synthetic_intake",
    "workflow_dispatch",
}
CANARY_INVALID_REQUEST_REASONS = {
    "promotion canary request must be an object": "request_not_object",
    "promotion canary request is not canonical": "request_not_canonical",
    "promotion canary request does not bind this exact deployment": "deployment_binding_mismatch",
}


class CanaryFailure(RuntimeError):
    """A source-free promotion-gate failure."""


class CanaryConnectivityFailure(CanaryFailure):
    """A retryable bounded transport failure."""


class CanaryDeploymentBindingMismatch(CanaryFailure):
    """A retryable stale staging deployment response."""


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Reject redirects before urllib can forward the readiness credential."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        RejectRedirects(),
    )


def http_failure_reason(status: int, encoded: bytes) -> str:
    if status != 400 or len(encoded) > 4096:
        return f"http_{status}"
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "http_400"
    if (
        not isinstance(value, dict)
        or set(value) != {"detail", "error"}
        or value.get("error") != "invalid_request"
        or not isinstance(value.get("detail"), str)
    ):
        return "http_400"
    return CANARY_INVALID_REQUEST_REASONS.get(value["detail"], "invalid_request_other")


def http_failure(path: str, status: int, encoded: bytes) -> CanaryFailure:
    reason = http_failure_reason(status, encoded)
    if reason == "deployment_binding_mismatch":
        failure = CanaryDeploymentBindingMismatch
    elif status in {502, 503, 504}:
        # A newly deployed Worker or Container can briefly cross the exact
        # readiness boundary after the deployment job has returned.  Reuse the
        # existing bounded transport deadline rather than requiring another
        # deployment for a source-free, idempotent canary request.
        failure = CanaryConnectivityFailure
    else:
        failure = CanaryFailure
    return failure(f"{path} returned HTTP {status} ({reason})")


def request_json(
    client: urllib.request.OpenerDirector,
    path: str,
    token: str,
    payload: dict[str, object] | None,
    *,
    timeout_seconds: int = 10,
) -> tuple[int, object]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        f"{STAGING_ORIGIN}{path}",
        data=body if body is not None else b"",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "lean-eval-staging-promotion-canary/1",
        },
    )
    try:
        with client.open(
            request,
            timeout=timeout_seconds,
        ) as response:
            status = response.status
            encoded = response.read(16 * 1024 + 1)
    except urllib.error.HTTPError as error:
        raise http_failure(path, error.code, error.read(4097)) from None
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        raise CanaryConnectivityFailure(
            f"{path} connectivity failed ({type(error).__name__})"
        ) from None
    if len(encoded) > 16 * 1024:
        raise CanaryFailure(f"{path} response exceeded its hard bound")
    try:
        return status, json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CanaryFailure(f"{path} did not return canonical JSON") from None


def require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CanaryFailure(f"{label} was not a JSON object")
    return value


def validate_readiness(status: int, value: object) -> None:
    body = require_object(value, "readiness response")
    if status != 200 or set(body) != {
        "environment",
        "intake_configured_enabled",
        "intake_effective_enabled",
        "intake_enabled",
        "intake_enablement_mode",
        "intake_lease_expires_at",
        "state_commit",
        "status",
    }:
        raise CanaryFailure("staging write-readiness response was not canonical")
    if (
        body["status"] != "state_writer_ready"
        or body["environment"] != "staging"
        or body["intake_configured_enabled"] is not False
        or body["intake_effective_enabled"] is not False
        or body["intake_enabled"] is not False
        or body["intake_enablement_mode"] != "disabled"
        or body["intake_lease_expires_at"] is not None
        or not isinstance(body["state_commit"], str)
        or SHA.fullmatch(body["state_commit"]) is None
    ):
        raise CanaryFailure("staging write-readiness did not prove the disabled State boundary")


def validate_canary(
    status: int,
    value: object,
    commit: str,
    dispatch_ref: str,
    run_id: str,
    run_attempt: str,
    expected_submission_id: str | None,
) -> tuple[str, bool]:
    body = require_object(value, "promotion canary response")
    if status not in {200, 202} or set(body) != CANARY_FIELDS:
        raise CanaryFailure("promotion canary response was not canonical and source-free")
    submission_id = body["submission_id"]
    if not isinstance(submission_id, str) or UUID_V7.fullmatch(submission_id) is None:
        raise CanaryFailure("promotion canary returned a non-canonical submission identity")
    if expected_submission_id is not None and submission_id != expected_submission_id:
        raise CanaryFailure("promotion canary was not idempotent for the exact deployment")
    if (
        body["environment"] != "staging"
        or body["deployed_commit"] != commit
        or body["dispatch_ref"] != dispatch_ref
        or body["controller_run_id"] != run_id
        or body["controller_run_attempt"] != run_attempt
        or body["github_connectivity"] != "verified"
        or body["synthetic_intake"] not in {"created", "idempotent"}
        or body["cas_contention"]
        not in {
            "collision_observed_and_retry_applied",
            "idempotent_prior_collision_and_retry_proof",
        }
    ):
        raise CanaryFailure("promotion canary did not prove every exact staging boundary")
    if (
        (body["synthetic_intake"] == "created")
        != (body["cas_contention"] == "collision_observed_and_retry_applied")
    ):
        raise CanaryFailure("promotion canary intake and contention proof disagree")
    complete = (
        status == 200
        and body["status"] == "passed"
        and body["scheduled_reconciliation"] == "completed"
        and body["dispatch_state"] == "succeeded"
        and body["workflow_dispatch"] == "accepted_by_github"
    )
    pending = (
        status == 202
        and body["status"] == "awaiting_scheduled_reconciliation"
        and body["scheduled_reconciliation"] == "pending"
        and body["dispatch_state"] == "pending"
        and body["workflow_dispatch"] == "pending"
    )
    if body["dispatch_state"] == "failed":
        if body["workflow_dispatch"] != "retry_pending":
            raise CanaryFailure("promotion canary failure fields were inconsistent")
        raise CanaryFailure("promotion canary dispatch entered a failed retry state")
    if not complete and not pending:
        raise CanaryFailure("promotion canary status fields were inconsistent")
    return submission_id, complete


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--dispatch-ref", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if SHA.fullmatch(args.commit) is None:
        raise CanaryFailure("deployment commit must be a full lowercase SHA")
    if args.dispatch_ref != f"lean-eval-dispatch/{args.commit}":
        raise CanaryFailure("dispatch ref must immutably bind the deployment commit")
    if re.fullmatch(r"[1-9][0-9]{0,19}", args.run_id) is None:
        raise CanaryFailure("controller run ID is not canonical")
    if re.fullmatch(r"[1-9][0-9]{0,5}", args.run_attempt) is None:
        raise CanaryFailure("controller run attempt is not canonical")
    if not 30 <= args.timeout_seconds <= 600 or not 1 <= args.poll_seconds <= 30:
        raise CanaryFailure("poll bounds are invalid")
    token = os.environ.get("READINESS_TOKEN", "")
    if not 32 <= len(token.encode()) <= 4096:
        raise CanaryFailure("READINESS_TOKEN length is outside the closed bound")

    client = opener()
    readiness_status, readiness = request_json(client, "/readyz", token, None)
    validate_readiness(readiness_status, readiness)
    request = {
        "schema_version": 2,
        "deployed_commit": args.commit,
        "dispatch_ref": args.dispatch_ref,
        "controller_run_id": args.run_id,
        "controller_run_attempt": args.run_attempt,
    }
    deadline = time.monotonic() + args.timeout_seconds
    submission_id: str | None = None
    while True:
        try:
            status, response = request_json(
                client,
                "/internal/v1/promotion-canary",
                token,
                request,
                timeout_seconds=30,
            )
        except CanaryConnectivityFailure:
            if time.monotonic() + args.poll_seconds > deadline:
                raise CanaryFailure(
                    "promotion transport did not recover before the promotion deadline"
                ) from None
            time.sleep(args.poll_seconds)
            continue
        except CanaryDeploymentBindingMismatch:
            if time.monotonic() + args.poll_seconds > deadline:
                raise CanaryFailure(
                    "staging deployment binding did not converge before the promotion deadline"
                ) from None
            time.sleep(args.poll_seconds)
            continue
        submission_id, complete = validate_canary(
            status,
            response,
            args.commit,
            args.dispatch_ref,
            args.run_id,
            args.run_attempt,
            submission_id,
        )
        if complete:
            print(json.dumps({
                "cas_contention": "verified",
                "controller_run_attempt": args.run_attempt,
                "controller_run_id": args.run_id,
                "deployed_commit": args.commit,
                "github_connectivity": "verified",
                "scheduled_reconciliation": "verified",
                "submission_id": submission_id,
                "synthetic_intake": "verified",
                "workflow_dispatch_acceptance": "verified",
            }, sort_keys=True, separators=(",", ":")))
            return 0
        if time.monotonic() + args.poll_seconds > deadline:
            raise CanaryFailure("scheduled reconciliation did not complete before the promotion deadline")
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CanaryFailure as error:
        print(f"promotion canary failed: {error}", file=sys.stderr)
        sys.exit(1)
