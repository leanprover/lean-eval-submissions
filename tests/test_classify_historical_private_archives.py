from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # The repository CI installs the pinned validator.
    Draft202012Validator = None  # type: ignore[assignment,misc]


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import classify_historical_private_archives as crosswalk
import inventory_historical_replay as historical_inventory
import migrate_archive_envelopes as migration
from results_schema import result_id

RESULTS_COMMIT = "a" * 40
AUDIT_COMMIT = "b" * 40
BENCHMARK = "c" * 40
OTHER_BENCHMARK = "d" * 40
SOURCE_REF = "e" * 40
USER = "private-user"
REPOSITORY = "private-user/private-repository"
MODEL = "Private Model"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def result(
    problem: str, issue: int, *, benchmark: str = BENCHMARK
) -> dict[str, object]:
    return {
        "result_id": result_id(USER, MODEL, problem, 1),
        "problem_id": problem,
        "statement_revision": 1,
        "declared_model": MODEL,
        "accepted_at": "2026-05-26T00:00:00Z",
        "benchmark_commit": benchmark,
        "intake": {"kind": "issue", "issue_number": issue},
        "submission": {
            "kind": "github_repo",
            "repo": REPOSITORY,
            "ref": SOURCE_REF,
            "public": False,
        },
        "production_metadata": {},
    }


