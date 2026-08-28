#!/usr/bin/env python3
"""Select and render one historical-private image qualification artifact.

This source-only helper deliberately does not provision or invoke a Cloudflare
Container.  The manual workflow must supply a source-free receipt from a
separately reviewed executor which actually ran the selected image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

from prepare_historical_private_replay import canonical
from replay_orchestrator import config_digest


MATRIX_SHA256 = "54ad4c237d08e5d0e298dfc8f752b25c89ce30e79b396a2256b4216a1c0f772c"
MATRIX_KIND = "historical_private_replay_image_matrix"
CANDIDATE_KIND = "historical_private_image_qualification_candidate"
RECEIPT_KIND = "historical_private_image_qualification_probe_receipt"
QUALIFICATION_KIND = "historical_private_replay_profile_qualification"
IMAGE_FAMILY = "lean-eval-authoritative-private-replay-v1"
IMAGE_REPOSITORY = "lean-eval-authoritative"
SOURCE_REPOSITORY = "leanprover/lean-eval-submissions"
WORKFLOW_PATH = ".github/workflows/historical-private-image-qualification.yml"
RUNNER_ENTRYPOINT = "/opt/lean-eval/replay-authoritative"

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
OCI_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")

SOURCE_PATHS = {
    "dockerfile": "Dockerfile.historical-private-replay",
    "dockerignore": "Dockerfile.historical-private-replay.dockerignore",
    "profile_matrix": "configuration/historical-private-replay-image-matrix-v1.json",
    "evaluator": "scripts/evaluate_submission.py",
    "orchestrator": "scripts/replay_orchestrator.py",
    "layer_preparation": "scripts/prepare_historical_image_layers.py",
    "runtime_helper": "server/replay-image/replay-authoritative",
    "measurement_helper": "server/replay-image/replay-measure",
    "comparator_patch": "server/replay-image/comparator-71b52-phase-metrics.patch",
    "age_file_key_go_mod": "server/age-file-key/go.mod",
    "age_file_key_go_sum": "server/age-file-key/go.sum",
    "age_file_key_main": "server/age-file-key/main.go",
}

MATRIX_FIELDS = {
    "schema_version",
    "kind",
    "benchmark_repository",
    "private_plan_sha256",
    "historical_public_profile_matrix_sha256",
    "historical_public_component_lock_sha256",
    "checker",
    "image_count",
    "toolchain_count",
    "result_count",
    "reused_public_source_count",
    "derived_exact_source_count",
    "images",
}
IMAGE_FIELDS = {
    "benchmark_commit",
    "benchmark_tree",
    "lean_toolchain_blob_sha256",
    "manifest_layout",
    "problem_ids",
    "profile_lock",
    "result_count",
    "source_pin_origin",
    "toolchain",
    "workspace_count",
}
PROFILE_LOCK_FIELDS = {
    "schema_version",
    "benchmark_repository",
    "benchmark_commit",
    "toolchain",
    "runner_profile",
    "go_toolchain",
    "rust_toolchain",
    "cache_state",
    "measurement_command",
    "components",
}
RECEIPT_FIELDS = {
    "schema_version",
    "kind",
    "registry_manifest_digest",
    "benchmark_commit",
    "runner_entrypoint",
    "archive_expectation_schema_version",
    "key_material_type",
    "network_probe",
    "status",
    "architecture",
    "kernel_release",
    "cpu_model",
}

MEASUREMENT_CONFIG = {
    "schema_version": 1,
    "memory_limit_bytes": 12 * 1024**3,
    "wall_time_limit_ms": 19_800_000,
    "retired_instructions": {
        "required": False,
        "perf_event": "instructions:u",
    },
}


class QualificationError(ValueError):
    """Qualification input or output is not the closed reviewed form."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def matches(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def load_canonical(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QualificationError(f"{label} is not readable UTF-8 JSON") from error
    if not isinstance(value, dict) or canonical(value) != raw:
        raise QualificationError(f"{label} is not canonical JSON")
    return value, raw


