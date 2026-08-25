#!/usr/bin/env python3
"""Plan historical public replay without inventing a submission or archive.

This source-free controller is deliberately transport-blocked.  It validates
State's distinct historical-public queue, the exact authority plan and
qualified execution profile, prepares ordinary replay State events, and binds
the existing public runner handoff.  It performs no network, Git write, State
write, Cloudflare, AWS, or executor operation.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import secrets
import stat
import subprocess
import sys
from typing import Any

SCRIPT_DIRECTORY = pathlib.Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIRECTORY.parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from historical_public_runner import (
    HistoricalPublicRunnerError,
    _validate_matrix_binding,
    canonical_document_bytes,
    validate_contract,
    validate_handoff,
)
from replay_orchestrator import (
    FAILURE_REASONS,
    RETRYABLE_FAILURES,
    config_digest,
    replay_task_id,
    validate_execution_profile,
    validate_measurement_config,
)
from results_schema import ResultsSchemaError
from results_schema import result_id as stable_result_id

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_STATE_EVENT_FILES = 1_000_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_REPLAY_ATTEMPTS = 3
RECOVERY_AFTER = dt.timedelta(hours=7)
QUALIFICATION_WORKFLOW_PATH = ".github/workflows/historical-public-image-qualification.yml"
QUALIFICATION_CONTROLLER_PATH = "historical-public-qualification/qualification.py"
QUALIFICATION_CONTRACT_PATH = "historical-public-qualification/contract-v1.json"
TRANSPORT_REASON = "historical_public_executor_not_implemented"
TRANSPORT_CONTRACT = "historical_public_executor_v1"
GIST_ADAPTER_REASON = "historical_public_gist_source_adapter_not_implemented"
GIST_ADAPTER_CONTRACT = "historical_public_gist_source_adapter_v1"
ATTEMPT_LIMIT_REASON = "historical_public_attempt_limit_reached"
ATTEMPT_BINDING_REASON = "historical_public_attempt_binding_not_implemented"

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REPLAY_ID = re.compile(r"rt1_[0-9a-f]{64}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
REQUEST_ID = re.compile(r"prr_[0-9a-f]{64}\Z")
UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
REPOSITORY = re.compile(
    r"(?!\.{1,2}/)(?![^/]+/\.{1,2}\Z)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z"
)
PROBLEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
LOGIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?\Z")
TOOLCHAIN = re.compile(
    r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta)[0-9]+)?\Z"
)
TIMESTAMP_MS = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z"
)
TIMESTAMP_SECONDS = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
RESULTS_PATH = re.compile(r"results/[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?\.json\Z")
AUTHORITY_PATH = re.compile(r"evidence/public-replay/plans/[0-9a-f]{64}\.json\Z")
QUALIFICATION_PATH = re.compile(
    r"evidence/public-replay/profiles/[0-9a-f]{64}\.json\Z"
)
RECONFIGURATION_PATH = re.compile(
    r"evidence/public-replay/reconfigurations/[0-9a-f]{64}\.json\Z"
)

TASK_BASE_FIELDS = {
    "replay_task_id",
    "result_id",
    "request_id",
    "historical_accepted_at",
    "owner_login",
    "declared_model",
    "problem_id",
    "statement_revision",
    "results_repository",
    "results_commit",
    "results_path",
    "result_file_sha256",
    "result_tree_digest",
    "source_kind",
    "source_repository",
    "source_commit",
    "source_visibility",
    "benchmark_repository",
    "benchmark_commit",
    "toolchain",
    "lean_toolchain_blob_sha256",
    "workflow_run_identity_sha256",
    "authority_repository",
    "authority_commit",
    "authority_path",
    "authority_sha256",
    "authority_event_id",
    "authorized_at",
    "qualification_repository",
    "qualification_commit",
    "qualification_path",
    "qualification_sha256",
    "qualification_event_id",
    "qualified_at",
    "checker",
    "measurement_config_digest",
    "execution_profile_digest",
    "status",
    "attempt",
    "event_id",
    "occurred_at",
}
RECONFIGURATION_FIELDS = {
    "reconfiguration_event_id",
    "reconfigured_at",
    "superseded_qualification_event_id",
    "reconfiguration_repository",
    "reconfiguration_commit",
    "reconfiguration_path",
    "reconfiguration_sha256",
}
QUALIFICATION_FIELDS = {
    "schema_version",
    "kind",
    "qualification_status",
    "benchmark_repository",
    "benchmark_commit",
    "benchmark_tree",
    "plan_repository",
    "plan_commit",
    "plan_path",
    "plan_sha256",
    "profile_matrix_path",
    "profile_matrix_sha256",
    "runner_contract_path",
    "runner_contract_sha256",
    "qualification_contract_path",
    "qualification_contract_sha256",
    "workflow_repository",
    "workflow_path",
    "workflow_run_id",
    "workflow_run_attempt",
    "controller_source_commit",
    "image_source_commit",
    "qualification_workflow_sha256",
    "qualification_controller_sha256",
    "artifact_provenance_sha256",
    "artifact_archive_bindings",
    "artifact_file_sha256",
    "registry_repository",
    "registry_tag",
    "registry_manifest_digest",
    "execution_profile",
    "execution_profile_digest",
    "measurement_config",
    "measurement_config_digest",
}


class HistoricalReplayControllerError(ValueError):
    """A historical replay controller input or transition is unsafe."""


def canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise HistoricalReplayControllerError("value is not canonical JSON") from error


def state_canonical_bytes(value: Any) -> bytes:
    """Match lean-eval-state's committed event and materialized-view bytes."""
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise HistoricalReplayControllerError("value is not canonical State JSON") from error


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise HistoricalReplayControllerError(f"{label} must be an object with string keys")
    return value


def _fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise HistoricalReplayControllerError(f"{label} fields are not closed")


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise HistoricalReplayControllerError(f"{label} is invalid")
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_SAFE_INTEGER:
        raise HistoricalReplayControllerError(f"{label} is invalid")
    return value


def _timestamp(value: Any, label: str, *, milliseconds: bool = True) -> str:
    pattern = TIMESTAMP_MS if milliseconds else TIMESTAMP_SECONDS
    text = _match(pattern, value, label)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise HistoricalReplayControllerError(f"{label} is not a real timestamp") from error
    rendered = parsed.isoformat(timespec="milliseconds" if milliseconds else "seconds").replace(
        "+00:00", "Z"
    )
    if rendered != text:
        raise HistoricalReplayControllerError(f"{label} is not canonical UTC")
    return text


def _parse_timestamp(value: Any, label: str) -> dt.datetime:
    return dt.datetime.fromisoformat(_timestamp(value, label).replace("Z", "+00:00"))


def _uuid7(now: dt.datetime, random_bytes: bytes | None = None) -> str:
    randomness = secrets.token_bytes(10) if random_bytes is None else random_bytes
    if not isinstance(randomness, bytes) or len(randomness) != 10:
        raise HistoricalReplayControllerError("UUIDv7 randomness must be ten bytes")
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    if now.tzinfo is None or now.utcoffset() != dt.timedelta(0):
        raise HistoricalReplayControllerError("UUIDv7 time must be UTC")
    delta = now - epoch
    milliseconds = (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )
    if not 0 <= milliseconds <= 0xFFFFFFFFFFFF:
        raise HistoricalReplayControllerError("UUIDv7 time is outside its range")
    raw = bytearray(milliseconds.to_bytes(6, "big") + randomness)
    raw[6] = 0x70 | (raw[6] & 0x0F)
    raw[8] = 0x80 | (raw[8] & 0x3F)
    encoded = raw.hex()
    return f"{encoded[:8]}-{encoded[8:12]}-{encoded[12:16]}-{encoded[16:20]}-{encoded[20:]}"


