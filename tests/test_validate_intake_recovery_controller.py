import argparse
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_intake_recovery_controller import validate  # noqa: E402


COMMIT = "a" * 40


def fixture(**changes: str | None) -> argparse.Namespace:
    values: dict[str, str | None] = {
        "event_name": "workflow_run",
        "live_commit": COMMIT,
        "controller_run_id": "100",
        "controller_run_attempt": "1",
        "controller_commit": COMMIT,
        "controller_status": "completed",
        "controller_conclusion": "failure",
        "trigger_run_id": "100",
        "trigger_run_attempt": "1",
        "trigger_commit": COMMIT,
        "trigger_conclusion": "failure",
    }
    values.update(changes)
    return argparse.Namespace(**values)


class IntakeRecoveryControllerTests(unittest.TestCase):
    def test_exact_failed_trigger_is_eligible(self) -> None:
        validate(fixture())

    def test_delayed_failure_after_successful_retry_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not the exact failed workflow_run trigger"):
            validate(
                fixture(
                    controller_run_attempt="2",
                    controller_conclusion="success",
                )
            )

    def test_different_live_commit_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "exact live intake commit"):
            validate(fixture(live_commit="b" * 40))

    def test_manual_latest_live_controller_remains_eligible(self) -> None:
        validate(
            fixture(
                event_name="workflow_dispatch",
                controller_conclusion="success",
                trigger_run_id=None,
                trigger_run_attempt=None,
                trigger_commit=None,
                trigger_conclusion=None,
            )
        )


if __name__ == "__main__":
    unittest.main()
