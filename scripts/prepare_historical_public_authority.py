#!/usr/bin/env python3
"""Prepare packet-bound State inputs from frozen historical public profiles.

This retained controller is deliberately offline. It validates the committed
profile set and exact State contract, then emits create-only batch review
material. It cannot build or qualify images, deploy a Worker, write State, or
enqueue replay.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

from replay_orchestrator import replay_task_id
from results_schema import result_id as stable_result_id

PLAN_COMMIT = "7eb77aa8c2ef7f4d598c77240ea9effbb248dce2"
PLAN_SHA256 = "d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e"
MATRIX_SHA256 = "a674707eea7a9556576c8dcbe57bcf6b4f44362d2bdfd47895fb7c783554f39c"
RUNNER_CONTRACT_SHA256 = "6d341a642dfd6aa9092228269da6761000bf0818128ce3f35cb259bd8fb2303f"
QUALIFICATION_CONTRACT_SHA256 = "937a1ce9800350de47fb2ce0c3d276b6cddc38cd39820727c8b8687bea89dad0"
STATE_COMMIT = "0c943edde8a247b8670e10339b80fc65be6c0f33"
STATE_TREE = "0ba2090d9c43e0d51fb08272efbd12a3efb490e9"
STATE_EVENT_SCHEMA_SHA256 = "2d19515da1b0798f00dd3e9809c3a2770fee8b27ce6323ac9b9e827db4c7ea27"
STATE_HISTORICAL_QUEUE_SCHEMA_SHA256 = (
    "a3b23b21f85370161892d4adc3c4170e35f864556da4339c53b404e5477077ab"
)
STATE_VALIDATOR_SHA256 = "d36222c071054c2bf925d081141c1f1dc4fca0c65ec686e5438b2eb02a131ed2"
STATE_MATERIALIZER_SHA256 = "5c437c12f1b3c24f9cd9d5a9da3f876fddc4f55e126cee74bf213723984719e7"
SUBMISSIONS_REPOSITORY = "leanprover/lean-eval-submissions"
BENCHMARK_REPOSITORY = "leanprover/lean-eval"
PLAN_PATH = f"evidence/public-replay/plans/{PLAN_SHA256}.json"
MATRIX_PATH = "configuration/historical-public-replay-profile-matrix-v1.json"
MAX_JSON_BYTES = 16 * 1024 * 1024
BATCH_PROFILE_COUNT = 35
SOURCE_PLAN_REQUEST_COUNT = 128
SOURCE_PLAN_RESULT_COUNT = 194
BATCH_REQUEST_COUNT = 120
BATCH_RESULT_COUNT = 174
BATCH_TERMINAL_EXCLUSION_COUNT = 20
BATCH_SELECTED_PROFILE_COUNT = 34
BATCH_EVENT_COUNT = BATCH_RESULT_COUNT * 3
PINNED_STATE_EVENT_COUNT = 489
PINNED_PUBLIC_UNAVAILABLE_COUNT = 459
BATCH_PROFILE_SET_SHA256 = (
    "d44e73c7ae58adf806a3b5147e9aa1dbfe700a53fa9482f16c2aea3127e04e2e"
)
BATCH_SELECTED_PROFILE_SET_SHA256 = (
    "03c1ad7bf4f5ac2c353db91df4647116f334bc812ce04238e9f84eabcabde8cd"
)
BATCH_TERMINAL_EXCLUSION_SET_SHA256 = (
    "4030cda13036869e451c57a6af921f811ec9495d551f5dd8ef5fcfa809a0c882"
)
BATCH_SELECTION_SET_SHA256 = (
    "a8451701c516c6d521d3c002aef48988a205e3e774700ec58a728332cbfe6b2a"
)
BATCH_TASK_CONTENT_SET_SHA256 = (
    "e8bffbc3afd93be21d51f58754e3435788fc0aae2d8109346950256d0f9cba81"
)
BATCH_UUID_DOMAIN = b"lean-eval-historical-public-authority-batch-v1\0"

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_ID = re.compile(r"prr_[0-9a-f]{64}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
PROFILE_PATH = re.compile(
    r"evidence/public-replay/profiles/([0-9a-f]{64})\.json\Z"
)
TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z"
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


def match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PreparationError(f"{label} is invalid")
    return value


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


def load_and_validate_pinned_state(
    root: pathlib.Path, candidates: list[dict[str, Any]]
) -> tuple[str, dict[str, Any], tuple[str, ...]]:
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
            terminal_public_results = tuple(
                sorted(
                    event["subject_id"]
                    for event in existing
                    if event["event_type"]
                    == "historical_result.replay_unavailable"
                )
            )
            if (
                len(existing) != PINNED_STATE_EVENT_COUNT
                or len(terminal_public_results) != PINNED_PUBLIC_UNAVAILABLE_COUNT
                or len(set(terminal_public_results))
                != PINNED_PUBLIC_UNAVAILABLE_COUNT
            ):
                raise PreparationError(
                    "pinned State terminal public disposition set changed"
                )
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
    return (
        latest,
        views["historical-public-replay-queue.json"],
        terminal_public_results,
    )


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


def _find_selection(
    plan: dict[str, Any], matrix: dict[str, Any], request_id: str, result_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    match(REQUEST_ID, request_id, "request id")
    match(RESULT_ID, result_id, "result id")
    requests = [
        item
        for item in plan.get("requests", [])
        if item.get("request_id") == request_id
    ]
    if len(requests) != 1:
        raise PreparationError("request does not select one plan entry")
    request = requests[0]
    results = [
        item
        for item in request.get("results", [])
        if item.get("result_id") == result_id
    ]
    if len(results) != 1:
        raise PreparationError("result does not select one request result")
    benchmark_commit = request.get("benchmark", {}).get("commit")
    entries = [
        item
        for item in matrix.get("images", [])
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


def load_batch_inputs(
    repository_root: pathlib.Path,
    qualification_commit: str,
    terminal_public_results: tuple[str, ...],
) -> tuple[
    dict[str, Any],
    dict[str, tuple[dict[str, Any], bytes, str]],
    list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
    tuple[str, ...],
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
        plan.get("resolved_request_count") != SOURCE_PLAN_REQUEST_COUNT
        or plan.get("resolved_result_count") != SOURCE_PLAN_RESULT_COUNT
        or not isinstance(requests, list)
        or len(requests) != SOURCE_PLAN_REQUEST_COUNT
        or matrix.get("image_count") != BATCH_PROFILE_COUNT
        or matrix.get("request_count") != SOURCE_PLAN_REQUEST_COUNT
        or matrix.get("result_count") != SOURCE_PLAN_RESULT_COUNT
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
        len(selections) != SOURCE_PLAN_RESULT_COUNT
        or len(set(result_ids)) != SOURCE_PLAN_RESULT_COUNT
    ):
        raise PreparationError("final plan does not contain exactly 194 unique Results")
    terminal_set = set(terminal_public_results)
    excluded_result_ids = tuple(sorted(terminal_set.intersection(result_ids)))
    if len(excluded_result_ids) != BATCH_TERMINAL_EXCLUSION_COUNT:
        raise PreparationError(
            "pinned State does not exclude exactly 20 terminal public Results"
        )
    excluded_set = set(excluded_result_ids)
    retained_selections = [
        selection
        for selection in selections
        if selection[1]["result_id"] not in excluded_set
    ]
    retained_requests = {
        request["request_id"] for request, _, _ in retained_selections
    }
    partially_excluded_requests = {
        request["request_id"]
        for request, result, _ in selections
        if result["result_id"] in excluded_set
        and request["request_id"] in retained_requests
    }
    if (
        len(retained_selections) != BATCH_RESULT_COUNT
        or len({selection[1]["result_id"] for selection in retained_selections})
        != BATCH_RESULT_COUNT
        or len(retained_requests) != BATCH_REQUEST_COUNT
        or partially_excluded_requests
    ):
        raise PreparationError(
            "terminal public dispositions do not produce the exact retained batch"
        )
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
    selected_benchmarks = {
        entry["benchmark_commit"] for _, _, entry in retained_selections
    }
    if len(selected_benchmarks) != BATCH_SELECTED_PROFILE_COUNT:
        raise PreparationError("retained batch does not use exactly 34 profiles")
    return matrix, profiles, retained_selections, excluded_result_ids


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


def batch_selection_content(
    selections: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
    profiles: dict[str, tuple[dict[str, Any], bytes, str]],
) -> list[dict[str, str]]:
    """Bind the State-selected retained Results to their exact frozen profiles."""
    content = []
    for request, result, entry in selections:
        profile = profiles[entry["benchmark_commit"]][0]
        content.append(
            {
                "request_id": request["request_id"],
                "result_id": result["result_id"],
                "benchmark_commit": entry["benchmark_commit"],
                "execution_profile_digest": profile["execution_profile_digest"],
                "measurement_config_digest": profile["measurement_config_digest"],
            }
        )
    return sorted(content, key=lambda item: item["result_id"])


def finalize_batch(args: argparse.Namespace) -> None:
    qualification_commit = match(
        COMMIT, args.qualification_commit, "qualification commit"
    )
    repository_root = pathlib.Path(args.qualification_repository_root)
    state_root = pathlib.Path(args.state_root)
    latest_state_time, initial_queue, terminal_public_results = (
        load_and_validate_pinned_state(state_root, [])
    )
    if initial_queue.get("tasks"):
        raise PreparationError(
            "pinned State already contains queued historical public replay"
        )
    matrix, profiles, selections, excluded_result_ids = load_batch_inputs(
        repository_root, qualification_commit, terminal_public_results
    )
    events, expected_tasks = build_batch_events(
        matrix,
        selections,
        profiles,
        qualification_commit,
        args.first_occurred_at,
        args.event_id_seed,
    )
    validated_latest_state_time, queue, validated_terminal_public_results = (
        load_and_validate_pinned_state(state_root, events)
    )
    if (
        validated_latest_state_time != latest_state_time
        or validated_terminal_public_results != terminal_public_results
    ):
        raise PreparationError("pinned State changed during batch validation")
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
            "pinned State did not materialize exactly 174 queued historical tasks"
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
    selected_benchmarks = {
        entry["benchmark_commit"] for _, _, entry in selections
    }
    selected_profile_files = [
        {"path": relative, "sha256": sha256_bytes(raw)}
        for benchmark_commit, (_, raw, relative) in sorted(profiles.items())
        if benchmark_commit in selected_benchmarks
    ]
    content_digests = {
        "profile_set_sha256": sha256_bytes(canonical(profile_files)),
        "selected_profile_set_sha256": sha256_bytes(
            canonical(selected_profile_files)
        ),
        "terminal_exclusion_set_sha256": sha256_bytes(
            canonical(list(excluded_result_ids))
        ),
        "batch_selection_set_sha256": sha256_bytes(
            canonical(batch_selection_content(selections, profiles))
        ),
        "materialized_task_content_set_sha256": sha256_bytes(
            canonical(batch_task_content(expected_tasks))
        ),
    }
    expected_content_digests = {
        "profile_set_sha256": BATCH_PROFILE_SET_SHA256,
        "selected_profile_set_sha256": BATCH_SELECTED_PROFILE_SET_SHA256,
        "terminal_exclusion_set_sha256": BATCH_TERMINAL_EXCLUSION_SET_SHA256,
        "batch_selection_set_sha256": BATCH_SELECTION_SET_SHA256,
        "materialized_task_content_set_sha256": BATCH_TASK_CONTENT_SET_SHA256,
    }
    if content_digests != expected_content_digests:
        raise PreparationError("retained public batch canonical content changed")
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
        "selected_profile_count": BATCH_SELECTED_PROFILE_COUNT,
        "source_plan_request_count": SOURCE_PLAN_REQUEST_COUNT,
        "source_plan_result_count": SOURCE_PLAN_RESULT_COUNT,
        "request_count": BATCH_REQUEST_COUNT,
        "result_count": BATCH_RESULT_COUNT,
        "terminal_exclusion_count": BATCH_TERMINAL_EXCLUSION_COUNT,
        "terminal_exclusion_result_ids": list(excluded_result_ids),
        "terminal_exclusion_set_sha256": content_digests[
            "terminal_exclusion_set_sha256"
        ],
        "batch_selection_set_sha256": content_digests[
            "batch_selection_set_sha256"
        ],
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
        "profile_set_sha256": content_digests["profile_set_sha256"],
        "selected_profile_set_sha256": content_digests[
            "selected_profile_set_sha256"
        ],
        "event_set_sha256": sha256_bytes(canonical(event_files)),
        "materialized_task_content_set_sha256": content_digests[
            "materialized_task_content_set_sha256"
        ],
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
    if args.command == "finalize-batch":
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
