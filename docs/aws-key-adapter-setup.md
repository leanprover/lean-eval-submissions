# Dedicated AWS key-adapter setup

This procedure was completed on 2026-08-22. It creates no replay VM and does
not enable intake or release. The same template creates isolated staging and
production resources in one dedicated Lean Eval AWS account; exact live
identifiers and the latest smoke evidence belong in `INFRASTRUCTURE.md`.

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

## 4. Record outputs; do not connect production yet

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

Before provisioning they are intentionally empty. The archive and replay
staging role variables and both release role variables are now installed. The
release variables are non-secret role selectors and do not enable publication;
the separate `PUBLICATION_ENABLED` repository variable remains absent.
Production archive and replay role variables remain deliberately unconnected.
Do not recreate or broaden the environments, and do not connect production
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

After the outputs exist, store each reviewed role ARN as a non-secret variable
in its existing environment. Recheck rather than change the recorded ref
policies. Use variable `AWS_WRAP_ROLE_ARN` in `archive-staging`,
`AWS_REPLAY_UNWRAP_ROLE_ARN` in `replay-staging`, and
`AWS_RELEASE_UNWRAP_ROLE_ARN` in each matching release environment. The
release role variables are installed because their workflows have been
reviewed; production publication remains independently disabled. Reserve the
production archive and replay variables until their workflows and launch gates
are qualified.

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

Do not wire the production archive workflow, enable private replay/release, or
enable production intake merely because the stacks and this synthetic smoke
exist.

## 5. Repair a transferred repository's release subject without widening trust

The release repository was transferred after GitHub's immutable-subject
rollout. The current source template pins the API-reported ID-bearing subject,
but the live staging stack still trusts the obsolete name-only subject. Do not
disable immutable subjects or edit the IAM role directly. Follow
[`aws-release-staging-trust-repair.md`](aws-release-staging-trust-repair.md) to
prepare a staging-only CloudFormation change set, require that it modifies
exactly the non-replacing staging `ReleaseInvokerRole`, leave the production
stack untouched with an unchanged `LastUpdatedTime`, and run the
publication-disabled credentialed smoke.

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
