import {
  stateEventPath,
  type StateEvent,
  type WritableResultLifecycleEvent,
  type ResultClaimedEvent,
  type ResultMetadataBackfilledEvent,
  type ResultProblemRepairRequestedEvent,
  type ResultRecordedEvent,
  type ResultAmendmentSystemEvent,
  type ResultRetractionRequestedEvent,
  type WritableSubmissionLifecycleEvent,
  type WritableStateEvent,
  validateStateEvent,
} from "./state-event";
import {
  challengeId,
  comparatorBindingSha256,
  decodeResultAmendmentView,
  decidedProblemRepairView,
  decidedRetractionView,
  initialResultAmendmentView,
  overriddenRetractionView,
  requestedProblemRepairView,
  requestedRetractionView,
  resultAmendmentPath,
  terminalRetractionView,
  type ComparatorEvidence,
  type ProblemRepairState,
  type ResultAmendmentView,
  type RetractionState,
} from "./result-amendment";
import {
  backfilledOverlay,
  canonicalJson as canonicalResultJson,
  claimedGuard,
  claimedOverlay,
  claimedSourceIndex,
  decodeEffectiveResultIdentityReservation,
  decodeResultIdentityGuard,
  decodeResultReleaseStatusView,
  decodeResultOverlay,
  decodeSourceRecordIndex,
  effectiveResultIdentityPath,
  effectiveResultIdentityReservation,
  initialResultReleaseStatusView,
  metadataAlreadyEqual,
  recordedGuard,
  resultIdentityPath,
  resultOverlayPath,
  resultReleaseStatusPath,
  sourceRecordId,
  sourceRecordPath,
  PRODUCTION_RESULT_OWNER_STATE_CONTRACT_COMMIT,
  STAGING_RESULT_OWNER_STATE_CONTRACT_COMMIT,
  type LegacyResultBase,
  type MetadataProvenance,
  type ResultOverlay,
  type ResultReleaseStatusView,
  type VerifiedLegacyResult,
} from "./result-owner";
import type { ProductionMetadata } from "./api-contract";
import {
  decodeDispatchOutbox,
  decodeSubmissionView,
  dispatchOutboxPath,
  latestLifecycleEventId,
  submissionViewPath,
  type DispatchOutbox,
  type SubmissionView,
} from "./submission-view";

const API = "https://api.github.com";
const STATE_BRANCH = "main";
const PRODUCTION_STATE_REPOSITORY = "leanprover/lean-eval-state";
const STAGING_STATE_REPOSITORY = "leanprover/lean-eval-state-staging";
const MAX_WRITE_ATTEMPTS = 8;
const NEW_EVENT_CLOCK_WINDOW_MS = 5 * 60 * 1000;
const SHA = /^[0-9a-f]{40}$/i;
const LOWER_SHA = /^[0-9a-f]{40}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const UUID_V7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const GITHUB_TIMEOUT_MS = 5000;
type ResultOwnerContract = Readonly<{
  commit: string;
  rootEntries: Readonly<Record<string, Readonly<{
    mode: string;
    type: string;
    sha: string;
  }>>>;
}>;
const RESULT_OWNER_CONTRACTS: Readonly<Record<string, ResultOwnerContract>> = {
  [PRODUCTION_STATE_REPOSITORY]: {
    commit: PRODUCTION_RESULT_OWNER_STATE_CONTRACT_COMMIT,
    rootEntries: {
      "README.md": { mode: "100644", type: "blob", sha: "069cc546e53d4ec2a109010f9e02dfffd8fdce06" },
      docs: { mode: "040000", type: "tree", sha: "76e48513a1284d5945a4e1d0a45dbfa84f127325" },
      schema: { mode: "040000", type: "tree", sha: "473e694e0d40026a7ec0ad33430ea622e3e03b66" },
      scripts: { mode: "040000", type: "tree", sha: "ab90d1a997e3bfc7292dbf1a515db1abb4278c01" },
    },
  },
  [STAGING_STATE_REPOSITORY]: {
    commit: STAGING_RESULT_OWNER_STATE_CONTRACT_COMMIT,
    rootEntries: {
      "README.md": { mode: "100644", type: "blob", sha: "a302e9611c7c32dda7462828b6f02ab919ea39a9" },
      docs: { mode: "040000", type: "tree", sha: "defb2fc26f37703008e49cb968c8971266bfb5e5" },
      schema: { mode: "040000", type: "tree", sha: "95a264fb61bffcec21ae91055675baf9c9ed78fc" },
      scripts: { mode: "040000", type: "tree", sha: "41962d05ebc32821a342ae6bc9cd6c2fa88db3eb" },
    },
  },
};
const RESULT_OWNER_CONTRACT_PROOF_CACHE_LIMIT = 64;
const resultOwnerContractProofCache = new Map<string, true>();

export type GitHubFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

const defaultGitHubFetch: GitHubFetch = (input, init) =>
  fetch(input, {
    ...init,
    signal: init?.signal ?? AbortSignal.timeout(GITHUB_TIMEOUT_MS),
  });

type GitHubStateConfig = Readonly<{
  repository: string;
  token: string;
  userAgent: string;
}>;

type BranchSnapshot = Readonly<{
  headSha: string;
  treeSha: string;
}>;

type TreeWrite = Readonly<{ path: string; value: unknown }>;

export type LegacyResultClaimRequest = Readonly<{
  eventId: string;
  occurredAt: string;
  verified: VerifiedLegacyResult;
}>;

export type LegacyResultBackfillRequest = Readonly<{
  eventId: string;
  occurredAt: string;
  resultId: string;
  ownerLogin: string;
  productionMetadata: ProductionMetadata;
}>;

export type ResultRetractionRequest = Readonly<{
  eventId: string;
  occurredAt: string;
  resultId: string;
  ownerLogin: string;
  reasonCode: string;
}>;

export type ResultProblemRepairRequest = Readonly<{
  eventId: string;
  occurredAt: string;
  resultId: string;
  ownerLogin: string;
  correctedProblemId: string;
  correctedStatementRevision: number;
  reasonCode: string;
}>;

export type ResultRetractionDecisionRequest = Readonly<{
  eventId: string;
  occurredAt: string;
  resultId: string;
  reviewerLogin: string;
  decision: "approve" | "reject";
  reasonCode: string;
}>;

export type ResultProblemRepairDecisionRequest = Readonly<{
  eventId: string;
  occurredAt: string;
  resultId: string;
  reviewerLogin: string;
  decision: "apply" | "reject";
  reasonCode: string | null;
  comparatorEvidence: ComparatorEvidence | null;
}>;

export type ResultRetractionOverrideRequest = Readonly<{
  eventId: string;
  occurredAt: string;
  resultId: string;
  reviewerLogin: string;
  reasonCode: string;
}>;

export type ResultRetractionFinalizationRequest = Readonly<{
  eventId: string;
  occurredAt: string;
  resultId: string;
  maintainerLogin: string;
}>;

export class GitHubStateError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(`GitHub State ${String(status)}: ${message}`);
    this.name = "GitHubStateError";
    this.status = status;
  }
}

/** Expected owner-API absence or contention; other State failures remain retryable. */
export class ResultOwnerStateError extends Error {
  readonly status: 404 | 409;

  constructor(status: 404 | 409, message: string) {
    super(message);
    this.name = "ResultOwnerStateError";
    this.status = status;
  }
}

export class ResultIdentityCollisionError extends Error {
  readonly existingKind: "claimed" | "recorded";

  constructor(existingKind: "claimed" | "recorded") {
    super(`result identity is already reserved by ${existingKind} authority`);
    this.name = "ResultIdentityCollisionError";
    this.existingKind = existingKind;
  }
}

export class StateUpdateOutcomeUnknownError extends Error {
  constructor() {
    super("GitHub State reference update outcome is unknown");
    this.name = "StateUpdateOutcomeUnknownError";
  }
}

export class StateEventConflictError extends Error {
  constructor(path: string) {
    super(`immutable State event already exists at ${path}`);
    this.name = "StateEventConflictError";
  }
}

export function clearResultOwnerContractProofCacheForTest(): void {
  resultOwnerContractProofCache.clear();
}

function resultOwnerContract(repository: string): ResultOwnerContract {
  const key = repository.toLowerCase();
  const contract = RESULT_OWNER_CONTRACTS[key];
  if (!Object.hasOwn(RESULT_OWNER_CONTRACTS, key) || contract === undefined) {
    throw new GitHubStateError(503, "result-owner contract targeted an unsupported State repository");
  }
  return contract;
}

function resultOwnerContractProofCacheKey(repository: string, head: string): string {
  const contract = resultOwnerContract(repository);
  const proofId = Object.entries(contract.rootEntries)
    .map(([path, entry]) => `${path}:${entry.mode}:${entry.type}:${entry.sha}`)
    .join("|");
  return `${repository.toLowerCase()}\0${head}\0${contract.commit}\0${proofId}`;
}

function rememberResultOwnerContractProof(key: string): void {
  resultOwnerContractProofCache.delete(key);
  if (resultOwnerContractProofCache.size >= RESULT_OWNER_CONTRACT_PROOF_CACHE_LIMIT) {
    resultOwnerContractProofCache.delete(resultOwnerContractProofCache.keys().next().value ?? "");
  }
  resultOwnerContractProofCache.set(key, true);
}

function headers(config: GitHubStateConfig): Headers {
  return new Headers({
    accept: "application/vnd.github+json",
    authorization: `Bearer ${config.token}`,
    "user-agent": config.userAgent,
    "x-github-api-version": "2022-11-28",
  });
}

function repoPath(config: GitHubStateConfig, path: string): string {
  return `${API}/repos/${config.repository}${path}`;
}

async function responseError(response: Response): Promise<GitHubStateError> {
  return new GitHubStateError(response.status, (await response.text()).slice(0, 300));
}

async function jsonCall(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  path: string,
  init: RequestInit = {},
): Promise<unknown> {
  const requestHeaders = headers(config);
  new Headers(init.headers).forEach((value, key) => requestHeaders.set(key, value));
  const response = await fetcher(repoPath(config, path), {
    ...init,
    headers: requestHeaders,
    redirect: "manual",
  });
  if (response.status === 404) return null;
  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return true;
  return response.json<unknown>();
}

function object(value: unknown, description: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new GitHubStateError(502, `${description} was not an object`);
  }
  return value as Record<string, unknown>;
}

function safeStatePath(value: string): boolean {
  if (value.length === 0 || value.startsWith("/") || value.includes("//") || value.includes("\\")) {
    return false;
  }
  if (value.split("/").some((segment) => segment === "." || segment === "..")) return false;
  for (const character of value) {
    const point = character.codePointAt(0) ?? 0;
    if (point <= 0x1f || point === 0x7f) return false;
  }
  return true;
}

function requiredSha(value: unknown, description: string): string {
  if (typeof value !== "string" || !SHA.test(value)) {
    throw new GitHubStateError(502, `${description} was not a commit SHA`);
  }
  return value;
}

function nested(value: unknown, keys: readonly string[]): unknown {
  let cursor = value;
  for (const key of keys) cursor = object(cursor, keys.join("."))[key];
  return cursor;
}

async function branchSnapshot(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
): Promise<BranchSnapshot> {
  const ref = await jsonCall(config, fetcher, `/git/ref/heads/${STATE_BRANCH}`);
  const headSha = requiredSha(nested(ref, ["object", "sha"]), "State branch head");
  const commit = await jsonCall(config, fetcher, `/git/commits/${headSha}`);
  const treeSha = requiredSha(nested(commit, ["tree", "sha"]), "State commit tree");
  return { headSha, treeSha };
}

async function assertProtectedBranchAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  expectedCommit: string,
): Promise<void> {
  const branch = object(
    await jsonCall(config, fetcher, `/branches/${STATE_BRANCH}`),
    "protected State branch",
  );
  if (
    branch.name !== STATE_BRANCH ||
    branch.protected !== true ||
    nested(branch, ["commit", "sha"]) !== expectedCommit
  ) {
    throw new GitHubStateError(
      503,
      "protected State branch does not bind the exact qualified commit",
    );
  }
}

async function assertResultOwnerContractAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  commit: string,
  treeSha: string,
): Promise<void> {
  const contract = resultOwnerContract(config.repository);
  if (commit !== contract.commit) {
    const comparison = object(
      await jsonCall(
        config,
        fetcher,
        `/compare/${contract.commit}...${commit}`,
      ),
      "State result-owner contract comparison",
    );
    if (
      comparison.status !== "ahead" ||
      nested(comparison, ["merge_base_commit", "sha"]) !== contract.commit
    ) {
      throw new GitHubStateError(503, "protected State main does not descend from the reviewed result-owner contract");
    }
  }
  const rawTree = object(
    await jsonCall(config, fetcher, `/git/trees/${treeSha}`),
    "State result-owner contract root tree",
  );
  if (rawTree.sha !== treeSha || rawTree.truncated !== false || !Array.isArray(rawTree.tree)) {
    throw new GitHubStateError(503, "protected State main result-owner contract root tree was incomplete");
  }
  for (const [path, expected] of Object.entries(contract.rootEntries)) {
    const matches = rawTree.tree.filter((value) =>
      value !== null && typeof value === "object" && !Array.isArray(value) &&
      (value as Record<string, unknown>).path === path);
    if (matches.length !== 1) {
      throw new GitHubStateError(503, "protected State main result-owner contract root entries changed");
    }
    const entry = object(matches[0], `${path} contract root entry`);
    if (
      entry.mode !== expected.mode ||
      entry.type !== expected.type ||
      entry.sha !== expected.sha
    ) {
      throw new GitHubStateError(503, "protected State main result-owner contract root entries changed");
    }
  }
}

function decodeInlineJson(value: unknown, path: string): unknown {
  const data = object(value, `${path} contents response`);
  if (data.encoding !== "base64" || typeof data.content !== "string") {
    throw new GitHubStateError(502, `${path} did not contain inline base64 JSON`);
  }
  try {
    const binary = atob(data.content.replaceAll("\n", ""));
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes)) as unknown;
  } catch (error) {
    throw new GitHubStateError(502, `${path} contained invalid JSON: ${String(error)}`);
  }
}

async function readPathAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  path: string,
  commit: string,
): Promise<{ found: false } | { found: true; value: unknown }> {
  const query = new URLSearchParams({ ref: commit });
  const value = await jsonCall(
    config,
    fetcher,
    `/contents/${encodeURI(path)}?${query.toString()}`,
  );
  return value === null
    ? { found: false }
    : { found: true, value: decodeInlineJson(value, path) };
}

function canonicalJson(value: unknown): string {
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
    typeof value === "number" ||
    typeof value === "string"
  ) {
    return JSON.stringify(value);
  }
  throw new TypeError("State event is not JSON serializable");
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (value !== null && typeof value === "object") {
    const source = value as Record<string, unknown>;
    return Object.fromEntries(Object.keys(source).sort().map((key) => [key, sortJson(source[key])]));
  }
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "number" ||
    typeof value === "string"
  ) {
    return value;
  }
  throw new TypeError("State document is not JSON serializable");
}

