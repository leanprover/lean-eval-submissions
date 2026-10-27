"""Boundary tests for compressed-source admission, without large allocations."""

from __future__ import annotations

import pathlib
import json
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from fetch_submission import source_problem_ids
from submission_limits import CFSG_PROBLEM_ID, check_source_size, source_size_cap


class SubmissionLimitsTests(unittest.TestCase):
    def test_replay_transport_limit_remains_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sidecar = pathlib.Path(directory) / "sidecar.json"
            for size, accepted in [(11 * 1024**2, True), (11 * 1024**2 + 1, False), (True, False)]:
                sidecar.write_text(json.dumps({"size_bytes_ciphertext": size}))
                result = subprocess.run([
                    sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "scripts/submission_limits.py"),
                    "--replay-sidecar", str(sidecar),
                ], capture_output=True)
                self.assertEqual(result.returncode == 0, accepted)

    def test_default_and_cfsg_exact_boundaries(self) -> None:
        for metadata, cap in [
            ({}, 1_000_000_000),
            ({"problem_id": "two_plus_two"}, 1_000_000_000),
            ({"problem_id": CFSG_PROBLEM_ID}, 10_000_000_000),
            ({"problem_ids": ["two_plus_two", CFSG_PROBLEM_ID]}, 10_000_000_000),
        ]:
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as directory:
                source = pathlib.Path(directory) / "source.tar.gz"
                with source.open("wb") as stream:
                    stream.truncate(cap)
                self.assertEqual(check_source_size(source, metadata), (cap, cap))
                with source.open("r+b") as stream:
                    stream.truncate(cap + 1)
                with self.assertRaisesRegex(ValueError, "over the"):
                    check_source_size(source, metadata)

    def test_service_selection_cannot_borrow_another_workspace_exception(self) -> None:
        self.assertEqual(source_size_cap({
            "problem_id": "two_plus_two", "problem_ids": [CFSG_PROBLEM_ID],
        }), 1_000_000_000)
        self.assertEqual(source_size_cap({"problem_id": "CFSG"}), 1_000_000_000)

    def test_legacy_forks_need_a_changed_cfsg_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "fork"
            workspace = source / "generated" / CFSG_PROBLEM_ID
            pristine = root / "trusted" / CFSG_PROBLEM_ID
            workspace.mkdir(parents=True)
            pristine.mkdir(parents=True)
            (workspace / "lakefile.toml").write_text(f'name = "{CFSG_PROBLEM_ID}"\n')
            (pristine / "Submission.lean").write_text('theorem cfsg := by sorry\n')
            (workspace / "Submission.lean").write_text('theorem cfsg := by sorry\n')
            (root / "lakefile.lean").write_text('panic! "must never execute"\n')
            self.assertEqual(source_problem_ids(source, root / "trusted"), [])
            (workspace / "Submission.lean").write_text('theorem cfsg := actual_proof\n')
            self.assertEqual(source_problem_ids(source, root / "trusted"), [CFSG_PROBLEM_ID])
            self.assertEqual(source_problem_ids(source), [])
            self.assertEqual(source_problem_ids(source, root / "missing-baseline"), [])
            (workspace / "Submission.lean").unlink()
            self.assertEqual(source_problem_ids(source, root / "trusted"), [])


if __name__ == "__main__":
    unittest.main()
