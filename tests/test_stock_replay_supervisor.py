from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import stock_replay_supervisor as supervisor
from test_stock_replay_measurement import Context, BWRAP, PYTHON

ORIGINAL = ROOT / 'tests/fixtures/stock-original-workspace-test.lean'
SOURCE_SHA = '082806a7a6db89461febef4cf50c1d8c620945f96cd1a214a0b481a4cb6ee971'
STOCK_CACHE = Path('/tmp/lean-eval-435stable-stock-compat')


class Fixture(Context):
    """Synthetic programs for owner faults, never a qualified mathematical run."""
    def __init__(self, root, *, codes=None, driver_exit=None, sleep=0, real=True):
        super().__init__(root, real=real, codes=codes)
        (self.project / 'WorkspaceTest.lean').write_bytes(ORIGINAL.read_bytes())
        (self.project / 'Challenge.lean').write_text('-- synthetic owner fixture\n')
        (self.project / 'Solution.lean').write_text('-- trusted generated wrapper fixture\n')
        (self.project / 'Submission.lean').write_text('-- untrusted fixture input\n')
        (self.prefix / 'bin/lean').write_text('#!/bin/sh\nexit 0\n')
        (self.prefix / 'bin/lean').chmod(0o700)
        (self.project / 'lean-toolchain').write_text('leanprover/lean4:v4.35.0\n')
        (self.project / 'lake-manifest.json').write_text('{}\n')
        (self.project / 'lakefile.toml').write_text('name = "fixture"\ntestDriver = "workspace_test"\n[[lean_exe]]\nname = "workspace_test"\nroot = "WorkspaceTest"\n')
        (self.project / 'config.json').write_text(json.dumps({'challenge_module': 'Challenge', 'solution_module': 'Solution',
            'theorem_names': ['target'], 'permitted_axioms': []}))
        driver = self.project / '.lake/build/bin/workspace_test'
        driver.parent.mkdir(parents=True)
        driver.write_text(f'#!{PYTHON}\n' + f'''
import os,pathlib,subprocess,sys,time
sys.path.insert(0,'/opt/lean-eval')
import stock_replay_measurement as s
p=s.decode(s.read_file(pathlib.Path('/run/lean-eval/stock-measurement-policy.json')))
time.sleep({sleep})
''' + (f'raise SystemExit({driver_exit})\n' if driver_exit is not None else '''
export=pathlib.Path(p['temporary_directory'])/'export'
export.write_text('synthetic export');export.chmod(0o600)
failed=False
for stage in s.STAGES:
 config=None
 if stage=='resolve':command=[p['tools']['lake']['path'],'env']
 elif stage.endswith('_build'):command=[p['tools']['lake']['path'],'build',p['challenge_module' if stage.startswith('challenge') else 'solution_module']]
 elif stage.endswith('_export'):command=[p['tools']['leanexport']['path'],p['challenge_module' if stage.startswith('challenge') else 'solution_module'],'--',*p['declarations']]
 else:
  config=pathlib.Path(p['temporary_directory'])/('config-'+stage)
  config.write_bytes(s.canonical({'use_stdin':False,'export_file_path':str(export),'permitted_axioms':p['permitted_axioms'],'unpermitted_axiom_hard_error':False,'num_threads':4,'nat_extension':True,'string_extension':True}));config.chmod(0o600)
  command=[p['tools']['nanoda_bin']['path'],str(config)] if stage=='nanoda' else [p['tools']['leanchecker']['path'],'--silent','--from-export',str(export)]
 args=s.sandbox_options(p,stage,config=str(config) if config else None,export=str(export) if config else None)+['--',*command]
 code=subprocess.run(['/opt/lean-eval/replay-measure-stock',*args]).returncode
 if code:failed=True
 if code and stage not in s.STAGES[5:]:break
raise SystemExit(1 if failed else 0)
'''))
        driver.chmod(0o700)
        lake = self.prefix / 'bin/lake'
        lake.write_text(f'#!{PYTHON}\nimport os,sys\n'
                        + f'\nif sys.argv[1:]==["test"]:os.execv({str(driver)!r},[{str(driver)!r}])\n')
        lake.chmod(0o700)
        self.policy['tools']['lake']['sha256'] = hashlib.sha256(lake.read_bytes()).hexdigest()
        self.policy['inherited_path'] = self.policy['resolved_path'] = str(Path(PYTHON).parent)
        # Nix has no /usr/bin/python3. This test-only interpreter spelling is
        # outside all original comparator/bwrap argv and stock binary bytes.
        wrapper = root / 'measurement-entry'
        original_entry = (ROOT / 'server/replay-image/replay-measure-stock').read_text().splitlines()
        wrapper.write_text(f'#!{PYTHON} -I\n' + '\n'.join(original_entry[1:]) + '\n')
        wrapper.chmod(0o700)
        self.files = supervisor.runtime_files()
        self.files.pop(ROOT / 'server/replay-image/replay-measure-stock')
        self.files[wrapper] = supervisor.INSTALL / 'replay-measure-stock'
        measurement = root / 'measurement.py'
        measurement.write_text((ROOT / 'scripts/stock_replay_measurement.py').read_text().replace(
            "REAL_BWRAP = '/usr/bin/bwrap'", f'REAL_BWRAP = {BWRAP!r}'))
        measurement.chmod(0o600)
        self.files.pop(ROOT / 'scripts/stock_replay_measurement.py')
        self.files[measurement] = supervisor.INSTALL / 'stock_replay_measurement.py'
        self.context = context(self.policy, files=self.files)


