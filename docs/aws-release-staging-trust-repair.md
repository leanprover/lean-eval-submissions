# Staging release OIDC trust repair

This procedure repairs one known live configuration drift: GitHub now issues
the release repository's ID-bearing immutable OIDC subject, while the staging
release invoker role still trusts its obsolete name-only subject. It changes
only the staging CloudFormation stack and then runs a staging smoke that
discards plaintext and cannot publish.

This is an administrator operation. Preparing this document, validating the
source, or inspecting the proposed change set does not authorize its execution.
Use an authenticated short-lived administrator session in AWS account
`161072922960`, and stop unless the proposed change has the exact closed shape
below.

## Proven mismatch and safety boundary

The current GitHub token subject is:

```text
repo:leanprover@7233018/lean-eval-releases@1340741242:environment:release-staging
```

The live role still expects:

```text
repo:leanprover/lean-eval-releases:environment:release-staging
```

The live role therefore cannot currently be assumed by the release workflow.
The current template and release workflow already use the correct ID-bearing
contract, so the repair is limited to bringing that one live trust policy back
under the tracked stack definition.

The accepted change set has exactly one resource change:

- action `Modify`;
- logical resource `ReleaseInvokerRole`;
- type `AWS::IAM::Role`;
- replacement `False`.

It must not contain a KMS key, DynamoDB table, Lambda function, archive role,
replay role, production role, or any second resource. The production stack's
`LastUpdatedTime` must remain unchanged. Repository variable
`PUBLICATION_ENABLED` must remain absent.

## Pin and validate the source

Start in a clean checkout that contains the recorded submissions commit. Keep
all generated operator evidence in a new mode-700 temporary directory and
remove it after the evidence has been recorded.

```sh
set -euo pipefail

LEAN_EVAL_AWS_REGION=us-east-1
LEAN_EVAL_AWS_ACCOUNT=161072922960
LEAN_EVAL_STAGING_STACK=lean-eval-key-adapter-staging
LEAN_EVAL_PRODUCTION_STACK=lean-eval-key-adapter-production
LEAN_EVAL_SUBMISSIONS_COMMIT="$(gh api \
  repos/leanprover/lean-eval-submissions/commits/main --jq .sha)"
LEAN_EVAL_RELEASE_COMMIT=90dadc872d624b8e6d171caf439313d185fc3e7f
LEAN_EVAL_OIDC_PROVIDER_ARN=arn:aws:iam::161072922960:oidc-provider/token.actions.githubusercontent.com
LEAN_EVAL_SUBMISSION_PREFIX=leanprover/lean-eval-submissions
LEAN_EVAL_RELEASE_PREFIX=leanprover@7233018/lean-eval-releases@1340741242
LEAN_EVAL_STAGING_ROLE=arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-staging
LEAN_EVAL_SOURCE_REPOSITORY="$(git rev-parse --show-toplevel)"

test "$(aws sts get-caller-identity --query Account --output text)" = \
  "$LEAN_EVAL_AWS_ACCOUNT"
test "$(gh api \
  repos/leanprover/lean-eval-submissions/actions/oidc/customization/sub \
  --jq .sub_claim_prefix)" = "repo:$LEAN_EVAL_SUBMISSION_PREFIX"
test "$(gh api \
  repos/leanprover/lean-eval-releases/actions/oidc/customization/sub \
  --jq .sub_claim_prefix)" = "repo:$LEAN_EVAL_RELEASE_PREFIX"
test "$(gh api repos/leanprover/lean-eval-submissions/commits/main --jq .sha)" = \
  "$LEAN_EVAL_SUBMISSIONS_COMMIT"
test "$(gh api repos/leanprover/lean-eval-releases/commits/main --jq .sha)" = \
  "$LEAN_EVAL_RELEASE_COMMIT"
test "$(git -C "$LEAN_EVAL_SOURCE_REPOSITORY" rev-parse HEAD)" = \
  "$LEAN_EVAL_SUBMISSIONS_COMMIT"
test -z "$(git -C "$LEAN_EVAL_SOURCE_REPOSITORY" status --porcelain)"

LEAN_EVAL_AWS_OPS="$(mktemp -d)"
chmod 700 "$LEAN_EVAL_AWS_OPS"
mkdir "$LEAN_EVAL_AWS_OPS/source"
git -C "$LEAN_EVAL_SOURCE_REPOSITORY" archive \
  "$LEAN_EVAL_SUBMISSIONS_COMMIT" |
  tar -x -C "$LEAN_EVAL_AWS_OPS/source"

aws cloudformation get-template \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --template-stage Original \
  --query TemplateBody \
  --output text > "$LEAN_EVAL_AWS_OPS/pre-template.yaml"

aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --output json |
  jq '[.Stacks[0].Parameters[] | {ParameterKey, ParameterValue}]' > \
    "$LEAN_EVAL_AWS_OPS/pre-parameters.json"

aws iam get-role \
  --role-name lean-eval-release-unwrap-invoker-staging \
  --query Role.AssumeRolePolicyDocument \
  --output json > "$LEAN_EVAL_AWS_OPS/pre-trust.json"

LEAN_EVAL_PRODUCTION_UPDATED_BEFORE="$(aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --query 'Stacks[0].LastUpdatedTime' \
  --output text)"

cd "$LEAN_EVAL_AWS_OPS/source"
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_aws_key_infrastructure
sam validate --lint \
  --template-file infrastructure/aws-key-adapter/template.yaml
sam build --template-file infrastructure/aws-key-adapter/template.yaml
sam package \
  --template-file .aws-sam/build/template.yaml \
  --s3-bucket aws-sam-cli-managed-default-samclisourcebucket-ygefen7ybulh \
  --region "$LEAN_EVAL_AWS_REGION" \
  --output-template-file "$LEAN_EVAL_AWS_OPS/packaged.yaml"
```

