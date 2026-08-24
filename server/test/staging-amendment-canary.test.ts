import { describe, expect, it } from "vitest";

import {
  decodeStagingAmendmentCanaryRequest,
  STAGING_CANARY_INTENTS,
  STAGING_CANARY_RESULTS_COMMIT,
  STAGING_CANARY_STATE_REPOSITORY,
  STAGING_CANARY_TARGETS,
} from "../src/staging-amendment-canary";

function request(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: 1,
    operation: "request_apply",
    deployed_commit: "a".repeat(40),
    expected_state_commit: "b".repeat(40),
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
    })).operation).toBe("apply");
  });

  it("rejects caller-supplied event material and other extra fields", () => {
    expect(() => decodeStagingAmendmentCanaryRequest(request({ extra: false }))).toThrow();
    expect(() => decodeStagingAmendmentCanaryRequest(request({
      event_id: STAGING_CANARY_INTENTS.reject.eventId,
    }))).toThrow();
    expect(() => decodeStagingAmendmentCanaryRequest(request({
      occurred_at: STAGING_CANARY_INTENTS.reject.occurredAt,
    }))).toThrow();
  });

  it("binds ordered UUIDv7 identities to their exact millisecond timestamps", () => {
    const intents = Object.values(STAGING_CANARY_INTENTS);
    expect(intents.map(({ eventId }) => eventId)).toEqual(
      [...intents].map(({ eventId }) => eventId).sort(),
    );
    for (const intent of intents) {
      expect(Number.parseInt(intent.eventId.replaceAll("-", "").slice(0, 12), 16)).toBe(
        Date.parse(intent.occurredAt),
      );
    }
    expect(STAGING_CANARY_INTENTS.apply.expectedRequestEventId).toBe(
      STAGING_CANARY_INTENTS.request_apply.eventId,
    );
    expect(STAGING_CANARY_INTENTS.reject.expectedRequestEventId).toBe(
      STAGING_CANARY_INTENTS.request_reject.eventId,
    );
  });
});
