#!/usr/bin/env python3
"""Validate provider-neutral archive-key envelopes and unwrap capabilities.

This module deliberately performs no unwrap. A provider adapter may call these
validators before using its own atomic one-use store and root-key API; keeping
validation here prevents AWS or any later provider from defining stable IDs.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
from typing import Any, Protocol


UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
DIGEST = re.compile(r"[0-9a-f]{64}")
ARCHIVE_KEY_ID = re.compile(r"ak1_[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
COMMIT = re.compile(r"[0-9a-f]{40}")
ADAPTER = re.compile(r"[a-z][a-z0-9-]{0,63}-v[1-9][0-9]*")
BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z"
)
AGE_RECIPIENT = re.compile(r"age1[0-9a-z]{40,4090}")
MAX_CAPABILITY_LIFETIME = dt.timedelta(minutes=10)
PURPOSES = {"lean-eval-release", "lean-eval-replay"}
ENVELOPE_FIELDS = {
    "schema_version",
    "submission_id",
    "archive_ciphertext_sha256",
    "data_key_id",
    "age_recipient",
    "adapter",
    "wrapped_identity",
}
CAPABILITY_FIELDS = {
    "schema_version",
    "purpose",
    "request_id",
    "submission_id",
    "archive_repository",
    "archive_commit",
    "archive_path",
    "archive_ciphertext_sha256",
    "data_key_id",
    "runner_nonce",
    "issued_at",
    "expires_at",
    "max_uses",
}


class ContractError(ValueError):
    """An archive-key object violates the stable v1 contract."""


class OneUseStore(Protocol):
    """Provider-owned atomic consume boundary."""

    def consume(self, digest: str, expires_at: str) -> bool:
        """Return true exactly once; the transition must be atomic and durable."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContractError(f"{label} must be an object with string keys")
    return value


