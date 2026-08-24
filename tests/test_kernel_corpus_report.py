from __future__ import annotations

import copy
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import kernel_corpus_report as corpus
from kernel_corpus_report import (
    MAX_CHECKER_INVOCATIONS,
    MAX_JSON_BYTES,
    KernelCorpusError,
    _directory_entries,
    _load,
    _load_shard_directory,
    _performance,
    _safe_sum,
    _write_json,
    aggregate_report,
    attempt_id,
    build_shard_plans,
    canonical_bytes,
    configuration_id,
    execution_receipt_sha256,
    inventory_id,
    main,
    validate_inventory,
    validate_observation_shard,
    validate_plan,
    validate_report,
    validate_series,
)


def series() -> dict:
    value = {
        "schema_version": 1,
        "series_name": "mathgraph-corpus-fixture",
        "configuration_id": "",
        "candidate": {
            "name": "mathgraph",
            "repository": "metalogiclabs/mathgraph-lean-kernel",
            "commit": "1" * 40,
            "binary_sha256": "2" * 64,
            "protocol": "nanoda_config_file",
        },
        "exporter": {
            "repository": "leanprover/lean4export",
            "commit": "3" * 40,
            "artifact_sha256": "4" * 64,
        },
        "checker": {
            "repository": "leanprover/comparator",
            "commit": "5" * 40,
            "protocol": "external_kernels_v1",
            "configuration_sha256": "6" * 64,
        },
        "runner": {
            "repository": "leanprover/lean-eval-submissions",
            "commit": "7" * 40,
            "image_digest": "sha256:" + "8" * 64,
            "architecture": "x86_64",
            "operating_system": "ubuntu-24.04",
            "resource_limits": {
                "wall_timeout_seconds": 600,
                "max_memory_bytes": 8_589_934_592,
            },
        },
    }
    value["configuration_id"] = configuration_id(value)
    return value


def inventory() -> dict:
    definitions = (
        ("0", "ready", "accepted", None),
        ("1", "ready", "rejected", None),
        ("2", "ready", "declined", None),
        ("3", "ready", "crashed", None),
        ("4", "ready", "timed_out", None),
        ("5", "ready", "accepted", None),
        ("6", "ready", "accepted", None),
        ("7", "source_unavailable", None, "a" * 64),
        ("8", "replay_unavailable", None, "b" * 64),
        ("9", "replay_pending", None, None),
    )
    results = [
        {
            "result_id": "r2_" + suffix * 64,
            "replay_task_id": "rt1_" + format(index + 1, "064x"),
            "replay_attempt": index + 1,
            "terminal_verdict_sha256": format(index + 20, "064x")
            if availability == "ready"
            else None,
            "terminal_event_sha256": format(index + 40, "064x")
            if availability == "ready"
            else None,
            "report_entry_sha256": format(index + 60, "064x")
            if availability == "ready"
            else None,
            "replay_export_input_sha256": format(index + 80, "064x")
            if availability == "ready"
            else None,
            "authoritative_outcome": outcome,
            "availability": availability,
            "unavailability_evidence_sha256": evidence,
        }
        for index, (suffix, availability, outcome, evidence) in enumerate(definitions)
    ]
    value = {
        "schema_version": 1,
        "inventory_id": "",
        "cutoff_at": "2026-08-24T00:00:00.000Z",
        "results_store": {
            "repository": "leanprover/lean-eval-submissions",
            "commit": "c" * 40,
            "tree_sha256": "d" * 64,
        },
        "historical_replay_report_sha256": "e" * 64,
        "results": results,
    }
    value["inventory_id"] = inventory_id(value)
    return value


