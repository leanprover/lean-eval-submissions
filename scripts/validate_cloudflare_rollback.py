#!/usr/bin/env python3
"""Validate a commit-coherent Cloudflare rollback before and after mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import sys
from typing import Any


FULL_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
VERSION_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
SECRET_BINDINGS = {
    "intake": {
        "AUTH_TOKEN_SECRET",
        "GITHUB_OAUTH_CLIENT_ID",
        "GITHUB_OAUTH_CLIENT_SECRET",
        "GITHUB_STATE_TOKEN",
        "LIFECYCLE_CALLBACK_TOKEN",
        "READINESS_TOKEN",
    },
    "broker": {
        "DISPATCH_APP_ID",
        "DISPATCH_APP_PRIVATE_KEY",
        "SOURCE_APP_ID",
        "SOURCE_APP_PRIVATE_KEY",
    },
    "replay": set(),
}
QUALIFICATION_FIXED = {
    "replay_durable_object_migration_tags": ["v1", "v2"],
    "scheduled_reconciliation_when_intake_disabled": "no_op",
    "schema_version": 1,
    "state_event_schema_version": 1,
    "state_repository": "leanprover/lean-eval-state",
    "state_event_schema_path": "schema/state-event-v1.schema.json",
    "wrangler_version": "4.124.0",
}
QUALIFICATION_FIELDS = set(QUALIFICATION_FIXED) | {
    "state_main_commit",
    "state_event_schema_sha256",
    "lifecycle_callback_contract_files",
    "lifecycle_callback_contract_sha256",
}
CALLBACK_CONTRACT_FILES = [
    "server/src/app.ts",
    "server/src/github-state.ts",
    "server/src/state-event.ts",
    "server/src/submission-view.ts",
]
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_CONTRACT_FILE_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024


class RollbackValidationError(ValueError):
    """The proposed rollback unit is not exact or commit-coherent."""


def _object(path: pathlib.Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_JSON_BYTES:
            raise RollbackValidationError(f"JSON object {path} is empty or oversized")
        value = json.loads(raw.decode("utf-8"))
    except RollbackValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RollbackValidationError(f"cannot read JSON object {path}: {error}") from error
    if not isinstance(value, dict):
        raise RollbackValidationError(f"{path} is not a JSON object")
    return value


def _callback_contract_digest(root: pathlib.Path) -> str:
    digest = hashlib.sha256(b"lean-eval-lifecycle-callback-contract-v1\0")
    for relative in CALLBACK_CONTRACT_FILES:
        path = root / relative
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise RollbackValidationError(
                f"cannot read qualified callback contract file {relative}"
            ) from error
        if not raw or len(raw) > MAX_CONTRACT_FILE_BYTES:
            raise RollbackValidationError(
                f"qualified callback contract file {relative} is empty or oversized"
            )
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(raw)).encode("ascii"))
        digest.update(b"\0")
        digest.update(raw)
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_qualification(
    qualification: dict[str, Any],
    target_root: pathlib.Path,
    state_main: dict[str, Any],
    state_schema_path: pathlib.Path,
) -> dict[str, Any]:
    if set(qualification) != QUALIFICATION_FIELDS:
        raise RollbackValidationError(
            "target commit does not carry a closed rollback qualification"
        )
    for name, expected in QUALIFICATION_FIXED.items():
        if qualification.get(name) != expected:
            raise RollbackValidationError(
                f"target rollback qualification has wrong {name}"
            )
    if qualification["lifecycle_callback_contract_files"] != CALLBACK_CONTRACT_FILES:
        raise RollbackValidationError(
            "target rollback qualification has wrong callback contract files"
        )
    callback_digest = _callback_contract_digest(target_root)
    if qualification["lifecycle_callback_contract_sha256"] != callback_digest:
        raise RollbackValidationError(
            "target callback implementation differs from its reviewed qualification"
        )
    package = _object(target_root / "server" / "package.json")
    lock = _object(target_root / "server" / "package-lock.json")
    wanted_wrangler = qualification["wrangler_version"]
    if (
        package.get("devDependencies", {}).get("wrangler") != wanted_wrangler
        or lock.get("packages", {}).get("node_modules/wrangler", {}).get("version")
        != wanted_wrangler
    ):
        raise RollbackValidationError(
            "target deployment toolchain differs from its reviewed qualification"
        )
    if set(state_main) != {"commit", "protected"}:
        raise RollbackValidationError("live State main proof is not closed")
    state_commit = state_main.get("commit")
    if state_main.get("protected") is not True or not isinstance(
        state_commit, str
    ) or COMMIT.fullmatch(state_commit) is None:
        raise RollbackValidationError("live State main is not an exact protected commit")
    if qualification["state_main_commit"] != state_commit:
        raise RollbackValidationError(
            "target qualification is not bound to current protected State main"
        )
    try:
        state_schema = state_schema_path.read_bytes()
    except OSError as error:
        raise RollbackValidationError("cannot read exact State event schema") from error
    if not state_schema or len(state_schema) > MAX_CONTRACT_FILE_BYTES:
        raise RollbackValidationError("exact State event schema is empty or oversized")
    state_digest = hashlib.sha256(state_schema).hexdigest()
    if qualification["state_event_schema_sha256"] != state_digest:
        raise RollbackValidationError(
            "target qualification does not match exact protected State schema"
        )
    return {
        "repository": qualification["state_repository"],
        "commit": state_commit,
        "path": qualification["state_event_schema_path"],
        "sha256": state_digest,
        "callback_contract_sha256": callback_digest,
    }


def _environment(config: dict[str, Any], name: str, label: str) -> dict[str, Any]:
    selected = config.get("env", {}).get(name)
    if not isinstance(selected, dict):
        raise RollbackValidationError(f"{label} has no {name!r} environment")
    return selected


def _plain_bindings(version: dict[str, Any], label: str) -> dict[str, str]:
    bindings = version.get("resources", {}).get("bindings")
    if not isinstance(bindings, list):
        raise RollbackValidationError(f"{label} version has no bindings list")
    result: dict[str, str] = {}
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("type") != "plain_text":
            continue
        name = binding.get("name")
        value = binding.get("text")
        if not isinstance(name, str) or not isinstance(value, str):
            raise RollbackValidationError(f"{label} has a malformed plain-text binding")
        if name in result:
            raise RollbackValidationError(f"{label} has duplicate binding {name}")
        result[name] = value
    return result


def _expected_vars(
    selected: dict[str, Any], expected_commit: str, label: str
) -> dict[str, str]:
    variables = selected.get("vars")
    if not isinstance(variables, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in variables.items()
    ):
        raise RollbackValidationError(f"{label} vars are not a string map")
    result = dict(variables)
    if result.get("DEPLOYED_COMMIT") != "development":
        raise RollbackValidationError(
            f"{label} tracked DEPLOYED_COMMIT must be the deployment placeholder"
        )
    result["DEPLOYED_COMMIT"] = expected_commit
    return result


def _validate_version(
    *,
    label: str,
    version: dict[str, Any],
    version_id: str,
    expected_bindings: dict[str, str],
) -> None:
    if not VERSION_ID.fullmatch(version_id):
        raise RollbackValidationError(f"{label} version ID is not a canonical UUID")
    if version.get("id") != version_id:
        raise RollbackValidationError(f"{label} version payload ID does not match its input")
    actual = _plain_bindings(version, label)
    if actual != expected_bindings:
        missing = sorted(set(expected_bindings) - set(actual))
        extra = sorted(set(actual) - set(expected_bindings))
        wrong = sorted(
            name
            for name in set(actual) & set(expected_bindings)
            if actual[name] != expected_bindings[name]
        )
        raise RollbackValidationError(
            f"{label} plain-text bindings differ (missing={missing}, extra={extra}, wrong={wrong})"
        )


def _capability_descriptors(
    version: dict[str, Any], label: str
) -> list[tuple[Any, ...]]:
    bindings = version.get("resources", {}).get("bindings")
    if not isinstance(bindings, list):
        raise RollbackValidationError(f"{label} version has no bindings list")
    result: list[tuple[Any, ...]] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            raise RollbackValidationError(f"{label} has a malformed binding")
        binding_type = binding.get("type")
        name = binding.get("name")
        if not isinstance(binding_type, str) or not isinstance(name, str):
            raise RollbackValidationError(f"{label} has an unidentified binding")
        if binding_type == "plain_text":
            continue
        if binding_type == "secret_text":
            result.append((binding_type, name))
        elif binding_type == "service":
            result.append(
                (
                    binding_type,
                    name,
                    binding.get("service"),
                    binding.get("environment"),
                )
            )
        elif binding_type == "ratelimit":
            simple = binding.get("simple")
            result.append(
                (
                    binding_type,
                    name,
                    binding.get("namespace_id"),
                    json.dumps(simple, sort_keys=True, separators=(",", ":")),
                )
            )
        elif binding_type == "durable_object_namespace":
            namespace = binding.get("namespace_id")
            if not isinstance(namespace, str) or re.fullmatch(
                r"[0-9a-f]{32}", namespace
            ) is None:
                raise RollbackValidationError(
                    f"{label} has an invalid Durable Object namespace"
                )
            result.append(
                (binding_type, name, binding.get("class_name"), namespace)
            )
        else:
            raise RollbackValidationError(
                f"{label} has unsupported capability binding type {binding_type!r}"
            )
    if len(result) != len(set(result)):
        raise RollbackValidationError(f"{label} has duplicate capability bindings")
    return sorted(result)


def _expected_capabilities(
    component: str, config: dict[str, Any], environment: str
) -> list[tuple[Any, ...]]:
    selected = _environment(config, environment, f"{component} config")
    required_secrets = selected.get("secrets", {}).get("required", [])
    expected_secret_names = sorted(SECRET_BINDINGS[component])
    if required_secrets != expected_secret_names:
        raise RollbackValidationError(
            f"{component} config does not declare the exact required secret contract"
        )
    expected: list[tuple[Any, ...]] = [
        ("secret_text", name) for name in expected_secret_names
    ]
    if component == "intake":
        for service in selected.get("services", []):
            expected.append(
                (
                    "service",
                    service.get("binding"),
                    service.get("service"),
                    service.get("environment", environment),
                )
            )
        for rate in selected.get("ratelimits", []):
            expected.append(
                (
                    "ratelimit",
                    rate.get("name"),
                    rate.get("namespace_id"),
                    json.dumps(
                        rate.get("simple"), sort_keys=True, separators=(",", ":")
                    ),
                )
            )
    elif component == "replay":
        for binding in selected.get("durable_objects", {}).get("bindings", []):
            expected.append(
                (
                    "durable_object_namespace",
                    binding.get("name"),
                    binding.get("class_name"),
                    None,
                )
            )
    return sorted(expected)


def _validate_capabilities(
    component: str,
    config: dict[str, Any],
    version: dict[str, Any],
    environment: str,
) -> None:
    actual = _capability_descriptors(version, component)
    expected = _expected_capabilities(component, config, environment)
    if component == "replay":
        actual_without_namespace = [item[:-1] + (None,) for item in actual]
        if len({item[-1] for item in actual}) != len(actual):
            raise RollbackValidationError("replay Durable Object namespaces are not unique")
        actual = actual_without_namespace
    if actual != expected:
        raise RollbackValidationError(
            f"{component} capability bindings differ from the reviewed contract"
        )


def _validate_intake_service(
    config: dict[str, Any], version: dict[str, Any], environment: str
) -> None:
    selected = _environment(config, environment, "intake config")
    configured = selected.get("services")
    if not isinstance(configured, list):
        raise RollbackValidationError("intake config has no service bindings")
    expected = sorted(
        (
            item.get("binding"),
            item.get("service"),
            item.get("environment", environment),
        )
        for item in configured
        if isinstance(item, dict)
    )
    bindings = version.get("resources", {}).get("bindings", [])
    actual = sorted(
        (item.get("name"), item.get("service"), item.get("environment"))
        for item in bindings
        if isinstance(item, dict) and item.get("type") == "service"
    )
    if actual != expected:
        raise RollbackValidationError(
            f"intake service bindings differ (actual={actual}, expected={expected})"
        )


def _validate_replay_container(
    config: dict[str, Any], version: dict[str, Any], environment: str
) -> None:
    selected = _environment(config, environment, "replay config")
    worker_name = selected.get("name", config.get("name"))
    containers = selected.get("containers")
    if not isinstance(worker_name, str) or not isinstance(containers, list):
        raise RollbackValidationError("replay config has no exact Worker/container unit")
    expected = sorted(
        (
            item.get("class_name"),
            f"{worker_name}-{str(item.get('class_name', '')).lower()}-{environment}",
        )
        for item in containers
        if isinstance(item, dict)
    )
    runtime = version.get("resources", {}).get("script_runtime", {})
    actual_containers = runtime.get("containers") if isinstance(runtime, dict) else None
    if not isinstance(actual_containers, list):
        raise RollbackValidationError("replay version has no container runtime")
    actual = sorted(
        (item.get("class_name"), item.get("name"))
        for item in actual_containers
        if isinstance(item, dict)
    )
    if actual != expected:
        raise RollbackValidationError(
            f"replay container unit differs (actual={actual}, expected={expected})"
        )
    migrations = selected.get("migrations")
    if not isinstance(migrations, list) or not migrations:
        raise RollbackValidationError("replay config has no Durable Object lifecycle")
    final_tag = migrations[-1].get("tag") if isinstance(migrations[-1], dict) else None
    if runtime.get("migration_tag") != final_tag:
        raise RollbackValidationError(
            "replay version migration tag differs from the target lifecycle"
        )


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    if not FULL_COMMIT.fullmatch(args.expected_commit):
        raise RollbackValidationError("expected commit must be a full lowercase Git SHA")
    intake_config = _object(args.intake_config)
    broker_config = _object(args.broker_config)
    replay_config = _object(args.replay_config)
    state_contract = _validate_qualification(
        _object(args.qualification),
        args.target_root,
        _object(args.state_main),
        args.state_schema,
    )
    intake_selected = _environment(intake_config, args.environment, "intake config")
    broker_selected = _environment(broker_config, args.environment, "broker config")
    replay_selected = _environment(replay_config, args.environment, "replay config")

    intake_expected = _expected_vars(
        intake_selected, args.expected_commit, "intake config"
    )
    intake_expected["DISPATCH_WORKFLOW_REF"] = (
        f"lean-eval-dispatch/{args.expected_commit}"
    )
    broker_expected = _expected_vars(
        broker_selected, args.expected_commit, "broker config"
    )
    replay_expected = _expected_vars(
        replay_selected, args.expected_commit, "replay config"
    )

    intake_version = _object(args.intake_version)
    broker_version = _object(args.broker_version)
    replay_version = _object(args.replay_version)
    _validate_version(
        label="intake",
        version=intake_version,
        version_id=args.intake_version_id,
        expected_bindings=intake_expected,
    )
    _validate_version(
        label="broker",
        version=broker_version,
        version_id=args.broker_version_id,
        expected_bindings=broker_expected,
    )
    _validate_version(
        label="replay",
        version=replay_version,
        version_id=args.replay_version_id,
        expected_bindings=replay_expected,
    )
    _validate_intake_service(intake_config, intake_version, args.environment)
    _validate_replay_container(replay_config, replay_version, args.environment)
    _validate_capabilities(
        "intake", intake_config, intake_version, args.environment
    )
    _validate_capabilities(
        "broker", broker_config, broker_version, args.environment
    )
    _validate_capabilities(
        "replay", replay_config, replay_version, args.environment
    )

    current_replay_config = _object(args.current_replay_config)
    target_migrations = replay_selected.get("migrations")
    current_migrations = _environment(
        current_replay_config, args.environment, "current replay config"
    ).get("migrations")
    if target_migrations != current_migrations:
        raise RollbackValidationError(
            "target replay Durable Object lifecycle differs from the current contract"
        )
    migration_tags = [
        item.get("tag") for item in target_migrations if isinstance(item, dict)
    ] if isinstance(target_migrations, list) else []
    if migration_tags != QUALIFICATION_FIXED["replay_durable_object_migration_tags"]:
        raise RollbackValidationError(
            "target replay migrations do not match its qualified schema epoch"
        )

    def enabled(variables: dict[str, str], name: str) -> bool:
        value = variables.get(name)
        if value not in {"true", "false"}:
            raise RollbackValidationError(f"{name} is not an explicit JSON boolean string")
        return value == "true"

    def positive_decimal(variables: dict[str, str], name: str) -> int:
        value = variables.get(name)
        if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]*", value) is None:
            raise RollbackValidationError(f"{name} is not a positive decimal integer")
        return int(value)

    for name in (
        "REVIEWED_EXECUTION_PROFILE_DIGEST",
        "REVIEWED_MEASUREMENT_CONFIG_DIGEST",
    ):
        if re.fullmatch(r"[0-9a-f]{64}", replay_expected.get(name, "")) is None:
            raise RollbackValidationError(f"{name} is not a canonical SHA-256 digest")
    if re.fullmatch(
        r"sha256:[0-9a-f]{64}", replay_expected.get("REVIEWED_VM_IMAGE_DIGEST", "")
    ) is None:
        raise RollbackValidationError(
            "REVIEWED_VM_IMAGE_DIGEST is not a canonical registry digest"
        )

    plan = {
        "schema_version": 1,
        "environment": args.environment,
        "expected_commit": args.expected_commit,
        "version_ids": {
            "broker": args.broker_version_id,
            "replay": args.replay_version_id,
            "intake": args.intake_version_id,
        },
        "intake_enabled": enabled(intake_expected, "INTAKE_ENABLED"),
        # Rollback targets predating the canary binding had no canary route and
        # are therefore safely equivalent to explicit false. A present value
        # remains strict and malformed values still fail closed.
        "promotion_canary_enabled": False
        if "PROMOTION_CANARY_ENABLED" not in intake_expected
        else enabled(intake_expected, "PROMOTION_CANARY_ENABLED"),
        "replay_enabled": enabled(replay_expected, "REPLAY_ENABLED"),
        "staging_acceptance_enabled": enabled(
            replay_expected, "STAGING_ACCEPTANCE_ENABLED"
        ),
        "staging_memory_limit_bytes": positive_decimal(
            replay_expected, "STAGING_MEMORY_LIMIT_BYTES"
        ),
        "production_memory_gate_bytes": positive_decimal(
            replay_expected, "PRODUCTION_MEMORY_GATE_BYTES"
        ),
        "reviewed_execution_profile_digest": replay_expected[
            "REVIEWED_EXECUTION_PROFILE_DIGEST"
        ],
        "reviewed_measurement_config_digest": replay_expected[
            "REVIEWED_MEASUREMENT_CONFIG_DIGEST"
        ],
        "reviewed_vm_image_digest": replay_expected["REVIEWED_VM_IMAGE_DIGEST"],
        "state_contract": state_contract,
    }
    if args.require_disabled and any(
        plan[name]
        for name in (
            "intake_enabled",
            "promotion_canary_enabled",
            "replay_enabled",
            "staging_acceptance_enabled",
        )
    ):
        raise RollbackValidationError(
            "an emergency production rollback target must disable intake, promotion canary, and replay"
        )
    return plan


def validate_component(args: argparse.Namespace) -> None:
    if not FULL_COMMIT.fullmatch(args.expected_commit):
        raise RollbackValidationError("expected commit must be a full lowercase Git SHA")
    config = _object(args.config)
    selected = _environment(config, args.environment, f"{args.component} config")
    expected = _expected_vars(
        selected, args.expected_commit, f"{args.component} config"
    )
    if args.component == "intake":
        expected["DISPATCH_WORKFLOW_REF"] = (
            f"lean-eval-dispatch/{args.expected_commit}"
        )
    version = _object(args.version)
    _validate_version(
        label=args.component,
        version=version,
        version_id=args.version_id,
        expected_bindings=expected,
    )
    if args.component == "intake":
        _validate_intake_service(config, version, args.environment)
    elif args.component == "replay":
        _validate_replay_container(config, version, args.environment)
    _validate_capabilities(args.component, config, version, args.environment)


def validate_compatible_capabilities(
    component: str, target: dict[str, Any], current: dict[str, Any]
) -> None:
    if _capability_descriptors(target, f"target {component}") != _capability_descriptors(
        current, f"current {component}"
    ):
        raise RollbackValidationError(
            f"target and current {component} capability bindings differ"
        )
    if set(_plain_bindings(target, f"target {component}")) != set(
        _plain_bindings(current, f"current {component}")
    ):
        raise RollbackValidationError(
            f"target and current {component} plain binding names differ"
        )
    if component == "replay":
        target_runtime = target.get("resources", {}).get("script_runtime")
        current_runtime = current.get("resources", {}).get("script_runtime")
        if not isinstance(target_runtime, dict) or not isinstance(current_runtime, dict):
            raise RollbackValidationError(
                "target and current replay versions lack exact runtime metadata"
            )
        target_tag = target_runtime.get("migration_tag")
        current_tag = current_runtime.get("migration_tag")
        if not isinstance(target_tag, str) or current_tag != target_tag:
            raise RollbackValidationError(
                "target and active replay migration tags differ"
            )


def container_id(value: Any, application: str) -> str:
    if not isinstance(value, list):
        raise RollbackValidationError("container application list is invalid")
    matches = [
        item.get("id")
        for item in value
        if isinstance(item, dict) and item.get("name") == application
    ]
    if len(matches) != 1 or not isinstance(matches[0], str) or not VERSION_ID.fullmatch(
        matches[0]
    ):
        raise RollbackValidationError("container application is not uniquely identified")
    return matches[0]


def validate_status(status: dict[str, Any], target_version: str) -> None:
    if not VERSION_ID.fullmatch(target_version):
        raise RollbackValidationError("target version ID is not a canonical UUID")
    expected = [{"version_id": target_version, "percentage": 100}]
    if status.get("versions") != expected:
        raise RollbackValidationError(
            f"deployment is not 100% on {target_version}: {status.get('versions')!r}"
        )


def active_version(status: dict[str, Any]) -> str:
    versions = status.get("versions")
    if not isinstance(versions, list) or len(versions) != 1:
        raise RollbackValidationError("deployment does not contain one active version")
    selected = versions[0]
    if not isinstance(selected, dict) or selected.get("percentage") != 100:
        raise RollbackValidationError("deployment is not at 100 percent")
    version_id = selected.get("version_id")
    if not isinstance(version_id, str) or not VERSION_ID.fullmatch(version_id):
        raise RollbackValidationError("deployment version ID is not a canonical UUID")
    return version_id


def validate_health(plan: dict[str, Any], component: str, health: dict[str, Any]) -> None:
    common = {
        "status": "ok",
        "environment": plan["environment"],
        "deployed_commit": plan["expected_commit"],
    }
    expected = dict(common)
    if component == "intake":
        expected["intake_enabled"] = plan["intake_enabled"]
    elif component == "replay":
        expected.update(
            {
                "service": "lean-eval-replay-executor",
                "replay_enabled": plan["replay_enabled"],
                "staging_acceptance_enabled": plan["staging_acceptance_enabled"],
                "staging_memory_limit_bytes": plan["staging_memory_limit_bytes"],
                "production_memory_gate_bytes": plan[
                    "production_memory_gate_bytes"
                ],
                "reviewed_execution_profile_digest": plan[
                    "reviewed_execution_profile_digest"
                ],
                "reviewed_measurement_config_digest": plan[
                    "reviewed_measurement_config_digest"
                ],
                "reviewed_vm_image_digest": plan["reviewed_vm_image_digest"],
            }
        )
    else:
        raise RollbackValidationError(f"unsupported health component {component!r}")
    wrong = {
        key: (health.get(key), value)
        for key, value in expected.items()
        if health.get(key) != value
    }
    if wrong:
        raise RollbackValidationError(f"{component} health differs: {wrong}")


def _descriptor_digest(version: dict[str, Any], component: str) -> str:
    raw = json.dumps(
        _capability_descriptors(version, component),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"lean-eval-cloudflare-capabilities-v1\0" + raw).hexdigest()


def build_prestate(args: argparse.Namespace) -> dict[str, Any]:
    plan = _object(args.plan)
    if set(plan) != {
        "schema_version", "environment", "expected_commit", "version_ids",
        "intake_enabled", "promotion_canary_enabled", "replay_enabled",
        "staging_acceptance_enabled",
        "staging_memory_limit_bytes", "production_memory_gate_bytes",
        "reviewed_execution_profile_digest",
        "reviewed_measurement_config_digest", "reviewed_vm_image_digest",
        "state_contract",
    }:
        raise RollbackValidationError("rollback plan is not a closed document")
    versions: dict[str, dict[str, Any]] = {}
    active_ids: dict[str, str] = {}
    capability_digests: dict[str, str] = {}
    for component in ("intake", "broker", "replay"):
        status = _object(getattr(args, f"{component}_status"))
        version = _object(getattr(args, f"{component}_version"))
        active = active_version(status)
        if version.get("id") != active:
            raise RollbackValidationError(
                f"original {component} version does not match active deployment"
            )
        active_ids[component] = active
        capability_digests[component] = _descriptor_digest(version, component)
        versions[component] = version

    replay_runtime = versions["replay"].get("resources", {}).get("script_runtime")
    if not isinstance(replay_runtime, dict) or not isinstance(
        replay_runtime.get("migration_tag"), str
    ):
        raise RollbackValidationError("original replay runtime has no migration tag")

    container = _object(args.container_info)
    configuration = container.get("configuration")
    disk = configuration.get("disk") if isinstance(configuration, dict) else None
    network = configuration.get("network") if isinstance(configuration, dict) else None
    ssh = configuration.get("wrangler_ssh") if isinstance(configuration, dict) else None
    vcpu = configuration.get("vcpu") if isinstance(configuration, dict) else None
    if (
        not isinstance(container.get("id"), str)
        or VERSION_ID.fullmatch(container["id"]) is None
        or not isinstance(container.get("name"), str)
        or re.fullmatch(r"[a-z0-9-]{1,128}", container["name"]) is None
        or type(container.get("version")) is not int
        or container["version"] < 1
        or type(container.get("max_instances")) is not int
        or container["max_instances"] < 1
        or not isinstance(configuration, dict)
        or not isinstance(configuration.get("image"), str)
        or re.fullmatch(
            r"registry\.cloudflare\.com/[0-9a-f]{32}/[a-z0-9-]+:[0-9a-f]{40}",
            configuration["image"],
        ) is None
        or type(vcpu) not in {int, float}
        or not math.isfinite(float(vcpu))
        or vcpu <= 0
        or type(configuration.get("memory_mib")) is not int
        or configuration["memory_mib"] < 1
        or not isinstance(disk, dict)
        or type(disk.get("size_mb")) is not int
        or disk["size_mb"] < 1
        or not isinstance(network, dict)
        or network.get("mode") != "private"
        or network.get("assign_ipv4") != "none"
        or network.get("assign_ipv6") != "none"
        or not isinstance(ssh, dict)
        or set(ssh) != {"enabled"}
        or not isinstance(ssh["enabled"], bool)
    ):
        raise RollbackValidationError("original container recovery state is malformed")
    return {
        "schema_version": 1,
        "kind": "cloudflare_rollback_prestate",
        "environment": plan["environment"],
        "rollback_expected_commit": plan["expected_commit"],
        "original_version_ids": active_ids,
        "original_capability_contract_sha256": capability_digests,
        "original_replay_migration_tag": replay_runtime["migration_tag"],
        "original_container": {
            "id": container["id"],
            "name": container["name"],
            "version": container["version"],
            "image": configuration["image"],
            "max_instances": container["max_instances"],
            "vcpu": vcpu,
            "memory_mib": configuration["memory_mib"],
            "disk_size_mb": disk["size_mb"],
            "network": {
                key: network.get(key)
                for key in ("mode", "assign_ipv4", "assign_ipv6")
            },
            "ssh_enabled": ssh["enabled"],
        },
        "contains_secret_values": False,
        "contains_worker_source": False,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--expected-commit", required=True)
    plan.add_argument("--environment", choices=("staging", "production"), required=True)
    for component in ("intake", "broker", "replay"):
        plan.add_argument(f"--{component}-config", type=pathlib.Path, required=True)
        plan.add_argument(f"--{component}-version", type=pathlib.Path, required=True)
        plan.add_argument(f"--{component}-version-id", required=True)
    plan.add_argument("--output", type=pathlib.Path, required=True)
    plan.add_argument("--require-disabled", action="store_true")
    plan.add_argument("--current-replay-config", type=pathlib.Path, required=True)
    plan.add_argument("--qualification", type=pathlib.Path, required=True)
    plan.add_argument("--target-root", type=pathlib.Path, required=True)
    plan.add_argument("--state-main", type=pathlib.Path, required=True)
    plan.add_argument("--state-schema", type=pathlib.Path, required=True)

    component = commands.add_parser("component")
    component.add_argument(
        "--component", choices=("intake", "broker", "replay"), required=True
    )
    component.add_argument("--expected-commit", required=True)
    component.add_argument(
        "--environment", choices=("staging", "production"), required=True
    )
    component.add_argument("--config", type=pathlib.Path, required=True)
    component.add_argument("--version", type=pathlib.Path, required=True)
    component.add_argument("--version-id", required=True)

    status = commands.add_parser("status")
    status.add_argument("--status", type=pathlib.Path, required=True)
    status.add_argument("--target-version", required=True)

    active = commands.add_parser("active-version")
    active.add_argument("--status", type=pathlib.Path, required=True)

    compatible = commands.add_parser("compatible-capabilities")
    compatible.add_argument(
        "--component", choices=("intake", "broker", "replay"), required=True
    )
    compatible.add_argument("--target-version", type=pathlib.Path, required=True)
    compatible.add_argument("--current-version", type=pathlib.Path, required=True)

    container = commands.add_parser("container-id")
    container.add_argument("--list", type=pathlib.Path, required=True)
    container.add_argument("--application", required=True)

    health = commands.add_parser("health")
    health.add_argument("--plan", type=pathlib.Path, required=True)
    health.add_argument("--component", choices=("intake", "replay"), required=True)
    health.add_argument("--health", type=pathlib.Path, required=True)

    prestate = commands.add_parser("prestate")
    prestate.add_argument("--plan", type=pathlib.Path, required=True)
    for component_name in ("intake", "broker", "replay"):
        prestate.add_argument(
            f"--{component_name}-status", type=pathlib.Path, required=True
        )
        prestate.add_argument(
            f"--{component_name}-version", type=pathlib.Path, required=True
        )
    prestate.add_argument("--container-info", type=pathlib.Path, required=True)
    prestate.add_argument("--output", type=pathlib.Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "plan":
            plan = build_plan(args)
            args.output.write_text(
                json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        elif args.command == "component":
            validate_component(args)
        elif args.command == "status":
            validate_status(_object(args.status), args.target_version)
        elif args.command == "active-version":
            print(active_version(_object(args.status)))
        elif args.command == "compatible-capabilities":
            validate_compatible_capabilities(
                args.component,
                _object(args.target_version),
                _object(args.current_version),
            )
        elif args.command == "container-id":
            value = json.loads(args.list.read_text(encoding="utf-8"))
            print(container_id(value, args.application))
        elif args.command == "prestate":
            args.output.write_text(
                json.dumps(build_prestate(args), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            validate_health(_object(args.plan), args.component, _object(args.health))
    except RollbackValidationError as error:
        print(f"rollback validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
