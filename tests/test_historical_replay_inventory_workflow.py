from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "historical-replay-inventory.yml"
).read_text(encoding="utf-8")


class HistoricalReplayInventoryWorkflowTests(unittest.TestCase):
    def test_is_manual_read_only_and_has_no_external_credentials(self) -> None:
        self.assertIn("  workflow_dispatch:", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)
        self.assertNotIn("\n  push:", WORKFLOW)
        self.assertRegex(WORKFLOW, r"(?m)^permissions:\n  contents: read$")
        self.assertNotIn("secrets.", WORKFLOW)
        self.assertNotIn("id-token: write", WORKFLOW)
        self.assertNotIn("contents: write", WORKFLOW)
        self.assertIn("persist-credentials: false", WORKFLOW)

    def test_requires_the_exact_protected_dispatch_tag_and_clean_checkout(self) -> None:
        self.assertIn(
            'test "$GITHUB_SHA" = "$EXPECTED_COMMIT"',
            WORKFLOW,
        )
        self.assertIn(
            'test "$GITHUB_REPOSITORY" = "leanprover/lean-eval-submissions"',
            WORKFLOW,
        )
        self.assertIn(
            'test "$GITHUB_REF" = "refs/tags/lean-eval-dispatch/$EXPECTED_COMMIT"',
            WORKFLOW,
        )
        self.assertIn(
            '"refs/tags/lean-eval-dispatch/$EXPECTED_COMMIT"',
            WORKFLOW,
        )
        self.assertIn("ref: ${{ inputs.expected_commit }}", WORKFLOW)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"', WORKFLOW)
        self.assertEqual(
            WORKFLOW.count(
                'test -z "$(git status --porcelain=v1 --untracked-files=all)"'
            ),
            2,
        )

    def test_recomputes_and_validates_every_publication_binding(self) -> None:
        self.assertEqual(
            WORKFLOW.count("python scripts/inventory_historical_replay.py"),
            2,
        )
        self.assertIn('cmp "$artifact" "$recomputed"', WORKFLOW)
        self.assertIn(
            'value["results_store_sha256"] == os.environ["EXPECTED_DIGEST"]',
            WORKFLOW,
        )
        self.assertIn(
            'str(value["result_count"]) == os.environ["EXPECTED_COUNT"]',
            WORKFLOW,
        )
        self.assertIn("identities == sorted(set(identities))", WORKFLOW)
        self.assertIn(
            'set(source) == {"kind", "visibility", "readiness"}',
            WORKFLOW,
        )
        self.assertIn("assert raw == canonical", WORKFLOW)

    def test_uploads_only_the_source_free_json_with_pinned_actions(self) -> None:
        upload = WORKFLOW.split("name: Upload only the source-free inventory", 1)[1]
        self.assertIn("historical-replay-inventory.json", upload)
        self.assertNotIn("results/", upload)
        actions = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", WORKFLOW)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in actions))


if __name__ == "__main__":
    unittest.main()
