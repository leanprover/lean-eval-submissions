from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from tests.test_historical_private_replay_controller import (
    Fixture,
    fixture_reconfiguration_incident,
    recursive_keys,
)

ROOT = pathlib.Path(__file__).parents[1]

import sys

sys.path.insert(0, str(ROOT / "scripts"))

import historical_private_replay_controller as controller
import prepare_historical_private_reconfiguration as preparation


class HistoricalPrivateReconfigurationPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture(backfill_archive=True)
        self.task, self.replacement, self.artifact = (
            self.fixture.reconfigured_task()
        )
        self.started = {
            "schema_version": 1,
            "event_id": "01900000-0000-7000-8000-000000000010",
            "event_type": "replay.started",
            "occurred_at": "2026-10-21T06:19:59.000Z",
            "subject_id": self.fixture.task["replay_task_id"],
            "causation_event_id": self.fixture.task["event_id"],
            "actor": {"kind": "system"},
            "payload": {"attempt": 3, "runner_profile": "historical-private-v1"},
        }
        self.failed = {
            "schema_version": 1,
            "event_id": "01900000-0000-7000-8000-000000000011",
            "event_type": "replay.failed",
            "occurred_at": "2026-10-21T06:20:00.000Z",
            "subject_id": self.fixture.task["replay_task_id"],
            "causation_event_id": self.started["event_id"],
            "actor": {"kind": "system"},
            "payload": {
                "attempt": 3,
                "reason_code": "runner_lost",
                "retryable": True,
            },
        }
        self.fixture.commit_state_event(self.started)
        self.fixture.commit_state_event(self.failed)

    def tearDown(self) -> None:
        self.fixture.close()

    def test_state_inventory_accepts_state_canonical_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            event_id = "01900000-0000-7000-8000-000000000012"
            path = root / "events" / event_id[:2] / f"{event_id}.json"
            path.parent.mkdir(parents=True)
            event = {"event_id": event_id, "description": "accepted source — packaged"}
            path.write_bytes(controller.state_canonical_bytes(event))
            self.assertEqual(preparation._load_state_events(root), [event])

    def render_batch(self, root: pathlib.Path) -> pathlib.Path:
        args = argparse.Namespace(
            state_root=self.fixture.state,
            repository_root=self.fixture.repository,
            reconfiguration_commit=self.task["reconfiguration_commit"],
            reconfiguration_path=self.task["reconfiguration_path"],
            first_occurred_at="2026-10-21T07:00:00.000Z",
            event_id_seed="f" * 64,
            output_directory=root / "batch",
        )
        with fixture_reconfiguration_incident(self.fixture):
            return preparation.render_state_batch(args)

    def install_state_remote(self, root: pathlib.Path) -> pathlib.Path:
        remote = root / "state.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(remote)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        url = remote.as_uri()
        subprocess.run(
            ["git", "-C", str(self.fixture.state), "remote", "set-url", "origin", url],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.fixture.state), "push", "origin", "HEAD:main"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        self.fixture.set_state_upstream()
        return remote

    def publish(self, manifest: pathlib.Path, remote: pathlib.Path) -> dict[str, object]:
        args = argparse.Namespace(
            state_root=self.fixture.state,
            repository_root=self.fixture.repository,
            manifest=manifest,
        )
        with mock.patch.dict(
            controller.REPOSITORY_REMOTES,
            {"leanprover/lean-eval-state": {remote.as_uri()}},
        ), fixture_reconfiguration_incident(self.fixture):
            return preparation.publish_state_branch(args)

    def test_prepare_decision_selects_only_one_failed_backfill_canary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "decision"
            args = argparse.Namespace(
                state_root=self.fixture.state,
                repository_root=self.fixture.repository,
                audit_root=self.fixture.audit,
                selection="failed_canary",
                replacement_profile=[self.task["execution_profile_digest"]],
                output_directory=output,
            )
            with (
                mock.patch.object(
                    preparation,
                    "SOURCE_FIX_COMMIT",
                    self.fixture.image_source_commit,
                ),
                fixture_reconfiguration_incident(self.fixture),
            ):
                path = preparation.prepare_decision(args)
            artifact = json.loads(path.read_bytes())
            with fixture_reconfiguration_incident(self.fixture):
                self.assertEqual(
                    controller.validate_reconfiguration(artifact), artifact
                )
            self.assertEqual(artifact["task_count"], 1)
            self.assertEqual(artifact["selection"], "failed_canary")
            self.assertEqual(
                artifact["entries"][0]["replacement_qualification"][
                    "execution_profile_digest"
                ],
                self.task["execution_profile_digest"],
            )
            self.assertTrue(
                recursive_keys(artifact).isdisjoint(
                    {
                        "archive_submission_id",
                        "archive_path",
                        "archive_sidecar_path",
                        "source_repository",
                        "source_commit",
                    }
                )
            )

    def test_non_backfill_archive_cannot_enter_the_bounded_decision(self) -> None:
        self.fixture.close()
        self.fixture = Fixture()
        task, _, _ = self.fixture.reconfigured_task()
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                state_root=self.fixture.state,
                repository_root=self.fixture.repository,
                audit_root=self.fixture.audit,
                selection="failed_canary",
                replacement_profile=[task["execution_profile_digest"]],
                output_directory=pathlib.Path(directory) / "decision",
            )
            with (
                mock.patch.object(
                    preparation,
                    "SOURCE_FIX_COMMIT",
                    self.fixture.image_source_commit,
                ),
                fixture_reconfiguration_incident(self.fixture),
                self.assertRaisesRegex(
                    preparation.ReconfigurationPreparationError,
                    "selects 0 tasks instead of 1",
                ),
            ):
                preparation.prepare_decision(args)

    def test_rendered_batch_is_four_event_cas_chain_and_validated_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest_path = self.render_batch(root)
            output = root / "batch"
            manifest = json.loads(manifest_path.read_bytes())
            events = [
                json.loads((output / descriptor["path"]).read_bytes())
                for descriptor in manifest["events"]
            ]
            self.assertEqual(manifest["expected_head"], self.fixture.state_head)
            self.assertEqual(
                manifest["first_occurred_at"], "2026-10-21T07:00:00.000Z"
            )
            self.assertEqual(manifest["event_id_seed"], "f" * 64)
            self.assertEqual(manifest["event_count"], 4)
            self.assertEqual(
                [event["event_type"] for event in events],
                [
                    "replay.unavailable",
                    "historical_archive_result.replay_profile_qualified",
                    "historical_archive_result.replay_reconfigured",
                    "replay.enqueued",
                ],
            )
            self.assertEqual(events[0]["causation_event_id"], self.failed["event_id"])
            self.assertEqual(events[2]["causation_event_id"], events[0]["event_id"])
            self.assertEqual(events[3]["causation_event_id"], events[2]["event_id"])
            self.assertEqual(
                events[2]["payload"]["replacement_qualification_event_id"],
                events[1]["event_id"],
            )

    def test_renderer_rejects_every_reviewed_task_binding_mismatch(self) -> None:
        queue, _, _ = controller.load_state_queue(self.fixture.state)
        live_task = next(
            task
            for task in queue["tasks"]
            if task["replay_task_id"] == self.fixture.task["replay_task_id"]
        )
        mutations = {
            "result_id": lambda entry: entry.__setitem__("result_id", "r2_" + "0" * 64),
            "toolchain": lambda entry: entry.__setitem__(
                "toolchain", "leanprover/lean4:v4.19.0"
            ),
            "lean_toolchain_blob_sha256": lambda entry: entry.__setitem__(
                "lean_toolchain_blob_sha256", "0" * 64
            ),
            "checker": lambda entry: entry.__setitem__("checker", "lean4"),
            "measurement_config_digest": lambda entry: entry.__setitem__(
                "measurement_config_digest", "0" * 64
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                entry = json.loads(
                    controller.canonical_bytes(self.artifact["entries"][0])
                )
                mutate(entry)
                with self.assertRaisesRegex(
                    preparation.ReconfigurationPreparationError,
                    "differs from the exact current task",
                ):
                    preparation._validate_reviewed_entry_against_task(
                        entry, live_task
                    )

    def test_renderer_rejects_reviewed_entry_that_differs_from_live_task(self) -> None:
        artifact = json.loads(controller.canonical_bytes(self.artifact))
        artifact["entries"][0]["toolchain"] = "leanprover/lean4:v4.19.0"
        raw = controller.canonical_bytes(artifact)
        digest = controller.sha256_bytes(raw)
        path = f"evidence/private-replay/reconfigurations/{digest}.json"
        destination = self.fixture.repository / path
        destination.write_bytes(raw)
        commit = self.fixture.commit("Add mismatched reconfiguration fixture")
        subprocess.run(
            [
                "git", "-C", str(self.fixture.repository), "update-ref",
                "refs/remotes/origin/main", commit,
            ],
            check=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                state_root=self.fixture.state,
                repository_root=self.fixture.repository,
                reconfiguration_commit=commit,
                reconfiguration_path=path,
                first_occurred_at="2026-10-21T07:00:00.000Z",
                event_id_seed="f" * 64,
                output_directory=pathlib.Path(directory) / "batch",
            )
            with (
                fixture_reconfiguration_incident(self.fixture),
                self.assertRaisesRegex(
                    preparation.ReconfigurationPreparationError,
                    "differs from the exact current task",
                ),
            ):
                preparation.render_state_batch(args)

    def test_publisher_creates_only_a_new_exact_parent_review_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.render_batch(root)
            expected_head = self.fixture.state_head
            remote = self.install_state_remote(root)
            result = self.publish(manifest, remote)
            branch = str(result["branch"])
            commit = subprocess.run(
                ["git", "--git-dir", str(remote), "rev-parse", f"refs/heads/{branch}"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            parent = subprocess.run(
                ["git", "--git-dir", str(remote), "rev-parse", f"{commit}^"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(commit, result["commit"])
            self.assertEqual(parent, expected_head)
            self.assertEqual(
                subprocess.run(
                    ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                expected_head,
            )
            self.assertIn("compare/main...historical-private-reconfiguration-", result["compare_url"])

    def test_publisher_rejects_stale_main_before_branch_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.render_batch(root)
            remote = self.install_state_remote(root)
            concurrent = root / "concurrent"
            subprocess.run(
                ["git", "clone", "--quiet", remote.as_uri(), str(concurrent)], check=True
            )
            (concurrent / "unrelated").write_text("new State event\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(concurrent), "add", "unrelated"], check=True)
            subprocess.run(
                [
                    "git", "-C", str(concurrent),
                    "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-m", "Advance protected State",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "-C", str(concurrent), "push", "origin", "HEAD:main"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            with self.assertRaisesRegex(
                preparation.ReconfigurationPreparationError,
                "no longer equals the manifest parent",
            ):
                self.publish(manifest, remote)
            heads = subprocess.run(
                ["git", "--git-dir", str(remote), "for-each-ref", "--format=%(refname)", "refs/heads"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(heads, ["refs/heads/main"])

    def test_publisher_rejects_tampered_batch_before_branch_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = self.render_batch(root)
            remote = self.install_state_remote(root)
            extra = manifest.parent / "undeclared"
            extra.write_text("not in manifest\n", encoding="utf-8")
            with self.assertRaisesRegex(
                preparation.ReconfigurationPreparationError,
                "closed manifest",
            ):
                self.publish(manifest, remote)
            extra.unlink()
            descriptor = json.loads(manifest.read_bytes())["events"][0]
            event_path = manifest.parent / descriptor["path"]
            event_path.write_bytes(event_path.read_bytes() + b" ")
            with self.assertRaises(
                (
                    controller.HistoricalPrivateReplayControllerError,
                    preparation.ReconfigurationPreparationError,
                )
            ):
                self.publish(manifest, remote)
            heads = subprocess.run(
                ["git", "--git-dir", str(remote), "for-each-ref", "--format=%(refname)", "refs/heads"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(heads, ["refs/heads/main"])

    def test_publisher_rejects_coherently_rehashed_generic_valid_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest_path = self.render_batch(root)
            remote = self.install_state_remote(root)
            manifest = json.loads(manifest_path.read_bytes())
            descriptor = manifest["events"][0]
            event_path = manifest_path.parent / descriptor["path"]
            event = json.loads(event_path.read_bytes())
            event["payload"]["evidence_sha256"] = "0" * 64
            raw = controller.state_canonical_bytes(event)
            event_path.write_bytes(raw)
            descriptor["sha256"] = controller.sha256_bytes(raw)
            manifest_path.write_bytes(controller.canonical_bytes(manifest))

            loaded, events, _ = preparation._load_exact_state_batch(manifest_path)
            self.assertEqual(loaded, manifest)
            preparation._write_and_validate_overlay(
                self.fixture.state, manifest["expected_head"], events, {}
            )
            with self.assertRaisesRegex(
                preparation.ReconfigurationPreparationError,
                "independently derived exact batch",
            ):
                self.publish(manifest_path, remote)
            heads = subprocess.run(
                [
                    "git", "--git-dir", str(remote), "for-each-ref",
                    "--format=%(refname)", "refs/heads",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(heads, ["refs/heads/main"])


if __name__ == "__main__":
    unittest.main()
