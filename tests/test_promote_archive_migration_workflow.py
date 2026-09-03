from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "promote-archive-migration.yml"
).read_text(encoding="utf-8")


class PromoteArchiveMigrationWorkflowTests(unittest.TestCase):
    def test_is_self_contained_and_protected_main_only(self) -> None:
        self.assertNotIn(
            "uses: leanprover/lean-eval-audit/.github/workflows/", WORKFLOW
        )
        for guard in (
            'test "$CALLER_REPOSITORY" = leanprover/lean-eval-submissions',
            'test "$CALLER_REF" = refs/heads/main',
            'test "$CALLER_REF_PROTECTED" = true',
            'test "$CALLER_COMMIT" = "$EXPECTED_CALLER_COMMIT"',
            'test "$CONFIRMATION" = promote-reviewed-archive-migration',
        ):
            self.assertIn(guard, WORKFLOW)

    def test_archiver_token_is_narrow_and_not_persisted(self) -> None:
        self.assertIn("repositories: lean-eval-audit", WORKFLOW)
        self.assertIn("persist-credentials: false", WORKFLOW)
        self.assertIn("Mint only the audit-repository archiver token", WORKFLOW)
        self.assertNotIn("permissions:\n  contents: write", WORKFLOW)

    def test_contract_patch_and_cas_are_exactly_bound(self) -> None:
        for binding in (
            "7a53c75c6d7c263c684ebcd54590c657c9298642",
            'rev-parse "$EXPECTED_CONTRACT_COMMIT:scripts/archive_migration_promotion.py"',
            "archive_migration_promotion.py prepare",
            '--expected-patch-sha256 "$EXPECTED_PATCH_SHA256"',
            '--expected-result-commit "$EXPECTED_RESULT_COMMIT"',
            '"--force-with-lease=refs/heads/main:$CURRENT_AUDIT_HEAD"',
            '"--force-with-lease=refs/heads/archive-file-key-rewrap-v1:$STAGED_MIGRATION_COMMIT"',
        ):
            self.assertIn(binding, WORKFLOW)

    def test_external_actions_are_commit_pinned(self) -> None:
        pins = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", WORKFLOW)
        self.assertTrue(pins)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in pins))


if __name__ == "__main__":
    unittest.main()
