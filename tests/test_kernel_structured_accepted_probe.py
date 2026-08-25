from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from kernel_structured_accepted_probe import (
    BLOCKED_STATUS,
    PROVISIONAL_STATUS,
    StructuredAcceptedProbeError,
    _validate_structured_result,
    build_attempt,
    canonical,
    digest,
    require_runnable,
    validate_attestation,
    validate_attempt,
    validate_fixture,
    validate_fixture_sources,
)

FIXTURE = ROOT / "tests/fixtures/kernel-structured-accepted-probe-v1.json"
PLAN = ROOT / "evidence/public-replay/plans/d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e.json"
MATRIX = ROOT / "configuration/historical-public-replay-profile-matrix-v1.json"
SMOKE = ROOT / "tests/fixtures/public-replay-smoke-v1.json"


class StructuredAcceptedProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = validate_fixture(json.loads(FIXTURE.read_text()))

    def test_fixed_target_is_bound_to_committed_public_evidence(self) -> None:
        validate_fixture_sources(
            self.fixture, plan_path=PLAN, matrix_path=MATRIX, smoke_path=SMOKE
        )
        self.assertEqual(self.fixture["qualification_status"], BLOCKED_STATUS)
        self.assertEqual(
            self.fixture["candidate"]["upstream_status"], PROVISIONAL_STATUS
        )
        self.assertEqual(
            self.fixture["target"]["result_id"],
            "r2_c4e178fbb6cdafcb8f2146245adf02a709a60836f022e8a3d75d72c84b472b60",
        )

    def test_provisional_upstream_head_is_a_hard_execution_block(self) -> None:
        with self.assertRaisesRegex(StructuredAcceptedProbeError, "PR #51"):
            require_runnable(self.fixture)
        incoherent = copy.deepcopy(self.fixture)
        incoherent["qualification_status"] = "ready_for_staging_probe"
        with self.assertRaisesRegex(StructuredAcceptedProbeError, "upstream-owned"):
            validate_fixture(incoherent)
        hostile = copy.deepcopy(self.fixture)
        hostile["target"]["statement_revision"] = True
        with self.assertRaisesRegex(StructuredAcceptedProbeError, "target changed"):
            validate_fixture(hostile)

    def _attempt(self) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "source.tar.gz"
            archive.write_bytes(b"public source archive fixture")
            return build_attempt(
                canonical(self.fixture),
                canonical({"kind": "handoff-fixture"}),
                archive,
                nonce="1" * 64,
                runner_source_commit="2" * 40,
                runner_image_id="sha256:" + "3" * 64,
                candidate_binary_sha256="4" * 64,
            )

    def test_attempt_id_binds_every_exact_input(self) -> None:
        attempt = validate_attempt(self._attempt())
        for field in (
            "nonce",
            "fixture_sha256",
            "handoff_sha256",
            "source_archive_sha256",
            "runner_source_commit",
            "runner_image_id",
            "candidate_binary_sha256",
        ):
            hostile = copy.deepcopy(attempt)
            value = hostile[field]
            hostile[field] = value[:-1] + ("0" if value[-1] != "0" else "1")
            with self.subTest(field=field):
                with self.assertRaises(StructuredAcceptedProbeError):
                    validate_attempt(hostile)

    def _attestation(self, attempt: dict) -> dict:
        target = self.fixture["target"]
        verdict = {
            "schema_version": 1,
            "request_id": target["request_id"],
            "result_id": target["result_id"],
            "execution_outcome": "completed",
            "checker_outcome": "accepted",
            "failure_reason": None,
            "statistics": {
                "checker_wall_time_ms": 1,
                "checker_retired_instructions": {
                    "status": "unavailable",
                    "reason": "counter_not_supported",
                },
                "build_wall_time_ms": 1,
                "build_retired_instructions": {
                    "status": "unavailable",
                    "reason": "counter_not_supported",
                },
                "lines_of_code": 1,
                "file_count": 1,
            },
        }
        return {
            "schema_version": 1,
            "kind": "kernel_structured_accepted_probe_attestation",
            "qualification_status": "provisional",
            "scope": "staging_only",
            "attempt_id": attempt["attempt_id"],
            "attempt_sha256": digest(attempt),
            "runner": {
                "source_commit": attempt["runner_source_commit"],
                "image_id": attempt["runner_image_id"],
                "environment": "replay-staging",
                "architecture": "x86_64",
                "kernel_release": "fixture-kernel",
                "network": "disabled_active_probe",
                "credentials": "absent",
                "source_free_handoff": True,
            },
            "target": {
                "request_id": target["request_id"],
                "result_id": target["result_id"],
                "problem_id": target["problem_id"],
                "statement_revision": target["statement_revision"],
                "source_repository": target["source_repository"],
                "source_commit": target["source_commit"],
                "source_tree": target["source_tree"],
                "source_archive_sha256": attempt["source_archive_sha256"],
                "benchmark_repository": target["benchmark_repository"],
                "benchmark_commit": target["benchmark_commit"],
                "benchmark_configuration_sha256": target[
                    "benchmark_configuration_sha256"
                ],
            },
            "pipeline": {
                "verdict": verdict,
                "verdict_sha256": digest(verdict),
                "results_sha256": "5" * 64,
                "metrics_sha256": "6" * 64,
            },
            "export": {
                "exporter": {"name": "lean4export", "version": "3.1.0"},
                "lean": {"version": "4.32.2", "githash": "7" * 40},
                "format": {"version": "3.1.0"},
                "line_count": 2,
                "size_bytes": 128,
                "sha256": "8" * 64,
                "source_free": True,
                "capture": "same_evaluator_process_before_cleanup",
            },
            "candidate": {
                "repository": self.fixture["candidate"]["repository"],
                "commit": self.fixture["candidate"]["commit"],
                "source_tree": self.fixture["candidate"]["source_tree"],
                "binary_sha256": attempt["candidate_binary_sha256"],
                "protocol": "sokonanoda_result_v1",
                "argv": [
                    "/opt/lean-eval/bin/sokonanoda",
                    "--result-file",
                    "/run/lean-eval/kernel-output/sokonanoda-result.json",
                    "/run/lean-eval/nanoda-config.json",
                ],
                "sandbox_argv": [
                    "/usr/local/bin/landrun",
                    "--rox", "/opt/lean-eval/bin/sokonanoda",
                    "--ro", "/run/lean-eval/solution-export.ndjson",
                    "--ro", "/run/lean-eval/nanoda-config.json",
                    "--rox", "/lib", "--rox", "/lib64", "--rox", "/usr/lib",
                    "--ro", "/etc/ld.so.cache",
                    "--rw", "/run/lean-eval/kernel-output",
                    "--", "/opt/lean-eval/bin/sokonanoda", "--result-file",
                    "/run/lean-eval/kernel-output/sokonanoda-result.json",
                    "/run/lean-eval/nanoda-config.json",
                ],
                "filesystem_policy": "closed_kernel_inputs_v1",
                "resource_limits": {
                    "wall_timeout_ms": 900_000,
                    "maximum_output_file_bytes": 16 * 1024 * 1024,
                    "maximum_open_files": 128,
                    "core_dump_bytes": 0,
                },
                "environment": {},
                "configuration_sha256": "9" * 64,
                "result_sha256": "a" * 64,
                "result": {
                    "schema_version": 1,
                    "protocol": "sokonanoda_result_v1",
                    "outcome": "accepted",
                    "reason_code": "checked",
                },
                "termination": {"exit_code": 0, "timed_out": False},
                "statistics": {
                    "wall_time_ms": 1,
                    "stdout_size_bytes": 0,
                    "stdout_sha256": hashlib.sha256(b"").hexdigest(),
                    "stderr_size_bytes": 0,
                    "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                },
            },
            "outcome": "accepted",
        }

    def test_attestation_is_closed_and_source_free(self) -> None:
        attempt = validate_attempt(self._attempt())
        attestation = self._attestation(attempt)
        validate_attestation(attestation, self.fixture, attempt)
        hostile = copy.deepcopy(attestation)
        hostile["target"]["source_bytes"] = "theorem secret"
        with self.assertRaisesRegex(StructuredAcceptedProbeError, "target fields"):
            validate_attestation(hostile, self.fixture, attempt)
        hostile = copy.deepcopy(attestation)
        hostile["candidate"]["stderr"] = "source-shaped output"
        with self.assertRaisesRegex(StructuredAcceptedProbeError, "candidate fields"):
            validate_attestation(hostile, self.fixture, attempt)
        hostile = copy.deepcopy(attestation)
        hostile["pipeline"]["verdict"]["source"] = "theorem secret"
        with self.assertRaisesRegex(ValueError, "verdict"):
            validate_attestation(hostile, self.fixture, attempt)

    def test_only_exact_structured_accepted_exit_pair_passes(self) -> None:
        accepted = (
            b'{"schema_version":1,"protocol":"sokonanoda_result_v1",'
            b'"outcome":"accepted","reason_code":"checked"}\n'
        )
        self.assertEqual(_validate_structured_result(accepted, 0)["outcome"], "accepted")
        for raw, exit_code in (
            (accepted, 1),
            (accepted.rstrip(), 0),
            (
                b'{"schema_version":1,"protocol":"sokonanoda_result_v1",'
                b'"outcome":"rejected","reason_code":"declaration_type_mismatch"}\n',
                1,
            ),
        ):
            with self.subTest(exit_code=exit_code, raw=raw):
                with self.assertRaises(StructuredAcceptedProbeError):
                    _validate_structured_result(raw, exit_code)


class StructuredAcceptedWorkflowTests(unittest.TestCase):
    def test_manual_workflow_is_blocked_staging_only_and_source_free(self) -> None:
        workflow = (ROOT / ".github/workflows/kernel-structured-accepted-probe.yml").read_text()
        gate = workflow.index("Refuse execution until upstream merge")
        source_checkout = workflow.index("repository: KitaKen1/lean-eval-two-plus-two")
        image_build = workflow.index("Build the exact source-locked staging probe image")
        self.assertLess(gate, source_checkout)
        self.assertLess(gate, image_build)
        self.assertIn("environment: replay-staging", workflow)
        self.assertIn("refs/heads/v2-arena-candidate", workflow)
        self.assertIn(".base.repo.full_name", workflow)
        self.assertIn(".base.ref", workflow)
        self.assertNotIn(
            "metalogiclabs/mathgraph-lean-kernel.git refs/heads/main", workflow
        )
        self.assertIn("--network none", workflow)
        self.assertIn("--cap-drop ALL", workflow)
        self.assertIn("--security-opt no-new-privileges", workflow)
        self.assertIn("--result-file", (ROOT / "scripts/kernel_structured_accepted_probe.py").read_text())
        runner = (ROOT / "scripts/kernel_structured_accepted_probe.py").read_text()
        self.assertIn('"filesystem_policy": "closed_kernel_inputs_v1"', runner)
        self.assertNotIn('"--ro", "/",', runner)
        self.assertNotIn("id-token: write", workflow)
        self.assertNotIn("CLOUDFLARE_", workflow)
        self.assertNotIn("wrangler", workflow)
        artifact = workflow.split("uses: actions/upload-artifact", 1)[1]
        self.assertIn("kernel-structured-accepted-attestation.json", artifact)
        self.assertNotIn("historical-public-source.tar.gz", artifact)
        self.assertNotIn("solution-export.ndjson", artifact)

    def test_candidate_builder_is_fixed_and_separate_from_publishable_image(self) -> None:
        dockerfile = (ROOT / "Dockerfile.kernel-structured-accepted-probe").read_text()
        historical = (ROOT / "Dockerfile.historical-public-replay").read_text()
        for identity in (
            "04e06b93603232b72b18a8f9793a1f5fa125061d",
            "0794641beb63834a28bea0d569459f93eb753fd7",
            "b96b99526a143ae39a9e8d058f80337f34d3e7c153e9e1878d2c29d9a56767d9",
            "0712d67d88c65f10742ede70d0697360a0fc22b5ff79197f19050ae5e2812f4d",
        ):
            self.assertIn(identity, dockerfile)
        self.assertIn("cargo build --locked --release", dockerfile)
        self.assertIn(
            "rust:1.95.0-bookworm@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1",
            dockerfile,
        )
        self.assertIn("sha256sum /build/mathgraph/target/release/sokonanoda", dockerfile)
        self.assertNotIn("sokonanoda", historical)
        self.assertNotIn("kernel_structured_accepted_probe", historical)


if __name__ == "__main__":
    unittest.main()
