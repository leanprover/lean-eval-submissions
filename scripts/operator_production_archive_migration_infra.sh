#!/usr/bin/env bash
set -euo pipefail

# Provision only the production archive-migration KMS boundary and install its
# non-secret GitHub role selector. This script never receives the legacy
# identity and never dispatches migration, replay, release, or State workflows.

readonly SOURCE_COMMIT=5397ca582e3d38a88ffda928a48a479a6e9afb6d
readonly SOURCE_ROOT="https://raw.githubusercontent.com/leanprover/lean-eval-submissions/${SOURCE_COMMIT}"
readonly EXPECTED_TEMPLATE_SHA=aac24318c973523a65b76af34b8e1408a5680f61b52c4fb996f93967253ef94d
readonly EXPECTED_ADAPTER_SHA=8aee87e2e8704125d610f7d6d2957dc2e7c518fd12a948de8976d68169857ca1
readonly EXPECTED_CONTRACT_SHA=b9cd5cee74228f09b7fb5e8d27ea33ef7584d69659814ca449adeb11b5d988d9

readonly AWS_REGION=us-east-1
readonly AWS_ACCOUNT=161072922960
readonly STACK=lean-eval-key-adapter-production
readonly STAGING_STACK=lean-eval-key-adapter-staging
readonly SAM_MANAGED_STACK=aws-sam-cli-managed-default
readonly REPOSITORY=leanprover/lean-eval-submissions
readonly ENVIRONMENT=archive-migration-production
readonly OIDC_PROVIDER_ARN=arn:aws:iam::161072922960:oidc-provider/token.actions.githubusercontent.com
readonly SUBMISSION_PREFIX=leanprover/lean-eval-submissions
readonly RELEASE_PREFIX=leanprover@7233018/lean-eval-releases@1340741242
readonly MIGRATION_ROLE=lean-eval-archive-migration-wrap-production
readonly MIGRATION_ROLE_ARN=arn:aws:iam::161072922960:role/lean-eval-archive-migration-wrap-production
readonly WRAP_ROLE=lean-eval-archive-wrap-production
readonly FUNCTION_ROLE=lean-eval-archive-unwrap-function-production
readonly FUNCTION_NAME=lean-eval-archive-unwrap-production
readonly FUNCTION_ALIAS_ARN=arn:aws:lambda:us-east-1:161072922960:function:lean-eval-archive-unwrap-production:live
readonly REPLAY_ROLE=lean-eval-replay-unwrap-invoker-production
readonly RELEASE_ROLE=lean-eval-release-unwrap-invoker-production

for command in aws awk cmp curl gh jq python3 sha256sum stat; do
  command -v "$command" >/dev/null
done

operator_tmp_root="$(realpath -e -- "${TMPDIR:-/tmp}")"
test -d "$operator_tmp_root"
ops="$(mktemp -d "$operator_tmp_root/lean-eval-production-migration.XXXXXXXX")"
chmod 700 "$ops"
change_set_id=
change_set_owned=false
execution_attempted=false
complete=false

remove_operator_material() {
  if [[ -L "$ops" || ! -d "$ops" ]]; then
    echo "REFUSING to remove an invalid operator directory" >&2
    return 1
  fi
  local parent basename canonical
  parent="$(dirname -- "$ops")"
  basename="$(basename -- "$ops")"
  canonical="$(realpath -e -- "$ops")"
  if [[ "$(realpath -e -- "$parent")" != "$operator_tmp_root" ||
        "$ops" != "$operator_tmp_root/$basename" ||
        "$canonical" != "$operator_tmp_root/$basename" ||
        ! "$basename" =~ ^lean-eval-production-migration\.[A-Za-z0-9]{8}$ ]]; then
    echo "REFUSING to remove an unexpected operator directory" >&2
    return 1
  fi
  chmod -R u+rwX "$ops" 2>/dev/null || true
  rm -rf -- "$ops"
}

