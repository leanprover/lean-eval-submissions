#!/usr/bin/env python3
"""Closed controller for one historical image publication and staging probe."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import pathlib
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from typing import Any

COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
OCI_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAX_JSON_BYTES = 1024 * 1024
MAX_ARTIFACT_ZIP_BYTES = 4 * 1024 * 1024
MAX_SAFE_INTEGER = 9_007_199_254_740_991
API_TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"
)
MATRIX_SHA256 = "a674707eea7a9556576c8dcbe57bcf6b4f44362d2bdfd47895fb7c783554f39c"
CONTRACT_SHA256 = "937a1ce9800350de47fb2ce0c3d276b6cddc38cd39820727c8b8687bea89dad0"
UNREVIEWED_DIGEST = "0" * 64
EXECUTOR_FAILURE_REASONS = {
    "input_transfer_failed",
    "command_rpc_failed",
    "command_failed",
    "command_output_invalid",
    "sandbox_destroy_failed",
    "unexpected_failure",
}
EXECUTOR_FAILURE_DETAILS = {
    "archive_decryption_failed",
    "archive_expansion_too_large",
    "archive_input_invalid",
    "archive_invalid",
    "archive_member_count_invalid",
    "archive_member_unsafe",
    "archive_plaintext_identity_mismatch",
    "benchmark_identity_mismatch",
    "benchmark_identity_unavailable",
    "ciphertext_digest_mismatch",
    "decoded_input_too_large",
    "encoded_input_invalid",
    "evaluator_did_not_terminate",
    "evaluator_preflight_failed",
    "evaluator_results_invalid",
    "evaluator_results_unavailable",
    "evaluator_unavailable",
    "expectation_fields_invalid",
    "expectation_invalid",
    "expectation_schema_invalid",
    "execution_request_invalid",
    "measurement_evidence_invalid",
    "measurement_evidence_unavailable",
    "measurement_limits_mismatch",
    "network_isolation_failed",
    "profile_lock_mismatch",
    "plaintext_digest_mismatch",
    "plaintext_size_mismatch",
    "runtime_profile_mismatch",
    "unclassified_archive_failure",
    "unclassified_authoritative_failure",
    "verdict_invalid",
    "workspace_not_found",
}
DIAGNOSTIC_OUTCOMES = {
    "initialized",
    "transport_failed",
    "response_too_large",
    "invalid_http_status",
    "executor_failed",
    "invalid_failure_response",
    "probe_succeeded",
    "evidence_invalid",
    "evidence_validated",
}


class QualificationError(ValueError):
    """The image candidate or staging evidence is not canonical."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load(path: pathlib.Path, expected_sha256: str | None = None) -> Any:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_JSON_BYTES:
            raise QualificationError(f"{path.name} exceeds its size limit")
        if expected_sha256 is not None and sha256_bytes(raw) != expected_sha256:
            raise QualificationError(f"{path.name} digest changed")
        value = json.loads(raw.decode("utf-8"))
    except QualificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QualificationError(f"{path.name} is invalid") from error
    if canonical(value) != raw:
        raise QualificationError(f"{path.name} is not canonical")
    return value


def load_external(path: pathlib.Path) -> Any:
    """Load bounded JSON emitted by a remote service without rewriting its bytes."""
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_JSON_BYTES:
            raise QualificationError(f"{path.name} exceeds its size limit")
        return json.loads(raw.decode("utf-8"))
    except QualificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QualificationError(f"{path.name} is invalid") from error


