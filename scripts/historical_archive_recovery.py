#!/usr/bin/env python3
"""Closed immutable evidence for historical public archive recovery; no effects."""
from __future__ import annotations

import hashlib
from typing import Any

from historical_replay_controller import (
    HistoricalReplayControllerError,
    _validate_schema,
    canonical_bytes,
    sha256_bytes,
)
from replay_orchestrator import canonical_json_bytes

RECOVERY_PREFIX = "evidence/historical-replay/source-recoveries/"
CROSSWALK_PREFIX = "evidence/historical-replay/archive-crosswalks/"
MIGRATION_PREFIX = "evidence/historical-replay/archive-migration-plans/"
PROFILE_PREFIX = "evidence/archive-replay/profiles/"
ENTRY_DOMAIN = b"lean-eval-historical-public-archive-recovery-entry-v2\0"
TASK_SET_DOMAIN = b"lean-eval-historical-public-archive-recovery-tasks-v2\0"
RECOVERY_FILE_FIELDS = {
    "reconfiguration_repository", "reconfiguration_commit", "reconfiguration_path",
    "reconfiguration_sha256", "reconfiguration_entry_sha256",
}


def recovery_entry_sha256(entry: dict[str, Any]) -> str:
    return hashlib.sha256(ENTRY_DOMAIN + canonical_json_bytes(entry)).hexdigest()


def task_id_set_sha256(identifiers: list[str]) -> str:
    return hashlib.sha256(TASK_SET_DOMAIN + canonical_json_bytes(identifiers)).hexdigest()


def crosswalk_entry_sha256(entry: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(entry))


def expected_recovery_entry(task: dict[str, Any]) -> dict[str, Any]:
    fields = {
        key: value for key, value in task.items()
        if key.startswith(("archive_", "crosswalk_"))
    }
    fields.update({
        "result_id": task["result_id"],
        "source_recovery_version": 2,
        "original_authority_event_id": task["authority_event_id"],
        "replay_task_id": task["replay_task_id"],
        "measurement_config_digest": task["measurement_config_digest"],
        "checker": task["checker"],
        "source_visibility": task["source_visibility"],
        "superseded_enqueue_event_id": task["superseded_enqueue_event_id"],
        "superseded_qualification_event_id": task["superseded_qualification_event_id"],
        "superseded_execution_profile_digest": task["superseded_execution_profile_digest"],
        "candidate_execution_profile_digest": task["execution_profile_digest"],
        "reason_code": "accepted_source_archive_recovery",
    })
    for key in ("repository", "commit", "path", "sha256"):
        fields["candidate_qualification_" + key] = task["qualification_" + key]
    return fields


def validate_recovery_manifest(value: Any, raw: bytes, task: dict[str, Any]) -> dict[str, Any]:
    _validate_schema(value, "historical-public-archive-recovery-v2.schema.json", "archive recovery manifest")
    if canonical_bytes(value) != raw or sha256_bytes(raw) != task["reconfiguration_sha256"]:
        raise HistoricalReplayControllerError("archive recovery manifest bytes differ")
    entries = value["entries"]
    identifiers = [entry["replay_task_id"] for entry in entries]
    results = [entry["result_id"] for entry in entries]
    if (
        identifiers != sorted(set(identifiers)) or len(set(results)) != len(results)
        or value["task_count"] != len(entries)
        or value["task_id_set_sha256"] != task_id_set_sha256(identifiers)
    ):
        raise HistoricalReplayControllerError("archive recovery manifest selection differs")
    selected = [entry for entry in entries if entry["replay_task_id"] == task["replay_task_id"]]
    if (
        len(selected) != 1 or selected[0] != expected_recovery_entry(task)
        or recovery_entry_sha256(selected[0]) != task["reconfiguration_entry_sha256"]
    ):
        raise HistoricalReplayControllerError("archive recovery manifest entry differs from queued task")
    return selected[0]


def expected_crosswalk_entry(task: dict[str, Any], original_schema: int) -> dict[str, Any]:
    return {
        "result_id": task["result_id"],
        "classification": "bound",
        "source_visibility": task["source_visibility"],
        "submission_id": task["archive_submission_id"],
        "archive_plan_entry_sha256": task["archive_plan_entry_sha256"],
        "archive_schema_version": original_schema,
        "archive_result_evidence": task["archive_result_evidence"],
        "benchmark_relation": task["archive_benchmark_relation"],
        **{key: task[key] for key in (
            "archive_commit", "archive_ciphertext_sha256", "archive_ciphertext_size",
            "archive_sidecar_sha256", "archive_key_envelope_sha256",
            "archive_plaintext_tar_sha256", "archive_plaintext_tar_size", "archive_capacity_class",
        )},
    }


def validate_archive_crosswalk(value: Any, raw: bytes, task: dict[str, Any]) -> dict[str, Any]:
    _validate_schema(value, "historical-archive-crosswalk-v2.schema.json", "archive source crosswalk")
    entries = value["entries"]
    results = [entry["result_id"] for entry in entries]
    if (
        canonical_bytes(value) != raw or sha256_bytes(raw) != task["crosswalk_sha256"]
        or results != sorted(set(results))
        or value["result_count"] != len(entries)
        or value["archive_count"] != len({entry["submission_id"] for entry in entries})
        or value["results_repository"] != task["results_repository"]
        or value["results_commit"] != task["results_commit"]
        or value["audit_repository"] != task["archive_repository"]
        or value["audit_commit"] != task["archive_commit"]
    ):
        raise HistoricalReplayControllerError("archive crosswalk roots/counts/bytes differ")
    selected = [entry for entry in entries if entry["result_id"] == task["result_id"]]
    if (
        len(selected) != 1
        or selected[0] != expected_crosswalk_entry(task, selected[0]["archive_schema_version"])
        or crosswalk_entry_sha256(selected[0]) != task["crosswalk_entry_sha256"]
    ):
        raise HistoricalReplayControllerError("archive crosswalk entry differs from queued task")
    return selected[0]
