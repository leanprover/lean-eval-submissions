from __future__ import annotations

import copy
import json
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "kernel-shadow-smoke-v1.json"

import sys

sys.path.insert(0, str(SCRIPTS))

from kernel_shadow_smoke import (
    ShadowSmokeError,
    build_evidence,
    canonical_bytes,
    prepare_workspace,
    validate_evidence,
    validate_fixture,
)


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def make_pristine(root: pathlib.Path, value: dict) -> pathlib.Path:
    workspace = root / "generated" / value["problem_id"]
    (workspace / "Submission").mkdir(parents=True)
    (workspace / "Submission.lean").write_text("theorem old : True := by sorry\n")
    (workspace / "Submission" / "Helpers.lean").write_text("def old := 1\n")
    (workspace / "Challenge.lean").write_text("theorem target : True := by sorry\n")
    (workspace / "Solution.lean").write_text("import Submission\n")
    (workspace / "lean-toolchain").write_text(
        value["benchmark"]["toolchain"] + "\n", encoding="utf-8"
    )
    (workspace / "lakefile.toml").write_text(
        f'name = "{value["problem_id"]}"\n'
        '[[require]]\nname = "mathlib"\n'
        f'rev = "{value["benchmark"]["mathlib_commit"]}"\n',
        encoding="utf-8",
    )
    (workspace / "config.json").write_text(
        json.dumps(
            {
                "challenge_module": "Challenge",
                "solution_module": "Solution",
                "theorem_names": ["target"],
                "permitted_axioms": ["propext"],
                "enable_nanoda": False,
            }
        ),
        encoding="utf-8",
    )
    return root / "generated"


class KernelShadowSmokeTests(unittest.TestCase):
    def test_tracked_fixture_locks_clean_arena_candidate(self) -> None:
        value = validate_fixture(fixture())
        self.assertEqual(value["candidate"]["name"], "mathgraph")
        self.assertEqual(value["candidate"]["arena"]["accepted_passed"], 121)
        self.assertEqual(value["candidate"]["arena"]["rejected_passed"], 66)
        self.assertEqual(value["candidate"]["arena"]["declined"], 0)
        self.assertEqual(value["benchmark"]["toolchain"], "leanprover/lean4:v4.33.0")

    def test_fixture_rejects_unknown_fields_and_unclean_arena(self) -> None:
        value = fixture()
        value["unexpected"] = True
        with self.assertRaisesRegex(ShadowSmokeError, "extra"):
            validate_fixture(value)

        for field in ("accepted_passed", "rejected_passed"):
            value = fixture()
            value["candidate"]["arena"][field] -= 1
            with self.assertRaisesRegex(ShadowSmokeError, "not clean"):
                validate_fixture(value)

        value = fixture()
        value["candidate"]["arena"]["declined"] = 1
        with self.assertRaisesRegex(ShadowSmokeError, "declines"):
            validate_fixture(value)

    def test_prepare_workspace_overlays_only_submission_lean_files(self) -> None:
        value = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            generated = make_pristine(root, value)
            source = root / "source"
            (source / "Submission" / "Nested").mkdir(parents=True)
            (source / "Submission.lean").write_text(
                "theorem target : True := by trivial\n", encoding="utf-8"
            )
            (source / "Submission" / "Helpers.lean").write_text(
                "def helper := 2\n", encoding="utf-8"
            )
            (source / "Submission" / "Nested" / "More.lean").write_text(
                "def more := 3\n", encoding="utf-8"
            )
            candidate = root / "sokonanoda"
            candidate.write_text("binary", encoding="utf-8")
            candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)
            output = root / "workspace"

            prepare_workspace(
                value,
                source_root=source,
                generated_root=generated,
                output=output,
                candidate_binary=candidate,
            )

            self.assertIn("trivial", (output / "Submission.lean").read_text())
            self.assertIn("helper := 2", (output / "Submission" / "Helpers.lean").read_text())
            self.assertTrue((output / "Submission" / "Nested" / "More.lean").is_file())
            config = json.loads((output / "config.json").read_text(encoding="utf-8"))
            self.assertNotIn("enable_nanoda", config)
            self.assertEqual(
                config["external_kernels"],
                {"mathgraph-noda": [str(candidate.resolve())]},
            )

    def test_prepare_workspace_rejects_symlink_and_nonlean_helpers(self) -> None:
        value = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            generated = make_pristine(root, value)
            source = root / "source"
            (source / "Submission").mkdir(parents=True)
            (source / "Submission.lean").write_text("theorem target : True := by trivial\n")
            candidate = root / "sokonanoda"
            candidate.write_text("binary")
            candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)
            outside = root / "outside.lean"
            outside.write_text("def leak := 1\n")
            (source / "Submission" / "Leak.lean").symlink_to(outside)
            with self.assertRaisesRegex(ShadowSmokeError, "symlink"):
                prepare_workspace(
                    value,
                    source_root=source,
                    generated_root=generated,
                    output=root / "workspace-a",
                    candidate_binary=candidate,
                )

            (source / "Submission" / "Leak.lean").unlink()
            (source / "Submission" / "secret.txt").write_text("not copied")
            with self.assertRaisesRegex(ShadowSmokeError, "non-Lean"):
                prepare_workspace(
                    value,
                    source_root=source,
                    generated_root=generated,
                    output=root / "workspace-b",
                    candidate_binary=candidate,
                )

    def test_evidence_is_source_free_and_binds_fixture(self) -> None:
        value = fixture()
        with mock.patch.dict(
            os.environ,
            {"ImageOS": "ubuntu24", "ImageVersion": "test-image", "RUNNER_ARCH": "X64"},
            clear=False,
        ):
            evidence = build_evidence(
                value,
                workflow_commit="1" * 40,
                pipeline_exit_code=0,
                pipeline_wall_time_ms=123,
            )
        validate_evidence(evidence)
        self.assertEqual(evidence["outcome"], "accepted")
        self.assertEqual(evidence["fixture_id"], value["fixture_id"])
        self.assertEqual(
            evidence["fixture_sha256"],
            __import__("hashlib").sha256(canonical_bytes(value)).hexdigest(),
        )
        rendered = json.dumps(evidence)
        self.assertNotIn("Submission.lean", rendered)
        self.assertNotIn("command_output", rendered)

        failed = copy.deepcopy(evidence)
        failed["outcome"] = "pipeline_failed"
        validate_evidence(failed)

    def test_evidence_rejects_unknown_and_malformed_values(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"ImageOS": "ubuntu24", "ImageVersion": "test-image", "RUNNER_ARCH": "X64"},
            clear=False,
        ):
            evidence = build_evidence(
                fixture(),
                workflow_commit="2" * 40,
                pipeline_exit_code=1,
                pipeline_wall_time_ms=1,
            )
        evidence["unexpected"] = True
        with self.assertRaisesRegex(ShadowSmokeError, "extra"):
            validate_evidence(evidence)

        del evidence["unexpected"]
        evidence["fixture_sha256"] = "0" * 64
        with self.assertRaisesRegex(ShadowSmokeError, "does not bind"):
            validate_evidence(evidence)

        with mock.patch.dict(
            os.environ,
            {"ImageOS": "ubuntu22", "ImageVersion": "test-image", "RUNNER_ARCH": "X64"},
            clear=False,
        ), self.assertRaisesRegex(ShadowSmokeError, "ubuntu24"):
            build_evidence(
                fixture(),
                workflow_commit="2" * 40,
                pipeline_exit_code=0,
                pipeline_wall_time_ms=1,
            )


if __name__ == "__main__":
    unittest.main()
