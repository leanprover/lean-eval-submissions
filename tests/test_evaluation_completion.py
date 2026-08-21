from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from scripts.build_evaluation_completion import build


SUBMISSION_ID = "0198abcd-1111-7000-8000-000000000001"


class EvaluationCompletionTests(unittest.TestCase):
    def fixture(self, passed: list[str] | None = None) -> tuple[pathlib.Path, pathlib.Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = pathlib.Path(temporary.name)
        archive = root / "archive.json"
        archive.write_text(json.dumps({
            "schema_version": 1,
            "occurred_at": "2026-08-21T12:00:00.000Z",
            "locator": {
                "schema_version": 1,
                "submission_id": SUBMISSION_ID,
                "archive_repository": "leanprover/lean-eval-audit",
                "archive_commit": "a" * 40,
                "archive_path": f"archives/01/{SUBMISSION_ID}.tar.age",
                "archive_ciphertext_sha256": "b" * 64,
                "encrypted": True,
            },
        }) + "\n", encoding="utf-8")
        results = root / "results.json"
        results.write_text(json.dumps({"passed": passed or []}) + "\n", encoding="utf-8")
        return archive, results

    def build(self, evaluate_result: str, passed: list[str] | None = None) -> dict[str, object]:
        archive, results = self.fixture(passed)
        return build(
            archive_completion=archive,
            results=results if evaluate_result == "success" else None,
            evaluate_result=evaluate_result,
            problem_id="two_plus_two",
            benchmark_commit="c" * 40,
            toolchain="leanprover/lean4:v4.32.0",
            evaluator_version="d" * 40,
        )

    def test_accepted_and_rejected_are_distinct(self) -> None:
        accepted = self.build("success", ["two_plus_two"])
        rejected = self.build("success", [])
        self.assertEqual(accepted["occurred_at"], "2026-08-21T12:00:00.001Z")
        self.assertEqual(accepted["outcome"], {
            "status": "accepted", "evaluator_version": "d" * 40,
        })
        self.assertEqual(rejected["outcome"], {
            "status": "rejected", "reason_code": "proof_rejected",
        })

    def test_pipeline_failure_is_explicit_and_retryable(self) -> None:
        completion = self.build("failure")
        self.assertEqual(completion["outcome"], {
            "status": "failed",
            "reason_code": "evaluation_pipeline_failed",
            "retryable": True,
        })

    def test_success_requires_a_results_document(self) -> None:
        archive, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, "requires results"):
            build(
                archive_completion=archive,
                results=None,
                evaluate_result="success",
                problem_id="two_plus_two",
                benchmark_commit="c" * 40,
                toolchain="leanprover/lean4:v4.32.0",
                evaluator_version="d" * 40,
            )


if __name__ == "__main__":
    unittest.main()
