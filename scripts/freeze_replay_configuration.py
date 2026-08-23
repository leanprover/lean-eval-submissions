#!/usr/bin/env python3
"""Freeze reviewed authoritative replay configuration from source-free evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from typing import Any

SCRIPT_DIRECTORY = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from replay_orchestrator import (  # noqa: E402
    config_digest,
    validate_execution_profile,
    validate_measurement_config,
)


ROOT = SCRIPT_DIRECTORY.parent
PROFILE_LOCK = ROOT / "server" / "replay-image" / "replay-profile-lock-v433.json"
DOCKERFILE = ROOT / "Dockerfile.replay-authoritative"
DIGEST = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
MAX_JSON_BYTES = 128 * 1024
IMAGE_LIMIT_BYTES = 20 * 1024**3
MEMORY_LIMIT_BYTES = 12 * 1024**3
WALL_TIME_LIMIT_MS = 6 * 60 * 60 * 1000
ZERO_DIGEST = "0" * 64
PUBLICATION_FIELDS = {
    "schema_version",
    "source_commit",
    "registry_repository",
    "registry_tag",
    "registry_manifest_digest",
    "image_size_bytes",
    "dockerfile_sha256",
    "profile_lock_sha256",
    "benchmark_commit",
    "workspace_manifest_count",
    "cloudflare_image_limit_bytes",
}
RUNTIME_FIELDS = {"schema_version", "health", "probe"}
HEALTH_FIELDS = {
    "status",
    "service",
    "environment",
    "deployed_commit",
    "replay_enabled",
    "staging_acceptance_enabled",
    "staging_memory_limit_bytes",
    "production_memory_gate_bytes",
    "reviewed_execution_profile_digest",
    "reviewed_measurement_config_digest",
    "reviewed_vm_image_digest",
}
PROBE_FIELDS = {
    "schema_version",
    "service",
    "environment",
    "request_id",
    "runner_nonce",
    "submission_id",
    "archive_ciphertext_sha256",
    "plaintext_tar_sha256",
    "plaintext_tar_size",
    "network_policy",
    "network_probe",
    "destruction",
    "architecture",
    "kernel_release",
    "cpu_model",
    "staging_memory_limit_bytes",
    "production_memory_gate_bytes",
}


class FreezeError(ValueError):
    """Publication or runtime evidence cannot safely freeze a profile."""


def object_value(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise FreezeError(f"{label} must be an object with string keys")
    return value


def exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise FreezeError(f"{label} fields are not canonical")


def load(path: pathlib.Path, label: str) -> Any:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_JSON_BYTES:
            raise FreezeError(f"{label} exceeds its size limit")
        return json.loads(raw.decode("utf-8"))
    except FreezeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FreezeError(f"{label} is not one UTF-8 JSON object") from error


def file_sha256(path: pathlib.Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise FreezeError(f"cannot read reviewed input {path}") from error


def canonical_sha256(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise FreezeError(f"{label} is invalid")
    return value


def positive_integer(value: Any, label: str, maximum: int) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise FreezeError(f"{label} is invalid")
    return value


def validate_publication(value: Any, lock: dict[str, Any]) -> dict[str, Any]:
    publication = object_value(value, "publication evidence")
    exact_fields(publication, PUBLICATION_FIELDS, "publication evidence")
    source_commit = canonical_sha256(
        publication["source_commit"], COMMIT, "publication source commit"
    )
    manifest = canonical_sha256(
        publication["registry_manifest_digest"],
        SHA256,
        "registry manifest digest",
    )
    image_size = positive_integer(
        publication["image_size_bytes"], "published image size", IMAGE_LIMIT_BYTES
    )
    if (
        publication["schema_version"] != 1
        or publication["registry_repository"] != "lean-eval-authoritative"
        or publication["registry_tag"] != source_commit
        or publication["dockerfile_sha256"] != file_sha256(DOCKERFILE)
        or publication["profile_lock_sha256"] != file_sha256(PROFILE_LOCK)
        or publication["benchmark_commit"] != lock["benchmark_commit"]
        or publication["workspace_manifest_count"] != 309
        or publication["cloudflare_image_limit_bytes"] != IMAGE_LIMIT_BYTES
    ):
        raise FreezeError("publication evidence does not match the reviewed image inputs")
    return {**publication, "registry_manifest_digest": manifest, "image_size_bytes": image_size}


def bounded_text(value: Any, label: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise FreezeError(f"{label} is invalid")
    return value


def validate_runtime(value: Any, manifest: str) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = object_value(value, "runtime evidence")
    exact_fields(runtime, RUNTIME_FIELDS, "runtime evidence")
    health = object_value(runtime["health"], "runtime health")
    probe = object_value(runtime["probe"], "runtime probe")
    exact_fields(health, HEALTH_FIELDS, "runtime health")
    exact_fields(probe, PROBE_FIELDS, "runtime probe")
    canonical_sha256(health["deployed_commit"], COMMIT, "deployed commit")
    canonical_sha256(probe["request_id"], UUID7, "probe request_id")
    canonical_sha256(probe["submission_id"], UUID7, "probe submission_id")
    canonical_sha256(probe["runner_nonce"], DIGEST, "probe runner_nonce")
    canonical_sha256(
        probe["archive_ciphertext_sha256"], DIGEST, "probe archive digest"
    )
    canonical_sha256(probe["plaintext_tar_sha256"], DIGEST, "probe plaintext digest")
    positive_integer(probe["plaintext_tar_size"], "probe plaintext size", 10 * 1024**2)
    if (
        runtime["schema_version"] != 1
        or health["status"] != "ok"
        or probe["schema_version"] != 1
        or health["service"] != "lean-eval-replay-executor"
        or probe["service"] != "lean-eval-replay-executor"
        or health["environment"] != "staging"
        or probe["environment"] != "staging"
        or health["replay_enabled"] is not False
        or health["staging_acceptance_enabled"] is not True
        or health["reviewed_execution_profile_digest"] != ZERO_DIGEST
        or health["reviewed_measurement_config_digest"] != ZERO_DIGEST
        or health["reviewed_vm_image_digest"] != manifest
        or probe["network_policy"] != "disabled"
        or probe["network_probe"] != "blocked"
        or probe["destruction"] != "confirmed"
        or health["staging_memory_limit_bytes"] != MEMORY_LIMIT_BYTES
        or probe["staging_memory_limit_bytes"] != MEMORY_LIMIT_BYTES
        or health["production_memory_gate_bytes"] != MEMORY_LIMIT_BYTES
        or probe["production_memory_gate_bytes"] != MEMORY_LIMIT_BYTES
    ):
        raise FreezeError("runtime evidence does not prove the disabled reviewed staging image")
    for field in ("architecture", "kernel_release", "cpu_model"):
        bounded_text(probe[field], f"runtime probe {field}")
    if probe["architecture"] not in {"x86_64", "aarch64"}:
        raise FreezeError("runtime probe architecture is not registered")
    return health, probe


def freeze(publication_value: Any, runtime_value: Any, lock_value: Any) -> dict[str, Any]:
    lock = object_value(lock_value, "profile lock")
    publication = validate_publication(publication_value, lock)
    health, probe = validate_runtime(runtime_value, publication["registry_manifest_digest"])
    profile = {
        "schema_version": 1,
        "runner_profile": lock["runner_profile"],
        "vm_image_digest": publication["registry_manifest_digest"],
        "toolchain": lock["toolchain"],
        "go_toolchain": lock["go_toolchain"],
        "rust_toolchain": lock["rust_toolchain"],
        "cpu_model": probe["cpu_model"],
        "architecture": probe["architecture"],
        "kernel_release": probe["kernel_release"],
        "cache_state": lock["cache_state"],
        "measurement_command": lock["measurement_command"],
        "components": lock["components"],
    }
    measurement = {
        "schema_version": 1,
        "wall_time_limit_ms": WALL_TIME_LIMIT_MS,
        "memory_limit_bytes": MEMORY_LIMIT_BYTES,
        "retired_instructions": {
            "required": False,
            "perf_event": "instructions:u",
        },
    }
    validate_execution_profile(profile)
    validate_measurement_config(measurement)
    return {
        "schema_version": 1,
        "image_source_commit": publication["source_commit"],
        "worker_deployed_commit": health["deployed_commit"],
        "registry_repository": publication["registry_repository"],
        "registry_tag": publication["registry_tag"],
        "registry_manifest_digest": publication["registry_manifest_digest"],
        "runtime_evidence_sha256": hashlib.sha256(
            json.dumps(runtime_value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        "execution_profile_digest": config_digest(
            "lean-eval-replay-execution-profile-v1", profile
        ),
        "measurement_config_digest": config_digest(
            "lean-eval-replay-measurement-config-v1", measurement
        ),
        "execution_profile": profile,
        "measurement_config": measurement,
    }


def write_exclusive(path: pathlib.Path, value: Any) -> None:
    encoded = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as output:
        output.write(encoded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication", required=True, type=pathlib.Path)
    parser.add_argument("--runtime", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        result = freeze(
            load(args.publication, "publication evidence"),
            load(args.runtime, "runtime evidence"),
            load(PROFILE_LOCK, "profile lock"),
        )
        write_exclusive(args.output, result)
    except (FreezeError, OSError, ValueError) as error:
        print(f"freeze-replay-configuration: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
