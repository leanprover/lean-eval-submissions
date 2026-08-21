import { decodeProductionMetadata } from "./api-contract";

const UUID_V7 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const GITHUB_LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const PROBLEM_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const COMMIT = /^[0-9a-f]{40}$/;
const RESULT_ID = /^r2_[0-9a-f]{64}$/;
const REPLAY_ID = /^rt1_[0-9a-f]{64}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const REASON = /^[a-z][a-z0-9_]{1,63}$/;
const TOOLCHAIN = /^leanprover\/lean4:v[0-9]+\.[0-9]+\.[0-9]+$/;
const COUNTER_REASONS = new Set([
  "counter_not_reported",
  "counter_not_supported",
  "counter_permission_denied",
]);
const REPLAY_FAILURES = new Set([
  "benchmark_fetch_failed",
  "runner_lost",
  "runner_start_failed",
  "source_fetch_failed",
  "toolchain_setup_failed",
  "verdict_invalid",
]);
const RETRYABLE_REPLAY_FAILURES = new Set([
  "benchmark_fetch_failed",
  "runner_lost",
  "runner_start_failed",
  "source_fetch_failed",
]);
const REPLAY_UNAVAILABLE = new Set([
  "source_ref_permanently_unavailable",
  "benchmark_ref_permanently_unavailable",
  "execution_profile_permanently_unavailable",
]);
const TOP_LEVEL_FIELDS = [
  "actor",
  "causation_event_id",
  "event_id",
  "event_type",
  "occurred_at",
  "payload",
  "schema_version",
  "subject_id",
] as const;

export const STATE_EVENT_SCHEMA_VERSION = 1 as const;

type EventEnvelope = Readonly<{
  schema_version: typeof STATE_EVENT_SCHEMA_VERSION;
  event_id: string;
  occurred_at: string;
  causation_event_id: null;
}>;

export type SystemInitializedEvent = EventEnvelope &
  Readonly<{
    event_type: "system.initialized";
    subject_id: "state_staging" | "state_production";
    actor: Readonly<{ kind: "system" }>;
    payload: Readonly<{ environment: "staging" | "production" }>;
  }>;

export type SubmissionReceivedEvent = EventEnvelope &
  Readonly<{
    event_type: "submission.received";
    subject_id: string;
    actor: Readonly<{ kind: "github"; login: string }>;
    payload: Readonly<{
      problem_id: string;
      statement_revision: number;
      declared_model: string;
      source_repository: string;
      source_commit: string;
      source_visibility: "private" | "public";
      publication_choice: "scheduled" | "withheld";
    }>;
  }>;

export type AuthenticationNonceConsumedEvent = EventEnvelope &
  Readonly<{
    event_type: "authentication.nonce_consumed";
    subject_id: string;
    actor: Readonly<{ kind: "system" }>;
    payload: Readonly<{
      nonce_digest: string;
      purpose: "agent" | "oauth" | "submission";
      expires_at: string;
    }>;
  }>;

export type SubmissionMetadataAmendedEvent = Omit<EventEnvelope, "causation_event_id"> &
  Readonly<{
    event_type: "submission.metadata_amended";
    subject_id: string;
    causation_event_id: string;
    actor: Readonly<{ kind: "github"; login: string }>;
    payload: Readonly<{ production_metadata: Readonly<Record<string, unknown>> }>;
  }>;

export type SubmissionPublicationChangedEvent = Omit<EventEnvelope, "causation_event_id"> &
  Readonly<{
    event_type: "submission.publication_changed";
    subject_id: string;
    causation_event_id: string;
    actor: Readonly<{ kind: "github"; login: string }>;
    payload: Readonly<{ publication_choice: "scheduled" | "withheld" }>;
  }>;

/** State events the public submission Worker is authorized to append. */
export type WritableStateEvent =
  | SystemInitializedEvent
  | SubmissionReceivedEvent
  | AuthenticationNonceConsumedEvent
  | SubmissionMetadataAmendedEvent
  | SubmissionPublicationChangedEvent;

type LifecycleEventType =
  | "archive.completed"
  | "archive.failed"
  | "evaluation.accepted"
  | "evaluation.failed"
  | "evaluation.rejected"
  | "evaluation.started"
  | "release.cancelled"
  | "release.failed"
  | "release.published"
  | "release.scheduled"
  | "release.started"
  | "replay.accepted"
  | "replay.crashed"
  | "replay.declined"
  | "replay.enqueued"
  | "replay.failed"
  | "replay.rejected"
  | "replay.started"
  | "replay.timed_out"
  | "replay.unavailable"
  | "result.recorded";

