from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.wait_replay_container_rollout import (
    RolloutError,
    load_expected_container,
    wait_for_rollout,
)


IMAGE = (
    "registry.cloudflare.com/" + "1" * 32 + "/lean-eval-authoritative:" + "a" * 40
)
APPLICATION = "lean-eval-replay-executor-staging-replaysandbox-staging"
APPLICATION_ID = "12345678-1234-1234-1234-123456789abc"
HISTORICAL_IMAGE = (
    "registry.cloudflare.com/"
    + "1" * 32
    + "/lean-eval-historical-public-v1:"
    + "b" * 40
    + "-"
    + "c" * 40
    + "@sha256:"
    + "d" * 64
)
HISTORICAL_APPLICATION = (
    "lean-eval-historical-public-replay-replaysandbox-production"
)


def application(
    image: str,
    version: int = 2,
    name: str = APPLICATION,
) -> dict[str, object]:
    return {
        "id": APPLICATION_ID,
        "name": name,
        "max_instances": 1,
        "configuration": {
            "image": image,
            "wrangler_ssh": {"enabled": False},
            "vcpu": 4,
            "memory_mib": 12 * 1024,
            "disk": {"size_mb": 20_000},
            "network": {
                "assign_ipv6": "none",
                "assign_ipv4": "none",
                "mode": "private",
            },
        },
        "version": version,
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


class WaitReplayContainerRolloutTests(unittest.TestCase):
    def config(self, root: pathlib.Path) -> pathlib.Path:
        path = root / "wrangler.replay.jsonc"
        path.write_text(
            json.dumps(
                {
                    "env": {
                        "staging": {
                            "containers": [
                                {
                                    "image": IMAGE,
                                    "instance_type": "standard-4",
                                    "max_instances": 1,
                                    "ssh": {"enabled": False},
                                }
                            ]
                        },
                        "production": {
                            "containers": [
                                {
                                    "image": IMAGE,
                                    "instance_type": "standard-4",
                                    "max_instances": 1,
                                    "ssh": {"enabled": False},
                                }
                            ]
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_historical_public_family_is_explicit_and_digest_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            config = self.config(root)
            value = json.loads(config.read_text(encoding="utf-8"))
            value["env"]["production"]["containers"][0]["image"] = (
                HISTORICAL_IMAGE
            )
            config.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RolloutError, "image is invalid"):
                load_expected_container(config, "production")
            self.assertEqual(
                load_expected_container(
                    config,
                    "production",
                    image_family="historical-public",
                )["image"],
                HISTORICAL_IMAGE,
            )

            value["env"]["production"]["containers"][0]["max_instances"] = 2
            config.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RolloutError, "max_instances is invalid"):
                load_expected_container(
                    config,
                    "production",
                    image_family="historical-public",
                )
            value["env"]["production"]["containers"][0]["max_instances"] = 1

            value["env"]["production"]["containers"][0]["image"] = (
                HISTORICAL_IMAGE.split("@", 1)[0]
            )
            config.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RolloutError, "image is invalid"):
                load_expected_container(
                    config,
                    "production",
                    image_family="historical-public",
                )

    def test_historical_public_family_rejects_authoritative_images(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(RolloutError, "image is invalid"),
        ):
            load_expected_container(
                self.config(pathlib.Path(directory)),
                "production",
                image_family="historical-public",
            )

    @mock.patch("scripts.wait_replay_container_rollout.subprocess.run")
    def test_waits_for_exact_historical_production_image(
        self, run: mock.Mock
    ) -> None:
        ready = application(HISTORICAL_IMAGE, name=HISTORICAL_APPLICATION)
        run.side_effect = [
            subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    [{"id": APPLICATION_ID, "name": HISTORICAL_APPLICATION}]
                ),
                "",
            ),
            subprocess.CompletedProcess([], 0, json.dumps(ready), ""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            config = self.config(root)
            value = json.loads(config.read_text(encoding="utf-8"))
            value["env"]["production"]["containers"][0]["image"] = (
                HISTORICAL_IMAGE
            )
            config.write_text(json.dumps(value), encoding="utf-8")
            result = wait_for_rollout(
                config,
                "production",
                HISTORICAL_APPLICATION,
                attempts=1,
                interval_seconds=0,
                image_family="historical-public",
            )
        self.assertEqual(result, ready)

    @mock.patch("scripts.wait_replay_container_rollout.time.sleep")
    @mock.patch("scripts.wait_replay_container_rollout.subprocess.run")
    def test_waits_for_exact_ready_image(self, run: mock.Mock, sleep: mock.Mock) -> None:
        listed = [{"id": APPLICATION_ID, "name": APPLICATION}]
        old = application(
            "registry.cloudflare.com/"
            + "1" * 32
            + "/lean-eval-authoritative:"
            + "b" * 40,
            version=1,
        )
        ready = application(IMAGE)
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

    @mock.patch("scripts.wait_replay_container_rollout.subprocess.run")
    def test_uses_target_config_only_for_expected_image(self, run: mock.Mock) -> None:
        ready = application(IMAGE)
        run.side_effect = [
            subprocess.CompletedProcess(
                [], 0, json.dumps([{"id": APPLICATION_ID, "name": APPLICATION}]), ""
            ),
            subprocess.CompletedProcess([], 0, json.dumps(ready), ""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            query = root / "query.json"
            query.write_text("{}", encoding="utf-8")
            result = wait_for_rollout(
                query,
                "staging",
                APPLICATION,
                attempts=1,
                interval_seconds=0,
                expected_image_config_path=self.config(root),
            )
        self.assertEqual(result, ready)
        for call in run.call_args_list:
            self.assertIn(str(query), call.args[0])

    @mock.patch("scripts.wait_replay_container_rollout.subprocess.run")
    def test_rejects_wrong_effective_capacity_or_ssh(self, run: mock.Mock) -> None:
        wrong = application(IMAGE)
        wrong["max_instances"] = 2
        wrong["configuration"]["wrangler_ssh"]["enabled"] = True
        run.side_effect = [
            subprocess.CompletedProcess(
                [], 0, json.dumps([{"id": APPLICATION_ID, "name": APPLICATION}]), ""
            ),
            subprocess.CompletedProcess([], 0, json.dumps(wrong), ""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RolloutError, "did not become ready"):
                wait_for_rollout(
                    self.config(pathlib.Path(directory)),
                    "staging",
                    APPLICATION,
                    attempts=1,
                    interval_seconds=0,
                )


if __name__ == "__main__":
    unittest.main()
