const UUID_V7 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const COMMIT = /^[0-9a-f]{40}$/;
const PROBLEM = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const GIST_ID = /^[0-9a-f]{5,64}$/;
const REASON = /^[a-z][a-z0-9_]{1,63}$/;
const TOOLCHAIN = /^leanprover\/lean4:v[0-9]+\.[0-9]+\.[0-9]+$/;
const RESULT_ID = /^r2_[0-9a-f]{64}$/;
const DIGEST = /^[0-9a-f]{64}$/;

export const MAX_JSON_BYTES = 16 * 1024;

export type ProblemGroup =
  | "formalization-evaluation"
  | "software-verification"
  | "open-conjectures";
export type IntakeProblemGroup = Exclude<ProblemGroup, "open-conjectures">;
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

export type IntakeSubmissionInput = Omit<SubmissionInput, "problem_group" | "source_visibility"> &
  Readonly<{
    problem_group: IntakeProblemGroup;
    source_visibility: "private";
  }>;

export type AgentChallengeInput = Readonly<{
  login: string;
  gist_id: string;
  source_repository: string;
  source_commit: string;
}>;

export type ArchiveLocator = Readonly<{
  schema_version: 1;
  submission_id: string;
  archive_repository: string;
  archive_commit: string;
  archive_path: string;
  archive_ciphertext_sha256: string;
  encrypted: true;
}>;

export type ArchiveCompletion = Readonly<{
  schema_version: 1;
  occurred_at: string;
  locator: ArchiveLocator;
}>;
export type ArchiveFailure = Readonly<{
  schema_version: 1;
  submission_id: string;
  occurred_at: string;
  reason_code: string;
  retryable: boolean;
}>;
export type EvaluationOutcome =
  | Readonly<{ status: "accepted"; evaluator_version: string }>
  | Readonly<{ status: "rejected"; reason_code: string }>
  | Readonly<{ status: "failed"; reason_code: string; retryable: boolean }>;
export type EvaluationCompletion = Readonly<{
  schema_version: 1;
  submission_id: string;
  attempt: number;
  occurred_at: string;
  benchmark_repository: string;
  benchmark_commit: string;
  toolchain: string;
  outcome: EvaluationOutcome;
}>;
export type ResultCompletion = Readonly<{
  schema_version: 1;
  submission_id: string;
  occurred_at: string;
  result_id: string;
  problem_id: string;
  statement_revision: number;
  result_repository: "leanprover/lean-eval-submissions";
  result_branch: "main" | "staging-results";
  result_commit: string;
  result_path: string;
  result_tree_digest: string;
}>;
export type LegacyResultClaimInput = Readonly<{
  result_id: string;
  results_commit: string;
}>;
export type ProblemRepairRequestInput = Readonly<{
  corrected_problem_id: string;
  corrected_statement_revision: number;
  reason_code: string;
}>;
export type ResultRetractionRequestInput = Readonly<{
  reason_code: string;
}>;

export type ResultRetractionDecisionInput = Readonly<{
  decision: "approve" | "reject";
  reason_code: string;
}>;

export type ResultRetractionOverrideInput = Readonly<{
  reason_code: string;
}>;

export type ProblemRepairDecisionInput =
  | Readonly<{ decision: "apply"; results_commit: string }>
  | Readonly<{ decision: "reject"; reason_code: string }>;

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

// Persisted State views and dispatch outboxes retain retired problem groups.
// New admission must use decodeIntakeSubmissionInput below.
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

export function decodeIntakeSubmissionInput(value: unknown): IntakeSubmissionInput {
  const input = decodeSubmissionInput(value);
  if (input.problem_group === "open-conjectures") {
    throw new ApiDecodeError("problem_group is not accepted for new submissions");
  }
  if (input.source_visibility !== "private") {
    throw new ApiDecodeError("source_visibility is not accepted for new submissions");
  }
  return input as IntakeSubmissionInput;
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
  submission: IntakeSubmissionInput;
} {
  const data = object(value, "agent submission");
  exactFields(data, ["challenge", "submission"], [], "agent submission");
  return {
    challenge: boundedString(data.challenge, "challenge", 8192),
    submission: decodeIntakeSubmissionInput(data.submission),
  };
}