def _fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ContractError(
            f"{label} fields are not canonical; "
            f"missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ContractError(f"{label} has invalid format")
    return value


def canonical_archive_path(submission_id: str) -> str:
    _match(UUID7, submission_id, "submission_id")
    return f"archives/{submission_id.replace('-', '')[:2]}/{submission_id}.tar.age"


def archive_key_id(submission_id: str, age_recipient: str) -> str:
    _match(UUID7, submission_id, "submission_id")
    _match(AGE_RECIPIENT, age_recipient, "age_recipient")
    payload = f"{submission_id}\0{age_recipient}".encode("ascii")
    return "ak1_" + hashlib.sha256(b"lean-eval-archive-key-v1\0" + payload).hexdigest()


def capability_digest(capability_value: Any) -> str:
    capability = validate_capability(capability_value)
    canonical = json.dumps(
        capability,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "uc1_" + hashlib.sha256(
        b"lean-eval-unwrap-capability-v1\0" + canonical
    ).hexdigest()


def _timestamp(value: Any, label: str) -> dt.datetime:
    raw = _match(TIMESTAMP, value, label)
    try:
        parsed = dt.datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as error:
        raise ContractError(f"{label} is not a real UTC timestamp") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" != raw:
        raise ContractError(f"{label} is not canonical UTC milliseconds")
    return parsed


def validate_envelope(value: Any) -> dict[str, Any]:
    envelope = _object(value, "envelope")
    _fields(envelope, ENVELOPE_FIELDS, "envelope")
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        raise ContractError("envelope.schema_version must be integer 1")
    submission_id = _match(UUID7, envelope["submission_id"], "envelope.submission_id")
    _match(DIGEST, envelope["archive_ciphertext_sha256"], "envelope.archive_ciphertext_sha256")
    recipient = _match(AGE_RECIPIENT, envelope["age_recipient"], "envelope.age_recipient")
    expected_key_id = archive_key_id(submission_id, recipient)
    if envelope["data_key_id"] != expected_key_id:
        raise ContractError("envelope.data_key_id does not match submission and recipient")
    _match(ARCHIVE_KEY_ID, envelope["data_key_id"], "envelope.data_key_id")
    _match(ADAPTER, envelope["adapter"], "envelope.adapter")
    wrapped = _match(BASE64, envelope["wrapped_identity"], "envelope.wrapped_identity")
    if len(wrapped) > 16_384 or len(wrapped) % 4 != 0:
        raise ContractError("envelope.wrapped_identity is not bounded canonical base64")
    try:
        decoded = base64.b64decode(wrapped, validate=True)
    except ValueError as error:
        raise ContractError("envelope.wrapped_identity is not valid base64") from error
    if not decoded:
        raise ContractError("envelope.wrapped_identity must not be empty")
    return envelope


def validate_capability(value: Any) -> dict[str, Any]:
    capability = _object(value, "capability")
    _fields(capability, CAPABILITY_FIELDS, "capability")
    if type(capability["schema_version"]) is not int or capability["schema_version"] != 1:
        raise ContractError("capability.schema_version must be integer 1")
    if capability["purpose"] not in PURPOSES:
        raise ContractError("capability.purpose is not registered")
    _match(UUID7, capability["request_id"], "capability.request_id")
    submission_id = _match(UUID7, capability["submission_id"], "capability.submission_id")
    if capability["archive_repository"] != "leanprover/lean-eval-audit":
        _match(REPOSITORY, capability["archive_repository"], "capability.archive_repository")
        raise ContractError("capability.archive_repository is not the audit repository")
    _match(COMMIT, capability["archive_commit"], "capability.archive_commit")
    if capability["archive_path"] != canonical_archive_path(submission_id):
        raise ContractError("capability.archive_path is not canonical for submission_id")
    _match(DIGEST, capability["archive_ciphertext_sha256"], "capability.archive_ciphertext_sha256")
    _match(ARCHIVE_KEY_ID, capability["data_key_id"], "capability.data_key_id")
    _match(DIGEST, capability["runner_nonce"], "capability.runner_nonce")
    issued = _timestamp(capability["issued_at"], "capability.issued_at")
    expires = _timestamp(capability["expires_at"], "capability.expires_at")
    lifetime = expires - issued
    if lifetime <= dt.timedelta(0) or lifetime > MAX_CAPABILITY_LIFETIME:
        raise ContractError("capability lifetime must be positive and at most ten minutes")
    if type(capability["max_uses"]) is not int or capability["max_uses"] != 1:
        raise ContractError("capability.max_uses must be integer 1")
    return capability


def validate_binding(
    envelope_value: Any,
    capability_value: Any,
    *,
    expected_purpose: str,
    expected_runner_nonce: str,
    now: dt.datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope = validate_envelope(envelope_value)
    capability = validate_capability(capability_value)
    if expected_purpose not in PURPOSES or capability["purpose"] != expected_purpose:
        raise ContractError("capability purpose does not match the requested operation")
    _match(DIGEST, expected_runner_nonce, "expected_runner_nonce")
    if capability["runner_nonce"] != expected_runner_nonce:
        raise ContractError("capability is bound to a different runner nonce")
    if now.tzinfo is None or now.utcoffset() != dt.timedelta(0):
        raise ContractError("current time must be timezone-aware UTC")
    issued = _timestamp(capability["issued_at"], "capability.issued_at")
    expires = _timestamp(capability["expires_at"], "capability.expires_at")
    if now < issued:
        raise ContractError("capability is not yet valid")
    if now > expires:
        raise ContractError("capability is expired")
    for field in ("submission_id", "archive_ciphertext_sha256", "data_key_id"):
        if capability[field] != envelope[field]:
            raise ContractError(f"capability.{field} does not match envelope")
    return envelope, capability


def authorize_once(
    envelope_value: Any,
    capability_value: Any,
    *,
    expected_purpose: str,
    expected_runner_nonce: str,
    now: dt.datetime,
    store: OneUseStore,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Validate and durably consume before a provider performs any unwrap."""
    envelope, capability = validate_binding(
        envelope_value,
        capability_value,
        expected_purpose=expected_purpose,
        expected_runner_nonce=expected_runner_nonce,
        now=now,
    )
    digest = capability_digest(capability)
    if not store.consume(digest, capability["expires_at"]):
        raise ContractError("capability has already been consumed")
    return envelope, capability, digest


def kms_encryption_context(envelope_value: Any) -> dict[str, str]:
    """Return the exact non-secret context every root-key adapter must bind."""
    envelope = validate_envelope(envelope_value)
    recipient_digest = hashlib.sha256(envelope["age_recipient"].encode("ascii")).hexdigest()
    return {
        "contract": "lean-eval-archive-key-v1",
        "submission_id": envelope["submission_id"],
        "archive_ciphertext_sha256": envelope["archive_ciphertext_sha256"],
        "data_key_id": envelope["data_key_id"],
        "age_recipient_sha256": recipient_digest,
    }


def _read(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    envelope_parser = subparsers.add_parser("validate-envelope")
    envelope_parser.add_argument("path", type=pathlib.Path)
    binding_parser = subparsers.add_parser("validate-binding")
    binding_parser.add_argument("--envelope", required=True, type=pathlib.Path)
    binding_parser.add_argument("--capability", required=True, type=pathlib.Path)
    binding_parser.add_argument("--purpose", required=True, choices=sorted(PURPOSES))
    binding_parser.add_argument("--runner-nonce", required=True)
    binding_parser.add_argument("--now", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-envelope":
            envelope = validate_envelope(_read(args.path))
            print(json.dumps({"valid": True, "data_key_id": envelope["data_key_id"]}, sort_keys=True))
        else:
            now = _timestamp(args.now, "--now")
            envelope, capability = validate_binding(
                _read(args.envelope),
                _read(args.capability),
                expected_purpose=args.purpose,
                expected_runner_nonce=args.runner_nonce,
                now=now,
            )
            print(json.dumps({
                "valid": True,
                "request_id": capability["request_id"],
                "data_key_id": envelope["data_key_id"],
                "capability_digest": capability_digest(capability),
            }, sort_keys=True))
    except (ContractError, json.JSONDecodeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
