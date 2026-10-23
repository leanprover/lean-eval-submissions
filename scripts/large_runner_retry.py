#!/usr/bin/env python3
"""Decide one larger-runner retry using authenticated submitter identity.

Run on a trusted runner after standard evaluation. The resource report and
GitHub step status suffice; no submitted log text is parsed by this controller.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

LOGIN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?')
ALLOWLIST_MESSAGE = (
    'Your submission appears to need a larger runner. Larger-runner retries '
    'are available to allowlisted submitters. Please ask Kim Morrison '
    '(@kim-em) to add your GitHub username to the larger-runner allowlist, '
    'then resubmit. This is not a rejection of your proof.'
)


def allowlisted(login: str, entries: object) -> bool:
    if not isinstance(entries, list) or any(
        not isinstance(entry, str) or LOGIN.fullmatch(entry) is None for entry in entries
    ):
        raise ValueError('allowlist must be an array of GitHub usernames')
    return login.casefold() in {entry.casefold() for entry in entries}


def resource_failure(job: dict, report: dict | None) -> str | None:
    if report and report.get('resource_reason'):
        return str(report['resource_reason'])
    evaluation = next((step for step in job.get('steps', [])
                       if step.get('name') == 'Run evaluate_submission.py'), {})
    status = evaluation.get('conclusion')
    if status in {'cancelled', 'timed_out'}:
        # The workflow's !cancelled() gate excludes user cancellation.
        return 'runner_interrupted'
    if status == 'failure' and job.get('conclusion') == 'timed_out':
        return 'evaluation_timeout'
    return None


def decide(login: str, entries: object, job: dict, report: dict | None = None) -> dict:
    reason = resource_failure(job, report)
    allowed = reason is not None and allowlisted(login, entries)
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
    parser.add_argument('--report', type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    jobs = json.loads(args.jobs.read_text())['jobs']
    job = next((job for job in jobs if job['name'].startswith('evaluate_standard /')), {})
    report = (json.loads(args.report.read_text())
              if args.report and args.report.is_file() else None)
    entries = json.loads(args.allowlist.read_text()) if resource_failure(job, report) else []
    result = decide(args.submitter, entries, job, report)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('retry=' + str(result['retry']).lower())
    print('allowlist_required=' + str(result['allowlist_required']).lower())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
