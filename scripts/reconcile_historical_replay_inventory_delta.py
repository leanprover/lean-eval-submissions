#!/usr/bin/env python3
"""Prove that a later historical inventory is an append-only baseline delta."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

from inventory_historical_replay import canonical_inventory_bytes
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

MAX_DELTA_BYTES = 16 * 1024 * 1024


class InventoryDeltaError(ValueError):
    """The selected inventories do not form one append-only cutoff delta."""


def _read_canonical_json(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise InventoryDeltaError(f"{label} must be one real JSON file")
    if not 0 < path.stat().st_size <= MAX_DELTA_BYTES:
        raise InventoryDeltaError(f"{label} exceeds the input size limit")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InventoryDeltaError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise InventoryDeltaError(f"{label} must be one JSON object")
    try:
        canonical = canonical_inventory_bytes(value)
    except ValueError as error:
        raise InventoryDeltaError(f"{label} is invalid: {error}") from error
    if raw != canonical:
        raise InventoryDeltaError(f"{label} is not canonical JSON")
    return value, raw


def _load_schema(path: pathlib.Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise InventoryDeltaError("inventory schema must be one bounded real file")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InventoryDeltaError(f"cannot read inventory schema: {error}") from error
    if not isinstance(value, dict):
        raise InventoryDeltaError("inventory schema must be one JSON object")
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as error:
        raise InventoryDeltaError(f"inventory schema is invalid: {error.message}") from error
    return value


def _validate_inventory(
    value: dict[str, Any], schema: dict[str, Any], label: str
) -> None:
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise InventoryDeltaError(
            f"{label} fails inventory schema at {location}: {error.message}"
        ) from error
    entries = value["entries"]
    identities = [entry["result_id"] for entry in entries]
    if identities != sorted(set(identities)):
        raise InventoryDeltaError(f"{label} entries are not uniquely sorted")
    if value["result_count"] != len(entries):
        raise InventoryDeltaError(f"{label} result count does not match entries")
    counts = value["classification_counts"]
    observed_public = sum(
        entry["source"]["readiness"] == "public_source_probe_pending"
        for entry in entries
    )
    observed_private = len(entries) - observed_public
    if counts != {
        "public_source_probe_pending": observed_public,
        "private_archive_migration_pending": observed_private,
    }:
        raise InventoryDeltaError(f"{label} classification counts do not match entries")


def _identity(value: dict[str, Any], raw: bytes) -> dict[str, Any]:
    return {
        "source_commit": value["source_commit"],
        "results_store_sha256": value["results_store_sha256"],
        "inventory_sha256": hashlib.sha256(raw).hexdigest(),
        "result_count": value["result_count"],
    }


def reconcile(
    baseline: dict[str, Any],
    baseline_raw: bytes,
    current: dict[str, Any],
    current_raw: bytes,
    inventory_schema: dict[str, Any],
) -> dict[str, Any]:
    _validate_inventory(baseline, inventory_schema, "baseline inventory")
    _validate_inventory(current, inventory_schema, "current inventory")
    if baseline["source_repository"] != current["source_repository"]:
        raise InventoryDeltaError("inventory repositories do not match")
    baseline_entries = {entry["result_id"]: entry for entry in baseline["entries"]}
    current_entries = {entry["result_id"]: entry for entry in current["entries"]}
    missing = sorted(set(baseline_entries) - set(current_entries))
    if missing:
        raise InventoryDeltaError(f"current inventory removed baseline result {missing[0]}")
    for result_id, entry in baseline_entries.items():
        if current_entries[result_id] != entry:
            raise InventoryDeltaError(
                f"current inventory changed baseline result {result_id}"
            )
    delta_entries = [
        entry
        for result_id, entry in current_entries.items()
        if result_id not in baseline_entries
    ]
    delta_entries.sort(key=lambda entry: entry["result_id"])
    public_count = sum(
        entry["source"]["readiness"] == "public_source_probe_pending"
        for entry in delta_entries
    )
    private_count = len(delta_entries) - public_count
    return {
        "schema_version": 1,
        "kind": "historical_replay_inventory_delta",
        "source_repository": baseline["source_repository"],
        "baseline": _identity(baseline, baseline_raw),
        "current": _identity(current, current_raw),
        "delta_counts": {
            "result_count": len(delta_entries),
            "public_source_probe_pending": public_count,
            "private_archive_migration_pending": private_count,
        },
        "entries": delta_entries,
    }


def canonical_delta_bytes(value: Any) -> bytes:
    try:
        encoded = (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise InventoryDeltaError(f"delta is not canonicalizable: {error}") from error
    if len(encoded) > MAX_DELTA_BYTES:
        raise InventoryDeltaError("delta exceeds the output size limit")
    return encoded


def write_exclusive(path: pathlib.Path, value: Any) -> None:
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise InventoryDeltaError("output parent must be one existing real directory")
    encoded = canonical_delta_bytes(value)
    with path.open("xb") as stream:
        stream.write(encoded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=pathlib.Path)
    parser.add_argument("--current", required=True, type=pathlib.Path)
    parser.add_argument("--inventory-schema", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        schema = _load_schema(args.inventory_schema)
        baseline, baseline_raw = _read_canonical_json(args.baseline, "baseline inventory")
        current, current_raw = _read_canonical_json(args.current, "current inventory")
        write_exclusive(
            args.output,
            reconcile(baseline, baseline_raw, current, current_raw, schema),
        )
    except (InventoryDeltaError, OSError, ValueError) as error:
        print(f"historical-replay-inventory-delta: {error}", file=sys.stderr)
        return 1
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
