"""Inactive original stock workspace preparation; no admission or Source fetch.

Only independently pinned local Git objects are read. Generation is an owned
original build/CLI operation, never a supplied receipt or retained workspace.
Only trusted benchmark/compiler preparation runs. No submitted Source/checker,
provider or State execution is exposed.
"""
from __future__ import annotations

import hashlib
import contextlib
import copy
import ctypes
import math
import os
from pathlib import Path
import re
import resource
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib

import stock_replay_contract as contracts
import stock_replay_supervisor as supervisor
from evaluate_submission import (EvaluateError, _install_root_manifest, _share_packages, detect_matches,
                                 _overlay_single_file, _overlay_submission_dir)

MAX_BLOB = 16 * 1024 * 1024
MAX_FILES = 50000
CONFIG_FILES = ('lean-toolchain', 'lakefile.toml', 'lake-manifest.json')
GENERATOR_COMMIT = '0b8cc2d141710a18515c53448f006c971dd8eeb4'
PROBLEM = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
GIT = str(Path(shutil.which('git')).resolve(strict=True))
INVENTORY_CONCURRENCY = 4
INVENTORY_ADDRESS_SPACE = 32 * 1024**3


_GENERATION_SEAL = object()


class _OwnedGeneration:
    """In-process handoff only; serialized observations are never generation proof."""
    __slots__ = ('_record', '_seal')

    def __init__(self, record, seal):
        require(seal is _GENERATION_SEAL)
        self._record, self._seal = record, seal

    def observation(self):
        return copy.deepcopy(self._record)


class Invalid(ValueError):
    """Fixed local preparation failure, never includes Source/output/errors."""


def require(condition):
    if not condition:
        raise Invalid('original stock workspace preparation is unproved')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def environment(home, prefix=None):
    return {'PATH': (str(Path(prefix) / 'bin') + ':' if prefix else '') + '/usr/bin:/bin',
            'HOME': str(home), 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'file'}


class Git:
    """CAS reader: commit/tree/mode/blob, no checkout hooks, filters or network.

    The expected benchmark commit/tree come from the closed reviewed matrix.
    An arbitrary caller-computed SHA is not substituted for those identities.
    Other repositories' expected commits must come from the pinned manifest or
    a separately validated request/authority, never from their working tree.
    """
    def __init__(self, repository, commit, expected_tree=None):
        self.repository, self.commit, self.cache = Path(repository), commit, {}
        require(isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit))
        require(self.run('rev-parse', commit + '^{commit}').decode().strip() == commit)
        self.tree = self.run('rev-parse', commit + '^{tree}').decode().strip()
        require(re.fullmatch('[0-9a-f]{40}', self.tree) and (expected_tree is None or self.tree == expected_tree))
        self.entries = {}
        for row in self.run('ls-tree', '-rz', '--full-tree', commit).rstrip(b'\0').split(b'\0'):
            header, path = row.split(b'\t', 1)
            mode, kind, oid = header.decode().split()
            path = path.decode()
            require(path not in self.entries and not path.startswith('/') and '..' not in Path(path).parts)
            self.entries[path] = {'mode': mode, 'kind': kind, 'git_blob': oid}
        require(0 < len(self.entries) <= MAX_FILES)

    def run(self, *arguments):
        try:
            raw = _capture_preparation([GIT, '--no-replace-objects', '-c', 'core.fsmonitor=false',
                '-c', 'core.hooksPath=/dev/null', '-C', str(self.repository), *arguments],
                None, environment('/nonexistent'), 30)
        except (OSError, subprocess.SubprocessError):
            raise Invalid('required original Git object is unavailable') from None
        require(len(raw) <= MAX_BLOB)
        return raw

    def blob(self, path, *, symlink=False):
        entry = self.entries.get(path)
        modes = {'120000'} if symlink else {'100644', '100755'}
        require(entry is not None and entry['kind'] == 'blob' and entry['mode'] in modes)
        if path in self.cache:
            return self.cache[path]
        size = int(self.run('cat-file', '-s', entry['git_blob']))
        require(0 <= size <= MAX_BLOB)
        raw = self.run('cat-file', 'blob', entry['git_blob'])
        require(len(raw) == size and hashlib.sha1(b'blob ' + str(size).encode() + b'\0' + raw).hexdigest() == entry['git_blob'])
        self.cache[path] = raw
        return raw

    def pin(self, path):
        return {'path': path, 'git_blob': self.entries[path]['git_blob'], 'sha256': sha(self.blob(path))}

    def export(self, destination):
        destination = Path(destination)
        destination.mkdir(mode=0o700)
        for path in sorted(self.entries):
            raw = self.blob(path)  # refuses tracked symlinks/submodules
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 int(self.entries[path]['mode'][-3:], 8))
            with os.fdopen(descriptor, 'wb') as stream:
                os.fchmod(stream.fileno(), int(self.entries[path]['mode'][-3:], 8))
                stream.write(raw)

    def checkout_dependency(self, destination, public_url):
        """Fresh public local clone preserves Lake's actual Git-package layout."""
        require(public_url.startswith('https://github.com/') and '@' not in public_url)
        env = environment('/nonexistent')
        command = [GIT, '-c', 'core.hooksPath=/dev/null', 'clone', '--local', '--no-hardlinks',
                   '--no-checkout', '--', str(self.repository), str(destination)]
        try:
            invoke(command, None, env, 60)
            for arguments in (['config', 'remote.origin.url', public_url], ['-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', self.commit]):
                invoke([GIT, '-C', str(destination), *arguments], None, env, 60)
        except (OSError, subprocess.SubprocessError):
            raise Invalid('fresh original dependency checkout is unavailable') from None
        self.verify_dependency_checkout(destination)

    def verify_dependency_checkout(self, destination):
        require(self.run('-C', str(destination), 'rev-parse', 'HEAD').decode().strip() == self.commit)
        for path in self.entries:
            target = Path(destination) / path
            if self.entries[path]['mode'] == '120000':
                require(target.is_symlink() and os.readlink(target).encode() == self.blob(path, symlink=True))
            else:
                require(not target.is_symlink() and target.is_file() and target.read_bytes() == self.blob(path))


