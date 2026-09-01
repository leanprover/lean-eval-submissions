from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from prepare_historical_baseline_state_batch import (  # noqa: E402
    BaselineBatchError,
    canonical,
    lane_inventory,
    load_event_tree,
    load_expectation,
    validate_combined,
)


def event_id(number: int) -> str:
    return f"01900000-0000-7000-8000-{number:012x}"


def task_id(number: int) -> str:
    return f"rt1_{number:064x}"


def result_id(number: int) -> str:
    return f"r2_{number:064x}"


def expectation() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "historical_baseline_state_batch_expectation",
        "environment": "production",
        "lanes": {
            "public": {
                "authority_event_type": "historical_result.replay_authorized",
                "qualification_event_type": "historical_result.replay_profile_qualified",
                "enqueue_event_type": "replay.enqueued",
                "event_count": 3,
                "task_count": 1,
            },
            "private": {
                "authority_event_type": "historical_archive_result.replay_authorized",
                "qualification_event_type": "historical_archive_result.replay_profile_qualified",
                "enqueue_event_type": "replay.enqueued",
                "event_count": 3,
                "task_count": 1,
            },
        },
        "total_event_count": 6,
        "total_task_count": 2,
    }


def lane_events(name: str, start: int, result_number: int) -> list[dict[str, object]]:
    prefix = "historical_result" if name == "public" else "historical_archive_result"
    result = result_id(result_number)
    task = task_id(result_number)
    types = (
        f"{prefix}.replay_authorized",
        f"{prefix}.replay_profile_qualified",
        "replay.enqueued",
    )
    events = []
    cause = None
    for offset, kind in enumerate(types):
        identity = event_id(start + offset)
        events.append(
            {
                "schema_version": 1,
                "event_id": identity,
                "event_type": kind,
                "occurred_at": f"2026-08-28T00:00:00.{start + offset:03d}Z",
                "subject_id": task if kind == "replay.enqueued" else result,
                "causation_event_id": cause,
                "actor": {"kind": "system"},
                "payload": {"result_id": result, "lane": name},
            }
        )
        cause = identity
    return events


class HistoricalBaselineBatchTests(unittest.TestCase):
    def test_committed_counts_are_packet_inputs(self) -> None:
        value = load_expectation(
            ROOT / "configuration/historical-baseline-state-batch-v1.json"
        )
        self.assertEqual(value["lanes"]["public"]["event_count"], 522)
        self.assertEqual(value["lanes"]["public"]["task_count"], 174)
        self.assertEqual(value["lanes"]["private"]["event_count"], 1917)
        self.assertEqual(value["lanes"]["private"]["task_count"], 639)
        self.assertEqual(value["total_event_count"], 2439)
        self.assertEqual(value["total_task_count"], 813)

    def test_lane_inventory_binds_every_event_task_and_result(self) -> None:
        events = lane_events("public", 1, 1)
        inventory = lane_inventory("public", events, expectation())
        self.assertEqual(inventory["event_ids"], sorted(event["event_id"] for event in events))
        self.assertEqual(inventory["task_ids"], [task_id(1)])
        self.assertEqual(inventory["result_ids"], [result_id(1)])
        self.assertEqual(len(inventory["event_files"]), 3)

    def test_lane_inventory_rejects_duplicate_and_extra_events(self) -> None:
        events = lane_events("public", 1, 1)
        events[1]["event_id"] = events[0]["event_id"]
        with self.assertRaisesRegex(BaselineBatchError, "count or identity"):
            lane_inventory("public", events, expectation())
        events = lane_events("public", 1, 1)
        events[0]["event_type"] = "replay.started"
        with self.assertRaisesRegex(BaselineBatchError, "unexpected event type"):
            lane_inventory("public", events, expectation())

    def test_event_tree_rejects_missing_or_extra_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            events = lane_events("public", 1, 1)
            for event in events:
                identity = event["event_id"]
                target = root / "events" / identity.replace("-", "")[:2] / f"{identity}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(canonical(event))
            self.assertEqual(len(load_event_tree(root, "public")), 3)
            (root / "events" / "unexpected.txt").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(BaselineBatchError, "inventory"):
                load_event_tree(root, "public")

    def make_state(self, root: pathlib.Path) -> None:
        scripts = root / "scripts"
        scripts.mkdir()
        (root / "events" / "01").mkdir(parents=True)
        existing = {
            "schema_version": 1,
            "event_id": event_id(0),
            "event_type": "baseline",
            "occurred_at": "2026-08-28T00:00:00.000Z",
            "subject_id": "baseline",
            "causation_event_id": None,
            "actor": {"kind": "system"},
            "payload": {},
        }
        (root / "events" / "01" / f"{event_id(0)}.json").write_bytes(canonical(existing))
        (root / "state.json").write_bytes(canonical({"environment": "production", "schema_version": 1}))
        (scripts / "validate_state.py").write_text(
            """import json, pathlib
def load_tree(root):
    events=[json.loads(p.read_text()) for p in sorted(pathlib.Path(root,'events').glob('*/*.json'))]
    return 'production',events
def validate_event_data(event,label):
    if 'event_id' not in event: raise ValueError(label)
def validate_semantics(events,environment):
    ids=[e['event_id'] for e in events]
    if len(ids)!=len(set(ids)): raise ValueError('duplicate')
""",
            encoding="utf-8",
        )
        (scripts / "materialize_state.py").write_text(
            """def materialize(environment,events):
    public=[]; private=[]
    for event in events:
      if event['event_type']=='replay.enqueued':
        task={'replay_task_id':event['subject_id']}
        (public if event['payload']['lane']=='public' else private).append(task)
    base={'schema_version':1,'environment':environment,'source_event_count':len(events),'source_digest':'0'*64}
    return {'historical-public-replay-queue.json':{**base,'tasks':public},'historical-private-replay-queue.json':{**base,'tasks':private}}
""",
            encoding="utf-8",
        )
        (scripts / "public_projection.py").write_text(
            """def project_public_state_v6(environment,events,commit):
    series=[{'replay_task_id':e['subject_id']} for e in events if e['event_type']=='replay.enqueued']
    return {'schema_version':6,'environment':environment,'source_state_commit':commit,'historical_replay_series':series,'historical_replay_unavailability':[]}
""",
            encoding="utf-8",
        )

    def test_combined_validation_binds_both_queues_and_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory)
            self.make_state(state)
            public = lane_events("public", 1, 1)
            private = lane_events("private", 4, 2)
            inventories = {
                "public": lane_inventory("public", public, expectation()),
                "private": lane_inventory("private", private, expectation()),
            }
            result = validate_combined(state, "a" * 40, public, private, inventories)
            self.assertEqual(result["combined_event_count"], 7)
            self.assertEqual(result["queues"]["public"]["task_ids"], [task_id(1)])
            self.assertEqual(result["queues"]["private"]["task_ids"], [task_id(2)])
            self.assertRegex(
                result["redacted_historical_projection_sha256"], r"^[0-9a-f]{64}$"
            )

    def test_combined_validation_rejects_cross_lane_task_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory)
            self.make_state(state)
            public = lane_events("public", 1, 1)
            private = lane_events("private", 4, 2)
            inventories = {
                "public": lane_inventory("public", public, expectation()),
                "private": lane_inventory("private", private, expectation()),
            }
            inventories["private"] = copy.deepcopy(inventories["private"])
            inventories["private"]["task_ids"] = inventories["public"]["task_ids"]
            with self.assertRaisesRegex(BaselineBatchError, "overlap"):
                validate_combined(state, "a" * 40, public, private, inventories)


if __name__ == "__main__":
    unittest.main()
