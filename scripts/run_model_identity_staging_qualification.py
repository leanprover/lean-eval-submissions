#!/usr/bin/env python3
"""Drive the one-shot dark model-identity staging qualification contract.

This controller deliberately cannot enable either public model-identity API.  It
speaks only to the separately reviewed staging qualification harness and makes
restoration mandatory after the harness has accepted the initial preflight.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import NamedTuple

STAGING_ORIGIN = "https://lean-eval-submission-server-staging.lean-eval.workers.dev"
QUALIFICATION_PATH = "/internal/v1/model-identity-qualification"
SHA = re.compile(r"[0-9a-f]{40}\Z")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
CONFIRMATION = "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING"
MAX_MODEL_IDENTITY_SUBREQUESTS = 400

HEALTH_FIELDS = {
    "deployed_commit",
    "environment",
    "intake_configured_enabled",
    "intake_effective_enabled",
    "intake_enabled",
    "intake_enablement_mode",
    "intake_lease_expires_at",
    "legacy_result_owner_api_enabled",
    "model_identity_consolidation_api",
    "model_identity_maintainer_api_enabled",
    "model_identity_owner_api_enabled",
    "model_identity_write_max_subrequests",
    "promotion_canary_configured_enabled",
    "promotion_canary_enabled",
    "result_amendment_maintainer_api_enabled",
    "result_amendment_owner_api_enabled",
    "service",
    "status",
}

# Each proof name is a closed assertion made by the staging harness.  In
# particular, an aggregate "lifecycle passed" response is never sufficient.
QUALIFICATION_PROOFS = (
    "oauth_session_identity",
    "agent_session_identity",
    "owner_request",
    "maintainer_approve",
    "maintainer_reject",
    "alias_assignment",
    "identity_rename",
    "complete_graph_consolidation",
    "chained_terminal_retry",
    "component_cap_refusal",
    "idempotent_retry",
    "cross_route_event_collision",
    "cross_owner_denial",
    "maximal_contention_measurement",
)

PROOF_OUTCOMES = {
    "oauth_session_identity": "exact_identity_verified",
    "agent_session_identity": "exact_identity_verified",
    "owner_request": "identity_requested",
    "maintainer_approve": "identity_approved",
    "maintainer_reject": "identity_rejected",
    "alias_assignment": "alias_assigned",
    "identity_rename": "identity_renamed",
    "complete_graph_consolidation": "complete_graph_retargeted",
    "chained_terminal_retry": "current_terminal_verified",
    "component_cap_refusal": "component_cap_refused",
    "idempotent_retry": "immutable_event_reused",
    "cross_route_event_collision": "event_collision_refused",
    "cross_owner_denial": "cross_owner_refused",
    "maximal_contention_measurement": "eight_attempt_path_measured",
}

NON_MUTATING_PROOFS = {
    "oauth_session_identity",
    "agent_session_identity",
    "component_cap_refusal",
    "idempotent_retry",
    "cross_route_event_collision",
    "cross_owner_denial",
}

COMMON_RESPONSE_FIELDS = {
    "cas_attempts",
    "deployed_commit",
    "maintainer_api_enabled",
    "mutation_created",
    "operation",
    "outcome",
    "owner_api_enabled",
    "previous_state_commit",
    "run_id",
    "schema_version",
    "state_commit",
    "status",
    "subrequests",
}


class QualificationFailure(RuntimeError):
    """A closed qualification or restoration failure."""


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward the dedicated credential or sessions across redirects."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        RejectRedirects(),
    )


def require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise QualificationFailure(f"{label} was not a JSON object")
    return value


def validate_health(status: int, value: object, expected_commit: str) -> None:
    body = require_object(value, "staging health response")
    if status != 200 or set(body) != HEALTH_FIELDS:
        raise QualificationFailure("staging health response was not canonical")
    if (
        body["status"] != "ok"
        or body["service"] != "lean-eval-submission"
        or body["environment"] != "staging"
        or body["deployed_commit"] != expected_commit
        or body["intake_configured_enabled"] is not False
        or body["intake_effective_enabled"] is not False
        or body["intake_enabled"] is not False
        or body["intake_enablement_mode"] != "disabled"
        or body["intake_lease_expires_at"] is not None
        or body["model_identity_owner_api_enabled"] is not False
        or body["model_identity_maintainer_api_enabled"] is not False
        or body["model_identity_write_max_subrequests"]
        != MAX_MODEL_IDENTITY_SUBREQUESTS
        or body["model_identity_consolidation_api"] != "atomic_reverse_impact_v1"
        or body["promotion_canary_configured_enabled"] is not True
        or body["promotion_canary_enabled"] is not True
    ):
        raise QualificationFailure(
            "staging health did not prove the exact disabled qualification boundary"
        )


class Sessions(NamedTuple):
    oauth: str
    agent: str
    cross_owner: str

    @classmethod
    def from_environment(cls) -> Sessions:
        values = cls(
            oauth=os.environ.get("MODEL_IDENTITY_OAUTH_SESSION", ""),
            agent=os.environ.get("MODEL_IDENTITY_AGENT_SESSION", ""),
            cross_owner=os.environ.get("MODEL_IDENTITY_CROSS_OWNER_SESSION", ""),
        )
        for label, value in (
            ("MODEL_IDENTITY_OAUTH_SESSION", values.oauth),
            ("MODEL_IDENTITY_AGENT_SESSION", values.agent),
            ("MODEL_IDENTITY_CROSS_OWNER_SESSION", values.cross_owner),
        ):
            if not 32 <= len(value.encode()) <= 4096:
                raise QualificationFailure(
                    f"{label} length is outside the closed bound"
                )
        if len({values.oauth, values.agent, values.cross_owner}) != 3:
            raise QualificationFailure(
                "qualification session credentials must be distinct"
            )
        return values


RequestJson = Callable[
    [str, Mapping[str, object] | None, Sessions | None], tuple[int, object]
]


def validate_proof_response(
    status: int,
    value: object,
    *,
    expected_commit: str,
    expected_head: str,
    operation: str,
    run_id: str,
) -> str:
    body = require_object(value, f"{operation} response")
    if status != 200 or set(body) != COMMON_RESPONSE_FIELDS:
        raise QualificationFailure(f"{operation} response was not canonical")
    state_commit = body["state_commit"]
    if not isinstance(state_commit, str) or SHA.fullmatch(state_commit) is None:
        raise QualificationFailure(f"{operation} returned a noncanonical State commit")
    if (
        body["schema_version"] != 1
        or body["status"] != "model_identity_qualification_step_verified"
        or body["operation"] != operation
        or body["outcome"] != PROOF_OUTCOMES[operation]
        or body["deployed_commit"] != expected_commit
        or body["run_id"] != run_id
        or body["previous_state_commit"] != expected_head
        or body["owner_api_enabled"] is not False
        or body["maintainer_api_enabled"] is not False
    ):
        raise QualificationFailure(f"{operation} did not prove its closed boundary")
    mutation_created = body["mutation_created"]
    if not isinstance(mutation_created, bool):
        raise QualificationFailure(f"{operation} mutation marker was not boolean")
    if operation in NON_MUTATING_PROOFS:
        if mutation_created or state_commit != expected_head:
            raise QualificationFailure(
                f"{operation} unexpectedly changed staging State"
            )
    elif not mutation_created:
        raise QualificationFailure(f"{operation} did not create its expected mutation")

    attempts = body["cas_attempts"]
    subrequests = body["subrequests"]
    if operation == "maximal_contention_measurement":
        if (
            attempts != 8
            or not isinstance(subrequests, int)
            or isinstance(subrequests, bool)
            or not 1 <= subrequests <= MAX_MODEL_IDENTITY_SUBREQUESTS
        ):
            raise QualificationFailure(
                "maximal contention did not measure the exact eight-attempt bounded path"
            )
    elif attempts is not None or subrequests is not None:
        raise QualificationFailure(f"{operation} exposed unexpected measurement fields")
    return state_commit


def validate_restore_response(
    status: int,
    value: object,
    *,
    expected_commit: str,
    initial_state_commit: str,
    run_id: str,
) -> str:
    fields = {
        "deployed_commit",
        "maintainer_api_enabled",
        "owner_api_enabled",
        "restored_tree_commit",
        "run_id",
        "schema_version",
        "state_commit",
        "status",
    }
    body = require_object(value, "qualification restoration response")
    if status != 200 or set(body) != fields:
        raise QualificationFailure(
            "qualification restoration response was not canonical"
        )
    state_commit = body["state_commit"]
    if (
        body["schema_version"] != 1
        or body["status"] != "model_identity_qualification_restored"
        or body["deployed_commit"] != expected_commit
        or body["run_id"] != run_id
        or body["restored_tree_commit"] != initial_state_commit
        or body["owner_api_enabled"] is not False
        or body["maintainer_api_enabled"] is not False
        or not isinstance(state_commit, str)
        or SHA.fullmatch(state_commit) is None
    ):
        raise QualificationFailure("qualification restoration was not exact")
    return state_commit


def run_qualification(
    request_json: RequestJson,
    *,
    expected_commit: str,
    initial_state_commit: str,
    run_id: str,
    sessions: Sessions,
) -> dict[str, object]:
    status, health = request_json("health", None, None)
    validate_health(status, health, expected_commit)

    current_head = initial_state_commit
    completed: list[str] = []
    primary_error: QualificationFailure | None = None
    restore_commit: str | None = None
    try:
        for operation in QUALIFICATION_PROOFS:
            payload = {
                "schema_version": 1,
                "confirmation": CONFIRMATION,
                "deployed_commit": expected_commit,
                "expected_state_commit": current_head,
                "initial_state_commit": initial_state_commit,
                "operation": operation,
                "run_id": run_id,
            }
            status, response = request_json("step", payload, sessions)
            current_head = validate_proof_response(
                status,
                response,
                expected_commit=expected_commit,
                expected_head=current_head,
                operation=operation,
                run_id=run_id,
            )
            completed.append(operation)
    except QualificationFailure as error:
        primary_error = error
    finally:
        cleanup_payload = {
            "schema_version": 1,
            "confirmation": CONFIRMATION,
            "deployed_commit": expected_commit,
            "initial_state_commit": initial_state_commit,
            "last_observed_state_commit": current_head,
            "operation": "restore",
            "run_id": run_id,
        }
        try:
            status, response = request_json("restore", cleanup_payload, sessions)
            restore_commit = validate_restore_response(
                status,
                response,
                expected_commit=expected_commit,
                initial_state_commit=initial_state_commit,
                run_id=run_id,
            )
        except QualificationFailure as cleanup_error:
            if primary_error is not None:
                raise QualificationFailure(
                    f"qualification failed ({type(primary_error).__name__}) and "
                    f"mandatory restoration failed ({type(cleanup_error).__name__})"
                ) from cleanup_error
            raise QualificationFailure(
                "mandatory qualification restoration failed"
            ) from cleanup_error
    if primary_error is not None:
        raise QualificationFailure(
            f"qualification failed after {len(completed)} verified proof(s); restoration passed"
        ) from primary_error

    status, health = request_json("health", None, None)
    validate_health(status, health, expected_commit)
    if restore_commit is None:
        raise QualificationFailure("mandatory restoration did not return a commit")
    return {
        "deployed_commit": expected_commit,
        "initial_state_commit": initial_state_commit,
        "proofs": completed,
        "restoration_commit": restore_commit,
        "run_id": run_id,
        "status": "model_identity_staging_qualification_passed_and_restored",
    }


def http_client(
    client: urllib.request.OpenerDirector,
    token: str,
) -> RequestJson:
    def request_json(
        kind: str,
        payload: Mapping[str, object] | None,
        sessions: Sessions | None,
    ) -> tuple[int, object]:
        if kind == "health":
            url = f"{STAGING_ORIGIN}/healthz"
            data = None
            method = "GET"
            headers = {"User-Agent": "lean-eval-model-identity-qualification/1"}
        else:
            if kind not in {"step", "restore"} or payload is None or sessions is None:
                raise QualificationFailure(
                    "qualification HTTP request was not canonical"
                )
            url = f"{STAGING_ORIGIN}{QUALIFICATION_PATH}"
            data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            method = "POST"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "lean-eval-model-identity-qualification/1",
                "X-Lean-Eval-Agent-Session": sessions.agent,
                "X-Lean-Eval-Cross-Owner-Session": sessions.cross_owner,
                "X-Lean-Eval-OAuth-Session": sessions.oauth,
            }
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with client.open(request, timeout=90) as response:
                status = response.status
                encoded = response.read(32 * 1024 + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            encoded = error.read(4097)
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            raise QualificationFailure(
                f"{kind} transport failed ({type(error).__name__})"
            ) from None
        if len(encoded) > 32 * 1024:
            raise QualificationFailure(f"{kind} response exceeded its hard bound")
        try:
            return status, json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise QualificationFailure(
                f"{kind} did not return canonical JSON"
            ) from None

    return request_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--initial-state-commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if SHA.fullmatch(args.expected_commit) is None:
        raise QualificationFailure("expected deployment commit is not canonical")
    if SHA.fullmatch(args.initial_state_commit) is None:
        raise QualificationFailure("initial State commit is not canonical")
    if RUN_ID.fullmatch(args.run_id) is None:
        raise QualificationFailure("workflow run ID is not canonical")
    if args.confirm != CONFIRMATION:
        raise QualificationFailure("one-shot confirmation is not exact")
    token = os.environ.get("MODEL_IDENTITY_QUALIFICATION_TOKEN", "")
    if not 32 <= len(token.encode()) <= 4096:
        raise QualificationFailure(
            "MODEL_IDENTITY_QUALIFICATION_TOKEN length is outside the closed bound"
        )
    result = run_qualification(
        http_client(opener(), token),
        expected_commit=args.expected_commit,
        initial_state_commit=args.initial_state_commit,
        run_id=args.run_id,
        sessions=Sessions.from_environment(),
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except QualificationFailure as error:
        print(f"model identity staging qualification failed: {error}", file=sys.stderr)
        sys.exit(1)
