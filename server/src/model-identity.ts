const MODEL_ID = /^mi1_[0-9a-f]{64}$/;
const ALIAS_KEY = /^ma1_[0-9a-f]{64}$/;
const EVENT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const REASON = /^[a-z][a-z0-9_]{1,63}$/;
const MODEL_ID_DOMAIN = "lean-eval-model-identity-v1\0";
const ALIAS_KEY_DOMAIN = "lean-eval-model-alias-v1\0";

export type ModelIdentityStatus = "pending" | "approved" | "rejected" | "consolidated";

export type ModelIdentityConsolidationCapability =
  "atomic_reverse_impact_v1";

export const MODEL_IDENTITY_CONSOLIDATION_CAPABILITY:
  ModelIdentityConsolidationCapability = "atomic_reverse_impact_v1";

export const PRODUCTION_MODEL_IDENTITY_STATE_CONTRACT_COMMIT =
  "c6a4bb67b55609ae7215bdd3cac2378b2db42a0a";
export const STAGING_MODEL_IDENTITY_STATE_CONTRACT_COMMIT =
  "8ae11456f0a439f91ec5822ec36adb93b76b0d96";
export const MODEL_IDENTITY_REVERSE_IMPACT_MAX_VIEWS = 32;

export type ModelIdentityView = Readonly<{
  schema_version: 1;
  model_id: string;
  owner_login: string;
  requested_name: string;
  display_name: string;
  status: ModelIdentityStatus;
  request_event_id: string;
  requested_at: string;
  decision_event_id: string | null;
  decided_at: string | null;
  reviewer_login: string | null;
  rejection_reason: string | null;
  mutation_event_id: string;
  consolidated_into: string | null;
  resolved_model_id: string | null;
}>;

export type ModelAliasView = Readonly<{
  schema_version: 1;
  alias_key: string;
  owner_login: string;
  alias: string;
  model_id: string;
  assignment_event_id: string;
  assigned_at: string;
  resolved_model_id: string;
}>;

export type ModelIdentityReverseImpactIdentityMember = Readonly<{
  kind: "identity";
  model_id: string;
  mutation_event_id: string;
  view_path: string;
}>;

export type ModelIdentityReverseImpactAliasMember = Readonly<{
  kind: "alias";
  alias_key: string;
  assignment_event_id: string;
  model_id: string;
  view_path: string;
}>;

export type ModelIdentityReverseImpactMember =
  | ModelIdentityReverseImpactIdentityMember
  | ModelIdentityReverseImpactAliasMember;

export type ModelIdentityReverseImpactView = Readonly<{
  schema_version: 1;
  terminal_model_id: string;
  owner_login: string;
  terminal_mutation_event_id: string;
  member_count: number;
  maximum_member_count: 32;
  members: readonly ModelIdentityReverseImpactMember[];
}>;

export type ModelIdentityDecisionInput = Readonly<{
  decision: "approve" | "reject";
  reason_code: string | null;
}>;

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exact(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new TypeError(`${label} fields are invalid`);
  }
}

function timestamp(value: unknown): value is string {
  return typeof value === "string" && !value.startsWith("0000-") &&
    !Number.isNaN(Date.parse(value)) && new Date(value).toISOString() === value;
}

export function decodeModelLabel(value: unknown, label = "model label"): string {
  if (typeof value !== "string" || value.length === 0 || new TextEncoder().encode(value).byteLength > 256) {
    throw new TypeError(`${label} must be nonempty UTF-8 of at most 256 bytes`);
  }
  for (const character of value) {
    const point = character.codePointAt(0) ?? 0;
    if (point <= 0x1f || point === 0x7f || (point >= 0xd800 && point <= 0xdfff)) {
      throw new TypeError(`${label} must be valid UTF-8 without ASCII controls`);
    }
  }
  return value;
}

export function decodeReasonCode(value: unknown): string {
  if (typeof value !== "string" || !REASON.test(value)) throw new TypeError("reason_code is invalid");
  return value;
}

export function decodeModelId(value: unknown): string {
  if (typeof value !== "string" || !MODEL_ID.test(value)) throw new TypeError("model_id is invalid");
  return value;
}

export function decodeModelIdentityRequest(value: unknown): Readonly<{ display_name: string }> {
  const input = object(value, "model identity request");
  exact(input, ["display_name"], "model identity request");
  return { display_name: decodeModelLabel(input.display_name, "display_name") };
}

export function decodeModelIdentityDecision(value: unknown): ModelIdentityDecisionInput {
  const input = object(value, "model identity decision");
  if (input.decision === "approve") {
    exact(input, ["decision"], "model identity approval");
    return { decision: "approve", reason_code: null };
  }
  exact(input, ["decision", "reason_code"], "model identity rejection");
  if (input.decision !== "reject") throw new TypeError("model identity decision is invalid");
  return { decision: "reject", reason_code: decodeReasonCode(input.reason_code) };
}

export function decodeModelAliasAssignment(value: unknown): Readonly<{ alias: string }> {
  const input = object(value, "model alias assignment");
  exact(input, ["alias"], "model alias assignment");
  return { alias: decodeModelLabel(input.alias, "alias") };
}

