import { describe, expect, it } from "vitest";

import {
  eventIdForIdempotencyKey,
  stateEventPath,
  type StateEvent,
  validateStateEvent,
} from "../src/state-event";

const EVENT: StateEvent = {
  schema_version: 1,
  event_id: "a".repeat(64),
  event_type: "submission.received",
  occurred_at: "2026-08-20T06:07:08.000Z",
  subject_id: "submission_123",
  actor: { kind: "github", login: "kim-em" },
  payload: { problem_id: "mathlib-1" },
};

describe("State event contract", () => {
  it("places one immutable event in a date-partitioned file", () => {
    expect(stateEventPath(EVENT)).toBe(`events/2026/08/20/${"a".repeat(64)}.json`);
  });

  it("derives stable domain-separated event ids", async () => {
    const first = await eventIdForIdempotencyKey("github-request-12345678");
    const second = await eventIdForIdempotencyKey("github-request-12345678");
    expect(first).toMatch(/^[0-9a-f]{64}$/);
    expect(second).toBe(first);
  });

  it("rejects noncanonical timestamps and actor logins", () => {
    expect(() =>
      validateStateEvent({ ...EVENT, occurred_at: "2026-08-20T06:07:08Z" }),
    ).toThrow(/timestamp/);
    expect(() =>
      validateStateEvent({ ...EVENT, actor: { kind: "github", login: "Kim-Em" } }),
    ).toThrow(/login/);
  });
});
