"""Inactive preparer evidence: real Git objects, no submitter/runtime access."""
from __future__ import annotations

import copy
import json
import os
import shutil
import time
from unittest import mock
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_stock_workspace as prepare
from test_stock_replay_supervisor import Fixture
from test_stock_replay_contract import refresh

BENCHMARK = Path('/home/kim/worktrees/lean-eval/lean-eval-pr-review')
COMMIT = '8955681b9914d7ebc7d286414843021b7d6c9de1'
TOOLCHAIN = 'leanprover/lean4:v4.35.0'


def bind_fixture_metadata(fixture, problem):
    # Controlled schema fixture only, never original generation evidence.
    _, _, _, manifest = prepare.selection(BENCHMARK, COMMIT, TOOLCHAIN, problem)
    path = fixture.project / 'lakefile.toml'
    path.write_text(path.read_text().replace('name = "fixture"', 'name = "' + problem + '"'))
    (fixture.project / 'holes.json').write_text(json.dumps({'id': problem, 'module': manifest['module']}))


def commit_files(root, files, *, public_repository=None):
    root.mkdir()
    env = prepare.environment(root)
    env.update(GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
               GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
    def git(*args):
        return subprocess.check_output([prepare.GIT, '-C', str(root), *args], env=env, stderr=subprocess.DEVNULL)
    git('init', '-q')
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    git('add', '.')
    git('commit', '-qm', 'controlled public fixture')
    if public_repository:
        git('remote', 'add', 'origin', 'https://github.com/' + public_repository + '.git')
    return git('rev-parse', 'HEAD').decode().strip()


class GitObjectTests(unittest.TestCase):
    def test_serialized_generation_observation_is_not_an_owned_handoff(self):
        with self.assertRaises(prepare.Invalid):
            prepare.finalize_public_context({}, {'generation_observed': True},
                source_repository='/nonexistent', benchmark_repository='/nonexistent',
                authority_repository='/nonexistent', lean_repository='/nonexistent',
                toolchain_prefix='/nonexistent', bubblewrap='/nonexistent',
                temporary_directory='/nonexistent')

    def test_archive_or_private_source_never_enters_the_public_git_reader(self):
        for visibility in ('public', 'private'):
            request = {'source': {'visibility': visibility, 'fetch_method': 'archive'}}
            with self.assertRaises(prepare.Invalid):
                prepare.overlay_public_git(request, '/nonexistent-source', '/nonexistent-workspace',
                    benchmark_repository='/nonexistent-benchmark', authority_repository='/nonexistent-authority')

    def test_fresh_dependency_preserves_pinned_git_bytes_then_scrubs_only_owned_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / 'repository'
            commit = commit_files(repository, {'Library.lean': b'-- original pinned public dependency\n'})
            git = prepare.Git(repository, commit)
            packages = root / 'fresh-packages'
            packages.mkdir()
            git.checkout_dependency(packages / 'library', 'https://github.com/fixture/library.git')
            self.assertTrue((packages / 'library/.git').is_dir())
            prepare._strip_owned_git_metadata(packages)
            self.assertFalse((packages / 'library/.git').exists())
            self.assertEqual((packages / 'library/Library.lean').read_bytes(), git.blob('Library.lean'))
            self.assertTrue((repository / '.git').is_dir())

    def test_real_stock_lake_resolves_all_local_packages_after_git_scrub(self):
        prefix = Path('/tmp/lean-eval-435rc3-checker-compat/lean-4.35.0-rc3-linux')
        if not (prefix / 'bin/lake').is_file():
            self.skipTest('existing offline official stock Lake required')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = root / 'packages'
            packages.mkdir()
            entries, dependencies = [], []
            for name in ('ordinary', 'lean-pool'):
                repository = root / ('repository-' + name)
                commit = commit_files(repository, {'lakefile.toml': ('name = "' + name + '"\n').encode(),
                    'Original.lean': b'-- pinned original public fixture\n'})
                git = prepare.Git(repository, commit)
                destination = packages / name
                url = 'https://github.com/fixture/' + name + '.git'
                git.checkout_dependency(destination, url)
                dependencies.append((git, destination))
                entries.append({'type': 'git', 'name': '«lean-pool»' if name == 'lean-pool' else name, 'url': url, 'rev': commit,
                    'inputRev': commit, 'scope': '', 'subDir': None, 'inherited': False,
                    'manifestFile': 'lake-manifest.json', 'configFile': 'lakefile.toml'})
            workspace = root / 'workspace'
            workspace.mkdir()
            requires = ''.join('\n[[require]]\nname = "' + item['name'].strip('«»') + '"\ngit = "' + item['url'] +
                '"\nrev = "' + item['rev'] + '"\n' for item in entries)
            (workspace / 'lakefile.toml').write_text('name = "workspace"\n' + requires)
            (workspace / 'lean-toolchain').write_text('leanprover/lean4:v4.35.0-rc3\n')
            manifest = {'version': '1.3.0', 'packagesDir': '.lake/packages', 'packages': entries,
                        'name': 'workspace', 'lakeDir': '.lake', 'fixedToolchain': False}
            manifest_raw = prepare.supervisor.stock.canonical(manifest) + b'\n'
            (workspace / 'lake-manifest.json').write_bytes(manifest_raw)
            self.assertIsNone(prepare._share_packages(workspace, packages))
            with self.assertRaises(prepare.Invalid):
                prepare._install_local_package_overrides(workspace, manifest, dependencies[:-1])
            original = packages / 'ordinary/Original.lean'
            original.write_text('-- changed unproved dependency\n')
            with self.assertRaises(prepare.Invalid):
                prepare._install_local_package_overrides(workspace, manifest, dependencies)
            original.write_bytes(b'-- pinned original public fixture\n')
            prepare._install_local_package_overrides(workspace, manifest, dependencies)
            overrides = json.loads((workspace / '.lake/package-overrides.json').read_bytes())
            self.assertEqual({item['name'].strip('«»') for item in overrides['packages']}, {'ordinary', 'lean-pool'})
            self.assertEqual(next(item for item in overrides['packages'] if item['name'] == '«lean-pool»')['dir'],
                             str(workspace / '.lake/packages/lean-pool'))
            prepare._strip_owned_git_metadata(packages)
            env = prepare.environment(root, prefix)
            for _ in range(2):
                result = subprocess.run([str(prefix / 'bin/lake'), 'env'],
                    cwd=workspace, env=env, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertNotIn(b'fetching', result.stderr)
                self.assertFalse(any(path.name == '.git' for path in packages.rglob('*')))
                self.assertEqual((workspace / 'lake-manifest.json').read_bytes(), manifest_raw)

    def test_selected_generator_contract_resolves_only_the_bound_single_problem(self):
        command = ['lake', 'exe', 'lean-eval', 'generate']
        self.assertEqual(prepare.resolved_generator_argv(command, {'two_plus_two': 1}),
            command + ['--problem', 'two_plus_two'])
        for problems in ({}, {'two_plus_two': 1, 'another': 1}, {'../escape': 1}, {'two_plus_two': True}):
            with self.assertRaises(prepare.Invalid):
                prepare.resolved_generator_argv(command, problems)
        with self.assertRaises(prepare.Invalid):
            prepare.resolved_generator_argv(command + ['--check'], {'two_plus_two': 1})

    def test_real_git_object_reader_ignores_working_tree_and_rejects_wrong_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / 'repository'
            commit = commit_files(repository, {'file': b'original Git bytes\n'})
            git = prepare.Git(repository, commit)
            (repository / 'file').write_text('dirty working tree')
            self.assertEqual(git.blob('file'), b'original Git bytes\n')
            with self.assertRaises(prepare.Invalid):
                prepare.Git(repository, commit, '0' * 40)
            git.export(root / 'export')
            self.assertEqual((root / 'export/file').read_bytes(), b'original Git bytes\n')
            with self.assertRaises(FileExistsError):
                git.export(root / 'export')

    def test_trusted_builder_timeout_does_not_emit_generation_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(prepare.Invalid):
                prepare.invoke([sys.executable, '-c', 'import time;time.sleep(10)'], root,
                    prepare.environment(root), 0.05)

    def test_actual_stock_export_order_matches_independent_original_argv_fixture(self):
        source = Path('/tmp/lean-eval-435stable-stock-compat/lean-4.35.0-linux/src/lean/lake/Lake/CLI/Check.lean')
        if not source.is_file():
            self.skipTest('existing offline official stock Source required')
        config = {'theorem_names': ['compatibility_goal'], 'permitted_axioms': ['propext', 'Quot.sound', 'Classical.choice']}
        fixture = json.loads((ROOT / 'tests/fixtures/stock-lake-original-bwrap-v1.json').read_bytes())
        self.assertEqual(prepare._export_declarations(source.read_bytes(), config), fixture['declarations'])
        config['definition_names'] = ['an_original_definition']
        self.assertEqual(prepare._export_declarations(source.read_bytes(), config)[-1], 'an_original_definition')


class InventoryPreparationTests(unittest.TestCase):
    def fixture(self, root, modules):
        files = {}
        for index, module in enumerate(modules):
            files['manifests/problems/problem' + str(index) + '.toml'] = (
                'id = "problem' + str(index) + '"\nmodule = "' + module + '"\n').encode()
            files[module.replace('.', '/') + '.lean'] = b'-- controlled ownership fixture; never Lean qualification\n'
        repository = root / 'source'
        commit = commit_files(repository, files)
        executable = root / 'owned-lake-fixture'
        executable.write_text('#!' + str(Path(sys.executable).resolve()) + '\n' + r'''import json,os,resource,sys,time,tomllib
from pathlib import Path
root=Path.cwd();args=sys.argv[1:]
with(root/'events').open('a')as h:h.write(json.dumps({'argv':args,'pid':os.getpid(),'as_limit':resource.getrlimit(resource.RLIMIT_AS)[0],'threads':os.getenv('LEAN_NUM_THREADS')})+'\n')
if args[0]=='build':
 module=args[1]
 if os.getenv('FIXTURE_BARRIER'):
  ready=root/'ready';ready.mkdir(exist_ok=True);(ready/module).write_text(str(os.getpid()));deadline=time.monotonic()+3
  while len(list(ready.iterdir()))<int(os.getenv('FIXTURE_BARRIER')):
   assert time.monotonic()<deadline;time.sleep(.005)
 if os.getenv('FIXTURE_FAILURE')==module:sys.exit(7)
 time.sleep(float(os.getenv('FIXTURE_SLEEP','0')))
 target=root/'.lake/build/lib/lean'/(module.replace('.','/')+'.olean');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'controlled artifact, not a Lean object')
elif '--assume-modules-built'in args:
 modules={tomllib.loads(p.read_text())['module']for p in(root/'manifests/problems').glob('*.toml')}
 if not all((root/'.lake/build/lib/lean'/(m.replace('.','/')+'.olean')).is_file()for m in modules):sys.exit(8)
else:
 assert args==['exe','lean-eval','validate-manifest','--structure-only']
''')
        executable.chmod(0o700)
        return prepare.Git(repository, commit), repository, executable

    def test_original_ignored_entry_and_closed_module_operand_rules(self):
        valid = {'manifests/problems/first.toml': b'id = "first"\nmodule = "Alpha"\n',
                 'Alpha.lean': b'-- controlled public fixture\n'}
        for ignored in ('manifests/problems/.DS_Store', 'manifests/problems/.DS_Store/ignored.toml'):
            with tempfile.TemporaryDirectory() as directory:
                repository = Path(directory) / 'repository'
                commit = commit_files(repository, {**valid, ignored: b'not UTF8/TOML: \xff'})
                self.assertEqual(prepare._original_inventory_modules(prepare.Git(repository, commit)), ('Alpha',))
        for extra in ('manifests/problems/other.txt', 'manifests/problems/nested/other.toml',
                      'manifests/problems/.not-DS_Store'):
            with tempfile.TemporaryDirectory() as directory:
                repository = Path(directory) / 'repository'
                commit = commit_files(repository, {**valid, extra: b'not an original manifest'})
                with self.assertRaises(prepare.Invalid):
                    prepare._original_inventory_modules(prepare.Git(repository, commit))
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / 'repository'
            commit = commit_files(repository, {'manifests/problems/first.toml': b'id = "first"\nmodule = "--help"\n',
                                               '--help.lean': b'-- cannot become a command flag\n'})
            with self.assertRaises(prepare.Invalid):
                prepare._original_inventory_modules(prepare.Git(repository, commit))

    def run_owned(self, operation):
        real, children = subprocess.Popen, []
        def launch(*args, **kwargs):
            child = real(*args, **kwargs)
            children.append(child)
            return child
        try:
            with mock.patch.object(prepare.subprocess, 'Popen', side_effect=launch):
                return operation()
        finally:
            self.assertTrue(children)
            for child in children:
                self.assertIsNotNone(child.returncode)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(child.pid, os.WNOHANG)

    def test_public_owner_guard_stops_and_reaps_real_child_on_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / 'owner.py'
            runner.write_text("import json,os,signal,subprocess,sys\n" +
                "sys.path.insert(0," + repr(str(ROOT / 'scripts')) + ")\n" +
                "import prepare_stock_workspace as p\n" +
                "real=subprocess.Popen;children=[]\n" +
                "def launch(*a,**kw):\n c=real(*a,**kw);children.append(c);return c\n" +
                "p.subprocess.Popen=launch\n" +
                "def operation(*a,**kw):\n p.invoke([sys.executable,'-c'," +
                repr('import os,signal,time;os.kill(os.getppid(),signal.SIGTERM);time.sleep(10)') +
                "]," + repr(str(root)) + ",p.environment(" + repr(str(root)) + "),3)\n" +
                "p._generate_original=operation\n" +
                "try:p.generate_original(None,None,None,None,None,toolchain_prefix=None,dependency_repositories={})\n" +
                "except p.supervisor.Interrupted:pass\n" +
                "else:raise AssertionError('termination was not observed')\n" +
                "assert len(children)==1 and children[0].returncode==-signal.SIGKILL\n" +
                "try:os.waitpid(children[0].pid,os.WNOHANG)\n" +
                "except ChildProcessError:pass\n" +
                "else:raise AssertionError('owned child was not reaped')\n" +
                "print(json.dumps({'interrupted':True,'reaped':True}))\n")
            result = subprocess.run([sys.executable, '-I', str(runner)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout), {'interrupted': True, 'reaped': True})

    def test_all_module_order_dedup_and_actual_child_exit_before_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git, source, lake = self.fixture(root, ('Beta', 'Alpha', 'Gamma', 'Delta', 'Beta'))
            self.assertEqual(prepare._original_inventory_modules(git), ('Beta', 'Alpha', 'Gamma', 'Delta'))
            env = dict(prepare.environment(root), FIXTURE_BARRIER='4')
            observation = self.run_owned(lambda: prepare._prepare_inventory(git, source, env, lake, 3))
            self.assertEqual([entry['module'] for entry in observation['module_builds']], ['Beta', 'Alpha', 'Gamma', 'Delta'])
            self.assertTrue(all(entry['exit_code'] == 0 for entry in observation['module_builds']))
            events = [json.loads(line) for line in (source / 'events').read_text().splitlines()]
            self.assertEqual(events[-1]['argv'], ['exe', 'lean-eval', 'validate-manifest', '--assume-modules-built'])
            builds = [entry for entry in events if entry['argv'][0] == 'build']
            self.assertEqual({entry['argv'][1] for entry in builds}, {'Alpha', 'Beta', 'Gamma', 'Delta'})
            self.assertTrue(all(entry['as_limit'] == 32 * 1024**3 and entry['threads'] == '1' for entry in builds))

    def test_partial_actual_build_failure_stops_and_reaps_all_owned_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git, source, lake = self.fixture(root, ('Slow', 'Bad', 'SlowTwo', 'SlowThree', 'Unstarted'))
            env = dict(prepare.environment(root), FIXTURE_FAILURE='Bad', FIXTURE_SLEEP='10', FIXTURE_BARRIER='4')
            started = time.monotonic()
            with self.assertRaises(prepare.Invalid):
                self.run_owned(lambda: prepare._prepare_inventory(git, source, env, lake, 3))
            self.assertLess(time.monotonic() - started, 2)
            events = [json.loads(line) for line in (source / 'events').read_text().splitlines()]
            self.assertFalse(any('--assume-modules-built' in entry['argv'] for entry in events))
            self.assertFalse(any(entry['argv'] == ['build', 'Unstarted'] for entry in events))

    def test_fourth_launch_or_pidfd_failure_reaps_every_started_child(self):
        for failing_setup in ('launch', 'pidfd'):
            with self.subTest(failing_setup=failing_setup), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _, source, lake = self.fixture(root, ('One', 'Two', 'Three', 'Four'))
                env = dict(prepare.environment(root), FIXTURE_BARRIER='4', FIXTURE_SLEEP='10')
                real_popen, real_pidfd = subprocess.Popen, prepare.supervisor.pidfd_open
                children, calls = [], {'launch': 0, 'pidfd': 0}
                def launch(*args, **kwargs):
                    calls['launch'] += 1
                    if failing_setup == 'launch' and calls['launch'] == 4:
                        raise OSError('controlled fourth launch failure')
                    child = real_popen(*args, **kwargs)
                    children.append(child)
                    return child
                def pidfd(pid):
                    calls['pidfd'] += 1
                    if failing_setup == 'pidfd' and calls['pidfd'] == 4:
                        raise OSError('controlled fourth pidfd failure')
                    return real_pidfd(pid)
                with mock.patch.object(prepare.subprocess, 'Popen', side_effect=launch), \
                     mock.patch.object(prepare.supervisor, 'pidfd_open', side_effect=pidfd):
                    with self.assertRaises(prepare.Invalid):
                        prepare._build_inventory_batch(('One', 'Two', 'Three', 'Four'), source, env, lake,
                                                       time.monotonic_ns() + 3_000_000_000)
                self.assertEqual(len(children), 3 if failing_setup == 'launch' else 4)
                for child in children:
                    self.assertIsNotNone(child.returncode)
                    with self.assertRaises(ChildProcessError):
                        os.waitpid(child.pid, os.WNOHANG)

    def test_one_shared_deadline_cannot_reset_between_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git, source, lake = self.fixture(root, ('One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine'))
            env = dict(prepare.environment(root), FIXTURE_SLEEP='0.45')
            started = time.monotonic()
            with self.assertRaises(prepare.Invalid):
                self.run_owned(lambda: prepare._prepare_inventory(git, source, env, lake, 1))
            self.assertLess(time.monotonic() - started, 2)
            events = [json.loads(line) for line in (source / 'events').read_text().splitlines()]
            self.assertFalse(any('--assume-modules-built' in entry['argv'] for entry in events))


class TrustedInvokeOwnershipTests(unittest.TestCase):
    def test_incompatible_auto_reaper_refuses_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = ("import ctypes,signal,sys\n" +
                "sys.path.insert(0," + repr(str(ROOT / 'scripts')) + ")\n" +
                "import prepare_stock_workspace as p\n" +
                "signal.signal(signal.SIGCHLD,signal.SIG_IGN)\n" +
                "try:p.invoke(['/nonexistent'],None,p.environment('/nonexistent'),1)\n" +
                "except p.Invalid:pass\nelse:raise AssertionError('auto reaper accepted')\n" +
                "assert signal.getsignal(signal.SIGCHLD)==signal.SIG_IGN\n")
            result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_both_inventory_phase_failures_reap_actual_same_group_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'phase-regression.py'
            script.write_text("import ctypes,json,os,signal,sys,tempfile\nfrom pathlib import Path\n" +
                "sys.path[:0]=" + repr([str(ROOT / 'scripts'), str(ROOT / 'tests')]) + "\n" +
                "import prepare_stock_workspace as p\nfrom test_prepare_stock_workspace import InventoryPreparationTests\n" +
                "assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0\nrows=[]\n" +
                "for phase in('structure','inventory'):\n" +
                " with tempfile.TemporaryDirectory()as directory:\n  root=Path(directory);git,source,lake=InventoryPreparationTests().fixture(root,('Alpha',))\n" +
                "  worker=" + repr("import json,os,subprocess,sys,time\nfrom pathlib import Path\na=sys.argv[1:];phase='structure'if'--structure-only'in a else'inventory'if'--assume-modules-built'in a else'build'\nif phase==FAIL:\n child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);Path('descendant').write_text(str(child.pid));raise SystemExit(7)\nif phase=='build':\n p=Path('.lake/build/lib/lean/Alpha.olean');p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'owned control, not a Lean object')\n") + ".replace('FAIL',repr(phase))\n" +
                "  lake.write_text('#!'+str(Path(sys.executable).resolve())+'\\n'+worker);lake.chmod(0o700)\n" +
                "  try:p._prepare_inventory(git,source,p.environment(root),lake,3)\n" +
                "  except p.Invalid:pass\n  else:raise AssertionError('failure accepted')\n" +
                "  pid=int((source/'descendant').read_text());reaped=False\n" +
                "  try:status=os.waitpid(pid,os.WNOHANG)\n  except ChildProcessError:reaped=True\n" +
                "  else:\n   if status==(0,0):os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)\n" +
                "  assert reaped,'phase returned with an unreaped descendant'\n  rows.append(phase)\n" +
                "print(json.dumps(rows))\n")
            result = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout), ['structure', 'inventory'])

    def test_capture_exact_binary_and_size_failure_have_bounded_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = bytes(range(256)) * 4096
            raw = prepare._capture_preparation([sys.executable, '-c',
                'import sys;sys.stdout.buffer.write(bytes(range(256))*4096)'], root, prepare.environment(root), 3)
            self.assertEqual(raw, expected)
            # The original Git/stdout16MiB bound is enforced while draining;
            # failure still traverses the shared owned-group teardown.
            with self.assertRaises(prepare.Invalid):
                prepare._capture_preparation([sys.executable, '-c',
                    'import sys;sys.stdout.buffer.write(b"x"*(16*1024*1024+1))'], root, prepare.environment(root), 3)

    def test_real_descendants_all_exits_prior_settings_and_unrelated_children(self):
        # An OUTER disposable subreaper cleans probes even if a regression makes
        # the tested controller (whose prior flag may be zero) lose ownership.
        controller_code = r'''import ctypes,json,os,signal,subprocess,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import prepare_stock_workspace as p
root=Path(sys.argv[2]);mode=sys.argv[3];prior=int(sys.argv[4]);capture=bool(int(sys.argv[5]));libc=ctypes.CDLL(None)
assert libc.prctl(36,prior,0,0,0)==0
saved={n:signal.getsignal(n)for n in(signal.SIGTERM,signal.SIGINT,signal.SIGHUP)}
real=subprocess.Popen
live=real([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
exited=real([sys.executable,'-c','raise SystemExit(23)'],start_new_session=True)
os.waitid(os.P_PID,exited.pid,os.WEXITED|os.WNOWAIT)
children=[]
def launch(*a,**kw):
 child=real(*a,**kw);children.append(child);return child
p.subprocess.Popen=launch
try:
 outcome='success'
 try:
  value=(p._capture_preparation if capture else p.invoke)([sys.executable,str(root/'worker.py'),mode],root,p.environment(root),.75 if mode=='timeout' else 3)
  if capture:assert value==bytes(range(256))*4096
 except p.Invalid:outcome='invalid'
 except p.supervisor.Interrupted:outcome='interrupted'
 assert outcome=={'success':'success','nonzero':'invalid','timeout':'invalid','signal':'interrupted'}[mode],outcome
 assert len(children)==1 and children[0].returncode=={'success':0,'nonzero':7,'timeout':-9,'signal':-9}[mode]
 identity=json.loads((root/'identity.json').read_text());pid=identity['descendant']
 try:os.waitpid(pid,os.WNOHANG)
 except ChildProcessError:pass
 else:raise AssertionError('owned descendant not reaped')
 status=Path(f'/proc/{pid}/stat')
 if status.exists():assert status.read_text().rsplit(')',1)[1].split()[19]!=identity['start_ticks'],'original descendant still exists'
 value=ctypes.c_int();assert libc.prctl(37,ctypes.byref(value),0,0,0)==0 and value.value==prior
 assert all(signal.getsignal(n)==old for n,old in saved.items())
 assert live.poll()is None,'unrelated live child was signalled'
 status=os.waitid(os.P_PID,exited.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
 assert status is not None and status.si_status==23,'unrelated exited child was reaped'
 print(json.dumps({'mode':mode,'prior':prior,'capture':capture,'descendant_reaped':True,'setting_restored':True,'unrelated_preserved':True}))
finally:
 if live.poll()is None:live.kill()
 live.wait();assert exited.wait()==23
'''
        worker_code = r'''import json,os,signal,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
ticks=Path(f'/proc/{child.pid}/stat').read_text().rsplit(')',1)[1].split()[19]
Path('identity.json').write_text(json.dumps({'descendant':child.pid,'leader':os.getpid(),'start_ticks':ticks}))
sys.stdout.buffer.write(bytes(range(256))*4096);sys.stdout.buffer.flush()
if sys.argv[1]=='signal':os.kill(os.getppid(),signal.SIGTERM)
if sys.argv[1]in('signal','timeout'):time.sleep(60)
raise SystemExit(7 if sys.argv[1]=='nonzero' else 0)
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'controller.py').write_text(controller_code)
            (root / 'worker-template').write_text(worker_code)
            harness = root / 'harness.py'
            harness.write_text("import ctypes,json,os,signal,subprocess,sys\nfrom pathlib import Path\n" +
                "root=Path(sys.argv[1]);assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0;rows=[]\n" +
                "for prior,capture in((0,0),(1,0),(0,1),(1,1)):\n for mode in('success','nonzero','timeout','signal'):\n" +
                "  case=root/(str(prior)+'-'+str(capture)+'-'+mode);case.mkdir();(case/'worker.py').write_text((root/'worker-template').read_text())\n" +
                "  result=subprocess.run([sys.executable,'-I',str(root/'controller.py'),sys.argv[2],str(case),mode,str(prior),str(capture)],capture_output=True,timeout=8)\n" +
                "  identity=case/'identity.json'\n" +
                "  if identity.exists():\n   pid=json.loads(identity.read_text())['descendant']\n" +
                "   try:\n    status=os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)\n" +
                "    if status is None:os.kill(pid,signal.SIGKILL)\n    os.waitpid(pid,0)\n" +
                "   except ChildProcessError:pass\n" +
                "  assert result.returncode==0,result.stderr.decode()\n  rows.append(json.loads(result.stdout))\n" +
                "print(json.dumps(rows))\n")
            result = subprocess.run([sys.executable, '-I', str(harness), str(root), str(ROOT / 'scripts')],
                                    capture_output=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            rows = json.loads(result.stdout)
            self.assertEqual(len(rows), 16)
            self.assertTrue(all(row['descendant_reaped'] and row['setting_restored'] and row['unrelated_preserved'] for row in rows))


@unittest.skipUnless((BENCHMARK / '.git').exists(), 'existing offline public benchmark Git objects required')
class OriginalStockTests(unittest.TestCase):
    def test_controlled_original_cli_inventory_success_and_semantic_rejection(self):
        # Small controlled PUBLIC fixture exercising the genuine original CLI,
        # not a cold full-family preparer run or a historical task qualification.
        donor = Path('/tmp/nix-shell.NgJZc4/lean-eval-stock-original-208-builder-2rbvrxx_/source')
        prefix = Path('/tmp/lean-eval-435rc3-checker-compat/lean-4.35.0-rc3-linux')
        compiler = Path('/tmp/lean-eval-stock-remaining-compat/435rc3/clang-host')
        if not (donor / '.lake/build/bin/lean-eval').is_file() or not compiler.is_file():
            self.skipTest('existing genuine offline original CLI/dependency cache required')
        commit = '208b6b22d396f28771dba56414b19f47cae5f2a7'
        git, _, selected, manifest = prepare.selection(BENCHMARK, commit, 'leanprover/lean4:v4.35.0-rc3', 'two_plus_two')
        paths = [path for path in git.entries if path.startswith('EvalTools/') and path.endswith('.lean')]
        manifest_path = 'manifests/problems/two_plus_two.toml'
        paths += list(prepare.CONFIG_FILES) + [manifest['module'].replace('.', '/') + '.lean']
        # The original module tags three declarations. Preserve every original
        # manifest for that module; one-problem inventory would falsely narrow it.
        paths += [path for path in git.entries if path.startswith('manifests/problems/') and path.endswith('.toml')
                  and prepare.tomllib.loads(git.blob(path).decode())['module'] == manifest['module']]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'controlled-public-original-cli'
            original = {path: git.blob(path) for path in paths}
            original.update(selected)
            fixture_commit = commit_files(project, original)
            fixture_git = prepare.Git(project, fixture_commit)
            self.assertIsNone(prepare._share_packages(project, donor / '.lake/packages'))
            packages = json.loads(selected['lake-manifest.json'])['packages']
            overrides = [{'type': 'path', 'name': item['name'],
                'dir': str((project / '.lake/packages' / item['name'].strip('«»')).resolve()),
                'manifestFile': item['manifestFile'], 'configFile': item['configFile'],
                'inherited': item['inherited']} for item in packages]
            (project / '.lake/package-overrides.json').write_bytes(prepare.supervisor.stock.canonical(
                {'version': '1.2.0', 'packages': overrides}))
            subprocess.run(['cp', '--reflink=auto', '-a', str(donor / '.lake/build'), str(project / '.lake/build')], check=True)
            env = prepare.environment(root, prefix)
            env['PATH'] = ':'.join(dict.fromkeys([str(prefix / 'bin')] + [str(Path(shutil.which(name)).resolve().parent)
                for name in ('env', 'bash', 'git') if shutil.which(name)]))
            env['LEAN_CC'] = str(compiler)
            for name in ('NIX_LD', 'NIX_LD_LIBRARY_PATH', 'LD_LIBRARY_PATH'):
                if name in os.environ:
                    env[name] = os.environ[name]
            real_popen = subprocess.Popen
            with open('/tmp/lean-eval-stock-preparer-genuine-inventory-child.log', 'wb') as log:
                def logged(*args, **kwargs):
                    if args[0][0] == str(prefix / 'bin/lake'):
                        kwargs.update(stdout=log, stderr=subprocess.STDOUT)
                    return real_popen(*args, **kwargs)
                with mock.patch.object(prepare.subprocess, 'Popen', side_effect=logged):
                    observation = prepare._prepare_inventory(fixture_git, project, env, prefix / 'bin/lake', 120)
            self.assertEqual([entry['module'] for entry in observation['module_builds']], [manifest['module']])
            self.assertEqual(observation['argv'], ['lake', 'exe', 'lean-eval', 'validate-manifest', '--assume-modules-built'])
            # A real original compiled inventory must reject changed manifest
            # metadata; the assume flag cannot replace semantic validation.
            path = project / manifest_path
            lines = path.read_text().splitlines()
            self.assertTrue(any(line.startswith('holes = ') for line in lines))
            path.write_text('\n'.join('holes = ["missing_inventory_theorem"]' if line.startswith('holes = ') else line
                                      for line in lines) + '\n')
            with self.assertRaises(prepare.Invalid):
                prepare.invoke([str(prefix / 'bin/lake'), *observation['argv'][1:]], project, env, 30)

    def test_all_original_inventory_modules_conserved_in_original_order(self):
        # Frozen independently from the three original Git catalogs and the
        # pinned original Core.Manifest loadManifest/uniqueModules semantics.
        expected = '7e84c623aa7ebab350a750882ceb620b110a5edda93761de9e673d6388a09202'
        for commit, toolchain in prepare.contracts.SELECTIONS:
            git, _, _, _ = prepare.selection(BENCHMARK, commit, toolchain, 'two_plus_two')
            modules = prepare._original_inventory_modules(git)
            self.assertEqual(len(modules), 296)
            self.assertEqual(prepare.sha(prepare.supervisor.stock.canonical(modules)), expected)

    def test_all_four_actual_original_selections_are_bound(self):
        for commit, toolchain in prepare.contracts.SELECTIONS:
            with self.subTest(commit=commit, toolchain=toolchain):
                git, directory, files, manifest = prepare.selection(BENCHMARK, commit, toolchain, 'two_plus_two')
                self.assertEqual(git.tree, prepare.contracts.SELECTIONS[(commit, toolchain)][0])
                self.assertEqual(files['lean-toolchain'], (toolchain + '\n').encode())
                self.assertEqual(manifest['statement_revision'], 1)
                self.assertEqual(directory == '.', commit != COMMIT)
        with self.assertRaises(prepare.Invalid):
            prepare.selection(BENCHMARK, COMMIT, 'leanprover/lean4:v4.32.0', 'two_plus_two')
        with self.assertRaises(prepare.Invalid):
            prepare.selection(BENCHMARK, COMMIT, TOOLCHAIN, '../escape')

    def test_actual_original_selector_and_complete_generated_files_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'original'
            proof = prepare.export_original(BENCHMARK, COMMIT, TOOLCHAIN, 'two_plus_two', target)
            self.assertFalse(proof['generation_observed'])
            self.assertEqual(proof['qualification'], 'not_established')
            self.assertFalse((target / '.git').exists())
            self.assertFalse((target / '.lake').exists())
            git, _, selected, _ = prepare.selection(BENCHMARK, COMMIT, TOOLCHAIN, 'two_plus_two')
            for name, raw in selected.items():
                self.assertEqual((target / name).read_bytes(), raw)
            for problem in ('two_plus_two', 'H1_not_closedComplemented'):
                prefix = 'generated/' + problem + '/'
                for path in git.entries:
                    if path.startswith(prefix):
                        self.assertEqual((target / path).read_bytes(), git.blob(path))
            self.assertTrue((target / 'generated/two_plus_two/README.md').is_file())
            self.assertTrue((target / 'generated/two_plus_two/holes.json').is_file())
            self.assertTrue((target / 'generated/H1_not_closedComplemented/ChallengeDeps.lean').is_file())

    def test_per_problem_configuration_is_legal_but_not_a_generation_claim(self):
        # Synthetic prepared binaries are schema tests only. They do not enter
        # generate_original or assert that original generation was observed.
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), real=False)
            bind_fixture_metadata(f, 'two_plus_two')
            lock, files = prepare._configuration_lock(BENCHMARK, COMMIT, TOOLCHAIN, 'two_plus_two', f.project)
            self.assertIs(prepare.contracts.validate_configuration_lock(lock, files), lock)
            self.assertEqual(lock['problem_statement_revisions'], {'two_plus_two': 1})
            path = f.project / 'lakefile.toml'
            path.write_text(path.read_text().replace('name = "two_plus_two"', 'name = "H1_not_closedComplemented"'))
            bind_fixture_metadata(f, 'H1_not_closedComplemented')
            other, _ = prepare._configuration_lock(BENCHMARK, COMMIT, TOOLCHAIN, 'H1_not_closedComplemented', f.project)
            self.assertNotEqual(prepare.contracts.configuration_digest(lock), prepare.contracts.configuration_digest(other))

    def test_public_git_overlay_checks_actual_git_authority_and_never_copies_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            f = Fixture(root, real=False)
            bind_fixture_metadata(f, 'two_plus_two')
            original = (f.project / 'lakefile.toml').read_bytes()
            source_root = root / 'owned-source-fixture'
            source_commit = commit_files(source_root, {
                'task/lakefile.toml': b'name = "two_plus_two"\n# Source config must never replace trusted config\n',
                'task/Submission.lean': b'-- controlled untrusted fixture\n',
                'task/Submission/Helper.lean': b'-- controlled helper\n',
                'task/Solution.lean': b'-- malicious replacement ignored\n'}, public_repository='synthetic-owner/submission')
            request = json.loads((ROOT / 'tests/fixtures/stock-replay-contract-v1.json').read_bytes())['request']
            request['result'].update(problem_id='two_plus_two', statement_revision=1, commit=source_commit)
            from results_schema import result_id
            request['result']['result_id'] = result_id('synthetic-owner', 'fixture-model', 'two_plus_two', 1)
            request['source'] = {'visibility': 'public', 'fetch_method': 'git', 'archive_capacity_class': None,
                'repository': 'synthetic-owner/submission', 'commit': source_commit}
            request['execution_profile']['source_capability'] = {'fetch_method': 'git', 'archive_capacity_class': None}
            request['network']['fetch_phase'] = 'public_https_only'
            lock, _ = prepare._configuration_lock(BENCHMARK, COMMIT, TOOLCHAIN, 'two_plus_two', f.project)
            request['benchmark']['configuration_lock'] = lock
            locator = request['benchmark']['configuration_authority']
            receipt = {'schema_version': 1, 'result_id': request['result']['result_id'], 'benchmark_commit': COMMIT,
                'toolchain': TOOLCHAIN, 'run_id': locator['run_id'], 'run_attempt': locator['run_attempt']}
            raw = prepare.supervisor.stock.canonical(receipt)
            locator.update(path='verification/' + receipt['result_id'] + '.json', sha256=prepare.sha(raw))
            authority = root / 'owned-authority-fixture'
            locator['commit'] = commit_files(authority, {locator['path']: raw})
            refresh(request)
            result = prepare.overlay_public_git(request, source_root, f.project,
                benchmark_repository=BENCHMARK, authority_repository=authority)
            self.assertEqual(result['source_commit'], source_commit)
            self.assertEqual((f.project / 'lakefile.toml').read_bytes(), original)
            self.assertEqual((f.project / 'Solution.lean').read_text(), '-- trusted generated wrapper fixture\n')
            self.assertEqual((f.project / 'Submission.lean').read_bytes(), b'-- controlled untrusted fixture\n')
            self.assertTrue((f.project / 'Submission/Helper.lean').is_file())
            changed = copy.deepcopy(request)
            changed['source']['repository'] = 'other/source'
            with self.assertRaises(prepare.Invalid):
                prepare.overlay_public_git(changed, source_root, f.project,
                    benchmark_repository=BENCHMARK, authority_repository=authority)


if __name__ == '__main__':
    unittest.main()
