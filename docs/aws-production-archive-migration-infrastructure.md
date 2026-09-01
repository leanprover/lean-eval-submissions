# Production archive-migration infrastructure

This one-time operator path provisions the already-reviewed, production-only
archive migration role and the v2 unwrap support needed for historical archive
file keys. It preserves the existing v1 archive path and the repaired release
OIDC trust.

The operation is deliberately narrower than the migration itself. It does not
install `LEGACY_ARCHIVE_IDENTITY`, dispatch the migration or replay workflows,
invoke the unwrap Lambda, write Git or State, or enable intake, replay, or
publication. The GitHub environment is already prebound to the dedicated role
ARN. The operation requires that exact selector before the AWS change and
reads it back unchanged afterward; it performs no GitHub mutation.

Run `scripts/operator_production_archive_migration_infra.sh` from the reviewed
repository commit in an AWS CLI session authenticated as account
`161072922960` root. On the maintained operator machine, authenticate the
existing local profile with `aws login --remote --profile lean-eval-bootstrap`,
then run and verify the script with `AWS_PROFILE=lean-eval-bootstrap`. The
account holder performs only the browser authorization step; the operator
session runs the commands. Do not move the procedure through CloudShell or
expose AWS credentials to the repository.

The script fails closed unless all of these facts hold:

- production is still the exact pre-migration stack and staging is unchanged;
- the repaired ID-bearing `release-production` trust is present;
- the live stack still has the legacy `SubmissionGitHubRepository` and
  `ReleaseGitHubRepository` parameter names with their exact current subject
  values; the update replaces those names with the reviewed subject-prefix
  parameters without changing the effective subjects;
- the migration environment is protected, is prebound to the exact dedicated
  migration role ARN, and has no legacy identity;
- the source template and two Lambda source files match commit
  `5397ca582e3d38a88ffda928a48a479a6e9afb6d` byte for byte;
- the CloudFormation change set contains only the migration role, the unwrap
  role's v2 policy addition, and the generated Lambda function/version/alias
  update; and
- every existing output, v1 role policy, role trust, function setting, and
  staging timestamp survives the update.

On success the final line is:

```text
PRODUCTION_ARCHIVE_MIGRATION_INFRA_OK
```

Provisioning makes the prebound selector effective as soon as the AWS stack
update creates the dedicated role, so admitted jobs can assume its v2
Encrypt-only authority. It does not authorize actual migration: the legacy
identity is absent and the separate protected workflow still requires its
exact reviewed inputs and its own early preflight before it can stage a review
branch.
