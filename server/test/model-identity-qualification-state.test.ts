import { env } from "cloudflare:workers";
import { describe, expect, it, vi } from "vitest";

import type { GitHubFetch } from "../src/github-state";
import {
  makeAgentChallenge,
  makeAgentSession,
  signToken,
  type BrowserSession,
} from "../src/auth";
import {
  handleModelIdentityQualificationRequest,
  type ModelIdentityQualificationEnv,
} from "../src/model-identity-qualification-app";
import {
  qualificationStateMutation,
  qualificationStateSnapshot,
  QualificationStateError,
  restoreQualificationState,
  type QualificationRestorationRequest,
  type QualificationStateConfig,
} from "../src/model-identity-qualification-state";
import { modelIdentityId } from "../src/model-identity";

const INITIAL_HEAD = "a".repeat(40);
const MUTATED_TREE = "b".repeat(40);
const RESTORATION_HEAD = "c".repeat(40);
const INITIAL_TREE = "d".repeat(40);
const FOREIGN_HEAD = "f".repeat(40);
const QUALIFICATION_MUTATION_HEAD = "9".repeat(40);
const QUALIFICATION_MUTATION_TREE = "8".repeat(40);
const JOURNAL_ID = `mqj_${"1".repeat(64)}`;
const RECOVERY_NONCE = "2".repeat(64);

const CONFIG: QualificationStateConfig = {
  repository: "leanprover/lean-eval-state-staging",
  token: "test-only-qualification-state-token-value",
  userAgent: "lean-eval-model-identity-qualification/2",
};

const QUALIFICATION_TOKEN = "test-only-model-identity-qualification-token-value";

function qualificationEnv(): ModelIdentityQualificationEnv {
  const namespace = env.MODEL_IDENTITY_QUALIFICATION_JOURNAL;
  if (namespace === undefined) throw new Error("qualification journal binding is unavailable");
  return {
    AUTH_TOKEN_SECRET: "test-only-auth-token-secret-value-long-enough",
    DEPLOYED_COMMIT: "e".repeat(40),
    DEPLOYMENT_ENVIRONMENT: "staging",
    INTAKE_ENABLED: "false",
    INTAKE_ENABLEMENT_MODE: "disabled",
    MODEL_IDENTITY_OWNER_API_ENABLED: "false",
    MODEL_IDENTITY_MAINTAINER_API_ENABLED: "false",
    MODEL_IDENTITY_MAINTAINERS: "[]",
    MODEL_IDENTITY_STATE_CONTRACT_COMMIT:
      "9fc7c431a92c678554c65ebac68d3fddf4990d29",
    PROMOTION_CANARY_ENABLED: "true",
    STATE_REPOSITORY: "leanprover/lean-eval-state-staging",
    GITHUB_STATE_TOKEN: CONFIG.token,
    MODEL_IDENTITY_QUALIFICATION_TOKEN: QUALIFICATION_TOKEN,
    MODEL_IDENTITY_QUALIFICATION_JOURNAL: namespace,
  };
}

function qualificationRequest(
  body: unknown,
  token = QUALIFICATION_TOKEN,
  additionalHeaders: HeadersInit = {},
): Request {
  return new Request("https://submit.test/internal/v1/model-identity-qualification", {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      ...Object.fromEntries(new Headers(additionalHeaders).entries()),
    },
    body: JSON.stringify(body),
  });
}

const REQUEST: QualificationRestorationRequest = {
  journalId: JOURNAL_ID,
  recoveryNonce: RECOVERY_NONCE,
  expectedHead: INITIAL_HEAD,
  expectedTree: MUTATED_TREE,
  initialTree: INITIAL_TREE,
};

function restorationMessage(): string {
  return `Restore model identity qualification ${JOURNAL_ID} nonce ${RECOVERY_NONCE}`;
}

function requestUrl(input: RequestInfo | URL): URL {
  if (input instanceof Request) return new URL(input.url);
  return new URL(String(input));
}

