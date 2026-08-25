import type {
  ModelIdentityQualificationJournal,
  QualificationApiRequestPlan,
  QualificationJournalStatus,
  QualificationOperation,
  QualificationRecoveryPlan,
  QualificationStepPlan,
} from "./model-identity-qualification-journal";
import {
  canonicalQualificationValue,
  qualificationApiRequestDigest,
  QUALIFICATION_OPERATIONS,
} from "./model-identity-qualification-journal";
import { signQualificationExecutorCapability } from "./model-identity-qualification-capability";
import {
  qualificationStateMutationSequence,
  qualificationStateMutationPrefix,
  qualificationStateSnapshot,
  QualificationStateError,
  restoreQualificationState,
} from "./model-identity-qualification-state";
import type { GitHubFetch } from "./github-state";
import {
  AuthError,
  verifyToken,
  type AgentSession,
  type BrowserSession,
} from "./auth";
import { STAGING_MODEL_IDENTITY_STATE_CONTRACT_COMMIT } from "./model-identity";

const CONFIRM_QUALIFICATION = "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING";
const CONFIRM_RESTORATION = "RESTORE_MODEL_IDENTITY_STAGING_JOURNAL";
const STATE_REPOSITORY = "leanprover/lean-eval-state-staging";
const SINGLETON = "model-identity-qualification-staging-global-v2";
const MAX_REQUEST_BYTES = 32 * 1024;
const RUN_ID = /^[1-9][0-9]{0,19}$/;
const SHA = /^[0-9a-f]{40}$/;
const JOURNAL_ID = /^mqj_[0-9a-f]{64}$/;

export type ModelIdentityQualificationEnv = Readonly<{
  DEPLOYED_COMMIT: string;
  DEPLOYMENT_ENVIRONMENT: "staging" | "production";
  INTAKE_ENABLED: string;
  INTAKE_ENABLEMENT_MODE?: string;
  MODEL_IDENTITY_OWNER_API_ENABLED?: string;
  MODEL_IDENTITY_MAINTAINER_API_ENABLED?: string;
  MODEL_IDENTITY_MAINTAINERS?: string;
  MODEL_IDENTITY_STATE_CONTRACT_COMMIT?: string;
  PROMOTION_CANARY_ENABLED?: string;
  STATE_REPOSITORY: string;
  GITHUB_STATE_TOKEN?: string;
  AUTH_TOKEN_SECRET?: string;
  MODEL_IDENTITY_QUALIFICATION_TOKEN?: string;
  MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET?: string;
  MODEL_IDENTITY_QUALIFICATION_EXECUTOR?: Fetcher;
  MODEL_IDENTITY_QUALIFICATION_JOURNAL?: DurableObjectNamespace<ModelIdentityQualificationJournal>;
}>;

export type ModelIdentityQualificationDependencies = Readonly<{
  stateFetch?: GitHubFetch;
  modelApiRequest?: (
    request: Request,
    maintainer: QualificationIdentity,
    occurredAtMilliseconds: number,
    context: QualificationExecutionContext,
  ) => Promise<Response>;
}>;

type QualificationExecutionContext = Readonly<{
  deployed_commit: string;
  run_id: string;
  run_attempt: 1;
  journal_id: string;
  journal_revision: number;
  operation: QualificationOperation;
  plan_digest: string;
  request_index: number;
}>;

export type QualificationIdentity = Readonly<{ github_id: number; login: string }>;

type RestoreRequest = Readonly<{
  schema_version: 2;
  operation: "restore";
  confirmation: typeof CONFIRM_RESTORATION;
  deployed_commit: string;
  run_id: string;
  run_attempt: 1;
  journal_id: string;
  expected_journal_revision: number;
  expected_state_commit: string;
  expected_state_tree: string;
}>;

type StepOperation = QualificationOperation;

type StepRequest = Readonly<{
  schema_version: 2;
  operation: StepOperation;
  confirmation: typeof CONFIRM_QUALIFICATION;
  deployed_commit: string;
  run_id: string;
  run_attempt: 1;
  journal_id: string;
  expected_journal_revision: number;
  expected_state_commit: string;
  expected_state_tree: string;
  intent: unknown;
}>;

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "cache-control": "no-store",
      "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
    },
  });
}

function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("qualification request is invalid");
  }
  return value as Record<string, unknown>;
}

function exactFields(value: Record<string, unknown>, fields: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new TypeError("qualification request fields are invalid");
  }
}

