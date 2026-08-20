from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import results_schema as rs  # noqa: E402


class ResultIdentifierTests(unittest.TestCase):
    def test_language_neutral_vectors(self) -> None:
        fixture = json.loads(
            (REPO_ROOT / "tests" / "fixtures" / "result_id_vectors.json").read_text()
        )
        for vector in fixture["vectors"]:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(
                    rs.result_id(
                        vector["user"],
                        vector["declared_model"],
                        vector["problem_id"],
                        vector["statement_revision"],
                    ),
                    vector["expected"],
                )

    def test_rejects_invalid_revision_and_surrogate(self) -> None:
        with self.assertRaisesRegex(rs.ResultsSchemaError, "positive integer"):
            rs.result_id("alice", "model", "problem", 0)
        with self.assertRaisesRegex(rs.ResultsSchemaError, "canonicalizable"):
            rs.result_id("alice", "bad\ud800", "problem", 1)


class ResultsSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.v1 = {
            "schema_version": 1,
            "user": "Alice",
            "solved": {
                "Model": {
                    "problem": {
                        "solved_at": "2026-01-01T00:00:00Z",
                        "benchmark_commit": "a" * 40,
                        "submission_kind": "github_repo",
                        "submission_repo": "alice/proofs",
                        "submission_ref": "b" * 40,
                        "submission_public": False,
                        "issue_number": 7,
                        "production_description": "agent harness",
                        "solution_publication_status": "planned",
                        "solution_publication_date": "2026-04-01",
                    }
                }
            },
        }

    def test_v1_conversion_preserves_every_field(self) -> None:
        converted = rs.convert_v1(self.v1)
        rs.validate_v2(converted)
        record = converted["results"][0]
        self.assertEqual(record["accepted_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(record["statement_revision"], 1)
        self.assertEqual(record["intake"], {"kind": "issue", "issue_number": 7})
        self.assertEqual(record["submission"]["repo"], "alice/proofs")
        self.assertEqual(
            set(record["production_metadata"]),
            {
                "production_description",
                "solution_publication_status",
                "solution_publication_date",
            },
        )

    def test_unknown_v1_field_fails_closed(self) -> None:
        self.v1["solved"]["Model"]["problem"]["future_field"] = "value"
        with self.assertRaisesRegex(rs.ResultsSchemaError, "unknown fields"):
            rs.convert_v1(self.v1)

    def test_v2_identifier_mismatch_and_duplicate_fail(self) -> None:
        converted = rs.convert_v1(self.v1)
        mismatched = copy.deepcopy(converted)
        mismatched["results"][0]["result_id"] = "r2_" + "0" * 64
        with self.assertRaisesRegex(rs.ResultsSchemaError, "does not match"):
            rs.validate_v2(mismatched)
        duplicate = copy.deepcopy(converted)
        duplicate["results"].append(copy.deepcopy(duplicate["results"][0]))
        with self.assertRaisesRegex(rs.ResultsSchemaError, "duplicate result_id"):
            rs.validate_v2(duplicate)

    def test_canonical_bytes_are_deterministic(self) -> None:
        converted = rs.convert_v1(self.v1)
        first = rs.canonical_file_bytes(converted)
        second = rs.canonical_file_bytes(json.loads(first))
        self.assertEqual(first, second)

    def test_machine_readable_schema_is_v2_and_strict(self) -> None:
        schema = json.loads(
            (REPO_ROOT / "schemas" / "results-v2.schema.json").read_text()
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], 2)
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["$defs"]["result"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
