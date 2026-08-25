import type {
  ModelIdentityQualificationJournal,
  QualificationJournalStatus,
  QualificationRecoveryPlan,
} from "./model-identity-qualification-journal";
import {
  qualificationStateMutation,
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
import { isUuidV7 } from "./api-contract";
import {
  modelIdentityId,
  STAGING_MODEL_IDENTITY_STATE_CONTRACT_COMMIT,
} from "./model-identity";

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
  MODEL_IDENTITY_QUALIFICATION_JOURNAL?: DurableObjectNamespace<ModelIdentityQualificationJournal>;
}>;

export type ModelIdentityQualificationDependencies = Readonly<{
  stateFetch?: GitHubFetch;
  modelApiRequest?: (
    request: Request,
    maintainer: QualificationIdentity,
  ) => Promise<Response>;
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

type StepOperation =
  | "oauth_session_identity"
  | "agent_session_identity"
  | "owner_request";

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
  request: unknown;
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
    env.MODEL_IDENTITY_QUALIFICATION_JOURNAL !== undefined;
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
): Promise<Response> {
  exactFields(body, ["run_attempt", "run_id", "schema_version"]);
  const { runId } = runIdentity(body);
  if (body.schema_version !== 2) throw new TypeError("qualification status request is invalid");
  const journal = await stub(env).readStatus(runId);
  if (journal.deployed_commit !== env.DEPLOYED_COMMIT) {
    throw new QualificationStateError("foreign_state_movement");
  }
  return json(journal);
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
    "operation", "request", "run_attempt", "run_id", "schema_version",
  ]);
  const { runId, runAttempt } = runIdentity(body);
  if (
    body.schema_version !== 2 ||
    (
      body.operation !== "oauth_session_identity" &&
      body.operation !== "agent_session_identity" &&
      body.operation !== "owner_request"
    ) ||
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
    operation: body.operation,
    confirmation: CONFIRM_QUALIFICATION,
    deployed_commit: env.DEPLOYED_COMMIT,
    run_id: runId,
    run_attempt: runAttempt,
    journal_id: body.journal_id,
    expected_journal_revision: body.expected_journal_revision,
    expected_state_commit: body.expected_state_commit,
    expected_state_tree: body.expected_state_tree,
    intent: body.intent,
    request: body.request,
  };
}

