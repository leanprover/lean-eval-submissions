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
  event_type: "system.initialized",
  occurred_at: "2026-08-20T06:07:08.000Z",
  subject_id: "state_staging",
  actor: { kind: "system" },
  payload: { environment: "staging" },
};

describe("State event contract", () => {
  it("places one immutable event in an id-partitioned file", () => {
    expect(stateEventPath(EVENT)).toBe(`events/aa/${"a".repeat(64)}.json`);
  });

  it("derives stable domain-separated event ids", async () => {
    const first = await eventIdForIdempotencyKey("github-request-12345678");
    const second = await eventIdForIdempotencyKey("github-request-12345678");
    expect(first).toMatch(/^[0-9a-f]{64}$/);
    expect(second).toBe(first);
  });

  it("rejects noncanonical timestamps and unregistered actor kinds", () => {
    expect(() =>
      validateStateEvent({ ...EVENT, occurred_at: "2026-08-20T06:07:08Z" }),
    ).toThrow(/timestamp/);
    expect(() =>
      validateStateEvent({ ...EVENT, occurred_at: "0000-08-20T06:07:08.000Z" }),
    ).toThrow(/timestamp/);
    expect(() => validateStateEvent({ ...EVENT, actor: { kind: "bogus" } })).toThrow(/actor/);
  });

  it("rejects schema drift and non-object payloads", () => {
    expect(() => validateStateEvent({ ...EVENT, schema_version: 2 })).toThrow(/schema/);
    expect(() => validateStateEvent({ ...EVENT, payload: null })).toThrow(/payload/);
    expect(() => validateStateEvent({ ...EVENT, surprise: true })).toThrow(/fields/);
  });
});