export function canonicalStateDocument(value: unknown): string {
  return `${JSON.stringify(sortJson(value), null, 2)
    .replace(/[\u0080-\uffff]/g, (character) =>
      `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`)}\n`;
}

function sameLogicalLegacyResult(left: LegacyResultBase, right: LegacyResultBase): boolean {
  const identity = (base: LegacyResultBase) => ({
    declared_model: base.declared_model,
    problem_id: base.problem_id,
    statement_revision: base.statement_revision,
    results_repository: base.results_repository,
    results_path: base.results_path,
    canonical_record_sha256: base.canonical_record_sha256,
  });
  return canonicalResultJson(identity(left)) === canonicalResultJson(identity(right));
}

function amendmentMarkerMatches(view: ResultAmendmentView, event: StateEvent): boolean {
  if (event.subject_id !== view.result_id || event.event_id !== view.mutation_event_id) return false;
  const payload = event.payload as Readonly<Record<string, unknown>>;
  if (event.event_id === view.authority_event_id) {
    const pristine =
      view.problem_repair === null &&
      view.applied_problem_repair === null &&
      view.retraction === null &&
      view.leaderboard_eligible &&
      view.effective_problem_id === view.base_problem_id &&
      view.effective_statement_revision === view.base_statement_revision;
    if (!pristine) return false;
    if (event.event_type === "result.recorded") {
      return payload.problem_id === view.base_problem_id &&
        payload.statement_revision === view.base_statement_revision;
    }
    if (event.event_type === "result.claimed") {
      return event.actor.login === view.owner_login &&
        payload.declared_model === view.declared_model &&
        payload.problem_id === view.base_problem_id &&
        payload.statement_revision === view.base_statement_revision;
    }
    return false;
  }
  if (event.event_type === "result.problem_repair_requested") {
    return event.actor.login === view.owner_login &&
      view.problem_repair?.status === "pending" &&
      view.problem_repair.request_event_id === event.event_id &&
      view.problem_repair.requested_at === event.occurred_at &&
      view.problem_repair.revision === payload.repair_revision &&
      view.problem_repair.corrected_problem_id === payload.corrected_problem_id &&
      view.problem_repair.corrected_statement_revision === payload.corrected_statement_revision &&
      view.problem_repair.reason_code === payload.reason_code;
  }
  if (event.event_type === "result.problem_repaired") {
    const evidence = view.problem_repair?.comparator_evidence;
    return view.problem_repair?.decision_event_id === event.event_id &&
      view.problem_repair.request_event_id === event.causation_event_id &&
      view.problem_repair.decided_at === event.occurred_at &&
      view.problem_repair.revision === payload.repair_revision &&
      view.problem_repair.status === "applied" &&
      view.problem_repair.corrected_problem_id === payload.corrected_problem_id &&
      view.problem_repair.corrected_statement_revision === payload.corrected_statement_revision &&
      view.problem_repair.reviewer_login === payload.reviewer_login &&
      evidence !== null && evidence !== undefined &&
      evidence.repository === payload.comparator_repository &&
      evidence.commit === payload.comparator_commit &&
      evidence.path === payload.comparator_path &&
      evidence.blob_oid === payload.comparator_blob_oid &&
      evidence.blob_sha256 === payload.comparator_blob_sha256 &&
      evidence.record_sha256 === payload.comparator_record_sha256 &&
      evidence.binding_sha256 === payload.comparator_binding_sha256 &&
      evidence.verification_method === payload.comparator_verification_method &&
      evidence.evidence_result_id === payload.evidence_result_id &&
      evidence.evidence_owner_login === payload.evidence_owner_login &&
      evidence.evidence_declared_model === payload.evidence_declared_model &&
      evidence.evidence_base_problem_group === payload.evidence_base_problem_group &&
      evidence.evidence_base_problem_id === payload.evidence_base_problem_id &&
      evidence.evidence_base_statement_revision === payload.evidence_base_statement_revision &&
      evidence.evidence_base_challenge_id === payload.evidence_base_challenge_id &&
      evidence.evidence_corrected_problem_group === payload.evidence_corrected_problem_group &&
      evidence.evidence_corrected_problem_id === payload.evidence_corrected_problem_id &&
      evidence.evidence_corrected_statement_revision === payload.evidence_corrected_statement_revision &&
      evidence.evidence_corrected_challenge_id === payload.evidence_corrected_challenge_id;
  }
  if (event.event_type === "result.problem_repair_rejected") {
    return view.problem_repair?.decision_event_id === event.event_id &&
      view.problem_repair.request_event_id === event.causation_event_id &&
      view.problem_repair.decided_at === event.occurred_at &&
      view.problem_repair.revision === payload.repair_revision &&
      view.problem_repair.status === "rejected" &&
      view.problem_repair.reviewer_login === payload.reviewer_login &&
      view.problem_repair.reason_code === payload.reason_code;
  }
  if (event.event_type === "result.retraction_requested") {
    return event.actor.login === view.owner_login &&
      view.retraction?.status === "pending" &&
      view.retraction.request_event_id === event.event_id &&
      view.retraction.requested_at === event.occurred_at &&
      view.retraction.revision === payload.retraction_revision &&
      view.retraction.reason_code === payload.reason_code;
  }
  if (event.event_type === "result.retraction_decided") {
    return view.retraction?.decision_event_id === event.event_id &&
      view.retraction.request_event_id === event.causation_event_id &&
      view.retraction.decided_at === event.occurred_at &&
      view.retraction.revision === payload.retraction_revision &&
      view.retraction.status === (payload.decision === "approve" ? "approved" : "rejected") &&
      view.retraction.reviewer_login === payload.reviewer_login &&
      view.retraction.reason_code === payload.reason_code &&
      !view.retraction.overridden;
  }
  if (event.event_type === "result.retraction_overridden") {
    return view.retraction?.status === "approved" &&
      view.retraction.overridden &&
      view.retraction.decision_event_id === event.event_id &&
      view.retraction.decided_at === event.occurred_at &&
      view.retraction.revision === payload.retraction_revision &&
      view.retraction.reviewer_login === payload.reviewer_login &&
      view.retraction.reason_code === payload.reason_code;
  }
  if (event.event_type === "result.retracted") {
    return view.retraction?.status === "retracted" &&
      view.retraction.retraction_event_id === event.event_id &&
      view.retraction.retracted_at === event.occurred_at &&
      view.retraction.decision_event_id === event.causation_event_id &&
      view.retraction.revision === payload.retraction_revision &&
      view.retraction.reviewer_login === payload.reviewer_login &&
      view.retraction.reason_code === payload.reason_code &&
      view.retraction.release_disposition === payload.release_disposition;
  }
  if (event.event_type === "result.metadata_backfilled") {
    return true;
  }
  return false;
}

function problemRepairRequestMatches(
  ownerLogin: string,
  repair: ProblemRepairState,
  event: StateEvent,
): boolean {
  const payload = event.payload as Readonly<Record<string, unknown>>;
  return event.event_type === "result.problem_repair_requested" &&
    event.event_id === repair.request_event_id &&
    event.actor.login === ownerLogin &&
    event.occurred_at === repair.requested_at &&
    payload.repair_revision === repair.revision &&
    payload.corrected_problem_id === repair.corrected_problem_id &&
    payload.corrected_statement_revision === repair.corrected_statement_revision &&
    (repair.status !== "pending" || payload.reason_code === repair.reason_code);
}

function retractionRequestMatches(
  ownerLogin: string,
  retraction: RetractionState,
  event: StateEvent,
): boolean {
  const payload = event.payload as Readonly<Record<string, unknown>>;
  return retraction.request_event_id !== null &&
    retraction.requested_at !== null &&
    event.event_type === "result.retraction_requested" &&
    event.event_id === retraction.request_event_id &&
    event.actor.login === ownerLogin &&
    event.occurred_at === retraction.requested_at &&
    payload.retraction_revision === retraction.revision &&
    (retraction.status !== "pending" || payload.reason_code === retraction.reason_code);
}

async function assertAmendmentHistoryAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  view: ResultAmendmentView,
  authority: StateEvent,
  mutation: StateEvent,
  commit: string,
): Promise<void> {
  const path = resultAmendmentPath(view.result_id);
  const referencedIds = new Set<string>([view.authority_event_id, view.mutation_event_id]);
  for (const repair of [view.problem_repair, view.applied_problem_repair]) {
    if (repair === null) continue;
    referencedIds.add(repair.request_event_id);
    if (repair.decision_event_id !== null) referencedIds.add(repair.decision_event_id);
  }
  if (view.retraction?.request_event_id !== null && view.retraction?.request_event_id !== undefined) {
    referencedIds.add(view.retraction.request_event_id);
  }
  if (view.retraction?.decision_event_id !== null && view.retraction?.decision_event_id !== undefined) {
    referencedIds.add(view.retraction.decision_event_id);
  }
  if (view.retraction?.retraction_event_id !== null && view.retraction?.retraction_event_id !== undefined) {
    referencedIds.add(view.retraction.retraction_event_id);
  }
  const events = new Map<string, StateEvent>([
    [authority.event_id, authority],
    [mutation.event_id, mutation],
  ]);
  await Promise.all([...referencedIds].filter((eventId) => !events.has(eventId)).map(async (eventId) => {
    events.set(eventId, await readEventAt(config, fetcher, eventId, commit));
  }));
  const referencedEvent = (eventId: string): StateEvent => {
    const event = events.get(eventId);
    if (event?.subject_id !== view.result_id) {
      throw new StateEventConflictError(path);
    }
    return event;
  };
  try {
    for (const repair of [view.problem_repair, view.applied_problem_repair]) {
      if (repair === null) continue;
      if (!problemRepairRequestMatches(
        view.owner_login,
        repair,
        referencedEvent(repair.request_event_id),
      )) {
        throw new TypeError("problem repair request disagreed with targeted view");
      }
      if (repair.decision_event_id !== null) {
        const decision = referencedEvent(repair.decision_event_id);
        const decisionView = { ...view, mutation_event_id: decision.event_id, problem_repair: repair };
        const expectedDecisionType = repair.status === "applied"
          ? "result.problem_repaired"
          : "result.problem_repair_rejected";
        if (repair.status === "pending" || decision.event_type !== expectedDecisionType ||
          !amendmentMarkerMatches(decisionView, decision)) {
          throw new TypeError("problem repair decision disagreed with targeted view");
        }
      }
    }
    const retraction = view.retraction;
    if (retraction !== null) {
      if (retraction.request_event_id !== null && !retractionRequestMatches(
        view.owner_login,
        retraction,
        referencedEvent(retraction.request_event_id),
      )) {
        throw new TypeError("retraction request disagreed with targeted view");
      }
      if (retraction.decision_event_id !== null) {
        const decision = referencedEvent(retraction.decision_event_id);
        const expectedDecisionType = retraction.overridden
          ? "result.retraction_overridden"
          : "result.retraction_decided";
        const payload = decision.payload as Readonly<Record<string, unknown>>;
        const decisionRetraction: RetractionState = {
          ...retraction,
          status: retraction.overridden
            ? "approved"
            : payload.decision === "approve" ? "approved" : "rejected",
          retraction_event_id: null,
          retracted_at: null,
          release_disposition: null,
        };
        const decisionView = {
          ...view,
          mutation_event_id: decision.event_id,
          retraction: decisionRetraction,
        };
        if (decision.event_type !== expectedDecisionType ||
          !amendmentMarkerMatches(decisionView, decision)) {
          throw new TypeError("retraction decision disagreed with targeted view");
        }
        if (decision.event_type === "result.retraction_overridden") {
          const prior = await readEventAt(config, fetcher, decision.causation_event_id, commit);
          const priorPayload = prior.payload as Readonly<Record<string, unknown>>;
          const priorMatches = (
            prior.event_id === view.authority_event_id &&
            (prior.event_type === "result.claimed" || prior.event_type === "result.recorded") &&
            retraction.revision === 1
          ) || (
            prior.event_type === "result.metadata_backfilled" &&
            retraction.revision === 1
          ) || (
            prior.event_type === "result.problem_repaired" &&
            view.problem_repair?.status === "applied" &&
            view.problem_repair.decision_event_id === prior.event_id
          ) || (
            prior.event_type === "result.problem_repair_rejected" &&
            view.problem_repair?.status === "rejected" &&
            view.problem_repair.decision_event_id === prior.event_id
          ) || (
            prior.event_type === "result.retraction_decided" &&
            priorPayload.decision === "reject" &&
            priorPayload.retraction_revision === retraction.revision - 1
          );
          if (prior.subject_id !== view.result_id || !priorMatches) {
            throw new TypeError("retraction override cause disagreed with targeted view");
          }
        }
      }
      if (retraction.retraction_event_id !== null) {
        const terminal = referencedEvent(retraction.retraction_event_id);
        const terminalView = { ...view, mutation_event_id: terminal.event_id, retraction };
        if (terminal.event_type !== "result.retracted" ||
          !amendmentMarkerMatches(terminalView, terminal)) {
          throw new TypeError("terminal retraction disagreed with targeted view");
        }
      }
    }
  } catch (error) {
    if (error instanceof StateEventConflictError || error instanceof GitHubStateError) throw error;
    throw new StateEventConflictError(path);
  }
}

async function createCommit(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  snapshot: BranchSnapshot,
  events: readonly (WritableStateEvent | ResultAmendmentSystemEvent)[],
  writes: readonly TreeWrite[] = [],
  message?: string,
): Promise<string> {
  let treeSha = snapshot.treeSha;
  if (events.length > 0 || writes.length > 0) {
    const tree = await jsonCall(config, fetcher, "/git/trees", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        base_tree: snapshot.treeSha,
        tree: [
          ...events.map((event) => ({
            path: stateEventPath(event),
            mode: "100644",
            type: "blob",
            content: canonicalStateDocument(event),
          })),
          ...writes.map((write) => write.value === null
            ? { path: write.path, mode: "100644", type: "blob", sha: null }
            : { path: write.path, mode: "100644", type: "blob", content: canonicalStateDocument(write.value) }),
        ],
      }),
    });
    treeSha = requiredSha(object(tree, "created State tree").sha, "created State tree");
  }
  const commit = await jsonCall(config, fetcher, "/git/commits", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      message: message ?? (events.length === 1
        ? `Record ${events[0]?.event_type ?? "State event"} ${events[0]?.event_id ?? ""}`
        : `Record atomic State event batch for ${events[0]?.subject_id ?? "unknown subject"}`),
      parents: [snapshot.headSha],
      tree: treeSha,
    }),
  });
  return requiredSha(object(commit, "created State commit").sha, "created State commit");
}

async function isCommitReachable(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  commit: string,
): Promise<boolean> {
  const ref = await jsonCall(config, fetcher, `/git/ref/heads/${STATE_BRANCH}`);
  const head = requiredSha(nested(ref, ["object", "sha"]), "State branch head");
  if (head === commit) return true;
  const comparison = await jsonCall(config, fetcher, `/compare/${commit}...${head}`);
  const data = object(comparison, "State comparison");
  return (
    (data.status === "ahead" || data.status === "identical") &&
    nested(data, ["merge_base_commit", "sha"]) === commit
  );
}

