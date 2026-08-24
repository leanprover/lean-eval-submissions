import {
  ApiDecodeError,
  assertSourcePolicy,
  decodeArchiveCompletion,
  decodeArchiveFailure,
  decodeEvaluationCompletion,
  decodeLegacyResultClaim,
  decodeProblemRepairRequest,
  decodeProblemRepairDecision,
  decodeEmptyObject,
  decodeResultRetractionDecision,
  decodeResultRetractionOverride,
  decodeResultRetractionRequest,
  decodeResultCompletion,
  decodeAgentChallengeInput,
  decodeBrowserSubmission,
  decodeChallengeSubmission,
  decodeMetadataAmendment,
  decodePublicationChoice,
  decodeSourceReaderPreflight,
  isUuidV7,
  readJson,
  type ProductionMetadata,
  type PublicationChoice,
  type SubmissionInput,
} from "./api-contract";
import {
  AuthError,
  equalToken,
  lifecycleEventId,
  makeAgentChallenge,
  makeOAuthState,
  makeSubmissionGrant,
  nonceDigest,
  signToken,
  verifyToken,
  type AgentChallenge,
  type BrowserSession,
  type OAuthState,
  type SubmissionGrant,
} from "./auth";
import { browserPage, browserScript } from "./browser-ui";
import {
  type LegacyResultBackfillRequest,
  type LegacyResultClaimRequest,
  type ResultProblemRepairRequest,
  type ResultProblemRepairDecisionRequest,
  type ResultRetractionDecisionRequest,
  type ResultRetractionFinalizationRequest,
  type ResultRetractionOverrideRequest,
  type ResultRetractionRequest,
  type GitHubFetch,
  DISPATCH_OUTBOX_SCAN_LIMIT,
  DISPATCH_UPDATE_MAX_SUBREQUESTS,
  GitHubStateError,
  GitHubStateRepository,
  ResultIdentityCollisionError,
  ResultOwnerStateError,
  StateEventConflictError,
  StateUpdateOutcomeUnknownError,
} from "./github-state";
import {
  ScheduledSubrequestBudget,
} from "./scheduled-subrequest-budget";
import {
  authenticateMaintainer,
  decodeMaintainerIdentities,
  type MaintainerIdentity,
} from "./maintainer";
import {
  buildDispatchRequest,
  buildPromotionCanaryDispatchRequest,
  GitHubProvider,
  GitHubProviderError,
  type GitHubIdentity,
} from "./github-provider";
import { githubBrokerFetch } from "./github-broker-client";
import { intakeEnablement, type IntakeEnablement } from "./intake-enablement";
import {
  type ArchiveCompletedEvent,
  type ArchiveFailedEvent,
  type EvaluationAcceptedEvent,
  type EvaluationFailedEvent,
  type EvaluationRejectedEvent,
  type EvaluationStartedEvent,
  type ReleaseScheduledEvent,
  type ResultRecordedEvent,
  type WritableStateEvent,
  type WritableResultLifecycleEvent,
  type WritableSubmissionLifecycleEvent,
} from "./state-event";
import {
  latestLifecycleEventId,
  type DispatchOutbox,
  type SubmissionView,
} from "./submission-view";
import {
  PRODUCTION_RESULT_OWNER_STATE_CONTRACT_COMMIT,
  resultOwnerStateContractCommit,
} from "./result-owner";
import type { ComparatorEvidence, ResultAmendmentView } from "./result-amendment";

export type RuntimeEnv = Omit<
  CloudflareEnv,
  | "API_RATE_LIMITER"
  | "AUTH_TOKEN_SECRET"
  | "BROWSER_SUCCESS_URL"
  | "DEPLOYED_COMMIT"
  | "DEPLOYMENT_ENVIRONMENT"
  | "DISPATCH_REPOSITORY"
  | "DISPATCH_WORKFLOW"
  | "DISPATCH_WORKFLOW_REF"
  | "GITHUB_DISPATCH_TOKEN"
  | "GITHUB_BROKER"
  | "GITHUB_OAUTH_CLIENT_ID"
  | "GITHUB_OAUTH_CLIENT_SECRET"
  | "GITHUB_STATE_TOKEN"
  | "GITHUB_VERIFICATION_TOKEN"
  | "INTAKE_ENABLED"
  | "INTAKE_ENABLEMENT_MODE"
  | "INTAKE_LEASE_CONTROLLER_COMMIT"
  | "INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT"
  | "INTAKE_LEASE_CONTROLLER_RUN_ID"
  | "INTAKE_LEASE_EVENT_ID"
  | "INTAKE_LEASE_EXPIRES_AT"
  | "INTAKE_LEASE_ISSUED_AT"
  | "INTAKE_LEASE_NONCE_DIGEST"
  | "INTAKE_LEASE_STATE_COMMIT"
  | "INTAKE_LEASE_TARGET_COMMIT"
  | "LIFECYCLE_CALLBACK_TOKEN"
  | "LEGACY_RESULT_OWNER_API_ENABLED"
  | "RESULT_AMENDMENT_OWNER_API_ENABLED"
  | "RESULT_AMENDMENT_MAINTAINER_API_ENABLED"
  | "RESULT_AMENDMENT_MAINTAINERS"
  | "OAUTH_CALLBACK_URL"
  | "PROMOTION_CANARY_ENABLED"
  | "READINESS_TOKEN"
  | "RESULT_OWNER_STATE_CONTRACT_COMMIT"
  | "STATE_REPOSITORY"
> &
  Readonly<{
    API_RATE_LIMITER: RateLimit;
    AUTH_TOKEN_SECRET?: string;
    BROWSER_SUCCESS_URL?: string;
    DEPLOYED_COMMIT: string;
    DEPLOYMENT_ENVIRONMENT: "staging" | "production";
    DISPATCH_REPOSITORY?: string;
    DISPATCH_WORKFLOW?: string;
    DISPATCH_WORKFLOW_REF?: string;
    GITHUB_DISPATCH_TOKEN?: string;
    GITHUB_BROKER?: Fetcher;
    GITHUB_OAUTH_CLIENT_ID?: string;
    GITHUB_OAUTH_CLIENT_SECRET?: string;
    GITHUB_STATE_TOKEN?: string;
    GITHUB_VERIFICATION_TOKEN?: string;
    INTAKE_ENABLED: string;
    INTAKE_ENABLEMENT_MODE?: string;
    INTAKE_LEASE_CONTROLLER_COMMIT?: string;
    INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT?: string;
    INTAKE_LEASE_CONTROLLER_RUN_ID?: string;
    INTAKE_LEASE_EVENT_ID?: string;
    INTAKE_LEASE_EXPIRES_AT?: string;
    INTAKE_LEASE_ISSUED_AT?: string;
    INTAKE_LEASE_NONCE_DIGEST?: string;
    INTAKE_LEASE_STATE_COMMIT?: string;
    INTAKE_LEASE_TARGET_COMMIT?: string;
    LIFECYCLE_CALLBACK_TOKEN?: string;
    LEGACY_RESULT_OWNER_API_ENABLED?: string;
    RESULT_AMENDMENT_OWNER_API_ENABLED?: string;
    RESULT_AMENDMENT_MAINTAINER_API_ENABLED?: string;
    RESULT_AMENDMENT_MAINTAINERS?: string;
    OAUTH_CALLBACK_URL?: string;
    PROMOTION_CANARY_ENABLED?: string;
    READINESS_TOKEN?: string;
    RESULT_OWNER_STATE_CONTRACT_COMMIT?: string;
    STATE_REPOSITORY: string;
  }>;

type Lifecycle = Pick<ExecutionContext, "waitUntil">;
export type StateAccess = Readonly<{
  assertResultOwnerContract(): Promise<string>;
  readResultAmendmentForMaintainer(resultId: string): Promise<ResultAmendmentView>;
  appendEvent(event: WritableStateEvent): Promise<{ commit?: string; created: boolean }>;
  appendEventAtHead?(
    event: WritableStateEvent,
    expectedHead: string,
  ): Promise<{ commit: string; created: boolean }>;
  appendSubmissionLifecycle(
    events: readonly WritableSubmissionLifecycleEvent[],
    expectedLifecycleEventId: string,
    nextView: SubmissionView,
  ): Promise<{ created: boolean; view: SubmissionView }>;
  recordAcceptedResult(
    events: readonly WritableResultLifecycleEvent[],
    expectedLifecycleEventId: string,
    nextView: SubmissionView,
  ): Promise<{ created: boolean; view: SubmissionView }>;
  acceptSubmission(
    events: readonly WritableStateEvent[],
    view: SubmissionView,
    outbox: DispatchOutbox,
  ): Promise<{ created: boolean; view: SubmissionView }>;
  readSubmission(submissionId: string): Promise<SubmissionView | null>;
  appendSubmissionMutation(
    event: WritableStateEvent,
    expectedMutationEventId: string,
    nextView: SubmissionView,
  ): Promise<{ created: boolean; view: SubmissionView }>;
  updateDispatch(
    nextView: SubmissionView,
    expectedAttempts: number,
    nextOutbox: DispatchOutbox | null,
  ): Promise<{ view: SubmissionView }>;
  listDispatchOutbox(
    shard: string,
    scanOffset: number,
    scanLimit: number,
  ): Promise<readonly DispatchOutbox[]>;
  provePromotionCanaryContention?(event: WritableStateEvent): Promise<{
    proofRecorded: boolean;
    idempotent: boolean;
    created: boolean;
  }>;
  claimLegacyResult(request: LegacyResultClaimRequest): Promise<{
    created: boolean;
    resultId: string;
    authorityEventId: string;
  }>;
  backfillLegacyResultMetadata(request: LegacyResultBackfillRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
  }>;
  requestResultRetraction(request: ResultRetractionRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    retractionRevision: number;
  }>;
  decideResultRetraction(request: ResultRetractionDecisionRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    retractionRevision: number;
  }>;
  overrideResultRetraction(request: ResultRetractionOverrideRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    retractionRevision: number;
  }>;
  finalizeResultRetraction(request: ResultRetractionFinalizationRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    releaseDisposition: "not_published" | "removal_required" | "already_removed";
  }>;
  requestResultProblemRepair(request: ResultProblemRepairRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    repairRevision: number;
  }>;
  decideResultProblemRepair(request: ResultProblemRepairDecisionRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    repairRevision: number;
  }>;
}>;
export type ApiDependencies = Readonly<{
  now?: () => number;
  provider?: GitHubProvider;
  state?: StateAccess;
  stateFetch?: GitHubFetch;
  dispatch?: (request: Request) => Promise<void>;
  rateLimit?: (key: string) => Promise<Readonly<{ success: boolean }>>;
  scheduledSubrequestBudget?: ScheduledSubrequestBudget;
}>;

const JSON_HEADERS = {
  "cache-control": "no-store",
  "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
  "content-type": "application/json; charset=utf-8",
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
} as const;
const SESSION_COOKIE = "lean_eval_session";
const OAUTH_COOKIE = "lean_eval_oauth_state";
const PROMOTION_CANARY_REPOSITORY = "kim-em/lean-eval-intake-fixture";
const PROMOTION_CANARY_SOURCE_COMMIT = "ae38f4d3e4ad2991212135435f54e6640bcc89e7";
const PROMOTION_CANARY_LOGIN = "kim-em";
// Keep deterministic canary identities strictly after staging State's
// 2026-08-20T06:47:06Z system.initialized event.
const PROMOTION_CANARY_EPOCH_MS = Date.UTC(2026, 7, 21);
const PROMOTION_CANARY_OUTBOX_SHARD = "ca";
const PROMOTION_CANARY_MODEL = /^lean-eval automatic staging promotion canary commit ([0-9a-f]{40}) run ([1-9][0-9]{0,19}) attempt ([1-9][0-9]{0,5})$/;
const PROMOTION_CANARY_NOTES = "Synthetic staging-only promotion canary using a deliberately rejected fixture; never a benchmark or publication claim.";
const SCHEDULED_DISPATCH_ITEM_RESERVE = 10 + 1 + DISPATCH_UPDATE_MAX_SUBREQUESTS;

function json(body: unknown, status = 200, additionalHeaders?: HeadersInit): Response {
  const headers = new Headers(JSON_HEADERS);
  new Headers(additionalHeaders).forEach((value, key) => headers.set(key, value));
  return Response.json(body, { status, headers });
}

function currentIntake(env: RuntimeEnv, dependencies: ApiDependencies = {}): IntakeEnablement {
  return intakeEnablement(env, dependencies.now?.() ?? Date.now());
}

