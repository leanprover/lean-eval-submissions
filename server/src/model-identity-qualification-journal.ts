import { DurableObject } from "cloudflare:workers";

import {
  modelIdentityId,
  modelAliasKey,
  modelAliasPath,
  modelIdentityPath,
  modelIdentityReverseImpactPath,
  modelIdentityReverseImpactView,
  type ModelIdentityView,
  type ModelIdentityReverseImpactView,
  type ModelAliasView,
} from "./model-identity";
import { newEventId, stateEventPath } from "./state-event";
import {
  reviewedQualificationFixtureManifest,
  type QualificationFixtureManifest,
} from "./model-identity-qualification-fixture";

const SCHEMA_VERSION = 2;
const ACTIVE_LEASE_ALARM_MS = 5 * 60 * 1000;
const SHA = /^[0-9a-f]{40}$/;
const RUN_ID = /^[1-9][0-9]{0,19}$/;
const JOURNAL_ID = /^mqj_[0-9a-f]{64}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const UUID_V7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export const QUALIFICATION_OPERATIONS = [
  "oauth_session_identity",
  "agent_session_identity",
  "owner_request",
  "maintainer_approve",
  "maintainer_reject",
  "alias_assignment",
  "identity_rename",
  "complete_graph_consolidation",
  "chained_terminal_retry",
  "component_cap_refusal",
  "idempotent_retry",
  "cross_route_event_collision",
  "cross_owner_denial",
  "maximal_contention_measurement",
] as const;

export type QualificationOperation = typeof QUALIFICATION_OPERATIONS[number];

type Identity = Readonly<{ github_id: number; login: string }>;

export type QualificationJson =
  | null
  | boolean
  | number
  | string
  | readonly QualificationJson[]
  | Readonly<{ [key: string]: QualificationJson }>;

export type QualificationIntent = Readonly<{
  owner: Identity;
  cross_owner: Identity;
  maintainer: Identity;
}>;

export type QualificationFixtureEvidence = Readonly<{
  evidence_class: "reviewed_live_fixture" | "source_test_only";
  fixture_id: string;
  manifest_digest: string;
}>;

export type QualificationAcquisition = Readonly<{
  schema_version: 2;
  run_id: string;
  run_attempt: 1;
  deployed_commit: string;
  initial_state_commit: string;
  initial_state_tree: string;
  intent: QualificationIntent;
  fixture_evidence: QualificationFixtureEvidence;
}>;

export type QualificationStepReservation = Readonly<{
  run_id: string;
  run_attempt: 1;
  journal_id: string;
  expected_journal_revision: number;
  expected_state_commit: string;
  expected_state_tree: string;
  operation: QualificationOperation;
}>;

export type QualificationStepCompletion = Readonly<{
  reservation: QualificationStepReservation;
  state_commit: string;
  state_tree: string;
  receipt: QualificationJson;
}>;

export type QualificationApiRequestPlan = Readonly<{
  actor: Identity;
  body: QualificationJson;
  credential_role: "oauth_owner" | "agent_owner" | "cross_owner" | "maintainer";
  event_id: string;
  method: "POST" | "PUT";
  occurred_at: string;
  path: string;
  expected_http_status: number;
  expected_response: QualificationJson;
  expected_commit_message: string | null;
  expected_documents: Readonly<Record<string, QualificationJson>>;
  expected_deleted_paths: readonly string[];
}>;

export type QualificationStepPlan = Readonly<{
  operation: QualificationOperation;
  route: string;
  actor: Identity;
  credential_roles: readonly (
    "oauth_owner" | "agent_owner" | "cross_owner" | "maintainer"
  )[];
  expected_http_status: number;
  mutation_expected: boolean;
  api_requests: readonly QualificationApiRequestPlan[];
  event_ids: readonly string[];
  model_ids: readonly string[];
  alias_keys: readonly string[];
  assertions: Readonly<Record<string, true>>;
  expected_commit_messages: readonly string[];
  expected_documents: Readonly<Record<string, QualificationJson>>;
  expected_state_prefix?: readonly Readonly<{
    expected_message: string;
    expected_documents: Readonly<Record<string, QualificationJson>>;
    expected_deleted_paths: readonly string[];
    expected_tree_unchanged: boolean;
  }>[];
}>;

export type PendingStep = Readonly<{
  operation: QualificationOperation;
  plan: QualificationStepPlan;
  plan_digest: string;
  reserved_revision: number;
  expected_state_commit: string;
  expected_state_tree: string;
}>;

type Restoration = Readonly<{
  restoration_commit: string;
  restoration_parent_commit: string;
  restoration_parent_tree: string;
  restoration_tree: string;
}>;

type StoredJournal = Readonly<{
  acquisition: QualificationAcquisition;
  journal_id: string;
  journal_revision: number;
  current_state_commit: string;
  current_state_tree: string;
  lease_status: "active" | "restored";
  pending_step: PendingStep | null;
  completed_plans: readonly QualificationStepPlan[];
  recovery_reconciliations: readonly RecoveryReconciliation[];
  recovery_nonce: string;
  restoration: Restoration | null;
}>;

export type RecoveryReconciliation = Readonly<{
  operation: QualificationOperation;
  plan_digest: string;
  applied_mutations: number;
  planned_mutations: number;
  state_commit: string;
  state_tree: string;
}>;

export type QualificationRecoveryPlan = Readonly<{
  journal_id: string;
  recovery_nonce: string;
  journal_revision: number;
  initial_state_commit: string;
  initial_state_tree: string;
  current_state_commit: string;
  current_state_tree: string;
  intent: QualificationIntent;
  fixture_evidence: QualificationFixtureEvidence;
  pending_step: PendingStep | null;
  completed_plans: readonly QualificationStepPlan[];
  recovery_reconciliations: readonly RecoveryReconciliation[];
}>;

export type QualificationJournalStatus = Readonly<{
  schema_version: 2;
  status: "model_identity_qualification_journal";
  environment: "staging";
  deployed_commit: string;
  run_id: string;
  run_attempt: 1;
  journal_id: string;
  journal_revision: number;
  initial_state_commit: string;
  initial_state_tree: string;
  current_state_commit: string;
  current_state_tree: string;
  fixture_evidence_class: "reviewed_live_fixture" | "source_test_only";
  fixture_id: string;
  fixture_manifest_digest: string;
  recovery_reconciliations: readonly RecoveryReconciliation[];
  lease_status: "active" | "restored";
  lease_released: boolean;
  owner_api_enabled: false;
  maintainer_api_enabled: false;
  foreign_commit_observed: false;
  restoration_commit: string | null;
  restoration_parent_commit: string | null;
  restoration_parent_tree: string | null;
  restoration_tree: string | null;
  restoration_fast_forward: boolean;
  restoration_tree_equal: boolean;
}>;

type StoredRow = Readonly<{ body: string }>;
type VerificationRow = Readonly<{ body: string; created_at: number }>;

export type QualificationStepReservationOutcome =
  | Readonly<{
      kind: "reserved";
      journal: QualificationJournalStatus;
      plan_json: string;
      plan_digest: string;
    }>
  | Readonly<{ kind: "completed"; receipt_json: string }>;

export function canonicalQualificationJson(value: QualificationJson): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalQualificationJson).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const record = value as Readonly<Record<string, QualificationJson>>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalQualificationJson(record[key] ?? null)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export function canonicalQualificationValue(value: unknown): string {
  return canonicalQualificationJson(qualificationJson(value));
}

function exactJson(left: QualificationJson, right: QualificationJson): boolean {
  return canonicalQualificationJson(left) === canonicalQualificationJson(right);
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactFields(
  value: Record<string, unknown>,
  fields: readonly string[],
  label: string,
): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new TypeError(`${label} fields are invalid`);
  }
}

function identity(value: unknown, label: string): Identity {
  const input = record(value, label);
  exactFields(input, ["github_id", "login"], label);
  if (
    typeof input.github_id !== "number" ||
    !Number.isSafeInteger(input.github_id) ||
    input.github_id < 1 ||
    typeof input.login !== "string" ||
    !/^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/.test(input.login)
  ) {
    throw new TypeError(`${label} is invalid`);
  }
  return { github_id: input.github_id, login: input.login };
}

function qualificationJson(value: unknown): QualificationJson {
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "string" ||
    (typeof value === "number" && Number.isFinite(value) && Number.isSafeInteger(value))
  ) {
    return value;
  }
  if (Array.isArray(value)) return value.map(qualificationJson);
  if (typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, member]) => [key, qualificationJson(member)]),
    );
  }
  throw new TypeError("qualification JSON value is invalid");
}

