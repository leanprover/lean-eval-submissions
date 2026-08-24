from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import tempfile
import unittest

from scripts import sanitize_executor_failure

ROOT = pathlib.Path(__file__).parents[1]
MATRIX = ROOT / "configuration/historical-public-replay-profile-matrix-v1.json"
CONTRACT = ROOT / "historical-public-qualification/contract-v1.json"
MODULE_PATH = ROOT / "historical-public-qualification/qualification.py"
SPEC = importlib.util.spec_from_file_location("historical_qualification", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)
WAIT_MODULE_PATH = ROOT / "historical-public-qualification/wait_rollout.py"
WAIT_SPEC = importlib.util.spec_from_file_location(
    "historical_qualification_wait_rollout", WAIT_MODULE_PATH
)
assert WAIT_SPEC is not None and WAIT_SPEC.loader is not None
wait_rollout = importlib.util.module_from_spec(WAIT_SPEC)
WAIT_SPEC.loader.exec_module(wait_rollout)

LAYER_MODULE_PATH = ROOT / "scripts/prepare_historical_image_layers.py"
LAYER_SPEC = importlib.util.spec_from_file_location(
    "prepare_historical_image_layers", LAYER_MODULE_PATH
)
assert LAYER_SPEC is not None and LAYER_SPEC.loader is not None
image_layers = importlib.util.module_from_spec(LAYER_SPEC)
LAYER_SPEC.loader.exec_module(image_layers)


class HistoricalPublicImageQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix = json.loads(MATRIX.read_text())
        cls.entry = cls.matrix["images"][0]
        cls.commit = cls.entry["benchmark_commit"]

    def test_all_25_profiles_remain_unqualified_and_select_exactly(self) -> None:
        self.assertEqual(self.matrix["image_count"], 25)
        self.assertEqual(len(self.matrix["images"]), 25)
        self.assertEqual(self.matrix["qualification_status"], "unqualified")
        self.assertEqual({item["qualification_status"] for item in self.matrix["images"]}, {"unqualified"})
        candidates = [
            qualification.candidate(MATRIX, CONTRACT, entry["benchmark_commit"])
            for entry in self.matrix["images"]
        ]
        self.assertEqual(len({item["benchmark_commit"] for item in candidates}), 25)
        self.assertEqual({item["qualification_status"] for item in candidates}, {"unqualified"})
        candidate = candidates[0]
        self.assertEqual(candidate["benchmark_tree"], self.entry["benchmark_tree"])
        self.assertEqual(candidate["toolchain"], self.entry["toolchain"])
        self.assertEqual(candidate["workspace_count"], self.entry["workspace_count"])
        self.assertEqual(candidate["profile_lock_sha256"], qualification.sha256_bytes(qualification.canonical(self.entry["profile_lock"])))
        self.assertEqual(candidate["qualification_status"], "unqualified")

    def test_unknown_or_mutated_matrix_is_rejected(self) -> None:
        with self.assertRaisesRegex(qualification.QualificationError, "does not select"):
            qualification.candidate(MATRIX, CONTRACT, "0" * 40)
        changed = json.loads(MATRIX.read_text())
        changed["images"][0]["qualification_status"] = "qualified"
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "matrix.json"
            path.write_text(json.dumps(changed, indent=2, sort_keys=True) + "\n")
            with self.assertRaisesRegex(qualification.QualificationError, "digest changed"):
                qualification.candidate(path, CONTRACT, self.commit)

    def test_rendered_worker_is_dedicated_replay_disabled_and_exactly_bound(self) -> None:
        config = qualification.render_config(
            MATRIX,
            CONTRACT,
            self.commit,
            "1" * 32,
            "sha256:" + "2" * 64,
            "3" * 40,
            "4" * 40,
        )
        staging = config["env"]["staging"]
        self.assertEqual(staging["name"], "lean-eval-historical-qualifier-staging")
        self.assertEqual(staging["vars"]["REPLAY_ENABLED"], "false")
        self.assertEqual(staging["vars"]["STAGING_ACCEPTANCE_ENABLED"], "true")
        self.assertEqual(staging["vars"]["REVIEWED_EXECUTION_PROFILE_DIGEST"], "0" * 64)
        self.assertEqual(staging["vars"]["REVIEWED_MEASUREMENT_CONFIG_DIGEST"], "0" * 64)
        self.assertEqual(staging["vars"]["GITHUB_OIDC_ENVIRONMENT"], "replay-staging")
        self.assertEqual(staging["containers"][0]["instance_type"], "standard-4")
        self.assertEqual(staging["containers"][0]["max_instances"], 1)
        self.assertEqual(staging["containers"][0]["ssh"], {"enabled": False})
        self.assertEqual(staging["vars"]["DEPLOYED_COMMIT"], "3" * 40)
        self.assertTrue(
            staging["containers"][0]["image"].endswith(
                "/lean-eval-historical-public-v1:"
                + self.commit
                + "-"
                + "4" * 40
                + "@sha256:"
                + "2" * 64
            )
        )

    def test_health_and_two_same_nonce_probe_responses_validate_but_stay_unqualified(self) -> None:
        config = qualification.render_config(
            MATRIX,
            CONTRACT,
            self.commit,
            "1" * 32,
            "sha256:" + "2" * 64,
            "3" * 40,
            "4" * 40,
        )
        binding = {"vars": config["env"]["staging"]["vars"]}
        variables = binding["vars"]
        health = {
            "status": "ok", "service": "lean-eval-replay-executor", "environment": "staging",
            "deployed_commit": variables["DEPLOYED_COMMIT"], "replay_enabled": False,
            "staging_acceptance_enabled": True,
            "staging_memory_limit_bytes": 12_884_901_888,
            "production_memory_gate_bytes": 12_884_901_888,
            "reviewed_execution_profile_digest": variables["REVIEWED_EXECUTION_PROFILE_DIGEST"],
            "reviewed_measurement_config_digest": variables["REVIEWED_MEASUREMENT_CONFIG_DIGEST"],
            "reviewed_vm_image_digest": variables["REVIEWED_VM_IMAGE_DIGEST"],
        }
        self.assertEqual(qualification.validate_health(health, binding), health)
        contract = qualification.load(CONTRACT, qualification.CONTRACT_SHA256)
        for number in (1, 2):
            request = {
                "request_id": f"01234567-89ab-7cde-8fab-{number:012d}",
                "runner_nonce": "4" * 64,
                "archive_ciphertext_sha256": str(number) * 64,
                "marker_sha256": str(number + 2) * 64,
            }
            response = {
                "schema_version": 1, "service": "lean-eval-replay-executor", "environment": "staging",
                "request_id": request["request_id"], "runner_nonce": request["runner_nonce"],
                "archive_ciphertext_sha256": request["archive_ciphertext_sha256"],
                "marker_sha256": request["marker_sha256"], "network_policy": "disabled",
                "network_probe": "blocked", "destruction": "confirmed", "architecture": "x86_64",
                "kernel_release": "fixture-kernel", "cpu_model": "fixture-cpu",
                "staging_memory_limit_bytes": 12_884_901_888,
                "production_memory_gate_bytes": 12_884_901_888,
            }
            self.assertEqual(qualification.validate_probe(response, request, contract), response)

    def test_compact_remote_json_and_account_free_rollout_validate(self) -> None:
        compact = {"status": "ok", "nested": {"value": 1}}
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "response.json"
            path.write_text(json.dumps(compact, separators=(",", ":")), encoding="utf-8")
            self.assertEqual(qualification.load_external(path), compact)
            with self.assertRaisesRegex(qualification.QualificationError, "not canonical"):
                qualification.load(path)
        contract = qualification.load(CONTRACT, qualification.CONTRACT_SHA256)
        rollout = {
            "schema_version": 2,
            "kind": "historical_public_qualification_rollout",
            "qualification_status": "unqualified",
            "name": contract["container_application"],
            "version": 1,
            "max_instances": 1,
            "image_repository": contract["registry_repository"],
            "image_tag": self.commit + "-" + "3" * 40,
            "image_manifest_digest": "sha256:" + "2" * 64,
            "runtime_boundary": {
                "vcpu": 4,
                "memory_mib": 12 * 1024,
                "disk_size_mb": 20_000,
                "network": {"assign_ipv6": "none", "assign_ipv4": "none", "mode": "private"},
                "ssh": {"enabled": False},
            },
            "health": {"errors": [], "instances": {"healthy": 1, "failed": 0, "starting": 0, "scheduling": 0}},
        }
        self.assertEqual(
            qualification.validate_rollout(
                rollout,
                contract,
                self.commit,
                "3" * 40,
                "sha256:" + "2" * 64,
            ),
            rollout,
        )
        wrong_digest = {**rollout, "image_manifest_digest": "sha256:" + "9" * 64}
        with self.assertRaisesRegex(qualification.QualificationError, "identity changed"):
            qualification.validate_rollout(
                wrong_digest,
                contract,
                self.commit,
                "3" * 40,
                "sha256:" + "2" * 64,
            )
        self.assertNotIn("account", json.dumps(rollout))

    def test_rollout_loader_requires_the_digest_pinned_tag(self) -> None:
        config = qualification.render_config(
            MATRIX,
            CONTRACT,
            self.commit,
            "1" * 32,
            "sha256:" + "2" * 64,
            "3" * 40,
            "4" * 40,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            expected = wait_rollout.load_expected(path, "staging")
            self.assertIn("@sha256:" + "2" * 64, expected["image"])
            config["env"]["staging"]["containers"][0]["image"] = expected[
                "image"
            ].split("@", 1)[0]
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(
                wait_rollout.rollout.RolloutError, "image reference is invalid"
            ):
                wait_rollout.load_expected(path, "staging")

    def test_source_free_diagnostic_contract_fails_closed(self) -> None:
        self.assertEqual(
            qualification.EXECUTOR_FAILURE_REASONS,
            sanitize_executor_failure.ALLOWED_REASONS,
        )
        self.assertEqual(
            qualification.EXECUTOR_FAILURE_DETAILS,
            sanitize_executor_failure.ALLOWED_DETAILS,
        )
        replay_app = (ROOT / "server/src/replay-app.ts").read_text()
        map_region = replay_app.split("const ARCHIVE_COMMAND_FAILURES", 1)[1].split(
            "type AuthoritativeFailureBody", 1
        )[0]
        emitted_details = set(
            re.findall(r'\[\s*"[^"]+"\s*,\s*"([a-z0-9_]+)"\s*\]', map_region)
        )
        classifier = replay_app.split(
            "function authoritativeCommandFailureDetail", 1
        )[1].split("function safeCommandFailureDetail", 1)[0]
        emitted_details.update(re.findall(r'return "([a-z0-9_]+)"', classifier))
        emitted_details.add("unclassified_archive_failure")
        self.assertTrue(
            emitted_details <= sanitize_executor_failure.ALLOWED_DETAILS,
            emitted_details - sanitize_executor_failure.ALLOWED_DETAILS,
        )
        binding = {
            "benchmark_commit": self.commit,
            "controller_source_commit": "1" * 40,
            "image_source_commit": "2" * 40,
            "manifest_digest": "sha256:" + "3" * 64,
        }
        initialized = qualification.staging_diagnostic(
            **binding,
            outcome="initialized",
            probe_number=0,
            http_status=None,
            failure=None,
        )
        self.assertEqual(initialized["qualification_status"], "unqualified")
        self.assertIsNone(initialized["executor_failure"])
        failure = {"error": "executor_failed", "reason": "command_rpc_failed"}
        diagnostic = qualification.staging_diagnostic(
            **binding,
            outcome="executor_failed",
            probe_number=1,
            http_status=500,
            failure=failure,
        )
        self.assertEqual(diagnostic["executor_failure"], failure)
        evidence_invalid = qualification.staging_diagnostic(
            **binding,
            outcome="evidence_invalid",
            probe_number=2,
            http_status=200,
            failure=None,
        )
        self.assertEqual(evidence_invalid["outcome"], "evidence_invalid")
        for hostile in (
            {**failure, "stderr": "private source"},
            {"error": "executor_failed", "reason": "private_source"},
            {"error": "executor_failed", "reason": ["command_rpc_failed"]},
        ):
            with self.assertRaises(qualification.QualificationError):
                qualification.staging_diagnostic(
                    **binding,
                    outcome="executor_failed",
                    probe_number=1,
                    http_status=500,
                    failure=hostile,
                )
        with self.assertRaisesRegex(qualification.QualificationError, "inconsistent"):
            qualification.staging_diagnostic(
                **binding,
                outcome="executor_failed",
                probe_number=1,
                http_status=200,
                failure=failure,
            )

    def test_uuid7_shape(self) -> None:
        self.assertRegex(qualification.uuid7(), r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

    def test_workflow_and_dockerfile_are_manual_create_only_contracts(self) -> None:
        workflow = (ROOT / ".github/workflows/historical-public-image-qualification.yml").read_text()
        dockerfile = (ROOT / "Dockerfile.historical-public-replay").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("immutable registry tag already exists; refusing overwrite", workflow)
        self.assertIn('test -z "$RESUME_IMAGE_SOURCE_COMMIT"', workflow)
        self.assertIn('git merge-base --is-ancestor "$image_source_commit" "$GITHUB_SHA"', workflow)
        self.assertIn('test "$image_source_remote_commit" = "$image_source_commit"', workflow)
        self.assertIn('test "$image_digest" = "$RESUME_DIGEST"', workflow)
        self.assertIn(
            'git show "$image_source_commit:server/replay-image/replay-staging-acceptance"',
            workflow,
        )
        self.assertIn('python "$FAILURE_SANITIZER" "$RUNNER_TEMP/probe-response.json"', workflow)
        self.assertIn('if [ "$number" -eq 2 ]; then', workflow)
        self.assertEqual(workflow.count("sleep 30"), 1)
        self.assertIn("Preserve the same-nonce recreation", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("write_diagnostic evidence_invalid 2 200", workflow)
        self.assertIn('local -a arguments=(', workflow)
        self.assertIn("historical-public-staging-qualification-diagnostic", workflow)
        self.assertNotIn("--fail --silent --show-error --max-time 480", workflow)
        self.assertIn("REPLAY_ENABLED\": \"false", MODULE_PATH.read_text())
        self.assertIn("qualification_status\": \"unqualified", MODULE_PATH.read_text())
        self.assertIn(qualification.MATRIX_SHA256, dockerfile)
        self.assertIn('matrix["image_count"] == len(matrix["images"]) == 25', dockerfile)
        self.assertNotIn("containers push", dockerfile)

    def test_large_image_layers_are_separated_canonical_and_root_owned(self) -> None:
        dockerfile = (ROOT / "Dockerfile.historical-public-replay").read_text()
        package_copy = (
            "COPY --from=lean-builder --chown=0:0 /runtime/benchmark-packages/ "
            "/opt/lean-eval/benchmark/.lake/packages/"
        )
        benchmark_copy = (
            "COPY --from=lean-builder --chown=0:0 /runtime/benchmark/ "
            "/opt/lean-eval/benchmark/"
        )
        home_copy = (
            "COPY --from=lean-builder --chown=0:0 /runtime/home/ "
            "/opt/lean-eval/home/"
        )
        self.assertEqual(dockerfile.count(package_copy), 1)
        self.assertEqual(dockerfile.count(benchmark_copy), 1)
        self.assertEqual(dockerfile.count(home_copy), 1)
        self.assertLess(dockerfile.index(benchmark_copy), dockerfile.index(package_copy))
        self.assertIn(
            "python3 /tmp/prepare-historical-image-layers.py --runtime-root /runtime",
            dockerfile,
        )
        self.assertIn(
            "pathlib.Path(package[\"dir\"]).is_dir() for package in packages",
            dockerfile,
        )
        self.assertIn("git diff --exit-code -- lake-manifest.json", dockerfile)
        self.assertNotIn(
            "COPY --from=lean-builder /runtime/benchmark /opt/lean-eval/benchmark",
            dockerfile,
        )
        dockerignore = (
            ROOT / "Dockerfile.historical-public-replay.dockerignore"
        ).read_text().splitlines()
        self.assertIn("!scripts/prepare_historical_image_layers.py", dockerignore)

    def test_layer_preparation_separates_packages_and_canonicalizes_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = pathlib.Path(directory) / "runtime"
            for path in (
                runtime / "benchmark/.lake/packages/mathlib",
                runtime / "bin",
                runtime / "home/.elan/toolchains/lean",
                runtime / "profile",
            ):
                path.mkdir(parents=True)
            (runtime / "benchmark/generated").mkdir()
            files = (
                runtime / "benchmark/.lake/packages/mathlib/Mathlib.olean",
                runtime / "benchmark/generated/Main.lean",
                runtime / "bin/comparator",
                runtime / "home/.elan/toolchains/lean/lean",
                runtime / "profile/profile-lock.json",
            )
            for index, path in enumerate(files, start=1):
                path.write_text(f"fixture-{index}")
                os.utime(path, ns=(index, index), follow_symlinks=False)
            link = runtime / "benchmark/.lake/packages/mathlib/current"
            link.symlink_to("Mathlib.olean")
            os.utime(link, ns=(99, 99), follow_symlinks=False)

            image_layers.prepare_runtime_layers(runtime)

            packages = runtime / "benchmark-packages"
            self.assertTrue((packages / "mathlib/Mathlib.olean").is_file())
            self.assertEqual(
                (packages / "mathlib/current").readlink(),
                pathlib.Path("Mathlib.olean"),
            )
            self.assertTrue((runtime / "benchmark/.lake/packages").is_dir())
            self.assertEqual(list((runtime / "benchmark/.lake/packages").iterdir()), [])
            self.assertTrue((runtime / "benchmark/generated/Main.lean").is_file())
            for root, directories, names in os.walk(runtime, followlinks=False):
                paths = [
                    pathlib.Path(root),
                    *[pathlib.Path(root) / name for name in directories + names],
                ]
                for path in paths:
                    self.assertEqual(
                        path.lstat().st_mtime_ns,
                        image_layers.CANONICAL_MTIME_NS,
                    )

    def test_layer_preparation_rejects_ambiguous_or_linked_package_store(self) -> None:
        for obstruction in ("existing-layer", "linked-packages", "extra-layer"):
            with (
                self.subTest(obstruction=obstruction),
                tempfile.TemporaryDirectory() as directory,
            ):
                runtime = pathlib.Path(directory) / "runtime"
                for path in (
                    runtime / "benchmark/.lake",
                    runtime / "bin",
                    runtime / "home",
                    runtime / "profile",
                ):
                    path.mkdir(parents=True)
                if obstruction == "existing-layer":
                    (runtime / "benchmark/.lake/packages").mkdir()
                    (runtime / "benchmark-packages").mkdir()
                else:
                    (runtime / "benchmark/.lake/packages").mkdir()
                    if obstruction == "linked-packages":
                        target = runtime / "external-packages"
                        target.mkdir()
                        (runtime / "benchmark/.lake/packages").rmdir()
                        (runtime / "benchmark/.lake/packages").symlink_to(target)
                    else:
                        (runtime / "unexpected").mkdir()
                with self.assertRaises(image_layers.LayerPreparationError):
                    image_layers.prepare_runtime_layers(runtime)

    def test_workflow_bounds_offline_cache_check_and_records_layer_identity(self) -> None:
        workflow = (
            ROOT / ".github/workflows/historical-public-image-qualification.yml"
        ).read_text()
        self.assertIn("timeout --signal=TERM --kill-after=30s 1800s", workflow)
        self.assertIn("find .lake/packages -name '*.olean' -newermt @1", workflow)
        self.assertIn("image-layer-diff-ids.json", workflow)
        self.assertIn('"layer_diff_ids": json.loads(', workflow)
        self.assertIn('"layer_preparation_sha256": hashlib.sha256(', workflow)


if __name__ == "__main__":
    unittest.main()
