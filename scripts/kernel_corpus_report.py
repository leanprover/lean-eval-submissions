#!/usr/bin/env python3
"""Prepare and aggregate source-free independent-kernel corpus evidence.

This contract is downstream of the historical replay inventory.  It never
fetches source, runs a checker, writes State, or approves checker promotion.
It binds offline observations to one exact inventory and checker-series
configuration and turns complete deterministic shards into a blocking report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import secrets
import stat
import sys
from typing import Any

DIGEST = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}")
REPLAY_TASK_ID = re.compile(r"rt1_[0-9a-f]{64}")
CONFIGURATION_ID = re.compile(r"kcc1_[0-9a-f]{64}")
INVENTORY_ID = re.compile(r"kci1_[0-9a-f]{64}")
ATTEMPT_ID = re.compile(r"kca1_[0-9a-f]{64}")
SHARD_ID = re.compile(r"ksh1_[0-9a-f]{64}")
SERIES_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}")
SAFE_NAME = re.compile(r"[A-Za-z0-9_.+-]{1,128}")
TOOLCHAIN = re.compile(
    r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta)[0-9]+)?"
)
PROBLEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z"
)

TERMINAL_OUTCOMES = ("accepted", "rejected", "declined", "crashed", "timed_out")
CANDIDATE_TERMINAL_OUTCOMES = ("accepted", "declined", "crashed", "timed_out")
AVAILABILITIES = (
    "ready",
    "replay_pending",
    "source_unavailable",
    "replay_unavailable",
)
UNAVAILABLE_OUTCOMES = (
    "source_unavailable",
    "replay_unavailable",
    "export_unavailable",
)
PENDING_OUTCOMES = ("replay_pending", "export_format_unsupported")

SERIES_FIELDS = {
    "schema_version",
    "series_name",
    "configuration_id",
    "candidate",
    "producer_profiles",
    "checker",
    "runner",
}
INVENTORY_FIELDS = {
    "schema_version",
    "inventory_id",
    "cutoff_at",
    "results_store",
    "historical_replay_report_sha256",
    "results",
}
INVENTORY_RESULT_FIELDS = {
    "result_id",
    "replay_task_id",
    "replay_attempt",
    "problem_id",
    "statement_revision",
    "benchmark_repository",
    "benchmark_commit",
    "benchmark_configuration_sha256",
    "terminal_verdict_sha256",
    "terminal_event_sha256",
    "report_entry_sha256",
    "replay_export_input_sha256",
    "authoritative_outcome",
    "availability",
    "unavailability_evidence_sha256",
}
PLAN_FIELDS = {
    "schema_version",
    "kind",
    "configuration_id",
    "configuration_sha256",
    "inventory_id",
    "inventory_sha256",
    "shard_index",
    "shard_count",
    "shard_id",
    "attempts",
}
ATTEMPT_FIELDS = {
    "result_id",
    "replay_task_id",
    "replay_attempt",
    "problem_id",
    "statement_revision",
    "benchmark_repository",
    "benchmark_commit",
    "benchmark_configuration_sha256",
    "terminal_verdict_sha256",
    "terminal_event_sha256",
    "report_entry_sha256",
    "replay_export_input_sha256",
    "attempt_id",
    "required_action",
}
OBSERVATION_SHARD_FIELDS = PLAN_FIELDS - {"attempts"} | {"observations"}
OBSERVATION_FIELDS = {
    "result_id",
    "replay_task_id",
    "replay_attempt",
    "attempt_id",
    "status",
    "outcome",
    "evidence_sha256",
    "statistics",
    "execution_receipt",
}
STATISTICS_FIELDS = {
    "wall_time_ms",
    "peak_memory_bytes",
    "checker_invocations",
}
EXECUTION_RECEIPT_FIELDS = {
    "schema_version",
    "receipt_sha256",
    "attempt_id",
    "input_sha256",
    "configuration_id",
    "configuration_sha256",
    "outcome",
    "resource_limit_disposition",
    "statistics",
    "transcript_sha256",
    "runner_attestation_sha256",
    "source_free",
}

MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 1_000_000
MAX_SHARD_DIRECTORY_BYTES = 256 * 1024 * 1024
MAX_SHARD_DIRECTORY_NODES = 4_000_000
MAX_SHARDS = 4_096
MAX_CHECKER_INVOCATIONS = 1_000_000
SCHEMA_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "schemas"
SCHEMA_FILES = {
    "series": "kernel-checker-series-v1.schema.json",
    "inventory": "kernel-corpus-inventory-v1.schema.json",
    "plan": "kernel-corpus-shard-plan-v1.schema.json",
    "runner_records": "kernel-corpus-runner-records-v1.schema.json",
    "observations": "kernel-corpus-observations-v1.schema.json",
    "report": "kernel-corpus-report-v1.schema.json",
}
_SCHEMA_VALIDATORS: dict[str, Any] = {}


class KernelCorpusError(ValueError):
    """An independent-kernel corpus artifact violates the contract."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _identity(prefix: str, value: Any) -> str:
    return prefix + _digest(value)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise KernelCorpusError(f"{label} must be an object with string keys")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise KernelCorpusError(f"{label} must be an array")
    return value


def _fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise KernelCorpusError(
            f"{label} fields are not canonical; "
            f"missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _string(value: Any, label: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise KernelCorpusError(
            f"{label} must be a non-empty string of at most {maximum} UTF-8 bytes"
        )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise KernelCorpusError(f"{label} must not contain control characters")
    return value


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    text = _string(value, label)
    if pattern.fullmatch(text) is None:
        raise KernelCorpusError(f"{label} is not canonical")
    return text


