# Dedicated AWS key-adapter setup

This procedure creates no replay VM and does not enable intake or release. The
same template creates isolated staging and production resources in one
dedicated Lean Eval AWS account; exact live identifiers, credential boundaries,
and rotation ownership belong in `INFRASTRUCTURE.md`.

Standing maintainer authorization covers the remaining in-scope infrastructure
and credential operations in this procedure. Each closed check, change packet,
rollback, and post-change readback remains mandatory. A short-lived
authenticated administrator session supplied by the maintainer is an operator
handoff, not a new permission gate.

## 1. Create the account

Create a new AWS account used only for Lean Eval archive identities. Record its
account ID, root/contact email, billing owner, and administrator in
`INFRASTRUCTURE.md`; never record a password, recovery code, or token. Enable
MFA for the root user and do not create an IAM access key. This standalone
bootstrap account deliberately does not create an AWS Organization or IAM
Identity Center instance: a future Lean FRO organization can invite the
account and supply centralized administration. Until then, use only short-lived
root console sessions for narrowly scoped administration and log out afterward.

Use `us-east-1` for the initial service. The archive and capability contracts
do not contain the account or region, so this choice does not prevent a later
provider or region migration.

## 2. Add GitHub's OIDC provider

In IAM → Identity providers, add the OpenID Connect provider:

```text
Provider URL: https://token.actions.githubusercontent.com
Audience:     sts.amazonaws.com
```

Copy its ARN. The deployment template binds archive and replay roles to exact
protected GitHub environment subjects in `leanprover/lean-eval-submissions`
and release roles to exact protected subjects in
`leanprover/lean-eval-releases`; it does not accept an organization-wide
wildcard. Read both subject prefixes from GitHub's repository OIDC
customization API immediately before preparing a deployment or change set.

## 3. Validate and deploy both stacks

From the repository root, with an authenticated administrator session in the
new account:

```sh
sam validate --lint \
  --template-file infrastructure/aws-key-adapter/template.yaml

sam build \
  --template-file infrastructure/aws-key-adapter/template.yaml
```

Set `OIDC_PROVIDER_ARN` to the copied non-secret ARN, then deploy staging and
production:

```sh
sam deploy \
  --template-file .aws-sam/build/template.yaml \
  --stack-name lean-eval-key-adapter-staging \
  --region us-east-1 \
  --resolve-s3 \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    EnvironmentName=staging \
    GitHubOidcProviderArn="$OIDC_PROVIDER_ARN" \
    SubmissionGitHubSubjectPrefix=leanprover/lean-eval-submissions \
    ReleaseGitHubSubjectPrefix=leanprover@7233018/lean-eval-releases@1340741242

sam deploy \
  --template-file .aws-sam/build/template.yaml \
  --stack-name lean-eval-key-adapter-production \
  --region us-east-1 \
  --resolve-s3 \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    EnvironmentName=production \
    GitHubOidcProviderArn="$OIDC_PROVIDER_ARN" \
    SubmissionGitHubSubjectPrefix=leanprover/lean-eval-submissions \
    ReleaseGitHubSubjectPrefix=leanprover@7233018/lean-eval-releases@1340741242
```

The subject prefixes are read from GitHub's repository OIDC customization API
before every stack deployment. Repositories created or transferred after
GitHub's immutable-subject rollout include stable owner and repository IDs in
that prefix. The release repository currently uses the immutable prefix above;
the older submissions repository retains its name-based prefix. Never infer a
subject from the display repository name when the API reports a different
prefix.

The build artifact contains only `aws_key_adapter.py` and
`key_capability_contract.py`. The template creates no public URL, API Gateway,
access key, backup system, alarm system, or recovery provider. One-use is an
atomic DynamoDB condition and does not depend on Lambda reserved concurrency;
this also permits deployment in a new account with AWS's minimum regional
concurrency quota.

## 4. Record outputs and connect only reviewed consumers

The six GitHub environment shells already exist:

- `archive-staging` and `archive-production` in
  `leanprover/lean-eval-submissions`, each restricted to the tag pattern
  `lean-eval-dispatch/*`;
- `replay-staging` in `leanprover/lean-eval-submissions`, restricted to exact
  branch `main` and tag pattern `lean-eval-dispatch/*` so the existing public
  replay and this immutable-tag smoke can both enter it;
- `replay-production` in `leanprover/lean-eval-submissions`, restricted to
  protected branches;
