#!/usr/bin/env python3
"""Materialize one independent-kernel observation shard from reviewed records.

This is a deliberately record-only boundary.  It verifies that every planned
``run`` attempt has the exact content-addressed replay/export input and one
source-free record from a separately reviewed runner.  It never executes the
exporter, checker, or candidate and has no network or credential interface.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import stat
import sys
from typing import Any

from kernel_corpus_report import (
    DIGEST,
    TERMINAL_OUTCOMES,
    KernelCorpusError,
    _fields,
    _load,
    _match,
    _object,
    _validate_observation_shard_against,
    _validate_schema,
    _validate_statistics,
    _write_json,
    canonical_bytes,
    execution_receipt_sha256,
    validate_inventory,
    validate_plan,
    validate_series,
)

RUNNER_RECORD_FIELDS = {
    "attempt_id",
    "input_sha256",
    "outcome",
    "evidence_sha256",
    "resource_limit_disposition",
    "statistics",
    "transcript_sha256",
    "runner_attestation_sha256",
    "source_free",
}
RECORD_BUNDLE_FIELDS = {
    "schema_version",
    "kind",
    "configuration_id",
    "configuration_sha256",
    "inventory_id",
    "inventory_sha256",
    "shard_id",
    "records",
}
EXECUTED_OUTCOMES = (
    *TERMINAL_OUTCOMES,
    "export_unavailable",
    "export_format_unsupported",
)
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_SHARD_INPUT_BYTES = 1024 * 1024 * 1024
MAX_INPUT_FILES = 100_000


class KernelCorpusRunnerError(KernelCorpusError):
    """A runner record or source-free input violates the adapter contract."""


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _open_input_directory(path: pathlib.Path) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise KernelCorpusRunnerError(
            f"{path}: unsafe or unavailable input directory: {error}"
        ) from error
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise KernelCorpusRunnerError(f"{path}: expected an input directory")
    return descriptor


def _input_names(path: pathlib.Path, descriptor: int) -> set[str]:
    try:
        with os.scandir(descriptor) as entries:
            names: set[str] = set()
            for entry in entries:
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise KernelCorpusRunnerError(
                        f"{path / entry.name}: input must be a regular non-symlink file"
                    )
                names.add(entry.name)
                if len(names) > MAX_INPUT_FILES:
                    raise KernelCorpusRunnerError(
                        "input directory exceeds the file-count limit"
                    )
            return names
    except OSError as error:
        raise KernelCorpusRunnerError(
            f"{path}: cannot inspect inputs: {error}"
        ) from error


def _digest_input(path: pathlib.Path, descriptor: int, name: str) -> tuple[str, int]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        input_descriptor = os.open(name, flags, dir_fd=descriptor)
    except OSError as error:
        raise KernelCorpusRunnerError(
            f"{path / name}: cannot open input: {error}"
        ) from error
    digest = hashlib.sha256()
    size = 0
    try:
        metadata = os.fstat(input_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise KernelCorpusRunnerError(
                f"{path / name}: input must be a regular file"
            )
        if metadata.st_size > MAX_INPUT_BYTES:
            raise KernelCorpusRunnerError(
                f"{path / name}: input exceeds the per-file byte limit"
            )
        while True:
            chunk = os.read(
                input_descriptor, min(1_048_576, MAX_INPUT_BYTES + 1 - size)
            )
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_INPUT_BYTES:
                raise KernelCorpusRunnerError(
                    f"{path / name}: input exceeds the per-file byte limit"
                )
            digest.update(chunk)
    except OSError as error:
        raise KernelCorpusRunnerError(
            f"{path / name}: cannot read input: {error}"
        ) from error
    finally:
        os.close(input_descriptor)
    return digest.hexdigest(), size


def validate_input_directory(
    path: pathlib.Path, attempts: list[dict[str, Any]]
) -> None:
    """Require exact, bounded input membership and bind every raw byte stream."""

    expected = {
        f"{attempt['attempt_id']}.input": attempt["replay_export_input_sha256"]
        for attempt in attempts
        if attempt["required_action"] == "run"
    }
    descriptor = _open_input_directory(path)
    try:
        before = os.fstat(descriptor)
        actual = _input_names(path, descriptor)
        if actual != set(expected):
            raise KernelCorpusRunnerError(
                "input directory membership does not exactly match planned run attempts"
            )
        total = 0
        for name in sorted(expected):
            digest, size = _digest_input(path, descriptor, name)
            total += size
            if total > MAX_SHARD_INPUT_BYTES:
                raise KernelCorpusRunnerError(
                    "input directory exceeds the total byte limit"
                )
            if digest != expected[name]:
                raise KernelCorpusRunnerError(
                    f"{path / name}: raw SHA-256 does not match the planned replay/export input"
                )
        after = os.fstat(descriptor)
        if (before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise KernelCorpusRunnerError(
                "input directory membership changed while it was verified"
            )
    finally:
        os.close(descriptor)


def validate_record_bundle(
    value: Any,
    plan: dict[str, Any],
    series: dict[str, Any],
    inventory: dict[str, Any],
) -> list[dict[str, Any]]:
    bundle = _object(value, "runner record bundle")
    _fields(bundle, RECORD_BUNDLE_FIELDS, "runner record bundle")
    if bundle["schema_version"] != 1 or isinstance(bundle["schema_version"], bool):
        raise KernelCorpusRunnerError(
            "runner record bundle.schema_version must be integer 1"
        )
    if bundle["kind"] != "kernel_corpus_runner_records":
        raise KernelCorpusRunnerError("runner record bundle.kind is not registered")
    exact = {
        "configuration_id": series["configuration_id"],
        "configuration_sha256": _sha256(series),
        "inventory_id": inventory["inventory_id"],
        "inventory_sha256": _sha256(inventory),
        "shard_id": plan["shard_id"],
    }
    for field, expected in exact.items():
        if bundle[field] != expected:
            raise KernelCorpusRunnerError(
                f"runner record bundle.{field} does not bind the exact shard"
            )
    records = bundle["records"]
    if not isinstance(records, list):
        raise KernelCorpusRunnerError("runner record bundle.records must be an array")
    expected_attempts = [
        attempt for attempt in plan["attempts"] if attempt["required_action"] == "run"
    ]
    if len(records) != len(expected_attempts):
        raise KernelCorpusRunnerError(
            "runner record bundle must contain exactly one record per planned run attempt"
        )
    validated: list[dict[str, Any]] = []
    limits = series["runner"]["resource_limits"]
    for index, (raw, attempt) in enumerate(
        zip(records, expected_attempts, strict=True)
    ):
        label = f"runner record bundle.records[{index}]"
        record = _object(raw, label)
        _fields(record, RUNNER_RECORD_FIELDS, label)
        if record["attempt_id"] != attempt["attempt_id"]:
            raise KernelCorpusRunnerError(f"{label}.attempt_id is out of plan order")
        if record["input_sha256"] != attempt["replay_export_input_sha256"]:
            raise KernelCorpusRunnerError(
                f"{label}.input_sha256 does not bind the plan"
            )
        if record["outcome"] not in EXECUTED_OUTCOMES:
            raise KernelCorpusRunnerError(f"{label}.outcome is not registered")
        if record["source_free"] is not True:
            raise KernelCorpusRunnerError(f"{label}.source_free must be true")
        _match(DIGEST, record["transcript_sha256"], f"{label}.transcript_sha256")
        _match(
            DIGEST,
            record["runner_attestation_sha256"],
            f"{label}.runner_attestation_sha256",
        )
        statistics = _validate_statistics(record["statistics"], f"{label}.statistics")
        if statistics["wall_time_ms"] > limits["wall_timeout_seconds"] * 1_000:
            raise KernelCorpusRunnerError(f"{label} exceeds the series wall timeout")
        if statistics["peak_memory_bytes"] > limits["max_memory_bytes"]:
            raise KernelCorpusRunnerError(f"{label} exceeds the series memory limit")
        outcome = record["outcome"]
        evidence = record["evidence_sha256"]
        if outcome in TERMINAL_OUTCOMES:
            if evidence is not None:
                raise KernelCorpusRunnerError(
                    f"{label} terminal checker outcome cannot claim availability evidence"
                )
        else:
            _match(DIGEST, evidence, f"{label}.evidence_sha256")
        disposition = record["resource_limit_disposition"]
        if disposition == "wall_timeout":
            if (
                outcome != "timed_out"
                or statistics["wall_time_ms"] != limits["wall_timeout_seconds"] * 1_000
            ):
                raise KernelCorpusRunnerError(
                    f"{label} has an inconsistent wall timeout"
                )
        elif disposition == "memory_limit":
            if (
                outcome != "crashed"
                or statistics["peak_memory_bytes"] != limits["max_memory_bytes"]
            ):
                raise KernelCorpusRunnerError(
                    f"{label} has an inconsistent memory limit"
                )
        elif disposition == "within_limits":
            if outcome == "timed_out":
                raise KernelCorpusRunnerError(
                    f"{label} timed_out must record wall_timeout"
                )
        else:
            raise KernelCorpusRunnerError(
                f"{label}.resource_limit_disposition is not registered"
            )
        if outcome in {"export_unavailable", "export_format_unsupported"}:
            if statistics["checker_invocations"] != 0:
                raise KernelCorpusRunnerError(
                    f"{label} export outcome must record zero checker invocations"
                )
        elif statistics["checker_invocations"] < 1:
            raise KernelCorpusRunnerError(
                f"{label} checker outcome must record at least one checker invocation"
            )
        validated.append(record)
    _validate_schema(bundle, "runner_records")
    return validated


def materialize_observation_shard(
    series_value: Any,
    inventory_value: Any,
    plan_value: Any,
    records_value: Any,
    inputs_dir: pathlib.Path,
) -> dict[str, Any]:
    """Build a schema-valid shard without inventing runner evidence."""

    series = validate_series(series_value)
    inventory = validate_inventory(inventory_value)
    plan = validate_plan(plan_value, series, inventory)
    validate_input_directory(inputs_dir, plan["attempts"])
    records = validate_record_bundle(records_value, plan, series, inventory)
    records_by_attempt = {record["attempt_id"]: record for record in records}
    by_result = {result["result_id"]: result for result in inventory["results"]}
    configuration_sha256 = _sha256(series)
    observations: list[dict[str, Any]] = []
    inherited = {
        "record_source_unavailable": ("unavailable", "source_unavailable"),
        "record_replay_unavailable": ("unavailable", "replay_unavailable"),
        "record_replay_pending": ("pending", "replay_pending"),
    }
    for attempt in plan["attempts"]:
        common = {
            "result_id": attempt["result_id"],
            "replay_task_id": attempt["replay_task_id"],
            "replay_attempt": attempt["replay_attempt"],
            "attempt_id": attempt["attempt_id"],
        }
        if attempt["required_action"] != "run":
            if attempt["required_action"] not in inherited:
                raise KernelCorpusRunnerError(
                    f"{attempt['attempt_id']}: required_action is not registered"
                )
            status, outcome = inherited[attempt["required_action"]]
            evidence = by_result[attempt["result_id"]]["unavailability_evidence_sha256"]
            observations.append(
                {
                    **common,
                    "status": status,
                    "outcome": outcome,
                    "evidence_sha256": evidence,
                    "statistics": None,
                    "execution_receipt": None,
                }
            )
            continue
        record = records_by_attempt[attempt["attempt_id"]]
        outcome = record["outcome"]
        receipt = {
            "schema_version": 1,
            "receipt_sha256": "",
            "attempt_id": attempt["attempt_id"],
            "input_sha256": record["input_sha256"],
            "configuration_id": series["configuration_id"],
            "configuration_sha256": configuration_sha256,
            "outcome": outcome,
            "resource_limit_disposition": record["resource_limit_disposition"],
            "statistics": record["statistics"],
            "transcript_sha256": record["transcript_sha256"],
            "runner_attestation_sha256": record["runner_attestation_sha256"],
            "source_free": record["source_free"],
        }
        receipt["receipt_sha256"] = execution_receipt_sha256(receipt)
        observations.append(
            {
                **common,
                "status": (
                    "completed"
                    if outcome in TERMINAL_OUTCOMES
                    else "unavailable"
                    if outcome == "export_unavailable"
                    else "pending"
                ),
                "outcome": outcome,
                "evidence_sha256": record["evidence_sha256"],
                "statistics": record["statistics"],
                "execution_receipt": receipt,
            }
        )
    shard = {
        **{key: value for key, value in plan.items() if key != "attempts"},
        "kind": "kernel_corpus_observations",
        "observations": observations,
    }
    return _validate_observation_shard_against(shard, plan, series, by_result)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", type=pathlib.Path, required=True)
    parser.add_argument("--inventory", type=pathlib.Path, required=True)
    parser.add_argument("--plan", type=pathlib.Path, required=True)
    parser.add_argument("--inputs-dir", type=pathlib.Path, required=True)
    parser.add_argument("--records", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        shard = materialize_observation_shard(
            _load(args.series),
            _load(args.inventory),
            _load(args.plan),
            _load(args.records),
            args.inputs_dir,
        )
        _write_json(args.output, shard)
    except KernelCorpusError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
