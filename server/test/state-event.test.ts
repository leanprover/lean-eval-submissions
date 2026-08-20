import { describe, expect, it } from "vitest";

import {
  newEventId,
  stateEventPath,
  type StateEvent,
  validateStateEvent,
} from "../src/state-event";

const EVENT: StateEvent = {
  schema_version: 1,
  event_id: "0198abcd-0000-7000-8000-000000000001",
  event_type: "system.initialized",
  occurred_at: "2026-08-20T06:07:08.000Z",
  subject_id: "state_staging",
  causation_event_id: null,
  actor: { kind: "system" },
  payload: { environment: "staging" },
};

describe("State event contract", () => {
  it("places one immutable event in an id-partitioned file", () => {
    expect(stateEventPath(EVENT)).toBe(`events/01/${EVENT.event_id}.json`);
  });

  it("allocates canonical UUIDv7 event ids", () => {
    const eventId = newEventId(0x0198abcd0000, new Uint8Array(16).fill(0xab));
    expect(eventId).toBe("0198abcd-0000-7bab-abab-abababababab");
    expect(() => newEventId(-1)).toThrow(/48 bits/);
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

  it("validates the exact submission intake event written by the Worker", () => {
    expect(() =>
      validateStateEvent({
        schema_version: 1,
        event_id: "0198abcd-0000-7000-8000-000000000002",
        event_type: "submission.received",
        occurred_at: "2026-08-20T06:07:09.000Z",
        subject_id: "0198abcd-1111-7000-8000-000000000001",
        causation_event_id: null,
        actor: { kind: "github", login: "kim-em" },
        payload: {
          problem_id: "two_plus_two",
          statement_revision: 1,
          declared_model: "Example Model",
          source_repository: "example/submission",
          source_commit: "a".repeat(40),
          source_visibility: "private",
          publication_choice: "scheduled",
        },
      }),
    ).not.toThrow();
    expect(() =>
      validateStateEvent({
        ...EVENT,
        event_type: "submission.received",
        subject_id: "submission_abc",
      }),
    ).toThrow(/UUIDv7/);
  });
});
