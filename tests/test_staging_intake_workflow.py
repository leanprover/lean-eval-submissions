import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "set-staging-intake.yml"


class StagingIntakeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_is_manual_and_staging_only(self) -> None:
        self.assertIn("workflow_dispatch", self.text)
        jobs = self.text.split("\njobs:\n", 1)[1]
        self.assertEqual(
            re.findall(r"^  ([A-Za-z0-9_-]+):$", jobs, re.MULTILINE),
            ["set-staging-intake"],
        )
        self.assertIn("    environment: cloudflare-staging", jobs)
        self.assertNotIn("\n    if:", jobs)
        self.assertNotIn("cloudflare-production", self.text)

    def test_requires_exact_dispatch_commit_and_immutable_tag(self) -> None:
        self.assertIn(
            'if [ "$GITHUB_REF" != "refs/tags/lean-eval-dispatch/$EXPECTED_COMMIT" ]',
            self.text,
        )
        self.assertIn('"$EXPECTED_COMMIT" != "$GITHUB_SHA"', self.text)
        self.assertIn("refs/tags/lean-eval-dispatch/$EXPECTED_COMMIT", self.text)

    def test_state_is_closed_and_health_checked(self) -> None:
        self.assertIn(
            "        options:\n          - disabled\n          - enabled",
            self.text,
        )
        self.assertIn('INTAKE_ENABLED:$intake_enabled', self.text)
        self.assertIn('INTAKE_ENABLEMENT_MODE:$intake_mode', self.text)
        self.assertIn('body["environment"] == "staging"', self.text)
        self.assertIn('body["deployed_commit"]', self.text)
        self.assertIn('body["intake_enabled"]', self.text)
        self.assertIn('body["intake_configured_enabled"]', self.text)
        self.assertIn('body["intake_effective_enabled"]', self.text)
        self.assertIn('body["intake_lease_expires_at"] is None', self.text)

    def test_ref_mistakes_fail_and_stale_tags_cannot_roll_staging_back(self) -> None:
        self.assertNotIn("github.ref_type == 'tag'", self.text)
        self.assertIn("workflow must run from the exact immutable dispatch tag", self.text)
        predeploy = self.text.split(
            "      - name: Verify exact reviewed deployment", 1
        )[1].split("\n      - run: npm ci", 1)[0]
        self.assertIn(
            "https://lean-eval-submission-server-staging.lean-eval.workers.dev/healthz",
            predeploy,
        )
        self.assertIn('body["service"] == "lean-eval-submission"', predeploy)
        self.assertIn(
            'body["deployed_commit"] == os.environ["EXPECTED_COMMIT"]',
            predeploy,
        )
        self.assertIn("for attempt in $(seq 1 5); do", predeploy)
        self.assertIn(
            "selected dispatch tag is not the exact live staging deployment",
            predeploy,
        )


if __name__ == "__main__":
    unittest.main()
