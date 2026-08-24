import { describe, expect, it, vi } from "vitest";

import {
  ApiDecodeError,
  assertSourcePolicy,
  decodeArchiveCompletion,
  decodeEmptyObject,
  decodeProblemRepairDecision,
  decodeResultRetractionDecision,
  decodeResultRetractionOverride,
  decodeResultCompletion,
  decodeSourceReaderPreflight,
  decodeSubmissionInput,
  readJson,
} from "../src/api-contract";
import {
  lifecycleEventId,
  makeSubmissionGrant,
  nonceDigest,
  signToken,
  verifyToken,
  type BrowserSession,
  type SubmissionGrant,
} from "../src/auth";
import { handleRequest, handleScheduled, type RuntimeEnv, type StateAccess } from "../src/app";
import {
  buildDispatchRequest,
  buildPromotionCanaryDispatchRequest,
  GitHubProvider,
} from "../src/github-provider";
import {
  GitHubStateError,
  ResultIdentityCollisionError,
  ResultOwnerStateError,
  StateEventConflictError,
  StateUpdateOutcomeUnknownError,
  type GitHubFetch,
  type LegacyResultBackfillRequest,
  type LegacyResultClaimRequest,
  type ResultProblemRepairRequest,
  type ResultProblemRepairDecisionRequest,
  type ResultRetractionDecisionRequest,
  type ResultRetractionFinalizationRequest,
  type ResultRetractionOverrideRequest,
  type ResultRetractionRequest,
} from "../src/github-state";
import {
  ScheduledSubrequestBudget,
  ScheduledSubrequestBudgetError,
} from "../src/scheduled-subrequest-budget";
import {
  initialResultAmendmentView,
  requestedProblemRepairView,
  type ComparatorEvidence,
  type ResultAmendmentView,
} from "../src/result-amendment";
import {
  validateStateEvent,
  type WritableResultLifecycleEvent,
  type WritableStateEvent,
  type WritableSubmissionLifecycleEvent,
} from "../src/state-event";
import {
  decodeDispatchOutbox,
  decodeSubmissionView,
  latestLifecycleEventId,
  type DispatchOutbox,
  type SubmissionView,
} from "../src/submission-view";

const SECRET = "test-secret-with-at-least-thirty-two-bytes";
const NOW_MS = 1_777_777_777_000;
const ENV = {
  API_RATE_LIMITER: { limit: () => Promise.resolve({ success: true }) },
  AUTH_TOKEN_SECRET: SECRET,
  DEPLOYED_COMMIT: "test-commit",
  DEPLOYMENT_ENVIRONMENT: "staging",
  DISPATCH_REPOSITORY: "leanprover/lean-eval-submissions",
  DISPATCH_WORKFLOW: "submission.yml",
  DISPATCH_WORKFLOW_REF: `lean-eval-dispatch/${"b".repeat(40)}`,
  INTAKE_ENABLED: "true",
  INTAKE_ENABLEMENT_MODE: "durable",
  LIFECYCLE_CALLBACK_TOKEN: "callback-token-with-at-least-thirty-two-bytes",
  STATE_REPOSITORY: "leanprover/state-staging",
} satisfies RuntimeEnv;
const LIFECYCLE = { waitUntil: () => undefined };
const SUBMISSION = {
  problem_id: "two_plus_two",
  problem_group: "formalization-evaluation",
  statement_revision: 2,
  declared_model: "Example Model",
  source_repository: "alice/proofs",
  source_commit: "a".repeat(40),
  source_visibility: "private",
  publication_choice: "scheduled",
  production_metadata: { web_access: false, input_tokens: 123 },
} as const;

function reachableLegacyResultFetch(contents: typeof fetch): typeof fetch {
  return (input, init) => {
    const url = input instanceof Request ? input.url : input.toString();
    const expected = new Headers(init?.headers).get("x-lean-eval-expected-commit");
    if (url.endsWith("/branches/staging-results")) {
      return Promise.resolve(Response.json({
        name: "staging-results",
        protected: true,
        commit: { sha: "f".repeat(40) },
      }));
    }
    if (url.endsWith(`/compare/${expected ?? ""}...staging-results`)) {
      return Promise.resolve(Response.json({
        status: "ahead",
        base_commit: { sha: expected },
        merge_base_commit: { sha: expected },
        head_commit: { sha: "f".repeat(40) },
      }));
    }
    return contents(input, init);
  };
}

class MemoryState implements StateAccess {
  readonly events: WritableStateEvent[] = [];
  readonly views = new Map<string, SubmissionView>();
  readonly outbox = new Map<string, DispatchOutbox>();
  created = true;
  head = "d".repeat(40);
  readonly legacyClaims: LegacyResultClaimRequest[] = [];
  readonly legacyBackfills: LegacyResultBackfillRequest[] = [];
  readonly problemRepairRequests: ResultProblemRepairRequest[] = [];
  readonly problemRepairDecisions: ResultProblemRepairDecisionRequest[] = [];
  readonly retractionRequests: ResultRetractionRequest[] = [];
  readonly retractionDecisions: ResultRetractionDecisionRequest[] = [];
  readonly retractionOverrides: ResultRetractionOverrideRequest[] = [];
  readonly retractionFinalizations: ResultRetractionFinalizationRequest[] = [];
  maintainerAmendment: ResultAmendmentView | null = null;
  contractAssertions = 0;

  assertResultOwnerContract(): Promise<string> {
    this.contractAssertions += 1;
    return Promise.resolve("f".repeat(40));
  }

  readResultAmendmentForMaintainer(): Promise<ResultAmendmentView> {
    if (this.maintainerAmendment === null) {
      return Promise.reject(new ResultOwnerStateError(404, "result amendment was not found"));
    }
    return Promise.resolve(this.maintainerAmendment);
  }

  appendEvent(event: WritableStateEvent): Promise<{ created: boolean }> {
    this.events.push(event);
    return Promise.resolve({ created: this.created });
  }

  appendEventAtHead(
    event: WritableStateEvent,
    expectedHead: string,
  ): Promise<{ commit: string; created: boolean }> {
    const existing = this.events.find((candidate) => candidate.event_id === event.event_id);
    if (existing !== undefined) {
      if (JSON.stringify(existing) !== JSON.stringify(event)) {
        throw new StateEventConflictError(event.event_id);
      }
      return Promise.resolve({ commit: this.head, created: false });
    }
    if (expectedHead !== this.head) throw new GitHubStateError(409, "State moved");
    validateStateEvent(event);
    this.events.push(event);
    this.head = "e".repeat(40);
    return Promise.resolve({ commit: this.head, created: true });
  }

  appendSubmissionLifecycle(
    events: readonly WritableSubmissionLifecycleEvent[],
    expectedLifecycleEventId: string,
    nextView: SubmissionView,
  ): Promise<{ created: boolean; view: SubmissionView }> {
    events.forEach((event) => validateStateEvent(event));
    const current = this.views.get(nextView.submission_id);
    if (!current || latestLifecycleEventId(current) !== expectedLifecycleEventId) {
      throw new Error("lifecycle conflict");
    }
    const decoded = decodeSubmissionView(nextView);
    this.events.push(...events);
    this.views.set(decoded.submission_id, decoded);
    return Promise.resolve({ created: true, view: decoded });
  }

  recordAcceptedResult(
    events: readonly WritableResultLifecycleEvent[],
    expectedLifecycleEventId: string,
    nextView: SubmissionView,
  ): Promise<{ created: boolean; view: SubmissionView }> {
    events.forEach((event) => validateStateEvent(event));
    const current = this.views.get(nextView.submission_id);
    if (!current || latestLifecycleEventId(current) !== expectedLifecycleEventId) {
      throw new Error("result lifecycle conflict");
    }
    const decoded = decodeSubmissionView(nextView);
    this.events.push(...events);
    this.views.set(decoded.submission_id, decoded);
    return Promise.resolve({ created: true, view: decoded });
  }

  acceptSubmission(
    events: readonly WritableStateEvent[],
    view: SubmissionView,
    outbox: DispatchOutbox,
  ): Promise<{ created: boolean; view: SubmissionView }> {
    events.forEach((event) => validateStateEvent(event));
    const decodedView = decodeSubmissionView(view);
    const decodedOutbox = decodeDispatchOutbox(outbox);
    if (decodedView.submission_id !== decodedOutbox.submission_id) {
      throw new TypeError("submission acceptance identities disagree");
    }
    const existing = this.views.get(view.submission_id);
    if (existing !== undefined) return Promise.resolve({ created: false, view: existing });
    this.events.push(...events);
    this.views.set(decodedView.submission_id, decodedView);
    this.outbox.set(decodedOutbox.submission_id, decodedOutbox);
    return Promise.resolve({ created: this.created, view: decodedView });
  }

  readSubmission(submissionId: string): Promise<SubmissionView | null> {
    return Promise.resolve(this.views.get(submissionId) ?? null);
  }

  appendSubmissionMutation(
    event: WritableStateEvent,
    expectedMutationEventId: string,
    nextView: SubmissionView,
  ): Promise<{ created: boolean; view: SubmissionView }> {
    const current = this.views.get(nextView.submission_id);
    if (current?.mutation_event_id !== expectedMutationEventId) throw new Error("mutation conflict");
    this.events.push(event);
    this.views.set(nextView.submission_id, nextView);
    return Promise.resolve({ created: true, view: nextView });
  }

  updateDispatch(
    nextView: SubmissionView,
    expectedAttempts: number,
    nextOutbox: DispatchOutbox | null,
  ): Promise<{ view: SubmissionView }> {
    const current = this.views.get(nextView.submission_id);
    if (current?.dispatch.attempts !== expectedAttempts) throw new Error("dispatch conflict");
    this.views.set(nextView.submission_id, nextView);
    if (nextOutbox === null) this.outbox.delete(nextView.submission_id);
    else this.outbox.set(nextOutbox.submission_id, nextOutbox);
    return Promise.resolve({ view: nextView });
  }

  listDispatchOutbox(shard: string, scanOffset: number, scanLimit: number): Promise<readonly DispatchOutbox[]> {
    const entries = [...this.outbox.values()]
      .filter((entry) => entry.submission_id.replaceAll("-", "").endsWith(shard))
      .sort((left, right) => left.submission_id.localeCompare(right.submission_id));
    const start = entries.length === 0 ? 0 : scanOffset % entries.length;
    return Promise.resolve(entries.length <= scanLimit
      ? entries
      : [...entries.slice(start, start + scanLimit), ...entries.slice(0, Math.max(0, start + scanLimit - entries.length))]);
  }

  provePromotionCanaryContention(event: WritableStateEvent): Promise<{
    proofRecorded: boolean;
    idempotent: boolean;
    created: boolean;
  }> {
    validateStateEvent(event);
    const existing = this.events.find((candidate) => candidate.event_id === event.event_id);
    if (existing === undefined) this.events.push(event);
    return Promise.resolve({
      proofRecorded: true,
      idempotent: existing !== undefined,
      created: existing === undefined,
    });
  }

  claimLegacyResult(request: LegacyResultClaimRequest): Promise<{
    created: boolean;
    resultId: string;
    authorityEventId: string;
  }> {
    this.legacyClaims.push(request);
    return Promise.resolve({
      created: this.created,
      resultId: request.verified.resultId,
      authorityEventId: request.eventId,
    });
  }

  backfillLegacyResultMetadata(request: LegacyResultBackfillRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
  }> {
    this.legacyBackfills.push(request);
    return Promise.resolve({
      created: this.created,
      resultId: request.resultId,
      mutationEventId: request.eventId,
    });
  }

  requestResultRetraction(request: ResultRetractionRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    retractionRevision: number;
  }> {
    this.retractionRequests.push(request);
    return Promise.resolve({
      created: this.created,
      resultId: request.resultId,
      mutationEventId: request.eventId,
      retractionRevision: 1,
    });
  }

  requestResultProblemRepair(request: ResultProblemRepairRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    repairRevision: number;
  }> {
    this.problemRepairRequests.push(request);
    return Promise.resolve({
      created: this.created,
      resultId: request.resultId,
      mutationEventId: request.eventId,
      repairRevision: 1,
    });
  }

  decideResultProblemRepair(request: ResultProblemRepairDecisionRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    repairRevision: number;
  }> {
    this.problemRepairDecisions.push(request);
    return Promise.resolve({
      created: this.created,
      resultId: request.resultId,
      mutationEventId: request.eventId,
      repairRevision: 1,
    });
  }

  decideResultRetraction(request: ResultRetractionDecisionRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    retractionRevision: number;
  }> {
    this.retractionDecisions.push(request);
    return Promise.resolve({
      created: this.created,
      resultId: request.resultId,
      mutationEventId: request.eventId,
      retractionRevision: 1,
    });
  }

  overrideResultRetraction(request: ResultRetractionOverrideRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    retractionRevision: number;
  }> {
    this.retractionOverrides.push(request);
    return Promise.resolve({
      created: this.created,
      resultId: request.resultId,
      mutationEventId: request.eventId,
      retractionRevision: 1,
    });
  }

  finalizeResultRetraction(request: ResultRetractionFinalizationRequest): Promise<{
    created: boolean;
    resultId: string;
    mutationEventId: string;
    releaseDisposition: "not_published";
  }> {
    this.retractionFinalizations.push(request);
    return Promise.resolve({
      created: this.created,
      resultId: request.resultId,
      mutationEventId: request.eventId,
      releaseDisposition: "not_published",
    });
  }
}