def observations(plans: list[dict]) -> list[dict]:
    outcomes = {
        "r2_" + "0" * 64: "accepted",
        "r2_" + "1" * 64: "rejected",
        "r2_" + "2" * 64: "declined",
        "r2_" + "3" * 64: "crashed",
        "r2_" + "4" * 64: "timed_out",
        "r2_" + "5" * 64: "export_unavailable",
        "r2_" + "6" * 64: "export_format_unsupported",
        "r2_" + "7" * 64: "source_unavailable",
        "r2_" + "8" * 64: "replay_unavailable",
        "r2_" + "9" * 64: "replay_pending",
    }
    evidence = {
        "export_unavailable": "f" * 64,
        "export_format_unsupported": "1" * 64,
        "source_unavailable": "a" * 64,
        "replay_unavailable": "b" * 64,
    }
    output = []
    sources = {item["result_id"]: item for item in inventory()["results"]}
    for plan in plans:
        items = []
        for index, attempt in enumerate(plan["attempts"]):
            outcome = outcomes[attempt["result_id"]]
            if outcome in {
                "source_unavailable",
                "replay_unavailable",
                "export_unavailable",
            }:
                status = "unavailable"
            elif outcome in {"replay_pending", "export_format_unsupported"}:
                status = "pending"
            else:
                status = "completed"
            executed = attempt["required_action"] == "run"
            statistics = None
            receipt = None
            if executed:
                statistics = {
                    "wall_time_ms": 600_000 if outcome == "timed_out" else 100 + index,
                    "peak_memory_bytes": 1_000 + index,
                    "checker_invocations": (
                        0
                        if outcome
                        in {"export_unavailable", "export_format_unsupported"}
                        else 1
                    ),
                }
                receipt = {
                    "schema_version": 1,
                    "receipt_sha256": "",
                    "attempt_id": attempt["attempt_id"],
                    "input_sha256": sources[attempt["result_id"]][
                        "replay_export_input_sha256"
                    ],
                    "configuration_id": plan["configuration_id"],
                    "configuration_sha256": plan["configuration_sha256"],
                    "outcome": outcome,
                    "resource_limit_disposition": (
                        "wall_timeout" if outcome == "timed_out" else "within_limits"
                    ),
                    "statistics": statistics,
                    "transcript_sha256": "2" * 64,
                    "runner_attestation_sha256": "3" * 64,
                    "source_free": True,
                }
                receipt["receipt_sha256"] = execution_receipt_sha256(receipt)
            items.append(
                {
                    "result_id": attempt["result_id"],
                    "replay_task_id": attempt["replay_task_id"],
                    "replay_attempt": attempt["replay_attempt"],
                    "attempt_id": attempt["attempt_id"],
                    "status": status,
                    "outcome": outcome,
                    "evidence_sha256": evidence.get(outcome),
                    "statistics": statistics,
                    "execution_receipt": receipt,
                }
            )
        output.append(
            {
                **{key: value for key, value in plan.items() if key != "attempts"},
                "kind": "kernel_corpus_observations",
                "observations": items,
            }
        )
    return output


