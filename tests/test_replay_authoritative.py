from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import io
import pathlib
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "server" / "replay-image" / "replay-authoritative"
loader = importlib.machinery.SourceFileLoader("replay_authoritative", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
authoritative = importlib.util.module_from_spec(spec)
loader.exec_module(authoritative)


def request() -> dict:
    return {
        "schema_version": 1,
        "replay_task_id": "rt1_" + "1" * 64,
        "attempt": 1,
        "source": {
            "visibility": "private",
            "archive": {
                "submission_id": "0198abcd-1111-7000-8000-000000000001",
                "archive_ciphertext_sha256": "2" * 64,
            },
        },
        "result": {
            "submission_id": "0198abcd-1111-7000-8000-000000000001",
            "problem_id": "two_plus_two",
            "statement_revision": 3,
        },
        "benchmark": {
            "repository": "leanprover/lean-eval",
            "commit": "3" * 40,
            "toolchain": "leanprover/lean4:v4.33.0",
        },
        "execution_profile": {
            "runner_profile": "cloudflare-sandbox-standard-4-v1",
            "vm_image_digest": "sha256:" + "4" * 64,
            "toolchain": "leanprover/lean4:v4.33.0",
            "go_toolchain": "go1.25.12",
            "rust_toolchain": "rustc-1.89.0",
            "cpu_model": "fixture-cpu",
            "architecture": "x86_64",
            "kernel_release": "fixture-kernel",
            "cache_state": "cold",
            "measurement_command": ["/opt/lean-eval/replay-measure"],
            "components": {
                "comparator": {
                    "repository": "leanprover/comparator",
                    "commit": "5" * 40,
                },
                "landrun": {
                    "repository": "zouuup/landrun",
                    "commit": "6" * 40,
                },
                "lean4export": {
                    "repository": "leanprover/lean4export",
                    "commit": "7" * 40,
                },
                "nanoda": {
                    "repository": "robsimmons/nanoda_lib",
                    "commit": "8" * 40,
                },
            },
        },
        "measurement_config": {
            "schema_version": 1,
            "wall_time_limit_ms": 19_800_000,
            "memory_limit_bytes": 12 * 1024**3,
            "retired_instructions": {
                "required": False,
                "perf_event": "instructions:u",
            },
        },
    }


def metrics(*, checker_invocations: int = 1) -> dict:
    return {
        "schema_version": 1,
        "phases": {
            "build": {
                "invocations": 2,
                "wall_time_ms": 120,
                "retired_instructions": {"status": "measured", "value": 500},
                "terminations": [
                    {"kind": "exited", "code": 0},
                    {"kind": "exited", "code": 0},
                ],
            },
            "checker": {
                "invocations": checker_invocations,
                "wall_time_ms": 30 if checker_invocations else 0,
                "retired_instructions": (
                    {"status": "measured", "value": 80}
                    if checker_invocations
                    else {"status": "measured", "value": 0}
                ),
                "terminations": (
                    [{"kind": "exited", "code": 0}]
                    if checker_invocations
                    else []
                ),
            },
        },
    }


def write_tar(path: pathlib.Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, contents in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))


