import { canonicalStateDocument } from "./github-state";
import {
  decodeModelAliasView,
  decodeModelIdentityReverseImpactView,
  decodeModelIdentityView,
  modelAliasKey,
  modelAliasPath,
  modelIdentityId,
  modelIdentityPath,
  modelIdentityReverseImpactPath,
  type ModelAliasView,
  type ModelIdentityReverseImpactView,
  type ModelIdentityView,
} from "./model-identity";
import { stateEventPath, validateStateEvent, type StateEvent } from "./state-event";

const SHA = /^[0-9a-f]{40}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const UUID_V7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const FIXTURE_DIGEST_DOMAIN =
  "lean-eval-model-identity-staging-fixture-manifest-v1\0";
const STAGING_REPOSITORY = "leanprover/lean-eval-state-staging";
const STAGING_CONTRACT = "9fc7c431a92c678554c65ebac68d3fddf4990d29";
const LIVE_DOCUMENT_COUNT = 148;
const MAX_MANIFEST_BYTES = 256 * 1024;

type FixtureIdentity = Readonly<{ github_id: number; login: string }>;

type FixtureComponent = Readonly<{
  source_terminal_model_id: string;
  source_member_count: number;
  target_terminal_model_id: string;
  target_member_count: number;
  union_member_count: number;
}>;

export type QualificationFixtureDocument = Readonly<{
  path: string;
  kind: "event" | "model_identity_view" | "model_alias_view" | "reverse_impact_view";
  byte_length: number;
  content_sha256: string;
  git_blob_sha1: string;
  value: unknown;
}>;

export type QualificationFixtureManifest = Readonly<{
  schema_version: 1;
  kind: "model_identity_staging_qualification_fixture";
  environment: "staging";
  fixture_id: string;
  state: Readonly<{
    repository: "leanprover/lean-eval-state-staging";
    ref: "refs/heads/main";
    contract_commit: string;
    seed_parent_commit: string;
    seed_parent_tree: string;
    seed_commit: string;
    seed_tree: string;
    seed_commit_message: string;
  }>;
  intent: Readonly<{
    owner: FixtureIdentity;
    cross_owner: FixtureIdentity;
    maintainer: FixtureIdentity;
  }>;
  bindings: Readonly<{
    rejection: Readonly<{ pending_model_id: string; reason_code: string }>;
    chain: Readonly<{
      first_target_model_id: string;
      second_target_model_id: string;
    }>;
    cap_refusal: FixtureComponent;
    contention: FixtureComponent & Readonly<{
      forced_collisions: 7;
      expected_cas_attempts: 8;
    }>;
  }>;
  document_count: 148;
  documents: readonly QualificationFixtureDocument[];
}>;

// Arming is a reviewed source change: both values remain absent until the exact
// live manifest has been seeded, independently reviewed, and its digest pinned.
export const REVIEWED_QUALIFICATION_FIXTURE_MANIFEST: unknown = null;
export const REVIEWED_QUALIFICATION_FIXTURE_DIGEST: string | null = null;

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
  if (
    actual.length !== expected.length ||
    actual.some((field, index) => field !== expected[index])
  ) throw new TypeError(`${label} fields are invalid`);
}

function identity(value: unknown, label: string): FixtureIdentity {
  const input = record(value, label);
  exactFields(input, ["github_id", "login"], label);
  if (
    typeof input.github_id !== "number" ||
    !Number.isSafeInteger(input.github_id) ||
    input.github_id < 1 ||
    typeof input.login !== "string" ||
    !LOGIN.test(input.login)
  ) throw new TypeError(`${label} is invalid`);
  return { github_id: input.github_id, login: input.login };
}

