#!/usr/bin/env python3
"""Prove that a later historical inventory is an append-only baseline delta."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

from build_result_receipt import result_tree_digest
from inventory_historical_replay import canonical_inventory_bytes
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from results_schema import (
    ResultsSchemaError,
    canonical_file_bytes,
    canonical_store_digest,
    validate_v2,
)

MAX_DELTA_BYTES = 16 * 1024 * 1024
MAX_RESULTS_BYTES = 32 * 1024 * 1024
MAX_RESULTS = 10_000


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


def _git_bytes(root: pathlib.Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise InventoryDeltaError("cannot read the exact Results commit") from error
    if len(completed.stdout) > MAX_RESULTS_BYTES:
        raise InventoryDeltaError("exact Results Git output exceeds its bound")
    return completed.stdout


def result_documents(
    results_root: pathlib.Path, source_commit: str | None = None
) -> list[tuple[str, dict[str, Any], bytes]]:
    if results_root.is_symlink() or not results_root.is_dir():
        raise InventoryDeltaError("Results root must be one real directory")
    sources: list[tuple[str, bytes]] = []
    if source_commit is None:
        for path in sorted(results_root.iterdir(), key=lambda item: item.name):
            if path.name == ".gitkeep":
                continue
            if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                raise InventoryDeltaError("Results contains a noncanonical root entry")
            sources.append((f"results/{path.name}", path.read_bytes()))
    else:
        repository_root = pathlib.Path(
            _git_bytes(results_root, "rev-parse", "--show-toplevel").decode().strip()
        ).resolve()
        if results_root.resolve() != repository_root / "results":
            raise InventoryDeltaError("Results root is not the canonical results subtree")
        entries = _git_bytes(
            repository_root,
            "ls-tree",
            "-r",
            source_commit,
            "--",
            "results",
        ).decode("utf-8").splitlines()
        for tree_entry in entries:
            try:
                metadata, relative = tree_entry.split("\t", 1)
                mode, object_kind, object_id = metadata.split(" ")
            except ValueError as error:
                raise InventoryDeltaError(
                    "exact Results commit has an invalid tree entry"
                ) from error
            if relative == "results/.gitkeep":
                continue
            if (
                mode != "100644"
                or object_kind != "blob"
                or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", object_id) is None
                or not relative.startswith("results/")
                or pathlib.PurePosixPath(relative).parent.as_posix() != "results"
                or not relative.endswith(".json")
            ):
                raise InventoryDeltaError("exact Results commit has a noncanonical entry")
            sources.append(
                (
                    relative,
                    _git_bytes(repository_root, "cat-file", "blob", object_id),
                )
            )
    documents: list[tuple[str, dict[str, Any], bytes]] = []
    total_bytes = 0
    for relative, raw in sources:
        total_bytes += len(raw)
        if total_bytes > MAX_RESULTS_BYTES:
            raise InventoryDeltaError("Results store exceeds its byte bound")
        try:
            document = validate_v2(json.loads(raw), context=relative)
        except (UnicodeError, json.JSONDecodeError, ResultsSchemaError) as error:
            raise InventoryDeltaError("Results store is invalid") from error
        if canonical_file_bytes(document) != raw:
            raise InventoryDeltaError("Results store is not canonical schema version 2")
        documents.append((relative, document, raw))
    return documents


def _result_intakes(
    results_root: pathlib.Path,
    current: dict[str, Any],
    source_commit: str | None = None,
) -> dict[str, dict[str, Any]]:
    documents = result_documents(results_root, source_commit)
    intakes: dict[str, dict[str, Any]] = {}
    for relative, document, raw in documents:
        file_sha256 = hashlib.sha256(raw).hexdigest()
        tree_digest = result_tree_digest(relative, raw)
        for record in document["results"]:
            result_id = record["result_id"]
            if result_id in intakes or len(intakes) >= MAX_RESULTS:
                raise InventoryDeltaError("Results identity inventory is invalid")
            intake = record["intake"]
            if intake["kind"] == "issue":
                binding = {
                    "kind": "issue",
                    "issue_number": intake["issue_number"],
                }
            elif intake["kind"] == "server":
                binding = {
                    "kind": "server",
                    "submission_id": intake["submission_id"],
                }
            else:  # The v2 reader currently closes this, but fail locally too.
                raise InventoryDeltaError("Result intake kind is invalid")
            intakes[result_id] = {
                **binding,
                "results_path": relative,
                "result_file_sha256": file_sha256,
                "result_tree_digest": tree_digest,
            }
    try:
        store_digest = canonical_store_digest(
            [(relative, document) for relative, document, _ in documents]
        )
    except (ResultsSchemaError, UnicodeError, ValueError) as error:
        raise InventoryDeltaError("Results store digest cannot be reproduced") from error
    if store_digest != current["results_store_sha256"]:
        raise InventoryDeltaError("Results store differs from the current inventory")
    current_ids = {entry["result_id"] for entry in current["entries"]}
    if set(intakes) != current_ids:
        raise InventoryDeltaError("Results identities differ from the current inventory")
    return intakes


def reconcile(
    baseline: dict[str, Any],
    baseline_raw: bytes,
    current: dict[str, Any],
    current_raw: bytes,
    inventory_schema: dict[str, Any],
    results_root: pathlib.Path | None = None,
    results_commit: str | None = None,
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
    all_delta_entries = [
        entry
        for result_id, entry in current_entries.items()
        if result_id not in baseline_entries
    ]
    if results_root is None:
        # Retain the pure inventory helper for old callers and focused tests.
        # Canonical cutoff production always supplies the exact Results root.
        delta_entries = all_delta_entries
        server_exclusions: list[dict[str, Any]] = []
    else:
        intakes = _result_intakes(results_root, current, results_commit)
        delta_entries = []
        server_exclusions = []
        for entry in all_delta_entries:
            result_id = entry["result_id"]
            intake = intakes[result_id]
            if intake["kind"] == "issue":
                delta_entries.append(entry)
            else:
                server_exclusions.append(
                    {
                        "result_id": result_id,
                        "submission_id": intake["submission_id"],
                        "results_path": intake["results_path"],
                        "result_file_sha256": intake["result_file_sha256"],
                        "result_tree_digest": intake["result_tree_digest"],
                    }
                )
    delta_entries.sort(key=lambda entry: entry["result_id"])
    server_exclusions.sort(key=lambda entry: entry["result_id"])
    if current["result_count"] != (
        baseline["result_count"] + len(delta_entries) + len(server_exclusions)
    ):
        raise InventoryDeltaError("issue and server delta partition is incomplete")
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
            "server_native_excluded": len(server_exclusions),
        },
        "server_exclusions": server_exclusions,
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
    parser.add_argument("--results-root", required=True, type=pathlib.Path)
    parser.add_argument("--results-commit")
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        schema = _load_schema(args.inventory_schema)
        baseline, baseline_raw = _read_canonical_json(args.baseline, "baseline inventory")
        current, current_raw = _read_canonical_json(args.current, "current inventory")
        write_exclusive(
            args.output,
            reconcile(
                baseline,
                baseline_raw,
                current,
                current_raw,
                schema,
                args.results_root.resolve(),
                args.results_commit,
            ),
        )
    except (InventoryDeltaError, OSError, ValueError) as error:
        print(f"historical-replay-inventory-delta: {error}", file=sys.stderr)
        return 1
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
