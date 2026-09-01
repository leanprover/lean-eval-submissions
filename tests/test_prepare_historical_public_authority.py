from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
MODULE_PATH = ROOT / "scripts/prepare_historical_public_authority.py"
SPEC = importlib.util.spec_from_file_location(
    "prepare_historical_public_authority", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
authority = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(authority)


class HistoricalPublicBatchFinalizationTests(unittest.TestCase):
    PACKET_QUALIFICATION_COMMIT = "81e94fe2f4fc819300fd7d4e036f00124166784f"

    @classmethod
    def setUpClass(cls) -> None:
        dispositions = json.loads(
            (
                ROOT
                / "evidence/public-replay/unavailability-dispositions-v1/"
                "e577802df7df3a657a1dbfea20d60985264cf82bc955e39951160acf39adc66b.json"
            ).read_text(encoding="utf-8")
        )["dispositions"]
        values = dispositions.values() if isinstance(dispositions, dict) else dispositions
        cls.terminal_public_results = tuple(
            sorted(
                result_id
                for disposition in values
                for result_id in disposition["result_ids"]
            )
        )
        if len(cls.terminal_public_results) != authority.PINNED_PUBLIC_UNAVAILABLE_COUNT:
            raise AssertionError("current public disposition fixture changed")

    def load_packet_inputs(self):
        with mock.patch.object(authority, "verify_checkout"):
            return authority.load_batch_inputs(
                ROOT,
                self.PACKET_QUALIFICATION_COMMIT,
                self.terminal_public_results,
            )

    @staticmethod
    def materialized_queue(_root: pathlib.Path, events: list[dict[str, object]]):
        latest = "2026-08-26T05:59:59.999Z"
        if not events:
            return (
                latest,
                {
                    "schema_version": 2,
                    "environment": "production",
                    "source_event_count": authority.PINNED_STATE_EVENT_COUNT,
                    "source_digest": "0" * 64,
                    "tasks": [],
                },
                HistoricalPublicBatchFinalizationTests.terminal_public_results,
            )
        tasks = []
        for index in range(0, len(events), 3):
            authorized, qualified, enqueued = events[index : index + 3]
            tasks.append(
                {
                    "replay_task_id": enqueued["subject_id"],
                    **enqueued["payload"],
                    **authorized["payload"],
                    "authority_event_id": authorized["event_id"],
                    "authorized_at": authorized["occurred_at"],
                    **qualified["payload"],
                    "qualification_event_id": qualified["event_id"],
                    "qualified_at": qualified["occurred_at"],
                    "status": "queued",
                    "attempt": 0,
                    "event_id": enqueued["event_id"],
                    "occurred_at": enqueued["occurred_at"],
                }
            )
        tasks.sort(key=lambda task: task["replay_task_id"])
        return (
            latest,
            {
                "schema_version": 2,
                "environment": "production",
                "source_event_count": (
                    authority.PINNED_STATE_EVENT_COUNT + authority.BATCH_EVENT_COUNT
                ),
                "source_digest": "0" * 64,
                "tasks": tasks,
            },
            HistoricalPublicBatchFinalizationTests.terminal_public_results,
        )

    def test_packet_profiles_and_task_content_are_exact(self) -> None:
        matrix, profiles, selections, excluded = self.load_packet_inputs()
        events, tasks = authority.build_batch_events(
            matrix,
            selections,
            profiles,
            self.PACKET_QUALIFICATION_COMMIT,
            "2026-08-29T22:30:00.000Z",
            "0" * 64,
        )
        self.assertEqual(
            (len(profiles), len(selections), len(excluded), len(events), len(tasks)),
            (35, 174, 20, 522, 174),
        )
        profile_files = [
            {"path": relative, "sha256": authority.sha256_bytes(raw)}
            for _, raw, relative in sorted(
                profiles.values(), key=lambda item: item[2]
            )
        ]
        selected_benchmarks = {
            entry["benchmark_commit"] for _, _, entry in selections
        }
        selected_profile_files = [
            {"path": relative, "sha256": authority.sha256_bytes(raw)}
            for benchmark_commit, (_, raw, relative) in sorted(profiles.items())
            if benchmark_commit in selected_benchmarks
        ]
        self.assertEqual(
            authority.sha256_bytes(authority.canonical(profile_files)),
            authority.BATCH_PROFILE_SET_SHA256,
        )
        self.assertEqual(
            authority.sha256_bytes(authority.canonical(selected_profile_files)),
            authority.BATCH_SELECTED_PROFILE_SET_SHA256,
        )
        self.assertEqual(
            authority.sha256_bytes(authority.canonical(list(excluded))),
            authority.BATCH_TERMINAL_EXCLUSION_SET_SHA256,
        )
        self.assertEqual(
            authority.sha256_bytes(
                authority.canonical(
                    authority.batch_selection_content(selections, profiles)
                )
            ),
            authority.BATCH_SELECTION_SET_SHA256,
        )
        self.assertEqual(
            authority.sha256_bytes(
                authority.canonical(authority.batch_task_content(tasks))
            ),
            authority.BATCH_TASK_CONTENT_SET_SHA256,
        )

    def test_finalize_batch_is_create_only(self) -> None:
        matrix, profiles, selections, excluded = self.load_packet_inputs()
        _, tasks = authority.build_batch_events(
            matrix,
            selections,
            profiles,
            self.PACKET_QUALIFICATION_COMMIT,
            "2026-08-29T22:30:00.000Z",
            "0" * 64,
        )
        expected_bindings = {
            "BATCH_PROFILE_SET_SHA256": authority.BATCH_PROFILE_SET_SHA256,
            "BATCH_SELECTED_PROFILE_SET_SHA256": (
                authority.BATCH_SELECTED_PROFILE_SET_SHA256
            ),
            "BATCH_TERMINAL_EXCLUSION_SET_SHA256": (
                authority.BATCH_TERMINAL_EXCLUSION_SET_SHA256
            ),
            "BATCH_SELECTION_SET_SHA256": authority.BATCH_SELECTION_SET_SHA256,
            "BATCH_TASK_CONTENT_SET_SHA256": authority.sha256_bytes(
                authority.canonical(authority.batch_task_content(tasks))
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "candidate"
            args = argparse.Namespace(
                qualification_commit=self.PACKET_QUALIFICATION_COMMIT,
                qualification_repository_root=str(ROOT),
                state_root="fixture-state",
                first_occurred_at="2026-08-29T22:30:00.000Z",
                event_id_seed="0" * 64,
                output_directory=str(output),
            )
            with (
                mock.patch.object(
                    authority,
                    "load_batch_inputs",
                    return_value=(matrix, profiles, selections, excluded),
                ),
                mock.patch.object(
                    authority,
                    "load_and_validate_pinned_state",
                    side_effect=self.materialized_queue,
                ),
                mock.patch.multiple(authority, **expected_bindings),
            ):
                authority.finalize_batch(args)
                with self.assertRaisesRegex(authority.PreparationError, "overwrite"):
                    authority.finalize_batch(args)
            self.assertEqual(
                len(list((output / "events").glob("*/*.json"))),
                authority.BATCH_EVENT_COUNT,
            )

    def test_cli_exposes_only_the_retained_batch_finalizer(self) -> None:
        parser = authority.parser()
        action = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(set(action.choices), {"finalize-batch"})

    def test_execution_packet_binds_the_retained_finalizer(self) -> None:
        digest = hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest()
        packet = (
            ROOT / "docs/historical-migration-replay-execution-packet.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            f"`scripts/prepare_historical_public_authority.py`, SHA-256 `{digest}`",
            packet,
        )

    def test_seeded_uuid7_is_deterministic_and_time_bound(self) -> None:
        timestamp = authority.timestamp_ms("2026-08-26T06:00:00.000Z")
        first = authority.deterministic_batch_uuid7(
            timestamp,
            "a" * 64,
            "r2_" + "b" * 64,
            "historical_result.replay_authorized",
        )
        self.assertRegex(first, authority.UUID7)
        self.assertEqual(authority.uuid7_timestamp_ms(first), timestamp)
        self.assertEqual(
            first,
            authority.deterministic_batch_uuid7(
                timestamp,
                "a" * 64,
                "r2_" + "b" * 64,
                "historical_result.replay_authorized",
            ),
        )


if __name__ == "__main__":
    unittest.main()
