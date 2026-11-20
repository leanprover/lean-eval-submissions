from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import stock_replay_contract as stock  # noqa: E402
from replay_orchestrator import (  # noqa: E402
    ReplayError, canonical_json_bytes, plan_next, replay_task_id,
    validate_execution_profile, validate_execution_request, validate_measurement_config,
)
from results_schema import canonical_file_bytes, result_id  # noqa: E402

FIXTURES = ROOT / 'tests' / 'fixtures'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def blob(raw):
    return {'git_blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest(),
            'sha256': sha(raw)}


def load(name):
    return json.loads((FIXTURES / name).read_bytes())


def refresh(request):
    configuration = request['benchmark']['configuration_lock']
    profile = request['execution_profile']
    profile['benchmark_configuration_digest'] = stock.configuration_digest(configuration)
    p, m = stock.stock_digests(profile, request['measurement_config'], configuration)
    request.update(execution_profile_digest=p, measurement_config_digest=m,
                   replay_task_id=replay_task_id(request['result']['result_id'], m))


class StockReplayContractTests(unittest.TestCase):
    def setUp(self):
        fixture = load('stock-replay-contract-v1.json')
        self.request = fixture['request']
        self.files = {name: raw.encode() for name, raw in fixture['benchmark_files_utf8'].items()}
        self.raw = fixture['authority_raw_utf8'].encode()
        self.expected = fixture['expected']
        self.commit = self.request['benchmark']['configuration_authority']['commit']
        self.path = self.request['benchmark']['configuration_authority']['path']

    def validate(self):
        return stock.validate_stock_request(self.request, benchmark_files=self.files,
            authority_raw=self.raw, authority_commit=self.commit, authority_path=self.path)

    def authority(self, value):
        self.raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        self.request['benchmark']['configuration_authority']['sha256'] = sha(self.raw)

    def git_source(self):
        self.request['source'] = {'visibility': 'public', 'fetch_method': 'git',
            'archive_capacity_class': None, 'repository': 'synthetic-owner/submission',
            'commit': self.request['result']['commit']}
        self.request['execution_profile']['source_capability'] = {
            'fetch_method': 'git', 'archive_capacity_class': None}
        self.request['network']['fetch_phase'] = 'public_https_only'
        refresh(self.request)

    def select_configuration(self, commit, toolchain):
        configuration = self.request['benchmark']['configuration_lock']
        tree, directory = stock.SELECTIONS[(commit, toolchain)]
        configuration.update(benchmark_commit=commit, benchmark_tree=tree, toolchain=toolchain,
            selection={'kind': 'root' if directory == '.' else 'toolchain_directory', 'path': directory})
        for name in configuration['selected_files']:
            path = name if directory == '.' else directory + '/' + name
            self.files[path] = (toolchain + '\n').encode() if name == 'lean-toolchain' else b'synthetic bytes\n'
            configuration['selected_files'][name] = blob(self.files[path])
        if directory == '.':
            configuration.update(supported_toolchains=None, prepare_toolchain=None)
        else:
            self.files['supported-toolchains.json'] = json.dumps({'configurations': {toolchain: directory}}).encode()
            configuration['supported_toolchains'].update(blob(self.files['supported-toolchains.json']))
        self.request['benchmark'].update(commit=commit, toolchain=toolchain)
        profile = self.request['execution_profile']
        profile['toolchain'] = toolchain
        lean, nanoda = stock.STOCK_COMPONENTS[toolchain]
        for name in ('lean', 'lake', 'leanexport', 'leanchecker'):
            profile['components'][name]['commit'] = lean
        profile['components']['nanoda']['commit'] = nanoda
        profile['release_artifact']['name'] = 'lean-' + toolchain.split(':v')[1] + '-linux.tar.zst'
        if directory != '.':
            receipt = json.loads(self.raw)
            receipt.update(benchmark_commit=commit, toolchain=toolchain)
            self.authority(receipt)
        refresh(self.request)

    def root_authority(self):
        record = {'result_id': self.request['result']['result_id'], 'problem_id': 'synthetic_problem',
            'statement_revision': 1, 'declared_model': 'synthetic-model',
            'accepted_at': '2026-01-01T00:00:00Z', 'benchmark_commit': self.request['benchmark']['commit'],
            'intake': {'kind': 'issue', 'issue_number': 123},
            'submission': {'kind': 'github_repo', 'repo': 'synthetic-owner/submission',
                'ref': self.request['result']['commit'], 'public': self.request['source']['visibility'] == 'public'},
            'production_metadata': {}}
        self.raw = canonical_file_bytes({'schema_version': 2, 'user': 'synthetic-owner', 'results': [record]})
        self.path = 'results/synthetic-owner.json'
        self.request['benchmark']['configuration_authority'] = {
            'kind': 'original_root_git_v1', 'repository': 'leanprover/lean-eval-submissions',
            'commit': self.commit, 'path': self.path, 'sha256': sha(self.raw),
            'entry_sha256': sha(canonical_json_bytes(record)),
            'root_toolchain': copy.deepcopy(self.request['benchmark']['configuration_lock']['selected_files']['lean-toolchain'])}

    def test_golden_bytes_domains_and_task_identity(self):
        self.assertIs(self.validate(), self.request)
        values = [('configuration_digest', stock.CONFIGURATION_DOMAIN, self.request['benchmark']['configuration_lock']),
                  ('execution_profile_digest', stock.PROFILE_DOMAIN, self.request['execution_profile']),
                  ('measurement_config_digest', stock.MEASUREMENT_DOMAIN, self.request['measurement_config'])]
        for name, domain, value in values:
            # Independent serialization/hash expression for later TS/runtime parity.
            encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            self.assertEqual(sha(domain.encode() + b'\0' + encoded), self.expected[name])
        expected_task = 'rt1_' + sha(b'lean-eval-replay-task-v1\0' + self.request['result']['result_id'].encode()
            + b'\0' + self.expected['measurement_config_digest'].encode())
        self.assertEqual(expected_task, self.expected['replay_task_id'])
        self.assertNotEqual(self.expected['measurement_config_digest'],
            load('historical-archive-executor-request-v2.json')['request']['measurement_config_digest'])

    def test_all_four_bounded_configurations_and_both_source_branches(self):
        for commit, toolchain in stock.SELECTIONS:
            for use_git in (False, True):
                with self.subTest(commit=commit, toolchain=toolchain, git=use_git):
                    self.setUp()
                    self.select_configuration(commit, toolchain)
                    if use_git:
                        self.git_source()
                    if self.request['benchmark']['configuration_lock']['selection']['kind'] == 'root':
                        self.root_authority()
                    self.validate()

    def test_receipt_is_actual_closed_shape_and_independently_located(self):
        original = json.loads(self.raw)
        for key, value in [('toolchain', 'leanprover/lean4:v4.36.0-rc2'), ('result_id', 'r2_' + '0' * 64),
                           ('benchmark_commit', '0' * 40), ('run_id', 124), ('run_attempt', True),
                           ('selected_directory', 'toolchains/v4.35.0')]:
            with self.subTest(key=key):
                altered = dict(original, **{key: value})
                self.authority(altered)
                with self.assertRaises(stock.StockContractError):
                    self.validate()
        self.setUp()
        self.commit = '2' * 40
        with self.assertRaisesRegex(stock.StockContractError, 'locator'):
            self.validate()
        self.setUp()
        self.path = 'verification/r2_' + '2' * 64 + '.json'
        with self.assertRaisesRegex(stock.StockContractError, 'locator'):
            self.validate()
        self.setUp()
        self.raw += b' '
        with self.assertRaisesRegex(stock.StockContractError, 'bytes differ'):
            self.validate()

    def test_root_binding_uses_original_result_without_receipt_run_fields(self):
        self.select_configuration(*next(iter(stock.SELECTIONS)))
        self.git_source()
        self.root_authority()
        self.validate()
        locator = self.request['benchmark']['configuration_authority']
        self.assertNotIn('run_id', locator)
        self.assertNotIn('run_attempt', locator)
        original_raw = self.raw
        for field, bad in [('benchmark_commit', '0' * 40), ('statement_revision', 2),
                           ('result_id', result_id('synthetic-owner', 'other-model', 'synthetic_problem', 1))]:
            with self.subTest(field=field):
                document = json.loads(original_raw)
                document['results'][0][field] = bad
                self.authority(document)
                locator['entry_sha256'] = sha(canonical_json_bytes(document['results'][0]))
                with self.assertRaises(stock.StockContractError):
                    self.validate()
        self.raw = original_raw
        locator['sha256'] = sha(self.raw)
        locator['entry_sha256'] = sha(canonical_json_bytes(json.loads(self.raw)['results'][0]))
        locator['root_toolchain']['git_blob'] = '0' * 40
        with self.assertRaisesRegex(stock.StockContractError, 'root pin'):
            self.validate()

    def test_authority_family_does_not_fallback(self):
        self.root_authority()  # original Results cannot authorize selected directory
        with self.assertRaisesRegex(stock.StockContractError, 'cannot select'):
            self.validate()
        self.setUp()
        self.select_configuration(*next(iter(stock.SELECTIONS)))
        with self.assertRaisesRegex(stock.StockContractError, 'requires original Git'):
            self.validate()

    def test_root_result_source_and_owner_cannot_be_substituted(self):
        for field, value in [('repo', 'other-owner/submission'), ('ref', '0' * 40),
                              ('public', False), ('kind', 'gist')]:
            with self.subTest(field=field):
                self.setUp()
                self.select_configuration(*next(iter(stock.SELECTIONS)))
                self.git_source()
                self.root_authority()
                document = json.loads(self.raw)
                document['results'][0]['submission'][field] = value
                self.authority(document)
                self.request['benchmark']['configuration_authority']['entry_sha256'] = sha(
                    canonical_json_bytes(document['results'][0]))
                with self.assertRaises(stock.StockContractError):
                    self.validate()
        self.setUp()
        self.select_configuration(*next(iter(stock.SELECTIONS)))
        self.root_authority()
        self.path = 'results/another-owner.json'
        self.request['benchmark']['configuration_authority']['path'] = self.path
        with self.assertRaisesRegex(stock.StockContractError, 'owner/path'):
            self.validate()

    def test_configuration_bytes_registry_tree_and_paths_are_bound(self):
        configuration = self.request['benchmark']['configuration_lock']
        for path in self.files:
            with self.subTest(path=path):
                original = self.files[path]
                self.files[path] += b'changed'
                with self.assertRaises(stock.StockContractError):
                    self.validate()
                self.files[path] = original
        self.files['supported-toolchains.json'] = b'{"configurations":{"leanprover/lean4:v4.35.0":"."}}'
        configuration['supported_toolchains'].update(blob(self.files['supported-toolchains.json']))
        refresh(self.request)
        with self.assertRaisesRegex(stock.StockContractError, 'select the bound directory'):
            self.validate()
        self.setUp()
        configuration = self.request['benchmark']['configuration_lock']
        configuration['benchmark_tree'] = '0' * 40
        with self.assertRaisesRegex(stock.StockContractError, 'tree differs'):
            self.validate()
        configuration['benchmark_tree'] = stock.SELECTIONS[(configuration['benchmark_commit'], configuration['toolchain'])][0]
        configuration['generator_source_binding']['sources'][0]['path'] = '../escape'
        with self.assertRaises(stock.StockContractError):
            self.validate()

    def test_statement_source_archive_and_network_cross_bindings(self):
        mutations = [lambda r: r['result'].update(statement_revision=2),
            lambda r: r['source']['archive'].update(submission_id='0198abcd-0000-7000-8000-000000000002'),
            lambda r: r['source']['archive'].update(archive_path='archives/ff/0198abcd-0000-7000-8000-000000000001.tar.age'),
            lambda r: r['network'].update(fetch_phase='public_https_only'),
            lambda r: r['execution_profile'].update(source_capability={'fetch_method': 'git', 'archive_capacity_class': None}),
            lambda r: r['source'].update(archive_capacity_class='large_30mib'),
            lambda r: r['source'].update(repository='synthetic-owner/fallback')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.setUp()
                mutate(self.request)
                # Keep unrelated hash checks current to test the substantive fence.
                try:
                    refresh(self.request)
                except stock.StockContractError:
                    pass
                with self.assertRaises(stock.StockContractError):
                    self.validate()
        self.setUp()
        self.git_source()
        self.request['source']['commit'] = '0' * 40
        with self.assertRaisesRegex(stock.StockContractError, 'source/result'):
            self.validate()
        self.setUp()
        self.git_source()
        self.request['source']['visibility'] = 'private'
        with self.assertRaises(stock.StockContractError):
            self.validate()

    def test_stock_component_family_and_release_cross_products_reject(self):
        mutations = [lambda p: p['components']['nanoda'].update(repository='robsimmons/nanoda_lib'),
            lambda p: p['components']['nanoda'].update(commit='68d5ca9db226849b41a6fff59d796ff19d0a8840'),
            lambda p: p['components']['leanexport'].update(commit='0' * 40),
            lambda p: p['components'].update(landrun={'repository': 'zouuup/landrun', 'commit': '0' * 40}),
            lambda p: p['components'].pop('leanchecker'),
            lambda p: p['release_artifact'].update(name='lean-4.36.0-rc2-linux.tar.zst'),
            lambda p: p.update(measurement_command=['perf', 'stat']),
            lambda p: p.update(cpu_model='unavailable')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.setUp()
                mutate(self.request['execution_profile'])
                with self.assertRaises(stock.StockContractError):
                    self.validate()

    def test_versions_closed_shapes_safe_numbers_and_text(self):
        mutations = [lambda r: r.update(schema_version=2),
            lambda r: r['execution_profile'].update(schema_version=2),
            lambda r: r['measurement_config'].update(schema_version=1),
            lambda r: r['measurement_config'].update(phase_semantics='legacy'),
            lambda r: r['measurement_config'].update(checker_sequence=['bundled_leanchecker', 'bundled_nanoda']),
            lambda r: r['measurement_config'].update(wall_time_limit_ms=True),
            lambda r: r['measurement_config'].update(wall_time_limit_ms=1.0),
            lambda r: r['measurement_config'].update(wall_time_limit_ms=9007199254740992),
            lambda r: r.update(attempt=5), lambda r: r.update(extra=None),
            lambda r: r['untrusted_environment'].update(TOKEN='forbidden'),
            lambda r: r['result'].update(result_id=r['result']['result_id'] + '\n'),
            lambda r: r['execution_profile'].update(kernel_release='x\x00y')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.setUp()
                mutate(self.request)
                with self.assertRaises(stock.StockContractError):
                    self.validate()

    def test_authority_duplicate_json_and_depth_fail_closed(self):
        for raw in [self.raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'),
                    b'{"schema_version":NaN}', b'[' * 25 + b'0' + b']' * 25]:
            self.raw = raw
            self.request['benchmark']['configuration_authority']['sha256'] = sha(raw)
            with self.assertRaises(stock.StockContractError):
                self.validate()

    def test_active_readers_reject_new_versions_and_keep_legacy_vectors(self):
        for validator, value in [(validate_execution_profile, self.request['execution_profile']),
                (validate_measurement_config, self.request['measurement_config']),
                (validate_execution_request, self.request)]:
            with self.subTest(validator=validator.__name__):
                with self.assertRaises(ReplayError):
                    validator(value)
        profile = load('replay-execution-profile-v1.json')
        measurement = load('replay-measurement-config-v1.json')
        queue = load('replay-queue-public-v1.json')
        legacy = plan_next(queue, profile, measurement)['request']
        self.assertIs(validate_execution_request(legacy), legacy)
        archived = load('historical-archive-executor-request-v2.json')['request']
        self.assertIs(validate_execution_request(archived), archived)
        self.assertEqual(archived['execution_profile_digest'], '20d66ce366bd63a2333cb32806dba3217a3906e0e576bc9e6a55f0d1cc3b7cbb')
        self.assertEqual(archived['measurement_config_digest'], 'e7c3af7e28623d048eff37c1c23ee1dba293c6788400cc683d692b1dae2a74f0')
        for value, validator in [(profile, lambda v: stock.validate_stock_profile(v, self.request['benchmark']['configuration_lock'])),
                                 (measurement, stock.validate_stock_measurement)]:
            with self.assertRaises(stock.StockContractError):
                validator(value)

    def test_legacy_schema_bytes_unchanged_and_schema_refs_are_local(self):
        import jsonschema
        import referencing

        for name, expected in load('stock-replay-contract-v1.json')['legacy_schema_sha256'].items():
            self.assertEqual(sha((ROOT / 'schemas' / name).read_bytes()), expected)
        registry = referencing.Registry()
        documents = {}
        for name in stock.SCHEMAS:
            document = json.loads((ROOT / 'schemas' / name).read_bytes())
            documents[name] = document
            registry = registry.with_resource(document['$id'], referencing.Resource.from_contents(document))
        validator = jsonschema.Draft202012Validator(documents[stock.SCHEMAS[3]], registry=registry)
        validator.validate(self.request)
        self.request['result']['result_id'] += '\n'
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(self.request)
        self.setUp()
        self.request['benchmark']['configuration_lock']['problem_statement_revisions'] = {'synthetic_problem\n': 1}
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(self.request)


if __name__ == '__main__':
    unittest.main()
