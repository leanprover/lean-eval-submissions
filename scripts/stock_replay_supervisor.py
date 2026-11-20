"""Inactive stock original-driver supervisor with a kernel-owned PID namespace.

No active evaluator, image, Worker or admission reader uses this slice. A trusted
selected-workspace/image preparer supplies the policy and provenance context.
Namespace cleanup here is not provider Sandbox destruction or qualification.
"""
from __future__ import annotations

import copy
import contextlib
import ctypes
import hashlib
import importlib.util
import os
from pathlib import Path
import select
import secrets
import signal
import stat
import subprocess
import time

_SPEC = importlib.util.spec_from_file_location('_supervised_stock_measurement', Path(__file__).with_name('stock_replay_measurement.py'))
stock = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stock)

CONTROL = Path('/run/lean-eval')
INSTALL = Path('/opt/lean-eval')
TRUSTED_FILES = ('Challenge.lean', 'WorkspaceTest.lean', 'config.json', 'lakefile.toml',
                 'lake-manifest.json', 'lean-toolchain', '.lake/build/bin/workspace_test')
FRAME_LIMIT = 4096
TEARDOWN_SECONDS = 5


class Invalid(stock.Invalid):
    """Closed supervisor failure, never includes Source/argv/output/errors."""


class Interrupted(BaseException):
    """A termination signal requires owner cleanup before returning failure."""


