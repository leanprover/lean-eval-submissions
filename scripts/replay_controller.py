#!/usr/bin/env python3
"""Build one credentialed replay handoff and canonical replay State events.

This helper performs no network, Git, AWS, decryption, or State writes.  It
validates the exact queue plan and schema-version-3 archive, creates a
submission-bound five-minute unwrap capability, builds the fixed executor
request, validates its source-free response, and wraps replay transitions in
complete State events for the protected controller workflow.
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
    AGE_FILE_KEY_MATERIAL_TYPE,
    ContractError,
    capability_digest,
    validate_age_identity_bytes,
    validate_binding,
    validate_envelope,
)
from replay_orchestrator import (  # noqa: E402
    FAILURE_REASONS,
    MAX_REPLAY_ATTEMPTS,
    REPLAY_ID,
    UUID7,
    ReplayError,
    plan_next,
    terminal_transition,
    validate_execution_plan,
    validate_queue,
    validate_verdict,
)

DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_JSON_BYTES = 512 * 1024
MAX_CIPHERTEXT_BYTES = 11 * 1024 * 1024
MAX_IDENTITY_BYTES = 4096
AGE_FILE_KEY_BYTES = 16
MAX_PLAINTEXT_BYTES = 10 * 1024 * 1024
RECOVERY_AFTER = dt.timedelta(hours=7)
UNWRAP_FIELDS = {
    "schema_version",
    "operation",
    "adapter",
    "envelope",
    "capability",
    "expected_purpose",
    "expected_runner_nonce",
}


class ReplayControllerError(ValueError):
    """A replay controller input or provider response is unsafe."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReplayControllerError(f"{label} must be an object with string keys")
    return value


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ReplayControllerError(f"{label} is invalid")
    return value


def _load(path: pathlib.Path, label: str, maximum: int = MAX_JSON_BYTES) -> Any:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > maximum:
            raise ReplayControllerError(f"{label} exceeds its size limit")
        return json.loads(raw.decode("utf-8"))
    except ReplayControllerError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReplayControllerError(f"{label} is not one UTF-8 JSON object") from error


