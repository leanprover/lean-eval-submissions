#!/usr/bin/env python3
"""Closed source-free receipt for an unqualified, nonpublishing image build."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys

import stock_image_208 as stock

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 20 * 1024**3
RELEASE_URL = 'https://github.com/leanprover/lean4/releases/download/v4.35.0-rc3/lean-4.35.0-rc3-linux.tar.zst'
RELEASE_BYTES = 595611952
FLAGS = ('generation_observed', 'image_qualified', 'runtime_qualified',
         'request3_admitted', 'private_Source_supported')
KEYS = {'schema_version', 'kind', 'benchmark_commit', 'benchmark_tree', 'toolchain',
        'release_sha256', 'tool_sha256', 'dependencies', 'module_count',
        'ordered_module_plan_sha256', 'instrumentation_sha256', 'bubblewrap_sha256',
        'remaining', *FLAGS}


def require(ok):
    if not ok:
        raise ValueError('stock208 build evidence is unproved')


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hex_value(value, length=64):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(length) + '}', value)


def evidence(raw, source_commit, image_id, image_size, root=ROOT):
    require(type(raw) is bytes and len(raw) <= 65536)
    check = json.loads(raw, object_pairs_hook=pairs)
    require(type(check) is dict and set(check) == KEYS)
    require(type(check['schema_version']) is int and check['schema_version'] == 1
            and check['kind'] == 'inactive_stock208_installation_check')
    expected = {'benchmark_commit': stock.BENCHMARK, 'benchmark_tree': stock.BENCHMARK_TREE,
                'toolchain': stock.TOOLCHAIN, 'release_sha256': stock.RELEASE_SHA256,
                'tool_sha256': stock.TOOLS, 'module_count': 296,
                'ordered_module_plan_sha256': stock.MODULE_PLAN}
    require(all(check[key] == value for key, value in expected.items()))
    require(type(check['module_count']) is int and all(check[key] is False for key in FLAGS))
    dependencies = check['dependencies']
    require(type(dependencies) is dict and len(dependencies) == 12)
    for name, pin in dependencies.items():
        require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name)
                and type(pin) is dict and set(pin) == {'commit', 'tree'}
                and all(hex_value(value, 40) for value in pin.values()))
    instrumentation = check['instrumentation_sha256']
    names = {*stock.WRAPPERS, *(name + '.py' for name in stock.MODULES)}
    require(type(instrumentation) is dict and set(instrumentation) == names
            and all(hex_value(value) for value in instrumentation.values())
            and hex_value(check['bubblewrap_sha256']))
    # These free-form explanatory strings are deliberately not copied to output.
    require(type(check['remaining']) is list and len(check['remaining']) == 5
            and all(isinstance(value, str) and len(value) <= 256 for value in check['remaining']))
    require(hex_value(source_commit, 40) and isinstance(image_id, str)
            and re.fullmatch(r'sha256:[0-9a-f]{64}', image_id)
            and type(image_size) is int and 0 < image_size <= LIMIT)
    result = {'schema_version': 1, 'kind': 'nonpublishing_stock208_image_build',
              'source_commit': source_commit, 'image_id': image_id,
              'image_size_bytes': image_size, 'image_limit_bytes': LIMIT,
              'recipe_sha256': sha(root / 'Dockerfile.stock-replay-208'),
              'dockerignore_sha256': sha(root / 'Dockerfile.stock-replay-208.dockerignore'),
              'installed_helper_sha256': sha(root / 'scripts/stock_image_208.py'),
              'evidence_helper_sha256': sha(root / 'scripts/build_stock_image_208_evidence.py'),
              'workflow_sha256': sha(root / '.github/workflows/stock-replay-image-208.yml'),
              'release_url': RELEASE_URL, 'release_bytes': RELEASE_BYTES,
              **expected, 'installation_check_passed': True,
              'installation_report_sha256': hashlib.sha256(raw).hexdigest(),
              'dependencies_sha256': hashlib.sha256(canonical(dependencies)).hexdigest(),
              'instrumentation_sha256': instrumentation,
              'bubblewrap_sha256': check['bubblewrap_sha256'],
              **{key: False for key in FLAGS},
              'published': False, 'provider_executed': False, 'State_written': False,
              'Source_fetched': False, 'Task_admitted': False}
    require(len(canonical(result)) <= 16384)
    return result


def main():
    path = ROOT / 'stock208-installation.private.json'
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 65536)
    with path.open('rb') as installed:
        raw = installed.read(65537)
    result = evidence(raw, os.environ['SOURCE_COMMIT'],
                      os.environ['IMAGE_ID'], int(os.environ['IMAGE_SIZE_BYTES']))
    raw_result = json.dumps(result, sort_keys=True, indent=2) + '\n'
    require(len(raw_result.encode()) <= 16384)
    with (ROOT / 'stock208-image-evidence.json').open('x', encoding='utf-8') as output:
        output.write(raw_result)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('stock208 build evidence unavailable', file=sys.stderr)
        raise SystemExit(1) from None
