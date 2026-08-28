from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import historical_private_replay_controller as controller  # noqa: E402
from prepare_historical_private_replay import entry_sha256  # noqa: E402
from key_capability_contract import archive_file_key_id, capability_digest  # noqa: E402
from replay_orchestrator import config_digest, replay_task_id  # noqa: E402
from results_schema import result_id  # noqa: E402


def recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(recursive_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(recursive_keys(item) for item in value))
    return set()


class Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = pathlib.Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.repository)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "remote",
                "add",
                "origin",
                "https://github.com/leanprover/lean-eval-submissions.git",
            ],
            check=True,
        )
        self.environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        self.owner = "private-owner"
        self.model = "Archived Private Model"
        self.problem = "private_problem"
        self.result_id = result_id(self.owner, self.model, self.problem, 1)
        self.archive_submission_id = "019a0000-0000-7000-8000-000000000040"
        self.measurement = {
            "schema_version": 1,
            "memory_limit_bytes": 12 * 1024**3,
            "wall_time_limit_ms": 7_200_000,
            "retired_instructions": {
                "required": False,
                "perf_event": "instructions:u",
            },
        }
        self.execution_profile = {
            "schema_version": 1,
            "runner_profile": "historical-private-v1",
            "vm_image_digest": "sha256:" + "a" * 64,
            "toolchain": "leanprover/lean4:v4.19.0-rc1",
            "architecture": "x86_64",
            "cpu_model": "fixture-cpu",
            "kernel_release": "6.8.0-fixture",
            "cache_state": "cold",
            "measurement_command": ["/opt/lean-eval/run"],
            "go_toolchain": "go1.24.2",
            "rust_toolchain": "rustc-1.85.0",
            "components": {
                name: {
                    "repository": repository,
                    "commit": character * 40,
                }
                for name, repository, character in (
                    ("comparator", "leanprover/comparator", "1"),
                    ("landrun", "leanprover/landrun", "2"),
                    ("lean4export", "leanprover/lean4export", "3"),
                    ("nanoda", "leanprover/nanoda", "4"),
                )
            },
        }
        self.measurement_digest = config_digest(
            "lean-eval-replay-measurement-config-v1", self.measurement
        )
        self.profile_digest = config_digest(
            "lean-eval-replay-execution-profile-v1", self.execution_profile
        )
        self.profile_core = {
            "benchmark_commit": "b" * 40,
            "benchmark_tree": "c" * 40,
            "toolchain": self.execution_profile["toolchain"],
            "lean_toolchain_blob_sha256": "d" * 64,
            "checker": "nanoda",
            "measurement_config_digest": self.measurement_digest,
            "measurement_config": self.measurement,
            "execution_profile": self.execution_profile,
            "execution_profile_digest": self.profile_digest,
        }
        source_blobs = {}
        for name, relative in controller.SOURCE_BLOB_PATHS.items():
            raw = f"fixture source blob: {name}\n".encode()
            path = self.repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            source_blobs[name] = {
                "path": relative,
                "sha256": controller.sha256_bytes(raw),
            }
        workflow_raw = b"name: Historical private qualification fixture\n"
        workflow_path = self.repository / controller.QUALIFICATION_WORKFLOW_PATH
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_bytes(workflow_raw)
        self.image_source_commit = self.commit("Add private replay image source fixture")
        self.profile = {
            "schema_version": 1,
            "kind": "historical_private_replay_profile_qualification",
            "qualification_status": "qualified",
            "image_family": "lean-eval-authoritative-private-replay-v1",
            "registry_repository": "lean-eval-authoritative",
            "registry_manifest_digest": self.execution_profile["vm_image_digest"],
            "image_source_repository": "leanprover/lean-eval-submissions",
            "image_source_commit": self.image_source_commit,
            "source_blobs": source_blobs,
            "qualification": {
                "workflow_repository": "leanprover/lean-eval-submissions",
                "workflow_commit": self.image_source_commit,
                "workflow_path": controller.QUALIFICATION_WORKFLOW_PATH,
                "workflow_sha256": controller.sha256_bytes(workflow_raw),
                "workflow_run_id": 123,
                "workflow_run_attempt": 1,
                "private_archive_probe": {
                    "archive_expectation_schema_version": 2,
                    "key_material_type": "age-file-key-v1",
                    "runner_entrypoint": "/opt/lean-eval/replay-authoritative",
                    "status": "passed",
                },
                "network_probe": "blocked",
            },
            **self.profile_core,
        }
        self.profile_raw = controller.canonical_bytes(self.profile)
        self.profile_sha256 = controller.sha256_bytes(self.profile_raw)
        self.profile_path = (
            f"evidence/private-replay/profiles/{self.profile_digest}.json"
        )
        profile_file = self.repository / self.profile_path
        profile_file.parent.mkdir(parents=True)
        profile_file.write_bytes(self.profile_raw)
        self.profile_commit = self.commit("Add exact private replay profile")

        self.entry = {
            "result_id": self.result_id,
            "historical_accepted_at": "2025-02-03T04:05:06Z",
            "owner_login": self.owner,
            "declared_model": self.model,
            "problem_id": self.problem,
            "statement_revision": 1,
            "benchmark_commit": "b" * 40,
            "results_path": f"results/{self.owner}.json",
            "result_file_sha256": "5" * 64,
            "result_tree_digest": "6" * 64,
            "crosswalk_entry_sha256": "7" * 64,
            "classification": "bound",
            "archive_submission_id": self.archive_submission_id,
            "archive_plan_entry_sha256": "8" * 64,
            "replay_profile_status": "profile_qualified",
            "execution_profile_digest": self.profile_digest,
        }
        self.authority = {
            "schema_version": 1,
            "kind": "historical_private_replay_plan",
            "results": {
                "repository": "leanprover/lean-eval-submissions",
                "commit": "9" * 40,
                "store_sha256": "a" * 64,
            },
            "crosswalk": {
                "repository": "leanprover/lean-eval-submissions",
                "commit": "0" * 40,
                "path": (
                    "evidence/historical-replay/private-crosswalks/"
                    + "e" * 64
                    + ".json"
                ),
                "sha256": "e" * 64,
            },
            "classification_counts": {"archive_not_found": 0, "bound": 1},
            "replay_readiness_counts": {
                "archive_not_found": 0,
                "profile_pending": 0,
                "profile_qualified": 1,
            },
            "profiles": {
                self.profile_digest: {
                    **{
                        key: copy.deepcopy(value)
                        for key, value in self.profile_core.items()
                        if key != "execution_profile_digest"
                    },
                    "private_profile": {
                        "repository": "leanprover/lean-eval-submissions",
                        "commit": self.profile_commit,
                        "path": self.profile_path,
                        "sha256": self.profile_sha256,
                    },
                }
            },
            "entries": [self.entry],
        }
        self.authority_raw = controller.canonical_bytes(self.authority)
        self.authority_sha256 = controller.sha256_bytes(self.authority_raw)
        self.authority_path = (
            f"evidence/private-replay/plans/{self.authority_sha256}.json"
        )
        authority_file = self.repository / self.authority_path
        authority_file.parent.mkdir(parents=True)
        authority_file.write_bytes(self.authority_raw)
        self.authority_commit = self.commit("Add exact private replay authority")
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", self.authority_commit,
            ],
            check=True,
        )

        self.audit_temporary = tempfile.TemporaryDirectory()
        self.audit = pathlib.Path(self.audit_temporary.name)
        subprocess.run(["git", "init", "-q", str(self.audit)], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.audit), "remote", "add", "origin",
                "https://github.com/leanprover/lean-eval-audit.git",
            ],
            check=True,
        )
        self.ciphertext = b"age-encryption.org/v1\nhistorical-private-fixture"
        self.ciphertext_sha256 = hashlib.sha256(self.ciphertext).hexdigest()
        self.sidecar = {
            "schema_version": 3,
            "submission_id": self.archive_submission_id,
            "submission_repo": "example/private-source",
            "submission_ref": "b" * 40,
            "submission_kind": "github_repo",
            "submission_public": False,
            "submitter": "example",
            "model": self.model,
            "size_bytes_plaintext_tar": 4096,
            "sha256_plaintext_tar": "4" * 64,
            "size_bytes_ciphertext": len(self.ciphertext),
            "sha256_ciphertext": self.ciphertext_sha256,
            "archived_at": "2026-08-23T03:40:44Z",
            "benchmark_commit": self.entry["benchmark_commit"],
            "archiver_workflow_run": (
                "https://github.com/leanprover/lean-eval-submissions/actions/runs/123"
            ),
            "key_envelope": {
                "schema_version": 2,
                "submission_id": self.archive_submission_id,
                "archive_ciphertext_sha256": self.ciphertext_sha256,
                "data_key_id": archive_file_key_id(
                    self.archive_submission_id, self.ciphertext_sha256
                ),
                "key_material_type": "age-file-key-v1",
                "adapter": "aws-kms-v1",
                "wrapped_key_material": "cHJvdmlkZXItd3JhcHBlZC1maWxlLWtleQ==",
            },
        }
        self.archive_path = f"archives/01/{self.archive_submission_id}.tar.age"
        self.sidecar_path = f"archives/01/{self.archive_submission_id}.json"
        archive_file = self.audit / self.archive_path
        archive_file.parent.mkdir(parents=True)
        archive_file.write_bytes(self.ciphertext)
        sidecar_file = self.audit / self.sidecar_path
        self.sidecar_raw = json.dumps(
            self.sidecar, ensure_ascii=True, indent=2, sort_keys=True
        ).encode() + b"\n"
        sidecar_file.write_bytes(self.sidecar_raw)
        self.archive_commit = self.commit_in(
            self.audit, "Add exact historical private archive"
        )
        subprocess.run(
            [
                "git", "-C", str(self.audit), "update-ref",
                "refs/remotes/origin/main", self.archive_commit,
            ],
            check=True,
        )
        self.task = {
            "replay_task_id": replay_task_id(self.result_id, self.measurement_digest),
            "result_id": self.result_id,
            "historical_accepted_at": self.entry["historical_accepted_at"],
            "owner_login": self.owner,
            "declared_model": self.model,
            "problem_id": self.problem,
            "statement_revision": 1,
            "results_repository": "leanprover/lean-eval-submissions",
            "results_commit": "9" * 40,
            "results_path": self.entry["results_path"],
            "result_file_sha256": self.entry["result_file_sha256"],
            "result_tree_digest": self.entry["result_tree_digest"],
            "source_visibility": "private",
            "crosswalk_repository": "leanprover/lean-eval-submissions",
            "crosswalk_commit": "0" * 40,
            "crosswalk_path": self.authority["crosswalk"]["path"],
            "crosswalk_sha256": "e" * 64,
            "crosswalk_entry_sha256": self.entry["crosswalk_entry_sha256"],
            "archive_plan_entry_sha256": self.entry["archive_plan_entry_sha256"],
            "archive_submission_id": self.archive_submission_id,
            "archive_schema_version": 3,
            "archive_repository": "leanprover/lean-eval-audit",
            "archive_commit": self.archive_commit,
            "archive_path": self.archive_path,
            "archive_sidecar_path": self.sidecar_path,
            "archive_ciphertext_sha256": self.ciphertext_sha256,
            "archive_sidecar_sha256": hashlib.sha256(self.sidecar_raw).hexdigest(),
            "archive_key_envelope_sha256": hashlib.sha256(
                controller.canonical_compact(self.sidecar["key_envelope"])
            ).hexdigest(),
            "archive_plaintext_tar_sha256": "4" * 64,
            "archive_plaintext_tar_size": 4096,
            "benchmark_repository": "leanprover/lean-eval",
            "benchmark_commit": self.entry["benchmark_commit"],
            "toolchain": self.execution_profile["toolchain"],
            "lean_toolchain_blob_sha256": self.profile["lean_toolchain_blob_sha256"],
            "workflow_run_identity_sha256": "5" * 64,
            "authority_repository": "leanprover/lean-eval-submissions",
            "authority_commit": self.authority_commit,
            "authority_path": self.authority_path,
            "authority_sha256": self.authority_sha256,
            "authority_entry_sha256": entry_sha256(self.entry),
            "authority_event_id": "01900000-0000-7000-8000-000000000001",
            "authorized_at": "2026-10-21T06:08:40.000Z",
            "qualification_repository": "leanprover/lean-eval-submissions",
            "qualification_commit": self.profile_commit,
            "qualification_path": self.profile_path,
            "qualification_sha256": self.profile_sha256,
            "qualification_event_id": "01900000-0000-7000-8000-000000000002",
            "qualified_at": "2026-10-21T06:09:41.000Z",
            "checker": "nanoda",
            "measurement_config_digest": self.measurement_digest,
            "execution_profile_digest": self.profile_digest,
            "status": "queued",
            "attempt": 0,
            "event_id": "01900000-0000-7000-8000-000000000003",
            "occurred_at": "2026-10-21T06:10:42.000Z",
        }
        self.queue = {
            "schema_version": 1,
            "environment": "production",
            "source_event_count": 4,
            "source_digest": "6" * 64,
            "tasks": [self.task],
        }
        self.archive_binding = controller.load_archive_inputs(
            self.audit, self.task
        )[3]

        self.state_temporary = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.state_temporary.name)
        subprocess.run(["git", "init", "-q", str(self.state)], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.state), "remote", "add", "origin",
                "https://github.com/leanprover/lean-eval-state.git",
            ],
            check=True,
        )
        (self.state / "scripts").mkdir()
        (self.state / "state.json").write_text(
            '{"environment":"production"}\n', encoding="utf-8"
        )
        (self.state / "fixture-queue.json").write_bytes(
            controller.state_canonical_bytes(self.queue)
        )
        (self.state / "scripts/state.py").write_text(
            "import argparse, json, pathlib\n"
            "parser = argparse.ArgumentParser()\n"
            "parser.add_argument('--root', type=pathlib.Path, required=True)\n"
            "commands = parser.add_subparsers(dest='command', required=True)\n"
            "commands.add_parser('validate')\n"
            "materialize = commands.add_parser('materialize')\n"
            "materialize.add_argument('--output', type=pathlib.Path, required=True)\n"
            "args = parser.parse_args()\n"
            "if args.command == 'materialize':\n"
            "    args.output.mkdir(parents=True)\n"
            "    queue = json.loads((args.root / 'fixture-queue.json').read_text())\n"
            "    events = [json.loads(path.read_text()) for path in "
            "sorted((args.root / 'events').glob('*/*.json'))]\n"
            "    if any(event['event_type'] == 'replay.started' for event in events):\n"
            "        queue['tasks'] = []\n"
            "    (args.output / 'historical-private-replay-queue.json').write_text("
            "json.dumps(queue, ensure_ascii=True, indent=2, sort_keys=True) + '\\n')\n",
            encoding="utf-8",
        )
        history = (
            {
                "schema_version": 1,
                "event_id": self.task["authority_event_id"],
                "event_type": controller.AUTHORITY_EVENT_TYPE,
                "occurred_at": self.task["authorized_at"],
                "subject_id": self.result_id,
                "causation_event_id": None,
                "actor": {"kind": "system"},
                "payload": {},
            },
            {
                "schema_version": 1,
                "event_id": self.task["qualification_event_id"],
                "event_type": "historical_archive_result.replay_profile_qualified",
                "occurred_at": self.task["qualified_at"],
                "subject_id": self.result_id,
                "causation_event_id": self.task["authority_event_id"],
                "actor": {"kind": "system"},
                "payload": {},
            },
            {
                "schema_version": 1,
                "event_id": self.task["event_id"],
                "event_type": "replay.enqueued",
                "occurred_at": self.task["occurred_at"],
                "subject_id": self.task["replay_task_id"],
                "causation_event_id": self.task["qualification_event_id"],
                "actor": {"kind": "system"},
                "payload": {"result_id": self.result_id},
            },
        )
        for event in history:
            path = (
                self.state / "events" / event["event_id"][:2]
                / f"{event['event_id']}.json"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(controller.state_canonical_bytes(event))
        self.state_head = self.commit_in(self.state, "Add exact protected State fixture")
        self.set_state_upstream()
        self.previous_state_minimum = controller.STATE_MINIMUM_COMMITS["production"]
        controller.STATE_MINIMUM_COMMITS["production"] = self.state_head

    def commit(self, message: str) -> str:
        return self.commit_in(self.repository, message)

    def commit_in(self, repository: pathlib.Path, message: str) -> str:
        subprocess.run(
            ["git", "-C", str(repository), "add", "."], check=True
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-qm", message],
            check=True,
            env=self.environment,
        )
        return subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def plan(self, queue: dict[str, object] | None = None) -> dict[str, object]:
        selected = self.queue if queue is None else queue
        binding = {
            **self.archive_binding,
            "ciphertext_sha256": selected["tasks"][0]["archive_ciphertext_sha256"],
        }
        return controller._plan_next(
            selected,
            controller.state_canonical_bytes(selected),
            self.state_head,
            self.authority,
            self.authority_raw,
            self.profile,
            self.profile_raw,
            binding,
        )

    def set_state_upstream(self) -> None:
        subprocess.run(
            [
                "git", "-C", str(self.state), "update-ref",
                "refs/remotes/origin/main", self.state_head,
            ],
            check=True,
        )

    def commit_state_event(self, event: dict[str, object]) -> None:
        path = self.state / "events" / event["event_id"][:2] / f"{event['event_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(controller.state_canonical_bytes(event))
        self.state_head = self.commit_in(self.state, "Append exact started event")
        self.set_state_upstream()

    def update_state_queue(self, queue: dict[str, object]) -> None:
        (self.state / "fixture-queue.json").write_bytes(
            controller.state_canonical_bytes(queue)
        )
        self.state_head = self.commit_in(self.state, "Update exact private queue")
        self.set_state_upstream()

    def close(self) -> None:
        controller.STATE_MINIMUM_COMMITS["production"] = self.previous_state_minimum
        self.state_temporary.cleanup()
        self.audit_temporary.cleanup()
        self.temporary.cleanup()


class HistoricalPrivateReplayControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def assert_post_start_actions_reject_terminal_history(
        self, plan: dict[str, object], started: dict[str, object]
    ) -> None:
        self.assertEqual(controller.load_state_queue(self.fixture.state)[0]["tasks"], [])
        verdict = {
            "schema_version": 1,
            "replay_task_id": self.fixture.task["replay_task_id"],
            "attempt": 1,
            "execution_outcome": "failed",
            "checker_outcome": None,
            "failure_reason": "runner_lost",
            "statistics": None,
        }
        calls = (
            lambda: controller.terminal_candidate(
                plan,
                started,
                verdict,
                self.fixture.state,
                "2026-10-21T07:00:02.000Z",
            ),
            lambda: controller.prepare_unwrap(
                plan,
                self.fixture.state,
                started,
                self.fixture.audit,
                "2026-10-21T07:00:02.000Z",
            ),
            lambda: controller.build_executor_request(
                plan,
                self.fixture.state,
                started,
                self.fixture.audit,
                {},
                pathlib.Path("unused-key-material"),
            ),
        )
        for action in calls:
            with self.assertRaisesRegex(
                controller.HistoricalPrivateReplayControllerError,
                "not the unique current running private replay",
            ):
                action()

    def test_queue_is_distinct_closed_schema_one_and_selects_first_sorted_task(self) -> None:
        queue = controller.validate_queue(self.fixture.queue)
        self.assertEqual(queue["schema_version"], 1)
        self.assertEqual(queue["tasks"][0], self.fixture.task)
        forbidden = {
            "submission_id",
            "submission.received",
            "release.scheduled",
            "release",
            "source_repository",
            "source_commit",
        }
        self.assertTrue(recursive_keys(queue).isdisjoint(forbidden))

        modern = copy.deepcopy(self.fixture.queue)
        modern["schema_version"] = 2
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError, "identity"
        ):
            controller.validate_queue(modern)
        injected = copy.deepcopy(self.fixture.queue)
        injected["tasks"][0]["submission_id"] = self.fixture.archive_submission_id
        with self.assertRaises(controller.HistoricalPrivateReplayControllerError):
            controller.validate_queue(injected)

    def test_exact_plan_profile_locators_commits_and_digests_are_required(self) -> None:
        reviewed = controller.load_reviewed_inputs(
            self.fixture.repository, self.fixture.task
        )
        self.assertEqual(reviewed[0], self.fixture.authority)
        self.assertEqual(reviewed[2], self.fixture.profile)
        self.fixture.task["authority_path"] = (
            "evidence/private-replay/plans/" + "0" * 64 + ".json"
        )
        with self.assertRaises(controller.HistoricalPrivateReplayControllerError):
            controller.load_reviewed_inputs(self.fixture.repository, self.fixture.task)

    def test_private_profile_provenance_dereferences_every_exact_source_blob(self) -> None:
        controller._verify_profile_provenance(
            self.fixture.repository, self.fixture.task, self.fixture.profile
        )
        profile = copy.deepcopy(self.fixture.profile)
        profile["source_blobs"]["orchestrator"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError,
            "orchestrator differs",
        ):
            controller._verify_profile_provenance(
                self.fixture.repository, self.fixture.task, profile
            )

    def test_plan_binds_state_head_and_only_adapts_archive_uuid_to_executor(self) -> None:
        plan = self.fixture.plan()
        self.assertEqual(plan["state"]["expected_head"], self.fixture.state_head)
        self.assertEqual(plan["task"], self.fixture.task)
        request = plan["execution_plan"]["request"]
        self.assertEqual(
            request["source"]["archive"]["submission_id"],
            self.fixture.archive_submission_id,
        )
        self.assertEqual(
            request["result"]["submission_id"], self.fixture.archive_submission_id
        )
        self.assertEqual(request["result"]["commit"], self.fixture.task["results_commit"])
        self.assertNotIn("source_repository", request)
        self.assertNotIn("release", recursive_keys(plan))

    def test_public_planner_derives_all_inputs_from_exact_protected_checkouts(self) -> None:
        plan = controller.plan_from_checkouts(
            self.fixture.state, self.fixture.repository, self.fixture.audit
        )
        self.assertEqual(plan, self.fixture.plan())
        self.assertEqual(plan["archive_binding"], self.fixture.archive_binding)

    def test_raw_values_and_canonical_state_head_cannot_be_substituted(self) -> None:
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError, "queue raw bytes"
        ):
            controller._plan_next(
                self.fixture.queue,
                controller.state_canonical_bytes(self.fixture.queue) + b" ",
                self.fixture.state_head,
            )
        changed_authority = copy.deepcopy(self.fixture.authority)
        changed_authority["classification_counts"]["bound"] = 2
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError, "reviewed private inputs"
        ):
            controller._plan_next(
                self.fixture.queue,
                controller.state_canonical_bytes(self.fixture.queue),
                self.fixture.state_head,
                changed_authority,
                self.fixture.authority_raw,
                self.fixture.profile,
                self.fixture.profile_raw,
                self.fixture.archive_binding,
            )

        (self.fixture.state / "fixture-queue.json").write_bytes(
            controller.state_canonical_bytes({**self.fixture.queue, "source_digest": "0" * 64})
        )
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError, "not clean"
        ):
            controller.plan_from_checkouts(
                self.fixture.state, self.fixture.repository, self.fixture.audit
            )

    def test_canonical_looking_local_commits_are_not_protected_upstream(self) -> None:
        forged = self.fixture.repository / "evidence/private-replay/forged.json"
        forged.write_text('{}\n', encoding="utf-8")
        self.fixture.commit("Add canonical-looking local forgery")
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError, "differs from origin/main"
        ):
            controller.load_reviewed_inputs(
                self.fixture.repository, self.fixture.task
            )
        audit_forgery = self.fixture.audit / "archives/local-forgery.json"
        audit_forgery.write_text('{}\n', encoding="utf-8")
        self.fixture.commit_in(self.fixture.audit, "Add local audit forgery")
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError,
            "differs from origin/main",
        ):
            controller.load_archive_inputs(self.fixture.audit, self.fixture.task)

    def test_local_state_head_cannot_replace_protected_upstream_head(self) -> None:
        (self.fixture.state / "local-forgery.json").write_text(
            '{}\n', encoding="utf-8"
        )
        self.fixture.state_head = self.fixture.commit_in(
            self.fixture.state, "Add canonical-looking local State forgery"
        )
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError,
            "differs from origin/main",
        ):
            controller.load_state_queue(self.fixture.state)

    def test_audit_sidecar_envelope_and_plaintext_claims_are_exact(self) -> None:
        changes = (
            ("archive_sidecar_sha256", "0" * 64),
            ("archive_key_envelope_sha256", "1" * 64),
            ("archive_plaintext_tar_sha256", "2" * 64),
            ("archive_plaintext_tar_size", 4097),
            ("archive_ciphertext_sha256", "3" * 64),
            ("benchmark_commit", "4" * 40),
            (
                "archive_submission_id",
                "019a0000-0000-7000-8000-000000000041",
            ),
        )
        for field, value in changes:
            with self.subTest(field=field):
                task = copy.deepcopy(self.fixture.task)
                task[field] = value
                if field == "archive_submission_id":
                    task["archive_path"] = (
                        f"archives/01/{value}.tar.age"
                    )
                    task["archive_sidecar_path"] = f"archives/01/{value}.json"
                with self.assertRaises(controller.HistoricalPrivateReplayControllerError):
                    controller.load_archive_inputs(self.fixture.audit, task)

    def test_post_start_alternate_existing_archive_plan_is_rejected_by_history(self) -> None:
        plan = self.fixture.plan()
        started = controller.started_candidate(
            plan,
            self.fixture.state,
            "2026-10-21T07:00:00.000Z",
            random_bytes=b"\x0c" * 10,
        )
        self.fixture.commit_state_event(started["event"])

        alternate_id = "019a0000-0000-7000-8000-000000000041"
        ciphertext = b"age-encryption.org/v1\nalternate-private-fixture"
        ciphertext_sha = hashlib.sha256(ciphertext).hexdigest()
        sidecar = copy.deepcopy(self.fixture.sidecar)
        sidecar.update(
            submission_id=alternate_id,
            sha256_ciphertext=ciphertext_sha,
            size_bytes_ciphertext=len(ciphertext),
            sha256_plaintext_tar="a" * 64,
            size_bytes_plaintext_tar=8192,
        )
        sidecar["key_envelope"].update(
            submission_id=alternate_id,
            archive_ciphertext_sha256=ciphertext_sha,
            data_key_id=archive_file_key_id(alternate_id, ciphertext_sha),
        )
        archive_path = f"archives/01/{alternate_id}.tar.age"
        sidecar_path = f"archives/01/{alternate_id}.json"
        (self.fixture.audit / archive_path).write_bytes(ciphertext)
        sidecar_raw = json.dumps(
            sidecar, ensure_ascii=True, indent=2, sort_keys=True
        ).encode() + b"\n"
        (self.fixture.audit / sidecar_path).write_bytes(sidecar_raw)
        alternate_commit = self.fixture.commit_in(
            self.fixture.audit, "Add second valid historical private archive"
        )
        subprocess.run(
            [
                "git", "-C", str(self.fixture.audit), "update-ref",
                "refs/remotes/origin/main", alternate_commit,
            ],
            check=True,
        )

        substituted = copy.deepcopy(plan)
        task = substituted["task"]
        task.update(
            archive_submission_id=alternate_id,
            archive_commit=alternate_commit,
            archive_path=archive_path,
            archive_sidecar_path=sidecar_path,
            archive_ciphertext_sha256=ciphertext_sha,
            archive_sidecar_sha256=hashlib.sha256(sidecar_raw).hexdigest(),
            archive_key_envelope_sha256=hashlib.sha256(
                controller.canonical_compact(sidecar["key_envelope"])
            ).hexdigest(),
            archive_plaintext_tar_sha256=sidecar["sha256_plaintext_tar"],
            archive_plaintext_tar_size=sidecar["size_bytes_plaintext_tar"],
        )
        substituted["archive_binding"] = {
            "repository": task["archive_repository"],
            "commit": alternate_commit,
            "archive_path": archive_path,
            "sidecar_path": sidecar_path,
            "ciphertext_sha256": ciphertext_sha,
            "sidecar_sha256": hashlib.sha256(sidecar_raw).hexdigest(),
            "key_envelope_sha256": task["archive_key_envelope_sha256"],
            "plaintext_tar_sha256": sidecar["sha256_plaintext_tar"],
            "plaintext_tar_size": sidecar["size_bytes_plaintext_tar"],
        }
        request = substituted["execution_plan"]["request"]
        request["source"]["archive"].update(
            submission_id=alternate_id,
            archive_commit=alternate_commit,
            archive_path=archive_path,
            archive_ciphertext_sha256=ciphertext_sha,
        )
        request["result"]["submission_id"] = alternate_id
        substituted["state"]["task_sha256"] = hashlib.sha256(
            controller.state_canonical_bytes(task)
        ).hexdigest()
        controller.validate_execution_plan(substituted)
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError,
            "next exact live queue task",
        ):
            controller.prepare_unwrap(
                substituted,
                self.fixture.state,
                started,
                self.fixture.audit,
                "2026-10-21T07:00:01.000Z",
            )
        verdict = {
            "schema_version": 1,
            "replay_task_id": task["replay_task_id"],
            "attempt": 1,
            "execution_outcome": "failed",
            "checker_outcome": None,
            "failure_reason": "runner_lost",
            "statistics": None,
        }
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError,
            "next exact live queue task",
        ):
            controller.terminal_candidate(
                substituted,
                started,
                verdict,
                self.fixture.state,
                "2026-10-21T07:00:01.000Z",
            )
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError,
            "next exact live queue task",
        ):
            controller.build_executor_request(
                substituted,
                self.fixture.state,
                started,
                self.fixture.audit,
                {},
                pathlib.Path("unused-key-material"),
            )

    def test_accepted_terminal_cannot_reuse_committed_start_for_actions(self) -> None:
        plan = self.fixture.plan()
        started = controller.started_candidate(
            plan,
            self.fixture.state,
            "2026-10-21T07:00:00.000Z",
            random_bytes=b"\x0e" * 10,
        )
        self.fixture.commit_state_event(started["event"])
        verdict = json.loads(
            (ROOT / "tests/fixtures/replay-verdict-accepted-v1.json").read_text(
                encoding="utf-8"
            )
        )
        verdict["replay_task_id"] = self.fixture.task["replay_task_id"]
        terminal = controller.terminal_candidate(
            plan,
            started,
            verdict,
            self.fixture.state,
            "2026-10-21T07:00:01.000Z",
            random_bytes=b"\x0d" * 10,
        )
        self.fixture.commit_state_event(terminal["event"])
        self.assert_post_start_actions_reject_terminal_history(plan, started)

    def test_failed_terminal_cannot_reuse_start_when_current_queue_is_empty(self) -> None:
        plan = self.fixture.plan()
        started = controller.started_candidate(
            plan,
            self.fixture.state,
            "2026-10-21T07:00:00.000Z",
            random_bytes=b"\x0f" * 10,
        )
        self.fixture.commit_state_event(started["event"])
        verdict = {
            "schema_version": 1,
            "replay_task_id": self.fixture.task["replay_task_id"],
            "attempt": 1,
            "execution_outcome": "failed",
            "checker_outcome": None,
            "failure_reason": "verdict_invalid",
            "statistics": None,
        }
        terminal = controller.terminal_candidate(
            plan,
            started,
            verdict,
            self.fixture.state,
            "2026-10-21T07:00:01.000Z",
            random_bytes=b"\x0d" * 10,
        )
        self.assertFalse(terminal["event"]["payload"]["retryable"])
        self.fixture.commit_state_event(terminal["event"])
        self.assert_post_start_actions_reject_terminal_history(plan, started)

    def test_authority_entry_and_profile_substitution_fail_closed(self) -> None:
        authority = copy.deepcopy(self.fixture.authority)
        authority["entries"][0]["archive_submission_id"] = (
            "019a0000-0000-7000-8000-000000000041"
        )
        authority_raw = controller.canonical_bytes(authority)
        with self.assertRaises(controller.HistoricalPrivateReplayControllerError):
            controller._plan_next(
                self.fixture.queue,
                controller.state_canonical_bytes(self.fixture.queue),
                self.fixture.state_head,
                authority,
                authority_raw,
                self.fixture.profile,
                self.fixture.profile_raw,
                self.fixture.archive_binding,
            )

        profile = copy.deepcopy(self.fixture.profile)
        profile["benchmark_commit"] = "0" * 40
        profile_raw = controller.canonical_bytes(profile)
        with self.assertRaises(controller.HistoricalPrivateReplayControllerError):
            controller._plan_next(
                self.fixture.queue,
                controller.state_canonical_bytes(self.fixture.queue),
                self.fixture.state_head,
                self.fixture.authority,
                self.fixture.authority_raw,
                profile,
                profile_raw,
                self.fixture.archive_binding,
            )

    def test_started_and_terminal_candidates_carry_exact_cas_heads(self) -> None:
        plan = self.fixture.plan()
        started = controller.started_candidate(
            plan,
            self.fixture.state,
            "2026-10-21T07:00:00.000Z",
            random_bytes=b"\x01" * 10,
        )
        self.assertEqual(started["expected_head"], self.fixture.state_head)
        self.assertEqual(started["event"]["event_type"], "replay.started")
        verdict = {
            "schema_version": 1,
            "replay_task_id": self.fixture.task["replay_task_id"],
            "attempt": 1,
            "execution_outcome": "failed",
            "checker_outcome": None,
            "failure_reason": "runner_lost",
            "statistics": None,
        }
        self.fixture.commit_state_event(started["event"])
        terminal = controller.terminal_candidate(
            plan,
            started,
            verdict,
            self.fixture.state,
            "2026-10-21T07:00:01.000Z",
            random_bytes=b"\x02" * 10,
        )
        self.assertEqual(terminal["expected_head"], self.fixture.state_head)
        self.assertEqual(terminal["event"]["event_type"], "replay.failed")
        self.assertTrue(terminal["event"]["payload"]["retryable"])

        stale_plan = copy.deepcopy(plan)
        stale_plan["state"]["queue_source_digest"] = "0" * 64
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError, "next exact"
        ):
            controller.started_candidate(
                stale_plan, self.fixture.state, "2026-10-21T07:00:00.000Z"
            )

    def test_fourth_attempt_is_terminal_and_fifth_attempt_is_refused(self) -> None:
        queue = copy.deepcopy(self.fixture.queue)
        queue["tasks"][0].update(
            status="failed", attempt=3, reason_code="runner_lost", retryable=True
        )
        self.fixture.update_state_queue(queue)
        plan = self.fixture.plan(queue)
        started = controller.started_candidate(
            plan,
            self.fixture.state,
            "2026-10-21T07:00:00.000Z",
            random_bytes=b"\x03" * 10,
        )
        verdict = {
            "schema_version": 1,
            "replay_task_id": self.fixture.task["replay_task_id"],
            "attempt": 4,
            "execution_outcome": "failed",
            "checker_outcome": None,
            "failure_reason": "runner_lost",
            "statistics": None,
        }
        self.fixture.commit_state_event(started["event"])
        running = [{
            "replay_task_id": self.fixture.task["replay_task_id"],
            "status": "running",
            "attempt": 4,
            "event": started["event"],
        }]
        with mock.patch.object(
            controller, "current_historical_running", return_value=running
        ):
            terminal = controller.terminal_candidate(
                plan,
                started,
                verdict,
                self.fixture.state,
                "2026-10-21T07:00:01.000Z",
                random_bytes=b"\x04" * 10,
            )
        self.assertFalse(terminal["event"]["payload"]["retryable"])
        queue["tasks"][0]["attempt"] = 4
        with self.assertRaisesRegex(
            controller.HistoricalPrivateReplayControllerError, "bounded retry"
        ):
            controller.validate_queue(queue)

    def test_existing_one_use_unwrap_and_executor_primitives_are_composed(self) -> None:
        plan = self.fixture.plan()
        started = controller.started_candidate(
            plan,
            self.fixture.state,
            "2026-10-21T07:00:00.000Z",
            random_bytes=b"\x0a" * 10,
        )
        self.fixture.commit_state_event(started["event"])
        with mock.patch.object(
            controller, "prepare_private_unwrap", return_value={"unwrap": "exact"}
        ) as unwrap:
            value = controller.prepare_unwrap(
                plan,
                self.fixture.state,
                started,
                self.fixture.audit,
                "2026-10-21T07:00:00.000Z",
            )
        self.assertEqual(value, {"unwrap": "exact"})
        self.assertEqual(unwrap.call_args.args[0], plan["execution_plan"])

        with mock.patch.object(
            controller,
            "build_private_executor_request",
            return_value={"request": "exact"},
        ) as executor:
            value = controller.build_executor_request(
                plan,
                self.fixture.state,
                started,
                self.fixture.audit,
                {"unwrap": "exact"},
                pathlib.Path("identity.txt"),
            )
        self.assertEqual(value, {"request": "exact"})
        self.assertEqual(executor.call_args.args[0], plan["execution_plan"])

    def test_schema_v2_file_key_uses_strict_existing_handoff(self) -> None:
        plan = self.fixture.plan()
        started = controller.started_candidate(
            plan,
            self.fixture.state,
            "2026-08-23T06:59:59.000Z",
            random_bytes=b"\x0b" * 10,
        )
        self.fixture.commit_state_event(started["event"])
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            unwrap = controller.prepare_unwrap(
                plan,
                self.fixture.state,
                started,
                self.fixture.audit,
                "2026-08-23T07:00:00.000Z",
                request_random=b"\x06" * 10,
                runner_nonce="7" * 64,
            )
            current = dt.datetime.now(dt.timezone.utc)
            unwrap["capability"]["issued_at"] = current.isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")
            unwrap["capability"]["expires_at"] = (
                (current + dt.timedelta(minutes=5))
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
            response = {
                "schema_version": 2,
                "adapter": "aws-kms-v1",
                "request_id": unwrap["capability"]["request_id"],
                "data_key_id": unwrap["envelope"]["data_key_id"],
                "capability_digest": capability_digest(unwrap["capability"]),
                "key_material_type": "age-file-key-v1",
                "plaintext_key_material_base64": "a2tra2tra2tra2tra2traw==",
            }
            material = controller.unwrap_identity(
                unwrap, response, {"StatusCode": 200}
            )
            material_path = root / "file-key"
            material_path.write_bytes(material)
            request = controller.build_executor_request(
                plan,
                self.fixture.state,
                started,
                self.fixture.audit,
                unwrap,
                material_path,
            )
        self.assertEqual(request["schema_version"], 2)
        self.assertEqual(request["key_material_type"], "age-file-key-v1")
        self.assertNotIn("plaintext_identity_base64", request)


class HistoricalPrivateRecoveryTests(unittest.TestCase):
    def write_event(
        self, root: pathlib.Path, event_id: str, event_type: str, subject: str,
        occurred_at: str, cause: str | None, payload: dict[str, object]
    ) -> None:
        event = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "subject_id": subject,
            "causation_event_id": cause,
            "actor": {"kind": "system"},
            "payload": payload,
        }
        path = root / event_id[:2] / f"{event_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(controller.state_canonical_bytes(event))

    def test_private_stale_recovery_requires_exact_destruction_confirmation(self) -> None:
        result = "r2_" + "1" * 64
        task = "rt1_" + "2" * 64
        ids = [f"01900000-0000-7000-8000-00000000000{index}" for index in range(1, 5)]
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory)
            events = state / "events"
            self.write_event(
                events,
                ids[0],
                controller.AUTHORITY_EVENT_TYPE,
                result,
                "2026-10-21T06:00:00.000Z",
                None,
                {},
            )
            self.write_event(
                events,
                ids[1],
                "historical_archive_result.replay_profile_qualified",
                result,
                "2026-10-21T06:00:01.000Z",
                ids[0],
                {},
            )
            self.write_event(
                events,
                ids[2],
                "replay.enqueued",
                task,
                "2026-10-21T06:00:02.000Z",
                ids[1],
                {"result_id": result},
            )
            self.write_event(
                events,
                ids[3],
                "replay.started",
                task,
                "2026-10-21T06:00:03.000Z",
                ids[2],
                {"attempt": 1, "runner_profile": "historical-private-v1"},
            )
            state_binding = ({"environment": "production"}, b"", "a" * 40)
            with mock.patch.object(controller, "load_state_queue", return_value=state_binding):
                first = controller.recover_running(
                    state, "2026-10-21T14:00:03.000Z"
                )
            self.assertEqual(first["kind"], "cleanup_required")
            confirmation = {
                "schema_version": 1,
                "replay_task_id": task,
                "attempt": 1,
                "destruction": "confirmed",
            }
            with mock.patch.object(controller, "load_state_queue", return_value=state_binding):
                recovered = controller.recover_running(
                    state,
                    "2026-10-21T14:00:03.000Z",
                    cleanup_confirmation_value=confirmation,
                    random_bytes=b"\x05" * 10,
                )
            self.assertEqual(recovered["kind"], "failed")
            self.assertEqual(recovered["append"]["expected_head"], "a" * 40)
            self.assertEqual(recovered["append"]["event"]["event_type"], "replay.failed")
            self.assertTrue(recovered["append"]["event"]["payload"]["retryable"])

            wrong = {**confirmation, "attempt": 2}
            with self.assertRaisesRegex(
                controller.HistoricalPrivateReplayControllerError,
                "differs from the running attempt",
            ):
                with mock.patch.object(
                    controller, "load_state_queue", return_value=state_binding
                ):
                    controller.recover_running(
                        state,
                        "2026-10-21T14:00:03.000Z",
                        cleanup_confirmation_value=wrong,
                    )


if __name__ == "__main__":
    unittest.main()
