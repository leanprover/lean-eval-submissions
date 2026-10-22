#!/usr/bin/env python3
"""Bind numeric acceptance measurements to immutable accepted result records."""

from __future__ import annotations
import argparse
import json
import pathlib
from acceptance_measurement import measurement_series, validate_measurement

COMPONENTS = {
    "comparator": "d03acab154d269c06e60e4de7e4cc85deebff94b",
    "lean4export": "076e8e57707e813375e8f9da8bf989799ace9680",
    "nanoda": "68d5ca9db226849b41a6fff59d796ff19d0a8840",
    "landrun": "5ed4a3db3a4ad930d577215c6b9abaa19df7f99f",
}


def build(
    *,
    records,
    metrics,
    owner,
    model,
    passed,
    benchmark_commit,
    results_commit,
    toolchain,
    workflow_commit,
    workflow_run_id,
    attempt,
    measured_at,
    architecture,
    submission=None,
):
    receipts = []
    expected_numeric = {"status", "reason_code"} | {
        phase + suffix
        for phase in ("build", "checker")
        for suffix in (
            "_wall_time_ms",
            "_retired_instructions",
            "_retired_instructions_unavailable_reason",
        )
    }
    for problem in passed:
        matches = [
            r
            for r in records
            if r["problem_id"] == problem
            and r["declared_model"] == model
            and r["benchmark_commit"] == benchmark_commit
        ]
        if not matches:
            # Sticky credit may already belong to an older accepted source.
            # Its statistics must never be replaced by this resubmission.
            continue
        if len(matches) != 1:
            raise ValueError(
                "accepted identity must resolve to one exact benchmark record"
            )
        record = matches[0]
        if submission is not None and any(
            record["submission"][key] != submission[key]
            for key in ("kind", "repo", "ref")
        ):
            continue
        if (
            not isinstance(metrics.get(problem), dict)
            or set(metrics[problem]) != expected_numeric
        ):
            raise ValueError("numeric artifact contains unknown or missing fields")
        measurement = {
            "schema_version": 1,
            "kind": "acceptance-build",
            "result_id": record["result_id"],
            "owner_login": owner.lower(),
            "declared_model": model,
            "problem_id": problem,
            "statement_revision": record["statement_revision"],
            "benchmark_commit": benchmark_commit,
            "results_commit": results_commit,
            "toolchain": toolchain,
            "workflow_commit": workflow_commit,
            "workflow_run_id": workflow_run_id,
            "attempt": attempt,
            "measured_at": measured_at,
            "runner_class": "github-ubuntu-latest-acceptance-v1",
            "architecture": architecture,
            "cache_state": "solution-cold-dependencies-cached",
            "components": COMPONENTS,
            **metrics[problem],
        }
        measurement["series"] = measurement_series(measurement)
        validate_measurement(measurement)
        receipts.append(measurement)
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("metadata", "results", "metrics", "results_root", "output"):
        parser.add_argument(
            "--" + field.replace("_", "-"), type=pathlib.Path, required=True
        )
    for field in ("benchmark_commit", "results_commit", "toolchain", "workflow_commit"):
        parser.add_argument("--" + field.replace("_", "-"), required=True)
    for field in ("workflow_run_id", "attempt"):
        parser.add_argument("--" + field.replace("_", "-"), type=int, required=True)
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text())
    results = json.loads(args.results.read_text())
    metrics = json.loads(args.metrics.read_text())
    owner = metadata["submitted_by"].lower()
    if not __import__("re").fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?", owner):
        raise ValueError("invalid owner")
    records = json.loads(
        (args.results_root / "results" / (owner + ".json")).read_text()
    )["results"]
    receipts = build(
        records=records,
        metrics=metrics["problems"],
        owner=owner,
        model=metadata["model"],
        passed=results["passed"],
        benchmark_commit=args.benchmark_commit,
        results_commit=args.results_commit,
        toolchain=args.toolchain,
        workflow_commit=args.workflow_commit,
        workflow_run_id=args.workflow_run_id,
        attempt=args.attempt,
        measured_at=metrics["measured_at"],
        architecture=metrics["architecture"],
        submission={
            "kind": metadata["submission_kind"],
            "repo": metadata["submission_repo"],
            "ref": metadata["submission_ref"],
        },
    )
    args.output.mkdir(parents=True, exist_ok=True)
    for receipt in receipts:
        (args.output / (receipt["result_id"] + ".json")).write_text(
            json.dumps(receipt, sort_keys=True) + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
