#!/usr/bin/env python3
"""Close the classification boundary for the one-shot final historical delta.

The output is deliberately not an activation packet.  It proves that one exact
append-only inventory delta is completely classified, identifies only the
replay images and legacy archives that still need work, and carries enough
source-free Result binding for a later, separately reviewed State append.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from build_result_receipt import result_tree_digest
from reconcile_historical_replay_inventory_delta import (
    InventoryDeltaError,
    _load_schema,
    _read_canonical_json,
    canonical_delta_bytes,
    reconcile,
)
from results_schema import (
    ResultsSchemaError,
    canonical_file_bytes,
    canonical_store_digest,
    read_results_file,
)


COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_RESULTS_BYTES = 32 * 1024 * 1024
MAX_RESULTS = 10_000
RESULTS_REPOSITORY = "leanprover/lean-eval-submissions"
AUDIT_REPOSITORY = "leanprover/lean-eval-audit"
BENCHMARK_REPOSITORY = "leanprover/lean-eval"


class FinalDeltaError(ValueError):
    """The selected cutoff inputs do not close one exact final delta."""


def canonical(value: Any) -> bytes:
    try:
        raw = (
            json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise FinalDeltaError("value is not canonicalizable JSON") from error
    if not 0 < len(raw) <= MAX_JSON_BYTES:
        raise FinalDeltaError("canonical JSON exceeds its size bound")
    return raw


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def entry_sha256(value: dict[str, Any]) -> str:
    return sha256(
        b"lean-eval-historical-final-delta-entry-v1\0"
        + json.dumps(
            value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _closed(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise FinalDeltaError(f"{label} fields are not closed")
    return value


def _read(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise FinalDeltaError(f"{label} must be one real file")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FinalDeltaError(f"cannot read {label}") from error
    if not 0 < len(raw) <= MAX_JSON_BYTES or not isinstance(value, dict) or canonical(value) != raw:
        raise FinalDeltaError(f"{label} is not bounded canonical JSON")
    return value, raw


def _read_schema(path: pathlib.Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise FinalDeltaError(f"{label} must be one real file")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FinalDeltaError(f"cannot read {label}") from error
    if not 0 < len(raw) <= MAX_JSON_BYTES or not isinstance(value, dict):
        raise FinalDeltaError(f"{label} is not bounded JSON")
    return value


def _git(root: pathlib.Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise FinalDeltaError("exact Results Git proof failed") from error
    if len(result.stdout.encode("utf-8")) > MAX_JSON_BYTES:
        raise FinalDeltaError("exact Results Git proof exceeded its bound")
    return result.stdout.strip()


def _verify_results_checkout(root: pathlib.Path, commit: str) -> None:
    if COMMIT.fullmatch(commit) is None:
        raise FinalDeltaError("Results commit is invalid")
    repository_root = pathlib.Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if root.resolve() != repository_root / "results":
        raise FinalDeltaError("Results root is not the canonical results subtree")
    head = _git(repository_root, "rev-parse", "HEAD")
    if _git(repository_root, "status", "--porcelain"):
        raise FinalDeltaError("Results checkout is not clean")
    try:
        _git(repository_root, "merge-base", "--is-ancestor", commit, head)
    except FinalDeltaError as error:
        raise FinalDeltaError("cutoff commit is not an ancestor of the packet") from error
    if _git(repository_root, "rev-parse", f"{head}:results") != _git(
        repository_root, "rev-parse", f"{commit}:results"
    ):
        raise FinalDeltaError("Results changed after the selected cutoff commit")
    if _git(repository_root, "remote", "get-url", "origin") not in {
        "https://github.com/leanprover/lean-eval-submissions",
        "https://github.com/leanprover/lean-eval-submissions.git",
    }:
        raise FinalDeltaError("Results checkout remote is not canonical")


def _results_bindings(
    root: pathlib.Path, commit: str, expected_store: str, *, verify_git: bool
) -> dict[str, dict[str, Any]]:
    if verify_git:
        _verify_results_checkout(root, commit)
    if not root.is_dir() or root.is_symlink():
        raise FinalDeltaError("Results root must be one real directory")
    bindings: dict[str, dict[str, Any]] = {}
    files: list[tuple[str, dict[str, Any]]] = []
    total_bytes = 0
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name == ".gitkeep":
            continue
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise FinalDeltaError("Results contains a noncanonical root entry")
        total_bytes += path.stat().st_size
        if total_bytes > MAX_RESULTS_BYTES:
            raise FinalDeltaError("Results store exceeds its byte bound")
        try:
            document, version = read_results_file(path)
        except (OSError, UnicodeError, ResultsSchemaError) as error:
            raise FinalDeltaError("Results store is invalid") from error
        raw = path.read_bytes()
        if version != 2 or canonical_file_bytes(document) != raw:
            raise FinalDeltaError("Results store is not canonical schema version 2")
        relative = f"results/{path.name}"
        files.append((relative, document))
        file_binding = {
            "results_path": relative,
            "result_file_sha256": sha256(raw),
            "result_tree_digest": result_tree_digest(relative, raw),
        }
        for record in document["results"]:
            result_id = record["result_id"]
            if result_id in bindings or len(bindings) >= MAX_RESULTS:
                raise FinalDeltaError("Results identity inventory is invalid")
            bindings[result_id] = {
                "owner_login": document["user"].lower(),
                "declared_model": record["declared_model"],
                "problem_id": record["problem_id"],
                "statement_revision": record["statement_revision"],
                "historical_accepted_at": record["accepted_at"],
                "benchmark_commit": record["benchmark_commit"],
                **file_binding,
            }
    try:
        actual_store = canonical_store_digest(files)
    except (ResultsSchemaError, UnicodeError, ValueError) as error:
        raise FinalDeltaError("Results store digest cannot be reproduced") from error
    if actual_store != expected_store:
        raise FinalDeltaError("Results store digest differs from the cutoff inventory")
    return bindings


def _inventory_inputs(
    baseline_path: pathlib.Path,
    current_path: pathlib.Path,
    delta_path: pathlib.Path,
    schema_path: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, bytes]]:
    try:
        schema = _load_schema(schema_path)
        baseline, baseline_raw = _read_canonical_json(baseline_path, "baseline inventory")
        current, current_raw = _read_canonical_json(current_path, "cutoff inventory")
    except (InventoryDeltaError, OSError, ValueError) as error:
        raise FinalDeltaError(str(error)) from error
    delta, delta_raw = _read(delta_path, "cutoff delta")
    derived = reconcile(baseline, baseline_raw, current, current_raw, schema)
    if canonical_delta_bytes(derived) != delta_raw:
        raise FinalDeltaError("cutoff delta is not the exact append-only reconciliation")
    if current["source_commit"] != delta["current"]["source_commit"]:
        raise FinalDeltaError("cutoff inventory and delta source commits differ")
    return baseline, current, delta, {
        "baseline": baseline_raw,
        "current": current_raw,
        "delta": delta_raw,
    }


def _public_decisions(
    path: pathlib.Path, delta: dict[str, Any], delta_raw: bytes
) -> tuple[dict[str, dict[str, Any]], bytes]:
    value, raw = _read(path, "public-source decisions")
    _closed(
        value,
        {
            "schema_version",
            "kind",
            "source_repository",
            "source_commit",
            "results_store_sha256",
            "delta_sha256",
            "entries",
        },
        "public-source decisions",
    )
    if (
        value["schema_version"] != 1
        or value["kind"] != "historical_final_delta_public_source_decisions"
        or value["source_repository"] != RESULTS_REPOSITORY
        or value["source_commit"] != delta["current"]["source_commit"]
        or value["results_store_sha256"] != delta["current"]["results_store_sha256"]
        or value["delta_sha256"] != sha256(delta_raw)
        or not isinstance(value["entries"], list)
    ):
        raise FinalDeltaError("public-source decision identity is invalid")
    expected = {
        item["result_id"]: item
        for item in delta["entries"]
        if item["source"]["visibility"] == "public"
    }
    decisions: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    for item in value["entries"]:
        if not isinstance(item, dict):
            raise FinalDeltaError("public-source decision entry is invalid")
        result_id = item.get("result_id")
        source = expected.get(result_id)
        classification = item.get("classification")
        base_fields = {
            "result_id",
            "source_repository",
            "source_commit",
            "classification",
        }
        if (
            not isinstance(result_id, str)
            or RESULT_ID.fullmatch(result_id) is None
            or result_id in decisions
            or source is None
            or item.get("source_repository") != source["source"]["repository"]
            or item.get("source_commit") != source["source"]["commit"]
        ):
            raise FinalDeltaError("public-source decision does not bind the delta")
        if classification == "available":
            _closed(item, base_fields | {"source_tree"}, "available source decision")
            if COMMIT.fullmatch(str(item["source_tree"])) is None:
                raise FinalDeltaError("available source tree is invalid")
        elif classification == "source_ref_permanently_unavailable":
            _closed(
                item,
                base_fields | {"review_status", "evidence_sha256"},
                "unavailable source decision",
            )
            if item["review_status"] != "reviewed" or DIGEST.fullmatch(
                str(item["evidence_sha256"])
            ) is None:
                raise FinalDeltaError("source unavailability is not reviewed and bound")
        else:
            raise FinalDeltaError("public source remains unclassified")
        decisions[result_id] = item
        ordered.append(result_id)
    if ordered != sorted(expected) or set(decisions) != set(expected):
        raise FinalDeltaError("public-source decisions do not exactly cover the delta")
    return decisions, raw


def _private_crosswalk(
    path: pathlib.Path, schema_path: pathlib.Path, current: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], bytes]:
    value, raw = _read(path, "private archive crosswalk")
    schema = _read_schema(schema_path, "private archive crosswalk schema")
    try:
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(
                schema, format_checker=FormatChecker()
            ).iter_errors(value),
            key=lambda error: list(error.absolute_path),
        )
    except SchemaError as error:
        raise FinalDeltaError("private archive crosswalk schema is invalid") from error
    if errors:
        raise FinalDeltaError("private archive crosswalk fails its exact schema")
    _closed(
        value,
        {
            "schema_version",
            "results_repository",
            "results_commit",
            "results_store_sha256",
            "private_result_count",
            "audit_repository",
            "audit_commit",
            "archive_inventory_digest",
            "archive_count",
            "classification_counts",
            "entries",
        },
        "private archive crosswalk",
    )
    expected_ids = {
        item["result_id"]
        for item in current["entries"]
        if item["source"]["visibility"] == "private"
    }
    if (
        value["schema_version"] != 1
        or value["results_repository"] != RESULTS_REPOSITORY
        or value["results_commit"] != current["source_commit"]
        or value["results_store_sha256"] != current["results_store_sha256"]
        or value["private_result_count"] != len(expected_ids)
        or value["audit_repository"] != AUDIT_REPOSITORY
        or COMMIT.fullmatch(str(value["audit_commit"])) is None
        or DIGEST.fullmatch(str(value["archive_inventory_digest"])) is None
        or not isinstance(value["entries"], list)
    ):
        raise FinalDeltaError("private crosswalk identity is invalid")
    entries: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    counts: Counter[str] = Counter()
    for item in value["entries"]:
        if not isinstance(item, dict):
            raise FinalDeltaError("private crosswalk entry is invalid")
        result_id = item.get("result_id")
        classification = item.get("classification")
        if (
            not isinstance(result_id, str)
            or RESULT_ID.fullmatch(result_id) is None
            or result_id in entries
            or result_id not in expected_ids
            or classification not in {
                "bound",
                "archive_not_found",
                "archive_identity_ambiguous",
                "archive_metadata_conflict",
            }
        ):
            raise FinalDeltaError("private crosswalk entry is invalid")
        entries[result_id] = item
        ordered.append(result_id)
        counts[classification] += 1
    expected_counts = {
        name: counts[name]
        for name in (
            "bound",
            "archive_not_found",
            "archive_identity_ambiguous",
            "archive_metadata_conflict",
        )
    }
    if (
        ordered != sorted(expected_ids)
        or set(entries) != expected_ids
        or value["classification_counts"] != expected_counts
    ):
        raise FinalDeltaError("private crosswalk does not exactly cover current Results")
    return entries, value, raw


def _identity(value: dict[str, Any], raw: bytes) -> dict[str, Any]:
    return {
        "source_commit": value["source_commit"],
        "results_store_sha256": value["results_store_sha256"],
        "inventory_sha256": sha256(raw),
        "result_count": value["result_count"],
    }


def build_packet(
    *,
    baseline_path: pathlib.Path,
    current_path: pathlib.Path,
    delta_path: pathlib.Path,
    inventory_schema_path: pathlib.Path,
    results_root: pathlib.Path,
    public_decisions_path: pathlib.Path,
    private_crosswalk_path: pathlib.Path,
    private_crosswalk_schema_path: pathlib.Path,
    verify_git: bool = True,
) -> dict[str, Any]:
    baseline, current, delta, inventory_raw = _inventory_inputs(
        baseline_path, current_path, delta_path, inventory_schema_path
    )
    results = _results_bindings(
        results_root,
        current["source_commit"],
        current["results_store_sha256"],
        verify_git=verify_git,
    )
    if set(results) != {item["result_id"] for item in current["entries"]}:
        raise FinalDeltaError("Results records do not exactly equal the cutoff inventory")
    public, public_raw = _public_decisions(public_decisions_path, delta, inventory_raw["delta"])
    private, crosswalk, crosswalk_raw = _private_crosswalk(
        private_crosswalk_path, private_crosswalk_schema_path, current
    )

    entries: list[dict[str, Any]] = []
    classifications: Counter[tuple[str, str]] = Counter()
    image_requirements: Counter[tuple[str, str]] = Counter()
    archive_versions: Counter[int] = Counter()
    legacy_archives: set[str] = set()
    migrated_archives: set[str] = set()
    for inventory_entry in delta["entries"]:
        result_id = inventory_entry["result_id"]
        visibility = inventory_entry["source"]["visibility"]
        result = results[result_id]
        if (
            result["owner_login"] != inventory_entry["owner"].lower()
            or result["problem_id"] != inventory_entry["problem_id"]
            or result["statement_revision"] != inventory_entry["statement_revision"]
            or result["historical_accepted_at"] != inventory_entry["accepted_at"]
            or result["benchmark_commit"] != inventory_entry["benchmark_commit"]
            or result["results_path"] != inventory_entry["results_path"]
        ):
            raise FinalDeltaError("cutoff inventory entry differs from its exact Result")
        output: dict[str, Any] = {
            "result_id": result_id,
            "source_visibility": visibility,
            "benchmark_repository": BENCHMARK_REPOSITORY,
            "benchmark_commit": inventory_entry["benchmark_commit"],
            "result": result,
        }
        if visibility == "public":
            decision = public[result_id]
            if decision["classification"] == "available":
                disposition = "replayable"
                output["source"] = {
                    "kind": inventory_entry["source"]["kind"],
                    "repository": decision["source_repository"],
                    "commit": decision["source_commit"],
                    "tree": decision["source_tree"],
                    "decision_entry_sha256": entry_sha256(decision),
                }
                image_requirements[(visibility, inventory_entry["benchmark_commit"])] += 1
            else:
                disposition = "unavailable"
                output["unavailability"] = {
                    "reason_code": decision["classification"],
                    "evidence_sha256": decision["evidence_sha256"],
                    "decision_entry_sha256": entry_sha256(decision),
                }
        else:
            crosswalk_entry = private[result_id]
            classification = crosswalk_entry["classification"]
            if classification == "bound":
                required = {
                    "result_id",
                    "classification",
                    "submission_id",
                    "archive_plan_entry_sha256",
                    "archive_schema_version",
                    "archive_result_evidence",
                    "benchmark_relation",
                }
                _closed(crosswalk_entry, required, "bound private crosswalk entry")
                schema_version = crosswalk_entry["archive_schema_version"]
                submission_id = crosswalk_entry["submission_id"]
                if (
                    UUID7.fullmatch(str(submission_id)) is None
                    or DIGEST.fullmatch(str(crosswalk_entry["archive_plan_entry_sha256"])) is None
                    or schema_version not in {1, 2, 3}
                ):
                    raise FinalDeltaError("bound private archive identity is invalid")
                disposition = "replayable"
                output["archive"] = {
                    key: crosswalk_entry[key]
                    for key in (
                        "submission_id",
                        "archive_plan_entry_sha256",
                        "archive_schema_version",
                        "archive_result_evidence",
                        "benchmark_relation",
                    )
                }
                output["archive"]["crosswalk_entry_sha256"] = entry_sha256(crosswalk_entry)
                archive_versions[schema_version] += 1
                (migrated_archives if schema_version == 3 else legacy_archives).add(submission_id)
                image_requirements[(visibility, inventory_entry["benchmark_commit"])] += 1
            elif classification == "archive_not_found":
                disposition = "unavailable"
                output["unavailability"] = {
                    "reason_code": "archive_not_found",
                    "crosswalk_entry_sha256": entry_sha256(crosswalk_entry),
                }
            else:
                raise FinalDeltaError("private delta has an unresolved archive classification")
        output["disposition"] = disposition
        output["packet_entry_sha256"] = entry_sha256(output)
        classifications[(visibility, disposition)] += 1
        entries.append(output)

    if [item["result_id"] for item in entries] != sorted(
        item["result_id"] for item in entries
    ):
        raise FinalDeltaError("delta entries are not canonically sorted")
    image_rows = [
        {
            "source_visibility": visibility,
            "benchmark_repository": BENCHMARK_REPOSITORY,
            "benchmark_commit": benchmark_commit,
            "result_count": count,
        }
        for (visibility, benchmark_commit), count in sorted(image_requirements.items())
    ]
    result_counts = {
        visibility: {
            "replayable": classifications[(visibility, "replayable")],
            "unavailable": classifications[(visibility, "unavailable")],
            "total": sum(
                classifications[(visibility, disposition)]
                for disposition in ("replayable", "unavailable")
            ),
        }
        for visibility in ("public", "private")
    }
    if (
        result_counts["public"]["total"]
        != delta["delta_counts"]["public_source_probe_pending"]
        or result_counts["private"]["total"]
        != delta["delta_counts"]["private_archive_migration_pending"]
        or sum(row["total"] for row in result_counts.values())
        != delta["delta_counts"]["result_count"]
    ):
        raise FinalDeltaError("final delta classification counts changed")
    return {
        "schema_version": 1,
        "kind": "historical_final_delta_preparation_packet",
        "activation_status": "blocked_pending_exact_profiles_and_state_append",
        "source_repository": RESULTS_REPOSITORY,
        "cutoff": {
            "baseline_inventory": _identity(baseline, inventory_raw["baseline"]),
            "current_inventory": _identity(current, inventory_raw["current"]),
            "delta_sha256": sha256(inventory_raw["delta"]),
            "delta_counts": delta["delta_counts"],
        },
        "classification_inputs": {
            "public_source_decisions_sha256": sha256(public_raw),
            "private_crosswalk": {
                "repository": RESULTS_REPOSITORY,
                "sha256": sha256(crosswalk_raw),
                "audit_repository": AUDIT_REPOSITORY,
                "audit_commit": crosswalk["audit_commit"],
                "archive_inventory_digest": crosswalk["archive_inventory_digest"],
            },
        },
        "classification_counts": result_counts,
        "archive_migration": {
            "legacy_unique_archive_count": len(legacy_archives),
            "migrated_unique_archive_count": len(migrated_archives),
            "bound_result_schema_counts": {
                "1": archive_versions[1],
                "2": archive_versions[2],
                "3": archive_versions[3],
            },
        },
        "image_requirement_count": len(image_rows),
        "image_requirements": image_rows,
        "entries": entries,
    }


def write_exclusive(path: pathlib.Path, value: Any) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise FinalDeltaError("output parent must be one existing real directory")
    try:
        with path.open("xb") as stream:
            stream.write(canonical(value))
    except FileExistsError as error:
        raise FinalDeltaError("refusing to overwrite output") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=pathlib.Path)
    parser.add_argument("--current", required=True, type=pathlib.Path)
    parser.add_argument("--delta", required=True, type=pathlib.Path)
    parser.add_argument("--inventory-schema", required=True, type=pathlib.Path)
    parser.add_argument("--results-root", required=True, type=pathlib.Path)
    parser.add_argument("--public-decisions", required=True, type=pathlib.Path)
    parser.add_argument("--private-crosswalk", required=True, type=pathlib.Path)
    parser.add_argument("--private-crosswalk-schema", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        packet = build_packet(
            baseline_path=args.baseline.resolve(),
            current_path=args.current.resolve(),
            delta_path=args.delta.resolve(),
            inventory_schema_path=args.inventory_schema.resolve(),
            results_root=args.results_root.resolve(),
            public_decisions_path=args.public_decisions.resolve(),
            private_crosswalk_path=args.private_crosswalk.resolve(),
            private_crosswalk_schema_path=args.private_crosswalk_schema.resolve(),
        )
        write_exclusive(args.output.resolve(), packet)
    except (FinalDeltaError, InventoryDeltaError, OSError, TypeError, ValueError) as error:
        print(f"historical-final-delta-packet: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
