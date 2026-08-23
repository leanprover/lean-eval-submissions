from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = (
    ROOT / ".github/workflows/accepted-archive-replay-staging.yml"
).read_text(encoding="utf-8")


class ReplayArchiveStagingWorkflowTests(unittest.TestCase):
    def test_is_protected_manual_staging_acceptance_only(self) -> None:
        self.assertIn("workflow_dispatch:", WORKFLOW)
        self.assertIn("if: inputs.confirm_staging_acceptance == true", WORKFLOW)
        self.assertIn("environment: replay-staging", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)
        self.assertNotIn("replay-production", WORKFLOW)

    def test_binds_state_audit_unwrap_and_executor_to_one_submission(self) -> None:
        for required in (
            "repository: leanprover/lean-eval-state-staging",
            "ssh-key: ${{ secrets.STAGING_STATE_READ_KEY }}",
            "repository: leanprover/lean-eval-audit",
            "ssh-key: ${{ secrets.AUDIT_READ_KEY }}",
            "--submission-id \"$SUBMISSION_ID\"",
            "--function-name lean-eval-archive-unwrap-staging",
            "validate-reuse-failure",
            "/api/v1/staging-archive-acceptance",
            "validate-response",
        ):
            self.assertIn(required, WORKFLOW)

    def test_drops_authority_and_never_uploads_private_material(self) -> None:
        self.assertIn('test -z "${AWS_ACCESS_KEY_ID:-}"', WORKFLOW)
        self.assertIn('rm "$RUNNER_TEMP/identity.age"', WORKFLOW)
        self.assertNotIn("actions/upload-artifact", WORKFLOW)
        self.assertNotIn("contents: write", WORKFLOW)
        self.assertNotIn("STATE_WRITE", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
