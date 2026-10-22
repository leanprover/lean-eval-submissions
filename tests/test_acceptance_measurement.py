from __future__ import annotations
import json
import pathlib
import unittest
from scripts.acceptance_measurement import validate_measurement

ROOT = pathlib.Path(__file__).parents[1]


def fixture():
    return json.loads(
        (ROOT / "tests/fixtures/acceptance-measurement-v1.json").read_text()
    )


class MeasurementContractTests(unittest.TestCase):
    def test_unavailable_counter_preserves_elapsed_time(self):
        m = validate_measurement(fixture())
        self.assertEqual(m["checker_wall_time_ms"], 120)
        self.assertIsNone(m["checker_retired_instructions"])

    def test_rejects_tampered_identity_environment_and_counter(self):
        for key, value in [
            ("result_id", "r2_" + "0" * 64),
            ("architecture", "aarch64"),
            ("attempt", True),
            ("build_wall_time_ms", -1),
            ("build_retired_instructions", None),
            ("checker_retired_instructions", 0),
            ("source_code", "private proof"),
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                m = fixture()
                m[key] = value
                validate_measurement(m)

    def test_failure_is_explicit_and_never_has_partial_totals(self):
        m = fixture()
        m.update(status="failed", reason_code="measurement_invalid")
        for phase in ("build", "checker"):
            m[phase + "_wall_time_ms"] = None
            m[phase + "_retired_instructions"] = None
            m[phase + "_retired_instructions_unavailable_reason"] = (
                "counter_not_reported"
            )
        validate_measurement(m)
        m["build_wall_time_ms"] = 100
        with self.assertRaises(ValueError):
            validate_measurement(m)


import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(ROOT / "scripts"))
from collect_acceptance_metrics import collect, measure
from build_acceptance_measurements import build
from measure_acceptance_phase import main as phase_main
from measure_acceptance_sandbox import phase_for
from evaluate_submission import _acceptance_sandbox
import os


