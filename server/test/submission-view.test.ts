import { describe, expect, it } from "vitest";

import { decodeSubmissionView, latestLifecycleEventId } from "../src/submission-view";

const SUBMISSION_ID = "019debcf-cb48-7000-8000-000000000001";
const CONFLICT_EVENT_ID = "019debcf-cb48-7000-8000-000000000004";

const CONFLICT_VIEW = {
  schema_version: 3,
  submission_id: SUBMISSION_ID,
  owner_login: "alice",
  received_event_id: SUBMISSION_ID,
  mutation_event_id: "019debcf-cb48-7000-8000-000000000002",
  metadata_event_id: "019debcf-cb48-7000-8000-000000000002",
  publication_event_id: null,
  accepted_at: "2026-01-30T00:00:00.000Z",
  submission: {
    problem_id: "two_plus_two",
    problem_group: "formalization-evaluation",
    statement_revision: 2,
    declared_model: "Example Model",
    source_repository: "alice/proofs",
    source_commit: "a".repeat(40),
    source_visibility: "private",
    publication_choice: "scheduled",
    production_metadata: { web_access: false, input_tokens: 123 },
  },
  production_metadata: { web_access: false, input_tokens: 123 },
  publication_choice: "scheduled",
  archive: {
    status: "completed",
    event_id: "019debcf-cb48-7000-8000-000000000003",
    occurred_at: "2026-01-30T01:00:00.000Z",
    archive_repository: "leanprover/lean-eval-audit",
    archive_commit: "d".repeat(40),
    archive_path: `archives/${SUBMISSION_ID.replaceAll("-", "").slice(0, 2)}/${SUBMISSION_ID}.tar.age`,
    archive_ciphertext_sha256: "e".repeat(64),
    encrypted: true,
  },
  evaluation: {
    status: "accepted",
    event_id: "019debcf-cb48-7000-8000-000000000005",
    occurred_at: "2026-01-31T12:34:56.789Z",
    attempt: 1,
    benchmark_repository: "leanprover/lean-eval",
    benchmark_commit: "c".repeat(40),
    toolchain: "leanprover/lean4:v4.32.0",
    evaluator_version: "b".repeat(40),
  },
  result_id: null,
  result_event_id: null,
  result_disposition: {
    status: "identity_conflict",
    event_id: CONFLICT_EVENT_ID,
    occurred_at: "2026-02-01T00:00:00.000Z",
    result_id: `r2_${"f".repeat(64)}`,
    authority_event_id: "019debcf-cb48-7000-8000-000000000099",
    existing_kind: "recorded",
    reason_code: "duplicate_result_identity",
  },
  dispatch: {
    status: "succeeded",
    attempts: 1,
    requested_at: "2026-01-30T00:00:00.000Z",
    updated_at: "2026-01-30T00:01:00.000Z",
    workflow_ref: `lean-eval-dispatch/${"b".repeat(40)}`,
    last_error_code: null,
  },
} as const;

describe("submission result identity conflict view", () => {
  it("decodes a terminal schema-v3 disposition and uses it as the lifecycle head", () => {
    const decoded = decodeSubmissionView(CONFLICT_VIEW);
    expect(decoded.schema_version).toBe(3);
    if (decoded.schema_version !== 3) throw new Error("expected schema version 3");
    expect(decoded.result_id).toBeNull();
    expect(decoded.result_event_id).toBeNull();
    expect(latestLifecycleEventId(decoded)).toBe(CONFLICT_EVENT_ID);
  });

  it("rejects result authority, owner semantics, and open-ended conflict fields", () => {
    expect(() => decodeSubmissionView({
      ...CONFLICT_VIEW,
      result_id: CONFLICT_VIEW.result_disposition.result_id,
    })).toThrow(/cannot name a recorded result event/u);
    expect(() => decodeSubmissionView({
      ...CONFLICT_VIEW,
      result_disposition: { ...CONFLICT_VIEW.result_disposition, owner_login: "alice" },
    })).toThrow(/fields/u);
    expect(() => decodeSubmissionView({
      ...CONFLICT_VIEW,
      result_disposition: { ...CONFLICT_VIEW.result_disposition, reason_code: "retry_duplicate" },
    })).toThrow(/invalid/u);
  });
});
