from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "historical-final-delta-packet.yml"
).read_text(encoding="utf-8")


class HistoricalFinalDeltaPacketWorkflowTests(unittest.TestCase):
    def test_is_manual_read_only_and_explicitly_nonactivating(self) -> None:
        self.assertIn("  workflow_dispatch:", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)
        self.assertNotIn("\n  push:", WORKFLOW)
        self.assertRegex(WORKFLOW, r"(?m)^permissions:\n  contents: read$")
        self.assertNotIn("secrets.", WORKFLOW)
        self.assertNotIn("id-token: write", WORKFLOW)
        self.assertNotIn("contents: write", WORKFLOW)
        self.assertIn("if: inputs.confirm_preparation_only == true", WORKFLOW)
        self.assertIn("blocked_pending_exact_profiles_and_state_append", WORKFLOW)
        self.assertNotIn("lean-eval-state.git", WORKFLOW)
        self.assertNotIn("lean-eval-audit.git", WORKFLOW)

    def test_requires_exact_current_protected_dispatch_tag(self) -> None:
        self.assertIn('test "$GITHUB_SHA" = "$EXPECTED_COMMIT"', WORKFLOW)
        self.assertIn(
            'test "$GITHUB_REF" = "refs/tags/lean-eval-dispatch/$EXPECTED_COMMIT"',
            WORKFLOW,
        )
        self.assertIn('"$tag_ref" "$tag_ref^{}"', WORKFLOW)
        self.assertIn('test "${#tag_lines[@]}" -le 2', WORKFLOW)
        self.assertIn('test "$protected_main" = "$EXPECTED_COMMIT"', WORKFLOW)
        self.assertIn("ref: ${{ inputs.expected_commit }}", WORKFLOW)
        self.assertIn("fetch-depth: 0", WORKFLOW)
        self.assertIn("persist-credentials: false", WORKFLOW)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', WORKFLOW)
        self.assertIn(
            'test -z "$(git status --porcelain=v1 --untracked-files=all)"', WORKFLOW
        )

    def test_requires_content_addressed_committed_inputs(self) -> None:
        for prefix in (
            "evidence/historical-replay/inventories/$CURRENT_SHA.json",
            "evidence/historical-replay/deltas/$DELTA_SHA.json",
            "evidence/historical-replay/public-source-decisions/$PUBLIC_SHA.json",
            "evidence/historical-replay/private-crosswalks/$PRIVATE_SHA.json",
        ):
            self.assertIn(prefix, WORKFLOW)
        self.assertIn('git ls-files --error-unmatch -- "$path"', WORKFLOW)
        self.assertIn('sha256sum "$path"', WORKFLOW)
        self.assertIn('git rev-parse "$GITHUB_SHA:$path"', WORKFLOW)

    def test_recomputes_validates_and_uploads_only_blocked_packet(self) -> None:
        self.assertIn("expected_server_native_excluded_count:", WORKFLOW)
        self.assertIn('len(packet["server_exclusions"])', WORKFLOW)
        self.assertEqual(
            WORKFLOW.count("python scripts/prepare_historical_final_delta_packet.py"),
            1,
        )
        self.assertIn('for target in "$output" "$recomputed"', WORKFLOW)
        self.assertIn('cmp "$output" "$recomputed"', WORKFLOW)
        self.assertIn("Draft202012Validator.check_schema(schema)", WORKFLOW)
        self.assertIn("FormatChecker()", WORKFLOW)
        self.assertIn('"source" not in entry', WORKFLOW)
        upload = WORKFLOW.split(
            "name: Upload only the source-minimized blocked packet", 1
        )[1]
        self.assertIn("historical-final-delta-preparation.json", upload)
        self.assertNotIn("results/", upload)
        self.assertIn("retention-days: 30", upload)
        pins = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", WORKFLOW)
        self.assertTrue(pins)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in pins))


if __name__ == "__main__":
    unittest.main()