async function requestBody(request: Request): Promise<Record<string, unknown>> {
  const bytes = new Uint8Array(await request.arrayBuffer());
  if (bytes.byteLength > MAX_REQUEST_BYTES) throw new TypeError("qualification request is too large");
  try {
    return object(JSON.parse(new TextDecoder().decode(bytes)) as unknown);
  } catch (error) {
    if (error instanceof TypeError) throw error;
    throw new TypeError("qualification request JSON is invalid", { cause: error });
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

async function authorized(request: Request, env: ModelIdentityQualificationEnv): Promise<boolean> {
  const expected = env.MODEL_IDENTITY_QUALIFICATION_TOKEN;
  const header = request.headers.get("authorization");
  if (
    expected === undefined ||
    new TextEncoder().encode(expected).byteLength < 32 ||
    !header?.startsWith("Bearer ")
  ) return false;
  return equalSecret(header.slice("Bearer ".length), expected);
}

function exactDarkStaging(env: ModelIdentityQualificationEnv): boolean {
  return env.DEPLOYMENT_ENVIRONMENT === "staging" &&
    SHA.test(env.DEPLOYED_COMMIT) &&
    env.STATE_REPOSITORY === STATE_REPOSITORY &&
    env.INTAKE_ENABLED === "false" &&
    env.INTAKE_ENABLEMENT_MODE === "disabled" &&
    env.MODEL_IDENTITY_OWNER_API_ENABLED === "false" &&
    env.MODEL_IDENTITY_MAINTAINER_API_ENABLED === "false" &&
    env.MODEL_IDENTITY_MAINTAINERS === "[]" &&
    env.MODEL_IDENTITY_STATE_CONTRACT_COMMIT ===
      STAGING_MODEL_IDENTITY_STATE_CONTRACT_COMMIT &&
    env.PROMOTION_CANARY_ENABLED === "true" &&
    env.GITHUB_STATE_TOKEN !== undefined &&
    new TextEncoder().encode(env.GITHUB_STATE_TOKEN).byteLength >= 32 &&
    env.AUTH_TOKEN_SECRET !== undefined &&
    new TextEncoder().encode(env.AUTH_TOKEN_SECRET).byteLength >= 32 &&
    env.MODEL_IDENTITY_QUALIFICATION_JOURNAL !== undefined &&
    env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR !== undefined &&
    env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET !== undefined &&
    new TextEncoder().encode(env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET)
      .byteLength >= 32;
}

function positiveRevision(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1;
}

function runIdentity(body: Record<string, unknown>): { runId: string; runAttempt: 1 } {
  if (
    typeof body.run_id !== "string" ||
    !RUN_ID.test(body.run_id) ||
    body.run_attempt !== 1
  ) throw new TypeError("qualification run identity is invalid");
  return { runId: body.run_id, runAttempt: 1 };
}

function stub(env: ModelIdentityQualificationEnv) {
  const namespace = env.MODEL_IDENTITY_QUALIFICATION_JOURNAL;
  if (namespace === undefined) throw new Error("qualification journal is unavailable");
  return namespace.getByName(SINGLETON);
}

function stateFetch(dependencies: ModelIdentityQualificationDependencies): GitHubFetch {
  return dependencies.stateFetch ?? ((input, init) => fetch(input, {
    ...init,
    signal: init?.signal ?? AbortSignal.timeout(10_000),
  }));
}

function stateConfig(env: ModelIdentityQualificationEnv) {
  if (env.GITHUB_STATE_TOKEN === undefined) throw new Error("qualification State token is unavailable");
  return {
    repository: env.STATE_REPOSITORY,
    token: env.GITHUB_STATE_TOKEN,
    userAgent: "lean-eval-model-identity-qualification/2",
  };
}

async function acquire(
  body: Record<string, unknown>,
  env: ModelIdentityQualificationEnv,
  dependencies: ModelIdentityQualificationDependencies,
): Promise<Response> {
  exactFields(body, [
    "confirmation", "deployed_commit", "initial_state_commit", "initial_state_tree",
    "intent", "operation", "run_attempt", "run_id", "schema_version",
  ]);
  const { runId, runAttempt } = runIdentity(body);
  if (
    body.schema_version !== 2 ||
    body.operation !== "acquire" ||
    body.confirmation !== CONFIRM_QUALIFICATION ||
    body.deployed_commit !== env.DEPLOYED_COMMIT ||
    typeof body.initial_state_commit !== "string" ||
    !SHA.test(body.initial_state_commit) ||
    typeof body.initial_state_tree !== "string" ||
    !SHA.test(body.initial_state_tree)
  ) throw new TypeError("qualification acquisition is invalid");
  const snapshot = await qualificationStateSnapshot(
    stateConfig(env),
    stateFetch(dependencies),
  );
  if (
    snapshot.head_commit !== body.initial_state_commit ||
    snapshot.head_tree !== body.initial_state_tree
  ) throw new QualificationStateError("foreign_state_movement");
  const journal = await stub(env).acquire({
    schema_version: 2,
    run_id: runId,
    run_attempt: runAttempt,
    deployed_commit: env.DEPLOYED_COMMIT,
    initial_state_commit: body.initial_state_commit,
    initial_state_tree: body.initial_state_tree,
    intent: body.intent,
  });
  return json(journal);
}

async function journalStatus(
  body: Record<string, unknown>,
  env: ModelIdentityQualificationEnv,
  dependencies: ModelIdentityQualificationDependencies,
): Promise<Response> {
  exactFields(body, ["run_attempt", "run_id", "schema_version"]);
  const { runId } = runIdentity(body);
  if (body.schema_version !== 2) throw new TypeError("qualification status request is invalid");
  const journal = await reconcilePendingJournal(runId, env, dependencies);
  if (journal.deployed_commit !== env.DEPLOYED_COMMIT) {
    throw new QualificationStateError("foreign_state_movement");
  }
  return json(journal);
}

async function reconcilePendingJournal(
  runId: string,
  env: ModelIdentityQualificationEnv,
  dependencies: ModelIdentityQualificationDependencies,
): Promise<QualificationJournalStatus> {
  const journal = await stub(env).readStatus(runId);
  if (journal.lease_status !== "active") return journal;
  const recovery = JSON.parse(
    await stub(env).readRecoveryPlan(runId),
  ) as QualificationRecoveryPlan;
  const pending = recovery.pending_step;
  if (pending === null) return journal;
  const snapshot = await qualificationStateSnapshot(
    stateConfig(env),
    stateFetch(dependencies),
  );
  if (
    snapshot.head_commit === pending.expected_state_commit &&
    snapshot.head_tree === pending.expected_state_tree
  ) {
    return stub(env).abandonNonMutatingStep(
      runId,
      recovery.journal_id,
      recovery.journal_revision,
    );
  }
  const expectedMutations = pending.plan.api_requests.flatMap((request) =>
    request.expected_commit_message === null
      ? []
      : [{
          expectedMessage: request.expected_commit_message,
          expectedDocuments: request.expected_documents,
          expectedDeletedPaths: request.expected_deleted_paths,
          expectedTreeUnchanged: false,
        }]);
  const expectedPrefix = pending.plan.expected_state_prefix?.map((commit) => ({
    expectedMessage: commit.expected_message,
    expectedDocuments: commit.expected_documents,
    expectedDeletedPaths: commit.expected_deleted_paths,
    expectedTreeUnchanged: commit.expected_tree_unchanged,
  })) ?? expectedMutations;
  if (
    !pending.plan.mutation_expected ||
    expectedPrefix.length < 1 ||
    expectedMutations.length !== pending.plan.expected_commit_messages.length
  ) throw new QualificationStateError("foreign_state_movement");
  const mutation = await qualificationStateMutationPrefix(
    stateConfig(env),
    stateFetch(dependencies),
    {
      expectedParent: pending.expected_state_commit,
      expectedMutations: expectedPrefix,
    },
  );
  return stub(env).reconcilePendingMutation(
    runId,
    recovery.journal_id,
    recovery.journal_revision,
    mutation.state_commit,
    mutation.state_tree,
    mutation.applied_mutations,
  );
}

function restoreRequest(body: Record<string, unknown>, env: ModelIdentityQualificationEnv): RestoreRequest {
  exactFields(body, [
    "confirmation", "deployed_commit", "expected_journal_revision",
    "expected_state_commit", "expected_state_tree", "journal_id", "operation",
    "run_attempt", "run_id", "schema_version",
  ]);
  const { runId, runAttempt } = runIdentity(body);
  if (
    body.schema_version !== 2 ||
    body.operation !== "restore" ||
    body.confirmation !== CONFIRM_RESTORATION ||
    body.deployed_commit !== env.DEPLOYED_COMMIT ||
    typeof body.journal_id !== "string" ||
    !JOURNAL_ID.test(body.journal_id) ||
    !positiveRevision(body.expected_journal_revision) ||
    typeof body.expected_state_commit !== "string" ||
    !SHA.test(body.expected_state_commit) ||
    typeof body.expected_state_tree !== "string" ||
    !SHA.test(body.expected_state_tree)
  ) throw new TypeError("qualification restoration request is invalid");
  return {
    schema_version: 2,
    operation: "restore",
    confirmation: CONFIRM_RESTORATION,
    deployed_commit: env.DEPLOYED_COMMIT,
    run_id: runId,
    run_attempt: runAttempt,
    journal_id: body.journal_id,
    expected_journal_revision: body.expected_journal_revision,
    expected_state_commit: body.expected_state_commit,
    expected_state_tree: body.expected_state_tree,
  };
}

function stepRequest(body: Record<string, unknown>, env: ModelIdentityQualificationEnv): StepRequest {
  exactFields(body, [
    "confirmation", "deployed_commit", "expected_journal_revision",
    "expected_state_commit", "expected_state_tree", "intent", "journal_id",
    "operation", "run_attempt", "run_id", "schema_version",
  ]);
  const { runId, runAttempt } = runIdentity(body);
  if (
    body.schema_version !== 2 ||
    typeof body.operation !== "string" ||
    !QUALIFICATION_OPERATIONS.includes(body.operation as QualificationOperation) ||
    body.confirmation !== CONFIRM_QUALIFICATION ||
    body.deployed_commit !== env.DEPLOYED_COMMIT ||
    typeof body.journal_id !== "string" ||
    !JOURNAL_ID.test(body.journal_id) ||
    !positiveRevision(body.expected_journal_revision) ||
    typeof body.expected_state_commit !== "string" ||
    !SHA.test(body.expected_state_commit) ||
    typeof body.expected_state_tree !== "string" ||
    !SHA.test(body.expected_state_tree)
  ) throw new TypeError("qualification step request is invalid");
  return {
    schema_version: 2,
    operation: body.operation as QualificationOperation,
    confirmation: CONFIRM_QUALIFICATION,
    deployed_commit: env.DEPLOYED_COMMIT,
    run_id: runId,
    run_attempt: runAttempt,
    journal_id: body.journal_id,
    expected_journal_revision: body.expected_journal_revision,
    expected_state_commit: body.expected_state_commit,
    expected_state_tree: body.expected_state_tree,
    intent: body.intent,
  };
}

const SESSION_HEADERS = {
  oauth_session_identity: "x-lean-eval-oauth-session",
  agent_session_identity: "x-lean-eval-agent-session",
  owner_request: "x-lean-eval-oauth-session",
  maintainer_approve: "x-lean-eval-maintainer-session",
  maintainer_reject: "x-lean-eval-maintainer-session",
  alias_assignment: "x-lean-eval-agent-session",
  identity_rename: "x-lean-eval-oauth-session",
  complete_graph_consolidation: "x-lean-eval-agent-session",
  chained_terminal_retry: "x-lean-eval-agent-session",
  component_cap_refusal: "x-lean-eval-oauth-session",
  idempotent_retry: "x-lean-eval-oauth-session",
  cross_route_event_collision: "x-lean-eval-oauth-session",
  cross_owner_denial: "x-lean-eval-cross-owner-session",
  maximal_contention_measurement: "x-lean-eval-agent-session",
} as const;

function stepCredential(request: Request, operation: StepOperation): string {
  const expected = SESSION_HEADERS[operation];
  for (const header of [
    "x-lean-eval-oauth-session",
    "x-lean-eval-agent-session",
    "x-lean-eval-cross-owner-session",
    "x-lean-eval-maintainer-session",
  ]) {
    const value = request.headers.get(header);
    if (header === expected) {
      if (value === null) throw new AuthError("qualification session is missing");
    } else if (value !== null) {
      throw new AuthError("qualification session role is invalid");
    }
  }
  const credential = request.headers.get(expected);
  if (credential === null) throw new AuthError("qualification session is missing");
  return credential;
}

function identity(value: unknown): QualificationIdentity {
  const input = object(value);
  exactFields(input, ["github_id", "login"]);
  if (
    typeof input.github_id !== "number" ||
    !Number.isSafeInteger(input.github_id) ||
    input.github_id < 1 ||
    typeof input.login !== "string"
  ) throw new TypeError("qualification owner identity is invalid");
  return { github_id: input.github_id, login: input.login };
}

function exactIntent(left: unknown, right: unknown): boolean {
  try {
    return canonicalQualificationValue(left) === canonicalQualificationValue(right);
  } catch {
    return false;
  }
}

async function responseBody(response: Response): Promise<Record<string, unknown>> {
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > MAX_REQUEST_BYTES) {
    throw new QualificationStateError("provider_unavailable");
  }
  try {
    return object(JSON.parse(new TextDecoder().decode(bytes)) as unknown);
  } catch (error) {
    if (error instanceof QualificationStateError) throw error;
    throw new QualificationStateError("provider_unavailable");
  }
}

type ExecutorMeasurements = Readonly<{
  subrequests: number;
  gitObjectWrites: number;
  casAttempts: number;
}>;

function executorMeasurements(response: Response): ExecutorMeasurements | null {
  const values = [
    response.headers.get("x-lean-eval-qualification-subrequests"),
    response.headers.get("x-lean-eval-qualification-git-object-writes"),
    response.headers.get("x-lean-eval-qualification-cas-attempts"),
  ];
  if (values.every((value) => value === null)) return null;
  if (values.some((value) => value === null)) {
    throw new QualificationStateError("provider_unavailable");
  }
  const numbers = values.map((value) => Number(value));
  if (numbers.some((value) =>
    !Number.isSafeInteger(value) || value < 0 || value > 400)) {
    throw new QualificationStateError("provider_unavailable");
  }
  const subrequests = numbers[0] ?? -1;
  const gitObjectWrites = numbers[1] ?? -1;
  const casAttempts = numbers[2] ?? -1;
  if (gitObjectWrites > subrequests || casAttempts > subrequests) {
    throw new QualificationStateError("provider_unavailable");
  }
  return { subrequests, gitObjectWrites, casAttempts };
}

async function invokeQualificationKernel(
  env: ModelIdentityQualificationEnv,
  dependencies: ModelIdentityQualificationDependencies,
  operation: QualificationApiRequestPlan,
  session: string,
  maintainer: QualificationIdentity,
  context: QualificationExecutionContext,
): Promise<Response> {
  if (dependencies.modelApiRequest !== undefined) {
    return dependencies.modelApiRequest(
      new Request(`https://qualification.invalid${operation.path}`, {
        method: operation.method,
        headers: {
          authorization: `Bearer ${session}`,
          "content-type": "application/json",
          "idempotency-key": operation.event_id,
        },
        body: JSON.stringify(operation.body),
      }),
      maintainer,
      Date.parse(operation.occurred_at),
      context,
    );
  }
  const executor = env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR;
  const secret = env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET;
  if (executor === undefined || secret === undefined) {
    throw new QualificationStateError("provider_unavailable");
  }
  const issuedAt = Math.floor(Date.now() / 1000);
  const capability = await signQualificationExecutorCapability(secret, {
    schema_version: 1,
    kind: "model_identity_qualification_executor",
    ...context,
    request_digest: await qualificationApiRequestDigest(operation),
    issued_at: issuedAt,
    expires_at: issuedAt + 60,
  });
  return executor.fetch("https://qualification-executor.invalid/internal/v1/execute", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ capability, maintainer, request: operation, session }),
  });
}

