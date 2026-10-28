#!/usr/bin/env python3
"""Classify compute-reporting coverage without opening submission source."""

from __future__ import annotations
import argparse
import collections
import json
import pathlib


def coverage(records, state):
    if state.get("schema_version") not in (6, 7):
        raise ValueError("expected public State projection v6 or v7")
    acceptance = collections.defaultdict(list)
    for m in state.get("acceptance_measurements", []):
        acceptance[m["result_id"]].append(m)
    historical = collections.defaultdict(list)
    for series in state["historical_replay_series"]:
        historical[series["result_id"]].append(series["replay"])
    unavailable = {
        x["result_id"]: x["reason"] for x in state["historical_replay_unavailability"]
    }
    modern = {x["result_id"]: x for x in state["results"]}
    output = []
    for r in records:
        rid = r["result_id"]
        replays = historical[rid] + (
            [modern[rid]["replay"]] if modern.get(rid, {}).get("replay") else []
        )
        if any(m["status"] == "available" for m in acceptance[rid]) or any(
            m["status"] == "accepted" and m.get("build_wall_time_ms") is not None
            for m in replays
        ):
            status, reason = "measured", None
        elif any(m["status"] in ("queued", "running") for m in replays):
            status, reason = "pending", None
        elif rid in unavailable:
            status, reason = "unavailable", unavailable[rid]
        elif acceptance[rid] or replays:
            status, reason = (
                "failed",
                next(
                    (
                        m.get("reason_code") or m.get("reason") or m["status"]
                        for m in acceptance[rid] + replays
                        if m["status"] not in ("accepted",)
                    ),
                    "measurement_unavailable",
                ),
            )
        else:
            status, reason = "needs_measurement", None
        output.append(
            {
                "result_id": rid,
                "problem_id": r["problem_id"],
                "statement_revision": r["statement_revision"],
                "accepted_at": r["accepted_at"],
                "intake_kind": r["intake"]["kind"],
                "benchmark_commit": r["benchmark_commit"],
                "status": status,
                "reason": reason,
            }
        )
    return {
        "schema_version": 1,
        "counts": dict(
            sorted(collections.Counter(r["status"] for r in output).items())
        ),
        "results": sorted(output, key=lambda r: (r["accepted_at"], r["result_id"])),
    }


def load_records(root):
    output = []
    for path in sorted((root / "results").glob("*.json")):
        document = json.loads(path.read_text())
        if document["schema_version"] != 2:
            raise ValueError("expected results schema 2")
        output.extend(document["results"])
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=pathlib.Path, required=True)
    parser.add_argument("--public-state", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    report = coverage(
        load_records(args.results_root), json.loads(args.public_state.read_text())
    )
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