cleanup() {
  local status=$?
  set +e
  trap - EXIT HUP INT TERM
  if [[ "$change_set_owned" == true && "$execution_attempted" == false ]]; then
    aws cloudformation delete-change-set \
      --stack-name "$STACK" \
      --change-set-name "$change_set_id" \
      --region "$AWS_REGION"
  fi
  remove_operator_material
  if [[ "$complete" != true && $status -eq 0 ]]; then
    status=1
  fi
  if [[ "$complete" != true ]]; then
    echo "PRODUCTION_ARCHIVE_MIGRATION_INFRA_INCOMPLETE" >&2
  fi
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

echo step=identity-and-boundary
aws sts get-caller-identity --output json > "$ops/caller.json"
jq -e --arg account "$AWS_ACCOUNT" '
  .Account == $account and
  .Arn == ("arn:aws:iam::" + $account + ":root")
' "$ops/caller.json" >/dev/null

gh api "repos/$REPOSITORY/environments/$ENVIRONMENT" > "$ops/environment-before.json"
jq -e '
  .name == "archive-migration-production" and
  .deployment_branch_policy == {
    protected_branches: true,
    custom_branch_policies: false
  } and
  [.protection_rules[].type] == ["branch_policy"]
' "$ops/environment-before.json" >/dev/null
gh api "repos/$REPOSITORY/environments/$ENVIRONMENT/variables" \
  > "$ops/environment-variables-before.json"
jq -e --arg migration "$MIGRATION_ROLE_ARN" '
  .total_count == 1 and
  [.variables[] | {name, value}] == [{name: "AWS_WRAP_ROLE_ARN", value: $migration}]
' "$ops/environment-variables-before.json" >/dev/null
gh api "repos/$REPOSITORY/environments/$ENVIRONMENT/secrets" \
  > "$ops/environment-secrets-before.json"
jq -e '
  ([.secrets[].name] | sort) == ["AUDIT_MIGRATION_READ_KEY"] and
  ([.secrets[].name] | index("LEGACY_ARCHIVE_IDENTITY")) == null
' "$ops/environment-secrets-before.json" >/dev/null

test "$(gh api "repos/$REPOSITORY/branches/main" --jq .protected)" = true
main_commit="$(gh api "repos/$REPOSITORY/branches/main" --jq .commit.sha)"
[[ "$main_commit" =~ ^[0-9a-f]{40}$ ]]
test "$(gh api "repos/$REPOSITORY/compare/$SOURCE_COMMIT...$main_commit" \
  --jq .status)" = ahead

echo step=live-stack-preflight
aws cloudformation describe-stacks --stack-name "$STACK" --region "$AWS_REGION" \
  --output json > "$ops/stack-before.json"
jq -e \
  --arg provider "$OIDC_PROVIDER_ARN" \
  --arg submissions "$SUBMISSION_PREFIX" \
  --arg releases "$RELEASE_PREFIX" '
  (.Stacks | length) == 1 and
  (.Stacks[0].StackStatus == "CREATE_COMPLETE" or
   .Stacks[0].StackStatus == "UPDATE_COMPLETE") and
  (.Stacks[0].EnableTerminationProtection | type) == "boolean" and
  (.Stacks[0].Parameters | map({key: .ParameterKey, value: .ParameterValue}) | from_entries) == {
    EnvironmentName: "production",
    GitHubOidcProviderArn: $provider,
    ReleaseGitHubRepository: $releases,
    SubmissionGitHubRepository: $submissions
  } and
  (.Stacks[0].Outputs | map(.OutputKey) | sort) == [
    "AdapterName",
    "CapabilityTableName",
    "KmsKeyArn",
    "ReleaseInvokerRoleArn",
    "ReplayInvokerRoleArn",
    "UnwrapFunctionAliasArn",
    "WrapRoleArn"
  ]
' "$ops/stack-before.json" >/dev/null

staging_updated_before="$(aws cloudformation describe-stacks \
  --stack-name "$STAGING_STACK" --region "$AWS_REGION" \
  --query 'Stacks[0].LastUpdatedTime' --output text)"
test -n "$staging_updated_before"

aws cloudformation describe-stack-resources --stack-name "$STACK" \
  --region "$AWS_REGION" --output json > "$ops/resources-before.json"
jq -e '
  ([.StackResources[] | select(.ResourceStatus | endswith("_COMPLETE") | not)] | length) == 0 and
  ([.StackResources[].LogicalResourceId] | index("MigrationWrapRole")) == null
' "$ops/resources-before.json" >/dev/null

capture_role() {
  local role=$1 prefix=$2 policy=$3
  aws iam get-role --role-name "$role" --query Role.AssumeRolePolicyDocument \
    --output json | jq -S . > "$ops/$prefix-trust-before.json"
  aws iam list-role-policies --role-name "$role" --output json \
    | jq -S . > "$ops/$prefix-inline-before.json"
  aws iam list-attached-role-policies --role-name "$role" --output json \
    | jq -S . > "$ops/$prefix-attached-before.json"
  aws iam get-role-policy --role-name "$role" --policy-name "$policy" \
    --query PolicyDocument --output json | jq -S . > "$ops/$prefix-policy-before.json"
}
capture_role "$WRAP_ROLE" wrap EncryptOneArchiveIdentity
capture_role "$REPLAY_ROLE" replay InvokeOnlyTheUnwrapAlias
capture_role "$RELEASE_ROLE" release InvokeOnlyTheUnwrapAlias
capture_role "$FUNCTION_ROLE" function ConsumeAndDecryptOneIdentity

jq -e --arg provider "$OIDC_PROVIDER_ARN" '
  .Statement[0].Principal.Federated == $provider and
  .Statement[0].Condition.StringEquals."token.actions.githubusercontent.com:sub" ==
    "repo:leanprover@7233018/lean-eval-releases@1340741242:environment:release-production"
' "$ops/release-trust-before.json" >/dev/null
jq -e '
  [.Statement[] | select(.Action == "kms:Decrypt") |
    .Condition.StringEquals."kms:EncryptionContext:contract"] ==
    ["lean-eval-archive-key-v1"]
' "$ops/function-policy-before.json" >/dev/null

aws lambda get-function-configuration --function-name "$FUNCTION_NAME" \
  --region "$AWS_REGION" --output json > "$ops/function-before.json"
aws lambda get-alias --function-name "$FUNCTION_NAME" --name live \
  --region "$AWS_REGION" --output json > "$ops/alias-before.json"
jq -e --arg role "arn:aws:iam::$AWS_ACCOUNT:role/$FUNCTION_ROLE" '
  .FunctionName == "lean-eval-archive-unwrap-production" and
  .Runtime == "python3.13" and .Handler == "aws_key_adapter.lambda_handler" and
  .Role == $role and .MemorySize == 128 and .Timeout == 10 and
  .TracingConfig.Mode == "PassThrough" and
  .Environment.Variables.LEAN_EVAL_ADAPTER_NAME == "aws-kms-v1" and
  .State == "Active" and .LastUpdateStatus == "Successful"
' "$ops/function-before.json" >/dev/null
jq -e --arg arn "$FUNCTION_ALIAS_ARN" '
  .AliasArn == $arn and .Name == "live" and
  (.FunctionVersion | test("^[1-9][0-9]*$")) and
  (.RoutingConfig.AdditionalVersionWeights | length) == 0
' "$ops/alias-before.json" >/dev/null

if aws iam get-role --role-name "$MIGRATION_ROLE" >/dev/null 2>&1; then
  echo "migration role unexpectedly already exists" >&2
  exit 1
fi

echo step=fetch-and-build-pinned-source
mkdir -p "$ops/source/infrastructure/aws-key-adapter" "$ops/source/scripts"
fetch_source() {
  local path=$1 expected=$2
  curl --fail --location --proto '=https' --tlsv1.2 \
    --output "$ops/source/$path" "$SOURCE_ROOT/$path"
  test "$(sha256sum "$ops/source/$path" | cut -d ' ' -f 1)" = "$expected"
}
fetch_source infrastructure/aws-key-adapter/template.yaml "$EXPECTED_TEMPLATE_SHA"
fetch_source scripts/aws_key_adapter.py "$EXPECTED_ADAPTER_SHA"
fetch_source scripts/key_capability_contract.py "$EXPECTED_CONTRACT_SHA"

aws cloudformation describe-stacks --stack-name "$SAM_MANAGED_STACK" \
  --region "$AWS_REGION" --output json > "$ops/sam-managed-stack.json"
sam_bucket="$(jq -er '
  select(
    (.Stacks | length) == 1 and
    (.Stacks[0].StackStatus == "CREATE_COMPLETE" or
     .Stacks[0].StackStatus == "UPDATE_COMPLETE")
  ) |
  .Stacks[0].Outputs[] | select(.OutputKey == "SourceBucket") | .OutputValue
' "$ops/sam-managed-stack.json")"
[[ "$sam_bucket" =~ ^aws-sam-cli-managed-default-samclisourcebucket-[a-z0-9]+$ ]]

python3 - "$ops/source/scripts" "$ops/lambda.zip" <<'PY'
import pathlib
import sys
import zipfile

source = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for name in ("aws_key_adapter.py", "key_capability_contract.py"):
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, (source / name).read_bytes())
PY
lambda_zip_sha="$(sha256sum "$ops/lambda.zip" | cut -d ' ' -f 1)"
[[ "$lambda_zip_sha" =~ ^[0-9a-f]{64}$ ]]
lambda_s3_key="lean-eval-production-migration/$lambda_zip_sha.zip"
aws s3api put-object --bucket "$sam_bucket" --key "$lambda_s3_key" \
  --body "$ops/lambda.zip" --region "$AWS_REGION" \
  --output json > "$ops/put-object.json"
