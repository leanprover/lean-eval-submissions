"""Offline installation checks; never build an image or fetch public inputs."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_stock_workspace as preparer
import stock_image_208 as image


class StockImage208(unittest.TestCase):
    def test_recipe_preserves_provider_startup_and_documents_explicit_offline_check(self):
        recipe = (ROOT / 'Dockerfile.stock-replay-208').read_text()
        directives = [line.split(None, 1)[0] for line in recipe.splitlines()
                      if line and not line.startswith('#')]
        self.assertNotIn('ENTRYPOINT', directives)
        self.assertNotIn('CMD', directives)
        self.assertIn('/usr/bin/python3 -I -B /opt/lean-eval/stock_image_208.py check-installed', recipe)
        self.assertIn('--network none --entrypoint /usr/bin/python3 lean-eval-stock208:local -I -B '
                      '/opt/lean-eval/stock_image_208.py check-installed',
                      (ROOT / 'docs/stock-image-208.md').read_text())

    def test_unknown_action_refuses_before_installed_entry(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/stock_image_208.py'), 'replay'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b'replay_task_id', result.stdout)

    def test_source_tree_cannot_impersonate_fixed_installed_module(self):
        with self.assertRaises(image.Invalid):
            image.libraries()

    def test_public_fetch_uses_owned_commands_and_no_inherited_auth(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'original.git'
            with mock.patch.object(preparer, 'invoke', side_effect=lambda *args: calls.append(args)):
                image.fetch(preparer, 'https://github.com/leanprover/lean-eval.git', image.BENCHMARK, target)
        self.assertEqual(len(calls), 3)
        for argv, source, env, timeout in calls:
            self.assertEqual(argv[0], '/usr/bin/git')
            self.assertIsNone(source)
            self.assertEqual(timeout, 300)
            self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
            self.assertEqual(env['GIT_ALLOW_PROTOCOL'], 'https')
            self.assertEqual(set(env), {'PATH', 'HOME', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM',
                                       'GIT_TERMINAL_PROMPT', 'GIT_ALLOW_PROTOCOL', 'GIT_NO_REPLACE_OBJECTS'})
            self.assertIn('credential.helper=', argv)
        self.assertIn(image.BENCHMARK, calls[1][0])
        for bad in ('file:///tmp/other', 'https://token@github.com/leanprover/lean-eval.git'):
            with mock.patch.object(preparer, 'invoke', side_effect=AssertionError('must refuse before command')):
                with self.assertRaises(image.Invalid):
                    image.fetch(preparer, bad, image.BENCHMARK, Path('/nonexistent-stock-image-test'))
        for bad in (None, 'main', '-upload-pack=other', 'a' * 39, 'A' * 40):
            with mock.patch.object(preparer, 'invoke', side_effect=AssertionError('must refuse before command')):
                with self.assertRaises(image.Invalid):
                    image.fetch(preparer, 'https://github.com/leanprover/lean-eval.git', bad,
                                Path('/nonexistent-stock-image-test'))

    def test_genuine_shallow_bare_dependency_retains_complete_pinned_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, bare, checkout = root / 'original', root / 'pin.git', root / 'fresh-checkout'
            source.mkdir()
            env = preparer.environment(root)
            env.update(GIT_AUTHOR_NAME='Owned fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                       GIT_COMMITTER_NAME='Owned fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
            def git(*args):
                return preparer._capture_preparation([preparer.GIT, '-c', 'core.hooksPath=/dev/null',
                    '-c', 'maintenance.auto=false', '-c', 'gc.auto=0', *args], None, env, 30)
            git('init', '-q', str(source))
            (source / 'original.lean').write_text('-- original first commit\n')
            git('-C', str(source), 'add', '.')
            git('-C', str(source), 'commit', '-qm', 'first fixture commit')
            (source / 'second.lean').write_text('-- complete selected tree\n')
            git('-C', str(source), 'add', '.')
            git('-C', str(source), 'commit', '-qm', 'second fixture commit')
            commit = git('-C', str(source), 'rev-parse', 'HEAD').decode().strip()
            git('clone', '--bare', '--depth', '1', source.as_uri(), str(bare))
            self.assertEqual(git('-C', str(bare), 'rev-parse', '--is-shallow-repository').strip(), b'true')
            self.assertEqual(git('-C', str(bare), 'rev-list', '--count', commit).strip(), b'1')
            pin = preparer.Git(bare, commit)
            pin.checkout_dependency(checkout, 'https://github.com/controlled-fixture/package.git')
            pin.verify_dependency_checkout(checkout)
            self.assertEqual((checkout / 'original.lean').read_bytes(), (source / 'original.lean').read_bytes())
            self.assertEqual((checkout / 'second.lean').read_bytes(), (source / 'second.lean').read_bytes())
            self.assertEqual(git('-C', str(checkout), 'rev-parse', 'HEAD').decode().strip(), commit)

    def test_consumed_public_record_matches_original_receipt_and_rejects_drift(self):
        source = ROOT / 'configuration/stock-image-208-public-inputs-v1.json'
        image.validate_public_inputs(source)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'changed.json'
            record = json.loads(source.read_bytes())
            record['release']['sha256'] = '0' * 64
            path.write_text(json.dumps(record))
            with self.assertRaises(image.Invalid):
                image.validate_public_inputs(path)

    def test_installed_layout_imports_all_existing_modules_and_closed_schemas(self):
        # Test the actual flat image layout, not the repository import layout.
        # No original compiler/build/generation is executed by this test.
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            install = base / 'lean-eval'
            install.mkdir()
            for name in image.MODULES:
                shutil.copyfile(ROOT / 'scripts' / (name + '.py'), install / (name + '.py'))
            for name in image.WRAPPERS:
                shutil.copyfile(ROOT / 'server/replay-image' / name, install / name)
            schemas = base / 'schemas'
            schemas.mkdir()
            for name in preparer.contracts.SCHEMAS:
                shutil.copyfile(ROOT / 'schemas' / name, schemas / name)
            code = '''import sys,json
sys.path.insert(0,sys.argv[1])
import prepare_stock_workspace as p
assert str(p.contracts.ROOT)==sys.argv[2]
assert len(p.contracts._validators())==4
assert callable(p.generate_original) and callable(p.supervisor.supervise)
assert callable(p.supervisor.stock.load_measure(sys.argv[1]+'/replay-measure').run_measured)
print(json.dumps({'closed_schemas':4,'imports':'pass','generation_observed':False}))
'''
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(install), str(base)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout), {'closed_schemas': 4, 'imports': 'pass',
                                                        'generation_observed': False})
            (schemas / preparer.contracts.SCHEMAS[0]).unlink()
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(install), str(base)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode, 0)

    def test_changed_or_aliased_tool_cannot_pass_original_binary_check(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            (prefix / 'bin').mkdir()
            tool = prefix / 'bin/lean'
            tool.write_bytes(b'a substituted compiler is not the release')
            tool.chmod(0o555)
            with mock.patch.object(image, 'PREFIX', prefix):
                with self.assertRaises(image.Invalid):
                    image.check_tools(preparer)
                tool.unlink()
                tool.symlink_to(ROOT / 'scripts/stock_image_208.py')
                with self.assertRaises(preparer.supervisor.Invalid):
                    image.check_tools(preparer)


if __name__ == '__main__':
    unittest.main()
