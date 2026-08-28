from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import pathlib
import tempfile
import unittest

from scripts.key_capability_contract import archive_key_id, capability_digest
from scripts.replay_controller import (
    ReplayControllerError,
    build_executor_request,
    failure_verdict,
    prepare_unwrap,
    recover_running,
    started_event,
    terminal_event,
    unwrap_identity,
    validate_executor_response,
)
from scripts.replay_orchestrator import plan_next


ROOT = pathlib.Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SUBMISSION_ID = "0198abcd-0000-7000-8000-000000000001"
CIPHERTEXT = b"age-encryption.org/v1\ncredentialed-controller-fixture"
PLAINTEXT = b"fixture private tar bytes"
RECIPIENT = "age1" + "q" * 40


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def inputs() -> tuple[dict, dict, dict, dict]:
    queue = fixture("replay-queue-public-v1.json")
    profile = fixture("replay-execution-profile-v1.json")
    measurement = fixture("replay-measurement-config-v1.json")
    task = queue["tasks"][0]
    task.update({
        "submission_id": SUBMISSION_ID,
        "source_visibility": "private",
        "archive_repository": "leanprover/lean-eval-audit",
        "archive_commit": "a" * 40,
        "archive_path": f"archives/01/{SUBMISSION_ID}.tar.age",
        "archive_ciphertext_sha256": digest(CIPHERTEXT),
    })
    plan = plan_next(queue, profile, measurement)
    return queue, profile, measurement, plan


def sidecar() -> dict:
    return {
        "schema_version": 3,
        "submission_id": SUBMISSION_ID,
        "submission_repo": "example/private-source",
        "submission_ref": "b" * 40,
        "submission_kind": "github_repo",
        "submission_public": False,
        "submitter": "example",
        "model": "Example Model",
        "size_bytes_plaintext_tar": len(PLAINTEXT),
        "sha256_plaintext_tar": digest(PLAINTEXT),
        "size_bytes_ciphertext": len(CIPHERTEXT),
        "sha256_ciphertext": digest(CIPHERTEXT),
        "archived_at": "2026-08-23T03:40:44Z",
        "benchmark_commit": "d" * 40,
        "archiver_workflow_run": "https://github.com/leanprover/lean-eval-submissions/actions/runs/123",
        "key_envelope": {
            "schema_version": 1,
            "submission_id": SUBMISSION_ID,
            "archive_ciphertext_sha256": digest(CIPHERTEXT),
            "data_key_id": archive_key_id(SUBMISSION_ID, RECIPIENT),
            "age_recipient": RECIPIENT,
            "adapter": "aws-kms-v1",
            "wrapped_identity": "cHJvdmlkZXItd3JhcHBlZC1pZGVudGl0eQ==",
        },
    }