test "$(aws s3api head-object --bucket "$sam_bucket" --key "$lambda_s3_key" \
  --region "$AWS_REGION" --query ContentLength --output text)" = \
  "$(stat -c %s "$ops/lambda.zip")"

awk -v bucket="$sam_bucket" -v key="$lambda_s3_key" '
  $0 == "      CodeUri: ../.." {
    print "      CodeUri:"
    print "        Bucket: " bucket
    print "        Key: " key
    replaced += 1
    next
  }
  { print }
  END { if (replaced != 1) exit 1 }
' "$ops/source/infrastructure/aws-key-adapter/template.yaml" \
  > "$ops/packaged-template.yaml"
aws cloudformation validate-template \
  --template-body "file://$ops/packaged-template.yaml" \
  --region "$AWS_REGION" --output json > "$ops/validated-template.json"

echo step=create-and-inspect-change-set
change_set="archive-migration-production-$(date -u +%Y%m%dT%H%M%SZ)-$$"
aws cloudformation create-change-set \
  --stack-name "$STACK" \
  --change-set-name "$change_set" \
  --change-set-type UPDATE \
  --description "Provision exact archive migration v2 key boundary from $SOURCE_COMMIT" \
  --template-body "file://$ops/packaged-template.yaml" \
  --parameters \
    ParameterKey=EnvironmentName,UsePreviousValue=true \
    ParameterKey=GitHubOidcProviderArn,UsePreviousValue=true \
    ParameterKey=SubmissionGitHubSubjectPrefix,ParameterValue="$SUBMISSION_PREFIX" \
    ParameterKey=ReleaseGitHubSubjectPrefix,ParameterValue="$RELEASE_PREFIX" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" --output json > "$ops/create-change-set.json"