export type LifecycleStateEvent = Readonly<{
  schema_version: 1;
  event_id: string;
  event_type: LifecycleEventType;
  occurred_at: string;
  subject_id: string;
  causation_event_id: string;
  actor: Readonly<{ kind: "system" }> | Readonly<{ kind: "github"; login: string }>;
  payload: Readonly<Record<string, unknown>>;
}>;

export type StateEvent = WritableStateEvent | LifecycleStateEvent;

function object(value: unknown, label: string): Record<string, unknown> {
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
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new TypeError(`${label} fields do not match schema version 1`);
  }
}

function isCanonicalUtcTimestamp(value: string): boolean {
  const date = new Date(value);
  return (
    !value.startsWith("0000-") &&
    !Number.isNaN(date.valueOf()) &&
    date.toISOString() === value
  );
}

function nonemptyString(value: unknown, label: string): asserts value is string {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`${label} must be a non-empty string`);
  }
}

function containsControlCharacter(value: string): boolean {
  for (const character of value) {
    const code = character.codePointAt(0) ?? 0;
    if (code <= 0x1f || code === 0x7f) return true;
  }
  return false;
}

function validateSystemEvent(event: Record<string, unknown>): void {
  if (event.causation_event_id !== null) {
    throw new TypeError("system.initialized must have null causation_event_id");
  }
  const actor = object(event.actor, "State event actor");
  exactFields(actor, ["kind"], "State event actor");
  if (actor.kind !== "system") throw new TypeError("system.initialized actor must be system");
  const payload = object(event.payload, "State event payload");
  exactFields(payload, ["environment"], "State event payload");
  if (payload.environment !== "staging" && payload.environment !== "production") {
    throw new TypeError("system.initialized environment must be staging or production");
  }
  if (event.subject_id !== `state_${payload.environment}`) {
    throw new TypeError("State event subject id must match its environment");
  }
}

function validateSubmissionEvent(event: Record<string, unknown>): void {
  if (event.causation_event_id !== null) {
    throw new TypeError("submission.received must have null causation_event_id");
  }
  if (typeof event.subject_id !== "string" || !UUID_V7.test(event.subject_id)) {
    throw new TypeError("submission.received subject must be a lowercase UUIDv7");
  }
  const actor = object(event.actor, "State event actor");
  exactFields(actor, ["kind", "login"], "State event actor");
  if (
    actor.kind !== "github" ||
    typeof actor.login !== "string" ||
    !GITHUB_LOGIN.test(actor.login)
  ) {
    throw new TypeError("submission.received actor must have a canonical GitHub login");
  }
  const payload = object(event.payload, "State event payload");
  exactFields(
    payload,
    [
      "declared_model",
      "problem_id",
      "publication_choice",
      "source_commit",
      "source_repository",
      "source_visibility",
      "statement_revision",
    ],
    "State event payload",
  );
  if (typeof payload.problem_id !== "string" || !PROBLEM_ID.test(payload.problem_id)) {
    throw new TypeError("submission.received problem_id is not canonical");
  }
  if (
    typeof payload.statement_revision !== "number" ||
    !Number.isSafeInteger(payload.statement_revision) ||
    payload.statement_revision < 1
  ) {
    throw new TypeError("submission.received statement_revision must be positive");
  }
  if (event.subject_id !== event.event_id) throw new TypeError("submission.received subject must equal event_id");
  nonemptyString(payload.declared_model, "submission.received declared_model");
  if (
    new TextEncoder().encode(payload.declared_model).length > 256 ||
    containsControlCharacter(payload.declared_model)
  ) {
    throw new TypeError("submission.received declared_model must be control-free UTF-8 of at most 256 bytes");
  }
  if (typeof payload.source_repository !== "string" || !REPOSITORY.test(payload.source_repository)) {
    throw new TypeError("submission.received source_repository is not canonical");
  }
  if (typeof payload.source_commit !== "string" || !COMMIT.test(payload.source_commit)) {
    throw new TypeError("submission.received source_commit is not canonical");
  }
  if (payload.source_visibility !== "private" && payload.source_visibility !== "public") {
    throw new TypeError("submission.received source_visibility is invalid");
  }
  if (payload.publication_choice !== "scheduled" && payload.publication_choice !== "withheld") {
    throw new TypeError("submission.received publication_choice is invalid");
  }
}

function validateCanonicalActor(event: Record<string, unknown>): void {
  const actor = object(event.actor, "State event actor");
  exactFields(actor, ["kind", "login"], "State event actor");
  if (actor.kind !== "github" || typeof actor.login !== "string" || !GITHUB_LOGIN.test(actor.login)) {
    throw new TypeError("State event actor must have a canonical GitHub login");
  }
}

