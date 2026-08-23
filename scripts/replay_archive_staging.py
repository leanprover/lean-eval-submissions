#!/usr/bin/env python3
"""Prepare and validate one accepted staging archive boundary acceptance.

This is a pre-enable proof for the credentialed replay controller. It consumes
one accepted staging archive and returns only source-free decryption, isolation,
and destruction evidence. It does not append replay State or claim a checker
verdict.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import secrets
import sys
from typing import Any

SCRIPT_DIRECTORY = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from archive_submission import _validate_sidecar  # noqa: E402
from key_capability_contract import (  # noqa: E402
    ContractError,
    canonical_archive_path,
    validate_age_identity_bytes,
    validate_binding,
    validate_envelope,
)


UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_JSON_BYTES = 64 * 1024
MAX_CIPHERTEXT_BYTES = 11 * 1024 * 1024
MAX_IDENTITY_BYTES = 4096


class StagingReplayError(ValueError):
    """The accepted-archive staging boundary input or evidence is invalid."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise StagingReplayError(f"{label} must be an object with string keys")
    return value


def _load(path: pathlib.Path, label: str) -> Any:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_JSON_BYTES:
            raise StagingReplayError(f"{label} exceeds its size limit")
        return json.loads(raw.decode("utf-8"))
    except StagingReplayError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StagingReplayError(f"cannot read {label}") from error