async function updateReference(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  commit: string,
): Promise<"applied" | "collision"> {
  const requestHeaders = headers(config);
  requestHeaders.set("content-type", "application/json");
  const request: RequestInit = {
    method: "PATCH",
    headers: requestHeaders,
    body: JSON.stringify({ sha: commit, force: false }),
    redirect: "manual",
  };
  let response: Response | null = null;
  let uncertain = false;
  try {
    response = await fetcher(repoPath(config, `/git/refs/heads/${STATE_BRANCH}`), request);
  } catch {
    uncertain = true;
  }
  if (response !== null && response.status >= 500) uncertain = true;
  if (uncertain) {
    try {
      response = await fetcher(repoPath(config, `/git/refs/heads/${STATE_BRANCH}`), request);
    } catch {
      response = null;
    }
  }
  if (response?.ok) return "applied";
  if (uncertain) {
    try {
      if (await isCommitReachable(config, fetcher, commit)) return "applied";
    } catch {
      throw new StateUpdateOutcomeUnknownError();
    }
  }
  if (response?.status === 409) return "collision";
  if (response?.status === 422) {
    const detail = (await response.text()).slice(0, 300);
    if (/not a fast.?forward/i.test(detail)) return "collision";
    throw new GitHubStateError(response.status, detail || "State reference update rejected");
  }
  if (response === null) throw new StateUpdateOutcomeUnknownError();
  throw await responseError(response);
}

function pause(attempt: number): Promise<void> {
  const spread = crypto.getRandomValues(new Uint8Array(1))[0] ?? 0;
  const delay = Math.round(Math.min(2 ** attempt * 80, 800) * (0.5 + spread / 255));
  return new Promise((resolve) => setTimeout(resolve, delay));
}

async function readEventAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  eventId: string,
  commit: string,
): Promise<StateEvent> {
  const path = `events/${eventId.replaceAll("-", "").slice(0, 2)}/${eventId}.json`;
  const entry = await readPathAt(config, fetcher, path, commit);
  if (!entry.found) throw new GitHubStateError(502, `${path} referenced by submission view is missing`);
  try {
    validateStateEvent(entry.value);
  } catch (error) {
    throw new GitHubStateError(502, `${path} referenced by submission view is invalid: ${String(error)}`);
  }
  if (stateEventPath(entry.value) !== path) throw new GitHubStateError(502, `${path} event identity disagrees with its path`);
  return entry.value;
}

async function assertReleaseStatusMarkerAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  status: ResultReleaseStatusView,
  commit: string,
): Promise<void> {
  if (status.status === "not_scheduled") return;
  const eventId = status.release_event_id;
  if (eventId === null) throw new StateEventConflictError(resultReleaseStatusPath(status.result_id));
  const releaseStatusPath = resultReleaseStatusPath(status.result_id);
  const validateRemovedPayload = (event: Record<string, unknown>): void => {
    const payload = object(event.payload, "release removal payload");
    const expectedFields = [
      "bundle_disposition", "bundle_path", "bundle_sha256", "classification",
      "evidence_blob", "evidence_commit", "evidence_path", "evidence_repository",
      "evidence_sha256", "incident_id", "published_release_tree_sha256",
      "published_repository_commit", "published_repository_tree",
      "published_state_event_blob", "published_state_event_commit",
      "published_state_event_path", "published_state_event_repository",
      "published_state_event_sha256", "release_path", "removal_repository_commit",
      "removal_repository_tree", "shared_release_paths",
    ].sort();
    const actualFields = Object.keys(payload).sort();
    const causation = String(event.causation_event_id);
    const releasePath = `releases/[0-9]{4}/(?:0[1-9]|1[0-2])/${status.result_id}`;
    const commits = [
      "evidence_blob", "evidence_commit", "published_repository_commit",
      "published_repository_tree", "published_state_event_blob",
      "published_state_event_commit", "removal_repository_commit",
      "removal_repository_tree",
    ];
    const digests = [
      "bundle_sha256", "evidence_sha256", "published_release_tree_sha256",
      "published_state_event_sha256",
    ];
    const shared = payload.shared_release_paths;
    if (
      actualFields.length !== expectedFields.length ||
      actualFields.some((field, index) => field !== expectedFields[index]) ||
      !UUID_V7.test(causation) ||
      typeof payload.incident_id !== "string" || !UUID_V7.test(payload.incident_id) ||
      !new Set(["confidentiality_incident", "erroneous_publication", "owner_retraction"])
        .has(payload.classification as string) ||
      payload.published_state_event_repository !== PRODUCTION_STATE_REPOSITORY ||
      payload.published_state_event_path !==
        `events/${causation.replaceAll("-", "").slice(0, 2)}/${causation}.json` ||
      typeof payload.release_path !== "string" ||
      !(new RegExp(`^${releasePath}$`, "u")).test(payload.release_path) ||
      typeof payload.bundle_path !== "string" ||
      !/^sources\/[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.tar\.gz$/u
        .test(payload.bundle_path) ||
      !new Set(["delete", "retain_shared"]).has(payload.bundle_disposition as string) ||
      !Array.isArray(shared) || shared.length > 128 ||
      new Set(shared).size !== shared.length ||
      shared.some((path) => typeof path !== "string" ||
        !/^releases\/[0-9]{4}\/(?:0[1-9]|1[0-2])\/r2_[0-9a-f]{64}$/u.test(path) ||
        path === payload.release_path) ||
      typeof payload.evidence_repository !== "string" ||
      !new Set(["leanprover/lean-eval-audit", PRODUCTION_STATE_REPOSITORY])
        .has(payload.evidence_repository) ||
      typeof payload.evidence_path !== "string" || !safeStatePath(payload.evidence_path) ||
      commits.some((field) => typeof payload[field] !== "string" || !LOWER_SHA.test(payload[field])) ||
      digests.some((field) => typeof payload[field] !== "string" || !DIGEST.test(payload[field])) ||
      (payload.bundle_disposition === "delete" ? shared.length !== 0 : shared.length === 0) ||
      (payload.classification === "confidentiality_incident" &&
        (payload.bundle_disposition !== "delete" || shared.length !== 0))
    ) {
      throw new TypeError("release removal payload disagreed with pinned State contract");
    }
  };
  const releaseEvent = async (
    identifier: string,
    allowUnregisteredRemoved: boolean,
  ): Promise<Record<string, unknown>> => {
    const path = `events/${identifier.replaceAll("-", "").slice(0, 2)}/${identifier}.json`;
    const entry = await readPathAt(config, fetcher, path, commit);
    if (!entry.found) throw new StateEventConflictError(releaseStatusPath);
    const event = object(entry.value, "result release-status marker");
    const eventType = typeof event.event_type === "string" ? event.event_type : "";
    // release.removed is part of the pinned private State contract but is
    // deliberately not registered as a Worker-writable event. All other
    // release markers can therefore use the Worker's complete event decoder.
    if (!(allowUnregisteredRemoved && eventType === "release.removed")) {
      validateStateEvent(entry.value);
    }
    const actor = event.actor === null || typeof event.actor !== "object" || Array.isArray(event.actor)
      ? null
      : event.actor as Record<string, unknown>;
    if (
      Object.keys(event).sort().join(",") !==
        "actor,causation_event_id,event_id,event_type,occurred_at,payload,schema_version,subject_id" ||
      event.schema_version !== 1 ||
      event.event_id !== identifier ||
      !new Set([
        "release.scheduled",
        "release.started",
        "release.published",
        "release.failed",
        "release.cancelled",
        "release.removed",
      ]).has(eventType) ||
      event.subject_id !== status.result_id ||
      typeof event.occurred_at !== "string" ||
      Number.isNaN(Date.parse(event.occurred_at)) ||
      new Date(event.occurred_at).toISOString() !== event.occurred_at ||
      typeof event.causation_event_id !== "string" ||
      actor?.kind !== "system" ||
      Object.keys(actor).length !== 1 ||
      event.payload === null ||
      typeof event.payload !== "object" ||
      Array.isArray(event.payload)
    ) {
      throw new TypeError("release marker disagreed with targeted status");
    }
    if (allowUnregisteredRemoved) validateRemovedPayload(event);
    return event;
  };
  const expectedType = {
    scheduled: "release.scheduled",
    running: "release.started",
    published: "release.published",
    failed: "release.failed",
    cancelled: "release.cancelled",
    removed: "release.removed",
  }[status.status];
  try {
    const current = await releaseEvent(eventId, expectedType === "release.removed");
    if (current.event_type !== expectedType) {
      throw new TypeError("release marker disagreed with targeted status");
    }
    const predecessorId = status.supersedes_release_event_id;
    if (expectedType === "release.removed" &&
      (status.release_revision <= 1 || predecessorId === null)) {
      throw new TypeError("release removal omitted its published predecessor");
    }
    if (status.release_revision > 1) {
      if (predecessorId === null) throw new TypeError("release predecessor was absent");
      const predecessor = await releaseEvent(predecessorId, false);
      if (expectedType === "release.removed") {
        const currentPayload = object(current.payload, "release removal payload");
        const predecessorPayload = object(predecessor.payload, "published release payload");
        const startedId = predecessor.causation_event_id;
        if (
          current.causation_event_id !== predecessorId ||
          predecessor.event_type !== "release.published" ||
          predecessor.subject_id !== status.result_id ||
          typeof startedId !== "string" || !UUID_V7.test(startedId) ||
          currentPayload.published_repository_commit !== predecessorPayload.repository_commit ||
          currentPayload.published_release_tree_sha256 !== predecessorPayload.tree_digest ||
          currentPayload.release_path !== predecessorPayload.path
        ) {
          throw new TypeError("release removal did not bind its published predecessor");
        }
        const started = await releaseEvent(startedId, false);
        const startedPayload = object(started.payload, "started release payload");
        if (
          started.event_type !== "release.started" ||
          started.subject_id !== status.result_id ||
          predecessorPayload.attempt !== startedPayload.attempt
        ) {
          throw new TypeError("published release did not bind its started predecessor");
        }
      }
      if (
        `${String(predecessor.occurred_at)}\0${predecessorId}` >=
          `${String(current.occurred_at)}\0${eventId}`
      ) {
        throw new TypeError("release predecessor did not precede the current marker");
      }
    }
  } catch {
    throw new StateEventConflictError(releaseStatusPath);
  }
}

async function assertEffectiveResultReservationAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  reservation: ReturnType<typeof decodeEffectiveResultIdentityReservation>,
  view: ResultAmendmentView,
  commit: string,
): Promise<void> {
  const path = effectiveResultIdentityPath(reservation.effective_result_identity_id);
  if (
    reservation.result_id !== view.result_id ||
    reservation.owner_login !== view.owner_login ||
    reservation.declared_model !== view.declared_model
  ) {
    throw new StateEventConflictError(path);
  }
  if (reservation.reservation_kind === "result_authority") {
    if (
      reservation.reservation_event_id !== view.authority_event_id ||
      reservation.problem_id !== view.base_problem_id ||
      reservation.statement_revision !== view.base_statement_revision
    ) {
      throw new StateEventConflictError(path);
    }
    return;
  }
  const event = await readEventAt(
    config,
    fetcher,
    reservation.reservation_event_id,
    commit,
  );
  const payload = event.payload as Readonly<Record<string, unknown>>;
  if (
    event.event_type !== "result.problem_repaired" ||
    event.subject_id !== view.result_id ||
    payload.corrected_problem_id !== reservation.problem_id ||
    payload.corrected_statement_revision !== reservation.statement_revision
  ) {
    throw new StateEventConflictError(path);
  }
}

function uuidV7Timestamp(eventId: string): number {
  return Number.parseInt(`${eventId.slice(0, 8)}${eventId.slice(9, 13)}`, 16);
}

async function readSubmissionAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  submissionId: string,
  commit: string,
): Promise<SubmissionView | null> {
  const path = submissionViewPath(submissionId);
  const entry = await readPathAt(config, fetcher, path, commit);
  if (!entry.found) return null;
  let view: SubmissionView;
  try {
    view = decodeSubmissionView(entry.value);
  } catch (error) {
    throw new GitHubStateError(502, `${path} is invalid: ${String(error)}`);
  }
  if (view.submission_id !== submissionId) throw new GitHubStateError(502, `${path} has the wrong submission identity`);
  const archiveEventId = view.schema_version === 2 && view.archive.status !== "pending"
    ? view.archive.event_id
    : null;
  const evaluationEventId = view.schema_version === 2 && view.evaluation.status !== "pending"
    ? view.evaluation.event_id
    : null;
  const eventIds = new Set([
    view.received_event_id,
    view.metadata_event_id,
    ...(view.publication_event_id === null ? [] : [view.publication_event_id]),
    ...(archiveEventId === null ? [] : [archiveEventId]),
    ...(evaluationEventId === null ? [] : [evaluationEventId]),
    ...(view.schema_version === 2 && view.result_event_id !== null ? [view.result_event_id] : []),
  ]);
  const events = await Promise.all([...eventIds].map((eventId) => readEventAt(config, fetcher, eventId, commit)));
  if (view.schema_version === 2 && evaluationEventId !== null && !new Set(["pending", "running"]).has(view.evaluation.status)) {
    const terminal = events.find((event) => event.event_id === evaluationEventId);
    const terminalCause = terminal?.causation_event_id;
    if (terminalCause === null || terminalCause === undefined) {
      throw new GitHubStateError(502, `${path} evaluation terminal event has no cause`);
    }
    if (!eventIds.has(terminalCause)) {
      eventIds.add(terminalCause);
      events.push(await readEventAt(config, fetcher, terminalCause, commit));
    }
  }
  const received = events.find((event) => event.event_id === view.received_event_id);
  if (
    received?.event_type !== "submission.received" ||
    received.subject_id !== submissionId ||
    received.actor.login !== view.owner_login
  ) {
    throw new GitHubStateError(502, `${path} does not match its submission.received event`);
  }
  const expectedReceived = {
    problem_id: view.submission.problem_id,
    statement_revision: view.submission.statement_revision,
    declared_model: view.submission.declared_model,
    source_repository: view.submission.source_repository,
    source_commit: view.submission.source_commit,
    source_visibility: view.submission.source_visibility,
    publication_choice: received.payload.publication_choice,
  };
  if (canonicalJson(received.payload) !== canonicalJson(expectedReceived)) {
    throw new GitHubStateError(502, `${path} submission input does not match its received event`);
  }
  const metadata = events.find((event) => event.event_id === view.metadata_event_id);
  if (
    metadata?.event_type !== "submission.metadata_amended" ||
    metadata.subject_id !== submissionId ||
    metadata.actor.login !== view.owner_login ||
    canonicalJson(metadata.payload.production_metadata) !== canonicalJson(view.production_metadata)
  ) {
    throw new GitHubStateError(502, `${path} does not match its metadata event`);
  }
  if (view.publication_event_id === null) {
    if (view.publication_choice !== received.payload.publication_choice) {
      throw new GitHubStateError(502, `${path} publication choice does not match intake`);
    }
  } else {
    const publication = events.find((event) => event.event_id === view.publication_event_id);
    if (
      publication?.event_type !== "submission.publication_changed" ||
      publication.subject_id !== submissionId ||
      publication.actor.login !== view.owner_login ||
      publication.payload.publication_choice !== view.publication_choice
    ) {
      throw new GitHubStateError(502, `${path} does not match its publication event`);
    }
  }
  if (!eventIds.has(view.mutation_event_id)) {
    throw new GitHubStateError(502, `${path} mutation head is not a referenced owner mutation`);
  }
  if (view.schema_version === 2) {
    if (view.archive.status !== "pending") {
      const archive = events.find((event) => event.event_id === archiveEventId);
      const expectedArchive = archive === undefined ? null : {
        status: archive.event_type.split(".", 2)[1],
        event_id: archive.event_id,
        occurred_at: archive.occurred_at,
        ...archive.payload,
      };
      if (
        archive?.subject_id !== submissionId ||
        !archive.event_type.startsWith("archive.") ||
        canonicalJson(expectedArchive) !== canonicalJson(view.archive)
      ) {
        throw new GitHubStateError(502, `${path} archive summary does not match its lifecycle event`);
      }
    }
    if (view.evaluation.status !== "pending") {
      const current = events.find((event) => event.event_id === evaluationEventId);
      const started = current?.event_type === "evaluation.started"
        ? current
        : events.find((event) => event.event_id === current?.causation_event_id);
      const expectedEvaluation = current === undefined || started?.event_type !== "evaluation.started"
        ? null
        : {
            status: current.event_type === "evaluation.started" ? "running" : current.event_type.split(".", 2)[1],
            event_id: current.event_id,
            occurred_at: current.occurred_at,
            ...started.payload,
            ...(current.event_type === "evaluation.started" ? {} : current.payload),
          };
      if (
        current?.subject_id !== submissionId ||
        started?.subject_id !== submissionId ||
        !current.event_type.startsWith("evaluation.") ||
        canonicalJson(expectedEvaluation) !== canonicalJson(view.evaluation)
      ) {
        throw new GitHubStateError(502, `${path} evaluation summary does not match its lifecycle events`);
      }
    }
    if (view.result_event_id !== null) {
      const result = events.find((event) => event.event_id === view.result_event_id);
      if (
        result?.event_type !== "result.recorded" ||
        result.subject_id !== view.result_id ||
        result.payload.submission_id !== submissionId
      ) {
        throw new GitHubStateError(502, `${path} result identity does not match its lifecycle event`);
      }
    }
  }
  return view;
}