function validateNonceEvent(event: Record<string, unknown>): void {
  if (event.causation_event_id !== null) throw new TypeError("nonce consumption must be a root event");
  if (typeof event.subject_id !== "string" || !UUID_V7.test(event.subject_id)) {
    throw new TypeError("nonce consumption subject must be a lowercase UUIDv7");
  }
  const actor = object(event.actor, "State event actor");
  exactFields(actor, ["kind"], "State event actor");
  if (actor.kind !== "system") throw new TypeError("nonce consumption actor must be system");
  const payload = object(event.payload, "State event payload");
  exactFields(payload, ["expires_at", "nonce_digest", "purpose"], "State event payload");
  if (typeof payload.nonce_digest !== "string" || !/^[0-9a-f]{64}$/.test(payload.nonce_digest)) {
    throw new TypeError("nonce digest must be lowercase SHA-256");
  }
  if (!new Set(["agent", "oauth", "submission"]).has(String(payload.purpose))) {
    throw new TypeError("nonce purpose is invalid");
  }
  if (typeof payload.expires_at !== "string" || !isCanonicalUtcTimestamp(payload.expires_at)) {
    throw new TypeError("nonce expiry must be canonical UTC ISO 8601 milliseconds");
  }
  if (event.subject_id !== event.event_id) throw new TypeError("nonce consumption subject must equal event_id");
  if (typeof event.occurred_at !== "string" || event.occurred_at >= payload.expires_at) {
    throw new TypeError("nonce must be consumed before expiry");
  }
}

function validateSubmissionChild(event: Record<string, unknown>): void {
  if (typeof event.subject_id !== "string" || !UUID_V7.test(event.subject_id)) {
    throw new TypeError("submission event subject must be a lowercase UUIDv7");
  }
  if (typeof event.causation_event_id !== "string" || !UUID_V7.test(event.causation_event_id)) {
    throw new TypeError("submission event cause must be a lowercase UUIDv7");
  }
  validateCanonicalActor(event);
  const payload = object(event.payload, "State event payload");
  if (event.event_type === "submission.publication_changed") {
    exactFields(payload, ["publication_choice"], "State event payload");
    if (payload.publication_choice !== "scheduled" && payload.publication_choice !== "withheld") {
      throw new TypeError("publication choice is invalid");
    }
  } else {
    exactFields(payload, ["production_metadata"], "State event payload");
    decodeProductionMetadata(payload.production_metadata);
  }
}

const LIFECYCLE_FIELDS: Readonly<Record<LifecycleEventType, readonly string[]>> = {
  "archive.completed": ["archive_ciphertext_sha256", "archive_commit", "archive_path", "archive_repository", "encrypted"],
  "archive.failed": ["reason_code", "retryable"],
  "evaluation.started": ["attempt", "benchmark_commit", "benchmark_repository", "toolchain"],
  "evaluation.accepted": ["attempt", "evaluator_version"],
  "evaluation.rejected": ["attempt", "reason_code"],
  "evaluation.failed": ["attempt", "reason_code", "retryable"],
  "result.recorded": ["problem_id", "result_commit", "statement_revision", "submission_id", "tree_digest"],
  "replay.enqueued": ["checker", "execution_profile_digest", "measurement_config_digest", "result_id"],
  "replay.started": ["attempt", "runner_profile"],
  "replay.accepted": ["attempt", "build_retired_instructions", "build_retired_instructions_unavailable_reason", "build_wall_time_ms", "checker", "checker_retired_instructions", "checker_retired_instructions_unavailable_reason", "checker_wall_time_ms", "file_count", "lines_of_code"],
  "replay.rejected": ["attempt", "build_retired_instructions", "build_retired_instructions_unavailable_reason", "build_wall_time_ms", "checker", "checker_retired_instructions", "checker_retired_instructions_unavailable_reason", "checker_wall_time_ms", "file_count", "lines_of_code"],
  "replay.declined": ["attempt", "build_retired_instructions", "build_retired_instructions_unavailable_reason", "build_wall_time_ms", "checker", "checker_retired_instructions", "checker_retired_instructions_unavailable_reason", "checker_wall_time_ms", "file_count", "lines_of_code"],
  "replay.crashed": ["attempt", "build_retired_instructions", "build_retired_instructions_unavailable_reason", "build_wall_time_ms", "checker", "checker_retired_instructions", "checker_retired_instructions_unavailable_reason", "checker_wall_time_ms", "file_count", "lines_of_code"],
  "replay.timed_out": ["attempt", "build_retired_instructions", "build_retired_instructions_unavailable_reason", "build_wall_time_ms", "checker", "checker_retired_instructions", "checker_retired_instructions_unavailable_reason", "checker_wall_time_ms", "file_count", "lines_of_code"],
  "replay.failed": ["attempt", "reason_code", "retryable"],
  "replay.unavailable": ["evidence_commit", "evidence_path", "evidence_repository", "evidence_sha256", "reason_code"],
  "release.scheduled": ["release_at", "result_id"],
  "release.started": ["attempt"],
  "release.published": ["attempt", "path", "repository_commit", "tree_digest"],
  "release.failed": ["attempt", "reason_code", "retryable"],
  "release.cancelled": ["reason_code"],
};