const SESSION_HEADERS = {
  oauth_session_identity: "x-lean-eval-oauth-session",
  agent_session_identity: "x-lean-eval-agent-session",
  owner_request: "x-lean-eval-oauth-session",
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

function emptyStepRequest(value: unknown): void {
  exactFields(object(value), []);
}

function ownerRequest(value: unknown): { event_id: string; display_name: string } {
  const input = object(value);
  exactFields(input, ["display_name", "event_id"]);
  if (
    typeof input.event_id !== "string" ||
    !isUuidV7(input.event_id) ||
    typeof input.display_name !== "string" ||
    input.display_name.length < 1 ||
    new TextEncoder().encode(input.display_name).byteLength > 200
  ) throw new TypeError("qualification owner request is invalid");
  return { event_id: input.event_id, display_name: input.display_name };
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

async function proveSession(
  request: Request,
  step: StepRequest,
  env: ModelIdentityQualificationEnv,
): Promise<Response> {
  if (step.operation === "owner_request") {
    throw new TypeError("qualification session operation is invalid");
  }
  emptyStepRequest(step.request);
  const journal = await stub(env).readStatus(step.run_id);
  const plan = JSON.parse(await stub(env).readRecoveryPlan(step.run_id)) as QualificationRecoveryPlan;
  if (
    journal.deployed_commit !== env.DEPLOYED_COMMIT ||
    journal.journal_id !== step.journal_id ||
    journal.journal_revision !== step.expected_journal_revision ||
    journal.current_state_commit !== step.expected_state_commit ||
    journal.current_state_tree !== step.expected_state_tree ||
    JSON.stringify(plan.intent) !== JSON.stringify(step.intent)
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
  const route = step.operation === "oauth_session_identity"
    ? "session/oauth-owner"
    : "session/agent-owner";
  const credentialRole = step.operation === "oauth_session_identity"
    ? "oauth_owner"
    : "agent_owner";
  const assertions = step.operation === "oauth_session_identity"
    ? {
        browser_session_signature_verified: true,
        exact_identity_verified: true,
        session_unexpired: true,
      }
    : {
        agent_source_commit_bound: true,
        browser_session_signature_verified: true,
        exact_identity_verified: true,
      };
  const reservation = {
    run_id: step.run_id,
    run_attempt: step.run_attempt,
    journal_id: step.journal_id,
    expected_journal_revision: step.expected_journal_revision,
    expected_state_commit: step.expected_state_commit,
    expected_state_tree: step.expected_state_tree,
    operation: step.operation,
    operation_request: step,
  };
  const reserved = await stub(env).reserveStep(reservation);
  if (reserved.kind === "completed") {
    return json(JSON.parse(reserved.receipt_json) as unknown);
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
    state_commit: step.expected_state_commit,
    state_tree: step.expected_state_tree,
    owner_api_enabled: false,
    maintainer_api_enabled: false,
    mutation_created: false,
    subrequests: null,
    cas_attempts: null,
    proof: {
      operation: step.operation,
      route,
      credential_roles: [credentialRole],
      actor: owner,
      http_status: 200,
      event_ids: [],
      model_ids: [],
      alias_keys: [],
      assertions,
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

async function requestOwnerIdentity(
  request: Request,
  step: StepRequest,
  env: ModelIdentityQualificationEnv,
  dependencies: ModelIdentityQualificationDependencies,
): Promise<Response> {
  if (step.operation !== "owner_request") {
    throw new TypeError("qualification owner operation is invalid");
  }
  const journal = await stub(env).readStatus(step.run_id);
  const plan = JSON.parse(await stub(env).readRecoveryPlan(step.run_id)) as QualificationRecoveryPlan;
  if (
    journal.deployed_commit !== env.DEPLOYED_COMMIT ||
    journal.journal_id !== step.journal_id ||
    journal.journal_revision !== step.expected_journal_revision ||
    journal.current_state_commit !== step.expected_state_commit ||
    journal.current_state_tree !== step.expected_state_tree ||
    JSON.stringify(plan.intent) !== JSON.stringify(step.intent)
  ) throw new QualificationStateError("foreign_state_movement");
  const owner = identity(object(step.intent).owner);
  const maintainer = identity(object(step.intent).maintainer);
  const operation = ownerRequest(step.request);
  const expectedModelId = await modelIdentityId(operation.event_id);
  const secret = env.AUTH_TOKEN_SECRET;
  if (secret === undefined) throw new AuthError("qualification session verifier is unavailable");
  const credential = stepCredential(request, step.operation);
  const authenticated = await verifyToken<BrowserSession>(
    secret,
    credential,
    "browser_session",
  );
  if (authenticated.github_id !== owner.github_id || authenticated.login !== owner.login) {
    throw new AuthError("qualification session identity changed");
  }
  const operationRequest = {
    actor: owner,
    body: { display_name: operation.display_name },
    idempotency_key: operation.event_id,
    method: "POST",
    path: "/api/v1/model-identities",
  };
  const reservation = {
    run_id: step.run_id,
    run_attempt: step.run_attempt,
    journal_id: step.journal_id,
    expected_journal_revision: step.expected_journal_revision,
    expected_state_commit: step.expected_state_commit,
    expected_state_tree: step.expected_state_tree,
    operation: step.operation,
    operation_request: operationRequest,
  };
  const reserved = await stub(env).reserveStep(reservation);
  if (reserved.kind === "completed") {
    return json(JSON.parse(reserved.receipt_json) as unknown);
  }
  if (plan.pending_step === null) {
    const before = await qualificationStateSnapshot(
      stateConfig(env),
      stateFetch(dependencies),
    );
    if (
      before.head_commit !== step.expected_state_commit ||
      before.head_tree !== step.expected_state_tree
    ) throw new QualificationStateError("foreign_state_movement");
  }
  const invoke = dependencies.modelApiRequest;
  if (invoke === undefined) throw new QualificationStateError("provider_unavailable");
  const apiResponse = await invoke(new Request(
    `https://qualification.invalid${operationRequest.path}`,
    {
      method: operationRequest.method,
      headers: {
        authorization: `Bearer ${credential}`,
        "content-type": "application/json",
        "idempotency-key": operationRequest.idempotency_key,
      },
      body: JSON.stringify(operationRequest.body),
    },
  ), maintainer);
  if (apiResponse.status !== 200 && apiResponse.status !== 201) {
    throw new QualificationStateError("provider_unavailable");
  }
  const outcome = await responseBody(apiResponse);
  exactFields(outcome, ["model_id", "status"]);
  const expectedStatus = apiResponse.status === 201
    ? "identity_requested"
    : "identity_already_requested";
  if (outcome.model_id !== expectedModelId || outcome.status !== expectedStatus) {
    throw new QualificationStateError("provider_unavailable");
  }
  const mutation = await qualificationStateMutation(
    stateConfig(env),
    stateFetch(dependencies),
    {
      expectedParent: step.expected_state_commit,
      expectedMessage: `Request model identity ${expectedModelId}`,
    },
  );
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
    state_commit: mutation.state_commit,
    state_tree: mutation.state_tree,
    owner_api_enabled: false,
    maintainer_api_enabled: false,
    mutation_created: true,
    subrequests: null,
    cas_attempts: null,
    proof: {
      operation: step.operation,
      route: operationRequest.path,
      credential_roles: ["oauth_owner"],
      actor: owner,
      http_status: apiResponse.status,
      event_ids: [operation.event_id],
      model_ids: [expectedModelId],
      alias_keys: [],
      assertions: {
        exact_idempotency_key_verified: true,
        exact_identity_verified: true,
        fixed_public_api_kernel_invoked: true,
        idempotent_response: apiResponse.status === 200,
        single_parent_state_commit_verified: true,
      },
    },
  };
  const receiptJson = await stub(env).completeStep({
    reservation,
    state_commit: mutation.state_commit,
    state_tree: mutation.state_tree,
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
    if (body.operation === "owner_request") {
      return await requestOwnerIdentity(
        request,
        stepRequest(body, env),
        env,
        dependencies,
      );
    }
    if (!("operation" in body)) return await journalStatus(body, env);
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
