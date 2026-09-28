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

    def test_rejects_mutable_dispatch_before_consuming_a_capability(self) -> None:
        immutable_guard = 'test "$GITHUB_REF" = "$expected_ref"'
        self.assertIn('expected_ref="refs/tags/lean-eval-dispatch/$GITHUB_SHA"', WORKFLOW)
        self.assertLess(WORKFLOW.index(immutable_guard), WORKFLOW.index("Validate staging State"))
        self.assertLess(
            WORKFLOW.index(immutable_guard),
            WORKFLOW.index("Assume only the staging replay Invoke role"),
        )

    def test_drops_authority_and_uploads_only_source_free_runtime_evidence(self) -> None:
        self.assertIn('test -z "${AWS_ACCESS_KEY_ID:-}"', WORKFLOW)
        self.assertIn('shred --remove "$RUNNER_TEMP/identity.age"', WORKFLOW)
        self.assertGreaterEqual(WORKFLOW.count("umask 077"), 3)
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", WORKFLOW)
        self.assertIn("name: authoritative-replay-runtime-evidence", WORKFLOW)
        self.assertIn('"health": json.loads(', WORKFLOW)
        self.assertIn('"probe": json.loads(', WORKFLOW)
        artifact_section = WORKFLOW.split("name: authoritative-replay-runtime-evidence", 1)[1]
        self.assertNotIn("identity.age", artifact_section.split("Remove all remaining", 1)[0])
        self.assertNotIn("archive.tar.age", artifact_section.split("Remove all remaining", 1)[0])
        self.assertNotIn("contents: write", WORKFLOW)
        self.assertNotIn("STATE_WRITE", WORKFLOW)

    def test_runtime_evidence_requires_the_disabled_exact_image_deployment(self) -> None:
        self.assertIn('.status == "ok"', WORKFLOW)
        self.assertIn("--max-time 15", WORKFLOW)
        self.assertIn("--max-time 240", WORKFLOW)
        self.assertIn('.deployed_commit == $commit', WORKFLOW)
        self.assertIn('.replay_enabled == false', WORKFLOW)
        self.assertIn(
            '.reviewed_execution_profile_digest == '
            '"fabd804359adbe7737e1db206d1877d3b923385cf2e8e95ec2b3be69a0d4c651"',
            WORKFLOW,
        )
        self.assertIn(
            '.reviewed_measurement_config_digest == '
            '"2dfc898270b83b6c99689e3f551a102c5e76636ec9f469a408498080e3e45945"',
            WORKFLOW,
        )
        self.assertIn('.reviewed_vm_image_digest | test(', WORKFLOW)
        self.assertIn('.production_memory_gate_bytes == 12884901888', WORKFLOW)


if __name__ == "__main__":
    unittest.main()


class AcceptedArchiveUploadOrderingTests(unittest.TestCase):
    """The archive transfer must precede the capability it would otherwise burn."""

    def test_upload_and_assembly_precede_the_unwrap_and_aws_authority(self) -> None:
        split = WORKFLOW.index("Bind the exact ciphertext and split it into upload parts")
        upload = WORKFLOW.index("Upload and assemble the archive before any unwrap authority")
        unwrap = WORKFLOW.index("Prepare one five-minute capability for the assembled archive")
        aws = WORKFLOW.index("Assume only the staging replay Invoke role")
        consume = WORKFLOW.index("Consume once, prove reuse refusal, and drop AWS authority")
        self.assertLess(split, upload)
        # Assembly digests the archive inside the Sandbox. Doing that before the
        # capability exists means a bad upload costs a retry, not a burned key.
        self.assertLess(upload, unwrap)
        self.assertLess(unwrap, aws)
        self.assertLess(aws, consume)

    def test_upload_binds_the_nonce_and_carries_no_aws_authority(self) -> None:
        upload = WORKFLOW.index("Upload and assemble the archive before any unwrap authority")
        unwrap = WORKFLOW.index("Prepare one five-minute capability for the assembled archive")
        section = WORKFLOW[upload:unwrap]
        self.assertIn('test -z "${AWS_ACCESS_KEY_ID:-}"', section)
        self.assertIn("X-Lean-Eval-Upload-Kind: staging-archive-acceptance", section)
        self.assertIn('test "$part_status" = 202', section)
        self.assertIn('test "$finalize_status" = 200', section)
        self.assertIn('rm -f "$part_path"', section)

    def test_capability_binds_the_nonce_the_upload_already_used(self) -> None:
        unwrap = WORKFLOW.index("Prepare one five-minute capability for the assembled archive")
        aws = WORKFLOW.index("Assume only the staging replay Invoke role")
        self.assertIn(
            '--runner-nonce "$(cat "$RUNNER_TEMP/runner-nonce")"',
            WORKFLOW[unwrap:aws],
        )
