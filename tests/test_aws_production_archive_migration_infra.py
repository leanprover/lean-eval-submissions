import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT / "scripts" / "operator_production_archive_migration_infra.sh"
).read_text()
DOC = (
    ROOT / "docs" / "aws-production-archive-migration-infrastructure.md"
).read_text()


class ProductionArchiveMigrationInfrastructureTests(unittest.TestCase):
    def test_source_and_capability_are_pinned(self) -> None:
        self.assertIn("SOURCE_COMMIT=c1013bee0b5b2f57956501e0258d27dc30413d2b", SCRIPT)
        self.assertEqual(SCRIPT.count("EXPECTED_"), 6)
        self.assertNotIn("sam build", SCRIPT)
        self.assertIn("date_time=(1980, 1, 1, 0, 0, 0)", SCRIPT)
        self.assertNotIn(".[0].StackStatus", SCRIPT)
        self.assertIn("archive-migration-production", SCRIPT)
        self.assertIn("lean-eval-archive-migration-wrap-production", SCRIPT)

    def test_change_set_is_closed_to_exact_sam_delta(self) -> None:
        for logical_id in (
            "MigrationWrapRole",
            "UnwrapFunctionRole",
            "UnwrapFunction",
            "UnwrapFunctionAliaslive",
        ):
            self.assertIn(logical_id, SCRIPT)
        self.assertIn("^UnwrapFunctionVersion[0-9a-f]{10}$", SCRIPT)
        self.assertIn("(.Changes | length) >= 5", SCRIPT)
        self.assertIn("(.Changes | length) <= 6", SCRIPT)
        self.assertIn("else false end", SCRIPT)
        self.assertIn("change_set_owned=true", SCRIPT)
        self.assertIn("execution_attempted=true", SCRIPT)
        self.assertIn("change-set-failure.json", SCRIPT)
        self.assertIn("jq '{Status, ExecutionStatus, StatusReason}'", SCRIPT)
        self.assertLess(
            SCRIPT.index("change-set-failure.json"),
            SCRIPT.index("echo step=execute-production-change-set"),
        )

    def test_preserves_existing_authority_and_v1_contract(self) -> None:
        self.assertEqual(SCRIPT.count("compare_role \"$WRAP_ROLE\""), 1)
        self.assertEqual(SCRIPT.count("compare_role \"$REPLAY_ROLE\""), 1)
        self.assertEqual(SCRIPT.count("compare_role \"$RELEASE_ROLE\""), 1)
        self.assertIn("lean-eval-archive-key-v1", SCRIPT)
        self.assertIn("lean-eval-archive-key-v2", SCRIPT)
        self.assertIn("$staging_updated_before", SCRIPT)
        self.assertIn("MigrationWrapRoleArn", SCRIPT)

    def test_migrates_the_exact_live_legacy_parameter_names(self) -> None:
        preflight, update = SCRIPT.split("echo step=create-and-inspect-change-set", 1)
        create, post = update.split("echo step=post-update-verification", 1)
        self.assertIn('ReleaseGitHubRepository: $releases', preflight)
        self.assertIn('SubmissionGitHubRepository: $submissions', preflight)
        self.assertIn(
            'ParameterKey=SubmissionGitHubSubjectPrefix,ParameterValue="$SUBMISSION_PREFIX"',
            create,
        )
        self.assertIn(
            'ParameterKey=ReleaseGitHubSubjectPrefix,ParameterValue="$RELEASE_PREFIX"',
            create,
        )
        self.assertNotIn(
            "ParameterKey=SubmissionGitHubSubjectPrefix,UsePreviousValue=true", create
        )
        self.assertNotIn(
            "ParameterKey=ReleaseGitHubSubjectPrefix,UsePreviousValue=true", create
        )
        self.assertIn('ReleaseGitHubSubjectPrefix: $releases', post)
        self.assertIn('SubmissionGitHubSubjectPrefix: $submissions', post)

    def test_live_resource_status_filter_keeps_the_root_object_in_scope(self) -> None:
        self.assertIn(
            '([.StackResources[] | select(.ResourceStatus | endswith("_COMPLETE") | not)] | length) == 0 and',
            SCRIPT,
        )
        self.assertNotIn(
            '[.StackResources[] | select(.ResourceStatus | endswith("_COMPLETE") | not)] | length == 0 and',
            SCRIPT,
        )

    def test_never_installs_secret_or_runs_workloads(self) -> None:
        self.assertNotIn("gh secret set", SCRIPT)
        self.assertNotIn("gh workflow run", SCRIPT)
        self.assertNotIn("aws lambda invoke", SCRIPT)
        self.assertNotIn("aws kms encrypt", SCRIPT)
        self.assertNotIn("git push", SCRIPT)
        self.assertEqual(SCRIPT.count("LEGACY_ARCHIVE_IDENTITY"), 2)
        self.assertIn("does not authorize migration", DOC)

    def test_connects_only_the_nonsecret_migration_selector_last(self) -> None:
        connect = SCRIPT.index("echo step=connect-migration-role-selector")
        execute = SCRIPT.index("echo step=execute-production-change-set")
        verify = SCRIPT.index("echo step=post-update-verification")
        self.assertLess(execute, verify)
        self.assertLess(verify, connect)
        self.assertEqual(SCRIPT.count("gh variable set"), 1)
        self.assertIn("--body \"$MIGRATION_ROLE_ARN\"", SCRIPT)
        self.assertGreater(SCRIPT.index("complete=true"), connect)


if __name__ == "__main__":
    unittest.main()
