from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import stock_replay_measurement as stock

PYTHON = str(Path('/run/current-system/sw/bin/python3').resolve()) if Path('/run/current-system/sw/bin/python3').exists() else sys.executable
BWRAP = str(Path(shutil.which('bwrap')).resolve()) if shutil.which('bwrap') else None


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Context:
    """Synthetic tools and metadata; not an execution profile or qualification."""
    def __init__(self, root, *, real=False, codes=None, extra=''):
        self.root = root
        self.prefix = root / 'toolchain'
        self.project = root / 'workspaces' / 'fixture'
        self.home = root / 'lake-home'
        self.temporary = root / 'trusted-temporary'
        self.control = self.prefix / 'private-control'
        for path in (self.prefix / 'bin', self.project / '.lake', self.home, self.temporary, self.control):
            path.mkdir(parents=True, mode=0o700)
        self.journal = self.control / 'journal.json'
        self.clock = self.control / 'clock.bin'
        self.fake = root / 'fake-bwrap'
        self.fake.write_text(f'#!{PYTHON}\nimport os,sys\n' + extra + '''
arity={'--ro-bind':2,'--bind':2,'--setenv':2,'--tmpfs':1,'--dir':1,'--dev':1,'--proc':1,'--chdir':1,'--clearenv':0,'--unshare-all':0,'--die-with-parent':0,'--new-session':0,'--share-net':0}
a=sys.argv[1:];i=0
while a[i]!='--':i+=1+arity[a[i]]
os.execv(a[i+1],a[i+1:])
''')
        self.fake.chmod(0o700)
        self.real = BWRAP if real else str(self.fake)
        self.export = self.temporary / 'solution-export'
        self.export.write_text('trusted synthetic export')
        self.export.chmod(0o600)
        codes = codes or {}
        tools = {}
        for name in stock.TOOL_NAMES:
            path = self.prefix / 'bin' / name
            code = codes.get(name, 0)
            source = (f'#!{PYTHON}\nimport os,sys\n'
                      + (f'os.kill(os.getpid(),{-code})\n' if code < 0 else f'raise SystemExit({code})\n'))
            path.write_text(source)
            path.chmod(0o700)
            tools[name] = {'path': str(path), 'sha256': digest(path)}
        self.policy = {'schema_version': 1, 'binding': {
            'replay_task_id': 'rt1_' + '1' * 64, 'attempt': 1,
            'execution_profile_digest': '2' * 64, 'measurement_config_digest': '3' * 64,
            'generation': '4' * 64, 'started_ms': time.monotonic_ns() // 1_000_000,
            'problem_id': 'fixture', 'statement_revision': 1},
            'project': str(self.project), 'lean_prefix': str(self.prefix), 'lake_home': str(self.home),
            'temporary_directory': str(self.temporary), 'inherited_path': os.defpath,
            'resolved_path': os.defpath, 'lean_path': str(self.project / '.lake'),
            'challenge_module': 'Challenge', 'solution_module': 'Solution',
            'declarations': ['target', 'Nat'], 'permitted_axioms': [], 'tools': tools,
            'bubblewrap': {'path': self.real, 'sha256': digest(self.real)}}

    def initialize(self):
        stock.initialize(self.policy, self.journal, self.clock)

    def trace(self):
        return stock.decode(self.journal.read_bytes())

    def arguments(self, stage):
        tools = self.policy['tools']
        config = export = None
        if stage == 'resolve':
            command = [tools['lake']['path'], 'env']
        elif stage.endswith('_build'):
            command = [tools['lake']['path'], 'build', self.policy['challenge_module' if stage.startswith('challenge') else 'solution_module']]
        elif stage.endswith('_export'):
            command = [tools['leanexport']['path'], self.policy['challenge_module' if stage.startswith('challenge') else 'solution_module'], '--', *self.policy['declarations']]
        else:
            config = self.temporary / ('config-' + stage)
            export = self.export
            config.write_bytes(stock.canonical({'use_stdin': False, 'export_file_path': str(export),
                'permitted_axioms': [], 'unpermitted_axiom_hard_error': False,
                'num_threads': 4, 'nat_extension': True, 'string_extension': True}))
            config.chmod(0o600)
            command = [tools['nanoda_bin']['path'], str(config)] if stage == 'nanoda' else [tools['leanchecker']['path'], '--silent', '--from-export', str(export)]
        return stock.sandbox_options(self.policy, stage, config=str(config) if config else None,
                                     export=str(export) if export else None) + ['--', *command]

    def run(self, stage, *, measure=None):
        with contextlib.chdir(self.project):
            return stock.run(self.policy, self.arguments(stage), journal_path=self.journal,
                             clock_path=self.clock, real_bwrap=self.real, measure=measure)

    def driver(self, code=0, *, accepted=True):
        return {'binding': copy.deepcopy(self.policy['binding']), 'termination': stock.termination(code),
                'passed': ['fixture'] if accepted else [],
                'statement_revisions': {'fixture': 1} if accepted else {}}


