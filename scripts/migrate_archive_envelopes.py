#!/usr/bin/env python3
"""Plan, execute, and verify historical age file-key rewrapping.

The source audit checkout is immutable input.  Migrated objects are written to
a separate clean tree so an operator can validate the complete replacement
before changing the private audit repository. Ciphertext bytes, ciphertext
digests, stable submission IDs, and recorded plaintext evidence never change.
Migration decrypts only the detached age header; archive plaintext is validated
later inside the replay sandbox.
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
import shutil
import subprocess
import sys
from typing import Any

from key_capability_contract import (
    AGE_FILE_KEY_MATERIAL_TYPE,
    archive_file_key_id,
    canonical_archive_path,
    file_key_envelope_binding_context,
    validate_envelope,
)

COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
MIGRATABLE_SCHEMAS = {1, 2}
CANONICAL_CROSSWALK_SHA256 = (
    "dfdcbc0da3a3526f8a26e6a69cefa41cbcd92de7608752193b742fcd92b00a67"
)
CANONICAL_AUDIT_COMMIT = "ad356e7bc5a2d650d9902ac3f6d352a0164360bc"
CANONICAL_AUDIT_INVENTORY_DIGEST = (
    "6b8867f41a13c3ba323746988058886e5dc73da7b509deaf01ccf9c36fe8d5d4"
)
CANONICAL_BOUND_RESULT_COUNT = 639
CANONICAL_BOUND_ARCHIVE_COUNT = 439
CANONICAL_SELECTED_INVENTORY_DIGEST = (
    "a8913f1c8b5073e5b7ab309ba10481b615ca4fc00e629e41a9e57962f3afebd4"
)
PLAN_ENTRY_DIGEST_DOMAIN = b"lean-eval-private-archive-crosswalk-entry-v1\0"
PRESERVED_FIELDS = {
    "submission_repo",
    "submission_ref",
    "submission_kind",
    "submission_public",
    "submitter",
    "model",
    "size_bytes_plaintext_tar",
    "sha256_plaintext_tar",
    "production_description",
    "solution_publication_status",
    "solution_publication_date",
    "archived_at",
    "benchmark_commit",
    "archiver_workflow_run",
    "problem_ids",
    "evaluator_verdict",
}
REQUIRED_PRESERVED_FIELDS = {
    "submission_repo",
    "submission_ref",
    "submission_kind",
    "submission_public",
    "submitter",
    "model",
    "size_bytes_plaintext_tar",
    "sha256_plaintext_tar",
    "archived_at",
    "benchmark_commit",
    "archiver_workflow_run",
}


class MigrationError(ValueError):
    """The source inventory or migrated replacement is unsafe."""


def _read_json(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"{label} is not one UTF-8 JSON object") from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MigrationError(f"{label} must be an object with string keys")
    return value


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise MigrationError(f"cannot hash {path}") from error
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _timestamp_milliseconds(value: Any) -> int:
    if not isinstance(value, str) or re.fullmatch(
        r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
        value,
    ) is None:
        raise MigrationError("legacy archived_at must use canonical UTC seconds")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise MigrationError("legacy archived_at is not a real timestamp") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise MigrationError("legacy archived_at is not a real timestamp")
    return int(parsed.timestamp() * 1000)


def historical_submission_id(
    source_path: str,
    ciphertext_digest: str,
    archived_at: str,
) -> str:
    """Derive a stable UUIDv7 for a pre-server archive without mutable state."""
    if not source_path.startswith("audit/") or not source_path.endswith(".tar.age"):
        raise MigrationError("legacy source path is not canonical")
    if DIGEST.fullmatch(ciphertext_digest) is None:
        raise MigrationError("legacy ciphertext digest is not canonical")
    milliseconds = _timestamp_milliseconds(archived_at)
    if not 0 <= milliseconds <= 0xFFFFFFFFFFFF:
        raise MigrationError("legacy archive timestamp is outside UUIDv7 range")
    randomness = hashlib.sha256(
        b"lean-eval-historical-submission-v1\0"
        + source_path.encode("utf-8")
        + b"\0"
        + ciphertext_digest.encode("ascii")
    ).digest()[:10]
    raw = bytearray(16)
    raw[:6] = milliseconds.to_bytes(6, "big")
    raw[6] = 0x70 | (randomness[0] & 0x0F)
    raw[7] = randomness[1]
    raw[8] = 0x80 | (randomness[2] & 0x3F)
    raw[9:] = randomness[3:]
    encoded = raw.hex()
    return f"{encoded[:8]}-{encoded[8:12]}-{encoded[12:16]}-{encoded[16:20]}-{encoded[20:]}"


def _validate_source_sidecar(
    sidecar: dict[str, Any],
    schema: int,
    ciphertext_path: pathlib.Path,
) -> None:
    if (
        type(sidecar.get("schema_version")) is not int
        or sidecar["schema_version"] != schema
    ):
        raise MigrationError("source sidecar schema_version disagrees with inventory")
    missing = REQUIRED_PRESERVED_FIELDS - set(sidecar)
    if missing:
        raise MigrationError(
            f"source sidecar lacks migration fields: {sorted(missing)}"
        )
    for field in ("sha256_ciphertext", "sha256_plaintext_tar"):
        if (
            not isinstance(sidecar.get(field), str)
            or DIGEST.fullmatch(sidecar[field]) is None
        ):
            raise MigrationError(f"source sidecar {field} is invalid")
    for field in ("size_bytes_ciphertext", "size_bytes_plaintext_tar"):
        if type(sidecar.get(field)) is not int or sidecar[field] < 0:
            raise MigrationError(f"source sidecar {field} is invalid")
    if sidecar["sha256_ciphertext"] != _sha256(ciphertext_path):
        raise MigrationError("source sidecar ciphertext digest disagrees with bytes")
    if sidecar["size_bytes_ciphertext"] != ciphertext_path.stat().st_size:
        raise MigrationError("source sidecar ciphertext size disagrees with bytes")
    if schema == 2:
        submission_id = sidecar.get("submission_id")
        if not isinstance(submission_id, str) or UUID7.fullmatch(submission_id) is None:
            raise MigrationError("schema-version-2 sidecar submission_id is invalid")
    if schema == 1 and "submission_id" in sidecar:
        raise MigrationError("schema-version-1 sidecar unexpectedly has submission_id")


def build_plan(
    audit_root: pathlib.Path,
    source_commit: str,
) -> dict[str, Any]:
    if COMMIT.fullmatch(source_commit) is None:
        raise MigrationError("source commit must be a lowercase commit SHA")
    if audit_root.is_symlink() or not audit_root.is_dir():
        raise MigrationError("audit root must be one real directory")
    entries: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for sidecar_path in sorted(audit_root.rglob("*.json")):
        if ".git" in sidecar_path.parts:
            continue
        sidecar = _read_json(sidecar_path, "audit sidecar")
        schema = sidecar.get("schema_version")
        if schema not in {1, 2, 3} or "sha256_ciphertext" not in sidecar:
            continue
        relative_sidecar = sidecar_path.relative_to(audit_root).as_posix()
        relative_ciphertext = relative_sidecar.removesuffix(".json") + ".tar.age"
        ciphertext_path = audit_root.joinpath(*relative_ciphertext.split("/"))
        if ciphertext_path.is_symlink() or not ciphertext_path.is_file():
            raise MigrationError(f"missing regular ciphertext for {relative_sidecar}")
        if schema == 3:
            envelope = validate_envelope(sidecar.get("key_envelope"))
            if envelope["archive_ciphertext_sha256"] != _sha256(ciphertext_path):
                raise MigrationError("retained schema-version-3 envelope digest mismatch")
            retained.append({
                "source_path": relative_ciphertext,
                "submission_id": envelope["submission_id"],
                "ciphertext_sha256": envelope["archive_ciphertext_sha256"],
                "sidecar_sha256": _sha256(sidecar_path),
            })
            seen_targets.add(relative_ciphertext)
            continue
        _validate_source_sidecar(sidecar, schema, ciphertext_path)
        submission_id = (
            sidecar["submission_id"]
            if schema == 2
            else historical_submission_id(
                relative_ciphertext,
                sidecar["sha256_ciphertext"],
                sidecar.get("archived_at"),
            )
        )
        target_path = canonical_archive_path(submission_id)
        if target_path in seen_targets:
            raise MigrationError(f"duplicate migration target: {target_path}")
        seen_targets.add(target_path)
        entries.append({
            "source_path": relative_ciphertext,
            "source_schema_version": schema,
            "source_ciphertext_sha256": sidecar["sha256_ciphertext"],
            "source_sidecar_sha256": _sha256(sidecar_path),
            "plaintext_sha256": sidecar["sha256_plaintext_tar"],
            "plaintext_size_bytes": sidecar["size_bytes_plaintext_tar"],
            "submission_id": submission_id,
            "target_path": target_path,
        })
    if not entries:
        raise MigrationError("audit inventory has no shared-recipient objects")
    plan_core = {
        "source_repository": "leanprover/lean-eval-audit",
        "source_commit": source_commit,
        "entries": entries,
        "retained": retained,
    }
    return {
        "schema_version": 1,
        **plan_core,
        "migration_count": len(entries),
        "retained_count": len(retained),
        "inventory_digest": hashlib.sha256(
            b"lean-eval-archive-envelope-migration-v1\0" + _canonical_bytes(plan_core)
        ).hexdigest(),
    }


def select_bound_schema1_archives(
    full_plan: dict[str, Any], crosswalk_path: pathlib.Path
) -> dict[str, Any]:
    """Select exactly the unique schema-1 archives bound by the canonical crosswalk."""
    crosswalk = _read_json(crosswalk_path, "canonical private crosswalk")
    if _sha256(crosswalk_path) != CANONICAL_CROSSWALK_SHA256:
        raise MigrationError("private crosswalk does not have the canonical digest")
    if (
        crosswalk.get("schema_version") != 1
        or crosswalk.get("audit_repository") != "leanprover/lean-eval-audit"
        or crosswalk.get("audit_commit") != CANONICAL_AUDIT_COMMIT
        or crosswalk.get("archive_inventory_digest") != CANONICAL_AUDIT_INVENTORY_DIGEST
        or full_plan.get("source_commit") != CANONICAL_AUDIT_COMMIT
        or full_plan.get("source_repository") != "leanprover/lean-eval-audit"
    ):
        raise MigrationError(
            "private crosswalk and archive plan authority do not match"
        )
    crosswalk_entries = crosswalk.get("entries")
    if not isinstance(crosswalk_entries, list):
        raise MigrationError("private crosswalk entries are invalid")
    bound = [
        entry
        for entry in crosswalk_entries
        if isinstance(entry, dict) and entry.get("classification") == "bound"
    ]
    if len(bound) != CANONICAL_BOUND_RESULT_COUNT:
        raise MigrationError("private crosswalk bound-result count changed")
    wanted: set[str] = set()
    for entry in bound:
        if entry.get("archive_schema_version") != 1:
            raise MigrationError(
                "bound private crosswalk includes a non-schema-1 archive"
            )
        digest = entry.get("archive_plan_entry_sha256")
        if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            raise MigrationError("bound private crosswalk entry digest is invalid")
        wanted.add(digest)
    if len(wanted) != CANONICAL_BOUND_ARCHIVE_COUNT:
        raise MigrationError("private crosswalk unique bound archive count changed")
    selected: list[dict[str, Any]] = []
    matched: set[str] = set()
    for entry in full_plan.get("entries", []):
        entry_digest = hashlib.sha256(
            PLAN_ENTRY_DIGEST_DOMAIN + _canonical_bytes(entry)
        ).hexdigest()
        if entry_digest in wanted:
            if entry.get("source_schema_version") != 1:
                raise MigrationError("selected archive is not schema version 1")
            selected.append(entry)
            matched.add(entry_digest)
    if matched != wanted or len(selected) != CANONICAL_BOUND_ARCHIVE_COUNT:
        raise MigrationError(
            "canonical crosswalk does not resolve uniquely in the archive plan"
        )
    plan_core = {
        "source_repository": full_plan["source_repository"],
        "source_commit": full_plan["source_commit"],
        "entries": selected,
        "retained": [],
    }
    return {
        "schema_version": 1,
        **plan_core,
        "migration_count": len(selected),
        "retained_count": 0,
        "inventory_digest": hashlib.sha256(
            b"lean-eval-archive-envelope-migration-v1\0" + _canonical_bytes(plan_core)
        ).hexdigest(),
    }


def _load_plan(path: pathlib.Path) -> dict[str, Any]:
    plan = _read_json(path, "migration plan")
    required = {
        "schema_version",
        "source_repository",
        "source_commit",
        "entries",
        "retained",
        "migration_count",
        "retained_count",
        "inventory_digest",
    }
    if set(plan) != required or plan["schema_version"] != 1:
        raise MigrationError("migration plan fields are not canonical")
    core = {
        key: plan[key]
        for key in ("source_repository", "source_commit", "entries", "retained")
    }
    expected = hashlib.sha256(
        b"lean-eval-archive-envelope-migration-v1\0" + _canonical_bytes(core)
    ).hexdigest()
    if plan["inventory_digest"] != expected:
        raise MigrationError("migration plan inventory digest is invalid")
    if plan["migration_count"] != len(plan["entries"]) or plan["retained_count"] != len(
        plan["retained"]
    ):
        raise MigrationError("migration plan counts are invalid")
    if (
        plan["source_repository"] != "leanprover/lean-eval-audit"
        or COMMIT.fullmatch(plan["source_commit"]) is None
    ):
        raise MigrationError("migration plan source is invalid")
    targets: set[str] = set()
    for entry in plan["entries"]:
        if not isinstance(entry, dict) or set(entry) != {
            "source_path",
            "source_schema_version",
            "source_ciphertext_sha256",
            "source_sidecar_sha256",
            "plaintext_sha256",
            "plaintext_size_bytes",
            "submission_id",
            "target_path",
        }:
            raise MigrationError("migration plan entry fields are invalid")
        source_path = entry["source_path"]
        submission_id = entry["submission_id"]
        if (
            not isinstance(source_path, str)
            or not source_path.endswith(".tar.age")
            or not source_path.startswith(("audit/", "archives/"))
            or any(part in {"", ".", ".."} for part in source_path.split("/"))
            or entry["source_schema_version"] not in MIGRATABLE_SCHEMAS
            or not isinstance(submission_id, str)
            or UUID7.fullmatch(submission_id) is None
            or entry["target_path"] != canonical_archive_path(submission_id)
        ):
            raise MigrationError("migration plan entry identity or path is invalid")
        for field in (
            "source_ciphertext_sha256",
            "source_sidecar_sha256",
            "plaintext_sha256",
        ):
            if (
                not isinstance(entry[field], str)
                or DIGEST.fullmatch(entry[field]) is None
            ):
                raise MigrationError("migration plan entry digest is invalid")
        if (
            type(entry["plaintext_size_bytes"]) is not int
            or entry["plaintext_size_bytes"] < 0
        ):
            raise MigrationError("migration plan plaintext size is invalid")
        if entry["target_path"] in targets:
            raise MigrationError("migration plan repeats a target path")
        targets.add(entry["target_path"])
    for entry in plan["retained"]:
        if not isinstance(entry, dict) or set(entry) != {
            "source_path",
            "submission_id",
            "ciphertext_sha256",
            "sidecar_sha256",
        }:
            raise MigrationError("retained plan entry fields are invalid")
        submission_id = entry["submission_id"]
        if (
            not isinstance(submission_id, str)
            or UUID7.fullmatch(submission_id) is None
            or entry["source_path"] != canonical_archive_path(submission_id)
            or any(
                not isinstance(entry[field], str)
                or DIGEST.fullmatch(entry[field]) is None
                for field in ("ciphertext_sha256", "sidecar_sha256")
            )
            or entry["source_path"] in targets
        ):
            raise MigrationError("retained plan entry is invalid")
        targets.add(entry["source_path"])
    return plan


def _extract_file_key(
    helper: pathlib.Path, identity: pathlib.Path, ciphertext: pathlib.Path
) -> bytes:
    if identity.is_symlink() or not identity.is_file():
        raise MigrationError("legacy identity must be one regular file")
    if helper.is_symlink() or not helper.is_file():
        raise MigrationError("age file-key helper must be one regular file")
    result = subprocess.run(
        [
            str(helper),
            "extract",
            "--identity",
            str(identity),
            "--input",
            str(ciphertext),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=120,
        env={"PATH": os.environ.get("PATH", "")},
    )
    if result.returncode != 0 or len(result.stdout) != 16:
        raise MigrationError("legacy age header file-key extraction failed")
    return result.stdout


def _wrap_file_key(
    adapter_executable: pathlib.Path,
    submission_id: str,
    ciphertext_digest: str,
    file_key: bytes,
) -> dict[str, Any]:
    if len(file_key) != 16:
        raise MigrationError("age file key must contain exactly 16 bytes")
    data_key_id = archive_file_key_id(submission_id, ciphertext_digest)
    context = file_key_envelope_binding_context(
        submission_id, ciphertext_digest, data_key_id
    )
    request = {
        "schema_version": 2,
        "operation": "wrap",
        "adapter": "aws-kms-v1",
        "context": context,
        "key_material_type": AGE_FILE_KEY_MATERIAL_TYPE,
        "plaintext_key_material_base64": base64.b64encode(file_key).decode("ascii"),
    }
    result = subprocess.run(
        [str(adapter_executable), "wrap"],
        input=_canonical_bytes(request),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(pathlib.Path(__file__).resolve().parent)},
    )
    if result.returncode != 0 or len(result.stdout) > 32_768:
        raise MigrationError("root-key adapter refused the age file key")
    try:
        wrapped = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise MigrationError("root-key adapter response is invalid") from error
    if (
        not isinstance(wrapped, dict)
        or set(wrapped) != {"schema_version", "adapter", "wrapped_key_material"}
        or wrapped.get("schema_version") != 2
        or wrapped.get("adapter") != "aws-kms-v1"
    ):
        raise MigrationError("root-key adapter response is not canonical")
    if wrapped["wrapped_key_material"] == base64.b64encode(file_key).decode("ascii"):
        raise MigrationError("root-key adapter returned the plaintext age file key")
    return validate_envelope(
        {
            "schema_version": 2,
            "submission_id": submission_id,
            "archive_ciphertext_sha256": ciphertext_digest,
            "data_key_id": data_key_id,
            "key_material_type": AGE_FILE_KEY_MATERIAL_TYPE,
            "adapter": wrapped["adapter"],
            "wrapped_key_material": wrapped["wrapped_key_material"],
        }
    )


def _require_canonical_selection(plan: dict[str, Any]) -> None:
    if (
        plan.get("source_commit") != CANONICAL_AUDIT_COMMIT
        or plan.get("inventory_digest") != CANONICAL_SELECTED_INVENTORY_DIGEST
        or plan.get("migration_count") != CANONICAL_BOUND_ARCHIVE_COUNT
        or plan.get("retained_count") != 0
        or plan.get("retained") != []
        or len(plan.get("entries", [])) != CANONICAL_BOUND_ARCHIVE_COUNT
        or any(entry.get("source_schema_version") != 1 for entry in plan["entries"])
    ):
        raise MigrationError("application requires the canonical 439-archive selection")


def migrate_one(
    plan: dict[str, Any],
    source_root: pathlib.Path,
    output_root: pathlib.Path,
    legacy_identity: pathlib.Path,
    file_key_helper: pathlib.Path,
    adapter_executable: pathlib.Path,
    source_path: str,
) -> None:
    _require_canonical_selection(plan)
    if output_root.is_symlink() or (output_root.exists() and not output_root.is_dir()):
        raise MigrationError("migration output root must be a real directory")
    matches = [entry for entry in plan["entries"] if entry.get("source_path") == source_path]
    if len(matches) != 1:
        raise MigrationError("source path must identify exactly one migration entry")
    entry = matches[0]
    source_ciphertext = source_root.joinpath(*source_path.split("/"))
    source_sidecar_path = source_ciphertext.with_suffix("").with_suffix(".json")
    source_sidecar = _read_json(source_sidecar_path, "source sidecar")
    if _sha256(source_ciphertext) != entry["source_ciphertext_sha256"] or _sha256(source_sidecar_path) != entry["source_sidecar_sha256"]:
        raise MigrationError("source object changed after the migration plan")
    target_ciphertext = output_root.joinpath(*entry["target_path"].split("/"))
    target_sidecar = target_ciphertext.with_suffix("").with_suffix(".json")
    if target_ciphertext.exists() or target_sidecar.exists():
        raise MigrationError("refusing to overwrite a migrated object")
    target_ciphertext.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    file_key = _extract_file_key(file_key_helper, legacy_identity, source_ciphertext)
    try:
        envelope = _wrap_file_key(
            adapter_executable,
            entry["submission_id"],
            entry["source_ciphertext_sha256"],
            file_key,
        )
    finally:
        file_key = b""
    try:
        migrated = {
            key: source_sidecar[key]
            for key in PRESERVED_FIELDS
            if key in source_sidecar
        }
        migrated.update({
            "schema_version": 3,
            "submission_id": entry["submission_id"],
            "sha256_ciphertext": entry["source_ciphertext_sha256"],
            "size_bytes_ciphertext": source_ciphertext.stat().st_size,
            "key_envelope": envelope,
        })
        shutil.copyfile(source_ciphertext, target_ciphertext)
        target_ciphertext.chmod(0o600)
        target_sidecar.write_text(
            json.dumps(migrated, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        target_sidecar.chmod(0o600)
    except Exception:
        target_ciphertext.unlink(missing_ok=True)
        target_sidecar.unlink(missing_ok=True)
        raise


def _files_equal(first: pathlib.Path, second: pathlib.Path) -> bool:
    try:
        if first.stat().st_size != second.stat().st_size:
            return False
        with first.open("rb") as left, second.open("rb") as right:
            while True:
                left_chunk = left.read(1024 * 1024)
                right_chunk = right.read(1024 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError as error:
        raise MigrationError("cannot compare source and migrated ciphertext") from error


def validate_output(
    plan: dict[str, Any], source_root: pathlib.Path, output_root: pathlib.Path
) -> dict[str, Any]:
    _require_canonical_selection(plan)
    if source_root.is_symlink() or not source_root.is_dir():
        raise MigrationError("migration source root must be a real directory")
    if output_root.is_symlink() or not output_root.is_dir():
        raise MigrationError("migration output root must be a real directory")
    expected = {entry["target_path"] for entry in plan["entries"]} | {
        entry["source_path"] for entry in plan["retained"]
    }
    actual = {
        path.relative_to(output_root).as_posix()
        for path in output_root.rglob("*.tar.age")
        if path.is_file() and not path.is_symlink()
    }
    if actual != expected:
        raise MigrationError(
            "migrated output archive inventory is incomplete or has extras"
        )
    expected_sidecars = {path.removesuffix(".tar.age") + ".json" for path in expected}
    actual_sidecars = {
        path.relative_to(output_root).as_posix()
        for path in output_root.rglob("*.json")
        if path.is_file() and not path.is_symlink()
    }
    if actual_sidecars != expected_sidecars:
        raise MigrationError(
            "migrated output sidecar inventory is incomplete or has extras"
        )
    for entry in plan["entries"]:
        source_ciphertext = source_root.joinpath(*entry["source_path"].split("/"))
        source_sidecar_path = source_ciphertext.with_suffix("").with_suffix(".json")
        source_sidecar = _read_json(source_sidecar_path, "source sidecar")
        if (
            _sha256(source_ciphertext) != entry["source_ciphertext_sha256"]
            or _sha256(source_sidecar_path) != entry["source_sidecar_sha256"]
        ):
            raise MigrationError("source object changed after the migration plan")
        _validate_source_sidecar(
            source_sidecar, entry["source_schema_version"], source_ciphertext
        )
        ciphertext = output_root.joinpath(*entry["target_path"].split("/"))
        sidecar_path = ciphertext.with_suffix("").with_suffix(".json")
        sidecar = _read_json(sidecar_path, "migrated sidecar")
        if (
            sidecar.get("schema_version") != 3
            or sidecar.get("submission_id") != entry["submission_id"]
        ):
            raise MigrationError("migrated sidecar identity is invalid")
        envelope = validate_envelope(sidecar.get("key_envelope"))
        digest = _sha256(ciphertext)
        if envelope["schema_version"] != 2:
            raise MigrationError("migrated sidecar does not use the file-key envelope")
        if (
            digest != entry["source_ciphertext_sha256"]
            or ciphertext.stat().st_size != source_ciphertext.stat().st_size
        ):
            raise MigrationError("historical archive ciphertext bytes changed")
        if not _files_equal(source_ciphertext, ciphertext):
            raise MigrationError("historical archive ciphertext bytes changed")
        if (
            sidecar.get("sha256_ciphertext") != digest
            or envelope["archive_ciphertext_sha256"] != digest
        ):
            raise MigrationError("migrated ciphertext binding is invalid")
        if (
            sidecar.get("sha256_plaintext_tar") != entry["plaintext_sha256"]
            or sidecar.get("size_bytes_plaintext_tar") != entry["plaintext_size_bytes"]
        ):
            raise MigrationError("migrated plaintext evidence changed")
        expected_fields = {
            key for key in PRESERVED_FIELDS if key in source_sidecar
        } | {
            "schema_version",
            "submission_id",
            "sha256_ciphertext",
            "size_bytes_ciphertext",
            "key_envelope",
        }
        if set(sidecar) != expected_fields or any(
            sidecar.get(key) != source_sidecar[key]
            for key in PRESERVED_FIELDS
            if key in source_sidecar
        ):
            raise MigrationError("migrated sidecar metadata changed")
    return {
        "schema_version": 1,
        "inventory_digest": plan["inventory_digest"],
        "migration_count": len(plan["entries"]),
        "retained_count": len(plan["retained"]),
        "all_sidecars_schema_version": 3,
        "ciphertext_bytes_changed": 0,
    }


def _write(path: pathlib.Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise MigrationError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--audit-root", required=True, type=pathlib.Path)
    inventory.add_argument("--source-commit", required=True)
    inventory.add_argument("--crosswalk", required=True, type=pathlib.Path)
    inventory.add_argument("--output", required=True, type=pathlib.Path)
    migrate = commands.add_parser("migrate-one")
    migrate.add_argument("--plan", required=True, type=pathlib.Path)
    migrate.add_argument("--source-root", required=True, type=pathlib.Path)
    migrate.add_argument("--output-root", required=True, type=pathlib.Path)
    migrate.add_argument("--legacy-identity", required=True, type=pathlib.Path)
    migrate.add_argument("--file-key-helper", required=True, type=pathlib.Path)
    migrate.add_argument("--adapter-executable", required=True, type=pathlib.Path)
    migrate.add_argument("--source-path", required=True)
    validate = commands.add_parser("validate-output")
    validate.add_argument("--plan", required=True, type=pathlib.Path)
    validate.add_argument("--source-root", required=True, type=pathlib.Path)
    validate.add_argument("--output-root", required=True, type=pathlib.Path)
    validate.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            full_plan = build_plan(args.audit_root.resolve(), args.source_commit)
            _write(
                args.output,
                select_bound_schema1_archives(full_plan, args.crosswalk.resolve()),
            )
        elif args.command == "migrate-one":
            migrate_one(
                _load_plan(args.plan),
                args.source_root.resolve(),
                args.output_root.resolve(),
                args.legacy_identity.resolve(),
                args.file_key_helper.resolve(),
                args.adapter_executable.resolve(),
                args.source_path,
            )
        else:
            _write(
                args.output,
                validate_output(
                    _load_plan(args.plan),
                    args.source_root.resolve(),
                    args.output_root.resolve(),
                ),
            )
    except (MigrationError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
