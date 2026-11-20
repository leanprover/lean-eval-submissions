from __future__ import annotations

import copy
import pathlib
import tempfile
import unittest
from unittest import mock
from scripts import public_runtime_guard_staging as stage

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHA = 'a' * 40
TASK = 'rt1_' + 'b' * 64


class PublicRuntimeGuardStagingTests(unittest.TestCase):
    def fixture(self, directory, case='a-match'):
        qualified = stage.load(ROOT / f'evidence/public-replay/profiles/{stage.FAMILIES[case[0]][0]}.json')
        config, profile = stage.render(case, qualified, SHA, TASK)
        receipt = dict(case=case, worker=config['name'], application=config['containers'][0]['name'],
                       commit=SHA, created=True, safe_to_delete=True, worker_version='old',
                       namespaces={'REPLAY_SANDBOX': 'ns1', 'REPLAY_TERMINAL_RECEIPT': 'ns2'},
                       application_id='app', application_version=1, application_created_at='date')
        request = dict(schema_version=1, runner_nonce='c' * 64, replay_task_id=TASK, attempt=1,
                       handoff_sha256='d' * 64, source_archive_sha256='e' * 64,
                       execution_profile_digest=config['vars']['REVIEWED_EXECUTION_PROFILE_DIGEST'],
                       measurement_config_digest=qualified['measurement_config_digest'],
                       vm_image_digest=qualified['registry_manifest_digest'])
        for name, value in [('config', config), ('receipt', receipt), ('request', request), ('profile', qualified['execution_profile'])]:
            stage.save(directory / (name + '.json'), value)
        return config, profile, receipt, request

    def test_four_closed_cases_preserve_qualified_images_and_only_negative_kernel_changes(self):
        for family, (digest, _, _) in stage.FAMILIES.items():
            q = stage.load(ROOT / f'evidence/public-replay/profiles/{digest}.json')
            positive, profile = stage.render(family + '-match', q, SHA, TASK)
            negative, wrong = stage.render(family + '-mismatch', q, SHA, TASK)
            expected = copy.deepcopy(profile)
            expected['kernel_release'] = 'intentional-staging-guard-mismatch'
            self.assertEqual(profile, q['execution_profile'])
            self.assertEqual(wrong, expected)
            self.assertEqual({k:v for k,v in positive['containers'][0].items() if k != 'name'}, {k:v for k,v in negative['containers'][0].items() if k != 'name'})
            self.assertTrue(positive['containers'][0]['image'].endswith('@' + q['registry_manifest_digest']))
            self.assertEqual(positive['vars']['REVIEWED_EXECUTION_PROFILE_DIGEST'], digest)
            self.assertNotEqual(negative['vars']['REVIEWED_EXECUTION_PROFILE_DIGEST'], digest)

    def test_manual_workflow_is_fixed_serial_failure_stopping_and_uploads_only_receipt(self):
        text = (ROOT / '.github/workflows/public-runtime-guard-staging.yml').read_text()
        for expected in ('workflow_dispatch:\n\npermissions:', 'case: [a-match, a-mismatch, b-match, b-mismatch]',
                         'max-parallel: 1', 'fail-fast: true', 'refs/tags/lean-eval-dispatch/${{ github.sha }}'):
            self.assertIn(expected, text)
        for forbidden in ('secrets.PRODUCTION_STATE', 'AWS_', 'STATE_READ_KEY'):
            self.assertNotIn(forbidden, text)
        upload = text.split('uses: actions/upload-artifact@', 1)[1]
        self.assertIn('/receipt.json', upload)
        self.assertNotIn('request.json', upload)
        self.assertNotIn('source.tar.gz', upload)
        self.assertIn('if: always()', text.split('- name: Retire only owned', 1)[1])
        promote = (ROOT / '.github/workflows/promote-workflow-dispatch-ref.yml').read_text()
        for path in ('.github/workflows/public-runtime-guard-staging.yml', 'scripts/public_runtime_guard_staging.py', 'runtime-guard-staging/retired.js'):
            self.assertIn(path, promote)

    def test_collision_is_not_adopted_or_deployed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            _, _, receipt, _ = self.fixture(directory)
            receipt['created'] = False
            stage.save(directory / 'receipt.json', receipt)
            with mock.patch.object(stage, 'cf', return_value={'existing': True}), mock.patch.object(stage, 'command') as cmd:
                with self.assertRaisesRegex(ValueError, 'collision'):
                    stage.deploy('a-match', directory)
                cmd.assert_not_called()
            self.assertFalse(stage.load(directory / 'receipt.json')['created'])

    def test_ambiguous_observation_never_authorizes_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            _, _, receipt, _ = self.fixture(directory)
            receipt.update(safe_to_delete=False, phase='runtime-observation')
            stage.save(directory / 'receipt.json', receipt)
            with mock.patch.object(stage, 'command') as cmd, mock.patch.object(stage, 'http') as http:
                with self.assertRaisesRegex(ValueError, 'destruction is unconfirmed'):
                    stage.cleanup('a-match', directory)
                cmd.assert_not_called()
                http.assert_not_called()

    def test_ambiguous_start_uses_only_exact_idempotent_cleanup_before_ownership(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            _, _, receipt, request = self.fixture(directory)
            receipt.update(safe_to_delete=False, phase='guarded-replay')
            stage.save(directory / 'receipt.json', receipt)
            binding = {k: request[k] for k in ('schema_version', 'replay_task_id', 'attempt')}
            with mock.patch.object(stage, 'http', return_value=(503, {})) as http, mock.patch.object(stage, 'ownership') as own:
                with self.assertRaisesRegex(ValueError, 'destruction is unconfirmed'):
                    stage.cleanup('a-match', directory)
                self.assertEqual(http.call_args.args[1:], (binding, True))
                own.assert_not_called()
            with mock.patch.object(stage, 'http', return_value=(200, {**binding, 'destruction': 'confirmed'})), mock.patch.object(stage, 'ownership', side_effect=ValueError('stop after destroy')):
                with self.assertRaisesRegex(ValueError, 'stop after destroy'):
                    stage.cleanup('a-match', directory)
                self.assertTrue(stage.load(directory / 'receipt.json')['safe_to_delete'])

    def test_changed_original_ownership_stops_before_stub(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            self.fixture(directory)
            with mock.patch.object(stage, 'ownership', return_value={'worker_version': 'foreign'}), mock.patch.object(stage, 'command') as cmd:
                with self.assertRaisesRegex(ValueError, 'ownership changed'):
                    stage.cleanup('a-match', directory)
                cmd.assert_not_called()

    def test_retire_verify_delete_app_verify_delete_worker_then_absence(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            _, _, receipt, _ = self.fixture(directory)
            own = {k: receipt[k] for k in ('worker_version', 'namespaces', 'application_id', 'application_version', 'application_created_at')}
            app = {k: receipt[k] for k in ('application_id', 'application_version', 'application_created_at')}
            calls = []
            def cmd(*args, **kwargs): calls.append(('command', args))
            def retired(*args):
                calls.append(('verified-stub',))
                return 'stub'
            def cf(path, method='GET', **kwargs):
                calls.append(('cf', method, path))
                return None
            with mock.patch.object(stage, 'ROOT', directory), mock.patch.object(stage, 'ownership', return_value=own), mock.patch.object(stage, 'command', side_effect=cmd), mock.patch.object(stage, 'retired_identity', side_effect=retired), mock.patch.object(stage, 'applications', return_value=[]), mock.patch.object(stage, '_verify_application', side_effect=[app, None]), mock.patch.object(stage, 'cf', side_effect=cf), mock.patch.object(stage, 'namespaces', return_value=[]):
                (directory / 'server').mkdir()
                stage.cleanup('a-match', directory)
                self.assertEqual([c[0] for c in calls], ['command', 'verified-stub', 'verified-stub', 'command', 'verified-stub', 'cf', 'cf'])
                self.assertEqual(calls[3][1][-1], receipt['application_id'])
                self.assertEqual(calls[5][1], 'DELETE')
                stub = stage.load(directory / 'server/.public-guard-retired-a-match.json')
                self.assertEqual(stub['migrations'][-1], {'tag': 'v3-retired', 'deleted_classes': ['ReplaySandbox', 'ReplayTerminalReceipt']})
                self.assertEqual(stub['durable_objects'], {'bindings': []})
                self.assertEqual(stage.load(directory / 'receipt.json')['cleanup'], 'confirmed')

    def test_changed_container_id_after_retirement_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            _, _, receipt, _ = self.fixture(directory)
            own = {k: receipt[k] for k in ('worker_version', 'namespaces', 'application_id', 'application_version', 'application_created_at')}
            with mock.patch.object(stage, 'ROOT', directory), mock.patch.object(stage, 'ownership', return_value=own), mock.patch.object(stage, 'command') as cmd, mock.patch.object(stage, 'retired_identity', return_value='stub'), mock.patch.object(stage, 'applications', return_value=[]), mock.patch.object(stage, '_verify_application', return_value={'application_id': 'foreign'}), mock.patch.object(stage, 'cf') as cf:
                (directory / 'server').mkdir()
                with self.assertRaisesRegex(ValueError, 'container identity changed'):
                    stage.cleanup('a-match', directory)
                self.assertEqual(cmd.call_count, 1)
                cf.assert_not_called()

    def test_runtime_execution_refuses_deployment_credential(self):
        with mock.patch.dict(stage.os.environ, {'CLOUDFLARE_API_TOKEN': 'sentinel'}):
            with self.assertRaisesRegex(ValueError, 'credential reached execution'):
                stage.run_case('a-match', pathlib.Path('/unused'))

    def test_host_drift_stops_before_public_start(self): self.run_fixture('a-match', True)
    def test_negative_guard_requires_exact_typed_failure_and_destruction(self): self.run_fixture('a-mismatch', False)
    def test_guard_internal_error_is_not_accepted_as_negative_case(self): self.run_fixture('a-mismatch', False, 'runtime_profile_guard_failed')

    def run_fixture(self, case, drift, detail='runtime_profile_mismatch'):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            self.fixture(directory, case)
            observed = stage.load(directory / 'profile.json')
            if drift: observed['kernel_release'] = 'observed-different-host'
            routes = []
            def command(*args, **kwargs):
                if args[1] == '-o': pathlib.Path(args[2]).write_bytes(b'disposable test age identity')
                return b'age1synthetic'
            def http(url, body=None, oidc=False):
                routes.append(url.rsplit('/', 1)[-1])
                self.assertTrue(oidc)
                if routes[-1] == 'staging-acceptance':
                    evidence = {k: body[k] for k in ('schema_version', 'request_id', 'runner_nonce', 'archive_ciphertext_sha256', 'marker_sha256')}
                    evidence.update(service='lean-eval-replay-executor', environment='staging', network_policy='disabled', network_probe='blocked', destruction='confirmed', staging_memory_limit_bytes=12*1024**3, production_memory_gate_bytes=12*1024**3)
                    evidence.update({k: observed[k] for k in ('architecture', 'kernel_release', 'cpu_model')})
                    return 200, evidence
                binding = {k: body[k] for k in ('schema_version', 'replay_task_id', 'attempt')}
                if routes[-1] == 'cleanup-reservation': return 200, {**binding, 'status': 'reserved'}
                if routes[-1] == 'historical-public-replay': return 202, {**binding, 'status': 'running'}
                if routes[-1] == 'status': return 500, dict(error='executor_failed', reason='command_failed', detail=detail)
                if routes[-1] == 'cleanup': return 200, {**binding, 'destruction': 'confirmed'}
                raise AssertionError('unexpected route')
            with mock.patch.dict(stage.os.environ, {'CLOUDFLARE_API_TOKEN': '', 'STATE_WRITE_KEY': ''}), mock.patch.object(stage, 'command', side_effect=command), mock.patch.object(stage.subprocess, 'run', return_value=mock.Mock(stdout=b'ciphertext')), mock.patch.object(stage, 'http', side_effect=http), mock.patch.object(stage.time, 'sleep'):
                if drift:
                    with self.assertRaisesRegex(ValueError, 'differs from the frozen profile'): stage.run_case(case, directory)
                    self.assertEqual(routes, ['staging-acceptance'])
                else:
                    if detail == 'runtime_profile_mismatch':
                        stage.run_case(case, directory)
                    else:
                        with self.assertRaisesRegex(ValueError, 'exact failure'):
                            stage.run_case(case, directory)
                    self.assertEqual(routes, ['staging-acceptance', 'cleanup-reservation', 'historical-public-replay', 'status', 'cleanup'])
                    if detail == 'runtime_profile_mismatch':
                        self.assertEqual(stage.load(directory / 'receipt.json')['outcome'], 'expected-runtime-mismatch')
                    else:
                        self.assertNotIn('outcome', stage.load(directory / 'receipt.json'))
                self.assertTrue(stage.load(directory / 'receipt.json')['safe_to_delete'])
                self.assertFalse((directory / 'marker-identity.age').exists())

    def test_retired_proof_requires_namespace_absence_and_active_code_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            config, _, receipt, _ = self.fixture(pathlib.Path(temp))
            config['durable_objects'] = {'bindings': []}
            bindings = [dict(type='plain_text', name=k, text=v) for k,v in config['vars'].items()]
            service = {'result': {'default_environment': {'script': {'etag': 'exact'}}}}
            version = {'result': {'id': 'stub', 'resources': {'bindings': bindings, 'script': {'etag': 'exact'}, 'script_runtime': {'containers': [], 'migration_tag': 'v3-retired'}}}}
            def cf(path):
                if '/versions/' in path: return version
                if path.endswith('/settings'): return {'result': {'bindings': bindings, 'compatibility_date': config['compatibility_date'], 'compatibility_flags': config['compatibility_flags']}}
                return service
            body = dict(status='retired', commit=receipt['commit'], ownership=config['vars']['EXECUTOR_OWNERSHIP_TAG'])
            with mock.patch.object(stage, 'cf', side_effect=cf), mock.patch.object(stage, '_active_version', return_value=('stub', 'tag')), mock.patch.object(stage, 'http', return_value=(503, body)), mock.patch.object(stage, 'namespaces', return_value=[dict(id='ns1', script='unrelated')]):
                with self.assertRaisesRegex(ValueError, 'namespaces have not disappeared'): stage.retired_identity(config, receipt)
            with mock.patch.object(stage, 'cf', side_effect=cf), mock.patch.object(stage, '_active_version', return_value=('stub', 'tag')), mock.patch.object(stage, 'http', return_value=(503, body)), mock.patch.object(stage, 'namespaces', return_value=[]):
                self.assertEqual(stage.retired_identity(config, receipt), 'stub')
                version['result']['resources']['script']['etag'] = 'wrong'
                with self.assertRaisesRegex(ValueError, 'code identity differ'): stage.retired_identity(config, receipt)


if __name__ == '__main__': unittest.main()
