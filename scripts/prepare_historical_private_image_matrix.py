#!/usr/bin/env python3
"""Build the bounded image matrix for historical private archive replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from collections import Counter
from typing import Any

from prepare_historical_private_replay import (
    PrivateReplayPlanError,
    canonical,
    load_json,
    validate_legacy_unavailability_plan,
)
from prepare_historical_replay_profile_matrix import (
    COMMIT,
    DIGEST,
    TOOLCHAIN,
    ProfileMatrixError,
    inspect_benchmark,
    validate_component_lock,
)


EXPECTED_IMAGE_COUNT = 63
EXPECTED_BOUND_RESULT_COUNT = 639
EXPECTED_PUBLIC_SOURCE_COUNT = 21
EXPECTED_DERIVED_SOURCE_COUNT = 42
PROBLEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


class PrivateImageMatrixError(ValueError):
    """The canonical private replay plan cannot produce an exact image matrix."""


def _canonical_input(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        value, raw = load_json(path, label)
    except PrivateReplayPlanError as error:
        raise PrivateImageMatrixError(str(error)) from error
    return value, raw


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _closed(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PrivateImageMatrixError(f"{label} fields are not closed")
    return value


def _validate_public_matrix(
    value: dict[str, Any], component_sha256: str
) -> dict[str, dict[str, Any]]:
    _closed(
        value,
        {
            "schema_version",
            "kind",
            "plan_sha256",
            "toolchain_registry_sha256",
            "component_lock_sha256",
            "qualification_status",
            "qualification_requirements",
            "image_count",
            "toolchain_count",
            "request_count",
            "result_count",
            "images",
        },
        "historical public profile matrix",
    )
    images = value["images"]
    if (
        value["schema_version"] != 1
        or value["kind"] != "historical_public_replay_profile_matrix"
        or value["component_lock_sha256"] != component_sha256
        or value["qualification_status"] != "unqualified"
        or not isinstance(images, list)
        or value["image_count"] != len(images)
    ):
        raise PrivateImageMatrixError("historical public profile matrix identity is invalid")
    for field in (
        "plan_sha256",
        "toolchain_registry_sha256",
        "component_lock_sha256",
    ):
        if not isinstance(value[field], str) or DIGEST.fullmatch(value[field]) is None:
            raise PrivateImageMatrixError("historical public profile matrix digest is invalid")

    indexed: dict[str, dict[str, Any]] = {}
    image_fields = {
        "benchmark_commit",
        "benchmark_tree",
        "lean_toolchain_blob_sha256",
        "manifest_layout",
        "problem_ids",
        "profile_lock",
        "qualification_status",
        "request_count",
        "result_count",
        "toolchain",
        "workspace_count",
    }
    forbidden = {"vm_image_digest", "execution_profile_digest"}
    for image in images:
        image = _closed(image, image_fields, "historical public matrix image")
        commit = image["benchmark_commit"]
        if (
            not isinstance(commit, str)
            or COMMIT.fullmatch(commit) is None
            or commit in indexed
            or image["qualification_status"] != "unqualified"
            or forbidden & set(image)
        ):
            raise PrivateImageMatrixError("historical public matrix image identity is invalid")
        encoded = json.dumps(image, sort_keys=True)
        if any(field in encoded for field in forbidden):
            raise PrivateImageMatrixError(
                "historical public source pin contains a qualification or image digest"
            )
        indexed[commit] = image
    if list(indexed) != sorted(indexed):
        raise PrivateImageMatrixError("historical public matrix images are not sorted")
    return indexed


def _private_bindings(
    plan: dict[str, Any], plan_raw: bytes
) -> dict[str, dict[str, Any]]:
    try:
        # The closed image corpus is derived from the exact retained legacy
        # plan because that artifact carries the already adjudicated source
        # locks. New qualification never imports its public-profile locators.
        validate_legacy_unavailability_plan(plan, plan_raw)
    except PrivateReplayPlanError as error:
        raise PrivateImageMatrixError(str(error)) from error

    bindings: dict[str, dict[str, Any]] = {}
    result_ids: set[str] = set()
    for entry in plan["entries"]:
        if entry["classification"] != "bound":
            continue
        commit = entry.get("benchmark_commit")
        problem = entry.get("problem_id")
        revision = entry.get("statement_revision")
        result_id = entry.get("result_id")
        if (
            not isinstance(commit, str)
            or COMMIT.fullmatch(commit) is None
            or not isinstance(problem, str)
            or PROBLEM.fullmatch(problem) is None
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or not isinstance(result_id, str)
            or result_id in result_ids
        ):
            raise PrivateImageMatrixError("bound private replay entry is invalid")
        result_ids.add(result_id)
        binding = bindings.setdefault(
            commit,
            {"problems": {}, "result_count": 0},
        )
        previous = binding["problems"].setdefault(problem, revision)
        if previous != revision:
            raise PrivateImageMatrixError(
                "one benchmark commit has conflicting statement revisions"
            )
        binding["result_count"] += 1

    if len(bindings) != EXPECTED_IMAGE_COUNT:
        raise PrivateImageMatrixError(
            f"canonical private replay image count is not {EXPECTED_IMAGE_COUNT}"
        )
    if len(result_ids) != EXPECTED_BOUND_RESULT_COUNT:
        raise PrivateImageMatrixError(
            f"canonical bound result count is not {EXPECTED_BOUND_RESULT_COUNT}"
        )
    return bindings


def _profile_lock(
    *,
    commit: str,
    toolchain: str,
    component_lock: dict[str, Any],
    components: dict[str, dict[str, str]],
    exports: dict[str, dict[str, str]],
) -> dict[str, Any]:
    export = exports.get(toolchain)
    if export is None:
        raise PrivateImageMatrixError(
            f"no exact lean4export source pin exists for {toolchain}"
        )
    return {
        "schema_version": 1,
        "benchmark_repository": "leanprover/lean-eval",
        "benchmark_commit": commit,
        "toolchain": toolchain,
        "runner_profile": component_lock["runner_profile"],
        "go_toolchain": component_lock["go_toolchain"],
        "rust_toolchain": component_lock["rust_toolchain"],
        "cache_state": component_lock["cache_state"],
        "measurement_command": component_lock["measurement_command"],
        "components": {**components, "lean4export": export},
    }


def _verify_reused_public_source(
    public: dict[str, Any], generated: dict[str, Any]
) -> None:
    for field in (
        "benchmark_commit",
        "benchmark_tree",
        "toolchain",
        "lean_toolchain_blob_sha256",
        "manifest_layout",
        "workspace_count",
        "profile_lock",
    ):
        if public[field] != generated[field]:
            raise PrivateImageMatrixError(
                f"private source pin differs from historical public matrix: {field}"
            )


def _verify_private_profiles(plan: dict[str, Any], images: dict[str, dict[str, Any]]) -> None:
    commits: set[str] = set()
    for profile in plan["profiles"].values():
        commit = profile["benchmark_commit"]
        image = images.get(commit)
        if image is None or commit in commits:
            raise PrivateImageMatrixError("private replay profiles do not map one-to-one")
        commits.add(commit)
        for field in (
            "benchmark_commit",
            "benchmark_tree",
            "toolchain",
            "lean_toolchain_blob_sha256",
        ):
            if profile[field] != image[field]:
                raise PrivateImageMatrixError(
                    f"private replay profile source differs from image matrix: {field}"
                )
        if profile["checker"] != "nanoda":
            raise PrivateImageMatrixError("private replay profile checker is not nanoda")


def build_matrix(
    *,
    private_plan: dict[str, Any],
    private_plan_raw: bytes,
    public_matrix: dict[str, Any],
    public_matrix_raw: bytes,
    component_lock: dict[str, Any],
    component_lock_raw: bytes,
    benchmark_repository: pathlib.Path,
) -> dict[str, Any]:
    bindings = _private_bindings(private_plan, private_plan_raw)
    exact_toolchains = {
        image["toolchain"] for image in public_matrix.get("images", [])
        if isinstance(image, dict) and isinstance(image.get("toolchain"), str)
    }
    try:
        components, exports = validate_component_lock(component_lock, exact_toolchains)
    except ProfileMatrixError as error:
        raise PrivateImageMatrixError(str(error)) from error
    public_images = _validate_public_matrix(public_matrix, _digest(component_lock_raw))

    images = []
    source_counts: Counter[str] = Counter()
    for commit, binding in sorted(bindings.items()):
        try:
            raw_toolchain = subprocess_git(
                benchmark_repository, "show", f"{commit}:lean-toolchain", maximum=4096
            )
            toolchain_text = raw_toolchain.decode("ascii")
            toolchain = toolchain_text.rstrip("\n")
        except (UnicodeError, ProfileMatrixError) as error:
            raise PrivateImageMatrixError(
                f"exact Lean toolchain is unavailable for {commit}"
            ) from error
        if (
            not toolchain_text.endswith("\n")
            or "\n" in toolchain
            or "\r" in toolchain_text
            or TOOLCHAIN.fullmatch(toolchain) is None
            or toolchain not in exports
        ):
            raise PrivateImageMatrixError(
                f"exact Lean toolchain is unsupported for {commit}"
            )
        benchmark = {
            "repository": "leanprover/lean-eval",
            "commit": commit,
            "toolchain": toolchain,
            "lean_toolchain_blob_sha256": _digest(raw_toolchain),
        }
        binding["benchmark"] = benchmark
        try:
            inspection = inspect_benchmark(
                benchmark_repository,
                commit,
                binding,
            )
        except ProfileMatrixError as error:
            raise PrivateImageMatrixError(str(error)) from error
        source = (
            "historical_public_matrix_v1"
            if commit in public_images
            else "exact_benchmark_git_object"
        )
        source_counts[source] += 1
        image = {
            "benchmark_commit": commit,
            **inspection,
            "toolchain": toolchain,
            "lean_toolchain_blob_sha256": benchmark["lean_toolchain_blob_sha256"],
            "result_count": binding["result_count"],
            "problem_ids": sorted(binding["problems"]),
            "profile_lock": _profile_lock(
                commit=commit,
                toolchain=toolchain,
                component_lock=component_lock,
                components=components,
                exports=exports,
            ),
            "source_pin_origin": source,
        }
        public = public_images.get(commit)
        if public is not None:
            _verify_reused_public_source(public, image)
        images.append(image)

    if source_counts != {
        "historical_public_matrix_v1": EXPECTED_PUBLIC_SOURCE_COUNT,
        "exact_benchmark_git_object": EXPECTED_DERIVED_SOURCE_COUNT,
    }:
        raise PrivateImageMatrixError("historical private source coverage changed")
    indexed = {image["benchmark_commit"]: image for image in images}
    _verify_private_profiles(private_plan, indexed)
    return {
        "schema_version": 1,
        "kind": "historical_private_replay_image_matrix",
        "benchmark_repository": "leanprover/lean-eval",
        "private_plan_sha256": _digest(private_plan_raw),
        "historical_public_profile_matrix_sha256": _digest(public_matrix_raw),
        "historical_public_component_lock_sha256": _digest(component_lock_raw),
        "checker": "nanoda",
        "image_count": len(images),
        "toolchain_count": len(exports),
        "result_count": sum(image["result_count"] for image in images),
        "reused_public_source_count": source_counts["historical_public_matrix_v1"],
        "derived_exact_source_count": source_counts["exact_benchmark_git_object"],
        "images": images,
    }


def subprocess_git(
    repository: pathlib.Path, *arguments: str, maximum: int
) -> bytes:
    from prepare_historical_replay_profile_matrix import _git

    return _git(repository, *arguments, maximum=maximum)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-plan", required=True, type=pathlib.Path)
    parser.add_argument("--public-profile-matrix", required=True, type=pathlib.Path)
    parser.add_argument("--public-component-lock", required=True, type=pathlib.Path)
    parser.add_argument("--benchmark-repository", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        plan, plan_raw = _canonical_input(args.private_plan, "private replay plan")
        public, public_raw = _canonical_input(
            args.public_profile_matrix, "historical public profile matrix"
        )
        components, components_raw = _canonical_input(
            args.public_component_lock, "historical public component lock"
        )
        result = build_matrix(
            private_plan=plan,
            private_plan_raw=plan_raw,
            public_matrix=public,
            public_matrix_raw=public_raw,
            component_lock=components,
            component_lock_raw=components_raw,
            benchmark_repository=args.benchmark_repository.resolve(),
        )
        output = args.output.resolve()
        if output.exists() or output.is_symlink():
            raise PrivateImageMatrixError("output already exists")
        output.write_bytes(canonical(result))
    except (
        OSError,
        PrivateImageMatrixError,
        PrivateReplayPlanError,
        ProfileMatrixError,
        ValueError,
    ) as error:
        print(f"historical-private-image-matrix: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
