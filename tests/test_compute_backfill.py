from __future__ import annotations
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))
from compute_reporting_coverage import coverage
from plan_compute_backfill import plan


def fixture():
    return {
        "result_id": "r2_" + "a" * 64,
        "problem_id": "two_plus_two",
        "benchmark_commit": "b" * 40,
        "status": "available",
    }


class ComputeBackfillTests(unittest.TestCase):
    def test_coverage_distinguishes_history_queue_and_unmeasured_results(self):
        m = fixture()
        r = {
            "result_id": m["result_id"],
            "problem_id": m["problem_id"],
            "statement_revision": 1,
            "accepted_at": "2026-10-20T00:00:00Z",
            "intake": {"kind": "server"},
            "benchmark_commit": m["benchmark_commit"],
        }
        state = {
            "schema_version": 7,
            "results": [],
            "historical_replay_series": [],
            "historical_replay_unavailability": [],
            "acceptance_measurements": [],
        }
        self.assertEqual(coverage([r], state)["counts"], {"needs_measurement": 1})
        state["acceptance_measurements"] = [m]
        self.assertEqual(coverage([r], state)["counts"], {"measured": 1})
        state["acceptance_measurements"] = []
        state["historical_replay_series"] = [
            {"result_id": m["result_id"], "replay": {"status": "queued"}}
        ]
        self.assertEqual(coverage([r], state)["counts"], {"pending": 1})

    def test_backfill_does_not_duplicate_tasks_or_guess_original_toolchains(self):
        rid = fixture()["result_id"]
        report = {
            "results": [
                {
                    "result_id": rid,
                    "status": "needs_measurement",
                    "intake_kind": "server",
                }
            ]
        }
        domain = {
            "submissions": [
                {
                    "submission_id": "s",
                    "evaluation": {
                        "benchmark_commit": "a" * 40,
                        "toolchain": "leanprover/lean4:v4.34.0",
                    },
                    "archive": {"status": "completed"},
                }
            ],
            "results": [
                {
                    "result_id": rid,
                    "submission_id": "s",
                    "event_id": "0198abcd-0000-7000-8000-000000000006",
                }
            ],
            "replay_tasks": [],
        }
        packet = plan(report, domain, {}, "2026-10-22T09:00:00.000Z")
        self.assertEqual(packet["events"], [])
        self.assertEqual(
            packet["blocked"][0]["reason"], "exact_benchmark_profile_not_qualified"
        )
        profile = {
            "a" * 40: {
                "qualified": True,
                "toolchain": "leanprover/lean4:v4.34.0",
                "measurement_config_digest": "b" * 64,
                "execution_profile_digest": "c" * 64,
            }
        }
        first = plan(report, domain, profile, "2026-10-22T09:00:00.000Z")
        self.assertEqual(
            first, plan(report, domain, profile, "2026-10-22T09:00:00.000Z")
        )
        self.assertEqual(len(first["events"]), 1)
        domain["replay_tasks"] = [{"result_id": rid}]
        self.assertEqual(
            plan(report, domain, profile, "2026-10-22T09:00:00.000Z")["events"], []
        )
