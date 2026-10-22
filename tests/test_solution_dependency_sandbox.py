"""Exercise private solution dependency builds inside comparator-style sandboxes."""

from __future__ import annotations

import os
import json
import shlex
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
    "requires Lean and bubblewrap; run in the solution dependency sandbox CI job",
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
            toolchain = os.environ.get("ELAN_TOOLCHAIN", "leanprover/lean4:v4.35.0-rc3")
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
                + '[[lean_lib]]\nname = "Challenge"\n'
                + '[[lean_lib]]\nname = "Solution"\n'
                + '[[lean_lib]]\nname = "Submission"\n'
            )
            (workspace / "Submission.lean").write_text(
                "import LeanPool.Extra\n"
                "theorem submitted : (2 : Nat) + 2 = 4 := pool_fact\n"
            )
            (workspace / "Challenge.lean").write_text(
                "theorem target : (2 : Nat) + 2 = 4 := rfl\n"
            )
            (workspace / "Solution.lean").write_text(
                "import Submission\ntheorem target : (2 : Nat) + 2 = 4 := submitted\n"
            )
            (workspace / ".lake" / "build").mkdir(parents=True)
            (workspace / ".lake" / "packages").symlink_to(
                benchmark / ".lake" / "packages", target_is_directory=True
            )
            shutil.copyfile(
                benchmark / "lake-manifest.json", workspace / "lake-manifest.json"
            )
            lean_prefix = Path(self._run(["lean", "--print-prefix"], benchmark).stdout.strip())
            (workspace / "config.json").write_text(json.dumps({
                "challenge_module": "Challenge", "solution_module": "Solution",
                "theorem_names": ["target"],
                "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
                "external_kernels": {"nanoda": [str(lean_prefix / "bin/nanoda_bin")]},
            }))
            real_bwrap = str(Path(shutil.which(os.environ.get("COMPARATOR_BWRAP", "bwrap"))).resolve())
            cache = str((benchmark / ".lake/packages").resolve())
            wrapper = root / "bwrap-with-cache"
            wrapper.write_text(
                f"#!{Path(shutil.which('sh')).resolve()}\n"
                "inserted=\nfor arg do\n  shift\n"
                '  if [ "$arg" = -- ] && [ -z "$inserted" ]; then\n'
                f'    set -- "$@" --ro-bind {shlex.quote(cache)} {shlex.quote(cache)}\n'
                '    inserted=1\n  fi\n  set -- "$@" "$arg"\ndone\n'
                f'exec {shlex.quote(real_bwrap)} "$@"\n'
            )
            wrapper.chmod(0o755)
            sandbox = [str(lean_prefix / "bin/lake"), "comparator", "--config", "config.json"]
            # Only the adapter's private Lean Pool layout changes between runs.
            previous_bwrap = os.environ.get("COMPARATOR_BWRAP")
            self.addCleanup(self._restore_bwrap, previous_bwrap)
            os.environ["COMPARATOR_BWRAP"] = str(wrapper)
            cold = subprocess.run(sandbox, cwd=workspace, text=True, capture_output=True)
            self.assertNotEqual(cold.returncode, 0, cold.stdout + cold.stderr)
            self.assertIn("read-only file system", cold.stdout + cold.stderr)

            # Production setup creates a private build directory without
            # elaborating either dependency or submitted source on the runner.
            self.assertIsNone(ev._share_packages(workspace, benchmark / ".lake/packages"))
            ev._install_root_manifest(workspace, benchmark / "lake-manifest.json")
            private_pool = workspace / ".lake/packages/lean-pool"
            shared_pool = benchmark / ".lake/packages/lean-pool"
            self.assertFalse((private_pool / ".lake").is_symlink())
            built = self._run(sandbox, workspace)
            self.assertIn("nanoda kernel accepts the solution", built.stdout)
            self.assertIn("Lean default kernel accepts the solution", built.stdout)
            self.assertNotIn("warning:", built.stdout + built.stderr)
            self.assertTrue((private_pool / ".lake/build/lib/lean/LeanPool/Extra.olean").is_file())
            self.assertFalse((shared_pool / ".lake/build/lib/lean/LeanPool/Extra.olean").exists())
            self.assertFalse((private_pool / ".lake/build/lib/lean/LeanPool/Basic.olean").exists())

    @staticmethod
    def _restore_bwrap(previous: str | None) -> None:
        if previous is None:
            os.environ.pop("COMPARATOR_BWRAP", None)
        else:
            os.environ["COMPARATOR_BWRAP"] = previous

    def _run(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result


if __name__ == "__main__":
    unittest.main()
