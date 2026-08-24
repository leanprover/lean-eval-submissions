import { isUuidV7 } from "./api-contract";
import { newEventId } from "./state-event";

const TOKEN_VERSION = "v1";
const MAX_TOKEN_BYTES = 8192;
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const NONCE = /^[A-Za-z0-9_-]{43}$/;
const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const COMMIT = /^[0-9a-f]{40}$/;
const GIST_ID = /^[0-9a-f]{5,64}$/;

type TokenKind = "agent_challenge" | "browser_session" | "oauth_state" | "submission_grant";

export type OAuthState = Readonly<{
  kind: "oauth_state";
  nonce: string;
  nonce_event_id: string;
  issued_at: number;
  expires_at: number;
}>;

export type BrowserSession = Readonly<{
  kind: "browser_session";
  login: string;
  github_id: number;
  issued_at: number;
  expires_at: number;
}>;

export type SubmissionGrant = Readonly<{
  kind: "submission_grant";
  login: string;
  submission_id: string;
  nonce: string;
  nonce_event_id: string;
  metadata_event_id: string;
  issued_at: number;
  expires_at: number;
}>;

export type AgentChallenge = Readonly<{
  kind: "agent_challenge";
  login: string;
  source_repository: string;
  source_commit: string;
  gist_id: string;
  tag: string;
  submission_id: string;
  nonce: string;
  nonce_event_id: string;
  metadata_event_id: string;
  issued_at: number;
  expires_at: number;
}>;

export type SignedPayload = OAuthState | BrowserSession | SubmissionGrant | AgentChallenge;

export class AuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthError";
  }
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

function decodeBase64Url(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]+$/u.test(value)) throw new AuthError("token encoding is invalid");
  const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  try {
    const binary = atob(padded);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    throw new AuthError("token encoding is invalid");
  }
}

async function hmac(secret: string, input: string): Promise<Uint8Array> {
  if (new TextEncoder().encode(secret).length < 32) {
    throw new AuthError("authentication secret is not configured safely");
  }
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(input)));
}

function exactObject(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new AuthError("token payload is invalid");
  }
  return value as Record<string, unknown>;
}

const FIELDS: Readonly<Record<TokenKind, readonly string[]>> = {
  oauth_state: ["expires_at", "issued_at", "kind", "nonce", "nonce_event_id"],
  browser_session: ["expires_at", "github_id", "issued_at", "kind", "login"],
  submission_grant: [
    "expires_at",
    "issued_at",
    "kind",
    "login",
    "metadata_event_id",
    "nonce",
    "nonce_event_id",
    "submission_id",
  ],
  agent_challenge: [
    "expires_at",
    "gist_id",
    "issued_at",
    "kind",
    "login",
    "metadata_event_id",
    "nonce",
    "nonce_event_id",
    "source_commit",
    "source_repository",
    "submission_id",
    "tag",
  ],
};

function validatePayload(value: unknown, expectedKind: TokenKind, now: number): SignedPayload {
  const payload = exactObject(value);
  const expected = [...FIELDS[expectedKind]].sort();
  const actual = Object.keys(payload).sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new AuthError("token payload fields are invalid");
  }
  if (payload.kind !== expectedKind) throw new AuthError("token purpose is invalid");
  if (
    typeof payload.issued_at !== "number" ||
    !Number.isSafeInteger(payload.issued_at) ||
    typeof payload.expires_at !== "number" ||
    !Number.isSafeInteger(payload.expires_at) ||
    payload.issued_at > now + 30 ||
    payload.expires_at <= now ||
    payload.expires_at - payload.issued_at > 3600
  ) {
    throw new AuthError("token is expired or has invalid timing");
  }
  for (const field of ["metadata_event_id", "nonce_event_id", "submission_id"] as const) {
    if (field in payload && (typeof payload[field] !== "string" || !isUuidV7(payload[field]))) {
      throw new AuthError(`token ${field} is invalid`);
    }
  }
  if ("nonce" in payload && (typeof payload.nonce !== "string" || !NONCE.test(payload.nonce))) {
    throw new AuthError("token nonce is invalid");
  }
  if ("login" in payload && (typeof payload.login !== "string" || !LOGIN.test(payload.login))) {
    throw new AuthError("token login is invalid");
  }
  if (
    expectedKind === "browser_session" &&
    (typeof payload.github_id !== "number" || !Number.isSafeInteger(payload.github_id) || payload.github_id < 1)
  ) {
    throw new AuthError("token GitHub identity is invalid");
  }
  if (expectedKind === "agent_challenge") {
    if (typeof payload.source_repository !== "string" || !REPOSITORY.test(payload.source_repository)) {
      throw new AuthError("token source repository is invalid");
    }
    if (typeof payload.source_commit !== "string" || !COMMIT.test(payload.source_commit)) {
      throw new AuthError("token source commit is invalid");
    }
    if (typeof payload.gist_id !== "string" || !GIST_ID.test(payload.gist_id)) {
      throw new AuthError("token gist is invalid");
    }
    if (payload.tag !== `lean-eval/${String(payload.submission_id)}`) {
      throw new AuthError("token tag is invalid");
    }
  }
  return payload as SignedPayload;
}

export async function signToken(secret: string, payload: SignedPayload): Promise<string> {
  const encoded = base64Url(new TextEncoder().encode(JSON.stringify(payload)));
  const signingInput = `${TOKEN_VERSION}.${encoded}`;
  return `${signingInput}.${base64Url(await hmac(secret, signingInput))}`;
}

