#!/usr/bin/env python3
"""Validate a newly opened API submission before CI labels and evaluates it.

GitHub applies labels declared by an Issue Form when the form is submitted in
the web UI, but silently drops labels requested by issue authors without
triage permission when they create the same issue through the API.  This
validator recognizes the rendered Issue Form shape so the workflow can safely
restore the routing label for API-created submissions.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import fetch_submission


TITLE_RE = re.compile(r"^\[submission\](?:\s|$)")
ACKNOWLEDGEMENTS_HEADING = "Acknowledgements"
CHECKBOX_RE = re.compile(r"^-\s+\[([ xX])\]\s+", re.MULTILINE)
EXPECTED_ACKNOWLEDGEMENTS = 3
ACKNOWLEDGEMENT_PHRASES = (
    "lean-eval CI will fetch my submission URL",
    "only the set of solved problem IDs",
    "encrypted copy of the submission source",
)


class IntakeError(Exception):
    """Raised when an opened issue is not a current, complete submission."""


def validate_submission_issue(title: str, body: str) -> dict[str, str | None]:
    """Validate the fields the web Issue Form requires for a new submission."""
    if not TITLE_RE.match(title):
        raise IntakeError("title must start with `[submission] `")

    fields = fetch_submission.parse_issue_body(body)
    source_url = fields["source_url"]
    if not isinstance(source_url, str):
        raise IntakeError("`Submission URL` must be a string")
    fetch_submission.parse_source_url(source_url)

    # fetch_submission accepts missing publication fields so historical issue
    # events remain replayable. Intake only handles newly opened issues, which
    # must follow the current form and provide the required declaration.
    if fields["solution_publication_status"] is None:
        raise IntakeError("`Exact solution publication status` is required")

    acknowledgements = fetch_submission.find_issue_section(
        body, ACKNOWLEDGEMENTS_HEADING
    )
    if acknowledgements is None:
        raise IntakeError("could not find `Acknowledgements` section")
    checkboxes = CHECKBOX_RE.findall(acknowledgements)
    if len(checkboxes) != EXPECTED_ACKNOWLEDGEMENTS or any(
        value.lower() != "x" for value in checkboxes
    ):
        raise IntakeError(
            f"all {EXPECTED_ACKNOWLEDGEMENTS} acknowledgements must be checked"
        )
    normalized_acknowledgements = " ".join(acknowledgements.split())
    if any(
        phrase not in normalized_acknowledgements
        for phrase in ACKNOWLEDGEMENT_PHRASES
    ):
        raise IntakeError("acknowledgements must match the current submission form")

    return fields


def validate_event(event: object) -> dict[str, str | None]:
    if not isinstance(event, dict) or event.get("action") != "opened":
        raise IntakeError("expected an `issues: opened` event")
    issue = event.get("issue")
    if not isinstance(issue, dict):
        raise IntakeError("event is missing the issue object")
    title = issue.get("title")
    body = issue.get("body")
    if not isinstance(title, str) or not isinstance(body, str):
        raise IntakeError("issue title and body must be strings")
    return validate_submission_issue(title, body)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-path", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        event = json.loads(args.event_path.read_text(encoding="utf-8"))
        fields = validate_event(event)
    except (OSError, json.JSONDecodeError, IntakeError, fetch_submission.FetchError) as exc:
        print(f"submission intake rejected: {exc}", file=sys.stderr)
        return 1
    print(f"validated API submission for model {fields['model']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
