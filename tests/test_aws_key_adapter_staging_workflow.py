from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "aws-key-adapter-staging-smoke.yml"


class AwsKeyAdapterStagingWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_is_manual_staging_only_and_exact_tag_bound(self) -> None:
        self.assertIn("  workflow_dispatch:", self.text)
        self.assertNotIn("schedule:", self.text)
        self.assertNotIn("push:", self.text)
        self.assertIn("refs/tags/lean-eval-dispatch/${{ github.sha }}", self.text)
        self.assertIn("environment: archive-staging", self.text)
        self.assertIn("environment: replay-staging", self.text)
        self.assertNotIn("archive-production", self.text)
        self.assertNotIn("replay-production", self.text)

    def test_authority_is_oidc_and_environment_specific(self) -> None:
        self.assertEqual(self.text.count("id-token: write"), 2)
        self.assertIn("vars.AWS_WRAP_ROLE_ARN", self.text)
        self.assertIn("vars.AWS_REPLAY_UNWRAP_ROLE_ARN", self.text)
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("AWS_ACCESS_KEY_ID: ${{", self.text)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY: ${{", self.text)
        self.assertGreaterEqual(
            self.text.count("unset ACTIONS_ID_TOKEN_REQUEST_TOKEN ACTIONS_ID_TOKEN_REQUEST_URL"),
            7,
        )
        self.assertIn("alias/lean-eval-archive-identities-staging", self.text)
        self.assertEqual(self.text.count("aws lambda invoke"), 2)

    def test_dependencies_and_actions_are_immutable(self) -> None:
        actions = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", self.text)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in actions))
        self.assertIn("boto3==1.43.77", (ROOT / "infrastructure/aws-key-adapter/requirements-workflow.txt").read_text())
        self.assertIn("--require-hashes", self.text)
        self.assertIn("age-v1.3.1-linux-amd64.tar.gz", self.text)
        self.assertIn("bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377", self.text)

    def test_no_plaintext_crosses_artifact_or_persistence_boundary(self) -> None:
        upload = self.text.split("name: Upload only ciphertext, envelope, and marker digest", 1)[1]
        upload = upload.split("\n\n  unwrap:", 1)[0]
        self.assertIn("/tmp/aws-smoke-artifact", upload)
        for forbidden in ("identity.age", "decrypted-source", "aws-smoke-source.tar.gz"):
            self.assertNotIn(forbidden, upload)
        self.assertIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN: ''", upload)
        unwrap = self.text.split("\n  unwrap:", 1)[1]
        self.assertNotIn("upload-artifact", unwrap)
        self.assertIn("rm -f", unwrap)
        self.assertIn("validate-reuse-failure", unwrap)

    def test_aws_credentials_are_dropped_before_non_aws_processing(self) -> None:
        wrap = self.text.split("name: Wrap one fresh identity and drop AWS authority", 1)[1]
        self.assertLess(wrap.index("trap clear_aws EXIT"), wrap.index("archive_envelope.py"))
        archive = wrap.index("archive_envelope.py")
        clear = wrap.index("\n          clear_aws\n", archive)
        validate = wrap.index("validate-artifact", archive)
        self.assertLess(archive, clear)
        self.assertLess(clear, validate)
        unwrap = self.text.split("name: Consume once, reject reuse, then decrypt without AWS authority", 1)[1]
        self.assertLess(unwrap.index("clear_aws\n"), unwrap.index("age --decrypt"))
        self.assertIn("env -u AWS_ACCESS_KEY_ID", unwrap)

    def test_smoke_cannot_write_project_state(self) -> None:
        for forbidden in (
            "GITHUB_STATE_TOKEN",
            "ARCHIVER_TOKEN",
            "LEADERBOARD_WRITE_TOKEN",
            "append-event",
            "record-result",
            "workflow_dispatcher",
            "lean-eval-releases",
        ):
            self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
