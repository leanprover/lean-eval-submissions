#!/usr/bin/env python3
"""Run evaluation, streaming its output and detecting resource failures.

The report contains no submitted source. OOM counters cover descendants even
when Lake turns a killed compiler into an ordinary nonzero proof verdict.
Runner/job deaths are handled separately by the trusted retry-decision job.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

RESOURCE_FAILURE = 75
# Match diagnostics, not incidental mentions of memory in Lean source paths.
RESOURCE_ERROR = re.compile(
    r"out of memory|cannot allocate memory|std::bad_alloc|"
    r"(?:exited|exit(?:ed)? with(?: code)?|exit code)[: ]+(?:137|143)\b|"
    r"terminated by signal (?:9|15)\b|\bSIGKILL\b",
    re.IGNORECASE,
)


def oom_count() -> int:
    """Read the process's cgroup and the namespace root, if available."""
    paths = {pathlib.Path('/sys/fs/cgroup/memory.events')}
    try:
        for line in pathlib.Path('/proc/self/cgroup').read_text().splitlines():
            if line.startswith('0::'):
                paths.add(pathlib.Path('/sys/fs/cgroup') / line[3:].lstrip('/') / 'memory.events')
    except OSError:
        pass
    count = 0
    for path in paths:
        try:
            values = dict(line.split() for line in path.read_text().splitlines())
            count = max(count, int(values.get('oom_kill', '0')))
        except (OSError, ValueError):
            pass
    return count


def run(command: list[str], report: pathlib.Path, timeout_seconds: int | None = None) -> int:
    before = oom_count()
    resource_diagnostic = False
    if timeout_seconds is not None:
        # GNU timeout terminates the process group, including descendants that
        # might otherwise keep the output pipe open after the parent exits.
        command = ['timeout', '--kill-after=10s', str(max(1, timeout_seconds)), *command]
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert child.stdout is not None
    # Fixed-size reads avoid buffering a submission's arbitrarily long line.
    tail = b''
    while chunk := child.stdout.read1(4096):
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        sample = tail + chunk
        resource_diagnostic |= RESOURCE_ERROR.search(sample.decode(errors='replace')) is not None
        tail = sample[-256:]
    child.stdout.close()
    code = child.wait()
    killed = code in {-9, -15, 137, 143}
    oom = oom_count() > before
    timed_out = timeout_seconds is not None and code == 124
    reason = ('evaluation_timeout' if timed_out else 'out_of_memory' if oom
              else 'process_killed' if killed else 'resource_diagnostic' if resource_diagnostic else None)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({'exit_code': code, 'resource_reason': reason}) + '\n')
    return RESOURCE_FAILURE if reason else (code if code >= 0 else 128 - code)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=pathlib.Path, required=True)
    parser.add_argument('--deadline', type=int, help='evaluation deadline as Unix seconds')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a command is required')
    seconds = None if args.deadline is None else args.deadline - int(time.time())
    return run(command, args.report, seconds)


if __name__ == '__main__':
    raise SystemExit(main())
