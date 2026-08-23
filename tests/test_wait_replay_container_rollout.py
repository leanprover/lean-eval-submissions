from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.wait_replay_container_rollout import RolloutError, wait_for_rollout


IMAGE = (
    "registry.cloudflare.com/" + "1" * 32 + "/lean-eval-authoritative:" + "a" * 40
)
APPLICATION = "lean-eval-replay-executor-staging-replaysandbox-staging"
APPLICATION_ID = "12345678-1234-1234-1234-123456789abc"


class WaitReplayContainerRolloutTests(unittest.TestCase):
    def config(self, root: pathlib.Path) -> pathlib.Path:
        path = root / "wrangler.replay.jsonc"
        path.write_text(
            json.dumps(
                {
                    "env": {
                        "staging": {"containers": [{"image": IMAGE}]},
                        "production": {"containers": [{"image": IMAGE}]},
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    @mock.patch("scripts.wait_replay_container_rollout.time.sleep")
    @mock.patch("scripts.wait_replay_container_rollout.subprocess.run")
    def test_waits_for_exact_ready_image(self, run: mock.Mock, sleep: mock.Mock) -> None:
        listed = [{"id": APPLICATION_ID, "name": APPLICATION}]
        old = {
            "id": APPLICATION_ID,
            "name": APPLICATION,
            "configuration": {
                "image": (
                    "registry.cloudflare.com/"
                    + "1" * 32
                    + "/lean-eval-authoritative:"
                    + "b" * 40
                )
            },
            "version": 1,
            "health": {
                "errors": [],
                "instances": {
                    "healthy": 1,
                    "failed": 0,
                    "starting": 0,
                    "scheduling": 0,
                },
            },
        }
        ready = {
            "id": APPLICATION_ID,
            "name": APPLICATION,
            "configuration": {"image": IMAGE},
            "version": 2,
            "health": {
                "errors": [],
                "instances": {
                    "healthy": 1,
                    "failed": 0,
                    "starting": 0,
                    "scheduling": 0,
                },
            },
        }
        run.side_effect = [
            subprocess.CompletedProcess([], 0, json.dumps(listed), ""),
            subprocess.CompletedProcess([], 0, json.dumps(old), ""),
            subprocess.CompletedProcess([], 0, json.dumps(listed), ""),
            subprocess.CompletedProcess([], 0, json.dumps(ready), ""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = wait_for_rollout(
                self.config(pathlib.Path(directory)),
                "staging",
                APPLICATION,
                attempts=2,
                interval_seconds=0,
            )
        self.assertEqual(result, ready)
        sleep.assert_called_once_with(0)
        self.assertEqual(run.call_count, 4)

    @mock.patch("scripts.wait_replay_container_rollout.time.sleep")
    @mock.patch("scripts.wait_replay_container_rollout.subprocess.run")
    def test_fails_closed_on_ambiguous_identity(
        self, run: mock.Mock, _sleep: mock.Mock
    ) -> None:
        duplicate = [
            {"id": APPLICATION_ID, "name": APPLICATION},
            {"id": APPLICATION_ID, "name": APPLICATION},
        ]
        run.return_value = subprocess.CompletedProcess(
            [], 0, json.dumps(duplicate), ""
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RolloutError, "did not become ready"):
                wait_for_rollout(
                    self.config(pathlib.Path(directory)),
                    "staging",
                    APPLICATION,
                    attempts=1,
                    interval_seconds=0,
                )

    @mock.patch("scripts.wait_replay_container_rollout.time.sleep")
    @mock.patch("scripts.wait_replay_container_rollout.subprocess.run")
    def test_fails_closed_when_listing_fails(
        self, run: mock.Mock, _sleep: mock.Mock
    ) -> None:
        run.return_value = subprocess.CompletedProcess([], 1, "", "private diagnostic")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RolloutError, "did not become ready") as raised:
                wait_for_rollout(
                    self.config(pathlib.Path(directory)),
                    "staging",
                    APPLICATION,
                    attempts=1,
                    interval_seconds=0,
                )
        self.assertNotIn("private diagnostic", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
