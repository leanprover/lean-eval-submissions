from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from scripts.historical_public_runner import canonical_document_bytes
from scripts.historical_replay_controller import (
    HistoricalReplayControllerError,
    _historical_replay_states,
    _is_expected_repository_remote,
    _load_canonical,
    _load_state_canonical,
    _read_regular,
    _terminal_transition,
    bind_handoff,
    canonical_bytes,
    load_reviewed_inputs,
    plan_next,
    recover_running,
    replay_task_id,
    sha256_bytes,
    started_event,
    state_canonical_bytes,
    terminal_event,
    validate_execution_plan,
    validate_plan_against_queue,
    validate_queue,
    verify_repository_bindings,
)
from scripts.replay_orchestrator import config_digest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evidence/public-replay/plans/2b00c9651f5c3f43d44e0306a8368947a4a950ab3dd1e8c9b1f283fc82101942.json"
PROFILE_PATH = ROOT / "evidence/public-replay/profiles/0886d3624de67d0ba1cb00657f66c5f7304743773a024509fceda6ae8f4ff660.json"
MATRIX_PATH = ROOT / "configuration/historical-public-replay-profile-matrix-v1.json"
CONTRACT_PATH = ROOT / "configuration/historical-public-runner-v1.json"
PROFILE_COMMIT = "7ed4a2e33cec8800f65eb6d53619c1b7fb703876"


