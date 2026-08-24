#!/usr/bin/env python3
"""Convert validated kernel wire chains into one corpus runner-record bundle.

This is an offline, record-only integration boundary.  It consumes the exact
series, inventory, shard plan, raw replay/export inputs, and the separately
attested wire objects.  It never starts a process, accesses the network or a
credential, or writes State or Results.
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
    KernelCorpusError,
    _load,
    _load_with_metrics,
    _write_json,
    canonical_bytes,
    validate_inventory,
    validate_plan,
    validate_series,
)
from kernel_corpus_runner_adapter import (
    KernelCorpusRunnerError,
    _input_names,
    _open_input_directory,
    validate_input_directory,
    validate_record_bundle,
)
from kernel_runner_wire_contract import (
    MAX_BENCHMARK_CONFIGURATION_BYTES,
    KernelRunnerWireError,
    validate_attestation,
    validate_export_metadata,
    validate_invocation,
    validate_transcript,
    validate_wire_schema,
)

WIRE_SUFFIXES = (
    ".export-metadata.json",
    ".benchmark-config.input",
    ".invocation.json",
    ".transcript.json",
    ".attestation.json",
)
MAX_WIRE_DIRECTORY_BYTES = 2 * 1024 * 1024 * 1024
MAX_WIRE_JSON_BYTES = 1024 * 1024
CANDIDATE_CONFIGURATION_POLICY_FIELDS = {
    "use_stdin",
    "export_file_path",
    "unpermitted_axiom_hard_error",
    "nat_extension",
    "string_extension",
}


class KernelWireRecordError(KernelCorpusRunnerError):
    """A wire chain cannot be bound to its deterministic corpus attempt."""


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _snapshot_files(
    directory: pathlib.Path, directory_descriptor: int, names: set[str]
) -> dict[str, tuple[int, ...]]:
    snapshot = {}
    for name in sorted(names):
        try:
            metadata = os.stat(
                name, dir_fd=directory_descriptor, follow_symlinks=False
            )
        except OSError as error:
            raise KernelWireRecordError(
                f"{directory / name}: cannot inspect wire input: {error}"
            ) from error
        if not stat.S_ISREG(metadata.st_mode):
            raise KernelWireRecordError(
                f"{directory / name}: wire input must be a regular file"
            )
        snapshot[name] = _file_identity(metadata)
    return snapshot


def _read_regular(
    directory: pathlib.Path,
    directory_descriptor: int,
    name: str,
    maximum: int,
) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        raise KernelWireRecordError(
            f"{directory / name}: cannot open wire input: {error}"
        ) from error
    chunks: list[bytes] = []
    size = 0
    try:
        metadata_before = os.fstat(descriptor)
        if not stat.S_ISREG(metadata_before.st_mode):
            raise KernelWireRecordError(
                f"{directory / name}: wire input must be a regular file"
            )
        if metadata_before.st_size > maximum:
            raise KernelWireRecordError(
                f"{directory / name}: wire input exceeds its byte limit"
            )
        while True:
            chunk = os.read(descriptor, min(1_048_576, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum:
                raise KernelWireRecordError(
                    f"{directory / name}: wire input exceeds its byte limit"
                )
        metadata_after = os.fstat(descriptor)
        if (
            _file_identity(metadata_before) != _file_identity(metadata_after)
            or size != metadata_before.st_size
        ):
            raise KernelWireRecordError(
                f"{directory / name}: wire input changed while it was read"
            )
    except OSError as error:
        raise KernelWireRecordError(
            f"{directory / name}: cannot read wire input: {error}"
        ) from error
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _read_raw_input(
    inputs_dir: pathlib.Path, directory_descriptor: int, name: str
) -> bytes:
    # The wire validator applies the authoritative 64 MiB bound.  Add one byte
    # here so the validator, rather than this transport helper, owns the error.
    return _read_regular(inputs_dir, directory_descriptor, name, 64 * 1024 * 1024 + 1)


def _load_wire_json(
    wire_dir: pathlib.Path, directory_descriptor: int, name: str
) -> tuple[Any, int]:
    value, size, _ = _load_with_metrics(
        wire_dir / name,
        directory_fd=directory_descriptor,
        maximum=MAX_WIRE_JSON_BYTES,
    )
    return value, size


def _require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise KernelWireRecordError(f"{label} does not bind the exact corpus attempt")


def candidate_configuration_policy_sha256(configuration: dict[str, Any]) -> str:
    """Digest the series-wide policy, excluding per-problem permitted axioms."""

    return _sha256(
        {field: configuration[field] for field in CANDIDATE_CONFIGURATION_POLICY_FIELDS}
    )


def _validate_series_binding(
    series: dict[str, Any],
    attempt: dict[str, Any],
    metadata: dict[str, Any],
    invocation: dict[str, Any],
    attestation: dict[str, Any],
) -> None:
    candidate = series["candidate"]
    checker = invocation["checker"]
    for field in ("name", "repository", "commit", "binary_sha256", "protocol"):
        _require_equal(
            checker[field], candidate[field], f"wire checker.{field}"
        )

    profile_key = (attempt["benchmark_repository"], attempt["benchmark_commit"])
    producer_profile = next(
        (
            profile
            for profile in series["producer_profiles"]
            if (profile["benchmark_repository"], profile["benchmark_commit"])
            == profile_key
        ),
        None,
    )
    if producer_profile is None:
        raise KernelWireRecordError(
            "series has no exact producer profile for the corpus attempt"
        )
    exporter = producer_profile["exporter"]
    export_identity = metadata["exporter"]
    for field in (
        "repository",
        "commit",
        "binary_sha256",
        "name",
        "version",
        "format_specification_sha256",
        "format_specification_path",
    ):
        _require_equal(
            export_identity[field], exporter[field], f"wire exporter.{field}"
        )
    _require_equal(
        metadata["format_version"],
        exporter["format_version"],
        "wire exporter.format_version",
    )
    for field in ("toolchain", "version", "githash"):
        _require_equal(
            metadata["lean"][field],
            producer_profile["lean"][field],
            f"wire Lean {field}",
        )
    comparison = metadata["comparison_framework"]
    for field in ("repository", "commit", "protocol"):
        _require_equal(
            comparison[field],
            series["checker"][field],
            f"wire comparison framework.{field}",
        )
    _require_equal(
        candidate_configuration_policy_sha256(invocation["configuration"]),
        candidate["configuration_policy_sha256"],
        "wire candidate configuration policy",
    )

    runner = series["runner"]
    attested_runner = attestation["runner"]
    for field in (
        "repository",
        "commit",
        "image_digest",
        "architecture",
        "operating_system",
    ):
        _require_equal(
            attested_runner[field], runner[field], f"wire runner.{field}"
        )
    limits = runner["resource_limits"]
    _require_equal(
        invocation["resource_limits"]["wall_timeout_ms"],
        limits["wall_timeout_seconds"] * 1000,
        "wire wall-time limit",
    )
    _require_equal(
        invocation["resource_limits"]["max_memory_bytes"],
        limits["max_memory_bytes"],
        "wire memory limit",
    )


def _record_from_wire(
    *,
    attempt: dict[str, Any],
    series: dict[str, Any],
    raw: bytes,
    benchmark_config_raw: bytes,
    metadata_value: Any,
    invocation_value: Any,
    transcript_value: Any,
    attestation_value: Any,
) -> dict[str, Any]:
    try:
        for kind, value in (
            ("export_metadata", metadata_value),
            ("invocation", invocation_value),
            ("transcript", transcript_value),
            ("attestation", attestation_value),
        ):
            validate_wire_schema(value, kind)
        metadata = validate_export_metadata(
            metadata_value, raw, benchmark_config_raw
        )
        invocation = validate_invocation(invocation_value, metadata)
        transcript = validate_transcript(transcript_value, invocation)
        attestation = validate_attestation(
            attestation_value, invocation, transcript
        )
    except KernelRunnerWireError as error:
        raise KernelWireRecordError(f"{attempt['attempt_id']}: {error}") from error

    for field, metadata_field in (
        ("result_id", "result_id"),
        ("replay_task_id", "replay_task_id"),
        ("replay_attempt", "replay_attempt"),
    ):
        _require_equal(
            metadata[metadata_field],
            attempt[field],
            f"export metadata.{metadata_field}",
        )
    _require_equal(
        metadata["input_sha256"],
        attempt["replay_export_input_sha256"],
        "export metadata.input_sha256",
    )
    benchmark = metadata["benchmark_configuration"]
    for field in (
        "problem_id",
        "statement_revision",
        "benchmark_repository",
        "benchmark_commit",
        "benchmark_configuration_sha256",
    ):
        metadata_field = {
            "benchmark_repository": "repository",
            "benchmark_commit": "commit",
            "benchmark_configuration_sha256": "blob_sha256",
        }.get(field, field)
        _require_equal(
            benchmark[metadata_field],
            attempt[field],
            f"export metadata.benchmark_configuration.{metadata_field}",
        )
    terminal = metadata["terminal_evidence"]
    for field in (
        "terminal_verdict_sha256",
        "terminal_event_sha256",
        "report_entry_sha256",
    ):
        _require_equal(terminal[field], attempt[field], f"export metadata.{field}")
    _require_equal(
        invocation["attempt_id"], attempt["attempt_id"], "wire invocation.attempt_id"
    )
    _validate_series_binding(series, attempt, metadata, invocation, attestation)

    classification = transcript["classification"]
    if classification["status"] != "classified":
        raise KernelWireRecordError(
            f"{attempt['attempt_id']}: blocked wire outcome "
            f"{classification['reason']} cannot become a corpus record"
        )
    outcome = classification["outcome"]
    termination = transcript["termination"]
    if outcome == "export_unavailable":
        raise KernelWireRecordError(
            f"{attempt['attempt_id']}: verified bound input cannot be export_unavailable"
        )
    if (
        outcome == "export_format_unsupported"
        and termination["observed_input_sha256"] != invocation["input_sha256"]
    ):
        raise KernelWireRecordError(
            f"{attempt['attempt_id']}: format failure does not bind the verified input"
        )
    evidence = (
        termination["evidence_sha256"]
        if termination["kind"] == "not_started"
        else None
    )
    disposition = (
        "wall_timeout"
        if termination["kind"] == "timed_out"
        else "memory_limit"
        if termination["kind"] == "memory_limit"
        else "within_limits"
    )
    return {
        "attempt_id": attempt["attempt_id"],
        "input_sha256": attempt["replay_export_input_sha256"],
        "outcome": outcome,
        "evidence_sha256": evidence,
        "resource_limit_disposition": disposition,
        "statistics": transcript["statistics"],
        "transcript_sha256": _sha256(transcript),
        "runner_attestation_sha256": _sha256(attestation),
        "source_free": attestation["source_free"],
    }


def build_record_bundle(
    series_value: Any,
    inventory_value: Any,
    plan_value: Any,
    inputs_dir: pathlib.Path,
    wire_dir: pathlib.Path,
) -> dict[str, Any]:
    """Validate all chains for one shard and emit existing runner records."""

    series = validate_series(series_value)
    inventory = validate_inventory(inventory_value)
    plan = validate_plan(plan_value, series, inventory)
    attempts = [
        attempt
        for attempt in plan["attempts"]
        if attempt["required_action"] == "run"
    ]
    validate_input_directory(inputs_dir, plan["attempts"])
    input_descriptor = _open_input_directory(inputs_dir)
    wire_descriptor: int | None = None
    try:
        wire_descriptor = _open_input_directory(wire_dir)
        input_before = os.fstat(input_descriptor)
        wire_before = os.fstat(wire_descriptor)
        expected_input_names = {
            f"{attempt['attempt_id']}.input" for attempt in attempts
        }
        if _input_names(inputs_dir, input_descriptor) != expected_input_names:
            raise KernelWireRecordError(
                "input directory membership changed before wire validation"
            )
        expected_wire_names = {
            f"{attempt['attempt_id']}{suffix}"
            for attempt in attempts
            for suffix in WIRE_SUFFIXES
        }
        if _input_names(wire_dir, wire_descriptor) != expected_wire_names:
            raise KernelWireRecordError(
                "wire directory membership does not exactly match planned run attempts"
            )
        input_files_before = _snapshot_files(
            inputs_dir, input_descriptor, expected_input_names
        )
        wire_files_before = _snapshot_files(
            wire_dir, wire_descriptor, expected_wire_names
        )
        records = []
        wire_total = 0
        for attempt in attempts:
            prefix = attempt["attempt_id"]
            benchmark_config_raw = _read_regular(
                wire_dir,
                wire_descriptor,
                f"{prefix}.benchmark-config.input",
                MAX_BENCHMARK_CONFIGURATION_BYTES + 1,
            )
            wire_total += len(benchmark_config_raw)
            json_values: dict[str, Any] = {}
            for label in (
                "export-metadata",
                "invocation",
                "transcript",
                "attestation",
            ):
                value, size = _load_wire_json(
                    wire_dir, wire_descriptor, f"{prefix}.{label}.json"
                )
                json_values[label] = value
                wire_total += size
            if wire_total > MAX_WIRE_DIRECTORY_BYTES:
                raise KernelWireRecordError(
                    "wire directory exceeds the aggregate byte limit"
                )
            records.append(
                _record_from_wire(
                    attempt=attempt,
                    series=series,
                    raw=_read_raw_input(
                        inputs_dir, input_descriptor, f"{prefix}.input"
                    ),
                    benchmark_config_raw=benchmark_config_raw,
                    metadata_value=json_values["export-metadata"],
                    invocation_value=json_values["invocation"],
                    transcript_value=json_values["transcript"],
                    attestation_value=json_values["attestation"],
                )
            )
        input_after = os.fstat(input_descriptor)
        wire_after = os.fstat(wire_descriptor)
        if input_files_before != _snapshot_files(
            inputs_dir, input_descriptor, expected_input_names
        ):
            raise KernelWireRecordError(
                "input directory files changed while they were verified"
            )
        if wire_files_before != _snapshot_files(
            wire_dir, wire_descriptor, expected_wire_names
        ):
            raise KernelWireRecordError(
                "wire directory files changed while they were verified"
            )
        for label, before, after in (
            ("input", input_before, input_after),
            ("wire", wire_before, wire_after),
        ):
            if (before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise KernelWireRecordError(
                    f"{label} directory membership changed while it was verified"
                )
    finally:
        try:
            os.close(input_descriptor)
        finally:
            if wire_descriptor is not None:
                os.close(wire_descriptor)
    bundle = {
        "schema_version": 1,
        "kind": "kernel_corpus_runner_records",
        "configuration_id": series["configuration_id"],
        "configuration_sha256": _sha256(series),
        "inventory_id": inventory["inventory_id"],
        "inventory_sha256": _sha256(inventory),
        "shard_id": plan["shard_id"],
        "records": records,
    }
    validate_record_bundle(bundle, plan, series, inventory)
    return bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", type=pathlib.Path, required=True)
    parser.add_argument("--inventory", type=pathlib.Path, required=True)
    parser.add_argument("--plan", type=pathlib.Path, required=True)
    parser.add_argument("--inputs-dir", type=pathlib.Path, required=True)
    parser.add_argument("--wire-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        bundle = build_record_bundle(
            _load(args.series),
            _load(args.inventory),
            _load(args.plan),
            args.inputs_dir,
            args.wire_dir,
        )
        _write_json(args.output, bundle)
    except (KernelCorpusError, KernelRunnerWireError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
