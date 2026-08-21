#!/usr/bin/env python3
"""Prepare and validate one credential-free independent-kernel shadow smoke.

The smoke is deliberately non-authoritative: it neither changes the required
checker set nor writes Results or State.  It proves that one exact Arena
candidate can consume LeanEval's pinned exporter output for one reviewed,
already-accepted public solution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import sys
import tomllib
from typing import Any


COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
PROBLEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
TOOLCHAIN = re.compile(r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+")
SAFE_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")

FIXTURE_FIELDS = {
    "schema_version",
    "fixture_id",
    "problem_id",
    "statement_revision",
    "source",
    "benchmark",
    "exporter",
    "comparator",
    "candidate",
}
EVIDENCE_FIELDS = {
    "schema_version",
    "kind",
    "fixture_id",
    "fixture_sha256",
    "workflow_commit",
    "problem_id",
    "statement_revision",
    "source",
    "benchmark",
    "exporter",
    "comparator",
    "candidate",
    "outcome",
    "pipeline_wall_time_ms",
    "runner_observation",
}


class ShadowSmokeError(ValueError):
    """A fixture, workspace, or evidence file violates the smoke contract."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ShadowSmokeError(f"{label} must be an object with string keys")
    return value


def _fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ShadowSmokeError(
            f"{label} fields are not canonical; "
            f"missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _string(value: Any, label: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ShadowSmokeError(
            f"{label} must be a non-empty string of at most {maximum} UTF-8 bytes"
        )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ShadowSmokeError(f"{label} must not contain control characters")
    return value


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    text = _string(value, label)
    if pattern.fullmatch(text) is None:
        raise ShadowSmokeError(f"{label} is not canonical")
    return text


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ShadowSmokeError(f"{label} must be an integer >= {minimum}")
    return value


