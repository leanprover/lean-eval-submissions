"""Contract tests for the journaled dark model-identity staging controller."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_model_identity_staging_qualification.py"
SPEC = importlib.util.spec_from_file_location("model_identity_qualification", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load model identity staging qualification module")
QUALIFICATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QUALIFICATION
SPEC.loader.exec_module(QUALIFICATION)

COMMIT = "a" * 40
INITIAL = "b" * 40
INITIAL_TREE = "c" * 40
RUN_ID = "33000000001"
RUN_ATTEMPT = 1
JOURNAL_ID = "mqj_" + "d" * 64
INTENT = QUALIFICATION.Intent(
    owner=QUALIFICATION.Identity(101, "owner-one"),
    cross_owner=QUALIFICATION.Identity(202, "owner-two"),
    maintainer=QUALIFICATION.Identity(303, "maintainer-one"),
)
SESSIONS = QUALIFICATION.Sessions("o" * 32, "a" * 32, "x" * 32, "m" * 32)


def health() -> dict[str, object]:
    return {
        "deployed_commit": COMMIT,
        "environment": "staging",
        "intake_configured_enabled": False,
        "intake_effective_enabled": False,
        "intake_enabled": False,
        "intake_enablement_mode": "disabled",
        "intake_lease_expires_at": None,
        "legacy_result_owner_api_enabled": False,
        "model_identity_consolidation_api": "atomic_reverse_impact_v1",
        "model_identity_maintainer_api_enabled": False,
        "model_identity_owner_api_enabled": False,
        "model_identity_write_max_subrequests": 400,
        "promotion_canary_configured_enabled": True,
        "promotion_canary_enabled": True,
        "result_amendment_maintainer_api_enabled": False,
        "result_amendment_owner_api_enabled": False,
        "service": "lean-eval-submission",
        "status": "ok",
    }


class FakeHarness:
    def __init__(self, fail_operation: str | None = None):
        self.calls: list[tuple[str, object, dict[str, str]]] = []
        self.head = INITIAL
        self.tree = INITIAL_TREE
        self.revision = 1
        self.restoration_commit: str | None = None
        self.restoration_parent_commit: str | None = None
        self.restoration_parent_tree: str | None = None
        self.fail_operation = fail_operation
        self.counter = 0
        self.first_event: str | None = None

    def journal(self) -> dict[str, object]:
        return {
            "current_state_commit": self.head,
            "current_state_tree": self.tree,
            "deployed_commit": COMMIT,
            "environment": "staging",
            "foreign_commit_observed": False,
            "initial_state_commit": INITIAL,
            "initial_state_tree": INITIAL_TREE,
            "journal_id": JOURNAL_ID,
            "journal_revision": self.revision,
            "lease_released": self.restoration_commit is not None,
            "lease_status": "restored" if self.restoration_commit else "active",
            "maintainer_api_enabled": False,
            "owner_api_enabled": False,
            "restoration_commit": self.restoration_commit,
            "restoration_fast_forward": self.restoration_commit is not None,
            "restoration_parent_commit": self.restoration_parent_commit,
            "restoration_parent_tree": self.restoration_parent_tree,
            "restoration_tree": INITIAL_TREE if self.restoration_commit else None,
            "restoration_tree_equal": self.restoration_commit is not None,
            "run_attempt": RUN_ATTEMPT,
            "run_id": RUN_ID,
            "schema_version": 2,
            "status": "model_identity_qualification_journal",
        }

    def __call__(self, kind, payload, credentials):
        self.calls.append((kind, payload, dict(credentials)))
        if kind == "health":
            return 200, health()
        if kind in {"acquire", "status"}:
            return 200, self.journal()
        if kind == "restore":
            parent = self.head
            parent_tree = self.tree
            self.revision += 1
            self.restoration_commit = "f" * 40
            self.restoration_parent_commit = parent
            self.restoration_parent_tree = parent_tree
            self.head = self.restoration_commit
            self.tree = INITIAL_TREE
            return 200, {
                "deployed_commit": COMMIT,
                "fast_forward": True,
                "foreign_commit_observed": False,
                "initial_state_commit": INITIAL,
                "initial_state_tree": INITIAL_TREE,
                "journal_id": JOURNAL_ID,
                "journal_revision": self.revision,
                "lease_released": True,
                "maintainer_api_enabled": False,
                "owner_api_enabled": False,
                "ref_head": self.restoration_commit,
                "restoration_commit": self.restoration_commit,
                "restoration_parent_commit": parent,
                "restoration_parent_tree": parent_tree,
                "restoration_tree": INITIAL_TREE,
                "run_attempt": RUN_ATTEMPT,
                "run_id": RUN_ID,
                "schema_version": 2,
                "status": "model_identity_qualification_restored",
                "tree_equal": True,
            }
        contract = QUALIFICATION.CONTRACT_BY_OPERATION[payload["operation"]]
        if contract.operation == self.fail_operation:
            return 409, {"error": "injected"}
        self.counter += 1
        if contract.mutation_created:
            self.head = f"{self.counter:040x}"
            self.tree = f"{self.counter + 100:040x}"
        self.revision += 1
        if contract.operation in {"idempotent_retry", "cross_route_event_collision"}:
            event_ids = [self.first_event]
        else:
            event_ids = [
                f"00000000-0000-7{self.counter:03x}-8000-{index + self.counter:012x}"
                for index in range(contract.minimum_event_ids)
            ]
            if self.first_event is None and event_ids:
                self.first_event = event_ids[0]
        model_ids = [
            f"mi1_{index + self.counter:064x}"
            for index in range(contract.minimum_model_ids)
        ]
        alias_keys = [
            f"ma1_{index + self.counter:064x}" for index in range(contract.alias_count)
        ]
        return 200, {
            "cas_attempts": 8
            if contract.operation == "maximal_contention_measurement"
            else None,
            "deployed_commit": COMMIT,
            "journal_id": JOURNAL_ID,
            "journal_revision": self.revision,
            "maintainer_api_enabled": False,
            "mutation_created": contract.mutation_created,
            "owner_api_enabled": False,
            "previous_state_commit": payload["expected_state_commit"],
            "previous_state_tree": payload["expected_state_tree"],
            "proof": {
                "actor": QUALIFICATION._identity_for_role(
                    INTENT, contract.actor_role
                ).json(),
                "alias_keys": alias_keys,
                "assertions": {assertion: True for assertion in contract.assertions},
                "credential_roles": list(contract.credential_roles),
                "event_ids": event_ids,
                "http_status": contract.http_status,
                "model_ids": model_ids,
                "operation": contract.operation,
                "route": contract.route,
            },
            "run_attempt": RUN_ATTEMPT,
            "run_id": RUN_ID,
            "schema_version": 2,
            "state_commit": self.head,
            "state_tree": self.tree,
            "status": "model_identity_qualification_step_verified",
            "subrequests": 400
            if contract.operation == "maximal_contention_measurement"
            else None,
        }


class ModelIdentityStagingQualificationTests(unittest.TestCase):
    def run_case(self, harness: FakeHarness):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = pathlib.Path(temporary.name) / "evidence.json"
        evidence = QUALIFICATION.Evidence(path, "qualification", RUN_ID, RUN_ATTEMPT)
        result = QUALIFICATION.run_qualification(
            harness,
            expected_commit=COMMIT,
            initial_state_commit=INITIAL,
            initial_state_tree=INITIAL_TREE,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            intent=INTENT,
            sessions=SESSIONS,
            evidence=evidence,
        )
        return result, json.loads(path.read_text()), harness

    def test_closed_lifecycle_uses_least_privilege_and_preserves_full_evidence(self):
        result, artifact, harness = self.run_case(FakeHarness())
        step_calls = [call for call in harness.calls if call[0] == "step"]
        self.assertEqual(
            [call[1]["operation"] for call in step_calls],
            [contract.operation for contract in QUALIFICATION.PROOF_CONTRACTS],
        )
        for (_, _, credentials), contract in zip(
            step_calls, QUALIFICATION.PROOF_CONTRACTS, strict=True
        ):
            self.assertEqual(set(credentials), set(contract.credential_roles))
        for kind, _, credentials in harness.calls:
            if kind != "step":
                self.assertEqual(credentials, {})
        self.assertEqual(len(artifact["proofs"]), len(QUALIFICATION.PROOF_CONTRACTS))
        self.assertEqual(artifact["proofs"][-1]["cas_attempts"], 8)
        self.assertEqual(artifact["proofs"][-1]["subrequests"], 400)
        self.assertEqual(
            result["status"], "model_identity_staging_qualification_passed_and_restored"
        )
        self.assertEqual(
            [call[0] for call in harness.calls][-2:], ["restore", "health"]
        )

    def test_mutating_step_must_advance_both_commit_and_tree(self):
        harness = FakeHarness()
        contract = QUALIFICATION.CONTRACT_BY_OPERATION["owner_request"]
        _, response = harness(
            "step",
            {
                "operation": contract.operation,
                "expected_state_commit": INITIAL,
                "expected_state_tree": INITIAL_TREE,
            },
            {"oauth_owner": SESSIONS.oauth_owner},
        )
        response["state_commit"] = INITIAL
        with self.assertRaisesRegex(
            QUALIFICATION.QualificationFailure, "did not advance"
        ):
            QUALIFICATION.validate_step(
                200,
                response,
                contract=contract,
                intent=INTENT,
                expected_commit=COMMIT,
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                journal_id=JOURNAL_ID,
                expected_revision=1,
                expected_head=INITIAL,
                expected_tree=INITIAL_TREE,
            )

    def test_partial_failure_still_restores_and_runs_final_disabled_health(self):
        harness = FakeHarness(fail_operation="identity_rename")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        evidence = QUALIFICATION.Evidence(
            pathlib.Path(temporary.name) / "evidence.json", "qualification", RUN_ID, 1
        )
        with self.assertRaisesRegex(
            QUALIFICATION.QualificationFailure, "restoration.*passed"
        ):
            QUALIFICATION.run_qualification(
                harness,
                expected_commit=COMMIT,
                initial_state_commit=INITIAL,
                initial_state_tree=INITIAL_TREE,
                run_id=RUN_ID,
                run_attempt=1,
                intent=INTENT,
                sessions=SESSIONS,
                evidence=evidence,
            )
        self.assertEqual(
            [call[0] for call in harness.calls][-3:], ["status", "restore", "health"]
        )
        self.assertIsNotNone(evidence.body["final_health"])

    def test_standalone_recovery_is_idempotent_after_restoration(self):
        harness = FakeHarness()
        harness.restoration_commit = "f" * 40
        harness.restoration_parent_commit = "e" * 40
        harness.restoration_parent_tree = "d" * 40
        harness.head = harness.restoration_commit
        harness.tree = INITIAL_TREE
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        evidence = QUALIFICATION.Evidence(
            pathlib.Path(temporary.name) / "recovery.json", "recovery", RUN_ID, 1
        )
        result = QUALIFICATION.recover_journal(
            harness,
            expected_commit=COMMIT,
            run_id=RUN_ID,
            run_attempt=1,
            evidence=evidence,
        )
        self.assertEqual(result["status"], "already_restored")
        self.assertEqual([call[0] for call in harness.calls], ["status", "health"])

    def test_exact_actor_evidence_is_required(self):
        harness = FakeHarness()
        contract = QUALIFICATION.CONTRACT_BY_OPERATION["oauth_session_identity"]
        _, response = harness(
            "step",
            {
                "operation": contract.operation,
                "expected_state_commit": INITIAL,
                "expected_state_tree": INITIAL_TREE,
            },
            {"oauth_owner": SESSIONS.oauth_owner},
        )
        response["proof"]["actor"] = {"github_id": 999, "login": "intruder"}
        with self.assertRaisesRegex(
            QUALIFICATION.QualificationFailure, "closed boundary"
        ):
            QUALIFICATION.validate_step(
                200,
                response,
                contract=contract,
                intent=INTENT,
                expected_commit=COMMIT,
                run_id=RUN_ID,
                run_attempt=1,
                journal_id=JOURNAL_ID,
                expected_revision=1,
                expected_head=INITIAL,
                expected_tree=INITIAL_TREE,
            )


if __name__ == "__main__":
    unittest.main()
