import { describe, expect, it } from "vitest";

import {
  decodeResultAmendmentView,
  initialResultAmendmentView,
  requestedRetractionView,
  resultAmendmentPath,
} from "../src/result-amendment";

const RESULT_ID = `r2_${"1".repeat(64)}`;
const AUTHORITY = "0198abcd-2222-7000-8000-000000000001";
const REQUEST = "0198abcd-2222-7000-8000-000000000002";

describe("targeted result amendment contract", () => {
  it("constructs the exact initial and pending owner-retraction views", () => {
    const initial = initialResultAmendmentView({
      resultId: RESULT_ID,
      ownerLogin: "alice",
      declaredModel: "Example Model",
      authorityEventId: AUTHORITY,
      mutationEventId: AUTHORITY,
      problemId: "two_plus_two",
      statementRevision: 1,
    });
    expect(resultAmendmentPath(RESULT_ID)).toBe(
      `views/result-amendments/11/${RESULT_ID}.json`,
    );
    expect(requestedRetractionView(
      initial,
      REQUEST,
      "2026-08-20T06:07:09.000Z",
      "owner_requested_withdrawal",
    )).toEqual({
      ...initial,
      mutation_event_id: REQUEST,
      retraction: {
        revision: 1,
        status: "pending",
        request_event_id: REQUEST,
        requested_at: "2026-08-20T06:07:09.000Z",
        decision_event_id: null,
        decided_at: null,
        retraction_event_id: null,
        retracted_at: null,
        reviewer_login: null,
        reason_code: "owner_requested_withdrawal",
        release_disposition: null,
        overridden: false,
      },
    });
  });

  it("rejects forged fields and inconsistent public lifecycle summaries", () => {
    const initial = initialResultAmendmentView({
      resultId: RESULT_ID,
      ownerLogin: "alice",
      declaredModel: "Example Model",
      authorityEventId: AUTHORITY,
      mutationEventId: AUTHORITY,
      problemId: "two_plus_two",
      statementRevision: 1,
    });
    expect(() => decodeResultAmendmentView({ ...initial, source_repository: "private/repo" }))
      .toThrow(/fields/u);
    expect(() => decodeResultAmendmentView({
      ...initial,
      retraction: {
        revision: 1,
        status: "approved",
        request_event_id: REQUEST,
        requested_at: "2026-08-20T06:07:09.000Z",
        decision_event_id: null,
        decided_at: null,
        retraction_event_id: null,
        retracted_at: null,
        reviewer_login: null,
        reason_code: "owner_requested_withdrawal",
        release_disposition: null,
        overridden: false,
      },
    })).toThrow(/decided/u);
    expect(() => decodeResultAmendmentView({
      ...initial,
      retraction: {
        revision: 1,
        status: "pending",
        request_event_id: REQUEST,
        requested_at: "2026-08-20T06:07:09.000Z",
        decision_event_id: REQUEST,
        decided_at: null,
        retraction_event_id: null,
        retracted_at: null,
        reviewer_login: null,
        reason_code: "owner_requested_withdrawal",
        release_disposition: null,
        overridden: false,
      },
    })).toThrow(/pending/u);
    expect(() => decodeResultAmendmentView({
      ...initial,
      leaderboard_eligible: true,
      retraction: {
        revision: 1,
        status: "retracted",
        request_event_id: REQUEST,
        requested_at: "2026-08-20T06:07:09.000Z",
        decision_event_id: "0198abcd-2222-7000-8000-000000000003",
        decided_at: "2026-08-20T06:07:10.000Z",
        retraction_event_id: "0198abcd-2222-7000-8000-000000000004",
        retracted_at: "2026-08-20T06:07:11.000Z",
        reviewer_login: "maintainer",
        reason_code: "owner_requested_withdrawal",
        release_disposition: "not_published",
        overridden: false,
      },
    })).toThrow(/leaderboard/u);
  });

  it("accepts exact applied-repair, maintainer-override, and terminal summaries", () => {
    const initial = initialResultAmendmentView({
      resultId: RESULT_ID,
      ownerLogin: "alice",
      declaredModel: "Example Model",
      authorityEventId: AUTHORITY,
      mutationEventId: AUTHORITY,
      problemId: "two_plus_two",
      statementRevision: 1,
    });
    const decision = "0198abcd-2222-7000-8000-000000000003";
    const repair = {
      revision: 1,
      status: "applied" as const,
      request_event_id: REQUEST,
      requested_at: "2026-08-20T06:07:09.000Z",
      corrected_problem_id: "two_plus_three",
      corrected_statement_revision: 2,
      decision_event_id: decision,
      decided_at: "2026-08-20T06:07:10.000Z",
      reviewer_login: "maintainer",
      reason_code: null,
      comparator_evidence: {
        repository: "leanprover/lean-eval-submissions",
        commit: "a".repeat(40),
        path: "results/alice.json",
        blob_oid: "b".repeat(40),
        blob_sha256: "c".repeat(64),
        record_sha256: "d".repeat(64),
        binding_sha256: "e".repeat(64),
        verification_method: "github_commit_blob_v1" as const,
        evidence_result_id: RESULT_ID,
        evidence_owner_login: "alice",
        evidence_declared_model: "Example Model",
        evidence_base_problem_group: "formalization-evaluation" as const,
        evidence_base_problem_id: "two_plus_two",
        evidence_base_statement_revision: 1,
        evidence_base_challenge_id: `ch1_${"f".repeat(64)}`,
        evidence_corrected_problem_group: "formalization-evaluation" as const,
        evidence_corrected_problem_id: "two_plus_three",
        evidence_corrected_statement_revision: 2,
        evidence_corrected_challenge_id: `ch1_${"1".repeat(64)}`,
      },
    };
    expect(decodeResultAmendmentView({
      ...initial,
      mutation_event_id: decision,
      effective_problem_id: "two_plus_three",
      effective_statement_revision: 2,
      problem_repair: repair,
      applied_problem_repair: repair,
    }).effective_problem_id).toBe("two_plus_three");

    const override = "0198abcd-2222-7000-8000-000000000004";
    const overridden = decodeResultAmendmentView({
      ...initial,
      mutation_event_id: override,
      retraction: {
        revision: 1,
        status: "approved",
        request_event_id: null,
        requested_at: null,
        decision_event_id: override,
        decided_at: "2026-08-20T06:07:11.000Z",
        retraction_event_id: null,
        retracted_at: null,
        reviewer_login: "maintainer",
        reason_code: "owner_account_unavailable",
        release_disposition: null,
        overridden: true,
      },
    });
    const terminal = "0198abcd-2222-7000-8000-000000000005";
    expect(decodeResultAmendmentView({
      ...overridden,
      mutation_event_id: terminal,
      leaderboard_eligible: false,
      retraction: {
        ...overridden.retraction,
        status: "retracted",
        retraction_event_id: terminal,
        retracted_at: "2026-08-20T06:07:12.000Z",
        release_disposition: "not_published",
      },
    }).retraction?.status).toBe("retracted");
  });
});