- `release-staging` and `release-production` in
  `leanprover/lean-eval-releases`, each restricted to protected branches.

The separate `archive-migration-production` environment is restricted to
protected branches. It must use the production-only
`MigrationWrapRoleArn` output: the ordinary production archive role trusts
only `archive-production` and deliberately cannot be assumed by a migration
job.

The archive and replay staging role variables, the production archive Wrap
variable, and both release role variables are installed. The release variables
are non-secret role selectors and do not enable publication; the separate
`PUBLICATION_ENABLED` repository variable remains absent. The production
replay role variable remains deliberately unconnected. Do not recreate or
broaden the environments, and do not connect another production consumer
merely because its dormant stack exists.

For each stack, copy the seven common non-secret outputs into
`INFRASTRUCTURE.md`:

- KMS key ARN;
- one-use DynamoDB table name;
- archive Wrap role ARN;
- versioned Unwrap Lambda alias ARN;
- replay Unwrap controller role ARN;
- release Unwrap controller role ARN; and
- adapter name (`aws-kms-v1`).

The production stack has an eighth output, `MigrationWrapRoleArn`. It is an
Encrypt-only role bound exactly to the `archive-migration-production` OIDC
subject. Store that ARN as `AWS_WRAP_ROLE_ARN` only in the migration
environment; do not reuse the ordinary `WrapRoleArn` there.
Use the pinned
[`aws-production-archive-migration-infrastructure.md`](aws-production-archive-migration-infrastructure.md)
operator path for the one-time production update and selector connection.

After the outputs exist, store each reviewed role ARN as a non-secret variable
in its existing environment. Recheck rather than change the recorded ref
policies. Use variable `AWS_WRAP_ROLE_ARN` in `archive-staging`,
`AWS_REPLAY_UNWRAP_ROLE_ARN` in `replay-staging`, and
`AWS_RELEASE_UNWRAP_ROLE_ARN` in each matching release environment. The
release role variables are installed because their workflows have been
reviewed; production publication remains independently disabled. Reserve the
production replay variable until its workflow and launch readiness packet are
qualified.

List both stacks' recorded outputs without exposing a credential:

```sh
aws cloudformation describe-stacks \
  --stack-name lean-eval-key-adapter-staging \
  --region us-east-1 \
  --query 'Stacks[0].Outputs' \
  --output table

aws cloudformation describe-stacks \
  --stack-name lean-eval-key-adapter-production \
  --region us-east-1 \
  --query 'Stacks[0].Outputs' \
  --output table
```

The original archive/replay staging bootstrap installed the two role outputs
below. The values are non-secret ARNs, but still avoid placing them in an issue
or chat:

```sh
LEAN_EVAL_STAGING_WRAP_ROLE_ARN="$(aws cloudformation describe-stacks \
  --stack-name lean-eval-key-adapter-staging \
  --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='WrapRoleArn'].OutputValue | [0]" \
  --output text)"

LEAN_EVAL_STAGING_REPLAY_ROLE_ARN="$(aws cloudformation describe-stacks \
  --stack-name lean-eval-key-adapter-staging \
  --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='ReplayInvokerRoleArn'].OutputValue | [0]" \
  --output text)"

test -n "$LEAN_EVAL_STAGING_WRAP_ROLE_ARN"
test "$LEAN_EVAL_STAGING_WRAP_ROLE_ARN" != None
test -n "$LEAN_EVAL_STAGING_REPLAY_ROLE_ARN"
test "$LEAN_EVAL_STAGING_REPLAY_ROLE_ARN" != None

gh variable set AWS_WRAP_ROLE_ARN \
  --repo leanprover/lean-eval-submissions \
  --env archive-staging \
  --body "$LEAN_EVAL_STAGING_WRAP_ROLE_ARN"

gh variable set AWS_REPLAY_UNWRAP_ROLE_ARN \
  --repo leanprover/lean-eval-submissions \
  --env replay-staging \
  --body "$LEAN_EVAL_STAGING_REPLAY_ROLE_ARN"
```

Verify the variable names, environment boundaries, and existing ref policies
through the GitHub API before running the smoke. The API returns names and
non-secret values; it never returns an AWS credential:

```sh
gh api repos/leanprover/lean-eval-submissions/environments/archive-staging/variables
gh api repos/leanprover/lean-eval-submissions/environments/replay-staging/variables
```

