"""Offline negative evidence/privacy checks; no download, Docker or provider."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_stock_image_208_evidence as build
import stock_image_208 as stock


class StockImage208Build(unittest.TestCase):
    def installed(self):
        return {'schema_version': 1, 'kind': 'inactive_stock208_installation_check',
                'benchmark_commit': stock.BENCHMARK, 'benchmark_tree': stock.BENCHMARK_TREE,
                'toolchain': stock.TOOLCHAIN, 'release_sha256': stock.RELEASE_SHA256,
                'tool_sha256': dict(stock.TOOLS), 'module_count': 296,
                'ordered_module_plan_sha256': stock.MODULE_PLAN,
                'dependencies': {f'fixture{i}': {'commit': '1' * 40, 'tree': '2' * 40}
                                 for i in range(12)},
                'instrumentation_sha256': {name: '3' * 64 for name in
                    (*stock.WRAPPERS, *(name + '.py' for name in stock.MODULES))},
                'bubblewrap_sha256': '4' * 64, 'remaining': ['not a qualification'] * 5,
                **{key: False for key in build.FLAGS}}

    def receipt(self, value=None, size=12345, raw=None):
        return build.evidence(raw if raw is not None else json.dumps(
            self.installed() if value is None else value).encode(),
            'a' * 40, 'sha256:' + 'b' * 64, size)

    def test_source_free_projection_and_actual_recipe_hashes_with_boundary(self):
        value = self.installed()
        value['remaining'] = ['DO_NOT_COPY_THIS_PRIVATE_SENTINEL'] * 5
        result = self.receipt(value, build.LIMIT)
        self.assertEqual(result['recipe_sha256'], hashlib.sha256(
            (ROOT / 'Dockerfile.stock-replay-208').read_bytes()).hexdigest())
        self.assertEqual(result['release_url'], build.RELEASE_URL)
        self.assertEqual(result['release_bytes'], 595611952)
        for key in (*build.FLAGS, 'published', 'provider_executed', 'State_written',
                    'Source_fetched', 'Task_admitted'):
            self.assertIs(result[key], False)
        self.assertTrue(result['installation_check_passed'])
        self.assertNotIn('DO_NOT_COPY', json.dumps(result))
        self.assertNotIn('dependencies', result)
        self.assertLessEqual(len(build.canonical(result)), 16384)

    def test_changed_tool_pin_identity_enabled_qualification_or_extra_field_refused(self):
        variants = []
        for key in build.FLAGS:
            for value in (True, 0, None, 'false'):
                v = self.installed()
                v[key] = value
                variants.append(v)
        for key, value in [('benchmark_commit', 'f' * 40), ('benchmark_tree', 'f' * 40),
                           ('release_sha256', 'f' * 64), ('toolchain', 'other'),
                           ('module_count', 295), ('ordered_module_plan_sha256', 'f' * 64),
                           ('schema_version', True), ('kind', 'qualified')]:
            v = self.installed()
            v[key] = value
            variants.append(v)
        v = self.installed()
        v['tool_sha256']['lean'] = 'f' * 64
        variants.append(v)
        v = self.installed()
        v['private_Source'] = 'NEVER_EXPORT'
        variants.append(v)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.receipt(value)

    def test_truncated_pin_maps_unknown_instrumentation_and_nonhash_values_refused(self):
        for kind in ('missing_dependency', 'credential_name', 'bad_tree', 'extra_pin',
                     'extra_instrumentation', 'nonhash_instrumentation'):
            value = self.installed()
            if kind == 'missing_dependency':
                value['dependencies'].pop('fixture0')
            elif kind == 'credential_name':
                value['dependencies']['https://credential@private'] = value['dependencies'].pop('fixture0')
            elif kind == 'bad_tree':
                value['dependencies']['fixture0']['tree'] = 'PRIVATE_SENTINEL'
            elif kind == 'extra_pin':
                value['dependencies']['fixture0']['url'] = 'PRIVATE_SENTINEL'
            elif kind == 'extra_instrumentation':
                value['instrumentation_sha256']['private'] = '3' * 64
            else:
                value['instrumentation_sha256']['replay-measure'] = 'PRIVATE_SENTINEL'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.receipt(value)

    def test_duplicate_oversize_nonobject_and_image_ceiling_fail_closed(self):
        for raw in (b'[]', b'{' + b' ' * 65536 + b'}',
                    json.dumps(self.installed()).encode()[:-1] + b',"schema_version":1}'):
            with self.assertRaises(ValueError):
                self.receipt(raw=raw)
        for size in (0, -1, True, build.LIMIT + 1):
            with self.assertRaises(ValueError):
                self.receipt(size=size)
        with self.assertRaises(ValueError):
            build.evidence(json.dumps(self.installed()).encode(), 'not a commit',
                           'sha256:' + 'b' * 64, 10)

    def test_main_writes_only_validated_evidence_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in ('Dockerfile.stock-replay-208', 'Dockerfile.stock-replay-208.dockerignore',
                         'scripts/stock_image_208.py', 'scripts/build_stock_image_208_evidence.py',
                         '.github/workflows/stock-replay-image-208.yml'):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / path).read_bytes())
            source = root / 'stock208-installation.private.json'
            source.write_text(json.dumps(self.installed()))
            env = {'SOURCE_COMMIT': 'a' * 40, 'IMAGE_ID': 'sha256:' + 'b' * 64,
                   'IMAGE_SIZE_BYTES': '10'}
            # evidence's root default is the real immutable repository root;
            # all host file hashes remain real source, while outputs stay scratch.
            with mock.patch.object(build, 'ROOT', root), mock.patch.dict(build.os.environ, env):
                build.main()
                result = (root / 'stock208-image-evidence.json').read_bytes()
                self.assertIs(json.loads(result)['image_qualified'], False)
                with self.assertRaises(FileExistsError):
                    build.main()
                self.assertEqual((root / 'stock208-image-evidence.json').read_bytes(), result)

    def test_workflow_has_closed_events_no_credentials_or_publication_and_default_offline_run(self):
        workflow = (ROOT / '.github/workflows/stock-replay-image-208.yml').read_text()
        self.assertIn('  pull_request:\n', workflow)
        self.assertIn('  workflow_dispatch:\n\npermissions:', workflow)
        for forbidden in ('  push:', 'pull_request_target', 'workflow_run:', 'secrets.',
                          'packages: write', 'contents: write', 'docker push', '--push',
                          '--privileged', '--network host', 'docker.sock', 'workflow_call:'):
            self.assertNotIn(forbidden, workflow)
        self.assertIn('persist-credentials: false', workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn('github.event.pull_request.head.repo.full_name == github.repository', workflow)
        self.assertIn('docker run --rm --network none "$IMAGE" >', workflow)
        self.assertIn('test "$image_size" -le 21474836480', workflow)
        self.assertIn(build.RELEASE_URL, workflow)
        self.assertIn(stock.RELEASE_SHA256, workflow)
        self.assertIn('test "$(stat -c %s "$archive.part")" = 595611952', workflow)
        self.assertIn('path: stock208-image-evidence.json', workflow)
        self.assertNotIn('path: stock208-installation', workflow)
        self.assertNotIn('inputs:', workflow)
        self.assertIn('rm -rf .stock-image-inputs', workflow)


if __name__ == '__main__':
    unittest.main()