def selection(repository, commit, toolchain, problem):
    require(isinstance(problem, str) and PROBLEM.fullmatch(problem))
    selected = contracts.SELECTIONS.get((commit, toolchain))
    require(selected is not None)
    tree, directory = selected
    git = Git(repository, commit, tree)
    configuration = {name: git.blob(name if directory == '.' else directory + '/' + name) for name in CONFIG_FILES}
    require(configuration['lean-toolchain'] == (toolchain + '\n').encode())
    manifest_path = 'manifests/problems/' + problem + '.toml'
    manifest = tomllib.loads(git.blob(manifest_path).decode())
    require(manifest.get('id') == problem and type(manifest.get('statement_revision')) is int
            and manifest['statement_revision'] >= 1)
    prefix = 'generated/' + problem + '/'
    original = [path for path in git.entries if path.startswith(prefix)]
    require(original and {'Challenge.lean', 'Solution.lean', 'Submission.lean', 'WorkspaceTest.lean',
            'README.md', 'holes.json', 'config.json', 'lakefile.toml', 'lean-toolchain'} <= {path[len(prefix):] for path in original})
    require(git.blob(prefix + 'WorkspaceTest.lean') == git.blob('templates/WorkspaceTest.lean'))
    selected_manifest = supervisor.stock.decode(configuration['lake-manifest.json'])
    generator = [package for package in selected_manifest['packages'] if package['name'].strip('«»') == 'lean-eval-generator']
    require(len(generator) == 1 and generator[0]['rev'] == GENERATOR_COMMIT
            and generator[0]['url'] == 'https://github.com/leanprover/lean-eval-generator.git')
    return git, directory, configuration, manifest


def export_original(repository, commit, toolchain, problem, destination):
    """Fresh exact original checkout plus original supported configuration selector."""
    git, directory, configuration, manifest = selection(repository, commit, toolchain, problem)
    destination = Path(destination)
    git.export(destination)
    if directory != '.':
        # Execute the exact Git-verified original selector, not a reimplementation.
        invoke([sys.executable, '-I', str(destination / 'scripts/prepare_toolchain.py'),
            toolchain, '--apply'], destination, environment(destination.parent), 30)
    require(all((destination / name).read_bytes() == raw for name, raw in configuration.items()))
    return {'schema_version': 1, 'benchmark_commit': commit, 'benchmark_tree': git.tree,
            'toolchain': toolchain, 'problem_id': problem, 'statement_revision': manifest['statement_revision'],
            'generation_observed': False, 'qualification': 'not_established'}


def verify_checkout(git, source, configuration, *, selected_problem=None):
    for path in git.entries:
        if selected_problem is not None and path.startswith('generated/' + selected_problem + '/'):
            continue
        target = Path(source) / path
        require(not target.is_symlink() and target.is_file()
                and target.read_bytes() == configuration.get(path, git.blob(path)))


def _stop_preparation_groups(children, descriptors):
    """Signal only unreaped owned groups, then reap only those groups."""
    failed, reserved = False, []
    deadline = time.monotonic_ns() + 5_000_000_000
    # Reserve EVERY group until its last required kill: no leader is reaped
    # before all group signals have been issued.
    for child in children:
        if child.returncode is not None:
            failed = True  # identity was unexpectedly released; never signal it
            continue
        reserved.append(child)
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            failed = True
    for child in reserved:
        try:
            child.wait(timeout=max(0, (deadline - time.monotonic_ns()) / 1_000_000_000))
        except (OSError, subprocess.SubprocessError):
            failed = True
    for child in reserved:
        while True:
            try:
                pid, _status = os.waitpid(-child.pid, os.WNOHANG)
            except ChildProcessError:
                break
            except OSError:
                failed = True
                break
            if pid:
                continue
            if time.monotonic_ns() >= deadline:
                failed = True
                break
            time.sleep(0.01)
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            failed = True
    require(not failed)


@contextlib.contextmanager
def _owned_preparation_groups():
    """Dedicated synchronous trusted-preparer scope, never a shared job server.

    The process-local subreaper flag is restored on every path. Only this
    scope's reserved process groups are signalled/reaped; no waitpid(-1).
    Signals record cancellation rather than interrupting teardown/restoration.
    """
    # An auto-reaping SIGCHLD policy could release PID identity before WNOWAIT.
    # This entry is a dedicated synchronous preparation process, not a server.
    require(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL)
    children, descriptors = [], []
    cancelled = [False]
    previous, setting = {}, None
    libc = ctypes.CDLL(None, use_errno=True)
    def cancel(_number, _frame):
        cancelled[0] = True
    try:
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[number] = signal.signal(number, cancel)
        value = ctypes.c_int()
        require(libc.prctl(37, ctypes.byref(value), 0, 0, 0) == 0)
        setting = value.value  # restore even if SET or later setup is interrupted
        require(libc.prctl(36, 1, 0, 0, 0) == 0)
        yield children, descriptors, cancelled
    finally:
        try:
            _stop_preparation_groups(children, descriptors)
        finally:
            blocked = signal.pthread_sigmask(signal.SIG_BLOCK, set(previous))
            try:
                restored = setting is None or libc.prctl(36, setting, 0, 0, 0) == 0
                for number, handler in previous.items():
                    signal.signal(number, handler)
                require(restored)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        if cancelled[0]:
            raise supervisor.Interrupted()


