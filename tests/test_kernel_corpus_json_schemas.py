from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

from kernel_corpus_report import (
    KernelCorpusError,
    validate_inventory,
    validate_observation_shard,
    validate_plan,
    validate_report,
    validate_series,
)
from kernel_corpus_runner_adapter import validate_record_bundle

ARTIFACTS = {
    "kernel-checker-series-v1": "series",
    "kernel-corpus-inventory-v1": "inventory",
    "kernel-corpus-shard-plan-v1": "plan",
    "kernel-corpus-runner-records-v1": "runner_records",
    "kernel-corpus-observations-v1": "observations",
    "kernel-corpus-report-v1": "report",
}


def load(directory: pathlib.Path, name: str) -> dict:
    return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))


class KernelCorpusJsonSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = {name: load(SCHEMAS, f"{name}.schema") for name in ARTIFACTS}
        cls.fixtures = {name: load(FIXTURES, name) for name in ARTIFACTS}

    def test_schemas_are_draft_2020_12_and_fixtures_validate(self) -> None:
        for name in ARTIFACTS:
            with self.subTest(name=name):
                Draft202012Validator.check_schema(self.schemas[name])
                Draft202012Validator(self.schemas[name]).validate(self.fixtures[name])

    def test_fixtures_also_pass_semantic_identity_and_coverage_checks(self) -> None:
        series = validate_series(self.fixtures["kernel-checker-series-v1"])
        inventory = validate_inventory(self.fixtures["kernel-corpus-inventory-v1"])
        plan = validate_plan(
            self.fixtures["kernel-corpus-shard-plan-v1"], series, inventory
        )
        validate_record_bundle(
            self.fixtures["kernel-corpus-runner-records-v1"],
            plan,
            series,
            inventory,
        )
        observations = validate_observation_shard(
            self.fixtures["kernel-corpus-observations-v1"],
            plan,
            series,
            inventory,
        )
        validate_report(
            self.fixtures["kernel-corpus-report-v1"],
            series,
            inventory,
            [plan],
            [observations],
        )

    def test_schema_and_semantic_validator_both_reject_open_fields(self) -> None:
        changed = copy.deepcopy(self.fixtures["kernel-checker-series-v1"])
        changed["candidate"]["source"] = "Submission.lean"
        errors = list(
            Draft202012Validator(self.schemas["kernel-checker-series-v1"]).iter_errors(
                changed
            )
        )
        self.assertTrue(errors)
        with self.assertRaisesRegex(KernelCorpusError, "extra"):
            validate_series(changed)

    def test_schema_and_semantic_validator_both_reject_human_auto_approval(
        self,
    ) -> None:
        changed = copy.deepcopy(self.fixtures["kernel-corpus-report-v1"])
        changed["promotion"]["automated_eligibility"] = True
        errors = list(
            Draft202012Validator(self.schemas["kernel-corpus-report-v1"]).iter_errors(
                changed
            )
        )
        self.assertTrue(errors)

        series = self.fixtures["kernel-checker-series-v1"]
        inventory = self.fixtures["kernel-corpus-inventory-v1"]
        plans = [self.fixtures["kernel-corpus-shard-plan-v1"]]
        observations = [self.fixtures["kernel-corpus-observations-v1"]]
        with self.assertRaisesRegex(KernelCorpusError, "deterministic"):
            validate_report(changed, series, inventory, plans, observations)

    def test_schema_requires_outcome_aware_checker_invocations(self) -> None:
        schema = Draft202012Validator(self.schemas["kernel-corpus-observations-v1"])
        terminal = copy.deepcopy(self.fixtures["kernel-corpus-observations-v1"])
        accepted = next(
            item for item in terminal["observations"] if item["outcome"] == "accepted"
        )
        accepted["statistics"]["checker_invocations"] = 0
        accepted["execution_receipt"]["statistics"]["checker_invocations"] = 0
        self.assertTrue(list(schema.iter_errors(terminal)))

        exported = copy.deepcopy(self.fixtures["kernel-corpus-observations-v1"])
        exported_observation = next(
            item for item in exported["observations"] if item["outcome"] == "accepted"
        )
        exported_observation.update(
            {
                "status": "unavailable",
                "outcome": "export_unavailable",
                "evidence_sha256": "f" * 64,
            }
        )
        exported_observation["execution_receipt"]["outcome"] = "export_unavailable"
        exported_observation["statistics"]["checker_invocations"] = 1
        exported_observation["execution_receipt"]["statistics"][
            "checker_invocations"
        ] = 1
        self.assertTrue(list(schema.iter_errors(exported)))

    def test_ci_installs_only_hash_pinned_binary_schema_dependencies(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: read\n", workflow)
        self.assertIn(
            "python -m pip install --disable-pip-version-check "
            "--only-binary=:all:\n"
            "          --require-hashes -r requirements-jsonschema-workflow.txt",
            workflow,
        )

        requirements = (ROOT / "requirements-jsonschema-workflow.txt").read_text(
            encoding="utf-8"
        )
        requirements_lines = [
            line for line in requirements.splitlines() if line and not line.isspace()
        ]
        self.assertEqual(requirements.count("--hash=sha256:"), 6)
        self.assertEqual(sum("==" in line for line in requirements_lines), 6)


if __name__ == "__main__":
    unittest.main()
