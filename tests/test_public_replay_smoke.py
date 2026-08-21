from __future__ import annotations

import copy
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "public-replay-smoke-v1.json"

import sys

sys.path.insert(0, str(SCRIPTS))

from public_replay_smoke import (  # noqa: E402
    SmokeError,
    build_evidence,
    validate_config,
    validate_evidence,
    validate_public_dependency_git,
)


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class PublicReplaySmokeTests(unittest.TestCase):
    def test_public_dependency_git_metadata_is_credential_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            config = root / "mathlib" / ".git" / "config"
            config.parent.mkdir(parents=True)
            config.write_text(
                '[core]\n\trepositoryformatversion = 0\n'
                '[remote "origin"]\n'
                '\turl = https://github.com/leanprover-community/mathlib4.git\n'
                '\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
                encoding="utf-8",
            )
            validate_public_dependency_git(root)

            config.write_text(
                config.read_text(encoding="utf-8")
                + '[http "https://github.com/"]\n\textraheader = AUTHORIZATION: secret\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SmokeError, "credential-bearing"):
                validate_public_dependency_git(root)

    def test_dependency_git_rejects_non_public_remote_and_nesting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            config = root / "package" / ".git" / "config"
            config.parent.mkdir(parents=True)
            config.write_text(
                '[remote "origin"]\n\turl = git@github.com:owner/private.git\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SmokeError, "not public GitHub HTTPS"):
                validate_public_dependency_git(root)

            config.write_text(
                '[remote "origin"]\n'
                '\turl = https://github.com/owner/public.git\n',
                encoding="utf-8",
            )
            nested = root / "package" / "nested" / ".git"
            nested.mkdir(parents=True)
            (nested / "config").write_text(
                '[remote "origin"]\n\turl = https://github.com/owner/public.git\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SmokeError, "one package deep"):
                validate_public_dependency_git(root)

    def test_tracked_fixture_is_strict_and_public(self) -> None:
        config = validate_config(fixture())
        self.assertEqual(config["problem_id"], "two_plus_two")
        self.assertEqual(config["statement_revision"], 1)
        self.assertEqual(config["source"]["visibility"], "public")

    def test_unknown_fields_and_private_sources_fail_closed(self) -> None:
        config = fixture()
        config["unexpected"] = True
        with self.assertRaisesRegex(SmokeError, "extra"):
            validate_config(config)
        config = fixture()
        config["source"]["visibility"] = "private"
        with self.assertRaisesRegex(SmokeError, "must be public"):
            validate_config(config)

    def test_evidence_requires_exact_single_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "Submission").mkdir()
            (root / "Submission.lean").write_text("by\n  norm_num\n", encoding="utf-8")
            (root / "Submission" / "Helpers.lean").write_text("def helper := 4\n", encoding="utf-8")
            results = {"passed": ["two_plus_two"]}
            summary = {
                "run_eval": {
                    "problems": [
                        {"id": "two_plus_two", "succeeded": True, "exit_code": 0}
                    ]
                }
            }
            with mock.patch.dict(
                os.environ,
                {"ImageOS": "ubuntu24", "ImageVersion": "20260817.1", "RUNNER_ARCH": "X64"},
                clear=False,
            ):
                evidence = build_evidence(
                    fixture(),
                    results,
                    summary,
                    source_dir=root,
                    workflow_commit="1" * 40,
                    wall_time_ms=1234,
                    counter_path=root / "absent-counter.csv",
                )
            validate_evidence(evidence)
            self.assertEqual(evidence["outcome"], "accepted")
            self.assertEqual(evidence["statement_revision"], 1)
            self.assertEqual(evidence["statistics"]["file_count"], 2)
            self.assertEqual(evidence["statistics"]["lines_of_code"], 3)
            self.assertEqual(
                evidence["statistics"]["pipeline_retired_instructions"],
                {"status": "unavailable", "reason": "counter_not_reported"},
            )

            failed_results = copy.deepcopy(results)
            failed_results["passed"] = []
            with mock.patch.dict(
                os.environ,
                {"ImageOS": "ubuntu24", "ImageVersion": "20260817.1", "RUNNER_ARCH": "X64"},
                clear=False,
            ):
                with self.assertRaisesRegex(SmokeError, "did not reproduce"):
                    build_evidence(
                        fixture(),
                        failed_results,
                        summary,
                        source_dir=root,
                        workflow_commit="1" * 40,
                        wall_time_ms=1234,
                        counter_path=root / "absent-counter.csv",
                    )

    def test_measured_counter_is_parsed_without_locale_punctuation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "Submission.lean").write_text("by norm_num\n", encoding="utf-8")
            counter = root / "counter.csv"
            counter.write_text("123456,,instructions:u,123.00,100.00,\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"ImageOS": "ubuntu24", "ImageVersion": "20260817.1", "RUNNER_ARCH": "X64"},
                clear=False,
            ):
                evidence = build_evidence(
                    fixture(),
                    {"passed": ["two_plus_two"]},
                    {
                        "run_eval": {
                            "problems": [
                                {
                                    "id": "two_plus_two",
                                    "succeeded": True,
                                    "exit_code": 0,
                                }
                            ]
                        }
                    },
                    source_dir=root,
                    workflow_commit="2" * 40,
                    wall_time_ms=1,
                    counter_path=counter,
                )
            self.assertEqual(
                evidence["statistics"]["pipeline_retired_instructions"],
                {"status": "measured", "value": 123456},
            )

    def test_historical_results_shape_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "Submission.lean").write_text("by norm_num\n", encoding="utf-8")
            summary = {
                "run_eval": {
                    "problems": [
                        {"id": "two_plus_two", "succeeded": True, "exit_code": 0}
                    ]
                }
            }
            with mock.patch.dict(
                os.environ,
                {"ImageOS": "ubuntu24", "ImageVersion": "test", "RUNNER_ARCH": "X64"},
                clear=False,
            ):
                with self.assertRaisesRegex(SmokeError, "results fields"):
                    build_evidence(
                        fixture(),
                        {
                            "passed": ["two_plus_two"],
                            "statement_revisions": {"two_plus_two": 1},
                        },
                        summary,
                        source_dir=root,
                        workflow_commit="3" * 40,
                        wall_time_ms=1,
                        counter_path=root / "absent-counter.csv",
                    )


if __name__ == "__main__":
    unittest.main()
