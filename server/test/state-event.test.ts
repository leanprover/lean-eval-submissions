import { describe, expect, it } from "vitest";

import attemptLimitEvent from "../../tests/fixtures/replay-failed-attempt-limit-v1.json";

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

const ATTEMPT_LIMIT_EVENT: unknown = attemptLimitEvent;

describe("State event contract", () => {
  it("validates the closed model identity producer event family", () => {
    const modelId = `mi1_${"a".repeat(64)}`;
    const request = {
      schema_version: 1,
      event_id: "0198abcd-0000-7000-8000-000000000020",
      event_type: "model_identity.requested",
      occurred_at: "2026-08-20T00:00:00.000Z",
      subject_id: modelId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: { display_name: "Model Alpha" },
    } as const;
    expect(() => validateStateEvent(request)).not.toThrow();
    expect(() => validateStateEvent({ ...request, actor: { kind: "github", login: "Alice" } })).toThrow(/actor/u);
    expect(() => validateStateEvent({ ...request, payload: { display_name: "Model", owner_login: "mallory" } })).toThrow(/fields/u);
    expect(() => validateStateEvent({ ...request, payload: { display_name: "\ud800" } })).toThrow(/invalid/u);
    expect(() => validateStateEvent({
      ...request,
      event_id: "0198abcd-0001-7000-8000-000000000021",
      event_type: "model_identity.approved",
      causation_event_id: request.event_id,
      actor: { kind: "system" },
      payload: { reviewer_login: "reviewer" },
    })).not.toThrow();
    expect(() => validateStateEvent({
      ...request,
      event_id: "0198abcd-0002-7000-8000-000000000022",
      event_type: "model_identity.consolidated",
      causation_event_id: request.event_id,
      payload: { target_model_id: `mi1_${"b".repeat(64)}` },
    })).not.toThrow();
  });

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
        event_id: "0198abcd-1111-7000-8000-000000000001",
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
    expect(() => validateStateEvent({
      schema_version: 1,
      event_id: "0198abcd-1111-7000-8000-000000000001",
      event_type: "submission.received",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: "0198abcd-1111-7000-8000-000000000001",
      causation_event_id: null,
      actor: { kind: "github", login: "kim-em" },
      payload: {
        problem_id: "two_plus_two", statement_revision: 1, declared_model: "é".repeat(129),
        source_repository: "example/submission", source_commit: "a".repeat(40),
        source_visibility: "private", publication_choice: "scheduled",
      },
    })).toThrow(/256 bytes/);
  });

  it("accepts the exact expanded Wave-2 replay terminal payload", () => {
    const terminal = {
      schema_version: 1,
      event_id: "0198abcd-2222-7000-8000-000000000003",
      event_type: "replay.crashed",
      occurred_at: "2026-08-20T06:07:10.000Z",
      subject_id: `rt1_${"a".repeat(64)}`,
      causation_event_id: "0198abcd-2222-7000-8000-000000000002",
      actor: { kind: "system" },
      payload: {
        attempt: 1,
        checker: "lean-checker-v1",
        checker_wall_time_ms: 123,
        checker_retired_instructions: null,
        checker_retired_instructions_unavailable_reason: "counter_not_supported",
        build_wall_time_ms: 456,
        build_retired_instructions: 789,
        build_retired_instructions_unavailable_reason: null,
        lines_of_code: 12,
        file_count: 2,
      },
    } as const;
    expect(() => validateStateEvent(terminal)).not.toThrow();
    expect(() => validateStateEvent({
      ...terminal,
      payload: { ...terminal.payload, build_retired_instructions_unavailable_reason: "counter_not_reported" },
    })).toThrow(/measured counter/);
  });

  it("accepts the shared terminal fourth-attempt fixture and rejects attempt five", () => {
    validateStateEvent(ATTEMPT_LIMIT_EVENT);
    if (ATTEMPT_LIMIT_EVENT.event_type !== "replay.failed") {
      throw new TypeError("attempt-limit fixture is not replay.failed");
    }
    expect(ATTEMPT_LIMIT_EVENT.payload).toEqual({
      attempt: 4,
      reason_code: "runner_lost",
      retryable: false,
    });
    expect(() => validateStateEvent({
      ...ATTEMPT_LIMIT_EVENT,
      payload: { ...ATTEMPT_LIMIT_EVENT.payload, attempt: 5 },
    })).toThrow(/attempt exceeds/u);
  });

  it("validates owner amendment requests and read-only maintainer decisions exactly", () => {
    const request = {
      schema_version: 1,
      event_id: "0198abcd-3333-7000-8000-000000000002",
      event_type: "result.retraction_requested",
      occurred_at: "2026-08-20T06:07:10.000Z",
      subject_id: `r2_${"a".repeat(64)}`,
      causation_event_id: "0198abcd-3333-7000-8000-000000000001",
      actor: { kind: "github", login: "alice" },
      payload: { retraction_revision: 1, reason_code: "owner_requested_withdrawal" },
    } as const;
    expect(() => validateStateEvent(request)).not.toThrow();
    expect(() => validateStateEvent({
      ...request,
      actor: { kind: "system" },
    })).toThrow(/actor/u);
    expect(() => validateStateEvent({
      ...request,
      payload: { ...request.payload, retraction_revision: 0 },
    })).toThrow(/positive/u);

    const decision = {
      ...request,
      event_id: "0198abcd-3333-7000-8000-000000000003",
      event_type: "result.retraction_decided",
      causation_event_id: request.event_id,
      actor: { kind: "system" },
      payload: {
        retraction_revision: 1,
        reviewer_login: "maintainer",
        decision: "reject",
        reason_code: "request_not_confirmed",
      },
    } as const;
    expect(() => validateStateEvent(decision)).not.toThrow();
    expect(() => validateStateEvent({
      ...decision,
      payload: { ...decision.payload, decision: "impersonate" },
    })).toThrow(/decision/u);
    expect(() => validateStateEvent({
      ...decision,
      actor: { kind: "github", login: "maintainer" },
    })).toThrow(/actor/u);
  });

  it("binds a scheduled release payload to its result subject", () => {
    const resultId = `r2_${"a".repeat(64)}`;
    const scheduled = {
      schema_version: 1,
      event_id: "0198abcd-3333-7000-8000-000000000004",
      event_type: "release.scheduled",
      occurred_at: "2026-08-20T06:07:11.000Z",
      subject_id: resultId,
      causation_event_id: "0198abcd-3333-7000-8000-000000000003",
      actor: { kind: "system" },
      payload: {
        result_id: resultId,
        release_at: "2026-10-20T06:07:11.000Z",
      },
    } as const;
    expect(() => validateStateEvent(scheduled)).not.toThrow();
    expect(() => validateStateEvent({
      ...scheduled,
      payload: { ...scheduled.payload, result_id: `r2_${"b".repeat(64)}` },
    })).toThrow(/disagrees/u);
  });

  it("validates the closed terminal duplicate-result disposition event", () => {
    const conflict = {
      schema_version: 1,
      event_id: "0198abcd-4444-7000-8000-000000000003",
      event_type: "submission.result_identity_conflicted",
      occurred_at: "2026-08-20T06:07:12.000Z",
      subject_id: "0198abcd-4444-7000-8000-000000000001",
      causation_event_id: "0198abcd-4444-7000-8000-000000000002",
      actor: { kind: "system" },
      payload: {
        result_id: `r2_${"a".repeat(64)}`,
        authority_event_id: "0198abcd-4444-7000-8000-000000000099",
        existing_kind: "recorded",
        reason_code: "duplicate_result_identity",
      },
    } as const;
    expect(() => validateStateEvent(conflict)).not.toThrow();
    expect(() => validateStateEvent({
      ...conflict,
      payload: { ...conflict.payload, existing_kind: "adopted" },
    })).toThrow(/existing_kind/u);
    expect(() => validateStateEvent({
      ...conflict,
      payload: { ...conflict.payload, owner_login: "alice" },
    })).toThrow(/fields/u);
    expect(() => validateStateEvent({
      ...conflict,
      payload: { ...conflict.payload, reason_code: "retry_duplicate" },
    })).toThrow(/reason_code/u);
  });
});
