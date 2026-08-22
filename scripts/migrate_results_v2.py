#!/usr/bin/env python3
"""Plan or apply the deterministic results-store schema-version-2 migration."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
from typing import Any

from results_schema import (
    ResultsSchemaError,
    canonical_file_bytes,
    canonical_store_digest,
    convert_v1,
    validate_v2,
)


class MigrationError(ValueError):
    pass


def _source_commit(results_dir: pathlib.Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(results_dir), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise MigrationError("--source-commit is required outside a Git checkout")
    return completed.stdout.strip()


def _semantic_digest(files: list[tuple[str, Any]]) -> str:
    digest = hashlib.sha256()
    for relative_path, data in sorted(files):
        path_bytes = relative_path.encode("utf-8")
        data_bytes = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(data_bytes).to_bytes(8, "big"))
        digest.update(data_bytes)
    return digest.hexdigest()


def _v1_record_count(data: dict[str, Any]) -> int:
    return sum(len(problems) for problems in data["solved"].values())


def _project_v1(converted: dict[str, Any]) -> dict[str, Any]:
    solved: dict[str, dict[str, Any]] = {}
    for record in converted["results"]:
        old = {
            "solved_at": record["accepted_at"],
            "benchmark_commit": record["benchmark_commit"],
            "submission_kind": record["submission"]["kind"],
            "submission_repo": record["submission"]["repo"],
            "submission_ref": record["submission"]["ref"],
            "submission_public": record["submission"]["public"],
            "issue_number": record["intake"]["issue_number"],
        }
        old.update(record["production_metadata"])
        solved.setdefault(record["declared_model"], {})[record["problem_id"]] = old
    return {"schema_version": 1, "user": converted["user"], "solved": solved}


def build_migration_plan(
    results_dir: pathlib.Path,
    *,
    source_commit: str,
) -> tuple[dict[str, Any], dict[pathlib.Path, bytes]]:
    if not results_dir.is_dir():
        raise MigrationError(f"results directory not found: {results_dir}")
    source_files: list[tuple[str, Any]] = []
    output_files: list[tuple[str, dict[str, Any]]] = []
    writes: dict[pathlib.Path, bytes] = {}
    source_versions = {"1": 0, "2": 0}
    source_record_count = 0
    migrated_record_count = 0
    preserved_v1_files = 0
    occurrences: dict[str, list[str]] = {}

    for path in sorted(results_dir.glob("*.json")):
        relative = path.name
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MigrationError(f"invalid JSON in {path}: {exc}") from exc
        if not isinstance(source, dict):
            raise MigrationError(f"{path} must contain a JSON object")
        source_files.append((relative, source))
        version = source.get("schema_version")
        try:
            if version == 1:
                converted = convert_v1(source, context=str(path))
                count = _v1_record_count(source)
                if _project_v1(converted) != source:
                    raise MigrationError(
                        f"field-preservation projection failed for {path}"
                    )
                preserved_v1_files += 1
                migrated_record_count += count
            elif version == 2:
                converted = validate_v2(source, context=str(path))
                count = len(converted["results"])
            else:
                raise MigrationError(
                    f"{path} has unsupported schema_version {version!r}"
                )
        except ResultsSchemaError as exc:
            raise MigrationError(str(exc)) from exc
        source_versions[str(version)] += 1
        source_record_count += count
        canonical = canonical_file_bytes(converted)
        if canonical != path.read_bytes():
            writes[path] = canonical
        output_files.append((relative, converted))
        for record in converted["results"]:
            occurrences.setdefault(record["result_id"], []).append(relative)

    duplicates = [
        {"result_id": identifier, "files": paths}
        for identifier, paths in sorted(occurrences.items())
        if len(paths) > 1
    ]
    report = {
        "schema_version": 1,
        "source_commit": source_commit,
        "source_file_count": len(source_files),
        "source_record_count": source_record_count,
        "source_versions": source_versions,
        "source_digest": _semantic_digest(source_files),
        "output_file_count": len(output_files),
        "output_record_count": sum(len(data["results"]) for _, data in output_files),
        "canonical_output_digest": canonical_store_digest(output_files),
        "changed_files": sorted(path.name for path in writes),
        "duplicate_result_ids": duplicates,
        "preservation": {
            "v1_files_projected_exactly": preserved_v1_files,
            "v1_records_migrated": migrated_record_count,
            "record_count_equal": source_record_count
            == sum(len(data["results"]) for _, data in output_files),
        },
    }
    report["ready_to_apply"] = (
        not duplicates and report["preservation"]["record_count_equal"]
    )
    return report, writes


def apply_plan(writes: dict[pathlib.Path, bytes]) -> None:
    for path, contents in sorted(writes.items(), key=lambda item: str(item[0])):
        temporary = path.with_name(path.name + ".v2-migration-tmp")
        temporary.write_bytes(contents)
        temporary.replace(path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir", type=pathlib.Path, default=pathlib.Path("results")
    )
    parser.add_argument("--source-commit")
    parser.add_argument("--report", type=pathlib.Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-source-digest")
    parser.add_argument("--expect-record-count", type=int)
    parser.add_argument("--expect-output-digest")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        source_commit = args.source_commit or _source_commit(args.results_dir)
        report, writes = build_migration_plan(
            args.results_dir, source_commit=source_commit
        )
        if not report["ready_to_apply"]:
            raise MigrationError("migration report contains duplicates or record loss")
        if args.apply:
            required = {
                "--expect-source-digest": args.expect_source_digest,
                "--expect-record-count": args.expect_record_count,
                "--expect-output-digest": args.expect_output_digest,
            }
            missing = [flag for flag, value in required.items() if value is None]
            if missing:
                raise MigrationError(
                    "--apply requires reviewed expectations: " + ", ".join(missing)
                )
            checks = [
                (args.expect_source_digest, report["source_digest"], "source digest"),
                (
                    args.expect_record_count,
                    report["source_record_count"],
                    "record count",
                ),
                (
                    args.expect_output_digest,
                    report["canonical_output_digest"],
                    "output digest",
                ),
            ]
            for expected, actual, label in checks:
                if expected != actual:
                    raise MigrationError(
                        f"{label} changed: expected {expected!r}, got {actual!r}"
                    )
            apply_plan(writes)
            report["applied"] = True
        else:
            report["applied"] = False
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
