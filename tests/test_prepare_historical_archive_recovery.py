from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest

from test_historical_archive_evidence import evidence
from scripts import prepare_historical_archive_recovery as prepare
from scripts.historical_replay_controller import _historical_replay_states, canonical_bytes

HistoricalReplayControllerError = prepare.public.HistoricalReplayControllerError

ROOT = pathlib.Path(__file__).parent / "fixtures"


class RecoveryPreparationTests(unittest.TestCase):
    def test_prospective_artifacts_reproduce_closed_validated_evidence(self):
        task, crosswalk, manifest = evidence()
        self.assertEqual(crosswalk, prepare.prepare_crosswalk([task], crosswalk["results_store_sha256"], crosswalk["migration_plan"]))
        self.assertEqual(manifest, prepare.prepare_manifest([task]))
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "candidate.json"
            prepare.public._write(path, manifest)
            self.assertEqual(canonical_bytes(manifest), path.read_bytes())
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            with self.assertRaises(HistoricalReplayControllerError):
                prepare.public._write(path, manifest)

    def test_events_preserve_queued_identity_and_use_strict_ordered_closure(self):
        events = json.loads((ROOT / "historical-public-archive-recovery-v2.json").read_text())
        queue = json.loads((ROOT / "historical-public-archive-recovery-queue-v2.json").read_text())
        task = queue["tasks"][0]
        original = copy.deepcopy(task)
        original.update(event_id=task["superseded_enqueue_event_id"], execution_profile_digest=task["superseded_execution_profile_digest"], qualification_event_id=task["superseded_qualification_event_id"])
        output = prepare.recovery_events({task["replay_task_id"]: original}, [task], "2026-11-20T04:00:00.000Z")
        self.assertEqual(["historical_result.replay_reconfigured", "historical_archive_result.replay_profile_qualified", "replay.enqueued"], [event["event_type"] for event in output])
        self.assertEqual([event["payload"] for event in events[-3:]], [event["payload"] for event in output])
        self.assertLess(output[0]["event_id"], output[1]["event_id"])
        self.assertLess(output[1]["event_id"], output[2]["event_id"])
        states = _historical_replay_states(events[:-3] + output, {task["result_id"]}, authority_event_type="historical_archive_result.replay_authorized")
        self.assertEqual(("queued", 0), (states[task["replay_task_id"]]["status"], states[task["replay_task_id"]]["attempt"]))
        for field, value in (("attempt", 1), ("status", "failed"), ("source_commit", "e" * 40), ("event_id", "0198abcd-0000-7000-8000-000000000099"), ("execution_profile_digest", "f" * 64)):
            wrong = copy.deepcopy(original)
            wrong[field] = value
            with self.subTest(field=field), self.assertRaises(HistoricalReplayControllerError):
                prepare.recovery_events({task["replay_task_id"]: wrong}, [task], "2026-11-20T04:00:00.000Z")

    def test_artifact_selection_cannot_hide_duplicate_results_or_nonqueued_tasks(self):
        queue = json.loads((ROOT / "historical-public-archive-recovery-queue-v2.json").read_text())
        self.assertEqual(1, len(prepare.proposed_tasks(queue)))
        for mutate in (
            lambda q: q["tasks"].append(copy.deepcopy(q["tasks"][0])),
            lambda q: q["tasks"][0].__setitem__("attempt", 1),
            lambda q: q["tasks"].clear(),
        ):
            wrong = copy.deepcopy(queue)
            mutate(wrong)
            with self.assertRaises(HistoricalReplayControllerError):
                prepare.proposed_tasks(wrong)
