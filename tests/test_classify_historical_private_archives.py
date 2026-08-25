from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # The repository CI installs the pinned validator.
    Draft202012Validator = None  # type: ignore[assignment,misc]


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import classify_historical_private_archives as crosswalk
import inventory_historical_replay as historical_inventory
import migrate_archive_envelopes as migration
from key_capability_contract import archive_key_id
from results_schema import result_id

RESULTS_COMMIT = "a" * 40
AUDIT_COMMIT = "b" * 40
BENCHMARK = "c" * 40
OTHER_BENCHMARK = "d" * 40
SOURCE_REF = "e" * 40
USER = "private-user"
REPOSITORY = "private-user/private-repository"
MODEL = "Private Model"
SERVER_SUBMISSION_ID = "0198abcd-0000-7000-8000-000000000003"


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


def server_result(problem: str, submission_id: str) -> dict[str, object]:
    value = result(problem, 1)
    value["intake"] = {"kind": "server", "submission_id": submission_id}
    return value


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


def write_v3_archive(
    root: pathlib.Path,
    *,
    submission_id: str,
    problems: list[str],
) -> pathlib.Path:
    ciphertext = b"current per-submission ciphertext"
    target = root.joinpath(*migration.canonical_archive_path(submission_id).split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(ciphertext)
    recipient = "age1" + "q" * 40
    sidecar = {
        "schema_version": 3,
        "submission_id": submission_id,
        "submission_repo": REPOSITORY,
        "submission_ref": SOURCE_REF,
        "submission_kind": "github_repo",
        "submission_public": False,
        "submitter": USER,
        "model": MODEL,
        "size_bytes_plaintext_tar": 123,
        "sha256_plaintext_tar": digest(b"private plaintext commitment"),
        "size_bytes_ciphertext": len(ciphertext),
        "sha256_ciphertext": digest(ciphertext),
        "archived_at": "2026-05-25T16:30:00Z",
        "benchmark_commit": BENCHMARK,
        "archiver_workflow_run": (
            "https://github.com/leanprover/lean-eval-submissions/actions/runs/456"
        ),
        "problem_ids": problems,
        "evaluator_verdict": {problem: "pass" for problem in problems},
        "key_envelope": {
            "schema_version": 1,
            "submission_id": submission_id,
            "archive_ciphertext_sha256": digest(ciphertext),
            "data_key_id": archive_key_id(submission_id, recipient),
            "age_recipient": recipient,
            "adapter": "aws-kms-v1",
            "wrapped_identity": base64.b64encode(b"wrapped-key").decode("ascii"),
        },
    }
    target.with_suffix("").with_suffix(".json").write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


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

    def test_join_digest_is_from_the_exact_parsed_results_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            results_root, audit_root, plan_path = build(root, [result("one", 1)])
            write_archive(audit_root, issue=1, suffix="one", problems=["one"])
            plan = migration.build_plan(audit_root, AUDIT_COMMIT)
            plan_path.write_text(
                json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            inventory = historical_inventory.inventory(results_root, RESULTS_COMMIT)
            results_path = results_root / f"{USER}.json"
            changed = json.loads(results_path.read_text(encoding="utf-8"))
            changed["results"][0]["submission"]["repo"] = (
                "private-user/other-private-repository"
            )
            changed_bytes = (
                json.dumps(changed, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            real_read_bytes = pathlib.Path.read_bytes

            def substitute(path: pathlib.Path) -> bytes:
                if path == results_path:
                    return changed_bytes
                return real_read_bytes(path)

            with (
                mock.patch.object(pathlib.Path, "read_bytes", new=substitute),
                self.assertRaisesRegex(
                    crosswalk.CrosswalkError, "results store changed"
                ),
            ):
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

    def test_sidecar_is_parsed_and_hashed_from_one_immutable_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            results_root, audit_root, plan_path = build(root, [result("one", 1)])
            write_archive(audit_root, issue=1, suffix="one", problems=["one"])
            sidecar_path = next(audit_root.rglob("*.json"))
            original = sidecar_path.read_bytes()
            hybrid = json.loads(original.decode("utf-8"))
            hybrid["submission_id"] = SERVER_SUBMISSION_ID
            hybrid_bytes = json.dumps(hybrid).encode("utf-8")
            real_read_bytes = pathlib.Path.read_bytes
            reads = 0

            def changing_read(path: pathlib.Path) -> bytes:
                nonlocal reads
                if path == sidecar_path:
                    reads += 1
                    return original if reads == 1 else hybrid_bytes
                return real_read_bytes(path)

            with mock.patch.object(pathlib.Path, "read_bytes", new=changing_read):
                value = finish_build(results_root, audit_root, plan_path)
            self.assertEqual(value["classification_counts"]["bound"], 1)
            self.assertEqual(reads, 1)

    def test_sidecar_mutation_after_snapshot_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            results_root, audit_root, plan_path = build(root, [result("one", 1)])
            write_archive(audit_root, issue=1, suffix="one", problems=["one"])
            sidecar_path = next(audit_root.rglob("*.json"))
            original = sidecar_path.read_bytes()
            hybrid = json.loads(original.decode("utf-8"))
            hybrid["submission_id"] = SERVER_SUBMISSION_ID
            changed = (json.dumps(hybrid, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            )
            real_read_bytes = pathlib.Path.read_bytes
            mutated = False

            def mutate_after_read(path: pathlib.Path) -> bytes:
                nonlocal mutated
                value = real_read_bytes(path)
                if path == sidecar_path and not mutated:
                    mutated = True
                    sidecar_path.write_bytes(changed)
                return value

            with (
                mock.patch.object(pathlib.Path, "read_bytes", new=mutate_after_read),
                self.assertRaisesRegex(
                    crosswalk.CrosswalkError, "archive inventory changed"
                ),
            ):
                finish_build(results_root, audit_root, plan_path)

    def test_refuses_orphan_ciphertext(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            results_root, audit_root, plan_path = build(root, [result("one", 1)])
            write_archive(audit_root, issue=1, suffix="one", problems=["one"])
            orphan = audit_root / "audit/2026/05/private-orphan.tar.age"
            orphan.write_bytes(b"unreferenced private ciphertext")
            with self.assertRaisesRegex(
                crosswalk.CrosswalkError, "ciphertext inventory"
            ):
                finish_build(results_root, audit_root, plan_path)

    def test_rejects_schema_hybrids_and_missing_v3_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            _results_root, audit_root, _plan_path = build(root, [result("one", 1)])
            write_archive(audit_root, issue=1, suffix="one", problems=["one"])
            plan = migration.build_plan(audit_root, AUDIT_COMMIT)
            entry = plan["entries"][0]
            sidecar_path = next(audit_root.rglob("*.json"))
            legacy = json.loads(sidecar_path.read_text(encoding="utf-8"))
            cases = []
            with_submission_id = dict(legacy)
            with_submission_id["submission_id"] = SERVER_SUBMISSION_ID
            cases.append(with_submission_id)
            with_envelope = dict(legacy)
            with_envelope["key_envelope"] = {}
            cases.append(with_envelope)
            schema2_with_issue = dict(legacy)
            schema2_with_issue["schema_version"] = 2
            schema2_with_issue["submission_id"] = entry["submission_id"]
            cases.append(schema2_with_issue)
            schema2_with_envelope = dict(schema2_with_issue)
            schema2_with_envelope.pop("issue")
            schema2_with_envelope["key_envelope"] = {}
            cases.append(schema2_with_envelope)
            schema3_without_envelope = dict(schema2_with_envelope)
            schema3_without_envelope["schema_version"] = 3
            schema3_without_envelope.pop("key_envelope")
            cases.append(schema3_without_envelope)

            for value in cases:
                with (
                    self.subTest(fields=sorted(value)),
                    self.assertRaisesRegex(crosswalk.CrosswalkError, "field set"),
                ):
                    crosswalk._validate_sidecar_metadata(
                        value,
                        entry,
                        ciphertext_size=legacy["size_bytes_ciphertext"],
                    )

    def test_retained_v3_sidecar_is_independently_validated_and_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            records = [
                result("legacy", 1),
                server_result("current", SERVER_SUBMISSION_ID),
            ]
            results_root, audit_root, plan_path = build(root, records)
            write_archive(audit_root, issue=1, suffix="legacy", problems=["legacy"])
            target = write_v3_archive(
                audit_root,
                submission_id=SERVER_SUBMISSION_ID,
                problems=["current"],
            )
            value = finish_build(results_root, audit_root, plan_path)
            self.assertEqual(value["classification_counts"]["bound"], 2)
            by_id = {entry["result_id"]: entry for entry in value["entries"]}
            self.assertEqual(
                by_id[result_id(USER, MODEL, "current", 1)]["archive_schema_version"],
                3,
            )

            sidecar_path = target.with_suffix("").with_suffix(".json")
            invalid = json.loads(sidecar_path.read_text(encoding="utf-8"))
            invalid["key_envelope"]["submission_id"] = (
                "0198abcd-0000-7000-8000-000000000004"
            )
            sidecar_path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(crosswalk.CrosswalkError, "migration plan"):
                crosswalk._load_archives(audit_root, AUDIT_COMMIT, plan_path)

    def test_private_dependency_errors_are_sanitized_at_cli_boundary(self) -> None:
        secret = "private-owner/private-repository@private-ref"
        stderr = io.StringIO()
        argv = [
            "--results-root",
            ".",
            "--results-commit",
            RESULTS_COMMIT,
            "--expected-results-store-sha256",
            "0" * 64,
            "--expected-private-result-count",
            "1",
            "--audit-root",
            ".",
            "--audit-commit",
            AUDIT_COMMIT,
            "--archive-plan",
            "plan.json",
            "--expected-archive-inventory-digest",
            "1" * 64,
            "--output",
            "out.json",
        ]
        with (
            mock.patch.object(
                crosswalk,
                "build_crosswalk",
                side_effect=migration.MigrationError(secret),
            ),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(crosswalk.main(argv), 1)
        self.assertNotIn(secret, stderr.getvalue())
        self.assertEqual(
            stderr.getvalue(),
            "historical-private-archive-crosswalk: validation failed\n",
        )

    def test_migration_and_results_errors_do_not_escape_classifier(self) -> None:
        secret = "private-locator/private-name/private-value"
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                migration,
                "_load_plan",
                side_effect=migration.MigrationError(secret),
            ),
            self.assertRaises(crosswalk.CrosswalkError) as caught,
        ):
            crosswalk._load_archives(
                pathlib.Path(directory), AUDIT_COMMIT, pathlib.Path("plan")
            )
        self.assertNotIn(secret, str(caught.exception))

        with (
            mock.patch.object(
                crosswalk,
                "replay_inventory",
                side_effect=historical_inventory.InventoryError(secret),
            ),
            self.assertRaises(crosswalk.CrosswalkError) as caught,
        ):
            crosswalk._load_private_results(
                pathlib.Path("unused-results"), RESULTS_COMMIT, "0" * 64
            )
        self.assertNotIn(secret, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
