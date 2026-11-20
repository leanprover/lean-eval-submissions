from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest

import test_historical_replay_controller as legacy
from scripts.historical_replay_controller import (
    HistoricalReplayControllerError,
    _historical_replay_states,
    _validate_public_authority_identity,
    current_historical_running,
)


ARCHIVE_AUTHORITY = "historical_archive_result.replay_authorized"


def recovered_history(*, running: bool = False) -> list[dict]:
    fixture = legacy.HistoricalReplayRecoveryTests()
    fixture.setUp()
    events = fixture.events()[:-1]
    authority, original_qualification, enqueue = events[1:]
    recovery = {
        **authority,
        "event_id": "01a035b4-d6d2-7000-8000-000000000001",
        "event_type": "historical_result.replay_reconfigured",
        "occurred_at": "2026-08-25T01:00:00.003Z",
        "causation_event_id": enqueue["event_id"],
        "payload": {
            "source_recovery_version": 2,
            "original_authority_event_id": authority["event_id"],
            "replay_task_id": enqueue["subject_id"],
            "superseded_enqueue_event_id": enqueue["event_id"],
            "superseded_qualification_event_id": original_qualification["event_id"],
            "superseded_execution_profile_digest": enqueue["payload"]["execution_profile_digest"],
            "measurement_config_digest": enqueue["payload"]["measurement_config_digest"],
            "checker": "nanoda",
            "reason_code": "accepted_source_archive_recovery",
            "source_visibility": "public",
            "candidate_execution_profile_digest": "5" * 64,
            "candidate_qualification_repository": "leanprover/lean-eval-submissions",
            "candidate_qualification_commit": "a" * 40,
            "candidate_qualification_path": "evidence/archive-replay/profiles/" + "5" * 64 + ".json",
            "candidate_qualification_sha256": "6" * 64,
        },
    }
    qualification = {
        **original_qualification,
        "event_id": "01a035b4-d6d3-7000-8000-000000000001",
        "event_type": "historical_archive_result.replay_profile_qualified",
        "occurred_at": "2026-08-25T01:00:00.004Z",
        "causation_event_id": recovery["event_id"],
        "payload": {
            **original_qualification["payload"],
            **{
                field.removeprefix("candidate_"): value
                for field, value in recovery["payload"].items()
                if field.startswith("candidate_")
            },
        },
    }
    reenqueue = {
        **enqueue,
        "event_id": "01a035b4-d6d4-7000-8000-000000000001",
        "occurred_at": "2026-08-25T01:00:00.005Z",
        "causation_event_id": recovery["event_id"],
        "payload": {**enqueue["payload"], "execution_profile_digest": "5" * 64},
    }
    events.extend([recovery, qualification, reenqueue])
    if running:
        events.append({
            **reenqueue,
            "event_id": "01a035b4-d6d5-7000-8000-000000000001",
            "event_type": "replay.started",
            "occurred_at": "2026-08-25T01:00:00.006Z",
            "causation_event_id": reenqueue["event_id"],
            "payload": {"attempt": 1, "runner_profile": "fixture"},
        })
    return events


