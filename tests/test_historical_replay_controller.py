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
    _verify_qualification_source_bindings,
    bind_handoff,
    build_executor_request,
    canonical_bytes,
    load_reviewed_inputs,
    plan_next,
    recover_running,
    render_executor_config,
    replay_task_id,
    sha256_bytes,
    started_event,
    state_canonical_bytes,
    terminal_event,
    validate_execution_plan,
    validate_executor_request,
    validate_executor_verdict,
    validate_plan_against_queue,
    validate_queue,
    verify_repository_bindings,
)
from scripts.replay_orchestrator import config_digest
from scripts.results_schema import result_id as stable_result_id

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "evidence/public-replay/plans/2b00c9651f5c3f43d44e0306a8368947a4a950ab3dd1e8c9b1f283fc82101942.json"
PROFILE_PATH = ROOT / "evidence/public-replay/profiles/0886d3624de67d0ba1cb00657f66c5f7304743773a024509fceda6ae8f4ff660.json"
MATRIX_PATH = ROOT / "configuration/historical-public-replay-profile-matrix-v1.json"
CONTRACT_PATH = ROOT / "configuration/historical-public-runner-v1.json"
PROFILE_COMMIT = "7ed4a2e33cec8800f65eb6d53619c1b7fb703876"


def loaded_git(commit: str, path: pathlib.Path) -> tuple[dict, bytes]:
    raw = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{commit}:{path.relative_to(ROOT)}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout
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
        task = queue["tasks"][0]
        task["declared_model"] = "model-β"
        task["result_id"] = stable_result_id(
            task["owner_login"],
            task["declared_model"],
            task["problem_id"],
            task["statement_revision"],
        )
        task["replay_task_id"] = replay_task_id(
            task["result_id"], task["measurement_config_digest"]
        )
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
        self.profile, self.profile_raw = loaded_git(PROFILE_COMMIT, PROFILE_PATH)
        self.authority, self.authority_raw = loaded_git(
            self.profile["plan_commit"], PLAN_PATH
        )
        self.matrix, self.matrix_raw = loaded_git(PROFILE_COMMIT, MATRIX_PATH)
        self.contract, self.contract_raw = loaded_git(PROFILE_COMMIT, CONTRACT_PATH)
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

    def test_plans_exact_first_eligible_task_without_modern_or_private_identity(self) -> None:
        plan = self.fixture.plan()
        self.assertEqual(plan["kind"], "execution")
        self.assertEqual(
            plan["transport"],
            {"status": "ready", "contract": "historical_public_executor_v1"},
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

    def test_empty_queue_is_bound_to_the_ready_transport(self) -> None:
        queue = copy.deepcopy(self.fixture.queue)
        queue["tasks"] = []
        plan = plan_next(queue)
        self.assertEqual(
            plan,
            {
                "schema_version": 1,
                "kind": "empty",
                "transport": {
                    "status": "ready",
                    "contract": "historical_public_executor_v1",
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

    def test_gist_adapter_is_eligible_and_attempt_limit_remains_typed(self) -> None:
        fixture = Fixture()
        request = next(
            request
            for request in fixture.authority["requests"]
            if request["request_id"] == fixture.task["request_id"]
        )
        request["source"]["kind"] = "gist"
        fixture.task["source_kind"] = "gist"
        fixture.recanonicalize_reviewed_inputs()
        planned = fixture.plan()
        self.assertEqual(planned["kind"], "execution")
        self.assertEqual(planned["task"]["source_kind"], "gist")
        self.assertEqual(validate_execution_plan(planned), planned)

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

    def test_blocked_tasks_do_not_starve_later_eligible_work(self) -> None:
        queue = copy.deepcopy(self.fixture.queue)
        blocked = copy.deepcopy(self.fixture.task)
        for index in range(1_000):
            model = f"blocked-gist-{index}"
            result = stable_result_id(
                blocked["owner_login"],
                model,
                blocked["problem_id"],
                blocked["statement_revision"],
            )
            candidate = replay_task_id(result, blocked["measurement_config_digest"])
            if candidate < self.fixture.task["replay_task_id"]:
                break
        else:  # pragma: no cover - a cryptographic-ordering impossibility guard
            self.fail("could not construct a preceding deterministic replay identity")
        blocked.update(
            declared_model=model,
            result_id=result,
            replay_task_id=candidate,
            request_id="prr_" + hashlib.sha256(model.encode()).hexdigest(),
            source_kind="gist",
            status="failed",
            attempt=3,
            reason_code="runner_lost",
            retryable=True,
            authority_event_id="01a035b4-d6ca-7000-8000-000000000001",
            qualification_event_id="01a035b4-d6cb-7000-8000-000000000001",
            event_id="01a035b4-d6cc-7000-8000-000000000001",
        )
        queue["tasks"] = [blocked, queue["tasks"][0]]
        plan = self.fixture.plan(queue)
        self.assertEqual(plan["kind"], "execution")
        self.assertEqual(plan["task"], self.fixture.task)
        self.assertEqual(validate_plan_against_queue(plan, queue), plan)

        queue["tasks"][0].update(status="queued", attempt=0)
        for field in ("reason_code", "retryable"):
            queue["tasks"][0].pop(field)
        with self.assertRaisesRegex(HistoricalReplayControllerError, "next live queue task"):
            validate_plan_against_queue(plan, queue)

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
            lambda task: task.__setitem__("declared_model", "identity drift"),
            lambda task: task.__setitem__("declared_model", "unpaired-\ud800"),
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

    def test_qualification_image_identity_and_source_provenance_are_enforced(self) -> None:
        fixture = Fixture()
        fixture.profile["registry_manifest_digest"] = "sha256:" + "0" * 64
        fixture.recanonicalize_reviewed_inputs()
        with self.assertRaisesRegex(HistoricalReplayControllerError, "execution image"):
            fixture.plan()

        fixture = Fixture()
        fixture.profile["registry_tag"] = (
            f"{'0' * 40}-{fixture.profile['image_source_commit']}"
        )
        fixture.recanonicalize_reviewed_inputs()
        with self.assertRaisesRegex(HistoricalReplayControllerError, "producer identity"):
            fixture.plan()

        _verify_qualification_source_bindings(ROOT, self.fixture.profile)
        for field in (
            "qualification_workflow_sha256",
            "qualification_controller_sha256",
            "qualification_contract_sha256",
        ):
            profile = copy.deepcopy(self.fixture.profile)
            profile[field] = "0" * 64
            with self.assertRaisesRegex(
                HistoricalReplayControllerError, "exact reviewed Git blobs"
            ):
                _verify_qualification_source_bindings(ROOT, profile)
        profile = copy.deepcopy(self.fixture.profile)
        profile["controller_source_commit"] = "0" * 40
        with self.assertRaisesRegex(HistoricalReplayControllerError, "not an ancestor"):
            _verify_qualification_source_bindings(ROOT, profile)

        profile = copy.deepcopy(self.fixture.profile)
        profile["image_source_commit"] = "0" * 40
        with self.assertRaisesRegex(HistoricalReplayControllerError, "unavailable"):
            _verify_qualification_source_bindings(ROOT, profile)

        for field in ("profile_matrix_sha256", "runner_contract_sha256"):
            profile = copy.deepcopy(self.fixture.profile)
            profile[field] = "0" * 64
            with self.assertRaisesRegex(
                HistoricalReplayControllerError, "image source differs"
            ):
                _verify_qualification_source_bindings(ROOT, profile)

        with tempfile.TemporaryDirectory() as directory:
            repository = pathlib.Path(directory) / "submissions"
            subprocess.run(
                ["git", "clone", "--quiet", "--shared", str(ROOT), str(repository)],
                check=True,
            )
            empty_tree = subprocess.run(
                ["git", "-C", str(repository), "mktree"],
                check=True,
                input=b"",
                stdout=subprocess.PIPE,
            ).stdout.decode().strip()
            environment = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Controller Test",
                "GIT_AUTHOR_EMAIL": "controller-test@example.invalid",
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
                "GIT_COMMITTER_NAME": "Controller Test",
                "GIT_COMMITTER_EMAIL": "controller-test@example.invalid",
                "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
            }
            unrelated = subprocess.run(
                ["git", "-C", str(repository), "commit-tree", empty_tree],
                check=True,
                input=b"unrelated image source\n",
                stdout=subprocess.PIPE,
                env=environment,
            ).stdout.decode().strip()
            profile = copy.deepcopy(self.fixture.profile)
            profile["image_source_commit"] = unrelated
            with self.assertRaisesRegex(HistoricalReplayControllerError, "not an ancestor"):
                _verify_qualification_source_bindings(repository, profile)

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
        plan["task"]["source_kind"] = "private_repo"
        plan["queue"]["task_sha256"] = sha256_bytes(
            state_canonical_bytes(plan["task"])
        )
        with self.assertRaisesRegex(HistoricalReplayControllerError, "source_kind"):
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
            "historical_public_attempt_binding_required",
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

    def test_binds_exact_handoff_to_ready_executor_transport(self) -> None:
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
        self.assertEqual(binding["kind"], "historical_public_executor_binding")
        self.assertEqual(binding["transport"]["status"], "ready")
        self.assertEqual(binding["attempt"], 1)

    def test_executor_envelope_binds_attempt_runtime_and_terminal_event(self) -> None:
        archive = b"public source archive fixture"
        plan = self.fixture.plan()
        handoff = self.fixture.handoff(archive)
        started = started_event(
            plan,
            self.fixture.queue,
            "2026-08-25T01:00:00.000Z",
            random_bytes=b"\x01" * 10,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "source.tar.gz"
            path.write_bytes(archive)
            request = build_executor_request(
                plan,
                handoff,
                path,
                self.fixture.matrix,
                self.fixture.contract,
                "4" * 64,
            )
        response = {
            "schema_version": 1,
            "contract": "historical_public_executor_v1",
            **{
                field: request[field]
                for field in (
                    "runner_nonce",
                    "replay_task_id",
                    "attempt",
                    "handoff_sha256",
                    "source_archive_sha256",
                    "execution_profile_digest",
                    "measurement_config_digest",
                    "vm_image_digest",
                )
            },
            "runner_verdict": self.fixture.verdict(),
            "destruction": "confirmed",
        }
        self.assertEqual(validate_executor_request(plan, started, request), request)
        self.assertEqual(
            validate_executor_verdict(request, response),
            self.fixture.verdict(),
        )
        terminal = terminal_event(
            plan,
            started,
            "2026-08-25T01:00:00.004Z",
            executor_request_value=request,
            verdict_value=response,
            random_bytes=b"\x02" * 10,
        )
        self.assertEqual(terminal["event_type"], "replay.accepted")
        self.assertEqual(terminal["subject_id"], request["replay_task_id"])
        self.assertEqual(terminal["causation_event_id"], started["event_id"])
        self.assertEqual(terminal["payload"]["attempt"], request["attempt"])

        for field, changed in (
            ("runner_nonce", "0" * 64),
            ("replay_task_id", "rt1_" + "0" * 64),
            ("attempt", 2),
            ("handoff_sha256", "0" * 64),
            ("source_archive_sha256", "0" * 64),
            ("execution_profile_digest", "0" * 64),
            ("measurement_config_digest", "0" * 64),
            ("vm_image_digest", "sha256:" + "0" * 64),
        ):
            with self.subTest(field=field):
                drifted = copy.deepcopy(response)
                drifted[field] = changed
                with self.assertRaises(HistoricalReplayControllerError):
                    terminal_event(
                        plan,
                        started,
                        "2026-08-25T01:00:00.004Z",
                        executor_request_value=request,
                        verdict_value=drifted,
                    )

        unconfirmed = copy.deepcopy(response)
        unconfirmed["destruction"] = "pending"
        with self.assertRaises(HistoricalReplayControllerError):
            terminal_event(
                plan,
                started,
                "2026-08-25T01:00:00.004Z",
                executor_request_value=request,
                verdict_value=unconfirmed,
            )

    def test_production_executor_config_is_exact_and_ordinary_replay_stays_dark(self) -> None:
        plan = self.fixture.plan()
        rendered = render_executor_config(
            plan,
            self.fixture.profile,
            "a" * 32,
            "b" * 40,
        )
        production = rendered["env"]["production"]
        variables = production["vars"]
        self.assertEqual(production["name"], "lean-eval-historical-public-replay")
        self.assertEqual(production["containers"][0]["max_instances"], 1)
        self.assertEqual(production["containers"][0]["instance_type"], "standard-4")
        self.assertEqual(
            production["containers"][0]["image"],
            "registry.cloudflare.com/"
            + "a" * 32
            + "/lean-eval-historical-public-v1:"
            + self.fixture.profile["registry_tag"]
            + "@"
            + self.fixture.profile["registry_manifest_digest"],
        )
        self.assertEqual(variables["REPLAY_ENABLED"], "false")
        self.assertEqual(variables["HISTORICAL_PUBLIC_REPLAY_ENABLED"], "true")
        self.assertEqual(variables["STAGING_ACCEPTANCE_ENABLED"], "false")
        self.assertEqual(variables["DEPLOYED_COMMIT"], "b" * 40)
        self.assertEqual(variables["DEPLOYMENT_ENVIRONMENT"], "production")
        self.assertEqual(
            variables["GITHUB_OIDC_AUDIENCE"],
            "lean-eval-historical-public-replay-production",
        )
        self.assertEqual(variables["GITHUB_OIDC_ENVIRONMENT"], "replay-production")
        self.assertEqual(
            variables["REVIEWED_EXECUTION_PROFILE_DIGEST"],
            plan["task"]["execution_profile_digest"],
        )
        self.assertEqual(
            variables["REVIEWED_MEASUREMENT_CONFIG_DIGEST"],
            plan["task"]["measurement_config_digest"],
        )
        self.assertEqual(
            variables["REVIEWED_VM_IMAGE_DIGEST"],
            plan["execution_profile"]["vm_image_digest"],
        )

        changed = copy.deepcopy(self.fixture.profile)
        changed["registry_manifest_digest"] = "sha256:" + "0" * 64
        with self.assertRaises(HistoricalReplayControllerError):
            render_executor_config(plan, changed, "a" * 32, "b" * 40)

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

    @staticmethod
    def modern_running_events() -> list[dict]:
        submission_id = "0198abcd-0000-7000-8000-000000000002"
        result_id = "r2_80f02f892fb0b90474675aa0b572252a8758faf74b95400521e9da724583931f"
        replay_id = "rt1_a49738fb7237c613ff62c98e58ff6e2bfff77b75a215c2e43c13854d97ca7cc1"
        ids = [f"0198abcd-0000-7000-8000-{number:012x}" for number in range(1, 9)]
        return [
            {
                "schema_version": 1, "event_id": ids[0], "event_type": "system.initialized",
                "occurred_at": "2026-08-20T06:07:01.000Z", "subject_id": "state_production",
                "causation_event_id": None, "actor": {"kind": "system"},
                "payload": {"environment": "production"},
            },
            {
                "schema_version": 1, "event_id": ids[1], "event_type": "submission.received",
                "occurred_at": "2026-08-20T06:07:02.000Z", "subject_id": submission_id,
                "causation_event_id": None, "actor": {"kind": "github", "login": "kim-em"},
                "payload": {
                    "problem_id": "two_plus_two", "statement_revision": 1,
                    "declared_model": "Example Model", "source_repository": "example/submission",
                    "source_commit": "a" * 40, "source_visibility": "private",
                    "publication_choice": "scheduled",
                },
            },
            {
                "schema_version": 1, "event_id": ids[2], "event_type": "archive.completed",
                "occurred_at": "2026-08-20T06:07:03.000Z", "subject_id": submission_id,
                "causation_event_id": ids[1], "actor": {"kind": "system"},
                "payload": {
                    "archive_repository": "leanprover/lean-eval-audit",
                    "archive_commit": "b" * 40,
                    "archive_path": f"archives/01/{submission_id}.tar.age",
                    "archive_ciphertext_sha256": "c" * 64, "encrypted": True,
                },
            },
            {
                "schema_version": 1, "event_id": ids[3], "event_type": "evaluation.started",
                "occurred_at": "2026-08-20T06:07:04.000Z", "subject_id": submission_id,
                "causation_event_id": ids[2], "actor": {"kind": "system"},
                "payload": {
                    "attempt": 1, "benchmark_repository": "leanprover/lean-eval",
                    "benchmark_commit": "d" * 40, "toolchain": "leanprover/lean4:v4.32.0",
                },
            },
            {
                "schema_version": 1, "event_id": ids[4], "event_type": "evaluation.accepted",
                "occurred_at": "2026-08-20T06:07:05.000Z", "subject_id": submission_id,
                "causation_event_id": ids[3], "actor": {"kind": "system"},
                "payload": {"attempt": 1, "evaluator_version": "v1"},
            },
            {
                "schema_version": 1, "event_id": ids[5], "event_type": "result.recorded",
                "occurred_at": "2026-08-20T06:07:06.000Z", "subject_id": result_id,
                "causation_event_id": ids[4], "actor": {"kind": "system"},
                "payload": {
                    "submission_id": submission_id, "problem_id": "two_plus_two",
                    "statement_revision": 1, "result_commit": "e" * 40,
                    "tree_digest": "f" * 64,
                },
            },
            {
                "schema_version": 1, "event_id": ids[6], "event_type": "replay.enqueued",
                "occurred_at": "2026-08-20T06:07:07.000Z", "subject_id": replay_id,
                "causation_event_id": ids[5], "actor": {"kind": "system"},
                "payload": {
                    "result_id": result_id, "measurement_config_digest": "1" * 64,
                    "execution_profile_digest": "2" * 64, "checker": "nanoda",
                },
            },
            {
                "schema_version": 1, "event_id": ids[7], "event_type": "replay.started",
                "occurred_at": "2026-08-20T06:07:08.000Z", "subject_id": replay_id,
                "causation_event_id": ids[6], "actor": {"kind": "system"},
                "payload": {"attempt": 1, "runner_profile": "lean-eval-disposable-v1"},
            },
        ]

    def events(self, *, second_task: bool = False) -> list[dict]:
        task = self.fixture.task
        initialized = {
            "schema_version": 1,
            "event_id": "01a035b4-d6cd-7000-8000-000000000001",
            "event_type": "system.initialized",
            "occurred_at": "2026-08-25T00:59:59.999Z",
            "subject_id": "state_production",
            "causation_event_id": None,
            "actor": {"kind": "system"},
            "payload": {"environment": "production"},
        }
        authority = {
            "schema_version": 1,
            "event_id": self.fixture.task["authority_event_id"],
            "event_type": "historical_result.replay_authorized",
            "occurred_at": self.fixture.task["authorized_at"],
            "subject_id": self.fixture.task["result_id"],
            "causation_event_id": None,
            "actor": {"kind": "system"},
            "payload": {
                field: task[field]
                for field in (
                    "request_id", "historical_accepted_at", "owner_login",
                    "declared_model", "problem_id", "statement_revision",
                    "results_repository", "results_commit", "results_path",
                    "result_file_sha256", "result_tree_digest", "source_kind",
                    "source_repository", "source_commit", "source_visibility",
                    "benchmark_repository", "benchmark_commit", "toolchain",
                    "lean_toolchain_blob_sha256", "workflow_run_identity_sha256",
                    "authority_repository", "authority_commit", "authority_path",
                    "authority_sha256",
                )
            },
        }
        enqueue = {
            "schema_version": 1,
            "event_id": self.fixture.task["event_id"],
            "event_type": "replay.enqueued",
            "occurred_at": self.fixture.task["occurred_at"],
            "subject_id": self.fixture.task["replay_task_id"],
            "causation_event_id": self.fixture.task["qualification_event_id"],
            "actor": {"kind": "system"},
            "payload": {
                field: task[field]
                for field in (
                    "result_id", "measurement_config_digest",
                    "execution_profile_digest", "checker", "benchmark_commit",
                )
            },
        }
        qualification = {
            "schema_version": 1,
            "event_id": self.fixture.task["qualification_event_id"],
            "event_type": "historical_result.replay_profile_qualified",
            "occurred_at": self.fixture.task["qualified_at"],
            "subject_id": self.fixture.task["result_id"],
            "causation_event_id": authority["event_id"],
            "actor": {"kind": "system"},
            "payload": {
                field: task[field]
                for field in (
                    "toolchain", "benchmark_commit", "measurement_config_digest",
                    "execution_profile_digest", "checker",
                    "qualification_repository", "qualification_commit",
                    "qualification_path", "qualification_sha256",
                )
            },
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
        events = [initialized, authority, qualification, enqueue, started]
        if second_task:
            second_model = "second-model"
            result_id = stable_result_id(
                task["owner_login"], second_model, task["problem_id"],
                task["statement_revision"],
            )
            task_id = replay_task_id(result_id, self.fixture.task["measurement_config_digest"])
            events.extend(
                [
                    {
                        **authority,
                        "event_id": "01a035b4-d6d2-7000-8000-000000000001",
                        "occurred_at": "2026-08-25T01:00:00.004Z",
                        "subject_id": result_id,
                        "payload": {
                            **authority["payload"],
                            "request_id": "prr_" + "a" * 64,
                            "declared_model": second_model,
                            "workflow_run_identity_sha256": "b" * 64,
                        },
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
                        "payload": {**enqueue["payload"], "result_id": result_id},
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
            busy = recover_running(root, "2026-08-25T02:00:00.000Z", state_validated=True)
            self.assertEqual(busy["kind"], "busy")
            stale = recover_running(
                root,
                "2026-08-25T08:00:00.004Z",
                state_validated=True,
            )
            self.assertEqual(stale, {
                "schema_version": 1,
                "kind": "cleanup_required",
                "replay_task_id": events[-1]["subject_id"],
                "attempt": 1,
            })
            confirmation = {
                "schema_version": 1,
                "replay_task_id": events[-1]["subject_id"],
                "attempt": 1,
                "destruction": "confirmed",
            }
            failed = recover_running(
                root,
                "2026-08-25T08:00:00.004Z",
                state_validated=True,
                cleanup_confirmation_value=confirmation,
                random_bytes=b"\x05" * 10,
            )
            repeated = recover_running(
                root,
                "2026-08-25T08:00:00.004Z",
                state_validated=True,
                cleanup_confirmation_value=confirmation,
                random_bytes=b"\x05" * 10,
            )
            self.assertEqual(failed, repeated)
            self.assertEqual(failed["event"]["payload"]["reason_code"], "runner_lost")
            events.append(failed["event"])
            self.write_events(root, [failed["event"]])
            self.assertEqual(
                recover_running(root, "2026-08-25T08:00:01.000Z", state_validated=True)["kind"],
                "none",
            )

    def test_stale_recovery_rejects_unconfirmed_or_mismatched_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            events = self.events()
            self.write_events(root, events)
            base = {
                "schema_version": 1,
                "replay_task_id": events[-1]["subject_id"],
                "attempt": 1,
                "destruction": "confirmed",
            }
            mutations = (
                {**base, "destruction": "pending"},
                {**base, "replay_task_id": "rt1_" + "f" * 64},
                {**base, "attempt": 2},
                {**base, "extra": True},
            )
            for confirmation in mutations:
                with self.assertRaises(HistoricalReplayControllerError):
                    recover_running(
                        root,
                        "2026-08-25T08:00:00.004Z",
                        state_validated=True,
                        cleanup_confirmation_value=confirmation,
                    )

    def test_multiple_running_historical_tasks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.write_events(root, self.events(second_task=True))
            with self.assertRaises(HistoricalReplayControllerError):
                recover_running(root, "2026-08-25T08:00:00.004Z", state_validated=True)

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
                    recover_running(root, "2026-08-25T08:00:00.004Z", state_validated=True)

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
            **events[3],
            "event_id": "01a035b4-d6d2-7000-8000-000000000002",
            "occurred_at": "2026-08-25T01:00:00.004Z",
        }
        with self.assertRaisesRegex(HistoricalReplayControllerError, "re-enqueue"):
            _historical_replay_states(events + [duplicate], authorities)

    def test_recovery_requires_authoritative_state_validation(self) -> None:
        with self.assertRaisesRegex(HistoricalReplayControllerError, "State-validated"):
            recover_running(
                pathlib.Path("not-read"),
                "2026-08-25T02:00:00.000Z",
                state_validated=False,
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
        replacement_qualification = copy.deepcopy(events[2])
        replacement_qualification.update(
            event_id="01a035b4-d6d3-7000-8000-000000000010",
            occurred_at="2026-08-25T01:00:00.005Z",
        )
        replacement_qualification["payload"].update(
            execution_profile_digest="5" * 64,
            qualification_path="evidence/public-replay/profiles/" + "5" * 64 + ".json",
            qualification_sha256="6" * 64,
        )
        reconfiguration = {
            **failed,
            "event_id": "01a035b4-d6d4-7000-8000-000000000010",
            "event_type": "historical_result.replay_reconfigured",
            "occurred_at": "2026-08-25T01:00:00.006Z",
            "subject_id": self.fixture.task["result_id"],
            "causation_event_id": failed["event_id"],
            "payload": {
                "replay_task_id": self.fixture.task["replay_task_id"],
                "measurement_config_digest": self.fixture.task["measurement_config_digest"],
                "checker": "nanoda",
                "superseded_enqueue_event_id": events[3]["event_id"],
                "superseded_qualification_event_id": events[2]["event_id"],
                "superseded_execution_profile_digest": self.fixture.task["execution_profile_digest"],
                "replacement_qualification_event_id": replacement_qualification["event_id"],
                "replacement_execution_profile_digest": "5" * 64,
                "reason_code": "profile_execution_failed",
                "reconfiguration_repository": "leanprover/lean-eval-submissions",
                "reconfiguration_commit": "3" * 40,
                "reconfiguration_path": "evidence/public-replay/reconfigurations/" + "4" * 64 + ".json",
                "reconfiguration_sha256": "4" * 64,
            },
        }
        third_qualification = copy.deepcopy(replacement_qualification)
        third_qualification.update(
            event_id="01a035b4-d6d5-7000-8000-000000000010",
            occurred_at="2026-08-25T01:00:00.007Z",
        )
        third_qualification["payload"].update(
            execution_profile_digest="7" * 64,
            qualification_path="evidence/public-replay/profiles/" + "7" * 64 + ".json",
            qualification_sha256="8" * 64,
        )
        withdrawal = {
            **reconfiguration,
            "event_id": "01a035b4-d6d6-7000-8000-000000000010",
            "occurred_at": "2026-08-25T01:00:00.008Z",
            "causation_event_id": reconfiguration["event_id"],
            "payload": {
                **reconfiguration["payload"],
                "superseded_qualification_event_id": replacement_qualification["event_id"],
                "superseded_execution_profile_digest": "5" * 64,
                "replacement_qualification_event_id": third_qualification["event_id"],
                "replacement_execution_profile_digest": "7" * 64,
                "reason_code": "profile_replacement_withdrawn",
                "reconfiguration_path": "evidence/public-replay/reconfigurations/" + "9" * 64 + ".json",
                "reconfiguration_sha256": "9" * 64,
            },
        }
        enqueue = {
            **events[3],
            "event_id": "01a035b4-d6d7-7000-8000-000000000010",
            "occurred_at": "2026-08-25T01:00:00.009Z",
            "causation_event_id": withdrawal["event_id"],
            "payload": {**events[3]["payload"], "execution_profile_digest": "7" * 64},
        }
        retried = {
            **started,
            "event_id": "01a035b4-d6d8-7000-8000-000000000010",
            "occurred_at": "2026-08-25T01:00:00.010Z",
            "causation_event_id": enqueue["event_id"],
            "payload": {"attempt": 2, "runner_profile": "fixture"},
        }
        states = _historical_replay_states(
            events + [failed, replacement_qualification, reconfiguration,
                      third_qualification, withdrawal, enqueue, retried],
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
            "payload": {
                "reason_code": "execution_profile_permanently_unavailable",
                "evidence_repository": "leanprover/lean-eval-submissions",
                "evidence_commit": "f" * 40,
                "evidence_path": "evidence/public-replay/unavailable.json",
                "evidence_sha256": "0" * 64,
            },
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
                    state_validated=True,
                    cleanup_confirmation_value={
                        "schema_version": 1,
                        "replay_task_id": events[-1]["subject_id"],
                        "attempt": 1,
                        "destruction": "confirmed",
                    },
                    random_bytes=b"\x05" * 10,
                )

    def test_modern_running_task_is_not_claimed_by_historical_controller(self) -> None:
        events = self.modern_running_events()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.write_events(root, events)
            self.assertEqual(
                recover_running(root, "2026-08-25T08:00:00.004Z", state_validated=True)["kind"],
                "none",
            )


if __name__ == "__main__":
    unittest.main()