def _preparation_command(command, source, env, timeout, *, capture=False):
    """Exact original command, bounded optional stdout, complete owned teardown."""
    require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0)
    deadline = time.monotonic_ns() + int(timeout * 1_000_000_000)
    code, process, output, eof = None, None, bytearray(), not capture
    def drain():
        nonlocal eof
        while not eof:
            try:
                chunk = os.read(process.stdout.fileno(), 65536)
            except BlockingIOError:
                return
            if not chunk:
                eof = True
                return
            require(len(output) + len(chunk) <= MAX_BLOB)
            output.extend(chunk)
    try:
        with _owned_preparation_groups() as (children, descriptors, cancelled):
            process = subprocess.Popen(command, cwd=source, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            children.append(process)
            descriptor = supervisor.pidfd_open(process.pid)
            descriptors.append(descriptor)
            if capture:
                os.set_blocking(process.stdout.fileno(), False)
            while code is None:
                if cancelled[0]:
                    raise supervisor.Interrupted()
                readers = [descriptor] + ([process.stdout.fileno()] if not eof else [])
                ready, _, _ = select.select(readers, [], [], min(0.05, _inventory_remaining(deadline)))
                _inventory_remaining(deadline)
                if capture and process.stdout.fileno() in ready:
                    drain()
                if descriptor in ready:
                    status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    require(status is not None)
                    code = status.si_status if status.si_code == os.CLD_EXITED else -status.si_status
        # Every owned writer is stopped/reaped before the final bounded drain.
        # An inherited pipe held outside the owned group is not accepted as EOF.
        if capture:
            drain()
            require(eof)
        require(process.returncode == code and code == 0)
        return bytes(output)
    except (OSError, subprocess.SubprocessError):
        raise Invalid('original stock builder/generator did not complete') from None
    finally:
        if process is not None and process.stdout is not None:
            process.stdout.close()


def invoke(command, source, env, timeout):
    # All exits quiesce/reap owned groups before accepting real leader status.
    _preparation_command(command, source, env, timeout)


def _capture_preparation(command, source, env, timeout):
    return _preparation_command(command, source, env, timeout, capture=True)


def _original_inventory_modules(git):
    """Original loadManifest/uniqueModules order: filename sort, first occurrence."""
    paths = sorted(path for path in git.entries if path.startswith('manifests/problems/'))
    require(0 < len(paths) <= 1001)
    modules = []
    for path in paths:
        if Path(path).parts[2] == '.DS_Store':
            continue
        require(len(Path(path).parts) == 3 and path.endswith('.toml'))
        entry = tomllib.loads(git.blob(path).decode())
        require(isinstance(entry.get('id'), str) and entry['id'] == Path(path).stem and PROBLEM.fullmatch(entry['id']))
        module = entry.get('module')
        require(isinstance(module, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*', module))
        git.blob(module.replace('.', '/') + '.lean')
        if module not in modules:
            modules.append(module)
    require(modules)
    return tuple(modules)


def _inventory_remaining(deadline):
    remaining = (deadline - time.monotonic_ns()) / 1_000_000_000
    require(remaining > 0)
    return remaining


def _build_inventory_batch(modules, source, env, lake, deadline):
    """Four trusted original single-module builds; no submitted Source runs.

    WNOWAIT keeps each owned group leader unreaped until group termination,
    preventing a completed leader's PID/group identity from being reused.
    Every exit path terminates groups and waits all direct children. This is
    trusted-build process-group ownership, not Source namespace qualification.
    """
    require(0 < len(modules) <= INVENTORY_CONCURRENCY and hasattr(os, 'WNOWAIT'))
    observed = {}
    values = dict(env, LEAN_NUM_THREADS='1')
    def limits():
        def restrict(kind, maximum):
            inherited = resource.getrlimit(kind)
            limit = min([maximum] + [value for value in inherited if value != resource.RLIM_INFINITY])
            resource.setrlimit(kind, (limit, limit))
        restrict(resource.RLIMIT_AS, INVENTORY_ADDRESS_SPACE)
        restrict(resource.RLIMIT_CPU, math.ceil(_inventory_remaining(deadline)))
    with _owned_preparation_groups() as (children, descriptors, cancelled):
        try:
            for module in modules:
                _inventory_remaining(deadline)
                child = subprocess.Popen([str(lake), 'build', module], cwd=source, env=values,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, preexec_fn=limits)
                children.append(child)
                descriptors.append(supervisor.pidfd_open(child.pid))
            pending = set(range(len(children)))
            while pending:
                if cancelled[0]:
                    raise supervisor.Interrupted()
                ready, _, _ = select.select([descriptors[index] for index in pending], [], [],
                                             min(0.05, _inventory_remaining(deadline)))
                _inventory_remaining(deadline)
                for index in tuple(pending):
                    if descriptors[index] not in ready:
                        continue
                    status = os.waitid(os.P_PID, children[index].pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    require(status is not None and status.si_code == os.CLD_EXITED and status.si_status == 0)
                    observed[index] = time.monotonic_ns()
                    pending.remove(index)
        except (OSError, subprocess.SubprocessError):
            raise Invalid('original inventory prebuild did not complete') from None
    require(len(observed) == len(modules) and all(child.returncode == 0 for child in children))
    return [{'module': module, 'exit_code': children[index].returncode,
             'observed_finished_ns': observed[index],
             'olean_sha256': supervisor.file_hash(Path(source) / '.lake/build/lib/lean' /
                 (module.replace('.', '/') + '.olean'))['sha256']}
            for index, module in enumerate(modules)]


def _prepare_inventory(git, source, env, lake, timeout_seconds):
    """One deadline covers original structure check, ALL builds and real inventory."""
    started = time.monotonic_ns()
    require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 1200)
    deadline = started + timeout_seconds * 1_000_000_000
    modules = _original_inventory_modules(git)
    invoke([str(lake), 'exe', 'lean-eval', 'validate-manifest', '--structure-only'],
           source, env, _inventory_remaining(deadline))
    builds = []
    for offset in range(0, len(modules), INVENTORY_CONCURRENCY):
        builds.extend(_build_inventory_batch(modules[offset:offset + INVENTORY_CONCURRENCY],
                                             source, env, lake, deadline))
    require(tuple(build['module'] for build in builds) == modules
            and all(build['exit_code'] == 0 for build in builds))
    argv = ['lake', 'exe', 'lean-eval', 'validate-manifest', '--assume-modules-built']
    # Original supported optimization skips rebuilds only; the actual tool
    # still imports EVERY original module and checks the full semantic inventory.
    invoke([str(lake), *argv[1:]], source, env, _inventory_remaining(deadline))
    _inventory_remaining(deadline)
    return {'argv': argv, 'timeout_seconds': timeout_seconds, 'elapsed_ns': time.monotonic_ns() - started,
            'module_plan_sha256': sha(supervisor.stock.canonical(modules)), 'module_builds': builds,
            'concurrency': INVENTORY_CONCURRENCY, 'per_process_address_space_upper_bound_bytes': INVENTORY_ADDRESS_SPACE,
            'lean_thread_hint': 1}


def resolved_generator_argv(command, problem_statement_revisions):
    """Explicit inactive contract clarification, never a generic argv escape.

    lock1.command names the original entrypoint. Selected preparation derives
    exactly one original --problem selector from the validated sole-key map.
    Full-catalog inventory synchronization is not claimed by that invocation.
    """
    require(command == ['lake', 'exe', 'lean-eval', 'generate']
            and isinstance(problem_statement_revisions, dict) and len(problem_statement_revisions) == 1)
    problem, revision = next(iter(problem_statement_revisions.items()))
    require(isinstance(problem, str) and PROBLEM.fullmatch(problem)
            and type(revision) is int and revision >= 1)
    return [*command, '--problem', problem]


def _install_local_package_overrides(workspace, manifest, dependencies):
    """Resolve every exact-pin-verified dependency locally after Git scrub.

    The original Git manifest remains unchanged. Lean Pool keeps the original
    helper's private cache; other packages keep its read-only source aliases.
    """
    workspace = Path(workspace)
    objects = {destination.name: (git, destination) for git, destination in dependencies}
    packages = manifest['packages']
    require(len(objects) == len(packages)
            and set(objects) == {package['name'].strip('«»') for package in packages})
    overrides = []
    for package in sorted(packages, key=lambda value: value['name'].strip('«»')):
        name = package['name'].strip('«»')
        git, destination = objects[name]
        require(git.commit == package['rev'])
        git.verify_dependency_checkout(destination)
        target = workspace / '.lake/packages' / name
        require(target.is_dir())
        resolved = target.resolve(strict=True)
        require((name == 'lean-pool' and resolved == target and not target.is_symlink())
                or (name != 'lean-pool' and resolved == destination.resolve(strict=True)))
        overrides.append({'type': 'path', 'name': package['name'], 'dir': str(resolved),
            'manifestFile': package['manifestFile'], 'configFile': package['configFile'],
            'inherited': package['inherited']})
    (workspace / '.lake/package-overrides.json').write_bytes(
        supervisor.stock.canonical({'version': '1.2.0', 'packages': overrides}) + b'\n')


def _strip_owned_git_metadata(packages):
    """Only fresh owned public dependency clones, after Git/source verification."""
    packages = Path(packages)
    require(packages.is_absolute() and packages.resolve(strict=True) == packages
            and packages.is_dir() and packages.stat().st_uid == os.geteuid())
    for directory, subdirectories, _ in os.walk(packages, followlinks=False):
        if '.git' in subdirectories:
            target = Path(directory) / '.git'
            require(not target.is_symlink() and target.resolve(strict=True).is_relative_to(packages))
            shutil.rmtree(target)
            subdirectories.remove('.git')


def _trusted_path(value, forbidden):
    require(isinstance(value, str) and 0 < len(value) <= 8192)
    canonical = []
    for item in value.split(':'):
        require(item and Path(item).is_absolute())
        path = Path(item).resolve(strict=True)
        require(path.is_dir() and path.stat().st_mode & 0o022 == 0
                and all(not path.is_relative_to(Path(root)) for root in forbidden))
        canonical.append(str(path))
    return ':'.join(canonical)


def generate_original(repository, commit, toolchain, problem, session, *, toolchain_prefix,
                      dependency_repositories, timeout_seconds=300, inventory_timeout_seconds=1200, driver_path=None):
    # The fixed synchronous preparer owns every trusted subprocess across its
    # whole build/generation path, including normal termination signals.
    with supervisor.termination_guard():
        return _generate_original(repository, commit, toolchain, problem, session,
            toolchain_prefix=toolchain_prefix, dependency_repositories=dependency_repositories,
            timeout_seconds=timeout_seconds, inventory_timeout_seconds=inventory_timeout_seconds,
            driver_path=driver_path)


def _generate_original(repository, commit, toolchain, problem, session, *, toolchain_prefix,
                      dependency_repositories, timeout_seconds=300, inventory_timeout_seconds=1200, driver_path=None):
    """Own the original build and generation, without accepting build receipts.

    Dependencies are fresh exact Git exports at the selected manifest's commits;
    no arbitrary working-tree/cache/retained generated output is imported.
    This deliberately does not fabricate unavailable dependency build caches.
    The stock release/runtime must be independently authenticated before calling;
    version strings and these component observations do not qualify an image.
    """
    git, _, configuration, manifest = selection(repository, commit, toolchain, problem)
    session, prefix = supervisor.stock.absolute(str(session)), Path(toolchain_prefix)
    require(prefix.is_absolute() and prefix.resolve(strict=True) == prefix
            and session.resolve(strict=False) == session
            and type(timeout_seconds) is int and 1 <= timeout_seconds <= 300
            and type(inventory_timeout_seconds) is int and 1 <= inventory_timeout_seconds <= 1200)
    session.mkdir(mode=0o700)
    source, home = session / 'source', session / 'home'
    home.mkdir(mode=0o700)
    export_original(repository, commit, toolchain, problem, source)
    env = environment(home, prefix)
    if driver_path is not None:
        env['PATH'] = driver_path
    env['PATH'] = _trusted_path(env['PATH'], (source, home))
    env['ELAN_TOOLCHAIN'] = toolchain
    env['TMPDIR'] = str(home)
    version = _capture_preparation([str(prefix / 'bin/lean'), '--version'], None, env, 30).decode()
    require(contracts.STOCK_COMPONENTS[toolchain][0] in version)
    packages = source / '.lake/packages'
    packages.mkdir(parents=True)
    selected_manifest = supervisor.stock.decode(configuration['lake-manifest.json'])
    dependency_objects = []
    for package in selected_manifest['packages']:
        name = package['name'].strip('«»')
        require(package['type'] == 'git' and package.get('subDir') is None and name in dependency_repositories)
        dependency = Git(dependency_repositories[name], package['rev'])
        dependency.checkout_dependency(packages / name, package['url'])
        dependency_objects.append((dependency, packages / name))
    def verify_inputs(*, generated_selected=False):
        verify_checkout(git, source, configuration, selected_problem=problem if generated_selected else None)
        for dependency, destination in dependency_objects:
            dependency.verify_dependency_checkout(destination)
    verify_checkout(git, source, configuration)
    # Original Mathlib cache helper, in the selected original root context.
    # Its pinned source derives artifact keys; no caller-supplied cache receipt
    # or arbitrary compiled workspace is imported as generation provenance.
    invoke([str(prefix / 'bin/lake'), 'exe', 'cache', 'get'], source, env, timeout_seconds)
    verify_inputs()
    restore = 'scripts/fetch_dependency_caches.sh'
    require(restore in git.entries)
    # Original cache-only TauCeti restoration; no exported shell text is eval'd
    # and no caller endpoint/credential is inherited into this owned phase.
    invoke(['/bin/bash', str(source / restore), '--restore', str(source)], source, env, timeout_seconds)
    verify_inputs()
    invoke([str(prefix / 'bin/lake'), 'build', 'lean-eval', 'extract_theorem', 'eval_inventory'], source, env, timeout_seconds)
    verify_inputs()
    # Preserve the original unselected generator's semantic guard. The original
    # validator builds every original statement module and runs eval_inventory;
    # this trusted PUBLIC preparation budget is separate from runtime M/T and
    # from the unchanged selected-generator timeout.
    inventory = _prepare_inventory(git, source, env, prefix / 'bin/lake', inventory_timeout_seconds)
    verify_inputs()
    invoke([str(prefix / 'bin/lake'), 'exe', 'lean-eval', 'validate-generated-catalog'], source, env, timeout_seconds)
    # Retained committed output is not selected generation evidence. Only the
    # bound problem is deleted; other original/catalog data stay intact.
    selected_directory = source / 'generated' / problem
    require(selected_directory.is_dir() and not selected_directory.is_symlink())
    shutil.rmtree(selected_directory)
    argv = resolved_generator_argv(['lake', 'exe', 'lean-eval', 'generate'],
                                   {problem: manifest['statement_revision']})
    invoke([str(prefix / 'bin/lake'), *argv[1:]], source, env, timeout_seconds)
    verify_inputs(generated_selected=True)
    workspace = session / 'workspace'
    shutil.copytree(source / 'generated' / problem, workspace, symlinks=False)
    require((workspace / 'WorkspaceTest.lean').read_bytes() == git.blob('templates/WorkspaceTest.lean'))
    require((workspace / 'lean-toolchain').read_bytes() == configuration['lean-toolchain'])
    _install_root_manifest(workspace, source / 'lake-manifest.json')
    require(_share_packages(workspace, packages) is None)
    _install_local_package_overrides(workspace, selected_manifest, dependency_objects)
    # WorkspaceTest imports only trusted Lean; never prime Challenge, Solution
    # or Submission. No submitter data has entered this workspace.
    invoke([str(prefix / 'bin/lake'), 'build', 'workspace_test'], workspace, env, timeout_seconds)
    verify_inputs(generated_selected=True)
    _strip_owned_git_metadata(packages)
    driver_environment = _driver_environment(workspace, prefix, env['PATH'], home, toolchain)
    prepared = supervisor.workspace_manifest(workspace)
    lock, _ = _configuration_lock(repository, commit, toolchain, problem, workspace)
    return _OwnedGeneration({'schema_version': 1, 'generation_observed': True, 'benchmark_commit': commit,
        'benchmark_tree': git.tree, 'toolchain': toolchain, 'problem_id': problem,
        'toolchain_prefix': str(prefix),
        'tool_sha256': {name: supervisor.file_hash(prefix / 'bin' / name)['sha256']
                       for name in (*sorted(supervisor.stock.TOOL_NAMES), 'lean')},
        'generator_binary_sha256': supervisor.file_hash(source / '.lake/build/bin/lean-eval')['sha256'],
        'statement_revision': manifest['statement_revision'], 'workspace': str(workspace),
        'prepared_manifest': prepared, 'configuration_lock': lock, 'driver_environment': driver_environment,
        'resolved_generator_argv': argv,
        'manifest_inventory_validation': inventory,
        'qualification': 'not_established'}, _GENERATION_SEAL)


def _driver_environment(workspace, prefix, driver_path, home, toolchain):
    """Probe original Lake before Source overlay and before freezing C/cache."""
    workspace, prefix, home = Path(workspace), Path(prefix), Path(home)
    driver_path = _trusted_path(driver_path, (workspace, home))
    env = environment(home, prefix)
    env.update(PATH=driver_path, ELAN_TOOLCHAIN=toolchain, TMPDIR=str(home))
    def probe(variable, values):
        try:
            raw = _capture_preparation([str(prefix / 'bin/lake'), 'env', 'printenv', variable], workspace, values, 30)
        except (OSError, subprocess.SubprocessError):
            raise Invalid('original driver environment could not be established') from None
        require(0 < len(raw) <= 8192)
        return raw.decode().strip()
    inherited = []
    roots = [workspace.resolve(), (workspace / '.lake').resolve()]
    for item in probe('PATH', env).split(':'):
        if not item:
            continue
        path = Path(item)
        if path.is_dir():
            path = path.resolve(strict=True)
            if all(not path.is_relative_to(root) for root in roots):
                inherited.append(str(path))
    require(inherited)
    inherited_path = ':'.join(inherited)
    resolved = {'PATH': inherited_path, 'HOME': '/tmp/home', 'LEAN_ABORT_ON_PANIC': '1'}
    return {'driver_path': driver_path, 'inherited_path': inherited_path,
            'resolved_path': probe('PATH', resolved), 'lean_path': probe('LEAN_PATH', resolved)}


def _configuration_lock(repository, commit, toolchain, problem, workspace):
    """One problem per C: legal existing schema, no global closure substitution."""
    git, directory, _, manifest = selection(repository, commit, toolchain, problem)
    workspace = Path(workspace)
    require(tomllib.loads((workspace / 'lakefile.toml').read_text()).get('name') == problem)
    holes = supervisor.stock.decode((workspace / 'holes.json').read_bytes())
    require(holes.get('id') == problem and holes.get('module') == manifest['module']
            and (workspace / 'WorkspaceTest.lean').read_bytes() == git.blob('templates/WorkspaceTest.lean'))
    paths = sorted(path for path in git.entries if path.startswith('EvalTools/') and path.endswith('.lean'))
    paths.append('manifests/problems/' + problem + '.toml')
    require(re.fullmatch('[A-Za-z_][A-Za-z0-9_.]*', manifest['module']))
    paths.append(manifest['module'].replace('.', '/') + '.lean')
    paths += ['scripts/fetch_dependency_caches.sh', 'scripts/lake_cache_curl.py']
    require(len(paths) <= 64)
    value = {'schema_version': 1, 'kind': 'stock_benchmark_configuration_lock',
        'benchmark_repository': 'leanprover/lean-eval', 'benchmark_commit': commit,
        'benchmark_tree': git.tree, 'selection': {'kind': 'root' if directory == '.' else 'toolchain_directory', 'path': directory},
        'toolchain': toolchain, 'selected_files': {name: {key: item for key, item in git.pin(name if directory == '.' else directory + '/' + name).items() if key != 'path'} for name in CONFIG_FILES},
        'supported_toolchains': None if directory == '.' else git.pin('supported-toolchains.json'),
        'prepare_toolchain': None if directory == '.' else git.pin('scripts/prepare_toolchain.py'),
        'workspace_driver': git.pin('templates/WorkspaceTest.lean'),
        'generator_source_binding': {'sources': [git.pin(path) for path in sorted(paths)],
            'command': ['lake', 'exe', 'lean-eval', 'generate'],
            'derived_output_manifest_sha256': supervisor.derived_manifest_digest(supervisor.workspace_manifest(workspace))},
        'manifest_layout': 'per_problem_v1', 'problem_statement_revisions': {problem: manifest['statement_revision']}}
    files = {path: git.blob(path) for path in paths + ['templates/WorkspaceTest.lean'] +
             [name if directory == '.' else directory + '/' + name for name in CONFIG_FILES] +
             ([] if directory == '.' else ['supported-toolchains.json', 'scripts/prepare_toolchain.py'])}
    contracts.validate_configuration_lock(value, files)
    return value, files


def overlay_public_git(request, source_repository, workspace, *, benchmark_repository, authority_repository):
    """Exact public Git Source overlay only; no network or Source execution.

    Archive/private decryption is deliberately not replaced by a caller hash.
    """
    source = request['source']
    require(source['visibility'] == 'public' and source['fetch_method'] == 'git'
            and source['commit'] == request['result']['commit'])
    lock = request['benchmark']['configuration_lock']
    benchmark, directory, _, _ = selection(benchmark_repository, request['benchmark']['commit'],
        request['benchmark']['toolchain'], request['result']['problem_id'])
    paths = [item['path'] for item in lock['generator_source_binding']['sources']] + ['templates/WorkspaceTest.lean']
    paths += [name if directory == '.' else directory + '/' + name for name in CONFIG_FILES]
    if directory != '.':
        paths += ['supported-toolchains.json', 'scripts/prepare_toolchain.py']
    locator = request['benchmark']['configuration_authority']
    authority = Git(authority_repository, locator['commit'])
    contracts.validate_stock_request(request, benchmark_files={path: benchmark.blob(path) for path in paths},
        authority_raw=authority.blob(locator['path']), authority_commit=authority.commit, authority_path=locator['path'])
    git = Git(source_repository, source['commit'])
    url = git.run('config', '--local', '--get', 'remote.origin.url').decode().strip()
    require(url in {'https://github.com/' + source['repository'], 'https://github.com/' + source['repository'] + '.git'})
    problem = request['result']['problem_id']
    workspace = Path(workspace)
    before = supervisor.derived_manifest_digest(supervisor.workspace_manifest(workspace))
    require(before == lock['generator_source_binding']['derived_output_manifest_sha256'])
    # Preserve the existing discovery, pristine-skip, duplicate and symlink
    # semantics. Only discovery data/Source files are materialized, never an
    # executable source config or a submitter Solution/Challenge replacement.
    with tempfile.TemporaryDirectory(prefix='stock-owned-source-') as directory:
        temporary = Path(directory)
        source_root, generated = temporary / 'source', temporary / 'pristine'
        source_root.mkdir()
        (generated / problem).mkdir(parents=True)
        shutil.copyfile(workspace / 'Submission.lean', generated / problem / 'Submission.lean')
        count, total = 0, 0
        for path, entry in sorted(git.entries.items()):
            parts = Path(path).parts
            relevant = Path(path).name in {'lakefile.toml', 'Submission.lean'} or (
                'Submission' in parts and path.endswith('.lean'))
            if not relevant and entry['mode'] != '120000':
                continue
            count += 1
            require(count <= 4096)
            raw = git.blob(path, symlink=entry['mode'] == '120000')
            total += len(raw)
            require(total <= 1024**3)
            target = source_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry['mode'] == '120000':
                os.symlink(raw.decode(), target)
            else:
                target.write_bytes(raw)
        try:
            with open(os.devnull, 'w') as sink, contextlib.redirect_stderr(sink):
                candidates = [match for match in detect_matches(source_root, {problem}, generated_root=generated)
                              if match.skip_reason is None]
        except (EvaluateError, OSError, UnicodeError):
            raise Invalid('selected Source discovery is unproved') from None
        require(len(candidates) == 1)
        selected = candidates[0].source_dir
        require(not (selected / 'Submission.lean').is_symlink() and (selected / 'Submission.lean').stat().st_size > 0)
        try:
            _overlay_single_file(selected / 'Submission.lean', workspace / 'Submission.lean')
            overlaid = _overlay_submission_dir(selected / 'Submission', workspace / 'Submission')
        except (EvaluateError, OSError, UnicodeError):
            raise Invalid('selected Source overlay is unproved') from None
        observed_files = [workspace / 'Submission.lean'] + [workspace / 'Submission' / path for path in overlaid]
        lines = sum(len(path.read_bytes().splitlines()) for path in observed_files)
    after = supervisor.workspace_manifest(workspace)
    require(supervisor.derived_manifest_digest(after) == before)
    return {'source_commit': git.commit, 'source_tree': git.tree,
            'source_sha256': after['source_sha256'], 'overlaid_files': len(observed_files), 'lines_of_code': lines}


def finalize_public_context(request, generated, *, source_repository, benchmark_repository,
                            authority_repository, lean_repository, toolchain_prefix,
                            bubblewrap, temporary_directory, instrumentation=None,
                            python='/usr/bin/python3'):
    """Handoff only: exact owned generation + independently validated request.

    This returns a supervisor template, never runs Source/lake test or grants
    task authority. No private/archive path or decryption shortcut is supplied.
    Runtime/image qualification remains an independent caller prerequisite.
    """
    require(isinstance(generated, _OwnedGeneration) and generated._seal is _GENERATION_SEAL)
    generated = generated.observation()
    require(generated['generation_observed'] is True
            and generated['configuration_lock'] == request['benchmark']['configuration_lock'])
    require(generated['resolved_generator_argv'] == resolved_generator_argv(
        generated['configuration_lock']['generator_source_binding']['command'],
        generated['configuration_lock']['problem_statement_revisions']))
    inventory = generated['manifest_inventory_validation']
    require(inventory['argv'] == ['lake', 'exe', 'lean-eval', 'validate-manifest', '--assume-modules-built']
            and type(inventory['timeout_seconds']) is int and 1 <= inventory['timeout_seconds'] <= 1200
            and type(inventory['elapsed_ns']) is int and inventory['elapsed_ns'] >= 0)
    for field in ('benchmark_commit', 'toolchain'):
        require(generated[field] == request['benchmark']['commit' if field == 'benchmark_commit' else field])
    require(generated['problem_id'] == request['result']['problem_id']
            and generated['statement_revision'] == request['result']['statement_revision'])
    workspace, prefix = Path(generated['workspace']), Path(toolchain_prefix)
    require(generated['toolchain_prefix'] == str(prefix)
            and generated['tool_sha256'] == {name: supervisor.file_hash(prefix / 'bin' / name)['sha256']
                for name in (*sorted(supervisor.stock.TOOL_NAMES), 'lean')})
    require(generated['prepared_manifest'] == supervisor.workspace_manifest(workspace))
    temporary = supervisor.stock.absolute(str(temporary_directory))
    require(temporary.resolve(strict=False) == temporary and not temporary.is_relative_to(workspace)
            and not workspace.is_relative_to(temporary) and not temporary.is_relative_to(prefix)
            and not prefix.is_relative_to(temporary))
    temporary.mkdir(mode=0o700)
    config = supervisor.stock.decode((workspace / 'config.json').read_bytes())
    stock_commit = contracts.STOCK_COMPONENTS[generated['toolchain']][0]
    lean_git = Git(lean_repository, stock_commit)
    check = lean_git.blob('src/lake/Lake/CLI/Check.lean')
    require(check == (prefix / 'src/lean/lake/Lake/CLI/Check.lean').read_bytes())
    declarations = _export_declarations(check, config)
    binding = {name: request[name] for name in ('replay_task_id', 'attempt', 'execution_profile_digest', 'measurement_config_digest')}
    # Explicit template slots only. The supervisor replaces these with a fresh
    # generation and actual monotonic start before go; no execution is claimed.
    binding.update(generation='0' * 64, started_ms=0, problem_id=request['result']['problem_id'],
                   statement_revision=request['result']['statement_revision'])
    tools = {name: {'path': str(prefix / 'bin' / name), 'sha256': supervisor.file_hash(prefix / 'bin' / name)['sha256']}
             for name in supervisor.stock.TOOL_NAMES}
    policy = {'schema_version': 1, 'binding': binding, 'project': str(workspace),
        'lean_prefix': str(prefix), 'lake_home': str(prefix), 'temporary_directory': str(temporary),
        'challenge_module': config['challenge_module'], 'solution_module': config['solution_module'],
        'declarations': declarations, 'permitted_axioms': config['permitted_axioms'], 'tools': tools,
        'bubblewrap': {'path': str(bubblewrap), 'sha256': supervisor.file_hash(bubblewrap)['sha256']},
        **{key: generated['driver_environment'][key] for key in ('inherited_path', 'resolved_path', 'lean_path')}}
    overlay = overlay_public_git(request, source_repository, workspace,
        benchmark_repository=benchmark_repository, authority_repository=authority_repository)
    git, directory, _, _ = selection(benchmark_repository, generated['benchmark_commit'],
                                   generated['toolchain'], generated['problem_id'])
    lock = generated['configuration_lock']
    paths = [item['path'] for item in lock['generator_source_binding']['sources']] + ['templates/WorkspaceTest.lean']
    paths += [name if directory == '.' else directory + '/' + name for name in CONFIG_FILES]
    if directory != '.':
        paths += ['supported-toolchains.json', 'scripts/prepare_toolchain.py']
    locator = request['benchmark']['configuration_authority']
    authority = Git(authority_repository, locator['commit'])
    context = supervisor.context_from_request(request, policy,
        benchmark_files={path: git.blob(path) for path in paths}, authority_raw=authority.blob(locator['path']),
        authority_commit=authority.commit, authority_path=locator['path'],
        driver_path=generated['driver_environment']['driver_path'], file_count=overlay['overlaid_files'],
        lines_of_code=overlay['lines_of_code'], instrumentation=instrumentation, python=python)
    return {'policy': policy, 'context': context}


def _export_declarations(verified_check_source, config):
    """Literal stock ordering; input is verified against the pinned Lean Git."""
    text = verified_check_source.decode()
    require(text.count('def primitiveTargets') == 1 and text.count('def builtinTargets') == 1)
    primitive_text = text.split('def primitiveTargets')[1].split('def builtinTargets')[0]
    primitive_text = '\n'.join(line.split('--', 1)[0] for line in primitive_text.splitlines())
    primitive = re.findall(r'``([A-Za-z_][A-Za-z0-9_.]*)', primitive_text)
    require(primitive)
    builtin = ['Quot', 'Quot.mk', 'Quot.lift', 'Quot.ind'] if 'Quot.sound' in config['permitted_axioms'] else []
    definitions = config.get('definition_names')
    require(definitions is None or isinstance(definitions, list))
    return builtin + config['theorem_names'] + config['permitted_axioms'] + primitive + (definitions or [])
