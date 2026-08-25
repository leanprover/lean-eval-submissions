import { describe, expect, it } from "vitest";

import {
  decodeModelAliasView,
  decodeModelConsolidation,
  decodeModelIdentityDecision,
  decodeModelIdentityRequest,
  decodeModelIdentityView,
  decodeModelIdentityReverseImpactView,
  modelAliasKey,
  modelIdentityId,
} from "../src/model-identity";

const MODEL_ID = "mi1_5a3dd8d6aa12ca21b76357b40cb5b9414b7097acf2a7817318e6277a40deba33";

describe("model identity wire contract", () => {
  it("matches the State repository's language-neutral identifier vectors", async () => {
    await expect(modelIdentityId("0198abcd-0000-7000-8000-000000000020")).resolves.toBe(MODEL_ID);
    await expect(modelAliasKey("kim-em", 'Model "Alpha" / β')).resolves.toBe(
      "ma1_fe5d3fa6b161ed674871fd13f75051dcd718164a1207d679623fa721c025ffe4",
    );
  });

  it("keeps request and decision bodies closed and byte-bounded", () => {
    expect(decodeModelIdentityRequest({ display_name: "Model β" })).toEqual({ display_name: "Model β" });
    expect(() => decodeModelIdentityRequest({ display_name: "Model", owner_login: "mallory" })).toThrow(/fields/u);
    expect(() => decodeModelIdentityRequest({ display_name: "é".repeat(129) })).toThrow(/256 bytes/u);
    expect(() => decodeModelIdentityRequest({ display_name: "\ud800" })).toThrow(/valid UTF-8/u);
    expect(decodeModelIdentityDecision({ decision: "approve" })).toEqual({ decision: "approve", reason_code: null });
    expect(decodeModelIdentityDecision({ decision: "reject", reason_code: "duplicate_identity" })).toEqual({
      decision: "reject", reason_code: "duplicate_identity",
    });
    expect(() => decodeModelIdentityDecision({ decision: "approve", reason_code: "smuggled" })).toThrow(/fields/u);
    expect(() => decodeModelIdentityDecision({ decision: "reject", reason_code: "INVALID" })).toThrow(/reason_code/u);
    expect(decodeModelConsolidation({ target_model_id: MODEL_ID })).toEqual({
      target_model_id: MODEL_ID,
    });
    expect(() => decodeModelConsolidation({
      target_model_id: MODEL_ID,
      owner_login: "mallory",
    })).toThrow(/fields/u);
  });

  it("rejects inconsistent lifecycle views and alias identities", () => {
    const approved = {
      schema_version: 1,
      model_id: MODEL_ID,
      owner_login: "kim-em",
      requested_name: "Model Alpha",
      display_name: "Model Alpha",
      status: "approved",
      request_event_id: "0198abcd-0000-7000-8000-000000000020",
      requested_at: "2026-08-20T00:00:00.000Z",
      decision_event_id: "0198abcd-0001-7000-8000-000000000021",
      decided_at: "2026-08-20T00:00:01.000Z",
      reviewer_login: "reviewer",
      rejection_reason: null,
      mutation_event_id: "0198abcd-0001-7000-8000-000000000021",
      consolidated_into: null,
      resolved_model_id: MODEL_ID,
    } as const;
    expect(decodeModelIdentityView(approved)).toEqual(approved);
    expect(() => decodeModelIdentityView({ ...approved, status: "pending" })).toThrow(/lifecycle/u);
    expect(() => decodeModelIdentityView({ ...approved, surprise: true })).toThrow(/fields/u);
    expect(() => decodeModelAliasView({
      schema_version: 1,
      alias_key: `ma1_${"a".repeat(64)}`,
      owner_login: "Kim-Em",
      alias: "Model Alpha",
      model_id: MODEL_ID,
      assignment_event_id: "0198abcd-0002-7000-8000-000000000022",
      assigned_at: "2026-08-20T00:00:02.000Z",
      resolved_model_id: MODEL_ID,
    })).toThrow(/invalid/u);
  });

  it("rejects malformed, mispathed, duplicate, and cross-field-inconsistent reverse indexes", () => {
    const member = {
      kind: "identity",
      model_id: MODEL_ID,
      mutation_event_id: "0198abcd-0001-7000-8000-000000000021",
      view_path: `views/model-identities/${MODEL_ID.slice(4, 6)}/${MODEL_ID}.json`,
    } as const;
    const view = {
      schema_version: 1,
      terminal_model_id: MODEL_ID,
      owner_login: "kim-em",
      terminal_mutation_event_id: member.mutation_event_id,
      member_count: 1,
      maximum_member_count: 32,
      members: [member],
    } as const;
    expect(decodeModelIdentityReverseImpactView(view)).toEqual(view);
    expect(() => decodeModelIdentityReverseImpactView({
      ...view,
      members: [{ ...member, view_path: "views/model-identities/../escape.json" }],
    })).toThrow(/invalid/u);
    expect(() => decodeModelIdentityReverseImpactView({
      ...view,
      member_count: 2,
      members: [member, member],
    })).toThrow(/members/u);
    expect(() => decodeModelIdentityReverseImpactView({
      ...view,
      terminal_mutation_event_id: "0198abcd-0002-7000-8000-000000000022",
    })).toThrow(/members/u);
    expect(() => decodeModelIdentityReverseImpactView({ ...view, surprise: true }))
      .toThrow(/fields/u);
  });
});
