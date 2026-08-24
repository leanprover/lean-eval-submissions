import { describe, expect, it } from "vitest";

import {
  decodeStagingAmendmentCanaryRequest,
  STAGING_CANARY_RESULTS_COMMIT,
  STAGING_CANARY_STATE_REPOSITORY,
  STAGING_CANARY_TARGETS,
} from "../src/staging-amendment-canary";

const EVENT = "01993a80-1234-7abc-8def-0123456789ab";

function request(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: 1,
    operation: "request_apply",
    deployed_commit: "a".repeat(40),
    expected_state_commit: "b".repeat(40),
    event_id: EVENT,
    occurred_at: "2026-09-12T00:00:00.000Z",
    expected_request_event_id: null,
    ...overrides,
  };
}

describe("staging amendment canary contract", () => {
  it("binds only the reviewed staging identities", () => {
    expect(STAGING_CANARY_STATE_REPOSITORY).toBe("leanprover/lean-eval-state-staging");
    expect(STAGING_CANARY_RESULTS_COMMIT).toBe("972178d59e2b3c5300baa728a1356f0d49dafb87");
    expect(STAGING_CANARY_TARGETS.apply.resultId).toBe(
      "r2_99df81809318fd2673d82da042b451f77b55606c6b506beb4526828ee1e7079e",
    );
    expect(STAGING_CANARY_TARGETS.reject.resultId).toBe(
      "r2_3f28ce10fd9bad352dc29394254ec7c414b57269757c3488cd108bd544186423",
    );
    expect(STAGING_CANARY_TARGETS.apply.candidateIdentityId).not.toBe(
      STAGING_CANARY_TARGETS.reject.candidateIdentityId,
    );
  });

  it("accepts closed request and decision forms", () => {
    expect(decodeStagingAmendmentCanaryRequest(request()).operation).toBe("request_apply");
    expect(decodeStagingAmendmentCanaryRequest(request({
      operation: "apply",
      expected_request_event_id: "01993a80-1235-7abc-8def-0123456789ab",
    })).operation).toBe("apply");
  });

  it("rejects extra fields and inconsistent request linkage", () => {
    expect(() => decodeStagingAmendmentCanaryRequest(request({ extra: false }))).toThrow();
    expect(() => decodeStagingAmendmentCanaryRequest(request({
      operation: "reject",
      expected_request_event_id: null,
    }))).toThrow();
    expect(() => decodeStagingAmendmentCanaryRequest(request({
      operation: "request_reject",
      expected_request_event_id: EVENT,
    }))).toThrow();
  });
});
