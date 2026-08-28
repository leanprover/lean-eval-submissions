# Production release OIDC trust repair

The production release invoker role still trusts the release repository's
obsolete name-only GitHub OIDC subject. This procedure changes only that
role's trust to the repository's current ID-bearing subject, then runs the
existing trust-only production preflight. It does not invoke Lambda, unwrap or
read an archive, write State or Git, or enable publication.

This is an administrator operation. Creating or executing a CloudFormation
change set and dispatching the protected preflight require explicit approval.
Keep `PUBLICATION_ENABLED` absent throughout.

Run every shell block below, in order, in one dedicated Bash session. The
first block installs an exit trap that removes operator material and deletes
the named change set unless CloudFormation has accepted its execution. Do not
close that shell between blocks. An exit after execution but before all
readbacks and the protected preflight is deliberately reported as incomplete.

## Exact boundary

The live trust currently names:

```text
repo:leanprover/lean-eval-releases:environment:release-production
```

The required trust names:

```text
repo:leanprover@7233018/lean-eval-releases@1340741242:environment:release-production
```

The accepted change set has exactly one resource change:

- action `Modify`;
- logical resource `ReleaseInvokerRole`;
- type `AWS::IAM::Role`;
- replacement `False`.

The production stack predates the deferred historical-migration role now in
the repository template. Do not update production from the current template:
that would also provision unrelated migration authority. Reuse the live stack
template and change only `ReleaseGitHubSubjectPrefix`. The staging stack must
remain unchanged.

## Read-only preconditions

Use an authenticated short-lived administrator session in AWS account
`161072922960`. Put all captured data in a new mode-700 temporary directory;
the trust and policy documents contain no credential, but they are operator
material rather than repository artifacts.