function canonicalAcquisition(value: unknown): QualificationAcquisition {
  const input = record(value, "qualification acquisition");
  exactFields(input, [
    "schema_version", "run_id", "run_attempt", "deployed_commit",
    "initial_state_commit", "initial_state_tree", "intent", "fixture_evidence",
  ], "qualification acquisition");
  const rawIntent = record(input.intent, "qualification intent");
  exactFields(rawIntent, ["owner", "cross_owner", "maintainer"], "qualification intent");
  const rawFixtureEvidence = record(
    input.fixture_evidence,
    "qualification fixture evidence",
  );
  exactFields(
    rawFixtureEvidence,
    ["evidence_class", "fixture_id", "manifest_digest"],
    "qualification fixture evidence",
  );
  if (
    input.schema_version !== SCHEMA_VERSION ||
    typeof input.run_id !== "string" ||
    !RUN_ID.test(input.run_id) ||
    input.run_attempt !== 1 ||
    typeof input.deployed_commit !== "string" ||
    !SHA.test(input.deployed_commit) ||
    typeof input.initial_state_commit !== "string" ||
    !SHA.test(input.initial_state_commit) ||
    typeof input.initial_state_tree !== "string" ||
    !SHA.test(input.initial_state_tree) ||
    (
      rawFixtureEvidence.evidence_class !== "reviewed_live_fixture" &&
      rawFixtureEvidence.evidence_class !== "source_test_only"
    ) ||
    typeof rawFixtureEvidence.fixture_id !== "string" ||
    !UUID_V7.test(rawFixtureEvidence.fixture_id) ||
    typeof rawFixtureEvidence.manifest_digest !== "string" ||
    !SHA256.test(rawFixtureEvidence.manifest_digest)
  ) {
    throw new TypeError("qualification acquisition is invalid");
  }
  const intent = {
    owner: identity(rawIntent.owner, "qualification owner"),
    cross_owner: identity(rawIntent.cross_owner, "qualification cross-owner"),
    maintainer: identity(rawIntent.maintainer, "qualification maintainer"),
  };
  const identities = [intent.owner, intent.cross_owner, intent.maintainer];
  if (
    new Set(identities.map((item) => item.github_id)).size !== identities.length ||
    new Set(identities.map((item) => item.login)).size !== identities.length
  ) {
    throw new TypeError("qualification identities are not distinct");
  }
  return {
    schema_version: SCHEMA_VERSION,
    run_id: input.run_id,
    run_attempt: 1,
    deployed_commit: input.deployed_commit,
    initial_state_commit: input.initial_state_commit,
    initial_state_tree: input.initial_state_tree,
    intent,
    fixture_evidence: {
      evidence_class: rawFixtureEvidence.evidence_class,
      fixture_id: rawFixtureEvidence.fixture_id,
      manifest_digest: rawFixtureEvidence.manifest_digest,
    },
  };
}

function canonicalReservation(value: unknown): QualificationStepReservation {
  const input = record(value, "qualification step reservation");
  exactFields(input, [
    "run_id", "run_attempt", "journal_id", "expected_journal_revision",
    "expected_state_commit", "expected_state_tree", "operation",
  ], "qualification step reservation");
  if (
    typeof input.run_id !== "string" ||
    !RUN_ID.test(input.run_id) ||
    input.run_attempt !== 1 ||
    typeof input.journal_id !== "string" ||
    !JOURNAL_ID.test(input.journal_id) ||
    typeof input.expected_journal_revision !== "number" ||
    !Number.isSafeInteger(input.expected_journal_revision) ||
    input.expected_journal_revision < 1 ||
    typeof input.expected_state_commit !== "string" ||
    !SHA.test(input.expected_state_commit) ||
    typeof input.expected_state_tree !== "string" ||
    !SHA.test(input.expected_state_tree) ||
    typeof input.operation !== "string" ||
    !QUALIFICATION_OPERATIONS.includes(input.operation as QualificationOperation)
  ) {
    throw new TypeError("qualification step reservation is invalid");
  }
  return {
    run_id: input.run_id,
    run_attempt: 1,
    journal_id: input.journal_id,
    expected_journal_revision: input.expected_journal_revision,
    expected_state_commit: input.expected_state_commit,
    expected_state_tree: input.expected_state_tree,
    operation: input.operation as QualificationOperation,
  };
}

function canonicalMilliseconds(milliseconds: number): string {
  return new Date(milliseconds).toISOString();
}

async function sha256Hex(value: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  ));
  return [...digest]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export async function qualificationPlanDigest(
  plan: QualificationStepPlan,
): Promise<string> {
  return sha256Hex(
    `lean-eval-model-identity-qualification-plan-v2\0${canonicalQualificationJson(plan)}`,
  );
}

export async function qualificationApiRequestDigest(
  request: QualificationApiRequestPlan,
): Promise<string> {
  return sha256Hex(
    `lean-eval-model-identity-qualification-api-request-v1\0${canonicalQualificationJson(request)}`,
  );
}

function sessionPlan(
  operation: "oauth_session_identity" | "agent_session_identity",
  owner: Identity,
): QualificationStepPlan {
  const oauth = operation === "oauth_session_identity";
  return {
    operation,
    route: oauth ? "session/oauth-owner" : "session/agent-owner",
    actor: owner,
    credential_roles: [oauth ? "oauth_owner" : "agent_owner"],
    expected_http_status: 200,
    mutation_expected: false,
    api_requests: [],
    event_ids: [],
    model_ids: [],
    alias_keys: [],
    assertions: oauth
      ? {
          browser_session_signature_verified: true,
          exact_identity_verified: true,
          session_unexpired: true,
        }
      : {
          agent_source_commit_bound: true,
          browser_session_signature_verified: true,
          exact_identity_verified: true,
        },
    expected_commit_messages: [],
    expected_documents: {},
  };
}

async function ownerRequestPlan(
  acquisition: QualificationAcquisition,
  nowMilliseconds: number,
): Promise<QualificationStepPlan> {
  const eventId = newEventId(nowMilliseconds);
  const occurredAt = canonicalMilliseconds(nowMilliseconds);
  const modelId = await modelIdentityId(eventId);
  const displayName = `Qualification owner model run ${acquisition.run_id}`;
  const event = {
    schema_version: 1,
    event_id: eventId,
    event_type: "model_identity.requested",
    occurred_at: occurredAt,
    subject_id: modelId,
    causation_event_id: null,
    actor: { kind: "github", login: acquisition.intent.owner.login },
    payload: { display_name: displayName },
  } as const;
  const view = {
    schema_version: 1,
    model_id: modelId,
    owner_login: acquisition.intent.owner.login,
    requested_name: displayName,
    display_name: displayName,
    status: "pending",
    request_event_id: eventId,
    requested_at: occurredAt,
    decision_event_id: null,
    decided_at: null,
    reviewer_login: null,
    rejection_reason: null,
    mutation_event_id: eventId,
    consolidated_into: null,
    resolved_model_id: null,
  } as const;
  return {
    operation: "owner_request",
    route: "POST /api/v1/model-identities",
    actor: acquisition.intent.owner,
    credential_roles: ["oauth_owner"],
    expected_http_status: 201,
    mutation_expected: true,
    api_requests: [{
      actor: acquisition.intent.owner,
      body: { display_name: displayName },
      credential_role: "oauth_owner",
      event_id: eventId,
      method: "POST",
      occurred_at: occurredAt,
      path: "/api/v1/model-identities",
      expected_http_status: 201,
      expected_response: { model_id: modelId, status: "identity_requested" },
      expected_commit_message: `Request model identity ${modelId}`,
      expected_documents: {
        [stateEventPath(event)]: event,
        [modelIdentityPath(modelId)]: view,
      },
      expected_deleted_paths: [],
    }],
    event_ids: [eventId],
    model_ids: [modelId],
    alias_keys: [],
    assertions: {
      immutable_event_written: true,
      owner_derived_from_session: true,
      pending_view_written: true,
    },
    expected_commit_messages: [`Request model identity ${modelId}`],
    expected_documents: {
      [stateEventPath(event)]: event,
      [modelIdentityPath(modelId)]: view,
    },
  };
}

function nextPlanMilliseconds(journal: StoredJournal): number {
  let milliseconds = Date.now();
  for (const plan of journal.completed_plans) {
    for (const request of plan.api_requests) {
      milliseconds = Math.max(milliseconds, Date.parse(request.occurred_at) + 1);
    }
  }
  return milliseconds;
}

function expectedModelView(
  plan: QualificationStepPlan | undefined,
  modelId: string,
): ModelIdentityView {
  const value = plan?.expected_documents[modelIdentityPath(modelId)];
  if (value === undefined) {
    throw new Error("qualification predecessor model view is unavailable");
  }
  return value as ModelIdentityView;
}

function maintainerApprovePlan(
  journal: StoredJournal,
): QualificationStepPlan {
  const ownerPlan = journal.completed_plans[2];
  const modelId = ownerPlan?.model_ids[0];
  if (ownerPlan?.operation !== "owner_request" || modelId === undefined) {
    throw new Error("qualification owner request predecessor is unavailable");
  }
  const current = expectedModelView(ownerPlan, modelId);
  const nowMilliseconds = nextPlanMilliseconds(journal);
  const eventId = newEventId(nowMilliseconds);
  const occurredAt = canonicalMilliseconds(nowMilliseconds);
  const event = {
    schema_version: 1,
    event_id: eventId,
    event_type: "model_identity.approved",
    occurred_at: occurredAt,
    subject_id: modelId,
    causation_event_id: current.request_event_id,
    actor: { kind: "system" },
    payload: { reviewer_login: journal.acquisition.intent.maintainer.login },
  } as const;
  const view: ModelIdentityView = {
    ...current,
    status: "approved",
    decision_event_id: eventId,
    decided_at: occurredAt,
    reviewer_login: journal.acquisition.intent.maintainer.login,
    rejection_reason: null,
    mutation_event_id: eventId,
    resolved_model_id: modelId,
  };
  const reverseImpact = modelIdentityReverseImpactView(view, [{
    kind: "identity",
    model_id: modelId,
    mutation_event_id: eventId,
    view_path: modelIdentityPath(modelId),
  }]);
  const documents = {
    [stateEventPath(event)]: event,
    [modelIdentityPath(modelId)]: view,
    [modelIdentityReverseImpactPath(modelId)]: reverseImpact,
  };
  const message = `Record model identity decision ${modelId}`;
  return {
    operation: "maintainer_approve",
    route: "POST /api/v1/model-identities/{model_id}/decisions",
    actor: journal.acquisition.intent.maintainer,
    credential_roles: ["maintainer"],
    expected_http_status: 201,
    mutation_expected: true,
    api_requests: [{
      actor: journal.acquisition.intent.maintainer,
      body: { decision: "approve" },
      credential_role: "maintainer",
      event_id: eventId,
      method: "POST",
      occurred_at: occurredAt,
      path: `/api/v1/model-identities/${modelId}/decisions`,
      expected_http_status: 201,
      expected_response: { model_id: modelId, status: "identity_approved" },
      expected_commit_message: message,
      expected_documents: documents,
      expected_deleted_paths: [],
    }],
    event_ids: [eventId],
    model_ids: [modelId],
    alias_keys: [],
    assertions: {
      approval_view_written: true,
      closed_maintainer_pair_verified: true,
      immutable_event_written: true,
    },
    expected_commit_messages: [message],
    expected_documents: documents,
  };
}