change_set_id="$(jq -er --arg stack "$STACK" --arg name "$change_set" '
  select(keys == ["Id", "StackId"]) |
  select(.StackId | test(
    "^arn:aws:cloudformation:us-east-1:161072922960:stack/" + $stack +
    "/[0-9a-f-]{36}$"
  )) |
  select(.Id | test(
    "^arn:aws:cloudformation:us-east-1:161072922960:changeSet/" + $name +
    "/[0-9a-f-]{36}$"
  )) |
  .Id
' "$ops/create-change-set.json")"
change_set_owned=true
if ! aws cloudformation wait change-set-create-complete --stack-name "$STACK" \
  --change-set-name "$change_set_id" --region "$AWS_REGION"; then
  if aws cloudformation describe-change-set --stack-name "$STACK" \
    --change-set-name "$change_set_id" --include-property-values \
    --region "$AWS_REGION" --output json > "$ops/change-set-failure.json"; then
    jq '{Status, ExecutionStatus, StatusReason}' \
      "$ops/change-set-failure.json" >&2
  else
    echo 'change set creation failed and its terminal status could not be read' >&2
  fi
  exit 1
fi
aws cloudformation describe-change-set --stack-name "$STACK" \
  --change-set-name "$change_set_id" --include-property-values \
  --region "$AWS_REGION" --output json > "$ops/change-set.json"

