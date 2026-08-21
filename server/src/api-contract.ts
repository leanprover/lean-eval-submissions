const UUID_V7 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const COMMIT = /^[0-9a-f]{40}$/;
const PROBLEM = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const GIST_ID = /^[0-9a-f]{5,64}$/;

export const MAX_JSON_BYTES = 16 * 1024;

export type ProblemGroup =
  | "formalization-evaluation"
  | "software-verification"
  | "open-conjectures";
export type PublicationChoice = "scheduled" | "withheld";
export type SourceVisibility = "private" | "public";

export type ProductionMetadata = Readonly<{
  credit_identity?: string;
  component_models?: readonly string[];
  harness?: string;
  human_involvement?: string;
  web_access?: boolean;
  wall_time_seconds?: number;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  billing_mode?: "api" | "subscription" | "unknown";
  prompt?: string;
  notes?: string;
}>;

export type SubmissionInput = Readonly<{
  problem_id: string;
  problem_group: ProblemGroup;
  statement_revision: number;
  declared_model: string;
  source_repository: string;
  source_commit: string;
  source_visibility: SourceVisibility;
  publication_choice: PublicationChoice;
  production_metadata: ProductionMetadata;
}>;

export type AgentChallengeInput = Readonly<{
  login: string;
  gist_id: string;
  source_repository: string;
  source_commit: string;
}>;

export class ApiDecodeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiDecodeError";
  }
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiDecodeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactFields(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
  label: string,
): void {
  const permitted = new Set([...required, ...optional]);
  const extras = Object.keys(value).filter((key) => !permitted.has(key));
  const missing = required.filter((key) => !(key in value));
  if (extras.length > 0 || missing.length > 0) {
    throw new ApiDecodeError(`${label} has unknown or missing fields`);
  }
}

function boundedString(value: unknown, label: string, maximum: number): string {
  let codePoints = 0;
  let hasControl = false;
  if (typeof value === "string") {
    for (const character of value) {
      codePoints += 1;
      const code = character.codePointAt(0) ?? 0;
      if (code <= 31 || code === 127) hasControl = true;
    }
  }
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    codePoints > maximum ||
    hasControl
  ) {
    throw new ApiDecodeError(`${label} must be nonempty, bounded text without controls`);
  }
  return value;
}

function boundedUtf8String(value: unknown, label: string, maximumBytes: number): string {
  const text = boundedString(value, label, maximumBytes);
  if (new TextEncoder().encode(text).length > maximumBytes) {
    throw new ApiDecodeError(`${label} must contain at most ${String(maximumBytes)} UTF-8 bytes`);
  }
  return text;
}

function safeNatural(value: unknown, label: string, positive = false): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < (positive ? 1 : 0)
  ) {
    throw new ApiDecodeError(`${label} must be a safe ${positive ? "positive" : "nonnegative"} integer`);
  }
  return value;
}

function optionalBoundedString(
  value: unknown,
  label: string,
  maximum: number,
): string | undefined {
  return value === undefined ? undefined : boundedString(value, label, maximum);
}

