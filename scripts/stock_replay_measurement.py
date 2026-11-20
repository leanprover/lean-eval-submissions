"""Inactive, trusted stock-Lake measurement adapter and trace reducer.

Only a future reviewed supervisor may create its policy/session, record the
per-problem driver's termination, and use the reducer. No active replay reader,
evaluator or image imports this module. It never interprets proof output.
"""
from __future__ import annotations

import ctypes
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import tempfile
import time

POLICY_PATH = Path('/run/lean-eval/stock-measurement-policy.json')
JOURNAL_PATH = Path('/run/lean-eval/stock-measurement.json')
CLOCK_PATH = Path('/run/lean-eval/stock-measurement.clock')
REAL_BWRAP = '/usr/bin/bwrap'
STAGES = ('resolve', 'challenge_build', 'challenge_export', 'solution_build',
          'solution_export', 'nanoda', 'native_kernel')
BUCKET = {name: ('build' if name in STAGES[3:5] else 'checker' if name in STAGES[5:] else None)
          for name in STAGES}
LIMIT = 65536
MAX_INTEGER = 2**53 - 1
MAX_WALL_MS = 19_800_000
HASH = re.compile(r'[0-9a-f]{64}\Z')
COUNTER_REASONS = {'counter_not_reported', 'counter_not_supported', 'counter_permission_denied'}
BINDING_FIELDS = {'replay_task_id', 'attempt', 'execution_profile_digest',
                  'measurement_config_digest', 'generation', 'started_ms',
                  'problem_id', 'statement_revision'}
POLICY_FIELDS = {'schema_version', 'binding', 'project', 'lean_prefix', 'lake_home',
                 'temporary_directory', 'inherited_path', 'resolved_path', 'lean_path',
                 'challenge_module', 'solution_module', 'declarations', 'permitted_axioms', 'tools', 'bubblewrap'}
TOOL_NAMES = {'lake', 'leanexport', 'nanoda_bin', 'leanchecker'}
JOURNAL_FIELDS = {'schema_version', 'binding', 'policy_sha256', 'invocations',
                  'active', 'invalid', 'kernel_export'}
ARITIES = {'--ro-bind': 2, '--bind': 2, '--setenv': 2, '--tmpfs': 1,
           '--dir': 1, '--dev': 1, '--proc': 1, '--chdir': 1,
           '--clearenv': 0, '--unshare-all': 0, '--die-with-parent': 0,
           '--new-session': 0, '--share-net': 0}
BASE_OPTIONS = ['--ro-bind', '/', '/', '--tmpfs', '/home', '--tmpfs', '/root',
                '--tmpfs', '/run/user', '--tmpfs', '/tmp', '--dir', '/tmp/home',
                '--dev', '/dev', '--proc', '/proc', '--clearenv']


class Invalid(ValueError):
    """Closed local evidence failure; messages never include input values."""


def require(condition):
    if not condition:
        raise Invalid('invalid stock measurement evidence')


def integer(value, low=0, high=MAX_INTEGER):
    return type(value) is int and low <= value <= high


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def decode(raw):
    require(len(raw) <= LIMIT)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(Invalid('nonfinite number')))
    except (ValueError, RecursionError, UnicodeError) as error:
        raise Invalid('invalid stock measurement JSON') from error
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(depth <= 16 and count <= 4096)
        if isinstance(item, dict):
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
    return value


def absolute(value):
    require(isinstance(value, str) and '\0' not in value)
    path = Path(value)
    require(path.is_absolute() and str(path) == value and '..' not in path.parts)
    return path


def validate_binding(binding):
    require(isinstance(binding, dict) and set(binding) == BINDING_FIELDS)
    require(isinstance(binding['replay_task_id'], str)
            and re.fullmatch(r'rt1_[0-9a-f]{64}', binding['replay_task_id']) is not None)
    for field in ('execution_profile_digest', 'measurement_config_digest', 'generation'):
        require(isinstance(binding[field], str) and HASH.fullmatch(binding[field]) is not None)
    require(integer(binding['attempt'], 1, 4) and integer(binding['started_ms']))
    require(integer(binding['statement_revision'], 1))
    require(isinstance(binding['problem_id'], str)
            and re.fullmatch(r'[a-z0-9][a-z0-9_]{0,127}', binding['problem_id']) is not None)


