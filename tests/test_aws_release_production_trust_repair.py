import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROCEDURE = ROOT / "docs" / "aws-release-production-trust-repair.md"


class ProductionReleaseTrustRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.procedure = PROCEDURE.read_text()

    def test_pins_exact_old_and_new_trust_subjects(self) -> None:
        self.assertIn(
            "repo:leanprover/lean-eval-releases:environment:release-production",
            self.procedure,
        )
        self.assertIn(
            "repo:leanprover@7233018/lean-eval-releases@1340741242:"
            "environment:release-production",
            self.procedure,
        )
        self.assertIn("arn:aws:iam::161072922960", self.procedure)

    def test_reuses_live_template_and_closes_change_set(self) -> None:
        self.assertIn("--use-previous-template", self.procedure)
        self.assertNotIn("--template-body", self.procedure)
        self.assertNotIn("sam package", self.procedure)
        self.assertNotIn("sam deploy", self.procedure)
        self.assertIn("(.Changes | length) == 1", self.procedure)
        self.assertIn(
            '.Changes[0].ResourceChange.LogicalResourceId == "ReleaseInvokerRole"',
            self.procedure,
        )
        self.assertIn(
            '.Changes[0].ResourceChange.ResourceType == "AWS::IAM::Role"',
            self.procedure,
        )
        self.assertIn(
            '.Changes[0].ResourceChange.Replacement == "False"',
            self.procedure,
        )
        self.assertNotIn("aws iam update-assume-role-policy", self.procedure)

    def test_change_set_ownership_is_unique_id_bound_before_all_operations(
        self,
    ) -> None:
        self.assertIn('select(keys == ["Id", "StackId"])', self.procedure)
        self.assertIn("LEAN_EVAL_CHANGE_SET_OWNED=true", self.procedure)
        self.assertIn("LEAN_EVAL_CHANGE_SET_EXECUTION_ATTEMPTED=true", self.procedure)
        self.assertGreaterEqual(
            self.procedure.count('--change-set-name "$LEAN_EVAL_CHANGE_SET_ID"'),
            4,
        )
        execute = self.procedure.index("aws cloudformation execute-change-set")
        attempted = self.procedure.index("LEAN_EVAL_CHANGE_SET_EXECUTION_ATTEMPTED=true")
        self.assertLess(attempted, execute)

    def test_create_response_accepts_only_exact_returned_unique_arn(self) -> None:
        match = re.search(
            r'(?s)LEAN_EVAL_CHANGE_SET_ID="\$\(jq -er \\.*?'
            r'--arg name "\$LEAN_EVAL_CHANGE_SET" \'(.*?)\' '
            r'"\$LEAN_EVAL_AWS_OPS/create-change-set.json"\)"',
            self.procedure,
        )
        self.assertIsNotNone(match)
        jq_filter = match.group(1)
        stack = "lean-eval-key-adapter-production"
        name = "release-oidc-production-contract"
        unique_id = "12345678-abcd-4abc-8def-1234567890ab"
        valid = {
            "Id": (
                "arn:aws:cloudformation:us-east-1:161072922960:changeSet/"
                f"{name}/{unique_id}"
            ),
            "StackId": (
                "arn:aws:cloudformation:us-east-1:161072922960:stack/"
                f"{stack}/{unique_id}"
            ),
        }

        def check(value: dict[str, Any]) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    "jq",
                    "-er",
                    "--arg",
                    "stack",
                    stack,
                    "--arg",
                    "name",
                    name,
                    jq_filter,
                ],
                input=json.dumps(value),
                check=False,
                capture_output=True,
                text=True,
            )

        result = check(valid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), valid["Id"])
        for case, value in (
            ("wrong-name", {**valid, "Id": valid["Id"].replace(name, "other")}),
            (
                "wrong-stack",
                {**valid, "StackId": valid["StackId"].replace(stack, "other")},
            ),
            ("malformed-id", {**valid, "Id": valid["Id"].rsplit("/", 1)[0] + "/-"}),
            ("extra-key", {**valid, "Unexpected": True}),
        ):
            with self.subTest(case=case):
                self.assertNotEqual(check(value).returncode, 0)

    def test_keeps_preflight_outside_workload_and_publication_paths(self) -> None:
        self.assertIn("PUBLICATION_ENABLED", self.procedure)
        self.assertIn("verify-production-release-oidc.yml", self.procedure)
        self.assertIn("Do not dispatch the publication controller", self.procedure)
        self.assertIn("lambda:InvokeFunction", self.procedure)
        self.assertIn("lean-eval-archive-unwrap-production:live", self.procedure)
        self.assertIn("cmp <(jq -S .", self.procedure)
        self.assertEqual(self.procedure.count("list-role-policies"), 2)
        self.assertEqual(self.procedure.count("list-attached-role-policies"), 2)
        self.assertIn("LEAN_EVAL_STAGING_UPDATED_BEFORE", self.procedure)

    def test_pins_protected_release_main_and_exact_dispatch_input(self) -> None:
        release_commit = "ff37a9d56aeb6906527cf7b75917907423d6f139"
        self.assertIn(f"LEAN_EVAL_RELEASES_COMMIT={release_commit}", self.procedure)
        self.assertIn("--ref main", self.procedure)
        self.assertIn(
            '-f expected_release_commit="$LEAN_EVAL_RELEASES_COMMIT"',
            self.procedure,
        )
        self.assertIn("--jq .protected", self.procedure)
        self.assertIn("PRODUCTION_RELEASE_TRUST_REPAIR_OK", self.procedure)

    def _run_cleanup_contract(
        self,
        *,
        owned: bool,
        execution_attempted: bool,
        complete: bool,
        requested_status: int,
        path_kind: str = "normal",
    ) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, str, pathlib.Path]:
        match = re.search(
            r"(?ms)^(lean_eval_remove_operator_material\(\) \{.*?^\}\n\n"
            r"lean_eval_cleanup\(\) \{.*?^\})\ntrap lean_eval_cleanup EXIT$",
            self.procedure,
        )
        self.assertIsNotNone(match)
        cleanup_functions = match.group(1)

        temporary_root = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, temporary_root, True)
        operator_basename = "lean-eval-production-trust.A1b2C3d4"
        cleanup_target = temporary_root / operator_basename
        if path_kind == "normal":
            operator_path = cleanup_target
            cleanup_target.mkdir(mode=0o700)
        elif path_kind == "traversal":
            (temporary_root / "subdirectory").mkdir()
            cleanup_target.mkdir(mode=0o700)
            operator_path = (
                temporary_root / "subdirectory" / ".." / operator_basename
            )
        elif path_kind == "symlink":
            cleanup_target = temporary_root / "symlink-target"
            cleanup_target.mkdir(mode=0o700)
            operator_path = temporary_root / operator_basename
            operator_path.symlink_to(cleanup_target, target_is_directory=True)
        elif path_kind == "wrong_parent":
            wrong_parent = temporary_root / "wrong-parent"
            wrong_parent.mkdir()
            cleanup_target = wrong_parent / operator_basename
            cleanup_target.mkdir(mode=0o700)
            operator_path = cleanup_target
        else:
            self.fail(f"unknown path kind: {path_kind}")
        (cleanup_target / "operator-material.json").write_text("operator material")

        aws_log = temporary_root / "aws.log"
        change_set_id = (
            "arn:aws:cloudformation:us-east-1:161072922960:changeSet/"
            "release-oidc-production-contract/12345678-abcd"
        )
        script = f"""
set -u
LEAN_EVAL_PRODUCTION_STACK=lean-eval-key-adapter-production
LEAN_EVAL_AWS_REGION=us-east-1
LEAN_EVAL_OPERATOR_TMP_ROOT={temporary_root!s}
LEAN_EVAL_AWS_OPS={operator_path!s}
LEAN_EVAL_CHANGE_SET=release-oidc-production-contract
LEAN_EVAL_CHANGE_SET_ID={change_set_id}
LEAN_EVAL_CHANGE_SET_OWNED={'true' if owned else 'false'}
LEAN_EVAL_CHANGE_SET_EXECUTION_ATTEMPTED={
            'true' if execution_attempted else 'false'
        }
LEAN_EVAL_TRUST_PREFLIGHT_COMPLETE={'true' if complete else 'false'}
aws() {{ printf '%s\\n' "$*" >> {aws_log!s}; }}
{cleanup_functions}
trap lean_eval_cleanup EXIT
exit {requested_status}
"""
        result = subprocess.run(
            ["bash", "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )
        aws_calls = aws_log.read_text() if aws_log.exists() else ""
        return result, operator_path, aws_calls, cleanup_target

    def test_cleanup_deletes_owned_unexecuted_id_and_operator_material(self) -> None:
        result, operator_dir, aws_calls, _ = self._run_cleanup_contract(
            owned=True,
            execution_attempted=False,
            complete=False,
            requested_status=7,
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertFalse(operator_dir.exists())
        self.assertIn("cloudformation delete-change-set", aws_calls)
        self.assertIn(
            "--change-set-name arn:aws:cloudformation:us-east-1:161072922960:"
            "changeSet/release-oidc-production-contract/12345678-abcd",
            aws_calls,
        )

    def test_cleanup_never_deletes_unowned_name_or_create_failure(self) -> None:
        result, operator_dir, aws_calls, _ = self._run_cleanup_contract(
            owned=False,
            execution_attempted=False,
            complete=False,
            requested_status=9,
        )
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertFalse(operator_dir.exists())
        self.assertEqual(aws_calls, "")

    def test_lost_execute_response_never_deletes_or_reports_success(self) -> None:
        result, operator_dir, aws_calls, _ = self._run_cleanup_contract(
            owned=True,
            execution_attempted=True,
            complete=False,
            requested_status=0,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("INCOMPLETE", result.stderr)
        self.assertFalse(operator_dir.exists())
        self.assertEqual(aws_calls, "")

    def test_cleanup_allows_success_only_after_preflight(self) -> None:
        result, operator_dir, aws_calls, _ = self._run_cleanup_contract(
            owned=True,
            execution_attempted=True,
            complete=True,
            requested_status=0,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(operator_dir.exists())
        self.assertEqual(aws_calls, "")

    def test_cleanup_rejects_traversal_symlink_and_wrong_parent(self) -> None:
        for path_kind in ("traversal", "symlink", "wrong_parent"):
            with self.subTest(path_kind=path_kind):
                result, _, aws_calls, cleanup_target = self._run_cleanup_contract(
                    owned=False,
                    execution_attempted=False,
                    complete=False,
                    requested_status=0,
                    path_kind=path_kind,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("REFUSING", result.stderr)
                self.assertTrue(cleanup_target.exists())
                self.assertEqual(aws_calls, "")

    def _jq_filter(self, output_name: str) -> str:
        output_marker = f"' \"$LEAN_EVAL_AWS_OPS/{output_name}\""
        output_end = self.procedure.index(output_marker) + len(output_marker)
        filter_start = self.procedure.rfind("\njq -e ", 0, output_end) + 1
        self.assertGreater(filter_start, 0)
        command = self.procedure[filter_start:output_end]
        match = re.search(r"(?s)'(.*)' \"\$LEAN_EVAL_AWS_OPS/", command)
        self.assertIsNotNone(match)
        return match.group(1)

    def _run_jq_contract(
        self,
        jq_filter: str,
        value: dict[str, Any],
        *,
        head: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = ["jq", "-e"]
        if head is not None:
            command.extend(["--arg", "head", head])
        command.extend(["--argjson", "run_id", "12345", jq_filter])
        return subprocess.run(
            command,
            input=json.dumps(value),
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _valid_jobs() -> dict[str, Any]:
        names = ["authorize", "oidc-trust", "summarize"]
        return {
            "total_count": 3,
            "jobs": [
                {
                    "id": index,
                    "name": name,
                    "run_id": 12345,
                    "run_attempt": 1,
                    "head_sha": "f" * 40,
                    "status": "completed",
                    "conclusion": "success",
                }
                for index, name in enumerate(names, 1)
            ],
        }

    def test_preflight_jobs_proof_accepts_only_exact_attempt_one_jobs(self) -> None:
        jq_filter = self._jq_filter("preflight-jobs.json")
        valid = self._valid_jobs()
        head = "f" * 40
        self.assertEqual(
            self._run_jq_contract(jq_filter, valid, head=head).returncode,
            0,
        )

        invalid: dict[str, dict[str, Any]] = {}
        invalid["empty"] = {"total_count": 0, "jobs": []}
        invalid["object-valued-jobs"] = {"total_count": 3, "jobs": {}}
        invalid["missing"] = {"total_count": 2, "jobs": valid["jobs"][:2]}
        invalid["truncated"] = {"total_count": 3, "jobs": valid["jobs"][:2]}
        invalid["extra"] = {
            "total_count": 4,
            "jobs": [
                *valid["jobs"],
                {**valid["jobs"][0], "id": 4, "name": "extra"},
            ],
        }
        invalid["skipped"] = {
            **valid,
            "jobs": [
                valid["jobs"][0],
                {**valid["jobs"][1], "conclusion": "skipped"},
                valid["jobs"][2],
            ],
        }
        invalid["wrong-run"] = {
            **valid,
            "jobs": [
                {**valid["jobs"][0], "run_id": 999},
                *valid["jobs"][1:],
            ],
        }
        invalid["rerun"] = {
            **valid,
            "jobs": [
                {**valid["jobs"][0], "run_attempt": 2},
                *valid["jobs"][1:],
            ],
        }
        invalid["wrong-head"] = {
            **valid,
            "jobs": [
                {**valid["jobs"][0], "head_sha": "0" * 40},
                *valid["jobs"][1:],
            ],
        }
        invalid["duplicate-id"] = {
            **valid,
            "jobs": [
                valid["jobs"][0],
                {**valid["jobs"][1], "id": 1},
                valid["jobs"][2],
            ],
        }
        invalid["fractional-id"] = {
            **valid,
            "jobs": [
                {**valid["jobs"][0], "id": 1.5},
                *valid["jobs"][1:],
            ],
        }
        for case, value in invalid.items():
            with self.subTest(case=case):
                self.assertNotEqual(
                    self._run_jq_contract(jq_filter, value, head=head).returncode,
                    0,
                )

    def test_preflight_run_proof_binds_id_head_and_attempt(self) -> None:
        jq_filter = self._jq_filter("preflight-run.json")
        head = "f" * 40
        valid: dict[str, Any] = {
            "databaseId": 12345,
            "attempt": 1,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "headBranch": "main",
            "headSha": head,
        }
        self.assertEqual(
            self._run_jq_contract(jq_filter, valid, head=head).returncode,
            0,
        )
        for field, replacement in (
            ("databaseId", 999),
            ("attempt", 2),
            ("headSha", "0" * 40),
        ):
            with self.subTest(field=field):
                invalid = {**valid, field: replacement}
                self.assertNotEqual(
                    self._run_jq_contract(jq_filter, invalid, head=head).returncode,
                    0,
                )


if __name__ == "__main__":
    unittest.main()