async function requiredFixture(
  journal: StoredJournal,
): Promise<QualificationFixtureManifest> {
  const fixture = await reviewedQualificationFixtureManifest();
  if (fixture === null) {
    throw new Error("reviewed qualification fixture is not source-armed");
  }
  for (const role of ["owner", "cross_owner", "maintainer"] as const) {
    const expected = journal.acquisition.intent[role];
    const actual = fixture.intent[role];
    if (
      expected.github_id !== actual.github_id ||
      expected.login !== actual.login
    ) throw new Error("qualification intent does not match the reviewed fixture");
  }
  if (
    journal.acquisition.initial_state_commit !== fixture.state.seed_commit ||
    journal.acquisition.initial_state_tree !== fixture.state.seed_tree
  ) throw new Error("qualification State does not match the reviewed fixture");
  return fixture;
}

function fixtureModelView(
  fixture: QualificationFixtureManifest,
  modelId: string,
): ModelIdentityView {
  const document = fixture.documents.find(
    (candidate) => candidate.path === modelIdentityPath(modelId),
  );
  if (document?.kind !== "model_identity_view") {
    throw new Error("qualification fixture model view is unavailable");
  }
  return document.value as ModelIdentityView;
}

function fixtureReverseImpact(
  fixture: QualificationFixtureManifest,
  modelId: string,
): ModelIdentityReverseImpactView {
  const document = fixture.documents.find(
    (candidate) => candidate.path === modelIdentityReverseImpactPath(modelId),
  );
  if (document?.kind !== "reverse_impact_view") {
    throw new Error("qualification fixture reverse impact is unavailable");
  }
  return document.value as ModelIdentityReverseImpactView;
}

function fixtureAliasView(
  fixture: QualificationFixtureManifest,
  aliasKey: string,
): ModelAliasView {
  const document = fixture.documents.find(
    (candidate) => candidate.path === modelAliasPath(aliasKey),
  );
  if (document?.kind !== "model_alias_view") {
    throw new Error("qualification fixture alias view is unavailable");
  }
  return document.value as ModelAliasView;
}

async function maintainerRejectPlan(
  journal: StoredJournal,
): Promise<QualificationStepPlan> {
  const fixture = await requiredFixture(journal);
  const modelId = fixture.bindings.rejection.pending_model_id;
  const current = fixtureModelView(fixture, modelId);
  const nowMilliseconds = nextPlanMilliseconds(journal);
  const eventId = newEventId(nowMilliseconds);
  const occurredAt = canonicalMilliseconds(nowMilliseconds);
  const reasonCode = fixture.bindings.rejection.reason_code;
  const event = {
    schema_version: 1,
    event_id: eventId,
    event_type: "model_identity.rejected",
    occurred_at: occurredAt,
    subject_id: modelId,
    causation_event_id: current.request_event_id,
    actor: { kind: "system" },
    payload: {
      reviewer_login: journal.acquisition.intent.maintainer.login,
      reason_code: reasonCode,
    },
  } as const;
  const view: ModelIdentityView = {
    ...current,
    status: "rejected",
    decision_event_id: eventId,
    decided_at: occurredAt,
    reviewer_login: journal.acquisition.intent.maintainer.login,
    rejection_reason: reasonCode,
    mutation_event_id: eventId,
    resolved_model_id: null,
  };
  const documents = {
    [stateEventPath(event)]: event,
    [modelIdentityPath(modelId)]: view,
  };
  const message = `Record model identity decision ${modelId}`;
  return {
    operation: "maintainer_reject",
    route: "POST /api/v1/model-identities/{model_id}/decisions",
    actor: journal.acquisition.intent.maintainer,
    credential_roles: ["maintainer"],
    expected_http_status: 201,
    mutation_expected: true,
    api_requests: [{
      actor: journal.acquisition.intent.maintainer,
      body: { decision: "reject", reason_code: reasonCode },
      credential_role: "maintainer",
      event_id: eventId,
      method: "POST",
      occurred_at: occurredAt,
      path: `/api/v1/model-identities/${modelId}/decisions`,
      expected_http_status: 201,
      expected_response: { model_id: modelId, status: "identity_rejected" },
      expected_commit_message: message,
      expected_documents: documents,
      expected_deleted_paths: [],
    }],
    event_ids: [current.request_event_id, eventId],
    model_ids: [modelId],
    alias_keys: [],
    assertions: {
      closed_reason_code_verified: true,
      immutable_events_written: true,
      rejection_view_written: true,
    },
    expected_commit_messages: [message],
    expected_documents: documents,
  };
}

async function aliasAssignmentPlan(
  journal: StoredJournal,
): Promise<QualificationStepPlan> {
  const approvalPlan = journal.completed_plans[3];
  const modelId = approvalPlan?.model_ids[0];
  if (approvalPlan?.operation !== "maintainer_approve" || modelId === undefined) {
    throw new Error("qualification approval predecessor is unavailable");
  }
  const current = expectedModelView(approvalPlan, modelId);
  const impactValue = approvalPlan.expected_documents[
    modelIdentityReverseImpactPath(modelId)
  ];
  if (impactValue === undefined) {
    throw new Error("qualification approval reverse impact is unavailable");
  }
  const impact = impactValue as ModelIdentityReverseImpactView;
  const alias = `Qualification alias run ${journal.acquisition.run_id}`;
  const aliasKey = await modelAliasKey(journal.acquisition.intent.owner.login, alias);
  const nowMilliseconds = nextPlanMilliseconds(journal);
  const eventId = newEventId(nowMilliseconds);
  const occurredAt = canonicalMilliseconds(nowMilliseconds);
  const event = {
    schema_version: 1,
    event_id: eventId,
    event_type: "model_identity.alias_assigned",
    occurred_at: occurredAt,
    subject_id: modelId,
    causation_event_id: current.mutation_event_id,
    actor: { kind: "github", login: journal.acquisition.intent.owner.login },
    payload: { alias },
  } as const;
  const view: ModelIdentityView = { ...current, mutation_event_id: eventId };
  const aliasView = {
    schema_version: 1,
    alias_key: aliasKey,
    owner_login: journal.acquisition.intent.owner.login,
    alias,
    model_id: modelId,
    assignment_event_id: eventId,
    assigned_at: occurredAt,
    resolved_model_id: modelId,
  } as const;
  const reverseImpact = modelIdentityReverseImpactView(view, [
    ...impact.members.map((member) =>
      member.kind === "identity" && member.model_id === modelId
        ? { ...member, mutation_event_id: eventId }
        : member),
    {
      kind: "alias",
      alias_key: aliasKey,
      assignment_event_id: eventId,
      model_id: modelId,
      view_path: modelAliasPath(aliasKey),
    },
  ]);
  const documents = {
    [stateEventPath(event)]: event,
    [modelIdentityPath(modelId)]: view,
    [modelAliasPath(aliasKey)]: aliasView,
    [modelIdentityReverseImpactPath(modelId)]: reverseImpact,
  };
  const message = `Assign model alias ${aliasKey}`;
  return {
    operation: "alias_assignment",
    route: "POST /api/v1/model-identities/{model_id}/aliases",
    actor: journal.acquisition.intent.owner,
    credential_roles: ["agent_owner"],
    expected_http_status: 201,
    mutation_expected: true,
    api_requests: [{
      actor: journal.acquisition.intent.owner,
      body: { alias },
      credential_role: "agent_owner",
      event_id: eventId,
      method: "POST",
      occurred_at: occurredAt,
      path: `/api/v1/model-identities/${modelId}/aliases`,
      expected_http_status: 201,
      expected_response: {
        alias_key: aliasKey,
        model_id: modelId,
        status: "alias_assigned",
      },
      expected_commit_message: message,
      expected_documents: documents,
      expected_deleted_paths: [],
    }],
    event_ids: [eventId],
    model_ids: [modelId],
    alias_keys: [aliasKey],
    assertions: {
      alias_reservation_written: true,
      immutable_event_written: true,
      reverse_impact_updated: true,
    },
    expected_commit_messages: [message],
    expected_documents: documents,
  };
}

