from __future__ import annotations

import copy
import contextlib
import io
import pathlib
import tempfile
import types
import unittest
from unittest import mock
import importlib.util

_SPEC = importlib.util.spec_from_file_location("public_runtime_guard_staging", pathlib.Path(__file__).resolve().parents[1] / "runtime-guard-staging/check.py")
stage = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stage)

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
                       application_id='app', application_version=1, application_created_at='date', health={'status': 'ok', 'deployed_commit': SHA})
        request = dict(schema_version=1, runner_nonce='c' * 64, replay_task_id=TASK, attempt=1,
                       handoff_sha256='d' * 64, source_archive_sha256='e' * 64,
                       execution_profile_digest=config['vars']['REVIEWED_EXECUTION_PROFILE_DIGEST'],
                       measurement_config_digest=qualified['measurement_config_digest'],
                       vm_image_digest=qualified['registry_manifest_digest'], execution_profile=profile,
                       source_archive_base64='Zml4dHVyZQ==', handoff={'request_id': 'fixture', 'result': {'result_id': 'fixture'}})
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
        for path in ('.github/workflows/public-runtime-guard-staging.yml', 'runtime-guard-staging/check.py', 'runtime-guard-staging/retired.js'):
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

    def test_ambiguous_observation_requires_full_ownership_before_control_plane_fence(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            _, _, receipt, _ = self.fixture(directory)
            receipt.update(safe_to_delete=False, phase='runtime-observation')
            stage.save(directory / 'receipt.json', receipt)
            with mock.patch.object(stage, 'command') as cmd, mock.patch.object(stage, 'http') as http, mock.patch.object(stage, 'ownership', side_effect=ValueError('unknown ownership')) as own:
                with self.assertRaisesRegex(ValueError, 'unknown ownership'):
                    stage.cleanup('a-match', directory)
                cmd.assert_not_called()
                http.assert_not_called()
                own.assert_called_once()
            result = stage.load(directory / 'receipt.json')
            self.assertEqual(result['cleanup_mode'], 'owned-control-plane-fence')
            self.assertEqual(result['outcome'], 'incomplete')
            self.assertNotIn('observation_http_destruction', result)

    def test_interrupted_start_fence_does_not_mix_oidc_with_control_plane_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=pathlib.Path(temp)
            _,_,receipt,_=self.fixture(directory)
            receipt.update(safe_to_delete=False,phase='guarded-replay')
            stage.save(directory/'receipt.json',receipt)
            with mock.patch.object(stage,'http') as http, mock.patch.object(stage,'ownership',side_effect=ValueError('unknown ownership')):
                with self.assertRaisesRegex(ValueError,'unknown ownership'):stage.cleanup('a-match',directory)
                http.assert_not_called()
            final=stage.load(directory/'receipt.json')
            self.assertEqual(final['outcome'],'incomplete')
            self.assertNotIn('public_http_destruction',final)

    def test_changed_original_ownership_stops_before_stub(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            self.fixture(directory)
            with mock.patch.object(stage, 'ownership', return_value={'worker_version': 'foreign'}), mock.patch.object(stage, 'command') as cmd:
                with self.assertRaisesRegex(ValueError, 'ownership changed'):
                    stage.cleanup('a-match', directory)
                cmd.assert_not_called()

    def test_retire_verify_delete_app_verify_delete_worker_then_absence(self): self.cleanup_order_fixture(False)
    def test_ambiguous_owned_fixture_uses_control_plane_fence_without_faking_http_proof(self): self.cleanup_order_fixture(True)

    def cleanup_order_fixture(self, incomplete, resume=False, ownership_faults=(), deployment_error=False, application_lag=0):
        with tempfile.TemporaryDirectory() as temp:
            directory=pathlib.Path(temp)
            _,_,receipt,_=self.fixture(directory)
            if incomplete:receipt.update(safe_to_delete=False,phase='runtime-observation')
            if resume:receipt.update(retirement_phase='disabled',disabled_worker_version='disabled-version')
            stage.save(directory/'receipt.json',receipt)
            own={k:receipt[k] for k in ('worker_version','namespaces','health','application_id','application_version','application_created_at')}
            app={k:receipt[k] for k in ('application_id','application_version','application_created_at')}
            calls=[]
            reads=0
            def command(*args,**kwargs):
                if 'deploy' in args:
                    path=directory/'server'/args[args.index('--config')+1]
                    config=stage.load(path)
                    final=not config['durable_objects']['bindings']
                    calls.append('deploy-final' if final else 'deploy-disabled')
                    self.assertEqual(config['containers'],[])
                    if final:self.assertEqual(config['migrations'][-1]['deleted_classes'],['ReplaySandbox','ReplayTerminalReceipt'])
                    else:
                        self.assertEqual(config['migrations'][-1]['tag'],'v2')
                        self.assertEqual(len(config['durable_objects']['bindings']),2)
                    if deployment_error:
                        raise stage.CommandFailure('Cloudflare cleanup deployment response lost; provider code 90000')
                else:
                    self.assertEqual(args[-1],receipt['application_id'])
                    calls.append('delete-app')
            def identity(config,*args,**kwargs):
                final=not config['durable_objects']['bindings']
                calls.append('verify-final' if final else 'verify-disabled')
                return 'stub-version' if final else 'disabled-version'
            def cf(path,method='GET',**kwargs):
                nonlocal reads
                calls.append('delete-worker' if method=='DELETE' else 'read-worker')
                if method=='DELETE':return {'success':True}
                reads+=1
                return {'present':True} if reads==1 else None
            diagnostics=io.StringIO()
            with contextlib.redirect_stderr(diagnostics), mock.patch.object(stage,'ROOT',directory), mock.patch.object(stage,'ownership',side_effect=[*ownership_faults,own]) as original, mock.patch.object(stage,'command',side_effect=command), mock.patch.object(stage,'retired_identity',side_effect=identity), mock.patch.object(stage,'applications',return_value=[]), mock.patch.object(stage,'_verify_application',side_effect=[app]*application_lag+[app,None,None,None]), mock.patch.object(stage,'cf',side_effect=cf), mock.patch.object(stage,'namespaces',return_value=[]), mock.patch.object(stage.time,'sleep'):
                (directory/'server').mkdir()
                stage.cleanup('a-match',directory)
                if resume:original.assert_not_called()
                else:
                    self.assertEqual(original.call_count,len(ownership_faults)+1)
                    self.assertEqual(calls[0],'deploy-disabled')
                self.assertLess(calls.index('delete-app'),calls.index('deploy-final'))
                self.assertLess(calls.index('deploy-final'),calls.index('delete-worker'))
                self.assertEqual(calls[calls.index('delete-app')-1],'verify-disabled')
                self.assertEqual(calls[calls.index('delete-worker')-1],'verify-final')
                final=stage.load(directory/'receipt.json')
                self.assertEqual(final['control_plane_cleanup'],'confirmed')
                self.assertEqual(calls.count('delete-app'),application_lag+1)
                if deployment_error:
                    self.assertIn('provider code 90000',diagnostics.getvalue())
                if incomplete:
                    self.assertEqual(final['outcome'],'incomplete')
                    self.assertNotIn('observation_http_destruction',final)
                    self.assertNotIn('public_http_destruction',final)

    def test_known_disabled_checkpoint_can_resume_without_adopting_original_app(self):self.cleanup_order_fixture(True,True)

    def test_first_cleanup_ownership_read_retries_transient_failure_before_fencing(self):
        self.cleanup_order_fixture(True,ownership_faults=(stage.TransientRpcError('edge unavailable'),))

    def test_cleanup_preserves_bounded_provider_diagnostics_after_ambiguous_deploy(self):
        self.cleanup_order_fixture(False,deployment_error=True)

    def test_cleanup_allows_ten_application_absence_observations(self):
        self.cleanup_order_fixture(True,application_lag=5)

    def test_container_listing_failure_and_timeout_are_retryable_reads(self):
        for fault in (stage.CommandFailure('CLI unavailable'), stage.subprocess.TimeoutExpired('wrangler',1)):
            with self.subTest(fault=type(fault).__name__), mock.patch.object(stage,'command',side_effect=fault):
                with self.assertRaises(stage.TransientRpcError):stage.applications(timeout=1)
        with mock.patch.object(stage,'applications',side_effect=[stage.TransientRpcError('edge unavailable'),[]]) as read, mock.patch.object(stage.time,'monotonic',return_value=100), mock.patch.object(stage.time,'sleep'):
            self.assertEqual(stage.wait_applications(120),[])
            self.assertEqual(read.call_args_list,[mock.call(timeout=20),mock.call(timeout=20)])

    def test_cleanup_ownership_reuses_shared_deadline_and_rejects_identity_drift(self):
        with mock.patch.object(stage,'ownership',side_effect=[stage.TransientRpcError('edge unavailable'),{}]) as read, mock.patch.object(stage.time,'monotonic',return_value=100), mock.patch.object(stage.time,'sleep'):
            self.assertEqual(stage.wait_ownership({},120,allow_propagation=False),{})
            self.assertEqual(read.call_args_list,[mock.call({},120),mock.call({},120)])
        with mock.patch.object(stage,'ownership',side_effect=ValueError('foreign identity')) as read, mock.patch.object(stage.time,'sleep') as sleep:
            with self.assertRaisesRegex(ValueError,'foreign identity'):stage.wait_ownership({},120,allow_propagation=False)
            read.assert_called_once()
            sleep.assert_not_called()

    def test_real_ownership_retries_health_503_in_strict_cleanup_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            config,_,receipt,_=self.fixture(pathlib.Path(temp))
            app={k:receipt[k] for k in ('application_id','application_version','application_created_at')}
            rows=[{'id':receipt['namespaces'][b['name']],'script':config['name'],'class':b['class_name']} for b in config['durable_objects']['bindings']]
            with mock.patch.object(stage,'cf',return_value={}), mock.patch.object(stage,'_active_version',return_value=('old',{})), mock.patch.object(stage,'http',side_effect=[(503,{'error':'edge unavailable'}),(200,receipt['health'])]) as health, mock.patch.object(stage,'_verify_worker',return_value=('old',receipt['namespaces'])) as verify, mock.patch.object(stage,'namespaces',return_value=rows), mock.patch.object(stage,'applications',return_value=[]), mock.patch.object(stage,'_verify_application',return_value=app), mock.patch.object(stage.time,'sleep'):
                result=stage.wait_ownership(config,stage.time.monotonic()+120,allow_propagation=False)
                self.assertEqual(result['worker_version'],'old')
                self.assertEqual(health.call_count,2)
                verify.assert_called_once()
            with mock.patch.object(stage,'cf',return_value={}), mock.patch.object(stage,'_active_version',return_value=('old',{})), mock.patch.object(stage,'http',return_value=(503,{'status':'retired'})) as health, mock.patch.object(stage.time,'sleep') as sleep:
                with self.assertRaisesRegex(ValueError,'owned staging health unavailable'):
                    stage.wait_ownership(config,stage.time.monotonic()+120,allow_propagation=False)
                health.assert_called_once()
                sleep.assert_not_called()

    def test_health_html_429_and_500_remain_transient_without_inventing_identity(self):
        for status in (429,500):
            with self.subTest(status=status), mock.patch.object(stage,'request_http',return_value=types.SimpleNamespace(status=status,body=b'<html>unavailable</html>')):
                code,body=stage.http('https://fixture.invalid/healthz')
                self.assertEqual(code,status)
                self.assertEqual(body,{'error':'transport_response_invalid'})

    def test_changed_container_id_after_retirement_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            _, _, receipt, _ = self.fixture(directory)
            own = {k: receipt[k] for k in ('worker_version', 'namespaces', 'application_id', 'application_version', 'application_created_at')}
            with mock.patch.object(stage, 'ROOT', directory), mock.patch.object(stage, 'ownership', return_value=own), mock.patch.object(stage, 'command') as cmd, mock.patch.object(stage, 'retired_identity', return_value='disabled-version'), mock.patch.object(stage, 'applications', return_value=[]), mock.patch.object(stage, '_verify_application', return_value={'application_id': 'foreign'}), mock.patch.object(stage, 'cf') as cf:
                (directory / 'server').mkdir()
                with self.assertRaisesRegex(ValueError, 'container identity changed'):
                    stage.cleanup('a-match', directory)
                self.assertEqual(cmd.call_count, 1)
                cf.assert_not_called()

    def test_namespace_paging_uses_total_count_without_total_pages(self):
        first = [dict(id=str(i)) for i in range(100)]
        second = [dict(id='100'), dict(id='101')]
        def page(rows, number, total=102):
            return dict(result=rows, result_info=dict(page=number, per_page=100, count=len(rows), total_count=total))
        with mock.patch.object(stage, 'cf', side_effect=[page(first,1), page(second,2)]):
            self.assertEqual(len(stage.namespaces()), 102)
        with mock.patch.object(stage, 'cf', side_effect=[page(first,1), page(second,2,103)]):
            with self.assertRaisesRegex(ValueError, 'changing'): stage.namespaces()

    def test_empty_successful_delete_body_still_needs_final_absence(self):
        with mock.patch.dict(stage.os.environ, {'CLOUDFLARE_ACCOUNT_ID':stage.ACCOUNT, 'CLOUDFLARE_API_TOKEN':'secret'}), mock.patch.object(stage, 'request_http', return_value=mock.Mock(status=200, body=b'')):
            self.assertEqual(stage.cf('workers/services/fixed', method='DELETE'), {'success':True})
            with self.assertRaises(ValueError): stage.cf('workers/services/fixed')

    def test_tool_failure_redacts_deployment_token_jwt_age_key_and_long_payload(self):
        token = 'unique-deployment-token'
        error = stage.subprocess.CalledProcessError(1, ['npx'], stderr=('ERROR HTTP403 '+token+' eyJheader.payload.signature AGE-SECRET-KEY-ABCDEF '+ 'A'*256).encode())
        with mock.patch.dict(stage.os.environ, {'CLOUDFLARE_API_TOKEN':token}), mock.patch.object(stage.subprocess, 'check_output', side_effect=error):
            with self.assertRaises(stage.CommandFailure) as captured:
                stage.command('npx','wrangler','deploy')
            text = str(captured.exception)
            self.assertIn('HTTP403', text)
            for secret in (token, 'eyJheader', 'AGE-SECRET-KEY', 'A'*256): self.assertNotIn(secret,text)
            self.assertLess(len(text),4200)

    def test_known_retirement_propagation_retries_but_foreign_identity_does_not(self):
        with mock.patch.object(stage, 'retired_identity', side_effect=[stage.RetirementPending('old view'), 'stub']) as verify, mock.patch.object(stage.time,'sleep'):
            self.assertEqual(stage.wait_retired({}, {}, {}),'stub')
            self.assertEqual(verify.call_count,2)
        with mock.patch.object(stage, 'retired_identity', side_effect=ValueError('foreign')) as verify:
            with self.assertRaisesRegex(ValueError,'foreign'): stage.wait_retired({}, {}, {})
            self.assertEqual(verify.call_count,1)

    def test_ownership_retry_is_bounded_and_missing_creation_record_is_explicit(self):
        with mock.patch.object(stage,'ownership',side_effect=[stage.TransientRpcError('listing lag'),{'worker_version':'captured'}]) as owner, mock.patch.object(stage.time,'sleep'):
            self.assertEqual(stage.wait_ownership({}),{'worker_version':'captured'})
            self.assertEqual(owner.call_count,2)
        with tempfile.TemporaryDirectory() as temp:
            directory=pathlib.Path(temp)
            _,_,receipt,_=self.fixture(directory)
            del receipt['worker_version']
            stage.save(directory/'receipt.json',receipt)
            with mock.patch.object(stage,'ownership') as owner:
                with self.assertRaisesRegex(ValueError,'ownership never recorded; controlled recovery'):stage.cleanup('a-match',directory)
                owner.assert_not_called()
            self.assertEqual(stage.load(directory/'receipt.json')['recovery_required'],'creation-ownership-unrecorded')

    def test_deploy_records_ownership_before_bounded_rollout_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=pathlib.Path(temp)
            _,_,receipt,_=self.fixture(directory)
            receipt['created']=False
            stage.save(directory/'receipt.json',receipt)
            captured={'worker_version':'new-recorded-version'}
            with mock.patch.object(stage,'cf',return_value=None), mock.patch.object(stage,'applications',return_value=[]), mock.patch.object(stage,'namespaces',return_value=[]), mock.patch.object(stage,'command'), mock.patch.object(stage,'wait_ownership',return_value=captured), mock.patch.object(stage.subprocess,'run',side_effect=stage.subprocess.TimeoutExpired('rollout',900)) as run:
                with self.assertRaises(stage.subprocess.TimeoutExpired):stage.deploy('a-match',directory)
                self.assertEqual(stage.load(directory/'receipt.json')['worker_version'],'new-recorded-version')
                self.assertEqual(run.call_args.kwargs['timeout'],900)
                self.assertIn('--attempts',run.call_args.args[0])

    def test_known_intermediate_drift_stops_without_application_or_class_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=pathlib.Path(temp)
            _,_,receipt,_=self.fixture(directory)
            receipt.update(retirement_phase='disabled',disabled_worker_version='expected')
            stage.save(directory/'receipt.json',receipt)
            with mock.patch.object(stage,'wait_retired',return_value='foreign'), mock.patch.object(stage,'command') as command:
                with self.assertRaisesRegex(ValueError,'disabled Worker changed'):stage.cleanup('a-match',directory)
                command.assert_not_called()

    def test_partial_unrecorded_retirement_refuses_without_guessing_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=pathlib.Path(temp)
            _,_,receipt,_=self.fixture(directory)
            receipt['retirement_phase']='disabling'
            stage.save(directory/'receipt.json',receipt)
            with mock.patch.object(stage,'command') as command, mock.patch.object(stage,'cf') as cf:
                with self.assertRaisesRegex(ValueError,'version was not recorded; controlled recovery'):stage.cleanup('a-match',directory)
                command.assert_not_called()
                cf.assert_not_called()

    def test_application_absent_checkpoint_finishes_classes_without_redeleting_app(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=pathlib.Path(temp)
            _,_,receipt,_=self.fixture(directory)
            receipt.update(retirement_phase='application-absent',disabled_worker_version='disabled')
            stage.save(directory/'receipt.json',receipt)
            def identity(config,*args,**kwargs):return 'stub' if not config['durable_objects']['bindings'] else 'disabled'
            with mock.patch.object(stage,'ROOT',directory), mock.patch.object(stage,'ownership') as owner, mock.patch.object(stage,'command') as command, mock.patch.object(stage,'retired_identity',side_effect=identity), mock.patch.object(stage,'applications',return_value=[]), mock.patch.object(stage,'_verify_application',return_value=None), mock.patch.object(stage,'cf',return_value=None), mock.patch.object(stage,'namespaces',return_value=[]):
                (directory/'server').mkdir()
                stage.cleanup('a-match',directory)
                owner.assert_not_called()
                self.assertEqual(command.call_count,1)
                self.assertIn('deploy',command.call_args.args)
                self.assertEqual(stage.load(directory/'receipt.json')['control_plane_cleanup'],'confirmed')

    def test_worker_delete_and_namespace_absence_recover_after_transient_readbacks(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=pathlib.Path(temp)
            _,_,receipt,_=self.fixture(directory)
            receipt.update(retirement_phase='retired',stub_version='stub')
            stage.save(directory/'receipt.json',receipt)
            gets=0
            deletes=0
            def cf(path,method='GET',**kwargs):
                nonlocal gets,deletes
                if method=='DELETE':
                    deletes+=1
                    if deletes==1:raise stage.TransportError('lost response')
                    return {'success':True}
                gets+=1
                return None if gets==3 else {'present':True}
            with mock.patch.object(stage,'cf',side_effect=cf), mock.patch.object(stage,'wait_retired',return_value='stub'), mock.patch.object(stage,'_verify_application',return_value=None), mock.patch.object(stage,'applications',return_value=[]), mock.patch.object(stage,'namespaces_absent',side_effect=[False,stage.TransientRpcError('lag'),True]), mock.patch.object(stage.time,'sleep'):
                stage.cleanup('a-match',directory)
                self.assertEqual(deletes,2)
                self.assertEqual(stage.load(directory/'receipt.json')['control_plane_cleanup'],'confirmed')

    def test_actual_mint_transient_stops_before_any_source_post(self):
        with mock.patch.dict(stage.os.environ,{'ACTIONS_ID_TOKEN_REQUEST_URL':'https://token.example/?q=1','ACTIONS_ID_TOKEN_REQUEST_TOKEN':'private-token'}), mock.patch.object(stage,'request_http',return_value=mock.Mock(status=503,body=b'{}')) as request:
            with self.assertRaises(stage.TransientRpcError):stage.http('https://fixed.example/api/v1/historical-public-replay/status',{},True)
            self.assertEqual(request.call_count,1)
            self.assertTrue(request.call_args.args[1].startswith('https://token.example/'))

    def test_rollout_uses_locked_server_cwd_and_relative_config(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            _, _, receipt, _ = self.fixture(directory)
            receipt['created']=False
            stage.save(directory/'receipt.json',receipt)
            with mock.patch.object(stage,'cf',return_value=None), mock.patch.object(stage,'applications',return_value=[]), mock.patch.object(stage,'namespaces',return_value=[]), mock.patch.object(stage,'command'), mock.patch.object(stage.subprocess,'run') as run, mock.patch.object(stage,'ownership',return_value={}):
                stage.deploy('a-match',directory)
                self.assertEqual(run.call_args.kwargs['cwd'],ROOT/'server')
                args=run.call_args.args[0]
                self.assertIn('../scripts/wait_replay_container_rollout.py',args)
                self.assertEqual(args[args.index('--config')+1],'.public-guard-a-match.json')

    def test_helper_and_all_finite_paths_do_not_match_normal_deploy_filters(self):
        import fnmatch,re
        normal=(ROOT/'.github/workflows/deploy-worker.yml').read_text().split('  workflow_dispatch:',1)[0]
        patterns=re.findall(r"      - '([^']+)'",normal)
        for path in ('runtime-guard-staging/check.py','runtime-guard-staging/retired.js','.github/workflows/public-runtime-guard-staging.yml','.github/workflows/promote-workflow-dispatch-ref.yml','docs/public-runtime-guard-staging.md','tests/test_public_runtime_guard_staging.py','tests/test_worker_deployment_workflow.py'):
            matched=False
            for pattern in patterns:
                if fnmatch.fnmatchcase(path,pattern.lstrip('!')):matched=not pattern.startswith('!')
            self.assertFalse(matched,path)

    def test_runtime_execution_refuses_deployment_credential(self):
        with mock.patch.dict(stage.os.environ, {'CLOUDFLARE_API_TOKEN': 'sentinel'}):
            with self.assertRaisesRegex(ValueError, 'credential reached execution'):
                stage.run_case('a-match', pathlib.Path('/unused'))

    def test_host_drift_stops_before_public_start(self): self.run_fixture('a-match', True)
    def test_negative_guard_requires_exact_typed_failure_and_destruction(self): self.run_fixture('a-mismatch', False)
    def test_positive_case_reaches_actual_accepted_verdict_validation(self): self.run_fixture('a-match', False)
    def test_guard_internal_error_is_not_accepted_as_negative_case(self): self.run_fixture('a-mismatch', False, 'runtime_profile_guard_failed')

    def test_typed_observation_refusal_does_not_claim_sandbox_destruction(self): self.run_fixture('a-match',False,refuse=(401,{'error':'unauthorized'}))
    def test_ambiguous_observation_preserves_unknown_http_outcome(self): self.run_fixture('a-match',False,refuse=(503,{'error':'transport_response_invalid'}))
    def test_two_transient_status_rpcs_can_recover_to_actual_acceptance(self): self.run_fixture('a-match',False,rpc_failures=2)
    def test_health_and_mint_share_poll_allowance_and_recover(self): self.run_fixture('a-match',False,health_faults=1,mint_faults=1)
    def test_third_combined_health_mint_failure_is_incomplete(self): self.run_fixture('a-match',False,health_faults=2,mint_faults=1)
    def test_third_status_rpc_failure_stops_without_success(self): self.run_fixture('a-match',False,rpc_failures=3)
    def test_positive_deadline_is_incomplete_and_cleans_without_mathematical_timeout(self): self.run_fixture('a-match',False,expire=True)

    def run_fixture(self, case, drift, detail='runtime_profile_mismatch', refuse=None, rpc_failures=0, expire=False, health_faults=0, mint_faults=0):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            _, _, receipt, request = self.fixture(directory, case)
            from tests.test_historical_replay_controller import Fixture
            canonical = Fixture()
            request['handoff'] = {'request_id':canonical.task['request_id'], 'result':{'result_id':canonical.task['result_id']}}
            stage.save(directory / 'request.json', request)
            observed = stage.load(directory / 'profile.json')
            if drift: observed['kernel_release'] = 'observed-different-host'
            routes = []
            health_left=health_faults
            mint_left=mint_faults
            started=False
            def command(*args, **kwargs):
                if args[1] == '-o': pathlib.Path(args[2]).write_bytes(b'disposable test age identity')
                return b'age1synthetic'
            def http(url, body=None, oidc=False):
                nonlocal health_left,mint_left,started
                if url.endswith('/healthz'):
                    if started and health_left:
                        health_left-=1
                        return 502,{'error':'transport_response_invalid'}
                    self.assertFalse(oidc)
                    return 200, receipt['health']
                routes.append(url.rsplit('/', 1)[-1])
                self.assertTrue(oidc)
                if routes[-1] == 'staging-acceptance':
                    if refuse:return refuse
                    evidence = {k: body[k] for k in ('schema_version', 'request_id', 'runner_nonce', 'archive_ciphertext_sha256', 'marker_sha256')}
                    evidence.update(service='lean-eval-replay-executor', environment='staging', network_policy='disabled', network_probe='blocked', destruction='confirmed', staging_memory_limit_bytes=12*1024**3, production_memory_gate_bytes=12*1024**3)
                    evidence.update({k: observed[k] for k in ('architecture', 'kernel_release', 'cpu_model')})
                    return 200, evidence
                binding = {k: body[k] for k in ('schema_version', 'replay_task_id', 'attempt')}
                if routes[-1] == 'cleanup-reservation': return 200, {**binding, 'status': 'reserved'}
                if routes[-1] == 'historical-public-replay':
                    started=True
                    return 202, {**binding, 'status': 'running'}
                if routes[-1] == 'status':
                    if mint_left:
                        mint_left-=1
                        raise stage.TransientRpcError('synthetic transient mint failure')
                    if routes.count('status')<=rpc_failures:return 500,{'error':'executor_failed','reason':'command_rpc_failed'}
                    if case.endswith('mismatch'):
                        return 500, dict(error='executor_failed', reason='command_failed', detail=detail)
                    fields=('schema_version','runner_nonce','replay_task_id','attempt','handoff_sha256','source_archive_sha256','execution_profile_digest','measurement_config_digest','vm_image_digest')
                    return 200, {**{k:request[k] for k in fields}, 'contract':'historical_public_executor_v1', 'runner_verdict': canonical.verdict(), 'destruction':'confirmed'}
                if routes[-1] == 'cleanup': return 200, {**binding, 'destruction': 'confirmed'}
                raise AssertionError('unexpected route')
            with mock.patch.dict(stage.os.environ, {'CLOUDFLARE_API_TOKEN': '', 'STATE_WRITE_KEY': ''}), mock.patch.object(stage, 'command', side_effect=command), mock.patch.object(stage.subprocess, 'run', return_value=mock.Mock(stdout=b'ciphertext')), mock.patch.object(stage, 'http', side_effect=http), mock.patch.object(stage.time, 'sleep'), mock.patch.object(stage.time,'monotonic',side_effect=[0,3601] if expire else None, wraps=None if expire else stage.time.monotonic):
                if refuse:
                    with self.assertRaises(ValueError):stage.run_case(case,directory)
                    self.assertEqual(routes,['staging-acceptance'])
                    final=stage.load(directory/'receipt.json')
                    self.assertEqual(final['safe_to_delete'],refuse[0]==401)
                    self.assertNotIn('observation_http_destruction',final)
                    return
                if expire or rpc_failures+health_faults+mint_faults>2:
                    with self.assertRaisesRegex(ValueError,'incomplete' if expire else 'RPC did not recover'):stage.run_case(case,directory)
                    final=stage.load(directory/'receipt.json')
                    self.assertEqual(final['public_http_destruction'],'confirmed')
                    self.assertNotEqual(final.get('outcome'),'accepted-nanoda')
                    if expire:self.assertEqual(final['outcome'],'incomplete')
                    self.assertNotIn('execution_outcome',final)
                    return
                if drift:
                    with self.assertRaisesRegex(ValueError, 'differs from the frozen profile'): stage.run_case(case, directory)
                    self.assertEqual(routes, ['staging-acceptance'])
                else:
                    if detail == 'runtime_profile_mismatch':
                        stage.run_case(case, directory)
                    else:
                        with self.assertRaisesRegex(ValueError, 'exact failure'):
                            stage.run_case(case, directory)
                    self.assertEqual(routes, ['staging-acceptance', 'cleanup-reservation', 'historical-public-replay']+['status']*(rpc_failures+mint_faults+1)+['cleanup'])
                    if detail == 'runtime_profile_mismatch':
                        self.assertEqual(stage.load(directory / 'receipt.json')['outcome'], 'expected-runtime-mismatch' if case.endswith('mismatch') else 'accepted-nanoda')
                    else:
                        self.assertEqual(stage.load(directory / 'receipt.json')['outcome'],'incomplete')
                self.assertTrue(stage.load(directory / 'receipt.json')['safe_to_delete'])
                self.assertFalse((directory / 'marker-identity.age').exists())

    def test_retired_proof_requires_namespace_absence_and_active_code_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            config, _, receipt, _ = self.fixture(pathlib.Path(temp))
            config['durable_objects'] = {'bindings': []}
            config['migrations'].append({'tag':'v3-retired','deleted_classes':['ReplaySandbox','ReplayTerminalReceipt']})
            bindings = [dict(type='plain_text', name=k, text=v) for k,v in config['vars'].items()]
            service = {'result': {'default_environment': {'script': {'etag': 'exact'}}}}
            version = {'result': {'id': 'stub', 'resources': {'bindings': bindings, 'script': {'etag': 'exact'}, 'script_runtime': {'containers': [], 'migration_tag': 'v3-retired'}}}}
            def cf(path, **kwargs):
                if '/versions/' in path: return version
                if path.endswith('/settings'): return {'result': {'bindings': bindings, 'compatibility_date': config['compatibility_date'], 'compatibility_flags': config['compatibility_flags']}}
                return service
            body = dict(status='retired', commit=receipt['commit'], ownership=config['vars']['EXECUTOR_OWNERSHIP_TAG'])
            with mock.patch.object(stage, 'cf', side_effect=cf), mock.patch.object(stage, '_active_version', return_value=('stub', 'tag')), mock.patch.object(stage, 'http', return_value=(503, body)), mock.patch.object(stage, 'namespaces', return_value=[dict(id='ns1', script='unrelated')]):
                with self.assertRaisesRegex(ValueError, 'unexpected namespace identity'): stage.retired_identity(config, receipt)
            with mock.patch.object(stage, 'cf', side_effect=cf), mock.patch.object(stage, '_active_version', return_value=('stub', 'tag')), mock.patch.object(stage, 'http', return_value=(503, body)), mock.patch.object(stage, 'namespaces', return_value=[]):
                self.assertEqual(stage.retired_identity(config, receipt), 'stub')
                version['result']['resources']['script']['etag'] = 'wrong'
                with self.assertRaisesRegex(ValueError, 'code identity differ'): stage.retired_identity(config, receipt)


if __name__ == '__main__': unittest.main()