export function decodeModelRename(value: unknown): Readonly<{ display_name: string }> {
  const input = object(value, "model identity rename");
  exact(input, ["display_name"], "model identity rename");
  return { display_name: decodeModelLabel(input.display_name, "display_name") };
}

export function decodeModelConsolidation(value: unknown): Readonly<{ target_model_id: string }> {
  const input = object(value, "model identity consolidation");
  exact(input, ["target_model_id"], "model identity consolidation");
  return { target_model_id: decodeModelId(input.target_model_id) };
}

async function sha256Hex(value: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function modelIdentityId(requestEventId: string): Promise<string> {
  if (!EVENT_ID.test(requestEventId)) throw new TypeError("model identity request event is invalid");
  return `mi1_${await sha256Hex(`${MODEL_ID_DOMAIN}${requestEventId}`)}`;
}

export async function modelAliasKey(ownerLogin: string, alias: string): Promise<string> {
  if (!LOGIN.test(ownerLogin)) throw new TypeError("model alias owner is invalid");
  decodeModelLabel(alias, "model alias");
  // JSON.stringify is RFC 8785-equivalent for this closed two-string tuple.
  return `ma1_${await sha256Hex(`${ALIAS_KEY_DOMAIN}${JSON.stringify([ownerLogin, alias])}`)}`;
}

export function modelIdentityPath(modelId: string): string {
  decodeModelId(modelId);
  return `views/model-identities/${modelId.slice(4, 6)}/${modelId}.json`;
}

export function modelAliasPath(aliasKey: string): string {
  if (!ALIAS_KEY.test(aliasKey)) throw new TypeError("model alias key is invalid");
  return `views/model-aliases/${aliasKey.slice(4, 6)}/${aliasKey}.json`;
}

export function modelIdentityReverseImpactPath(terminalModelId: string): string {
  decodeModelId(terminalModelId);
  return `views/model-identity-reverse-impacts/${terminalModelId.slice(4, 6)}/${terminalModelId}.json`;
}

export function decodeModelIdentityView(value: unknown): ModelIdentityView {
  const view = object(value, "model identity view");
  exact(view, [
    "consolidated_into", "decided_at", "decision_event_id", "display_name", "model_id",
    "mutation_event_id", "owner_login", "rejection_reason", "request_event_id", "requested_at",
    "requested_name", "resolved_model_id", "reviewer_login", "schema_version", "status",
  ], "model identity view");
  const nullableEvent = (field: unknown): boolean => field === null || (typeof field === "string" && EVENT_ID.test(field));
  const nullableModel = (field: unknown): boolean => field === null || (typeof field === "string" && MODEL_ID.test(field));
  if (
    view.schema_version !== 1 || typeof view.model_id !== "string" || !MODEL_ID.test(view.model_id) ||
    typeof view.owner_login !== "string" || !LOGIN.test(view.owner_login) ||
    typeof view.request_event_id !== "string" || !EVENT_ID.test(view.request_event_id) ||
    typeof view.mutation_event_id !== "string" || !EVENT_ID.test(view.mutation_event_id) ||
    !timestamp(view.requested_at) || !nullableEvent(view.decision_event_id) ||
    !(view.decided_at === null || timestamp(view.decided_at)) ||
    !(view.reviewer_login === null || (typeof view.reviewer_login === "string" && LOGIN.test(view.reviewer_login))) ||
    !(view.rejection_reason === null || (typeof view.rejection_reason === "string" && REASON.test(view.rejection_reason))) ||
    !nullableModel(view.consolidated_into) || !nullableModel(view.resolved_model_id) ||
    !new Set(["pending", "approved", "rejected", "consolidated"]).has(String(view.status))
  ) throw new TypeError("model identity view is invalid");
  decodeModelLabel(view.requested_name, "requested model name");
  decodeModelLabel(view.display_name, "model display name");
  const decisionFieldsAbsent = view.decision_event_id === null && view.decided_at === null &&
    view.reviewer_login === null;
  const decisionFieldsPresent = view.decision_event_id !== null && view.decided_at !== null &&
    view.reviewer_login !== null;
  if (
    (view.status === "pending" ? !decisionFieldsAbsent : !decisionFieldsPresent) ||
    (view.status === "rejected") !== (view.rejection_reason !== null) ||
    (view.status === "consolidated") !== (view.consolidated_into !== null) ||
    (view.status === "pending" || view.status === "rejected") !== (view.resolved_model_id === null) ||
    (view.status === "approved" && view.resolved_model_id !== view.model_id) ||
    ((view.status === "pending" || view.status === "rejected") && view.display_name !== view.requested_name) ||
    (view.status === "pending" && view.mutation_event_id !== view.request_event_id) ||
    (view.status === "rejected" && view.mutation_event_id !== view.decision_event_id)
  ) throw new TypeError("model identity view lifecycle fields disagree");
  return view as ModelIdentityView;
}

export function decodeModelAliasView(value: unknown): ModelAliasView {
  const view = object(value, "model alias view");
  exact(view, [
    "alias", "alias_key", "assigned_at", "assignment_event_id", "model_id", "owner_login",
    "resolved_model_id", "schema_version",
  ], "model alias view");
  if (
    view.schema_version !== 1 || typeof view.alias_key !== "string" || !ALIAS_KEY.test(view.alias_key) ||
    typeof view.owner_login !== "string" || !LOGIN.test(view.owner_login) ||
    typeof view.model_id !== "string" || !MODEL_ID.test(view.model_id) ||
    typeof view.resolved_model_id !== "string" || !MODEL_ID.test(view.resolved_model_id) ||
    typeof view.assignment_event_id !== "string" || !EVENT_ID.test(view.assignment_event_id) ||
    !timestamp(view.assigned_at)
  ) throw new TypeError("model alias view is invalid");
  decodeModelLabel(view.alias, "model alias");
  return view as ModelAliasView;
}

export function decodeModelIdentityReverseImpactView(
  value: unknown,
): ModelIdentityReverseImpactView {
  const view = object(value, "model identity reverse-impact view");
  exact(view, [
    "maximum_member_count", "member_count", "members", "owner_login", "schema_version",
    "terminal_model_id", "terminal_mutation_event_id",
  ], "model identity reverse-impact view");
  if (
    view.schema_version !== 1 ||
    typeof view.terminal_model_id !== "string" || !MODEL_ID.test(view.terminal_model_id) ||
    typeof view.owner_login !== "string" || !LOGIN.test(view.owner_login) ||
    typeof view.terminal_mutation_event_id !== "string" || !EVENT_ID.test(view.terminal_mutation_event_id) ||
    typeof view.member_count !== "number" || !Number.isInteger(view.member_count) || view.member_count < 1 ||
    view.maximum_member_count !== MODEL_IDENTITY_REVERSE_IMPACT_MAX_VIEWS ||
    !Array.isArray(view.members) || view.members.length !== view.member_count ||
    view.members.length > MODEL_IDENTITY_REVERSE_IMPACT_MAX_VIEWS
  ) throw new TypeError("model identity reverse-impact view is invalid");

  const members: ModelIdentityReverseImpactMember[] = [];
  for (const rawMember of view.members) {
    const member = object(rawMember, "model identity reverse-impact member");
    if (member.kind === "identity") {
      exact(member, ["kind", "model_id", "mutation_event_id", "view_path"], "model identity reverse-impact identity member");
      if (
        typeof member.model_id !== "string" || !MODEL_ID.test(member.model_id) ||
        typeof member.mutation_event_id !== "string" || !EVENT_ID.test(member.mutation_event_id) ||
        member.view_path !== modelIdentityPath(member.model_id)
      ) throw new TypeError("model identity reverse-impact identity member is invalid");
      members.push(member as ModelIdentityReverseImpactIdentityMember);
      continue;
    }
    if (member.kind === "alias") {
      exact(member, [
        "alias_key", "assignment_event_id", "kind", "model_id", "view_path",
      ], "model identity reverse-impact alias member");
      if (
        typeof member.alias_key !== "string" || !ALIAS_KEY.test(member.alias_key) ||
        typeof member.assignment_event_id !== "string" || !EVENT_ID.test(member.assignment_event_id) ||
        typeof member.model_id !== "string" || !MODEL_ID.test(member.model_id) ||
        member.view_path !== modelAliasPath(member.alias_key)
      ) throw new TypeError("model identity reverse-impact alias member is invalid");
      members.push(member as ModelIdentityReverseImpactAliasMember);
      continue;
    }
    throw new TypeError("model identity reverse-impact member kind is invalid");
  }
  const paths = members.map((member) => member.view_path);
  if (
    paths.some((path, index) => index > 0 && path <= (paths[index - 1] ?? "")) ||
    !members.some((member) =>
      member.kind === "identity" &&
      member.model_id === view.terminal_model_id &&
      member.mutation_event_id === view.terminal_mutation_event_id)
  ) throw new TypeError("model identity reverse-impact members are invalid");
  return {
    schema_version: 1,
    terminal_model_id: view.terminal_model_id,
    owner_login: view.owner_login,
    terminal_mutation_event_id: view.terminal_mutation_event_id,
    member_count: view.member_count,
    maximum_member_count: MODEL_IDENTITY_REVERSE_IMPACT_MAX_VIEWS,
    members,
  };
}

export function modelIdentityReverseImpactView(
  terminal: ModelIdentityView,
  members: readonly ModelIdentityReverseImpactMember[],
): ModelIdentityReverseImpactView {
  const view = {
    schema_version: 1,
    terminal_model_id: terminal.model_id,
    owner_login: terminal.owner_login,
    terminal_mutation_event_id: terminal.mutation_event_id,
    member_count: members.length,
    maximum_member_count: MODEL_IDENTITY_REVERSE_IMPACT_MAX_VIEWS,
    members: [...members].sort((left, right) => left.view_path.localeCompare(right.view_path)),
  } as const;
  return decodeModelIdentityReverseImpactView(view);
}