jq '[.Changes[].ResourceChange | {
  action: .Action,
  logical_resource: .LogicalResourceId,
  type: .ResourceType,
  replacement: .Replacement
}]' "$ops/change-set.json"
jq -e '
  .Status == "CREATE_COMPLETE" and
  (.Changes | length) >= 5 and (.Changes | length) <= 6 and
  all(.Changes[].ResourceChange;
    if .LogicalResourceId == "MigrationWrapRole" then
      .Action == "Add" and .ResourceType == "AWS::IAM::Role" and .Replacement == null
    elif .LogicalResourceId == "UnwrapFunctionRole" then
      .Action == "Modify" and .ResourceType == "AWS::IAM::Role" and .Replacement == "False"
    elif .LogicalResourceId == "UnwrapFunction" then
      .Action == "Modify" and .ResourceType == "AWS::Lambda::Function" and .Replacement == "False"
    elif .LogicalResourceId == "UnwrapFunctionAliaslive" then
      .Action == "Modify" and .ResourceType == "AWS::Lambda::Alias" and .Replacement == "False"
    elif (.LogicalResourceId | test("^UnwrapFunctionVersion[0-9a-f]{10}$")) then
      (.Action == "Add" or .Action == "Remove") and
      .ResourceType == "AWS::Lambda::Version" and .Replacement == null
    else false end
  ) and
  ([.Changes[].ResourceChange | select(
    .LogicalResourceId == "MigrationWrapRole" and .Action == "Add"
  )] | length) == 1 and
  ([.Changes[].ResourceChange | select(
    .LogicalResourceId == "UnwrapFunctionRole" and .Action == "Modify"
  )] | length) == 1 and
  ([.Changes[].ResourceChange | select(
    .LogicalResourceId == "UnwrapFunction" and .Action == "Modify"
  )] | length) == 1 and
  ([.Changes[].ResourceChange | select(
    .LogicalResourceId == "UnwrapFunctionAliaslive" and .Action == "Modify"
  )] | length) == 1 and
  ([.Changes[].ResourceChange | select(
    (.LogicalResourceId | test("^UnwrapFunctionVersion[0-9a-f]{10}$")) and
    .Action == "Add"
  )] | length) == 1 and
  ([.Changes[].ResourceChange | select(
    (.LogicalResourceId | test("^UnwrapFunctionVersion[0-9a-f]{10}$")) and
    .Action == "Remove"
  )] | length) <= 1
' "$ops/change-set.json" >/dev/null

echo step=execute-production-change-set
execution_attempted=true
aws cloudformation execute-change-set --stack-name "$STACK" \
  --change-set-name "$change_set_id" --region "$AWS_REGION"
aws cloudformation wait stack-update-complete --stack-name "$STACK" \
  --region "$AWS_REGION"

echo step=post-update-verification
aws cloudformation describe-stacks --stack-name "$STACK" --region "$AWS_REGION" \
  --output json > "$ops/stack-after.json"
jq -e \
  --arg provider "$OIDC_PROVIDER_ARN" \
  --arg submissions "$SUBMISSION_PREFIX" \
  --arg releases "$RELEASE_PREFIX" \
  --arg role "$MIGRATION_ROLE_ARN" \
  --slurpfile before "$ops/stack-before.json" '
  (.Stacks | length) == 1 and .Stacks[0].StackStatus == "UPDATE_COMPLETE" and
  .Stacks[0].EnableTerminationProtection ==
    $before[0].Stacks[0].EnableTerminationProtection and
  (.Stacks[0].Parameters | map({key: .ParameterKey, value: .ParameterValue}) | from_entries) == {
    EnvironmentName: "production",
    GitHubOidcProviderArn: $provider,
    ReleaseGitHubSubjectPrefix: $releases,
    SubmissionGitHubSubjectPrefix: $submissions
  } and
  (.Stacks[0].Outputs | map(.OutputKey) | sort) == [
    "AdapterName",
    "CapabilityTableName",
    "KmsKeyArn",
    "MigrationWrapRoleArn",
    "ReleaseInvokerRoleArn",
    "ReplayInvokerRoleArn",
    "UnwrapFunctionAliasArn",
    "WrapRoleArn"
  ] and
  (.Stacks[0].Outputs[] | select(.OutputKey == "MigrationWrapRoleArn") | .OutputValue) == $role