def loaded(path: pathlib.Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    return json.loads(raw), raw


class HistoricalReplayInputTests(unittest.TestCase):
    def test_duplicate_keys_nonfinite_numbers_and_oversize_fail_closed(self) -> None:
        cases = (
            b'{\n  "schema_version": 1,\n  "schema_version": 1\n}\n',
            b'{\n  "value": NaN\n}\n',
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for index, raw in enumerate(cases):
                path = root / f"invalid-{index}.json"
                path.write_bytes(raw)
                with self.assertRaises(HistoricalReplayControllerError):
                    _load_canonical(path, "hostile input")
            oversized = root / "oversized"
            oversized.write_bytes(b"12345")
            with self.assertRaises(HistoricalReplayControllerError):
                _read_regular(oversized, 4, "hostile input")

    def test_symlink_input_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "target.json"
            target.write_bytes(b"{}\n")
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaises(HistoricalReplayControllerError):
                _load_canonical(link, "hostile input")

    def test_state_codec_accepts_only_ascii_escaped_canonical_bytes(self) -> None:
        value = {"declared_model": "model-β"}
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_path = root / "state.json"
            state_path.write_bytes(state_canonical_bytes(value))
            self.assertEqual(_load_state_canonical(state_path, "State fixture")[0], value)
            with self.assertRaises(HistoricalReplayControllerError):
                _load_canonical(state_path, "submissions fixture")
            submissions_path = root / "submissions.json"
            submissions_path.write_bytes(canonical_bytes(value))
            with self.assertRaises(HistoricalReplayControllerError):
                _load_state_canonical(submissions_path, "State fixture")

        fixture = Fixture()
        queue = copy.deepcopy(fixture.queue)
        queue["tasks"][0]["declared_model"] = "model-β"
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "queue.json"
            path.write_bytes(state_canonical_bytes(queue))
            loaded_queue, _ = _load_state_canonical(path, "State queue")
            self.assertEqual(validate_queue(loaded_queue), loaded_queue)

    def test_cli_output_is_create_only(self) -> None:
        fixture = Fixture()
        queue = copy.deepcopy(fixture.queue)
        queue["tasks"] = []
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            queue_path = root / "queue.json"
            output = root / "plan.json"
            queue_path.write_bytes(state_canonical_bytes(queue))
            command = [
                sys.executable,
                str(ROOT / "scripts/historical_replay_controller.py"),
                "plan",
                "--queue",
                str(queue_path),
                "--repository-root",
                str(ROOT),
                "--output",
                str(output),
            ]
            first = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            original = output.read_bytes()
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            second = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(second.returncode, 1)
            self.assertEqual(output.read_bytes(), original)


class Fixture:
    def __init__(self) -> None:
        self.authority, self.authority_raw = loaded(PLAN_PATH)
        self.profile, self.profile_raw = loaded(PROFILE_PATH)
        self.matrix, self.matrix_raw = loaded(MATRIX_PATH)
        self.contract, self.contract_raw = loaded(CONTRACT_PATH)
        requests = [
            request
            for request in self.authority["requests"]
            if request["request_id"]
            == "prr_9927609e2e68eb0fbd8c2b599571321a4923b2a180642668203894514d5675af"
        ]
        assert len(requests) == 1
        request = requests[0]
        result = request["results"][0]
        measurement = self.profile["measurement_config_digest"]
        task_id = replay_task_id(result["result_id"], measurement)
        self.task = {
            "replay_task_id": task_id,
            "result_id": result["result_id"],
            "request_id": request["request_id"],
            "historical_accepted_at": request["historical_accepted_at"],
            "owner_login": request["owner_login"],
            "declared_model": request["declared_model"],
            "problem_id": result["problem_id"],
            "statement_revision": result["statement_revision"],
            "results_repository": result["results_repository"],
            "results_commit": result["results_commit"],
            "results_path": result["results_path"],
            "result_file_sha256": result["result_file_sha256"],
            "result_tree_digest": result["result_tree_digest"],
            "source_kind": request["source"]["kind"],
            "source_repository": request["source"]["repository"],
            "source_commit": request["source"]["commit"],
            "source_visibility": request["source"]["visibility"],
            "benchmark_repository": request["benchmark"]["repository"],
            "benchmark_commit": request["benchmark"]["commit"],
            "toolchain": request["benchmark"]["toolchain"],
            "lean_toolchain_blob_sha256": request["benchmark"]["lean_toolchain_blob_sha256"],
            "workflow_run_identity_sha256": request["historical_evaluation"]["workflow_run_identity_sha256"],
            "authority_repository": "leanprover/lean-eval-submissions",
            "authority_commit": self.profile["plan_commit"],
            "authority_path": self.profile["plan_path"],
            "authority_sha256": self.profile["plan_sha256"],
            "authority_event_id": "01a035b4-d6ce-7213-8dc6-6e140474e02e",
            "authorized_at": "2026-08-25T01:00:00.000Z",
            "qualification_repository": "leanprover/lean-eval-submissions",
            "qualification_commit": PROFILE_COMMIT,
            "qualification_path": (
                "evidence/public-replay/profiles/"
                f"{self.profile['execution_profile_digest']}.json"
            ),
            "qualification_sha256": hashlib.sha256(self.profile_raw).hexdigest(),
            "qualification_event_id": "01a035b4-d6cf-718a-b5af-c903c1b66336",
            "qualified_at": "2026-08-25T01:00:00.001Z",
            "checker": "nanoda",
            "measurement_config_digest": measurement,
            "execution_profile_digest": self.profile["execution_profile_digest"],
            "status": "queued",
            "attempt": 0,
            "event_id": "01a035b4-d6d0-786d-bd03-5018f6ea4de6",
            "occurred_at": "2026-08-25T01:00:00.002Z",
        }
        self.queue = {
            "schema_version": 2,
            "environment": "production",
            "source_event_count": 4,
            "source_digest": "1" * 64,
            "tasks": [self.task],
        }

    def plan(self, queue: dict | None = None) -> dict:
        return plan_next(
            self.queue if queue is None else queue,
            self.authority,
            self.authority_raw,
            self.profile,
            self.profile_raw,
            self.matrix,
            self.matrix_raw,
            self.contract,
            self.contract_raw,
        )

    def recanonicalize_reviewed_inputs(self) -> None:
        self.authority_raw = canonical_bytes(self.authority)
        authority_sha = sha256_bytes(self.authority_raw)
        self.task["authority_sha256"] = authority_sha
        self.task["authority_path"] = f"evidence/public-replay/plans/{authority_sha}.json"
        self.profile["plan_sha256"] = authority_sha
        self.profile["plan_path"] = self.task["authority_path"]
        self.matrix["plan_sha256"] = authority_sha
        self.matrix_raw = canonical_bytes(self.matrix)
        self.profile["profile_matrix_sha256"] = sha256_bytes(self.matrix_raw)
        self.profile_raw = canonical_bytes(self.profile)
        self.task["qualification_sha256"] = sha256_bytes(self.profile_raw)

    def verdict(self, outcome: str = "completed", checker: str | None = "accepted") -> dict:
        return {
            "schema_version": 1,
            "request_id": self.task["request_id"],
            "result_id": self.task["result_id"],
            "execution_outcome": outcome,
            "checker_outcome": checker,
            "failure_reason": None,
            "statistics": {
                "checker_wall_time_ms": 11,
                "checker_retired_instructions": {"status": "measured", "value": 12},
                "build_wall_time_ms": 13,
                "build_retired_instructions": {
                    "status": "unavailable",
                    "reason": "counter_not_supported",
                },
                "lines_of_code": 14,
                "file_count": 2,
            },
        }

    def handoff(self, archive: bytes) -> dict:
        entries = [
            entry
            for entry in self.matrix["images"]
            if entry["benchmark_commit"] == self.task["benchmark_commit"]
        ]
        assert len(entries) == 1
        entry = entries[0]
        return {
            "schema_version": 1,
            "kind": "historical_public_runner_handoff",
            "contract": "historical_public_runner_v1",
            "contract_sha256": sha256_bytes(self.contract_raw),
            "plan_sha256": sha256_bytes(self.authority_raw),
            "profile_matrix_sha256": sha256_bytes(self.matrix_raw),
            "request_id": self.task["request_id"],
            "source": {
                "repository": self.task["source_repository"],
                "commit": self.task["source_commit"],
                "tree": "2" * 40,
                "visibility": "public",
                "archive_format": "git_archive_tar_gzip_v1",
                "archive_member_prefix": "source",
                "archive_sha256": hashlib.sha256(archive).hexdigest(),
                "archive_size_bytes": len(archive),
            },
            "benchmark": {
                "repository": self.task["benchmark_repository"],
                "commit": self.task["benchmark_commit"],
                "tree": entry["benchmark_tree"],
                "toolchain": self.task["toolchain"],
                "lean_toolchain_blob_sha256": self.task["lean_toolchain_blob_sha256"],
            },
            "result": {
                "result_id": self.task["result_id"],
                "problem_id": self.task["problem_id"],
                "statement_revision": self.task["statement_revision"],
                "results_repository": self.task["results_repository"],
                "results_commit": self.task["results_commit"],
                "result_tree_digest": self.task["result_tree_digest"],
            },
            "profile": {
                "matrix_entry_sha256": sha256_bytes(canonical_document_bytes(entry)),
                "qualification_status": "unqualified",
                "profile_lock": entry["profile_lock"],
            },
            "checker": "nanoda",
            "network": self.contract["network"],
            "untrusted_environment": {},
        }


class HistoricalReplayPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def test_plans_exact_first_task_without_modern_or_private_identity(self) -> None:
        plan = self.fixture.plan()
        self.assertEqual(plan["kind"], "execution")
        self.assertEqual(plan["transport"]["status"], "blocked")
        self.assertEqual(
            plan["transport"]["reason"], "historical_public_executor_not_implemented"
        )
        self.assertEqual(plan["started_transition"]["payload"]["attempt"], 1)
        rendered = json.dumps(plan)
        for forbidden in (
            "submission_id",
            "archive_repository",
            "archive_path",
            "archive_ciphertext_sha256",
            "key_envelope",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(validate_execution_plan(plan), plan)

    def test_empty_queue_is_bound_and_remains_transport_blocked(self) -> None:
        queue = copy.deepcopy(self.fixture.queue)
        queue["tasks"] = []
        plan = plan_next(queue)
        self.assertEqual(
            plan,
            {
                "schema_version": 1,
                "kind": "empty",
                "transport": {
                    "status": "blocked",
                    "reason": "historical_public_executor_not_implemented",
                    "required_contract": "historical_public_executor_v1",
                },
                "queue": {
                    "queue_environment": "production",
                    "queue_source_event_count": 4,
                    "queue_source_digest": "1" * 64,
                },
            },
        )

    def test_retry_attempt_is_monotone(self) -> None:
        queue = copy.deepcopy(self.fixture.queue)
        task = queue["tasks"][0]
        task.update(
            status="failed",
            attempt=2,
            reason_code="runner_lost",
            retryable=True,
        )
        plan = self.fixture.plan(queue)
        self.assertEqual(plan["started_transition"]["payload"]["attempt"], 3)

    def test_gist_and_attempt_limit_are_typed_blockers_before_started(self) -> None:
        fixture = Fixture()
        request = next(
            request
            for request in fixture.authority["requests"]
            if request["request_id"] == fixture.task["request_id"]
        )
        request["source"]["kind"] = "gist"
        fixture.task["source_kind"] = "gist"
        fixture.recanonicalize_reviewed_inputs()
        blocked = fixture.plan()
        self.assertEqual(blocked["kind"], "blocked")
        self.assertEqual(
            blocked["blocker"]["reason"],
            "historical_public_gist_source_adapter_not_implemented",
        )
        with self.assertRaises(HistoricalReplayControllerError):
            started_event(
                blocked,
                fixture.queue,
                "2026-08-25T01:00:00.004Z",
            )

        queue = copy.deepcopy(self.fixture.queue)
        queue["tasks"][0].update(
            status="failed",
            attempt=3,
            reason_code="runner_lost",
            retryable=True,
        )
        limited = self.fixture.plan(queue)
        self.assertEqual(limited["kind"], "blocked")
        self.assertEqual(
            limited["blocker"]["reason"],
            "historical_public_attempt_limit_reached",
        )

    def test_reconfigured_nonzero_queued_attempt_is_preserved(self) -> None:
        queue = copy.deepcopy(self.fixture.queue)
        task = queue["tasks"][0]
        task["attempt"] = 2
        task.update(
            reconfiguration_event_id="01a035b4-d6d1-7f6f-b93f-29306171a7cf",
            reconfigured_at="2026-08-25T01:00:00.003Z",
            superseded_qualification_event_id="01a035b4-d6cf-718a-b5af-c903c1b66336",
            reconfiguration_repository="leanprover/lean-eval-submissions",
            reconfiguration_commit="3" * 40,
            reconfiguration_path="evidence/public-replay/reconfigurations/" + "4" * 64 + ".json",
            reconfiguration_sha256="4" * 64,
        )
        plan = self.fixture.plan(queue)
        self.assertEqual(plan["started_transition"]["payload"]["attempt"], 3)
        del task["reconfiguration_sha256"]
        with self.assertRaises(HistoricalReplayControllerError):
            validate_queue(queue)

    def test_queue_refuses_private_shape_unknown_fields_and_identity_drift(self) -> None:
        for mutation in (
            lambda task: task.__setitem__("source_visibility", "private"),
            lambda task: task.__setitem__("submission_id", "not-authority"),
            lambda task: task.__setitem__("replay_task_id", "rt1_" + "0" * 64),
            lambda task: task.__setitem__("results_path", "results/other.json"),
            lambda task: task.__setitem__("qualification_path", "profiles/x.json"),
        ):
            queue = copy.deepcopy(self.fixture.queue)
            mutation(queue["tasks"][0])
            with self.assertRaises(HistoricalReplayControllerError):
                validate_queue(queue)

    def test_queue_refuses_duplicates_and_unsorted_work(self) -> None:
        queue = copy.deepcopy(self.fixture.queue)
        queue["tasks"].append(copy.deepcopy(queue["tasks"][0]))
        with self.assertRaises(HistoricalReplayControllerError):
            validate_queue(queue)

    def test_cross_bindings_fail_closed_under_each_authority_drift(self) -> None:
        mutations = (
            lambda fixture: fixture.queue["tasks"][0].__setitem__("source_commit", "0" * 40),
            lambda fixture: fixture.queue["tasks"][0].__setitem__("benchmark_commit", "0" * 40),
            lambda fixture: fixture.queue["tasks"][0].__setitem__("authority_sha256", "0" * 64),
            lambda fixture: fixture.queue["tasks"][0].__setitem__("qualification_sha256", "0" * 64),
            lambda fixture: next(
                request
                for request in fixture.authority["requests"]
                if request["request_id"] == fixture.task["request_id"]
            ).__setitem__("declared_model", "different authority model"),
            lambda fixture: fixture.profile.__setitem__("benchmark_commit", "0" * 40),
            lambda fixture: fixture.matrix.__setitem__("plan_sha256", "0" * 64),
            lambda fixture: fixture.contract.__setitem__("wall_time_limit_ms", 1),
        )
        for mutation in mutations:
            fixture = Fixture()
            mutation(fixture)
            with self.assertRaises(HistoricalReplayControllerError):
                fixture.plan()

    def test_qualification_schema_and_selected_matrix_entry_are_enforced(self) -> None:
        fixture = Fixture()
        fixture.profile["artifact_archive_bindings"][0]["name"] = "wrong-artifact"
        fixture.profile_raw = canonical_bytes(fixture.profile)
        fixture.task["qualification_sha256"] = sha256_bytes(fixture.profile_raw)
        with self.assertRaisesRegex(HistoricalReplayControllerError, "schema"):
            fixture.plan()

        fixture = Fixture()
        entry = next(
            entry
            for entry in fixture.matrix["images"]
            if entry["benchmark_commit"] == fixture.task["benchmark_commit"]
        )
        entry["problem_ids"].remove(fixture.task["problem_id"])
        fixture.recanonicalize_reviewed_inputs()
        with self.assertRaisesRegex(HistoricalReplayControllerError, "matrix entry"):
            fixture.plan()

    def test_plan_validation_detects_embedded_task_and_transition_tampering(self) -> None:
        for field, value in (
            ("source_commit", "0" * 40),
            ("measurement_config_digest", "0" * 64),
        ):
            plan = self.fixture.plan()
            plan["task"][field] = value
            with self.assertRaises(HistoricalReplayControllerError):
                validate_execution_plan(plan)
        plan = self.fixture.plan()
        plan["started_transition"]["payload"]["attempt"] = 7
        with self.assertRaises(HistoricalReplayControllerError):
            validate_execution_plan(plan)
        plan = self.fixture.plan()
        plan["task"]["source_kind"] = "gist"
        plan["queue"]["task_sha256"] = sha256_bytes(
            state_canonical_bytes(plan["task"])
        )
        with self.assertRaisesRegex(HistoricalReplayControllerError, "source adapter"):
            validate_execution_plan(plan)
        plan = self.fixture.plan()
        plan["task"].update(
            status="failed",
            attempt=3,
            reason_code="runner_lost",
            retryable=True,
        )
        plan["queue"]["task_sha256"] = sha256_bytes(
            state_canonical_bytes(plan["task"])
        )
        plan["started_transition"]["payload"]["attempt"] = 4
        with self.assertRaisesRegex(HistoricalReplayControllerError, "attempt limit"):
            validate_execution_plan(plan)

    def test_live_queue_binding_refuses_stale_plan(self) -> None:
        plan = self.fixture.plan()
        queue = copy.deepcopy(self.fixture.queue)
        queue["source_digest"] = "f" * 64
        with self.assertRaises(HistoricalReplayControllerError):
            validate_plan_against_queue(plan, queue)

    def test_exact_repository_blobs_are_proved(self) -> None:
        verify_repository_bindings(
            ROOT,
            self.fixture.task,
            self.fixture.authority_raw,
            self.fixture.profile_raw,
        )
        with self.assertRaises(HistoricalReplayControllerError):
            verify_repository_bindings(
                ROOT,
                self.fixture.task,
                self.fixture.authority_raw + b" ",
                self.fixture.profile_raw,
            )
        reviewed = load_reviewed_inputs(ROOT, self.fixture.task)
        self.assertEqual(reviewed[1], self.fixture.authority_raw)
        self.assertEqual(reviewed[3], self.fixture.profile_raw)
        self.assertEqual(
            reviewed[5],
            canonical_bytes(reviewed[4]),
        )

    def test_repository_binding_accepts_only_the_two_public_origin_spellings(self) -> None:
        self.assertTrue(
            _is_expected_repository_remote(
                ["https://github.com/leanprover/lean-eval-submissions"]
            )
        )
        self.assertTrue(
            _is_expected_repository_remote(
                ["https://github.com/leanprover/lean-eval-submissions.git"]
            )
        )
        for hostile in (
            "git@github.com:leanprover/lean-eval-submissions.git",
            "https://github.com/attacker/lean-eval-submissions.git",
            "https://user:token@github.com/leanprover/lean-eval-submissions.git",
        ):
            self.assertFalse(_is_expected_repository_remote([hostile]))
        self.assertFalse(_is_expected_repository_remote([]))
        self.assertFalse(
            _is_expected_repository_remote(
                [
                    "https://github.com/leanprover/lean-eval-submissions",
                    "https://github.com/leanprover/lean-eval-submissions.git",
                ]
            )
        )

    def test_repository_loading_rejects_wrong_remote_and_nonancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = pathlib.Path(directory)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/attacker/lean-eval-submissions.git",
                ],
                check=True,
            )
            with self.assertRaisesRegex(HistoricalReplayControllerError, "remote identity"):
                load_reviewed_inputs(repository, self.fixture.task)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "remote",
                    "set-url",
                    "origin",
                    "https://github.com/leanprover/lean-eval-submissions.git",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "commit", "--allow-empty", "-qm", "root"],
                check=True,
                env={
                    **os.environ,
                    "GIT_AUTHOR_NAME": "test",
                    "GIT_AUTHOR_EMAIL": "test@example.com",
                    "GIT_COMMITTER_NAME": "test",
                    "GIT_COMMITTER_EMAIL": "test@example.com",
                },
            )
            with self.assertRaisesRegex(HistoricalReplayControllerError, "not an ancestor"):
                load_reviewed_inputs(repository, self.fixture.task)


class HistoricalReplayEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()
        self.plan = self.fixture.plan()
        self.started = started_event(
            self.plan,
            self.fixture.queue,
            "2026-08-25T01:00:00.000Z",
            random_bytes=b"\x01" * 10,
        )

    def test_started_event_follows_cause_and_is_reproducible_for_fixed_randomness(self) -> None:
        self.assertEqual(self.started["occurred_at"], "2026-08-25T01:00:00.003Z")
        self.assertEqual(self.started["causation_event_id"], self.fixture.task["event_id"])
        again = started_event(
            self.plan,
            self.fixture.queue,
            "2026-08-25T00:00:00.000Z",
            random_bytes=b"\x01" * 10,
        )
        self.assertEqual(again, self.started)

    def test_all_reported_terminal_outcomes_remain_distinct(self) -> None:
        cases = (
            ("completed", "accepted", "replay.accepted"),
            ("completed", "rejected", "replay.rejected"),
            ("completed", "declined", "replay.declined"),
            ("crashed", None, "replay.crashed"),
            ("timed_out", None, "replay.timed_out"),
        )
        for outcome, checker, event_type in cases:
            transition = _terminal_transition(
                self.plan,
                self.fixture.verdict(outcome, checker),
                None,
            )
            self.assertEqual(transition["event_type"], event_type)
            self.assertEqual(transition["payload"]["attempt"], 1)
            self.assertIsNone(transition["payload"]["build_retired_instructions"])
            self.assertEqual(
                transition["payload"]["build_retired_instructions_unavailable_reason"],
                "counter_not_supported",
            )

    def test_orchestration_failures_do_not_become_checker_rejections(self) -> None:
        retryable = _terminal_transition(
            self.plan, None, "runner_lost"
        )
        permanent = _terminal_transition(
            self.plan, None, "verdict_invalid"
        )
        self.assertEqual(retryable["event_type"], "replay.failed")
        self.assertTrue(retryable["payload"]["retryable"])
        self.assertFalse(permanent["payload"]["retryable"])

    def test_terminal_refuses_wrong_verdict_and_wrong_started_identity(self) -> None:
        with self.assertRaisesRegex(
            HistoricalReplayControllerError,
            "historical_public_attempt_binding_not_implemented",
        ):
            terminal_event(
                self.plan,
                self.started,
                "2026-08-25T01:00:00.004Z",
                verdict_value=self.fixture.verdict(),
            )

    def test_required_counter_unavailability_fails_closed(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["measurement_config"]["retired_instructions"]["required"] = True
        digest = config_digest(
            "lean-eval-replay-measurement-config-v1",
            plan["measurement_config"],
        )
        task = plan["task"]
        task["measurement_config_digest"] = digest
        task["replay_task_id"] = replay_task_id(task["result_id"], digest)
        plan["queue"]["task_sha256"] = sha256_bytes(state_canonical_bytes(task))
        plan["started_transition"]["subject_id"] = task["replay_task_id"]
        self.assertEqual(validate_execution_plan(plan), plan)
        with self.assertRaisesRegex(
            HistoricalReplayControllerError,
            "required historical counters",
        ):
            _terminal_transition(plan, self.fixture.verdict(), None)


class HistoricalReplayHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def test_binds_exact_handoff_but_keeps_executor_transport_blocked(self) -> None:
        archive = b"public source archive fixture"
        handoff = self.fixture.handoff(archive)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "source.tar.gz"
            path.write_bytes(archive)
            binding = bind_handoff(
                self.fixture.plan(),
                handoff,
                path,
                self.fixture.matrix,
                self.fixture.contract,
            )
        self.assertEqual(binding["kind"], "historical_public_executor_transport_blocker")
        self.assertEqual(binding["transport"]["status"], "blocked")
        self.assertEqual(binding["attempt"], 1)

    def test_handoff_refuses_source_result_profile_and_archive_drift(self) -> None:
        archive = b"public source archive fixture"
        mutations = (
            lambda handoff: handoff["source"].__setitem__("commit", "0" * 40),
            lambda handoff: handoff["result"].__setitem__("result_id", "r2_" + "0" * 64),
            lambda handoff: handoff["profile"].__setitem__("matrix_entry_sha256", "0" * 64),
            lambda handoff: handoff.__setitem__("plan_sha256", "0" * 64),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "source.tar.gz"
            path.write_bytes(archive)
            for mutation in mutations:
                handoff = self.fixture.handoff(archive)
                mutation(handoff)
                with self.assertRaises(HistoricalReplayControllerError):
                    bind_handoff(
                        self.fixture.plan(),
                        handoff,
                        path,
                        self.fixture.matrix,
                        self.fixture.contract,
                    )
            with self.assertRaises(HistoricalReplayControllerError):
                bind_handoff(
                    self.fixture.plan(),
                    self.fixture.handoff(archive),
                    pathlib.Path(directory) / "missing.tar.gz",
                    self.fixture.matrix,
                    self.fixture.contract,
                )
            changed_matrix = copy.deepcopy(self.fixture.matrix)
            changed_matrix["plan_sha256"] = "0" * 64
            with self.assertRaises(HistoricalReplayControllerError):
                bind_handoff(
                    self.fixture.plan(),
                    self.fixture.handoff(archive),
                    path,
                    changed_matrix,
                    self.fixture.contract,
                )


class HistoricalReplayRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    @staticmethod
    def write_events(root: pathlib.Path, events: list[dict]) -> None:
        for event in events:
            event_id = event["event_id"]
            directory = root / event_id.replace("-", "")[:2]
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{event_id}.json").write_bytes(state_canonical_bytes(event))

    def events(self, *, second_task: bool = False) -> list[dict]:
        authority = {
            "schema_version": 1,
            "event_id": self.fixture.task["authority_event_id"],
            "event_type": "historical_result.replay_authorized",
            "occurred_at": self.fixture.task["authorized_at"],
            "subject_id": self.fixture.task["result_id"],
            "causation_event_id": None,
            "actor": {"kind": "system"},
            "payload": {},
        }
        enqueue = {
            "schema_version": 1,
            "event_id": self.fixture.task["event_id"],
            "event_type": "replay.enqueued",
            "occurred_at": self.fixture.task["occurred_at"],
            "subject_id": self.fixture.task["replay_task_id"],
            "causation_event_id": self.fixture.task["qualification_event_id"],
            "actor": {"kind": "system"},
            "payload": {"result_id": self.fixture.task["result_id"]},
        }
        qualification = {
            "schema_version": 1,
            "event_id": self.fixture.task["qualification_event_id"],
            "event_type": "historical_result.replay_profile_qualified",
            "occurred_at": self.fixture.task["qualified_at"],
            "subject_id": self.fixture.task["result_id"],
            "causation_event_id": authority["event_id"],
            "actor": {"kind": "system"},
            "payload": {},
        }
        started = {
            "schema_version": 1,
            "event_id": "01a035b4-d6d1-7f6f-b93f-29306171a7cf",
            "event_type": "replay.started",
            "occurred_at": "2026-08-25T01:00:00.003Z",
            "subject_id": self.fixture.task["replay_task_id"],
            "causation_event_id": enqueue["event_id"],
            "actor": {"kind": "system"},
            "payload": {"attempt": 1, "runner_profile": "fixture"},
        }
        events = [authority, qualification, enqueue, started]
        if second_task:
            result_id = "r2_" + "a" * 64
            task_id = replay_task_id(result_id, self.fixture.task["measurement_config_digest"])
            events.extend(
                [
                    {
                        **authority,
                        "event_id": "01a035b4-d6d2-7000-8000-000000000001",
                        "occurred_at": "2026-08-25T01:00:00.004Z",
                        "subject_id": result_id,
                    },
                    {
                        **qualification,
                        "event_id": "01a035b4-d6d3-7000-8000-000000000001",
                        "occurred_at": "2026-08-25T01:00:00.005Z",
                        "subject_id": result_id,
                        "causation_event_id": "01a035b4-d6d2-7000-8000-000000000001",
                    },
                    {
                        **enqueue,
                        "event_id": "01a035b4-d6d4-7000-8000-000000000001",
                        "occurred_at": "2026-08-25T01:00:00.006Z",
                        "subject_id": task_id,
                        "causation_event_id": "01a035b4-d6d3-7000-8000-000000000001",
                        "payload": {"result_id": result_id},
                    },
                    {
                        **started,
                        "event_id": "01a035b4-d6d5-7000-8000-000000000001",
                        "occurred_at": "2026-08-25T01:00:00.007Z",
                        "subject_id": task_id,
                        "causation_event_id": "01a035b4-d6d4-7000-8000-000000000001",
                    },
                ]
            )
        return events

    def test_busy_and_stale_recovery_are_closed_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            events = self.events()
            self.write_events(root, events)
            busy = recover_running(root, "2026-08-25T02:00:00.000Z")
            self.assertEqual(busy["kind"], "busy")
            failed = recover_running(
                root,
                "2026-08-25T08:00:00.004Z",
                random_bytes=b"\x05" * 10,
            )
            repeated = recover_running(
                root,
                "2026-08-25T08:00:00.004Z",
                random_bytes=b"\x05" * 10,
            )
            self.assertEqual(failed, repeated)
            self.assertEqual(failed["event"]["payload"]["reason_code"], "runner_lost")
            events.append(failed["event"])
            self.write_events(root, [failed["event"]])
            self.assertEqual(
                recover_running(root, "2026-08-25T08:00:01.000Z")["kind"],
                "none",
            )

    def test_multiple_running_historical_tasks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.write_events(root, self.events(second_task=True))
            with self.assertRaises(HistoricalReplayControllerError):
                recover_running(root, "2026-08-25T08:00:00.004Z")

    def test_recovery_rejects_invalid_causality_and_attempt(self) -> None:
        mutations = (
            lambda events: events[-1].__setitem__(
                "causation_event_id", "01a035b4-d6d5-7000-8000-000000000001"
            ),
            lambda events: events[-1]["payload"].__setitem__("attempt", 2),
        )
        for mutation in mutations:
            events = self.events()
            mutation(events)
            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                self.write_events(root, events)
                with self.assertRaises(HistoricalReplayControllerError):
                    recover_running(root, "2026-08-25T08:00:00.004Z")

    def test_reducer_sorts_authoritatively_and_rejects_unknown_or_duplicate_enqueue(self) -> None:
        events = self.events()
        authorities = {self.fixture.task["result_id"]}
        states = _historical_replay_states(list(reversed(events)), authorities)
        self.assertEqual(states[self.fixture.task["replay_task_id"]]["status"], "running")

        unknown = {
            **events[-1],
            "event_id": "01a035b4-d6d2-7000-8000-000000000001",
            "event_type": "replay.future_terminal",
            "occurred_at": "2026-08-25T01:00:00.004Z",
            "causation_event_id": events[-1]["event_id"],
            "payload": {},
        }
        with self.assertRaisesRegex(HistoricalReplayControllerError, "not recognized"):
            _historical_replay_states(events + [unknown], authorities)

        duplicate = {
            **events[2],
            "event_id": "01a035b4-d6d2-7000-8000-000000000002",
            "occurred_at": "2026-08-25T01:00:00.004Z",
        }
        with self.assertRaisesRegex(HistoricalReplayControllerError, "re-enqueue"):
            _historical_replay_states(events + [duplicate], authorities)

    def test_unrelated_nonascii_state_event_does_not_break_recovery(self) -> None:
        events = self.events()
        events.append(
            {
                "schema_version": 1,
                "event_id": "01a035b4-d6d2-7000-8000-000000000003",
                "event_type": "authentication.nonce_consumed",
                "occurred_at": "2026-08-25T01:00:00.004Z",
                "subject_id": "unrelated-β",
                "causation_event_id": None,
                "actor": {"kind": "system"},
                "payload": {"note": "β"},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.write_events(root, events)
            self.assertEqual(
                recover_running(root, "2026-08-25T02:00:00.000Z")["kind"],
                "busy",
            )

    def test_reconfiguration_chain_preserves_attempt_and_allows_withdrawal(self) -> None:
        events = self.events()
        started = events[-1]
        failed = {
            **started,
            "event_id": "01a035b4-d6d2-7000-8000-000000000010",
            "event_type": "replay.failed",
            "occurred_at": "2026-08-25T01:00:00.004Z",
            "causation_event_id": started["event_id"],
            "payload": {
                "attempt": 1,
                "reason_code": "verdict_invalid",
                "retryable": False,
            },
        }
        reconfiguration = {
            **failed,
            "event_id": "01a035b4-d6d3-7000-8000-000000000010",
            "event_type": "historical_result.replay_reconfigured",
            "occurred_at": "2026-08-25T01:00:00.005Z",
            "subject_id": self.fixture.task["result_id"],
            "causation_event_id": failed["event_id"],
            "payload": {"replay_task_id": self.fixture.task["replay_task_id"]},
        }
        withdrawal = {
            **reconfiguration,
            "event_id": "01a035b4-d6d4-7000-8000-000000000010",
            "occurred_at": "2026-08-25T01:00:00.006Z",
            "causation_event_id": reconfiguration["event_id"],
        }
        enqueue = {
            **events[2],
            "event_id": "01a035b4-d6d5-7000-8000-000000000010",
            "occurred_at": "2026-08-25T01:00:00.007Z",
            "causation_event_id": withdrawal["event_id"],
        }
        retried = {
            **started,
            "event_id": "01a035b4-d6d6-7000-8000-000000000010",
            "occurred_at": "2026-08-25T01:00:00.008Z",
            "causation_event_id": enqueue["event_id"],
            "payload": {"attempt": 2, "runner_profile": "fixture"},
        }
        states = _historical_replay_states(
            events + [failed, reconfiguration, withdrawal, enqueue, retried],
            {self.fixture.task["result_id"]},
        )
        state = states[self.fixture.task["replay_task_id"]]
        self.assertEqual((state["status"], state["attempt"]), ("running", 2))

    def test_unavailable_may_follow_nonretryable_failure(self) -> None:
        events = self.events()
        started = events[-1]
        failed = {
            **started,
            "event_id": "01a035b4-d6d2-7000-8000-000000000020",
            "event_type": "replay.failed",
            "occurred_at": "2026-08-25T01:00:00.004Z",
            "causation_event_id": started["event_id"],
            "payload": {
                "attempt": 1,
                "reason_code": "verdict_invalid",
                "retryable": False,
            },
        }
        unavailable = {
            **failed,
            "event_id": "01a035b4-d6d3-7000-8000-000000000020",
            "event_type": "replay.unavailable",
            "occurred_at": "2026-08-25T01:00:00.005Z",
            "causation_event_id": failed["event_id"],
            "payload": {"reason_code": "execution_profile_permanently_unavailable"},
        }
        states = _historical_replay_states(
            events + [failed, unavailable],
            {self.fixture.task["result_id"]},
        )
        self.assertEqual(
            states[self.fixture.task["replay_task_id"]]["status"],
            "unavailable",
        )

    def test_recovery_refuses_uuid_that_cannot_follow_causation(self) -> None:
        events = self.events()
        events[-1]["event_id"] = "7fffffff-ffff-7000-8000-000000000001"
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.write_events(root, events)
            with self.assertRaisesRegex(HistoricalReplayControllerError, "append order"):
                recover_running(
                    root,
                    "2026-08-25T08:00:00.004Z",
                    random_bytes=b"\x05" * 10,
                )

    def test_modern_running_task_is_not_claimed_by_historical_controller(self) -> None:
        events = self.events()
        events = [event for event in events if event["event_type"] != "historical_result.replay_authorized"]
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.write_events(root, events)
            self.assertEqual(
                recover_running(root, "2026-08-25T08:00:00.004Z")["kind"],
                "none",
            )


if __name__ == "__main__":
    unittest.main()