def _timestamp(value: Any, label: str) -> str:
    text = _match(TIMESTAMP, value, label)
    try:
        dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.UTC)
    except ValueError as error:
        raise KernelCorpusError(f"{label} is not a real UTC timestamp") from error
    return text


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise KernelCorpusError(f"{label} must be an integer >= {minimum}")
    if value > 9_007_199_254_740_991:
        raise KernelCorpusError(f"{label} must be IEEE-754 safe")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise KernelCorpusError(f"duplicate JSON object key: {key}")
        output[key] = value
    return output


def _reject_nonfinite_number(token: str) -> Any:
    raise KernelCorpusError(f"non-finite JSON number: {token}")


def _check_json_complexity(value: Any) -> int:
    stack = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise KernelCorpusError("JSON artifact exceeds the node-count limit")
        if depth > MAX_JSON_DEPTH:
            raise KernelCorpusError("JSON artifact exceeds the nesting-depth limit")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return nodes


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _load_with_metrics(
    path: pathlib.Path,
    *,
    directory_fd: int | None = None,
    maximum: int = MAX_JSON_BYTES,
) -> tuple[Any, int, int]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(
            path.name if directory_fd is not None else path,
            flags,
            dir_fd=directory_fd,
        )
    except OSError as error:
        raise KernelCorpusError(f"{path}: cannot read JSON: {error}") from error
    try:
        metadata_before = os.fstat(descriptor)
        if not stat.S_ISREG(metadata_before.st_mode):
            raise KernelCorpusError(f"{path}: JSON input must be a regular file")
        if metadata_before.st_size > maximum:
            raise KernelCorpusError(f"{path}: JSON input exceeds the byte limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1_048_576, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise KernelCorpusError(f"{path}: JSON input exceeds the byte limit")
        metadata_after = os.fstat(descriptor)
        if (
            _stat_identity(metadata_before) != _stat_identity(metadata_after)
            or total != metadata_before.st_size
        ):
            raise KernelCorpusError(f"{path}: JSON input changed while it was read")
    except OSError as error:
        raise KernelCorpusError(f"{path}: cannot read JSON: {error}") from error
    finally:
        os.close(descriptor)
    try:
        value = json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_number,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise KernelCorpusError(f"{path}: cannot parse JSON: {error}") from error
    nodes = _check_json_complexity(value)
    return value, total, nodes


def _load(path: pathlib.Path) -> Any:
    return _load_with_metrics(path)[0]


def _validate_schema(value: Any, kind: str) -> None:
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError, ValidationError
    except ImportError as error:
        raise KernelCorpusError(
            "jsonschema is required for kernel corpus artifact validation"
        ) from error
    validator = _SCHEMA_VALIDATORS.get(kind)
    if validator is None:
        try:
            schema = _load(SCHEMA_DIRECTORY / SCHEMA_FILES[kind])
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
        except (KeyError, SchemaError) as error:
            raise KernelCorpusError(
                f"{kind} JSON Schema is invalid: {error}"
            ) from error
        _SCHEMA_VALIDATORS[kind] = validator
    try:
        validator.validate(value)
    except ValidationError as error:
        location = ".".join(str(item) for item in error.absolute_path) or "root"
        raise KernelCorpusError(
            f"{kind} fails JSON Schema at {location}: {error.message}"
        ) from error


def _component(value: Any, label: str, extra_fields: set[str]) -> dict[str, Any]:
    component = _object(value, label)
    _fields(component, {"repository", "commit", *extra_fields}, label)
    repository = _match(REPOSITORY, component["repository"], f"{label}.repository")
    if any(segment in {".", ".."} for segment in repository.split("/")):
        raise KernelCorpusError(f"{label}.repository contains a path segment")
    _match(COMMIT, component["commit"], f"{label}.commit")
    return component


def configuration_id(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "configuration_id"}
    return _identity("kcc1_", body)


def validate_series(value: Any) -> dict[str, Any]:
    series = _object(value, "series")
    _fields(series, SERIES_FIELDS, "series")
    if series["schema_version"] != 1 or isinstance(series["schema_version"], bool):
        raise KernelCorpusError("series schema_version must be integer 1")
    _match(SERIES_NAME, series["series_name"], "series.series_name")
    _match(CONFIGURATION_ID, series["configuration_id"], "series.configuration_id")

    candidate = _component(
        series["candidate"],
        "series.candidate",
        {"name", "binary_sha256", "protocol", "configuration_policy_sha256"},
    )
    _match(SAFE_NAME, candidate["name"], "series.candidate.name")
    _match(DIGEST, candidate["binary_sha256"], "series.candidate.binary_sha256")
    _match(SAFE_NAME, candidate["protocol"], "series.candidate.protocol")
    _match(
        DIGEST,
        candidate["configuration_policy_sha256"],
        "series.candidate.configuration_policy_sha256",
    )

    profiles = _array(series["producer_profiles"], "series.producer_profiles")
    if not profiles:
        raise KernelCorpusError("series.producer_profiles must not be empty")
    profile_keys: list[tuple[str, str]] = []
    for index, raw in enumerate(profiles):
        label = f"series.producer_profiles[{index}]"
        profile = _object(raw, label)
        _fields(
            profile,
            {"benchmark_repository", "benchmark_commit", "exporter", "lean"},
            label,
        )
        repository = _match(
            REPOSITORY,
            profile["benchmark_repository"],
            f"{label}.benchmark_repository",
        )
        if repository != "leanprover/lean-eval":
            raise KernelCorpusError(
                f"{label}.benchmark_repository is not registered"
            )
        benchmark_commit = _match(
            COMMIT, profile["benchmark_commit"], f"{label}.benchmark_commit"
        )
        profile_keys.append((repository, benchmark_commit))

        exporter = _component(
            profile["exporter"],
            f"{label}.exporter",
            {
                "binary_sha256",
                "name",
                "version",
                "format_version",
                "format_specification_sha256",
                "format_specification_path",
            },
        )
        if (
            exporter["repository"] != "leanprover/lean4export"
            or exporter["name"] != "lean4export"
            or exporter["format_version"] != "3.1.0"
            or exporter["format_specification_sha256"]
            != "f82a21e17e4258a1043895d0653ea4333bef8cb07aad2e3d6c1fc4be52b138e3"
            or exporter["format_specification_path"] != "format_ndjson.md"
        ):
            raise KernelCorpusError(f"{label}.exporter is not registered")
        _match(DIGEST, exporter["binary_sha256"], f"{label}.exporter.binary_sha256")
        _string(exporter["version"], f"{label}.exporter.version", 64)

        lean = _object(profile["lean"], f"{label}.lean")
        _fields(lean, {"toolchain", "version", "githash"}, f"{label}.lean")
        _match(TOOLCHAIN, lean["toolchain"], f"{label}.lean.toolchain")
        _string(lean["version"], f"{label}.lean.version", 64)
        _match(COMMIT, lean["githash"], f"{label}.lean.githash")
    if profile_keys != sorted(profile_keys):
        raise KernelCorpusError("series.producer_profiles must be sorted")
    if len(set(profile_keys)) != len(profile_keys):
        raise KernelCorpusError(
            "series.producer_profiles contains a duplicate benchmark identity"
        )

    checker = _component(
        series["checker"],
        "series.checker",
        {"protocol"},
    )
    _match(SAFE_NAME, checker["protocol"], "series.checker.protocol")

    runner = _component(
        series["runner"],
        "series.runner",
        {"image_digest", "architecture", "operating_system", "resource_limits"},
    )
    image_digest = _string(runner["image_digest"], "series.runner.image_digest")
    if (
        not image_digest.startswith("sha256:")
        or DIGEST.fullmatch(image_digest[7:]) is None
    ):
        raise KernelCorpusError("series.runner.image_digest is not canonical")
    _match(SAFE_NAME, runner["architecture"], "series.runner.architecture")
    _match(SAFE_NAME, runner["operating_system"], "series.runner.operating_system")
    limits = _object(runner["resource_limits"], "series.runner.resource_limits")
    _fields(
        limits,
        {"wall_timeout_seconds", "max_memory_bytes"},
        "series.runner.resource_limits",
    )
    wall_timeout = _integer(limits["wall_timeout_seconds"], "wall_timeout_seconds", 1)
    if wall_timeout > 9_007_199_254_740:
        raise KernelCorpusError("wall_timeout_seconds cannot overflow milliseconds")
    _integer(limits["max_memory_bytes"], "max_memory_bytes", 1)

    if series["configuration_id"] != configuration_id(series):
        raise KernelCorpusError(
            "series.configuration_id does not bind the exact series"
        )
    _validate_schema(series, "series")
    return series


def inventory_id(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "inventory_id"}
    return _identity("kci1_", body)


def validate_inventory(value: Any) -> dict[str, Any]:
    inventory = _object(value, "inventory")
    _fields(inventory, INVENTORY_FIELDS, "inventory")
    if inventory["schema_version"] != 1 or isinstance(
        inventory["schema_version"], bool
    ):
        raise KernelCorpusError("inventory schema_version must be integer 1")
    _match(INVENTORY_ID, inventory["inventory_id"], "inventory.inventory_id")
    _timestamp(inventory["cutoff_at"], "inventory.cutoff_at")
    store = _component(
        inventory["results_store"], "inventory.results_store", {"tree_sha256"}
    )
    _match(DIGEST, store["tree_sha256"], "inventory.results_store.tree_sha256")
    _match(
        DIGEST,
        inventory["historical_replay_report_sha256"],
        "inventory.historical_replay_report_sha256",
    )
    results = _array(inventory["results"], "inventory.results")
    if not results:
        raise KernelCorpusError("inventory.results must not be empty")
    identifiers: list[str] = []
    replay_tasks: list[str] = []
    for index, raw in enumerate(results):
        label = f"inventory.results[{index}]"
        result = _object(raw, label)
        _fields(result, INVENTORY_RESULT_FIELDS, label)
        identifiers.append(_match(RESULT_ID, result["result_id"], f"{label}.result_id"))
        replay_tasks.append(
            _match(
                REPLAY_TASK_ID,
                result["replay_task_id"],
                f"{label}.replay_task_id",
            )
        )
        _integer(result["replay_attempt"], f"{label}.replay_attempt", 1)
        _match(PROBLEM, result["problem_id"], f"{label}.problem_id")
        _integer(result["statement_revision"], f"{label}.statement_revision", 1)
        if result["benchmark_repository"] != "leanprover/lean-eval":
            raise KernelCorpusError(
                f"{label}.benchmark_repository is not registered"
            )
        _match(COMMIT, result["benchmark_commit"], f"{label}.benchmark_commit")
        _match(
            DIGEST,
            result["benchmark_configuration_sha256"],
            f"{label}.benchmark_configuration_sha256",
        )
        availability = result["availability"]
        if availability not in AVAILABILITIES:
            raise KernelCorpusError(f"{label}.availability is not registered")
        outcome = result["authoritative_outcome"]
        evidence = result["unavailability_evidence_sha256"]
        terminal_digests = (
            result["terminal_verdict_sha256"],
            result["terminal_event_sha256"],
            result["report_entry_sha256"],
        )
        replay_input = result["replay_export_input_sha256"]
        if availability == "ready":
            if outcome not in TERMINAL_OUTCOMES:
                raise KernelCorpusError(
                    f"{label} ready result requires a terminal outcome"
                )
            if evidence is not None:
                raise KernelCorpusError(
                    f"{label} ready result cannot claim unavailability"
                )
            for field in (
                "terminal_verdict_sha256",
                "terminal_event_sha256",
                "report_entry_sha256",
                "replay_export_input_sha256",
            ):
                _match(DIGEST, result[field], f"{label}.{field}")
        elif availability == "replay_pending":
            if (
                outcome is not None
                or evidence is not None
                or replay_input is not None
                or any(item is not None for item in terminal_digests)
            ):
                raise KernelCorpusError(
                    f"{label} pending replay cannot claim terminal/input evidence"
                )
        else:
            if outcome is not None:
                raise KernelCorpusError(
                    f"{label} unavailable result cannot claim a terminal outcome"
                )
            _match(DIGEST, evidence, f"{label}.unavailability_evidence_sha256")
            if replay_input is not None or any(
                item is not None for item in terminal_digests
            ):
                raise KernelCorpusError(
                    f"{label} unavailable result cannot claim terminal/input evidence"
                )
    if identifiers != sorted(identifiers):
        raise KernelCorpusError("inventory.results must be sorted by result_id")
    if len(set(identifiers)) != len(identifiers):
        raise KernelCorpusError("inventory contains duplicate result_id values")
    if len(set(replay_tasks)) != len(replay_tasks):
        raise KernelCorpusError("inventory contains duplicate replay_task_id values")
    if inventory["inventory_id"] != inventory_id(inventory):
        raise KernelCorpusError(
            "inventory.inventory_id does not bind the exact inventory"
        )
    _validate_schema(inventory, "inventory")
    return inventory


def attempt_id(
    series: dict[str, Any], inventory: dict[str, Any], result: dict[str, Any]
) -> str:
    return _identity(
        "kca1_",
        {
            "configuration_id": series["configuration_id"],
            "inventory_id": inventory["inventory_id"],
            "replay_task_id": result["replay_task_id"],
            "replay_attempt": result["replay_attempt"],
            "problem_id": result["problem_id"],
            "statement_revision": result["statement_revision"],
            "benchmark_repository": result["benchmark_repository"],
            "benchmark_commit": result["benchmark_commit"],
            "benchmark_configuration_sha256": result[
                "benchmark_configuration_sha256"
            ],
            "terminal_verdict_sha256": result["terminal_verdict_sha256"],
            "terminal_event_sha256": result["terminal_event_sha256"],
            "report_entry_sha256": result["report_entry_sha256"],
            "replay_export_input_sha256": result["replay_export_input_sha256"],
            "result_id": result["result_id"],
        },
    )


def _shard_index(result_id: str, shard_count: int) -> int:
    return int(hashlib.sha256(result_id.encode("ascii")).hexdigest(), 16) % shard_count


def _plan_without_id(
    configuration_id: str,
    configuration_sha256: str,
    inventory_id: str,
    inventory_sha256: str,
    shard_index: int,
    shard_count: int,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "kernel_corpus_shard_plan",
        "configuration_id": configuration_id,
        "configuration_sha256": configuration_sha256,
        "inventory_id": inventory_id,
        "inventory_sha256": inventory_sha256,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "attempts": attempts,
    }


def build_shard_plans(
    series_value: Any, inventory_value: Any, shard_count: int
) -> list[dict[str, Any]]:
    series = validate_series(series_value)
    inventory = validate_inventory(inventory_value)
    producer_profile_keys = {
        (profile["benchmark_repository"], profile["benchmark_commit"])
        for profile in series["producer_profiles"]
    }
    missing_profiles = sorted(
        {
            (result["benchmark_repository"], result["benchmark_commit"])
            for result in inventory["results"]
            if result["availability"] == "ready"
            and (result["benchmark_repository"], result["benchmark_commit"])
            not in producer_profile_keys
        }
    )
    if missing_profiles:
        raise KernelCorpusError(
            "series producer profiles do not cover every ready benchmark"
        )
    _integer(shard_count, "shard_count", 1)
    if shard_count > MAX_SHARDS:
        raise KernelCorpusError(f"shard_count cannot exceed {MAX_SHARDS}")
    if shard_count > len(inventory["results"]):
        raise KernelCorpusError("shard_count cannot exceed inventory result count")
    configuration_sha256 = _digest(series)
    inventory_sha256 = _digest(inventory)
    assigned: list[list[dict[str, Any]]] = [[] for _ in range(shard_count)]
    for result in inventory["results"]:
        action = {
            "ready": "run",
            "replay_pending": "record_replay_pending",
            "source_unavailable": "record_source_unavailable",
            "replay_unavailable": "record_replay_unavailable",
        }[result["availability"]]
        assigned[_shard_index(result["result_id"], shard_count)].append(
            {
                "result_id": result["result_id"],
                "replay_task_id": result["replay_task_id"],
                "replay_attempt": result["replay_attempt"],
                "problem_id": result["problem_id"],
                "statement_revision": result["statement_revision"],
                "benchmark_repository": result["benchmark_repository"],
                "benchmark_commit": result["benchmark_commit"],
                "benchmark_configuration_sha256": result[
                    "benchmark_configuration_sha256"
                ],
                "terminal_verdict_sha256": result["terminal_verdict_sha256"],
                "terminal_event_sha256": result["terminal_event_sha256"],
                "report_entry_sha256": result["report_entry_sha256"],
                "replay_export_input_sha256": result["replay_export_input_sha256"],
                "attempt_id": attempt_id(series, inventory, result),
                "required_action": action,
            }
        )
    plans = []
    for index, attempts in enumerate(assigned):
        body = _plan_without_id(
            series["configuration_id"],
            configuration_sha256,
            inventory["inventory_id"],
            inventory_sha256,
            index,
            shard_count,
            attempts,
        )
        plans.append({**body, "shard_id": _identity("ksh1_", body)})
    for plan in plans:
        _validate_schema(plan, "plan")
    return plans


def _plan_shape(value: Any) -> tuple[dict[str, Any], int, int]:
    plan = _object(value, "plan")
    _fields(plan, PLAN_FIELDS, "plan")
    _match(SHARD_ID, plan["shard_id"], "plan.shard_id")
    shard_count = _integer(plan["shard_count"], "plan.shard_count", 1)
    shard_index = _integer(plan["shard_index"], "plan.shard_index")
    if shard_index >= shard_count:
        raise KernelCorpusError("plan.shard_index is outside shard_count")
    return plan, shard_count, shard_index


def _validate_plan_against(value: Any, expected: dict[str, Any]) -> dict[str, Any]:
    plan, _, _ = _plan_shape(value)
    if plan != expected:
        raise KernelCorpusError("plan does not match the deterministic shard")
    _validate_schema(plan, "plan")
    return plan


def validate_plan(
    value: Any,
    series_value: Any,
    inventory_value: Any,
) -> dict[str, Any]:
    _, shard_count, shard_index = _plan_shape(value)
    expected = build_shard_plans(series_value, inventory_value, shard_count)[
        shard_index
    ]
    return _validate_plan_against(value, expected)


def _validate_statistics(value: Any, label: str) -> dict[str, Any]:
    statistics = _object(value, label)
    _fields(statistics, STATISTICS_FIELDS, label)
    _integer(statistics["wall_time_ms"], f"{label}.wall_time_ms")
    _integer(statistics["peak_memory_bytes"], f"{label}.peak_memory_bytes")
    invocations = _integer(
        statistics["checker_invocations"], f"{label}.checker_invocations"
    )
    if invocations > MAX_CHECKER_INVOCATIONS:
        raise KernelCorpusError(
            f"{label}.checker_invocations exceeds the contract limit"
        )
    return statistics


def execution_receipt_sha256(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    return _digest(body)


def validate_execution_receipt(
    value: Any,
    observation: dict[str, Any],
    expected: dict[str, Any],
    source: dict[str, Any],
    series: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    receipt = _object(value, label)
    _fields(receipt, EXECUTION_RECEIPT_FIELDS, label)
    if receipt["schema_version"] != 1 or isinstance(receipt["schema_version"], bool):
        raise KernelCorpusError(f"{label}.schema_version must be integer 1")
    _match(DIGEST, receipt["receipt_sha256"], f"{label}.receipt_sha256")
    _match(DIGEST, receipt["transcript_sha256"], f"{label}.transcript_sha256")
    _match(
        DIGEST,
        receipt["runner_attestation_sha256"],
        f"{label}.runner_attestation_sha256",
    )
    if receipt["source_free"] is not True:
        raise KernelCorpusError(f"{label}.source_free must be true")
    exact = {
        "attempt_id": expected["attempt_id"],
        "input_sha256": source["replay_export_input_sha256"],
        "configuration_id": series["configuration_id"],
        "configuration_sha256": _digest(series),
        "outcome": observation["outcome"],
    }
    for field, expected_value in exact.items():
        if receipt[field] != expected_value:
            raise KernelCorpusError(f"{label}.{field} does not bind the execution")
    statistics = _validate_statistics(receipt["statistics"], f"{label}.statistics")
    if observation["statistics"] != statistics:
        raise KernelCorpusError(f"{label}.statistics does not match the observation")
    limits = series["runner"]["resource_limits"]
    timeout_ms = limits["wall_timeout_seconds"] * 1_000
    maximum_memory = limits["max_memory_bytes"]
    if statistics["wall_time_ms"] > timeout_ms:
        raise KernelCorpusError(f"{label}.statistics exceeds the series wall timeout")
    if statistics["peak_memory_bytes"] > maximum_memory:
        raise KernelCorpusError(f"{label}.statistics exceeds the series memory limit")
    disposition = receipt["resource_limit_disposition"]
    outcome = observation["outcome"]
    if outcome in {"export_unavailable", "export_format_unsupported"}:
        if statistics["checker_invocations"] != 0:
            raise KernelCorpusError(
                f"{label} export outcome must record zero checker invocations"
            )
    elif statistics["checker_invocations"] < 1:
        raise KernelCorpusError(
            f"{label} checker outcome must record at least one checker invocation"
        )
    if disposition == "wall_timeout":
        if outcome != "timed_out" or statistics["wall_time_ms"] != timeout_ms:
            raise KernelCorpusError(
                f"{label} has an inconsistent wall-time disposition"
            )
    elif disposition == "memory_limit":
        if outcome != "crashed" or statistics["peak_memory_bytes"] != maximum_memory:
            raise KernelCorpusError(f"{label} has an inconsistent memory disposition")
    elif disposition == "within_limits":
        if outcome == "timed_out":
            raise KernelCorpusError(f"{label} timed_out must record wall_timeout")
    else:
        raise KernelCorpusError(f"{label}.resource_limit_disposition is not registered")
    if receipt["receipt_sha256"] != execution_receipt_sha256(receipt):
        raise KernelCorpusError(f"{label}.receipt_sha256 does not bind the receipt")
    return receipt


def _validate_observation_shard_against(
    value: Any,
    plan: dict[str, Any],
    series: dict[str, Any],
    by_result: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    shard = _object(value, "observation shard")
    _fields(shard, OBSERVATION_SHARD_FIELDS, "observation shard")
    for field in PLAN_FIELDS - {"attempts", "kind"}:
        if shard[field] != plan[field]:
            raise KernelCorpusError(
                f"observation shard {field} does not match its plan"
            )
    if shard["kind"] != "kernel_corpus_observations":
        raise KernelCorpusError("observation shard kind is not registered")
    observations = _array(shard["observations"], "observation shard.observations")
    if len(observations) != len(plan["attempts"]):
        raise KernelCorpusError(
            "observation shard does not cover every planned attempt"
        )
    for index, (raw, expected) in enumerate(
        zip(observations, plan["attempts"], strict=True)
    ):
        label = f"observation shard.observations[{index}]"
        observation = _object(raw, label)
        _fields(observation, OBSERVATION_FIELDS, label)
        for field in ("result_id", "replay_task_id", "replay_attempt", "attempt_id"):
            if observation[field] != expected[field]:
                raise KernelCorpusError(
                    f"{label}.{field} does not match the planned attempt"
                )
        status = observation["status"]
        outcome = observation["outcome"]
        evidence = observation["evidence_sha256"]
        statistics = observation["statistics"]
        receipt = observation["execution_receipt"]
        source = by_result[observation["result_id"]]
        required = expected["required_action"]
        if status == "completed":
            if required != "run":
                raise KernelCorpusError(f"{label} cannot complete an unavailable input")
            if outcome not in CANDIDATE_TERMINAL_OUTCOMES:
                raise KernelCorpusError(f"{label}.outcome is not a terminal outcome")
            if evidence is not None:
                raise KernelCorpusError(
                    f"{label} completed outcome cannot claim unavailability"
                )
            _validate_statistics(statistics, f"{label}.statistics")
        elif status == "unavailable":
            if outcome not in UNAVAILABLE_OUTCOMES:
                raise KernelCorpusError(
                    f"{label}.outcome is not an unavailable outcome"
                )
            expected_unavailable = {
                "run": "export_unavailable",
                "record_source_unavailable": "source_unavailable",
                "record_replay_unavailable": "replay_unavailable",
            }.get(required)
            if outcome != expected_unavailable:
                raise KernelCorpusError(
                    f"{label} changes the planned availability class"
                )
            _match(DIGEST, evidence, f"{label}.evidence_sha256")
            if (
                required != "run"
                and evidence != source["unavailability_evidence_sha256"]
            ):
                raise KernelCorpusError(
                    f"{label} does not preserve inherited unavailability evidence"
                )
            if required != "run" and statistics is not None:
                raise KernelCorpusError(
                    f"{label} inherited unavailability cannot claim statistics"
                )
        elif status == "pending":
            expected_pending = {
                "run": "export_format_unsupported",
                "record_replay_pending": "replay_pending",
            }.get(required)
            if outcome != expected_pending:
                raise KernelCorpusError(
                    f"{label} changes the planned pending/export-format class"
                )
            if outcome == "replay_pending":
                if evidence is not None:
                    raise KernelCorpusError(
                        f"{label} pending replay cannot claim evidence"
                    )
            else:
                _match(DIGEST, evidence, f"{label}.evidence_sha256")
            if required != "run" and statistics is not None:
                raise KernelCorpusError(
                    f"{label} inherited pending outcome cannot claim statistics"
                )
        else:
            raise KernelCorpusError(f"{label}.status is not registered")
        if required == "run":
            validate_execution_receipt(
                receipt,
                observation,
                expected,
                source,
                series,
                f"{label}.execution_receipt",
            )
        elif receipt is not None:
            raise KernelCorpusError(
                f"{label} inherited pending/unavailability cannot claim execution"
            )
    _validate_schema(shard, "observations")
    return shard


def validate_observation_shard(
    value: Any,
    plan_value: Any,
    series_value: Any,
    inventory_value: Any,
) -> dict[str, Any]:
    series = validate_series(series_value)
    inventory = validate_inventory(inventory_value)
    plan = validate_plan(plan_value, series, inventory)
    by_result = {result["result_id"]: result for result in inventory["results"]}
    return _validate_observation_shard_against(value, plan, series, by_result)


def _quantile(values: list[int], numerator: int, denominator: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) * numerator + denominator - 1) // denominator
    return ordered[max(rank, 1) - 1]


def _upper_median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _safe_sum(values: list[int], label: str) -> int:
    return _integer(sum(values), label)


def _performance(observations: list[dict[str, Any]]) -> dict[str, Any]:
    terminal = [item for item in observations if item["status"] == "completed"]
    wall = [item["statistics"]["wall_time_ms"] for item in terminal]
    memory = [item["statistics"]["peak_memory_bytes"] for item in terminal]
    invocations = [item["statistics"]["checker_invocations"] for item in terminal]
    return {
        "sample_count": _integer(len(terminal), "performance.sample_count"),
        "wall_time_ms": {
            "minimum": min(wall) if wall else None,
            "maximum": max(wall) if wall else None,
            "median_upper": _upper_median(wall),
            "p95_nearest_rank": _quantile(wall, 95, 100),
            "sum": _safe_sum(wall, "performance.wall_time_ms.sum"),
        },
        "peak_memory_bytes": {
            "maximum": max(memory) if memory else None,
        },
        "checker_invocations": {
            "sum": _safe_sum(invocations, "performance.checker_invocations.sum")
        },
    }


def aggregate_report(
    series_value: Any,
    inventory_value: Any,
    plan_values: list[Any],
    observation_values: list[Any],
) -> dict[str, Any]:
    series = validate_series(series_value)
    inventory = validate_inventory(inventory_value)
    if not plan_values:
        raise KernelCorpusError("at least one shard plan is required")
    _, shard_count, _ = _plan_shape(plan_values[0])
    if len(plan_values) != shard_count:
        raise KernelCorpusError("plan set does not contain every shard")
    expected_plans = build_shard_plans(series, inventory, shard_count)
    plans = []
    for index, value in enumerate(plan_values):
        _, selected_count, selected_index = _plan_shape(value)
        if selected_count != shard_count:
            raise KernelCorpusError("plan set mixes shard counts")
        if selected_index != index:
            raise KernelCorpusError(
                "plans must be ordered and cover every shard exactly once"
            )
        plans.append(_validate_plan_against(value, expected_plans[index]))
    if len(observation_values) != shard_count:
        raise KernelCorpusError("observation set does not contain every shard")
    by_result = {result["result_id"]: result for result in inventory["results"]}
    shards = [
        _validate_observation_shard_against(value, plan, series, by_result)
        for value, plan in zip(observation_values, plans, strict=True)
    ]
    observations = [item for shard in shards for item in shard["observations"]]
    result_ids = [item["result_id"] for item in observations]
    expected_ids = [item["result_id"] for item in inventory["results"]]
    if sorted(result_ids) != expected_ids or len(set(result_ids)) != len(result_ids):
        raise KernelCorpusError("observation set omits or duplicates inventory results")

    counters = {
        outcome: 0
        for outcome in (*TERMINAL_OUTCOMES, *UNAVAILABLE_OUTCOMES, *PENDING_OUTCOMES)
    }
    for item in observations:
        counters[item["outcome"]] += 1
    authoritative = {
        item["result_id"]: item["authoritative_outcome"]
        for item in inventory["results"]
    }
    disagreements = [
        {
            "result_id": item["result_id"],
            "authoritative_outcome": authoritative[item["result_id"]],
            "candidate_outcome": item["outcome"],
            "adjudication": "required",
        }
        for item in observations
        if item["status"] == "completed"
        and item["outcome"] != authoritative[item["result_id"]]
    ]
    unavailable_count = sum(counters[outcome] for outcome in UNAVAILABLE_OUTCOMES)
    pending_count = sum(counters[outcome] for outcome in PENDING_OUTCOMES)
    blocking_reasons = ["human_promotion_review_required"]
    if unavailable_count:
        blocking_reasons.append("corpus_unavailable_results")
    if pending_count:
        blocking_reasons.append("corpus_pending_results")
    if counters["export_format_unsupported"]:
        blocking_reasons.append("export_format_review_required")
    if disagreements:
        blocking_reasons.append("disagreement_adjudication_required")
    report = {
        "schema_version": 1,
        "kind": "kernel_corpus_report",
        "configuration_id": series["configuration_id"],
        "configuration_sha256": _digest(series),
        "inventory_id": inventory["inventory_id"],
        "inventory_sha256": _digest(inventory),
        "shard_count": shard_count,
        "plan_set_sha256": _digest(plans),
        "observation_set_sha256": _digest(shards),
        "coverage": {
            "inventory_results": len(inventory["results"]),
            "observations": len(observations),
            "complete": True,
        },
        "counters": counters,
        "performance": _performance(observations),
        "disagreements": sorted(disagreements, key=lambda item: item["result_id"]),
        "promotion": {
            "automated_eligibility": False,
            "blocking_reasons": blocking_reasons,
        },
    }
    _validate_schema(report, "report")
    return report


def validate_report(
    value: Any,
    series_value: Any,
    inventory_value: Any,
    plan_values: list[Any],
    observation_values: list[Any],
) -> dict[str, Any]:
    report = _object(value, "report")
    expected = aggregate_report(
        series_value,
        inventory_value,
        plan_values,
        observation_values,
    )
    if report != expected:
        raise KernelCorpusError("report is not the deterministic corpus aggregate")
    _validate_schema(report, "report")
    return report


def _open_directory(path: pathlib.Path, create: bool = False) -> int:
    if create:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise KernelCorpusError(
                f"{path}: cannot create output directory: {error}"
            ) from error
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise KernelCorpusError(
            f"{path}: unsafe or unavailable directory: {error}"
        ) from error
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise KernelCorpusError(f"{path}: expected a directory")
    return descriptor


def _directory_entries(
    path: pathlib.Path, *, descriptor: int | None = None
) -> list[str]:
    owned_descriptor = descriptor is None
    if descriptor is None:
        descriptor = _open_directory(path)
    try:
        with os.scandir(descriptor) as entries:
            names = []
            for entry in entries:
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise KernelCorpusError(
                        f"{path / entry.name}: shard artifact must be a regular file"
                    )
                names.append(entry.name)
                if len(names) > MAX_SHARDS:
                    raise KernelCorpusError(
                        "shard directory exceeds the file-count limit"
                    )
    finally:
        if owned_descriptor:
            os.close(descriptor)
    return sorted(names)


def _load_shard_directory(path: pathlib.Path) -> dict[str, Any]:
    descriptor = _open_directory(path)
    try:
        names = _directory_entries(path, descriptor=descriptor)
        pattern = re.compile(r"shard-[0-9]{4}\.json")
        if any(pattern.fullmatch(name) is None for name in names):
            raise KernelCorpusError(
                f"{path}: shard directory contains an unknown filename"
            )
        output: dict[str, Any] = {}
        total_bytes = 0
        total_nodes = 0
        for name in names:
            value, byte_count, node_count = _load_with_metrics(
                path / name, directory_fd=descriptor
            )
            total_bytes += byte_count
            total_nodes += node_count
            if total_bytes > MAX_SHARD_DIRECTORY_BYTES:
                raise KernelCorpusError(
                    f"{path}: shard directory exceeds the total byte limit"
                )
            if total_nodes > MAX_SHARD_DIRECTORY_NODES:
                raise KernelCorpusError(
                    f"{path}: shard directory exceeds the total node-count limit"
                )
            output[name] = value
        return output
    finally:
        os.close(descriptor)


def _write_json(path: pathlib.Path, value: Any) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > MAX_JSON_BYTES:
        raise KernelCorpusError(f"{path}: generated JSON exceeds the byte limit")
    directory = _open_directory(path.parent, create=True)
    temporary_name = f".{path.name}.tmp-{secrets.token_hex(16)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    temporary_descriptor: int | None = None
    published = False
    try:
        temporary_descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=directory,
        )
        position = 0
        try:
            while position < len(data):
                position += os.write(temporary_descriptor, data[position:])
            os.fsync(temporary_descriptor)
        except OSError as error:
            try:
                os.close(temporary_descriptor)
            except OSError:
                pass
            temporary_descriptor = None
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except OSError:
                pass
            raise KernelCorpusError(
                f"{path}: could not durably write the temporary output: {error}"
            ) from error
        descriptor, temporary_descriptor = temporary_descriptor, None
        os.close(descriptor)
        os.link(
            temporary_name,
            path.name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
        published = True
        try:
            os.unlink(temporary_name, dir_fd=directory)
        except OSError as error:
            raise KernelCorpusError(
                f"{path}: output was published but temporary cleanup failed: {error}"
            ) from error
        try:
            os.fsync(directory)
        except OSError as error:
            raise KernelCorpusError(
                f"{path}: output was published but directory fsync failed: {error}"
            ) from error
    except OSError as error:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if not published:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except OSError:
                pass
        raise KernelCorpusError(
            f"{path}: refusing unsafe or existing output: {error}"
        ) from error
    finally:
        os.close(directory)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-series", "validate-inventory"):
        command = commands.add_parser(name)
        command.add_argument("--input", type=pathlib.Path, required=True)
    prepare = commands.add_parser("prepare-shards")
    prepare.add_argument("--series", type=pathlib.Path, required=True)
    prepare.add_argument("--inventory", type=pathlib.Path, required=True)
    prepare.add_argument("--shard-count", type=int, required=True)
    prepare.add_argument("--output-dir", type=pathlib.Path, required=True)
    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--series", type=pathlib.Path, required=True)
    aggregate.add_argument("--inventory", type=pathlib.Path, required=True)
    aggregate.add_argument("--plans-dir", type=pathlib.Path, required=True)
    aggregate.add_argument("--observations-dir", type=pathlib.Path, required=True)
    aggregate.add_argument("--output", type=pathlib.Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-series":
            validate_series(_load(args.input))
        elif args.command == "validate-inventory":
            validate_inventory(_load(args.input))
        elif args.command == "prepare-shards":
            plans = build_shard_plans(
                _load(args.series), _load(args.inventory), args.shard_count
            )
            output_descriptor = _open_directory(args.output_dir, create=True)
            try:
                if _directory_entries(args.output_dir, descriptor=output_descriptor):
                    raise KernelCorpusError("output directory must be absent or empty")
            finally:
                os.close(output_descriptor)
            for plan in plans:
                _write_json(
                    args.output_dir / f"shard-{plan['shard_index']:04d}.json", plan
                )
        else:
            plans_by_name = _load_shard_directory(args.plans_dir)
            observations_by_name = _load_shard_directory(args.observations_dir)
            if not plans_by_name or set(plans_by_name) != set(observations_by_name):
                raise KernelCorpusError(
                    "plan and observation directories must have exact nonempty membership"
                )
            names = sorted(plans_by_name)
            plans = [plans_by_name[name] for name in names]
            observations = [observations_by_name[name] for name in names]
            report = aggregate_report(
                _load(args.series), _load(args.inventory), plans, observations
            )
            _write_json(args.output, report)
    except KernelCorpusError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
