import { canonicalQualificationValue } from "./model-identity-qualification-journal";

const SHA = /^[0-9a-f]{40}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const JOURNAL_ID = /^mqj_[0-9a-f]{64}$/;
const RUN_ID = /^[1-9][0-9]{0,19}$/;
const MAX_CAPABILITY_BYTES = 4096;

export type QualificationExecutorCapability = Readonly<{
  schema_version: 1;
  kind: "model_identity_qualification_executor";
  deployed_commit: string;
  run_id: string;
  run_attempt: 1;
  journal_id: string;
  journal_revision: number;
  operation: string;
  plan_digest: string;
  request_digest: string;
  request_index: number;
  issued_at: number;
  expires_at: number;
}>;

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/u, "");
}

function decodeBase64Url(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]+$/u.test(value)) {
    throw new TypeError("qualification executor capability encoding is invalid");
  }
  try {
    const binary = atob(
      value.replaceAll("-", "+").replaceAll("_", "/")
        .padEnd(Math.ceil(value.length / 4) * 4, "="),
    );
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    throw new TypeError("qualification executor capability encoding is invalid");
  }
}

async function hmac(secret: string, bytes: Uint8Array): Promise<Uint8Array> {
  if (new TextEncoder().encode(secret).byteLength < 32) {
    throw new TypeError("qualification executor secret is unavailable");
  }
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, bytes));
}

function capability(value: unknown, nowSeconds: number): QualificationExecutorCapability {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("qualification executor capability is invalid");
  }
  const input = value as Record<string, unknown>;
  const expected = [
    "deployed_commit", "expires_at", "issued_at", "journal_id",
    "journal_revision", "kind", "operation", "plan_digest", "request_digest",
    "request_index",
    "run_attempt", "run_id", "schema_version",
  ];
  const fields = Object.keys(input).sort();
  if (
    fields.length !== expected.length ||
    fields.some((field, index) => field !== expected[index]) ||
    input.schema_version !== 1 ||
    input.kind !== "model_identity_qualification_executor" ||
    typeof input.deployed_commit !== "string" ||
    !SHA.test(input.deployed_commit) ||
    typeof input.run_id !== "string" ||
    !RUN_ID.test(input.run_id) ||
    input.run_attempt !== 1 ||
    typeof input.journal_id !== "string" ||
    !JOURNAL_ID.test(input.journal_id) ||
    typeof input.journal_revision !== "number" ||
    !Number.isSafeInteger(input.journal_revision) ||
    input.journal_revision < 1 ||
    typeof input.operation !== "string" ||
    !/^[a-z][a-z0-9_]{0,63}$/.test(input.operation) ||
    typeof input.plan_digest !== "string" ||
    !SHA256.test(input.plan_digest) ||
    typeof input.request_digest !== "string" ||
    !SHA256.test(input.request_digest) ||
    typeof input.request_index !== "number" ||
    !Number.isSafeInteger(input.request_index) ||
    input.request_index < 0 ||
    input.request_index > 127 ||
    typeof input.issued_at !== "number" ||
    !Number.isSafeInteger(input.issued_at) ||
    typeof input.expires_at !== "number" ||
    !Number.isSafeInteger(input.expires_at) ||
    input.issued_at > nowSeconds + 30 ||
    input.expires_at <= nowSeconds ||
    input.expires_at - input.issued_at > 120
  ) throw new TypeError("qualification executor capability is invalid");
  return input as QualificationExecutorCapability;
}

export async function signQualificationExecutorCapability(
  secret: string,
  value: QualificationExecutorCapability,
): Promise<string> {
  capability(value, value.issued_at);
  const encoded = new TextEncoder().encode(canonicalQualificationValue(value));
  return `${base64Url(encoded)}.${base64Url(await hmac(secret, encoded))}`;
}

export async function verifyQualificationExecutorCapability(
  secret: string,
  token: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<QualificationExecutorCapability> {
  if (new TextEncoder().encode(token).byteLength > MAX_CAPABILITY_BYTES) {
    throw new TypeError("qualification executor capability is invalid");
  }
  const pieces = token.split(".");
  if (pieces.length !== 2 || pieces[0] === undefined || pieces[1] === undefined) {
    throw new TypeError("qualification executor capability is invalid");
  }
  const encoded = decodeBase64Url(pieces[0]);
  const actual = decodeBase64Url(pieces[1]);
  const expected = await hmac(secret, encoded);
  if (
    actual.byteLength !== expected.byteLength ||
    !crypto.subtle.timingSafeEqual(actual, expected)
  ) throw new TypeError("qualification executor capability is invalid");
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder().decode(encoded)) as unknown;
  } catch {
    throw new TypeError("qualification executor capability is invalid");
  }
  return capability(parsed, nowSeconds);
}
