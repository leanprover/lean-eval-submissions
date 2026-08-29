#!/usr/bin/env python3
"""Freeze reviewed historical qualification evidence and prepare State inputs.

This controller is deliberately offline.  It never calls GitHub, writes State,
or enqueues replay.  The workflow surrounding it obtains read-only GitHub
metadata and artifacts; this file closes those inputs and emits create-only
review material.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib
import io
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from typing import Any

from replay_orchestrator import (
    config_digest,
    validate_execution_profile,
    validate_measurement_config,
)
from results_schema import result_id as stable_result_id

PLAN_COMMIT = "7eb77aa8c2ef7f4d598c77240ea9effbb248dce2"
PLAN_SHA256 = "d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e"
MATRIX_SHA256 = "a674707eea7a9556576c8dcbe57bcf6b4f44362d2bdfd47895fb7c783554f39c"
RUNNER_CONTRACT_SHA256 = "6d341a642dfd6aa9092228269da6761000bf0818128ce3f35cb259bd8fb2303f"
QUALIFICATION_CONTRACT_SHA256 = "937a1ce9800350de47fb2ce0c3d276b6cddc38cd39820727c8b8687bea89dad0"
STATE_COMMIT = "15a96673efd44d3b198890c1e94581b33c2a1a87"
STATE_TREE = "14cbbd50bb3b23561ed465dbf074543e03cee2a1"
STATE_EVENT_SCHEMA_SHA256 = "acbdd88fa233fe2bc64eb928a421c06521e58b113bbd3f1b90c8a8744c84395a"
STATE_HISTORICAL_QUEUE_SCHEMA_SHA256 = (
    "a3b23b21f85370161892d4adc3c4170e35f864556da4339c53b404e5477077ab"
)
STATE_VALIDATOR_SHA256 = "3f2abdfa48a00c8040519dd74307986dfede20a27d81019572df7968493c7750"
STATE_MATERIALIZER_SHA256 = "2594de45c6e5fdc1e00759bdf032f46a0dd3b2e5e92be7e989f9dd5255d4a0b1"
WORKFLOW_PATH = ".github/workflows/historical-public-image-qualification.yml"
SUBMISSIONS_REPOSITORY = "leanprover/lean-eval-submissions"
BENCHMARK_REPOSITORY = "leanprover/lean-eval"
PLAN_PATH = f"evidence/public-replay/plans/{PLAN_SHA256}.json"
MATRIX_PATH = "configuration/historical-public-replay-profile-matrix-v1.json"
RUNNER_CONTRACT_PATH = "configuration/historical-public-runner-v1.json"
QUALIFICATION_CONTRACT_PATH = "historical-public-qualification/contract-v1.json"
ROOT = pathlib.Path(__file__).parents[1]
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_ZIP_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_MEMBER_BYTES = 1024 * 1024
MAX_SAFE_INTEGER = 9_007_199_254_740_991
BATCH_PROFILE_COUNT = 35
BATCH_REQUEST_COUNT = 128
BATCH_RESULT_COUNT = 194
BATCH_EVENT_COUNT = BATCH_RESULT_COUNT * 3
PINNED_STATE_EVENT_COUNT = 440
BATCH_UUID_DOMAIN = b"lean-eval-historical-public-authority-batch-v1\0"

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
OCI_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
REQUEST_ID = re.compile(r"prr_[0-9a-f]{64}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
REPLAY_ID = re.compile(r"rt1_[0-9a-f]{64}\Z")
UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
PROFILE_PATH = re.compile(
    r"evidence/public-replay/profiles/([0-9a-f]{64})\.json\Z"
)
TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z"
)
API_TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)


class PreparationError(ValueError):
    """Qualification evidence or a requested State input is not exact."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def canonical_state_event(value: Any) -> bytes:
    """Match lean-eval-state's exact append-byte contract."""
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _regular_bytes(path: pathlib.Path, maximum: int, label: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise PreparationError(f"{label} is not one regular file")
        size = path.stat().st_size
        if not 1 <= size <= maximum:
            raise PreparationError(f"{label} exceeds its size boundary")
        return path.read_bytes()
    except OSError as error:
        raise PreparationError(f"{label} is unavailable") from error


def load_canonical(
    path: pathlib.Path,
    label: str,
    *,
    expected_sha256: str | None = None,
    maximum: int = MAX_JSON_BYTES,
) -> tuple[dict[str, Any], bytes]:
    raw = _regular_bytes(path, maximum, label)
    return parse_canonical(raw, label, expected_sha256=expected_sha256)


def parse_canonical(
    raw: bytes,
    label: str,
    *,
    expected_sha256: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    if expected_sha256 is not None and sha256_bytes(raw) != expected_sha256:
        raise PreparationError(f"{label} digest changed")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PreparationError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict) or canonical(value) != raw:
        raise PreparationError(f"{label} is not canonical JSON")
    return value, raw


def load_external(path: pathlib.Path, label: str) -> dict[str, Any]:
    raw = _regular_bytes(path, MAX_JSON_BYTES, label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PreparationError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise PreparationError(f"{label} is not an object")
    return value


def closed(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PreparationError(f"{label} fields changed")
    return value


def match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PreparationError(f"{label} is invalid")
    return value


def integer(value: Any, label: str, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= MAX_SAFE_INTEGER:
        raise PreparationError(f"{label} is invalid")
    return value


def digest_file(path: pathlib.Path, label: str, maximum: int = MAX_JSON_BYTES) -> str:
    return sha256_bytes(_regular_bytes(path, maximum, label))


def write_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise PreparationError(f"output parent is not one real directory: {path.parent}")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(canonical(value))
    except OSError as error:
        raise PreparationError(f"refusing to overwrite output: {path}") from error


def create_output_root(path: pathlib.Path) -> None:
    parent = path.parent
    if path.name in {"", ".", ".."} or parent.is_symlink() or not parent.is_dir():
        raise PreparationError("output directory parent is not one real directory")
    try:
        parent_fd = os.open(
            parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
    except OSError as error:
        raise PreparationError("refusing to overwrite preparation directory") from error


def write_relative(
    root: pathlib.Path,
    relative: str,
    value: dict[str, Any],
    *,
    state_event: bool = False,
) -> None:
    path = pathlib.PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise PreparationError("output path is not a safe relative path")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(root, flags)
        try:
            for component in path.parts[:-1]:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            file_descriptor = os.open(
                path.parts[-1],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=descriptor,
            )
            with os.fdopen(file_descriptor, "wb") as output:
                output.write(
                    canonical_state_event(value) if state_event else canonical(value)
                )
        finally:
            os.close(descriptor)
    except OSError as error:
        raise PreparationError(f"refusing unsafe or existing output: {relative}") from error


def _git(root: pathlib.Path, *arguments: str, maximum: int = MAX_JSON_BYTES) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreparationError("exact Git checkout proof failed") from error
    if len(result.stdout) > maximum:
        raise PreparationError("exact Git checkout proof exceeded its size boundary")
    return result.stdout


def _git_optional_blob(root: pathlib.Path, commit: str, relative: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{relative}"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise PreparationError("exact Git blob proof failed") from error
    if result.returncode != 0:
        return None
    if len(result.stdout) > MAX_JSON_BYTES:
        raise PreparationError("exact Git blob exceeds its size boundary")
    return result.stdout


def verify_checkout(
    root: pathlib.Path,
    repository: str,
    commit: str,
    tree: str | None = None,
    *,
    label: str,
) -> None:
    expected_remote = f"https://github.com/{repository}.git"
    remote = _git(root, "remote", "get-url", "origin", maximum=4096).decode().strip()
    if remote != expected_remote:
        raise PreparationError(f"exact {label} Git checkout remote changed")
    head = _git(root, "rev-parse", "HEAD^{commit}", maximum=64).decode().strip()
    if head != commit:
        raise PreparationError(f"exact {label} Git checkout commit changed")
    if tree is not None:
        actual_tree = _git(root, "rev-parse", "HEAD^{tree}", maximum=64).decode().strip()
        if actual_tree != tree:
            raise PreparationError(f"exact {label} Git checkout tree changed")
    if _git(root, "status", "--porcelain", maximum=4096) != b"":
        raise PreparationError(f"exact {label} Git checkout cleanliness changed")


def verify_qualification_blob(
    root: pathlib.Path, commit: str, relative: str, expected: bytes
) -> None:
    verify_checkout(root, SUBMISSIONS_REPOSITORY, commit, label="qualification source")
    blob = _git(root, "show", f"{commit}:{relative}", maximum=MAX_JSON_BYTES)
    if blob != expected:
        raise PreparationError("qualification commit does not contain the exact profile blob")


def verify_source_blob(
    root: pathlib.Path, commit: str, relative: str, expected: bytes, label: str
) -> None:
    blob = _git_optional_blob(root, commit, relative)
    if blob != expected:
        raise PreparationError(f"{label} is not the exact commit blob")


def load_artifact_zip(
    path: pathlib.Path,
    expected_sha256: str,
    expected_size: int,
    expected_names: list[str],
    label: str,
) -> dict[str, tuple[dict[str, Any], bytes]]:
    raw = _regular_bytes(path, MAX_ARTIFACT_ZIP_BYTES, label)
    if len(raw) != expected_size or sha256_bytes(raw) != expected_sha256:
        raise PreparationError(f"{label} archive digest does not match GitHub metadata")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise PreparationError(f"{label} is not one valid ZIP archive") from error
    if sorted(entry.filename for entry in entries) != sorted(expected_names):
        raise PreparationError(f"{label} member set changed")
    if len({entry.filename for entry in entries}) != len(entries):
        raise PreparationError(f"{label} contains duplicate members")
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
            or entry.file_size < 1
            or entry.file_size > MAX_ARTIFACT_MEMBER_BYTES
            or entry.compress_size > MAX_ARTIFACT_MEMBER_BYTES
            or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            or (mode != 0 and not stat.S_ISREG(mode))
        ):
            raise PreparationError(f"{label} contains an unsafe member")
        total += entry.file_size
        if total > MAX_ARTIFACT_MEMBER_BYTES:
            raise PreparationError(f"{label} expanded content is too large")
        try:
            member_raw = archive.read(entry)
            value = json.loads(member_raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as error:
            raise PreparationError(f"{label} member is not readable JSON") from error
        if not isinstance(value, dict) or canonical(value) != member_raw:
            raise PreparationError(f"{label} member is not canonical JSON")
        output[entry.filename] = (value, member_raw)
    archive.close()
    return output


def load_and_validate_pinned_state(
    root: pathlib.Path, candidates: list[dict[str, Any]]
) -> tuple[str, dict[str, Any]]:
    try:
        import jsonschema
    except ImportError as error:
        raise PreparationError("pinned JSON Schema dependencies are required") from error
    verify_checkout(
        root,
        "leanprover/lean-eval-state",
        STATE_COMMIT,
        STATE_TREE,
        label="State source",
    )
    expected_files = {
        "schema/state-event-v1.schema.json": STATE_EVENT_SCHEMA_SHA256,
        "schema/historical-public-replay-queue-v2.schema.json": STATE_HISTORICAL_QUEUE_SCHEMA_SHA256,
        "scripts/validate_state.py": STATE_VALIDATOR_SHA256,
        "scripts/materialize_state.py": STATE_MATERIALIZER_SHA256,
    }
    exact_blobs: dict[str, bytes] = {}
    for relative, expected in expected_files.items():
        blob = _git_optional_blob(root, STATE_COMMIT, relative)
        if blob is None or sha256_bytes(blob) != expected:
            raise PreparationError(f"pinned State component changed: {relative}")
        exact_blobs[relative] = blob
    state_document = _git_optional_blob(root, STATE_COMMIT, "state.json")
    if state_document != b'{\n  "environment": "production",\n  "schema_version": 1\n}\n':
        raise PreparationError("pinned State environment changed")
    script_paths = _git(
        root, "ls-tree", "-r", "--name-only", STATE_COMMIT, "scripts", maximum=64 * 1024
    ).decode().splitlines()
    event_paths = _git(
        root, "ls-tree", "-r", "--name-only", STATE_COMMIT, "events", maximum=1024 * 1024
    ).decode().splitlines()
    if not script_paths or not event_paths:
        raise PreparationError("pinned State object inventory is empty")
    existing = []
    for relative in event_paths:
        if re.fullmatch(r"events/[0-9a-f]{2}/[0-9a-f-]{36}\.json", relative) is None:
            raise PreparationError("pinned State event path is not canonical")
        raw = _git_optional_blob(root, STATE_COMMIT, relative)
        if raw is None or len(raw) > MAX_JSON_BYTES:
            raise PreparationError("pinned State event blob is unavailable")
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise PreparationError("pinned State event blob is invalid") from error
        if not isinstance(event, dict):
            raise PreparationError("pinned State event blob is not an object")
        state_canonical = (
            json.dumps(event, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode()
        if state_canonical != raw:
            raise PreparationError("pinned State event blob is not canonical")
        if relative != f"events/{event['event_id'].replace('-', '')[:2]}/{event['event_id']}.json":
            raise PreparationError("pinned State event path and identity differ")
        existing.append(event)
    module_names = (
        "validate_state", "materialize_state", "result_effective_identities",
        "model_identity", "result_amendments", "result_owner_indexes",
        "result_release_status",
    )
    with tempfile.TemporaryDirectory(prefix="historical-state-contract-") as directory:
        scripts_root = pathlib.Path(directory)
        for relative in script_paths:
            if re.fullmatch(r"scripts/[A-Za-z0-9_]+\.py", relative) is None:
                continue
            blob = _git_optional_blob(root, STATE_COMMIT, relative)
            if blob is None or len(blob) > MAX_JSON_BYTES:
                raise PreparationError("pinned State script blob is unavailable")
            (scripts_root / pathlib.PurePosixPath(relative).name).write_bytes(blob)
        scripts = str(scripts_root)
        for module in module_names:
            sys.modules.pop(module, None)
        sys.path.insert(0, scripts)
        try:
            state_validator = importlib.import_module("validate_state")
            state_materializer = importlib.import_module("materialize_state")
            for index, event in enumerate(existing):
                state_validator.validate_event_data(event, f"pinned[{index}]")
            state_validator.validate_semantics(existing, "production")
            for index, event in enumerate(candidates):
                state_validator.validate_event_data(event, f"candidate[{index}]")
            combined = [*existing, *candidates]
            state_validator.validate_semantics(combined, "production")
            views = state_materializer.materialize("production", combined)
            try:
                queue_schema = json.loads(
                    exact_blobs[
                        "schema/historical-public-replay-queue-v2.schema.json"
                    ].decode("utf-8")
                )
                jsonschema.Draft202012Validator(queue_schema).validate(
                    views["historical-public-replay-queue.json"]
                )
            except (UnicodeError, json.JSONDecodeError, jsonschema.ValidationError) as error:
                raise PreparationError(
                    "materialized historical queue fails its exact pinned schema"
                ) from error
        except Exception as error:
            if error.__class__.__module__ in {"validate_state", "materialize_state"}:
                raise PreparationError("candidate append fails pinned State validation") from error
            raise
        finally:
            sys.path.remove(scripts)
            for module in module_names:
                sys.modules.pop(module, None)
    latest = max(event["occurred_at"] for event in existing)
    return latest, views["historical-public-replay-queue.json"]


def uuid7_timestamp_ms(event_id: str) -> int:
    return int(event_id.replace("-", "")[:12], 16)


def timestamp_ms(value: str) -> int:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PreparationError("event timestamp is not a real UTC time") from error
    if parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") != value:
        raise PreparationError("event timestamp is not canonical UTC milliseconds")
    return int(parsed.timestamp() * 1000)


def validate_finalization_inputs(
    preparation: dict[str, Any], profile: dict[str, Any], profile_raw: bytes
) -> None:
    try:
        import jsonschema
        from referencing import Registry, Resource
    except ImportError as error:
        raise PreparationError("pinned JSON Schema dependencies are required") from error
    schema_paths = (
        ROOT / "schemas/replay-execution-profile-v1.schema.json",
        ROOT / "schemas/historical-public-profile-qualification-v1.schema.json",
        ROOT / "schemas/historical-public-authority-preparation-v2.schema.json",
    )
    schemas = []
    for path in schema_paths:
        raw = _regular_bytes(path, 1024 * 1024, f"schema {path.name}")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise PreparationError(f"schema {path.name} is invalid") from error
        if not isinstance(value, dict):
            raise PreparationError(f"schema {path.name} is not an object")
        schemas.append(value)
    registry = Registry().with_resources(
        [(schema["$id"], Resource.from_contents(schema)) for schema in schemas]
    )
    try:
        jsonschema.Draft202012Validator(schemas[1], registry=registry).validate(profile)
        jsonschema.Draft202012Validator(schemas[2], registry=registry).validate(preparation)
    except jsonschema.ValidationError as error:
        raise PreparationError("profile or preparation does not match its closed schema") from error
    state = preparation["state_contract"]
    if state != {
        "repository": "leanprover/lean-eval-state",
        "commit": STATE_COMMIT,
        "tree": STATE_TREE,
        "event_schema_sha256": STATE_EVENT_SCHEMA_SHA256,
        "historical_queue_schema_sha256": STATE_HISTORICAL_QUEUE_SCHEMA_SHA256,
        "validator_sha256": STATE_VALIDATOR_SHA256,
        "materializer_sha256": STATE_MATERIALIZER_SHA256,
    }:
        raise PreparationError("preparation State contract changed")
    binding = preparation["qualification_profile"]
    selection = preparation["selection"]
    authority_payload = preparation["authority_event_payload"]
    qualification_payload = preparation["profile_qualification_payload_without_commit"]
    enqueue = preparation["ordinary_replay_enqueue"]
    profile_digest = profile["execution_profile_digest"]
    measurement_digest = profile["measurement_config_digest"]
    expected_profile_path = f"evidence/public-replay/profiles/{profile_digest}.json"
    derived_result = stable_result_id(
        authority_payload["owner_login"],
        authority_payload["declared_model"],
        authority_payload["problem_id"],
        authority_payload["statement_revision"],
    )
    if (
        sha256_bytes(profile_raw) != binding["sha256"]
        or binding["path"] != expected_profile_path
        or binding["execution_profile_digest"] != profile_digest
        or binding["measurement_config_digest"] != measurement_digest
        or profile["plan_path"] != PLAN_PATH
        or profile["plan_sha256"] != PLAN_SHA256
        or profile["plan_commit"] != authority_payload["authority_commit"]
        or authority_payload["authority_path"] != PLAN_PATH
        or authority_payload["authority_sha256"] != PLAN_SHA256
        or authority_payload["authority_repository"] != SUBMISSIONS_REPOSITORY
        or authority_payload["source_visibility"] != "public"
        or authority_payload["results_path"]
        != f"results/{authority_payload['owner_login']}.json"
        or selection["result_id"] != derived_result
        or selection["benchmark_commit"] != authority_payload["benchmark_commit"]
        or profile["benchmark_commit"] != selection["benchmark_commit"]
        or profile["execution_profile"]["toolchain"] != authority_payload["toolchain"]
        or profile["registry_manifest_digest"]
        != profile["execution_profile"]["vm_image_digest"]
        or config_digest(
            "lean-eval-replay-execution-profile-v1", profile["execution_profile"]
        )
        != profile_digest
        or config_digest(
            "lean-eval-replay-measurement-config-v1", profile["measurement_config"]
        )
        != measurement_digest
        or qualification_payload != {
            "toolchain": authority_payload["toolchain"],
            "benchmark_commit": authority_payload["benchmark_commit"],
            "measurement_config_digest": measurement_digest,
            "execution_profile_digest": profile_digest,
            "checker": "nanoda",
            "qualification_repository": SUBMISSIONS_REPOSITORY,
            "qualification_path": expected_profile_path,
            "qualification_sha256": binding["sha256"],
        }
        or enqueue["payload"] != {
            "result_id": selection["result_id"],
            "measurement_config_digest": measurement_digest,
            "execution_profile_digest": profile_digest,
            "checker": "nanoda",
            "benchmark_commit": authority_payload["benchmark_commit"],
        }
        or enqueue["replay_task_id"] != replay_task_id(selection["result_id"], measurement_digest)
    ):
        raise PreparationError("profile and preparation cross-field binding changed")
    validate_execution_profile(profile["execution_profile"])
    validate_measurement_config(profile["measurement_config"])


def write_provenance(args: argparse.Namespace) -> None:
    run = load_external(pathlib.Path(args.run_metadata), "workflow run metadata")
    candidate = load_external(
        pathlib.Path(args.candidate_artifact_metadata), "candidate artifact metadata"
    )
    staging = load_external(
        pathlib.Path(args.staging_artifact_metadata), "staging artifact metadata"
    )
    controller_commit = match(COMMIT, args.controller_source_commit, "controller commit")
    image_commit = match(COMMIT, args.image_source_commit, "image commit")
    run_id = integer(args.run_id, "workflow run id")
    run_attempt = integer(args.run_attempt, "workflow run attempt")
    run_created_at = match(API_TIMESTAMP, run.get("created_at"), "workflow creation time")
    started_at = match(API_TIMESTAMP, run.get("run_started_at"), "workflow attempt start time")
    completed_at = match(API_TIMESTAMP, run.get("updated_at"), "workflow completion time")
    if (
        run.get("id") != run_id
        or run.get("run_attempt") != run_attempt
        or run.get("event") != "workflow_dispatch"
        or run.get("head_sha") != controller_commit
        or run.get("head_branch") != f"lean-eval-dispatch/{controller_commit}"
        or run.get("path") != WORKFLOW_PATH
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or not run_created_at <= started_at <= completed_at
    ):
        raise PreparationError("workflow run is not the exact successful qualification run")

    def artifact(value: dict[str, Any], artifact_id: int, name: str) -> dict[str, Any]:
        workflow_run = value.get("workflow_run")
        digest = value.get("digest")
        artifact_created_at = value.get("created_at")
        size = value.get("size_in_bytes")
        if (
            value.get("id") != artifact_id
            or value.get("name") != name
            or value.get("expired") is not False
            or not isinstance(workflow_run, dict)
            or workflow_run.get("id") != run_id
            or workflow_run.get("head_sha") != controller_commit
            or workflow_run.get("head_branch") != f"lean-eval-dispatch/{controller_commit}"
            or not isinstance(digest, str)
            or OCI_DIGEST.fullmatch(digest) is None
            or not isinstance(artifact_created_at, str)
            or API_TIMESTAMP.fullmatch(artifact_created_at) is None
            or not run_created_at <= artifact_created_at <= completed_at
            or type(size) is not int
            or not 1 <= size <= MAX_ARTIFACT_ZIP_BYTES
        ):
            raise PreparationError(f"{name} metadata does not bind the successful run")
        return {
            "artifact_id": artifact_id,
            "archive_sha256": digest[7:],
            "created_at": artifact_created_at,
            "name": name,
            "size_in_bytes": size,
        }

    candidate_binding = artifact(
        candidate, integer(args.candidate_artifact_id, "candidate artifact id"),
        "historical-public-image-candidate",
    )
    staging_binding = artifact(
        staging, integer(args.staging_artifact_id, "staging artifact id"),
        "historical-public-staging-qualification",
    )
    output = {
        "schema_version": 2,
        "kind": "historical_public_qualification_artifact_provenance",
        "repository": SUBMISSIONS_REPOSITORY,
        "workflow_path": WORKFLOW_PATH,
        "workflow_run_id": run_id,
        "workflow_run_attempt": run_attempt,
        "workflow_event": "workflow_dispatch",
        "workflow_conclusion": "success",
        "workflow_run_created_at": run_created_at,
        "workflow_run_started_at": started_at,
        "workflow_run_completed_at": completed_at,
        "dispatch_ref": f"lean-eval-dispatch/{controller_commit}",
        "controller_source_commit": controller_commit,
        "image_source_commit": image_commit,
        "artifacts": [candidate_binding, staging_binding],
    }
    write_exclusive(pathlib.Path(args.output), output)


def validate_provenance(value: Any) -> dict[str, Any]:
    result = closed(
        value,
        {
            "schema_version", "kind", "repository", "workflow_path",
            "workflow_run_id", "workflow_run_attempt", "workflow_event",
            "workflow_conclusion", "dispatch_ref", "controller_source_commit",
            "image_source_commit", "workflow_run_created_at",
            "workflow_run_started_at",
            "workflow_run_completed_at", "artifacts",
        },
        "qualification provenance",
    )
    controller = match(COMMIT, result["controller_source_commit"], "controller commit")
    match(COMMIT, result["image_source_commit"], "image commit")
    if (
        result["schema_version"] != 2
        or result["kind"] != "historical_public_qualification_artifact_provenance"
        or result["repository"] != SUBMISSIONS_REPOSITORY
        or result["workflow_path"] != WORKFLOW_PATH
        or result["workflow_event"] != "workflow_dispatch"
        or result["workflow_conclusion"] != "success"
        or result["dispatch_ref"] != f"lean-eval-dispatch/{controller}"
    ):
        raise PreparationError("qualification provenance identity changed")
    integer(result["workflow_run_id"], "workflow run id")
    integer(result["workflow_run_attempt"], "workflow run attempt")
    run_created_at = match(
        API_TIMESTAMP, result["workflow_run_created_at"], "workflow creation time"
    )
    started_at = match(API_TIMESTAMP, result["workflow_run_started_at"], "workflow start time")
    completed_at = match(
        API_TIMESTAMP, result["workflow_run_completed_at"], "workflow completion time"
    )
    if not run_created_at <= started_at <= completed_at:
        raise PreparationError("qualification workflow time window is invalid")
    artifacts = result["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise PreparationError("qualification provenance must bind two artifacts")
    names = []
    for index, item in enumerate(artifacts):
        item = closed(
            item,
            {"artifact_id", "archive_sha256", "created_at", "name", "size_in_bytes"},
            f"artifact {index}",
        )
        integer(item["artifact_id"], f"artifact {index} id")
        match(DIGEST, item["archive_sha256"], f"artifact {index} archive digest")
        artifact_created_at = match(
            API_TIMESTAMP, item["created_at"], f"artifact {index} creation time"
        )
        integer(item["size_in_bytes"], f"artifact {index} size")
        if not run_created_at <= artifact_created_at <= completed_at:
            raise PreparationError(f"artifact {index} is not from the exact workflow run window")
        if item["size_in_bytes"] > MAX_ARTIFACT_ZIP_BYTES:
            raise PreparationError(f"artifact {index} exceeds its ZIP size boundary")
        names.append(item["name"])
    if names != [
        "historical-public-image-candidate",
        "historical-public-staging-qualification",
    ]:
        raise PreparationError("qualification artifact identities changed")
    return result


def _find_selection(
    plan: dict[str, Any], matrix: dict[str, Any], request_id: str, result_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    match(REQUEST_ID, request_id, "request id")
    match(RESULT_ID, result_id, "result id")
    requests = [item for item in plan.get("requests", []) if item.get("request_id") == request_id]
    if len(requests) != 1:
        raise PreparationError("request does not select one plan entry")
    request = requests[0]
    results = [item for item in request.get("results", []) if item.get("result_id") == result_id]
    if len(results) != 1:
        raise PreparationError("result does not select one request result")
    benchmark_commit = request.get("benchmark", {}).get("commit")
    entries = [
        item for item in matrix.get("images", [])
        if item.get("benchmark_commit") == benchmark_commit
    ]
    if len(entries) != 1:
        raise PreparationError("benchmark does not select one matrix entry")
    entry = entries[0]
    if (
        plan.get("schema_version") != 1
        or plan.get("kind") != "historical_public_replay_plan"
        or plan.get("activation_status") != "blocked"
        or plan.get("execution_profile_status") != "unresolved"
        or matrix.get("schema_version") != 1
        or matrix.get("qualification_status") != "unqualified"
        or matrix.get("plan_sha256") != PLAN_SHA256
        or entry.get("qualification_status") != "unqualified"
        or request["benchmark"]["repository"] != BENCHMARK_REPOSITORY
        or request["benchmark"]["toolchain"] != entry.get("toolchain")
        or results[0]["problem_id"] not in entry.get("problem_ids", [])
    ):
        raise PreparationError("plan, matrix, and selected result binding changed")
    return request, results[0], entry


def _validate_candidate(
    value: Any, provenance: dict[str, Any], entry: dict[str, Any], contract: dict[str, Any]
) -> dict[str, Any]:
    candidate = closed(
        value,
        {
            "schema_version", "benchmark_commit", "controller_source_commit",
            "image_source_commit", "qualification_status", "vars",
        },
        "candidate binding",
    )
    variables = closed(
        candidate["vars"],
        {
            "DEPLOYED_COMMIT", "DEPLOYMENT_ENVIRONMENT", "GITHUB_OIDC_AUDIENCE",
            "GITHUB_OIDC_ENVIRONMENT", "PRODUCTION_MEMORY_GATE_BYTES", "REPLAY_ENABLED",
            "REVIEWED_EXECUTION_PROFILE_DIGEST", "REVIEWED_MEASUREMENT_CONFIG_DIGEST",
            "REVIEWED_VM_IMAGE_DIGEST", "SANDBOX_TRANSPORT",
            "STAGING_ACCEPTANCE_ENABLED", "STAGING_MEMORY_LIMIT_BYTES",
        },
        "candidate variables",
    )
    controller = provenance["controller_source_commit"]
    image = provenance["image_source_commit"]
    expected = {
        "DEPLOYED_COMMIT": controller,
        "DEPLOYMENT_ENVIRONMENT": "staging",
        "GITHUB_OIDC_AUDIENCE": "lean-eval-historical-public-qualification-staging",
        "GITHUB_OIDC_ENVIRONMENT": "replay-staging",
        "PRODUCTION_MEMORY_GATE_BYTES": str(contract["memory_limit_bytes"]),
        "REPLAY_ENABLED": "false",
        "REVIEWED_EXECUTION_PROFILE_DIGEST": "0" * 64,
        "REVIEWED_MEASUREMENT_CONFIG_DIGEST": "0" * 64,
        "REVIEWED_VM_IMAGE_DIGEST": variables["REVIEWED_VM_IMAGE_DIGEST"],
        "SANDBOX_TRANSPORT": "rpc",
        "STAGING_ACCEPTANCE_ENABLED": "true",
        "STAGING_MEMORY_LIMIT_BYTES": str(contract["memory_limit_bytes"]),
    }
    if (
        candidate["schema_version"] != 2
        or candidate["benchmark_commit"] != entry["benchmark_commit"]
        or candidate["controller_source_commit"] != controller
        or candidate["image_source_commit"] != image
        or candidate["qualification_status"] != "unqualified"
        or variables != expected
    ):
        raise PreparationError("candidate binding is not the closed v2 seam")
    match(OCI_DIGEST, variables["REVIEWED_VM_IMAGE_DIGEST"], "candidate manifest digest")
    return candidate


def _validate_publication(
    value: Any,
    provenance: dict[str, Any],
    entry: dict[str, Any],
    candidate: dict[str, Any],
    image_source_root: pathlib.Path,
) -> dict[str, Any]:
    publication = closed(
        value,
        {
            "schema_version", "kind", "qualification_status",
            "controller_source_commit", "image_source_commit", "benchmark_commit",
            "benchmark_tree", "registry_repository", "registry_tag",
            "registry_manifest_digest", "publication_mode", "image_size_bytes",
            "dockerfile_sha256", "layer_preparation_sha256", "layer_diff_ids",
            "matrix_sha256", "matrix_entry_sha256", "profile_lock_sha256",
            "workspace_manifest_count", "workflow_image_limit_bytes",
        },
        "image publication evidence",
    )
    controller = provenance["controller_source_commit"]
    image = provenance["image_source_commit"]
    expected_tag = f"{entry['benchmark_commit']}-{image}"
    manifest = candidate["vars"]["REVIEWED_VM_IMAGE_DIGEST"]
    if (
        publication["schema_version"] != 2
        or publication["kind"] != "historical_public_image_publication_evidence"
        or publication["qualification_status"] != "unqualified"
        or publication["controller_source_commit"] != controller
        or publication["image_source_commit"] != image
        or publication["benchmark_commit"] != entry["benchmark_commit"]
        or publication["benchmark_tree"] != entry["benchmark_tree"]
        or publication["registry_repository"] != "lean-eval-historical-public-v1"
        or publication["registry_tag"] != expected_tag
        or publication["registry_manifest_digest"] != manifest
        or publication["publication_mode"] not in {"created", "resumed"}
        or publication["matrix_sha256"] != MATRIX_SHA256
        or publication["matrix_entry_sha256"] != sha256_bytes(canonical(entry))
        or publication["profile_lock_sha256"]
        != sha256_bytes(canonical(entry["profile_lock"]))
        or publication["workspace_manifest_count"] != entry["workspace_count"]
        or publication["workflow_image_limit_bytes"] != 18_000_000_000
    ):
        raise PreparationError("image publication evidence differs from its locked image")
    verify_checkout(image_source_root, SUBMISSIONS_REPOSITORY, image, label="image source")
    dockerfile = _git_optional_blob(
        image_source_root, image, "Dockerfile.historical-public-replay"
    )
    if dockerfile is None or sha256_bytes(dockerfile) != publication["dockerfile_sha256"]:
        raise PreparationError("image publication Dockerfile is not from image source")
    helper = _git_optional_blob(
        image_source_root, image, "scripts/prepare_historical_image_layers.py"
    )
    if publication["layer_preparation_sha256"] is None:
        if helper is not None:
            raise PreparationError("legacy image unexpectedly has a layer preparation helper")
    elif (
        match(DIGEST, publication["layer_preparation_sha256"], "layer helper digest")
        != (None if helper is None else sha256_bytes(helper))
    ):
        raise PreparationError("image layer helper is not from image source")
    if publication["publication_mode"] == "created":
        integer(publication["image_size_bytes"], "image size")
        layers = publication["layer_diff_ids"]
        if not isinstance(layers, list) or not layers:
            raise PreparationError("created image must bind layer diff IDs")
        for layer in layers:
            match(OCI_DIGEST, layer, "layer diff ID")
    elif publication["image_size_bytes"] is not None or publication["layer_diff_ids"] is not None:
        raise PreparationError("resumed publication must not fabricate build-local metrics")
    return publication


def _validate_rollout(
    value: Any,
    entry: dict[str, Any],
    publication: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    rollout = closed(
        value,
        {
            "schema_version", "kind", "qualification_status", "name", "version",
            "max_instances", "image_repository", "image_tag", "image_manifest_digest",
            "runtime_boundary", "health",
        },
        "qualification rollout",
    )
    boundary = closed(
        rollout["runtime_boundary"], {"vcpu", "memory_mib", "disk_size_mb", "network", "ssh"},
        "rollout runtime boundary",
    )
    health = closed(rollout["health"], {"errors", "instances"}, "rollout health")
    instances = closed(
        health["instances"], {"healthy", "failed", "starting", "scheduling"},
        "rollout instances",
    )
    if (
        rollout["schema_version"] != 2
        or rollout["kind"] != "historical_public_qualification_rollout"
        or rollout["qualification_status"] != "unqualified"
        or rollout["name"] != contract["container_application"]
        or type(rollout["version"]) is not int
        or rollout["version"] < 1
        or rollout["max_instances"] != 1
        or rollout["image_repository"] != publication["registry_repository"]
        or rollout["image_tag"] != publication["registry_tag"]
        or rollout["image_manifest_digest"] != publication["registry_manifest_digest"]
        or boundary != {
            "vcpu": 4,
            "memory_mib": 12 * 1024,
            "disk_size_mb": 20_000,
            "network": {"assign_ipv4": "none", "assign_ipv6": "none", "mode": "private"},
            "ssh": {"enabled": False},
        }
        or health["errors"] != []
        or type(instances["healthy"]) is not int
        or instances["healthy"] < 1
        or any(instances[field] != 0 for field in ("failed", "starting", "scheduling"))
    ):
        raise PreparationError("qualification rollout is not the exact healthy v2 boundary")
    return rollout


def _validate_staging(
    value: Any,
    provenance: dict[str, Any],
    entry: dict[str, Any],
    publication: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    staging = closed(
        value,
        {
            "schema_version", "kind", "qualification_status", "benchmark_commit",
            "controller_source_commit", "image_source_commit", "registry_manifest_digest",
            "health", "runtime_boundary", "probes",
        },
        "staging qualification evidence",
    )
    if (
        staging["schema_version"] != 2
        or staging["kind"] != "historical_public_staging_qualification_evidence"
        or staging["qualification_status"] != "unqualified"
        or staging["benchmark_commit"] != entry["benchmark_commit"]
        or staging["controller_source_commit"] != provenance["controller_source_commit"]
        or staging["image_source_commit"] != provenance["image_source_commit"]
        or staging["registry_manifest_digest"] != publication["registry_manifest_digest"]
        or staging["runtime_boundary"] != {
            "vcpu": 4,
            "memory_mib": 12 * 1024,
            "disk_size_mb": 20_000,
            "network": {"assign_ipv4": "none", "assign_ipv6": "none", "mode": "private"},
            "ssh": {"enabled": False},
        }
    ):
        raise PreparationError("staging qualification evidence is not the exact v2 seam")
    health = closed(
        staging["health"],
        {
            "status", "service", "environment", "deployed_commit", "replay_enabled",
            "staging_acceptance_enabled", "staging_memory_limit_bytes",
            "production_memory_gate_bytes", "reviewed_execution_profile_digest",
            "reviewed_measurement_config_digest", "reviewed_vm_image_digest",
        },
        "staging health",
    )
    if health != {
        "status": "ok",
        "service": "lean-eval-replay-executor",
        "environment": "staging",
        "deployed_commit": provenance["controller_source_commit"],
        "replay_enabled": False,
        "staging_acceptance_enabled": True,
        "staging_memory_limit_bytes": contract["memory_limit_bytes"],
        "production_memory_gate_bytes": contract["memory_limit_bytes"],
        "reviewed_execution_profile_digest": "0" * 64,
        "reviewed_measurement_config_digest": "0" * 64,
        "reviewed_vm_image_digest": publication["registry_manifest_digest"],
    }:
        raise PreparationError("staging health does not remain replay-disabled")
    probes = staging["probes"]
    if not isinstance(probes, list) or len(probes) != 2:
        raise PreparationError("exactly two successful destruction probes are required")
    request_ids: set[str] = set()
    nonce: str | None = None
    hardware: tuple[str, str] | None = None
    probe_fields = {
        "schema_version", "service", "environment", "request_id", "runner_nonce",
        "archive_ciphertext_sha256", "marker_sha256", "network_policy", "network_probe",
        "destruction", "architecture", "kernel_release", "cpu_model",
        "staging_memory_limit_bytes", "production_memory_gate_bytes",
    }
    for index, item in enumerate(probes):
        probe = closed(item, probe_fields, f"staging probe {index}")
        match(UUID7, probe["request_id"], f"staging probe {index} request")
        match(DIGEST, probe["runner_nonce"], f"staging probe {index} nonce")
        match(DIGEST, probe["archive_ciphertext_sha256"], f"staging probe {index} archive")
        match(DIGEST, probe["marker_sha256"], f"staging probe {index} marker")
        pair = (probe["kernel_release"], probe["cpu_model"])
        if (
            probe["schema_version"] != 1
            or probe["service"] != "lean-eval-replay-executor"
            or probe["environment"] != "staging"
            or probe["network_policy"] != "disabled"
            or probe["network_probe"] != "blocked"
            or probe["destruction"] != "confirmed"
            or probe["architecture"] != "x86_64"
            or probe["staging_memory_limit_bytes"] != contract["memory_limit_bytes"]
            or probe["production_memory_gate_bytes"] != contract["memory_limit_bytes"]
            or not all(isinstance(text, str) and 0 < len(text.encode()) <= 256 for text in pair)
            or (nonce is not None and probe["runner_nonce"] != nonce)
            or (hardware is not None and pair != hardware)
        ):
            raise PreparationError("staging probes do not bind one reproducible runtime")
        nonce, hardware = probe["runner_nonce"], pair
        request_ids.add(probe["request_id"])
    if len(request_ids) != 2:
        raise PreparationError("staging destruction probes are not distinct")
    return staging


def replay_task_id(result_id: str, measurement_digest: str) -> str:
    raw = f"lean-eval-replay-task-v1\0{result_id}\0{measurement_digest}".encode()
    return "rt1_" + sha256_bytes(raw)


def authority_event_payload(
    request: dict[str, Any], result: dict[str, Any], plan_commit: str
) -> dict[str, Any]:
    return {
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
        "source_kind": request["source"]["kind"],
        "source_repository": request["source"]["repository"],
        "source_commit": request["source"]["commit"],
        "source_visibility": "public",
        "benchmark_repository": request["benchmark"]["repository"],
        "benchmark_commit": request["benchmark"]["commit"],
        "toolchain": request["benchmark"]["toolchain"],
        "lean_toolchain_blob_sha256": request["benchmark"][
            "lean_toolchain_blob_sha256"
        ],
        "workflow_run_identity_sha256": request["historical_evaluation"][
            "workflow_run_identity_sha256"
        ],
        "authority_repository": SUBMISSIONS_REPOSITORY,
        "authority_commit": plan_commit,
        "authority_path": PLAN_PATH,
        "authority_sha256": PLAN_SHA256,
    }


def profile_qualification_payload(
    profile: dict[str, Any], profile_raw: bytes, qualification_commit: str | None
) -> dict[str, Any]:
    digest = profile["execution_profile_digest"]
    payload = {
        "toolchain": profile["execution_profile"]["toolchain"],
        "benchmark_commit": profile["benchmark_commit"],
        "measurement_config_digest": profile["measurement_config_digest"],
        "execution_profile_digest": digest,
        "checker": "nanoda",
        "qualification_repository": SUBMISSIONS_REPOSITORY,
        "qualification_path": f"evidence/public-replay/profiles/{digest}.json",
        "qualification_sha256": sha256_bytes(profile_raw),
    }
    if qualification_commit is not None:
        payload["qualification_commit"] = qualification_commit
    return payload


def replay_enqueue_payload(
    result_id: str, profile: dict[str, Any]
) -> dict[str, Any]:
    return {
        "result_id": result_id,
        "measurement_config_digest": profile["measurement_config_digest"],
        "execution_profile_digest": profile["execution_profile_digest"],
        "checker": "nanoda",
        "benchmark_commit": profile["benchmark_commit"],
    }


def prepare(args: argparse.Namespace) -> None:
    plan_commit = match(COMMIT, args.plan_commit, "plan commit")
    preparation_source_root = pathlib.Path(args.preparation_source_root)
    verify_checkout(
        preparation_source_root,
        SUBMISSIONS_REPOSITORY,
        plan_commit,
        label="preparation source",
    )
    plan, _ = load_canonical(pathlib.Path(args.plan), "historical replay plan", expected_sha256=PLAN_SHA256)
    matrix, _ = load_canonical(pathlib.Path(args.matrix), "profile matrix", expected_sha256=MATRIX_SHA256)
    runner_contract, _ = load_canonical(
        pathlib.Path(args.runner_contract), "runner contract", expected_sha256=RUNNER_CONTRACT_SHA256
    )
    qualification_contract, _ = load_canonical(
        pathlib.Path(args.qualification_contract), "qualification contract",
        expected_sha256=QUALIFICATION_CONTRACT_SHA256,
    )
    provenance, provenance_raw = load_canonical(pathlib.Path(args.provenance), "qualification provenance")
    validate_provenance(provenance)
    for relative, path in (
        (PLAN_PATH, pathlib.Path(args.plan)),
        (MATRIX_PATH, pathlib.Path(args.matrix)),
        (RUNNER_CONTRACT_PATH, pathlib.Path(args.runner_contract)),
        (QUALIFICATION_CONTRACT_PATH, pathlib.Path(args.qualification_contract)),
    ):
        verify_source_blob(
            preparation_source_root,
            plan_commit,
            relative,
            _regular_bytes(path, MAX_JSON_BYTES, relative),
            relative,
        )
    controller_workflow = _git_optional_blob(
        preparation_source_root, provenance["controller_source_commit"], WORKFLOW_PATH
    )
    controller_program = _git_optional_blob(
        preparation_source_root,
        provenance["controller_source_commit"],
        "historical-public-qualification/qualification.py",
    )
    if controller_workflow is None or controller_program is None:
        raise PreparationError("qualification controller source blobs are unavailable")
    request, result, entry = _find_selection(plan, matrix, args.request_id, args.result_id)
    if entry["benchmark_commit"] != args.benchmark_commit:
        raise PreparationError("selected benchmark commit changed")
    if (
        runner_contract.get("memory_limit_bytes") != 12 * 1024**3
        or runner_contract.get("wall_time_limit_ms") != 19_800_000
        or runner_contract.get("measurement_command") != ["/opt/lean-eval/replay-measure"]
        or qualification_contract.get("memory_limit_bytes") != runner_contract["memory_limit_bytes"]
        or qualification_contract.get("network") != "disabled"
        or qualification_contract.get("replay_enabled") is not False
    ):
        raise PreparationError("runner and qualification contracts no longer agree")

    candidate_archive = load_artifact_zip(
        pathlib.Path(args.candidate_artifact_zip),
        provenance["artifacts"][0]["archive_sha256"],
        provenance["artifacts"][0]["size_in_bytes"],
        [
            "candidate-binding.json", "historical-image-publication.json",
            "historical-qualification-rollout.json",
        ],
        "candidate artifact",
    )
    staging_archive = load_artifact_zip(
        pathlib.Path(args.staging_artifact_zip),
        provenance["artifacts"][1]["archive_sha256"],
        provenance["artifacts"][1]["size_in_bytes"],
        ["historical-public-staging-qualification.json"],
        "staging artifact",
    )
    candidate, candidate_raw = candidate_archive["candidate-binding.json"]
    publication, publication_raw = candidate_archive["historical-image-publication.json"]
    rollout, rollout_raw = candidate_archive["historical-qualification-rollout.json"]
    staging, staging_raw = staging_archive["historical-public-staging-qualification.json"]
    candidate = _validate_candidate(candidate, provenance, entry, qualification_contract)
    publication = _validate_publication(
        publication, provenance, entry, candidate, pathlib.Path(args.image_source_root)
    )
    _validate_rollout(rollout, entry, publication, qualification_contract)
    staging = _validate_staging(staging, provenance, entry, publication, qualification_contract)

    profile_lock = entry["profile_lock"]
    probe = staging["probes"][0]
    execution_profile = {
        "schema_version": 1,
        "runner_profile": profile_lock["runner_profile"],
        "vm_image_digest": publication["registry_manifest_digest"],
        "toolchain": profile_lock["toolchain"],
        "go_toolchain": profile_lock["go_toolchain"],
        "rust_toolchain": profile_lock["rust_toolchain"],
        "cpu_model": probe["cpu_model"],
        "architecture": probe["architecture"],
        "kernel_release": probe["kernel_release"],
        "cache_state": profile_lock["cache_state"],
        "measurement_command": profile_lock["measurement_command"],
        "components": profile_lock["components"],
    }
    validate_execution_profile(execution_profile)
    execution_profile_digest = config_digest(
        "lean-eval-replay-execution-profile-v1", execution_profile
    )
    measurement_config = {
        "schema_version": 1,
        "wall_time_limit_ms": runner_contract["wall_time_limit_ms"],
        "memory_limit_bytes": runner_contract["memory_limit_bytes"],
        "retired_instructions": {"perf_event": "instructions:u", "required": False},
    }
    validate_measurement_config(measurement_config)
    measurement_digest = config_digest(
        "lean-eval-replay-measurement-config-v1", measurement_config
    )
    file_digests = {
        "candidate-binding.json": sha256_bytes(candidate_raw),
        "historical-image-publication.json": sha256_bytes(publication_raw),
        "historical-qualification-rollout.json": sha256_bytes(rollout_raw),
        "historical-public-staging-qualification.json": sha256_bytes(staging_raw),
    }
    profile = {
        "schema_version": 1,
        "kind": "historical_public_replay_profile_qualification",
        "qualification_status": "qualified",
        "benchmark_repository": BENCHMARK_REPOSITORY,
        "benchmark_commit": entry["benchmark_commit"],
        "benchmark_tree": entry["benchmark_tree"],
        "plan_repository": SUBMISSIONS_REPOSITORY,
        "plan_commit": args.plan_commit,
        "plan_path": PLAN_PATH,
        "plan_sha256": PLAN_SHA256,
        "profile_matrix_path": MATRIX_PATH,
        "profile_matrix_sha256": MATRIX_SHA256,
        "runner_contract_path": RUNNER_CONTRACT_PATH,
        "runner_contract_sha256": RUNNER_CONTRACT_SHA256,
        "qualification_contract_path": QUALIFICATION_CONTRACT_PATH,
        "qualification_contract_sha256": QUALIFICATION_CONTRACT_SHA256,
        "workflow_repository": SUBMISSIONS_REPOSITORY,
        "workflow_path": WORKFLOW_PATH,
        "workflow_run_id": provenance["workflow_run_id"],
        "workflow_run_attempt": provenance["workflow_run_attempt"],
        "controller_source_commit": provenance["controller_source_commit"],
        "image_source_commit": provenance["image_source_commit"],
        "qualification_workflow_sha256": sha256_bytes(controller_workflow),
        "qualification_controller_sha256": sha256_bytes(controller_program),
        "artifact_provenance_sha256": sha256_bytes(provenance_raw),
        "artifact_archive_bindings": provenance["artifacts"],
        "artifact_file_sha256": file_digests,
        "registry_repository": publication["registry_repository"],
        "registry_tag": publication["registry_tag"],
        "registry_manifest_digest": publication["registry_manifest_digest"],
        "execution_profile": execution_profile,
        "execution_profile_digest": execution_profile_digest,
        "measurement_config": measurement_config,
        "measurement_config_digest": measurement_digest,
    }
    profile_raw = canonical(profile)
    profile_sha256 = sha256_bytes(profile_raw)
    profile_path = f"evidence/public-replay/profiles/{execution_profile_digest}.json"
    authority_payload = authority_event_payload(request, result, args.plan_commit)
    qualification_without_commit = profile_qualification_payload(
        profile, profile_raw, None
    )
    enqueue_payload = replay_enqueue_payload(result["result_id"], profile)
    task_id = replay_task_id(result["result_id"], measurement_digest)
    preparation = {
        "schema_version": 2,
        "kind": "historical_public_authority_preparation",
        "activation_status": "blocked",
        "activation_blockers": [
            "review_and_commit_exact_qualification_profile",
            "supply_exact_qualification_commit",
            "supply_fresh_ordered_state_event_metadata",
            "validate_candidate_append_against_current_production_state",
            "separately_authorize_state_append_and_replay_enqueue",
        ],
        "state_contract": {
            "repository": "leanprover/lean-eval-state",
            "commit": STATE_COMMIT,
            "tree": STATE_TREE,
            "event_schema_sha256": STATE_EVENT_SCHEMA_SHA256,
            "historical_queue_schema_sha256": STATE_HISTORICAL_QUEUE_SCHEMA_SHA256,
            "validator_sha256": STATE_VALIDATOR_SHA256,
            "materializer_sha256": STATE_MATERIALIZER_SHA256,
        },
        "selection": {
            "request_id": request["request_id"],
            "result_id": result["result_id"],
            "benchmark_commit": entry["benchmark_commit"],
        },
        "qualification_profile": {
            "repository": SUBMISSIONS_REPOSITORY,
            "path": profile_path,
            "sha256": profile_sha256,
            "execution_profile_digest": execution_profile_digest,
            "measurement_config_digest": measurement_digest,
        },
        "authority_event_payload": authority_payload,
        "profile_qualification_payload_without_commit": qualification_without_commit,
        "qualification_commit_requirement": (
            "exact commit containing the byte-identical qualification profile at its digest-derived path"
        ),
        "ordinary_replay_enqueue": {
            "replay_task_id": task_id,
            "payload": enqueue_payload,
        },
    }
    output_root = pathlib.Path(args.output_directory)
    create_output_root(output_root)
    write_relative(output_root, profile_path, profile)
    write_relative(output_root, "historical-public-authority-preparation.json", preparation)


def finalize(args: argparse.Namespace) -> None:
    preparation, _ = load_canonical(pathlib.Path(args.preparation), "authority preparation")
    profile, profile_raw = load_canonical(pathlib.Path(args.profile), "qualification profile")
    validate_finalization_inputs(preparation, profile, profile_raw)
    binding = preparation["qualification_profile"]
    qualification_commit = match(COMMIT, args.qualification_commit, "qualification commit")
    verify_qualification_blob(
        pathlib.Path(args.qualification_repository_root),
        qualification_commit,
        binding["path"],
        profile_raw,
    )
    ids = [args.authority_event_id, args.qualification_event_id, args.enqueue_event_id]
    times = [args.authority_occurred_at, args.qualification_occurred_at, args.enqueue_occurred_at]
    for index, event_id in enumerate(ids):
        match(UUID7, event_id, f"event {index} id")
    for index, occurred_at in enumerate(times):
        match(TIMESTAMP, occurred_at, f"event {index} timestamp")
    if len(set(ids)) != 3 or ids != sorted(ids) or times != sorted(times) or len(set(times)) != 3:
        raise PreparationError("State event IDs and timestamps must be distinct and increasing")
    for index, (event_id, occurred_at) in enumerate(zip(ids, times, strict=True)):
        if uuid7_timestamp_ms(event_id) != timestamp_ms(occurred_at):
            raise PreparationError(f"event {index} UUIDv7 timestamp does not match occurred_at")
    result_id = preparation["selection"]["result_id"]
    replay_id = preparation["ordinary_replay_enqueue"]["replay_task_id"]
    match(RESULT_ID, result_id, "prepared result id")
    match(REPLAY_ID, replay_id, "prepared replay id")
    authority = {
        "schema_version": 1,
        "event_id": ids[0],
        "event_type": "historical_result.replay_authorized",
        "occurred_at": times[0],
        "subject_id": result_id,
        "causation_event_id": None,
        "actor": {"kind": "system"},
        "payload": preparation["authority_event_payload"],
    }
    qualification_payload = {
        **preparation["profile_qualification_payload_without_commit"],
        "qualification_commit": qualification_commit,
    }
    qualification = {
        "schema_version": 1,
        "event_id": ids[1],
        "event_type": "historical_result.replay_profile_qualified",
        "occurred_at": times[1],
        "subject_id": result_id,
        "causation_event_id": ids[0],
        "actor": {"kind": "system"},
        "payload": qualification_payload,
    }
    enqueue = {
        "schema_version": 1,
        "event_id": ids[2],
        "event_type": "replay.enqueued",
        "occurred_at": times[2],
        "subject_id": replay_id,
        "causation_event_id": ids[1],
        "actor": {"kind": "system"},
        "payload": preparation["ordinary_replay_enqueue"]["payload"],
    }
    latest_state_time, queue = load_and_validate_pinned_state(
        pathlib.Path(args.state_root), [authority, qualification, enqueue]
    )
    if times[0] <= latest_state_time:
        raise PreparationError("candidate events do not follow the pinned State time window")
    tasks = [task for task in queue.get("tasks", []) if task.get("replay_task_id") == replay_id]
    if (
        len(tasks) != 1
        or tasks[0].get("result_id") != result_id
        or tasks[0].get("qualification_event_id") != ids[1]
        or tasks[0].get("event_id") != ids[2]
        or tasks[0].get("status") != "queued"
        or tasks[0].get("attempt") != 0
    ):
        raise PreparationError("pinned State did not materialize one exact queued task")
    output_root = pathlib.Path(args.output_directory)
    create_output_root(output_root)
    for event in (authority, qualification, enqueue):
        event_id = event["event_id"]
        write_relative(
            output_root,
            f"events/{event_id.replace('-', '')[:2]}/{event_id}.json",
            event,
            state_event=True,
        )
    manifest = {
        "schema_version": 1,
        "kind": "historical_public_state_append_candidate",
        "activation_status": "blocked_pending_external_state_validation_and_append_authorization",
        "state_contract": preparation["state_contract"],
        "qualification_commit": qualification_commit,
        "event_ids": ids,
        "replay_task_id": replay_id,
        "pinned_state_latest_occurred_at": latest_state_time,
        "materialized_task_sha256": sha256_bytes(canonical(tasks[0])),
    }
    write_relative(output_root, "historical-public-state-append-candidate.json", manifest)


def load_batch_inputs(
    repository_root: pathlib.Path, qualification_commit: str
) -> tuple[
    dict[str, Any],
    dict[str, tuple[dict[str, Any], bytes, str]],
    list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
]:
    verify_checkout(
        repository_root,
        SUBMISSIONS_REPOSITORY,
        qualification_commit,
        label="qualification source",
    )
    _git(
        repository_root,
        "merge-base",
        "--is-ancestor",
        PLAN_COMMIT,
        qualification_commit,
        maximum=1,
    )
    plan_raw = _git_optional_blob(repository_root, PLAN_COMMIT, PLAN_PATH)
    matrix_raw = _git_optional_blob(repository_root, PLAN_COMMIT, MATRIX_PATH)
    if plan_raw is None or matrix_raw is None:
        raise PreparationError("final plan or profile matrix Git blob is unavailable")
    plan, _ = parse_canonical(
        plan_raw, "final historical replay plan", expected_sha256=PLAN_SHA256
    )
    matrix, _ = parse_canonical(
        matrix_raw, "final historical profile matrix", expected_sha256=MATRIX_SHA256
    )
    requests = plan.get("requests")
    images = matrix.get("images")
    if (
        plan.get("resolved_request_count") != BATCH_REQUEST_COUNT
        or plan.get("resolved_result_count") != BATCH_RESULT_COUNT
        or not isinstance(requests, list)
        or len(requests) != BATCH_REQUEST_COUNT
        or matrix.get("image_count") != BATCH_PROFILE_COUNT
        or matrix.get("request_count") != BATCH_REQUEST_COUNT
        or matrix.get("result_count") != BATCH_RESULT_COUNT
        or not isinstance(images, list)
        or len(images) != BATCH_PROFILE_COUNT
    ):
        raise PreparationError("final plan or matrix counts changed")
    selections = []
    result_ids = []
    for request in requests:
        for result in request.get("results", []):
            selection = _find_selection(
                plan, matrix, request["request_id"], result["result_id"]
            )
            if result["result_id"] != stable_result_id(
                request["owner_login"],
                request["declared_model"],
                result["problem_id"],
                result["statement_revision"],
            ):
                raise PreparationError("final historical result identity changed")
            selections.append(selection)
            result_ids.append(result["result_id"])
    if (
        len(selections) != BATCH_RESULT_COUNT
        or len(set(result_ids)) != BATCH_RESULT_COUNT
    ):
        raise PreparationError("final plan does not contain exactly 194 unique Results")
    entries = {entry["benchmark_commit"]: entry for entry in matrix["images"]}
    profile_paths = _git(
        repository_root,
        "ls-tree",
        "-r",
        "--name-only",
        qualification_commit,
        "evidence/public-replay/profiles",
        maximum=1024 * 1024,
    ).decode().splitlines()
    profiles: dict[str, tuple[dict[str, Any], bytes, str]] = {}
    try:
        from historical_replay_controller import HistoricalReplayControllerError, validate_qualification
    except ImportError as error:
        raise PreparationError("historical qualification validator is unavailable") from error
    for relative in profile_paths:
        path_match = PROFILE_PATH.fullmatch(relative)
        if path_match is None:
            raise PreparationError("qualification profile Git path is not canonical")
        raw = _git_optional_blob(repository_root, qualification_commit, relative)
        if raw is None:
            raise PreparationError("qualification profile Git blob is unavailable")
        profile, _ = parse_canonical(raw, f"qualification profile {relative}")
        if (
            profile.get("plan_sha256") != PLAN_SHA256
            or profile.get("profile_matrix_sha256") != MATRIX_SHA256
        ):
            continue
        try:
            validate_qualification(profile, raw)
        except HistoricalReplayControllerError as error:
            raise PreparationError("current qualification profile is invalid") from error
        benchmark_commit = profile["benchmark_commit"]
        if benchmark_commit not in entries:
            raise PreparationError("qualification profile benchmark is not in the matrix")
        if (
            path_match.group(1) != profile["execution_profile_digest"]
            or profile["plan_commit"] != PLAN_COMMIT
            or profile["plan_path"] != PLAN_PATH
            or profile["runner_contract_sha256"] != RUNNER_CONTRACT_SHA256
            or profile["qualification_contract_sha256"]
            != QUALIFICATION_CONTRACT_SHA256
            or benchmark_commit in profiles
        ):
            raise PreparationError("qualification profile identity binding changed")
        profiles[benchmark_commit] = (profile, raw, relative)
    if len(profiles) != BATCH_PROFILE_COUNT or set(profiles) != set(entries):
        raise PreparationError("qualification commit does not contain exactly 35 current profiles")
    return matrix, profiles, selections


def deterministic_batch_uuid7(
    timestamp: int, seed: str, result_id: str, event_type: str
) -> str:
    if not 0 <= timestamp < 2**48:
        raise PreparationError("batch event timestamp exceeds UUIDv7 range")
    identity = (
        BATCH_UUID_DOMAIN
        + bytes.fromhex(seed)
        + b"\0"
        + result_id.encode()
        + b"\0"
        + event_type.encode()
    )
    random_bits = int.from_bytes(hashlib.sha256(identity).digest()[:10], "big") & (
        2**74 - 1
    )
    random_a = random_bits >> 62
    random_b = random_bits & (2**62 - 1)
    value = (
        (timestamp << 80)
        | (0x7 << 76)
        | (random_a << 64)
        | (0b10 << 62)
        | random_b
    )
    encoded = f"{value:032x}"
    return (
        f"{encoded[:8]}-{encoded[8:12]}-{encoded[12:16]}-"
        f"{encoded[16:20]}-{encoded[20:]}"
    )


def timestamp_from_ms(value: int) -> str:
    try:
        timestamp = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(
            milliseconds=value
        )
    except (OverflowError, ValueError) as error:
        raise PreparationError("batch event timestamp is outside canonical UTC") from error
    return timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_batch_events(
    matrix: dict[str, Any],
    selections: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
    profiles: dict[str, tuple[dict[str, Any], bytes, str]],
    qualification_commit: str,
    first_occurred_at: str,
    event_id_seed: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        from historical_replay_controller import (
            HistoricalReplayControllerError,
            _validate_selected_matrix_entry,
        )
    except ImportError as error:
        raise PreparationError("historical matrix validator is unavailable") from error
    match(TIMESTAMP, first_occurred_at, "first batch event timestamp")
    match(DIGEST, event_id_seed, "batch event id seed")
    first_timestamp = timestamp_ms(first_occurred_at)
    events: list[dict[str, Any]] = []
    expected_tasks: list[dict[str, Any]] = []
    for request, result, entry in selections:
        profile, profile_raw, _ = profiles[entry["benchmark_commit"]]
        result_id = result["result_id"]
        authority_payload = authority_event_payload(request, result, PLAN_COMMIT)
        try:
            _validate_selected_matrix_entry(authority_payload, profile, matrix)
        except HistoricalReplayControllerError as error:
            raise PreparationError(
                "historical matrix and qualification profile binding changed"
            ) from error
        qualification_payload = profile_qualification_payload(
            profile, profile_raw, qualification_commit
        )
        enqueue_payload = replay_enqueue_payload(result_id, profile)
        replay_id = replay_task_id(
            result_id, profile["measurement_config_digest"]
        )
        chain: list[dict[str, Any]] = []
        for event_type, subject_id, payload in (
            ("historical_result.replay_authorized", result_id, authority_payload),
            (
                "historical_result.replay_profile_qualified",
                result_id,
                qualification_payload,
            ),
            ("replay.enqueued", replay_id, enqueue_payload),
        ):
            event_timestamp = first_timestamp + len(events)
            occurred_at = timestamp_from_ms(event_timestamp)
            event_id = deterministic_batch_uuid7(
                event_timestamp, event_id_seed, result_id, event_type
            )
            event = {
                "schema_version": 1,
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "subject_id": subject_id,
                "causation_event_id": None if not chain else chain[-1]["event_id"],
                "actor": {"kind": "system"},
                "payload": payload,
            }
            chain.append(event)
            events.append(event)
        expected_tasks.append(
            {
                "replay_task_id": replay_id,
                **enqueue_payload,
                **authority_payload,
                "authority_event_id": chain[0]["event_id"],
                "authorized_at": chain[0]["occurred_at"],
                **qualification_payload,
                "qualification_event_id": chain[1]["event_id"],
                "qualified_at": chain[1]["occurred_at"],
                "status": "queued",
                "attempt": 0,
                "event_id": chain[2]["event_id"],
                "occurred_at": chain[2]["occurred_at"],
            }
        )
    ids = [event["event_id"] for event in events]
    times = [event["occurred_at"] for event in events]
    if (
        len(events) != BATCH_EVENT_COUNT
        or len(ids) != len(set(ids))
        or ids != sorted(ids)
        or times != sorted(times)
        or len(times) != len(set(times))
        or any(
            uuid7_timestamp_ms(event_id) != timestamp_ms(occurred_at)
            for event_id, occurred_at in zip(ids, times, strict=True)
        )
    ):
        raise PreparationError("batch State event identities are not exact and increasing")
    expected_tasks.sort(key=lambda task: task["replay_task_id"])
    if (
        len(expected_tasks) != BATCH_RESULT_COUNT
        or len({task["replay_task_id"] for task in expected_tasks})
        != BATCH_RESULT_COUNT
    ):
        raise PreparationError("batch replay task identities are not exactly unique")
    return events, expected_tasks


def batch_task_content(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove only State-assigned event identities from materialized tasks."""
    state_fields = {
        "authority_event_id",
        "authorized_at",
        "qualification_event_id",
        "qualified_at",
        "event_id",
        "occurred_at",
    }
    if any(not state_fields.issubset(task) for task in tasks):
        raise PreparationError("materialized task lacks State-assigned identity")
    return [
        {key: value for key, value in task.items() if key not in state_fields}
        for task in tasks
    ]


def finalize_batch(args: argparse.Namespace) -> None:
    qualification_commit = match(
        COMMIT, args.qualification_commit, "qualification commit"
    )
    repository_root = pathlib.Path(args.qualification_repository_root)
    matrix, profiles, selections = load_batch_inputs(
        repository_root, qualification_commit
    )
    events, expected_tasks = build_batch_events(
        matrix,
        selections,
        profiles,
        qualification_commit,
        args.first_occurred_at,
        args.event_id_seed,
    )
    latest_state_time, queue = load_and_validate_pinned_state(
        pathlib.Path(args.state_root), events
    )
    if events[0]["occurred_at"] <= latest_state_time:
        raise PreparationError("candidate events do not follow the pinned State time window")
    if (
        queue.get("schema_version") != 2
        or queue.get("environment") != "production"
        or queue.get("source_event_count")
        != PINNED_STATE_EVENT_COUNT + BATCH_EVENT_COUNT
        or queue.get("tasks") != expected_tasks
    ):
        raise PreparationError(
            "pinned State did not materialize exactly 194 queued historical tasks"
        )
    event_files = []
    for event in events:
        event_id = event["event_id"]
        relative = f"events/{event_id.replace('-', '')[:2]}/{event_id}.json"
        event_files.append(
            {
                "path": relative,
                "sha256": sha256_bytes(canonical_state_event(event)),
            }
        )
    profile_files = [
        {"path": relative, "sha256": sha256_bytes(raw)}
        for _, raw, relative in sorted(profiles.values(), key=lambda item: item[2])
    ]
    output_root = pathlib.Path(args.output_directory)
    create_output_root(output_root)
    for event, descriptor in zip(events, event_files, strict=True):
        write_relative(
            output_root,
            descriptor["path"],
            event,
            state_event=True,
        )
    manifest = {
        "schema_version": 1,
        "kind": "historical_public_state_append_batch_candidate",
        "activation_status": (
            "blocked_pending_external_state_validation_and_append_authorization"
        ),
        "state_contract": {
            "repository": "leanprover/lean-eval-state",
            "commit": STATE_COMMIT,
            "tree": STATE_TREE,
            "event_schema_sha256": STATE_EVENT_SCHEMA_SHA256,
            "historical_queue_schema_sha256": STATE_HISTORICAL_QUEUE_SCHEMA_SHA256,
            "validator_sha256": STATE_VALIDATOR_SHA256,
            "materializer_sha256": STATE_MATERIALIZER_SHA256,
        },
        "qualification_repository": SUBMISSIONS_REPOSITORY,
        "qualification_commit": qualification_commit,
        "plan_commit": PLAN_COMMIT,
        "plan_path": PLAN_PATH,
        "plan_sha256": PLAN_SHA256,
        "profile_matrix_path": MATRIX_PATH,
        "profile_matrix_sha256": MATRIX_SHA256,
        "profile_count": BATCH_PROFILE_COUNT,
        "request_count": BATCH_REQUEST_COUNT,
        "result_count": BATCH_RESULT_COUNT,
        "event_count": BATCH_EVENT_COUNT,
        "authority_event_count": BATCH_RESULT_COUNT,
        "qualification_event_count": BATCH_RESULT_COUNT,
        "enqueue_event_count": BATCH_RESULT_COUNT,
        "replay_task_count": BATCH_RESULT_COUNT,
        "event_id_seed": args.event_id_seed,
        "first_event_id": events[0]["event_id"],
        "last_event_id": events[-1]["event_id"],
        "first_occurred_at": events[0]["occurred_at"],
        "last_occurred_at": events[-1]["occurred_at"],
        "pinned_state_latest_occurred_at": latest_state_time,
        "profile_set_sha256": sha256_bytes(canonical(profile_files)),
        "event_set_sha256": sha256_bytes(canonical(event_files)),
        "materialized_task_content_set_sha256": sha256_bytes(
            canonical(batch_task_content(expected_tasks))
        ),
        "materialized_task_set_sha256": sha256_bytes(canonical(expected_tasks)),
    }
    write_relative(
        output_root,
        "historical-public-state-append-batch-candidate.json",
        manifest,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    provenance = commands.add_parser("provenance")
    provenance.add_argument("--run-metadata", required=True)
    provenance.add_argument("--candidate-artifact-metadata", required=True)
    provenance.add_argument("--staging-artifact-metadata", required=True)
    provenance.add_argument("--run-id", required=True, type=int)
    provenance.add_argument("--run-attempt", required=True, type=int)
    provenance.add_argument("--candidate-artifact-id", required=True, type=int)
    provenance.add_argument("--staging-artifact-id", required=True, type=int)
    provenance.add_argument("--controller-source-commit", required=True)
    provenance.add_argument("--image-source-commit", required=True)
    provenance.add_argument("--output", required=True)

    prepare_command = commands.add_parser("prepare")
    prepare_command.add_argument("--plan", required=True)
    prepare_command.add_argument("--matrix", required=True)
    prepare_command.add_argument("--runner-contract", required=True)
    prepare_command.add_argument("--qualification-contract", required=True)
    prepare_command.add_argument("--provenance", required=True)
    prepare_command.add_argument("--candidate-artifact-zip", required=True)
    prepare_command.add_argument("--staging-artifact-zip", required=True)
    prepare_command.add_argument("--preparation-source-root", required=True)
    prepare_command.add_argument("--image-source-root", required=True)
    prepare_command.add_argument("--plan-commit", required=True)
    prepare_command.add_argument("--benchmark-commit", required=True)
    prepare_command.add_argument("--request-id", required=True)
    prepare_command.add_argument("--result-id", required=True)
    prepare_command.add_argument("--output-directory", required=True)

    finalize_command = commands.add_parser("finalize")
    finalize_command.add_argument("--preparation", required=True)
    finalize_command.add_argument("--profile", required=True)
    finalize_command.add_argument("--qualification-commit", required=True)
    finalize_command.add_argument("--qualification-repository-root", required=True)
    finalize_command.add_argument("--state-root", required=True)
    finalize_command.add_argument("--authority-event-id", required=True)
    finalize_command.add_argument("--authority-occurred-at", required=True)
    finalize_command.add_argument("--qualification-event-id", required=True)
    finalize_command.add_argument("--qualification-occurred-at", required=True)
    finalize_command.add_argument("--enqueue-event-id", required=True)
    finalize_command.add_argument("--enqueue-occurred-at", required=True)
    finalize_command.add_argument("--output-directory", required=True)

    batch_command = commands.add_parser("finalize-batch")
    batch_command.add_argument("--qualification-commit", required=True)
    batch_command.add_argument("--qualification-repository-root", required=True)
    batch_command.add_argument("--state-root", required=True)
    batch_command.add_argument("--first-occurred-at", required=True)
    batch_command.add_argument("--event-id-seed", required=True)
    batch_command.add_argument("--output-directory", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "provenance":
        write_provenance(args)
    elif args.command == "prepare":
        prepare(args)
    elif args.command == "finalize":
        finalize(args)
    elif args.command == "finalize-batch":
        finalize_batch(args)
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PreparationError, KeyError, TypeError, ValueError) as error:
        print(f"historical authority preparation: {error}", file=sys.stderr)
        raise SystemExit(1) from None
