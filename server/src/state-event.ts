const EVENT_ID = /^[0-9a-f]{64}$/;
const TOP_LEVEL_FIELDS = [
  "actor",
  "event_id",
  "event_type",
  "occurred_at",
  "payload",
  "schema_version",
  "subject_id",
] as const;

export const STATE_EVENT_SCHEMA_VERSION = 1 as const;

export type StateEvent = Readonly<{
  schema_version: typeof STATE_EVENT_SCHEMA_VERSION;
  event_id: string;
  event_type: "system.initialized";
  occurred_at: string;
  subject_id: "state_staging" | "state_production";
  actor: Readonly<{ kind: "system" }>;
  payload: Readonly<{ environment: "staging" | "production" }>;
}>;

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

export function validateStateEvent(value: unknown): asserts value is StateEvent {
  const event = object(value, "State event");
  exactFields(event, TOP_LEVEL_FIELDS, "State event");
  if (event.schema_version !== STATE_EVENT_SCHEMA_VERSION) {
    throw new TypeError("unsupported State event schema version");
  }
  if (typeof event.event_id !== "string" || !EVENT_ID.test(event.event_id)) {
    throw new TypeError("State event id must be 64 lowercase hexadecimal characters");
  }
  if (event.event_type !== "system.initialized") {
    throw new TypeError("State event type is not registered");
  }
  if (typeof event.occurred_at !== "string" || !isCanonicalUtcTimestamp(event.occurred_at)) {
    throw new TypeError("State event timestamp must be canonical UTC ISO 8601 milliseconds");
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

export function stateEventPath(event: StateEvent): string {
  validateStateEvent(event);
  return `events/${event.event_id.slice(0, 2)}/${event.event_id}.json`;
}

export async function eventIdForIdempotencyKey(key: string): Promise<string> {
  if (key.length < 16 || key.length > 200 || !/^[\x21-\x7e]+$/.test(key)) {
    throw new TypeError("idempotency key must contain 16-200 visible ASCII characters");
  }
  const bytes = new TextEncoder().encode(`lean-eval-state-event-v1\0${key}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
