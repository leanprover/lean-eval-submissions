from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_benchmark_root as br  # noqa: E402


TAUCETI_EXPORT = "export LAKE_CONFIG=/tmp/dependency-cache.toml LAKE_ARTIFACT_CACHE=true"


def _manifest(names: list[str]) -> str:
    return json.dumps(
        {
            "version": "1.1.0",
            "packages": [{"name": name, "rev": "0" * 40} for name in names],
        }
    )


class BuildBenchmarkRootTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.root = self.tmp / "lean-eval"
        (self.root / "scripts").mkdir(parents=True)
        self.calls = self.tmp / "lake-calls.jsonl"
        self.github_env = self.tmp / "github-env"
        self.github_env.write_text("", encoding="utf-8")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        lake = bin_dir / "lake"
        # A fake lake that records its argv and Lake environment, prints the
        # lines configured for its target, then optionally lingers.
        lake.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import json, os, sys, time
                with open({str(self.calls)!r}, "a", encoding="utf-8") as f:
                    f.write(json.dumps({{
                        "argv": sys.argv[1:],
                        "cwd": os.getcwd(),
                        "env": {{k: v for k, v in os.environ.items()
                                if k.startswith("LAKE_") or k == "GITHUB_ENV"}},
                    }}) + "\\n")
                target = sys.argv[2] if len(sys.argv) > 2 else "root"
                for line in json.loads(os.environ.get("FAKE_LAKE_OUTPUT", "{{}}")).get(target, []):
                    print(line, flush=True)
                time.sleep(float(os.environ.get("FAKE_LAKE_LINGER", "0")))
                sys.exit(int(os.environ.get("FAKE_LAKE_RC", "0")))
                """
            ),
            encoding="utf-8",
        )
        lake.chmod(0o755)
        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_ENV": str(self.github_env),
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_fetch_script(self, body: str) -> None:
        (self.root / "scripts" / "fetch_dependency_caches.sh").write_text(
            "set -euo pipefail\n"
            # Mirror the real script: append to $GITHUB_ENV when set.
            'if [ -n "${GITHUB_ENV:-}" ]; then echo LAKE_CONFIG=leak >> "$GITHUB_ENV"; fi\n'
            + body,
            encoding="utf-8",
        )

    def _run(self) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = br.main([str(self.root)])
        return rc, stdout.getvalue(), stderr.getvalue()

    def _calls(self) -> list[dict]:
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_benchmark_without_tauceti_gets_a_plain_build(self) -> None:
        (self.root / "lake-manifest.json").write_text(
            _manifest(["mathlib", "batteries"]), encoding="utf-8"
        )
        self._write_fetch_script("exit 1\n")
        rc, _, stderr = self._run()
        self.assertEqual(rc, 0, stderr)
        calls = self._calls()
        self.assertEqual([call["argv"] for call in calls], [["build"]])
        self.assertNotIn("LAKE_RESTORE_ARTIFACTS", calls[0]["env"])
        self.assertEqual(self.github_env.read_text(), "")

    def test_tauceti_without_fetch_script_fails_before_building(self) -> None:
        (self.root / "lake-manifest.json").write_text(
            _manifest(["mathlib", "TauCeti"]), encoding="utf-8"
        )
        rc, _, stderr = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("pins TauCeti", stderr)
        self.assertIn("fetch_dependency_caches.sh", stderr)
        self.assertEqual(self._calls(), [])

    def test_tauceti_restores_whole_library_then_root_with_step_scoped_cache(self) -> None:
        (self.root / "lake-manifest.json").write_text(
            _manifest(["«lean-eval-generator»", "mathlib", "TauCeti"]),
            encoding="utf-8",
        )
        self._write_fetch_script(f'echo "{TAUCETI_EXPORT}"\n')
        os.environ["FAKE_LAKE_OUTPUT"] = json.dumps(
            {
                "TauCeti": [
                    "✔ [4477/4479] Unpacked TauCeti.GroupTheory.Foo",
                    "✔ [4478/4479] Fetched TauCeti.GroupTheory.Bar",
                ],
                "root": [
                    "⚠ [4480/4482] Built LeanEval.GroupTheory.Baz (2.1s)",
                    "Build completed successfully (4482 jobs).",
                ],
            }
        )
        rc, stdout, stderr = self._run()
        self.assertEqual(rc, 0, stderr)
        calls = self._calls()
        self.assertEqual(
            [call["argv"] for call in calls], [["build", "TauCeti"], ["build"]]
        )
        for call in calls:
            self.assertEqual(pathlib.Path(call["cwd"]), self.root.resolve())
            self.assertEqual(
                call["env"],
                {
                    "LAKE_CONFIG": "/tmp/dependency-cache.toml",
                    "LAKE_ARTIFACT_CACHE": "true",
                    "LAKE_RESTORE_ARTIFACTS": "true",
                },
            )
        # The fetch script saw no GITHUB_ENV, so later steps inherit nothing.
        self.assertEqual(self.github_env.read_text(), "")
        self.assertIn("Unpacked TauCeti.GroupTheory.Foo", stdout)
        self.assertIn("Build completed successfully", stdout)

    def test_compiling_a_tauceti_module_stops_the_build_and_fails(self) -> None:
        (self.root / "lake-manifest.json").write_text(
            _manifest(["mathlib", "TauCeti"]), encoding="utf-8"
        )
        self._write_fetch_script(f'echo "{TAUCETI_EXPORT}"\n')
        os.environ["FAKE_LAKE_OUTPUT"] = json.dumps(
            {
                "TauCeti": [
                    "✔ [10/4479] Unpacked TauCeti.A",
                    "✔ [11/4479] Built TauCeti.Algebra.Lie.B (41s)",
                ]
            }
        )
        os.environ["FAKE_LAKE_LINGER"] = "60"
        started = time.monotonic()
        rc, _, stderr = self._run()
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(rc, 1)
        self.assertIn("compiled TauCeti instead of downloading it", stderr)
        self.assertIn("Built TauCeti.Algebra.Lie.B", stderr)
        self.assertEqual([call["argv"] for call in self._calls()], [["build", "TauCeti"]])

    def test_failed_tauceti_job_counts_as_compiling(self) -> None:
        self.assertTrue(br.COMPILED_TAUCETI.search("✖ [3/9] Building TauCeti.X"))
        for line in (
            "✔ [3/9] Unpacked TauCeti.X",
            "✔ [3/9] Replayed TauCeti.X",
            "✔ [3/9] Fetched TauCeti.X",
            "✔ [3/9] Built LeanEval.TauCetiProblems",
            "✔ [3/9] Built Mathlib.Order.Basic (1.2s)",
        ):
            self.assertIsNone(br.COMPILED_TAUCETI.search(line), line)

    def test_fetch_script_failure_or_bad_output_fails_closed(self) -> None:
        (self.root / "lake-manifest.json").write_text(
            _manifest(["mathlib", "TauCeti"]), encoding="utf-8"
        )
        for body, message in (
            ("exit 3\n", "failed with exit code 3"),
            ("true\n", "no single `export` line"),
            ('echo "export LAKE_CONFIG=/tmp/x"\n', "did not enable"),
            ('echo "export LAKE_CONFIG=/tmp/x LAKE_ARTIFACT_CACHE=false"\n', "did not enable"),
            (f'echo "{TAUCETI_EXPORT} LD_PRELOAD=/tmp/evil.so"\n', "unexpected export"),
        ):
            with self.subTest(body=body):
                self._write_fetch_script(body)
                rc, _, stderr = self._run()
                self.assertEqual(rc, 1)
                self.assertIn(message, stderr)
                self.assertEqual(self._calls(), [])

    def test_lake_failure_is_reported(self) -> None:
        (self.root / "lake-manifest.json").write_text(
            _manifest(["mathlib"]), encoding="utf-8"
        )
        os.environ["FAKE_LAKE_RC"] = "1"
        rc, _, stderr = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("`lake build` failed with exit code 1", stderr)


if __name__ == "__main__":
    unittest.main()
