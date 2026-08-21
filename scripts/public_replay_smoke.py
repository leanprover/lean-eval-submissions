#!/usr/bin/env python3
"""Validate one immutable legacy public replay fixture and its smoke evidence.

This is deliberately not the authoritative State replay writer.  It provides a
credential-free reproducibility check for an already-public historical result
while the disposable, profile-pinned replay backend is still launch-gated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import sys
from typing import Any


COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
PROBLEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
TOOLCHAIN = re.compile(r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+")
TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z"
)

CONFIG_FIELDS = {
    "schema_version",
    "fixture_id",
    "issue_number",
    "submitter",
    "declared_model",
    "problem_id",
    "solved_at",
    "source",
    "benchmark",
    "evaluator",
    "checker",
}
EVIDENCE_FIELDS = {
    "schema_version",
    "kind",
    "fixture_sha256",
    "workflow_commit",
    "source",
    "benchmark",
    "evaluator",
    "problem_id",
    "outcome",
    "runner_observation",
    "statistics",
}


class SmokeError(ValueError):
    """A fixture or evidence file violates the public-replay smoke contract."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SmokeError(f"{label} must be an object with string keys")
    return value


def _fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise SmokeError(
            f"{label} fields are not canonical; "
            f"missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _string(value: Any, label: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise SmokeError(f"{label} must be a non-empty string of at most {maximum} UTF-8 bytes")
    if any(ord(character) < 0x20 for character in value):
        raise SmokeError(f"{label} must not contain control characters")
    return value


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    text = _string(value, label)
    if pattern.fullmatch(text) is None:
        raise SmokeError(f"{label} is not canonical")
    return text


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SmokeError(f"{label} must be a positive integer")
    return value


def _load(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SmokeError(f"{path}: cannot read JSON: {error}") from error


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def validate_config(value: Any) -> dict[str, Any]:
    config = _object(value, "fixture")
    _fields(config, CONFIG_FIELDS, "fixture")
    if config["schema_version"] != 1 or isinstance(config["schema_version"], bool):
        raise SmokeError("fixture schema_version must be integer 1")
    _match(re.compile(r"[a-z0-9][a-z0-9_-]{0,127}"), config["fixture_id"], "fixture_id")
    _positive_integer(config["issue_number"], "issue_number")
    _match(re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?"), config["submitter"], "submitter")
    _string(config["declared_model"], "declared_model", maximum=256)
    _match(PROBLEM, config["problem_id"], "problem_id")
    _match(TIMESTAMP, config["solved_at"], "solved_at")

    source = _object(config["source"], "source")
    _fields(source, {"repository", "commit", "visibility"}, "source")
    _match(REPOSITORY, source["repository"], "source.repository")
    _match(COMMIT, source["commit"], "source.commit")
    if source["visibility"] != "public":
        raise SmokeError("public replay fixture source must be public")

    benchmark = _object(config["benchmark"], "benchmark")
    _fields(benchmark, {"repository", "commit", "toolchain"}, "benchmark")
    _match(REPOSITORY, benchmark["repository"], "benchmark.repository")
    _match(COMMIT, benchmark["commit"], "benchmark.commit")
    _match(TOOLCHAIN, benchmark["toolchain"], "benchmark.toolchain")

    evaluator = _object(config["evaluator"], "evaluator")
    _fields(evaluator, {"repository", "commit", "workflow_run_id"}, "evaluator")
    _match(REPOSITORY, evaluator["repository"], "evaluator.repository")
    _match(COMMIT, evaluator["commit"], "evaluator.commit")
    _positive_integer(evaluator["workflow_run_id"], "evaluator.workflow_run_id")
    if config["checker"] != "nanoda":
        raise SmokeError("public replay smoke supports only nanoda")
    return config


def _source_statistics(root: pathlib.Path) -> tuple[int, int]:
    if root.is_symlink() or not root.is_dir():
        raise SmokeError("source root must be a regular directory")
    candidates = [root / "Submission.lean"]
    submission_dir = root / "Submission"
    if submission_dir.is_dir() and not submission_dir.is_symlink():
        candidates.extend(sorted(submission_dir.rglob("*.lean")))
    files = 0
    lines = 0
    for path in candidates:
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise SmokeError(f"refusing non-regular replay source file: {path}")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise SmokeError(f"cannot read replay source file {path}: {error}") from error
        files += 1
        lines += len(content.splitlines())
    if files == 0:
        raise SmokeError("replay source contains no Submission Lean files")
    return files, lines


def _cpu_model() -> str:
    try:
        for line in pathlib.Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name") and ":" in line:
                return _string(line.split(":", 1)[1].strip(), "runner cpu model")
    except OSError:
        pass
    return _string(platform.processor() or "unreported", "runner cpu model")


def _counter(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "unavailable", "reason": "counter_not_reported"}
    raw = path.read_text(encoding="utf-8", errors="strict")
    for line in raw.splitlines():
        fields = line.split(",")
        if len(fields) >= 3 and fields[2].strip() == "instructions:u":
            digits = fields[0].strip().replace(" ", "")
            if digits.isdigit():
                return {"status": "measured", "value": int(digits)}
    if "not supported" in raw.lower():
        reason = "counter_not_supported"
    elif "permission" in raw.lower() or "access" in raw.lower():
        reason = "counter_permission_denied"
    else:
        reason = "counter_not_reported"
    return {"status": "unavailable", "reason": reason}


def build_evidence(
    config_value: Any,
    results_value: Any,
    summary_value: Any,
    *,
    source_dir: pathlib.Path,
    workflow_commit: str,
    wall_time_ms: int,
    counter_path: pathlib.Path,
) -> dict[str, Any]:
    config = validate_config(config_value)
    _match(COMMIT, workflow_commit, "workflow_commit")
    _positive_integer(wall_time_ms, "pipeline_wall_time_ms")
    results = _object(results_value, "results")
    if set(results) != {"passed", "statement_revisions"}:
        raise SmokeError("results fields are not canonical")
    if results["passed"] != [config["problem_id"]]:
        raise SmokeError("replay did not reproduce exactly the recorded solved problem")
    revisions = _object(results["statement_revisions"], "statement_revisions")
    if set(revisions) != {config["problem_id"]}:
        raise SmokeError("replay statement revision evidence is not exact")
    _positive_integer(revisions[config["problem_id"]], "statement_revision")

    summary = _object(summary_value, "summary")
    run_eval = _object(summary.get("run_eval"), "summary.run_eval")
    problems = run_eval.get("problems")
    if not isinstance(problems, list):
        raise SmokeError("summary.run_eval.problems must be an array")
    matching = [
        item
        for item in problems
        if isinstance(item, dict) and item.get("id") == config["problem_id"]
    ]
    if len(matching) != 1 or matching[0].get("succeeded") is not True:
        raise SmokeError("replay summary does not contain one successful target")
    if matching[0].get("exit_code") != 0:
        raise SmokeError("replay checker did not exit successfully")

    file_count, lines_of_code = _source_statistics(source_dir)
    image_os = _string(os.environ.get("ImageOS"), "ImageOS")
    image_version = _string(os.environ.get("ImageVersion"), "ImageVersion")
    runner_arch = _string(os.environ.get("RUNNER_ARCH"), "RUNNER_ARCH")
    if image_os != "ubuntu24" or runner_arch != "X64":
        raise SmokeError("public replay smoke requires the reviewed ubuntu24/X64 runner class")

    return {
        "schema_version": 1,
        "kind": "public_replay_smoke",
        "fixture_sha256": hashlib.sha256(canonical_bytes(config)).hexdigest(),
        "workflow_commit": workflow_commit,
        "source": config["source"],
        "benchmark": config["benchmark"],
        "evaluator": config["evaluator"],
        "problem_id": config["problem_id"],
        "outcome": "accepted",
        "runner_observation": {
            "image_os": image_os,
            "image_version": image_version,
            "architecture": platform.machine(),
            "kernel_release": platform.release(),
            "cpu_model": _cpu_model(),
            "cache_state": "cold",
            "untrusted_network": "disabled_by_landrun",
        },
        "statistics": {
            "pipeline_wall_time_ms": wall_time_ms,
            "pipeline_retired_instructions": _counter(counter_path),
            "lines_of_code": lines_of_code,
            "file_count": file_count,
        },
    }


def validate_evidence(value: Any) -> dict[str, Any]:
    evidence = _object(value, "evidence")
    _fields(evidence, EVIDENCE_FIELDS, "evidence")
    if evidence["schema_version"] != 1 or evidence["kind"] != "public_replay_smoke":
        raise SmokeError("evidence must be public-replay smoke schema version 1")
    _match(DIGEST, evidence["fixture_sha256"], "fixture_sha256")
    _match(COMMIT, evidence["workflow_commit"], "workflow_commit")
    if evidence["outcome"] != "accepted":
        raise SmokeError("smoke evidence outcome must be accepted")
    _match(PROBLEM, evidence["problem_id"], "problem_id")
    # Reuse the strict nested fixture decoder for immutable identities.
    validate_config({
        "schema_version": 1,
        "fixture_id": "evidence_validation",
        "issue_number": 1,
        "submitter": "evidence",
        "declared_model": "evidence",
        "problem_id": evidence["problem_id"],
        "solved_at": "2026-01-01T00:00:00.000Z",
        "source": evidence["source"],
        "benchmark": evidence["benchmark"],
        "evaluator": evidence["evaluator"],
        "checker": "nanoda",
    })
    runner = _object(evidence["runner_observation"], "runner_observation")
    _fields(
        runner,
        {
            "image_os",
            "image_version",
            "architecture",
            "kernel_release",
            "cpu_model",
            "cache_state",
            "untrusted_network",
        },
        "runner_observation",
    )
    for field in ("image_os", "image_version", "architecture", "kernel_release", "cpu_model"):
        _string(runner[field], f"runner_observation.{field}")
    if runner["cache_state"] != "cold" or runner["untrusted_network"] != "disabled_by_landrun":
        raise SmokeError("runner observation policy is not canonical")
    statistics = _object(evidence["statistics"], "statistics")
    _fields(
        statistics,
        {
            "pipeline_wall_time_ms",
            "pipeline_retired_instructions",
            "lines_of_code",
            "file_count",
        },
        "statistics",
    )
    _positive_integer(statistics["pipeline_wall_time_ms"], "pipeline_wall_time_ms")
    for field in ("lines_of_code", "file_count"):
        _positive_integer(statistics[field], field)
    counter = _object(statistics["pipeline_retired_instructions"], "pipeline_retired_instructions")
    if counter.get("status") == "measured":
        _fields(counter, {"status", "value"}, "pipeline_retired_instructions")
        if (
            isinstance(counter["value"], bool)
            or not isinstance(counter["value"], int)
            or counter["value"] < 0
        ):
            raise SmokeError("measured instruction count must be a nonnegative integer")
    elif counter.get("status") == "unavailable":
        _fields(counter, {"status", "reason"}, "pipeline_retired_instructions")
        if counter["reason"] not in {
            "counter_not_reported",
            "counter_not_supported",
            "counter_permission_denied",
        }:
            raise SmokeError("instruction counter unavailable reason is not registered")
    else:
        raise SmokeError("instruction counter status is invalid")
    return evidence


def _write(value: Any, path: pathlib.Path) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_config_parser = subparsers.add_parser("validate-config")
    validate_config_parser.add_argument("--config", type=pathlib.Path, required=True)
    evidence_parser = subparsers.add_parser("evidence")
    evidence_parser.add_argument("--config", type=pathlib.Path, required=True)
    evidence_parser.add_argument("--results", type=pathlib.Path, required=True)
    evidence_parser.add_argument("--summary", type=pathlib.Path, required=True)
    evidence_parser.add_argument("--source-dir", type=pathlib.Path, required=True)
    evidence_parser.add_argument("--workflow-commit", required=True)
    evidence_parser.add_argument("--wall-time-ms", type=int, required=True)
    evidence_parser.add_argument("--counter", type=pathlib.Path, required=True)
    evidence_parser.add_argument("--output", type=pathlib.Path, required=True)
    validate_evidence_parser = subparsers.add_parser("validate-evidence")
    validate_evidence_parser.add_argument("--evidence", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-config":
            validate_config(_load(args.config))
        elif args.command == "evidence":
            evidence = build_evidence(
                _load(args.config),
                _load(args.results),
                _load(args.summary),
                source_dir=args.source_dir,
                workflow_commit=args.workflow_commit,
                wall_time_ms=args.wall_time_ms,
                counter_path=args.counter,
            )
            validate_evidence(evidence)
            _write(evidence, args.output)
        else:
            validate_evidence(_load(args.evidence))
    except SmokeError as error:
        print(f"public-replay-smoke: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
