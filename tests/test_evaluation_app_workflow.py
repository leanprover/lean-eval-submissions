import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "verify-evaluation-app.yml"
FIXTURE = "leanprover/lean-eval-state-staging"


class EvaluationAppWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_is_protected_staging_only_and_read_only(self) -> None:
        self.assertIn(
            "\npermissions:\n  contents: read\n\nconcurrency:",
            self.text,
        )
        verify_job = self.text.split("  verify:", 1)[1]
        self.assertRegex(
            verify_job,
            re.compile(r"^    environment: cloudflare-staging$", re.MULTILINE),
        )
        self.assertNotIn("cloudflare-production", self.text)
        self.assertNotIn("actions/checkout", self.text)
        self.assertNotIn("GITHUB_STATE_TOKEN", self.text)

    def test_uses_the_same_evaluation_app_credentials(self) -> None:
        self.assertIn("secrets.LEAN_EVAL_BOT_CLIENT_ID", self.text)
        self.assertIn("secrets.LEAN_EVAL_BOT_PRIVATE_KEY", self.text)
        self.assertIn("owner: leanprover", self.text)
        self.assertIn("repositories: lean-eval-state-staging", self.text)

    def test_checks_the_fixed_private_fixture_and_exact_inputs(self) -> None:
        self.assertGreaterEqual(self.text.count(FIXTURE), 3)
        self.assertIn("expected_branch:", self.text)
        self.assertIn("expected_commit:", self.text)
        self.assertIn("^[0-9a-f]{40}$", self.text)
        self.assertIn('branch["ref"] == f"refs/heads/', self.text)
        self.assertIn('branch["object"] == {', self.text)
        self.assertIn('commit["sha"] == os.environ["EXPECTED_COMMIT"]', self.text)
        self.assertIn('metadata["private"] is True', self.text)
        self.assertIn("curl --fail-with-body", self.text)


if __name__ == "__main__":
    unittest.main()