@contextlib.contextmanager
def termination_guard():
    previous, active = {}, [True]
    def interrupt(_number, _frame):
        if active[0]:
            active[0] = False
            raise Interrupted()
    try:
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[number] = signal.signal(number, interrupt)
        yield active
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def require(condition):
    if not condition:
        raise Invalid('invalid original stock driver supervision')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_hash(path, maximum=256 * 1024 * 1024):
    path = stock.absolute(str(path))
    require(path.resolve(strict=True) == path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_mode & 0o6022 == 0
                and 0 <= info.st_size <= maximum)
        digest = hashlib.sha256()
        with os.fdopen(descriptor, 'rb', closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return {'sha256': digest.hexdigest(), 'mode': stat.S_IMODE(info.st_mode)}
    finally:
        os.close(descriptor)


def workspace_manifest(project):
    return {'schema_version': 1, 'files': {name: file_hash(Path(project) / name) for name in TRUSTED_FILES}}


def context_from_request(request, policy, *, benchmark_files, authority_raw,
                         authority_commit, authority_path, driver_path, file_count, lines_of_code):
    """Trusted preparer only: request3 + original bytes + derived closure.

    Caller must independently prove Git origin/tree/modes and deterministic
    generation; the contracts explicitly do not certify caller-supplied bytes.
    No Source, generator, Git/network or subprocess runs in this function.
    """
    import stock_replay_contract as contracts
    contracts.validate_stock_request(request, benchmark_files=benchmark_files, authority_raw=authority_raw,
                                     authority_commit=authority_commit, authority_path=authority_path)
    stock.validate_policy(policy)
    result, configuration = request['result'], request['benchmark']['configuration_lock']
    expected = {key: request[key] for key in ('replay_task_id', 'attempt', 'execution_profile_digest', 'measurement_config_digest')}
    expected.update(problem_id=result['problem_id'], statement_revision=result['statement_revision'])
    require(all(policy['binding'][key] == value for key, value in expected.items()))
    manifest = workspace_manifest(policy['project'])
    require(sha(stock.canonical(manifest)) == configuration['generator_source_binding']['derived_output_manifest_sha256'])
    require(manifest['files']['WorkspaceTest.lean']['sha256'] == configuration['workspace_driver']['sha256'])
    names = {'lake': 'lake', 'leanexport': 'leanexport', 'leanchecker': 'leanchecker', 'nanoda_bin': 'nanoda'}
    for name, field in names.items():
        require(policy['tools'][name]['sha256'] == request['execution_profile']['binary_sha256'][field])
    require(file_hash(Path(policy['lean_prefix']) / 'bin/lean')['sha256']
            == request['execution_profile']['binary_sha256']['lean'])
    require(policy['bubblewrap']['sha256'] == request['execution_profile']['binary_sha256']['bubblewrap'])
    context = {'schema_version': 1, 'binding': copy.deepcopy(policy['binding']),
        'request_sha256': sha(stock.canonical(request)),
        'benchmark': {key: request['benchmark'][key] for key in ('repository', 'commit', 'toolchain')},
        'configuration_digest': contracts.configuration_digest(configuration),
        'benchmark_tree': configuration['benchmark_tree'], 'workspace_manifest': manifest,
        'measurement_config': copy.deepcopy(request['measurement_config']),
        'driver_path': driver_path,
        'file_count': file_count, 'lines_of_code': lines_of_code}
    validate_context(context, policy)
    return context


def validate_context(context, policy):
    stock.validate_policy(policy)
    require(policy['challenge_module'] == 'Challenge' and policy['solution_module'] == 'Solution')
    require(isinstance(context, dict) and set(context) == {'schema_version', 'binding', 'request_sha256',
        'benchmark', 'configuration_digest', 'benchmark_tree', 'workspace_manifest', 'measurement_config',
        'driver_path', 'file_count', 'lines_of_code'} and type(context['schema_version']) is int and context['schema_version'] == 1)
    require(context['binding'] == policy['binding'])
    for name in ('request_sha256', 'configuration_digest'):
        require(isinstance(context[name], str) and stock.HASH.fullmatch(context[name]))
    benchmark = context['benchmark']
    require(isinstance(benchmark, dict) and set(benchmark) == {'repository', 'commit', 'toolchain'}
            and benchmark['repository'] == 'leanprover/lean-eval')
    import re
    require(isinstance(benchmark['commit'], str) and re.fullmatch('[0-9a-f]{40}', benchmark['commit'])
            and isinstance(context['benchmark_tree'], str) and re.fullmatch('[0-9a-f]{40}', context['benchmark_tree'])
            and benchmark['toolchain'] in {'leanprover/lean4:v4.35.0-rc3', 'leanprover/lean4:v4.35.0', 'leanprover/lean4:v4.36.0-rc2'})
    measurement = context['measurement_config']
    require(isinstance(measurement, dict) and set(measurement) == {'schema_version', 'phase_semantics',
        'wall_time_limit_ms', 'memory_limit_bytes', 'retired_instructions', 'checker_sequence'}
        and type(measurement['schema_version']) is int and measurement['schema_version'] == 2
        and measurement['phase_semantics'] == 'stock_lake_solution_build_export_dual_kernel_v1'
        and measurement['checker_sequence'] == ['bundled_nanoda', 'bundled_leanchecker']
        and isinstance(measurement['retired_instructions'], dict)
        and measurement['retired_instructions'].keys() == {'required', 'perf_event'}
        and type(measurement['retired_instructions']['required']) is bool
        and measurement['retired_instructions']['perf_event'] == 'instructions:u'
        and stock.integer(measurement['wall_time_limit_ms'], 1, stock.MAX_WALL_MS)
        and stock.integer(measurement['memory_limit_bytes'], 1))
    require(policy['binding']['measurement_config_digest'] == sha(
        b'lean-eval-replay-measurement-config-v1\0' + stock.canonical(measurement)))
    require(stock.integer(context['file_count']) and stock.integer(context['lines_of_code']))
    require(isinstance(context['driver_path'], str) and 0 < len(context['driver_path']) <= 8192)
    for directory in context['driver_path'].split(':'):
        path = stock.absolute(directory)
        require(path.resolve(strict=True) == path and path.is_dir() and path.stat().st_mode & 0o022 == 0
                and not path.is_relative_to(Path(policy['project']))
                and not path.is_relative_to(Path(policy['temporary_directory'])))
    require(not Path(policy['project']).is_relative_to(Path(policy['lean_prefix'])))
    require(context['workspace_manifest'] == workspace_manifest(policy['project']))
    # A preprimed original driver is required; no substitute test target or
    # no-build flag is inserted into the original `lake test` command.
    import tomllib
    lakefile = tomllib.loads((Path(policy['project']) / 'lakefile.toml').read_text())
    require(lakefile.get('testDriver') == 'workspace_test'
            and any(item.get('name') == 'workspace_test' and item.get('root') == 'WorkspaceTest'
                    for item in lakefile.get('lean_exe', [])))
    configuration = stock.decode((Path(policy['project']) / 'config.json').read_bytes())
    require(configuration.get('challenge_module') == policy['challenge_module']
            and configuration.get('solution_module') == policy['solution_module']
            and configuration.get('permitted_axioms') == policy['permitted_axioms'])
    for name, tool in policy['tools'].items():
        require(file_hash(tool['path'])['sha256'] == tool['sha256'])
    require(file_hash(policy['bubblewrap']['path'])['sha256'] == policy['bubblewrap']['sha256'])
    require(len(stock.canonical(context)) <= stock.LIMIT)
    return context


def private_write(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        raw = stock.canonical(value)
        require(len(raw) <= stock.LIMIT)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def pidfd_open(pid):
    # Supported Linux arches use these generic pidfd syscall numbers. Some
    # Python builds omit os.pidfd_open despite kernel support.
    require(os.uname().machine in {'x86_64', 'aarch64'})
    descriptor = ctypes.CDLL(None, use_errno=True).syscall(434, pid, 0)
    require(descriptor >= 0)
    os.set_inheritable(descriptor, False)
    return descriptor


def pidfd_kill(descriptor):
    if select.select([descriptor], [], [], 0)[0]:
        return
    result = ctypes.CDLL(None, use_errno=True).syscall(424, descriptor, signal.SIGKILL, 0, 0)
    if result != 0:
        require(select.select([descriptor], [], [], 0)[0])


def proc_identity(pid):
    pieces = Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()
    status = Path(f'/proc/{pid}/status').read_text().splitlines()
    nested = next(line.split()[1:] for line in status if line.startswith('NSpid:'))
    require(len(pieces) >= 20 and len(nested) >= 2 and int(nested[-1]) == 1)
    return {'parent': int(pieces[1]), 'started_ticks': int(pieces[19])}


class Frames:
    def __init__(self, descriptor):
        self.descriptor, self.pending = descriptor, b''

    def read(self, deadline):
        while b'\n' not in self.pending:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.descriptor], [], [], left)[0]:
                raise TimeoutError()
            raw = os.read(self.descriptor, FRAME_LIMIT + 1)
            require(raw and len(self.pending) + len(raw) <= FRAME_LIMIT)
            self.pending += raw
        raw, self.pending = self.pending.split(b'\n', 1)
        return stock.decode(raw)


