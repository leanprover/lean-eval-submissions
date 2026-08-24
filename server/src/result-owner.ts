import { decodeProductionMetadata, type ProductionMetadata } from "./api-contract";

export const RESULT_OWNER_STATE_CONTRACT_COMMIT =
  "fa4fe8f0e74d66130e5f8671b05cc708e77c4b1f" as const;
export const RESULTS_REPOSITORY = "leanprover/lean-eval-submissions" as const;

const RESULT_ID_DOMAIN = "lean-eval-result-v2\0";
const RESULT_ID = /^r2_[0-9a-f]{64}$/;
const SOURCE_RECORD_ID = /^src1_[0-9a-f]{64}$/;
const EVENT_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const PROBLEM = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const TIMESTAMP =
  /^(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$/;

export type LegacyResultBase = Readonly<{
  declared_model: string;
  problem_id: string;
  statement_revision: number;
  results_repository: typeof RESULTS_REPOSITORY;
  results_commit: string;
  results_path: string;
  canonical_record_sha256: string;
}>;

export type VerifiedLegacyResult = Readonly<{
  resultId: string;
  ownerLogin: string;
  baseResult: LegacyResultBase;
}>;

export type ResultIdentityGuard = Readonly<{
  schema_version: 1;
  result_id: string;
  record_kind: "claimed" | "recorded";
  authority_event_id: string;
}>;

export type MetadataProvenance = Readonly<{
  value: unknown;
  provenance: "backfilled";
  event_id: string;
  recorded_at: string;
}>;

export type ResultOverlay = Readonly<{
  schema_version: 1;
  result_id: string;
  owner_login: string;
  claim_event_id: string;
  mutation_event_id: string;
  claimed_at: string;
  base_result: LegacyResultBase;
  metadata: Readonly<Record<string, MetadataProvenance>>;
}>;

export type SourceRecordIndex = Readonly<{
  schema_version: 1;
  source_record_id: string;
  result_id: string;
  owner_login: string;
  claim_event_id: string;
  results_repository: typeof RESULTS_REPOSITORY;
  results_commit: string;
  results_path: string;
  canonical_record_sha256: string;
}>;

export type ResultReleaseStatusView = Readonly<{
  schema_version: 1;
  result_id: string;
  authority_event_id: string;
  status:
    | "not_scheduled"
    | "scheduled"
    | "running"
    | "published"
    | "failed"
    | "cancelled"
    | "removed";
  release_event_id: string | null;
}>;

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactFields(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new TypeError(`${label} fields do not match schema version 1`);
  }
}

function assertScalarUnicode(value: unknown): void {
  if (typeof value === "string") {
    for (const character of value) {
      const point = character.codePointAt(0) ?? 0;
      if (point >= 0xd800 && point <= 0xdfff) {
        throw new TypeError("RFC 8785 input contains an unpaired Unicode surrogate");
      }
    }
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) assertScalarUnicode(item);
    return;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      assertScalarUnicode(key);
      assertScalarUnicode(item);
    }
  }
}

function containsControlCharacter(value: string): boolean {
  for (const character of value) {
    const point = character.codePointAt(0) ?? 0;
    if (point <= 0x1f || point === 0x7f) return true;
  }
  return false;
}

function isCanonicalTimestamp(value: string): boolean {
  if (!TIMESTAMP.test(value)) return false;
  const parsed = new Date(value);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString() === value;
}