def _event_time(trusted_now: str, cause_time: str) -> dt.datetime:
    current = _parse_timestamp(trusted_now, "trusted_now")
    cause = _parse_timestamp(cause_time, "causation occurred_at")
    return max(current, cause + dt.timedelta(milliseconds=1))


def _causal_uuid7(
    now: dt.datetime, cause_event_id: str, random_bytes: bytes | None = None
) -> str:
    cause = _match(UUID7, cause_event_id, "causation event_id")
    candidate = _uuid7(now, random_bytes)
    if candidate <= cause:
        raise HistoricalReplayControllerError(
            "cannot establish State event_id append order from trusted time"
        )
    return candidate


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise HistoricalReplayControllerError("JSON object contains duplicate keys")
        value[key] = item
    return value


def _reject_nonfinite_constant(value: str) -> None:
    raise HistoricalReplayControllerError(f"non-finite JSON number {value} is invalid")


def _read_regular(path: pathlib.Path, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= maximum:
                raise HistoricalReplayControllerError(f"{label} exceeds its size limit")
            raw = stream.read(maximum + 1)
            if len(raw) != metadata.st_size:
                raise HistoricalReplayControllerError(f"{label} changed while being read")
            return raw
    except HistoricalReplayControllerError:
        raise
    except OSError as error:
        raise HistoricalReplayControllerError(f"{label} is unavailable") from error


def _parse_canonical(
    raw: bytes,
    label: str,
    serializer: Any = canonical_bytes,
) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_constant,
        )
    except HistoricalReplayControllerError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise HistoricalReplayControllerError(f"{label} is not UTF-8 JSON") from error
    value = _object(value, label)
    if serializer(value) != raw:
        raise HistoricalReplayControllerError(f"{label} is not canonical JSON")
    return value


def _load_canonical(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, MAX_JSON_BYTES, label)
    value = _parse_canonical(raw, label)
    return value, raw


def _load_state_canonical(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, MAX_JSON_BYTES, label)
    value = _parse_canonical(raw, label, state_canonical_bytes)
    return value, raw


def _write(path: pathlib.Path, value: Any, serializer: Any = canonical_bytes) -> None:
    if path.exists() or path.is_symlink():
        raise HistoricalReplayControllerError("refusing to overwrite controller output")
    raw = serializer(value)
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise HistoricalReplayControllerError("controller output is unavailable") from error


def _validate_schema(value: Any, schema_name: str, label: str) -> None:
    try:
        import jsonschema
        import referencing
    except ImportError as error:
        raise HistoricalReplayControllerError("jsonschema is required") from error
    try:
        registry = referencing.Registry()
        schema = None
        for path in sorted((REPOSITORY_ROOT / "schemas").glob("*.json")):
            candidate = json.loads(path.read_text(encoding="utf-8"))
            resource = referencing.Resource.from_contents(candidate)
            identifier = candidate.get("$id")
            if isinstance(identifier, str):
                registry = registry.with_resource(identifier, resource)
            if path.name == schema_name:
                schema = candidate
        if schema is None:
            raise HistoricalReplayControllerError("required JSON schema is unavailable")
        jsonschema.Draft202012Validator(schema, registry=registry).validate(value)
    except HistoricalReplayControllerError:
        raise
    except (
        OSError,
        json.JSONDecodeError,
        jsonschema.SchemaError,
        jsonschema.ValidationError,
        referencing.exceptions.CannotDetermineSpecification,
        referencing.exceptions.InvalidAnchor,
        referencing.exceptions.NoInternalID,
        referencing.exceptions.NoSuchAnchor,
        referencing.exceptions.NoSuchResource,
        referencing.exceptions.PointerToNowhere,
        referencing.exceptions.Unresolvable,
        referencing.exceptions.Unretrievable,
    ) as error:
        raise HistoricalReplayControllerError(f"{label} does not match its schema") from error


def _validate_task(value: Any, index: int) -> dict[str, Any]:
    label = f"historical queue tasks[{index}]"
    task = _object(value, label)
    status = task.get("status")
    expected = set(TASK_BASE_FIELDS)
    if status == "failed":
        expected |= {"reason_code", "retryable"}
    if "reconfiguration_event_id" in task:
        expected |= RECONFIGURATION_FIELDS
    _fields(task, expected, label)
    replay_id = _match(REPLAY_ID, task["replay_task_id"], f"{label}.replay_task_id")
    result_id = _match(RESULT_ID, task["result_id"], f"{label}.result_id")
    _match(REQUEST_ID, task["request_id"], f"{label}.request_id")
    _timestamp(task["historical_accepted_at"], f"{label}.historical_accepted_at", milliseconds=False)
    owner = _match(LOGIN, task["owner_login"], f"{label}.owner_login")
    model = task["declared_model"]
    try:
        model_size = len(model.encode("utf-8")) if isinstance(model, str) else 0
    except UnicodeEncodeError as error:
        raise HistoricalReplayControllerError(f"{label}.declared_model is invalid") from error
    if (
        not isinstance(model, str)
        or not model
        or model_size > 256
        or any(ord(character) <= 0x1F or ord(character) == 0x7F for character in model)
    ):
        raise HistoricalReplayControllerError(f"{label}.declared_model is invalid")
    problem = _match(PROBLEM, task["problem_id"], f"{label}.problem_id")
    revision = _integer(task["statement_revision"], f"{label}.statement_revision", 1)
    try:
        expected_result_id = stable_result_id(owner, model, problem, revision)
    except ResultsSchemaError as error:
        raise HistoricalReplayControllerError(f"{label}.result_id is invalid") from error
    if result_id != expected_result_id:
        raise HistoricalReplayControllerError(f"{label}.result_id differs from its identity")
    if task["results_repository"] != "leanprover/lean-eval-submissions":
        raise HistoricalReplayControllerError(f"{label}.results_repository is invalid")
    _match(COMMIT, task["results_commit"], f"{label}.results_commit")
    results_path = _match(RESULTS_PATH, task["results_path"], f"{label}.results_path")
    if results_path != f"results/{owner}.json":
        raise HistoricalReplayControllerError(f"{label}.results_path differs from owner")
    for field in (
        "result_file_sha256",
        "result_tree_digest",
        "lean_toolchain_blob_sha256",
        "workflow_run_identity_sha256",
        "authority_sha256",
        "qualification_sha256",
        "measurement_config_digest",
        "execution_profile_digest",
    ):
        _match(DIGEST, task[field], f"{label}.{field}")
    if task["source_kind"] not in {"github_repo", "gist"}:
        raise HistoricalReplayControllerError(f"{label}.source_kind is invalid")
    for field in ("source_repository", "benchmark_repository"):
        _match(REPOSITORY, task[field], f"{label}.{field}")
    for field in (
        "source_commit",
        "benchmark_commit",
        "authority_commit",
        "qualification_commit",
    ):
        _match(COMMIT, task[field], f"{label}.{field}")
    if task["source_visibility"] != "public":
        raise HistoricalReplayControllerError(f"{label} is not explicitly public")
    if task["benchmark_repository"] != "leanprover/lean-eval":
        raise HistoricalReplayControllerError(f"{label}.benchmark_repository is invalid")
    _match(TOOLCHAIN, task["toolchain"], f"{label}.toolchain")
    if task["authority_repository"] != "leanprover/lean-eval-submissions":
        raise HistoricalReplayControllerError(f"{label}.authority_repository is invalid")
    authority_path = _match(AUTHORITY_PATH, task["authority_path"], f"{label}.authority_path")
    if authority_path != f"evidence/public-replay/plans/{task['authority_sha256']}.json":
        raise HistoricalReplayControllerError(f"{label}.authority_path differs from digest")
    if task["qualification_repository"] != "leanprover/lean-eval-submissions":
        raise HistoricalReplayControllerError(f"{label}.qualification_repository is invalid")
    qualification_path = _match(
        QUALIFICATION_PATH, task["qualification_path"], f"{label}.qualification_path"
    )
    if qualification_path != (
        "evidence/public-replay/profiles/"
        f"{task['execution_profile_digest']}.json"
    ):
        raise HistoricalReplayControllerError(f"{label}.qualification_path differs from profile")
    for field in ("authority_event_id", "qualification_event_id", "event_id"):
        _match(UUID7, task[field], f"{label}.{field}")
    for field in ("authorized_at", "qualified_at", "occurred_at"):
        _timestamp(task[field], f"{label}.{field}")
    if task["checker"] != "nanoda":
        raise HistoricalReplayControllerError(f"{label}.checker is not nanoda")
    attempt = _integer(task["attempt"], f"{label}.attempt")
    if status == "queued":
        if attempt != 0 and "reconfiguration_event_id" not in task:
            raise HistoricalReplayControllerError(f"{label} queued initial attempt is not zero")
    elif status == "failed":
        if (
            attempt < 1
            or task["retryable"] is not True
            or task["reason_code"] not in RETRYABLE_FAILURES
        ):
            raise HistoricalReplayControllerError(f"{label} failed task is not retryable")
    else:
        raise HistoricalReplayControllerError(f"{label}.status is not queueable")
    if replay_id != replay_task_id(result_id, task["measurement_config_digest"]):
        raise HistoricalReplayControllerError(f"{label}.replay_task_id differs from its identity")
    if "reconfiguration_event_id" in task:
        for field in (
            "reconfiguration_event_id",
            "superseded_qualification_event_id",
        ):
            _match(UUID7, task[field], f"{label}.{field}")
        _timestamp(task["reconfigured_at"], f"{label}.reconfigured_at")
        if task["reconfiguration_repository"] != "leanprover/lean-eval-submissions":
            raise HistoricalReplayControllerError(f"{label}.reconfiguration_repository is invalid")
        _match(COMMIT, task["reconfiguration_commit"], f"{label}.reconfiguration_commit")
        path = _match(
            RECONFIGURATION_PATH,
            task["reconfiguration_path"],
            f"{label}.reconfiguration_path",
        )
        digest = _match(DIGEST, task["reconfiguration_sha256"], f"{label}.reconfiguration_sha256")
        if path != f"evidence/public-replay/reconfigurations/{digest}.json":
            raise HistoricalReplayControllerError(f"{label}.reconfiguration_path differs from digest")
    return task


