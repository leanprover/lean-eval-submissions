# Manual compute coverage and backfill planning

These operator tools account for historical accepted submissions separately from
acceptance instrumentation and submitter metadata editing. They have no scheduled
workflow, callback credential, source fetch, or automatic replay dispatch.

Produce a coverage report from the public Results store and a public State
projection (v6, or v7 when available):

```sh
python scripts/compute_reporting_coverage.py \
  --results-root /path/to/lean-eval-submissions \
  --public-state /path/to/public-state.json \
  --output /tmp/compute-coverage.json
```

Rows distinguish measured, pending, failed, source-unavailable and unmeasured
results. Measurements and terminal reasons already recorded by the existing
historical replay controllers are reused. A missing instruction counter does
not make a complete wall-clock measurement unavailable.

Issue submissions continue through the existing historical inventory/delta
planners and public/private replay controllers. For server results, prepare a
create-only event packet using the State repository's privately materialized
`domain.json` and a reviewed map keyed by the original benchmark commit:

```sh
python scripts/plan_compute_backfill.py \
  --coverage /tmp/compute-coverage.json \
  --domain /path/to/domain.json \
  --profiles /path/to/reviewed-profiles.json \
  --occurred-at 2026-10-28T12:00:00.000Z \
  --output /tmp/proposed-compute-backfill.json
```

Each profile entry supplies `toolchain`, `qualified: true`,
`execution_profile_digest` and `measurement_config_digest`. This map records
qualification already established through the existing replay procedure; it
cannot qualify an image. Missing or mismatched exact toolchain profiles are
reported as blocked. Existing replay tasks are not duplicated, and the same
frozen inputs and timestamp produce the same event identities.

Validate the proposed events with State before appending through the existing
operator procedure. The planner never appends events, decrypts source, dispatches
proofs, changes replay gates, or substitutes a newer benchmark. Results and
coverage reports are snapshots; regenerate coverage after historical replay to
account for the remaining backlog.
