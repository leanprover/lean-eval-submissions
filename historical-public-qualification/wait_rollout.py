#!/usr/bin/env python3
"""Wait for the isolated historical qualification Container rollout."""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "wait_replay_container_rollout", ROOT / "scripts/wait_replay_container_rollout.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load the reviewed rollout controller")
rollout = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rollout
SPEC.loader.exec_module(rollout)


def load_expected(
    config_path: pathlib.Path,
    environment: str,
    image_family: str = "historical-public",
) -> dict[str, object]:
    if image_family != "historical-public":
        raise rollout.RolloutError("qualification image family changed")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        container = config["env"][environment]["containers"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise rollout.RolloutError("qualification configuration is invalid") from error
    if not isinstance(container, list) or len(container) != 1 or not isinstance(container[0], dict):
        raise rollout.RolloutError("qualification configuration must define one container")
    value = container[0]
    image = value.get("image")
    if not isinstance(image, str) or re.fullmatch(
        r"registry\.cloudflare\.com/[0-9a-f]{32}/lean-eval-historical-public-v1:"
        r"[0-9a-f]{40}-[0-9a-f]{40}@sha256:[0-9a-f]{64}",
        image,
    ) is None:
        raise rollout.RolloutError("qualification image reference is invalid")
    if value.get("instance_type") != "standard-4" or value.get("max_instances") != 1:
        raise rollout.RolloutError("qualification resource boundary changed")
    if value.get("ssh") != {"enabled": False}:
        raise rollout.RolloutError("qualification SSH must be disabled")
    return {
        "image": image,
        "instance_type": "standard-4",
        "max_instances": 1,
        "ssh": {"enabled": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--application", required=True)
    parser.add_argument("--attempts", type=int, default=80)
    parser.add_argument("--interval-seconds", type=float, default=15)
    args = parser.parse_args()
    if args.application != "lean-eval-historical-qualifier-staging-replaysandbox-staging":
        raise rollout.RolloutError("qualification application identity changed")
    if not 1 <= args.attempts <= 120 or not 0 <= args.interval_seconds <= 60:
        raise rollout.RolloutError("qualification rollout wait boundary is invalid")
    if args.output.exists():
        raise rollout.RolloutError("refusing to overwrite rollout evidence")
    rollout.load_expected_container = load_expected
    value = rollout.wait_for_rollout(
        args.config,
        "staging",
        args.application,
        args.attempts,
        args.interval_seconds,
        image_family="historical-public",
    )
    configuration = value["configuration"]
    image = configuration["image"].split("/")[-1]
    tagged_image, manifest_digest = image.split("@", 1)
    repository, tag = tagged_image.split(":", 1)
    evidence = {
        "schema_version": 2,
        "kind": "historical_public_qualification_rollout",
        "qualification_status": "unqualified",
        "name": value["name"],
        "version": value["version"],
        "max_instances": value["max_instances"],
        "image_repository": repository,
        "image_tag": tag,
        "image_manifest_digest": manifest_digest,
        "runtime_boundary": {
            "vcpu": configuration["vcpu"],
            "memory_mib": configuration["memory_mib"],
            "disk_size_mb": configuration["disk"]["size_mb"],
            "network": configuration["network"],
            "ssh": configuration["wrangler_ssh"],
        },
        "health": {
            "errors": value["health"]["errors"],
            "instances": {
                field: value["health"]["instances"][field]
                for field in ("healthy", "failed", "starting", "scheduling")
            },
        },
    }
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except rollout.RolloutError as error:
        print(f"historical qualification rollout: {error}", file=sys.stderr)
        raise SystemExit(1) from None