async function proveSession(
  request: Request,
  step: StepRequest,
  env: ModelIdentityQualificationEnv,
): Promise<Response> {
  if (
    step.operation !== "oauth_session_identity" &&
    step.operation !== "agent_session_identity"
  ) {
    throw new TypeError("qualification session operation is invalid");
  }
  const journal = await stub(env).readStatus(step.run_id);
  const plan = JSON.parse(await stub(env).readRecoveryPlan(step.run_id)) as QualificationRecoveryPlan;
  if (
    journal.deployed_commit !== env.DEPLOYED_COMMIT ||
    journal.journal_id !== step.journal_id ||
    journal.journal_revision !== step.expected_journal_revision ||
    journal.current_state_commit !== step.expected_state_commit ||
    journal.current_state_tree !== step.expected_state_tree ||
    !exactIntent(plan.intent, step.intent)
  ) throw new QualificationStateError("foreign_state_movement");
  const owner = identity(object(step.intent).owner);
  const secret = env.AUTH_TOKEN_SECRET;
  if (secret === undefined) throw new AuthError("qualification session verifier is unavailable");
  const credential = stepCredential(request, step.operation);
  const authenticated = step.operation === "oauth_session_identity"
    ? await verifyToken<BrowserSession>(secret, credential, "browser_session")
    : await verifyToken<AgentSession>(secret, credential, "agent_session");
  if (authenticated.github_id !== owner.github_id || authenticated.login !== owner.login) {
    throw new AuthError("qualification session identity changed");
  }
  const reservation = {
    run_id: step.run_id,
    run_attempt: step.run_attempt,
    journal_id: step.journal_id,
    expected_journal_revision: step.expected_journal_revision,
    expected_state_commit: step.expected_state_commit,
    expected_state_tree: step.expected_state_tree,
    operation: step.operation,
  };
  const reserved = await stub(env).reserveStep(reservation);
  if (reserved.kind === "completed") {
    return json(JSON.parse(reserved.receipt_json) as unknown);
  }
  const durablePlan = JSON.parse(reserved.plan_json) as QualificationStepPlan;
  if (
    durablePlan.operation !== step.operation ||
    !exactIntent(durablePlan.actor, owner) ||
    durablePlan.api_requests.length !== 0
  ) throw new QualificationStateError("foreign_state_movement");
  const receipt = {
    schema_version: 2,
    status: "model_identity_qualification_step_verified",
    deployed_commit: env.DEPLOYED_COMMIT,
    run_id: step.run_id,
    run_attempt: step.run_attempt,
    journal_id: step.journal_id,
    journal_revision: step.expected_journal_revision + 1,
    previous_state_commit: step.expected_state_commit,
    previous_state_tree: step.expected_state_tree,
    state_commit: step.expected_state_commit,
    state_tree: step.expected_state_tree,
    owner_api_enabled: false,
    maintainer_api_enabled: false,
    mutation_created: false,
    subrequests: null,
    cas_attempts: null,
    proof: {
      operation: step.operation,
      route: durablePlan.route,
      credential_roles: durablePlan.credential_roles,
      actor: durablePlan.actor,
      http_status: durablePlan.expected_http_status,
      event_ids: durablePlan.event_ids,
      model_ids: durablePlan.model_ids,
      alias_keys: durablePlan.alias_keys,
      assertions: durablePlan.assertions,
    },
  };
  const receiptJson = await stub(env).completeStep({
    reservation,
    state_commit: step.expected_state_commit,
    state_tree: step.expected_state_tree,
    receipt,
  });
  return json(JSON.parse(receiptJson) as unknown);
}