def validate_queue(value: Any) -> dict[str, Any]:
    queue = _object(value, "historical public replay queue")
    _fields(
        queue,
        {"schema_version", "environment", "source_event_count", "source_digest", "tasks"},
        "historical public replay queue",
    )
    if queue["schema_version"] != 2 or queue["environment"] not in {"staging", "production"}:
        raise HistoricalReplayControllerError("historical queue identity is invalid")
    _integer(queue["source_event_count"], "historical queue source_event_count", 1)
    _match(DIGEST, queue["source_digest"], "historical queue source_digest")
    if not isinstance(queue["tasks"], list):
        raise HistoricalReplayControllerError("historical queue tasks must be an array")
    tasks = [_validate_task(task, index) for index, task in enumerate(queue["tasks"])]
    identities = [task["replay_task_id"] for task in tasks]
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise HistoricalReplayControllerError("historical queue tasks are not unique and sorted")
    return queue


def validate_qualification(value: Any, raw: bytes) -> dict[str, Any]:
    profile = _object(value, "historical qualification profile")
    _validate_schema(
        profile,
        "historical-public-profile-qualification-v1.schema.json",
        "historical qualification profile",
    )
    _fields(profile, QUALIFICATION_FIELDS, "historical qualification profile")
    if (
        profile["schema_version"] != 1
        or profile["kind"] != "historical_public_replay_profile_qualification"
        or profile["qualification_status"] != "qualified"
        or profile["benchmark_repository"] != "leanprover/lean-eval"
        or profile["plan_repository"] != "leanprover/lean-eval-submissions"
        or profile["workflow_repository"] != "leanprover/lean-eval-submissions"
        or profile["workflow_path"] != QUALIFICATION_WORKFLOW_PATH
        or profile["registry_repository"] != "lean-eval-historical-public-v1"
    ):
        raise HistoricalReplayControllerError("historical qualification identity is invalid")
    for field in (
        "benchmark_commit",
        "benchmark_tree",
        "plan_commit",
        "controller_source_commit",
        "image_source_commit",
    ):
        _match(COMMIT, profile[field], f"historical qualification {field}")
    for field in (
        "plan_sha256",
        "profile_matrix_sha256",
        "runner_contract_sha256",
        "qualification_contract_sha256",
        "qualification_workflow_sha256",
        "qualification_controller_sha256",
        "artifact_provenance_sha256",
        "execution_profile_digest",
        "measurement_config_digest",
    ):
        _match(DIGEST, profile[field], f"historical qualification {field}")
    if profile["plan_path"] != f"evidence/public-replay/plans/{profile['plan_sha256']}.json":
        raise HistoricalReplayControllerError("qualification plan path differs from digest")
    if profile["profile_matrix_path"] != "configuration/historical-public-replay-profile-matrix-v1.json":
        raise HistoricalReplayControllerError("qualification matrix path changed")
    if profile["runner_contract_path"] != "configuration/historical-public-runner-v1.json":
        raise HistoricalReplayControllerError("qualification runner contract path changed")
    if profile["qualification_contract_path"] != QUALIFICATION_CONTRACT_PATH:
        raise HistoricalReplayControllerError("qualification producer contract path changed")
    execution_profile = validate_execution_profile(profile["execution_profile"])
    measurement = validate_measurement_config(profile["measurement_config"])
    expected_registry_tag = f"{profile['benchmark_commit']}-{profile['image_source_commit']}"
    if profile["registry_manifest_digest"] != execution_profile["vm_image_digest"]:
        raise HistoricalReplayControllerError(
            "qualified registry manifest differs from execution image"
        )
    if profile["registry_tag"] != expected_registry_tag:
        raise HistoricalReplayControllerError(
            "qualified registry tag differs from producer identity"
        )
    if profile["execution_profile_digest"] != config_digest(
        "lean-eval-replay-execution-profile-v1", execution_profile
    ):
        raise HistoricalReplayControllerError("qualified execution profile digest differs")
    if profile["measurement_config_digest"] != config_digest(
        "lean-eval-replay-measurement-config-v1", measurement
    ):
        raise HistoricalReplayControllerError("qualified measurement digest differs")
    if canonical_bytes(profile) != raw:
        raise HistoricalReplayControllerError("qualification profile bytes are not canonical")
    return profile


def _validate_selected_matrix_entry(
    task: dict[str, Any], qualification: dict[str, Any], matrix: dict[str, Any]
) -> None:
    entries = [
        entry
        for entry in matrix.get("images", [])
        if isinstance(entry, dict)
        and entry.get("benchmark_commit") == task["benchmark_commit"]
    ]
    if len(entries) != 1:
        raise HistoricalReplayControllerError("qualified benchmark matrix entry is not unique")
    entry = entries[0]
    profile_lock = entry.get("profile_lock")
    execution_profile = qualification["execution_profile"]
    if not isinstance(profile_lock, dict):
        raise HistoricalReplayControllerError("qualified benchmark matrix lock is invalid")
    shared_profile = {
        key: execution_profile.get(key)
        for key in profile_lock
        if key not in {"benchmark_commit", "benchmark_repository"}
    }
    expected_profile = {
        key: value
        for key, value in profile_lock.items()
        if key not in {"benchmark_commit", "benchmark_repository"}
    }
    if (
        matrix.get("qualification_status") != "unqualified"
        or matrix.get("qualification_requirements")
        != [
            "historical_public_runner_v1",
            "immutable_registry_publication_v1",
            "cloudflare_staging_runtime_probe_v1",
        ]
        or matrix.get("plan_sha256") != task["authority_sha256"]
        or entry.get("benchmark_tree") != qualification["benchmark_tree"]
        or entry.get("toolchain") != task["toolchain"]
        or entry.get("lean_toolchain_blob_sha256")
        != task["lean_toolchain_blob_sha256"]
        or task["problem_id"] not in entry.get("problem_ids", [])
        or profile_lock.get("benchmark_repository") != task["benchmark_repository"]
        or profile_lock.get("benchmark_commit") != task["benchmark_commit"]
        or shared_profile != expected_profile
    ):
        raise HistoricalReplayControllerError(
            "qualification differs from its selected profile matrix entry"
        )


