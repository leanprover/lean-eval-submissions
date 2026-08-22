#!/usr/bin/env python3
"""Prepare and verify the source-free staging AWS key-adapter smoke.

This helper never calls AWS.  The protected workflow owns OIDC role assumption
and direct Lambda invocation; this module constructs strict synthetic inputs,
validates provider responses, and proves the one-use result decrypts exactly
one synthetic archive without placing plaintext in an artifact.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import os
import pathlib
import re
import secrets
import stat
import sys
import tarfile
import uuid
from typing import Any

try:
    from .key_capability_contract import (
        COMMIT,
        ContractError,
        canonical_archive_path,
        capability_digest,
        validate_age_identity_bytes,
        validate_binding,
        validate_envelope,
    )
except ImportError:
    from key_capability_contract import (  # type: ignore[no-redef]
        COMMIT,
        ContractError,
        canonical_archive_path,
        capability_digest,
        validate_age_identity_bytes,
        validate_binding,
        validate_envelope,
    )


DIGEST = re.compile(r"[0-9a-f]{64}")
EXPECTATION_FIELDS = {"schema_version", "kind", "marker_sha256"}
RESPONSE_FIELDS = {
    "schema_version",
    "adapter",
    "request_id",
    "data_key_id",
    "capability_digest",
    "plaintext_identity_base64",
}
REQUEST_FIELDS = {
    "schema_version",
    "operation",
    "adapter",
    "envelope",
    "capability",
    "expected_purpose",
    "expected_runner_nonce",
}
MAX_JSON_BYTES = 32_768
MAX_CIPHERTEXT_BYTES = 131_072
SUBMISSION_ID = "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f"


class LiveSmokeError(ValueError):
    """The staging smoke input or provider result is unsafe or malformed."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise LiveSmokeError(f"{label} must be an object with string keys")
    return value