def context(policy, wall=20000, *, files=None, required=False):
    measurement = {'schema_version': 2, 'phase_semantics': 'stock_lake_solution_build_export_dual_kernel_v1',
        'wall_time_limit_ms': wall, 'memory_limit_bytes': 12 * 1024**3,
        'retired_instructions': {'required': required, 'perf_event': 'instructions:u'},
        'checker_sequence': ['bundled_nanoda', 'bundled_leanchecker']}
    policy['binding']['measurement_config_digest'] = supervisor.sha(b'lean-eval-replay-measurement-config-v1\0' + supervisor.stock.canonical(measurement))
    return {'schema_version': 1, 'binding': copy.deepcopy(policy['binding']), 'request_sha256': '7' * 64,
        'benchmark': {'repository': 'leanprover/lean-eval', 'commit': '8955681b9914d7ebc7d286414843021b7d6c9de1', 'toolchain': 'leanprover/lean4:v4.35.0'},
        'configuration_digest': '8' * 64, 'benchmark_tree': 'e1d860fb72dc13bf5d15ed0fb29c49043aa3440b',
        'workspace_manifest': supervisor.workspace_manifest(policy['project']), 'measurement_config': measurement,
        'driver_path': policy['inherited_path'],
        'policy_template_sha256': supervisor.policy_template_digest(policy),
        'lean_sha256': supervisor.file_hash(Path(policy['lean_prefix']) / 'bin/lean')['sha256'],
        'instrumentation': supervisor.instrumentation_commitments(supervisor.runtime_files() if files is None else files),
        'interpreter': {'path': str(Path(PYTHON).resolve()), 'sha256': supervisor.file_hash(Path(PYTHON).resolve())['sha256']},
        'file_count': 1, 'lines_of_code': 2}


