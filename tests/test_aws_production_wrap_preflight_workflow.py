from __future__ import annotations

import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "aws-production-wrap-preflight.yml"
PROCEDURE = ROOT / "docs" / "aws-key-adapter-setup.md"


class AwsProductionWrapPreflightWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.procedure = PROCEDURE.read_text(encoding="utf-8")

    def test_is_manual_one_shot_and_exact_tag_bound(self) -> None:
        self.assertIn("  workflow_dispatch:", self.text)
        self.assertNotIn("schedule:", self.text)
        self.assertNotIn("push:", self.text)
        self.assertIn("refs/tags/lean-eval-dispatch/${{ github.sha }}", self.text)
        self.assertIn("environment: archive-production", self.text)
        self.assertNotIn("archive-staging", self.text)
        self.assertNotIn("replay-production", self.text)
        self.assertIn("permissions: {}", self.text)
        self.assertEqual(self.text.count("contents: read"), 0)

    def test_absent_role_is_inert_and_exact_role_is_required(self) -> None:
        guard = self.text.split(
            "name: Require the exact configured production Wrap role", 1
        )[1].split("# aws-actions/configure-aws-credentials", 1)[0]
        self.assertIn('if [ -z "$CONFIGURED_ROLE_ARN" ]', guard)
        self.assertIn("configured=false", guard)
        self.assertIn('test "$CONFIGURED_ROLE_ARN" = "$EXPECTED_ROLE_ARN"', guard)
        self.assertIn(
            "arn:aws:iam::161072922960:role/lean-eval-archive-wrap-production",
            guard,
        )
        self.assertEqual(
            self.text.count("if: steps.configuration.outputs.configured == 'true'"),
            2,
        )

    def test_authority_is_only_oidc_wrap_role_and_exact_key(self) -> None:
        self.assertEqual(self.text.count("id-token: write"), 1)
        self.assertIn("role-to-assume: ${{ vars.AWS_WRAP_ROLE_ARN }}", self.text)
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("AWS_ACCESS_KEY_ID: ${{", self.text)
        self.assertIn(
            "arn:aws:kms:us-east-1:161072922960:key/"
            "219904f9-4952-400f-b60a-6f027c4d070b",
            self.text,
        )
        actions = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", self.text)
        self.assertEqual(len(actions), 1)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in actions))
        self.assertIn("allowed-account-ids: 161072922960", self.text)
        self.assertIn("retry-max-attempts: 4", self.text)
        self.assertIn("output-credentials: false", self.text)
        self.assertIn("output-env-credentials: true", self.text)
        self.assertIn("unset-current-credentials: true", self.text)

    def test_exact_contract_context_and_decrypt_denial_are_required(self) -> None:
        for field in (
            "contract=lean-eval-archive-key-v1",
            "submission_id=",
            "archive_ciphertext_sha256=",
            "data_key_id=$data_key_id",
            "age_recipient_sha256=",
        ):
            self.assertIn(field, self.text)
        self.assertEqual(self.text.count("aws kms encrypt"), 1)
        self.assertEqual(self.text.count("aws kms decrypt"), 1)
        self.assertIn("AccessDeniedException", self.text)
        self.assertIn('test ! -s "$decrypt_output"', self.text)
        self.assertIn('data_key_id="ak1_$(', self.text)
        self.assertIn("lean-eval-archive-key-v1\\0%s\\0%s", self.text)

    def test_preflight_has_no_write_or_artifact_surface(self) -> None:
        for forbidden in (
            "actions/checkout",
            "upload-artifact",
            "download-artifact",
            "GITHUB_STATE_TOKEN",
            "ARCHIVER_TOKEN",
            "LEADERBOARD_WRITE_TOKEN",
            "lean-eval-audit",
            "lean-eval-state",
            "submission.yml",
            "repository_dispatch",
            "workflow_call",
            "aws lambda",
            "aws dynamodb",
        ):
            self.assertNotIn(forbidden, self.text)
        self.assertIn('rm -rf "$scratch"', self.text)
        self.assertIn(
            "unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN", self.text
        )
        self.assertIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN=", self.text)
        self.assertIn("ACTIONS_ID_TOKEN_REQUEST_URL=", self.text)
        self.assertIn(
            "AWS authority survived production Wrap preflight cleanup", self.text
        )
        self.assertEqual(self.text.count("aws sts get-caller-identity"), 1)

    def test_operator_procedure_is_closed_and_states_durable_authority(self) -> None:
        for required in (
            "scripts/preflight_production_wrap_role.py",
            "including historical immutable tags",
            "one synthetic preflight run",
            "AWS_WRAP_ROLE_ARN",
            "gh variable delete AWS_WRAP_ROLE_ARN",
            "active_wrap_runs",
            "aws-production-wrap-preflight.yml submission.yml",
            "authority-quiet-at",
            'test "$(( $(date -u +%s) - quiet_at ))" -ge 3600',
            "including every 900-second preflight session",
            "monitor_cloudflare_health.py",
            "verify_production_capabilities_disabled.py",
            "production.capabilities",
            "PUBLICATION_ENABLED",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.procedure)
        self.assertIn(
            "otherwise changing the wildcard policy is a separate",
            self.procedure,
        )
        self.assertIn(
            "git/ref/tags/lean-eval-dispatch/$LEAN_EVAL_WRAP_WORKFLOW_COMMIT",
            self.procedure,
        )
        self.assertIn(
            "repos/$LEAN_EVAL_SUBMISSIONS/branches/main",
            self.procedure,
        )
        self.assertIn(
            '--jq .commit.sha)" = "$LEAN_EVAL_WRAP_WORKFLOW_COMMIT"',
            self.procedure,
        )
        self.assertIn('--jq .protected)" = true', self.procedure)
        self.assertIn("use_default: true", self.procedure)
        self.assertIn("use_immutable_subject: false", self.procedure)
        self.assertIn(
            'sub_claim_prefix: "repo:leanprover/lean-eval-submissions"',
            self.procedure,
        )
        self.assertIn(".can_admins_bypass == true", self.procedure)
        self.assertIn("repository administrators can bypass", self.procedure)
        self.assertEqual(
            self.procedure.count('--expected-commit "$LEAN_EVAL_WRAP_WORKFLOW_COMMIT"'),
            1,
        )
        self.assertIn('--expected-commit "$selected_commit"', self.procedure)
        self.assertEqual(
            self.procedure.count(".deployed_commit == $commit"),
            2,
        )
        self.assertEqual(
            self.procedure.count(
                "repos/$LEAN_EVAL_SUBMISSIONS/environments/replay-production/variables"
            ),
            2,
        )
        self.assertNotIn("AWS_REPLAY_UNWRAP_ROLE_ARN=", self.procedure)
        self.assertEqual(self.procedure.count('active_ids="$(active_wrap_runs)"'), 3)
        self.assertNotIn("for run_id in $(active_wrap_runs)", self.procedure)
        self.assertNotIn('test -z "$(active_wrap_runs)"', self.procedure)
        dispatch = (
            self.procedure.split("Select the exact final protected-main", 1)[1]
            .split("```sh\n", 1)[1]
            .split("\n```", 1)[0]
        )
        self.assertLess(dispatch.index("branches/main"), dispatch.index("git/ref/tags"))
        self.assertLess(
            dispatch.index("git/ref/tags"), dispatch.index("gh workflow run")
        )

    def test_rollback_fails_closed_when_run_listing_fails(self) -> None:
        rollback = (
            self.procedure.split("The first block deletes the variable,", 1)[1]
            .split("```sh\n", 1)[1]
            .split("\n```", 1)[0]
        )
        self.assertEqual(rollback.count('active_ids="$(active_wrap_runs)"'), 2)
        self.assertNotIn("for run_id in $(active_wrap_runs)", rollback)
        self.assertNotIn('test -z "$(active_wrap_runs)"', rollback)

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = pathlib.Path(temporary_directory)
            gh = temporary / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                'case "$*" in\n'
                "  *actions/workflows*)\n"
                '    count="$(cat "$GH_SHIM_STATE")"\n'
                '    count="$((count + 1))"\n'
                '    printf \'%s\\n\' "$count" > "$GH_SHIM_STATE"\n'
                '    test "$count" -ne 1 || exit 71\n'
                "    exit 0\n"
                "    ;;\n"
                "  *) exit 0 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
            shim_state = temporary / "gh-count"
            shim_state.write_text("0\n", encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{temporary}:{environment['PATH']}",
                    "GH_SHIM_STATE": str(shim_state),
                    "LEAN_EVAL_SUBMISSIONS": "leanprover/lean-eval-submissions",
                    "LEAN_EVAL_ARCHIVE_ENVIRONMENT": "archive-production",
                    "LEAN_EVAL_WRAP_WORKFLOW_COMMIT": "a" * 40,
                }
            )
            result = subprocess.run(
                ["bash", "-c", rollback],
                check=False,
                capture_output=True,
                env=environment,
                text=True,
            )

        self.assertEqual(result.returncode, 71, result.stderr)


if __name__ == "__main__":
    unittest.main()
