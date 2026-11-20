"""Inactive preparer evidence: real Git objects, no submitter/runtime access."""
from __future__ import annotations

import copy
import json
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


@unittest.skipUnless((BENCHMARK / '.git').exists(), 'existing offline public benchmark Git objects required')
class OriginalStockTests(unittest.TestCase):
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