async function executePlannedStep(
  request: Request,
  step: StepRequest,
  env: ModelIdentityQualificationEnv,
  dependencies: ModelIdentityQualificationDependencies,
): Promise<Response> {
  const journal = await stub(env).readStatus(step.run_id);
  const recovery = JSON.parse(
    await stub(env).readRecoveryPlan(step.run_id),
  ) as QualificationRecoveryPlan;
  if (
    journal.deployed_commit !== env.DEPLOYED_COMMIT ||
    journal.journal_id !== step.journal_id ||
    journal.journal_revision !== step.expected_journal_revision ||
    journal.current_state_commit !== step.expected_state_commit ||
    journal.current_state_tree !== step.expected_state_tree ||
    !exactIntent(recovery.intent, step.intent)
  ) throw new QualificationStateError("foreign_state_movement");
  const intent = object(step.intent);
  const owner = identity(intent.owner);
  const crossOwner = identity(intent.cross_owner);
  const maintainer = identity(object(step.intent).maintainer);
  const secret = env.AUTH_TOKEN_SECRET;
  if (secret === undefined) throw new AuthError("qualification session verifier is unavailable");
  const credential = stepCredential(request, step.operation);
  const reservation = {
    run_id: step.run_id,
    run_attempt: step.run_attempt,
    journal_id: step.journal_id,
    expected_journal_revision: step.expected_journal_revision,
    expected_state_commit: step.expected_state_commit,
    expected_state_tree: step.expected_state_tree,
    operation: step.operation,
  };
  const reserved = await stub(env).reserveStep(reservation);
  if (reserved.kind === "completed") {
    return json(JSON.parse(reserved.receipt_json) as unknown);
  }
  const plan = JSON.parse(reserved.plan_json) as QualificationStepPlan;
  const credentialRole = plan.credential_roles[0];
  const expectedActor = credentialRole === "maintainer"
    ? maintainer
    : credentialRole === "cross_owner"
      ? crossOwner
      : owner;
  if (
    plan.operation !== step.operation ||
    credentialRole === undefined ||
    plan.credential_roles.length !== 1 ||
    !exactIntent(plan.actor, expectedActor) ||
    plan.api_requests.some((operationRequest) =>
      operationRequest.credential_role !== credentialRole ||
      !exactIntent(operationRequest.actor, expectedActor))
  ) {
    throw new QualificationStateError("foreign_state_movement");
  }
  const authenticated = credentialRole === "agent_owner"
    ? await verifyToken<AgentSession>(secret, credential, "agent_session")
    : await verifyToken<BrowserSession>(secret, credential, "browser_session");
  if (
    authenticated.github_id !== expectedActor.github_id ||
    authenticated.login !== expectedActor.login
  ) throw new AuthError("qualification session identity changed");
  const before = await qualificationStateSnapshot(
    stateConfig(env),
    stateFetch(dependencies),
  );
  if (
    before.head_commit !== step.expected_state_commit ||
    before.head_tree !== step.expected_state_tree
  ) throw new QualificationStateError("foreign_state_movement");
  let apiStatus = plan.expected_http_status;
  let lastMeasurements: ExecutorMeasurements | null = null;
  let responseLost = false;
  for (const [requestIndex, operationRequest] of plan.api_requests.entries()) {
    let apiResponse: Response;
    try {
      apiResponse = await invokeQualificationKernel(
        env,
        dependencies,
        operationRequest,
        credential,
        maintainer,
        {
          deployed_commit: env.DEPLOYED_COMMIT,
          run_id: step.run_id,
          run_attempt: step.run_attempt,
          journal_id: step.journal_id,
          journal_revision: step.expected_journal_revision,
          operation: step.operation,
          plan_digest: reserved.plan_digest,
          request_index: requestIndex,
        },
      );
    } catch {
      responseLost = true;
      break;
    }
    apiStatus = apiResponse.status;
    lastMeasurements = executorMeasurements(apiResponse);
    if (apiStatus !== operationRequest.expected_http_status) {
      throw new QualificationStateError("provider_unavailable");
    }
    const outcome = await responseBody(apiResponse);
    if (
      canonicalQualificationValue(outcome) !==
      canonicalQualificationValue(operationRequest.expected_response)
    ) throw new QualificationStateError("provider_unavailable");
  }
  const expectedMutations = plan.api_requests.flatMap((operationRequest) =>
    operationRequest.expected_commit_message === null
      ? []
      : [{
          expectedMessage: operationRequest.expected_commit_message,
          expectedDocuments: operationRequest.expected_documents,
          expectedDeletedPaths: operationRequest.expected_deleted_paths,
          expectedTreeUnchanged: false,
        }]);
  const expectedPrefix = plan.expected_state_prefix?.map((commit) => ({
    expectedMessage: commit.expected_message,
    expectedDocuments: commit.expected_documents,
    expectedDeletedPaths: commit.expected_deleted_paths,
    expectedTreeUnchanged: commit.expected_tree_unchanged,
  })) ?? expectedMutations;
  let stateCommit = step.expected_state_commit;
  let stateTree = step.expected_state_tree;
  if (
    plan.mutation_expected
  ) {
    if (expectedPrefix.length < 1) {
      throw new QualificationStateError("foreign_state_movement");
    }
    const mutation = await qualificationStateMutationSequence(
      stateConfig(env),
      stateFetch(dependencies),
      { expectedParent: step.expected_state_commit, expectedMutations: expectedPrefix },
    );
    stateCommit = mutation.state_commit;
    stateTree = mutation.state_tree;
  } else {
    const after = await qualificationStateSnapshot(
      stateConfig(env),
      stateFetch(dependencies),
    );
    if (
      after.head_commit !== step.expected_state_commit ||
      after.head_tree !== step.expected_state_tree ||
      expectedMutations.length !== 0 ||
      lastMeasurements?.gitObjectWrites !== 0
    ) throw new QualificationStateError("foreign_state_movement");
  }
  if (responseLost && !plan.mutation_expected) {
    throw new QualificationStateError("provider_unavailable");
  }
  const receipt = {
    schema_version: 2,
    status: "model_identity_qualification_step_verified",
    deployed_commit: env.DEPLOYED_COMMIT,
    run_id: step.run_id,
    run_attempt: step.run_attempt,
    journal_id: step.journal_id,
    journal_revision: step.expected_journal_revision + 1,
    previous_state_commit: step.expected_state_commit,
    previous_state_tree: step.expected_state_tree,
    state_commit: stateCommit,
    state_tree: stateTree,
    owner_api_enabled: false,
    maintainer_api_enabled: false,
    mutation_created: plan.mutation_expected,
    subrequests: step.operation === "maximal_contention_measurement"
      ? lastMeasurements?.subrequests ?? null
      : null,
    cas_attempts: step.operation === "maximal_contention_measurement"
      ? lastMeasurements?.casAttempts ?? null
      : null,
    proof: {
      operation: step.operation,
      route: plan.route,
      credential_roles: plan.credential_roles,
      actor: plan.actor,
      http_status: apiStatus,
      event_ids: plan.event_ids,
      model_ids: plan.model_ids,
      alias_keys: plan.alias_keys,
      assertions: plan.assertions,
    },
  };
  const receiptJson = await stub(env).completeStep({
    reservation,
    state_commit: stateCommit,
    state_tree: stateTree,
    receipt,
  });
  return json(JSON.parse(receiptJson) as unknown);
}