function resultOwnerApiEnabled(env: RuntimeEnv): boolean {
  return env.LEGACY_RESULT_OWNER_API_ENABLED === "true" &&
    env.RESULT_OWNER_STATE_CONTRACT_COMMIT ===
      resultOwnerStateContractCommit(env.DEPLOYMENT_ENVIRONMENT);
}

function resultAmendmentOwnerApiEnabled(env: RuntimeEnv): boolean {
  return env.RESULT_AMENDMENT_OWNER_API_ENABLED === "true" &&
    env.RESULT_OWNER_STATE_CONTRACT_COMMIT ===
      resultOwnerStateContractCommit(env.DEPLOYMENT_ENVIRONMENT);
}

function resultAmendmentMaintainerApiEnabled(env: RuntimeEnv): boolean {
  if (
    env.RESULT_AMENDMENT_MAINTAINER_API_ENABLED !== "true" ||
    env.RESULT_OWNER_STATE_CONTRACT_COMMIT !==
      resultOwnerStateContractCommit(env.DEPLOYMENT_ENVIRONMENT)
  ) return false;
  try {
    return decodeMaintainerIdentities(env.RESULT_AMENDMENT_MAINTAINERS).length > 0;
  } catch {
    return false;
  }
}

function requireResultAmendmentOwnerApi(env: RuntimeEnv): void {
  if (!resultAmendmentOwnerApiEnabled(env)) {
    throw new GitHubProviderError(503, "result amendment owner API is not configured");
  }
}

function requireResultAmendmentMaintainer(
  env: RuntimeEnv,
  authenticated: BrowserSession,
): MaintainerIdentity {
  if (!resultAmendmentMaintainerApiEnabled(env)) {
    throw new GitHubProviderError(503, "result amendment maintainer API is not configured");
  }
  try {
    return authenticateMaintainer(env.RESULT_AMENDMENT_MAINTAINERS, authenticated);
  } catch (error) {
    if (error instanceof AuthError) {
      throw new ResultOwnerStateError(404, "result amendment operation was not found");
    }
    throw error;
  }
}

function requireResultOwnerApi(env: RuntimeEnv): void {
  if (!resultOwnerApiEnabled(env)) {
    throw new GitHubProviderError(503, "legacy result owner API is not configured");
  }
}

