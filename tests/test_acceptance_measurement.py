from __future__ import annotations
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]


def fixture():
    return json.loads(
        (ROOT / "tests/fixtures/acceptance-measurement-v1.json").read_text()
    )


import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(ROOT / "scripts"))
from collect_acceptance_metrics import collect, measure
from build_acceptance_measurements import build
from measure_acceptance_phase import main as phase_main
from measure_acceptance_sandbox import phase_for, install
from evaluate_submission import _acceptance_sandbox
import os
import subprocess


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
            "submission": {
                "kind": "github_repo",
                "repo": "owner/proof",
                "ref": "a" * 40,
            },
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
            submission=dict(record["submission"]),
        )
        self.assertEqual(build(**kwargs), [m])
        for key, different in (
            ("kind", "github_pr"),
            ("repo", "other/proof"),
            ("ref", "b" * 40),
        ):
            with self.subTest(key=key):
                changed = {**record["submission"], key: different}
                self.assertEqual(build(**{**kwargs, "submission": changed}), [])
        self.assertEqual(build(**{**kwargs, "benchmark_commit": "b" * 40}), [])
        self.assertEqual(
            build(
                **{
                    **kwargs,
                    "runner_class": "namespace-ubuntu-24.04-amd64-32x64-lake-v1",
                }
            )[0]["runner_class"],
            "namespace-ubuntu-24.04-amd64-32x64-lake-v1",
        )
        numeric["workflow_commit"] = "b" * 40
        with self.assertRaises(ValueError):
            build(**kwargs)


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


class RuntimeIsolationTests(unittest.TestCase):
    def test_counter_errors_before_and_after_execution_preserve_the_single_command(
        self,
    ):
        for failure in ("before", "after"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = pathlib.Path(directory) / "calls"
                descriptor = (
                    os.open(os.devnull, os.O_RDONLY) if failure == "after" else None
                )
                with (
                    mock.patch.object(
                        measure,
                        "open_counter",
                        side_effect=RuntimeError("counter unavailable")
                        if failure == "before"
                        else None,
                        return_value=(descriptor, None),
                    ),
                    mock.patch.object(
                        measure.fcntl,
                        "ioctl",
                        side_effect=[0, 0, RuntimeError("counter read failed")],
                    ),
                ):
                    result = measure.run_measured(
                        [
                            sys.executable,
                            "-c",
                            "import pathlib,sys; pathlib.Path(sys.argv[1]).open('a').write('ran\\n');sys.exit(7)",
                            str(output),
                        ],
                        best_effort=True,
                        preserve_fds=True,
                    )
                self.assertEqual(result[0], 7)
                self.assertEqual(
                    result[2],
                    {"status": "unavailable", "reason": "counter_not_reported"},
                )
                self.assertEqual(output.read_text(), "ran\n")

    def test_sandbox_control_fds_survive_but_counter_control_fd_is_not_inherited(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "control"
            path.write_text("sandbox-control")
            control = os.open(path, os.O_RDONLY)
            counter = os.open(os.devnull, os.O_RDONLY)
            os.set_inheritable(control, True)
            script = "import os,sys; assert os.read(int(sys.argv[1]),100)==b'sandbox-control';\ntry: os.fstat(int(sys.argv[2]));sys.exit(1)\nexcept OSError: pass"
            try:
                with (
                    mock.patch.object(
                        measure, "open_counter", return_value=(counter, None)
                    ),
                    mock.patch.object(measure.fcntl, "ioctl", return_value=0),
                ):
                    result = measure.run_measured(
                        [sys.executable, "-c", script, str(control), str(counter)],
                        best_effort=True,
                        preserve_fds=True,
                    )
                self.assertEqual(result[0], 0)
            finally:
                os.close(control)

    def test_launcher_import_failure_still_executes_the_real_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            metrics = root / "metrics"
            metrics.mkdir()
            broken = root / "broken"
            broken.mkdir()
            (broken / "measure_acceptance_sandbox.py").write_text(
                "raise RuntimeError('observer import failed')\n"
            )
            with mock.patch(
                "measure_acceptance_sandbox.__file__", str(broken / "source.py")
            ):
                wrapper = install(metrics, root, {}, pathlib.Path(sys.executable), root)
            process = subprocess.run(
                [str(wrapper), "-c", "import sys; print('real sandbox');sys.exit(7)"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(process.returncode, 7)
            self.assertEqual(process.stdout.strip(), "real sandbox")
            self.assertNotIn("Traceback", process.stderr)