class ReplayControllerTests(unittest.TestCase):
    def test_started_and_terminal_events_are_exactly_queue_bound(self) -> None:
        queue, _, _, plan = inputs()
        started = started_event(
            plan,
            queue,
            "2026-08-23T07:00:00.000Z",
            random_bytes=b"\x01" * 10,
        )
        self.assertEqual(started["event_type"], "replay.started")
        self.assertEqual(started["causation_event_id"], queue["tasks"][0]["event_id"])
        verdict = fixture("replay-verdict-accepted-v1.json")
        verdict["replay_task_id"] = plan["request"]["replay_task_id"]
        verdict["attempt"] = plan["request"]["attempt"]
        terminal = terminal_event(
            plan,
            verdict,
            started,
            "2026-08-23T07:00:01.000Z",
            random_bytes=b"\x02" * 10,
        )
        self.assertEqual(terminal["event_type"], "replay.accepted")
        self.assertEqual(terminal["causation_event_id"], started["event_id"])
        changed = copy.deepcopy(queue)
        changed["tasks"][0]["event_id"] = "0198abcd-0000-7000-8000-000000000009"
        with self.assertRaisesRegex(ReplayControllerError, "next exact queue task"):
            started_event(plan, changed, "2026-08-23T07:00:00.000Z")

    def test_builds_one_use_bound_private_executor_handoff(self) -> None:
        _, _, _, plan = inputs()
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            ciphertext = root / "archive.tar.age"
            ciphertext.write_bytes(CIPHERTEXT)
            unwrap = prepare_unwrap(
                plan,
                sidecar(),
                ciphertext,
                "2026-08-23T07:00:00.000Z",
                request_random=b"\x03" * 10,
                runner_nonce="4" * 64,
            )
            self.assertEqual(unwrap["capability"]["max_uses"], 1)
            self.assertEqual(unwrap["capability"]["purpose"], "lean-eval-replay")
            current = dt.datetime.now(dt.timezone.utc)
            unwrap["capability"]["issued_at"] = current.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            unwrap["capability"]["expires_at"] = (current + dt.timedelta(minutes=5)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            native_identity = b"AGE-SECRET-KEY-1FIXTURE\n"
            response = {
                "schema_version": 1,
                "adapter": unwrap["adapter"],
                "request_id": unwrap["capability"]["request_id"],
                "data_key_id": unwrap["envelope"]["data_key_id"],
                "capability_digest": capability_digest(unwrap["capability"]),
                "plaintext_identity_base64": "QUdFLVNFQ1JFVC1LRVktMUZJWFRVUkUK",
            }
            self.assertEqual(
                unwrap_identity(unwrap, response, {"StatusCode": 200}),
                native_identity,
            )
            changed_response = {**response, "request_id": "0198abcd-0000-7000-8000-000000000009"}
            with self.assertRaisesRegex(ReplayControllerError, "exact request"):
                unwrap_identity(unwrap, changed_response, {"StatusCode": 200})
            identity = root / "identity.age"
            identity.write_bytes(native_identity)
            request = build_executor_request(
                plan, sidecar(), ciphertext, unwrap, identity
            )
            self.assertEqual(request["request"], plan["request"])
            self.assertEqual(request["runner_nonce"], "4" * 64)
            self.assertNotIn("wrapped_identity", json.dumps(request))
            changed = sidecar()
            changed["sha256_plaintext_tar"] = "9" * 64
            changed["key_envelope"]["archive_ciphertext_sha256"] = "9" * 64
            with self.assertRaises((ReplayControllerError, ValueError)):
                prepare_unwrap(plan, changed, ciphertext, "2026-08-23T07:00:00.000Z")

    def test_response_and_failure_paths_preserve_distinct_outcomes(self) -> None:
        _, _, _, plan = inputs()
        verdict = fixture("replay-verdict-accepted-v1.json")
        verdict["replay_task_id"] = plan["request"]["replay_task_id"]
        verdict["attempt"] = plan["request"]["attempt"]
        self.assertEqual(validate_executor_response({
            "schema_version": 1,
            "verdict": verdict,
            "destruction": "confirmed",
        }, plan), verdict)
        with self.assertRaisesRegex(ReplayControllerError, "destruction"):
            validate_executor_response({
                "schema_version": 1,
                "verdict": verdict,
                "destruction": "unknown",
            }, plan)
        failed = failure_verdict(plan, "runner_lost")
        self.assertEqual(failed["execution_outcome"], "failed")
        self.assertIsNone(failed["checker_outcome"])

    def test_stale_running_replay_recovers_as_retryable_runner_loss(self) -> None:
        queue, _, _, _ = inputs()
        task = {
            **queue["tasks"][0],
            "status": "running",
            "attempt": 1,
            "event_id": "0198abcd-0000-7000-8000-000000000008",
            "occurred_at": "2026-08-22T20:00:00.000Z",
        }
        recovered = recover_running(
            {"replay_tasks": [task]}, "2026-08-23T07:00:00.000Z"
        )
        self.assertEqual(recovered["kind"], "failed")
        self.assertEqual(recovered["event"]["payload"], {
            "attempt": 1,
            "reason_code": "runner_lost",
            "retryable": True,
        })
        task["occurred_at"] = "2026-08-23T06:30:00.000Z"
        self.assertEqual(
            recover_running({"replay_tasks": [task]}, "2026-08-23T07:00:00.000Z")["kind"],
            "busy",
        )

    def test_stale_fourth_attempt_recovers_terminally_and_attempt_five_is_rejected(
        self,
    ) -> None:
        queue, _, _, _ = inputs()
        task = {
            **queue["tasks"][0],
            "status": "running",
            "attempt": 4,
            "event_id": "0198abcd-0000-7000-8000-000000000008",
            "occurred_at": "2026-08-22T20:00:00.000Z",
        }
        recovered = recover_running(
            {"replay_tasks": [task]}, "2026-08-23T07:00:00.000Z"
        )
        self.assertEqual(recovered["kind"], "failed")
        self.assertFalse(recovered["event"]["payload"]["retryable"])

        task["attempt"] = 5
        with self.assertRaisesRegex(ReplayControllerError, "attempt is invalid"):
            recover_running(
                {"replay_tasks": [task]}, "2026-08-23T07:00:00.000Z"
            )

    def test_fourth_attempt_terminal_matches_shared_state_contract_fixture(
        self,
    ) -> None:
        queue, profile, measurement, _ = inputs()
        task = queue["tasks"][0]
        task.update(
            status="failed",
            attempt=3,
            reason_code="runner_lost",
            retryable=True,
            event_id="0198abcd-0000-7000-8000-000000000009",
            occurred_at="2026-08-23T06:59:59.000Z",
        )
        plan = plan_next(queue, profile, measurement)
        started = started_event(
            plan,
            queue,
            "2026-08-23T07:00:00.000Z",
            random_bytes=b"\x01" * 10,
        )
        terminal = terminal_event(
            plan,
            failure_verdict(plan, "runner_lost"),
            started,
            "2026-08-23T07:00:01.000Z",
            random_bytes=b"\x02" * 10,
        )
        self.assertEqual(
            terminal,
            fixture("replay-failed-attempt-limit-v1.json"),
        )


if __name__ == "__main__":
    unittest.main()
