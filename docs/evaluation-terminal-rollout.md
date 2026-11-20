# Evaluation terminal timestamp rollout

Evaluation callback version 2 carries the original evaluation attempt's start
and terminal times. The receiver accepts versions 1 and 2; version 1 retains its
existing event identities and timestamp calculation. Identical retries also work
after canonical State serialization reorders object keys, and terminal updates
explicitly preserve the consecutive retryable-failure rule. Version 2 binds the complete callback
identity to its event IDs, uses the terminal time for the evaluation outcome,
and rejects changed retries with HTTP 409.

Deploy this receiver before changing the evaluation workflow or completion
producer. This revision leaves both workflows and the Python completion
producer unchanged, so submissions dispatched during deployment still send
version 1. No existing State event or release schedule is rewritten.

## Receiver deployment

1. Run the normal source and Worker checks. Verify the exact callback digest in
   `.audit/cloudflare-rollback-qualification-v1.json` and both supported State
   contracts. Keep historical admission and publication paused.
2. Use the ordinary coupled staging and production deployment. Require the
   exact deployed source on every component, the authenticated protected-State
   write proof, and the normal intake lease/smoke/finalization checks. Existing
   callbacks must continue to be accepted during the transition.
3. Retain the actual deployed protected-main merge commit, its immutable
   `lean-eval-dispatch/<commit>` tag, the exact callback digest, and the
   corresponding intake, broker and disabled replay version IDs as the rollback
   unit for the later producer change. A local feature-branch commit is not a
   deployed rollback unit. Validate that unit through the existing rollback preparation checks,
   including its exact source, callback digest, State roots, bindings and
   disabled intake/replay configuration. Record the evidence in the operation
   packet before enabling a version 2 producer.

## Producer deployment

Only after the receiver deployment and compatible rollback unit are proved,
merge the separate workflow/producer change that freezes the original runner's
terminal receipt and emits version 2. Keep the receiver implementation identical
to the qualified first stage. Follow the normal coupled deployment checks for
the resulting source revision and observe an actual submission through its
original receipt, completion callback, canonical events and release schedule.

Before enabling a version 2 producer, enforce the compatible callback digest in
the production rollback admission path and prove the retained receiver unit.
After any version 2 workflow has been dispatched, use that qualified receiver
unit or a later independently qualified compatible unit for emergency rollback.
An older version 1-only Worker cannot receive those in-flight callbacks, even
with intake disabled and even when it supports the same State contract.
Disabling intake prevents new submissions; it does not remove existing callback
obligations. Preserve original receipts for callback-only repair if needed.

Receiver support alone does not repair the old acceptance clock. Keep
publication paused until the separate safeguard for existing backdated release
schedules is qualified. The two-calendar-month policy and canonical historical
events remain unchanged.