/** RFC 8785 for JSON values, using ECMAScript number/string serialization. */
export function canonicalJson(value: unknown): string {
  assertScalarUnicode(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`;
  }
  if (
    value === null ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value)) ||
    typeof value === "string"
  ) {
    return JSON.stringify(value);
  }
  throw new TypeError("value is not RFC 8785 canonicalizable JSON");
}

export async function sha256Hex(value: string | Uint8Array): Promise<string> {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function resultId(
  login: string,
  declaredModel: string,
  problemId: string,
  statementRevision: number,
): Promise<string> {
  return `r2_${await sha256Hex(
    RESULT_ID_DOMAIN + canonicalJson([login.toLowerCase(), declaredModel, problemId, statementRevision]),
  )}`;
}

export async function sourceRecordId(base: LegacyResultBase): Promise<string> {
  // The exact pinned State contract defines src1 as the bare canonical tuple
  // digest. Domain separation would be a new cross-language index version,
  // not a compatible Worker-only change.
  return `src1_${await sha256Hex(canonicalJson([
    base.results_repository,
    base.results_commit,
    base.results_path,
    base.canonical_record_sha256,
  ]))}`;
}

export function resultIdentityPath(identifier: string): string {
  if (!RESULT_ID.test(identifier)) throw new TypeError("result identity is invalid");
  return `views/result-identities/${identifier.slice(3, 5)}/${identifier}.json`;
}

export function resultOverlayPath(identifier: string): string {
  if (!RESULT_ID.test(identifier)) throw new TypeError("result identity is invalid");
  return `views/result-overlays/${identifier.slice(3, 5)}/${identifier}.json`;
}

export function resultReleaseStatusPath(identifier: string): string {
  if (!RESULT_ID.test(identifier)) throw new TypeError("result identity is invalid");
  return `views/result-release-status/${identifier.slice(3, 5)}/${identifier}.json`;
}

export function sourceRecordPath(identifier: string): string {
  if (!SOURCE_RECORD_ID.test(identifier)) throw new TypeError("source-record identity is invalid");
  return `views/result-source-records/${identifier.slice(5, 7)}/${identifier}.json`;
}

function validateBase(value: unknown): LegacyResultBase {
  const data = object(value, "legacy result base");
  exactFields(data, [
    "canonical_record_sha256",
    "declared_model",
    "problem_id",
    "results_commit",
    "results_path",
    "results_repository",
    "statement_revision",
  ], "legacy result base");
  if (
    typeof data.declared_model !== "string" ||
    data.declared_model.length === 0 ||
    new TextEncoder().encode(data.declared_model).byteLength > 256 ||
    containsControlCharacter(data.declared_model)
  ) {
    throw new TypeError("legacy result declared_model is invalid");
  }
  if (typeof data.problem_id !== "string" || !PROBLEM.test(data.problem_id)) {
    throw new TypeError("legacy result problem_id is invalid");
  }
  if (
    typeof data.statement_revision !== "number" ||
    !Number.isSafeInteger(data.statement_revision) ||
    data.statement_revision < 1
  ) {
    throw new TypeError("legacy result statement_revision is invalid");
  }
  if (
    data.results_repository !== RESULTS_REPOSITORY ||
    typeof data.results_commit !== "string" ||
    !COMMIT.test(data.results_commit) ||
    typeof data.results_path !== "string" ||
    !/^results\/[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?\.json$/.test(data.results_path) ||
    typeof data.canonical_record_sha256 !== "string" ||
    !DIGEST.test(data.canonical_record_sha256)
  ) {
    throw new TypeError("legacy result source binding is invalid");
  }
  return data as LegacyResultBase;
}

export function decodeResultIdentityGuard(value: unknown): ResultIdentityGuard {
  const data = object(value, "result identity guard");
  exactFields(data, ["authority_event_id", "record_kind", "result_id", "schema_version"], "result identity guard");
  if (
    data.schema_version !== 1 ||
    typeof data.result_id !== "string" ||
    !RESULT_ID.test(data.result_id) ||
    (data.record_kind !== "claimed" && data.record_kind !== "recorded") ||
    typeof data.authority_event_id !== "string" ||
    !EVENT_ID.test(data.authority_event_id)
  ) {
    throw new TypeError("result identity guard values are invalid");
  }
  return data as ResultIdentityGuard;
}

export function decodeResultOverlay(value: unknown): ResultOverlay {
  const data = object(value, "result overlay");
  exactFields(data, [
    "base_result",
    "claim_event_id",
    "claimed_at",
    "metadata",
    "mutation_event_id",
    "owner_login",
    "result_id",
    "schema_version",
  ], "result overlay");
  if (
    data.schema_version !== 1 ||
    typeof data.result_id !== "string" ||
    !RESULT_ID.test(data.result_id) ||
    typeof data.owner_login !== "string" ||
    !LOGIN.test(data.owner_login) ||
    typeof data.claim_event_id !== "string" ||
    !EVENT_ID.test(data.claim_event_id) ||
    typeof data.mutation_event_id !== "string" ||
    !EVENT_ID.test(data.mutation_event_id) ||
    typeof data.claimed_at !== "string" ||
    !isCanonicalTimestamp(data.claimed_at)
  ) {
    throw new TypeError("result overlay values are invalid");
  }
  const metadata = object(data.metadata, "result overlay metadata");
  for (const [field, raw] of Object.entries(metadata)) {
    const provenance = object(raw, `result overlay metadata.${field}`);
    exactFields(provenance, ["event_id", "provenance", "recorded_at", "value"], `result overlay metadata.${field}`);
    if (
      provenance.provenance !== "backfilled" ||
      typeof provenance.event_id !== "string" ||
      !EVENT_ID.test(provenance.event_id) ||
      typeof provenance.recorded_at !== "string" ||
      !isCanonicalTimestamp(provenance.recorded_at)
    ) {
      throw new TypeError(`result overlay metadata.${field} provenance is invalid`);
    }
    decodeProductionMetadata({ [field]: provenance.value });
  }
  return { ...data, base_result: validateBase(data.base_result), metadata } as ResultOverlay;
}

export function decodeSourceRecordIndex(value: unknown): SourceRecordIndex {
  const data = object(value, "source-record index");
  exactFields(data, [
    "canonical_record_sha256",
    "claim_event_id",
    "owner_login",
    "result_id",
    "results_commit",
    "results_path",
    "results_repository",
    "schema_version",
    "source_record_id",
  ], "source-record index");
  validateBase({
    canonical_record_sha256: data.canonical_record_sha256,
    declared_model: "placeholder",
    problem_id: "placeholder",
    results_commit: data.results_commit,
    results_path: data.results_path,
    results_repository: data.results_repository,
    statement_revision: 1,
  });
  if (
    data.schema_version !== 1 ||
    typeof data.source_record_id !== "string" ||
    !SOURCE_RECORD_ID.test(data.source_record_id) ||
    typeof data.result_id !== "string" ||
    !RESULT_ID.test(data.result_id) ||
    typeof data.owner_login !== "string" ||
    !LOGIN.test(data.owner_login) ||
    typeof data.claim_event_id !== "string" ||
    !EVENT_ID.test(data.claim_event_id)
  ) {
    throw new TypeError("source-record index values are invalid");
  }
  return {
    schema_version: 1,
    source_record_id: data.source_record_id,
    result_id: data.result_id,
    owner_login: data.owner_login,
    claim_event_id: data.claim_event_id,
    results_repository: RESULTS_REPOSITORY,
    results_commit: data.results_commit as string,
    results_path: data.results_path as string,
    canonical_record_sha256: data.canonical_record_sha256 as string,
  };
}

export function decodeResultReleaseStatusView(
  value: unknown,
): ResultReleaseStatusView {
  const data = object(value, "result release-status view");
  exactFields(data, [
    "authority_event_id",
    "release_event_id",
    "result_id",
    "schema_version",
    "status",
  ], "result release-status view");
  if (
    data.schema_version !== 1 ||
    typeof data.result_id !== "string" ||
    !RESULT_ID.test(data.result_id) ||
    typeof data.authority_event_id !== "string" ||
    !EVENT_ID.test(data.authority_event_id) ||
    typeof data.status !== "string" ||
    !new Set<string>([
      "not_scheduled",
      "scheduled",
      "running",
      "published",
      "failed",
      "cancelled",
      "removed",
    ]).has(data.status) ||
    (data.status === "not_scheduled" && data.release_event_id !== null) ||
    (data.status !== "not_scheduled" &&
      (typeof data.release_event_id !== "string" || !EVENT_ID.test(data.release_event_id)))
  ) {
    throw new TypeError("result release-status view values are invalid");
  }
  return data as ResultReleaseStatusView;
}

export function initialResultReleaseStatusView(
  resultIdentifier: string,
  authorityEventId: string,
  releaseEventId: string | null = null,
): ResultReleaseStatusView {
  return decodeResultReleaseStatusView({
    schema_version: 1,
    result_id: resultIdentifier,
    authority_event_id: authorityEventId,
    status: releaseEventId === null ? "not_scheduled" : "scheduled",
    release_event_id: releaseEventId,
  });
}

export function claimedGuard(resultIdentifier: string, eventId: string): ResultIdentityGuard {
  return decodeResultIdentityGuard({
    schema_version: 1,
    result_id: resultIdentifier,
    record_kind: "claimed",
    authority_event_id: eventId,
  });
}

export function recordedGuard(resultIdentifier: string, eventId: string): ResultIdentityGuard {
  return decodeResultIdentityGuard({
    schema_version: 1,
    result_id: resultIdentifier,
    record_kind: "recorded",
    authority_event_id: eventId,
  });
}

export function claimedOverlay(
  verified: VerifiedLegacyResult,
  eventId: string,
  occurredAt: string,
): ResultOverlay {
  return decodeResultOverlay({
    schema_version: 1,
    result_id: verified.resultId,
    owner_login: verified.ownerLogin,
    claim_event_id: eventId,
    mutation_event_id: eventId,
    claimed_at: occurredAt,
    base_result: verified.baseResult,
    metadata: {},
  });
}

export async function claimedSourceIndex(
  verified: VerifiedLegacyResult,
  eventId: string,
): Promise<SourceRecordIndex> {
  return decodeSourceRecordIndex({
    schema_version: 1,
    source_record_id: await sourceRecordId(verified.baseResult),
    result_id: verified.resultId,
    owner_login: verified.ownerLogin,
    claim_event_id: eventId,
    results_repository: verified.baseResult.results_repository,
    results_commit: verified.baseResult.results_commit,
    results_path: verified.baseResult.results_path,
    canonical_record_sha256: verified.baseResult.canonical_record_sha256,
  });
}

export function backfilledOverlay(
  current: ResultOverlay,
  eventId: string,
  occurredAt: string,
  metadata: ProductionMetadata,
): ResultOverlay {
  const nextMetadata: Record<string, MetadataProvenance> = { ...current.metadata };
  for (const [field, value] of Object.entries(metadata)) {
    nextMetadata[field] = {
      value,
      provenance: "backfilled",
      event_id: eventId,
      recorded_at: occurredAt,
    };
  }
  return decodeResultOverlay({
    ...current,
    mutation_event_id: eventId,
    metadata: nextMetadata,
  });
}

export function metadataAlreadyEqual(
  current: ResultOverlay,
  metadata: ProductionMetadata,
): boolean {
  return Object.entries(metadata).every(([field, value]) => {
    const provenance = current.metadata[field];
    return provenance !== undefined && canonicalJson(provenance.value) === canonicalJson(value);
  });
}
