import copy
import hashlib
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aggregate_public_replay_github_evidence import (  # noqa: E402
    AggregationError,
    aggregate,
    canonical_document_bytes,
)
from resolve_public_replay_github_evidence import (  # noqa: E402
    resolve,
)
from tests.test_resolve_public_replay_github_evidence import (  # noqa: E402
    FakeClient,
    refresh_request_id,
    request_value,
)


def registry() -> tuple[dict, str]:
    value = json.loads(
        (
            ROOT / "configuration/public-replay-workflow-definitions-v1.json"
        ).read_text()
    )
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    return value, hashlib.sha256(raw).hexdigest()


def two_partition_requests() -> dict:
    first = request_value()["requests"][0]
    requests = [first]
    wanted_parity = 1 - int(first["request_id"].removeprefix("prr_"), 16) % 2
    for issue_number in range(145, 1000):
        candidate = copy.deepcopy(first)
        candidate["issue_number"] = issue_number
        candidate["results"] = [
            {
                "result_id": "r2_" + format(issue_number, "064x"),
                "owner": candidate["owner"],
                "problem_id": f"problem_{issue_number}",
                "statement_revision": 1,
            }
        ]
        wrapper = {"requests": [candidate]}
        refresh_request_id(wrapper)
        if int(candidate["request_id"].removeprefix("prr_"), 16) % 2 == wanted_parity:
            requests.append(candidate)
            break
    requests.sort(key=lambda item: item["request_id"])
    return {
        "schema_version": 1,
        "kind": "historical_public_replay_resolution_requests",
        "source_repository": "leanprover/lean-eval-submissions",
        "source_commit": "4" * 40,
        "inventory_sha256": "5" * 64,
        "request_count": 2,
        "result_count": sum(len(item["results"]) for item in requests),
        "requests": requests,
    }


def empty_shard(requests: dict, requests_digest: str, registry_digest: str, index: int) -> dict:
    selected = [
        request
        for request in requests["requests"]
        if int(request["request_id"].removeprefix("prr_"), 16) % 2 == index
    ]
    resolutions = [
        {
            "request_id": request["request_id"],
            "status": "evidence_missing",
            "selected_issue_repository": None,
            "candidates": [
                {
                    "issue_repository": "leanprover/lean-eval",
                    "status": "issue_not_found",
                },
                {
                    "issue_repository": "leanprover/lean-eval-submissions",
                    "status": "issue_not_found",
                },
            ],
        }
        for request in selected
    ]
    return {
        "schema_version": 1,
        "kind": "historical_public_replay_github_evidence",
        "source_repository": requests["source_repository"],
        "source_commit": requests["source_commit"],
        "inventory_sha256": requests["inventory_sha256"],
        "resolution_requests_sha256": requests_digest,
        "workflow_definition_registry_sha256": registry_digest,
        "request_count": requests["request_count"],
        "result_count": requests["result_count"],
        "shard_index": index,
        "shard_count": 2,
        "shard_request_count": len(selected),
        "shard_result_count": sum(len(item["results"]) for item in selected),
        "resolved_count": 0,
        "source_unavailable_count": 0,
        "source_indeterminate_count": 0,
        "pending_count": len(selected),
        "resolutions": resolutions,
    }


class AggregatePublicReplayEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requests = two_partition_requests()
        self.requests_raw = (
            json.dumps(self.requests, indent=2, sort_keys=True) + "\n"
        ).encode()
        self.requests_digest = hashlib.sha256(self.requests_raw).hexdigest()
        self.registry, self.registry_digest = registry()
        self.shards = [
            empty_shard(self.requests, self.requests_digest, self.registry_digest, i)
            for i in range(2)
        ]
        self.evidence = [
            (hashlib.sha256(canonical_document_bytes(value)).hexdigest(), value)
            for value in self.shards
        ]

    def test_complete_exact_shards_aggregate_deterministically(self) -> None:
        output = aggregate(
            self.requests,
            self.requests_digest,
            self.registry,
            self.registry_digest,
            self.evidence,
        )
        self.assertEqual(output["shard_count"], 2)
        self.assertEqual(output["request_count"], 2)
        self.assertEqual(output["pending_count"], 2)
        self.assertEqual(output["evidence_missing_count"], 2)
        self.assertEqual(
            [item["request_id"] for item in output["resolutions"]],
            [item["request_id"] for item in self.requests["requests"]],
        )

    def test_missing_duplicate_and_mixed_identity_shards_fail_closed(self) -> None:
        with self.assertRaisesRegex(AggregationError, "indices are incomplete"):
            aggregate(
                self.requests,
                self.requests_digest,
                self.registry,
                self.registry_digest,
                self.evidence[:1],
            )
        with self.assertRaisesRegex(AggregationError, "index is duplicated"):
            aggregate(
                self.requests,
                self.requests_digest,
                self.registry,
                self.registry_digest,
                [self.evidence[0], self.evidence[0]],
            )
        mixed = copy.deepcopy(self.shards[1])
        mixed["resolution_requests_sha256"] = "9" * 64
        with self.assertRaisesRegex(AggregationError, "identity differs"):
            aggregate(
                self.requests,
                self.requests_digest,
                self.registry,
                self.registry_digest,
                [
                    self.evidence[0],
                    (hashlib.sha256(canonical_document_bytes(mixed)).hexdigest(), mixed),
                ],
            )

    def test_source_unavailable_remains_pending(self) -> None:
        requests = request_value()
        raw = (json.dumps(requests, indent=2, sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        evidence = resolve(
            requests,
            digest,
            FakeClient(source_available=False),
            self.registry,
            self.registry_digest,
        )
        output = aggregate(
            requests,
            digest,
            self.registry,
            self.registry_digest,
            [
                (
                    hashlib.sha256(canonical_document_bytes(evidence)).hexdigest(),
                    evidence,
                )
            ],
        )
        self.assertEqual(output["source_unavailable_count"], 1)
        self.assertEqual(output["resolved_count"], 0)
        self.assertEqual(output["pending_count"], 1)

    def test_published_aggregate_schema_is_closed(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "schemas/public-replay-github-evidence-aggregate-v1.schema.json"
            ).read_text()
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIs(schema["additionalProperties"], False)
        self.assertIs(schema["$defs"]["shard"]["additionalProperties"], False)


if __name__ == "__main__":
    unittest.main()
