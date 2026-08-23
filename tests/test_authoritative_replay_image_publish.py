from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-authoritative-replay-image.yml"


class AuthoritativeReplayImagePublishTests(unittest.TestCase):
    def test_publication_is_manual_protected_and_source_exact(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("environment: cloudflare-staging", workflow)
        self.assertIn('test "$GITHUB_REF" = refs/heads/main', workflow)
        self.assertIn("refs/heads/main | cut -f1", workflow)
        self.assertIn("cancel-in-progress: false", workflow)

    def test_registry_push_is_digest_resolved_without_account_evidence(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('wrangler containers push "$IMAGE"', workflow)
        push = workflow.index('wrangler containers push "$IMAGE"')
        self.assertLess(workflow.index('current_main=$(git ls-remote', push - 1000), push)
        self.assertIn("refusing stale publication", workflow)
        self.assertIn("refusing to overwrite it", workflow)
        self.assertIn("ocker-[Cc]ontent-[Dd]igest", workflow)
        self.assertIn('"registry_manifest_digest"', workflow)
        self.assertNotIn('"account_id"', workflow)
        self.assertIn("registry-credentials.json", workflow)
        self.assertIn('registry_username=$(jq -er .username', workflow)
        self.assertIn('--user "$registry_username:$registry_password"', workflow)
        self.assertEqual(workflow.count("::add-mask::"), 2)
        self.assertIn("umask 077", workflow)
        self.assertIn("timeout 60s npx", workflow)
        self.assertIn("--max-time 30", workflow)

    def test_publish_repeats_the_image_content_and_size_gates(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("21474836480", workflow)
        self.assertIn("--network none", workflow)
        self.assertIn("lake-manifest.json | wc -l", workflow)
        self.assertIn("nanoda_bin", workflow)
        self.assertIn("workspace_manifest_count", workflow)

    def test_actions_are_commit_pinned_and_permissions_are_read_only(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        actions = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", workflow)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in actions))
        self.assertIn("permissions:\n  contents: read", workflow)


if __name__ == "__main__":
    unittest.main()
