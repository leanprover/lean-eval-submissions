import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/bootstrap-audit-migration-promotion-contract.yml"
CALLER = ROOT / ".github/workflows/promote-archive-migration.yml"


class AuditPromotionBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_is_exactly_bound_to_protected_submissions_main(self) -> None:
        self.assertIn('test "$EVENT_REF" = refs/heads/main', self.workflow)
        self.assertIn('test "$EVENT_REF_PROTECTED" = true', self.workflow)
        self.assertIn('test "$EVENT_SHA" = "$EXPECTED_WORKFLOW_COMMIT"', self.workflow)
        self.assertIn('test "$CONFIRMATION" = promote-reviewed-audit-contract', self.workflow)

    def test_is_one_shot_and_exactly_bound_to_the_reviewed_audit_pr(self) -> None:
        self.assertEqual(
            self.workflow.count("77cdadf733b20b8fdb274f7f7be5d314dba850ad"), 5
        )
        self.assertEqual(
            self.workflow.count("f50c46574dd719486a01272e3eaeced396ac5ada"), 2
        )
        self.assertEqual(
            self.workflow.count("a6dd8ab53ac50ec047239820f969d913501f118a"), 3
        )

    def test_mints_only_the_existing_audit_archiver_app(self) -> None:
        self.assertIn("secrets.LEAN_EVAL_ARCHIVER_CLIENT_ID", self.workflow)
        self.assertIn("secrets.LEAN_EVAL_ARCHIVER_PRIVATE_KEY", self.workflow)
        self.assertIn("repositories: lean-eval-audit", self.workflow)
        self.assertNotIn("id-token: write", self.workflow)
        self.assertNotIn("AWS_", self.workflow)
        self.assertNotIn("LEGACY_ARCHIVE_IDENTITY", self.workflow)

    def test_uses_cas_and_deletes_only_after_verified_main(self) -> None:
        cas = self.workflow.index("--force-with-lease=refs/heads/main:$base")
        main_tree = self.workflow.index(
            'git -C audit rev-parse refs/remotes/origin/main^{tree}'
        )
        delete_step = self.workflow.index(
            "- name: Delete only the verified contract branch"
        )
        branch_delete = self.workflow.index('\":refs/heads/$branch\"')
        self.assertLess(cas, main_tree)
        self.assertLess(main_tree, delete_step)
        self.assertLess(delete_step, branch_delete)


class AuditPromotionCallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = CALLER.read_text(encoding="utf-8")

    def test_calls_only_the_exact_reviewed_private_contract(self) -> None:
        contract = "77cdadf733b20b8fdb274f7f7be5d314dba850ad"
        self.assertIn(
            "uses: leanprover/lean-eval-audit/.github/workflows/"
            f"promote-archive-migration.yml@{contract}",
            self.workflow,
        )
        self.assertIn(f"expected_contract_commit: {contract}", self.workflow)
        self.assertIn(
            "expected_caller_commit: ${{ inputs.expected_workflow_commit }}",
            self.workflow,
        )

    def test_passes_only_existing_archiver_app_credentials(self) -> None:
        self.assertIn("secrets.LEAN_EVAL_ARCHIVER_CLIENT_ID", self.workflow)
        self.assertIn("secrets.LEAN_EVAL_ARCHIVER_PRIVATE_KEY", self.workflow)
        self.assertNotIn("id-token: write", self.workflow)
        self.assertNotIn("AWS_", self.workflow)
        self.assertNotIn("LEGACY_ARCHIVE_IDENTITY", self.workflow)

    def test_exposes_every_review_binding_and_no_apply_switch(self) -> None:
        for name in (
            "source_audit_commit",
            "staged_migration_commit",
            "expected_staged_tree",
            "expected_patch_sha256",
            "expected_migration_count",
            "current_audit_head",
            "expected_result_tree",
            "expected_result_commit",
            "confirmation",
        ):
            self.assertIn(f"{name}:", self.workflow)
        self.assertNotIn("apply:", self.workflow)


if __name__ == "__main__":
    unittest.main()