```bash
set -euo pipefail

LEAN_EVAL_AWS_REGION=us-east-1
LEAN_EVAL_AWS_ACCOUNT=161072922960
LEAN_EVAL_PRODUCTION_STACK=lean-eval-key-adapter-production
LEAN_EVAL_STAGING_STACK=lean-eval-key-adapter-staging
LEAN_EVAL_RELEASE_ROLE=lean-eval-release-unwrap-invoker-production
LEAN_EVAL_RELEASE_ROLE_ARN=arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-production
LEAN_EVAL_RELEASE_ALIAS_ARN=arn:aws:lambda:us-east-1:161072922960:function:lean-eval-archive-unwrap-production:live
LEAN_EVAL_OLD_PREFIX=leanprover/lean-eval-releases
LEAN_EVAL_NEW_PREFIX=leanprover@7233018/lean-eval-releases@1340741242
LEAN_EVAL_OIDC_PROVIDER_ARN=arn:aws:iam::161072922960:oidc-provider/token.actions.githubusercontent.com
LEAN_EVAL_SUBMISSION_PREFIX=leanprover/lean-eval-submissions
LEAN_EVAL_RELEASES_COMMIT="$(gh api \
  repos/leanprover/lean-eval-releases/branches/main \
  --jq .commit.sha)"
[[ "$LEAN_EVAL_RELEASES_COMMIT" =~ ^[0-9a-f]{40}$ ]]
LEAN_EVAL_OPERATOR_TMP_ROOT="$(realpath -e -- "${TMPDIR:-/tmp}")"
test -d "$LEAN_EVAL_OPERATOR_TMP_ROOT"
LEAN_EVAL_AWS_OPS=
LEAN_EVAL_CHANGE_SET=
LEAN_EVAL_CHANGE_SET_ID=
LEAN_EVAL_CHANGE_SET_OWNED=false
LEAN_EVAL_CHANGE_SET_EXECUTION_ATTEMPTED=false
LEAN_EVAL_TRUST_PREFLIGHT_COMPLETE=false

lean_eval_remove_operator_material() {
  [[ -n "$LEAN_EVAL_AWS_OPS" ]] || return 0
  if [[ -L "$LEAN_EVAL_AWS_OPS" || ! -d "$LEAN_EVAL_AWS_OPS" ]]; then
    echo "REFUSING to remove a non-directory or symlink operator path" >&2
    return 1
  fi

  operator_parent="$(dirname -- "$LEAN_EVAL_AWS_OPS")"
  operator_basename="$(basename -- "$LEAN_EVAL_AWS_OPS")"
  canonical_parent="$(realpath -e -- "$operator_parent")" || return 1
  if [[ "$canonical_parent" != "$LEAN_EVAL_OPERATOR_TMP_ROOT" ||
        "$LEAN_EVAL_AWS_OPS" != "$LEAN_EVAL_OPERATOR_TMP_ROOT/$operator_basename" ||
        ! "$operator_basename" =~ ^lean-eval-production-trust\.[A-Za-z0-9]{8}$ ]]; then
    echo "REFUSING to remove an unexpected operator path" >&2
    return 1
  fi
  canonical_operator_dir="$(realpath -e -- "$LEAN_EVAL_AWS_OPS")" || return 1
  if [[ "$canonical_operator_dir" != "$LEAN_EVAL_OPERATOR_TMP_ROOT/$operator_basename" ]]; then
    echo "REFUSING to remove a redirected operator path" >&2
    return 1
  fi

  chmod -R u+rwX "$LEAN_EVAL_AWS_OPS" 2>/dev/null || true
  rm -rf -- "$LEAN_EVAL_AWS_OPS"
}

lean_eval_cleanup() {
  status=$?
  set +e
  trap - EXIT HUP INT TERM

  if [[ "$LEAN_EVAL_CHANGE_SET_OWNED" == true &&
        "$LEAN_EVAL_CHANGE_SET_EXECUTION_ATTEMPTED" != true ]]; then
    if ! aws cloudformation delete-change-set \
      --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
      --change-set-name "$LEAN_EVAL_CHANGE_SET_ID" \
      --region "$LEAN_EVAL_AWS_REGION"; then
      echo "FAILED to delete the unexecuted production change set" >&2
      status=1
    fi
  fi

  if ! lean_eval_remove_operator_material; then
    echo "FAILED to remove production trust operator material" >&2
    status=1
  fi

  if [[ "$LEAN_EVAL_CHANGE_SET_EXECUTION_ATTEMPTED" == true &&
        "$LEAN_EVAL_TRUST_PREFLIGHT_COMPLETE" != true ]]; then
    echo "INCOMPLETE: production trust execution was attempted, but verification did not finish" >&2
    status=1
  fi
  exit "$status"
}
trap lean_eval_cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

test "$(aws sts get-caller-identity --query Account --output text)" = \
  "$LEAN_EVAL_AWS_ACCOUNT"
test "$(gh api \
  repos/leanprover/lean-eval-releases/actions/oidc/customization/sub \
  --jq .sub_claim_prefix)" = "repo:$LEAN_EVAL_NEW_PREFIX"
test "$(gh api \
  repos/leanprover/lean-eval-submissions/actions/oidc/customization/sub \
  --jq .sub_claim_prefix)" = "repo:$LEAN_EVAL_SUBMISSION_PREFIX"
test "$(gh api repos/leanprover/lean-eval-releases/actions/variables \
  --jq '[.variables[] | select(.name=="PUBLICATION_ENABLED")] | length')" = 0
test "$(gh api repos/leanprover/lean-eval-releases/branches/main \
  --jq .commit.sha)" = "$LEAN_EVAL_RELEASES_COMMIT"
test "$(gh api repos/leanprover/lean-eval-releases/branches/main \
  --jq .protected)" = true
printf 'reviewed_release_commit=%s\n' "$LEAN_EVAL_RELEASES_COMMIT"

LEAN_EVAL_AWS_OPS="$(mktemp -d \
  "$LEAN_EVAL_OPERATOR_TMP_ROOT/lean-eval-production-trust.XXXXXXXX")"
chmod 700 "$LEAN_EVAL_AWS_OPS"
test ! -L "$LEAN_EVAL_AWS_OPS"
test -d "$LEAN_EVAL_AWS_OPS"
test "$(stat -c %a "$LEAN_EVAL_AWS_OPS")" = 700

aws cloudformation get-template \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --template-stage Original \
  --query TemplateBody \
  --output text > "$LEAN_EVAL_AWS_OPS/pre-template.yaml"
! grep -q 'AWS::LanguageExtensions' "$LEAN_EVAL_AWS_OPS/pre-template.yaml"

aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --query 'Stacks[0].Parameters' \
  --output json > "$LEAN_EVAL_AWS_OPS/pre-parameters.json"

jq -e \
  --arg provider "$LEAN_EVAL_OIDC_PROVIDER_ARN" \
  --arg submissions "$LEAN_EVAL_SUBMISSION_PREFIX" \
  --arg releases "$LEAN_EVAL_OLD_PREFIX" '
  (map(.ParameterKey) | sort) == [
    "EnvironmentName",
    "GitHubOidcProviderArn",
    "ReleaseGitHubSubjectPrefix",
    "SubmissionGitHubSubjectPrefix"
  ] and
  (map({key: .ParameterKey, value: .ParameterValue}) | from_entries) == {
    EnvironmentName: "production",
    GitHubOidcProviderArn: $provider,
    ReleaseGitHubSubjectPrefix: $releases,
    SubmissionGitHubSubjectPrefix: $submissions
  }
' "$LEAN_EVAL_AWS_OPS/pre-parameters.json"

aws iam get-role \
  --role-name "$LEAN_EVAL_RELEASE_ROLE" \
  --query Role.AssumeRolePolicyDocument \
  --output json > "$LEAN_EVAL_AWS_OPS/pre-trust.json"
aws iam get-role-policy \
  --role-name "$LEAN_EVAL_RELEASE_ROLE" \
  --policy-name InvokeOnlyTheUnwrapAlias \
  --query PolicyDocument \
  --output json > "$LEAN_EVAL_AWS_OPS/pre-policy.json"
test "$(aws iam list-role-policies \
  --role-name "$LEAN_EVAL_RELEASE_ROLE" \
  --query PolicyNames \
  --output json | jq -cS .)" = '["InvokeOnlyTheUnwrapAlias"]'
test "$(aws iam list-attached-role-policies \
  --role-name "$LEAN_EVAL_RELEASE_ROLE" \
  --query AttachedPolicies \
  --output json | jq -cS .)" = '[]'

jq -e --arg provider "$LEAN_EVAL_OIDC_PROVIDER_ARN" '
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
' "$LEAN_EVAL_AWS_OPS/pre-trust.json"

jq -e --arg alias "$LEAN_EVAL_RELEASE_ALIAS_ARN" '
  (keys == ["Statement", "Version"]) and
  .Version == "2012-10-17" and
  .Statement == [{
    Action: "lambda:InvokeFunction",
    Effect: "Allow",
    Resource: $alias
  }]
' "$LEAN_EVAL_AWS_OPS/pre-policy.json"

LEAN_EVAL_STAGING_UPDATED_BEFORE="$(aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --query 'Stacks[0].LastUpdatedTime' \
  --output text)"
```