def _find_plan_selection(
    authority_plan: dict[str, Any], task: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    requests = [
        request
        for request in authority_plan.get("requests", [])
        if isinstance(request, dict) and request.get("request_id") == task["request_id"]
    ]
    if len(requests) != 1:
        raise HistoricalReplayControllerError("queued authority request is not unique")
    request = requests[0]
    results = [
        result
        for result in request.get("results", [])
        if isinstance(result, dict) and result.get("result_id") == task["result_id"]
    ]
    if len(results) != 1:
        raise HistoricalReplayControllerError("queued authority result is not unique")
    return request, results[0]


def _validate_cross_bindings(
    task: dict[str, Any],
    authority_plan: dict[str, Any],
    authority_raw: bytes,
    qualification: dict[str, Any],
    qualification_raw: bytes,
    matrix: dict[str, Any],
    matrix_raw: bytes,
    contract: dict[str, Any],
    contract_raw: bytes,
) -> None:
    if (
        canonical_bytes(authority_plan) != authority_raw
        or canonical_bytes(matrix) != matrix_raw
        or canonical_bytes(contract) != contract_raw
    ):
        raise HistoricalReplayControllerError("reviewed controller input bytes differ")
    _validate_schema(authority_plan, "historical-public-replay-plan-v1.schema.json", "authority plan")
    _validate_schema(matrix, "historical-public-replay-profile-matrix-v1.schema.json", "profile matrix")
    validate_contract(contract)
    _validate_selected_matrix_entry(task, qualification, matrix)
    authority_sha = sha256_bytes(authority_raw)
    qualification_sha = sha256_bytes(qualification_raw)
    matrix_sha = sha256_bytes(matrix_raw)
    contract_sha = sha256_bytes(contract_raw)
    if (
        task["authority_sha256"] != authority_sha
        or task["authority_commit"] != qualification["plan_commit"]
        or task["authority_path"] != qualification["plan_path"]
        or qualification["plan_sha256"] != authority_sha
        or qualification["profile_matrix_sha256"] != matrix_sha
        or qualification["runner_contract_sha256"] != contract_sha
        or task["qualification_sha256"] != qualification_sha
        or task["qualification_path"]
        != f"evidence/public-replay/profiles/{qualification['execution_profile_digest']}.json"
        or task["benchmark_commit"] != qualification["benchmark_commit"]
        or task["toolchain"] != qualification["execution_profile"]["toolchain"]
        or task["execution_profile_digest"] != qualification["execution_profile_digest"]
        or task["measurement_config_digest"] != qualification["measurement_config_digest"]
    ):
        raise HistoricalReplayControllerError("queue, authority, and qualification bindings differ")
    request, result = _find_plan_selection(authority_plan, task)
    source = request["source"]
    benchmark = request["benchmark"]
    historical = request["historical_evaluation"]
    expected = {
        "request_id": request["request_id"],
        "historical_accepted_at": request["historical_accepted_at"],
        "owner_login": request["owner_login"],
        "declared_model": request["declared_model"],
        "problem_id": result["problem_id"],
        "statement_revision": result["statement_revision"],
        "results_repository": result["results_repository"],
        "results_commit": result["results_commit"],
        "results_path": result["results_path"],
        "result_file_sha256": result["result_file_sha256"],
        "result_tree_digest": result["result_tree_digest"],
        "source_kind": source["kind"],
        "source_repository": source["repository"],
        "source_commit": source["commit"],
        "source_visibility": source["visibility"],
        "benchmark_repository": benchmark["repository"],
        "benchmark_commit": benchmark["commit"],
        "toolchain": benchmark["toolchain"],
        "lean_toolchain_blob_sha256": benchmark["lean_toolchain_blob_sha256"],
        "workflow_run_identity_sha256": historical["workflow_run_identity_sha256"],
    }
    for field, expected_value in expected.items():
        if task[field] != expected_value:
            raise HistoricalReplayControllerError(f"queued {field} differs from authority plan")


def _transport() -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": TRANSPORT_REASON,
        "required_contract": TRANSPORT_CONTRACT,
    }


def _blocked_plan(
    bindings: dict[str, Any], task: dict[str, Any], reason: str, contract: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "blocked",
        "transport": _transport(),
        "blocker": {
            "status": "blocked",
            "reason": reason,
            "required_contract": contract,
        },
        "queue": {
            **bindings,
            "task_sha256": sha256_bytes(state_canonical_bytes(task)),
        },
    }


def _task_blocker(task: dict[str, Any]) -> tuple[str, str] | None:
    if task["source_kind"] == "gist":
        return GIST_ADAPTER_REASON, GIST_ADAPTER_CONTRACT
    if task["attempt"] >= MAX_REPLAY_ATTEMPTS:
        return ATTEMPT_LIMIT_REASON, "historical_public_retry_policy_v1"
    return None


def _next_eligible_task(queue: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (task for task in queue["tasks"] if _task_blocker(task) is None),
        None,
    )


def plan_next(
    queue_value: Any,
    authority_plan: dict[str, Any] | None = None,
    authority_raw: bytes | None = None,
    qualification_value: dict[str, Any] | None = None,
    qualification_raw: bytes | None = None,
    matrix_value: dict[str, Any] | None = None,
    matrix_raw: bytes | None = None,
    contract_value: dict[str, Any] | None = None,
    contract_raw: bytes | None = None,
) -> dict[str, Any]:
    queue = validate_queue(queue_value)
    bindings = {
        "queue_environment": queue["environment"],
        "queue_source_event_count": queue["source_event_count"],
        "queue_source_digest": queue["source_digest"],
    }
    if not queue["tasks"]:
        return {
            "schema_version": 1,
            "kind": "empty",
            "transport": _transport(),
            "queue": bindings,
        }
    eligible_task = _next_eligible_task(queue)
    task = queue["tasks"][0] if eligible_task is None else eligible_task
    if any(
        value is None
        for value in (
            authority_plan,
            authority_raw,
            qualification_value,
            qualification_raw,
            matrix_value,
            matrix_raw,
            contract_value,
            contract_raw,
        )
    ):
        raise HistoricalReplayControllerError("nonempty queue requires all reviewed bindings")
    assert authority_plan is not None and authority_raw is not None
    assert qualification_value is not None and qualification_raw is not None
    assert matrix_value is not None and matrix_raw is not None
    assert contract_value is not None and contract_raw is not None
    qualification = validate_qualification(qualification_value, qualification_raw)
    _validate_cross_bindings(
        task,
        authority_plan,
        authority_raw,
        qualification,
        qualification_raw,
        matrix_value,
        matrix_raw,
        contract_value,
        contract_raw,
    )
    if eligible_task is None:
        blocker = _task_blocker(task)
        assert blocker is not None
        return _blocked_plan(bindings, task, *blocker)
    attempt = task["attempt"] + 1
    return {
        "schema_version": 1,
        "kind": "execution",
        "transport": _transport(),
        "queue": {
            **bindings,
            "task_sha256": sha256_bytes(state_canonical_bytes(task)),
        },
        "inputs": {
            "authority_plan_sha256": sha256_bytes(authority_raw),
            "qualification_profile_sha256": sha256_bytes(qualification_raw),
            "profile_matrix_sha256": sha256_bytes(matrix_raw),
            "runner_contract_sha256": sha256_bytes(contract_raw),
        },
        "task": copy.deepcopy(task),
        "execution_profile": copy.deepcopy(qualification["execution_profile"]),
        "measurement_config": copy.deepcopy(qualification["measurement_config"]),
        "started_transition": {
            "event_type": "replay.started",
            "subject_id": task["replay_task_id"],
            "causation_event_id": task["event_id"],
            "payload": {
                "attempt": attempt,
                "runner_profile": qualification["execution_profile"]["runner_profile"],
            },
        },
    }


