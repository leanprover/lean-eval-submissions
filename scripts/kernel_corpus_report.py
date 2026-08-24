#!/usr/bin/env python3
"""Prepare and aggregate source-free independent-kernel corpus evidence.

This contract is downstream of the historical replay inventory.  It never
fetches source, runs a checker, writes State, or approves checker promotion.
It binds offline observations to one exact inventory and checker-series
configuration and turns complete deterministic shards into a blocking report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
from typing import Any


DIGEST = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}")
REPLAY_ATTEMPT_ID = re.compile(r"rt1_[0-9a-f]{64}")
CONFIGURATION_ID = re.compile(r"kcc1_[0-9a-f]{64}")
INVENTORY_ID = re.compile(r"kci1_[0-9a-f]{64}")
ATTEMPT_ID = re.compile(r"kca1_[0-9a-f]{64}")
SHARD_ID = re.compile(r"ksh1_[0-9a-f]{64}")
SERIES_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}")
SAFE_NAME = re.compile(r"[A-Za-z0-9_.+-]{1,128}")
TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z"
)

TERMINAL_OUTCOMES = ("accepted", "rejected", "declined", "crashed", "timed_out")
AVAILABILITIES = (
    "ready",
    "replay_pending",
    "source_unavailable",
    "replay_unavailable",
)
UNAVAILABLE_OUTCOMES = (
    "source_unavailable",
    "replay_unavailable",
    "export_unavailable",
)
PENDING_OUTCOMES = ("replay_pending", "export_format_unsupported")

SERIES_FIELDS = {
    "schema_version",
    "series_name",
    "configuration_id",
    "candidate",
    "exporter",
    "checker",
    "runner",
}
INVENTORY_FIELDS = {
    "schema_version",
    "inventory_id",
    "cutoff_at",
    "results_store",
    "historical_replay_report_sha256",
    "results",
}
INVENTORY_RESULT_FIELDS = {
    "result_id",
    "replay_attempt_id",
    "authoritative_outcome",
    "availability",
    "unavailability_evidence_sha256",
}
PLAN_FIELDS = {
    "schema_version",
    "kind",
    "configuration_id",
    "configuration_sha256",
    "inventory_id",
    "inventory_sha256",
    "shard_index",
    "shard_count",
    "shard_id",
    "attempts",
}
ATTEMPT_FIELDS = {
    "result_id",
    "replay_attempt_id",
    "attempt_id",
    "required_action",
}
OBSERVATION_SHARD_FIELDS = PLAN_FIELDS - {"attempts"} | {"observations"}
OBSERVATION_FIELDS = {
    "result_id",
    "replay_attempt_id",
    "attempt_id",
    "status",
    "outcome",
    "evidence_sha256",
    "statistics",
}
STATISTICS_FIELDS = {
    "wall_time_ms",
    "peak_memory_bytes",
    "checker_invocations",
}


class KernelCorpusError(ValueError):
    """An independent-kernel corpus artifact violates the contract."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _identity(prefix: str, value: Any) -> str:
    return prefix + _digest(value)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise KernelCorpusError(f"{label} must be an object with string keys")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise KernelCorpusError(f"{label} must be an array")
    return value


def _fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise KernelCorpusError(
            f"{label} fields are not canonical; "
            f"missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _string(value: Any, label: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise KernelCorpusError(
            f"{label} must be a non-empty string of at most {maximum} UTF-8 bytes"
        )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise KernelCorpusError(f"{label} must not contain control characters")
    return value


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    text = _string(value, label)
    if pattern.fullmatch(text) is None:
        raise KernelCorpusError(f"{label} is not canonical")
    return text


def _timestamp(value: Any, label: str) -> str:
    text = _match(TIMESTAMP, value, label)
    try:
        dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as error:
        raise KernelCorpusError(f"{label} is not a real UTC timestamp") from error
    return text


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise KernelCorpusError(f"{label} must be an integer >= {minimum}")
    if value > 9_007_199_254_740_991:
        raise KernelCorpusError(f"{label} must be IEEE-754 safe")
    return value


