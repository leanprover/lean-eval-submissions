# Production archive-migration infrastructure

This one-time operator path provisions the already-reviewed, production-only
archive migration role and the v2 unwrap support needed for historical archive
file keys. It preserves the existing v1 archive path and the repaired release
OIDC trust.

The operation is deliberately narrower than the migration itself. It does not
install `LEGACY_ARCHIVE_IDENTITY`, dispatch the migration or replay workflows,
invoke the unwrap Lambda, write Git or State, or enable intake, replay, or
publication. Its final GitHub mutation only replaces the unusable ordinary
Wrap-role ARN in `archive-migration-production` with the new dedicated role
ARN.

Run `scripts/operator_production_archive_migration_infra.sh` from an
authenticated AWS CloudShell root session for account `161072922960`. Execute
only an immutable raw GitHub URL whose commit and SHA-256 have been supplied by
the maintainer preparing the operation.

The script fails closed unless all of these facts hold:

- production is still the exact pre-migration stack and staging is unchanged;
- the repaired ID-bearing `release-production` trust is present;
- the live stack still has the legacy `SubmissionGitHubRepository` and
  `ReleaseGitHubRepository` parameter names with their exact current subject
  values; the update replaces those names with the reviewed subject-prefix
  parameters without changing the effective subjects;
- the migration environment is protected and has no legacy identity;
- the source template and two Lambda source files match commit
  `c1013bee0b5b2f57956501e0258d27dc30413d2b` byte for byte;
- the CloudFormation change set contains only the migration role, the unwrap
  role's v2 policy addition, and the generated Lambda function/version/alias
  update; and
- every existing output, v1 role policy, role trust, function setting, and
  staging timestamp survives the update.

On success the final line is:

```text
PRODUCTION_ARCHIVE_MIGRATION_INFRA_OK
```

Provisioning does not authorize migration. The separate protected workflow
still requires its exact reviewed inputs, the absent legacy identity, and its
own early preflight before it can stage a review branch.
