#!/usr/bin/env python3
"""Prepare and finalize reviewed historical-public unavailability evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from typing import Any

from aggregate_public_replay_github_evidence import (
    AggregationError,
    canonical_document_bytes,
    validate_aggregate,
)
from inventory_historical_replay import InventoryError, inventory
from prepare_public_replay_plan import PublicReplayPlanError, _selected_records
from prepare_public_replay_resolution import ResolutionError, prepare
from resolve_public_replay_github_evidence import (
    _read_bounded,
    _write_exclusive,
    validate_legacy_adjudication_registry,
    validate_workflow_registry,
)
from results_schema import result_id

DIGEST = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
REQUEST_ID = re.compile(r"prr_[0-9a-f]{64}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
MAX_INVENTORY_BYTES = 16 * 1024 * 1024
MAX_AGGREGATE_BYTES = 32 * 1024 * 1024
MAX_REGISTRY_BYTES = 512 * 1024
MAX_REVIEW_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_SHARD_BYTES = 450 * 1000
SHARD_CANDIDATE_COUNT = 32
PERMANENT_REASON = "source_ref_permanently_unavailable"
RATIONALE = "accepted_immutable_source_ref_unavailable_without_archive"


class UnavailabilityError(ValueError):
    """Public source evidence cannot become a reviewed disposition."""


def _canonical_input(
    path: pathlib.Path, limit: int, label: str
) -> tuple[dict[str, Any], bytes]:
    raw = _read_bounded(path, limit, label)
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise UnavailabilityError(f"{label} is not readable JSON") from error
    if not isinstance(value, dict) or canonical_document_bytes(value) != raw:
        raise UnavailabilityError(f"{label} is not canonical JSON")
    return value, raw


def _compact_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_document_bytes(value)).hexdigest()


def _require_canonical_pair(value: Any, raw: bytes, label: str) -> None:
    if not isinstance(value, dict) or canonical_document_bytes(value) != raw:
        raise UnavailabilityError(f"{label} value and canonical bytes differ")


def build_candidates(
    *,
    inventory_value: dict[str, Any],
    inventory_raw: bytes,
    requests_value: dict[str, Any],
    requests_raw: bytes,
    aggregate: dict[str, Any],
    aggregate_raw: bytes,
    workflow_registry: dict[str, Any],
    workflow_registry_raw: bytes,
    legacy_registry: dict[str, Any],
    legacy_registry_raw: bytes,
    results_root: pathlib.Path,
) -> dict[str, Any]:
    for value, raw, label in (
        (inventory_value, inventory_raw, "inventory"),
        (requests_value, requests_raw, "resolution requests"),
        (aggregate, aggregate_raw, "aggregate"),
        (workflow_registry, workflow_registry_raw, "workflow registry"),
        (legacy_registry, legacy_registry_raw, "legacy adjudication registry"),
    ):
        _require_canonical_pair(value, raw, label)
    inventory_sha256 = hashlib.sha256(inventory_raw).hexdigest()
    aggregate_sha256 = hashlib.sha256(aggregate_raw).hexdigest()
    workflow_sha256 = hashlib.sha256(workflow_registry_raw).hexdigest()
    legacy_sha256 = hashlib.sha256(legacy_registry_raw).hexdigest()
    try:
        recomputed_inventory = inventory(results_root, aggregate["source_commit"])
    except (InventoryError, KeyError) as error:
        raise UnavailabilityError(str(error)) from error
    if recomputed_inventory != inventory_value:
        raise UnavailabilityError("inventory does not equal the exact results snapshot")
    try:
        recomputed_requests = prepare(inventory_value, inventory_sha256, results_root)
    except ResolutionError as error:
        raise UnavailabilityError(str(error)) from error
    if recomputed_requests != requests_value:
        raise UnavailabilityError(
            "resolution requests do not equal the exact results snapshot"
        )
    requests = requests_value
    requests_sha256 = hashlib.sha256(requests_raw).hexdigest()
    try:
        validate_workflow_registry(workflow_registry)
        validate_legacy_adjudication_registry(legacy_registry)
        validate_aggregate(
            aggregate,
            requests,
            requests_sha256,
            workflow_registry,
            workflow_sha256,
            legacy_registry,
            legacy_sha256,
        )
    except (AggregationError, ValueError) as error:
        raise UnavailabilityError(str(error)) from error
    if (
        aggregate_sha256
        != hashlib.sha256(canonical_document_bytes(aggregate)).hexdigest()
    ):
        raise UnavailabilityError(
            "aggregate is not digest-bound to its canonical bytes"
        )
    if aggregate["inventory_sha256"] != inventory_sha256:
        raise UnavailabilityError("aggregate inventory digest differs")
    if any(
        aggregate[field] != 0
        for field in (
            "source_indeterminate_count",
            "probe_indeterminate_count",
            "timing_indeterminate_count",
            "workflow_contract_unreviewed_count",
            "ambiguous_count",
            "evidence_missing_count",
        )
    ):
        raise UnavailabilityError(
            "aggregate still contains non-reviewable pending classes"
        )

    request_by_id = {request["request_id"]: request for request in requests["requests"]}
    unavailable_ids = {
        resolution["request_id"]
        for resolution in aggregate["resolutions"]
        if resolution["status"] == "source_unavailable"
    }
    try:
        records = _selected_records(results_root, requests["requests"], unavailable_ids)
    except PublicReplayPlanError as error:
        raise UnavailabilityError(str(error)) from error
    candidates: list[dict[str, Any]] = []
    for resolution in aggregate["resolutions"]:
        if resolution["status"] != "source_unavailable":
            continue
        request = request_by_id[resolution["request_id"]]
        matched = [
            item
            for item in resolution["candidates"]
            if item.get("status") == "matched_source_unavailable"
        ]
        if len(matched) != 1 or resolution.get("selected_issue_repository") != matched[
            0
        ].get("issue_repository"):
            raise UnavailabilityError(
                f"{resolution['request_id']} lacks one selected unavailable source"
            )
        selected_evidence = matched[0]
        owner_login = request["owner"].lower()
        output_results = []
        for expected in request["results"]:
            selected = records[expected["result_id"]]
            record = selected["record"]
            if (
                expected["owner"].lower() != owner_login
                or result_id(
                    owner_login,
                    request["declared_model"],
                    expected["problem_id"],
                    expected["statement_revision"],
                )
                != expected["result_id"]
                or record["problem_id"] != expected["problem_id"]
                or record["statement_revision"] != expected["statement_revision"]
                or record["declared_model"] != request["declared_model"]
                or record["benchmark_commit"] != request["benchmark"]["commit"]
                or record["submission"]
                != {
                    "kind": request["source"]["kind"],
                    "repo": request["source"]["repository"],
                    "ref": request["source"]["commit"],
                    "public": True,
                }
            ):
                raise UnavailabilityError(
                    "unavailable result differs from its request: "
                    f"{expected['result_id']}"
                )
            output_results.append(
                {
                    "result_id": expected["result_id"],
                    "owner_login": owner_login,
                    "problem_id": expected["problem_id"],
                    "statement_revision": expected["statement_revision"],
                    "results_repository": aggregate["source_repository"],
                    "results_commit": aggregate["source_commit"],
                    **selected["binding"],
                }
            )
        candidate = {
            "request_id": request["request_id"],
            "historical_accepted_at": request["accepted_at"],
            "owner_login": owner_login,
            "declared_model": request["declared_model"],
            "issue": {
                "repository": resolution["selected_issue_repository"],
                "number": request["issue_number"],
                "identity_sha256": selected_evidence["issue_identity_sha256"],
            },
            "historical_evaluation": {
                "workflow_contract": selected_evidence["workflow_contract"],
                "workflow_repository_commit": selected_evidence[
                    "workflow_repository_commit"
                ],
                "workflow_definition_sha256": selected_evidence[
                    "workflow_definition_sha256"
                ],
                "workflow_run_id": selected_evidence["workflow_run_id"],
                "workflow_run_attempt": selected_evidence["workflow_run_attempt"],
                "workflow_run_identity_sha256": selected_evidence[
                    "workflow_run_identity_sha256"
                ],
            },
            "source": request["source"],
            "benchmark": request["benchmark"],
            "github_resolution_sha256": _digest(resolution),
            "issue_candidates": [
                {
                    "repository": item["issue_repository"],
                    "status": item["status"],
                }
                for item in resolution["candidates"]
            ],
            "results": output_results,
            "proposed_reason_code": PERMANENT_REASON,
            "proposed_rationale_code": RATIONALE,
            "review_status": "pending",
        }
        candidate["candidate_sha256"] = _digest(candidate)
        candidates.append(candidate)
    candidates.sort(key=lambda item: item["request_id"])
    result_count = sum(len(item["results"]) for item in candidates)
    if (
        len(candidates) != aggregate["source_unavailable_count"]
        or len(candidates) != aggregate["pending_count"]
        or len(candidates) + aggregate["resolved_count"] != aggregate["request_count"]
    ):
        raise UnavailabilityError("candidate coverage differs from aggregate counters")
    return {
        "schema_version": 1,
        "kind": "historical_public_replay_unavailability_review_candidates",
        "source_repository": aggregate["source_repository"],
        "source_commit": aggregate["source_commit"],
        "inventory_sha256": inventory_sha256,
        "resolution_requests_sha256": requests_sha256,
        "github_evidence_aggregate_sha256": aggregate_sha256,
        "workflow_definition_registry_sha256": workflow_sha256,
        "legacy_adjudication_registry_sha256": legacy_sha256,
        "candidate_request_count": len(candidates),
        "candidate_result_count": result_count,
        "review_status": "required",
        "claims": {
            "permanent_unavailability_decided": False,
            "state_append_authorized": False,
            "replay_executed": False,
            "corpus_complete": False,
        },
        "candidates": candidates,
    }


def build_candidate_bundle(**arguments: Any) -> tuple[dict[str, Any], dict[str, bytes]]:
    candidate_document = build_candidates(**arguments)
    identity = {
        key: value for key, value in candidate_document.items() if key != "candidates"
    }
    identity_sha256 = _digest(identity)
    candidates = candidate_document["candidates"]
    chunks = [
        candidates[index : index + SHARD_CANDIDATE_COUNT]
        for index in range(0, len(candidates), SHARD_CANDIDATE_COUNT)
    ]
    shard_count = len(chunks)
    shard_bytes: dict[str, bytes] = {}
    descriptors: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        shard = {
            "schema_version": 1,
            "kind": "historical_public_replay_unavailability_candidate_shard",
            "candidate_identity_sha256": identity_sha256,
            "shard_index": index,
            "shard_count": shard_count,
            "request_count": len(chunk),
            "result_count": sum(len(item["results"]) for item in chunk),
            "first_request_id": chunk[0]["request_id"],
            "last_request_id": chunk[-1]["request_id"],
            "candidates": chunk,
        }
        raw = _compact_bytes(shard)
        if len(raw) > MAX_SHARD_BYTES:
            raise UnavailabilityError(
                f"candidate shard {index} exceeds the portable review size limit"
            )
        digest = hashlib.sha256(raw).hexdigest()
        shard_bytes[digest] = raw
        descriptors.append(
            {
                "shard_index": index,
                "sha256": digest,
                "byte_count": len(raw),
                "request_count": shard["request_count"],
                "result_count": shard["result_count"],
                "first_request_id": shard["first_request_id"],
                "last_request_id": shard["last_request_id"],
            }
        )
    manifest = {
        **identity,
        "kind": "historical_public_replay_unavailability_candidate_manifest",
        "candidate_identity_sha256": identity_sha256,
        "shard_count": shard_count,
        "shards": descriptors,
    }
    if len(canonical_document_bytes(manifest)) > MAX_MANIFEST_BYTES:
        raise UnavailabilityError("candidate manifest exceeds the size limit")
    return manifest, shard_bytes


def validate_candidates(candidate_value: dict[str, Any]) -> list[dict[str, Any]]:
    expected_candidate_fields = {
        "schema_version",
        "kind",
        "source_repository",
        "source_commit",
        "inventory_sha256",
        "resolution_requests_sha256",
        "github_evidence_aggregate_sha256",
        "workflow_definition_registry_sha256",
        "legacy_adjudication_registry_sha256",
        "candidate_request_count",
        "candidate_result_count",
        "review_status",
        "claims",
        "candidates",
    }
    if set(candidate_value) != expected_candidate_fields:
        raise UnavailabilityError("candidate fields are not closed")
    if (
        type(candidate_value["schema_version"]) is not int
        or candidate_value["schema_version"] != 1
        or candidate_value["kind"]
        != "historical_public_replay_unavailability_review_candidates"
        or candidate_value["source_repository"] != "leanprover/lean-eval-submissions"
        or not isinstance(candidate_value["source_commit"], str)
        or COMMIT.fullmatch(candidate_value["source_commit"]) is None
        or candidate_value["review_status"] != "required"
        or candidate_value["claims"]
        != {
            "permanent_unavailability_decided": False,
            "state_append_authorized": False,
            "replay_executed": False,
            "corpus_complete": False,
        }
    ):
        raise UnavailabilityError("candidate identity is invalid")
    for field in (
        "inventory_sha256",
        "resolution_requests_sha256",
        "github_evidence_aggregate_sha256",
        "workflow_definition_registry_sha256",
        "legacy_adjudication_registry_sha256",
    ):
        if (
            not isinstance(candidate_value[field], str)
            or DIGEST.fullmatch(candidate_value[field]) is None
        ):
            raise UnavailabilityError("candidate identity digest is invalid")
    candidates = candidate_value["candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise UnavailabilityError("candidate list is invalid")
    expected_item_fields = {
        "request_id",
        "historical_accepted_at",
        "owner_login",
        "declared_model",
        "issue",
        "historical_evaluation",
        "source",
        "benchmark",
        "github_resolution_sha256",
        "issue_candidates",
        "results",
        "proposed_reason_code",
        "proposed_rationale_code",
        "review_status",
        "candidate_sha256",
    }
    previous = ""
    result_count = 0
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != expected_item_fields:
            raise UnavailabilityError("candidate entry fields are not closed")
        request_id = candidate["request_id"]
        if (
            not isinstance(request_id, str)
            or REQUEST_ID.fullmatch(request_id) is None
            or request_id <= previous
        ):
            raise UnavailabilityError("candidates are not uniquely sorted")
        source = candidate["source"]
        benchmark = candidate["benchmark"]
        issue = candidate["issue"]
        evaluation = candidate["historical_evaluation"]
        resolution_sha256 = candidate["github_resolution_sha256"]
        issue_candidates = candidate["issue_candidates"]
        results = candidate["results"]
        if (
            not isinstance(source, dict)
            or set(source) != {"kind", "repository", "commit", "visibility"}
            or source["kind"] not in {"github_repo", "gist"}
            or not isinstance(source["repository"], str)
            or not isinstance(source["commit"], str)
            or COMMIT.fullmatch(source["commit"]) is None
            or source["visibility"] != "public"
            or not isinstance(benchmark, dict)
            or set(benchmark) != {"repository", "commit"}
            or benchmark["repository"] != "leanprover/lean-eval"
            or not isinstance(benchmark["commit"], str)
            or COMMIT.fullmatch(benchmark["commit"]) is None
            or not isinstance(candidate["historical_accepted_at"], str)
            or not isinstance(candidate["owner_login"], str)
            or not candidate["owner_login"]
            or candidate["owner_login"] != candidate["owner_login"].lower()
            or not isinstance(candidate["declared_model"], str)
            or not candidate["declared_model"]
            or not isinstance(issue, dict)
            or set(issue) != {"repository", "number", "identity_sha256"}
            or issue["repository"]
            not in {"leanprover/lean-eval", "leanprover/lean-eval-submissions"}
            or type(issue["number"]) is not int
            or issue["number"] < 1
            or not isinstance(issue["identity_sha256"], str)
            or DIGEST.fullmatch(issue["identity_sha256"]) is None
            or not isinstance(evaluation, dict)
            or set(evaluation)
            != {
                "workflow_contract",
                "workflow_repository_commit",
                "workflow_definition_sha256",
                "workflow_run_id",
                "workflow_run_attempt",
                "workflow_run_identity_sha256",
            }
            or not isinstance(evaluation["workflow_contract"], str)
            or not isinstance(evaluation["workflow_repository_commit"], str)
            or COMMIT.fullmatch(evaluation["workflow_repository_commit"]) is None
            or not isinstance(evaluation["workflow_definition_sha256"], str)
            or DIGEST.fullmatch(evaluation["workflow_definition_sha256"]) is None
            or type(evaluation["workflow_run_id"]) is not int
            or evaluation["workflow_run_id"] < 1
            or type(evaluation["workflow_run_attempt"]) is not int
            or evaluation["workflow_run_attempt"] < 1
            or not isinstance(evaluation["workflow_run_identity_sha256"], str)
            or DIGEST.fullmatch(evaluation["workflow_run_identity_sha256"]) is None
            or not isinstance(resolution_sha256, str)
            or DIGEST.fullmatch(resolution_sha256) is None
            or not isinstance(issue_candidates, list)
            or len(issue_candidates) != 2
            or not all(
                isinstance(item, dict)
                and set(item) == {"repository", "status"}
                and item["repository"]
                in {"leanprover/lean-eval", "leanprover/lean-eval-submissions"}
                for item in issue_candidates
            )
            or [item["repository"] for item in issue_candidates]
            != ["leanprover/lean-eval", "leanprover/lean-eval-submissions"]
            or len(
                [
                    item
                    for item in issue_candidates
                    if item["status"] == "matched_source_unavailable"
                    and item["repository"] == issue["repository"]
                ]
            )
            != 1
            or candidate["proposed_reason_code"] != PERMANENT_REASON
            or candidate["proposed_rationale_code"] != RATIONALE
            or candidate["review_status"] != "pending"
            or not isinstance(results, list)
            or not results
        ):
            raise UnavailabilityError(f"{request_id} candidate binding is invalid")
        previous_result = ""
        for result in results:
            if not isinstance(result, dict) or set(result) != {
                "result_id",
                "owner_login",
                "problem_id",
                "statement_revision",
                "results_repository",
                "results_commit",
                "results_path",
                "result_file_sha256",
                "result_tree_digest",
            }:
                raise UnavailabilityError(f"{request_id} result fields are not closed")
            result_id_value = result["result_id"]
            if (
                not isinstance(result_id_value, str)
                or RESULT_ID.fullmatch(result_id_value) is None
                or result_id_value <= previous_result
                or result["owner_login"] != candidate["owner_login"]
                or not isinstance(result["problem_id"], str)
                or not result["problem_id"]
                or type(result["statement_revision"]) is not int
                or result["statement_revision"] < 1
                or result["results_repository"] != "leanprover/lean-eval-submissions"
                or result["results_commit"] != candidate_value["source_commit"]
                or not isinstance(result["results_path"], str)
                or not result["results_path"].startswith("results/")
                or not isinstance(result["result_file_sha256"], str)
                or DIGEST.fullmatch(result["result_file_sha256"]) is None
                or not isinstance(result["result_tree_digest"], str)
                or DIGEST.fullmatch(result["result_tree_digest"]) is None
                or result_id(
                    candidate["owner_login"],
                    candidate["declared_model"],
                    result["problem_id"],
                    result["statement_revision"],
                )
                != result_id_value
            ):
                raise UnavailabilityError(f"{request_id} results are invalid")
            previous_result = result_id_value
        candidate_without_digest = {
            key: value for key, value in candidate.items() if key != "candidate_sha256"
        }
        if candidate["candidate_sha256"] != _digest(candidate_without_digest):
            raise UnavailabilityError(f"{request_id} candidate digest is invalid")
        result_count += len(results)
        previous = request_id
    if (
        type(candidate_value["candidate_request_count"]) is not int
        or candidate_value["candidate_request_count"] != len(candidates)
        or type(candidate_value["candidate_result_count"]) is not int
        or candidate_value["candidate_result_count"] != result_count
    ):
        raise UnavailabilityError("candidate counters are inconsistent")
    return candidates


def _candidate_document_from_manifest(
    manifest: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    omitted = {"candidate_identity_sha256", "shard_count", "shards"}
    return {
        **{key: value for key, value in manifest.items() if key not in omitted},
        "kind": "historical_public_replay_unavailability_review_candidates",
        "candidates": candidates,
    }


def validate_candidate_bundle(
    manifest: dict[str, Any], shard_bytes: dict[str, bytes]
) -> list[dict[str, Any]]:
    expected_manifest_fields = {
        "schema_version",
        "kind",
        "source_repository",
        "source_commit",
        "inventory_sha256",
        "resolution_requests_sha256",
        "github_evidence_aggregate_sha256",
        "workflow_definition_registry_sha256",
        "legacy_adjudication_registry_sha256",
        "candidate_request_count",
        "candidate_result_count",
        "review_status",
        "claims",
        "candidate_identity_sha256",
        "shard_count",
        "shards",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_manifest_fields:
        raise UnavailabilityError("candidate manifest fields are not closed")
    descriptors = manifest["shards"]
    if (
        manifest["kind"] != "historical_public_replay_unavailability_candidate_manifest"
        or not isinstance(manifest["candidate_identity_sha256"], str)
        or DIGEST.fullmatch(manifest["candidate_identity_sha256"]) is None
        or type(manifest["shard_count"]) is not int
        or manifest["shard_count"] < 1
        or not isinstance(descriptors, list)
        or len(descriptors) != manifest["shard_count"]
    ):
        raise UnavailabilityError("candidate manifest identity is invalid")
    expected_digests: set[str] = set()
    candidates: list[dict[str, Any]] = []
    descriptor_fields = {
        "shard_index",
        "sha256",
        "byte_count",
        "request_count",
        "result_count",
        "first_request_id",
        "last_request_id",
    }
    for index, descriptor in enumerate(descriptors):
        if not isinstance(descriptor, dict) or set(descriptor) != descriptor_fields:
            raise UnavailabilityError(
                "candidate shard descriptor fields are not closed"
            )
        digest = descriptor["sha256"]
        if (
            descriptor["shard_index"] != index
            or not isinstance(digest, str)
            or DIGEST.fullmatch(digest) is None
            or digest in expected_digests
            or type(descriptor["byte_count"]) is not int
            or not 0 < descriptor["byte_count"] <= MAX_SHARD_BYTES
        ):
            raise UnavailabilityError("candidate shard descriptor is invalid")
        expected_digests.add(digest)
        raw = shard_bytes.get(digest)
        if (
            raw is None
            or len(raw) != descriptor["byte_count"]
            or hashlib.sha256(raw).hexdigest() != digest
        ):
            raise UnavailabilityError("candidate shard bytes differ from manifest")
        try:
            shard = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise UnavailabilityError("candidate shard is not readable JSON") from error
        if not isinstance(shard, dict) or _compact_bytes(shard) != raw:
            raise UnavailabilityError("candidate shard is not canonical compact JSON")
        shard_fields = {
            "schema_version",
            "kind",
            "candidate_identity_sha256",
            "shard_index",
            "shard_count",
            "request_count",
            "result_count",
            "first_request_id",
            "last_request_id",
            "candidates",
        }
        chunk = shard.get("candidates")
        if (
            set(shard) != shard_fields
            or shard["schema_version"] != 1
            or shard["kind"]
            != "historical_public_replay_unavailability_candidate_shard"
            or shard["candidate_identity_sha256"]
            != manifest["candidate_identity_sha256"]
            or shard["shard_index"] != index
            or shard["shard_count"] != manifest["shard_count"]
            or not isinstance(chunk, list)
            or not chunk
            or len(chunk) > SHARD_CANDIDATE_COUNT
            or not all(isinstance(item, dict) for item in chunk)
            or shard["request_count"] != len(chunk)
            or shard["result_count"]
            != sum(len(item.get("results", [])) for item in chunk)
            or shard["first_request_id"] != chunk[0].get("request_id")
            or shard["last_request_id"] != chunk[-1].get("request_id")
            or any(
                shard[field] != descriptor[field]
                for field in descriptor_fields - {"sha256", "byte_count"}
            )
        ):
            raise UnavailabilityError("candidate shard does not match its descriptor")
        candidates.extend(chunk)
    if set(shard_bytes) != expected_digests:
        raise UnavailabilityError("candidate shard directory contains unbound files")
    document = _candidate_document_from_manifest(manifest, candidates)
    identity = {key: value for key, value in document.items() if key != "candidates"}
    if _digest(identity) != manifest["candidate_identity_sha256"]:
        raise UnavailabilityError("candidate manifest identity digest is invalid")
    return validate_candidates(document)


def finalize(
    *,
    manifest: dict[str, Any],
    manifest_raw: bytes,
    shard_bytes: dict[str, bytes],
    review_value: dict[str, Any],
    trusted_arguments: dict[str, Any],
) -> dict[str, Any]:
    if canonical_document_bytes(manifest) != manifest_raw:
        raise UnavailabilityError("candidate manifest is not canonical JSON")
    candidates = validate_candidate_bundle(manifest, shard_bytes)
    expected_manifest, expected_shards = build_candidate_bundle(**trusted_arguments)
    if canonical_document_bytes(expected_manifest) != manifest_raw:
        raise UnavailabilityError(
            "candidate manifest differs from trusted frozen inputs"
        )
    if expected_shards != shard_bytes:
        raise UnavailabilityError("candidate shards differ from trusted frozen inputs")
    candidate_digest = hashlib.sha256(manifest_raw).hexdigest()
    expected_review_fields = {
        "schema_version",
        "kind",
        "candidate_manifest_sha256",
        "reviews",
    }
    if set(review_value) != expected_review_fields or (
        review_value.get("schema_version") != 1
        or review_value.get("kind") != "historical_public_replay_unavailability_reviews"
        or review_value.get("candidate_manifest_sha256") != candidate_digest
        or not isinstance(review_value.get("reviews"), list)
    ):
        raise UnavailabilityError("review registry identity is invalid")
    reviews = review_value["reviews"]
    if not isinstance(candidates, list) or len(reviews) != len(candidates):
        raise UnavailabilityError("reviews do not cover every candidate")
    candidate_by_id = {item["request_id"]: item for item in candidates}
    dispositions: list[dict[str, Any]] = []
    previous = ""
    for review in reviews:
        if not isinstance(review, dict) or set(review) != {
            "request_id",
            "candidate_sha256",
            "decision",
            "reason_code",
            "rationale_code",
        }:
            raise UnavailabilityError("review fields are not closed")
        request_id = review["request_id"]
        if (
            not isinstance(request_id, str)
            or REQUEST_ID.fullmatch(request_id) is None
            or request_id <= previous
            or request_id not in candidate_by_id
        ):
            raise UnavailabilityError("reviews are not uniquely sorted and complete")
        candidate = candidate_by_id[request_id]
        if review["candidate_sha256"] != candidate["candidate_sha256"]:
            raise UnavailabilityError(f"{request_id} review candidate digest differs")
        if review["decision"] == "defer":
            if (
                review["reason_code"] is not None
                or review["rationale_code"] is not None
            ):
                raise UnavailabilityError(
                    "deferred review cannot claim a terminal reason"
                )
        elif review["decision"] == "permanently_unavailable":
            if (
                review["reason_code"] != PERMANENT_REASON
                or review["rationale_code"] != RATIONALE
            ):
                raise UnavailabilityError("terminal review reason is not registered")
            dispositions.append(
                {
                    "request_id": request_id,
                    "candidate_sha256": candidate["candidate_sha256"],
                    "reason_code": PERMANENT_REASON,
                    "rationale_code": RATIONALE,
                    "result_ids": [
                        result["result_id"] for result in candidate["results"]
                    ],
                }
            )
        else:
            raise UnavailabilityError("review decision is not registered")
        previous = request_id
    if set(candidate_by_id) != {review["request_id"] for review in reviews}:
        raise UnavailabilityError("review coverage differs from candidates")
    permanent_results = sum(len(item["result_ids"]) for item in dispositions)
    complete = len(dispositions) == len(candidates)
    output = {
        "schema_version": 1,
        "kind": "historical_public_replay_unavailability_dispositions",
        "candidate_manifest_sha256": candidate_digest,
        "review_registry_sha256": _digest(review_value),
        "request_count": len(dispositions),
        "result_count": permanent_results,
        "deferred_request_count": len(candidates) - len(dispositions),
        "review_status": "complete" if complete else "incomplete",
        "activation_status": "blocked_on_state_contract_and_append_authorization",
        "claims": {
            "state_append_authorized": False,
            "replay_executed": False,
            "unavailability_review_complete": complete,
            "corpus_complete": False,
        },
        "dispositions": dispositions,
    }
    validate_dispositions(output)
    return output


def validate_dispositions(value: Any) -> list[dict[str, Any]]:
    fields = {
        "schema_version",
        "kind",
        "candidate_manifest_sha256",
        "review_registry_sha256",
        "request_count",
        "result_count",
        "deferred_request_count",
        "review_status",
        "activation_status",
        "claims",
        "dispositions",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise UnavailabilityError("disposition fields are not closed")
    claims = value["claims"]
    dispositions = value["dispositions"]
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "historical_public_replay_unavailability_dispositions"
        or not isinstance(value["candidate_manifest_sha256"], str)
        or DIGEST.fullmatch(value["candidate_manifest_sha256"]) is None
        or not isinstance(value["review_registry_sha256"], str)
        or DIGEST.fullmatch(value["review_registry_sha256"]) is None
        or type(value["request_count"]) is not int
        or value["request_count"] < 0
        or type(value["result_count"]) is not int
        or value["result_count"] < 0
        or type(value["deferred_request_count"]) is not int
        or value["deferred_request_count"] < 0
        or value["review_status"] not in {"complete", "incomplete"}
        or value["activation_status"]
        != "blocked_on_state_contract_and_append_authorization"
        or claims
        not in (
            {
                "state_append_authorized": False,
                "replay_executed": False,
                "unavailability_review_complete": True,
                "corpus_complete": False,
            },
            {
                "state_append_authorized": False,
                "replay_executed": False,
                "unavailability_review_complete": False,
                "corpus_complete": False,
            },
        )
        or not isinstance(dispositions, list)
    ):
        raise UnavailabilityError("disposition identity is invalid")
    complete = value["deferred_request_count"] == 0
    if (
        (value["review_status"] == "complete") != complete
        or claims["unavailability_review_complete"] != complete
        or value["request_count"] != len(dispositions)
    ):
        raise UnavailabilityError("disposition review counters are inconsistent")
    previous_request = ""
    result_count = 0
    all_results: set[str] = set()
    for disposition in dispositions:
        item_fields = {
            "request_id",
            "candidate_sha256",
            "reason_code",
            "rationale_code",
            "result_ids",
        }
        if not isinstance(disposition, dict) or set(disposition) != item_fields:
            raise UnavailabilityError("disposition entry fields are not closed")
        request_id = disposition["request_id"]
        result_ids = disposition["result_ids"]
        if (
            not isinstance(request_id, str)
            or REQUEST_ID.fullmatch(request_id) is None
            or request_id <= previous_request
            or not isinstance(disposition["candidate_sha256"], str)
            or DIGEST.fullmatch(disposition["candidate_sha256"]) is None
            or disposition["reason_code"] != PERMANENT_REASON
            or disposition["rationale_code"] != RATIONALE
            or not isinstance(result_ids, list)
            or not result_ids
            or result_ids != sorted(result_ids)
            or len(set(result_ids)) != len(result_ids)
            or not all(
                isinstance(result_id_value, str)
                and RESULT_ID.fullmatch(result_id_value) is not None
                for result_id_value in result_ids
            )
            or all_results.intersection(result_ids)
        ):
            raise UnavailabilityError("disposition entry is invalid")
        all_results.update(result_ids)
        result_count += len(result_ids)
        previous_request = request_id
    if value["result_count"] != result_count:
        raise UnavailabilityError("disposition result counter is inconsistent")
    return dispositions


def _trusted_arguments(args: argparse.Namespace) -> dict[str, Any]:
    inventory_value, inventory_raw = _canonical_input(
        args.inventory, MAX_INVENTORY_BYTES, "inventory"
    )
    requests_value, requests_raw = _canonical_input(
        args.resolution_requests, MAX_INVENTORY_BYTES, "resolution requests"
    )
    aggregate, aggregate_raw = _canonical_input(
        args.aggregate, MAX_AGGREGATE_BYTES, "aggregate"
    )
    workflow, workflow_raw = _canonical_input(
        args.workflow_registry, MAX_REGISTRY_BYTES, "workflow registry"
    )
    legacy, legacy_raw = _canonical_input(
        args.legacy_adjudication_registry,
        MAX_REGISTRY_BYTES,
        "legacy adjudication registry",
    )
    return {
        "inventory_value": inventory_value,
        "inventory_raw": inventory_raw,
        "requests_value": requests_value,
        "requests_raw": requests_raw,
        "aggregate": aggregate,
        "aggregate_raw": aggregate_raw,
        "workflow_registry": workflow,
        "workflow_registry_raw": workflow_raw,
        "legacy_registry": legacy,
        "legacy_registry_raw": legacy_raw,
        "results_root": args.results_root,
    }


def _load_candidate_bundle(
    manifest_path: pathlib.Path, shards_directory: pathlib.Path
) -> tuple[dict[str, Any], bytes, dict[str, bytes]]:
    manifest, manifest_raw = _canonical_input(
        manifest_path, MAX_MANIFEST_BYTES, "candidate manifest"
    )
    if shards_directory.is_symlink() or not shards_directory.is_dir():
        raise UnavailabilityError("candidate shards path is not one real directory")
    shard_bytes: dict[str, bytes] = {}
    for path in sorted(shards_directory.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise UnavailabilityError("candidate shard path is not one real file")
        digest = path.stem
        if path.suffix != ".json" or DIGEST.fullmatch(digest) is None:
            raise UnavailabilityError(
                "candidate shard filename is not content-addressed"
            )
        shard_bytes[digest] = _read_bounded(path, MAX_SHARD_BYTES, "candidate shard")
    validate_candidate_bundle(manifest, shard_bytes)
    return manifest, manifest_raw, shard_bytes


def _write_raw_exclusive(path: pathlib.Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(raw)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_candidate_bundle(
    output_directory: pathlib.Path,
    manifest: dict[str, Any],
    shard_bytes: dict[str, bytes],
) -> pathlib.Path:
    os.mkdir(output_directory, 0o700)
    shards_directory = output_directory / "shards"
    os.mkdir(shards_directory, 0o700)
    for digest, raw in sorted(shard_bytes.items()):
        _write_raw_exclusive(shards_directory / f"{digest}.json", raw)
    manifest_raw = canonical_document_bytes(manifest)
    manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
    manifest_path = output_directory / f"{manifest_digest}.json"
    _write_raw_exclusive(manifest_path, manifest_raw)
    return manifest_path


def _verify_against_trusted(
    manifest: dict[str, Any],
    manifest_raw: bytes,
    shard_bytes: dict[str, bytes],
    trusted_arguments: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = validate_candidate_bundle(manifest, shard_bytes)
    expected_manifest, expected_shards = build_candidate_bundle(**trusted_arguments)
    if canonical_document_bytes(expected_manifest) != manifest_raw:
        raise UnavailabilityError(
            "candidate manifest differs from trusted frozen inputs"
        )
    if expected_shards != shard_bytes:
        raise UnavailabilityError("candidate shards differ from trusted frozen inputs")
    return candidates


def _add_trusted_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--inventory", required=True, type=pathlib.Path)
    parser.add_argument("--resolution-requests", required=True, type=pathlib.Path)
    parser.add_argument("--results-root", required=True, type=pathlib.Path)
    parser.add_argument("--aggregate", required=True, type=pathlib.Path)
    parser.add_argument("--workflow-registry", required=True, type=pathlib.Path)
    parser.add_argument(
        "--legacy-adjudication-registry", required=True, type=pathlib.Path
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    _add_trusted_arguments(prepare_parser)
    prepare_parser.add_argument("--output-directory", required=True, type=pathlib.Path)
    verify_parser = commands.add_parser("verify")
    _add_trusted_arguments(verify_parser)
    verify_parser.add_argument("--candidate-manifest", required=True, type=pathlib.Path)
    verify_parser.add_argument("--candidate-shards", required=True, type=pathlib.Path)
    finalize_parser = commands.add_parser("finalize")
    _add_trusted_arguments(finalize_parser)
    finalize_parser.add_argument(
        "--candidate-manifest", required=True, type=pathlib.Path
    )
    finalize_parser.add_argument("--candidate-shards", required=True, type=pathlib.Path)
    finalize_parser.add_argument("--reviews", required=True, type=pathlib.Path)
    finalize_parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        trusted = _trusted_arguments(args)
        if args.command == "prepare":
            manifest, shard_bytes = build_candidate_bundle(**trusted)
            manifest_path = _write_candidate_bundle(
                args.output_directory, manifest, shard_bytes
            )
            print(f"candidate manifest: {manifest_path}")
            print(f"candidate shards: {args.output_directory / 'shards'}")
            return 0
        else:
            manifest, manifest_raw, shard_bytes = _load_candidate_bundle(
                args.candidate_manifest, args.candidate_shards
            )
            if args.command == "verify":
                candidates = _verify_against_trusted(
                    manifest, manifest_raw, shard_bytes, trusted
                )
                print(
                    "verified candidate manifest "
                    f"{hashlib.sha256(manifest_raw).hexdigest()} "
                    f"({len(candidates)} requests)"
                )
                return 0
            reviews, _ = _canonical_input(args.reviews, MAX_REVIEW_BYTES, "reviews")
            output = finalize(
                manifest=manifest,
                manifest_raw=manifest_raw,
                shard_bytes=shard_bytes,
                review_value=reviews,
                trusted_arguments=trusted,
            )
            _write_exclusive(args.output, output)
    except (OSError, UnavailabilityError, ValueError) as error:
        print(f"public-replay-unavailability: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