async function digest(algorithm: "SHA-1" | "SHA-256", bytes: Uint8Array): Promise<string> {
  const result = new Uint8Array(await crypto.subtle.digest(algorithm, bytes));
  return [...result].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function gitBlobSha(bytes: Uint8Array): Promise<string> {
  const prefix = new TextEncoder().encode(`blob ${String(bytes.byteLength)}\0`);
  const input = new Uint8Array(prefix.byteLength + bytes.byteLength);
  input.set(prefix);
  input.set(bytes, prefix.byteLength);
  return digest("SHA-1", input);
}

export async function qualificationFixtureDigest(value: unknown): Promise<string> {
  const canonical = canonicalStateDocument(value);
  const bytes = new TextEncoder().encode(`${FIXTURE_DIGEST_DOMAIN}${canonical}`);
  return digest("SHA-256", bytes);
}

function component(value: unknown, label: string): FixtureComponent {
  const input = record(value, label);
  exactFields(input, [
    "source_member_count",
    "source_terminal_model_id",
    "target_member_count",
    "target_terminal_model_id",
    "union_member_count",
  ], label);
  if (
    typeof input.source_terminal_model_id !== "string" ||
    !/^mi1_[0-9a-f]{64}$/.test(input.source_terminal_model_id) ||
    typeof input.target_terminal_model_id !== "string" ||
    !/^mi1_[0-9a-f]{64}$/.test(input.target_terminal_model_id) ||
    input.source_terminal_model_id === input.target_terminal_model_id ||
    typeof input.source_member_count !== "number" ||
    !Number.isSafeInteger(input.source_member_count) ||
    typeof input.target_member_count !== "number" ||
    !Number.isSafeInteger(input.target_member_count) ||
    typeof input.union_member_count !== "number" ||
    !Number.isSafeInteger(input.union_member_count) ||
    input.union_member_count !== input.source_member_count + input.target_member_count
  ) throw new TypeError(`${label} is invalid`);
  return input as FixtureComponent;
}

function eventReferenceIds(
  identities: Iterable<ModelIdentityView>,
  aliases: Iterable<ModelAliasView>,
): Set<string> {
  const referenced = new Set<string>();
  for (const view of identities) {
    referenced.add(view.request_event_id);
    referenced.add(view.mutation_event_id);
    if (view.decision_event_id !== null) referenced.add(view.decision_event_id);
  }
  for (const view of aliases) referenced.add(view.assignment_event_id);
  return referenced;
}

function fixtureDecisionMatches(
  event: StateEvent | undefined,
  modelId: string,
  maintainerLogin: string,
): boolean {
  if (
    event?.event_type !== "model_identity.approved" &&
    event?.event_type !== "model_identity.rejected"
  ) return false;
  return event.subject_id === modelId &&
    event.payload.reviewer_login === maintainerLogin;
}

function requireComponent(
  impacts: ReadonlyMap<string, ModelIdentityReverseImpactView>,
  modelViews: ReadonlyMap<string, ModelIdentityView>,
  fixture: FixtureComponent,
  sourceCount: number,
  targetCount: number,
  unionCount: number,
  label: string,
): void {
  const source = impacts.get(fixture.source_terminal_model_id);
  const target = impacts.get(fixture.target_terminal_model_id);
  const sourceView = modelViews.get(fixture.source_terminal_model_id);
  const targetView = modelViews.get(fixture.target_terminal_model_id);
  if (
    fixture.source_member_count !== sourceCount ||
    fixture.target_member_count !== targetCount ||
    fixture.union_member_count !== unionCount ||
    source?.member_count !== sourceCount ||
    target?.member_count !== targetCount ||
    source.members.filter((member) => member.kind === "identity").length !== 1 ||
    source.members.filter((member) => member.kind === "alias").length !==
      sourceCount - 1 ||
    source.members.find((member) => member.kind === "identity")?.model_id !==
      fixture.source_terminal_model_id ||
    target.members.filter((member) => member.kind === "identity").length !== 1 ||
    target.members.filter((member) => member.kind === "alias").length !==
      targetCount - 1 ||
    target.members.find((member) => member.kind === "identity")?.model_id !==
      fixture.target_terminal_model_id ||
    sourceView?.status !== "approved" ||
    sourceView.resolved_model_id !== sourceView.model_id ||
    sourceView.consolidated_into !== null ||
    targetView?.status !== "approved" ||
    targetView.resolved_model_id !== targetView.model_id ||
    targetView.consolidated_into !== null
  ) throw new TypeError(`${label} topology is invalid`);
}

export async function verifyQualificationFixtureManifest(
  value: unknown,
  expectedDigest: string,
): Promise<QualificationFixtureManifest> {
  const input = record(value, "qualification fixture manifest");
  exactFields(input, [
    "bindings", "document_count", "documents", "environment", "fixture_id",
    "intent", "kind", "schema_version", "state",
  ], "qualification fixture manifest");
  const canonicalBytes = new TextEncoder().encode(canonicalStateDocument(value));
  if (
    canonicalBytes.byteLength > MAX_MANIFEST_BYTES ||
    input.schema_version !== 1 ||
    input.kind !== "model_identity_staging_qualification_fixture" ||
    input.environment !== "staging" ||
    typeof input.fixture_id !== "string" ||
    !UUID_V7.test(input.fixture_id) ||
    input.document_count !== LIVE_DOCUMENT_COUNT ||
    !Array.isArray(input.documents) ||
    input.documents.length !== LIVE_DOCUMENT_COUNT ||
    !SHA256.test(expectedDigest) ||
    await qualificationFixtureDigest(value) !== expectedDigest
  ) throw new TypeError("qualification fixture manifest is invalid");

  const state = record(input.state, "qualification fixture State binding");
  exactFields(state, [
    "contract_commit", "ref", "repository", "seed_commit",
    "seed_commit_message", "seed_parent_commit", "seed_parent_tree", "seed_tree",
  ], "qualification fixture State binding");
  if (
    state.repository !== STAGING_REPOSITORY ||
    state.ref !== "refs/heads/main" ||
    state.contract_commit !== STAGING_CONTRACT ||
    typeof state.seed_commit !== "string" || !SHA.test(state.seed_commit) ||
    typeof state.seed_tree !== "string" || !SHA.test(state.seed_tree) ||
    typeof state.seed_parent_commit !== "string" || !SHA.test(state.seed_parent_commit) ||
    typeof state.seed_parent_tree !== "string" || !SHA.test(state.seed_parent_tree) ||
    state.seed_commit === state.seed_parent_commit ||
    typeof state.seed_commit_message !== "string" ||
    state.seed_commit_message.length < 1 ||
    new TextEncoder().encode(state.seed_commit_message).byteLength > 512
  ) throw new TypeError("qualification fixture State binding is invalid");

  const rawIntent = record(input.intent, "qualification fixture intent");
  exactFields(rawIntent, ["cross_owner", "maintainer", "owner"], "qualification fixture intent");
  const intent = {
    owner: identity(rawIntent.owner, "qualification fixture owner"),
    cross_owner: identity(rawIntent.cross_owner, "qualification fixture cross-owner"),
    maintainer: identity(rawIntent.maintainer, "qualification fixture maintainer"),
  };
  const identities = Object.values(intent);
  if (
    new Set(identities.map((item) => item.github_id)).size !== 3 ||
    new Set(identities.map((item) => item.login)).size !== 3
  ) throw new TypeError("qualification fixture identities are not distinct");

  const bindings = record(input.bindings, "qualification fixture bindings");
  exactFields(bindings, [
    "cap_refusal", "chain", "contention", "rejection",
  ], "qualification fixture bindings");
  const rejection = record(bindings.rejection, "qualification rejection fixture");
  exactFields(rejection, ["pending_model_id", "reason_code"], "qualification rejection fixture");
  const chain = record(bindings.chain, "qualification chain fixture");
  exactFields(chain, [
    "first_target_model_id", "second_target_model_id",
  ], "qualification chain fixture");
  if (
    typeof rejection.pending_model_id !== "string" ||
    !/^mi1_[0-9a-f]{64}$/.test(rejection.pending_model_id) ||
    typeof rejection.reason_code !== "string" ||
    !/^[a-z][a-z0-9_]{1,63}$/.test(rejection.reason_code) ||
    typeof chain.first_target_model_id !== "string" ||
    !/^mi1_[0-9a-f]{64}$/.test(chain.first_target_model_id) ||
    typeof chain.second_target_model_id !== "string" ||
    !/^mi1_[0-9a-f]{64}$/.test(chain.second_target_model_id) ||
    chain.first_target_model_id === chain.second_target_model_id
  ) throw new TypeError("qualification fixture bindings are invalid");
  const capRefusal = component(bindings.cap_refusal, "qualification cap fixture");
  const rawContention = record(bindings.contention, "qualification contention fixture");
  exactFields(rawContention, [
    "expected_cas_attempts", "forced_collisions", "source_member_count",
    "source_terminal_model_id", "target_member_count", "target_terminal_model_id",
    "union_member_count",
  ], "qualification contention fixture");
  const contention = component(
    Object.fromEntries(Object.entries(rawContention).filter(
      ([key]) => key !== "forced_collisions" && key !== "expected_cas_attempts")),
    "qualification contention fixture",
  );
  if (rawContention.forced_collisions !== 7 || rawContention.expected_cas_attempts !== 8) {
    throw new TypeError("qualification contention fixture is invalid");
  }

  const events = new Map<string, StateEvent>();
  const modelViews = new Map<string, ModelIdentityView>();
  const aliasViews = new Map<string, ModelAliasView>();
  const impacts = new Map<string, ModelIdentityReverseImpactView>();
  let previousPath = "";
  for (const rawDocument of input.documents) {
    const document = record(rawDocument, "qualification fixture document");
    exactFields(document, [
      "byte_length", "content_sha256", "git_blob_sha1", "kind", "path", "value",
    ], "qualification fixture document");
    if (
      typeof document.path !== "string" ||
      document.path <= previousPath ||
      typeof document.byte_length !== "number" ||
      !Number.isSafeInteger(document.byte_length) ||
      document.byte_length < 1 ||
      typeof document.content_sha256 !== "string" ||
      !SHA256.test(document.content_sha256) ||
      typeof document.git_blob_sha1 !== "string" ||
      !SHA.test(document.git_blob_sha1)
    ) throw new TypeError("qualification fixture document metadata is invalid");
    previousPath = document.path;
    const bytes = new TextEncoder().encode(canonicalStateDocument(document.value));
    if (
      bytes.byteLength !== document.byte_length ||
      await digest("SHA-256", bytes) !== document.content_sha256 ||
      await gitBlobSha(bytes) !== document.git_blob_sha1
    ) throw new TypeError("qualification fixture document digest is invalid");
    if (document.kind === "event") {
      validateStateEvent(document.value);
      if (
        !document.value.event_type.startsWith("model_identity.") ||
        document.path !== stateEventPath(document.value) ||
        events.has(document.value.event_id)
      ) throw new TypeError("qualification fixture event is invalid");
      events.set(document.value.event_id, document.value);
    } else if (document.kind === "model_identity_view") {
      const view = decodeModelIdentityView(document.value);
      if (document.path !== modelIdentityPath(view.model_id) || modelViews.has(view.model_id)) {
        throw new TypeError("qualification fixture model view is invalid");
      }
      modelViews.set(view.model_id, view);
    } else if (document.kind === "model_alias_view") {
      const view = decodeModelAliasView(document.value);
      if (document.path !== modelAliasPath(view.alias_key) || aliasViews.has(view.alias_key)) {
        throw new TypeError("qualification fixture alias view is invalid");
      }
      aliasViews.set(view.alias_key, view);
    } else if (document.kind === "reverse_impact_view") {
      const view = decodeModelIdentityReverseImpactView(document.value);
      if (
        document.path !== modelIdentityReverseImpactPath(view.terminal_model_id) ||
        impacts.has(view.terminal_model_id)
      ) throw new TypeError("qualification fixture reverse impact is invalid");
      impacts.set(view.terminal_model_id, view);
    } else {
      throw new TypeError("qualification fixture document kind is invalid");
    }
  }
  if (
    events.size !== 74 ||
    modelViews.size !== 7 ||
    aliasViews.size !== 61 ||
    impacts.size !== 6
  ) throw new TypeError("qualification fixture document topology is invalid");

  for (const event of events.values()) {
    if (event.causation_event_id !== null) {
      const cause = events.get(event.causation_event_id);
      if (
        cause === undefined ||
        cause.event_id >= event.event_id ||
        cause.occurred_at >= event.occurred_at
      ) throw new TypeError("qualification fixture event causality is invalid");
    }
  }
  for (const view of modelViews.values()) {
    const request = events.get(view.request_event_id);
    const decision = view.decision_event_id === null
      ? null
      : events.get(view.decision_event_id);
    const mutation = events.get(view.mutation_event_id);
    if (
      view.owner_login !== intent.owner.login ||
      await modelIdentityId(view.request_event_id) !== view.model_id ||
      request?.event_type !== "model_identity.requested" ||
      request.subject_id !== view.model_id ||
      request.actor.login !== intent.owner.login ||
      request.payload.display_name !== view.requested_name ||
      mutation?.subject_id !== view.model_id ||
      (view.status === "pending" && decision !== null) ||
      (view.status !== "pending" && (
        !fixtureDecisionMatches(
          decision ?? undefined,
          view.model_id,
          intent.maintainer.login,
        )
      ))
    ) throw new TypeError("qualification fixture model binding is invalid");
  }
  for (const view of aliasViews.values()) {
    const assignment = events.get(view.assignment_event_id);
    if (
      view.owner_login !== intent.owner.login ||
      await modelAliasKey(view.owner_login, view.alias) !== view.alias_key ||
      !modelViews.has(view.model_id) ||
      assignment?.event_type !== "model_identity.alias_assigned" ||
      assignment.subject_id !== view.model_id ||
      assignment.actor.login !== intent.owner.login ||
      assignment.payload.alias !== view.alias ||
      assignment.occurred_at !== view.assigned_at
    ) throw new TypeError("qualification fixture alias binding is invalid");
  }
  const referencedEvents = eventReferenceIds(modelViews.values(), aliasViews.values());
  if (
    referencedEvents.size !== events.size ||
    [...events.keys()].some((eventId) => !referencedEvents.has(eventId))
  ) throw new TypeError("qualification fixture contains an orphan event");

  const memberPaths = new Set<string>();
  for (const impact of impacts.values()) {
    const terminalMutation = events.get(impact.terminal_mutation_event_id);
    if (
      impact.owner_login !== intent.owner.login ||
      terminalMutation?.subject_id !== impact.terminal_model_id
    ) {
      throw new TypeError("qualification fixture reverse-impact owner is invalid");
    }
    for (const member of impact.members) {
      if (memberPaths.has(member.view_path)) {
        throw new TypeError("qualification fixture component membership overlaps");
      }
      memberPaths.add(member.view_path);
      if (member.kind === "identity") {
        const view = modelViews.get(member.model_id);
        if (view === undefined) {
          throw new TypeError("qualification fixture identity component is invalid");
        }
        if (
          view.resolved_model_id !== impact.terminal_model_id ||
          view.mutation_event_id !== member.mutation_event_id
        ) throw new TypeError("qualification fixture identity component is invalid");
      } else {
        const view = aliasViews.get(member.alias_key);
        if (view === undefined) {
          throw new TypeError("qualification fixture alias component is invalid");
        }
        if (
          view.model_id !== member.model_id ||
          view.resolved_model_id !== impact.terminal_model_id ||
          view.assignment_event_id !== member.assignment_event_id
        ) throw new TypeError("qualification fixture alias component is invalid");
      }
    }
  }
  const pending = modelViews.get(rejection.pending_model_id);
  const firstTarget = modelViews.get(chain.first_target_model_id);
  const secondTarget = modelViews.get(chain.second_target_model_id);
  if (
    pending?.status !== "pending" ||
    impacts.has(rejection.pending_model_id) ||
    firstTarget?.status !== "approved" ||
    firstTarget.resolved_model_id !== firstTarget.model_id ||
    secondTarget?.status !== "approved" ||
    secondTarget.resolved_model_id !== secondTarget.model_id
  ) throw new TypeError("qualification fixture lifecycle binding is invalid");
  requireComponent(
    impacts,
    modelViews,
    capRefusal,
    16,
    17,
    33,
    "qualification cap fixture",
  );
  requireComponent(
    impacts,
    modelViews,
    contention,
    16,
    16,
    32,
    "qualification contention fixture",
  );
  const boundTerminals = new Set([
    chain.first_target_model_id,
    chain.second_target_model_id,
    capRefusal.source_terminal_model_id,
    capRefusal.target_terminal_model_id,
    contention.source_terminal_model_id,
    contention.target_terminal_model_id,
  ]);
  if (
    boundTerminals.size !== 6 ||
    [...impacts.keys()].some((modelId) => !boundTerminals.has(modelId)) ||
    memberPaths.size !== modelViews.size - 1 + aliasViews.size
  ) throw new TypeError("qualification fixture bindings are not complete");

  return value as QualificationFixtureManifest;
}

export async function reviewedQualificationFixtureManifest(): Promise<
  QualificationFixtureManifest | null
> {
  if (
    REVIEWED_QUALIFICATION_FIXTURE_MANIFEST === null ||
    REVIEWED_QUALIFICATION_FIXTURE_DIGEST === null
  ) return null;
  return verifyQualificationFixtureManifest(
    REVIEWED_QUALIFICATION_FIXTURE_MANIFEST,
    REVIEWED_QUALIFICATION_FIXTURE_DIGEST,
  );
}