def positive_integer(value: Any, label: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_INTEGER:
        raise QualificationError(f"{label} is invalid")
    return value


def load_created_artifact_zip(
    path: pathlib.Path,
    expected_sha256: str,
    expected_size: int,
) -> dict[str, tuple[dict[str, Any], bytes]]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise QualificationError("created publication artifact is unreadable") from error
    if (
        not 1 <= len(raw) <= MAX_ARTIFACT_ZIP_BYTES
        or len(raw) != expected_size
        or sha256_bytes(raw) != expected_sha256
    ):
        raise QualificationError("created publication artifact digest changed")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise QualificationError("created publication artifact is not one ZIP") from error
    expected_names = {
        "candidate-binding.json",
        "historical-image-publication.json",
    }
    if {entry.filename for entry in entries} != expected_names or len(entries) != 2:
        raise QualificationError("created publication artifact member set changed")
    output: dict[str, tuple[dict[str, Any], bytes]] = {}
    total = 0
    for entry in entries:
        pure = pathlib.PurePosixPath(entry.filename)
        mode = entry.external_attr >> 16
        if (
            pure.is_absolute()
            or len(pure.parts) != 1
            or any(part in {"", ".", ".."} for part in pure.parts)
            or entry.is_dir()
            or entry.flag_bits & 0x1
            or not 1 <= entry.file_size <= MAX_JSON_BYTES
            or entry.compress_size > MAX_JSON_BYTES
            or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            or (mode != 0 and not stat.S_ISREG(mode))
        ):
            raise QualificationError("created publication artifact member is unsafe")
        total += entry.file_size
        if total > MAX_JSON_BYTES:
            raise QualificationError("created publication artifact expands too far")
        try:
            member_raw = archive.read(entry)
            value = json.loads(member_raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as error:
            raise QualificationError("created publication artifact member is invalid") from error
        if not isinstance(value, dict) or canonical(value) != member_raw:
            raise QualificationError("created publication artifact member is not canonical")
        output[entry.filename] = (value, member_raw)
    archive.close()
    return output


def exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise QualificationError(f"{label} fields changed")
    return value


def selected(matrix_path: pathlib.Path, benchmark_commit: str) -> tuple[dict[str, Any], str]:
    if COMMIT.fullmatch(benchmark_commit) is None:
        raise QualificationError("benchmark commit is invalid")
    matrix = load(matrix_path, MATRIX_SHA256)
    if (
        not isinstance(matrix, dict)
        or matrix.get("image_count") != 35
        or matrix.get("qualification_status") != "unqualified"
        or not isinstance(matrix.get("images"), list)
        or len(matrix["images"]) != 35
    ):
        raise QualificationError("profile matrix is not the unqualified 35-image matrix")
    if any(image.get("qualification_status") != "unqualified" for image in matrix["images"]):
        raise QualificationError("a profile matrix entry is not unqualified")
    matches = [image for image in matrix["images"] if image.get("benchmark_commit") == benchmark_commit]
    if len(matches) != 1:
        raise QualificationError("benchmark commit does not select exactly one matrix entry")
    entry = matches[0]
    return entry, sha256_bytes(canonical(entry))


def qualification_contract(path: pathlib.Path) -> dict[str, Any]:
    contract = load(path, CONTRACT_SHA256)
    expected = {
        "schema_version": 1,
        "kind": "historical_public_image_qualification_contract",
        "qualification_status": "unqualified",
        "image_count": 35,
        "instance_type": "standard-4",
        "vcpu": 4,
        "memory_limit_bytes": 12_884_901_888,
        "disk_size_mb": 20_000,
        "max_instances": 1,
        "ssh_enabled": False,
        "network": "disabled",
        "replay_enabled": False,
        "staging_acceptance_enabled": True,
        "architecture": "x86_64",
        "kernel_release_requirement": "nonempty",
        "cpu_model_requirement": "nonempty",
        "registry_repository": "lean-eval-historical-public-v1",
        "worker_name": "lean-eval-historical-qualifier-staging",
        "container_application": "lean-eval-historical-qualifier-staging-replaysandbox-staging",
        "destruction_probe_count": 2,
    }
    if contract != expected:
        raise QualificationError("qualification contract changed")
    return contract


def candidate(matrix_path: pathlib.Path, contract_path: pathlib.Path, benchmark_commit: str) -> dict[str, Any]:
    entry, entry_digest = selected(matrix_path, benchmark_commit)
    contract = qualification_contract(contract_path)
    profile_digest = sha256_bytes(canonical(entry["profile_lock"]))
    return {
        "benchmark_commit": benchmark_commit,
        "benchmark_tree": entry["benchmark_tree"],
        "entry_sha256": entry_digest,
        "profile_lock_sha256": profile_digest,
        "first_problem_id": entry["problem_ids"][0],
        "lean_toolchain_blob_sha256": entry["lean_toolchain_blob_sha256"],
        "manifest_layout": entry["manifest_layout"],
        "qualification_status": "unqualified",
        "registry_repository": contract["registry_repository"],
        "registry_tag_prefix": benchmark_commit,
        "toolchain": entry["toolchain"],
        "workspace_count": entry["workspace_count"],
    }


def render_config(
    matrix_path: pathlib.Path,
    contract_path: pathlib.Path,
    benchmark_commit: str,
    account_id: str,
    manifest_digest: str,
    source_commit: str,
    image_source_commit: str,
) -> dict[str, Any]:
    item = candidate(matrix_path, contract_path, benchmark_commit)
    contract = qualification_contract(contract_path)
    if re.fullmatch(r"[0-9a-f]{32}", account_id) is None:
        raise QualificationError("Cloudflare account id is invalid")
    if (
        OCI_DIGEST.fullmatch(manifest_digest) is None
        or COMMIT.fullmatch(source_commit) is None
        or COMMIT.fullmatch(image_source_commit) is None
    ):
        raise QualificationError("deployment binding is invalid")
    image = (
        f"registry.cloudflare.com/{account_id}/{item['registry_repository']}:"
        f"{benchmark_commit}-{image_source_commit}@{manifest_digest}"
    )
    return {
        "$schema": "../server/node_modules/wrangler/config-schema.json",
        "name": "lean-eval-historical-public-qualification",
        "main": "../server/src/replay-entry.ts",
        "compatibility_date": "2026-08-22",
        "compatibility_flags": ["nodejs_compat"],
        "workers_dev": False,
        "preview_urls": False,
        "observability": {"enabled": True, "head_sampling_rate": 1},
        "env": {"staging": {
            "name": contract["worker_name"],
            "workers_dev": True,
            "preview_urls": False,
            "containers": [{
                "class_name": "ReplaySandbox",
                "image": image,
                "instance_type": contract["instance_type"],
                "max_instances": contract["max_instances"],
                "ssh": {"enabled": False},
            }],
            "durable_objects": {"bindings": [
                {"name": "REPLAY_SANDBOX", "class_name": "ReplaySandbox"},
                {"name": "REPLAY_TERMINAL_RECEIPT", "class_name": "ReplayTerminalReceipt"},
            ]},
            "migrations": [
                {"tag": "v1", "new_sqlite_classes": ["ReplaySandbox"]},
                {"tag": "v2", "new_sqlite_classes": ["ReplayTerminalReceipt"]},
            ],
            "vars": {
                "DEPLOYED_COMMIT": source_commit,
                "DEPLOYMENT_ENVIRONMENT": "staging",
                "REPLAY_ENABLED": "false",
                "STAGING_ACCEPTANCE_ENABLED": "true",
                "GITHUB_OIDC_AUDIENCE": "lean-eval-historical-public-qualification-staging",
                "GITHUB_OIDC_ENVIRONMENT": "replay-staging",
                "STAGING_MEMORY_LIMIT_BYTES": str(contract["memory_limit_bytes"]),
                "PRODUCTION_MEMORY_GATE_BYTES": str(contract["memory_limit_bytes"]),
                "REVIEWED_EXECUTION_PROFILE_DIGEST": UNREVIEWED_DIGEST,
                "REVIEWED_MEASUREMENT_CONFIG_DIGEST": UNREVIEWED_DIGEST,
                "REVIEWED_VM_IMAGE_DIGEST": manifest_digest,
                "SANDBOX_TRANSPORT": "rpc",
            },
        }},
    }


def validate_created_publication_origin(
    matrix_path: pathlib.Path,
    contract_path: pathlib.Path,
    benchmark_commit: str,
    source_commit: str,
    image_source_commit: str,
    manifest_digest: str,
    run_id: int,
    run_attempt: int,
    artifact_id: int,
    current_run_id: int,
    run_metadata_path: pathlib.Path,
    artifact_metadata_path: pathlib.Path,
    artifact_zip_path: pathlib.Path,
    dockerfile_path: pathlib.Path,
    layer_preparation_path: pathlib.Path,
) -> dict[str, Any]:
    """Bind a resumed probe to one exact successful create-only build."""
    if (
        COMMIT.fullmatch(source_commit) is None
        or image_source_commit != source_commit
        or OCI_DIGEST.fullmatch(manifest_digest) is None
    ):
        raise QualificationError("created publication source binding is invalid")
    for value, label in (
        (run_id, "created publication run id"),
        (run_attempt, "created publication run attempt"),
        (artifact_id, "created publication artifact id"),
        (current_run_id, "current workflow run id"),
    ):
        positive_integer(value, label)
    if run_id == current_run_id:
        raise QualificationError("created publication cannot reference the current run")
    run = load_external(run_metadata_path)
    started_at = run.get("run_started_at") if isinstance(run, dict) else None
    completed_at = run.get("updated_at") if isinstance(run, dict) else None
    if (
        not isinstance(run, dict)
        or run.get("id") != run_id
        or run.get("run_attempt") != run_attempt
        or run.get("event") != "workflow_dispatch"
        or run.get("head_sha") != source_commit
        or run.get("head_branch") != f"lean-eval-dispatch/{source_commit}"
        or run.get("path")
        != ".github/workflows/historical-public-image-qualification.yml"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or not isinstance(started_at, str)
        or API_TIMESTAMP.fullmatch(started_at) is None
        or not isinstance(completed_at, str)
        or API_TIMESTAMP.fullmatch(completed_at) is None
        or started_at > completed_at
    ):
        raise QualificationError("created publication run is not exact and successful")
    artifact = load_external(artifact_metadata_path)
    workflow_run = artifact.get("workflow_run") if isinstance(artifact, dict) else None
    digest = artifact.get("digest") if isinstance(artifact, dict) else None
    created_at = artifact.get("created_at") if isinstance(artifact, dict) else None
    size = artifact.get("size_in_bytes") if isinstance(artifact, dict) else None
    if (
        not isinstance(artifact, dict)
        or artifact.get("id") != artifact_id
        or artifact.get("name") != "historical-public-image-candidate"
        or artifact.get("expired") is not False
        or not isinstance(workflow_run, dict)
        or workflow_run.get("id") != run_id
        or workflow_run.get("head_sha") != source_commit
        or workflow_run.get("head_branch")
        != f"lean-eval-dispatch/{source_commit}"
        or not isinstance(digest, str)
        or OCI_DIGEST.fullmatch(digest) is None
        or not isinstance(created_at, str)
        or API_TIMESTAMP.fullmatch(created_at) is None
        or not started_at <= created_at <= completed_at
        or type(size) is not int
        or not 1 <= size <= MAX_ARTIFACT_ZIP_BYTES
    ):
        raise QualificationError("created publication artifact metadata changed")
    members = load_created_artifact_zip(
        artifact_zip_path,
        digest.removeprefix("sha256:"),
        size,
    )
    binding = members["candidate-binding.json"][0]
    expected_config = render_config(
        matrix_path,
        contract_path,
        benchmark_commit,
        "0" * 32,
        manifest_digest,
        source_commit,
        image_source_commit,
    )
    expected_binding = {
        "schema_version": 2,
        "benchmark_commit": benchmark_commit,
        "controller_source_commit": source_commit,
        "image_source_commit": image_source_commit,
        "qualification_status": "unqualified",
        "vars": expected_config["env"]["staging"]["vars"],
    }
    if binding != expected_binding:
        raise QualificationError("created publication candidate binding changed")
    entry, entry_digest = selected(matrix_path, benchmark_commit)
    publication = exact(
        members["historical-image-publication.json"][0],
        {
            "schema_version", "kind", "qualification_status",
            "controller_source_commit", "image_source_commit", "benchmark_commit",
            "benchmark_tree", "registry_repository", "registry_tag",
            "registry_manifest_digest", "publication_mode", "image_size_bytes",
            "dockerfile_sha256", "layer_preparation_sha256", "layer_diff_ids",
            "matrix_sha256", "matrix_entry_sha256", "profile_lock_sha256",
            "workspace_manifest_count", "workflow_image_limit_bytes",
        },
        "created image publication evidence",
    )
    layers = publication.get("layer_diff_ids")
    image_size = publication.get("image_size_bytes")
    try:
        dockerfile_sha256 = sha256_bytes(dockerfile_path.read_bytes())
        layer_preparation_sha256 = sha256_bytes(layer_preparation_path.read_bytes())
    except OSError as error:
        raise QualificationError("created publication source file is unavailable") from error
    if (
        publication["schema_version"] != 2
        or publication["kind"]
        != "historical_public_image_publication_evidence"
        or publication["qualification_status"] != "unqualified"
        or publication["controller_source_commit"] != source_commit
        or publication["image_source_commit"] != image_source_commit
        or publication["benchmark_commit"] != benchmark_commit
        or publication["benchmark_tree"] != entry["benchmark_tree"]
        or publication["registry_repository"]
        != "lean-eval-historical-public-v1"
        or publication["registry_tag"]
        != f"{benchmark_commit}-{image_source_commit}"
        or publication["registry_manifest_digest"] != manifest_digest
        or publication["publication_mode"] != "created"
        or type(image_size) is not int
        or not 1 <= image_size <= 18_000_000_000
        or not isinstance(layers, list)
        or not 1 <= len(layers) <= 512
        or any(not isinstance(layer, str) or OCI_DIGEST.fullmatch(layer) is None for layer in layers)
        or publication["dockerfile_sha256"] != dockerfile_sha256
        or publication["layer_preparation_sha256"] != layer_preparation_sha256
        or publication["matrix_sha256"] != MATRIX_SHA256
        or publication["matrix_entry_sha256"] != entry_digest
        or publication["profile_lock_sha256"]
        != sha256_bytes(canonical(entry["profile_lock"]))
        or publication["workspace_manifest_count"] != entry["workspace_count"]
        or publication["workflow_image_limit_bytes"] != 18_000_000_000
    ):
        raise QualificationError("created image publication evidence changed")
    return publication


def validate_health(value: Any, binding: dict[str, Any]) -> dict[str, Any]:
    expected = binding["vars"]
    health = exact(value, {
        "status", "service", "environment", "deployed_commit", "replay_enabled",
        "historical_public_replay_enabled",
        "staging_acceptance_enabled", "staging_memory_limit_bytes",
        "production_memory_gate_bytes", "reviewed_execution_profile_digest",
        "reviewed_measurement_config_digest", "reviewed_vm_image_digest",
    }, "health response")
    checks = {
        "status": "ok", "service": "lean-eval-replay-executor", "environment": "staging",
        "deployed_commit": expected["DEPLOYED_COMMIT"], "replay_enabled": False,
        "historical_public_replay_enabled": False,
        "staging_acceptance_enabled": True,
        "staging_memory_limit_bytes": int(expected["STAGING_MEMORY_LIMIT_BYTES"]),
        "production_memory_gate_bytes": int(expected["PRODUCTION_MEMORY_GATE_BYTES"]),
        "reviewed_execution_profile_digest": expected["REVIEWED_EXECUTION_PROFILE_DIGEST"],
        "reviewed_measurement_config_digest": expected["REVIEWED_MEASUREMENT_CONFIG_DIGEST"],
        "reviewed_vm_image_digest": expected["REVIEWED_VM_IMAGE_DIGEST"],
    }
    if health != checks:
        raise QualificationError("health response does not bind the candidate")
    return health


def validate_probe(value: Any, request: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    probe = exact(value, {
        "schema_version", "service", "environment", "request_id", "runner_nonce",
        "archive_ciphertext_sha256", "marker_sha256", "network_policy", "network_probe",
        "destruction", "architecture", "kernel_release", "cpu_model",
        "staging_memory_limit_bytes", "production_memory_gate_bytes",
    }, "probe response")
    expected = {
        "schema_version": 1, "service": "lean-eval-replay-executor", "environment": "staging",
        "request_id": request["request_id"], "runner_nonce": request["runner_nonce"],
        "archive_ciphertext_sha256": request["archive_ciphertext_sha256"],
        "marker_sha256": request["marker_sha256"], "network_policy": "disabled",
        "network_probe": "blocked", "destruction": "confirmed", "architecture": contract["architecture"],
        "staging_memory_limit_bytes": contract["memory_limit_bytes"],
        "production_memory_gate_bytes": contract["memory_limit_bytes"],
    }
    for field, expected_value in expected.items():
        if probe.get(field) != expected_value:
            raise QualificationError(f"probe response {field} changed")
    for field in ("kernel_release", "cpu_model"):
        if not isinstance(probe[field], str) or not probe[field].strip() or len(probe[field]) > 256:
            raise QualificationError(f"probe response {field} is invalid")
    return probe


def validate_rollout(
    value: Any,
    contract: dict[str, Any],
    benchmark_commit: str,
    image_source_commit: str,
    manifest_digest: str,
) -> dict[str, Any]:
    rollout = exact(value, {
        "schema_version", "kind", "qualification_status", "name", "version",
        "max_instances", "image_repository", "image_tag", "image_manifest_digest",
        "runtime_boundary", "health",
    }, "rollout evidence")
    if (
        rollout["schema_version"] != 2
        or rollout["kind"] != "historical_public_qualification_rollout"
        or rollout["qualification_status"] != "unqualified"
        or rollout["name"] != contract["container_application"]
        or type(rollout["version"]) is not int
        or rollout["version"] < 1
        or rollout["max_instances"] != contract["max_instances"]
        or rollout["image_repository"] != contract["registry_repository"]
        or rollout["image_tag"] != f"{benchmark_commit}-{image_source_commit}"
        or rollout["image_manifest_digest"] != manifest_digest
    ):
        raise QualificationError("rollout identity changed")
    boundary = exact(rollout["runtime_boundary"], {
        "vcpu", "memory_mib", "disk_size_mb", "network", "ssh",
    }, "runtime boundary")
    if boundary != {
        "vcpu": contract["vcpu"],
        "memory_mib": contract["memory_limit_bytes"] // 1024**2,
        "disk_size_mb": contract["disk_size_mb"],
        "network": {"assign_ipv6": "none", "assign_ipv4": "none", "mode": "private"},
        "ssh": {"enabled": contract["ssh_enabled"]},
    }:
        raise QualificationError("rollout runtime boundary changed")
    health = exact(rollout["health"], {"errors", "instances"}, "rollout health")
    instances = exact(health["instances"], {
        "healthy", "failed", "starting", "scheduling",
    }, "rollout instance health")
    if health["errors"] != [] or not (
        type(instances["healthy"]) is int
        and instances["healthy"] >= 1
        and instances["failed"] == instances["starting"] == instances["scheduling"] == 0
    ):
        raise QualificationError("rollout is not healthy")
    return rollout


def validated_executor_failure(value: Any) -> dict[str, str]:
    """Accept only the source-free executor failure vocabulary."""

    if not isinstance(value, dict) or set(value) not in (
        {"error", "reason"},
        {"error", "reason", "detail"},
    ):
        raise QualificationError("executor failure fields changed")
    reason = value.get("reason")
    if (
        value.get("error") != "executor_failed"
        or not isinstance(reason, str)
        or reason not in EXECUTOR_FAILURE_REASONS
    ):
        raise QualificationError("executor failure reason changed")
    detail = value.get("detail")
    if detail is not None and (
        not isinstance(detail, str) or detail not in EXECUTOR_FAILURE_DETAILS
    ):
        raise QualificationError("executor failure detail changed")
    return {
        "error": "executor_failed",
        "reason": str(reason),
        **({} if detail is None else {"detail": str(detail)}),
    }


def staging_diagnostic(
    benchmark_commit: str,
    controller_source_commit: str,
    image_source_commit: str,
    manifest_digest: str,
    outcome: str,
    probe_number: int,
    http_status: int | None,
    failure: Any | None,
) -> dict[str, Any]:
    """Return one bounded, source-free staging probe diagnostic."""

    if (
        COMMIT.fullmatch(benchmark_commit) is None
        or COMMIT.fullmatch(controller_source_commit) is None
        or COMMIT.fullmatch(image_source_commit) is None
        or OCI_DIGEST.fullmatch(manifest_digest) is None
        or outcome not in DIAGNOSTIC_OUTCOMES
        or probe_number not in (0, 1, 2)
    ):
        raise QualificationError("diagnostic binding is invalid")
    if http_status is not None and not 100 <= http_status <= 599:
        raise QualificationError("diagnostic HTTP status is invalid")
    safe_failure = None if failure is None else validated_executor_failure(failure)
    valid_shape = (
        outcome == "initialized"
        and probe_number == 0
        and http_status is None
        and safe_failure is None
    ) or (
        outcome in {"transport_failed", "response_too_large", "invalid_http_status"}
        and probe_number in (1, 2)
        and http_status is None
        and safe_failure is None
    ) or (
        outcome == "executor_failed"
        and probe_number in (1, 2)
        and http_status == 500
        and safe_failure is not None
    ) or (
        outcome == "invalid_failure_response"
        and probe_number in (1, 2)
        and http_status is not None
        and http_status != 200
        and safe_failure is None
    ) or (
        outcome == "probe_succeeded"
        and probe_number in (1, 2)
        and http_status == 200
        and safe_failure is None
    ) or (
        outcome in {"evidence_invalid", "evidence_validated"}
        and probe_number == 2
        and http_status == 200
        and safe_failure is None
    )
    if not valid_shape:
        raise QualificationError("diagnostic outcome is inconsistent")
    return {
        "schema_version": 1,
        "kind": "historical_public_staging_qualification_diagnostic",
        "qualification_status": "unqualified",
        "benchmark_commit": benchmark_commit,
        "controller_source_commit": controller_source_commit,
        "image_source_commit": image_source_commit,
        "registry_manifest_digest": manifest_digest,
        "probe_number": probe_number,
        "outcome": outcome,
        "http_status": http_status,
        "executor_failure": safe_failure,
    }


def uuid7() -> str:
    value = bytearray((int(time.time() * 1000)).to_bytes(6, "big") + secrets.token_bytes(10))
    value[6] = 0x70 | (value[6] & 0x0F)
    value[8] = 0x80 | (value[8] & 0x3F)
    raw = value.hex()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def build_probe(runner_nonce: str) -> dict[str, Any]:
    if DIGEST.fullmatch(runner_nonce) is None:
        raise QualificationError("runner nonce is invalid")
    marker = secrets.token_bytes(64)
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo("marker.bin")
        member.size = len(marker)
        member.mode = 0o600
        member.mtime = 0
        archive.addfile(member, io.BytesIO(marker))
    plaintext = gzip.compress(payload.getvalue(), mtime=0)
    with tempfile.TemporaryDirectory(prefix="historical-qualification-") as directory:
        root = pathlib.Path(directory)
        identity = root / "identity.age"
        source = root / "marker.tar.gz"
        ciphertext = root / "marker.tar.gz.age"
        source.write_bytes(plaintext)
        subprocess.run(["age-keygen", "-o", str(identity)], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        recipient = subprocess.run(["age-keygen", "-y", str(identity)], check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout.strip()
        if re.fullmatch(r"age1[0-9a-z]{58}", recipient) is None:
            raise QualificationError("generated age recipient is invalid")
        subprocess.run(["age", "--encrypt", "--recipient", recipient, "--output", str(ciphertext), str(source)], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cipher = ciphertext.read_bytes()
        identity_bytes = identity.read_bytes()
    return {
        "schema_version": 1,
        "request_id": uuid7(),
        "runner_nonce": runner_nonce,
        "archive_ciphertext_sha256": sha256_bytes(cipher),
        "ciphertext_base64": base64.b64encode(cipher).decode("ascii"),
        "plaintext_identity_base64": base64.b64encode(identity_bytes).decode("ascii"),
        "marker_sha256": sha256_bytes(marker),
    }


def command(args: argparse.Namespace) -> None:
    matrix = pathlib.Path(args.matrix)
    contract_path = pathlib.Path(args.contract)
    if args.action == "select":
        value = candidate(matrix, contract_path, args.benchmark_commit)
    elif args.action == "build-probe":
        value = build_probe(args.runner_nonce)
    elif args.action == "render-config":
        value = render_config(
            matrix,
            contract_path,
            args.benchmark_commit,
            args.account_id,
            args.manifest_digest,
            args.source_commit,
            args.image_source_commit,
        )
    elif args.action == "render-binding":
        config = render_config(
            matrix,
            contract_path,
            args.benchmark_commit,
            "0" * 32,
            args.manifest_digest,
            args.source_commit,
            args.image_source_commit,
        )
        value = {
            "schema_version": 2,
            "benchmark_commit": args.benchmark_commit,
            "controller_source_commit": args.source_commit,
            "image_source_commit": args.image_source_commit,
            "qualification_status": "unqualified",
            "vars": config["env"]["staging"]["vars"],
        }
    elif args.action == "validate-evidence":
        binding = load(pathlib.Path(args.binding))
        contract = qualification_contract(contract_path)
        expected_config = render_config(
            matrix,
            contract_path,
            args.benchmark_commit,
            "0" * 32,
            binding["vars"]["REVIEWED_VM_IMAGE_DIGEST"],
            args.source_commit,
            args.image_source_commit,
        )
        expected_binding = {
            "schema_version": 2,
            "benchmark_commit": args.benchmark_commit,
            "controller_source_commit": args.source_commit,
            "image_source_commit": args.image_source_commit,
            "qualification_status": "unqualified",
            "vars": expected_config["env"]["staging"]["vars"],
        }
        if binding != expected_binding:
            raise QualificationError("candidate binding does not re-derive from the matrix")
        health = validate_health(load_external(pathlib.Path(args.health)), binding)
        rollout = validate_rollout(
            load(pathlib.Path(args.rollout)),
            contract,
            args.benchmark_commit,
            args.image_source_commit,
            binding["vars"]["REVIEWED_VM_IMAGE_DIGEST"],
        )
        requests = [load(pathlib.Path(path)) for path in args.requests]
        responses = [validate_probe(load_external(pathlib.Path(path)), request, contract) for path, request in zip(args.responses, requests, strict=True)]
        if len(responses) != contract["destruction_probe_count"] or len({r["request_id"] for r in responses}) != len(responses):
            raise QualificationError("two distinct destruction probes are required")
        if len({r["runner_nonce"] for r in responses}) != 1:
            raise QualificationError("destruction probes must reuse one runner nonce")
        value = {
            "schema_version": 2,
            "kind": "historical_public_staging_qualification_evidence",
            "qualification_status": "unqualified",
            "benchmark_commit": args.benchmark_commit,
            "controller_source_commit": args.source_commit,
            "image_source_commit": args.image_source_commit,
            "registry_manifest_digest": binding["vars"]["REVIEWED_VM_IMAGE_DIGEST"],
            "health": health,
            "runtime_boundary": rollout["runtime_boundary"],
            "probes": responses,
        }
    elif args.action == "render-diagnostic":
        candidate(matrix, contract_path, args.benchmark_commit)
        failure = (
            None
            if args.failure is None
            else load_external(pathlib.Path(args.failure))
        )
        value = staging_diagnostic(
            args.benchmark_commit,
            args.source_commit,
            args.image_source_commit,
            args.manifest_digest,
            args.outcome,
            args.probe_number,
            args.http_status,
            failure,
        )
    elif args.action == "validate-created-publication":
        value = validate_created_publication_origin(
            matrix,
            contract_path,
            args.benchmark_commit,
            args.source_commit,
            args.image_source_commit,
            args.manifest_digest,
            args.created_run_id,
            args.created_run_attempt,
            args.created_artifact_id,
            args.current_run_id,
            pathlib.Path(args.created_run_metadata),
            pathlib.Path(args.created_artifact_metadata),
            pathlib.Path(args.created_artifact_zip),
            pathlib.Path(args.dockerfile),
            pathlib.Path(args.layer_preparation),
        )
    else:
        raise AssertionError(args.action)
    output = canonical(value)
    if args.output == "-":
        sys.stdout.buffer.write(output)
    else:
        path = pathlib.Path(args.output)
        if path.exists():
            raise QualificationError("refusing to overwrite output")
        path.write_bytes(output)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument(
        "action",
        choices=(
            "select",
            "build-probe",
            "render-binding",
            "render-config",
            "render-diagnostic",
            "validate-created-publication",
            "validate-evidence",
        ),
    )
    result.add_argument("--matrix", required=True)
    result.add_argument("--contract", required=True)
    result.add_argument("--benchmark-commit", required=True)
    result.add_argument("--source-commit")
    result.add_argument("--image-source-commit")
    result.add_argument("--account-id")
    result.add_argument("--manifest-digest")
    result.add_argument("--binding")
    result.add_argument("--health")
    result.add_argument("--rollout")
    result.add_argument("--requests", nargs="*")
    result.add_argument("--responses", nargs="*")
    result.add_argument("--runner-nonce")
    result.add_argument("--outcome", choices=sorted(DIAGNOSTIC_OUTCOMES))
    result.add_argument("--probe-number", type=int)
    result.add_argument("--http-status", type=int)
    result.add_argument("--failure")
    result.add_argument("--created-run-id", type=int)
    result.add_argument("--created-run-attempt", type=int)
    result.add_argument("--created-artifact-id", type=int)
    result.add_argument("--current-run-id", type=int)
    result.add_argument("--created-run-metadata")
    result.add_argument("--created-artifact-metadata")
    result.add_argument("--created-artifact-zip")
    result.add_argument("--dockerfile")
    result.add_argument("--layer-preparation")
    result.add_argument("--output", default="-")
    return result


if __name__ == "__main__":
    try:
        command(parser().parse_args())
    except (QualificationError, TypeError, KeyError, ValueError) as error:
        print(f"qualification: {error}", file=sys.stderr)
        raise SystemExit(1) from None
