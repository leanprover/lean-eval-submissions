#!/usr/bin/env python3
"""Create one source-free, controller-bound production intake lease."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import secrets
import sys
import time

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
RUN = re.compile(r"[1-9][0-9]*\Z")
LEASE_SECONDS = 900


class LeaseError(ValueError):
    """Lease material cannot be safely constructed."""


def _uuid7(timestamp_ms: int) -> str:
    if not 0 <= timestamp_ms < 2**48:
        raise LeaseError("lease timestamp cannot be represented as UUIDv7")
    value = (timestamp_ms << 80) | (0x7 << 76) | (secrets.randbits(12) << 64)
    value |= (0b10 << 62) | secrets.randbits(62)
    encoded = f"{value:032x}"
    return f"{encoded[:8]}-{encoded[8:12]}-{encoded[12:16]}-{encoded[16:20]}-{encoded[20:]}"


def nonce_digest(nonce: str) -> str:
    return hashlib.sha256(
        b"lean-eval-auth-nonce-v1\0intake_lease\0" + nonce.encode("ascii")
    ).hexdigest()


def prepare(
    *,
    controller_commit: str,
    controller_run_attempt: str,
    controller_run_id: str,
    state_commit: str,
    target_commit: str,
    now: int,
    nonce: str | None = None,
) -> tuple[dict[str, str], dict[str, object]]:
    for value in (controller_commit, state_commit, target_commit):
        if COMMIT.fullmatch(value) is None:
            raise LeaseError("lease commit binding is not canonical")
    for value in (controller_run_attempt, controller_run_id):
        if RUN.fullmatch(value) is None:
            raise LeaseError("lease controller run binding is not canonical")
    if controller_commit != target_commit:
        raise LeaseError("lease controller and target commits must match")
    if type(now) is not int or not 1_000_000_000 <= now <= 8_999_999_999:
        raise LeaseError("lease issue time is not canonical")
    nonce = secrets.token_hex(32) if nonce is None else nonce
    if re.fullmatch(r"[0-9a-f]{64}", nonce) is None:
        raise LeaseError("lease nonce is not canonical")
    expires_at = now + LEASE_SECONDS
    event_id = _uuid7(now * 1000)
    bindings = {
        "INTAKE_ENABLED": "true",
        "INTAKE_ENABLEMENT_MODE": "leased",
        "INTAKE_LEASE_CONTROLLER_COMMIT": controller_commit,
        "INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT": controller_run_attempt,
        "INTAKE_LEASE_CONTROLLER_RUN_ID": controller_run_id,
        "INTAKE_LEASE_EVENT_ID": event_id,
        "INTAKE_LEASE_EXPIRES_AT": str(expires_at),
        "INTAKE_LEASE_ISSUED_AT": str(now),
        "INTAKE_LEASE_NONCE_DIGEST": nonce_digest(nonce),
        "INTAKE_LEASE_STATE_COMMIT": state_commit,
        "INTAKE_LEASE_TARGET_COMMIT": target_commit,
    }
    smoke: dict[str, object] = {
        "schema_version": 1,
        "environment": "production",
        "controller_commit": controller_commit,
        "controller_run_attempt": controller_run_attempt,
        "controller_run_id": controller_run_id,
        "event_id": event_id,
        "expires_at": expires_at,
        "issued_at": now,
        "nonce": nonce,
        "state_commit": state_commit,
        "target_commit": target_commit,
    }
    return bindings, smoke


def _write_new(path: pathlib.Path, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise LeaseError("lease output path cannot be created exclusively") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise LeaseError("lease output cannot be written durably") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-commit", required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--bindings-output", required=True, type=pathlib.Path)
    parser.add_argument("--smoke-output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        bindings, smoke = prepare(
            controller_commit=os.environ.get("GITHUB_SHA", ""),
            controller_run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            controller_run_id=os.environ.get("GITHUB_RUN_ID", ""),
            state_commit=args.state_commit,
            target_commit=args.target_commit,
            now=int(time.time()),
        )
        _write_new(
            args.bindings_output,
            "\n".join(f"{name}={value}" for name, value in bindings.items()) + "\n",
        )
        _write_new(
            args.smoke_output,
            json.dumps(smoke, separators=(",", ":"), sort_keys=True) + "\n",
        )
    except (LeaseError, OSError, ValueError):
        print("intake-enablement-lease: creation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
