#!/usr/bin/env python3
"""Resolve exact Lean toolchains for evidence-backed public replay benchmarks."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from aggregate_public_replay_github_evidence import (
    AggregationError,
    canonical_document_bytes,
    validate_aggregate,
)
from resolve_public_replay_github_evidence import (
    MAX_REQUEST_BYTES,
    _read_bounded,
    _write_exclusive,
    validate_workflow_registry,
)

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
TOOLCHAIN = re.compile(
    r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta)[0-9]+)?\Z"
)
MAX_AGGREGATE_BYTES = 32 * 1024 * 1024
MAX_REGISTRY_BYTES = 512 * 1024


class ToolchainRegistryError(ValueError):
    """A benchmark set cannot be reduced to exact toolchain metadata."""


def _canonical_input(
    path: pathlib.Path, limit: int, label: str
) -> tuple[dict[str, Any], bytes]:
    raw = _read_bounded(path, limit, label)
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ToolchainRegistryError(f"{label} is not readable JSON") from error
    if not isinstance(value, dict) or canonical_document_bytes(value) != raw:
        raise ToolchainRegistryError(f"{label} is not canonical JSON")
    return value, raw


def resolved_benchmark_commits(
    requests: dict[str, Any], aggregate: dict[str, Any]
) -> list[str]:
    by_id = {request["request_id"]: request for request in requests["requests"]}
    commits: set[str] = set()
    for resolution in aggregate["resolutions"]:
        if resolution["status"] != "resolved":
            continue
        request = by_id[resolution["request_id"]]
        if request["benchmark"]["repository"] != "leanprover/lean-eval":
            raise ToolchainRegistryError(
                "resolved benchmark repository is not registered"
            )
        commits.add(request["benchmark"]["commit"])
    if not commits:
        raise ToolchainRegistryError("aggregate has no resolved public replay requests")
    return sorted(commits)


def build_registry(
    commits: list[str], read_toolchain: Callable[[str], bytes]
) -> dict[str, Any]:
    if (
        not commits
        or commits != sorted(set(commits))
        or not all(COMMIT.fullmatch(item) for item in commits)
    ):
        raise ToolchainRegistryError(
            "benchmark commits must be a nonempty set of unique, sorted full SHAs"
        )
    entries = []
    for commit in commits:
        raw = read_toolchain(commit)
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 256:
            raise ToolchainRegistryError(
                f"lean-toolchain at {commit} has an invalid size"
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeError as error:
            raise ToolchainRegistryError(
                f"lean-toolchain at {commit} is not UTF-8"
            ) from error
        toolchain = text.rstrip("\n")
        if not text.endswith("\n") or "\n" in toolchain or "\r" in text:
            raise ToolchainRegistryError(
                f"lean-toolchain at {commit} is not one value with LF-only termination"
            )
        if TOOLCHAIN.fullmatch(toolchain) is None:
            raise ToolchainRegistryError(
                f"lean-toolchain at {commit} is not an exact Lean release"
            )
        entries.append(
            {
                "benchmark_commit": commit,
                "lean_toolchain": toolchain,
                "lean_toolchain_blob_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return {
        "schema_version": 1,
        "kind": "historical_public_replay_toolchain_registry",
        "benchmark_repository": "leanprover/lean-eval",
        "commit_count": len(entries),
        "commits": entries,
    }


def _git_reader(repository: pathlib.Path) -> Callable[[str], bytes]:
    def read(commit: str) -> bytes:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repository), "show", f"{commit}:lean-toolchain"],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ToolchainRegistryError(
                f"cannot read lean-toolchain from benchmark commit {commit}"
            ) from error
        return completed.stdout

    return read


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", required=True, type=pathlib.Path)
    parser.add_argument("--evidence-aggregate", required=True, type=pathlib.Path)
    parser.add_argument("--workflow-registry", required=True, type=pathlib.Path)
    parser.add_argument("--benchmark-repository", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        requests, requests_raw = _canonical_input(
            args.requests, MAX_REQUEST_BYTES, "resolution requests"
        )
        aggregate, _ = _canonical_input(
            args.evidence_aggregate, MAX_AGGREGATE_BYTES, "evidence aggregate"
        )
        workflow_registry, workflow_raw = _canonical_input(
            args.workflow_registry, MAX_REGISTRY_BYTES, "workflow registry"
        )
        validate_workflow_registry(workflow_registry)
        validate_aggregate(
            aggregate,
            requests,
            hashlib.sha256(requests_raw).hexdigest(),
            workflow_registry,
            hashlib.sha256(workflow_raw).hexdigest(),
        )
        if (
            not args.benchmark_repository.is_dir()
            or args.benchmark_repository.is_symlink()
        ):
            raise ToolchainRegistryError(
                "benchmark repository must be one real directory"
            )
        output = build_registry(
            resolved_benchmark_commits(requests, aggregate),
            _git_reader(args.benchmark_repository),
        )
        _write_exclusive(args.output, output)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        AggregationError,
        ToolchainRegistryError,
        ValueError,
    ) as error:
        print(f"public-replay-toolchains: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
