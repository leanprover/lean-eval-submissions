from __future__ import annotations

import base64
import collections
import copy
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_historical_private_replay as private_replay  # noqa: E402
from key_capability_contract import (  # noqa: E402
    archive_file_key_id,
    archive_key_id,
    canonical_archive_path,
)

from tests.frozen_results_tree import materialize_results_tree  # noqa: E402

CROSSWALK_SHA256 = "dfdcbc0da3a3526f8a26e6a69cefa41cbcd92de7608752193b742fcd92b00a67"
CROSSWALK_COMMIT = "da421bf6a55f3234719151d8a2422da3b2febf23"
RESULTS_COMMIT = "7fb2e762e5470ae1929dbe069dbcd0c8488b51d7"
PROFILE_COMMIT = "faf631452b399ecbab3bd2981e8052390bac5a99"
PLAN_SHA256 = "d9561ad62098e0542656678f207b3360b0b295be975c292cbf729dc48d03bd5e"
CROSSWALK = (
    ROOT
    / "evidence/historical-replay/private-crosswalks"
    / f"{CROSSWALK_SHA256}.json"
)
PLAN = ROOT / "evidence/historical-replay/private-plans" / f"{PLAN_SHA256}.json"


def recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(recursive_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(recursive_keys(item) for item in value))
    return set()


def envelope(submission_id: str, ciphertext: bytes) -> dict[str, object]:
    recipient = "age1" + "q" * 40
    return {
        "schema_version": 1,
        "submission_id": submission_id,
        "archive_ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
        "data_key_id": archive_key_id(submission_id, recipient),
        "age_recipient": recipient,
        "adapter": "aws-kms-v1",
        "wrapped_identity": base64.b64encode(b"wrapped fixture identity").decode(),
    }


def file_key_envelope(submission_id: str, ciphertext: bytes) -> dict[str, object]:
    ciphertext_digest = hashlib.sha256(ciphertext).hexdigest()
    return {
        "schema_version": 2,
        "submission_id": submission_id,
        "archive_ciphertext_sha256": ciphertext_digest,
        "data_key_id": archive_file_key_id(submission_id, ciphertext_digest),
        "key_material_type": "age-file-key-v1",
        "adapter": "aws-kms-v1",
        "wrapped_key_material": base64.b64encode(b"wrapped fixture file key").decode(),
    }


class HistoricalPrivateReplayPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = PLAN.read_bytes()
        cls.plan = json.loads(cls.raw)

    def test_committed_plan_is_canonical_content_addressed_and_schema_valid(self) -> None:
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), PLAN_SHA256)
        self.assertEqual(self.raw, private_replay.canonical(self.plan))
        private_replay.validate_plan(self.plan)
        result = subprocess.run(
            [
                "npx", "--yes", "ajv-cli@5.0.0", "validate",
                "--spec=draft2020", "--strict=false",
                "-s", str(private_replay.PLAN_SCHEMA), "-d", str(PLAN),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("unknown format", result.stdout + result.stderr)

    def test_committed_plan_covers_the_exact_retained_private_corpus(self) -> None:
        self.assertEqual(
            self.plan["classification_counts"],
            {"archive_not_found": 29, "bound": 639},
        )
        self.assertEqual(
            self.plan["replay_readiness_counts"],
            {
                "archive_not_found": 29,
                "profile_pending": 282,
                "profile_qualified": 357,
            },
        )
        self.assertEqual(len(self.plan["entries"]), 668)
        self.assertEqual(len(self.plan["profiles"]), 21)
        identifiers = [entry["result_id"] for entry in self.plan["entries"]]
        self.assertEqual(identifiers, sorted(set(identifiers)))

    def test_plan_preserves_result_identity_and_shared_archive_reuse(self) -> None:
        crosswalk = json.loads(CROSSWALK.read_text(encoding="utf-8"))
        expected = {entry["result_id"]: entry for entry in crosswalk["entries"]}
        actual = {entry["result_id"]: entry for entry in self.plan["entries"]}
        self.assertEqual(set(actual), set(expected))
        for result_id, source in expected.items():
            entry = actual[result_id]
            self.assertEqual(entry["classification"], source["classification"])
            self.assertEqual(
                entry["crosswalk_entry_sha256"],
                hashlib.sha256(private_replay.canonical_compact(source)).hexdigest(),
            )
            if source["classification"] == "bound":
                self.assertEqual(entry["archive_submission_id"], source["submission_id"])
                self.assertEqual(
                    entry["archive_plan_entry_sha256"],
                    source["archive_plan_entry_sha256"],
                )
        plan_reuse = collections.Counter(
            entry["archive_submission_id"]
            for entry in self.plan["entries"]
            if entry["classification"] == "bound"
        )
        self.assertEqual(len(plan_reuse), 439)
        self.assertEqual(max(plan_reuse.values()), 19)
        self.assertEqual(sum(count > 1 for count in plan_reuse.values()), 58)

    def test_plan_emits_no_private_source_data(self) -> None:
        forbidden = {
            "archiver_workflow_run",
            "issue_number",
            "source_commit",
            "source_repository",
            "submission_ref",
            "submission_repo",
            "submitter",
        }
        self.assertTrue(recursive_keys(self.plan).isdisjoint(forbidden))
        crosswalk = json.loads(CROSSWALK.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            results = materialize_results_tree(RESULTS_COMMIT, pathlib.Path(directory))
            private_values = []
            for path in results.glob("*.json"):
                if path.name == ".gitkeep":
                    continue
                value = json.loads(path.read_text(encoding="utf-8"))
                private_values.extend(
                    record["submission"][field]
                    for record in value["results"]
                    if record["submission"]["public"] is False
                    for field in ("repo", "ref")
                )
        encoded = self.raw.decode("utf-8")
        self.assertTrue(all(value not in encoded for value in private_values))
        self.assertEqual(crosswalk["private_result_count"], 668)

    def test_builder_reproduces_the_committed_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results = materialize_results_tree(RESULTS_COMMIT, pathlib.Path(directory))
            with mock.patch.object(private_replay, "verify_checkout"):
                rebuilt = private_replay.build_plan(
                    crosswalk_path=CROSSWALK,
                    crosswalk_commit=CROSSWALK_COMMIT,
                    results_root=results,
                    public_profile_directory=ROOT / "evidence/public-replay/profiles",
                    public_profile_commit=PROFILE_COMMIT,
                    profile_matrix=ROOT / "configuration/historical-public-replay-profile-matrix-v1.json",
                    private_profiles=[],
                )
        self.assertEqual(private_replay.canonical(rebuilt), self.raw)

    def test_state_compatibility_executes_validator_instead_of_searching_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts/validate_state.py").write_text(
                "# historical_archive_result.replay_authorized\n"
                "# historical_archive_result.replay_profile_qualified\n"
                "# historical_archive_result.replay_unavailable\n"
                "# replay.enqueued\n"
                "def validate_event_data(event, label):\n"
                "    raise ValueError('unsupported')\n"
                "def validate_semantics(events, environment):\n"
                "    return None\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                private_replay.PrivateReplayPlanError,
                "fails the supplied validator",
            ):
                private_replay.validate_state_candidates(
                    state_root=root,
                    state_commit=None,
                    candidates=[{"event_type": "replay.enqueued"}],
                    append_ready=False,
                )

    def test_unavailable_candidates_cover_all_twenty_nine_terminal_entries(self) -> None:
        entries = [
            entry
            for entry in self.plan["entries"]
            if entry["classification"] == "archive_not_found"
        ]
        first = private_replay._parse_timestamp("2026-08-27T12:00:00.000Z")
        events = [
            private_replay.build_unavailable_event(
                entry=entry,
                plan_commit="1" * 40,
                plan_path=f"{private_replay.PLAN_PREFIX}/{PLAN.name}",
                plan_sha256=PLAN_SHA256,
                results_commit=self.plan["results"]["commit"],
                crosswalk=self.plan["crosswalk"],
                occurred_at=first + private_replay.dt.timedelta(milliseconds=index),
            )
            for index, entry in enumerate(entries)
        ]
        self.assertEqual(len(events), 29)
        self.assertEqual(
            {event["subject_id"] for event in events},
            {entry["result_id"] for entry in entries},
        )
        expected_payload_fields = {
            "historical_accepted_at", "owner_login", "declared_model",
            "problem_id", "statement_revision", "results_repository",
            "results_commit", "results_path", "result_file_sha256",
            "result_tree_digest", "crosswalk_repository", "crosswalk_commit",
            "crosswalk_path", "crosswalk_sha256", "crosswalk_entry_sha256",
            "plan_repository", "plan_commit", "plan_path", "plan_sha256",
            "plan_entry_sha256", "reason_code",
        }
        for event in events:
            self.assertEqual(
                event["event_type"],
                "historical_archive_result.replay_unavailable",
            )
            self.assertIsNone(event["causation_event_id"])
            self.assertEqual(set(event["payload"]), expected_payload_fields)
            self.assertEqual(event["payload"]["reason_code"], "archive_not_found")
        encoded = private_replay.canonical(events)
        for forbidden in (b"source_repository", b"source_commit", b"archive_path"):
            self.assertNotIn(forbidden, encoded)

    def test_bound_state_candidate_uses_existing_event_chain(self) -> None:
        entry = next(
            item
            for item in self.plan["entries"]
            if item.get("replay_profile_status") == "profile_qualified"
        )
        profile = self.plan["profiles"][entry["execution_profile_digest"]]
        archive = {
            "archive_repository": "leanprover/lean-eval-audit",
            "archive_commit": "a" * 40,
            "archive_path": canonical_archive_path(entry["archive_submission_id"]),
            "archive_sidecar_path": canonical_archive_path(
                entry["archive_submission_id"]
            ).removesuffix(".tar.age")
            + ".json",
            "archive_ciphertext_sha256": "b" * 64,
            "archive_sidecar_sha256": "c" * 64,
            "archive_key_envelope_sha256": "d" * 64,
            "archive_plaintext_tar_sha256": "e" * 64,
            "archive_plaintext_tar_size": 4096,
            "workflow_run_identity_sha256": "f" * 64,
        }
        events = private_replay.build_bound_events(
            entry=entry,
            profile=profile,
            archive=archive,
            plan_commit="1" * 40,
            plan_path=f"{private_replay.PLAN_PREFIX}/{PLAN.name}",
            plan_sha256=PLAN_SHA256,
            results_commit=self.plan["results"]["commit"],
            crosswalk=self.plan["crosswalk"],
            occurred_at=private_replay._parse_timestamp(
                "2026-08-27T12:00:00.000Z"
            ),
        )
        self.assertEqual(
            [event["event_type"] for event in events],
            [
                "historical_archive_result.replay_authorized",
                "historical_archive_result.replay_profile_qualified",
                "replay.enqueued",
            ],
        )
        self.assertNotIn("causation_event_id", events[0])
        self.assertEqual(events[1]["causation_event_id"], events[0]["event_id"])
        self.assertEqual(events[2]["causation_event_id"], events[1]["event_id"])
        self.assertEqual(events[0]["payload"]["crosswalk_path"], self.plan["crosswalk"]["path"])
        encoded = private_replay.canonical(events)
        self.assertNotIn(b"submission_repo", encoded)
        self.assertNotIn(b"submission_ref", encoded)

    def test_schema_v3_binding_accepts_v1_and_v2_envelopes(self) -> None:
        submission_id = "019a0000-0000-7000-8000-000000000001"
        entry = {
            "archive_submission_id": submission_id,
            "benchmark_commit": "a" * 40,
        }
        bindings = []
        for index, ciphertext in enumerate(
            (b"first fresh ciphertext", b"second fresh ciphertext")
        ):
            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                relative = canonical_archive_path(submission_id)
                archive = root.joinpath(*relative.split("/"))
                archive.parent.mkdir(parents=True)
                archive.write_bytes(ciphertext)
                value = {
                    "schema_version": 3,
                    "submission_id": submission_id,
                    "submission_repo": "example/private-source",
                    "submission_ref": "d" * 40,
                    "submission_kind": "github_repo",
                    "submission_public": False,
                    "submitter": "example",
                    "model": "Example Model",
                    "sha256_ciphertext": hashlib.sha256(ciphertext).hexdigest(),
                    "size_bytes_ciphertext": len(ciphertext),
                    "sha256_plaintext_tar": "b" * 64,
                    "size_bytes_plaintext_tar": 4096,
                    "archived_at": "2026-08-27T12:00:00Z",
                    "benchmark_commit": "a" * 40,
                    "archiver_workflow_run": "https://github.com/leanprover/lean-eval-submissions/actions/runs/123",
                    "key_envelope": (
                        envelope(submission_id, ciphertext)
                        if index == 0
                        else file_key_envelope(submission_id, ciphertext)
                    ),
                }
                archive.with_suffix("").with_suffix(".json").write_text(
                    json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                bindings.append(private_replay.archive_binding(root, "c" * 40, entry))
                with self.assertRaisesRegex(
                    private_replay.PrivateReplayPlanError,
                    "migrated archive binding is invalid",
                ):
                    private_replay.archive_binding(
                        root,
                        "c" * 40,
                        {**entry, "benchmark_commit": "f" * 40},
                    )
        self.assertNotEqual(
            bindings[0]["archive_ciphertext_sha256"],
            bindings[1]["archive_ciphertext_sha256"],
        )
        self.assertEqual(
            bindings[0]["archive_plaintext_tar_sha256"],
            bindings[1]["archive_plaintext_tar_sha256"],
        )
        self.assertEqual(
            bindings[0]["workflow_run_identity_sha256"],
            bindings[1]["workflow_run_identity_sha256"],
        )

    def test_exact_committed_plan_blob_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            path = root / private_replay.PLAN_PREFIX / PLAN.name
            path.parent.mkdir(parents=True)
            path.write_bytes(self.raw)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                [
                    "git", "-C", str(root),
                    "-c", "user.name=fixture",
                    "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "fixture",
                ],
                check=True,
            )
            commit = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            private_replay.verify_blob_at_commit(
                path, self.raw, commit, "historical private replay plan"
            )
            with self.assertRaisesRegex(
                private_replay.PrivateReplayPlanError,
                "not the exact selected commit blob",
            ):
                private_replay.verify_blob_at_commit(
                    path,
                    self.raw + b"\n",
                    commit,
                    "historical private replay plan",
                )

    def test_append_ready_checks_plan_commit_before_other_inputs(self) -> None:
        arguments = private_replay.argparse.Namespace(
            plan=str(PLAN),
            authority_commit=PROFILE_COMMIT,
            selection="full",
            audit_commit="a" * 40,
            append_ready=True,
            audit_root="/does/not/matter",
            state_root="/does/not/matter",
            state_commit="b" * 40,
            first_occurred_at="2026-08-27T12:00:00.000Z",
            output_directory="/does/not/matter",
        )
        with self.assertRaisesRegex(
            private_replay.PrivateReplayPlanError,
            "exact Git checkout proof failed",
        ):
            private_replay.prepare_state_events(arguments)

    def test_unavailable_only_is_append_ready_without_audit_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = pathlib.Path(directory)
            authority_root = fixture_root / "authority"
            state_root = fixture_root / "State"
            output_root = fixture_root / "output"

            subprocess.run(["git", "init", "-q", str(authority_root)], check=True)
            plan_path = authority_root / private_replay.PLAN_PREFIX / PLAN.name
            plan_path.parent.mkdir(parents=True)
            plan_path.write_bytes(self.raw)
            subprocess.run(["git", "-C", str(authority_root), "add", "."], check=True)
            subprocess.run(
                [
                    "git", "-C", str(authority_root),
                    "-c", "user.name=fixture",
                    "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "fixture authority",
                ],
                check=True,
            )
            authority_commit = subprocess.check_output(
                ["git", "-C", str(authority_root), "rev-parse", "HEAD"],
                text=True,
            ).strip()

            subprocess.run(["git", "init", "-q", str(state_root)], check=True)
            subprocess.run(
                [
                    "git", "-C", str(state_root), "remote", "add", "origin",
                    "https://github.com/leanprover/lean-eval-state.git",
                ],
                check=True,
            )
            (state_root / "scripts").mkdir()
            (state_root / "state.json").write_bytes(
                private_replay.canonical(
                    {"environment": "production", "schema_version": 1},
                    state_event=True,
                )
            )
            (state_root / "scripts/validate_state.py").write_text(
                "def validate_event_data(event, label):\n"
                "    if event.get('event_type') != "
                "'historical_archive_result.replay_unavailable':\n"
                "        raise ValueError(label)\n"
                "    if event.get('causation_event_id', 'missing') is not None:\n"
                "        raise ValueError('not a terminal root')\n"
                "def validate_semantics(events, environment):\n"
                "    if environment != 'production' or len(events) not in (0, 29):\n"
                "        raise ValueError('combined graph')\n"
                "    if len({event['event_id'] for event in events}) != len(events):\n"
                "        raise ValueError('duplicate event')\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(state_root), "add", "."], check=True)
            subprocess.run(
                [
                    "git", "-C", str(state_root),
                    "-c", "user.name=fixture",
                    "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "fixture State",
                ],
                check=True,
            )
            state_commit = subprocess.check_output(
                ["git", "-C", str(state_root), "rev-parse", "HEAD"],
                text=True,
            ).strip()

            # This namespace intentionally has no audit_root or audit_commit.
            # Any access to the credentialed lane therefore fails the test.
            arguments = private_replay.argparse.Namespace(
                plan=str(plan_path),
                authority_commit=authority_commit,
                selection="unavailable-only",
                append_ready=False,
                state_root=str(state_root),
                state_commit=state_commit,
                first_occurred_at="2026-08-27T12:00:00.000Z",
                output_directory=str(output_root),
            )
            with mock.patch.object(
                private_replay,
                "archive_binding",
                side_effect=AssertionError("audit archive path was touched"),
            ):
                result = private_replay.prepare_state_events(arguments)

            self.assertEqual(result, 0)
            paths = sorted(output_root.glob("events/*/*.json"))
            self.assertEqual(len(paths), 29)
            events = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
            self.assertEqual(
                {event["subject_id"] for event in events},
                {
                    entry["result_id"]
                    for entry in self.plan["entries"]
                    if entry["classification"] == "archive_not_found"
                },
            )
            self.assertTrue(
                all(event["causation_event_id"] is None for event in events)
            )

    def test_full_selection_retains_the_credentialed_audit_boundary(self) -> None:
        arguments = private_replay.argparse.Namespace(
            plan=str(PLAN),
            authority_commit="a" * 40,
            selection="full",
            audit_root=None,
            audit_commit=None,
            append_ready=False,
            state_root="/not/reached",
            state_commit=None,
            first_occurred_at="2026-08-27T12:00:00.000Z",
            output_directory="/not/reached",
        )
        with self.assertRaisesRegex(
            private_replay.PrivateReplayPlanError,
            "full selection requires a valid audit root and commit",
        ):
            private_replay.prepare_state_events(arguments)

    def test_append_ready_requires_clean_exact_state_and_combined_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                [
                    "git", "-C", str(root), "remote", "add", "origin",
                    "https://github.com/leanprover/lean-eval-state.git",
                ],
                check=True,
            )
            (root / "scripts").mkdir()
            (root / "events/00").mkdir(parents=True)
            (root / "state.json").write_bytes(
                private_replay.canonical(
                    {"environment": "production", "schema_version": 1},
                    state_event=True,
                )
            )
            existing = {
                "event_id": "00000000-0000-7000-8000-000000000000",
                "event_type": "existing",
            }
            (root / "events/00/00000000-0000-7000-8000-000000000000.json").write_bytes(
                private_replay.canonical(existing, state_event=True)
            )
            (root / "scripts/validate_state.py").write_text(
                "def validate_event_data(event, label):\n"
                "    if set(event) != {'event_id', 'event_type'}:\n"
                "        raise ValueError(label)\n"
                "def validate_semantics(events, environment):\n"
                "    if environment != 'production':\n"
                "        raise ValueError('environment')\n"
                "    if len(events) > 1 and events[-1]['event_type'] != 'candidate':\n"
                "        raise ValueError('combined graph')\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                [
                    "git", "-C", str(root),
                    "-c", "user.name=fixture",
                    "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "fixture",
                ],
                check=True,
            )
            commit = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            candidate = {
                "event_id": "00000000-0000-7000-8000-000000000001",
                "event_type": "candidate",
            }
            private_replay.validate_state_candidates(
                state_root=root,
                state_commit=commit,
                candidates=[candidate],
                append_ready=True,
            )
            with self.assertRaisesRegex(
                private_replay.PrivateReplayPlanError,
                "combined existing and candidate State graph is invalid",
            ):
                private_replay.validate_state_candidates(
                    state_root=root,
                    state_commit=commit,
                    candidates=[{**candidate, "event_type": "semantic_failure"}],
                    append_ready=True,
                )
            (root / "untracked").write_text("dirty", encoding="utf-8")
            with self.assertRaisesRegex(
                private_replay.PrivateReplayPlanError,
                "State input tree is not clean",
            ):
                private_replay.validate_state_candidates(
                    state_root=root,
                    state_commit=commit,
                    candidates=[candidate],
                    append_ready=True,
                )

    def test_private_profile_is_embedded_without_a_new_evidence_locator(self) -> None:
        public_digest, public = next(iter(self.plan["profiles"].items()))
        private = copy.deepcopy(public)
        private.pop("reused_public_profile")
        private["execution_profile_digest"] = public_digest
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "profile.json"
            path.write_bytes(private_replay.canonical(private))
            loaded = private_replay.load_private_profiles([path])
        self.assertEqual(set(loaded), {public_digest})
        self.assertNotIn("reused_public_profile", loaded[public_digest])


if __name__ == "__main__":
    unittest.main()
