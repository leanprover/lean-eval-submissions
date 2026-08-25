from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "historical-replay-inventory.yml"
).read_text(encoding="utf-8")
DOCUMENTATION = (ROOT / "docs" / "historical-replay-inventory.md").read_text(
    encoding="utf-8"
)


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
        self.assertIn("if: inputs.confirm_contract_only == true", WORKFLOW)

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
        self.assertIn('"$tag_ref^{}"', WORKFLOW)
        self.assertIn(
            'if [ -z "$remote_commit" ]; then remote_commit="$tag_object"; fi',
            WORKFLOW,
        )
        self.assertIn('"$tag_ref" "$tag_ref^{}"', WORKFLOW)
        self.assertIn('test "${#tag_lines[@]}" -le 2', WORKFLOW)
        self.assertIn("ref: ${{ inputs.expected_commit }}", WORKFLOW)
        self.assertIn("fetch-depth: 0", WORKFLOW)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"', WORKFLOW)
        self.assertIn('"repos/$GITHUB_REPOSITORY/branches/main"', WORKFLOW)
        self.assertIn(
            'git merge-base --is-ancestor "$EXPECTED_COMMIT" origin/main',
            WORKFLOW,
        )
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
        self.assertIn("Draft202012Validator.check_schema(schema)", WORKFLOW)
        self.assertIn("FormatChecker()", WORKFLOW)
        self.assertIn(
            'path.stat().st_size <= int(os.environ["MAX_INVENTORY_BYTES"])',
            WORKFLOW,
        )

    def test_uploads_only_the_source_free_json_with_pinned_actions(self) -> None:
        upload = WORKFLOW.split("name: Upload only the source-free inventory", 1)[1]
        self.assertIn("historical-replay-inventory.json", upload)
        self.assertNotIn("results/", upload)
        self.assertIn("retention-days: 90", upload)
        actions = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", WORKFLOW)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in actions))

    def test_optional_delta_is_append_only_source_free_and_recomputed(self) -> None:
        self.assertIn("confirm_append_only_delta:", WORKFLOW)
        self.assertEqual(
            WORKFLOW.count(
                "python scripts/reconcile_historical_replay_inventory_delta.py"
            ),
            2,
        )
        self.assertIn('cmp "$delta" "$delta_recomputed"', WORKFLOW)
        self.assertIn(
            'git merge-base --is-ancestor "$baseline_commit" "$EXPECTED_COMMIT"',
            WORKFLOW,
        )
        self.assertIn(
            'delta["current"]["result_count"] - delta["baseline"]["result_count"]',
            WORKFLOW,
        )
        self.assertIn(
            "historical-replay-inventory-delta-${{ inputs.expected_commit }}",
            WORKFLOW,
        )
        self.assertIn("if: inputs.confirm_append_only_delta == true", WORKFLOW)
        self.assertNotIn("contents: write", WORKFLOW)
        self.assertNotIn("id-token: write", WORKFLOW)

    def test_transient_run_requires_followup_durable_evidence(self) -> None:
        self.assertIn("transient transport, not durable", DOCUMENTATION)
        self.assertIn("run ID and attempt", DOCUMENTATION)
        self.assertIn("inventory SHA-256", DOCUMENTATION)
        self.assertIn("inventory workflow deliberately has no", DOCUMENTATION)
        self.assertIn("write credential", DOCUMENTATION)


if __name__ == "__main__":
    unittest.main()
