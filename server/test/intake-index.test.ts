import { describe, expect, it } from "vitest";

import {
  intakeClaim,
  intakeClaimPath,
  intakeOwner,
  intakeOwnerPath,
  submissionIsActive,
} from "../src/intake-index";
import type { SubmissionInput } from "../src/api-contract";
import type { SubmissionView } from "../src/submission-view";

const submission: SubmissionInput = {
  problem_id: "two_plus_two",
  problem_group: "formalization-evaluation",
  statement_revision: 2,
  declared_model: "Example Model",
  source_repository: "Alice/Proofs",
  source_commit: "a".repeat(40),
  source_visibility: "private",
  publication_choice: "scheduled",
  production_metadata: {},
};
const submissionId = "019debcf-cb48-7000-8000-000000000001";

function pending(acceptedAt: string): Extract<SubmissionView, { schema_version: 2 }> {
  return {
    schema_version: 2,
    submission_id: submissionId,
    owner_login: "alice",
    received_event_id: submissionId,
    mutation_event_id: "019debcf-cb48-7000-8000-000000000002",
    metadata_event_id: "019debcf-cb48-7000-8000-000000000002",
    publication_event_id: null,
    accepted_at: acceptedAt,
    submission,
    production_metadata: {},
    publication_choice: "scheduled",
    archive: { status: "pending" },
    evaluation: { status: "pending" },
    result_id: null,
    result_event_id: null,
    dispatch: {
      status: "pending",
      attempts: 0,
      requested_at: acceptedAt,
      updated_at: acceptedAt,
      workflow_ref: `lean-eval-dispatch/${"b".repeat(40)}`,
      last_error_code: null,
    },
  };
}

describe("intake indexes", () => {
  it("matches the language-neutral State identity vectors", async () => {
    const claim = await intakeClaim("Alice", submission, submissionId);
    expect(claim.claim_id).toBe("ic1_65a5d731752d0744a5fa33fee491d6f5fd5b1b9f3c94ad0ddee571f3a9bae058");
    expect(claim.source_repository).toBe("alice/proofs");
    expect(intakeClaimPath(claim.claim_id)).toBe(`views/intake-claims/65/${claim.claim_id}.json`);

    const owner = await intakeOwner("Alice", [submissionId, submissionId]);
    expect(owner.owner_id).toBe("io1_3566570c3247cd1188da93db4e7e1ecd7de9b859445dbceda249e16c9f1565d1");
    expect(owner.submission_ids).toEqual([submissionId]);
    expect(intakeOwnerPath(owner.owner_id)).toBe(`views/intake-owners/35/${owner.owner_id}.json`);
  });

  it("counts only live incomplete work and expires abandoned work after 24 hours", () => {
    const now = Date.parse("2026-09-13T12:00:00.000Z");
    expect(submissionIsActive(pending("2026-09-13T11:00:00.000Z"), now)).toBe(true);
    expect(submissionIsActive(pending("2026-09-12T11:59:59.999Z"), now)).toBe(false);
    expect(submissionIsActive({
      ...pending("2026-09-13T11:00:00.000Z"),
      evaluation: {
        status: "rejected",
        event_id: "019debcf-cb48-7000-8000-000000000003",
        occurred_at: "2026-09-13T11:30:00.000Z",
        attempt: 1,
        benchmark_repository: "leanprover/lean-eval",
        benchmark_commit: "c".repeat(40),
        toolchain: "leanprover/lean4:v4.32.0",
        reason_code: "proof_rejected",
      },
    }, now)).toBe(false);
  });
});
