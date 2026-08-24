from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from kernel_corpus_report import (  # noqa: E402
    KernelCorpusError,
    aggregate_report,
    attempt_id,
    build_shard_plans,
    canonical_bytes,
    configuration_id,
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
            "replay_attempt_id": "rt1_" + format(index + 1, "064x"),
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
            items.append(
                {
                    "result_id": attempt["result_id"],
                    "replay_attempt_id": attempt["replay_attempt_id"],
                    "attempt_id": attempt["attempt_id"],
                    "status": status,
                    "outcome": outcome,
                    "evidence_sha256": evidence.get(outcome),
                    "statistics": (
                        {
                            "wall_time_ms": 100 + index,
                            "peak_memory_bytes": 1_000 + index,
                            "checker_invocations": 1,
                        }
                        if status == "completed"
                        else None
                    ),
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

    def test_canonical_serialization_rejects_nan(self) -> None:
        with self.assertRaises(ValueError):
            canonical_bytes({"value": float("nan")})


if __name__ == "__main__":
    unittest.main()