' "$ops/stack-after.json" >/dev/null

for output in AdapterName CapabilityTableName KmsKeyArn ReleaseInvokerRoleArn \
  ReplayInvokerRoleArn UnwrapFunctionAliasArn WrapRoleArn; do
  before="$(jq -er --arg key "$output" '.Stacks[0].Outputs[] | select(.OutputKey == $key) | .OutputValue' "$ops/stack-before.json")"
  after="$(jq -er --arg key "$output" '.Stacks[0].Outputs[] | select(.OutputKey == $key) | .OutputValue' "$ops/stack-after.json")"
  test "$before" = "$after"
done
test "$(aws cloudformation describe-stacks --stack-name "$STAGING_STACK" \
  --region "$AWS_REGION" --query 'Stacks[0].LastUpdatedTime' --output text)" = \
  "$staging_updated_before"

compare_role() {
  local role=$1 prefix=$2 policy=$3
  aws iam get-role --role-name "$role" --query Role.AssumeRolePolicyDocument \
    --output json | jq -S . > "$ops/$prefix-trust-after.json"
  aws iam list-role-policies --role-name "$role" --output json \
    | jq -S . > "$ops/$prefix-inline-after.json"
  aws iam list-attached-role-policies --role-name "$role" --output json \
    | jq -S . > "$ops/$prefix-attached-after.json"
  aws iam get-role-policy --role-name "$role" --policy-name "$policy" \
    --query PolicyDocument --output json | jq -S . > "$ops/$prefix-policy-after.json"
  cmp "$ops/$prefix-trust-before.json" "$ops/$prefix-trust-after.json"
  cmp "$ops/$prefix-inline-before.json" "$ops/$prefix-inline-after.json"
  cmp "$ops/$prefix-attached-before.json" "$ops/$prefix-attached-after.json"
  cmp "$ops/$prefix-policy-before.json" "$ops/$prefix-policy-after.json"
}
compare_role "$WRAP_ROLE" wrap EncryptOneArchiveIdentity
compare_role "$REPLAY_ROLE" replay InvokeOnlyTheUnwrapAlias
compare_role "$RELEASE_ROLE" release InvokeOnlyTheUnwrapAlias

aws iam get-role --role-name "$MIGRATION_ROLE" --query Role.AssumeRolePolicyDocument \
  --output json > "$ops/migration-trust.json"
aws iam get-role-policy --role-name "$MIGRATION_ROLE" \
  --policy-name EncryptOneArchiveFileKey --query PolicyDocument \
  --output json > "$ops/migration-policy.json"
jq -e --arg provider "$OIDC_PROVIDER_ARN" '
  .Statement == [{
    Action: "sts:AssumeRoleWithWebIdentity",
    Condition: {StringEquals: {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub":
        "repo:leanprover/lean-eval-submissions:environment:archive-migration-production"
    }},
    Effect: "Allow",
    Principal: {Federated: $provider}
  }]
' "$ops/migration-trust.json" >/dev/null
jq -e '
  .Statement == [{
    Action: "kms:Encrypt",
    Condition: {
      "ForAllValues:StringEquals": {"kms:EncryptionContextKeys": [
        "contract", "submission_id", "archive_ciphertext_sha256",
        "data_key_id", "key_material_type"
      ]},
      Null: {
        "kms:EncryptionContext:archive_ciphertext_sha256": "false",
        "kms:EncryptionContext:contract": "false",
        "kms:EncryptionContext:data_key_id": "false",
        "kms:EncryptionContext:key_material_type": "false",
        "kms:EncryptionContext:submission_id": "false"
      },
      StringEquals: {
        "kms:EncryptionContext:contract": "lean-eval-archive-key-v2",
        "kms:EncryptionContext:key_material_type": "age-file-key-v1"
      }
    },
    Effect: "Allow",
    Resource: "arn:aws:kms:us-east-1:161072922960:key/219904f9-4952-400f-b60a-6f027c4d070b"
  }]
' "$ops/migration-policy.json" >/dev/null
test "$(aws iam list-role-policies --role-name "$MIGRATION_ROLE" \
  --query PolicyNames --output json | jq -cS .)" = '["EncryptOneArchiveFileKey"]'