def synthetic_trace(policy, count=7):
    trace = {'schema_version': 2, 'binding': copy.deepcopy(policy['binding']),
             'policy_sha256': stock.policy_digest(policy), 'invocations': [],
             'active': None, 'invalid': False, 'kernel_export': None}
    for index, stage in enumerate(stock.STAGES[:count]):
        start = policy['binding']['started_ms'] + 10 + index * 10
        trace['invocations'].append({'stage': stage, 'started_ms': start, 'finished_ms': start + 5,
            'wall_time_ms': 3, 'measurement_started_ns': (start + 1) * 1_000_000 if stock.BUCKET[stage] else None,
            'counter': {'status': 'measured', 'value': 10} if stock.BUCKET[stage] else None,
            'termination': {'kind': 'exited', 'code': 0}})
    if count > 5:
        trace['kernel_export'] = {'device': 1, 'inode': 2, 'size': 3, 'modified_ns': 4, 'path_sha256': '5' * 64}
    return trace


class StockMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.context = Context(self.root)
        self.context.initialize()

    def tearDown(self):
        self.directory.cleanup()

    def verdict(self, trace=None, driver=None, **kwargs):
        return stock.reduce_trace(self.context.policy, trace or self.context.trace(),
            driver or self.context.driver(), file_count=1, lines_of_code=2, **kwargs)

    def test_all_seven_original_boundaries_and_dual_kernel_acceptance(self):
        c = self.context
        measure = stock.load_measure()
        with mock.patch.object(measure, 'run_measured', wraps=measure.run_measured) as run:
            for stage in stock.STAGES:
                self.assertEqual(c.run(stage, measure=measure), 0)
            self.assertEqual(run.call_count, 4)
        trace = c.trace()
        stock.validate_journal(trace, c.policy)
        self.assertEqual([r['stage'] for r in trace['invocations']], list(stock.STAGES))
        self.assertEqual(self.verdict(trace)['checker_outcome'], 'accepted')
        self.assertEqual(stock.read_start(c.policy, c.clock, 'native_kernel')['generation'], '4' * 64)

    def test_independently_captured_original_stock_argv_and_configs_match(self):
        c = self.context
        fixture = json.loads((ROOT / 'tests/fixtures/stock-lake-original-bwrap-v1.json').read_text())
        self.assertEqual(fixture['provenance']['lean_commit'], 'eb6cafb7290e8bd5f2937522d8d804f58f02bafc')
        c.policy['lake_home'] = str(c.prefix)
        c.policy['declarations'] = fixture['declarations']
        c.policy['permitted_axioms'] = fixture['permitted_axioms']
        stock.validate_policy(c.policy)
        previous = None
        for row in fixture['invocations']:
            stage = row['stage']
            config = c.temporary / ('captured-' + stage)
            replace = {'$PROJECT': str(c.project), '$LEAN_PREFIX': str(c.prefix),
                       '$TEMP': str(c.temporary), '$CONFIG': str(config), '$EXPORT': str(c.export),
                       '$INHERITED_PATH': c.policy['inherited_path'], '$RESOLVED_PATH': c.policy['resolved_path'],
                       '$LEAN_PATH': c.policy['lean_path']}
            def value(text):
                for key, target in replace.items(): text = text.replace(key, target)
                return text
            if stage in stock.STAGES[5:]:
                contents = copy.deepcopy(fixture['kernel_configs'][stock.STAGES.index(stage) - 5])
                contents['export_file_path'] = str(c.export)
                config.write_bytes(stock.canonical(contents)); config.chmod(0o600)
            arguments = [value(a) for a in row['arguments']]
            # Deliberately do not construct these arguments with sandbox_options:
            # the frozen fixture came from the independent original stock driver.
            previous = stock.match_invocation(c.policy, arguments, stage, previous)

    def test_first_nonzero_kernel_cannot_be_hidden_by_native_success(self):
        trace = synthetic_trace(self.context.policy)
        trace['invocations'][5]['termination']['code'] = 1
        result = self.verdict(trace, self.context.driver(1, accepted=False))
        self.assertEqual((result['execution_outcome'], result['checker_outcome']), ('completed', 'declined'))
        self.assertEqual(result['statistics']['checker_wall_time_ms'], 6)

    def test_native_failure_and_both_failures_are_declined(self):
        for indices in ((6,), (5, 6)):
            trace = synthetic_trace(self.context.policy)
            for index in indices:
                trace['invocations'][index]['termination']['code'] = 7
            self.assertEqual(self.verdict(trace, self.context.driver(1, accepted=False))['checker_outcome'], 'declined')

    def test_missing_kernel_and_ambiguous_precheck_are_invalid_not_rejected(self):
        for count in (4, 5, 6):
            result = self.verdict(synthetic_trace(self.context.policy, count), self.context.driver(1, accepted=False))
            self.assertEqual(result['execution_outcome'], 'failed')
            self.assertEqual(result['failure_reason'], 'verdict_invalid')
            self.assertIsNone(result['checker_outcome'])

    def test_complete_kernels_with_driver_failure_or_stale_pass_are_invalid(self):
        trace = synthetic_trace(self.context.policy)
        self.assertEqual(self.verdict(trace, self.context.driver(1, accepted=False))['failure_reason'], 'verdict_invalid')
        trace['invocations'][5]['termination']['code'] = 1
        self.assertEqual(self.verdict(trace)['failure_reason'], 'verdict_invalid')

    def test_per_problem_driver_binding_and_revision_are_required(self):
        for field, value in (('attempt', 2), ('generation', 'f' * 64), ('problem_id', 'other')):
            driver = self.context.driver()
            driver['binding'][field] = value
            self.assertEqual(self.verdict(synthetic_trace(self.context.policy), driver)['failure_reason'], 'verdict_invalid')
        driver = self.context.driver()
        driver['statement_revisions']['fixture'] = True
        self.assertEqual(self.verdict(synthetic_trace(self.context.policy), driver)['failure_reason'], 'verdict_invalid')

    def test_phase_wall_uses_measured_duration_not_journal_overhead(self):
        result = self.verdict(synthetic_trace(self.context.policy))
        self.assertEqual(result['statistics']['build_wall_time_ms'], 6)
        self.assertEqual(result['statistics']['checker_wall_time_ms'], 6)
        self.assertEqual(result['statistics']['build_retired_instructions'], {'status': 'measured', 'value': 20})

    def test_unavailable_counter_remains_unavailable_not_zero(self):
        trace = synthetic_trace(self.context.policy)
        trace['invocations'][3]['counter'] = {'status': 'unavailable', 'reason': 'counter_permission_denied'}
        self.assertEqual(self.verdict(trace)['statistics']['build_retired_instructions'], trace['invocations'][3]['counter'])

    def test_timeout_uses_actual_measurement_start_and_stopped_generation(self):
        c = self.context
        trace = synthetic_trace(c.policy, 3)
        intent_ms = trace['invocations'][-1]['finished_ms'] + 1
        trace['active'] = {'stage': 'solution_build', 'started_ms': intent_ms}
        measured_ns = (intent_ms + 20) * 1_000_000 + 900_000
        stock.publish_start(c.policy, c.clock, 'solution_build', measured_ns)
        started = stock.read_start(c.policy, c.clock, 'solution_build')
        cutoff = measured_ns + 10_900_000
        stopped = {'binding': c.policy['binding'], 'cutoff_ns': cutoff, 'stopped': True}
        result = stock.reduce_trace(c.policy, trace, None, file_count=1, lines_of_code=2,
                                   timeout=stopped, measurement_start=started)
        self.assertEqual(result['execution_outcome'], 'timed_out')
        self.assertEqual(result['statistics']['build_wall_time_ms'], 10)
        self.assertEqual(result['statistics']['build_retired_instructions'], {'status': 'unavailable', 'reason': 'counter_not_reported'})
        for alteration in ({'stopped': False}, {'binding': {**c.policy['binding'], 'attempt': 2}}):
            self.assertEqual(stock.reduce_trace(c.policy, trace, None, file_count=1, lines_of_code=2,
                timeout={**stopped, **alteration}, measurement_start=started)['failure_reason'], 'verdict_invalid')

    def test_intent_only_or_wrong_generation_is_not_a_measurement_start(self):
        c = self.context
        trace = synthetic_trace(c.policy, 3)
        at = trace['invocations'][-1]['finished_ms'] + 1
        trace['active'] = {'stage': 'solution_build', 'started_ms': at}
        stopped = {'binding': c.policy['binding'], 'cutoff_ns': (at + 10) * 1_000_000, 'stopped': True}
        result = stock.reduce_trace(c.policy, trace, None, file_count=1, lines_of_code=2, timeout=stopped)
        self.assertEqual(result['failure_reason'], 'verdict_invalid')
        start = {'policy_sha256': stock.policy_digest(c.policy), 'generation': 'f' * 64, 'stage': 'solution_build', 'started_ns': at * 1_000_000}
        self.assertEqual(stock.reduce_trace(c.policy, trace, None, file_count=1, lines_of_code=2,
            timeout=stopped, measurement_start=start)['failure_reason'], 'verdict_invalid')

    def test_timeout_during_resolution_charges_neither_included_phase(self):
        c = self.context
        trace = c.trace()
        trace['active'] = {'stage': 'resolve', 'started_ms': c.policy['binding']['started_ms']}
        stopped = {'binding': c.policy['binding'], 'cutoff_ns': (trace['active']['started_ms'] + 30) * 1_000_000, 'stopped': True}
        result = stock.reduce_trace(c.policy, trace, None, file_count=1, lines_of_code=2, timeout=stopped)
        self.assertEqual(result['execution_outcome'], 'timed_out')
        self.assertEqual(result['statistics']['build_wall_time_ms'], 0)
        self.assertEqual(result['statistics']['checker_wall_time_ms'], 0)
        self.assertEqual(result['statistics']['checker_retired_instructions']['status'], 'unavailable')

    def test_signal_is_execution_crash_not_mathematical_decline(self):
        trace = synthetic_trace(self.context.policy)
        trace['invocations'][5]['termination'] = {'kind': 'signaled', 'signal': 15}
        result = self.verdict(trace, self.context.driver(1, accepted=False))
        self.assertEqual(result['execution_outcome'], 'crashed')
        self.assertIsNone(result['checker_outcome'])

    def test_option_value_equal_to_separator_is_not_a_command_boundary(self):
        options, command = stock.split_bwrap(['--setenv', 'PATH', '--', '--unshare-all', '--', '/trusted/lake', 'env'])
        self.assertEqual(command, ['/trusted/lake', 'env'])
        self.assertEqual(options, ['--setenv', 'PATH', '--', '--unshare-all'])

    def test_trusted_unicode_names_and_literal_argument_values_are_preserved(self):
        c = self.context
        c.policy['solution_module'] = 'Δήλωση'
        c.policy['declarations'] = ['τεκμήριο', '«exact name\nwith newline»']
        c.policy['inherited_path'] = '--'
        stock.validate_policy(c.policy)
        arguments = c.arguments('solution_build')
        stock.match_invocation(c.policy, arguments, 'solution_build', None)
        self.assertEqual(stock.split_bwrap(arguments)[1][-1], 'Δήλωση')
        arguments = c.arguments('solution_export')
        stock.match_invocation(c.policy, arguments, 'solution_export', None)
        self.assertEqual(stock.split_bwrap(arguments)[1][-2:], c.policy['declarations'])
        c.policy['binding']['problem_id'] = 'Original-Problem_7'
        c.policy['declarations'].append(c.policy['declarations'][0])
        stock.validate_policy(c.policy)  # same problem alphabet as execution-request1

    def test_unknown_options_network_mount_or_module_substitution_refuse(self):
        c = self.context
        for edit in ('network', 'mount', 'module', 'extra', 'unknown'):
            arguments = c.arguments('solution_build')
            if edit == 'network': arguments.insert(arguments.index('--chdir'), '--share-net')
            elif edit == 'mount': arguments[arguments.index('--bind') + 1] = '/'
            elif edit == 'module': arguments[-1] = 'Challenge'
            elif edit == 'extra': arguments.append('--extra')
            else: arguments.insert(0, '--unknown')
            with self.assertRaises(stock.Invalid):
                stock.match_invocation(c.policy, arguments, 'solution_build', None)

    def test_kernel_config_export_identity_and_native_binding(self):
        c = self.context
        first = stock.match_invocation(c.policy, c.arguments('nanoda'), 'nanoda', None)
        self.assertEqual(stock.match_invocation(c.policy, c.arguments('native_kernel'), 'native_kernel', first), first)
        config = c.temporary / 'config-nanoda'
        arguments = c.arguments('nanoda')
        data = json.loads(config.read_text()); data['use_stdin'] = 0
        config.write_text(json.dumps(data))
        with self.assertRaises(stock.Invalid): stock.match_invocation(c.policy, arguments, 'nanoda', None)
        c.export.write_text('changed synthetic export')
        with self.assertRaises(stock.Invalid): stock.match_invocation(c.policy, c.arguments('native_kernel'), 'native_kernel', first)

    def test_temp_symlink_and_world_readable_kernel_config_refuse(self):
        c = self.context
        arguments = c.arguments('nanoda')
        config = c.temporary / 'config-nanoda'
        config.chmod(0o644)
        with self.assertRaises(stock.Invalid): stock.match_invocation(c.policy, arguments, 'nanoda', None)
        config.chmod(0o600)
        c.export.unlink(); c.export.symlink_to(config)
        with self.assertRaises(stock.Invalid): stock.match_invocation(c.policy, arguments, 'nanoda', None)

    def test_stale_or_repeated_session_and_private_modes_refuse(self):
        c = self.context
        with self.assertRaises(FileExistsError): c.initialize()
        bad = copy.deepcopy(c.policy); bad['binding']['generation'] = 'a' * 64
        with self.assertRaises(stock.Invalid): stock.validate_journal(c.trace(), bad)
        c.journal.chmod(0o644)
        with self.assertRaises(stock.Invalid):
            with stock.Journal(c.policy, c.journal): pass

    def test_clock_slot_rejects_corruption_replay_and_cross_generation(self):
        c = self.context
        start = (c.policy['binding']['started_ms'] + 1) * 1_000_000
        stock.publish_start(c.policy, c.clock, 'solution_build', start)
        with self.assertRaises(stock.Invalid): stock.publish_start(c.policy, c.clock, 'solution_build', start)
        wrong = copy.deepcopy(c.policy); wrong['binding']['generation'] = 'a' * 64
        with self.assertRaises(stock.Invalid): stock.read_start(wrong, c.clock, 'solution_build')
        c.clock.write_bytes(b'X' * 112)
        with self.assertRaises(stock.Invalid): stock.read_start(c.policy, c.clock, 'solution_build')

    def test_concurrent_lock_is_nonblocking_and_own_fd_closes_on_enter_error(self):
        c = self.context
        with stock.Journal(c.policy, c.journal):
            with self.assertRaises(BlockingIOError):
                with stock.Journal(c.policy, c.journal): pass

    def test_observer_fault_after_child_start_never_runs_child_twice(self):
        c = self.context
        class Fault:
            calls = 0
            def run_measured(self, command, **kwargs):
                self.calls += 1
                subprocess.run(command, check=False, close_fds=False)
                raise RuntimeError('synthetic observer failure after child')
        observer = Fault()
        with contextlib.chdir(c.project), self.assertRaises(RuntimeError):
            stock.run(c.policy, c.arguments('solution_build'), journal_path=c.journal,
                      clock_path=c.clock, real_bwrap=c.real, measure=observer)
        self.assertEqual(observer.calls, 1)
        self.assertTrue(c.trace()['invalid'])

    def test_start_hook_failure_preserves_single_child_exit_and_legacy_api(self):
        measure = stock.load_measure()
        ran = self.root / 'ran'
        def observe(start):
            self.assertIsInstance(start, int)
            self.assertFalse(ran.exists())
            raise OSError('synthetic unavailable observer')
        command = [PYTHON, '-c', 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("ran");sys.exit(7)', str(ran)]
        result = measure.run_measured(command, best_effort=True, preserve_fds=True, started_observer=observe)
        self.assertEqual(result[0], 7)
        self.assertEqual(ran.read_text(), 'ran')

    def test_actual_control_fd_survives_while_clock_and_perf_fds_are_closed(self):
        c = self.context
        control = os.open(os.devnull, os.O_RDONLY)
        counter = os.open(os.devnull, os.O_RDONLY)
        os.set_inheritable(control, True)
        closed = []
        original = stock.clock_descriptor
        def opening(*args, **kwargs):
            descriptor = original(*args, **kwargs)
            self.assertFalse(os.get_inheritable(descriptor))
            closed.append(descriptor)
            return descriptor
        # Known allocation order: after control/perf, the private clock FD is
        # the next descriptor. The callee probes it after exec, not in a mock.
        clock_fd = max(control, counter) + 1
        c.fake.write_text(f'#!{PYTHON}\nimport os,sys\n'
            f'os.fstat({control})\n'
            f'for descriptor in ({counter},{clock_fd}):\n'
            ' try:os.fstat(descriptor)\n except OSError:pass\n else:sys.exit(98)\n')
        c.policy['bubblewrap']['sha256'] = digest(c.fake)
        c.policy['binding']['started_ms'] -= 100
        for path in (c.journal, c.journal.with_suffix('.json.lock'), c.clock): path.unlink()
        c.initialize()
        # Seed the three excluded boundaries without opening a perf event.
        trace = synthetic_trace(c.policy, 3)
        c.journal.write_bytes(stock.canonical(trace))
        measure = stock.load_measure()
        try:
            with mock.patch.object(measure, 'open_counter', return_value=(counter, None)), mock.patch.object(measure.fcntl, 'ioctl', return_value=0), mock.patch.object(stock, 'clock_descriptor', side_effect=opening):
                self.assertEqual(c.run('solution_build', measure=measure), 0)
            self.assertEqual(closed, [clock_fd])
            self.assertFalse(c.trace()['invalid'])
        finally:
            os.close(control)

    def test_main_never_exec_falls_back_after_a_possible_child_launch(self):
        c = self.context
        with mock.patch.object(stock, 'parent_death_guard'), mock.patch.object(stock, 'read_file', return_value=stock.canonical(c.policy)), mock.patch.object(stock, 'load_measure', return_value=object()), mock.patch.object(stock, 'run', side_effect=RuntimeError('post-launch fault')), mock.patch.object(stock.os, 'execv') as execute:
            self.assertEqual(stock.main(c.arguments('resolve')), 125)
            execute.assert_not_called()

    def test_parent_guard_failure_directly_execs_original_bwrap_once(self):
        with mock.patch.object(stock, 'parent_death_guard', side_effect=OSError('unavailable guard')), mock.patch.object(stock.os, 'execv', side_effect=SystemExit(9)) as execute:
            with self.assertRaises(SystemExit): stock.main(['literal argument'])
            execute.assert_called_once_with(stock.REAL_BWRAP, [stock.REAL_BWRAP, 'literal argument'])

    def test_uncatchable_kill_signal_is_propagated_without_installing_handler(self):
        with mock.patch.object(stock, 'parent_death_guard'), mock.patch.object(stock, 'read_file', return_value=stock.canonical(self.context.policy)), mock.patch.object(stock, 'load_measure', return_value=object()), mock.patch.object(stock, 'run', return_value=-signal.SIGKILL), mock.patch.object(stock.signal, 'signal') as handler, mock.patch.object(stock.os, 'kill') as kill:
            stock.main([])
            handler.assert_not_called()
            kill.assert_called_once_with(os.getpid(), signal.SIGKILL)

    def test_failed_launch_invalidates_existing_measurement_intent(self):
        c = self.context
        trace = synthetic_trace(c.policy, 3)
        # Keep synthetic prefix in the past relative to actual new call clocks.
        c.policy['binding']['started_ms'] -= 100
        trace = synthetic_trace(c.policy, 3)
        for path in (c.journal, c.journal.with_suffix('.json.lock'), c.clock): path.unlink()
        c.initialize(); c.journal.write_bytes(stock.canonical(trace))
        measure = stock.load_measure()
        with mock.patch.object(measure.subprocess, 'run', side_effect=OSError('synthetic failed launch')):
            with self.assertRaises(measure.MeasurementError): c.run('solution_build', measure=measure)
        self.assertTrue(c.trace()['invalid'])
        self.assertEqual(self.verdict(c.trace(), c.driver(1, accepted=False))['failure_reason'], 'verdict_invalid')

    def test_unknown_invocation_is_delegated_once_but_invalidates_admission(self):
        c = self.context
        with contextlib.chdir(c.project):
            self.assertEqual(stock.run(c.policy, c.arguments('solution_build'), journal_path=c.journal,
                             clock_path=c.clock, real_bwrap=c.real), 0)
        self.assertTrue(c.trace()['invalid'])
        self.assertEqual(self.verdict()['failure_reason'], 'verdict_invalid')

    def test_main_propagates_actual_child_signal_instead_of_normal_exit_143(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Context(Path(directory), codes={'lake': -signal.SIGTERM})
            c.policy['binding']['started_ms'] -= 100
            c.initialize()
            c.journal.write_bytes(stock.canonical(synthetic_trace(c.policy, 3)))
            policy_path = c.control / 'policy.json'
            policy_path.write_bytes(stock.canonical(c.policy)); policy_path.chmod(0o600)
            script = c.root / 'signal-wrapper.py'
            script.write_text('import pathlib,sys\nsys.path.insert(0,' + repr(str(ROOT / 'scripts')) + ')\nimport stock_replay_measurement as s\n'
                + 'original=s.load_measure\ns.load_measure=lambda _:original(' + repr(str(ROOT / 'server/replay-image/replay-measure')) + ')\n'
                + 's.POLICY_PATH=pathlib.Path(' + repr(str(policy_path)) + ')\ns.REAL_BWRAP=' + repr(c.real) + '\n'
                + 'original_run=s.run\ns.run=lambda p,a,**kw:original_run(p,a,journal_path=pathlib.Path(' + repr(str(c.journal)) + '),clock_path=pathlib.Path(' + repr(str(c.clock)) + '),real_bwrap=s.REAL_BWRAP,**kw)\n'
                + 'raise SystemExit(s.main(' + repr(c.arguments('solution_build')) + '))\n')
            result = subprocess.run([PYTHON, str(script)], cwd=c.project, timeout=5, capture_output=True)
            self.assertEqual(result.returncode, -signal.SIGTERM)
            self.assertEqual(c.trace()['invocations'][-1]['termination'], {'kind': 'signaled', 'signal': 15})
            self.assertFalse(c.trace()['invalid'])


class RealNamespaceTests(unittest.TestCase):
    @unittest.skipUnless(BWRAP and sys.platform == 'linux', 'Linux bubblewrap is unavailable')
    def test_stopped_real_phase_timeout_excludes_pre_measurement_journal_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Context(Path(directory), real=True)
            tool = c.prefix / 'bin/lake'
            tool.write_text(f'#!{PYTHON}\nimport pathlib,sys,time\nif sys.argv[1:]==["build","Solution"]:\n pathlib.Path(".lake/leaf-started").write_text("started")\n time.sleep(5)\n')
            c.policy['tools']['lake']['sha256'] = digest(tool)
            c.initialize()
            script = c.root / 'generation.py'
            arguments = [c.arguments(stage) for stage in stock.STAGES[:4]]
            script.write_text('import json,os,pathlib,sys,time\nsys.path.insert(0,' + repr(str(ROOT / 'scripts')) + ')\nimport stock_replay_measurement as s\n'
                + 's.parent_death_guard()\np=json.loads(' + repr(json.dumps(c.policy)) + ')\nos.chdir(p["project"])\n'
                + 'save=s.Journal.save\ndef delayed(self):\n if self.value["active"] is not None and self.value["active"]["stage"]=="solution_build":time.sleep(.2)\n return save(self)\ns.Journal.save=delayed\n'
                + 'for a in ' + repr(arguments) + ':\n code=s.run(p,a,journal_path=pathlib.Path(' + repr(str(c.journal)) + '),clock_path=pathlib.Path(' + repr(str(c.clock)) + '),real_bwrap=' + repr(c.real) + ')\n if code:raise SystemExit(code)\n')
            process = subprocess.Popen([PYTHON, str(script)], start_new_session=True)
            try:
                deadline = time.monotonic() + 4
                while not (c.project / '.lake/leaf-started').exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue((c.project / '.lake/leaf-started').exists(), 'real phase failed to launch')
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
                cutoff = time.monotonic_ns()
                start = stock.read_start(c.policy, c.clock, 'solution_build')
                trace = c.trace()
                intent_ms = trace['active']['started_ms']
                self.assertGreaterEqual(start['started_ns'] // 1_000_000 - intent_ms, 150)
                result = stock.reduce_trace(c.policy, trace, None, file_count=1, lines_of_code=2,
                    timeout={'binding': c.policy['binding'], 'stopped': True, 'cutoff_ns': cutoff}, measurement_start=start)
                self.assertEqual(result['execution_outcome'], 'timed_out')
                self.assertEqual(result['statistics']['build_wall_time_ms'], (cutoff - start['started_ns']) // 1_000_000)
                self.assertLessEqual(result['statistics']['build_wall_time_ms'], cutoff // 1_000_000 - intent_ms - 150)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=2)

    @unittest.skipUnless(BWRAP, 'real bubblewrap is unavailable')
    def test_original_namespace_mounts_preserve_only_lake_writes_and_deny_host_network(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            port = listener.getsockname()[1]
            c = Context(Path(directory), real=True)
            tool = c.prefix / 'bin/lake'
            tool.write_text(f'#!{PYTHON}\n' + '''import pathlib,socket,sys
p=pathlib.Path(''' + repr(str(c.journal)) + ''')
try:
 p.write_text('MUST_NOT_WRITE')
except OSError:pass
else:sys.exit(91)
if sys.argv[1:]==['build','Solution']:
 pathlib.Path('.lake/allowed').write_text('allowed')
 s=socket.socket();s.settimeout(.2)
 try:
  if s.connect_ex(('127.0.0.1',''' + str(port) + '''))==0:sys.exit(92)
 finally:s.close()
''')
            c.policy['tools']['lake']['sha256'] = digest(tool)
            c.initialize()
            for stage in stock.STAGES[:4]:
                self.assertEqual(c.run(stage), 0)
            self.assertEqual((c.project / '.lake/allowed').read_text(), 'allowed')
            self.assertFalse(c.trace()['invalid'])

    @unittest.skipUnless(BWRAP and sys.platform == 'linux', 'Linux bubblewrap is unavailable')
    def test_actual_parent_death_kills_wrapper_and_real_sandbox_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            heartbeat = root / 'heartbeat'
            wrapper_id = root / 'wrapper.pid'
            owner_source = root / 'owner.py'
            wrapper_source = root / 'wrapper.py'
            child = 'import pathlib,time; p=pathlib.Path(' + repr(str(heartbeat)) + ');\nfor i in range(100):p.write_text(str(i));time.sleep(.05)'
            arguments = [BWRAP, '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--unshare-all', '--die-with-parent', '--new-session', '--', PYTHON, '-c', child]
            wrapper_source.write_text('import os,pathlib,subprocess,sys\nsys.path.insert(0,' + repr(str(ROOT / 'scripts')) + ')\nfrom stock_replay_measurement import parent_death_guard\nparent_death_guard()\npathlib.Path(' + repr(str(wrapper_id)) + ').write_text(str(os.getpid()))\nsubprocess.run(' + repr(arguments) + ',check=False)\n')
            owner_source.write_text('import subprocess,time\np=subprocess.Popen(' + repr([PYTHON, str(wrapper_source)]) + ')\ntime.sleep(10)\n')
            owner = subprocess.Popen([PYTHON, str(owner_source)])
            try:
                deadline = time.monotonic() + 5
                while not heartbeat.exists() and time.monotonic() < deadline: time.sleep(.02)
                self.assertTrue(heartbeat.exists(), 'real sandbox did not start')
                owner.kill(); owner.wait(timeout=2)
                time.sleep(.2)
                first = heartbeat.read_text(); time.sleep(.2)
                self.assertEqual(heartbeat.read_text(), first, 'sandbox survived its original owner')
                proc = Path('/proc') / wrapper_id.read_text().strip() / 'stat'
                if proc.exists(): self.assertEqual(proc.read_text().split()[2], 'Z')
            finally:
                if owner.poll() is None: owner.kill(); owner.wait()
                if wrapper_id.exists():
                    try: os.kill(int(wrapper_id.read_text()), signal.SIGKILL)
                    except ProcessLookupError: pass


if __name__ == '__main__':
    unittest.main()
