from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from scripts.build_evaluation_completion import build
from scripts.build_evaluation_receipt import build as freeze_receipt

SUBMISSION_ID = "0198abcd-1111-7000-8000-000000000001"
START = "2026-08-21T12:01:00.000Z"
END = "2026-08-21T14:01:00.000Z"


class EvaluationCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.archive = self.root / 'archive.json'
        self.archive.write_text(json.dumps({
            'schema_version': 1, 'occurred_at': '2026-08-21T12:00:00.000Z',
            'locator': {'schema_version': 1, 'submission_id': SUBMISSION_ID,
                        'archive_repository': 'leanprover/lean-eval-audit', 'archive_commit': 'a'*40,
                        'archive_path': f'archives/01/{SUBMISSION_ID}.tar.age',
                        'archive_ciphertext_sha256': 'b'*64, 'encrypted': True}}))
        self.results = self.root / 'results.json'
        self.results.write_text(json.dumps({'passed': ['two_plus_two']}))
        self.context = {
            'submission_id': SUBMISSION_ID, 'workflow_commit': 'd'*40, 'workflow_run_id': '123',
            'source_repository': 'owner/source', 'source_commit': 'e'*40,
            'source_metadata_sha256': 'f'*64, 'benchmark_commit': 'c'*40,
            'toolchain': 'leanprover/lean4:v4.36.0', 'problem_id': 'two_plus_two',
            'statement_revision': 1, 'runner_tier': 'standard'}
        self.receipt = self.root / 'terminal.json'
        self.freeze()

    def freeze(self, outcome='success', results=True, **changes):
        value = freeze_receipt(context=self.context, workflow_run_attempt=1,
                               started_at=START, completed_at=END, evaluate_result=outcome,
                               results=self.results if results else None)
        value.update(changes)
        self.receipt.write_text(json.dumps(value))

    def complete(self, **changes):
        args = dict(archive_completion=self.archive, results=self.results, evaluate_result='success',
                    problem_id=self.context['problem_id'], benchmark_commit=self.context['benchmark_commit'],
                    toolchain=self.context['toolchain'], evaluator_version=self.context['workflow_commit'],
                    terminal_receipt=self.receipt, receipt_context=self.context,
                    current_run_attempt=1)
        args.update(changes)
        return build(**args)

    def test_two_hour_evaluation_uses_actual_frozen_terminal_time(self):
        value = self.complete()
        self.assertEqual(value['schema_version'], 2)
        self.assertEqual(value['occurred_at'], START)
        self.assertEqual(value['completed_at'], END)
        self.assertEqual(value['outcome'], {'status': 'accepted', 'evaluator_version': 'd'*40})
        # Re-running only the callback uses the original attempt's receipt.
        self.assertEqual(self.complete(current_run_attempt=3), value)

    def test_rejected_and_unsupported_outcomes_bind_to_original_results(self):
        for reason in ['proof_rejected', 'unsupported_toolchain_request']:
            self.results.write_text(json.dumps({'passed': [], 'rejection_reason': reason}))
            self.freeze()
            self.assertEqual(self.complete()['outcome'], {'status': 'rejected', 'reason_code': reason})

    def test_pipeline_failure_is_explicit_and_retryable(self):
        self.freeze('failure', results=False)
        self.assertEqual(self.complete(results=None, evaluate_result='failure')['outcome'], {
            'status': 'failed', 'reason_code': 'evaluation_pipeline_failed', 'retryable': True})

    def test_allowlist_block_is_a_policy_failure_not_rejection(self):
        self.freeze('failure', results=False)
        self.assertEqual(self.complete(results=None, evaluate_result='failure', allowlist_required=True)['outcome'], {
            'status': 'failed', 'reason_code': 'large_runner_allowlist_required', 'retryable': False})

    def test_success_requires_results(self):
        with self.assertRaisesRegex(ValueError, 'requires results'):
            self.complete(results=None)

    def test_release_candidate_toolchains(self):
        for toolchain in ['leanprover/lean4:v4.35.0-rc3', 'leanprover/lean4:v4.36.0-beta1']:
            self.context['toolchain'] = toolchain
            self.freeze()
            self.assertEqual(self.complete()['toolchain'], toolchain)
        for toolchain in ['leanprover/lean4:v4.35.0-rc', 'leanprover/lean4:v4.35.0-nightly', 'v4.35.0-rc3']:
            with self.assertRaisesRegex(ValueError, 'toolchain is not canonical'):
                self.complete(toolchain=toolchain)

    def test_each_original_identity_and_outcome_binding_is_required(self):
        original = json.loads(self.receipt.read_text())
        swaps = {'submission_id': '0198abcd-1111-7000-8000-000000000002',
                 'workflow_commit': '0'*40, 'workflow_run_id': '124', 'source_repository': 'other/source',
                 'source_commit': '0'*40, 'source_metadata_sha256': '0'*64,
                 'benchmark_commit': '0'*40, 'toolchain': 'leanprover/lean4:v4.35.0',
                 'problem_id': 'other', 'statement_revision': 2, 'runner_tier': 'large',
                 'evaluate_result': 'failure', 'results_sha256': '0'*64}
        for field, replacement in swaps.items():
            with self.subTest(field=field):
                value = {**original, field: replacement}
                self.receipt.write_text(json.dumps(value))
                with self.assertRaises(ValueError): self.complete()
        self.receipt.write_text(json.dumps(original))

    def test_newer_receipt_cannot_be_used_by_older_attempt(self):
        self.freeze(workflow_run_attempt=3)
        with self.assertRaisesRegex(ValueError, 'run attempt'):
            self.complete(current_run_attempt=2)

    def test_mutated_results_cannot_reuse_a_good_receipt(self):
        self.results.write_text(json.dumps({'passed': []}))
        with self.assertRaisesRegex(ValueError, 'digest'):
            self.complete()

    def test_missing_receipt_never_falls_back_to_archive_or_callback_time(self):
        self.receipt.unlink()
        with self.assertRaisesRegex(ValueError, 'receipt is unreadable'):
            self.complete()

    def test_terminal_start_and_archive_must_be_causally_ordered(self):
        original = json.loads(self.receipt.read_text())
        for changes in [{'completed_at': START}, {'completed_at': 'bad'},
                        {'started_at': '2026-08-20T12:00:00.000Z'}]:
            with self.subTest(changes=changes):
                self.receipt.write_text(json.dumps({**original, **changes}))
                with self.assertRaises(ValueError): self.complete()

    def test_shared_worker_vector_matches_both_real_producers(self):
        vector = json.loads((pathlib.Path(__file__).parent / 'fixtures/evaluation-terminal-v2-vector.json').read_text())
        self.archive.write_text(json.dumps(vector['archive']))
        self.results.write_text(json.dumps(vector['results']))
        self.context = vector['context']
        receipt = freeze_receipt(context=self.context, workflow_run_attempt=1,
                                 started_at=vector['receipt']['started_at'],
                                 completed_at=vector['receipt']['completed_at'],
                                 evaluate_result='success', results=self.results)
        self.assertEqual(receipt, vector['receipt'])
        self.receipt.write_text(json.dumps(receipt))
        self.assertEqual(self.complete(current_run_attempt=2), vector['completion'])

    def test_closed_receipt_rejects_unknown_fields(self):
        self.freeze(unknown='data')
        with self.assertRaisesRegex(ValueError, 'fields'):
            self.complete()


if __name__ == '__main__':
    unittest.main()
