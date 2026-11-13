from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import os
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_benchmark_toolchain",
                                            ROOT / "scripts/prepare_benchmark_toolchain.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PrepareBenchmarkToolchainTests(unittest.TestCase):
    def test_input_rejection_writes_a_completed_attempt_and_cannot_escalate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "scripts/select_submission_toolchain.py").write_text("raise SystemExit(3)\n")
            metadata = root / "metadata.json"
            metadata.write_text("{}")
            output = root / "github-output"
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/prepare_benchmark_toolchain.py"),
                "--benchmark-root", str(root), "--source-tar", str(root / "source.tar.gz"),
                "--metadata", str(metadata), "--expected", "leanprover/lean4:v4.36.0-rc2",
                "--apply", "--rejection-output", str(root / "results"),
            ], env=dict(os.environ, GITHUB_OUTPUT=str(output)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), "rejected=true\n")
            self.assertEqual(json.loads((root / "results/results.json").read_text()), {
                "passed": [], "statement_revisions": {},
                "rejection_reason": "unsupported_toolchain_request"})
            workflow = (ROOT / ".github/workflows/evaluate-attempt.yml").read_text()
            build = workflow.split("      - name: Build the benchmark root", 1)[1].split("      - name:", 1)[0]
            self.assertIn("if: steps.configuration.outputs.rejected != 'true'", build)

    def test_old_snapshots_keep_their_single_pin_and_check_the_frozen_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "lean-toolchain").write_text("leanprover/lean4:v4.34.0\n")
            self.assertEqual(MODULE.prepare(root, root / "absent.tar.gz", root / "absent.json"),
                             "leanprover/lean4:v4.34.0")
            with self.assertRaisesRegex(ValueError, "archived evaluation toolchain"):
                MODULE.prepare(root, root / "absent.tar.gz", root / "absent.json",
                               expected="leanprover/lean4:v4.35.0")

    def test_trusted_helper_receives_locked_problem_selection_and_preparation_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            helper = root / "scripts/select_submission_toolchain.py"
            helper.write_text("import json,pathlib,sys\n"
                              "pathlib.Path('called.json').write_text(json.dumps(sys.argv[1:]))\n"
                              "print('leanprover/lean4:v4.35.0')\n")
            metadata = root / "metadata.json"
            metadata.write_text(json.dumps({"problem_id": "two_plus_two"}))
            MODULE.prepare(root, root / "source.tar.gz", metadata,
                           expected="leanprover/lean4:v4.35.0", apply=True)
            arguments = json.loads((root / "called.json").read_text())
            self.assertEqual(arguments[arguments.index("--problem") + 1], "two_plus_two")
            self.assertIn("--apply", arguments)
            self.assertNotIn("--archive", arguments)
            helper.write_text("raise SystemExit(7)\n")
            with self.assertRaises(subprocess.CalledProcessError):
                MODULE.prepare(root, root / "source.tar.gz", metadata)

    def test_archive_selection_and_regeneration_are_ordered_around_trusted_setup(self):
        attempt = (ROOT / ".github/workflows/evaluate-attempt.yml").read_text()
        select = attempt.index("- name: Select and verify the archived benchmark configuration")
        verify = attempt.index("- name: Verify frozen source identity")
        build = attempt.index("- name: Build the benchmark root")
        regenerate = attempt.index("- name: Regenerate selected workspaces")
        unpack = attempt.index("tar -xzf /tmp/fetch-out/source.tar.gz")
        self.assertLess(verify, select)
        self.assertLess(select, build)
        self.assertLess(build, regenerate)
        self.assertLess(regenerate, unpack)
        for workflow in ["submission.yml", "server-archive.yml"]:
            content = (ROOT / ".github/workflows" / workflow).read_text()
            self.assertIn("toolchain: ${{ steps.toolchain.outputs.toolchain || steps.benchmark.outputs.toolchain }}", content)
            self.assertIn("--metadata /tmp/archive-fetch/metadata.json --archive", content)


if __name__ == "__main__":
    unittest.main()
