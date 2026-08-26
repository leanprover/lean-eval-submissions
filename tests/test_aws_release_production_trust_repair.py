import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROCEDURE = ROOT / "docs" / "aws-release-production-trust-repair.md"
STAGING_PROCEDURE = ROOT / "docs" / "aws-release-staging-trust-repair.md"


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

    def test_staging_procedure_captures_current_protected_release_head(self) -> None:
        staging = STAGING_PROCEDURE.read_text()
        self.assertIn(
            'LEAN_EVAL_RELEASE_COMMIT="$(gh api \\\n'
            "  repos/leanprover/lean-eval-releases/commits/main --jq .sha)",
            staging,
        )
        self.assertNotIn(
            "90dadc872d624b8e6d171caf439313d185fc3e7f", staging
        )
        self.assertIn("test \"$(gh api \\", staging)
        self.assertIn('test "$LEAN_EVAL_RELEASE_BEFORE" =', staging)


if __name__ == "__main__":
    unittest.main()