function validateLifecycleEvent(event: Record<string, unknown>, kind: LifecycleEventType): void {
  if (typeof event.causation_event_id !== "string" || !UUID_V7.test(event.causation_event_id)) {
    throw new TypeError("lifecycle event cause must be a lowercase UUIDv7");
  }
  const submissionSubject = kind.startsWith("archive.") || kind.startsWith("evaluation.");
  const subjectPattern = submissionSubject ? UUID_V7 : kind.startsWith("replay.") ? REPLAY_ID : RESULT_ID;
  if (typeof event.subject_id !== "string" || !subjectPattern.test(event.subject_id)) {
    throw new TypeError("lifecycle event subject is invalid");
  }
  const actor = object(event.actor, "State event actor");
  if (actor.kind === "system") exactFields(actor, ["kind"], "State event actor");
  else {
    if (kind !== "release.cancelled") throw new TypeError("lifecycle event actor must be system");
    validateCanonicalActor(event);
  }
  const payload = object(event.payload, "State event payload");
  exactFields(payload, LIFECYCLE_FIELDS[kind], "State event payload");
  const positive = (field: string): void => {
    if (typeof payload[field] !== "number" || !Number.isSafeInteger(payload[field]) || payload[field] < 1) {
      throw new TypeError(`${field} must be positive`);
    }
  };
  const natural = (field: string): void => {
    if (typeof payload[field] !== "number" || !Number.isSafeInteger(payload[field]) || payload[field] < 0) {
      throw new TypeError(`${field} must be a non-negative safe integer`);
    }
  };
  for (const field of ["archive_commit", "benchmark_commit", "evidence_commit", "repository_commit", "result_commit"] as const) {
    if (field in payload && (typeof payload[field] !== "string" || !COMMIT.test(payload[field]))) throw new TypeError(`${field} is invalid`);
  }
  for (const field of ["archive_ciphertext_sha256", "evidence_sha256", "execution_profile_digest", "measurement_config_digest", "tree_digest"] as const) {
    if (field in payload && (typeof payload[field] !== "string" || !DIGEST.test(payload[field]))) throw new TypeError(`${field} is invalid`);
  }
  if ("attempt" in payload) positive("attempt");
  if ("reason_code" in payload && (typeof payload.reason_code !== "string" || !REASON.test(payload.reason_code))) throw new TypeError("reason_code is invalid");
  if ("retryable" in payload && typeof payload.retryable !== "boolean") throw new TypeError("retryable is invalid");
  if (kind === "archive.completed" && payload.encrypted !== true) throw new TypeError("archive must be encrypted");
  if (kind === "archive.completed") {
    const submissionId = event.subject_id;
    const expectedPath = `archives/${submissionId.replaceAll("-", "").slice(0, 2)}/${submissionId}.tar.age`;
    if (payload.archive_path !== expectedPath) throw new TypeError("archive_path does not match submission identity");
  }
  for (const field of ["archive_repository", "benchmark_repository", "evidence_repository"] as const) {
    if (field in payload && (typeof payload[field] !== "string" || !REPOSITORY.test(payload[field]))) throw new TypeError(`${field} is invalid`);
  }
  if ("toolchain" in payload && (typeof payload.toolchain !== "string" || !TOOLCHAIN.test(payload.toolchain))) throw new TypeError("toolchain is invalid");
  if ("checker" in payload) nonemptyString(payload.checker, "checker");
  if (kind === "result.recorded") {
    if (typeof payload.submission_id !== "string" || !UUID_V7.test(payload.submission_id)) throw new TypeError("submission_id is invalid");
    if (typeof payload.problem_id !== "string" || !PROBLEM_ID.test(payload.problem_id)) throw new TypeError("problem_id is invalid");
    positive("statement_revision");
  }
  if ("result_id" in payload && (typeof payload.result_id !== "string" || !RESULT_ID.test(payload.result_id))) throw new TypeError("result_id is invalid");
  if ("release_at" in payload && (typeof payload.release_at !== "string" || !isCanonicalUtcTimestamp(payload.release_at))) throw new TypeError("release_at is invalid");
  if (kind === "replay.failed") {
    if (!REPLAY_FAILURES.has(String(payload.reason_code))) throw new TypeError("replay failure reason is not registered");
    if (payload.retryable !== RETRYABLE_REPLAY_FAILURES.has(String(payload.reason_code))) {
      throw new TypeError("replay retryability disagrees with its registered reason");
    }
  }
  if (kind === "replay.unavailable") {
    if (!REPLAY_UNAVAILABLE.has(String(payload.reason_code))) throw new TypeError("replay unavailable reason is not registered");
    if (typeof payload.evidence_path !== "string" || payload.evidence_path.length === 0 || payload.evidence_path.startsWith("/") || payload.evidence_path.includes("..")) {
      throw new TypeError("replay unavailable evidence path is invalid");
    }
  }
  if (new Set<LifecycleEventType>(["replay.accepted", "replay.rejected", "replay.declined", "replay.crashed", "replay.timed_out"]).has(kind)) {
    for (const field of ["checker_wall_time_ms", "build_wall_time_ms", "lines_of_code", "file_count"]) natural(field);
    for (const prefix of ["checker", "build"] as const) {
      const measured = payload[`${prefix}_retired_instructions`];
      const reason = payload[`${prefix}_retired_instructions_unavailable_reason`];
      if (measured === null) {
        if (typeof reason !== "string" || !COUNTER_REASONS.has(reason)) throw new TypeError(`${prefix} counter requires a registered unavailable reason`);
      } else {
        natural(`${prefix}_retired_instructions`);
        if (reason !== null) throw new TypeError(`${prefix} measured counter requires null unavailable reason`);
      }
    }
  }
}

