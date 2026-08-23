from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "publish_replay_state_event"


class PublishReplayStateEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_writer_is_staging_only_non_force_and_parent_exact(self) -> None:
        self.assertIn('!= "$expected_head"', self.text)
        self.assertIn('!= staging', self.text)
        self.assertIn("lean-eval-state-staging.git", self.text)
        self.assertIn("HEAD:refs/heads/main", self.text)
        self.assertNotIn("--force", self.text)
        self.assertNotIn("+HEAD:", self.text)

    def test_writer_validates_and_stages_only_the_new_event(self) -> None:
        self.assertIn('scripts/state.py" --root "$state_root" append', self.text)
        self.assertIn('scripts/state.py" --root "$state_root" validate', self.text)
        self.assertIn("diff --cached --name-status", self.text)
        self.assertIn("expected_change=$(printf 'A\\t%s'", self.text)

    def test_writer_uses_isolated_pinned_ssh_material_and_scrubs_it(self) -> None:
        self.assertIn("GitHub's published Ed25519 host key", self.text)
        self.assertIn("StrictHostKeyChecking=yes", self.text)
        self.assertIn("IdentitiesOnly=yes", self.text)
        self.assertIn('shred --remove "$key_file"', self.text)
        self.assertIn('unset STATE_WRITE_KEY', self.text)

    def test_unknown_push_outcome_is_resolved_against_remote_head(self) -> None:
        self.assertIn("push --porcelain", self.text)
        self.assertIn("HEAD:refs/heads/main >&2", self.text)
        self.assertIn('git ls-remote "$remote" refs/heads/main', self.text)
        self.assertIn('if [ "$remote_head" != "$new_commit" ]', self.text)


if __name__ == "__main__":
    unittest.main()