def require_fields(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise QualificationError(f"{label} fields are not closed")
    return value


def safe_text(value: Any, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise QualificationError(f"{label} is invalid")
    return value


def validate_matrix(value: dict[str, Any], raw: bytes) -> list[dict[str, Any]]:
    require_fields(value, MATRIX_FIELDS, "private image matrix")
    images = value.get("images")
    if (
        sha256(raw) != MATRIX_SHA256
        or value.get("schema_version") != 1
        or value.get("kind") != MATRIX_KIND
        or value.get("benchmark_repository") != "leanprover/lean-eval"
        or value.get("checker") != "nanoda"
        or value.get("image_count") != 63
        or value.get("toolchain_count") != 5
        or value.get("result_count") != 639
        or value.get("reused_public_source_count") != 21
        or value.get("derived_exact_source_count") != 42
        or not isinstance(images, list)
        or len(images) != 63
    ):
        raise QualificationError("private image matrix identity changed")
    commits: list[str] = []
    validated: list[dict[str, Any]] = []
    for index, image in enumerate(images):
        image = require_fields(image, IMAGE_FIELDS, f"private image matrix entry {index}")
        commit = image.get("benchmark_commit")
        lock = image.get("profile_lock")
        if (
            not isinstance(commit, str)
            or COMMIT.fullmatch(commit) is None
            or not isinstance(image.get("benchmark_tree"), str)
            or COMMIT.fullmatch(image["benchmark_tree"]) is None
            or not isinstance(image.get("lean_toolchain_blob_sha256"), str)
            or DIGEST.fullmatch(image["lean_toolchain_blob_sha256"]) is None
            or not isinstance(lock, dict)
            or set(lock) != PROFILE_LOCK_FIELDS
            or lock.get("schema_version") != 1
            or lock.get("benchmark_repository") != "leanprover/lean-eval"
            or lock.get("benchmark_commit") != commit
            or lock.get("toolchain") != image.get("toolchain")
            or lock.get("runner_profile") != "cloudflare-sandbox-standard-4-v1"
            or lock.get("measurement_command") != [RUNNER_ENTRYPOINT.replace("replay-authoritative", "replay-measure")]
        ):
            raise QualificationError("private image matrix entry identity changed")
        commits.append(commit)
        validated.append(image)
    if commits != sorted(commits) or len(commits) != len(set(commits)):
        raise QualificationError("private image matrix entries are not unique and sorted")
    return validated


def source_blobs(root: pathlib.Path) -> dict[str, dict[str, str]]:
    blobs: dict[str, dict[str, str]] = {}
    for name, relative in SOURCE_PATHS.items():
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise QualificationError(f"private image source is unavailable: {relative}")
        blobs[name] = {"path": relative, "sha256": sha256(path.read_bytes())}
    if blobs["profile_matrix"]["sha256"] != MATRIX_SHA256:
        raise QualificationError("private image matrix source changed")
    return blobs


def select_candidate(
    matrix_path: pathlib.Path,
    benchmark_commit: str,
    source_root: pathlib.Path,
    source_commit: str,
) -> dict[str, Any]:
    if not matches(COMMIT, benchmark_commit) or not matches(COMMIT, source_commit):
        raise QualificationError("commit input is not canonical")
    matrix, raw = load_canonical(matrix_path, "private image matrix")
    selected = [
        image for image in validate_matrix(matrix, raw)
        if image["benchmark_commit"] == benchmark_commit
    ]
    if len(selected) != 1:
        raise QualificationError("benchmark commit does not select exactly one of 63 images")
    entry = selected[0]
    return {
        "schema_version": 1,
        "kind": CANDIDATE_KIND,
        "image_source_repository": SOURCE_REPOSITORY,
        "image_source_commit": source_commit,
        "matrix_sha256": MATRIX_SHA256,
        "matrix_entry_sha256": sha256(canonical(entry)),
        "benchmark_commit": entry["benchmark_commit"],
        "benchmark_tree": entry["benchmark_tree"],
        "toolchain": entry["toolchain"],
        "lean_toolchain_blob_sha256": entry["lean_toolchain_blob_sha256"],
        "checker": "nanoda",
        "profile_lock": entry["profile_lock"],
        "source_blobs": source_blobs(source_root),
    }


def validate_candidate(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "kind", "image_source_repository", "image_source_commit",
        "matrix_sha256", "matrix_entry_sha256", "benchmark_commit", "benchmark_tree",
        "toolchain", "lean_toolchain_blob_sha256", "checker", "profile_lock",
        "source_blobs",
    }
    require_fields(value, fields, "qualification candidate")
    if (
        value["schema_version"] != 1
        or value["kind"] != CANDIDATE_KIND
        or value["image_source_repository"] != SOURCE_REPOSITORY
        or not matches(COMMIT, value.get("image_source_commit"))
        or value["matrix_sha256"] != MATRIX_SHA256
        or not matches(DIGEST, value.get("matrix_entry_sha256"))
        or not matches(COMMIT, value.get("benchmark_commit"))
        or not matches(COMMIT, value.get("benchmark_tree"))
        or not matches(DIGEST, value.get("lean_toolchain_blob_sha256"))
        or value["checker"] != "nanoda"
        or not isinstance(value["profile_lock"], dict)
        or set(value["profile_lock"]) != PROFILE_LOCK_FIELDS
        or value["profile_lock"].get("benchmark_commit") != value["benchmark_commit"]
        or value["profile_lock"].get("toolchain") != value["toolchain"]
        or not isinstance(value["source_blobs"], dict)
        or set(value["source_blobs"]) != set(SOURCE_PATHS)
    ):
        raise QualificationError("qualification candidate identity is invalid")
    for name, relative in SOURCE_PATHS.items():
        blob = value["source_blobs"][name]
        if (
            not isinstance(blob, dict)
            or set(blob) != {"path", "sha256"}
            or blob.get("path") != relative
            or not matches(DIGEST, blob.get("sha256"))
        ):
            raise QualificationError("qualification candidate source closure is invalid")
    return value


def validate_receipt(
    value: dict[str, Any], candidate: dict[str, Any], manifest_digest: str
) -> dict[str, Any]:
    require_fields(value, RECEIPT_FIELDS, "qualification probe receipt")
    if (
        value.get("schema_version") != 1
        or value.get("kind") != RECEIPT_KIND
        or value.get("registry_manifest_digest") != manifest_digest
        or value.get("benchmark_commit") != candidate["benchmark_commit"]
        or value.get("runner_entrypoint") != RUNNER_ENTRYPOINT
        or value.get("archive_expectation_schema_version") != 2
        or value.get("key_material_type") != "age-file-key-v1"
        or value.get("network_probe") != "blocked"
        or value.get("status") != "passed"
        or value.get("architecture") not in {"x86_64", "aarch64"}
    ):
        raise QualificationError("qualification probe receipt is not a passing exact probe")
    safe_text(value.get("kernel_release"), "probe kernel release", 256)
    safe_text(value.get("cpu_model"), "probe CPU model", 256)
    return value


def render_qualification(
    candidate: dict[str, Any],
    receipt: dict[str, Any],
    manifest_digest: str,
    workflow_commit: str,
    workflow_sha256: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
) -> tuple[str, dict[str, Any]]:
    candidate = validate_candidate(candidate)
    if (
        not matches(OCI_DIGEST, manifest_digest)
        or not matches(COMMIT, workflow_commit)
        or not matches(DIGEST, workflow_sha256)
        or isinstance(workflow_run_id, bool)
        or not 1 <= workflow_run_id <= 9_007_199_254_740_991
        or isinstance(workflow_run_attempt, bool)
        or not 1 <= workflow_run_attempt <= 9_007_199_254_740_991
    ):
        raise QualificationError("workflow or registry identity is invalid")
    receipt = validate_receipt(receipt, candidate, manifest_digest)
    lock = candidate["profile_lock"]
    execution_profile = {
        "schema_version": 1,
        "runner_profile": lock["runner_profile"],
        "vm_image_digest": manifest_digest,
        "toolchain": lock["toolchain"],
        "architecture": receipt["architecture"],
        "cpu_model": receipt["cpu_model"],
        "kernel_release": receipt["kernel_release"],
        "cache_state": lock["cache_state"],
        "measurement_command": lock["measurement_command"],
        "go_toolchain": lock["go_toolchain"],
        "rust_toolchain": lock["rust_toolchain"],
        "components": lock["components"],
    }
    execution_digest = config_digest(
        "lean-eval-replay-execution-profile-v1", execution_profile
    )
    measurement_digest = config_digest(
        "lean-eval-replay-measurement-config-v1", MEASUREMENT_CONFIG
    )
    value = {
        "schema_version": 1,
        "kind": QUALIFICATION_KIND,
        "qualification_status": "qualified",
        "image_family": IMAGE_FAMILY,
        "registry_repository": IMAGE_REPOSITORY,
        "registry_manifest_digest": manifest_digest,
        "image_source_repository": SOURCE_REPOSITORY,
        "image_source_commit": candidate["image_source_commit"],
        "source_blobs": candidate["source_blobs"],
        "qualification": {
            "workflow_repository": SOURCE_REPOSITORY,
            "workflow_commit": workflow_commit,
            "workflow_path": WORKFLOW_PATH,
            "workflow_sha256": workflow_sha256,
            "workflow_run_id": workflow_run_id,
            "workflow_run_attempt": workflow_run_attempt,
            "private_archive_probe": {
                "archive_expectation_schema_version": 2,
                "key_material_type": "age-file-key-v1",
                "runner_entrypoint": RUNNER_ENTRYPOINT,
                "status": "passed",
            },
            "network_probe": "blocked",
        },
        "benchmark_commit": candidate["benchmark_commit"],
        "benchmark_tree": candidate["benchmark_tree"],
        "toolchain": candidate["toolchain"],
        "lean_toolchain_blob_sha256": candidate["lean_toolchain_blob_sha256"],
        "checker": "nanoda",
        "measurement_config_digest": measurement_digest,
        "measurement_config": MEASUREMENT_CONFIG,
        "execution_profile": execution_profile,
        "execution_profile_digest": execution_digest,
    }
    return execution_digest, value


def write_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise QualificationError("output already exists")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise QualificationError("output parent is not one existing real directory")
    path.write_bytes(canonical(value))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select")
    select.add_argument("--matrix", required=True, type=pathlib.Path)
    select.add_argument("--benchmark-commit", required=True)
    select.add_argument("--source-root", required=True, type=pathlib.Path)
    select.add_argument("--source-commit", required=True)
    select.add_argument("--output", required=True, type=pathlib.Path)

    render = subparsers.add_parser("render-qualification")
    render.add_argument("--candidate", required=True, type=pathlib.Path)
    render.add_argument("--receipt", required=True, type=pathlib.Path)
    render.add_argument("--registry-manifest-digest", required=True)
    render.add_argument("--workflow-commit", required=True)
    render.add_argument("--workflow-sha256", required=True)
    render.add_argument("--workflow-run-id", required=True, type=int)
    render.add_argument("--workflow-run-attempt", required=True, type=int)
    render.add_argument("--output-directory", required=True, type=pathlib.Path)

    args = parser.parse_args()
    try:
        if args.command == "select":
            value = select_candidate(
                args.matrix,
                args.benchmark_commit,
                args.source_root.resolve(),
                args.source_commit,
            )
            write_exclusive(args.output, value)
        else:
            candidate, _ = load_canonical(args.candidate, "qualification candidate")
            receipt, _ = load_canonical(args.receipt, "qualification probe receipt")
            digest, value = render_qualification(
                candidate,
                receipt,
                args.registry_manifest_digest,
                args.workflow_commit,
                args.workflow_sha256,
                args.workflow_run_id,
                args.workflow_run_attempt,
            )
            output = args.output_directory / f"{digest}.json"
            write_exclusive(output, value)
            print(output.as_posix())
    except (OSError, QualificationError, ValueError) as error:
        print(f"historical-private-image-qualification: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
