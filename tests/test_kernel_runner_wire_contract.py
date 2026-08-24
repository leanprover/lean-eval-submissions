from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import unittest

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SCHEMAS = ROOT / "schemas"
sys.path.insert(0, str(ROOT / "scripts"))

from kernel_runner_wire_contract import (
    KernelRunnerWireError,
    canonical_configuration_bytes,
    validate_attestation,
    validate_export_metadata,
    validate_invocation,
    validate_transcript,
)

KINDS = (
    "kernel-solution-export-input-v1",
    "kernel-nanoda-invocation-v1",
    "kernel-runner-transcript-v1",
    "kernel-runner-attestation-v1",
)


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class KernelRunnerWireContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = (FIXTURES / "kernel-solution-export-input-v1.input").read_bytes()
        self.benchmark_config_raw = (
            FIXTURES / "kernel-benchmark-config-v1.input"
        ).read_bytes()
        self.metadata = load("kernel-solution-export-input-v1")
        self.invocation = load("kernel-nanoda-invocation-v1")
        self.transcript = load("kernel-runner-transcript-v1")
        self.attestation = load("kernel-runner-attestation-v1")

    def test_closed_schemas_and_fixtures_validate(self) -> None:
        for name in KINDS:
            with self.subTest(name=name):
                schema = json.loads(
                    (SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(load(name))

    def test_fixture_chain_passes_semantic_validation(self) -> None:
        metadata = validate_export_metadata(
            self.metadata, self.raw, self.benchmark_config_raw
        )
        invocation = validate_invocation(self.invocation, metadata)
        transcript = validate_transcript(self.transcript, invocation)
        validate_attestation(self.attestation, invocation, transcript)

    def test_raw_export_bytes_and_embedded_metadata_are_exact(self) -> None:
        changed = bytearray(self.raw)
        changed[-2] ^= 1
        with self.assertRaisesRegex(KernelRunnerWireError, "digest"):
            validate_export_metadata(
                self.metadata, bytes(changed), self.benchmark_config_raw
            )

        sidecar = copy.deepcopy(self.metadata)
        sidecar["lean"]["version"] = "4.34.0"
        with self.assertRaisesRegex(KernelRunnerWireError, "differs"):
            validate_export_metadata(sidecar, self.raw, self.benchmark_config_raw)

        duplicate_meta = self.raw + self.raw.splitlines(keepends=True)[0]
        sidecar = copy.deepcopy(self.metadata)
        sidecar["input_size_bytes"] = len(duplicate_meta)
        sidecar["input_sha256"] = hashlib.sha256(duplicate_meta).hexdigest()
        with self.assertRaisesRegex(KernelRunnerWireError, "more than one"):
            validate_export_metadata(sidecar, duplicate_meta, self.benchmark_config_raw)

        injected = self.raw + b'{"source_file":"Solution.lean"}\n'
        sidecar = copy.deepcopy(self.metadata)
        sidecar["input_size_bytes"] = len(injected)
        sidecar["input_sha256"] = hashlib.sha256(injected).hexdigest()
        with self.assertRaisesRegex(KernelRunnerWireError, "unregistered record"):
            validate_export_metadata(sidecar, injected, self.benchmark_config_raw)

        for hostile in (
            b'{"str":{"pre":0,"str":"\\ud800"},"in":2}\n',
            b'{"str":{"pre":0,"str":1e400},"in":2}\n',
        ):
            with self.subTest(hostile=hostile):
                injected = self.raw + hostile
                sidecar = copy.deepcopy(self.metadata)
                sidecar["input_size_bytes"] = len(injected)
                sidecar["input_sha256"] = hashlib.sha256(injected).hexdigest()
                with self.assertRaisesRegex(
                    KernelRunnerWireError, "invalid Unicode|non-finite"
                ):
                    validate_export_metadata(
                        sidecar, injected, self.benchmark_config_raw
                    )

        changed_config = self.benchmark_config_raw.replace(b"propext", b"sorryAx")
        with self.assertRaisesRegex(KernelRunnerWireError, "bound blob"):
            validate_export_metadata(self.metadata, self.raw, changed_config)

        changed_config_value = json.loads(self.benchmark_config_raw)
        changed_config_value["permitted_axioms"] = ["propext"]
        changed_config = json.dumps(
            changed_config_value, separators=(",", ":"), sort_keys=True
        ).encode()
        sidecar = copy.deepcopy(self.metadata)
        sidecar["benchmark_configuration"]["blob_sha256"] = hashlib.sha256(
            changed_config
        ).hexdigest()
        with self.assertRaisesRegex(KernelRunnerWireError, "differs from the sidecar"):
            validate_export_metadata(sidecar, self.raw, changed_config)

    def test_configuration_bytes_argv_environment_and_identity_are_fixed(self) -> None:
        metadata = validate_export_metadata(
            self.metadata, self.raw, self.benchmark_config_raw
        )
        expected = hashlib.sha256(
            canonical_configuration_bytes(self.invocation["configuration"])
        ).hexdigest()
        self.assertEqual(self.invocation["configuration_sha256"], expected)

        changes = (
            ("environment", {"HOME": "/tmp"}, "argv, paths, or environment"),
            ("argv", ["sokonanoda"], "argv, paths, or environment"),
        )
        for field, value, message in changes:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.invocation)
                changed[field] = value
                with self.assertRaisesRegex(KernelRunnerWireError, message):
                    validate_invocation(changed, metadata)

        changed = copy.deepcopy(self.invocation)
        changed["configuration"]["permitted_axioms"].reverse()
        with self.assertRaisesRegex(KernelRunnerWireError, "bounded sorted unique"):
            validate_invocation(changed, metadata)

        changed = copy.deepcopy(self.invocation)
        changed["configuration"]["permitted_axioms"].append("sorryAx")
        changed["configuration"]["permitted_axioms"].sort()
        changed["configuration_sha256"] = hashlib.sha256(
            canonical_configuration_bytes(changed["configuration"])
        ).hexdigest()
        with self.assertRaisesRegex(KernelRunnerWireError, "bound benchmark"):
            validate_invocation(changed, metadata)

        changed = copy.deepcopy(self.invocation)
        changed["checker"]["commit"] = "0" * 40
        with self.assertRaisesRegex(KernelRunnerWireError, "reviewed fixed checker"):
            validate_invocation(changed, metadata)

        changed = copy.deepcopy(self.invocation)
        changed["export_metadata_sha256"] = "0" * 64
        with self.assertRaisesRegex(KernelRunnerWireError, "export sidecar"):
            validate_invocation(changed, metadata)

    def _transcript_for(
        self,
        termination: dict,
        outcome: str | None,
        *,
        status: str = "classified",
        reason: str | None = None,
        invocations: int = 1,
    ) -> dict:
        value = copy.deepcopy(self.transcript)
        value["termination"] = termination
        value["classification"] = {
            "status": status,
            "outcome": outcome,
            "reason": reason,
        }
        value["statistics"]["checker_invocations"] = invocations
        if invocations == 0:
            value["statistics"]["wall_time_ms"] = 0
            value["statistics"]["peak_memory_bytes"] = 0
        return value

    def test_only_unambiguous_process_results_are_classified(self) -> None:
        metadata = validate_export_metadata(
            self.metadata, self.raw, self.benchmark_config_raw
        )
        invocation = validate_invocation(self.invocation, metadata)
        cases = (
            ({"kind": "exited", "code": 0}, "accepted", 1),
            ({"kind": "exited", "code": 2}, "declined", 1),
            ({"kind": "signaled", "signal": 11}, "crashed", 1),
            (
                {
                    "kind": "memory_limit",
                    "evidence_sha256": "d" * 64,
                    "limiter_code": "cgroup_memory_max",
                },
                "crashed",
                1,
            ),
            (
                {
                    "kind": "timed_out",
                    "evidence_sha256": "e" * 64,
                    "limiter_code": "runner_wall_timer",
                },
                "timed_out",
                1,
            ),
            (
                {
                    "kind": "not_started",
                    "reason": "export_unavailable",
                    "evidence_sha256": "a" * 64,
                    "validator_code": "input_absent",
                    "observed_input_sha256": None,
                },
                "export_unavailable",
                0,
            ),
            (
                {
                    "kind": "not_started",
                    "reason": "export_format_unsupported",
                    "evidence_sha256": "b" * 64,
                    "validator_code": "unsupported_format",
                    "observed_input_sha256": "c" * 64,
                },
                "export_format_unsupported",
                0,
            ),
        )
        for termination, outcome, invocations in cases:
            with self.subTest(outcome=outcome):
                value = self._transcript_for(
                    termination, outcome, invocations=invocations
                )
                if termination["kind"] == "timed_out":
                    value["statistics"]["wall_time_ms"] = 600000
                elif termination["kind"] == "memory_limit":
                    value["statistics"]["peak_memory_bytes"] = 8589934592
                validate_transcript(value, invocation)
                schema = json.loads(
                    (SCHEMAS / "kernel-runner-transcript-v1.schema.json").read_text(
                        encoding="utf-8"
                    )
                )
                Draft202012Validator(schema).validate(value)

    def test_exit_one_is_blocked_never_guessed_as_rejected_or_crashed(self) -> None:
        metadata = validate_export_metadata(
            self.metadata, self.raw, self.benchmark_config_raw
        )
        invocation = validate_invocation(self.invocation, metadata)
        blocked = self._transcript_for(
            {"kind": "exited", "code": 1},
            None,
            status="blocked",
            reason="ambiguous_exit_status",
        )
        validate_transcript(blocked, invocation)

        for guessed in ("rejected", "crashed"):
            with self.subTest(guessed=guessed):
                changed = copy.deepcopy(blocked)
                changed["classification"] = {
                    "status": "classified",
                    "outcome": guessed,
                    "reason": None,
                }
                with self.assertRaisesRegex(KernelRunnerWireError, "not implied"):
                    validate_transcript(changed, invocation)
                schema = json.loads(
                    (SCHEMAS / "kernel-runner-transcript-v1.schema.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertTrue(list(Draft202012Validator(schema).iter_errors(changed)))

    def test_unknown_exit_and_attestation_drift_fail_closed(self) -> None:
        metadata = validate_export_metadata(
            self.metadata, self.raw, self.benchmark_config_raw
        )
        invocation = validate_invocation(self.invocation, metadata)
        blocked = self._transcript_for(
            {"kind": "exited", "code": 125},
            None,
            status="blocked",
            reason="unregistered_exit_status",
        )
        validate_transcript(blocked, invocation)

        resource_signal = self._transcript_for(
            {"kind": "signaled", "signal": 9},
            None,
            status="blocked",
            reason="ambiguous_signal",
        )
        validate_transcript(resource_signal, invocation)

        external_signal = self._transcript_for(
            {"kind": "signaled", "signal": 15},
            None,
            status="blocked",
            reason="ambiguous_signal",
        )
        validate_transcript(external_signal, invocation)

        schema = json.loads(
            (SCHEMAS / "kernel-runner-transcript-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        for original, guessed in (
            (blocked, "crashed"),
            (resource_signal, "crashed"),
        ):
            with self.subTest(termination=original["termination"]):
                changed = copy.deepcopy(original)
                changed["classification"] = {
                    "status": "classified",
                    "outcome": guessed,
                    "reason": None,
                }
                self.assertTrue(list(Draft202012Validator(schema).iter_errors(changed)))

        transcript = validate_transcript(self.transcript, invocation)
        for path in ("network_disabled", "destroyed", "credentials_absent"):
            with self.subTest(path=path):
                changed = copy.deepcopy(self.attestation)
                changed["isolation"][path] = False
                with self.assertRaisesRegex(KernelRunnerWireError, "isolation"):
                    validate_attestation(changed, invocation, transcript)

        changed = copy.deepcopy(self.attestation)
        changed["transcript_sha256"] = "0" * 64
        with self.assertRaisesRegex(KernelRunnerWireError, "exact execution"):
            validate_attestation(changed, invocation, transcript)

        changed_transcript = copy.deepcopy(self.transcript)
        changed_transcript["stdout_sha256"] = "d" * 64
        with self.assertRaisesRegex(KernelRunnerWireError, "empty digest"):
            validate_transcript(changed_transcript, invocation)

        changed_transcript = copy.deepcopy(self.transcript)
        changed_transcript["stdout_size_bytes"] = 1
        with self.assertRaisesRegex(KernelRunnerWireError, "nonempty digest"):
            validate_transcript(changed_transcript, invocation)


if __name__ == "__main__":
    unittest.main()
