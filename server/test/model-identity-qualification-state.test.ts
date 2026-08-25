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
  qualificationStateMutationPrefix,
  qualificationStateMutationSequence,
  qualificationStateSnapshot,
  QualificationStateError,
  restoreQualificationState,
  type QualificationRestorationRequest,
  type QualificationStateConfig,
} from "../src/model-identity-qualification-state";
import { modelIdentityId } from "../src/model-identity";
import { canonicalStateDocument } from "../src/github-state";

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
const SOURCE_TEST_ONLY_FIXTURE_VERIFICATION = () => Promise.resolve();

function qualificationEnv(): ModelIdentityQualificationEnv {
  const namespace = env.MODEL_IDENTITY_QUALIFICATION_JOURNAL;
  if (namespace === undefined) throw new Error("qualification journal binding is unavailable");
  const executor = env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR;
  if (executor === undefined) throw new Error("qualification executor binding is unavailable");
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
    MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET:
      "test-only-qualification-executor-secret-value",
    MODEL_IDENTITY_QUALIFICATION_EXECUTOR: executor,
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

async function gitBlobSha(content: string): Promise<string> {
  const encoded = new TextEncoder().encode(content);
  const prefix = new TextEncoder().encode(`blob ${String(encoded.byteLength)}\0`);
  const bytes = new Uint8Array(prefix.byteLength + encoded.byteLength);
  bytes.set(prefix);
  bytes.set(encoded, prefix.byteLength);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-1", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function fakeState(initialHead = INITIAL_HEAD, initialTree = MUTATED_TREE) {
  let head = initialHead;
  let failingDocumentReads = 0;
  const commits = new Map<string, {
    message: string;
    tree: string;
    parents: string[];
    files?: { filename: string; status: "added" | "modified" | "removed"; sha?: string }[];
  }>([
    [initialHead, { message: "qualification mutation", tree: initialTree, parents: ["0".repeat(40)] }],
  ]);
  const documents = new Map<string, unknown>();
  const writes: { method: string; path: string; body: unknown }[] = [];
  const fetcher = vi.fn<GitHubFetch>(async (input, init) => {
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
    if (method === "GET" && path.startsWith("/contents/")) {
      if (failingDocumentReads > 0) {
        failingDocumentReads -= 1;
        return Response.json({ message: "injected" }, { status: 500 });
      }
      const documentPath = decodeURI(path.slice("/contents/".length));
      const document = documents.get(documentPath);
      if (document === undefined) {
        return Response.json({ message: "missing" }, { status: 404 });
      }
      const content = canonicalStateDocument(document);
      return Response.json({
        type: "file",
        path: documentPath,
        encoding: "base64",
        content: btoa(content),
        sha: await gitBlobSha(content),
      });
    }
    if (method === "GET" && path.startsWith("/compare/")) {
      const comparison = path.slice("/compare/".length).split("...");
      const parent = comparison[0];
      const child = comparison[1];
      const commit = child === undefined ? undefined : commits.get(child);
      if (parent === undefined || child === undefined || commit === undefined) {
        return Response.json({ message: "missing" }, { status: 404 });
      }
      const files = commit.files ?? await Promise.all(
        [...documents.entries()].map(async ([filename, value]) => ({
          filename,
          status: "added" as const,
          sha: await gitBlobSha(canonicalStateDocument(value)),
        })),
      );
      return Response.json({
        status: "ahead",
        ahead_by: 1,
        behind_by: 0,
        total_commits: 1,
        merge_base_commit: { sha: parent },
        commits: [{ sha: child }],
        files,
      });
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
  return {
    fetcher,
    writes,
    commits,
    documents,
    failNextDocumentRead(): void { failingDocumentReads += 1; },
    setHead(value: string): void { head = value; },
  };
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
    const expectedDocument = { schema_version: 1, value: "exact" };
    state.documents.set("events/exact.json", expectedDocument);
    await expect(qualificationStateMutation(CONFIG, state.fetcher, {
      expectedParent: INITIAL_HEAD,
      expectedMessage: "Request model identity mi1_test",
      expectedDocuments: { "events/exact.json": expectedDocument },
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
      expectedDocuments: { "events/exact.json": expectedDocument },
    })).rejects.toEqual(new QualificationStateError("foreign_state_movement"));
  });

  it("binds an exact ordered mutation prefix and rejects extra changed paths", async () => {
    const first = "1".repeat(40);
    const second = "2".repeat(40);
    const firstTree = "3".repeat(40);
    const state = fakeState(second, QUALIFICATION_MUTATION_TREE);
    const firstDocument = { schema_version: 1, step: 1 };
    const secondDocument = { schema_version: 1, step: 2 };
    const firstSha = await gitBlobSha(canonicalStateDocument(firstDocument));
    const secondSha = await gitBlobSha(canonicalStateDocument(secondDocument));
    state.documents.set("events/first.json", firstDocument);
    state.documents.set("events/second.json", secondDocument);
    state.commits.set(first, {
      message: "First exact qualification mutation",
      tree: firstTree,
      parents: [INITIAL_HEAD],
      files: [{ filename: "events/first.json", status: "added", sha: firstSha }],
    });
    state.commits.set(second, {
      message: "Second exact qualification mutation",
      tree: QUALIFICATION_MUTATION_TREE,
      parents: [first],
      files: [{ filename: "events/second.json", status: "added", sha: secondSha }],
    });
    const request = {
      expectedParent: INITIAL_HEAD,
      expectedMutations: [{
        expectedMessage: "First exact qualification mutation",
        expectedDocuments: { "events/first.json": firstDocument },
        expectedDeletedPaths: [],
        expectedTreeUnchanged: false,
      }, {
        expectedMessage: "Second exact qualification mutation",
        expectedDocuments: { "events/second.json": secondDocument },
        expectedDeletedPaths: [],
        expectedTreeUnchanged: false,
      }],
    } as const;
    await expect(qualificationStateMutationSequence(CONFIG, state.fetcher, request))
      .resolves.toEqual({
        state_commit: second,
        state_tree: QUALIFICATION_MUTATION_TREE,
        parent_commit: INITIAL_HEAD,
      });
    state.setHead(first);
    await expect(qualificationStateMutationPrefix(CONFIG, state.fetcher, request))
      .resolves.toEqual({
        state_commit: first,
        state_tree: firstTree,
        parent_commit: INITIAL_HEAD,
        applied_mutations: 1,
      });
    state.setHead(second);
    state.commits.get(second)?.files?.push({
      filename: "views/foreign.json",
      status: "added",
      sha: "4".repeat(40),
    });
    await expect(qualificationStateMutationSequence(CONFIG, state.fetcher, request))
      .rejects.toEqual(new QualificationStateError("foreign_state_movement"));
  });

  it("accepts only an exact same-tree zero-diff planned contender commit", async () => {
    const contender = "5".repeat(40);
    const state = fakeState(contender, INITIAL_TREE);
    state.commits.set(INITIAL_HEAD, {
      message: "parent",
      tree: INITIAL_TREE,
      parents: ["0".repeat(40)],
    });
    state.commits.set(contender, {
      message: "planned contender",
      tree: INITIAL_TREE,
      parents: [INITIAL_HEAD],
      files: [],
    });
    const request = {
      expectedParent: INITIAL_HEAD,
      expectedMutations: [{
        expectedMessage: "planned contender",
        expectedDocuments: {},
        expectedDeletedPaths: [],
        expectedTreeUnchanged: true,
      }],
    } as const;
    await expect(qualificationStateMutationSequence(CONFIG, state.fetcher, request))
      .resolves.toEqual({
        state_commit: contender,
        state_tree: INITIAL_TREE,
        parent_commit: INITIAL_HEAD,
      });
    state.commits.get(contender)?.files?.push({
      filename: "events/foreign.json",
      status: "added",
      sha: "6".repeat(40),
    });
    await expect(qualificationStateMutationSequence(CONFIG, state.fetcher, request))
      .rejects.toEqual(new QualificationStateError("foreign_state_movement"));
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

  it("cannot acquire before an exact reviewed live fixture is source-armed", async () => {
    const state = fakeState();
    const runtime = qualificationEnv();
    const response = await handleModelIdentityQualificationRequest(
      qualificationRequest({
        schema_version: 2,
        operation: "acquire",
        confirmation: "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING",
        deployed_commit: runtime.DEPLOYED_COMMIT,
        initial_state_commit: INITIAL_HEAD,
        initial_state_tree: MUTATED_TREE,
        run_id: "4099",
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
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "provider_unavailable" });
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
      qualificationRequest(acquireBody), runtime, {
        stateFetch: state.fetcher,
        sourceTestOnlyFixtureVerification: SOURCE_TEST_ONLY_FIXTURE_VERIFICATION,
      },
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
      {
        stateFetch: state.fetcher,
        sourceTestOnlyFixtureVerification: SOURCE_TEST_ONLY_FIXTURE_VERIFICATION,
      },
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
      {
        stateFetch: state.fetcher,
        sourceTestOnlyFixtureVerification: SOURCE_TEST_ONLY_FIXTURE_VERIFICATION,
      },
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
      {
        stateFetch: state.fetcher,
        sourceTestOnlyFixtureVerification: SOURCE_TEST_ONLY_FIXTURE_VERIFICATION,
      },
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
      {
        stateFetch: state.fetcher,
        sourceTestOnlyFixtureVerification: SOURCE_TEST_ONLY_FIXTURE_VERIFICATION,
      },
    );
    expect(acquiredResponse.status).toBe(200);
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
    const sessionToken = await signToken(
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
    const identityStep = (
      operation: "oauth_session_identity" | "agent_session_identity",
      revision: number,
    ) => ({
      schema_version: 2,
      operation,
      confirmation: "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING",
      deployed_commit: runtime.DEPLOYED_COMMIT,
      run_id: "4004",
      run_attempt: 1,
      journal_id: acquired.journal_id,
      expected_journal_revision: revision,
      expected_state_commit: INITIAL_HEAD,
      expected_state_tree: MUTATED_TREE,
      intent,
    });
    const oauth = await handleModelIdentityQualificationRequest(
      qualificationRequest(identityStep("oauth_session_identity", 1), QUALIFICATION_TOKEN, {
        "x-lean-eval-oauth-session": sessionToken,
      }),
      runtime,
    );
    expect(oauth.status).toBe(200);
    const agent = await handleModelIdentityQualificationRequest(
      qualificationRequest(identityStep("agent_session_identity", 2), QUALIFICATION_TOKEN, {
        "x-lean-eval-agent-session": agentToken,
      }),
      runtime,
    );
    expect(agent.status).toBe(200);
    const stepBody = {
      schema_version: 2,
      operation: "owner_request",
      confirmation: "QUALIFY_AND_RESTORE_MODEL_IDENTITY_STAGING",
      deployed_commit: runtime.DEPLOYED_COMMIT,
      run_id: "4004",
      run_attempt: 1,
      journal_id: acquired.journal_id,
      expected_journal_revision: 3,
      expected_state_commit: INITIAL_HEAD,
      expected_state_tree: MUTATED_TREE,
      intent,
    };
    let invocation = 0;
    let expectedModelId = "";
    let eventId = "";
    const modelApiRequest = vi.fn(async (
      internalRequest: Request,
      maintainer: { github_id: number; login: string },
      occurredAtMilliseconds: number,
    ): Promise<Response> => {
      invocation += 1;
      expect(maintainer).toEqual(intent.maintainer);
      expect(new URL(internalRequest.url).pathname).toBe("/api/v1/model-identities");
      expect(internalRequest.headers.get("authorization")).toBe(`Bearer ${sessionToken}`);
      eventId = internalRequest.headers.get("idempotency-key") ?? "";
      expectedModelId = await modelIdentityId(eventId);
      const body = await internalRequest.json<{ display_name: string }>();
      expect(body.display_name).toBe(`Qualification owner model run 4004`);
      if (invocation === 1) {
        const occurredAt = new Date(occurredAtMilliseconds).toISOString();
        state.documents.set(
          `events/${eventId.replaceAll("-", "").slice(0, 2)}/${eventId}.json`,
          {
            schema_version: 1,
            event_id: eventId,
            event_type: "model_identity.requested",
            occurred_at: occurredAt,
            subject_id: expectedModelId,
            causation_event_id: null,
            actor: { kind: "github", login: intent.owner.login },
            payload: { display_name: body.display_name },
          },
        );
        state.documents.set(
          `views/model-identities/${expectedModelId.slice(4, 6)}/${expectedModelId}.json`,
          {
            schema_version: 1,
            model_id: expectedModelId,
            owner_login: intent.owner.login,
            requested_name: body.display_name,
            display_name: body.display_name,
            status: "pending",
            request_event_id: eventId,
            requested_at: occurredAt,
            decision_event_id: null,
            decided_at: null,
            reviewer_login: null,
            rejection_reason: null,
            mutation_event_id: eventId,
            consolidated_into: null,
            resolved_model_id: null,
          },
        );
        state.commits.set(QUALIFICATION_MUTATION_HEAD, {
          message: `Request model identity ${expectedModelId}`,
          tree: QUALIFICATION_MUTATION_TREE,
          parents: [INITIAL_HEAD],
        });
        state.setHead(QUALIFICATION_MUTATION_HEAD);
        throw new Error("simulated lost response");
      }
      return Response.json({ model_id: expectedModelId, status: "identity_requested" }, {
        status: 201,
      });
    });
    state.failNextDocumentRead();
    const first = await handleModelIdentityQualificationRequest(
      qualificationRequest(stepBody, QUALIFICATION_TOKEN, {
        "x-lean-eval-oauth-session": sessionToken,
      }),
      runtime,
      { stateFetch: state.fetcher, modelApiRequest },
    );
    expect(first.status).toBe(503);
    const recovered = await handleModelIdentityQualificationRequest(
      qualificationRequest({ schema_version: 2, run_id: "4004", run_attempt: 1 }),
      runtime,
      { stateFetch: state.fetcher },
    );
    expect(recovered.status).toBe(200);
    expect(await recovered.json()).toMatchObject({
      journal_revision: 3,
      current_state_commit: QUALIFICATION_MUTATION_HEAD,
      current_state_tree: QUALIFICATION_MUTATION_TREE,
      lease_status: "active",
    });
    expect(modelApiRequest).toHaveBeenCalledTimes(1);

    const restored = await handleModelIdentityQualificationRequest(
      qualificationRequest({
        schema_version: 2,
        operation: "restore",
        confirmation: "RESTORE_MODEL_IDENTITY_STAGING_JOURNAL",
        deployed_commit: runtime.DEPLOYED_COMMIT,
        run_id: "4004",
        run_attempt: 1,
        journal_id: acquired.journal_id,
        expected_journal_revision: 3,
        expected_state_commit: QUALIFICATION_MUTATION_HEAD,
        expected_state_tree: QUALIFICATION_MUTATION_TREE,
      }),
      runtime,
      { stateFetch: state.fetcher },
    );
    expect(restored.status).toBe(200);
  });
});
