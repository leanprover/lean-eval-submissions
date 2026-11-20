"""Exercise the actual original-run producer and callback CLI across a retry."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EvaluationReceiptCliTests(unittest.TestCase):
    def test_failure_receipt_shell_reaches_failed_completion_without_results(self):
        # Execute the authored cleanup shell with only its absolute output path
        # relocated. This proves local receipt -> completion behavior, not that
        # GitHub guarantees cleanup after cancellation or runner termination.
        attempt = (ROOT / '.github/workflows/evaluate-attempt.yml').read_text()
        step = attempt.split('      - name: Freeze trusted evaluation terminal receipt\n', 1)[1].split(
            '      - name: Collect source-free acceptance measurements', 1)[0]
        self.assertIn("EVALUATE_RESULT: ${{ job.status == 'success' && 'success' || 'failure' }}", step)
        shell = step.split('        run: |\n', 1)[1]
        shell = '\n'.join(line[10:] if line.startswith('          ') else line
                          for line in shell.splitlines())
        vector = json.loads((ROOT / 'tests/fixtures/evaluation-terminal-v2-vector.json').read_text())
        context = vector['context']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'evaluate-out'
            receipt = output / 'evaluation-terminal.json'
            archive = root / 'archive.json'
            completion = root / 'completion.json'
            archive.write_text(json.dumps(vector['archive']))
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                       EVALUATE_RESULT='failure', STARTED_AT=vector['receipt']['started_at'],
                       GITHUB_SHA=context['workflow_commit'], GITHUB_RUN_ID=context['workflow_run_id'],
                       GITHUB_RUN_ATTEMPT='1')
            for key in ['submission_id', 'source_repository', 'source_commit', 'source_metadata_sha256',
                        'benchmark_commit', 'toolchain', 'problem_id', 'statement_revision', 'runner_tier']:
                env[key.upper()] = str(context[key])
            result = subprocess.run(['bash', '-c', shell.replace('/tmp/evaluate-out', str(output))],
                                    cwd=ROOT, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            frozen = json.loads(receipt.read_text())
            self.assertEqual(frozen['evaluate_result'], 'failure')
            self.assertIsNone(frozen['results_sha256'])
            context_args = [part for key, value in context.items()
                            if key != 'workflow_commit'
                            for part in ['--' + key.replace('_', '-'), str(value)]]
            callback = [sys.executable, '-B', 'scripts/build_evaluation_completion.py',
                        *context_args, '--evaluator-version', context['workflow_commit'],
                        '--archive-completion', str(archive), '--terminal-receipt', str(receipt),
                        '--current-run-attempt', '2', '--output', str(completion)]
            for allowlist_required in ['false', 'true']:
                command = [*callback, '--evaluate-result', 'failure',
                           '--allowlist-required', allowlist_required]
                result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                body = json.loads(completion.read_text())
                self.assertEqual(body['outcome'], {
                    'status': 'failed', 'reason_code': 'large_runner_allowlist_required'
                    if allowlist_required == 'true' else 'evaluation_pipeline_failed',
                    'retryable': allowlist_required != 'true'})
                self.assertEqual(body['completed_at'], frozen['completed_at'])
                self.assertEqual(body['occurred_at'], frozen['started_at'])
            # A locally recoverable attempt failure is not permission to
            # synthesize success or treat a cancelled/skipped aggregate as
            # a terminal callback. The real completion CLI must refuse.
            for aggregate_result in ['success', 'cancelled', 'skipped']:
                completion.unlink(missing_ok=True)
                result = subprocess.run([*callback, '--evaluate-result', aggregate_result],
                                        cwd=ROOT, env=env, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(completion.exists())

    def test_original_runner_freezes_once_and_callback_retry_reuses_that_time(self):
        vector = json.loads((ROOT / 'tests/fixtures/evaluation-terminal-v2-vector.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'archive.json'
            results = root / 'results.json'
            receipt = root / 'terminal.json'
            completion = root / 'completion.json'
            archive.write_text(json.dumps(vector['archive']))
            results.write_text(json.dumps(vector['results']))
            context = vector['context']
            context_args = [part for key, value in context.items()
                            for part in ['--' + key.replace('_', '-'), str(value)]]
            command = [sys.executable, '-B', 'scripts/build_evaluation_receipt.py',
                       *context_args, '--workflow-run-attempt', '1',
                       '--started-at', vector['receipt']['started_at'],
                       '--evaluate-result', 'success', '--results', str(results),
                       '--output', str(receipt)]
            before = dt.datetime.now(dt.timezone.utc)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            first = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
            after = dt.datetime.now(dt.timezone.utc)
            self.assertEqual(first.returncode, 0, first.stderr)
            original = receipt.read_bytes()
            terminal = json.loads(original)['completed_at']
            actual = dt.datetime.fromisoformat(terminal.replace('Z', '+00:00'))
            self.assertGreaterEqual(actual, before - dt.timedelta(milliseconds=1))
            self.assertLessEqual(actual, after)
            # A producer rerun must never rewrite original timing/evidence.
            duplicate = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertEqual(receipt.read_bytes(), original)
            callback = [sys.executable, '-B', 'scripts/build_evaluation_completion.py',
                        '--archive-completion', str(archive), '--results', str(results),
                        '--terminal-receipt', str(receipt), '--submission-id', context['submission_id'], '--evaluate-result', 'success',
                        '--problem-id', context['problem_id'], '--benchmark-commit', context['benchmark_commit'],
                        '--toolchain', context['toolchain'], '--evaluator-version', context['workflow_commit'],
                        '--workflow-run-id', context['workflow_run_id'], '--current-run-attempt', '2',
                        '--source-repository', context['source_repository'], '--source-commit', context['source_commit'],
                        '--source-metadata-sha256', context['source_metadata_sha256'],
                        '--statement-revision', str(context['statement_revision']), '--runner-tier', context['runner_tier'],
                        '--output', str(completion)]
            for _ in range(2):
                result = subprocess.run(callback, cwd=ROOT, env=env, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                body = json.loads(completion.read_text())
                self.assertEqual(body['completed_at'], terminal)
                self.assertEqual(body['occurred_at'], vector['receipt']['started_at'])
                self.assertEqual(body['schema_version'], 2)
                self.assertEqual(body['outcome']['status'], 'accepted')
            # Changing the selected original run cannot borrow this receipt.
            bad = callback.copy()
            bad[bad.index('--workflow-run-id') + 1] = '999'
            self.assertNotEqual(subprocess.run(bad, cwd=ROOT, env=env, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
