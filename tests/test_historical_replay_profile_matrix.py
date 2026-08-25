from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import unittest
from unittest import mock

import jsonschema

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aggregate_public_replay_github_evidence import (
    canonical_document_bytes,
)
from prepare_historical_replay_profile_matrix import (
    ProfileMatrixError,
    build_matrix,
    validate_component_lock,
    validate_plan,
)
from prepare_public_replay_plan import validate_toolchain_registry

PLAN = (
    ROOT
    / "evidence/public-replay/plans/d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e.json"
)
REGISTRY = (
    ROOT
    / "evidence/public-replay/toolchains/4f2f3737d79e6abd6c169ebdde3f2218157d8f6c482a85ad2026821a4b8e81a0.json"
)
COMPONENTS = ROOT / "configuration/historical-public-replay-components-v1.json"
MATRIX = ROOT / "configuration/historical-public-replay-profile-matrix-v1.json"
SCHEMA = ROOT / "schemas/historical-public-replay-profile-matrix-v1.schema.json"


class HistoricalReplayProfileMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_raw = PLAN.read_bytes()
        cls.registry_raw = REGISTRY.read_bytes()
        cls.component_raw = COMPONENTS.read_bytes()
        cls.matrix_raw = MATRIX.read_bytes()
        cls.plan = json.loads(cls.plan_raw)
        cls.registry = json.loads(cls.registry_raw)
        cls.components = json.loads(cls.component_raw)
        cls.matrix = json.loads(cls.matrix_raw)
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        cls.toolchains = validate_toolchain_registry(cls.registry)
        jsonschema.Draft202012Validator.check_schema(cls.schema)

    def assert_plan_rejected(self, change, message: str) -> None:
        changed = copy.deepcopy(self.plan)
        change(changed)
        with self.assertRaises(ProfileMatrixError, msg=message):
            validate_plan(
                changed,
                self.toolchains,
                hashlib.sha256(self.registry_raw).hexdigest(),
            )

    def test_committed_matrix_is_canonical_schema_valid_and_source_free(self) -> None:
        self.assertEqual(canonical_document_bytes(self.matrix), self.matrix_raw)
        jsonschema.Draft202012Validator(self.schema).validate(self.matrix)
        self.assertEqual(self.matrix["image_count"], 35)
        self.assertEqual(self.matrix["toolchain_count"], 5)
        self.assertEqual(self.matrix["request_count"], 128)
        self.assertEqual(self.matrix["result_count"], 194)
        encoded = self.matrix_raw.decode("utf-8").lower()
        for forbidden in (
            "submission_source",
            "source_bytes",
            "ciphertext",
            "credential",
            "account_id",
            "archive_path",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_matrix_binds_every_exact_input_and_stays_unqualified(self) -> None:
        self.assertEqual(
            self.matrix["plan_sha256"], hashlib.sha256(self.plan_raw).hexdigest()
        )
        self.assertEqual(
            self.matrix["toolchain_registry_sha256"],
            hashlib.sha256(self.registry_raw).hexdigest(),
        )
        self.assertEqual(
            self.matrix["component_lock_sha256"],
            hashlib.sha256(self.component_raw).hexdigest(),
        )
        self.assertEqual(self.matrix["qualification_status"], "unqualified")
        self.assertEqual(
            self.matrix["qualification_requirements"],
            [
                "historical_public_runner_v1",
                "immutable_registry_publication_v1",
                "cloudflare_staging_runtime_probe_v1",
            ],
        )
        commits = [image["benchmark_commit"] for image in self.matrix["images"]]
        self.assertEqual(commits, sorted(commits))
        self.assertEqual(len(commits), len(set(commits)))
        self.assertTrue(
            all(
                image["qualification_status"] == "unqualified"
                for image in self.matrix["images"]
            )
        )

    def test_matrix_has_one_image_per_baked_benchmark_not_per_toolchain(self) -> None:
        versions: dict[str, int] = {}
        for image in self.matrix["images"]:
            versions[image["toolchain"]] = versions.get(image["toolchain"], 0) + 1
            lock = image["profile_lock"]
            self.assertEqual(lock["benchmark_commit"], image["benchmark_commit"])
            self.assertEqual(lock["toolchain"], image["toolchain"])
            self.assertEqual(
                lock["components"]["lean4export"]["repository"],
                "leanprover/lean4export",
            )
        self.assertEqual(
            versions,
            {
                "leanprover/lean4:v4.30.0": 3,
                "leanprover/lean4:v4.30.0-rc2": 21,
                "leanprover/lean4:v4.32.0-rc1": 3,
                "leanprover/lean4:v4.32.2": 5,
                "leanprover/lean4:v4.33.0": 3,
            },
        )
        layouts = [image["manifest_layout"] for image in self.matrix["images"]]
        self.assertEqual(layouts.count("monolith_v1"), 15)
        self.assertEqual(layouts.count("per_problem_v1"), 20)

    def test_producer_reconstructs_exact_bytes_from_reviewed_inspections(self) -> None:
        inspections = {
            image["benchmark_commit"]: {
                "benchmark_tree": image["benchmark_tree"],
                "manifest_layout": image["manifest_layout"],
                "workspace_count": image["workspace_count"],
            }
            for image in self.matrix["images"]
        }
        with mock.patch(
            "prepare_historical_replay_profile_matrix.inspect_benchmark",
            side_effect=lambda _repository, commit, _binding: inspections[commit],
        ):
            rebuilt = build_matrix(
                plan=self.plan,
                plan_raw=self.plan_raw,
                registry=self.registry,
                registry_raw=self.registry_raw,
                component_lock=self.components,
                component_raw=self.component_raw,
                benchmark_repository=ROOT,
            )
        self.assertEqual(canonical_document_bytes(rebuilt), self.matrix_raw)

    def test_plan_toolchain_drift_fails_closed(self) -> None:
        changed = copy.deepcopy(self.plan)
        changed["requests"][0]["benchmark"]["toolchain"] = "leanprover/lean4:v4.99.0"
        with self.assertRaisesRegex(ProfileMatrixError, "toolchain registry"):
            build_matrix(
                plan=changed,
                plan_raw=self.plan_raw,
                registry=self.registry,
                registry_raw=self.registry_raw,
                component_lock=self.components,
                component_raw=self.component_raw,
                benchmark_repository=ROOT,
            )

    def test_plan_contract_rejects_open_or_malformed_nested_shapes(self) -> None:
        changes = {
            "top-level extension": lambda plan: plan.__setitem__("extension", True),
            "request extension": lambda plan: plan["requests"][0].__setitem__(
                "extension", True
            ),
            "issue extension": lambda plan: plan["requests"][0]["issue"].__setitem__(
                "extension", True
            ),
            "evaluation extension": lambda plan: plan["requests"][0][
                "historical_evaluation"
            ].__setitem__("extension", True),
            "source extension": lambda plan: plan["requests"][0][
                "source"
            ].__setitem__("extension", True),
            "benchmark extension": lambda plan: plan["requests"][0][
                "benchmark"
            ].__setitem__("extension", True),
            "result extension": lambda plan: plan["requests"][0]["results"][
                0
            ].__setitem__("extension", True),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                self.assert_plan_rejected(change, label)

    def test_plan_contract_rejects_bool_numbers_and_noncanonical_ids(self) -> None:
        changes = {
            "boolean schema version": lambda plan: plan.__setitem__(
                "schema_version", True
            ),
            "boolean request count": lambda plan: plan.__setitem__(
                "resolved_request_count", True
            ),
            "boolean pending count": lambda plan: plan.__setitem__(
                "pending_request_count", False
            ),
            "boolean issue number": lambda plan: plan["requests"][0][
                "issue"
            ].__setitem__("number", True),
            "boolean workflow run": lambda plan: plan["requests"][0][
                "historical_evaluation"
            ].__setitem__("workflow_run_id", True),
            "boolean statement revision": lambda plan: plan["requests"][0][
                "results"
            ][0].__setitem__("statement_revision", True),
            "uppercase request id": lambda plan: plan["requests"][0].__setitem__(
                "request_id", "prr_" + "A" * 64
            ),
            "uppercase result id": lambda plan: plan["requests"][0]["results"][
                0
            ].__setitem__("result_id", "r2_" + "A" * 64),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                self.assert_plan_rejected(change, label)

    def test_plan_contract_rejects_cross_binding_and_order_drift(self) -> None:
        changes = {
            "derived result identity": lambda plan: plan["requests"][0]["results"][
                0
            ].__setitem__("problem_id", "different_problem"),
            "result owner": lambda plan: plan["requests"][0]["results"][
                0
            ].__setitem__("owner_login", "different-owner"),
            "result path": lambda plan: plan["requests"][0]["results"][0].__setitem__(
                "results_path", "results/different-owner.json"
            ),
            "result snapshot commit": lambda plan: plan["requests"][0]["results"][
                0
            ].__setitem__("results_commit", "f" * 40),
            "request ordering": lambda plan: plan["requests"].reverse(),
            "result ordering": lambda plan: plan["requests"][4]["results"].reverse(),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                self.assert_plan_rejected(change, label)

    def test_component_lock_contract_rejects_ambiguous_identity(self) -> None:
        exact_toolchains = {
            entry["lean_toolchain"] for entry in self.registry["commits"]
        }
        changes = {
            "boolean schema version": lambda lock: lock.__setitem__(
                "schema_version", True
            ),
            "top-level extension": lambda lock: lock.__setitem__("extension", True),
            "component extension": lambda lock: lock["components"][
                "comparator"
            ].__setitem__("extension", True),
            "component label swap": lambda lock: lock["components"][
                "comparator"
            ].__setitem__("repository", "zouuup/landrun"),
            "unrecognized runner": lambda lock: lock.__setitem__(
                "runner_profile", "some-other-runner"
            ),
            "lean4export extension": lambda lock: lock["lean4export"][0].__setitem__(
                "extension", True
            ),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                changed = copy.deepcopy(self.components)
                change(changed)
                with self.assertRaises(ProfileMatrixError):
                    validate_component_lock(changed, exact_toolchains)

    def test_direct_builder_rejects_raw_bytes_that_do_not_bind_inputs(self) -> None:
        arguments = {
            "plan": self.plan,
            "plan_raw": self.plan_raw,
            "registry": self.registry,
            "registry_raw": self.registry_raw,
            "component_lock": self.components,
            "component_raw": self.component_raw,
            "benchmark_repository": ROOT,
        }
        for raw_name in ("plan_raw", "registry_raw", "component_raw"):
            with self.subTest(raw_name=raw_name):
                changed = {**arguments, raw_name: b"{}\n"}
                with self.assertRaises(ProfileMatrixError):
                    build_matrix(**changed)

    def test_required_ci_executes_historical_profile_matrix_tests(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("python -m unittest discover", workflow)
        self.assertIn("-p 'test_*.py'", workflow)


if __name__ == "__main__":
    unittest.main()