def _load(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise KernelCorpusError(f"{path}: cannot read JSON: {error}") from error


def _component(value: Any, label: str, extra_fields: set[str]) -> dict[str, Any]:
    component = _object(value, label)
    _fields(component, {"repository", "commit", *extra_fields}, label)
    repository = _match(REPOSITORY, component["repository"], f"{label}.repository")
    if any(segment in {".", ".."} for segment in repository.split("/")):
        raise KernelCorpusError(f"{label}.repository contains a path segment")
    _match(COMMIT, component["commit"], f"{label}.commit")
    return component


def configuration_id(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "configuration_id"}
    return _identity("kcc1_", body)


def validate_series(value: Any) -> dict[str, Any]:
    series = _object(value, "series")
    _fields(series, SERIES_FIELDS, "series")
    if series["schema_version"] != 1 or isinstance(series["schema_version"], bool):
        raise KernelCorpusError("series schema_version must be integer 1")
    _match(SERIES_NAME, series["series_name"], "series.series_name")
    _match(CONFIGURATION_ID, series["configuration_id"], "series.configuration_id")

    candidate = _component(
        series["candidate"],
        "series.candidate",
        {"name", "binary_sha256", "protocol"},
    )
    _match(SAFE_NAME, candidate["name"], "series.candidate.name")
    _match(DIGEST, candidate["binary_sha256"], "series.candidate.binary_sha256")
    _match(SAFE_NAME, candidate["protocol"], "series.candidate.protocol")

    exporter = _component(series["exporter"], "series.exporter", {"artifact_sha256"})
    _match(DIGEST, exporter["artifact_sha256"], "series.exporter.artifact_sha256")

    checker = _component(
        series["checker"],
        "series.checker",
        {"protocol", "configuration_sha256"},
    )
    _match(SAFE_NAME, checker["protocol"], "series.checker.protocol")
    _match(
        DIGEST,
        checker["configuration_sha256"],
        "series.checker.configuration_sha256",
    )

    runner = _component(
        series["runner"],
        "series.runner",
        {"image_digest", "architecture", "operating_system", "resource_limits"},
    )
    image_digest = _string(runner["image_digest"], "series.runner.image_digest")
    if (
        not image_digest.startswith("sha256:")
        or DIGEST.fullmatch(image_digest[7:]) is None
    ):
        raise KernelCorpusError("series.runner.image_digest is not canonical")
    _match(SAFE_NAME, runner["architecture"], "series.runner.architecture")
    _match(SAFE_NAME, runner["operating_system"], "series.runner.operating_system")
    limits = _object(runner["resource_limits"], "series.runner.resource_limits")
    _fields(
        limits,
        {"wall_timeout_seconds", "max_memory_bytes"},
        "series.runner.resource_limits",
    )
    _integer(limits["wall_timeout_seconds"], "wall_timeout_seconds", 1)
    _integer(limits["max_memory_bytes"], "max_memory_bytes", 1)

    if series["configuration_id"] != configuration_id(series):
        raise KernelCorpusError(
            "series.configuration_id does not bind the exact series"
        )
    return series


def inventory_id(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "inventory_id"}
    return _identity("kci1_", body)


def validate_inventory(value: Any) -> dict[str, Any]:
    inventory = _object(value, "inventory")
    _fields(inventory, INVENTORY_FIELDS, "inventory")
    if inventory["schema_version"] != 1 or isinstance(
        inventory["schema_version"], bool
    ):
        raise KernelCorpusError("inventory schema_version must be integer 1")
    _match(INVENTORY_ID, inventory["inventory_id"], "inventory.inventory_id")
    _timestamp(inventory["cutoff_at"], "inventory.cutoff_at")
    store = _component(
        inventory["results_store"], "inventory.results_store", {"tree_sha256"}
    )
    _match(DIGEST, store["tree_sha256"], "inventory.results_store.tree_sha256")
    _match(
        DIGEST,
        inventory["historical_replay_report_sha256"],
        "inventory.historical_replay_report_sha256",
    )
    results = _array(inventory["results"], "inventory.results")
    if not results:
        raise KernelCorpusError("inventory.results must not be empty")
    identifiers: list[str] = []
    replay_attempts: list[str] = []
    for index, raw in enumerate(results):
        label = f"inventory.results[{index}]"
        result = _object(raw, label)
        _fields(result, INVENTORY_RESULT_FIELDS, label)
        identifiers.append(_match(RESULT_ID, result["result_id"], f"{label}.result_id"))
        replay_attempts.append(
            _match(
                REPLAY_ATTEMPT_ID,
                result["replay_attempt_id"],
                f"{label}.replay_attempt_id",
            )
        )
        availability = result["availability"]
        if availability not in AVAILABILITIES:
            raise KernelCorpusError(f"{label}.availability is not registered")
        outcome = result["authoritative_outcome"]
        evidence = result["unavailability_evidence_sha256"]
        if availability == "ready":
            if outcome not in TERMINAL_OUTCOMES:
                raise KernelCorpusError(
                    f"{label} ready result requires a terminal outcome"
                )
            if evidence is not None:
                raise KernelCorpusError(
                    f"{label} ready result cannot claim unavailability"
                )
        elif availability == "replay_pending":
            if outcome is not None or evidence is not None:
                raise KernelCorpusError(
                    f"{label} pending replay cannot claim outcome or unavailability"
                )
        else:
            if outcome is not None:
                raise KernelCorpusError(
                    f"{label} unavailable result cannot claim a terminal outcome"
                )
            _match(DIGEST, evidence, f"{label}.unavailability_evidence_sha256")
    if identifiers != sorted(identifiers):
        raise KernelCorpusError("inventory.results must be sorted by result_id")
    if len(set(identifiers)) != len(identifiers):
        raise KernelCorpusError("inventory contains duplicate result_id values")
    if len(set(replay_attempts)) != len(replay_attempts):
        raise KernelCorpusError("inventory contains duplicate replay_attempt_id values")
    if inventory["inventory_id"] != inventory_id(inventory):
        raise KernelCorpusError(
            "inventory.inventory_id does not bind the exact inventory"
        )
    return inventory


def attempt_id(
    series: dict[str, Any], inventory: dict[str, Any], result: dict[str, Any]
) -> str:
    return _identity(
        "kca1_",
        {
            "configuration_id": series["configuration_id"],
            "inventory_id": inventory["inventory_id"],
            "replay_attempt_id": result["replay_attempt_id"],
            "result_id": result["result_id"],
        },
    )


def _shard_index(result_id: str, shard_count: int) -> int:
    return int(hashlib.sha256(result_id.encode("ascii")).hexdigest(), 16) % shard_count


def _plan_without_id(
    series: dict[str, Any],
    inventory: dict[str, Any],
    shard_index: int,
    shard_count: int,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "kernel_corpus_shard_plan",
        "configuration_id": series["configuration_id"],
        "configuration_sha256": _digest(series),
        "inventory_id": inventory["inventory_id"],
        "inventory_sha256": _digest(inventory),
        "shard_index": shard_index,
        "shard_count": shard_count,
        "attempts": attempts,
    }


def build_shard_plans(
    series_value: Any, inventory_value: Any, shard_count: int
) -> list[dict[str, Any]]:
    series = validate_series(series_value)
    inventory = validate_inventory(inventory_value)
    _integer(shard_count, "shard_count", 1)
    if shard_count > len(inventory["results"]):
        raise KernelCorpusError("shard_count cannot exceed inventory result count")
    assigned: list[list[dict[str, Any]]] = [[] for _ in range(shard_count)]
    for result in inventory["results"]:
        action = {
            "ready": "run",
            "replay_pending": "record_replay_pending",
            "source_unavailable": "record_source_unavailable",
            "replay_unavailable": "record_replay_unavailable",
        }[result["availability"]]
        assigned[_shard_index(result["result_id"], shard_count)].append(
            {
                "result_id": result["result_id"],
                "replay_attempt_id": result["replay_attempt_id"],
                "attempt_id": attempt_id(series, inventory, result),
                "required_action": action,
            }
        )
    plans = []
    for index, attempts in enumerate(assigned):
        body = _plan_without_id(series, inventory, index, shard_count, attempts)
        plans.append({**body, "shard_id": _identity("ksh1_", body)})
    return plans


def validate_plan(
    value: Any,
    series_value: Any,
    inventory_value: Any,
) -> dict[str, Any]:
    plan = _object(value, "plan")
    _fields(plan, PLAN_FIELDS, "plan")
    _match(SHARD_ID, plan["shard_id"], "plan.shard_id")
    shard_count = _integer(plan["shard_count"], "plan.shard_count", 1)
    shard_index = _integer(plan["shard_index"], "plan.shard_index")
    if shard_index >= shard_count:
        raise KernelCorpusError("plan.shard_index is outside shard_count")
    expected = build_shard_plans(series_value, inventory_value, shard_count)[
        shard_index
    ]
    if plan != expected:
        raise KernelCorpusError("plan does not match the deterministic shard")
    return plan


def _validate_statistics(value: Any, label: str) -> dict[str, Any]:
    statistics = _object(value, label)
    _fields(statistics, STATISTICS_FIELDS, label)
    _integer(statistics["wall_time_ms"], f"{label}.wall_time_ms")
    _integer(statistics["peak_memory_bytes"], f"{label}.peak_memory_bytes")
    _integer(statistics["checker_invocations"], f"{label}.checker_invocations", 1)
    return statistics


def validate_observation_shard(
    value: Any,
    plan_value: Any,
    series_value: Any,
    inventory_value: Any,
) -> dict[str, Any]:
    plan = validate_plan(plan_value, series_value, inventory_value)
    inventory = validate_inventory(inventory_value)
    shard = _object(value, "observation shard")
    _fields(shard, OBSERVATION_SHARD_FIELDS, "observation shard")
    for field in PLAN_FIELDS - {"attempts", "kind"}:
        if shard[field] != plan[field]:
            raise KernelCorpusError(
                f"observation shard {field} does not match its plan"
            )
    if shard["kind"] != "kernel_corpus_observations":
        raise KernelCorpusError("observation shard kind is not registered")
    observations = _array(shard["observations"], "observation shard.observations")
    if len(observations) != len(plan["attempts"]):
        raise KernelCorpusError(
            "observation shard does not cover every planned attempt"
        )
    by_result = {result["result_id"]: result for result in inventory["results"]}
    for index, (raw, expected) in enumerate(
        zip(observations, plan["attempts"], strict=True)
    ):
        label = f"observation shard.observations[{index}]"
        observation = _object(raw, label)
        _fields(observation, OBSERVATION_FIELDS, label)
        for field in ("result_id", "replay_attempt_id", "attempt_id"):
            if observation[field] != expected[field]:
                raise KernelCorpusError(
                    f"{label}.{field} does not match the planned attempt"
                )
        status = observation["status"]
        outcome = observation["outcome"]
        evidence = observation["evidence_sha256"]
        statistics = observation["statistics"]
        source = by_result[observation["result_id"]]
        required = expected["required_action"]
        if status == "completed":
            if required != "run":
                raise KernelCorpusError(f"{label} cannot complete an unavailable input")
            if outcome not in TERMINAL_OUTCOMES:
                raise KernelCorpusError(f"{label}.outcome is not a terminal outcome")
            if evidence is not None:
                raise KernelCorpusError(
                    f"{label} completed outcome cannot claim unavailability"
                )
            _validate_statistics(statistics, f"{label}.statistics")
        elif status == "unavailable":
            if outcome not in UNAVAILABLE_OUTCOMES:
                raise KernelCorpusError(
                    f"{label}.outcome is not an unavailable outcome"
                )
            expected_unavailable = {
                "run": "export_unavailable",
                "record_source_unavailable": "source_unavailable",
                "record_replay_unavailable": "replay_unavailable",
            }.get(required)
            if outcome != expected_unavailable:
                raise KernelCorpusError(
                    f"{label} changes the planned availability class"
                )
            _match(DIGEST, evidence, f"{label}.evidence_sha256")
            if (
                required != "run"
                and evidence != source["unavailability_evidence_sha256"]
            ):
                raise KernelCorpusError(
                    f"{label} does not preserve inherited unavailability evidence"
                )
            if statistics is not None:
                raise KernelCorpusError(
                    f"{label} unavailable outcome cannot claim statistics"
                )
        elif status == "pending":
            expected_pending = {
                "run": "export_format_unsupported",
                "record_replay_pending": "replay_pending",
            }.get(required)
            if outcome != expected_pending:
                raise KernelCorpusError(
                    f"{label} changes the planned pending/export-format class"
                )
            if outcome == "replay_pending":
                if evidence is not None:
                    raise KernelCorpusError(
                        f"{label} pending replay cannot claim evidence"
                    )
            else:
                _match(DIGEST, evidence, f"{label}.evidence_sha256")
            if statistics is not None:
                raise KernelCorpusError(
                    f"{label} pending outcome cannot claim statistics"
                )
        else:
            raise KernelCorpusError(f"{label}.status is not registered")
    return shard


def _quantile(values: list[int], numerator: int, denominator: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) * numerator + denominator - 1) // denominator
    return ordered[max(rank, 1) - 1]


