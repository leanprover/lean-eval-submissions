from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from kernel_corpus_report import (
    KernelCorpusError,
    build_shard_plans,
    canonical_bytes,
    configuration_id,
    execution_receipt_sha256,
    inventory_id,
)
from kernel_corpus_runner_adapter import (
    KernelCorpusRunnerError,
    _digest_input,
    _open_input_directory,
    main,
    materialize_observation_shard,
    validate_record_bundle,
)

from tests.test_kernel_corpus_report import inventory, series

OUTCOMES = (
    "accepted",
    "declined",
    "crashed",
    "timed_out",
    "export_unavailable",
    "export_format_unsupported",
)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class AdapterFixture:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.series = series()
        self.inventory = inventory()
        self.payloads: dict[str, bytes] = {}
        ready = [
            result
            for result in self.inventory["results"]
            if result["availability"] == "ready"
        ]
        for index, result in enumerate(ready):
            payload = f"reviewed-source-free-export-{index}\n".encode()
            input_sha256 = hashlib.sha256(payload).hexdigest()
            result["replay_export_input_sha256"] = input_sha256
            self.payloads[result["result_id"]] = payload
        self.inventory["inventory_id"] = inventory_id(self.inventory)
        self.plan = build_shard_plans(self.series, self.inventory, 1)[0]
        self.inputs = root / "inputs"
        self.inputs.mkdir()
        run_attempts = [
            attempt
            for attempt in self.plan["attempts"]
            if attempt["required_action"] == "run"
        ]
        for attempt in run_attempts:
            (self.inputs / f"{attempt['attempt_id']}.input").write_bytes(
                self.payloads[attempt["result_id"]]
            )
        self.bundle = {
            "schema_version": 1,
            "kind": "kernel_corpus_runner_records",
            "configuration_id": self.series["configuration_id"],
            "configuration_sha256": digest(self.series),
            "inventory_id": self.inventory["inventory_id"],
            "inventory_sha256": digest(self.inventory),
            "shard_id": self.plan["shard_id"],
            "records": [
                self.record(attempt, OUTCOMES[index % len(OUTCOMES)])
                for index, attempt in enumerate(run_attempts)
            ],
        }

    def record(self, attempt: dict, outcome: str) -> dict:
        timeout_ms = (
            self.series["runner"]["resource_limits"]["wall_timeout_seconds"] * 1_000
        )
        export_outcome = outcome.startswith("export_")
        memory_limit = outcome == "crashed"
        return {
            "attempt_id": attempt["attempt_id"],
            "input_sha256": attempt["replay_export_input_sha256"],
            "outcome": outcome,
            "evidence_sha256": "f" * 64 if export_outcome else None,
            "resource_limit_disposition": (
                "wall_timeout"
                if outcome == "timed_out"
                else "memory_limit"
                if memory_limit
                else "within_limits"
            ),
            "statistics": {
                "wall_time_ms": timeout_ms if outcome == "timed_out" else 100,
                "peak_memory_bytes": (
                    self.series["runner"]["resource_limits"]["max_memory_bytes"]
                    if memory_limit
                    else 1_000
                ),
                "checker_invocations": 0 if export_outcome else 1,
            },
            "transcript_sha256": "2" * 64,
            "runner_attestation_sha256": "3" * 64,
            "source_free": True,
        }

    def materialize(self) -> dict:
        return materialize_observation_shard(
            self.series,
            self.inventory,
            self.plan,
            self.bundle,
            self.inputs,
        )