Then run `AWS key-adapter staging smoke` from the immutable
`lean-eval-dispatch/<workflow-commit>` tag containing the workflow. It creates
one synthetic archive under the Encrypt-only role, transfers only ciphertext,
the provider-neutral envelope, and a marker digest through a one-day artifact,
invokes the versioned
unwrap alias through the replay Invoke-only role, proves the same capability is
rejected on its second use, drops AWS authority, and decrypts the synthetic
archive. The synthetic locator is never appended to State and is not evidence
that an audit-repository object exists. No identity, plaintext source, AWS
credential, State event, result, or release is uploaded.

### Production Wrap-only launch preflight

The production `archive-production` environment has the exact Encrypt-only
`AWS_WRAP_ROLE_ARN`, and the synthetic Wrap/decrypt-denial preflight is
qualified. Before changing or rechecking that boundary, run the repository's
fail-closed, read-only check with a short-lived AWS administrator session:

```sh
python3 scripts/preflight_production_wrap_role.py
```

It requires the exact account, stable production stack, stack outputs, role
name and ARN, one exact OIDC trust statement, 3600-second role maximum, absence
of a permissions boundary, managed policies and instance profiles, one exact
inline policy, the production alias resolving to the exact KMS key, the
root-only key policy, no KMS grants, and enabled annual rotation. The inline
policy contains only `kms:Encrypt` on the
production key with all five version-1 contexts present and no other context
key. Any difference stops the operation; do not replace the check with a
visual policy review.

In a separately authenticated GitHub shell, require the current boundary to be
connected to the exact reviewed role and the environment's existing wildcard
immutable-tag policy to be unchanged:

```sh
set -euo pipefail

LEAN_EVAL_SUBMISSIONS=leanprover/lean-eval-submissions
LEAN_EVAL_ARCHIVE_ENVIRONMENT=archive-production
LEAN_EVAL_WRAP_ROLE_ARN=arn:aws:iam::161072922960:role/lean-eval-archive-wrap-production

gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/actions/oidc/customization/sub \
  | jq -e '. == {
      use_default: true,
      use_immutable_subject: false,
      sub_claim_prefix: "repo:leanprover/lean-eval-submissions"
    }'
gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/environments/$LEAN_EVAL_ARCHIVE_ENVIRONMENT \
  | jq -e '
      .can_admins_bypass == true and
      .deployment_branch_policy == {
        protected_branches: false,
        custom_branch_policies: true
      } and
      [.protection_rules[] | .type] == ["branch_policy"]
    '
gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/environments/$LEAN_EVAL_ARCHIVE_ENVIRONMENT/deployment-branch-policies \
  | jq -e '
      .total_count == 1 and
      .branch_policies == [{
        id: 57914846,
        node_id: "MDE2OkdhdGVCcmFuY2hQb2xpY3k1NzkxNDg0Ng==",
        name: "lean-eval-dispatch/*",
        type: "tag"
      }]
    '
test "$(gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/environments/$LEAN_EVAL_ARCHIVE_ENVIRONMENT/variables \
  --jq '.variables | if length == 1 then .[0].name + "=" + .[0].value else "" end')" = \
  "AWS_WRAP_ROLE_ARN=$LEAN_EVAL_WRAP_ROLE_ARN"
test "$(gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/environments/replay-production/variables \
  --jq .total_count)" = 0
test "$(gh api repos/leanprover/lean-eval-releases/actions/variables \
  --jq '[.variables[] | select(.name=="PUBLICATION_ENABLED")] | length')" = 0

LEAN_EVAL_WRAP_WORKFLOW_COMMIT="$(gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/branches/main \
  --jq .commit.sha)"
case "$LEAN_EVAL_WRAP_WORKFLOW_COMMIT" in
  ''|*[!0-9a-f]*) exit 1 ;;
esac
test "${#LEAN_EVAL_WRAP_WORKFLOW_COMMIT}" -eq 40
test "$(gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/branches/main \
  --jq .protected)" = true

LEAN_EVAL_HEALTH_DIR="$(mktemp -d)"
chmod 700 "$LEAN_EVAL_HEALTH_DIR"
python3 scripts/verify_production_capabilities_disabled.py \
  --repository . \
  --expected-commit "$LEAN_EVAL_WRAP_WORKFLOW_COMMIT" \
  --output-directory "$LEAN_EVAL_HEALTH_DIR"
python3 scripts/monitor_cloudflare_health.py \
  --intake-config "$LEAN_EVAL_HEALTH_DIR/intake.jsonc" \
  --replay-config "$LEAN_EVAL_HEALTH_DIR/replay.jsonc" \
  --output "$LEAN_EVAL_HEALTH_DIR/health.json"
jq -e --arg commit "$LEAN_EVAL_WRAP_WORKFLOW_COMMIT" '
    .status == "ready" and
    .deployed_commit == $commit and
    .observations.production.capabilities == {
      historical_public_replay_enabled: false,
      intake_enabled: false,
      legacy_result_owner_api_enabled: false,
      model_identity_consolidation_api_enabled: false,
      model_identity_maintainer_api_enabled: false,
      model_identity_owner_api_enabled: false,
      promotion_canary_enabled: false,
      release_opt_out_api_enabled: false,
      replay_enabled: false,
      result_amendment_maintainer_api_enabled: false,
      result_amendment_owner_api_enabled: false,
      staging_acceptance_enabled: false
    }
  ' "$LEAN_EVAL_HEALTH_DIR/health.json"
rm -rf "$LEAN_EVAL_HEALTH_DIR"
```

