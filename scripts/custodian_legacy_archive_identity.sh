#!/usr/bin/env bash
set +x
set -euo pipefail

# This helper is the only custodian-facing part of the bounded historical
# archive migration. It never accepts the identity path as an argument, writes
# the identity to a temporary file, or prints the path or identity value.

readonly REPOSITORY=leanprover/lean-eval-submissions
readonly ENVIRONMENT=archive-migration-production
readonly SECRET_NAME=LEGACY_ARCHIVE_IDENTITY
readonly EXPECTED_FINGERPRINT='SHA256:4unwBywJxfq9LsOjygB+/NRHaXdBhvxKP+a3EEpqjoE'

upload_fd=
upload_started=false
installation_verified=false

cleanup() {
  set +e
  if [[ -n "${upload_fd:-}" ]]; then
    exec {upload_fd}<&-
  fi
  unset identity_path key_metadata key_bits key_fingerprint
  if [[ "$upload_started" == true && "$installation_verified" != true ]]; then
    printf '%s\n' \
      'LEGACY_ARCHIVE_IDENTITY_INSTALLATION_STATE_INDETERMINATE; run remove before proceeding' \
      >&2
  fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

require_environment() {
  command -v gh >/dev/null || fail 'GitHub CLI is required'
  gh auth status --hostname github.com >/dev/null 2>&1 || \
    fail 'GitHub CLI is not authenticated'

  local protected
  protected="$({
    gh api "repos/$REPOSITORY/environments/$ENVIRONMENT" \
      --jq '
        .name == "archive-migration-production" and
        .deployment_branch_policy.protected_branches == true and
        .deployment_branch_policy.custom_branch_policies == false
      '
  } 2>/dev/null)" || fail 'Could not verify the protected migration environment'
  [[ "$protected" == true ]] || fail 'Migration environment protection is not exact'
}

secret_count() {
  gh api --paginate --slurp \
    "repos/$REPOSITORY/environments/$ENVIRONMENT/secrets?per_page=100" \
    --jq '[.[].secrets[].name | select(. == "LEGACY_ARCHIVE_IDENTITY")] | length' \
    2>/dev/null
}

install_identity() {
  local current_count set_status
  command -v ssh-keygen >/dev/null || fail 'ssh-keygen is required'
  require_environment

  current_count="$(secret_count)" || fail 'Could not inspect migration secrets'
  [[ "$current_count" == 0 ]] || \
    fail 'LEGACY_ARCHIVE_IDENTITY is already installed; refusing to replace it'

  printf 'Legacy identity file (input hidden): ' >&2
  IFS= read -r -s identity_path || fail 'No legacy identity was supplied'
  printf '\n' >&2
  [[ -n "$identity_path" && ! -L "$identity_path" ]] || \
    fail 'The supplied identity must be one regular, non-symlink file'

  # Open once before forgetting the path. ssh-keygen independently opens this
  # descriptor through procfs, so it verifies the same inode while the original
  # descriptor remains at offset zero for the upload.
  if ! { exec {upload_fd}<"$identity_path"; } 2>/dev/null; then
    fail 'Could not open the supplied identity'
  fi
  unset identity_path
  [[ -f "/proc/self/fd/$upload_fd" ]] || \
    fail 'The supplied identity must be one regular, non-symlink file'

  key_metadata="$({
    ssh-keygen -y -P '' -f "/proc/self/fd/$upload_fd" 2>/dev/null \
      | ssh-keygen -lf - -E sha256 2>/dev/null
  })" || fail 'The identity is not an unencrypted SSH private key'

  key_bits="${key_metadata%% *}"
  key_fingerprint="${key_metadata#* }"
  key_fingerprint="${key_fingerprint%% *}"
  if [[ "$key_bits" != 2048 || "$key_fingerprint" != "$EXPECTED_FINGERPRINT" ||
        "$key_metadata" != *' (RSA)' ]]; then
    fail 'Legacy identity fingerprint mismatch'
  fi
  unset key_metadata key_bits key_fingerprint

  set_status=0
  upload_started=true
  gh secret set "$SECRET_NAME" \
    --repo "$REPOSITORY" \
    --env "$ENVIRONMENT" \
    <&"$upload_fd" >/dev/null 2>&1 || set_status=$?
  exec {upload_fd}<&-
  upload_fd=

  current_count="$(secret_count)" || fail 'Could not verify legacy identity installation'
  [[ "$current_count" == 1 ]] || fail 'Legacy identity installation did not verify'
  [[ "$set_status" == 0 ]] || \
    fail 'GitHub reported an error although the secret now exists; stop for review'
  installation_verified=true
  printf 'LEGACY_ARCHIVE_IDENTITY_INSTALLED\n'
}

remove_identity() {
  local current_count
  require_environment
  current_count="$(secret_count)" || fail 'Could not inspect migration secrets'
  [[ "$current_count" == 1 ]] || \
    fail 'LEGACY_ARCHIVE_IDENTITY is not installed exactly once'
  gh secret delete "$SECRET_NAME" \
    --repo "$REPOSITORY" \
    --env "$ENVIRONMENT" \
    >/dev/null 2>&1 || fail 'Legacy identity removal failed'
  current_count="$(secret_count)" || fail 'Could not verify legacy identity removal'
  [[ "$current_count" == 0 ]] || fail 'Legacy identity removal did not verify'
  printf 'LEGACY_ARCHIVE_IDENTITY_REMOVED\n'
}

[[ $# == 1 ]] || fail 'Usage: custodian_legacy_archive_identity.sh install|remove'
case "$1" in
  install) install_identity ;;
  remove) remove_identity ;;
  *) fail 'Usage: custodian_legacy_archive_identity.sh install|remove' ;;
esac
