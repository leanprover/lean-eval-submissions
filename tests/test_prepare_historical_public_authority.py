from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import jsonschema
from referencing import Registry, Resource

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
MODULE_PATH = ROOT / "scripts/prepare_historical_public_authority.py"
SPEC = importlib.util.spec_from_file_location("prepare_historical_public_authority", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
authority = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(authority)

PLAN = ROOT / authority.PLAN_PATH
MATRIX = ROOT / authority.MATRIX_PATH
RUNNER_CONTRACT = ROOT / authority.RUNNER_CONTRACT_PATH
QUALIFICATION_CONTRACT = ROOT / authority.QUALIFICATION_CONTRACT_PATH
BENCHMARK_COMMIT = "11081d345a580a0f3c46699240f28e4f41fbf9fe"
REQUEST_ID = "prr_9927609e2e68eb0fbd8c2b599571321a4923b2a180642668203894514d5675af"
RESULT_ID = "r2_70b509d79a3787270b020128e4050ce0a9c6bf4cfe50a722013de43967e106b4"
CONTROLLER_COMMIT = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
).strip()
IMAGE_COMMIT = CONTROLLER_COMMIT
MANIFEST = "sha256:" + "3" * 64


def write(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(authority.canonical(value))


def event_id(occurred_at: str, tail: int) -> str:
    timestamp = f"{authority.timestamp_ms(occurred_at):012x}"
    return f"{timestamp[:8]}-{timestamp[8:]}-7000-8000-{tail:012x}"


def zip_members(path: pathlib.Path, members: dict[str, pathlib.Path]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in members.items():
            info = zipfile.ZipInfo(name, (2026, 8, 24, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, source.read_bytes())


class HistoricalPublicAuthorityPreparationTests(unittest.TestCase):
    def test_state_event_canonical_bytes_escape_non_ascii(self) -> None:
        value = {"declared_model": "λ-model", "schema_version": 1}
        self.assertEqual(
            authority.canonical_state_event(value),
            (
                b'{\n  "declared_model": "\\u03bb-model",\n'
                b'  "schema_version": 1\n}\n'
            ),
        )
        self.assertNotEqual(
            authority.canonical(value), authority.canonical_state_event(value)
        )

    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN.read_text())
        cls.matrix = json.loads(MATRIX.read_text())
        cls.entry = next(
            item for item in cls.matrix["images"]
            if item["benchmark_commit"] == BENCHMARK_COMMIT
        )
        cls.contract = json.loads(QUALIFICATION_CONTRACT.read_text())

    def fixture(self, root: pathlib.Path) -> dict[str, pathlib.Path]:
        preparation_source = root / "preparation-source"
        image_source = root / "image-source"
        for checkout in (preparation_source, image_source):
            subprocess.run(
                ["git", "init", "-q", str(checkout)], check=True
            )
            subprocess.run(
                [
                    "git", "-C", str(checkout), "fetch", "-q", "--depth=1",
                    str(ROOT), CONTROLLER_COMMIT,
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(checkout), "checkout", "-q", "--detach", "FETCH_HEAD"],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(checkout), "remote", "add", "origin",
                    "https://github.com/leanprover/lean-eval-submissions.git",
                ],
                check=True,
            )
        provenance = {
            "schema_version": 2,
            "kind": "historical_public_qualification_artifact_provenance",
            "repository": "leanprover/lean-eval-submissions",
            "workflow_path": ".github/workflows/historical-public-image-qualification.yml",
            "workflow_run_id": 123456789,
            "workflow_run_attempt": 2,
            "workflow_event": "workflow_dispatch",
            "workflow_conclusion": "success",
            "workflow_run_created_at": "2026-08-24T09:00:00Z",
            "workflow_run_started_at": "2026-08-24T10:00:00Z",
            "workflow_run_completed_at": "2026-08-24T11:00:00Z",
            "dispatch_ref": "lean-eval-dispatch/" + CONTROLLER_COMMIT,
            "controller_source_commit": CONTROLLER_COMMIT,
            "image_source_commit": IMAGE_COMMIT,
            "artifacts": [
                {
                    "artifact_id": 111,
                    "archive_sha256": "4" * 64,
                    "created_at": "2026-08-24T10:30:00Z",
                    "name": "historical-public-image-candidate",
                    "size_in_bytes": 1000,
                },
                {
                    "artifact_id": 222,
                    "archive_sha256": "5" * 64,
                    "created_at": "2026-08-24T10:45:00Z",
                    "name": "historical-public-staging-qualification",
                    "size_in_bytes": 1000,
                },
            ],
        }
        variables = {
            "DEPLOYED_COMMIT": CONTROLLER_COMMIT,
            "DEPLOYMENT_ENVIRONMENT": "staging",
            "GITHUB_OIDC_AUDIENCE": "lean-eval-historical-public-qualification-staging",
            "GITHUB_OIDC_ENVIRONMENT": "replay-staging",
            "PRODUCTION_MEMORY_GATE_BYTES": "12884901888",
            "REPLAY_ENABLED": "false",
            "REVIEWED_EXECUTION_PROFILE_DIGEST": "0" * 64,
            "REVIEWED_MEASUREMENT_CONFIG_DIGEST": "0" * 64,
            "REVIEWED_VM_IMAGE_DIGEST": MANIFEST,
            "SANDBOX_TRANSPORT": "rpc",
            "STAGING_ACCEPTANCE_ENABLED": "true",
            "STAGING_MEMORY_LIMIT_BYTES": "12884901888",
        }
        candidate = {
            "schema_version": 2,
            "benchmark_commit": BENCHMARK_COMMIT,
            "controller_source_commit": CONTROLLER_COMMIT,
            "image_source_commit": IMAGE_COMMIT,
            "qualification_status": "unqualified",
            "vars": variables,
        }
        publication = {
            "schema_version": 2,
            "kind": "historical_public_image_publication_evidence",
            "qualification_status": "unqualified",
            "controller_source_commit": CONTROLLER_COMMIT,
            "image_source_commit": IMAGE_COMMIT,
            "benchmark_commit": BENCHMARK_COMMIT,
            "benchmark_tree": self.entry["benchmark_tree"],
            "registry_repository": "lean-eval-historical-public-v1",
            "registry_tag": f"{BENCHMARK_COMMIT}-{IMAGE_COMMIT}",
            "registry_manifest_digest": MANIFEST,
            "publication_mode": "created",
            "image_size_bytes": 12_345,
            "dockerfile_sha256": authority.sha256_bytes(
                authority._git_optional_blob(image_source, IMAGE_COMMIT, "Dockerfile.historical-public-replay")
            ),
            "layer_preparation_sha256": authority.sha256_bytes(
                authority._git_optional_blob(image_source, IMAGE_COMMIT, "scripts/prepare_historical_image_layers.py")
            ),
            "layer_diff_ids": ["sha256:" + "6" * 64],
            "matrix_sha256": authority.MATRIX_SHA256,
            "matrix_entry_sha256": authority.sha256_bytes(authority.canonical(self.entry)),
            "profile_lock_sha256": authority.sha256_bytes(
                authority.canonical(self.entry["profile_lock"])
            ),
            "workspace_manifest_count": self.entry["workspace_count"],
            "workflow_image_limit_bytes": 18_000_000_000,
        }
        boundary = {
            "vcpu": 4,
            "memory_mib": 12 * 1024,
            "disk_size_mb": 20_000,
            "network": {"assign_ipv4": "none", "assign_ipv6": "none", "mode": "private"},
            "ssh": {"enabled": False},
        }
        rollout = {
            "schema_version": 2,
            "kind": "historical_public_qualification_rollout",
            "qualification_status": "unqualified",
            "name": self.contract["container_application"],
            "version": 7,
            "max_instances": 1,
            "image_repository": "lean-eval-historical-public-v1",
            "image_tag": publication["registry_tag"],
            "image_manifest_digest": MANIFEST,
            "runtime_boundary": boundary,
            "health": {
                "errors": [],
                "instances": {"healthy": 1, "failed": 0, "starting": 0, "scheduling": 0},
            },
        }
        health = {
            "status": "ok",
            "service": "lean-eval-replay-executor",
            "environment": "staging",
            "deployed_commit": CONTROLLER_COMMIT,
            "replay_enabled": False,
            "staging_acceptance_enabled": True,
            "staging_memory_limit_bytes": 12_884_901_888,
            "production_memory_gate_bytes": 12_884_901_888,
            "reviewed_execution_profile_digest": "0" * 64,
            "reviewed_measurement_config_digest": "0" * 64,
            "reviewed_vm_image_digest": MANIFEST,
        }
        probes = []
        for number in (1, 2):
            probes.append(
                {
                    "schema_version": 1,
                    "service": "lean-eval-replay-executor",
                    "environment": "staging",
                    "request_id": f"01234567-89ab-7cde-8fab-{number:012d}",
                    "runner_nonce": "7" * 64,
                    "archive_ciphertext_sha256": str(number) * 64,
                    "marker_sha256": str(number + 2) * 64,
                    "network_policy": "disabled",
                    "network_probe": "blocked",
                    "destruction": "confirmed",
                    "architecture": "x86_64",
                    "kernel_release": "fixture-kernel",
                    "cpu_model": "fixture-cpu",
                    "staging_memory_limit_bytes": 12_884_901_888,
                    "production_memory_gate_bytes": 12_884_901_888,
                }
            )
        staging = {
            "schema_version": 2,
            "kind": "historical_public_staging_qualification_evidence",
            "qualification_status": "unqualified",
            "benchmark_commit": BENCHMARK_COMMIT,
            "controller_source_commit": CONTROLLER_COMMIT,
            "image_source_commit": IMAGE_COMMIT,
            "registry_manifest_digest": MANIFEST,
            "health": health,
            "runtime_boundary": boundary,
            "probes": probes,
        }
        values = {
            "candidate": candidate,
            "publication": publication,
            "rollout": rollout,
            "staging": staging,
        }
        paths = {"image_source": image_source, "preparation_source": preparation_source}
        for name, value in values.items():
            paths[name] = root / f"{name}.json"
            write(paths[name], value)
        paths["candidate_zip"] = root / "candidate.zip"
        paths["staging_zip"] = root / "staging.zip"
        zip_members(paths["candidate_zip"], {
            "candidate-binding.json": paths["candidate"],
            "historical-image-publication.json": paths["publication"],
            "historical-qualification-rollout.json": paths["rollout"],
        })
        zip_members(paths["staging_zip"], {
            "historical-public-staging-qualification.json": paths["staging"],
        })
        provenance["artifacts"][0]["archive_sha256"] = authority.digest_file(
            paths["candidate_zip"], "candidate fixture ZIP"
        )
        provenance["artifacts"][0]["size_in_bytes"] = paths["candidate_zip"].stat().st_size
        provenance["artifacts"][1]["archive_sha256"] = authority.digest_file(
            paths["staging_zip"], "staging fixture ZIP"
        )
        provenance["artifacts"][1]["size_in_bytes"] = paths["staging_zip"].stat().st_size
        paths["provenance"] = root / "provenance.json"
        write(paths["provenance"], provenance)
        return paths

    def repack(self, paths: dict[str, pathlib.Path], file_name: str) -> None:
        provenance = json.loads(paths["provenance"].read_text())
        if file_name in {"candidate", "publication", "rollout"}:
            zip_members(paths["candidate_zip"], {
                "candidate-binding.json": paths["candidate"],
                "historical-image-publication.json": paths["publication"],
                "historical-qualification-rollout.json": paths["rollout"],
            })
            provenance["artifacts"][0]["archive_sha256"] = authority.digest_file(
                paths["candidate_zip"], "candidate fixture ZIP"
            )
            provenance["artifacts"][0]["size_in_bytes"] = paths["candidate_zip"].stat().st_size
        else:
            zip_members(paths["staging_zip"], {
                "historical-public-staging-qualification.json": paths["staging"],
            })
            provenance["artifacts"][1]["archive_sha256"] = authority.digest_file(
                paths["staging_zip"], "staging fixture ZIP"
            )
            provenance["artifacts"][1]["size_in_bytes"] = paths["staging_zip"].stat().st_size
        write(paths["provenance"], provenance)

    def arguments(self, root: pathlib.Path, paths: dict[str, pathlib.Path]) -> argparse.Namespace:
        output = root / "prepared"
        return argparse.Namespace(
            plan=str(PLAN),
            matrix=str(MATRIX),
            runner_contract=str(RUNNER_CONTRACT),
            qualification_contract=str(QUALIFICATION_CONTRACT),
            provenance=str(paths["provenance"]),
            candidate_artifact_zip=str(paths["candidate_zip"]),
            staging_artifact_zip=str(paths["staging_zip"]),
            preparation_source_root=str(paths["preparation_source"]),
            image_source_root=str(paths["image_source"]),
            plan_commit=CONTROLLER_COMMIT,
            benchmark_commit=BENCHMARK_COMMIT,
            request_id=REQUEST_ID,
            result_id=RESULT_ID,
            output_directory=str(output),
        )

    def prepare(self, root: pathlib.Path, paths: dict[str, pathlib.Path]) -> pathlib.Path:
        args = self.arguments(root, paths)
        authority.prepare(args)
        return pathlib.Path(args.output_directory)

    def test_successful_v2_artifacts_freeze_profile_but_keep_activation_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = self.prepare(root, self.fixture(root))
            preparation = json.loads(
                (output / "historical-public-authority-preparation.json").read_text()
            )
            binding = preparation["qualification_profile"]
            profile_path = output / binding["path"]
            profile_raw = profile_path.read_bytes()
            profile = json.loads(profile_raw)
            self.assertEqual(preparation["activation_status"], "blocked")
            self.assertEqual(profile["qualification_status"], "qualified")
            self.assertEqual(authority.sha256_bytes(profile_raw), binding["sha256"])
            self.assertEqual(
                authority.config_digest(
                    "lean-eval-replay-execution-profile-v1", profile["execution_profile"]
                ),
                profile["execution_profile_digest"],
            )
            from replay_orchestrator import config_digest as orchestrator_config_digest

            self.assertIs(authority.config_digest, orchestrator_config_digest)
            self.assertEqual(
                orchestrator_config_digest(
                    "lean-eval-replay-measurement-config-v1", profile["measurement_config"]
                ),
                profile["measurement_config_digest"],
            )
            self.assertEqual(
                profile["measurement_config_digest"],
                "2dfc898270b83b6c99689e3f551a102c5e76636ec9f469a408498080e3e45945",
            )
            self.assertEqual(profile["controller_source_commit"], CONTROLLER_COMMIT)
            self.assertEqual(profile["image_source_commit"], IMAGE_COMMIT)
            self.assertEqual(profile["registry_manifest_digest"], MANIFEST)
            self.assertEqual(preparation["selection"]["result_id"], RESULT_ID)
            self.assertEqual(
                preparation["authority_event_payload"]["authority_sha256"],
                authority.PLAN_SHA256,
            )
            encoded = json.dumps(preparation) + json.dumps(profile)
            for forbidden in (
                "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "ciphertext_base64",
                "plaintext_identity_base64", "submission_id", "archive_path",
            ):
                self.assertNotIn(forbidden, encoded)

    def test_finalization_requires_committed_profile_and_emits_exact_causal_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = self.prepare(root, self.fixture(root))
            preparation_path = output / "historical-public-authority-preparation.json"
            preparation = json.loads(preparation_path.read_text())
            profile_path = output / preparation["qualification_profile"]["path"]
            finalized = root / "finalized"
            times = [
                "2026-08-24T20:00:01.000Z",
                "2026-08-24T20:00:02.000Z",
                "2026-08-24T20:00:03.000Z",
            ]
            ids = [event_id(value, index) for index, value in enumerate(times, start=1)]

            def validate_state(_root: pathlib.Path, candidates: list[dict[str, object]]):
                enqueue = candidates[-1]
                return "2026-08-24T19:59:59.000Z", {
                    "tasks": [{
                        "replay_task_id": enqueue["subject_id"],
                        "result_id": enqueue["payload"]["result_id"],
                        "qualification_event_id": candidates[1]["event_id"],
                        "event_id": enqueue["event_id"],
                        "status": "queued",
                        "attempt": 0,
                    }]
                }

            with (
                mock.patch.object(authority, "verify_qualification_blob"),
                mock.patch.object(authority, "load_and_validate_pinned_state", side_effect=validate_state),
            ):
                authority.finalize(
                    argparse.Namespace(
                        preparation=str(preparation_path),
                        profile=str(profile_path),
                        qualification_commit="9" * 40,
                        qualification_repository_root=str(root / "qualification-repository"),
                        state_root=str(root / "state"),
                        authority_event_id=ids[0],
                        authority_occurred_at=times[0],
                        qualification_event_id=ids[1],
                        qualification_occurred_at=times[1],
                        enqueue_event_id=ids[2],
                        enqueue_occurred_at=times[2],
                        output_directory=str(finalized),
                    )
                )
            event_paths = sorted((finalized / "events").glob("*/*.json"))
            events = [json.loads(path.read_text()) for path in event_paths]
            self.assertEqual(
                [event["event_type"] for event in events],
                [
                    "historical_result.replay_authorized",
                    "historical_result.replay_profile_qualified",
                    "replay.enqueued",
                ],
            )
            self.assertIsNone(events[0]["causation_event_id"])
            self.assertEqual(events[1]["causation_event_id"], events[0]["event_id"])
            self.assertEqual(events[2]["causation_event_id"], events[1]["event_id"])
            self.assertEqual(events[1]["payload"]["qualification_commit"], "9" * 40)
            self.assertEqual(events[2]["subject_id"], preparation["ordinary_replay_enqueue"]["replay_task_id"])

            with (
                mock.patch.object(authority, "verify_qualification_blob"),
                mock.patch.object(authority, "load_and_validate_pinned_state", side_effect=validate_state),
                self.assertRaisesRegex(authority.PreparationError, "UUIDv7 timestamp"),
            ):
                authority.finalize(
                    argparse.Namespace(
                        preparation=str(preparation_path), profile=str(profile_path),
                        qualification_commit="9" * 40,
                        qualification_repository_root=str(root / "qualification-repository"),
                        state_root=str(root / "state"),
                        authority_event_id=event_id("2026-08-24T20:00:01.500Z", 1),
                        authority_occurred_at=times[0],
                        qualification_event_id=ids[1], qualification_occurred_at=times[1],
                        enqueue_event_id=ids[2], enqueue_occurred_at=times[2],
                        output_directory=str(root / "wrong-time"),
                    )
                )

    def test_v2_seam_rejects_replay_enablement_source_and_manifest_drift(self) -> None:
        mutations = (
            ("candidate", lambda value: value["vars"].update(REPLAY_ENABLED="true")),
            ("candidate", lambda value: value.update(image_source_commit="f" * 40)),
            ("publication", lambda value: value.update(registry_manifest_digest="sha256:" + "f" * 64)),
            ("rollout", lambda value: value.update(image_manifest_digest="sha256:" + "e" * 64)),
            ("staging", lambda value: value["health"].update(replay_enabled=True)),
            ("staging", lambda value: value["probes"][1].update(cpu_model="different-cpu")),
            ("staging", lambda value: value["probes"][1].update(request_id=value["probes"][0]["request_id"])),
        )
        for file_name, mutate in mutations:
            with self.subTest(file_name=file_name), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                paths = self.fixture(root)
                value = json.loads(paths[file_name].read_text())
                mutate(value)
                write(paths[file_name], value)
                self.repack(paths, file_name)
                with self.assertRaises(authority.PreparationError):
                    self.prepare(root, paths)

    def test_image_source_bytes_and_resumed_build_metrics_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            (paths["image_source"] / "Dockerfile.historical-public-replay").write_text("changed\n")
            with self.assertRaisesRegex(authority.PreparationError, "Git checkout"):
                self.prepare(root, paths)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            publication = json.loads(paths["publication"].read_text())
            publication["publication_mode"] = "resumed"
            with self.assertRaisesRegex(authority.PreparationError, "resumed"):
                write(paths["publication"], publication)
                self.repack(paths, "publication")
                self.prepare(root, paths)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            dockerfile = paths["image_source"] / "Dockerfile.historical-public-replay"
            subprocess.run(
                ["git", "-C", str(paths["image_source"]), "update-index", "--skip-worktree", "Dockerfile.historical-public-replay"],
                check=True,
            )
            dockerfile.write_text("hostile hidden worktree bytes\n")
            output = self.prepare(root, paths)
            self.assertTrue((output / "historical-public-authority-preparation.json").is_file())

    def test_checkout_remote_and_cleanliness_proofs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            preparation_source = paths["preparation_source"]
            image_source = paths["image_source"]

            subprocess.run(
                [
                    "git", "-C", str(preparation_source), "remote", "set-url", "origin",
                    "https://github.com/leanprover/lean-eval-submissions",
                ],
                check=True,
            )
            with self.assertRaisesRegex(
                authority.PreparationError, "preparation source Git checkout remote"
            ):
                self.prepare(root, paths)
            subprocess.run(
                [
                    "git", "-C", str(preparation_source), "remote", "set-url", "origin",
                    "https://github.com/leanprover/lean-eval-submissions.git",
                ],
                check=True,
            )

            hostile = preparation_source / "hostile-untracked-input"
            hostile.write_text("must be rejected\n")
            with self.assertRaisesRegex(
                authority.PreparationError, "preparation source Git checkout cleanliness"
            ):
                self.prepare(root, paths)
            hostile.unlink()

            subprocess.run(
                [
                    "git", "-C", str(image_source), "remote", "set-url", "origin",
                    "https://example.invalid/lean-eval-submissions.git",
                ],
                check=True,
            )
            with self.assertRaisesRegex(
                authority.PreparationError, "image source Git checkout remote"
            ):
                self.prepare(root, paths)

    def test_checkout_commit_and_tree_proofs_are_distinct_and_labelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            preparation_source = paths["preparation_source"]
            with self.assertRaisesRegex(
                authority.PreparationError, "preparation source Git checkout commit"
            ):
                authority.verify_checkout(
                    preparation_source,
                    authority.SUBMISSIONS_REPOSITORY,
                    "f" * 40,
                    label="preparation source",
                )
            subprocess.run(
                [
                    "git", "-C", str(preparation_source), "remote", "set-url", "origin",
                    "https://github.com/leanprover/lean-eval-state.git",
                ],
                check=True,
            )
            with self.assertRaisesRegex(
                authority.PreparationError, "State source Git checkout tree"
            ):
                authority.verify_checkout(
                    preparation_source,
                    "leanprover/lean-eval-state",
                    CONTROLLER_COMMIT,
                    "f" * 40,
                    label="State source",
                )

    def test_provenance_accepts_reused_job_artifacts_but_rejects_pre_run_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            run = {
                "id": 123,
                "run_attempt": 2,
                "event": "workflow_dispatch",
                "head_sha": CONTROLLER_COMMIT,
                "head_branch": "lean-eval-dispatch/" + CONTROLLER_COMMIT,
                "path": authority.WORKFLOW_PATH,
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-08-24T09:00:00Z",
                "run_started_at": "2026-08-24T10:00:00Z",
                "updated_at": "2026-08-24T11:00:00Z",
            }
            artifact = lambda artifact_id, name, digest, created_at="2026-08-24T10:30:00Z": {
                "id": artifact_id,
                "name": name,
                "expired": False,
                "digest": "sha256:" + digest,
                "created_at": created_at,
                "workflow_run": {
                    "id": 123,
                    "head_sha": CONTROLLER_COMMIT,
                    "head_branch": "lean-eval-dispatch/" + CONTROLLER_COMMIT,
                },
                "size_in_bytes": 1000,
            }
            paths = {
                "run": root / "run.json",
                "candidate": root / "candidate.json",
                "staging": root / "staging.json",
            }
            write(paths["run"], run)
            write(
                paths["candidate"],
                artifact(
                    111,
                    "historical-public-image-candidate",
                    "4" * 64,
                    "2026-08-24T09:30:00Z",
                ),
            )
            write(paths["staging"], artifact(222, "historical-public-staging-qualification", "5" * 64))
            args = argparse.Namespace(
                run_metadata=str(paths["run"]),
                candidate_artifact_metadata=str(paths["candidate"]),
                staging_artifact_metadata=str(paths["staging"]),
                run_id=123,
                run_attempt=2,
                candidate_artifact_id=111,
                staging_artifact_id=222,
                controller_source_commit=CONTROLLER_COMMIT,
                image_source_commit=IMAGE_COMMIT,
                output=str(root / "provenance.json"),
            )
            authority.write_provenance(args)
            provenance, _ = authority.load_canonical(root / "provenance.json", "fixture provenance")
            self.assertEqual(authority.validate_provenance(provenance), provenance)
            self.assertEqual(provenance["schema_version"], 2)
            self.assertEqual(provenance["workflow_run_created_at"], run["created_at"])
            self.assertEqual(provenance["workflow_run_started_at"], run["run_started_at"])
            failed = copy.deepcopy(run)
            failed["conclusion"] = "failure"
            write(root / "failed.json", failed)
            args.run_metadata = str(root / "failed.json")
            args.output = str(root / "failed-provenance.json")
            with self.assertRaisesRegex(authority.PreparationError, "successful"):
                authority.write_provenance(args)
            stale = artifact(222, "historical-public-staging-qualification", "5" * 64)
            stale["created_at"] = "2026-08-24T08:59:59Z"
            write(root / "stale-artifact.json", stale)
            args.run_metadata = str(paths["run"])
            args.staging_artifact_metadata = str(root / "stale-artifact.json")
            args.output = str(root / "stale-provenance.json")
            with self.assertRaisesRegex(authority.PreparationError, "successful run"):
                authority.write_provenance(args)

    def test_outputs_are_create_only_and_finalization_rejects_profile_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            self.prepare(root, paths)
            with self.assertRaisesRegex(authority.PreparationError, "overwrite"):
                self.prepare(root, paths)
            preparation_path = root / "prepared/historical-public-authority-preparation.json"
            preparation = json.loads(preparation_path.read_text())
            profile_path = root / "prepared" / preparation["qualification_profile"]["path"]
            profile = json.loads(profile_path.read_text())
            profile["registry_tag"] = "f" * 40 + "-" + "e" * 40
            changed = root / "changed-profile.json"
            write(changed, profile)
            with self.assertRaises(authority.PreparationError):
                authority.finalize(
                    argparse.Namespace(
                        preparation=str(preparation_path), profile=str(changed),
                        qualification_commit="9" * 40,
                        qualification_repository_root=str(root / "qualification-repository"),
                        state_root=str(root / "state"),
                        authority_event_id=event_id("2026-08-24T20:00:01.000Z", 1),
                        authority_occurred_at="2026-08-24T20:00:01.000Z",
                        qualification_event_id=event_id("2026-08-24T20:00:02.000Z", 2),
                        qualification_occurred_at="2026-08-24T20:00:02.000Z",
                        enqueue_event_id=event_id("2026-08-24T20:00:03.000Z", 3),
                        enqueue_occurred_at="2026-08-24T20:00:03.000Z",
                        output_directory=str(root / "finalized"),
                    )
                )

    def test_profile_commit_must_contain_exact_digest_path_blob(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "repository"
            root.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
            subprocess.run(
                ["git", "-C", str(root), "remote", "add", "origin", "https://github.com/leanprover/lean-eval-submissions.git"],
                check=True,
            )
            subprocess.run(["git", "-C", str(root), "config", "user.name", "fixture"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.com"], check=True)
            relative = "evidence/public-replay/profiles/" + "a" * 64 + ".json"
            blob = authority.canonical({"fixture": True})
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(blob)
            subprocess.run(["git", "-C", str(root), "add", relative], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "fixture"], check=True)
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            authority.verify_qualification_blob(root, commit, relative, blob)
            with self.assertRaisesRegex(authority.PreparationError, "exact profile"):
                authority.verify_qualification_blob(root, commit, relative, authority.canonical({"fixture": False}))

    def test_published_schemas_close_nested_profile_and_append_inputs(self) -> None:
        schemas = {
            name: json.loads((ROOT / "schemas" / name).read_text())
            for name in (
                "replay-execution-profile-v1.schema.json",
                "historical-public-profile-qualification-v1.schema.json",
                "historical-public-authority-preparation-v2.schema.json",
            )
        }
        registry = Registry().with_resources(
            [(schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()]
        )
        for schema in schemas.values():
            jsonschema.Draft202012Validator.check_schema(schema)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = self.prepare(root, self.fixture(root))
            preparation = json.loads((output / "historical-public-authority-preparation.json").read_text())
            profile = json.loads((output / preparation["qualification_profile"]["path"]).read_text())
            profile_validator = jsonschema.Draft202012Validator(
                schemas["historical-public-profile-qualification-v1.schema.json"],
                registry=registry,
            )
            preparation_validator = jsonschema.Draft202012Validator(
                schemas["historical-public-authority-preparation-v2.schema.json"],
                registry=registry,
            )
            profile_validator.validate(profile)
            preparation_validator.validate(preparation)
            changed_profile = copy.deepcopy(profile)
            changed_profile["measurement_config"]["unreviewed"] = True
            self.assertTrue(list(profile_validator.iter_errors(changed_profile)))
            changed_preparation = copy.deepcopy(preparation)
            changed_preparation["ordinary_replay_enqueue"]["payload"]["source"] = "private"
            self.assertTrue(list(preparation_validator.iter_errors(changed_preparation)))

    def test_output_root_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            target = root / "target"
            target.mkdir()
            (root / "prepared").symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(authority.PreparationError, "overwrite"):
                self.prepare(root, paths)

    def test_exact_artifact_zip_digest_member_set_and_file_types_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            raw = bytearray(paths["candidate_zip"].read_bytes())
            raw[-1] ^= 1
            paths["candidate_zip"].write_bytes(raw)
            with self.assertRaisesRegex(authority.PreparationError, "archive digest"):
                self.prepare(root, paths)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            with zipfile.ZipFile(paths["candidate_zip"], "a") as archive:
                archive.writestr("unexpected.json", authority.canonical({"unexpected": True}))
            provenance = json.loads(paths["provenance"].read_text())
            provenance["artifacts"][0]["archive_sha256"] = authority.digest_file(
                paths["candidate_zip"], "candidate fixture ZIP"
            )
            provenance["artifacts"][0]["size_in_bytes"] = paths["candidate_zip"].stat().st_size
            write(paths["provenance"], provenance)
            with self.assertRaisesRegex(authority.PreparationError, "member set"):
                self.prepare(root, paths)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            with zipfile.ZipFile(paths["candidate_zip"], "w") as archive:
                for name, source in {
                    "candidate-binding.json": paths["candidate"],
                    "historical-image-publication.json": paths["publication"],
                    "historical-qualification-rollout.json": paths["rollout"],
                }.items():
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (
                        (stat.S_IFLNK | 0o777) if name == "candidate-binding.json"
                        else (stat.S_IFREG | 0o600)
                    ) << 16
                    archive.writestr(info, source.read_bytes())
            provenance = json.loads(paths["provenance"].read_text())
            provenance["artifacts"][0]["archive_sha256"] = authority.digest_file(
                paths["candidate_zip"], "candidate fixture ZIP"
            )
            provenance["artifacts"][0]["size_in_bytes"] = paths["candidate_zip"].stat().st_size
            write(paths["provenance"], provenance)
            with self.assertRaisesRegex(authority.PreparationError, "unsafe member"):
                self.prepare(root, paths)

    def test_plan_and_contract_inputs_must_be_exact_commit_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            changed_plan = root / "changed-plan.json"
            plan = json.loads(PLAN.read_text())
            plan["pending_request_count"] += 1
            write(changed_plan, plan)
            args = self.arguments(root, paths)
            args.plan = str(changed_plan)
            with self.assertRaises(authority.PreparationError):
                authority.prepare(args)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            plan_worktree = paths["preparation_source"] / authority.PLAN_PATH
            subprocess.run(
                ["git", "-C", str(paths["preparation_source"]), "update-index", "--skip-worktree", authority.PLAN_PATH],
                check=True,
            )
            plan_worktree.write_text("hidden hostile plan worktree bytes\n")
            output = self.prepare(root, paths)
            self.assertTrue((output / "historical-public-authority-preparation.json").is_file())

    def test_workflow_is_read_only_exact_zip_preparation_without_finalize(self) -> None:
        workflow = (
            ROOT / ".github/workflows/historical-public-authority-preparation.yml"
        ).read_text()
        self.assertIn("actions: read\n  contents: read", workflow)
        self.assertNotIn("id-token: write", workflow)
        self.assertNotIn("environment:", workflow)
        self.assertIn("actions/artifacts/$CANDIDATE_ARTIFACT_ID/zip", workflow)
        self.assertIn("actions/artifacts/$STAGING_ARTIFACT_ID/zip", workflow)
        self.assertIn('"$api/actions/runs/$RUN_ID"', workflow)
        self.assertNotIn("/attempts/$RUN_ATTEMPT", workflow)
        self.assertEqual(workflow.count("--max-filesize 4194304"), 2)
        self.assertIn("--require-hashes -r requirements-jsonschema-workflow.txt", workflow)
        self.assertIn('--candidate-artifact-zip "$RUNNER_TEMP/candidate-artifact.zip"', workflow)
        self.assertIn('--staging-artifact-zip "$RUNNER_TEMP/staging-artifact.zip"', workflow)
        self.assertIn('--preparation-source-root .', workflow)
        self.assertIn('--image-source-root "$RUNNER_TEMP/image-source"', workflow)
        canonical_remote = (
            "git remote set-url origin \\\n"
            "            https://github.com/leanprover/lean-eval-submissions.git"
        )
        observed_remote = "observed_origin=$(git config --get-all remote.origin.url)"
        self.assertIn(observed_remote, workflow)
        self.assertIn(canonical_remote, workflow)
        self.assertLess(workflow.index(observed_remote), workflow.index(canonical_remote))
        self.assertLess(workflow.index(canonical_remote), workflow.index('python "$CONTROLLER" prepare'))
        self.assertNotIn(" download-artifact@", workflow)
        self.assertNotIn(" finalize ", workflow)
        for forbidden in (
            "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "aws-actions/",
            "wrangler deploy", "containers push", "git push", "gh pr",
            "REPLAY_ENABLED=true", "INTAKE_ENABLED=true", "PUBLICATION_ENABLED=true",
        ):
            self.assertNotIn(forbidden, workflow)


class HistoricalPublicAuthorityBatchFinalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix = json.loads(MATRIX.read_text())
        cls.template = json.loads(
            (
                ROOT
                / "evidence/public-replay/profiles/"
                "0886d3624de67d0ba1cb00657f66c5f7304743773a024509fceda6ae8f4ff660.json"
            ).read_text()
        )

    def synthetic_profile(
        self, entry: dict[str, object], *, salt: str = "primary"
    ) -> dict[str, object]:
        profile = copy.deepcopy(self.template)
        benchmark_commit = str(entry["benchmark_commit"])
        profile_lock = entry["profile_lock"]
        self.assertIsInstance(profile_lock, dict)
        image_digest = "sha256:" + hashlib.sha256(
            f"batch-profile-fixture\0{benchmark_commit}\0{salt}".encode()
        ).hexdigest()
        execution_profile = {
            "schema_version": 1,
            "runner_profile": profile_lock["runner_profile"],
            "vm_image_digest": image_digest,
            "toolchain": profile_lock["toolchain"],
            "go_toolchain": profile_lock["go_toolchain"],
            "rust_toolchain": profile_lock["rust_toolchain"],
            "cpu_model": "batch-fixture-cpu",
            "architecture": "x86_64",
            "kernel_release": "batch-fixture-kernel",
            "cache_state": profile_lock["cache_state"],
            "measurement_command": profile_lock["measurement_command"],
            "components": profile_lock["components"],
        }
        profile.update(
            benchmark_commit=benchmark_commit,
            benchmark_tree=entry["benchmark_tree"],
            plan_commit=authority.PLAN_COMMIT,
            plan_path=authority.PLAN_PATH,
            plan_sha256=authority.PLAN_SHA256,
            profile_matrix_path=authority.MATRIX_PATH,
            profile_matrix_sha256=authority.MATRIX_SHA256,
            runner_contract_path=authority.RUNNER_CONTRACT_PATH,
            runner_contract_sha256=authority.RUNNER_CONTRACT_SHA256,
            qualification_contract_path=authority.QUALIFICATION_CONTRACT_PATH,
            qualification_contract_sha256=authority.QUALIFICATION_CONTRACT_SHA256,
            controller_source_commit=authority.PLAN_COMMIT,
            image_source_commit=authority.PLAN_COMMIT,
            registry_tag=f"{benchmark_commit}-{authority.PLAN_COMMIT}",
            registry_manifest_digest=image_digest,
            execution_profile=execution_profile,
            execution_profile_digest=authority.config_digest(
                "lean-eval-replay-execution-profile-v1", execution_profile
            ),
        )
        return profile

    def exact_git_inputs(self) -> tuple[dict[str, bytes], list[str]]:
        blobs = {
            authority.PLAN_PATH: PLAN.read_bytes(),
            authority.MATRIX_PATH: MATRIX.read_bytes(),
        }
        paths = []
        for entry in self.matrix["images"]:
            profile = self.synthetic_profile(entry)
            relative = (
                "evidence/public-replay/profiles/"
                f"{profile['execution_profile_digest']}.json"
            )
            blobs[relative] = authority.canonical(profile)
            paths.append(relative)
        return blobs, paths

    def load_inputs(
        self, blobs: dict[str, bytes], paths: list[str]
    ) -> tuple[dict[str, object], dict[str, tuple[dict[str, object], bytes, str]], list[object]]:
        def git(_root: pathlib.Path, *arguments: str, **_kwargs: object) -> bytes:
            if arguments[:4] == ("ls-tree", "-r", "--name-only", "f" * 40):
                return ("\n".join(paths) + "\n").encode()
            return b""

        def blob(_root: pathlib.Path, _commit: str, relative: str) -> bytes | None:
            return blobs.get(relative)

        with (
            mock.patch.object(authority, "verify_checkout"),
            mock.patch.object(authority, "_git", side_effect=git),
            mock.patch.object(authority, "_git_optional_blob", side_effect=blob),
        ):
            return authority.load_batch_inputs(pathlib.Path("fixture"), "f" * 40)

    def materialized_queue(
        self, _root: pathlib.Path, events: list[dict[str, object]]
    ) -> tuple[str, dict[str, object]]:
        self.assertEqual(len(events), authority.BATCH_EVENT_COUNT)
        tasks = []
        for index in range(0, len(events), 3):
            authorized, qualified, enqueued = events[index : index + 3]
            self.assertEqual(
                [authorized["event_type"], qualified["event_type"], enqueued["event_type"]],
                [
                    "historical_result.replay_authorized",
                    "historical_result.replay_profile_qualified",
                    "replay.enqueued",
                ],
            )
            self.assertEqual(qualified["causation_event_id"], authorized["event_id"])
            self.assertEqual(enqueued["causation_event_id"], qualified["event_id"])
            tasks.append(
                {
                    "replay_task_id": enqueued["subject_id"],
                    **enqueued["payload"],
                    **authorized["payload"],
                    "authority_event_id": authorized["event_id"],
                    "authorized_at": authorized["occurred_at"],
                    **qualified["payload"],
                    "qualification_event_id": qualified["event_id"],
                    "qualified_at": qualified["occurred_at"],
                    "status": "queued",
                    "attempt": 0,
                    "event_id": enqueued["event_id"],
                    "occurred_at": enqueued["occurred_at"],
                }
            )
        tasks.sort(key=lambda task: task["replay_task_id"])
        return "2026-08-26T05:59:59.999Z", {
            "schema_version": 2,
            "environment": "production",
            "source_event_count": (
                authority.PINNED_STATE_EVENT_COUNT + authority.BATCH_EVENT_COUNT
            ),
            "source_digest": "0" * 64,
            "tasks": tasks,
        }

    def test_batch_finalization_is_complete_deterministic_and_create_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            blobs, paths = self.exact_git_inputs()
            matrix, profiles, selections = self.load_inputs(blobs, paths)
            events, tasks = authority.build_batch_events(
                matrix,
                selections,
                profiles,
                "f" * 40,
                "2026-08-26T06:00:00.000Z",
                "5" * 64,
            )
            self.assertEqual((len(profiles), len(selections), len(events), len(tasks)), (35, 194, 582, 194))
            args = argparse.Namespace(
                qualification_commit="f" * 40,
                qualification_repository_root="fixture",
                state_root="fixture-state",
                first_occurred_at="2026-08-26T06:00:00.000Z",
                event_id_seed="5" * 64,
                output_directory=str(root / "candidate"),
            )
            with (
                mock.patch.object(
                    authority,
                    "load_batch_inputs",
                    return_value=(matrix, profiles, selections),
                ),
                mock.patch.object(
                    authority,
                    "load_and_validate_pinned_state",
                    side_effect=self.materialized_queue,
                ),
            ):
                authority.finalize_batch(args)
            output = pathlib.Path(args.output_directory)
            event_paths = sorted((output / "events").glob("*/*.json"))
            self.assertEqual(len(event_paths), authority.BATCH_EVENT_COUNT)
            manifest = json.loads(
                (output / "historical-public-state-append-batch-candidate.json").read_text()
            )
            self.assertEqual(
                (
                    manifest["profile_count"],
                    manifest["request_count"],
                    manifest["result_count"],
                    manifest["event_count"],
                    manifest["replay_task_count"],
                ),
                (35, 128, 194, 582, 194),
            )
            repeated, _ = authority.build_batch_events(
                matrix, selections, profiles, "f" * 40,
                args.first_occurred_at, args.event_id_seed,
            )
            self.assertEqual(
                [authority.canonical_state_event(event) for event in events],
                [authority.canonical_state_event(event) for event in repeated],
            )
            with (
                mock.patch.object(
                    authority,
                    "load_batch_inputs",
                    return_value=(matrix, profiles, selections),
                ),
                mock.patch.object(
                    authority,
                    "load_and_validate_pinned_state",
                    side_effect=self.materialized_queue,
                ),
                self.assertRaisesRegex(authority.PreparationError, "overwrite"),
            ):
                authority.finalize_batch(args)

    def test_batch_requires_every_exact_fully_valid_profile(self) -> None:
        blobs, paths = self.exact_git_inputs()
        with self.assertRaisesRegex(authority.PreparationError, "exactly 35"):
            self.load_inputs(blobs, paths[:-1])
        invalid = json.loads(blobs[paths[0]])
        invalid["registry_tag"] = "invalid"
        blobs[paths[0]] = authority.canonical(invalid)
        with self.assertRaisesRegex(authority.PreparationError, "profile is invalid"):
            self.load_inputs(blobs, paths)

    def test_batch_translates_matrix_profile_binding_failure(self) -> None:
        blobs, paths = self.exact_git_inputs()
        matrix, profiles, selections = self.load_inputs(blobs, paths)
        benchmark_commit = selections[0][2]["benchmark_commit"]
        profile, raw, relative = profiles[benchmark_commit]
        changed = copy.deepcopy(profile)
        changed["execution_profile"]["toolchain"] = "leanprover/lean4:invalid"
        profiles[benchmark_commit] = (changed, raw, relative)
        with self.assertRaisesRegex(authority.PreparationError, "binding changed"):
            authority.build_batch_events(
                matrix,
                selections,
                profiles,
                "f" * 40,
                "2026-08-26T06:00:00.000Z",
                "5" * 64,
            )

    def test_seeded_uuid7_is_deterministic_and_time_bound(self) -> None:
        timestamp = authority.timestamp_ms("2026-08-26T06:00:00.000Z")
        first = authority.deterministic_batch_uuid7(
            timestamp,
            "a" * 64,
            "r2_" + "b" * 64,
            "historical_result.replay_authorized",
        )
        self.assertEqual(
            first,
            authority.deterministic_batch_uuid7(
                timestamp,
                "a" * 64,
                "r2_" + "b" * 64,
                "historical_result.replay_authorized",
            ),
        )
        self.assertRegex(first, authority.UUID7)
        self.assertEqual(authority.uuid7_timestamp_ms(first), timestamp)
        self.assertNotEqual(
            first,
            authority.deterministic_batch_uuid7(
                timestamp,
                "c" * 64,
                "r2_" + "b" * 64,
                "historical_result.replay_authorized",
            ),
        )


if __name__ == "__main__":
    unittest.main()
