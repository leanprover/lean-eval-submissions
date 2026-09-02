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
        self.assertIn("SOURCE_COMMIT=5397ca582e3d38a88ffda928a48a479a6e9afb6d", SCRIPT)
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
            SCRIPT.index("logical_resource: .LogicalResourceId"),
            SCRIPT.index('  .Status == "CREATE_COMPLETE"'),
        )
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

    def test_live_stack_preflight_accepts_created_or_updated_stack(self) -> None:
        preflight, post = SCRIPT.split("echo step=post-update-verification", 1)
        self.assertIn(
            '(.Stacks[0].StackStatus == "CREATE_COMPLETE" or\n'
            '   .Stacks[0].StackStatus == "UPDATE_COMPLETE")',
            preflight,
        )
        self.assertNotIn('StackStatus == "CREATE_COMPLETE"', post)
        self.assertIn('.Stacks[0].StackStatus == "UPDATE_COMPLETE"', post)

    def test_never_installs_secret_or_runs_workloads(self) -> None:
        self.assertNotIn("gh secret set", SCRIPT)
        self.assertNotIn("gh workflow run", SCRIPT)
        self.assertNotIn("aws lambda invoke", SCRIPT)
        self.assertNotIn("aws kms encrypt", SCRIPT)
        self.assertNotIn("git push", SCRIPT)
        self.assertEqual(SCRIPT.count("LEGACY_ARCHIVE_IDENTITY"), 2)
        self.assertIn("does not authorize actual migration", DOC)

    def test_requires_and_preserves_prebound_migration_selector(self) -> None:
        verify_selector = SCRIPT.index("echo step=verify-migration-role-selector")
        execute = SCRIPT.index("echo step=execute-production-change-set")
        verify = SCRIPT.index("echo step=post-update-verification")
        self.assertLess(execute, verify)
        self.assertLess(verify, verify_selector)
        self.assertNotIn("gh variable set", SCRIPT)
        self.assertIn("environment-variables-before.json", SCRIPT)
        self.assertIn("environment-variables-after.json", SCRIPT)
        self.assertIn(
            'value: $migration}]\n\' "$ops/environment-variables-before.json"',
            SCRIPT,
        )
        self.assertIn(
            'cmp "$ops/environment-variables-before.json"', SCRIPT
        )
        self.assertGreater(SCRIPT.index("complete=true"), verify_selector)


if __name__ == "__main__":
    unittest.main()
