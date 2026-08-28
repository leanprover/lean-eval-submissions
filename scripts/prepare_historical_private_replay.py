#!/usr/bin/env python3
"""Prepare the source-free historical-private replay plan and State candidates.

The ``plan`` mode is offline and needs no private archive checkout.  The later
``state-events`` mode has two deliberately separate selections.  The
``unavailable-only`` selection emits the terminal ``archive_not_found`` roots
from an exact committed plan and validates them against an exact clean State
checkout without requiring or reading an audit checkout.  The ``full``
selection retains the credentialed archive-validation path for bound entries.
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
from collections import Counter
from typing import Any

from build_result_receipt import result_tree_digest
from classify_historical_private_archives import _validate_sidecar_metadata
from key_capability_contract import canonical_archive_path, validate_envelope
from replay_orchestrator import (
    config_digest,
    validate_execution_profile,
    validate_measurement_config,
)
from results_schema import (
    ResultsSchemaError,
    canonical_file_bytes,
    canonical_store_digest,
    result_id as stable_result_id,
    validate_v2,
)

ROOT = pathlib.Path(__file__).parents[1]
PLAN_SCHEMA = ROOT / "schemas/historical-private-replay-plan-v1.schema.json"
RESULTS_REPOSITORY = "leanprover/lean-eval-submissions"
CROSSWALK_PREFIX = "evidence/historical-replay/private-crosswalks"
PLAN_PREFIX = "evidence/historical-replay/private-plans"
PUBLIC_PROFILE_PREFIX = "evidence/public-replay/profiles"
AUDIT_REPOSITORY = "leanprover/lean-eval-audit"
BENCHMARK_REPOSITORY = "leanprover/lean-eval"
ENTRY_DOMAIN = b"lean-eval-historical-private-replay-plan-entry-v1\0"
WORKFLOW_DOMAIN = b"lean-eval-historical-private-workflow-run-v1\0"
EVENT_ID_DOMAIN = b"lean-eval-historical-private-state-event-v1\0"
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
TIMESTAMP_SECONDS = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
TIMESTAMP_MILLISECONDS = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z"
)


class PrivateReplayPlanError(ValueError):
    """A closed input cannot produce trustworthy replay authority."""


def canonical_compact(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise PrivateReplayPlanError("input is not canonically encodable") from error


def canonical(value: Any, *, state_event: bool = False) -> bytes:
    try:
        raw = (
            json.dumps(
                value,
                ensure_ascii=state_event,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise PrivateReplayPlanError("output is not canonically encodable") from error
    if len(raw) > MAX_JSON_BYTES:
        raise PrivateReplayPlanError("JSON document exceeds the size boundary")
    return raw


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def entry_sha256(value: dict[str, Any]) -> str:
    return sha256(ENTRY_DOMAIN + canonical_compact(value))


def _regular_bytes(path: pathlib.Path, label: str, maximum: int = MAX_JSON_BYTES) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise PrivateReplayPlanError(f"{label} is not one regular file")
        size = path.stat().st_size
        if not 1 <= size <= maximum:
            raise PrivateReplayPlanError(f"{label} exceeds its size boundary")
        return path.read_bytes()
    except OSError as error:
        raise PrivateReplayPlanError(f"{label} is unavailable") from error


def load_json(path: pathlib.Path, label: str, *, canonical_input: bool = True) -> tuple[dict[str, Any], bytes]:
    raw = _regular_bytes(path, label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PrivateReplayPlanError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise PrivateReplayPlanError(f"{label} is not a JSON object")
    if canonical_input and canonical(value) != raw:
        raise PrivateReplayPlanError(f"{label} is not canonical JSON")
    return value, raw


def validate_plan(value: dict[str, Any]) -> None:
    if set(value) != {
        "schema_version",
        "kind",
        "results",
        "crosswalk",
        "classification_counts",
        "replay_readiness_counts",
        "profiles",
        "entries",
    } or value.get("schema_version") != 1 or value.get("kind") != "historical_private_replay_plan":
        raise PrivateReplayPlanError("historical private replay plan envelope is invalid")
    entries = value["entries"]
    profiles = value["profiles"]
    results = value["results"]
    crosswalk = value["crosswalk"]
    if (
        not isinstance(entries, list)
        or not entries
        or len(entries) > 10_000
        or not isinstance(profiles, dict)
        or len(profiles) > 1_000
    ):
        raise PrivateReplayPlanError("historical private replay plan bounds are invalid")
    if (
        not isinstance(results, dict)
        or set(results) != {"repository", "commit", "store_sha256"}
        or results["repository"] != RESULTS_REPOSITORY
        or not isinstance(results["commit"], str)
        or COMMIT.fullmatch(results["commit"]) is None
        or not isinstance(results["store_sha256"], str)
        or DIGEST.fullmatch(results["store_sha256"]) is None
        or not isinstance(crosswalk, dict)
        or set(crosswalk) != {"repository", "commit", "path", "sha256"}
        or crosswalk["repository"] != RESULTS_REPOSITORY
        or not isinstance(crosswalk["commit"], str)
        or COMMIT.fullmatch(crosswalk["commit"]) is None
        or crosswalk["path"] != f"{CROSSWALK_PREFIX}/{crosswalk['sha256']}.json"
        or not isinstance(crosswalk["sha256"], str)
        or DIGEST.fullmatch(crosswalk["sha256"]) is None
    ):
        raise PrivateReplayPlanError("historical private replay plan roots are invalid")
    identifiers = [entry.get("result_id") for entry in entries if isinstance(entry, dict)]
    if len(identifiers) != len(entries) or identifiers != sorted(set(identifiers)):
        raise PrivateReplayPlanError("historical private replay plan entries are not canonical")
    classification_counts = Counter(entry.get("classification") for entry in entries)
    readiness_counts = Counter(
        entry.get("replay_profile_status", "archive_not_found") for entry in entries
    )
    if value["classification_counts"] != {
        "archive_not_found": classification_counts["archive_not_found"],
        "bound": classification_counts["bound"],
    } or value["replay_readiness_counts"] != {
        "archive_not_found": readiness_counts["archive_not_found"],
        "profile_pending": readiness_counts["profile_pending"],
        "profile_qualified": readiness_counts["profile_qualified"],
    }:
        raise PrivateReplayPlanError("historical private replay plan counts are invalid")
    for digest, profile in profiles.items():
        if set(profile) not in (
            {
                "benchmark_commit", "benchmark_tree", "toolchain",
                "lean_toolchain_blob_sha256", "checker",
                "measurement_config_digest", "measurement_config",
                "execution_profile",
            },
            {
                "benchmark_commit", "benchmark_tree", "toolchain",
                "lean_toolchain_blob_sha256", "checker",
                "measurement_config_digest", "measurement_config",
                "execution_profile", "reused_public_profile",
            },
        ):
            raise PrivateReplayPlanError("historical private replay profile fields are invalid")
        actual, core = _profile_core({**profile, "execution_profile_digest": digest})
        if actual != digest or core != {
            key: profile[key]
            for key in core
        }:
            raise PrivateReplayPlanError("historical private replay profile registry is invalid")
        locator = profile.get("reused_public_profile")
        if locator is not None and (
            not isinstance(locator, dict)
            or set(locator) != {"repository", "commit", "path", "sha256"}
            or locator["repository"] != RESULTS_REPOSITORY
            or locator["path"] != f"{PUBLIC_PROFILE_PREFIX}/{digest}.json"
            or COMMIT.fullmatch(locator["commit"]) is None
            or DIGEST.fullmatch(locator["sha256"]) is None
        ):
            raise PrivateReplayPlanError("reused public profile locator is invalid")
    base_fields = {
        "result_id", "historical_accepted_at", "owner_login", "declared_model",
        "problem_id", "statement_revision", "benchmark_commit", "results_path",
        "result_file_sha256", "result_tree_digest", "crosswalk_entry_sha256",
        "classification",
    }
    for entry in entries:
        classification = entry.get("classification")
        expected_fields = base_fields
        if classification == "bound":
            expected_fields = base_fields | {
                "archive_submission_id", "archive_plan_entry_sha256",
                "replay_profile_status",
            }
            if entry.get("replay_profile_status") == "profile_qualified":
                expected_fields |= {"execution_profile_digest"}
        if set(entry) != expected_fields:
            raise PrivateReplayPlanError("historical private replay entry fields are invalid")
        if (
            not isinstance(entry["result_id"], str)
            or re.fullmatch(r"r2_[0-9a-f]{64}", entry["result_id"]) is None
            or stable_result_id(
                entry["owner_login"], entry["declared_model"], entry["problem_id"],
                entry["statement_revision"],
            ) != entry["result_id"]
            or entry["results_path"] != f"results/{entry['owner_login']}.json"
            or COMMIT.fullmatch(entry["benchmark_commit"]) is None
            or any(
                DIGEST.fullmatch(entry[field]) is None
                for field in (
                    "result_file_sha256", "result_tree_digest",
                    "crosswalk_entry_sha256",
                )
            )
        ):
            raise PrivateReplayPlanError("historical private replay result binding is invalid")
        if classification == "bound" and (
            UUID7.fullmatch(entry["archive_submission_id"]) is None
            or DIGEST.fullmatch(entry["archive_plan_entry_sha256"]) is None
            or entry["replay_profile_status"] not in {
                "profile_pending", "profile_qualified"
            }
            or (
                entry["replay_profile_status"] == "profile_qualified"
                and entry["execution_profile_digest"] not in profiles
            )
        ):
            raise PrivateReplayPlanError("historical private replay archive binding is invalid")
        if classification not in {"bound", "archive_not_found"}:
            raise PrivateReplayPlanError("historical private replay classification is invalid")


def write_exclusive(path: pathlib.Path, value: Any, *, state_event: bool = False) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise PrivateReplayPlanError("output parent must be one existing real directory")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(canonical(value, state_event=state_event))
    except OSError as error:
        raise PrivateReplayPlanError(f"refusing to overwrite output: {path}") from error


def _git(root: pathlib.Path, *arguments: str, maximum: int = MAX_JSON_BYTES) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PrivateReplayPlanError("exact Git checkout proof failed") from error
    if completed.returncode != 0 or len(completed.stdout) > maximum:
        raise PrivateReplayPlanError("exact Git checkout proof failed")
    return completed.stdout


def verify_checkout(root: pathlib.Path, commit: str, label: str, *, subtree: pathlib.Path | None = None) -> None:
    if COMMIT.fullmatch(commit) is None:
        raise PrivateReplayPlanError(f"{label} commit is invalid")
    checkout = pathlib.Path(
        _git(root, "rev-parse", "--show-toplevel", maximum=4096).decode().strip()
    ).resolve()
    if _git(checkout, "rev-parse", "HEAD", maximum=64).decode().strip() != commit:
        raise PrivateReplayPlanError(f"{label} checkout is not at the selected commit")
    selected = subtree.resolve() if subtree is not None else root.resolve()
    try:
        relative = selected.relative_to(checkout).as_posix()
    except ValueError as error:
        raise PrivateReplayPlanError(f"{label} input is outside its checkout") from error
    arguments = ["status", "--porcelain=v1", "--untracked-files=all"]
    if relative != ".":
        arguments.extend(["--", relative])
    if _git(checkout, *arguments, maximum=4096):
        raise PrivateReplayPlanError(f"{label} input tree is not clean")


def verify_blob_at_commit(path: pathlib.Path, raw: bytes, commit: str, label: str) -> None:
    if COMMIT.fullmatch(commit) is None:
        raise PrivateReplayPlanError(f"{label} commit is invalid")
    checkout = pathlib.Path(
        _git(path.parent, "rev-parse", "--show-toplevel", maximum=4096)
        .decode()
        .strip()
    ).resolve()
    try:
        relative = path.resolve().relative_to(checkout).as_posix()
    except ValueError as error:
        raise PrivateReplayPlanError(f"{label} is outside its checkout") from error
    if _git(checkout, "show", f"{commit}:{relative}") != raw:
        raise PrivateReplayPlanError(f"{label} is not the exact selected commit blob")


def load_results(results_root: pathlib.Path, commit: str, expected_store: str) -> dict[str, dict[str, Any]]:
    verify_checkout(results_root, commit, "Results", subtree=results_root)
    records: dict[str, dict[str, Any]] = {}
    canonical_files: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(results_root.iterdir(), key=lambda item: item.name):
        if path.name == ".gitkeep":
            continue
        raw = _regular_bytes(path, "Results file")
        try:
            document = validate_v2(json.loads(raw.decode("utf-8")), context="private replay plan Results")
        except (UnicodeError, json.JSONDecodeError, ResultsSchemaError) as error:
            raise PrivateReplayPlanError("Results snapshot is invalid") from error
        if canonical_file_bytes(document) != raw:
            raise PrivateReplayPlanError("Results snapshot is not canonical schema version 2")
        relative = f"results/{path.name}"
        canonical_files.append((relative, document))
        binding = {
            "results_path": relative,
            "result_file_sha256": sha256(raw),
            "result_tree_digest": result_tree_digest(relative, raw),
        }
        for record in document["results"]:
            identifier = record["result_id"]
            if identifier in records:
                raise PrivateReplayPlanError("Results snapshot repeats a result ID")
            records[identifier] = {
                "owner_login": document["user"].lower(),
                "record": record,
                "binding": binding,
            }
    try:
        actual_store = canonical_store_digest(canonical_files)
    except (ResultsSchemaError, UnicodeError, ValueError) as error:
        raise PrivateReplayPlanError("Results store digest cannot be reproduced") from error
    if actual_store != expected_store:
        raise PrivateReplayPlanError("Results store digest changed")
    return records


def _closed_crosswalk(value: dict[str, Any], raw: bytes, commit: str, path: pathlib.Path) -> list[dict[str, Any]]:
    digest = sha256(raw)
    if (
        set(value) != {
            "schema_version", "results_repository", "results_commit",
            "results_store_sha256", "private_result_count", "audit_repository",
            "audit_commit", "archive_inventory_digest", "archive_count",
            "classification_counts", "entries",
        }
        or value.get("schema_version") != 1
        or COMMIT.fullmatch(commit) is None
        or path.name != f"{digest}.json"
        or value["results_repository"] != RESULTS_REPOSITORY
        or value["classification_counts"].get("bound") != 639
        or value["classification_counts"].get("archive_not_found") != 29
        or value["classification_counts"].get("archive_identity_ambiguous") != 0
        or value["classification_counts"].get("archive_metadata_conflict") != 0
    ):
        raise PrivateReplayPlanError("private archive crosswalk is not the closed retained corpus")
    return value["entries"]


def _profile_core(value: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    required = {
        "benchmark_commit",
        "benchmark_tree",
        "toolchain",
        "lean_toolchain_blob_sha256",
        "checker",
        "measurement_config_digest",
        "measurement_config",
        "execution_profile",
    }
    if not required <= set(value):
        raise PrivateReplayPlanError("replay profile lacks its closed execution fields")
    profile = {key: value[key] for key in sorted(required)}
    digest = value.get("execution_profile_digest")
    try:
        validate_execution_profile(profile["execution_profile"])
        validate_measurement_config(profile["measurement_config"])
    except ValueError as error:
        raise PrivateReplayPlanError("replay profile configuration is invalid") from error
    if (
        not isinstance(digest, str)
        or DIGEST.fullmatch(digest) is None
        or config_digest("lean-eval-replay-execution-profile-v1", profile["execution_profile"]) != digest
        or config_digest("lean-eval-replay-measurement-config-v1", profile["measurement_config"])
        != profile["measurement_config_digest"]
        or profile["execution_profile"]["toolchain"] != profile["toolchain"]
        or profile["checker"] != "nanoda"
    ):
        raise PrivateReplayPlanError("replay profile digest binding is invalid")
    return digest, profile


def load_public_profiles(
    directory: pathlib.Path,
    profile_commit: str,
    matrix_path: pathlib.Path,
) -> dict[str, dict[str, Any]]:
    matrix, matrix_raw = load_json(matrix_path, "profile matrix")
    verify_blob_at_commit(matrix_path, matrix_raw, profile_commit, "profile matrix")
    matrix_digest = sha256(matrix_raw)
    if matrix.get("qualification_status") != "unqualified":
        raise PrivateReplayPlanError("profile matrix status changed")
    matrix_entries = {
        entry["benchmark_commit"]: entry for entry in matrix.get("images", [])
    }
    profiles: dict[str, dict[str, Any]] = {}
    benchmarks: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        value, raw = load_json(path, "qualified public profile")
        if value.get("profile_matrix_sha256") != matrix_digest:
            continue
        verify_blob_at_commit(path, raw, profile_commit, "qualified public profile")
        matrix_entry = matrix_entries.get(value.get("benchmark_commit"))
        if matrix_entry is None:
            raise PrivateReplayPlanError("qualified public profile is absent from its matrix")
        digest, core = _profile_core(
            {
                **value,
                "checker": "nanoda",
                "toolchain": matrix_entry["toolchain"],
                "lean_toolchain_blob_sha256": matrix_entry[
                    "lean_toolchain_blob_sha256"
                ],
            }
        )
        if (
            value.get("qualification_status") != "qualified"
            or path.name != f"{digest}.json"
            or value.get("benchmark_repository") != BENCHMARK_REPOSITORY
            or value.get("benchmark_commit") != core["benchmark_commit"]
            or value.get("benchmark_tree") != matrix_entry["benchmark_tree"]
            or digest in profiles
            or core["benchmark_commit"] in benchmarks
        ):
            raise PrivateReplayPlanError("qualified public profile is ambiguous or invalid")
        relative = f"{PUBLIC_PROFILE_PREFIX}/{path.name}"
        core["reused_public_profile"] = {
            "repository": RESULTS_REPOSITORY,
            "commit": profile_commit,
            "path": relative,
            "sha256": sha256(raw),
        }
        profiles[digest] = core
        benchmarks.add(core["benchmark_commit"])
    if not profiles:
        raise PrivateReplayPlanError("no current qualified public profiles were selected")
    return profiles


def load_private_profiles(paths: list[pathlib.Path]) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for path in paths:
        value, _raw = load_json(path, "private replay profile")
        digest, core = _profile_core(value)
        if set(value) != set(core) | {"execution_profile_digest"} or digest in profiles:
            raise PrivateReplayPlanError("private replay profile fields are not closed")
        profiles[digest] = core
    return profiles


def build_plan(
    *,
    crosswalk_path: pathlib.Path,
    crosswalk_commit: str,
    results_root: pathlib.Path,
    public_profile_directory: pathlib.Path,
    public_profile_commit: str,
    profile_matrix: pathlib.Path,
    private_profiles: list[pathlib.Path],
) -> dict[str, Any]:
    crosswalk, crosswalk_raw = load_json(crosswalk_path, "private archive crosswalk")
    verify_blob_at_commit(
        crosswalk_path, crosswalk_raw, crosswalk_commit, "private archive crosswalk"
    )
    crosswalk_entries = _closed_crosswalk(
        crosswalk, crosswalk_raw, crosswalk_commit, crosswalk_path
    )
    records = load_results(
        results_root, crosswalk["results_commit"], crosswalk["results_store_sha256"]
    )
    profiles = load_public_profiles(
        public_profile_directory, public_profile_commit, profile_matrix
    )
    private = load_private_profiles(private_profiles)
    for digest, profile in private.items():
        if digest in profiles and profiles[digest] != profile:
            raise PrivateReplayPlanError("private and public profile digests conflict")
        profiles[digest] = profile
    profiles_by_benchmark: dict[str, str] = {}
    for digest, profile in profiles.items():
        benchmark = profile["benchmark_commit"]
        if benchmark in profiles_by_benchmark:
            raise PrivateReplayPlanError("multiple selected profiles target one benchmark")
        profiles_by_benchmark[benchmark] = digest

    entries: list[dict[str, Any]] = []
    for crosswalk_entry in crosswalk_entries:
        identifier = crosswalk_entry["result_id"]
        source = records.get(identifier)
        if source is None:
            raise PrivateReplayPlanError("crosswalk result is absent from Results")
        record = source["record"]
        owner = source["owner_login"]
        if (
            record["submission"]["public"] is not False
            or stable_result_id(
                owner,
                record["declared_model"],
                record["problem_id"],
                record["statement_revision"],
            )
            != identifier
        ):
            raise PrivateReplayPlanError("crosswalk result identity is invalid")
        entry = {
            "result_id": identifier,
            "historical_accepted_at": record["accepted_at"],
            "owner_login": owner,
            "declared_model": record["declared_model"],
            "problem_id": record["problem_id"],
            "statement_revision": record["statement_revision"],
            "benchmark_commit": record["benchmark_commit"],
            **source["binding"],
            "crosswalk_entry_sha256": sha256(canonical_compact(crosswalk_entry)),
            "classification": crosswalk_entry["classification"],
        }
        if crosswalk_entry["classification"] == "bound":
            profile_digest = profiles_by_benchmark.get(record["benchmark_commit"])
            entry.update(
                archive_submission_id=crosswalk_entry["submission_id"],
                archive_plan_entry_sha256=crosswalk_entry[
                    "archive_plan_entry_sha256"
                ],
                replay_profile_status=(
                    "profile_qualified" if profile_digest else "profile_pending"
                ),
            )
            if profile_digest is not None:
                entry["execution_profile_digest"] = profile_digest
        elif crosswalk_entry["classification"] != "archive_not_found":
            raise PrivateReplayPlanError("crosswalk contains a non-terminal classification")
        entries.append(entry)

    used_profiles = {
        entry["execution_profile_digest"]
        for entry in entries
        if entry.get("replay_profile_status") == "profile_qualified"
    }
    selected_profiles = {digest: profiles[digest] for digest in sorted(used_profiles)}
    classification_counts = Counter(entry["classification"] for entry in entries)
    readiness_counts = Counter(
        entry.get("replay_profile_status", "archive_not_found") for entry in entries
    )
    plan = {
        "schema_version": 1,
        "kind": "historical_private_replay_plan",
        "results": {
            "repository": RESULTS_REPOSITORY,
            "commit": crosswalk["results_commit"],
            "store_sha256": crosswalk["results_store_sha256"],
        },
        "crosswalk": {
            "repository": RESULTS_REPOSITORY,
            "commit": crosswalk_commit,
            "path": f"{CROSSWALK_PREFIX}/{crosswalk_path.name}",
            "sha256": sha256(crosswalk_raw),
        },
        "classification_counts": {
            "archive_not_found": classification_counts["archive_not_found"],
            "bound": classification_counts["bound"],
        },
        "replay_readiness_counts": {
            "archive_not_found": readiness_counts["archive_not_found"],
            "profile_pending": readiness_counts["profile_pending"],
            "profile_qualified": readiness_counts["profile_qualified"],
        },
        "profiles": selected_profiles,
        "entries": entries,
    }
    validate_plan(plan)
    return plan


def archive_binding(audit_root: pathlib.Path, audit_commit: str, entry: dict[str, Any]) -> dict[str, Any]:
    submission_id = entry["archive_submission_id"]
    relative = canonical_archive_path(submission_id)
    ciphertext = audit_root.joinpath(*relative.split("/"))
    sidecar_path = ciphertext.with_suffix("").with_suffix(".json")
    ciphertext_raw = _regular_bytes(ciphertext, "migrated archive", MAX_ARCHIVE_BYTES)
    sidecar, sidecar_raw = load_json(sidecar_path, "migrated archive sidecar", canonical_input=False)
    ciphertext_digest = sha256(ciphertext_raw)
    try:
        metadata = _validate_sidecar_metadata(
            sidecar,
            {
                "submission_id": submission_id,
                "ciphertext_sha256": ciphertext_digest,
            },
            ciphertext_size=len(ciphertext_raw),
        )
        envelope = validate_envelope(sidecar.get("key_envelope"))
    except ValueError as error:
        raise PrivateReplayPlanError("migrated archive sidecar is invalid") from error
    plaintext_digest = sidecar.get("sha256_plaintext_tar")
    plaintext_size = sidecar.get("size_bytes_plaintext_tar")
    if (
        sidecar.get("schema_version") != 3
        or metadata["schema_version"] != 3
        or metadata["submission_id"] != submission_id
        or sidecar.get("submission_id") != submission_id
        or sidecar.get("submission_public") is not False
        or sidecar.get("benchmark_commit") != entry["benchmark_commit"]
        or sidecar.get("sha256_ciphertext") != ciphertext_digest
        or sidecar.get("size_bytes_ciphertext") != len(ciphertext_raw)
        or envelope["submission_id"] != submission_id
        or envelope["archive_ciphertext_sha256"] != ciphertext_digest
        or not isinstance(plaintext_digest, str)
        or DIGEST.fullmatch(plaintext_digest) is None
        or type(plaintext_size) is not int
        or not 0 < plaintext_size <= 10 * 1024 * 1024
    ):
        raise PrivateReplayPlanError("migrated archive binding is invalid")
    workflow_run = sidecar.get("archiver_workflow_run")
    if not isinstance(workflow_run, str) or not workflow_run:
        raise PrivateReplayPlanError("migrated archive workflow identity is invalid")
    return {
        "archive_repository": AUDIT_REPOSITORY,
        "archive_commit": audit_commit,
        "archive_path": relative,
        "archive_sidecar_path": relative.removesuffix(".tar.age") + ".json",
        "archive_ciphertext_sha256": ciphertext_digest,
        "archive_sidecar_sha256": sha256(sidecar_raw),
        "archive_key_envelope_sha256": sha256(canonical_compact(envelope)),
        "archive_plaintext_tar_sha256": plaintext_digest,
        "archive_plaintext_tar_size": plaintext_size,
        "workflow_run_identity_sha256": sha256(
            WORKFLOW_DOMAIN
            + canonical_compact(
                {
                    "archiver_workflow_run": workflow_run,
                    "benchmark_commit": entry["benchmark_commit"],
                }
            )
        ),
    }


def _parse_timestamp(value: str) -> dt.datetime:
    if TIMESTAMP_MILLISECONDS.fullmatch(value) is None:
        raise PrivateReplayPlanError("first event time must use canonical UTC milliseconds")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise PrivateReplayPlanError("first event time is invalid") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" != value:
        raise PrivateReplayPlanError("first event time is invalid")
    return parsed


def _event_identity(occurred_at: dt.datetime, result_id: str, event_type: str) -> str:
    milliseconds = int(occurred_at.timestamp() * 1000)
    randomness = hashlib.sha256(
        EVENT_ID_DOMAIN
        + milliseconds.to_bytes(8, "big")
        + result_id.encode("ascii")
        + b"\0"
        + event_type.encode("ascii")
    ).digest()[:10]
    raw = bytearray(16)
    raw[:6] = milliseconds.to_bytes(6, "big")
    raw[6] = 0x70 | (randomness[0] & 0x0F)
    raw[7] = randomness[1]
    raw[8] = 0x80 | (randomness[2] & 0x3F)
    raw[9:] = randomness[3:]
    encoded = raw.hex()
    return f"{encoded[:8]}-{encoded[8:12]}-{encoded[12:16]}-{encoded[16:20]}-{encoded[20:]}"


def replay_task_id(result_id: str, measurement_digest: str) -> str:
    return "rt1_" + sha256(
        b"lean-eval-replay-task-v1\0"
        + canonical_compact([result_id, measurement_digest])
    )


def build_bound_events(
    *,
    entry: dict[str, Any],
    profile: dict[str, Any],
    archive: dict[str, Any],
    plan_commit: str,
    plan_path: str,
    plan_sha256: str,
    results_commit: str,
    crosswalk: dict[str, str],
    occurred_at: dt.datetime,
) -> list[dict[str, Any]]:
    result_id = entry["result_id"]
    authority_payload = {
        "historical_accepted_at": entry["historical_accepted_at"],
        "owner_login": entry["owner_login"],
        "declared_model": entry["declared_model"],
        "problem_id": entry["problem_id"],
        "statement_revision": entry["statement_revision"],
        "results_repository": RESULTS_REPOSITORY,
        "results_commit": results_commit,
        "results_path": entry["results_path"],
        "result_file_sha256": entry["result_file_sha256"],
        "result_tree_digest": entry["result_tree_digest"],
        "source_visibility": "private",
        "crosswalk_repository": RESULTS_REPOSITORY,
        "crosswalk_commit": crosswalk["commit"],
        "crosswalk_path": crosswalk["path"],
        "crosswalk_sha256": crosswalk["sha256"],
        "crosswalk_entry_sha256": entry["crosswalk_entry_sha256"],
        "archive_plan_entry_sha256": entry["archive_plan_entry_sha256"],
        "archive_submission_id": entry["archive_submission_id"],
        "archive_schema_version": 3,
        "benchmark_repository": BENCHMARK_REPOSITORY,
        "benchmark_commit": entry["benchmark_commit"],
        "toolchain": profile["toolchain"],
        "lean_toolchain_blob_sha256": profile["lean_toolchain_blob_sha256"],
        "authority_repository": RESULTS_REPOSITORY,
        "authority_commit": plan_commit,
        "authority_path": plan_path,
        "authority_sha256": plan_sha256,
        "authority_entry_sha256": entry_sha256(entry),
        **archive,
    }
    profile_digest = entry["execution_profile_digest"]
    locator = profile.get("reused_public_profile")
    if locator is None:
        qualification_path = plan_path
        qualification_commit = plan_commit
        qualification_sha256 = plan_sha256
    else:
        qualification_path = locator["path"]
        qualification_commit = locator["commit"]
        qualification_sha256 = locator["sha256"]
    qualification_payload = {
        "toolchain": profile["toolchain"],
        "benchmark_commit": entry["benchmark_commit"],
        "measurement_config_digest": profile["measurement_config_digest"],
        "execution_profile_digest": profile_digest,
        "checker": "nanoda",
        "qualification_repository": RESULTS_REPOSITORY,
        "qualification_commit": qualification_commit,
        "qualification_path": qualification_path,
        "qualification_sha256": qualification_sha256,
    }
    kinds = (
        ("historical_archive_result.replay_authorized", result_id, authority_payload),
        ("historical_archive_result.replay_profile_qualified", result_id, qualification_payload),
        (
            "replay.enqueued",
            replay_task_id(result_id, profile["measurement_config_digest"]),
            {
                "result_id": result_id,
                "measurement_config_digest": profile["measurement_config_digest"],
                "execution_profile_digest": profile_digest,
                "checker": "nanoda",
                "benchmark_commit": entry["benchmark_commit"],
            },
        ),
    )
    events: list[dict[str, Any]] = []
    parent: str | None = None
    for offset, (event_type, subject, payload) in enumerate(kinds):
        timestamp = occurred_at + dt.timedelta(milliseconds=offset)
        timestamp_text = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        event = {
            "schema_version": 1,
            "event_id": _event_identity(timestamp, result_id, event_type),
            "event_type": event_type,
            "occurred_at": timestamp_text,
            "subject_id": subject,
            "actor": {"kind": "system"},
            "payload": payload,
        }
        if parent is not None:
            event["causation_event_id"] = parent
        parent = event["event_id"]
        events.append(event)
    return events


def build_unavailable_event(
    *,
    entry: dict[str, Any],
    plan_commit: str,
    plan_path: str,
    plan_sha256: str,
    results_commit: str,
    crosswalk: dict[str, str],
    occurred_at: dt.datetime,
) -> dict[str, Any]:
    timestamp = occurred_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    event_type = "historical_archive_result.replay_unavailable"
    return {
        "schema_version": 1,
        "event_id": _event_identity(occurred_at, entry["result_id"], event_type),
        "event_type": event_type,
        "occurred_at": timestamp,
        "subject_id": entry["result_id"],
        "causation_event_id": None,
        "actor": {"kind": "system"},
        "payload": {
            "historical_accepted_at": entry["historical_accepted_at"],
            "owner_login": entry["owner_login"],
            "declared_model": entry["declared_model"],
            "problem_id": entry["problem_id"],
            "statement_revision": entry["statement_revision"],
            "results_repository": RESULTS_REPOSITORY,
            "results_commit": results_commit,
            "results_path": entry["results_path"],
            "result_file_sha256": entry["result_file_sha256"],
            "result_tree_digest": entry["result_tree_digest"],
            "crosswalk_repository": RESULTS_REPOSITORY,
            "crosswalk_commit": crosswalk["commit"],
            "crosswalk_path": crosswalk["path"],
            "crosswalk_sha256": crosswalk["sha256"],
            "crosswalk_entry_sha256": entry["crosswalk_entry_sha256"],
            "plan_repository": RESULTS_REPOSITORY,
            "plan_commit": plan_commit,
            "plan_path": plan_path,
            "plan_sha256": plan_sha256,
            "plan_entry_sha256": entry_sha256(entry),
            "reason_code": "archive_not_found",
        },
    }


def build_unavailable_selection(
    *,
    plan: dict[str, Any],
    plan_commit: str,
    plan_path: str,
    plan_sha256: str,
    first_occurred_at: dt.datetime,
) -> list[dict[str, Any]]:
    """Build the closed terminal corpus without an audit input capability."""
    entries = [
        entry
        for entry in plan["entries"]
        if entry["classification"] == "archive_not_found"
    ]
    if len(entries) != 29:
        raise PrivateReplayPlanError(
            "unavailable-only selection is not the exact terminal corpus"
        )
    events = [
        build_unavailable_event(
            entry=entry,
            plan_commit=plan_commit,
            plan_path=plan_path,
            plan_sha256=plan_sha256,
            results_commit=plan["results"]["commit"],
            crosswalk=plan["crosswalk"],
            occurred_at=first_occurred_at + dt.timedelta(milliseconds=index),
        )
        for index, entry in enumerate(entries)
    ]
    if any(
        event["event_type"] != "historical_archive_result.replay_unavailable"
        or event.get("causation_event_id") is not None
        or event["payload"].get("reason_code") != "archive_not_found"
        for event in events
    ):
        raise PrivateReplayPlanError(
            "unavailable-only selection contains a non-terminal candidate"
        )
    return events


def _state_script_blobs(
    state_root: pathlib.Path, state_commit: str | None
) -> dict[str, bytes]:
    if state_commit is None:
        scripts = sorted((state_root / "scripts").glob("*.py"))
        if not scripts:
            raise PrivateReplayPlanError("State script inventory is empty")
        return {
            path.name: _regular_bytes(path, f"State script {path.name}")
            for path in scripts
        }
    inventory = _git(
        state_root, "ls-tree", "-r", "--name-only", state_commit, "scripts",
        maximum=64 * 1024,
    ).decode().splitlines()
    selected = [
        relative
        for relative in inventory
        if re.fullmatch(r"scripts/[A-Za-z0-9_]+\.py", relative)
    ]
    if not selected or "scripts/validate_state.py" not in selected:
        raise PrivateReplayPlanError("State script inventory is incomplete")
    return {
        pathlib.PurePosixPath(relative).name: _git(
            state_root, "show", f"{state_commit}:{relative}"
        )
        for relative in selected
    }


def _load_committed_state_events(
    state_root: pathlib.Path, state_commit: str
) -> tuple[str, list[dict[str, Any]]]:
    state_raw = _git(state_root, "show", f"{state_commit}:state.json")
    try:
        state = json.loads(state_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PrivateReplayPlanError("State environment document is invalid") from error
    if (
        not isinstance(state, dict)
        or set(state) != {"environment", "schema_version"}
        or state.get("schema_version") != 1
        or state.get("environment") not in {"staging", "production"}
        or canonical(state, state_event=True) != state_raw
    ):
        raise PrivateReplayPlanError("State environment document is invalid")
    paths = _git(
        state_root, "ls-tree", "-r", "--name-only", state_commit, "events",
        maximum=2 * 1024 * 1024,
    ).decode().splitlines()
    events: list[dict[str, Any]] = []
    for relative in paths:
        if re.fullmatch(r"events/[0-9a-f]{2}/[0-9a-f-]{36}\.json", relative) is None:
            raise PrivateReplayPlanError("State event path is not canonical")
        raw = _git(state_root, "show", f"{state_commit}:{relative}")
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise PrivateReplayPlanError("State event blob is invalid") from error
        if (
            not isinstance(event, dict)
            or canonical(event, state_event=True) != raw
            or relative
            != (
                f"events/{event.get('event_id', '').replace('-', '')[:2]}/"
                f"{event.get('event_id', '')}.json"
            )
        ):
            raise PrivateReplayPlanError("State event blob is not canonical")
        events.append(event)
    return state["environment"], events


def validate_state_candidates(
    *,
    state_root: pathlib.Path,
    state_commit: str | None,
    candidates: list[dict[str, Any]],
    append_ready: bool,
) -> None:
    if append_ready:
        if state_commit is None:
            raise PrivateReplayPlanError("append-ready output requires --state-commit")
        verify_checkout(state_root, state_commit, "State")
        remote = _git(state_root, "remote", "get-url", "origin", maximum=4096).decode().strip()
        if remote != "https://github.com/leanprover/lean-eval-state.git":
            raise PrivateReplayPlanError("State checkout remote is not canonical")
    blobs = _state_script_blobs(state_root, state_commit if append_ready else None)
    module_names = tuple(pathlib.Path(name).stem for name in blobs)
    saved_modules = {name: sys.modules.get(name) for name in module_names}
    with tempfile.TemporaryDirectory(prefix="historical-private-state-") as directory:
        scripts_root = pathlib.Path(directory)
        for name, raw in blobs.items():
            (scripts_root / name).write_bytes(raw)
        for name in module_names:
            sys.modules.pop(name, None)
        sys.path.insert(0, str(scripts_root))
        try:
            validator = importlib.import_module("validate_state")
            validate_event_data = getattr(validator, "validate_event_data", None)
            validate_semantics = getattr(validator, "validate_semantics", None)
            if not callable(validate_event_data) or not callable(validate_semantics):
                raise PrivateReplayPlanError("State validator interface is incompatible")
            for index, event in enumerate(candidates):
                try:
                    validate_event_data(event, f"candidate[{index}]")
                except Exception as error:
                    raise PrivateReplayPlanError(
                        f"State candidate {index} fails the supplied validator"
                    ) from error
            if append_ready:
                assert state_commit is not None
                environment, existing = _load_committed_state_events(
                    state_root, state_commit
                )
                for index, event in enumerate(existing):
                    try:
                        validate_event_data(event, f"existing[{index}]")
                    except Exception as error:
                        raise PrivateReplayPlanError(
                            f"State event {index} fails its exact validator"
                        ) from error
                try:
                    validate_semantics(existing, environment)
                    validate_semantics([*existing, *candidates], environment)
                except Exception as error:
                    raise PrivateReplayPlanError(
                        "combined existing and candidate State graph is invalid"
                    ) from error
        except PrivateReplayPlanError:
            raise
        except Exception as error:
            raise PrivateReplayPlanError("State validator cannot be loaded") from error
        finally:
            sys.path.remove(str(scripts_root))
            for name in module_names:
                sys.modules.pop(name, None)
                previous = saved_modules[name]
                if previous is not None:
                    sys.modules[name] = previous


def prepare_state_events(args: argparse.Namespace) -> int:
    plan_path = pathlib.Path(args.plan).resolve()
    plan, plan_raw = load_json(plan_path, "historical private replay plan")
    validate_plan(plan)
    plan_digest = sha256(plan_raw)
    if plan_path.name != f"{plan_digest}.json":
        raise PrivateReplayPlanError("plan filename is not its canonical SHA-256")
    state_root = pathlib.Path(args.state_root).resolve()
    selection = args.selection
    if selection not in {"unavailable-only", "full"}:
        raise PrivateReplayPlanError("State event selection is invalid")
    unavailable_only = selection == "unavailable-only"
    exact_output = args.append_ready or unavailable_only
    if COMMIT.fullmatch(args.authority_commit) is None:
        raise PrivateReplayPlanError("authority commit is invalid")
    if exact_output:
        verify_blob_at_commit(
            plan_path,
            plan_raw,
            args.authority_commit,
            "historical private replay plan",
        )
    first = _parse_timestamp(args.first_occurred_at)
    plan_relative = f"{PLAN_PREFIX}/{plan_path.name}"
    events: list[dict[str, Any]] = []
    archive_cache: dict[str, tuple[str, dict[str, Any]]] = {}
    if unavailable_only:
        events = build_unavailable_selection(
            plan=plan,
            plan_commit=args.authority_commit,
            plan_path=plan_relative,
            plan_sha256=plan_digest,
            first_occurred_at=first,
        )
    else:
        if (
            args.audit_root is None
            or args.audit_commit is None
            or COMMIT.fullmatch(args.audit_commit) is None
        ):
            raise PrivateReplayPlanError(
                "full selection requires a valid audit root and commit"
            )
        audit_root = pathlib.Path(args.audit_root).resolve()
        verify_checkout(audit_root, args.audit_commit, "audit")
        for entry in plan["entries"]:
            if entry["classification"] == "archive_not_found":
                events.append(
                    build_unavailable_event(
                        entry=entry,
                        plan_commit=args.authority_commit,
                        plan_path=plan_relative,
                        plan_sha256=plan_digest,
                        results_commit=plan["results"]["commit"],
                        crosswalk=plan["crosswalk"],
                        occurred_at=first + dt.timedelta(milliseconds=len(events)),
                    )
                )
                continue
            submission_id = entry["archive_submission_id"]
            cached = archive_cache.get(submission_id)
            if cached is None:
                archive = archive_binding(audit_root, args.audit_commit, entry)
                archive_cache[submission_id] = (entry["benchmark_commit"], archive)
            else:
                cached_benchmark, archive = cached
                if cached_benchmark != entry["benchmark_commit"]:
                    raise PrivateReplayPlanError(
                        "shared migrated archive has conflicting benchmark bindings"
                    )
            if entry["replay_profile_status"] != "profile_qualified":
                continue
            profile = plan["profiles"][entry["execution_profile_digest"]]
            selected = build_bound_events(
                entry=entry,
                profile=profile,
                archive=archive,
                plan_commit=args.authority_commit,
                plan_path=plan_relative,
                plan_sha256=plan_digest,
                results_commit=plan["results"]["commit"],
                crosswalk=plan["crosswalk"],
                occurred_at=first + dt.timedelta(milliseconds=len(events)),
            )
            events.extend(selected)
    validate_state_candidates(
        state_root=state_root,
        state_commit=args.state_commit,
        candidates=events,
        append_ready=exact_output,
    )
    output = pathlib.Path(args.output_directory).resolve()
    try:
        output.mkdir(mode=0o700)
    except OSError as error:
        raise PrivateReplayPlanError("refusing to overwrite output directory") from error
    for index, event in enumerate(events):
        relative = pathlib.Path(
            "events", event["event_id"].replace("-", "")[:2], f"{event['event_id']}.json"
        )
        target = output / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_exclusive(target, event, state_event=True)
    status = "APPEND_READY" if exact_output else "PROVISIONAL_NOT_APPEND_READY"
    print(
        f"{status}: prepared {len(events)} State event candidate(s); "
        f"validated {len(archive_cache)} unique schema-version-3 archive(s)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_command = commands.add_parser("plan")
    plan_command.add_argument("--crosswalk", required=True)
    plan_command.add_argument("--crosswalk-commit", required=True)
    plan_command.add_argument("--results-root", required=True)
    plan_command.add_argument("--public-profile-directory", required=True)
    plan_command.add_argument("--public-profile-commit", required=True)
    plan_command.add_argument("--profile-matrix", required=True)
    plan_command.add_argument("--private-profile", action="append", default=[])
    plan_command.add_argument("--output", required=True)
    events_command = commands.add_parser("state-events")
    events_command.add_argument("--plan", required=True)
    events_command.add_argument("--authority-commit", required=True)
    events_command.add_argument(
        "--selection", choices=("unavailable-only", "full"), required=True
    )
    events_command.add_argument("--audit-root")
    events_command.add_argument("--audit-commit")
    events_command.add_argument("--state-root", required=True)
    events_command.add_argument("--state-commit")
    events_command.add_argument("--append-ready", action="store_true")
    events_command.add_argument("--first-occurred-at", required=True)
    events_command.add_argument("--output-directory", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            value = build_plan(
                crosswalk_path=pathlib.Path(args.crosswalk).resolve(),
                crosswalk_commit=args.crosswalk_commit,
                results_root=pathlib.Path(args.results_root).resolve(),
                public_profile_directory=pathlib.Path(
                    args.public_profile_directory
                ).resolve(),
                public_profile_commit=args.public_profile_commit,
                profile_matrix=pathlib.Path(args.profile_matrix).resolve(),
                private_profiles=[pathlib.Path(path).resolve() for path in args.private_profile],
            )
            write_exclusive(pathlib.Path(args.output).resolve(), value)
            return 0
        return prepare_state_events(args)
    except PrivateReplayPlanError as error:
        print(f"historical-private-replay-plan: {error}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError):
        print("historical-private-replay-plan: validation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