class KernelCorpusRunnerAdapterTests(unittest.TestCase):
    def test_materializes_all_executed_and_inherited_outcome_classes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = AdapterFixture(pathlib.Path(directory))
            shard = fixture.materialize()
            by_outcome = {item["outcome"]: item for item in shard["observations"]}
            self.assertEqual(
                set(by_outcome),
                {
                    *OUTCOMES,
                    "source_unavailable",
                    "replay_unavailable",
                    "replay_pending",
                },
            )
            for outcome in OUTCOMES:
                receipt = by_outcome[outcome]["execution_receipt"]
                self.assertEqual(receipt["outcome"], outcome)
                self.assertTrue(receipt["source_free"])
                self.assertEqual(
                    receipt["configuration_id"], fixture.series["configuration_id"]
                )
                self.assertEqual(
                    receipt["receipt_sha256"], execution_receipt_sha256(receipt)
                )
            for outcome in (
                "source_unavailable",
                "replay_unavailable",
                "replay_pending",
            ):
                self.assertIsNone(by_outcome[outcome]["statistics"])
                self.assertIsNone(by_outcome[outcome]["execution_receipt"])
            serialized = json.dumps(shard, sort_keys=True)
            self.assertNotIn(str(fixture.root), serialized)
            self.assertNotIn("Submission.lean", serialized)
            self.assertNotIn("source_repository", serialized)

    def test_record_bundle_rejects_missing_extra_reordered_and_mixed_records(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = AdapterFixture(pathlib.Path(directory))
            cases: list[tuple[str, dict, str]] = []
            missing = copy.deepcopy(fixture.bundle)
            missing["records"].pop()
            cases.append(("missing", missing, "exactly one record"))
            extra = copy.deepcopy(fixture.bundle)
            extra["records"].append(copy.deepcopy(extra["records"][-1]))
            cases.append(("extra", extra, "exactly one record"))
            reordered = copy.deepcopy(fixture.bundle)
            reordered["records"][0], reordered["records"][1] = (
                reordered["records"][1],
                reordered["records"][0],
            )
            cases.append(("reordered", reordered, "out of plan order"))
            mixed = copy.deepcopy(fixture.bundle)
            mixed["records"][0]["input_sha256"] = mixed["records"][1]["input_sha256"]
            cases.append(("mixed", mixed, "does not bind the plan"))
            duplicate = copy.deepcopy(fixture.bundle)
            duplicate["records"][1] = copy.deepcopy(duplicate["records"][0])
            cases.append(("duplicate", duplicate, "out of plan order"))
            for name, bundle, message in cases:
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(KernelCorpusError, message),
                ):
                    validate_record_bundle(
                        bundle, fixture.plan, fixture.series, fixture.inventory
                    )

    def test_exact_series_inventory_plan_and_bundle_pins_cannot_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = AdapterFixture(pathlib.Path(directory))

            changed_series = copy.deepcopy(fixture.series)
            changed_series["candidate"]["commit"] = "9" * 40
            changed_series["configuration_id"] = configuration_id(changed_series)
            with self.assertRaisesRegex(KernelCorpusError, "deterministic shard"):
                materialize_observation_shard(
                    changed_series,
                    fixture.inventory,
                    fixture.plan,
                    fixture.bundle,
                    fixture.inputs,
                )

            changed_inventory = copy.deepcopy(fixture.inventory)
            changed_inventory["historical_replay_report_sha256"] = "9" * 64
            changed_inventory["inventory_id"] = inventory_id(changed_inventory)
            with self.assertRaisesRegex(KernelCorpusError, "deterministic shard"):
                materialize_observation_shard(
                    fixture.series,
                    changed_inventory,
                    fixture.plan,
                    fixture.bundle,
                    fixture.inputs,
                )

            changed_plan = copy.deepcopy(fixture.plan)
            changed_plan["attempts"][0]["attempt_id"] = "kca1_" + "9" * 64
            with self.assertRaisesRegex(KernelCorpusError, "deterministic shard"):
                materialize_observation_shard(
                    fixture.series,
                    fixture.inventory,
                    changed_plan,
                    fixture.bundle,
                    fixture.inputs,
                )

            for field in (
                "configuration_id",
                "configuration_sha256",
                "inventory_id",
                "inventory_sha256",
                "shard_id",
            ):
                changed_bundle = copy.deepcopy(fixture.bundle)
                changed_bundle[field] = (
                    "kcc1_" + "9" * 64
                    if field == "configuration_id"
                    else "kci1_" + "9" * 64
                    if field == "inventory_id"
                    else "ksh1_" + "9" * 64
                    if field == "shard_id"
                    else "9" * 64
                )
                with (
                    self.subTest(field=field),
                    self.assertRaisesRegex(KernelCorpusRunnerError, field),
                ):
                    validate_record_bundle(
                        changed_bundle,
                        fixture.plan,
                        fixture.series,
                        fixture.inventory,
                    )

    def test_source_free_schema_statistics_and_dispositions_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = AdapterFixture(pathlib.Path(directory))
            accepted = 0
            crashed = OUTCOMES.index("crashed")
            timed_out = OUTCOMES.index("timed_out")
            exported = OUTCOMES.index("export_unavailable")
            cases: list[tuple[str, dict, str]] = []

            false_assertion = copy.deepcopy(fixture.bundle)
            false_assertion["records"][accepted]["source_free"] = False
            cases.append(
                ("false source_free", false_assertion, "source_free must be true")
            )
            open_record = copy.deepcopy(fixture.bundle)
            open_record["records"][accepted]["source_path"] = "/secret/Submission.lean"
            cases.append(("open record", open_record, "fields are not canonical"))
            terminal_evidence = copy.deepcopy(fixture.bundle)
            terminal_evidence["records"][accepted]["evidence_sha256"] = "f" * 64
            cases.append(
                (
                    "terminal evidence",
                    terminal_evidence,
                    "cannot claim availability evidence",
                )
            )
            export_without_evidence = copy.deepcopy(fixture.bundle)
            export_without_evidence["records"][exported]["evidence_sha256"] = None
            cases.append(
                (
                    "export evidence",
                    export_without_evidence,
                    "evidence_sha256",
                )
            )
            unknown_outcome = copy.deepcopy(fixture.bundle)
            unknown_outcome["records"][accepted]["outcome"] = "unknown"
            cases.append(("unknown outcome", unknown_outcome, "not registered"))
            guessed_rejection = copy.deepcopy(fixture.bundle)
            guessed_rejection["records"][accepted]["outcome"] = "rejected"
            cases.append(
                ("guessed rejection", guessed_rejection, "not registered")
            )
            wrong_schema = copy.deepcopy(fixture.bundle)
            wrong_schema["schema_version"] = 2
            cases.append(("wrong schema", wrong_schema, "schema_version"))
            wrong_kind = copy.deepcopy(fixture.bundle)
            wrong_kind["kind"] = "kernel_corpus_observations"
            cases.append(("wrong kind", wrong_kind, "kind is not registered"))
            malformed_transcript = copy.deepcopy(fixture.bundle)
            malformed_transcript["records"][accepted]["transcript_sha256"] = "bad"
            cases.append(
                ("malformed transcript", malformed_transcript, "not canonical")
            )
            records_object = copy.deepcopy(fixture.bundle)
            records_object["records"] = {}
            cases.append(("records object", records_object, "must be an array"))
            accepted_without_checker = copy.deepcopy(fixture.bundle)
            accepted_without_checker["records"][accepted]["statistics"][
                "checker_invocations"
            ] = 0
            cases.append(("zero checker", accepted_without_checker, "at least one"))
            export_with_checker = copy.deepcopy(fixture.bundle)
            export_with_checker["records"][exported]["statistics"][
                "checker_invocations"
            ] = 1
            cases.append(("export checker", export_with_checker, "zero checker"))
            timed_within = copy.deepcopy(fixture.bundle)
            timed_within["records"][timed_out]["resource_limit_disposition"] = (
                "within_limits"
            )
            cases.append(("timed within", timed_within, "must record wall_timeout"))
            false_memory = copy.deepcopy(fixture.bundle)
            false_memory["records"][crashed]["statistics"]["peak_memory_bytes"] = 1_000
            cases.append(("false memory", false_memory, "inconsistent memory limit"))
            over_timeout = copy.deepcopy(fixture.bundle)
            over_timeout["records"][accepted]["statistics"]["wall_time_ms"] = (
                fixture.series["runner"]["resource_limits"]["wall_timeout_seconds"]
                * 1_000
                + 1
            )
            cases.append(("over timeout", over_timeout, "series wall timeout"))
            over_memory = copy.deepcopy(fixture.bundle)
            over_memory["records"][accepted]["statistics"]["peak_memory_bytes"] = (
                fixture.series["runner"]["resource_limits"]["max_memory_bytes"] + 1
            )
            cases.append(("over memory", over_memory, "series memory limit"))
            for name, bundle, message in cases:
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(KernelCorpusError, message),
                ):
                    validate_record_bundle(
                        bundle, fixture.plan, fixture.series, fixture.inventory
                    )

    def test_input_membership_digest_and_file_types_fail_closed(self) -> None:
        def assert_case(mutate, message: str) -> None:
            with tempfile.TemporaryDirectory() as directory:
                fixture = AdapterFixture(pathlib.Path(directory))
                mutate(fixture)
                with self.assertRaisesRegex(KernelCorpusRunnerError, message):
                    fixture.materialize()

        def remove_input(fixture: AdapterFixture) -> None:
            next(fixture.inputs.iterdir()).unlink()

        def add_input(fixture: AdapterFixture) -> None:
            (fixture.inputs / "unexpected.input").write_bytes(b"extra")

        def change_input(fixture: AdapterFixture) -> None:
            next(fixture.inputs.iterdir()).write_bytes(b"changed")

        def symlink_input(fixture: AdapterFixture) -> None:
            path = next(fixture.inputs.iterdir())
            payload = path.read_bytes()
            path.unlink()
            target = fixture.root / "outside.input"
            target.write_bytes(payload)
            path.symlink_to(target)

        def fifo_input(fixture: AdapterFixture) -> None:
            path = next(fixture.inputs.iterdir())
            path.unlink()
            os.mkfifo(path)

        def symlink_directory(fixture: AdapterFixture) -> None:
            real_inputs = fixture.root / "real-inputs"
            fixture.inputs.rename(real_inputs)
            fixture.inputs.symlink_to(real_inputs, target_is_directory=True)

        for name, mutate, message in (
            ("missing", remove_input, "membership"),
            ("extra", add_input, "membership"),
            ("digest", change_input, "raw SHA-256"),
            ("symlink", symlink_input, "regular non-symlink"),
            ("fifo", fifo_input, "regular non-symlink"),
            ("directory symlink", symlink_directory, "unsafe or unavailable"),
        ):
            with self.subTest(name=name):
                assert_case(mutate, message)

    def test_input_file_count_per_file_and_total_byte_bounds(self) -> None:
        for variable, value, message in (
            ("MAX_INPUT_FILES", 1, "file-count"),
            ("MAX_INPUT_BYTES", 1, "per-file byte"),
            ("MAX_SHARD_INPUT_BYTES", 1, "total byte"),
        ):
            with (
                self.subTest(variable=variable),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = AdapterFixture(pathlib.Path(directory))
                with (
                    mock.patch(f"kernel_corpus_runner_adapter.{variable}", value),
                    self.assertRaisesRegex(KernelCorpusRunnerError, message),
                ):
                    fixture.materialize()

    def test_device_mode_is_refused_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = AdapterFixture(pathlib.Path(directory))
            path = next(fixture.inputs.iterdir())
            descriptor = _open_input_directory(fixture.inputs)
            real_fstat = os.fstat

            def device_fstat(file_descriptor: int):
                metadata = real_fstat(file_descriptor)
                if file_descriptor == descriptor:
                    return metadata
                return mock.Mock(st_mode=stat.S_IFCHR | 0o600, st_size=0)

            try:
                with (
                    mock.patch(
                        "kernel_corpus_runner_adapter.os.fstat",
                        side_effect=device_fstat,
                    ),
                    self.assertRaisesRegex(KernelCorpusRunnerError, "regular file"),
                ):
                    _digest_input(fixture.inputs, descriptor, path.name)
            finally:
                os.close(descriptor)

    def test_empty_run_shard_requires_no_records_but_still_requires_a_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = AdapterFixture(pathlib.Path(directory))
            plan = next(
                plan
                for plan in build_shard_plans(fixture.series, fixture.inventory, 7)
                if not plan["attempts"]
            )
            empty_inputs = fixture.root / "empty-inputs"
            empty_inputs.mkdir()
            bundle = {
                **{
                    key: value
                    for key, value in fixture.bundle.items()
                    if key not in {"shard_id", "records"}
                },
                "shard_id": plan["shard_id"],
                "records": [],
            }
            shard = materialize_observation_shard(
                fixture.series,
                fixture.inventory,
                plan,
                bundle,
                empty_inputs,
            )
            self.assertEqual(shard["observations"], [])

            empty_inputs.rmdir()
            with self.assertRaisesRegex(KernelCorpusRunnerError, "unavailable"):
                materialize_observation_shard(
                    fixture.series,
                    fixture.inventory,
                    plan,
                    bundle,
                    empty_inputs,
                )

    def test_cli_publishes_exclusively_and_refuses_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = AdapterFixture(pathlib.Path(directory))
            root = fixture.root
            paths = {
                "series": root / "series.json",
                "inventory": root / "inventory.json",
                "plan": root / "plan.json",
                "records": root / "records.json",
            }
            for name, path in paths.items():
                value = getattr(fixture, name if name != "records" else "bundle")
                path.write_text(json.dumps(value), encoding="utf-8")
            output = root / "observations.json"
            arguments = [
                "--series",
                str(paths["series"]),
                "--inventory",
                str(paths["inventory"]),
                "--plan",
                str(paths["plan"]),
                "--inputs-dir",
                str(fixture.inputs),
                "--records",
                str(paths["records"]),
                "--output",
                str(output),
            ]
            self.assertEqual(main(arguments), 0)
            original = output.read_bytes()
            self.assertEqual(main(arguments), 1)
            self.assertEqual(output.read_bytes(), original)

            output.unlink()
            target = root / "symlink-target.json"
            target.write_text("preserve me", encoding="utf-8")
            output.symlink_to(target)
            self.assertEqual(main(arguments), 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve me")
            output.unlink()

            paths["records"].write_text('{"same":1,"same":2}', encoding="utf-8")
            self.assertEqual(main(arguments), 1)
            self.assertFalse(output.exists())

    def test_adapter_has_no_executor_network_or_credential_interface(self) -> None:
        source = (ROOT / "scripts" / "kernel_corpus_runner_adapter.py").read_text()
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
            "import importlib",
            "import http",
            "import ssl",
            "getpass",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
