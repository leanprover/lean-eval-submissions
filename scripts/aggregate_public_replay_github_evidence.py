#!/usr/bin/env python3
"""Aggregate a complete, exact set of historical public replay evidence shards."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys
from typing import Any

from resolve_public_replay_github_evidence import (
    DIGEST,
    MAX_REQUEST_BYTES,
    EvidenceError,
    _read_bounded,
    _write_exclusive,
    validate_evidence,
    validate_requests,
    validate_workflow_registry,
)


MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
MAX_REGISTRY_BYTES = 512 * 1024


class AggregationError(ValueError):
    """Evidence shards do not form one exact, complete corpus."""


def canonical_document_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _status_counts(resolutions: list[dict[str, Any]]) -> collections.Counter[str]:
    return collections.Counter(item["status"] for item in resolutions)


def aggregate(
    requests: dict[str, Any],
    requests_sha256: str,
    workflow_registry: dict[str, Any],
    workflow_registry_sha256: str,
    evidence: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    try:
        validate_requests(requests)
        validate_workflow_registry(workflow_registry)
    except EvidenceError as error:
        raise AggregationError(str(error)) from error
    if DIGEST.fullmatch(requests_sha256) is None or DIGEST.fullmatch(
        workflow_registry_sha256
    ) is None:
        raise AggregationError("aggregate input digest is invalid")
    if hashlib.sha256(canonical_document_bytes(requests)).hexdigest() != requests_sha256:
        raise AggregationError("resolution requests are not canonical or digest-bound")
    if (
        hashlib.sha256(canonical_document_bytes(workflow_registry)).hexdigest()
        != workflow_registry_sha256
    ):
        raise AggregationError("workflow registry is not canonical or digest-bound")
    if not evidence:
        raise AggregationError("no evidence shards were supplied")

    shard_count: int | None = None
    by_index: dict[int, tuple[str, dict[str, Any]]] = {}
    for digest, value in evidence:
        if DIGEST.fullmatch(digest) is None:
            raise AggregationError("evidence shard digest is invalid")
        if hashlib.sha256(canonical_document_bytes(value)).hexdigest() != digest:
            raise AggregationError("evidence shard is not canonical or digest-bound")
        try:
            validate_evidence(
                value,
                requests,
                workflow_registry,
                workflow_registry_sha256,
            )
        except EvidenceError as error:
            raise AggregationError(str(error)) from error
        expected_identity = {
            "source_repository": requests["source_repository"],
            "source_commit": requests["source_commit"],
            "inventory_sha256": requests["inventory_sha256"],
            "resolution_requests_sha256": requests_sha256,
            "workflow_definition_registry_sha256": workflow_registry_sha256,
            "request_count": requests["request_count"],
            "result_count": requests["result_count"],
        }
        if any(value.get(field) != wanted for field, wanted in expected_identity.items()):
            raise AggregationError("evidence shard identity differs from the request set")
        if shard_count is None:
            shard_count = value["shard_count"]
        elif value["shard_count"] != shard_count:
            raise AggregationError("evidence shards use mixed shard counts")
        index = value["shard_index"]
        if index in by_index:
            raise AggregationError("evidence shard index is duplicated")
        by_index[index] = (digest, value)

    assert shard_count is not None
    if set(by_index) != set(range(shard_count)):
        raise AggregationError("evidence shard indices are incomplete")

    resolutions = sorted(
        (
            resolution
            for index in range(shard_count)
            for resolution in by_index[index][1]["resolutions"]
        ),
        key=lambda item: item["request_id"],
    )
    expected_ids = [request["request_id"] for request in requests["requests"]]
    actual_ids = [resolution["request_id"] for resolution in resolutions]
    if actual_ids != expected_ids or len(actual_ids) != len(set(actual_ids)):
        raise AggregationError("evidence shards do not cover every request exactly once")

    counts = _status_counts(resolutions)
    shards = []
    for index in range(shard_count):
        digest, value = by_index[index]
        shards.append(
            {
                "shard_index": index,
                "evidence_sha256": digest,
                "request_count": value["shard_request_count"],
                "result_count": value["shard_result_count"],
                "resolved_count": value["resolved_count"],
                "pending_count": value["pending_count"],
            }
        )
    output = {
        "schema_version": 1,
        "kind": "historical_public_replay_github_evidence_aggregate",
        "source_repository": requests["source_repository"],
        "source_commit": requests["source_commit"],
        "inventory_sha256": requests["inventory_sha256"],
        "resolution_requests_sha256": requests_sha256,
        "workflow_definition_registry_sha256": workflow_registry_sha256,
        "request_count": requests["request_count"],
        "result_count": requests["result_count"],
        "shard_count": shard_count,
        "resolved_count": counts["resolved"],
        "source_unavailable_count": counts["source_unavailable"],
        "source_indeterminate_count": counts["source_probe_indeterminate"],
        "probe_indeterminate_count": counts["probe_indeterminate"],
        "timing_indeterminate_count": counts["timing_indeterminate"],
        "workflow_contract_unreviewed_count": counts[
            "workflow_contract_unreviewed"
        ],
        "ambiguous_count": counts["ambiguous"],
        "evidence_missing_count": counts["evidence_missing"],
        "pending_count": len(resolutions) - counts["resolved"],
        "shards": shards,
        "resolutions": resolutions,
    }
    validate_aggregate(
        output,
        requests,
        requests_sha256,
        workflow_registry,
        workflow_registry_sha256,
    )
    return output


def validate_aggregate(
    value: Any,
    requests: dict[str, Any],
    requests_sha256: str,
    workflow_registry: dict[str, Any],
    workflow_registry_sha256: str,
) -> None:
    expected_fields = {
        "schema_version",
        "kind",
        "source_repository",
        "source_commit",
        "inventory_sha256",
        "resolution_requests_sha256",
        "workflow_definition_registry_sha256",
        "request_count",
        "result_count",
        "shard_count",
        "resolved_count",
        "source_unavailable_count",
        "source_indeterminate_count",
        "probe_indeterminate_count",
        "timing_indeterminate_count",
        "workflow_contract_unreviewed_count",
        "ambiguous_count",
        "evidence_missing_count",
        "pending_count",
        "shards",
        "resolutions",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise AggregationError("aggregate fields are not closed")
    if (
        value["schema_version"] != 1
        or value["kind"] != "historical_public_replay_github_evidence_aggregate"
        or value["source_repository"] != requests["source_repository"]
        or value["source_commit"] != requests["source_commit"]
        or value["inventory_sha256"] != requests["inventory_sha256"]
        or value["resolution_requests_sha256"] != requests_sha256
        or value["workflow_definition_registry_sha256"]
        != workflow_registry_sha256
        or value["request_count"] != requests["request_count"]
        or value["result_count"] != requests["result_count"]
        or type(value["shard_count"]) is not int
        or not 1 <= value["shard_count"] <= 64
    ):
        raise AggregationError("aggregate identity is invalid")
    for field in ("resolution_requests_sha256", "workflow_definition_registry_sha256"):
        if not isinstance(value[field], str) or DIGEST.fullmatch(value[field]) is None:
            raise AggregationError(f"aggregate {field} is invalid")

    shards = value["shards"]
    if not isinstance(shards, list) or len(shards) != value["shard_count"]:
        raise AggregationError("aggregate shard manifest is incomplete")
    if [item.get("shard_index") for item in shards if isinstance(item, dict)] != list(
        range(value["shard_count"])
    ):
        raise AggregationError("aggregate shard indices are not exact")
    for shard in shards:
        if not isinstance(shard, dict) or set(shard) != {
            "shard_index",
            "evidence_sha256",
            "request_count",
            "result_count",
            "resolved_count",
            "pending_count",
        }:
            raise AggregationError("aggregate shard entry is not closed")
        if not isinstance(shard["evidence_sha256"], str) or DIGEST.fullmatch(
            shard["evidence_sha256"]
        ) is None:
            raise AggregationError("aggregate shard digest is invalid")
        for field in ("request_count", "result_count", "resolved_count", "pending_count"):
            if type(shard[field]) is not int or shard[field] < 0:
                raise AggregationError("aggregate shard count is invalid")

    resolutions = value["resolutions"]
    if not isinstance(resolutions, list):
        raise AggregationError("aggregate resolutions are invalid")
    expected_ids = [request["request_id"] for request in requests["requests"]]
    if [item.get("request_id") for item in resolutions if isinstance(item, dict)] != expected_ids:
        raise AggregationError("aggregate does not cover every request exactly once")

    # Reconstruct every shard and reuse the authoritative evidence validator.
    for shard in shards:
        selected = [
            resolution
            for resolution in resolutions
            if int(resolution["request_id"].removeprefix("prr_"), 16)
            % value["shard_count"]
            == shard["shard_index"]
        ]
        selected_requests = [
            request
            for request in requests["requests"]
            if int(request["request_id"].removeprefix("prr_"), 16)
            % value["shard_count"]
            == shard["shard_index"]
        ]
        counts = _status_counts(selected)
        reconstructed = {
            "schema_version": 1,
            "kind": "historical_public_replay_github_evidence",
            "source_repository": value["source_repository"],
            "source_commit": value["source_commit"],
            "inventory_sha256": value["inventory_sha256"],
            "resolution_requests_sha256": value["resolution_requests_sha256"],
            "workflow_definition_registry_sha256": value[
                "workflow_definition_registry_sha256"
            ],
            "request_count": value["request_count"],
            "result_count": value["result_count"],
            "shard_index": shard["shard_index"],
            "shard_count": value["shard_count"],
            "shard_request_count": len(selected),
            "shard_result_count": sum(len(item["results"]) for item in selected_requests),
            "resolved_count": counts["resolved"],
            "source_unavailable_count": counts["source_unavailable"],
            "source_indeterminate_count": counts["source_probe_indeterminate"],
            "probe_indeterminate_count": counts["probe_indeterminate"],
            "timing_indeterminate_count": counts["timing_indeterminate"],
            "workflow_contract_unreviewed_count": counts[
                "workflow_contract_unreviewed"
            ],
            "pending_count": len(selected) - counts["resolved"],
            "resolutions": selected,
        }
        try:
            validate_evidence(
                reconstructed,
                requests,
                workflow_registry,
                workflow_registry_sha256,
            )
        except EvidenceError as error:
            raise AggregationError(str(error)) from error
        if (
            shard["request_count"] != reconstructed["shard_request_count"]
            or shard["result_count"] != reconstructed["shard_result_count"]
            or shard["resolved_count"] != reconstructed["resolved_count"]
            or shard["pending_count"] != reconstructed["pending_count"]
        ):
            raise AggregationError("aggregate shard counters are inconsistent")

    counts = _status_counts(resolutions)
    expected_counts = {
        "resolved_count": counts["resolved"],
        "source_unavailable_count": counts["source_unavailable"],
        "source_indeterminate_count": counts["source_probe_indeterminate"],
        "probe_indeterminate_count": counts["probe_indeterminate"],
        "timing_indeterminate_count": counts["timing_indeterminate"],
        "workflow_contract_unreviewed_count": counts[
            "workflow_contract_unreviewed"
        ],
        "ambiguous_count": counts["ambiguous"],
        "evidence_missing_count": counts["evidence_missing"],
        "pending_count": len(resolutions) - counts["resolved"],
    }
    if any(value[field] != wanted for field, wanted in expected_counts.items()):
        raise AggregationError("aggregate counters are inconsistent")
    if sum(shard["request_count"] for shard in shards) != value["request_count"] or sum(
        shard["result_count"] for shard in shards
    ) != value["result_count"]:
        raise AggregationError("aggregate shard coverage counts are inconsistent")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", required=True, type=pathlib.Path)
    parser.add_argument("--workflow-registry", required=True, type=pathlib.Path)
    parser.add_argument("--evidence", required=True, action="append", type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        requests_raw = _read_bounded(
            args.requests, MAX_REQUEST_BYTES, "resolution requests"
        )
        requests = json.loads(requests_raw)
        registry_raw = _read_bounded(
            args.workflow_registry, MAX_REGISTRY_BYTES, "workflow definition registry"
        )
        registry = json.loads(registry_raw)
        shards = []
        for path in args.evidence:
            raw = _read_bounded(path, MAX_EVIDENCE_BYTES, "evidence shard")
            shards.append((hashlib.sha256(raw).hexdigest(), json.loads(raw)))
        output = aggregate(
            requests,
            hashlib.sha256(requests_raw).hexdigest(),
            registry,
            hashlib.sha256(registry_raw).hexdigest(),
            shards,
        )
        _write_exclusive(args.output, output)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        EvidenceError,
        AggregationError,
    ) as error:
        print(f"public-replay-evidence-aggregate: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
