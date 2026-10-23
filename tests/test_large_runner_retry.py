from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from large_runner_retry import LogRedirect, allowlisted, decide  # noqa: E402
import run_with_resource_report  # noqa: E402


def job(conclusion: str = 'success', step: str = 'success') -> dict:
    return {'name': 'evaluate_standard / evaluate', 'conclusion': conclusion,
            'steps': [{'name': 'Run evaluate_submission.py', 'conclusion': step}]}


class RetryTests(unittest.TestCase):
    def test_log_storage_redirect_does_not_receive_the_github_token(self) -> None:
        request = urllib.request.Request('https://api.github.com/jobs/1/logs',
                                         headers={'Authorization': 'Bearer test-token'})
        redirected = LogRedirect().redirect_request(
            request, None, 302, 'Found', {}, 'https://storage.example/signed-log',
        )
        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header('Authorization'))

    def test_cgroup_oom_is_detected_when_child_returns_no_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = pathlib.Path(directory) / 'report.json'
            with patch.object(run_with_resource_report, 'oom_count', side_effect=[0, 1]):
                result = run_with_resource_report.run([sys.executable, '-c', 'pass'], report)
            self.assertEqual(result, 75)
            self.assertEqual(json.loads(report.read_text())['resource_reason'], 'out_of_memory')

    def test_empty_policy_blocks_escalation_with_actionable_message(self) -> None:
        result = decide('alice', [], job('failure', 'failure'), '',
                        {'resource_reason': 'out_of_memory'})
        self.assertFalse(result['retry'])
        self.assertTrue(result['allowlist_required'])
        self.assertIn('Kim Morrison', result['message'])
        self.assertIn('GitHub username', result['message'])

    def test_membership_uses_the_full_case_insensitive_login(self) -> None:
        self.assertTrue(allowlisted('Alice', ['aLiCe']))
        self.assertFalse(allowlisted('alice-other', ['alice']))
        with self.assertRaises(ValueError):
            allowlisted('alice', 'alice')

    def test_ordinary_bad_proof_never_retries_or_requests_access(self) -> None:
        for entries in ([], ['alice']):
            result = decide('alice', entries, job(), 'type mismatch\n')
            self.assertFalse(result['retry'])
            self.assertFalse(result['allowlist_required'])

    def test_resource_failure_inside_successful_harness_still_escalates(self) -> None:
        result = decide('alice', ['alice'], job(), 'error: lean exited with code 137')
        self.assertTrue(result['retry'])

    def test_dead_runner_needs_no_artifact(self) -> None:
        result = decide('alice', ['alice'], job('failure', 'cancelled'),
                        'The runner has received a shutdown signal')
        self.assertTrue(result['retry'])

    def test_timeout_and_unexpected_job_interruption_retry(self) -> None:
        self.assertTrue(decide('alice', ['alice'], job('timed_out', 'failure'), '')['retry'])
        self.assertTrue(decide('alice', ['alice'], job('cancelled', 'cancelled'), '')['retry'])
        workflow = (ROOT / '.github/workflows/submission.yml').read_text()
        escalation = workflow.split('\n  escalation:', 1)[1].split('\n  evaluate_large:', 1)[0]
        self.assertIn('!cancelled()', escalation)

    def test_deadline_returns_a_resource_report_instead_of_cancelling_the_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = pathlib.Path(directory) / 'timeout.json'
            code = run_with_resource_report.run(
                [sys.executable, '-c', 'import time; time.sleep(60)'], report, timeout_seconds=1,
            )
            self.assertEqual(code, 75)
            self.assertEqual(json.loads(report.read_text())['resource_reason'], 'evaluation_timeout')

    def test_preflight_and_heartbeat_failures_do_not_buy_more_ram(self) -> None:
        preflight = {'conclusion': 'failure', 'steps': [
            {'name': 'Run evaluate_submission.py', 'conclusion': 'skipped'}]}
        for details, log in ((preflight, 'exit code 137'),
                             (job(), 'maximum number of heartbeats has been reached')):
            self.assertFalse(decide('alice', ['alice'], details, log)['retry'])

    def test_real_child_signal_and_nested_allocation_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = pathlib.Path(directory) / 'resource.json'
            for code in (
                'import os, signal; os.kill(os.getpid(), signal.SIGKILL)',
                'print("error: cannot allocate memory"); raise SystemExit(0)',
            ):
                result = subprocess.run([
                    sys.executable, str(ROOT / 'scripts/run_with_resource_report.py'),
                    '--report', str(report), '--', sys.executable, '-c', code,
                ], capture_output=True, check=False)
                self.assertEqual(result.returncode, 75)
                self.assertTrue(decide('alice', ['alice'], job(), '',
                                       json.loads(report.read_text()))['retry'])

    def test_normalizer_selects_only_the_final_attempt(self) -> None:
        text = (ROOT / '.github/workflows/submission.yml').read_text()
        normalize = text.split('\n  evaluate:', 1)[1].split('\n  archive_issue:', 1)[0]
        script = normalize.split('        run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in script.splitlines())
        cases = [
            # Standard success, large skipped.
            ('true', '', 'false', 'false', 'success', 0),
            # Failed standard then successful large: one successful outcome.
            ('false', 'true', 'true', 'false', 'success', 0),
            # Neither failed attempt nor blocked escalation publishes a verdict.
            ('false', 'false', 'true', 'false', 'success', 1),
            ('true', '', 'false', 'true', 'success', 1),
            ('true', '', 'false', 'false', 'failure', 1),
        ]
        for standard, large, retry, blocked, decision_result, expected in cases:
            env = {**os.environ, 'STANDARD_SUCCEEDED': standard, 'LARGE_SUCCEEDED': large,
                   'RETRY': retry, 'ALLOWLIST_REQUIRED': blocked,
                   'ESCALATION_RESULT': decision_result}
            self.assertEqual(subprocess.run(['bash', '-e', '-c', script], env=env).returncode,
                             expected)
        large = text.split('\n  evaluate_large:', 1)[1].split('\n  evaluate:', 1)[0]
        self.assertIn("needs.escalation.outputs.retry == 'true'", large)
        self.assertNotIn('evaluate_large', large.split('uses:', 1)[0])
        self.assertIn('needs.archive.outputs.benchmark_commit', large)
        self.assertIn('needs.archive.outputs.source_metadata_sha256', large)


if __name__ == '__main__':
    unittest.main()