def _write(path: pathlib.Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise ReplayControllerError(f"refusing to overwrite {path}")
    encoded = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(encoded)


def _write_bytes(path: pathlib.Path, value: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ReplayControllerError(f"refusing to overwrite {path}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(value)


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReplayControllerError("cannot read the encrypted archive") from error
    return digest.hexdigest()


def _timestamp(value: dt.datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != dt.timedelta(0):
        raise ReplayControllerError("trusted time must be UTC")
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReplayControllerError(f"{label} is not canonical UTC milliseconds")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ReplayControllerError(
            f"{label} is not canonical UTC milliseconds"
        ) from error
    if _timestamp(parsed) != value:
        raise ReplayControllerError(f"{label} is not canonical UTC milliseconds")
    return parsed


def _uuid7(now: dt.datetime, random_bytes: bytes | None = None) -> str:
    randomness = secrets.token_bytes(10) if random_bytes is None else random_bytes
    if not isinstance(randomness, bytes) or len(randomness) != 10:
        raise ReplayControllerError("UUIDv7 randomness must contain exactly ten bytes")
    milliseconds = int(now.timestamp() * 1000)
    if not 0 <= milliseconds <= 0xFFFFFFFFFFFF:
        raise ReplayControllerError("UUIDv7 time is outside its 48-bit range")
    raw = bytearray(16)
    raw[:6] = milliseconds.to_bytes(6, "big")
    raw[6] = 0x70 | (randomness[0] & 0x0F)
    raw[7] = randomness[1]
    raw[8] = 0x80 | (randomness[2] & 0x3F)
    raw[9:] = randomness[3:]
    encoded = raw.hex()
    return f"{encoded[:8]}-{encoded[8:12]}-{encoded[12:16]}-{encoded[16:20]}-{encoded[20:]}"


def _event_time(trusted_now: str, cause_time: str) -> dt.datetime:
    current = _parse_timestamp(trusted_now, "trusted_now")
    cause = _parse_timestamp(cause_time, "causation occurred_at")
    return max(current, cause + dt.timedelta(milliseconds=1))


def _validated_plan(plan_value: Any, queue_value: Any | None = None) -> dict[str, Any]:
    try:
        plan = validate_execution_plan(plan_value)
        if queue_value is not None:
            queue = validate_queue(queue_value)
            expected = plan_next(
                queue,
                plan["request"]["execution_profile"],
                plan["request"]["measurement_config"],
            )
            if plan != expected:
                raise ReplayControllerError(
                    "replay plan is not the next exact queue task"
                )
        return plan
    except (ReplayError, TypeError, ValueError) as error:
        if isinstance(error, ReplayControllerError):
            raise
        raise ReplayControllerError(str(error)) from error


def _validate_archive(
    plan: dict[str, Any], sidecar_value: Any, ciphertext_path: pathlib.Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = plan["request"]
    source = request["source"]
    if source.get("visibility") != "private":
        raise ReplayControllerError(
            "credentialed controller requires a private replay plan"
        )
    archive = source["archive"]
    sidecar = _object(sidecar_value, "archive sidecar")
    try:
        _validate_sidecar(sidecar, finalized=True)
    except SystemExit as error:
        raise ReplayControllerError(f"archive sidecar is invalid: {error}") from error
    if sidecar.get("schema_version") != 3:
        raise ReplayControllerError(
            "credentialed replay requires archive sidecar schema version 3"
        )
    if not ciphertext_path.is_file() or ciphertext_path.is_symlink():
        raise ReplayControllerError("encrypted archive is not one regular file")
    size = ciphertext_path.stat().st_size
    if size < 1 or size > MAX_CIPHERTEXT_BYTES:
        raise ReplayControllerError("encrypted archive exceeds its size limit")
    actual_digest = _sha256(ciphertext_path)
    expected_submission = request["result"]["submission_id"]
    if (
        sidecar.get("submission_id") != expected_submission
        or sidecar.get("sha256_ciphertext") != archive["archive_ciphertext_sha256"]
        or actual_digest != archive["archive_ciphertext_sha256"]
        or sidecar.get("size_bytes_ciphertext") != size
        or sidecar.get("benchmark_commit") != request["benchmark"]["commit"]
    ):
        raise ReplayControllerError(
            "archive, sidecar, and replay plan are not exactly bound"
        )
    plaintext_size = sidecar.get("size_bytes_plaintext_tar")
    if (
        type(plaintext_size) is not int
        or not 0 < plaintext_size <= MAX_PLAINTEXT_BYTES
        or not isinstance(sidecar.get("sha256_plaintext_tar"), str)
        or DIGEST.fullmatch(sidecar["sha256_plaintext_tar"]) is None
    ):
        raise ReplayControllerError(
            "archive plaintext identity exceeds the executor contract"
        )
    envelope = validate_envelope(sidecar.get("key_envelope"))
    if (
        envelope["submission_id"] != expected_submission
        or envelope["archive_ciphertext_sha256"] != actual_digest
    ):
        raise ReplayControllerError("archive envelope differs from the replay plan")
    return sidecar, envelope


def prepare_unwrap(
    plan_value: Any,
    sidecar_value: Any,
    ciphertext_path: pathlib.Path,
    trusted_now: str,
    *,
    request_random: bytes | None = None,
    runner_nonce: str | None = None,
) -> dict[str, Any]:
    plan = _validated_plan(plan_value)
    _, envelope = _validate_archive(plan, sidecar_value, ciphertext_path)
    current = _parse_timestamp(trusted_now, "trusted_now")
    request = plan["request"]
    archive = request["source"]["archive"]
    nonce = secrets.token_hex(32) if runner_nonce is None else runner_nonce
    _match(DIGEST, nonce, "runner_nonce")
    capability = {
        "schema_version": 1,
        "purpose": "lean-eval-replay",
        "request_id": _uuid7(current, request_random),
        "submission_id": request["result"]["submission_id"],
        "archive_repository": archive["archive_repository"],
        "archive_commit": archive["archive_commit"],
        "archive_path": archive["archive_path"],
        "archive_ciphertext_sha256": archive["archive_ciphertext_sha256"],
        "data_key_id": envelope["data_key_id"],
        "runner_nonce": nonce,
        "issued_at": _timestamp(current),
        "expires_at": _timestamp(current + dt.timedelta(minutes=5)),
        "max_uses": 1,
    }
    validate_binding(
        envelope,
        capability,
        expected_purpose="lean-eval-replay",
        expected_runner_nonce=nonce,
        now=current,
    )
    return {
        "schema_version": 1,
        "operation": "unwrap",
        "adapter": envelope["adapter"],
        "envelope": envelope,
        "capability": capability,
        "expected_purpose": "lean-eval-replay",
        "expected_runner_nonce": nonce,
    }


def build_executor_request(
    plan_value: Any,
    sidecar_value: Any,
    ciphertext_path: pathlib.Path,
    unwrap_value: Any,
    key_material_path: pathlib.Path,
) -> dict[str, Any]:
    plan = _validated_plan(plan_value)
    sidecar, envelope = _validate_archive(plan, sidecar_value, ciphertext_path)
    unwrap = _object(unwrap_value, "unwrap request")
    if set(unwrap) != UNWRAP_FIELDS or (
        unwrap.get("schema_version") != 1
        or unwrap.get("operation") != "unwrap"
        or unwrap.get("adapter") != envelope["adapter"]
        or unwrap.get("envelope") != envelope
        or unwrap.get("expected_purpose") != "lean-eval-replay"
    ):
        raise ReplayControllerError("unwrap request is not the exact replay capability")
    capability = _object(unwrap.get("capability"), "unwrap capability")
    validate_binding(
        envelope,
        capability,
        expected_purpose="lean-eval-replay",
        expected_runner_nonce=capability.get("runner_nonce"),
        now=dt.datetime.now(dt.timezone.utc),
    )
    archive = plan["request"]["source"]["archive"]
    expected_capability = {
        "submission_id": plan["request"]["result"]["submission_id"],
        "archive_repository": archive["archive_repository"],
        "archive_commit": archive["archive_commit"],
        "archive_path": archive["archive_path"],
        "archive_ciphertext_sha256": archive["archive_ciphertext_sha256"],
        "data_key_id": envelope["data_key_id"],
        "runner_nonce": unwrap["expected_runner_nonce"],
        "purpose": "lean-eval-replay",
        "max_uses": 1,
    }
    if any(capability.get(field) != expected for field, expected in expected_capability.items()):
        raise ReplayControllerError("unwrap capability differs from the execution plan")
    try:
        key_material = key_material_path.read_bytes()
        ciphertext = ciphertext_path.read_bytes()
    except OSError as error:
        raise ReplayControllerError("cannot read private executor input") from error
    if envelope["schema_version"] == 1:
        if not 0 < len(key_material) <= MAX_IDENTITY_BYTES:
            raise ReplayControllerError("plaintext identity exceeds its size limit")
        validate_age_identity_bytes(key_material)
    elif len(key_material) != AGE_FILE_KEY_BYTES:
        raise ReplayControllerError(
            "plaintext age file key must contain exactly 16 bytes"
        )
    archive_expectation = {
        "schema_version": envelope["schema_version"],
        "submission_id": capability["submission_id"],
        "archive_ciphertext_sha256": capability["archive_ciphertext_sha256"],
        "plaintext_tar_sha256": sidecar["sha256_plaintext_tar"],
        "plaintext_tar_size": sidecar["size_bytes_plaintext_tar"],
    }
    if envelope["schema_version"] == 2:
        archive_expectation["key_material_type"] = AGE_FILE_KEY_MATERIAL_TYPE
    common = {
        "runner_nonce": capability["runner_nonce"],
        "request": plan["request"],
        "archive_expectation": archive_expectation,
        "ciphertext_base64": base64.b64encode(ciphertext).decode("ascii"),
    }
    if envelope["schema_version"] == 1:
        return {
            "schema_version": 1,
            **common,
            "plaintext_identity_base64": base64.b64encode(key_material).decode("ascii"),
        }
    return {
        "schema_version": 2,
        **common,
        "key_material_type": AGE_FILE_KEY_MATERIAL_TYPE,
        "plaintext_key_material_base64": base64.b64encode(key_material).decode("ascii"),
    }


def unwrap_identity(
    request_value: Any, response_value: Any, metadata_value: Any
) -> bytes:
    request = _object(request_value, "unwrap request")
    if set(request) != UNWRAP_FIELDS:
        raise ReplayControllerError("unwrap request fields are not canonical")
    metadata = _object(metadata_value, "Lambda invocation metadata")
    if metadata.get("StatusCode") != 200 or "FunctionError" in metadata:
        raise ReplayControllerError("unwrap Lambda did not return a successful invocation")
    response = _object(response_value, "unwrap response")
    try:
        envelope = validate_envelope(request.get("envelope"))
    except ContractError as error:
        raise ReplayControllerError("unwrap envelope is invalid") from error
    if envelope["adapter"] != request.get("adapter"):
        raise ReplayControllerError("unwrap envelope names a different adapter")
    schema_version = envelope.get("schema_version")
    expected_fields = {
        "schema_version",
        "adapter",
        "request_id",
        "data_key_id",
        "capability_digest",
        "plaintext_identity_base64"
        if schema_version == 1
        else "plaintext_key_material_base64",
    }
    if schema_version == 2:
        expected_fields.add("key_material_type")
    if set(response) != expected_fields:
        raise ReplayControllerError("unwrap response fields are not canonical")
    capability = _object(request.get("capability"), "unwrap capability")
    expected_digest = capability_digest(capability)
    if (
        response.get("schema_version") != schema_version
        or response.get("adapter") != request.get("adapter")
        or response.get("request_id") != capability.get("request_id")
        or response.get("data_key_id") != envelope.get("data_key_id")
        or response.get("capability_digest") != expected_digest
    ):
        raise ReplayControllerError("unwrap response is not bound to the exact request")
    if schema_version == 1:
        encoded = response.get("plaintext_identity_base64")
        maximum = MAX_IDENTITY_BYTES
    else:
        if response.get("key_material_type") != AGE_FILE_KEY_MATERIAL_TYPE:
            raise ReplayControllerError("unwrap response key material type is invalid")
        encoded = response.get("plaintext_key_material_base64")
        maximum = AGE_FILE_KEY_BYTES
    if not isinstance(encoded, str) or len(encoded) > ((maximum + 2) // 3) * 4:
        raise ReplayControllerError(
            "unwrap response key material exceeds its encoded size limit"
        )
    try:
        material = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ReplayControllerError(
            "unwrap response key material is not canonical base64"
        ) from error
    if base64.b64encode(material).decode("ascii") != encoded:
        raise ReplayControllerError(
            "unwrap response key material is not canonical base64"
        )
    if schema_version == 1:
        validate_age_identity_bytes(material)
    elif len(material) != AGE_FILE_KEY_BYTES:
        raise ReplayControllerError(
            "unwrap response age file key must contain exactly 16 bytes"
        )
    return material


def validate_executor_response(response_value: Any, plan_value: Any) -> dict[str, Any]:
    plan = _validated_plan(plan_value)
    response = _object(response_value, "executor response")
    if set(response) != {"schema_version", "verdict", "destruction"}:
        raise ReplayControllerError("executor response fields are not canonical")
    if response["schema_version"] != 1 or response["destruction"] != "confirmed":
        raise ReplayControllerError("executor did not confirm schema and destruction")
    try:
        return validate_verdict(response["verdict"], plan["request"])
    except ReplayError as error:
        raise ReplayControllerError(str(error)) from error


def started_event(
    plan_value: Any,
    queue_value: Any,
    trusted_now: str,
    *,
    random_bytes: bytes | None = None,
) -> dict[str, Any]:
    plan = _validated_plan(plan_value, queue_value)
    queue = validate_queue(queue_value)
    task = queue["tasks"][0]
    transition = plan["started_transition"]
    occurred = _event_time(trusted_now, task["occurred_at"])
    return {
        "schema_version": 1,
        "event_id": _uuid7(occurred, random_bytes),
        "event_type": transition["event_type"],
        "occurred_at": _timestamp(occurred),
        "subject_id": transition["subject_id"],
        "causation_event_id": transition["causation_event_id"],
        "actor": {"kind": "system"},
        "payload": transition["payload"],
    }


def failure_verdict(plan_value: Any, reason: str) -> dict[str, Any]:
    plan = _validated_plan(plan_value)
    if reason not in FAILURE_REASONS:
        raise ReplayControllerError("replay failure reason is not registered")
    request = plan["request"]
    return {
        "schema_version": 1,
        "replay_task_id": request["replay_task_id"],
        "attempt": request["attempt"],
        "execution_outcome": "failed",
        "checker_outcome": None,
        "failure_reason": reason,
        "statistics": None,
    }


def terminal_event(
    plan_value: Any,
    verdict_value: Any,
    started_value: Any,
    trusted_now: str,
    *,
    random_bytes: bytes | None = None,
) -> dict[str, Any]:
    plan = _validated_plan(plan_value)
    started = _object(started_value, "replay.started event")
    transition = plan["started_transition"]
    if (
        set(started)
        != {
            "schema_version",
            "event_id",
            "event_type",
            "occurred_at",
            "subject_id",
            "causation_event_id",
            "actor",
            "payload",
        }
        or started.get("schema_version") != 1
        or started.get("event_type") != "replay.started"
        or started.get("subject_id") != transition["subject_id"]
        or started.get("causation_event_id") != transition["causation_event_id"]
        or started.get("actor") != {"kind": "system"}
        or started.get("payload") != transition["payload"]
    ):
        raise ReplayControllerError(
            "replay.started event does not match the execution plan"
        )
    started_id = _match(UUID7, started.get("event_id"), "replay.started event_id")
    body = terminal_transition(plan, verdict_value, started_id)
    occurred = _event_time(trusted_now, started.get("occurred_at"))
    return {
        "schema_version": 1,
        "event_id": _uuid7(occurred, random_bytes),
        "event_type": body["event_type"],
        "occurred_at": _timestamp(occurred),
        "subject_id": body["subject_id"],
        "causation_event_id": body["causation_event_id"],
        "actor": {"kind": "system"},
        "payload": body["payload"],
    }


def recover_running(domain_value: Any, trusted_now: str) -> dict[str, Any]:
    domain = _object(domain_value, "State domain view")
    tasks = domain.get("replay_tasks")
    if not isinstance(tasks, list):
        raise ReplayControllerError("State domain replay_tasks must be an array")
    running = [
        _object(task, "replay task")
        for task in tasks
        if isinstance(task, dict) and task.get("status") == "running"
    ]
    if not running:
        return {"schema_version": 1, "kind": "none"}
    task = min(
        running,
        key=lambda item: (
            str(item.get("occurred_at")),
            str(item.get("replay_task_id")),
        ),
    )
    now = _parse_timestamp(trusted_now, "trusted_now")
    started_at = _parse_timestamp(task.get("occurred_at"), "running replay occurred_at")
    if now - started_at < RECOVERY_AFTER:
        return {
            "schema_version": 1,
            "kind": "busy",
            "replay_task_id": _match(
                REPLAY_ID, task.get("replay_task_id"), "replay_task_id"
            ),
        }
    attempt = task.get("attempt")
    if (
        type(attempt) is not int
        or attempt < 1
        or attempt > MAX_REPLAY_ATTEMPTS
    ):
        raise ReplayControllerError("running replay attempt is invalid")
    event_id = _match(UUID7, task.get("event_id"), "running replay event_id")
    occurred = max(now, started_at + dt.timedelta(milliseconds=1))
    terminal = {
        "schema_version": 1,
        "event_id": _uuid7(occurred),
        "event_type": "replay.failed",
        "occurred_at": _timestamp(occurred),
        "subject_id": _match(REPLAY_ID, task.get("replay_task_id"), "replay_task_id"),
        "causation_event_id": event_id,
        "actor": {"kind": "system"},
        "payload": {
            "attempt": attempt,
            "reason_code": "runner_lost",
            "retryable": attempt < MAX_REPLAY_ATTEMPTS,
        },
    }
    return {"schema_version": 1, "kind": "failed", "event": terminal}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    unwrap = commands.add_parser("prepare-unwrap")
    for name in ("plan", "sidecar", "ciphertext", "output"):
        unwrap.add_argument(f"--{name}", required=True, type=pathlib.Path)
    unwrap.add_argument("--trusted-now", required=True)
    executor = commands.add_parser("build-executor-request")
    for name in ("plan", "sidecar", "ciphertext", "unwrap", "identity", "output"):
        executor.add_argument(f"--{name}", required=True, type=pathlib.Path)
    identity = commands.add_parser("unwrap-identity")
    for name in ("request", "response", "metadata", "output"):
        identity.add_argument(f"--{name}", required=True, type=pathlib.Path)
    response = commands.add_parser("validate-response")
    response.add_argument("--plan", required=True, type=pathlib.Path)
    response.add_argument("--response", required=True, type=pathlib.Path)
    response.add_argument("--verdict-output", required=True, type=pathlib.Path)
    event = commands.add_parser("state-event")
    event.add_argument("kind", choices=("started", "terminal", "failed"))
    event.add_argument("--plan", required=True, type=pathlib.Path)
    event.add_argument("--queue", type=pathlib.Path)
    event.add_argument("--started-event", type=pathlib.Path)
    event.add_argument("--verdict", type=pathlib.Path)
    event.add_argument("--reason", choices=sorted(FAILURE_REASONS))
    event.add_argument("--trusted-now", required=True)
    event.add_argument("--output", required=True, type=pathlib.Path)
    recover = commands.add_parser("recover")
    recover.add_argument("--domain", required=True, type=pathlib.Path)
    recover.add_argument("--trusted-now", required=True)
    recover.add_argument("--output", required=True, type=pathlib.Path)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "prepare-unwrap":
            _write(args.output, prepare_unwrap(
                _load(args.plan, "replay plan"),
                _load(args.sidecar, "archive sidecar"),
                args.ciphertext,
                args.trusted_now,
            ))
        elif args.command == "build-executor-request":
            _write(args.output, build_executor_request(
                _load(args.plan, "replay plan"),
                _load(args.sidecar, "archive sidecar"),
                args.ciphertext,
                _load(args.unwrap, "unwrap request"),
                args.identity,
            ))
        elif args.command == "unwrap-identity":
            _write_bytes(args.output, unwrap_identity(
                _load(args.request, "unwrap request"),
                _load(args.response, "unwrap response"),
                _load(args.metadata, "Lambda invocation metadata"),
            ))
        elif args.command == "validate-response":
            _write(args.verdict_output, validate_executor_response(
                _load(args.response, "executor response"),
                _load(args.plan, "replay plan"),
            ))
        elif args.command == "recover":
            _write(args.output, recover_running(
                _load(args.domain, "State domain view"), args.trusted_now
            ))
        elif args.kind == "started":
            if args.queue is None:
                raise ReplayControllerError("started event requires --queue")
            _write(args.output, started_event(
                _load(args.plan, "replay plan"),
                _load(args.queue, "replay queue"),
                args.trusted_now,
            ))
        else:
            if args.started_event is None:
                raise ReplayControllerError("terminal event requires --started-event")
            plan = _load(args.plan, "replay plan")
            verdict = (
                failure_verdict(plan, args.reason)
                if args.kind == "failed"
                else _load(args.verdict, "replay verdict") if args.verdict is not None
                else None
            )
            if verdict is None:
                raise ReplayControllerError("terminal event requires --verdict")
            _write(args.output, terminal_event(
                plan,
                verdict,
                _load(args.started_event, "replay.started event"),
                args.trusted_now,
            ))
    except (ReplayControllerError, ContractError, ReplayError, OSError) as error:
        print(f"replay-controller: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
