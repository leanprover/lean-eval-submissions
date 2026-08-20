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

import results_schema as rs  # noqa: E402
import update_leaderboard as ul  # noqa: E402

BENCHMARK_COMMIT = "8e1b9cf5e1d3c2b1a0f9e8d7c6b5a4938271605f"
SUBMISSION_REF = "deadbeefcafef00dbaadc0de1234567890abcdef"


def default_call(
    *,
    leaderboard_dir: pathlib.Path,
    passed: list[str],
    user: str = "alice",
    now: str = "2026-04-11T10:45:00Z",
    submission_public: bool = True,
    submission_kind: str = "github_repo",
    submission_repo: str = "alice/proofs",
    model: str = "Claude Opus 4.6",
    issue_number: int = 42,
    benchmark_commit: str = BENCHMARK_COMMIT,
    statement_revisions: dict[str, int] | None = None,
    production_description: str | None = None,
    solution_publication_status: str | None = None,
    solution_publication_date: str | None = None,
) -> dict:
    return ul.update_leaderboard(
        user=user,
        leaderboard_dir=leaderboard_dir,
        passed=passed,
        benchmark_commit=benchmark_commit,
        submission_kind=submission_kind,
        submission_repo=submission_repo,
        submission_ref=SUBMISSION_REF,
        submission_public=submission_public,
        model=model,
        issue_number=issue_number,
        statement_revisions=statement_revisions,
        production_description=production_description,
        solution_publication_status=solution_publication_status,
        solution_publication_date=solution_publication_date,
        now=now,
    )


def load_user(root: pathlib.Path, user: str = "alice") -> dict:
    return json.loads((root / "results" / f"{user.lower()}.json").read_text())


