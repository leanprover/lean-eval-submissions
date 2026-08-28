from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest
from collections.abc import Callable

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preflight_production_wrap_role as preflight


def valid_values() -> dict[str, object]:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": preflight.OIDC_PROVIDER_ARN},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": preflight.OIDC_SUBJECT,
                    }
                },
            }
        ],
    }
    required = {
        f"kms:EncryptionContext:{key}": "false" for key in preflight.CONTEXT_KEYS
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "kms:Encrypt",
                "Resource": preflight.KEY_ARN,
                "Condition": {
                    "StringEquals": {
                        "kms:EncryptionContext:contract": "lean-eval-archive-key-v1"
                    },
                    "ForAllValues:StringEquals": {
                        "kms:EncryptionContextKeys": preflight.CONTEXT_KEYS
                    },
                    "Null": required,
                },
            }
        ],
    }
    key_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "EnableAccountIamPolicies",
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{preflight.ACCOUNT}:root"},
                "Action": "kms:*",
                "Resource": "*",
            }
        ],
    }
    return {
        "caller": {"Account": preflight.ACCOUNT},
        "stack": {
            "Stacks": [
                {
                    "StackName": preflight.STACK,
                    "StackStatus": "CREATE_COMPLETE",
                    "Outputs": [
                        {
                            "OutputKey": "WrapRoleArn",
                            "OutputValue": preflight.ROLE_ARN,
                        },
                        {
                            "OutputKey": "KmsKeyArn",
                            "OutputValue": preflight.KEY_ARN,
                        },
                    ],
                }
            ]
        },
        "role": {
            "Role": {
                "RoleName": preflight.ROLE_NAME,
                "Arn": preflight.ROLE_ARN,
                "MaxSessionDuration": 3600,
                "AssumeRolePolicyDocument": trust,
            }
        },
        "inline": {"PolicyNames": ["EncryptOneArchiveIdentity"]},
        "attached": {"AttachedPolicies": []},
        "instances": {"InstanceProfiles": []},
        "policy": {
            "RoleName": preflight.ROLE_NAME,
            "PolicyName": "EncryptOneArchiveIdentity",
            "PolicyDocument": policy,
        },
        "key": {
            "KeyMetadata": {
                "Arn": preflight.KEY_ARN,
                "AWSAccountId": preflight.ACCOUNT,
                "Enabled": True,
                "KeyManager": "CUSTOMER",
                "KeySpec": "SYMMETRIC_DEFAULT",
                "KeyUsage": "ENCRYPT_DECRYPT",
                "MultiRegion": False,
                "Origin": "AWS_KMS",
            }
        },
        "alias_key": {
            "KeyMetadata": {
                "Arn": preflight.KEY_ARN,
                "AWSAccountId": preflight.ACCOUNT,
                "Enabled": True,
                "KeyManager": "CUSTOMER",
                "KeySpec": "SYMMETRIC_DEFAULT",
                "KeyUsage": "ENCRYPT_DECRYPT",
                "MultiRegion": False,
                "Origin": "AWS_KMS",
            }
        },
        "key_policy": {"Policy": json.dumps(key_policy)},
        "grants": {"Grants": []},
        "rotation": {"KeyRotationEnabled": True, "RotationPeriodInDays": 365},
    }


class ProductionWrapRolePreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.values = valid_values()

    def test_accepts_only_the_reviewed_live_boundary(self) -> None:
        calls: list[tuple[str, ...]] = []
        responses = iter(self.values.values())

        def reader(arguments: list[str]) -> object:
            calls.append(tuple(arguments))
            return next(responses)

        result = preflight.run_preflight(reader)
        self.assertEqual(result["status"], "production-wrap-boundary-ready")
        self.assertEqual(result["role_arn"], preflight.ROLE_ARN)
        self.assertEqual(len(calls), 12)
        self.assertEqual(calls[0], ("sts", "get-caller-identity"))
        self.assertEqual(calls[-1][0:2], ("kms", "get-key-rotation-status"))

    def test_rejects_every_authority_expansion(self) -> None:
        hostile: list[tuple[str, Callable[[dict[str, object]], object]]] = [
            (
                "wrong trust subject",
                lambda value: value["role"]["Role"]["AssumeRolePolicyDocument"][
                    "Statement"
                ][0]["Condition"]["StringEquals"].__setitem__(
                    "token.actions.githubusercontent.com:sub",
                    "repo:leanprover/lean-eval-submissions:environment:any",
                ),
            ),
            (
                "permissions boundary",
                lambda value: value["role"]["Role"].__setitem__(
                    "PermissionsBoundary", {"PermissionsBoundaryArn": "arn:unexpected"}
                ),
            ),
            (
                "extra inline policy",
                lambda value: value["inline"]["PolicyNames"].append("Unexpected"),
            ),
            (
                "managed policy",
                lambda value: value["attached"]["AttachedPolicies"].append(
                    {"PolicyArn": "arn:unexpected"}
                ),
            ),
            (
                "instance profile",
                lambda value: value["instances"]["InstanceProfiles"].append(
                    {"Arn": "arn:unexpected"}
                ),
            ),
            (
                "decrypt",
                lambda value: value["policy"]["PolicyDocument"]["Statement"][
                    0
                ].__setitem__("Action", ["kms:Encrypt", "kms:Decrypt"]),
            ),
            (
                "wildcard resource",
                lambda value: value["policy"]["PolicyDocument"]["Statement"][
                    0
                ].__setitem__("Resource", "*"),
            ),
            (
                "optional context",
                lambda value: value["policy"]["PolicyDocument"]["Statement"][0][
                    "Condition"
                ]["Null"].__setitem__("kms:EncryptionContext:submission_id", "true"),
            ),
            (
                "direct key grant",
                lambda value: value["grants"]["Grants"].append(
                    {"Operations": ["Decrypt"]}
                ),
            ),
            (
                "broadened key policy",
                lambda value: value["key_policy"].__setitem__(
                    "Policy",
                    json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Principal": {"AWS": preflight.ROLE_ARN},
                                    "Action": "kms:Decrypt",
                                    "Resource": "*",
                                }
                            ],
                        }
                    ),
                ),
            ),
        ]
        validators = (
            preflight.validate_role,
            lambda value: preflight.validate_policy_names(
                value["inline"], value["attached"], value["instances"]
            ),
            preflight.validate_role_policy,
            preflight.validate_key_policy,
            preflight.validate_key_grants,
        )
        validator_for_case = (0, 0, 1, 1, 1, 2, 2, 2, 4, 3)
        source_for_case = (
            "role",
            None,
            None,
            None,
            None,
            "policy",
            "policy",
            "policy",
            "grants",
            "key_policy",
        )
        for index, (label, mutate) in enumerate(hostile):
            with self.subTest(label=label):
                value = copy.deepcopy(self.values)
                mutate(value)
                validator = validators[validator_for_case[index]]
                source = source_for_case[index]
                with self.assertRaises(preflight.PreflightError):
                    validator(value if source is None else value[source])

    def test_rejects_wrong_account_stack_key_and_rotation(self) -> None:
        cases = (
            ("caller", preflight.validate_caller, "Account", "000000000000"),
            (
                "stack",
                preflight.validate_stack,
                "StackStatus",
                "UPDATE_ROLLBACK_COMPLETE",
            ),
            ("key", preflight.validate_key, "Enabled", False),
            ("rotation", preflight.validate_rotation, "KeyRotationEnabled", False),
        )
        for source, validator, field, replacement in cases:
            with self.subTest(source=source):
                value = copy.deepcopy(self.values[source])
                if source == "stack":
                    value["Stacks"][0][field] = replacement
                elif source == "key":
                    value["KeyMetadata"][field] = replacement
                else:
                    value[field] = replacement
                with self.assertRaises(preflight.PreflightError):
                    validator(value)


if __name__ == "__main__":
    unittest.main()