export function decodeProductionMetadata(value: unknown): ProductionMetadata {
  const data = object(value, "production_metadata");
  const fields = [
    "billing_mode",
    "component_models",
    "cost_usd",
    "credit_identity",
    "harness",
    "human_involvement",
    "input_tokens",
    "notes",
    "output_tokens",
    "prompt",
    "wall_time_seconds",
    "web_access",
  ] as const;
  exactFields(data, [], fields, "production_metadata");
  let componentModels: readonly string[] | undefined;
  if (data.component_models !== undefined) {
    if (!Array.isArray(data.component_models) || data.component_models.length > 16) {
      throw new ApiDecodeError("component_models must be an array with at most 16 entries");
    }
    componentModels = data.component_models.map((entry, index) =>
      boundedString(entry, `component_models[${String(index)}]`, 256),
    );
  }
  const cost = data.cost_usd;
  if (
    cost !== undefined &&
    (typeof cost !== "number" || !Number.isFinite(cost) || cost < 0 || cost > 1_000_000)
  ) {
    throw new ApiDecodeError("cost_usd must be a finite nonnegative number");
  }
  const wallTime = data.wall_time_seconds;
  if (
    wallTime !== undefined &&
    (typeof wallTime !== "number" || !Number.isFinite(wallTime) || wallTime < 0 || wallTime > 31_536_000)
  ) {
    throw new ApiDecodeError("wall_time_seconds must be a finite nonnegative number");
  }
  if (data.web_access !== undefined && typeof data.web_access !== "boolean") {
    throw new ApiDecodeError("web_access must be boolean");
  }
  const billingModes = new Set(["api", "subscription", "unknown"] as const);
  if (
    data.billing_mode !== undefined &&
    (typeof data.billing_mode !== "string" || !billingModes.has(data.billing_mode as "api" | "subscription" | "unknown"))
  ) {
    throw new ApiDecodeError("billing_mode is invalid");
  }
  const result: { -readonly [Key in keyof ProductionMetadata]?: ProductionMetadata[Key] } = {};
  const textFields = [
    ["credit_identity", 256],
    ["harness", 1024],
    ["human_involvement", 1024],
    ["prompt", 8192],
    ["notes", 4096],
  ] as const;
  for (const [field, maximum] of textFields) {
    const decoded = optionalBoundedString(data[field], field, maximum);
    if (decoded !== undefined) result[field] = decoded;
  }
  if (componentModels !== undefined) result.component_models = componentModels;
  if (data.web_access !== undefined) result.web_access = data.web_access;
  if (wallTime !== undefined) result.wall_time_seconds = wallTime;
  if (data.input_tokens !== undefined) result.input_tokens = safeNatural(data.input_tokens, "input_tokens");
  if (data.output_tokens !== undefined) result.output_tokens = safeNatural(data.output_tokens, "output_tokens");
  if (cost !== undefined) result.cost_usd = cost;
  if (data.billing_mode !== undefined) {
    result.billing_mode = data.billing_mode as Exclude<ProductionMetadata["billing_mode"], undefined>;
  }
  return result;
}

export function decodeSubmissionInput(value: unknown): SubmissionInput {
  const data = object(value, "submission");
  exactFields(
    data,
    [
      "declared_model",
      "problem_group",
      "problem_id",
      "production_metadata",
      "publication_choice",
      "source_commit",
      "source_repository",
      "source_visibility",
      "statement_revision",
    ],
    [],
    "submission",
  );
  if (typeof data.problem_id !== "string" || !PROBLEM.test(data.problem_id)) {
    throw new ApiDecodeError("problem_id is not canonical");
  }
  const groups = new Set<unknown>([
    "formalization-evaluation",
    "software-verification",
    "open-conjectures",
  ]);
  if (!groups.has(data.problem_group)) throw new ApiDecodeError("problem_group is invalid");
  if (typeof data.source_repository !== "string" || !REPOSITORY.test(data.source_repository)) {
    throw new ApiDecodeError("source_repository is not canonical");
  }
  if (typeof data.source_commit !== "string" || !COMMIT.test(data.source_commit)) {
    throw new ApiDecodeError("source_commit must be a lowercase 40-character commit");
  }
  if (data.source_visibility !== "private" && data.source_visibility !== "public") {
    throw new ApiDecodeError("source_visibility is invalid");
  }
  if (data.publication_choice !== "scheduled" && data.publication_choice !== "withheld") {
    throw new ApiDecodeError("publication_choice is invalid");
  }
  return {
    problem_id: data.problem_id,
    problem_group: data.problem_group as ProblemGroup,
    statement_revision: safeNatural(data.statement_revision, "statement_revision", true),
    declared_model: boundedUtf8String(data.declared_model, "declared_model", 256),
    source_repository: data.source_repository,
    source_commit: data.source_commit,
    source_visibility: data.source_visibility,
    publication_choice: data.publication_choice,
    production_metadata: decodeProductionMetadata(data.production_metadata),
  };
}

