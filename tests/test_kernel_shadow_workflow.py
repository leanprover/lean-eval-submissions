from __future__ import annotations

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "kernel-shadow-smoke.yml"
FIXTURE = ROOT / "tests" / "fixtures" / "kernel-shadow-smoke-v1.json"


class KernelShadowWorkflowTests(unittest.TestCase):
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
        for credential in (
            "GITHUB_STATE_TOKEN",
            "LEADERBOARD_WRITE_TOKEN",
            "ARCHIVER_TOKEN",
            "CLOUDFLARE_API_TOKEN",
        ):
            self.assertNotIn(credential, self.text)

    def test_every_identity_is_exact_and_matches_fixture(self) -> None:
        component_values = [
            self.fixture["source"]["repository"],
            self.fixture["source"]["commit"],
            self.fixture["benchmark"]["repository"],
            self.fixture["benchmark"]["commit"],
            self.fixture["benchmark"]["toolchain"],
            self.fixture["benchmark"]["mathlib_commit"],
            self.fixture["exporter"]["commit"],
            self.fixture["comparator"]["commit"],
            self.fixture["candidate"]["repository"],
            self.fixture["candidate"]["commit"],
            self.fixture["candidate"]["arena"]["commit"],
            self.fixture["candidate"]["arena"]["declaration_path"],
        ]
        for value in component_values:
            self.assertIn(value, self.text)
        self.assertIn('repository.get("private") is not False', self.text)
        self.assertIn('test "$(git -C source rev-parse HEAD)" = "$SOURCE_COMMIT"', self.text)
        self.assertIn('test "$(git -C benchmark rev-parse HEAD)" = "$BENCHMARK_COMMIT"', self.text)
        self.assertIn(
            'test "$(git -C /tmp/kernel-shadow-workspace/.lake/packages/mathlib rev-parse HEAD)" = "$MATHLIB_COMMIT"',
            self.text,
        )

    def test_actions_and_source_builds_are_commit_pinned(self) -> None:
        actions = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", self.text)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in actions))
        self.assertIn("go install github.com/zouuup/landrun/cmd/landrun@5ed4a3db3a4ad930d577215c6b9abaa19df7f99f", self.text)
        self.assertIn('git -C .ci/lean4export checkout "$EXPORTER_COMMIT"', self.text)
        self.assertIn('git -C .ci/comparator checkout "$COMPARATOR_COMMIT"', self.text)
        self.assertIn('git -C .ci/mathgraph checkout "$CANDIDATE_COMMIT"', self.text)
        self.assertIn("cargo build --locked --release", self.text)
        self.assertIn("rustup toolchain install 1.89.0", self.text)
        self.assertIn("go-version: '1.25.12'", self.text)
        self.assertIn("runs-on: ubuntu-24.04", self.text)

    def test_untrusted_source_is_overlaid_only_after_credentials_are_removed(self) -> None:
        strip = self.text.index("name: Remove checkout credentials and nondependency Git metadata")
        probes = self.text.index("name: Prove the untrusted sandbox and environment boundary")
        prepare = self.text.index("name: Prepare reviewed public submission without elaborating it")
        execute = self.text.index("name: Run candidate in non-authoritative shadow mode")
        self.assertLess(strip, probes)
        self.assertLess(probes, prepare)
        self.assertLess(prepare, execute)
        self.assertIn("sandbox_engaged_probe.py --require-tools", self.text)
        self.assertIn("env_dump_probe.py --require-tools", self.text)
        self.assertIn("prepare-workspace", self.text)
        self.assertNotIn("lake build Submission", self.text)
        self.assertIn("auto-config: false", self.text)
        self.assertIn("build: false", self.text)
        self.assertIn("use-mathlib-cache: false", self.text)
        self.assertIn("/tmp/kernel-shadow-workspace/.lake/packages/mathlib", self.text)
        self.assertNotIn("ln -s", self.text)
        self.assertNotIn("--shared-packages", self.text)
        self.assertIn("mathgraph-noda", (ROOT / "scripts" / "kernel_shadow_smoke.py").read_text())

    def test_shadow_cannot_change_acceptance_or_write_state(self) -> None:
        self.assertIn("continue-on-error: true", self.text)
        self.assertIn("Require the compatibility smoke to accept", self.text)
        self.assertNotIn("record-result", self.text)
        self.assertNotIn("append-event", self.text)
        self.assertNotIn("workflow_dispatcher", self.text)
        self.assertNotIn("migrate-results", self.text)
        self.assertNotIn("lean-eval-state", self.text)

    def test_artifact_is_only_strict_source_free_evidence(self) -> None:
        artifact = self.text.split("name: kernel-shadow-smoke-evidence", 1)[1]
        self.assertIn("/tmp/kernel-shadow-evidence.json", artifact)
        for forbidden in (
            "source/",
            "Submission.lean",
            "config.json",
            "kernel-shadow-workspace",
            ".ci/",
        ):
            self.assertNotIn(forbidden, artifact)


if __name__ == "__main__":
    unittest.main()
