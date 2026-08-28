#!/usr/bin/env python3
"""Select and render one historical-private image qualification artifact.

This source-only helper never provisions or invokes Cloudflare itself.  It
closes the input, disposable Worker configuration, and source-free receipt for
the separately reviewed manual workflow.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import pathlib
import re
import sys
import tarfile
from typing import Any

from prepare_historical_private_replay import canonical
from replay_orchestrator import (
    canonical_archive_path,
    config_digest,
    replay_task_id,
    validate_execution_request,
)


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
CLOUDFLARE_ACCOUNT_ID = "a46b90978a1c29cc4795f30677e7e4b8"
CLOUDFLARE_WORKERS_SUBDOMAIN = "lean-eval.workers.dev"
OIDC_AUDIENCE = "lean-eval-historical-private-qualification"
OIDC_ENVIRONMENT = "cloudflare-production"
EXPECTED_ARCHITECTURE = "x86_64"
EXPECTED_CPU_MODEL = "AMD EPYC"
EXPECTED_KERNEL_RELEASE = "6.18.36-cloudflare-firecracker-2026.6.17"
PROBLEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")

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


def validate_registry_image(
    candidate_value: dict[str, Any],
    manifest_digest: str,
    manifest_path: pathlib.Path,
    config_path: pathlib.Path,
) -> str:
    """Verify an existing immutable tag is the exact reviewed image closure."""

    candidate = validate_candidate(candidate_value)
    if not matches(OCI_DIGEST, manifest_digest):
        raise QualificationError("registry manifest digest is invalid")
    try:
        manifest_raw = manifest_path.read_bytes()
        config_raw = config_path.read_bytes()
        manifest = json.loads(manifest_raw)
        image_config = json.loads(config_raw)
    except (OSError, json.JSONDecodeError, UnicodeError) as error:
        raise QualificationError("registry manifest or image config is invalid") from error
    if (
        not isinstance(manifest, dict)
        or not isinstance(image_config, dict)
        or manifest_digest != f"sha256:{sha256(manifest_raw)}"
        or not isinstance(manifest.get("config"), dict)
        or manifest["config"].get("digest") != f"sha256:{sha256(config_raw)}"
    ):
        raise QualificationError(
            "registry manifest does not bind the fetched exact image config"
        )
    config = image_config.get("config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    expected_labels = {
        "org.lean-eval.image-family": IMAGE_FAMILY,
        "org.lean-eval.image-matrix-sha256": MATRIX_SHA256,
        "org.lean-eval.image-source-commit": candidate["image_source_commit"],
        "org.lean-eval.benchmark-commit": candidate["benchmark_commit"],
        "org.lean-eval.image-source-closure-sha256": sha256(
            canonical(candidate)
        ),
    }
    if not isinstance(labels, dict) or any(
        labels.get(name) != value for name, value in expected_labels.items()
    ):
        raise QualificationError(
            "registry image labels differ from the exact qualification candidate"
        )
    return manifest_digest


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
        "probe_problem_id": entry["problem_ids"][0],
        "profile_lock": entry["profile_lock"],
        "source_blobs": source_blobs(source_root),
    }


def validate_candidate(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "kind", "image_source_repository", "image_source_commit",
        "matrix_sha256", "matrix_entry_sha256", "benchmark_commit", "benchmark_tree",
        "toolchain", "lean_toolchain_blob_sha256", "checker", "probe_problem_id",
        "profile_lock", "source_blobs",
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
        or not matches(PROBLEM_ID, value.get("probe_problem_id"))
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


def create_probe_archive(candidate: dict[str, Any], output: pathlib.Path) -> None:
    """Create a deterministic one-workspace archive with no historical source."""
    candidate = validate_candidate(candidate)
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise QualificationError("probe archive output is not one new file")
    problem_id = candidate["probe_problem_id"]
    members = {
        "source/proof/Submission.lean": b"by\n  sorry\n",
        "source/proof/lakefile.toml": f'name = "{problem_id}"\n'.encode("ascii"),
    }
    with output.open("xb") as raw_output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
                for directory in ("source", "source/proof"):
                    info = tarfile.TarInfo(directory)
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    info.mtime = 0
                    archive.addfile(info)
                for name, body in sorted(members.items()):
                    info = tarfile.TarInfo(name)
                    info.mode = 0o644
                    info.mtime = 0
                    info.size = len(body)
                    archive.addfile(info, io.BytesIO(body))


def _identity(domain: str, *parts: str) -> str:
    body = domain.encode("ascii") + b"\0" + b"\0".join(
        part.encode("ascii") for part in parts
    )
    return sha256(body)


def _positive_safe_integer(value: int, label: str) -> int:
    if isinstance(value, bool) or not 1 <= value <= 9_007_199_254_740_991:
        raise QualificationError(f"{label} is invalid")
    return value


def prepare_probe_inputs(
    candidate: dict[str, Any],
    plaintext_path: pathlib.Path,
    ciphertext_path: pathlib.Path,
    key_path: pathlib.Path,
    manifest_digest: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
    output_directory: pathlib.Path,
) -> dict[str, Any]:
    """Close one disposable Worker deployment and schema-v2 probe request."""
    candidate = validate_candidate(candidate)
    run_id = _positive_safe_integer(workflow_run_id, "workflow run ID")
    run_attempt = _positive_safe_integer(workflow_run_attempt, "workflow run attempt")
    if not matches(OCI_DIGEST, manifest_digest):
        raise QualificationError("registry manifest digest is invalid")
    if not output_directory.is_dir() or output_directory.is_symlink():
        raise QualificationError("probe output directory is not one real directory")
    if any(output_directory.iterdir()):
        raise QualificationError("probe output directory is not empty")
    try:
        plaintext = plaintext_path.read_bytes()
        ciphertext = ciphertext_path.read_bytes()
        key = key_path.read_bytes()
    except OSError as error:
        raise QualificationError("probe archive material is unavailable") from error
    if not plaintext or len(plaintext) > 10 * 1024 * 1024:
        raise QualificationError("probe plaintext archive is invalid")
    if not ciphertext or len(ciphertext) > 11 * 1024 * 1024 or len(key) != 16:
        raise QualificationError("probe ciphertext or file key is invalid")

    lock = candidate["profile_lock"]
    execution_profile = {
        "schema_version": 1,
        "runner_profile": lock["runner_profile"],
        "vm_image_digest": manifest_digest,
        "toolchain": lock["toolchain"],
        "architecture": EXPECTED_ARCHITECTURE,
        "cpu_model": EXPECTED_CPU_MODEL,
        "kernel_release": EXPECTED_KERNEL_RELEASE,
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
    run_text = str(run_id)
    attempt_text = str(run_attempt)
    binding = _identity(
        "lean-eval-private-qualification-binding-v1",
        candidate["image_source_commit"],
        candidate["benchmark_commit"],
        manifest_digest,
        run_text,
        attempt_text,
    )
    submission_id = f"01800000-0000-7000-8000-{binding[:12]}"
    result_id = "r2_" + _identity(
        "lean-eval-private-qualification-result-v1", binding
    )
    task_id = replay_task_id(result_id, measurement_digest)
    ciphertext_digest = sha256(ciphertext)
    request = {
        "schema_version": 1,
        "replay_task_id": task_id,
        "attempt": 1,
        "source": {
            "visibility": "private",
            "archive": {
                "schema_version": 1,
                "submission_id": submission_id,
                "archive_repository": SOURCE_REPOSITORY,
                "archive_commit": candidate["image_source_commit"],
                "archive_path": canonical_archive_path(submission_id),
                "archive_ciphertext_sha256": ciphertext_digest,
                "encrypted": True,
            },
        },
        "benchmark": {
            "repository": lock["benchmark_repository"],
            "commit": candidate["benchmark_commit"],
            "toolchain": candidate["toolchain"],
        },
        "result": {
            "result_id": result_id,
            "submission_id": submission_id,
            "problem_id": candidate["probe_problem_id"],
            "statement_revision": 1,
            "commit": candidate["image_source_commit"],
            "tree_digest": _identity(
                "lean-eval-private-qualification-tree-v1", binding
            ),
        },
        "checker": "nanoda",
        "execution_profile_digest": execution_digest,
        "measurement_config_digest": measurement_digest,
        "execution_profile": execution_profile,
        "measurement_config": MEASUREMENT_CONFIG,
        "network": {
            "fetch_phase": "controller_pinned_archive_only",
            "untrusted_execution_phase": "disabled",
        },
        "untrusted_environment": {},
    }
    validate_execution_request(request)
    expectation = {
        "schema_version": 2,
        "submission_id": submission_id,
        "archive_ciphertext_sha256": ciphertext_digest,
        "plaintext_tar_sha256": sha256(plaintext),
        "plaintext_tar_size": len(plaintext),
        "key_material_type": "age-file-key-v1",
    }
    outer = {
        "schema_version": 2,
        "runner_nonce": binding,
        "request": request,
        "archive_expectation": expectation,
        "ciphertext_base64": base64.b64encode(ciphertext).decode("ascii"),
        "key_material_type": "age-file-key-v1",
        "plaintext_key_material_base64": base64.b64encode(key).decode("ascii"),
    }
    status = {
        "schema_version": 1,
        "runner_nonce": binding,
        "replay_task_id": task_id,
        "attempt": 1,
        "execution_profile_digest": execution_digest,
        "measurement_config_digest": measurement_digest,
        "vm_image_digest": manifest_digest,
    }
    request_sha256 = sha256(canonical(outer))
    worker_name = f"lean-eval-private-q-{run_id}-{run_attempt}"
    container_application_name = f"le-q-{run_id}-{run_attempt}"
    if len(worker_name) > 63:
        raise QualificationError("disposable Worker name is too long")
    if len(container_application_name) > 32:
        raise QualificationError("disposable Container application name is too long")
    config = {
        "$schema": "node_modules/wrangler/config-schema.json",
        "name": worker_name,
        "main": (pathlib.Path.cwd() / "server/src/private-qualification-entry.ts")
        .resolve()
        .as_posix(),
        "account_id": CLOUDFLARE_ACCOUNT_ID,
        "compatibility_date": "2026-08-22",
        "compatibility_flags": ["nodejs_compat"],
        "workers_dev": True,
        "preview_urls": False,
        "observability": {"enabled": False},
        "containers": [{
            "name": container_application_name,
            "class_name": "PrivateQualificationSandbox",
            "image": (
                f"registry.cloudflare.com/{CLOUDFLARE_ACCOUNT_ID}/"
                f"{IMAGE_REPOSITORY}@{manifest_digest}"
            ),
            "instance_type": "standard-4",
            "max_instances": 1,
            "ssh": {"enabled": False},
        }],
        "durable_objects": {"bindings": [
            {"name": "REPLAY_SANDBOX", "class_name": "PrivateQualificationSandbox"},
            {"name": "REPLAY_TERMINAL_RECEIPT", "class_name": "ReplayTerminalReceipt"},
        ]},
        "migrations": [{
            "tag": "v1",
            "new_sqlite_classes": [
                "PrivateQualificationSandbox", "ReplayTerminalReceipt"
            ],
        }],
        "vars": {
            "DEPLOYED_COMMIT": candidate["image_source_commit"],
            "DEPLOYMENT_ENVIRONMENT": "private-qualification",
            "REPLAY_ENABLED": "true",
            "HISTORICAL_PUBLIC_REPLAY_ENABLED": "false",
            "STAGING_ACCEPTANCE_ENABLED": "false",
            "GITHUB_OIDC_AUDIENCE": OIDC_AUDIENCE,
            "GITHUB_OIDC_ENVIRONMENT": OIDC_ENVIRONMENT,
            "QUALIFICATION_RUN_ID": run_text,
            "QUALIFICATION_RUN_ATTEMPT": attempt_text,
            "EXPECTED_RUNNER_NONCE": binding,
            "EXPECTED_REPLAY_TASK_ID": task_id,
            "EXPECTED_REPLAY_ATTEMPT": "1",
            "EXPECTED_QUALIFICATION_REQUEST_SHA256": request_sha256,
            "STAGING_MEMORY_LIMIT_BYTES": str(MEASUREMENT_CONFIG["memory_limit_bytes"]),
            "PRODUCTION_MEMORY_GATE_BYTES": str(MEASUREMENT_CONFIG["memory_limit_bytes"]),
            "REVIEWED_EXECUTION_PROFILE_DIGEST": execution_digest,
            "REVIEWED_MEASUREMENT_CONFIG_DIGEST": measurement_digest,
            "REVIEWED_VM_IMAGE_DIGEST": manifest_digest,
            "SANDBOX_TRANSPORT": "rpc",
        },
    }
    context = {
        "schema_version": 1,
        "worker_name": worker_name,
        "worker_url": f"https://{worker_name}.{CLOUDFLARE_WORKERS_SUBDOMAIN}",
        "container_application_name": container_application_name,
        "registry_manifest_digest": manifest_digest,
        "benchmark_commit": candidate["benchmark_commit"],
        "runner_nonce": binding,
        "replay_task_id": task_id,
        "request_sha256": request_sha256,
        "workflow_run_id": run_id,
        "workflow_run_attempt": run_attempt,
        "architecture": EXPECTED_ARCHITECTURE,
        "kernel_release": EXPECTED_KERNEL_RELEASE,
        "cpu_model": EXPECTED_CPU_MODEL,
    }
    write_exclusive(output_directory / "wrangler.json", config)
    write_exclusive(output_directory / "request.json", outer)
    write_exclusive(output_directory / "status.json", status)
    write_exclusive(output_directory / "context.json", context)
    return context


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

    archive = subparsers.add_parser("create-probe-archive")
    archive.add_argument("--candidate", required=True, type=pathlib.Path)
    archive.add_argument("--output", required=True, type=pathlib.Path)

    prepare = subparsers.add_parser("prepare-probe")
    prepare.add_argument("--candidate", required=True, type=pathlib.Path)
    prepare.add_argument("--plaintext", required=True, type=pathlib.Path)
    prepare.add_argument("--ciphertext", required=True, type=pathlib.Path)
    prepare.add_argument("--file-key", required=True, type=pathlib.Path)
    prepare.add_argument("--registry-manifest-digest", required=True)
    prepare.add_argument("--workflow-run-id", required=True, type=int)
    prepare.add_argument("--workflow-run-attempt", required=True, type=int)
    prepare.add_argument("--output-directory", required=True, type=pathlib.Path)

    registry = subparsers.add_parser("validate-registry-image")
    registry.add_argument("--candidate", required=True, type=pathlib.Path)
    registry.add_argument("--manifest-digest", required=True)
    registry.add_argument("--manifest", required=True, type=pathlib.Path)
    registry.add_argument("--image-config", required=True, type=pathlib.Path)

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
        elif args.command == "render-qualification":
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
        elif args.command == "create-probe-archive":
            candidate, _ = load_canonical(args.candidate, "qualification candidate")
            create_probe_archive(candidate, args.output)
        elif args.command == "validate-registry-image":
            candidate, _ = load_canonical(args.candidate, "qualification candidate")
            print(
                validate_registry_image(
                    candidate,
                    args.manifest_digest,
                    args.manifest,
                    args.image_config,
                )
            )
        else:
            candidate, _ = load_canonical(args.candidate, "qualification candidate")
            context = prepare_probe_inputs(
                candidate,
                args.plaintext,
                args.ciphertext,
                args.file_key,
                args.registry_manifest_digest,
                args.workflow_run_id,
                args.workflow_run_attempt,
                args.output_directory,
            )
            print(json.dumps(context, separators=(",", ":"), sort_keys=True))
    except (OSError, QualificationError, ValueError) as error:
        print(f"historical-private-image-qualification: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
