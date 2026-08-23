from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_result_receipt as receipt  # noqa: E402
from results_schema import canonical_file_bytes, result_id  # noqa: E402


SUBMISSION_ID = "0198abcd-1111-7000-8000-000000000001"


class ResultReceiptTests(unittest.TestCase):
    def test_tree_digest_cross_runtime_vector(self) -> None:
        contents = (
            '{"schema_version":2,"user":"Alice","results":[{"result_id":'
            '"r2_ecad1e075c37192258a92f9c40ffa743864404c99cd14f790ecd26e80dc4ddaf",'
            '"problem_id":"two_plus_two","statement_revision":2,'
            '"declared_model":"Example Model"}]}'
        ).encode("utf-8")
        self.assertEqual(
            receipt.result_tree_digest("results/alice.json", contents),
            "00e10d25d0f8a5a1acb0f838db8011a2d570677bb062925cd100b7297fc4f0b2",
        )

    def _store(self, root: pathlib.Path) -> pathlib.Path:
        path = root / "results" / "alice.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(canonical_file_bytes({
            "schema_version": 2,
            "user": "Alice",
            "results": [{
                "result_id": result_id("alice", "Example Model", "two_plus_two", 2),
                "problem_id": "two_plus_two",
                "statement_revision": 2,
                "declared_model": "Example Model",
                "accepted_at": "2026-08-23T02:00:00Z",
                "benchmark_commit": "a" * 40,
                "intake": {"kind": "server", "submission_id": SUBMISSION_ID},
                "submission": {
                    "kind": "github_repo",
                    "repo": "alice/proofs",
                    "ref": "b" * 40,
                    "public": False,
                },
                "production_metadata": {},
            }],
        }))
        return path

    def test_builds_exact_commit_and_tree_bound_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            result_file = self._store(root)
            built = receipt.build_receipt(
                results_file=result_file,
                results_root=root,
                submission_id=SUBMISSION_ID,
                user="alice",
                declared_model="Example Model",
                problem_id="two_plus_two",
                statement_revision=2,
                result_branch="staging-results",
                result_commit="c" * 40,
                occurred_at="2026-08-23T02:01:02.003Z",
            )
            self.assertEqual(built["result_path"], "results/alice.json")
            self.assertEqual(
                built["result_id"],
                result_id("alice", "Example Model", "two_plus_two", 2),
            )
            self.assertEqual(
                built["result_tree_digest"],
                receipt.result_tree_digest("results/alice.json", result_file.read_bytes()),
            )

    def test_rejects_wrong_identity_path_and_noncanonical_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            result_file = self._store(root)
            arguments = dict(
                results_file=result_file,
                results_root=root,
                submission_id=SUBMISSION_ID,
                user="alice",
                declared_model="Example Model",
                problem_id="two_plus_two",
                statement_revision=2,
                result_branch="staging-results",
                result_commit="c" * 40,
                occurred_at="2026-08-23T02:01:02.003Z",
            )
            with self.assertRaisesRegex(receipt.ReceiptError, "exactly one"):
                receipt.build_receipt(**{**arguments, "declared_model": "Other"})
            with self.assertRaisesRegex(receipt.ReceiptError, "result path"):
                receipt.build_receipt(**{**arguments, "user": "bob"})
            parsed = json.loads(result_file.read_text(encoding="utf-8"))
            result_file.write_text(json.dumps(parsed), encoding="utf-8")
            with self.assertRaisesRegex(receipt.ReceiptError, "canonical"):
                receipt.build_receipt(**arguments)


if __name__ == "__main__":
    unittest.main()
