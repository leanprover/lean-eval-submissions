from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_submission_intake as intake  # noqa: E402


VALID_BODY = """### Submission URL

https://github.com/alice/my-proofs/commit/8e1b9cf5e1d3c2b1a0f9e8d7c6b5a4938271605f

### Model

Claude Opus 4.6

### Exact solution publication status

Public

### Publication date (if public)

2026-08-06

### Intended publication date (if planned)

_No response_

### How this solution was produced (optional)

_No response_

### Acknowledgements

- [x] I understand that the lean-eval CI will fetch my submission URL and run comparator on it.
- [X] I understand that only the set of solved problem IDs will be published.
- [x] I understand that an encrypted copy of the submission source will be retained.
"""


class ValidateSubmissionIssueTests(unittest.TestCase):
    def test_accepts_current_issue_form_shape(self) -> None:
        fields = intake.validate_submission_issue(
            "[submission] prove the thing", VALID_BODY
        )
        self.assertEqual(fields["model"], "Claude Opus 4.6")
        self.assertEqual(fields["solution_publication_status"], "published")

    def test_rejects_non_submission_title(self) -> None:
        with self.assertRaisesRegex(intake.IntakeError, "title must start"):
            intake.validate_submission_issue("A normal issue", VALID_BODY)

    def test_rejects_missing_current_publication_fields(self) -> None:
        body = VALID_BODY.split("### Exact solution publication status", 1)[0]
        with self.assertRaisesRegex(intake.IntakeError, "publication status"):
            intake.validate_submission_issue("[submission] legacy body", body)

    def test_rejects_unchecked_acknowledgement(self) -> None:
        body = VALID_BODY.replace(
            "- [X] I understand that only the set of solved problem IDs will be published.",
            "- [ ] I understand that only the set of solved problem IDs will be published.",
        )
        with self.assertRaisesRegex(intake.IntakeError, "all 3 acknowledgements"):
            intake.validate_submission_issue("[submission] unchecked", body)

    def test_rejects_invented_acknowledgement_text(self) -> None:
        body = VALID_BODY.replace(
            "only the set of solved problem IDs", "unrelated metadata"
        )
        with self.assertRaisesRegex(intake.IntakeError, "current submission form"):
            intake.validate_submission_issue("[submission] invented", body)

    def test_rejects_unsupported_source_url(self) -> None:
        body = VALID_BODY.replace("https://github.com/", "https://example.com/")
        with self.assertRaisesRegex(Exception, "Unsupported host"):
            intake.validate_submission_issue("[submission] bad URL", body)

    def test_validates_opened_event_from_file(self) -> None:
        event = {
            "action": "opened",
            "issue": {"title": "[submission] API", "body": VALID_BODY},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "event.json"
            path.write_text(json.dumps(event), encoding="utf-8")
            self.assertEqual(intake.main(["--event-path", str(path)]), 0)

    def test_rejects_non_opened_event(self) -> None:
        with self.assertRaisesRegex(intake.IntakeError, "issues: opened"):
            intake.validate_event(
                {"action": "labeled", "issue": {"title": "x", "body": "y"}}
            )


if __name__ == "__main__":
    unittest.main()
