#!/usr/bin/env python3
"""Prepare exact legacy delta build inputs and run the original workspace generator.

Build provenance is not historical classification, image qualification or task
authority. The existing baseline generators and selectors are not modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tomllib

COMMIT = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
PROBLEM = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
MODULE = re.compile(r'[A-Za-z_][A-Za-z0-9_.]*\Z')
MAX_JSON = 16 * 1024 * 1024
MAX_BLOB = 16 * 1024 * 1024
MAX_FILES = 50000
HARNESS_HASHES = {'d550bdc174f54b363443785e203b3d84146b52434e80219640e15c8066806beb',
                  '17bd1978ec5ec46b151a616ae626d14cef01fcb6ce90a2d104dffae9a95e40df'}
STOCK_HARNESS = '082806a7a6db89461febef4cf50c1d8c620945f96cd1a214a0b481a4cb6ee971'
COMMON = {
    'comparator': {'repository': 'leanprover/comparator', 'commit': '71b52ec29e06d4b7d882726553b1ceb99a2499e0'},
    'landrun': {'repository': 'zouuup/landrun', 'commit': '5ed4a3db3a4ad930d577215c6b9abaa19df7f99f'},
    'nanoda': {'repository': 'robsimmons/nanoda_lib', 'commit': '68d5ca9db226849b41a6fff59d796ff19d0a8840'},
}
TOOLCHAINS = {
    'leanprover/lean4:v4.33.0': ('d8b18978322de05a8f3dba51ef03cf5461676c17', '15f6055e299ad5b89345e533cc2192f4cc00f659'),
    'leanprover/lean4:v4.34.0': ('293d5d0c0c3f3dded4688b3ccd6a33939ac5102b', '076e8e57707e813375e8f9da8bf989799ace9680'),
    'leanprover/lean4:v4.34.1': ('5045d0056413266e57c625dcd7c365b10e377c52', '076e8e57707e813375e8f9da8bf989799ace9680'),
    'leanprover/lean4:v4.35.0-rc3': ('470d5ce1400764999581fd26d5d72b00d990b0f4', '66f1fb4bc256072069767fce52d39480e4524869'),
}
ROW_FIELDS = set('benchmark_commit benchmark_repository benchmark_tree blockers harness_blob_sha256 harness_contract inspection_status lane manifest_layout problem_statement_revisions prospective_components qualification_status result_count_pending_classification root_lake_manifest_sha256 root_lean_toolchain_blob_sha256 selected_configuration_file_sha256 selected_configuration_path toolchain verification_receipt_bindings workspace_count row_path row_sha256'.split())
MATRIX_FIELDS = set('activation_status baseline_inventory_sha256 blocker_row_counts effective_toolchain_result_counts harness_counts kind lane_counts official_stock_component_metadata qualification_status requirement_count requirements result_count_pending_classification results_commit schema_version unique_benchmark_count'.split())


class BuildError(ValueError):
    """Exact original inputs or a bounded build observation do not match."""


def require(condition, message='legacy build input is invalid'):
    if not condition:
        raise BuildError(message)


def number(value, minimum=0):
    return type(value) is int and minimum <= value <= 2**53-1


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(domain, value):
    return digest(domain.encode('ascii') + b'\0' + canonical(value))


def decode(raw):
    require(len(raw) <= MAX_JSON)
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'JSON has duplicate fields')
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(BuildError('JSON is nonfinite')))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise BuildError('JSON is not a bounded document') from error


def read(path, maximum=MAX_JSON):
    require(not path.is_symlink() and path.is_file(), 'input is not a regular file')
    require(path.stat().st_size <= maximum, 'input exceeds its limit')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum)
    return raw


def write_new(path, value):
    require(not path.exists() and not path.is_symlink(), 'output already exists')
    raw = canonical(value)
    require(len(raw) <= MAX_JSON, 'output exceeds its limit')
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)


def run_git(repository, *arguments, maximum=MAX_BLOB):
    try:
        result = subprocess.run(['git', '-C', str(repository), *arguments],
                                capture_output=True, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise BuildError('required local Git evidence is unavailable') from error
    require(len(result.stdout) <= maximum, 'Git metadata exceeds its limit')
    return result.stdout


class GitInputs:
    def __init__(self, repository, commit):
        self.repository, self.commit = Path(repository), commit
        require(isinstance(commit, str) and COMMIT.fullmatch(commit))
        require(run_git(self.repository, 'rev-parse', commit + '^{commit}').decode().strip() == commit)
        self.tree = run_git(self.repository, 'rev-parse', commit + '^{tree}').decode().strip()
        require(COMMIT.fullmatch(self.tree))
        raw = run_git(self.repository, 'ls-tree', '-rz', '--full-tree', commit, maximum=MAX_JSON)
        self.files, self.cache = {}, {}
        for entry in raw.rstrip(b'\0').split(b'\0'):
            metadata, name = entry.split(b'\t', 1)
            mode, kind, oid = metadata.decode('ascii').split()
            name = name.decode('utf-8')
            require(name not in self.files and not name.startswith('/') and '..' not in Path(name).parts)
            self.files[name] = {'mode': mode, 'kind': kind, 'git_blob': oid}
        require(0 < len(self.files) <= MAX_FILES)

    def blob(self, path):
        entry = self.files.get(path)
        require(entry is not None and entry['kind'] == 'blob' and entry['mode'] in {'100644', '100755'}, 'required Git path is not a regular blob')
        if path not in self.cache:
            size = int(run_git(self.repository, 'cat-file', '-s', entry['git_blob']).decode())
            require(0 <= size <= MAX_BLOB, 'required Git blob exceeds its limit')
            raw = run_git(self.repository, 'cat-file', 'blob', entry['git_blob'], maximum=size)
            require(len(raw) == size)
            self.cache[path] = raw
        return self.cache[path]

    def pin(self, path):
        raw = self.blob(path)
        return {'path': path, 'git_blob': self.files[path]['git_blob'], 'mode': self.files[path]['mode'],
                'sha256': digest(raw), 'bytes': len(raw)}


def load_requirements(path, expected_sha256):
    raw = read(path)
    require(isinstance(expected_sha256, str) and DIGEST.fullmatch(expected_sha256)
            and digest(raw) == expected_sha256, 'requirements bytes differ from expected digest')
    value = decode(raw)
    require(isinstance(value, dict) and set(value) == MATRIX_FIELDS)
    require(type(value['schema_version']) is int and value['schema_version'] == 1)
    require(value['kind'] == 'nonfrozen_final_delta_profile_build_readiness'
            and value['activation_status'] == 'blocked_pending_classification_profiles_and_exact_final_cutoff'
            and value['qualification_status'] == 'unqualified', 'input is not the preparation snapshot contract')
    require(isinstance(value['requirements'], list) and 0 < len(value['requirements']) <= 1024)
    for field in ('baseline_inventory_sha256',):
        require(isinstance(value[field], str) and DIGEST.fullmatch(value[field]))
    require(isinstance(value['results_commit'], str) and COMMIT.fullmatch(value['results_commit']))
    rows, seen = [], set()
    for row in value['requirements']:
        require(isinstance(row, dict) and set(row) == ROW_FIELDS)
        sha = row['row_sha256']
        require(isinstance(sha, str) and DIGEST.fullmatch(sha) and sha not in seen)
        require(row['row_path'] == sha + '.json', 'row locator is not content addressed')
        document = read(path.parent / row['row_path'])
        require(digest(document) == sha and decode(document) == {k:v for k,v in row.items() if k not in {'row_sha256','row_path'}}, 'row bytes differ from embedded evidence')
        seen.add(sha); rows.append(row)
    require(value['requirement_count'] == len(rows) and type(value['requirement_count']) is int)
    require(value['result_count_pending_classification'] == sum(r['result_count_pending_classification'] for r in rows))
    require(value['unique_benchmark_count'] == len({r['benchmark_commit'] for r in rows}))
    return rows, {'input_sha256': digest(raw), 'results_commit': value['results_commit'],
                  'baseline_inventory_sha256': value['baseline_inventory_sha256'],
                  'input_scope': 'nonfrozen_preparation_snapshot_not_historical_authority'}


def legacy_components(row):
    toolchain = row['toolchain']
    require(toolchain in TOOLCHAINS, 'legacy toolchain needs a separately reviewed component binding')
    lean, exporter = TOOLCHAINS[toolchain]
    prospective = row['prospective_components']
    require(isinstance(prospective, dict) and prospective.get('kind') == 'prospective_legacy_component_lock')
    require(prospective.get('stock_lean_repository') == 'leanprover/lean4'
            and prospective.get('stock_lean_commit') == lean and prospective.get('stock_lean_toolchain') == toolchain)
    require(prospective.get('go_toolchain') == 'go1.25.12' and prospective.get('rust_toolchain') == 'rustc-1.89.0')
    components = prospective.get('components')
    require(isinstance(components, dict))
    require(all(components.get(name) == value for name,value in COMMON.items()))
    expected = {**{name:dict(value)for name,value in COMMON.items()},
                'lean4export': {'repository': 'leanprover/lean4export', 'commit': exporter}}
    if toolchain == 'leanprover/lean4:v4.34.1':
        require(components == COMMON or components == expected)
    else:
        require(components == expected, 'legacy components differ from reviewed replay pins')
    return {'schema_version': 1, 'components': expected, 'go_toolchain': 'go1.25.12',
            'rust_toolchain': 'rustc-1.89.0', 'stock_lean_repository': 'leanprover/lean4',
            'stock_lean_commit': lean, 'stock_lean_toolchain': toolchain,
            'cache_state': 'cold', 'measurement_command': ['/opt/lean-eval/replay-measure'],
            'exporter_4341_binding': ('source076e_build_with_original4341_not_an_upstream4341_tag'
                                     if toolchain.endswith(':v4.34.1') else None),
            'qualification_status': 'unqualified'}


def inspect_legacy(row, git):
    require(row['benchmark_repository'] == 'leanprover/lean-eval' and row['benchmark_tree'] == git.tree)
    require(row['harness_contract'] == 'legacy_standalone' and row['qualification_status'] == 'unqualified')
    require(row['lane'] in {'public','private'} and number(row['result_count_pending_classification'], 1))
    require(row['selected_configuration_path'] is None and row['selected_configuration_file_sha256'] == {}
            and not row['verification_receipt_bindings'], 'legacy selected-configuration authority requires a new exact contract')
    require('supported-toolchains.json' not in git.files, 'a root profile cannot silently select a multi-config benchmark')
    root = {name: git.pin(name) for name in ('lean-toolchain','lakefile.toml','lake-manifest.json')}
    require(git.blob('lean-toolchain').decode('ascii').strip() == row['toolchain']
            and root['lean-toolchain']['sha256'] == row['root_lean_toolchain_blob_sha256']
            and root['lake-manifest.json']['sha256'] == row['root_lake_manifest_sha256'])
    harness = git.pin('templates/WorkspaceTest.lean')
    require(harness['sha256'] == row['harness_blob_sha256'] and harness['sha256'] in HARNESS_HASHES)
    generator_paths = sorted(p for p in git.files if p.startswith('EvalTools/') and p.endswith('.lean'))
    require({'EvalTools/Main.lean','EvalTools/Generate.lean','EvalTools/StartProblem.lean'} <= set(generator_paths))
    generator = [git.pin(p) for p in generator_paths]
    lakefile = tomllib.loads(git.blob('lakefile.toml').decode())
    require(any(v.get('name') == 'lean-eval' and v.get('root') == 'EvalTools.Main' for v in lakefile.get('lean_exe',[])))
    require(row['manifest_layout'] == 'per_problem_v1', 'original manifest layout requires explicit implementation')
    revisions = row['problem_statement_revisions']
    require(isinstance(revisions,dict) and revisions)
    workspaces = []
    for problem, revision in sorted(revisions.items()):
        require(isinstance(problem,str) and PROBLEM.fullmatch(problem) and number(revision,1))
        manifest_path = 'manifests/problems/' + problem + '.toml'
        manifest = tomllib.loads(git.blob(manifest_path).decode())
        require(manifest.get('id') == problem and manifest.get('statement_revision',1) == revision
                and type(manifest.get('statement_revision',1)) is int)
        module = manifest.get('module')
        require(isinstance(module,str) and MODULE.fullmatch(module))
        module_path = module.replace('.','/') + '.lean'
        holes = manifest.get('holes')
        require(isinstance(holes,list) and holes and all(isinstance(h,str) and h for h in holes))
        prefix = 'generated/' + problem + '/'
        paths = sorted(p for p in git.files if p.startswith(prefix))
        required = {'Challenge.lean','Solution.lean','Submission.lean','WorkspaceTest.lean','config.json','lakefile.toml','lean-toolchain'}
        require(required <= {p[len(prefix):] for p in paths}, 'committed original workspace is incomplete')
        require(all(git.files[p]['mode'] == '100644' for p in paths), 'committed workspace contains a nonregular/executable input')
        config = decode(git.blob(prefix+'config.json'))
        require(isinstance(config,dict) and config.get('challenge_module') == 'Challenge'
                and config.get('solution_module') == 'Solution' and type(config.get('enable_nanoda')) is bool
                and 'measurement_command' not in config, 'original legacy workspace configuration is not pristine')
        require(git.blob(prefix+'lean-toolchain') == git.blob('lean-toolchain'))
        require(git.blob(prefix+'WorkspaceTest.lean') == git.blob('templates/WorkspaceTest.lean'))
        workspace_lakefile = tomllib.loads(git.blob(prefix+'lakefile.toml').decode())
        require(workspace_lakefile.get('name') == problem)
        workspaces.append({'problem_id':problem,'statement_revision':revision,
            'revision_provenance':'explicit_manifest' if 'statement_revision'in manifest else 'original_default_revision1',
            'manifest':git.pin(manifest_path),'module':module,'module_source':git.pin(module_path),
            'holes':holes,'committed_workspace_files':[git.pin(p) for p in paths]})
    count = len({p.split('/')[1] for p in git.files if re.fullmatch(r'generated/[^/]+/lakefile\.toml',p)})
    require(count == row['workspace_count'])
    binding = {'row_sha256':row['row_sha256'],'benchmark_repository':row['benchmark_repository'],
        'benchmark_commit':git.commit,'benchmark_tree':git.tree,'toolchain':row['toolchain'],
        'original_visibility':row['lane'],'result_count_pending_classification':row['result_count_pending_classification'],
        'selection':{'kind':'original_root','path':None},'configuration_files':root,
        'original_harness':harness,'original_generator_sources':generator,
        'generator_source_sha256':identity('lean-eval-original-generator-source-v1',generator),
        'committed_workspace_count':count,'workspaces':workspaces,'component_build_profile':legacy_components(row),
        'qualification_status':'unqualified','generation_status':'requires_original_generator_execution',
        'runtime_source_route_and_capacity':'not_selected_by_workspace_preparation'}
    return {**binding,'build_row_sha256':identity('lean-eval-legacy-delta-build-row-v1',binding)}


def build_matrix(rows, provenance, benchmark_repository):
    """Reusable build layer; a future classified selector can supply exact rows.

    This function validates build inputs only. It never treats provenance or a
    caller's classification label as replay/enqueue/image authority.
    """
    require(isinstance(rows,list) and rows)
    legacy, stock, cache, seen = [],[],{},set()
    for row in rows:
        require(isinstance(row,dict) and set(row)==ROW_FIELDS)
        require(isinstance(row['row_sha256'],str) and DIGEST.fullmatch(row['row_sha256'])
                and row['row_sha256'] not in seen);seen.add(row['row_sha256'])
        require(number(row['result_count_pending_classification'],1))
        require(row['lane']in {'public','private'} and row['benchmark_repository']=='leanprover/lean-eval'
                and row['qualification_status']=='unqualified')
        commit=row['benchmark_commit']
        if commit not in cache:cache[commit]=GitInputs(benchmark_repository,commit)
        git=cache[commit]
        require(git.tree==row['benchmark_tree']
                and git.pin('templates/WorkspaceTest.lean')['sha256']==row['harness_blob_sha256'])
        if row['harness_contract'] == 'stock_lake_comparator':
            require(row['harness_blob_sha256']==STOCK_HARNESS,'a legacy/unknown harness cannot be hidden as stock pending')
            stock.append({'row_sha256':row['row_sha256'],'benchmark_commit':row['benchmark_commit'],
                'toolchain':row['toolchain'],'original_visibility':row['lane'],
                'result_count_pending_classification':row['result_count_pending_classification'],
                'status':'pending_separate_stock_contract_and_generator'})
            continue
        require(row['harness_contract']=='legacy_standalone','unknown harness cannot be omitted')
        legacy.append(inspect_legacy(row,git))
    return {'schema_version':1,'kind':'legacy_delta_workspace_build_preparation',
        'provenance':provenance,'authority_status':'none_build_preparation_is_not_classification_or_activation',
        'qualification_status':'unqualified','total_requirement_count':len(rows),
        'total_result_count_pending_classification':sum(r['result_count_pending_classification']for r in rows),
        'legacy_requirement_count':len(legacy),'legacy_result_count_pending_classification':sum(r['result_count_pending_classification']for r in legacy),
        'stock_requirement_count_pending':len(stock),'stock_result_count_pending_classification':sum(r['result_count_pending_classification']for r in stock),
        'legacy_rows':legacy,'stock_pending':stock}


def export_builder(benchmark_repository, row, destination):
    """Create a fresh, owned original source checkout; no generator ran yet."""
    destination=Path(destination)
    require(not destination.exists() and not destination.is_symlink(),'destination already exists')
    row=checked_row(row)
    git=GitInputs(benchmark_repository,row['benchmark_commit'])
    verify_original_row(row,git)
    destination.mkdir(mode=0o700)
    source=destination/'source'
    run_git(benchmark_repository,'worktree','add','--detach',str(source.resolve()),git.commit)
    config=row['configuration_files']
    for name,pin in config.items():require(digest(read(source/name))==pin['sha256'])
    session={'schema_version':1,'kind':'owned_original_generator_preparation','row':row,
             'original_commit':git.commit,'original_tree':git.tree}
    write_new(destination/'session.json',session)
    return {'source_commit':git.commit,'source_tree':git.tree,'build_row_sha256':row['build_row_sha256'],
            'source_export_status':'exact_original_checkout','generator_executed':False,
            'qualification_status':'unqualified'}


def checked_row(row):
    fields=set('row_sha256 benchmark_repository benchmark_commit benchmark_tree toolchain original_visibility result_count_pending_classification selection configuration_files original_harness original_generator_sources generator_source_sha256 committed_workspace_count workspaces component_build_profile qualification_status generation_status runtime_source_route_and_capacity build_row_sha256'.split())
    require(isinstance(row,dict) and set(row)==fields)
    require(row['build_row_sha256']==identity('lean-eval-legacy-delta-build-row-v1',{k:v for k,v in row.items()if k!='build_row_sha256'}))
    require(row.get('qualification_status')=='unqualified'
            and row.get('generation_status')=='requires_original_generator_execution')
    return row


def verify_original_row(row,git):
    """Recompute source/module/revision pins, not just a caller-provided seal."""
    profile=row['component_build_profile']
    reconstructed={'benchmark_repository':row['benchmark_repository'],
        'benchmark_commit':row['benchmark_commit'],'benchmark_tree':row['benchmark_tree'],
        'harness_contract':'legacy_standalone','qualification_status':'unqualified',
        'lane':row['original_visibility'],'result_count_pending_classification':row['result_count_pending_classification'],
        'selected_configuration_path':None,'selected_configuration_file_sha256':{},'verification_receipt_bindings':[],
        'toolchain':row['toolchain'],'root_lean_toolchain_blob_sha256':row['configuration_files']['lean-toolchain']['sha256'],
        'root_lake_manifest_sha256':row['configuration_files']['lake-manifest.json']['sha256'],
        'harness_blob_sha256':row['original_harness']['sha256'],'manifest_layout':'per_problem_v1',
        'problem_statement_revisions':{w['problem_id']:w['statement_revision']for w in row['workspaces']},
        'workspace_count':row['committed_workspace_count'],'row_sha256':row['row_sha256'],
        'prospective_components':{k:profile[k]for k in ('components','go_toolchain','rust_toolchain','stock_lean_repository','stock_lean_commit','stock_lean_toolchain')}}
    reconstructed['prospective_components']['kind']='prospective_legacy_component_lock'
    require(inspect_legacy(reconstructed,git)==row,'build row differs from recomputed original Git inputs')


def file_manifest(root):
    require(root.is_dir() and not root.is_symlink(), 'generated workspace is not a regular directory')
    records=[]
    directory_count=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        directory_count+=1
        require(directory_count<=4096 and len(records)+len(files)<=4096,'generated workspace has too many entries')
        for name in dirs:
            path=Path(directory)/name
            require(not path.is_symlink() and name not in {'.git','.lake','build','.cache'},'generated workspace contains an unexpected tree')
        for name in files:
            path=Path(directory)/name;info=path.lstat()
            require(stat.S_ISREG(info.st_mode) and not info.st_mode&0o111,'generated workspace contains a nonregular/executable file')
            raw=read(path,MAX_BLOB)
            records.append({'path':path.relative_to(root).as_posix(),'mode':'100644','bytes':len(raw),'sha256':digest(raw)})
    require(records and len(records)<=4096)
    return sorted(records,key=lambda r:r['path'])


def invoke_generator(argv, source, environment, timeout_seconds):
    """Bound the original generator's whole process group, without a retry."""
    require(type(timeout_seconds) in {int,float} and 0 < timeout_seconds <= 3600)
    process=subprocess.Popen(argv,cwd=source,env=environment,stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
    try:
        return process.wait(timeout=timeout_seconds)
    except BaseException:
        try:
            os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise


def materialize(session_root,builder_raw,expected_builder_sha256,*,timeout_seconds=300):
    """Invoke the original built CLI; build observation is NOT historical authority.

    The future trusted image builder must actually build this CLI from the
    pinned original source/toolchain/dependencies before supplying its observed
    hashes. This helper does not turn an arbitrary receipt into qualification.
    """
    supplied=Path(session_root)
    require(not supplied.is_symlink())
    session_root=supplied.resolve(strict=True)
    require(session_root.stat().st_uid==os.geteuid() and not session_root.stat().st_mode&0o077)
    session=decode(read(session_root/'session.json'))
    require(set(session)=={'schema_version','kind','row','original_commit','original_tree'}
            and session['kind']=='owned_original_generator_preparation')
    row=checked_row(session['row']);source=session_root/'source'
    require(source.is_dir() and not source.is_symlink())
    require(session['original_commit']==row['benchmark_commit'] and session['original_tree']==row['benchmark_tree'])
    verify_original_row(row,GitInputs(source,row['benchmark_commit']))
    require(digest(builder_raw)==expected_builder_sha256 and DIGEST.fullmatch(expected_builder_sha256))
    build=decode(builder_raw)
    fields={'schema_version','kind','benchmark_commit','benchmark_tree','toolchain','stock_lean_commit',
            'generator_source_sha256','generator_binary_sha256','lean_binary_sha256','lake_binary_sha256','toolchain_prefix'}
    require(isinstance(build,dict) and set(build)==fields and build['schema_version']==1
            and build['kind']=='original_generator_build_observation')
    for name in ('benchmark_commit','benchmark_tree','toolchain','generator_source_sha256'):
        require(build[name]==row[name])
    require(build['stock_lean_commit']==row['component_build_profile']['stock_lean_commit'])
    require(run_git(source,'rev-parse','HEAD').decode().strip()==row['benchmark_commit'])
    require(run_git(source,'rev-parse','HEAD^{tree}').decode().strip()==row['benchmark_tree'])
    require(not run_git(source,'diff','--name-only','HEAD','--'), 'builder checkout changed original tracked inputs')
    cli=source/'.lake/build/bin/lean-eval'
    prefix=Path(build['toolchain_prefix'])
    require(prefix.is_absolute() and prefix.resolve(strict=True)==prefix)
    for path,sha in ((cli,build['generator_binary_sha256']),
                     (prefix/'bin/lean',build['lean_binary_sha256']),(prefix/'bin/lake',build['lake_binary_sha256'])):
        require(isinstance(sha,str) and DIGEST.fullmatch(sha)
                and path.is_file() and not path.is_symlink() and os.access(path,os.X_OK)
                and digest(read(path,512*1024*1024))==sha,'original builder executable differs from observation')
    target=session_root/'workspaces'
    require(not target.exists() and not target.is_symlink()
            and not (session_root/'generation-claim.json').exists(), 'generation already claimed/materialized')
    home=session_root/'builder-home';home.mkdir(mode=0o700)
    # No inherited provider/GitHub credentials, proxy or alternate toolchain.
    environment={'PATH':str(prefix/'bin')+':/usr/bin:/bin','HOME':str(home),
                 'ELAN_TOOLCHAIN':row['toolchain'],'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
    version=subprocess.run([str(prefix/'bin/lean'),'--version'],cwd=source,env=environment,
                           capture_output=True,check=True,timeout=30).stdout.decode()
    require(row['component_build_profile']['stock_lean_commit']in version,'builder compiler commit differs')
    # A local generation claim is not a replay event/capability. It makes this
    # owned public-source operation single-use even after a partial failure.
    write_new(session_root/'generation-claim.json',{'schema_version':1,
        'build_row_sha256':row['build_row_sha256'],'builder_observation_sha256':digest(builder_raw)})
    observed=[]
    for workspace in row['workspaces']:
        problem=workspace['problem_id']
        # Committed generated files are input evidence, never proof this CLI
        # produced fresh output. Remove only this validated owned workspace.
        previous=source/'generated'/problem
        require((source/'generated').is_dir() and not (source/'generated').is_symlink())
        file_manifest(previous)
        shutil.rmtree(previous)
        returncode=invoke_generator([str(cli),'generate','--problem',problem],source,environment,timeout_seconds)
        require(returncode==0,'original generator failed; no derived-workspace proof')
        observed.append({'problem_id':problem,'statement_revision':workspace['statement_revision'],
                         'argv':['generate','--problem',problem],'exit_code':0})
    changed=run_git(source,'diff','--name-only','HEAD','--').decode().splitlines()
    require(all(p.startswith('generated/')for p in changed),'generator changed original source/config/statement inputs')
    from evaluate_submission import EvaluateError, _install_root_manifest
    target.mkdir(mode=0o700)
    derived=[]
    for workspace in row['workspaces']:
        problem=workspace['problem_id'];original=source/'generated'/problem
        require((source/'generated').is_dir() and not (source/'generated').is_symlink())
        before=file_manifest(original)
        require({'Challenge.lean','Solution.lean','Submission.lean','WorkspaceTest.lean','config.json','lakefile.toml','lean-toolchain'} <= {p['path']for p in before})
        require(read(original/'WorkspaceTest.lean')==read(source/'templates/WorkspaceTest.lean'))
        require(read(original/'lean-toolchain')==read(source/'lean-toolchain'))
        destination=target/problem
        shutil.copytree(original,destination,symlinks=False)
        # Same trusted manifest/toolchain/require checks as the active evaluator;
        # no cache priming, source overlay, build, checker or measurement mutation.
        try:
            _install_root_manifest(destination,source/'lake-manifest.json')
        except EvaluateError as error:
            raise BuildError('generated workspace does not match original root manifest/toolchain') from error
        files=file_manifest(destination)
        derived.append({'problem_id':problem,'statement_revision':workspace['statement_revision'],
            'original_generated_file_manifest_sha256':identity('lean-eval-original-generated-workspace-v1',before),
            'derived_files':files,'derived_workspace_sha256':identity('lean-eval-derived-workspace-v1',files)})
    proof={'schema_version':1,'kind':'original_legacy_workspace_generation_observation',
        'build_row_sha256':row['build_row_sha256'],'original_benchmark_commit':row['benchmark_commit'],
        'original_benchmark_tree':row['benchmark_tree'],'builder_observation_sha256':digest(builder_raw),
        'original_generator_binary_sha256':build['generator_binary_sha256'],
        'generator_invocations':observed,'derived_workspaces':derived,
        'qualification_status':'unqualified','authority_status':'none_generated_workspaces_are_not_replay_authority',
        'actual_original_cli_build_provenance':'must_be_verified_by_the_future_trusted_builder_not_asserted_by_this_receipt'}
    write_new(session_root/'generated-workspace-proof.json',proof)
    return proof


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    matrix=commands.add_parser('matrix');matrix.add_argument('--requirements',type=Path,required=True)
    matrix.add_argument('--expected-requirements-sha256',required=True);matrix.add_argument('--benchmark-repository',type=Path,required=True)
    matrix.add_argument('--output',type=Path,required=True)
    export=commands.add_parser('export');export.add_argument('--matrix',type=Path,required=True)
    export.add_argument('--expected-matrix-sha256',required=True);export.add_argument('--row-sha256',required=True)
    export.add_argument('--benchmark-repository',type=Path,required=True);export.add_argument('--destination',type=Path,required=True)
    generate=commands.add_parser('generate');generate.add_argument('--session',type=Path,required=True)
    generate.add_argument('--builder-observation',type=Path,required=True);generate.add_argument('--expected-builder-sha256',required=True)
    args=parser.parse_args()
    try:
        if args.command=='matrix':
            rows,source=load_requirements(args.requirements,args.expected_requirements_sha256)
            value=build_matrix(rows,source,args.benchmark_repository);write_new(args.output,value)
            print('Legacy build preparation written; no qualification or activation.')
        elif args.command=='export':
            raw=read(args.matrix);require(digest(raw)==args.expected_matrix_sha256)
            value=decode(raw);require(value['kind']=='legacy_delta_workspace_build_preparation'
                and value['authority_status']=='none_build_preparation_is_not_classification_or_activation')
            rows=[checked_row(r)for r in value['legacy_rows']if r['row_sha256']==args.row_sha256];require(len(rows)==1)
            export_builder(args.benchmark_repository,rows[0],args.destination)
            print('Original public benchmark checkout exported; generator not executed.')
        else:
            materialize(args.session,read(args.builder_observation),args.expected_builder_sha256)
            print('Original generator observed and derived workspaces hashed; still unqualified.')
    except (BuildError,OSError,subprocess.SubprocessError,ValueError,KeyError,TypeError):
        parser.exit(1,'Legacy build preparation stopped; no authority or successful generation is inferred.\n')


if __name__=='__main__':
    main()