def validate_policy(policy):
    require(isinstance(policy, dict) and set(policy) == POLICY_FIELDS
            and type(policy['schema_version']) is int and policy['schema_version'] == 1)
    validate_binding(policy['binding'])
    for field in ('project', 'lean_prefix', 'lake_home', 'temporary_directory'):
        absolute(policy[field])
    require(not Path(policy['temporary_directory']).is_relative_to(Path(policy['project'])))
    for field in ('inherited_path', 'resolved_path', 'lean_path'):
        require(isinstance(policy[field], str) and policy[field] and '\0' not in policy[field]
                and len(policy[field]) <= 8192)
    for field in ('challenge_module', 'solution_module'):
        require(isinstance(policy[field], str) and 0 < len(policy[field]) <= 512 and '\0' not in policy[field])
    require(policy['challenge_module'] != policy['solution_module'])
    for field in ('declarations', 'permitted_axioms'):
        values = policy[field]
        require(isinstance(values, list) and len(values) <= 256)
        require(all(isinstance(v, str) and 0 < len(v) <= 512 and '\0' not in v for v in values))
        require(len(set(values)) == len(values))
    require(bool(policy['declarations']))
    tools = policy['tools']
    require(isinstance(tools, dict) and set(tools) == TOOL_NAMES)
    for name, value in tools.items():
        require(isinstance(value, dict) and set(value) == {'path', 'sha256'})
        absolute(value['path'])
        require(isinstance(value['sha256'], str) and HASH.fullmatch(value['sha256']))
        require(Path(value['path']).is_relative_to(Path(policy['lean_prefix']) / 'bin'))
        require(Path(value['path']).name == name)
    bubblewrap = policy['bubblewrap']
    require(isinstance(bubblewrap, dict) and set(bubblewrap) == {'path', 'sha256'})
    absolute(bubblewrap['path'])
    require(isinstance(bubblewrap['sha256'], str) and HASH.fullmatch(bubblewrap['sha256']))
    require(len(canonical(policy)) <= LIMIT)
    return policy


def policy_digest(policy):
    validate_policy(policy)
    return hashlib.sha256(b'lean-eval-stock-measurement-policy-v1\0' + canonical(policy)).hexdigest()


def secure_directory(path):
    path = absolute(str(path))
    require(path.resolve(strict=True) == path)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and info.st_mode & 0o077 == 0)


def read_file(path, limit=LIMIT):
    path = absolute(str(path))
    require(path.resolve(strict=True) == path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and info.st_nlink == 1 and info.st_mode & 0o077 == 0 and info.st_size <= limit)
        with os.fdopen(descriptor, 'rb', closefd=False) as handle:
            raw = handle.read(limit + 1)
        require(len(raw) <= limit)
        return raw
    finally:
        os.close(descriptor)


