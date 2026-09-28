#!/usr/bin/env python3
"""Fail closed unless a disposable historical-private executor is exactly owned.

This verifier is deliberately read-only.  It binds the active Worker service,
settings, version, public health response, and (when present) container
application to one rendered task-scoped Wrangler configuration.  Its success is
only an authorization input to the separate deletion script.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

NAME = re.compile(r"[a-z0-9-]{1,128}\Z")
BINDING_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
NAMESPACE = re.compile(r"[0-9a-f]{32}\Z")
TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z"
)
UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
INSTANCE_TYPES = {
    "standard-4": {"vcpu": 4, "memory_mib": 12_288, "disk_size_mb": 20_000},
}


class OwnershipError(ValueError):
    pass


def object_value(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OwnershipError(f"{label} is not an object")
    return value


def array_value(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise OwnershipError(f"{label} is not an array")
    return value


def load(path: pathlib.Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OwnershipError(f"{label} is not strict UTF-8 JSON") from error


def text(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise OwnershipError(f"{label} is invalid")
    return value


def _binding_inventory(
    value: Any, label: str
) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    bindings = array_value(value, f"{label} bindings")
    descriptors: list[tuple[str, str, str]] = []
    namespaces: dict[str, str] = {}
    names: set[str] = set()
    for index, item_value in enumerate(bindings):
        item = object_value(item_value, f"{label} binding {index}")
        binding_type = item.get("type")
        name = item.get("name")
        if not isinstance(binding_type, str) or not isinstance(name, str) or not name:
            raise OwnershipError(f"{label} binding {index} has no exact identity")
        if name in names:
            raise OwnershipError(f"{label} has duplicate binding {name}")
        names.add(name)
        if binding_type == "plain_text":
            value_text = item.get("text")
            if not isinstance(value_text, str):
                raise OwnershipError(f"{label} plain-text binding {name} is malformed")
            descriptors.append((binding_type, name, value_text))
        elif binding_type == "durable_object_namespace":
            class_name = item.get("class_name")
            namespace = item.get("namespace_id")
            if (
                not isinstance(class_name, str)
                or not class_name
                or not isinstance(namespace, str)
                or NAMESPACE.fullmatch(namespace) is None
                or namespace in namespaces.values()
            ):
                raise OwnershipError(
                    f"{label} Durable Object binding {name} is malformed"
                )
            descriptors.append((binding_type, name, class_name))
            namespaces[name] = namespace
        else:
            raise OwnershipError(
                f"{label} has unsupported binding type {binding_type!r}"
            )
    return sorted(descriptors), namespaces


def _expected_inventory(config: dict[str, Any]) -> list[tuple[str, str, str]]:
    variables = object_value(config.get("vars"), "rendered Worker variables")
    durable = object_value(
        config.get("durable_objects"), "rendered Durable Object configuration"
    )
    descriptors: list[tuple[str, str, str]] = []
    for name, value in variables.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise OwnershipError("rendered Worker variables are not exact strings")
        descriptors.append(("plain_text", name, value))
    for item_value in array_value(durable.get("bindings"), "rendered DO bindings"):
        item = object_value(item_value, "rendered DO binding")
        if set(item) != {"name", "class_name"}:
            raise OwnershipError("rendered DO binding has an unexpected field")
        descriptors.append(
            (
                "durable_object_namespace",
                text(item["name"], BINDING_NAME, "rendered DO binding name"),
                str(item["class_name"]),
            )
        )
    if len({item[1] for item in descriptors}) != len(descriptors):
        raise OwnershipError("rendered Worker binding names are ambiguous")
    return sorted(descriptors)


def _active_version(
    service: dict[str, Any], deployments: dict[str, Any], worker: str
) -> tuple[str, str]:
    result = object_value(service.get("result"), "Worker service result")
    environment = object_value(
        result.get("default_environment"), "Worker default environment"
    )
    script = object_value(environment.get("script"), "Worker active script")
    tag = script.get("tag")
    if (
        service.get("success") is not True
        or result.get("id") != worker
        or environment.get("environment") != "production"
        or not isinstance(tag, str)
        or not tag
    ):
        raise OwnershipError("Worker service identity differs")

    deployment_result = object_value(
        deployments.get("result"), "Worker deployments result"
    )
    history = array_value(
        deployment_result.get("deployments"), "Worker deployment history"
    )
    if deployments.get("success") is not True or not history:
        raise OwnershipError("Worker has no reviewed active deployment")
    active = object_value(history[0], "active Worker deployment")
    versions = array_value(active.get("versions"), "active Worker versions")
    if len(versions) != 1:
        raise OwnershipError("Worker active deployment is not single-version")
    version = object_value(versions[0], "active Worker version allocation")
    version_id = text(version.get("version_id"), UUID, "active Worker version id")
    if version.get("percentage") != 100:
        raise OwnershipError("Worker active version is not allocated at 100 percent")
    return version_id, tag


def _verify_worker(
    config: dict[str, Any],
    service: dict[str, Any],
    settings: dict[str, Any],
    deployments: dict[str, Any],
    version: dict[str, Any],
    health: dict[str, Any],
) -> tuple[str, dict[str, str]]:
    worker = text(config.get("name"), NAME, "rendered Worker name")
    version_id, tag = _active_version(service, deployments, worker)
    expected_inventory = _expected_inventory(config)

    settings_result = object_value(settings.get("result"), "Worker settings result")
    settings_inventory, settings_namespaces = _binding_inventory(
        settings_result.get("bindings"), "Worker settings"
    )
    if (
        settings.get("success") is not True
        or settings_result.get("compatibility_date") != config.get("compatibility_date")
        or sorted(settings_result.get("compatibility_flags", []))
        != sorted(config.get("compatibility_flags", []))
        or settings_inventory != expected_inventory
    ):
        raise OwnershipError("Worker settings differ from the rendered contract")

    version_result = object_value(version.get("result"), "Worker version result")
    metadata = object_value(version_result.get("metadata"), "Worker version metadata")
    annotations = object_value(
        version_result.get("annotations"), "Worker version annotations"
    )
    resources = object_value(version_result.get("resources"), "Worker version resources")
    version_inventory, version_namespaces = _binding_inventory(
        resources.get("bindings"), "Worker version"
    )
    runtime = object_value(resources.get("script_runtime"), "Worker script runtime")
    configured_containers = array_value(config.get("containers"), "rendered containers")
    if len(configured_containers) != 1:
        raise OwnershipError("rendered executor has no unique container")
    configured_container = object_value(configured_containers[0], "rendered container")
    expected_runtime_containers = [
        {
            "class_name": configured_container.get("class_name"),
            "name": configured_container.get("name"),
        }
    ]
    migrations = array_value(config.get("migrations"), "rendered migrations")
    if not migrations:
        raise OwnershipError("rendered executor has no migration lifecycle")
    final_migration = object_value(migrations[-1], "rendered final migration")
    if (
        version.get("success") is not True
        or version_result.get("id") != version_id
        or TIMESTAMP.fullmatch(str(metadata.get("created_on"))) is None
        or annotations.get("workers/tag") != tag
        or version_inventory != expected_inventory
        or version_namespaces != settings_namespaces
        or runtime.get("migration_tag") != final_migration.get("tag")
        or runtime.get("containers") != expected_runtime_containers
    ):
        raise OwnershipError("active Worker version differs from reviewed identity")

    variables = object_value(config.get("vars"), "rendered Worker variables")
    expected_health = {
        "status": "ok",
        "service": "lean-eval-replay-executor",
        "environment": variables["DEPLOYMENT_ENVIRONMENT"],
        "deployed_commit": variables["DEPLOYED_COMMIT"],
        "replay_enabled": variables["REPLAY_ENABLED"] == "true",
        "historical_public_replay_enabled": (
            variables["HISTORICAL_PUBLIC_REPLAY_ENABLED"] == "true"
        ),
        "staging_acceptance_enabled": variables["STAGING_ACCEPTANCE_ENABLED"] == "true",
        "staging_memory_limit_bytes": int(variables["STAGING_MEMORY_LIMIT_BYTES"]),
        "production_memory_gate_bytes": int(variables["PRODUCTION_MEMORY_GATE_BYTES"]),
        "reviewed_execution_profile_digest": variables[
            "REVIEWED_EXECUTION_PROFILE_DIGEST"
        ],
        "reviewed_measurement_config_digest": variables[
            "REVIEWED_MEASUREMENT_CONFIG_DIGEST"
        ],
        "reviewed_vm_image_digest": variables["REVIEWED_VM_IMAGE_DIGEST"],
        "executor_ownership_tag": variables["EXECUTOR_OWNERSHIP_TAG"],
        "expected_replay_task_id": variables["EXPECTED_REPLAY_TASK_ID"],
        "expected_replay_attempt": variables["EXPECTED_REPLAY_ATTEMPT"],
    }
    if health != expected_health:
        raise OwnershipError("Worker health identity differs from the rendered contract")
    return version_id, settings_namespaces


def _verify_application(
    config: dict[str, Any], applications: list[Any]
) -> dict[str, Any] | None:
    container = object_value(
        array_value(config.get("containers"), "rendered containers")[0],
        "rendered container",
    )
    application_name = text(container.get("name"), NAME, "container application name")
    matches = [
        object_value(item, "container application")
        for item in applications
        if isinstance(item, dict) and item.get("name") == application_name
    ]
    if len(matches) > 1:
        raise OwnershipError("container application identity is ambiguous")
    if not matches:
        return None
    application = matches[0]
    configuration = object_value(
        application.get("configuration"), "container application configuration"
    )
    instance_type = container.get("instance_type")
    instance = INSTANCE_TYPES.get(instance_type)
    if instance is None:
        raise OwnershipError("rendered container instance type is unsupported")
    expected_configuration = {
        "image": container.get("image"),
        "wrangler_ssh": container.get("ssh"),
        "vcpu": instance["vcpu"],
        "memory_mib": instance["memory_mib"],
        "disk": {"size_mb": instance["disk_size_mb"]},
        "network": {
            "assign_ipv6": "none",
            "assign_ipv4": "none",
            "mode": "private",
        },
    }
    application_id = application.get("id")
    version = application.get("version")
    created_at = application.get("created_at")
    if (
        not isinstance(application_id, str)
        or UUID.fullmatch(application_id) is None
        or type(version) is not int
        or version < 1
        or application.get("max_instances") != container.get("max_instances")
        or configuration != expected_configuration
        or not isinstance(created_at, str)
        or TIMESTAMP.fullmatch(created_at) is None
    ):
        raise OwnershipError("container application differs from rendered settings")
    top_level_image = application.get("image")
    if top_level_image is not None and top_level_image != container.get("image"):
        raise OwnershipError("container application image aliases disagree")
    return {
        "application_id": application_id,
        "application_version": version,
        "application_created_at": created_at,
    }


def verify(
    config_value: Any,
    service_value: Any,
    settings_value: Any,
    deployments_value: Any,
    version_value: Any,
    health_value: Any,
    applications_value: Any,
) -> dict[str, Any]:
    config = object_value(config_value, "rendered Wrangler config")
    service = object_value(service_value, "Worker service")
    settings = object_value(settings_value, "Worker settings")
    deployments = object_value(deployments_value, "Worker deployments")
    version = object_value(version_value, "Worker version")
    health = object_value(health_value, "Worker health")
    applications = array_value(applications_value, "container applications")
    worker = text(config.get("name"), NAME, "rendered Worker name")
    application = text(
        object_value(
            array_value(config.get("containers"), "rendered containers")[0],
            "rendered container",
        ).get("name"),
        NAME,
        "rendered container application name",
    )
    active_version, _ = _verify_worker(
        config, service, settings, deployments, version, health
    )
    application_identity = _verify_application(config, applications)
    proof = {
        "schema_version": 1,
        "kind": "historical_private_executor_ownership_proof",
        "worker_name": worker,
        "application_name": application,
        "active_worker_version": active_version,
        "worker_owned": True,
        "application_owned": application_identity is not None,
    }
    if application_identity is not None:
        proof.update(application_identity)
    return proof


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    for name in (
        "config",
        "worker-service",
        "worker-settings",
        "worker-deployments",
        "worker-version",
        "worker-health",
        "container-applications",
        "output",
    ):
        result.add_argument(f"--{name}", required=True, type=pathlib.Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        proof = verify(
            load(args.config, "rendered Wrangler config"),
            load(args.worker_service, "Worker service"),
            load(args.worker_settings, "Worker settings"),
            load(args.worker_deployments, "Worker deployments"),
            load(args.worker_version, "Worker version"),
            load(args.worker_health, "Worker health"),
            load(args.container_applications, "container applications"),
        )
        args.output.write_text(
            json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OwnershipError, OSError, ValueError) as error:
        print(f"historical-private-executor-ownership: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