def validate_execution_plan(value: Any) -> dict[str, Any]:
    plan = _object(value, "historical execution plan")
    _fields(
        plan,
        {
            "schema_version",
            "kind",
            "transport",
            "queue",
            "inputs",
            "task",
            "execution_profile",
            "measurement_config",
            "started_transition",
        },
        "historical execution plan",
    )
    if plan["schema_version"] != 1 or plan["kind"] != "execution" or plan["transport"] != _transport():
        raise HistoricalReplayControllerError("historical execution plan identity changed")
    task = _validate_task(plan["task"], 0)
    if task["source_kind"] != "github_repo":
        raise HistoricalReplayControllerError("historical execution plan lacks a source adapter")
    if task["attempt"] >= MAX_REPLAY_ATTEMPTS:
        raise HistoricalReplayControllerError("historical execution plan exceeds attempt limit")
    profile = validate_execution_profile(plan["execution_profile"])
    measurement = validate_measurement_config(plan["measurement_config"])
    inputs = _object(plan["inputs"], "historical plan inputs")
    _fields(
        inputs,
        {
            "authority_plan_sha256",
            "qualification_profile_sha256",
            "profile_matrix_sha256",
            "runner_contract_sha256",
        },
        "historical plan inputs",
    )
    for field in inputs:
        _match(DIGEST, inputs[field], f"historical plan inputs.{field}")
    if (
        inputs["authority_plan_sha256"] != task["authority_sha256"]
        or inputs["qualification_profile_sha256"] != task["qualification_sha256"]
    ):
        raise HistoricalReplayControllerError("historical plan reviewed inputs differ from task")
    queue = _object(plan["queue"], "historical plan queue")
    _fields(
        queue,
        {"queue_environment", "queue_source_event_count", "queue_source_digest", "task_sha256"},
        "historical plan queue",
    )
    if queue["queue_environment"] not in {"staging", "production"}:
        raise HistoricalReplayControllerError("historical plan environment is invalid")
    _integer(queue["queue_source_event_count"], "historical plan source count", 1)
    _match(DIGEST, queue["queue_source_digest"], "historical plan source digest")
    if queue["task_sha256"] != sha256_bytes(state_canonical_bytes(task)):
        raise HistoricalReplayControllerError("historical plan task digest differs")
    if (
        task["execution_profile_digest"]
        != config_digest("lean-eval-replay-execution-profile-v1", profile)
        or task["measurement_config_digest"]
        != config_digest("lean-eval-replay-measurement-config-v1", measurement)
        or task["toolchain"] != profile["toolchain"]
    ):
        raise HistoricalReplayControllerError("historical plan profile differs from task")
    transition = _object(plan["started_transition"], "historical started transition")
    _fields(
        transition,
        {"event_type", "subject_id", "causation_event_id", "payload"},
        "historical started transition",
    )
    expected_transition = {
        "event_type": "replay.started",
        "subject_id": task["replay_task_id"],
        "causation_event_id": task["event_id"],
        "payload": {
            "attempt": task["attempt"] + 1,
            "runner_profile": profile["runner_profile"],
        },
    }
    if transition != expected_transition:
        raise HistoricalReplayControllerError("historical started transition differs")
    return plan


def validate_plan_against_queue(plan_value: Any, queue_value: Any) -> dict[str, Any]:
    plan = validate_execution_plan(plan_value)
    queue = validate_queue(queue_value)
    task = _next_eligible_task(queue)
    expected_queue = {
        "queue_environment": queue["environment"],
        "queue_source_event_count": queue["source_event_count"],
        "queue_source_digest": queue["source_digest"],
        **({} if task is None else {"task_sha256": sha256_bytes(state_canonical_bytes(task))}),
    }
    if task is None or task != plan["task"] or plan["queue"] != expected_queue:
        raise HistoricalReplayControllerError("historical plan is not the next live queue task")
    return plan


def started_event(
    plan_value: Any,
    queue_value: Any,
    trusted_now: str,
    *,
    random_bytes: bytes | None = None,
) -> dict[str, Any]:
    plan = validate_plan_against_queue(plan_value, queue_value)
    transition = plan["started_transition"]
    occurred = _event_time(trusted_now, plan["task"]["occurred_at"])
    return {
        "schema_version": 1,
        "event_id": _causal_uuid7(
            occurred,
            transition["causation_event_id"],
            random_bytes,
        ),
        "event_type": "replay.started",
        "occurred_at": occurred.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "subject_id": transition["subject_id"],
        "causation_event_id": transition["causation_event_id"],
        "actor": {"kind": "system"},
        "payload": transition["payload"],
    }


def _validate_started(plan: dict[str, Any], value: Any) -> dict[str, Any]:
    started = _object(value, "historical replay.started event")
    _fields(
        started,
        {
            "schema_version",
            "event_id",
            "event_type",
            "occurred_at",
            "subject_id",
            "causation_event_id",
            "actor",
            "payload",
        },
        "historical replay.started event",
    )
    transition = plan["started_transition"]
    if (
        started["schema_version"] != 1
        or started["event_type"] != "replay.started"
        or started["subject_id"] != transition["subject_id"]
        or started["causation_event_id"] != transition["causation_event_id"]
        or started["actor"] != {"kind": "system"}
        or started["payload"] != transition["payload"]
    ):
        raise HistoricalReplayControllerError("historical started event differs from plan")
    _match(UUID7, started["event_id"], "historical started event_id")
    _timestamp(started["occurred_at"], "historical started occurred_at")
    return started


def _terminal_transition(
    plan: dict[str, Any], verdict_value: Any | None, failure_reason: str | None
) -> dict[str, Any]:
    task = plan["task"]
    if task["source_kind"] != "github_repo":
        raise HistoricalReplayControllerError(
            "historical public runner handoff requires a github_repo source"
        )
    attempt = plan["started_transition"]["payload"]["attempt"]
    if failure_reason is not None:
        if verdict_value is not None or failure_reason not in FAILURE_REASONS:
            raise HistoricalReplayControllerError("historical orchestration failure is invalid")
        return {
            "event_type": "replay.failed",
            "payload": {
                "attempt": attempt,
                "reason_code": failure_reason,
                "retryable": failure_reason in RETRYABLE_FAILURES,
            },
        }
    if verdict_value is None:
        raise HistoricalReplayControllerError("historical terminal verdict is required")
    try:
        from historical_public_runner import validate_historical_verdict

        verdict = validate_historical_verdict(verdict_value)
    except HistoricalPublicRunnerError as error:
        raise HistoricalReplayControllerError(str(error)) from error
    if verdict["request_id"] != task["request_id"] or verdict["result_id"] != task["result_id"]:
        raise HistoricalReplayControllerError("historical verdict identity differs from task")
    outcome = verdict["execution_outcome"]
    event_type = (
        f"replay.{verdict['checker_outcome']}"
        if outcome == "completed"
        else f"replay.{outcome}"
    )
    statistics = verdict["statistics"]
    checker = statistics["checker_retired_instructions"]
    build = statistics["build_retired_instructions"]
    checker_measured = checker["status"] == "measured"
    build_measured = build["status"] == "measured"
    if plan["measurement_config"]["retired_instructions"]["required"] and not (
        checker_measured and build_measured
    ):
        raise HistoricalReplayControllerError("required historical counters are unavailable")
    return {
        "event_type": event_type,
        "payload": {
            "attempt": attempt,
            "checker": task["checker"],
            "checker_wall_time_ms": statistics["checker_wall_time_ms"],
            "checker_retired_instructions": checker["value"] if checker_measured else None,
            "checker_retired_instructions_unavailable_reason": (
                None if checker_measured else checker["reason"]
            ),
            "build_wall_time_ms": statistics["build_wall_time_ms"],
            "build_retired_instructions": build["value"] if build_measured else None,
            "build_retired_instructions_unavailable_reason": (
                None if build_measured else build["reason"]
            ),
            "lines_of_code": statistics["lines_of_code"],
            "file_count": statistics["file_count"],
        },
    }


