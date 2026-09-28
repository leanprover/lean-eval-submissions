"""Exercise production dependency preparation with a cold, read-only package tree."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_benchmark_root as br  # noqa: E402


@unittest.skipUnless(
    os.environ.get("LEAN_EVAL_SANDBOX_TEST") == "1",
    "requires Lean and landrun; run in the solution dependency sandbox CI job",
)
class SolutionDependencySandboxTest(unittest.TestCase):
    def test_production_preparation_enables_cold_solution_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = root / "benchmark"
            workspace = root / "workspace"
            pool = root / "shared" / "lean-pool"
            (pool / "LeanPool").mkdir(parents=True)
            benchmark.mkdir()
            workspace.mkdir()
            (pool / "lakefile.toml").write_text(
                'name = "lean-pool"\n[[lean_lib]]\nname = "LeanPool"\n'
            )
            (pool / "LeanPool.lean").write_text(
                "import LeanPool.Basic\nimport LeanPool.Extra\n"
            )
            (pool / "LeanPool" / "Basic.lean").write_text('def hello := "world"\n')
            (pool / "LeanPool" / "Extra.lean").write_text(
                "theorem pool_fact : (2 : Nat) + 2 = 4 := rfl\n"
            )
            require = (
                '[[require]]\nname = "lean-pool"\n'
                'path = "../shared/lean-pool"\n'
            )
            (benchmark / "lakefile.toml").write_text(
                'name = "benchmark"\ndefaultTargets = ["Benchmark"]\n'
                + require + '[[lean_lib]]\nname = "Benchmark"\n'
            )
            (benchmark / "Benchmark.lean").write_text("def benchmarkReady := true\n")
            self._run(["lake", "update"], benchmark)
            (benchmark / ".lake" / "packages").mkdir(parents=True, exist_ok=True)
            (workspace / "lakefile.toml").write_text(
                'name = "workspace"\n' + require
                + '[[lean_lib]]\nname = "Submission"\n'
            )
            (workspace / "Submission.lean").write_text(
                "import LeanPool.Extra\n"
                "theorem submitted : (2 : Nat) + 2 = 4 := pool_fact\n"
            )
            (workspace / ".lake" / "build").mkdir(parents=True)
            (workspace / ".lake" / "packages").symlink_to(
                benchmark / ".lake" / "packages", target_is_directory=True
            )
            shutil.copyfile(
                benchmark / "lake-manifest.json", workspace / "lake-manifest.json"
            )
            lean_prefix = Path(self._run(["lean", "--print-prefix"], benchmark).stdout.strip())
            sandbox = [
                "landrun", "--ro", "/", "--rw", "/dev",
                "--rox", str(lean_prefix), "--rwx", str(workspace / ".lake"),
                "--env", "PATH", "--env", "HOME", "--env", "LEAN_ABORT_ON_PANIC=1",
                "--ldd", "--add-exec", str(lean_prefix / "bin" / "lake"),
                "build", "Submission",
            ]
            cold = subprocess.run(sandbox, cwd=workspace, text=True, capture_output=True)
            self.assertNotEqual(cold.returncode, 0, cold.stdout + cold.stderr)
            self.assertIn("permission denied", cold.stdout + cold.stderr)

            # Exactly the helper production runs before unpacking submitted code.
            br.build_benchmark_root(benchmark)
            self.assertTrue((pool / ".lake/build/lib/lean/LeanPool/Extra.olean").is_file())
            self._run(sandbox, workspace)

    def _run(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result


if __name__ == "__main__":
    unittest.main()
