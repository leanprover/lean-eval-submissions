#!/usr/bin/env python3
"""Fail closed unless the live production archive role is exactly Wrap-only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

ACCOUNT = "161072922960"
REGION = "us-east-1"
STACK = "lean-eval-key-adapter-production"
ROLE_NAME = "lean-eval-archive-wrap-production"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}"
KEY_ARN = f"arn:aws:kms:{REGION}:{ACCOUNT}:key/219904f9-4952-400f-b60a-6f027c4d070b"
KEY_ALIAS = "alias/lean-eval-archive-identities-production"
OIDC_PROVIDER_ARN = (
    f"arn:aws:iam::{ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
)
OIDC_SUBJECT = "repo:leanprover/lean-eval-submissions:environment:archive-production"
CONTEXT_KEYS = [
    "contract",
    "submission_id",
    "archive_ciphertext_sha256",
    "data_key_id",
    "age_recipient_sha256",
]


class PreflightError(ValueError):
    """The live role or key differs from the reviewed production boundary."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PreflightError(f"{label} is not an object")
    return value


def _false(value: Any) -> bool:
    return value is False or value == "false"


def validate_caller(value: Any) -> None:
    caller = _object(value, "caller identity")
    if caller.get("Account") != ACCOUNT:
        raise PreflightError("AWS caller is not in the Lean Eval account")


def validate_stack(value: Any) -> None:
    response = _object(value, "CloudFormation response")
    stacks = response.get("Stacks")
    if not isinstance(stacks, list) or len(stacks) != 1:
        raise PreflightError("production stack lookup was not singular")
    stack = _object(stacks[0], "production stack")
    if stack.get("StackName") != STACK or stack.get("StackStatus") not in {
        "CREATE_COMPLETE",
        "UPDATE_COMPLETE",
    }:
        raise PreflightError("production stack identity or status is unexpected")
    outputs = stack.get("Outputs")
    if not isinstance(outputs, list):
        raise PreflightError("production stack outputs are missing")
    selected = {
        item.get("OutputKey"): item.get("OutputValue")
        for item in outputs
        if isinstance(item, dict)
    }
    if selected.get("WrapRoleArn") != ROLE_ARN:
        raise PreflightError("production stack Wrap role output drifted")
    if selected.get("KmsKeyArn") != KEY_ARN:
        raise PreflightError("production stack KMS key output drifted")


def validate_role(value: Any) -> None:
    response = _object(value, "IAM role response")
    role = _object(response.get("Role"), "IAM role")
    if (
        role.get("RoleName") != ROLE_NAME
        or role.get("Arn") != ROLE_ARN
        or role.get("MaxSessionDuration") != 3600
        or "PermissionsBoundary" in role
    ):
        raise PreflightError("production Wrap role properties drifted")
    expected_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": OIDC_PROVIDER_ARN},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": OIDC_SUBJECT,
                    }
                },
            }
        ],
    }
    if role.get("AssumeRolePolicyDocument") != expected_trust:
        raise PreflightError("production Wrap role trust drifted")


def validate_policy_names(inline: Any, attached: Any, instances: Any) -> None:
    inline_value = _object(inline, "inline-policy response")
    attached_value = _object(attached, "attached-policy response")
    instance_value = _object(instances, "instance-profile response")
    if inline_value.get("PolicyNames") != ["EncryptOneArchiveIdentity"]:
        raise PreflightError("production Wrap role inline policies drifted")
    if attached_value.get("AttachedPolicies") != []:
        raise PreflightError("production Wrap role has a managed policy")
    if instance_value.get("InstanceProfiles") != []:
        raise PreflightError("production Wrap role has an instance profile")


def validate_role_policy(value: Any) -> None:
    response = _object(value, "role-policy response")
    if (
        response.get("RoleName") != ROLE_NAME
        or response.get("PolicyName") != "EncryptOneArchiveIdentity"
    ):
        raise PreflightError("production Wrap policy identity drifted")
    policy = _object(response.get("PolicyDocument"), "production Wrap policy")
    if set(policy) != {"Version", "Statement"} or policy.get("Version") != "2012-10-17":
        raise PreflightError("production Wrap policy envelope drifted")
    statements = policy.get("Statement")
    if not isinstance(statements, list) or len(statements) != 1:
        raise PreflightError("production Wrap policy is not one statement")
    statement = _object(statements[0], "production Wrap statement")
    if set(statement) != {"Effect", "Action", "Resource", "Condition"}:
        raise PreflightError("production Wrap statement fields drifted")
    if (
        statement.get("Effect") != "Allow"
        or statement.get("Action") != "kms:Encrypt"
        or statement.get("Resource") != KEY_ARN
    ):
        raise PreflightError("production Wrap action or resource drifted")
    condition = _object(statement.get("Condition"), "production Wrap condition")
    if set(condition) != {"StringEquals", "ForAllValues:StringEquals", "Null"}:
        raise PreflightError("production Wrap condition operators drifted")
    if condition.get("StringEquals") != {
        "kms:EncryptionContext:contract": "lean-eval-archive-key-v1"
    }:
        raise PreflightError("production Wrap contract condition drifted")
    if condition.get("ForAllValues:StringEquals") != {
        "kms:EncryptionContextKeys": CONTEXT_KEYS
    }:
        raise PreflightError("production Wrap context-key allowlist drifted")
    nulls = _object(condition.get("Null"), "production Wrap required contexts")
    expected_null_keys = {f"kms:EncryptionContext:{key}" for key in CONTEXT_KEYS}
    if set(nulls) != expected_null_keys or not all(
        _false(value) for value in nulls.values()
    ):
        raise PreflightError("production Wrap required contexts drifted")