def _write(path: pathlib.Path, value: Any) -> None:
    encoded = (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()
    if path.exists() or path.is_symlink():
        raise StagingReplayError("output already exists")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(encoded)


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise StagingReplayError(f"{label} is invalid")
    return value


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise StagingReplayError("cannot read ciphertext") from error
    return digest.hexdigest()


def _uuid7(now: dt.datetime) -> str:
    milliseconds = int(now.timestamp() * 1000)
    random_bytes = secrets.token_bytes(10)
    raw = bytearray(16)
    raw[:6] = milliseconds.to_bytes(6, "big")
    raw[6] = 0x70 | (random_bytes[0] & 0x0F)
    raw[7] = random_bytes[1]
    raw[8] = 0x80 | (random_bytes[2] & 0x3F)
    raw[9:] = random_bytes[3:]
    encoded = raw.hex()
    return f"{encoded[:8]}-{encoded[8:12]}-{encoded[12:16]}-{encoded[16:20]}-{encoded[20:]}"


def _timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _trusted_now(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise StagingReplayError("trusted_now is invalid") from error
    if _timestamp(parsed) != value:
        raise StagingReplayError("trusted_now is not canonical UTC milliseconds")
    return parsed


def build_plan(domain_path: pathlib.Path, submission_id: str, output: pathlib.Path) -> None:
    _match(UUID7, submission_id, "submission_id")
    domain = _object(_load(domain_path, "State domain view"), "State domain view")
    if domain.get("schema_version") != 1 or domain.get("environment") != "staging":
        raise StagingReplayError("State domain view is not staging schema version 1")
    submissions = domain.get("submissions")
    results = domain.get("results")
    if not isinstance(submissions, list) or not isinstance(results, list):
        raise StagingReplayError("State domain collections are invalid")
    matching_submissions = [
        _object(item, "submission")
        for item in submissions
        if isinstance(item, dict) and item.get("submission_id") == submission_id
    ]
    matching_results = [
        _object(item, "result")
        for item in results
        if isinstance(item, dict) and item.get("submission_id") == submission_id
    ]
    if len(matching_submissions) != 1 or len(matching_results) != 1:
        raise StagingReplayError("submission does not have exactly one accepted State result")
    submission = matching_submissions[0]
    result = matching_results[0]
    archive = _object(submission.get("archive"), "submission archive")
    evaluation = _object(submission.get("evaluation"), "submission evaluation")
    if (
        submission.get("source_visibility") != "private"
        or evaluation.get("status") != "accepted"
        or archive.get("status") != "completed"
        or submission.get("result_id") != result.get("result_id")
    ):
        raise StagingReplayError("submission is not one accepted private archived result")
    result_id = _match(RESULT_ID, result.get("result_id"), "result_id")
    archive_commit = _match(COMMIT, archive.get("archive_commit"), "archive_commit")
    archive_digest = _match(
        DIGEST, archive.get("archive_ciphertext_sha256"), "archive digest"
    )
    archive_path = archive.get("archive_path")
    if archive_path != canonical_archive_path(submission_id):
        raise StagingReplayError("archive path is not canonical")
    if archive.get("archive_repository") != "leanprover/lean-eval-audit" or archive.get("encrypted") is not True:
        raise StagingReplayError("archive locator is not the encrypted audit repository")
    _write(output, {
        "schema_version": 1,
        "kind": "accepted_archive_staging_acceptance",
        "submission_id": submission_id,
        "result_id": result_id,
        "archive_repository": "leanprover/lean-eval-audit",
        "archive_commit": archive_commit,
        "archive_path": archive_path,
        "archive_ciphertext_sha256": archive_digest,
    })


def _validate_plan(path: pathlib.Path) -> dict[str, Any]:
    plan = _object(_load(path, "staging replay plan"), "staging replay plan")
    fields = {
        "schema_version", "kind", "submission_id", "result_id", "archive_repository",
        "archive_commit", "archive_path", "archive_ciphertext_sha256",
    }
    if set(plan) != fields or plan.get("schema_version") != 1 or plan.get("kind") != "accepted_archive_staging_acceptance":
        raise StagingReplayError("staging replay plan is not canonical")
    submission_id = _match(UUID7, plan.get("submission_id"), "plan submission_id")
    _match(RESULT_ID, plan.get("result_id"), "plan result_id")
    _match(COMMIT, plan.get("archive_commit"), "plan archive_commit")
    _match(DIGEST, plan.get("archive_ciphertext_sha256"), "plan archive digest")
    if plan.get("archive_repository") != "leanprover/lean-eval-audit" or plan.get("archive_path") != canonical_archive_path(submission_id):
        raise StagingReplayError("staging replay plan archive locator is invalid")
    return plan


def _validate_archive(
    plan: dict[str, Any], sidecar_path: pathlib.Path, ciphertext_path: pathlib.Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    sidecar = _object(_load(sidecar_path, "archive sidecar"), "archive sidecar")
    try:
        _validate_sidecar(sidecar, finalized=True)
    except SystemExit as error:
        raise StagingReplayError(f"archive sidecar is invalid: {error}") from error
    if sidecar.get("schema_version") != 3 or sidecar.get("submission_id") != plan["submission_id"]:
        raise StagingReplayError("archive sidecar is not the planned schema-version-3 submission")
    if not ciphertext_path.is_file() or ciphertext_path.is_symlink():
        raise StagingReplayError("ciphertext is not one regular file")
    if ciphertext_path.stat().st_size > MAX_CIPHERTEXT_BYTES:
        raise StagingReplayError("ciphertext exceeds the replay boundary size limit")
    digest = _sha256(ciphertext_path)
    if digest != plan["archive_ciphertext_sha256"] or digest != sidecar.get("sha256_ciphertext"):
        raise StagingReplayError("ciphertext digest differs from State or sidecar")
    envelope = validate_envelope(sidecar.get("key_envelope"))
    if envelope["submission_id"] != plan["submission_id"] or envelope["archive_ciphertext_sha256"] != digest:
        raise StagingReplayError("archive envelope differs from the planned archive")
    return sidecar, envelope


def prepare_unwrap(
    plan_path: pathlib.Path,
    sidecar_path: pathlib.Path,
    ciphertext_path: pathlib.Path,
    trusted_now: str,
    output: pathlib.Path,
) -> None:
    plan = _validate_plan(plan_path)
    _, envelope = _validate_archive(plan, sidecar_path, ciphertext_path)
    current = _trusted_now(trusted_now)
    runner_nonce = secrets.token_hex(32)
    capability = {
        "schema_version": 1,
        "purpose": "lean-eval-replay",
        "request_id": _uuid7(current),
        "submission_id": plan["submission_id"],
        "archive_repository": plan["archive_repository"],
        "archive_commit": plan["archive_commit"],
        "archive_path": plan["archive_path"],
        "archive_ciphertext_sha256": plan["archive_ciphertext_sha256"],
        "data_key_id": envelope["data_key_id"],
        "runner_nonce": runner_nonce,
        "issued_at": _timestamp(current),
        "expires_at": _timestamp(current + dt.timedelta(minutes=5)),
        "max_uses": 1,
    }
    validate_binding(
        envelope,
        capability,
        expected_purpose="lean-eval-replay",
        expected_runner_nonce=runner_nonce,
        now=current,
    )
    _write(output, {
        "schema_version": 1,
        "operation": "unwrap",
        "adapter": envelope["adapter"],
        "envelope": envelope,
        "capability": capability,
        "expected_purpose": "lean-eval-replay",
        "expected_runner_nonce": runner_nonce,
    })


def build_executor_request(
    plan_path: pathlib.Path,
    sidecar_path: pathlib.Path,
    ciphertext_path: pathlib.Path,
    unwrap_path: pathlib.Path,
    identity_path: pathlib.Path,
    output: pathlib.Path,
) -> None:
    plan = _validate_plan(plan_path)
    sidecar, envelope = _validate_archive(plan, sidecar_path, ciphertext_path)
    unwrap = _object(_load(unwrap_path, "unwrap request"), "unwrap request")
    capability = _object(unwrap.get("capability"), "unwrap capability")
    validate_binding(
        envelope,
        capability,
        expected_purpose="lean-eval-replay",
        expected_runner_nonce=capability.get("runner_nonce"),
        now=dt.datetime.now(dt.timezone.utc),
    )
    try:
        identity = identity_path.read_bytes()
        ciphertext = ciphertext_path.read_bytes()
    except OSError as error:
        raise StagingReplayError("cannot read private executor input") from error
    if len(identity) > MAX_IDENTITY_BYTES:
        raise StagingReplayError("plaintext identity exceeds its size limit")
    validate_age_identity_bytes(identity)
    _write(output, {
        "schema_version": 1,
        "request_id": capability["request_id"],
        "runner_nonce": capability["runner_nonce"],
        "submission_id": plan["submission_id"],
        "archive_ciphertext_sha256": plan["archive_ciphertext_sha256"],
        "plaintext_tar_sha256": sidecar["sha256_plaintext_tar"],
        "plaintext_tar_size": sidecar["size_bytes_plaintext_tar"],
        "ciphertext_base64": base64.b64encode(ciphertext).decode("ascii"),
        "plaintext_identity_base64": base64.b64encode(identity).decode("ascii"),
    })


def validate_response(request_path: pathlib.Path, response_path: pathlib.Path) -> None:
    request = _object(_load(request_path, "executor request"), "executor request")
    response = _object(_load(response_path, "executor response"), "executor response")
    expected = {
        "schema_version": 1,
        "service": "lean-eval-replay-executor",
        "environment": "staging",
        "request_id": request.get("request_id"),
        "runner_nonce": request.get("runner_nonce"),
        "submission_id": request.get("submission_id"),
        "archive_ciphertext_sha256": request.get("archive_ciphertext_sha256"),
        "plaintext_tar_sha256": request.get("plaintext_tar_sha256"),
        "plaintext_tar_size": request.get("plaintext_tar_size"),
        "network_policy": "disabled",
        "network_probe": "blocked",
        "destruction": "confirmed",
        "staging_memory_limit_bytes": 12_884_901_888,
        "production_memory_gate_bytes": 12_884_901_888,
    }
    allowed = {*expected, "architecture", "kernel_release", "cpu_model"}
    if set(response) != allowed or any(response.get(field) != value for field, value in expected.items()):
        raise StagingReplayError("executor response is not bound to the accepted archive request")
    for field in ("architecture", "kernel_release", "cpu_model"):
        if not isinstance(response.get(field), str) or not response[field] or len(response[field]) > 256:
            raise StagingReplayError(f"executor response {field} is invalid")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--domain", required=True, type=pathlib.Path)
    plan.add_argument("--submission-id", required=True)
    plan.add_argument("--output", required=True, type=pathlib.Path)
    unwrap = commands.add_parser("prepare-unwrap")
    for name in ("plan", "sidecar", "ciphertext", "output"):
        unwrap.add_argument(f"--{name}", required=True, type=pathlib.Path)
    unwrap.add_argument("--trusted-now", required=True)
    executor = commands.add_parser("build-executor-request")
    for name in ("plan", "sidecar", "ciphertext", "unwrap", "identity", "output"):
        executor.add_argument(f"--{name}", required=True, type=pathlib.Path)
    response = commands.add_parser("validate-response")
    response.add_argument("--request", required=True, type=pathlib.Path)
    response.add_argument("--response", required=True, type=pathlib.Path)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "plan":
            build_plan(args.domain, args.submission_id, args.output)
        elif args.command == "prepare-unwrap":
            prepare_unwrap(args.plan, args.sidecar, args.ciphertext, args.trusted_now, args.output)
        elif args.command == "build-executor-request":
            build_executor_request(
                args.plan, args.sidecar, args.ciphertext, args.unwrap, args.identity, args.output
            )
        elif args.command == "validate-response":
            validate_response(args.request, args.response)
    except (StagingReplayError, ContractError) as error:
        print(f"staging-replay: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
