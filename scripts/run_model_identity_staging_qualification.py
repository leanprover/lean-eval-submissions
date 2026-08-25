#!/usr/bin/env python3
"""Drive or recover the journaled dark model-identity staging qualification.

The source workflows that call this controller remain immutable-source-disabled
until a separately reviewed staging-only harness implements this exact protocol.
The harness must journal before the first State write so recovery needs only the
dedicated harness token and the original workflow run ID.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

STAGING_ORIGIN = "https://lean-eval-submission-server-staging.lean-eval.workers.dev"
QUALIFICATION_PATH = "/internal/v1/model-identity-qualification"
SHA = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
LOGIN = re.compile(r"[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?\Z")
JOURNAL_ID = re.compile(r"mqj_[0-9a-f]{64}\Z")
EVENT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
MODEL_ID = re.compile(r"mi1_[0-9a-f]{64}\Z")
ALIAS_KEY = re.compile(r"ma1_[0-9a-f]{64}\Z")
QUALIFY_CONFIRMATION = "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING"
RECOVER_CONFIRMATION = "RESTORE_MODEL_IDENTITY_STAGING_JOURNAL"
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


class QualificationFailure(RuntimeError):
    """A closed qualification, journal, or restoration failure."""


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


def require_fields(
    value: object,
    fields: set[str],
    label: str,
) -> dict[str, object]:
    body = require_object(value, label)
    if set(body) != fields:
        raise QualificationFailure(f"{label} fields were not canonical")
    return body


def positive_int(value: object, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 9_999_999_999_999_999_999
    ):
        raise QualificationFailure(f"{label} was not a positive integer")
    return value


@dataclass(frozen=True)
class Identity:
    github_id: int
    login: str

    @classmethod
    def parse(cls, value: str, label: str) -> Identity:
        pieces = value.split(":", 1)
        if len(pieces) != 2 or RUN_ID.fullmatch(pieces[0]) is None:
            raise QualificationFailure(f"{label} identity was not canonical")
        if LOGIN.fullmatch(pieces[1]) is None:
            raise QualificationFailure(f"{label} login was not canonical")
        return cls(int(pieces[0]), pieces[1])

    def json(self) -> dict[str, object]:
        return {"github_id": self.github_id, "login": self.login}


@dataclass(frozen=True)
class Intent:
    owner: Identity
    cross_owner: Identity
    maintainer: Identity

    def validate(self) -> None:
        if self.owner == self.cross_owner or (
            self.owner.github_id == self.cross_owner.github_id
            or self.owner.login == self.cross_owner.login
        ):
            raise QualificationFailure("cross-owner identity was not distinct")
        identities = (self.owner, self.cross_owner, self.maintainer)
        if len({identity.github_id for identity in identities}) != 3:
            raise QualificationFailure("qualification account IDs were not distinct")
        if len({identity.login for identity in identities}) != 3:
            raise QualificationFailure("qualification logins were not distinct")

    def json(self) -> dict[str, object]:
        return {
            "cross_owner": self.cross_owner.json(),
            "maintainer": self.maintainer.json(),
            "owner": self.owner.json(),
        }


class Sessions(NamedTuple):
    oauth_owner: str
    agent_owner: str
    cross_owner: str
    maintainer: str

    @classmethod
    def from_environment(cls, harness_token: str) -> Sessions:
        values = cls(
            oauth_owner=os.environ.get("MODEL_IDENTITY_OAUTH_SESSION", ""),
            agent_owner=os.environ.get("MODEL_IDENTITY_AGENT_SESSION", ""),
            cross_owner=os.environ.get("MODEL_IDENTITY_CROSS_OWNER_SESSION", ""),
            maintainer=os.environ.get("MODEL_IDENTITY_MAINTAINER_SESSION", ""),
        )
        for label, value in zip(
            (
                "MODEL_IDENTITY_OAUTH_SESSION",
                "MODEL_IDENTITY_AGENT_SESSION",
                "MODEL_IDENTITY_CROSS_OWNER_SESSION",
                "MODEL_IDENTITY_MAINTAINER_SESSION",
            ),
            values,
            strict=True,
        ):
            if not 32 <= len(value.encode()) <= 4096:
                raise QualificationFailure(
                    f"{label} length is outside the closed bound"
                )
        if len({harness_token, *values}) != 5:
            raise QualificationFailure("all qualification credentials must be distinct")
        return values

    def select(self, roles: Sequence[str]) -> dict[str, str]:
        values = self._asdict()
        return {role: values[role] for role in roles}


@dataclass(frozen=True)
class ProofContract:
    operation: str
    route: str
    credential_roles: tuple[str, ...]
    actor_role: str
    http_status: int
    mutation_created: bool
    minimum_event_ids: int
    minimum_model_ids: int
    alias_count: int
    assertions: tuple[str, ...]


PROOF_CONTRACTS = (
    ProofContract(
        "oauth_session_identity",
        "session/oauth-owner",
        ("oauth_owner",),
        "owner",
        200,
        False,
        0,
        0,
        0,
        (
            "browser_session_signature_verified",
            "exact_identity_verified",
            "session_unexpired",
        ),
    ),
    ProofContract(
        "agent_session_identity",
        "session/agent-owner",
        ("agent_owner",),
        "owner",
        200,
        False,
        0,
        0,
        0,
        (
            "agent_source_commit_bound",
            "browser_session_signature_verified",
            "exact_identity_verified",
        ),
    ),
    ProofContract(
        "owner_request",
        "POST /api/v1/model-identities",
        ("oauth_owner",),
        "owner",
        201,
        True,
        1,
        1,
        0,
        (
            "immutable_event_written",
            "owner_derived_from_session",
            "pending_view_written",
        ),
    ),
    ProofContract(
        "maintainer_approve",
        "POST /api/v1/model-identities/{model_id}/decisions",
        ("maintainer",),
        "maintainer",
        201,
        True,
        1,
        1,
        0,
        (
            "approval_view_written",
            "closed_maintainer_pair_verified",
            "immutable_event_written",
        ),
    ),
    ProofContract(
        "maintainer_reject",
        "POST /api/v1/model-identities/{model_id}/decisions",
        ("maintainer",),
        "maintainer",
        201,
        True,
        2,
        1,
        0,
        (
            "closed_reason_code_verified",
            "immutable_events_written",
            "rejection_view_written",
        ),
    ),
    ProofContract(
        "alias_assignment",
        "POST /api/v1/model-identities/{model_id}/aliases",
        ("agent_owner",),
        "owner",
        201,
        True,
        1,
        1,
        1,
        (
            "alias_reservation_written",
            "immutable_event_written",
            "reverse_impact_updated",
        ),
    ),
    ProofContract(
        "identity_rename",
        "PUT /api/v1/model-identities/{model_id}/name",
        ("oauth_owner",),
        "owner",
        201,
        True,
        1,
        1,
        0,
        ("immutable_event_written", "reverse_impact_updated", "view_renamed"),
    ),
    ProofContract(
        "complete_graph_consolidation",
        "POST /api/v1/model-identities/{model_id}/consolidations",
        ("agent_owner",),
        "owner",
        201,
        True,
        1,
        2,
        0,
        (
            "all_reverse_impacts_retargeted",
            "immutable_event_written",
            "source_component_deleted",
        ),
    ),
    ProofContract(
        "chained_terminal_retry",
        "POST /api/v1/model-identities/{model_id}/consolidations",
        ("agent_owner",),
        "owner",
        200,
        True,
        2,
        3,
        0,
        (
            "later_chain_created",
            "retry_created_no_event",
            "retry_resolved_current_terminal",
        ),
    ),
    ProofContract(
        "component_cap_refusal",
        "POST /api/v1/model-identities/{model_id}/consolidations",
        ("oauth_owner",),
        "owner",
        409,
        False,
        0,
        2,
        0,
        ("candidate_union_count_is_33", "component_cap_is_32", "no_git_object_created"),
    ),
    ProofContract(
        "idempotent_retry",
        "POST /api/v1/model-identities",
        ("oauth_owner",),
        "owner",
        200,
        False,
        1,
        1,
        0,
        ("event_payload_byte_equal", "existing_event_reused", "no_git_object_created"),
    ),
    ProofContract(
        "cross_route_event_collision",
        "PUT /api/v1/model-identities/{model_id}/name",
        ("oauth_owner",),
        "owner",
        409,
        False,
        1,
        1,
        0,
        (
            "event_route_family_mismatch",
            "immutable_event_preserved",
            "no_git_object_created",
        ),
    ),
    ProofContract(
        "cross_owner_denial",
        "PUT /api/v1/model-identities/{model_id}/name",
        ("cross_owner",),
        "cross_owner",
        404,
        False,
        0,
        1,
        0,
        (
            "distinct_signed_owner_verified",
            "no_git_object_created",
            "target_owner_not_disclosed",
        ),
    ),
    ProofContract(
        "maximal_contention_measurement",
        "POST /api/v1/model-identities/{model_id}/consolidations",
        ("agent_owner",),
        "owner",
        201,
        True,
        1,
        2,
        0,
        (
            "eight_cas_attempts_executed",
            "network_subrequests_measured",
            "successful_final_cas",
        ),
    ),
)

CONTRACT_BY_OPERATION = {contract.operation: contract for contract in PROOF_CONTRACTS}

RequestJson = Callable[
    [str, Mapping[str, object] | None, Mapping[str, str]], tuple[int, object]
]


def validate_health(
    status: int, value: object, expected_commit: str
) -> dict[str, object]:
    body = require_fields(value, HEALTH_FIELDS, "staging health response")
    if (
        status != 200
        or body["status"] != "ok"
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
            "staging health did not prove the exact disabled boundary"
        )
    return body


JOURNAL_FIELDS = {
    "current_state_commit",
    "current_state_tree",
    "deployed_commit",
    "environment",
    "foreign_commit_observed",
    "fixture_evidence_class",
    "fixture_id",
    "fixture_manifest_digest",
    "initial_state_commit",
    "initial_state_tree",
    "journal_id",
    "journal_revision",
    "lease_released",
    "lease_status",
    "maintainer_api_enabled",
    "owner_api_enabled",
    "restoration_commit",
    "restoration_fast_forward",
    "restoration_parent_commit",
    "restoration_parent_tree",
    "restoration_tree",
    "restoration_tree_equal",
    "run_attempt",
    "run_id",
    "schema_version",
    "status",
}


def validate_journal(
    status: int,
    value: object,
    *,
    expected_commit: str,
    run_id: str,
    run_attempt: int,
    expected_initial_commit: str | None = None,
    expected_initial_tree: str | None = None,
) -> dict[str, object]:
    body = require_fields(value, JOURNAL_FIELDS, "qualification journal response")
    for field in (
        "current_state_commit",
        "current_state_tree",
        "initial_state_commit",
        "initial_state_tree",
    ):
        if not isinstance(body[field], str) or SHA.fullmatch(body[field]) is None:
            raise QualificationFailure(f"journal {field} was not canonical")
    if (
        status != 200
        or body["schema_version"] != 2
        or body["status"] != "model_identity_qualification_journal"
        or body["environment"] != "staging"
        or body["deployed_commit"] != expected_commit
        or body["run_id"] != run_id
        or body["run_attempt"] != run_attempt
        or not isinstance(body["journal_id"], str)
        or JOURNAL_ID.fullmatch(body["journal_id"]) is None
        or positive_int(body["journal_revision"], "journal revision")
        != body["journal_revision"]
        or body["lease_status"] not in {"active", "restored"}
        or body["owner_api_enabled"] is not False
        or body["maintainer_api_enabled"] is not False
        or body["foreign_commit_observed"] is not False
        or body["fixture_evidence_class"] != "reviewed_live_fixture"
        or not isinstance(body["fixture_id"], str)
        or EVENT_ID.fullmatch(body["fixture_id"]) is None
        or not isinstance(body["fixture_manifest_digest"], str)
        or SHA256.fullmatch(body["fixture_manifest_digest"]) is None
    ):
        raise QualificationFailure("qualification journal boundary was not exact")
    if (
        expected_initial_commit is not None
        and body["initial_state_commit"] != expected_initial_commit
    ):
        raise QualificationFailure("journal initial State commit changed")
    if (
        expected_initial_tree is not None
        and body["initial_state_tree"] != expected_initial_tree
    ):
        raise QualificationFailure("journal initial State tree changed")
    if body["lease_status"] == "active" and (
        body["restoration_commit"] is not None
        or body["restoration_parent_commit"] is not None
        or body["restoration_parent_tree"] is not None
        or body["restoration_tree"] is not None
        or body["restoration_fast_forward"] is not False
        or body["restoration_tree_equal"] is not False
        or body["lease_released"] is not False
    ):
        raise QualificationFailure("active journal exposed restoration evidence")
    if body["lease_status"] == "restored":
        restoration_commit = body["restoration_commit"]
        restoration_parent_commit = body["restoration_parent_commit"]
        restoration_parent_tree = body["restoration_parent_tree"]
        if (
            not isinstance(restoration_commit, str)
            or SHA.fullmatch(restoration_commit) is None
            or not isinstance(restoration_parent_commit, str)
            or SHA.fullmatch(restoration_parent_commit) is None
            or not isinstance(restoration_parent_tree, str)
            or SHA.fullmatch(restoration_parent_tree) is None
        ):
            raise QualificationFailure(
                "restored journal omitted its restoration commit or parent"
            )
        if (
            restoration_commit == restoration_parent_commit
            or body["current_state_commit"] != restoration_commit
        ):
            raise QualificationFailure(
                "restored journal head or parent was not fast-forwarded"
            )
        if (
            body["current_state_tree"] != body["initial_state_tree"]
            or body["restoration_tree"] != body["initial_state_tree"]
            or body["restoration_fast_forward"] is not True
            or body["restoration_tree_equal"] is not True
            or body["lease_released"] is not True
        ):
            raise QualificationFailure(
                "restored journal did not retain exact tree and lease evidence"
            )
    return body


PROOF_FIELDS = {
    "actor",
    "alias_keys",
    "assertions",
    "credential_roles",
    "event_ids",
    "http_status",
    "model_ids",
    "operation",
    "route",
}
STEP_FIELDS = {
    "deployed_commit",
    "journal_id",
    "journal_revision",
    "maintainer_api_enabled",
    "mutation_created",
    "owner_api_enabled",
    "previous_state_commit",
    "previous_state_tree",
    "proof",
    "run_attempt",
    "run_id",
    "schema_version",
    "state_commit",
    "state_tree",
    "status",
    "subrequests",
    "cas_attempts",
}


def _identity_for_role(intent: Intent, role: str) -> Identity:
    return {
        "owner": intent.owner,
        "cross_owner": intent.cross_owner,
        "maintainer": intent.maintainer,
    }[role]


def _canonical_identifiers(
    value: object,
    pattern: re.Pattern[str],
    label: str,
) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and pattern.fullmatch(item) is not None for item in value
    ):
        raise QualificationFailure(f"proof {label} were not canonical")
    if len(set(value)) != len(value):
        raise QualificationFailure(f"proof {label} were not unique")
    return value


def validate_step(
    status: int,
    value: object,
    *,
    contract: ProofContract,
    intent: Intent,
    expected_commit: str,
    run_id: str,
    run_attempt: int,
    journal_id: str,
    expected_revision: int,
    expected_head: str,
    expected_tree: str,
) -> dict[str, object]:
    body = require_fields(value, STEP_FIELDS, f"{contract.operation} response")
    proof = require_fields(body["proof"], PROOF_FIELDS, f"{contract.operation} proof")
    state_commit = body["state_commit"]
    state_tree = body["state_tree"]
    if not isinstance(state_commit, str) or SHA.fullmatch(state_commit) is None:
        raise QualificationFailure(
            f"{contract.operation} State commit was not canonical"
        )
    if not isinstance(state_tree, str) or SHA.fullmatch(state_tree) is None:
        raise QualificationFailure(f"{contract.operation} State tree was not canonical")
    assertions = proof["assertions"]
    if not isinstance(assertions, dict) or assertions != {
        assertion: True for assertion in contract.assertions
    }:
        raise QualificationFailure(f"{contract.operation} assertions were not exact")
    if (
        status != 200
        or body["schema_version"] != 2
        or body["status"] != "model_identity_qualification_step_verified"
        or body["deployed_commit"] != expected_commit
        or body["run_id"] != run_id
        or body["run_attempt"] != run_attempt
        or body["journal_id"] != journal_id
        or body["journal_revision"] != expected_revision + 1
        or body["previous_state_commit"] != expected_head
        or body["previous_state_tree"] != expected_tree
        or body["owner_api_enabled"] is not False
        or body["maintainer_api_enabled"] is not False
        or body["mutation_created"] is not contract.mutation_created
        or proof["operation"] != contract.operation
        or proof["route"] != contract.route
        or proof["credential_roles"] != list(contract.credential_roles)
        or proof["actor"] != _identity_for_role(intent, contract.actor_role).json()
        or proof["http_status"] != contract.http_status
    ):
        raise QualificationFailure(
            f"{contract.operation} did not prove its closed boundary"
        )
    event_ids = _canonical_identifiers(proof["event_ids"], EVENT_ID, "event IDs")
    model_ids = _canonical_identifiers(proof["model_ids"], MODEL_ID, "model IDs")
    alias_keys = _canonical_identifiers(proof["alias_keys"], ALIAS_KEY, "alias keys")
    if (
        len(event_ids) < contract.minimum_event_ids
        or len(model_ids) < contract.minimum_model_ids
        or len(alias_keys) != contract.alias_count
    ):
        raise QualificationFailure(
            f"{contract.operation} identifier evidence was incomplete"
        )
    if contract.mutation_created:
        if state_commit == expected_head or state_tree == expected_tree:
            raise QualificationFailure(
                f"{contract.operation} did not advance State commit and tree"
            )
    elif state_commit != expected_head or state_tree != expected_tree:
        raise QualificationFailure(f"{contract.operation} unexpectedly changed State")
    if contract.operation == "maximal_contention_measurement":
        if body["cas_attempts"] != 8 or (
            not isinstance(body["subrequests"], int)
            or isinstance(body["subrequests"], bool)
            or not 1 <= body["subrequests"] <= MAX_MODEL_IDENTITY_SUBREQUESTS
        ):
            raise QualificationFailure("maximal contention measurement was not exact")
    elif body["cas_attempts"] is not None or body["subrequests"] is not None:
        raise QualificationFailure(
            f"{contract.operation} exposed unexpected measurement fields"
        )
    return body


RESTORE_FIELDS = {
    "deployed_commit",
    "fast_forward",
    "foreign_commit_observed",
    "initial_state_commit",
    "initial_state_tree",
    "journal_id",
    "journal_revision",
    "lease_released",
    "maintainer_api_enabled",
    "owner_api_enabled",
    "ref_head",
    "restoration_commit",
    "restoration_parent_commit",
    "restoration_parent_tree",
    "restoration_tree",
    "run_attempt",
    "run_id",
    "schema_version",
    "status",
    "tree_equal",
}


def validate_restore(
    status: int,
    value: object,
    *,
    journal: Mapping[str, object],
) -> dict[str, object]:
    body = require_fields(value, RESTORE_FIELDS, "qualification restoration response")
    restoration_commit = body["restoration_commit"]
    if (
        status != 200
        or body["schema_version"] != 2
        or body["status"] != "model_identity_qualification_restored"
        or body["deployed_commit"] != journal["deployed_commit"]
        or body["run_id"] != journal["run_id"]
        or body["run_attempt"] != journal["run_attempt"]
        or body["journal_id"] != journal["journal_id"]
        or body["journal_revision"] != journal["journal_revision"] + 1
        or body["initial_state_commit"] != journal["initial_state_commit"]
        or body["initial_state_tree"] != journal["initial_state_tree"]
        or body["restoration_parent_commit"] != journal["current_state_commit"]
        or body["restoration_parent_tree"] != journal["current_state_tree"]
        or not isinstance(restoration_commit, str)
        or SHA.fullmatch(restoration_commit) is None
        or restoration_commit == journal["current_state_commit"]
        or body["restoration_tree"] != journal["initial_state_tree"]
        or body["ref_head"] != restoration_commit
        or body["fast_forward"] is not True
        or body["tree_equal"] is not True
        or body["lease_released"] is not True
        or body["foreign_commit_observed"] is not False
        or body["owner_api_enabled"] is not False
        or body["maintainer_api_enabled"] is not False
    ):
        raise QualificationFailure(
            "qualification restoration was not exact and fast-forwarded"
        )
    return body


class Evidence:
    def __init__(self, path: pathlib.Path, mode: str, run_id: str, run_attempt: int):
        self.path = path
        self.body: dict[str, object] = {
            "errors": [],
            "final_health": None,
            "journal": None,
            "kind": f"model_identity_staging_{mode}_evidence",
            "mode": mode,
            "proofs": [],
            "restoration": None,
            "run_attempt": run_attempt,
            "run_id": run_id,
            "schema_version": 2,
            "status": "started",
        }
        self.write()

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.body, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def error(self, phase: str, error: BaseException) -> None:
        errors = self.body["errors"]
        assert isinstance(errors, list)
        errors.append({"error_class": type(error).__name__, "phase": phase})
        self.write()


def recover_journal(
    request_json: RequestJson,
    *,
    expected_commit: str,
    run_id: str,
    run_attempt: int,
    evidence: Evidence,
    expected_initial_commit: str | None = None,
    expected_initial_tree: str | None = None,
    expected_journal_id: str | None = None,
) -> dict[str, object]:
    status, response = request_json(
        "status",
        {"run_attempt": run_attempt, "run_id": run_id, "schema_version": 2},
        {},
    )
    journal = validate_journal(
        status,
        response,
        expected_commit=expected_commit,
        run_id=run_id,
        run_attempt=run_attempt,
        expected_initial_commit=expected_initial_commit,
        expected_initial_tree=expected_initial_tree,
    )
    if expected_journal_id is not None and journal["journal_id"] != expected_journal_id:
        raise QualificationFailure("qualification journal identity changed")
    evidence.body["journal"] = journal
    evidence.write()
    if journal["lease_status"] == "restored":
        restoration = {
            "journal_id": journal["journal_id"],
            "restoration_commit": journal["restoration_commit"],
            "restoration_fast_forward": journal["restoration_fast_forward"],
            "restoration_parent_commit": journal["restoration_parent_commit"],
            "restoration_parent_tree": journal["restoration_parent_tree"],
            "restoration_tree": journal["restoration_tree"],
            "restoration_tree_equal": journal["restoration_tree_equal"],
            "status": "already_restored",
        }
    else:
        payload = {
            "confirmation": RECOVER_CONFIRMATION,
            "deployed_commit": expected_commit,
            "expected_journal_revision": journal["journal_revision"],
            "expected_state_commit": journal["current_state_commit"],
            "expected_state_tree": journal["current_state_tree"],
            "journal_id": journal["journal_id"],
            "operation": "restore",
            "run_attempt": run_attempt,
            "run_id": run_id,
            "schema_version": 2,
        }
        status, response = request_json("restore", payload, {})
        restoration = validate_restore(status, response, journal=journal)
    evidence.body["restoration"] = restoration
    evidence.write()
    status, health = request_json("health", None, {})
    evidence.body["final_health"] = validate_health(status, health, expected_commit)
    evidence.body["status"] = "restored_and_disabled_health_verified"
    evidence.write()
    return restoration


def run_qualification(
    request_json: RequestJson,
    *,
    expected_commit: str,
    initial_state_commit: str,
    initial_state_tree: str,
    run_id: str,
    run_attempt: int,
    intent: Intent,
    sessions: Sessions,
    evidence: Evidence,
) -> dict[str, object]:
    intent.validate()
    status, health = request_json("health", None, {})
    evidence.body["initial_health"] = validate_health(status, health, expected_commit)
    evidence.body["intent"] = intent.json()
    evidence.write()
    acquire_attempted = False
    expected_journal_id: str | None = None
    primary_error: Exception | None = None
    recovery_error: Exception | None = None
    try:
        acquire_attempted = True
        status, response = request_json(
            "acquire",
            {
                "confirmation": QUALIFY_CONFIRMATION,
                "deployed_commit": expected_commit,
                "initial_state_commit": initial_state_commit,
                "initial_state_tree": initial_state_tree,
                "intent": intent.json(),
                "operation": "acquire",
                "run_attempt": run_attempt,
                "run_id": run_id,
                "schema_version": 2,
            },
            {},
        )
        journal = validate_journal(
            status,
            response,
            expected_commit=expected_commit,
            run_id=run_id,
            run_attempt=run_attempt,
            expected_initial_commit=initial_state_commit,
            expected_initial_tree=initial_state_tree,
        )
        if (
            journal["lease_status"] != "active"
            or journal["current_state_commit"] != initial_state_commit
            or journal["current_state_tree"] != initial_state_tree
        ):
            raise QualificationFailure(
                "new journal did not bind the exact initial State"
            )
        evidence.body["journal"] = journal
        evidence.write()
        expected_journal_id = str(journal["journal_id"])
        current_head = initial_state_commit
        current_tree = initial_state_tree
        revision = int(journal["journal_revision"])
        seen_events: set[str] = set()
        for contract in PROOF_CONTRACTS:
            credentials = sessions.select(contract.credential_roles)
            payload = {
                "confirmation": QUALIFY_CONFIRMATION,
                "deployed_commit": expected_commit,
                "expected_journal_revision": revision,
                "expected_state_commit": current_head,
                "expected_state_tree": current_tree,
                "intent": intent.json(),
                "journal_id": journal["journal_id"],
                "operation": contract.operation,
                "run_attempt": run_attempt,
                "run_id": run_id,
                "schema_version": 2,
            }
            status, response = request_json("step", payload, credentials)
            step = validate_step(
                status,
                response,
                contract=contract,
                intent=intent,
                expected_commit=expected_commit,
                run_id=run_id,
                run_attempt=run_attempt,
                journal_id=str(journal["journal_id"]),
                expected_revision=revision,
                expected_head=current_head,
                expected_tree=current_tree,
            )
            proof = require_object(step["proof"], "step proof")
            event_ids = set(proof["event_ids"])
            if contract.operation not in {
                "idempotent_retry",
                "cross_route_event_collision",
            }:
                if seen_events & event_ids:
                    raise QualificationFailure(
                        f"{contract.operation} reused an unexpected event ID"
                    )
                seen_events.update(event_ids)
            elif not event_ids or not event_ids <= seen_events:
                raise QualificationFailure(
                    f"{contract.operation} did not bind an earlier event ID"
                )
            current_head = str(step["state_commit"])
            current_tree = str(step["state_tree"])
            revision = int(step["journal_revision"])
            proofs = evidence.body["proofs"]
            assert isinstance(proofs, list)
            proofs.append(step)
            evidence.write()
    except Exception as error:  # noqa: BLE001 -- every controllable failure requires recovery
        primary_error = error
        evidence.error("qualification", error)
    finally:
        if acquire_attempted:
            try:
                recover_journal(
                    request_json,
                    expected_commit=expected_commit,
                    run_id=run_id,
                    run_attempt=run_attempt,
                    evidence=evidence,
                    expected_initial_commit=initial_state_commit,
                    expected_initial_tree=initial_state_tree,
                    expected_journal_id=expected_journal_id,
                )
            except Exception as error:  # noqa: BLE001 -- preserve the combined failure class
                recovery_error = error
                evidence.error("restoration_or_final_health", error)
    if primary_error is not None or recovery_error is not None:
        evidence.body["status"] = "failed"
        evidence.write()
        if primary_error is not None and recovery_error is not None:
            raise QualificationFailure(
                "qualification and mandatory recovery both failed"
            ) from recovery_error
        if recovery_error is not None:
            raise QualificationFailure(
                "mandatory recovery or final disabled health verification failed"
            ) from recovery_error
        raise QualificationFailure(
            "qualification failed; restoration and disabled health verification passed"
        ) from primary_error
    evidence.body["status"] = "model_identity_staging_qualification_passed_and_restored"
    evidence.write()
    return evidence.body


def http_client(client: urllib.request.OpenerDirector, token: str) -> RequestJson:
    headers_by_role = {
        "agent_owner": "X-Lean-Eval-Agent-Session",
        "cross_owner": "X-Lean-Eval-Cross-Owner-Session",
        "maintainer": "X-Lean-Eval-Maintainer-Session",
        "oauth_owner": "X-Lean-Eval-OAuth-Session",
    }

    def request_json(
        kind: str,
        payload: Mapping[str, object] | None,
        credentials: Mapping[str, str],
    ) -> tuple[int, object]:
        if kind == "health":
            if payload is not None or credentials:
                raise QualificationFailure("health request carried privileged material")
            url = f"{STAGING_ORIGIN}/healthz"
            data = None
            method = "GET"
            headers = {"User-Agent": "lean-eval-model-identity-qualification/2"}
        else:
            if kind not in {"acquire", "status", "step", "restore"} or payload is None:
                raise QualificationFailure(
                    "qualification HTTP request was not canonical"
                )
            if not set(credentials) <= set(headers_by_role):
                raise QualificationFailure("qualification credential role was unknown")
            if kind != "step" and credentials:
                raise QualificationFailure(
                    f"{kind} request carried a session credential"
                )
            url = f"{STAGING_ORIGIN}{QUALIFICATION_PATH}"
            data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            method = "POST"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "lean-eval-model-identity-qualification/2",
                **{headers_by_role[role]: value for role, value in credentials.items()},
            }
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with client.open(request, timeout=90) as response:
                status = response.status
                encoded = response.read(32 * 1024 + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            encoded = error.read(32 * 1024 + 1)
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
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode, confirmation in (
        ("qualify", QUALIFY_CONFIRMATION),
        ("recover", RECOVER_CONFIRMATION),
    ):
        subparser = subparsers.add_parser(mode)
        subparser.add_argument("--expected-commit", required=True)
        subparser.add_argument("--run-id", required=True)
        subparser.add_argument("--run-attempt", required=True, type=int)
        subparser.add_argument("--evidence", required=True, type=pathlib.Path)
        subparser.add_argument("--confirm", required=True, choices=(confirmation,))
        if mode == "qualify":
            subparser.add_argument("--initial-state-commit", required=True)
            subparser.add_argument("--initial-state-tree", required=True)
            subparser.add_argument("--owner", required=True)
            subparser.add_argument("--cross-owner", required=True)
            subparser.add_argument("--maintainer", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    evidence = Evidence(args.evidence, args.mode, args.run_id, args.run_attempt)
    try:
        if SHA.fullmatch(args.expected_commit) is None:
            raise QualificationFailure("expected deployment commit was not canonical")
        if RUN_ID.fullmatch(args.run_id) is None:
            raise QualificationFailure("workflow run ID was not canonical")
        if args.run_attempt != 1:
            raise QualificationFailure(
                "qualification and recovery require the first run attempt"
            )
        token = os.environ.get("MODEL_IDENTITY_QUALIFICATION_TOKEN", "")
        if not 32 <= len(token.encode()) <= 4096:
            raise QualificationFailure(
                "qualification token length is outside the closed bound"
            )
        request_json = http_client(opener(), token)
        if args.mode == "recover":
            recover_journal(
                request_json,
                expected_commit=args.expected_commit,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                evidence=evidence,
            )
            return 0
        if (
            SHA.fullmatch(args.initial_state_commit) is None
            or SHA.fullmatch(args.initial_state_tree) is None
        ):
            raise QualificationFailure("initial State commit or tree was not canonical")
        intent = Intent(
            owner=Identity.parse(args.owner, "owner"),
            cross_owner=Identity.parse(args.cross_owner, "cross-owner"),
            maintainer=Identity.parse(args.maintainer, "maintainer"),
        )
        sessions = Sessions.from_environment(token)
        run_qualification(
            request_json,
            expected_commit=args.expected_commit,
            initial_state_commit=args.initial_state_commit,
            initial_state_tree=args.initial_state_tree,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            intent=intent,
            sessions=sessions,
            evidence=evidence,
        )
        return 0
    except Exception as error:
        errors = evidence.body["errors"]
        assert isinstance(errors, list)
        if not errors:
            evidence.error("controller", error)
        evidence.body["status"] = "failed"
        evidence.write()
        raise


if __name__ == "__main__":
    try:
        sys.exit(main())
    except QualificationFailure as error:
        print(f"model identity staging qualification failed: {error}", file=sys.stderr)
        sys.exit(1)