function identityRenamePlan(journal: StoredJournal): QualificationStepPlan {
  const aliasPlan = journal.completed_plans[5];
  const modelId = aliasPlan?.model_ids[0];
  if (aliasPlan?.operation !== "alias_assignment" || modelId === undefined) {
    throw new Error("qualification alias predecessor is unavailable");
  }
  const current = expectedModelView(aliasPlan, modelId);
  const impactValue = aliasPlan.expected_documents[
    modelIdentityReverseImpactPath(modelId)
  ];
  if (impactValue === undefined) {
    throw new Error("qualification alias reverse impact is unavailable");
  }
  const impact = impactValue as ModelIdentityReverseImpactView;
  const displayName = `Qualification renamed model run ${journal.acquisition.run_id}`;
  const nowMilliseconds = nextPlanMilliseconds(journal);
  const eventId = newEventId(nowMilliseconds);
  const occurredAt = canonicalMilliseconds(nowMilliseconds);
  const event = {
    schema_version: 1,
    event_id: eventId,
    event_type: "model_identity.renamed",
    occurred_at: occurredAt,
    subject_id: modelId,
    causation_event_id: current.mutation_event_id,
    actor: { kind: "github", login: journal.acquisition.intent.owner.login },
    payload: { display_name: displayName },
  } as const;
  const view: ModelIdentityView = {
    ...current,
    display_name: displayName,
    mutation_event_id: eventId,
  };
  const reverseImpact = modelIdentityReverseImpactView(view, impact.members.map(
    (member) => member.kind === "identity" && member.model_id === modelId
      ? { ...member, mutation_event_id: eventId }
      : member,
  ));
  const documents = {
    [stateEventPath(event)]: event,
    [modelIdentityPath(modelId)]: view,
    [modelIdentityReverseImpactPath(modelId)]: reverseImpact,
  };
  const message = `Rename model identity ${modelId}`;
  return {
    operation: "identity_rename",
    route: "PUT /api/v1/model-identities/{model_id}/name",
    actor: journal.acquisition.intent.owner,
    credential_roles: ["oauth_owner"],
    expected_http_status: 201,
    mutation_expected: true,
    api_requests: [{
      actor: journal.acquisition.intent.owner,
      body: { display_name: displayName },
      credential_role: "oauth_owner",
      event_id: eventId,
      method: "PUT",
      occurred_at: occurredAt,
      path: `/api/v1/model-identities/${modelId}/name`,
      expected_http_status: 201,
      expected_response: { model_id: modelId, status: "identity_renamed" },
      expected_commit_message: message,
      expected_documents: documents,
      expected_deleted_paths: [],
    }],
    event_ids: [eventId],
    model_ids: [modelId],
    alias_keys: [],
    assertions: {
      immutable_event_written: true,
      reverse_impact_updated: true,
      view_renamed: true,
    },
    expected_commit_messages: [message],
    expected_documents: documents,
  };
}

async function completeGraphConsolidationPlan(
  journal: StoredJournal,
): Promise<QualificationStepPlan> {
  const fixture = await requiredFixture(journal);
  const renamePlan = journal.completed_plans[6];
  const aliasPlan = journal.completed_plans[5];
  const sourceModelId = renamePlan?.model_ids[0];
  const aliasKey = aliasPlan?.alias_keys[0];
  const targetModelId = fixture.bindings.chain.first_target_model_id;
  if (
    renamePlan?.operation !== "identity_rename" ||
    aliasPlan?.operation !== "alias_assignment" ||
    sourceModelId === undefined ||
    aliasKey === undefined
  ) throw new Error("qualification consolidation predecessor is unavailable");
  const source = expectedModelView(renamePlan, sourceModelId);
  const sourceImpactValue = renamePlan.expected_documents[
    modelIdentityReverseImpactPath(sourceModelId)
  ];
  const sourceAliasValue = aliasPlan.expected_documents[modelAliasPath(aliasKey)];
  if (sourceImpactValue === undefined || sourceAliasValue === undefined) {
    throw new Error("qualification consolidation source component is unavailable");
  }
  const sourceImpact = sourceImpactValue as ModelIdentityReverseImpactView;
  const sourceAlias = sourceAliasValue as ModelAliasView;
  const target = fixtureModelView(fixture, targetModelId);
  const targetImpact = fixtureReverseImpact(fixture, targetModelId);
  const nowMilliseconds = nextPlanMilliseconds(journal);
  const eventId = newEventId(nowMilliseconds);
  const occurredAt = canonicalMilliseconds(nowMilliseconds);
  const event = {
    schema_version: 1,
    event_id: eventId,
    event_type: "model_identity.consolidated",
    occurred_at: occurredAt,
    subject_id: sourceModelId,
    causation_event_id: source.mutation_event_id,
    actor: { kind: "github", login: journal.acquisition.intent.owner.login },
    payload: { target_model_id: targetModelId },
  } as const;
  const sourceView: ModelIdentityView = {
    ...source,
    status: "consolidated",
    mutation_event_id: eventId,
    consolidated_into: targetModelId,
    resolved_model_id: targetModelId,
  };
  const aliasView: ModelAliasView = {
    ...sourceAlias,
    resolved_model_id: targetModelId,
  };
  const movedMembers = sourceImpact.members.map((member) =>
    member.kind === "identity" && member.model_id === sourceModelId
      ? { ...member, mutation_event_id: eventId }
      : member);
  const targetReverseImpact = modelIdentityReverseImpactView(target, [
    ...targetImpact.members,
    ...movedMembers,
  ]);
  const documents = {
    [stateEventPath(event)]: event,
    [modelIdentityPath(sourceModelId)]: sourceView,
    [modelAliasPath(aliasKey)]: aliasView,
    [modelIdentityReverseImpactPath(targetModelId)]: targetReverseImpact,
  };
  const message = `Consolidate model identity ${sourceModelId} into ${targetModelId}`;
  return {
    operation: "complete_graph_consolidation",
    route: "POST /api/v1/model-identities/{model_id}/consolidations",
    actor: journal.acquisition.intent.owner,
    credential_roles: ["agent_owner"],
    expected_http_status: 201,
    mutation_expected: true,
    api_requests: [{
      actor: journal.acquisition.intent.owner,
      body: { target_model_id: targetModelId },
      credential_role: "agent_owner",
      event_id: eventId,
      method: "POST",
      occurred_at: occurredAt,
      path: `/api/v1/model-identities/${sourceModelId}/consolidations`,
      expected_http_status: 201,
      expected_response: {
        model_id: sourceModelId,
        target_model_id: targetModelId,
        status: "identity_consolidated",
      },
      expected_commit_message: message,
      expected_documents: documents,
      expected_deleted_paths: [modelIdentityReverseImpactPath(sourceModelId)],
    }],
    event_ids: [eventId],
    model_ids: [sourceModelId, targetModelId],
    alias_keys: [],
    assertions: {
      all_reverse_impacts_retargeted: true,
      immutable_event_written: true,
      source_component_deleted: true,
    },
    expected_commit_messages: [message],
    expected_documents: documents,
  };
}

