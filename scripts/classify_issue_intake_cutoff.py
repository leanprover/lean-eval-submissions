#!/usr/bin/env python3
"""Classify one issue-workflow run against the selected intake cutoff."""

from __future__ import annotations

import argparse
import datetime as dt
import re


TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


class CutoffError(ValueError):
    """The cutoff or workflow-run timestamp is not canonical."""


def timestamp(value: str, label: str) -> dt.datetime:
    if TIMESTAMP.fullmatch(value) is None:
        raise CutoffError(f"{label} must be a canonical UTC second")
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise CutoffError(f"{label} must be a real UTC timestamp") from error
    return parsed.replace(tzinfo=dt.timezone.utc)


def classify(run_created_at: str, cutoff: str) -> tuple[bool, str]:
    created = timestamp(run_created_at, "workflow run creation time")
    selected = timestamp(cutoff, "issue-intake cutoff")
    if created < selected:
        return True, "before_cutoff"
    return False, "at_or_after_cutoff"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-created-at", required=True)
    parser.add_argument("--cutoff", required=True)
    args = parser.parse_args()
    try:
        allowed, reason = classify(args.run_created_at, args.cutoff)
    except CutoffError as error:
        parser.error(str(error))
    print(f"allowed={str(allowed).lower()}")
    print(f"reason={reason}")


if __name__ == "__main__":
    main()
