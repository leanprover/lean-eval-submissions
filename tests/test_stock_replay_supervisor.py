from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shlex
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
from test_stock_replay_measurement import Context, BWRAP, PYTHON as HOST_PYTHON


def owned_python_launcher(directory, host_python, *, library_directory=None,
                          startup_program=None, startup_stderr=None):
    """Keep the test host's runtime layout behind an owned, pinned executable.

    Hosted setup-python installs need not meet the production image's strict
    mode/link policy. Do not chmod that shared install or relocate its ELF:
    libpython and the standard library must still resolve at their original
    paths. This synthetic test adapter establishes no runtime qualification.
    """
    # replay-measure uses /usr/bin/env python3 under this fixture's PATH.
    if library_directory is None:
        base = subprocess.check_output([host_python, '-I', '-c',
            'import sys;print(sys.base_prefix)'], text=True).strip()
        library_directory = Path(base) / 'lib'
    launcher = Path(directory) / 'python3'
    with launcher.open('x', encoding='utf-8') as output:
        output.write('#!/bin/sh\n')
        if startup_stderr is not None:
            # Capture the outer fake Lake/driver chain before Python's loader
            # or imports run. Original inner sandboxes cannot open this owned
            # temporary file; retain their existing stderr pipe in that case.
            target = shlex.quote(str(startup_stderr))
            output.write('if [ "$1" = ' + shlex.quote(str(startup_program)) + ' ]; then\n'
                         + '  if (umask 077; : >> ' + target + ') 2>/dev/null; then\n'
                         + '    exec 2>> ' + target + '\n  fi\nfi\n')
        # clearenv removes setup-python's loader configuration. Restore only
        # this trusted fixture runtime's lib directory, never inherited paths.
        output.write('LD_LIBRARY_PATH=' + shlex.quote(str(library_directory))
                     + '\nexport LD_LIBRARY_PATH\nexec '
                     + shlex.quote(str(Path(host_python).absolute())) + ' "$@"\n')
    launcher.chmod(0o700)
    return str(launcher)


_PYTHON_DIRECTORY = tempfile.TemporaryDirectory(prefix='lean-eval-stock-supervisor-python-')
PYTHON = owned_python_launcher(_PYTHON_DIRECTORY.name, HOST_PYTHON)


def with_host_python_runtime(command):
    """Restore only the test host installation masked by the outer image."""
    runtime = Path(subprocess.check_output([HOST_PYTHON, '-I', '-c',
        'import sys;print(sys.base_prefix)'], text=True).strip())
    def host_runtime(*args, **kwargs):
        result = command(*args, **kwargs)
        if runtime.is_relative_to('/opt'):
            index = result.index('--clearenv')
            result[index:index] = ['--ro-bind', str(runtime), str(runtime)]
        return result
    return host_runtime


ORIGINAL = ROOT / 'tests/fixtures/stock-original-workspace-test.lean'
SOURCE_SHA = '082806a7a6db89461febef4cf50c1d8c620945f96cd1a214a0b481a4cb6ee971'
STOCK_CACHE = Path('/tmp/lean-eval-435stable-stock-compat')


