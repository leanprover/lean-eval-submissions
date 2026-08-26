# Production release OIDC trust repair

The production release invoker role still trusts the release repository's
obsolete name-only GitHub OIDC subject. This procedure changes only that
role's trust to the repository's current ID-bearing subject, then runs the
existing trust-only production preflight. It does not invoke Lambda, unwrap or
read an archive, write State or Git, or enable publication.

This is an administrator operation. Creating or executing a CloudFormation
change set and dispatching the protected preflight require explicit approval.
Keep `PUBLICATION_ENABLED` absent throughout.

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

```sh
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

LEAN_EVAL_AWS_OPS="$(mktemp -d)"
chmod 700 "$LEAN_EVAL_AWS_OPS"

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

```sh
LEAN_EVAL_CHANGE_SET="release-oidc-production-$(date -u +%Y%m%dT%H%M%SZ)"

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
  --region "$LEAN_EVAL_AWS_REGION"

aws cloudformation wait change-set-create-complete \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --change-set-name "$LEAN_EVAL_CHANGE_SET" \
  --region "$LEAN_EVAL_AWS_REGION"

aws cloudformation describe-change-set \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --change-set-name "$LEAN_EVAL_CHANGE_SET" \
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

If the closed change-set check fails, delete the change set and stop. Do not
switch to the current repository template or admit a second resource.

## Execute and verify

Execution requires the explicit approval for this exact production mutation.

```sh
aws cloudformation execute-change-set \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --change-set-name "$LEAN_EVAL_CHANGE_SET" \
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

After the trust readback succeeds, dispatch
`verify-production-release-oidc.yml` from exact protected release `main` with
`confirm_publication_disabled=true`. That existing workflow assumes the role
under an inline policy permitting only `sts:GetCallerIdentity`, then removes
AWS and GitHub OIDC handles. It has no Lambda invocation, archive access,
checkout, State, Git write, publication, or artifact path. Verify its exact
head SHA, successful jobs, zero artifacts, and the still-absent publication
variable. Do not dispatch the publication controller.

## Rollback

CloudFormation automatically rolls back a failed update. A successful update
can be reversed by creating another change set with `--use-previous-template`
and the same closed one-resource check, setting only:

```text
ParameterKey=ReleaseGitHubSubjectPrefix,ParameterValue=leanprover/lean-eval-releases
```

Execute that rollback only after a separate operator decision, then require
the trust document to equal `pre-trust.json` and the workload policy to remain
byte-equivalent after canonical JSON sorting. Do not use a direct IAM trust
edit: that would create CloudFormation drift. Delete the temporary directory
after the approved operation's required review is complete.
