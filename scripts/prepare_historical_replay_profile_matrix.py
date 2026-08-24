#!/usr/bin/env python3
"""Build a source-free image matrix for exact historical public replay bindings."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

import tomllib
from aggregate_public_replay_github_evidence import canonical_document_bytes
from prepare_public_replay_plan import (
    COMMIT,
    DIGEST,
    TOOLCHAIN,
    PublicReplayPlanError,
    validate_toolchain_registry,
)
from resolve_public_replay_github_evidence import _read_bounded, _write_exclusive
from results_schema import result_id as canonical_result_id

MAX_PLAN_BYTES = 16 * 1024 * 1024
MAX_REGISTRY_BYTES = 1024 * 1024
MAX_COMPONENT_BYTES = 128 * 1024
MAX_GIT_OBJECT_BYTES = 16 * 1024 * 1024
PROBLEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
REQUEST_ID = re.compile(r"prr_[0-9a-f]{64}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
LOGIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?\Z")
TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
GO = re.compile(r"go[0-9]+\.[0-9]+\.[0-9]+\Z")
RUST = re.compile(r"rustc-[0-9]+\.[0-9]+\.[0-9]+\Z")
MAX_SAFE_INTEGER = 9_007_199_254_740_991


class ProfileMatrixError(ValueError):
    """Historical replay bindings cannot produce an exact image matrix."""


def _fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ProfileMatrixError(f"{label} fields are not closed")
    return value


def _integer(value: Any, label: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum or value > MAX_SAFE_INTEGER:
        raise ProfileMatrixError(f"{label} is invalid")
    return value


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ProfileMatrixError(f"{label} is invalid")
    return value


def _timestamp(value: Any, label: str) -> str:
    text = _match(TIMESTAMP, value, label)
    try:
        datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProfileMatrixError(f"{label} is invalid") from error
    return text


def _canonical_binding(value: dict[str, Any], raw: bytes, label: str) -> None:
    if canonical_document_bytes(value) != raw:
        raise ProfileMatrixError(f"{label} bytes do not bind the validated value")


def _canonical_input(
    path: pathlib.Path, maximum: int, label: str
) -> tuple[dict[str, Any], bytes]:
    raw = _read_bounded(path, maximum, label)
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProfileMatrixError(f"{label} is not readable JSON") from error
    if not isinstance(value, dict) or canonical_document_bytes(value) != raw:
        raise ProfileMatrixError(f"{label} is not canonical JSON")
    return value, raw


def _git(repository: pathlib.Path, *arguments: str, maximum: int = 4096) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ProfileMatrixError(
            f"benchmark Git object is unavailable: {' '.join(arguments)}"
        ) from error
    if len(result.stdout) > maximum:
        raise ProfileMatrixError("benchmark Git object exceeds its size limit")
    return result.stdout


def _component(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"repository", "commit"}:
        raise ProfileMatrixError(f"{label} fields are not closed")
    if (
        not isinstance(value["repository"], str)
        or REPOSITORY.fullmatch(value["repository"]) is None
        or not isinstance(value["commit"], str)
        or COMMIT.fullmatch(value["commit"]) is None
    ):
        raise ProfileMatrixError(f"{label} is invalid")
    return value


def validate_component_lock(
    value: Any, exact_toolchains: set[str]
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    fields = {
        "schema_version",
        "kind",
        "runner_profile",
        "go_toolchain",
        "rust_toolchain",
        "cache_state",
        "measurement_command",
        "components",
        "lean4export",
    }
    value = _fields(value, fields, "component lock")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "historical_public_replay_component_lock"
        or value["runner_profile"] != "cloudflare-sandbox-standard-4-v1"
        or not isinstance(value["go_toolchain"], str)
        or GO.fullmatch(value["go_toolchain"]) is None
        or not isinstance(value["rust_toolchain"], str)
        or RUST.fullmatch(value["rust_toolchain"]) is None
        or value["cache_state"] != "cold"
        or value["measurement_command"] != ["/opt/lean-eval/replay-measure"]
        or not isinstance(value["components"], dict)
        or set(value["components"]) != {"comparator", "landrun", "nanoda"}
        or not isinstance(value["lean4export"], list)
    ):
        raise ProfileMatrixError("component lock identity is invalid")
    components = {
        name: _component(item, f"component {name}")
        for name, item in value["components"].items()
    }
    expected_repositories = {
        "comparator": "leanprover/comparator",
        "landrun": "zouuup/landrun",
        "nanoda": "robsimmons/nanoda_lib",
    }
    if any(
        components[name]["repository"] != repository
        for name, repository in expected_repositories.items()
    ):
        raise ProfileMatrixError("component lock repositories are invalid")
    exports: dict[str, dict[str, str]] = {}
    for item in value["lean4export"]:
        if not isinstance(item, dict) or set(item) != {
            "repository",
            "commit",
            "toolchain",
        }:
            raise ProfileMatrixError("lean4export lock fields are not closed")
        toolchain = item["toolchain"]
        component = _component(
            {"repository": item["repository"], "commit": item["commit"]},
            "lean4export component",
        )
        if (
            not isinstance(toolchain, str)
            or TOOLCHAIN.fullmatch(toolchain) is None
            or toolchain in exports
            or component["repository"] != "leanprover/lean4export"
        ):
            raise ProfileMatrixError("lean4export locks are not exact and unique")
        exports[toolchain] = component
    if set(exports) != exact_toolchains:
        raise ProfileMatrixError("lean4export locks do not cover exact toolchains")
    return components, exports


def validate_plan(
    value: Any, toolchains: dict[str, dict[str, str]], registry_sha256: str
) -> dict[str, dict[str, Any]]:
    top_fields = {
        "schema_version",
        "kind",
        "source_repository",
        "source_commit",
        "inventory_sha256",
        "resolution_requests_sha256",
        "github_evidence_aggregate_sha256",
        "workflow_definition_registry_sha256",
        "benchmark_toolchain_registry_sha256",
        "resolved_request_count",
        "resolved_result_count",
        "pending_request_count",
        "activation_status",
        "activation_requirement",
        "execution_profile_status",
        "execution_profile_requirement",
        "requests",
    }
    value = _fields(value, top_fields, "historical replay plan")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "historical_public_replay_plan"
        or value["source_repository"] != "leanprover/lean-eval-submissions"
        or not isinstance(value["source_commit"], str)
        or COMMIT.fullmatch(value["source_commit"]) is None
        or value["activation_status"] != "blocked"
        or value["activation_requirement"]
        != "legacy_public_result_replay_authority_v1"
        or value["execution_profile_status"] != "unresolved"
        or value["execution_profile_requirement"]
        != "historical_benchmark_toolchain_execution_profile_v1"
        or value["benchmark_toolchain_registry_sha256"] != registry_sha256
    ):
        raise ProfileMatrixError("historical replay plan identity is invalid")
    for field in (
        "inventory_sha256",
        "resolution_requests_sha256",
        "github_evidence_aggregate_sha256",
        "workflow_definition_registry_sha256",
        "benchmark_toolchain_registry_sha256",
    ):
        _match(DIGEST, value[field], f"historical replay plan {field}")
    request_count = _integer(
        value["resolved_request_count"], "historical replay plan request count"
    )
    expected_result_count = _integer(
        value["resolved_result_count"], "historical replay plan result count"
    )
    _integer(
        value["pending_request_count"],
        "historical replay plan pending request count",
        minimum=0,
    )
    requests = value["requests"]
    if not isinstance(requests, list) or len(requests) != request_count:
        raise ProfileMatrixError("historical replay plan request count is inconsistent")
    bindings: dict[str, dict[str, Any]] = {}
    request_ids: set[str] = set()
    result_ids: set[str] = set()
    result_count = 0
    ordered_request_ids: list[str] = []
    request_fields = {
        "request_id",
        "historical_accepted_at",
        "owner_login",
        "declared_model",
        "issue",
        "historical_evaluation",
        "source",
        "benchmark",
        "results",
        "activation_requirement",
    }
    for request in requests:
        request = _fields(request, request_fields, "historical replay request")
        request_id = _match(
            REQUEST_ID, request["request_id"], "historical replay request_id"
        )
        owner = _match(LOGIN, request["owner_login"], "historical replay owner")
        _timestamp(
            request["historical_accepted_at"], "historical replay accepted_at"
        )
        model = request["declared_model"]
        if (
            request_id in request_ids
            or not isinstance(model, str)
            or not 1 <= len(model) <= 256
            or any(ord(char) < 32 for char in model)
            or request["activation_requirement"]
            != "legacy_public_result_replay_authority_v1"
        ):
            raise ProfileMatrixError("historical replay request binding is invalid")
        request_ids.add(request_id)
        ordered_request_ids.append(request_id)
        issue = _fields(
            request["issue"],
            {"repository", "number", "identity_sha256"},
            "historical replay issue",
        )
        if issue["repository"] not in {
            "leanprover/lean-eval",
            "leanprover/lean-eval-submissions",
        }:
            raise ProfileMatrixError("historical replay issue repository is invalid")
        _integer(issue["number"], "historical replay issue number")
        _match(DIGEST, issue["identity_sha256"], "historical replay issue identity")
        evaluation = _fields(
            request["historical_evaluation"],
            {
                "workflow_contract",
                "workflow_repository_commit",
                "workflow_definition_sha256",
                "workflow_run_id",
                "workflow_run_attempt",
                "workflow_run_identity_sha256",
            },
            "historical replay evaluation",
        )
        if evaluation["workflow_contract"] not in {
            "benchmark_repository_head",
            "split_repository_recorded_benchmark_v1",
        }:
            raise ProfileMatrixError("historical replay workflow contract is invalid")
        _match(
            COMMIT,
            evaluation["workflow_repository_commit"],
            "historical replay workflow commit",
        )
        _match(
            DIGEST,
            evaluation["workflow_definition_sha256"],
            "historical replay workflow definition digest",
        )
        _integer(evaluation["workflow_run_id"], "historical replay workflow run")
        _integer(
            evaluation["workflow_run_attempt"],
            "historical replay workflow run attempt",
        )
        _match(
            DIGEST,
            evaluation["workflow_run_identity_sha256"],
            "historical replay workflow run identity",
        )
        source = _fields(
            request["source"],
            {"kind", "repository", "commit", "visibility"},
            "historical replay source",
        )
        if source["kind"] not in {"github_repo", "gist"}:
            raise ProfileMatrixError("historical replay source kind is invalid")
        _match(
            REPOSITORY,
            source["repository"],
            "historical replay source repository",
        )
        _match(COMMIT, source["commit"], "historical replay source commit")
        if source["visibility"] != "public":
            raise ProfileMatrixError("historical replay source visibility is invalid")
        benchmark = _fields(
            request["benchmark"],
            {"repository", "commit", "toolchain", "lean_toolchain_blob_sha256"},
            "historical replay benchmark",
        )
        commit = _match(
            COMMIT, benchmark["commit"], "historical replay benchmark commit"
        )
        registry = toolchains.get(commit)
        if (
            registry is None
            or benchmark["repository"] != "leanprover/lean-eval"
            or benchmark["toolchain"] != registry["lean_toolchain"]
            or benchmark["lean_toolchain_blob_sha256"]
            != registry["lean_toolchain_blob_sha256"]
        ):
            raise ProfileMatrixError("plan benchmark differs from toolchain registry")
        results = request["results"]
        if not isinstance(results, list) or not results:
            raise ProfileMatrixError("historical replay results are invalid")
        binding = bindings.setdefault(
            commit,
            {
                "benchmark": benchmark,
                "request_count": 0,
                "result_count": 0,
                "problems": {},
            },
        )
        if binding["benchmark"] != benchmark:
            raise ProfileMatrixError("benchmark commit has conflicting bindings")
        binding["request_count"] += 1
        binding["result_count"] += len(results)
        result_count += len(results)
        ordered_result_ids: list[str] = []
        for result in results:
            result = _fields(
                result,
                {
                    "result_id",
                    "owner_login",
                    "problem_id",
                    "statement_revision",
                    "results_repository",
                    "results_commit",
                    "results_path",
                    "result_file_sha256",
                    "result_tree_digest",
                },
                "historical replay result",
            )
            result_id = _match(
                RESULT_ID, result["result_id"], "historical replay result_id"
            )
            problem = _match(
                PROBLEM, result["problem_id"], "historical replay problem"
            )
            revision = _integer(
                result["statement_revision"], "historical replay statement revision"
            )
            if (
                result_id in result_ids
                or result["owner_login"] != owner
                or result["results_repository"]
                != "leanprover/lean-eval-submissions"
                or result["results_commit"] != value["source_commit"]
                or result["results_path"] != f"results/{owner}.json"
                or result_id != canonical_result_id(owner, model, problem, revision)
            ):
                raise ProfileMatrixError("historical result identity is invalid")
            _match(
                DIGEST,
                result["result_file_sha256"],
                "historical replay result file digest",
            )
            _match(
                DIGEST,
                result["result_tree_digest"],
                "historical replay result tree digest",
            )
            result_ids.add(result_id)
            ordered_result_ids.append(result_id)
            previous_revision = binding["problems"].setdefault(problem, revision)
            if previous_revision != revision:
                raise ProfileMatrixError("problem has conflicting statement revisions")
        if ordered_result_ids != sorted(ordered_result_ids):
            raise ProfileMatrixError("historical replay results are not sorted")
    if ordered_request_ids != sorted(ordered_request_ids):
        raise ProfileMatrixError("historical replay requests are not sorted")
    if set(bindings) != set(toolchains) or result_count != expected_result_count:
        raise ProfileMatrixError("historical replay plan coverage is incomplete")
    return bindings


def _manifest_revisions(
    repository: pathlib.Path, commit: str, paths: set[str]
) -> tuple[str, dict[str, int]]:
    monolith = "manifests/problems.toml" in paths
    individual = sorted(
        path
        for path in paths
        if re.fullmatch(
            r"manifests/problems/[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.toml", path
        )
    )
    if monolith == bool(individual):
        raise ProfileMatrixError("benchmark manifest layout is ambiguous or absent")
    revisions: dict[str, int] = {}
    if monolith:
        try:
            document = tomllib.loads(
                _git(
                    repository,
                    "show",
                    f"{commit}:manifests/problems.toml",
                    maximum=MAX_GIT_OBJECT_BYTES,
                ).decode("utf-8")
            )
        except (UnicodeError, tomllib.TOMLDecodeError) as error:
            raise ProfileMatrixError(
                "monolithic problem manifest is invalid"
            ) from error
        records = document.get("problem")
        if not isinstance(records, list):
            raise ProfileMatrixError("monolithic problem manifest has no records")
        layout = "monolith_v1"
    else:
        records = []
        for path in individual:
            try:
                records.append(
                    tomllib.loads(
                        _git(
                            repository,
                            "show",
                            f"{commit}:{path}",
                            maximum=MAX_GIT_OBJECT_BYTES,
                        ).decode("utf-8")
                    )
                )
            except (UnicodeError, tomllib.TOMLDecodeError) as error:
                raise ProfileMatrixError(
                    f"problem manifest is invalid: {path}"
                ) from error
        layout = "per_problem_v1"
    for record in records:
        if not isinstance(record, dict):
            raise ProfileMatrixError("problem manifest record is invalid")
        problem = record.get("id")
        revision = record.get("statement_revision", 1)
        if (
            not isinstance(problem, str)
            or PROBLEM.fullmatch(problem) is None
            or problem in revisions
            or type(revision) is not int
            or revision <= 0
        ):
            raise ProfileMatrixError("problem manifest identity is invalid")
        revisions[problem] = revision
    if not revisions:
        raise ProfileMatrixError("problem manifest is empty")
    return layout, revisions


def inspect_benchmark(
    repository: pathlib.Path,
    commit: str,
    binding: dict[str, Any],
) -> dict[str, Any]:
    resolved_commit = (
        _git(repository, "rev-parse", f"{commit}^{{commit}}").decode().strip()
    )
    tree = _git(repository, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    if resolved_commit != commit or COMMIT.fullmatch(tree) is None:
        raise ProfileMatrixError("benchmark commit identity is invalid")
    paths = set(
        _git(
            repository,
            "ls-tree",
            "-r",
            "--name-only",
            "-z",
            commit,
            maximum=MAX_GIT_OBJECT_BYTES,
        )
        .decode("utf-8")
        .rstrip("\0")
        .split("\0")
    )
    required_root_paths = {
        "LeanEval/EasyProblems.lean",
        "EvalTools/Main.lean",
        "lakefile.toml",
        "lake-manifest.json",
        "lean-toolchain",
    }
    if not required_root_paths <= paths:
        raise ProfileMatrixError("benchmark is missing required evaluator inputs")
    toolchain_bytes = _git(
        repository,
        "show",
        f"{commit}:lean-toolchain",
        maximum=4096,
    )
    benchmark = binding["benchmark"]
    if (
        toolchain_bytes.decode("ascii").strip() != benchmark["toolchain"]
        or hashlib.sha256(toolchain_bytes).hexdigest()
        != benchmark["lean_toolchain_blob_sha256"]
    ):
        raise ProfileMatrixError("benchmark lean-toolchain bytes differ from plan")
    layout, revisions = _manifest_revisions(repository, commit, paths)
    workspaces = sorted(
        path.split("/")[1]
        for path in paths
        if re.fullmatch(
            r"generated/[A-Za-z0-9][A-Za-z0-9_-]{0,127}/lakefile\.toml", path
        )
    )
    if len(workspaces) != len(set(workspaces)) or not workspaces:
        raise ProfileMatrixError("benchmark generated workspaces are invalid")
    for problem, revision in binding["problems"].items():
        if revisions.get(problem) != revision:
            raise ProfileMatrixError(
                f"benchmark manifest does not contain {problem} revision {revision}"
            )
        required = {
            f"generated/{problem}/lakefile.toml",
            f"generated/{problem}/Challenge.lean",
            f"generated/{problem}/Solution.lean",
            f"generated/{problem}/Submission.lean",
            f"generated/{problem}/config.json",
        }
        if not required <= paths:
            raise ProfileMatrixError(f"benchmark workspace is incomplete: {problem}")
        try:
            lakefile = tomllib.loads(
                _git(
                    repository,
                    "show",
                    f"{commit}:generated/{problem}/lakefile.toml",
                    maximum=1024 * 1024,
                ).decode("utf-8")
            )
        except (UnicodeError, tomllib.TOMLDecodeError) as error:
            raise ProfileMatrixError(
                f"benchmark workspace is invalid: {problem}"
            ) from error
        if lakefile.get("name") != problem:
            raise ProfileMatrixError(f"benchmark workspace name differs: {problem}")
        try:
            workspace_config = json.loads(
                _git(
                    repository,
                    "show",
                    f"{commit}:generated/{problem}/config.json",
                    maximum=1024 * 1024,
                ).decode("utf-8")
            )
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ProfileMatrixError(
                f"benchmark workspace configuration is invalid: {problem}"
            ) from error
        if (
            not isinstance(workspace_config, dict)
            or type(workspace_config.get("enable_nanoda")) is not bool
            or "measurement_command" in workspace_config
        ):
            raise ProfileMatrixError(
                f"benchmark workspace cannot accept the authoritative checker: {problem}"
            )
    return {
        "benchmark_tree": tree,
        "manifest_layout": layout,
        "workspace_count": len(workspaces),
    }


def build_matrix(
    *,
    plan: dict[str, Any],
    plan_raw: bytes,
    registry: dict[str, Any],
    registry_raw: bytes,
    component_lock: dict[str, Any],
    component_raw: bytes,
    benchmark_repository: pathlib.Path,
) -> dict[str, Any]:
    try:
        toolchains = validate_toolchain_registry(registry)
    except PublicReplayPlanError as error:
        raise ProfileMatrixError(str(error)) from error
    registry_sha256 = hashlib.sha256(registry_raw).hexdigest()
    bindings = validate_plan(plan, toolchains, registry_sha256)
    components, exports = validate_component_lock(
        component_lock,
        {entry["lean_toolchain"] for entry in toolchains.values()},
    )
    _canonical_binding(plan, plan_raw, "historical replay plan")
    _canonical_binding(registry, registry_raw, "toolchain registry")
    _canonical_binding(component_lock, component_raw, "component lock")
    images = []
    for commit, binding in sorted(bindings.items()):
        benchmark = binding["benchmark"]
        inspection = inspect_benchmark(benchmark_repository, commit, binding)
        profile_components = {
            **components,
            "lean4export": exports[benchmark["toolchain"]],
        }
        images.append(
            {
                "benchmark_commit": commit,
                **inspection,
                "toolchain": benchmark["toolchain"],
                "lean_toolchain_blob_sha256": benchmark["lean_toolchain_blob_sha256"],
                "request_count": binding["request_count"],
                "result_count": binding["result_count"],
                "problem_ids": sorted(binding["problems"]),
                "profile_lock": {
                    "schema_version": 1,
                    "benchmark_repository": "leanprover/lean-eval",
                    "benchmark_commit": commit,
                    "toolchain": benchmark["toolchain"],
                    "runner_profile": component_lock["runner_profile"],
                    "go_toolchain": component_lock["go_toolchain"],
                    "rust_toolchain": component_lock["rust_toolchain"],
                    "cache_state": component_lock["cache_state"],
                    "measurement_command": component_lock["measurement_command"],
                    "components": profile_components,
                },
                "qualification_status": "unqualified",
            }
        )
    return {
        "schema_version": 1,
        "kind": "historical_public_replay_profile_matrix",
        "plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
        "toolchain_registry_sha256": registry_sha256,
        "component_lock_sha256": hashlib.sha256(component_raw).hexdigest(),
        "qualification_status": "unqualified",
        "qualification_requirements": [
            "historical_public_runner_v1",
            "immutable_registry_publication_v1",
            "cloudflare_staging_runtime_probe_v1",
        ],
        "image_count": len(images),
        "toolchain_count": len(exports),
        "request_count": sum(image["request_count"] for image in images),
        "result_count": sum(image["result_count"] for image in images),
        "images": images,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=pathlib.Path)
    parser.add_argument("--toolchain-registry", required=True, type=pathlib.Path)
    parser.add_argument("--component-lock", required=True, type=pathlib.Path)
    parser.add_argument("--benchmark-repository", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        plan, plan_raw = _canonical_input(args.plan, MAX_PLAN_BYTES, "plan")
        registry, registry_raw = _canonical_input(
            args.toolchain_registry, MAX_REGISTRY_BYTES, "toolchain registry"
        )
        component_lock, component_raw = _canonical_input(
            args.component_lock, MAX_COMPONENT_BYTES, "component lock"
        )
        result = build_matrix(
            plan=plan,
            plan_raw=plan_raw,
            registry=registry,
            registry_raw=registry_raw,
            component_lock=component_lock,
            component_raw=component_raw,
            benchmark_repository=args.benchmark_repository,
        )
        _write_exclusive(args.output, result)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ProfileMatrixError,
        ValueError,
    ) as error:
        print(f"historical-replay-profile-matrix: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
