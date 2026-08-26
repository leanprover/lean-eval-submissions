from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "aws-production-wrap-preflight.yml"


class AwsProductionWrapPreflightWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_is_manual_one_shot_and_exact_tag_bound(self) -> None:
        self.assertIn("  workflow_dispatch:", self.text)
        self.assertNotIn("schedule:", self.text)
        self.assertNotIn("push:", self.text)
        self.assertIn("refs/tags/lean-eval-dispatch/${{ github.sha }}", self.text)
        self.assertIn("environment: archive-production", self.text)
        self.assertNotIn("archive-staging", self.text)
        self.assertNotIn("replay-production", self.text)

    def test_absent_role_is_inert_and_exact_role_is_required(self) -> None:
        guard = self.text.split(
            "name: Require the exact configured production Wrap role", 1
        )[1].split("# aws-actions/configure-aws-credentials", 1)[0]
        self.assertIn('if [ -z "$CONFIGURED_ROLE_ARN" ]', guard)
        self.assertIn("configured=false", guard)
        self.assertIn('test "$CONFIGURED_ROLE_ARN" = "$EXPECTED_ROLE_ARN"', guard)
        self.assertIn(
            "arn:aws:iam::161072922960:role/lean-eval-archive-wrap-production",
            guard,
        )
        self.assertEqual(
            self.text.count("if: steps.configuration.outputs.configured == 'true'"),
            2,
        )

    def test_authority_is_only_oidc_wrap_role_and_exact_key(self) -> None:
        self.assertEqual(self.text.count("id-token: write"), 1)
        self.assertIn("role-to-assume: ${{ vars.AWS_WRAP_ROLE_ARN }}", self.text)
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("AWS_ACCESS_KEY_ID: ${{", self.text)
        self.assertIn(
            "arn:aws:kms:us-east-1:161072922960:key/"
            "219904f9-4952-400f-b60a-6f027c4d070b",
            self.text,
        )
        actions = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", self.text)
        self.assertEqual(len(actions), 1)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in actions))

    def test_exact_contract_context_and_decrypt_denial_are_required(self) -> None:
        for field in (
            "contract=lean-eval-archive-key-v1",
            "submission_id=",
            "archive_ciphertext_sha256=",
            "data_key_id=$data_key_id",
            "age_recipient_sha256=",
        ):
            self.assertIn(field, self.text)
        self.assertEqual(self.text.count("aws kms encrypt"), 1)
        self.assertEqual(self.text.count("aws kms decrypt"), 1)
        self.assertIn("AccessDeniedException", self.text)
        self.assertIn('test ! -s "$decrypt_output"', self.text)
        self.assertIn('data_key_id="ak1_$(', self.text)
        self.assertIn("lean-eval-archive-key-v1\\0%s\\0%s", self.text)

    def test_preflight_has_no_write_or_artifact_surface(self) -> None:
        for forbidden in (
            "actions/checkout",
            "upload-artifact",
            "download-artifact",
            "GITHUB_STATE_TOKEN",
            "ARCHIVER_TOKEN",
            "LEADERBOARD_WRITE_TOKEN",
            "lean-eval-audit",
            "lean-eval-state",
            "submission.yml",
            "repository_dispatch",
            "workflow_call",
            "aws lambda",
            "aws dynamodb",
        ):
            self.assertNotIn(forbidden, self.text)
        self.assertIn('rm -rf "$scratch"', self.text)
        self.assertIn("unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN", self.text)


if __name__ == "__main__":
    unittest.main()
