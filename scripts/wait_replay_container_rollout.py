#!/usr/bin/env python3
"""Wait for one Cloudflare Container application to run the reviewed image."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
from typing import Any


MAX_JSON_BYTES = 1024 * 1024
INSTANCE_TYPES = {
    "standard-4": {"vcpu": 4, "memory_mib": 12 * 1024, "disk_size_mb": 20_000}
}


class RolloutError(ValueError):
    """The reviewed container rollout cannot be established."""


def load_expected_container(
    config_path: pathlib.Path, environment: str
) -> dict[str, Any]:
    try:
        raw = config_path.read_bytes()
        if not raw or len(raw) > MAX_JSON_BYTES:
            raise RolloutError("replay configuration exceeds its size limit")
        config = json.loads(raw.decode("utf-8"))
        containers = config["env"][environment]["containers"]
        if not isinstance(containers, list) or len(containers) != 1:
            raise RolloutError("replay environment must define one container")
        container = containers[0]
    except RolloutError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RolloutError("replay configuration is invalid") from error
    if not isinstance(container, dict):
        raise RolloutError("reviewed container configuration is invalid")
    image = container.get("image")
    if not isinstance(image, str) or re.fullmatch(
        r"registry\.cloudflare\.com/[0-9a-f]{32}/lean-eval-authoritative:[0-9a-f]{40}",
        image,
    ) is None:
        raise RolloutError("reviewed container image is invalid")
    instance_type = container.get("instance_type")
    max_instances = container.get("max_instances")
    ssh = container.get("ssh")
    if instance_type not in INSTANCE_TYPES:
        raise RolloutError("reviewed container instance type is unsupported")
    if type(max_instances) is not int or max_instances < 1:
        raise RolloutError("reviewed container max_instances is invalid")
    if ssh != {"enabled": False}:
        raise RolloutError("reviewed container must explicitly disable SSH")
    return {
        "image": image,
        "instance_type": instance_type,
        "max_instances": max_instances,
        "ssh": ssh,
    }


def listed_application(value: Any, application: str) -> dict[str, Any] | None:
    if not isinstance(value, list):
        raise RolloutError("container application list is invalid")
    matches = [
        item
        for item in value
        if isinstance(item, dict) and item.get("name") == application
    ]
    if len(matches) > 1:
        raise RolloutError("container application identity is ambiguous")
    return matches[0] if matches else None


def ready_application(
    value: Any,
    application_id: str,
    application: str,
    expected_container: dict[str, Any],
) -> bool:
    if not isinstance(value, dict):
        return False
    configuration = value.get("configuration")
    health = value.get("health")
    instances = health.get("instances") if isinstance(health, dict) else None
    instance = INSTANCE_TYPES[expected_container["instance_type"]]
    disk = configuration.get("disk") if isinstance(configuration, dict) else None
    network = configuration.get("network") if isinstance(configuration, dict) else None
    return (
        value.get("id") == application_id
        and value.get("name") == application
        and type(value.get("version")) is int
        and isinstance(configuration, dict)
        and configuration.get("image") == expected_container["image"]
        and value.get("max_instances") == expected_container["max_instances"]
        and configuration.get("wrangler_ssh") == expected_container["ssh"]
        and configuration.get("vcpu") == instance["vcpu"]
        and configuration.get("memory_mib") == instance["memory_mib"]
        and isinstance(disk, dict)
        and disk.get("size_mb") == instance["disk_size_mb"]
        and network == {
            "assign_ipv6": "none",
            "assign_ipv4": "none",
            "mode": "private",
        }
        and isinstance(health, dict)
        and health.get("errors") == []
        and isinstance(instances, dict)
        and type(instances.get("healthy")) is int
        and instances["healthy"] >= 1
        and instances.get("failed") == 0
        and instances.get("starting") == 0
        and instances.get("scheduling") == 0
    )


def wait_for_rollout(
    config_path: pathlib.Path,
    environment: str,
    application: str,
    attempts: int,
    interval_seconds: float,
    expected_image_config_path: pathlib.Path | None = None,
    command_timeout_seconds: float = 60,
) -> dict[str, Any]:
    expected_container = load_expected_container(
        expected_image_config_path or config_path, environment
    )
    last_application: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                [
                    "npx",
                    "wrangler",
                    "containers",
                    "list",
                    "--config",
                    str(config_path),
                    "--env",
                    environment,
                    "--json",
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=command_timeout_seconds,
            )
        except subprocess.SubprocessError:
            result = None
        if (
            result is not None
            and result.returncode == 0
            and len(result.stdout.encode("utf-8")) <= MAX_JSON_BYTES
        ):
            try:
                listed = listed_application(json.loads(result.stdout), application)
            except (UnicodeError, json.JSONDecodeError, RolloutError):
                listed = None
            application_id = listed.get("id") if listed is not None else None
            if isinstance(application_id, str) and re.fullmatch(
                r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", application_id
            ):
                try:
                    info = subprocess.run(
                        [
                            "npx",
                            "wrangler",
                            "containers",
                            "info",
                            application_id,
                            "--config",
                            str(config_path),
                            "--env",
                            environment,
                        ],
                        check=False,
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        timeout=command_timeout_seconds,
                    )
                except subprocess.SubprocessError:
                    info = None
                if (
                    info is not None
                    and info.returncode == 0
                    and len(info.stdout.encode("utf-8")) <= MAX_JSON_BYTES
                ):
                    try:
                        parsed_info = json.loads(info.stdout)
                    except (UnicodeError, json.JSONDecodeError):
                        parsed_info = None
                    if isinstance(parsed_info, dict):
                        last_application = parsed_info
                        if ready_application(
                            parsed_info,
                            application_id,
                            application,
                            expected_container,
                        ):
                            return parsed_info
        if attempt < attempts:
            time.sleep(interval_seconds)
    observed = {}
    if last_application is not None:
        configuration = last_application.get("configuration")
        health = last_application.get("health")
        observed = {
            key: last_application[key]
            for key in ("name", "version", "updated_at")
            if key in last_application
        }
        if isinstance(configuration, dict):
            observed["image"] = configuration.get("image")
        if isinstance(health, dict):
            errors = health.get("errors")
            instances = health.get("instances")
            observed["health"] = {
                "error_count": len(errors) if isinstance(errors, list) else None,
                "instances": {
                    key: instances.get(key)
                    for key in ("healthy", "failed", "starting", "scheduling")
                } if isinstance(instances, dict) else None,
            }
    raise RolloutError(
        "reviewed container rollout did not become ready; "
        f"last observed application: {json.dumps(observed, sort_keys=True)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument(
        "--expected-image-config",
        type=pathlib.Path,
        help="Optional target-commit config used only to select the expected image",
    )
    parser.add_argument(
        "--environment", required=True, choices=("staging", "production")
    )
    parser.add_argument("--application", required=True)
    parser.add_argument("--attempts", type=int, default=80)
    parser.add_argument("--interval-seconds", type=float, default=15)
    parser.add_argument("--command-timeout-seconds", type=float, default=60)
    args = parser.parse_args()
    if (
        not 1 <= args.attempts <= 120
        or not 0 <= args.interval_seconds <= 60
        or not 1 <= args.command_timeout_seconds <= 60
    ):
        print("wait-replay-container-rollout: retry settings are invalid", file=sys.stderr)
        return 1
    try:
        application = wait_for_rollout(
            args.config,
            args.environment,
            args.application,
            args.attempts,
            args.interval_seconds,
            args.expected_image_config,
            args.command_timeout_seconds,
        )
    except (RolloutError, OSError, subprocess.SubprocessError) as error:
        print(f"wait-replay-container-rollout: {error}", file=sys.stderr)
        return 1
    print(
        "reviewed container rollout ready: "
        + json.dumps(
            {
                "name": application["name"],
                "image": application["configuration"]["image"],
                "max_instances": application["max_instances"],
                "vcpu": application["configuration"]["vcpu"],
                "memory_mib": application["configuration"]["memory_mib"],
                "ssh_enabled": application["configuration"]["wrangler_ssh"][
                    "enabled"
                ],
                "version": application["version"],
                "healthy_instances": application["health"]["instances"]["healthy"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
