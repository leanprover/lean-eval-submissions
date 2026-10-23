#!/usr/bin/env python3
"""Decide one larger-runner retry using authenticated submitter identity.

Run only on a trusted runner after the standard evaluation job has finished.
An unexpected death is a reason to try more capacity, not proof of an OOM.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import sys
import urllib.error
import urllib.request

from run_with_resource_report import RESOURCE_ERROR

LOGIN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?')
ALLOWLIST_MESSAGE = (
    'Your submission appears to need a larger runner. Larger-runner retries '
    'are available to allowlisted submitters. Please ask Kim Morrison '
    '(@kim-em) to add your GitHub username to the larger-runner allowlist, '
    'then resubmit. This is not a rejection of your proof.'
)


class LogRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub job logs redirect to signed storage; do not forward the token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header('Authorization')
        return redirected


def fetch_log(job_id: int, target: pathlib.Path) -> None:
    # gh run view --log refuses logs from in-progress workflows, even when
    # this particular job has finished. Read the job endpoint directly and
    # keep ANSI bytes out of the terminal/GITHUB_OUTPUT channel.
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    token = os.environ.get('GH_TOKEN', '')
    if not repository or not token:
        return
    request = urllib.request.Request(
        f'https://api.github.com/repos/{repository}/actions/jobs/{job_id}/logs',
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
    )
    try:
        with urllib.request.build_opener(LogRedirect()).open(request, timeout=30) as response:
            with target.open('wb') as stream:
                shutil.copyfileobj(response, stream)
    except (OSError, urllib.error.URLError):
        print('Job log is not available; using step status and the resource report.', file=sys.stderr)


def allowlisted(login: str, entries: object) -> bool:
    if not isinstance(entries, list) or any(
        not isinstance(entry, str) or LOGIN.fullmatch(entry) is None for entry in entries
    ):
        raise ValueError('allowlist must be an array of GitHub usernames')
    if LOGIN.fullmatch(login) is None:
        raise ValueError('submitter must be an authenticated GitHub username')
    return login.casefold() in {entry.casefold() for entry in entries}


def resource_failure(job: dict, log: str, report: dict | None) -> str | None:
    # Ordinary bad proofs are successful jobs. Only explicit resource evidence
    # can make those retryable; a negative verdict alone never does.
    if report and report.get('resource_reason'):
        return str(report['resource_reason'])
    evaluation = next((step for step in job.get('steps', [])
                       if step.get('name') == 'Run evaluate_submission.py'), None)
    if not evaluation or evaluation.get('conclusion') in {'skipped', None}:
        return None
    # Explicit user cancellation is excluded by the caller's !cancelled()
    # gate. A cancelled evaluation step on a lost runner is retryable even
    # when that machine could not upload logs or a resource report.
    if job.get('conclusion') == 'cancelled' and evaluation.get('conclusion') == 'cancelled':
        return 'runner_interrupted'
    if job.get('conclusion') == 'timed_out' or evaluation.get('conclusion') == 'timed_out' or re.search(
        r'##\[error\].*(?:exceeded the maximum execution time|timed out)', log, re.I
    ):
        return 'evaluation_timeout'
    if RESOURCE_ERROR.search(log) or 'The runner has received a shutdown signal' in log:
        return 'possible_resource_failure'
    return None


def decide(login: str, entries: object, job: dict, log: str,
           report: dict | None = None) -> dict:
    allowed = allowlisted(login, entries)
    reason = resource_failure(job, log, report)
    blocked = reason is not None and not allowed
    return {
        'retry': reason is not None and allowed,
        'resource_reason': reason,
        'allowlist_required': blocked,
        'message': ALLOWLIST_MESSAGE if blocked else (
            'Retrying once on a larger runner (32 CPUs / 64 GB RAM).'
            if reason else ''
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submitter', required=True)
    parser.add_argument('--allowlist', type=pathlib.Path, required=True)
    parser.add_argument('--jobs', type=pathlib.Path, required=True)
    parser.add_argument('--log', type=pathlib.Path, required=True)
    parser.add_argument('--report', type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    jobs = json.loads(args.jobs.read_text())['jobs']
    job = next((job for job in jobs if job['name'].startswith('evaluate_standard /')), {})
    if job.get('id') and (not args.log.is_file() or args.log.stat().st_size == 0):
        fetch_log(job['id'], args.log)
    report = (json.loads(args.report.read_text())
              if args.report and args.report.is_file() else None)
    result = decide(args.submitter, json.loads(args.allowlist.read_text()), job,
                    args.log.read_text(errors='replace') if args.log.is_file() else '', report)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('retry=' + str(result['retry']).lower())
    print('allowlist_required=' + str(result['allowlist_required']).lower())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