Stop if any precondition fails. A different parameter set, trust document, or
workload policy needs a new reviewed repair; do not broaden these checks.

## Create and inspect the production-only change set

The following creates external AWS state and therefore belongs inside the
explicitly approved operation.

```bash
LEAN_EVAL_CHANGE_SET="release-oidc-production-$(date -u +%Y%m%dT%H%M%SZ)-$$"

aws cloudformation create-change-set \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --change-set-name "$LEAN_EVAL_CHANGE_SET" \
  --change-set-type UPDATE \
  --description "Trust exact ID-bearing GitHub release-production OIDC subject" \
  --use-previous-template \
  --parameters \
    ParameterKey=EnvironmentName,UsePreviousValue=true \
    ParameterKey=GitHubOidcProviderArn,UsePreviousValue=true \
    ParameterKey=SubmissionGitHubSubjectPrefix,UsePreviousValue=true \
    ParameterKey=ReleaseGitHubSubjectPrefix,ParameterValue="$LEAN_EVAL_NEW_PREFIX" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$LEAN_EVAL_AWS_REGION" \
  --output json > "$LEAN_EVAL_AWS_OPS/create-change-set.json"

LEAN_EVAL_CHANGE_SET_ID="$(jq -er \
  --arg stack "$LEAN_EVAL_PRODUCTION_STACK" \
  --arg name "$LEAN_EVAL_CHANGE_SET" '
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
' "$LEAN_EVAL_AWS_OPS/create-change-set.json")"
LEAN_EVAL_CHANGE_SET_OWNED=true

aws cloudformation wait change-set-create-complete \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --change-set-name "$LEAN_EVAL_CHANGE_SET_ID" \
  --region "$LEAN_EVAL_AWS_REGION"

aws cloudformation describe-change-set \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --change-set-name "$LEAN_EVAL_CHANGE_SET_ID" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --output json > "$LEAN_EVAL_AWS_OPS/change-set.json"

jq -e '
  .Status == "CREATE_COMPLETE" and
  (.Changes | length) == 1 and
  .Changes[0].ResourceChange.Action == "Modify" and
  .Changes[0].ResourceChange.LogicalResourceId == "ReleaseInvokerRole" and
  .Changes[0].ResourceChange.ResourceType == "AWS::IAM::Role" and
  .Changes[0].ResourceChange.Replacement == "False"
' "$LEAN_EVAL_AWS_OPS/change-set.json"
```