function jsonRequest(path: string, body: unknown): Request {
  return new Request(`https://submit.test${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

function pendingView(
  submissionId: string,
  acceptedAt: string,
  attempts = 0,
  status: "failed" | "pending" | "succeeded" = "pending",
): SubmissionView {
  const metadataEventId = "019debcf-cb48-7000-8000-000000000002";
  return {
    schema_version: 1,
    submission_id: submissionId,
    owner_login: "alice",
    received_event_id: submissionId,
    mutation_event_id: metadataEventId,
    metadata_event_id: metadataEventId,
    publication_event_id: null,
    accepted_at: acceptedAt,
    submission: SUBMISSION,
    production_metadata: SUBMISSION.production_metadata,
    publication_choice: "scheduled",
    archive: { status: "pending" },
    evaluation: { status: "pending" },
    result_id: null,
    dispatch: {
      status,
      attempts,
      requested_at: acceptedAt,
      updated_at: acceptedAt,
      workflow_ref: `lean-eval-dispatch/${"b".repeat(40)}`,
      last_error_code: status === "failed" ? "dispatch_provider_unavailable" : null,
    },
  };
}

function acceptedView(submissionId: string): SubmissionView {
  const base = pendingView(submissionId, "2026-01-30T00:00:00.000Z", 1, "succeeded");
  return {
    ...base,
    schema_version: 2,
    archive: {
      status: "completed",
      event_id: "019c0a80-1000-7000-8000-000000000001",
      occurred_at: "2026-01-30T01:00:00.000Z",
      archive_repository: "leanprover/lean-eval-audit",
      archive_commit: "d".repeat(40),
      archive_path: `archives/${submissionId.replaceAll("-", "").slice(0, 2)}/${submissionId}.tar.age`,
      archive_ciphertext_sha256: "e".repeat(64),
      encrypted: true,
    },
    evaluation: {
      status: "accepted",
      event_id: "019c0da3-6b80-7000-8000-000000000002",
      occurred_at: "2026-01-31T12:34:56.789Z",
      attempt: 1,
      benchmark_repository: "leanprover/lean-eval",
      benchmark_commit: "c".repeat(40),
      toolchain: "leanprover/lean4:v4.32.0",
      evaluator_version: "b".repeat(40),
    },
    result_id: null,
    result_event_id: null,
  };
}

async function digest(value: string): Promise<string> {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

describe("strict API contract", () => {
  it("rejects extra fields, policy mismatches, and oversized bodies", async () => {
    expect(() => decodeSubmissionInput({ ...SUBMISSION, surprise: true })).toThrow(ApiDecodeError);
    expect(() => decodeSubmissionInput({ ...SUBMISSION, declared_model: "é".repeat(129) })).toThrow(/UTF-8 bytes/);
    expect(() => decodeSubmissionInput({ ...SUBMISSION, declared_model: "x".repeat(256) })).not.toThrow();
    expect(() => assertSourcePolicy("open-conjectures", "private", true)).toThrow(/public/);
    expect(() => assertSourcePolicy("formalization-evaluation", "private", true)).not.toThrow();
    const request = new Request("https://submit.test/api/v1/test", {
      method: "POST",
      headers: { "content-type": "application/json", "content-length": "999999" },
      body: "{}",
    });
    await expect(readJson(request)).rejects.toThrow(/too large/);
  });

  it("strictly separates maintainer decision shapes and rejects smuggled fields", () => {
    expect(() => decodeProblemRepairDecision({
      decision: "apply",
      results_commit: "a".repeat(40),
      reason_code: "smuggled",
    })).toThrow(ApiDecodeError);
    expect(() => decodeProblemRepairDecision({
      decision: "reject",
      reason_code: "insufficient_comparator_evidence",
      results_commit: "a".repeat(40),
    })).toThrow(ApiDecodeError);
    expect(() => decodeProblemRepairDecision({
      decision: "apply",
      results_commit: "A".repeat(40),
    })).toThrow(ApiDecodeError);
    expect(() => decodeResultRetractionDecision({
      decision: "approve",
      reason_code: "owner_request_verified",
      reviewer_login: "forged",
    })).toThrow(ApiDecodeError);
    expect(() => decodeResultRetractionOverride({
      reason_code: "owner_account_unavailable",
      owner_login: "forged",
    })).toThrow(ApiDecodeError);
    expect(() => decodeEmptyObject({ event_id: "forged" }, "terminal retraction request")).toThrow(
      ApiDecodeError,
    );
  });

  it("signs purpose-bound expiring tokens and rejects tampering", async () => {
    const grant = makeSubmissionGrant("alice", Math.floor(NOW_MS / 1000));
    const token = await signToken(SECRET, grant);
    await expect(
      verifyToken<SubmissionGrant>(SECRET, token, "submission_grant", Math.floor(NOW_MS / 1000)),
    ).resolves.toEqual(grant);
    await expect(
      verifyToken<SubmissionGrant>(SECRET, token.replace("v1.", "v1.A"), "submission_grant", Math.floor(NOW_MS / 1000)),
    ).rejects.toThrow(/signature/);
    await expect(
      verifyToken<SubmissionGrant>(SECRET, token, "submission_grant", grant.expires_at),
    ).rejects.toThrow(/expired/);
    const malformedIdentity: BrowserSession = {
      kind: "browser_session",
      login: "Alice",
      github_id: 0,
      issued_at: Math.floor(NOW_MS / 1000),
      expires_at: Math.floor(NOW_MS / 1000) + 60,
    };
    await expect(
      verifyToken<BrowserSession>(
        SECRET,
        await signToken(SECRET, malformedIdentity),
        "browser_session",
        Math.floor(NOW_MS / 1000),
      ),
    ).rejects.toThrow(/login|identity/);
  });

  it("builds an exact-ref dispatch carrying the UUID archive contract", async () => {
    const request = buildDispatchRequest(
      "leanprover/lean-eval-submissions",
      "submission.yml",
      `lean-eval-dispatch/${"b".repeat(40)}`,
      "019debcf-cb48-7000-8000-000000000001",
      "alice",
      "staging",
      SUBMISSION,
    );
    const body = await request.json<{ ref: string; inputs: Record<string, string> }>();
    expect(body.ref).toBe(`lean-eval-dispatch/${"b".repeat(40)}`);
    expect(body.inputs).toMatchObject({
      submission_id: "019debcf-cb48-7000-8000-000000000001",
      source_commit: "a".repeat(40),
      archive_locator_required: "true",
      archive_sidecar_schema: "3",
      archive_state_callback_required: "true",
      callback_environment: "staging",
      workflow_commit: "b".repeat(40),
    });
    expect(() => buildDispatchRequest("leanprover/x", "submission.yml", "main", body.inputs.submission_id ?? "", "alice", "staging", SUBMISSION)).toThrow(/immutable/);
  });

  it("strictly decodes the verified archive completion contract", () => {
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    const completion = {
      schema_version: 1,
      occurred_at: "2026-05-02T03:04:05.000Z",
      locator: {
        schema_version: 1,
        submission_id: submissionId,
        archive_repository: "leanprover/lean-eval-audit",
        archive_commit: "a".repeat(40),
        archive_path: `archives/01/${submissionId}.tar.age`,
        archive_ciphertext_sha256: "b".repeat(64),
        encrypted: true,
      },
    };
    expect(decodeArchiveCompletion(completion)).toEqual(completion);
    expect(() => decodeArchiveCompletion({ ...completion, surprise: true })).toThrow(/unknown/);
    expect(() => decodeArchiveCompletion({
      ...completion,
      locator: { ...completion.locator, archive_path: `archives/ff/${submissionId}.tar.age` },
    })).toThrow(/path/);
  });

  it("strictly decodes a result receipt", () => {
    const completion = {
      schema_version: 1,
      submission_id: "019debcf-cb48-7000-8000-000000000001",
      occurred_at: "2026-02-01T00:00:00.000Z",
      result_id: `r2_${"a".repeat(64)}`,
      problem_id: "two_plus_two",
      statement_revision: 2,
      result_repository: "leanprover/lean-eval-submissions",
      result_branch: "staging-results",
      result_commit: "b".repeat(40),
      result_path: "results/alice.json",
      result_tree_digest: "c".repeat(64),
    } as const;
    expect(decodeResultCompletion(completion)).toEqual(completion);
    expect(() => decodeResultCompletion({ ...completion, result_path: "results/../alice.json" }))
      .toThrow(/path/);
    expect(() => decodeResultCompletion({ ...completion, result_branch: "main-copy" }))
      .toThrow(/pins/);
  });

  it("strictly decodes and authenticates the staging source-reader preflight", async () => {
    expect(decodeSourceReaderPreflight({ repository: "kim-em/lean-eval-intake-fixture" }))
      .toBe("kim-em/lean-eval-intake-fixture");
    expect(() => decodeSourceReaderPreflight({ repository: "Kim Em/bad", extra: true }))
      .toThrow(/unknown|canonical/);

    const upstream = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json({
      full_name: "kim-em/lean-eval-intake-fixture",
      private: true,
    }));
    const request = jsonRequest("/internal/v1/source-reader-preflight", {
      repository: "kim-em/lean-eval-intake-fixture",
    });
    request.headers.set("authorization", "Bearer readiness-secret");
    const response = await handleRequest(
      request,
      { ...ENV, READINESS_TOKEN: "readiness-secret" },
      LIFECYCLE,
      { provider: new GitHubProvider(upstream, "verification-token") },
    );
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      status: "source_reader_ready",
      environment: "staging",
      repository: "kim-em/lean-eval-intake-fixture",
      private: true,
    });

    const productionRequest = jsonRequest("/internal/v1/source-reader-preflight", {
      repository: "kim-em/lean-eval-intake-fixture",
    });
    productionRequest.headers.set("authorization", "Bearer readiness-secret");
    const production = await handleRequest(
      productionRequest,
      { ...ENV, DEPLOYMENT_ENVIRONMENT: "production", READINESS_TOKEN: "readiness-secret" },
      LIFECYCLE,
      { provider: new GitHubProvider(upstream, "verification-token") },
    );
    expect(production.status).toBe(403);
  });

  it("records authenticated archive completion while public intake is disabled", async () => {
    const state = new MemoryState();
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    state.views.set(submissionId, pendingView(
      submissionId,
      "2026-05-01T00:00:00.000Z",
      1,
      "succeeded",
    ));
    const completion = {
      schema_version: 1,
      occurred_at: "2026-05-02T03:04:05.000Z",
      locator: {
        schema_version: 1,
        submission_id: submissionId,
        archive_repository: "leanprover/lean-eval-audit",
        archive_commit: "a".repeat(40),
        archive_path: `archives/01/${submissionId}.tar.age`,
        archive_ciphertext_sha256: "b".repeat(64),
        encrypted: true,
      },
    };
    const request = jsonRequest("/internal/v1/archive-completed", completion);
    request.headers.set("authorization", `Bearer ${ENV.LIFECYCLE_CALLBACK_TOKEN}`);
    const response = await handleRequest(
      request,
      { ...ENV, INTAKE_ENABLED: "false" },
      LIFECYCLE,
      { state },
    );
    expect(response.status).toBe(201);
    const event = state.events.at(-1);
    expect(event).toMatchObject({
      event_type: "archive.completed",
      subject_id: submissionId,
      causation_event_id: submissionId,
      actor: { kind: "system" },
      payload: {
        archive_repository: completion.locator.archive_repository,
        archive_commit: completion.locator.archive_commit,
        archive_path: completion.locator.archive_path,
        archive_ciphertext_sha256: completion.locator.archive_ciphertext_sha256,
        encrypted: true,
      },
    });
    expect(event?.event_id).toBe(await lifecycleEventId(
      "archive.completed",
      submissionId,
      completion.occurred_at,
    ));
    expect(state.views.get(submissionId)).toMatchObject({
      schema_version: 2,
      archive: {
        status: "completed",
        event_id: event?.event_id,
        occurred_at: completion.occurred_at,
        archive_commit: completion.locator.archive_commit,
      },
      evaluation: { status: "pending" },
    });
    const retryRequest = jsonRequest("/internal/v1/archive-completed", completion);
    retryRequest.headers.set("authorization", `Bearer ${ENV.LIFECYCLE_CALLBACK_TOKEN}`);
    const retry = await handleRequest(
      retryRequest,
      { ...ENV, INTAKE_ENABLED: "false" },
      LIFECYCLE,
      { state },
    );
    expect(retry.status).toBe(200);
    expect(state.events).toHaveLength(1);
    const evaluationCompletion = {
      schema_version: 1,
      submission_id: submissionId,
      attempt: 1,
      occurred_at: "2026-05-02T03:04:05.001Z",
      benchmark_repository: "leanprover/lean-eval",
      benchmark_commit: "c".repeat(40),
      toolchain: "leanprover/lean4:v4.32.0",
      outcome: { status: "rejected", reason_code: "proof_rejected" },
    };
    const evaluationRequest = jsonRequest("/internal/v1/evaluation-completed", evaluationCompletion);
    evaluationRequest.headers.set("authorization", `Bearer ${ENV.LIFECYCLE_CALLBACK_TOKEN}`);
    const evaluationResponse = await handleRequest(
      evaluationRequest,
      { ...ENV, INTAKE_ENABLED: "false" },
      LIFECYCLE,
      { state },
    );
    expect(evaluationResponse.status).toBe(201);
    expect(state.events.slice(-2).map((item) => item.event_type)).toEqual([
      "evaluation.started", "evaluation.rejected",
    ]);
    expect(state.views.get(submissionId)).toMatchObject({
      schema_version: 2,
      evaluation: {
        status: "rejected",
        attempt: 1,
        reason_code: "proof_rejected",
        benchmark_commit: evaluationCompletion.benchmark_commit,
      },
    });
    const unauthorized = await handleRequest(
      jsonRequest("/internal/v1/archive-completed", completion),
      { ...ENV, INTAKE_ENABLED: "false" },
      LIFECYCLE,
      { state },
    );
    expect(unauthorized.status).toBe(401);
  });

  it("keeps lifecycle callback CAS exhaustion retryable", async () => {
    const state = new MemoryState();
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    state.views.set(submissionId, pendingView(
      submissionId,
      "2026-05-01T00:00:00.000Z",
      1,
      "succeeded",
    ));
    vi.spyOn(state, "appendSubmissionLifecycle").mockRejectedValue(
      new GitHubStateError(409, "State branch kept changing during lifecycle update"),
    );
    const request = jsonRequest("/internal/v1/archive-completed", {
      schema_version: 1,
      occurred_at: "2026-05-02T03:04:05.000Z",
      locator: {
        schema_version: 1,
        submission_id: submissionId,
        archive_repository: "leanprover/lean-eval-audit",
        archive_commit: "a".repeat(40),
        archive_path: `archives/01/${submissionId}.tar.age`,
        archive_ciphertext_sha256: "b".repeat(64),
        encrypted: true,
      },
    });
    request.headers.set("authorization", `Bearer ${ENV.LIFECYCLE_CALLBACK_TOKEN}`);
    const response = await handleRequest(
      request,
      { ...ENV, INTAKE_ENABLED: "false" },
      LIFECYCLE,
      { state },
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "state_unavailable" });
  });

  it("records an authenticated archive failure without leaving status pending", async () => {
    const state = new MemoryState();
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    state.views.set(submissionId, pendingView(
      submissionId,
      "2026-05-01T00:00:00.000Z",
      1,
      "succeeded",
    ));
    const failure = {
      schema_version: 1,
      submission_id: submissionId,
      occurred_at: "2026-05-01T00:01:00.000Z",
      reason_code: "source_fetch_failed",
      retryable: true,
    };
    const request = jsonRequest("/internal/v1/archive-failed", failure);
    request.headers.set("authorization", `Bearer ${ENV.LIFECYCLE_CALLBACK_TOKEN}`);
    const response = await handleRequest(
      request,
      { ...ENV, INTAKE_ENABLED: "false" },
      LIFECYCLE,
      { state },
    );
    expect(response.status).toBe(201);
    expect(state.events.at(-1)).toMatchObject({
      event_type: "archive.failed",
      causation_event_id: submissionId,
      payload: { reason_code: "source_fetch_failed", retryable: true },
    });
    expect(state.views.get(submissionId)).toMatchObject({
      schema_version: 2,
      archive: { status: "failed", reason_code: "source_fetch_failed", retryable: true },
      evaluation: { status: "pending" },
    });
  });

  it("verifies an exact staging Results blob and atomically records result and release", async () => {
    const state = new MemoryState();
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    const accepted = acceptedView(submissionId);
    state.views.set(submissionId, accepted);
    const resultId = `r2_${await digest(`lean-eval-result-v2\0${JSON.stringify([
      "alice", SUBMISSION.declared_model, SUBMISSION.problem_id, SUBMISSION.statement_revision,
    ])}`)}`;
    expect(resultId).toBe("r2_ecad1e075c37192258a92f9c40ffa743864404c99cd14f790ecd26e80dc4ddaf");
    const resultFile = JSON.stringify({
      schema_version: 2,
      user: "Alice",
      results: [{
        result_id: resultId,
        problem_id: SUBMISSION.problem_id,
        statement_revision: SUBMISSION.statement_revision,
        declared_model: SUBMISSION.declared_model,
      }],
    });
    const fileBytes = new TextEncoder().encode(resultFile);
    const fileDigest = await digest(resultFile);
    const treeDigest = await digest(`lean-eval-result-tree-v1\0${JSON.stringify([{
      path: "results/alice.json",
      sha256: fileDigest,
      size: fileBytes.byteLength,
    }])}`);
    expect(treeDigest).toBe("00e10d25d0f8a5a1acb0f838db8011a2d570677bb062925cd100b7297fc4f0b2");
    const resultFetch = vi.fn<typeof fetch>((input, init) => {
      const url = input instanceof Request
        ? input.url
        : typeof input === "string"
          ? input
          : input.toString();
      expect(url).toContain(`/contents/results/alice.json?ref=${"f".repeat(40)}`);
      expect(new Headers(init?.headers).get("x-lean-eval-expected-commit")).toBe("f".repeat(40));
      let binary = "";
      for (const byte of fileBytes) binary += String.fromCharCode(byte);
      return Promise.resolve(Response.json({
        type: "file",
        path: "results/alice.json",
        encoding: "base64",
        content: btoa(binary),
        size: fileBytes.byteLength,
      }));
    });
    const completion = {
      schema_version: 1,
      submission_id: submissionId,
      occurred_at: "2026-02-01T00:00:00.000Z",
      result_id: resultId,
      problem_id: SUBMISSION.problem_id,
      statement_revision: SUBMISSION.statement_revision,
      result_repository: "leanprover/lean-eval-submissions",
      result_branch: "staging-results",
      result_commit: "f".repeat(40),
      result_path: "results/alice.json",
      result_tree_digest: treeDigest,
    };
    const request = jsonRequest("/internal/v1/result-completed", completion);
    request.headers.set("authorization", `Bearer ${ENV.LIFECYCLE_CALLBACK_TOKEN}`);
    const response = await handleRequest(
      request,
      { ...ENV, INTAKE_ENABLED: "false" },
      LIFECYCLE,
      {
        state,
        provider: new GitHubProvider(undefined, undefined, undefined, undefined, resultFetch),
      },
    );
    expect(response.status).toBe(201);
    expect(state.events.slice(-2)).toMatchObject([
      {
        event_type: "result.recorded",
        subject_id: resultId,
        causation_event_id: accepted.evaluation.status === "accepted"
          ? accepted.evaluation.event_id
          : "unreachable",
        payload: {
          submission_id: submissionId,
          problem_id: SUBMISSION.problem_id,
          statement_revision: SUBMISSION.statement_revision,
          result_commit: completion.result_commit,
          tree_digest: treeDigest,
        },
      },
      {
        event_type: "release.scheduled",
        subject_id: resultId,
        payload: { result_id: resultId, release_at: "2026-03-31T12:34:56.789Z" },
      },
    ]);
    expect(state.views.get(submissionId)).toMatchObject({
      result_id: resultId,
      result_event_id: state.events.at(-2)?.event_id,
    });
    expect(resultFetch).toHaveBeenCalledOnce();

    const eventCount = state.events.length;
    const guardCheck = vi.spyOn(state, "recordAcceptedResult").mockRejectedValue(
      new StateEventConflictError(`views/result-identities/${resultId}.json`),
    );
    const retryRequest = jsonRequest("/internal/v1/result-completed", completion);
    retryRequest.headers.set("authorization", `Bearer ${ENV.LIFECYCLE_CALLBACK_TOKEN}`);
    const retry = await handleRequest(
      retryRequest,
      { ...ENV, INTAKE_ENABLED: "false" },
      LIFECYCLE,
      {
        state,
        provider: new GitHubProvider(undefined, undefined, undefined, undefined, resultFetch),
      },
    );
    expect(retry.status).toBe(409);
    await expect(retry.json()).resolves.toEqual({ error: "idempotency_conflict" });
    expect(guardCheck).toHaveBeenCalledOnce();
    expect(state.events).toHaveLength(eventCount);
  });

  it("fails closed with 429 when the Cloudflare limiter denies or errors", async () => {
    const request = jsonRequest("/api/v1/agent/challenges", {
      login: "alice",
      gist_id: "abcde",
      source_repository: "alice/proofs",
      source_commit: "a".repeat(40),
    });
    const bindingLimit = vi.fn<RateLimit["limit"]>(() => Promise.resolve({ success: false }));
    const denied = await handleRequest(request.clone(), { ...ENV, API_RATE_LIMITER: { limit: bindingLimit } }, LIFECYCLE);
    expect(denied.status).toBe(429);
    expect(denied.headers.get("retry-after")).toBe("60");
    expect(bindingLimit).toHaveBeenCalledOnce();
    const failed = await handleRequest(request, {
      ...ENV,
      API_RATE_LIMITER: { limit: () => Promise.reject(new Error("binding unavailable")) },
    }, LIFECYCLE);
    expect(failed.status).toBe(429);
  });

  it("keys transient limits by route and a hashed actor signal, not a shared IP alone", async () => {
    const keys: string[] = [];
    for (const userAgent of ["agent-a", "agent-b"]) {
      await handleRequest(new Request("https://submit.test/api/v1/agent/challenges", {
        method: "POST",
        headers: { "cf-connecting-ip": "192.0.2.1", "user-agent": userAgent },
      }), ENV, LIFECYCLE, {
        rateLimit: (key) => {
          keys.push(key);
          return Promise.resolve({ success: false });
        },
      });
    }
    expect(keys).toHaveLength(2);
    expect(keys[0]).not.toBe(keys[1]);
    expect(keys.every((key) => key.startsWith("POST:/api/v1/agent/challenges:"))).toBe(true);
    expect(keys.join(" ")).not.toContain("192.0.2.1");
  });
});

describe("production intake lease smoke", () => {
  const issuedAt = Math.floor(NOW_MS / 1000);
  const expiresAt = issuedAt + 900;
  const nonce = "lease-smoke-secret-with-at-least-thirty-two-bytes";
  const eventId = "019debcf-f258-7000-8000-000000000001";
  const stateCommit = "d".repeat(40);
  const targetCommit = "a".repeat(40);
  const body = {
    schema_version: 1,
    environment: "production",
    controller_commit: targetCommit,
    controller_run_attempt: "2",
    controller_run_id: "123456",
    event_id: eventId,
    expires_at: expiresAt,
    issued_at: issuedAt,
    nonce,
    state_commit: stateCommit,
    target_commit: targetCommit,
  } as const;

  async function leasedEnvironment(): Promise<RuntimeEnv> {
    return {
      ...ENV,
      DEPLOYED_COMMIT: targetCommit,
      DEPLOYMENT_ENVIRONMENT: "production",
      INTAKE_ENABLEMENT_MODE: "leased",
      INTAKE_LEASE_CONTROLLER_COMMIT: body.controller_commit,
      INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT: body.controller_run_attempt,
      INTAKE_LEASE_CONTROLLER_RUN_ID: body.controller_run_id,
      INTAKE_LEASE_EVENT_ID: body.event_id,
      INTAKE_LEASE_EXPIRES_AT: String(body.expires_at),
      INTAKE_LEASE_ISSUED_AT: String(body.issued_at),
      INTAKE_LEASE_NONCE_DIGEST: await nonceDigest("intake_lease", nonce),
      INTAKE_LEASE_STATE_COMMIT: body.state_commit,
      INTAKE_LEASE_TARGET_COMMIT: body.target_commit,
      READINESS_TOKEN: "readiness-secret",
      STATE_REPOSITORY: "leanprover/lean-eval-state",
    };
  }

  function request(value: unknown = body): Request {
    return new Request("https://submit.test/internal/v1/intake-lease-smoke", {
      method: "POST",
      headers: {
        authorization: "Bearer readiness-secret",
        "content-type": "application/json",
      },
      body: JSON.stringify(value),
    });
  }

  it("consumes one exact deterministic State nonce and survives response loss", async () => {
    const state = new MemoryState();
    const env = await leasedEnvironment();
    const dependencies = { now: () => NOW_MS, state };
    const first = await handleRequest(request(), env, LIFECYCLE, dependencies);
    expect(first.status).toBe(200);
    await expect(first.json()).resolves.toMatchObject({
      status: "lease_smoke_consumed",
      state_commit: "e".repeat(40),
    });
    expect(state.events).toHaveLength(1);
    expect(state.events[0]).toMatchObject({
      event_id: eventId,
      occurred_at: new Date(issuedAt * 1000).toISOString(),
      payload: {
        purpose: "intake_lease",
        expires_at: new Date(expiresAt * 1000).toISOString(),
      },
    });

    const retry = await handleRequest(request(), env, LIFECYCLE, dependencies);
    expect(retry.status).toBe(200);
    await expect(retry.json()).resolves.toMatchObject({
      status: "lease_smoke_already_consumed",
      state_commit: "e".repeat(40),
    });
    expect(state.events).toHaveLength(1);
  });

  it("serializes concurrent duplicate consumption to one State event", async () => {
    const state = new MemoryState();
    const env = await leasedEnvironment();
    const responses = await Promise.all([
      handleRequest(request(), env, LIFECYCLE, { now: () => NOW_MS, state }),
      handleRequest(request(), env, LIFECYCLE, { now: () => NOW_MS, state }),
    ]);
    expect(responses.map((response) => response.status)).toEqual([200, 200]);
    const statuses = await Promise.all(responses.map(async (response) =>
      (await response.json<{ status: string }>()).status));
    expect(statuses.sort()).toEqual(["lease_smoke_already_consumed", "lease_smoke_consumed"]);
    expect(state.events).toHaveLength(1);
  });

  it("rechecks lease expiry immediately before the State mutation", async () => {
    const state = new MemoryState();
    const env = await leasedEnvironment();
    const times = [NOW_MS, expiresAt * 1000];
    const response = await handleRequest(request(), env, LIFECYCLE, {
      now: () => times.shift() ?? expiresAt * 1000,
      state,
    });
    expect(response.status).toBe(409);
    expect(state.events).toHaveLength(0);
  });

  it("rejects forged and cross-bound requests before touching State", async () => {
    const env = await leasedEnvironment();
    for (const [changed, expectedStatus] of [
      [{ ...body, nonce: `${nonce}x` }, 409],
      [{ ...body, target_commit: "c".repeat(40) }, 409],
      [{ ...body, controller_run_id: "123457" }, 409],
      [{ ...body, controller_run_attempt: "3" }, 409],
      [{ ...body, controller_commit: "c".repeat(40) }, 409],
      [{ ...body, state_commit: "c".repeat(40) }, 409],
      [{ ...body, environment: "staging" }, 400],
    ]) {
      const state = new MemoryState();
      const response = await handleRequest(request(changed), env, LIFECYCLE, {
        now: () => NOW_MS,
        state,
      });
      expect(response.status).toBe(expectedStatus);
      expect(state.events).toHaveLength(0);
    }
  });

  it("fails closed after expiry and when the exact State head has moved", async () => {
    const env = await leasedEnvironment();
    const expiredState = new MemoryState();
    const expired = await handleRequest(request(), env, LIFECYCLE, {
      now: () => expiresAt * 1000,
      state: expiredState,
    });
    expect(expired.status).toBe(409);
    expect(expiredState.events).toHaveLength(0);

    const ordinaryIntake = await handleRequest(
      new Request("https://submit.test/api/v1/agent/challenges", { method: "POST" }),
      env,
      LIFECYCLE,
      { now: () => expiresAt * 1000, state: expiredState },
    );
    expect(ordinaryIntake.status).toBe(503);
    await expect(ordinaryIntake.json()).resolves.toEqual({ error: "intake_disabled" });

    const moved = new MemoryState();
    moved.head = "f".repeat(40);
    const drifted = await handleRequest(request(), env, LIFECYCLE, {
      now: () => NOW_MS,
      state: moved,
    });
    expect(drifted.status).toBe(503);
    expect(moved.events).toHaveLength(0);
  });
});

describe("agent intake in workerd", () => {
  it("reads the exact secret gist anonymously instead of through the source broker", async () => {
    const anonymousFetch = vi.fn<typeof fetch>(function (this: unknown, _input, init) {
      expect(this).toBeUndefined();
      expect(new Headers(init?.headers).has("authorization")).toBe(false);
      return Promise.resolve(Response.json({
        public: false,
        owner: { id: 42, login: "Alice" },
        files: { "lean-eval-proof.txt": { truncated: false, content: "challenge" } },
      }));
    });
    const sourceBroker = vi.fn<typeof fetch>(() => Promise.reject(new Error("gist reached source broker")));
    const provider = new GitHubProvider(anonymousFetch, undefined, sourceBroker);
    await expect(provider.verifySecretGist("abcde", "alice", "challenge"))
      .resolves.toEqual({ id: 42, login: "alice" });
    expect(anonymousFetch).toHaveBeenCalledOnce();
    expect(sourceBroker).not.toHaveBeenCalled();
  });

  it("verifies secret gist ownership and tag-at-exact-commit before one atomic append", async () => {
    const state = new MemoryState();
    let challenge = "";
    const upstream = vi.fn<typeof fetch>((input, init) => {
      const url = typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
      if (url.includes("/gists/abcde")) {
        expect(new Headers(init?.headers).has("authorization")).toBe(false);
        return Promise.resolve(Response.json({
          public: false,
          owner: { id: 42, login: "alice" },
          files: { "lean-eval-proof.txt": { truncated: false, content: challenge } },
        }));
      }
      if (url.includes("/git/ref/tags/")) {
        return Promise.resolve(Response.json({ object: { type: "commit", sha: "a".repeat(40) } }));
      }
      if (url.endsWith("/repos/alice/proofs")) {
        return Promise.resolve(Response.json({ full_name: "alice/proofs", private: true }));
      }
      return Promise.reject(new Error(`unexpected provider request: ${url}`));
    });
    const github = new GitHubProvider(upstream, "verification-token");
    const challengeResponse = await handleRequest(
      jsonRequest("/api/v1/agent/challenges", {
        login: "alice",
        gist_id: "abcde",
        source_repository: "alice/proofs",
        source_commit: "a".repeat(40),
      }),
      ENV,
      LIFECYCLE,
      { now: () => NOW_MS, provider: github, state },
    );
    const challengeBody = await challengeResponse.json<{ challenge: string; submission_id: string }>();
    challenge = challengeBody.challenge;
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    const response = await handleRequest(
      jsonRequest("/api/v1/agent/submissions", { challenge, submission: SUBMISSION }),
      ENV,
      LIFECYCLE,
      { now: () => NOW_MS + 300_000, provider: github, state, dispatch },
    );
    expect(response.status).toBe(202);
    expect(state.events.map((event) => event.event_type)).toEqual([
      "authentication.nonce_consumed",
      "submission.received",
      "submission.metadata_amended",
    ]);
    expect(state.events[0]?.payload).not.toHaveProperty("nonce");
    expect(state.events[0]?.occurred_at).toBe(new Date(NOW_MS + 300_000).toISOString());
    expect(state.events[1]?.occurred_at).toBe(new Date(NOW_MS + 300_001).toISOString());
    expect(dispatch).toHaveBeenCalledOnce();
    const dispatchBody = await dispatch.mock.calls[0]?.[0].json<{ inputs: Record<string, string> }>();
    expect(dispatchBody?.inputs.source_commit).toBe("a".repeat(40));
  });

  it("rejects public, truncated, wrong-owner, and wrong-content gist proofs", async () => {
    const cases = [
      { public: true, owner: { id: 42, login: "alice" }, truncated: false, content: "challenge" },
      { public: false, owner: { id: 42, login: "alice" }, truncated: true, content: "challenge" },
      { public: false, owner: { id: 43, login: "mallory" }, truncated: false, content: "challenge" },
      { public: false, owner: { id: 42, login: "alice" }, truncated: false, content: "different" },
    ];
    for (const candidate of cases) {
      const upstream = vi.fn<typeof fetch>((_input, init) => {
        expect(new Headers(init?.headers).has("authorization")).toBe(false);
        return Promise.resolve(Response.json({
          public: candidate.public,
          owner: candidate.owner,
          files: {
            "lean-eval-proof.txt": {
              truncated: candidate.truncated,
              content: candidate.content,
            },
          },
        }));
      });
      const provider = new GitHubProvider(upstream, "verification-token");
      await expect(provider.verifySecretGist("abcde", "alice", "challenge"))
        .rejects.toMatchObject({ status: 409 });
      expect(upstream).toHaveBeenCalledOnce();
    }
  });

  it("persists a failed dispatch and retries the existing submission without duplicating events", async () => {
    const state = new MemoryState();
    let challenge = "";
    const upstream = vi.fn<typeof fetch>((input) => {
      const url = input instanceof Request ? input.url : input.toString();
      if (url.includes("/gists/abcde")) return Promise.resolve(Response.json({
        public: false,
        owner: { id: 42, login: "alice" },
        files: { "lean-eval-proof.txt": { truncated: false, content: challenge } },
      }));
      if (url.includes("/git/ref/tags/")) return Promise.resolve(Response.json({ object: { type: "commit", sha: "a".repeat(40) } }));
      if (url.endsWith("/repos/alice/proofs")) return Promise.resolve(Response.json({ full_name: "alice/proofs", private: true }));
      return Promise.reject(new Error(`unexpected provider request: ${url}`));
    });
    const github = new GitHubProvider(upstream, "verification-token");
    const challengeResponse = await handleRequest(jsonRequest("/api/v1/agent/challenges", {
      login: "alice", gist_id: "abcde", source_repository: "alice/proofs", source_commit: "a".repeat(40),
    }), ENV, LIFECYCLE, { now: () => NOW_MS, provider: github, state });
    challenge = (await challengeResponse.json<{ challenge: string }>()).challenge;
    const failedDispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.reject(new Error("temporary")));
    const first = await handleRequest(jsonRequest("/api/v1/agent/submissions", { challenge, submission: SUBMISSION }), ENV, LIFECYCLE, {
      now: () => NOW_MS + 1_000, provider: github, state, dispatch: failedDispatch,
    });
    expect(first.status).toBe(202);
    await expect(first.json()).resolves.toMatchObject({ dispatch_status: "failed" });
    expect(state.events).toHaveLength(3);
    expect(state.outbox).toHaveLength(1);

    const successfulDispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    const retry = await handleRequest(jsonRequest("/api/v1/agent/submissions", { challenge, submission: SUBMISSION }), ENV, LIFECYCLE, {
      now: () => NOW_MS + 2_000, provider: github, state, dispatch: successfulDispatch,
    });
    expect(retry.status).toBe(200);
    await expect(retry.json()).resolves.toMatchObject({ status: "already_received", dispatch_status: "succeeded" });
    expect(state.events).toHaveLength(3);
    expect(state.outbox).toHaveLength(0);
    expect(successfulDispatch).toHaveBeenCalledOnce();
  });

  it("requires an authenticated owner and same-origin mutation", async () => {
    const authenticated: BrowserSession = {
      kind: "browser_session",
      login: "alice",
      github_id: 42,
      issued_at: Math.floor(NOW_MS / 1000),
      expires_at: Math.floor(NOW_MS / 1000) + 3600,
    };
    const token = await signToken(SECRET, authenticated);
    const response = await handleRequest(
      new Request("https://submit.test/api/v1/browser/submission-grants", {
        method: "POST",
        headers: { cookie: `lean_eval_session=${token}`, origin: "https://evil.test" },
      }),
      ENV,
      LIFECYCLE,
      { now: () => NOW_MS, state: new MemoryState() },
    );
    expect(response.status).toBe(401);
  });
});

describe("scheduled dispatch reconciliation in workerd", () => {
  it("routes the real scheduled State adapter through the shared budget", async () => {
    const responses = [
      Response.json({ object: { sha: "1".repeat(40) } }),
      Response.json({ tree: { sha: "2".repeat(40) } }),
    ];
    const stateFetch = vi.fn<GitHubFetch>(() => {
      const response = responses.shift();
      if (response === undefined) throw new Error("unexpected State request");
      return Promise.resolve(response);
    });
    const budget = new ScheduledSubrequestBudget(2);

    await expect(handleScheduled(
      { ...ENV, GITHUB_STATE_TOKEN: "state-token" },
      NOW_MS,
      {
        stateFetch,
        scheduledSubrequestBudget: budget,
        dispatch: () => Promise.resolve(),
      },
    )).rejects.toBeInstanceOf(ScheduledSubrequestBudgetError);

    expect(stateFetch).toHaveBeenCalledTimes(2);
    expect(budget.remaining).toBe(0);
  });

  it("logs and leaves due work pending when a full item reserve is unavailable", async () => {
    const state = new MemoryState();
    const scheduledTime = (Math.floor(NOW_MS / (256 * 60_000)) * 256 + 1) * 60_000;
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    const view = pendingView(submissionId, new Date(scheduledTime - 60_000).toISOString());
    state.views.set(submissionId, view);
    state.outbox.set(submissionId, {
      schema_version: 1,
      submission_id: submissionId,
      owner_login: view.owner_login,
      submission: view.submission,
      attempts: 0,
      next_attempt_at: new Date(scheduledTime - 1).toISOString(),
      workflow_ref: view.dispatch.workflow_ref,
    });
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);

    await handleScheduled(ENV, scheduledTime, {
      state,
      dispatch,
      scheduledSubrequestBudget: new ScheduledSubrequestBudget(154),
    });

    expect(dispatch).not.toHaveBeenCalled();
    expect(state.outbox).toHaveLength(1);
    expect(log).toHaveBeenCalledWith(expect.stringContaining("scheduled_dispatch_budget_deferred"));
    log.mockRestore();
  });

  it("does not read or mutate State while intake is disabled", async () => {
    const state = new MemoryState();
    const list = vi.spyOn(state, "listDispatchOutbox");
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    await handleScheduled(
      { ...ENV, INTAKE_ENABLED: "false" },
      NOW_MS,
      { state, dispatch },
    );
    expect(list).not.toHaveBeenCalled();
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("does not reconcile ordinary outboxes at the exact lease expiry", async () => {
    const issuedAt = Math.floor(NOW_MS / 1000);
    const expiresAt = issuedAt + 900;
    const targetCommit = "a".repeat(40);
    const state = new MemoryState();
    const list = vi.spyOn(state, "listDispatchOutbox");
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    await handleScheduled(
      {
        ...ENV,
        DEPLOYED_COMMIT: targetCommit,
        DEPLOYMENT_ENVIRONMENT: "production",
        INTAKE_ENABLEMENT_MODE: "leased",
        INTAKE_LEASE_CONTROLLER_COMMIT: targetCommit,
        INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT: "1",
        INTAKE_LEASE_CONTROLLER_RUN_ID: "123456",
        INTAKE_LEASE_EVENT_ID: "019debcf-f258-7000-8000-000000000001",
        INTAKE_LEASE_EXPIRES_AT: String(expiresAt),
        INTAKE_LEASE_ISSUED_AT: String(issuedAt),
        INTAKE_LEASE_NONCE_DIGEST: "b".repeat(64),
        INTAKE_LEASE_STATE_COMMIT: "d".repeat(40),
        INTAKE_LEASE_TARGET_COMMIT: targetCommit,
      },
      expiresAt * 1000,
      { now: () => expiresAt * 1000, state, dispatch },
    );
    expect(list).not.toHaveBeenCalled();
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("reads one bounded shard and clears a due outbox after successful dispatch", async () => {
    const state = new MemoryState();
    const scheduledTime = (Math.floor(NOW_MS / (256 * 60_000)) * 256 + 1) * 60_000;
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    const view = pendingView(submissionId, new Date(scheduledTime - 60_000).toISOString(), 1, "failed");
    state.views.set(submissionId, view);
    state.outbox.set(submissionId, {
      schema_version: 1,
      submission_id: submissionId,
      owner_login: "alice",
      submission: SUBMISSION,
      attempts: 1,
      next_attempt_at: new Date(scheduledTime - 1).toISOString(),
      workflow_ref: view.dispatch.workflow_ref,
    });
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    await handleScheduled(ENV, scheduledTime, { state, dispatch });
    expect(dispatch).toHaveBeenCalledOnce();
    expect(state.views.get(submissionId)?.dispatch).toMatchObject({ status: "succeeded", attempts: 2 });
    expect(state.outbox).toHaveLength(0);
  });

  it("reconciles due entries while the scheduled budget retains a full-item reserve", async () => {
    const state = new MemoryState();
    const scheduledTime = (Math.floor(NOW_MS / (256 * 60_000)) * 256 + 1) * 60_000;
    for (let index = 0; index < 3; index += 1) {
      const submissionId = `019debcf-cb48-7000-8000-000000000${String(index)}01`;
      const view = pendingView(submissionId, new Date(scheduledTime - 60_000).toISOString());
      state.views.set(submissionId, view);
      state.outbox.set(submissionId, {
        schema_version: 1,
        submission_id: submissionId,
        owner_login: view.owner_login,
        submission: view.submission,
        attempts: 0,
        next_attempt_at: new Date(scheduledTime - 1).toISOString(),
        workflow_ref: view.dispatch.workflow_ref,
      });
    }
    const list = vi.spyOn(state, "listDispatchOutbox");
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());

    await handleScheduled(ENV, scheduledTime, { state, dispatch });

    expect(list).toHaveBeenCalledOnce();
    expect(list.mock.calls[0]?.[0]).toBe("01");
    expect(list.mock.calls[0]?.[2]).toBe(32);
    expect(dispatch).toHaveBeenCalledTimes(3);
    expect(state.outbox).toHaveLength(0);
  });

  it("does not reinterpret a failed success-state write as a provider failure", async () => {
    const state = new MemoryState();
    const scheduledTime = (Math.floor(NOW_MS / (256 * 60_000)) * 256 + 1) * 60_000;
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    const view = pendingView(submissionId, new Date(scheduledTime - 60_000).toISOString());
    state.views.set(submissionId, view);
    state.outbox.set(submissionId, {
      schema_version: 1,
      submission_id: submissionId,
      owner_login: view.owner_login,
      submission: view.submission,
      attempts: 0,
      next_attempt_at: new Date(scheduledTime - 1).toISOString(),
      workflow_ref: view.dispatch.workflow_ref,
    });
    const update = vi.spyOn(state, "updateDispatch").mockRejectedValue(new Error("State unavailable"));
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());

    await expect(handleScheduled(ENV, scheduledTime, { state, dispatch })).rejects.toThrow("State unavailable");

    expect(dispatch).toHaveBeenCalledOnce();
    expect(update).toHaveBeenCalledOnce();
    expect(state.outbox).toHaveLength(1);
  });

  it("removes an ordinary outbox after its terminal retry bound", async () => {
    const state = new MemoryState();
    const scheduledTime = (Math.floor(NOW_MS / (256 * 60_000)) * 256 + 1) * 60_000;
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    const view = pendingView(
      submissionId,
      new Date(scheduledTime - 60_000).toISOString(),
      32,
      "failed",
    );
    state.views.set(submissionId, view);
    state.outbox.set(submissionId, {
      schema_version: 1,
      submission_id: submissionId,
      owner_login: view.owner_login,
      submission: view.submission,
      attempts: 32,
      next_attempt_at: new Date(scheduledTime - 1).toISOString(),
      workflow_ref: view.dispatch.workflow_ref,
    });
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);

    await handleScheduled(ENV, scheduledTime, { state, dispatch });

    expect(dispatch).not.toHaveBeenCalled();
    expect(state.outbox).toHaveLength(0);
    expect(log).toHaveBeenCalledWith(expect.stringContaining("submission_dispatch_terminal_retry_exhausted"));
    log.mockRestore();
  });

  it("does not let a canary-looking model label bypass production reconciliation", async () => {
    const state = new MemoryState();
    const scheduledTime = (Math.floor(NOW_MS / (256 * 60_000)) * 256 + 1) * 60_000;
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    const submission = {
      ...SUBMISSION,
      declared_model: "lean-eval automatic staging promotion canary v3",
    };
    const view = {
      ...pendingView(submissionId, new Date(scheduledTime - 60_000).toISOString(), 1, "failed"),
      submission,
    } satisfies SubmissionView;
    state.views.set(submissionId, view);
    state.outbox.set(submissionId, {
      schema_version: 1,
      submission_id: submissionId,
      owner_login: "alice",
      submission,
      attempts: 1,
      next_attempt_at: new Date(scheduledTime - 1).toISOString(),
      workflow_ref: view.dispatch.workflow_ref,
    });
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    await handleScheduled({
      ...ENV,
      DEPLOYMENT_ENVIRONMENT: "production",
      STATE_REPOSITORY: "leanprover/lean-eval-state",
    }, scheduledTime, { state, dispatch });
    expect(dispatch).toHaveBeenCalledOnce();
    expect(state.outbox).toHaveLength(0);
  });
});

describe("automatic staging promotion canary in workerd", () => {
  const commit = "c".repeat(40);
  const canaryEnv = {
    ...ENV,
    DEPLOYED_COMMIT: commit,
    DISPATCH_WORKFLOW_REF: `lean-eval-dispatch/${commit}`,
    INTAKE_ENABLED: "false",
    PROMOTION_CANARY_ENABLED: "true",
    READINESS_TOKEN: SECRET,
    STATE_REPOSITORY: "leanprover/lean-eval-state-staging",
  } satisfies RuntimeEnv;
  const canaryBody = {
    schema_version: 2,
    deployed_commit: commit,
    dispatch_ref: `lean-eval-dispatch/${commit}`,
    controller_run_id: "32712345678",
    controller_run_attempt: "1",
  };

  function canaryRequest(): Request {
    return new Request("https://submit.test/internal/v1/promotion-canary", {
      method: "POST",
      headers: {
        authorization: `Bearer ${SECRET}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(canaryBody),
    });
  }

  it("defers exact synthetic intake to the actual scheduled handler and is idempotent", async () => {
    const state = new MemoryState();
    const source = vi.fn<typeof fetch>(() => Promise.resolve(Response.json({
      full_name: "kim-em/lean-eval-intake-fixture",
      private: true,
    })));
    const github = new GitHubProvider(undefined, undefined, source);
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    const first = await handleRequest(canaryRequest(), canaryEnv, LIFECYCLE, {
      provider: github,
      state,
      dispatch,
    });
    expect(first.status).toBe(202);
    const firstBody = await first.json<Record<string, unknown>>();
    expect(firstBody).toMatchObject({
      status: "awaiting_scheduled_reconciliation",
      environment: "staging",
      deployed_commit: commit,
      dispatch_ref: `lean-eval-dispatch/${commit}`,
      controller_run_id: "32712345678",
      controller_run_attempt: "1",
      github_connectivity: "verified",
      synthetic_intake: "created",
      cas_contention: "collision_observed_and_retry_applied",
      dispatch_state: "pending",
      workflow_dispatch: "pending",
      scheduled_reconciliation: "pending",
    });
    expect(JSON.stringify(firstBody)).not.toContain("source_repository");
    expect(JSON.stringify(firstBody)).not.toContain("ae38f4d3");
    expect(dispatch).not.toHaveBeenCalled();
    expect(state.outbox).toHaveLength(1);

    const canaryId = String(firstBody.submission_id);
    const canaryMilliseconds = Number.parseInt(
      canaryId.replaceAll("-", "").slice(0, 12),
      16,
    );
    expect(canaryMilliseconds).toBeGreaterThanOrEqual(Date.UTC(2026, 7, 21));
    expect(canaryMilliseconds).toBeLessThan(Date.UTC(2026, 7, 22));
    expect(canaryMilliseconds).toBeGreaterThan(
      Date.parse("2026-08-20T06:47:06.000Z"),
    );
    const unrelatedId = `019debcf-f258-7000-8000-0000000000${canaryId.slice(-2)}`;
    const canaryOutbox = [...state.outbox.values()][0];
    if (canaryOutbox === undefined) throw new Error("canary outbox was not persisted");
    state.outbox.set(unrelatedId, {
      ...canaryOutbox,
      submission_id: unrelatedId,
    });

    const laterCommit = "d".repeat(40);
    await handleScheduled({
      ...canaryEnv,
      DEPLOYED_COMMIT: laterCommit,
      DISPATCH_WORKFLOW_REF: `lean-eval-dispatch/${laterCommit}`,
    }, Date.UTC(2026, 7, 25), { state, dispatch });
    expect(dispatch).toHaveBeenCalledOnce();
    const dispatched = dispatch.mock.calls[0]?.[0];
    expect(dispatched?.url).toContain("/actions/workflows/promotion-canary.yml/dispatches");
    expect(await dispatched?.clone().json()).toEqual({
      ref: `lean-eval-dispatch/${commit}`,
      inputs: {
        workflow_commit: commit,
        submission_id: canaryId,
        controller_run_id: "32712345678",
        controller_run_attempt: "1",
      },
    });
    expect(state.outbox).toHaveLength(1);
    expect(state.outbox.has(unrelatedId)).toBe(true);

    const second = await handleRequest(canaryRequest(), canaryEnv, LIFECYCLE, {
      provider: github,
      state,
      dispatch,
    });
    expect(second.status).toBe(200);
    expect(await second.json()).toMatchObject({
      status: "passed",
      submission_id: firstBody.submission_id,
      synthetic_intake: "idempotent",
      cas_contention: "idempotent_prior_collision_and_retry_proof",
      dispatch_state: "succeeded",
      workflow_dispatch: "accepted_by_github",
      scheduled_reconciliation: "completed",
    });
    expect(state.views).toHaveLength(1);
    expect(dispatch).toHaveBeenCalledOnce();
  });

  it("creates fresh material per workflow attempt and reconciles every prior run", async () => {
    const state = new MemoryState();
    const source = vi.fn<typeof fetch>(() => Promise.resolve(Response.json({
      full_name: "kim-em/lean-eval-intake-fixture",
      private: true,
    })));
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    const ids: string[] = [];
    for (const runAttempt of ["1", "2"]) {
      const request = new Request("https://submit.test/internal/v1/promotion-canary", {
        method: "POST",
        headers: {
          authorization: `Bearer ${SECRET}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ ...canaryBody, controller_run_attempt: runAttempt }),
      });
      const response = await handleRequest(request, canaryEnv, LIFECYCLE, {
        provider: new GitHubProvider(undefined, undefined, source),
        state,
        dispatch,
      });
      expect(response.status).toBe(202);
      const body = await response.json<Record<string, unknown>>();
      expect(body.synthetic_intake).toBe("created");
      expect(body.cas_contention).toBe("collision_observed_and_retry_applied");
      ids.push(String(body.submission_id));
    }
    expect(new Set(ids).size).toBe(2);
    expect(state.outbox).toHaveLength(2);
    await handleScheduled(canaryEnv, Date.UTC(2026, 7, 25), { state, dispatch });
    expect(dispatch).toHaveBeenCalledTimes(2);
    expect(state.outbox).toHaveLength(0);
  });

  it("reconciles one complete fixed-shard canary scan when operations are in-memory", async () => {
    const state = new MemoryState();
    const source = vi.fn<typeof fetch>(() => Promise.resolve(Response.json({
      full_name: "kim-em/lean-eval-intake-fixture",
      private: true,
    })));
    const github = new GitHubProvider(undefined, undefined, source);
    for (let index = 0; index < 21; index += 1) {
      const request = new Request("https://submit.test/internal/v1/promotion-canary", {
        method: "POST",
        headers: {
          authorization: `Bearer ${SECRET}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ ...canaryBody, controller_run_id: String(1000 + index) }),
      });
      const response = await handleRequest(request, canaryEnv, LIFECYCLE, {
        provider: github,
        state,
        dispatch: () => Promise.resolve(),
      });
      expect(response.status).toBe(202);
    }
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    await handleScheduled(canaryEnv, Date.UTC(2026, 7, 25), { state, dispatch });
    expect(dispatch).toHaveBeenCalledTimes(21);
    expect(state.outbox).toHaveLength(0);
  });

  it("records a failed no-op dispatch and reports it without claiming success", async () => {
    const state = new MemoryState();
    const source = vi.fn<typeof fetch>(() => Promise.resolve(Response.json({
      full_name: "kim-em/lean-eval-intake-fixture",
      private: true,
    })));
    const github = new GitHubProvider(undefined, undefined, source);
    const failedDispatch = vi.fn<(request: Request) => Promise<void>>(() =>
      Promise.reject(new Error("source-free test failure")));
    await handleRequest(canaryRequest(), canaryEnv, LIFECYCLE, {
      provider: github,
      state,
      dispatch: failedDispatch,
    });
    await handleScheduled(canaryEnv, Date.UTC(2026, 7, 25), {
      state,
      dispatch: failedDispatch,
    });
    const response = await handleRequest(canaryRequest(), canaryEnv, LIFECYCLE, {
      provider: github,
      state,
      dispatch: failedDispatch,
    });
    expect(response.status).toBe(202);
    expect(await response.json()).toMatchObject({
      status: "dispatch_failed",
      dispatch_state: "failed",
      workflow_dispatch: "retry_pending",
      scheduled_reconciliation: "retry_pending",
    });
  });

  it("terminally removes an exact canary outbox after the bounded retry limit", async () => {
    const state = new MemoryState();
    const source = vi.fn<typeof fetch>(() => Promise.resolve(Response.json({
      full_name: "kim-em/lean-eval-intake-fixture",
      private: true,
    })));
    const response = await handleRequest(canaryRequest(), canaryEnv, LIFECYCLE, {
      provider: new GitHubProvider(undefined, undefined, source),
      state,
      dispatch: () => Promise.resolve(),
    });
    const submissionId = String((await response.json<Record<string, unknown>>()).submission_id);
    const view = state.views.get(submissionId);
    const outbox = state.outbox.get(submissionId);
    if (!view || !outbox) throw new Error("canary material was not persisted");
    state.views.set(submissionId, {
      ...view,
      dispatch: {
        ...view.dispatch,
        status: "failed",
        attempts: 32,
        last_error_code: "dispatch_provider_unavailable",
      },
    });
    state.outbox.set(submissionId, {
      ...outbox,
      attempts: 32,
      next_attempt_at: "2026-08-20T00:00:00.000Z",
    });
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    await handleScheduled(canaryEnv, Date.UTC(2026, 7, 25), { state, dispatch });
    expect(dispatch).not.toHaveBeenCalled();
    expect(state.outbox.has(submissionId)).toBe(false);
    expect(log).toHaveBeenCalledWith(expect.stringContaining("promotion_canary_terminal_retry_exhausted"));
    log.mockRestore();
  });

  it("contains canary scheduler errors and still reconciles ordinary staging intake", async () => {
    const state = new MemoryState();
    const source = vi.fn<typeof fetch>(() => Promise.resolve(Response.json({
      full_name: "kim-em/lean-eval-intake-fixture",
      private: true,
    })));
    await handleRequest(canaryRequest(), canaryEnv, LIFECYCLE, {
      provider: new GitHubProvider(undefined, undefined, source),
      state,
      dispatch: () => Promise.resolve(),
    });
    const exactCanary = [...state.outbox.values()][0];
    if (exactCanary === undefined) throw new Error("canary outbox was not persisted");
    state.outbox.clear();
    const corruptCanaryId = "019debcf-cb48-7000-8000-0000000000ca";
    state.outbox.set(corruptCanaryId, { ...exactCanary, submission_id: corruptCanaryId });

    const ordinaryId = "019debcf-f258-7000-8000-0000000000ca";
    const ordinary = pendingView(ordinaryId, "2026-08-20T00:00:00.000Z");
    const workflowRef = `lean-eval-dispatch/${commit}`;
    const ordinaryView = {
      ...ordinary,
      owner_login: "kim-em",
      dispatch: { ...ordinary.dispatch, workflow_ref: workflowRef },
    } satisfies SubmissionView;
    state.views.set(ordinaryId, ordinaryView);
    state.outbox.set(ordinaryId, {
      schema_version: 1,
      submission_id: ordinaryId,
      owner_login: ordinaryView.owner_login,
      submission: ordinaryView.submission,
      attempts: 0,
      next_attempt_at: ordinaryView.accepted_at,
      workflow_ref: workflowRef,
    });
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    await handleScheduled(
      { ...canaryEnv, INTAKE_ENABLED: "true" },
      1_787_624_280_000,
      { state, dispatch },
    );
    expect(log).toHaveBeenCalledWith(expect.stringContaining("promotion_canary_scheduled_scan_failed"));
    expect(dispatch).toHaveBeenCalledOnce();
    expect(dispatch.mock.calls[0]?.[0].url).toContain("/submission.yml/dispatches");
    expect(state.outbox.has(ordinaryId)).toBe(false);
    expect(state.outbox.has(corruptCanaryId)).toBe(true);
    log.mockRestore();
  });

  it("builds only the dedicated source-free canary dispatch contract", async () => {
    const request = buildPromotionCanaryDispatchRequest(
      "leanprover/lean-eval-submissions",
      `lean-eval-dispatch/${commit}`,
      "019debcf-cb48-7000-8000-0000000000ca",
      "32712345678",
      "3",
    );
    expect(request.url).toContain("/promotion-canary.yml/dispatches");
    const body = await request.json<Record<string, unknown>>();
    expect(JSON.stringify(body)).not.toMatch(/source|archive|audit|evaluation|result/iu);
    expect(() => buildPromotionCanaryDispatchRequest(
      "leanprover/lean-eval-submissions",
      `lean-eval-dispatch/${commit}`,
      "019debcf-cb48-7000-8000-000000000001",
      "32712345678",
      "3",
    )).toThrow(/identity/iu);
  });

  it("hides the route from production even with the staging flag and readiness token", async () => {
    const state = new MemoryState();
    const list = vi.spyOn(state, "listDispatchOutbox");
    const dispatch = vi.fn<(request: Request) => Promise<void>>(() => Promise.resolve());
    const productionEnv = { ...canaryEnv, DEPLOYMENT_ENVIRONMENT: "production" } as const;
    const response = await handleRequest(
      canaryRequest(),
      productionEnv,
      LIFECYCLE,
      { state },
    );
    expect(response.status).toBe(404);
    expect(state.views).toHaveLength(0);
    await handleScheduled(productionEnv, Date.UTC(2026, 7, 25), { state, dispatch });
    expect(list).not.toHaveBeenCalled();
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("fails closed when the request is unauthenticated or the immutable ref is not exact", async () => {
    const state = new MemoryState();
    const unauthenticated = await handleRequest(
      new Request("https://submit.test/internal/v1/promotion-canary", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(canaryBody),
      }),
      canaryEnv,
      LIFECYCLE,
      { state, dispatch: () => Promise.resolve() },
    );
    expect(unauthenticated.status).toBe(404);
    const noncanonical = await handleRequest(
      new Request("https://submit.test/internal/v1/promotion-canary", {
        method: "POST",
        headers: {
          authorization: `Bearer ${SECRET}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ ...canaryBody, source_repository: "attacker/repository" }),
      }),
      canaryEnv,
      LIFECYCLE,
      { state, dispatch: () => Promise.resolve() },
    );
    expect(noncanonical.status).toBe(400);
    const invalidRun = await handleRequest(
      new Request("https://submit.test/internal/v1/promotion-canary", {
        method: "POST",
        headers: {
          authorization: `Bearer ${SECRET}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ ...canaryBody, controller_run_attempt: "000001" }),
      }),
      canaryEnv,
      LIFECYCLE,
      { state, dispatch: () => Promise.resolve() },
    );
    expect(invalidRun.status).toBe(400);
    const misbound = await handleRequest(
      canaryRequest(),
      { ...canaryEnv, DISPATCH_WORKFLOW_REF: `lean-eval-dispatch/${"d".repeat(40)}` },
      LIFECYCLE,
      { state, dispatch: () => Promise.resolve() },
    );
    expect(misbound.status).toBe(503);
    expect(state.views).toHaveLength(0);
  });
});

