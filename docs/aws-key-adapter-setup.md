# Dedicated AWS key-adapter setup

This is the remaining manual D6 infrastructure step. It creates no replay VM
and does not enable intake or release. The same template creates isolated
staging and production resources in one dedicated Lean Eval AWS account.

## 1. Create the account

Create a new AWS account used only for Lean Eval archive identities. Record its
account ID, root/contact email, billing owner, and administrator in
`INFRASTRUCTURE.md`; never record a password, recovery code, or token. Enable
MFA for the root user, perform ordinary administration through IAM Identity
Center, and do not create an IAM access key.

Use `us-east-1` for the initial service. The archive and capability contracts
do not contain the account or region, so this choice does not prevent a later
provider or region migration.

## 2. Add GitHub's OIDC provider

In IAM → Identity providers, add the OpenID Connect provider:

```text
Provider URL: https://token.actions.githubusercontent.com
Audience:     sts.amazonaws.com
```

Copy its ARN. The deployment template binds roles to the exact protected
GitHub environment subjects in `leanprover/lean-eval-submissions`; it does not
accept an organization-wide wildcard.

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
    GitHubOidcProviderArn="$OIDC_PROVIDER_ARN"

sam deploy \
  --template-file .aws-sam/build/template.yaml \
  --stack-name lean-eval-key-adapter-production \
  --region us-east-1 \
  --resolve-s3 \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    EnvironmentName=production \
    GitHubOidcProviderArn="$OIDC_PROVIDER_ARN"
```

The build artifact contains only `aws_key_adapter.py` and
`key_capability_contract.py`. The template creates no public URL, API Gateway,
access key, backup system, alarm system, or recovery provider.

## 4. Record outputs; do not connect production yet

The six GitHub environment shells already exist:

- `archive-staging` and `archive-production` in
  `leanprover/lean-eval-submissions`, each restricted to the tag pattern
  `lean-eval-dispatch/*`;
- `replay-staging` and `replay-production` in
  `leanprover/lean-eval-submissions`, each restricted to protected branches;
- `release-staging` and `release-production` in
  `leanprover/lean-eval-releases`, each restricted to protected branches.

They are intentionally empty: no secret or variable is installed and they
grant no AWS authority. Do not recreate or broaden them.

For each stack, copy the seven non-secret outputs into `INFRASTRUCTURE.md`:

- KMS key ARN;
- one-use DynamoDB table name;
- archive Wrap role ARN;
- versioned Unwrap Lambda alias ARN;
- replay Unwrap controller role ARN;
- release Unwrap controller role ARN; and
- adapter name (`aws-kms-v1`).

After the outputs exist, store each corresponding role ARN as a non-secret
variable in its existing environment. Recheck rather than change the recorded
ref policies. Use variable `AWS_WRAP_ROLE_ARN` in `archive-staging` and
`AWS_REPLAY_UNWRAP_ROLE_ARN` in `replay-staging`; reserve the corresponding
production and release role variables until their workflows are reviewed.

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