Only the unique change-set ARN returned by a successful, exact creation
response becomes cleanup-owned. A name collision, create failure, malformed
response, or same-name replacement never authorizes deletion. If waiting,
description, or the closed check then fails, the exit trap deletes that exact
owned ARN. Confirm that cleanup succeeded before leaving the operation. Do not
switch to the current repository template or admit a second resource.

## Execute and verify

Execution requires the explicit approval for this exact production mutation.
The attempt marker is set before the execute request, so a lost response can
never authorize deletion of a change set that CloudFormation may be executing.

```bash
LEAN_EVAL_CHANGE_SET_EXECUTION_ATTEMPTED=true
aws cloudformation execute-change-set \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --change-set-name "$LEAN_EVAL_CHANGE_SET_ID" \
  --region "$LEAN_EVAL_AWS_REGION"

aws cloudformation wait stack-update-complete \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --region "$LEAN_EVAL_AWS_REGION"

aws iam get-role \
  --role-name "$LEAN_EVAL_RELEASE_ROLE" \
  --query Role.AssumeRolePolicyDocument \
  --output json > "$LEAN_EVAL_AWS_OPS/post-trust.json"
aws iam get-role-policy \
  --role-name "$LEAN_EVAL_RELEASE_ROLE" \
  --policy-name InvokeOnlyTheUnwrapAlias \
  --query PolicyDocument \
  --output json > "$LEAN_EVAL_AWS_OPS/post-policy.json"
aws cloudformation get-template \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --template-stage Original \
  --query TemplateBody \
  --output text > "$LEAN_EVAL_AWS_OPS/post-template.yaml"
aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --query 'Stacks[0].Parameters' \
  --output json > "$LEAN_EVAL_AWS_OPS/post-parameters.json"

jq -e --arg provider "$LEAN_EVAL_OIDC_PROVIDER_ARN" '
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
' "$LEAN_EVAL_AWS_OPS/post-trust.json"
cmp <(jq -S . "$LEAN_EVAL_AWS_OPS/pre-policy.json") \
  <(jq -S . "$LEAN_EVAL_AWS_OPS/post-policy.json")
cmp "$LEAN_EVAL_AWS_OPS/pre-template.yaml" \
  "$LEAN_EVAL_AWS_OPS/post-template.yaml"
jq -e \
  --arg provider "$LEAN_EVAL_OIDC_PROVIDER_ARN" \
  --arg submissions "$LEAN_EVAL_SUBMISSION_PREFIX" \
  --arg releases "$LEAN_EVAL_NEW_PREFIX" '
  (map(.ParameterKey) | sort) == [
    "EnvironmentName",
    "GitHubOidcProviderArn",
    "ReleaseGitHubSubjectPrefix",
    "SubmissionGitHubSubjectPrefix"
  ] and
  (map({key: .ParameterKey, value: .ParameterValue}) | from_entries) == {
    EnvironmentName: "production",
    GitHubOidcProviderArn: $provider,
    ReleaseGitHubSubjectPrefix: $releases,
    SubmissionGitHubSubjectPrefix: $submissions
  }
' "$LEAN_EVAL_AWS_OPS/post-parameters.json"
test "$(aws iam list-role-policies \
  --role-name "$LEAN_EVAL_RELEASE_ROLE" \
  --query PolicyNames \
  --output json | jq -cS .)" = '["InvokeOnlyTheUnwrapAlias"]'
test "$(aws iam list-attached-role-policies \
  --role-name "$LEAN_EVAL_RELEASE_ROLE" \
  --query AttachedPolicies \
  --output json | jq -cS .)" = '[]'

test "$(aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ReleaseInvokerRoleArn'].OutputValue | [0]" \
  --output text)" = "$LEAN_EVAL_RELEASE_ROLE_ARN"
test "$(aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --query 'Stacks[0].LastUpdatedTime' \
  --output text)" = "$LEAN_EVAL_STAGING_UPDATED_BEFORE"
test "$(gh api \
  repos/leanprover/lean-eval-releases/environments/release-production/variables \
  --jq '.variables[] | select(.name=="AWS_RELEASE_UNWRAP_ROLE_ARN") | .value')" = \
  "$LEAN_EVAL_RELEASE_ROLE_ARN"
test "$(gh api repos/leanprover/lean-eval-releases/actions/variables \
  --jq '[.variables[] | select(.name=="PUBLICATION_ENABLED")] | length')" = 0
```

Only after every trust, policy, template, parameter, output, staging, and
publication-latch readback above succeeds, dispatch the source-free preflight
from the already reviewed exact protected release commit. The workflow itself
requires that same commit in `expected_release_commit` and rejects a moving or
unprotected ref.

