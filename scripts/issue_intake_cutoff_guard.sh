#!/usr/bin/env bash
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${ISSUE_INTAKE_CUTOFF+x}"
: "${REPOSITORY:?REPOSITORY is required}"
: "${RUN_ATTEMPT:?RUN_ATTEMPT is required}"
: "${RUN_ID:?RUN_ID is required}"

if [[ ! "$RUN_ID" =~ ^[1-9][0-9]*$ ]] || [[ ! "$RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]]; then
  echo "invalid workflow run identity" >&2
  exit 1
fi

if [ -z "$ISSUE_INTAKE_CUTOFF" ]; then
  classification=$'allowed=true\nreason=cutoff_absent'
else
  run=$(timeout 30s gh api \
    --header 'Accept: application/vnd.github+json' \
    "repos/$REPOSITORY/actions/runs/$RUN_ID")
  jq -e \
    --arg repository "$REPOSITORY" \
    --argjson run_attempt "$RUN_ATTEMPT" \
    --argjson run_id "$RUN_ID" '
      .id == $run_id and
      .repository.full_name == $repository and
      .event == "issues" and
      .run_attempt == $run_attempt and
      (.run_started_at | type == "string") and
      (.created_at | type == "string")
    ' <<< "$run" >/dev/null
  run_created_at=$(jq -er .created_at <<< "$run")
  run_started_at=$(jq -er .run_started_at <<< "$run")
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  classification=$(python "$script_dir/classify_issue_intake_cutoff.py" \
    --run-attempt "$RUN_ATTEMPT" \
    --run-created-at "$run_created_at" \
    --run-started-at "$run_started_at" \
    --cutoff "$ISSUE_INTAKE_CUTOFF")
fi

case "$classification" in
  $'allowed=true\nreason=cutoff_absent' | $'allowed=true\nreason=before_cutoff')
    printf '%s\n' "$classification"
    ;;
  $'allowed=false\nreason=at_or_after_cutoff')
    printf '%s\n' "$classification"
    if [ "${ISSUE_INTAKE_REQUIRE_ALLOWED:-true}" = "true" ]; then
      echo "issue intake attempt is at or after the selected cutoff" >&2
      exit 1
    fi
    ;;
  *)
    echo "cutoff classifier returned an invalid response" >&2
    exit 1
    ;;
esac