The fixed verifier reads both configs from that exact Git object, rejects every
production intake, lifecycle, model-consolidation, canary, general-replay,
historical-replay and production acceptance capability unless it is literal
`false` (and the intake mode literal `disabled`), then gives those immutable
blobs to the health monitor. The monitor requires the live production values
to equal them and every live component to report that same selected commit.
This is production-only: intentional staging acceptance and canary flags may
remain enabled. Stop if any command fails.

The exact environment readback also discloses and requires the existing
`can_admins_bypass: true`: repository administrators can bypass its tag
protection. This procedure does not change or hide that standing boundary.
The exact connected variable is:

```text
Repository:  leanprover/lean-eval-submissions
Environment: archive-production
Variable:    AWS_WRAP_ROLE_ARN
Value:       arn:aws:iam::161072922960:role/lean-eval-archive-wrap-production
```

The role trust must name exactly
`repo:leanprover/lean-eval-submissions:environment:archive-production`; its
workload policy must contain only `kms:Encrypt` on production key
`219904f9-4952-400f-b60a-6f027c4d070b`, conditioned on the five exact
`lean-eval-archive-key-v1` encryption-context keys. It must not contain
`kms:Decrypt`, Lambda, DynamoDB, IAM, GitHub, State, replay, or release
authority.

The environment's `lean-eval-dispatch/*` policy is deliberately not changed by
this operation. Consequently, connecting this variable is durable standing
Wrap authority for the normal archive lane on every tag admitted by that
policy, including historical immutable tags; it is not authority limited to
the one synthetic preflight run. Each archive workflow independently requires
its tag name, workflow commit input and `github.sha` to agree. Narrowing or
otherwise changing the wildcard policy is a separate protected-environment
design, not part of this connection.

To repair a missing connection, add and read back only the exact variable, then
rerun the AWS boundary check:

```sh
gh variable set AWS_WRAP_ROLE_ARN \
  --repo "$LEAN_EVAL_SUBMISSIONS" \
  --env "$LEAN_EVAL_ARCHIVE_ENVIRONMENT" \
  --body "$LEAN_EVAL_WRAP_ROLE_ARN"
test "$(gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/environments/$LEAN_EVAL_ARCHIVE_ENVIRONMENT/variables \
  --jq '.variables | if length == 1 then .[0].name + "=" + .[0].value else "" end')" = \
  "AWS_WRAP_ROLE_ARN=$LEAN_EVAL_WRAP_ROLE_ARN"
python3 scripts/preflight_production_wrap_role.py
```

Select the exact final protected-main submission commit only after it has its
immutable `lean-eval-dispatch/<workflow-commit>` tag. Require the remote tag to
resolve to that commit before dispatch; do not select an older tag merely
because it contains the same workflow file:

```sh
test "$(gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/branches/main \
  --jq .commit.sha)" = "$LEAN_EVAL_WRAP_WORKFLOW_COMMIT"
test "$(gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/branches/main \
  --jq .protected)" = true
test "$(gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/git/ref/tags/lean-eval-dispatch/$LEAN_EVAL_WRAP_WORKFLOW_COMMIT \
  --jq .object.sha)" = "$LEAN_EVAL_WRAP_WORKFLOW_COMMIT"
gh workflow run aws-production-wrap-preflight.yml \
  --repo "$LEAN_EVAL_SUBMISSIONS" \
  --ref "lean-eval-dispatch/$LEAN_EVAL_WRAP_WORKFLOW_COMMIT"
```

