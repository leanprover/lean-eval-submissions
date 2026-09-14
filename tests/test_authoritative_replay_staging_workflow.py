from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "authoritative-replay-staging.yml"


class AuthoritativeReplayStagingWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_controller_is_manual_serialized_and_staging_only(self) -> None:
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("environment: replay-staging", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("timeout-minutes: 360", self.text)
        self.assertIn('REPLAY_JOB_STARTED_EPOCH=$(date +%s)', self.text)
        self.assertNotIn("schedule:", self.text)
        self.assertNotIn("replay-production", self.text)
        self.assertNotIn("contents: write", self.text)

    def test_started_is_durable_before_audit_aws_or_executor_credentials(self) -> None:
        started = self.text.index("Append replay.started before acquiring")
        audit = self.text.index("Check out only the exact immutable audit commit")
        aws = self.text.index("Assume only the staging replay Invoke role")
        executor = self.text.index("Invoke the reviewed endpoint")
        self.assertLess(started, audit)
        self.assertLess(started, aws)
        self.assertLess(started, executor)
        self.assertIn("scripts/publish_replay_state_event", self.text[started:audit])

    def test_exact_enabled_health_is_required_before_started(self) -> None:
        health = self.text.index("Require the exact enabled reviewed staging executor")
        started = self.text.index("Append replay.started before acquiring")
        self.assertLess(health, started)
        section = self.text[health:started]
        self.assertIn('"deployed_commit": os.environ["GITHUB_SHA"]', section)
        self.assertIn('"replay_enabled": True', section)
        self.assertIn('bundle["execution_profile_digest"]', section)
        self.assertIn('bundle["measurement_config_digest"]', section)
        self.assertIn('bundle["registry_manifest_digest"]', section)

    def test_executor_has_no_aws_or_state_write_authority(self) -> None:
        section = self.text.split(
            "- name: Invoke the reviewed endpoint without AWS or State write authority", 1
        )[1].split("- name: Append the exact reported terminal outcome", 1)[0]
        self.assertIn('test -z "${AWS_ACCESS_KEY_ID:-}"', section)
        self.assertIn('test -z "${AWS_SECRET_ACCESS_KEY:-}"', section)
        self.assertIn('test -z "${AWS_SESSION_TOKEN:-}"', section)
        self.assertIn('test -z "${STATE_WRITE_KEY:-}"', section)
        self.assertNotIn("STAGING_STATE_WRITE_KEY", section)
        self.assertIn("--max-time 15", section)
        self.assertIn("--max-time 300", section)
        self.assertIn("--max-time 60", section)
        self.assertIn("--retry-all-errors", section)
        self.assertIn("--retry-max-time 300", section)
        self.assertIn("--retry-max-time 60", section)
        self.assertIn("process_deadline=$(( $(date +%s) + 20100 ))", section)
        self.assertIn("job_deadline=$(( REPLAY_JOB_STARTED_EPOCH + 20880 ))", section)
        self.assertIn('if [ "$job_deadline" -lt "$deadline" ]', section)
        self.assertIn('"$REPLAY_EXECUTOR_URL/status"', section)
        self.assertIn('sleep 15', section)

    def test_every_started_attempt_gets_terminal_or_explicit_failure(self) -> None:
        self.assertIn("state-event terminal", self.text)
        self.assertIn("state-event failed", self.text)
        self.assertIn("runner_lost", self.text)
        self.assertIn("runner_start_failed", self.text)
        self.assertIn("source_fetch_failed", self.text)
        self.assertIn("verdict_invalid", self.text)
        self.assertIn(
            "steps.started.outputs.appended == 'true' && steps.terminal.outputs.appended != 'true'",
            self.text,
        )

    def test_executor_failure_diagnostic_is_bounded_and_sanitized(self) -> None:
        executor = self.text.split(
            "- name: Invoke the reviewed endpoint without AWS or State write authority", 1
        )[1].split("- name: Append the exact reported terminal outcome", 1)[0]
        failure = self.text.split(
            "- name: Fail a started attempt explicitly if orchestration did not finish", 1
        )[1].split("- name: Write source-free successful replay evidence", 1)[0]
        self.assertIn("--write-out '%{http_code}'", executor)
        self.assertIn('test "$poll_status" = 200', executor)
        self.assertIn('executor-poll-response.json', executor)
        self.assertIn('.reason == "command_rpc_failed"', executor)
        self.assertIn('.reason == "sandbox_destroy_failed"', executor)
        self.assertIn('scripts/sanitize_executor_failure.py', failure)
        self.assertIn('sanitized_failure', failure)
        self.assertNotIn('cat "$RUNNER_TEMP/executor-response.json"', failure)
        self.assertNotIn('jq', failure)

    def test_aws_authority_is_unconditionally_dropped_before_state_finalization(self) -> None:
        cleanup = self.text.index(
            "Drop any remaining AWS authority before executor or State writer"
        )
        executor = self.text.index("Invoke the reviewed endpoint")
        terminal = self.text.index("Append the exact reported terminal outcome")
        failure = self.text.index("Fail a started attempt explicitly")
        self.assertLess(cleanup, executor)
        self.assertLess(cleanup, terminal)
        self.assertLess(cleanup, failure)
        section = self.text[cleanup:executor]
        self.assertIn("always() && steps.started.outputs.appended == 'true'", section)
        self.assertIn("AWS_ACCESS_KEY_ID=", section)
        self.assertIn("AWS_SECRET_ACCESS_KEY=", section)
        self.assertIn("AWS_SESSION_TOKEN=", section)

    def test_recovery_is_bounded_and_happens_before_new_planning(self) -> None:
        self.assertIn("replay_controller.py recover", self.text)
        recovery = self.text.index("Publish one stale-runner recovery and stop")
        planning = self.text.index("Plan the exact next queue task")
        self.assertLess(recovery, planning)
        self.assertIn("steps.preflight.outputs.kind == 'none'", self.text)

    def test_artifact_is_source_free_and_identity_is_scrubbed(self) -> None:
        artifact = self.text.split("name: authoritative-replay-staging-evidence", 1)[1]
        self.assertIn("authoritative-replay-staging-evidence.json", artifact)
        evidence = self.text.split("Write source-free successful replay evidence", 1)[1]
        evidence = evidence.split("- uses: actions/upload-artifact", 1)[0]
        self.assertNotIn("identity.age", evidence)
        self.assertNotIn("archive.tar.age", evidence)
        self.assertNotIn("archive-sidecar", evidence)
        self.assertIn('"executor_health"', evidence)
        self.assertIn(
            'shred --remove "$RUNNER_TEMP/identity.age" '
            '"$RUNNER_TEMP/executor-request.json"',
            self.text,
        )
        final_cleanup = self.text.split("Remove all private and internal scratch", 1)[1]
        self.assertIn(
            'shred --remove "$RUNNER_TEMP/executor-request.json"', final_cleanup
        )

    def test_actions_are_commit_pinned(self) -> None:
        pins = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", self.text)
        self.assertTrue(pins)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in pins))