def write_results(root: pathlib.Path, records: list[dict[str, object]]) -> pathlib.Path:
    results_root = root / "results"
    results_root.mkdir()
    (results_root / f"{USER}.json").write_text(
        json.dumps(
            {"schema_version": 2, "user": USER, "results": records},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return results_root


def write_archive(
    root: pathlib.Path,
    *,
    issue: int,
    suffix: str,
    problems: list[str] | None,
    model: str = MODEL,
    public: bool = False,
) -> None:
    ciphertext = f"ciphertext-{issue}-{suffix}".encode()
    target = root / f"audit/2026/05/{USER}-{issue}-{suffix}.tar.age"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(ciphertext)
    sidecar: dict[str, object] = {
        "schema_version": 1,
        "issue": issue,
        "submission_repo": REPOSITORY,
        "submission_ref": SOURCE_REF,
        "submission_kind": "github_repo",
        "submission_public": public,
        "submitter": USER,
        "model": model,
        "size_bytes_plaintext_tar": 123,
        "sha256_plaintext_tar": digest(b"private plaintext commitment"),
        "size_bytes_ciphertext": len(ciphertext),
        "sha256_ciphertext": digest(ciphertext),
        "archived_at": "2026-05-25T16:30:00Z",
        "benchmark_commit": BENCHMARK,
        "archiver_workflow_run": (
            "https://github.com/leanprover/lean-eval-submissions/actions/runs/123"
        ),
    }
    if problems is not None:
        sidecar["problem_ids"] = problems
        sidecar["evaluator_verdict"] = {problem: "pass" for problem in problems}
    target.with_suffix("").with_suffix(".json").write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build(
    root: pathlib.Path,
    records: list[dict[str, object]],
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    results_root = write_results(root, records)
    audit_root = root / "audit-repository"
    audit_root.mkdir()
    plan_path = root / "archive-plan.json"
    return results_root, audit_root, plan_path


def finish_build(
    results_root: pathlib.Path,
    audit_root: pathlib.Path,
    plan_path: pathlib.Path,
) -> dict[str, object]:
    plan = migration.build_plan(audit_root, AUDIT_COMMIT)
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    inventory = historical_inventory.inventory(results_root, RESULTS_COMMIT)
    return crosswalk.build_crosswalk(
        results_root=results_root,
        results_commit=RESULTS_COMMIT,
        expected_results_store_sha256=inventory["results_store_sha256"],
        expected_private_result_count=inventory["classification_counts"][
            "private_archive_migration_pending"
        ],
        audit_root=audit_root,
        audit_commit=AUDIT_COMMIT,
        archive_plan=plan_path,
        expected_archive_inventory_digest=plan["inventory_digest"],
        verify_git_checkouts=False,
    )


class HistoricalPrivateArchiveCrosswalkTests(unittest.TestCase):
    def test_git_checkout_binding_rejects_a_false_commit_label(self) -> None:
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        crosswalk._require_git_checkout(
            ROOT / "results",
            head,
            "results",
            require_repository_root=False,
        )
        wrong = "0" * 40 if head != "0" * 40 else "1" * 40
        with self.assertRaisesRegex(crosswalk.CrosswalkError, "selected commit"):
            crosswalk._require_git_checkout(
                ROOT / "results",
                wrong,
                "results",
                require_repository_root=False,
            )

    def test_classifies_every_result_without_copying_private_join_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            records = [
                result("bound_exact", 1),
                result("bound_replayed", 1, benchmark=OTHER_BENCHMARK),
                result("missing", 2),
                result("conflict", 3),
                result("ambiguous", 4),
            ]
            results_root, audit_root, plan_path = build(root, records)
            write_archive(
                audit_root,
                issue=1,
                suffix="one",
                problems=["bound_exact", "bound_replayed"],
            )
            write_archive(
                audit_root,
                issue=3,
                suffix="three",
                problems=["conflict"],
                model="Different Model",
            )
            write_archive(audit_root, issue=4, suffix="four-a", problems=["ambiguous"])
            write_archive(audit_root, issue=4, suffix="four-b", problems=["ambiguous"])
            value = finish_build(results_root, audit_root, plan_path)

            self.assertEqual(
                value["classification_counts"],
                {
                    "bound": 2,
                    "archive_not_found": 1,
                    "archive_identity_ambiguous": 1,
                    "archive_metadata_conflict": 1,
                },
            )
            entries = {entry["result_id"]: entry for entry in value["entries"]}
            exact = entries[result_id(USER, MODEL, "bound_exact", 1)]
            replayed = entries[result_id(USER, MODEL, "bound_replayed", 1)]
            self.assertEqual(exact["archive_result_evidence"], "confirmed_pass")
            self.assertEqual(exact["benchmark_relation"], "same")
            self.assertEqual(
                replayed["benchmark_relation"], "archive_recorded_different"
            )
            self.assertEqual(
                entries[result_id(USER, MODEL, "conflict", 1)]["reason"],
                "declared_model_mismatch",
            )
            self.assertEqual(
                entries[result_id(USER, MODEL, "ambiguous", 1)]["candidate_count"],
                2,
            )

            encoded = crosswalk.canonical_output_bytes(value)
            for private_value in (REPOSITORY, SOURCE_REF, "bound_exact", MODEL, USER):
                self.assertNotIn(private_value.encode(), encoded)
            self.assertEqual(
                [entry["result_id"] for entry in value["entries"]],
                sorted(entry["result_id"] for entry in value["entries"]),
            )

            if Draft202012Validator is not None:
                schema = json.loads(
                    (
                        ROOT
                        / "schemas/historical-private-archive-crosswalk-v1.schema.json"
                    ).read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(schema)
                self.assertEqual(
                    list(Draft202012Validator(schema).iter_errors(value)), []
                )

    def test_legacy_sidecar_without_problem_evidence_is_still_source_bound(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            results_root, audit_root, plan_path = build(root, [result("old", 1)])
            write_archive(audit_root, issue=1, suffix="old", problems=None)
            value = finish_build(results_root, audit_root, plan_path)
            self.assertEqual(value["classification_counts"]["bound"], 1)
            self.assertEqual(
                value["entries"][0]["archive_result_evidence"],
                "legacy_unrecorded",
            )

    def test_refuses_stale_results_and_archive_digests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            results_root, audit_root, plan_path = build(root, [result("one", 1)])
            write_archive(audit_root, issue=1, suffix="one", problems=["one"])
            plan = migration.build_plan(audit_root, AUDIT_COMMIT)
            plan_path.write_text(
                json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            inventory = historical_inventory.inventory(results_root, RESULTS_COMMIT)
            with self.assertRaisesRegex(
                crosswalk.CrosswalkError, "results-store digest"
            ):
                crosswalk.build_crosswalk(
                    results_root=results_root,
                    results_commit=RESULTS_COMMIT,
                    expected_results_store_sha256="0" * 64,
                    expected_private_result_count=1,
                    audit_root=audit_root,
                    audit_commit=AUDIT_COMMIT,
                    archive_plan=plan_path,
                    expected_archive_inventory_digest=plan["inventory_digest"],
                    verify_git_checkouts=False,
                )
            with self.assertRaisesRegex(
                crosswalk.CrosswalkError, "archive inventory digest"
            ):
                crosswalk.build_crosswalk(
                    results_root=results_root,
                    results_commit=RESULTS_COMMIT,
                    expected_results_store_sha256=inventory["results_store_sha256"],
                    expected_private_result_count=1,
                    audit_root=audit_root,
                    audit_commit=AUDIT_COMMIT,
                    archive_plan=plan_path,
                    expected_archive_inventory_digest="0" * 64,
                    verify_git_checkouts=False,
                )

    def test_refuses_a_plan_that_no_longer_matches_archive_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            results_root, audit_root, plan_path = build(root, [result("one", 1)])
            write_archive(audit_root, issue=1, suffix="one", problems=["one"])
            plan = migration.build_plan(audit_root, AUDIT_COMMIT)
            plan_path.write_text(
                json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            inventory = historical_inventory.inventory(results_root, RESULTS_COMMIT)
            ciphertext = next(audit_root.rglob("*.tar.age"))
            ciphertext.write_bytes(b"changed after planning")
            with self.assertRaisesRegex(crosswalk.CrosswalkError, "migration plan"):
                crosswalk.build_crosswalk(
                    results_root=results_root,
                    results_commit=RESULTS_COMMIT,
                    expected_results_store_sha256=inventory["results_store_sha256"],
                    expected_private_result_count=1,
                    audit_root=audit_root,
                    audit_commit=AUDIT_COMMIT,
                    archive_plan=plan_path,
                    expected_archive_inventory_digest=plan["inventory_digest"],
                    verify_git_checkouts=False,
                )

    def test_refuses_unknown_audit_json_and_nonexclusive_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            results_root, audit_root, plan_path = build(root, [result("one", 1)])
            write_archive(audit_root, issue=1, suffix="one", problems=["one"])
            plan = migration.build_plan(audit_root, AUDIT_COMMIT)
            plan_path.write_text(
                json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (audit_root / "unrelated.json").write_text("{}\n", encoding="utf-8")
            inventory = historical_inventory.inventory(results_root, RESULTS_COMMIT)
            with self.assertRaisesRegex(crosswalk.CrosswalkError, "unrelated"):
                crosswalk.build_crosswalk(
                    results_root=results_root,
                    results_commit=RESULTS_COMMIT,
                    expected_results_store_sha256=inventory["results_store_sha256"],
                    expected_private_result_count=1,
                    audit_root=audit_root,
                    audit_commit=AUDIT_COMMIT,
                    archive_plan=plan_path,
                    expected_archive_inventory_digest=plan["inventory_digest"],
                    verify_git_checkouts=False,
                )
            output = root / "output.json"
            output.write_text("existing\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                crosswalk.write_exclusive(output, {})


if __name__ == "__main__":
    unittest.main()
