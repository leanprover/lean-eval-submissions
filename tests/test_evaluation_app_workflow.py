import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "verify-evaluation-app.yml"
FIXTURE = "kim-em/lean-eval-intake-fixture"
COMMIT = "ae38f4d3e4ad2991212135435f54e6640bcc89e7"


class EvaluationAppWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.text)

    def test_is_protected_staging_only_and_read_only(self) -> None:
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        job = self.workflow["jobs"]["verify"]
        self.assertEqual(job["environment"], "cloudflare-staging")
        self.assertNotIn("cloudflare-production", self.text)
        self.assertNotIn("actions/checkout", self.text)
        self.assertNotIn("GITHUB_STATE_TOKEN", self.text)

    def test_uses_the_same_evaluation_app_credentials(self) -> None:
        self.assertIn("secrets.LEAN_EVAL_BOT_CLIENT_ID", self.text)
        self.assertIn("secrets.LEAN_EVAL_BOT_PRIVATE_KEY", self.text)
        self.assertIn("owner: kim-em", self.text)
        self.assertIn("repositories: lean-eval-intake-fixture", self.text)

    def test_checks_the_fixed_private_fixture_and_commit(self) -> None:
        self.assertGreaterEqual(self.text.count(FIXTURE), 3)
        self.assertEqual(self.text.count(COMMIT), 2)
        self.assertIn('metadata["private"] is True', self.text)
        self.assertIn("curl --fail-with-body", self.text)


if __name__ == "__main__":
    unittest.main()
