const EVENT_ID = /^[0-9a-f]{64}$/;
const SUBJECT_ID = /^[a-z][a-z0-9_-]{2,127}$/;

export const STATE_EVENT_SCHEMA_VERSION = 1 as const;

export type StateEvent = Readonly<{
  schema_version: typeof STATE_EVENT_SCHEMA_VERSION;
  event_id: string;
  event_type: string;
  occurred_at: string;
  subject_id: string;
  actor: Readonly<{
    kind: "github" | "system";
    login?: string;
  }>;
  payload: Readonly<Record<string, unknown>>;
}>;

function isCanonicalUtcTimestamp(value: string): boolean {
  const date = new Date(value);
  return !Number.isNaN(date.valueOf()) && date.toISOString() === value;
}

export function validateStateEvent(event: StateEvent): void {
  if (!EVENT_ID.test(event.event_id)) {
    throw new TypeError("State event id must be 64 lowercase hexadecimal characters");
  }
  if (!/^[a-z][a-z0-9.]{2,127}$/.test(event.event_type)) {
    throw new TypeError("State event type is not canonical");
  }
  if (!isCanonicalUtcTimestamp(event.occurred_at)) {
    throw new TypeError("State event timestamp must be canonical UTC ISO 8601");
  }
  if (!SUBJECT_ID.test(event.subject_id)) {
    throw new TypeError("State event subject id is not canonical");
  }
  if (event.actor.kind === "github") {
    if (!event.actor.login || !/^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/.test(event.actor.login)) {
      throw new TypeError("GitHub actor login must be lowercase and canonical");
    }
  } else if (event.actor.login !== undefined) {
    throw new TypeError("system actors cannot carry a GitHub login");
  }
}

export function stateEventPath(event: StateEvent): string {
  validateStateEvent(event);
  const date = event.occurred_at.slice(0, 10).replaceAll("-", "/");
  return `events/${date}/${event.event_id}.json`;
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
