import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aggregate_public_replay_github_evidence import (
    aggregate,
    canonical_document_bytes,
)
from build_public_replay_toolchain_registry import (
    ToolchainRegistryError,
    build_registry,
    main as build_toolchain_main,
    resolved_benchmark_commits,
)
from inventory_historical_replay import inventory
from prepare_public_replay_plan import (
    PublicReplayPlanError,
    build_plan,
)
from prepare_public_replay_resolution import prepare
from resolve_public_replay_github_evidence import resolve
from results_schema import canonical_file_bytes, result_id

from tests.test_resolve_public_replay_github_evidence import (
    BENCHMARK,
    SOURCE,
    FakeClient,
    adjudication_bytes,
    registry_bytes,
)

SOURCE_COMMIT = "4" * 40


def canonical(value: dict) -> bytes:
    return canonical_document_bytes(value)


class PublicReplayPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.results_root = self.root / "results"
        self.results_root.mkdir()
        records = []
        for problem in ("sturm_separation", "bvp_comparison"):
            records.append(
                {
                    "result_id": result_id("A-M-Berns", "GPT-5.5 Codex", problem, 1),
                    "problem_id": problem,
                    "statement_revision": 1,
                    "declared_model": "GPT-5.5 Codex",
                    "accepted_at": "2026-05-07T07:05:49Z",
                    "benchmark_commit": BENCHMARK,
                    "intake": {"kind": "issue", "issue_number": 144},
                    "submission": {
                        "kind": "github_repo",
                        "repo": "A-M-Berns/lean-eval-submissions",
                        "ref": SOURCE,
                        "public": True,
                    },
                    "production_metadata": {},
                }
            )
        document = {"schema_version": 2, "user": "A-M-Berns", "results": records}
        (self.results_root / "a-m-berns.json").write_bytes(
            canonical_file_bytes(document)
        )
        (self.results_root / ".gitkeep").write_bytes(b"")

        self.inventory = inventory(self.results_root, SOURCE_COMMIT)
        self.inventory_raw = canonical(self.inventory)
        self.requests = prepare(
            self.inventory,
            hashlib.sha256(self.inventory_raw).hexdigest(),
            self.results_root,
        )
        self.requests_raw = canonical(self.requests)
        self.workflow_registry, self.workflow_digest = registry_bytes()
        self.workflow_raw = canonical(self.workflow_registry)
        evidence = resolve(
            self.requests,
            hashlib.sha256(self.requests_raw).hexdigest(),
            FakeClient(),
            self.workflow_registry,
            self.workflow_digest,
        )
        evidence_raw = canonical(evidence)
        self.aggregate = aggregate(
            self.requests,
            hashlib.sha256(self.requests_raw).hexdigest(),
            self.workflow_registry,
            self.workflow_digest,
            [(hashlib.sha256(evidence_raw).hexdigest(), evidence)],
        )
        self.aggregate_raw = canonical(self.aggregate)
        self.legacy_registry, self.legacy_digest = adjudication_bytes()
        self.legacy_raw = canonical(self.legacy_registry)
        legacy_evidence = resolve(
            self.requests,
            hashlib.sha256(self.requests_raw).hexdigest(),
            FakeClient(),
            self.workflow_registry,
            self.workflow_digest,
            self.legacy_registry,
            self.legacy_digest,
        )
        legacy_evidence_raw = canonical(legacy_evidence)
        self.legacy_aggregate = aggregate(
            self.requests,
            hashlib.sha256(self.requests_raw).hexdigest(),
            self.workflow_registry,
            self.workflow_digest,
            [
                (
                    hashlib.sha256(legacy_evidence_raw).hexdigest(),
                    legacy_evidence,
                )
            ],
            self.legacy_registry,
            self.legacy_digest,
        )
        self.legacy_aggregate_raw = canonical(self.legacy_aggregate)
        self.toolchains = build_registry(
            resolved_benchmark_commits(self.requests, self.aggregate),
            lambda _commit: b"leanprover/lean4:v4.30.0-rc2\n\n",
        )
        self.toolchains_raw = canonical(self.toolchains)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, **changes):
        arguments = {
            "inventory_value": self.inventory,
            "inventory_raw": self.inventory_raw,
            "requests": self.requests,
            "requests_raw": self.requests_raw,
            "aggregate": self.aggregate,
            "aggregate_raw": self.aggregate_raw,
            "workflow_registry": self.workflow_registry,
            "workflow_registry_raw": self.workflow_raw,
            "toolchain_registry": self.toolchains,
            "toolchain_registry_raw": self.toolchains_raw,
            "results_root": self.results_root,
        }
        arguments.update(changes)
        return build_plan(**arguments)

    def test_resolved_evidence_becomes_exact_blocked_replay_seeds(self) -> None:
        output = self.build()
        self.assertEqual(output["resolved_request_count"], 1)
        self.assertEqual(output["resolved_result_count"], 2)
        self.assertEqual(output["pending_request_count"], 0)
        self.assertEqual(output["activation_status"], "blocked")
        self.assertEqual(output["execution_profile_status"], "unresolved")
        request = output["requests"][0]
        self.assertEqual(request["owner_login"], "a-m-berns")
        self.assertEqual(request["historical_accepted_at"], "2026-05-07T07:05:49Z")
        self.assertEqual(request["source"]["commit"], SOURCE)
        self.assertEqual(request["benchmark"]["commit"], BENCHMARK)
        self.assertEqual(
            request["benchmark"]["toolchain"], "leanprover/lean4:v4.30.0-rc2"
        )
        self.assertEqual(
            request["historical_evaluation"]["workflow_run_id"], 25480965896
        )
        self.assertEqual(
            {item["result_id"] for item in request["results"]},
            {
                record["result_id"]
                for record in json.loads(
                    (self.results_root / "a-m-berns.json").read_text()
                )["results"]
            },
        )
        for result in request["results"]:
            self.assertEqual(result["owner_login"], "a-m-berns")
            self.assertEqual(result["results_commit"], SOURCE_COMMIT)
            self.assertRegex(result["result_tree_digest"], r"^[0-9a-f]{64}$")

    def test_output_is_byte_deterministic(self) -> None:
        self.assertEqual(canonical(self.build()), canonical(self.build()))

    def test_new_aggregate_requires_and_binds_the_exact_legacy_registry(self) -> None:
        changes = {
            "aggregate": self.legacy_aggregate,
            "aggregate_raw": self.legacy_aggregate_raw,
        }
        with self.assertRaisesRegex(PublicReplayPlanError, "mode does not match"):
            self.build(**changes)
        output = self.build(
            **changes,
            legacy_adjudication_registry=self.legacy_registry,
            legacy_adjudication_registry_raw=self.legacy_raw,
        )
        self.assertEqual(output["resolved_request_count"], 1)
        changed_raw = self.legacy_raw + b"\n"
        with self.assertRaisesRegex(PublicReplayPlanError, "identity is invalid"):
            self.build(
                **changes,
                legacy_adjudication_registry=self.legacy_registry,
                legacy_adjudication_registry_raw=changed_raw,
            )

    def test_toolchain_cli_requires_registry_for_new_aggregate(self) -> None:
        inputs = {
            "requests.json": self.requests_raw,
            "aggregate.json": self.legacy_aggregate_raw,
            "workflow.json": self.workflow_raw,
            "legacy.json": self.legacy_raw,
        }
        for name, raw in inputs.items():
            (self.root / name).write_bytes(raw)
        common = [
            "build_public_replay_toolchain_registry.py",
            "--requests",
            str(self.root / "requests.json"),
            "--evidence-aggregate",
            str(self.root / "aggregate.json"),
            "--workflow-registry",
            str(self.root / "workflow.json"),
            "--benchmark-repository",
            str(self.root),
        ]
        with mock.patch(
            "build_public_replay_toolchain_registry._git_reader",
            return_value=lambda _commit: b"leanprover/lean4:v4.30.0-rc2\n",
        ), mock.patch.object(
            sys,
            "argv",
            common + ["--output", str(self.root / "missing-registry.json")],
        ):
            self.assertEqual(build_toolchain_main(), 1)
        with mock.patch(
            "build_public_replay_toolchain_registry._git_reader",
            return_value=lambda _commit: b"leanprover/lean4:v4.30.0-rc2\n",
        ), mock.patch.object(
            sys,
            "argv",
            common
            + [
                "--legacy-adjudication-registry",
                str(self.root / "legacy.json"),
                "--output",
                str(self.root / "toolchains-cli.json"),
            ],
        ):
            self.assertEqual(build_toolchain_main(), 0)
        self.assertTrue((self.root / "toolchains-cli.json").is_file())

    def test_changed_snapshot_or_evidence_binding_fails_closed(self) -> None:
        changed = json.loads((self.results_root / "a-m-berns.json").read_text())
        changed["results"][0]["production_metadata"] = {"notes": "changed"}
        (self.results_root / "a-m-berns.json").write_bytes(
            canonical_file_bytes(changed)
        )
        with self.assertRaisesRegex(PublicReplayPlanError, "inventory does not equal"):
            self.build()

    def test_missing_and_extra_toolchains_fail_closed(self) -> None:
        missing = copy.deepcopy(self.toolchains)
        missing["commits"] = []
        missing["commit_count"] = 0
        with self.assertRaisesRegex(PublicReplayPlanError, "identity is invalid"):
            self.build(
                toolchain_registry=missing,
                toolchain_registry_raw=canonical(missing),
            )
        extra = copy.deepcopy(self.toolchains)
        extra["commits"].append(
            {
                "benchmark_commit": "f" * 40,
                "lean_toolchain": "leanprover/lean4:v4.33.0",
                "lean_toolchain_blob_sha256": "e" * 64,
            }
        )
        extra["commits"].sort(key=lambda item: item["benchmark_commit"])
        extra["commit_count"] = 2
        with self.assertRaisesRegex(PublicReplayPlanError, "unused or missing"):
            self.build(
                toolchain_registry=extra,
                toolchain_registry_raw=canonical(extra),
            )

    def test_toolchain_registry_rejects_ambiguous_content(self) -> None:
        with self.assertRaisesRegex(ToolchainRegistryError, "nonempty"):
            build_registry([], lambda _commit: b"leanprover/lean4:v4.30.0\n")
        with self.assertRaisesRegex(ToolchainRegistryError, "LF-only"):
            build_registry(
                [BENCHMARK], lambda _commit: b"leanprover/lean4:v4.30.0\nother\n"
            )
        with self.assertRaisesRegex(ToolchainRegistryError, "exact Lean release"):
            build_registry([BENCHMARK], lambda _commit: b"leanprover/lean4:nightly\n")

    def test_nonresolved_requests_never_enter_plan(self) -> None:
        pending = copy.deepcopy(self.aggregate)
        resolution = pending["resolutions"][0]
        resolution["status"] = "evidence_missing"
        resolution["selected_issue_repository"] = None
        resolution["candidates"] = [
            {"issue_repository": "leanprover/lean-eval", "status": "issue_not_found"},
            {
                "issue_repository": "leanprover/lean-eval-submissions",
                "status": "issue_not_found",
            },
        ]
        pending["resolved_count"] = 0
        pending["evidence_missing_count"] = 1
        pending["pending_count"] = 1
        pending["shards"][0]["resolved_count"] = 0
        pending["shards"][0]["pending_count"] = 1
        with self.assertRaisesRegex(ToolchainRegistryError, "no resolved"):
            # The toolchain stage deliberately refuses to create an empty executable seed set.
            resolved_benchmark_commits(self.requests, pending)


class PublicReplayPlanSchemaTests(unittest.TestCase):
    def test_schemas_are_closed_draft_2020_contracts(self) -> None:
        for name in (
            "historical-public-replay-toolchains-v1.schema.json",
            "historical-public-replay-plan-v1.schema.json",
        ):
            schema = json.loads((ROOT / "schemas" / name).read_text())
            self.assertEqual(
                schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
            )
            self.assertIs(schema["additionalProperties"], False)
        plan = json.loads(
            (ROOT / "schemas/historical-public-replay-plan-v1.schema.json").read_text()
        )
        self.assertIs(plan["$defs"]["request"]["additionalProperties"], False)
        self.assertIs(plan["$defs"]["result"]["additionalProperties"], False)


if __name__ == "__main__":
    unittest.main()
