from __future__ import annotations

import hashlib
import json
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

import jsonschema
from referencing import Registry, Resource

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from inventory_historical_replay import canonical_inventory_bytes, inventory
from prepare_public_replay_resolution import prepare

EVIDENCE = ROOT / "evidence" / "historical-public-replay-github-evidence-6c13c24.json"
EXPECTED_SHA256 = "8122b4ee0a308ce1202f66e94c3cd6bf189c65641a6755f2de95ff1ec78127e2"
SOURCE_COMMIT = "6c13c245d17a1e25a59846769e533265e8ac9ba8"


def materialize_results_tree(commit: str, destination: pathlib.Path) -> pathlib.Path:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            commit,
            "--",
            "results",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    relative_paths = [
        pathlib.PurePosixPath(raw.decode("utf-8"))
        for raw in completed.stdout.split(b"\0")
        if raw
    ]
    if not relative_paths:
        raise AssertionError("frozen Results tree is empty")
    results_root = destination / "results"
    results_root.mkdir()
    for relative in relative_paths:
        if (
            len(relative.parts) != 2
            or relative.parts[0] != "results"
            or (relative.name != ".gitkeep" and relative.suffix != ".json")
        ):
            raise AssertionError("frozen Results tree has an unexpected path")
        blob = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{commit}:{relative.as_posix()}"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        (destination / pathlib.Path(*relative.parts)).write_bytes(blob)
    return results_root


class CommittedPublicGistProbeEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = EVIDENCE.read_bytes()
        cls.value = json.loads(cls.raw)
        schemas = [
            json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            for name in (
                "public-replay-github-evidence-v1.schema.json",
                "public-replay-github-evidence-aggregate-v1.schema.json",
            )
        ]
        cls.schema = schemas[1]
        cls.registry = Registry().with_resources(
            [(schema["$id"], Resource.from_contents(schema)) for schema in schemas]
        )

    def test_asset_is_regular_source_free_schema_valid_json(self) -> None:
        self.assertTrue(stat.S_ISREG(EVIDENCE.stat(follow_symlinks=False).st_mode))
        self.assertLess(len(self.raw), 1_000_000)
        jsonschema.Draft202012Validator(self.schema, registry=self.registry).validate(
            self.value
        )

    def test_reviewed_bytes_and_inputs_are_immutable(self) -> None:
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), EXPECTED_SHA256)
        self.assertEqual(self.value["source_commit"], SOURCE_COMMIT)
        self.assertEqual(
            self.value["inventory_sha256"],
            "7c1b393711654741a6d69d5c0e8db02cf89078c4cc5fe3e96002c614d5c0bd22",
        )
        self.assertEqual(
            self.value["resolution_requests_sha256"],
            "50202e7331a77ed04be04a784315b8ecfad6f593edc6686763d196552df5e2fa",
        )
        self.assertEqual(
            self.value["workflow_definition_registry_sha256"],
            "82eff4dce70c2fcb7f480522f4de1fb16884534ce5f9452032908bb299c12196",
        )

    def test_gist_permission_boundary_is_resolved(self) -> None:
        counts = Counter(item["status"] for item in self.value["resolutions"])
        self.assertEqual(
            counts,
            {
                "resolved": 126,
                "source_unavailable": 184,
                "timing_indeterminate": 2,
                "evidence_missing": 3,
            },
        )
        self.assertEqual(self.value["request_count"], 315)
        self.assertEqual(self.value["result_count"], 633)
        self.assertEqual(self.value["pending_count"], 189)
        self.assertEqual(self.value["source_indeterminate_count"], 0)
        self.assertEqual(self.value["probe_indeterminate_count"], 0)
        self.assertEqual(self.value["workflow_contract_unreviewed_count"], 0)
        self.assertEqual(
            [item["shard_index"] for item in self.value["shards"]],
            list(range(16)),
        )

    def test_result_classification_counts_join_exact_recomputed_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results_root = materialize_results_tree(
                SOURCE_COMMIT, pathlib.Path(directory)
            )
            inventory_value = inventory(results_root, SOURCE_COMMIT)
            inventory_raw = canonical_inventory_bytes(inventory_value)
            inventory_sha256 = hashlib.sha256(inventory_raw).hexdigest()
            self.assertEqual(inventory_sha256, self.value["inventory_sha256"])

            requests = prepare(inventory_value, inventory_sha256, results_root)
            requests_raw = (
                json.dumps(requests, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(requests_raw).hexdigest(),
            self.value["resolution_requests_sha256"],
        )
        result_counts = {
            request["request_id"]: len(request["results"])
            for request in requests["requests"]
        }
        request_totals = Counter()
        result_totals = Counter()
        for resolution in self.value["resolutions"]:
            status = resolution["status"]
            request_totals[status] += 1
            result_totals[status] += result_counts[resolution["request_id"]]
        self.assertEqual(
            dict(request_totals),
            {
                "resolved": 126,
                "source_unavailable": 184,
                "timing_indeterminate": 2,
                "evidence_missing": 3,
            },
        )
        self.assertEqual(
            dict(result_totals),
            {
                "resolved": 192,
                "source_unavailable": 219,
                "timing_indeterminate": 2,
                "evidence_missing": 220,
            },
        )


if __name__ == "__main__":
    unittest.main()