function restoredResponse(
  request: RestoreRequest,
  journal: QualificationJournalStatus,
): Response {
  if (
    journal.lease_status !== "restored" ||
    journal.journal_id !== request.journal_id ||
    journal.journal_revision !== request.expected_journal_revision + 1 ||
    journal.restoration_parent_commit !== request.expected_state_commit ||
    journal.restoration_parent_tree !== request.expected_state_tree ||
    journal.restoration_commit === null ||
    journal.restoration_tree === null
  ) throw new QualificationStateError("foreign_state_movement");
  return json({
    schema_version: 2,
    status: "model_identity_qualification_restored",
    deployed_commit: journal.deployed_commit,
    run_id: journal.run_id,
    run_attempt: journal.run_attempt,
    journal_id: journal.journal_id,
    journal_revision: journal.journal_revision,
    initial_state_commit: journal.initial_state_commit,
    initial_state_tree: journal.initial_state_tree,
    restoration_parent_commit: journal.restoration_parent_commit,
    restoration_parent_tree: journal.restoration_parent_tree,
    restoration_commit: journal.restoration_commit,
    restoration_tree: journal.restoration_tree,
    ref_head: journal.restoration_commit,
    fast_forward: true,
    tree_equal: true,
    lease_released: true,
    foreign_commit_observed: false,
    owner_api_enabled: false,
    maintainer_api_enabled: false,
  });
}