class AcceptanceProducerTests(unittest.TestCase):
    def test_native_phase_selection_excludes_challenge_and_resolution(self):
        tools = {
            name: "/trusted/" + name
            for name in ("lake", "leanexport", "nanoda_bin", "leanchecker")
        }
        for command, expected in [
            (["/trusted/lake", "env"], None),
            (["/trusted/lake", "build", "Challenge"], None),
            (["/trusted/leanexport", "Challenge", "--", "target"], None),
            (["/trusted/lake", "build", "Solution"], "build"),
            (["/trusted/leanexport", "Solution", "--", "target"], "build"),
            (["/trusted/nanoda_bin", "/tmp/config"], "checker"),
            (
                ["/trusted/leanchecker", "--silent", "--from-export", "/tmp/export"],
                "checker",
            ),
        ]:
            with self.subTest(command=command):
                self.assertEqual(
                    phase_for(["--unshare-all", "--", *command], tools, "Solution"),
                    expected,
                )

    def test_native_wrapper_restores_environment_after_evaluation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            workspace = root / "workspaces" / "problem"
            workspace.mkdir(parents=True)
            (workspace / "config.json").write_text(
                json.dumps({"solution_module": "Solution"})
            )
            metrics = root / "metrics"
            metrics.mkdir()
            with (
                mock.patch.dict(os.environ, {"COMPARATOR_BWRAP": "/original/bwrap"}),
                mock.patch(
                    "evaluate_submission.shutil.which", return_value=sys.executable
                ),
                mock.patch(
                    "evaluate_submission.subprocess.check_output",
                    side_effect=["/trusted", "a" * 40],
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "proof failed"):
                    with _acceptance_sandbox(
                        metrics, workspace.parent, ["problem"], root
                    ):
                        self.assertEqual(
                            os.environ["COMPARATOR_BWRAP"], str(metrics / "bwrap")
                        )
                        self.assertEqual(
                            json.loads((metrics / "setup.json").read_text())[
                                "toolchain"
                            ],
                            "a" * 40,
                        )
                        raise RuntimeError("proof failed")
                self.assertEqual(os.environ["COMPARATOR_BWRAP"], "/original/bwrap")

    def test_native_measurement_setup_failure_does_not_suppress_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            workspace = root / "workspaces" / "problem"
            workspace.mkdir(parents=True)
            (workspace / "config.json").write_text(
                json.dumps({"solution_module": "Solution"})
            )
            with (
                mock.patch.dict(os.environ, {"COMPARATOR_BWRAP": "/original/bwrap"}),
                mock.patch(
                    "evaluate_submission.shutil.which", return_value=sys.executable
                ),
                mock.patch(
                    "evaluate_submission.subprocess.check_output",
                    return_value="/trusted",
                ),
                mock.patch(
                    "measure_acceptance_sandbox.install",
                    side_effect=OSError("disk full"),
                ),
            ):
                with _acceptance_sandbox(
                    root / "metrics", workspace.parent, ["problem"], root
                ):
                    self.assertEqual(os.environ["COMPARATOR_BWRAP"], "/original/bwrap")

    def test_complete_two_phase_measurement_and_missing_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            metrics = measure.empty_metrics()
            for phase in ("build", "build", "checker", "checker"):
                measure.aggregate(
                    metrics,
                    phase,
                    7,
                    {"status": "unavailable", "reason": "counter_permission_denied"},
                    0,
                )
            (root / "two_plus_two.json").write_text(json.dumps(metrics))
            output = collect(root, ["two_plus_two"])["two_plus_two"]
            self.assertEqual(output["build_wall_time_ms"], 14)
            self.assertEqual(output["status"], "available")
            self.assertEqual(
                output["build_retired_instructions_unavailable_reason"],
                "counter_permission_denied",
            )
            (root / "two_plus_two.failed").touch()
            output = collect(root, ["two_plus_two"])["two_plus_two"]
            self.assertEqual(output["status"], "failed")
            self.assertIsNone(output["build_wall_time_ms"])

    def test_partial_tampered_or_absent_metrics_are_never_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for text in ("{}", "invalid json", json.dumps(measure.empty_metrics())):
                (root / "two_plus_two.json").write_text(text)
                self.assertEqual(
                    collect(root, ["two_plus_two"])["two_plus_two"]["status"], "failed"
                )
            with self.assertRaises(ValueError):
                collect(root, ["../../source"])

    def test_measurement_persistence_failure_preserves_exit_code(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch(
                "measure_acceptance_phase.measure.run_measured",
                return_value=(0, 12, {"status": "measured", "value": 10}),
            ),
            mock.patch(
                "measure_acceptance_phase.measure.save_metrics",
                side_effect=OSError("disk full"),
            ),
        ):
            output = pathlib.Path(directory) / "problem.json"
            self.assertEqual(
                phase_main(
                    [
                        "--metrics-path",
                        str(output),
                        "--landrun",
                        "/bin/true",
                        "--",
                        "--phase",
                        "build",
                        "--",
                        "/bin/true",
                    ]
                ),
                0,
            )
            self.assertTrue(output.with_suffix(".failed").exists())

    def test_receipts_bind_only_the_original_accepted_source(self):
        m = fixture()
        numeric = {
            k: v
            for k, v in m.items()
            if k in {"status", "reason_code"} or k.startswith(("build_", "checker_"))
        }
        record = {
            "result_id": m["result_id"],
            "problem_id": m["problem_id"],
            "declared_model": m["declared_model"],
            "statement_revision": 1,
            "benchmark_commit": m["benchmark_commit"],
        }
        kwargs = dict(
            records=[record],
            metrics={m["problem_id"]: numeric},
            owner=m["owner_login"],
            model=m["declared_model"],
            passed=[m["problem_id"]],
            benchmark_commit=m["benchmark_commit"],
            results_commit=m["results_commit"],
            toolchain=m["toolchain"],
            workflow_commit=m["workflow_commit"],
            workflow_run_id=m["workflow_run_id"],
            attempt=m["attempt"],
            measured_at=m["measured_at"],
            architecture=m["architecture"],
            components=m["components"],
        )
        self.assertEqual(build(**kwargs), [m])
        numeric["workflow_commit"] = "b" * 40
        with self.assertRaises(ValueError):
            build(**kwargs)


from compute_reporting_coverage import coverage
from plan_compute_backfill import plan
from reconcile_acceptance_measurements import receipts, recovery_runs
import datetime as dt
import io
import zipfile


class CoverageAndRecoveryTests(unittest.TestCase):
    def test_published_failure_does_not_starve_older_missing_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            newest, older = "r2_" + "a" * 64, "r2_" + "b" * 64
            for rid, run in ((newest, 3), (older, 2)):
                (root / (rid + ".json")).write_text(json.dumps({"run_id": run}))
            report = {
                "results": [
                    {
                        "result_id": rid,
                        "status": "failed",
                        "accepted_at": "2026-10-22T00:00:00Z",
                    }
                    for rid in (newest, older)
                ]
            }
            state = {
                "acceptance_measurements": [
                    {"result_id": newest, "workflow_run_id": 3, "status": "failed"}
                ]
            }
            self.assertEqual(
                recovery_runs(
                    report,
                    state,
                    root,
                    dt.datetime(2026, 10, 22, tzinfo=dt.timezone.utc),
                ),
                {2: {older}},
            )

    def test_recovery_rejects_untrusted_artifact_paths_and_identity(self):
        for name, value in [
            ("../source.json", fixture()),
            (fixture()["result_id"] + ".json", dict(fixture(), workflow_run_id=999)),
        ]:
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(name, json.dumps(value))
            with self.assertRaises(ValueError):
                list(receipts(archive.getvalue(), 123, "a" * 40))

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


class PersistenceIsolationTests(unittest.TestCase):
    def test_unwritable_failure_marker_cannot_change_proof_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            args = [
                "--metrics-path",
                str(pathlib.Path(directory) / "problem.json"),
                "--landrun",
                "/bin/true",
                "--",
                "--phase",
                "build",
                "--",
                "/bin/true",
            ]
            with (
                mock.patch(
                    "measure_acceptance_phase.measure.run_measured",
                    return_value=(0, 12, {"status": "measured", "value": 10}),
                ),
                mock.patch(
                    "measure_acceptance_phase.measure.save_metrics",
                    side_effect=OSError("read-only filesystem"),
                ),
                mock.patch(
                    "measure_acceptance_phase.pathlib.Path.unlink",
                    side_effect=OSError("read-only filesystem"),
                ),
                mock.patch(
                    "measure_acceptance_phase.pathlib.Path.touch",
                    side_effect=OSError("read-only filesystem"),
                ),
            ):
                self.assertEqual(phase_main(args), 0)
