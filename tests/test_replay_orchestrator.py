from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from replay_orchestrator import (  # noqa: E402
    ReplayError,
    config_digest,
    exercise_private_provider_for_test,
    plan_next,
    private_replay_locator,
    replay_task_id,
    run_with_disposable_vm,
    terminal_transition,
    unavailable_transition,
    validate_execution_profile,
    validate_execution_request,
    validate_queue,
)

FIXTURES = ROOT / "tests" / "fixtures"
STARTED_EVENT_ID = "0198abcd-0000-7000-8000-000000000008"
UNAVAILABILITY_EVIDENCE = {
    "repository": "leanprover/lean-eval-state",
    "commit": "8" * 40,
    "path": "evidence/replay/source-ref-missing.json",
    "sha256": "9" * 64,
}


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RecordingPrivateProvider:
    def __init__(self) -> None:
        self.locators: list[dict[str, object]] = []

    def prepare(self, locator: dict[str, object]) -> dict[str, object]:
        self.locators.append(locator)
        return {"prepared": False, "reason": "test_double_only"}


class RecordingDisposableVm:
    def __init__(self, verdict: dict[str, object]) -> None:
        self.verdict = verdict
        self.requests: list[dict[str, object]] = []
        self.destroyed = False

    def run(self, request: dict[str, object]) -> dict[str, object]:
        self.requests.append(request)
        return self.verdict

    def destroy(self) -> None:
        self.destroyed = True