def find_record(data: dict, model: str, problem_id: str, revision: int = 1) -> dict:
    matches = [
        record
        for record in data["results"]
        if record["declared_model"] == model
        and record["problem_id"] == problem_id
        and record["statement_revision"] == revision
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one record, found {len(matches)}")
    return matches[0]


def v1_file() -> dict:
    return {
        "schema_version": 1,
        "user": "alice",
        "solved": {
            "Claude Opus 4.6": {
                "two_plus_two": {
                    "solved_at": "2026-04-11T10:45:00Z",
                    "benchmark_commit": BENCHMARK_COMMIT,
                    "submission_kind": "github_repo",
                    "submission_repo": "alice/proofs",
                    "submission_ref": SUBMISSION_REF,
                    "submission_public": True,
                    "issue_number": 42,
                }
            }
        },
    }


class UpdateLeaderboardTests(unittest.TestCase):
    def test_first_write_creates_flat_schema_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            result = default_call(leaderboard_dir=root, passed=["two_plus_two"])
            self.assertTrue(result["changed"])
            data = load_user(root)
            self.assertEqual(data["schema_version"], 2)
            self.assertEqual(data["user"], "alice")
            record = find_record(data, "Claude Opus 4.6", "two_plus_two")
            self.assertEqual(
                record["result_id"],
                rs.result_id("alice", "Claude Opus 4.6", "two_plus_two", 1),
            )
            self.assertEqual(record["accepted_at"], "2026-04-11T10:45:00Z")
            self.assertEqual(record["benchmark_commit"], BENCHMARK_COMMIT)
            self.assertEqual(
                record["submission"],
                {
                    "kind": "github_repo",
                    "repo": "alice/proofs",
                    "ref": SUBMISSION_REF,
                    "public": True,
                },
            )
            self.assertEqual(record["intake"], {"kind": "issue", "issue_number": 42})
            self.assertEqual(record["production_metadata"], {})

    def test_reads_v1_and_writes_v2_when_adding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            target = root / "results" / "alice.json"
            target.parent.mkdir()
            target.write_text(json.dumps(v1_file()))
            result = default_call(leaderboard_dir=root, passed=["new_problem"])
            self.assertTrue(result["changed"])
            data = load_user(root)
            self.assertEqual(data["schema_version"], 2)
            self.assertEqual(len(data["results"]), 2)
            find_record(data, "Claude Opus 4.6", "two_plus_two")
            find_record(data, "Claude Opus 4.6", "new_problem")

    def test_duplicate_retry_is_sticky_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            default_call(leaderboard_dir=root, passed=["two_plus_two"])
            before = (root / "results" / "alice.json").read_bytes()
            result = default_call(
                leaderboard_dir=root,
                passed=["two_plus_two"],
                now="2026-05-01T00:00:00Z",
                production_description="must not overwrite",
            )
            self.assertFalse(result["changed"])
            self.assertEqual((root / "results" / "alice.json").read_bytes(), before)

    def test_new_records_append_without_reordering_existing_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            default_call(leaderboard_dir=root, passed=["z_problem"])
            first_id = load_user(root)["results"][0]["result_id"]
            default_call(leaderboard_dir=root, passed=["a_problem"])
            records = load_user(root)["results"]
            self.assertEqual(records[0]["result_id"], first_id)
            self.assertEqual(
                [record["problem_id"] for record in records],
                ["z_problem", "a_problem"],
            )

    def test_new_revision_and_different_model_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            default_call(leaderboard_dir=root, passed=["x"])
            self.assertTrue(
                default_call(
                    leaderboard_dir=root,
                    passed=["x"],
                    statement_revisions={"x": 2},
                )["changed"]
            )
            self.assertTrue(
                default_call(leaderboard_dir=root, passed=["x"], model="GPT-5.5")[
                    "changed"
                ]
            )
            data = load_user(root)
            find_record(data, "Claude Opus 4.6", "x", 1)
            find_record(data, "Claude Opus 4.6", "x", 2)
            find_record(data, "GPT-5.5", "x", 1)

    def test_metadata_and_gist_provenance_are_nested_losslessly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            default_call(
                leaderboard_dir=root,
                passed=["x"],
                submission_kind="gist",
                submission_repo="alice/abc123",
                production_description="Custom orchestrator.",
                solution_publication_status="published",
                solution_publication_date="2026-08-05",
            )
            record = find_record(load_user(root), "Claude Opus 4.6", "x")
            self.assertEqual(record["submission"]["kind"], "gist")
            self.assertEqual(
                record["production_metadata"],
                {
                    "production_description": "Custom orchestrator.",
                    "solution_publication_status": "published",
                    "solution_publication_date": "2026-08-05",
                },
            )

    def test_private_planned_publication_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            default_call(
                leaderboard_dir=root,
                passed=["x"],
                submission_public=False,
                solution_publication_status="planned",
                solution_publication_date="2027-01-15",
            )
            record = find_record(load_user(root), "Claude Opus 4.6", "x")
            self.assertFalse(record["submission"]["public"])
            self.assertEqual(
                record["production_metadata"]["solution_publication_status"],
                "planned",
            )

    def test_input_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cases = [
                ("40-char hex SHA", {"benchmark_commit": "notasha"}),
                ("GitHub login", {"user": "not a login"}),
                ("submission-kind", {"submission_kind": "bitbucket"}),
                ("positive integer", {"statement_revisions": {"x": 0}}),
            ]
            for message, overrides in cases:
                with self.subTest(message=message), self.assertRaisesRegex(
                    ul.UpdateError, message
                ):
                    default_call(leaderboard_dir=root, passed=["x"], **overrides)
            with self.assertRaisesRegex(ul.UpdateError, "requires a public"):
                default_call(
                    leaderboard_dir=root,
                    passed=["x"],
                    submission_public=False,
                    solution_publication_status="published",
                    solution_publication_date="2026-08-05",
                )
            with self.assertRaisesRegex(ul.UpdateError, "valid calendar date"):
                default_call(
                    leaderboard_dir=root,
                    passed=["x"],
                    solution_publication_status="published",
                    solution_publication_date="2026-02-30",
                )

    def test_description_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with self.assertRaisesRegex(ul.UpdateError, "NUL"):
                default_call(
                    leaderboard_dir=root,
                    passed=["x"],
                    production_description="bad\x00string",
                )
            with self.assertRaisesRegex(ul.UpdateError, "at most"):
                default_call(
                    leaderboard_dir=root,
                    passed=["x"],
                    production_description=(
                        "x" * (ul.PRODUCTION_DESCRIPTION_MAX_LEN + 1)
                    ),
                )

    def test_deduping_empty_and_case_insensitive_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            result = default_call(
                leaderboard_dir=root, passed=["a", "a", "b"], user="Alice"
            )
            self.assertEqual(result["added"], ["a", "b"])
            data = load_user(root)
            self.assertEqual(data["user"], "Alice")
            self.assertEqual(
                find_record(data, "Claude Opus 4.6", "a")["result_id"],
                rs.result_id("alice", "Claude Opus 4.6", "a", 1),
            )
            empty_root = root / "empty"
            self.assertFalse(
                default_call(leaderboard_dir=empty_root, passed=[])["changed"]
            )
            self.assertFalse((empty_root / "results" / "alice.json").exists())

    def test_unknown_or_malformed_schema_is_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            target = root / "results" / "alice.json"
            target.parent.mkdir()
            target.write_text(json.dumps({"schema_version": 999, "user": "alice"}))
            with self.assertRaisesRegex(ul.UpdateError, "schema_version"):
                default_call(leaderboard_dir=root, passed=["x"])
            target.write_text(
                json.dumps({"schema_version": 2, "user": "alice", "solved": {}})
            )
            with self.assertRaisesRegex(ul.UpdateError, "must contain only"):
                default_call(leaderboard_dir=root, passed=["x"])

    def test_cli_reads_revision_map_and_writes_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "store"
            root.mkdir()
            evaluation = pathlib.Path(tmp) / "results.json"
            evaluation.write_text(
                json.dumps(
                    {
                        "passed": ["two_plus_two"],
                        "statement_revisions": {"two_plus_two": 3},
                    }
                )
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = ul.main(
                    [
                        "--user", "alice",
                        "--leaderboard-dir", str(root),
                        "--results-json", str(evaluation),
                        "--benchmark-commit", BENCHMARK_COMMIT,
                        "--submission-kind", "github_repo",
                        "--submission-repo", "alice/proofs",
                        "--submission-ref", SUBMISSION_REF,
                        "--submission-public",
                        "--model", "Claude Opus 4.6",
                        "--issue-number", "42",
                        "--now", "2026-04-11T10:45:00Z",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(output.getvalue())["changed"])
            find_record(load_user(root), "Claude Opus 4.6", "two_plus_two", 3)


if __name__ == "__main__":
    unittest.main()
