#!/usr/bin/env python3
"""Reduce trusted phase files to a source-free per-problem artifact."""

from __future__ import annotations
import argparse
import json
import datetime as dt
import platform
import pathlib
from measure_acceptance_phase import measure


def collect(metrics_dir, passed):
    output = {}
    for problem in passed:
        if not isinstance(problem, str) or not __import__("re").fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", problem
        ):
            raise ValueError("invalid problem identifier")
        fields = {"status": "failed", "reason_code": "measurement_unavailable"}
        for phase in ("build", "checker"):
            fields[phase + "_wall_time_ms"] = None
            fields[phase + "_retired_instructions"] = None
            fields[phase + "_retired_instructions_unavailable_reason"] = (
                "counter_not_reported"
            )
        try:
            path = metrics_dir / (problem + ".json")
            if path.is_symlink() or path.with_suffix(".failed").exists():
                raise ValueError("invalidated metrics")
            if path.stat().st_size > 16384:
                raise ValueError("oversize metrics")
            metrics = measure.validate_metrics(json.loads(path.read_text()))
            for phase, count in (("build", 2), ("checker", 2)):
                record = metrics["phases"][phase]
                if record["invocations"] != count or any(
                    t != {"kind": "exited", "code": 0} for t in record["terminations"]
                ):
                    raise ValueError("incomplete or unsuccessful phase")
            fields.update(status="available", reason_code=None)
            for phase in ("build", "checker"):
                record = metrics["phases"][phase]
                counter = record["retired_instructions"]
                fields[phase + "_wall_time_ms"] = record["wall_time_ms"]
                fields[phase + "_retired_instructions"] = counter.get("value")
                fields[phase + "_retired_instructions_unavailable_reason"] = (
                    counter.get("reason")
                )
        except (OSError, ValueError):
            pass
        output[problem] = fields
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics-dir", type=pathlib.Path, required=True)
    parser.add_argument("--results", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "measured_at": dt.datetime.now(dt.timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "architecture": platform.machine(),
                "components": json.loads((args.metrics_dir / "setup.json").read_text()),
                "problems": collect(
                    args.metrics_dir, json.loads(args.results.read_text())["passed"]
                ),
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