class ReplayOrchestratorTests(unittest.TestCase):
    def inputs(self) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        return (
            load_fixture("replay-queue-public-v1.json"),
            load_fixture("replay-execution-profile-v1.json"),
            load_fixture("replay-measurement-config-v1.json"),
        )

    def test_public_plan_is_deterministic_and_exactly_pinned(self) -> None:
        queue, profile, measurement = self.inputs()
        first = plan_next(queue, profile, measurement)
        second = plan_next(copy.deepcopy(queue), copy.deepcopy(profile), copy.deepcopy(measurement))
        self.assertEqual(first, second)
        self.assertEqual(first["kind"], "execution")
        request = first["request"]
        self.assertEqual(request["source"], {
            "repository": "example/public-submission",
            "commit": "a" * 40,
            "visibility": "public",
        })
        self.assertEqual(request["benchmark"], {
            "repository": "leanprover/lean-eval",
            "commit": "d" * 40,
            "toolchain": "leanprover/lean4:v4.32.0",
        })
        self.assertEqual(request["execution_profile"], profile)
        self.assertEqual(request["measurement_config"], measurement)
        self.assertEqual(request["untrusted_environment"], {})
        self.assertEqual(request["network"]["untrusted_execution_phase"], "disabled")
        self.assertEqual(first["started_transition"]["payload"]["attempt"], 1)

    def test_disposable_runner_can_validate_request_without_state_transition(self) -> None:
        queue, profile, measurement = self.inputs()
        request = plan_next(queue, profile, measurement)["request"]
        self.assertIs(validate_execution_request(request), request)
        request["untrusted_environment"] = {"TOKEN": "forbidden"}
        with self.assertRaisesRegex(ReplayError, "environment must be empty"):
            validate_execution_request(request)

    def test_queue_recomputes_identity_and_rejects_unknown_fields(self) -> None:
        queue, _, _ = self.inputs()
        queue["tasks"][0]["replay_task_id"] = "rt1_" + "0" * 64
        with self.assertRaisesRegex(ReplayError, "locked identity"):
            validate_queue(queue)

        queue, _, _ = self.inputs()
        queue["tasks"][0]["surprise"] = True
        with self.assertRaisesRegex(ReplayError, "fields are not canonical"):
            validate_queue(queue)

        queue, _, _ = self.inputs()
        queue["tasks"][0]["occurred_at"] = "2026-02-30T06:07:07.000Z"
        with self.assertRaisesRegex(ReplayError, "real calendar timestamp"):
            validate_queue(queue)

        queue, _, _ = self.inputs()
        queue["tasks"][0]["statement_revision"] = 9_007_199_254_740_992
        with self.assertRaisesRegex(ReplayError, "IEEE-754-safe"):
            validate_queue(queue)

    def test_profile_and_measurement_digests_are_domain_separated_and_locked(self) -> None:
        queue, profile, measurement = self.inputs()
        self.assertEqual(
            config_digest("lean-eval-replay-execution-profile-v1", profile),
            queue["tasks"][0]["execution_profile_digest"],
        )
        self.assertEqual(
            config_digest("lean-eval-replay-measurement-config-v1", measurement),
            queue["tasks"][0]["measurement_config_digest"],
        )
        measurement["wall_time_limit_ms"] = 1
        with self.assertRaisesRegex(ReplayError, "measurement configuration digest"):
            plan_next(queue, profile, measurement)

    def test_exact_prerelease_toolchain_is_a_registered_profile(self) -> None:
        _, profile, _ = self.inputs()
        profile["toolchain"] = "leanprover/lean4:v4.30.0-rc2"
        self.assertIs(validate_execution_profile(profile), profile)
        profile["toolchain"] = "leanprover/lean4:v4.30.0-rc"
        with self.assertRaisesRegex(ReplayError, "toolchain"):
            validate_execution_profile(profile)

    def test_retry_consumes_failed_queue_state_with_next_attempt(self) -> None:
        queue, profile, measurement = self.inputs()
        task = queue["tasks"][0]
        task.update(
            status="failed",
            attempt=2,
            reason_code="runner_lost",
            retryable=True,
            event_id="0198abcd-0000-7000-8000-000000000009",
        )
        plan = plan_next(queue, profile, measurement)
        self.assertEqual(plan["request"]["attempt"], 3)
        self.assertEqual(plan["started_transition"]["payload"]["attempt"], 3)
        self.assertEqual(
            plan["started_transition"]["causation_event_id"],
            task["event_id"],
        )

    def test_fourth_attempt_is_terminal_and_fifth_attempt_is_impossible(self) -> None:
        queue, profile, measurement = self.inputs()
        task = queue["tasks"][0]
        task.update(
            status="failed",
            attempt=3,
            reason_code="runner_lost",
            retryable=True,
            event_id="0198abcd-0000-7000-8000-000000000009",
        )
        plan = plan_next(queue, profile, measurement)
        verdict = load_fixture("replay-verdict-accepted-v1.json")
        verdict.update(
            attempt=4,
            execution_outcome="failed",
            checker_outcome=None,
            failure_reason="runner_lost",
            statistics=None,
        )
        terminal = terminal_transition(plan, verdict, STARTED_EVENT_ID)
        self.assertEqual(terminal["event_type"], "replay.failed")
        self.assertFalse(terminal["payload"]["retryable"])

        task["attempt"] = 4
        with self.assertRaisesRegex(ReplayError, "failed queue task"):
            validate_queue(queue)
        request = plan["request"]
        request["attempt"] = 5
        with self.assertRaisesRegex(ReplayError, "no greater than 4"):
            validate_execution_request(request)

    def test_private_source_plans_the_exact_d6_archive_without_git_locator(self) -> None:
        queue, profile, measurement = self.inputs()
        task = queue["tasks"][0]
        task["source_visibility"] = "private"
        plan = plan_next(queue, profile, measurement)
        self.assertEqual(plan["kind"], "execution")
        self.assertEqual(plan["request"]["source"], {
            "visibility": "private",
            "archive": private_replay_locator(task),
        })
        self.assertNotIn("repository", plan["request"]["source"])
        self.assertNotIn("commit", plan["request"]["source"])
        self.assertEqual(
            plan["request"]["network"]["fetch_phase"],
            "controller_pinned_archive_only",
        )
        self.assertEqual(
            plan["started_transition"]["causation_event_id"],
            task["event_id"],
        )

    def test_permanent_unavailability_requires_evidence(self) -> None:
        queue, _, _ = self.inputs()
        transition = unavailable_transition(
            queue,
            "source_ref_permanently_unavailable",
            UNAVAILABILITY_EVIDENCE,
        )
        self.assertEqual(transition, {
            "event_type": "replay.unavailable",
            "subject_id": queue["tasks"][0]["replay_task_id"],
            "causation_event_id": queue["tasks"][0]["event_id"],
            "payload": {
                "reason_code": "source_ref_permanently_unavailable",
                "evidence_repository": "leanprover/lean-eval-state",
                "evidence_commit": "8" * 40,
                "evidence_path": "evidence/replay/source-ref-missing.json",
                "evidence_sha256": "9" * 64,
            },
        })
        with self.assertRaisesRegex(ReplayError, "not registered"):
            unavailable_transition(queue, "unknown_reason", UNAVAILABILITY_EVIDENCE)
        with self.assertRaisesRegex(ReplayError, "not registered"):
            unavailable_transition(
                queue,
                "transient_provider_failure",
                UNAVAILABILITY_EVIDENCE,
            )
        invalid_evidence = {**UNAVAILABILITY_EVIDENCE, "path": "../claim.json"}
        with self.assertRaisesRegex(ReplayError, "safe repository path"):
            unavailable_transition(
                queue,
                "source_ref_permanently_unavailable",
                invalid_evidence,
            )

    def test_private_provider_boundary_uses_exact_locator_but_no_key_design(self) -> None:
        queue, _, _ = self.inputs()
        task = queue["tasks"][0]
        task["source_visibility"] = "private"
        provider = RecordingPrivateProvider()
        result = exercise_private_provider_for_test(task, provider)
        self.assertEqual(result, {"prepared": False, "reason": "test_double_only"})
        self.assertEqual(provider.locators, [private_replay_locator(task)])
        locator_text = json.dumps(provider.locators[0], sort_keys=True).lower()
        for forbidden in ("private_key", "master_key", "unwrap", "capability", "secret"):
            self.assertNotIn(forbidden, locator_text)

    def test_private_execution_plan_supports_the_normal_terminal_contract(self) -> None:
        queue, profile, measurement = self.inputs()
        queue["tasks"][0]["source_visibility"] = "private"
        plan = plan_next(queue, profile, measurement)
        transition = terminal_transition(
            plan,
            load_fixture("replay-verdict-accepted-v1.json"),
            "0198abcd-0000-7000-8000-000000000008",
        )
        self.assertEqual(transition["event_type"], "replay.accepted")

    def test_archive_path_is_correlated_to_submission_uuid(self) -> None:
        queue, _, _ = self.inputs()
        queue["tasks"][0]["archive_path"] = (
            "archives/ff/0198abcd-1111-7000-8000-000000000001.tar.age"
        )
        with self.assertRaisesRegex(ReplayError, "archive_path"):
            validate_queue(queue)

    def test_writer_credentials_cannot_enter_untrusted_request(self) -> None:
        queue, profile, measurement = self.inputs()
        request = plan_next(queue, profile, measurement)["request"]
        serialized = json.dumps(request, sort_keys=True).lower()
        for forbidden in (
            "actions_write",
            "archiver_token",
            "github_token",
            "private_key",
            "release_writer",
            "results_writer",
            "state_writer",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(request["untrusted_environment"], {})
        plan = plan_next(queue, profile, measurement)
        plan["request"]["untrusted_environment"] = {"STATE_WRITER_TOKEN": "secret"}
        with self.assertRaisesRegex(ReplayError, "environment must be empty"):
            terminal_transition(
                plan,
                load_fixture("replay-verdict-accepted-v1.json"),
                STARTED_EVENT_ID,
            )

    def test_checker_verdict_maps_to_separate_state_outcome(self) -> None:
        queue, profile, measurement = self.inputs()
        plan = plan_next(queue, profile, measurement)
        verdict = load_fixture("replay-verdict-accepted-v1.json")
        transition = terminal_transition(plan, verdict, STARTED_EVENT_ID)
        self.assertEqual(transition["event_type"], "replay.accepted")
        self.assertEqual(transition["causation_event_id"], STARTED_EVENT_ID)
        self.assertEqual(transition["payload"], {
            "attempt": 1,
            "checker": "nanoda",
            "checker_wall_time_ms": 1234,
            "checker_retired_instructions": 123456,
            "checker_retired_instructions_unavailable_reason": None,
            "build_wall_time_ms": 4321,
            "build_retired_instructions": 987654,
            "build_retired_instructions_unavailable_reason": None,
            "lines_of_code": 87,
            "file_count": 3,
        })

        verdict["checker_outcome"] = "rejected"
        verdict["statistics"]["checker_retired_instructions"] = {
            "status": "unavailable",
            "reason": "counter_not_supported",
        }
        transition = terminal_transition(plan, verdict, STARTED_EVENT_ID)
        self.assertEqual(transition["event_type"], "replay.rejected")
        self.assertIsNone(transition["payload"]["checker_retired_instructions"])
        self.assertEqual(
            transition["payload"]["checker_retired_instructions_unavailable_reason"],
            "counter_not_supported",
        )

    def test_all_five_checker_outcomes_are_distinct(self) -> None:
        queue, profile, measurement = self.inputs()
        plan = plan_next(queue, profile, measurement)
        expected = (
            ("completed", "accepted", "replay.accepted"),
            ("completed", "rejected", "replay.rejected"),
            ("completed", "declined", "replay.declined"),
            ("crashed", None, "replay.crashed"),
            ("timed_out", None, "replay.timed_out"),
        )
        for execution_outcome, checker_outcome, event_type in expected:
            with self.subTest(event_type=event_type):
                verdict = load_fixture("replay-verdict-accepted-v1.json")
                verdict["execution_outcome"] = execution_outcome
                verdict["checker_outcome"] = checker_outcome
                transition = terminal_transition(plan, verdict, STARTED_EVENT_ID)
                self.assertEqual(transition["event_type"], event_type)

    def test_disposable_vm_interface_always_destroys_test_double(self) -> None:
        queue, profile, measurement = self.inputs()
        plan = plan_next(queue, profile, measurement)
        runner = RecordingDisposableVm(
            load_fixture("replay-verdict-accepted-v1.json")
        )
        transition = run_with_disposable_vm(plan, runner, STARTED_EVENT_ID)
        self.assertEqual(transition["event_type"], "replay.accepted")
        self.assertEqual(runner.requests, [plan["request"]])
        self.assertTrue(runner.destroyed)

        invalid = load_fixture("replay-verdict-accepted-v1.json")
        invalid["checker_outcome"] = None
        runner = RecordingDisposableVm(invalid)
        with self.assertRaises(ReplayError):
            run_with_disposable_vm(plan, runner, STARTED_EVENT_ID)
        self.assertTrue(runner.destroyed)

    def test_required_counter_fails_closed_when_unavailable(self) -> None:
        queue, profile, measurement = self.inputs()
        measurement["retired_instructions"]["required"] = True
        queue["tasks"][0]["measurement_config_digest"] = config_digest(
            "lean-eval-replay-measurement-config-v1", measurement
        )
        queue["tasks"][0]["replay_task_id"] = replay_task_id(
            queue["tasks"][0]["result_id"],
            queue["tasks"][0]["measurement_config_digest"],
        )
        plan = plan_next(queue, profile, measurement)
        verdict = load_fixture("replay-verdict-accepted-v1.json")
        verdict["replay_task_id"] = queue["tasks"][0]["replay_task_id"]
        verdict["statistics"]["checker_retired_instructions"] = {
            "status": "unavailable",
            "reason": "counter_permission_denied",
        }
        with self.assertRaisesRegex(ReplayError, "required retired-instruction"):
            terminal_transition(plan, verdict, STARTED_EVENT_ID)

        verdict = load_fixture("replay-verdict-accepted-v1.json")
        verdict["replay_task_id"] = queue["tasks"][0]["replay_task_id"]
        verdict["statistics"]["build_retired_instructions"] = {
            "status": "unavailable",
            "reason": "counter_not_reported",
        }
        with self.assertRaisesRegex(ReplayError, "required retired-instruction"):
            terminal_transition(plan, verdict, STARTED_EVENT_ID)

    def test_execution_failure_does_not_claim_checker_outcome(self) -> None:
        queue, profile, measurement = self.inputs()
        plan = plan_next(queue, profile, measurement)
        verdict = load_fixture("replay-verdict-accepted-v1.json")
        verdict.update(
            execution_outcome="failed",
            checker_outcome=None,
            failure_reason="runner_lost",
            statistics=None,
        )
        transition = terminal_transition(plan, verdict, STARTED_EVENT_ID)
        self.assertEqual(transition["event_type"], "replay.failed")
        self.assertTrue(transition["payload"]["retryable"])
        verdict["checker_outcome"] = "rejected"
        with self.assertRaisesRegex(ReplayError, "cannot claim a checker"):
            terminal_transition(plan, verdict, STARTED_EVENT_ID)

    def test_cli_writes_machine_json_and_diagnostics_to_stderr(self) -> None:
        command = [
            sys.executable,
            str(ROOT / "scripts/replay_orchestrator.py"),
            "plan",
            "--queue",
            str(FIXTURES / "replay-queue-public-v1.json"),
            "--execution-profile",
            str(FIXTURES / "replay-execution-profile-v1.json"),
            "--measurement-config",
            str(FIXTURES / "replay-measurement-config-v1.json"),
        ]
        success = subprocess.run(command, check=False, capture_output=True, text=True)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout)["kind"], "execution")
        self.assertEqual(success.stderr, "")
        failure = subprocess.run(
            [*command[:-1], str(FIXTURES / "replay-verdict-accepted-v1.json")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(failure.stdout, "")
        self.assertIn("error:", failure.stderr)

    def test_all_schemas_and_fixtures_are_valid_json(self) -> None:
        for path in sorted((ROOT / "schemas").glob("*.json")):
            with self.subTest(path=path):
                json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURES.glob("*.json")):
            with self.subTest(path=path):
                json.loads(path.read_text(encoding="utf-8"))

    def test_replay_schemas_describe_the_private_execution_union(self) -> None:
        request_schema = json.loads(
            (ROOT / "schemas/replay-execution-request-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        source_variants = request_schema["properties"]["source"]["oneOf"]
        self.assertEqual(
            {variant["properties"]["visibility"]["const"] for variant in source_variants},
            {"public", "private"},
        )
        private_source = next(
            variant
            for variant in source_variants
            if variant["properties"]["visibility"]["const"] == "private"
        )
        self.assertEqual(set(private_source["required"]), {"visibility", "archive"})
        plan_schema = json.loads(
            (ROOT / "schemas/replay-plan-v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {variant["properties"]["kind"]["const"] for variant in plan_schema["oneOf"]},
            {"empty", "execution"},
        )

    def test_replay_schemas_enforce_attempt_ceiling(self) -> None:
        queue_schema = json.loads(
            (ROOT / "schemas/replay-queue-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        request_schema = json.loads(
            (ROOT / "schemas/replay-execution-request-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        plan_schema = json.loads(
            (ROOT / "schemas/replay-plan-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        verdict_schema = json.loads(
            (ROOT / "schemas/replay-verdict-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            queue_schema["$defs"]["task"]["properties"]["attempt"]["maximum"],
            3,
        )
        self.assertEqual(request_schema["properties"]["attempt"]["maximum"], 4)
        execution_plan = next(
            variant
            for variant in plan_schema["oneOf"]
            if variant["properties"]["kind"]["const"] == "execution"
        )
        started_properties = execution_plan["properties"]["started_transition"]
        attempt_schema = started_properties["properties"]["payload"]["properties"]
        self.assertEqual(
            attempt_schema["attempt"]["maximum"],
            4,
        )
        self.assertEqual(verdict_schema["properties"]["attempt"]["maximum"], 4)


if __name__ == "__main__":
    unittest.main()