async function restore(
  body: Record<string, unknown>,
  env: ModelIdentityQualificationEnv,
  dependencies: ModelIdentityQualificationDependencies,
): Promise<Response> {
  const request = restoreRequest(body, env);
  const journal = await stub(env).readStatus(request.run_id);
  if (journal.deployed_commit !== env.DEPLOYED_COMMIT) {
    throw new QualificationStateError("foreign_state_movement");
  }
  if (journal.lease_status === "restored") return restoredResponse(request, journal);
  const plan = JSON.parse(await stub(env).readRecoveryPlan(request.run_id)) as QualificationRecoveryPlan;
  if (
    plan.pending_step?.operation === "oauth_session_identity" ||
    plan.pending_step?.operation === "agent_session_identity"
  ) {
    const snapshot = await qualificationStateSnapshot(
      stateConfig(env),
      stateFetch(dependencies),
    );
    if (
      snapshot.head_commit !== plan.current_state_commit ||
      snapshot.head_tree !== plan.current_state_tree
    ) throw new QualificationStateError("foreign_state_movement");
    await stub(env).abandonNonMutatingStep(
      request.run_id,
      request.journal_id,
      request.expected_journal_revision,
    );
    return await restore(body, env, dependencies);
  }
  if (
    plan.journal_id !== request.journal_id ||
    plan.journal_revision !== request.expected_journal_revision ||
    plan.current_state_commit !== request.expected_state_commit ||
    plan.current_state_tree !== request.expected_state_tree ||
    plan.pending_step !== null
  ) throw new QualificationStateError("foreign_state_movement");
  const restoration = await restoreQualificationState(
    stateConfig(env),
    stateFetch(dependencies),
    {
      journalId: plan.journal_id,
      recoveryNonce: plan.recovery_nonce,
      expectedHead: plan.current_state_commit,
      expectedTree: plan.current_state_tree,
      initialTree: plan.initial_state_tree,
    },
  );
  const completed = await stub(env).completeRestoration(
    request.run_id,
    request.expected_journal_revision,
    restoration,
  );
  return restoredResponse(request, completed);
}