class Fixture(Context):
    """Synthetic programs for owner faults, never a qualified mathematical run."""
    def __init__(self, root, *, codes=None, driver_exit=None, sleep=0, real=True):
        super().__init__(root, real=real, codes=codes)
        # Every original inner comparator sandbox binds the stock prefix but
        # masks /tmp. Keep its synthetic tool shebangs inside that prefix.
        self.python = owned_python_launcher(self.prefix / 'bin', HOST_PYTHON,
            startup_program=self.prefix / 'bin/lake',
            startup_stderr=self.temporary / 'synthetic-startup.stderr')
        for tool in self.policy['tools'].values():
            path = Path(tool['path'])
            lines = path.read_text().splitlines(keepends=True)
            path.write_text(f'#!{self.python}\n' + ''.join(lines[1:]))
            tool['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.project / 'WorkspaceTest.lean').write_bytes(ORIGINAL.read_bytes())
        (self.project / 'Challenge.lean').write_text('-- synthetic owner fixture\n')
        (self.project / 'README.md').write_text('# Synthetic supervisor fault fixture\n')
        (self.project / 'holes.json').write_text('{}\n')
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
        driver.write_text(f'#!{self.python}\n' + f'''
import json,os,pathlib,subprocess,sys,time
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
 completed=subprocess.run(['/opt/lean-eval/replay-measure-stock',*args],stderr=subprocess.PIPE)
 code=completed.returncode
 if code:
  # Only this owned synthetic driver/tools produce this bounded diagnostic;
  # actual submitted Lean Source and environment variables are never recorded.
  diagnostic={'stage':stage,'returncode':code,'stderr':completed.stderr[:4096].decode('utf-8','replace')}
  (pathlib.Path(p['temporary_directory'])/'synthetic-phase-failure.json').write_text(json.dumps(diagnostic))
 if code:failed=True
 if code and stage not in s.STAGES[5:]:break
raise SystemExit(1 if failed else 0)
'''))
        driver.chmod(0o700)
        lake = self.prefix / 'bin/lake'
        lake.write_text(f'#!{self.python}\nimport os,sys\n'
                        + f'\nif sys.argv[1:]==["test"]:os.execv({str(driver)!r},[{str(driver)!r}])\n')
        lake.chmod(0o700)
        self.policy['tools']['lake']['sha256'] = hashlib.sha256(lake.read_bytes()).hexdigest()
        self.policy['inherited_path'] = self.policy['resolved_path'] = str(Path(self.python).parent)
        # Nix has no /usr/bin/python3. This test-only interpreter spelling is
        # outside all original comparator/bwrap argv and stock binary bytes.
        wrapper = root / 'measurement-entry'
        original_entry = (ROOT / 'server/replay-image/replay-measure-stock').read_text().splitlines()
        wrapper.write_text(f'#!{self.python} -I\n' + '\n'.join(original_entry[1:]) + '\n')
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

    def diagnostics(self):
        """Closed journal fields and controlled synthetic phase stderr only."""
        value = {}
        try:
            trace = json.loads((self.control / 'stock-measurement.json').read_bytes())
            value['journal'] = {'invalid': trace['invalid'],
                'active_stage': None if trace['active'] is None else trace['active']['stage'],
                'invocations': [{'stage': entry['stage'], 'termination': entry['termination']}
                                for entry in trace['invocations']]}
        except (OSError, ValueError, KeyError) as error:
            value['journal_error'] = type(error).__name__
        try:
            value['synthetic_phase'] = json.loads((self.temporary / 'synthetic-phase-failure.json').read_bytes())
        except (OSError, ValueError) as error:
            value['synthetic_phase_error'] = type(error).__name__
        try:
            with (self.temporary / 'synthetic-startup.stderr').open('rb') as handle:
                value['synthetic_startup_stderr'] = handle.read(4096).decode('utf-8', 'replace')
        except OSError as error:
            value['synthetic_startup_error'] = type(error).__name__
        return value


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
        # The production outer image masks /opt. Hosted setup-python's test
        # runtime lives there, unlike the production image's /usr/bin/python3.
        # Restore only that existing trusted host installation read-only;
        # original inner comparator options and production code stay exact.
        patch = mock.patch.object(supervisor, 'outer_command',
            side_effect=with_host_python_runtime(supervisor.outer_command))
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_share_packages_layouts_are_committed_and_validated(self):
        from evaluate_submission import _share_packages
        for lean_pool in (False, True):
            with self.subTest(lean_pool=lean_pool), tempfile.TemporaryDirectory(dir=self.root) as directory:
                root = Path(directory)
                f = Fixture(root)
                shared = root / 'shared-packages'
                package = shared / ('lean-pool' if lean_pool else 'mathlib')
                package.mkdir(parents=True)
                (package / 'lakefile.toml').write_text('name = "lean-pool"\n')
                (package / 'lake-manifest.json').write_text('{}\n')
                (package / 'Library.lean').write_text('-- trusted pinned dependency\n')
                self.assertIsNone(_share_packages(f.project, shared))
                f.context = context(f.policy, files=f.files)
                supervisor.validate_context(f.context, f.policy, control=f.control)
                alias = f.project / '.lake/packages'
                self.assertEqual(alias.is_symlink(), not lean_pool)
                if lean_pool:
                    self.assertTrue((alias / 'lean-pool/lakefile.toml').is_symlink())
                    self.assertFalse((alias / 'lean-pool/.lake').is_symlink())
                    self.assertTrue((f.project / '.lake/package-overrides.json').is_file())
                (package / 'Library.lean').write_text('-- changed pinned dependency\n')
                with self.assertRaises(supervisor.Invalid):
                    supervisor.validate_context(f.context, f.policy, control=f.control)

    def test_real_hosted_style_python_runtime_survives_masked_opt_with_owned_launcher(self):
        # Use this session's ordinary installation, including libpython and
        # extension-module dependencies, at a hosted-style /opt location. No
        # host filesystem installation or original comparator argv is changed.
        base = Path(subprocess.check_output([sys.executable, '-I', '-c',
            'import sys;print(sys.base_prefix)'], text=True).strip()).resolve()
        executable = Path(sys.executable).resolve().relative_to(base)
        destination = Path('/opt/lean-eval-hosted-python-fixture')
        launcher = owned_python_launcher(self.root, destination / executable,
                                         library_directory=destination / 'lib')
        code = 'import json,ssl,sys;print(json.dumps([sys.version,ssl.OPENSSL_VERSION,sys.flags.isolated]))'
        command = [BWRAP, '--unshare-all', '--die-with-parent', '--new-session',
            '--ro-bind', '/', '/', '--tmpfs', '/opt', '--tmpfs', '/home',
            '--ro-bind', str(base), str(destination), '--dev', '/dev', '--proc', '/proc',
            '--clearenv', '--', launcher, '-I', '-c', code]
        expected = subprocess.check_output([sys.executable, '-I', '-c', code])
        self.assertEqual(subprocess.check_output(command), expected)

    def test_dependency_aliases_refuse_source_project_control_temporary_and_cycles(self):
        for destination in ('source', 'project', 'control', 'temporary', 'cycle', 'top_file'):
            with self.subTest(destination=destination), tempfile.TemporaryDirectory(dir=self.root) as directory:
                root = Path(directory)
                f = Fixture(root)
                alias = f.project / '.lake/packages'
                if destination == 'source':
                    (f.project / 'Submission.lean').unlink()
                    alias = f.project / 'Submission.lean'
                    target = f.prefix / 'bin/lean'
                elif destination == 'project':
                    target = f.project
                elif destination == 'control':
                    target = f.control
                elif destination == 'temporary':
                    target = f.temporary
                elif destination == 'top_file':
                    target = f.prefix / 'bin/lean'
                else:
                    target = root / 'shared-packages'
                    target.mkdir()
                    (target / 'cycle').symlink_to(target, target_is_directory=True)
                alias.symlink_to(target, target_is_directory=target.is_dir())
                with self.assertRaises(supervisor.Invalid):
                    supervisor.workspace_manifest(f.project, forbidden_roots=(f.temporary, f.control))

    def test_buffered_real_completion_is_judged_by_trusted_finish_not_delivery(self):
        for over_budget in (False, True):
            with self.subTest(over_budget=over_budget), tempfile.TemporaryDirectory(dir=self.root) as directory:
                f = Fixture(Path(directory))
                if over_budget:
                    kernel = f.prefix / 'bin/leanchecker'
                    kernel.write_text(f'#!{f.python}\nimport time;time.sleep(0.7)\n')
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
                self.assertEqual(result['driver']['termination'], {'kind': 'exited', 'code': 0}, (result, f.diagnostics()))
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
            information = supervisor.read_information(reader, time.monotonic() + 5)
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
            information = supervisor.read_information(info_r, time.monotonic() + 5)
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
        self.assertEqual(result['verdict']['checker_outcome'], 'accepted', (result, f.diagnostics()))
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
            'sys.path.insert(0,' + repr(str(ROOT / 'tests')) + ');'
            'from test_stock_replay_supervisor import with_host_python_runtime;'
            's.outer_command=with_host_python_runtime(s.outer_command);'
            'v=json.loads(pathlib.Path(' + repr(str(input_file)) + ').read_bytes());'
            's.supervise(v["policy"],v["context"],control=pathlib.Path(' + repr(str(f.control)) + '),'
            'python=' + repr(PYTHON) + ',files={pathlib.Path(a):pathlib.Path(b) for a,b in v["files"].items()})')
        process = subprocess.Popen([PYTHON, '-I', '-c', code],
            env={**os.environ, 'TMPDIR': str(self.root)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        self.assertEqual(result['driver']['termination'], {'kind': 'exited', 'code': 0}, (result, f.diagnostics()))
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

    def test_synthetic_startup_failure_is_captured_before_python_imports(self):
        f = Fixture(self.root)
        launcher = Path(f.python)
        original = shlex.quote(str(Path(HOST_PYTHON).absolute())) + ' "$@"'
        launcher.write_text(launcher.read_text().replace(original, '/fixture-missing-python "$@"'))
        result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertEqual(result['driver']['termination'], {'kind': 'exited', 'code': 127})
        self.assertEqual(result['verdict']['failure_reason'], 'verdict_invalid')
        diagnostic = f.diagnostics()
        self.assertEqual(diagnostic['journal']['invocations'], [])
        self.assertIn('/fixture-missing-python', diagnostic['synthetic_startup_stderr'])
        with (f.temporary / 'synthetic-startup.stderr').open('ab') as handle:
            handle.write(b'x' * 8192)
        self.assertEqual(len(f.diagnostics()['synthetic_startup_stderr']), 4096)

    def test_real_kernel_failure_remains_declined_when_native_kernel_succeeds(self):
        f = Fixture(self.root, codes={'nanoda_bin': 1})
        result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertEqual(result['driver']['termination'], {'kind': 'exited', 'code': 1})
        self.assertEqual(result['verdict']['checker_outcome'], 'declined', (result, f.diagnostics()))
        trace = json.loads((f.control / 'stock-measurement.json').read_bytes())
        self.assertEqual(len(trace['invocations']), 7)
        self.assertEqual(trace['invocations'][-1]['termination']['code'], 0)
        diagnostic = f.diagnostics()
        self.assertEqual(diagnostic['synthetic_phase'], {'stage': 'nanoda', 'returncode': 1, 'stderr': ''})
        self.assertEqual(diagnostic['journal']['invocations'][-1]['stage'], 'native_kernel')

    def test_timeout_in_actual_included_phase_uses_private_start_and_kernel_stop_cutoff(self):
        f = Fixture(self.root)
        lake = f.prefix / 'bin/lake'
        lake.write_text(lake.read_text() + '\nif sys.argv[1:]==["build","Solution"]:__import__("time").sleep(30)\n')
        f.policy['tools']['lake']['sha256'] = hashlib.sha256(lake.read_bytes()).hexdigest()
        f.context = context(f.policy, files=f.files, wall=1200)
        result = supervisor.supervise(f.policy, f.context, control=f.control, python=PYTHON, files=f.files)
        self.assertTrue(result['timed_out'], (result, f.diagnostics()))
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
    def test_owned_interpreter_launcher_preserves_isolation_argv_and_runtime_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Exercise shell quoting with a host executable spelling that
            # contains whitespace, a quote, and literal substitution syntax.
            host = root / "host python ' $(literal)"
            host.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
            host.chmod(0o700)
            launcher = owned_python_launcher(root, host)
            before = (host.stat().st_mode, host.stat().st_nlink, host.read_bytes())
            command = ['-I', '-c', 'import json,ssl,sys;print(json.dumps([sys.executable,sys.prefix,sys.flags.isolated,ssl.OPENSSL_VERSION,sys.argv[1:]]))', '', 'argument with spaces']
            expected = subprocess.check_output([sys.executable, *command])
            self.assertEqual(subprocess.check_output([launcher, *command]), expected)
            self.assertEqual(supervisor.file_hash(Path(launcher))['mode'], 0o700)
            self.assertEqual(Path(launcher).stat().st_nlink, 1)
            self.assertEqual((host.stat().st_mode, host.stat().st_nlink, host.read_bytes()), before)
            base = subprocess.check_output([sys.executable, '-I', '-c',
                'import sys;print(sys.base_prefix)'], text=True).strip()
            inherited = {**os.environ, 'LD_LIBRARY_PATH': str(root / 'untrusted-library')}
            observed = subprocess.check_output([launcher, '-I', '-c',
                'import os;print(os.environ["LD_LIBRARY_PATH"])'], env=inherited, text=True).strip()
            self.assertEqual(observed, str(Path(base) / 'lib'))

    def test_unsafe_interpreter_launchers_refuse_before_process_creation(self):
        for case in ('group_writable', 'world_writable', 'setuid', 'setgid', 'hardlinked', 'symlink'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                f = Fixture(root, real=False)
                launcher = root / 'unsafe-python'
                launcher.write_bytes(Path(PYTHON).read_bytes())
                launcher.chmod({'group_writable': 0o720, 'world_writable': 0o702,
                                'setuid': 0o4700, 'setgid': 0o2700}.get(case, 0o700))
                if case == 'hardlinked':
                    os.link(launcher, root / 'another-link')
                elif case == 'symlink':
                    target = root / 'launcher-target'
                    launcher.rename(target)
                    launcher.symlink_to(target)
                f.context['interpreter'] = {'path': str(launcher),
                    'sha256': hashlib.sha256(Path(PYTHON).read_bytes()).hexdigest()}
                with mock.patch.object(supervisor.subprocess, 'Popen') as launch:
                    with self.assertRaises(supervisor.Invalid):
                        supervisor.supervise(f.policy, f.context, control=f.control,
                                             python=str(launcher), files=f.files)
                    launch.assert_not_called()

    def test_actual_original_generated_filesets_are_retained_and_bound(self):
        fixture = json.loads((ROOT / 'tests/fixtures/stock-original-workspace-filesets-v1.json').read_bytes())
        self.assertEqual(fixture['commit'], '8955681b9914d7ebc7d286414843021b7d6c9de1')
        def tree_sha(entries):
            children = {}
            for path, mode, blob in entries:
                first, separator, remaining = path.partition('/')
                if separator:
                    children.setdefault(first, []).append((remaining, mode, blob))
                else:
                    children[first] = (mode, blob)
            records = []
            for name, value in children.items():
                directory = isinstance(value, list)
                mode, blob = ('40000', tree_sha(value)) if directory else value
                records.append(((name + ('/' if directory else '')).encode(),
                    mode.encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(blob)))
            raw = b''.join(value for _, value in sorted(records))
            return hashlib.sha1(b'tree ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        for row in fixture['workspaces']:
            with self.subTest(problem=row['problem_id']), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory), real=False)
                entries = []
                for item in row['files']:
                    raw = item['utf8'].encode()
                    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
                    self.assertEqual(blob, item['git_blob'])
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), item['sha256'])
                    entries.append((item['path'], item['mode'], blob))
                    path = f.project / item['path']
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                    path.chmod(int(item['mode'][-3:], 8))
                self.assertEqual(tree_sha(entries), row['git_tree'])
                manifest = supervisor.workspace_manifest(f.project)
                expected_deps = row['problem_id'] == 'H1_not_closedComplemented'
                self.assertEqual('ChallengeDeps.lean' in manifest['files'], expected_deps)
                for name in ('README.md', 'holes.json') + (('ChallengeDeps.lean',) if expected_deps else ()):
                    original = (f.project / name).read_bytes()
                    self.assertEqual(manifest['files'][name]['sha256'], hashlib.sha256(original).hexdigest())
                    (f.project / name).write_bytes(original + b'\n')
                    self.assertNotEqual(supervisor.workspace_manifest(f.project), manifest)
                    (f.project / name).write_bytes(original)
                self.assertEqual(supervisor.workspace_manifest(f.project), manifest)
                for unknown in ('lakefile.lean', 'extra-config.toml'):
                    path = f.project / unknown
                    path.write_text('unexpected executable configuration\n')
                    with self.assertRaises(supervisor.Invalid):
                        supervisor.workspace_manifest(f.project)
                    path.unlink()

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
