"""Local Git and synthetic original-CLI tests; no historical CLI qualification."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_historical_legacy_delta_builds as delta


class LegacyDeltaBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'benchmark'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.toolchain = 'leanprover/lean4:v4.33.0'
        self.driver = (ROOT / 'tests/fixtures/legacy-delta/legacy-workspace-driver.txt').read_bytes()
        self.files = {
            'lean-toolchain': (self.toolchain + '\n').encode(),
            'lakefile.toml': b'name = "benchmark"\n[[lean_exe]]\nname = "lean-eval"\nroot = "EvalTools.Main"\n',
            'lake-manifest.json': b'{"version":"1.1.0","packages":[]}\n',
            'templates/WorkspaceTest.lean': self.driver,
            'EvalTools/Main.lean': b'-- Synthetic source, never a historical CLI build.\n',
            'EvalTools/Generate.lean': b'import LeanEvalGenerator.Core.Generate\n',
            'EvalTools/StartProblem.lean': b'-- synthetic fixture\n',
            'manifests/problems/example.toml': b'id = "example"\nmodule = "Problems.Example"\nholes = ["example"]\nstatement_revision = 3\n',
            'Problems/Example.lean': b'theorem example : True := by sorry\n',
        }
        self.workspace = {
            'Challenge.lean': b'theorem example : True := by sorry\n',
            'Solution.lean': b'import Submission\n',
            'Submission.lean': b'import Challenge\n',
            'WorkspaceTest.lean': self.driver,
            'config.json': b'{"challenge_module":"Challenge","solution_module":"Solution","enable_nanoda":true}\n',
            'lakefile.toml': b'name = "example"\n',
            'lean-toolchain': self.files['lean-toolchain'],
        }
        self.files.update({'generated/example/' + k:v for k,v in self.workspace.items()})
        for name, raw in self.files.items():
            self.put(self.repo / name, raw)
        self.commit()
        self.row = self.requirement()

    @staticmethod
    def put(path, raw, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        if executable:
            path.chmod(0o700)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True).stdout.decode().strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Synthetic original benchmark inputs')

    def requirement(self):
        commit = self.git('rev-parse', 'HEAD')
        row = dict.fromkeys(delta.ROW_FIELDS)
        lean, exporter = delta.TOOLCHAINS[self.toolchain]
        row.update(benchmark_repository='leanprover/lean-eval', benchmark_commit=commit,
            benchmark_tree=self.git('rev-parse', 'HEAD^{tree}'), lane='public',
            harness_contract='legacy_standalone', harness_blob_sha256=delta.digest(self.driver),
            toolchain=self.toolchain, qualification_status='unqualified',
            selected_configuration_path=None, selected_configuration_file_sha256={},
            verification_receipt_bindings=[], manifest_layout='per_problem_v1',
            problem_statement_revisions={'example':3}, workspace_count=1,
            result_count_pending_classification=2, inspection_status='inspected', blockers=[],
            root_lean_toolchain_blob_sha256=delta.digest(self.files['lean-toolchain']),
            root_lake_manifest_sha256=delta.digest(self.files['lake-manifest.json']),
            prospective_components={'kind':'prospective_legacy_component_lock',
                'components':{**copy.deepcopy(delta.COMMON), 'lean4export':{
                    'repository':'leanprover/lean4export', 'commit':exporter}},
                'stock_lean_repository':'leanprover/lean4', 'stock_lean_commit':lean,
                'stock_lean_toolchain':self.toolchain, 'go_toolchain':'go1.25.12',
                'rust_toolchain':'rustc-1.89.0'})
        return self.seal(row)

    @staticmethod
    def seal(row):
        raw = delta.canonical({k:v for k,v in row.items() if k not in {'row_path','row_sha256'}})
        row['row_sha256'] = delta.digest(raw)
        row['row_path'] = row['row_sha256'] + '.json'
        return row

    def matrix(self, rows=None):
        return delta.build_matrix(rows or [self.row], {'input_scope':'synthetic_test'}, self.repo)

    def export(self):
        row = self.matrix()['legacy_rows'][0]
        session = self.root / 'builder'
        result = delta.export_builder(self.repo, row, session)
        return session, row, result

    def builder(self, session, row, mode='good'):
        source = session / 'source'
        prefix = self.root / 'synthetic-toolchain'
        lean_commit = row['component_build_profile']['stock_lean_commit']
        self.put(prefix / 'bin/lean', ('#!/bin/sh\nprintf "%s\\n" "Synthetic Lean ' + lean_commit + '"\n').encode(), True)
        self.put(prefix / 'bin/lake', b'#!/bin/sh\nexit 0\n', True)
        # This executable is explicitly synthetic. It exercises invocation and
        # output binding, not authenticity of a historical compiler/build.
        content = {k:v.decode() for k,v in self.workspace.items()}
        script = ('#!' + sys.executable + '\nimport json, os, pathlib, sys\n'
            'source = pathlib.Path.cwd()\n'
            'with (source / "synthetic-invocations.jsonl").open("a") as f:\n'
            ' f.write(json.dumps({"argv":sys.argv[1:],"env":dict(os.environ)})+"\\n")\n'
            'assert sys.argv[1:] == ["generate","--problem","example"]\n'
            'assert not (source / "generated/example").exists()\n'
            + ('sys.exit(7)\n' if mode == 'failure' else '') +
            'target = source / "generated/example"\ntarget.mkdir(parents=True)\n'
            'for name, raw in ' + repr(content) + '.items():\n (target/name).write_text(raw)\n'
            '(target / "Challenge.lean").write_text("-- freshly generated\\n")\n')
        if mode == 'source_change':
            script += '(source / "Problems/Example.lean").write_text("changed")\n'
        if mode == 'symlink':
            script += '(target/"escape").symlink_to("/etc/passwd")\n'
        if mode == 'cache':
            script += '(target/".lake").mkdir()\n'
        if mode == 'noop':
            script = '#!/bin/sh\nexit 0\n'
        if mode == 'timeout':
            script = ('#!' + sys.executable + '\nimport pathlib,time\n'
                      'pathlib.Path("synthetic-started").touch()\ntime.sleep(60)\n')
        cli = source / '.lake/build/bin/lean-eval'
        self.put(cli, script.encode(), True)
        observation = {'schema_version':1, 'kind':'original_generator_build_observation',
            **{k:row[k] for k in ('benchmark_commit','benchmark_tree','toolchain','generator_source_sha256')},
            'stock_lean_commit':lean_commit, 'generator_binary_sha256':delta.digest(cli.read_bytes()),
            'lean_binary_sha256':delta.digest((prefix/'bin/lean').read_bytes()),
            'lake_binary_sha256':delta.digest((prefix/'bin/lake').read_bytes()),
            'toolchain_prefix':str(prefix)}
        raw = delta.canonical(observation)
        return raw, delta.digest(raw)

    def test_matrix_original_bindings_and_no_authority(self):
        value = self.matrix()
        self.assertEqual((value['total_requirement_count'], value['legacy_result_count_pending_classification']), (1,2))
        row = value['legacy_rows'][0]
        self.assertEqual(row['workspaces'][0]['module'], 'Problems.Example')
        self.assertEqual(row['workspaces'][0]['statement_revision'], 3)
        self.assertEqual(row['qualification_status'], 'unqualified')
        self.assertEqual(value['authority_status'], 'none_build_preparation_is_not_classification_or_activation')
        self.assertNotIn('image_digest', row)
        self.assertNotIn('profile_digest', row)

    def test_stock_rows_explicit_pending_and_legacy_relabel_rejected(self):
        fake = copy.deepcopy(self.row)
        fake['harness_contract'] = 'stock_lake_comparator'
        with self.assertRaises(delta.BuildError):
            self.matrix([fake])
        stock_driver = (ROOT/'tests/fixtures/legacy-delta/stock-workspace-driver.txt').read_bytes()
        self.put(self.repo/'templates/WorkspaceTest.lean', stock_driver)
        self.commit()
        stock = copy.deepcopy(fake)
        stock.update(benchmark_commit=self.git('rev-parse','HEAD'), benchmark_tree=self.git('rev-parse','HEAD^{tree}'),
                     harness_blob_sha256=delta.digest(stock_driver))
        self.seal(stock)
        value = self.matrix([self.row,stock])
        self.assertEqual((value['total_requirement_count'],value['total_result_count_pending_classification']), (2,4))
        self.assertEqual(value['stock_requirement_count_pending'], 1)
        self.assertEqual(value['legacy_requirement_count'], 1)

    def test_duplicate_unknown_and_configuration_fallback_rejected(self):
        with self.assertRaises(delta.BuildError):
            self.matrix([self.row,self.row])
        for updates in ({'harness_contract':'unknown'}, {'selected_configuration_path':'configs/other'},
                        {'toolchain':'leanprover/lean4:v4.36.0-rc2'},
                        {'problem_statement_revisions':{'example':2}},
                        {'root_lake_manifest_sha256':'0'*64}, {'workspace_count':2}):
            with self.subTest(updates=updates), self.assertRaises(delta.BuildError):
                self.matrix([{**self.row, **updates}])

    def test_source_module_missing_rejected(self):
        (self.repo/'Problems/Example.lean').unlink()
        self.commit()
        self.row.update(benchmark_commit=self.git('rev-parse','HEAD'),benchmark_tree=self.git('rev-parse','HEAD^{tree}'))
        with self.assertRaises(delta.BuildError):
            self.matrix()

    def test_committed_symlink_and_multiconfig_root_fallback_rejected(self):
        path = self.repo/'generated/example/Submission.lean'
        path.unlink()
        path.symlink_to('../untrusted')
        self.commit()
        self.row.update(benchmark_commit=self.git('rev-parse','HEAD'),benchmark_tree=self.git('rev-parse','HEAD^{tree}'))
        with self.assertRaises(delta.BuildError):
            self.matrix()
        path.unlink()
        self.put(path,self.workspace['Submission.lean'])
        self.put(self.repo/'supported-toolchains.json', b'{}')
        self.commit()
        self.row.update(benchmark_commit=self.git('rev-parse','HEAD'),benchmark_tree=self.git('rev-parse','HEAD^{tree}'))
        with self.assertRaisesRegex(delta.BuildError, 'multi-config'):
            self.matrix()

    def test_component_family_and_4341_compatibility_binding(self):
        bad = copy.deepcopy(self.row)
        bad['prospective_components']['components']['nanoda']['commit'] = '0'*40
        with self.assertRaises(delta.BuildError):
            delta.legacy_components(bad)
        candidate = copy.deepcopy(self.row)
        candidate['toolchain'] = 'leanprover/lean4:v4.34.1'
        prospect = candidate['prospective_components']
        prospect['stock_lean_toolchain'] = candidate['toolchain']
        prospect['stock_lean_commit'] = delta.TOOLCHAINS[candidate['toolchain']][0]
        prospect['components'] = copy.deepcopy(delta.COMMON)
        result = delta.legacy_components(candidate)
        self.assertEqual(result['components']['lean4export']['commit'], '076e8e57707e813375e8f9da8bf989799ace9680')
        self.assertIn('not_an_upstream4341_tag', result['exporter_4341_binding'])
        self.assertEqual(result['qualification_status'], 'unqualified')

    def test_export_exact_git_without_generation_and_counterfeit_reseal(self):
        session, row, receipt = self.export()
        self.assertFalse(receipt['generator_executed'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), receipt['source_commit'])
        self.assertEqual((session/'source/Problems/Example.lean').read_bytes(), self.files['Problems/Example.lean'])
        self.assertEqual(session.stat().st_mode & 0o777, 0o700)
        changed = copy.deepcopy(row)
        changed['workspaces'][0]['module_source']['sha256'] = '0'*64
        changed['build_row_sha256'] = delta.identity('lean-eval-legacy-delta-build-row-v1', {k:v for k,v in changed.items() if k!='build_row_sha256'})
        with self.assertRaises(delta.BuildError):
            delta.export_builder(self.repo, changed, self.root/'counterfeit')

    def test_synthetic_original_cli_generates_fresh_output_exact_argv_clean_env(self):
        session, row, _ = self.export()
        raw, sha = self.builder(session,row)
        with mock.patch.dict(os.environ, {'GH_TOKEN':'synthetic-sentinel','AWS_SECRET_ACCESS_KEY':'synthetic-sentinel','HTTPS_PROXY':'synthetic-sentinel'}):
            value = delta.materialize(session,raw,sha)
        call = json.loads((session/'source/synthetic-invocations.jsonl').read_text())
        self.assertEqual(call['argv'], ['generate','--problem','example'])
        for forbidden in ('GH_TOKEN','AWS_SECRET_ACCESS_KEY','HTTPS_PROXY'):
            self.assertNotIn(forbidden, call['env'])
        self.assertEqual((session/'workspaces/example/Challenge.lean').read_text(), '-- freshly generated\n')
        self.assertEqual((session/'workspaces/example/lake-manifest.json').read_bytes(),self.files['lake-manifest.json'])
        self.assertEqual(value['original_benchmark_tree'], row['benchmark_tree'])
        self.assertEqual(value['qualification_status'], 'unqualified')
        self.assertNotEqual(value['derived_workspaces'][0]['derived_workspace_sha256'], row['benchmark_tree'])
        with self.assertRaises(delta.BuildError):
            delta.materialize(session,raw,sha)

    def test_generation_failure_single_use_and_no_success_proof(self):
        session, row, _ = self.export()
        raw, sha = self.builder(session,row,'failure')
        for _ in range(2):
            with self.assertRaises(delta.BuildError):
                delta.materialize(session,raw,sha)
        self.assertEqual(len((session/'source/synthetic-invocations.jsonl').read_text().splitlines()),1)
        self.assertFalse((session/'generated-workspace-proof.json').exists())

    def test_timeout_has_claim_no_proof_and_never_reexecutes(self):
        session, row, _ = self.export()
        raw, sha = self.builder(session,row,'timeout')
        with self.assertRaises(subprocess.TimeoutExpired):
            delta.materialize(session,raw,sha,timeout_seconds=0.2)
        self.assertTrue((session/'source/synthetic-started').exists())
        self.assertTrue((session/'generation-claim.json').exists())
        self.assertFalse((session/'generated-workspace-proof.json').exists())
        with self.assertRaises(delta.BuildError):
            delta.materialize(session,raw,sha,timeout_seconds=0.2)

    def test_wrong_compiler_commit_stops_before_claim_or_generator(self):
        session, row, _ = self.export()
        raw, _ = self.builder(session,row)
        observation = json.loads(raw)
        lean = Path(observation['toolchain_prefix'])/'bin/lean'
        self.put(lean,b'#!/bin/sh\nprintf "Wrong Lean commit\\n"\n',True)
        observation['lean_binary_sha256'] = delta.digest(lean.read_bytes())
        raw = delta.canonical(observation)
        with self.assertRaisesRegex(delta.BuildError, 'compiler commit'):
            delta.materialize(session,raw,delta.digest(raw))
        self.assertFalse((session/'generation-claim.json').exists())
        self.assertFalse((session/'source/synthetic-invocations.jsonl').exists())

    def test_missing_or_counterfeit_builder_and_changed_inputs_fail_before_generation(self):
        session, row, _ = self.export()
        raw, sha = self.builder(session,row)
        cli = session/'source/.lake/build/bin/lean-eval'
        original = cli.read_bytes()
        for alteration in ('missing','binary','source','observation'):
            with self.subTest(alteration=alteration):
                if alteration == 'missing':
                    cli.unlink()
                elif alteration == 'binary':
                    self.put(cli,b'#!/bin/sh\nexit 0\n',True)
                elif alteration == 'source':
                    self.put(cli,original,True)
                    self.put(session/'source/Problems/Example.lean',b'changed')
                else:
                    self.put(session/'source/Problems/Example.lean',self.files['Problems/Example.lean'])
                with self.assertRaises(delta.BuildError):
                    delta.materialize(session,raw,'0'*64 if alteration=='observation' else sha)
                self.assertFalse((session/'source/synthetic-invocations.jsonl').exists())

    def test_bad_generated_outputs_have_no_success_proof(self):
        for mode in ('source_change','symlink','cache','noop'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.root) as directory:
                row = self.matrix()['legacy_rows'][0]
                session = Path(directory)/'builder'
                delta.export_builder(self.repo,row,session)
                raw,sha = self.builder(session,row,mode)
                with self.assertRaises(delta.BuildError):
                    delta.materialize(session,raw,sha)
                self.assertFalse((session/'generated-workspace-proof.json').exists())

    def test_duplicate_json_and_content_addressed_requirements(self):
        with self.assertRaises(delta.BuildError):
            delta.decode(b'{"a":1,"a":2}')
        value = dict.fromkeys(delta.MATRIX_FIELDS)
        value.update(schema_version=1, kind='nonfrozen_final_delta_profile_build_readiness',
            activation_status='blocked_pending_classification_profiles_and_exact_final_cutoff',
            qualification_status='unqualified', requirements=[self.row], requirement_count=1,
            result_count_pending_classification=2, unique_benchmark_count=1,
            results_commit='1'*40, baseline_inventory_sha256='2'*64)
        raw = delta.canonical(value)
        path = self.root/'matrix.json'
        self.put(path, raw)
        rowpath = self.root/self.row['row_path']
        self.put(rowpath,delta.canonical({k:v for k,v in self.row.items() if k not in {'row_path','row_sha256'}}))
        self.assertEqual(delta.load_requirements(path,delta.digest(raw))[0], [self.row])
        self.put(rowpath,b'{}')
        with self.assertRaises(delta.BuildError):
            delta.load_requirements(path,delta.digest(raw))
        with self.assertRaises(delta.BuildError):
            delta.load_requirements(path,'0'*64)


if __name__ == '__main__':
    unittest.main()
