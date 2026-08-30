from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import prepare_historical_private_image_matrix as image_matrix  # noqa: E402


MATRIX_PATH = ROOT / "configuration/historical-private-replay-image-matrix-v1.json"
PRIVATE_PLAN_PATH = (
    ROOT
    / "evidence/historical-replay/private-plans"
    / "d9561ad62098e0542656678f207b3360b0b295be975c292cbf729dc48d03bd5e.json"
)
PUBLIC_MATRIX_PATH = ROOT / "configuration/historical-public-replay-profile-matrix-v1.json"
COMPONENT_LOCK_PATH = ROOT / "configuration/historical-public-replay-components-v1.json"
DOCKERFILE = ROOT / "Dockerfile.historical-private-replay"
DOCKERIGNORE = ROOT / "Dockerfile.historical-private-replay.dockerignore"


def load(path: pathlib.Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    return json.loads(raw), raw


class HistoricalPrivateReplayImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix, cls.matrix_raw = load(MATRIX_PATH)
        cls.plan, cls.plan_raw = load(PRIVATE_PLAN_PATH)
        cls.public, cls.public_raw = load(PUBLIC_MATRIX_PATH)
        cls.components, cls.components_raw = load(COMPONENT_LOCK_PATH)

    def test_committed_matrix_is_canonical_and_exactly_bounded(self) -> None:
        self.assertEqual(image_matrix.canonical(self.matrix), self.matrix_raw)
        self.assertEqual(self.matrix["schema_version"], 1)
        self.assertEqual(
            self.matrix["kind"], "historical_private_replay_image_matrix"
        )
        self.assertEqual(self.matrix["benchmark_repository"], "leanprover/lean-eval")
        self.assertEqual(self.matrix["checker"], "nanoda")
        self.assertEqual(self.matrix["image_count"], 63)
        self.assertEqual(len(self.matrix["images"]), 63)
        self.assertEqual(self.matrix["result_count"], 639)
        self.assertEqual(self.matrix["toolchain_count"], 5)
        self.assertEqual(self.matrix["reused_public_source_count"], 21)
        self.assertEqual(self.matrix["derived_exact_source_count"], 42)
        self.assertEqual(
            self.matrix["private_plan_sha256"], hashlib.sha256(self.plan_raw).hexdigest()
        )
        self.assertEqual(
            self.matrix["historical_public_profile_matrix_sha256"],
            hashlib.sha256(self.public_raw).hexdigest(),
        )
        self.assertEqual(
            self.matrix["historical_public_component_lock_sha256"],
            hashlib.sha256(self.components_raw).hexdigest(),
        )

    def test_matrix_covers_every_bound_private_result_and_commit(self) -> None:
        bound = [entry for entry in self.plan["entries"] if entry["classification"] == "bound"]
        commits = sorted({entry["benchmark_commit"] for entry in bound})
        images = self.matrix["images"]
        self.assertEqual([image["benchmark_commit"] for image in images], commits)
        self.assertEqual(sum(image["result_count"] for image in images), len(bound))
        for image in images:
            selected = [
                entry for entry in bound
                if entry["benchmark_commit"] == image["benchmark_commit"]
            ]
            self.assertEqual(image["result_count"], len(selected))
            self.assertEqual(
                image["problem_ids"], sorted({entry["problem_id"] for entry in selected})
            )

    def test_public_source_pins_are_reused_without_qualification_digests(self) -> None:
        public = {image["benchmark_commit"]: image for image in self.public["images"]}
        reused = [
            image for image in self.matrix["images"]
            if image["source_pin_origin"] == "historical_public_matrix_v1"
        ]
        self.assertEqual(len(reused), 21)
        for image in reused:
            anchor = public[image["benchmark_commit"]]
            for field in (
                "benchmark_commit",
                "benchmark_tree",
                "toolchain",
                "lean_toolchain_blob_sha256",
                "manifest_layout",
                "workspace_count",
                "profile_lock",
            ):
                self.assertEqual(image[field], anchor[field])
        encoded = self.matrix_raw.decode("utf-8")
        for forbidden in (
            "qualification_status",
            "registry_manifest_digest",
            "vm_image_digest",
            "execution_profile_digest",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_only_publicly_locked_toolchains_and_official_nanoda_components_are_used(self) -> None:
        exports = {
            entry["toolchain"]: entry["commit"]
            for entry in self.components["lean4export"]
        }
        counts: dict[str, int] = {}
        for image in self.matrix["images"]:
            toolchain = image["toolchain"]
            counts[toolchain] = counts.get(toolchain, 0) + 1
            lock = image["profile_lock"]
            self.assertEqual(lock["toolchain"], toolchain)
            self.assertEqual(
                lock["components"]["lean4export"]["commit"], exports[toolchain]
            )
            self.assertEqual(
                set(lock["components"]),
                {"comparator", "landrun", "lean4export", "nanoda"},
            )
        self.assertEqual(
            counts,
            {
                "leanprover/lean4:v4.30.0": 7,
                "leanprover/lean4:v4.30.0-rc2": 35,
                "leanprover/lean4:v4.32.0-rc1": 4,
                "leanprover/lean4:v4.32.2": 8,
                "leanprover/lean4:v4.33.0": 9,
            },
        )

    def test_missing_exact_benchmark_source_fails_closed(self) -> None:
        with mock.patch.object(
            image_matrix,
            "subprocess_git",
            side_effect=image_matrix.ProfileMatrixError("missing object"),
        ):
            with self.assertRaisesRegex(
                image_matrix.PrivateImageMatrixError,
                "exact Lean toolchain is unavailable",
            ):
                image_matrix.build_matrix(
                    private_plan=self.plan,
                    private_plan_raw=self.plan_raw,
                    public_matrix=self.public,
                    public_matrix_raw=self.public_raw,
                    component_lock=self.components,
                    component_lock_raw=self.components_raw,
                    benchmark_repository=ROOT,
                )

    def test_dedicated_dockerfile_is_pinned_private_capable_and_matrix_selected(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        from_lines = [
            line for line in dockerfile.splitlines() if line.startswith("FROM ")
        ]
        self.assertEqual(len(from_lines), 5)
        self.assertTrue(
            all(re.search(r"@sha256:[0-9a-f]{64}(?: AS [a-z-]+)?$", line) for line in from_lines)
        )
        python_base = (
            "FROM docker.io/cloudflare/sandbox:0.12.7-python@sha256:"
            "6dfa7301e69d3e5cd8e0404b92fd240026fe834ed7101ee29cb66337b0af0981"
        )
        self.assertIn(f"{python_base} AS lean-builder", from_lines)
        self.assertIn(python_base, from_lines)
        self.assertIn("python3 -c 'import tomllib'", dockerfile)
        self.assertIn("ARG BENCHMARK_COMMIT", dockerfile)
        self.assertIn("len(matrix[\"images\"]) == 63", dockerfile)
        self.assertIn(hashlib.sha256(self.matrix_raw).hexdigest(), dockerfile)
        self.assertIn(
            'org.lean-eval.image-family="lean-eval-authoritative-private-replay-v1"',
            dockerfile,
        )
        self.assertIn(
            "COPY --from=age-file-key-builder /age-file-key /opt/lean-eval/bin/age-file-key",
            dockerfile,
        )
        self.assertIn(
            "COPY --from=nanoda-builder /build/nanoda/target/release/nanoda_bin",
            dockerfile,
        )
        self.assertIn(
            "COPY scripts/replay_orchestrator.py /opt/lean-eval/replay_orchestrator.py",
            dockerfile,
        )
        self.assertIn(
            "mkdir -p -m 0700 /run/lean-eval /workspace \\\n"
            "      /opt/lean-eval/benchmark/.replay-workspaces",
            dockerfile,
        )
        self.assertIn("git init /opt/lean-eval/benchmark", dockerfile)
        self.assertNotIn("git init /build/benchmark", dockerfile)
        self.assertIn("lake exe lean-eval validate-manifest", dockerfile)
        self.assertIn("lake build extract_theorem", dockerfile)
        self.assertNotIn("replay-archive-acceptance", dockerfile)
        self.assertNotIn("qualification", dockerfile.lower().replace("qualification record", ""))
        self.assertNotIn("experimental", dockerfile.lower())

    def test_docker_context_is_the_closed_direct_dependency_set(self) -> None:
        included = {
            line[1:] for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
            if line.startswith("!")
        }
        self.assertEqual(
            included,
            {
                "Dockerfile.historical-private-replay",
                "configuration/historical-private-replay-image-matrix-v1.json",
                "scripts/evaluate_submission.py",
                "scripts/prepare_historical_image_layers.py",
                "scripts/replay_orchestrator.py",
                "server/age-file-key/go.mod",
                "server/age-file-key/go.sum",
                "server/age-file-key/main.go",
                "server/replay-image/comparator-71b52-phase-metrics.patch",
                "server/replay-image/replay-authoritative",
                "server/replay-image/replay-measure",
            },
        )


if __name__ == "__main__":
    unittest.main()
