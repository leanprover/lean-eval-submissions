#!/usr/bin/env python3
"""Build a source-free, fail-closed plan from resolved historical public evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

from aggregate_public_replay_github_evidence import (
    AggregationError,
    canonical_document_bytes,
    validate_aggregate,
)
from build_result_receipt import result_tree_digest
from inventory_historical_replay import InventoryError, inventory
from prepare_public_replay_resolution import ResolutionError, prepare
from resolve_public_replay_github_evidence import (
    MAX_REQUEST_BYTES,
    _read_bounded,
    _write_exclusive,
    validate_workflow_registry,
)
from results_schema import ResultsSchemaError, canonical_file_bytes, read_results_file

DIGEST = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
TOOLCHAIN = re.compile(
    r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta)[0-9]+)?\Z"
)
MAX_INVENTORY_BYTES = 16 * 1024 * 1024
MAX_AGGREGATE_BYTES = 32 * 1024 * 1024
MAX_REGISTRY_BYTES = 512 * 1024
MAX_TOOLCHAIN_REGISTRY_BYTES = 1024 * 1024


class PublicReplayPlanError(ValueError):
    """Resolved evidence cannot produce an exact public replay seed plan."""


def _canonical_input(
    path: pathlib.Path, limit: int, label: str
) -> tuple[dict[str, Any], bytes]:
    raw = _read_bounded(path, limit, label)
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PublicReplayPlanError(f"{label} is not readable JSON") from error
    if not isinstance(value, dict) or canonical_document_bytes(value) != raw:
        raise PublicReplayPlanError(f"{label} is not canonical JSON")
    return value, raw


def validate_toolchain_registry(value: Any) -> dict[str, dict[str, str]]:
    fields = {
        "schema_version",
        "kind",
        "benchmark_repository",
        "commit_count",
        "commits",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise PublicReplayPlanError("toolchain registry fields are not closed")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "historical_public_replay_toolchain_registry"
        or value["benchmark_repository"] != "leanprover/lean-eval"
        or type(value["commit_count"]) is not int
        or value["commit_count"] <= 0
        or not isinstance(value["commits"], list)
        or len(value["commits"]) != value["commit_count"]
    ):
        raise PublicReplayPlanError("toolchain registry identity is invalid")
    result: dict[str, dict[str, str]] = {}
    previous = ""
    for entry in value["commits"]:
        if not isinstance(entry, dict) or set(entry) != {
            "benchmark_commit",
            "lean_toolchain",
            "lean_toolchain_blob_sha256",
        }:
            raise PublicReplayPlanError(
                "toolchain registry entry fields are not closed"
            )
        commit = entry["benchmark_commit"]
        if (
            not isinstance(commit, str)
            or COMMIT.fullmatch(commit) is None
            or commit <= previous
        ):
            raise PublicReplayPlanError(
                "toolchain registry commits are not unique and sorted"
            )
        if (
            not isinstance(entry["lean_toolchain"], str)
            or TOOLCHAIN.fullmatch(entry["lean_toolchain"]) is None
        ):
            raise PublicReplayPlanError(
                "toolchain registry contains a non-exact toolchain"
            )
        digest = entry["lean_toolchain_blob_sha256"]
        if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            raise PublicReplayPlanError("toolchain registry blob digest is invalid")
        result[commit] = entry
        previous = commit
    return result


def _selected_records(
    results_root: pathlib.Path, requests: list[dict[str, Any]], resolved_ids: set[str]
) -> dict[str, dict[str, Any]]:
    selected_results = {
        result["result_id"]
        for request in requests
        if request["request_id"] in resolved_ids
        for result in request["results"]
    }
    by_path = {
        f"results/{request['owner'].lower()}.json"
        for request in requests
        if request["request_id"] in resolved_ids
    }
    records: dict[str, dict[str, Any]] = {}
    for relative in sorted(by_path):
        path = results_root.parent / relative
        if path.is_symlink() or not path.is_file():
            raise PublicReplayPlanError(
                f"result snapshot path is not one real file: {relative}"
            )
        try:
            document, version = read_results_file(path)
        except (OSError, UnicodeError, ResultsSchemaError) as error:
            raise PublicReplayPlanError(
                f"result snapshot is invalid: {relative}"
            ) from error
        raw = path.read_bytes()
        if version != 2 or canonical_file_bytes(document) != raw:
            raise PublicReplayPlanError(
                f"result snapshot is not canonical schema version 2: {relative}"
            )
        binding = {
            "results_path": relative,
            "result_file_sha256": hashlib.sha256(raw).hexdigest(),
            "result_tree_digest": result_tree_digest(relative, raw),
        }
        for record in document["results"]:
            result_id = record["result_id"]
            if result_id in selected_results:
                if result_id in records:
                    raise PublicReplayPlanError(
                        f"resolved result is duplicated: {result_id}"
                    )
                records[result_id] = {"record": record, "binding": binding}
    missing = sorted(selected_results - set(records))
    if missing:
        raise PublicReplayPlanError(
            f"resolved results are missing from snapshot: {missing}"
        )
    return records


def build_plan(
    *,
    inventory_value: dict[str, Any],
    inventory_raw: bytes,
    requests: dict[str, Any],
    requests_raw: bytes,
    aggregate: dict[str, Any],
    aggregate_raw: bytes,
    workflow_registry: dict[str, Any],
    workflow_registry_raw: bytes,
    toolchain_registry: dict[str, Any],
    toolchain_registry_raw: bytes,
    results_root: pathlib.Path,
) -> dict[str, Any]:
    if hashlib.sha256(inventory_raw).hexdigest() != aggregate["inventory_sha256"]:
        raise PublicReplayPlanError("inventory digest differs from evidence aggregate")
    try:
        recomputed_inventory = inventory(results_root, aggregate["source_commit"])
    except InventoryError as error:
        raise PublicReplayPlanError(str(error)) from error
    if recomputed_inventory != inventory_value:
        raise PublicReplayPlanError(
            "inventory does not equal the exact results snapshot"
        )
    try:
        recomputed_requests = prepare(
            inventory_value, hashlib.sha256(inventory_raw).hexdigest(), results_root
        )
    except ResolutionError as error:
        raise PublicReplayPlanError(str(error)) from error
    if recomputed_requests != requests:
        raise PublicReplayPlanError(
            "resolution requests do not equal the exact results snapshot"
        )
    try:
        validate_workflow_registry(workflow_registry)
        validate_aggregate(
            aggregate,
            requests,
            hashlib.sha256(requests_raw).hexdigest(),
            workflow_registry,
            hashlib.sha256(workflow_registry_raw).hexdigest(),
        )
    except (AggregationError, ValueError) as error:
        raise PublicReplayPlanError(str(error)) from error
    toolchains = validate_toolchain_registry(toolchain_registry)

    resolved = {
        item["request_id"]: item
        for item in aggregate["resolutions"]
        if item["status"] == "resolved"
    }
    records = _selected_records(results_root, requests["requests"], set(resolved))
    planned = []
    used_toolchains: set[str] = set()
    result_count = 0
    for request in requests["requests"]:
        resolution = resolved.get(request["request_id"])
        if resolution is None:
            continue
        candidates = [
            candidate
            for candidate in resolution["candidates"]
            if candidate.get("issue_repository")
            == resolution["selected_issue_repository"]
            and candidate.get("status") == "matched_source_available"
        ]
        if len(candidates) != 1:
            raise PublicReplayPlanError(
                "resolved request has no unique selected available candidate"
            )
        candidate = candidates[0]
        benchmark_commit = request["benchmark"]["commit"]
        toolchain = toolchains.get(benchmark_commit)
        if toolchain is None:
            raise PublicReplayPlanError(
                f"resolved benchmark has no exact toolchain: {benchmark_commit}"
            )
        used_toolchains.add(benchmark_commit)
        output_results = []
        for expected in request["results"]:
            selected = records[expected["result_id"]]
            record = selected["record"]
            if (
                record["problem_id"] != expected["problem_id"]
                or record["statement_revision"] != expected["statement_revision"]
                or record["declared_model"] != request["declared_model"]
                or record["benchmark_commit"] != benchmark_commit
                or record["submission"]
                != {
                    "kind": request["source"]["kind"],
                    "repo": request["source"]["repository"],
                    "ref": request["source"]["commit"],
                    "public": True,
                }
            ):
                raise PublicReplayPlanError(
                    f"resolved result differs from its request: {expected['result_id']}"
                )
            output_results.append(
                {
                    **expected,
                    "results_repository": aggregate["source_repository"],
                    "results_commit": aggregate["source_commit"],
                    **selected["binding"],
                }
            )
        result_count += len(output_results)
        planned.append(
            {
                "request_id": request["request_id"],
                "accepted_at": request["accepted_at"],
                "owner": request["owner"],
                "declared_model": request["declared_model"],
                "issue": {
                    "repository": resolution["selected_issue_repository"],
                    "number": request["issue_number"],
                    "identity_sha256": candidate["issue_identity_sha256"],
                },
                "historical_evaluation": {
                    "workflow_contract": candidate["workflow_contract"],
                    "workflow_repository_commit": candidate[
                        "workflow_repository_commit"
                    ],
                    "workflow_definition_sha256": candidate[
                        "workflow_definition_sha256"
                    ],
                    "workflow_run_id": candidate["workflow_run_id"],
                    "workflow_run_attempt": candidate["workflow_run_attempt"],
                    "workflow_run_identity_sha256": candidate[
                        "workflow_run_identity_sha256"
                    ],
                },
                "source": request["source"],
                "benchmark": {
                    **request["benchmark"],
                    "toolchain": toolchain["lean_toolchain"],
                    "lean_toolchain_blob_sha256": toolchain[
                        "lean_toolchain_blob_sha256"
                    ],
                },
                "results": output_results,
                "activation_requirement": "legacy_public_result_replay_authority_v1",
            }
        )
    if len(planned) != aggregate["resolved_count"] or result_count != sum(
        len(request["results"])
        for request in requests["requests"]
        if request["request_id"] in resolved
    ):
        raise PublicReplayPlanError("resolved evidence coverage was not preserved")
    if set(toolchains) != used_toolchains:
        raise PublicReplayPlanError(
            "toolchain registry has unused or missing benchmark commits"
        )
    return {
        "schema_version": 1,
        "kind": "historical_public_replay_plan",
        "source_repository": aggregate["source_repository"],
        "source_commit": aggregate["source_commit"],
        "inventory_sha256": aggregate["inventory_sha256"],
        "resolution_requests_sha256": aggregate["resolution_requests_sha256"],
        "github_evidence_aggregate_sha256": hashlib.sha256(aggregate_raw).hexdigest(),
        "workflow_definition_registry_sha256": aggregate[
            "workflow_definition_registry_sha256"
        ],
        "benchmark_toolchain_registry_sha256": hashlib.sha256(
            toolchain_registry_raw
        ).hexdigest(),
        "resolved_request_count": len(planned),
        "resolved_result_count": result_count,
        "pending_request_count": aggregate["pending_count"],
        "activation_status": "blocked",
        "activation_requirement": "legacy_public_result_replay_authority_v1",
        "requests": planned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=pathlib.Path)
    parser.add_argument("--requests", required=True, type=pathlib.Path)
    parser.add_argument("--evidence-aggregate", required=True, type=pathlib.Path)
    parser.add_argument("--workflow-registry", required=True, type=pathlib.Path)
    parser.add_argument("--toolchain-registry", required=True, type=pathlib.Path)
    parser.add_argument("--results-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        inventory_value, inventory_raw = _canonical_input(
            args.inventory, MAX_INVENTORY_BYTES, "inventory"
        )
        requests, requests_raw = _canonical_input(
            args.requests, MAX_REQUEST_BYTES, "resolution requests"
        )
        aggregate, aggregate_raw = _canonical_input(
            args.evidence_aggregate, MAX_AGGREGATE_BYTES, "evidence aggregate"
        )
        workflow_registry, workflow_registry_raw = _canonical_input(
            args.workflow_registry, MAX_REGISTRY_BYTES, "workflow registry"
        )
        toolchain_registry, toolchain_registry_raw = _canonical_input(
            args.toolchain_registry,
            MAX_TOOLCHAIN_REGISTRY_BYTES,
            "toolchain registry",
        )
        output = build_plan(
            inventory_value=inventory_value,
            inventory_raw=inventory_raw,
            requests=requests,
            requests_raw=requests_raw,
            aggregate=aggregate,
            aggregate_raw=aggregate_raw,
            workflow_registry=workflow_registry,
            workflow_registry_raw=workflow_registry_raw,
            toolchain_registry=toolchain_registry,
            toolchain_registry_raw=toolchain_registry_raw,
            results_root=args.results_root,
        )
        _write_exclusive(args.output, output)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ResultsSchemaError,
        PublicReplayPlanError,
        ValueError,
    ) as error:
        print(f"public-replay-plan: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
