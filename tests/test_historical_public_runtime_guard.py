from __future__ import annotations

import ast
import copy
import json
import re
import pathlib
import subprocess
import sys
import tempfile
import unittest

from tests.test_historical_replay_controller import Fixture
from scripts.historical_replay_controller import (
    HistoricalReplayControllerError, build_executor_request, started_event, validate_executor_request,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMAND = ROOT / "server/src/historical-public-command.ts"


def guard_code() -> str:
    text = COMMAND.read_text()
    # A JavaScript template literal containing only escaped backslashes.
    return text.split("PUBLIC_RUNTIME_GUARD = `", 1)[1].split("`;", 1)[0].replace("\\\\", "\\")


def composed_command() -> str:
    expression = COMMAND.read_text().split("export const HISTORICAL_PUBLIC_COMMAND =", 1)[1].split(";", 1)[0]
    token = re.compile(r'`([^`]*)`|"(?:[^"\\]|\\.)*"', re.S)
    parts = []
    consumed = 0
    for match in token.finditer(expression):
        if expression[consumed:match.start()].strip(" +\n\t"):
            raise AssertionError("unknown command expression")
        value = match.group(1).replace("${PUBLIC_RUNTIME_GUARD}", guard_code()) if match.group(1) is not None else json.loads(match.group(0))
        parts.append(value)
        consumed = match.end()
    if expression[consumed:].strip(" +\n\t"):
        raise AssertionError("unknown command expression suffix")
    return "".join(parts)


class HistoricalPublicRuntimeGuardTests(unittest.TestCase):
    def test_all_remaining_immutable_sources_have_identical_reviewed_guard_functions(self) -> None:
        profiles = [json.loads(p.read_text()) for p in (ROOT / "evidence/public-replay/profiles").glob("*.json")]
        sources = {p["image_source_commit"] for p in profiles}
        reference = ast.parse((ROOT / "server/replay-image/replay-authoritative").read_text())
        names = {"platform_evidence", "validate_runtime_profile", "AuthoritativeReplayError"}
        expected = {n.name: ast.dump(n, include_attributes=False) for n in reference.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names}
        for commit in sources:
            with self.subTest(commit=commit):
                raw = subprocess.check_output(["git", "show", f"{commit}:server/replay-image/replay-authoritative"], cwd=ROOT, text=True)
                tree = ast.parse(raw)
                found = {n.name: ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names}
                self.assertEqual(found, expected)
                for n in tree.body:
                    self.assertIsInstance(n, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.ClassDef, ast.If, ast.Expr))
                    if isinstance(n, (ast.Import, ast.ImportFrom)):
                        modules = [a.name.split(".")[0] for a in n.names] if isinstance(n, ast.Import) else [(n.module or "").split(".")[0]]
                        self.assertTrue(set(modules) <= {"__future__", "base64", "hashlib", "json", "os", "pathlib", "signal", "shutil", "subprocess", "sys", "tarfile", "typing"})
                    if isinstance(n, ast.Expr): self.assertIsInstance(n.value, ast.Constant)
                    if isinstance(n, ast.If): self.assertEqual(ast.unparse(n.test), "__name__ == '__main__'")
                docker = subprocess.check_output(["git", "show", f"{commit}:Dockerfile.historical-public-replay"], cwd=ROOT, text=True)
                self.assertIn("apt-get install --yes --no-install-recommends", docker)
                self.assertIn("python3", docker)
                self.assertIn("COPY server/replay-image/replay-authoritative /opt/lean-eval/replay-authoritative", docker)

    def test_actual_constant_guard_matches_or_fails_before_source_decode(self) -> None:
        code = guard_code()
        self.assertNotIn("'", code)
        ast.parse(code)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            module = root / "replay-authoritative"
            module.write_bytes((ROOT / "server/replay-image/replay-authoritative").read_bytes())
            profile = root / "profile.json"
            # Only fixed absolute test paths change; guard body is otherwise exact.
            adapted = code.replace("/workspace/historical-public-runtime-profile.json", str(profile)).replace("/opt/lean-eval/replay-authoritative", str(module))
            observed = subprocess.check_output([sys.executable, "-I", "-S", "-B", "-c", f'import runpy,json; m=runpy.run_path({str(module)!r}); print(json.dumps(m["platform_evidence"]()))'], text=True)
            architecture, kernel, cpu = json.loads(observed)
            expected = {"architecture": architecture, "kernel_release": kernel, "cpu_model": cpu}
            profile.write_text(json.dumps(expected))
            good = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", adapted], text=True, capture_output=True)
            self.assertEqual((good.returncode, good.stdout, good.stderr), (0, "", ""))
            for field in expected:
                profile.write_text(json.dumps({**expected, field: "mismatch"}))
                bad = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", adapted], text=True, capture_output=True)
                self.assertEqual((bad.returncode, bad.stdout, bad.stderr), (42, "", "historical-public-runtime: runtime_profile_mismatch\n"))
            profile.unlink()
            missing = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", adapted], text=True, capture_output=True)
            self.assertEqual((missing.returncode, missing.stdout, missing.stderr), (43, "", "historical-public-runtime: runtime_profile_guard_failed\n"))
            self.assertFalse((root / "__pycache__").exists())

    def test_composed_multiline_shell_command_stops_before_decode_on_mismatch(self) -> None:
        import base64
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            module = root / "replay-authoritative"
            module.write_bytes((ROOT / "server/replay-image/replay-authoritative").read_bytes())
            profile = root / "profile.json"
            encoded = root / "source.b64"
            decoded = root / "source.tar.gz"
            runner = root / "runner"
            runner.write_text("#!/bin/sh\necho trusted-runner-reached\n")
            runner.chmod(0o700)
            observed = subprocess.check_output([sys.executable, "-I", "-S", "-B", "-c", f'import runpy,json; m=runpy.run_path({str(module)!r}); print(json.dumps(m["platform_evidence"]()))'], text=True)
            architecture, kernel, cpu = json.loads(observed)
            command = composed_command()
            for before, after in {
                "/usr/bin/python3": sys.executable,
                "/workspace/historical-public-runtime-profile.json": str(profile),
                "/opt/lean-eval/replay-authoritative": str(module),
                "/workspace/historical-public-source.tar.gz.b64": str(encoded),
                "/workspace/historical-public-source.tar.gz": str(decoded),
                "/opt/lean-eval/historical-public-runner": str(runner),
            }.items():
                command = command.replace(before, after)
            for mismatch in (False, True):
                decoded.unlink(missing_ok=True)
                encoded.write_text(base64.b64encode(b"trusted tiny fixture").decode())
                profile.write_text(json.dumps({"architecture": architecture, "kernel_release": "wrong" if mismatch else kernel, "cpu_model": cpu}))
                result = subprocess.run(["bash", "-c", command], text=True, capture_output=True)
                if mismatch:
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (42, "", "historical-public-runtime: runtime_profile_mismatch\n"))
                    self.assertTrue(encoded.exists())
                    self.assertFalse(decoded.exists())
                else:
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "trusted-runner-reached\n", ""))
                    self.assertFalse(encoded.exists())
                    self.assertEqual(decoded.read_bytes(), b"trusted tiny fixture")
            # A semicolon regression in the actual prefix must be observable:
            # the guard fails, but decoding/runner would still execute.
            decoded.unlink(missing_ok=True)
            encoded.write_text(base64.b64encode(b"trusted tiny fixture").decode())
            profile.write_text(json.dumps({"architecture": architecture, "kernel_release": "wrong", "cpu_model": cpu}))
            mutated = command.replace("&& base64", "; base64", 1)
            self.assertNotEqual(mutated, command)
            result = subprocess.run(["bash", "-c", mutated], text=True, capture_output=True)
            self.assertEqual(result.stdout, "trusted-runner-reached\n")
            self.assertTrue(decoded.exists())

    def test_controller_binds_full_profile_and_rejects_tampered_runtime(self) -> None:
        f = Fixture()
        plan = f.plan()
        started = started_event(plan, f.queue, "2026-08-25T01:00:00.004Z", random_bytes=b"\x01" * 10)
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "source.tar.gz"
            archive.write_bytes(b"fixture")
            request = build_executor_request(plan, f.handoff(b"fixture"), archive, f.matrix, f.contract, "4" * 64)
        self.assertEqual(request["execution_profile"], plan["execution_profile"])
        validate_executor_request(plan, started, request)
        for mutation in ("kernel_release", "cpu_model", "architecture"):
            tampered = copy.deepcopy(request)
            tampered["execution_profile"][mutation] = "wrong"
            with self.subTest(field=mutation), self.assertRaises(ValueError):
                validate_executor_request(plan, started, tampered)
        del request["execution_profile"]
        with self.assertRaises(HistoricalReplayControllerError):
            validate_executor_request(plan, started, request)


if __name__ == "__main__":
    unittest.main()
