#!/usr/bin/env python3
"""Stage only the four reviewed retained public archives; never decrypt payloads."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import historical_replay_controller as public
import migrate_archive_envelopes as migration
from build_result_receipt import result_tree_digest
from results_schema import ResultsSchemaError, canonical_file_bytes, validate_v2

AUDIT_COMMIT = "f3df4480d48cf5024602c15d656e4469fa774cc9"
INVENTORY_DIGEST = "260dd44bab488046530ef5bb8ae647c2d38e6ec98db1402ceb70a96d848a18ff"
SELECTION_SHA256 = "47a6d80ac52799d2eb55be4846b56869d9bea7c1cf92aac2814bdbc3c0827d79"
SELECTION_DIRECTORY = "evidence/historical-replay/archive-migration-selections"
SUBMISSIONS_REPOSITORY = "leanprover/lean-eval-submissions"
SUBMISSIONS_ORIGINS = {
    "git@github.com:leanprover/lean-eval-submissions.git",
    "https://github.com/leanprover/lean-eval-submissions",
    "https://github.com/leanprover/lean-eval-submissions.git",
}
IDENTITY_FIELDS = {
    "request_id", "historical_accepted_at", "owner_login", "declared_model",
    "problem_id", "statement_revision", "results_repository", "results_commit",
    "results_path", "result_file_sha256", "result_tree_digest", "source_kind",
    "source_repository", "source_commit", "source_visibility", "benchmark_repository",
    "benchmark_commit", "toolchain", "lean_toolchain_blob_sha256",
    "workflow_run_identity_sha256", "result_id", "authority_repository",
    "authority_commit", "authority_path", "authority_sha256",
}


def _refuse(condition: bool, reason: str) -> None:
    if not condition:
        raise migration.MigrationError(reason)


def _entry_digest(entry: dict[str, Any]) -> str:
    return hashlib.sha256(
        migration.PLAN_ENTRY_DIGEST_DOMAIN + migration._canonical_bytes(entry)
    ).hexdigest()


def _check_repository(root: pathlib.Path) -> None:
    origin = migration._git(root, "remote", "get-url", "origin").stdout.decode().strip()
    _refuse(origin in SUBMISSIONS_ORIGINS, "submissions origin differs")
    checkout = migration._git(root, "rev-parse", "--show-toplevel").stdout.decode().strip()
    _refuse(pathlib.Path(checkout).resolve() == root.resolve(), "submissions root differs")
    _refuse(migration._git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == b"false",
            "full submissions history is required")


def _blob(root: pathlib.Path, commit: str, path: str) -> bytes:
    _refuse(isinstance(commit, str) and migration.COMMIT.fullmatch(commit) is not None,
            "original evidence commit differs")
    _refuse(isinstance(path, str) and not path.startswith("/")
            and not any(p in {"", ".", ".."} for p in path.split("/")),
            "original evidence path differs")
    _refuse(migration._git(root, "merge-base", "--is-ancestor", commit, "HEAD", check=False).returncode == 0,
            "original evidence is not an ancestor")
    return migration._git(root, "show", f"{commit}:{path}").stdout


def load_selection(repository_root: pathlib.Path) -> dict[str, Any]:
    """Prove the frozen source selection and five ORIGINAL public identities."""
    _check_repository(repository_root)
    path = repository_root / SELECTION_DIRECTORY / f"{SELECTION_SHA256}.json"
    _refuse(not path.is_symlink() and path.is_file(), "finite selection is not regular")
    raw = path.read_bytes()
    _refuse(hashlib.sha256(raw).hexdigest() == SELECTION_SHA256, "finite selection digest differs")
    selection = json.loads(raw)
    _refuse(isinstance(selection, dict) and set(selection) == {
        "schema_version", "kind", "migration_plan_path", "migration_plan_sha256", "result_bindings"}, "finite selection fields differ")
    _refuse(public.canonical_bytes(selection) == raw and type(selection["schema_version"]) is int
            and selection["schema_version"] == 1
            and selection["kind"] == "queued_public_archive_migration_selection",
            "finite selection format differs")
    plan_sha = selection["migration_plan_sha256"]
    _refuse(isinstance(plan_sha, str) and migration.DIGEST.fullmatch(plan_sha) is not None
            and selection["migration_plan_path"] == f"evidence/historical-replay/archive-migration-plans/{plan_sha}.json",
            "finite producer plan locator differs")
    plan_path = repository_root / selection["migration_plan_path"]
    _refuse(not plan_path.is_symlink() and plan_path.is_file(), "finite producer plan is not regular")
    _refuse(hashlib.sha256(plan_path.read_bytes()).hexdigest() == plan_sha, "finite producer plan SHA differs")
    plan = migration._load_plan(plan_path)
    selection["migration_plan"] = plan
    _refuse(isinstance(plan, dict) and plan.get("source_repository") == "leanprover/lean-eval-audit"
            and plan.get("source_commit") == AUDIT_COMMIT
            and plan.get("inventory_digest") == INVENTORY_DIGEST
            and plan.get("migration_count") == 4 and len(plan.get("entries", [])) == 4
            and plan.get("retained_count") == 0 and plan.get("retained") == []
            and all(type(e.get("source_schema_version")) is int
                    and e["source_schema_version"] == 1 for e in plan["entries"]),
            "finite four-archive plan differs")
    core = {key: plan[key] for key in ("source_repository", "source_commit", "entries", "retained")}
    _refuse(hashlib.sha256(b"lean-eval-archive-envelope-migration-v1\0"
                          + migration._canonical_bytes(core)).hexdigest() == INVENTORY_DIGEST,
            "finite inventory does not reproduce")
    entries = {e["submission_id"]: e for e in plan["entries"]}
    _refuse(len(entries) == 4 and len({_entry_digest(e) for e in entries.values()}) == 4,
            "finite archive identities repeat")
    bindings = selection["result_bindings"]
    _refuse(isinstance(bindings, list) and len(bindings) == 5, "finite Result count differs")
    seen: set[str] = set()
    covered: set[str] = set()
    for binding in bindings:
        _refuse(isinstance(binding, dict) and set(binding) == {
            "archive_submission_id", "original_authority_task"}, "finite Result binding fields differ")
        task = binding["original_authority_task"]
        _refuse(isinstance(task, dict) and set(task) == IDENTITY_FIELDS,
                "original public identity fields differ")
        _refuse(task["result_id"] not in seen and task["source_visibility"] == "public"
                and task["authority_repository"] == SUBMISSIONS_REPOSITORY
                and task["results_repository"] == SUBMISSIONS_REPOSITORY
                and binding["archive_submission_id"] in entries,
                "original Result association differs")
        seen.add(task["result_id"])
        covered.add(binding["archive_submission_id"])
        authority_raw = _blob(repository_root, task["authority_commit"], task["authority_path"])
        public._validate_public_authority_identity(task, json.loads(authority_raw), authority_raw)
        result_raw = _blob(repository_root, task["results_commit"], task["results_path"])
        _refuse(hashlib.sha256(result_raw).hexdigest() == task["result_file_sha256"]
                and result_tree_digest(task["results_path"], result_raw) == task["result_tree_digest"],
                "original Results Git bytes differ")
        document = validate_v2(json.loads(result_raw))
        _refuse(canonical_file_bytes(document) == result_raw, "original Results is not canonical")
        records = [r for r in document["results"] if r["result_id"] == task["result_id"]]
        _refuse(len(records) == 1 and document["user"].lower() == task["owner_login"],
                "original Result is not unique")
        result = records[0]
        _refuse(all(result[k] == task[k] for k in ("declared_model", "problem_id", "statement_revision", "benchmark_commit"))
                and dt.datetime.fromisoformat(result["accepted_at"].replace("Z", "+00:00"))
                    == dt.datetime.fromisoformat(task["historical_accepted_at"].replace("Z", "+00:00"))
                and result["submission"] == {"kind": task["source_kind"], "repo": task["source_repository"],
                                             "ref": task["source_commit"], "public": True},
                "original Result provenance differs")
    _refuse(covered == set(entries) and len(seen) == 5, "finite archive/Result coverage differs")
    return selection


def _require_plan(plan: dict[str, Any], selection: dict[str, Any]) -> None:
    _refuse(migration._canonical_bytes(plan) == migration._canonical_bytes(selection["migration_plan"]),
            "plan is not the exact finite four-archive selection")


def _require_pairs(root: pathlib.Path, plan: dict[str, Any], *, target: bool = False) -> None:
    _refuse(not root.is_symlink() and root.is_dir(), "finite source root is not regular")
    expected = {e["target_path" if target else "source_path"] for e in plan["entries"]}
    expected |= {p.removesuffix(".tar.age") + ".json" for p in expected}
    actual: set[str] = set()
    for path in root.rglob("*"):
        _refuse(not path.is_symlink(), "finite source contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    _refuse(actual == expected, "finite source has missing or extra objects")


def verify_source(plan: dict[str, Any], source_root: pathlib.Path,
                  selection: dict[str, Any]) -> None:
    _require_plan(plan, selection)
    _require_pairs(source_root, plan)
    _refuse(migration.build_plan(source_root, AUDIT_COMMIT) == plan,
            "actual finite source plan differs")
    for entry in plan["entries"]:
        path = source_root / (entry["source_path"].removesuffix(".tar.age") + ".json")
        sidecar = migration._read_json(path, "finite source sidecar")
        _refuse(sidecar.get("submission_public") is True, "original archive is not public")
        for binding in selection["result_bindings"]:
            if binding["archive_submission_id"] != entry["submission_id"]:
                continue
            task = binding["original_authority_task"]
            _refuse(sidecar["submission_repo"] == task["source_repository"]
                    and sidecar["submission_ref"] == task["source_commit"]
                    and sidecar["submission_kind"] == task["source_kind"]
                    and sidecar["submitter"].lower() == task["owner_login"]
                    and sidecar["model"] == task["declared_model"]
                    and sidecar["benchmark_commit"] == task["benchmark_commit"]
                    and ("problem_ids" not in sidecar or task["problem_id"] in sidecar["problem_ids"]),
                    "original archive/Result provenance differs")


def inventory(audit_root: pathlib.Path, repository_root: pathlib.Path,
              export_root: pathlib.Path) -> dict[str, Any]:
    selection = load_selection(repository_root)
    plan = selection["migration_plan"]
    origin = migration._git(audit_root, "remote", "get-url", "origin").stdout.decode().strip()
    _refuse(origin in migration.AUDIT_ORIGINS
            and migration._git(audit_root, "rev-parse", "HEAD").stdout.decode().strip() == AUDIT_COMMIT,
            "finite Audit origin or source differs")
    _refuse(not export_root.exists() and not export_root.is_symlink(), "finite export already exists")
    objects: dict[str, bytes] = {}
    for entry in plan["entries"]:
        for target in [entry["target_path"], entry["target_path"].removesuffix(".tar.age") + ".json"]:
            _refuse(not migration._git(audit_root, "ls-tree", AUDIT_COMMIT, "--", target).stdout,
                    "finite migration target already exists")
        for path, digest in [(entry["source_path"], entry["source_ciphertext_sha256"]),
                             (entry["source_path"].removesuffix(".tar.age") + ".json",
                              entry["source_sidecar_sha256"])]:
            mode = migration._git(audit_root, "ls-tree", AUDIT_COMMIT, "--", path).stdout.split()
            _refuse(len(mode) == 4 and mode[0] == b"100644" and mode[1] == b"blob",
                    "finite source is not an exact regular Git blob")
            raw = migration._git(audit_root, "show", f"{AUDIT_COMMIT}:{path}").stdout
            _refuse(hashlib.sha256(raw).hexdigest() == digest, "finite source Git SHA differs")
            objects[path] = raw
    export_root.mkdir(mode=0o700, parents=True)
    for path, raw in objects.items():
        target = export_root.joinpath(*path.split("/"))
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o600)
    verify_source(plan, export_root, selection)
    return plan


def preflight(plan: dict[str, Any], audit_root: pathlib.Path,
              repository_root: pathlib.Path) -> dict[str, Any]:
    _require_plan(plan, load_selection(repository_root))
    return migration.preflight_audit_checkout(audit_root, AUDIT_COMMIT, plan)


def migrate_one(plan: dict[str, Any], source_root: pathlib.Path, output_root: pathlib.Path,
                repository_root: pathlib.Path, legacy_identity: pathlib.Path,
                file_key_helper: pathlib.Path, adapter_executable: pathlib.Path,
                source_path: str) -> None:
    selection = load_selection(repository_root)
    verify_source(plan, source_root, selection)
    migration._migrate_one_validated(plan, source_root, output_root, legacy_identity,
                                    file_key_helper, adapter_executable, source_path)


def validate_output(plan: dict[str, Any], source_root: pathlib.Path,
                    output_root: pathlib.Path, repository_root: pathlib.Path) -> dict[str, Any]:
    selection = load_selection(repository_root)
    verify_source(plan, source_root, selection)
    _require_pairs(output_root, plan, target=True)
    return migration._validate_output_validated(plan, source_root, output_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "verify-source", "preflight-audit", "migrate-one", "validate-output"))
    parser.add_argument("--repository-root", required=True, type=pathlib.Path)
    parser.add_argument("--audit-root", type=pathlib.Path)
    parser.add_argument("--source-root", type=pathlib.Path)
    parser.add_argument("--plan", type=pathlib.Path)
    parser.add_argument("--output-root", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--legacy-identity", type=pathlib.Path)
    parser.add_argument("--file-key-helper", type=pathlib.Path)
    parser.add_argument("--adapter-executable", type=pathlib.Path)
    parser.add_argument("--source-path")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "inventory":
            plan = inventory(args.audit_root, args.repository_root, args.source_root)
            migration._write(args.output, plan)
        else:
            plan = migration._load_plan(args.plan)
            if args.command == "preflight-audit":
                print(json.dumps(preflight(plan, args.audit_root, args.repository_root), sort_keys=True))
            elif args.command == "migrate-one":
                migrate_one(plan, args.source_root, args.output_root, args.repository_root,
                            args.legacy_identity, args.file_key_helper, args.adapter_executable,
                            args.source_path)
            elif args.command == "validate-output":
                migration._write(args.output, validate_output(plan, args.source_root,
                                                              args.output_root, args.repository_root))
            else:
                verify_source(plan, args.source_root, load_selection(args.repository_root))
        return 0
    except (migration.MigrationError, public.HistoricalReplayControllerError,
            ResultsSchemaError, OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        # Paths and original identities belong only in private review artifacts.
        print(f"finite archive migration refused: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