`sam package` may read or upload a content-addressed build artifact in the
account's private SAM-managed artifact bucket. That packaging side effect is
not a workload-stack change, but it is still an AWS write and belongs inside
the explicitly authorized operator session.

## Create and inspect the staging-only change set

```sh
LEAN_EVAL_CHANGE_SET="release-oidc-staging-$(date -u +%Y%m%dT%H%M%SZ)"

aws cloudformation create-change-set \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --change-set-name "$LEAN_EVAL_CHANGE_SET" \
  --change-set-type UPDATE \
  --description "Trust exact ID-bearing GitHub release-staging OIDC subject" \
  --template-body "file://$LEAN_EVAL_AWS_OPS/packaged.yaml" \
  --parameters \
    ParameterKey=EnvironmentName,ParameterValue=staging \
    ParameterKey=GitHubOidcProviderArn,ParameterValue="$LEAN_EVAL_OIDC_PROVIDER_ARN" \
    ParameterKey=SubmissionGitHubSubjectPrefix,ParameterValue="$LEAN_EVAL_SUBMISSION_PREFIX" \
    ParameterKey=ReleaseGitHubSubjectPrefix,ParameterValue="$LEAN_EVAL_RELEASE_PREFIX" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$LEAN_EVAL_AWS_REGION"

aws cloudformation wait change-set-create-complete \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --change-set-name "$LEAN_EVAL_CHANGE_SET" \
  --region "$LEAN_EVAL_AWS_REGION"

aws cloudformation describe-change-set \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
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

Stop if the final command fails. Delete the change set rather than broadening
the whitelist.

## Execute and verify

Execution requires explicit approval for this exact staging-only mutation.

```sh
aws cloudformation execute-change-set \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --change-set-name "$LEAN_EVAL_CHANGE_SET" \
  --region "$LEAN_EVAL_AWS_REGION"

aws cloudformation wait stack-update-complete \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --region "$LEAN_EVAL_AWS_REGION"

aws iam get-role \
  --role-name lean-eval-release-unwrap-invoker-staging \
  --query Role.AssumeRolePolicyDocument \
  --output json > "$LEAN_EVAL_AWS_OPS/post-trust.json"

jq -e --arg provider "$LEAN_EVAL_OIDC_PROVIDER_ARN" '
  (keys == ["Statement", "Version"]) and
  .Version == "2012-10-17" and
  (.Statement | length) == 1 and
  (.Statement[0] | keys == ["Action", "Condition", "Effect", "Principal"]) and
  .Statement[0].Effect == "Allow" and
  (.Statement[0].Principal | keys == ["Federated"]) and
  .Statement[0].Principal.Federated == $provider and
  .Statement[0].Action == "sts:AssumeRoleWithWebIdentity" and
  (.Statement[0].Condition | keys == ["StringEquals"]) and
  (.Statement[0].Condition.StringEquals | keys == [
    "token.actions.githubusercontent.com:aud",
    "token.actions.githubusercontent.com:sub"
  ]) and
  .Statement[0].Condition.StringEquals[
    "token.actions.githubusercontent.com:aud"
  ] == "sts.amazonaws.com" and
  .Statement[0].Condition.StringEquals[
    "token.actions.githubusercontent.com:sub"
  ] == "repo:leanprover@7233018/lean-eval-releases@1340741242:environment:release-staging"
' "$LEAN_EVAL_AWS_OPS/post-trust.json"

