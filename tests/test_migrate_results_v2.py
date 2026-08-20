from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import migrate_results_v2 as migration  # noqa: E402


def legacy_file(user: str = "alice", model: str = "Model", problem: str = "p") -> dict:
    return {
        "schema_version": 1,
        "user": user,
        "solved": {
            model: {
                problem: {
                    "solved_at": "2026-01-01T00:00:00Z",
                    "benchmark_commit": "a" * 40,
                    "submission_kind": "github_repo",
                    "submission_repo": f"{user.lower()}/proofs",
                    "submission_ref": "b" * 40,
                    "submission_public": True,
                    "issue_number": 7,
                    "production_description": "description",
                }
            }
        },
    }


class MigrationTests(unittest.TestCase):
    def test_dry_run_is_deterministic_and_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = pathlib.Path(tmp) / "results"
            results.mkdir()
            target = results / "alice.json"
            target.write_text(json.dumps(legacy_file(), indent=2) + "\n")
            before = target.read_bytes()
            first, writes = migration.build_migration_plan(
                results, source_commit="c" * 40
            )
            second, _ = migration.build_migration_plan(
                results, source_commit="c" * 40
            )
            self.assertEqual(first, second)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(first["source_record_count"], 1)
            self.assertEqual(first["output_record_count"], 1)
            self.assertTrue(first["preservation"]["record_count_equal"])
            self.assertEqual(first["preservation"]["v1_files_projected_exactly"], 1)
            self.assertIn(target, writes)

    def test_apply_requires_and_checks_reviewed_expectations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = pathlib.Path(tmp) / "results"
            results.mkdir()
            target = results / "alice.json"
            target.write_text(json.dumps(legacy_file()))
            report, _ = migration.build_migration_plan(
                results, source_commit="c" * 40
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = migration.main(
                    [
                        "--results-dir", str(results),
                        "--source-commit", "c" * 40,
                        "--apply",
                        "--expect-source-digest", report["source_digest"],
                        "--expect-record-count", "1",
                        "--expect-output-digest", report["canonical_output_digest"],
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(target.read_text())["schema_version"], 2)
            self.assertTrue(json.loads(output.getvalue())["applied"])

    def test_stale_expectation_fails_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = pathlib.Path(tmp) / "results"
            results.mkdir()
            target = results / "alice.json"
            target.write_text(json.dumps(legacy_file()))
            before = target.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                rc = migration.main(
                    [
                        "--results-dir", str(results),
                        "--source-commit", "c" * 40,
                        "--apply",
                        "--expect-source-digest", "wrong",
                        "--expect-record-count", "1",
                        "--expect-output-digest", "wrong",
                    ]
                )
            self.assertEqual(rc, 1)
            self.assertEqual(target.read_bytes(), before)

    def test_cross_file_identifier_collision_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = pathlib.Path(tmp) / "results"
            results.mkdir()
            # File names differ, but folded logins and identity fields collide.
            (results / "one.json").write_text(json.dumps(legacy_file(user="Alice")))
            (results / "two.json").write_text(json.dumps(legacy_file(user="alice")))
            report, _ = migration.build_migration_plan(
                results, source_commit="c" * 40
            )
            self.assertFalse(report["ready_to_apply"])
            self.assertEqual(len(report["duplicate_result_ids"]), 1)


if __name__ == "__main__":
    unittest.main()