def terminal_event(
    plan_value: Any,
    started_value: Any,
    trusted_now: str,
    *,
    verdict_value: Any | None = None,
    failure_reason: str | None = None,
    random_bytes: bytes | None = None,
) -> dict[str, Any]:
    validate_execution_plan(plan_value)
    raise HistoricalReplayControllerError(ATTEMPT_BINDING_REASON)


def bind_handoff(
    plan_value: Any,
    handoff_value: Any,
    source_archive: pathlib.Path,
    matrix_value: Any,
    contract_value: Any,
) -> dict[str, Any]:
    plan = validate_execution_plan(plan_value)
    contract = validate_contract(contract_value)
    if (
        sha256_bytes(canonical_bytes(matrix_value))
        != plan["inputs"]["profile_matrix_sha256"]
        or sha256_bytes(canonical_bytes(contract_value))
        != plan["inputs"]["runner_contract_sha256"]
    ):
        raise HistoricalReplayControllerError("historical runner inputs differ from plan")
    try:
        handoff = validate_handoff(handoff_value, contract)
        _validate_matrix_binding(handoff, matrix_value)
    except HistoricalPublicRunnerError as error:
        raise HistoricalReplayControllerError(str(error)) from error
    expected_archive_size = handoff["source"]["archive_size_bytes"]
    archive_raw = _read_regular(
        source_archive,
        contract["source_archive"]["maximum_compressed_bytes"],
        "historical source archive",
    )
    archive_size = len(archive_raw)
    if archive_size != expected_archive_size:
        raise HistoricalReplayControllerError("historical source archive size differs")
    archive_sha = hashlib.sha256(archive_raw).hexdigest()
    task = plan["task"]
    expected_result = {
        "result_id": task["result_id"],
        "problem_id": task["problem_id"],
        "statement_revision": task["statement_revision"],
        "results_repository": task["results_repository"],
        "results_commit": task["results_commit"],
        "result_tree_digest": task["result_tree_digest"],
    }
    if (
        handoff["plan_sha256"] != plan["inputs"]["authority_plan_sha256"]
        or handoff["profile_matrix_sha256"] != plan["inputs"]["profile_matrix_sha256"]
        or handoff["contract_sha256"] != plan["inputs"]["runner_contract_sha256"]
        or handoff["request_id"] != task["request_id"]
        or handoff["result"] != expected_result
        or handoff["source"]["repository"] != task["source_repository"]
        or handoff["source"]["commit"] != task["source_commit"]
        or handoff["benchmark"]["repository"] != task["benchmark_repository"]
        or handoff["benchmark"]["commit"] != task["benchmark_commit"]
        or handoff["benchmark"]["toolchain"] != task["toolchain"]
        or handoff["benchmark"]["lean_toolchain_blob_sha256"]
        != task["lean_toolchain_blob_sha256"]
        or handoff["source"]["archive_size_bytes"] != archive_size
        or handoff["source"]["archive_sha256"] != archive_sha
    ):
        raise HistoricalReplayControllerError("historical runner handoff differs from plan")
    return {
        "schema_version": 1,
        "kind": "historical_public_executor_transport_blocker",
        "transport": _transport(),
        "replay_task_id": task["replay_task_id"],
        "attempt": task["attempt"] + 1,
        "handoff_sha256": sha256_bytes(canonical_document_bytes(handoff)),
        "source_archive_sha256": archive_sha,
    }


def _event_files(root: pathlib.Path) -> list[pathlib.Path]:
    if not root.is_dir() or root.is_symlink():
        raise HistoricalReplayControllerError("State events root is not a directory")
    try:
        paths = sorted(root.glob("*/*.json"))
    except OSError as error:
        raise HistoricalReplayControllerError("State events are unavailable") from error
    if len(paths) > MAX_STATE_EVENT_FILES:
        raise HistoricalReplayControllerError("State events exceed the controller limit")
    if any(
        path.parent.is_symlink() or path.is_symlink() or not path.is_file()
        for path in paths
    ):
        raise HistoricalReplayControllerError("State events contain a non-regular file")
    return paths


