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
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any

COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
OCI_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAX_JSON_BYTES = 1024 * 1024
MATRIX_SHA256 = "aad9132f729ef9f429532900d1e50b665330721fa9360699328c47bdfb2aedfc"
CONTRACT_SHA256 = "afac0306192c63c7a6d1e2fc83f179180b695e009f869d14cc6a1eb5028afb85"
UNREVIEWED_DIGEST = "0" * 64


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
        or matrix.get("image_count") != 25
        or matrix.get("qualification_status") != "unqualified"
        or not isinstance(matrix.get("images"), list)
        or len(matrix["images"]) != 25
    ):
        raise QualificationError("profile matrix is not the unqualified 25-image matrix")
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
        "image_count": 25,
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
) -> dict[str, Any]:
    item = candidate(matrix_path, contract_path, benchmark_commit)
    contract = qualification_contract(contract_path)
    if re.fullmatch(r"[0-9a-f]{32}", account_id) is None:
        raise QualificationError("Cloudflare account id is invalid")
    if OCI_DIGEST.fullmatch(manifest_digest) is None or COMMIT.fullmatch(source_commit) is None:
        raise QualificationError("deployment binding is invalid")
    image = f"registry.cloudflare.com/{account_id}/{item['registry_repository']}:{benchmark_commit}-{source_commit}"
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


def validate_health(value: Any, binding: dict[str, Any]) -> dict[str, Any]:
    expected = binding["vars"]
    health = exact(value, {
        "status", "service", "environment", "deployed_commit", "replay_enabled",
        "staging_acceptance_enabled", "staging_memory_limit_bytes",
        "production_memory_gate_bytes", "reviewed_execution_profile_digest",
        "reviewed_measurement_config_digest", "reviewed_vm_image_digest",
    }, "health response")
    checks = {
        "status": "ok", "service": "lean-eval-replay-executor", "environment": "staging",
        "deployed_commit": expected["DEPLOYED_COMMIT"], "replay_enabled": False,
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
    value: Any, contract: dict[str, Any], benchmark_commit: str, source_commit: str
) -> dict[str, Any]:
    rollout = exact(value, {
        "schema_version", "kind", "qualification_status", "name", "version",
        "max_instances", "image_repository", "image_tag", "runtime_boundary", "health",
    }, "rollout evidence")
    if (
        rollout["schema_version"] != 1
        or rollout["kind"] != "historical_public_qualification_rollout"
        or rollout["qualification_status"] != "unqualified"
        or rollout["name"] != contract["container_application"]
        or type(rollout["version"]) is not int
        or rollout["version"] < 1
        or rollout["max_instances"] != contract["max_instances"]
        or rollout["image_repository"] != contract["registry_repository"]
        or rollout["image_tag"] != f"{benchmark_commit}-{source_commit}"
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
        value = render_config(matrix, contract_path, args.benchmark_commit, args.account_id, args.manifest_digest, args.source_commit)
    elif args.action == "render-binding":
        config = render_config(matrix, contract_path, args.benchmark_commit, "0" * 32, args.manifest_digest, args.source_commit)
        value = {
            "schema_version": 1,
            "benchmark_commit": args.benchmark_commit,
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
        )
        expected_binding = {
            "schema_version": 1,
            "benchmark_commit": args.benchmark_commit,
            "qualification_status": "unqualified",
            "vars": expected_config["env"]["staging"]["vars"],
        }
        if binding != expected_binding:
            raise QualificationError("candidate binding does not re-derive from the matrix")
        health = validate_health(load_external(pathlib.Path(args.health)), binding)
        rollout = validate_rollout(
            load(pathlib.Path(args.rollout)), contract, args.benchmark_commit, args.source_commit
        )
        requests = [load(pathlib.Path(path)) for path in args.requests]
        responses = [validate_probe(load_external(pathlib.Path(path)), request, contract) for path, request in zip(args.responses, requests, strict=True)]
        if len(responses) != contract["destruction_probe_count"] or len({r["request_id"] for r in responses}) != len(responses):
            raise QualificationError("two distinct destruction probes are required")
        if len({r["runner_nonce"] for r in responses}) != 1:
            raise QualificationError("destruction probes must reuse one runner nonce")
        value = {
            "schema_version": 1,
            "kind": "historical_public_staging_qualification_evidence",
            "qualification_status": "unqualified",
            "benchmark_commit": args.benchmark_commit,
            "source_commit": args.source_commit,
            "registry_manifest_digest": binding["vars"]["REVIEWED_VM_IMAGE_DIGEST"],
            "health": health,
            "runtime_boundary": rollout["runtime_boundary"],
            "probes": responses,
        }
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
    result.add_argument("action", choices=("select", "build-probe", "render-binding", "render-config", "validate-evidence"))
    result.add_argument("--matrix", required=True)
    result.add_argument("--contract", required=True)
    result.add_argument("--benchmark-commit", required=True)
    result.add_argument("--source-commit")
    result.add_argument("--account-id")
    result.add_argument("--manifest-digest")
    result.add_argument("--binding")
    result.add_argument("--health")
    result.add_argument("--rollout")
    result.add_argument("--requests", nargs="*")
    result.add_argument("--responses", nargs="*")
    result.add_argument("--runner-nonce")
    result.add_argument("--output", default="-")
    return result


if __name__ == "__main__":
    try:
        command(parser().parse_args())
    except (QualificationError, TypeError, KeyError, ValueError) as error:
        print(f"qualification: {error}", file=sys.stderr)
        raise SystemExit(1) from None
