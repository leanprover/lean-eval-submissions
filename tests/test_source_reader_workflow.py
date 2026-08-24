import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "verify-source-reader.yml"


class SourceReaderWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_is_protected_staging_only_and_fixed_to_fixture(self) -> None:
        verify_job = self.text.split("  verify:", 1)[1]
        self.assertRegex(
            verify_job,
            re.compile(r"^    environment: cloudflare-staging$", re.MULTILINE),
        )
        self.assertNotIn("cloudflare-production", self.text)
        self.assertEqual(self.text.count("kim-em/lean-eval-intake-fixture"), 2)
        self.assertIn("secrets.READINESS_TOKEN", self.text)

    def test_requires_private_exact_response(self) -> None:
        self.assertIn('"status": "source_reader_ready"', self.text)
        self.assertIn('"private": True', self.text)
        self.assertIn("curl --fail-with-body", self.text)


if __name__ == "__main__":
    unittest.main()
