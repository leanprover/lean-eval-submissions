#!/usr/bin/env python3
"""Plan missing server replay events using a trusted operator-supplied profile map.

The map asserts prior nanoda profile qualification; this tool does not verify
profile artifacts, write State, or access submission source.
"""

from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import uuid


def plan(report, domain, profiles, occurred_at):
    submissions = {s["submission_id"]: s for s in domain["submissions"]}
    results = {r["result_id"]: r for r in domain["results"]}
    existing = {t["result_id"] for t in domain["replay_tasks"]}
    events = []
    blocked = []
    timestamp = dt.datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    if timestamp.tzinfo != dt.timezone.utc:
        raise ValueError("timestamp must be UTC")
    for row in report["results"]:
        rid = row["result_id"]
        if row["status"] in ("measured", "pending", "unavailable") or rid in existing:
            continue
        if row["intake_kind"] == "issue":
            blocked.append(
                {"result_id": rid, "reason": "use_existing_historical_delta_planner"}
            )
            continue
        result = results.get(rid)
        if result is None:
            raise ValueError("server result is absent from State")
        submission = submissions[result["submission_id"]]
        benchmark = submission["evaluation"]["benchmark_commit"]
        toolchain = submission["evaluation"]["toolchain"]
        profile = profiles.get(benchmark)
        if (
            profile is None
            or profile.get("toolchain") != toolchain
            or profile.get("qualified") is not True
        ):
            blocked.append(
                {
                    "result_id": rid,
                    "reason": "exact_benchmark_profile_not_qualified",
                    "benchmark_commit": benchmark,
                    "toolchain": toolchain,
                }
            )
            continue
        if submission.get("archive", {}).get("status") != "completed":
            blocked.append(
                {"result_id": rid, "reason": "accepted_archive_not_available"}
            )
            continue
        measurement = profile["measurement_config_digest"]
        execution = profile["execution_profile_digest"]
        if any(
            not isinstance(x, str)
            or len(x) != 64
            or any(c not in "0123456789abcdef" for c in x)
            for x in (measurement, execution)
        ):
            raise ValueError("profile digests must be exact SHA256")
        replay = (
            "rt1_"
            + hashlib.sha256(
                ("lean-eval-replay-task-v1\0" + rid + "\0" + measurement).encode()
            ).hexdigest()
        )
        # Deterministic UUIDv7 makes re-running the same frozen packet idempotent.
        identity = hashlib.sha256((replay + "\0" + occurred_at).encode()).digest()
        milliseconds = int(timestamp.timestamp() * 1000)
        raw = bytearray(milliseconds.to_bytes(6, "big") + identity[:10])
        raw[6] = (raw[6] & 15) | 112
        raw[8] = (raw[8] & 63) | 128
        events.append(
            {
                "schema_version": 1,
                "event_id": str(uuid.UUID(bytes=bytes(raw))),
                "event_type": "replay.enqueued",
                "occurred_at": occurred_at,
                "subject_id": replay,
                "causation_event_id": result["event_id"],
                "actor": {"kind": "system"},
                "payload": {
                    "result_id": rid,
                    "measurement_config_digest": measurement,
                    "execution_profile_digest": execution,
                    "checker": "nanoda",
                },
            }
        )
    return {"schema_version": 1, "events": events, "blocked": blocked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("coverage", "domain", "profiles", "output"):
        parser.add_argument("--" + name, type=pathlib.Path, required=True)
    parser.add_argument("--occurred-at", required=True)
    args = parser.parse_args()
    packet = plan(
        json.loads(args.coverage.read_text()),
        json.loads(args.domain.read_text()),
        json.loads(args.profiles.read_text()),
        args.occurred_at,
    )
    # Create-only: profile qualification and State append remain separate reviewed operations.
    with args.output.open("x") as stream:
        json.dump(packet, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(
        json.dumps(
            {"events": len(packet["events"]), "blocked": len(packet["blocked"])},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
