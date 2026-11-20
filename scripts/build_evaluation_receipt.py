#!/usr/bin/env python3
"""Freeze source-free evaluation timing on the original trusted attempt runner."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re

CONTEXT_FIELDS = {
    "submission_id", "workflow_commit", "workflow_run_id", "source_repository",
    "source_commit", "source_metadata_sha256", "benchmark_commit", "toolchain",
    "problem_id", "statement_revision", "runner_tier",
}
FIELDS = CONTEXT_FIELDS | {
    "schema_version", "workflow_run_attempt", "started_at", "completed_at",
    "evaluate_result", "results_sha256",
}


def timestamp(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or value.startswith("0000-"):
        raise ValueError(f"{label} is not canonical UTC milliseconds")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is not canonical UTC milliseconds") from error
    if parsed.tzinfo != dt.timezone.utc or parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") != value:
        raise ValueError(f"{label} is not canonical UTC milliseconds")
    return parsed


def validate_context(context: dict[str, object]) -> None:
    if set(context) != CONTEXT_FIELDS:
        raise ValueError("evaluation receipt context fields are invalid")
    patterns = {
        "submission_id": r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        "workflow_commit": r"[0-9a-f]{40}", "source_commit": r"[0-9a-f]{40}",
        "source_metadata_sha256": r"[0-9a-f]{64}", "benchmark_commit": r"[0-9a-f]{40}",
        "source_repository": r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
        "workflow_run_id": r"[1-9][0-9]*", "problem_id": r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}",
        "toolchain": r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta)[0-9]+)?",
    }
    for name, pattern in patterns.items():
        if not isinstance(context[name], str) or re.fullmatch(pattern, context[name]) is None:
            raise ValueError(f"evaluation receipt {name} is invalid")
    if type(context["statement_revision"]) is not int or context["statement_revision"] < 1:
        raise ValueError("evaluation receipt statement_revision is invalid")
    if context["runner_tier"] not in {"standard", "large"}:
        raise ValueError("evaluation receipt runner_tier is invalid")


def results_digest(path: pathlib.Path | None) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path is not None else None


def build(*, context: dict[str, object], workflow_run_attempt: int, started_at: str,
          completed_at: str, evaluate_result: str, results: pathlib.Path | None) -> dict[str, object]:
    validate_context(context)
    if type(workflow_run_attempt) is not int or workflow_run_attempt < 1:
        raise ValueError("evaluation receipt workflow_run_attempt is invalid")
    if timestamp(completed_at, "completed_at") <= timestamp(started_at, "started_at"):
        raise ValueError("evaluation terminal time must follow its start")
    if evaluate_result not in {"success", "failure"}:
        raise ValueError("evaluation receipt outcome is invalid")
    if evaluate_result == "success" and results is None:
        raise ValueError("successful evaluation receipt requires results")
    return {**context, "schema_version": 1, "workflow_run_attempt": workflow_run_attempt,
            "started_at": started_at, "completed_at": completed_at,
            "evaluate_result": evaluate_result, "results_sha256": results_digest(results)}


def validate(value: object, *, context: dict[str, object], current_run_attempt: int,
             evaluate_result: str, results: pathlib.Path | None) -> dict[str, object]:
    validate_context(context)
    if not isinstance(value, dict) or set(value) != FIELDS or type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        raise ValueError("evaluation terminal receipt fields or version are invalid")
    if type(value["statement_revision"]) is not int:
        raise ValueError("evaluation terminal receipt statement_revision is invalid")
    if any(value[key] != expected for key, expected in context.items()):
        raise ValueError("evaluation terminal receipt identity does not match the selected attempt")
    original_attempt = value["workflow_run_attempt"]
    if type(current_run_attempt) is not int or type(original_attempt) is not int or not 1 <= original_attempt <= current_run_attempt:
        raise ValueError("evaluation terminal receipt run attempt is invalid")
    expected = build(context=context, workflow_run_attempt=original_attempt,
                     started_at=value["started_at"], completed_at=value["completed_at"],
                     evaluate_result=evaluate_result, results=results)
    if value != expected:
        raise ValueError("evaluation terminal receipt outcome or results digest disagrees")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in sorted(CONTEXT_FIELDS):
        parser.add_argument("--" + name.replace("_", "-"), required=True,
                            type=int if name == "statement_revision" else str)
    parser.add_argument("--workflow-run-attempt", type=int, required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--evaluate-result", required=True, choices=("success", "failure"))
    parser.add_argument("--results", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        value = build(context={name: getattr(args, name) for name in CONTEXT_FIELDS},
                      workflow_run_attempt=args.workflow_run_attempt, started_at=args.started_at,
                      completed_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                      evaluate_result=args.evaluate_result, results=args.results)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # An existing receipt must never acquire a callback/retry clock.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
