#!/usr/bin/env python3
"""Close and validate the two retained-baseline State candidate lanes.

This one-shot preparation helper does not generate a new event model and does
not write State.  It consumes the event trees emitted by the existing public
and private historical finalizers, validates them together with the exact
current production State checkout, and emits one closed append candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from typing import Any, Iterator


COMMIT = re.compile(r"[0-9a-f]{40}\Z")
UUID7 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
REPLAY_TASK_ID = re.compile(r"rt1_[0-9a-f]{64}\Z")
EVENT_PATH = re.compile(r"events/[0-9a-f]{2}/[0-9a-f-]{36}\.json\Z")
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 10_000
EXPECTED_REMOTE = "https://github.com/leanprover/lean-eval-state.git"


class BaselineBatchError(ValueError):
    """A candidate or its exact State binding is unsafe."""


def canonical(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise BaselineBatchError("value is not canonical JSON") from error


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _regular_bytes(path: pathlib.Path, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= MAX_JSON_BYTES:
                raise BaselineBatchError(f"{label} is not one bounded regular file")
            raw = stream.read(MAX_JSON_BYTES + 1)
            if len(raw) != metadata.st_size:
                raise BaselineBatchError(f"{label} changed while being read")
            return raw
    except BaselineBatchError:
        raise
    except OSError as error:
        raise BaselineBatchError(f"{label} is unavailable") from error


def load_canonical(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _regular_bytes(path, label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BaselineBatchError(f"{label} is not JSON") from error
    if not isinstance(value, dict) or canonical(value) != raw:
        raise BaselineBatchError(f"{label} is not canonical JSON")
    return value, raw


def _closed(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise BaselineBatchError(f"{label} fields are not closed")
    return value


def load_expectation(path: pathlib.Path) -> dict[str, Any]:
    value, _ = load_canonical(path, "batch expectation")
    _closed(
        value,
        {"schema_version", "kind", "environment", "lanes", "total_event_count", "total_task_count"},
        "batch expectation",
    )
    if (
        value["schema_version"] != 1
        or value["kind"] != "historical_baseline_state_batch_expectation"
        or value["environment"] != "production"
    ):
        raise BaselineBatchError("batch expectation identity is invalid")
    lanes = _closed(value["lanes"], {"public", "private"}, "batch lanes")
    lane_fields = {
        "authority_event_type",
        "qualification_event_type",
        "enqueue_event_type",
        "event_count",
        "task_count",
    }
    for name in ("public", "private"):
        lane = _closed(lanes[name], lane_fields, f"{name} lane")
        if (
            any(
                not isinstance(lane[field], str) or not lane[field]
                for field in lane_fields - {"event_count", "task_count"}
            )
            or type(lane["event_count"]) is not int
            or type(lane["task_count"]) is not int
            or lane["task_count"] < 1
            or lane["event_count"] != 3 * lane["task_count"]
            or lane["event_count"] > MAX_EVENTS
        ):
            raise BaselineBatchError(f"{name} lane expectation is invalid")
    if (
        type(value["total_event_count"]) is not int
        or type(value["total_task_count"]) is not int
        or value["total_event_count"] != sum(lanes[name]["event_count"] for name in lanes)
        or value["total_task_count"] != sum(lanes[name]["task_count"] for name in lanes)
    ):
        raise BaselineBatchError("batch totals do not equal the closed lane totals")
    return value


def _git(root: pathlib.Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise BaselineBatchError("exact State checkout proof failed") from error


def verify_state_checkout(root: pathlib.Path, expected_head: str) -> None:
    if COMMIT.fullmatch(expected_head) is None:
        raise BaselineBatchError("expected State head is invalid")
    if pathlib.Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise BaselineBatchError("State root is not the checkout root")
    if _git(root, "rev-parse", "HEAD") != expected_head:
        raise BaselineBatchError("State checkout is not at the expected head")
    if _git(root, "status", "--porcelain"):
        raise BaselineBatchError("State checkout is not clean")
    if _git(root, "remote", "get-url", "origin") != EXPECTED_REMOTE:
        raise BaselineBatchError("State checkout remote is not canonical")


def load_event_tree(root: pathlib.Path, label: str) -> list[dict[str, Any]]:
    events_root = root / "events"
    if events_root.is_symlink() or not events_root.is_dir():
        raise BaselineBatchError(f"{label} event root is unavailable")
    paths = sorted(path for path in events_root.glob("*/*.json"))
    selected = {path.relative_to(root).as_posix() for path in paths}
    unexpected = sorted(
        path.relative_to(root).as_posix()
        for path in events_root.rglob("*")
        if path.is_symlink()
        or (path.is_file() and path.relative_to(root).as_posix() not in selected)
        or (
            path.is_dir()
            and path.parent != events_root
        )
    )
    if unexpected or not paths or len(paths) > MAX_EVENTS:
        raise BaselineBatchError(f"{label} event inventory is invalid")
    events: list[dict[str, Any]] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if EVENT_PATH.fullmatch(relative) is None:
            raise BaselineBatchError(f"{label} event path is not canonical")
        event, _ = load_canonical(path, f"{label} event")
        event_id = event.get("event_id")
        if (
            not isinstance(event_id, str)
            or UUID7.fullmatch(event_id) is None
            or relative != f"events/{event_id.replace('-', '')[:2]}/{event_id}.json"
        ):
            raise BaselineBatchError(f"{label} event identity and path differ")
        events.append(event)
    return events


def lane_inventory(
    name: str,
    events: list[dict[str, Any]],
    expectation: dict[str, Any],
) -> dict[str, Any]:
    lane = expectation["lanes"][name]
    expected_types = {
        lane["authority_event_type"],
        lane["qualification_event_type"],
        lane["enqueue_event_type"],
    }
    type_counts = {event_type: 0 for event_type in expected_types}
    event_ids: list[str] = []
    task_ids: list[str] = []
    result_ids: list[str] = []
    descriptors: list[dict[str, str]] = []
    for event in events:
        event_type = event.get("event_type")
        if event_type not in expected_types:
            raise BaselineBatchError(f"{name} lane contains an unexpected event type")
        type_counts[event_type] += 1
        event_id = event["event_id"]
        event_ids.append(event_id)
        relative = f"events/{event_id.replace('-', '')[:2]}/{event_id}.json"
        descriptors.append({"path": relative, "sha256": sha256(canonical(event))})
        if event_type == lane["enqueue_event_type"]:
            task_id = event.get("subject_id")
            payload = event.get("payload")
            if (
                not isinstance(task_id, str)
                or REPLAY_TASK_ID.fullmatch(task_id) is None
                or not isinstance(payload, dict)
                or not isinstance(payload.get("result_id"), str)
            ):
                raise BaselineBatchError(f"{name} lane contains an invalid replay task")
            task_ids.append(task_id)
            result_ids.append(payload["result_id"])
    if (
        len(events) != lane["event_count"]
        or any(count != lane["task_count"] for count in type_counts.values())
        or len(task_ids) != lane["task_count"]
        or len(set(event_ids)) != len(event_ids)
        or len(set(task_ids)) != len(task_ids)
        or len(set(result_ids)) != len(result_ids)
    ):
        raise BaselineBatchError(f"{name} lane count or identity set changed")
    descriptors.sort(key=lambda item: item["path"])
    return {
        "event_count": len(event_ids),
        "task_count": len(task_ids),
        "event_ids": sorted(event_ids),
        "task_ids": sorted(task_ids),
        "result_ids": sorted(result_ids),
        "event_files": descriptors,
        "event_set_sha256": sha256(canonical(descriptors)),
        "event_id_set_sha256": sha256(canonical(sorted(event_ids))),
        "task_id_set_sha256": sha256(canonical(sorted(task_ids))),
        "result_id_set_sha256": sha256(canonical(sorted(result_ids))),
    }


@contextmanager
def state_modules(state_root: pathlib.Path) -> Iterator[tuple[Any, Any, Any]]:
    scripts_root = state_root / "scripts"
    module_names = tuple(path.stem for path in scripts_root.glob("*.py"))
    if not module_names:
        raise BaselineBatchError("State script inventory is empty")
    saved = {name: sys.modules.get(name) for name in module_names}
    for name in module_names:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(scripts_root))
    try:
        yield (
            importlib.import_module("validate_state"),
            importlib.import_module("materialize_state"),
            importlib.import_module("public_projection"),
        )
    except BaselineBatchError:
        raise
    except Exception as error:
        raise BaselineBatchError("exact State implementation cannot be loaded") from error
    finally:
        sys.path.remove(str(scripts_root))
        for name in module_names:
            sys.modules.pop(name, None)
            if saved[name] is not None:
                sys.modules[name] = saved[name]


def _queue_task_ids(queue: Any, label: str) -> list[str]:
    if not isinstance(queue, dict) or not isinstance(queue.get("tasks"), list):
        raise BaselineBatchError(f"{label} is invalid")
    task_ids = [task.get("replay_task_id") for task in queue["tasks"] if isinstance(task, dict)]
    if len(task_ids) != len(queue["tasks"]) or any(
        not isinstance(task_id, str) or REPLAY_TASK_ID.fullmatch(task_id) is None
        for task_id in task_ids
    ):
        raise BaselineBatchError(f"{label} task identities are invalid")
    return sorted(task_ids)


def validate_combined(
    state_root: pathlib.Path,
    state_head: str,
    public_events: list[dict[str, Any]],
    private_events: list[dict[str, Any]],
    inventories: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    with state_modules(state_root) as (validator, materializer, projection):
        try:
            environment, existing = validator.load_tree(state_root)
            if environment != "production":
                raise BaselineBatchError("State environment is not production")
            validator.validate_semantics(existing, environment)
            base_views = materializer.materialize(environment, existing)
            for queue_name in (
                "historical-public-replay-queue.json",
                "historical-private-replay-queue.json",
            ):
                if _queue_task_ids(base_views[queue_name], f"base {queue_name}"):
                    raise BaselineBatchError("a retained-baseline queue is already nonempty")
            candidates = [*public_events, *private_events]
            for index, event in enumerate(candidates):
                validator.validate_event_data(event, f"candidate[{index}]")
            existing_ids = {event["event_id"] for event in existing}
            candidate_ids = [event["event_id"] for event in candidates]
            if (
                len(set(candidate_ids)) != len(candidate_ids)
                or existing_ids.intersection(candidate_ids)
                or set(inventories["public"]["task_ids"]).intersection(
                    inventories["private"]["task_ids"]
                )
                or set(inventories["public"]["result_ids"]).intersection(
                    inventories["private"]["result_ids"]
                )
            ):
                raise BaselineBatchError("combined candidate identities overlap")
            latest = max((event["occurred_at"], event["event_id"]) for event in existing)
            ordered_candidates = sorted(
                candidates, key=lambda event: (event["occurred_at"], event["event_id"])
            )
            if ordered_candidates != candidates or (
                ordered_candidates[0]["occurred_at"], ordered_candidates[0]["event_id"]
            ) <= latest:
                raise BaselineBatchError("candidate ordering does not follow current State")
            combined = [*existing, *candidates]
            validator.validate_semantics(combined, environment)
            views = materializer.materialize(environment, combined)
            queue_names = {
                "public": "historical-public-replay-queue.json",
                "private": "historical-private-replay-queue.json",
            }
            queues: dict[str, dict[str, Any]] = {}
            for lane, queue_name in queue_names.items():
                queue = views[queue_name]
                if _queue_task_ids(queue, queue_name) != inventories[lane]["task_ids"]:
                    raise BaselineBatchError(f"{lane} materialized queue differs from candidates")
                queues[lane] = {
                    "path": queue_name,
                    "sha256": sha256(canonical(queue)),
                    "task_ids": inventories[lane]["task_ids"],
                    "task_id_set_sha256": inventories[lane]["task_id_set_sha256"],
                }
            public = projection.project_public_state_v6(environment, combined, state_head)
            historical_series = public["historical_replay_series"]
            projected_task_ids = sorted(item["replay_task_id"] for item in historical_series)
            all_task_ids = sorted(
                [*inventories["public"]["task_ids"], *inventories["private"]["task_ids"]]
            )
            if projected_task_ids != all_task_ids:
                raise BaselineBatchError("redacted projection does not contain the exact task set")
            redacted_historical_projection = {
                "historical_replay_series": historical_series,
                "historical_replay_unavailability": public[
                    "historical_replay_unavailability"
                ],
            }
            view_descriptors = [
                {"path": path, "sha256": sha256(canonical(value))}
                for path, value in sorted(views.items())
            ]
            return {
                "base_event_count": len(existing),
                "base_event_id_set_sha256": sha256(
                    canonical(sorted(event["event_id"] for event in existing))
                ),
                "combined_event_count": len(combined),
                "materialized_views_sha256": sha256(canonical(view_descriptors)),
                "queues": queues,
                "redacted_historical_projection_sha256": sha256(
                    canonical(redacted_historical_projection)
                ),
                "redacted_historical_series_sha256": sha256(canonical(historical_series)),
            }
        except BaselineBatchError:
            raise
        except Exception as error:
            raise BaselineBatchError("combined candidate fails exact State validation") from error


def write_exclusive(path: pathlib.Path, raw: bytes) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise BaselineBatchError("candidate output parent is unsafe")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
    except FileExistsError as error:
        raise BaselineBatchError("refusing to overwrite candidate output") from error


def prepare(args: argparse.Namespace) -> None:
    state_root = args.state_root.resolve()
    verify_state_checkout(state_root, args.state_head)
    expectation = load_expectation(args.expectation.resolve())
    lane_roots = {
        "public": args.public_candidate_root.resolve(),
        "private": args.private_candidate_root.resolve(),
    }
    events = {name: load_event_tree(root, name) for name, root in lane_roots.items()}
    inventories = {
        name: lane_inventory(name, events[name], expectation) for name in lane_roots
    }
    if (
        sum(item["event_count"] for item in inventories.values())
        != expectation["total_event_count"]
        or sum(item["task_count"] for item in inventories.values())
        != expectation["total_task_count"]
    ):
        raise BaselineBatchError("combined candidate totals changed")
    projection = validate_combined(
        state_root,
        args.state_head,
        events["public"],
        events["private"],
        inventories,
    )
    output = args.output_directory.resolve()
    if output.exists() or output.parent.is_symlink() or not output.parent.is_dir():
        raise BaselineBatchError("candidate output directory is unsafe or already exists")
    output.mkdir(mode=0o700)
    for name in ("public", "private"):
        for descriptor in inventories[name]["event_files"]:
            source = lane_roots[name] / descriptor["path"]
            target = output / descriptor["path"]
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            raw = _regular_bytes(source, f"{name} candidate event")
            if sha256(raw) != descriptor["sha256"]:
                raise BaselineBatchError(f"{name} candidate changed after validation")
            write_exclusive(target, raw)
    expectation_raw = _regular_bytes(args.expectation.resolve(), "batch expectation")
    event_files = sorted(
        [*inventories["public"]["event_files"], *inventories["private"]["event_files"]],
        key=lambda item: item["path"],
    )
    manifest = {
        "schema_version": 1,
        "kind": "historical_baseline_state_append_candidate",
        "activation_status": "reviewed_but_not_appended",
        "state": {
            "repository": "leanprover/lean-eval-state",
            "expected_head": args.state_head,
            "expected_tree": _git(state_root, "rev-parse", f"{args.state_head}^{{tree}}"),
            **projection,
        },
        "expectation": {
            "path": args.expectation.name,
            "sha256": sha256(expectation_raw),
            "total_event_count": expectation["total_event_count"],
            "total_task_count": expectation["total_task_count"],
        },
        "lanes": inventories,
        "event_files": event_files,
        "event_set_sha256": sha256(canonical(event_files)),
    }
    write_exclusive(output / "historical-baseline-state-append-candidate.json", canonical(manifest))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state-root", required=True, type=pathlib.Path)
    result.add_argument("--state-head", required=True)
    result.add_argument("--expectation", required=True, type=pathlib.Path)
    result.add_argument("--public-candidate-root", required=True, type=pathlib.Path)
    result.add_argument("--private-candidate-root", required=True, type=pathlib.Path)
    result.add_argument("--output-directory", required=True, type=pathlib.Path)
    return result


def main() -> int:
    try:
        prepare(parser().parse_args())
        return 0
    except BaselineBatchError as error:
        print(f"historical-baseline-state-batch: {error}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError):
        print("historical-baseline-state-batch: validation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
