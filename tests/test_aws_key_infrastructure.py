from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "infrastructure" / "aws-key-adapter" / "template.yaml"
MAKEFILE_PATH = ROOT / "Makefile"


def _section(template: str, start: str, end: str) -> str:
    return template.split(start, 1)[1].split(end, 1)[0]


class AwsKeyInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = TEMPLATE_PATH.read_text(encoding="utf-8")

    def test_resources_are_closed_and_have_no_public_endpoint(self) -> None:
        resources = _section(self.template, "Resources:\n", "Outputs:\n")
        names = set(re.findall(r"^  ([A-Za-z0-9]+):\n    Type:", resources, re.MULTILINE))
        self.assertEqual(names, {
            "ArchiveIdentityKey",
            "ArchiveIdentityKeyAlias",
            "CapabilityConsumption",
            "WrapRole",
            "MigrationWrapRole",
            "UnwrapFunctionRole",
            "UnwrapFunction",
            "ReplayInvokerRole",
            "ReleaseInvokerRole",
        })
        self.assertNotIn("AWS::Lambda::Url", self.template)
        self.assertNotIn("AWS::ApiGateway", self.template)
        self.assertNotIn("FunctionUrlConfig", self.template)

    def test_lambda_package_build_copies_only_the_two_reviewed_modules(self) -> None:
        makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
        copy_lines = [line for line in makefile.splitlines() if line.startswith("\tcp ")]
        self.assertEqual(copy_lines, [
            '\tcp scripts/aws_key_adapter.py "$(ARTIFACTS_DIR)/aws_key_adapter.py"',
            '\tcp scripts/key_capability_contract.py "$(ARTIFACTS_DIR)/key_capability_contract.py"',
        ])
        self.assertNotIn("*", "\n".join(copy_lines))
        function = _section(self.template, "  UnwrapFunction:\n", "  ReplayInvokerRole:\n")
        self.assertIn("BuildMethod: makefile", function)
        self.assertIn("CodeUri: ../..", function)
        self.assertIn("Handler: aws_key_adapter.lambda_handler", function)

    def test_kms_and_one_use_table_are_environment_scoped(self) -> None:
        key = _section(self.template, "  ArchiveIdentityKey:\n", "  ArchiveIdentityKeyAlias:\n")
        self.assertIn("EnableKeyRotation: true", key)
        table = _section(self.template, "  CapabilityConsumption:\n", "  WrapRole:\n")
        self.assertIn("TableName: !Sub lean-eval-capability-consumption-${EnvironmentName}", table)
        self.assertIn("BillingMode: PAY_PER_REQUEST", table)
        self.assertIn("AttributeName: capability_digest", table)
        self.assertIn("KeyType: HASH", table)
        self.assertIn("AttributeName: expires_at_epoch", table)
        self.assertIn("Enabled: true", table)

    def test_wrap_role_has_only_encrypt_and_exact_oidc_subject(self) -> None:
        role = _section(self.template, "  WrapRole:\n", "  MigrationWrapRole:\n")
        self.assertIn("token.actions.githubusercontent.com:aud: sts.amazonaws.com", role)
        self.assertIn(
            "token.actions.githubusercontent.com:sub: !Sub "
            "repo:${SubmissionGitHubSubjectPrefix}:environment:archive-${EnvironmentName}",
            role,
        )
        self.assertIn("Action: kms:Encrypt", role)
        self.assertNotIn("kms:Decrypt", role)
        self.assertIn("kms:EncryptionContext:contract: lean-eval-archive-key-v1", role)
        for name in (
            "contract",
            "submission_id",
            "archive_ciphertext_sha256",
            "data_key_id",
            "age_recipient_sha256",
        ):
            self.assertIn(f"- {name}", role)
            self.assertIn(f"kms:EncryptionContext:{name}: false", role)

    def test_migration_role_exists_only_in_production_and_has_exact_subject(self) -> None:
        conditions = _section(self.template, "Conditions:\n", "Resources:\n")
        self.assertIn("IsProduction: !Equals [!Ref EnvironmentName, production]", conditions)
        role = _section(
            self.template,
            "  MigrationWrapRole:\n",
            "  UnwrapFunctionRole:\n",
        )
        self.assertIn("Condition: IsProduction", role)
        self.assertIn("RoleName: lean-eval-archive-migration-wrap-production", role)
        self.assertIn(
            "repo:${SubmissionGitHubSubjectPrefix}:environment:archive-migration-production",
            role,
        )
        self.assertNotIn("environment:archive-production", role)
        self.assertIn("Action: kms:Encrypt", role)
        self.assertNotIn("kms:Decrypt", role)
        self.assertNotIn('Resource: "*"', role)
        outputs = self.template.split("Outputs:\n", 1)[1]
        self.assertIn("MigrationWrapRoleArn:\n    Condition: IsProduction", outputs)
        self.assertIn("Value: !GetAtt MigrationWrapRole.Arn", outputs)

    def test_oidc_subject_prefixes_match_github_repository_configuration(self) -> None:
        parameters = _section(self.template, "Parameters:\n", "Resources:\n")
        self.assertIn("Default: leanprover/lean-eval-submissions", parameters)
        self.assertIn(
            "Default: leanprover@7233018/lean-eval-releases@1340741242",
            parameters,
        )
        self.assertIn("(?:@[0-9]+)?", parameters)
        self.assertNotIn("GitHubRepository", parameters)

    def test_function_role_alone_can_consume_and_decrypt(self) -> None:
        role = _section(self.template, "  UnwrapFunctionRole:\n", "  UnwrapFunction:\n")
        self.assertIn("Action: dynamodb:PutItem", role)
        self.assertIn("Action: kms:Decrypt", role)
        self.assertNotIn("Action: kms:Encrypt", role)
        self.assertNotIn('Resource: "*"', role)
        for name in (
            "contract",
            "submission_id",
            "archive_ciphertext_sha256",
            "data_key_id",
            "age_recipient_sha256",
        ):
            self.assertIn(f"kms:EncryptionContext:{name}: false", role)
        function = _section(self.template, "  UnwrapFunction:\n", "  ReplayInvokerRole:\n")
        # One-use is enforced atomically by DynamoDB, so correctness must not
        # depend on Lambda serialization or an account concurrency quota.
        self.assertNotIn("ReservedConcurrentExecutions", function)
        self.assertIn("AutoPublishAlias: live", function)
        self.assertNotIn("Events:", function)

    def test_controller_roles_are_repo_specific_and_invoke_only(self) -> None:
        replay = _section(self.template, "  ReplayInvokerRole:\n", "  ReleaseInvokerRole:\n")
        release = _section(self.template, "  ReleaseInvokerRole:\n", "Outputs:\n")
        self.assertIn(
            "repo:${SubmissionGitHubSubjectPrefix}:environment:replay-${EnvironmentName}",
            replay,
        )
        self.assertNotIn("ReleaseGitHubSubjectPrefix", replay)
        self.assertIn(
            "repo:${ReleaseGitHubSubjectPrefix}:environment:release-${EnvironmentName}",
            release,
        )
        self.assertNotIn("SubmissionGitHubSubjectPrefix", release)
        for role in (replay, release):
            self.assertIn("Action: lambda:InvokeFunction", role)
            self.assertIn("Resource: !Sub ${UnwrapFunction.Arn}:live", role)
            self.assertNotIn("kms:", role)
            self.assertNotIn("dynamodb:", role)
            self.assertNotIn('Resource: "*"', role)


if __name__ == "__main__":
    unittest.main()