async function chainedTerminalRetryPlan(
  journal: StoredJournal,
): Promise<QualificationStepPlan> {
  const fixture = await requiredFixture(journal);
  const firstConsolidation = journal.completed_plans[7];
  const aliasPlan = journal.completed_plans[5];
  const sourceModelId = firstConsolidation?.model_ids[0];
  const firstTargetModelId = firstConsolidation?.model_ids[1];
  const secondTargetModelId = fixture.bindings.chain.second_target_model_id;
  const aliasKey = aliasPlan?.alias_keys[0];
  const firstRequest = firstConsolidation?.api_requests[0];
  if (
    firstConsolidation?.operation !== "complete_graph_consolidation" ||
    aliasPlan?.operation !== "alias_assignment" ||
    sourceModelId === undefined ||
    firstTargetModelId === undefined ||
    aliasKey === undefined ||
    firstRequest === undefined
  ) throw new Error("qualification chained predecessor is unavailable");
  const sourceView = expectedModelView(firstConsolidation, sourceModelId);
  const aliasValue = firstConsolidation.expected_documents[modelAliasPath(aliasKey)];
  const firstImpactValue = firstConsolidation.expected_documents[
    modelIdentityReverseImpactPath(firstTargetModelId)
  ];
  if (aliasValue === undefined || firstImpactValue === undefined) {
    throw new Error("qualification chained source component is unavailable");
  }
  const sourceAlias = aliasValue as ModelAliasView;
  const firstImpact = firstImpactValue as ModelIdentityReverseImpactView;
  const firstTarget = fixtureModelView(fixture, firstTargetModelId);
  const secondTarget = fixtureModelView(fixture, secondTargetModelId);
  const secondImpact = fixtureReverseImpact(fixture, secondTargetModelId);
  const firstMilliseconds = nextPlanMilliseconds(journal);
  const firstEventId = newEventId(firstMilliseconds);
  const firstOccurredAt = canonicalMilliseconds(firstMilliseconds);
  const displayName = `Qualification chain target run ${journal.acquisition.run_id}`;
  const renameEvent = {
    schema_version: 1,
    event_id: firstEventId,
    event_type: "model_identity.renamed",
    occurred_at: firstOccurredAt,
    subject_id: secondTargetModelId,
    causation_event_id: secondTarget.mutation_event_id,
    actor: { kind: "github", login: journal.acquisition.intent.owner.login },
    payload: { display_name: displayName },
  } as const;
  const renamedTarget: ModelIdentityView = {
    ...secondTarget,
    display_name: displayName,
    mutation_event_id: firstEventId,
  };
  const renamedImpact = modelIdentityReverseImpactView(
    renamedTarget,
    secondImpact.members.map((member) =>
      member.kind === "identity" && member.model_id === secondTargetModelId
        ? { ...member, mutation_event_id: firstEventId }
        : member),
  );
  const renameDocuments = {
    [stateEventPath(renameEvent)]: renameEvent,
    [modelIdentityPath(secondTargetModelId)]: renamedTarget,
    [modelIdentityReverseImpactPath(secondTargetModelId)]: renamedImpact,
  };
  const renameMessage = `Rename model identity ${secondTargetModelId}`;

  const secondMilliseconds = firstMilliseconds + 1;
  const secondEventId = newEventId(secondMilliseconds);
  const secondOccurredAt = canonicalMilliseconds(secondMilliseconds);
  const consolidationEvent = {
    schema_version: 1,
    event_id: secondEventId,
    event_type: "model_identity.consolidated",
    occurred_at: secondOccurredAt,
    subject_id: firstTargetModelId,
    causation_event_id: firstTarget.mutation_event_id,
    actor: { kind: "github", login: journal.acquisition.intent.owner.login },
    payload: { target_model_id: secondTargetModelId },
  } as const;
  const consolidatedFirstTarget: ModelIdentityView = {
    ...firstTarget,
    status: "consolidated",
    mutation_event_id: secondEventId,
    consolidated_into: secondTargetModelId,
    resolved_model_id: secondTargetModelId,
  };
  const movedSource: ModelIdentityView = {
    ...sourceView,
    resolved_model_id: secondTargetModelId,
  };
  const movedAlias: ModelAliasView = {
    ...sourceAlias,
    resolved_model_id: secondTargetModelId,
  };
  const movedMembers = firstImpact.members.map((member) =>
    member.kind === "identity" && member.model_id === firstTargetModelId
      ? { ...member, mutation_event_id: secondEventId }
      : member);
  const finalImpact = modelIdentityReverseImpactView(renamedTarget, [
    ...renamedImpact.members,
    ...movedMembers,
  ]);
  const consolidationDocuments = {
    [stateEventPath(consolidationEvent)]: consolidationEvent,
    [modelIdentityPath(firstTargetModelId)]: consolidatedFirstTarget,
    [modelIdentityPath(sourceModelId)]: movedSource,
    [modelAliasPath(aliasKey)]: movedAlias,
    [modelIdentityReverseImpactPath(secondTargetModelId)]: finalImpact,
  };
  const consolidationMessage =
    `Consolidate model identity ${firstTargetModelId} into ${secondTargetModelId}`;
  const requests: QualificationApiRequestPlan[] = [{
    actor: journal.acquisition.intent.owner,
    body: { display_name: displayName },
    credential_role: "agent_owner",
    event_id: firstEventId,
    method: "PUT",
    occurred_at: firstOccurredAt,
    path: `/api/v1/model-identities/${secondTargetModelId}/name`,
    expected_http_status: 201,
    expected_response: {
      model_id: secondTargetModelId,
      status: "identity_renamed",
    },
    expected_commit_message: renameMessage,
    expected_documents: renameDocuments,
    expected_deleted_paths: [],
  }, {
    actor: journal.acquisition.intent.owner,
    body: { target_model_id: secondTargetModelId },
    credential_role: "agent_owner",
    event_id: secondEventId,
    method: "POST",
    occurred_at: secondOccurredAt,
    path: `/api/v1/model-identities/${firstTargetModelId}/consolidations`,
    expected_http_status: 201,
    expected_response: {
      model_id: firstTargetModelId,
      target_model_id: secondTargetModelId,
      status: "identity_consolidated",
    },
    expected_commit_message: consolidationMessage,
    expected_documents: consolidationDocuments,
    expected_deleted_paths: [modelIdentityReverseImpactPath(firstTargetModelId)],
  }, {
    ...firstRequest,
    expected_http_status: 200,
    expected_response: {
      model_id: sourceModelId,
      target_model_id: firstTargetModelId,
      status: "identity_already_consolidated",
    },
    expected_commit_message: null,
    expected_documents: {},
    expected_deleted_paths: [],
  }];
  return {
    operation: "chained_terminal_retry",
    route: "POST /api/v1/model-identities/{model_id}/consolidations",
    actor: journal.acquisition.intent.owner,
    credential_roles: ["agent_owner"],
    expected_http_status: 200,
    mutation_expected: true,
    api_requests: requests,
    event_ids: [firstEventId, secondEventId],
    model_ids: [sourceModelId, firstTargetModelId, secondTargetModelId],
    alias_keys: [],
    assertions: {
      later_chain_created: true,
      retry_created_no_event: true,
      retry_resolved_current_terminal: true,
    },
    expected_commit_messages: [renameMessage, consolidationMessage],
    expected_documents: { ...renameDocuments, ...consolidationDocuments },
  };
}

async function componentCapRefusalPlan(
  journal: StoredJournal,
): Promise<QualificationStepPlan> {
  const fixture = await requiredFixture(journal);
  const sourceModelId = fixture.bindings.cap_refusal.source_terminal_model_id;
  const targetModelId = fixture.bindings.cap_refusal.target_terminal_model_id;
  const nowMilliseconds = nextPlanMilliseconds(journal);
  const eventId = newEventId(nowMilliseconds);
  const request: QualificationApiRequestPlan = {
    actor: journal.acquisition.intent.owner,
    body: { target_model_id: targetModelId },
    credential_role: "oauth_owner",
    event_id: eventId,
    method: "POST",
    occurred_at: canonicalMilliseconds(nowMilliseconds),
    path: `/api/v1/model-identities/${sourceModelId}/consolidations`,
    expected_http_status: 409,
    expected_response: { error: "idempotency_conflict" },
    expected_commit_message: null,
    expected_documents: {},
    expected_deleted_paths: [],
  };
  return {
    operation: "component_cap_refusal",
    route: "POST /api/v1/model-identities/{model_id}/consolidations",
    actor: journal.acquisition.intent.owner,
    credential_roles: ["oauth_owner"],
    expected_http_status: 409,
    mutation_expected: false,
    api_requests: [request],
    event_ids: [],
    model_ids: [sourceModelId, targetModelId],
    alias_keys: [],
    assertions: {
      candidate_union_count_is_33: true,
      component_cap_is_32: true,
      no_git_object_created: true,
    },
    expected_commit_messages: [],
    expected_documents: {},
  };
}

function idempotentRetryPlan(journal: StoredJournal): QualificationStepPlan {
  const ownerPlan = journal.completed_plans[2];
  const request = ownerPlan?.api_requests[0];
  const modelId = ownerPlan?.model_ids[0];
  if (ownerPlan?.operation !== "owner_request" || request === undefined || modelId === undefined) {
    throw new Error("qualification idempotent predecessor is unavailable");
  }
  return {
    operation: "idempotent_retry",
    route: "POST /api/v1/model-identities",
    actor: journal.acquisition.intent.owner,
    credential_roles: ["oauth_owner"],
    expected_http_status: 200,
    mutation_expected: false,
    api_requests: [{
      ...request,
      expected_http_status: 200,
      expected_response: { model_id: modelId, status: "identity_already_requested" },
      expected_commit_message: null,
      expected_documents: {},
      expected_deleted_paths: [],
    }],
    event_ids: [request.event_id],
    model_ids: [modelId],
    alias_keys: [],
    assertions: {
      event_payload_byte_equal: true,
      existing_event_reused: true,
      no_git_object_created: true,
    },
    expected_commit_messages: [],
    expected_documents: {},
  };
}

function crossRouteCollisionPlan(journal: StoredJournal): QualificationStepPlan {
  const ownerPlan = journal.completed_plans[2];
  const request = ownerPlan?.api_requests[0];
  const modelId = ownerPlan?.model_ids[0];
  if (ownerPlan?.operation !== "owner_request" || request === undefined || modelId === undefined) {
    throw new Error("qualification collision predecessor is unavailable");
  }
  return {
    operation: "cross_route_event_collision",
    route: "PUT /api/v1/model-identities/{model_id}/name",
    actor: journal.acquisition.intent.owner,
    credential_roles: ["oauth_owner"],
    expected_http_status: 409,
    mutation_expected: false,
    api_requests: [{
      actor: journal.acquisition.intent.owner,
      body: { display_name: `Qualification collision run ${journal.acquisition.run_id}` },
      credential_role: "oauth_owner",
      event_id: request.event_id,
      method: "PUT",
      occurred_at: request.occurred_at,
      path: `/api/v1/model-identities/${modelId}/name`,
      expected_http_status: 409,
      expected_response: { error: "idempotency_conflict" },
      expected_commit_message: null,
      expected_documents: {},
      expected_deleted_paths: [],
    }],
    event_ids: [request.event_id],
    model_ids: [modelId],
    alias_keys: [],
    assertions: {
      event_route_family_mismatch: true,
      immutable_event_preserved: true,
      no_git_object_created: true,
    },
    expected_commit_messages: [],
    expected_documents: {},
  };
}

