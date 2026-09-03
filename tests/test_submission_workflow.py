"""Structural guards for the server-only submission workflow."""

from __future__ import annotations

import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "submission.yml"
SERVER_ARCHIVE = REPO_ROOT / ".github" / "workflows" / "server-archive.yml"
ISSUE_CONFIG = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml"
ISSUE_FORM = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "submit.yml"
ISSUE_RECONCILER = REPO_ROOT / ".github" / "workflows" / "submission-reconciler.yml"
ISSUE_CUTOFF_GUARD = REPO_ROOT / "scripts" / "issue_intake_cutoff_guard.sh"


class SubmissionWorkflowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.archive_text = SERVER_ARCHIVE.read_text(encoding="utf-8")
        cls.lines = cls.text.splitlines()

    def test_server_dispatch_is_the_only_trigger(self) -> None:
        trigger = self.text.split("\non:", 1)[1].split("\nconcurrency:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("issues:", trigger)
        self.assertNotIn("issue_comment:", trigger)
        self.assertNotIn("pull_request:", trigger)
        self.assertNotIn("schedule:", trigger)
        self.assertNotIn("github.event.issue", self.text)
        self.assertNotIn("github.event.label", self.text)
        self.assertNotIn("github.event_name", self.text)

    def test_server_dispatch_contract_is_exact_and_complete(self) -> None:
        for field in (
            "workflow_commit", "submission_id", "submitted_by",
            "source_repository", "source_commit", "source_visibility",
            "problem_id", "problem_group", "statement_revision",
            "declared_model", "publication_choice", "production_metadata_json",
            "archive_locator_required", "archive_sidecar_schema",
            "archive_state_callback_required",
        ):
            self.assertRegex(
                self.text,
                rf"(?m)^      {field}: \{{description: .+, required: true, type: string\}}$",
            )
        self.assertRegex(self.text, r"(?m)^      callback_environment:$")
        self.assertIn("group: submission-${{ inputs.submission_id }}", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn('EXPECTED_WORKFLOW_COMMIT: ${{ inputs.workflow_commit }}', self.text)
        self.assertIn('if [ "$GITHUB_SHA" != "$EXPECTED_WORKFLOW_COMMIT" ]; then', self.text)

    def test_only_reviewed_server_jobs_remain(self) -> None:
        jobs_idx = self.lines.index("jobs:")
        job_headers = [
            line
            for line in self.lines[jobs_idx + 1 :]
            if re.match(r"^  \w[\w-]*:$", line)
        ]
        self.assertEqual(
            job_headers,
            [
                "  evaluate:",
                "  archive_server:",
                "  archive:",
                "  archive_failure_state:",
                "  archive_state:",
                "  evaluation_state:",
                "  record:",
                "  result_state:",
            ],
        )
        for retired in (
            "issue_intake_admission", "archive_issue", "notify:",
            "validate_submission_intake", "classify_evaluate_failure",
            "issue_intake_cutoff_guard",
        ):
            self.assertNotIn(retired, self.text)
        self.assertNotIn("issues: write", self.text)
        self.assertNotIn("secrets.GITHUB_TOKEN", self.text)

    def test_archive_precedes_independent_evaluation(self) -> None:
        evaluate = self.text.split("\n  evaluate:", 1)[1].split(
            "\n  archive_server:", 1
        )[0]
        self.assertIn("needs: [archive, archive_state]", evaluate)
        self.assertIn("needs.archive.result == 'success'", evaluate)
        self.assertIn("needs.archive_state.result == 'success'", evaluate)
        self.assertIn("python scripts/fetch_submission.py", evaluate)
        self.assertIn("--server-dispatch", evaluate)
        self.assertIn("python scripts/evaluate_submission.py", evaluate)
        self.assertIn("EXPECTED_METADATA_SHA256", evaluate)
        self.assertIn("ref: ${{ needs.archive.outputs.benchmark_commit }}", evaluate)
        self.assertNotIn("LIFECYCLE_CALLBACK_TOKEN", evaluate)
        self.assertNotIn("name: submission-source", self.text)
        self.assertNotIn("name: submission-audit-ciphertext", self.text)

    def test_kms_archive_lane_is_the_only_archive_authority(self) -> None:
        archive_server = self.text.split("\n  archive_server:", 1)[1].split(
            "\n  archive:", 1
        )[0]
        self.assertIn("uses: ./.github/workflows/server-archive.yml", archive_server)
        self.assertIn("id-token: write", archive_server)
        self.assertNotIn("if:", archive_server)

        normalized = self.text.split("\n  archive:", 1)[1].split(
            "\n  archive_failure_state:", 1
        )[0]
        self.assertIn("needs: archive_server", normalized)
        self.assertIn("if: always()", normalized)
        self.assertIn("SERVER_RESULT: ${{ needs.archive_server.result }}", normalized)
        self.assertIn('test "$SERVER_RESULT" = success', normalized)
        self.assertNotIn("ISSUE_", normalized)

        self.assertIn("environment: archive-${{ inputs.callback_environment }}", self.archive_text)
        self.assertIn("AWS_WRAP_ROLE_ARN", self.archive_text)
        self.assertIn("archive_envelope.py", self.archive_text)
        self.assertIn("prepare-envelope-sidecar", self.archive_text)
        self.assertIn("--locator-output /tmp/archive-locator.json", self.archive_text)
        self.assertIn("--completion-output /tmp/archive-completion.json", self.archive_text)
        self.assertIn("10 * 1024 * 1024", self.archive_text)

    def test_untrusted_evaluation_credentials_are_minimal(self) -> None:
        evaluate = self.text.split("\n  evaluate:", 1)[1].split(
            "\n  archive_server:", 1
        )[0]
        match = re.search(
            r"^    permissions:\n((?:^      [^\n]+\n?)+)", evaluate, re.MULTILINE
        )
        self.assertIsNotNone(match)
        self.assertEqual(
            [line.strip() for line in match.group(1).splitlines()],
            ["contents: read"],
        )
        self.assertEqual(evaluate.count("APP_INSTALLATION_TOKEN:"), 1)
        self.assertIn("rm -rf .git lean-eval/.git", evaluate)
        self.assertRegex(
            evaluate,
            re.compile(
                r"^      - uses: leanprover/lean-action@[0-9a-f]{40}\n"
                r"        with:\n"
                r"          lake-package-directory: lean-eval\n"
                r"          use-mathlib-cache: true\n"
                r"(?:          #.*\n)*"
                r"          use-github-cache: false$",
                re.MULTILINE,
            ),
        )

    def test_checkouts_and_app_tokens_remain_scoped(self) -> None:
        checkout_sha = "3d3c42e5aac5ba805825da76410c181273ba90b1"
        combined = self.text + self.archive_text
        self.assertEqual(
            set(re.findall(r"uses: actions/checkout@([0-9a-f]{40})", combined)),
            {checkout_sha},
        )
        self.assertGreaterEqual(combined.count("persist-credentials: false"), 4)
        self.assertIn("persist-credentials: true", self.text)
        self.assertEqual(self.archive_text.count("ARCHIVER_TOKEN:"), 1)
        self.assertRegex(self.archive_text, r"repositories:\s*lean-eval-audit\b")
        self.assertNotRegex(combined, re.compile(r"^\s+app-id:", re.MULTILINE))

    def test_lifecycle_callbacks_are_source_free_and_bounded(self) -> None:
        self.assertEqual(
            self.text.count(
                "LIFECYCLE_CALLBACK_TOKEN: ${{ secrets.LIFECYCLE_CALLBACK_TOKEN }}"
            ),
            4,
        )
        self.assertEqual(self.text.count("curl --fail-with-body"), 4)
        for option in (
            "--connect-timeout 10", "--max-time 30", "--retry 15",
            "--retry-delay 60", "--retry-max-time 900", "--retry-connrefused",
        ):
            self.assertEqual(self.text.count(option), 4)
        self.assertEqual(self.text.count('--output "$response_file"'), 4)
        self.assertNotIn('cat "$response_file"', self.text)
        self.assertNotIn("--retry-all-errors", self.text)
        for job, following in (
            ("archive_failure_state", "archive_state:"),
            ("archive_state", "evaluation_state:"),
            ("evaluation_state", "record:"),
            ("result_state", None),
        ):
            section = self.text.split(f"\n  {job}:", 1)[1]
            if following is not None:
                section = section.split(f"\n  {following}", 1)[0]
            self.assertIn("timeout-minutes: 20", section)

    def test_record_requires_terminal_server_state_and_receipts(self) -> None:
        record = self.text.split("\n  record:", 1)[1].split(
            "\n  result_state:", 1)[0]
        self.assertIn(
            "needs: [evaluate, archive, archive_state, evaluation_state]", record
        )
        self.assertIn("needs.evaluate.result == 'success'", record)
        self.assertIn("needs.archive.result == 'success'", record)
        self.assertIn("needs.evaluation_state.result == 'success'", record)
        self.assertIn("inputs.callback_environment == 'staging'", record)
        self.assertIn('push origin "HEAD:$RESULTS_BRANCH"', record)
        self.assertIn("scripts/build_result_receipt.py", record)
        self.assertIn("name: submission-result-completion", record)
        self.assertIn("if: inputs.callback_environment == 'production'", record)
        self.assertIn("event_type=results-advanced", record)
        self.assertIn("client_payload[submission_id]", record)
        self.assertNotIn("client_payload[issue]", record)

        result_state = self.text.split("\n  result_state:", 1)[1]
        self.assertIn("/internal/v1/result-completed", result_state)
        self.assertIn('"result_identity_conflicted"', result_state)
        self.assertIn('"already_result_identity_conflicted"', result_state)
        self.assertNotIn("submission-results", result_state)
        self.assertNotIn("actions/checkout", result_state)


class IssueIntakeRetirementTests(unittest.TestCase):
    def test_issue_form_and_reconciler_are_retired(self) -> None:
        self.assertFalse(ISSUE_FORM.exists())
        self.assertFalse(ISSUE_RECONCILER.exists())
        for path in (
            REPO_ROOT / "scripts" / "reconcile_orphan_submissions.py",
            REPO_ROOT / "scripts" / "validate_submission_intake.py",
            REPO_ROOT / "scripts" / "classify_evaluate_failure.py",
            REPO_ROOT / "tests" / "test_validate_submission_intake.py",
            REPO_ROOT / "tests" / "test_classify_evaluate_failure.py",
        ):
            self.assertFalse(path.exists(), str(path))

    def test_issue_picker_links_directly_to_submission_service(self) -> None:
        config = ISSUE_CONFIG.read_text(encoding="utf-8")
        self.assertIn("name: Submit a LeanEval solution", config)
        self.assertIn("url: https://lean-lang.org/eval/submit/", config)
        self.assertNotIn("template=submit.yml", config)

    def test_readme_is_server_only(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("https://lean-lang.org/eval/submit/", readme)
        self.assertIn("GitHub Issues are no longer a submission path", readme)
        self.assertNotIn("Issue Form", readme)
        self.assertNotIn("submission-reconciler", readme)
        self.assertNotIn("template=submit.yml", readme)

    def test_cutoff_guard_remains_for_final_delta_verification(self) -> None:
        self.assertTrue(ISSUE_CUTOFF_GUARD.is_file())
        self.assertTrue((REPO_ROOT / "tests" / "test_issue_intake_cutoff_guard.py").is_file())
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("issue_intake_cutoff_guard", workflow)


if __name__ == "__main__":
    unittest.main()