describe("browser OAuth and owner routes in workerd", () => {
  it("verifies and discards the OAuth token before atomically consuming bound state", async () => {
    const state = new MemoryState();
    const oauthToken = "oauth-token-never-persisted";
    const upstream = vi.fn<typeof fetch>((input, init) => {
      const url = typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
      if (url === "https://github.com/login/oauth/access_token") {
        const body = init?.body;
        if (!(body instanceof URLSearchParams)) throw new Error("OAuth body was not form encoded");
        expect(body.toString()).toContain("code=abcdefgh");
        return Promise.resolve(Response.json({ access_token: oauthToken, token_type: "bearer" }));
      }
      if (url === "https://api.github.com/user") {
        expect(new Headers(init?.headers).get("authorization")).toBe(`Bearer ${oauthToken}`);
        return Promise.resolve(Response.json({ id: 42, login: "Alice" }));
      }
      return Promise.reject(new Error(`unexpected provider request: ${url}`));
    });
    const oauthEnv: RuntimeEnv = {
      ...ENV,
      BROWSER_SUCCESS_URL: "/preview/",
      GITHUB_OAUTH_CLIENT_ID: "client-id",
      GITHUB_OAUTH_CLIENT_SECRET: "client-secret",
      OAUTH_CALLBACK_URL: "https://submit.test/api/v1/oauth/callback",
    };
    const start = await handleRequest(
      new Request("https://submit.test/api/v1/oauth/start"),
      oauthEnv,
      LIFECYCLE,
      { now: () => NOW_MS, provider: new GitHubProvider(upstream), state },
    );
    expect(start.status).toBe(302);
    const location = new URL(start.headers.get("location") ?? "");
    const signedState = location.searchParams.get("state") ?? "";
    expect(signedState).not.toBe("");
    const boundCookie = /lean_eval_oauth_state=([^;]+)/u.exec(start.headers.get("set-cookie") ?? "")?.[1] ?? "";
    expect(boundCookie).toBe(signedState);

    const callbackUrl = new URL(oauthEnv.OAUTH_CALLBACK_URL ?? "");
    callbackUrl.search = new URLSearchParams({ code: "abcdefgh", state: signedState }).toString();
    const callback = await handleRequest(
      new Request(callbackUrl, { headers: { cookie: `lean_eval_oauth_state=${boundCookie}` } }),
      oauthEnv,
      LIFECYCLE,
      { now: () => NOW_MS, provider: new GitHubProvider(upstream), state },
    );
    expect(callback.status).toBe(303);
    expect(callback.headers.get("location")).toBe("https://submit.test/preview/");
    const callbackCookies = callback.headers.getSetCookie();
    expect(callbackCookies).toHaveLength(2);
    expect(callbackCookies[0]).toMatch(/^lean_eval_session=/u);
    expect(callbackCookies[1]).toBe("lean_eval_oauth_state=deleted; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax");
    expect(JSON.stringify(state.events)).not.toContain(oauthToken);
    expect(state.events).toHaveLength(1);
    expect(state.events[0]?.event_type).toBe("authentication.nonce_consumed");

    state.created = false;
    const replay = await handleRequest(
      new Request(callbackUrl, { headers: { cookie: `lean_eval_oauth_state=${boundCookie}` } }),
      oauthEnv,
      LIFECYCLE,
      { now: () => NOW_MS, provider: new GitHubProvider(upstream), state },
    );
    expect(replay.status).toBe(401);
    expect(upstream).toHaveBeenCalledTimes(4);
  });

  it("returns owner status and appends linear idempotent metadata/publication events", async () => {
    const state = new MemoryState();
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    state.events.push(
      {
        schema_version: 1,
        event_id: submissionId,
        event_type: "submission.received",
        occurred_at: "2026-01-01T00:00:00.000Z",
        subject_id: submissionId,
        causation_event_id: null,
        actor: { kind: "github", login: "alice" },
        payload: {
          problem_id: "two_plus_two",
          statement_revision: 2,
          declared_model: "Example Model",
          source_repository: "alice/proofs",
          source_commit: "a".repeat(40),
          source_visibility: "private",
          publication_choice: "scheduled",
        },
      },
      {
        schema_version: 1,
        event_id: "019debcf-cb48-7000-8000-000000000002",
        event_type: "submission.metadata_amended",
        occurred_at: "2026-01-01T00:00:01.000Z",
        subject_id: submissionId,
        causation_event_id: submissionId,
        actor: { kind: "github", login: "alice" },
        payload: { production_metadata: { web_access: false } },
      },
    );
    state.views.set(submissionId, {
      schema_version: 1,
      submission_id: submissionId,
      owner_login: "alice",
      received_event_id: submissionId,
      mutation_event_id: "019debcf-cb48-7000-8000-000000000002",
      metadata_event_id: "019debcf-cb48-7000-8000-000000000002",
      publication_event_id: null,
      accepted_at: "2026-01-01T00:00:00.000Z",
      submission: { ...SUBMISSION, production_metadata: { web_access: false } },
      production_metadata: { web_access: false },
      publication_choice: "scheduled",
      archive: { status: "pending" },
      evaluation: { status: "pending" },
      result_id: null,
      dispatch: {
        status: "succeeded",
        attempts: 1,
        requested_at: "2026-01-01T00:00:00.000Z",
        updated_at: "2026-01-01T00:00:03.000Z",
        workflow_ref: `lean-eval-dispatch/${"b".repeat(40)}`,
        last_error_code: null,
      },
    });
    const alice: BrowserSession = {
      kind: "browser_session",
      login: "alice",
      github_id: 42,
      issued_at: Math.floor(NOW_MS / 1000),
      expires_at: Math.floor(NOW_MS / 1000) + 3600,
    };
    const authorization = `Bearer ${await signToken(SECRET, alice)}`;
    const patch = await handleRequest(
      new Request(`https://submit.test/api/v1/submissions/${submissionId}/metadata`, {
        method: "PATCH",
        headers: {
          authorization,
          "content-type": "application/json",
          "idempotency-key": "019debcf-cb48-7000-8000-000000000003",
        },
        body: JSON.stringify({ production_metadata: { notes: "amended" } }),
      }),
      ENV,
      LIFECYCLE,
      { now: () => NOW_MS, state },
    );
    expect(patch.status).toBe(200);
    expect(state.events.at(-1)).toMatchObject({
      event_type: "submission.metadata_amended",
      causation_event_id: "019debcf-cb48-7000-8000-000000000002",
    });

    const publication = await handleRequest(
      new Request(`https://submit.test/api/v1/submissions/${submissionId}/publication`, {
        method: "PUT",
        headers: {
          authorization,
          "content-type": "application/json",
          "idempotency-key": "019debcf-cb48-7000-8000-000000000004",
        },
        body: JSON.stringify({ publication_choice: "withheld" }),
      }),
      ENV,
      LIFECYCLE,
      { now: () => NOW_MS, state },
    );
    expect(publication.status).toBe(200);
    expect(state.events.at(-1)).toMatchObject({
      event_type: "submission.publication_changed",
      causation_event_id: "019debcf-cb48-7000-8000-000000000003",
    });

    const status = await handleRequest(
      new Request(`https://submit.test/api/v1/submissions/${submissionId}`, { headers: { authorization } }),
      ENV,
      LIFECYCLE,
      { now: () => NOW_MS, state },
    );
    expect(status.status).toBe(200);
    await expect(status.json()).resolves.toMatchObject({
      submission_id: submissionId,
      production_metadata: { notes: "amended" },
      publication_choice: "withheld",
    });

    const bob = await signToken(SECRET, { ...alice, login: "bob" });
    const hidden = await handleRequest(
      new Request(`https://submit.test/api/v1/submissions/${submissionId}`, {
        headers: { authorization: `Bearer ${bob}` },
      }),
      ENV,
      LIFECYCLE,
      { now: () => NOW_MS, state },
    );
    expect(hidden.status).toBe(404);
  });
});

