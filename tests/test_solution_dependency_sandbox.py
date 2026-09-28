"""Exercise private solution dependency builds inside comparator-style sandboxes."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_submission as ev  # noqa: E402


@unittest.skipUnless(
    os.environ.get("LEAN_EVAL_SANDBOX_TEST") == "1",
    "requires Lean and landrun; run in the solution dependency sandbox CI job",
)
class SolutionDependencySandboxTest(unittest.TestCase):
    def test_private_build_cache_enables_cold_solution_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = root / "benchmark"
            workspace = root / "workspace"
            pool = root / "pool-source"
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
            self._run(["git", "init", "-q", str(pool)], root)
            self._run(["git", "-C", str(pool), "add", "."], root)
            self._run([
                "git", "-C", str(pool), "-c", "user.name=Sandbox Fixture",
                "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture",
            ], root)
            rev = self._run(["git", "-C", str(pool), "rev-parse", "HEAD"], root).stdout.strip()
            require = (
                '[[require]]\nname = "lean-pool"\n'
                f'git = "{pool.as_uri()}"\nrev = "{rev}"\n'
            )
            toolchain = os.environ.get("ELAN_TOOLCHAIN", "leanprover/lean4:v4.34.0")
            for project in (benchmark, workspace):
                (project / "lean-toolchain").write_text(toolchain + "\n")
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
                "--rox", str(Path(shutil.which("git")).resolve().parent),
                "--rox", str(lean_prefix), "--rwx", str(workspace / ".lake"),
                "--env", "PATH", "--env", "HOME", "--env", "LEAN_ABORT_ON_PANIC=1",
                "--ldd", "--add-exec", str(lean_prefix / "bin" / "lake"),
                "build", "Submission",
            ]
            if Path("/nix/store").is_dir():
                # Nix wraps git with an interpreter and helper executables;
                # Ubuntu CI uses the ordinary /usr/bin/git binary.
                sandbox[1:1] = ["--rox", "/nix/store"]
            cold = subprocess.run(sandbox, cwd=workspace, text=True, capture_output=True)
            self.assertNotEqual(cold.returncode, 0, cold.stdout + cold.stderr)
            self.assertIn("permission denied", cold.stdout + cold.stderr)

            # Production setup creates a private build directory without
            # elaborating either dependency or submitted source on the runner.
            self.assertIsNone(ev._share_packages(workspace, benchmark / ".lake/packages"))
            ev._install_root_manifest(workspace, benchmark / "lake-manifest.json")
            private_pool = workspace / ".lake/packages/lean-pool"
            shared_pool = benchmark / ".lake/packages/lean-pool"
            self.assertFalse((private_pool / ".lake").is_symlink())
            built = self._run(sandbox, workspace)
            self.assertNotIn("warning:", built.stdout + built.stderr)
            self.assertTrue((private_pool / ".lake/build/lib/lean/LeanPool/Extra.olean").is_file())
            self.assertFalse((shared_pool / ".lake/build/lib/lean/LeanPool/Extra.olean").exists())
            self.assertFalse((private_pool / ".lake/build/lib/lean/LeanPool/Basic.olean").exists())

    def _run(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result


if __name__ == "__main__":
    unittest.main()
