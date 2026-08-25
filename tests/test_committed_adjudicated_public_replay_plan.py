from __future__ import annotations

import hashlib
import json
import pathlib
import stat
import sys
import unittest
from collections import Counter

import jsonschema

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aggregate_public_replay_github_evidence import canonical_document_bytes
from prepare_historical_replay_profile_matrix import validate_plan
from prepare_public_replay_plan import validate_toolchain_registry

PLAN_DIGEST = "d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e"
TOOLCHAIN_DIGEST = "4f2f3737d79e6abd6c169ebdde3f2218157d8f6c482a85ad2026821a4b8e81a0"
PLAN = ROOT / "evidence" / "public-replay" / "plans" / f"{PLAN_DIGEST}.json"
TOOLCHAINS = (
    ROOT
    / "evidence"
    / "public-replay"
    / "toolchains"
    / f"{TOOLCHAIN_DIGEST}.json"
)


class CommittedAdjudicatedPublicReplayPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_raw = PLAN.read_bytes()
        cls.toolchain_raw = TOOLCHAINS.read_bytes()
        cls.plan = json.loads(cls.plan_raw)
        cls.toolchains = json.loads(cls.toolchain_raw)

    def test_artifacts_are_regular_canonical_digest_named_json(self) -> None:
        for path, raw, value, digest in (
            (PLAN, self.plan_raw, self.plan, PLAN_DIGEST),
            (TOOLCHAINS, self.toolchain_raw, self.toolchains, TOOLCHAIN_DIGEST),
        ):
            self.assertTrue(stat.S_ISREG(path.stat(follow_symlinks=False).st_mode))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            self.assertEqual(canonical_document_bytes(value), raw)

    def test_closed_schemas_and_producer_validation_pass(self) -> None:
        for value, schema_name in (
            (self.plan, "historical-public-replay-plan-v1.schema.json"),
            (
                self.toolchains,
                "historical-public-replay-toolchains-v1.schema.json",
            ),
        ):
            schema = json.loads(
                (ROOT / "schemas" / schema_name).read_text(encoding="utf-8")
            )
            jsonschema.Draft202012Validator(schema).validate(value)
        decoded = validate_toolchain_registry(self.toolchains)
        validate_plan(self.plan, decoded, TOOLCHAIN_DIGEST)

    def test_plan_binds_the_complete_adjudicated_replayable_set(self) -> None:
        self.assertEqual(
            self.plan["github_evidence_aggregate_sha256"],
            "ba816b52558cf77bd202618f820ffa6294ca2167698c94ab1096a39375c50212",
        )
        self.assertEqual(
            self.plan["source_commit"],
            "ba5f5784427621f8b9be7396dd45a0938792707d",
        )
        self.assertEqual(self.plan["benchmark_toolchain_registry_sha256"], TOOLCHAIN_DIGEST)
        self.assertEqual(self.plan["resolved_request_count"], 128)
        self.assertEqual(self.plan["resolved_result_count"], 194)
        self.assertEqual(self.plan["pending_request_count"], 187)
        self.assertEqual(
            Counter(request["source"]["kind"] for request in self.plan["requests"]),
            {"github_repo": 69, "gist": 59},
        )
        self.assertEqual(self.toolchains["commit_count"], 35)
        self.assertEqual(
            len({entry["lean_toolchain"] for entry in self.toolchains["commits"]}),
            5,
        )

    def test_artifacts_remain_blocked_and_source_free(self) -> None:
        self.assertEqual(self.plan["activation_status"], "blocked")
        self.assertEqual(
            self.plan["activation_requirement"],
            "legacy_public_result_replay_authority_v1",
        )
        self.assertEqual(self.plan["execution_profile_status"], "unresolved")
        encoded = (self.plan_raw + self.toolchain_raw).decode("utf-8").lower()
        for forbidden in (
            "submission_source",
            "source_bytes",
            "ciphertext",
            "credential",
            "archive_path",
            "aws_access_key",
        ):
            self.assertNotIn(forbidden, encoded)


if __name__ == "__main__":
    unittest.main()