def frame(descriptor, value):
    raw = stock.canonical(value) + b'\n'
    require(len(raw) <= FRAME_LIMIT)
    require(os.write(descriptor, raw) == len(raw))


class NamespaceOwner:
    """Own one real bwrap child and its pinned PID-namespace init, never killpg."""
    def __init__(self, command, inherited):
        self.process = None
        self.outer_fd = self.init_fd = self.namespace_fd = None
        self.verified = False
        self.identity = None
        self.process = subprocess.Popen(command, pass_fds=inherited, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Pin the unreaped direct child before any poll/wait can permit reuse.
        try:
            self.outer_fd = pidfd_open(self.process.pid)
        except BaseException:
            os.kill(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=TEARDOWN_SECONDS)
            raise

    def bind(self, information):
        require(isinstance(information, dict) and type(information.get('child-pid')) is int
                and type(information.get('pid-namespace')) is int)
        require(not select.select([self.outer_fd], [], [], 0)[0])
        pid = information['child-pid']
        require(pid > 1 and pid != self.process.pid)
        namespace_fd = os.open(f'/proc/{pid}/ns/pid', os.O_RDONLY | os.O_CLOEXEC)
        init_fd = None
        try:
            namespace = os.fstat(namespace_fd)
            require(namespace.st_ino == information['pid-namespace']
                    and namespace.st_ino != Path('/proc/self/ns/pid').stat().st_ino)
            before = proc_identity(pid)
            require(before['parent'] == self.process.pid)
            init_fd = pidfd_open(pid)
            # Parent is our still-pinned immutable bwrap process; Source is
            # blocked at the private go gate. Recheck identity after pinning.
            after = proc_identity(pid)
            require(after == before and Path(f'/proc/{pid}/ns/pid').stat().st_ino == namespace.st_ino
                    and not select.select([self.outer_fd, init_fd], [], [], 0)[0])
            self.identity = {'namespace_device': namespace.st_dev, 'namespace_inode': namespace.st_ino,
                             'init_started_ticks': before['started_ticks']}
            self.namespace_fd, self.init_fd = namespace_fd, init_fd
            self.verified = True
        except BaseException:
            os.close(namespace_fd)
            if init_fd is not None:
                os.close(init_fd)
            raise

    def stop(self):
        require(self.process is not None)
        requested = time.monotonic_ns()
        # Unverified reported PIDs are never signaled. Our pinned direct child
        # is the only fallback target before Source has been released.
        target = self.init_fd if self.verified else self.outer_fd
        pidfd_kill(target)
        require(select.select([target], [], [], TEARDOWN_SECONDS)[0])
        self.process.wait(timeout=TEARDOWN_SECONDS)
        cutoff = time.monotonic_ns()
        return {'stopped': self.verified, 'requested_ns': requested, 'cutoff_ns': cutoff,
                'namespace_identity_sha256': sha(stock.canonical(self.identity)) if self.verified else None}

    def close(self):
        for name in ('outer_fd', 'init_fd', 'namespace_fd'):
            descriptor = getattr(self, name)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)


