#!/usr/bin/env bash
set -euo pipefail

# AWS-only half of the production release OIDC trust repair. GitHub-side
# preconditions and the protected post-change preflight are intentionally run
# by the authenticated release operator outside CloudShell.

readonly AWS_REGION=us-east-1
readonly AWS_ACCOUNT=161072922960
readonly PRODUCTION_STACK=lean-eval-key-adapter-production
readonly STAGING_STACK=lean-eval-key-adapter-staging
readonly RELEASE_ROLE=lean-eval-release-unwrap-invoker-production
readonly RELEASE_ROLE_ARN=arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-production
readonly RELEASE_ALIAS_ARN=arn:aws:lambda:us-east-1:161072922960:function:lean-eval-archive-unwrap-production:live
readonly OLD_RELEASE_PREFIX=leanprover/lean-eval-releases
readonly NEW_RELEASE_PREFIX=leanprover@7233018/lean-eval-releases@1340741242
readonly OIDC_PROVIDER_ARN=arn:aws:iam::161072922960:oidc-provider/token.actions.githubusercontent.com
readonly SUBMISSION_PREFIX=leanprover/lean-eval-submissions
readonly PRE_TEMPLATE_SHA256=8f6acec8b91cffeb5ecae92a6ac83d906f77cb9fe1057b5a27e3f0f72b1deccd

tmp_root="$(realpath -e -- "${TMPDIR:-/tmp}")"
test -d "$tmp_root"
operator_dir=
change_set=
change_set_id=
change_set_owned=false
execution_attempted=false
aws_readback_complete=false

normalize_pinned_template() {
  local path=$1
  python3 - "$path" "$PRE_TEMPLATE_SHA256" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
template = path.read_bytes()
if hashlib.sha256(template).hexdigest() == expected:
    raise SystemExit(0)
if template.endswith(b"\n"):
    normalized = template[:-1]
    if hashlib.sha256(normalized).hexdigest() == expected:
        path.write_bytes(normalized)
        raise SystemExit(0)
raise SystemExit("live template bytes do not match the pinned template")
PY
}

remove_operator_material() {
  [[ -n "$operator_dir" ]] || return 0
  if [[ -L "$operator_dir" || ! -d "$operator_dir" ]]; then
    echo "REFUSING to remove an unexpected operator path" >&2
    return 1
  fi
  local parent base canonical_parent canonical_dir
  parent="$(dirname -- "$operator_dir")"
  base="$(basename -- "$operator_dir")"
  canonical_parent="$(realpath -e -- "$parent")" || return 1
  canonical_dir="$(realpath -e -- "$operator_dir")" || return 1
  if [[ "$canonical_parent" != "$tmp_root" ||
        "$operator_dir" != "$tmp_root/$base" ||
        "$canonical_dir" != "$tmp_root/$base" ||
        ! "$base" =~ ^lean-eval-production-trust\.[A-Za-z0-9]{8}$ ]]; then
    echo "REFUSING to remove an unexpected operator path" >&2
    return 1
  fi
  chmod -R u+rwX "$operator_dir" 2>/dev/null || true
  rm -rf -- "$operator_dir"
}

cleanup() {
  local status=$?
  set +e
  trap - EXIT HUP INT TERM
  if [[ "$change_set_owned" == true && "$execution_attempted" != true ]]; then
    if ! aws cloudformation delete-change-set \
      --stack-name "$PRODUCTION_STACK" \
      --change-set-name "$change_set_id" \
      --region "$AWS_REGION"; then
      echo "FAILED to delete the unexecuted production change set" >&2
      status=1
    fi
  fi
  if ! remove_operator_material; then
    echo "FAILED to remove production trust operator material" >&2
    status=1
  fi
  if [[ "$execution_attempted" == true && "$aws_readback_complete" != true ]]; then
    echo "INCOMPLETE: production trust execution was attempted, but AWS verification did not finish" >&2
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

operator_dir="$(mktemp -d "$tmp_root/lean-eval-production-trust.XXXXXXXX")"
chmod 700 "$operator_dir"
test ! -L "$operator_dir"
test "$(stat -c %a "$operator_dir")" = 700

echo step=identity-and-live-preflight
test "$(aws sts get-caller-identity --query Account --output text)" = "$AWS_ACCOUNT"

aws cloudformation get-template \
  --stack-name "$PRODUCTION_STACK" \
  --region "$AWS_REGION" \
  --template-stage Original \
  --query TemplateBody \
  --output text > "$operator_dir/pre-template.yaml"
normalize_pinned_template "$operator_dir/pre-template.yaml"
! grep -q 'AWS::LanguageExtensions' "$operator_dir/pre-template.yaml"
test "$(sha256sum "$operator_dir/pre-template.yaml" | awk '{print $1}')" = \
  "$PRE_TEMPLATE_SHA256"

aws cloudformation describe-stacks \
  --stack-name "$PRODUCTION_STACK" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].Parameters' \
  --output json > "$operator_dir/pre-parameters.json"

