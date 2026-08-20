#!/usr/bin/env python3
"""Explain *why* the `evaluate` job failed, for the comment `notify` posts.

`notify` used to tell every submitter that "the most common cause is that
`Submission.lean` failed to compile".  That hint is close to never right.  A
submission whose proof does not compile is not an error at all as far as this
pipeline is concerned: `run-eval` reports the problem as `succeeded: false`,
`evaluate_submission.py` exits 0, and the submitter gets the ordinary
per-problem result comment saying `fail`.  For the `evaluate` *job* to fail,
something else has to have gone wrong -- a pre-flight step, the harness
itself, or the runner dying underneath the build -- and none of those are
addressed by rereading one's own Lean.

Classification uses two inputs the workflow can fetch with `gh`:

  * the `evaluate` job's step list, which says which step failed, and
  * that job's log, which is the only place a death-by-signal is recorded.

A runner killed for exhausting memory is the case worth naming explicitly.
The VM disappears mid-step, so nothing inside the job can record it; all that
survives is Actions' own "received a shutdown signal" line and exit code 143
(SIGTERM) or 137 (SIGKILL).  Reported as a compile error, as it was three
times on issue #1077, it sends the submitter to look at proofs that were
building perfectly well.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


EVALUATE_STEP = "Run evaluate_submission.py"

# Actions writes both of these when the runner goes away underneath a step.
# 143 is SIGTERM (the usual shape of a hosted-runner memory kill), 137 is
# SIGKILL (a direct OOM-killer hit on the process).
SIGNAL_DEATH_RE = re.compile(
    r"received a shutdown signal|Process completed with exit code (?:143|137)\b"
)

RUNBOOK = (
    "This is not something to fix in your submission. An operator will need "
    "to look at it."
)


def _failed_step(steps: list[dict]) -> str | None:
    """Name of the first step that failed, or None if every step passed.

    `cancelled` counts: when a job is killed part-way, the step that was
    running is marked cancelled rather than failed.
    """
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("conclusion") in ("failure", "cancelled"):
            name = step.get("name")
            return name if isinstance(name, str) else None
    return None


def classify(
    steps: list[dict],
    log: str | None,
    *,
    job_conclusion: str | None = None,
) -> tuple[str, str, bool]:
    """Return `(category, message, blames_submission)` for a failed evaluate job.

    `blames_submission` is False whenever the failure is ours rather than the
    submitter's, which is the signal `notify` uses to decide whether closing
    their issue is honest.
    """
    if log is not None and SIGNAL_DEATH_RE.search(log):
        return (
            "runner-killed",
            "The runner was killed part-way through the build (exit 143/137, "
            "no Lean error). That is the signature of the job exhausting the "
            "runner's memory: the machine goes away before anything can "
            "record why, so the build simply stops mid-module. Your proof did "
            "not fail to compile. " + RUNBOOK,
            False,
        )

    failed = _failed_step(steps)

    if failed is None:
        return (
            "unknown",
            "The evaluation did not produce results, and no individual step "
            "reported the failure"
            + (f" (job: {job_conclusion})" if job_conclusion else "")
            + ". " + RUNBOOK,
            False,
        )

    if failed != EVALUATE_STEP:
        return (
            "preflight",
            f"The run stopped at the `{failed}` step, before the submission "
            "was evaluated. This is an infrastructure failure and has nothing "
            "to do with your proof. " + RUNBOOK,
            False,
        )

    return (
        "harness",
        "The evaluation harness reported an error rather than a result. This "
        "is usually a problem with how the submission overlays the benchmark "
        "workspace (a file that collides with the harness, an unexpected "
        "layout) rather than with the mathematics, and it can also be a bug "
        "on our side. The workflow logs have the harness's own error message.",
        True,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jobs",
        type=pathlib.Path,
        required=True,
        help="JSON from `gh api .../actions/runs/<id>/jobs`",
    )
    parser.add_argument(
        "--log",
        type=pathlib.Path,
        help="the evaluate job's log; omit if it could not be fetched",
    )
    parser.add_argument("--job-name", default="evaluate")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    payload = json.loads(args.jobs.read_text())
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    job = next(
        (
            j
            for j in (jobs or [])
            if isinstance(j, dict) and j.get("name") == args.job_name
        ),
        None,
    )
    steps = job.get("steps") or [] if job else []
    conclusion = job.get("conclusion") if job else None

    log = None
    # A missing or unreadable log is expected rather than exceptional: the
    # API serves logs from blob storage that is not always populated the
    # instant a job ends. Fall through to step-based classification.
    if args.log is not None and args.log.exists():
        try:
            log = args.log.read_text(errors="replace")
        except OSError:
            log = None

    category, message, blames = classify(steps, log, job_conclusion=conclusion)
    print(f"category={category}")
    print(f"blames_submission={'true' if blames else 'false'}")
    print(f"message<<CLASSIFY_EOF\n{message}\nCLASSIFY_EOF")
    return 0


if __name__ == "__main__":
    sys.exit(main())