```bash
LEAN_EVAL_PREFLIGHT_URL="$(gh workflow run \
  verify-production-release-oidc.yml \
  --repo leanprover/lean-eval-releases \
  --ref main \
  -f expected_release_commit="$LEAN_EVAL_RELEASES_COMMIT" \
  -f confirm_publication_disabled=true)"
case "$LEAN_EVAL_PREFLIGHT_URL" in
  https://github.com/leanprover/lean-eval-releases/actions/runs/*) ;;
  *) echo "preflight dispatch returned no exact run URL" >&2; exit 1 ;;
esac
LEAN_EVAL_PREFLIGHT_RUN_ID="${LEAN_EVAL_PREFLIGHT_URL##*/}"
[[ "$LEAN_EVAL_PREFLIGHT_RUN_ID" =~ ^[0-9]+$ ]]

gh run watch "$LEAN_EVAL_PREFLIGHT_RUN_ID" \
  --repo leanprover/lean-eval-releases \
  --exit-status
gh run view "$LEAN_EVAL_PREFLIGHT_RUN_ID" \
  --repo leanprover/lean-eval-releases \
  --json attempt,conclusion,databaseId,event,headBranch,headSha,status \
  > "$LEAN_EVAL_AWS_OPS/preflight-run.json"
jq -e \
  --arg head "$LEAN_EVAL_RELEASES_COMMIT" \
  --argjson run_id "$LEAN_EVAL_PREFLIGHT_RUN_ID" '
  .databaseId == $run_id and
  .attempt == 1 and
  .status == "completed" and
  .conclusion == "success" and
  .event == "workflow_dispatch" and
  .headBranch == "main" and
  .headSha == $head
' "$LEAN_EVAL_AWS_OPS/preflight-run.json"

gh api \
  "repos/leanprover/lean-eval-releases/actions/runs/$LEAN_EVAL_PREFLIGHT_RUN_ID/attempts/1/jobs?per_page=100" \
  > "$LEAN_EVAL_AWS_OPS/preflight-jobs.json"
jq -e \
  --arg head "$LEAN_EVAL_RELEASES_COMMIT" \
  --argjson run_id "$LEAN_EVAL_PREFLIGHT_RUN_ID" '
  .total_count == 3 and
  (.jobs | type) == "array" and
  (.jobs | length) == 3 and
  ([.jobs[].name] | sort) == ["authorize", "oidc-trust", "summarize"] and
  ([.jobs[].id] | all(type == "number" and . > 0 and floor == .)) and
  ([.jobs[].id] | unique | length) == 3 and
  ([.jobs[]] | all(
    .run_id == $run_id and
    .run_attempt == 1 and
    .head_sha == $head and
    .status == "completed" and
    .conclusion == "success"
  ))
' "$LEAN_EVAL_AWS_OPS/preflight-jobs.json"
test "$(gh api \
  "repos/leanprover/lean-eval-releases/actions/runs/$LEAN_EVAL_PREFLIGHT_RUN_ID/artifacts" \
  --jq .total_count)" = 0
test "$(gh api repos/leanprover/lean-eval-releases/actions/variables \
  --jq '[.variables[] | select(.name=="PUBLICATION_ENABLED")] | length')" = 0

LEAN_EVAL_TRUST_PREFLIGHT_COMPLETE=true
echo PRODUCTION_RELEASE_TRUST_REPAIR_OK
exit 0
```

The preflight assumes the role under an inline policy permitting only
`sts:GetCallerIdentity`, then removes AWS and GitHub OIDC handles. It has no
Lambda invocation, archive access, checkout, State, Git write, publication,
or artifact path. Do not dispatch the publication controller. Completion is
claimed only by the final marker after exact run and latch readback; the exit
trap then removes the mode-700 operator directory.

## Rollback

CloudFormation automatically rolls back a failed update. A successful update
must not be reversed ad hoc. A rollback is a separate approval-gated operator
change whose reviewed procedure must start from fresh readbacks, install the
same unexecuted-change-set and mode-700 cleanup trap, use
`--use-previous-template`, and require the same closed one-resource check. It
may set only:

```text
ParameterKey=ReleaseGitHubSubjectPrefix,ParameterValue=leanprover/lean-eval-releases
```

The rollback procedure must expect the exact ID-bearing trust before it creates
anything. After execution it must re-read and require the exact obsolete trust,
the unchanged workload policy and live template, the exact reverted parameter
set, the unchanged staging timestamp, and the absent publication latch before
claiming rollback completion. Any ambiguity is an incomplete rollback, not a
success. Do not use a direct IAM trust edit: that would create CloudFormation
drift.
