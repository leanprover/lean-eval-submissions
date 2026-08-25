"""Contract tests for the dark model-identity staging controller."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_model_identity_staging_qualification.py"
SPEC = importlib.util.spec_from_file_location("model_identity_qualification", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load model identity staging qualification module")
QUALIFICATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUALIFICATION)


COMMIT = "a" * 40
INITIAL = "b" * 40
RUN_ID = "33000000001"
SESSIONS = QUALIFICATION.Sessions("o" * 32, "a" * 32, "x" * 32)


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
    def __init__(self, fail_operation: str | None = None, fail_restore: bool = False):
        self.calls: list[tuple[str, object, object]] = []
        self.head = INITIAL
        self.counter = 0
        self.fail_operation = fail_operation
        self.fail_restore = fail_restore

    def __call__(self, kind, payload, sessions):
        self.calls.append((kind, payload, sessions))
        if kind == "health":
            return 200, health()
        if kind == "restore":
            if self.fail_restore:
                return 503, {"error": "restore_failed"}
            return 200, {
                "deployed_commit": COMMIT,
                "maintainer_api_enabled": False,
                "owner_api_enabled": False,
                "restored_tree_commit": INITIAL,
                "run_id": RUN_ID,
                "schema_version": 1,
                "state_commit": "f" * 40,
                "status": "model_identity_qualification_restored",
            }
        operation = payload["operation"]
        if operation == self.fail_operation:
            return 409, {"error": "injected"}
        if operation not in QUALIFICATION.NON_MUTATING_PROOFS:
            self.counter += 1
            self.head = f"{self.counter:040x}"
        measurement = operation == "maximal_contention_measurement"
        return 200, {
            "cas_attempts": 8 if measurement else None,
            "deployed_commit": COMMIT,
            "maintainer_api_enabled": False,
            "mutation_created": operation not in QUALIFICATION.NON_MUTATING_PROOFS,
            "operation": operation,
            "outcome": QUALIFICATION.PROOF_OUTCOMES[operation],
            "owner_api_enabled": False,
            "previous_state_commit": payload["expected_state_commit"],
            "run_id": RUN_ID,
            "schema_version": 1,
            "state_commit": self.head,
            "status": "model_identity_qualification_step_verified",
            "subrequests": 400 if measurement else None,
        }


class ModelIdentityStagingQualificationTests(unittest.TestCase):
    def run_case(self, harness: FakeHarness) -> dict[str, object]:
        return QUALIFICATION.run_qualification(
            harness,
            expected_commit=COMMIT,
            initial_state_commit=INITIAL,
            run_id=RUN_ID,
            sessions=SESSIONS,
        )

    def test_executes_every_closed_proof_chains_heads_and_restores(self) -> None:
        harness = FakeHarness()
        result = self.run_case(harness)
        operations = [
            payload["operation"] for kind, payload, _ in harness.calls if kind == "step"
        ]
        self.assertEqual(tuple(operations), QUALIFICATION.QUALIFICATION_PROOFS)
        self.assertEqual(result["proofs"], list(QUALIFICATION.QUALIFICATION_PROOFS))
        self.assertEqual(
            [kind for kind, _, _ in harness.calls],
            ["health", *("step" for _ in operations), "restore", "health"],
        )
        expected_head = INITIAL
        mutation_count = 0
        for kind, payload, sessions in harness.calls:
            if kind != "step":
                continue
            self.assertEqual(payload["expected_state_commit"], expected_head)
            self.assertIs(sessions, SESSIONS)
            if payload["operation"] not in QUALIFICATION.NON_MUTATING_PROOFS:
                mutation_count += 1
                expected_head = f"{mutation_count:040x}"

    def test_restores_after_partial_failure_and_does_not_claim_success(self) -> None:
        harness = FakeHarness(fail_operation="identity_rename")
        with self.assertRaisesRegex(
            QUALIFICATION.QualificationFailure,
            r"qualification failed after 6 verified proof\(s\); restoration passed",
        ):
            self.run_case(harness)
        kinds = [kind for kind, _, _ in harness.calls]
        self.assertEqual(kinds[-1], "restore")
        self.assertNotIn("health", kinds[1:])

    def test_combines_primary_and_mandatory_restore_failure(self) -> None:
        harness = FakeHarness(
            fail_operation="identity_rename",
            fail_restore=True,
        )
        with self.assertRaisesRegex(
            QUALIFICATION.QualificationFailure,
            "qualification failed .* mandatory restoration failed",
        ):
            self.run_case(harness)

    def test_rejects_enabled_flags_extra_fields_and_state_movement_on_denial(
        self,
    ) -> None:
        canonical = health()
        for hostile in (
            {**canonical, "model_identity_owner_api_enabled": True},
            {**canonical, "model_identity_maintainer_api_enabled": True},
            {**canonical, "unexpected": False},
            {**canonical, "model_identity_write_max_subrequests": 401},
        ):
            with (
                self.subTest(hostile=hostile),
                self.assertRaises(QUALIFICATION.QualificationFailure),
            ):
                QUALIFICATION.validate_health(200, hostile, COMMIT)

        operation = "cross_owner_denial"
        response = {
            "cas_attempts": None,
            "deployed_commit": COMMIT,
            "maintainer_api_enabled": False,
            "mutation_created": False,
            "operation": operation,
            "outcome": QUALIFICATION.PROOF_OUTCOMES[operation],
            "owner_api_enabled": False,
            "previous_state_commit": INITIAL,
            "run_id": RUN_ID,
            "schema_version": 1,
            "state_commit": "c" * 40,
            "status": "model_identity_qualification_step_verified",
            "subrequests": None,
        }
        with self.assertRaisesRegex(
            QUALIFICATION.QualificationFailure,
            "unexpectedly changed staging State",
        ):
            QUALIFICATION.validate_proof_response(
                200,
                response,
                expected_commit=COMMIT,
                expected_head=INITIAL,
                operation=operation,
                run_id=RUN_ID,
            )

    def test_requires_exact_live_maximal_contention_measurement(self) -> None:
        harness = FakeHarness()
        original = harness.__call__

        def hostile(kind, payload, sessions):
            status, response = original(kind, payload, sessions)
            if (
                kind == "step"
                and payload["operation"] == "maximal_contention_measurement"
            ):
                response = {**response, "cas_attempts": 7}
            return status, response

        with self.assertRaisesRegex(
            QUALIFICATION.QualificationFailure,
            "qualification failed after 13 verified proof",
        ):
            QUALIFICATION.run_qualification(
                hostile,
                expected_commit=COMMIT,
                initial_state_commit=INITIAL,
                run_id=RUN_ID,
                sessions=SESSIONS,
            )


if __name__ == "__main__":
    unittest.main()
