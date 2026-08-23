"""Security and wiring guards for the isolated server archive lane."""

from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "server-archive.yml"
SUBMISSION = ROOT / ".github" / "workflows" / "submission.yml"


class ServerArchiveWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.submission = SUBMISSION.read_text(encoding="utf-8")

    def test_is_reusable_and_selected_only_for_server_dispatch(self) -> None:
        self.assertIn("  workflow_call:", self.text)
        self.assertNotIn("  workflow_dispatch:", self.text)
        caller = self.submission.split("\n  archive_server:", 1)[1].split(
            "\n  archive:", 1
        )[0]
        self.assertIn("github.event_name == 'workflow_dispatch'", caller)
        self.assertIn("uses: ./.github/workflows/server-archive.yml", caller)
        self.assertIn("id-token: write", caller)
        self.assertIn("callback_environment: ${{ inputs.callback_environment }}", caller)

    def test_environment_and_dispatch_ref_are_fail_closed(self) -> None:
        self.assertIn("environment: archive-${{ inputs.callback_environment }}", self.text)
        self.assertIn('test "$GITHUB_SHA" = "$EXPECTED_WORKFLOW_COMMIT"', self.text)
        self.assertIn(
            'test "$GITHUB_REF" = "refs/tags/lean-eval-dispatch/$EXPECTED_WORKFLOW_COMMIT"',
            self.text,
        )
        self.assertIn("case \"$CALLBACK_ENVIRONMENT\" in staging|production)", self.text)

    def test_uses_one_submission_kms_envelope_not_shared_recipients(self) -> None:
        self.assertIn("python scripts/archive_envelope.py", self.text)
        self.assertIn("--adapter-executable scripts/aws_key_adapter.py", self.text)
        self.assertIn("python scripts/archive_submission.py prepare-envelope-sidecar", self.text)
        self.assertIn("alias/lean-eval-archive-identities-${{ inputs.callback_environment }}", self.text)
        self.assertIn("vars.AWS_WRAP_ROLE_ARN", self.text)
        self.assertNotIn(".audit/recipients.txt", self.text)
        self.assertNotIn("archive_submission.py encrypt", self.text)

    def test_plaintext_and_authority_do_not_cross_job_boundaries(self) -> None:
        self.assertNotIn("submission-source", self.text)
        self.assertNotIn("submission-audit-ciphertext", self.text)
        self.assertIn("rm -rf /tmp/archive-fetch /tmp/archive-envelope", self.text)
        upload = self.text.split("name: Upload verified archive locator", 1)[1]
        self.assertIn("path: /tmp/archive-locator.json", upload)
        self.assertIn("path: /tmp/archive-completion.json", upload)
        for forbidden in ("source.tar.gz", "source.tar.gz.age", "identity.age"):
            self.assertNotIn(forbidden, upload)

    def test_aws_authority_is_dropped_before_github_write_authority(self) -> None:
        encrypt = self.text.split(
            "name: Encrypt with one fresh identity and wrap only that identity", 1
        )[1]
        self.assertLess(encrypt.index("trap clear_aws EXIT"), encrypt.index("archive_envelope.py"))
        self.assertLess(encrypt.index("archive_envelope.py"), encrypt.index("clear_aws\n"))
        archiver = self.text.split(
            "name: Mint audit-archive writer token after dropping AWS authority", 1
        )[1]
        self.assertIn("AWS_ACCESS_KEY_ID: ''", archiver)
        self.assertIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN: ''", archiver)
        self.assertEqual(self.text.count("ARCHIVER_TOKEN:"), 1)

    def test_actions_and_dependencies_are_immutable(self) -> None:
        actions = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", self.text)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in actions))
        self.assertIn("--require-hashes", self.text)
        self.assertIn("age-v1.3.1-linux-amd64.tar.gz", self.text)
        self.assertIn(
            "bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377",
            self.text,
        )

    def test_issue_lane_cannot_assume_the_kms_role(self) -> None:
        issue = self.submission.split("\n  archive_issue:", 1)[1].split(
            "\n  archive_server:", 1
        )[0]
        self.assertIn("github.event_name == 'issues'", issue)
        self.assertNotIn("id-token: write", issue)
        self.assertNotIn("AWS_WRAP_ROLE_ARN", issue)
        self.assertNotIn("archive_envelope.py", issue)


if __name__ == "__main__":
    unittest.main()
