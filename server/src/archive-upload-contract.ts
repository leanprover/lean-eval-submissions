// Chunked raw-binary archive upload.
//
// The replay transport used to carry the whole archive base64-encoded inside one
// JSON body, which an isolate had to parse and digest in full. That capped the
// archive at what fits in a 128 MB isolate alongside its own base64 text. Parts
// now arrive as `application/octet-stream` and are streamed straight into the
// Sandbox, so isolate memory is bounded by one chunk rather than one archive.
//
// Application metadata travels in `x-lean-eval-*` headers because the body is the
// payload. Only that namespace is validated as an exact set: the full header
// collection carries Cloudflare and proxy additions that we neither control nor
// should reject.

const DIGEST = /^[0-9a-f]{64}$/;
const HEADER_PREFIX = "x-lean-eval-";

// One part is bounded well below any request body limit, and deliberately below
// the smallest archive bound this transport serves, so that the multi-part path
// is exercised by ordinary traffic rather than only by the largest archives.
export const MAX_PART_BYTES = 8 * 1024 * 1024;
export const MAX_PART_COUNT = 32;
// Raised with the archive cap. The aggregate, not the part count, is the bound
// that matters: a client controls how many parts it declares.
export const MAX_ARCHIVE_BYTES = 11 * 1024 * 1024;
const MAX_FINALIZE_REQUEST_BYTES = 64 * 1024;

export const ARCHIVE_UPLOAD_KINDS = [
  "authoritative-archive",
  "staging-archive-acceptance",
  "historical-public-source",
] as const;

export type ArchiveUploadKind = typeof ARCHIVE_UPLOAD_KINDS[number];

/** Identifies one upload. Every part and the finalize must carry it unchanged. */
export type ArchiveUploadIdentity = {
  schema_version: 1;
  upload_kind: ArchiveUploadKind;
  runner_nonce: string;
  archive_sha256: string;
  archive_bytes: number;
  part_count: number;
};

export type ArchiveUploadPartHeader = ArchiveUploadIdentity & {
  part_index: number;
  part_sha256: string;
  part_bytes: number;
};

export type ArchiveUploadFinalizeRequest = ArchiveUploadIdentity & {
  parts: readonly { index: number; sha256: string; bytes: number }[];
};

export class ArchiveUploadContractError extends Error {}

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ArchiveUploadContractError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactFields(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new ArchiveUploadContractError(`${label} fields are not canonical`);
  }
}

function digest(value: unknown, label: string): string {
  if (typeof value !== "string" || !DIGEST.test(value)) {
    throw new ArchiveUploadContractError(`${label} is invalid`);
  }
  return value;
}

function boundedInteger(value: unknown, label: string, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    throw new ArchiveUploadContractError(`${label} is invalid`);
  }
  return value as number;
}

function uploadKind(value: unknown, label: string): ArchiveUploadKind {
  if (
    typeof value !== "string"
    || !(ARCHIVE_UPLOAD_KINDS as readonly string[]).includes(value)
  ) {
    throw new ArchiveUploadContractError(`${label} is invalid`);
  }
  return value as ArchiveUploadKind;
}

/**
 * Decimal integer from a header. Headers are text, so this rejects anything a
 * `Number()` coercion would quietly accept: signs, whitespace, exponents, hex.
 */
function headerInteger(
  headers: Record<string, string>,
  name: string,
  minimum: number,
  maximum: number,
): number {
  const raw = headers[HEADER_PREFIX + name];
  if (raw === undefined || !/^(0|[1-9][0-9]{0,15})$/.test(raw)) {
    throw new ArchiveUploadContractError(`${name} header is invalid`);
  }
  return boundedInteger(Number(raw), `${name} header`, minimum, maximum);
}

/**
 * Collect the `x-lean-eval-*` headers and require exactly `expected`. Headers
 * outside that namespace are ignored: the transport adds its own.
 */
function applicationHeaders(
  incoming: Request,
  expected: readonly string[],
): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [name, value] of incoming.headers) {
    const lowered = name.toLowerCase();
    if (lowered.startsWith(HEADER_PREFIX)) headers[lowered] = value;
  }
  const actual = Object.keys(headers).sort();
  const wanted = expected.map((name) => HEADER_PREFIX + name).sort();
  if (actual.length !== wanted.length || actual.some((name, index) => name !== wanted[index])) {
    throw new ArchiveUploadContractError("upload headers are not canonical");
  }
  return headers;
}

function identityFromHeaders(headers: Record<string, string>): ArchiveUploadIdentity {
  const partCount = headerInteger(headers, "part-count", 1, MAX_PART_COUNT);
  const archiveBytes = headerInteger(headers, "archive-bytes", 1, MAX_ARCHIVE_BYTES);
  // A declared part count must be able to cover the declared archive with the
  // fixed part size, and must not claim more parts than that needs. Both
  // directions matter: the first rejects a truncated upload that would finalize
  // as complete, the second rejects padding the part list with empty slots.
  if (partCount !== Math.ceil(archiveBytes / MAX_PART_BYTES)) {
    throw new ArchiveUploadContractError("part count does not match the declared archive size");
  }
  return {
    schema_version: 1,
    upload_kind: uploadKind(headers[HEADER_PREFIX + "upload-kind"], "upload_kind header"),
    runner_nonce: digest(headers[HEADER_PREFIX + "runner-nonce"], "runner_nonce header"),
    archive_sha256: digest(headers[HEADER_PREFIX + "archive-sha256"], "archive_sha256 header"),
    archive_bytes: archiveBytes,
    part_count: partCount,
  };
}