export async function verifyToken<T extends SignedPayload>(
  secret: string,
  token: string,
  expectedKind: T["kind"],
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<T> {
  if (new TextEncoder().encode(token).length > MAX_TOKEN_BYTES) throw new AuthError("token is too large");
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== TOKEN_VERSION || !parts[1] || !parts[2]) {
    throw new AuthError("token format is invalid");
  }
  const signingInput = `${parts[0]}.${parts[1]}`;
  const expected = await hmac(secret, signingInput);
  const actual = decodeBase64Url(parts[2]);
  if (actual.length !== expected.length || !crypto.subtle.timingSafeEqual(actual, expected)) {
    throw new AuthError("token signature is invalid");
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(decodeBase64Url(parts[1]))) as unknown;
  } catch {
    throw new AuthError("token payload is invalid");
  }
  return validatePayload(decoded, expectedKind, nowSeconds) as T;
}

export function randomNonce(bytes = 32): string {
  return base64Url(crypto.getRandomValues(new Uint8Array(bytes)));
}

function intakeEventIds(nowSeconds: number): Readonly<{
  nonceEventId: string;
  submissionId: string;
  metadataEventId: string;
}> {
  // State requires every causation event ID to sort after its parent. Reserve
  // adjacent UUIDv7 milliseconds so independent random tails cannot invert the
  // nonce -> submission -> metadata chain when a grant is issued quickly.
  const firstMillisecond = nowSeconds * 1000;
  if (
    !Number.isSafeInteger(nowSeconds) ||
    nowSeconds < 0 ||
    !Number.isSafeInteger(firstMillisecond) ||
    firstMillisecond + 2 > 0xffffffffffff
  ) {
    throw new TypeError("intake grant time must fit an ordered UUIDv7 sequence");
  }
  return {
    nonceEventId: newEventId(firstMillisecond),
    submissionId: newEventId(firstMillisecond + 1),
    metadataEventId: newEventId(firstMillisecond + 2),
  };
}

export function makeOAuthState(nowSeconds: number): OAuthState {
  return {
    kind: "oauth_state",
    nonce: randomNonce(),
    nonce_event_id: newEventId(),
    issued_at: nowSeconds,
    expires_at: nowSeconds + 600,
  };
}

export function makeSubmissionGrant(login: string, nowSeconds: number): SubmissionGrant {
  const eventIds = intakeEventIds(nowSeconds);
  return {
    kind: "submission_grant",
    login,
    submission_id: eventIds.submissionId,
    metadata_event_id: eventIds.metadataEventId,
    nonce: randomNonce(),
    nonce_event_id: eventIds.nonceEventId,
    issued_at: nowSeconds,
    expires_at: nowSeconds + 900,
  };
}

export function makeAgentChallenge(
  input: Readonly<{
    login: string;
    source_repository: string;
    source_commit: string;
    gist_id: string;
  }>,
  nowSeconds: number,
): AgentChallenge {
  const eventIds = intakeEventIds(nowSeconds);
  return {
    kind: "agent_challenge",
    ...input,
    tag: `lean-eval/${eventIds.submissionId}`,
    submission_id: eventIds.submissionId,
    metadata_event_id: eventIds.metadataEventId,
    nonce: randomNonce(),
    nonce_event_id: eventIds.nonceEventId,
    issued_at: nowSeconds,
    expires_at: nowSeconds + 600,
  };
}

export async function nonceDigest(purpose: "agent" | "intake_lease" | "oauth" | "submission", nonce: string): Promise<string> {
  const bytes = new TextEncoder().encode(`lean-eval-auth-nonce-v1\0${purpose}\0${nonce}`);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Derive a replay-safe UUIDv7 for one immutable lifecycle fact. */
export async function lifecycleEventId(
  eventType:
    | "archive.completed"
    | "archive.failed"
    | "evaluation.started"
    | "evaluation.accepted"
    | "evaluation.rejected"
    | "evaluation.failed"
    | "result.recorded"
    | "release.scheduled",
  subjectId: string,
  occurredAt: string,
): Promise<string> {
  const submissionEvent = eventType.startsWith("archive.") || eventType.startsWith("evaluation.");
  if (
    (submissionEvent && !isUuidV7(subjectId)) ||
    (!submissionEvent && !/^r2_[0-9a-f]{64}$/.test(subjectId))
  ) {
    throw new AuthError("lifecycle subject is invalid");
  }
  const date = new Date(occurredAt);
  if (
    occurredAt.startsWith("0000-") ||
    Number.isNaN(date.valueOf()) ||
    date.toISOString() !== occurredAt ||
    date.valueOf() > 0xffffffffffff
  ) {
    throw new AuthError("lifecycle timestamp is invalid");
  }
  const bytes = new Uint8Array(await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(
      `lean-eval-lifecycle-event-v1\0${eventType}\0${subjectId}\0${occurredAt}`,
    ),
  )).slice(0, 16);
  let milliseconds = date.valueOf();
  for (let index = 5; index >= 0; index -= 1) {
    bytes[index] = milliseconds % 256;
    milliseconds = Math.floor(milliseconds / 256);
  }
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x70;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export async function equalToken(left: string, right: string): Promise<boolean> {
  const [leftDigest, rightDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", new TextEncoder().encode(left)),
    crypto.subtle.digest("SHA-256", new TextEncoder().encode(right)),
  ]);
  return crypto.subtle.timingSafeEqual(leftDigest, rightDigest);
}
