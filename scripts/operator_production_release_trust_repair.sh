#!/usr/bin/env bash
set -euo pipefail

# Run only in the authenticated AWS CloudShell/root session for account
# 161072922960. This launcher downloads the immutable reviewed procedure,
# checks the document and extracted Bash bytes, and executes all three Bash
# blocks in one process so its cleanup trap and change-set ownership remain
# intact.

readonly SOURCE_COMMIT=0bf88bf0e29c6f2abe8fe07aed1ab803ce98f2ec
readonly DOCUMENT_SHA256=f8ee117f7d96316718433a9ca409f992071b07e69d8e190887941a29636d629e
readonly SCRIPT_SHA256=bb78175c3560e0dd0e151ec4ff427ea86731d14ae685f106138297124abc6bfc
readonly SOURCE_URL="https://raw.githubusercontent.com/leanprover/lean-eval-submissions/${SOURCE_COMMIT}/docs/aws-release-production-trust-repair.md"

operator_dir="$(mktemp -d)"
chmod 700 "$operator_dir"
document="$operator_dir/procedure.md"
script="$operator_dir/procedure.sh"

cleanup_launcher() {
  status=$?
  trap - EXIT
  if [[ -d "$operator_dir" && ! -L "$operator_dir" ]]; then
    chmod -R u+rwX "$operator_dir" 2>/dev/null || true
    rm -rf -- "$operator_dir"
  fi
  exit "$status"
}
trap cleanup_launcher EXIT

curl --fail --location --proto '=https' --tlsv1.2 \
  --output "$document" "$SOURCE_URL"
test "$(sha256sum "$document" | cut -d ' ' -f 1)" = "$DOCUMENT_SHA256"

awk '
  $0 == "```bash" { inside = 1; next }
  inside && $0 == "```" { inside = 0; print ""; next }
  inside { print }
' "$document" > "$script"
chmod 700 "$script"
test "$(sha256sum "$script" | cut -d ' ' -f 1)" = "$SCRIPT_SHA256"

bash "$script"