/**
 * Validate one part's headers. The body is deliberately left unread: the caller
 * streams it, and the declared `part_bytes` is checked against the bytes that
 * actually arrive rather than trusted here. `content-length` is optional and
 * client-controlled, so it is not the bound.
 */
export function readArchiveUploadPartHeader(incoming: Request): ArchiveUploadPartHeader {
  const contentType = incoming.headers.get("content-type") ?? "";
  if ((contentType.split(";")[0] ?? "").trim().toLowerCase() !== "application/octet-stream") {
    throw new ArchiveUploadContractError("upload part must be application/octet-stream");
  }
  if (incoming.headers.get("content-encoding") !== null) {
    throw new ArchiveUploadContractError("upload part must not be content-encoded");
  }
  if (incoming.body === null) {
    throw new ArchiveUploadContractError("upload part requires a body");
  }
  const headers = applicationHeaders(incoming, [
    "upload-kind",
    "runner-nonce",
    "archive-sha256",
    "archive-bytes",
    "part-count",
    "part-index",
    "part-sha256",
    "part-bytes",
  ]);
  const identity = identityFromHeaders(headers);
  const partIndex = headerInteger(headers, "part-index", 0, identity.part_count - 1);
  const partBytes = headerInteger(headers, "part-bytes", 1, MAX_PART_BYTES);
  const isLast = partIndex === identity.part_count - 1;
  const expectedBytes = isLast
    ? identity.archive_bytes - partIndex * MAX_PART_BYTES
    : MAX_PART_BYTES;
  // Fixed part size means every part's length is determined by the identity, so
  // a part that lies about its own size cannot be assembled into the declared
  // archive. Checking it here fails before any bytes reach the Sandbox.
  if (partBytes !== expectedBytes) {
    throw new ArchiveUploadContractError("part size does not match its position in the archive");
  }
  return {
    ...identity,
    part_index: partIndex,
    part_sha256: digest(headers[HEADER_PREFIX + "part-sha256"], "part_sha256 header"),
    part_bytes: partBytes,
  };
}

export async function readArchiveUploadFinalizeRequest(
  incoming: Request,
): Promise<ArchiveUploadFinalizeRequest> {
  const contentLength = incoming.headers.get("content-length");
  if (
    contentLength !== null
    && (!/^\d+$/.test(contentLength) || Number(contentLength) > MAX_FINALIZE_REQUEST_BYTES)
  ) {
    throw new ArchiveUploadContractError("request exceeds the size limit");
  }
  const bytes = new Uint8Array(await incoming.arrayBuffer());
  if (bytes.length === 0 || bytes.length > MAX_FINALIZE_REQUEST_BYTES) {
    throw new ArchiveUploadContractError("request exceeds the size limit");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes));
  } catch {
    throw new ArchiveUploadContractError("request is not one UTF-8 JSON object");
  }
  const outer = object(parsed, "request");
  exactFields(outer, [
    "schema_version",
    "upload_kind",
    "runner_nonce",
    "archive_sha256",
    "archive_bytes",
    "part_count",
    "parts",
  ], "request");
  if (outer.schema_version !== 1) {
    throw new ArchiveUploadContractError("request schema_version must be integer 1");
  }
  const partCount = boundedInteger(outer.part_count, "part_count", 1, MAX_PART_COUNT);
  const archiveBytes = boundedInteger(outer.archive_bytes, "archive_bytes", 1, MAX_ARCHIVE_BYTES);
  if (partCount !== Math.ceil(archiveBytes / MAX_PART_BYTES)) {
    throw new ArchiveUploadContractError("part count does not match the declared archive size");
  }
  if (!Array.isArray(outer.parts) || outer.parts.length !== partCount) {
    throw new ArchiveUploadContractError("parts must list every declared part exactly once");
  }
  const parts = outer.parts.map((entry, position) => {
    const part = object(entry, `part ${String(position)}`);
    exactFields(part, ["index", "sha256", "bytes"], `part ${String(position)}`);
    // Positional equality rather than a sort: it pins the caller's ordering to
    // the assembly order, so a reordered manifest cannot finalize.
    if (part.index !== position) {
      throw new ArchiveUploadContractError("parts must be listed in index order");
    }
    const isLast = position === partCount - 1;
    const expectedBytes = isLast ? archiveBytes - position * MAX_PART_BYTES : MAX_PART_BYTES;
    return {
      index: position,
      sha256: digest(part.sha256, `part ${String(position)} sha256`),
      bytes: boundedInteger(part.bytes, `part ${String(position)} bytes`, expectedBytes, expectedBytes),
    };
  });
  const total = parts.reduce((sum, part) => sum + part.bytes, 0);
  if (total !== archiveBytes) {
    throw new ArchiveUploadContractError("parts do not sum to the declared archive size");
  }
  return {
    schema_version: 1,
    upload_kind: uploadKind(outer.upload_kind, "upload_kind"),
    runner_nonce: digest(outer.runner_nonce, "runner_nonce"),
    archive_sha256: digest(outer.archive_sha256, "archive_sha256"),
    archive_bytes: archiveBytes,
    part_count: partCount,
    parts,
  };
}

/** True when two upload identities name the same upload. */
export function sameArchiveUploadIdentity(
  left: ArchiveUploadIdentity,
  right: ArchiveUploadIdentity,
): boolean {
  return left.upload_kind === right.upload_kind
    && left.runner_nonce === right.runner_nonce
    && left.archive_sha256 === right.archive_sha256
    && left.archive_bytes === right.archive_bytes
    && left.part_count === right.part_count;
}