async function equalSecret(actual: string, expected: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [actualDigest, expectedDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(actual)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  return crypto.subtle.timingSafeEqual(actualDigest, expectedDigest);
}

async function readinessAuthorized(request: Request, env: RuntimeEnv): Promise<boolean> {
  if (!env.READINESS_TOKEN) return false;
  const header = request.headers.get("authorization");
  if (!header?.startsWith("Bearer ")) return false;
  return equalSecret(header.slice("Bearer ".length), env.READINESS_TOKEN);
}

function readinessCacheKey(env: RuntimeEnv): Request {
  return new Request(`https://readiness.invalid/${env.DEPLOYMENT_ENVIRONMENT}`);
}

async function cachedReadiness(env: RuntimeEnv): Promise<Response | null> {
  try {
    const cached = await caches.default.match(readinessCacheKey(env));
    return cached ? new Response(cached.body, { status: cached.status, headers: JSON_HEADERS }) : null;
  } catch {
    return null;
  }
}

function cacheReadiness(env: RuntimeEnv, response: Response, lifecycle: Lifecycle): void {
  const cached = response.clone();
  cached.headers.set("cache-control", "public, max-age=15");
  lifecycle.waitUntil(caches.default.put(readinessCacheKey(env), cached).catch(() => undefined));
}

const timedGitHubFetch: GitHubFetch = (input, init) =>
  fetch(input, { ...init, signal: init?.signal ?? AbortSignal.timeout(5000) });

function stateRepository(
  env: RuntimeEnv,
  githubFetch: GitHubFetch = timedGitHubFetch,
): GitHubStateRepository {
  if (!env.GITHUB_STATE_TOKEN) throw new GitHubStateError(503, "State credential missing");
  return new GitHubStateRepository(
    { repository: env.STATE_REPOSITORY, token: env.GITHUB_STATE_TOKEN, userAgent: "lean-eval-submission-worker" },
    githubFetch,
  );
}

async function readiness(
  request: Request,
  env: RuntimeEnv,
  lifecycle: Lifecycle,
  dependencies: ApiDependencies,
): Promise<Response> {
  if (!(await readinessAuthorized(request, env))) return json({ error: "not_found" }, 404);
  const verifyWrite = request.method === "POST";
  const intake = currentIntake(env, dependencies);
  if (!verifyWrite && !intake.effective) {
    return json({ status: "not_ready", reason: "intake_disabled", environment: env.DEPLOYMENT_ENVIRONMENT }, 503);
  }
  if (!env.GITHUB_STATE_TOKEN) return json({ status: "not_ready", reason: "state_credential_missing" }, 503);
  if (!verifyWrite) {
    const cached = await cachedReadiness(env);
    if (cached) return cached;
  }
  try {
    const repository = stateRepository(env);
    if (verifyWrite) {
      const production = env.DEPLOYMENT_ENVIRONMENT === "production";
      const stateCommit = production
        ? await repository.assertProductionQualifiedWritable()
        : await repository.assertWritable();
      const response: Record<string, unknown> = {
        status: "state_writer_ready",
        environment: env.DEPLOYMENT_ENVIRONMENT,
        intake_configured_enabled: intake.configured,
        intake_effective_enabled: intake.effective,
        intake_enabled: intake.effective,
        intake_enablement_mode: intake.mode,
        intake_lease_expires_at: intake.leaseExpiresAt,
        state_commit: stateCommit,
      };
      if (production) {
        response.state_branch_protected = true;
        response.state_contract_commit = PRODUCTION_RESULT_OWNER_STATE_CONTRACT_COMMIT;
        response.state_contract_verified = true;
        response.state_event_schema_sha256 =
          "af753eb3aba7a82c6c5d7b153ea0a0e411df9aa94768772aa8b99d985b6d57cb";
      }
      return json(response);
    }
    await repository.assertAvailable();
    const response = json({ status: "ready", environment: env.DEPLOYMENT_ENVIRONMENT });
    cacheReadiness(env, response, lifecycle);
    return response;
  } catch (error) {
    console.error(JSON.stringify({
      event: "state_readiness_failed",
      environment: env.DEPLOYMENT_ENVIRONMENT,
      error_name: error instanceof Error ? error.name : "unknown",
      upstream_status: error instanceof GitHubStateError ? error.status : null,
    }));
    const response = json({ status: "not_ready", reason: "state_unavailable" }, 503);
    cacheReadiness(env, response, lifecycle);
    return response;
  }
}

function cookie(request: Request, name: string): string | null {
  for (const part of request.headers.get("cookie")?.split(";") ?? []) {
    const [key, ...value] = part.trim().split("=");
    if (key === name) return value.join("=");
  }
  return null;
}

function setCookie(name: string, value: string, maxAge: number, sameSite: "Lax" | "Strict"): string {
  return `${name}=${value}; Path=/; Max-Age=${String(maxAge)}; HttpOnly; Secure; SameSite=${sameSite}`;
}

async function rateLimitKey(request: Request): Promise<string> {
  const url = new URL(request.url);
  const credential = request.headers.get("authorization") ??
    cookie(request, SESSION_COOKIE) ??
    cookie(request, OAUTH_COOKIE) ??
    url.searchParams.get("state");
  const actorMaterial = credential === null
    ? [
        request.headers.get("cf-connecting-ip") ?? "no-ip",
        request.headers.get("user-agent") ?? "no-user-agent",
        request.headers.get("accept-language") ?? "no-language",
      ].join("\u0000")
    : `credential\u0000${credential}`;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(actorMaterial));
  const actor = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${request.method}:${url.pathname}:${actor}`;
}

async function rateLimit(
  request: Request,
  env: RuntimeEnv,
  dependencies: ApiDependencies,
): Promise<boolean> {
  try {
    const key = await rateLimitKey(request);
    const result = dependencies.rateLimit === undefined
      ? await env.API_RATE_LIMITER.limit({ key })
      : await dependencies.rateLimit(key);
    return result.success;
  } catch (error) {
    console.error(JSON.stringify({
      event: "api_rate_limit_failed_closed",
      error_name: error instanceof Error ? error.name : "unknown",
    }));
    return false;
  }
}

function configuredSecret(env: RuntimeEnv): string {
  if (!env.AUTH_TOKEN_SECRET) throw new AuthError("authentication is not configured");
  return env.AUTH_TOKEN_SECRET;
}

function nowSeconds(dependencies: ApiDependencies): number {
  return Math.floor((dependencies.now?.() ?? Date.now()) / 1000);
}

function provider(env: RuntimeEnv, dependencies: ApiDependencies): GitHubProvider {
  if (dependencies.provider) return dependencies.provider;
  return new GitHubProvider(
    undefined,
    env.GITHUB_VERIFICATION_TOKEN,
    env.GITHUB_BROKER ? githubBrokerFetch(env.GITHUB_BROKER, "source") : undefined,
    env.GITHUB_BROKER ? githubBrokerFetch(env.GITHUB_BROKER, "dispatch") : undefined,
    env.GITHUB_BROKER ? githubBrokerFetch(env.GITHUB_BROKER, "results") : undefined,
    env.DEPLOYMENT_ENVIRONMENT === "production" ? "main" : "staging-results",
    env.GITHUB_BROKER ? githubBrokerFetch(env.GITHUB_BROKER, "benchmark") : undefined,
  );
}

function state(
  env: RuntimeEnv,
  dependencies: ApiDependencies,
  githubFetch?: GitHubFetch,
): StateAccess {
  return dependencies.state ?? stateRepository(env, githubFetch);
}

async function submissionStage<T>(stage: string, operation: () => Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    console.error(JSON.stringify({
      event: "submission_stage_failed",
      stage,
      error_name: error instanceof Error ? error.name : "unknown",
    }));
    throw error;
  }
}

async function session(request: Request, env: RuntimeEnv, dependencies: ApiDependencies): Promise<BrowserSession> {
  const authorization = request.headers.get("authorization");
  const token = authorization?.startsWith("Bearer ") ? authorization.slice("Bearer ".length) : cookie(request, SESSION_COOKIE);
  if (!token) throw new AuthError("authentication required");
  if (!authorization && !new Set(["GET", "HEAD"]).has(request.method)) {
    if (request.headers.get("origin") !== new URL(request.url).origin) throw new AuthError("same-origin request required");
  }
  return verifyToken<BrowserSession>(configuredSecret(env), token, "browser_session", nowSeconds(dependencies));
}

function canonicalTimestamp(seconds: number): string {
  return new Date(seconds * 1000).toISOString();
}

function canonicalMilliseconds(milliseconds: number): string {
  return new Date(milliseconds).toISOString();
}

type PromotionCanaryRequest = Readonly<{
  schema_version: 2;
  deployed_commit: string;
  dispatch_ref: string;
  controller_run_id: string;
  controller_run_attempt: string;
}>;

type PromotionCanaryIdentity = Readonly<{
  commit: string;
  runId: string;
  runAttempt: string;
}>;

type PromotionCanaryMaterial = Readonly<{
  acceptedAtMilliseconds: number;
  evidenceEvent: WritableStateEvent;
  grant: SubmissionGrant;
  input: SubmissionInput;
}>;

function decodePromotionCanaryRequest(value: unknown): PromotionCanaryRequest {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiDecodeError("promotion canary request must be an object");
  }
  const request = value as Record<string, unknown>;
  if (
    Object.keys(request).sort().join(",") !==
      "controller_run_attempt,controller_run_id,deployed_commit,dispatch_ref,schema_version" ||
    request.schema_version !== 2 ||
    typeof request.deployed_commit !== "string" ||
    !/^[0-9a-f]{40}$/.test(request.deployed_commit) ||
    typeof request.dispatch_ref !== "string" ||
    !/^lean-eval-dispatch\/[0-9a-f]{40}$/.test(request.dispatch_ref) ||
    typeof request.controller_run_id !== "string" ||
    !/^[1-9][0-9]{0,19}$/.test(request.controller_run_id) ||
    typeof request.controller_run_attempt !== "string" ||
    !/^[1-9][0-9]{0,5}$/.test(request.controller_run_attempt)
  ) {
    throw new ApiDecodeError("promotion canary request is not canonical");
  }
  return request as PromotionCanaryRequest;
}

async function canaryDigest(
  commit: string,
  identity: PromotionCanaryIdentity,
  label: string,
): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(
      `lean-eval-promotion-canary-v2\0${commit}\0${identity.runId}\0${identity.runAttempt}\0${label}`,
    ),
  ));
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

function uuidV7FromDigest(
  digest: Uint8Array,
  occurredAtMilliseconds: number,
  fixedOutboxShard = false,
): string {
  if (digest.length !== 32) throw new TypeError("promotion canary digest must be SHA-256");
  const bytes = digest.slice(0, 16);
  let timestamp = BigInt(occurredAtMilliseconds);
  for (let index = 5; index >= 0; index -= 1) {
    bytes[index] = Number(timestamp & 0xffn);
    timestamp >>= 8n;
  }
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x70;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  if (fixedOutboxShard) bytes[15] = 0xca;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function promotionCanaryMaterial(
  identity: PromotionCanaryIdentity,
): Promise<PromotionCanaryMaterial> {
  const commit = identity.commit;
  const [submissionDigest, nonceDigestBytes, metadataDigest, evidenceDigest, timeDigest] = await Promise.all([
    canaryDigest(commit, identity, "submission"),
    canaryDigest(commit, identity, "nonce"),
    canaryDigest(commit, identity, "metadata"),
    canaryDigest(commit, identity, "cas-evidence"),
    canaryDigest(commit, identity, "accepted-at"),
  ]);
  const offset = (
    ((timeDigest[0] ?? 0) * 0x1000000) +
    ((timeDigest[1] ?? 0) * 0x10000) +
    ((timeDigest[2] ?? 0) * 0x100) +
    (timeDigest[3] ?? 0)
  ) % (24 * 60 * 60 * 1000 - 10);
  const acceptedAtMilliseconds = PROMOTION_CANARY_EPOCH_MS + offset;
  const issuedAt = Math.floor(acceptedAtMilliseconds / 1000);
  const nonce = base64Url(nonceDigestBytes);
  const evidenceNonce = base64Url(evidenceDigest);
  const grant: SubmissionGrant = {
    kind: "submission_grant",
    login: PROMOTION_CANARY_LOGIN,
    submission_id: uuidV7FromDigest(submissionDigest, acceptedAtMilliseconds + 1, true),
    nonce,
    nonce_event_id: uuidV7FromDigest(
      await canaryDigest(commit, identity, "nonce-event"),
      acceptedAtMilliseconds,
    ),
    metadata_event_id: uuidV7FromDigest(metadataDigest, acceptedAtMilliseconds + 2),
    issued_at: issuedAt,
    expires_at: issuedAt + 600,
  };
  const input: SubmissionInput = {
    problem_id: "two_plus_two",
    problem_group: "formalization-evaluation",
    statement_revision: 1,
    declared_model: `lean-eval automatic staging promotion canary commit ${identity.commit} run ${identity.runId} attempt ${identity.runAttempt}`,
    source_repository: PROMOTION_CANARY_REPOSITORY,
    source_commit: PROMOTION_CANARY_SOURCE_COMMIT,
    source_visibility: "private",
    publication_choice: "withheld",
    production_metadata: {
      notes: PROMOTION_CANARY_NOTES,
      web_access: false,
      billing_mode: "unknown",
    },
  };
  const evidenceEvent = await nonceEvent(
    uuidV7FromDigest(evidenceDigest, acceptedAtMilliseconds + 4),
    evidenceNonce,
    "submission",
    acceptedAtMilliseconds + 4,
    issuedAt + 600,
  );
  return { acceptedAtMilliseconds, evidenceEvent, grant, input };
}

async function nonceEvent(
  eventId: string,
  nonce: string,
  purpose: "agent" | "intake_lease" | "oauth" | "submission",
  occurredAtMilliseconds: number,
  expiresAt: number,
): Promise<WritableStateEvent> {
  return {
    schema_version: 1,
    event_id: eventId,
    event_type: "authentication.nonce_consumed",
    occurred_at: canonicalMilliseconds(occurredAtMilliseconds),
    subject_id: eventId,
    causation_event_id: null,
    actor: { kind: "system" },
    payload: {
      nonce_digest: await nonceDigest(purpose, nonce),
      purpose,
      expires_at: canonicalTimestamp(expiresAt),
    },
  };
}

type IntakeLeaseSmoke = Readonly<{
  controller_commit: string;
  controller_run_attempt: string;
  controller_run_id: string;
  environment: "production";
  event_id: string;
  expires_at: number;
  issued_at: number;
  nonce: string;
  schema_version: 1;
  state_commit: string;
  target_commit: string;
}>;

function decodeIntakeLeaseSmoke(value: unknown): IntakeLeaseSmoke {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiDecodeError("intake lease smoke body must be an object");
  }
  const body = value as Record<string, unknown>;
  const fields = [
    "controller_commit", "controller_run_attempt", "controller_run_id", "environment",
    "event_id", "expires_at", "issued_at", "nonce", "schema_version", "state_commit",
    "target_commit",
  ];
  if (Object.keys(body).sort().join("\0") !== [...fields].sort().join("\0")) {
    throw new ApiDecodeError("intake lease smoke body fields are not exact");
  }
  if (
    body.schema_version !== 1 || body.environment !== "production" ||
    typeof body.controller_commit !== "string" || typeof body.controller_run_attempt !== "string" ||
    typeof body.controller_run_id !== "string" || typeof body.event_id !== "string" ||
    typeof body.expires_at !== "number" || typeof body.issued_at !== "number" ||
    typeof body.nonce !== "string" || typeof body.state_commit !== "string" ||
    typeof body.target_commit !== "string"
  ) {
    throw new ApiDecodeError("intake lease smoke body types are invalid");
  }
  return body as IntakeLeaseSmoke;
}

async function intakeLeaseSmoke(
  request: Request,
  env: RuntimeEnv,
  dependencies: ApiDependencies,
): Promise<Response> {
  if (!(await readinessAuthorized(request, env))) return json({ error: "not_found" }, 404);
  const intake = currentIntake(env, dependencies);
  if (env.DEPLOYMENT_ENVIRONMENT !== "production" || intake.mode !== "leased" || !intake.effective) {
    return json({ error: "lease_not_effective" }, 409);
  }
  const body = decodeIntakeLeaseSmoke(await readJson(request));
  const exact =
    body.controller_commit === env.INTAKE_LEASE_CONTROLLER_COMMIT &&
    body.controller_run_attempt === env.INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT &&
    body.controller_run_id === env.INTAKE_LEASE_CONTROLLER_RUN_ID &&
    body.event_id === env.INTAKE_LEASE_EVENT_ID &&
    String(body.expires_at) === env.INTAKE_LEASE_EXPIRES_AT &&
    String(body.issued_at) === env.INTAKE_LEASE_ISSUED_AT &&
    body.state_commit === env.INTAKE_LEASE_STATE_COMMIT &&
    body.target_commit === env.INTAKE_LEASE_TARGET_COMMIT;
  const digest = await nonceDigest("intake_lease", body.nonce);
  if (!exact || !env.INTAKE_LEASE_NONCE_DIGEST || !(await equalSecret(digest, env.INTAKE_LEASE_NONCE_DIGEST))) {
    return json({ error: "lease_binding_mismatch" }, 409);
  }
  const ledger = state(env, dependencies);
  if (!ledger.appendEventAtHead) throw new GitHubStateError(503, "bound State append unavailable");
  const event = await nonceEvent(
    body.event_id,
    body.nonce,
    "intake_lease",
    body.issued_at * 1000,
    body.expires_at,
  );
  if (!currentIntake(env, dependencies).effective) {
    return json({ error: "lease_not_effective" }, 409);
  }
  const outcome = await ledger.appendEventAtHead(event, body.state_commit);
  return json({
    status: outcome.created ? "lease_smoke_consumed" : "lease_smoke_already_consumed",
    environment: "production",
    intake_configured_enabled: true,
    intake_effective_enabled: true,
    intake_enablement_mode: "leased",
    intake_lease_expires_at: body.expires_at,
    state_commit: outcome.commit,
  });
}

function receivedEvent(submissionId: string, login: string, input: SubmissionInput, occurredAtMilliseconds: number): WritableStateEvent {
  return {
    schema_version: 1,
    event_id: submissionId,
    event_type: "submission.received",
    occurred_at: canonicalMilliseconds(occurredAtMilliseconds),
    subject_id: submissionId,
    causation_event_id: null,
    actor: { kind: "github", login },
    payload: {
      problem_id: input.problem_id,
      statement_revision: input.statement_revision,
      declared_model: input.declared_model,
      source_repository: input.source_repository,
      source_commit: input.source_commit,
      source_visibility: input.source_visibility,
      publication_choice: input.publication_choice,
    },
  };
}

function metadataEvent(
  eventId: string,
  submissionId: string,
  login: string,
  metadata: ProductionMetadata,
  occurredAtMilliseconds: number,
): WritableStateEvent {
  return {
    schema_version: 1,
    event_id: eventId,
    event_type: "submission.metadata_amended",
    occurred_at: canonicalMilliseconds(occurredAtMilliseconds),
    subject_id: submissionId,
    causation_event_id: submissionId,
    actor: { kind: "github", login },
    payload: { production_metadata: metadata },
  };
}

function requireDispatchConfiguration(env: RuntimeEnv, dependencies: ApiDependencies): void {
  if (!/^lean-eval-dispatch\/[0-9a-f]{40}$/.test(env.DISPATCH_WORKFLOW_REF ?? "")) {
    throw new GitHubProviderError(503, "immutable dispatch workflow tag is not configured");
  }
  if (!dependencies.dispatch && !env.GITHUB_DISPATCH_TOKEN && !env.GITHUB_BROKER) {
    throw new GitHubProviderError(503, "exact-ref dispatch is not configured");
  }
}

async function dispatchSubmission(
  env: RuntimeEnv,
  dependencies: ApiDependencies,
  submissionId: string,
  login: string,
  input: SubmissionInput,
  workflowRef: string,
  target: "submission" | "promotion_canary" = "submission",
): Promise<void> {
  const identity = promotionCanaryIdentity(input);
  const request = target === "promotion_canary"
    ? buildPromotionCanaryDispatchRequest(
      env.DISPATCH_REPOSITORY ?? "leanprover/lean-eval-submissions",
      workflowRef,
      submissionId,
      identity?.runId ?? "",
      identity?.runAttempt ?? "",
    )
    : buildDispatchRequest(
      env.DISPATCH_REPOSITORY ?? "leanprover/lean-eval-submissions",
      env.DISPATCH_WORKFLOW ?? "submission.yml",
      workflowRef,
      submissionId,
      login,
      env.DEPLOYMENT_ENVIRONMENT,
      input,
    );
  if (dependencies.dispatch) return dependencies.dispatch(request);
  await provider(env, dependencies).dispatch(env.GITHUB_DISPATCH_TOKEN ?? "", request);
}

function initialSubmissionView(
  grant: SubmissionGrant | AgentChallenge,
  login: string,
  input: SubmissionInput,
  acceptedAtMilliseconds: number,
  workflowRef: string,
): SubmissionView {
  const acceptedAt = canonicalMilliseconds(acceptedAtMilliseconds);
  return {
    schema_version: 2,
    submission_id: grant.submission_id,
    owner_login: login,
    received_event_id: grant.submission_id,
    mutation_event_id: grant.metadata_event_id,
    metadata_event_id: grant.metadata_event_id,
    publication_event_id: null,
    accepted_at: acceptedAt,
    submission: input,
    production_metadata: input.production_metadata,
    publication_choice: input.publication_choice,
    archive: { status: "pending" },
    evaluation: { status: "pending" },
    result_id: null,
    result_event_id: null,
    dispatch: {
      status: "pending",
      attempts: 0,
      requested_at: acceptedAt,
      updated_at: acceptedAt,
      workflow_ref: workflowRef,
      last_error_code: null,
    },
  };
}

function initialDispatchOutbox(view: SubmissionView): DispatchOutbox {
  return {
    schema_version: 1,
    submission_id: view.submission_id,
    owner_login: view.owner_login,
    submission: view.submission,
    attempts: 0,
    next_attempt_at: view.accepted_at,
    workflow_ref: view.dispatch.workflow_ref,
  };
}

function dispatchFailureCode(error: unknown): string {
  if (error instanceof GitHubProviderError && error.status === 401) return "dispatch_credential_rejected";
  if (error instanceof GitHubProviderError && error.status === 404) return "dispatch_workflow_not_found";
  if (error instanceof GitHubProviderError && error.status === 409) return "dispatch_ref_conflict";
  return "dispatch_provider_unavailable";
}

function nextDispatchTime(nowMilliseconds: number, attempts: number): string {
  const delay = Math.min(5 * 60_000 * (2 ** Math.min(attempts, 6)), 6 * 60 * 60_000);
  return canonicalMilliseconds(nowMilliseconds + delay);
}

async function reconcileDispatch(
  env: RuntimeEnv,
  dependencies: ApiDependencies,
  ledger: StateAccess,
  view: SubmissionView,
  nowMilliseconds: number,
  target: "submission" | "promotion_canary" = "submission",
  subrequests?: ScheduledSubrequestBudget,
): Promise<SubmissionView> {
  if (view.dispatch.status === "succeeded") return view;
  const attempt = view.dispatch.attempts + 1;
  if (attempt > 32) return view;
  subrequests?.requireRemaining(DISPATCH_UPDATE_MAX_SUBREQUESTS + 1);
  subrequests?.take();
  try {
    await dispatchSubmission(
      env,
      dependencies,
      view.submission_id,
      view.owner_login,
      view.submission,
      view.dispatch.workflow_ref,
      target,
    );
  } catch (error) {
    const failed: SubmissionView = {
      ...view,
      dispatch: {
        ...view.dispatch,
        status: "failed",
        attempts: attempt,
        updated_at: canonicalMilliseconds(nowMilliseconds),
        last_error_code: dispatchFailureCode(error),
      },
    };
    const outbox: DispatchOutbox = {
      schema_version: 1,
      submission_id: view.submission_id,
      owner_login: view.owner_login,
      submission: view.submission,
      attempts: attempt,
      next_attempt_at: nextDispatchTime(nowMilliseconds, attempt),
      workflow_ref: view.dispatch.workflow_ref,
    };
    return (await ledger.updateDispatch(failed, view.dispatch.attempts, outbox)).view;
  }
  const succeeded: SubmissionView = {
    ...view,
    dispatch: {
      ...view.dispatch,
      status: "succeeded",
      attempts: attempt,
      updated_at: canonicalMilliseconds(nowMilliseconds),
      last_error_code: null,
    },
  };
  return (await ledger.updateDispatch(succeeded, view.dispatch.attempts, null)).view;
}

async function acceptSubmission(
  env: RuntimeEnv,
  dependencies: ApiDependencies,
  identity: GitHubIdentity,
  grant: SubmissionGrant | AgentChallenge,
  input: SubmissionInput,
  purpose: "agent" | "submission",
): Promise<Response> {
  requireDispatchConfiguration(env, dependencies);
  if (grant.login !== identity.login) throw new AuthError("authenticated identity does not match grant");
  const repository = await submissionStage(
    "source_repository_verification",
    () => provider(env, dependencies).repository(input.source_repository),
  );
  assertSourcePolicy(input.problem_group, input.source_visibility, repository.private);
  const acceptedAtMilliseconds = dependencies.now?.() ?? Date.now();
  const workflowRef = env.DISPATCH_WORKFLOW_REF ?? "";
  const consumedNonce = await submissionStage(
    "state_event_construction",
    () => nonceEvent(grant.nonce_event_id, grant.nonce, purpose, acceptedAtMilliseconds, grant.expires_at),
  );
  const events: WritableStateEvent[] = [
    consumedNonce,
    receivedEvent(grant.submission_id, identity.login, input, acceptedAtMilliseconds + 1),
    metadataEvent(grant.metadata_event_id, grant.submission_id, identity.login, input.production_metadata, acceptedAtMilliseconds + 2),
  ];
  const ledger = state(env, dependencies);
  const proposedView = initialSubmissionView(grant, identity.login, input, acceptedAtMilliseconds, workflowRef);
  if (!currentIntake(env, dependencies).effective) {
    throw new GitHubStateError(503, "intake lease expired before State acceptance");
  }
  const outcome = await submissionStage(
    "state_acceptance",
    () => ledger.acceptSubmission(events, proposedView, initialDispatchOutbox(proposedView)),
  );
  const reconciled = await submissionStage(
    "dispatch_reconciliation",
    () => reconcileDispatch(env, dependencies, ledger, outcome.view, acceptedAtMilliseconds + 3),
  );
  return json(
    {
      submission_id: grant.submission_id,
      status: outcome.created ? "queued" : "already_received",
      dispatch_status: reconciled.dispatch.status,
    },
    reconciled.dispatch.status === "succeeded" && !outcome.created ? 200 : 202,
    { location: `/api/v1/submissions/${grant.submission_id}` },
  );
}

function promotionCanaryEnabled(env: RuntimeEnv): boolean {
  return env.DEPLOYMENT_ENVIRONMENT === "staging" && env.PROMOTION_CANARY_ENABLED === "true";
}

function requirePromotionCanaryConfiguration(env: RuntimeEnv, dependencies: ApiDependencies): void {
  if (!promotionCanaryEnabled(env)) {
    throw new GitHubStateError(503, "promotion canary is not enabled for this staging Worker");
  }
  if (env.STATE_REPOSITORY !== "leanprover/lean-eval-state-staging") {
    throw new GitHubStateError(503, "promotion canary is not bound to staging State");
  }
  if (env.DISPATCH_REPOSITORY !== "leanprover/lean-eval-submissions") {
    throw new GitHubProviderError(503, "promotion canary dispatch repository is not canonical");
  }
  if (!/^[0-9a-f]{40}$/.test(env.DEPLOYED_COMMIT)) {
    throw new GitHubStateError(503, "promotion canary is not bound to an exact deployed commit");
  }
  if (env.DISPATCH_WORKFLOW_REF !== `lean-eval-dispatch/${env.DEPLOYED_COMMIT}`) {
    throw new GitHubProviderError(503, "promotion canary dispatch ref does not bind the deployed commit");
  }
  requireDispatchConfiguration(env, dependencies);
}

function assertPromotionCanaryView(expected: SubmissionView, actual: SubmissionView): void {
  if (
    actual.schema_version !== 2 ||
    expected.schema_version !== 2 ||
    actual.submission_id !== expected.submission_id ||
    actual.owner_login !== expected.owner_login ||
    actual.received_event_id !== expected.received_event_id ||
    actual.mutation_event_id !== expected.mutation_event_id ||
    actual.metadata_event_id !== expected.metadata_event_id ||
    actual.publication_event_id !== null ||
    actual.accepted_at !== expected.accepted_at ||
    JSON.stringify(actual.submission) !== JSON.stringify(expected.submission) ||
    JSON.stringify(actual.production_metadata) !== JSON.stringify(expected.production_metadata) ||
    actual.publication_choice !== "withheld" ||
    actual.archive.status !== "pending" ||
    actual.evaluation.status !== "pending" ||
    actual.result_id !== null ||
    actual.result_event_id !== null ||
    actual.dispatch.workflow_ref !== expected.dispatch.workflow_ref ||
    actual.dispatch.requested_at !== expected.dispatch.requested_at ||
    (actual.dispatch.status === "pending" &&
      (actual.dispatch.attempts !== 0 || actual.dispatch.last_error_code !== null)) ||
    (actual.dispatch.status === "failed" &&
      (actual.dispatch.attempts < 1 || actual.dispatch.last_error_code === null)) ||
    (actual.dispatch.status === "succeeded" &&
      (actual.dispatch.attempts < 1 || actual.dispatch.last_error_code !== null))
  ) {
    throw new GitHubStateError(502, "promotion canary State view does not match the exact synthetic intake");
  }
}

async function promotionCanary(
  request: Request,
  env: RuntimeEnv,
  dependencies: ApiDependencies,
): Promise<Response> {
  if (!(await readinessAuthorized(request, env))) return json({ error: "not_found" }, 404);
  requirePromotionCanaryConfiguration(env, dependencies);
  if (currentIntake(env, dependencies).effective) {
    throw new GitHubStateError(503, "promotion canary requires ordinary intake to remain disabled");
  }
  const canaryRequest = decodePromotionCanaryRequest(await readJson(request));
  if (
    canaryRequest.deployed_commit !== env.DEPLOYED_COMMIT ||
    canaryRequest.dispatch_ref !== env.DISPATCH_WORKFLOW_REF
  ) {
    throw new ApiDecodeError("promotion canary request does not bind this exact deployment");
  }

  const identity = {
    commit: canaryRequest.deployed_commit,
    runId: canaryRequest.controller_run_id,
    runAttempt: canaryRequest.controller_run_attempt,
  } satisfies PromotionCanaryIdentity;
  const material = await promotionCanaryMaterial(identity);
  const repository = await submissionStage(
    "promotion_canary_github_connectivity",
    () => provider(env, dependencies).repository(PROMOTION_CANARY_REPOSITORY),
  );
  if (!repository.private || repository.fullName.toLowerCase() !== PROMOTION_CANARY_REPOSITORY.toLowerCase()) {
    throw new GitHubProviderError(409, "promotion canary fixture repository identity or visibility changed");
  }
  assertSourcePolicy(material.input.problem_group, material.input.source_visibility, repository.private);

  const ledger = state(env, dependencies);
  const consumedNonce = await nonceEvent(
    material.grant.nonce_event_id,
    material.grant.nonce,
    "submission",
    material.acceptedAtMilliseconds,
    material.grant.expires_at,
  );
  const events: WritableStateEvent[] = [
    consumedNonce,
    receivedEvent(
      material.grant.submission_id,
      PROMOTION_CANARY_LOGIN,
      material.input,
      material.acceptedAtMilliseconds + 1,
    ),
    metadataEvent(
      material.grant.metadata_event_id,
      material.grant.submission_id,
      PROMOTION_CANARY_LOGIN,
      material.input.production_metadata,
      material.acceptedAtMilliseconds + 2,
    ),
  ];
  const proposedView = initialSubmissionView(
    material.grant,
    PROMOTION_CANARY_LOGIN,
    material.input,
    material.acceptedAtMilliseconds,
    env.DISPATCH_WORKFLOW_REF ?? "",
  );
  const outcome = await submissionStage(
    "promotion_canary_state_acceptance",
    () => ledger.acceptSubmission(events, proposedView, initialDispatchOutbox(proposedView)),
  );
  assertPromotionCanaryView(proposedView, outcome.view);
  const proveContention = ledger.provePromotionCanaryContention?.bind(ledger);
  if (proveContention === undefined) {
    throw new GitHubStateError(503, "State adapter does not implement the promotion canary CAS proof");
  }
  const contention = await submissionStage(
    "promotion_canary_cas_contention",
    () => proveContention(material.evidenceEvent),
  );
  if (
    !contention.proofRecorded ||
    contention.created === contention.idempotent
  ) {
    throw new GitHubStateError(502, "promotion canary CAS adapter did not prove collision and retry");
  }
  const current = await ledger.readSubmission(material.grant.submission_id);
  if (current === null) throw new GitHubStateError(502, "promotion canary submission disappeared from State");
  assertPromotionCanaryView(proposedView, current);
  const complete = current.dispatch.status === "succeeded";
  const failed = current.dispatch.status === "failed";
  return json({
    status: complete ? "passed" : failed ? "dispatch_failed" : "awaiting_scheduled_reconciliation",
    environment: "staging",
    deployed_commit: env.DEPLOYED_COMMIT,
    dispatch_ref: env.DISPATCH_WORKFLOW_REF,
    controller_run_id: identity.runId,
    controller_run_attempt: identity.runAttempt,
    submission_id: material.grant.submission_id,
    github_connectivity: "verified",
    synthetic_intake: outcome.created ? "created" : "idempotent",
    cas_contention: contention.idempotent
      ? "idempotent_prior_collision_and_retry_proof"
      : "collision_observed_and_retry_applied",
    dispatch_state: current.dispatch.status,
    workflow_dispatch: complete ? "accepted_by_github" : failed ? "retry_pending" : "pending",
    scheduled_reconciliation: complete ? "completed" : failed ? "retry_pending" : "pending",
  }, complete ? 200 : 202);
}

async function archiveCompleted(
  request: Request,
  env: RuntimeEnv,
  dependencies: ApiDependencies,
): Promise<Response> {
  const configured = env.LIFECYCLE_CALLBACK_TOKEN;
  const authorization = request.headers.get("authorization");
  const supplied = authorization?.startsWith("Bearer ")
    ? authorization.slice("Bearer ".length)
    : "";
  if (!configured) return json({ error: "callback_unavailable" }, 503);
  if (!supplied || !(await equalToken(supplied, configured))) {
    return json({ error: "authentication_failed" }, 401);
  }
  const completion = decodeArchiveCompletion(await readJson(request));
  if (completion.locator.archive_repository !== "leanprover/lean-eval-audit") {
    throw new ApiDecodeError("archive completion named an unapproved repository");
  }
  const ledger = state(env, dependencies);
  const view = await ledger.readSubmission(completion.locator.submission_id);
  if (view === null) return json({ error: "submission_not_found" }, 409);
  if (view.dispatch.status !== "succeeded") {
    return json({ error: "submission_not_dispatched" }, 409);
  }
  if (Date.parse(completion.occurred_at) <= Date.parse(view.accepted_at)) {
    throw new ApiDecodeError("archive completion must follow submission acceptance");
  }
  const eventId = await lifecycleEventId(
    "archive.completed",
    view.submission_id,
    completion.occurred_at,
  );
  if (view.archive.status === "completed" && view.archive.event_id === eventId) {
    if (
      view.archive.occurred_at !== completion.occurred_at ||
      view.archive.archive_repository !== completion.locator.archive_repository ||
      view.archive.archive_commit !== completion.locator.archive_commit ||
      view.archive.archive_path !== completion.locator.archive_path ||
      view.archive.archive_ciphertext_sha256 !== completion.locator.archive_ciphertext_sha256
    ) {
      throw new StateEventConflictError(`archive completion ${eventId}`);
    }
    return json({
      status: "already_recorded",
      submission_id: view.submission_id,
      event_id: eventId,
    });
  }
  const expectedLifecycleEventId = latestLifecycleEventId(view);
  const event: ArchiveCompletedEvent = {
    schema_version: 1,
    event_id: eventId,
    event_type: "archive.completed",
    occurred_at: completion.occurred_at,
    subject_id: view.submission_id,
    causation_event_id: expectedLifecycleEventId,
    actor: { kind: "system" },
    payload: {
      archive_repository: completion.locator.archive_repository,
      archive_commit: completion.locator.archive_commit,
      archive_path: completion.locator.archive_path,
      archive_ciphertext_sha256: completion.locator.archive_ciphertext_sha256,
      encrypted: true,
    },
  };
  const nextView: SubmissionView = {
    ...view,
    schema_version: 2,
    result_event_id: view.schema_version === 2 ? view.result_event_id : null,
    archive: {
      status: "completed",
      event_id: event.event_id,
      occurred_at: event.occurred_at,
      ...event.payload,
    },
  };
  const outcome = await ledger.appendSubmissionLifecycle(
    [event],
    expectedLifecycleEventId,
    nextView,
  );
  return json({
    status: outcome.created ? "recorded" : "already_recorded",
    submission_id: view.submission_id,
    event_id: event.event_id,
  }, outcome.created ? 201 : 200);
}

async function archiveFailed(
  request: Request,
  env: RuntimeEnv,
  dependencies: ApiDependencies,
): Promise<Response> {
  const configured = env.LIFECYCLE_CALLBACK_TOKEN;
  const authorization = request.headers.get("authorization");
  const supplied = authorization?.startsWith("Bearer ")
    ? authorization.slice("Bearer ".length)
    : "";
  if (!configured) return json({ error: "callback_unavailable" }, 503);
  if (!supplied || !(await equalToken(supplied, configured))) {
    return json({ error: "authentication_failed" }, 401);
  }
  const failure = decodeArchiveFailure(await readJson(request));
  const ledger = state(env, dependencies);
  const view = await ledger.readSubmission(failure.submission_id);
  if (view === null) return json({ error: "submission_not_found" }, 409);
  if (view.dispatch.status !== "succeeded") return json({ error: "submission_not_dispatched" }, 409);
  if (view.archive.status === "completed" || view.evaluation.status !== "pending") {
    return json({ error: "submission_lifecycle_already_advanced" }, 409);
  }
  const eventId = await lifecycleEventId("archive.failed", view.submission_id, failure.occurred_at);
  const nextArchive = {
    status: "failed" as const,
    event_id: eventId,
    occurred_at: failure.occurred_at,
    reason_code: failure.reason_code,
    retryable: failure.retryable,
  };
  if (view.archive.status === "failed" && view.archive.event_id === eventId) {
    if (JSON.stringify(view.archive) !== JSON.stringify(nextArchive)) {
      throw new StateEventConflictError(`archive failure ${eventId}`);
    }
    return json({ status: "already_recorded", submission_id: view.submission_id, event_id: eventId });
  }
  const previousAt = view.archive.status === "failed" ? view.archive.occurred_at : view.accepted_at;
  if (Date.parse(failure.occurred_at) <= Date.parse(previousAt)) {
    throw new ApiDecodeError("archive failure must follow the current archive state");
  }
  const expectedLifecycleEventId = latestLifecycleEventId(view);
  const event: ArchiveFailedEvent = {
    schema_version: 1,
    event_id: eventId,
    event_type: "archive.failed",
    occurred_at: failure.occurred_at,
    subject_id: view.submission_id,
    causation_event_id: expectedLifecycleEventId,
    actor: { kind: "system" },
    payload: { reason_code: failure.reason_code, retryable: failure.retryable },
  };
  const nextView: SubmissionView = {
    ...view,
    schema_version: 2,
    result_event_id: view.schema_version === 2 ? view.result_event_id : null,
    archive: nextArchive,
  };
  const outcome = await ledger.appendSubmissionLifecycle(
    [event],
    expectedLifecycleEventId,
    nextView,
  );
  return json({
    status: outcome.created ? "recorded" : "already_recorded",
    submission_id: view.submission_id,
    event_id: event.event_id,
  }, outcome.created ? 201 : 200);
}

async function evaluationCompleted(
  request: Request,
  env: RuntimeEnv,
  dependencies: ApiDependencies,
): Promise<Response> {
  const configured = env.LIFECYCLE_CALLBACK_TOKEN;
  const authorization = request.headers.get("authorization");
  const supplied = authorization?.startsWith("Bearer ")
    ? authorization.slice("Bearer ".length)
    : "";
  if (!configured) return json({ error: "callback_unavailable" }, 503);
  if (!supplied || !(await equalToken(supplied, configured))) {
    return json({ error: "authentication_failed" }, 401);
  }
  const completion = decodeEvaluationCompletion(await readJson(request));
  const ledger = state(env, dependencies);
  const view = await ledger.readSubmission(completion.submission_id);
  if (view === null) return json({ error: "submission_not_found" }, 409);
  if (view.dispatch.status !== "succeeded") return json({ error: "submission_not_dispatched" }, 409);
  if (view.archive.status !== "completed") return json({ error: "submission_not_archived" }, 409);
  if (Date.parse(completion.occurred_at) <= Date.parse(view.archive.occurred_at)) {
    throw new ApiDecodeError("evaluation completion must follow archive completion");
  }
  const startedId = await lifecycleEventId(
    "evaluation.started",
    view.submission_id,
    completion.occurred_at,
  );
  const terminalAt = canonicalMilliseconds(Date.parse(completion.occurred_at) + 1);
  const terminalType = `evaluation.${completion.outcome.status}` as const;
  const terminalId = await lifecycleEventId(terminalType, view.submission_id, terminalAt);
  const evaluationBase = {
    event_id: terminalId,
    occurred_at: terminalAt,
    attempt: completion.attempt,
    benchmark_repository: completion.benchmark_repository,
    benchmark_commit: completion.benchmark_commit,
    toolchain: completion.toolchain,
  };
  const nextEvaluation = completion.outcome.status === "accepted"
    ? { status: "accepted" as const, ...evaluationBase, evaluator_version: completion.outcome.evaluator_version }
    : completion.outcome.status === "rejected"
      ? { status: "rejected" as const, ...evaluationBase, reason_code: completion.outcome.reason_code }
      : {
          status: "failed" as const,
          ...evaluationBase,
          reason_code: completion.outcome.reason_code,
          retryable: completion.outcome.retryable,
        };
  if (view.evaluation.status !== "pending" && view.evaluation.event_id === terminalId) {
    if (JSON.stringify(view.evaluation) !== JSON.stringify(nextEvaluation)) {
      throw new StateEventConflictError(`evaluation completion ${terminalId}`);
    }
    return json({
      status: "already_recorded",
      submission_id: view.submission_id,
      event_id: terminalId,
    });
  }
  const expectedLifecycleEventId = latestLifecycleEventId(view);
  const started: EvaluationStartedEvent = {
    schema_version: 1,
    event_id: startedId,
    event_type: "evaluation.started",
    occurred_at: completion.occurred_at,
    subject_id: view.submission_id,
    causation_event_id: expectedLifecycleEventId,
    actor: { kind: "system" },
    payload: {
      attempt: completion.attempt,
      benchmark_repository: completion.benchmark_repository,
      benchmark_commit: completion.benchmark_commit,
      toolchain: completion.toolchain,
    },
  };
  const terminal: EvaluationAcceptedEvent | EvaluationRejectedEvent | EvaluationFailedEvent =
    completion.outcome.status === "accepted"
      ? {
          schema_version: 1,
          event_id: terminalId,
          event_type: "evaluation.accepted",
          occurred_at: terminalAt,
          subject_id: view.submission_id,
          causation_event_id: started.event_id,
          actor: { kind: "system" },
          payload: { attempt: completion.attempt, evaluator_version: completion.outcome.evaluator_version },
        }
      : completion.outcome.status === "rejected"
        ? {
            schema_version: 1,
            event_id: terminalId,
            event_type: "evaluation.rejected",
            occurred_at: terminalAt,
            subject_id: view.submission_id,
            causation_event_id: started.event_id,
            actor: { kind: "system" },
            payload: { attempt: completion.attempt, reason_code: completion.outcome.reason_code },
          }
        : {
            schema_version: 1,
            event_id: terminalId,
            event_type: "evaluation.failed",
            occurred_at: terminalAt,
            subject_id: view.submission_id,
            causation_event_id: started.event_id,
            actor: { kind: "system" },
            payload: {
              attempt: completion.attempt,
              reason_code: completion.outcome.reason_code,
              retryable: completion.outcome.retryable,
            },
          };
  const nextView: SubmissionView = {
    ...view,
    schema_version: 2,
    result_event_id: view.schema_version === 2 ? view.result_event_id : null,
    evaluation: nextEvaluation,
  };
  const outcome = await ledger.appendSubmissionLifecycle(
    [started, terminal],
    expectedLifecycleEventId,
    nextView,
  );
  return json({
    status: outcome.created ? "recorded" : "already_recorded",
    submission_id: view.submission_id,
    event_id: terminal.event_id,
  }, outcome.created ? 201 : 200);
}

function addCalendarMonths(timestamp: string, months: number): string {
  const date = new Date(timestamp);
  const day = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + months);
  const endOfMonth = new Date(Date.UTC(
    date.getUTCFullYear(),
    date.getUTCMonth() + 1,
    0,
  )).getUTCDate();
  date.setUTCDate(Math.min(day, endOfMonth));
  return date.toISOString();
}

async function resultCompleted(
  request: Request,
  env: RuntimeEnv,
  dependencies: ApiDependencies,
): Promise<Response> {
  const configured = env.LIFECYCLE_CALLBACK_TOKEN;
  const authorization = request.headers.get("authorization");
  const supplied = authorization?.startsWith("Bearer ")
    ? authorization.slice("Bearer ".length)
    : "";
  if (!configured) return json({ error: "callback_unavailable" }, 503);
  if (!supplied || !(await equalToken(supplied, configured))) {
    return json({ error: "authentication_failed" }, 401);
  }
  const completion = decodeResultCompletion(await readJson(request));
  const expectedBranch = env.DEPLOYMENT_ENVIRONMENT === "staging" ? "staging-results" : "main";
  if (completion.result_branch !== expectedBranch) {
    throw new ApiDecodeError("result completion named the wrong environment branch");
  }
  const ledger = state(env, dependencies);
  const view = await ledger.readSubmission(completion.submission_id);
  if (view === null) return json({ error: "submission_not_found" }, 409);
  if (view.evaluation.status !== "accepted") {
    return json({ error: "submission_not_accepted" }, 409);
  }
  if (
    completion.problem_id !== view.submission.problem_id ||
    completion.statement_revision !== view.submission.statement_revision
  ) {
    throw new ApiDecodeError("result completion disagrees with submission identity");
  }
  if (Date.parse(completion.occurred_at) <= Date.parse(view.evaluation.occurred_at)) {
    throw new ApiDecodeError("result completion must follow evaluation acceptance");
  }
  const verified = await provider(env, dependencies).verifyResult(completion, {
    login: view.owner_login,
    declaredModel: view.submission.declared_model,
    problemId: view.submission.problem_id,
    statementRevision: view.submission.statement_revision,
  });
  if (
    verified.resultId !== completion.result_id ||
    verified.treeDigest !== completion.result_tree_digest
  ) {
    throw new GitHubProviderError(409, "verified result disagreed with completion");
  }
  if (view.result_id !== null) {
    if (view.result_id !== completion.result_id || view.result_event_id === null) {
      throw new StateEventConflictError(`result completion ${completion.result_id}`);
    }
  }
  const resultEventId = await lifecycleEventId(
    "result.recorded",
    completion.result_id,
    completion.occurred_at,
  );
  const result: ResultRecordedEvent = {
    schema_version: 1,
    event_id: resultEventId,
    event_type: "result.recorded",
    occurred_at: completion.occurred_at,
    subject_id: completion.result_id,
    causation_event_id: view.evaluation.event_id,
    actor: { kind: "system" },
    payload: {
      submission_id: view.submission_id,
      problem_id: view.submission.problem_id,
      statement_revision: view.submission.statement_revision,
      result_commit: completion.result_commit,
      tree_digest: completion.result_tree_digest,
    },
  };
  const releaseRequired =
    view.submission.problem_group !== "open-conjectures" &&
    view.publication_choice === "scheduled";
  let release: ReleaseScheduledEvent | undefined;
  if (releaseRequired) {
    const releaseOccurredAt = canonicalMilliseconds(Date.parse(completion.occurred_at) + 1);
    release = {
      schema_version: 1,
      event_id: await lifecycleEventId(
        "release.scheduled",
        completion.result_id,
        releaseOccurredAt,
      ),
      event_type: "release.scheduled",
      occurred_at: releaseOccurredAt,
      subject_id: completion.result_id,
      causation_event_id: result.event_id,
      actor: { kind: "system" },
      payload: {
        result_id: completion.result_id,
        release_at: addCalendarMonths(view.evaluation.occurred_at, 2),
      },
    };
  }
  const nextView: SubmissionView = {
    ...view,
    schema_version: 2,
    result_id: completion.result_id,
    result_event_id: result.event_id,
  };
  const events: WritableResultLifecycleEvent[] = release === undefined
    ? [result]
    : [result, release];
  const outcome = await ledger.recordAcceptedResult(
    events,
    view.evaluation.event_id,
    nextView,
  );
  return json({
    status: outcome.created ? "recorded" : "already_recorded",
    submission_id: view.submission_id,
    result_id: completion.result_id,
    event_id: result.event_id,
    release_event_id: release?.event_id ?? null,
  }, outcome.created ? 201 : 200);
}

async function sourceReaderPreflight(
  request: Request,
  env: RuntimeEnv,
  dependencies: ApiDependencies,
): Promise<Response> {
  if (!(await readinessAuthorized(request, env))) return json({ error: "not_found" }, 404);
  if (env.DEPLOYMENT_ENVIRONMENT !== "staging") {
    return json({ error: "staging_only" }, 403);
  }
  const repository = decodeSourceReaderPreflight(await readJson(request));
  const source = await provider(env, dependencies).repository(repository);
  return json({
    status: "source_reader_ready",
    environment: "staging",
    repository: source.fullName,
    private: source.private,
  });
}

function statusFor(view: SubmissionView): Record<string, unknown> {
  return {
    submission_id: view.submission_id,
    owner: view.owner_login,
    received_at: view.accepted_at,
    submission: view.submission,
    production_metadata: view.production_metadata,
    publication_choice: view.publication_choice,
    archive: view.archive,
    evaluation: view.evaluation,
    result_id: view.result_id,
    dispatch: view.dispatch,
  };
}

function idempotencyEventId(request: Request, nowMilliseconds: number): string {
  const value = request.headers.get("idempotency-key") ?? "";
  if (!isUuidV7(value)) throw new ApiDecodeError("Idempotency-Key must be a canonical lowercase UUIDv7");
  const timestamp = Number.parseInt(`${value.slice(0, 8)}${value.slice(9, 13)}`, 16);
  if (timestamp > nowMilliseconds) {
    throw new ApiDecodeError("Idempotency-Key timestamp must not be in the future");
  }
  return value;
}

async function apiRequest(request: Request, env: RuntimeEnv, dependencies: ApiDependencies): Promise<Response> {
  const url = new URL(request.url);
  const nowMilliseconds = dependencies.now?.() ?? Date.now();
  const now = Math.floor(nowMilliseconds / 1000);
  if (request.method === "GET" && url.pathname === "/api/v1/oauth/start") {
    if (!env.GITHUB_OAUTH_CLIENT_ID || !env.OAUTH_CALLBACK_URL) throw new AuthError("OAuth is not configured");
    const callback = new URL(env.OAUTH_CALLBACK_URL);
    if (callback.protocol !== "https:" || callback.pathname !== "/api/v1/oauth/callback") {
      throw new AuthError("OAuth callback is not an allowlisted HTTPS API callback");
    }
    const signed = await signToken(configuredSecret(env), makeOAuthState(now));
    const authorize = new URL("https://github.com/login/oauth/authorize");
    authorize.search = new URLSearchParams({ client_id: env.GITHUB_OAUTH_CLIENT_ID, redirect_uri: callback.toString(), scope: "read:user", state: signed }).toString();
    return new Response(null, {
      status: 302,
      headers: { "cache-control": "no-store", location: authorize.toString(), "set-cookie": setCookie(OAUTH_COOKIE, signed, 600, "Lax") },
    });
  }
  if (request.method === "GET" && url.pathname === "/api/v1/oauth/callback") {
    if (!env.GITHUB_OAUTH_CLIENT_ID || !env.GITHUB_OAUTH_CLIENT_SECRET || !env.OAUTH_CALLBACK_URL) throw new AuthError("OAuth is not configured");
    const signed = url.searchParams.get("state") ?? "";
    const bound = cookie(request, OAUTH_COOKIE) ?? "";
    if (!signed || !bound || !(await equalToken(signed, bound))) throw new AuthError("OAuth state is not session-bound");
    const oauth = await verifyToken<OAuthState>(configuredSecret(env), signed, "oauth_state", now);
    const code = url.searchParams.get("code");
    if (!code || !/^[A-Za-z0-9_=-]{8,512}$/.test(code)) throw new AuthError("OAuth code is invalid");
    const { identity } = await provider(env, dependencies).exchangeOAuth(
      env.GITHUB_OAUTH_CLIENT_ID,
      env.GITHUB_OAUTH_CLIENT_SECRET,
      code,
      env.OAUTH_CALLBACK_URL,
    );
    const consumed = await state(env, dependencies).appendEvent(
      await nonceEvent(oauth.nonce_event_id, oauth.nonce, "oauth", dependencies.now?.() ?? Date.now(), oauth.expires_at),
    );
    if (!consumed.created) throw new AuthError("OAuth state was already consumed");
    const browserSession: BrowserSession = { kind: "browser_session", login: identity.login, github_id: identity.id, issued_at: now, expires_at: now + 3600 };
    const token = await signToken(configuredSecret(env), browserSession);
    const destination = new URL(env.BROWSER_SUCCESS_URL ?? "/", url.origin);
    if (destination.origin !== url.origin) throw new AuthError("browser success URL must be same-origin");
    const headers = new Headers({
      "cache-control": "no-store",
      location: destination.toString(),
    });
    headers.append("set-cookie", setCookie(SESSION_COOKIE, token, 3600, "Strict"));
    headers.append("set-cookie", setCookie(OAUTH_COOKIE, "deleted", 0, "Lax"));
    return new Response(null, {
      status: 303,
      headers,
    });
  }
  if (request.method === "POST" && url.pathname === "/api/v1/browser/submission-grants") {
    const authenticated = await session(request, env, dependencies);
    const grant = makeSubmissionGrant(authenticated.login, now);
    return json({ grant: await signToken(configuredSecret(env), grant), submission_id: grant.submission_id, expires_at: canonicalTimestamp(grant.expires_at) }, 201);
  }
  if (request.method === "POST" && url.pathname === "/api/v1/browser/submissions") {
    const authenticated = await session(request, env, dependencies);
    const body = decodeBrowserSubmission(await readJson(request));
    const grant = await verifyToken<SubmissionGrant>(configuredSecret(env), body.grant, "submission_grant", now);
    return acceptSubmission(env, dependencies, { id: authenticated.github_id, login: authenticated.login }, grant, body.submission, "submission");
  }
  if (request.method === "POST" && url.pathname === "/api/v1/agent/challenges") {
    const challenge = makeAgentChallenge(decodeAgentChallengeInput(await readJson(request)), now);
    const signed = await signToken(configuredSecret(env), challenge);
    return json({
      challenge: signed,
      expires_at: canonicalTimestamp(challenge.expires_at),
      gist_id: challenge.gist_id,
      gist_file: "lean-eval-proof.txt",
      gist_content: signed,
      submission_id: challenge.submission_id,
      tag: challenge.tag,
    }, 201);
  }
  if (request.method === "POST" && url.pathname === "/api/v1/agent/submissions") {
    const body = decodeChallengeSubmission(await readJson(request));
    const challenge = await verifyToken<AgentChallenge>(configuredSecret(env), body.challenge, "agent_challenge", now);
    if (body.submission.source_repository !== challenge.source_repository || body.submission.source_commit !== challenge.source_commit) {
      throw new AuthError("agent challenge is not bound to this exact source");
    }
    const github = provider(env, dependencies);
    const identity = await submissionStage(
      "agent_gist_verification",
      () => github.verifySecretGist(challenge.gist_id, challenge.login, body.challenge),
    );
    await submissionStage(
      "agent_tag_verification",
      () => github.verifyTag(challenge.source_repository, challenge.tag, challenge.source_commit),
    );
    const response = await acceptSubmission(env, dependencies, identity, challenge, body.submission, "agent");
    const agentSession: BrowserSession = { kind: "browser_session", login: identity.login, github_id: identity.id, issued_at: now, expires_at: now + 3600 };
    const responseBody = await response.json<Record<string, unknown>>();
    return json({ ...responseBody, session_token: await signToken(configuredSecret(env), agentSession) }, response.status, {
      location: response.headers.get("location") ?? "",
    });
  }
  if (request.method === "POST" && url.pathname === "/api/v1/results/claims") {
    requireResultOwnerApi(env);
    const authenticated = await session(request, env, dependencies);
    const eventId = idempotencyEventId(request, nowMilliseconds);
    const input = decodeLegacyResultClaim(await readJson(request));
    const ledger = state(env, dependencies);
    const verified = await submissionStage(
      "legacy_result_verification",
      () => provider(env, dependencies).verifyLegacyResult(
        authenticated.login,
        input.results_commit,
        input.result_id,
      ),
    );
    const outcome = await ledger.claimLegacyResult({
      eventId,
      occurredAt: canonicalMilliseconds(nowMilliseconds),
      verified,
    });
    return json({
      result_id: outcome.resultId,
      status: outcome.created ? "claimed" : "already_claimed",
    }, outcome.created ? 201 : 200);
  }
  const resultMetadataMatch = /^\/api\/v1\/results\/(r2_[0-9a-f]{64})\/metadata$/.exec(url.pathname);
  if (request.method === "PATCH" && resultMetadataMatch?.[1]) {
    requireResultOwnerApi(env);
    const authenticated = await session(request, env, dependencies);
    const eventId = idempotencyEventId(request, nowMilliseconds);
    const metadata = decodeMetadataAmendment(await readJson(request));
    if (Object.keys(metadata).length === 0) {
      throw new ApiDecodeError("production_metadata must contain at least one backfill field");
    }
    const ledger = state(env, dependencies);
    const outcome = await ledger.backfillLegacyResultMetadata({
      eventId,
      occurredAt: canonicalMilliseconds(nowMilliseconds),
      resultId: resultMetadataMatch[1],
      ownerLogin: authenticated.login,
      productionMetadata: metadata,
    });
    return json({
      result_id: outcome.resultId,
      status: outcome.created ? "backfilled" : "unchanged",
    }, outcome.created ? 201 : 200);
  }
  const problemRepairMatch = /^\/api\/v1\/results\/(r2_[0-9a-f]{64})\/problem-repairs$/.exec(url.pathname);
  if (request.method === "POST" && problemRepairMatch?.[1]) {
    requireResultAmendmentOwnerApi(env);
    const authenticated = await session(request, env, dependencies);
    const eventId = idempotencyEventId(request, nowMilliseconds);
    const input = decodeProblemRepairRequest(await readJson(request));
    const outcome = await state(env, dependencies).requestResultProblemRepair({
      eventId,
      occurredAt: canonicalMilliseconds(nowMilliseconds),
      resultId: problemRepairMatch[1],
      ownerLogin: authenticated.login,
      correctedProblemId: input.corrected_problem_id,
      correctedStatementRevision: input.corrected_statement_revision,
      reasonCode: input.reason_code,
    });
    return json({
      result_id: outcome.resultId,
      repair_revision: outcome.repairRevision,
      status: outcome.created ? "problem_repair_requested" : "problem_repair_already_requested",
    }, outcome.created ? 201 : 200);
  }
  const problemRepairDecisionMatch = /^\/api\/v1\/results\/(r2_[0-9a-f]{64})\/problem-repairs\/decisions$/.exec(url.pathname);
  if (request.method === "POST" && problemRepairDecisionMatch?.[1]) {
    const authenticated = await session(request, env, dependencies);
    const maintainer = requireResultAmendmentMaintainer(env, authenticated);
    const eventId = idempotencyEventId(request, nowMilliseconds);
    const input = decodeProblemRepairDecision(await readJson(request));
    const ledger = state(env, dependencies);
    let comparatorEvidence: ComparatorEvidence | null = null;
    let reasonCode: string | null = null;
    if (input.decision === "apply") {
      const current = await ledger.readResultAmendmentForMaintainer(problemRepairDecisionMatch[1]);
      const pending = current.problem_repair;
      if (pending?.status !== "pending") {
        throw new ResultOwnerStateError(409, "result does not have one pending problem repair");
      }
      comparatorEvidence = await submissionStage(
        "problem_repair_comparator_verification",
        () => provider(env, dependencies).verifyProblemRepairComparator({
          resultsCommit: input.results_commit,
          resultId: current.result_id,
          ownerLogin: current.owner_login,
          declaredModel: current.declared_model,
          baseProblemId: current.base_problem_id,
          baseStatementRevision: current.base_statement_revision,
          correctedProblemId: pending.corrected_problem_id,
          correctedStatementRevision: pending.corrected_statement_revision,
        }),
      );
    } else {
      reasonCode = input.reason_code;
    }
    const outcome = await ledger.decideResultProblemRepair({
      eventId,
      occurredAt: canonicalMilliseconds(nowMilliseconds),
      resultId: problemRepairDecisionMatch[1],
      reviewerLogin: maintainer.login,
      decision: input.decision,
      reasonCode,
      comparatorEvidence,
    });
    const decisionStatus = input.decision === "apply" ? "applied" : "rejected";
    return json({
      result_id: outcome.resultId,
      repair_revision: outcome.repairRevision,
      status: outcome.created ? `problem_repair_${decisionStatus}` : `problem_repair_already_${decisionStatus}`,
    }, outcome.created ? 201 : 200);
  }
  const retractionMatch = /^\/api\/v1\/results\/(r2_[0-9a-f]{64})\/retractions$/.exec(url.pathname);
  if (request.method === "POST" && retractionMatch?.[1]) {
    requireResultAmendmentOwnerApi(env);
    const authenticated = await session(request, env, dependencies);
    const eventId = idempotencyEventId(request, nowMilliseconds);
    const input = decodeResultRetractionRequest(await readJson(request));
    const outcome = await state(env, dependencies).requestResultRetraction({
      eventId,
      occurredAt: canonicalMilliseconds(nowMilliseconds),
      resultId: retractionMatch[1],
      ownerLogin: authenticated.login,
      reasonCode: input.reason_code,
    });
    return json({
      result_id: outcome.resultId,
      retraction_revision: outcome.retractionRevision,
      status: outcome.created ? "retraction_requested" : "retraction_already_requested",
    }, outcome.created ? 201 : 200);
  }
  const retractionDecisionMatch = /^\/api\/v1\/results\/(r2_[0-9a-f]{64})\/retractions\/decisions$/.exec(url.pathname);
  if (request.method === "POST" && retractionDecisionMatch?.[1]) {
    const authenticated = await session(request, env, dependencies);
    const maintainer = requireResultAmendmentMaintainer(env, authenticated);
    const eventId = idempotencyEventId(request, nowMilliseconds);
    const input = decodeResultRetractionDecision(await readJson(request));
    const outcome = await state(env, dependencies).decideResultRetraction({
      eventId,
      occurredAt: canonicalMilliseconds(nowMilliseconds),
      resultId: retractionDecisionMatch[1],
      reviewerLogin: maintainer.login,
      decision: input.decision,
      reasonCode: input.reason_code,
    });
    const decisionStatus = input.decision === "approve" ? "approved" : "rejected";
    return json({
      result_id: outcome.resultId,
      retraction_revision: outcome.retractionRevision,
      status: outcome.created ? `retraction_${decisionStatus}` : `retraction_already_${decisionStatus}`,
    }, outcome.created ? 201 : 200);
  }
  const retractionOverrideMatch = /^\/api\/v1\/results\/(r2_[0-9a-f]{64})\/retractions\/override$/.exec(url.pathname);
  if (request.method === "POST" && retractionOverrideMatch?.[1]) {
    const authenticated = await session(request, env, dependencies);
    const maintainer = requireResultAmendmentMaintainer(env, authenticated);
    const eventId = idempotencyEventId(request, nowMilliseconds);
    const input = decodeResultRetractionOverride(await readJson(request));
    const outcome = await state(env, dependencies).overrideResultRetraction({
      eventId,
      occurredAt: canonicalMilliseconds(nowMilliseconds),
      resultId: retractionOverrideMatch[1],
      reviewerLogin: maintainer.login,
      reasonCode: input.reason_code,
    });
    return json({
      result_id: outcome.resultId,
      retraction_revision: outcome.retractionRevision,
      status: outcome.created ? "retraction_overridden" : "retraction_already_overridden",
    }, outcome.created ? 201 : 200);
  }
  const retractionFinalizationMatch = /^\/api\/v1\/results\/(r2_[0-9a-f]{64})\/retractions\/finalize$/.exec(url.pathname);
  if (request.method === "POST" && retractionFinalizationMatch?.[1]) {
    const authenticated = await session(request, env, dependencies);
    const maintainer = requireResultAmendmentMaintainer(env, authenticated);
    decodeEmptyObject(await readJson(request), "terminal retraction request");
    const outcome = await state(env, dependencies).finalizeResultRetraction({
      eventId: idempotencyEventId(request, nowMilliseconds),
      occurredAt: canonicalMilliseconds(nowMilliseconds),
      resultId: retractionFinalizationMatch[1],
      maintainerLogin: maintainer.login,
    });
    return json({
      result_id: outcome.resultId,
      release_disposition: outcome.releaseDisposition,
      status: outcome.created ? "retracted" : "already_retracted",
    }, outcome.created ? 201 : 200);
  }
  const match = /^\/api\/v1\/submissions\/([^/]+)(?:\/(metadata|publication))?$/.exec(url.pathname);
  if (match?.[1] && isUuidV7(match[1])) {
    const authenticated = await session(request, env, dependencies);
    const ledger = state(env, dependencies);
    const current = await ledger.readSubmission(match[1]);
    if (current?.owner_login !== authenticated.login) return json({ error: "not_found" }, 404);
    if (request.method === "GET" && !match[2]) return json(statusFor(current));
    const eventId = idempotencyEventId(request, nowMilliseconds);
    if (request.method === "PATCH" && match[2] === "metadata") {
      const metadata = decodeMetadataAmendment(await readJson(request));
      const event: WritableStateEvent = {
        schema_version: 1, event_id: eventId, event_type: "submission.metadata_amended",
        occurred_at: canonicalMilliseconds(nowMilliseconds), subject_id: match[1], causation_event_id: current.mutation_event_id,
        actor: { kind: "github", login: authenticated.login },
        payload: { production_metadata: metadata },
      };
      const nextView: SubmissionView = {
        ...current,
        mutation_event_id: eventId,
        metadata_event_id: eventId,
        submission: { ...current.submission, production_metadata: metadata },
        production_metadata: metadata,
      };
      await ledger.appendSubmissionMutation(event, current.mutation_event_id, nextView);
      return json({ submission_id: match[1], status: "amended" });
    }
    if (request.method === "PUT" && match[2] === "publication") {
      const choice: PublicationChoice = decodePublicationChoice(await readJson(request));
      const event: WritableStateEvent = {
        schema_version: 1, event_id: eventId, event_type: "submission.publication_changed",
        occurred_at: canonicalMilliseconds(nowMilliseconds), subject_id: match[1], causation_event_id: current.mutation_event_id,
        actor: { kind: "github", login: authenticated.login }, payload: { publication_choice: choice },
      };
      const nextView: SubmissionView = {
        ...current,
        mutation_event_id: eventId,
        publication_event_id: eventId,
        submission: { ...current.submission, publication_choice: choice },
        publication_choice: choice,
      };
      await ledger.appendSubmissionMutation(event, current.mutation_event_id, nextView);
      return json({ submission_id: match[1], publication_choice: choice });
    }
  }
  return json({ error: "not_found" }, 404);
}

function errorResponse(error: unknown): Response {
  if (error instanceof ApiDecodeError) return json({ error: "invalid_request", detail: error.message }, 400);
  if (error instanceof AuthError) return json({ error: "authentication_failed" }, 401);
  if (error instanceof StateUpdateOutcomeUnknownError) {
    return json({ error: "state_unavailable" }, 503);
  }
  if (error instanceof ResultIdentityCollisionError) {
    return json({ error: "result_identity_conflict" }, 409);
  }
  if (error instanceof StateEventConflictError) return json({ error: "idempotency_conflict" }, 409);
  if (error instanceof ResultOwnerStateError) {
    return error.status === 404
      ? json({ error: "not_found" }, 404)
      : json({ error: "idempotency_conflict" }, 409);
  }
  if (error instanceof GitHubProviderError) {
    const status = error.status === 409 ? 409 : error.status === 404 ? 422 : 503;
    return json({ error: status === 409 ? "proof_failed" : status === 422 ? "source_not_found" : "provider_unavailable" }, status);
  }
  if (error instanceof GitHubStateError) {
    return json({ error: "state_unavailable" }, 503);
  }
  console.error(JSON.stringify({ event: "api_request_failed", error_name: error instanceof Error ? error.name : "unknown" }));
  return json({ error: "internal_error" }, 500);
}

export async function handleRequest(
  request: Request,
  env: RuntimeEnv,
  lifecycle: Lifecycle,
  dependencies: ApiDependencies = {},
): Promise<Response> {
  const url = new URL(request.url);
  const intake = currentIntake(env, dependencies);
  if (request.method === "GET" && url.pathname === "/") {
    return browserPage(env.DEPLOYMENT_ENVIRONMENT, intake.effective);
  }
  if (request.method === "GET" && url.pathname === "/intake.js") {
    return browserScript();
  }
  if (request.method === "GET" && url.pathname === "/healthz") {
    return json({
      status: "ok",
      service: "lean-eval-submission",
      deployed_commit: env.DEPLOYED_COMMIT,
      environment: env.DEPLOYMENT_ENVIRONMENT,
      intake_configured_enabled: intake.configured,
      intake_effective_enabled: intake.effective,
      intake_enabled: intake.effective,
      intake_enablement_mode: intake.mode,
      intake_lease_expires_at: intake.leaseExpiresAt,
      legacy_result_owner_api_enabled: resultOwnerApiEnabled(env),
      result_amendment_owner_api_enabled: resultAmendmentOwnerApiEnabled(env),
      result_amendment_maintainer_api_enabled: resultAmendmentMaintainerApiEnabled(env),
      promotion_canary_configured_enabled: env.PROMOTION_CANARY_ENABLED === "true",
      promotion_canary_enabled: promotionCanaryEnabled(env),
    });
  }
  if ((request.method === "GET" || request.method === "POST") && url.pathname === "/readyz") {
    return readiness(request, env, lifecycle, dependencies);
  }
  if (request.method === "POST" && url.pathname === "/internal/v1/promotion-canary") {
    if (!promotionCanaryEnabled(env)) return json({ error: "not_found" }, 404);
    try {
      return await promotionCanary(request, env, dependencies);
    } catch (error) {
      return errorResponse(error);
    }
  }
  if (request.method === "POST" && url.pathname === "/internal/v1/archive-completed") {
    try {
      return await archiveCompleted(request, env, dependencies);
    } catch (error) {
      return errorResponse(error);
    }
  }
  if (request.method === "POST" && url.pathname === "/internal/v1/archive-failed") {
    try {
      return await archiveFailed(request, env, dependencies);
    } catch (error) {
      return errorResponse(error);
    }
  }
  if (request.method === "POST" && url.pathname === "/internal/v1/evaluation-completed") {
    try {
      return await evaluationCompleted(request, env, dependencies);
    } catch (error) {
      return errorResponse(error);
    }
  }
  if (request.method === "POST" && url.pathname === "/internal/v1/result-completed") {
    try {
      return await resultCompleted(request, env, dependencies);
    } catch (error) {
      return errorResponse(error);
    }
  }
  if (request.method === "POST" && url.pathname === "/internal/v1/source-reader-preflight") {
    try {
      return await sourceReaderPreflight(request, env, dependencies);
    } catch (error) {
      return errorResponse(error);
    }
  }
  if (request.method === "POST" && url.pathname === "/internal/v1/intake-lease-smoke") {
    try {
      return await intakeLeaseSmoke(request, env, dependencies);
    } catch (error) {
      return errorResponse(error);
    }
  }
  const legacyResultOwnerRoute = url.pathname === "/api/v1/results/claims" ||
    /^\/api\/v1\/results\/r2_[0-9a-f]{64}\/metadata$/.test(url.pathname);
  const amendmentOwnerRoute = /^\/api\/v1\/results\/r2_[0-9a-f]{64}\/(?:problem-repairs|retractions)$/.test(url.pathname);
  const amendmentMaintainerRoute = /^\/api\/v1\/results\/r2_[0-9a-f]{64}\/(?:problem-repairs\/decisions|retractions\/(?:decisions|override|finalize))$/.test(url.pathname);
  if (legacyResultOwnerRoute && !resultOwnerApiEnabled(env)) return json({ error: "not_found" }, 404);
  if (amendmentOwnerRoute && !resultAmendmentOwnerApiEnabled(env)) return json({ error: "not_found" }, 404);
  if (amendmentMaintainerRoute && !resultAmendmentMaintainerApiEnabled(env)) return json({ error: "not_found" }, 404);
  const oauthRoute = url.pathname === "/api/v1/oauth/start" || url.pathname === "/api/v1/oauth/callback";
  const anyOwnerApiEnabled = resultOwnerApiEnabled(env) || resultAmendmentOwnerApiEnabled(env) ||
    resultAmendmentMaintainerApiEnabled(env);
  if (
    url.pathname.startsWith("/api/") &&
    !intake.effective &&
    !((legacyResultOwnerRoute || amendmentOwnerRoute || amendmentMaintainerRoute || oauthRoute) && anyOwnerApiEnabled)
  ) {
    return json({ error: "intake_disabled" }, 503);
  }
  if (url.pathname.startsWith("/api/v1/")) {
    if (!(await rateLimit(request, env, dependencies))) {
      return json({ error: "rate_limited" }, 429, { "retry-after": "60" });
    }
    try {
      return await apiRequest(request, env, dependencies);
    } catch (error) {
      return errorResponse(error);
    }
  }
  return json({ error: "not_found" }, 404);
}

function promotionCanaryIdentity(input: SubmissionInput): PromotionCanaryIdentity | null {
  const match = PROMOTION_CANARY_MODEL.exec(input.declared_model);
  if (
    match?.[1] === undefined ||
    match[2] === undefined ||
    match[3] === undefined ||
    input.problem_id !== "two_plus_two" ||
    input.problem_group !== "formalization-evaluation" ||
    input.statement_revision !== 1 ||
    input.source_repository !== PROMOTION_CANARY_REPOSITORY ||
    input.source_commit !== PROMOTION_CANARY_SOURCE_COMMIT ||
    input.source_visibility !== "private" ||
    input.publication_choice !== "withheld" ||
    input.production_metadata.notes !== PROMOTION_CANARY_NOTES ||
    input.production_metadata.web_access !== false ||
    input.production_metadata.billing_mode !== "unknown"
  ) {
    return null;
  }
  return { commit: match[1], runId: match[2], runAttempt: match[3] };
}

type PromotionCanaryClassification =
  | Readonly<{ kind: "ordinary" }>
  | Readonly<{ kind: "invalid_canary" }>
  | Readonly<{ kind: "canary"; identity: PromotionCanaryIdentity }>;

function classifyPromotionCanary(entry: DispatchOutbox): PromotionCanaryClassification {
  const claimed =
    entry.owner_login === PROMOTION_CANARY_LOGIN &&
    entry.submission.source_repository === PROMOTION_CANARY_REPOSITORY &&
    entry.submission.source_commit === PROMOTION_CANARY_SOURCE_COMMIT &&
    entry.submission.source_visibility === "private" &&
    entry.submission.publication_choice === "withheld" &&
    entry.submission_id.endsWith("ca");
  if (!claimed) return { kind: "ordinary" };
  const identity = promotionCanaryIdentity(entry.submission);
  if (
    identity === null ||
    entry.workflow_ref !== `lean-eval-dispatch/${identity.commit}` ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{10}ca$/.test(entry.submission_id)
  ) {
    return { kind: "invalid_canary" };
  }
  return { kind: "canary", identity };
}

function logScheduledBudgetDeferral(
  target: "submission" | "promotion_canary",
  subrequests: ScheduledSubrequestBudget,
): void {
  console.error(JSON.stringify({
    event: "scheduled_dispatch_budget_deferred",
    target,
    remaining_subrequests: subrequests.remaining,
    required_subrequests: SCHEDULED_DISPATCH_ITEM_RESERVE,
  }));
}

async function reconcilePromotionCanariesScheduled(
  env: RuntimeEnv,
  scheduledTime: number,
  dependencies: ApiDependencies,
  subrequests: ScheduledSubrequestBudget,
  intake: IntakeEnablement,
): Promise<void> {
  requirePromotionCanaryConfiguration(env, dependencies);
  if (intake.effective) {
    throw new GitHubStateError(503, "promotion canary scheduling requires ordinary intake disabled");
  }
  const ledger = state(
    env,
    dependencies,
    subrequests.wrap(dependencies.stateFetch ?? timedGitHubFetch),
  );
  const scanOffset = Math.floor(scheduledTime / 60_000) * DISPATCH_OUTBOX_SCAN_LIMIT;
  const entries = await ledger.listDispatchOutbox(
    PROMOTION_CANARY_OUTBOX_SHARD,
    scanOffset,
    DISPATCH_OUTBOX_SCAN_LIMIT,
  );
  const due = entries
    .filter((entry) => classifyPromotionCanary(entry).kind !== "ordinary")
    .filter((entry) => Date.parse(entry.next_attempt_at) <= scheduledTime)
    .sort((left, right) =>
      left.next_attempt_at.localeCompare(right.next_attempt_at) ||
      left.submission_id.localeCompare(right.submission_id));
  for (const entry of due) {
    if (subrequests.remaining < SCHEDULED_DISPATCH_ITEM_RESERVE) {
      logScheduledBudgetDeferral("promotion_canary", subrequests);
      break;
    }
    try {
      const classification = classifyPromotionCanary(entry);
      if (classification.kind !== "canary") {
        throw new GitHubStateError(502, "promotion canary outbox material is not exact");
      }
      const { identity } = classification;
      const material = await promotionCanaryMaterial(identity);
      const expected = initialSubmissionView(
        material.grant,
        PROMOTION_CANARY_LOGIN,
        material.input,
        material.acceptedAtMilliseconds,
        `lean-eval-dispatch/${identity.commit}`,
      );
      if (
        entry.submission_id !== material.grant.submission_id ||
        entry.owner_login !== PROMOTION_CANARY_LOGIN ||
        entry.workflow_ref !== expected.dispatch.workflow_ref ||
        JSON.stringify(entry.submission) !== JSON.stringify(expected.submission)
      ) {
        throw new GitHubStateError(502, "promotion canary outbox identity is not exact");
      }
      const view = await ledger.readSubmission(material.grant.submission_id);
      if (view === null) throw new GitHubStateError(502, "promotion canary State view is missing");
      assertPromotionCanaryView(expected, view);
      if (view.dispatch.status === "succeeded") continue;
      if (entry.attempts !== view.dispatch.attempts) {
        throw new GitHubStateError(502, "promotion canary outbox attempt does not match State");
      }
      if (view.dispatch.status === "failed" && view.dispatch.attempts >= 32) {
        subrequests.requireRemaining(DISPATCH_UPDATE_MAX_SUBREQUESTS);
        await ledger.updateDispatch(view, view.dispatch.attempts, null);
        console.error(JSON.stringify({
          event: "promotion_canary_terminal_retry_exhausted",
          error_name: "retry_limit_reached",
        }));
        continue;
      }
      await reconcileDispatch(
        env,
        dependencies,
        ledger,
        view,
        scheduledTime,
        "promotion_canary",
        subrequests,
      );
    } catch (error) {
      console.error(JSON.stringify({
        event: "promotion_canary_scheduled_item_failed",
        error_name: error instanceof Error ? error.name : "unknown",
      }));
    }
  }
}

export async function handleScheduled(
  env: RuntimeEnv,
  scheduledTime: number,
  dependencies: ApiDependencies = {},
): Promise<void> {
  const subrequests = dependencies.scheduledSubrequestBudget ?? new ScheduledSubrequestBudget();
  const githubFetch = dependencies.stateFetch ?? timedGitHubFetch;
  const intake = currentIntake(env, dependencies);
  if (promotionCanaryEnabled(env)) {
    try {
      await reconcilePromotionCanariesScheduled(
        env,
        scheduledTime,
        dependencies,
        subrequests,
        intake,
      );
    } catch (error) {
      console.error(JSON.stringify({
        event: "promotion_canary_scheduled_scan_failed",
        error_name: error instanceof Error ? error.name : "unknown",
      }));
    }
  }
  if (!intake.effective) return;
  requireDispatchConfiguration(env, dependencies);
  const ledger = state(env, dependencies, subrequests.wrap(githubFetch));
  const shardNumber = Math.floor(scheduledTime / 60_000) % 256;
  const shard = shardNumber.toString(16).padStart(2, "0");
  const scanOffset = Math.floor(scheduledTime / (256 * 60_000)) * DISPATCH_OUTBOX_SCAN_LIMIT;
  const entries = await ledger.listDispatchOutbox(shard, scanOffset, DISPATCH_OUTBOX_SCAN_LIMIT);
  const due = entries
    .filter((entry) =>
      !promotionCanaryEnabled(env) ||
      classifyPromotionCanary(entry).kind === "ordinary")
    .filter((entry) => Date.parse(entry.next_attempt_at) <= scheduledTime)
    .sort((left, right) => left.next_attempt_at.localeCompare(right.next_attempt_at) || left.submission_id.localeCompare(right.submission_id));
  for (const entry of due) {
    if (subrequests.remaining < SCHEDULED_DISPATCH_ITEM_RESERVE) {
      logScheduledBudgetDeferral("submission", subrequests);
      break;
    }
    const view = await ledger.readSubmission(entry.submission_id);
    if (
      view?.owner_login !== entry.owner_login ||
      view.dispatch.workflow_ref !== entry.workflow_ref ||
      view.dispatch.attempts !== entry.attempts ||
      JSON.stringify(view.submission) !== JSON.stringify(entry.submission)
    ) {
      throw new GitHubStateError(502, `dispatch outbox ${entry.submission_id} does not match its targeted submission view`);
    }
    if (view.dispatch.status === "failed" && view.dispatch.attempts >= 32) {
      subrequests.requireRemaining(DISPATCH_UPDATE_MAX_SUBREQUESTS);
      await ledger.updateDispatch(view, view.dispatch.attempts, null);
      console.error(JSON.stringify({
        event: "submission_dispatch_terminal_retry_exhausted",
        error_name: "retry_limit_reached",
      }));
      continue;
    }
    await reconcileDispatch(env, dependencies, ledger, view, scheduledTime, "submission", subrequests);
  }
}
