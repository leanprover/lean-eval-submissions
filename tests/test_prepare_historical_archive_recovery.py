from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from test_historical_archive_evidence import evidence
from scripts import prepare_historical_archive_recovery as prepare
from scripts.results_schema import result_id
from scripts.replay_orchestrator import replay_task_id
from scripts.historical_archive_recovery import expected_crosswalk_entry, crosswalk_entry_sha256, recovery_entry_sha256
from scripts.historical_replay_controller import sha256_bytes, state_canonical_bytes
from scripts.historical_replay_controller import _historical_replay_states, canonical_bytes

HistoricalReplayControllerError = prepare.public.HistoricalReplayControllerError

ROOT = pathlib.Path(__file__).parent / "fixtures"


def five_task_fixture():
    base = json.loads((ROOT / "historical-public-archive-recovery-v2.json").read_text())
    sample = json.loads((ROOT / "historical-public-archive-recovery-queue-v2.json").read_text())["tasks"][0]
    history = [base[0]]
    originals, tasks = {}, []
    for index in range(5):
        task = copy.deepcopy(sample)
        task["problem_id"] = "synthetic_problem_" + str(index)
        task["result_id"] = result_id(task["owner_login"], task["declared_model"], task["problem_id"], task["statement_revision"])
        task["replay_task_id"] = replay_task_id(task["result_id"], task["measurement_config_digest"])
        prefix = copy.deepcopy(base[1:4])
        for offset, event in enumerate(prefix):
            event["event_id"] = f"0198abcd-0000-7000-8000-{100 + index * 3 + offset:012x}"
            event["occurred_at"] = f"2026-10-20T06:08:{20 + index * 3 + offset:02d}.000Z"
        authority, qualification, enqueue = prefix
        authority["subject_id"] = qualification["subject_id"] = task["result_id"]
        authority["payload"]["problem_id"] = task["problem_id"]
        qualification["causation_event_id"] = authority["event_id"]
        enqueue.update(subject_id=task["replay_task_id"], causation_event_id=qualification["event_id"])
        enqueue["payload"]["result_id"] = task["result_id"]
        task.update(authority_event_id=authority["event_id"], superseded_enqueue_event_id=enqueue["event_id"], superseded_qualification_event_id=qualification["event_id"])
        original = copy.deepcopy(task)
        original.update(event_id=enqueue["event_id"], occurred_at=enqueue["occurred_at"], execution_profile_digest=task["superseded_execution_profile_digest"], qualification_event_id=qualification["event_id"])
        history.extend(prefix)
        originals[task["replay_task_id"]] = original
        tasks.append(task)
    tasks.sort(key=lambda task: task["replay_task_id"])
    _, crosswalk, _ = evidence()
    crosswalk = prepare.prepare_crosswalk(tasks, crosswalk["results_store_sha256"], crosswalk["migration_plan"])
    digest = sha256_bytes(canonical_bytes(crosswalk))
    for task in tasks:
        task.update(crosswalk_sha256=digest, crosswalk_path="evidence/historical-replay/archive-crosswalks/" + digest + ".json", crosswalk_entry_sha256=crosswalk_entry_sha256(expected_crosswalk_entry(task, 1)))
    manifest = prepare.prepare_manifest(tasks)
    digest = sha256_bytes(canonical_bytes(manifest))
    for task, entry in zip(tasks, manifest["entries"]):
        task.update(reconfiguration_sha256=digest, reconfiguration_path="evidence/historical-replay/source-recoveries/" + digest + ".json", reconfiguration_entry_sha256=recovery_entry_sha256(entry))
    return history, originals, tasks


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

    def test_five_task_batch_has_strict_times_even_with_identical_random_bits(self):
        history, originals, tasks = five_task_fixture()
        with mock.patch.object(prepare.public.secrets, "token_bytes", return_value=b"\0" * 10):
            events = prepare.recovery_events(originals, tasks, "2026-11-20T04:00:00.000Z")
        self.assertEqual(15, len(events))
        for left, right in zip(events, events[1:]):
            self.assertLess(left["occurred_at"], right["occurred_at"])
            self.assertLess(left["event_id"], right["event_id"])
        states = _historical_replay_states(history + events, {task["result_id"] for task in tasks}, authority_event_type="historical_archive_result.replay_authorized")
        self.assertEqual(set(originals), set(states))
        self.assertTrue(all(state["status"] == "queued" and state["attempt"] == 0 for state in states.values()))

    def test_original_component_runtime_and_measurement_locks_cannot_drift(self):
        from test_historical_archive_planning import planning_fixture
        _, _, replacement, _, _ = planning_fixture()
        original = copy.deepcopy(replacement)
        original["execution_profile"].update(schema_version=1, vm_image_digest="sha256:" + "0" * 64)
        del original["execution_profile"]["archive_capacity_class"]
        prepare._require_profile_lock_preserved(original, replacement)
        for field, value in (("go_toolchain", "go1.26.0"), ("rust_toolchain", "rustc-1.90.0"), ("cache_state", "warm"), ("components", {}), ("runner_profile", "different")):
            wrong = copy.deepcopy(replacement)
            wrong["execution_profile"][field] = value
            with self.subTest(field=field), self.assertRaises(HistoricalReplayControllerError):
                prepare._require_profile_lock_preserved(original, wrong)
        wrong = copy.deepcopy(replacement)
        wrong["measurement_config"]["wall_time_limit_ms"] += 1
        with self.assertRaises(HistoricalReplayControllerError):
            prepare._require_profile_lock_preserved(original, wrong)

    def test_shared_artifacts_require_the_exact_closed_selection(self):
        task, crosswalk, manifest = evidence()
        blobs = {task["crosswalk_path"]: canonical_bytes(crosswalk), task["reconfiguration_path"]: canonical_bytes(manifest)}
        with mock.patch.object(prepare.public, "_git_blob", side_effect=lambda root, commit, path: blobs[path]):
            prepare._require_shared_artifacts(pathlib.Path("/synthetic"), [task])
            expanded = copy.deepcopy(manifest)
            expanded["task_count"] = 2
            blobs[task["reconfiguration_path"]] = canonical_bytes(expanded)
            with self.assertRaises(HistoricalReplayControllerError):
                prepare._require_shared_artifacts(pathlib.Path("/synthetic"), [task])
        second = copy.deepcopy(task)
        second["reconfiguration_commit"] = "f" * 40
        with self.assertRaisesRegex(HistoricalReplayControllerError, "mixes"):
            prepare._require_shared_artifacts(pathlib.Path("/synthetic"), [task, second])

    def test_consumer_preflight_checks_materialized_tasks_and_both_routes(self):
        queue = json.loads((ROOT / "historical-public-archive-recovery-queue-v2.json").read_text())
        events = json.loads((ROOT / "historical-public-archive-recovery-v2.json").read_text())
        proposal = copy.deepcopy(queue["tasks"][0])
        proposal["event_id"] = "0198abcd-0000-7000-8000-000000000099"
        empty = {**queue, "tasks": []}
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory)
            (output / "historical-private-replay-queue.json").write_bytes(state_canonical_bytes(queue))
            (output / "historical-public-replay-queue.json").write_bytes(state_canonical_bytes(empty))
            with (
                mock.patch.object(prepare.archive, "_load_reviewed_input_context", return_value=(None, None, {}, None, None, None)) as reviewed,
                mock.patch.object(prepare.archive, "load_reviewed_crosswalk_entry", return_value={}) as crosswalk,
                mock.patch.object(prepare.archive, "load_archive_inputs") as inputs,
                mock.patch.object(prepare.archive, "_execution_plan") as plan,
            ):
                prepare._validate_prospective_consumers(pathlib.Path("/synthetic"), pathlib.Path("/audit"), output, events, [proposal])
                self.assertEqual(queue["tasks"][0], reviewed.call_args.args[1])
                self.assertEqual(queue["tasks"][0], crosswalk.call_args.args[2])
                self.assertEqual(queue["tasks"][0], inputs.call_args.args[1])
                self.assertEqual(queue["tasks"][0], plan.call_args.args[0])
            (output / "historical-private-replay-queue.json").write_bytes(state_canonical_bytes({**queue, "tasks": []}))
            with self.assertRaisesRegex(HistoricalReplayControllerError, "selection once"):
                prepare._validate_prospective_consumers(pathlib.Path("/synthetic"), pathlib.Path("/audit"), output, events, [proposal])

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