test "$(aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ReleaseInvokerRoleArn'].OutputValue | [0]" \
  --output text)" = "$LEAN_EVAL_STAGING_ROLE"

test "$(gh api \
  repos/leanprover/lean-eval-releases/environments/release-staging/variables \
  --jq '.variables[] | select(.name=="AWS_RELEASE_UNWRAP_ROLE_ARN") | .value')" = \
  "$LEAN_EVAL_STAGING_ROLE"

test "$(aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --query 'Stacks[0].LastUpdatedTime' \
  --output text)" = "$LEAN_EVAL_PRODUCTION_UPDATED_BEFORE"
```

## Run the publication-disabled release smoke

The smoke enters only `release-staging`, invokes only the staging Lambda alias,
uploads no artifact, discards plaintext, and exits before every production
publication step.

```sh
test "$(gh api repos/leanprover/lean-eval-releases/actions/variables \
  --jq '[.variables[] | select(.name=="PUBLICATION_ENABLED")] | length')" = 0

LEAN_EVAL_STAGING_STATE_BEFORE="$(gh api \
  repos/leanprover/lean-eval-state-staging/git/ref/heads/main \
  --jq .object.sha)"
LEAN_EVAL_AUDIT_BEFORE="$(gh api \
  repos/leanprover/lean-eval-audit/git/ref/heads/main \
  --jq .object.sha)"
LEAN_EVAL_RELEASE_BEFORE="$(gh api \
  repos/leanprover/lean-eval-releases/git/ref/heads/main \
  --jq .object.sha)"
test "$LEAN_EVAL_RELEASE_BEFORE" = "$LEAN_EVAL_RELEASE_COMMIT"

LEAN_EVAL_DISPATCHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

gh workflow run credentialed-release-staging-smoke.yml \
  --repo leanprover/lean-eval-releases \
  --ref main \
  -f submission_id=01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584 \
  -f confirm_staging_smoke=true

LEAN_EVAL_RUN_ID=
LEAN_EVAL_DISCOVERY_DEADLINE=$(($(date +%s) + 120))
while [ -z "$LEAN_EVAL_RUN_ID" ]; do
  gh run list \
    --repo leanprover/lean-eval-releases \
    --workflow credentialed-release-staging-smoke.yml \
    --event workflow_dispatch \
    --branch main \
    --limit 20 \
    --json databaseId,createdAt,headSha \
    --jq "[.[] | select(
      .headSha == \"$LEAN_EVAL_RELEASE_COMMIT\" and
      .createdAt >= \"$LEAN_EVAL_DISPATCHED_AT\"
    )]" > "$LEAN_EVAL_AWS_OPS/run-candidates.json"
  LEAN_EVAL_CANDIDATE_COUNT="$(jq length \
    "$LEAN_EVAL_AWS_OPS/run-candidates.json")"
  if [ "$LEAN_EVAL_CANDIDATE_COUNT" -gt 1 ]; then
    echo "multiple matching staging-smoke runs; refusing ambiguous evidence" >&2
    exit 1
  fi
  if [ "$LEAN_EVAL_CANDIDATE_COUNT" -eq 1 ]; then
    LEAN_EVAL_RUN_ID="$(jq -er '.[0].databaseId' \
      "$LEAN_EVAL_AWS_OPS/run-candidates.json")"
    break
  fi
  if [ "$(date +%s)" -ge "$LEAN_EVAL_DISCOVERY_DEADLINE" ]; then
    echo "timed out waiting for the exact staging-smoke run" >&2
    exit 1
  fi
  sleep 5
done
[[ "$LEAN_EVAL_RUN_ID" =~ ^[1-9][0-9]*$ ]]

gh run watch "$LEAN_EVAL_RUN_ID" \
  --repo leanprover/lean-eval-releases \
  --exit-status

gh run view "$LEAN_EVAL_RUN_ID" \
  --repo leanprover/lean-eval-releases \
  --json headSha,conclusion,jobs,url > "$LEAN_EVAL_AWS_OPS/run.json"

jq -e --arg head "$LEAN_EVAL_RELEASE_COMMIT" '
  .headSha == $head and
  .conclusion == "success" and
  ([.jobs[] | {name, conclusion}] | sort_by(.name)) == [
    {"name": "prepare-one", "conclusion": "success"},
    {"name": "unwrap-one", "conclusion": "success"}
  ]
' "$LEAN_EVAL_AWS_OPS/run.json"

test "$(gh api \
  "repos/leanprover/lean-eval-releases/actions/runs/$LEAN_EVAL_RUN_ID/artifacts" \
  --jq .total_count)" = 0