if __name__ == "__main__":
    unittest.main()


class AuthoritativeReplayUploadOrderingTests(unittest.TestCase):
    """The archive transfer must sit between the audit checkout and the unwrap."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_archive_uploads_and_assembles_before_any_unwrap_or_aws_authority(self) -> None:
        split = self.text.index("Bind ciphertext and split it into upload parts")
        upload = self.text.index("Upload and assemble the archive before any unwrap authority")
        unwrap = self.text.index("Prepare one five-minute unwrap for the assembled archive")
        aws = self.text.index("Assume only the staging replay Invoke role")
        consume = self.text.index("Consume once, validate the exact identity")
        start = self.text.index("Invoke the reviewed endpoint without AWS or State write authority")
        self.assertLess(split, upload)
        # The capability lasts five minutes. Minting it after the transfer keeps
        # that window around the Lambda round trip rather than the whole upload.
        self.assertLess(upload, unwrap)
        self.assertLess(unwrap, aws)
        self.assertLess(aws, consume)
        self.assertLess(consume, start)

    def test_nonce_is_minted_before_the_archive_is_split_or_bound(self) -> None:
        nonce = self.text.index("replay_controller.py mint-nonce")
        split = self.text.index("replay_controller.py split-archive")
        unwrap = self.text.index("replay_controller.py prepare-unwrap")
        self.assertLess(nonce, split)
        self.assertLess(split, unwrap)
        self.assertIn('--runner-nonce "$(cat "$RUNNER_TEMP/runner-nonce")"', self.text)

    def test_upload_carries_no_aws_authority_and_checks_every_part(self) -> None:
        upload = self.text.index("Upload and assemble the archive before any unwrap authority")
        unwrap = self.text.index("Prepare one five-minute unwrap for the assembled archive")
        section = self.text[upload:unwrap]
        self.assertIn('test -z "${AWS_ACCESS_KEY_ID:-}"', section)
        self.assertIn('test "$part_status" = 202', section)
        self.assertIn('test "$finalize_status" = 200', section)
        self.assertIn("X-Lean-Eval-Part-Sha256", section)
        # Parts are removed as they are accepted, so a failure part way through
        # does not leave archive fragments on the runner.
        self.assertIn('rm -f "$part_path"', section)

    def test_status_requests_use_the_status_schema_not_the_transport_schema(self) -> None:
        # The status contract is schema 1 and does not track the transport
        # version, which is now 3.
        self.assertIn("schema_version: 1,", self.text)
        self.assertNotIn("            schema_version,\n", self.text)
