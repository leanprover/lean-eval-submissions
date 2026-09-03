#!/usr/bin/env python3
"""Close reviewed public-source decisions for one exact historical final delta."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
REQUEST_ID = re.compile(r"prr_[0-9a-f]{64}\Z")
DISPOSITION_PATH = re.compile(
    r"evidence/public-replay/unavailability-dispositions-v1/[0-9a-f]{64}\.json\Z"
)
MAX_BYTES = 16 * 1024 * 1024


class PublicDecisionError(ValueError):
    """The reviewed authority input does not close the exact public delta."""


def canonical(value: Any) -> bytes:
    try:
        raw = (
            json.dumps(
                value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise PublicDecisionError("value is not canonicalizable JSON") from error
    if not 0 < len(raw) <= MAX_BYTES:
        raise PublicDecisionError("canonical JSON exceeds its size bound")
    return raw


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_canonical(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise PublicDecisionError(f"{label} must be one regular file")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublicDecisionError(f"cannot read {label}") from error
    if not isinstance(value, dict) or canonical(value) != raw:
        raise PublicDecisionError(f"{label} is not canonical JSON")
    return value, raw


def _closed(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PublicDecisionError(f"{label} fields are not closed")
    return value


def build_decisions(
    delta_path: pathlib.Path, authority_path: pathlib.Path
) -> dict[str, Any]:
    delta, delta_raw = read_canonical(delta_path, "inventory delta")
    authority, _ = read_canonical(authority_path, "reviewed public authority")
    _closed(
        delta,
        {
            "schema_version",
            "kind",
            "source_repository",
            "baseline",
            "current",
            "delta_counts",
            "entries",
        },
        "inventory delta",
    )
    _closed(
        authority,
        {
            "schema_version",
            "kind",
            "source_repository",
            "source_commit",
            "results_store_sha256",
            "delta_sha256",
            "entries",
        },
        "reviewed public authority",
    )
    current = delta.get("current")
    if (
        delta.get("schema_version") != 1
        or delta.get("kind") != "historical_replay_inventory_delta"
        or delta.get("source_repository") != "leanprover/lean-eval-submissions"
        or not isinstance(current, dict)
        or authority.get("schema_version") != 1
        or authority.get("kind") != "historical_final_delta_public_authority"
        or authority.get("source_repository") != delta["source_repository"]
        or authority.get("source_commit") != current.get("source_commit")
        or authority.get("results_store_sha256") != current.get("results_store_sha256")
        or authority.get("delta_sha256") != sha256(delta_raw)
        or not isinstance(delta.get("entries"), list)
        or not isinstance(authority.get("entries"), list)
    ):
        raise PublicDecisionError("public authority identity is invalid")

    expected: dict[str, dict[str, Any]] = {}
    for entry in delta["entries"]:
        if not isinstance(entry, dict):
            raise PublicDecisionError("inventory delta entry is invalid")
        source = entry.get("source")
        result_id = entry.get("result_id")
        if (
            isinstance(source, dict)
            and source.get("visibility") == "public"
            and isinstance(result_id, str)
            and RESULT_ID.fullmatch(result_id) is not None
        ):
            if result_id in expected:
                raise PublicDecisionError("inventory delta repeats a public Result")
            expected[result_id] = entry

    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    common = {
        "result_id",
        "request_id",
        "workflow_run_identity_sha256",
        "source_kind",
        "source_repository",
        "source_commit",
        "classification",
    }
    for reviewed in authority["entries"]:
        if not isinstance(reviewed, dict):
            raise PublicDecisionError("reviewed public entry is invalid")
        result_id = reviewed.get("result_id")
        inventory = expected.get(result_id)
        source = None if inventory is None else inventory.get("source")
        if (
            inventory is None
            or result_id in seen
            or REQUEST_ID.fullmatch(str(reviewed.get("request_id"))) is None
            or DIGEST.fullmatch(str(reviewed.get("workflow_run_identity_sha256")))
            is None
            or reviewed.get("source_kind") != source.get("kind")
            or reviewed.get("source_repository") != source.get("repository")
            or reviewed.get("source_commit") != source.get("commit")
        ):
            raise PublicDecisionError("reviewed public entry does not bind the delta")
        classification = reviewed.get("classification")
        if classification == "available":
            _closed(reviewed, common | {"source_tree"}, "available public entry")
            if COMMIT.fullmatch(str(reviewed["source_tree"])) is None:
                raise PublicDecisionError("available public source tree is invalid")
        elif classification == "source_ref_permanently_unavailable":
            _closed(
                reviewed,
                common
                | {
                    "review_status",
                    "candidate_entry_sha256",
                    "disposition_path",
                    "disposition_sha256",
                    "reason_code",
                    "rationale_code",
                },
                "unavailable public entry",
            )
            if (
                reviewed["review_status"] != "reviewed"
                or DIGEST.fullmatch(str(reviewed["candidate_entry_sha256"])) is None
                or DIGEST.fullmatch(str(reviewed["disposition_sha256"])) is None
                or DISPOSITION_PATH.fullmatch(str(reviewed["disposition_path"])) is None
                or reviewed["disposition_path"]
                != "evidence/public-replay/unavailability-dispositions-v1/"
                + reviewed["disposition_sha256"]
                + ".json"
                or reviewed["reason_code"] != "source_ref_permanently_unavailable"
                or reviewed["rationale_code"]
                != "accepted_immutable_source_ref_unavailable_without_archive"
            ):
                raise PublicDecisionError(
                    "public unavailability is not exactly reviewed"
                )
        else:
            raise PublicDecisionError("public source remains unclassified")
        decisions.append(dict(reviewed))
        seen.add(result_id)
    if seen != set(expected) or [item["result_id"] for item in decisions] != sorted(
        seen
    ):
        raise PublicDecisionError(
            "reviewed authority does not exactly cover the public delta"
        )
    return {
        "schema_version": 1,
        "kind": "historical_final_delta_public_source_decisions",
        "source_repository": delta["source_repository"],
        "source_commit": current["source_commit"],
        "results_store_sha256": current["results_store_sha256"],
        "delta_sha256": sha256(delta_raw),
        "entries": decisions,
    }


def write_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise PublicDecisionError("refusing to overwrite decisions output")
    path.write_bytes(canonical(value))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delta", required=True, type=pathlib.Path)
    parser.add_argument("--reviewed-authority", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        write_exclusive(
            args.output,
            build_decisions(args.delta.resolve(), args.reviewed_authority.resolve()),
        )
    except (OSError, PublicDecisionError, TypeError, ValueError) as error:
        print(f"historical-final-delta-public-decisions: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