export function validateStateEvent(value: unknown): asserts value is StateEvent {
  const event = object(value, "State event");
  exactFields(event, TOP_LEVEL_FIELDS, "State event");
  if (event.schema_version !== STATE_EVENT_SCHEMA_VERSION) {
    throw new TypeError("unsupported State event schema version");
  }
  if (typeof event.event_id !== "string" || !UUID_V7.test(event.event_id)) {
    throw new TypeError("State event id must be a canonical lowercase UUIDv7");
  }
  if (typeof event.occurred_at !== "string" || !isCanonicalUtcTimestamp(event.occurred_at)) {
    throw new TypeError("State event timestamp must be canonical UTC ISO 8601 milliseconds");
  }
  if (event.event_type === "system.initialized") {
    validateSystemEvent(event);
  } else if (event.event_type === "submission.received") {
    validateSubmissionEvent(event);
  } else if (event.event_type === "authentication.nonce_consumed") {
    validateNonceEvent(event);
  } else if (
    event.event_type === "submission.metadata_amended" ||
    event.event_type === "submission.publication_changed"
  ) {
    validateSubmissionChild(event);
  } else if (typeof event.event_type === "string" && event.event_type in LIFECYCLE_FIELDS) {
    validateLifecycleEvent(event, event.event_type as LifecycleEventType);
  } else {
    throw new TypeError("State event type is not writable by the submission Worker");
  }
}

export function stateEventPath(event: StateEvent): string {
  validateStateEvent(event);
  const prefix = event.event_id.replaceAll("-", "").slice(0, 2);
  return `events/${prefix}/${event.event_id}.json`;
}

/** Allocate an RFC 9562 UUIDv7. Retries must retain the allocated identifier. */
export function newEventId(
  unixMilliseconds = Date.now(),
  randomBytes?: Uint8Array,
): string {
  if (
    !Number.isSafeInteger(unixMilliseconds) ||
    unixMilliseconds < 0 ||
    unixMilliseconds > 0xffffffffffff
  ) {
    throw new TypeError("UUIDv7 timestamp must fit in 48 bits");
  }
  const bytes = randomBytes === undefined
    ? crypto.getRandomValues(new Uint8Array(16))
    : Uint8Array.from(randomBytes);
  if (bytes.length !== 16) throw new TypeError("UUIDv7 randomness must contain 16 bytes");
  let timestamp = unixMilliseconds;
  for (let index = 5; index >= 0; index -= 1) {
    bytes[index] = timestamp % 256;
    timestamp = Math.floor(timestamp / 256);
  }
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x70;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