class ArchivedHistoricalConsumerTests(unittest.TestCase):
    def test_archive_decoder_accepts_exact_recovered_public_queue(self):
        from scripts import historical_private_replay_controller as archived
        path = pathlib.Path(__file__).parent / "fixtures/historical-public-archive-recovery-queue-v2.json"
        queue = json.loads(path.read_text())
        self.assertEqual(queue, archived.validate_queue(queue))
        for marker in (True, 1, 3, "2"):
            wrong = copy.deepcopy(queue)
            wrong["tasks"][0]["source_recovery_version"] = marker
            with self.subTest(marker=marker), self.assertRaises(archived.HistoricalPrivateReplayControllerError):
                archived.validate_queue(wrong)
        for field, value in (
            ("source_visibility", "private"),
            ("fetch_method", "git"),
            ("archive_capacity_class", "archive_single_blob_30m"),
            ("archive_ciphertext_size", 12 * 1024 * 1024),
            ("archive_benchmark_relation", "unknown"),
        ):
            wrong = copy.deepcopy(queue)
            wrong["tasks"][0][field] = value
            with self.subTest(field=field), self.assertRaises(archived.HistoricalPrivateReplayControllerError):
                archived.validate_queue(wrong)
        wrong = copy.deepcopy(queue)
        wrong["schema_version"] = 1
        with self.assertRaises(archived.HistoricalPrivateReplayControllerError):
            archived.validate_queue(wrong)
        wrong = copy.deepcopy(queue)
        wrong["tasks"][0]["archive_authority_version"] = 2
        with self.assertRaisesRegex(archived.HistoricalPrivateReplayControllerError, "standalone"):
            archived.validate_queue(wrong)

    def test_original_public_identity_is_independent_of_replacement_profile(self):
        fixture = legacy.Fixture()
        task = copy.deepcopy(fixture.task)
        task["qualification_path"] = "evidence/archive-replay/profiles/" + "f" * 64 + ".json"
        task["qualification_sha256"] = "e" * 64
        task["execution_profile_digest"] = "f" * 64
        _validate_public_authority_identity(task, fixture.authority, fixture.authority_raw)
        for field, value in (
            ("source_commit", "e" * 40),
            ("benchmark_commit", "e" * 40),
            ("result_tree_digest", "e" * 64),
            ("source_visibility", "private"),
        ):
            wrong = copy.deepcopy(task)
            wrong[field] = value
            with self.subTest(field=field), self.assertRaises(HistoricalReplayControllerError):
                _validate_public_authority_identity(wrong, fixture.authority, fixture.authority_raw)

    def test_state_contract_fixture_keeps_one_original_task(self):
        # Synthetic fixture independently validated by the prepared State v2
        # schema and semantic reducer; no private source or live authority.
        path = pathlib.Path(__file__).parent / "fixtures/historical-public-archive-recovery-v2.json"
        events = json.loads(path.read_text())
        authorities = {
            event["subject_id"] for event in events
            if event["event_type"] == "historical_result.replay_authorized"
        }
        self.assertEqual({}, _historical_replay_states(events, authorities))
        archive = _historical_replay_states(events, authorities, authority_event_type=ARCHIVE_AUTHORITY)
        self.assertEqual(1, len(archive))
        task_id, state = next(iter(archive.items()))
        self.assertEqual(events[-1]["subject_id"], task_id)
        self.assertEqual(("queued", 0), (state["status"], state["attempt"]))

    def test_recovered_task_moves_to_archive_without_changing_identity_or_attempt(self):
        events = recovered_history()
        authorities = {events[1]["subject_id"]}
        self.assertEqual({}, _historical_replay_states(events, authorities))
        archive = _historical_replay_states(events, authorities, authority_event_type=ARCHIVE_AUTHORITY)
        self.assertEqual([events[3]["subject_id"]], list(archive))
        self.assertEqual(("queued", 0), (next(iter(archive.values()))["status"], next(iter(archive.values()))["attempt"]))

    def test_running_recovery_is_visible_to_archive_cleanup_only(self):
        events = recovered_history(running=True)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            legacy.HistoricalReplayRecoveryTests.write_events(root, events)
            self.assertEqual([], current_historical_running(root, state_validated=True))
            running = current_historical_running(root, state_validated=True, authority_event_type=ARCHIVE_AUTHORITY)
        self.assertEqual(1, len(running))
        self.assertEqual(events[3]["subject_id"], running[0]["replay_task_id"])
        self.assertEqual(1, running[0]["attempt"])
        self.assertEqual(events[-1], running[0]["event"])

    def test_recovery_requires_exact_qualification_and_reenqueue_bindings(self):
        mutations = (
            lambda events: events.pop(5),
            lambda events: events[5]["payload"].__setitem__("qualification_sha256", "f" * 64),
            lambda events: events[5]["payload"].__setitem__("toolchain", "leanprover/lean4:v4.32.1"),
            lambda events: events[6]["payload"].__setitem__("measurement_config_digest", "f" * 64),
            lambda events: events[4]["payload"].__setitem__("superseded_qualification_event_id", events[1]["event_id"]),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                events = recovered_history()
                mutate(events)
                with self.assertRaises(HistoricalReplayControllerError):
                    _historical_replay_states(events, {events[1]["subject_id"]}, authority_event_type=ARCHIVE_AUTHORITY)

    def test_recovery_marker_is_closed_and_cannot_follow_an_execution(self):
        for marker in (True, 1, 3, "2"):
            events = recovered_history()
            events[4]["payload"]["source_recovery_version"] = marker
            with self.subTest(marker=marker), self.assertRaises(HistoricalReplayControllerError):
                _historical_replay_states(events, {events[1]["subject_id"]}, authority_event_type=ARCHIVE_AUTHORITY)
        events = recovered_history()
        events[4]["causation_event_id"] = events[1]["event_id"]
        with self.assertRaises(HistoricalReplayControllerError):
            _historical_replay_states(events, {events[1]["subject_id"]}, authority_event_type=ARCHIVE_AUTHORITY)

    def test_unused_later_qualification_does_not_replace_enqueue_binding(self):
        events = json.loads((pathlib.Path(__file__).parent / "fixtures/historical-public-archive-recovery-v2.json").read_text())
        unused = copy.deepcopy(events[2])
        unused.update(event_id="0198abcd-0000-7000-8000-000000000017", occurred_at="2026-10-20T06:08:23.000Z")
        unused["payload"]["execution_profile_digest"] = "f" * 64
        unused["payload"]["qualification_path"] = "evidence/public-replay/profiles/" + "f" * 64 + ".json"
        events.insert(4, unused)
        task_id = events[-1]["subject_id"]
        states = _historical_replay_states(events, {events[1]["subject_id"]}, authority_event_type=ARCHIVE_AUTHORITY)
        self.assertEqual(("queued", 0), (states[task_id]["status"], states[task_id]["attempt"]))
        events[5]["payload"]["superseded_qualification_event_id"] = unused["event_id"]
        with self.assertRaises(HistoricalReplayControllerError):
            _historical_replay_states(events, {events[1]["subject_id"]}, authority_event_type=ARCHIVE_AUTHORITY)

    def test_recovered_source_cannot_use_an_old_profile_withdrawal(self):
        events = recovered_history()
        withdrawn = copy.deepcopy(events[4])
        withdrawn.update(event_id="01a035b4-d6d6-7000-8000-000000000001", occurred_at="2026-08-25T01:00:00.007Z")
        withdrawn["payload"].pop("source_recovery_version")
        withdrawn["payload"]["reason_code"] = "profile_replacement_withdrawn"
        events.append(withdrawn)
        with self.assertRaisesRegex(HistoricalReplayControllerError, "cannot be withdrawn"):
            _historical_replay_states(events, {events[1]["subject_id"]}, authority_event_type=ARCHIVE_AUTHORITY)