class AuthoritativeReplayTests(unittest.TestCase):
    def test_baked_profile_lock_avoids_image_digest_cycle(self) -> None:
        replay_request = request()
        profile = replay_request["execution_profile"]
        lock = {
            "schema_version": 1,
            "benchmark_repository": replay_request["benchmark"]["repository"],
            "benchmark_commit": replay_request["benchmark"]["commit"],
            "toolchain": replay_request["benchmark"]["toolchain"],
            **{
                field: profile[field]
                for field in (
                    "runner_profile",
                    "go_toolchain",
                    "rust_toolchain",
                    "cache_state",
                    "measurement_command",
                    "components",
                )
            },
        }
        self.assertNotIn("vm_image_digest", lock)
        with tempfile.TemporaryDirectory() as raw:
            benchmark = pathlib.Path(raw)
            (benchmark / ".lean-eval-commit").write_text("3" * 40 + "\n")
            (benchmark / "lean-toolchain").write_text(
                "leanprover/lean4:v4.33.0\n"
            )
            with mock.patch.object(authoritative, "BENCHMARK", benchmark):
                self.assertIs(
                    authoritative.validate_profile_lock(lock, replay_request), lock
                )
                lock["components"] = {
                    **profile["components"],
                    "landrun": {
                        "repository": "zouuup/landrun",
                        "commit": "9" * 40,
                    },
                }
                with self.assertRaisesRegex(
                    authoritative.AuthoritativeReplayError, "profile lock"
                ):
                    authoritative.validate_profile_lock(lock, replay_request)

    def test_runtime_identity_must_match_profile(self) -> None:
        replay_request = request()
        authoritative.validate_runtime_profile(
            replay_request, "x86_64", "fixture-kernel", "fixture-cpu"
        )
        with self.assertRaisesRegex(
            authoritative.AuthoritativeReplayError, "execution profile"
        ):
            authoritative.validate_runtime_profile(
                replay_request, "x86_64", "different-kernel", "fixture-cpu"
            )

    def test_measurement_configuration_must_match_enforced_limits(self) -> None:
        replay_request = request()
        authoritative.validate_measurement_limits(replay_request)
        replay_request["measurement_config"]["wall_time_limit_ms"] -= 1
        with self.assertRaisesRegex(
            authoritative.AuthoritativeReplayError, "executor limits"
        ):
            authoritative.validate_measurement_limits(replay_request)

    def test_timeout_kills_the_entire_evaluator_process_group(self) -> None:
        process = mock.Mock(pid=1234, returncode=-9)
        process.wait.side_effect = [
            __import__("subprocess").TimeoutExpired(["evaluator"], 1),
            -9,
        ]
        with (
            mock.patch.object(authoritative.subprocess, "Popen", return_value=process) as popen,
            mock.patch.object(authoritative.os, "killpg") as killpg,
        ):
            returncode, timed_out = authoritative.run_process_group(
                ["evaluator"], {"PATH": "/bin"}, 1
            )
        self.assertEqual((returncode, timed_out), (-9, True))
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(1234, authoritative.signal.SIGKILL)

    def test_encoded_input_limit_is_enforced_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            encoded = root / "input.b64"
            encoded.write_bytes(base64.b64encode(b"1234567"))
            with self.assertRaisesRegex(
                authoritative.AuthoritativeReplayError, "encoded replay input"
            ):
                authoritative.decode_file(encoded, root / "decoded", 4)

    def test_nonzero_evaluator_without_measurements_is_retryable_infrastructure_failure(
        self,
    ) -> None:
        normalized = authoritative.normalize_metrics(authoritative.empty_metrics())
        with self.assertRaisesRegex(
            authoritative.AuthoritativeReplayError,
            "evaluator failed before measurement",
        ):
            authoritative.require_measurement_after_evaluator_failure(1, normalized)

        measured = authoritative.normalize_metrics(metrics(checker_invocations=0))
        authoritative.require_measurement_after_evaluator_failure(1, measured)
        authoritative.require_measurement_after_evaluator_failure(0, normalized)

    def test_archive_expectation_is_bound_to_request(self) -> None:
        value = {
            "schema_version": 1,
            "submission_id": request()["result"]["submission_id"],
            "archive_ciphertext_sha256": "2" * 64,
            "plaintext_tar_sha256": "3" * 64,
            "plaintext_tar_size": 123,
        }
        self.assertIs(authoritative.validate_expectation(value, request()), value)
        value["submission_id"] = "0198abcd-2222-7000-8000-000000000002"
        with self.assertRaisesRegex(
            authoritative.AuthoritativeReplayError, "submission_id mismatch"
        ):
            authoritative.validate_expectation(value, request())

    def test_safe_extraction_rebuilds_files_without_modes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            archive = root / "source.tar.gz"
            write_tar(
                archive,
                {
                    "source/proof/lakefile.toml": b'name = "two_plus_two"\n',
                    "source/proof/Submission.lean": b"by decide\n",
                },
            )
            source = authoritative.extract_archive(archive, root / "out")
            self.assertEqual(
                (source / "proof" / "Submission.lean").read_bytes(), b"by decide\n"
            )

    def test_extraction_rejects_git_and_traversal(self) -> None:
        for name in ("source/.git/config", "source/../escape"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                root = pathlib.Path(raw)
                archive = root / "source.tar.gz"
                write_tar(archive, {name: b"bad"})
                with self.assertRaisesRegex(
                    authoritative.AuthoritativeReplayError, "unsafe member"
                ):
                    authoritative.extract_archive(archive, root / "out")

    def test_source_statistics_counts_only_locked_submission_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = pathlib.Path(raw)
            proof = source / "proof"
            (proof / "Submission").mkdir(parents=True)
            (proof / "lakefile.toml").write_text(
                'name = "two_plus_two"\n', encoding="utf-8"
            )
            (proof / "Submission.lean").write_text("line1\nline2\n", encoding="utf-8")
            (proof / "Submission" / "Helper.lean").write_text(
                "helper\n", encoding="utf-8"
            )
            (proof / "ignored.txt").write_text("not source\n", encoding="utf-8")
            self.assertEqual(
                authoritative.source_statistics(source, "two_plus_two"), (2, 3)
            )

    def test_verdict_maps_exact_result_and_phase_metrics(self) -> None:
        accepted = authoritative.build_verdict(
            request(),
            {
                "passed": ["two_plus_two"],
                "statement_revisions": {"two_plus_two": 3},
            },
            metrics(),
            2,
            3,
        )
        self.assertEqual(accepted["checker_outcome"], "accepted")
        self.assertEqual(accepted["statistics"]["build_retired_instructions"]["value"], 500)
        self.assertEqual(accepted["statistics"]["checker_wall_time_ms"], 30)

        rejected = authoritative.build_verdict(
            request(),
            {"passed": [], "statement_revisions": {}},
            metrics(checker_invocations=0),
            2,
            3,
        )
        self.assertEqual(rejected["checker_outcome"], "rejected")
        self.assertEqual(
            rejected["statistics"]["checker_retired_instructions"],
            {"status": "unavailable", "reason": "counter_not_reported"},
        )

        declined_metrics = metrics()
        declined_metrics["phases"]["checker"]["terminations"] = [
            {"kind": "exited", "code": 1}
        ]
        declined = authoritative.build_verdict(
            request(),
            {"passed": [], "statement_revisions": {}},
            declined_metrics,
            2,
            3,
        )
        self.assertEqual(declined["execution_outcome"], "completed")
        self.assertEqual(declined["checker_outcome"], "declined")

        crashed_metrics = metrics()
        crashed_metrics["phases"]["checker"]["terminations"] = [
            {"kind": "signaled", "signal": 9}
        ]
        crashed = authoritative.build_verdict(
            request(),
            {"passed": [], "statement_revisions": {}},
            crashed_metrics,
            2,
            3,
        )
        self.assertEqual(crashed["execution_outcome"], "crashed")
        self.assertIsNone(crashed["checker_outcome"])

        timed_out = authoritative.reported_execution_verdict(
            request(),
            "timed_out",
            authoritative.normalize_metrics(metrics(checker_invocations=0)),
            2,
            3,
        )
        self.assertEqual(timed_out["execution_outcome"], "timed_out")
        self.assertEqual(
            timed_out["statistics"]["build_wall_time_ms"]
            + timed_out["statistics"]["checker_wall_time_ms"],
            authoritative.WALL_TIME_LIMIT_MS,
        )

        impossible = metrics()
        impossible["phases"]["build"]["invocations"] = 3
        impossible["phases"]["build"]["terminations"].append(
            {"kind": "exited", "code": 0}
        )
        with self.assertRaisesRegex(
            authoritative.AuthoritativeReplayError, "impossible phase counts"
        ):
            authoritative.build_verdict(
                request(),
                {
                    "passed": ["two_plus_two"],
                    "statement_revisions": {"two_plus_two": 3},
                },
                impossible,
                2,
                3,
            )

        with self.assertRaisesRegex(
            authoritative.AuthoritativeReplayError, "locked problem"
        ):
            authoritative.build_verdict(
                request(),
                {"passed": ["other"], "statement_revisions": {"other": 1}},
                metrics(),
                2,
                3,
            )


if __name__ == "__main__":
    unittest.main()
