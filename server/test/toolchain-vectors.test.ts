import { describe, expect, it } from "vitest";

import vectors from "../../schemas/toolchain-vectors-v1.json";
import { decodeEvaluationCompletion } from "../src/api-contract";
import { validateStateEvent } from "../src/state-event";
import { decodeSubmissionView } from "../src/submission-view";

const SUBMISSION_ID = "019debcf-cb48-7000-8000-000000000001";

function completion(toolchain: string): unknown {
  return {
    schema_version: 1,
    submission_id: SUBMISSION_ID,
    attempt: 1,
    occurred_at: "2026-10-07T10:00:00.000Z",
    benchmark_repository: "leanprover/lean-eval",
    benchmark_commit: "c".repeat(40),
    toolchain,
    outcome: { status: "accepted", evaluator_version: "d".repeat(40) },
  };
}

function startedEvent(toolchain: string): unknown {
  return {
    schema_version: 1,
    event_id: "019debcf-cb48-7000-8000-000000000006",
    event_type: "evaluation.started",
    occurred_at: "2026-10-07T10:00:00.000Z",
    subject_id: SUBMISSION_ID,
    causation_event_id: "019debcf-cb48-7000-8000-000000000005",
    actor: { kind: "system" },
    payload: {
      attempt: 1,
      benchmark_repository: "leanprover/lean-eval",
      benchmark_commit: "c".repeat(40),
      toolchain,
    },
  };
}

function view(toolchain: string): unknown {
  return {
    schema_version: 2,
    submission_id: SUBMISSION_ID,
    owner_login: "alice",
    received_event_id: SUBMISSION_ID,
    mutation_event_id: SUBMISSION_ID,
    metadata_event_id: SUBMISSION_ID,
    publication_event_id: null,
    accepted_at: "2026-10-07T09:00:00.000Z",
    submission: {
      problem_id: "two_plus_two",
      problem_group: "formalization-evaluation",
      statement_revision: 1,
      declared_model: "Example Model",
      source_repository: "alice/proofs",
      source_commit: "a".repeat(40),
      source_visibility: "public",
      publication_choice: "scheduled",
      production_metadata: {},
    },
    production_metadata: {},
    publication_choice: "scheduled",
    archive: { status: "pending" },
    evaluation: {
      status: "accepted",
      event_id: "019debcf-cb48-7000-8000-000000000005",
      occurred_at: "2026-10-07T10:00:00.000Z",
      attempt: 1,
      benchmark_repository: "leanprover/lean-eval",
      benchmark_commit: "c".repeat(40),
      toolchain,
      evaluator_version: "d".repeat(40),
    },
    result_id: null,
    result_event_id: null,
    dispatch: {
      status: "succeeded",
      attempts: 1,
      requested_at: "2026-10-07T09:00:00.000Z",
      updated_at: "2026-10-07T09:00:01.000Z",
      workflow_ref: `lean-eval-dispatch/${"e".repeat(40)}`,
      last_error_code: null,
    },
  };
}

describe("shared toolchain vectors", () => {
  it("are well formed", () => {
    expect(vectors.schema_version).toBe(1);
    expect(vectors.accepted.length).toBeGreaterThan(0);
    expect(vectors.rejected.length).toBeGreaterThan(0);
  });

  it.each(vectors.accepted)("every decoder accepts %s", (toolchain) => {
    expect(decodeEvaluationCompletion(completion(toolchain)).toolchain).toBe(toolchain);
    expect(() => validateStateEvent(startedEvent(toolchain))).not.toThrow();
    const decoded = decodeSubmissionView(view(toolchain));
    expect(decoded.evaluation.status === "pending" ? null : decoded.evaluation.toolchain).toBe(toolchain);
  });

  it.each(vectors.rejected)("every decoder rejects %j", (toolchain) => {
    expect(() => decodeEvaluationCompletion(completion(toolchain))).toThrow();
    expect(() => validateStateEvent(startedEvent(toolchain))).toThrow();
    expect(() => decodeSubmissionView(view(toolchain))).toThrow();
  });
});