export function decodeBrowserSubmission(value: unknown): {
  grant: string;
  submission: IntakeSubmissionInput;
} {
  const data = object(value, "browser submission");
  exactFields(data, ["grant", "submission"], [], "browser submission");
  return {
    grant: boundedString(data.grant, "grant", 8192),
    submission: decodeIntakeSubmissionInput(data.submission),
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

export function decodeLegacyResultClaim(value: unknown): LegacyResultClaimInput {
  const data = object(value, "legacy result claim");
  exactFields(data, ["result_id", "results_commit"], [], "legacy result claim");
  if (typeof data.result_id !== "string" || !RESULT_ID.test(data.result_id)) {
    throw new ApiDecodeError("result_id is not a schema-version-2 result identity");
  }
  if (typeof data.results_commit !== "string" || !COMMIT.test(data.results_commit)) {
    throw new ApiDecodeError("results_commit must be a lowercase 40-character commit");
  }
  return { result_id: data.result_id, results_commit: data.results_commit };
}

export function decodeProblemRepairRequest(value: unknown): ProblemRepairRequestInput {
  const data = object(value, "problem repair request");
  exactFields(data, ["corrected_problem_id", "corrected_statement_revision", "reason_code"], [], "problem repair request");
  if (typeof data.corrected_problem_id !== "string" || !PROBLEM.test(data.corrected_problem_id)) {
    throw new ApiDecodeError("corrected_problem_id is invalid");
  }
  const correctedStatementRevision = safeNatural(
    data.corrected_statement_revision,
    "corrected_statement_revision",
    true,
  );
  if (typeof data.reason_code !== "string" || !REASON.test(data.reason_code)) {
    throw new ApiDecodeError("reason_code is invalid");
  }
  return {
    corrected_problem_id: data.corrected_problem_id,
    corrected_statement_revision: correctedStatementRevision,
    reason_code: data.reason_code,
  };
}

export function decodeResultRetractionRequest(value: unknown): ResultRetractionRequestInput {
  const data = object(value, "result retraction request");
  exactFields(data, ["reason_code"], [], "result retraction request");
  if (typeof data.reason_code !== "string" || !REASON.test(data.reason_code)) {
    throw new ApiDecodeError("reason_code is invalid");
  }
  return { reason_code: data.reason_code };
}

export function decodeResultRetractionDecision(value: unknown): ResultRetractionDecisionInput {
  const data = object(value, "result retraction decision");
  exactFields(data, ["decision", "reason_code"], [], "result retraction decision");
  if (data.decision !== "approve" && data.decision !== "reject") {
    throw new ApiDecodeError("result retraction decision is invalid");
  }
  if (typeof data.reason_code !== "string" || !REASON.test(data.reason_code)) {
    throw new ApiDecodeError("reason_code is invalid");
  }
  return { decision: data.decision, reason_code: data.reason_code };
}

export function decodeResultRetractionOverride(value: unknown): ResultRetractionOverrideInput {
  const data = object(value, "result retraction override");
  exactFields(data, ["reason_code"], [], "result retraction override");
  if (typeof data.reason_code !== "string" || !REASON.test(data.reason_code)) {
    throw new ApiDecodeError("reason_code is invalid");
  }
  return { reason_code: data.reason_code };
}

export function decodeProblemRepairDecision(value: unknown): ProblemRepairDecisionInput {
  const data = object(value, "problem repair decision");
  if (data.decision === "apply") {
    exactFields(data, ["decision", "results_commit"], [], "problem repair apply decision");
    if (typeof data.results_commit !== "string" || !COMMIT.test(data.results_commit)) {
      throw new ApiDecodeError("results_commit must be a lowercase 40-character commit");
    }
    return { decision: "apply", results_commit: data.results_commit };
  }
  if (data.decision === "reject") {
    exactFields(data, ["decision", "reason_code"], [], "problem repair reject decision");
    if (typeof data.reason_code !== "string" || !REASON.test(data.reason_code)) {
      throw new ApiDecodeError("reason_code is invalid");
    }
    return { decision: "reject", reason_code: data.reason_code };
  }
  throw new ApiDecodeError("problem repair decision is invalid");
}

export function decodeEmptyObject(value: unknown, label: string): Readonly<Record<string, never>> {
  const data = object(value, label);
  exactFields(data, [], [], label);
  return {};
}

export function decodePublicationChoice(value: unknown): PublicationChoice {
  const data = object(value, "publication choice");
  exactFields(data, ["publication_choice"], [], "publication choice");
  if (data.publication_choice !== "scheduled" && data.publication_choice !== "withheld") {
    throw new ApiDecodeError("publication_choice is invalid");
  }
  return data.publication_choice;
}

export function decodeArchiveCompletion(value: unknown): ArchiveCompletion {
  const data = object(value, "archive completion");
  exactFields(data, ["locator", "occurred_at", "schema_version"], [], "archive completion");
  if (data.schema_version !== 1 || typeof data.occurred_at !== "string") {
    throw new ApiDecodeError("archive completion version or timestamp is invalid");
  }
  const occurredAt = new Date(data.occurred_at);
  if (
    data.occurred_at.startsWith("0000-") ||
    Number.isNaN(occurredAt.valueOf()) ||
    occurredAt.toISOString() !== data.occurred_at
  ) {
    throw new ApiDecodeError("archive completion timestamp is not canonical UTC milliseconds");
  }
  const locator = object(data.locator, "archive locator");
  exactFields(locator, [
    "archive_ciphertext_sha256",
    "archive_commit",
    "archive_path",
    "archive_repository",
    "encrypted",
    "schema_version",
    "submission_id",
  ], [], "archive locator");
  if (
    locator.schema_version !== 1 ||
    typeof locator.submission_id !== "string" ||
    !UUID_V7.test(locator.submission_id) ||
    typeof locator.archive_repository !== "string" ||
    !REPOSITORY.test(locator.archive_repository) ||
    typeof locator.archive_commit !== "string" ||
    !COMMIT.test(locator.archive_commit) ||
    typeof locator.archive_ciphertext_sha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(locator.archive_ciphertext_sha256) ||
    locator.encrypted !== true
  ) {
    throw new ApiDecodeError("archive locator fields are invalid");
  }
  const expectedPath = `archives/${locator.submission_id.replaceAll("-", "").slice(0, 2)}/${locator.submission_id}.tar.age`;
  if (locator.archive_path !== expectedPath) {
    throw new ApiDecodeError("archive locator path does not match its submission");
  }
  return {
    schema_version: 1,
    occurred_at: data.occurred_at,
    locator: locator as ArchiveLocator,
  };
}

export function decodeArchiveFailure(value: unknown): ArchiveFailure {
  const data = object(value, "archive failure");
  exactFields(data, [
    "occurred_at", "reason_code", "retryable", "schema_version", "submission_id",
  ], [], "archive failure");
  if (
    data.schema_version !== 1 ||
    typeof data.submission_id !== "string" || !UUID_V7.test(data.submission_id) ||
    typeof data.reason_code !== "string" || !REASON.test(data.reason_code) ||
    typeof data.retryable !== "boolean" ||
    typeof data.occurred_at !== "string"
  ) {
    throw new ApiDecodeError("archive failure fields are invalid");
  }
  const occurredAt = new Date(data.occurred_at);
  if (data.occurred_at.startsWith("0000-") || Number.isNaN(occurredAt.valueOf()) || occurredAt.toISOString() !== data.occurred_at) {
    throw new ApiDecodeError("archive failure timestamp is not canonical UTC milliseconds");
  }
  return data as ArchiveFailure;
}

export function decodeEvaluationCompletion(value: unknown): EvaluationCompletion {
  const data = object(value, "evaluation completion");
  exactFields(data, [
    "attempt", "benchmark_commit", "benchmark_repository", "occurred_at", "outcome",
    "schema_version", "submission_id", "toolchain",
  ], [], "evaluation completion");
  if (
    data.schema_version !== 1 ||
    typeof data.submission_id !== "string" || !UUID_V7.test(data.submission_id) ||
    typeof data.attempt !== "number" || !Number.isSafeInteger(data.attempt) || data.attempt < 1 ||
    typeof data.benchmark_repository !== "string" || !REPOSITORY.test(data.benchmark_repository) ||
    typeof data.benchmark_commit !== "string" || !COMMIT.test(data.benchmark_commit) ||
    typeof data.toolchain !== "string" || !TOOLCHAIN.test(data.toolchain) ||
    typeof data.occurred_at !== "string"
  ) {
    throw new ApiDecodeError("evaluation completion identity or pins are invalid");
  }
  const occurredAt = new Date(data.occurred_at);
  if (data.occurred_at.startsWith("0000-") || Number.isNaN(occurredAt.valueOf()) || occurredAt.toISOString() !== data.occurred_at) {
    throw new ApiDecodeError("evaluation completion timestamp is not canonical UTC milliseconds");
  }
  const outcome = object(data.outcome, "evaluation outcome");
  if (outcome.status === "accepted") {
    exactFields(outcome, ["evaluator_version", "status"], [], "evaluation outcome");
    if (typeof outcome.evaluator_version !== "string" || !COMMIT.test(outcome.evaluator_version)) {
      throw new ApiDecodeError("evaluation accepted version is invalid");
    }
  } else if (outcome.status === "rejected") {
    exactFields(outcome, ["reason_code", "status"], [], "evaluation outcome");
    if (typeof outcome.reason_code !== "string" || !REASON.test(outcome.reason_code)) {
      throw new ApiDecodeError("evaluation rejection reason is invalid");
    }
  } else if (outcome.status === "failed") {
    exactFields(outcome, ["reason_code", "retryable", "status"], [], "evaluation outcome");
    if (typeof outcome.reason_code !== "string" || !REASON.test(outcome.reason_code) || typeof outcome.retryable !== "boolean") {
      throw new ApiDecodeError("evaluation failure is invalid");
    }
  } else {
    throw new ApiDecodeError("evaluation outcome status is invalid");
  }
  return { ...data, outcome } as EvaluationCompletion;
}

export function decodeResultCompletion(value: unknown): ResultCompletion {
  const data = object(value, "result completion");
  exactFields(data, [
    "occurred_at", "problem_id", "result_branch", "result_commit", "result_id",
    "result_path", "result_repository", "result_tree_digest", "schema_version",
    "statement_revision", "submission_id",
  ], [], "result completion");
  if (
    data.schema_version !== 1 ||
    typeof data.submission_id !== "string" || !UUID_V7.test(data.submission_id) ||
    typeof data.result_id !== "string" || !RESULT_ID.test(data.result_id) ||
    typeof data.problem_id !== "string" || !PROBLEM.test(data.problem_id) ||
    typeof data.statement_revision !== "number" ||
      !Number.isSafeInteger(data.statement_revision) || data.statement_revision < 1 ||
    data.result_repository !== "leanprover/lean-eval-submissions" ||
    (data.result_branch !== "main" && data.result_branch !== "staging-results") ||
    typeof data.result_commit !== "string" || !COMMIT.test(data.result_commit) ||
    typeof data.result_tree_digest !== "string" || !DIGEST.test(data.result_tree_digest) ||
    typeof data.occurred_at !== "string"
  ) {
    throw new ApiDecodeError("result completion identity or pins are invalid");
  }
  const occurredAt = new Date(data.occurred_at);
  if (
    data.occurred_at.startsWith("0000-") ||
    Number.isNaN(occurredAt.valueOf()) ||
    occurredAt.toISOString() !== data.occurred_at
  ) {
    throw new ApiDecodeError("result completion timestamp is not canonical UTC milliseconds");
  }
  if (
    typeof data.result_path !== "string" ||
    !/^results\/[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?\.json$/.test(data.result_path) ||
    data.result_path !== data.result_path.toLowerCase()
  ) {
    throw new ApiDecodeError("result completion path is invalid");
  }
  return data as ResultCompletion;
}

export function decodeSourceReaderPreflight(value: unknown): string {
  const data = object(value, "source reader preflight");
  exactFields(data, ["repository"], [], "source reader preflight");
  if (typeof data.repository !== "string" || !REPOSITORY.test(data.repository)) {
    throw new ApiDecodeError("source reader preflight repository is not canonical");
  }
  return data.repository;
}

export function assertSourcePolicy(
  group: IntakeProblemGroup,
  declared: "private",
  actualPrivate: boolean,
): void {
  const actual = actualPrivate ? "private" : "public";
  if (declared !== actual) {
    throw new ApiDecodeError("declared source visibility does not match GitHub");
  }
  if (!actualPrivate) {
    throw new ApiDecodeError(`${group} submissions require private source`);
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