jq -e \
  --arg provider "$OIDC_PROVIDER_ARN" \
  --arg submissions "$SUBMISSION_PREFIX" \
  --arg releases "$OLD_RELEASE_PREFIX" '
  (map(.ParameterKey) | sort) == [
    "EnvironmentName",
    "GitHubOidcProviderArn",
    "ReleaseGitHubRepository",
    "SubmissionGitHubRepository"
  ] and
  (map({key: .ParameterKey, value: .ParameterValue}) | from_entries) == {
    EnvironmentName: "production",
    GitHubOidcProviderArn: $provider,
    ReleaseGitHubRepository: $releases,
    SubmissionGitHubRepository: $submissions
  }
' "$operator_dir/pre-parameters.json"

# The live production template predates ID-bearing subject prefixes. Preserve
# it byte-for-byte except for widening the release parameter's input pattern;
# the explicit parameter value below is the only resource-affecting change.
python3 - \
  "$operator_dir/pre-template.yaml" \
  "$operator_dir/update-template.yaml" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
old = """  ReleaseGitHubRepository:
    Type: String
    Default: leanprover/lean-eval-releases
    AllowedPattern: ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$
"""
new = """  ReleaseGitHubRepository:
    Type: String
    Default: leanprover/lean-eval-releases
    AllowedPattern: ^[A-Za-z0-9_.-]+(?:@[0-9]+)?/[A-Za-z0-9_.-]+(?:@[0-9]+)?$
"""
template = source.read_text()
if template.count(old) != 1:
    raise SystemExit("live template did not contain the exact release parameter")
updated = template.replace(old, new)
if updated.count(new) != 1:
    raise SystemExit("release parameter transformation was not exact")
target.write_text(updated)
PY

aws iam get-role \
  --role-name "$RELEASE_ROLE" \
  --query Role.AssumeRolePolicyDocument \
  --output json > "$operator_dir/pre-trust.json"
aws iam get-role-policy \
  --role-name "$RELEASE_ROLE" \
  --policy-name InvokeOnlyTheUnwrapAlias \
  --query PolicyDocument \
  --output json > "$operator_dir/pre-policy.json"
test "$(aws iam list-role-policies \
  --role-name "$RELEASE_ROLE" \
  --query PolicyNames \
  --output json | jq -cS .)" = '["InvokeOnlyTheUnwrapAlias"]'
test "$(aws iam list-attached-role-policies \
  --role-name "$RELEASE_ROLE" \
  --query AttachedPolicies \
  --output json | jq -cS .)" = '[]'

jq -e --arg provider "$OIDC_PROVIDER_ARN" '
  (keys == ["Statement", "Version"]) and
  .Version == "2012-10-17" and
  (.Statement | length) == 1 and
  (.Statement[0] | keys == ["Action", "Condition", "Effect", "Principal"]) and
  .Statement[0].Effect == "Allow" and
  .Statement[0].Principal == {Federated: $provider} and
  .Statement[0].Action == "sts:AssumeRoleWithWebIdentity" and
  .Statement[0].Condition == {StringEquals: {
    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
    "token.actions.githubusercontent.com:sub":
      "repo:leanprover/lean-eval-releases:environment:release-production"
  }}
' "$operator_dir/pre-trust.json"

jq -e --arg alias "$RELEASE_ALIAS_ARN" '
  (keys == ["Statement", "Version"]) and
  .Version == "2012-10-17" and
  .Statement == [{
    Action: "lambda:InvokeFunction",
    Effect: "Allow",
    Resource: $alias
  }]
' "$operator_dir/pre-policy.json"

staging_updated_before="$(aws cloudformation describe-stacks \
  --stack-name "$STAGING_STACK" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].LastUpdatedTime' \
  --output text)"

echo step=create-and-inspect-change-set
change_set="release-oidc-production-$(date -u +%Y%m%dT%H%M%SZ)-$$"
aws cloudformation create-change-set \
  --stack-name "$PRODUCTION_STACK" \
  --change-set-name "$change_set" \
  --change-set-type UPDATE \
  --description "Trust exact ID-bearing GitHub release-production OIDC subject" \
  --template-body "file://$operator_dir/update-template.yaml" \
  --parameters \
    ParameterKey=EnvironmentName,UsePreviousValue=true \
    ParameterKey=GitHubOidcProviderArn,UsePreviousValue=true \
    ParameterKey=SubmissionGitHubRepository,UsePreviousValue=true \
    ParameterKey=ReleaseGitHubRepository,ParameterValue="$NEW_RELEASE_PREFIX" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" \
  --output json > "$operator_dir/create-change-set.json"

change_set_id="$(jq -er \
  --arg stack "$PRODUCTION_STACK" \
  --arg name "$change_set" '
  select(keys == ["Id", "StackId"]) |
  .StackId as $stack_id |
  .Id as $change_set_id |
  select(
    ($stack_id | type) == "string" and
    ($stack_id | test(
      "^arn:aws:cloudformation:us-east-1:161072922960:stack/" +
      $stack + "/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-" +
      "[0-9a-f]{4}-[0-9a-f]{12}$"
    )) and
    ($change_set_id | type) == "string" and
    ($change_set_id | test(
      "^arn:aws:cloudformation:us-east-1:161072922960:changeSet/" +
      $name + "/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-" +
      "[0-9a-f]{4}-[0-9a-f]{12}$"
    ))
  ) |
  $change_set_id