async function readResultOwnerDocumentAt<T>(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  path: string,
  commit: string,
  decode: (value: unknown) => T,
): Promise<T | null> {
  const entry = await readPathAt(config, fetcher, path, commit);
  if (!entry.found) return null;
  try {
    return decode(entry.value);
  } catch (error) {
    throw new GitHubStateError(502, `${path} is invalid: ${String(error)}`);
  }
}

export class GitHubStateRepository {
  readonly #config: GitHubStateConfig;
  readonly #fetcher: GitHubFetch;

  constructor(config: GitHubStateConfig, fetcher: GitHubFetch = defaultGitHubFetch) {
    if (!/^[A-Za-z\d_.-]+\/[A-Za-z\d_.-]+$/.test(config.repository)) {
      throw new TypeError("State repository must be an owner/name pair");
    }
    this.#config = config;
    this.#fetcher = fetcher;
  }

  async #authorizedSnapshot(): Promise<BranchSnapshot> {
    const repository = object(
      await jsonCall(this.#config, this.#fetcher, ""),
      "State repository",
    );
    const permissions = object(repository.permissions, "State repository permissions");
    if (permissions.push !== true) {
      throw new GitHubStateError(403, "State credential does not have push permission");
    }
    return branchSnapshot(this.#config, this.#fetcher);
  }

  async #resultOwnerSnapshot(): Promise<BranchSnapshot> {
    const snapshot = await branchSnapshot(this.#config, this.#fetcher);
    const proofKey = resultOwnerContractProofCacheKey(
      this.#config.repository,
      snapshot.headSha,
    );
    if (!resultOwnerContractProofCache.has(proofKey)) {
      await assertResultOwnerContractAt(
        this.#config,
        this.#fetcher,
        snapshot.headSha,
        snapshot.treeSha,
      );
      rememberResultOwnerContractProof(proofKey);
    } else {
      rememberResultOwnerContractProof(proofKey);
    }
    return snapshot;
  }

  async assertResultOwnerContract(): Promise<string> {
    return (await this.#resultOwnerSnapshot()).headSha;
  }

  async readResultAmendmentForMaintainer(resultId: string): Promise<ResultAmendmentView> {
    const snapshot = await this.#resultOwnerSnapshot();
    return (await this.#resultAmendmentAt(resultId, snapshot)).view;
  }

  async assertAvailable(): Promise<void> {
    await this.#authorizedSnapshot();
  }

  async assertWritable(): Promise<string> {
    const snapshot = await this.#authorizedSnapshot();
    if ((await updateReference(this.#config, this.#fetcher, snapshot.headSha)) !== "applied") {
      throw new GitHubStateError(409, "State branch rejected a same-commit write probe");
    }
    return snapshot.headSha;
  }

  async assertProductionQualifiedWritable(): Promise<string> {
    if (this.#config.repository.toLowerCase() !== PRODUCTION_STATE_REPOSITORY) {
      throw new GitHubStateError(503, "production State qualification targeted the wrong repository");
    }
    const snapshot = await this.#authorizedSnapshot();
    await Promise.all([
      assertProtectedBranchAt(this.#config, this.#fetcher, snapshot.headSha),
      assertResultOwnerContractAt(
        this.#config,
        this.#fetcher,
        snapshot.headSha,
        snapshot.treeSha,
      ),
    ]);
    if ((await updateReference(this.#config, this.#fetcher, snapshot.headSha)) !== "applied") {
      throw new GitHubStateError(409, "State branch rejected a same-commit write probe");
    }
    await assertProtectedBranchAt(this.#config, this.#fetcher, snapshot.headSha);
    return snapshot.headSha;
  }

  async provePromotionCanaryContention(event: WritableStateEvent): Promise<{
    proofRecorded: boolean;
    idempotent: boolean;
    commit: string;
    created: boolean;
  }> {
    validateStateEvent(event);
    if (event.event_type !== "authentication.nonce_consumed") {
      throw new TypeError("promotion canary evidence must be an authentication.nonce_consumed event");
    }
    const path = stateEventPath(event);
    const snapshot = await branchSnapshot(this.#config, this.#fetcher);
    const existing = await readPathAt(this.#config, this.#fetcher, path, snapshot.headSha);
    if (existing.found) {
      try {
        validateStateEvent(existing.value);
      } catch {
        throw new StateEventConflictError(path);
      }
      if (canonicalJson(existing.value) !== canonicalJson(event)) {
        throw new StateEventConflictError(path);
      }
      return {
        proofRecorded: true,
        idempotent: true,
        commit: snapshot.headSha,
        created: false,
      };
    }

    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const current = attempt === 0
        ? snapshot
        : await branchSnapshot(this.#config, this.#fetcher);
      const winner = await createCommit(
        this.#config,
        this.#fetcher,
        current,
        [],
        [],
        `Promotion canary CAS winner ${event.event_id}`,
      );
      const contender = await createCommit(
        this.#config,
        this.#fetcher,
        current,
        [event],
        [],
        `Promotion canary CAS contender ${event.event_id}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, winner)) !== "applied") {
        if (attempt === MAX_WRITE_ATTEMPTS) {
          throw new GitHubStateError(409, "State branch kept changing before the canary collision");
        }
        await pause(attempt);
        continue;
      }
      if ((await updateReference(this.#config, this.#fetcher, contender)) !== "collision") {
        throw new GitHubStateError(502, "promotion canary competing commit did not collide");
      }
      const outcome = await this.appendEvent(event);
      return {
        proofRecorded: true,
        idempotent: !outcome.created,
        commit: outcome.commit,
        created: outcome.created,
      };
    }
    throw new Error("unreachable promotion canary CAS attempt");
  }

  async readSubmission(submissionId: string): Promise<SubmissionView | null> {
    const snapshot = await branchSnapshot(this.#config, this.#fetcher);
    return readSubmissionAt(this.#config, this.#fetcher, submissionId, snapshot.headSha);
  }

  async acceptSubmission(
    events: readonly WritableStateEvent[],
    view: SubmissionView,
    outbox: DispatchOutbox,
  ): Promise<{ commit: string; created: boolean; view: SubmissionView }> {
    if (events.length !== 3) throw new TypeError("submission acceptance requires exactly three State events");
    for (const event of events) validateStateEvent(event);
    const decodedView = decodeSubmissionView(view);
    const decodedOutbox = decodeDispatchOutbox(outbox);
    if (decodedView.submission_id !== decodedOutbox.submission_id || decodedView.submission_id !== events[1]?.subject_id) {
      throw new TypeError("submission acceptance identities disagree");
    }
    const paths = events.map(stateEventPath);
    const viewPath = submissionViewPath(view.submission_id);
    const outboxPath = dispatchOutboxPath(view.submission_id);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const current = await readSubmissionAt(this.#config, this.#fetcher, view.submission_id, snapshot.headSha);
      if (current !== null) return { commit: snapshot.headSha, created: false, view: current };
      const existing = await Promise.all(
        [...paths, outboxPath].map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)),
      );
      if (existing.some((entry) => entry.found)) {
        throw new StateEventConflictError(paths[existing.findIndex((entry) => entry.found)] ?? viewPath);
      }
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        events,
        [
          { path: viewPath, value: decodedView },
          { path: outboxPath, value: decodedOutbox },
        ],
        `Accept submission ${view.submission_id} and enqueue dispatch`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, created: true, view: decodedView };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) throw new GitHubStateError(409, "State branch kept changing during submission acceptance");
      await pause(attempt);
    }
    throw new Error("unreachable submission acceptance attempt");
  }

  async appendSubmissionMutation(
    event: WritableStateEvent,
    expectedMutationEventId: string,
    nextView: SubmissionView,
  ): Promise<{ commit: string; created: boolean; view: SubmissionView }> {
    validateStateEvent(event);
    if (event.event_type !== "submission.metadata_amended" && event.event_type !== "submission.publication_changed") {
      throw new TypeError("only owner submission mutations may update a submission view");
    }
    const decodedView = decodeSubmissionView(nextView);
    const path = stateEventPath(event);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const current = await readSubmissionAt(this.#config, this.#fetcher, event.subject_id, snapshot.headSha);
      if (current === null) throw new GitHubStateError(404, "submission view does not exist");
      if (current.mutation_event_id === event.event_id) {
        const existing = await readPathAt(this.#config, this.#fetcher, path, snapshot.headSha);
        if (!existing.found || canonicalJson(existing.value) !== canonicalJson(event) || canonicalJson(current) !== canonicalJson(decodedView)) {
          throw new StateEventConflictError(path);
        }
        return { commit: snapshot.headSha, created: false, view: current };
      }
      if (current.mutation_event_id !== expectedMutationEventId) throw new StateEventConflictError(path);
      if (event.event_id <= current.mutation_event_id) throw new StateEventConflictError(path);
      const existing = await readPathAt(this.#config, this.#fetcher, path, snapshot.headSha);
      if (existing.found) throw new StateEventConflictError(path);
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [event],
        [{ path: submissionViewPath(event.subject_id), value: decodedView }],
        `Record owner mutation ${event.event_id} and refresh submission view`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, created: true, view: decodedView };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) throw new GitHubStateError(409, "State branch kept changing during owner mutation");
      await pause(attempt);
    }
    throw new Error("unreachable owner mutation attempt");
  }

  async updateDispatch(
    nextView: SubmissionView,
    expectedAttempts: number,
    nextOutbox: DispatchOutbox | null,
  ): Promise<{ commit: string; view: SubmissionView }> {
    const decodedView = decodeSubmissionView(nextView);
    const decodedOutbox = nextOutbox === null ? null : decodeDispatchOutbox(nextOutbox);
    if (decodedOutbox !== null && decodedOutbox.submission_id !== decodedView.submission_id) {
      throw new TypeError("dispatch view and outbox identities disagree");
    }
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const current = await readSubmissionAt(this.#config, this.#fetcher, nextView.submission_id, snapshot.headSha);
      if (current === null) throw new GitHubStateError(404, "submission view does not exist");
      if (current.dispatch.status === "succeeded") return { commit: snapshot.headSha, view: current };
      if (current.dispatch.attempts !== expectedAttempts) throw new StateEventConflictError(submissionViewPath(nextView.submission_id));
      const outboxPath = dispatchOutboxPath(nextView.submission_id);
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [],
        [
          { path: submissionViewPath(nextView.submission_id), value: decodedView },
          { path: outboxPath, value: decodedOutbox },
        ],
        `Record dispatch ${decodedView.dispatch.status} for ${nextView.submission_id}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, view: decodedView };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) throw new GitHubStateError(409, "State branch kept changing during dispatch update");
      await pause(attempt);
    }
    throw new Error("unreachable dispatch update attempt");
  }

  async listDispatchOutbox(shard: string): Promise<readonly DispatchOutbox[]> {
    if (!/^[0-9a-f]{2}$/.test(shard)) throw new TypeError("dispatch outbox shard must be two lowercase hexadecimal digits");
    const snapshot = await branchSnapshot(this.#config, this.#fetcher);
    const directory = `views/dispatch-outbox/${shard}`;
    const query = new URLSearchParams({ ref: snapshot.headSha });
    const listing = await jsonCall(this.#config, this.#fetcher, `/contents/${directory}?${query.toString()}`);
    if (listing === null) return [];
    if (!Array.isArray(listing) || listing.length > 1000) {
      throw new GitHubStateError(503, "dispatch outbox shard is invalid or exceeds its hard bound");
    }
    const paths = listing.map((raw) => {
      const entry = object(raw, "dispatch outbox directory entry");
      if (entry.type !== "file" || typeof entry.path !== "string" || !new RegExp(`^${directory}/[0-9a-f-]{36}\\.json$`).test(entry.path)) {
        throw new GitHubStateError(502, "dispatch outbox directory contains an unexpected entry");
      }
      return entry.path;
    });
    return Promise.all(paths.map(async (path) => {
      const entry = await readPathAt(this.#config, this.#fetcher, path, snapshot.headSha);
      if (!entry.found) throw new GitHubStateError(502, `${path} disappeared from the pinned State snapshot`);
      let outbox: DispatchOutbox;
      try {
        outbox = decodeDispatchOutbox(entry.value);
      } catch (error) {
        throw new GitHubStateError(502, `${path} is invalid: ${String(error)}`);
      }
      if (dispatchOutboxPath(outbox.submission_id) !== path) throw new GitHubStateError(502, `${path} has the wrong submission identity`);
      return outbox;
    }));
  }

  async appendSubmissionLifecycle(
    events: readonly WritableSubmissionLifecycleEvent[],
    expectedLifecycleEventId: string,
    nextView: SubmissionView,
  ): Promise<{ commit: string; created: boolean; view: SubmissionView }> {
    if (events.length === 0 || events.length > 4) {
      throw new TypeError("submission lifecycle batch must contain between one and four events");
    }
    for (const event of events) validateStateEvent(event);
    const decodedView = decodeSubmissionView(nextView);
    if (decodedView.schema_version !== 2) {
      throw new TypeError("submission lifecycle requires submission-view schema version 2");
    }
    if (events.some((event) => event.subject_id !== decodedView.submission_id)) {
      throw new TypeError("submission lifecycle event subjects disagree with the view");
    }
    for (let index = 0; index < events.length; index += 1) {
      const expectedCause = index === 0 ? expectedLifecycleEventId : events[index - 1]?.event_id;
      if (events[index]?.causation_event_id !== expectedCause) {
        throw new TypeError("submission lifecycle batch does not form one causal chain");
      }
    }
    const lastEventId = events.at(-1)?.event_id;
    if (!lastEventId || latestLifecycleEventId(decodedView) !== lastEventId) {
      throw new TypeError("submission lifecycle view does not name the batch terminal event");
    }
    const paths = events.map(stateEventPath);
    if (new Set(paths).size !== paths.length) throw new TypeError("submission lifecycle event paths must be unique");
    const viewPath = submissionViewPath(decodedView.submission_id);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const current = await readSubmissionAt(this.#config, this.#fetcher, decodedView.submission_id, snapshot.headSha);
      if (current === null) throw new GitHubStateError(404, "submission view does not exist");
      if (latestLifecycleEventId(current) === lastEventId) {
        const existing = await Promise.all(paths.map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)));
        if (
          existing.some((entry, index) => {
            if (!entry.found) return true;
            return canonicalJson(entry.value) !== canonicalJson(events[index]);
          }) ||
          canonicalJson(current) !== canonicalJson(decodedView)
        ) {
          throw new StateEventConflictError(viewPath);
        }
        return { commit: snapshot.headSha, created: false, view: current };
      }
      if (latestLifecycleEventId(current) !== expectedLifecycleEventId) {
        throw new StateEventConflictError(viewPath);
      }
      if (current.mutation_event_id !== decodedView.mutation_event_id) {
        throw new StateEventConflictError(viewPath);
      }
      const existing = await Promise.all(paths.map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)));
      if (existing.some((entry) => entry.found)) {
        throw new StateEventConflictError(paths[existing.findIndex((entry) => entry.found)] ?? viewPath);
      }
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        events,
        [{ path: viewPath, value: decodedView }],
        `Record lifecycle through ${lastEventId} for ${decodedView.submission_id}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, created: true, view: decodedView };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new GitHubStateError(409, "State branch kept changing during lifecycle update");
      }
      await pause(attempt);
    }
    throw new Error("unreachable submission lifecycle update");
  }

  async recordAcceptedResult(
    events: readonly WritableResultLifecycleEvent[],
    expectedLifecycleEventId: string,
    nextView: SubmissionView,
  ): Promise<{ commit: string; created: boolean; view: SubmissionView }> {
    if (events.length < 1 || events.length > 2) {
      throw new TypeError("result lifecycle batch must contain result recording and optional release schedule");
    }
    for (const event of events) validateStateEvent(event);
    const result = events[0];
    if (result?.event_type !== "result.recorded") {
      throw new TypeError("result lifecycle batch must begin with result.recorded");
    }
    const decodedView = decodeSubmissionView(nextView);
    if (
      decodedView.schema_version !== 2 ||
      decodedView.result_id !== result.subject_id ||
      decodedView.result_event_id !== result.event_id ||
      result.causation_event_id !== expectedLifecycleEventId ||
      result.payload.submission_id !== decodedView.submission_id ||
      result.payload.problem_id !== decodedView.submission.problem_id ||
      result.payload.statement_revision !== decodedView.submission.statement_revision
    ) {
      throw new TypeError("result lifecycle identities disagree with the submission view");
    }
    const release = events[1];
    const releaseRequired =
      decodedView.submission.problem_group !== "open-conjectures" &&
      decodedView.publication_choice === "scheduled";
    if (releaseRequired !== (release !== undefined)) {
      throw new TypeError("result lifecycle release scheduling disagrees with problem policy");
    }
    if (
      release !== undefined &&
      (release.event_type !== "release.scheduled" ||
        release.subject_id !== result.subject_id ||
        release.payload.result_id !== result.subject_id ||
        release.causation_event_id !== result.event_id ||
        Date.parse(release.occurred_at) <= Date.parse(result.occurred_at))
    ) {
      throw new TypeError("release schedule does not follow its recorded result");
    }
    const paths = events.map(stateEventPath);
    const viewPath = submissionViewPath(decodedView.submission_id);
    const identityPath = resultIdentityPath(result.subject_id);
    const identityGuard = recordedGuard(result.subject_id, result.event_id);
    const amendmentPath = resultAmendmentPath(result.subject_id);
    const amendmentView = initialResultAmendmentView({
      resultId: result.subject_id,
      ownerLogin: decodedView.owner_login,
      declaredModel: decodedView.submission.declared_model,
      problemId: result.payload.problem_id,
      statementRevision: result.payload.statement_revision,
      authorityEventId: result.event_id,
      mutationEventId: result.event_id,
    });
    const releaseStatusPath = resultReleaseStatusPath(result.subject_id);
    const releaseStatusView = initialResultReleaseStatusView(
      result.subject_id,
      result.event_id,
      release?.event_id ?? null,
    );
    const baseReservation = await effectiveResultIdentityReservation({
      ownerLogin: decodedView.owner_login,
      declaredModel: decodedView.submission.declared_model,
      problemId: result.payload.problem_id,
      statementRevision: result.payload.statement_revision,
      resultId: result.subject_id,
      reservationEventId: result.event_id,
      reservationKind: "result_authority",
    });
    const baseReservationPath = effectiveResultIdentityPath(
      baseReservation.effective_result_identity_id,
    );
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await this.#resultOwnerSnapshot();
      const current = await readSubmissionAt(
        this.#config,
        this.#fetcher,
        decodedView.submission_id,
        snapshot.headSha,
      );
      if (current === null) throw new GitHubStateError(404, "submission view does not exist");
      if (current.schema_version === 2 && current.result_event_id === result.event_id) {
        const existing = await Promise.all([
          ...paths.map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)),
          readPathAt(this.#config, this.#fetcher, identityPath, snapshot.headSha),
          readPathAt(this.#config, this.#fetcher, amendmentPath, snapshot.headSha),
          readPathAt(this.#config, this.#fetcher, releaseStatusPath, snapshot.headSha),
          readPathAt(this.#config, this.#fetcher, baseReservationPath, snapshot.headSha),
        ]);
        const currentGuardEntry = existing.at(paths.length);
        const currentAmendmentEntry = existing.at(paths.length + 1);
        const currentReleaseStatusEntry = existing.at(paths.length + 2);
        const currentReservationEntry = existing.at(paths.length + 3);
        let currentAmendment;
        let currentReleaseStatus;
        try {
          currentAmendment = decodeResultAmendmentView(
            currentAmendmentEntry?.found ? currentAmendmentEntry.value : null,
          );
          currentReleaseStatus = decodeResultReleaseStatusView(
            currentReleaseStatusEntry?.found ? currentReleaseStatusEntry.value : null,
          );
        } catch {
          throw new StateEventConflictError(viewPath);
        }
        if (
          existing.slice(0, paths.length).some((entry, index) =>
            !entry.found || canonicalJson(entry.value) !== canonicalJson(events[index])) ||
          !currentGuardEntry?.found ||
          canonicalJson(currentGuardEntry.value) !== canonicalJson(identityGuard) ||
          currentAmendment.result_id !== result.subject_id ||
          currentAmendment.owner_login !== decodedView.owner_login ||
          currentAmendment.declared_model !== decodedView.submission.declared_model ||
          currentAmendment.authority_event_id !== result.event_id ||
          currentAmendment.base_problem_id !== result.payload.problem_id ||
          currentAmendment.base_statement_revision !== result.payload.statement_revision ||
          currentReleaseStatus.result_id !== result.subject_id ||
          currentReleaseStatus.authority_event_id !== result.event_id ||
          !currentReservationEntry?.found ||
          canonicalJson(currentReservationEntry.value) !== canonicalJson(baseReservation) ||
          canonicalJson(current) !== canonicalJson(decodedView)
        ) {
          throw new StateEventConflictError(viewPath);
        }
        return { commit: snapshot.headSha, created: false, view: current };
      }
      if (
        (current.schema_version === 2 && current.result_event_id !== null) ||
        latestLifecycleEventId(current) !== expectedLifecycleEventId ||
        current.mutation_event_id !== decodedView.mutation_event_id
      ) {
        throw new StateEventConflictError(viewPath);
      }
      const [eventEntries, guardEntry, amendmentEntry, releaseStatusEntry, reservationEntry] = await Promise.all([
        Promise.all(paths.map((path) =>
          readPathAt(this.#config, this.#fetcher, path, snapshot.headSha))),
        readPathAt(this.#config, this.#fetcher, identityPath, snapshot.headSha),
        readPathAt(this.#config, this.#fetcher, amendmentPath, snapshot.headSha),
        readPathAt(this.#config, this.#fetcher, releaseStatusPath, snapshot.headSha),
        readPathAt(this.#config, this.#fetcher, baseReservationPath, snapshot.headSha),
      ]);
      if (guardEntry.found) {
        let guard;
        try {
          guard = decodeResultIdentityGuard(guardEntry.value);
        } catch {
          throw new StateEventConflictError(identityPath);
        }
        if (guard.result_id !== result.subject_id) {
          throw new StateEventConflictError(identityPath);
        }
        if (guard.record_kind === "claimed") {
          throw new ResultIdentityCollisionError("claimed");
        }
        if (guard.authority_event_id !== result.event_id) {
          throw new ResultIdentityCollisionError("recorded");
        }
        // A same-authority recorded guard and event are created atomically with
        // the matching submission view. Missing that view is corruption, not a
        // second result that this callback may safely adopt.
        throw new StateEventConflictError(viewPath);
      }
      if (amendmentEntry.found) throw new StateEventConflictError(amendmentPath);
      if (releaseStatusEntry.found) throw new StateEventConflictError(releaseStatusPath);
      if (reservationEntry.found) throw new StateEventConflictError(baseReservationPath);
      const eventConflict = eventEntries.findIndex((entry) => entry.found);
      if (eventConflict >= 0) {
        throw new StateEventConflictError(paths[eventConflict] ?? viewPath);
      }
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        events,
        [
          { path: viewPath, value: decodedView },
          { path: identityPath, value: identityGuard },
          { path: amendmentPath, value: amendmentView },
          { path: releaseStatusPath, value: releaseStatusView },
          { path: baseReservationPath, value: baseReservation },
        ],
        `Record accepted result ${result.subject_id} for ${decodedView.submission_id}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, created: true, view: decodedView };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new GitHubStateError(409, "State branch kept changing during result recording");
      }
      await pause(attempt);
    }
    throw new Error("unreachable result recording attempt");
  }

  async #resultAmendmentAt(
    resultId: string,
    snapshot: BranchSnapshot,
  ): Promise<{
    view: ResultAmendmentView;
    mutationEvent: StateEvent;
    overlay: ResultOverlay | null;
    releaseStatus: ResultReleaseStatusView;
  }> {
    const identityPath = resultIdentityPath(resultId);
    const amendmentPath = resultAmendmentPath(resultId);
    const overlayPath = resultOverlayPath(resultId);
    const releaseStatusPath = resultReleaseStatusPath(resultId);
    const [guard, trackedView, overlay, releaseStatus] = await Promise.all([
      readResultOwnerDocumentAt(
        this.#config,
        this.#fetcher,
        identityPath,
        snapshot.headSha,
        decodeResultIdentityGuard,
      ),
      readResultOwnerDocumentAt(
        this.#config,
        this.#fetcher,
        amendmentPath,
        snapshot.headSha,
        decodeResultAmendmentView,
      ),
      readResultOwnerDocumentAt(
        this.#config,
        this.#fetcher,
        overlayPath,
        snapshot.headSha,
        decodeResultOverlay,
      ),
      readResultOwnerDocumentAt(
        this.#config,
        this.#fetcher,
        releaseStatusPath,
        snapshot.headSha,
        decodeResultReleaseStatusView,
      ),
    ]);
    if (guard?.result_id !== resultId) {
      throw new ResultOwnerStateError(404, "result authority was not found");
    }
    const authority = await readEventAt(
      this.#config,
      this.#fetcher,
      guard.authority_event_id,
      snapshot.headSha,
    );
    if (authority.subject_id !== resultId) {
      throw new StateEventConflictError(identityPath);
    }

    let initial: ResultAmendmentView;
    if (guard.record_kind === "claimed") {
      if (
        authority.event_type !== "result.claimed" ||
        overlay?.result_id !== resultId ||
        overlay.claim_event_id !== authority.event_id ||
        overlay.owner_login !== authority.actor.login ||
        canonicalResultJson(overlay.base_result) !== canonicalResultJson(authority.payload)
      ) {
        throw new StateEventConflictError(identityPath);
      }
      initial = initialResultAmendmentView({
        resultId,
        ownerLogin: overlay.owner_login,
        declaredModel: overlay.base_result.declared_model,
        authorityEventId: authority.event_id,
        mutationEventId: authority.event_id,
        problemId: overlay.base_result.problem_id,
        statementRevision: overlay.base_result.statement_revision,
      });
    } else {
      if (authority.event_type !== "result.recorded" || overlay !== null) {
        throw new StateEventConflictError(identityPath);
      }
      const resultAuthority = authority as ResultRecordedEvent;
      const submission = await readSubmissionAt(
        this.#config,
        this.#fetcher,
        resultAuthority.payload.submission_id,
        snapshot.headSha,
      );
      if (
        submission?.schema_version !== 2 ||
        submission.result_id !== resultId ||
        submission.result_event_id !== authority.event_id ||
        submission.submission.problem_id !== resultAuthority.payload.problem_id ||
        submission.submission.statement_revision !== resultAuthority.payload.statement_revision
      ) {
        throw new StateEventConflictError(identityPath);
      }
      initial = initialResultAmendmentView({
        resultId,
        ownerLogin: submission.owner_login,
        declaredModel: submission.submission.declared_model,
        authorityEventId: authority.event_id,
        mutationEventId: authority.event_id,
        problemId: resultAuthority.payload.problem_id,
        statementRevision: resultAuthority.payload.statement_revision,
      });
    }

    if (trackedView === null) {
      throw new StateEventConflictError(amendmentPath);
    }
    if (
      releaseStatus?.result_id !== resultId ||
      releaseStatus.authority_event_id !== initial.authority_event_id
    ) {
      throw new StateEventConflictError(releaseStatusPath);
    }
    await assertReleaseStatusMarkerAt(
      this.#config,
      this.#fetcher,
      releaseStatus,
      snapshot.headSha,
    );
    const view = trackedView;
    if (
      view.result_id !== initial.result_id ||
      view.owner_login !== initial.owner_login ||
      view.declared_model !== initial.declared_model ||
      view.authority_event_id !== initial.authority_event_id ||
      view.base_problem_id !== initial.base_problem_id ||
      view.base_statement_revision !== initial.base_statement_revision
    ) {
      throw new StateEventConflictError(amendmentPath);
    }
    const mutationEvent = await readEventAt(
      this.#config,
      this.#fetcher,
      view.mutation_event_id,
      snapshot.headSha,
    );
    if (mutationEvent.subject_id !== resultId) {
      throw new StateEventConflictError(amendmentPath);
    }
    if (!amendmentMarkerMatches(view, mutationEvent)) {
      throw new StateEventConflictError(amendmentPath);
    }
    await assertAmendmentHistoryAt(
      this.#config,
      this.#fetcher,
      view,
      authority,
      mutationEvent,
      snapshot.headSha,
    );
    if (
      mutationEvent.event_type === "result.metadata_backfilled" &&
      (overlay?.mutation_event_id !== mutationEvent.event_id ||
        mutationEvent.actor.login !== view.owner_login)
    ) {
      throw new StateEventConflictError(amendmentPath);
    }
    return { view, mutationEvent, overlay, releaseStatus };
  }

  async requestResultProblemRepair(request: ResultProblemRepairRequest): Promise<{
    commit: string;
    created: boolean;
    resultId: string;
    mutationEventId: string;
    repairRevision: number;
  }> {
    const requestedEventPath = `events/${request.eventId.replaceAll("-", "").slice(0, 2)}/${request.eventId}.json`;
    const amendmentPath = resultAmendmentPath(request.resultId);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await this.#resultOwnerSnapshot();
      const [{ view, mutationEvent, releaseStatus }, requestedEventEntry] = await Promise.all([
        this.#resultAmendmentAt(request.resultId, snapshot),
        readPathAt(this.#config, this.#fetcher, requestedEventPath, snapshot.headSha),
      ]);
      if (view.owner_login !== request.ownerLogin) {
        throw new ResultOwnerStateError(404, "result authority was not found");
      }
      if (requestedEventEntry.found) {
        let existing: StateEvent;
        try {
          validateStateEvent(requestedEventEntry.value);
          existing = requestedEventEntry.value;
        } catch {
          throw new StateEventConflictError(requestedEventPath);
        }
        if (
          existing.event_type !== "result.problem_repair_requested" ||
          existing.event_id !== request.eventId ||
          existing.subject_id !== request.resultId ||
          existing.actor.login !== request.ownerLogin ||
          existing.payload.corrected_problem_id !== request.correctedProblemId ||
          existing.payload.corrected_statement_revision !== request.correctedStatementRevision ||
          existing.payload.reason_code !== request.reasonCode
        ) {
          throw new StateEventConflictError(requestedEventPath);
        }
        return {
          commit: snapshot.headSha,
          created: false,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          repairRevision: existing.payload.repair_revision,
        };
      }
      if (
        view.problem_repair?.status === "pending" ||
        view.retraction?.status === "pending" ||
        view.retraction?.status === "approved" ||
        view.retraction?.status === "retracted" ||
        !view.leaderboard_eligible
      ) {
        throw new ResultOwnerStateError(409, "result has a conflicting amendment state");
      }
      if (new Set(["running", "published", "removed"]).has(releaseStatus.status)) {
        throw new ResultOwnerStateError(409, "result release state forbids problem repair");
      }
      if (
        request.correctedProblemId === view.effective_problem_id &&
        request.correctedStatementRevision === view.effective_statement_revision
      ) {
        throw new ResultOwnerStateError(409, "result problem repair must change the effective problem");
      }
      if (
        request.eventId <= view.mutation_event_id ||
        Date.parse(request.occurredAt) <= Date.parse(mutationEvent.occurred_at)
      ) {
        throw new ResultOwnerStateError(409, "result problem repair request does not follow the current mutation");
      }
      const revision = (view.problem_repair?.revision ?? 0) + 1;
      const event: ResultProblemRepairRequestedEvent = {
        schema_version: 1,
        event_id: request.eventId,
        event_type: "result.problem_repair_requested",
        occurred_at: request.occurredAt,
        subject_id: request.resultId,
        causation_event_id: view.mutation_event_id,
        actor: { kind: "github", login: request.ownerLogin },
        payload: {
          repair_revision: revision,
          corrected_problem_id: request.correctedProblemId,
          corrected_statement_revision: request.correctedStatementRevision,
          reason_code: request.reasonCode,
        },
      };
      validateStateEvent(event);
      const nextView = requestedProblemRepairView(
        view,
        request.eventId,
        request.occurredAt,
        request.correctedProblemId,
        request.correctedStatementRevision,
        request.reasonCode,
      );
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [event],
        [{ path: amendmentPath, value: nextView }],
        `Request owner problem repair for ${request.resultId}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return {
          commit,
          created: true,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          repairRevision: revision,
        };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new ResultOwnerStateError(409, "State branch kept changing during problem repair request");
      }
      await pause(attempt);
    }
    throw new Error("unreachable result problem repair request attempt");
  }

  async decideResultProblemRepair(request: ResultProblemRepairDecisionRequest): Promise<{
    commit: string;
    created: boolean;
    resultId: string;
    mutationEventId: string;
    repairRevision: number;
  }> {
    const requestedEventPath = `events/${request.eventId.replaceAll("-", "").slice(0, 2)}/${request.eventId}.json`;
    const amendmentPath = resultAmendmentPath(request.resultId);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await this.#resultOwnerSnapshot();
      const [{ view, mutationEvent, releaseStatus }, requestedEventEntry] = await Promise.all([
        this.#resultAmendmentAt(request.resultId, snapshot),
        readPathAt(this.#config, this.#fetcher, requestedEventPath, snapshot.headSha),
      ]);
      if (requestedEventEntry.found) {
        let existing: StateEvent;
        try {
          validateStateEvent(requestedEventEntry.value);
          existing = requestedEventEntry.value;
        } catch {
          throw new StateEventConflictError(requestedEventPath);
        }
        const expectedType = request.decision === "apply"
          ? "result.problem_repaired"
          : "result.problem_repair_rejected";
        if (
          existing.event_type !== expectedType ||
          existing.event_id !== request.eventId ||
          existing.subject_id !== request.resultId ||
          existing.payload.reviewer_login !== request.reviewerLogin ||
          view.mutation_event_id !== request.eventId ||
          !amendmentMarkerMatches(view, existing) ||
          (request.decision === "reject" && existing.payload.reason_code !== request.reasonCode) ||
          (request.decision === "apply" &&
            canonicalResultJson(view.problem_repair?.comparator_evidence) !==
              canonicalResultJson(request.comparatorEvidence))
        ) {
          throw new StateEventConflictError(requestedEventPath);
        }
        if (request.decision === "apply") {
          const repair = view.problem_repair;
          if (repair?.status !== "applied") throw new StateEventConflictError(amendmentPath);
          const expectedReservation = await effectiveResultIdentityReservation({
            ownerLogin: view.owner_login,
            declaredModel: view.declared_model,
            problemId: repair.corrected_problem_id,
            statementRevision: repair.corrected_statement_revision,
            resultId: request.resultId,
            reservationEventId: request.eventId,
            reservationKind: "problem_repair",
          });
          const reservationPath = effectiveResultIdentityPath(
            expectedReservation.effective_result_identity_id,
          );
          const reservationEntry = await readPathAt(
            this.#config,
            this.#fetcher,
            reservationPath,
            snapshot.headSha,
          );
          let reservation;
          try {
            reservation = decodeEffectiveResultIdentityReservation(
              reservationEntry.found ? reservationEntry.value : null,
            );
          } catch {
            throw new StateEventConflictError(reservationPath);
          }
          if (
            reservation.effective_result_identity_id !==
              expectedReservation.effective_result_identity_id ||
            reservation.owner_login !== expectedReservation.owner_login ||
            reservation.declared_model !== expectedReservation.declared_model ||
            reservation.problem_id !== expectedReservation.problem_id ||
            reservation.statement_revision !== expectedReservation.statement_revision ||
            reservation.result_id !== request.resultId
          ) {
            throw new StateEventConflictError(reservationPath);
          }
          await assertEffectiveResultReservationAt(
            this.#config,
            this.#fetcher,
            reservation,
            view,
            snapshot.headSha,
          );
        }
        return {
          commit: snapshot.headSha,
          created: false,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          repairRevision: existing.payload.repair_revision as number,
        };
      }
      const pending = view.problem_repair;
      if (pending?.status !== "pending" || view.mutation_event_id !== pending.request_event_id) {
        throw new ResultOwnerStateError(409, "result does not have one pending problem repair");
      }
      if (new Set(["running", "published", "removed"]).has(releaseStatus.status)) {
        throw new ResultOwnerStateError(409, "result release state forbids problem repair decision");
      }
      if (
        request.eventId <= view.mutation_event_id ||
        Date.parse(request.occurredAt) <= Date.parse(mutationEvent.occurred_at)
      ) {
        throw new ResultOwnerStateError(409, "problem repair decision does not follow the pending request");
      }
      if (
        (request.decision === "apply" && (request.reasonCode !== null || request.comparatorEvidence === null)) ||
        (request.decision === "reject" && (request.reasonCode === null || request.comparatorEvidence !== null))
      ) {
        throw new ResultOwnerStateError(409, "problem repair decision material is inconsistent");
      }
      const evidence = request.comparatorEvidence;
      let candidateReservationWrite: TreeWrite | null = null;
      if (request.decision === "apply") {
        if (
          evidence?.evidence_result_id !== request.resultId ||
          evidence.evidence_owner_login !== view.owner_login ||
          evidence.evidence_declared_model !== view.declared_model ||
          evidence.evidence_base_problem_id !== view.base_problem_id ||
          evidence.evidence_base_statement_revision !== view.base_statement_revision ||
          evidence.evidence_corrected_problem_id !== pending.corrected_problem_id ||
          evidence.evidence_corrected_statement_revision !== pending.corrected_statement_revision ||
          evidence.evidence_base_problem_group !== evidence.evidence_corrected_problem_group
        ) {
          throw new ResultOwnerStateError(409, "problem repair comparator evidence disagrees with State");
        }
        const {
          binding_sha256: suppliedBinding,
          ...evidenceWithoutBinding
        } = evidence;
        const [baseChallengeId, correctedChallengeId, binding] = await Promise.all([
          challengeId(
            evidence.evidence_base_problem_group,
            evidence.evidence_base_problem_id,
            evidence.evidence_base_statement_revision,
          ),
          challengeId(
            evidence.evidence_corrected_problem_group,
            evidence.evidence_corrected_problem_id,
            evidence.evidence_corrected_statement_revision,
          ),
          comparatorBindingSha256(evidenceWithoutBinding),
        ]);
        if (
          evidence.evidence_base_challenge_id !== baseChallengeId ||
          evidence.evidence_corrected_challenge_id !== correctedChallengeId ||
          suppliedBinding !== binding
        ) {
          throw new ResultOwnerStateError(409, "problem repair comparator evidence digest was invalid");
        }
        const candidateReservation = await effectiveResultIdentityReservation({
          ownerLogin: view.owner_login,
          declaredModel: view.declared_model,
          problemId: pending.corrected_problem_id,
          statementRevision: pending.corrected_statement_revision,
          resultId: request.resultId,
          reservationEventId: request.eventId,
          reservationKind: "problem_repair",
        });
        const candidateReservationPath = effectiveResultIdentityPath(
          candidateReservation.effective_result_identity_id,
        );
        const reservationEntry = await readPathAt(
          this.#config,
          this.#fetcher,
          candidateReservationPath,
          snapshot.headSha,
        );
        if (reservationEntry.found) {
          let reservation;
          try {
            reservation = decodeEffectiveResultIdentityReservation(reservationEntry.value);
          } catch {
            throw new StateEventConflictError(candidateReservationPath);
          }
          if (
            reservation.effective_result_identity_id !==
              candidateReservation.effective_result_identity_id ||
            reservation.owner_login !== candidateReservation.owner_login ||
            reservation.declared_model !== candidateReservation.declared_model ||
            reservation.problem_id !== candidateReservation.problem_id ||
            reservation.statement_revision !== candidateReservation.statement_revision
          ) {
            throw new StateEventConflictError(candidateReservationPath);
          }
          if (reservation.result_id !== request.resultId) {
            throw new ResultOwnerStateError(
              409,
              "problem repair effective identity is permanently reserved by another result",
            );
          }
          await assertEffectiveResultReservationAt(
            this.#config,
            this.#fetcher,
            reservation,
            view,
            snapshot.headSha,
          );
        } else {
          candidateReservationWrite = {
            path: candidateReservationPath,
            value: candidateReservation,
          };
        }
      }
      let event: ResultAmendmentSystemEvent;
      if (request.decision === "apply") {
        if (evidence === null) throw new Error("applied problem repair omitted comparator evidence");
        event = {
          schema_version: 1,
          event_id: request.eventId,
          event_type: "result.problem_repaired",
          occurred_at: request.occurredAt,
          subject_id: request.resultId,
          causation_event_id: pending.request_event_id,
          actor: { kind: "system" },
          payload: {
            repair_revision: pending.revision,
            corrected_problem_id: pending.corrected_problem_id,
            corrected_statement_revision: pending.corrected_statement_revision,
            reviewer_login: request.reviewerLogin,
            comparator_repository: evidence.repository,
            comparator_commit: evidence.commit,
            comparator_path: evidence.path,
            comparator_blob_oid: evidence.blob_oid,
            comparator_blob_sha256: evidence.blob_sha256,
            comparator_record_sha256: evidence.record_sha256,
            comparator_binding_sha256: evidence.binding_sha256,
            comparator_verification_method: evidence.verification_method,
            evidence_result_id: evidence.evidence_result_id,
            evidence_owner_login: evidence.evidence_owner_login,
            evidence_declared_model: evidence.evidence_declared_model,
            evidence_base_problem_group: evidence.evidence_base_problem_group,
            evidence_base_problem_id: evidence.evidence_base_problem_id,
            evidence_base_statement_revision: evidence.evidence_base_statement_revision,
            evidence_base_challenge_id: evidence.evidence_base_challenge_id,
            evidence_corrected_problem_group: evidence.evidence_corrected_problem_group,
            evidence_corrected_problem_id: evidence.evidence_corrected_problem_id,
            evidence_corrected_statement_revision: evidence.evidence_corrected_statement_revision,
            evidence_corrected_challenge_id: evidence.evidence_corrected_challenge_id,
          },
        };
      } else {
        event = {
          schema_version: 1,
          event_id: request.eventId,
          event_type: "result.problem_repair_rejected",
          occurred_at: request.occurredAt,
          subject_id: request.resultId,
          causation_event_id: pending.request_event_id,
          actor: { kind: "system" },
          payload: {
            repair_revision: pending.revision,
            reviewer_login: request.reviewerLogin,
            reason_code: request.reasonCode,
          },
        };
      }
      validateStateEvent(event);
      const nextView = decidedProblemRepairView(
        view,
        request.eventId,
        request.occurredAt,
        request.reviewerLogin,
        request.decision,
        request.reasonCode,
        request.comparatorEvidence,
      );
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [event],
        [
          { path: amendmentPath, value: nextView },
          ...(candidateReservationWrite === null ? [] : [candidateReservationWrite]),
        ],
        `Record maintainer problem repair decision for ${request.resultId}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return {
          commit,
          created: true,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          repairRevision: pending.revision,
        };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new ResultOwnerStateError(409, "State branch kept changing during problem repair decision");
      }
      await pause(attempt);
    }
    throw new Error("unreachable problem repair decision attempt");
  }

  async requestResultRetraction(request: ResultRetractionRequest): Promise<{
    commit: string;
    created: boolean;
    resultId: string;
    mutationEventId: string;
    retractionRevision: number;
  }> {
    const requestedEventPath = `events/${request.eventId.replaceAll("-", "").slice(0, 2)}/${request.eventId}.json`;
    const amendmentPath = resultAmendmentPath(request.resultId);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await this.#resultOwnerSnapshot();
      const [{ view, mutationEvent }, requestedEventEntry] = await Promise.all([
        this.#resultAmendmentAt(request.resultId, snapshot),
        readPathAt(this.#config, this.#fetcher, requestedEventPath, snapshot.headSha),
      ]);
      if (view.owner_login !== request.ownerLogin) {
        throw new ResultOwnerStateError(404, "result authority was not found");
      }
      if (requestedEventEntry.found) {
        let existing: StateEvent;
        try {
          validateStateEvent(requestedEventEntry.value);
          existing = requestedEventEntry.value;
        } catch {
          throw new StateEventConflictError(requestedEventPath);
        }
        if (
          existing.event_type !== "result.retraction_requested" ||
          existing.event_id !== request.eventId ||
          existing.subject_id !== request.resultId ||
          existing.actor.login !== request.ownerLogin ||
          existing.payload.reason_code !== request.reasonCode
        ) {
          throw new StateEventConflictError(requestedEventPath);
        }
        return {
          commit: snapshot.headSha,
          created: false,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          retractionRevision: existing.payload.retraction_revision,
        };
      }
      if (
        view.problem_repair?.status === "pending" ||
        view.retraction?.status === "pending" ||
        view.retraction?.status === "approved" ||
        view.retraction?.status === "retracted" ||
        !view.leaderboard_eligible
      ) {
        throw new ResultOwnerStateError(409, "result has a conflicting amendment state");
      }
      if (
        request.eventId <= view.mutation_event_id ||
        Date.parse(request.occurredAt) <= Date.parse(mutationEvent.occurred_at)
      ) {
        throw new ResultOwnerStateError(409, "result retraction request does not follow the current mutation");
      }
      const revision = (view.retraction?.revision ?? 0) + 1;
      const event: ResultRetractionRequestedEvent = {
        schema_version: 1,
        event_id: request.eventId,
        event_type: "result.retraction_requested",
        occurred_at: request.occurredAt,
        subject_id: request.resultId,
        causation_event_id: view.mutation_event_id,
        actor: { kind: "github", login: request.ownerLogin },
        payload: { retraction_revision: revision, reason_code: request.reasonCode },
      };
      validateStateEvent(event);
      const nextView = requestedRetractionView(
        view,
        request.eventId,
        request.occurredAt,
        request.reasonCode,
      );
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [event],
        [{ path: amendmentPath, value: nextView }],
        `Request owner retraction for ${request.resultId}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return {
          commit,
          created: true,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          retractionRevision: revision,
        };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new ResultOwnerStateError(409, "State branch kept changing during result retraction request");
      }
      await pause(attempt);
    }
    throw new Error("unreachable result retraction request attempt");
  }

  async decideResultRetraction(request: ResultRetractionDecisionRequest): Promise<{
    commit: string;
    created: boolean;
    resultId: string;
    mutationEventId: string;
    retractionRevision: number;
  }> {
    const requestedEventPath = `events/${request.eventId.replaceAll("-", "").slice(0, 2)}/${request.eventId}.json`;
    const amendmentPath = resultAmendmentPath(request.resultId);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await this.#resultOwnerSnapshot();
      const [{ view, mutationEvent }, requestedEventEntry] = await Promise.all([
        this.#resultAmendmentAt(request.resultId, snapshot),
        readPathAt(this.#config, this.#fetcher, requestedEventPath, snapshot.headSha),
      ]);
      if (requestedEventEntry.found) {
        let existing: StateEvent;
        try {
          validateStateEvent(requestedEventEntry.value);
          existing = requestedEventEntry.value;
        } catch {
          throw new StateEventConflictError(requestedEventPath);
        }
        if (
          existing.event_type !== "result.retraction_decided" ||
          existing.event_id !== request.eventId ||
          existing.subject_id !== request.resultId ||
          existing.payload.reviewer_login !== request.reviewerLogin ||
          existing.payload.decision !== request.decision ||
          existing.payload.reason_code !== request.reasonCode ||
          view.mutation_event_id !== request.eventId ||
          !amendmentMarkerMatches(view, existing)
        ) {
          throw new StateEventConflictError(requestedEventPath);
        }
        return {
          commit: snapshot.headSha,
          created: false,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          retractionRevision: existing.payload.retraction_revision as number,
        };
      }
      const pending = view.retraction;
      if (pending?.status !== "pending" || view.mutation_event_id !== pending.request_event_id) {
        throw new ResultOwnerStateError(409, "result does not have one pending retraction request");
      }
      if (
        request.eventId <= view.mutation_event_id ||
        Date.parse(request.occurredAt) <= Date.parse(mutationEvent.occurred_at)
      ) {
        throw new ResultOwnerStateError(409, "result retraction decision does not follow the current mutation");
      }
      const event: ResultAmendmentSystemEvent = {
        schema_version: 1,
        event_id: request.eventId,
        event_type: "result.retraction_decided",
        occurred_at: request.occurredAt,
        subject_id: request.resultId,
        causation_event_id: pending.request_event_id,
        actor: { kind: "system" },
        payload: {
          retraction_revision: pending.revision,
          reviewer_login: request.reviewerLogin,
          decision: request.decision,
          reason_code: request.reasonCode,
        },
      };
      validateStateEvent(event);
      const nextView = decidedRetractionView(
        view,
        request.eventId,
        request.occurredAt,
        request.reviewerLogin,
        request.decision,
        request.reasonCode,
      );
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [event],
        [{ path: amendmentPath, value: nextView }],
        `Record maintainer retraction decision for ${request.resultId}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return {
          commit,
          created: true,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          retractionRevision: pending.revision,
        };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new ResultOwnerStateError(409, "State branch kept changing during retraction decision");
      }
      await pause(attempt);
    }
    throw new Error("unreachable result retraction decision attempt");
  }

  async overrideResultRetraction(request: ResultRetractionOverrideRequest): Promise<{
    commit: string;
    created: boolean;
    resultId: string;
    mutationEventId: string;
    retractionRevision: number;
  }> {
    const requestedEventPath = `events/${request.eventId.replaceAll("-", "").slice(0, 2)}/${request.eventId}.json`;
    const amendmentPath = resultAmendmentPath(request.resultId);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await this.#resultOwnerSnapshot();
      const [{ view, mutationEvent }, requestedEventEntry] = await Promise.all([
        this.#resultAmendmentAt(request.resultId, snapshot),
        readPathAt(this.#config, this.#fetcher, requestedEventPath, snapshot.headSha),
      ]);
      if (requestedEventEntry.found) {
        let existing: StateEvent;
        try {
          validateStateEvent(requestedEventEntry.value);
          existing = requestedEventEntry.value;
        } catch {
          throw new StateEventConflictError(requestedEventPath);
        }
        if (
          existing.event_type !== "result.retraction_overridden" ||
          existing.event_id !== request.eventId ||
          existing.subject_id !== request.resultId ||
          existing.payload.reviewer_login !== request.reviewerLogin ||
          existing.payload.reason_code !== request.reasonCode ||
          view.mutation_event_id !== request.eventId ||
          !amendmentMarkerMatches(view, existing)
        ) {
          throw new StateEventConflictError(requestedEventPath);
        }
        return {
          commit: snapshot.headSha,
          created: false,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          retractionRevision: existing.payload.retraction_revision as number,
        };
      }
      if (
        request.eventId <= view.mutation_event_id ||
        Date.parse(request.occurredAt) <= Date.parse(mutationEvent.occurred_at)
      ) {
        throw new ResultOwnerStateError(409, "result retraction override does not follow the current mutation");
      }
      let nextView: ResultAmendmentView;
      try {
        nextView = overriddenRetractionView(
          view,
          request.eventId,
          request.occurredAt,
          request.reviewerLogin,
          request.reasonCode,
        );
      } catch {
        throw new ResultOwnerStateError(409, "result has a conflicting amendment state");
      }
      const revision = nextView.retraction?.revision;
      if (revision === undefined) throw new Error("retraction override transition omitted its revision");
      const event: ResultAmendmentSystemEvent = {
        schema_version: 1,
        event_id: request.eventId,
        event_type: "result.retraction_overridden",
        occurred_at: request.occurredAt,
        subject_id: request.resultId,
        causation_event_id: view.mutation_event_id,
        actor: { kind: "system" },
        payload: {
          retraction_revision: revision,
          reviewer_login: request.reviewerLogin,
          reason_code: request.reasonCode,
        },
      };
      validateStateEvent(event);
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [event],
        [{ path: amendmentPath, value: nextView }],
        `Record maintainer retraction override for ${request.resultId}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return {
          commit,
          created: true,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          retractionRevision: revision,
        };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new ResultOwnerStateError(409, "State branch kept changing during retraction override");
      }
      await pause(attempt);
    }
    throw new Error("unreachable result retraction override attempt");
  }

  async finalizeResultRetraction(request: ResultRetractionFinalizationRequest): Promise<{
    commit: string;
    created: boolean;
    resultId: string;
    mutationEventId: string;
    releaseDisposition: "not_published" | "removal_required" | "already_removed";
  }> {
    const requestedEventPath = `events/${request.eventId.replaceAll("-", "").slice(0, 2)}/${request.eventId}.json`;
    const amendmentPath = resultAmendmentPath(request.resultId);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await this.#resultOwnerSnapshot();
      const [{ view, mutationEvent, releaseStatus }, requestedEventEntry] = await Promise.all([
        this.#resultAmendmentAt(request.resultId, snapshot),
        readPathAt(this.#config, this.#fetcher, requestedEventPath, snapshot.headSha),
      ]);
      if (requestedEventEntry.found) {
        let existing: StateEvent;
        try {
          validateStateEvent(requestedEventEntry.value);
          existing = requestedEventEntry.value;
        } catch {
          throw new StateEventConflictError(requestedEventPath);
        }
        if (
          existing.event_type !== "result.retracted" ||
          existing.event_id !== request.eventId ||
          existing.subject_id !== request.resultId ||
          view.mutation_event_id !== request.eventId ||
          !amendmentMarkerMatches(view, existing)
        ) {
          throw new StateEventConflictError(requestedEventPath);
        }
        return {
          commit: snapshot.headSha,
          created: false,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          releaseDisposition: existing.payload.release_disposition as
            "not_published" | "removal_required" | "already_removed",
        };
      }
      const approved = view.retraction;
      if (
        approved?.status !== "approved" ||
        view.mutation_event_id !== approved.decision_event_id ||
        approved.reviewer_login === null ||
        approved.reason_code === null
      ) {
        throw new ResultOwnerStateError(409, "result does not have a matching approved retraction");
      }
      if (releaseStatus.status === "running") {
        throw new ResultOwnerStateError(409, "result release is still running");
      }
      if (
        request.eventId <= view.mutation_event_id ||
        Date.parse(request.occurredAt) <= Date.parse(mutationEvent.occurred_at)
      ) {
        throw new ResultOwnerStateError(409, "terminal retraction does not follow the approved decision");
      }
      const releaseDisposition = releaseStatus.status === "published"
        ? "removal_required"
        : releaseStatus.status === "removed"
          ? "already_removed"
          : "not_published";
      const event: ResultAmendmentSystemEvent = {
        schema_version: 1,
        event_id: request.eventId,
        event_type: "result.retracted",
        occurred_at: request.occurredAt,
        subject_id: request.resultId,
        causation_event_id: approved.decision_event_id,
        actor: { kind: "system" },
        payload: {
          retraction_revision: approved.revision,
          reviewer_login: approved.reviewer_login,
          reason_code: approved.reason_code,
          release_disposition: releaseDisposition,
        },
      };
      validateStateEvent(event);
      const nextView = terminalRetractionView(
        view,
        request.eventId,
        request.occurredAt,
        approved.reviewer_login,
        approved.reason_code,
        releaseDisposition,
      );
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [event],
        [{ path: amendmentPath, value: nextView }],
        `Record terminal retraction for ${request.resultId}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return {
          commit,
          created: true,
          resultId: request.resultId,
          mutationEventId: request.eventId,
          releaseDisposition,
        };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new ResultOwnerStateError(409, "State branch kept changing during terminal retraction");
      }
      await pause(attempt);
    }
    throw new Error("unreachable terminal retraction attempt");
  }

  async claimLegacyResult(request: LegacyResultClaimRequest): Promise<{
    commit: string;
    created: boolean;
    resultId: string;
    authorityEventId: string;
  }> {
    const { verified } = request;
    const event: ResultClaimedEvent = {
      schema_version: 1,
      event_id: request.eventId,
      event_type: "result.claimed",
      occurred_at: request.occurredAt,
      subject_id: verified.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: verified.ownerLogin },
      payload: verified.baseResult,
    };
    validateStateEvent(event);
    const identityPath = resultIdentityPath(verified.resultId);
    const overlayPath = resultOverlayPath(verified.resultId);
    const amendmentPath = resultAmendmentPath(verified.resultId);
    const releaseStatusPath = resultReleaseStatusPath(verified.resultId);
    const sourceId = await sourceRecordId(verified.baseResult);
    const sourcePath = sourceRecordPath(sourceId);
    const eventPath = stateEventPath(event);
    const baseReservation = await effectiveResultIdentityReservation({
      ownerLogin: verified.ownerLogin,
      declaredModel: verified.baseResult.declared_model,
      problemId: verified.baseResult.problem_id,
      statementRevision: verified.baseResult.statement_revision,
      resultId: verified.resultId,
      reservationEventId: request.eventId,
      reservationKind: "result_authority",
    });
    const baseReservationPath = effectiveResultIdentityPath(
      baseReservation.effective_result_identity_id,
    );
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await this.#resultOwnerSnapshot();
      const [
        guardEntry,
        overlayEntry,
        amendmentEntry,
        releaseStatusEntry,
        incomingSourceEntry,
        requestedEventEntry,
        baseReservationEntry,
      ] = await Promise.all([
        readPathAt(this.#config, this.#fetcher, identityPath, snapshot.headSha),
        readPathAt(this.#config, this.#fetcher, overlayPath, snapshot.headSha),
        readPathAt(this.#config, this.#fetcher, amendmentPath, snapshot.headSha),
        readPathAt(this.#config, this.#fetcher, releaseStatusPath, snapshot.headSha),
        readPathAt(this.#config, this.#fetcher, sourcePath, snapshot.headSha),
        readPathAt(this.#config, this.#fetcher, eventPath, snapshot.headSha),
        readPathAt(this.#config, this.#fetcher, baseReservationPath, snapshot.headSha),
      ]);
      if (
        guardEntry.found ||
        overlayEntry.found ||
        amendmentEntry.found ||
        releaseStatusEntry.found
      ) {
        let guard;
        try {
          guard = decodeResultIdentityGuard(guardEntry.found ? guardEntry.value : null);
        } catch {
          throw new StateEventConflictError(identityPath);
        }
        if (guard.record_kind === "recorded") {
          throw new ResultIdentityCollisionError("recorded");
        }
        let overlay;
        try {
          overlay = decodeResultOverlay(overlayEntry.found ? overlayEntry.value : null);
        } catch {
          throw new StateEventConflictError(identityPath);
        }
        let amendment;
        let releaseStatus;
        let reservation;
        try {
          amendment = decodeResultAmendmentView(
            amendmentEntry.found ? amendmentEntry.value : null,
          );
          releaseStatus = decodeResultReleaseStatusView(
            releaseStatusEntry.found ? releaseStatusEntry.value : null,
          );
          reservation = decodeEffectiveResultIdentityReservation(
            baseReservationEntry.found ? baseReservationEntry.value : null,
          );
        } catch {
          throw new StateEventConflictError(identityPath);
        }
        if (
          guard.result_id !== verified.resultId ||
          overlay.result_id !== verified.resultId ||
          overlay.owner_login !== verified.ownerLogin ||
          overlay.claim_event_id !== guard.authority_event_id ||
          amendment.result_id !== verified.resultId ||
          amendment.owner_login !== verified.ownerLogin ||
          amendment.declared_model !== overlay.base_result.declared_model ||
          amendment.authority_event_id !== guard.authority_event_id ||
          amendment.base_problem_id !== overlay.base_result.problem_id ||
          amendment.base_statement_revision !== overlay.base_result.statement_revision ||
          releaseStatus.result_id !== verified.resultId ||
          releaseStatus.authority_event_id !== guard.authority_event_id ||
          reservation.effective_result_identity_id !==
            baseReservation.effective_result_identity_id ||
          reservation.owner_login !== verified.ownerLogin ||
          reservation.declared_model !== overlay.base_result.declared_model ||
          reservation.problem_id !== overlay.base_result.problem_id ||
          reservation.statement_revision !== overlay.base_result.statement_revision ||
          reservation.result_id !== verified.resultId ||
          reservation.reservation_event_id !== guard.authority_event_id ||
          reservation.reservation_kind !== "result_authority"
        ) {
          throw new StateEventConflictError(identityPath);
        }
        if (requestedEventEntry.found && request.eventId !== guard.authority_event_id) {
          throw new StateEventConflictError(eventPath);
        }
        const authoritySourceId = await sourceRecordId(overlay.base_result);
        const authoritySourcePath = sourceRecordPath(authoritySourceId);
        const authoritySourceEntry = authoritySourcePath === sourcePath
          ? incomingSourceEntry
          : await readPathAt(this.#config, this.#fetcher, authoritySourcePath, snapshot.headSha);
        let source;
        try {
          source = decodeSourceRecordIndex(authoritySourceEntry.found ? authoritySourceEntry.value : null);
        } catch {
          throw new StateEventConflictError(authoritySourcePath);
        }
        const authority = await readEventAt(
          this.#config,
          this.#fetcher,
          guard.authority_event_id,
          snapshot.headSha,
        );
        if (
          !sameLogicalLegacyResult(overlay.base_result, verified.baseResult) ||
          source.source_record_id !== authoritySourceId ||
          source.result_id !== verified.resultId ||
          source.owner_login !== verified.ownerLogin ||
          source.claim_event_id !== guard.authority_event_id ||
          canonicalResultJson({
            results_repository: source.results_repository,
            results_commit: source.results_commit,
            results_path: source.results_path,
            canonical_record_sha256: source.canonical_record_sha256,
          }) !== canonicalResultJson({
            results_repository: verified.baseResult.results_repository,
            results_commit: overlay.base_result.results_commit,
            results_path: overlay.base_result.results_path,
            canonical_record_sha256: overlay.base_result.canonical_record_sha256,
          }) ||
          authority.event_type !== "result.claimed" ||
          authority.event_id !== guard.authority_event_id ||
          authority.subject_id !== verified.resultId ||
          authority.actor.login !== verified.ownerLogin ||
          authority.occurred_at !== overlay.claimed_at ||
          canonicalResultJson(authority.payload) !== canonicalResultJson(overlay.base_result)
        ) {
          throw new StateEventConflictError(identityPath);
        }
        if (authoritySourcePath !== sourcePath && incomingSourceEntry.found) {
          throw new StateEventConflictError(sourcePath);
        }
        if (requestedEventEntry.found) {
          const expectedReplay = { ...event, occurred_at: authority.occurred_at };
          if (
            request.eventId !== guard.authority_event_id ||
            canonicalResultJson(requestedEventEntry.value) !==
              canonicalResultJson(expectedReplay)
          ) {
            throw new StateEventConflictError(eventPath);
          }
        }
        return {
          commit: snapshot.headSha,
          created: false,
          resultId: verified.resultId,
          authorityEventId: guard.authority_event_id,
        };
      }
      if (incomingSourceEntry.found) throw new StateEventConflictError(sourcePath);
      if (requestedEventEntry.found) throw new StateEventConflictError(eventPath);
      if (baseReservationEntry.found) throw new StateEventConflictError(baseReservationPath);
      const occurredAt = Date.parse(request.occurredAt);
      const eventTimestamp = uuidV7Timestamp(request.eventId);
      if (
        eventTimestamp > occurredAt ||
        eventTimestamp < occurredAt - NEW_EVENT_CLOCK_WINDOW_MS
      ) {
        throw new ResultOwnerStateError(409, "Idempotency-Key does not match the current request clock");
      }
      const guard = claimedGuard(verified.resultId, request.eventId);
      const overlay = claimedOverlay(verified, request.eventId, request.occurredAt);
      const source = await claimedSourceIndex(verified, request.eventId);
      const amendment = initialResultAmendmentView({
        resultId: verified.resultId,
        ownerLogin: verified.ownerLogin,
        declaredModel: verified.baseResult.declared_model,
        problemId: verified.baseResult.problem_id,
        statementRevision: verified.baseResult.statement_revision,
        authorityEventId: request.eventId,
        mutationEventId: request.eventId,
      });
      const releaseStatus = initialResultReleaseStatusView(
        verified.resultId,
        request.eventId,
      );
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [event],
        [
          { path: identityPath, value: guard },
          { path: overlayPath, value: overlay },
          { path: sourcePath, value: source },
          { path: amendmentPath, value: amendment },
          { path: releaseStatusPath, value: releaseStatus },
          { path: baseReservationPath, value: baseReservation },
        ],
        `Claim legacy result ${verified.resultId}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return {
          commit,
          created: true,
          resultId: verified.resultId,
          authorityEventId: request.eventId,
        };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new ResultOwnerStateError(409, "State branch kept changing during legacy result claim");
      }
      await pause(attempt);
    }
    throw new Error("unreachable legacy result claim attempt");
  }

  async backfillLegacyResultMetadata(request: LegacyResultBackfillRequest): Promise<{
    commit: string;
    created: boolean;
    resultId: string;
    mutationEventId: string;
  }> {
    const overlayPath = resultOverlayPath(request.resultId);
    const amendmentPath = resultAmendmentPath(request.resultId);
    const requestedEventPath = `events/${request.eventId.replaceAll("-", "").slice(0, 2)}/${request.eventId}.json`;
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await this.#resultOwnerSnapshot();
      const [context, requestedEventEntry] = await Promise.all([
        this.#resultAmendmentAt(request.resultId, snapshot),
        readPathAt(this.#config, this.#fetcher, requestedEventPath, snapshot.headSha),
      ]);
      const { overlay, releaseStatus, view } = context;
      if (
        overlay?.result_id !== request.resultId ||
        overlay.owner_login !== request.ownerLogin ||
        view.owner_login !== request.ownerLogin
      ) {
        throw new ResultOwnerStateError(404, "legacy result claim was not found");
      }
      if (requestedEventEntry.found) {
        let existing: StateEvent;
        try {
          validateStateEvent(requestedEventEntry.value);
          existing = requestedEventEntry.value;
        } catch {
          throw new StateEventConflictError(requestedEventPath);
        }
        if (
          existing.event_type !== "result.metadata_backfilled" ||
          existing.event_id !== request.eventId ||
          existing.subject_id !== request.resultId ||
          existing.actor.login !== request.ownerLogin ||
          canonicalResultJson(existing.payload.production_metadata) !==
            canonicalResultJson(request.productionMetadata) ||
          Object.keys(request.productionMetadata).some((field) => {
            const provenance = (overlay.metadata as Record<string, MetadataProvenance | undefined>)[field];
            if (provenance?.event_id !== request.eventId) return true;
            return provenance.recorded_at !== existing.occurred_at ||
              canonicalResultJson(provenance.value) !==
                canonicalResultJson(request.productionMetadata[field as keyof ProductionMetadata]);
          })
        ) {
          throw new StateEventConflictError(requestedEventPath);
        }
        return {
          commit: snapshot.headSha,
          created: false,
          resultId: request.resultId,
          mutationEventId: request.eventId,
        };
      }
      if (metadataAlreadyEqual(overlay, request.productionMetadata)) {
        return {
          commit: snapshot.headSha,
          created: false,
          resultId: request.resultId,
          mutationEventId: view.mutation_event_id,
        };
      }
      if (new Set(["running", "published", "removed"]).has(releaseStatus.status)) {
        throw new ResultOwnerStateError(409, "result release state forbids metadata backfill");
      }
      if (
        view.problem_repair?.status === "pending" ||
        view.retraction?.status === "pending" ||
        view.retraction?.status === "approved" ||
        view.retraction?.status === "retracted" ||
        !view.leaderboard_eligible
      ) {
        throw new ResultOwnerStateError(409, "result has a conflicting amendment state");
      }
      if (
        request.eventId <= view.mutation_event_id ||
        Date.parse(request.occurredAt) <= Date.parse(context.mutationEvent.occurred_at)
      ) {
        throw new ResultOwnerStateError(409, "result metadata backfill does not follow the current mutation");
      }
      const event: ResultMetadataBackfilledEvent = {
        schema_version: 1,
        event_id: request.eventId,
        event_type: "result.metadata_backfilled",
        occurred_at: request.occurredAt,
        subject_id: request.resultId,
        causation_event_id: view.mutation_event_id,
        actor: { kind: "github", login: request.ownerLogin },
        payload: { production_metadata: request.productionMetadata },
      };
      validateStateEvent(event);
      const nextOverlay: ResultOverlay = backfilledOverlay(
        overlay,
        request.eventId,
        request.occurredAt,
        request.productionMetadata,
      );
      const nextAmendment = decodeResultAmendmentView({
        ...view,
        mutation_event_id: request.eventId,
      });
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [event],
        [
          { path: overlayPath, value: nextOverlay },
          { path: amendmentPath, value: nextAmendment },
        ],
        `Backfill legacy result metadata ${request.resultId}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return {
          commit,
          created: true,
          resultId: request.resultId,
          mutationEventId: request.eventId,
        };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new ResultOwnerStateError(409, "State branch kept changing during legacy result backfill");
      }
      await pause(attempt);
    }
    throw new Error("unreachable legacy result metadata backfill attempt");
  }

  async appendEvents(events: readonly WritableStateEvent[]): Promise<{
    commit: string;
    paths: readonly string[];
    created: boolean;
  }> {
    if (events.length === 0 || events.length > 4) {
      throw new TypeError("State append batch must contain between one and four events");
    }
    for (const event of events) validateStateEvent(event);
    const paths = events.map(stateEventPath);
    if (new Set(paths).size !== paths.length) throw new TypeError("State append paths must be unique");
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const existing = await Promise.all(
        paths.map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)),
      );
      const found = existing.filter((entry) => entry.found).length;
      if (found > 0) {
        if (found !== events.length) throw new StateEventConflictError(paths[existing.findIndex((entry) => entry.found)] ?? paths[0] ?? "events");
        for (let index = 0; index < events.length; index += 1) {
          const entry = existing[index];
          const event = events[index];
          if (!entry?.found || !event) throw new Error("unreachable State batch comparison");
          try {
            validateStateEvent(entry.value);
          } catch {
            throw new StateEventConflictError(paths[index] ?? "events");
          }
          if (canonicalJson(entry.value) !== canonicalJson(event)) {
            throw new StateEventConflictError(paths[index] ?? "events");
          }
        }
        return { commit: snapshot.headSha, paths, created: false };
      }
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        events,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, paths, created: true };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new GitHubStateError(409, "State branch kept changing underneath the append");
      }
      await pause(attempt);
    }
    throw new Error("unreachable State append attempt");
  }

  async appendEvent(event: WritableStateEvent): Promise<{ commit: string; path: string; created: boolean }> {
    const outcome = await this.appendEvents([event]);
    const path = outcome.paths[0];
    if (!path) throw new Error("unreachable empty State append outcome");
    return { commit: outcome.commit, path, created: outcome.created };
  }

  async appendEventAtHead(
    event: WritableStateEvent,
    expectedHead: string,
  ): Promise<{ commit: string; path: string; created: boolean }> {
    validateStateEvent(event);
    if (!SHA.test(expectedHead)) throw new TypeError("expected State head must be a commit SHA");
    const path = stateEventPath(event);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const existing = await readPathAt(this.#config, this.#fetcher, path, snapshot.headSha);
      if (existing.found) {
        try {
          validateStateEvent(existing.value);
        } catch {
          throw new StateEventConflictError(path);
        }
        if (canonicalJson(existing.value) !== canonicalJson(event)) {
          throw new StateEventConflictError(path);
        }
        return { commit: snapshot.headSha, path, created: false };
      }
      if (snapshot.headSha !== expectedHead) {
        throw new GitHubStateError(409, "State moved before the bound event append");
      }
      const commit = await createCommit(this.#config, this.#fetcher, snapshot, [event]);
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, path, created: true };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new GitHubStateError(409, "State branch kept changing underneath the bound append");
      }
      await pause(attempt);
    }
    throw new Error("unreachable bound State append attempt");
  }
}
