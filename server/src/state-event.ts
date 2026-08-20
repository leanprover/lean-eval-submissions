const UUID_V7 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const GITHUB_LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const PROBLEM_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const COMMIT = /^[0-9a-f]{40}$/;
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

/** State events the public submission Worker is authorized to append. */
export type StateEvent = SystemInitializedEvent | SubmissionReceivedEvent;

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
    !Number.isInteger(payload.statement_revision) ||
    payload.statement_revision < 1
  ) {
    throw new TypeError("submission.received statement_revision must be positive");
  }
  nonemptyString(payload.declared_model, "submission.received declared_model");
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
