"""Validate the offline wire contract for independent-kernel corpus runners.

This module does not produce exports or execute a checker.  It only validates
closed metadata, exact raw NDJSON bytes, a canonical fixed invocation, and the
source-free transcript/attestation objects produced by a separately reviewed
runner.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from typing import Any

from kernel_corpus_report import canonical_bytes

DIGEST = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
REPLAY_TASK_ID = re.compile(r"rt1_[0-9a-f]{64}\Z")
ATTEMPT_ID = re.compile(r"kca1_[0-9a-f]{64}\Z")
TOOLCHAIN = re.compile(
    r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta)[0-9]+)?\Z"
)
PROBLEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
AXIOM = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\Z")
VALIDATOR_CODE = re.compile(r"[A-Za-z0-9_.+-]{1,128}\Z")

MAX_EXPORT_BYTES = 64 * 1024 * 1024
MAX_EXPORT_LINES = 2_000_000
MAX_EXPORT_LINE_BYTES = 4 * 1024 * 1024
MAX_BENCHMARK_CONFIGURATION_BYTES = 1024 * 1024
MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 1_000_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991

CHECKER_REPOSITORY = "metalogiclabs/mathgraph-lean-kernel"
CHECKER_COMMIT = "3d7585c21242f29fdaa48ae9a16e16c6afe42238"
CHECKER_BINARY = "/opt/lean-eval/bin/sokonanoda"
CONFIG_PATH = "/run/lean-eval/nanoda-config.json"
INPUT_PATH = "/run/lean-eval/solution-export.ndjson"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
CRASH_SIGNALS = {4, 6, 7, 8, 11}
EXPORT_RECORD_KEYS = {
    "meta",
    "str",
    "num",
    "succ",
    "max",
    "imax",
    "param",
    "natVal",
    "strVal",
    "mdata",
    "letE",
    "const",
    "app",
    "forallE",
    "lam",
    "proj",
    "sort",
    "bvar",
    "axiom",
    "def",
    "opaque",
    "thm",
    "quot",
    "inductive",
}
BACK_REFERENCE_KEYS = {"in", "il", "ie"}


class KernelRunnerWireError(ValueError):
    """A wire object or raw export violates the closed runner contract."""


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise KernelRunnerWireError(f"{label} fields are not closed")
    return value


def _string(value: Any, label: str, maximum: int = 512) -> str:
    try:
        encoded = value.encode("utf-8") if isinstance(value, str) else b""
    except UnicodeEncodeError as error:
        raise KernelRunnerWireError(f"{label} is invalid") from error
    if (
        not isinstance(value, str)
        or not value
        or len(encoded) > maximum
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise KernelRunnerWireError(f"{label} is invalid")
    return value


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    text = _string(value, label)
    if pattern.fullmatch(text) is None:
        raise KernelRunnerWireError(f"{label} is invalid")
    return text


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_SAFE_INTEGER:
        raise KernelRunnerWireError(f"{label} is invalid")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def canonical_configuration_bytes(value: dict[str, Any]) -> bytes:
    """Return the exact UTF-8 bytes written to the fixed nanoda config path."""

    return canonical_bytes(value)


def validate_export_metadata(
    value: Any, raw: bytes, benchmark_configuration_raw: bytes
) -> dict[str, Any]:
    metadata = _object(
        value,
        {
            "schema_version",
            "kind",
            "result_id",
            "replay_task_id",
            "replay_attempt",
            "input_sha256",
            "input_size_bytes",
            "encoding",
            "format_version",
            "source_free",
            "exporter",
            "lean",
            "benchmark_configuration",
            "terminal_evidence",
        },
        "export metadata",
    )
    if (
        metadata["schema_version"] != 1
        or type(metadata["schema_version"]) is not int
        or metadata["kind"] != "kernel_solution_export_input"
        or metadata["encoding"] != "utf8_ndjson"
        or metadata["format_version"] != "3.1.0"
        or metadata["source_free"] is not True
    ):
        raise KernelRunnerWireError("export metadata identity is invalid")
    _match(RESULT_ID, metadata["result_id"], "export metadata.result_id")
    _match(REPLAY_TASK_ID, metadata["replay_task_id"], "export metadata.replay_task_id")
    _integer(metadata["replay_attempt"], "export metadata.replay_attempt", 1)
    _match(DIGEST, metadata["input_sha256"], "export metadata.input_sha256")
    _integer(metadata["input_size_bytes"], "export metadata.input_size_bytes", 1)

    exporter = _object(
        metadata["exporter"],
        {
            "repository",
            "commit",
            "binary_sha256",
            "name",
            "version",
            "format_specification_sha256",
            "format_specification_path",
        },
        "export metadata.exporter",
    )
    if (
        exporter["repository"] != "leanprover/lean4export"
        or exporter["name"] != "lean4export"
    ):
        raise KernelRunnerWireError("exporter identity is not registered")
    _match(COMMIT, exporter["commit"], "export metadata.exporter.commit")
    _match(DIGEST, exporter["binary_sha256"], "export metadata.exporter.binary_sha256")
    if (
        exporter["format_specification_sha256"]
        != "f82a21e17e4258a1043895d0653ea4333bef8cb07aad2e3d6c1fc4be52b138e3"
        or exporter["format_specification_path"] != "format_ndjson.md"
    ):
        raise KernelRunnerWireError(
            "export format specification is not the pinned v1 document"
        )
    _string(exporter["version"], "export metadata.exporter.version", 64)

    lean = _object(
        metadata["lean"], {"toolchain", "version", "githash"}, "export metadata.lean"
    )
    _match(TOOLCHAIN, lean["toolchain"], "export metadata.lean.toolchain")
    _string(lean["version"], "export metadata.lean.version", 64)
    _match(COMMIT, lean["githash"], "export metadata.lean.githash")

    benchmark_config = _object(
        metadata["benchmark_configuration"],
        {
            "repository",
            "commit",
            "problem_id",
            "statement_revision",
            "path",
            "blob_sha256",
            "permitted_axioms",
        },
        "export metadata.benchmark_configuration",
    )
    if benchmark_config["repository"] != "leanprover/lean-eval":
        raise KernelRunnerWireError(
            "benchmark configuration repository is not registered"
        )
    _match(COMMIT, benchmark_config["commit"], "benchmark configuration.commit")
    problem_id = _match(
        PROBLEM, benchmark_config["problem_id"], "benchmark configuration.problem_id"
    )
    _integer(
        benchmark_config["statement_revision"],
        "benchmark configuration.statement_revision",
        1,
    )
    if benchmark_config["path"] != f"generated/{problem_id}/config.json":
        raise KernelRunnerWireError("benchmark configuration path is not canonical")
    _match(
        DIGEST, benchmark_config["blob_sha256"], "benchmark configuration.blob_sha256"
    )
    _validate_axioms(
        benchmark_config["permitted_axioms"], "benchmark configuration.permitted_axioms"
    )
    if not 1 <= len(benchmark_configuration_raw) <= MAX_BENCHMARK_CONFIGURATION_BYTES:
        raise KernelRunnerWireError(
            "benchmark configuration size is outside the contract"
        )
    if (
        hashlib.sha256(benchmark_configuration_raw).hexdigest()
        != benchmark_config["blob_sha256"]
    ):
        raise KernelRunnerWireError(
            "benchmark configuration bytes differ from the bound blob"
        )
    try:
        benchmark_value = json.loads(
            benchmark_configuration_raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        KernelRunnerWireError,
        RecursionError,
    ) as error:
        raise KernelRunnerWireError(
            "benchmark configuration is not strict JSON"
        ) from error
    if (
        not isinstance(benchmark_value, dict)
        or benchmark_value.get("permitted_axioms")
        != benchmark_config["permitted_axioms"]
    ):
        raise KernelRunnerWireError(
            "benchmark configuration permitted_axioms differs from the sidecar"
        )
    _reject_invalid_json_scalars(benchmark_value)

    evidence = _object(
        metadata["terminal_evidence"],
        {"terminal_verdict_sha256", "terminal_event_sha256", "report_entry_sha256"},
        "export metadata.terminal_evidence",
    )
    for field in evidence:
        _match(DIGEST, evidence[field], f"export metadata.terminal_evidence.{field}")

    if not 1 <= len(raw) <= MAX_EXPORT_BYTES:
        raise KernelRunnerWireError("raw export size is outside the contract")
    if metadata["input_size_bytes"] != len(raw):
        raise KernelRunnerWireError("raw export size does not match metadata")
    if hashlib.sha256(raw).hexdigest() != metadata["input_sha256"]:
        raise KernelRunnerWireError("raw export digest does not match metadata")
    if not raw.endswith(b"\n") or b"\r" in raw or b"\x00" in raw:
        raise KernelRunnerWireError("raw export is not canonical LF-terminated NDJSON")
    first: Any = None
    line_count = 0
    for index, raw_line_with_lf in enumerate(io.BytesIO(raw)):
        line_count += 1
        if line_count > MAX_EXPORT_LINES or not raw_line_with_lf.endswith(b"\n"):
            raise KernelRunnerWireError("raw export line count is outside the contract")
        raw_line = raw_line_with_lf[:-1]
        if not raw_line or len(raw_line) > MAX_EXPORT_LINE_BYTES:
            raise KernelRunnerWireError("raw export contains an invalid line")
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError as error:
            raise KernelRunnerWireError("raw export is not strict UTF-8") from error
        if len(line.splitlines()) != 1:
            raise KernelRunnerWireError("raw export contains an invalid line boundary")
        try:
            item = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonfinite,
            )
        except (
            UnicodeError,
            json.JSONDecodeError,
            KernelRunnerWireError,
            RecursionError,
        ) as error:
            raise KernelRunnerWireError("raw export contains invalid NDJSON") from error
        if not isinstance(item, dict):
            raise KernelRunnerWireError("raw export lines must be JSON objects")
        _reject_invalid_json_scalars(item)
        if index == 0:
            first = item
        elif "meta" in item:
            raise KernelRunnerWireError(
                "raw export contains more than one metadata record"
            )
        keys = set(item)
        record_keys = keys & EXPORT_RECORD_KEYS
        if len(record_keys) != 1 or not keys <= record_keys | BACK_REFERENCE_KEYS:
            raise KernelRunnerWireError(
                "raw export contains an unregistered record shape"
            )
    if line_count == 0:
        raise KernelRunnerWireError("raw export line count is outside the contract")
    meta = _object(first, {"meta"}, "raw export first record")["meta"]
    meta = _object(meta, {"exporter", "lean", "format"}, "raw export metadata")
    raw_exporter = _object(
        meta["exporter"], {"name", "version"}, "raw exporter metadata"
    )
    raw_lean = _object(meta["lean"], {"version", "githash"}, "raw Lean metadata")
    raw_format = _object(meta["format"], {"version"}, "raw format metadata")
    if raw_exporter != {"name": exporter["name"], "version": exporter["version"]}:
        raise KernelRunnerWireError("raw exporter metadata differs from its sidecar")
    if raw_lean != {"version": lean["version"], "githash": lean["githash"]}:
        raise KernelRunnerWireError("raw Lean metadata differs from its sidecar")
    if raw_format != {"version": metadata["format_version"]}:
        raise KernelRunnerWireError("raw export format differs from its sidecar")
    return metadata


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise KernelRunnerWireError("JSON object contains a duplicate key")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> Any:
    raise KernelRunnerWireError(f"non-finite JSON number is not permitted: {value}")


def _reject_invalid_json_scalars(value: Any) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise KernelRunnerWireError("JSON complexity is outside the contract")
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as error:
                raise KernelRunnerWireError(
                    "JSON contains an invalid Unicode scalar"
                ) from error
        elif isinstance(item, float) and not math.isfinite(item):
            raise KernelRunnerWireError("JSON contains a non-finite number")
        elif isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _validate_axioms(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 64
        or not all(isinstance(item, str) and AXIOM.fullmatch(item) for item in value)
        or value != sorted(set(value))
    ):
        raise KernelRunnerWireError(
            f"{label} must be a bounded sorted unique Lean-name list"
        )
    return value


def validate_invocation(value: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    invocation = _object(
        value,
        {
            "schema_version",
            "kind",
            "attempt_id",
            "attempt_identity_qualification",
            "input_sha256",
            "export_metadata_sha256",
            "checker",
            "configuration",
            "configuration_sha256",
            "configuration_path",
            "input_path",
            "argv",
            "environment",
            "resource_limits",
            "outcome_protocol",
        },
        "invocation",
    )
    if (
        invocation["schema_version"] != 1
        or type(invocation["schema_version"]) is not int
        or invocation["kind"] != "kernel_nanoda_invocation"
        or invocation["attempt_identity_qualification"] != "unbound"
    ):
        raise KernelRunnerWireError("invocation identity is invalid")
    _match(ATTEMPT_ID, invocation["attempt_id"], "invocation.attempt_id")
    if invocation["input_sha256"] != metadata["input_sha256"]:
        raise KernelRunnerWireError("invocation does not bind the raw export")
    if invocation["export_metadata_sha256"] != _digest(metadata):
        raise KernelRunnerWireError("invocation does not bind the export sidecar")
    checker = _object(
        invocation["checker"],
        {
            "name",
            "repository",
            "commit",
            "binary_sha256",
            "binary_qualification",
            "executable",
            "protocol",
        },
        "invocation.checker",
    )
    if checker != {
        "name": "mathgraph",
        "repository": CHECKER_REPOSITORY,
        "commit": CHECKER_COMMIT,
        "binary_sha256": checker["binary_sha256"],
        "binary_qualification": "unqualified",
        "executable": CHECKER_BINARY,
        "protocol": "nanoda_config_file_v1",
    }:
        raise KernelRunnerWireError(
            "invocation checker identity is not the reviewed fixed checker"
        )
    _match(DIGEST, checker["binary_sha256"], "invocation.checker.binary_sha256")
    config = _object(
        invocation["configuration"],
        {
            "use_stdin",
            "export_file_path",
            "permitted_axioms",
            "unpermitted_axiom_hard_error",
            "nat_extension",
            "string_extension",
        },
        "invocation.configuration",
    )
    if (
        config["use_stdin"] is not False
        or config["export_file_path"] != INPUT_PATH
        or config["unpermitted_axiom_hard_error"] is not True
        or config["nat_extension"] is not True
        or config["string_extension"] is not True
    ):
        raise KernelRunnerWireError("nanoda configuration changes the reviewed policy")
    axioms = _validate_axioms(config["permitted_axioms"], "permitted_axioms")
    if axioms != metadata["benchmark_configuration"]["permitted_axioms"]:
        raise KernelRunnerWireError(
            "permitted_axioms differs from the bound benchmark configuration"
        )
    expected_config_digest = hashlib.sha256(
        canonical_configuration_bytes(config)
    ).hexdigest()
    if invocation["configuration_sha256"] != expected_config_digest:
        raise KernelRunnerWireError(
            "configuration_sha256 does not bind canonical bytes"
        )
    if (
        invocation["configuration_path"] != CONFIG_PATH
        or invocation["input_path"] != INPUT_PATH
        or invocation["argv"] != [CHECKER_BINARY, CONFIG_PATH]
        or invocation["environment"] != {}
    ):
        raise KernelRunnerWireError(
            "invocation argv, paths, or environment are not fixed"
        )
    limits = _object(
        invocation["resource_limits"],
        {"wall_timeout_ms", "max_memory_bytes"},
        "invocation.resource_limits",
    )
    _integer(limits["wall_timeout_ms"], "invocation.resource_limits.wall_timeout_ms", 1)
    _integer(
        limits["max_memory_bytes"], "invocation.resource_limits.max_memory_bytes", 1
    )
    protocol = _object(
        invocation["outcome_protocol"],
        {
            "name",
            "accepted_exit_code",
            "declined_exit_code",
            "ambiguous_exit_codes",
            "rejected_exit_codes",
            "source_path",
            "source_sha256",
            "blocking_reason",
        },
        "invocation.outcome_protocol",
    )
    if protocol != {
        "name": "mathgraph_nanoda_exit_v1",
        "accepted_exit_code": 0,
        "declined_exit_code": 2,
        "ambiguous_exit_codes": [1],
        "rejected_exit_codes": [],
        "source_path": "src/main.rs",
        "source_sha256": "c38ff157412ff9d494c282cf47d9e5059ab35b32c35dc6f74548f9e40c5be166",
        "blocking_reason": "candidate_exit_1_conflates_rejection_and_internal_failure",
    }:
        raise KernelRunnerWireError(
            "outcome protocol guesses at an unsupported checker result"
        )
    return invocation


def invocation_sha256(invocation: dict[str, Any]) -> str:
    return _digest(invocation)


def transcript_sha256(transcript: dict[str, Any]) -> str:
    return _digest(transcript)


def validate_transcript(value: Any, invocation: dict[str, Any]) -> dict[str, Any]:
    transcript = _object(
        value,
        {
            "schema_version",
            "kind",
            "attempt_id",
            "input_sha256",
            "invocation_sha256",
            "termination",
            "statistics",
            "stdout_sha256",
            "stdout_size_bytes",
            "stdout_truncated",
            "stderr_sha256",
            "stderr_size_bytes",
            "stderr_truncated",
            "classification",
        },
        "transcript",
    )
    if (
        transcript["schema_version"] != 1
        or type(transcript["schema_version"]) is not int
        or transcript["kind"] != "kernel_runner_transcript"
    ):
        raise KernelRunnerWireError("transcript identity is invalid")
    if (
        transcript["attempt_id"] != invocation["attempt_id"]
        or transcript["input_sha256"] != invocation["input_sha256"]
        or transcript["invocation_sha256"] != invocation_sha256(invocation)
    ):
        raise KernelRunnerWireError("transcript does not bind the exact invocation")
    for field in ("stdout_sha256", "stderr_sha256"):
        _match(DIGEST, transcript[field], f"transcript.{field}")
    for field in ("stdout_size_bytes", "stderr_size_bytes"):
        if _integer(transcript[field], f"transcript.{field}") > MAX_STREAM_BYTES:
            raise KernelRunnerWireError(f"transcript.{field} exceeds the bound")
    for stream in ("stdout", "stderr"):
        if transcript[f"{stream}_truncated"] is not False:
            raise KernelRunnerWireError(f"transcript.{stream} must not be truncated")
        if (
            transcript[f"{stream}_size_bytes"] == 0
            and transcript[f"{stream}_sha256"] != EMPTY_SHA256
        ):
            raise KernelRunnerWireError(
                f"transcript.{stream} empty digest is inconsistent"
            )
        if (
            transcript[f"{stream}_size_bytes"] != 0
            and transcript[f"{stream}_sha256"] == EMPTY_SHA256
        ):
            raise KernelRunnerWireError(
                f"transcript.{stream} nonempty digest is inconsistent"
            )
    statistics = _object(
        transcript["statistics"],
        {"wall_time_ms", "peak_memory_bytes", "checker_invocations"},
        "transcript.statistics",
    )
    for field in ("wall_time_ms", "peak_memory_bytes", "checker_invocations"):
        _integer(statistics[field], f"transcript.statistics.{field}")
    limits = invocation["resource_limits"]
    if statistics["wall_time_ms"] > limits["wall_timeout_ms"]:
        raise KernelRunnerWireError("transcript wall time exceeds the invocation limit")
    if statistics["peak_memory_bytes"] > limits["max_memory_bytes"]:
        raise KernelRunnerWireError(
            "transcript peak memory exceeds the invocation limit"
        )
    termination = transcript["termination"]
    if not isinstance(termination, dict) or "kind" not in termination:
        raise KernelRunnerWireError("transcript.termination is invalid")
    kind = termination["kind"]
    expected_status = "classified"
    expected_outcome: str | None
    expected_reason: str | None = None
    if kind == "not_started":
        _object(
            termination,
            {
                "kind",
                "reason",
                "evidence_sha256",
                "validator_code",
                "observed_input_sha256",
            },
            "transcript.termination",
        )
        if termination["reason"] not in {
            "export_unavailable",
            "export_format_unsupported",
        }:
            raise KernelRunnerWireError("not-started termination reason is invalid")
        expected_outcome = termination["reason"]
        _match(
            DIGEST,
            termination["evidence_sha256"],
            "transcript.termination.evidence_sha256",
        )
        _match(
            VALIDATOR_CODE,
            termination["validator_code"],
            "transcript.termination.validator_code",
        )
        observed = termination["observed_input_sha256"]
        if termination["reason"] == "export_unavailable":
            if observed is not None:
                raise KernelRunnerWireError(
                    "unavailable export cannot claim observed bytes"
                )
        else:
            _match(DIGEST, observed, "transcript.termination.observed_input_sha256")
        if statistics["checker_invocations"] != 0:
            raise KernelRunnerWireError(
                "not-started transcript claims a checker invocation"
            )
        if statistics["wall_time_ms"] != 0 or statistics["peak_memory_bytes"] != 0:
            raise KernelRunnerWireError(
                "not-started transcript claims process statistics"
            )
        if transcript["stdout_size_bytes"] != 0 or transcript["stderr_size_bytes"] != 0:
            raise KernelRunnerWireError("not-started transcript claims process output")
    elif kind == "exited":
        _object(termination, {"kind", "code"}, "transcript.termination")
        code = _integer(termination["code"], "transcript.termination.code")
        if code > 255:
            raise KernelRunnerWireError("exited transcript status is invalid")
        if statistics["checker_invocations"] != 1:
            raise KernelRunnerWireError("exited transcript invocation count is invalid")
        if code == 0:
            expected_outcome = "accepted"
        elif code == 2:
            expected_outcome = "declined"
        else:
            expected_status = "blocked"
            expected_outcome = None
            expected_reason = (
                "ambiguous_exit_status" if code == 1 else "unregistered_exit_status"
            )
    elif kind == "signaled":
        _object(termination, {"kind", "signal"}, "transcript.termination")
        signal = _integer(termination["signal"], "transcript.termination.signal", 1)
        if signal > 127 or statistics["checker_invocations"] != 1:
            raise KernelRunnerWireError("signaled transcript is invalid")
        if signal not in CRASH_SIGNALS:
            expected_status = "blocked"
            expected_outcome = None
            expected_reason = "ambiguous_signal"
        else:
            expected_outcome = "crashed"
    elif kind in {"timed_out", "memory_limit"}:
        _object(
            termination,
            {"kind", "evidence_sha256", "limiter_code"},
            "transcript.termination",
        )
        _match(
            DIGEST,
            termination["evidence_sha256"],
            "transcript.termination.evidence_sha256",
        )
        _match(
            VALIDATOR_CODE,
            termination["limiter_code"],
            "transcript.termination.limiter_code",
        )
        if statistics["checker_invocations"] != 1:
            raise KernelRunnerWireError(
                "limited transcript invocation count is invalid"
            )
        if kind == "timed_out":
            if statistics["wall_time_ms"] != limits["wall_timeout_ms"]:
                raise KernelRunnerWireError(
                    "timed-out transcript does not reach the bound limit"
                )
            expected_outcome = "timed_out"
        else:
            if statistics["peak_memory_bytes"] != limits["max_memory_bytes"]:
                raise KernelRunnerWireError(
                    "memory-limit transcript does not reach the bound limit"
                )
            expected_outcome = "crashed"
    else:
        raise KernelRunnerWireError("transcript termination kind is not registered")
    classification = _object(
        transcript["classification"],
        {"status", "outcome", "reason"},
        "transcript.classification",
    )
    if classification != {
        "status": expected_status,
        "outcome": expected_outcome,
        "reason": expected_reason,
    }:
        raise KernelRunnerWireError(
            "transcript classification is not implied by termination"
        )
    return transcript


def validate_attestation(
    value: Any, invocation: dict[str, Any], transcript: dict[str, Any]
) -> dict[str, Any]:
    attestation = _object(
        value,
        {
            "schema_version",
            "kind",
            "attempt_id",
            "input_sha256",
            "invocation_sha256",
            "transcript_sha256",
            "runner",
            "isolation",
            "source_free",
        },
        "attestation",
    )
    if (
        attestation["schema_version"] != 1
        or type(attestation["schema_version"]) is not int
        or attestation["kind"] != "kernel_runner_attestation"
        or attestation["source_free"] is not True
    ):
        raise KernelRunnerWireError("attestation identity is invalid")
    if (
        attestation["attempt_id"] != invocation["attempt_id"]
        or attestation["input_sha256"] != invocation["input_sha256"]
        or attestation["invocation_sha256"] != invocation_sha256(invocation)
        or attestation["transcript_sha256"] != transcript_sha256(transcript)
    ):
        raise KernelRunnerWireError(
            "attestation does not bind the exact execution objects"
        )
    runner = _object(
        attestation["runner"],
        {
            "repository",
            "commit",
            "image_digest",
            "image_qualification",
            "architecture",
            "operating_system",
        },
        "attestation.runner",
    )
    if runner["repository"] != "leanprover/lean-eval-submissions":
        raise KernelRunnerWireError("attestation runner repository is not registered")
    _match(COMMIT, runner["commit"], "attestation.runner.commit")
    if not isinstance(runner["image_digest"], str) or not runner[
        "image_digest"
    ].startswith("sha256:"):
        raise KernelRunnerWireError("attestation runner image digest is invalid")
    _match(DIGEST, runner["image_digest"][7:], "attestation.runner.image_digest")
    if (
        runner["image_qualification"] != "unqualified"
        or runner["architecture"] != "x86_64"
        or runner["operating_system"] != "ubuntu-24.04"
    ):
        raise KernelRunnerWireError("attestation runner platform is not registered")
    isolation = _object(
        attestation["isolation"],
        {
            "fresh_instance",
            "network_disabled",
            "credentials_absent",
            "input_read_only",
            "destroyed",
        },
        "attestation.isolation",
    )
    if any(isolation[field] is not True for field in isolation):
        raise KernelRunnerWireError(
            "attestation does not assert the complete isolation boundary"
        )
    return attestation