' "$operator_dir/create-change-set.json")"
change_set_owned=true

aws cloudformation wait change-set-create-complete \
  --stack-name "$PRODUCTION_STACK" \
  --change-set-name "$change_set_id" \
  --region "$AWS_REGION"
aws cloudformation describe-change-set \
  --stack-name "$PRODUCTION_STACK" \
  --change-set-name "$change_set_id" \
  --region "$AWS_REGION" \
  --output json > "$operator_dir/change-set.json"
jq -e '
  .Status == "CREATE_COMPLETE" and
  (.Changes | length) == 1 and
  .Changes[0].ResourceChange.Action == "Modify" and
  .Changes[0].ResourceChange.LogicalResourceId == "ReleaseInvokerRole" and
  .Changes[0].ResourceChange.ResourceType == "AWS::IAM::Role" and
  .Changes[0].ResourceChange.Replacement == "False"
' "$operator_dir/change-set.json"

echo step=execute-production-change-set
execution_attempted=true
aws cloudformation execute-change-set \
  --stack-name "$PRODUCTION_STACK" \
  --change-set-name "$change_set_id" \
  --region "$AWS_REGION"
aws cloudformation wait stack-update-complete \
  --stack-name "$PRODUCTION_STACK" \
  --region "$AWS_REGION"

echo step=post-update-aws-verification
aws iam get-role \
  --role-name "$RELEASE_ROLE" \
  --query Role.AssumeRolePolicyDocument \
  --output json > "$operator_dir/post-trust.json"
aws iam get-role-policy \
  --role-name "$RELEASE_ROLE" \
  --policy-name InvokeOnlyTheUnwrapAlias \
  --query PolicyDocument \
  --output json > "$operator_dir/post-policy.json"
aws cloudformation get-template \
  --stack-name "$PRODUCTION_STACK" \
  --region "$AWS_REGION" \
  --template-stage Original \
  --query TemplateBody \
  --output text > "$operator_dir/post-template.yaml"
python3 - "$operator_dir/post-template.yaml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
template = path.read_bytes()
if template.endswith(b"\n\n"):
    path.write_bytes(template[:-1])
PY
aws cloudformation describe-stacks \
  --stack-name "$PRODUCTION_STACK" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].Parameters' \
  --output json > "$operator_dir/post-parameters.json"

jq -e --arg provider "$OIDC_PROVIDER_ARN" '
  (keys == ["Statement", "Version"]) and
  .Version == "2012-10-17" and
  (.Statement | length) == 1 and
  (.Statement[0] | keys == ["Action", "Condition", "Effect", "Principal"]) and
  .Statement[0].Effect == "Allow" and
  .Statement[0].Principal == {Federated: $provider} and
  .Statement[0].Action == "sts:AssumeRoleWithWebIdentity" and
  .Statement[0].Condition == {StringEquals: {
    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
    "token.actions.githubusercontent.com:sub":
      "repo:leanprover@7233018/lean-eval-releases@1340741242:environment:release-production"
  }}
' "$operator_dir/post-trust.json"
cmp <(jq -S . "$operator_dir/pre-policy.json") \
  <(jq -S . "$operator_dir/post-policy.json")
cmp "$operator_dir/update-template.yaml" "$operator_dir/post-template.yaml"

jq -e \
  --arg provider "$OIDC_PROVIDER_ARN" \
  --arg submissions "$SUBMISSION_PREFIX" \
  --arg releases "$NEW_RELEASE_PREFIX" '
  (map(.ParameterKey) | sort) == [
    "EnvironmentName",
    "GitHubOidcProviderArn",
    "ReleaseGitHubRepository",
    "SubmissionGitHubRepository"
  ] and
  (map({key: .ParameterKey, value: .ParameterValue}) | from_entries) == {
    EnvironmentName: "production",
    GitHubOidcProviderArn: $provider,
    ReleaseGitHubRepository: $releases,
    SubmissionGitHubRepository: $submissions
  }
' "$operator_dir/post-parameters.json"

test "$(aws iam list-role-policies \
  --role-name "$RELEASE_ROLE" \
  --query PolicyNames \
  --output json | jq -cS .)" = '["InvokeOnlyTheUnwrapAlias"]'
test "$(aws iam list-attached-role-policies \
  --role-name "$RELEASE_ROLE" \
  --query AttachedPolicies \
  --output json | jq -cS .)" = '[]'
test "$(aws cloudformation describe-stacks \
  --stack-name "$PRODUCTION_STACK" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ReleaseInvokerRoleArn'].OutputValue | [0]" \
  --output text)" = "$RELEASE_ROLE_ARN"
test "$(aws cloudformation describe-stacks \
  --stack-name "$STAGING_STACK" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].LastUpdatedTime' \
  --output text)" = "$staging_updated_before"

aws_readback_complete=true
echo PRODUCTION_RELEASE_TRUST_AWS_OK