describe("authenticated legacy result owner routes", () => {
  const enabledEnv: RuntimeEnv = {
    ...ENV,
    INTAKE_ENABLED: "false",
    LEGACY_RESULT_OWNER_API_ENABLED: "true",
    RESULT_OWNER_STATE_CONTRACT_COMMIT: "48f8c975d725a9ac18df545653fdb2f8371c3293",
  };

  async function ownerAuthorization(login = "alice"): Promise<string> {
    const browser: BrowserSession = {
      kind: "browser_session",
      login,
      github_id: login === "alice" ? 42 : 43,
      issued_at: Math.floor(NOW_MS / 1000),
      expires_at: Math.floor(NOW_MS / 1000) + 3600,
    };
    return `Bearer ${await signToken(SECRET, browser)}`;
  }

  function comparatorEvidence(identifier: string, ownerLogin: string): ComparatorEvidence {
    return {
      repository: "leanprover/lean-eval-submissions",
      commit: "a".repeat(40),
      path: `results/${ownerLogin}.json`,
      blob_oid: "b".repeat(40),
      blob_sha256: "c".repeat(64),
      record_sha256: "d".repeat(64),
      binding_sha256: "e".repeat(64),
      verification_method: "github_commit_blob_v1",
      evidence_result_id: identifier,
      evidence_owner_login: ownerLogin,
      evidence_declared_model: "Example Model",
      evidence_base_problem_group: "formalization-evaluation",
      evidence_base_problem_id: "two_plus_two",
      evidence_base_statement_revision: 1,
      evidence_base_challenge_id: `ch1_${"1".repeat(64)}`,
      evidence_corrected_problem_group: "formalization-evaluation",
      evidence_corrected_problem_id: "two_plus_three",
      evidence_corrected_statement_revision: 2,
      evidence_corrected_challenge_id: `ch1_${"2".repeat(64)}`,
    };
  }

  it("keeps the route dark unless both reviewed configuration values are exact", async () => {
    const request = new Request("https://submit.test/api/v1/results/claims", { method: "POST" });
    const disabled = await handleRequest(request.clone(), { ...ENV, INTAKE_ENABLED: "false" }, LIFECYCLE);
    expect(disabled.status).toBe(404);
    const wrongContract = await handleRequest(request, {
      ...ENV,
      INTAKE_ENABLED: "false",
      LEGACY_RESULT_OWNER_API_ENABLED: "true",
      RESULT_OWNER_STATE_CONTRACT_COMMIT: "b".repeat(40),
    }, LIFECYCLE);
    expect(wrongContract.status).toBe(404);
  });

  it("claims an exact owner record while production intake stays disabled and redacts source bindings", async () => {
    const identifier = `r2_${await digest(
      `lean-eval-result-v2\0${JSON.stringify(["alice", "Example Model", "two_plus_two", 1])}`,
    )}`;
    const record = {
      result_id: identifier,
      problem_id: "two_plus_two",
      statement_revision: 1,
      declared_model: "Example Model",
      accepted_at: "2024-01-02T03:04:05Z",
      benchmark_commit: "c".repeat(40),
      intake: { kind: "issue", issue_number: 42 },
      submission: { kind: "gist", repo: "alice/abcdef", ref: "d".repeat(40), public: false },
      production_metadata: { solution_publication_status: "private" },
    };
    const documentBytes = new TextEncoder().encode(JSON.stringify({
      schema_version: 2,
      user: "alice",
      results: [record],
    }));
    let binary = "";
    for (const byte of documentBytes) binary += String.fromCharCode(byte);
    const resultFetch = vi.fn<typeof fetch>(() => Promise.resolve(Response.json({
      type: "file",
      path: "results/alice.json",
      encoding: "base64",
      content: btoa(binary),
      size: documentBytes.byteLength,
    })));
    const state = new MemoryState();
    const response = await handleRequest(new Request("https://submit.test/api/v1/results/claims", {
      method: "POST",
      headers: {
        authorization: await ownerAuthorization(),
        "content-type": "application/json",
        "idempotency-key": "019debd0-1968-7000-8000-000000000001",
      },
      body: JSON.stringify({ result_id: identifier, results_commit: "e".repeat(40) }),
    }), enabledEnv, LIFECYCLE, {
      now: () => NOW_MS,
      provider: new GitHubProvider(
        undefined,
        undefined,
        undefined,
        undefined,
        reachableLegacyResultFetch(resultFetch),
        "staging-results",
      ),
      state,
    });
    expect(response.status).toBe(201);
    const responseBody = await response.json();
    expect(responseBody).toEqual({ result_id: identifier, status: "claimed" });
    expect(state.legacyClaims).toHaveLength(1);
    expect(state.contractAssertions).toBe(0);
    expect(state.legacyClaims[0]).toMatchObject({
      eventId: "019debd0-1968-7000-8000-000000000001",
      occurredAt: new Date(NOW_MS).toISOString(),
      verified: {
        resultId: identifier,
        ownerLogin: "alice",
        baseResult: {
          results_commit: "e".repeat(40),
          results_path: "results/alice.json",
        },
      },
    });
    const publicResponse = JSON.stringify(responseBody);
    expect(publicResponse).not.toContain("results/alice.json");
    expect(publicResponse).not.toContain("e".repeat(40));
    expect(enabledEnv.INTAKE_ENABLED).toBe("false");
  });

  it("binds metadata backfill to the session, request body, and idempotency event", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const response = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/metadata`,
      {
        method: "PATCH",
        headers: {
          authorization: await ownerAuthorization(),
          "content-type": "application/json",
          "idempotency-key": "019debd0-1968-7000-8000-000000000002",
        },
        body: JSON.stringify({ production_metadata: { notes: "historical note", web_access: false } }),
      },
    ), enabledEnv, LIFECYCLE, { now: () => NOW_MS, state });
    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ result_id: identifier, status: "backfilled" });
    expect(state.legacyBackfills).toEqual([{
      eventId: "019debd0-1968-7000-8000-000000000002",
      occurredAt: new Date(NOW_MS).toISOString(),
      resultId: identifier,
      ownerLogin: "alice",
      productionMetadata: { notes: "historical note", web_access: false },
    }]);
    expect(state.contractAssertions).toBe(0);
  });

  it("rejects a far-future idempotency event before invoking State", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const response = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/metadata`,
      {
        method: "PATCH",
        headers: {
          authorization: await ownerAuthorization(),
          "content-type": "application/json",
          "idempotency-key": "ffffffff-ffff-7fff-bfff-ffffffffffff",
        },
        body: JSON.stringify({ production_metadata: { web_access: false } }),
      },
    ), enabledEnv, LIFECYCLE, { now: () => NOW_MS, state });
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({
      error: "invalid_request",
      detail: "Idempotency-Key timestamp must not be in the future",
    });
    expect(state.legacyBackfills).toHaveLength(0);
  });

  it("preserves millisecond ordering for owner mutations in the same second", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const request = (eventId: string) => new Request(
      `https://submit.test/api/v1/results/${identifier}/metadata`,
      {
        method: "PATCH",
        headers: {
          authorization: "placeholder",
          "content-type": "application/json",
          "idempotency-key": eventId,
        },
        body: JSON.stringify({ production_metadata: { web_access: false } }),
      },
    );
    const first = request("019debd0-1968-7000-8000-000000000008");
    first.headers.set("authorization", await ownerAuthorization());
    const second = request("019debd0-1968-7000-8000-000000000009");
    second.headers.set("authorization", await ownerAuthorization());
    expect((await handleRequest(first, enabledEnv, LIFECYCLE, {
      now: () => NOW_MS + 123,
      state,
    })).status).toBe(201);
    expect((await handleRequest(second, enabledEnv, LIFECYCLE, {
      now: () => NOW_MS + 456,
      state,
    })).status).toBe(201);
    expect(state.legacyBackfills.map((entry) => entry.occurredAt)).toEqual([
      new Date(NOW_MS + 123).toISOString(),
      new Date(NOW_MS + 456).toISOString(),
    ]);
  });

  it("rejects missing authentication and empty metadata without invoking State", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const unauthorized = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/metadata`,
      { method: "PATCH" },
    ), enabledEnv, LIFECYCLE, { now: () => NOW_MS, state });
    expect(unauthorized.status).toBe(401);
    const empty = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/metadata`,
      {
        method: "PATCH",
        headers: {
          authorization: await ownerAuthorization(),
          "content-type": "application/json",
          "idempotency-key": "019debd0-1968-7000-8000-000000000003",
        },
        body: JSON.stringify({ production_metadata: {} }),
      },
    ), enabledEnv, LIFECYCLE, { now: () => NOW_MS, state });
    expect(empty.status).toBe(400);
    expect(state.legacyBackfills).toHaveLength(0);
  });

  it("preserves owner-operation CAS exhaustion as an explicit 409 conflict", async () => {
    const state = new MemoryState();
    vi.spyOn(state, "backfillLegacyResultMetadata").mockRejectedValue(
      new ResultOwnerStateError(409, "State branch kept changing"),
    );
    const identifier = `r2_${"1".repeat(64)}`;
    const response = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/metadata`,
      {
        method: "PATCH",
        headers: {
          authorization: await ownerAuthorization(),
          "content-type": "application/json",
          "idempotency-key": "019debd0-1968-7000-8000-000000000004",
        },
        body: JSON.stringify({ production_metadata: { web_access: false } }),
      },
    ), enabledEnv, LIFECYCLE, { now: () => NOW_MS, state });
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: "idempotency_conflict" });
  });

  it("maps an unknown State update outcome to a retryable 503", async () => {
    const state = new MemoryState();
    vi.spyOn(state, "backfillLegacyResultMetadata").mockRejectedValue(
      new StateUpdateOutcomeUnknownError(),
    );
    const identifier = `r2_${"1".repeat(64)}`;
    const response = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/metadata`,
      {
        method: "PATCH",
        headers: {
          authorization: await ownerAuthorization(),
          "content-type": "application/json",
          "idempotency-key": "019debd0-1968-7000-8000-000000000006",
        },
        body: JSON.stringify({ production_metadata: { web_access: false } }),
      },
    ), enabledEnv, LIFECYCLE, { now: () => NOW_MS, state });
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "state_unavailable" });
  });

  it("maps a first-authority identity collision to a terminal 409", async () => {
    const state = new MemoryState();
    vi.spyOn(state, "backfillLegacyResultMetadata").mockRejectedValue(
      new ResultIdentityCollisionError("recorded"),
    );
    const identifier = `r2_${"1".repeat(64)}`;
    const response = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/metadata`,
      {
        method: "PATCH",
        headers: {
          authorization: await ownerAuthorization(),
          "content-type": "application/json",
          "idempotency-key": "019debd0-1968-7000-8000-000000000007",
        },
        body: JSON.stringify({ production_metadata: { web_access: false } }),
      },
    ), enabledEnv, LIFECYCLE, { now: () => NOW_MS, state });
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: "result_identity_conflict" });
  });

  it("does not expose private provider details in a response or structured log", async () => {
    const sensitive = "private-owner/repository oauth-secret-value";
    const resultFetch = vi.fn<typeof fetch>(() => Promise.resolve(new Response(sensitive, { status: 503 })));
    const logged: string[] = [];
    const errorLog = vi.spyOn(console, "error").mockImplementation((value: unknown) => {
      logged.push(String(value));
    });
    try {
      const response = await handleRequest(new Request("https://submit.test/api/v1/results/claims", {
        method: "POST",
        headers: {
          authorization: await ownerAuthorization(),
          "content-type": "application/json",
          "idempotency-key": "019debd0-1968-7000-8000-000000000005",
        },
        body: JSON.stringify({ result_id: `r2_${"1".repeat(64)}`, results_commit: "e".repeat(40) }),
      }), enabledEnv, LIFECYCLE, {
        now: () => NOW_MS,
        provider: new GitHubProvider(undefined, undefined, undefined, undefined, resultFetch, "staging-results"),
        state: new MemoryState(),
      });
      const publicBody = await response.text();
      expect(response.status).toBe(503);
      expect(publicBody).not.toContain(sensitive);
      expect(logged.join("\n")).not.toContain(sensitive);
      expect(logged).toEqual([JSON.stringify({
        event: "submission_stage_failed",
        stage: "legacy_result_verification",
        error_name: "GitHubProviderError",
      })]);
    } finally {
      errorLog.mockRestore();
    }
  });

  it("keeps result amendment routes dark unless their independent reviewed gate is exact", async () => {
    const identifier = `r2_${"1".repeat(64)}`;
    const request = new Request(
      `https://submit.test/api/v1/results/${identifier}/retractions`,
      { method: "POST" },
    );
    const disabled = await handleRequest(request.clone(), enabledEnv, LIFECYCLE);
    expect(disabled.status).toBe(404);
    const wrongContract = await handleRequest(request, {
      ...enabledEnv,
      RESULT_AMENDMENT_OWNER_API_ENABLED: "true",
      RESULT_OWNER_STATE_CONTRACT_COMMIT: "b".repeat(40),
    }, LIFECYCLE);
    expect(wrongContract.status).toBe(404);
  });

  it("records a redacted owner retraction request while intake stays disabled", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const response = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/retractions`,
      {
        method: "POST",
        headers: {
          authorization: await ownerAuthorization(),
          "content-type": "application/json",
          "idempotency-key": "019debd0-1968-7000-8000-000000000011",
        },
        body: JSON.stringify({ reason_code: "owner_requested_withdrawal" }),
      },
    ), {
      ...enabledEnv,
      RESULT_AMENDMENT_OWNER_API_ENABLED: "true",
    }, LIFECYCLE, { now: () => NOW_MS, state });
    expect(response.status).toBe(201);
    const body = await response.json();
    expect(body).toEqual({
      result_id: identifier,
      retraction_revision: 1,
      status: "retraction_requested",
    });
    expect(JSON.stringify(body)).not.toContain("owner_requested_withdrawal");
    expect(JSON.stringify(body)).not.toContain("alice");
    expect(state.retractionRequests).toEqual([{
      eventId: "019debd0-1968-7000-8000-000000000011",
      occurredAt: new Date(NOW_MS).toISOString(),
      resultId: identifier,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    }]);
    expect(enabledEnv.INTAKE_ENABLED).toBe("false");
  });

  it("authenticates amendment requests and enforces same-origin cookie mutations", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const token = (await ownerAuthorization()).slice("Bearer ".length);
    const request = (headers: HeadersInit) => new Request(
      `https://submit.test/api/v1/results/${identifier}/retractions`,
      {
        method: "POST",
        headers: {
          ...Object.fromEntries(new Headers(headers)),
          "content-type": "application/json",
          "idempotency-key": "019debd0-1968-7000-8000-000000000012",
        },
        body: JSON.stringify({ reason_code: "owner_requested_withdrawal" }),
      },
    );
    const env = { ...enabledEnv, RESULT_AMENDMENT_OWNER_API_ENABLED: "true" };
    const unauthenticated = await handleRequest(request({}), env, LIFECYCLE, { state });
    expect(unauthenticated.status).toBe(401);
    const crossSiteCookie = await handleRequest(
      request({ cookie: `lean_eval_session=${token}`, origin: "https://attacker.test" }),
      env,
      LIFECYCLE,
      { now: () => NOW_MS, state },
    );
    expect(crossSiteCookie.status).toBe(401);
    expect(state.retractionRequests).toHaveLength(0);
  });

  it("records a redacted owner problem-repair request while intake stays disabled", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const request = (authorization?: string) => new Request(
      `https://submit.test/api/v1/results/${identifier}/problem-repairs`,
      {
        method: "POST",
        headers: {
          ...(authorization === undefined ? {} : { authorization }),
          "content-type": "application/json",
          "idempotency-key": "019debd0-1968-7000-8000-000000000013",
        },
        body: JSON.stringify({
          corrected_problem_id: "corrected_problem",
          corrected_statement_revision: 2,
          reason_code: "wrong_problem_revision",
        }),
      },
    );
    const env = { ...enabledEnv, RESULT_AMENDMENT_OWNER_API_ENABLED: "true" };
    const unauthenticated = await handleRequest(request(), env, LIFECYCLE, { state });
    expect(unauthenticated.status).toBe(401);
    const response = await handleRequest(
      request(await ownerAuthorization()),
      env,
      LIFECYCLE,
      { now: () => NOW_MS, state },
    );
    expect(response.status).toBe(201);
    const body = await response.json();
    expect(body).toEqual({
      result_id: identifier,
      repair_revision: 1,
      status: "problem_repair_requested",
    });
    expect(JSON.stringify(body)).not.toContain("wrong_problem_revision");
    expect(JSON.stringify(body)).not.toContain("alice");
    expect(state.problemRepairRequests).toEqual([{
      eventId: "019debd0-1968-7000-8000-000000000013",
      occurredAt: new Date(NOW_MS).toISOString(),
      resultId: identifier,
      ownerLogin: "alice",
      correctedProblemId: "corrected_problem",
      correctedStatementRevision: 2,
      reasonCode: "wrong_problem_revision",
    }]);
    expect(state.retractionRequests).toHaveLength(0);
    expect(enabledEnv.INTAKE_ENABLED).toBe("false");
  });

  it("does not treat the lifecycle machine token as maintainer authority", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    for (const suffix of [
      "problem-repairs/decisions",
      "retractions/decisions",
      "retractions/override",
      "retractions/finalize",
    ]) {
      const response = await handleRequest(new Request(
        `https://submit.test/internal/v1/results/${identifier}/${suffix}`,
        {
          method: "POST",
          headers: {
            authorization: "Bearer machine-callback-token",
            "content-type": "application/json",
          },
          body: JSON.stringify({ reviewer_login: "forged-maintainer" }),
        },
      ), {
        ...enabledEnv,
        LIFECYCLE_CALLBACK_TOKEN: "machine-callback-token",
        RESULT_AMENDMENT_OWNER_API_ENABLED: "true",
      }, LIFECYCLE, { now: () => NOW_MS, state });
      expect(response.status).toBe(404);
      expect(await response.json()).toEqual({ error: "not_found" });
    }
    expect(state.retractionRequests).toHaveLength(0);
  });

  it("keeps maintainer routes dark unless the gate, State pin, and closed allowlist are valid", async () => {
    const identifier = `r2_${"1".repeat(64)}`;
    const request = () => new Request(
      `https://submit.test/api/v1/results/${identifier}/retractions/decisions`,
      { method: "POST" },
    );
    for (const env of [
      enabledEnv,
      {
        ...enabledEnv,
        RESULT_AMENDMENT_MAINTAINER_API_ENABLED: "true",
        RESULT_AMENDMENT_MAINTAINERS: "[]",
        RESULT_OWNER_STATE_CONTRACT_COMMIT: "b".repeat(40),
      },
      {
        ...enabledEnv,
        RESULT_AMENDMENT_MAINTAINER_API_ENABLED: "true",
        RESULT_AMENDMENT_MAINTAINERS: "not-json",
      },
    ]) {
      const response = await handleRequest(request(), env, LIFECYCLE);
      expect(response.status).toBe(404);
      expect(await response.json()).toEqual({ error: "not_found" });
    }
  });

  it("verifies and records a redacted maintainer problem-repair application", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const authorityEventId = "0198abcd-3333-7000-8000-000000000010";
    const requestEventId = "0198abcd-3333-7000-8000-000000000011";
    state.maintainerAmendment = requestedProblemRepairView(
      initialResultAmendmentView({
        resultId: identifier,
        ownerLogin: "owner",
        declaredModel: "Example Model",
        authorityEventId,
        mutationEventId: authorityEventId,
        problemId: "two_plus_two",
        statementRevision: 1,
      }),
      requestEventId,
      "2026-08-20T06:07:09.000Z",
      "two_plus_three",
      2,
      "wrong_problem_revision",
    );
    const evidence = comparatorEvidence(identifier, "owner");
    const provider = new GitHubProvider();
    const verify = vi.spyOn(provider, "verifyProblemRepairComparator").mockResolvedValue(evidence);
    const env = {
      ...enabledEnv,
      RESULT_AMENDMENT_MAINTAINER_API_ENABLED: "true",
      RESULT_AMENDMENT_MAINTAINERS: JSON.stringify([{ github_id: 42, login: "alice" }]),
    };
    const response = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/problem-repairs/decisions`,
      {
        method: "POST",
        headers: {
          authorization: await ownerAuthorization(),
          "content-type": "application/json",
          "idempotency-key": "0198abcd-3333-7000-8000-000000000018",
        },
        body: JSON.stringify({ decision: "apply", results_commit: "a".repeat(40) }),
      },
    ), env, LIFECYCLE, { now: () => NOW_MS, provider, state });
    expect(response.status).toBe(201);
    const body = await response.json();
    expect(body).toEqual({
      result_id: identifier,
      repair_revision: 1,
      status: "problem_repair_applied",
    });
    expect(JSON.stringify(body)).not.toContain("alice");
    expect(JSON.stringify(body)).not.toContain("results/");
    expect(verify).toHaveBeenCalledWith({
      resultsCommit: "a".repeat(40),
      resultId: identifier,
      ownerLogin: "owner",
      declaredModel: "Example Model",
      baseProblemId: "two_plus_two",
      baseStatementRevision: 1,
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 2,
    });
    expect(state.problemRepairDecisions).toEqual([{
      eventId: "0198abcd-3333-7000-8000-000000000018",
      occurredAt: new Date(NOW_MS).toISOString(),
      resultId: identifier,
      reviewerLogin: "alice",
      decision: "apply",
      reasonCode: null,
      comparatorEvidence: evidence,
    }]);
  });

  it("records a redacted problem-repair rejection without invoking the comparator", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const provider = new GitHubProvider();
    const verify = vi.spyOn(provider, "verifyProblemRepairComparator");
    const env = {
      ...enabledEnv,
      RESULT_AMENDMENT_MAINTAINER_API_ENABLED: "true",
      RESULT_AMENDMENT_MAINTAINERS: JSON.stringify([{ github_id: 42, login: "alice" }]),
    };
    const response = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/problem-repairs/decisions`,
      {
        method: "POST",
        headers: {
          authorization: await ownerAuthorization(),
          "content-type": "application/json",
          "idempotency-key": "0198abcd-3333-7000-8000-000000000019",
        },
        body: JSON.stringify({
          decision: "reject",
          reason_code: "insufficient_comparator_evidence",
        }),
      },
    ), env, LIFECYCLE, { now: () => NOW_MS, provider, state });
    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({
      result_id: identifier,
      repair_revision: 1,
      status: "problem_repair_rejected",
    });
    expect(verify).not.toHaveBeenCalled();
    expect(state.problemRepairDecisions).toEqual([{
      eventId: "0198abcd-3333-7000-8000-000000000019",
      occurredAt: new Date(NOW_MS).toISOString(),
      resultId: identifier,
      reviewerLogin: "alice",
      decision: "reject",
      reasonCode: "insufficient_comparator_evidence",
      comparatorEvidence: null,
    }]);
  });

  it("authenticates the exact maintainer pair and returns a redacted retraction decision", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const env = {
      ...enabledEnv,
      RESULT_AMENDMENT_MAINTAINER_API_ENABLED: "true",
      RESULT_AMENDMENT_MAINTAINERS: JSON.stringify([{ github_id: 42, login: "alice" }]),
    };
    const request = (authorization: string) => new Request(
      `https://submit.test/api/v1/results/${identifier}/retractions/decisions`,
      {
        method: "POST",
        headers: {
          authorization,
          "content-type": "application/json",
          "idempotency-key": "0198abcd-3333-7000-8000-000000000015",
        },
        body: JSON.stringify({ decision: "approve", reason_code: "owner_request_verified" }),
      },
    );
    const unauthorized = await handleRequest(
      request(await ownerAuthorization("mallory")),
      env,
      LIFECYCLE,
      { now: () => NOW_MS, state },
    );
    expect(unauthorized.status).toBe(404);
    expect(await unauthorized.json()).toEqual({ error: "not_found" });
    expect(state.retractionDecisions).toHaveLength(0);

    const response = await handleRequest(
      request(await ownerAuthorization()),
      env,
      LIFECYCLE,
      { now: () => NOW_MS, state },
    );
    expect(response.status).toBe(201);
    const body = await response.json();
    expect(body).toEqual({
      result_id: identifier,
      retraction_revision: 1,
      status: "retraction_approved",
    });
    expect(JSON.stringify(body)).not.toContain("owner_request_verified");
    expect(JSON.stringify(body)).not.toContain("alice");
    expect(state.retractionDecisions).toEqual([{
      eventId: "0198abcd-3333-7000-8000-000000000015",
      occurredAt: new Date(NOW_MS).toISOString(),
      resultId: identifier,
      reviewerLogin: "alice",
      decision: "approve",
      reasonCode: "owner_request_verified",
    }]);
  });

  it("uses closed redacted maintainer requests for override and terminal retraction", async () => {
    const state = new MemoryState();
    const identifier = `r2_${"1".repeat(64)}`;
    const env = {
      ...enabledEnv,
      RESULT_AMENDMENT_MAINTAINER_API_ENABLED: "true",
      RESULT_AMENDMENT_MAINTAINERS: JSON.stringify([{ github_id: 42, login: "alice" }]),
    };
    const authorization = await ownerAuthorization();
    const override = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/retractions/override`,
      {
        method: "POST",
        headers: {
          authorization,
          "content-type": "application/json",
          "idempotency-key": "0198abcd-3333-7000-8000-000000000016",
        },
        body: JSON.stringify({ reason_code: "owner_account_unavailable" }),
      },
    ), env, LIFECYCLE, { now: () => NOW_MS, state });
    expect(override.status).toBe(201);
    expect(await override.json()).toEqual({
      result_id: identifier,
      retraction_revision: 1,
      status: "retraction_overridden",
    });
    const terminal = await handleRequest(new Request(
      `https://submit.test/api/v1/results/${identifier}/retractions/finalize`,
      {
        method: "POST",
        headers: {
          authorization,
          "content-type": "application/json",
          "idempotency-key": "0198abcd-3333-7000-8000-000000000017",
        },
        body: JSON.stringify({}),
      },
    ), env, LIFECYCLE, { now: () => NOW_MS, state });
    expect(terminal.status).toBe(201);
    expect(await terminal.json()).toEqual({
      result_id: identifier,
      release_disposition: "not_published",
      status: "retracted",
    });
    expect(state.retractionOverrides).toHaveLength(1);
    expect(state.retractionFinalizations).toHaveLength(1);
    expect(state.retractionFinalizations[0]?.maintainerLogin).toBe("alice");
  });

  it("conceals wrong-owner retraction authority and preserves conflicts", async () => {
    const identifier = `r2_${"1".repeat(64)}`;
    const request = () => new Request(
      `https://submit.test/api/v1/results/${identifier}/retractions`,
      {
        method: "POST",
        headers: {
          authorization: "placeholder",
          "content-type": "application/json",
          "idempotency-key": "019debd0-1968-7000-8000-000000000014",
        },
        body: JSON.stringify({ reason_code: "owner_requested_withdrawal" }),
      },
    );
    const env = { ...enabledEnv, RESULT_AMENDMENT_OWNER_API_ENABLED: "true" };
    const hiddenState = new MemoryState();
    vi.spyOn(hiddenState, "requestResultRetraction").mockRejectedValue(
      new ResultOwnerStateError(404, "not found"),
    );
    const hiddenRequest = request();
    hiddenRequest.headers.set("authorization", await ownerAuthorization("mallory"));
    const hidden = await handleRequest(hiddenRequest, env, LIFECYCLE, {
      now: () => NOW_MS,
      state: hiddenState,
    });
    expect(hidden.status).toBe(404);
    expect(await hidden.json()).toEqual({ error: "not_found" });

    const conflictState = new MemoryState();
    vi.spyOn(conflictState, "requestResultRetraction").mockRejectedValue(
      new StateEventConflictError("event"),
    );
    const conflictRequest = request();
    conflictRequest.headers.set("authorization", await ownerAuthorization());
    const conflict = await handleRequest(conflictRequest, env, LIFECYCLE, {
      now: () => NOW_MS,
      state: conflictState,
    });
    expect(conflict.status).toBe(409);
    expect(await conflict.json()).toEqual({ error: "idempotency_conflict" });
  });
});