export async function handleModelIdentityQualificationRequest(
  request: Request,
  env: ModelIdentityQualificationEnv,
  dependencies: ModelIdentityQualificationDependencies = {},
): Promise<Response> {
  if (
    request.method !== "POST" ||
    !exactDarkStaging(env) ||
    !(await authorized(request, env))
  ) return json({ error: "not_found" }, 404);
  try {
    const body = await requestBody(request);
    if (body.operation === "acquire") return await acquire(body, env, dependencies);
    if (body.operation === "restore") return await restore(body, env, dependencies);
    if (body.operation === "oauth_session_identity" || body.operation === "agent_session_identity") {
      return await proveSession(request, stepRequest(body, env), env);
    }
    if (
      typeof body.operation === "string" &&
      QUALIFICATION_OPERATIONS.slice(2).includes(
        body.operation as QualificationOperation,
      )
    ) {
      return await executePlannedStep(
        request,
        stepRequest(body, env),
        env,
        dependencies,
      );
    }
    if (!("operation" in body)) {
      return await journalStatus(body, env, dependencies);
    }
    return json({ error: "not_found" }, 404);
  } catch (error) {
    if (error instanceof TypeError) return json({ error: "invalid_request" }, 400);
    if (error instanceof AuthError) return json({ error: "authentication_failed" }, 401);
    if (error instanceof QualificationStateError) {
      return json({ error: error.reason }, error.reason === "foreign_state_movement" ? 409 : 503);
    }
    return json({ error: "qualification_unavailable" }, 503);
  }
}