def outer_command(policy, control, files, info_fd, status_fd, go_fd, python):
    """Extra outer containment only; inner comparator argv remains untouched."""
    command = [policy['bubblewrap']['path'], '--unshare-all', '--die-with-parent', '--new-session',
        '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/run', '--dir', '/run/user',
        '--dir', str(CONTROL), '--bind', str(control), str(CONTROL),
        '--tmpfs', '/opt', '--dir', str(INSTALL),
        '--bind', policy['project'], policy['project'],
        '--bind', policy['temporary_directory'], policy['temporary_directory'],
        '--ro-bind', policy['lean_prefix'], policy['lean_prefix']]
    for name in TRUSTED_FILES:
        path = str(Path(policy['project']) / name)
        command += ['--ro-bind', path, path]
    # Only these immutable instrumentation files are overlaid at their fixed
    # future image locations. They never replace a Lean/Lake/tool binary.
    for source, target in files.items():
        require(not Path(source).is_relative_to(Path(policy['project'])))
        file_hash(source)
        command += ['--ro-bind', str(source), str(target)]
    command += ['--clearenv', '--setenv', 'PATH', '/usr/bin:/bin', '--chdir', policy['project'],
        '--info-fd', str(info_fd), '--', python, '-I', str(INSTALL / 'stock-supervisor-agent'), str(status_fd), str(go_fd)]
    return command


def runtime_files():
    root = Path(__file__).resolve().parents[1]
    installed = Path(__file__).resolve().parent == INSTALL
    return {
        Path(__file__).resolve(): INSTALL / 'stock_replay_supervisor.py',
        Path(stock.__file__).resolve(): INSTALL / 'stock_replay_measurement.py',
        (INSTALL / 'stock-supervisor-agent' if installed else root / 'server/replay-image/stock-supervisor-agent'): INSTALL / 'stock-supervisor-agent',
        (INSTALL / 'replay-measure-stock' if installed else root / 'server/replay-image/replay-measure-stock'): INSTALL / 'replay-measure-stock',
        (INSTALL / 'replay-measure' if installed else root / 'server/replay-image/replay-measure'): INSTALL / 'replay-measure',
    }


def supervise(policy_template, context_template, *, control=CONTROL, python='/usr/bin/python3', files=None):
    """Main-thread owner entry; signal cleanup is completed before return."""
    with termination_guard() as active:
        return _supervise(policy_template, context_template, control=control, python=python, files=files, active=active)


