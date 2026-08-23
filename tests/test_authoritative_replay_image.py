from __future__ import annotations

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).parents[1]
DOCKERFILE = ROOT / "Dockerfile.replay-authoritative"
WORKFLOW = ROOT / ".github" / "workflows" / "authoritative-replay-image.yml"
PROFILE = ROOT / "server" / "replay-image" / "replay-profile-lock-v433.json"
RUNNER = ROOT / "server" / "replay-image" / "replay-authoritative"


class AuthoritativeReplayImageTests(unittest.TestCase):
    def test_base_images_and_sources_are_immutable(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
        self.assertEqual(len(from_lines), 4)
        for line in from_lines:
            self.assertRegex(line, r"@sha256:[0-9a-f]{64}(?: AS [a-z-]+)?$")
        for commit in (
            "5ed4a3db3a4ad930d577215c6b9abaa19df7f99f",
            "68d5ca9db226849b41a6fff59d796ff19d0a8840",
            "71b52ec29e06d4b7d882726553b1ceb99a2499e0",
            "15f6055e299ad5b89345e533cc2192f4cc00f659",
            "b91d4757aa0d7776c02540c9089df54fa0d0658a",
        ):
            self.assertIn(commit, dockerfile)

    def test_profile_lock_has_no_self_referential_image_digest(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        self.assertNotIn("vm_image_digest", profile)
        self.assertNotIn("execution_profile_digest", profile)
        self.assertEqual(profile["toolchain"], "leanprover/lean4:v4.33.0")
        self.assertEqual(
            profile["measurement_command"], ["/opt/lean-eval/replay-measure"]
        )
        self.assertEqual(
            set(profile["components"]),
            {"comparator", "landrun", "lean4export", "nanoda"},
        )

    def test_image_is_build_gated_and_pinned_but_replay_is_disabled(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        replay_config = (ROOT / "server" / "wrangler.replay.jsonc").read_text(
            encoding="utf-8"
        )
        self.assertIn("21474836480", workflow)
        self.assertIn("--network none", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertEqual(
            replay_config.count(
                '"image": "registry.cloudflare.com/'
                'a46b90978a1c29cc4795f30677e7e4b8/lean-eval-authoritative:'
                'fdcabb95085edccd70c81dc079c27bcaf20a4b16"'
            ),
            2,
        )
        self.assertEqual(
            replay_config.count(
                '"REVIEWED_VM_IMAGE_DIGEST": '
                '"sha256:53d1964edc01f736ae66d7faa715d5b1fb67c96dcc167b4c5012282d8c14c807"'
            ),
            2,
        )
        self.assertEqual(replay_config.count('"REPLAY_ENABLED": "false"'), 2)

    def test_runner_uses_the_baked_elan_home(self) -> None:
        runner = RUNNER.read_text(encoding="utf-8")
        self.assertIn("/opt/lean-eval/home/.elan/bin", runner)
        self.assertNotIn("/root/.elan/bin", runner)
        self.assertRegex(runner, r'"--authoritative-checker",\s*"nanoda"')

    def test_image_contains_every_fixed_replay_command(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        for command in (
            "replay-authoritative",
            "replay-staging-acceptance",
            "replay-archive-acceptance",
            "replay-measure",
        ):
            with self.subTest(command=command):
                self.assertIn(
                    f"COPY server/replay-image/{command} /opt/lean-eval/{command}",
                    dockerfile,
                )
                self.assertIn(f"/opt/lean-eval/{command}", dockerfile)

    def test_workflow_actions_are_commit_pinned(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        actions = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", workflow)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in actions))


if __name__ == "__main__":
    unittest.main()