def validate_key(value: Any) -> None:
    response = _object(value, "KMS key response")
    metadata = _object(response.get("KeyMetadata"), "KMS key metadata")
    expected = {
        "Arn": KEY_ARN,
        "AWSAccountId": ACCOUNT,
        "Enabled": True,
        "KeyManager": "CUSTOMER",
        "KeySpec": "SYMMETRIC_DEFAULT",
        "KeyUsage": "ENCRYPT_DECRYPT",
        "MultiRegion": False,
        "Origin": "AWS_KMS",
    }
    if any(
        metadata.get(key) != expected_value for key, expected_value in expected.items()
    ):
        raise PreflightError("production KMS key properties drifted")


def validate_key_policy(value: Any) -> None:
    response = _object(value, "KMS key-policy response")
    raw = response.get("Policy")
    if not isinstance(raw, str):
        raise PreflightError("production KMS key policy is missing")
    try:
        policy = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PreflightError("production KMS key policy is invalid JSON") from error
    expected = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "EnableAccountIamPolicies",
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:root"},
                "Action": "kms:*",
                "Resource": "*",
            }
        ],
    }
    if policy != expected:
        raise PreflightError("production KMS key policy drifted")


def validate_key_grants(value: Any) -> None:
    response = _object(value, "KMS grants response")
    if response.get("Grants") != []:
        raise PreflightError("production KMS key has a grant")


def validate_rotation(value: Any) -> None:
    response = _object(value, "KMS rotation response")
    if (
        response.get("KeyRotationEnabled") is not True
        or response.get("RotationPeriodInDays") != 365
    ):
        raise PreflightError("production KMS key does not use annual rotation")


def _aws_json(arguments: list[str]) -> Any:
    command = ["aws", *arguments, "--region", REGION, "--output", "json"]
    environment = os.environ.copy()
    environment["AWS_PAGER"] = ""
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
        return json.loads(result.stdout)
    except FileNotFoundError as error:
        raise PreflightError("aws CLI is not installed") from error
    except subprocess.CalledProcessError as error:
        raise PreflightError("read-only AWS command failed") from error
    except json.JSONDecodeError as error:
        raise PreflightError("AWS command did not return JSON") from error


def run_preflight(reader: Callable[[list[str]], Any] = _aws_json) -> dict[str, str]:
    validate_caller(reader(["sts", "get-caller-identity"]))
    validate_stack(reader(["cloudformation", "describe-stacks", "--stack-name", STACK]))
    validate_role(reader(["iam", "get-role", "--role-name", ROLE_NAME]))
    validate_policy_names(
        reader(["iam", "list-role-policies", "--role-name", ROLE_NAME]),
        reader(["iam", "list-attached-role-policies", "--role-name", ROLE_NAME]),
        reader(["iam", "list-instance-profiles-for-role", "--role-name", ROLE_NAME]),
    )
    validate_role_policy(
        reader(
            [
                "iam",
                "get-role-policy",
                "--role-name",
                ROLE_NAME,
                "--policy-name",
                "EncryptOneArchiveIdentity",
            ]
        )
    )
    validate_key(reader(["kms", "describe-key", "--key-id", KEY_ARN]))
    validate_key(reader(["kms", "describe-key", "--key-id", KEY_ALIAS]))
    validate_key_policy(
        reader(
            [
                "kms",
                "get-key-policy",
                "--key-id",
                KEY_ARN,
                "--policy-name",
                "default",
            ]
        )
    )
    validate_key_grants(reader(["kms", "list-grants", "--key-id", KEY_ARN]))
    validate_rotation(reader(["kms", "get-key-rotation-status", "--key-id", KEY_ARN]))
    return {
        "account": ACCOUNT,
        "key_alias": KEY_ALIAS,
        "key_arn": KEY_ARN,
        "role_arn": ROLE_ARN,
        "status": "production-wrap-boundary-ready",
        "subject": OIDC_SUBJECT,
    }


def main() -> int:
    try:
        result = run_preflight()
    except PreflightError as error:
        print(f"production Wrap preflight: {error}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