class KernelCorpusReportTests(unittest.TestCase):
    def test_exact_series_and_inventory_identities_are_recomputed(self) -> None:
        selected_series = validate_series(series())
        selected_inventory = validate_inventory(inventory())
        self.assertRegex(selected_series["configuration_id"], r"^kcc1_[0-9a-f]{64}$")
        self.assertRegex(selected_inventory["inventory_id"], r"^kci1_[0-9a-f]{64}$")

        for component in ("candidate", "exporter", "checker", "runner"):
            changed = copy.deepcopy(selected_series)
            changed[component]["commit"] = "9" * 40
            with self.assertRaisesRegex(KernelCorpusError, "does not bind"):
                validate_series(changed)

        changed_inventory = copy.deepcopy(selected_inventory)
        changed_inventory["historical_replay_report_sha256"] = "0" * 64
        with self.assertRaisesRegex(KernelCorpusError, "does not bind"):
            validate_inventory(changed_inventory)

    def test_series_repositories_reject_dot_path_segments(self) -> None:
        for repository in ("./checker", "owner/.."):
            with self.subTest(repository=repository):
                changed = series()
                changed["checker"]["repository"] = repository
                changed["configuration_id"] = configuration_id(changed)
                with self.assertRaisesRegex(KernelCorpusError, "path segment"):
                    validate_series(changed)

    def test_inventory_keeps_pending_and_unavailability_nonterminal(self) -> None:
        selected = inventory()
        pending = selected["results"][-1]
        self.assertEqual(pending["availability"], "replay_pending")
        self.assertIsNone(pending["authoritative_outcome"])
        self.assertIsNone(pending["unavailability_evidence_sha256"])

        pending["authoritative_outcome"] = "rejected"
        selected["inventory_id"] = inventory_id(selected)
        with self.assertRaisesRegex(KernelCorpusError, "pending replay"):
            validate_inventory(selected)

    def test_inventory_rejects_impossible_cutoff_timestamp(self) -> None:
        selected = inventory()
        selected["cutoff_at"] = "2026-13-24T00:00:00.000Z"
        selected["inventory_id"] = inventory_id(selected)
        with self.assertRaisesRegex(KernelCorpusError, "real UTC timestamp"):
            validate_inventory(selected)

    def test_shards_are_deterministic_and_cover_each_result_once(self) -> None:
        first = build_shard_plans(series(), inventory(), 3)
        second = build_shard_plans(series(), inventory(), 3)
        self.assertEqual(first, second)
        attempts = [attempt for plan in first for attempt in plan["attempts"]]
        self.assertEqual(len(attempts), len(inventory()["results"]))
        self.assertEqual(
            sorted(attempt["result_id"] for attempt in attempts),
            [result["result_id"] for result in inventory()["results"]],
        )
        self.assertEqual(
            len({attempt["attempt_id"] for attempt in attempts}), len(attempts)
        )
        by_result = {item["result_id"]: item for item in inventory()["results"]}
        for attempt in attempts:
            self.assertEqual(
                attempt["attempt_id"],
                attempt_id(series(), inventory(), by_result[attempt["result_id"]]),
            )

    def test_attempt_identity_binds_replay_task_attempt_terminal_tuple_and_input(
        self,
    ) -> None:
        original_inventory = inventory()
        original_plan = build_shard_plans(series(), original_inventory, 1)[0]
        original_attempt = original_plan["attempts"][0]
        for field in (
            "replay_task_id",
            "replay_attempt",
            "terminal_verdict_sha256",
            "terminal_event_sha256",
            "report_entry_sha256",
            "replay_export_input_sha256",
        ):
            self.assertEqual(
                original_attempt[field], original_inventory["results"][0][field]
            )

        changed_inventory = copy.deepcopy(original_inventory)
        changed_inventory["results"][0]["terminal_verdict_sha256"] = "f" * 64
        changed_inventory["inventory_id"] = inventory_id(changed_inventory)
        changed_attempt = build_shard_plans(series(), changed_inventory, 1)[0][
            "attempts"
        ][0]
        self.assertNotEqual(
            original_attempt["attempt_id"], changed_attempt["attempt_id"]
        )

    def test_empty_deterministic_shards_remain_producible_and_aggregatable(
        self,
    ) -> None:
        plans = build_shard_plans(series(), inventory(), 7)
        self.assertTrue(any(not plan["attempts"] for plan in plans))
        shards = observations(plans)
        report = aggregate_report(series(), inventory(), plans, shards)
        self.assertEqual(report["coverage"]["observations"], 10)
        self.assertEqual(sum(report["counters"].values()), 10)

    def test_plan_rejects_mixed_series_inventory_and_attempts(self) -> None:
        plan = build_shard_plans(series(), inventory(), 1)[0]
        changed = copy.deepcopy(plan)
        changed["configuration_id"] = "kcc1_" + "0" * 64
        with self.assertRaisesRegex(KernelCorpusError, "deterministic shard"):
            validate_plan(changed, series(), inventory())

        changed = copy.deepcopy(plan)
        changed["attempts"][0]["attempt_id"] = "kca1_" + "0" * 64
        with self.assertRaisesRegex(KernelCorpusError, "deterministic shard"):
            validate_plan(changed, series(), inventory())

    def test_observations_preserve_all_closed_outcome_classes(self) -> None:
        plans = build_shard_plans(series(), inventory(), 3)
        shards = observations(plans)
        for shard, plan in zip(shards, plans, strict=True):
            validate_observation_shard(shard, plan, series(), inventory())
        outcomes = {
            item["outcome"] for shard in shards for item in shard["observations"]
        }
        self.assertEqual(
            outcomes,
            {
                "accepted",
                "rejected",
                "declined",
                "crashed",
                "timed_out",
                "source_unavailable",
                "replay_unavailable",
                "export_unavailable",
                "replay_pending",
                "export_format_unsupported",
            },
        )
        serialized = json.dumps(shards, sort_keys=True)
        self.assertNotIn("Submission.lean", serialized)
        self.assertNotIn("source_repository", serialized)

    def test_observations_cannot_turn_pending_or_unavailable_into_verdicts(
        self,
    ) -> None:
        plans = build_shard_plans(series(), inventory(), 1)
        shard = observations(plans)[0]
        by_outcome = {item["outcome"]: item for item in shard["observations"]}
        for outcome in ("replay_pending", "source_unavailable", "replay_unavailable"):
            with self.subTest(outcome=outcome):
                changed = copy.deepcopy(shard)
                target = next(
                    item
                    for item in changed["observations"]
                    if item["outcome"] == outcome
                )
                target.update(
                    {
                        "status": "completed",
                        "outcome": "rejected",
                        "evidence_sha256": None,
                        "statistics": {
                            "wall_time_ms": 1,
                            "peak_memory_bytes": 1,
                            "checker_invocations": 1,
                        },
                    }
                )
                with self.assertRaisesRegex(KernelCorpusError, "unavailable input"):
                    validate_observation_shard(changed, plans[0], series(), inventory())
        self.assertEqual(by_outcome["replay_pending"]["status"], "pending")

    def test_observation_omission_duplication_and_export_relabel_fail(self) -> None:
        plans = build_shard_plans(series(), inventory(), 1)
        shard = observations(plans)[0]
        omitted = copy.deepcopy(shard)
        omitted["observations"].pop()
        with self.assertRaisesRegex(KernelCorpusError, "cover every"):
            validate_observation_shard(omitted, plans[0], series(), inventory())

        duplicate = copy.deepcopy(shard)
        duplicate["observations"][1] = copy.deepcopy(duplicate["observations"][0])
        with self.assertRaisesRegex(KernelCorpusError, "planned attempt"):
            validate_observation_shard(duplicate, plans[0], series(), inventory())

        relabeled = copy.deepcopy(shard)
        target = next(
            item
            for item in relabeled["observations"]
            if item["outcome"] == "export_unavailable"
        )
        target["outcome"] = "source_unavailable"
        with self.assertRaisesRegex(KernelCorpusError, "availability class"):
            validate_observation_shard(relabeled, plans[0], series(), inventory())

    def test_execution_receipt_binds_input_series_outcome_and_transcript(self) -> None:
        plans = build_shard_plans(series(), inventory(), 1)
        shard = observations(plans)[0]
        target = next(
            item for item in shard["observations"] if item["outcome"] == "accepted"
        )
        for field, value, message in (
            ("input_sha256", "0" * 64, "input_sha256"),
            ("configuration_sha256", "0" * 64, "configuration_sha256"),
            ("outcome", "rejected", "outcome"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(shard)
                receipt = next(
                    item
                    for item in changed["observations"]
                    if item["attempt_id"] == target["attempt_id"]
                )["execution_receipt"]
                receipt[field] = value
                receipt["receipt_sha256"] = execution_receipt_sha256(receipt)
                with self.assertRaisesRegex(KernelCorpusError, message):
                    validate_observation_shard(changed, plans[0], series(), inventory())

        changed = copy.deepcopy(shard)
        receipt = next(
            item
            for item in changed["observations"]
            if item["attempt_id"] == target["attempt_id"]
        )["execution_receipt"]
        receipt["transcript_sha256"] = "0" * 64
        with self.assertRaisesRegex(KernelCorpusError, "does not bind the receipt"):
            validate_observation_shard(changed, plans[0], series(), inventory())

        changed = copy.deepcopy(shard)
        receipt = next(
            item
            for item in changed["observations"]
            if item["attempt_id"] == target["attempt_id"]
        )["execution_receipt"]
        receipt["source_free"] = False
        receipt["receipt_sha256"] = execution_receipt_sha256(receipt)
        with self.assertRaisesRegex(KernelCorpusError, "source_free must be true"):
            validate_observation_shard(changed, plans[0], series(), inventory())

    def test_execution_measurements_are_series_bounded_and_outcome_aware(self) -> None:
        plans = build_shard_plans(series(), inventory(), 1)
        shard = observations(plans)[0]
        accepted = next(
            item for item in shard["observations"] if item["outcome"] == "accepted"
        )
        for field, value, message in (
            ("wall_time_ms", 600_001, "wall timeout"),
            ("peak_memory_bytes", 8_589_934_593, "memory limit"),
            (
                "checker_invocations",
                MAX_CHECKER_INVOCATIONS + 1,
                "contract limit",
            ),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(shard)
                target = next(
                    item
                    for item in changed["observations"]
                    if item["attempt_id"] == accepted["attempt_id"]
                )
                target["statistics"][field] = value
                target["execution_receipt"]["statistics"][field] = value
                target["execution_receipt"]["receipt_sha256"] = (
                    execution_receipt_sha256(target["execution_receipt"])
                )
                with self.assertRaisesRegex(KernelCorpusError, message):
                    validate_observation_shard(changed, plans[0], series(), inventory())

        timed_out = next(
            item for item in shard["observations"] if item["outcome"] == "timed_out"
        )
        timed_out["execution_receipt"]["resource_limit_disposition"] = "within_limits"
        timed_out["execution_receipt"]["receipt_sha256"] = execution_receipt_sha256(
            timed_out["execution_receipt"]
        )
        with self.assertRaisesRegex(KernelCorpusError, "must record wall_timeout"):
            validate_observation_shard(shard, plans[0], series(), inventory())

    def test_checker_invocations_distinguish_export_from_checker_outcomes(self) -> None:
        plans = build_shard_plans(series(), inventory(), 1)
        shard = observations(plans)[0]
        by_outcome = {item["outcome"]: item for item in shard["observations"]}
        for outcome in ("export_unavailable", "export_format_unsupported"):
            self.assertEqual(
                by_outcome[outcome]["statistics"]["checker_invocations"], 0
            )
            changed = copy.deepcopy(shard)
            target = next(
                item for item in changed["observations"] if item["outcome"] == outcome
            )
            target["statistics"]["checker_invocations"] = 1
            target["execution_receipt"]["statistics"]["checker_invocations"] = 1
            target["execution_receipt"]["receipt_sha256"] = execution_receipt_sha256(
                target["execution_receipt"]
            )
            with self.assertRaisesRegex(KernelCorpusError, "zero checker"):
                validate_observation_shard(changed, plans[0], series(), inventory())

        changed = copy.deepcopy(shard)
        accepted = next(
            item for item in changed["observations"] if item["outcome"] == "accepted"
        )
        accepted["statistics"]["checker_invocations"] = 0
        accepted["execution_receipt"]["statistics"]["checker_invocations"] = 0
        accepted["execution_receipt"]["receipt_sha256"] = execution_receipt_sha256(
            accepted["execution_receipt"]
        )
        with self.assertRaisesRegex(KernelCorpusError, "at least one"):
            validate_observation_shard(changed, plans[0], series(), inventory())

    def test_safe_sum_rejects_ieee754_overflow(self) -> None:
        with self.assertRaisesRegex(KernelCorpusError, "IEEE-754 safe"):
            _safe_sum([9_007_199_254_740_991, 1], "hostile sum")

    def test_upper_median_uses_the_upper_rank_for_even_samples(self) -> None:
        measured = _performance(
            [
                {
                    "status": "completed",
                    "statistics": {
                        "wall_time_ms": wall_time,
                        "peak_memory_bytes": 1,
                        "checker_invocations": 1,
                    },
                }
                for wall_time in (10, 20, 30, 40)
            ]
        )
        self.assertEqual(measured["wall_time_ms"]["median_upper"], 30)

    def test_aggregate_builds_expected_shard_set_once(self) -> None:
        plans = build_shard_plans(series(), inventory(), 7)
        shards = observations(plans)
        with mock.patch(
            "kernel_corpus_report.build_shard_plans", wraps=build_shard_plans
        ) as build:
            aggregate_report(series(), inventory(), plans, shards)
        build.assert_called_once()

    def test_plan_set_digests_series_and_inventory_once(self) -> None:
        selected_series = series()
        selected_inventory = inventory()
        with mock.patch("kernel_corpus_report._digest", wraps=corpus._digest) as digest:
            build_shard_plans(selected_series, selected_inventory, 7)
        self.assertEqual(
            sum(call.args[0] is selected_series for call in digest.call_args_list),
            1,
        )
        self.assertEqual(
            sum(call.args[0] is selected_inventory for call in digest.call_args_list),
            1,
        )

    def test_report_is_complete_blocking_and_performance_deterministic(self) -> None:
        plans = build_shard_plans(series(), inventory(), 3)
        shards = observations(plans)
        report = aggregate_report(series(), inventory(), plans, shards)
        self.assertEqual(
            report["coverage"],
            {
                "inventory_results": 10,
                "observations": 10,
                "complete": True,
            },
        )
        for outcome in (
            "accepted",
            "rejected",
            "declined",
            "crashed",
            "timed_out",
            "source_unavailable",
            "replay_unavailable",
            "export_unavailable",
            "replay_pending",
            "export_format_unsupported",
        ):
            self.assertEqual(report["counters"][outcome], 1)
        self.assertEqual(sum(report["counters"].values()), 10)
        self.assertEqual(report["performance"]["sample_count"], 5)
        self.assertIs(report["promotion"]["automated_eligibility"], False)
        self.assertIn(
            "human_promotion_review_required", report["promotion"]["blocking_reasons"]
        )
        self.assertIn("corpus_pending_results", report["promotion"]["blocking_reasons"])
        self.assertIn(
            "export_format_review_required", report["promotion"]["blocking_reasons"]
        )
        validate_report(report, series(), inventory(), plans, shards)

    def test_disagreements_always_require_adjudication(self) -> None:
        plans = build_shard_plans(series(), inventory(), 1)
        shards = observations(plans)
        target = next(
            item for item in shards[0]["observations"] if item["outcome"] == "rejected"
        )
        target["outcome"] = "accepted"
        target["execution_receipt"]["outcome"] = "accepted"
        target["execution_receipt"]["receipt_sha256"] = execution_receipt_sha256(
            target["execution_receipt"]
        )
        report = aggregate_report(series(), inventory(), plans, shards)
        self.assertEqual(len(report["disagreements"]), 1)
        self.assertEqual(report["disagreements"][0]["adjudication"], "required")
        self.assertIn(
            "disagreement_adjudication_required",
            report["promotion"]["blocking_reasons"],
        )

    def test_missing_mixed_and_forged_aggregates_fail_closed(self) -> None:
        plans = build_shard_plans(series(), inventory(), 3)
        shards = observations(plans)
        with self.assertRaisesRegex(KernelCorpusError, "every shard"):
            aggregate_report(series(), inventory(), plans[:-1], shards[:-1])

        mixed = copy.deepcopy(shards)
        mixed[1]["configuration_id"] = "kcc1_" + "0" * 64
        with self.assertRaisesRegex(KernelCorpusError, "does not match"):
            aggregate_report(series(), inventory(), plans, mixed)

        report = aggregate_report(series(), inventory(), plans, shards)
        forged = copy.deepcopy(report)
        forged["promotion"]["automated_eligibility"] = True
        with self.assertRaisesRegex(KernelCorpusError, "deterministic"):
            validate_report(forged, series(), inventory(), plans, shards)

    def test_cli_preparation_is_exclusive_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            series_path = root / "series.json"
            inventory_path = root / "inventory.json"
            series_path.write_text(json.dumps(series()), encoding="utf-8")
            inventory_path.write_text(json.dumps(inventory()), encoding="utf-8")
            output = root / "plans"
            self.assertEqual(
                main(
                    [
                        "prepare-shards",
                        "--series",
                        str(series_path),
                        "--inventory",
                        str(inventory_path),
                        "--shard-count",
                        "3",
                        "--output-dir",
                        str(output),
                    ]
                ),
                0,
            )
            self.assertEqual(len(list(output.glob("shard-*.json"))), 3)
            self.assertEqual(
                [
                    json.loads(path.read_text())
                    for path in sorted(output.glob("*.json"))
                ],
                build_shard_plans(series(), inventory(), 3),
            )
            self.assertEqual(
                main(
                    [
                        "prepare-shards",
                        "--series",
                        str(series_path),
                        "--inventory",
                        str(inventory_path),
                        "--shard-count",
                        "3",
                        "--output-dir",
                        str(output),
                    ]
                ),
                1,
            )

    def test_json_reads_reject_symlink_fifo_oversize_duplicate_and_depth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            regular = root / "regular.json"
            regular.write_text("{}", encoding="utf-8")
            symlink = root / "symlink.json"
            symlink.symlink_to(regular)
            with self.assertRaisesRegex(KernelCorpusError, "cannot read JSON"):
                _load(symlink)

            fifo = root / "fifo.json"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(KernelCorpusError, "regular file"):
                _load(fifo)

            oversized = root / "oversized.json"
            with oversized.open("wb") as stream:
                stream.truncate(MAX_JSON_BYTES + 1)
            with self.assertRaisesRegex(KernelCorpusError, "byte limit"):
                _load(oversized)

            duplicate = root / "duplicate.json"
            duplicate.write_text('{"same": 1, "same": 2}', encoding="utf-8")
            with self.assertRaisesRegex(KernelCorpusError, "duplicate JSON"):
                _load(duplicate)

            nested = root / "nested.json"
            nested.write_text("[" * 65 + "0" + "]" * 65, encoding="utf-8")
            with self.assertRaisesRegex(KernelCorpusError, "nesting-depth"):
                _load(nested)

            nodes = root / "nodes.json"
            nodes.write_text("[1, 2]", encoding="utf-8")
            with (
                mock.patch("kernel_corpus_report.MAX_JSON_NODES", 2),
                self.assertRaisesRegex(KernelCorpusError, "node-count"),
            ):
                _load(nodes)

            with (
                mock.patch(
                    "kernel_corpus_report.json.loads",
                    side_effect=RecursionError("hostile parser recursion"),
                ),
                self.assertRaisesRegex(KernelCorpusError, "cannot parse JSON"),
            ):
                _load(regular)

            shards = root / "shards"
            shards.mkdir()
            (shards / "shard-0000.json").write_text("{}", encoding="utf-8")
            (shards / "shard-0001.json").write_text("{}", encoding="utf-8")
            with (
                mock.patch("kernel_corpus_report.MAX_SHARDS", 1),
                self.assertRaisesRegex(KernelCorpusError, "file-count"),
            ):
                _directory_entries(shards)

            with (
                mock.patch("kernel_corpus_report.MAX_SHARD_DIRECTORY_BYTES", 3),
                self.assertRaisesRegex(KernelCorpusError, "total byte limit"),
            ):
                _load_shard_directory(shards)
            with (
                mock.patch("kernel_corpus_report.MAX_SHARD_DIRECTORY_NODES", 1),
                self.assertRaisesRegex(KernelCorpusError, "total node-count"),
            ):
                _load_shard_directory(shards)

    def test_outputs_are_no_follow_exclusive_and_aggregate_membership_is_exact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = root / "output.json"
            _write_json(output, {"first": True})
            with self.assertRaisesRegex(KernelCorpusError, "existing output"):
                _write_json(output, {"second": True})

            target = root / "target.json"
            target.write_text("unchanged", encoding="utf-8")
            linked = root / "linked.json"
            linked.symlink_to(target)
            with self.assertRaisesRegex(KernelCorpusError, "existing output"):
                _write_json(linked, {"changed": True})
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")

            published = root / "published.json"
            with (
                mock.patch(
                    "kernel_corpus_report.os.fsync",
                    side_effect=(None, OSError("directory sync failed")),
                ),
                self.assertRaisesRegex(
                    KernelCorpusError, "published but directory fsync failed"
                ),
            ):
                _write_json(published, {"durable": "unknown"})
            self.assertEqual(
                json.loads(published.read_text(encoding="utf-8")),
                {"durable": "unknown"},
            )

            selected_series = series()
            selected_inventory = inventory()
            plans = build_shard_plans(selected_series, selected_inventory, 1)
            shards = observations(plans)
            plans_dir = root / "plans"
            observations_dir = root / "observations"
            plans_dir.mkdir()
            observations_dir.mkdir()
            _write_json(plans_dir / "shard-0000.json", plans[0])
            _write_json(observations_dir / "shard-0000.json", shards[0])
            (observations_dir / "rogue.json").write_text("{}", encoding="utf-8")
            series_path = root / "series.json"
            inventory_path = root / "inventory.json"
            series_path.write_text(json.dumps(selected_series), encoding="utf-8")
            inventory_path.write_text(json.dumps(selected_inventory), encoding="utf-8")
            self.assertEqual(
                main(
                    [
                        "aggregate",
                        "--series",
                        str(series_path),
                        "--inventory",
                        str(inventory_path),
                        "--plans-dir",
                        str(plans_dir),
                        "--observations-dir",
                        str(observations_dir),
                        "--output",
                        str(root / "report.json"),
                    ]
                ),
                1,
            )

    def test_canonical_serialization_rejects_nan(self) -> None:
        with self.assertRaises(ValueError):
            canonical_bytes({"value": float("nan")})


if __name__ == "__main__":
    unittest.main()