function crossOwnerDenialPlan(journal: StoredJournal): QualificationStepPlan {
  const ownerPlan = journal.completed_plans[2];
  const modelId = ownerPlan?.model_ids[0];
  if (ownerPlan?.operation !== "owner_request" || modelId === undefined) {
    throw new Error("qualification cross-owner predecessor is unavailable");
  }
  const nowMilliseconds = nextPlanMilliseconds(journal);
  return {
    operation: "cross_owner_denial",
    route: "PUT /api/v1/model-identities/{model_id}/name",
    actor: journal.acquisition.intent.cross_owner,
    credential_roles: ["cross_owner"],
    expected_http_status: 404,
    mutation_expected: false,
    api_requests: [{
      actor: journal.acquisition.intent.cross_owner,
      body: { display_name: `Qualification denied rename run ${journal.acquisition.run_id}` },
      credential_role: "cross_owner",
      event_id: newEventId(nowMilliseconds),
      method: "PUT",
      occurred_at: canonicalMilliseconds(nowMilliseconds),
      path: `/api/v1/model-identities/${modelId}/name`,
      expected_http_status: 404,
      expected_response: { error: "not_found" },
      expected_commit_message: null,
      expected_documents: {},
      expected_deleted_paths: [],
    }],
    event_ids: [],
    model_ids: [modelId],
    alias_keys: [],
    assertions: {
      distinct_signed_owner_verified: true,
      no_git_object_created: true,
      target_owner_not_disclosed: true,
    },
    expected_commit_messages: [],
    expected_documents: {},
  };
}

async function maximalContentionPlan(
  journal: StoredJournal,
): Promise<QualificationStepPlan> {
  const fixture = await requiredFixture(journal);
  const sourceModelId = fixture.bindings.contention.source_terminal_model_id;
  const targetModelId = fixture.bindings.contention.target_terminal_model_id;
  const source = fixtureModelView(fixture, sourceModelId);
  const target = fixtureModelView(fixture, targetModelId);
  const sourceImpact = fixtureReverseImpact(fixture, sourceModelId);
  const targetImpact = fixtureReverseImpact(fixture, targetModelId);
  const nowMilliseconds = nextPlanMilliseconds(journal);
  const eventId = newEventId(nowMilliseconds);
  const occurredAt = canonicalMilliseconds(nowMilliseconds);
  const event = {
    schema_version: 1,
    event_id: eventId,
    event_type: "model_identity.consolidated",
    occurred_at: occurredAt,
    subject_id: sourceModelId,
    causation_event_id: source.mutation_event_id,
    actor: { kind: "github", login: journal.acquisition.intent.owner.login },
    payload: { target_model_id: targetModelId },
  } as const;
  const documents: Record<string, QualificationJson> = {
    [stateEventPath(event)]: event,
  };
  const movedMembers = [] as ModelIdentityReverseImpactView["members"][number][];
  for (const member of sourceImpact.members) {
    if (member.kind === "identity") {
      const current = fixtureModelView(fixture, member.model_id);
      const next: ModelIdentityView = member.model_id === sourceModelId
        ? {
            ...current,
            status: "consolidated",
            mutation_event_id: eventId,
            consolidated_into: targetModelId,
            resolved_model_id: targetModelId,
          }
        : { ...current, resolved_model_id: targetModelId };
      documents[member.view_path] = next;
      movedMembers.push(member.model_id === sourceModelId
        ? { ...member, mutation_event_id: eventId }
        : member);
    } else {
      const current = fixtureAliasView(fixture, member.alias_key);
      documents[member.view_path] = {
        ...current,
        resolved_model_id: targetModelId,
      };
      movedMembers.push(member);
    }
  }
  const mergedImpact = modelIdentityReverseImpactView(target, [
    ...targetImpact.members,
    ...movedMembers,
  ]);
  documents[modelIdentityReverseImpactPath(targetModelId)] = mergedImpact;
  const message = `Consolidate model identity ${sourceModelId} into ${targetModelId}`;
  const expectedStatePrefix: {
    expected_message: string;
    expected_documents: Readonly<Record<string, QualificationJson>>;
    expected_deleted_paths: readonly string[];
    expected_tree_unchanged: boolean;
  }[] = Array.from({ length: 7 }, (_, index) => ({
    expected_message:
      `Model identity qualification collision ${journal.journal_id} revision ${String(journal.journal_revision)} attempt ${String(index + 1)}`,
    expected_documents: {},
    expected_deleted_paths: [],
    expected_tree_unchanged: true,
  }));
  expectedStatePrefix.push({
    expected_message: message,
    expected_documents: documents,
    expected_deleted_paths: [modelIdentityReverseImpactPath(sourceModelId)],
    expected_tree_unchanged: false,
  });
  return {
    operation: "maximal_contention_measurement",
    route: "POST /api/v1/model-identities/{model_id}/consolidations",
    actor: journal.acquisition.intent.owner,
    credential_roles: ["agent_owner"],
    expected_http_status: 201,
    mutation_expected: true,
    api_requests: [{
      actor: journal.acquisition.intent.owner,
      body: { target_model_id: targetModelId },
      credential_role: "agent_owner",
      event_id: eventId,
      method: "POST",
      occurred_at: occurredAt,
      path: `/api/v1/model-identities/${sourceModelId}/consolidations`,
      expected_http_status: 201,
      expected_response: {
        model_id: sourceModelId,
        target_model_id: targetModelId,
        status: "identity_consolidated",
      },
      expected_commit_message: message,
      expected_documents: documents,
      expected_deleted_paths: [modelIdentityReverseImpactPath(sourceModelId)],
    }],
    event_ids: [eventId],
    model_ids: [sourceModelId, targetModelId],
    alias_keys: [],
    assertions: {
      eight_cas_attempts_executed: true,
      network_subrequests_measured: true,
      successful_final_cas: true,
    },
    expected_commit_messages: [message],
    expected_documents: documents,
    expected_state_prefix: expectedStatePrefix,
  };
}

async function createPlan(
  journal: StoredJournal,
  operation: QualificationOperation,
): Promise<QualificationStepPlan> {
  if (operation === "oauth_session_identity" || operation === "agent_session_identity") {
    return sessionPlan(operation, journal.acquisition.intent.owner);
  }
  if (operation === "owner_request") {
    return ownerRequestPlan(journal.acquisition, Date.now());
  }
  if (operation === "maintainer_approve") {
    return maintainerApprovePlan(journal);
  }
  if (operation === "maintainer_reject") {
    return maintainerRejectPlan(journal);
  }
  if (operation === "alias_assignment") {
    return aliasAssignmentPlan(journal);
  }
  if (operation === "identity_rename") {
    return identityRenamePlan(journal);
  }
  if (operation === "complete_graph_consolidation") {
    return completeGraphConsolidationPlan(journal);
  }
  if (operation === "chained_terminal_retry") {
    return chainedTerminalRetryPlan(journal);
  }
  if (operation === "component_cap_refusal") {
    return componentCapRefusalPlan(journal);
  }
  if (operation === "idempotent_retry") {
    return idempotentRetryPlan(journal);
  }
  if (operation === "cross_route_event_collision") {
    return crossRouteCollisionPlan(journal);
  }
  if (operation === "cross_owner_denial") {
    return crossOwnerDenialPlan(journal);
  }
  return maximalContentionPlan(journal);
}

