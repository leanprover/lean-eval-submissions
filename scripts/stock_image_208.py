#!/usr/bin/env python3
"""Finite inactive stock208 image installation; no request/Source admission.

The Dockerfile alone obtains the hash-pinned original release. This helper
retains immutable public Git inputs and checks their installation, not a Task,
profile, generated workspace, provider or stock execution qualification.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

INSTALL = Path('/opt/lean-eval')
GIT_ROOT = INSTALL / 'git'
PREFIX = INSTALL / 'toolchain'
BENCHMARK = '208b6b22d396f28771dba56414b19f47cae5f2a7'
BENCHMARK_TREE = '47b79175d09dd3d0c195d5f363a078df60e3cd6e'
LEAN = '470d5ce1400764999581fd26d5d72b00d990b0f4'
LEAN_TREE = '062f40e3b5e68d7dfda6c43ea26784e0e7850f43'
TOOLCHAIN = 'leanprover/lean4:v4.35.0-rc3'
RELEASE_SHA256 = '1526de16c0496f16b46a168f26c990af4e3796505273c3342fec17d72d763997'
MODULE_PLAN = '7e84c623aa7ebab350a750882ceb620b110a5edda93761de9e673d6388a09202'
TOOLS = {
    'lean': 'bf8d54e4714cc4b03d3f6bb34c83b7202b87e49c8bfcbff6895c085bb90ceb38',
    'lake': '8ba83c98f76562d03b8dbc33f1057151ba6cbb121679dde76b2260ccc90495e1',
    'leanexport': 'c5bc1a10a21e22cbb3671d65987cdef2076833b14182d823ccb2153adf2fa954',
    'leanchecker': '9e36e955a251b1313c8eb5b7fa101282d51df109d34d8bb7c1cfdf51985aba29',
    'nanoda_bin': '8241c5e6baa29490aa118d382f97961b208ae730fdb96545c2abed74c9ece5a8',
}
MODULES = ('stock_replay_contract', 'stock_replay_measurement', 'stock_replay_supervisor',
           'prepare_stock_workspace', 'replay_orchestrator', 'results_schema', 'evaluate_submission')
WRAPPERS = ('replay-measure', 'replay-measure-stock', 'replay-supervise-stock', 'stock-supervisor-agent')


class Invalid(ValueError):
    """Fixed installation refusal, without subprocess output or credentials."""


def require(value):
    if not value:
        raise Invalid('stock208 image installation is unproved')


def libraries():
    # A fixed trusted installation path is needed by the unchanged flat-module
    # wrappers and contract schema lookup. No caller/PYTHONPATH path is added.
    require(Path(__file__).resolve() == INSTALL / 'stock_image_208.py')
    sys.path.insert(0, str(INSTALL))
    import prepare_stock_workspace as preparer
    return preparer


def fetch(preparer, repository, commit, destination):
    """Build-only public acquisition. Operands derive solely from bound Git."""
    require(repository.startswith('https://github.com/') and '@' not in repository)
    require(not destination.exists() and not destination.is_symlink())
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'https',
           'GIT_NO_REPLACE_OBJECTS': '1'}
    common = ['/usr/bin/git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
              '-c', 'core.fsmonitor=false', '-c', 'credential.helper=']
    for args in (['init', '--bare', str(destination)],
                 ['-C', str(destination), 'fetch', '--depth', '1', '--no-tags', '--', repository, commit],
                 ['-C', str(destination), 'update-ref', 'refs/heads/pinned', commit]):
        preparer.invoke([*common, *args], None, env, 300)


def inputs(preparer):
    benchmark, _, configuration, _ = preparer.selection(GIT_ROOT / 'benchmark.git', BENCHMARK, TOOLCHAIN, 'two_plus_two')
    require(benchmark.tree == BENCHMARK_TREE)
    manifest = json.loads(configuration['lake-manifest.json'])
    require(len(manifest['packages']) == 12)
    return benchmark, manifest


def fetch_public_git():
    preparer = libraries()
    preparer._require_dedicated()
    GIT_ROOT.mkdir(mode=0o755)
    fetch(preparer, 'https://github.com/leanprover/lean-eval.git', BENCHMARK, GIT_ROOT / 'benchmark.git')
    _, manifest = inputs(preparer)
    dependencies = GIT_ROOT / 'dependencies'
    dependencies.mkdir()
    for package in manifest['packages']:
        # Do not accept caller-supplied mirrors, pins, subdirectories or URLs.
        require(package['type'] == 'git' and package.get('subDir') is None)
        name = package['name'].strip('«»')
        require(name and '/' not in name and name not in ('.', '..'))
        fetch(preparer, package['url'], package['rev'], dependencies / (name + '.git'))
        preparer.Git(dependencies / (name + '.git'), package['rev'])
    fetch(preparer, 'https://github.com/leanprover/lean4.git', LEAN, GIT_ROOT / 'lean.git')
    preparer.Git(GIT_ROOT / 'lean.git', LEAN, LEAN_TREE)


def installed(preparer):
    benchmark, manifest = inputs(preparer)
    modules = preparer._original_inventory_modules(benchmark)
    require(len(modules) == 296 and preparer.sha(preparer.supervisor.stock.canonical(modules)) == MODULE_PLAN)
    require(len([p for p in benchmark.entries if p.startswith('manifests/problems/')]) == 312)
    dependencies = {}
    for package in manifest['packages']:
        name = package['name'].strip('«»')
        pin = preparer.Git(GIT_ROOT / 'dependencies' / (name + '.git'), package['rev'])
        dependencies[name] = {'commit': pin.commit, 'tree': pin.tree}
    lean = preparer.Git(GIT_ROOT / 'lean.git', LEAN, LEAN_TREE)
    require(lean.blob('src/lake/Lake/CLI/Check.lean') == (PREFIX / 'src/lean/lake/Lake/CLI/Check.lean').read_bytes())
    check_tools(preparer)
    instrumentation = {}
    for name in (*WRAPPERS, *(name + '.py' for name in MODULES)):
        instrumentation[name] = preparer.supervisor.file_hash(INSTALL / name)['sha256']
    require(preparer.contracts.ROOT == INSTALL.parent)
    preparer.contracts._validators()  # closed schemas, offline registry only
    for name in WRAPPERS:
        require(os.access(INSTALL / name, os.X_OK))
    bwrap = preparer.supervisor.file_hash('/usr/bin/bwrap')['sha256']
    # Real original launcher execution verifies native loader resolution too;
    # it never elaborates Source or invokes lake test/comparator.
    version = preparer._capture_preparation([str(PREFIX / 'bin/lean'), '--version'], None,
                                           preparer.environment('/nonexistent', PREFIX), 30).decode()
    require(LEAN in version and '4.35.0-rc3' in version)
    return {'schema_version': 1, 'kind': 'inactive_stock208_installation_check',
            'benchmark_commit': BENCHMARK, 'benchmark_tree': BENCHMARK_TREE,
            'toolchain': TOOLCHAIN, 'release_sha256': RELEASE_SHA256, 'tool_sha256': TOOLS,
            'dependencies': dependencies, 'module_count': 296, 'ordered_module_plan_sha256': MODULE_PLAN,
            'instrumentation_sha256': instrumentation, 'bubblewrap_sha256': bwrap,
            'generation_observed': False, 'image_qualified': False, 'runtime_qualified': False,
            'request3_admitted': False, 'private_Source_supported': False,
            'remaining': ['fresh original all296 semantic validation and selected generation before Source',
                          'real image original driver/measurement/namespace/counters/resource qualification',
                          'per-problem C/P and actual authoritative Task/Source integration',
                          'provider destruction/job/scratch cleanup', 'other three configurations/all twelve Results']}


def check_tools(preparer):
    for name, expected in TOOLS.items():
        require(preparer.supervisor.file_hash(PREFIX / 'bin' / name)['sha256'] == expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('fetch-public-git', 'check-installed'))
    args = parser.parse_args()
    os.umask(0o022)
    if args.action == 'fetch-public-git':
        fetch_public_git()
    else:
        print(json.dumps(installed(libraries()), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('stock208 image installation unavailable', file=sys.stderr)
        raise SystemExit(1) from None