export function decodeAgentChallengeInput(value: unknown): AgentChallengeInput {
  const data = object(value, "agent challenge");
  exactFields(data, ["gist_id", "login", "source_commit", "source_repository"], [], "agent challenge");
  if (typeof data.login !== "string" || !LOGIN.test(data.login)) {
    throw new ApiDecodeError("login must be canonical lowercase GitHub login");
  }
  if (typeof data.gist_id !== "string" || !GIST_ID.test(data.gist_id)) {
    throw new ApiDecodeError("gist_id is not canonical");
  }
  if (typeof data.source_repository !== "string" || !REPOSITORY.test(data.source_repository)) {
    throw new ApiDecodeError("source_repository is not canonical");
  }
  if (typeof data.source_commit !== "string" || !COMMIT.test(data.source_commit)) {
    throw new ApiDecodeError("source_commit must be a lowercase 40-character commit");
  }
  return {
    login: data.login,
    gist_id: data.gist_id,
    source_repository: data.source_repository,
    source_commit: data.source_commit,
  };
}

export function decodeChallengeSubmission(value: unknown): {
  challenge: string;
  submission: SubmissionInput;
} {
  const data = object(value, "agent submission");
  exactFields(data, ["challenge", "submission"], [], "agent submission");
  return {
    challenge: boundedString(data.challenge, "challenge", 8192),
    submission: decodeSubmissionInput(data.submission),
  };
}

export function decodeBrowserSubmission(value: unknown): {
  grant: string;
  submission: SubmissionInput;
} {
  const data = object(value, "browser submission");
  exactFields(data, ["grant", "submission"], [], "browser submission");
  return {
    grant: boundedString(data.grant, "grant", 8192),
    submission: decodeSubmissionInput(data.submission),
  };
}

export function decodeMetadataAmendment(value: unknown): ProductionMetadata {
  const data = object(value, "metadata amendment");
  exactFields(data, ["production_metadata"], [], "metadata amendment");
  const metadata = decodeProductionMetadata(data.production_metadata);
  if (Object.keys(metadata).length === 0) {
    throw new ApiDecodeError("metadata amendment must not be empty");
  }
  return metadata;
}

export function decodePublicationChoice(value: unknown): PublicationChoice {
  const data = object(value, "publication choice");
  exactFields(data, ["publication_choice"], [], "publication choice");
  if (data.publication_choice !== "scheduled" && data.publication_choice !== "withheld") {
    throw new ApiDecodeError("publication_choice is invalid");
  }
  return data.publication_choice;
}

export function assertSourcePolicy(
  group: ProblemGroup,
  declared: SourceVisibility,
  actualPrivate: boolean,
): void {
  const actual = actualPrivate ? "private" : "public";
  if (declared !== actual) throw new ApiDecodeError("declared source visibility does not match GitHub");
  const required = group === "open-conjectures" ? "public" : "private";
  if (actual !== required) {
    throw new ApiDecodeError(`${group} submissions require ${required} source`);
  }
}

export function isUuidV7(value: string): boolean {
  return UUID_V7.test(value);
}

export async function readJson(request: Request): Promise<unknown> {
  const mediaType = request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (mediaType !== "application/json") throw new ApiDecodeError("content-type must be application/json");
  const length = request.headers.get("content-length");
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > MAX_JSON_BYTES)) {
    throw new ApiDecodeError("request body is too large");
  }
  if (!request.body) throw new ApiDecodeError("request body is required");
  const reader = (request.body as ReadableStream<Uint8Array>).getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > MAX_JSON_BYTES) {
      await reader.cancel();
      throw new ApiDecodeError("request body is too large");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes)) as unknown;
  } catch {
    throw new ApiDecodeError("request body must be valid UTF-8 JSON");
  }
}
