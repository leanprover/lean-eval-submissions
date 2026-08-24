#!/usr/bin/env python3
"""Print a source-free authoritative executor failure, or print nothing."""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

MAXIMUM_RESPONSE_BYTES = 4096
ALLOWED_REASONS = {
    "input_transfer_failed",
    "command_rpc_failed",
    "command_failed",
    "command_output_invalid",
    "sandbox_destroy_failed",
    "unexpected_failure",
}
ALLOWED_DETAILS = {
    "archive_decryption_failed",
    "archive_expansion_too_large",
    "archive_input_invalid",
    "archive_invalid",
    "archive_member_count_invalid",
    "archive_member_unsafe",
    "archive_plaintext_identity_mismatch",
    "benchmark_identity_mismatch",
    "benchmark_identity_unavailable",
    "ciphertext_digest_mismatch",
    "decoded_input_too_large",
    "encoded_input_invalid",
    "evaluator_did_not_terminate",
    "evaluator_preflight_failed",
    "evaluator_results_invalid",
    "evaluator_results_unavailable",
    "evaluator_unavailable",
    "expectation_fields_invalid",
    "expectation_invalid",
    "expectation_schema_invalid",
    "execution_request_invalid",
    "measurement_evidence_invalid",
    "measurement_evidence_unavailable",
    "measurement_limits_mismatch",
    "network_isolation_failed",
    "profile_lock_mismatch",
    "plaintext_digest_mismatch",
    "plaintext_size_mismatch",
    "runtime_profile_mismatch",
    "unclassified_archive_failure",
    "unclassified_authoritative_failure",
    "verdict_invalid",
    "workspace_not_found",
}


def sanitized_failure(value: Any) -> dict[str, str] | None:
    """Return only the closed authoritative failure vocabulary."""

    if not isinstance(value, dict) or set(value) not in (
        {"error", "reason"},
        {"error", "reason", "detail"},
    ):
        return None
    reason = value.get("reason")
    if value.get("error") != "executor_failed" or not isinstance(reason, str):
        return None
    if reason not in ALLOWED_REASONS:
        return None
    detail = value.get("detail")
    if detail is not None and (not isinstance(detail, str) or detail not in ALLOWED_DETAILS):
        return None
    result = {"error": "executor_failed", "reason": reason}
    if detail is not None:
        result["detail"] = detail
    return result


def read_sanitized_failure(path: pathlib.Path) -> dict[str, str] | None:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAXIMUM_RESPONSE_BYTES:
            return None
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return sanitized_failure(value)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 2
    failure = read_sanitized_failure(pathlib.Path(argv[1]))
    if failure is None:
        return 1
    print(json.dumps(failure, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
