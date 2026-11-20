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
