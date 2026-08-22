#!/usr/bin/env python3
"""Plan public replay work and validate disposable-runner verdict handoffs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
from typing import Any, Protocol

UUID7 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}")
REPLAY_ID = re.compile(r"rt1_[0-9a-f]{64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
PROBLEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
TOOLCHAIN = re.compile(r"leanprover/lean4:v[0-9]+\.[0-9]+\.[0-9]+")
TIMESTAMP = re.compile(
    r"(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z"
)

COUNTER_REASONS = {
    "counter_not_reported",
    "counter_not_supported",
    "counter_permission_denied",
}
FAILURE_REASONS = {
    "benchmark_fetch_failed",
    "runner_lost",
    "runner_start_failed",
    "source_fetch_failed",
    "toolchain_setup_failed",
    "verdict_invalid",
}
RETRYABLE_FAILURES = {
    "benchmark_fetch_failed",
    "runner_lost",
    "runner_start_failed",
    "source_fetch_failed",
}
UNAVAILABLE_REASONS = {
    "benchmark_ref_permanently_unavailable",
    "execution_profile_permanently_unavailable",
    "source_ref_permanently_unavailable",
}
BLOCKING_REASONS = {"private_replay_requires_d6"}
CHECKER_OUTCOMES = {
    "accepted": "replay.accepted",
    "rejected": "replay.rejected",
    "declined": "replay.declined",
}
EXECUTION_OUTCOMES = {
    "crashed": "replay.crashed",
    "timed_out": "replay.timed_out",
}
QUEUE_BASE_FIELDS = {
    "schema_version",
    "environment",
    "source_event_count",
    "source_digest",
    "tasks",
}
TASK_FIELDS = {
    "replay_task_id",
    "result_id",
    "submission_id",
    "problem_id",
    "statement_revision",
    "result_commit",
    "result_tree_digest",
    "source_repository",
    "source_commit",
    "source_visibility",
    "archive_repository",
    "archive_commit",
    "archive_path",
    "archive_ciphertext_sha256",
    "benchmark_repository",
    "benchmark_commit",
    "toolchain",
    "checker",
    "measurement_config_digest",
    "execution_profile_digest",
    "status",
    "attempt",
    "event_id",
    "occurred_at",
}


class ReplayError(ValueError):
    """A replay object violates replay schema version 1."""


class PrivateReplayProvider(Protocol):
    """Future D6-owned adapter; no production implementation exists here."""

    def prepare(self, locator: dict[str, Any]) -> dict[str, Any]:
        """Prepare one private archive without exposing a general key."""


class DisposableVmRunner(Protocol):
    """Host-owned one-task VM adapter; the foundation provides no backend."""

    def run(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run one schema-locked request and return one verdict object."""

    def destroy(self) -> None:
        """Destroy the VM and revoke its one-job registration."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReplayError(f"{label} must be an object with string keys")
    return value


def _fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ReplayError(
            f"{label} fields are not canonical; "
            f"missing={sorted(expected - set(value))}, extra={sorted(set(value) - expected)}"
        )


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReplayError(f"{label} must be a non-empty string")
    return value


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    text = _string(value, label)
    if pattern.fullmatch(text) is None:
        raise ReplayError(f"{label} is not canonical")
    return text


def _integer(value: Any, label: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ReplayError(f"{label} must be an integer >= {minimum}")
    return value


def _safe_positive_integer(value: Any, label: str) -> int:
    number = _integer(value, label, 1)
    if number > 9_007_199_254_740_991:
        raise ReplayError(f"{label} must be an IEEE-754-safe positive integer")
    return number


def _timestamp(value: Any, label: str) -> str:
    text = _match(TIMESTAMP, value, label)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReplayError(f"{label} is not a real calendar timestamp") from error
    if parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") != text:
        raise ReplayError(f"{label} is not canonical UTC milliseconds")
    return text


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical JSON for the schema-version-1 configuration value subset."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ReplayError("value is not canonical JSON") from error


def config_digest(domain: str, value: Any) -> str:
    return hashlib.sha256(domain.encode("utf-8") + b"\0" + canonical_json_bytes(value)).hexdigest()


def replay_task_id(result_id: str, measurement_config_digest: str) -> str:
    body = (
        "lean-eval-replay-task-v1\0"
        + result_id
        + "\0"
        + measurement_config_digest
    ).encode("utf-8")
    return "rt1_" + hashlib.sha256(body).hexdigest()


def canonical_archive_path(submission_id: str) -> str:
    return f"archives/{submission_id.replace('-', '')[:2]}/{submission_id}.tar.age"


def _repository_path(value: Any, label: str) -> str:
    text = _string(value, label)
    path = pathlib.PurePosixPath(text)
    if (
        path.is_absolute()
        or text != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) <= 0x1F or ord(character) == 0x7F for character in text)
    ):
        raise ReplayError(f"{label} is not a canonical safe repository path")
    return text


def validate_measurement_config(value: Any) -> dict[str, Any]:
    config = _object(value, "measurement configuration")
    _fields(
        config,
        {"schema_version", "wall_time_limit_ms", "memory_limit_bytes", "retired_instructions"},
        "measurement configuration",
    )
    if config["schema_version"] != 1 or isinstance(config["schema_version"], bool):
        raise ReplayError("measurement configuration schema_version must be integer 1")
    _integer(config["wall_time_limit_ms"], "wall_time_limit_ms", 1)
    _integer(config["memory_limit_bytes"], "memory_limit_bytes", 1)
    counter = _object(config["retired_instructions"], "retired_instructions")
    _fields(counter, {"required", "perf_event"}, "retired_instructions")
    if not isinstance(counter["required"], bool):
        raise ReplayError("retired_instructions.required must be boolean")
    if counter["perf_event"] != "instructions:u":
        raise ReplayError("retired_instructions.perf_event must be instructions:u")
    return config


def validate_execution_profile(value: Any) -> dict[str, Any]:
    profile = _object(value, "execution profile")
    _fields(
        profile,
        {
            "schema_version",
            "runner_profile",
            "vm_image_digest",
            "toolchain",
            "go_toolchain",
            "rust_toolchain",
            "cpu_model",
            "architecture",
            "kernel_release",
            "cache_state",
            "measurement_command",
            "components",
        },
        "execution profile",
    )
    if profile["schema_version"] != 1 or isinstance(profile["schema_version"], bool):
        raise ReplayError("execution profile schema_version must be integer 1")
    _string(profile["runner_profile"], "runner_profile")
    vm_digest = _string(profile["vm_image_digest"], "vm_image_digest")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", vm_digest) is None:
        raise ReplayError("vm_image_digest is not canonical")
    _match(TOOLCHAIN, profile["toolchain"], "toolchain")
    go_toolchain = _string(profile["go_toolchain"], "go_toolchain")
    if re.fullmatch(r"go[0-9]+\.[0-9]+\.[0-9]+", go_toolchain) is None:
        raise ReplayError("go_toolchain is not an exact version")
    rust_toolchain = _string(profile["rust_toolchain"], "rust_toolchain")
    if re.fullmatch(r"rustc-[0-9]+\.[0-9]+\.[0-9]+", rust_toolchain) is None:
        raise ReplayError("rust_toolchain is not an exact version")
    _string(profile["cpu_model"], "cpu_model")
    if profile["architecture"] not in {"aarch64", "x86_64"}:
        raise ReplayError("architecture is not registered")
    _string(profile["kernel_release"], "kernel_release")
    if profile["cache_state"] not in {"cold", "warm"}:
        raise ReplayError("cache_state is not registered")
    command = profile["measurement_command"]
    if not isinstance(command, list) or not command:
        raise ReplayError("measurement_command must be a non-empty argv array")
    for index, argument in enumerate(command):
        _string(argument, f"measurement_command[{index}]")
    components = _object(profile["components"], "components")
    _fields(components, {"comparator", "landrun", "lean4export", "nanoda"}, "components")
    for name, raw_component in components.items():
        component = _object(raw_component, f"components.{name}")
        _fields(component, {"repository", "commit"}, f"components.{name}")
        _match(REPOSITORY, component["repository"], f"components.{name}.repository")
        _match(COMMIT, component["commit"], f"components.{name}.commit")
    return profile


def _validate_task(value: Any, index: int) -> dict[str, Any]:
    label = f"tasks[{index}]"
    task = _object(value, label)
    status = task.get("status")
    fields = TASK_FIELDS | ({"reason_code", "retryable"} if status == "failed" else set())
    _fields(task, fields, label)
    _match(REPLAY_ID, task["replay_task_id"], f"{label}.replay_task_id")
    result = _match(RESULT_ID, task["result_id"], f"{label}.result_id")
    submission = _match(UUID7, task["submission_id"], f"{label}.submission_id")
    _match(PROBLEM, task["problem_id"], f"{label}.problem_id")
    _safe_positive_integer(task["statement_revision"], f"{label}.statement_revision")
    for field in ("result_commit", "source_commit", "archive_commit", "benchmark_commit"):
        _match(COMMIT, task[field], f"{label}.{field}")
    for field in (
        "result_tree_digest",
        "archive_ciphertext_sha256",
        "measurement_config_digest",
        "execution_profile_digest",
    ):
        _match(DIGEST, task[field], f"{label}.{field}")
    for field in ("source_repository", "archive_repository", "benchmark_repository"):
        _match(REPOSITORY, task[field], f"{label}.{field}")
    if task["archive_path"] != canonical_archive_path(submission):
        raise ReplayError(f"{label}.archive_path is not canonical for submission_id")
    if task["source_visibility"] not in {"public", "private"}:
        raise ReplayError(f"{label}.source_visibility is invalid")
    _match(TOOLCHAIN, task["toolchain"], f"{label}.toolchain")
    _string(task["checker"], f"{label}.checker")
    attempt = _integer(task["attempt"], f"{label}.attempt", 0)
    if status == "queued" and attempt != 0:
        raise ReplayError(f"{label}: queued task must have attempt 0")
    if status == "failed":
        if attempt < 1 or task["retryable"] is not True:
            raise ReplayError(f"{label}: failed queue task must be retryable after an attempt")
        if task["reason_code"] not in RETRYABLE_FAILURES:
            raise ReplayError(f"{label}.reason_code is not a retryable failure reason")
    elif status != "queued":
        raise ReplayError(f"{label}.status is not queueable")
    _match(UUID7, task["event_id"], f"{label}.event_id")
    _timestamp(task["occurred_at"], f"{label}.occurred_at")
    expected = replay_task_id(result, task["measurement_config_digest"])
    if task["replay_task_id"] != expected:
        raise ReplayError(f"{label}.replay_task_id does not match its locked identity")
    return task


def validate_queue(value: Any) -> dict[str, Any]:
    queue = _object(value, "replay queue")
    _fields(queue, QUEUE_BASE_FIELDS, "replay queue")
    if queue["schema_version"] != 1 or isinstance(queue["schema_version"], bool):
        raise ReplayError("replay queue schema_version must be integer 1")
    if queue["environment"] not in {"production", "staging"}:
        raise ReplayError("replay queue environment is invalid")
    _integer(queue["source_event_count"], "source_event_count", 1)
    _match(DIGEST, queue["source_digest"], "source_digest")
    if not isinstance(queue["tasks"], list):
        raise ReplayError("replay queue tasks must be an array")
    tasks = [_validate_task(task, index) for index, task in enumerate(queue["tasks"])]
    identities = [task["replay_task_id"] for task in tasks]
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise ReplayError("replay queue tasks must be unique and sorted by replay_task_id")
    return queue


def private_replay_locator(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "submission_id": task["submission_id"],
        "archive_repository": task["archive_repository"],
        "archive_commit": task["archive_commit"],
        "archive_path": task["archive_path"],
        "archive_ciphertext_sha256": task["archive_ciphertext_sha256"],
        "encrypted": True,
    }


def exercise_private_provider_for_test(
    task: dict[str, Any], provider: PrivateReplayProvider
) -> dict[str, Any]:
    """Exercise the future boundary in tests; production CLI never calls it."""
    if task["source_visibility"] != "private":
        raise ReplayError("private provider accepts only private-source tasks")
    return provider.prepare(private_replay_locator(task))


def plan_next(
    queue_value: Any,
    profile_value: Any,
    measurement_value: Any,
) -> dict[str, Any]:
    queue = validate_queue(queue_value)
    profile = validate_execution_profile(profile_value)
    measurement = validate_measurement_config(measurement_value)
    if not queue["tasks"]:
        return {"schema_version": 1, "kind": "empty"}
    task = queue["tasks"][0]
    expected_profile = config_digest("lean-eval-replay-execution-profile-v1", profile)
    if task["execution_profile_digest"] != expected_profile:
        raise ReplayError("execution profile digest does not match queued task")
    expected_measurement = config_digest("lean-eval-replay-measurement-config-v1", measurement)
    if task["measurement_config_digest"] != expected_measurement:
        raise ReplayError("measurement configuration digest does not match queued task")
    if profile["toolchain"] != task["toolchain"]:
        raise ReplayError("execution profile toolchain does not match queued toolchain")
    if task["checker"] != "nanoda":
        raise ReplayError(
            "execution profile schema version 1 supports only the pinned nanoda checker"
        )
    if task["source_visibility"] == "private":
        return {
            "schema_version": 1,
            "kind": "blocked",
            "replay_task_id": task["replay_task_id"],
            "queue_event_id": task["event_id"],
            "blocking_reason": "private_replay_requires_d6",
        }
    request = {
        "schema_version": 1,
        "replay_task_id": task["replay_task_id"],
        "attempt": task["attempt"] + 1,
        "source": {
            "repository": task["source_repository"],
            "commit": task["source_commit"],
            "visibility": "public",
        },
        "benchmark": {
            "repository": task["benchmark_repository"],
            "commit": task["benchmark_commit"],
            "toolchain": task["toolchain"],
        },
        "result": {
            "result_id": task["result_id"],
            "submission_id": task["submission_id"],
            "problem_id": task["problem_id"],
            "statement_revision": task["statement_revision"],
            "commit": task["result_commit"],
            "tree_digest": task["result_tree_digest"],
        },
        "checker": task["checker"],
        "execution_profile_digest": task["execution_profile_digest"],
        "measurement_config_digest": task["measurement_config_digest"],
        "execution_profile": profile,
        "measurement_config": measurement,
        "network": {
            "fetch_phase": "public_https_only",
            "untrusted_execution_phase": "disabled",
        },
        "untrusted_environment": {},
    }
    return {
        "schema_version": 1,
        "kind": "execution",
        "started_transition": {
            "event_type": "replay.started",
            "subject_id": task["replay_task_id"],
            "causation_event_id": task["event_id"],
            "payload": {
                "attempt": task["attempt"] + 1,
                "runner_profile": profile["runner_profile"],
            },
        },
        "request": request,
    }


def unavailable_transition(
    queue_value: Any,
    reason: str,
    evidence_value: Any,
) -> dict[str, Any]:
    queue = validate_queue(queue_value)
    if reason not in UNAVAILABLE_REASONS:
        raise ReplayError("unavailable reason is not registered")
    if not queue["tasks"]:
        raise ReplayError("cannot mark unavailable work from an empty queue")
    task = queue["tasks"][0]
    evidence = _object(evidence_value, "unavailability evidence")
    _fields(
        evidence,
        {"repository", "commit", "path", "sha256"},
        "unavailability evidence",
    )
    _match(REPOSITORY, evidence["repository"], "unavailability evidence.repository")
    _match(COMMIT, evidence["commit"], "unavailability evidence.commit")
    _repository_path(evidence["path"], "unavailability evidence.path")
    _match(DIGEST, evidence["sha256"], "unavailability evidence.sha256")
    return {
        "event_type": "replay.unavailable",
        "subject_id": task["replay_task_id"],
        "causation_event_id": task["event_id"],
        "payload": {
            "reason_code": reason,
            "evidence_repository": evidence["repository"],
            "evidence_commit": evidence["commit"],
            "evidence_path": evidence["path"],
            "evidence_sha256": evidence["sha256"],
        },
    }


def validate_execution_plan(value: Any) -> dict[str, Any]:
    plan = _object(value, "plan")
    _fields(
        plan,
        {"schema_version", "kind", "started_transition", "request"},
        "plan",
    )
    if plan["schema_version"] != 1 or plan["kind"] != "execution":
        raise ReplayError("plan must be an execution plan at schema version 1")
    request = _object(plan["request"], "plan.request")
    _fields(
        request,
        {
            "schema_version",
            "replay_task_id",
            "attempt",
            "source",
            "benchmark",
            "result",
            "checker",
            "execution_profile_digest",
            "measurement_config_digest",
            "execution_profile",
            "measurement_config",
            "network",
            "untrusted_environment",
        },
        "plan.request",
    )
    if request["schema_version"] != 1 or isinstance(request["schema_version"], bool):
        raise ReplayError("request schema_version must be integer 1")
    task_identity = _match(REPLAY_ID, request["replay_task_id"], "request.replay_task_id")
    attempt = _integer(request["attempt"], "request.attempt", 1)
    source = _object(request["source"], "request.source")
    _fields(source, {"repository", "commit", "visibility"}, "request.source")
    _match(REPOSITORY, source["repository"], "request.source.repository")
    _match(COMMIT, source["commit"], "request.source.commit")
    if source["visibility"] != "public":
        raise ReplayError("request source must be public")
    benchmark = _object(request["benchmark"], "request.benchmark")
    _fields(benchmark, {"repository", "commit", "toolchain"}, "request.benchmark")
    _match(REPOSITORY, benchmark["repository"], "request.benchmark.repository")
    _match(COMMIT, benchmark["commit"], "request.benchmark.commit")
    _match(TOOLCHAIN, benchmark["toolchain"], "request.benchmark.toolchain")
    result = _object(request["result"], "request.result")
    _fields(
        result,
        {
            "result_id",
            "submission_id",
            "problem_id",
            "statement_revision",
            "commit",
            "tree_digest",
        },
        "request.result",
    )
    result_identity = _match(RESULT_ID, result["result_id"], "request.result.result_id")
    _match(UUID7, result["submission_id"], "request.result.submission_id")
    _match(PROBLEM, result["problem_id"], "request.result.problem_id")
    _safe_positive_integer(
        result["statement_revision"],
        "request.result.statement_revision",
    )
    _match(COMMIT, result["commit"], "request.result.commit")
    _match(DIGEST, result["tree_digest"], "request.result.tree_digest")
    if request["checker"] != "nanoda":
        raise ReplayError("request checker must be nanoda")
    profile = validate_execution_profile(request["execution_profile"])
    measurement = validate_measurement_config(request["measurement_config"])
    if benchmark["toolchain"] != profile["toolchain"]:
        raise ReplayError("request benchmark and execution-profile toolchains differ")
    measurement_digest = config_digest(
        "lean-eval-replay-measurement-config-v1",
        measurement,
    )
    _match(DIGEST, request["measurement_config_digest"], "request.measurement_config_digest")
    if request["measurement_config_digest"] != measurement_digest:
        raise ReplayError("request measurement configuration digest does not match content")
    profile_digest = config_digest(
        "lean-eval-replay-execution-profile-v1",
        profile,
    )
    _match(DIGEST, request["execution_profile_digest"], "request.execution_profile_digest")
    if request["execution_profile_digest"] != profile_digest:
        raise ReplayError("request execution profile digest does not match content")
    if task_identity != replay_task_id(result_identity, measurement_digest):
        raise ReplayError("request replay_task_id does not match embedded configuration")
    network = _object(request["network"], "request.network")
    _fields(network, {"fetch_phase", "untrusted_execution_phase"}, "request.network")
    if network != {
        "fetch_phase": "public_https_only",
        "untrusted_execution_phase": "disabled",
    }:
        raise ReplayError("request network policy is not canonical")
    environment = _object(request["untrusted_environment"], "request.untrusted_environment")
    if environment:
        raise ReplayError("request untrusted environment must be empty")
    started = _object(plan["started_transition"], "plan.started_transition")
    _fields(
        started,
        {"event_type", "subject_id", "causation_event_id", "payload"},
        "plan.started_transition",
    )
    if started["event_type"] != "replay.started" or started["subject_id"] != task_identity:
        raise ReplayError("started transition does not match replay request")
    _match(UUID7, started["causation_event_id"], "started_transition.causation_event_id")
    payload = _object(started["payload"], "started_transition.payload")
    _fields(payload, {"attempt", "runner_profile"}, "started_transition.payload")
    if payload != {"attempt": attempt, "runner_profile": profile["runner_profile"]}:
        raise ReplayError("started transition payload does not match replay request")
    return plan


def validate_verdict(value: Any, request: dict[str, Any]) -> dict[str, Any]:
    verdict = _object(value, "verdict")
    _fields(
        verdict,
        {
            "schema_version",
            "replay_task_id",
            "attempt",
            "execution_outcome",
            "checker_outcome",
            "failure_reason",
            "statistics",
        },
        "verdict",
    )
    if verdict["schema_version"] != 1 or isinstance(verdict["schema_version"], bool):
        raise ReplayError("verdict schema_version must be integer 1")
    if verdict["replay_task_id"] != request["replay_task_id"]:
        raise ReplayError("verdict replay_task_id does not match request")
    if verdict["attempt"] != request["attempt"]:
        raise ReplayError("verdict attempt does not match request")
    outcome = verdict["execution_outcome"]
    if outcome == "failed":
        if verdict["checker_outcome"] is not None or verdict["statistics"] is not None:
            raise ReplayError("failed execution cannot claim a checker outcome or statistics")
        if verdict["failure_reason"] not in FAILURE_REASONS:
            raise ReplayError("failed execution requires a registered failure_reason")
        return verdict
    if outcome not in {"completed", *EXECUTION_OUTCOMES}:
        raise ReplayError("execution_outcome is not registered")
    if verdict["failure_reason"] is not None:
        raise ReplayError("reported execution cannot have a failure_reason")
    if outcome == "completed":
        if verdict["checker_outcome"] not in CHECKER_OUTCOMES:
            raise ReplayError("completed execution requires a checker outcome")
    elif verdict["checker_outcome"] is not None:
        raise ReplayError("crash or timeout cannot claim a checker outcome")
    statistics = _object(verdict["statistics"], "statistics")
    _fields(
        statistics,
        {
            "checker_wall_time_ms",
            "checker_retired_instructions",
            "build_wall_time_ms",
            "build_retired_instructions",
            "lines_of_code",
            "file_count",
        },
        "statistics",
    )
    for field in ("checker_wall_time_ms", "build_wall_time_ms", "lines_of_code", "file_count"):
        _integer(statistics[field], f"statistics.{field}", 0)
    for field in ("checker_retired_instructions", "build_retired_instructions"):
        counter = _object(statistics[field], f"statistics.{field}")
        status = counter.get("status")
        if status == "measured":
            _fields(counter, {"status", "value"}, f"statistics.{field}")
            _integer(counter["value"], f"statistics.{field}.value", 0)
        elif status == "unavailable":
            _fields(counter, {"status", "reason"}, f"statistics.{field}")
            if counter["reason"] not in COUNTER_REASONS:
                raise ReplayError(f"statistics.{field} reason is not registered")
        else:
            raise ReplayError(f"statistics.{field} status is invalid")
    return verdict


def terminal_transition(
    plan_value: Any,
    verdict_value: Any,
    started_event_id: str,
) -> dict[str, Any]:
    plan = validate_execution_plan(plan_value)
    request = plan["request"]
    _match(UUID7, started_event_id, "started_event_id")
    verdict = validate_verdict(verdict_value, request)
    if verdict["execution_outcome"] == "failed":
        reason = verdict["failure_reason"]
        return {
            "event_type": "replay.failed",
            "subject_id": request["replay_task_id"],
            "causation_event_id": started_event_id,
            "payload": {
                "attempt": request["attempt"],
                "reason_code": reason,
                "retryable": reason in RETRYABLE_FAILURES,
            },
        }
    statistics = verdict["statistics"]
    checker_counter = statistics["checker_retired_instructions"]
    build_counter = statistics["build_retired_instructions"]
    checker_measured = checker_counter["status"] == "measured"
    build_measured = build_counter["status"] == "measured"
    if (
        request["measurement_config"]["retired_instructions"]["required"]
        and not (checker_measured and build_measured)
    ):
        raise ReplayError("required retired-instruction counter was unavailable")
    event_type = (
        CHECKER_OUTCOMES[verdict["checker_outcome"]]
        if verdict["execution_outcome"] == "completed"
        else EXECUTION_OUTCOMES[verdict["execution_outcome"]]
    )
    return {
        "event_type": event_type,
        "subject_id": request["replay_task_id"],
        "causation_event_id": started_event_id,
        "payload": {
            "attempt": request["attempt"],
            "checker": request["checker"],
            "checker_wall_time_ms": statistics["checker_wall_time_ms"],
            "checker_retired_instructions": (
                checker_counter["value"] if checker_measured else None
            ),
            "checker_retired_instructions_unavailable_reason": (
                None if checker_measured else checker_counter["reason"]
            ),
            "build_wall_time_ms": statistics["build_wall_time_ms"],
            "build_retired_instructions": (
                build_counter["value"] if build_measured else None
            ),
            "build_retired_instructions_unavailable_reason": (
                None if build_measured else build_counter["reason"]
            ),
            "lines_of_code": statistics["lines_of_code"],
            "file_count": statistics["file_count"],
        },
    }


def run_with_disposable_vm(
    plan_value: Any,
    runner: DisposableVmRunner,
    started_event_id: str,
) -> dict[str, Any]:
    """Exercise one host adapter, always destroying it before returning."""
    try:
        plan = validate_execution_plan(plan_value)
        verdict = runner.run(plan["request"])
        return terminal_transition(plan, verdict, started_event_id)
    finally:
        runner.destroy()


def _read(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReplayError(f"{path}: cannot read JSON: {error}") from error


def _emit(value: Any, output: pathlib.Path | None) -> None:
    text = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--queue", required=True, type=pathlib.Path)
    plan_parser.add_argument("--execution-profile", required=True, type=pathlib.Path)
    plan_parser.add_argument("--measurement-config", required=True, type=pathlib.Path)
    plan_parser.add_argument("--output", type=pathlib.Path)
    verdict_parser = commands.add_parser("terminal-transition")
    verdict_parser.add_argument("--plan", required=True, type=pathlib.Path)
    verdict_parser.add_argument("--verdict", required=True, type=pathlib.Path)
    verdict_parser.add_argument("--started-event-id", required=True)
    verdict_parser.add_argument("--output", type=pathlib.Path)
    unavailable_parser = commands.add_parser("unavailable-transition")
    unavailable_parser.add_argument("--queue", required=True, type=pathlib.Path)
    unavailable_parser.add_argument("--reason", required=True)
    unavailable_parser.add_argument("--evidence", required=True, type=pathlib.Path)
    unavailable_parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            value = plan_next(
                _read(args.queue),
                _read(args.execution_profile),
                _read(args.measurement_config),
            )
        elif args.command == "terminal-transition":
            value = terminal_transition(
                _read(args.plan),
                _read(args.verdict),
                args.started_event_id,
            )
        else:
            value = unavailable_transition(
                _read(args.queue),
                args.reason,
                _read(args.evidence),
            )
        _emit(value, args.output)
    except ReplayError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
