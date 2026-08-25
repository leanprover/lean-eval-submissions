#!/usr/bin/env python3
"""Prepare and validate the closed historical public replay runner handoff."""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from typing import Any

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_SAFE_INTEGER = 9_007_199_254_740_991
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REPOSITORY = re.compile(
    r"(?!\.{1,2}/)(?![^/]+/\.{1,2}\Z)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z"
)
REQUEST_ID = re.compile(r"prr_[0-9a-f]{64}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
PROBLEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
TOOLCHAIN = re.compile(
    r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta)[0-9]+)?\Z"
)


class HistoricalPublicRunnerError(ValueError):
    """A historical public controller or runner boundary is not exact."""


def canonical_document_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise HistoricalPublicRunnerError(f"{label} fields are not closed")
    return value


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise HistoricalPublicRunnerError(f"{label} is invalid")
    return value


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise HistoricalPublicRunnerError(f"{label} is invalid")
    return value


def _read(path: pathlib.Path, maximum: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
        if not path.is_file() or path.is_symlink() or not 1 <= size <= maximum:
            raise HistoricalPublicRunnerError(f"{label} is not a bounded regular file")
        return path.read_bytes()
    except OSError as error:
        raise HistoricalPublicRunnerError(f"{label} is unavailable") from error


def load_canonical_json(
    path: pathlib.Path, label: str, maximum: int = MAX_JSON_BYTES
) -> tuple[dict[str, Any], bytes]:
    raw = _read(path, maximum, label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise HistoricalPublicRunnerError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict) or canonical_document_bytes(value) != raw:
        raise HistoricalPublicRunnerError(f"{label} is not canonical JSON")
    return value, raw


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise HistoricalPublicRunnerError("source archive cannot be hashed") from error
    return digest.hexdigest()


def validate_contract(value: Any) -> dict[str, Any]:
    contract = _object(
        value,
        {
            "schema_version",
            "kind",
            "contract",
            "runner_command",
            "measurement_command",
            "memory_limit_bytes",
            "wall_time_limit_ms",
            "source_archive",
            "network",
        },
        "historical public runner contract",
    )
    archive = _object(
        contract["source_archive"],
        {
            "format",
            "member_prefix",
            "maximum_compressed_bytes",
            "maximum_expanded_bytes",
            "maximum_members",
        },
        "historical public runner source archive contract",
    )
    network = _object(
        contract["network"],
        {"controller_fetch_phase", "untrusted_execution_phase"},
        "historical public runner network contract",
    )
    if (
        type(contract["schema_version"]) is not int
        or contract["schema_version"] != 1
        or contract["kind"] != "historical_public_runner_contract"
        or contract["contract"] != "historical_public_runner_v1"
        or contract["runner_command"] != ["/opt/lean-eval/historical-public-runner"]
        or contract["measurement_command"] != ["/opt/lean-eval/replay-measure"]
        or contract["memory_limit_bytes"] != 12 * 1024**3
        or contract["wall_time_limit_ms"] != 19_800_000
        or archive["format"] != "git_archive_tar_gzip_v1"
        or archive["member_prefix"] != "source"
        or network["controller_fetch_phase"] != "public_https_exact_commits_only"
        or network["untrusted_execution_phase"] != "disabled"
    ):
        raise HistoricalPublicRunnerError("historical public runner contract changed")
    _integer(
        archive["maximum_compressed_bytes"],
        "maximum compressed source archive size",
        1,
        1024**3,
    )
    _integer(
        archive["maximum_expanded_bytes"],
        "maximum expanded source archive size",
        archive["maximum_compressed_bytes"],
        2 * 1024**3,
    )
    _integer(archive["maximum_members"], "maximum source archive members", 1, 100_000)
    return contract


def _validate_profile_lock(value: Any, benchmark: dict[str, Any]) -> dict[str, Any]:
    lock = _object(
        value,
        {
            "schema_version",
            "benchmark_repository",
            "benchmark_commit",
            "toolchain",
            "runner_profile",
            "go_toolchain",
            "rust_toolchain",
            "cache_state",
            "measurement_command",
            "components",
        },
        "historical public profile lock",
    )
    if (
        type(lock["schema_version"]) is not int
        or lock["schema_version"] != 1
        or lock["benchmark_repository"] != benchmark["repository"]
        or lock["benchmark_commit"] != benchmark["commit"]
        or lock["toolchain"] != benchmark["toolchain"]
        or lock["runner_profile"] != "cloudflare-sandbox-standard-4-v1"
        or lock["cache_state"] != "cold"
        or lock["measurement_command"] != ["/opt/lean-eval/replay-measure"]
        or not isinstance(lock["components"], dict)
        or set(lock["components"]) != {"comparator", "landrun", "lean4export", "nanoda"}
    ):
        raise HistoricalPublicRunnerError("historical public profile lock differs")
    for name, component_value in lock["components"].items():
        component = _object(
            component_value, {"repository", "commit"}, f"profile component {name}"
        )
        _match(
            REPOSITORY, component["repository"], f"profile component {name} repository"
        )
        _match(COMMIT, component["commit"], f"profile component {name} commit")
    if (
        not isinstance(lock["go_toolchain"], str)
        or re.fullmatch(r"go[0-9]+\.[0-9]+\.[0-9]+", lock["go_toolchain"]) is None
    ):
        raise HistoricalPublicRunnerError("profile Go toolchain is invalid")
    if (
        not isinstance(lock["rust_toolchain"], str)
        or re.fullmatch(r"rustc-[0-9]+\.[0-9]+\.[0-9]+", lock["rust_toolchain"]) is None
    ):
        raise HistoricalPublicRunnerError("profile Rust toolchain is invalid")
    return lock


def validate_handoff(value: Any, contract: dict[str, Any]) -> dict[str, Any]:
    handoff = _object(
        value,
        {
            "schema_version",
            "kind",
            "contract",
            "contract_sha256",
            "plan_sha256",
            "profile_matrix_sha256",
            "request_id",
            "source",
            "benchmark",
            "result",
            "profile",
            "checker",
            "network",
            "untrusted_environment",
        },
        "historical public runner handoff",
    )
    if (
        type(handoff["schema_version"]) is not int
        or handoff["schema_version"] != 1
        or handoff["kind"] != "historical_public_runner_handoff"
        or handoff["contract"] != "historical_public_runner_v1"
        or handoff["checker"] != "nanoda"
        or handoff["network"] != contract["network"]
        or handoff["untrusted_environment"] != {}
    ):
        raise HistoricalPublicRunnerError("historical public runner handoff changed")
    for field in ("contract_sha256", "plan_sha256", "profile_matrix_sha256"):
        _match(DIGEST, handoff[field], f"historical public runner {field}")
    _match(REQUEST_ID, handoff["request_id"], "historical public request id")
    source = _object(
        handoff["source"],
        {
            "repository",
            "commit",
            "tree",
            "visibility",
            "archive_format",
            "archive_member_prefix",
            "archive_sha256",
            "archive_size_bytes",
        },
        "historical public source",
    )
    _match(REPOSITORY, source["repository"], "historical public source repository")
    _match(COMMIT, source["commit"], "historical public source commit")
    _match(COMMIT, source["tree"], "historical public source tree")
    _match(DIGEST, source["archive_sha256"], "historical public source archive")
    if (
        source["visibility"] != "public"
        or source["archive_format"] != contract["source_archive"]["format"]
        or source["archive_member_prefix"]
        != contract["source_archive"]["member_prefix"]
    ):
        raise HistoricalPublicRunnerError("historical public source boundary changed")
    _integer(
        source["archive_size_bytes"],
        "historical public source archive size",
        1,
        contract["source_archive"]["maximum_compressed_bytes"],
    )
    benchmark = _object(
        handoff["benchmark"],
        {"repository", "commit", "tree", "toolchain", "lean_toolchain_blob_sha256"},
        "historical public benchmark",
    )
    if benchmark["repository"] != "leanprover/lean-eval":
        raise HistoricalPublicRunnerError(
            "historical public benchmark repository changed"
        )
    _match(COMMIT, benchmark["commit"], "historical public benchmark commit")
    _match(COMMIT, benchmark["tree"], "historical public benchmark tree")
    _match(TOOLCHAIN, benchmark["toolchain"], "historical public benchmark toolchain")
    _match(
        DIGEST,
        benchmark["lean_toolchain_blob_sha256"],
        "historical public benchmark toolchain blob",
    )
    result = _object(
        handoff["result"],
        {
            "result_id",
            "problem_id",
            "statement_revision",
            "results_repository",
            "results_commit",
            "result_tree_digest",
        },
        "historical public result",
    )
    _match(RESULT_ID, result["result_id"], "historical public result id")
    _match(PROBLEM, result["problem_id"], "historical public problem id")
    _integer(
        result["statement_revision"],
        "historical statement revision",
        1,
        MAX_SAFE_INTEGER,
    )
    if result["results_repository"] != "leanprover/lean-eval-submissions":
        raise HistoricalPublicRunnerError("historical results repository changed")
    _match(COMMIT, result["results_commit"], "historical results commit")
    _match(DIGEST, result["result_tree_digest"], "historical result tree digest")
    profile = _object(
        handoff["profile"],
        {"matrix_entry_sha256", "qualification_status", "profile_lock"},
        "historical public runner profile",
    )
    _match(DIGEST, profile["matrix_entry_sha256"], "historical profile matrix entry")
    if profile["qualification_status"] != "unqualified":
        raise HistoricalPublicRunnerError(
            "historical runner cannot claim qualification"
        )
    _validate_profile_lock(profile["profile_lock"], benchmark)
    return handoff


def _validate_json_schema(value: Any, schema_path: pathlib.Path, label: str) -> None:
    try:
        import jsonschema
    except ImportError as error:
        raise HistoricalPublicRunnerError(
            "jsonschema is required by the controller"
        ) from error
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError as error:
        raise HistoricalPublicRunnerError(
            f"{label} does not match its schema"
        ) from error


def _validate_handoff_json_schema(value: Any, root: pathlib.Path) -> None:
    try:
        import jsonschema
        from referencing import Registry, Resource
    except ImportError as error:
        raise HistoricalPublicRunnerError(
            "jsonschema and referencing are required by the controller"
        ) from error
    matrix_schema = json.loads(
        (
            root / "schemas/historical-public-replay-profile-matrix-v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    handoff_schema = json.loads(
        (root / "schemas/historical-public-runner-handoff-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    registry = Registry().with_resource(
        matrix_schema["$id"], Resource.from_contents(matrix_schema)
    )
    try:
        jsonschema.Draft202012Validator(handoff_schema, registry=registry).validate(
            value
        )
    except jsonschema.ValidationError as error:
        raise HistoricalPublicRunnerError(
            "historical public runner handoff does not match its schema"
        ) from error


def _git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_NO_REPLACE_OBJECTS="1",
    )
    return environment


def _git(repository: pathlib.Path, *arguments: str, maximum: int = 4096) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            env=_git_environment(),
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalPublicRunnerError(
            f"exact public Git object is unavailable: {' '.join(arguments)}"
        ) from error
    if len(result.stdout) > maximum:
        raise HistoricalPublicRunnerError("public Git identity exceeds its size limit")
    return result.stdout


def _require_checkout_identity(
    repository: pathlib.Path, repository_name: str, commit: str, label: str
) -> str:
    expected_remote = f"https://github.com/{repository_name}.git"
    actual_remote = (
        _git(repository, "remote", "get-url", "origin").decode("utf-8").strip()
    )
    resolved_commit = (
        _git(repository, "rev-parse", f"{commit}^{{commit}}").decode().strip()
    )
    tree = _git(repository, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    if (
        actual_remote != expected_remote
        or resolved_commit != commit
        or COMMIT.fullmatch(tree) is None
    ):
        raise HistoricalPublicRunnerError(f"{label} checkout identity differs")
    return tree


def _write_git_archive(
    repository: pathlib.Path,
    commit: str,
    destination: pathlib.Path,
    maximum_compressed_bytes: int,
    maximum_tar_bytes: int,
) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise HistoricalPublicRunnerError("source archive output already exists")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = pathlib.Path(temporary_name)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "tar.umask=0022",
                "archive",
                "--format=tar",
                "--prefix=source/",
                commit,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
        )
        if process.stdout is None:
            raise HistoricalPublicRunnerError("git archive did not expose its output")
        with (
            temporary.open("wb") as raw_output,
            gzip.GzipFile(
                fileobj=raw_output, mode="wb", filename="", mtime=0
            ) as output,
        ):
            tar_bytes = 0
            for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                tar_bytes += len(chunk)
                if tar_bytes > maximum_tar_bytes:
                    raise HistoricalPublicRunnerError(
                        "source archive exceeds its expanded tar stream limit"
                    )
                output.write(chunk)
                if raw_output.tell() > maximum_compressed_bytes:
                    raise HistoricalPublicRunnerError(
                        "source archive exceeds its compressed size limit"
                    )
        process.stdout.close()
        process.wait()
        if process.returncode != 0:
            raise HistoricalPublicRunnerError("git archive failed")
        size = temporary.stat().st_size
        if not 1 <= size <= maximum_compressed_bytes:
            raise HistoricalPublicRunnerError(
                "source archive exceeds its compressed size limit"
            )
        os.link(temporary, destination)
        return size, sha256_file(destination)
    except HistoricalPublicRunnerError:
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
        raise
    except OSError as error:
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
        raise HistoricalPublicRunnerError("source archive cannot be created") from error
    finally:
        temporary.unlink(missing_ok=True)


def _find_plan_result(
    plan: dict[str, Any], request_id: str, result_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    requests = [
        item for item in plan["requests"] if item.get("request_id") == request_id
    ]
    if len(requests) != 1:
        raise HistoricalPublicRunnerError(
            "historical request is not unique in the plan"
        )
    request = requests[0]
    results = [
        item for item in request["results"] if item.get("result_id") == result_id
    ]
    if len(results) != 1:
        raise HistoricalPublicRunnerError(
            "historical result is not unique in the request"
        )
    return request, results[0]


def _find_matrix_entry(matrix: dict[str, Any], benchmark_commit: str) -> dict[str, Any]:
    entries = [
        entry
        for entry in matrix["images"]
        if entry.get("benchmark_commit") == benchmark_commit
    ]
    if len(entries) != 1:
        raise HistoricalPublicRunnerError(
            "benchmark is not unique in the profile matrix"
        )
    return entries[0]


def _build_handoff_with_source_identity(
    *,
    plan: dict[str, Any],
    plan_raw: bytes,
    matrix: dict[str, Any],
    matrix_raw: bytes,
    contract: dict[str, Any],
    contract_raw: bytes,
    request_id: str,
    result_id: str,
    source_repository: pathlib.Path,
    benchmark_repository: pathlib.Path,
    source_archive: pathlib.Path,
    source_kind: str,
    source_identity_validator: Callable[[pathlib.Path, dict[str, Any]], str],
) -> dict[str, Any]:
    validate_contract(contract)
    plan_sha256 = sha256_bytes(plan_raw)
    matrix_sha256 = sha256_bytes(matrix_raw)
    if matrix["plan_sha256"] != plan_sha256:
        raise HistoricalPublicRunnerError(
            "profile matrix does not bind the exact plan bytes"
        )
    if matrix["qualification_status"] != "unqualified" or matrix[
        "qualification_requirements"
    ] != [
        "historical_public_runner_v1",
        "immutable_registry_publication_v1",
        "cloudflare_staging_runtime_probe_v1",
    ]:
        raise HistoricalPublicRunnerError(
            "profile matrix qualification boundary changed"
        )
    request, result = _find_plan_result(plan, request_id, result_id)
    source = request["source"]
    benchmark = request["benchmark"]
    entry = _find_matrix_entry(matrix, benchmark["commit"])
    if (
        source["kind"] != source_kind
        or source["visibility"] != "public"
        or entry["toolchain"] != benchmark["toolchain"]
        or entry["lean_toolchain_blob_sha256"]
        != benchmark["lean_toolchain_blob_sha256"]
        or result["problem_id"] not in entry["problem_ids"]
        or entry["qualification_status"] != "unqualified"
    ):
        raise HistoricalPublicRunnerError(
            "plan result differs from its profile matrix entry"
        )
    source_tree = source_identity_validator(source_repository, source)
    _match(COMMIT, source_tree, "verified historical public source tree")
    benchmark_tree = _require_checkout_identity(
        benchmark_repository,
        benchmark["repository"],
        benchmark["commit"],
        "benchmark",
    )
    toolchain_bytes = _git(
        benchmark_repository, "show", f"{benchmark['commit']}:lean-toolchain"
    )
    if (
        benchmark_tree != entry["benchmark_tree"]
        or toolchain_bytes.decode("ascii").strip() != benchmark["toolchain"]
        or sha256_bytes(toolchain_bytes) != benchmark["lean_toolchain_blob_sha256"]
    ):
        raise HistoricalPublicRunnerError(
            "fetched benchmark differs from the matrix binding"
        )
    archive_size, archive_sha256 = _write_git_archive(
        source_repository,
        source["commit"],
        source_archive,
        contract["source_archive"]["maximum_compressed_bytes"],
        (
            contract["source_archive"]["maximum_expanded_bytes"]
            + contract["source_archive"]["maximum_members"] * 4096
            + 1024 * 1024
        ),
    )
    with tempfile.TemporaryDirectory(
        prefix=".historical-public-source-check.", dir=source_archive.parent
    ) as directory:
        extract_source_archive(
            source_archive,
            pathlib.Path(directory) / "tree",
            contract,
            source_tree,
        )
    return {
        "schema_version": 1,
        "kind": "historical_public_runner_handoff",
        "contract": "historical_public_runner_v1",
        "contract_sha256": sha256_bytes(contract_raw),
        "plan_sha256": plan_sha256,
        "profile_matrix_sha256": matrix_sha256,
        "request_id": request_id,
        "source": {
            "repository": source["repository"],
            "commit": source["commit"],
            "tree": source_tree,
            "visibility": "public",
            "archive_format": contract["source_archive"]["format"],
            "archive_member_prefix": contract["source_archive"]["member_prefix"],
            "archive_sha256": archive_sha256,
            "archive_size_bytes": archive_size,
        },
        "benchmark": {
            "repository": benchmark["repository"],
            "commit": benchmark["commit"],
            "tree": benchmark_tree,
            "toolchain": benchmark["toolchain"],
            "lean_toolchain_blob_sha256": benchmark["lean_toolchain_blob_sha256"],
        },
        "result": {
            "result_id": result["result_id"],
            "problem_id": result["problem_id"],
            "statement_revision": result["statement_revision"],
            "results_repository": result["results_repository"],
            "results_commit": result["results_commit"],
            "result_tree_digest": result["result_tree_digest"],
        },
        "profile": {
            "matrix_entry_sha256": sha256_bytes(canonical_document_bytes(entry)),
            "qualification_status": "unqualified",
            "profile_lock": entry["profile_lock"],
        },
        "checker": "nanoda",
        "network": contract["network"],
        "untrusted_environment": {},
    }


def _repository_source_identity(
    repository: pathlib.Path, source: dict[str, Any]
) -> str:
    return _require_checkout_identity(
        repository, source["repository"], source["commit"], "source"
    )


def build_handoff(
    *,
    plan: dict[str, Any],
    plan_raw: bytes,
    matrix: dict[str, Any],
    matrix_raw: bytes,
    contract: dict[str, Any],
    contract_raw: bytes,
    request_id: str,
    result_id: str,
    source_repository: pathlib.Path,
    benchmark_repository: pathlib.Path,
    source_archive: pathlib.Path,
) -> dict[str, Any]:
    """Build the unchanged repository-source runner handoff."""
    return _build_handoff_with_source_identity(
        plan=plan,
        plan_raw=plan_raw,
        matrix=matrix,
        matrix_raw=matrix_raw,
        contract=contract,
        contract_raw=contract_raw,
        request_id=request_id,
        result_id=result_id,
        source_repository=source_repository,
        benchmark_repository=benchmark_repository,
        source_archive=source_archive,
        source_kind="github_repo",
        source_identity_validator=_repository_source_identity,
    )


def _write_exclusive_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    raw = canonical_document_bytes(value)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
    except OSError as error:
        raise HistoricalPublicRunnerError(
            "handoff output cannot be written create-only"
        ) from error


def _validate_matrix_binding(handoff: dict[str, Any], matrix: dict[str, Any]) -> None:
    if matrix.get("qualification_status") != "unqualified" or matrix.get(
        "qualification_requirements"
    ) != [
        "historical_public_runner_v1",
        "immutable_registry_publication_v1",
        "cloudflare_staging_runtime_probe_v1",
    ]:
        raise HistoricalPublicRunnerError(
            "runner matrix qualification boundary changed"
        )
    entry = _find_matrix_entry(matrix, handoff["benchmark"]["commit"])
    if (
        matrix.get("plan_sha256") != handoff["plan_sha256"]
        or sha256_bytes(canonical_document_bytes(entry))
        != handoff["profile"]["matrix_entry_sha256"]
        or entry["benchmark_tree"] != handoff["benchmark"]["tree"]
        or entry["toolchain"] != handoff["benchmark"]["toolchain"]
        or entry["lean_toolchain_blob_sha256"]
        != handoff["benchmark"]["lean_toolchain_blob_sha256"]
        or entry["profile_lock"] != handoff["profile"]["profile_lock"]
        or handoff["result"]["problem_id"] not in entry["problem_ids"]
    ):
        raise HistoricalPublicRunnerError(
            "runner handoff differs from its matrix entry"
        )


def _validate_baked_benchmark(
    handoff: dict[str, Any], benchmark_root: pathlib.Path
) -> None:
    expected = handoff["benchmark"]
    markers = {
        "commit": benchmark_root / ".lean-eval-commit",
        "tree": benchmark_root / ".lean-eval-tree",
        "toolchain": benchmark_root / "lean-toolchain",
    }
    try:
        if any(not path.is_file() or path.is_symlink() for path in markers.values()):
            raise HistoricalPublicRunnerError("baked benchmark marker is not regular")
        values = {
            name: path.read_text(encoding="ascii").strip()
            for name, path in markers.items()
        }
        toolchain_raw = markers["toolchain"].read_bytes()
    except (OSError, UnicodeError) as error:
        raise HistoricalPublicRunnerError(
            "baked benchmark identity is unavailable"
        ) from error
    if (
        values["commit"] != expected["commit"]
        or values["tree"] != expected["tree"]
        or values["toolchain"] != expected["toolchain"]
        or sha256_bytes(toolchain_raw) != expected["lean_toolchain_blob_sha256"]
    ):
        raise HistoricalPublicRunnerError("baked benchmark identity differs")


@contextlib.contextmanager
def _opened_source_archive(path: pathlib.Path) -> Any:
    try:
        with tarfile.open(path, "r|gz") as archive:
            yield archive
    except (OSError, tarfile.TarError) as error:
        raise HistoricalPublicRunnerError(
            "historical source archive is invalid"
        ) from error


def _git_source_tree_oid(
    files: dict[tuple[str, ...], tuple[str, bytes]],
) -> str:
    if not files:
        raise HistoricalPublicRunnerError("historical source tree has no files")
    directories: set[tuple[str, ...]] = {()}
    for path in files:
        directories.update(path[:depth] for depth in range(1, len(path)))
    tree_oids: dict[tuple[str, ...], bytes] = {}
    for directory in sorted(directories, key=len, reverse=True):
        entries: list[tuple[bytes, bool, bytes, bytes]] = []
        for path, (mode, oid) in files.items():
            if path[:-1] == directory:
                name = path[-1].encode("utf-8")
                entries.append((name, False, mode.encode("ascii"), oid))
        for child in directories:
            if len(child) == len(directory) + 1 and child[:-1] == directory:
                name = child[-1].encode("utf-8")
                entries.append((name, True, b"40000", tree_oids[child]))
        entries.sort(key=lambda entry: entry[0] + (b"/" if entry[1] else b""))
        body = b"".join(
            mode + b" " + name + b"\0" + oid for name, _directory, mode, oid in entries
        )
        digest = hashlib.sha1(usedforsecurity=False)
        digest.update(b"tree " + str(len(body)).encode("ascii") + b"\0" + body)
        tree_oids[directory] = digest.digest()
    return tree_oids[()].hex()


def extract_source_archive(
    archive_path: pathlib.Path,
    destination: pathlib.Path,
    contract: dict[str, Any],
    expected_tree: str | None = None,
) -> pathlib.Path:
    limits = contract["source_archive"]
    created = False
    try:
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        created = True
        expanded = 0
        member_count = 0
        seen_names: set[tuple[str, ...]] = set()
        files: dict[tuple[str, ...], tuple[str, bytes]] = {}
        with _opened_source_archive(archive_path) as archive:
            for member in archive:
                member_count += 1
                if member_count > limits["maximum_members"]:
                    raise HistoricalPublicRunnerError(
                        "historical source archive member count is invalid"
                    )
                relative = pathlib.PurePosixPath(member.name)
                if (
                    relative.is_absolute()
                    or not relative.parts
                    or relative.parts[0] != limits["member_prefix"]
                    or any(part in {"", ".", "..", ".git"} for part in relative.parts)
                    or not (member.isfile() or member.isdir())
                    or relative.parts in seen_names
                ):
                    raise HistoricalPublicRunnerError(
                        "historical source archive has an unsafe member"
                    )
                seen_names.add(relative.parts)
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                if len(relative.parts) == 1 or member.mode & 0o777 not in {
                    0o644,
                    0o755,
                }:
                    raise HistoricalPublicRunnerError(
                        "historical source archive has an unsafe file mode"
                    )
                expanded += member.size
                if member.size < 0 or expanded > limits["maximum_expanded_bytes"]:
                    raise HistoricalPublicRunnerError(
                        "historical source archive expands too far"
                    )
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise HistoricalPublicRunnerError(
                        "historical source member cannot be read"
                    )
                output_mode = 0o700 if member.mode & 0o111 else 0o600
                descriptor = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, output_mode
                )
                digest = hashlib.sha1(usedforsecurity=False)
                digest.update(b"blob " + str(member.size).encode("ascii") + b"\0")
                copied = 0
                with source, os.fdopen(descriptor, "wb") as output:
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        digest.update(chunk)
                        output.write(chunk)
                if copied != member.size:
                    raise HistoricalPublicRunnerError(
                        "historical source member size differs"
                    )
                files[relative.parts[1:]] = (
                    "100755" if member.mode & 0o111 else "100644",
                    digest.digest(),
                )
        if member_count == 0:
            raise HistoricalPublicRunnerError(
                "historical source archive member count is invalid"
            )
        source_root = destination / limits["member_prefix"]
        if not source_root.is_dir():
            raise HistoricalPublicRunnerError(
                "historical source archive has no source root"
            )
        tree = _git_source_tree_oid(files)
        if expected_tree is not None and tree != expected_tree:
            raise HistoricalPublicRunnerError(
                "historical source archive differs from its Git tree"
            )
        return source_root
    except (HistoricalPublicRunnerError, OSError, UnicodeError):
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise


def validate_runner_inputs(
    *,
    handoff_path: pathlib.Path,
    source_archive: pathlib.Path,
    contract_path: pathlib.Path,
    matrix_path: pathlib.Path,
    benchmark_root: pathlib.Path,
    scratch: pathlib.Path,
) -> tuple[dict[str, Any], pathlib.Path]:
    handoff, _ = load_canonical_json(handoff_path, "historical public runner handoff")
    contract, contract_raw = load_canonical_json(
        contract_path, "historical public runner contract", 128 * 1024
    )
    matrix, matrix_raw = load_canonical_json(
        matrix_path, "historical public profile matrix"
    )
    validate_contract(contract)
    validate_handoff(handoff, contract)
    if handoff["contract_sha256"] != sha256_bytes(contract_raw):
        raise HistoricalPublicRunnerError("runner contract digest differs")
    if handoff["profile_matrix_sha256"] != sha256_bytes(matrix_raw):
        raise HistoricalPublicRunnerError("runner profile matrix digest differs")
    if not source_archive.is_file() or source_archive.is_symlink():
        raise HistoricalPublicRunnerError("runner source archive is not regular")
    size = source_archive.stat().st_size
    if (
        size != handoff["source"]["archive_size_bytes"]
        or sha256_file(source_archive) != handoff["source"]["archive_sha256"]
    ):
        raise HistoricalPublicRunnerError("runner source archive identity differs")
    _validate_matrix_binding(handoff, matrix)
    _validate_baked_benchmark(handoff, benchmark_root)
    source_root = extract_source_archive(
        source_archive, scratch, contract, handoff["source"]["tree"]
    )
    return handoff, source_root


def _load_authoritative_runtime(path: pathlib.Path) -> Any:
    loader = importlib.machinery.SourceFileLoader("lean_eval_replay_runtime", str(path))
    spec = importlib.util.spec_from_file_location(
        "lean_eval_replay_runtime", path, loader=loader
    )
    if spec is None or spec.loader is None:
        raise HistoricalPublicRunnerError("authoritative runtime cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError) as error:
        raise HistoricalPublicRunnerError(
            "authoritative runtime cannot be loaded"
        ) from error
    return module


def _historical_verdict(
    runtime: Any,
    handoff: dict[str, Any],
    *,
    evaluator_results: Any | None,
    metrics_value: Any,
    file_count: int,
    lines_of_code: int,
    execution_outcome: str | None = None,
) -> dict[str, Any]:
    compatibility_request = {
        "replay_task_id": "rt1_" + "0" * 64,
        "attempt": 1,
        "result": handoff["result"],
    }
    if execution_outcome is None:
        generic = runtime.build_verdict(
            compatibility_request,
            evaluator_results,
            metrics_value,
            file_count,
            lines_of_code,
        )
    else:
        normalized = runtime.normalize_metrics(metrics_value)
        generic = runtime.reported_execution_verdict(
            compatibility_request,
            execution_outcome,
            normalized,
            file_count,
            lines_of_code,
        )
    verdict = {
        "schema_version": 1,
        "request_id": handoff["request_id"],
        "result_id": handoff["result"]["result_id"],
        "execution_outcome": generic["execution_outcome"],
        "checker_outcome": generic["checker_outcome"],
        "failure_reason": generic["failure_reason"],
        "statistics": generic["statistics"],
    }
    return validate_historical_verdict(verdict)


def validate_historical_verdict(value: Any) -> dict[str, Any]:
    verdict = _object(
        value,
        {
            "schema_version",
            "request_id",
            "result_id",
            "execution_outcome",
            "checker_outcome",
            "failure_reason",
            "statistics",
        },
        "historical public runner verdict",
    )
    statistics = _object(
        verdict["statistics"],
        {
            "checker_wall_time_ms",
            "checker_retired_instructions",
            "build_wall_time_ms",
            "build_retired_instructions",
            "lines_of_code",
            "file_count",
        },
        "historical public runner statistics",
    )
    if (
        type(verdict["schema_version"]) is not int
        or verdict["schema_version"] != 1
        or verdict["execution_outcome"] not in {"completed", "crashed", "timed_out"}
        or verdict["failure_reason"] is not None
    ):
        raise HistoricalPublicRunnerError("historical public runner verdict changed")
    _match(REQUEST_ID, verdict["request_id"], "historical public verdict request")
    _match(RESULT_ID, verdict["result_id"], "historical public verdict result")
    if verdict["checker_outcome"] not in (
        {"accepted", "rejected", "declined"}
        if verdict["execution_outcome"] == "completed"
        else {None}
    ):
        raise HistoricalPublicRunnerError(
            "historical public checker outcome is incoherent"
        )
    for field in (
        "checker_wall_time_ms",
        "build_wall_time_ms",
        "lines_of_code",
        "file_count",
    ):
        _integer(statistics[field], f"historical public {field}", 0, MAX_SAFE_INTEGER)
    for field in ("checker_retired_instructions", "build_retired_instructions"):
        counter = statistics[field]
        if not isinstance(counter, dict) or counter.get("status") not in {
            "measured",
            "unavailable",
        }:
            raise HistoricalPublicRunnerError("historical public counter is invalid")
        if counter["status"] == "measured":
            _object(counter, {"status", "value"}, "measured historical counter")
            _integer(
                counter["value"], "historical public counter value", 0, MAX_SAFE_INTEGER
            )
        else:
            _object(counter, {"status", "reason"}, "unavailable historical counter")
            if counter["reason"] not in {
                "counter_not_reported",
                "counter_not_supported",
                "counter_permission_denied",
            }:
                raise HistoricalPublicRunnerError(
                    "historical public counter reason is invalid"
                )
    return verdict


def execute_fixed_runner(
    install: pathlib.Path, workspace: pathlib.Path
) -> dict[str, Any]:
    """Execute one closed handoff after the host has disabled runner networking."""
    os.umask(0o077)
    runtime = _load_authoritative_runtime(install / "replay-authoritative")
    scratch = workspace / "historical-public"
    output = workspace / "historical-public-output"
    metrics = pathlib.Path("/run/lean-eval/metrics.json")
    try:
        handoff, source_root = validate_runner_inputs(
            handoff_path=workspace / "historical-public-request.json",
            source_archive=workspace / "historical-public-source.tar.gz",
            contract_path=install / "historical-public-runner-v1.json",
            matrix_path=install / "historical-public-replay-profile-matrix-v1.json",
            benchmark_root=install / "benchmark",
            scratch=scratch,
        )
        contract, _ = load_canonical_json(
            install / "historical-public-runner-v1.json",
            "historical public runner contract",
            128 * 1024,
        )
        if (
            runtime.MEMORY_LIMIT_BYTES != contract["memory_limit_bytes"]
            or runtime.WALL_TIME_LIMIT_MS != contract["wall_time_limit_ms"]
        ):
            raise HistoricalPublicRunnerError("runner limits differ from the contract")
        # This active probe runs before any source file is inspected or executed. An
        # environment flag is not accepted as evidence of host network isolation.
        runtime.network_probe()
        problem_id = handoff["result"]["problem_id"]
        revision = handoff["result"]["statement_revision"]
        file_count, lines_of_code = runtime.source_statistics(source_root, problem_id)
        metrics.unlink(missing_ok=True)
        returncode, timed_out = runtime.run_process_group(
            [
                sys.executable,
                str(install / "evaluate_submission.py"),
                "--source-dir",
                str(source_root),
                "--generated-root",
                str(install / "benchmark" / "generated"),
                "--manifest-dir",
                str(
                    install / "benchmark" / "manifests" / "problems"
                    if (install / "benchmark" / "manifests" / "problems").is_dir()
                    else install / "benchmark" / "manifests" / "problems.toml"
                ),
                "--output-dir",
                str(output),
                "--repo-root",
                str(install / "benchmark"),
                "--shared-packages",
                str(install / "benchmark" / ".lake" / "packages"),
                "--problem-id",
                problem_id,
                "--statement-revision",
                str(revision),
                "--measurement-command-json",
                json.dumps(contract["measurement_command"], separators=(",", ":")),
                "--authoritative-checker",
                "nanoda",
                "--preprimed-workspaces",
            ],
            {
                "PATH": (
                    f"{install}/bin:{install}/home/.elan/bin:"
                    "/usr/local/bin:/usr/bin:/bin"
                ),
                "HOME": str(install / "home"),
                "COMPARATOR_BIN": str(install / "bin" / "comparator"),
                "COMPARATOR_LANDRUN": "/usr/local/bin/landrun",
            },
            contract["wall_time_limit_ms"] / 1000,
        )
        metrics_value = runtime.load_metrics_after_execution()
        normalized = runtime.normalize_metrics(metrics_value)
        if timed_out:
            return _historical_verdict(
                runtime,
                handoff,
                evaluator_results=None,
                metrics_value=metrics_value,
                file_count=file_count,
                lines_of_code=lines_of_code,
                execution_outcome="timed_out",
            )
        if returncode != 0:
            runtime.require_measurement_after_evaluator_failure(returncode, normalized)
            return _historical_verdict(
                runtime,
                handoff,
                evaluator_results=None,
                metrics_value=metrics_value,
                file_count=file_count,
                lines_of_code=lines_of_code,
                execution_outcome="crashed",
            )
        evaluator_results = runtime.load_json(
            output / "results.json", "evaluator results", 64 * 1024
        )
        return _historical_verdict(
            runtime,
            handoff,
            evaluator_results=evaluator_results,
            metrics_value=metrics_value,
            file_count=file_count,
            lines_of_code=lines_of_code,
        )
    finally:
        metrics.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(output, ignore_errors=True)


def _prepare(args: argparse.Namespace, root: pathlib.Path) -> None:
    plan, plan_raw = load_canonical_json(args.plan, "historical replay plan")
    matrix, matrix_raw = load_canonical_json(
        args.profile_matrix, "historical profile matrix"
    )
    contract, contract_raw = load_canonical_json(
        args.contract, "historical public runner contract", 128 * 1024
    )
    for actual, expected, label in (
        (sha256_bytes(plan_raw), args.expected_plan_sha256, "plan"),
        (
            sha256_bytes(matrix_raw),
            args.expected_profile_matrix_sha256,
            "profile matrix",
        ),
        (sha256_bytes(contract_raw), args.expected_contract_sha256, "runner contract"),
    ):
        if actual != expected:
            raise HistoricalPublicRunnerError(f"exact {label} digest differs")
    _validate_json_schema(
        plan, root / "schemas/historical-public-replay-plan-v1.schema.json", "plan"
    )
    _validate_json_schema(
        matrix,
        root / "schemas/historical-public-replay-profile-matrix-v1.schema.json",
        "profile matrix",
    )
    handoff = build_handoff(
        plan=plan,
        plan_raw=plan_raw,
        matrix=matrix,
        matrix_raw=matrix_raw,
        contract=contract,
        contract_raw=contract_raw,
        request_id=args.request_id,
        result_id=args.result_id,
        source_repository=args.source_repository,
        benchmark_repository=args.benchmark_repository,
        source_archive=args.source_archive,
    )
    validate_handoff(handoff, contract)
    _validate_handoff_json_schema(handoff, root)
    _write_exclusive_json(args.output, handoff)


def _contract_check(args: argparse.Namespace) -> None:
    scratch_parent = args.scratch.parent
    scratch_parent.mkdir(parents=True, exist_ok=True)
    try:
        validate_runner_inputs(
            handoff_path=args.handoff,
            source_archive=args.source_archive,
            contract_path=args.contract,
            matrix_path=args.profile_matrix,
            benchmark_root=args.benchmark_root,
            scratch=args.scratch,
        )
    finally:
        shutil.rmtree(args.scratch, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    subparsers = command.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--plan", required=True, type=pathlib.Path)
    prepare.add_argument("--profile-matrix", required=True, type=pathlib.Path)
    prepare.add_argument("--contract", required=True, type=pathlib.Path)
    prepare.add_argument("--expected-plan-sha256", required=True)
    prepare.add_argument("--expected-profile-matrix-sha256", required=True)
    prepare.add_argument("--expected-contract-sha256", required=True)
    prepare.add_argument("--request-id", required=True)
    prepare.add_argument("--result-id", required=True)
    prepare.add_argument("--source-repository", required=True, type=pathlib.Path)
    prepare.add_argument("--benchmark-repository", required=True, type=pathlib.Path)
    prepare.add_argument("--source-archive", required=True, type=pathlib.Path)
    prepare.add_argument("--output", required=True, type=pathlib.Path)
    contract_check = subparsers.add_parser("contract-check")
    contract_check.add_argument("--handoff", required=True, type=pathlib.Path)
    contract_check.add_argument("--source-archive", required=True, type=pathlib.Path)
    contract_check.add_argument("--contract", required=True, type=pathlib.Path)
    contract_check.add_argument("--profile-matrix", required=True, type=pathlib.Path)
    contract_check.add_argument("--benchmark-root", required=True, type=pathlib.Path)
    contract_check.add_argument("--scratch", required=True, type=pathlib.Path)
    return command


def main() -> int:
    args = parser().parse_args()
    root = pathlib.Path(__file__).parents[1]
    try:
        if args.command == "prepare":
            _prepare(args, root)
        else:
            _contract_check(args)
    except (
        HistoricalPublicRunnerError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        print(f"historical-public-runner: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
