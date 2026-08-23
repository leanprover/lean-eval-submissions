from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from scripts.sanitize_executor_failure import read_sanitized_failure, sanitized_failure


class SanitizeExecutorFailureTests(unittest.TestCase):
    def test_accepts_only_the_closed_failure_contract(self) -> None:
        self.assertEqual(
            sanitized_failure(
                {
                    "error": "executor_failed",
                    "reason": "command_failed",
                    "detail": "measurement_evidence_unavailable",
                }
            ),
            {
                "error": "executor_failed",
                "reason": "command_failed",
                "detail": "measurement_evidence_unavailable",
            },
        )
        self.assertEqual(
            sanitized_failure(
                {"error": "executor_failed", "reason": "input_transfer_failed"}
            ),
            {"error": "executor_failed", "reason": "input_transfer_failed"},
        )

    def test_rejects_extra_fields_and_unregistered_values(self) -> None:
        self.assertIsNone(
            sanitized_failure(
                {
                    "error": "executor_failed",
                    "reason": "command_failed",
                    "detail": "measurement_evidence_unavailable",
                    "stderr": "private source",
                }
            )
        )
        self.assertIsNone(
            sanitized_failure(
                {
                    "error": "executor_failed",
                    "reason": "command_failed",
                    "detail": "private_source",
                }
            )
        )
        self.assertIsNone(
            sanitized_failure(
                {"error": "executor_failed", "reason": "private_source"}
            )
        )
        self.assertIsNone(
            sanitized_failure({"error": "executor_failed", "reason": ["command_failed"]})
        )
        self.assertIsNone(
            sanitized_failure(
                {
                    "error": "executor_failed",
                    "reason": "command_failed",
                    "detail": ["measurement_evidence_unavailable"],
                }
            )
        )

    def test_file_reader_fails_closed_without_reproducing_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "response.json"
            path.write_text(json.dumps({"secret": "private source"}), encoding="utf-8")
            self.assertIsNone(read_sanitized_failure(path))
            path.write_bytes(b"{" + b"x" * 4096 + b"}")
            self.assertIsNone(read_sanitized_failure(path))


if __name__ == "__main__":
    unittest.main()
