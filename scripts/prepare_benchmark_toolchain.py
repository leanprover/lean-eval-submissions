#!/usr/bin/env python3
"""Resolve the source's approved toolchain using the frozen trusted benchmark.

Archive mode always retains source, even when its request is unsupported.
Evaluation repeats the resolution and checks the archived selection before
installing Lean or elaborating any submission code. Historical snapshots keep
their single pinned toolchain and preprimed replay does not call this helper.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


class ToolchainRequestError(ValueError):
    """The archived source requests an unsupported evaluation configuration."""


def prepare(benchmark: Path, archive: Path, metadata: Path, *, archive_mode: bool = False,
            expected: str | None = None, apply: bool = False, regenerate: bool = False) -> str:
    benchmark = benchmark.resolve()
    helper = benchmark / "scripts/select_submission_toolchain.py"
    if helper.is_file():
        command = [sys.executable, str(helper), "--source-tar", str(archive.resolve())]
        data = json.loads(metadata.read_text())
        if "problem_id" in data:
            command.extend(["--problem", data["problem_id"]])
        if archive_mode:
            command.append("--archive")
        if expected is not None:
            command.extend(["--expected", expected])
        if apply:
            command.append("--apply")
        if regenerate:
            command.append("--regenerate")
        result = subprocess.run(command, cwd=benchmark, text=True, stdout=subprocess.PIPE,
                                stderr=None if regenerate else subprocess.PIPE, check=False)
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode == 3:
            raise ToolchainRequestError("unsupported or invalid submission toolchain request")
        result.check_returncode()
        toolchain = result.stdout.strip()
    else:
        toolchain = (benchmark / "lean-toolchain").read_text().strip()
    if not toolchain or "\n" in toolchain or "\r" in toolchain:
        raise ValueError("benchmark returned an invalid toolchain selection")
    if expected is not None and expected != toolchain:
        raise ValueError("source toolchain does not match the archived evaluation toolchain")
    return toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", required=True, type=Path)
    parser.add_argument("--source-tar", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--expected")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--regenerate", action="store_true")
    parser.add_argument("--rejection-output", type=Path)
    args = parser.parse_args()
    try:
        print(prepare(args.benchmark_root, args.source_tar, args.metadata, archive_mode=args.archive,
                      expected=args.expected, apply=args.apply, regenerate=args.regenerate))
    except ToolchainRequestError as error:
        if args.rejection_output is None or args.expected is None:
            parser.exit(1, f"prepare_benchmark_toolchain: {error}\n")
        args.rejection_output.mkdir(parents=True, exist_ok=True)
        (args.rejection_output / "results.json").write_text(json.dumps({
            "passed": [], "statement_revisions": {},
            "rejection_reason": "unsupported_toolchain_request",
        }) + "\n")
        (args.rejection_output / "summary.json").write_text(json.dumps({
            "run_eval": {"summary": {"total_problems": 0, "attempted_problems": 0,
                                     "succeeded_problems": 0}, "problems": []},
            "overlay_records": [], "rejection_reason": "unsupported_toolchain_request",
        }) + "\n")
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                output.write("rejected=true\n")
        print(args.expected)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"prepare_benchmark_toolchain: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