These immediate readbacks require the selected commit to remain the current
protected `main` tip and its immutable dispatch tag to resolve to that same
commit. The workflow refuses any other role ARN, assumes only the exact AWS
account, KMS-encrypts one temporary random 32-byte synthetic key with the exact
contract/context, and requires direct decrypt to return `AccessDeniedException`.
It uploads nothing,
writes no repository, State, audit object, result, release, or submission, and
removes the plaintext, ciphertext, AWS session and OIDC request handles on
exit. It then pins credential/config files to absent paths and requires an STS
caller lookup to fail. This is the bounded launch check, not a recurring
qualification harness.

Rollback is deletion of only `AWS_WRAP_ROLE_ARN` from `archive-production`,
followed by cancellation of any queued or running archive/preflight jobs. An
already-started job may retain its short-lived AWS session until it exits, so
deleting the variable alone does not revoke that active session. Use one shell
for the following fail-closed rollback. The first block deletes the variable,
cancels relevant runs, and records the first time at which no relevant run
remains active:

```sh
set -euo pipefail

LEAN_EVAL_WRAP_ROLLBACK_DIR="$(mktemp -d)"
chmod 700 "$LEAN_EVAL_WRAP_ROLLBACK_DIR"

active_wrap_runs() {
  local all_ids response
  all_ids=
  for workflow in aws-production-wrap-preflight.yml submission.yml; do
    for status in queued in_progress waiting requested pending; do
      response="$(gh api --method GET \
        "repos/$LEAN_EVAL_SUBMISSIONS/actions/workflows/$workflow/runs" \
        -f status="$status" -f per_page=100 \
        --paginate \
        --jq '.workflow_runs[].id')" || return $?
      if [ -n "$response" ]; then
        all_ids+="$response"$'\n'
      fi
    done
  done
  if [ -n "$all_ids" ]; then
    printf '%s' "$all_ids" | sort -u
  fi
}

gh variable delete AWS_WRAP_ROLE_ARN \
  --repo "$LEAN_EVAL_SUBMISSIONS" \
  --env "$LEAN_EVAL_ARCHIVE_ENVIRONMENT"
case "$LEAN_EVAL_WRAP_WORKFLOW_COMMIT" in
  ''|*[!0-9a-f]*) exit 1 ;;
esac
test "${#LEAN_EVAL_WRAP_WORKFLOW_COMMIT}" -eq 40
printf '%s\n' "$LEAN_EVAL_WRAP_WORKFLOW_COMMIT" > \
  "$LEAN_EVAL_WRAP_ROLLBACK_DIR/selected-commit"
active_ids="$(active_wrap_runs)"
for run_id in $active_ids; do
  gh run cancel "$run_id" --repo "$LEAN_EVAL_SUBMISSIONS"
done
active_ids="$(active_wrap_runs)"
test -z "$active_ids"
date -u +%s > "$LEAN_EVAL_WRAP_ROLLBACK_DIR/authority-quiet-at"
```

Cancellation is asynchronous; if the final assertion fails, recheck and
cancel again, then record a new quiet timestamp only after the assertion
passes. Do not proceed on a merely requested cancellation. Keep the shell and
temporary directory. No earlier than 3600 seconds after that quiet timestamp,
run the complete readback. This covers the preflight's requested 900-second
session and the role's 3600-second maximum if a normal archive job had already
assumed it before cancellation:

