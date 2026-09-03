# Final historical delta

This is the one-shot contract for Results accepted after the retained
1,301-Result baseline. It does not extend or modify the retained-baseline
packet, plans, matrices, or State workflow.

## Cutoff inputs

At the announced issue-intake cutoff, after all pending accepted issue
submissions have settled, select the exact protected submissions commit whose
`results/` tree is the final corpus. From that commit:

1. build and review the complete historical inventory;
2. reconcile it against the retained inventory at
   `evidence/historical-replay/inventories/bb405fbabe084e106ad5500b455a05ba1e1d54175d1964db3aebcc3b6ea3fce3.json`;
3. classify every public delta source as either addressable at its exact commit
   or reviewed permanently unavailable;
4. build the complete private crosswalk against the exact current audit tree;
   and
5. commit each canonical input at its content-addressed path.

The accepted paths are:

- `evidence/historical-replay/inventories/<sha256>.json`;
- `evidence/historical-replay/deltas/<sha256>.json`;
- `evidence/historical-replay/public-source-decisions/<sha256>.json`; and
- `evidence/historical-replay/private-crosswalks/<sha256>.json`.

The packet commit may be a documentation/data-only descendant of the cutoff
commit, but its `results/` tree must be byte-identical to the cutoff commit.

## Preparation packet

Dispatch `Prepare final historical delta packet` from the immutable
`lean-eval-dispatch/<packet-commit>` tag with the exact four input digests and
public, private, and total delta counts. The workflow:

- requires that tag to equal current protected `main`;
- reads only committed content-addressed inputs;
- independently rederives the append-only delta;
- binds every delta entry to the exact canonical Result;
- requires complete reviewed public-source decisions;
- requires the complete current private crosswalk and rejects ambiguous or
  conflicting delta entries;
- omits private source locators;
- reports the exact lane/benchmark image set and unique legacy archives still
  requiring migration; and
- emits the packet twice and requires byte identity.

The artifact kind is `historical_final_delta_preparation_packet`; its schema is
`schemas/historical-final-delta-preparation-v1.schema.json`. It is deliberately
marked `blocked_pending_exact_profiles_and_state_append`. The workflow has no
State, audit, archive, replay, credential, or publication authority.

## Activation boundary

The preparation artifact is not permission to mutate anything. Before the
final delta can enter State, a later compact execution binding must add:

- immutable qualified official-kernel/nanoda profiles for every reported image
  requirement;
- the exact post-rewrap audit commit and tree, with every replayable private
  entry at archive schema version 3;
- one exact current production State parent and create-only event set;
- independent validation that every delta Result is represented exactly once
  by a replay task or reviewed unavailable event; and
- the existing two-phase State review/promotion and bounded two-lane replay
  controls.

That execution binding must use separate final-delta files and a separate
review branch. It must not reuse the retained-baseline fixed counts, matrices,
promotion binding, or review branch. Delete the temporary final-delta workflow
after all delta Results have terminal dispositions and the migration identity
has been retired.