def _component(value: Any, label: str) -> dict[str, Any]:
    component = _object(value, label)
    _fields(component, {"repository", "commit"}, label)
    _match(REPOSITORY, component["repository"], f"{label}.repository")
    _match(COMMIT, component["commit"], f"{label}.commit")
    return component


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _load(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ShadowSmokeError(f"{path}: cannot read JSON: {error}") from error


def validate_fixture(value: Any) -> dict[str, Any]:
    fixture = _object(value, "fixture")
    _fields(fixture, FIXTURE_FIELDS, "fixture")
    if fixture["schema_version"] != 1 or isinstance(fixture["schema_version"], bool):
        raise ShadowSmokeError("fixture schema_version must be integer 1")
    _match(re.compile(r"[a-z0-9][a-z0-9_-]{0,127}"), fixture["fixture_id"], "fixture_id")
    _match(PROBLEM, fixture["problem_id"], "problem_id")
    revision = _integer(fixture["statement_revision"], "statement_revision", 1)
    if revision > 9_007_199_254_740_991:
        raise ShadowSmokeError("statement_revision must be IEEE-754 safe")

    source = _object(fixture["source"], "source")
    _fields(source, {"repository", "commit", "visibility"}, "source")
    _match(REPOSITORY, source["repository"], "source.repository")
    _match(COMMIT, source["commit"], "source.commit")
    if source["visibility"] != "public":
        raise ShadowSmokeError("kernel shadow smoke source must be public")

    benchmark = _object(fixture["benchmark"], "benchmark")
    _fields(
        benchmark,
        {"repository", "commit", "toolchain", "mathlib_commit"},
        "benchmark",
    )
    _match(REPOSITORY, benchmark["repository"], "benchmark.repository")
    _match(COMMIT, benchmark["commit"], "benchmark.commit")
    _match(TOOLCHAIN, benchmark["toolchain"], "benchmark.toolchain")
    _match(COMMIT, benchmark["mathlib_commit"], "benchmark.mathlib_commit")

    _component(fixture["exporter"], "exporter")
    _component(fixture["comparator"], "comparator")

    candidate = _object(fixture["candidate"], "candidate")
    _fields(
        candidate,
        {"name", "repository", "commit", "binary", "protocol", "arena"},
        "candidate",
    )
    if candidate["name"] != "mathgraph":
        raise ShadowSmokeError("v1 shadow fixture supports only the reviewed mathgraph candidate")
    _match(REPOSITORY, candidate["repository"], "candidate.repository")
    _match(COMMIT, candidate["commit"], "candidate.commit")
    if candidate["binary"] != "sokonanoda":
        raise ShadowSmokeError("candidate.binary must be sokonanoda")
    if candidate["protocol"] != "nanoda_config_file":
        raise ShadowSmokeError("candidate.protocol must be nanoda_config_file")

    arena = _object(candidate["arena"], "candidate.arena")
    _fields(
        arena,
        {
            "repository",
            "commit",
            "declaration_path",
            "accepted_passed",
            "accepted_total",
            "rejected_passed",
            "rejected_total",
            "declined",
        },
        "candidate.arena",
    )
    _match(REPOSITORY, arena["repository"], "candidate.arena.repository")
    _match(COMMIT, arena["commit"], "candidate.arena.commit")
    _match(SAFE_PATH, arena["declaration_path"], "candidate.arena.declaration_path")
    for field in (
        "accepted_passed",
        "accepted_total",
        "rejected_passed",
        "rejected_total",
        "declined",
    ):
        _integer(arena[field], f"candidate.arena.{field}")
    if arena["accepted_passed"] != arena["accepted_total"]:
        raise ShadowSmokeError("candidate Arena completeness is not clean")
    if arena["rejected_passed"] != arena["rejected_total"]:
        raise ShadowSmokeError("candidate Arena soundness is not clean")
    if arena["declined"] != 0:
        raise ShadowSmokeError("candidate Arena declaration contains declines")
    return fixture


def _copy_submission(source_root: pathlib.Path, workspace: pathlib.Path) -> None:
    if source_root.is_symlink() or not source_root.is_dir():
        raise ShadowSmokeError("source root must be a regular directory")
    source_file = source_root / "Submission.lean"
    if source_file.is_symlink() or not source_file.is_file():
        raise ShadowSmokeError("source Submission.lean must be a regular file")
    shutil.copyfile(source_file, workspace / "Submission.lean")

    source_dir = source_root / "Submission"
    destination_dir = workspace / "Submission"
    if not source_dir.exists():
        return
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise ShadowSmokeError("source Submission must be a regular directory")
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            raise ShadowSmokeError(f"refusing source symlink: {path}")
        relative = path.relative_to(source_dir)
        if path.is_dir():
            (destination_dir / relative).mkdir(parents=True, exist_ok=True)
        elif path.is_file() and path.suffix == ".lean":
            target = destination_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        elif path.is_file():
            raise ShadowSmokeError(f"refusing non-Lean submission helper: {path}")
        else:
            raise ShadowSmokeError(f"refusing non-regular submission entry: {path}")


def prepare_workspace(
    fixture_value: Any,
    *,
    source_root: pathlib.Path,
    generated_root: pathlib.Path,
    output: pathlib.Path,
    candidate_binary: pathlib.Path,
) -> None:
    fixture = validate_fixture(fixture_value)
    if output.exists():
        raise ShadowSmokeError("output workspace already exists")
    if candidate_binary.is_symlink() or not candidate_binary.is_file():
        raise ShadowSmokeError("candidate binary must be a regular file")
    if not os.access(candidate_binary, os.X_OK):
        raise ShadowSmokeError("candidate binary must be executable")

    pristine = generated_root / fixture["problem_id"]
    if pristine.is_symlink() or not pristine.is_dir():
        raise ShadowSmokeError("pristine generated workspace is missing or non-regular")
    try:
        toolchain = (pristine / "lean-toolchain").read_text(encoding="utf-8").strip()
        lakefile = tomllib.loads((pristine / "lakefile.toml").read_text(encoding="utf-8"))
        config = _object(_load(pristine / "config.json"), "pristine config")
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ShadowSmokeError(f"cannot inspect pristine workspace: {error}") from error
    if toolchain != fixture["benchmark"]["toolchain"]:
        raise ShadowSmokeError("pristine workspace toolchain does not match fixture")
    if lakefile.get("name") != fixture["problem_id"]:
        raise ShadowSmokeError("pristine workspace package name does not match problem_id")
    dependencies = lakefile.get("require")
    if not isinstance(dependencies, list) or len(dependencies) != 1:
        raise ShadowSmokeError("pristine workspace must contain one dependency")
    mathlib = _object(dependencies[0], "pristine mathlib dependency")
    if mathlib.get("name") != "mathlib" or mathlib.get("rev") != fixture["benchmark"]["mathlib_commit"]:
        raise ShadowSmokeError("pristine workspace Mathlib pin does not match fixture")
    _fields(
        config,
        {
            "challenge_module",
            "solution_module",
            "theorem_names",
            "permitted_axioms",
            "enable_nanoda",
        },
        "pristine config",
    )
    if config["enable_nanoda"] is not False:
        raise ShadowSmokeError("pristine config unexpectedly enables a required external kernel")

    shutil.copytree(pristine, output, symlinks=False)
    _copy_submission(source_root, output)
    del config["enable_nanoda"]
    config["external_kernels"] = {
        "mathgraph-noda": [str(candidate_binary.resolve(strict=True))]
    }
    (output / "config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_evidence(
    fixture_value: Any,
    *,
    workflow_commit: str,
    pipeline_exit_code: int,
    pipeline_wall_time_ms: int,
) -> dict[str, Any]:
    fixture = validate_fixture(fixture_value)
    _match(COMMIT, workflow_commit, "workflow_commit")
    _integer(pipeline_exit_code, "pipeline_exit_code")
    _integer(pipeline_wall_time_ms, "pipeline_wall_time_ms", 1)
    image_os = _string(os.environ.get("ImageOS"), "ImageOS")
    image_version = _string(os.environ.get("ImageVersion"), "ImageVersion")
    runner_arch = _string(os.environ.get("RUNNER_ARCH"), "RUNNER_ARCH")
    if image_os != "ubuntu24" or runner_arch != "X64":
        raise ShadowSmokeError("kernel shadow smoke requires ubuntu24/X64")
    return {
        "schema_version": 1,
        "kind": "kernel_shadow_smoke",
        "fixture_id": fixture["fixture_id"],
        "fixture_sha256": hashlib.sha256(canonical_bytes(fixture)).hexdigest(),
        "workflow_commit": workflow_commit,
        "problem_id": fixture["problem_id"],
        "statement_revision": fixture["statement_revision"],
        "source": fixture["source"],
        "benchmark": fixture["benchmark"],
        "exporter": fixture["exporter"],
        "comparator": fixture["comparator"],
        "candidate": fixture["candidate"],
        "outcome": "accepted" if pipeline_exit_code == 0 else "pipeline_failed",
        "pipeline_wall_time_ms": pipeline_wall_time_ms,
        "runner_observation": {
            "image_os": image_os,
            "image_version": image_version,
            "architecture": platform.machine(),
            "kernel_release": platform.release(),
        },
    }


def validate_evidence(value: Any) -> dict[str, Any]:
    evidence = _object(value, "evidence")
    _fields(evidence, EVIDENCE_FIELDS, "evidence")
    if evidence["schema_version"] != 1 or isinstance(evidence["schema_version"], bool):
        raise ShadowSmokeError("evidence schema_version must be integer 1")
    if evidence["kind"] != "kernel_shadow_smoke":
        raise ShadowSmokeError("evidence kind is invalid")
    _match(
        re.compile(r"[a-z0-9][a-z0-9_-]{0,127}"),
        evidence["fixture_id"],
        "evidence.fixture_id",
    )
    _match(DIGEST, evidence["fixture_sha256"], "evidence.fixture_sha256")
    _match(COMMIT, evidence["workflow_commit"], "evidence.workflow_commit")
    _match(PROBLEM, evidence["problem_id"], "evidence.problem_id")
    _integer(evidence["statement_revision"], "evidence.statement_revision", 1)
    source = _object(evidence["source"], "evidence.source")
    _fields(source, {"repository", "commit", "visibility"}, "evidence.source")
    _match(REPOSITORY, source["repository"], "evidence.source.repository")
    _match(COMMIT, source["commit"], "evidence.source.commit")
    if source["visibility"] != "public":
        raise ShadowSmokeError("evidence source must be public")
    benchmark = _object(evidence["benchmark"], "evidence.benchmark")
    _fields(
        benchmark,
        {"repository", "commit", "toolchain", "mathlib_commit"},
        "evidence.benchmark",
    )
    _match(REPOSITORY, benchmark["repository"], "evidence.benchmark.repository")
    _match(COMMIT, benchmark["commit"], "evidence.benchmark.commit")
    _match(TOOLCHAIN, benchmark["toolchain"], "evidence.benchmark.toolchain")
    _match(COMMIT, benchmark["mathlib_commit"], "evidence.benchmark.mathlib_commit")
    _component(evidence["exporter"], "evidence.exporter")
    _component(evidence["comparator"], "evidence.comparator")
    candidate = _object(evidence["candidate"], "evidence.candidate")
    reconstructed_fixture = validate_fixture(
        {
            "schema_version": 1,
            "fixture_id": evidence["fixture_id"],
            "problem_id": evidence["problem_id"],
            "statement_revision": evidence["statement_revision"],
            "source": source,
            "benchmark": benchmark,
            "exporter": evidence["exporter"],
            "comparator": evidence["comparator"],
            "candidate": candidate,
        }
    )
    expected_digest = hashlib.sha256(canonical_bytes(reconstructed_fixture)).hexdigest()
    if evidence["fixture_sha256"] != expected_digest:
        raise ShadowSmokeError("evidence fixture_sha256 does not bind its fixture fields")
    if evidence["outcome"] not in {"accepted", "pipeline_failed"}:
        raise ShadowSmokeError("evidence outcome is invalid")
    _integer(evidence["pipeline_wall_time_ms"], "evidence.pipeline_wall_time_ms", 1)
    runner = _object(evidence["runner_observation"], "evidence.runner_observation")
    _fields(
        runner,
        {"image_os", "image_version", "architecture", "kernel_release"},
        "evidence.runner_observation",
    )
    for field in runner:
        _string(runner[field], f"evidence.runner_observation.{field}")
    return evidence


def _write(value: Any, output: pathlib.Path | None) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(rendered)
    else:
        output.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_fixture_parser = subparsers.add_parser("validate-fixture")
    validate_fixture_parser.add_argument("--fixture", type=pathlib.Path, required=True)

    prepare_parser = subparsers.add_parser("prepare-workspace")
    prepare_parser.add_argument("--fixture", type=pathlib.Path, required=True)
    prepare_parser.add_argument("--source-root", type=pathlib.Path, required=True)
    prepare_parser.add_argument("--generated-root", type=pathlib.Path, required=True)
    prepare_parser.add_argument("--output", type=pathlib.Path, required=True)
    prepare_parser.add_argument("--candidate-binary", type=pathlib.Path, required=True)

    evidence_parser = subparsers.add_parser("evidence")
    evidence_parser.add_argument("--fixture", type=pathlib.Path, required=True)
    evidence_parser.add_argument("--workflow-commit", required=True)
    evidence_parser.add_argument("--pipeline-exit-code", type=int, required=True)
    evidence_parser.add_argument("--pipeline-wall-time-ms", type=int, required=True)
    evidence_parser.add_argument("--output", type=pathlib.Path)

    validate_evidence_parser = subparsers.add_parser("validate-evidence")
    validate_evidence_parser.add_argument("--evidence", type=pathlib.Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "validate-fixture":
            validate_fixture(_load(args.fixture))
        elif args.command == "prepare-workspace":
            prepare_workspace(
                _load(args.fixture),
                source_root=args.source_root,
                generated_root=args.generated_root,
                output=args.output,
                candidate_binary=args.candidate_binary,
            )
        elif args.command == "evidence":
            _write(
                build_evidence(
                    _load(args.fixture),
                    workflow_commit=args.workflow_commit,
                    pipeline_exit_code=args.pipeline_exit_code,
                    pipeline_wall_time_ms=args.pipeline_wall_time_ms,
                ),
                args.output,
            )
        else:
            validate_evidence(_load(args.evidence))
    except ShadowSmokeError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
