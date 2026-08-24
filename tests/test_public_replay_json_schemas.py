from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import unittest

import jsonschema
from referencing import Registry, Resource

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aggregate_public_replay_github_evidence import (
    aggregate,
    canonical_document_bytes,
    validate_aggregate,
)
from resolve_public_replay_github_evidence import (
    SOURCE_PROBE_REASON_CODES,
    shard_requests,
    validate_evidence,
    validate_requests,
)
from tests.test_resolve_public_replay_github_evidence import (
    FakeClient,
    refresh_request_id,
    registry_bytes,
    request_value,
    resolve,
)


SCHEMA_NAMES = {
    "requests": "public-replay-resolution-requests-v1.schema.json",
    "registry": "public-replay-workflow-definitions-v1.schema.json",
    "evidence": "public-replay-github-evidence-v1.schema.json",
    "aggregate": "public-replay-github-evidence-aggregate-v1.schema.json",
}


class PublicReplayJsonSchemaParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = {
            name: json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
            for name, filename in SCHEMA_NAMES.items()
        }
        resources = [
            (schema["$id"], Resource.from_contents(schema))
            for schema in cls.schemas.values()
        ]
        cls.registry = Registry().with_resources(resources)
        for schema in cls.schemas.values():
            jsonschema.Draft202012Validator.check_schema(schema)

    def validate(self, schema_name: str, value: object) -> None:
        jsonschema.Draft202012Validator(
            self.schemas[schema_name], registry=self.registry
        ).validate(value)

    def test_source_probe_reason_codes_match_runtime(self) -> None:
        matched_source = next(
            branch
            for branch in self.schemas["evidence"]["$defs"]["candidate"]["oneOf"]
            if "matched_source_indeterminate"
            in branch.get("properties", {}).get("status", {}).get("enum", [])
        )
        schema_codes = matched_source["properties"]["source_probe_reason_code"][
            "enum"
        ]
        self.assertEqual(set(schema_codes), SOURCE_PROBE_REASON_CODES)

    def artifacts(self) -> tuple[dict, dict, dict, dict]:
        requests = request_value()
        requests_raw = (json.dumps(requests, indent=2, sort_keys=True) + "\n").encode()
        requests_digest = hashlib.sha256(requests_raw).hexdigest()
        workflow_registry, registry_digest = registry_bytes()
        evidence = resolve(requests, requests_digest, FakeClient())
        evidence_digest = hashlib.sha256(canonical_document_bytes(evidence)).hexdigest()
        aggregated = aggregate(
            requests,
            requests_digest,
            workflow_registry,
            registry_digest,
            [(evidence_digest, evidence)],
        )
        return requests, workflow_registry, evidence, aggregated

    def test_generated_artifacts_validate_against_published_schemas(self) -> None:
        artifacts = self.artifacts()
        for name, value in zip(SCHEMA_NAMES, artifacts, strict=True):
            with self.subTest(schema=name):
                self.validate(name, value)
        requests, workflow_registry, evidence, aggregated = artifacts
        requests_raw = (json.dumps(requests, indent=2, sort_keys=True) + "\n").encode()
        requests_digest = hashlib.sha256(requests_raw).hexdigest()
        _, registry_digest = registry_bytes(workflow_registry)
        validate_requests(requests)
        validate_evidence(evidence, requests, workflow_registry, registry_digest)
        validate_aggregate(
            aggregated,
            requests,
            requests_digest,
            workflow_registry,
            registry_digest,
        )

    def test_empty_shard_is_producible_and_schema_valid(self) -> None:
        requests = request_value()
        request = requests["requests"][0]
        requests_digest = hashlib.sha256(
            (json.dumps(requests, indent=2, sort_keys=True) + "\n").encode()
        ).hexdigest()
        occupied = next(
            index
            for index in range(2)
            if shard_requests(requests["requests"], index, 2)
        )
        workflow_registry, registry_digest = registry_bytes()
        empty = resolve(
            requests,
            requests_digest,
            FakeClient(),
            shard_index=1 - occupied,
            shard_count=2,
        )
        self.assertEqual(empty["resolutions"], [])
        self.assertEqual(empty["workflow_definition_registry_sha256"], registry_digest)
        self.validate("evidence", empty)

        occupied_evidence = resolve(
            requests,
            requests_digest,
            FakeClient(),
            shard_index=occupied,
            shard_count=2,
        )
        shards = [empty, occupied_evidence]
        shards.sort(key=lambda value: value["shard_index"])
        aggregated = aggregate(
            requests,
            requests_digest,
            workflow_registry,
            registry_digest,
            [
                (
                    hashlib.sha256(canonical_document_bytes(value)).hexdigest(),
                    value,
                )
                for value in shards
            ],
        )
        self.assertIn(0, [item["request_count"] for item in aggregated["shards"]])
        self.validate("aggregate", aggregated)

    def test_schema_and_runtime_are_both_closed(self) -> None:
        requests, _, evidence, aggregated = self.artifacts()
        for name, value in (
            ("requests", requests),
            ("evidence", evidence),
            ("aggregate", aggregated),
        ):
            changed = copy.deepcopy(value)
            changed["unexpected"] = True
            with self.subTest(schema=name), self.assertRaises(
                jsonschema.ValidationError
            ):
                self.validate(name, changed)

    def test_repository_dot_segments_fail_schema_and_runtime(self) -> None:
        requests = request_value()
        requests["requests"][0]["source"]["repository"] = "owner/.."
        with self.assertRaises(jsonschema.ValidationError):
            self.validate("requests", requests)
        with self.assertRaisesRegex(ValueError, "repository"):
            validate_requests(requests)

    def test_gist_source_name_is_variable_length_lowercase_hex(self) -> None:
        for gist_id in ("a", "a" * 20, "a" * 32, "a" * 64):
            with self.subTest(gist_id=gist_id):
                requests = request_value()
                requests["requests"][0]["source"] = {
                    "kind": "gist",
                    "repository": f"A-M-Berns/{gist_id}",
                    "commit": "2" * 40,
                    "visibility": "public",
                }
                refresh_request_id(requests)
                self.validate("requests", requests)
                validate_requests(requests)

        for gist_id in ("", "G", "A" * 32, "not-a-gist"):
            with self.subTest(gist_id=gist_id):
                requests = request_value()
                requests["requests"][0]["source"] = {
                    "kind": "gist",
                    "repository": f"A-M-Berns/{gist_id}",
                    "commit": "2" * 40,
                    "visibility": "public",
                }
                refresh_request_id(requests)
                with self.assertRaises(jsonschema.ValidationError):
                    self.validate("requests", requests)
                with self.assertRaisesRegex(ValueError, "source identity|repository"):
                    validate_requests(requests)


if __name__ == "__main__":
    unittest.main()