```sh
set -euo pipefail

quiet_at="$(cat "$LEAN_EVAL_WRAP_ROLLBACK_DIR/authority-quiet-at")"
case "$quiet_at" in ''|*[!0-9]*) exit 1 ;; esac
selected_commit="$(cat "$LEAN_EVAL_WRAP_ROLLBACK_DIR/selected-commit")"
case "$selected_commit" in
  ''|*[!0-9a-f]*) exit 1 ;;
esac
test "${#selected_commit}" -eq 40
test "$(( $(date -u +%s) - quiet_at ))" -ge 3600
test "$(gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/environments/$LEAN_EVAL_ARCHIVE_ENVIRONMENT/variables \
  --jq .total_count)" = 0
test "$(gh api \
  repos/$LEAN_EVAL_SUBMISSIONS/environments/replay-production/variables \
  --jq .total_count)" = 0
active_ids="$(active_wrap_runs)"
test -z "$active_ids"
test "$(gh api repos/leanprover/lean-eval-releases/actions/variables \
  --jq '[.variables[] | select(.name=="PUBLICATION_ENABLED")] | length')" = 0

mkdir -m 700 "$LEAN_EVAL_WRAP_ROLLBACK_DIR/exact-config"
python3 scripts/verify_production_capabilities_disabled.py \
  --repository . \
  --expected-commit "$selected_commit" \
  --output-directory "$LEAN_EVAL_WRAP_ROLLBACK_DIR/exact-config"
python3 scripts/monitor_cloudflare_health.py \
  --intake-config "$LEAN_EVAL_WRAP_ROLLBACK_DIR/exact-config/intake.jsonc" \
  --replay-config "$LEAN_EVAL_WRAP_ROLLBACK_DIR/exact-config/replay.jsonc" \
  --output "$LEAN_EVAL_WRAP_ROLLBACK_DIR/health.json"
jq -e --arg commit "$selected_commit" '
    .status == "ready" and
    .deployed_commit == $commit and
    .observations.production.capabilities == {
      historical_public_replay_enabled: false,
      intake_enabled: false,
      legacy_result_owner_api_enabled: false,
      model_identity_consolidation_api_enabled: false,
      model_identity_maintainer_api_enabled: false,
      model_identity_owner_api_enabled: false,
      promotion_canary_enabled: false,
      release_opt_out_api_enabled: false,
      replay_enabled: false,
      result_amendment_maintainer_api_enabled: false,
      result_amendment_owner_api_enabled: false,
      staging_acceptance_enabled: false
    }
  ' "$LEAN_EVAL_WRAP_ROLLBACK_DIR/health.json"
python3 scripts/preflight_production_wrap_role.py
rm -rf "$LEAN_EVAL_WRAP_ROLLBACK_DIR"
```

The final AWS check proves the standing role itself was not broadened while
connected; the 3600-second delay and inactive-run check prove every possible
session has expired, including every 900-second preflight session. Keep
production intake disabled throughout the connection, preflight, and rollback
decision.

Do not connect production replay authority or enable production intake or
publication merely because the stacks and this bounded synthetic preflight
exist.

## 5. Keep transferred-repository release subjects current without widening trust

The release repository was transferred after GitHub's immutable-subject
rollout. The source template and both live release roles pin the API-reported
ID-bearing subjects. Do not disable immutable subjects or edit either IAM role
directly.

Any future trust drift repair must use CloudFormation, reconcile the exact live
template and complete parameter set, and review the complete change set before
execution. A trust-only correction must contain exactly one non-replacing
`Modify` of `ReleaseInvokerRole` (`AWS::IAM::Role`) and leave the other stack
unchanged. Do not use a full newer template merely to repair trust when that
template contains unrelated deferred authority.

## Why this is one-use

The archive role can only encrypt. The separate replay and release controller
roles can only invoke one versioned Lambda alias. The Lambda role alone can
conditionally insert one
`uc1_…` digest and decrypt one environment KMS ciphertext. It performs the
conditional insert before KMS decrypt. DynamoDB rejects a second insert for
the same digest, so the repeat never reaches KMS. Capability expiry is checked
from the signed/authorized request independently of DynamoDB TTL; TTL is only
eventual cleanup.

When workflow integration is reviewed, the Encrypt-only OIDC role belongs to a
separate trusted archive job that fetches and persists the exact commit before
evaluation. Never add `id-token: write`, AWS credentials, the wrapped identity,
or a plaintext source artifact to the untrusted evaluation job.

Primary references:

- [AWS KMS Encrypt and exact encryption context](https://docs.aws.amazon.com/kms/latest/APIReference/API_Encrypt.html)
- [AWS KMS encryption-context policy conditions](https://docs.aws.amazon.com/kms/latest/developerguide/conditions-kms.html)
- [DynamoDB conditional expressions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.OperatorsAndFunctions.html)
- [DynamoDB TTL semantics](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html)
- [Synchronous Lambda Invoke](https://docs.aws.amazon.com/lambda/latest/api/API_Invoke.html)
- [GitHub OIDC role scoping in AWS](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html)
