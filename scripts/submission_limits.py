#!/usr/bin/env python3
"""Compressed-source limits shared by issue and service submission lanes."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


DEFAULT_SIZE_CAP_BYTES = 1_000_000_000
CFSG_PROBLEM_ID = "classification_finite_simple_groups"
CFSG_SIZE_CAP_BYTES = 10_000_000_000


def source_size_cap(metadata: dict) -> int:
    # Service metadata selects one exact problem. Issue metadata contains the
    # workspace names discovered from the frozen checkout, never issue prose.
    if "problem_id" in metadata:
        is_cfsg = metadata["problem_id"] == CFSG_PROBLEM_ID
    else:
        problems = metadata.get("problem_ids", [])
        if not isinstance(problems, list) or not all(isinstance(p, str) for p in problems):
            raise ValueError("metadata.problem_ids must be an array of strings")
        is_cfsg = CFSG_PROBLEM_ID in problems
    return CFSG_SIZE_CAP_BYTES if is_cfsg else DEFAULT_SIZE_CAP_BYTES


def check_source_size(source_tar: pathlib.Path, metadata: dict) -> tuple[int, int]:
    size = source_tar.stat().st_size
    cap = source_size_cap(metadata)
    if size > cap:
        raise ValueError(f"source tarball is {size} bytes, over the {cap}-byte audit cap")
    return size, cap


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tar", type=pathlib.Path, required=True)
    parser.add_argument("--metadata", type=pathlib.Path, required=True)
    parser.add_argument("--github-output", type=pathlib.Path)
    args = parser.parse_args()
    try:
        metadata = json.loads(args.metadata.read_text())
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        cap = source_size_cap(metadata)
        size = args.source_tar.stat().st_size
        if args.github_output:
            with args.github_output.open("a") as output:
                output.write(f"size_bytes={size}\ncap_bytes={cap}\n")
        check_source_size(args.source_tar, metadata)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(f"submission tarball: {size} bytes (cap: {cap} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
