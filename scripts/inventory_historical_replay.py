#!/usr/bin/env python3
"""Build a deterministic, source-minimized historical replay inventory."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

from results_schema import (
    ResultsSchemaError,
    canonical_store_digest,
    read_results_file,
)

COMMIT = re.compile(r"[0-9a-f]{40}")
RESULT_PATH = re.compile(r"results/[A-Za-z0-9][A-Za-z0-9_.-]*\.json")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
MAX_ROOT_ENTRIES = 4096
MAX_RESULTS_FILE_BYTES = 1024 * 1024
MAX_RESULTS_STORE_BYTES = 32 * 1024 * 1024
MAX_RECORDS_PER_FILE = 4096
MAX_TOTAL_RECORDS = 10_000
MAX_INVENTORY_BYTES = 16 * 1024 * 1024


class InventoryError(ValueError):
    """The results store cannot produce an unambiguous replay inventory."""


def _entry(
    *,
    user: str,
    relative_path: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    if len(user) > 100 or len(relative_path) > 255:
        raise InventoryError("result owner or path exceeds the inventory limit")
    if len(record["problem_id"]) > 256:
        raise InventoryError("problem ID exceeds the inventory limit")
    submission = record["submission"]
    public = submission["public"]
    source = {
        "kind": submission["kind"],
        "visibility": "public" if public else "private",
        "readiness": (
            "public_source_probe_pending"
            if public
            else "private_archive_migration_pending"
        ),
    }
    if public:
        repository = submission["repo"]
        if (
            not isinstance(repository, str)
            or len(repository) > 201
            or REPOSITORY.fullmatch(repository) is None
            or any(segment in {".", ".."} for segment in repository.split("/"))
        ):
            raise InventoryError("public source repository is not canonical")
        source.update(
            {
                "repository": repository,
                "commit": submission["ref"],
            }
        )
    return {
        "result_id": record["result_id"],
        "owner": user,
        "results_path": relative_path,
        "problem_id": record["problem_id"],
        "statement_revision": record["statement_revision"],
        "accepted_at": record["accepted_at"],
        "benchmark_commit": record["benchmark_commit"],
        "source": source,
    }


def inventory(results_root: pathlib.Path, source_commit: str) -> dict[str, Any]:
    if COMMIT.fullmatch(source_commit) is None:
        raise InventoryError("source commit must be a full lowercase Git SHA")
    if not results_root.is_dir() or results_root.is_symlink():
        raise InventoryError("results root must be one real directory")

    canonical_files: list[tuple[str, dict[str, Any]]] = []
    entries: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    paths: list[pathlib.Path] = []
    for path in results_root.iterdir():
        if len(paths) >= MAX_ROOT_ENTRIES:
            raise InventoryError("results root exceeds the entry-count limit")
        paths.append(path)
    paths.sort(key=lambda path: path.name)
    if not paths:
        raise InventoryError("results root contains no JSON files")
    total_bytes = 0
    for path in paths:
        if path.name == ".gitkeep":
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > 1
                or path.read_bytes() not in {b"", b"\n"}
            ):
                raise InventoryError("results .gitkeep is not canonical")
            continue
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise InventoryError(
                f"results root entry is not one canonical JSON file: {path.name}"
            )
        size = path.stat().st_size
        if not 0 < size <= MAX_RESULTS_FILE_BYTES:
            raise InventoryError(
                f"results file exceeds the size limit: {path.name}"
            )
        total_bytes += size
        if total_bytes > MAX_RESULTS_STORE_BYTES:
            raise InventoryError("results store exceeds the total size limit")
        relative = f"results/{path.name}"
        if RESULT_PATH.fullmatch(relative) is None:
            raise InventoryError(f"results path is not canonical: {relative}")
        try:
            data, version = read_results_file(path)
        except (OSError, UnicodeError, ResultsSchemaError) as error:
            raise InventoryError(str(error)) from error
        if version != 2:
            raise InventoryError(f"historical inventory requires schema version 2: {relative}")
        if path.stem.casefold() != data["user"].casefold():
            raise InventoryError(
                f"results filename stem does not bind user: {relative}"
            )
        if len(data["results"]) > MAX_RECORDS_PER_FILE:
            raise InventoryError(f"results file exceeds the record limit: {relative}")
        if len(entries) + len(data["results"]) > MAX_TOTAL_RECORDS:
            raise InventoryError("results store exceeds the total record limit")
        canonical_files.append((relative, data))
        for record in data["results"]:
            result_id = record["result_id"]
            previous = seen.get(result_id)
            if previous is not None:
                raise InventoryError(
                    f"duplicate result_id {result_id} in {previous} and {relative}"
                )
            seen[result_id] = relative
            entries.append(
                _entry(user=data["user"], relative_path=relative, record=record)
            )

    if not canonical_files:
        raise InventoryError("results root contains no JSON files")
    if not entries:
        raise InventoryError("results store contains no accepted results")
    entries.sort(key=lambda value: value["result_id"])
    public_count = sum(
        entry["source"]["visibility"] == "public" for entry in entries
    )
    private_count = len(entries) - public_count
    return {
        "schema_version": 1,
        "source_repository": "leanprover/lean-eval-submissions",
        "source_commit": source_commit,
        "results_store_sha256": canonical_store_digest(canonical_files),
        "result_count": len(entries),
        "classification_counts": {
            "public_source_probe_pending": public_count,
            "private_archive_migration_pending": private_count,
        },
        "entries": entries,
    }


def canonical_inventory_bytes(value: Any) -> bytes:
    try:
        encoded = (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise InventoryError(f"inventory is not canonicalizable: {error}") from error
    if len(encoded) > MAX_INVENTORY_BYTES:
        raise InventoryError("inventory exceeds the output size limit")
    return encoded


def write_exclusive(path: pathlib.Path, value: Any) -> None:
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise InventoryError("output parent must be one existing real directory")
    encoded = canonical_inventory_bytes(value)
    with path.open("xb") as stream:
        stream.write(encoded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", required=True, type=pathlib.Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        write_exclusive(args.output, inventory(args.results_root, args.source_commit))
    except (InventoryError, OSError, ValueError) as error:
        print(f"historical-replay-inventory: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