test "$(gh api repos/leanprover/lean-eval-state-staging/git/ref/heads/main \
  --jq .object.sha)" = "$LEAN_EVAL_STAGING_STATE_BEFORE"
test "$(gh api repos/leanprover/lean-eval-audit/git/ref/heads/main \
  --jq .object.sha)" = "$LEAN_EVAL_AUDIT_BEFORE"
test "$(gh api repos/leanprover/lean-eval-releases/git/ref/heads/main \
  --jq .object.sha)" = "$LEAN_EVAL_RELEASE_BEFORE"
test "$(aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --query 'Stacks[0].LastUpdatedTime' \
  --output text)" = "$LEAN_EVAL_PRODUCTION_UPDATED_BEFORE"
test "$(gh api repos/leanprover/lean-eval-releases/actions/variables \
  --jq '[.variables[] | select(.name=="PUBLICATION_ENABLED")] | length')" = 0
```

The machine checks require exact release head
`90dadc872d624b8e6d171caf439313d185fc3e7f`, overall success, and exactly the
successful `prepare-one` and `unwrap-one` jobs. Open the exact URL recorded in
`run.json` and retain the `unwrap-one` job summary; GitHub does not reliably
return Actions job-summary text through its check-run API. The summary must say
`Credentialed staging release boundary passed` and identify submission
`01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584`, audit commit
`92b95c162ad9bf38d027e11193683ca61ed2a994`, and exact ciphertext digest
`58f9e6c60d4736d82a831d53a1b99b75e86eac5b34b2a27ed2afafe460ab7f22`.
The commands above independently require no uploaded artifact,
unchanged staging State, audit, and release refs, the unchanged production
stack timestamp, and an absent `PUBLICATION_ENABLED` variable.

## Rollback

CloudFormation performs normal automatic rollback if the update fails. To
restore the deliberately captured pre-update template after a successful
update, create another change set and apply the same one-resource whitelist:

```sh
LEAN_EVAL_ROLLBACK_SET="release-oidc-staging-rollback-$(date -u +%Y%m%dT%H%M%SZ)"

aws cloudformation create-change-set \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --change-set-name "$LEAN_EVAL_ROLLBACK_SET" \
  --change-set-type UPDATE \
  --template-body "file://$LEAN_EVAL_AWS_OPS/pre-template.yaml" \
  --parameters "file://$LEAN_EVAL_AWS_OPS/pre-parameters.json" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$LEAN_EVAL_AWS_REGION"

aws cloudformation wait change-set-create-complete \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --change-set-name "$LEAN_EVAL_ROLLBACK_SET" \
  --region "$LEAN_EVAL_AWS_REGION"

aws cloudformation describe-change-set \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --change-set-name "$LEAN_EVAL_ROLLBACK_SET" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --output json > "$LEAN_EVAL_AWS_OPS/rollback-change-set.json"

jq -e '
  .Status == "CREATE_COMPLETE" and
  (.Changes | length) == 1 and
  .Changes[0].ResourceChange.Action == "Modify" and
  .Changes[0].ResourceChange.LogicalResourceId == "ReleaseInvokerRole" and
  .Changes[0].ResourceChange.ResourceType == "AWS::IAM::Role" and
  .Changes[0].ResourceChange.Replacement == "False"
' "$LEAN_EVAL_AWS_OPS/rollback-change-set.json"

aws cloudformation execute-change-set \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --change-set-name "$LEAN_EVAL_ROLLBACK_SET" \
  --region "$LEAN_EVAL_AWS_REGION"

aws cloudformation wait stack-update-complete \
  --stack-name "$LEAN_EVAL_STAGING_STACK" \
  --region "$LEAN_EVAL_AWS_REGION"

aws iam get-role \
  --role-name lean-eval-release-unwrap-invoker-staging \
  --query Role.AssumeRolePolicyDocument \
  --output json > "$LEAN_EVAL_AWS_OPS/rollback-trust.json"

cmp <(jq -S . "$LEAN_EVAL_AWS_OPS/pre-trust.json") \
  <(jq -S . "$LEAN_EVAL_AWS_OPS/rollback-trust.json")
test "$(aws cloudformation describe-stacks \
  --stack-name "$LEAN_EVAL_PRODUCTION_STACK" \
  --region "$LEAN_EVAL_AWS_REGION" \
  --query 'Stacks[0].LastUpdatedTime' \
  --output text)" = "$LEAN_EVAL_PRODUCTION_UPDATED_BEFORE"
```

Do not use `iam update-assume-role-policy` during the planned repair: it would
create stack drift. Reserve direct IAM mutation for emergency recovery only.
