from __future__ import annotations

import json
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "public-replay-smoke.yml"
FIXTURE = ROOT / "tests" / "fixtures" / "public-replay-smoke-v1.json"


class PublicReplayWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_is_manual_staging_only_and_has_no_write_authority(self) -> None:
        self.assertIn("  workflow_dispatch:", self.text)
        self.assertNotIn("schedule:", self.text)
        self.assertNotIn("push:", self.text)
        self.assertIn("environment: replay-staging", self.text)
        self.assertNotIn("replay-production", self.text)
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read$")
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("GITHUB_STATE_TOKEN", self.text)
        self.assertNotIn("LEADERBOARD_WRITE_TOKEN", self.text)
        self.assertNotIn("ARCHIVER_TOKEN", self.text)

    def test_all_historical_identities_are_exact_and_match_fixture(self) -> None:
        identities = (
            self.fixture["source"]["repository"],
            self.fixture["source"]["commit"],
            self.fixture["benchmark"]["repository"],
            self.fixture["benchmark"]["commit"],
            self.fixture["benchmark"]["toolchain"],
            self.fixture["evaluator"]["repository"],
            self.fixture["evaluator"]["commit"],
        )
        for identity in identities:
            self.assertIn(identity, self.text)
        self.assertIn("Verify source repository remains anonymously public", self.text)
        self.assertIn('value.get("private") is not False', self.text)
        self.assertIn('test "$(git -C source rev-parse HEAD)" = "$SOURCE_COMMIT"', self.text)
        self.assertIn('test "$(git -C lean-eval rev-parse HEAD)" = "$BENCHMARK_COMMIT"', self.text)
        self.assertIn('test "$(git -C evaluator rev-parse HEAD)" = "$EVALUATOR_COMMIT"', self.text)

    def test_untrusted_execution_boundary_is_fail_closed(self) -> None:
        self.assertEqual(self.text.count("persist-credentials: false"), 4)
        strip = self.text.index("name: Remove every repository credential and Git metadata")
        sandbox_probe = self.text.index(
            "name: Prove the untrusted sandbox and environment boundary"
        )
        evaluate = self.text.index("python evaluator/scripts/evaluate_submission.py")
        self.assertLess(strip, sandbox_probe)
        self.assertLess(sandbox_probe, evaluate)
        self.assertIn("sandbox_engaged_probe.py --require-tools", self.text)
        self.assertIn("env_dump_probe.py --require-tools", self.text)
        self.assertIn(
            "find . -type d -name .git -prune -exec rm -rf '{}' +", self.text
        )
        self.assertIn("find . -type d -name .git -print -quit", self.text)
        self.assertNotIn("submission-source", self.text)

    def test_component_and_action_dependencies_are_commit_pinned(self) -> None:
        actions = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", self.text)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in actions))
        for commit in (
            "5ed4a3db3a4ad930d577215c6b9abaa19df7f99f",
            "4e7915201d3f9f04470d9eae002fa695f7cdc589",
            "71b52ec29e06d4b7d882726553b1ceb99a2499e0",
            "68d5ca9db226849b41a6fff59d796ff19d0a8840",
        ):
            self.assertIn(commit, self.text)
        self.assertIn("go-version: '1.25.12'", self.text)
        self.assertIn("rustup toolchain install 1.89.0", self.text)
        self.assertIn("runs-on: ubuntu-24.04", self.text)

    def test_artifact_contains_only_source_free_json(self) -> None:
        artifact = self.text.split("name: public-replay-smoke-evidence", 1)[1]
        self.assertIn("evidence.json", artifact)
        self.assertIn("results.json", artifact)
        self.assertIn("summary.json", artifact)
        self.assertNotIn("source/", artifact)
        self.assertNotIn("Submission.lean", artifact)


if __name__ == "__main__":
    unittest.main()