async function journalId(acquisition: QualificationAcquisition): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(
      `lean-eval-model-identity-qualification-journal-v3\0${canonicalQualificationValue(acquisition)}`,
    ),
  );
  return `mqj_${[...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

function recoveryNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function status(journal: StoredJournal): QualificationJournalStatus {
  const restoration = journal.restoration;
  return {
    schema_version: SCHEMA_VERSION,
    status: "model_identity_qualification_journal",
    environment: "staging",
    deployed_commit: journal.acquisition.deployed_commit,
    run_id: journal.acquisition.run_id,
    run_attempt: journal.acquisition.run_attempt,
    journal_id: journal.journal_id,
    journal_revision: journal.journal_revision,
    initial_state_commit: journal.acquisition.initial_state_commit,
    initial_state_tree: journal.acquisition.initial_state_tree,
    current_state_commit: journal.current_state_commit,
    current_state_tree: journal.current_state_tree,
    fixture_evidence_class: journal.acquisition.fixture_evidence.evidence_class,
    fixture_id: journal.acquisition.fixture_evidence.fixture_id,
    fixture_manifest_digest: journal.acquisition.fixture_evidence.manifest_digest,
    recovery_reconciliations: journal.recovery_reconciliations,
    lease_status: journal.lease_status,
    lease_released: journal.lease_status === "restored",
    owner_api_enabled: false,
    maintainer_api_enabled: false,
    foreign_commit_observed: false,
    restoration_commit: restoration?.restoration_commit ?? null,
    restoration_parent_commit: restoration?.restoration_parent_commit ?? null,
    restoration_parent_tree: restoration?.restoration_parent_tree ?? null,
    restoration_tree: restoration?.restoration_tree ?? null,
    restoration_fast_forward: restoration !== null,
    restoration_tree_equal:
      restoration?.restoration_tree === journal.acquisition.initial_state_tree,
  };
}

export class ModelIdentityQualificationJournal extends DurableObject {
  constructor(ctx: DurableObjectState, env: CloudflareEnv) {
    super(ctx, env);
    this.ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS qualification_journals (run_id TEXT PRIMARY KEY, body TEXT NOT NULL)",
    );
    this.ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS qualification_step_receipts (run_id TEXT NOT NULL, reserved_revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY (run_id, reserved_revision))",
    );
    this.ctx.storage.sql.exec(
      "CREATE UNIQUE INDEX IF NOT EXISTS one_active_qualification ON qualification_journals ((1)) WHERE json_extract(body, '$.lease_status') = 'active'",
    );
    this.ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS qualification_verification_lease (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), body TEXT NOT NULL, created_at INTEGER NOT NULL)",
    );
  }

  async beginAcquisitionVerification(value: unknown): Promise<string> {
    const acquisition = canonicalAcquisition(value);
    const identifier = await journalId(acquisition);
    return this.ctx.storage.transaction(async () => {
      const existing = this.read(acquisition.run_id);
      if (existing !== null) {
        if (!exactJson(existing.acquisition, acquisition)) {
          throw new Error("qualification acquisition conflicts with its durable journal");
        }
        return JSON.stringify({ kind: "active", journal: status(existing) });
      }
      const verification = this.verification();
      if (verification !== null) {
        if (!exactJson(verification.acquisition, acquisition)) {
          throw new Error("another model identity qualification holds the staging verification lease");
        }
      } else {
        if (this.active() !== null) {
          throw new Error("another model identity qualification holds the staging lease");
        }
        this.ctx.storage.sql.exec(
          "INSERT INTO qualification_verification_lease (singleton, body, created_at) VALUES (1, ?, ?)",
          JSON.stringify({ acquisition, journal_id: identifier }),
          Date.now(),
        );
      }
      await this.ctx.storage.setAlarm(Date.now() + ACTIVE_LEASE_ALARM_MS);
      return JSON.stringify({ kind: "verifying", journal_id: identifier });
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async activateVerifiedAcquisition(value: unknown): Promise<QualificationJournalStatus> {
    const acquisition = canonicalAcquisition(value);
    return this.ctx.storage.transactionSync(() => {
      const verification = this.verification();
      if (
        verification === null ||
        !exactJson(verification.acquisition, acquisition)
      ) throw new Error("qualification acquisition verification lease is unavailable");
      const next: StoredJournal = {
        acquisition,
        journal_id: verification.journal_id,
        journal_revision: 1,
        current_state_commit: acquisition.initial_state_commit,
        current_state_tree: acquisition.initial_state_tree,
        lease_status: "active",
        pending_step: null,
        completed_plans: [],
        recovery_reconciliations: [],
        recovery_nonce: recoveryNonce(),
        restoration: null,
      };
      this.write(next);
      this.ctx.storage.sql.exec(
        "DELETE FROM qualification_verification_lease WHERE singleton = 1",
      );
      return status(next);
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async failAcquisitionVerification(value: unknown): Promise<void> {
    const acquisition = canonicalAcquisition(value);
    this.ctx.storage.transactionSync(() => {
      const verification = this.verification();
      if (
        verification !== null &&
        exactJson(verification.acquisition, acquisition)
      ) {
        this.ctx.storage.sql.exec(
          "DELETE FROM qualification_verification_lease WHERE singleton = 1",
        );
      }
    });
  }

  async acquire(value: unknown): Promise<QualificationJournalStatus> {
    const acquisition = canonicalAcquisition(value);
    const identifier = await journalId(acquisition);
    const next: StoredJournal = {
      acquisition,
      journal_id: identifier,
      journal_revision: 1,
      current_state_commit: acquisition.initial_state_commit,
      current_state_tree: acquisition.initial_state_tree,
      lease_status: "active",
      pending_step: null,
      completed_plans: [],
      recovery_reconciliations: [],
      recovery_nonce: recoveryNonce(),
      restoration: null,
    };
    return this.ctx.storage.transaction(async () => {
      const existing = this.read(acquisition.run_id);
      if (existing !== null) {
        if (!exactJson(existing.acquisition, acquisition)) {
          throw new Error("qualification acquisition conflicts with its durable journal");
        }
        return status(existing);
      }
      try {
        this.write(next);
      } catch {
        throw new Error("another model identity qualification holds the staging lease");
      }
      await this.ctx.storage.setAlarm(Date.now() + ACTIVE_LEASE_ALARM_MS);
      return status(next);
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async readStatus(runId: string): Promise<QualificationJournalStatus> {
    const journal = this.required(runId);
    return status(journal);
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async readRecoveryPlan(runId: string): Promise<string> {
    const journal = this.required(runId);
    if (journal.lease_status !== "active") {
      throw new Error("qualification journal no longer requires recovery");
    }
    const plan: QualificationRecoveryPlan = {
      journal_id: journal.journal_id,
      recovery_nonce: journal.recovery_nonce,
      journal_revision: journal.journal_revision,
      initial_state_commit: journal.acquisition.initial_state_commit,
      initial_state_tree: journal.acquisition.initial_state_tree,
      current_state_commit: journal.current_state_commit,
      current_state_tree: journal.current_state_tree,
      intent: journal.acquisition.intent,
      fixture_evidence: journal.acquisition.fixture_evidence,
      pending_step: journal.pending_step,
      completed_plans: journal.completed_plans,
      recovery_reconciliations: journal.recovery_reconciliations,
    };
    return JSON.stringify(plan);
  }

  async reserveStep(
    value: unknown,
  ): Promise<QualificationStepReservationOutcome> {
    const reservation = canonicalReservation(value);
    const before = this.required(reservation.run_id);
    const completedBefore = this.readCompletion(
      reservation.run_id,
      reservation.expected_journal_revision,
    );
    if (completedBefore !== null) {
      if (!exactJson(completedBefore.reservation, reservation)) {
        throw new Error("qualification step retry conflicts with its durable receipt");
      }
      return { kind: "completed", receipt_json: JSON.stringify(completedBefore.receipt) };
    }
    this.assertReservation(before, reservation);
    const expectedOperation = QUALIFICATION_OPERATIONS[before.journal_revision - 1];
    if (expectedOperation === undefined || reservation.operation !== expectedOperation) {
      throw new Error("qualification step is outside the exact ordered proof sequence");
    }
    if (before.pending_step !== null) {
      if (
        before.pending_step.operation !== reservation.operation ||
        before.pending_step.reserved_revision !== reservation.expected_journal_revision ||
        before.pending_step.expected_state_commit !== reservation.expected_state_commit ||
        before.pending_step.expected_state_tree !== reservation.expected_state_tree
      ) {
        throw new Error("qualification journal already has another pending step");
      }
      return {
        kind: "reserved",
        journal: status(before),
        plan_json: JSON.stringify(before.pending_step.plan),
        plan_digest: before.pending_step.plan_digest,
      };
    }
    const plan = await createPlan(before, reservation.operation);
    const digest = await qualificationPlanDigest(plan);
    return this.ctx.storage.transactionSync(() => {
      const journal = this.required(reservation.run_id);
      const completed = this.readCompletion(
        reservation.run_id,
        reservation.expected_journal_revision,
      );
      if (completed !== null) {
        if (!exactJson(completed.reservation, reservation)) {
          throw new Error("qualification step retry conflicts with its durable receipt");
        }
        return { kind: "completed", receipt_json: JSON.stringify(completed.receipt) };
      }
      this.assertReservation(journal, reservation);
      const operation = QUALIFICATION_OPERATIONS[journal.journal_revision - 1];
      if (operation === undefined || operation !== reservation.operation) {
        throw new Error("qualification step is outside the exact ordered proof sequence");
      }
      const pending: PendingStep = {
        operation: reservation.operation,
        plan,
        plan_digest: digest,
        reserved_revision: reservation.expected_journal_revision,
        expected_state_commit: reservation.expected_state_commit,
        expected_state_tree: reservation.expected_state_tree,
      };
      if (journal.pending_step !== null && !exactJson(journal.pending_step, pending)) {
        throw new Error("qualification journal already has another pending step");
      }
      if (journal.pending_step === null) {
        this.write({ ...journal, pending_step: pending });
        return {
          kind: "reserved",
          journal: status(journal),
          plan_json: JSON.stringify(plan),
          plan_digest: digest,
        };
      }
      return {
        kind: "reserved",
        journal: status(journal),
        plan_json: JSON.stringify(journal.pending_step.plan),
        plan_digest: journal.pending_step.plan_digest,
      };
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async completeStep(value: unknown): Promise<string> {
    const input = record(value, "qualification step completion");
    exactFields(input, ["reservation", "state_commit", "state_tree", "receipt"], "qualification step completion");
    const reservation = canonicalReservation(input.reservation);
    if (typeof input.state_commit !== "string" || typeof input.state_tree !== "string") {
      throw new TypeError("qualification step completion State identity is invalid");
    }
    const completion: QualificationStepCompletion = {
      reservation,
      state_commit: input.state_commit,
      state_tree: input.state_tree,
      receipt: qualificationJson(input.receipt),
    };
    if (!SHA.test(completion.state_commit) || !SHA.test(completion.state_tree)) {
      throw new TypeError("qualification step completion State identity is invalid");
    }
    return this.ctx.storage.transactionSync(() => {
      const journal = this.required(completion.reservation.run_id);
      const completed = this.readCompletion(
        completion.reservation.run_id,
        completion.reservation.expected_journal_revision,
      );
      if (completed !== null) {
        if (!exactJson(completed, completion)) {
          throw new Error("qualification step completion conflicts with its durable receipt");
        }
        return JSON.stringify(completed.receipt);
      }
      this.assertReservation(journal, completion.reservation);
      if (journal.pending_step === null) {
        throw new Error("qualification step was not durably reserved");
      }
      const expectedPending: PendingStep = {
        operation: completion.reservation.operation,
        plan: journal.pending_step.plan,
        plan_digest: journal.pending_step.plan_digest,
        reserved_revision: completion.reservation.expected_journal_revision,
        expected_state_commit: completion.reservation.expected_state_commit,
        expected_state_tree: completion.reservation.expected_state_tree,
      };
      if (!exactJson(journal.pending_step, expectedPending)) {
        throw new Error("qualification step completion does not match its reservation");
      }
      const next: StoredJournal = {
        ...journal,
        journal_revision: journal.journal_revision + 1,
        current_state_commit: completion.state_commit,
        current_state_tree: completion.state_tree,
        pending_step: null,
        completed_plans: [...journal.completed_plans, journal.pending_step.plan],
      };
      this.write(next);
      this.ctx.storage.sql.exec(
        "INSERT INTO qualification_step_receipts (run_id, reserved_revision, body) VALUES (?, ?, ?)",
        completion.reservation.run_id,
        completion.reservation.expected_journal_revision,
        JSON.stringify(completion),
      );
      return JSON.stringify(completion.receipt);
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async abandonNonMutatingStep(
    runId: string,
    journalId: string,
    expectedRevision: number,
  ): Promise<QualificationJournalStatus> {
    return this.ctx.storage.transactionSync(() => {
      const journal = this.required(runId);
      if (
        journal.lease_status !== "active" ||
        journal.journal_id !== journalId ||
        journal.journal_revision !== expectedRevision ||
        journal.current_state_commit !== journal.pending_step?.expected_state_commit ||
        journal.current_state_tree !== journal.pending_step.expected_state_tree
      ) {
        throw new Error("qualification pending step cannot be abandoned safely");
      }
      const next = { ...journal, pending_step: null };
      this.write(next);
      return status(next);
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async reconcilePendingMutation(
    runId: string,
    journalId: string,
    expectedRevision: number,
    stateCommit: string,
    stateTree: string,
    appliedMutations: number,
  ): Promise<QualificationJournalStatus> {
    if (
      !SHA.test(stateCommit) ||
      !SHA.test(stateTree) ||
      !Number.isSafeInteger(appliedMutations) ||
      appliedMutations < 1
    ) {
      throw new TypeError("qualification reconciled State identity is invalid");
    }
    return this.ctx.storage.transactionSync(() => {
      const journal = this.required(runId);
      const pending = journal.pending_step;
      if (
        journal.lease_status !== "active" ||
        journal.journal_id !== journalId ||
        journal.journal_revision !== expectedRevision ||
        pending?.expected_state_commit !== journal.current_state_commit ||
        pending.expected_state_tree !== journal.current_state_tree ||
        stateCommit === journal.current_state_commit
      ) {
        throw new Error("qualification pending mutation cannot be reconciled safely");
      }
      const plannedMutations = pending.plan.expected_state_prefix?.length ??
        pending.plan.api_requests.filter(
          (request) => request.expected_commit_message !== null,
        ).length;
      if (appliedMutations > plannedMutations) {
        throw new Error("qualification recovered mutation prefix is invalid");
      }
      const reconciliation: RecoveryReconciliation = {
        operation: pending.operation,
        plan_digest: pending.plan_digest,
        applied_mutations: appliedMutations,
        planned_mutations: plannedMutations,
        state_commit: stateCommit,
        state_tree: stateTree,
      };
      const reservation: QualificationStepReservation = {
        run_id: runId,
        run_attempt: 1,
        journal_id: journalId,
        expected_journal_revision: expectedRevision,
        expected_state_commit: pending.expected_state_commit,
        expected_state_tree: pending.expected_state_tree,
        operation: pending.operation,
      };
      const receipt = {
        schema_version: 2,
        status: "model_identity_qualification_mutation_reconciled",
        journal_id: journalId,
        journal_revision: expectedRevision + 1,
        operation: pending.operation,
        plan_digest: pending.plan_digest,
        applied_mutations: appliedMutations,
        planned_mutations: plannedMutations,
        state_commit: stateCommit,
        state_tree: stateTree,
      } as const;
      const completion: QualificationStepCompletion = {
        reservation,
        state_commit: stateCommit,
        state_tree: stateTree,
        receipt,
      };
      const next: StoredJournal = {
        ...journal,
        journal_revision: journal.journal_revision + 1,
        current_state_commit: stateCommit,
        current_state_tree: stateTree,
        pending_step: null,
        completed_plans: [...journal.completed_plans, pending.plan],
        recovery_reconciliations: [
          ...journal.recovery_reconciliations,
          reconciliation,
        ],
      };
      this.write(next);
      this.ctx.storage.sql.exec(
        "INSERT INTO qualification_step_receipts (run_id, reserved_revision, body) VALUES (?, ?, ?)",
        runId,
        expectedRevision,
        JSON.stringify(completion),
      );
      return status(next);
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async completeRestoration(
    runId: string,
    expectedRevision: number,
    restoration: Restoration,
  ): Promise<QualificationJournalStatus> {
    if (
      !SHA.test(restoration.restoration_commit) ||
      !SHA.test(restoration.restoration_parent_commit) ||
      !SHA.test(restoration.restoration_parent_tree) ||
      !SHA.test(restoration.restoration_tree)
    ) {
      throw new TypeError("qualification restoration identity is invalid");
    }
    return this.ctx.storage.transactionSync(() => {
      const journal = this.required(runId);
      if (journal.lease_status === "restored") {
        if (!exactJson(journal.restoration, restoration)) {
          throw new Error("qualification restoration conflicts with its durable receipt");
        }
        return status(journal);
      }
      if (
        journal.pending_step !== null ||
        journal.journal_revision !== expectedRevision ||
        restoration.restoration_parent_commit !== journal.current_state_commit ||
        restoration.restoration_parent_tree !== journal.current_state_tree ||
        restoration.restoration_tree !== journal.acquisition.initial_state_tree ||
        restoration.restoration_commit === restoration.restoration_parent_commit
      ) {
        throw new Error("qualification restoration does not match the active journal");
      }
      const next: StoredJournal = {
        ...journal,
        journal_revision: journal.journal_revision + 1,
        current_state_commit: restoration.restoration_commit,
        current_state_tree: restoration.restoration_tree,
        lease_status: "restored",
        restoration,
      };
      this.write(next);
      return status(next);
    });
  }

  override async alarm(): Promise<void> {
    this.ctx.storage.sql.exec(
      "DELETE FROM qualification_verification_lease WHERE created_at <= ?",
      Date.now() - ACTIVE_LEASE_ALARM_MS,
    );
    const active = this.active();
    if (active === null) return;
    console.error(JSON.stringify({
      event: "model_identity_qualification_recovery_due",
      journal_id: active.journal_id,
      run_id: active.acquisition.run_id,
    }));
    await this.ctx.storage.setAlarm(Date.now() + ACTIVE_LEASE_ALARM_MS);
  }

  private assertReservation(
    journal: StoredJournal,
    reservation: QualificationStepReservation,
  ): void {
    if (
      journal.lease_status !== "active" ||
      journal.journal_id !== reservation.journal_id ||
      journal.journal_revision !== reservation.expected_journal_revision ||
      journal.current_state_commit !== reservation.expected_state_commit ||
      journal.current_state_tree !== reservation.expected_state_tree
    ) {
      throw new Error("qualification step does not match the active durable journal");
    }
  }

  private active(): StoredJournal | null {
    const row = this.ctx.storage.sql.exec<StoredRow>(
      "SELECT body FROM qualification_journals WHERE json_extract(body, '$.lease_status') = 'active' LIMIT 1",
    ).toArray()[0];
    return row === undefined ? null : JSON.parse(row.body) as StoredJournal;
  }

  private verification(): Readonly<{
    acquisition: QualificationAcquisition;
    journal_id: string;
  }> | null {
    const row = this.ctx.storage.sql.exec<VerificationRow>(
      "SELECT body, created_at FROM qualification_verification_lease WHERE singleton = 1",
    ).toArray()[0];
    if (row === undefined) return null;
    return JSON.parse(row.body) as Readonly<{
      acquisition: QualificationAcquisition;
      journal_id: string;
    }>;
  }

  private read(runId: string): StoredJournal | null {
    if (!RUN_ID.test(runId)) throw new TypeError("qualification run ID is invalid");
    const row = this.ctx.storage.sql.exec<StoredRow>(
      "SELECT body FROM qualification_journals WHERE run_id = ?",
      runId,
    ).toArray()[0];
    return row === undefined ? null : JSON.parse(row.body) as StoredJournal;
  }

  private required(runId: string): StoredJournal {
    const journal = this.read(runId);
    if (journal === null) throw new Error("qualification journal was not found");
    return journal;
  }

  private readCompletion(
    runId: string,
    reservedRevision: number,
  ): QualificationStepCompletion | null {
    const row = this.ctx.storage.sql.exec<StoredRow>(
      "SELECT body FROM qualification_step_receipts WHERE run_id = ? AND reserved_revision = ?",
      runId,
      reservedRevision,
    ).toArray()[0];
    return row === undefined ? null : JSON.parse(row.body) as QualificationStepCompletion;
  }

  private write(journal: StoredJournal): void {
    this.ctx.storage.sql.exec(
      "INSERT INTO qualification_journals (run_id, body) VALUES (?, ?) ON CONFLICT(run_id) DO UPDATE SET body = excluded.body",
      journal.acquisition.run_id,
      JSON.stringify(journal),
    );
  }
}