def _historical_replay_states(
    events: list[dict[str, Any]], authorities: set[str]
) -> dict[str, dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for event in events:
        event_id = _match(UUID7, event.get("event_id"), "State event_id")
        _timestamp(event.get("occurred_at"), "State event occurred_at")
        if event_id in event_ids:
            raise HistoricalReplayControllerError("State events contain a duplicate event_id")
        event_ids.add(event_id)
        ordered.append(event)
    ordered.sort(key=lambda event: (event["occurred_at"], event["event_id"]))
    by_id = {event["event_id"]: event for event in ordered}
    historical_tasks: dict[str, str] = {}
    for event in ordered:
        if event.get("event_type") != "replay.enqueued":
            continue
        payload = _object(event.get("payload"), "replay.enqueued payload")
        result_id = payload.get("result_id")
        if result_id not in authorities:
            continue
        task_id = _match(REPLAY_ID, event.get("subject_id"), "historical replay task")
        if task_id in historical_tasks and historical_tasks[task_id] != result_id:
            raise HistoricalReplayControllerError("historical replay task changes result identity")
        historical_tasks[task_id] = result_id

    def parent(event: dict[str, Any], expected: set[str]) -> dict[str, Any]:
        cause_id = _match(
            UUID7,
            event.get("causation_event_id"),
            "historical replay causation_event_id",
        )
        cause = by_id.get(cause_id)
        if (
            cause is None
            or cause.get("event_type") not in expected
            or (cause["occurred_at"], cause["event_id"])
            >= (event["occurred_at"], event["event_id"])
            or cause["event_id"] >= event["event_id"]
        ):
            raise HistoricalReplayControllerError("historical replay causality is invalid")
        return cause

    states: dict[str, dict[str, Any]] = {}
    terminal_types = {
        "replay.accepted",
        "replay.rejected",
        "replay.declined",
        "replay.crashed",
        "replay.timed_out",
        "replay.failed",
    }
    for event in ordered:
        kind = event.get("event_type")
        subject = event.get("subject_id")
        payload_value = event.get("payload")
        if kind == "historical_result.replay_reconfigured":
            payload = _object(payload_value, "historical replay reconfiguration payload")
            task_id = payload.get("replay_task_id")
            if task_id not in historical_tasks:
                continue
            state = states.get(task_id)
            cause = parent(
                event,
                {
                    "historical_result.replay_reconfigured",
                    "replay.failed",
                    "replay.unavailable",
                },
            )
            if (
                state is None
                or state["event"]["event_id"] != cause["event_id"]
                or (
                    cause["event_type"] == "historical_result.replay_reconfigured"
                    and state["status"] != "reconfiguring"
                )
                or (
                    cause["event_type"] != "historical_result.replay_reconfigured"
                    and state["status"] not in {"failed", "unavailable"}
                )
                or event.get("subject_id") != historical_tasks[task_id]
            ):
                raise HistoricalReplayControllerError(
                    "historical replay reconfiguration transition is invalid"
                )
            states[task_id] = {
                "status": "reconfiguring",
                "attempt": state["attempt"],
                "event": event,
            }
            continue
        if subject not in historical_tasks:
            continue
        task_id = subject
        if kind == "replay.enqueued":
            payload = _object(payload_value, "historical replay.enqueued payload")
            previous = states.get(task_id)
            if payload.get("result_id") != historical_tasks[task_id]:
                raise HistoricalReplayControllerError(
                    "historical replay enqueue changes result identity"
                )
            cause = parent(
                event,
                {
                    "historical_result.replay_profile_qualified",
                    "historical_result.replay_reconfigured",
                },
            )
            if previous is None:
                valid = (
                    cause["event_type"] == "historical_result.replay_profile_qualified"
                    and cause.get("subject_id") == historical_tasks[task_id]
                )
                attempt = 0
            else:
                valid = (
                    cause["event_type"] == "historical_result.replay_reconfigured"
                    and previous["status"] == "reconfiguring"
                    and previous["event"]["event_id"] == cause["event_id"]
                    and cause.get("subject_id") == historical_tasks[task_id]
                    and isinstance(cause.get("payload"), dict)
                    and cause["payload"].get("replay_task_id") == task_id
                )
                attempt = previous["attempt"]
            if not valid:
                raise HistoricalReplayControllerError(
                    "historical replay enqueue or re-enqueue is invalid"
                )
            states[task_id] = {
                "status": "queued",
                "attempt": attempt,
                "event": event,
            }
            continue
        state = states.get(task_id)
        if state is None:
            raise HistoricalReplayControllerError(
                "historical replay transition precedes its enqueue"
            )
        if kind == "replay.started":
            payload = _object(payload_value, "historical replay.started payload")
            attempt = _integer(payload.get("attempt"), "historical replay attempt", 1)
            cause = parent(event, {"replay.enqueued", "replay.failed"})
            retryable = (
                state["status"] == "failed"
                and isinstance(cause.get("payload"), dict)
                and cause["payload"].get("retryable") is True
            )
            if (
                (state["status"] != "queued" and not retryable)
                or state["event"]["event_id"] != cause["event_id"]
                or cause.get("subject_id") != task_id
                or attempt != state["attempt"] + 1
            ):
                raise HistoricalReplayControllerError(
                    "historical replay.started has invalid causality or attempt"
                )
            states[task_id] = {
                "status": "running",
                "attempt": attempt,
                "event": event,
            }
        elif kind in terminal_types:
            payload = _object(payload_value, "historical replay terminal payload")
            attempt = _integer(payload.get("attempt"), "historical replay attempt", 1)
            cause = parent(event, {"replay.started"})
            if (
                state["status"] != "running"
                or state["event"]["event_id"] != cause["event_id"]
                or cause.get("subject_id") != task_id
                or attempt != state["attempt"]
            ):
                raise HistoricalReplayControllerError(
                    "historical replay terminal has invalid causality or attempt"
                )
            states[task_id] = {
                "status": kind.removeprefix("replay."),
                "attempt": attempt,
                "event": event,
            }
        elif kind == "replay.unavailable":
            cause = parent(event, {"replay.enqueued", "replay.failed"})
            if (
                state["status"] not in {"queued", "failed"}
                or state["event"]["event_id"] != cause["event_id"]
                or cause.get("subject_id") != task_id
            ):
                raise HistoricalReplayControllerError(
                    "historical replay.unavailable has invalid causality"
                )
            states[task_id] = {
                "status": "unavailable",
                "attempt": state["attempt"],
                "event": event,
            }
        elif isinstance(kind, str) and kind.startswith("replay."):
            raise HistoricalReplayControllerError(
                "historical replay transition type is not recognized"
            )
    return states


def recover_running(
    events_root: pathlib.Path,
    trusted_now: str,
    *,
    state_validated: bool,
    random_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Project recovery only after the caller completed full State validation.

    This reducer defensively rechecks the historical transition subset that it
    consumes; it is not a replacement for lean-eval-state's authoritative
    schema and materialized-view validation.
    """
    if state_validated is not True:
        raise HistoricalReplayControllerError(
            "recovery requires an authoritative State-validated event set"
        )
    events: list[dict[str, Any]] = []
    for path in _event_files(events_root):
        value, _ = _load_state_canonical(path, "State event")
        event_id = _match(UUID7, value.get("event_id"), "State event_id")
        if path.name != f"{event_id}.json" or path.parent.name != event_id[:2]:
            raise HistoricalReplayControllerError("State event path differs from event_id")
        events.append(value)
    authorities = set()
    for event in events:
        if event.get("event_type") == "historical_result.replay_authorized":
            authorities.add(
                _match(RESULT_ID, event.get("subject_id"), "historical authority result_id")
            )
    states = _historical_replay_states(events, authorities)
    running = [state["event"] for state in states.values() if state["status"] == "running"]
    if not running:
        return {"schema_version": 1, "kind": "none"}
    if len(running) != 1:
        raise HistoricalReplayControllerError("more than one historical replay is running")
    started = running[0]
    _match(UUID7, started.get("event_id"), "running historical event_id")
    _match(REPLAY_ID, started.get("subject_id"), "running historical replay_task_id")
    started_at = _parse_timestamp(started.get("occurred_at"), "running historical occurred_at")
    now = _parse_timestamp(trusted_now, "trusted_now")
    if now - started_at < RECOVERY_AFTER:
        return {
            "schema_version": 1,
            "kind": "busy",
            "replay_task_id": started["subject_id"],
        }
    payload = _object(started.get("payload"), "running historical payload")
    attempt = _integer(payload.get("attempt"), "running historical attempt", 1)
    occurred = max(now, started_at + dt.timedelta(milliseconds=1))
    return {
        "schema_version": 1,
        "kind": "failed",
        "event": {
            "schema_version": 1,
            "event_id": _causal_uuid7(occurred, started["event_id"], random_bytes),
            "event_type": "replay.failed",
            "occurred_at": occurred.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "subject_id": started["subject_id"],
            "causation_event_id": started["event_id"],
            "actor": {"kind": "system"},
            "payload": {"attempt": attempt, "reason_code": "runner_lost", "retryable": True},
        },
    }


def _git_blob(repository_root: pathlib.Path, commit: str, path: str) -> bytes:
    try:
        object_name = f"{commit}:{path}"
        size_text = subprocess.run(
            ["git", "-C", str(repository_root), "cat-file", "-s", object_name],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.strip()
        size = int(size_text)
        if not 1 <= size <= MAX_JSON_BYTES:
            raise HistoricalReplayControllerError("exact reviewed Git object exceeds its limit")
        raw = subprocess.run(
            ["git", "-C", str(repository_root), "show", object_name],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout
        if len(raw) != size:
            raise HistoricalReplayControllerError("exact reviewed Git object changed while read")
        return raw
    except HistoricalReplayControllerError:
        raise
    except ValueError as error:
        raise HistoricalReplayControllerError("exact reviewed Git object size is invalid") from error
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalReplayControllerError("exact reviewed Git object is unavailable") from error


def _is_expected_repository_remote(remote: list[str]) -> bool:
    return remote in (
        ["https://github.com/leanprover/lean-eval-submissions"],
        ["https://github.com/leanprover/lean-eval-submissions.git"],
    )


def _verify_repository_identity_and_ancestry(
    repository_root: pathlib.Path, task: dict[str, Any]
) -> None:
    try:
        remote = subprocess.run(
            ["git", "-C", str(repository_root), "config", "--get-all", "remote.origin.url"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalReplayControllerError("submissions Git identity is unavailable") from error
    if not _is_expected_repository_remote(remote):
        raise HistoricalReplayControllerError("submissions Git remote identity differs")
    for commit in (task["authority_commit"], task["qualification_commit"]):
        try:
            subprocess.run(
                ["git", "-C", str(repository_root), "merge-base", "--is-ancestor", commit, "HEAD"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise HistoricalReplayControllerError(
                "reviewed Git commit is not an ancestor of the controller checkout"
            ) from error


def _verify_commit_ancestor(repository_root: pathlib.Path, commit: str) -> None:
    try:
        subprocess.run(
            ["git", "-C", str(repository_root), "merge-base", "--is-ancestor", commit, "HEAD"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalReplayControllerError(
            "reviewed Git commit is not an ancestor of the controller checkout"
        ) from error


def _verify_image_source_ancestry(
    repository_root: pathlib.Path,
    image_source_commit: str,
    controller_source_commit: str,
) -> None:
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "cat-file",
                "-e",
                f"{image_source_commit}^{{commit}}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalReplayControllerError(
            "qualification image source commit is unavailable"
        ) from error
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "merge-base",
                "--is-ancestor",
                image_source_commit,
                controller_source_commit,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalReplayControllerError(
            "qualification image source is not an ancestor of its controller source"
        ) from error


def _verify_qualification_source_bindings(
    repository_root: pathlib.Path, qualification: dict[str, Any]
) -> None:
    controller_commit = qualification["controller_source_commit"]
    _verify_commit_ancestor(repository_root, controller_commit)
    image_source_commit = qualification["image_source_commit"]
    _verify_image_source_ancestry(
        repository_root,
        image_source_commit,
        controller_commit,
    )
    workflow = _git_blob(repository_root, controller_commit, QUALIFICATION_WORKFLOW_PATH)
    controller = _git_blob(repository_root, controller_commit, QUALIFICATION_CONTROLLER_PATH)
    contract = _git_blob(repository_root, controller_commit, QUALIFICATION_CONTRACT_PATH)
    matrix = _git_blob(
        repository_root,
        image_source_commit,
        qualification["profile_matrix_path"],
    )
    runner_contract = _git_blob(
        repository_root,
        image_source_commit,
        qualification["runner_contract_path"],
    )
    if (
        sha256_bytes(workflow) != qualification["qualification_workflow_sha256"]
        or sha256_bytes(controller) != qualification["qualification_controller_sha256"]
        or sha256_bytes(contract) != qualification["qualification_contract_sha256"]
    ):
        raise HistoricalReplayControllerError(
            "qualification controller source differs from exact reviewed Git blobs"
        )
    if (
        sha256_bytes(matrix) != qualification["profile_matrix_sha256"]
        or sha256_bytes(runner_contract) != qualification["runner_contract_sha256"]
    ):
        raise HistoricalReplayControllerError(
            "qualification image source differs from exact reviewed Git blobs"
        )


def verify_repository_bindings(
    repository_root: pathlib.Path,
    task: dict[str, Any],
    authority_raw: bytes,
    qualification_raw: bytes,
) -> None:
    _verify_repository_identity_and_ancestry(repository_root, task)
    observed_authority = _git_blob(
        repository_root, task["authority_commit"], task["authority_path"]
    )
    observed_qualification = _git_blob(
        repository_root,
        task["qualification_commit"],
        task["qualification_path"],
    )
    if observed_authority != authority_raw or observed_qualification != qualification_raw:
        raise HistoricalReplayControllerError("reviewed Git blob differs from controller input")


def load_reviewed_inputs(
    repository_root: pathlib.Path, task_value: Any
) -> tuple[
    dict[str, Any],
    bytes,
    dict[str, Any],
    bytes,
    dict[str, Any],
    bytes,
    dict[str, Any],
    bytes,
]:
    task = _validate_task(task_value, 0)
    _verify_repository_identity_and_ancestry(repository_root, task)
    authority_raw = _git_blob(
        repository_root, task["authority_commit"], task["authority_path"]
    )
    authority = _parse_canonical(authority_raw, "exact authority Git blob")
    qualification_raw = _git_blob(
        repository_root,
        task["qualification_commit"],
        task["qualification_path"],
    )
    qualification = _parse_canonical(
        qualification_raw, "exact qualification Git blob"
    )
    validate_qualification(qualification, qualification_raw)
    _verify_qualification_source_bindings(repository_root, qualification)
    matrix_raw = _git_blob(
        repository_root,
        task["qualification_commit"],
        qualification["profile_matrix_path"],
    )
    matrix = _parse_canonical(matrix_raw, "exact profile matrix Git blob")
    contract_raw = _git_blob(
        repository_root,
        task["qualification_commit"],
        qualification["runner_contract_path"],
    )
    contract = _parse_canonical(contract_raw, "exact runner contract Git blob")
    return (
        authority,
        authority_raw,
        qualification,
        qualification_raw,
        matrix,
        matrix_raw,
        contract,
        contract_raw,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--queue", required=True, type=pathlib.Path)
    plan.add_argument("--repository-root", required=True, type=pathlib.Path)
    plan.add_argument("--output", required=True, type=pathlib.Path)
    bind = commands.add_parser("bind-handoff")
    for name in ("plan", "handoff", "source-archive", "repository-root", "output"):
        bind.add_argument(f"--{name}", required=True, type=pathlib.Path)
    event = commands.add_parser("state-event")
    event.add_argument("kind", choices=("started",))
    event.add_argument("--plan", required=True, type=pathlib.Path)
    event.add_argument("--queue", required=True, type=pathlib.Path)
    event.add_argument("--trusted-now", required=True)
    event.add_argument("--output", required=True, type=pathlib.Path)
    recover = commands.add_parser("recover")
    recover.add_argument("--events-root", required=True, type=pathlib.Path)
    recover.add_argument("--state-validated", required=True, action="store_true")
    recover.add_argument("--trusted-now", required=True)
    recover.add_argument("--output", required=True, type=pathlib.Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "plan":
            queue, _ = _load_state_canonical(args.queue, "historical replay queue")
            if queue.get("tasks"):
                validated_queue = validate_queue(queue)
                selected = _next_eligible_task(validated_queue)
                if selected is None:
                    selected = validated_queue["tasks"][0]
                (
                    authority,
                    authority_raw,
                    qualification,
                    qualification_raw,
                    matrix,
                    matrix_raw,
                    contract,
                    contract_raw,
                ) = load_reviewed_inputs(args.repository_root, selected)
                planned = plan_next(
                    queue,
                    authority,
                    authority_raw,
                    qualification,
                    qualification_raw,
                    matrix,
                    matrix_raw,
                    contract,
                    contract_raw,
                )
            else:
                planned = plan_next(queue)
            _write(args.output, planned)
        elif args.command == "bind-handoff":
            plan, _ = _load_canonical(args.plan, "historical execution plan")
            handoff, _ = _load_canonical(args.handoff, "historical runner handoff")
            reviewed = load_reviewed_inputs(args.repository_root, plan.get("task"))
            matrix, contract = reviewed[4], reviewed[6]
            _write(
                args.output,
                bind_handoff(plan, handoff, args.source_archive, matrix, contract),
            )
        elif args.command == "state-event":
            plan, _ = _load_canonical(args.plan, "historical execution plan")
            queue, _ = _load_state_canonical(args.queue, "historical replay queue")
            value = started_event(plan, queue, args.trusted_now)
            _write(args.output, value, state_canonical_bytes)
        else:
            _write(
                args.output,
                recover_running(
                    args.events_root,
                    args.trusted_now,
                    state_validated=args.state_validated,
                ),
                state_canonical_bytes,
            )
    except (HistoricalReplayControllerError, HistoricalPublicRunnerError) as error:
        print(f"historical replay controller: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