@unittest.skipUnless(BWRAP, 'real existing bwrap required')
class StockSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_buffered_real_completion_is_judged_by_trusted_finish_not_delivery(self):
        for over_budget in (False, True):
            with self.subTest(over_budget=over_budget), tempfile.TemporaryDirectory(dir=self.root) as directory:
                f = Fixture(Path(directory))
                if over_budget:
                    kernel = f.prefix / 'bin/leanchecker'
                    kernel.write_text(f'#!{PYTHON}\nimport time;time.sleep(0.7)\n')
                    f.policy['tools']['leanchecker']['sha256'] = supervisor.file_hash(kernel)['sha256']
                locked_wall = 500 if over_budget else 1500
                f.context = context(f.policy, files=f.files, wall=locked_wall)
                original, calls = supervisor.Frames.read, [0]
                def descheduled(frames, deadline):
                    calls[0] += 1
                    if calls[0] == 2:
                        # Actual original agent runs on while the owner is not
                        # scheduled. Both real trusted frames coalesce in pipe.
                        time.sleep(2)
                    return original(frames, deadline)
                with mock.patch.object(supervisor.Frames, 'read', descheduled):
                    result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
                self.assertFalse(result['supervisor_failure'], result)
                self.assertEqual(result['driver']['termination'], {'kind': 'exited', 'code': 0})
                deadline = result['generation_started_ns'] + locked_wall * 1_000_000
                self.assertEqual(result['driver_finished']['finished_ns'] > deadline, over_budget)
                self.assertGreater(result['generation_stop']['cutoff_ns'], deadline)
                self.assertEqual(result['timed_out'], over_budget)
                self.assertEqual(result['verdict']['execution_outcome'], 'timed_out' if over_budget else 'completed')
                self.assertEqual(result['verdict']['checker_outcome'], None if over_budget else 'accepted')
                self.assertEqual(f.context['measurement_config']['wall_time_limit_ms'], locked_wall)

    def test_required_counters_invalid_and_interrupted_evidence_keep_closed_envelope(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory(dir=self.root) as directory:
                f = Fixture(Path(directory), sleep=5 if timeout else 0, driver_exit=1)
                f.context = context(f.policy, files=f.files, wall=300 if timeout else 20000, required=True)
                result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
                self.assertTrue(result['generation_stop']['stopped'])
                self.assertEqual(result['verdict']['failure_reason'], 'verdict_invalid')
                self.assertIsNone(result['verdict']['statistics'])

    def test_startup_budget_exhaustion_never_launches_or_fabricates_execution(self):
        f = Fixture(self.root)
        f.context = context(f.policy, files=f.files, wall=100)
        initialize = supervisor.stock.initialize
        def slow_storage(*args):
            time.sleep(0.3)
            return initialize(*args)
        with mock.patch.object(supervisor.stock, 'initialize', slow_storage):
            result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertTrue(result['supervisor_failure'])
        self.assertFalse(result['timed_out'])
        self.assertIsNone(result['driver_started'])
        self.assertIsNone(result['driver'])
        self.assertTrue(result['generation_stop']['stopped'])

    def test_solution_alternative_config_cache_and_source_grammar_are_bound(self):
        for name, contents in [('Solution.lean', 'changed trusted wrapper'), ('lakefile.lean', 'import Lean'),
                               ('.lake/stale.olean', 'stale'), ('Submission/escape.py', 'wrong Source grammar')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=self.root) as directory:
                f = Fixture(Path(directory))
                path = f.project / name
                path.parent.mkdir(exist_ok=True)
                path.write_text(contents)
                with mock.patch.object(supervisor.subprocess, 'Popen') as launch, self.assertRaises(supervisor.Invalid):
                    supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
                launch.assert_not_called()
        f = Fixture(self.root)
        original = f.context['workspace_manifest']
        (f.project / 'Submission.lean').write_text('-- another untrusted Source\n')
        changed = supervisor.workspace_manifest(f.project)
        self.assertEqual(changed['prepared_tree_sha256'], original['prepared_tree_sha256'])
        self.assertNotEqual(changed['source_sha256'], original['source_sha256'])
        with self.assertRaises(supervisor.Invalid):
            supervisor.validate_context(f.context, f.policy)

    def test_policy_lean_and_instrumentation_cannot_be_repaired_under_unchanged_context(self):
        for change in ('declarations', 'tool', 'lean', 'instrumentation'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(dir=self.root) as directory:
                f = Fixture(Path(directory))
                if change == 'declarations':
                    f.policy['declarations'] = ['other']
                else:
                    path = Path(f.policy['tools']['leanexport']['path']) if change == 'tool' else (
                        f.prefix / 'bin/lean' if change == 'lean' else f.root / 'measurement.py')
                    path.write_text(path.read_text() + '\n# changed\n')
                    if change == 'tool':
                        f.policy['tools']['leanexport']['sha256'] = supervisor.file_hash(path)['sha256']
                with mock.patch.object(supervisor.subprocess, 'Popen') as launch, self.assertRaises(supervisor.Invalid):
                    supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
                launch.assert_not_called()

    def test_consumed_workspace_is_refused_with_a_different_fresh_control(self):
        f = Fixture(self.root, driver_exit=1)
        first = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertTrue(first['generation_stop']['stopped'])
        other = self.root / 'another-control'
        other.mkdir(mode=0o700)
        with mock.patch.object(supervisor.subprocess, 'Popen') as launch, self.assertRaises(supervisor.Invalid):
            supervisor.supervise(f.policy, f.context, control=other, python=PYTHON, files=f.files)
        launch.assert_not_called()

    def test_unrepresentable_measurement_limits_refuse_without_rewriting_M(self):
        f = Fixture(self.root)
        unmodified_fixture = json.loads((ROOT / 'tests/fixtures/stock-replay-contract-v1.json').read_bytes())['request']['measurement_config']
        for wall in (supervisor.stock.MAX_WALL_MS, unmodified_fixture['wall_time_limit_ms']):
            with self.subTest(wall=wall):
                proposed = context(f.policy, files=f.files, wall=wall)
                with mock.patch.object(supervisor.subprocess, 'Popen') as launch, self.assertRaises(supervisor.Invalid):
                    supervisor.supervise(f.policy, proposed, control=f.control, python=PYTHON, files=f.files)
                launch.assert_not_called()
                self.assertEqual(proposed['measurement_config']['wall_time_limit_ms'], wall)
        proposed = context(f.policy, files=f.files, wall=supervisor.stock.MAX_WALL_MS - supervisor.CUTOFF_RESERVE_MS)
        supervisor.validate_context(proposed, f.policy)

    def namespace(self, body='import time;time.sleep(30)'):
        reader, writer = os.pipe2(os.O_CLOEXEC)
        command = [BWRAP, '--unshare-all', '--die-with-parent', '--new-session', '--ro-bind', '/', '/',
            '--proc', '/proc', '--dev', '/dev', '--info-fd', str(writer), '--', PYTHON, '-c', body]
        owner = supervisor.NamespaceOwner(command, (writer,))
        os.close(writer)
        try:
            self.assertTrue(select.select([reader], [], [], 5)[0])
            information = json.loads(os.read(reader, 4096))
            return owner, information
        finally:
            os.close(reader)

    def test_real_namespace_init_teardown_kills_new_session_descendants(self):
        # Outer host root is read-only; this helper reports through an owned
        # pipe inherited only by the namespace helper, never Source.
        reader, writer = os.pipe2(os.O_CLOEXEC)
        info_r, info_w = os.pipe2(os.O_CLOEXEC)
        body = 'import os,subprocess,time,json;child=subprocess.Popen([' + repr(PYTHON) + \
            ',"-c","import time;time.sleep(30)"],start_new_session=True);' + \
            f'os.write({writer},json.dumps({{"child":child.pid,"separate_session":os.getsid(child.pid)!=os.getsid(0)}}).encode());time.sleep(30)'
        owner = supervisor.NamespaceOwner([BWRAP, '--unshare-all', '--die-with-parent', '--new-session',
            '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev', '--info-fd', str(info_w), '--', PYTHON, '-c', body], (info_w, writer))
        os.close(info_w)
        os.close(writer)
        descendants = []
        try:
            self.assertTrue(select.select([info_r], [], [], 5)[0])
            information = json.loads(os.read(info_r, 4096))
            self.assertTrue(select.select([reader], [], [], 5)[0])
            reported = json.loads(os.read(reader, 4096))
            self.assertTrue(reported['separate_session'])
            owner.bind(information)
            for entry in Path('/proc').iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    if (entry / 'ns/pid').stat().st_ino != information['pid-namespace']:
                        continue
                    nested = next(line.split()[1:] for line in (entry / 'status').read_text().splitlines() if line.startswith('NSpid:'))
                    if int(nested[-1]) == reported['child']:
                        descendants.append(supervisor.pidfd_open(int(entry.name)))
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    continue
            self.assertEqual(len(descendants), 1)
            self.assertTrue(owner.stop()['stopped'])
            self.assertTrue(select.select(descendants, [], [], 5)[0])
        finally:
            owner.stop()
            owner.close()
            for descriptor in (reader, info_r, *descendants):
                os.close(descriptor)

    def test_forged_init_of_another_real_namespace_is_not_signaled(self):
        owned, _ = self.namespace()
        unrelated, information = self.namespace()
        try:
            with self.assertRaises(supervisor.Invalid):
                owned.bind(information)
            self.assertIsNone(owned.init_fd)
            self.assertFalse(owned.stop()['stopped'])
            self.assertFalse(select.select([unrelated.outer_fd], [], [], 0)[0])
            unrelated.bind(information)
            descriptor = unrelated.init_fd
            with self.assertRaises(supervisor.Invalid):
                unrelated.bind(information)
            self.assertEqual(unrelated.init_fd, descriptor)
        finally:
            owned.stop()
            owned.close()
            unrelated.stop()
            unrelated.close()

    def test_signal_interruption_waits_for_owned_teardown_and_restores_handlers(self):
        f = Fixture(self.root, sleep=30)
        original = supervisor.Frames.read
        def interrupt(frames, deadline):
            value = original(frames, deadline)
            if value['kind'] == 'driver_started':
                os.kill(os.getpid(), signal.SIGTERM)
            return value
        handler = signal.getsignal(signal.SIGTERM)
        with mock.patch.object(supervisor.Frames, 'read', interrupt):
            result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertTrue(result['supervisor_failure'])
        self.assertTrue(result['generation_stop']['stopped'])
        self.assertEqual(result['generation_stop']['binding'], result['binding'])
        self.assertEqual(signal.getsignal(signal.SIGTERM), handler)
        self.assertEqual(result['verdict']['failure_reason'], 'verdict_invalid')

    def test_inner_source_cannot_write_control_or_inherit_owner_pipes(self):
        f = Fixture(self.root)
        lake = f.prefix / 'bin/lake'
        lake.write_text(lake.read_text() + '''
if sys.argv[1:]==['env']:
 import errno,json,pathlib
 try:
  open('/run/lean-eval/stock-measurement.json','w');blocked=False
 except OSError as error:blocked=error.errno==errno.EROFS
 descriptors={}
 for name in os.listdir('/proc/self/fd'):
  try:descriptors[name]=os.readlink('/proc/self/fd/'+name)
  except FileNotFoundError:pass
 pathlib.Path('.lake/owner-probe.json').write_text(json.dumps({'blocked':blocked,'descriptors':descriptors}))
''')
        f.policy['tools']['lake']['sha256'] = hashlib.sha256(lake.read_bytes()).hexdigest()
        f.context = context(f.policy, files=f.files)
        before = set(os.listdir('/proc/self/fd'))
        result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        self.assertEqual(result['verdict']['checker_outcome'], 'accepted', result)
        probe = json.loads((f.project / '.lake/owner-probe.json').read_bytes())
        self.assertTrue(probe['blocked'])
        self.assertLessEqual(set(probe['descriptors']), {'0', '1', '2'})

    def test_owner_sigkill_kernel_guard_stops_namespace_without_fabricating_receipt(self):
        f = Fixture(self.root, sleep=30)
        driver = f.project / '.lake/build/bin/workspace_test'
        driver.write_text(driver.read_text().replace('time.sleep(30)',
            "pathlib.Path(p['temporary_directory']+'/driver-alive').touch();time.sleep(30)"))
        f.context = context(f.policy, files=f.files)
        input_file = self.root / 'owner-input.json'
        supervisor.private_write(input_file, {'policy': f.policy, 'context': f.context,
            'files': {str(source): str(target) for source, target in f.files.items()}})
        code = ('import sys,json,pathlib;sys.path.insert(0,' + repr(str(ROOT / 'scripts')) + ');'
            'import stock_replay_supervisor as s;s.stock.parent_death_guard();'
            'v=json.loads(pathlib.Path(' + repr(str(input_file)) + ').read_bytes());'
            's.supervise(v["policy"],v["context"],control=pathlib.Path(' + repr(str(f.control)) + '),'
            'python=' + repr(PYTHON) + ',files={pathlib.Path(a):pathlib.Path(b) for a,b in v["files"].items()})')
        process = subprocess.Popen([PYTHON, '-I', '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        descriptor = None
        try:
            deadline = time.monotonic() + 5
            while not (f.temporary / 'driver-alive').exists() and time.monotonic() < deadline:
                self.assertIsNone(process.poll())
                time.sleep(0.02)
            self.assertTrue((f.temporary / 'driver-alive').exists())
            owner = json.loads((f.control / 'stock-generation-owner.json').read_bytes())
            for entry in Path('/proc').iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    if (entry / 'ns/pid').stat().st_ino == owner['namespace']['namespace_inode']:
                        identity = supervisor.proc_identity(int(entry.name))
                        if identity['started_ticks'] == owner['namespace']['init_started_ticks']:
                            descriptor = supervisor.pidfd_open(int(entry.name))
                            break
                except (OSError, ValueError):
                    continue
            self.assertIsNotNone(descriptor)
            process.kill()
            process.wait(timeout=5)
            self.assertTrue(select.select([descriptor], [], [], 5)[0])
            self.assertFalse((f.control / 'stock-generation-stop.json').exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if descriptor is not None:
                os.close(descriptor)

    def test_real_per_problem_wait_and_complete_reducer_not_outer_status(self):
        f = Fixture(self.root)
        result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertFalse(result['supervisor_failure'], result)
        self.assertTrue(result['generation_stop']['stopped'])
        self.assertEqual(result['driver']['termination'], {'kind': 'exited', 'code': 0})
        self.assertEqual(result['driver']['passed'], ['fixture'])
        self.assertEqual(result['verdict']['checker_outcome'], 'accepted', result)
        trace = supervisor.stock.decode((f.control / 'stock-measurement.json').read_bytes())
        self.assertEqual([r['stage'] for r in trace['invocations']], list(supervisor.stock.STAGES))
        self.assertNotEqual(result['binding']['generation'], f.policy['binding']['generation'])
        self.assertGreaterEqual(result['driver_started']['spawned_ns'], result['generation_started_ns'])
        self.assertEqual(result['provider_destruction'], 'not_established')

    def test_actual_nonzero_without_kernel_evidence_is_invalid_not_rejected(self):
        f = Fixture(self.root, driver_exit=1)
        result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertEqual(result['driver']['termination'], {'kind': 'exited', 'code': 1}, result)
        self.assertEqual(result['verdict']['failure_reason'], 'verdict_invalid')
        self.assertIsNone(result['verdict']['checker_outcome'])
        self.assertTrue(result['generation_stop']['stopped'])

    def test_real_kernel_failure_remains_declined_when_native_kernel_succeeds(self):
        f = Fixture(self.root, codes={'nanoda_bin': 1})
        result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertEqual(result['driver']['termination'], {'kind': 'exited', 'code': 1})
        self.assertEqual(result['verdict']['checker_outcome'], 'declined', result)
        trace = json.loads((f.control / 'stock-measurement.json').read_bytes())
        self.assertEqual(len(trace['invocations']), 7)
        self.assertEqual(trace['invocations'][-1]['termination']['code'], 0)

    def test_timeout_in_actual_included_phase_uses_private_start_and_kernel_stop_cutoff(self):
        f = Fixture(self.root)
        lake = f.prefix / 'bin/lake'
        lake.write_text(lake.read_text() + '\nif sys.argv[1:]==["build","Solution"]:__import__("time").sleep(30)\n')
        f.policy['tools']['lake']['sha256'] = hashlib.sha256(lake.read_bytes()).hexdigest()
        f.context = context(f.policy, files=f.files, wall=1200)
        result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertTrue(result['timed_out'], result)
        trace = json.loads((f.control / 'stock-measurement.json').read_bytes())
        self.assertEqual(trace['active']['stage'], 'solution_build')
        policy = json.loads((f.control / 'stock-measurement-policy.json').read_bytes())
        started = supervisor.stock.read_start(policy, f.control / 'stock-measurement.clock', 'solution_build')
        self.assertLess(started['started_ns'], result['generation_stop']['cutoff_ns'])
        self.assertEqual(result['verdict']['execution_outcome'], 'timed_out', result)
        self.assertGreater(result['verdict']['statistics']['build_wall_time_ms'], 100)
        self.assertIsNone(result['driver'])

    def test_reused_control_and_temporary_control_alias_refuse_before_launch(self):
        f = Fixture(self.root, driver_exit=1)
        supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        with mock.patch.object(supervisor.subprocess, 'Popen') as launch, self.assertRaises((FileExistsError, supervisor.Invalid)):
            supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        launch.assert_not_called()
        with mock.patch.object(supervisor.subprocess, 'Popen') as launch, self.assertRaises(supervisor.Invalid):
            supervisor.supervise(f.policy, f.context, control=f.temporary, python=PYTHON, files=f.files)
        launch.assert_not_called()

    def test_timeout_requires_actual_generation_teardown_and_no_fabricated_driver_exit(self):
        f = Fixture(self.root, sleep=5)
        f.context = context(f.policy, files=f.files, wall=150)
        result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertTrue(result['timed_out'], result)
        self.assertTrue(result['generation_stop']['stopped'])
        self.assertIsNone(result['driver'])
        self.assertIsNotNone(result['driver_started'])
        self.assertEqual(result['verdict']['execution_outcome'], 'timed_out')

    def test_changed_driver_or_context_refuses_before_any_process(self):
        f = Fixture(self.root)
        (f.project / 'WorkspaceTest.lean').write_text('changed')
        with mock.patch.object(supervisor.subprocess, 'Popen') as launch, self.assertRaises(supervisor.Invalid):
            supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        launch.assert_not_called()

    def test_unrelated_reported_pid_never_becomes_a_signal_target(self):
        f = Fixture(self.root)
        with mock.patch.object(supervisor.NamespaceOwner, 'bind', side_effect=supervisor.Invalid('closed')):
            result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertTrue(result['supervisor_failure'])
        self.assertIsNone(result['driver_started'])
        self.assertFalse(result['generation_stop']['stopped'])

    @unittest.skipUnless((STOCK_CACHE / 'lean-4.35.0-linux/bin/lake').exists(), 'offline packaged stock 4.35.0 cache required')
    def test_actual_original_workspace_test_stock_lake_and_seven_original_phases(self):
        f = Fixture(self.root)
        prefix = STOCK_CACHE / 'lean-4.35.0-linux'
        for name in (*supervisor.TRUSTED_FILES, 'Solution.lean'):
            source = STOCK_CACHE / 'stock-positive' / name
            if source.is_file():
                (f.project / name).write_bytes(source.read_bytes())
        # Compile the unchanged trusted original driver before the generation.
        # This local Nix linker adapter affects priming only, never lake test
        # or any of the seven original comparator-owned child invocations.
        trusted_path = ':'.join([str(prefix / 'bin')] + [str(Path(shutil.which(name)).resolve().parent)
            for name in ('env', 'bash', 'which', 'git')])
        environment = {'PATH': trusted_path,
            'HOME': str(f.home), 'TMPDIR': str(f.temporary), 'LEAN_CC': str(STOCK_CACHE / 'clang-435stable'),
            'ELAN_TOOLCHAIN': 'leanprover/lean4:v4.35.0', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
        for name in ('NIX_LD', 'NIX_LD_LIBRARY_PATH', 'LD_LIBRARY_PATH'):
            if name in os.environ:
                environment[name] = os.environ[name]
        primed = subprocess.run([str(prefix / 'bin/lake'), 'build', 'workspace_test'], cwd=f.project,
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(primed.returncode, 0, primed.stdout.decode(errors='replace')[-4000:])
        self.assertNotIn(b'warning:', primed.stdout)
        configuration = json.loads((f.project / 'config.json').read_bytes())
        check_source = (prefix / 'src/lean/lake/Lake/CLI/Check.lean').read_text()
        self.assertEqual(hashlib.sha256(check_source.encode()).hexdigest(),
            '55daf430e5adea38453b21798cbdf4cd099f66ec969494022a566a8ae098c00b')
        primitive_source = check_source.split('def primitiveTargets')[1].split('def builtinTargets')[0]
        primitive_source = '\n'.join(line.split('--', 1)[0] for line in primitive_source.splitlines())
        declarations = ['Quot', 'Quot.mk', 'Quot.lift', 'Quot.ind'] + configuration['theorem_names'] + configuration['permitted_axioms'] + re.findall(r'``([A-Za-z_][A-Za-z0-9_.]*)', primitive_source)
        inherited = subprocess.check_output([str(prefix / 'bin/lake'), 'env', 'printenv', 'PATH'],
            cwd=f.project, env=environment, text=True).strip()
        inherited = ':'.join(str(Path(path).resolve()) for path in inherited.split(':')
            if path and Path(path).exists() and not Path(path).resolve().is_relative_to(f.project))
        f.policy.update(lean_prefix=str(prefix), lake_home=str(prefix), inherited_path=inherited,
            declarations=declarations, permitted_axioms=configuration['permitted_axioms'])
        for name in supervisor.stock.TOOL_NAMES:
            tool = prefix / 'bin' / name
            f.policy['tools'][name] = {'path': str(tool), 'sha256': hashlib.sha256(tool.read_bytes()).hexdigest()}
        resolve_environment = {**environment, 'PATH': inherited, 'HOME': '/tmp/home', 'LEAN_ABORT_ON_PANIC': '1'}
        for field, variable in (('resolved_path', 'PATH'), ('lean_path', 'LEAN_PATH')):
            f.policy[field] = subprocess.check_output([str(prefix / 'bin/lake'), 'env', 'printenv', variable],
                cwd=f.project, env=resolve_environment, text=True).strip()
        f.context = context(f.policy, files=f.files, wall=120000)
        f.context['driver_path'] = environment['PATH']
        # The host is Nix, the future image is conventional Linux. Supply only
        # its existing glibc loader at the conventional ABI location, outside
        # all original argv/tool bytes. No NIX_LD env is inserted into them.
        loader = Path('/nix/store/r3hvhzl5fxhrgn0rfy5g7rda17702pj4-glibc-2.44-57/lib/ld-linux-x86-64.so.2')
        f.files[loader] = Path('/lib64/ld-linux-x86-64.so.2')
        f.context['instrumentation'] = supervisor.instrumentation_commitments(f.files)
        command = supervisor.outer_command
        def conventional_abi(*args, **kwargs):
            result = command(*args, **kwargs)
            index = result.index(str(loader)) - 1
            result[index:index] = ['--tmpfs', '/lib64']
            return result
        with mock.patch.object(supervisor, 'outer_command', side_effect=conventional_abi):
            result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertIsNotNone(result['driver'], result)
        self.assertEqual(result['driver']['termination'], {'kind': 'exited', 'code': 0}, result)
        self.assertTrue(result['generation_stop']['stopped'], result)
        self.assertEqual(result['verdict']['checker_outcome'], 'accepted', result)
        trace = supervisor.stock.decode((f.control / 'stock-measurement.json').read_bytes())
        self.assertEqual([row['stage'] for row in trace['invocations']], list(supervisor.stock.STAGES))
        self.assertTrue(all(row['termination'] == {'kind': 'exited', 'code': 0} for row in trace['invocations']))
        self.assertEqual(result['runtime_qualification'], 'not_established')


class SupervisorSourceTests(unittest.TestCase):
    def test_info_pipe_accumulates_fragments_through_eof_and_refuses_truncation(self):
        for pieces, valid in (([b'{"child-', b'pid":42}'], True), ([b'{"child-'], False), ([b'x' * 4097], False)):
            reader, writer = os.pipe2(os.O_CLOEXEC)
            def send():
                try:
                    for piece in pieces:
                        os.write(writer, piece)
                        time.sleep(0.01)
                finally:
                    os.close(writer)
            thread = threading.Thread(target=send)
            thread.start()
            try:
                if valid:
                    self.assertEqual(supervisor.read_information(reader, time.monotonic() + 1), {'child-pid': 42})
                else:
                    with self.assertRaises(ValueError):
                        supervisor.read_information(reader, time.monotonic() + 1)
            finally:
                thread.join()
                os.close(reader)

    def test_original_driver_fixture_is_exact_and_no_active_reader_is_changed(self):
        self.assertEqual(hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(), SOURCE_SHA)
        self.assertNotIn('stock_replay_supervisor', (ROOT / 'scripts/evaluate_submission.py').read_text())
        self.assertNotIn('replay-supervise-stock', (ROOT / 'server/Dockerfile.replay').read_text())

    def test_request_factory_preserves_complete_contract_binding_and_refuses_changed_identity(self):
        from test_stock_replay_contract import blob, refresh
        fixture = json.loads((ROOT / 'tests/fixtures/stock-replay-contract-v1.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), real=False)
            lean = f.prefix / 'bin/lean'
            lean.write_text('#!/bin/sh\nexit 0\n')
            lean.chmod(0o700)
            request = fixture['request']
            files = {name: raw.encode() for name, raw in fixture['benchmark_files_utf8'].items()}
            files['templates/WorkspaceTest.lean'] = ORIGINAL.read_bytes()
            configuration = request['benchmark']['configuration_lock']
            configuration['workspace_driver'].update(blob(ORIGINAL.read_bytes()))
            configuration['generator_source_binding']['derived_output_manifest_sha256'] = supervisor.derived_manifest_digest(supervisor.workspace_manifest(f.project))
            for name, field in {'lake': 'lake', 'leanexport': 'leanexport', 'leanchecker': 'leanchecker', 'nanoda_bin': 'nanoda'}.items():
                request['execution_profile']['binary_sha256'][field] = f.policy['tools'][name]['sha256']
            request['execution_profile']['binary_sha256'].update(lean=supervisor.file_hash(lean)['sha256'],
                bubblewrap=f.policy['bubblewrap']['sha256'])
            request['measurement_config'] = f.context['measurement_config']
            refresh(request)
            for field in ('replay_task_id', 'attempt', 'execution_profile_digest', 'measurement_config_digest'):
                f.policy['binding'][field] = request[field]
            f.policy['binding'].update(problem_id=request['result']['problem_id'],
                statement_revision=request['result']['statement_revision'])
            authority = request['benchmark']['configuration_authority']
            arguments = {'benchmark_files': files, 'authority_raw': fixture['authority_raw_utf8'].encode(),
                'authority_commit': authority['commit'], 'authority_path': authority['path'],
                'driver_path': f.context['driver_path'], 'file_count': 1, 'lines_of_code': 2,
                'instrumentation': f.files, 'python': PYTHON}
            context_value = supervisor.context_from_request(request, f.policy, **arguments)
            self.assertEqual(context_value['binding'], f.policy['binding'])
            self.assertEqual(context_value['benchmark_tree'], configuration['benchmark_tree'])
            self.assertEqual(context_value['request_sha256'], supervisor.sha(supervisor.stock.canonical(request)))
            for field, replacement in (('problem_id', 'another'), ('statement_revision', 2),
                                       ('execution_profile_digest', '9' * 64), ('attempt', 2)):
                changed = copy.deepcopy(f.policy)
                changed['binding'][field] = replacement
                with self.subTest(field=field), self.assertRaises(supervisor.Invalid):
                    supervisor.context_from_request(request, changed, **arguments)
            lean.write_text('changed stock binary')
            with self.assertRaises(supervisor.Invalid):
                supervisor.context_from_request(request, f.policy, **arguments)


if __name__ == '__main__':
    unittest.main()
