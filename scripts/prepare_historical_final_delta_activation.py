#!/usr/bin/env python3
"""Bind exact qualified profiles and emit separate final-delta replay plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter
from typing import Any

from historical_replay_controller import (
    HistoricalReplayControllerError,
    validate_qualification,
)
from prepare_historical_private_replay import (
    PrivateReplayPlanError,
    load_private_profiles,
)
from prepare_historical_private_replay import (
    validate_plan as validate_private_plan,
)

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SUBMISSIONS_REPOSITORY = "leanprover/lean-eval-submissions"
PUBLIC_PROFILE_PREFIX = "evidence/public-replay/profiles"
PRIVATE_PLAN_PREFIX = "evidence/private-replay/plans"
PUBLIC_PLAN_PREFIX = "evidence/public-replay/plans"
MAX_BYTES = 16 * 1024 * 1024


class ActivationError(ValueError):
    """The final delta is not ready for an exact State append."""


def canonical(value: Any) -> bytes:
    try:
        raw = (
            json.dumps(
                value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True
            )
            + "\n"
        ).encode()
    except (TypeError, ValueError, UnicodeError) as error:
        raise ActivationError("value is not canonicalizable JSON") from error
    if not 0 < len(raw) <= MAX_BYTES:
        raise ActivationError("canonical JSON exceeds its bound")
    return raw


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_canonical(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ActivationError(f"{label} must be one regular file")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ActivationError(f"cannot read {label}") from error
    if not isinstance(value, dict) or canonical(value) != raw:
        raise ActivationError(f"{label} is not canonical JSON")
    return value, raw


def git(root: pathlib.Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ActivationError("exact Git profile binding failed") from error


def checkout_root(path: pathlib.Path) -> pathlib.Path:
    root = pathlib.Path(git(path.parent, "rev-parse", "--show-toplevel")).resolve()
    if git(root, "remote", "get-url", "origin") not in {
        "https://github.com/leanprover/lean-eval-submissions",
        "https://github.com/leanprover/lean-eval-submissions.git",
    }:
        raise ActivationError("profile checkout remote is not canonical")
    return root


def benchmark_authorities(
    root: pathlib.Path, commits: set[str]
) -> dict[str, dict[str, str]]:
    root = root.resolve()
    if git(root, "remote", "get-url", "origin") not in {
        "https://github.com/leanprover/lean-eval",
        "https://github.com/leanprover/lean-eval.git",
    }:
        raise ActivationError("benchmark checkout remote is not canonical")
    authorities: dict[str, dict[str, str]] = {}
    for commit in sorted(commits):
        if COMMIT.fullmatch(commit) is None:
            raise ActivationError("benchmark commit is invalid")
        try:
            raw = subprocess.run(
                ["git", "-C", str(root), "show", f"{commit}:lean-toolchain"],
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise ActivationError("benchmark toolchain blob is unavailable") from error
        try:
            toolchain = raw.decode("utf-8").strip()
        except UnicodeError as error:
            raise ActivationError("benchmark toolchain blob is invalid") from error
        authorities[commit] = {
            "benchmark_tree": git(root, "rev-parse", f"{commit}^{{tree}}"),
            "toolchain": toolchain,
            "lean_toolchain_blob_sha256": sha256(raw),
        }
    return authorities


def verify_crosswalk_blob(
    root: pathlib.Path, commit: str, binding: dict[str, Any]
) -> None:
    if (
        COMMIT.fullmatch(commit) is None
        or binding.get("commit") != commit
        or not isinstance(binding.get("path"), str)
        or DIGEST.fullmatch(str(binding.get("sha256"))) is None
    ):
        raise ActivationError("private crosswalk locator is invalid")
    try:
        raw = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{binding['path']}"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ActivationError(
            "private crosswalk committed blob is unavailable"
        ) from error
    if sha256(raw) != binding["sha256"]:
        raise ActivationError("private crosswalk committed blob differs")


def public_profiles(
    paths: list[pathlib.Path], commit: str | None
) -> dict[str, dict[str, Any]]:
    if paths and (not isinstance(commit, str) or COMMIT.fullmatch(commit) is None):
        raise ActivationError("public profiles require one exact authority commit")
    selected: dict[str, dict[str, Any]] = {}
    for path in paths:
        value, raw = read_canonical(path, "public profile")
        try:
            validate_qualification(value, raw)
        except HistoricalReplayControllerError as error:
            raise ActivationError("public profile is not qualified") from error
        root = checkout_root(path)
        relative = path.resolve().relative_to(root).as_posix()
        expected = f"{PUBLIC_PROFILE_PREFIX}/{value['execution_profile_digest']}.json"
        if (
            relative != expected
            or git(root, "hash-object", relative)
            != git(root, "rev-parse", f"{commit}:{relative}")
            or value["benchmark_commit"] in selected
        ):
            raise ActivationError("public profile Git locator is not exact")
        selected[value["benchmark_commit"]] = {
            "profile": value,
            "locator": {
                "repository": SUBMISSIONS_REPOSITORY,
                "commit": commit,
                "path": relative,
                "sha256": sha256(raw),
            },
        }
    return selected


def build(
    *,
    preparation: dict[str, Any],
    preparation_raw: bytes,
    preparation_commit: str,
    preparation_path: str,
    crosswalk_commit: str,
    public: dict[str, dict[str, Any]],
    private: dict[str, dict[str, Any]],
    benchmarks: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    if (
        COMMIT.fullmatch(preparation_commit) is None
        or COMMIT.fullmatch(crosswalk_commit) is None
    ):
        raise ActivationError("preparation or crosswalk commit is invalid")
    if (
        preparation.get("kind") != "historical_final_delta_preparation_packet"
        or preparation.get("activation_status")
        != "blocked_pending_exact_profiles_and_state_append"
        or preparation.get("source_repository") != SUBMISSIONS_REPOSITORY
        or not isinstance(preparation.get("entries"), list)
    ):
        raise ActivationError("preparation packet identity is invalid")
    crosswalk = preparation.get("classification_inputs", {}).get(
        "private_crosswalk", {}
    )
    if crosswalk.get("commit") != crosswalk_commit:
        raise ActivationError("private crosswalk commit differs from preparation")
    prep_sha = sha256(preparation_raw)
    if preparation_path != (
        f"evidence/historical-replay/final-delta-preparations/{prep_sha}.json"
    ):
        raise ActivationError("preparation packet path is not content addressed")
    required = {
        (entry["source_visibility"], entry["benchmark_commit"])
        for entry in preparation["entries"]
        if entry.get("disposition") == "replayable"
    }
    private_by_benchmark: dict[str, str] = {}
    for digest, profile in private.items():
        benchmark = profile["benchmark_commit"]
        if benchmark in private_by_benchmark:
            raise ActivationError("multiple private profiles target one benchmark")
        private_by_benchmark[benchmark] = digest
    supplied = {
        *(("public", benchmark) for benchmark in public),
        *(("private", benchmark) for benchmark in private_by_benchmark),
    }
    missing = sorted(required - supplied)
    extra = sorted(supplied - required)
    requirements = {
        "schema_version": 1,
        "kind": "historical_final_delta_qualification_requirements",
        "preparation_sha256": prep_sha,
        "activation_status": "ready" if not missing and not extra else "blocked",
        "missing": [
            {"source_visibility": visibility, "benchmark_commit": benchmark}
            for visibility, benchmark in missing
        ],
        "unexpected": [
            {"source_visibility": visibility, "benchmark_commit": benchmark}
            for visibility, benchmark in extra
        ],
        "conditional_action": (
            "none"
            if not missing
            else "run one-shot qualification only for each missing exact image"
        ),
    }
    if missing or extra:
        return requirements, None, None
    prep_locator = {
        "repository": SUBMISSIONS_REPOSITORY,
        "commit": preparation_commit,
        "path": preparation_path,
        "sha256": prep_sha,
    }
    public_entries: list[dict[str, Any]] = []
    private_entries: list[dict[str, Any]] = []
    for entry in preparation["entries"]:
        common = {**entry["result"], "result_id": entry["result_id"]}
        if entry["source_visibility"] == "public":
            output = {
                **common,
                "benchmark_repository": entry["benchmark_repository"],
                "benchmark_commit": entry["benchmark_commit"],
                "disposition": entry["disposition"],
                "public_authority": entry["public_authority"],
            }
            if entry["disposition"] == "replayable":
                selected = public[entry["benchmark_commit"]]
                profile = selected["profile"]
                benchmark = benchmarks[entry["benchmark_commit"]]
                if (
                    profile["benchmark_tree"] != benchmark["benchmark_tree"]
                    or profile["execution_profile"]["toolchain"]
                    != benchmark["toolchain"]
                ):
                    raise ActivationError(
                        "public profile and benchmark authority differ"
                    )
                output.update(
                    source=entry["source"],
                    toolchain=benchmark["toolchain"],
                    lean_toolchain_blob_sha256=benchmark["lean_toolchain_blob_sha256"],
                    execution_profile_digest=profile["execution_profile_digest"],
                    measurement_config_digest=profile["measurement_config_digest"],
                    qualification=selected["locator"],
                )
            else:
                output["source"] = entry["source"]
                output["unavailability"] = {
                    **entry["unavailability"],
                    "disposition_commit": preparation_commit,
                }
            public_entries.append(output)
        else:
            base = {
                **common,
                "benchmark_commit": entry["benchmark_commit"],
                "crosswalk_entry_sha256": entry.get(
                    "archive", entry.get("unavailability")
                )["crosswalk_entry_sha256"],
                "classification": (
                    "bound"
                    if entry["disposition"] == "replayable"
                    else "archive_not_found"
                ),
            }
            if entry["disposition"] == "replayable":
                profile_digest = private_by_benchmark[entry["benchmark_commit"]]
                base.update(
                    archive_submission_id=entry["archive"]["submission_id"],
                    archive_plan_entry_sha256=entry["archive"][
                        "archive_plan_entry_sha256"
                    ],
                    replay_profile_status="profile_qualified",
                    execution_profile_digest=profile_digest,
                )
            private_entries.append(base)
    public_plan = {
        "schema_version": 1,
        "kind": "historical_final_delta_public_replay_plan",
        "activation_status": "ready",
        "preparation": prep_locator,
        "results": {
            "repository": SUBMISSIONS_REPOSITORY,
            "commit": preparation["cutoff"]["current_inventory"]["source_commit"],
            "store_sha256": preparation["cutoff"]["current_inventory"][
                "results_store_sha256"
            ],
        },
        "entries": public_entries,
    }
    counts = Counter(entry["classification"] for entry in private_entries)
    private_plan = {
        "schema_version": 1,
        "kind": "historical_private_replay_plan",
        "results": {
            "repository": SUBMISSIONS_REPOSITORY,
            "commit": preparation["cutoff"]["current_inventory"]["source_commit"],
            "store_sha256": preparation["cutoff"]["current_inventory"][
                "results_store_sha256"
            ],
        },
        "crosswalk": {
            "repository": SUBMISSIONS_REPOSITORY,
            "commit": crosswalk_commit,
            "path": "evidence/historical-replay/private-crosswalks/"
            + preparation["classification_inputs"]["private_crosswalk"]["sha256"]
            + ".json",
            "sha256": preparation["classification_inputs"]["private_crosswalk"][
                "sha256"
            ],
        },
        "classification_counts": {
            "archive_not_found": counts["archive_not_found"],
            "bound": counts["bound"],
        },
        "replay_readiness_counts": {
            "archive_not_found": counts["archive_not_found"],
            "profile_pending": 0,
            "profile_qualified": counts["bound"],
        },
        "profiles": private,
        "entries": private_entries,
    }
    validate_private_plan(private_plan)
    return requirements, public_plan, private_plan


def write_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ActivationError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation", required=True, type=pathlib.Path)
    parser.add_argument("--preparation-commit", required=True)
    parser.add_argument("--crosswalk-commit", required=True)
    parser.add_argument(
        "--public-profile", action="append", default=[], type=pathlib.Path
    )
    parser.add_argument("--public-profile-commit")
    parser.add_argument(
        "--private-profile", action="append", default=[], type=pathlib.Path
    )
    parser.add_argument("--private-profile-commit")
    parser.add_argument("--benchmark-root", required=True, type=pathlib.Path)
    parser.add_argument("--requirements-output", required=True, type=pathlib.Path)
    parser.add_argument("--public-plan-output", type=pathlib.Path)
    parser.add_argument("--private-plan-output", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        preparation, raw = read_canonical(
            args.preparation.resolve(), "preparation packet"
        )
        preparation_root = pathlib.Path(
            git(args.preparation.resolve().parent, "rev-parse", "--show-toplevel")
        ).resolve()
        preparation_relative = (
            args.preparation.resolve().relative_to(preparation_root).as_posix()
        )
        if git(preparation_root, "hash-object", preparation_relative) != git(
            preparation_root,
            "rev-parse",
            f"{args.preparation_commit}:{preparation_relative}",
        ):
            raise ActivationError("preparation differs from its committed blob")
        for entry in preparation.get("entries", []):
            unavailable = entry.get("unavailability", {})
            disposition_path = unavailable.get("disposition_path")
            if disposition_path is not None and (
                git(
                    preparation_root,
                    "rev-parse",
                    f"{args.preparation_commit}:{disposition_path}",
                )
                != git(preparation_root, "hash-object", disposition_path)
                or sha256((preparation_root / disposition_path).read_bytes())
                != unavailable.get("disposition_sha256")
            ):
                raise ActivationError("public disposition authority is not exact")
        verify_crosswalk_blob(
            preparation_root,
            args.crosswalk_commit,
            preparation["classification_inputs"]["private_crosswalk"],
        )
        private = load_private_profiles(
            [path.resolve() for path in args.private_profile],
            args.private_profile_commit,
        )
        requirements, public_plan, private_plan = build(
            preparation=preparation,
            preparation_raw=raw,
            preparation_commit=args.preparation_commit,
            preparation_path=preparation_relative,
            crosswalk_commit=args.crosswalk_commit,
            public=public_profiles(
                [path.resolve() for path in args.public_profile],
                args.public_profile_commit,
            ),
            private=private,
            benchmarks=benchmark_authorities(
                args.benchmark_root,
                {
                    entry["benchmark_commit"]
                    for entry in preparation["entries"]
                    if entry.get("disposition") == "replayable"
                },
            ),
        )
        write_exclusive(args.requirements_output, requirements)
        if public_plan is not None:
            if args.public_plan_output is None or args.private_plan_output is None:
                raise ActivationError("ready activation requires both plan outputs")
            write_exclusive(args.public_plan_output, public_plan)
            write_exclusive(args.private_plan_output, private_plan)
    except (ActivationError, PrivateReplayPlanError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
