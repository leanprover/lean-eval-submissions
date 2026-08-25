import copy
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

import jsonschema
from referencing import Registry, Resource

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aggregate_public_replay_github_evidence import (  # noqa: E402
    aggregate,
    canonical_document_bytes,
)
from inventory_historical_replay import inventory  # noqa: E402
from prepare_public_replay_resolution import prepare  # noqa: E402
from prepare_public_replay_unavailability import (  # noqa: E402
    PERMANENT_REASON,
    RATIONALE,
    UnavailabilityError,
    _compact_bytes,
    _write_candidate_bundle,
    build_candidate_bundle,
    build_candidates,
    finalize,
    validate_candidate_bundle,
    validate_candidates,
    validate_dispositions,
)
from resolve_public_replay_github_evidence import resolve  # noqa: E402
from results_schema import canonical_file_bytes, result_id  # noqa: E402

from tests.frozen_results_tree import materialize_results_tree  # noqa: E402
from tests.test_resolve_public_replay_github_evidence import (  # noqa: E402
    BENCHMARK,
    SOURCE,
    FakeClient,
    adjudication_bytes,
    registry_bytes,
)

SOURCE_COMMIT = "4" * 40


class PublicReplayUnavailabilityTests(unittest.TestCase):
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
        self.inventory_raw = canonical_document_bytes(self.inventory)
        self.requests = prepare(
            self.inventory,
            hashlib.sha256(self.inventory_raw).hexdigest(),
            self.results_root,
        )
        self.requests_raw = canonical_document_bytes(self.requests)
        self.workflow, _ = registry_bytes()
        self.workflow_raw = canonical_document_bytes(self.workflow)
        self.legacy, _ = adjudication_bytes()
        self.legacy_raw = canonical_document_bytes(self.legacy)
        evidence = resolve(
            self.requests,
            hashlib.sha256(self.requests_raw).hexdigest(),
            FakeClient(source_available=False),
            self.workflow,
            hashlib.sha256(self.workflow_raw).hexdigest(),
            self.legacy,
            hashlib.sha256(self.legacy_raw).hexdigest(),
        )
        evidence_raw = canonical_document_bytes(evidence)
        self.aggregate = aggregate(
            self.requests,
            hashlib.sha256(self.requests_raw).hexdigest(),
            self.workflow,
            hashlib.sha256(self.workflow_raw).hexdigest(),
            [(hashlib.sha256(evidence_raw).hexdigest(), evidence)],
            self.legacy,
            hashlib.sha256(self.legacy_raw).hexdigest(),
        )
        self.aggregate_raw = canonical_document_bytes(self.aggregate)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, **changes):
        arguments = {
            "inventory_value": self.inventory,
            "inventory_raw": self.inventory_raw,
            "requests_value": self.requests,
            "requests_raw": self.requests_raw,
            "aggregate": self.aggregate,
            "aggregate_raw": self.aggregate_raw,
            "workflow_registry": self.workflow,
            "workflow_registry_raw": self.workflow_raw,
            "legacy_registry": self.legacy,
            "legacy_registry_raw": self.legacy_raw,
            "results_root": self.results_root,
        }
        arguments.update(changes)
        return build_candidates(**arguments)

    def trusted_arguments(self, **changes):
        arguments = {
            "inventory_value": self.inventory,
            "inventory_raw": self.inventory_raw,
            "requests_value": self.requests,
            "requests_raw": self.requests_raw,
            "aggregate": self.aggregate,
            "aggregate_raw": self.aggregate_raw,
            "workflow_registry": self.workflow,
            "workflow_registry_raw": self.workflow_raw,
            "legacy_registry": self.legacy,
            "legacy_registry_raw": self.legacy_raw,
            "results_root": self.results_root,
        }
        arguments.update(changes)
        return arguments

    def bundle(self, **changes):
        return build_candidate_bundle(**self.trusted_arguments(**changes))

    def reviews(self, manifest, shards, decision="permanently_unavailable"):
        manifest_raw = canonical_document_bytes(manifest)
        candidates = validate_candidate_bundle(manifest, shards)
        return {
            "schema_version": 1,
            "kind": "historical_public_replay_unavailability_reviews",
            "candidate_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "reviews": [
                {
                    "request_id": item["request_id"],
                    "candidate_sha256": item["candidate_sha256"],
                    "decision": decision,
                    "reason_code": (
                        PERMANENT_REASON
                        if decision == "permanently_unavailable"
                        else None
                    ),
                    "rationale_code": (
                        RATIONALE if decision == "permanently_unavailable" else None
                    ),
                }
                for item in candidates
            ],
        }

    def finish(self, manifest, shards, reviews=None):
        if reviews is None:
            reviews = self.reviews(manifest, shards)
        return finalize(
            manifest=manifest,
            manifest_raw=canonical_document_bytes(manifest),
            shard_bytes=shards,
            review_value=reviews,
            trusted_arguments=self.trusted_arguments(),
        )

    def mutate_first_candidate(self, manifest, shards, mutate):
        manifest = copy.deepcopy(manifest)
        shards = dict(shards)
        descriptor = manifest["shards"][0]
        old_digest = descriptor["sha256"]
        shard = json.loads(shards.pop(old_digest))
        candidate = shard["candidates"][0]
        mutate(candidate)
        candidate["candidate_sha256"] = hashlib.sha256(
            canonical_document_bytes(
                {
                    key: value
                    for key, value in candidate.items()
                    if key != "candidate_sha256"
                }
            )
        ).hexdigest()
        raw = _compact_bytes(shard)
        digest = hashlib.sha256(raw).hexdigest()
        shards[digest] = raw
        descriptor["sha256"] = digest
        descriptor["byte_count"] = len(raw)
        return manifest, shards

    def test_unavailable_probe_becomes_review_required_not_terminal(self) -> None:
        output = self.build()
        self.assertEqual(output["candidate_request_count"], 1)
        self.assertEqual(output["candidate_result_count"], 2)
        self.assertEqual(output["review_status"], "required")
        self.assertFalse(output["claims"]["permanent_unavailability_decided"])
        candidate = output["candidates"][0]
        self.assertEqual(candidate["review_status"], "pending")
        self.assertEqual(candidate["proposed_reason_code"], PERMANENT_REASON)
        self.assertEqual(
            candidate["issue_candidates"][0]["status"],
            "matched_source_unavailable",
        )
        self.assertNotIn("source_bytes", canonical_document_bytes(output).decode())

    def test_candidate_output_is_deterministic(self) -> None:
        first_manifest, first_shards = self.bundle()
        second_manifest, second_shards = self.bundle()
        self.assertEqual(first_manifest, second_manifest)
        self.assertEqual(first_shards, second_shards)

    def test_verify_cli_mechanically_rederives_exact_bundle(self) -> None:
        manifest, shards = self.bundle()
        bundle = self.root / "bundle"
        manifest_path = _write_candidate_bundle(bundle, manifest, shards)
        input_values = {
            "inventory": self.inventory,
            "resolution-requests": self.requests,
            "aggregate": self.aggregate,
            "workflow-registry": self.workflow,
            "legacy-adjudication-registry": self.legacy,
        }
        arguments = []
        for option, value in input_values.items():
            path = self.root / f"{option}.json"
            path.write_bytes(canonical_document_bytes(value))
            arguments.extend((f"--{option}", str(path)))
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/prepare_public_replay_unavailability.py"),
                "verify",
                *arguments,
                "--results-root",
                str(self.results_root),
                "--candidate-manifest",
                str(manifest_path),
                "--candidate-shards",
                str(bundle / "shards"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(), completed.stdout
        )

    def test_verify_disposition_cli_rederives_exact_terminal_bytes(self) -> None:
        manifest, shards = self.bundle()
        bundle = self.root / "terminal-bundle"
        manifest_path = _write_candidate_bundle(bundle, manifest, shards)
        reviews = self.reviews(manifest, shards)
        review_path = self.root / "reviews.json"
        review_path.write_bytes(canonical_document_bytes(reviews))
        disposition_path = self.root / "dispositions.json"
        disposition_path.write_bytes(
            canonical_document_bytes(self.finish(manifest, shards, reviews))
        )
        input_values = {
            "inventory": self.inventory,
            "resolution-requests": self.requests,
            "aggregate": self.aggregate,
            "workflow-registry": self.workflow,
            "legacy-adjudication-registry": self.legacy,
        }
        arguments = []
        for option, value in input_values.items():
            path = self.root / f"terminal-{option}.json"
            path.write_bytes(canonical_document_bytes(value))
            arguments.extend((f"--{option}", str(path)))
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/prepare_public_replay_unavailability.py"),
                "verify-disposition",
                *arguments,
                "--results-root",
                str(self.results_root),
                "--candidate-manifest",
                str(manifest_path),
                "--candidate-shards",
                str(bundle / "shards"),
                "--reviews",
                str(review_path),
                "--dispositions",
                str(disposition_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            hashlib.sha256(disposition_path.read_bytes()).hexdigest(),
            completed.stdout,
        )

    def test_exact_results_snapshot_is_required(self) -> None:
        changed = copy.deepcopy(self.inventory)
        changed["entries"][0]["owner"] = "forged"
        with self.assertRaisesRegex(UnavailabilityError, "exact results snapshot"):
            self.build(
                inventory_value=changed,
                inventory_raw=canonical_document_bytes(changed),
            )

    def test_complete_reviews_still_do_not_authorize_state(self) -> None:
        manifest, shards = self.bundle()
        output = self.finish(manifest, shards)
        self.assertEqual(output["review_status"], "complete")
        self.assertEqual(output["request_count"], 1)
        self.assertEqual(output["result_count"], 2)
        self.assertEqual(output["candidate_request_count"], 1)
        self.assertEqual(output["candidate_result_count"], 2)
        self.assertEqual(output["deferred_result_count"], 0)
        self.assertFalse(output["claims"]["state_append_authorized"])
        self.assertFalse(output["claims"]["corpus_complete"])
        self.assertTrue(output["claims"]["unavailability_review_complete"])

    def test_deferred_review_keeps_disposition_incomplete(self) -> None:
        manifest, shards = self.bundle()
        output = self.finish(
            manifest, shards, self.reviews(manifest, shards, decision="defer")
        )
        self.assertEqual(output["review_status"], "incomplete")
        self.assertEqual(output["deferred_request_count"], 1)
        self.assertEqual(output["deferred_result_count"], 2)
        self.assertEqual(output["dispositions"], [])

    def test_review_must_bind_every_exact_candidate(self) -> None:
        manifest, shards = self.bundle()
        for mutate, message in (
            (
                lambda review: review.__setitem__(
                    "candidate_manifest_sha256", "0" * 64
                ),
                "identity",
            ),
            (
                lambda review: review["reviews"][0].__setitem__(
                    "candidate_sha256", "0" * 64
                ),
                "digest differs",
            ),
            (
                lambda review: review["reviews"][0].__setitem__(
                    "reason_code", "temporary_provider_failure"
                ),
                "reason is not registered",
            ),
            (lambda review: review["reviews"].clear(), "cover every"),
        ):
            with self.subTest(message=message):
                review = self.reviews(manifest, shards)
                mutate(review)
                with self.assertRaisesRegex(UnavailabilityError, message):
                    self.finish(manifest, shards, review)

    def test_finalize_rederives_every_enriched_candidate_binding(self) -> None:
        mutations = {
            "issue identity": lambda candidate: candidate["issue"].__setitem__(
                "identity_sha256", "0" * 64
            ),
            "workflow identity": lambda candidate: candidate[
                "historical_evaluation"
            ].__setitem__("workflow_run_identity_sha256", "0" * 64),
            "result file binding": lambda candidate: candidate["results"][0].update(
                results_path="results/forged.json",
                result_file_sha256="0" * 64,
                result_tree_digest="1" * 64,
            ),
            "source identity": lambda candidate: candidate["source"].__setitem__(
                "repository", "forged/source"
            ),
            "acceptance time": lambda candidate: candidate.__setitem__(
                "historical_accepted_at", "not-a-timestamp"
            ),
            "GitHub evidence": lambda candidate: candidate.__setitem__(
                "github_resolution_sha256", "0" * 64
            ),
        }
        original_manifest, original_shards = self.bundle()
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                manifest, shards = self.mutate_first_candidate(
                    original_manifest, original_shards, mutate
                )
                reviews = self.reviews(manifest, shards)
                with self.assertRaisesRegex(
                    UnavailabilityError, "differs from trusted frozen inputs"
                ):
                    self.finish(manifest, shards, reviews)

    def test_schemas_accept_exact_candidates_reviews_and_dispositions(self) -> None:
        names = (
            "public-replay-github-evidence-v1.schema.json",
            "public-replay-unavailability-candidates-v1.schema.json",
            "public-replay-unavailability-candidate-shard-v1.schema.json",
            "public-replay-unavailability-reviews-v1.schema.json",
            "public-replay-unavailability-dispositions-v1.schema.json",
        )
        schemas = [
            json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            for name in names
        ]
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in schemas
        )
        for schema in schemas:
            jsonschema.Draft202012Validator.check_schema(schema)
        manifest, shards = self.bundle()
        reviews = self.reviews(manifest, shards)
        dispositions = self.finish(manifest, shards, reviews)
        values = [manifest]
        values.extend(json.loads(raw) for raw in shards.values())
        values.extend((reviews, dispositions))
        value_schemas = [schemas[1]]
        value_schemas.extend([schemas[2]] * len(shards))
        value_schemas.extend((schemas[3], schemas[4]))
        for schema, value in zip(value_schemas, values):
            jsonschema.Draft202012Validator(
                schema, registry=registry, format_checker=jsonschema.FormatChecker()
            ).validate(value)

    def test_committed_candidate_evidence_is_exact_and_pending(self) -> None:
        digest = "0177bec519a803e52652368572ec06b5bcdd3fdc3591c06e2e25b14cf5ff725e"
        bundle = ROOT / "evidence/public-replay/unavailability-candidate-bundle-v1"
        path = bundle / f"{digest}.json"
        raw = path.read_bytes()
        manifest = json.loads(raw)
        shard_bytes = {
            shard_path.stem: shard_path.read_bytes()
            for shard_path in (bundle / "shards").iterdir()
        }
        self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        self.assertLess(len(raw), 500_000)
        self.assertTrue(all(len(shard) < 500_000 for shard in shard_bytes.values()))
        self.assertEqual(manifest["candidate_request_count"], 187)
        self.assertEqual(manifest["candidate_result_count"], 439)
        self.assertEqual(manifest["review_status"], "required")
        self.assertFalse(manifest["claims"]["permanent_unavailability_decided"])
        candidates = validate_candidate_bundle(manifest, shard_bytes)
        self.assertEqual(len(candidates), 187)
        self.assertEqual(
            [item["request_id"] for item in candidates],
            sorted(item["request_id"] for item in candidates),
        )

    def test_committed_bundle_rederives_from_frozen_inputs(self) -> None:
        source_commit = "ba5f5784427621f8b9be7396dd45a0938792707d"
        bundle = ROOT / "evidence/public-replay/unavailability-candidate-bundle-v1"
        manifest_path = next(path for path in bundle.glob("*.json"))
        with tempfile.TemporaryDirectory() as directory:
            results_root = materialize_results_tree(
                source_commit, pathlib.Path(directory)
            )
            inventory_value = inventory(results_root, source_commit)
            inventory_raw = canonical_document_bytes(inventory_value)
            requests_value = prepare(
                inventory_value,
                hashlib.sha256(inventory_raw).hexdigest(),
                results_root,
            )
            requests_raw = canonical_document_bytes(requests_value)

            def load(relative):
                raw = (ROOT / relative).read_bytes()
                return json.loads(raw), raw

            aggregate, aggregate_raw = load(
                "evidence/historical-public-replay-github-evidence-ba5f578.json"
            )
            workflow, workflow_raw = load(
                "configuration/public-replay-workflow-definitions-v1.json"
            )
            legacy, legacy_raw = load(
                "configuration/public-replay-legacy-adjudications-v1.json"
            )
            manifest, shards = build_candidate_bundle(
                inventory_value=inventory_value,
                inventory_raw=inventory_raw,
                requests_value=requests_value,
                requests_raw=requests_raw,
                aggregate=aggregate,
                aggregate_raw=aggregate_raw,
                workflow_registry=workflow,
                workflow_registry_raw=workflow_raw,
                legacy_registry=legacy,
                legacy_registry_raw=legacy_raw,
                results_root=results_root,
            )
        self.assertEqual(canonical_document_bytes(manifest), manifest_path.read_bytes())
        self.assertEqual(
            shards,
            {path.stem: path.read_bytes() for path in (bundle / "shards").iterdir()},
        )

    def test_candidate_validator_revalidates_counts_and_order(self) -> None:
        def duplicate_candidate(value):
            value["candidates"].append(copy.deepcopy(value["candidates"][0]))
            value["candidate_request_count"] = 2
            value["candidate_result_count"] = 4

        for mutate, message in (
            (
                lambda value: value.__setitem__("candidate_result_count", 999),
                "counters",
            ),
            (duplicate_candidate, "uniquely sorted"),
        ):
            with self.subTest(message=message):
                candidates = self.build()
                mutate(candidates)
                with self.assertRaisesRegex(UnavailabilityError, message):
                    validate_candidates(candidates)

    def test_candidate_validator_rejects_two_selected_issue_repositories(self) -> None:
        candidates = self.build()
        candidate = candidates["candidates"][0]
        candidate["issue_candidates"][0]["status"] = "matched_source_unavailable"
        candidate["issue_candidates"][1]["status"] = "matched_source_unavailable"
        candidate["candidate_sha256"] = hashlib.sha256(
            canonical_document_bytes(
                {
                    key: value
                    for key, value in candidate.items()
                    if key != "candidate_sha256"
                }
            )
        ).hexdigest()
        with self.assertRaisesRegex(UnavailabilityError, "binding is invalid"):
            validate_candidates(candidates)
        shard_schema = json.loads(
            (
                ROOT
                / "schemas/public-replay-unavailability-candidate-shard-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        _manifest, shards = self.bundle()
        shard = json.loads(next(iter(shards.values())))
        shard["candidates"][0]["issue_candidates"][0]["status"] = (
            "matched_source_unavailable"
        )
        shard["candidates"][0]["issue_candidates"][1]["status"] = (
            "matched_source_unavailable"
        )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(shard_schema).validate(shard)

    def test_review_and_disposition_schemas_reject_contradictions(self) -> None:
        manifest, shards = self.bundle()
        reviews = self.reviews(manifest, shards)
        dispositions = self.finish(manifest, shards, reviews)
        review_schema = json.loads(
            (
                ROOT / "schemas/public-replay-unavailability-reviews-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        disposition_schema = json.loads(
            (
                ROOT
                / "schemas/public-replay-unavailability-dispositions-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        deferred_with_reason = copy.deepcopy(reviews)
        deferred_with_reason["reviews"][0].update(
            decision="defer",
            reason_code=PERMANENT_REASON,
            rationale_code=RATIONALE,
        )
        permanent_without_reason = copy.deepcopy(reviews)
        permanent_without_reason["reviews"][0].update(
            reason_code=None, rationale_code=None
        )
        complete_with_false_claim = copy.deepcopy(dispositions)
        complete_with_false_claim["claims"]["unavailability_review_complete"] = False
        incomplete_with_true_claim = copy.deepcopy(dispositions)
        incomplete_with_true_claim.update(
            review_status="incomplete", deferred_request_count=1
        )
        incomplete_with_true_claim["deferred_result_count"] = 1
        complete_with_deferred_results = copy.deepcopy(dispositions)
        complete_with_deferred_results["deferred_result_count"] = 1
        empty_complete = copy.deepcopy(dispositions)
        empty_complete.update(
            candidate_request_count=0,
            candidate_result_count=0,
            request_count=0,
            result_count=0,
            dispositions=[],
        )
        for schema, value in (
            (review_schema, deferred_with_reason),
            (review_schema, permanent_without_reason),
            (disposition_schema, complete_with_false_claim),
            (disposition_schema, incomplete_with_true_claim),
            (disposition_schema, complete_with_deferred_results),
            (disposition_schema, empty_complete),
        ):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.Draft202012Validator(schema).validate(value)
        with self.assertRaisesRegex(UnavailabilityError, "inconsistent"):
            validate_dispositions(
                complete_with_false_claim,
                manifest=manifest,
                manifest_raw=canonical_document_bytes(manifest),
                shard_bytes=shards,
            )

    def test_runtime_disposition_validator_rejects_counter_drift(self) -> None:
        manifest, shards = self.bundle()
        dispositions = self.finish(manifest, shards)
        for field in (
            "candidate_request_count",
            "candidate_result_count",
            "request_count",
            "result_count",
            "deferred_request_count",
            "deferred_result_count",
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(dispositions)
                changed[field] += 1
                with self.assertRaises(UnavailabilityError):
                    validate_dispositions(
                        changed,
                        manifest=manifest,
                        manifest_raw=canonical_document_bytes(manifest),
                        shard_bytes=shards,
                    )

    def test_runtime_disposition_validator_requires_exact_candidate_coverage(
        self,
    ) -> None:
        manifest, shards = self.bundle()
        dispositions = self.finish(manifest, shards)
        mutations = {}

        empty_complete = copy.deepcopy(dispositions)
        empty_complete.update(request_count=0, result_count=0, dispositions=[])
        mutations["empty complete"] = empty_complete

        reauthored_totals = copy.deepcopy(empty_complete)
        reauthored_totals.update(candidate_request_count=0, candidate_result_count=0)
        mutations["drop one and recount"] = reauthored_totals

        wrong_identity = copy.deepcopy(dispositions)
        wrong_identity["candidate_identity_sha256"] = "0" * 64
        mutations["wrong manifest identity"] = wrong_identity

        wrong_candidate = copy.deepcopy(dispositions)
        wrong_candidate["dispositions"][0]["candidate_sha256"] = "0" * 64
        mutations["wrong candidate identity"] = wrong_candidate

        wrong_results = copy.deepcopy(dispositions)
        wrong_results["dispositions"][0]["result_ids"] = ["r2_" + "0" * 64]
        wrong_results["result_count"] = 1
        wrong_results["candidate_result_count"] = 1
        mutations["re-authored result subset"] = wrong_results

        for label, changed in mutations.items():
            with self.subTest(label=label), self.assertRaises(UnavailabilityError):
                validate_dispositions(
                    changed,
                    manifest=manifest,
                    manifest_raw=canonical_document_bytes(manifest),
                    shard_bytes=shards,
                )
        with self.assertRaisesRegex(UnavailabilityError, "not canonical"):
            validate_dispositions(
                dispositions,
                manifest=manifest,
                manifest_raw=canonical_document_bytes(manifest) + b" ",
                shard_bytes=shards,
            )


if __name__ == "__main__":
    unittest.main()
