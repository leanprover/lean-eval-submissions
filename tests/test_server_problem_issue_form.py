"""Structural guard for the public server-problem Issue Form."""

from __future__ import annotations

import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ISSUE_FORM = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "server-problem.yml"


class ServerProblemIssueFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = ISSUE_FORM.read_text(encoding="utf-8")

    def test_form_is_public_bug_intake_with_required_safe_report_fields(self) -> None:
        self.assertIn("title: \"[server problem] \"", self.text)
        self.assertIn("  - bug", self.text)
        for field in ("surface", "description", "expected", "safety"):
            block = self.text.split(f"    id: {field}\n", 1)[1]
            self.assertIn("required: true", block.split("\n  - type:", 1)[0])

    def test_form_warns_against_public_disclosure_and_links_stable_entry(self) -> None:
        for prohibited_material in (
            "credentials",
            "OAuth tokens",
            "cookies",
            "private source",
            "unpublished proofs",
            "archive contents",
            "authenticated response bodies",
        ):
            self.assertIn(prohibited_material, self.text)
        self.assertIn("https://lean-lang.org/eval/submit/", self.text)


if __name__ == "__main__":
    unittest.main()
