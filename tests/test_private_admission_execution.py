"""Run actual workflow CAS bodies with bounded local transport doubles."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/historical-private-replay.yml').read_text()


def run_body(name):
    section = WORKFLOW.split('- name: ' + name, 1)[1].split('\n      - name:', 1)[0]
    return textwrap.dedent(section.split('        run: |\n', 1)[1])


class AdmissionExecutionTests(unittest.TestCase):
    def exercise(self, name, *, capacity=True, prewarm=True):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / 'bin'
            binary.mkdir()
            (root / 'scripts').mkdir()
            runner = root / 'temporary'
            runner.mkdir()
            event_id = '01900000-0000-7000-8000-000000000001'
            sha = 'a' * 40
            stub = binary / 'python'
            stub.write_text(f'''#!{sys.executable}
import json,pathlib,subprocess,sys
if sys.argv[1].endswith('historical_private_phase.py'):
    raise SystemExit(subprocess.call([{sys.executable!r}, {str(ROOT / 'scripts/historical_private_phase.py')!r}, *sys.argv[2:]]))
args=sys.argv
if '--output' in args:
    output=pathlib.Path(args[args.index('--output')+1])
    event={{'event_id':{event_id!r}}}
    output.write_text(json.dumps({{'kind':'failed','expected_head':{sha!r},'event':event,'append':{{'expected_head':{sha!r},'event':event}},'state_head':{sha!r},'started_event_id':{event_id!r},'terminal_event_id':{event_id!r}}}))
''')
            stub.chmod(0o755)
            git = binary / 'git'
            git.write_text('#!/bin/sh\nif [ "$1" = clone ]; then for last; do :; done; mkdir -p "$last"; fi\nexit 0\n')
            git.chmod(0o755)
            publish = root / 'scripts/publish_replay_state_event'
            publish.write_text(f'#!/bin/sh\ntouch {root / "published"}\nprintf "%s\\n" {sha}\n')
            publish.chmod(0o755)
            (runner / 'private-replay-plan.json').write_text('{}\n')
            output = root / 'output'
            environment = os.environ.copy()
            for key in ['AWS_ACCESS_KEY_ID', 'CLOUDFLARE_API_TOKEN', 'PRIVATE_PREWARM_MONOTONIC',
                        'PRIVATE_PREWARM_EPOCH', 'REPLAY_JOB_STARTED_MONOTONIC']:
                environment.pop(key, None)
            now = int(time.monotonic())
            environment.update(PATH=str(binary) + ':' + environment['PATH'],
                               RUNNER_TEMP=str(runner), GITHUB_OUTPUT=str(output),
                               PRIVATE_REPLAY_LANE_INDEX='2', PRIVATE_REPLAY_LANE_COUNT='4')
            if prewarm:
                environment['PRIVATE_PREWARM_MONOTONIC'] = str(now)
                environment['REPLAY_JOB_STARTED_MONOTONIC'] = str(now - (0 if capacity else 7000))
            result = subprocess.run(['bash', '-c', run_body(name)], cwd=root,
                                    env=environment, capture_output=True, text=True, timeout=10)
            return result, (root / 'published').exists(), output.read_text() if output.exists() else ''

    def test_recovery_needs_no_prewarm_clock_and_still_publishes(self):
        result, published, output = self.exercise(
            'Publish only a destruction-confirmed abandoned-run recovery', prewarm=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(published)
        self.assertIn('appended=true', output)

    def test_real_started_cas_refuses_insufficient_capacity_before_publish(self):
        result, published, output = self.exercise('Append the exact replay.started candidate with CAS',
                                                  capacity=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(published)
        self.assertNotIn('appended=true', output)
        result, published, output = self.exercise('Append the exact replay.started candidate with CAS')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(published)
        self.assertIn('appended=true', output)


class AcceptedStartExecutionTests(unittest.TestCase):
    def test_valid_202_transfers_cleanup_before_failed_host_shred(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / 'bin'
            binary.mkdir()
            shred = binary / 'shred'
            shred.write_text('#!/bin/sh\nexit 1\n')
            shred.chmod(0o755)
            identity = {'schema_version': 1, 'replay_task_id': 'fixture', 'attempt': 1}
            (root / 'cleanup-request.json').write_text(json.dumps(identity))
            (root / 'executor-start-response.json').write_text(json.dumps({**identity, 'status': 'running'}))
            output = root / 'output'
            body = run_body('Start the exact blocked-network executor asynchronously')
            body = body[body.index('if [ "$start_transport" != ok ]'):]
            environment = os.environ.copy()
            environment.update(PATH=str(binary) + ':' + environment['PATH'],
                               RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(output),
                               PRIVATE_PREWARM_MONOTONIC=str(int(time.monotonic())))
            result = subprocess.run(['bash', '-c',
                'set -euo pipefail\nstart_transport=ok\nstart_status=202\ncleanup() { :; }\n' + body],
                env=environment, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)  # Host shred actually failed.
            self.assertIn('accepted=true', output.read_text())
            self.assertIn('accepted_elapsed=', output.read_text())


if __name__ == '__main__':
    unittest.main()