def initialize(policy, path=JOURNAL_PATH, clock_path=CLOCK_PATH):
    """Trusted supervisor only; refuses stale files, locks, and unsafe locations."""
    validate_policy(policy)
    path = absolute(str(path))
    secure_directory(path.parent)
    require(not path.is_relative_to(Path(policy['project'])))
    lock = path.with_suffix(path.suffix + '.lock')
    lock_descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    os.close(lock_descriptor)
    value = {'schema_version': 2, 'binding': policy['binding'], 'policy_sha256': policy_digest(policy),
             'invocations': [], 'active': None, 'invalid': False, 'kernel_export': None}
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(canonical(value))
        handle.flush()
        os.fsync(handle.fileno())
    clock_path = absolute(str(clock_path))
    require(clock_path.parent == path.parent and clock_path not in {path, lock})
    descriptor = os.open(clock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(clock_bytes(policy, 0, 0))
        handle.flush()
        os.fsync(handle.fileno())
    return value


def clock_bytes(policy, index, started_ns):
    header = bytes.fromhex(policy_digest(policy)) + bytes.fromhex(policy['binding']['generation'])
    raw = header + struct.pack('>QQ', index, started_ns)
    return raw + hashlib.sha256(raw).digest()


def decode_clock(raw, policy):
    require(len(raw) == 112 and raw[:64] == clock_bytes(policy, 0, 0)[:64])
    require(hashlib.sha256(raw[:80]).digest() == raw[80:])
    index, started_ns = struct.unpack('>QQ', raw[64:80])
    require(integer(index, 0, len(STAGES)) and integer(started_ns, 0, 2**64-1))
    require((index == 0) == (started_ns == 0))
    return index, started_ns


def clock_descriptor(path, *, write):
    path = absolute(str(path))
    secure_directory(path.parent)
    require(path.resolve(strict=True) == path)
    descriptor = os.open(path, (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and info.st_nlink == 1 and info.st_mode & 0o077 == 0 and info.st_size == 112)
        fcntl.flock(descriptor, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def publish_start(policy, path, stage, started_ns):
    require(stage in STAGES and BUCKET[stage] is not None)
    require(integer(started_ns, policy['binding']['started_ms'] * 1_000_000, 2**64-1))
    descriptor = clock_descriptor(path, write=True)
    try:
        previous, _ = decode_clock(os.pread(descriptor, 113, 0), policy)
        index = STAGES.index(stage) + 1
        require(previous < index)
        raw = clock_bytes(policy, index, started_ns)
        require(os.pwrite(descriptor, raw, 0) == len(raw))
        # No fsync: the stopped-generation reader uses this same filesystem's
        # page cache and lock. Persistence across host loss is not claimed.
    finally:
        os.close(descriptor)


def read_start(policy, path, stage):
    descriptor = clock_descriptor(path, write=False)
    try:
        index, started_ns = decode_clock(os.pread(descriptor, 113, 0), policy)
        require(index == STAGES.index(stage) + 1)
        return {'policy_sha256': policy_digest(policy), 'generation': policy['binding']['generation'],
                'stage': stage, 'started_ns': started_ns}
    finally:
        os.close(descriptor)


def counter_valid(value):
    require(isinstance(value, dict))
    if value.get('status') == 'measured':
        require(set(value) == {'status', 'value'} and integer(value['value']))
    else:
        require(set(value) == {'status', 'reason'} and value['status'] == 'unavailable'
                and value['reason'] in COUNTER_REASONS)


def termination(returncode):
    require(integer(returncode, -127, 255))
    return {'kind': 'signaled', 'signal': -returncode} if returncode < 0 else {'kind': 'exited', 'code': returncode}


def termination_valid(value):
    require(isinstance(value, dict))
    require((set(value) == {'kind', 'code'} and value['kind'] == 'exited' and integer(value['code'], 0, 255))
            or (set(value) == {'kind', 'signal'} and value['kind'] == 'signaled' and integer(value['signal'], 1, 127)))


def validate_journal(value, policy):
    require(isinstance(value, dict) and set(value) == JOURNAL_FIELDS
            and type(value['schema_version']) is int and value['schema_version'] == 2)
    validate_binding(value['binding'])
    require(value['binding'] == policy['binding'] and value['policy_sha256'] == policy_digest(policy))
    require(type(value['invalid']) is bool and isinstance(value['invocations'], list)
            and len(value['invocations']) <= len(STAGES))
    previous_finish = policy['binding']['started_ms']
    for index, record in enumerate(value['invocations']):
        require(isinstance(record, dict) and set(record) == {'stage', 'started_ms', 'finished_ms', 'wall_time_ms', 'measurement_started_ns', 'counter', 'termination'})
        require(record['stage'] == STAGES[index])
        require(integer(record['started_ms'], previous_finish) and integer(record['finished_ms'], record['started_ms']))
        require(record['finished_ms'] - record['started_ms'] <= MAX_WALL_MS)
        require(record['finished_ms'] <= policy['binding']['started_ms'] + MAX_WALL_MS)
        require(integer(record['wall_time_ms'], 0, record['finished_ms'] - record['started_ms']))
        termination_valid(record['termination'])
        if BUCKET[record['stage']] is None:
            require(record['counter'] is None and record['measurement_started_ns'] is None)
        else:
            counter_valid(record['counter'])
            require(integer(record['measurement_started_ns'], record['started_ms'] * 1_000_000,
                            (record['finished_ms'] + 1) * 1_000_000))
        previous_finish = record['finished_ms']
        if index < min(5, len(value['invocations']) - 1 + (value['active'] is not None)):
            require(record['termination'] == {'kind': 'exited', 'code': 0})
    active = value['active']
    if active is not None:
        require(isinstance(active, dict) and set(active) == {'stage', 'started_ms'})
        require(len(value['invocations']) < len(STAGES) and active['stage'] == STAGES[len(value['invocations'])])
        require(integer(active['started_ms'], previous_finish))
    identity = value['kernel_export']
    kernel_observed = len(value['invocations']) > STAGES.index('nanoda') or (
        active is not None and active['stage'] in STAGES[5:])
    require((identity is not None) == kernel_observed)
    if identity is not None:
        require(isinstance(identity, dict) and set(identity) == {'device', 'inode', 'size', 'modified_ns', 'path_sha256'})
        require(all(integer(identity[k], 0, 2**64-1) for k in ('device', 'inode', 'size', 'modified_ns')))
        require(isinstance(identity['path_sha256'], str) and HASH.fullmatch(identity['path_sha256']))
    return value


class Journal:
    def __init__(self, policy, path=JOURNAL_PATH):
        self.policy, self.path = validate_policy(policy), absolute(str(path))

    def __enter__(self):
        secure_directory(self.path.parent)
        require(not self.path.is_relative_to(Path(self.policy['project'])))
        lock = self.path.with_suffix(self.path.suffix + '.lock')
        self.descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(self.descriptor)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                    and info.st_nlink == 1 and info.st_mode & 0o077 == 0)
            # Never wait behind an adversarial/concurrent invocation.
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.value = validate_journal(decode(read_file(self.path)), self.policy)
            return self
        except BaseException:
            os.close(self.descriptor)
            raise

    def __exit__(self, *_):
        os.close(self.descriptor)

    def save(self):
        validate_journal(self.value, self.policy)
        data = canonical(self.value)
        require(len(data) <= LIMIT)
        descriptor, name = tempfile.mkstemp(prefix='.stock-measure-', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)


def split_bwrap(arguments):
    """Parse the supported stock grammar, consuming option values before `--`."""
    options, index = [], 0
    require(isinstance(arguments, list) and len(arguments) <= 512
            and all(isinstance(a, str) and '\0' not in a and len(a) <= 8192 for a in arguments))
    while index < len(arguments) and arguments[index] != '--':
        option = arguments[index]
        require(option in ARITIES)
        arity = ARITIES[option]
        require(index + arity < len(arguments))
        options.extend(arguments[index:index + arity + 1])
        index += arity + 1
    require(index < len(arguments) - 1 and arguments[index] == '--')
    return options, arguments[index + 1:]


def sandbox_options(policy, stage, *, config=None, export=None):
    project, prefix, home = (policy[k] for k in ('project', 'lean_prefix', 'lake_home'))
    tools = policy['tools']
    options = list(BASE_OPTIONS)
    if stage != 'resolve':
        options += ['--tmpfs', '/run', '--tmpfs', '/var']
    if stage in ('resolve', 'challenge_build', 'solution_build'):
        readable, writable = [project, prefix, home], [project + '/.lake']
        environment = [('PATH', policy['inherited_path']), ('LEAN_ABORT_ON_PANIC', '1')]
    elif stage in ('challenge_export', 'solution_export'):
        readable, writable = [project, project + '/.lake', prefix, tools['leanexport']['path']], []
        environment = [('LEAN_ABORT_ON_PANIC', '1'), ('LEAN_PATH', policy['lean_path']), ('PATH', policy['resolved_path'])]
    else:
        require(stage in ('nanoda', 'native_kernel') and config is not None and export is not None)
        executable = tools['nanoda_bin' if stage == 'nanoda' else 'leanchecker']['path']
        readable, writable = [config, export, executable, prefix], []
        environment = [('LEAN_ABORT_ON_PANIC', '1')]
    for path in readable:
        options += ['--ro-bind', path, path]
    for path in writable:
        options += ['--bind', path, path]
    for key, value in environment:
        options += ['--setenv', key, value]
    options += ['--setenv', 'HOME', '/tmp/home', '--unshare-all', '--die-with-parent', '--new-session']
    if stage == 'resolve':
        options += ['--share-net']
    return options + ['--chdir', policy['temporary_directory'] if stage in STAGES[5:] else project]


def temporary_identity(path, policy):
    path = absolute(path)
    directory = Path(policy['temporary_directory'])
    secure_directory(directory)
    require(path.parent == directory and path.resolve(strict=True) == path)
    info = path.stat(follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and info.st_nlink == 1 and info.st_mode & 0o077 == 0)
    return {'device': info.st_dev, 'inode': info.st_ino, 'size': info.st_size,
            'modified_ns': info.st_mtime_ns, 'path_sha256': hashlib.sha256(str(path).encode()).hexdigest()}


def match_invocation(policy, arguments, stage, previous_export):
    options, command = split_bwrap(arguments)
    tools = policy['tools']
    config, export, identity = None, None, previous_export
    if stage == 'resolve':
        expected = [tools['lake']['path'], 'env']
    elif stage in ('challenge_build', 'solution_build'):
        expected = [tools['lake']['path'], 'build', policy['challenge_module' if stage == 'challenge_build' else 'solution_module']]
    elif stage in ('challenge_export', 'solution_export'):
        expected = [tools['leanexport']['path'], policy['challenge_module' if stage == 'challenge_export' else 'solution_module'], '--', *policy['declarations']]
    else:
        require(stage in STAGES[5:])
        # The first kernel's config is its single argument. The native kernel
        # also has a generated config in its readable binds, although it uses
        # only the export argument. The stock option grammar fixes their order.
        base = BASE_OPTIONS + ['--tmpfs', '/run', '--tmpfs', '/var']
        require(len(options) >= len(base) + 6)
        require(options[:len(base)] == base)
        offset = len(base)
        require(options[offset] == '--ro-bind')
        config = options[offset + 1]
        require(options[offset + 2] == config and options[offset + 3] == '--ro-bind')
        export = options[offset + 4]
        temporary_identity(config, policy)
        identity = temporary_identity(export, policy)
        document = decode(read_file(Path(config), 16384))
        require(canonical(document) == canonical({'use_stdin': False, 'export_file_path': export,
                             'permitted_axioms': policy['permitted_axioms'],
                             'unpermitted_axiom_hard_error': False, 'num_threads': 4,
                             'nat_extension': True, 'string_extension': True}))
        if stage == 'native_kernel':
            require(identity == previous_export)
        expected = ([tools['nanoda_bin']['path'], config] if stage == 'nanoda' else
                    [tools['leanchecker']['path'], '--silent', '--from-export', export])
    require(options == sandbox_options(policy, stage, config=config, export=export) and command == expected)
    tool = Path(command[0])
    require(tool.resolve(strict=True) == tool)
    key = 'lake' if stage in ('resolve', 'challenge_build', 'solution_build') else 'leanexport' if stage.endswith('_export') else 'nanoda_bin' if stage == 'nanoda' else 'leanchecker'
    require(hashlib.sha256(tool.read_bytes()).hexdigest() == tools[key]['sha256'])
    return identity


def load_measure(path=None):
    if path is None:
        installed = Path('/opt/lean-eval/replay-measure')
        path = installed if installed.exists() else Path(__file__).resolve().parents[1] / 'server/replay-image/replay-measure'
    loader = importlib.machinery.SourceFileLoader('_stock_perf_primitive', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    require(spec is not None)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def parent_death_guard():
    """The wrapper must not outlive the Lake process that originally owned bwrap."""
    parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.prctl(1, signal.SIGKILL, 0, 0, 0) == 0)  # PR_SET_PDEATHSIG
    if os.getppid() != parent or parent == 1:
        os.kill(os.getpid(), signal.SIGKILL)


def mark_invalid(policy, journal_path):
    try:
        with Journal(policy, journal_path) as journal:
            journal.value['invalid'] = True
            journal.save()
    except Exception:
        # Missing/unreadable/active/stale evidence never passes the reducer.
        pass


def run(policy, arguments, *, journal_path=JOURNAL_PATH, clock_path=CLOCK_PATH, real_bwrap=REAL_BWRAP,
        measure=None, clock=lambda: time.monotonic_ns() // 1_000_000):
    """Library entry point for a trusted future supervisor; argv is never edited."""
    command = [real_bwrap, *arguments]
    stage, started, observed = None, None, False
    measured_started = None
    def observe_started(started_ns):
        nonlocal measured_started
        try:
            require(observed)
            publish_start(policy, clock_path, stage, started_ns)
            measured_started = started_ns
        except Exception:
            # Missing clock proof invalidates this invocation, without changing
            # the mathematical child invocation or executing it twice.
            measured_started = None
    try:
        validate_policy(policy)
        require(str(Path.cwd()) == policy['project'])
        require(real_bwrap == policy['bubblewrap']['path'])
        require(Path(real_bwrap).resolve(strict=True) == Path(real_bwrap))
        require(hashlib.sha256(Path(real_bwrap).read_bytes()).hexdigest() == policy['bubblewrap']['sha256'])
        with Journal(policy, journal_path) as journal:
            value = journal.value
            require(not value['invalid'] and value['active'] is None and len(value['invocations']) < len(STAGES))
            stage = STAGES[len(value['invocations'])]
            value['kernel_export'] = match_invocation(policy, arguments, stage, value['kernel_export'])
            started = clock()
            require(integer(started, policy['binding']['started_ms']))
            value['active'] = {'stage': stage, 'started_ms': started}
            journal.save()
        observed = True
    except Exception:
        mark_invalid(policy, journal_path)
    if measure is None:
        try:
            measure = load_measure()
        except Exception:
            mark_invalid(policy, journal_path)
            # No child has started. This is the only safe fallback point.
            os.execv(real_bwrap, command)
    # run_measured preserves comparator-owned FDs and makes its perf control
    # FD noninheritable. Excluded calls open no counter at all.
    try:
        if observed and BUCKET[stage] is None:
            before = clock()
            code = subprocess.run(command, check=False, close_fds=False).returncode
            wall, counter = clock() - before, None
        else:
            code, wall, counter = measure.run_measured(command, best_effort=True, preserve_fds=True,
                                                      started_observer=observe_started)
    except Exception:
        mark_invalid(policy, journal_path)
        raise
    if observed:
        try:
            finished = clock()
            with Journal(policy, journal_path) as journal:
                require(journal.value['active'] == {'stage': stage, 'started_ms': started})
                require(BUCKET[stage] is None or measured_started is not None)
                journal.value['invocations'].append({'stage': stage, 'started_ms': started,
                    'finished_ms': finished, 'wall_time_ms': wall, 'termination': termination(code),
                    'measurement_started_ns': measured_started,
                    'counter': counter if BUCKET[stage] is not None else None})
                journal.value['active'] = None
                journal.save()
        except Exception:
            mark_invalid(policy, journal_path)
    return code


def failed_verdict(binding):
    return {'schema_version': 1, 'replay_task_id': binding['replay_task_id'], 'attempt': binding['attempt'],
            'execution_outcome': 'failed', 'checker_outcome': None,
            'failure_reason': 'verdict_invalid', 'statistics': None}


def sum_counter(records):
    counters = [r['counter'] for r in records]
    if not counters:
        return {'status': 'unavailable', 'reason': 'counter_not_reported'}
    for counter in counters:
        if counter['status'] == 'unavailable':
            return dict(counter)
    value = sum(c['value'] for c in counters)
    require(integer(value))
    return {'status': 'measured', 'value': value}


def reduce_trace(policy, value, driver, *, file_count, lines_of_code, timeout=None, measurement_start=None):
    """Build verdict1 from private evidence and a SUPERVISOR-owned driver record.

    Driver fields are the actual locked problem's lake-test termination and
    exact results.json data, not the enclosing Python evaluator's return code.
    A timeout instead requires a bound supervisor stop/cutoff record and, for
    an active included phase, the private actual-start slot read after stopping.
    """
    binding = validate_policy(policy)['binding']
    invalid = failed_verdict(binding)
    try:
        validate_journal(value, policy)
        require(not value['invalid'] and integer(file_count) and integer(lines_of_code))
        accepted = rejected = False
        if driver is not None:
            require(isinstance(driver, dict) and set(driver) == {'binding', 'termination', 'passed', 'statement_revisions'})
            validate_binding(driver['binding'])
            require(driver['binding'] == binding)
            termination_valid(driver['termination'])
            accepted = driver['passed'] == [binding['problem_id']] and canonical(driver['statement_revisions']) == canonical({binding['problem_id']: binding['statement_revision']})
            rejected = driver['passed'] == [] and driver['statement_revisions'] == {}
            require(accepted or rejected)
        require(driver is not None or timeout is not None)
        records = [dict(r) for r in value['invocations']]
        active = value['active']
        outcome, checker = 'completed', None
        if timeout is not None:
            require(isinstance(timeout, dict) and set(timeout) == {'binding', 'cutoff_ns', 'stopped'})
            validate_binding(timeout['binding'])
            require(timeout['binding'] == binding and timeout['stopped'] is True and not accepted)
            cutoff_ns = timeout['cutoff_ns']
            require(integer(cutoff_ns, binding['started_ms'] * 1_000_000,
                            (binding['started_ms'] + MAX_WALL_MS) * 1_000_000))
            if active is not None:
                require(cutoff_ns >= active['started_ms'] * 1_000_000)
                if BUCKET[active['stage']] is not None:
                    require(isinstance(measurement_start, dict)
                            and set(measurement_start) == {'policy_sha256', 'generation', 'stage', 'started_ns'})
                    require(measurement_start['policy_sha256'] == policy_digest(policy)
                            and measurement_start['generation'] == binding['generation']
                            and measurement_start['stage'] == active['stage'])
                    measured_ns = measurement_start['started_ns']
                    require(integer(measured_ns, active['started_ms'] * 1_000_000, cutoff_ns))
                    elapsed_ms = (cutoff_ns - measured_ns) // 1_000_000
                    require(elapsed_ms <= MAX_WALL_MS)
                    records.append({'stage': active['stage'], 'wall_time_ms': elapsed_ms,
                                    'counter': {'status': 'unavailable', 'reason': 'counter_not_reported'}})
            else:
                require(not records or cutoff_ns >= records[-1]['finished_ms'] * 1_000_000)
            outcome = 'timed_out'
        else:
            require(active is None)
            if driver['termination']['kind'] == 'signaled' or any(r['termination']['kind'] == 'signaled' for r in records):
                require(not accepted)
                outcome = 'crashed'
            else:
                # The prefix ending before kernels cannot distinguish source
                # rejection from parser/I/O failure in stock Lake's exit1.
                require(len(records) == len(STAGES))
                require(all(r['termination'] == {'kind': 'exited', 'code': 0} for r in records[:5]))
                kernels_ok = all(r['termination'] == {'kind': 'exited', 'code': 0} for r in records[5:])
                if accepted:
                    require(driver['termination'] == {'kind': 'exited', 'code': 0} and kernels_ok)
                    checker = 'accepted'
                else:
                    require(driver['termination']['kind'] == 'exited' and driver['termination']['code'] != 0 and not kernels_ok)
                    checker = 'declined'
        statistics = {'file_count': file_count, 'lines_of_code': lines_of_code}
        for bucket in ('build', 'checker'):
            included = [r for r in records if BUCKET[r['stage']] == bucket]
            statistics[bucket + '_wall_time_ms'] = sum(r['wall_time_ms'] for r in included)
            statistics[bucket + '_retired_instructions'] = sum_counter(included)
        return {**invalid, 'execution_outcome': outcome, 'checker_outcome': checker,
                'failure_reason': None, 'statistics': statistics}
    except (Invalid, TypeError, ValueError, KeyError):
        return invalid


def main(arguments):
    # Fixed paths only. Policy/real sandbox cannot be selected by argv or env.
    try:
        parent_death_guard()
    except Exception:
        # Direct exec restores the native parent relationship when an observer
        # cannot establish its own parent-death guard.
        os.execv(REAL_BWRAP, [REAL_BWRAP, *arguments])
    try:
        policy = validate_policy(decode(read_file(POLICY_PATH)))
        measure = load_measure('/opt/lean-eval/replay-measure')
    except Exception:
        # Only a failure BEFORE launching the child can delegate by exec.
        os.execv(REAL_BWRAP, [REAL_BWRAP, *arguments])
    try:
        returncode = run(policy, arguments, measure=measure)
    except Exception:
        # The child may already have run. Never execute it again to repair
        # observation; the supervisor must refuse incomplete evidence.
        return 125
    if returncode < 0:
        number = -returncode
        if number not in {signal.SIGKILL, signal.SIGSTOP}:
            signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)
    return returncode
