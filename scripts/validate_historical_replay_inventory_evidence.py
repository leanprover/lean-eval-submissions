#!/usr/bin/env python3
"""Validate a committed historical replay inventory and its closed evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import stat
import sys
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource

MAX_EVIDENCE_BYTES = 65536
MAX_INVENTORY_BYTES = 16777216


class EvidenceError(ValueError):
    """The committed inventory evidence is malformed or inconsistently bound."""


def _regular_bytes(path: pathlib.Path, maximum: int, label: str) -> bytes:
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError as error:
        raise EvidenceError(f"cannot read {label}: {error}") from error
    if not stat.S_ISREG(mode):
        raise EvidenceError(f"{label} must be a regular file")
    size = path.stat(follow_symlinks=False).st_size
    if size < 1 or size > maximum:
        raise EvidenceError(f"{label} size is outside the closed bound")
    try:
        return path.read_bytes()
    except OSError as error:
        raise EvidenceError(f"cannot read {label}: {error}") from error


def _canonical_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceError(f"{label} is not UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a JSON object")
    canonical = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if raw != canonical:
        raise EvidenceError(f"{label} is not canonical JSON")
    return value


def _schema(root: pathlib.Path, name: str) -> dict[str, Any]:
    raw = _regular_bytes(root / "schemas" / name, MAX_EVIDENCE_BYTES, name)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceError(f"{name} is not UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise EvidenceError(f"{name} must be a JSON object")
    return value


def validate(evidence_path: pathlib.Path, root: pathlib.Path) -> dict[str, Any]:
    root = root.resolve()
    evidence_raw = _regular_bytes(evidence_path, MAX_EVIDENCE_BYTES, "evidence")
    evidence = _canonical_json(evidence_raw, "evidence")

    inventory_schema = _schema(root, "historical-replay-inventory-v1.schema.json")
    evidence_schema = _schema(
        root, "historical-replay-inventory-evidence-v1.schema.json"
    )
    registry = Registry().with_resources(
        [
            (inventory_schema["$id"], Resource.from_contents(inventory_schema)),
            (evidence_schema["$id"], Resource.from_contents(evidence_schema)),
        ]
    )
    Draft202012Validator.check_schema(evidence_schema)
    Draft202012Validator(
        evidence_schema,
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(evidence)

    inventory_relative = pathlib.PurePosixPath(evidence["inventory_path"])
    expected_relative = (
        pathlib.PurePosixPath("evidence/historical-replay/inventories")
        / f"{evidence['inventory_sha256']}.json"
    )
    if inventory_relative != expected_relative:
        raise EvidenceError("inventory path is not bound to its SHA-256")
    inventory_path = root.joinpath(*inventory_relative.parts)
    try:
        inventory_path.resolve().relative_to(root)
    except ValueError as error:
        raise EvidenceError("inventory path escapes the repository root") from error

    inventory_raw = _regular_bytes(inventory_path, MAX_INVENTORY_BYTES, "inventory")
    if hashlib.sha256(inventory_raw).hexdigest() != evidence["inventory_sha256"]:
        raise EvidenceError("inventory SHA-256 does not match the closed evidence")
    if len(inventory_raw) != evidence["inventory_size_bytes"]:
        raise EvidenceError("inventory byte count does not match the closed evidence")
    inventory = _canonical_json(inventory_raw, "inventory")
    Draft202012Validator.check_schema(inventory_schema)
    Draft202012Validator(
        inventory_schema,
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(inventory)

    for field in (
        "source_repository",
        "source_commit",
        "results_store_sha256",
        "result_count",
        "classification_counts",
    ):
        if evidence[field] != inventory[field]:
            raise EvidenceError(f"inventory {field} is not bound to the evidence")
    source_commit = evidence["source_commit"]
    if evidence["workflow_head_sha"] != source_commit:
        raise EvidenceError("workflow head is not the inventory source commit")
    if evidence["dispatch_tag"] != f"lean-eval-dispatch/{source_commit}":
        raise EvidenceError("dispatch tag is not the immutable source tag")
    expected_run_url = (
        "https://github.com/leanprover/lean-eval-submissions/actions/runs/"
        f"{evidence['workflow_run_id']}"
    )
    if evidence["workflow_run_url"] != expected_run_url:
        raise EvidenceError("workflow run URL is not derived from the bound run ID")
    expected_artifact_name = f"historical-replay-inventory-{source_commit}"
    if evidence["transport_artifact"]["name"] != expected_artifact_name:
        raise EvidenceError("transport artifact name is not source-bound")

    entries = inventory["entries"]
    result_ids = [entry["result_id"] for entry in entries]
    if result_ids != sorted(set(result_ids)):
        raise EvidenceError("inventory result identities are not sorted and unique")
    if len(entries) != inventory["result_count"]:
        raise EvidenceError("inventory entry count is incomplete")
    if sum(inventory["classification_counts"].values()) != len(entries):
        raise EvidenceError("inventory classification counts are incomplete")
    for entry in entries:
        source = entry["source"]
        if source["visibility"] == "private" and set(source) != {
            "kind",
            "visibility",
            "readiness",
        }:
            raise EvidenceError("private inventory entry exposes a source locator")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=pathlib.Path)
    parser.add_argument(
        "--repository-root",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1],
    )
    arguments = parser.parse_args()
    try:
        validate(arguments.evidence, arguments.repository_root)
    except (
        EvidenceError,
        KeyError,
        SchemaError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        print(f"historical-replay-inventory-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
