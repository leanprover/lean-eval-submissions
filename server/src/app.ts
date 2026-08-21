import {
  ApiDecodeError,
  assertSourcePolicy,
  decodeAgentChallengeInput,
  decodeBrowserSubmission,
  decodeChallengeSubmission,
  decodeMetadataAmendment,
  decodePublicationChoice,
  isUuidV7,
  readJson,
  type ProductionMetadata,
  type PublicationChoice,
  type SubmissionInput,
} from "./api-contract";
import {
  AuthError,
  equalToken,
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
import {
  type GitHubFetch,
  GitHubStateError,
  GitHubStateRepository,
  StateEventConflictError,
} from "./github-state";
import {
  buildDispatchRequest,
  GitHubProvider,
  GitHubProviderError,
  type GitHubIdentity,
} from "./github-provider";
import { githubBrokerFetch } from "./github-broker-client";
import { type WritableStateEvent } from "./state-event";
import {
  type DispatchOutbox,
  type SubmissionView,
} from "./submission-view";

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
  | "OAUTH_CALLBACK_URL"
  | "READINESS_TOKEN"
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
    OAUTH_CALLBACK_URL?: string;
    READINESS_TOKEN?: string;
    STATE_REPOSITORY: string;
  }>;

type Lifecycle = Pick<ExecutionContext, "waitUntil">;
export type StateAccess = Readonly<{
  appendEvent(event: WritableStateEvent): Promise<{ created: boolean }>;
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
  listDispatchOutbox(shard: string): Promise<readonly DispatchOutbox[]>;
}>;
export type ApiDependencies = Readonly<{
  now?: () => number;
  provider?: GitHubProvider;
  state?: StateAccess;
  dispatch?: (request: Request) => Promise<void>;
  rateLimit?: (key: string) => Promise<Readonly<{ success: boolean }>>;
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

function json(body: unknown, status = 200, additionalHeaders?: HeadersInit): Response {
  const headers = new Headers(JSON_HEADERS);
  new Headers(additionalHeaders).forEach((value, key) => headers.set(key, value));
  return Response.json(body, { status, headers });
}

function intakeEnabled(env: RuntimeEnv): boolean {
  return env.INTAKE_ENABLED === "true";
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

function stateRepository(env: RuntimeEnv): GitHubStateRepository {
  if (!env.GITHUB_STATE_TOKEN) throw new GitHubStateError(503, "State credential missing");
  return new GitHubStateRepository(
    { repository: env.STATE_REPOSITORY, token: env.GITHUB_STATE_TOKEN, userAgent: "lean-eval-submission-worker" },
    timedGitHubFetch,
  );
}

async function readiness(request: Request, env: RuntimeEnv, lifecycle: Lifecycle): Promise<Response> {
  if (!(await readinessAuthorized(request, env))) return json({ error: "not_found" }, 404);
  const verifyWrite = request.method === "POST";
  if (!verifyWrite && !intakeEnabled(env)) {
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
      const stateCommit = await repository.assertWritable();
      return json({
        status: "state_writer_ready",
        environment: env.DEPLOYMENT_ENVIRONMENT,
        intake_enabled: intakeEnabled(env),
        state_commit: stateCommit,
      });
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
  );
}

function state(env: RuntimeEnv, dependencies: ApiDependencies): StateAccess {
  return dependencies.state ?? stateRepository(env);
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

async function nonceEvent(
  eventId: string,
  nonce: string,
  purpose: "agent" | "oauth" | "submission",
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
): Promise<void> {
  const request = buildDispatchRequest(
    env.DISPATCH_REPOSITORY ?? "leanprover/lean-eval-submissions",
    env.DISPATCH_WORKFLOW ?? "submission.yml",
    env.DISPATCH_WORKFLOW_REF ?? "",
    submissionId,
    login,
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
    schema_version: 1,
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
): Promise<SubmissionView> {
  if (view.dispatch.status === "succeeded") return view;
  const attempt = view.dispatch.attempts + 1;
  if (attempt > 32) return view;
  try {
    await dispatchSubmission(env, dependencies, view.submission_id, view.owner_login, view.submission);
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
  const repository = await provider(env, dependencies).repository(input.source_repository);
  assertSourcePolicy(input.problem_group, input.source_visibility, repository.private);
  const acceptedAtMilliseconds = dependencies.now?.() ?? Date.now();
  const workflowRef = env.DISPATCH_WORKFLOW_REF ?? "";
  const events: WritableStateEvent[] = [
    await nonceEvent(grant.nonce_event_id, grant.nonce, purpose, acceptedAtMilliseconds, grant.expires_at),
    receivedEvent(grant.submission_id, identity.login, input, acceptedAtMilliseconds + 1),
    metadataEvent(grant.metadata_event_id, grant.submission_id, identity.login, input.production_metadata, acceptedAtMilliseconds + 2),
  ];
  const ledger = state(env, dependencies);
  const proposedView = initialSubmissionView(grant, identity.login, input, acceptedAtMilliseconds, workflowRef);
  const outcome = await ledger.acceptSubmission(events, proposedView, initialDispatchOutbox(proposedView));
  const reconciled = await reconcileDispatch(env, dependencies, ledger, outcome.view, acceptedAtMilliseconds + 3);
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

function idempotencyEventId(request: Request): string {
  const value = request.headers.get("idempotency-key") ?? "";
  if (!isUuidV7(value)) throw new ApiDecodeError("Idempotency-Key must be a canonical lowercase UUIDv7");
  return value;
}

async function apiRequest(request: Request, env: RuntimeEnv, dependencies: ApiDependencies): Promise<Response> {
  const url = new URL(request.url);
  const now = nowSeconds(dependencies);
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
    const identity = await github.verifySecretGist(challenge.gist_id, challenge.login, body.challenge);
    await github.verifyTag(challenge.source_repository, challenge.tag, challenge.source_commit);
    const response = await acceptSubmission(env, dependencies, identity, challenge, body.submission, "agent");
    const agentSession: BrowserSession = { kind: "browser_session", login: identity.login, github_id: identity.id, issued_at: now, expires_at: now + 3600 };
    const responseBody = await response.json<Record<string, unknown>>();
    return json({ ...responseBody, session_token: await signToken(configuredSecret(env), agentSession) }, response.status, {
      location: response.headers.get("location") ?? "",
    });
  }
  const match = /^\/api\/v1\/submissions\/([^/]+)(?:\/(metadata|publication))?$/.exec(url.pathname);
  if (match?.[1] && isUuidV7(match[1])) {
    const authenticated = await session(request, env, dependencies);
    const ledger = state(env, dependencies);
    const current = await ledger.readSubmission(match[1]);
    if (current?.owner_login !== authenticated.login) return json({ error: "not_found" }, 404);
    if (request.method === "GET" && !match[2]) return json(statusFor(current));
    const eventId = idempotencyEventId(request);
    if (request.method === "PATCH" && match[2] === "metadata") {
      const metadata = decodeMetadataAmendment(await readJson(request));
      const event: WritableStateEvent = {
        schema_version: 1, event_id: eventId, event_type: "submission.metadata_amended",
        occurred_at: canonicalTimestamp(now), subject_id: match[1], causation_event_id: current.mutation_event_id,
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
        occurred_at: canonicalTimestamp(now), subject_id: match[1], causation_event_id: current.mutation_event_id,
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
  if (error instanceof StateEventConflictError) return json({ error: "idempotency_conflict" }, 409);
  if (error instanceof GitHubProviderError) {
    const status = error.status === 409 ? 409 : error.status === 404 ? 422 : 503;
    return json({ error: status === 409 ? "proof_failed" : status === 422 ? "source_not_found" : "provider_unavailable" }, status);
  }
  if (error instanceof GitHubStateError) return json({ error: "state_unavailable" }, 503);
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
  if (request.method === "GET" && url.pathname === "/healthz") {
    return json({ status: "ok", service: "lean-eval-submission", deployed_commit: env.DEPLOYED_COMMIT, environment: env.DEPLOYMENT_ENVIRONMENT, intake_enabled: intakeEnabled(env) });
  }
  if ((request.method === "GET" || request.method === "POST") && url.pathname === "/readyz") {
    return readiness(request, env, lifecycle);
  }
  if (url.pathname.startsWith("/api/") && !intakeEnabled(env)) return json({ error: "intake_disabled" }, 503);
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

export async function handleScheduled(
  env: RuntimeEnv,
  scheduledTime: number,
  dependencies: ApiDependencies = {},
): Promise<void> {
  requireDispatchConfiguration(env, dependencies);
  const ledger = state(env, dependencies);
  const shardNumber = Math.floor(scheduledTime / 60_000) % 256;
  const shard = shardNumber.toString(16).padStart(2, "0");
  const entries = await ledger.listDispatchOutbox(shard);
  const due = entries
    .filter((entry) => Date.parse(entry.next_attempt_at) <= scheduledTime)
    .sort((left, right) => left.next_attempt_at.localeCompare(right.next_attempt_at) || left.submission_id.localeCompare(right.submission_id))
    .slice(0, 20);
  for (const entry of due) {
    const view = await ledger.readSubmission(entry.submission_id);
    if (
      view?.owner_login !== entry.owner_login ||
      view.dispatch.workflow_ref !== entry.workflow_ref ||
      view.dispatch.attempts !== entry.attempts ||
      JSON.stringify(view.submission) !== JSON.stringify(entry.submission)
    ) {
      throw new GitHubStateError(502, `dispatch outbox ${entry.submission_id} does not match its targeted submission view`);
    }
    await reconcileDispatch(env, dependencies, ledger, view, scheduledTime);
  }
}
