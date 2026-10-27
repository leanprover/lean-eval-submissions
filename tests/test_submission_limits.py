"""Boundary tests for compressed-source admission, without large allocations."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from fetch_submission import source_problem_ids
from submission_limits import CFSG_PROBLEM_ID, check_source_size, source_size_cap


class SubmissionLimitsTests(unittest.TestCase):
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

    def test_legacy_problem_names_come_from_toml_without_executing_lean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "cfsg").mkdir()
            (root / "bad").mkdir()
            (root / "lakefile.toml").write_text('name = "two_plus_two"\n')
            (root / "cfsg/lakefile.toml").write_text(f'name = "{CFSG_PROBLEM_ID}"\n')
            (root / "bad/lakefile.toml").write_text('name = [invalid\n')
            (root / "lakefile.lean").write_text('panic! "must never execute"\n')
            self.assertEqual(source_problem_ids(root), [CFSG_PROBLEM_ID, "two_plus_two"])


if __name__ == "__main__":
    unittest.main()