def _fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise LiveSmokeError(
            f"{label} fields are not canonical; "
            f"missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _load(path: pathlib.Path, label: str) -> Any:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise LiveSmokeError(f"cannot read {label}") from error
    if len(payload) > MAX_JSON_BYTES:
        raise LiveSmokeError(f"{label} exceeds the size limit")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise LiveSmokeError(f"{label} is not one UTF-8 JSON object") from error


def _write_json(path: pathlib.Path, value: Any, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise LiveSmokeError(f"refusing existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, separators=(",", ":"), sort_keys=True)
            output.write("\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise LiveSmokeError(f"cannot hash {path}") from error
    return digest.hexdigest()


def _uuid7() -> str:
    milliseconds = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    if not 0 <= milliseconds < 2**48:
        raise LiveSmokeError("current timestamp cannot be encoded as UUIDv7")
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (
        (milliseconds << 80)
        | (0x7 << 76)
        | (random_a << 64)
        | (0b10 << 62)
        | random_b
    )
    return str(uuid.UUID(int=value))


def _timestamp(value: dt.datetime) -> str:
    canonical = value.astimezone(dt.timezone.utc).replace(microsecond=(value.microsecond // 1000) * 1000)
    return canonical.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def validate_expectation(value: Any) -> dict[str, Any]:
    expectation = _object(value, "expectation")
    _fields(expectation, EXPECTATION_FIELDS, "expectation")
    if type(expectation["schema_version"]) is not int or expectation["schema_version"] != 1:
        raise LiveSmokeError("expectation schema_version must be integer 1")
    if expectation["kind"] != "aws_key_adapter_staging_smoke":
        raise LiveSmokeError("expectation kind is invalid")
    if not isinstance(expectation["marker_sha256"], str) or DIGEST.fullmatch(expectation["marker_sha256"]) is None:
        raise LiveSmokeError("expectation marker_sha256 is invalid")
    return expectation


def prepare_source(output: pathlib.Path, expectation_output: pathlib.Path) -> None:
    if output.exists() or output.is_symlink():
        raise LiveSmokeError("source-tar output already exists")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = secrets.token_bytes(64)
    member = tarfile.TarInfo("marker.bin")
    member.size = len(marker)
    member.mode = 0o600
    member.mtime = 0
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    try:
        with tarfile.open(output, "x:gz") as archive:
            archive.addfile(member, io.BytesIO(marker))
        output.chmod(0o600)
    except (OSError, tarfile.TarError) as error:
        output.unlink(missing_ok=True)
        raise LiveSmokeError("cannot create the synthetic source tar") from error
    _write_json(
        expectation_output,
        {
            "schema_version": 1,
            "kind": "aws_key_adapter_staging_smoke",
            "marker_sha256": hashlib.sha256(marker).hexdigest(),
        },
        mode=0o644,
    )


def validate_artifact(root: pathlib.Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise LiveSmokeError("artifact root must be one regular directory")
    if {entry.name for entry in root.iterdir()} != {"archive", "expectation.json"}:
        raise LiveSmokeError("artifact root has unexpected entries")
    archive = root / "archive"
    if archive.is_symlink() or not archive.is_dir():
        raise LiveSmokeError("artifact archive must be one regular directory")
    if {entry.name for entry in archive.iterdir()} != {
        "source.tar.gz.age",
        "archive-key-envelope.json",
    }:
        raise LiveSmokeError("artifact archive has unexpected entries")
    for path in (*root.iterdir(), *archive.iterdir()):
        if path.is_symlink():
            raise LiveSmokeError("artifact contains a symlink")
    expectation = validate_expectation(_load(root / "expectation.json", "expectation"))
    envelope = validate_envelope(
        _load(archive / "archive-key-envelope.json", "archive envelope")
    )
    ciphertext = archive / "source.tar.gz.age"
    if not ciphertext.is_file() or ciphertext.is_symlink():
        raise LiveSmokeError("ciphertext must be one regular file")
    try:
        if ciphertext.stat().st_size > MAX_CIPHERTEXT_BYTES:
            raise LiveSmokeError("synthetic ciphertext exceeds the size limit")
        with ciphertext.open("rb") as source:
            header = source.read(32)
    except OSError as error:
        raise LiveSmokeError("cannot read ciphertext") from error
    if not header.startswith(b"age-encryption.org/v1\n"):
        raise LiveSmokeError("ciphertext does not have an age format-version-1 header")
    if _sha256(ciphertext) != envelope["archive_ciphertext_sha256"]:
        raise LiveSmokeError("ciphertext digest does not match envelope")
    if envelope["submission_id"] != SUBMISSION_ID or envelope["adapter"] != "aws-kms-v1":
        raise LiveSmokeError("artifact uses the wrong staging identity or adapter")
    return expectation, envelope


def build_unwrap_request(
    artifact_root: pathlib.Path,
    workflow_commit: str,
    output: pathlib.Path,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    _, envelope = validate_artifact(artifact_root)
    if not isinstance(workflow_commit, str) or COMMIT.fullmatch(workflow_commit) is None:
        raise LiveSmokeError("workflow_commit must be a full lowercase commit")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None or current.utcoffset() != dt.timedelta(0):
        raise LiveSmokeError("current time must be timezone-aware UTC")
    issued = current.astimezone(dt.timezone.utc)
    runner_nonce = secrets.token_hex(32)
    capability = {
        "schema_version": 1,
        "purpose": "lean-eval-replay",
        "request_id": _uuid7(),
        "submission_id": envelope["submission_id"],
        # This is an explicitly synthetic smoke locator. The workflow does not
        # append it to State or claim that the artifact exists in audit Git.
        "archive_repository": "leanprover/lean-eval-audit",
        "archive_commit": workflow_commit,
        "archive_path": canonical_archive_path(envelope["submission_id"]),
        "archive_ciphertext_sha256": envelope["archive_ciphertext_sha256"],
        "data_key_id": envelope["data_key_id"],
        "runner_nonce": runner_nonce,
        "issued_at": _timestamp(issued),
        "expires_at": _timestamp(issued + dt.timedelta(minutes=5)),
        "max_uses": 1,
    }
    validate_binding(
        envelope,
        capability,
        expected_purpose="lean-eval-replay",
        expected_runner_nonce=runner_nonce,
        now=issued,
    )
    request = {
        "schema_version": 1,
        "operation": "unwrap",
        "adapter": "aws-kms-v1",
        "envelope": envelope,
        "capability": capability,
        "expected_purpose": "lean-eval-replay",
        "expected_runner_nonce": runner_nonce,
    }
    _write_json(output, request)
    return request


def validate_unwrap_response(
    request_path: pathlib.Path,
    metadata_path: pathlib.Path,
    response_path: pathlib.Path,
    identity_output: pathlib.Path,
) -> dict[str, Any]:
    request = _object(_load(request_path, "unwrap request"), "unwrap request")
    _fields(request, REQUEST_FIELDS, "unwrap request")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise LiveSmokeError("unwrap request schema_version must be integer 1")
    if request["operation"] != "unwrap" or request["adapter"] != "aws-kms-v1":
        raise LiveSmokeError("unwrap request operation or adapter is invalid")
    if request["expected_purpose"] != "lean-eval-replay":
        raise LiveSmokeError("unwrap request purpose is invalid")
    metadata = _object(_load(metadata_path, "Lambda invocation metadata"), "Lambda invocation metadata")
    if metadata.get("StatusCode") != 200 or "FunctionError" in metadata:
        raise LiveSmokeError("Lambda unwrap did not complete successfully")
    response = _object(_load(response_path, "Lambda unwrap response"), "Lambda unwrap response")
    _fields(response, RESPONSE_FIELDS, "Lambda unwrap response")
    if type(response["schema_version"]) is not int or response["schema_version"] != 1:
        raise LiveSmokeError("Lambda response schema_version must be integer 1")
    capability = _object(request.get("capability"), "request capability")
    envelope = _object(request.get("envelope"), "request envelope")
    validate_binding(
        envelope,
        capability,
        expected_purpose=request["expected_purpose"],
        expected_runner_nonce=request["expected_runner_nonce"],
        now=dt.datetime.now(dt.timezone.utc),
    )
    expected = {
        "adapter": request.get("adapter"),
        "request_id": capability.get("request_id"),
        "data_key_id": envelope.get("data_key_id"),
        "capability_digest": capability_digest(capability),
    }
    for field, value in expected.items():
        if response[field] != value:
            raise LiveSmokeError(f"Lambda response {field} does not match the request")
    encoded = response["plaintext_identity_base64"]
    if not isinstance(encoded, str):
        raise LiveSmokeError("Lambda response identity is not base64 text")
    try:
        identity = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise LiveSmokeError("Lambda response identity is not canonical base64") from error
    if base64.b64encode(identity).decode("ascii") != encoded:
        raise LiveSmokeError("Lambda response identity is not canonical base64")
    validate_age_identity_bytes(identity)
    if identity_output.exists() or identity_output.is_symlink():
        raise LiveSmokeError("identity output already exists")
    descriptor = os.open(identity_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(identity)
    if stat.S_IMODE(identity_output.stat().st_mode) != 0o600:
        raise LiveSmokeError("identity output permissions are not private")
    return {"request_id": expected["request_id"], "capability_digest": expected["capability_digest"]}


def validate_reuse_failure(metadata_path: pathlib.Path, response_path: pathlib.Path) -> None:
    metadata = _object(_load(metadata_path, "repeat invocation metadata"), "repeat invocation metadata")
    if metadata.get("StatusCode") != 200 or not isinstance(metadata.get("FunctionError"), str):
        raise LiveSmokeError("repeat invocation did not report a Lambda function error")
    try:
        raw = response_path.read_bytes()
    except OSError as error:
        raise LiveSmokeError("cannot read repeat invocation response") from error
    if len(raw) > MAX_JSON_BYTES:
        raise LiveSmokeError("repeat invocation response exceeds the size limit")
    if b"plaintext_identity_base64" in raw or b"AGE-SECRET-KEY-" in raw:
        raise LiveSmokeError("repeat invocation exposed private identity material")
    response = _object(_load(response_path, "repeat invocation response"), "repeat invocation response")
    message = response.get("errorMessage")
    if not isinstance(message, str) or "capability has already been consumed" not in message:
        raise LiveSmokeError("repeat invocation failed for an unexpected reason")


def verify_decrypted(decrypted_tar: pathlib.Path, expectation_path: pathlib.Path) -> None:
    expectation = validate_expectation(_load(expectation_path, "expectation"))
    if not decrypted_tar.is_file() or decrypted_tar.is_symlink():
        raise LiveSmokeError("decrypted tar must be one regular file")
    try:
        with tarfile.open(decrypted_tar, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != 1 or members[0].name != "marker.bin" or not members[0].isfile():
                raise LiveSmokeError("decrypted tar has unexpected entries")
            extracted = archive.extractfile(members[0])
            if extracted is None:
                raise LiveSmokeError("decrypted marker is unreadable")
            marker = extracted.read(65)
    except (OSError, tarfile.TarError) as error:
        raise LiveSmokeError("decrypted payload is not the synthetic tar") from error
    if len(marker) != 64 or hashlib.sha256(marker).hexdigest() != expectation["marker_sha256"]:
        raise LiveSmokeError("decrypted marker does not match the source-free expectation")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare-source")
    prepare.add_argument("--output", required=True, type=pathlib.Path)
    prepare.add_argument("--expectation-output", required=True, type=pathlib.Path)

    artifact = commands.add_parser("validate-artifact")
    artifact.add_argument("--root", required=True, type=pathlib.Path)

    request = commands.add_parser("build-unwrap-request")
    request.add_argument("--artifact-root", required=True, type=pathlib.Path)
    request.add_argument("--workflow-commit", required=True)
    request.add_argument("--output", required=True, type=pathlib.Path)

    response = commands.add_parser("validate-unwrap-response")
    response.add_argument("--request", required=True, type=pathlib.Path)
    response.add_argument("--metadata", required=True, type=pathlib.Path)
    response.add_argument("--response", required=True, type=pathlib.Path)
    response.add_argument("--identity-output", required=True, type=pathlib.Path)

    reuse = commands.add_parser("validate-reuse-failure")
    reuse.add_argument("--metadata", required=True, type=pathlib.Path)
    reuse.add_argument("--response", required=True, type=pathlib.Path)

    decrypted = commands.add_parser("verify-decrypted")
    decrypted.add_argument("--decrypted-tar", required=True, type=pathlib.Path)
    decrypted.add_argument("--expectation", required=True, type=pathlib.Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-source":
            prepare_source(args.output, args.expectation_output)
        elif args.command == "validate-artifact":
            validate_artifact(args.root)
        elif args.command == "build-unwrap-request":
            build_unwrap_request(args.artifact_root, args.workflow_commit, args.output)
        elif args.command == "validate-unwrap-response":
            summary = validate_unwrap_response(
                args.request,
                args.metadata,
                args.response,
                args.identity_output,
            )
            print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
        elif args.command == "validate-reuse-failure":
            validate_reuse_failure(args.metadata, args.response)
        else:
            verify_decrypted(args.decrypted_tar, args.expectation)
    except (ContractError, LiveSmokeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
