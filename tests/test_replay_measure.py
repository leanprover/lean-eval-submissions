from __future__ import annotations

import copy
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
SCRIPT = ROOT / "server" / "replay-image" / "replay-measure"
loader = importlib.machinery.SourceFileLoader("replay_measure", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
replay_measure = importlib.util.module_from_spec(spec)
loader.exec_module(replay_measure)


class ReplayMeasureTests(unittest.TestCase):
    def test_accepts_only_fixed_phases_and_landrun(self) -> None:
        self.assertEqual(
            replay_measure.parse_invocation(
                ["--phase", "build", "--", replay_measure.LANDRUN, "--ro", "/"]
            ),
            ("build", [replay_measure.LANDRUN, "--ro", "/"]),
        )
        self.assertEqual(
            replay_measure.parse_invocation(
                [
                    "--phase",
                    "solution-export",
                    "--",
                    replay_measure.LANDRUN,
                    "--ro",
                    "/",
                ]
            ),
            ("solution-export", [replay_measure.LANDRUN, "--ro", "/"]),
        )
        for argv in (
            ["--phase", "challenge", "--", replay_measure.LANDRUN],
            ["--phase", "checker", "--", "/bin/sh"],
            ["checker", "--", replay_measure.LANDRUN],
        ):
            with self.subTest(argv=argv), self.assertRaises(
                replay_measure.MeasurementError
            ):
                replay_measure.parse_invocation(argv)

    def test_aggregates_wall_time_and_counters_by_phase(self) -> None:
        metrics = replay_measure.empty_metrics()
        replay_measure.aggregate(
            metrics, "build", 10, {"status": "measured", "value": 20}, 0
        )
        replay_measure.aggregate(
            metrics, "build", 11, {"status": "measured", "value": 21}, 1
        )
        self.assertEqual(metrics["phases"]["build"], {
            "invocations": 2,
            "wall_time_ms": 21,
            "retired_instructions": {"status": "measured", "value": 41},
            "terminations": [
                {"kind": "exited", "code": 0},
                {"kind": "exited", "code": 1},
            ],
        })
        self.assertEqual(metrics["phases"]["checker"]["invocations"], 0)

    def test_unavailable_counter_taints_the_whole_aggregate(self) -> None:
        metrics = replay_measure.empty_metrics()
        replay_measure.aggregate(
            metrics, "checker", 2, {"status": "measured", "value": 3}, 0
        )
        replay_measure.aggregate(
            metrics,
            "checker",
            5,
            {"status": "unavailable", "reason": "counter_permission_denied"},
            -9,
        )
        replay_measure.aggregate(
            metrics, "checker", 7, {"status": "measured", "value": 11}, 2
        )
        self.assertEqual(metrics["phases"]["checker"], {
            "invocations": 3,
            "wall_time_ms": 14,
            "retired_instructions": {
                "status": "unavailable",
                "reason": "counter_permission_denied",
            },
            "terminations": [
                {"kind": "exited", "code": 0},
                {"kind": "signaled", "signal": 9},
                {"kind": "exited", "code": 2},
            ],
        })

    def test_rejects_noncanonical_metrics(self) -> None:
        metrics = replay_measure.empty_metrics()
        broken = copy.deepcopy(metrics)
        broken["phases"]["build"]["surprise"] = True
        with self.assertRaises(replay_measure.MeasurementError):
            replay_measure.validate_metrics(broken)
        broken = copy.deepcopy(metrics)
        broken["phases"]["build"]["retired_instructions"] = {
            "status": "unavailable",
            "reason": "made_up",
        }
        with self.assertRaises(replay_measure.MeasurementError):
            replay_measure.validate_metrics(broken)

    def test_maps_kernel_failures_to_registered_reasons(self) -> None:
        self.assertEqual(
            replay_measure.unavailable_reason(1),
            "counter_permission_denied",
        )
        self.assertEqual(
            replay_measure.unavailable_reason(95),
            "counter_not_supported",
        )
        self.assertEqual(
            replay_measure.unavailable_reason(5),
            "counter_not_reported",
        )

    def test_main_preserves_success_and_atomically_records_phase(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            metrics = pathlib.Path(raw) / "metrics.json"
            with (
                mock.patch.object(replay_measure, "LANDRUN", sys.executable),
                mock.patch.object(replay_measure, "METRICS_PATH", metrics),
                mock.patch.object(
                    replay_measure,
                    "open_counter",
                    return_value=(None, "counter_not_supported"),
                ),
            ):
                self.assertEqual(
                    replay_measure.main(
                        ["--phase", "checker", "--", sys.executable, "-c", "pass"]
                    ),
                    0,
                )
                value = replay_measure.validate_metrics(
                    json.loads(metrics.read_text(encoding="utf-8"))
                )
            self.assertEqual(value["phases"]["checker"]["invocations"], 1)
            self.assertEqual(
                value["phases"]["checker"]["terminations"],
                [{"kind": "exited", "code": 0}],
            )
            self.assertEqual(
                value["phases"]["checker"]["retired_instructions"],
                {"status": "unavailable", "reason": "counter_not_supported"},
            )

    def test_solution_export_is_forwarded_and_captured_byte_for_byte(self) -> None:
        class Output:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()

        with tempfile.TemporaryDirectory() as raw:
            destination = pathlib.Path(raw) / "solution-export.ndjson"
            output = Output()
            with mock.patch.object(replay_measure.sys, "stdout", output):
                returncode = replay_measure.run_with_export_capture(
                    [
                        sys.executable,
                        "-c",
                        "import sys;sys.stdout.buffer.write(b'{\\\"meta\\\":{}}\\n')",
                    ],
                    destination,
                )
            expected = b'{"meta":{}}\n'
            self.assertEqual(returncode, 0)
            self.assertEqual(output.buffer.getvalue(), expected)
            self.assertEqual(destination.read_bytes(), expected)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

            with mock.patch.object(replay_measure.sys, "stdout", Output()):
                self.assertEqual(
                    replay_measure.run_with_export_capture(
                        [sys.executable, "-c", "pass"], destination
                    ),
                    0,
                )
            self.assertEqual(destination.read_bytes(), expected)

    def test_failed_or_over_limit_export_leaves_no_capture(self) -> None:
        class Output:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()

        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            failed = root / "failed.ndjson"
            with mock.patch.object(replay_measure.sys, "stdout", Output()):
                self.assertEqual(
                    replay_measure.run_with_export_capture(
                        [sys.executable, "-c", "raise SystemExit(7)"], failed
                    ),
                    7,
                )
            self.assertFalse(failed.exists())

            oversized = root / "oversized.ndjson"
            output = Output()
            with (
                mock.patch.object(replay_measure.sys, "stdout", output),
                mock.patch.object(replay_measure, "MAX_SOLUTION_EXPORT_BYTES", 3),
            ):
                self.assertEqual(
                    replay_measure.run_with_export_capture(
                        [
                            sys.executable,
                            "-c",
                            "import sys;sys.stdout.buffer.write(b'abcd\\n')",
                        ],
                        oversized,
                    ),
                    0,
                )
            self.assertFalse(oversized.exists())
            self.assertEqual(output.buffer.getvalue(), b"abcd\n")

    def test_unusable_capture_parent_does_not_change_child_exit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = pathlib.Path(raw) / "not-a-directory"
            parent.write_bytes(b"incumbent")
            self.assertEqual(
                replay_measure.run_with_export_capture(
                    [sys.executable, "-c", "raise SystemExit(7)"],
                    parent / "solution-export.ndjson",
                ),
                7,
            )
            self.assertEqual(parent.read_bytes(), b"incumbent")

    def test_forwarding_failure_removes_partial_capture(self) -> None:
        class BrokenBuffer:
            def write(self, value: bytes) -> int:
                raise BrokenPipeError

            def flush(self) -> None:
                pass

        class Output:
            buffer = BrokenBuffer()

        with tempfile.TemporaryDirectory() as raw:
            destination = pathlib.Path(raw) / "solution-export.ndjson"
            with mock.patch.object(replay_measure.sys, "stdout", Output()):
                with self.assertRaises(replay_measure.MeasurementError):
                    replay_measure.run_with_export_capture(
                        [
                            sys.executable,
                            "-c",
                            "import sys;sys.stdout.buffer.write(b'partial\\n')",
                        ],
                        destination,
                    )
            self.assertFalse(destination.exists())

    def test_forwarding_failure_records_a_crashing_phase_termination(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics = root / "metrics.json"
            export = root / "solution-export.ndjson"
            with (
                mock.patch.object(replay_measure, "LANDRUN", sys.executable),
                mock.patch.object(replay_measure, "METRICS_PATH", metrics),
                mock.patch.object(replay_measure, "SOLUTION_EXPORT_PATH", export),
                mock.patch.object(
                    replay_measure,
                    "run_with_export_capture",
                    side_effect=replay_measure.MeasurementError(
                        "solution export capture failed"
                    ),
                ),
                mock.patch.object(
                    replay_measure,
                    "open_counter",
                    return_value=(None, "counter_not_supported"),
                ),
            ):
                self.assertEqual(
                    replay_measure.main(
                        [
                            "--phase",
                            "solution-export",
                            "--",
                            sys.executable,
                            "-c",
                            "pass",
                        ]
                    ),
                    125,
                )
            value = replay_measure.validate_metrics(
                json.loads(metrics.read_text(encoding="utf-8"))
            )
            self.assertEqual(value["phases"]["build"]["invocations"], 1)
            self.assertEqual(
                value["phases"]["build"]["terminations"],
                [{"kind": "exited", "code": 125}],
            )

    def test_solution_export_counts_as_build_and_uses_fixed_capture_path(self) -> None:
        class Output:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()

        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            metrics = root / "metrics.json"
            export = root / "solution-export.ndjson"
            output = Output()
            with (
                mock.patch.object(replay_measure, "LANDRUN", sys.executable),
                mock.patch.object(replay_measure, "METRICS_PATH", metrics),
                mock.patch.object(replay_measure, "SOLUTION_EXPORT_PATH", export),
                mock.patch.object(replay_measure.sys, "stdout", output),
                mock.patch.object(
                    replay_measure,
                    "open_counter",
                    return_value=(None, "counter_not_supported"),
                ),
            ):
                self.assertEqual(
                    replay_measure.main(
                        [
                            "--phase",
                            "solution-export",
                            "--",
                            sys.executable,
                            "-c",
                            "import sys;sys.stdout.buffer.write(b'{}\\n')",
                        ]
                    ),
                    0,
                )
            value = replay_measure.validate_metrics(
                json.loads(metrics.read_text(encoding="utf-8"))
            )
            self.assertEqual(value["phases"]["build"]["invocations"], 1)
            self.assertEqual(value["phases"]["checker"]["invocations"], 0)
            self.assertEqual(export.read_bytes(), b"{}\n")
            self.assertEqual(output.buffer.getvalue(), b"{}\n")

    def test_metrics_persistence_failure_discards_stale_phase_history(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            metrics = pathlib.Path(raw) / "metrics.json"
            metrics.write_text(
                json.dumps(replay_measure.empty_metrics()), encoding="utf-8"
            )
            with (
                mock.patch.object(replay_measure, "LANDRUN", sys.executable),
                mock.patch.object(replay_measure, "METRICS_PATH", metrics),
                mock.patch.object(
                    replay_measure,
                    "open_counter",
                    return_value=(None, "counter_not_supported"),
                ),
                mock.patch.object(
                    replay_measure,
                    "save_metrics",
                    side_effect=OSError("simulated persistence failure"),
                ),
            ):
                self.assertEqual(
                    replay_measure.main(
                        ["--phase", "checker", "--", sys.executable, "-c", "pass"]
                    ),
                    125,
                )
            self.assertFalse(metrics.exists())


if __name__ == "__main__":
    unittest.main()