def _performance(observations: list[dict[str, Any]]) -> dict[str, Any]:
    terminal = [item for item in observations if item["status"] == "completed"]
    wall = [item["statistics"]["wall_time_ms"] for item in terminal]
    memory = [item["statistics"]["peak_memory_bytes"] for item in terminal]
    invocations = [item["statistics"]["checker_invocations"] for item in terminal]
    return {
        "sample_count": len(terminal),
        "wall_time_ms": {
            "minimum": min(wall) if wall else None,
            "maximum": max(wall) if wall else None,
            "median_upper": _quantile(wall, 1, 2),
            "p95_nearest_rank": _quantile(wall, 95, 100),
            "sum": sum(wall),
        },
        "peak_memory_bytes": {
            "maximum": max(memory) if memory else None,
        },
        "checker_invocations": {"sum": sum(invocations)},
    }


def aggregate_report(
    series_value: Any,
    inventory_value: Any,
    plan_values: list[Any],
    observation_values: list[Any],
) -> dict[str, Any]:
    series = validate_series(series_value)
    inventory = validate_inventory(inventory_value)
    if not plan_values:
        raise KernelCorpusError("at least one shard plan is required")
    plans = [validate_plan(value, series, inventory) for value in plan_values]
    shard_count = plans[0]["shard_count"]
    if len(plans) != shard_count:
        raise KernelCorpusError("plan set does not contain every shard")
    if [plan["shard_index"] for plan in plans] != list(range(shard_count)):
        raise KernelCorpusError(
            "plans must be ordered and cover every shard exactly once"
        )
    if any(plan["shard_count"] != shard_count for plan in plans):
        raise KernelCorpusError("plan set mixes shard counts")
    if len(observation_values) != shard_count:
        raise KernelCorpusError("observation set does not contain every shard")
    shards = [
        validate_observation_shard(value, plan, series, inventory)
        for value, plan in zip(observation_values, plans, strict=True)
    ]
    observations = [item for shard in shards for item in shard["observations"]]
    result_ids = [item["result_id"] for item in observations]
    expected_ids = [item["result_id"] for item in inventory["results"]]
    if sorted(result_ids) != expected_ids or len(set(result_ids)) != len(result_ids):
        raise KernelCorpusError("observation set omits or duplicates inventory results")

    counters = {
        outcome: 0
        for outcome in (*TERMINAL_OUTCOMES, *UNAVAILABLE_OUTCOMES, *PENDING_OUTCOMES)
    }
    for item in observations:
        counters[item["outcome"]] += 1
    authoritative = {
        item["result_id"]: item["authoritative_outcome"]
        for item in inventory["results"]
    }
    disagreements = [
        {
            "result_id": item["result_id"],
            "authoritative_outcome": authoritative[item["result_id"]],
            "candidate_outcome": item["outcome"],
            "adjudication": "required",
        }
        for item in observations
        if item["status"] == "completed"
        and item["outcome"] != authoritative[item["result_id"]]
    ]
    unavailable_count = sum(counters[outcome] for outcome in UNAVAILABLE_OUTCOMES)
    pending_count = sum(counters[outcome] for outcome in PENDING_OUTCOMES)
    blocking_reasons = ["human_promotion_review_required"]
    if unavailable_count:
        blocking_reasons.append("corpus_unavailable_results")
    if pending_count:
        blocking_reasons.append("corpus_pending_results")
    if counters["export_format_unsupported"]:
        blocking_reasons.append("export_format_review_required")
    if disagreements:
        blocking_reasons.append("disagreement_adjudication_required")
    return {
        "schema_version": 1,
        "kind": "kernel_corpus_report",
        "configuration_id": series["configuration_id"],
        "configuration_sha256": _digest(series),
        "inventory_id": inventory["inventory_id"],
        "inventory_sha256": _digest(inventory),
        "shard_count": shard_count,
        "plan_set_sha256": _digest(plans),
        "observation_set_sha256": _digest(shards),
        "coverage": {
            "inventory_results": len(inventory["results"]),
            "observations": len(observations),
            "complete": True,
        },
        "counters": counters,
        "performance": _performance(observations),
        "disagreements": sorted(disagreements, key=lambda item: item["result_id"]),
        "promotion": {
            "automated_eligibility": False,
            "blocking_reasons": blocking_reasons,
        },
    }


