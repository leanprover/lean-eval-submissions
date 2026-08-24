#!/usr/bin/env python3
"""Prepare deterministic GitHub-evidence requests for historical public replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

from inventory_historical_replay import InventoryError, inventory
from results_schema import ResultsSchemaError, read_results_file


RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")


class ResolutionError(ValueError):
    """Public replay identities cannot be resolved unambiguously."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def load_inventory(path: pathlib.Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ResolutionError("inventory is not readable JSON") from error
    if not isinstance(value, dict):
        raise ResolutionError("inventory is not a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _records(
    results_root: pathlib.Path, entries: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    by_path: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        path = entry.get("results_path")
        if not isinstance(path, str) or not path.startswith("results/"):
            raise ResolutionError("inventory results_path is invalid")
        by_path.setdefault(path, []).append(entry)
    result: dict[str, dict[str, Any]] = {}
    for relative, selected_entries in sorted(by_path.items()):
        path = results_root.parent / relative
        try:
            data, version = read_results_file(path)
        except (OSError, UnicodeError, ResultsSchemaError) as error:
            raise ResolutionError(f"cannot read inventory source {relative}") from error
        if version != 2:
            raise ResolutionError(f"inventory source {relative} is not schema version 2")
        wanted = {entry["result_id"] for entry in selected_entries}
        for record in data["results"]:
            result_id = record["result_id"]
            if result_id in wanted:
                if result_id in result:
                    raise ResolutionError(f"duplicate selected result {result_id}")
                result[result_id] = record
        missing = sorted(wanted - set(result))
        if missing:
            raise ResolutionError(f"inventory results missing from {relative}: {missing}")
    return result


def prepare(
    inventory_value: dict[str, Any],
    inventory_sha256: str,
    results_root: pathlib.Path,
) -> dict[str, Any]:
    source_commit = inventory_value.get("source_commit")
    if not isinstance(source_commit, str):
        raise ResolutionError("inventory source_commit is missing")
    try:
        recomputed = inventory(results_root, source_commit)
    except InventoryError as error:
        raise ResolutionError(str(error)) from error
    if inventory_value != recomputed:
        raise ResolutionError("inventory does not equal the exact recomputed results store")
    entries = inventory_value.get("entries")
    if not isinstance(entries, list):
        raise ResolutionError("inventory entries are invalid")
    public_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("source", {}).get("visibility") == "public"
    ]
    records = _records(results_root, public_entries)

    grouped: dict[bytes, dict[str, Any]] = {}
    for entry in public_entries:
        result_id = entry.get("result_id")
        if not isinstance(result_id, str) or RESULT_ID.fullmatch(result_id) is None:
            raise ResolutionError("public inventory result_id is invalid")
        record = records[result_id]
        expected = {
            "problem_id": record["problem_id"],
            "statement_revision": record["statement_revision"],
            "accepted_at": record["accepted_at"],
            "benchmark_commit": record["benchmark_commit"],
            "source": {
                "kind": record["submission"]["kind"],
                "visibility": "public",
                "readiness": "public_source_probe_pending",
                "repository": record["submission"]["repo"],
                "commit": record["submission"]["ref"],
            },
        }
        if any(entry.get(name) != value for name, value in expected.items()):
            raise ResolutionError(f"inventory entry {result_id} differs from its result")
        intake = record.get("intake")
        if not isinstance(intake, dict) or set(intake) != {"kind", "issue_number"}:
            raise ResolutionError(f"public result {result_id} has no exact issue intake")
        if intake.get("kind") != "issue" or type(intake.get("issue_number")) is not int:
            raise ResolutionError(f"public result {result_id} has invalid issue intake")
        identity = {
            "owner": entry["owner"],
            "issue_number": intake["issue_number"],
            "accepted_at": record["accepted_at"],
            "declared_model": record["declared_model"],
            "source": {
                "kind": record["submission"]["kind"],
                "repository": record["submission"]["repo"],
                "commit": record["submission"]["ref"],
                "visibility": "public",
            },
            "benchmark": {
                "repository": "leanprover/lean-eval",
                "commit": record["benchmark_commit"],
            },
        }
        identity_bytes = canonical_bytes(identity)
        selected = grouped.setdefault(
            identity_bytes,
            {
                "request_id": "prr_" + hashlib.sha256(identity_bytes).hexdigest(),
                **identity,
                "candidate_issue_repositories": [
                    "leanprover/lean-eval",
                    "leanprover/lean-eval-submissions",
                ],
                "expected_workflow": {"event": "issues", "name": "Submission"},
                "readiness": "github_evidence_fetch_pending",
                "results": [],
            },
        )
        selected["results"].append(
            {
                "result_id": result_id,
                "owner": entry["owner"],
                "problem_id": record["problem_id"],
                "statement_revision": record["statement_revision"],
            }
        )

    requests = sorted(grouped.values(), key=lambda value: value["request_id"])
    for request in requests:
        request["results"].sort(key=lambda value: value["result_id"])
    if sum(len(request["results"]) for request in requests) != len(public_entries):
        raise ResolutionError("public results were lost while grouping resolution requests")
    return {
        "schema_version": 1,
        "kind": "historical_public_replay_resolution_requests",
        "source_repository": inventory_value["source_repository"],
        "source_commit": source_commit,
        "inventory_sha256": inventory_sha256,
        "request_count": len(requests),
        "result_count": len(public_entries),
        "requests": requests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=pathlib.Path)
    parser.add_argument("--results-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        value, digest = load_inventory(args.inventory)
        output = prepare(value, digest, args.results_root)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(output, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except (OSError, ResolutionError, ValueError) as error:
        print(f"public-replay-resolution: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
