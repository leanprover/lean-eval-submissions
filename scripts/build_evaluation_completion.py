#!/usr/bin/env python3
"""Build the strict trusted evaluation-completion callback document."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re

UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
COMMIT = re.compile(r"[0-9a-f]{40}")
PROBLEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
TOOLCHAIN = re.compile(r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+")


def _object(path: pathlib.Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def build(
    *,
    archive_completion: pathlib.Path,
    results: pathlib.Path | None,
    evaluate_result: str,
    problem_id: str,
    benchmark_commit: str,
    toolchain: str,
    evaluator_version: str,
) -> dict[str, object]:
    if evaluate_result not in {"success", "failure"}:
        raise ValueError("evaluate_result must be success or failure")
    if PROBLEM.fullmatch(problem_id) is None:
        raise ValueError("problem_id is not canonical")
    if COMMIT.fullmatch(benchmark_commit) is None or COMMIT.fullmatch(evaluator_version) is None:
        raise ValueError("benchmark and evaluator versions must be exact commits")
    if TOOLCHAIN.fullmatch(toolchain) is None:
        raise ValueError("toolchain is not canonical")
    archive = _object(archive_completion, "archive completion")
    if set(archive) != {"schema_version", "occurred_at", "locator"} or archive["schema_version"] != 1:
        raise ValueError("archive completion fields or version are invalid")
    locator = archive["locator"]
    if not isinstance(locator, dict) or set(locator) != {
        "schema_version", "submission_id", "archive_repository", "archive_commit",
        "archive_path", "archive_ciphertext_sha256", "encrypted",
    }:
        raise ValueError("archive locator fields are invalid")
    submission_id = locator.get("submission_id")
    if not isinstance(submission_id, str) or UUID7.fullmatch(submission_id) is None:
        raise ValueError("archive locator submission_id is invalid")
    occurred_at = archive["occurred_at"]
    if not isinstance(occurred_at, str) or occurred_at.startswith("0000-"):
        raise ValueError("archive completion timestamp is invalid")
    try:
        archive_time = dt.datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("archive completion timestamp is invalid") from error
    if archive_time.tzinfo != dt.timezone.utc or archive_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" != occurred_at:
        raise ValueError("archive completion timestamp is not canonical UTC milliseconds")
    evaluation_time = archive_time + dt.timedelta(milliseconds=1)
    if evaluate_result == "failure":
        outcome: dict[str, object] = {
            "status": "failed",
            "reason_code": "evaluation_pipeline_failed",
            "retryable": True,
        }
    else:
        if results is None:
            raise ValueError("successful evaluation requires results.json")
        result_document = _object(results, "evaluation results")
        passed = result_document.get("passed")
        if not isinstance(passed, list) or any(not isinstance(item, str) for item in passed):
            raise ValueError("evaluation results passed field is invalid")
        outcome = (
            {"status": "accepted", "evaluator_version": evaluator_version}
            if problem_id in passed
            else {"status": "rejected", "reason_code": "proof_rejected"}
        )
    return {
        "schema_version": 1,
        "submission_id": submission_id,
        "attempt": 1,
        "occurred_at": evaluation_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "benchmark_repository": "leanprover/lean-eval",
        "benchmark_commit": benchmark_commit,
        "toolchain": toolchain,
        "outcome": outcome,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-completion", type=pathlib.Path, required=True)
    parser.add_argument("--results", type=pathlib.Path)
    parser.add_argument("--evaluate-result", choices=("success", "failure"), required=True)
    parser.add_argument("--problem-id", required=True)
    parser.add_argument("--benchmark-commit", required=True)
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--evaluator-version", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        completion = build(
            archive_completion=args.archive_completion,
            results=args.results,
            evaluate_result=args.evaluate_result,
            problem_id=args.problem_id,
            benchmark_commit=args.benchmark_commit,
            toolchain=args.toolchain,
            evaluator_version=args.evaluator_version,
        )
    except ValueError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(completion, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
