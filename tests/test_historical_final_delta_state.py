from __future__ import annotations

import importlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from prepare_historical_final_delta_activation import (
    ActivationError,
    build,
    canonical,
    verify_crosswalk_blob,
)
from prepare_historical_final_delta_state import (
    document_bytes,
    expectation,
    load_final_expectation,
)
from test_prepare_historical_final_delta_packet import Fixture


class FinalDeltaStateTests(unittest.TestCase):
    def test_substituted_crosswalk_commit_and_blob_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
            subprocess.run(
                ["git", "-C", root, "config", "user.name", "test"], check=True
            )
            subprocess.run(
                ["git", "-C", root, "config", "user.email", "test@example.com"],
                check=True,
            )
            raw = canonical({"crosswalk": 1})
            (root / "crosswalk.json").write_bytes(raw)
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                ["git", "-C", root, "commit", "-q", "-m", "crosswalk"],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", root, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            binding = {
                "commit": commit,
                "path": "crosswalk.json",
                "sha256": __import__("hashlib").sha256(raw).hexdigest(),
            }
            verify_crosswalk_blob(root, commit, binding)
            with self.assertRaisesRegex(ActivationError, "locator"):
                verify_crosswalk_blob(root, "f" * 40, binding)
            binding["sha256"] = "0" * 64
            with self.assertRaisesRegex(ActivationError, "differs"):
                verify_crosswalk_blob(root, commit, binding)

    def test_missing_profiles_emit_only_conditional_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            packet = fixture.build()
            raw = canonical(packet)
            requirements, public_plan, private_plan = build(
                preparation=packet,
                preparation_raw=raw,
                preparation_commit="a" * 40,
                preparation_path="evidence/historical-replay/final-delta-preparations/"
                + __import__("hashlib").sha256(raw).hexdigest()
                + ".json",
                crosswalk_commit=packet["classification_inputs"]["private_crosswalk"][
                    "commit"
                ],
                public={},
                private={},
                benchmarks={},
            )
        self.assertEqual(requirements["activation_status"], "blocked")
        self.assertEqual(len(requirements["missing"]), 2)
        self.assertIsNone(public_plan)
        self.assertIsNone(private_plan)
        self.assertIn("one-shot", requirements["conditional_action"])

    def test_activation_rejects_substituted_crosswalk_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            packet = Fixture(pathlib.Path(temporary)).build()
            raw = canonical(packet)
            with self.assertRaisesRegex(ActivationError, "crosswalk commit"):
                build(
                    preparation=packet,
                    preparation_raw=raw,
                    preparation_commit="a" * 40,
                    preparation_path="evidence/historical-replay/final-delta-preparations/"
                    + __import__("hashlib").sha256(raw).hexdigest()
                    + ".json",
                    crosswalk_commit="b" * 40,
                    public={},
                    private={},
                    benchmarks={},
                )

    def test_dynamic_expectation_accepts_unavailable_and_zero_task_lane(self) -> None:
        public_plan = {
            "entries": [
                {"disposition": "unavailable"},
                {"disposition": "replayable"},
            ]
        }
        private_plan = {"entries": [{"classification": "archive_not_found"}]}
        value = expectation(public_plan, private_plan)
        self.assertEqual(value["total_event_count"], 5)
        self.assertEqual(value["total_task_count"], 1)
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "expectation.json"
            path.write_bytes(document_bytes(value))
            self.assertEqual(load_final_expectation(path), value)

    def test_stage_and_verify_accept_unavailable_events_and_zero_task_lane(self) -> None:
        import review_historical_baseline_state_batch as baseline_review
        from test_review_historical_baseline_state_batch import (
            ReviewHistoricalBaselineStateBatchTests,
            result_identity,
        )

        saved = {
            name: getattr(baseline_review, name)
            for name in (
                "REVIEW_BRANCH",
                "SUMMARY_KIND",
                "IMPLEMENTATION_PATHS",
                "load_expectation",
                "lane_inventory",
                "copy_candidate",
                "stage",
                "verify",
            )
        }
        final_review = importlib.reload(
            importlib.import_module("review_historical_final_delta_state")
        )

        class FinalFixture(ReviewHistoricalBaselineStateBatchTests):
            def candidate_events(self) -> list[dict[str, object]]:
                public_replay = super().candidate_events()[:3]
                public_unavailable = self.event(
                    7,
                    "historical_result.replay_unavailable",
                    result_identity(3),
                    None,
                )
                private_unavailable = self.event(
                    8,
                    "historical_archive_result.replay_unavailable",
                    result_identity(4),
                    None,
                )
                return [*public_replay, public_unavailable, private_unavailable]

            def make_final_fixture(
                self, directory: str
            ) -> tuple[
                pathlib.Path,
                pathlib.Path,
                pathlib.Path,
                pathlib.Path,
                pathlib.Path,
                str,
            ]:
                full_implementation_paths = baseline_review.IMPLEMENTATION_PATHS
                baseline_review.IMPLEMENTATION_PATHS = tuple(
                    path
                    for path in full_implementation_paths
                    if path
                    != "configuration/historical-final-delta-state-batch-v1.json"
                ) + ("configuration/historical-baseline-state-batch-v1.json",)
                try:
                    submissions, state, candidate, baseline_expectation, _ = (
                        super().make_fixture(directory)
                    )
                finally:
                    baseline_review.IMPLEMENTATION_PATHS = full_implementation_paths
                projection = state / "scripts/public_projection.py"
                projection.write_text(
                    """def project_public_state_v6(environment,events,commit):
  series=[{'replay_task_id':e['subject_id']} for e in events if e['event_type']=='replay.enqueued']
  unavailable=[
    {'result_id':'r2_'+'e'*64,'source_visibility':'public'},
    {'result_id':'r2_'+'f'*64,'source_visibility':'private'},
  ]
  return {'schema_version':6,'environment':environment,'source_state_commit':commit,'historical_replay_series':series,'historical_replay_unavailability':unavailable}
""",
                    encoding="utf-8",
                )
                self.git(state, "add", str(projection))
                self.git(state, "commit", "--amend", "--no-edit")
                parent = self.git(state, "rev-parse", "HEAD")

                expectation_path = (
                    submissions
                    / "configuration/historical-final-delta-state-batch-v1.json"
                )
                value = {
                    "schema_version": 1,
                    "kind": "historical_final_delta_state_batch_expectation",
                    "environment": "production",
                    "lanes": {
                        "public": {
                            "authority_event_type": "historical_result.replay_authorized",
                            "qualification_event_type": "historical_result.replay_profile_qualified",
                            "enqueue_event_type": "replay.enqueued",
                            "unavailable_event_type": "historical_result.replay_unavailable",
                            "event_count": 4,
                            "task_count": 1,
                            "unavailable_count": 1,
                        },
                        "private": {
                            "authority_event_type": "historical_archive_result.replay_authorized",
                            "qualification_event_type": "historical_archive_result.replay_profile_qualified",
                            "enqueue_event_type": "replay.enqueued",
                            "unavailable_event_type": "historical_archive_result.replay_unavailable",
                            "event_count": 1,
                            "task_count": 0,
                            "unavailable_count": 1,
                        },
                    },
                    "reviewed_unavailability_counts": {
                        "public": 1,
                        "private": 1,
                        "total": 2,
                    },
                    "total_event_count": 5,
                    "total_task_count": 1,
                }
                expectation_path.write_bytes(document_bytes(value))
                baseline_expectation.unlink()

                baseline_manifest = (
                    candidate / "historical-baseline-state-append-candidate.json"
                )
                manifest = json.loads(baseline_manifest.read_text(encoding="utf-8"))
                manifest["kind"] = "historical_final_delta_state_append_candidate"
                manifest["state"]["expected_head"] = parent
                manifest_path = (
                    candidate / "historical-final-delta-state-append-candidate.json"
                )
                manifest_path.write_bytes(document_bytes(manifest))
                baseline_manifest.unlink()

                self.git(submissions, "add", "-A")
                self.git(submissions, "commit", "--amend", "--no-edit")
                implementation = self.git(submissions, "rev-parse", "HEAD")
                return (
                    submissions,
                    state,
                    candidate,
                    expectation_path,
                    manifest_path,
                    implementation,
                )

        try:
            with tempfile.TemporaryDirectory() as directory:
                fixture = FinalFixture(
                    "test_stage_emits_source_free_closed_binding_and_one_create_only_commit"
                )
                (
                    submissions,
                    state,
                    candidate,
                    expectation_path,
                    manifest_path,
                    implementation,
                ) = fixture.make_final_fixture(directory)
                output = pathlib.Path(directory) / "binding.json"
                args = type(
                    "Args",
                    (),
                    {
                        "state_root": state,
                        "submissions_root": submissions,
                        "state_parent": fixture.git(state, "rev-parse", "HEAD"),
                        "audit_head": "a" * 40,
                        "audit_tree": "b" * 40,
                        "implementation_commit": implementation,
                        "expectation": expectation_path,
                        "candidate_root": candidate,
                        "manifest": manifest_path,
                        "output": output,
                    },
                )()
                final_review.stage(args)
                binding = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(binding["candidate"]["event_count"], 5)
                self.assertEqual(binding["candidate"]["lanes"]["public"]["task_count"], 1)
                self.assertEqual(binding["candidate"]["lanes"]["private"]["task_count"], 0)
                final_review.verify(
                    type(
                        "Args",
                        (),
                        {
                            "state_root": state,
                            "submissions_root": submissions,
                            "binding": output,
                        },
                    )()
                )
        finally:
            for name, value in saved.items():
                setattr(baseline_review, name, value)


if __name__ == "__main__":
    unittest.main()
