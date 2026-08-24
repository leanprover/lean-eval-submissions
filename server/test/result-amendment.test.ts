import { describe, expect, it } from "vitest";

import {
  challengeId,
  comparatorBindingSha256,
  decidedProblemRepairView,
  decidedRetractionView,
  decodeResultAmendmentView,
  initialResultAmendmentView,
  overriddenRetractionView,
  requestedProblemRepairView,
  requestedRetractionView,
  resultAmendmentPath,
  terminalRetractionView,
} from "../src/result-amendment";

const RESULT_ID = `r2_${"1".repeat(64)}`;
const AUTHORITY = "0198abcd-2222-7000-8000-000000000001";
const REQUEST = "0198abcd-2222-7000-8000-000000000002";

describe("targeted result amendment contract", () => {
  it("matches the protected State challenge and comparator-binding vectors", async () => {
    await expect(challengeId("formalization-evaluation", "two_plus_two", 1)).resolves.toBe(
      "ch1_6b96093e822f811a31d09ed4d35b44f3135e5170cf1ea84a59f87eb09aa20cf7",
    );
    await expect(comparatorBindingSha256({
      repository: "leanprover/lean-eval-submissions",
      commit: "a".repeat(40),
      path: "results/kim-em.json",
      blob_oid: "c".repeat(40),
      blob_sha256: "d".repeat(64),
      record_sha256: "b".repeat(64),
      verification_method: "github_commit_blob_v1",
      evidence_result_id: "r2_80f02f892fb0b90474675aa0b572252a8758faf74b95400521e9da724583931f",
      evidence_owner_login: "kim-em",
      evidence_declared_model: "Example Model",
      evidence_base_problem_group: "formalization-evaluation",
      evidence_base_problem_id: "two_plus_two",
      evidence_base_statement_revision: 1,
      evidence_base_challenge_id: "ch1_6b96093e822f811a31d09ed4d35b44f3135e5170cf1ea84a59f87eb09aa20cf7",
      evidence_corrected_problem_group: "formalization-evaluation",
      evidence_corrected_problem_id: "three_plus_three",
      evidence_corrected_statement_revision: 2,
      evidence_corrected_challenge_id: "ch1_2ee792b9940091b30b893826d3d60cd36378bbd2aabd5743a18ab2bc3d46c5fb",
    })).resolves.toBe("8ff3254de3ebd9a7991f866b5a7e15877bb89e54739dbc1a166e53634ef7135d");
  });

  it("constructs the exact initial and pending owner-request views", () => {
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
    expect(requestedProblemRepairView(
      initial,
      REQUEST,
      "2026-08-20T06:07:09.000Z",
      "two_plus_three",
      2,
      "wrong_problem_revision",
    )).toEqual({
      ...initial,
      mutation_event_id: REQUEST,
      problem_repair: {
        revision: 1,
        status: "pending",
        request_event_id: REQUEST,
        requested_at: "2026-08-20T06:07:09.000Z",
        corrected_problem_id: "two_plus_three",
        corrected_statement_revision: 2,
        decision_event_id: null,
        decided_at: null,
        reviewer_login: null,
        reason_code: "wrong_problem_revision",
        comparator_evidence: null,
      },
    });
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
    const applied = decodeResultAmendmentView({
      ...initial,
      mutation_event_id: decision,
      effective_problem_id: "two_plus_three",
      effective_statement_revision: 2,
      problem_repair: repair,
      applied_problem_repair: repair,
    });
    const pendingRepair = requestedProblemRepairView(
      initial,
      REQUEST,
      "2026-08-20T06:07:09.000Z",
      "two_plus_three",
      2,
      "wrong_problem_revision",
    );
    expect(decidedProblemRepairView(
      pendingRepair,
      decision,
      "2026-08-20T06:07:10.000Z",
      "maintainer",
      "apply",
      null,
      repair.comparator_evidence,
    )).toEqual(applied);
    expect(decidedProblemRepairView(
      pendingRepair,
      decision,
      "2026-08-20T06:07:10.000Z",
      "maintainer",
      "reject",
      "evidence_mismatch",
      null,
    ).problem_repair?.status).toBe("rejected");

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
    expect(overriddenRetractionView(
      initial,
      override,
      "2026-08-20T06:07:11.000Z",
      "maintainer",
      "owner_account_unavailable",
    )).toEqual(overridden);
    const terminal = "0198abcd-2222-7000-8000-000000000005";
    const terminalView = decodeResultAmendmentView({
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
    });
    expect(terminalRetractionView(
      overridden,
      terminal,
      "2026-08-20T06:07:12.000Z",
      "maintainer",
      "owner_account_unavailable",
      "not_published",
    )).toEqual(terminalView);

    const pendingRetraction = requestedRetractionView(
      initial,
      REQUEST,
      "2026-08-20T06:07:09.000Z",
      "owner_requested_withdrawal",
    );
    expect(decidedRetractionView(
      pendingRetraction,
      decision,
      "2026-08-20T06:07:10.000Z",
      "maintainer",
      "reject",
      "request_not_eligible",
    ).retraction?.status).toBe("rejected");
  });
});
