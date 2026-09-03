#!/usr/bin/env python3
"""Build independently reconciled final-delta activation and terminal packets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

from prepare_historical_final_delta_state import document_bytes

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
RESULT_ID = re.compile(r"r2_[0-9a-f]{64}\Z")
TASK_ID = re.compile(r"rt1_[0-9a-f]{64}\Z")
TIMESTAMP = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z\Z"
)
SUBMISSIONS_REPOSITORY = "leanprover/lean-eval-submissions"
AUDIT_REPOSITORY = "leanprover/lean-eval-audit"
STATE_REPOSITORY = "leanprover/lean-eval-state"
PUBLIC_PLAN_PREFIX = "evidence/public-replay/plans"
PRIVATE_PLAN_PREFIX = "evidence/private-replay/plans"
ACTIVATION_PREFIX = "evidence/historical-replay/final-delta-activations"
ABSENCE_PREFIX = "evidence/historical-replay/final-delta-executor-absence"
TERMINAL_PREFIX = "evidence/historical-replay/final-delta-terminals"
TEMPORARY_WORKFLOWS = [
    ".github/workflows/historical-final-delta-activation.yml",
    ".github/workflows/historical-final-delta-archive-migration.yml",
    ".github/workflows/historical-final-delta-packet.yml",
    ".github/workflows/historical-final-delta-plans.yml",
    ".github/workflows/historical-final-delta-public-decisions.yml",
    ".github/workflows/historical-final-delta-state.yml",
    ".github/workflows/promote-historical-final-delta-archive-migration.yml",
]
MAX_BYTES = 16 * 1024 * 1024


class ClosureError(ValueError):
    """The final delta is not exactly activated or terminal."""


def canonical(value: Any) -> bytes:
    try:
        raw = (
            json.dumps(
                value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True
            )
            + "\n"
        ).encode()
    except (TypeError, ValueError, UnicodeError) as error:
        raise ClosureError("value is not canonicalizable JSON") from error
    if not 0 < len(raw) <= MAX_BYTES:
        raise ClosureError("canonical JSON exceeds its bound")
    return raw


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_canonical(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ClosureError(f"{label} must be one regular file")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ClosureError(f"cannot read {label}") from error
    if not isinstance(value, dict) or canonical(value) != raw:
        raise ClosureError(f"{label} is not canonical JSON")
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
        raise ClosureError("exact Git authority proof failed") from error


def digest_set(values: list[str]) -> str:
    if len(values) != len(set(values)):
        raise ClosureError("identity set contains duplicates")
    return sha256(document_bytes(sorted(values)))


def locator(*, commit: str, path: str, raw: bytes, prefix: str) -> dict[str, str]:
    digest = sha256(raw)
    if (
        COMMIT.fullmatch(commit) is None
        or path != f"{prefix}/{digest}.json"
        or pathlib.PurePosixPath(path).is_absolute()
    ):
        raise ClosureError("content-addressed locator is invalid")
    return {
        "repository": SUBMISSIONS_REPOSITORY,
        "commit": commit,
        "path": path,
        "sha256": digest,
    }


def _plan_inventory(
    public: dict[str, Any], private: dict[str, Any]
) -> dict[str, dict[str, list[str]]]:
    if (
        public.get("kind") != "historical_final_delta_public_replay_plan"
        or public.get("activation_status") != "ready"
        or private.get("kind") != "historical_private_replay_plan"
        or not isinstance(public.get("entries"), list)
        or not isinstance(private.get("entries"), list)
    ):
        raise ClosureError("final-delta replay plan identity is invalid")
    output: dict[str, dict[str, list[str]]] = {}
    for lane, entries in (
        ("public", public["entries"]),
        ("private", private["entries"]),
    ):
        results: list[str] = []
        replayable: list[str] = []
        unavailable: list[str] = []
        for entry in entries:
            result_id = entry.get("result_id")
            if not isinstance(result_id, str) or RESULT_ID.fullmatch(result_id) is None:
                raise ClosureError("plan Result identity is invalid")
            results.append(result_id)
            disposition = (
                entry.get("disposition")
                if lane == "public"
                else (
                    "replayable"
                    if entry.get("classification") == "bound"
                    else "unavailable"
                )
            )
            if disposition == "replayable":
                replayable.append(result_id)
            elif disposition == "unavailable":
                unavailable.append(result_id)
            else:
                raise ClosureError("plan disposition is invalid")
        digest_set(results)
        output[lane] = {
            "result_ids": sorted(results),
            "replayable_result_ids": sorted(replayable),
            "unavailable_result_ids": sorted(unavailable),
        }
    if set(output["public"]["result_ids"]).intersection(
        output["private"]["result_ids"]
    ):
        raise ClosureError("public and private plans overlap")
    return output


def _candidate_inventory(
    state_root: pathlib.Path, binding: dict[str, Any]
) -> dict[str, dict[str, list[str]]]:
    state = binding.get("state", {})
    candidate = state.get("candidate_commit")
    parent = state.get("parent")
    if not all(
        isinstance(value, str) and COMMIT.fullmatch(value)
        for value in (candidate, parent)
    ):
        raise ClosureError("State promotion binding is invalid")
    if git(state_root, "rev-parse", f"{candidate}^{{tree}}") != state.get(
        "candidate_tree"
    ) or git(state_root, "rev-list", "--parents", "-n", "1", candidate).split() != [
        candidate,
        parent,
    ]:
        raise ClosureError("State candidate commit or tree differs")
    status = git(
        state_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "--no-renames",
        "-r",
        parent,
        candidate,
    ).splitlines()
    if (
        not status
        or len(status) != len(set(status))
        or any(not line.startswith(("A\tevents/", "A\tviews/")) for line in status)
    ):
        raise ClosureError("State candidate is not a create-only event and view append")
    paths = [line.split("\t", 1)[1] for line in status if line.startswith("A\tevents/")]
    if not paths:
        raise ClosureError("State candidate contains no final-delta events")
    qualification_lanes: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    for path in paths:
        try:
            value = json.loads(
                subprocess.run(
                    ["git", "-C", str(state_root), "show", f"{candidate}:{path}"],
                    check=True,
                    capture_output=True,
                ).stdout
            )
        except (
            subprocess.CalledProcessError,
            UnicodeError,
            json.JSONDecodeError,
        ) as error:
            raise ClosureError("cannot read staged State event") from error
        events.append(value)
        event_type = value.get("event_type")
        if event_type == "historical_result.replay_profile_qualified":
            qualification_lanes[value["event_id"]] = "public"
        elif event_type == "historical_archive_result.replay_profile_qualified":
            qualification_lanes[value["event_id"]] = "private"
    output = {
        lane: {
            "task_ids": [],
            "replayable_result_ids": [],
            "unavailable_result_ids": [],
        }
        for lane in ("public", "private")
    }
    for event in events:
        event_type = event.get("event_type")
        if event_type == "replay.enqueued":
            lane = qualification_lanes.get(event.get("causation_event_id"))
            task_id = event.get("subject_id")
            result_id = event.get("payload", {}).get("result_id")
            if (
                lane is None
                or not isinstance(task_id, str)
                or TASK_ID.fullmatch(task_id) is None
                or not isinstance(result_id, str)
                or RESULT_ID.fullmatch(result_id) is None
            ):
                raise ClosureError("candidate replay enqueue is invalid")
            output[lane]["task_ids"].append(task_id)
            output[lane]["replayable_result_ids"].append(result_id)
        elif event_type in {
            "historical_result.replay_unavailable",
            "historical_archive_result.replay_unavailable",
        }:
            lane = (
                "public" if event_type.startswith("historical_result.") else "private"
            )
            result_id = event.get("subject_id")
            if not isinstance(result_id, str) or RESULT_ID.fullmatch(result_id) is None:
                raise ClosureError("candidate unavailability Result is invalid")
            output[lane]["unavailable_result_ids"].append(result_id)
    for lane in output.values():
        for values in lane.values():
            values.sort()
            digest_set(values)
    return output


def build_activation(
    *,
    preparation: dict[str, Any],
    preparation_locator: dict[str, str],
    public_plan: dict[str, Any],
    public_locator: dict[str, str],
    private_plan: dict[str, Any],
    private_locator: dict[str, str],
    promotion: dict[str, Any],
    promotion_locator: dict[str, str],
    candidate: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    if (
        preparation.get("kind") != "historical_final_delta_preparation_packet"
        or preparation.get("cutoff", {}).get("delta_counts", {}).get("result_count")
        != len(preparation.get("entries", []))
        or promotion.get("kind") != "historical_final_delta_state_promotion_binding"
        or promotion.get("review_branch") != "historical-final-delta-state-v1"
    ):
        raise ClosureError("preparation or State promotion identity is invalid")
    cutoff = preparation["cutoff"]
    accepted_inventory = cutoff.get("current_inventory", {})
    if (
        not isinstance(accepted_inventory, dict)
        or accepted_inventory.get("result_count")
        != cutoff["delta_counts"]["result_count"]
        + cutoff.get("baseline_inventory", {}).get("result_count", -1)
        or DIGEST.fullmatch(str(cutoff.get("delta_sha256"))) is None
    ):
        raise ClosureError("accepted inventory and final delta counts differ")
    plans = _plan_inventory(public_plan, private_plan)
    preparation_ids = sorted(entry.get("result_id") for entry in preparation["entries"])
    if any(not isinstance(value, str) for value in preparation_ids):
        raise ClosureError("preparation Result identity is invalid")
    planned_ids = sorted(plans["public"]["result_ids"] + plans["private"]["result_ids"])
    if preparation_ids != planned_ids:
        raise ClosureError("plans do not exactly cover the preparation packet")
    for lane in ("public", "private"):
        if (
            plans[lane]["replayable_result_ids"]
            != candidate[lane]["replayable_result_ids"]
            or plans[lane]["unavailable_result_ids"]
            != candidate[lane]["unavailable_result_ids"]
            or promotion.get("candidate", {})
            .get("lanes", {})
            .get(lane, {})
            .get("task_count")
            != len(candidate[lane]["task_ids"])
            or promotion.get("candidate", {})
            .get("lanes", {})
            .get(lane, {})
            .get("task_id_set_sha256")
            != digest_set(candidate[lane]["task_ids"])
            or promotion.get("candidate", {})
            .get("lanes", {})
            .get(lane, {})
            .get("result_id_set_sha256")
            != digest_set(candidate[lane]["replayable_result_ids"])
        ):
            raise ClosureError(
                "State candidate does not exactly cover a final-delta plan"
            )
    audit = promotion.get("audit", {})
    state = promotion.get("state", {})
    if (
        audit.get("repository") != AUDIT_REPOSITORY
        or state.get("repository") != STATE_REPOSITORY
        or not all(
            isinstance(value, str) and COMMIT.fullmatch(value)
            for value in (
                audit.get("head"),
                audit.get("tree"),
                state.get("parent"),
                state.get("parent_tree"),
                state.get("candidate_commit"),
                state.get("candidate_tree"),
            )
        )
    ):
        raise ClosureError("audit or State authority is invalid")
    lanes: dict[str, Any] = {}
    for lane in ("public", "private"):
        all_ids = plans[lane]["result_ids"]
        tasks = candidate[lane]["task_ids"]
        unavailable = plans[lane]["unavailable_result_ids"]
        lanes[lane] = {
            "accepted_result_count": len(all_ids),
            "accepted_result_ids": all_ids,
            "accepted_result_id_set_sha256": digest_set(all_ids),
            "replay_task_count": len(tasks),
            "replay_task_ids": tasks,
            "replay_task_id_set_sha256": digest_set(tasks),
            "reviewed_unavailable_count": len(unavailable),
            "reviewed_unavailable_result_ids": unavailable,
            "reviewed_unavailable_result_id_set_sha256": digest_set(unavailable),
        }
    accepted = (
        lanes["public"]["accepted_result_ids"] + lanes["private"]["accepted_result_ids"]
    )
    return {
        "schema_version": 1,
        "kind": "historical_final_delta_activation_binding",
        "activation_status": "reviewed_state_candidate",
        "preparation": preparation_locator,
        "cutoff": {
            "accepted_inventory": preparation["cutoff"]["current_inventory"],
            "delta_sha256": preparation["cutoff"]["delta_sha256"],
            "delta_counts": preparation["cutoff"]["delta_counts"],
        },
        "plans": {"public": public_locator, "private": private_locator},
        "state_promotion": promotion_locator,
        "audit": {
            "repository": AUDIT_REPOSITORY,
            "head": audit["head"],
            "tree": audit["tree"],
        },
        "state": {
            "repository": STATE_REPOSITORY,
            "parent": state["parent"],
            "parent_tree": state["parent_tree"],
            "candidate_commit": state["candidate_commit"],
            "candidate_tree": state["candidate_tree"],
        },
        "review_branches": {
            "audit": "historical-final-delta-archive-rewrap-v1",
            "state": "historical-final-delta-state-v1",
        },
        "accepted_result_count": len(accepted),
        "accepted_result_id_set_sha256": digest_set(accepted),
        "lanes": lanes,
    }


def _load_state_events(
    state_root: pathlib.Path, state_head: str
) -> list[dict[str, Any]]:
    paths = git(
        state_root, "ls-tree", "-r", "--name-only", state_head, "events"
    ).splitlines()
    events: list[dict[str, Any]] = []
    for path in paths:
        if not path.startswith("events/") or not path.endswith(".json"):
            raise ClosureError("State event tree path is invalid")
        try:
            raw = subprocess.run(
                ["git", "-C", str(state_root), "show", f"{state_head}:{path}"],
                check=True,
                capture_output=True,
            ).stdout
            event = json.loads(raw)
        except (
            subprocess.CalledProcessError,
            UnicodeError,
            json.JSONDecodeError,
        ) as error:
            raise ClosureError("cannot read terminal State event") from error
        if not isinstance(event, dict):
            raise ClosureError("State event is invalid")
        events.append(event)
    return events


def build_terminal(
    *,
    activation: dict[str, Any],
    activation_locator: dict[str, str],
    absence: dict[str, Any],
    absence_locator: dict[str, str],
    terminal_readback: dict[str, Any],
    state_root: pathlib.Path,
    state_head: str,
    audit_head: str,
    audit_tree: str,
) -> dict[str, Any]:
    expected_controller_variables = {
        "HISTORICAL_PRIVATE_REPLAY_CONTROLLER_ENABLED": "absent",
        "HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED": "absent",
    }
    expected_review_branches = {
        "audit": {
            "name": "historical-final-delta-archive-rewrap-v1",
            "status": "absent",
        },
        "state": {"name": "historical-final-delta-state-v1", "status": "absent"},
    }
    if (
        activation.get("kind") != "historical_final_delta_activation_binding"
        or activation.get("activation_status") != "reviewed_state_candidate"
        or absence.get("kind") != "historical_final_delta_executor_absence"
        or absence.get("status") != "verified_absent"
        or absence.get("controller_variables") != expected_controller_variables
        or absence.get("review_branches") != expected_review_branches
        or absence.get("temporary_workflows") != TEMPORARY_WORKFLOWS
        or absence.get("executors", {}).get("public", {}).get("status")
        != "no_running_task"
        or absence.get("executors", {}).get("private", {}).get("worker_count") != 0
        or absence.get("executors", {}).get("private", {}).get("application_count") != 0
        or absence.get("authority", {}).get("repository") != SUBMISSIONS_REPOSITORY
        or absence.get("authority", {}).get("workflow_path")
        != ".github/workflows/historical-final-delta-activation.yml"
        or COMMIT.fullmatch(
            str(absence.get("authority", {}).get("implementation_commit"))
        )
        is None
        or not isinstance(absence.get("authority", {}).get("workflow_run_id"), int)
        or absence["authority"]["workflow_run_id"] < 1
        or not isinstance(absence.get("readbacks"), dict)
        or set(absence["readbacks"])
        != {
            "github_variables_sha256",
            "github_review_refs_sha256",
            "cloudflare_worker_services_sha256",
            "cloudflare_container_applications_sha256",
        }
        or any(
            DIGEST.fullmatch(str(value)) is None
            for value in absence["readbacks"].values()
        )
        or absence.get("audit")
        != {"repository": AUDIT_REPOSITORY, "head": audit_head, "tree": audit_tree}
        or absence.get("state", {}).get("repository") != STATE_REPOSITORY
        or absence.get("state", {}).get("head") != state_head
    ):
        raise ClosureError("executor-absence proof is not closed")
    if (
        set(terminal_readback)
        != {
            "schema_version",
            "kind",
            "checked_at",
            "audit",
            "state",
            "controller_variables",
            "review_branches",
            "queues",
            "recovery",
            "executors",
            "readbacks",
        }
        or terminal_readback.get("schema_version") != 1
        or terminal_readback.get("kind")
        != "historical_final_delta_terminal_live_readback"
        or TIMESTAMP.fullmatch(str(terminal_readback.get("checked_at"))) is None
        or terminal_readback.get("audit")
        != {"repository": AUDIT_REPOSITORY, "head": audit_head, "tree": audit_tree}
        or terminal_readback.get("state", {}).get("repository") != STATE_REPOSITORY
        or terminal_readback.get("state", {}).get("head") != state_head
        or terminal_readback.get("controller_variables")
        != expected_controller_variables
        or terminal_readback.get("review_branches") != expected_review_branches
        or terminal_readback.get("queues") != {"private": 0, "public": 0}
        or terminal_readback.get("recovery") != {"private": "none", "public": "none"}
        or terminal_readback.get("executors")
        != {
            "private_application_count": 0,
            "private_worker_count": 0,
            "public_running_task_count": 0,
        }
        or not isinstance(terminal_readback.get("readbacks"), dict)
        or set(terminal_readback["readbacks"])
        != {
            "cloudflare_container_applications_sha256",
            "cloudflare_worker_services_sha256",
            "github_review_refs_sha256",
            "github_variables_sha256",
        }
        or any(
            DIGEST.fullmatch(str(value)) is None
            for value in terminal_readback["readbacks"].values()
        )
    ):
        raise ClosureError("terminal live executor readback is not closed")
    if (
        COMMIT.fullmatch(state_head) is None
        or COMMIT.fullmatch(audit_head) is None
        or COMMIT.fullmatch(audit_tree) is None
        or git(state_root, "rev-parse", "HEAD") != state_head
        or git(state_root, "rev-parse", f"{state_head}^{{tree}}") == ""
        or audit_head != activation.get("audit", {}).get("head")
        or audit_tree != activation.get("audit", {}).get("tree")
    ):
        raise ClosureError("final audit or State authority differs")
    candidate = activation.get("state", {}).get("candidate_commit")
    if (
        not isinstance(candidate, str)
        or COMMIT.fullmatch(candidate) is None
        or subprocess.run(
            [
                "git",
                "-C",
                str(state_root),
                "merge-base",
                "--is-ancestor",
                candidate,
                state_head,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        != 0
    ):
        raise ClosureError(
            "activated State candidate is not an ancestor of terminal State"
        )
    try:
        subprocess.run(
            [
                sys.executable,
                str(state_root / "scripts/state.py"),
                "--root",
                str(state_root),
                "validate",
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ClosureError("terminal State validation failed") from error
    events = _load_state_events(state_root, state_head)
    enqueued: dict[str, str] = {}
    terminal: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("event_type") == "replay.enqueued":
            task_id = event.get("subject_id")
            if task_id in enqueued:
                raise ClosureError("State contains duplicate replay enqueue identity")
            enqueued[task_id] = event.get("payload", {}).get("result_id")
        elif event.get("event_type") in {"replay.accepted", "replay.failed"}:
            terminal.setdefault(event.get("subject_id"), []).append(event)
    lanes: dict[str, Any] = {}
    terminal_results: list[str] = []
    for lane in ("public", "private"):
        authority = activation.get("lanes", {}).get(lane, {})
        task_ids = authority.get("replay_task_ids")
        unavailable = authority.get("reviewed_unavailable_result_ids")
        if not isinstance(task_ids, list) or not isinstance(unavailable, list):
            raise ClosureError("activation lane inventory is invalid")
        replayed: list[str] = []
        terminal_event_ids: list[str] = []
        for task_id in task_ids:
            result_id = enqueued.get(task_id)
            candidates = [
                event
                for event in terminal.get(task_id, [])
                if event.get("event_type") == "replay.accepted"
                or (
                    event.get("event_type") == "replay.failed"
                    and event.get("payload", {}).get("retryable") is False
                )
            ]
            if (
                not isinstance(result_id, str)
                or len(candidates) != 1
                or not isinstance(candidates[0].get("event_id"), str)
            ):
                raise ClosureError(
                    "final-delta task lacks one terminal State disposition"
                )
            replayed.append(result_id)
            terminal_event_ids.append(candidates[0]["event_id"])
        if sorted(replayed) != sorted(
            set(authority["accepted_result_ids"]) - set(unavailable)
        ):
            raise ClosureError("terminal State Results differ from activation")
        results = sorted(replayed + unavailable)
        if results != authority["accepted_result_ids"]:
            raise ClosureError("terminal lane does not cover every accepted Result")
        lanes[lane] = {
            "terminal_result_count": len(results),
            "terminal_result_id_set_sha256": digest_set(results),
            "terminal_replay_event_count": len(terminal_event_ids),
            "terminal_replay_event_id_set_sha256": digest_set(terminal_event_ids),
            "reviewed_unavailable_count": len(unavailable),
        }
        terminal_results.extend(results)
    if len(terminal_results) != activation.get("accepted_result_count") or digest_set(
        terminal_results
    ) != activation.get("accepted_result_id_set_sha256"):
        raise ClosureError("terminal result set differs from accepted final delta")
    state_tree = git(state_root, "rev-parse", f"{state_head}^{{tree}}")
    if absence["state"].get("tree") != state_tree:
        raise ClosureError("executor-absence State tree differs")
    if terminal_readback["state"].get("tree") != state_tree:
        raise ClosureError("terminal live-readback State tree differs")
    return {
        "schema_version": 1,
        "kind": "historical_final_delta_terminal_binding",
        "status": "terminal_and_executors_absent",
        "activation": activation_locator,
        "executor_absence": absence_locator,
        "terminal_live_readback": terminal_readback,
        "cutoff": activation["cutoff"],
        "accepted_result_count": activation["accepted_result_count"],
        "terminal_result_count": len(terminal_results),
        "terminal_result_id_set_sha256": digest_set(terminal_results),
        "lanes": lanes,
        "audit": {
            "repository": AUDIT_REPOSITORY,
            "head": audit_head,
            "tree": audit_tree,
        },
        "state": {
            "repository": STATE_REPOSITORY,
            "head": state_head,
            "tree": state_tree,
        },
        "retirement": {
            "audit_review_refs": [
                "refs/heads/historical-final-delta-archive-rewrap-v1"
            ],
            "state_review_refs": ["refs/heads/historical-final-delta-state-v1"],
            "submissions_workflow_paths": TEMPORARY_WORKFLOWS,
        },
    }


def write_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ClosureError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(value))


def _checkout_locator(
    path: pathlib.Path, commit: str, prefix: str, label: str
) -> tuple[dict[str, Any], dict[str, str]]:
    value, raw = read_canonical(path, label)
    root = pathlib.Path(git(path.parent, "rev-parse", "--show-toplevel")).resolve()
    relative = path.resolve().relative_to(root).as_posix()
    bound = locator(commit=commit, path=relative, raw=raw, prefix=prefix)
    if git(root, "hash-object", relative) != git(
        root, "rev-parse", f"{commit}:{relative}"
    ):
        raise ClosureError(f"{label} differs from its committed blob")
    return value, bound


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    activation = commands.add_parser("activation")
    activation.add_argument("--submissions-commit", required=True)
    activation.add_argument("--preparation", required=True, type=pathlib.Path)
    activation.add_argument("--public-plan", required=True, type=pathlib.Path)
    activation.add_argument("--private-plan", required=True, type=pathlib.Path)
    activation.add_argument("--state-promotion", required=True, type=pathlib.Path)
    activation.add_argument("--state-root", required=True, type=pathlib.Path)
    activation.add_argument("--output", required=True, type=pathlib.Path)
    terminal = commands.add_parser("terminal")
    terminal.add_argument("--submissions-commit", required=True)
    terminal.add_argument("--activation", required=True, type=pathlib.Path)
    terminal.add_argument("--executor-absence", required=True, type=pathlib.Path)
    terminal.add_argument("--terminal-readback", required=True, type=pathlib.Path)
    terminal.add_argument("--state-root", required=True, type=pathlib.Path)
    terminal.add_argument("--state-head", required=True)
    terminal.add_argument("--audit-head", required=True)
    terminal.add_argument("--audit-tree", required=True)
    terminal.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if COMMIT.fullmatch(args.submissions_commit) is None:
            raise ClosureError("submissions commit is invalid")
        if args.command == "activation":
            preparation, preparation_bound = _checkout_locator(
                args.preparation.resolve(),
                args.submissions_commit,
                "evidence/historical-replay/final-delta-preparations",
                "preparation packet",
            )
            public, public_bound = _checkout_locator(
                args.public_plan.resolve(),
                args.submissions_commit,
                PUBLIC_PLAN_PREFIX,
                "public plan",
            )
            private, private_bound = _checkout_locator(
                args.private_plan.resolve(),
                args.submissions_commit,
                PRIVATE_PLAN_PREFIX,
                "private plan",
            )
            promotion, promotion_raw = read_canonical(
                args.state_promotion.resolve(), "State promotion binding"
            )
            root = pathlib.Path(
                git(
                    args.state_promotion.resolve().parent,
                    "rev-parse",
                    "--show-toplevel",
                )
            ).resolve()
            relative = args.state_promotion.resolve().relative_to(root).as_posix()
            if (
                relative
                != "configuration/historical-final-delta-state-promotion-v1.json"
                or git(root, "hash-object", relative)
                != git(root, "rev-parse", f"{args.submissions_commit}:{relative}")
            ):
                raise ClosureError(
                    "State promotion binding differs from committed blob"
                )
            promotion_bound = {
                "repository": SUBMISSIONS_REPOSITORY,
                "commit": args.submissions_commit,
                "path": relative,
                "sha256": sha256(promotion_raw),
            }
            if git(args.state_root.resolve(), "rev-parse", "HEAD") != promotion.get(
                "state", {}
            ).get("candidate_commit"):
                raise ClosureError("State checkout is not the staged candidate")
            candidate = _candidate_inventory(args.state_root.resolve(), promotion)
            value = build_activation(
                preparation=preparation,
                preparation_locator=preparation_bound,
                public_plan=public,
                public_locator=public_bound,
                private_plan=private,
                private_locator=private_bound,
                promotion=promotion,
                promotion_locator=promotion_bound,
                candidate=candidate,
            )
        else:
            activation_value, activation_bound = _checkout_locator(
                args.activation.resolve(),
                args.submissions_commit,
                ACTIVATION_PREFIX,
                "activation binding",
            )
            absence_value, absence_bound = _checkout_locator(
                args.executor_absence.resolve(),
                args.submissions_commit,
                ABSENCE_PREFIX,
                "executor-absence proof",
            )
            terminal_readback, _ = read_canonical(
                args.terminal_readback.resolve(), "terminal live executor readback"
            )
            root = pathlib.Path(
                git(
                    args.executor_absence.resolve().parent,
                    "rev-parse",
                    "--show-toplevel",
                )
            ).resolve()
            git(
                root,
                "merge-base",
                "--is-ancestor",
                absence_value.get("submissions_commit", ""),
                args.submissions_commit,
            )
            value = build_terminal(
                activation=activation_value,
                activation_locator=activation_bound,
                absence=absence_value,
                absence_locator=absence_bound,
                terminal_readback=terminal_readback,
                state_root=args.state_root.resolve(),
                state_head=args.state_head,
                audit_head=args.audit_head,
                audit_tree=args.audit_tree,
            )
        write_exclusive(args.output.resolve(), value)
    except (ClosureError, OSError, TypeError, ValueError) as error:
        print(f"historical-final-delta-closure: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
