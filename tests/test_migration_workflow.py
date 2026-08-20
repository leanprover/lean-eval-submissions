from __future__ import annotations

import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SUBMISSION = (REPO_ROOT / ".github/workflows/submission.yml").read_text()
MIGRATION = (REPO_ROOT / ".github/workflows/migrate-results-v2.yml").read_text()


class MigrationWorkflowStructureTests(unittest.TestCase):
    def test_shared_writer_contract_is_named_in_both_workflows(self) -> None:
        self.assertIn("RESULTS_STORE_WRITER_GROUP: results-store-writer", SUBMISSION)
        self.assertIn("group: results-store-writer", MIGRATION)
        self.assertIn("RESULTS_STORE_WRITER_GROUP: results-store-writer", MIGRATION)

    def test_record_waits_on_durable_lock_without_lossy_job_concurrency(self) -> None:
        record = SUBMISSION.split("\n  record:", 1)[1].split("\n  notify:", 1)[0]
        self.assertIn(".results-store-writer-lock.json", record)
        self.assertIn("git -C results-store fetch origin main", record)
        self.assertIn("git -C results-store reset --hard origin/main", record)
        self.assertNotIn("concurrency:", record)
        self.assertIn("timeout-minutes: 45", record)

    def test_migration_is_manual_dry_run_by_default(self) -> None:
        self.assertIn("workflow_dispatch:", MIGRATION)
        self.assertIn("default: false", MIGRATION)
        self.assertIn("resume_locked_migration:", MIGRATION)
        self.assertIn("if: inputs.apply", MIGRATION)
        self.assertIn("--report /tmp/results-v2-report.json", MIGRATION)

    def test_apply_is_bound_to_fresh_report_and_removes_lock(self) -> None:
        self.assertIn("--expect-source-digest", MIGRATION)
        self.assertIn("--expect-record-count", MIGRATION)
        self.assertIn("--expect-output-digest", MIGRATION)
        self.assertIn(
            "git -C results-store rm .results-store-writer-lock.json", MIGRATION
        )
        self.assertIn("resuming the operator-reviewed locked migration", MIGRATION)
        self.assertLess(
            MIGRATION.index("Acquire durable writer lock"),
            MIGRATION.index("Create fresh migration report"),
        )
        self.assertLess(
            MIGRATION.index("Create fresh migration report"),
            MIGRATION.index("Apply exactly the reviewed in-run report"),
        )

    def test_only_recorder_app_can_push_migration(self) -> None:
        self.assertIn("LEAN_EVAL_RECORDER_CLIENT_ID", MIGRATION)
        self.assertIn("LEAN_EVAL_RECORDER_PRIVATE_KEY", MIGRATION)
        self.assertIn("persist-credentials: false", MIGRATION)
        self.assertNotIn("permissions:\n      contents: write", MIGRATION)


if __name__ == "__main__":
    unittest.main()