def _supervise(policy_template, context_template, *, control, python, files, active):
    """Run one original per-problem driver. Never activates request/profile3."""
    validate_context(context_template, policy_template)
    control = stock.absolute(str(control))
    stock.secure_directory(control)
    require(not control.is_relative_to(Path(policy_template['project']))
            and not Path(policy_template['project']).is_relative_to(control)
            and not control.is_relative_to(Path(policy_template['temporary_directory']))
            and not Path(policy_template['temporary_directory']).is_relative_to(control))
    policy, context = copy.deepcopy(policy_template), copy.deepcopy(context_template)
    generation = secrets.token_hex(32)
    private_write(control / 'stock-supervisor-bootstrap.json', {'generation': generation})
    information_r = information_w = status_r = status_w = go_r = go_w = None
    owner, stopped, driver, driver_started, failure = None, None, None, None, False
    started_ns, boundary = None, 'namespace_setup'
    timed_out = False
    try:
        information_r, information_w = os.pipe2(os.O_CLOEXEC)
        status_r, status_w = os.pipe2(os.O_CLOEXEC)
        go_r, go_w = os.pipe2(os.O_CLOEXEC)
        command = outer_command(policy, control, runtime_files() if files is None else files,
                                information_w, status_w, go_r, python)
        owner = NamespaceOwner(command, (information_w, status_w, go_r))
        for descriptor in (information_w, status_w, go_r):
            os.close(descriptor)
        information_w = status_w = go_r = None
        frames = Frames(status_r)
        boundary = 'namespace_information'
        deadline = time.monotonic() + 10
        require(select.select([information_r], [], [], 10)[0])
        information = stock.decode(os.read(information_r, FRAME_LIMIT + 1))
        boundary = 'namespace_ready'
        ready = frames.read(deadline)
        require(ready == {'kind': 'ready', 'generation': generation})
        boundary = 'namespace_identity'
        owner.bind(information)
        started_ns = time.monotonic_ns()
        policy['binding'].update(generation=generation, started_ms=started_ns // 1_000_000)
        context['binding'] = copy.deepcopy(policy['binding'])
        boundary = 'control_initialize'
        private_write(control / 'stock-measurement-policy.json', policy)
        private_write(control / 'stock-supervisor-context.json', context)
        stock.initialize(policy, control / 'stock-measurement.json', control / 'stock-measurement.clock')
        private_write(control / 'stock-generation-owner.json', {'binding': policy['binding'],
            'policy_sha256': stock.policy_digest(policy), 'context_sha256': sha(stock.canonical(context)),
            'namespace': owner.identity, 'started_ns': started_ns})
        # Nothing untrusted can start before kernel ownership and the exact
        # fresh generation/policy/context are recorded successfully.
        require(os.write(go_w, b'1') == 1)
        os.close(go_w)
        go_w = None
        deadline = started_ns / 1_000_000_000 + context['measurement_config']['wall_time_limit_ms'] / 1000
        try:
            boundary = 'driver_spawn'
            started = frames.read(deadline)
            require(set(started) == {'kind', 'binding', 'pid', 'spawned_ns'} and started['kind'] == 'driver_started'
                    and started['binding'] == policy['binding'] and stock.integer(started['pid'], 2)
                    and stock.integer(started['spawned_ns'], started_ns))
            driver_started = started
            boundary = 'driver_exit'
            finished = frames.read(deadline)
            require(set(finished) == {'kind', 'binding', 'pid', 'finished_ns', 'termination'}
                    and finished['kind'] == 'driver_finished' and finished['binding'] == policy['binding']
                    and finished['pid'] == started['pid']
                    and stock.integer(finished['finished_ns'], started['spawned_ns']))
            stock.termination_valid(finished['termination'])
            passed = finished['termination'] == {'kind': 'exited', 'code': 0}
            # Same per-problem score rule as _run_preprimed_eval: actual lake
            # test status, never the outer Python/bwrap status or message text.
            driver = {'binding': copy.deepcopy(policy['binding']), 'termination': finished['termination'],
                      'passed': [policy['binding']['problem_id']] if passed else [],
                      'statement_revisions': {policy['binding']['problem_id']: policy['binding']['statement_revision']} if passed else {}}
        except TimeoutError:
            timed_out = True
    except BaseException:
        failure = True
    finally:
        # Subsequent termination signals cannot interrupt kernel teardown.
        # SIGKILL/parent death is covered separately by bwrap's own guard.
        active[0] = False
        if owner is not None:
            try:
                stopped = owner.stop()
                if stopped['stopped']:
                    stopped['binding'] = copy.deepcopy(policy['binding'])
                    private_write(control / 'stock-generation-stop.json', {'schema_version': 1,
                        'started_ns': started_ns, **stopped})
            except BaseException:
                failure = True
                boundary = 'namespace_teardown'
            finally:
                owner.close()
        for descriptor in (information_r, information_w, status_r, status_w, go_r, go_w):
            if descriptor is not None:
                os.close(descriptor)
    verdict = stock.failed_verdict(policy['binding'])
    trace, measurement_start = None, None
    if not failure and stopped is not None and stopped['stopped']:
        try:
            trace = stock.validate_journal(stock.decode(stock.read_file(control / 'stock-measurement.json')), policy)
            timeout = None
            if timed_out:
                timeout = {'binding': policy['binding'], 'cutoff_ns': stopped['cutoff_ns'], 'stopped': True}
                if trace['active'] is not None and stock.BUCKET[trace['active']['stage']] is not None:
                    measurement_start = stock.read_start(policy, control / 'stock-measurement.clock', trace['active']['stage'])
            verdict = stock.reduce_trace(policy, trace, driver, file_count=context['file_count'], lines_of_code=context['lines_of_code'],
                                         timeout=timeout, measurement_start=measurement_start)
            if context['measurement_config']['retired_instructions']['required']:
                if any(verdict['statistics'].get(name + '_retired_instructions', {}).get('status') != 'measured'
                       for name in ('build', 'checker')):
                    verdict = stock.failed_verdict(policy['binding'])
        except (OSError, ValueError, TypeError, KeyError):
            pass
    output = {'schema_version': 1, 'binding': policy['binding'], 'request_sha256': context['request_sha256'],
        'configuration_digest': context['configuration_digest'], 'benchmark': context['benchmark'],
        'benchmark_tree': context['benchmark_tree'], 'workspace_manifest_sha256': sha(stock.canonical(context['workspace_manifest'])),
        'driver': driver, 'driver_started': driver_started, 'generation_stop': stopped,
        'generation_started_ns': started_ns,
        'supervisor_failure': failure, 'failure_boundary': boundary if failure else None, 'timed_out': timed_out,
        'provider_destruction': 'not_established', 'runtime_qualification': 'not_established', 'verdict': verdict}
    require(len(stock.canonical(output)) <= stock.LIMIT)
    return output


def agent(status_fd, go_fd):
    """Trusted inner agent: real Popen/wait record, private channels not inherited."""
    require(status_fd > 2 and go_fd > 2 and status_fd != go_fd)
    for descriptor in (status_fd, go_fd):
        require(stat.S_ISFIFO(os.fstat(descriptor).st_mode))
        os.set_inheritable(descriptor, False)
    bootstrap = stock.decode(stock.read_file(CONTROL / 'stock-supervisor-bootstrap.json'))
    generation = bootstrap['generation']
    require(set(bootstrap) == {'generation'} and stock.HASH.fullmatch(generation))
    frame(status_fd, {'kind': 'ready', 'generation': generation})
    require(os.read(go_fd, 1) == b'1')
    os.close(go_fd)
    policy = stock.validate_policy(stock.decode(stock.read_file(CONTROL / 'stock-measurement-policy.json')))
    context = stock.decode(stock.read_file(CONTROL / 'stock-supervisor-context.json'))
    validate_context(context, policy)
    require(policy['binding']['generation'] == generation)
    environment = {'PATH': context['driver_path'], 'TMPDIR': policy['temporary_directory'],
        'HOME': policy['temporary_directory'], 'ELAN_TOOLCHAIN': context['benchmark']['toolchain'],
        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
        'COMPARATOR_BWRAP': str(INSTALL / 'replay-measure-stock')}
    process = subprocess.Popen([policy['tools']['lake']['path'], 'test'], cwd=policy['project'], env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    frame(status_fd, {'kind': 'driver_started', 'binding': policy['binding'], 'pid': process.pid,
                      'spawned_ns': time.monotonic_ns()})
    code = process.wait()
    frame(status_fd, {'kind': 'driver_finished', 'binding': policy['binding'], 'pid': process.pid,
                      'finished_ns': time.monotonic_ns(), 'termination': stock.termination(code)})
    os.close(status_fd)
    return 0


def main():
    """Future image entry only; fixed private input, no argv/env overrides."""
    stock.parent_death_guard()
    value = stock.decode(stock.read_file(CONTROL / 'stock-supervisor-input.json'))
    require(isinstance(value, dict) and set(value) == {'policy', 'context'})
    result = supervise(value['policy'], value['context'])
    print(stock.canonical(result).decode())
    return 0 if result['generation_stop'] is not None and result['generation_stop']['stopped'] else 125
