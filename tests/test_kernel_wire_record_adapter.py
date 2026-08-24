from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

from kernel_corpus_report import (
    KernelCorpusError,
    build_shard_plans,
    canonical_bytes,
    configuration_id,
    inventory_id,
)
from kernel_runner_wire_contract import (
    canonical_configuration_bytes,
    invocation_sha256,
    transcript_sha256,
)
import kernel_wire_record_adapter as wire_adapter
from kernel_wire_record_adapter import (
    KernelWireRecordError,
    WIRE_SUFFIXES,
    build_record_bundle,
    candidate_configuration_policy_sha256,
    main,
)
from tests.test_kernel_corpus_report import inventory, series


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class WireFixture:
    def __init__(
        self, root: pathlib.Path, benchmark_axioms: list[str] | None = None
    ) -> None:
        self.root = root
        self.raw = (FIXTURES / "kernel-solution-export-input-v1.input").read_bytes()
        self.benchmark = (FIXTURES / "kernel-benchmark-config-v1.input").read_bytes()
        self.metadata = load("kernel-solution-export-input-v1")
        self.invocation = load("kernel-nanoda-invocation-v1")
        self.transcript = load("kernel-runner-transcript-v1")
        self.attestation = load("kernel-runner-attestation-v1")
        if benchmark_axioms is not None:
            benchmark_value = json.loads(self.benchmark)
            benchmark_value["permitted_axioms"] = benchmark_axioms
            self.benchmark = (
                json.dumps(benchmark_value, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            self.metadata["benchmark_configuration"]["blob_sha256"] = (
                hashlib.sha256(self.benchmark).hexdigest()
            )
            self.metadata["benchmark_configuration"]["permitted_axioms"] = (
                benchmark_axioms
            )
            self.invocation["configuration"]["permitted_axioms"] = sorted(
                benchmark_axioms
            )
            self.invocation["configuration_sha256"] = hashlib.sha256(
                canonical_configuration_bytes(self.invocation["configuration"])
            ).hexdigest()

        self.series = series()
        self.series["candidate"] = {
            field: self.invocation["checker"][field]
            for field in ("name", "repository", "commit", "binary_sha256", "protocol")
        }
        self.series["candidate"]["configuration_policy_sha256"] = (
            candidate_configuration_policy_sha256(self.invocation["configuration"])
        )
        self.series["producer_profiles"] = [
            {
                "benchmark_repository": self.metadata["benchmark_configuration"][
                    "repository"
                ],
                "benchmark_commit": self.metadata["benchmark_configuration"][
                    "commit"
                ],
                "exporter": {
                    **self.metadata["exporter"],
                    "format_version": self.metadata["format_version"],
                },
                "lean": copy.deepcopy(self.metadata["lean"]),
            }
        ]
        self.series["runner"] = {
            **{
                field: self.attestation["runner"][field]
                for field in (
                    "repository",
                    "commit",
                    "image_digest",
                    "architecture",
                    "operating_system",
                )
            },
            "resource_limits": {
                "wall_timeout_seconds": self.invocation["resource_limits"][
                    "wall_timeout_ms"
                ]
                // 1000,
                "max_memory_bytes": self.invocation["resource_limits"][
                    "max_memory_bytes"
                ],
            },
        }
        self.series["configuration_id"] = configuration_id(self.series)

        self.inventory = inventory()
        row = copy.deepcopy(self.inventory["results"][0])
        row.update(
            {
                "result_id": self.metadata["result_id"],
                "replay_task_id": self.metadata["replay_task_id"],
                "replay_attempt": self.metadata["replay_attempt"],
                "problem_id": self.metadata["benchmark_configuration"]["problem_id"],
                "statement_revision": self.metadata["benchmark_configuration"][
                    "statement_revision"
                ],
                "benchmark_repository": self.metadata["benchmark_configuration"][
                    "repository"
                ],
                "benchmark_commit": self.metadata["benchmark_configuration"][
                    "commit"
                ],
                "benchmark_configuration_sha256": self.metadata[
                    "benchmark_configuration"
                ]["blob_sha256"],
                "terminal_verdict_sha256": self.metadata["terminal_evidence"][
                    "terminal_verdict_sha256"
                ],
                "terminal_event_sha256": self.metadata["terminal_evidence"][
                    "terminal_event_sha256"
                ],
                "report_entry_sha256": self.metadata["terminal_evidence"][
                    "report_entry_sha256"
                ],
                "replay_export_input_sha256": hashlib.sha256(self.raw).hexdigest(),
                "authoritative_outcome": "accepted",
                "availability": "ready",
                "unavailability_evidence_sha256": None,
            }
        )
        self.inventory["results"] = [row]
        self.inventory["inventory_id"] = inventory_id(self.inventory)
        self.plan = build_shard_plans(self.series, self.inventory, 1)[0]
        self.attempt = self.plan["attempts"][0]

        self.invocation["attempt_id"] = self.attempt["attempt_id"]
        self.invocation["export_metadata_sha256"] = digest(self.metadata)
        self.transcript.update(
            {
                "attempt_id": self.attempt["attempt_id"],
                "input_sha256": self.attempt["replay_export_input_sha256"],
                "invocation_sha256": invocation_sha256(self.invocation),
            }
        )
        self.attestation.update(
            {
                "attempt_id": self.attempt["attempt_id"],
                "input_sha256": self.attempt["replay_export_input_sha256"],
                "invocation_sha256": invocation_sha256(self.invocation),
                "transcript_sha256": transcript_sha256(self.transcript),
            }
        )
        self.inputs = root / "inputs"
        self.wire = root / "wire"
        self.inputs.mkdir()
        self.wire.mkdir()
        self._write()

    def _write(self) -> None:
        prefix = self.attempt["attempt_id"]
        (self.inputs / f"{prefix}.input").write_bytes(self.raw)
        values = {
            ".export-metadata.json": self.metadata,
            ".benchmark-config.input": self.benchmark,
            ".invocation.json": self.invocation,
            ".transcript.json": self.transcript,
            ".attestation.json": self.attestation,
        }
        for suffix, value in values.items():
            path = self.wire / f"{prefix}{suffix}"
            if isinstance(value, bytes):
                path.write_bytes(value)
            else:
                path.write_text(json.dumps(value), encoding="utf-8")

    def refresh_execution_chain(self) -> None:
        self.invocation["export_metadata_sha256"] = digest(self.metadata)
        self.transcript["invocation_sha256"] = invocation_sha256(self.invocation)
        self.attestation["invocation_sha256"] = invocation_sha256(self.invocation)
        self.attestation["transcript_sha256"] = transcript_sha256(self.transcript)
        self._write()

    def build(self) -> dict:
        return build_record_bundle(
            self.series, self.inventory, self.plan, self.inputs, self.wire
        )


class KernelWireRecordAdapterTests(unittest.TestCase):
    def test_exact_wire_chain_becomes_existing_runner_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(pathlib.Path(directory))
            bundle = fixture.build()
        self.assertEqual(bundle["configuration_id"], fixture.series["configuration_id"])
        self.assertEqual(bundle["inventory_id"], fixture.inventory["inventory_id"])
        self.assertEqual(bundle["shard_id"], fixture.plan["shard_id"])
        self.assertEqual(len(bundle["records"]), 1)
        record = bundle["records"][0]
        self.assertEqual(record["attempt_id"], fixture.attempt["attempt_id"])
        self.assertEqual(record["outcome"], "accepted")
        self.assertEqual(
            record["input_sha256"], hashlib.sha256(fixture.raw).hexdigest()
        )
        self.assertEqual(record["transcript_sha256"], digest(fixture.transcript))
        self.assertEqual(
            record["runner_attestation_sha256"], digest(fixture.attestation)
        )
        self.assertTrue(record["source_free"])

    def test_historical_benchmark_axiom_order_normalizes_for_invocation(self) -> None:
        historical_order = ["propext", "Quot.sound", "Classical.choice"]
        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(
                pathlib.Path(directory), benchmark_axioms=historical_order
            )
            bundle = fixture.build()
        self.assertEqual(
            fixture.metadata["benchmark_configuration"]["permitted_axioms"],
            historical_order,
        )
        self.assertEqual(
            fixture.invocation["configuration"]["permitted_axioms"],
            sorted(historical_order),
        )
        self.assertEqual(bundle["records"][0]["outcome"], "accepted")

    def test_ambiguous_exit_one_cannot_become_a_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(pathlib.Path(directory))
            fixture.transcript["termination"] = {"kind": "exited", "code": 1}
            fixture.transcript["classification"] = {
                "status": "blocked",
                "outcome": None,
                "reason": "ambiguous_exit_status",
            }
            fixture.refresh_execution_chain()
            with self.assertRaisesRegex(KernelWireRecordError, "cannot become"):
                fixture.build()

    def test_possessed_input_cannot_be_dropped_as_an_export_failure(self) -> None:
        for outcome, observed, message in (
            ("export_unavailable", None, "cannot be export_unavailable"),
            ("export_format_unsupported", "0" * 64, "verified input"),
        ):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as raw:
                fixture = WireFixture(pathlib.Path(raw))
                fixture.transcript["termination"] = {
                    "kind": "not_started",
                    "reason": outcome,
                    "evidence_sha256": "1" * 64,
                    "validator_code": "kernel_export_v1",
                    "observed_input_sha256": observed,
                }
                fixture.transcript["classification"] = {
                    "status": "classified",
                    "outcome": outcome,
                    "reason": None,
                }
                fixture.transcript["statistics"] = {
                    "wall_time_ms": 0,
                    "peak_memory_bytes": 0,
                    "checker_invocations": 0,
                }
                fixture.refresh_execution_chain()
                with self.assertRaisesRegex(KernelWireRecordError, message):
                    fixture.build()

    def test_exact_attempt_terminal_series_and_runner_bindings_are_required(
        self,
    ) -> None:
        mutations = (
            (
                "terminal",
                lambda fixture: fixture.metadata["terminal_evidence"].update(
                    {"terminal_event_sha256": "0" * 64}
                ),
                "terminal_event_sha256",
            ),
            (
                "attempt",
                lambda fixture: fixture.invocation.update(
                    {"attempt_id": "kca1_" + "0" * 64}
                ),
                "attempt_id",
            ),
            (
                "candidate",
                lambda fixture: fixture.invocation["checker"].update(
                    {"binary_sha256": "0" * 64}
                ),
                "checker.binary_sha256",
            ),
            (
                "comparison framework",
                lambda fixture: fixture.metadata["comparison_framework"].update(
                    {"commit": "0" * 40}
                ),
                "comparison framework.commit",
            ),
            (
                "exporter",
                lambda fixture: fixture.metadata["exporter"].update(
                    {"commit": "0" * 40}
                ),
                "exporter.commit",
            ),
            (
                "Lean toolchain",
                lambda fixture: fixture.metadata["lean"].update(
                    {"toolchain": "leanprover/lean4:v4.32.2"}
                ),
                "Lean toolchain",
            ),
            (
                "benchmark provenance",
                lambda fixture: fixture.metadata["benchmark_configuration"].update(
                    {"commit": "0" * 40}
                ),
                "benchmark_configuration.commit",
            ),
            (
                "runner",
                lambda fixture: fixture.attestation["runner"].update(
                    {"commit": "0" * 40}
                ),
                "runner.commit",
            ),
        )
        for name, mutate, message in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture = WireFixture(pathlib.Path(directory))
                mutate(fixture)
                if name == "attempt":
                    fixture.transcript["attempt_id"] = fixture.invocation["attempt_id"]
                    fixture.attestation["attempt_id"] = fixture.invocation["attempt_id"]
                fixture.refresh_execution_chain()
                with self.assertRaisesRegex(KernelWireRecordError, message):
                    fixture.build()

    def test_series_binds_the_canonical_candidate_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(pathlib.Path(directory))
            fixture.series["candidate"]["configuration_policy_sha256"] = "0" * 64
            fixture.series["configuration_id"] = configuration_id(fixture.series)
            fixture.plan = build_shard_plans(fixture.series, fixture.inventory, 1)[0]
            fixture.attempt = fixture.plan["attempts"][0]
            fixture.invocation["attempt_id"] = fixture.attempt["attempt_id"]
            fixture.transcript["attempt_id"] = fixture.attempt["attempt_id"]
            fixture.attestation["attempt_id"] = fixture.attempt["attempt_id"]
            for path in (*fixture.inputs.iterdir(), *fixture.wire.iterdir()):
                path.unlink()
            fixture.refresh_execution_chain()
            with self.assertRaisesRegex(
                KernelWireRecordError, "candidate configuration policy"
            ):
                fixture.build()

    def test_candidate_policy_digest_excludes_per_problem_axioms(self) -> None:
        configuration = load("kernel-nanoda-invocation-v1")["configuration"]
        changed = copy.deepcopy(configuration)
        changed["permitted_axioms"] = ["Classical.choice"]
        self.assertNotEqual(configuration, changed)
        self.assertEqual(
            candidate_configuration_policy_sha256(configuration),
            candidate_configuration_policy_sha256(changed),
        )

    def test_multi_attempt_shard_accepts_distinct_problem_axiom_lists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(pathlib.Path(directory))
            metadata_values = [fixture.metadata, copy.deepcopy(fixture.metadata)]
            invocation_values = [fixture.invocation, copy.deepcopy(fixture.invocation)]
            transcript_values = [fixture.transcript, copy.deepcopy(fixture.transcript)]
            attestation_values = [
                fixture.attestation,
                copy.deepcopy(fixture.attestation),
            ]
            benchmark_values = [fixture.benchmark]
            second_benchmark = json.loads(fixture.benchmark)
            second_benchmark["permitted_axioms"] = ["Classical.choice"]
            second_benchmark_raw = (
                json.dumps(second_benchmark, sort_keys=True, separators=(",", ":"))
                .encode("utf-8")
            )
            benchmark_values.append(second_benchmark_raw)
            second_metadata = metadata_values[1]
            second_metadata.update(
                {
                    "result_id": "r2_" + "3" * 64,
                    "replay_task_id": "rt1_" + "2" * 64,
                    "replay_attempt": 2,
                }
            )
            second_metadata["benchmark_configuration"].update(
                {
                    "problem_id": "axiom_variant",
                    "path": "generated/axiom_variant/config.json",
                    "blob_sha256": hashlib.sha256(second_benchmark_raw).hexdigest(),
                    "permitted_axioms": ["Classical.choice"],
                }
            )
            second_invocation = invocation_values[1]
            second_invocation["configuration"]["permitted_axioms"] = [
                "Classical.choice"
            ]
            second_invocation["configuration_sha256"] = hashlib.sha256(
                canonical_configuration_bytes(second_invocation["configuration"])
            ).hexdigest()

            rows = [fixture.inventory["results"][0]]
            second_row = copy.deepcopy(rows[0])
            second_row.update(
                {
                    "result_id": second_metadata["result_id"],
                    "replay_task_id": second_metadata["replay_task_id"],
                    "replay_attempt": second_metadata["replay_attempt"],
                    "problem_id": "axiom_variant",
                    "benchmark_configuration_sha256": second_metadata[
                        "benchmark_configuration"
                    ]["blob_sha256"],
                }
            )
            rows.append(second_row)
            fixture.inventory["results"] = rows
            fixture.inventory["inventory_id"] = inventory_id(fixture.inventory)
            fixture.plan = build_shard_plans(fixture.series, fixture.inventory, 1)[0]
            for path in (*fixture.inputs.iterdir(), *fixture.wire.iterdir()):
                path.unlink()
            for attempt, metadata, invocation, transcript, attestation, benchmark in zip(
                fixture.plan["attempts"],
                metadata_values,
                invocation_values,
                transcript_values,
                attestation_values,
                benchmark_values,
                strict=True,
            ):
                invocation["attempt_id"] = attempt["attempt_id"]
                invocation["export_metadata_sha256"] = digest(metadata)
                transcript.update(
                    {
                        "attempt_id": attempt["attempt_id"],
                        "input_sha256": attempt["replay_export_input_sha256"],
                        "invocation_sha256": invocation_sha256(invocation),
                    }
                )
                attestation.update(
                    {
                        "attempt_id": attempt["attempt_id"],
                        "input_sha256": attempt["replay_export_input_sha256"],
                        "invocation_sha256": invocation_sha256(invocation),
                        "transcript_sha256": transcript_sha256(transcript),
                    }
                )
                prefix = attempt["attempt_id"]
                (fixture.inputs / f"{prefix}.input").write_bytes(fixture.raw)
                for suffix, value in (
                    (".export-metadata.json", metadata),
                    (".benchmark-config.input", benchmark),
                    (".invocation.json", invocation),
                    (".transcript.json", transcript),
                    (".attestation.json", attestation),
                ):
                    path = fixture.wire / f"{prefix}{suffix}"
                    if isinstance(value, bytes):
                        path.write_bytes(value)
                    else:
                        path.write_text(json.dumps(value), encoding="utf-8")
            bundle = build_record_bundle(
                fixture.series,
                fixture.inventory,
                fixture.plan,
                fixture.inputs,
                fixture.wire,
            )
        self.assertEqual(
            [record["attempt_id"] for record in bundle["records"]],
            [attempt["attempt_id"] for attempt in fixture.plan["attempts"]],
        )

    def test_wire_directory_membership_and_file_types_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(pathlib.Path(directory))
            (fixture.wire / "unexpected.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(KernelWireRecordError, "membership"):
                fixture.build()

        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(pathlib.Path(directory))
            prefix = fixture.attempt["attempt_id"]
            target = fixture.root / "outside.json"
            target.write_text("{}", encoding="utf-8")
            path = fixture.wire / f"{prefix}.attestation.json"
            path.unlink()
            path.symlink_to(target)
            with self.assertRaisesRegex(KernelCorpusError, "regular non-symlink"):
                fixture.build()

    def test_in_place_wire_mutation_during_validation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(pathlib.Path(directory))
            original_load = wire_adapter._load_wire_json
            mutated = False

            def mutate_then_load(*args: object, **kwargs: object) -> tuple[object, int]:
                nonlocal mutated
                if not mutated:
                    mutated = True
                    target = fixture.wire / (
                        f"{fixture.attempt['attempt_id']}.benchmark-config.input"
                    )
                    target.write_bytes(target.read_bytes())
                return original_load(*args, **kwargs)

            with mock.patch.object(
                wire_adapter, "_load_wire_json", side_effect=mutate_then_load
            ):
                with self.assertRaisesRegex(KernelWireRecordError, "files changed"):
                    fixture.build()

    def test_input_directory_descriptor_closes_when_wire_open_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(pathlib.Path(directory))
            with (
                mock.patch.object(
                    wire_adapter,
                    "_open_input_directory",
                    side_effect=[
                        91,
                        wire_adapter.KernelCorpusRunnerError("wire open failed"),
                    ],
                ),
                mock.patch.object(wire_adapter.os, "close") as close,
                self.assertRaisesRegex(KernelCorpusError, "wire open failed"),
            ):
                fixture.build()
            self.assertEqual(close.call_args_list.count(mock.call(91)), 1)

    def test_exclusive_cli_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(pathlib.Path(directory))
            paths = {
                "series": fixture.root / "series.json",
                "inventory": fixture.root / "inventory.json",
                "plan": fixture.root / "plan.json",
            }
            for name, path in paths.items():
                path.write_text(json.dumps(getattr(fixture, name)), encoding="utf-8")
            output = fixture.root / "records.json"
            args = [
                "--series",
                str(paths["series"]),
                "--inventory",
                str(paths["inventory"]),
                "--plan",
                str(paths["plan"]),
                "--inputs-dir",
                str(fixture.inputs),
                "--wire-dir",
                str(fixture.wire),
                "--output",
                str(output),
            ]
            self.assertEqual(main(args), 0)
            original = output.read_bytes()
            self.assertEqual(main(args), 1)
            self.assertEqual(output.read_bytes(), original)

    def test_empty_deterministic_shard_emits_an_empty_bundle(self) -> None:
        selected_series = series()
        selected_inventory = inventory()
        plan = next(
            item
            for item in build_shard_plans(
                selected_series, selected_inventory, 7
            )
            if not item["attempts"]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            inputs = root / "inputs"
            wire = root / "wire"
            inputs.mkdir()
            wire.mkdir()
            bundle = build_record_bundle(
                selected_series, selected_inventory, plan, inputs, wire
            )
        self.assertEqual(bundle["shard_id"], plan["shard_id"])
        self.assertEqual(bundle["records"], [])

    def test_wire_directory_has_an_aggregate_byte_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WireFixture(pathlib.Path(directory))
            with mock.patch(
                "kernel_wire_record_adapter.MAX_WIRE_DIRECTORY_BYTES", 1
            ):
                with self.assertRaisesRegex(KernelWireRecordError, "aggregate"):
                    fixture.build()

    def test_adapter_has_no_executor_network_or_credential_interface(self) -> None:
        source = (ROOT / "scripts" / "kernel_wire_record_adapter.py").read_text()
        for forbidden in (
            "import subprocess",
            "import socket",
            "import urllib",
            "requests",
            "os.environ",
            "os.getenv",
            "os.system",
            "os.popen",
            "os.exec",
            "os.spawn",
            "import http",
            "import ssl",
            "getpass",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source.lower())

        self.assertEqual(
            set(WIRE_SUFFIXES),
            {
                ".export-metadata.json",
                ".benchmark-config.input",
                ".invocation.json",
                ".transcript.json",
                ".attestation.json",
            },
        )


if __name__ == "__main__":
    unittest.main()