test "$(aws iam list-attached-role-policies --role-name "$MIGRATION_ROLE" \
  --query AttachedPolicies --output json | jq -cS .)" = '[]'

aws iam get-role-policy --role-name "$FUNCTION_ROLE" \
  --policy-name ConsumeAndDecryptOneIdentity --query PolicyDocument \
  --output json > "$ops/function-policy-after.json"
aws iam get-role --role-name "$FUNCTION_ROLE" --query Role.AssumeRolePolicyDocument \
  --output json | jq -S . > "$ops/function-trust-after.json"
aws iam list-role-policies --role-name "$FUNCTION_ROLE" --output json \
  | jq -S . > "$ops/function-inline-after.json"
aws iam list-attached-role-policies --role-name "$FUNCTION_ROLE" --output json \
  | jq -S . > "$ops/function-attached-after.json"
cmp "$ops/function-trust-before.json" "$ops/function-trust-after.json"
cmp "$ops/function-inline-before.json" "$ops/function-inline-after.json"
cmp "$ops/function-attached-before.json" "$ops/function-attached-after.json"
jq -e --slurpfile before "$ops/function-policy-before.json" '
  [.Statement[] | select(.Action == "kms:Decrypt") |
    .Condition.StringEquals."kms:EncryptionContext:contract"] == [
      "lean-eval-archive-key-v1", "lean-eval-archive-key-v2"
    ] and
  (.Statement[] | select(
  .Action == "kms:Decrypt" and
    .Condition.StringEquals."kms:EncryptionContext:contract" ==
      "lean-eval-archive-key-v1"
  )) as $v1 |
  $v1 == ($before[0].Statement[] | select(
    .Action == "kms:Decrypt" and
    .Condition.StringEquals."kms:EncryptionContext:contract" ==
      "lean-eval-archive-key-v1"
  ))
' "$ops/function-policy-after.json" >/dev/null

aws lambda get-function-configuration --function-name "$FUNCTION_NAME" \
  --region "$AWS_REGION" --output json > "$ops/function-after.json"
aws lambda get-alias --function-name "$FUNCTION_NAME" --name live \
  --region "$AWS_REGION" --output json > "$ops/alias-after.json"
jq -e --arg role "arn:aws:iam::$AWS_ACCOUNT:role/$FUNCTION_ROLE" '
  .FunctionName == "lean-eval-archive-unwrap-production" and
  .Runtime == "python3.13" and .Handler == "aws_key_adapter.lambda_handler" and
  .Role == $role and .MemorySize == 128 and .Timeout == 10 and
  .TracingConfig.Mode == "PassThrough" and
  .Environment.Variables.LEAN_EVAL_ADAPTER_NAME == "aws-kms-v1" and
  .State == "Active" and .LastUpdateStatus == "Successful"
' "$ops/function-after.json" >/dev/null
jq -e --arg arn "$FUNCTION_ALIAS_ARN" '
  .AliasArn == $arn and .Name == "live" and
  (.FunctionVersion | test("^[1-9][0-9]*$")) and
  (.RoutingConfig.AdditionalVersionWeights | length) == 0
' "$ops/alias-after.json" >/dev/null
test "$(jq -r .FunctionVersion "$ops/alias-before.json")" != \
  "$(jq -r .FunctionVersion "$ops/alias-after.json")"

echo step=verify-migration-role-selector
gh api "repos/$REPOSITORY/environments/$ENVIRONMENT/variables" \
  > "$ops/environment-variables-after.json"
cmp "$ops/environment-variables-before.json" \
  "$ops/environment-variables-after.json"
jq -e --arg migration "$MIGRATION_ROLE_ARN" '
  .total_count == 1 and
  [.variables[] | {name, value}] == [{name: "AWS_WRAP_ROLE_ARN", value: $migration}]
' "$ops/environment-variables-after.json" >/dev/null
test "$(gh api "repos/$REPOSITORY/environments/$ENVIRONMENT/secrets" \
  --jq '[.secrets[].name | select(. == "LEGACY_ARCHIVE_IDENTITY")] | length')" = 0

complete=true
echo PRODUCTION_ARCHIVE_MIGRATION_INFRA_OK