def validate_report(
    value: Any,
    series_value: Any,
    inventory_value: Any,
    plan_values: list[Any],
    observation_values: list[Any],
) -> dict[str, Any]:
    report = _object(value, "report")
    expected = aggregate_report(
        series_value,
        inventory_value,
        plan_values,
        observation_values,
    )
    if report != expected:
        raise KernelCorpusError("report is not the deterministic corpus aggregate")
    return report


def _write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-series", "validate-inventory"):
        command = commands.add_parser(name)
        command.add_argument("--input", type=pathlib.Path, required=True)
    prepare = commands.add_parser("prepare-shards")
    prepare.add_argument("--series", type=pathlib.Path, required=True)
    prepare.add_argument("--inventory", type=pathlib.Path, required=True)
    prepare.add_argument("--shard-count", type=int, required=True)
    prepare.add_argument("--output-dir", type=pathlib.Path, required=True)
    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--series", type=pathlib.Path, required=True)
    aggregate.add_argument("--inventory", type=pathlib.Path, required=True)
    aggregate.add_argument("--plans-dir", type=pathlib.Path, required=True)
    aggregate.add_argument("--observations-dir", type=pathlib.Path, required=True)
    aggregate.add_argument("--output", type=pathlib.Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-series":
            validate_series(_load(args.input))
        elif args.command == "validate-inventory":
            validate_inventory(_load(args.input))
        elif args.command == "prepare-shards":
            plans = build_shard_plans(
                _load(args.series), _load(args.inventory), args.shard_count
            )
            if args.output_dir.exists() and any(args.output_dir.iterdir()):
                raise KernelCorpusError("output directory must be absent or empty")
            for plan in plans:
                _write_json(
                    args.output_dir / f"shard-{plan['shard_index']:04d}.json", plan
                )
        else:
            plans = [
                _load(path) for path in sorted(args.plans_dir.glob("shard-*.json"))
            ]
            observations = [
                _load(path)
                for path in sorted(args.observations_dir.glob("shard-*.json"))
            ]
            report = aggregate_report(
                _load(args.series), _load(args.inventory), plans, observations
            )
            _write_json(args.output, report)
    except KernelCorpusError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
