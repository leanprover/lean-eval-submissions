"""Exercise phase ownership conditions and the closed split boundaries."""
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import historical_private_phase as phase

TEXT = (ROOT / '.github/workflows/historical-private-replay.yml').read_text()
START = TEXT.split('  start-one:', 1)[1].split('  observe-one:', 1)[0]
OBSERVE = TEXT.split('  observe-one:', 1)[1].split('  replenish-private-lane:', 1)[0]


def condition_truth(condition, *, accepted, cancelled=False, always=True, **values):
    replacements = {'steps.execute.outputs.accepted': accepted,
                    'needs.start-one.outputs.accepted': accepted,
                    'always()': always, 'cancelled()': cancelled, **values}
    for name in sorted(replacements, key=len, reverse=True):
        condition = condition.replace(name, repr(replacements[name]))
    condition = condition.replace('&&', ' and ').replace('||', ' or ')
    condition = re.sub(r'!(?!=)', ' not ', condition)
    return eval(condition, {'__builtins__': {}}, {})


class PhaseWorkflowTests(unittest.TestCase):
    def test_accepted_transfer_retains_resource_even_when_later_start_step_failed(self):
        cleanup = "always() && steps.execute.outputs.accepted != 'true' && steps.started.outputs.appended == 'true'"
        observer = "always() && !cancelled() && needs.start-one.outputs.accepted == 'true'"
        for accepted in ['', 'true']:
            values = {'steps.started.outputs.appended': 'true'}
            self.assertEqual(condition_truth(cleanup, accepted=accepted, **values), accepted != 'true')
            self.assertEqual(condition_truth(observer, accepted=accepted), accepted == 'true')
        self.assertFalse(condition_truth(observer, accepted='true', cancelled=True))
        self.assertIn(cleanup, START)
        self.assertIn(observer, TEXT)
        self.assertIn("needs.start-one.result == 'success'", TEXT)
        self.assertIn("needs.observe-one.result == 'success'", TEXT)

    def test_no_fallible_running_proof_between_valid_202_and_ownership_output(self):
        post = START.split('Start the exact blocked-network executor asynchronously', 1)[1]
        check = post.index('private executor start response binding invalid')
        accepted = post.index("echo 'accepted=true'", check)
        self.assertNotIn('prove_running', post[check:accepted])
        self.assertNotIn('prove_running', post[accepted:post.index('- name:', accepted)])
        self.assertIn('timeout --signal=TERM --kill-after=5s 780s curl', post)

    def test_observer_cannot_restart_unwrap_or_retransfer_private_source(self):
        for forbidden in ['aws-actions/', 'aws lambda', 'prepare-unwrap', 'build-executor-request',
                          '/api/v1/replay/archive-part', '/api/v1/replay/archive-finalize', 'key_material', 'DISPATCH_TOKEN']:
            self.assertNotIn(forbidden, OBSERVE)
        self.assertNotIn('secrets.', TEXT.split('  replenish-private-lane:')[1])
        self.assertIn('PRIVATE_HANDOFF: ${{ needs.start-one.outputs.handoff }}', OBSERVE)
        self.assertIn('git -C metadata merge-base --is-ancestor "$GITHUB_SHA" HEAD', OBSERVE)
        self.assertIn('--repository-root metadata --audit-root audit', OBSERVE)
        self.assertEqual(TEXT.count('test "$GITHUB_RUN_ATTEMPT" = 1'), 2)
        self.assertIn('timeout-minutes: 120', START)
        self.assertIn('timeout-minutes: 360', OBSERVE)

    def test_sdk_pin_and_finite_handoff_do_not_shorten_process_budget(self):
        package = json.loads((ROOT / 'server/package.json').read_text())
        self.assertEqual(package['dependencies']['@cloudflare/sandbox'], '0.12.7')
        app = (ROOT / 'server/src/replay-app.ts').read_text()
        self.assertIn(f'const AUTHORITATIVE_TIMEOUT_MS = {phase.SERVER_PROCESS_SECONDS * 1000:_};', app)
        entry = (ROOT / 'server/src/historical-private-replay-entry.ts').read_text()
        self.assertIn('6 * 60 * 60', entry)
        self.assertIn('cleanupDeadline - 60 * 60 * 1000', entry)
        self.assertNotIn('keepAlive: true', entry)
        self.assertIn('starting ? null : "15m"', entry)
        self.assertLess(phase.PREWARM_MAX_AGE_SECONDS + phase.POST_SECONDS
                        + phase.SERVER_PROCESS_SECONDS + phase.RECEIPT_GRACE_SECONDS
                        + phase.CLEANUP_SECONDS, 7 * 3600)

    def test_source_free_job_outputs_cannot_become_private_artifacts(self):
        output = START.split('    outputs:', 1)[1].split('    environment:', 1)[0]
        for forbidden in ['archive', 'key', 'unwrap', 'source', 'token', 'request.json']:
            self.assertNotIn(forbidden, output)
        self.assertNotIn('upload-artifact', OBSERVE.split('Observe the exact blocked-network executor')[0])
        self.assertIn('historical-private-replay-redacted.json', OBSERVE)


if __name__ == '__main__':
    unittest.main()
