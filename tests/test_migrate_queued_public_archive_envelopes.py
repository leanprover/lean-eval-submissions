from __future__ import annotations
import base64
import copy
import hashlib
import json
import pathlib
import sys
import os
import subprocess
import contextlib
import io
import tempfile
import unittest
from unittest import mock
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import migrate_archive_envelopes as old
import migrate_queued_public_archive_envelopes as finite
from key_capability_contract import archive_file_key_id
from test_migrate_archive_envelopes import sidecar, write_pair, named_workflow_steps, git

class FinitePublicArchiveMigrationTests(unittest.TestCase):

    def test_actual_closed_selection_proves_five_original_public_identities(self):
        selection = finite.load_selection(ROOT)
        self.assertEqual(4, selection['migration_plan']['migration_count'])
        self.assertEqual(5, len(selection['result_bindings']))
        self.assertEqual(finite.INVENTORY_DIGEST, selection['migration_plan']['inventory_digest'])
        self.assertEqual(4, len({b['archive_submission_id'] for b in selection['result_bindings']}))
        self.assertTrue(all((b['original_authority_task']['source_visibility'] == 'public' for b in selection['result_bindings'])))
        with self.assertRaisesRegex(old.MigrationError, 'canonical 439'):
            old._require_canonical_selection(selection['migration_plan'])

    def test_old_apply_and_output_apis_keep_their_439_guard_before_any_shared_body(self):
        plan = finite.load_selection(ROOT)['migration_plan']
        with mock.patch.object(old, '_migrate_one_validated') as transform, mock.patch.object(old, '_validate_output_validated') as validate:
            with self.assertRaisesRegex(old.MigrationError, 'canonical 439'):
                old.migrate_one(plan, ROOT, ROOT, ROOT, ROOT, ROOT, 'irrelevant')
            with self.assertRaisesRegex(old.MigrationError, 'canonical 439'):
                old.validate_output(plan, ROOT, ROOT)
            transform.assert_not_called()
            validate.assert_not_called()

    def test_missing_extra_or_changed_finite_plan_refuses_old_and_new_apply(self):
        selection = finite.load_selection(ROOT)
        original = selection['migration_plan']
        mutations = [lambda p: p['entries'].pop(), lambda p: p['entries'].append(copy.deepcopy(p['entries'][0])), lambda p: p['entries'][0].update(source_schema_version=2), lambda p: p['entries'][0].update(submission_id='01900000-0000-7000-8000-000000000000'), lambda p: p['entries'][0].update(source_ciphertext_sha256='0' * 64), lambda p: p.update(source_commit='0' * 40), lambda p: p.update(inventory_digest='0' * 64)]
        for mutate in mutations:
            plan = copy.deepcopy(original)
            mutate(plan)
            with self.subTest(mutation=mutate), self.assertRaises(old.MigrationError):
                finite._require_plan(plan, selection)
            with self.assertRaises(old.MigrationError):
                old._require_canonical_selection(plan)

    def test_selection_hash_and_original_identity_refuse_before_key_operations(self):
        original = json.loads((ROOT / finite.SELECTION_DIRECTORY / f'{finite.SELECTION_SHA256}.json').read_bytes())
        for field, value in [('source_visibility', 'private'), ('source_commit', '0' * 40), ('result_tree_digest', '0' * 64)]:
            selection = copy.deepcopy(original)
            selection['result_bindings'][0]['original_authority_task'][field] = value
            raw = finite.public.canonical_bytes(selection)
            with tempfile.TemporaryDirectory() as directory:
                repository = pathlib.Path(directory)
                sha = hashlib.sha256(raw).hexdigest()
                path = repository / finite.SELECTION_DIRECTORY / f'{sha}.json'
                path.parent.mkdir(parents=True)
                path.write_bytes(raw)
                plan_path = repository / selection['migration_plan_path']
                plan_path.parent.mkdir(parents=True)
                plan_path.write_bytes((ROOT / selection['migration_plan_path']).read_bytes())
                with mock.patch.object(finite, 'SELECTION_SHA256', sha), mock.patch.object(finite, '_check_repository'), mock.patch.object(finite, '_blob', side_effect=lambda _r, c, p: old._git(ROOT, 'show', f'{c}:{p}').stdout), mock.patch.object(old, '_extract_file_key') as extract, mock.patch.object(old, '_wrap_file_key') as wrap:
                    with self.assertRaises((old.MigrationError, finite.public.HistoricalReplayControllerError)):
                        finite.load_selection(repository)
                    extract.assert_not_called()
                    wrap.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            repository = pathlib.Path(directory)
            path = repository / finite.SELECTION_DIRECTORY / f'{finite.SELECTION_SHA256}.json'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'{}\n')
            with mock.patch.object(finite, '_check_repository'), self.assertRaisesRegex(old.MigrationError, 'selection digest'):
                finite.load_selection(repository)

    def fixture(self, source):
        for index in range(4):
            encrypted = b'age-encryption.org/v1\nfixture-cipher-' + str(index).encode()
            metadata = sidecar(1, encrypted)
            metadata['submission_public'] = True
            write_pair(source, f'audit/2026/05/example-{index}-bbbbbbbb.tar.age', metadata, encrypted)
        plan = old.build_plan(source, finite.AUDIT_COMMIT)
        bindings = []
        for entry in plan['entries']:
            metadata = json.loads((source / (entry['source_path'].removesuffix('.tar.age') + '.json')).read_text())
            task = {'source_repository': metadata['submission_repo'], 'source_commit': metadata['submission_ref'], 'source_kind': metadata['submission_kind'], 'owner_login': metadata['submitter'], 'declared_model': metadata['model'], 'benchmark_commit': metadata['benchmark_commit'], 'problem_id': 'fixture'}
            bindings.append({'archive_submission_id': entry['submission_id'], 'original_authority_task': task})
        return (plan, {'migration_plan': plan, 'result_bindings': bindings})

    def test_shared_transform_preserves_public_ciphertext_tar_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / 'source'
            output = root / 'output'
            plan, selection = self.fixture(source)

            def wrap(_adapter, identifier, digest, key):
                self.assertEqual(b'k' * 16, key)
                return {'schema_version': 2, 'submission_id': identifier, 'archive_ciphertext_sha256': digest, 'data_key_id': archive_file_key_id(identifier, digest), 'key_material_type': 'age-file-key-v1', 'adapter': 'aws-kms-v1', 'wrapped_key_material': base64.b64encode(b'wrapped-fixture').decode()}
            with mock.patch.object(finite, 'load_selection', return_value=selection), mock.patch.object(old, '_extract_file_key', return_value=b'k' * 16), mock.patch.object(old, '_wrap_file_key', side_effect=wrap), mock.patch.object(old, '_require_canonical_selection', side_effect=AssertionError('old guard bypassed')):
                for entry in plan['entries']:
                    finite.migrate_one(plan, source, output, root, root / 'identity', root / 'helper', root / 'adapter', entry['source_path'])
                report = finite.validate_output(plan, source, output, root)
                self.assertEqual(0, report['ciphertext_bytes_changed'])
                self.assertEqual(4, report['migration_count'])
                extra = output / 'unexpected.txt'
                extra.write_text('must never enter Audit staging')
                with self.assertRaises(old.MigrationError):
                    finite.validate_output(plan, source, output, root)
                extra.unlink()
                extra.symlink_to(output / plan['entries'][0]['target_path'])
                with self.assertRaises(old.MigrationError):
                    finite.validate_output(plan, source, output, root)
                extra.unlink()
                target = output / plan['entries'][0]['target_path'].removesuffix('.tar.age')
                target = target.parent / (target.name + '.json')
                data = json.loads(target.read_text())
                self.assertIs(data['submission_public'], True)
                for field, value in [('submission_public', False), ('sha256_plaintext_tar', '0' * 64), ('submission_id', '01900000-0000-7000-8000-000000000000')]:
                    changed = copy.deepcopy(data)
                    changed[field] = value
                    target.write_text(json.dumps(changed))
                    with self.subTest(field=field), self.assertRaises(old.MigrationError):
                        finite.validate_output(plan, source, output, root)
                target.write_text(json.dumps(data))
                cipher = output / plan['entries'][0]['target_path']
                cipher.write_bytes(b'changed')
                with self.assertRaises(old.MigrationError):
                    finite.validate_output(plan, source, output, root)

    def test_source_inventory_rejects_symlink_extra_file_and_original_public_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / 'source'
            plan, selection = self.fixture(source)
            finite.verify_source(plan, source, selection)
            extra = source / 'extra.json'
            extra.write_text('{}')
            with self.assertRaises(old.MigrationError):
                finite.verify_source(plan, source, selection)
            extra.unlink()
            extra.symlink_to(source / plan['entries'][0]['source_path'])
            with self.assertRaises(old.MigrationError):
                finite.verify_source(plan, source, selection)
            extra.unlink()
            side = source / (plan['entries'][0]['source_path'].removesuffix('.tar.age') + '.json')
            value = json.loads(side.read_text())
            value['submission_public'] = False
            side.write_text(json.dumps(value))
            with self.assertRaises(old.MigrationError):
                finite.verify_source(plan, source, selection)

    def test_closed_guard_refuses_before_detached_header_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            plan, selection = self.fixture(root / 'source')
            wrong = copy.deepcopy(plan)
            wrong['entries'].pop()
            with mock.patch.object(finite, 'load_selection', return_value=selection), mock.patch.object(old, '_extract_file_key') as extract:
                with self.assertRaises(old.MigrationError):
                    finite.migrate_one(wrong, root / 'source', root / 'output', root, root / 'identity', root / 'helper', root / 'adapter', 'unknown')
                extract.assert_not_called()

    def test_real_git_preflight_preserves_unrelated_history_and_refuses_overlap_or_branch(self):
        for collision in [False, True]:
            with self.subTest(collision=collision), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                writer = root / 'writer'
                writer.mkdir()
                subprocess.run(['git', 'init', '-b', 'main', str(writer)], check=True, capture_output=True)
                git(writer, 'config', 'user.name', 'Finite Test')
                git(writer, 'config', 'user.email', 'fixture@example.com')
                self.fixture(writer)
                git(writer, 'add', '.')
                git(writer, 'commit', '-m', 'four source pairs')
                source_commit = git(writer, 'rev-parse', 'HEAD')
                plan = old.build_plan(writer, source_commit)
                selection = {'migration_plan': plan}
                remote = root / 'remote.git'
                subprocess.run(['git', 'clone', '--bare', str(writer), str(remote)], check=True, capture_output=True)
                selected = root / 'selected'
                subprocess.run(['git', 'clone', str(remote), str(selected)], check=True, capture_output=True)
                git(selected, 'checkout', '--detach', source_commit)
                git(writer, 'remote', 'add', 'origin', str(remote))
                with mock.patch.object(finite, 'load_selection', return_value=selection), mock.patch.object(finite, 'AUDIT_COMMIT', source_commit), mock.patch.object(old, 'AUDIT_ORIGINS', {str(remote)}):
                    (writer / 'unrelated.txt').write_text('normal intake survives\n')
                    git(writer, 'add', '.')
                    git(writer, 'commit', '-m', 'unrelated advance')
                    git(writer, 'push', 'origin', 'main')
                    report = finite.preflight(plan, selected, root)
                    self.assertEqual(report['overlap_count'], 0)
                    self.assertEqual(report['migration_touched_path_count'], 16)
                    self.assertEqual(report['audit_main_commit'], git(writer, 'rev-parse', 'HEAD'))
                    if collision:
                        git(writer, 'push', 'origin', source_commit + ':refs/heads/archive-file-key-rewrap-v1')
                        race = subprocess.run(['git', '-C', str(writer), 'push', 'origin', 'HEAD:refs/heads/archive-file-key-rewrap-v1', '--force-with-lease=refs/heads/archive-file-key-rewrap-v1:'], capture_output=True)
                        self.assertNotEqual(0, race.returncode)
                        self.assertEqual(source_commit, git(writer, 'ls-remote', 'origin', 'refs/heads/archive-file-key-rewrap-v1').split()[0])
                        expected = 'review branch already exists'
                    else:
                        side = writer / (plan['entries'][0]['source_path'].removesuffix('.tar.age') + '.json')
                        side.write_bytes(side.read_bytes() + b' ')
                        git(writer, 'add', '.')
                        git(writer, 'commit', '-m', 'overlapping sidecar change')
                        git(writer, 'push', 'origin', 'main')
                        expected = 'changed 1 migration-touched paths'
                    with self.assertRaisesRegex(old.MigrationError, expected):
                        finite.preflight(plan, selected, root)
                    with mock.patch.object(old, 'preflight_audit_checkout') as inherited:
                        wrong = copy.deepcopy(plan)
                        wrong['entries'].pop()
                        with self.assertRaises(old.MigrationError):
                            finite.preflight(wrong, selected, root)
                        inherited.assert_not_called()

    def test_real_inventory_reads_only_selected_git_blobs_and_refuses_mode_hash_or_export_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            audit = root / 'audit'
            audit.mkdir()
            subprocess.run(['git', 'init', '-b', 'main', str(audit)], check=True, capture_output=True)
            git(audit, 'config', 'user.name', 'Inventory Test')
            git(audit, 'config', 'user.email', 'fixture@example.com')
            plan, selection = self.fixture(audit)
            git(audit, 'add', '.')
            git(audit, 'commit', '-m', 'source four')
            commit = git(audit, 'rev-parse', 'HEAD')
            plan = old.build_plan(audit, commit)
            selection['migration_plan'] = plan
            git(audit, 'remote', 'add', 'origin', 'local-audit-fixture')
            with mock.patch.object(finite, 'load_selection', return_value=selection), \
                 mock.patch.object(finite, 'AUDIT_COMMIT', commit), \
                 mock.patch.object(old, 'AUDIT_ORIGINS', {'local-audit-fixture'}):
                self.assertEqual(plan, finite.inventory(audit, root, root / 'export'))
                self.assertEqual(8, len([p for p in (root / 'export').rglob('*') if p.is_file()]))
                with self.assertRaisesRegex(old.MigrationError, 'export already exists'):
                    finite.inventory(audit, root, root / 'export')
                # Changing Git mode is refused before any header/key operation.
                source = audit / plan['entries'][0]['source_path']
                source.chmod(0o755)
                git(audit, 'add', '.')
                git(audit, 'commit', '-m', 'wrong source mode')
                changed = git(audit, 'rev-parse', 'HEAD')
                with mock.patch.object(finite, 'AUDIT_COMMIT', changed), \
                     self.assertRaisesRegex(old.MigrationError, 'regular Git blob'):
                    finite.inventory(audit, root, root / 'wrong-mode')
                source.chmod(0o644)
                source.write_bytes(b'changed ciphertext')
                git(audit, 'add', '.')
                git(audit, 'commit', '-m', 'wrong ciphertext SHA')
                changed = git(audit, 'rev-parse', 'HEAD')
                with mock.patch.object(finite, 'AUDIT_COMMIT', changed), \
                     self.assertRaisesRegex(old.MigrationError, 'Git SHA differs'):
                    finite.inventory(audit, root, root / 'wrong-sha')

    def test_cli_inventory_writes_plan_and_cli_apply_refuses_before_header_helper(self):
        self.addCleanup(os.umask, os.umask(0o077))
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            plan, selection = self.fixture(root / 'source')
            output = root / 'plan.json'
            with mock.patch.object(sys, 'argv', ['finite', 'inventory', '--repository-root', str(root), '--audit-root', str(root), '--source-root', str(root / 'export'), '--output', str(output)]), \
                 mock.patch.object(finite, 'inventory', return_value=plan) as inventory:
                self.assertEqual(0, finite.main())
                inventory.assert_called_once_with(root, root, root / 'export')
            self.assertEqual(plan, old._load_plan(output))
            wrong = copy.deepcopy(plan)
            wrong['entries'][0]['source_sidecar_sha256'] = '0' * 64
            core = {k: wrong[k] for k in ['source_repository', 'source_commit', 'entries', 'retained']}
            wrong['inventory_digest'] = hashlib.sha256(b'lean-eval-archive-envelope-migration-v1\0' + old._canonical_bytes(core)).hexdigest()
            output.write_text(json.dumps(wrong))
            errors = io.StringIO()
            with mock.patch.object(sys, 'argv', ['finite', 'migrate-one', '--repository-root', str(root), '--source-root', str(root / 'source'), '--output-root', str(root / 'target'), '--plan', str(output), '--legacy-identity', str(root / 'identity'), '--file-key-helper', str(root / 'helper'), '--adapter-executable', str(root / 'adapter'), '--source-path', plan['entries'][0]['source_path']]), \
                 mock.patch.object(finite, 'load_selection', return_value=selection), \
                 mock.patch.object(old, '_extract_file_key') as extract, contextlib.redirect_stderr(errors):
                self.assertEqual(1, finite.main())
                extract.assert_not_called()
            self.assertNotIn(str(root), errors.getvalue())

    def test_sparse_staging_keeps_unselected_git_history_and_archive_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            writer = root / 'writer'
            writer.mkdir()
            subprocess.run(['git', 'init', '-b', 'main', str(writer)], check=True, capture_output=True)
            git(writer, 'config', 'user.name', 'Sparse Test')
            git(writer, 'config', 'user.email', 'fixture@example.com')
            plan, _ = self.fixture(writer)
            write_pair(writer, 'audit/2026/05/unrelated.tar.age', sidecar(1, b'unrelated'), b'unrelated')
            git(writer, 'add', '.')
            git(writer, 'commit', '-m', 'selected and unrelated')
            base = git(writer, 'rev-parse', 'HEAD')
            paths = old._migration_touched_paths(plan)
            subprocess.run(['git', '-C', str(writer), 'sparse-checkout', 'set', '--no-cone', '--stdin'], input=''.join('/' + p + '\n' for p in sorted(paths)), text=True, check=True, capture_output=True)
            self.assertFalse((writer / 'audit/2026/05/unrelated.tar.age').exists())
            for entry in plan['entries']:
                old_path = writer / entry['source_path']
                data = old_path.read_bytes()
                side = old_path.parent / (old_path.name.removesuffix('.tar.age') + '.json')
                metadata = side.read_bytes()
                git(writer, 'rm', '--', entry['source_path'], side.relative_to(writer).as_posix())
                target = writer / entry['target_path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                (target.parent / (target.name.removesuffix('.tar.age') + '.json')).write_bytes(metadata)
            git(writer, 'add', '.')
            tree = git(writer, 'write-tree')
            self.assertEqual(old.count_archive_ciphertexts_in_tree(writer, base), old.count_archive_ciphertexts_in_tree(writer, tree))
            self.assertEqual(git(writer, 'rev-parse', base + ':audit/2026/05/unrelated.tar.age'), git(writer, 'rev-parse', tree + ':audit/2026/05/unrelated.tar.age'))
            self.assertEqual(16, len(git(writer, 'diff', '--cached', '--no-renames', '--name-only').splitlines()))

    def test_workflow_guards_precede_identity_aws_and_writer_and_keep_old_callers(self):
        text = (ROOT / '.github/workflows/migrate-queued-public-archive-envelopes.yml').read_text()
        steps = named_workflow_steps(text)
        self.assertIn('default: false', text)
        source_checkout = text.split('      - uses: actions/checkout@', 1)[1].split('      - name:', 1)[0]
        self.assertIn('fetch-depth: 0', source_checkout)
        self.assertLess(text.index('Prove the finite branch and non-overlap'), text.index('Install the exact source-only original identity validator'))
        self.assertLess(text.index('Prove the finite branch and non-overlap'), text.index('python -m pip install'))
        self.assertIn('group: lean-eval-historical-archive-envelope-migration', text)
        self.assertLess(text.index('Reprove the closed source selection'), text.index('Assume only the production Encrypt role'))
        self.assertLess(text.index('Freeze and verify the complete inventory'), text.index('LEGACY_ARCHIVE_IDENTITY:'))
        self.assertLess(text.index('Prove legacy decrypt material and AWS session are gone'), text.index('Mint audit-repository-only migration writer'))
        self.assertIn('test "$actual_count" = 4', text)
        self.assertIn('--force-with-lease=refs/heads/archive-file-key-rewrap-v1:', text)
        self.assertNotIn('            /*', text)
        self.assertIn('/dev/shm/lean-eval-audit-migration-read-key', text.split('      - name: Remove migration scratch')[1])
        old_text = (ROOT / '.github/workflows/migrate-archive-envelopes.yml').read_text()
        self.assertIn('test "$actual_count" = 439', old_text)
        self.assertNotIn('migrate_queued_public', old_text)
        self.assertNotIn('migrate_queued_public', (ROOT / '.github/workflows/promote-archive-migration.yml').read_text())
if __name__ == '__main__':
    unittest.main()
