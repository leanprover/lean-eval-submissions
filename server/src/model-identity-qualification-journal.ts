import { DurableObject } from "cloudflare:workers";

import {
  modelIdentityId,
  modelIdentityPath,
} from "./model-identity";
import { newEventId, stateEventPath } from "./state-event";

const SCHEMA_VERSION = 2;
const ACTIVE_LEASE_ALARM_MS = 5 * 60 * 1000;
const SHA = /^[0-9a-f]{40}$/;
const RUN_ID = /^[1-9][0-9]{0,19}$/;
const JOURNAL_ID = /^mqj_[0-9a-f]{64}$/;

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

export type QualificationAcquisition = Readonly<{
  schema_version: 2;
  run_id: string;
  run_attempt: 1;
  deployed_commit: string;
  initial_state_commit: string;
  initial_state_tree: string;
  intent: QualificationIntent;
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
    "initial_state_commit", "initial_state_tree", "intent",
  ], "qualification acquisition");
  const rawIntent = record(input.intent, "qualification intent");
  exactFields(rawIntent, ["owner", "cross_owner", "maintainer"], "qualification intent");
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
    !SHA.test(input.initial_state_tree)
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
  throw new Error(`qualification operation ${operation} does not yet have a fixed durable plan`);
}

async function journalId(acquisition: QualificationAcquisition): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(
      `lean-eval-model-identity-qualification-journal-v2\0${acquisition.run_id}\0${String(acquisition.run_attempt)}\0${acquisition.deployed_commit}`,
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
  ): Promise<QualificationJournalStatus> {
    if (!SHA.test(stateCommit) || !SHA.test(stateTree)) {
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
      const reconciliation: RecoveryReconciliation = {
        operation: pending.operation,
        plan_digest: pending.plan_digest,
        state_commit: stateCommit,
        state_tree: stateTree,
      };
      const next: StoredJournal = {
        ...journal,
        current_state_commit: stateCommit,
        current_state_tree: stateTree,
        pending_step: null,
        recovery_reconciliations: [
          ...journal.recovery_reconciliations,
          reconciliation,
        ],
      };
      this.write(next);
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
