import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "set-staging-intake.yml"


class StagingIntakeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.text)

    def test_is_manual_and_staging_only(self) -> None:
        self.assertIn("workflow_dispatch", self.text)
        self.assertEqual(set(self.workflow["jobs"]), {"set-staging-intake"})
        job = self.workflow["jobs"]["set-staging-intake"]
        self.assertEqual(job["environment"], "cloudflare-staging")
        self.assertNotIn("cloudflare-production", self.text)

    def test_requires_exact_main_commit_and_immutable_tag(self) -> None:
        self.assertIn("github.ref == 'refs/heads/main'", self.text)
        self.assertIn('"$EXPECTED_COMMIT" != "$GITHUB_SHA"', self.text)
        self.assertIn("refs/tags/lean-eval-dispatch/$EXPECTED_COMMIT", self.text)

    def test_state_is_closed_and_health_checked(self) -> None:
        inputs = self.workflow[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["state"]["options"], ["disabled", "enabled"])
        self.assertIn('INTAKE_ENABLED:$intake_enabled', self.text)
        self.assertIn('body["environment"] == "staging"', self.text)
        self.assertIn('body["deployed_commit"]', self.text)
        self.assertIn('body["intake_enabled"]', self.text)


if __name__ == "__main__":
    unittest.main()
