import { describe, expect, it, vi } from "vitest";

import {
  ApiDecodeError,
  assertSourcePolicy,
  decodeArchiveCompletion,
  decodeResultCompletion,
  decodeSourceReaderPreflight,
  decodeSubmissionInput,
  readJson,
} from "../src/api-contract";
import {
  lifecycleEventId,
  makeSubmissionGrant,
  signToken,
  verifyToken,
  type BrowserSession,
  type SubmissionGrant,
} from "../src/auth";
import { handleRequest, handleScheduled, type RuntimeEnv, type StateAccess } from "../src/app";
import { buildDispatchRequest, GitHubProvider } from "../src/github-provider";
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

class MemoryState implements StateAccess {
  readonly events: WritableStateEvent[] = [];
  readonly views = new Map<string, SubmissionView>();
  readonly outbox = new Map<string, DispatchOutbox>();
  created = true;

  appendEvent(event: WritableStateEvent): Promise<{ created: boolean }> {
    this.events.push(event);
    return Promise.resolve({ created: this.created });
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

  listDispatchOutbox(shard: string): Promise<readonly DispatchOutbox[]> {
    return Promise.resolve([...this.outbox.values()].filter((entry) => entry.submission_id.replaceAll("-", "").endsWith(shard)));
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
  const metadataEventId = "0198abcd-1111-7000-8000-000000000002";
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
      "0198abcd-1111-7000-8000-000000000001",
      "alice",
      "staging",
      SUBMISSION,
    );
    const body = await request.json<{ ref: string; inputs: Record<string, string> }>();
    expect(body.ref).toBe(`lean-eval-dispatch/${"b".repeat(40)}`);
    expect(body.inputs).toMatchObject({
      submission_id: "0198abcd-1111-7000-8000-000000000001",
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
    const submissionId = "0198abcd-1111-7000-8000-000000000001";
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
      submission_id: "0198abcd-1111-7000-8000-000000000001",
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
    const submissionId = "0198abcd-1111-7000-8000-000000000001";
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

  it("records an authenticated archive failure without leaving status pending", async () => {
    const state = new MemoryState();
    const submissionId = "0198abcd-1111-7000-8000-000000000001";
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
    const submissionId = "0198abcd-1111-7000-8000-000000000001";
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

  it("reads one bounded shard and clears a due outbox after successful dispatch", async () => {
    const state = new MemoryState();
    const scheduledTime = (Math.floor(NOW_MS / (256 * 60_000)) * 256 + 1) * 60_000;
    const submissionId = "0198abcd-1111-7000-8000-000000000001";
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
    const submissionId = "0198abcd-1111-7000-8000-000000000001";
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
        event_id: "0198abcd-1111-7000-8000-000000000002",
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
      mutation_event_id: "0198abcd-1111-7000-8000-000000000002",
      metadata_event_id: "0198abcd-1111-7000-8000-000000000002",
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
          "idempotency-key": "0198abcd-1111-7000-8000-000000000003",
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
      causation_event_id: "0198abcd-1111-7000-8000-000000000002",
    });

    const publication = await handleRequest(
      new Request(`https://submit.test/api/v1/submissions/${submissionId}/publication`, {
        method: "PUT",
        headers: {
          authorization,
          "content-type": "application/json",
          "idempotency-key": "0198abcd-1111-7000-8000-000000000004",
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
      causation_event_id: "0198abcd-1111-7000-8000-000000000003",
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
