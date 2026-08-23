#!/usr/bin/env python3
"""Plan, execute, and verify the one-time shared-recipient archive migration.

The source audit checkout is immutable input.  Migrated objects are written to
a separate clean tree so an operator can validate the complete replacement
before changing the private audit repository.  Plaintext and identities are
never included in the plan or validation report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

from archive_envelope import create_archive_envelope
from key_capability_contract import canonical_archive_path, validate_envelope


COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
UUID7 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
MIGRATABLE_SCHEMAS = {1, 2}
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
    if type(sidecar.get("schema_version")) is not int or sidecar["schema_version"] != schema:
        raise MigrationError("source sidecar schema_version disagrees with inventory")
    missing = REQUIRED_PRESERVED_FIELDS - set(sidecar)
    if missing:
        raise MigrationError(f"source sidecar lacks migration fields: {sorted(missing)}")
    for field in ("sha256_ciphertext", "sha256_plaintext_tar"):
        if not isinstance(sidecar.get(field), str) or DIGEST.fullmatch(sidecar[field]) is None:
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
    core = {key: plan[key] for key in ("source_repository", "source_commit", "entries", "retained")}
    expected = hashlib.sha256(
        b"lean-eval-archive-envelope-migration-v1\0" + _canonical_bytes(core)
    ).hexdigest()
    if plan["inventory_digest"] != expected:
        raise MigrationError("migration plan inventory digest is invalid")
    if plan["migration_count"] != len(plan["entries"]) or plan["retained_count"] != len(plan["retained"]):
        raise MigrationError("migration plan counts are invalid")
    if plan["source_repository"] != "leanprover/lean-eval-audit" or COMMIT.fullmatch(plan["source_commit"]) is None:
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
            or not (source_path.startswith("audit/") or source_path.startswith("archives/"))
            or any(part in {"", ".", ".."} for part in source_path.split("/"))
            or entry["source_schema_version"] not in MIGRATABLE_SCHEMAS
            or not isinstance(submission_id, str)
            or UUID7.fullmatch(submission_id) is None
            or entry["target_path"] != canonical_archive_path(submission_id)
        ):
            raise MigrationError("migration plan entry identity or path is invalid")
        for field in ("source_ciphertext_sha256", "source_sidecar_sha256", "plaintext_sha256"):
            if not isinstance(entry[field], str) or DIGEST.fullmatch(entry[field]) is None:
                raise MigrationError("migration plan entry digest is invalid")
        if type(entry["plaintext_size_bytes"]) is not int or entry["plaintext_size_bytes"] < 0:
            raise MigrationError("migration plan plaintext size is invalid")
        if entry["target_path"] in targets:
            raise MigrationError("migration plan repeats a target path")
        targets.add(entry["target_path"])
    for entry in plan["retained"]:
        if not isinstance(entry, dict) or set(entry) != {
            "source_path", "submission_id", "ciphertext_sha256", "sidecar_sha256"
        }:
            raise MigrationError("retained plan entry fields are invalid")
        submission_id = entry["submission_id"]
        if (
            not isinstance(submission_id, str)
            or UUID7.fullmatch(submission_id) is None
            or entry["source_path"] != canonical_archive_path(submission_id)
            or any(
                not isinstance(entry[field], str) or DIGEST.fullmatch(entry[field]) is None
                for field in ("ciphertext_sha256", "sidecar_sha256")
            )
            or entry["source_path"] in targets
        ):
            raise MigrationError("retained plan entry is invalid")
        targets.add(entry["source_path"])
    return plan


def _run_age_decrypt(identity: pathlib.Path, ciphertext: pathlib.Path, plaintext: pathlib.Path) -> None:
    if identity.is_symlink() or not identity.is_file():
        raise MigrationError("legacy identity must be one regular file")
    result = subprocess.run(
        ["age", "--decrypt", "--identity", str(identity), "--output", str(plaintext), str(ciphertext)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        env={"PATH": os.environ.get("PATH", "")},
    )
    if result.returncode != 0:
        raise MigrationError("legacy age decryption failed")


def migrate_one(
    plan: dict[str, Any],
    source_root: pathlib.Path,
    output_root: pathlib.Path,
    legacy_identity: pathlib.Path,
    adapter_executable: pathlib.Path,
    source_path: str,
) -> None:
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
    with tempfile.TemporaryDirectory(prefix="lean-eval-archive-migration-") as raw:
        scratch = pathlib.Path(raw)
        plaintext = scratch / "source.tar.gz"
        _run_age_decrypt(legacy_identity, source_ciphertext, plaintext)
        if _sha256(plaintext) != entry["plaintext_sha256"] or plaintext.stat().st_size != entry["plaintext_size_bytes"]:
            raise MigrationError("decrypted plaintext disagrees with the legacy sidecar")
        envelope_dir = scratch / "envelope"
        new_ciphertext, envelope_path = create_archive_envelope(
            source_tar=plaintext,
            submission_id=entry["submission_id"],
            output_dir=envelope_dir,
            adapter_executable=adapter_executable,
            adapter_name="aws-kms-v1",
        )
        envelope = validate_envelope(_read_json(envelope_path, "new envelope"))
        migrated = {
            key: source_sidecar[key]
            for key in PRESERVED_FIELDS
            if key in source_sidecar
        }
        migrated.update({
            "schema_version": 3,
            "submission_id": entry["submission_id"],
            "sha256_ciphertext": envelope["archive_ciphertext_sha256"],
            "size_bytes_ciphertext": new_ciphertext.stat().st_size,
            "key_envelope": envelope,
        })
        target_ciphertext.write_bytes(new_ciphertext.read_bytes())
        target_ciphertext.chmod(0o600)
        target_sidecar.write_text(
            json.dumps(migrated, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        target_sidecar.chmod(0o600)


def seed_retained(plan: dict[str, Any], source_root: pathlib.Path, output_root: pathlib.Path) -> None:
    if output_root.is_symlink() or (output_root.exists() and not output_root.is_dir()):
        raise MigrationError("migration output root must be a real directory")
    for entry in plan["retained"]:
        source = source_root.joinpath(*entry["source_path"].split("/"))
        sidecar = source.with_suffix("").with_suffix(".json")
        target = output_root.joinpath(*entry["source_path"].split("/"))
        target_sidecar = target.with_suffix("").with_suffix(".json")
        if target.exists() or target_sidecar.exists():
            raise MigrationError("refusing to overwrite a retained object")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        target_sidecar.write_bytes(sidecar.read_bytes())


def validate_output(plan: dict[str, Any], output_root: pathlib.Path) -> dict[str, Any]:
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
        raise MigrationError("migrated output archive inventory is incomplete or has extras")
    expected_sidecars = {path.removesuffix(".tar.age") + ".json" for path in expected}
    actual_sidecars = {
        path.relative_to(output_root).as_posix()
        for path in output_root.rglob("*.json")
        if path.is_file() and not path.is_symlink()
    }
    if actual_sidecars != expected_sidecars:
        raise MigrationError("migrated output sidecar inventory is incomplete or has extras")
    for entry in plan["entries"]:
        ciphertext = output_root.joinpath(*entry["target_path"].split("/"))
        sidecar_path = ciphertext.with_suffix("").with_suffix(".json")
        sidecar = _read_json(sidecar_path, "migrated sidecar")
        if sidecar.get("schema_version") != 3 or sidecar.get("submission_id") != entry["submission_id"]:
            raise MigrationError("migrated sidecar identity is invalid")
        envelope = validate_envelope(sidecar.get("key_envelope"))
        digest = _sha256(ciphertext)
        if digest == entry["source_ciphertext_sha256"]:
            raise MigrationError("migrated ciphertext still equals the shared-recipient object")
        if sidecar.get("sha256_ciphertext") != digest or envelope["archive_ciphertext_sha256"] != digest:
            raise MigrationError("migrated ciphertext binding is invalid")
        if sidecar.get("sha256_plaintext_tar") != entry["plaintext_sha256"] or sidecar.get("size_bytes_plaintext_tar") != entry["plaintext_size_bytes"]:
            raise MigrationError("migrated plaintext evidence changed")
    for entry in plan["retained"]:
        ciphertext = output_root.joinpath(*entry["source_path"].split("/"))
        sidecar_path = ciphertext.with_suffix("").with_suffix(".json")
        if _sha256(ciphertext) != entry["ciphertext_sha256"] or _sha256(sidecar_path) != entry["sidecar_sha256"]:
            raise MigrationError("retained schema-version-3 object changed")
    return {
        "schema_version": 1,
        "inventory_digest": plan["inventory_digest"],
        "migration_count": len(plan["entries"]),
        "retained_count": len(plan["retained"]),
        "all_sidecars_schema_version": 3,
        "legacy_ciphertexts_retained": 0,
    }


def _write(path: pathlib.Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise MigrationError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--audit-root", required=True, type=pathlib.Path)
    inventory.add_argument("--source-commit", required=True)
    inventory.add_argument("--output", required=True, type=pathlib.Path)
    migrate = commands.add_parser("migrate-one")
    migrate.add_argument("--plan", required=True, type=pathlib.Path)
    migrate.add_argument("--source-root", required=True, type=pathlib.Path)
    migrate.add_argument("--output-root", required=True, type=pathlib.Path)
    migrate.add_argument("--legacy-identity", required=True, type=pathlib.Path)
    migrate.add_argument("--adapter-executable", required=True, type=pathlib.Path)
    migrate.add_argument("--source-path", required=True)
    retained = commands.add_parser("seed-retained")
    retained.add_argument("--plan", required=True, type=pathlib.Path)
    retained.add_argument("--source-root", required=True, type=pathlib.Path)
    retained.add_argument("--output-root", required=True, type=pathlib.Path)
    validate = commands.add_parser("validate-output")
    validate.add_argument("--plan", required=True, type=pathlib.Path)
    validate.add_argument("--output-root", required=True, type=pathlib.Path)
    validate.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            _write(args.output, build_plan(args.audit_root.resolve(), args.source_commit))
        elif args.command == "migrate-one":
            migrate_one(
                _load_plan(args.plan),
                args.source_root.resolve(),
                args.output_root.resolve(),
                args.legacy_identity.resolve(),
                args.adapter_executable.resolve(),
                args.source_path,
            )
        elif args.command == "seed-retained":
            seed_retained(
                _load_plan(args.plan),
                args.source_root.resolve(),
                args.output_root.resolve(),
            )
        else:
            _write(args.output, validate_output(_load_plan(args.plan), args.output_root.resolve()))
    except (MigrationError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