function fakeState(initialHead = INITIAL_HEAD, initialTree = MUTATED_TREE) {
  let head = initialHead;
  const commits = new Map<string, { message: string; tree: string; parents: string[] }>([
    [initialHead, { message: "qualification mutation", tree: initialTree, parents: ["0".repeat(40)] }],
  ]);
  const writes: { method: string; path: string; body: unknown }[] = [];
  const fetcher = vi.fn<GitHubFetch>((input, init) => {
    const url = requestUrl(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.pathname.replace("/repos/leanprover/lean-eval-state-staging", "");
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as unknown : null;
    if (method !== "GET") writes.push({ method, path, body });
    if (method === "GET" && path === "/git/ref/heads/main") {
      return Promise.resolve(Response.json({ ref: "refs/heads/main", object: { sha: head } }));
    }
    if (method === "GET" && path === "/branches/main") {
      return Promise.resolve(Response.json({ name: "main", protected: true, commit: { sha: head } }));
    }
    if (method === "GET" && path.startsWith("/git/commits/")) {
      const commitSha = path.slice("/git/commits/".length);
      const commit = commits.get(commitSha);
      if (commit === undefined) return Promise.resolve(Response.json({ message: "missing" }, { status: 404 }));
      return Promise.resolve(Response.json({
        sha: commitSha,
        message: commit.message,
        tree: { sha: commit.tree },
        parents: commit.parents.map((sha) => ({ sha })),
      }));
    }
    if (method === "POST" && path === "/git/commits") {
      const value = body as { message: string; tree: string; parents: string[] };
      commits.set(RESTORATION_HEAD, value);
      return Promise.resolve(Response.json({ sha: RESTORATION_HEAD }, { status: 201 }));
    }
    if (method === "PATCH" && path === "/git/refs/heads/main") {
      const value = body as { sha: string; force: boolean };
      if (value.force || value.sha !== RESTORATION_HEAD) {
        return Promise.resolve(Response.json({ message: "invalid" }, { status: 422 }));
      }
      head = value.sha;
      return Promise.resolve(Response.json({ ref: "refs/heads/main", object: { sha: head } }));
    }
    return Promise.resolve(Response.json({ message: "unexpected" }, { status: 500 }));
  });
  return { fetcher, writes, commits, setHead(value: string): void { head = value; } };
}

describe("model identity qualification State restoration", () => {
  it("captures only the exact protected staging head and complete tree", async () => {
    const state = fakeState();
    await expect(qualificationStateSnapshot(CONFIG, state.fetcher)).resolves.toEqual({
      head_commit: INITIAL_HEAD,
      head_tree: MUTATED_TREE,
    });
    for (const [, init] of state.fetcher.mock.calls) {
      expect(new Headers(init?.headers).get("authorization"))
        .toBe(`Bearer ${CONFIG.token}`);
      expect(init?.redirect).toBe("manual");
    }
  });

  it("binds one qualification mutation to its exact parent, tree, and message", async () => {
    const state = fakeState(QUALIFICATION_MUTATION_HEAD, QUALIFICATION_MUTATION_TREE);
    state.commits.set(QUALIFICATION_MUTATION_HEAD, {
      message: "Request model identity mi1_test",
      tree: QUALIFICATION_MUTATION_TREE,
      parents: [INITIAL_HEAD],
    });
    await expect(qualificationStateMutation(CONFIG, state.fetcher, {
      expectedParent: INITIAL_HEAD,
      expectedMessage: "Request model identity mi1_test",
    })).resolves.toEqual({
      state_commit: QUALIFICATION_MUTATION_HEAD,
      state_tree: QUALIFICATION_MUTATION_TREE,
      parent_commit: INITIAL_HEAD,
    });
    state.commits.set(QUALIFICATION_MUTATION_HEAD, {
      message: "foreign commit",
      tree: QUALIFICATION_MUTATION_TREE,
      parents: [INITIAL_HEAD],
    });
    await expect(qualificationStateMutation(CONFIG, state.fetcher, {
      expectedParent: INITIAL_HEAD,
      expectedMessage: "Request model identity mi1_test",
    })).rejects.toEqual(new QualificationStateError("foreign_state_movement"));
  });

  it("creates one non-force audit commit and verifies the restored ref and tree", async () => {
    const state = fakeState();
    await expect(restoreQualificationState(CONFIG, state.fetcher, REQUEST)).resolves.toEqual({
      restoration_commit: RESTORATION_HEAD,
      restoration_parent_commit: INITIAL_HEAD,
      restoration_parent_tree: MUTATED_TREE,
      restoration_tree: INITIAL_TREE,
      ref_head: RESTORATION_HEAD,
      fast_forward: true,
      tree_equal: true,
    });
    expect(state.writes).toEqual([{
      method: "POST",
      path: "/git/commits",
      body: {
        message: restorationMessage(),
        tree: INITIAL_TREE,
        parents: [INITIAL_HEAD],
      },
    }, {
      method: "PATCH",
      path: "/git/refs/heads/main",
      body: { sha: RESTORATION_HEAD, force: false },
    }]);
  });

  it("recognizes only its exact audit commit after a lost ref-update response", async () => {
    const state = fakeState(RESTORATION_HEAD, INITIAL_TREE);
    state.commits.set(RESTORATION_HEAD, {
      message: restorationMessage(),
      tree: INITIAL_TREE,
      parents: [INITIAL_HEAD],
    });
    await expect(restoreQualificationState(CONFIG, state.fetcher, REQUEST))
      .resolves.toMatchObject({
        restoration_commit: RESTORATION_HEAD,
        ref_head: RESTORATION_HEAD,
        fast_forward: true,
        tree_equal: true,
      });
    expect(state.writes).toEqual([]);
  });

  it("rejects foreign movement even when the foreign tree equals the initial tree", async () => {
    const state = fakeState(FOREIGN_HEAD, INITIAL_TREE);
    await expect(restoreQualificationState(CONFIG, state.fetcher, REQUEST))
      .rejects.toEqual(new QualificationStateError("foreign_state_movement"));
    expect(state.writes).toEqual([]);
  });

  it("never includes provider response content in a failure", async () => {
    const fetcher = vi.fn<GitHubFetch>(() => Promise.resolve(new Response(
      "PRIVATE_FILENAME_SENTINEL PRIVATE_CONTENT_SENTINEL",
      { status: 500 },
    )));
    let failure: unknown;
    try {
      await qualificationStateSnapshot(CONFIG, fetcher);
    } catch (error) {
      failure = error;
    }
    expect(failure).toEqual(new QualificationStateError("provider_unavailable"));
    expect(JSON.stringify(failure)).not.toContain("PRIVATE_");
  });
});

describe("closed model identity qualification HTTP boundary", () => {
  it("is unreachable in production and without the dedicated credential", async () => {
    const state = fakeState();
    const body = { schema_version: 2, run_id: "4001", run_attempt: 1 };
    const production = await handleModelIdentityQualificationRequest(
      qualificationRequest(body),
      { ...qualificationEnv(), DEPLOYMENT_ENVIRONMENT: "production" },
      { stateFetch: state.fetcher },
    );
    const wrongToken = await handleModelIdentityQualificationRequest(
      qualificationRequest(body, "wrong-token-value-that-is-deliberately-long"),
      qualificationEnv(),
      { stateFetch: state.fetcher },
    );
    expect([production.status, wrongToken.status]).toEqual([404, 404]);
    expect(state.fetcher).not.toHaveBeenCalled();
  });

  it("acquires, reports, restores, and idempotently re-reports one exact journal", async () => {
    const state = fakeState();
    const runtime = qualificationEnv();
    const acquireBody = {
      schema_version: 2,
      operation: "acquire",
      confirmation: "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING",
      deployed_commit: runtime.DEPLOYED_COMMIT,
      initial_state_commit: INITIAL_HEAD,
      initial_state_tree: MUTATED_TREE,
      run_id: "4002",
      run_attempt: 1,
      intent: {
        owner: { github_id: 1, login: "owner" },
        cross_owner: { github_id: 2, login: "cross-owner" },
        maintainer: { github_id: 3, login: "maintainer" },
      },
    };
    const acquiredResponse = await handleModelIdentityQualificationRequest(
      qualificationRequest(acquireBody), runtime, { stateFetch: state.fetcher },
    );
    expect(acquiredResponse.status).toBe(200);
    const acquired = await acquiredResponse.json<{
      journal_id: string;
      journal_revision: number;
      current_state_commit: string;
      current_state_tree: string;
    }>();
    expect(acquired).toMatchObject({
      journal_revision: 1,
      current_state_commit: INITIAL_HEAD,
      current_state_tree: MUTATED_TREE,
    });

    const statusResponse = await handleModelIdentityQualificationRequest(
      qualificationRequest({ schema_version: 2, run_id: "4002", run_attempt: 1 }),
      runtime,
    );
    expect(statusResponse.status).toBe(200);
    expect(await statusResponse.json()).toMatchObject({
      journal_id: acquired.journal_id,
      lease_status: "active",
    });

    const restoreBody = {
      schema_version: 2,
      operation: "restore",
      confirmation: "RESTORE_MODEL_IDENTITY_STAGING_JOURNAL",
      deployed_commit: runtime.DEPLOYED_COMMIT,
      run_id: "4002",
      run_attempt: 1,
      journal_id: acquired.journal_id,
      expected_journal_revision: 1,
      expected_state_commit: INITIAL_HEAD,
      expected_state_tree: MUTATED_TREE,
    };
    const restoredResponse = await handleModelIdentityQualificationRequest(
      qualificationRequest(restoreBody), runtime, { stateFetch: state.fetcher },
    );
    expect(restoredResponse.status).toBe(200);
    const restored = await restoredResponse.json();
    expect(restored).toMatchObject({
      status: "model_identity_qualification_restored",
      journal_revision: 2,
      restoration_parent_commit: INITIAL_HEAD,
      restoration_parent_tree: MUTATED_TREE,
      restoration_commit: RESTORATION_HEAD,
      restoration_tree: MUTATED_TREE,
      ref_head: RESTORATION_HEAD,
      fast_forward: true,
      tree_equal: true,
      lease_released: true,
    });
    const writesAfterRestore = state.writes.length;
    const retry = await handleModelIdentityQualificationRequest(
      qualificationRequest(restoreBody), runtime, { stateFetch: state.fetcher },
    );
    expect(retry.status).toBe(200);
    expect(await retry.json()).toEqual(restored);
    expect(state.writes).toHaveLength(writesAfterRestore);
  });

  it("refuses an acquisition that does not match the protected live head", async () => {
    const state = fakeState();
    const runtime = qualificationEnv();
    const response = await handleModelIdentityQualificationRequest(
      qualificationRequest({
        schema_version: 2,
        operation: "acquire",
        confirmation: "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING",
        deployed_commit: runtime.DEPLOYED_COMMIT,
        initial_state_commit: FOREIGN_HEAD,
        initial_state_tree: MUTATED_TREE,
        run_id: "4003",
        run_attempt: 1,
        intent: {
          owner: { github_id: 1, login: "owner" },
          cross_owner: { github_id: 2, login: "cross-owner" },
          maintainer: { github_id: 3, login: "maintainer" },
        },
      }),
      runtime,
      { stateFetch: state.fetcher },
    );
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: "foreign_state_movement" });
    expect(state.writes).toEqual([]);
  });

  it("proves exact OAuth and source-bound agent identities without mutating State", async () => {
    const state = fakeState();
    const runtime = qualificationEnv();
    const intent = {
      owner: { github_id: 1, login: "owner" },
      cross_owner: { github_id: 2, login: "cross-owner" },
      maintainer: { github_id: 3, login: "maintainer" },
    };
    const acquiredResponse = await handleModelIdentityQualificationRequest(
      qualificationRequest({
        schema_version: 2,
        operation: "acquire",
        confirmation: "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING",
        deployed_commit: runtime.DEPLOYED_COMMIT,
        initial_state_commit: INITIAL_HEAD,
        initial_state_tree: MUTATED_TREE,
        run_id: "4005",
        run_attempt: 1,
        intent,
      }),
      runtime,
      { stateFetch: state.fetcher },
    );
    const acquired = await acquiredResponse.json<{
      journal_id: string;
      journal_revision: number;
    }>();
    const now = Math.floor(Date.now() / 1000);
    const browserSession: BrowserSession = {
      kind: "browser_session",
      github_id: intent.owner.github_id,
      login: intent.owner.login,
      issued_at: now - 1,
      expires_at: now + 600,
    };
    const browserToken = await signToken(
      runtime.AUTH_TOKEN_SECRET ?? "",
      browserSession,
    );
    const challenge = makeAgentChallenge({
      login: intent.owner.login,
      source_repository: "owner/private-proof",
      source_commit: "7".repeat(40),
      gist_id: "qualification-secret-gist",
    }, now - 1);
    const agentToken = await signToken(
      runtime.AUTH_TOKEN_SECRET ?? "",
      makeAgentSession(
        { id: intent.owner.github_id, login: intent.owner.login },
        challenge,
        now,
      ),
    );
    const step = (
      operation: "oauth_session_identity" | "agent_session_identity",
      revision: number,
    ) => ({
      schema_version: 2,
      operation,
      confirmation: "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING",
      deployed_commit: runtime.DEPLOYED_COMMIT,
      run_id: "4005",
      run_attempt: 1,
      journal_id: acquired.journal_id,
      expected_journal_revision: revision,
      expected_state_commit: INITIAL_HEAD,
      expected_state_tree: MUTATED_TREE,
      intent,
      request: {},
    });

    const oauth = await handleModelIdentityQualificationRequest(
      qualificationRequest(step("oauth_session_identity", 1), QUALIFICATION_TOKEN, {
        "x-lean-eval-oauth-session": browserToken,
      }),
      runtime,
    );
    expect(oauth.status).toBe(200);
    expect(await oauth.json()).toMatchObject({
      journal_revision: 2,
      mutation_created: false,
      state_commit: INITIAL_HEAD,
      state_tree: MUTATED_TREE,
      proof: {
        operation: "oauth_session_identity",
        route: "session/oauth-owner",
        credential_roles: ["oauth_owner"],
        actor: intent.owner,
        assertions: {
          browser_session_signature_verified: true,
          exact_identity_verified: true,
          session_unexpired: true,
        },
      },
    });

    const agent = await handleModelIdentityQualificationRequest(
      qualificationRequest(step("agent_session_identity", 2), QUALIFICATION_TOKEN, {
        "x-lean-eval-agent-session": agentToken,
      }),
      runtime,
    );
    expect(agent.status).toBe(200);
    expect(await agent.json()).toMatchObject({
      journal_revision: 3,
      mutation_created: false,
      state_commit: INITIAL_HEAD,
      state_tree: MUTATED_TREE,
      proof: {
        operation: "agent_session_identity",
        route: "session/agent-owner",
        credential_roles: ["agent_owner"],
        actor: intent.owner,
        assertions: {
          agent_source_commit_bound: true,
          browser_session_signature_verified: true,
          exact_identity_verified: true,
        },
      },
    });
    expect(state.writes).toEqual([]);

    const wrongRole = await handleModelIdentityQualificationRequest(
      qualificationRequest(step("agent_session_identity", 3), QUALIFICATION_TOKEN, {
        "x-lean-eval-oauth-session": browserToken,
      }),
      runtime,
    );
    expect(wrongRole.status).toBe(401);
    expect(await wrongRole.json()).toEqual({ error: "authentication_failed" });

    const foreignToken = await signToken(runtime.AUTH_TOKEN_SECRET ?? "", {
      ...browserSession,
      github_id: intent.cross_owner.github_id,
      login: intent.cross_owner.login,
    });
    const wrongIdentity = await handleModelIdentityQualificationRequest(
      qualificationRequest(step("oauth_session_identity", 3), QUALIFICATION_TOKEN, {
        "x-lean-eval-oauth-session": foreignToken,
      }),
      runtime,
    );
    expect(wrongIdentity.status).toBe(401);
    expect(await wrongIdentity.json()).toEqual({ error: "authentication_failed" });

    const restored = await handleModelIdentityQualificationRequest(
      qualificationRequest({
        schema_version: 2,
        operation: "restore",
        confirmation: "RESTORE_MODEL_IDENTITY_STAGING_JOURNAL",
        deployed_commit: runtime.DEPLOYED_COMMIT,
        run_id: "4005",
        run_attempt: 1,
        journal_id: acquired.journal_id,
        expected_journal_revision: 3,
        expected_state_commit: INITIAL_HEAD,
        expected_state_tree: MUTATED_TREE,
      }),
      runtime,
      { stateFetch: state.fetcher },
    );
    expect(restored.status).toBe(200);
    expect(await restored.json()).toMatchObject({
      status: "model_identity_qualification_restored",
      journal_revision: 4,
      lease_released: true,
    });
  });

  it("recovers an owner request after the public API response is lost", async () => {
    const state = fakeState();
    const runtime = qualificationEnv();
    const intent = {
      owner: { github_id: 1, login: "owner" },
      cross_owner: { github_id: 2, login: "cross-owner" },
      maintainer: { github_id: 3, login: "maintainer" },
    };
    const acquiredResponse = await handleModelIdentityQualificationRequest(
      qualificationRequest({
        schema_version: 2,
        operation: "acquire",
        confirmation: "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING",
        deployed_commit: runtime.DEPLOYED_COMMIT,
        initial_state_commit: INITIAL_HEAD,
        initial_state_tree: MUTATED_TREE,
        run_id: "4004",
        run_attempt: 1,
        intent,
      }),
      runtime,
      { stateFetch: state.fetcher },
    );
    expect(acquiredResponse.status).toBe(200);
    const acquired = await acquiredResponse.json<{
      journal_id: string;
      journal_revision: number;
    }>();
    const eventId = "0198abcd-0000-7000-8000-000000000001";
    const expectedModelId = await modelIdentityId(eventId);
    const now = Math.floor(Date.now() / 1000);
    const browserSession: BrowserSession = {
      kind: "browser_session",
      github_id: intent.owner.github_id,
      login: intent.owner.login,
      issued_at: now - 1,
      expires_at: now + 600,
    };
    const sessionToken = await signToken(
      runtime.AUTH_TOKEN_SECRET ?? "",
      browserSession,
    );
    const stepBody = {
      schema_version: 2,
      operation: "owner_request",
      confirmation: "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING",
      deployed_commit: runtime.DEPLOYED_COMMIT,
      run_id: "4004",
      run_attempt: 1,
      journal_id: acquired.journal_id,
      expected_journal_revision: acquired.journal_revision,
      expected_state_commit: INITIAL_HEAD,
      expected_state_tree: MUTATED_TREE,
      intent,
      request: {
        event_id: eventId,
        display_name: "Qualification Owner Model",
      },
    };
    let invocation = 0;
    const modelApiRequest = vi.fn(async (
      internalRequest: Request,
      maintainer: { github_id: number; login: string },
    ): Promise<Response> => {
      invocation += 1;
      expect(maintainer).toEqual(intent.maintainer);
      expect(new URL(internalRequest.url).pathname).toBe("/api/v1/model-identities");
      expect(internalRequest.headers.get("authorization")).toBe(`Bearer ${sessionToken}`);
      expect(internalRequest.headers.get("idempotency-key")).toBe(eventId);
      expect(await internalRequest.json()).toEqual({
        display_name: "Qualification Owner Model",
      });
      if (invocation === 1) {
        state.commits.set(QUALIFICATION_MUTATION_HEAD, {
          message: `Request model identity ${expectedModelId}`,
          tree: QUALIFICATION_MUTATION_TREE,
          parents: [INITIAL_HEAD],
        });
        state.setHead(QUALIFICATION_MUTATION_HEAD);
        throw new Error("simulated lost response");
      }
      return Response.json({
        model_id: expectedModelId,
        status: "identity_already_requested",
      });
    });
    const first = await handleModelIdentityQualificationRequest(
      qualificationRequest(stepBody, QUALIFICATION_TOKEN, {
        "x-lean-eval-oauth-session": sessionToken,
      }),
      runtime,
      { stateFetch: state.fetcher, modelApiRequest },
    );
    expect(first.status).toBe(503);
    const recovered = await handleModelIdentityQualificationRequest(
      qualificationRequest(stepBody, QUALIFICATION_TOKEN, {
        "x-lean-eval-oauth-session": sessionToken,
      }),
      runtime,
      { stateFetch: state.fetcher, modelApiRequest },
    );
    expect(recovered.status).toBe(200);
    expect(await recovered.json()).toMatchObject({
      journal_revision: 2,
      mutation_created: true,
      previous_state_commit: INITIAL_HEAD,
      state_commit: QUALIFICATION_MUTATION_HEAD,
      state_tree: QUALIFICATION_MUTATION_TREE,
      proof: {
        operation: "owner_request",
        http_status: 200,
        event_ids: [eventId],
        model_ids: [expectedModelId],
        assertions: { idempotent_response: true },
      },
    });
    expect(modelApiRequest).toHaveBeenCalledTimes(2);

    const restored = await handleModelIdentityQualificationRequest(
      qualificationRequest({
        schema_version: 2,
        operation: "restore",
        confirmation: "RESTORE_MODEL_IDENTITY_STAGING_JOURNAL",
        deployed_commit: runtime.DEPLOYED_COMMIT,
        run_id: "4004",
        run_attempt: 1,
        journal_id: acquired.journal_id,
        expected_journal_revision: 2,
        expected_state_commit: QUALIFICATION_MUTATION_HEAD,
        expected_state_tree: QUALIFICATION_MUTATION_TREE,
      }),
      runtime,
      { stateFetch: state.fetcher },
    );
    expect(restored.status).toBe(200);
  });
});
