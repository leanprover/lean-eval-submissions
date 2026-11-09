#!/usr/bin/env python3
"""Wait for the exact version acknowledged by a Wrangler deployment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from validate_cloudflare_rollback import (
    RollbackValidationError,
    VERSION_ID,
    validate_status,
)


def deployed_version(path: Path, environment: str, worker_name: str) -> str:
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    deployments = [
        item for item in records
        if isinstance(item, dict) and item.get("type") == "deploy"
    ]
    if len(deployments) != 1:
        raise ValueError("Wrangler output must contain exactly one deployment")
    deployment = deployments[0]
    version = deployment.get("version_id")
    if (
        deployment.get("version") != 1
        or deployment.get("wrangler_environment") != environment
        or deployment.get("worker_name") != worker_name
        or not isinstance(version, str)
        or not VERSION_ID.fullmatch(version)
    ):
        raise ValueError("Wrangler deployment identity differs")
    return version


def wait_for_deployment(
    wrangler: str, config: Path, environment: str, version: str, output: Path
) -> None:
    for attempt in range(13):
        try:
            result = subprocess.run(
                [
                    wrangler, "deployments", "status", "--config", str(config),
                    "--env", environment, "--json",
                ],
                capture_output=True, text=True, check=True, timeout=30,
            )
            status = json.loads(result.stdout)
            if not isinstance(status, dict):
                raise ValueError("deployment status is not an object")
            validate_status(status, version)
        except (subprocess.SubprocessError, ValueError, RollbackValidationError) as error:
            print(f"Waiting for deployment {version}: {error}", file=sys.stderr)
            if attempt == 12:
                raise ValueError("exact Worker deployment did not converge") from error
            time.sleep(5)
        else:
            output.write_text(json.dumps(status) + "\n")
            return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wrangler", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--worker-name", required=True)
    parser.add_argument("--deploy-output", type=Path, required=True)
    parser.add_argument("--status-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        version = deployed_version(args.deploy_output, args.environment, args.worker_name)
        wait_for_deployment(
            args.wrangler, args.config, args.environment, version, args.status_output
        )
    except (OSError, ValueError) as error:
        print(f"deployment verification failed: {error}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
