import { beforeAll, describe, expect, it, vi } from "vitest";

import { githubBrokerFetch } from "../src/github-broker-client";
import { handleBrokerRequest, type BrokerRuntimeEnv } from "../src/github-broker";

const NOW = 1_800_000_000_000;
const COMMIT = "a".repeat(40);
let privateKey = "";
let rsaPrivateKey = "";

const ENV = {
  DEPLOYED_COMMIT: "test-commit",
  DEPLOYMENT_ENVIRONMENT: "staging",
  DISPATCH_APP_ID: "4666633",
  DISPATCH_APP_PRIVATE_KEY: "set in beforeAll",
  DISPATCH_REPOSITORY: "leanprover/lean-eval-submissions",
  DISPATCH_WORKFLOW: "submission.yml",
  LEGACY_SOURCE_APP_ID: "Iv23liLATwL7VxAK37uX",
  LEGACY_SOURCE_APP_PRIVATE_KEY: "set in beforeAll",
  SOURCE_APP_ID: "4666604",
  SOURCE_APP_PRIVATE_KEY: "set in beforeAll",
} satisfies BrokerRuntimeEnv;

function base64(bytes: ArrayBuffer): string {
  let binary = "";
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/(.{64})/g, "$1\n");
}

function inputUrl(input: RequestInfo | URL): string {
  if (input instanceof Request) return input.url;
  return typeof input === "string" ? input : input.toString();
}

function bodyText(body: BodyInit | null | undefined): string {
  if (typeof body !== "string") throw new TypeError("expected a string request body");
  return body;
}

function derLength(bytes: Uint8Array, offset: number): { length: number; next: number } {
  const first = bytes[offset];
  if (first === undefined) throw new TypeError("truncated DER length");
  if ((first & 0x80) === 0) return { length: first, next: offset + 1 };
  const count = first & 0x7f;
  if (count < 1 || count > 4) throw new TypeError("invalid DER length");
  let length = 0;
  for (let index = 0; index < count; index += 1) {
    const byte = bytes[offset + 1 + index];
    if (byte === undefined) throw new TypeError("truncated DER length bytes");
    length = length * 256 + byte;
  }
  return { length, next: offset + 1 + count };
}

function skipDer(bytes: Uint8Array, offset: number): number {
  if (bytes[offset] === undefined) throw new TypeError("truncated DER value");
  const length = derLength(bytes, offset + 1);
  return length.next + length.length;
}

function extractPkcs1(pkcs8: ArrayBuffer): ArrayBuffer {
  const bytes = new Uint8Array(pkcs8);
  if (bytes[0] !== 0x30) throw new TypeError("PKCS#8 was not a sequence");
  const outer = derLength(bytes, 1);
  let offset = skipDer(bytes, outer.next);
  offset = skipDer(bytes, offset);
  if (bytes[offset] !== 0x04) throw new TypeError("PKCS#8 did not contain a private-key octet string");
  const inner = derLength(bytes, offset + 1);
  return bytes.slice(inner.next, inner.next + inner.length).buffer;
}

function environment(): BrokerRuntimeEnv {
  return {
    ...ENV,
    DISPATCH_APP_PRIVATE_KEY: privateKey,
    LEGACY_SOURCE_APP_PRIVATE_KEY: privateKey,
    SOURCE_APP_PRIVATE_KEY: privateKey,
  };
}

function brokerRequest(
  authority: "source" | "legacy_source" | "dispatch" | "results" | "benchmark",
  url: string,
  options: Readonly<{ method?: string; body?: string | null; expectedCommit?: string | null }> = {},
): Request {
  return new Request("https://github-broker.internal/v1/proxy", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      schema_version: 1,
      audience: "lean-eval-submission-server",
      authority,
      method: options.method ?? "GET",
      url,
      body: options.body ?? null,
      expected_commit: options.expectedCommit ?? null,
    }),
  });
}

beforeAll(async () => {
  const pair = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: Uint8Array.of(1, 0, 1), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  );
  if (!("privateKey" in pair)) throw new TypeError("RSA key generation did not return a key pair");
  const exported = await crypto.subtle.exportKey("pkcs8", pair.privateKey);
  if (!(exported instanceof ArrayBuffer)) throw new TypeError("RSA private key export was not binary");
  privateKey = `-----BEGIN PRIVATE KEY-----\n${base64(exported)}\n-----END PRIVATE KEY-----`;
  rsaPrivateKey = `-----BEGIN RSA PRIVATE KEY-----\n${base64(extractPkcs1(exported))}\n-----END RSA PRIVATE KEY-----`;
});

describe("GitHub App broker", () => {
  it("mints a repository-scoped source token and proxies an allowlisted read", async () => {
    const calls: string[] = [];
    const upstream = vi.fn<typeof fetch>((input, init) => {
      const url = inputUrl(input);
      calls.push(url);
      if (url.endsWith("/repos/alice/proofs/installation")) {
        const authorization = new Headers(init?.headers).get("authorization") ?? "";
        const jwt = authorization.replace(/^Bearer /, "");
        expect(jwt.split(".")).toHaveLength(3);
        return Promise.resolve(Response.json({ id: 123 }));
      }
      if (url.endsWith("/app/installations/123/access_tokens")) {
        expect(JSON.parse(bodyText(init?.body))).toEqual({
          repositories: ["proofs"],
          permissions: { contents: "read", metadata: "read" },
        });
        return Promise.resolve(Response.json({ token: "ghs_test-installation-token", expires_at: new Date(NOW + 3_600_000).toISOString() }));
      }
      expect(url).toBe("https://api.github.com/repos/alice/proofs");
      expect(new Headers(init?.headers).get("authorization")).toBe("Bearer ghs_test-installation-token");
      return Promise.resolve(Response.json({ full_name: "alice/proofs", private: true }));
    });
    const response = await handleBrokerRequest(
      brokerRequest("source", "https://api.github.com/repos/alice/proofs"),
      environment(),
      upstream,
      NOW,
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ full_name: "alice/proofs", private: true });
    expect(calls).toHaveLength(3);
  });

  it("uses the legacy App client ID and permits only exact repository and commit reads", async () => {
    const expected = "c".repeat(40);
    const issuers: string[] = [];
    const upstream = vi.fn<typeof fetch>((input, init) => {
      const url = inputUrl(input);
      if (url.endsWith("/repos/alice/legacy-source/installation")) {
        const jwt = (new Headers(init?.headers).get("authorization") ?? "").replace(/^Bearer /, "");
        const encodedPayload = jwt.split(".")[1];
        if (encodedPayload === undefined) throw new TypeError("JWT payload was missing");
        const payload: unknown = JSON.parse(
          atob(encodedPayload.replaceAll("-", "+").replaceAll("_", "/")),
        );
        if (payload === null || typeof payload !== "object" || Array.isArray(payload) || !("iss" in payload)) {
          throw new TypeError("JWT payload was not an object");
        }
        issuers.push(String(payload.iss));
        return Promise.resolve(Response.json({ id: 9123 }));
      }
      if (url.endsWith("/app/installations/9123/access_tokens")) {
        expect(JSON.parse(bodyText(init?.body))).toEqual({
          repositories: ["legacy-source"],
          permissions: { contents: "read", metadata: "read" },
        });
        return Promise.resolve(Response.json({
          token: "ghs_legacy-source-installation-token",
          expires_at: new Date(NOW + 3_600_000).toISOString(),
        }));
      }
      if (url.endsWith(`/repos/alice/legacy-source/git/commits/${expected}`)) {
        return Promise.resolve(Response.json({ sha: expected }));
      }
      return Promise.reject(new Error(`unexpected GitHub call: ${url}`));
    });
    const response = await handleBrokerRequest(
      brokerRequest(
        "legacy_source",
        `https://api.github.com/repos/alice/legacy-source/git/commits/${expected}`,
        { expectedCommit: expected },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ sha: expected });
    expect(issuers).toEqual(["Iv23liLATwL7VxAK37uX"]);
  });

  it("rejects legacy-source tag access and a commit response with the wrong identity", async () => {
    const upstream = vi.fn<typeof fetch>((input) => {
      const url = inputUrl(input);
      if (url.endsWith("/installation")) return Promise.resolve(Response.json({ id: 9124 }));
      if (url.endsWith("/access_tokens")) {
        return Promise.resolve(Response.json({
          token: "ghs_wrong-commit-installation-token",
          expires_at: new Date(NOW + 3_600_000).toISOString(),
        }));
      }
      return Promise.resolve(Response.json({ sha: "d".repeat(40) }));
    });
    const tag = await handleBrokerRequest(
      brokerRequest(
        "legacy_source",
        "https://api.github.com/repos/alice/rejected/git/ref/tags/lean-eval%2F0198abcd-1111-7000-8000-000000000001",
        { expectedCommit: COMMIT },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(tag.status).toBe(403);
    expect(upstream).not.toHaveBeenCalled();

    const wrongCommit = await handleBrokerRequest(
      brokerRequest(
        "legacy_source",
        `https://api.github.com/repos/alice/wrong-commit/git/commits/${COMMIT}`,
        { expectedCommit: COMMIT },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(wrongCommit.status).toBe(409);
  });

  it("fails closed when the legacy-source credential is absent or malformed", async () => {
    const upstream = vi.fn<typeof fetch>();
    const configured = environment();
    const { LEGACY_SOURCE_APP_ID: removed, ...missing } = configured;
    expect(removed).toBe("Iv23liLATwL7VxAK37uX");
    for (const candidate of [
      missing,
      { ...configured, LEGACY_SOURCE_APP_ID: "not-a-github-client-id" },
      { ...configured, LEGACY_SOURCE_APP_ID: "Iv1.bad-punctuation" },
      { ...configured, LEGACY_SOURCE_APP_ID: "Iv23liLATwL7VxAK37uY" },
    ]) {
      const response = await handleBrokerRequest(
        brokerRequest("legacy_source", "https://api.github.com/repos/alice/unconfigured"),
        candidate,
        upstream,
        NOW,
      );
      expect(response.status).toBe(503);
    }
    expect(upstream).not.toHaveBeenCalled();
  });

  it("rejects well-formed but unreviewed source and dispatcher App identities", async () => {
    const upstream = vi.fn<typeof fetch>();
    const wrongSource = await handleBrokerRequest(
      brokerRequest("source", "https://api.github.com/repos/alice/unreviewed"),
      { ...environment(), SOURCE_APP_ID: "4666605" },
      upstream,
      NOW,
    );
    expect(wrongSource.status).toBe(503);

    const body = JSON.stringify({
      ref: `lean-eval-dispatch/${COMMIT}`,
      inputs: {
        workflow_commit: COMMIT,
        archive_state_callback_required: "true",
        callback_environment: "staging",
      },
    });
    const wrongDispatcher = await handleBrokerRequest(
      brokerRequest(
        "dispatch",
        "https://api.github.com/repos/leanprover/lean-eval-submissions/actions/workflows/submission.yml/dispatches",
        { method: "POST", body },
      ),
      { ...environment(), DISPATCH_APP_ID: "4666634" },
      upstream,
      NOW,
    );
    expect(wrongDispatcher.status).toBe(503);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("rejects gist proof instead of broadening installation authority", async () => {
    const upstream = vi.fn<typeof fetch>();
    const response = await handleBrokerRequest(
      brokerRequest("source", "https://api.github.com/gists/abcdef"),
      environment(),
      upstream,
      NOW,
    );
    expect(response.status).toBe(501);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("imports the PKCS#1 PEM format downloaded from GitHub App settings", async () => {
    const upstream = vi.fn<typeof fetch>((input) => {
      const url = inputUrl(input);
      if (url.endsWith("/installation")) return Promise.resolve(Response.json({ id: 124 }));
      if (url.endsWith("/access_tokens")) {
        return Promise.resolve(Response.json({ token: "ghs_pkcs1-installation-token", expires_at: new Date(NOW + 3_600_000).toISOString() }));
      }
      return Promise.resolve(Response.json({ full_name: "alice/pkcs1", private: true }));
    });
    const response = await handleBrokerRequest(
      brokerRequest("source", "https://api.github.com/repos/alice/pkcs1"),
      { ...environment(), SOURCE_APP_PRIVATE_KEY: rsaPrivateKey },
      upstream,
      NOW,
    );
    expect(response.status).toBe(200);
  });

  it("requires the immutable commit proof on source tag reads", async () => {
    const upstream = vi.fn<typeof fetch>();
    const response = await handleBrokerRequest(
      brokerRequest("source", "https://api.github.com/repos/alice/other/git/ref/tags/lean-eval%2F0198abcd-1111-7000-8000-000000000001"),
      environment(),
      upstream,
      NOW,
    );
    expect(response.status).toBe(400);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("rejects a dispatch whose tag and workflow commit disagree", async () => {
    const upstream = vi.fn<typeof fetch>();
    const body = JSON.stringify({
      ref: `lean-eval-dispatch/${COMMIT}`,
      inputs: {
        workflow_commit: "b".repeat(40),
        archive_state_callback_required: "true",
        callback_environment: "staging",
      },
    });
    const response = await handleBrokerRequest(
      brokerRequest(
        "dispatch",
        "https://api.github.com/repos/leanprover/lean-eval-submissions/actions/workflows/submission.yml/dispatches",
        { method: "POST", body },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(response.status).toBe(403);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("mints the separately permissioned dispatch token", async () => {
    const upstream = vi.fn<typeof fetch>((input, init) => {
      const url = inputUrl(input);
      if (url.endsWith("/installation")) return Promise.resolve(Response.json({ id: 456 }));
      if (url.endsWith("/access_tokens")) {
        expect(JSON.parse(bodyText(init?.body))).toEqual({
          repositories: ["lean-eval-submissions"],
          permissions: { actions: "write", contents: "read", metadata: "read" },
        });
        return Promise.resolve(Response.json({ token: "ghs_dispatch-installation-token", expires_at: new Date(NOW + 3_600_000).toISOString() }));
      }
      expect(new Headers(init?.headers).get("authorization")).toBe("Bearer ghs_dispatch-installation-token");
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    const body = JSON.stringify({
      ref: `lean-eval-dispatch/${COMMIT}`,
      inputs: {
        workflow_commit: COMMIT,
        archive_state_callback_required: "true",
        callback_environment: "staging",
      },
    });
    const response = await handleBrokerRequest(
      brokerRequest(
        "dispatch",
        "https://api.github.com/repos/leanprover/lean-eval-submissions/actions/workflows/submission.yml/dispatches",
        { method: "POST", body },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(response.status).toBe(204);
  });

  it("allows only the exact run-scoped source-free promotion canary target", async () => {
    const upstream = vi.fn<typeof fetch>((input) => {
      const url = inputUrl(input);
      if (url.endsWith("/installation")) return Promise.resolve(Response.json({ id: 457 }));
      if (url.endsWith("/access_tokens")) {
        return Promise.resolve(Response.json({
          token: "ghs_canary-installation-token",
          expires_at: new Date(NOW + 3_600_000).toISOString(),
        }));
      }
      expect(url).toBe(
        "https://api.github.com/repos/leanprover/lean-eval-submissions/actions/workflows/promotion-canary.yml/dispatches",
      );
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    const body = JSON.stringify({
      ref: `lean-eval-dispatch/${COMMIT}`,
      inputs: {
        workflow_commit: COMMIT,
        submission_id: "0198abcd-1111-7000-8000-0000000000ca",
        controller_run_id: "32712345678",
        controller_run_attempt: "2",
      },
    });
    const response = await handleBrokerRequest(
      brokerRequest(
        "dispatch",
        "https://api.github.com/repos/leanprover/lean-eval-submissions/actions/workflows/promotion-canary.yml/dispatches",
        { method: "POST", body },
      ),
      { ...environment(), DEPLOYED_COMMIT: COMMIT },
      upstream,
      NOW,
    );
    expect(response.status).toBe(204);

    const production = await handleBrokerRequest(
      brokerRequest(
        "dispatch",
        "https://api.github.com/repos/leanprover/lean-eval-submissions/actions/workflows/promotion-canary.yml/dispatches",
        { method: "POST", body },
      ),
      { ...environment(), DEPLOYED_COMMIT: COMMIT, DEPLOYMENT_ENVIRONMENT: "production" },
      upstream,
      NOW,
    );
    expect(production.status).toBe(403);
  });

  it("accepts an exact historical no-op canary after the broker deploy changes", async () => {
    const upstream = vi.fn<typeof fetch>((input) => {
      const url = inputUrl(input);
      if (url.endsWith("/installation")) return Promise.resolve(Response.json({ id: 458 }));
      if (url.endsWith("/access_tokens")) {
        return Promise.resolve(Response.json({
          token: "ghs_historical-canary-token",
          expires_at: new Date(NOW + 3_600_000).toISOString(),
        }));
      }
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    const body = JSON.stringify({
      ref: `lean-eval-dispatch/${COMMIT}`,
      inputs: {
        workflow_commit: COMMIT,
        submission_id: "0198abcd-1111-7000-8000-0000000000ca",
        controller_run_id: "32712345678",
        controller_run_attempt: "1",
      },
    });
    const response = await handleBrokerRequest(
      brokerRequest(
        "dispatch",
        "https://api.github.com/repos/leanprover/lean-eval-submissions/actions/workflows/promotion-canary.yml/dispatches",
        { method: "POST", body },
      ),
      { ...environment(), DEPLOYED_COMMIT: "b".repeat(40) },
      upstream,
      NOW,
    );
    expect(response.status).toBe(204);
    expect(upstream).toHaveBeenCalled();
  });

  it("allows only exact-commit Results reads through the dispatch installation", async () => {
    const upstream = vi.fn<typeof fetch>((input, init) => {
      const url = inputUrl(input);
      expect(init?.redirect).toBe("manual");
      if (url.endsWith("/repos/leanprover/lean-eval-submissions/installation")) {
        return Promise.resolve(Response.json({ id: 789 }));
      }
      if (url.endsWith("/branches/staging-results")) {
        return Promise.resolve(Response.json({
          name: "staging-results",
          protected: true,
          commit: { sha: "f".repeat(40) },
        }));
      }
      if (url.endsWith(`/compare/${COMMIT}...staging-results`)) {
        return Promise.resolve(Response.json({
          status: "ahead",
          base_commit: { sha: COMMIT },
          merge_base_commit: { sha: COMMIT },
          ahead_by: 1,
          behind_by: 0,
          total_commits: 1,
          commits: [{ sha: "f".repeat(40) }],
        }));
      }
      if (url.endsWith("/app/installations/789/access_tokens")) {
        expect(JSON.parse(bodyText(init?.body))).toEqual({
          repositories: ["lean-eval-submissions"],
          permissions: { contents: "read", metadata: "read" },
        });
        return Promise.resolve(Response.json({
          token: "ghs_results-installation-token",
          expires_at: new Date(NOW + 3_600_000).toISOString(),
        }));
      }
      expect(url).toBe(
        `https://api.github.com/repos/leanprover/lean-eval-submissions/contents/results/alice.json?ref=${COMMIT}`,
      );
      expect(new Headers(init?.headers).get("authorization"))
        .toBe("Bearer ghs_results-installation-token");
      return Promise.resolve(Response.json({
        type: "file",
        path: "results/alice.json",
        encoding: "base64",
        content: "e30=",
        size: 2,
      }));
    });
    const branchResponse = await handleBrokerRequest(
      brokerRequest(
        "results",
        "https://api.github.com/repos/leanprover/lean-eval-submissions/branches/staging-results",
        { expectedCommit: COMMIT },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(branchResponse.status).toBe(200);

    const compareResponse = await handleBrokerRequest(
      brokerRequest(
        "results",
        `https://api.github.com/repos/leanprover/lean-eval-submissions/compare/${COMMIT}...staging-results`,
        { expectedCommit: COMMIT },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(compareResponse.status).toBe(200);

    const response = await handleBrokerRequest(
      brokerRequest(
        "results",
        `https://api.github.com/repos/leanprover/lean-eval-submissions/contents/results/alice.json?ref=${COMMIT}`,
        { expectedCommit: COMMIT },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(response.status).toBe(200);

    const rejected = await handleBrokerRequest(
      brokerRequest(
        "results",
        `https://api.github.com/repos/leanprover/lean-eval-submissions/contents/README.md?ref=${COMMIT}`,
        { expectedCommit: COMMIT },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(rejected.status).toBe(403);
  });

  it("allows only ancestry-bound public benchmark reads without an App installation", async () => {
    const upstream = vi.fn<typeof fetch>((input, init) => {
      const url = inputUrl(input);
      expect(new Headers(init?.headers).get("authorization")).toBeNull();
      if (url.endsWith("/branches/main")) {
        return Promise.resolve(Response.json({
          name: "main",
          protected: true,
          commit: { sha: "f".repeat(40) },
        }));
      }
      if (url.endsWith(`/compare/${COMMIT}...main`)) {
        return Promise.resolve(Response.json({
          status: "ahead",
          base_commit: { sha: COMMIT },
          merge_base_commit: { sha: COMMIT },
          ahead_by: 1,
          behind_by: 0,
          total_commits: 1,
          commits: [{ sha: "f".repeat(40) }],
        }));
      }
      expect(url).toBe(
        `https://api.github.com/repos/leanprover/lean-eval/contents/manifests/problems/two_plus_two.toml?ref=${COMMIT}`,
      );
      return Promise.resolve(Response.json({
        type: "file",
        path: "manifests/problems/two_plus_two.toml",
        encoding: "base64",
        content: "aWQgPSAidHdvX3BsdXNfdHdvIg==",
        size: 19,
      }));
    });
    for (const suffix of [
      "branches/main",
      `compare/${COMMIT}...main`,
      `contents/manifests/problems/two_plus_two.toml?ref=${COMMIT}`,
    ]) {
      const response = await handleBrokerRequest(
        brokerRequest(
          "benchmark",
          `https://api.github.com/repos/leanprover/lean-eval/${suffix}`,
          { expectedCommit: COMMIT },
        ),
        environment(),
        upstream,
        NOW,
      );
      expect(response.status).toBe(200);
    }
    const rejected = await handleBrokerRequest(
      brokerRequest(
        "benchmark",
        `https://api.github.com/repos/leanprover/lean-eval/contents/README.md?ref=${COMMIT}`,
        { expectedCommit: COMMIT },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(rejected.status).toBe(403);
    expect(upstream.mock.calls.every(([input]) =>
      !inputUrl(input).includes("/installation") &&
      !inputUrl(input).includes("/access_tokens"))).toBe(true);
  });

  it("accepts the live identical compare shape for Results and benchmark reads", async () => {
    for (const authority of ["results", "benchmark"] as const) {
      const suffix = authority === "results" ? "staging-results" : "main";
      const repository = authority === "results"
        ? "leanprover/lean-eval-submissions"
        : "leanprover/lean-eval";
      const upstream = vi.fn<typeof fetch>((input) => {
        const url = inputUrl(input);
        if (url.endsWith("/installation")) return Promise.resolve(Response.json({ id: 792 }));
        if (url.endsWith("/access_tokens")) {
          return Promise.resolve(Response.json({
            token: "ghs_identical-comparison-token",
            expires_at: new Date(NOW + 3_600_000).toISOString(),
          }));
        }
        return Promise.resolve(Response.json({
          status: "identical",
          base_commit: { sha: COMMIT },
          merge_base_commit: { sha: COMMIT },
          ahead_by: 0,
          behind_by: 0,
          total_commits: 0,
          commits: [],
        }));
      });
      const response = await handleBrokerRequest(
        brokerRequest(
          authority,
          `https://api.github.com/repos/${repository}/compare/${COMMIT}...${suffix}`,
          { expectedCommit: COMMIT },
        ),
        environment(),
        upstream,
        NOW + 3,
      );
      expect(response.status).toBe(200);
    }
  });

  it("rejects empty, incoherent, truncated, or tampered compare shapes for both authorities", async () => {
    const invalidComparisons = [
      {
        status: "ahead",
        base_commit: { sha: COMMIT },
        merge_base_commit: { sha: COMMIT },
        ahead_by: 1,
        behind_by: 0,
        total_commits: 1,
        commits: [],
      },
      {
        status: "ahead",
        base_commit: { sha: COMMIT },
        merge_base_commit: { sha: COMMIT },
        ahead_by: 2,
        behind_by: 0,
        total_commits: 1,
        commits: [{ sha: "f".repeat(40) }],
      },
      {
        status: "ahead",
        base_commit: { sha: COMMIT },
        merge_base_commit: { sha: COMMIT },
        ahead_by: 251,
        behind_by: 0,
        total_commits: 251,
        commits: [{ sha: "f".repeat(40) }],
      },
      {
        status: "ahead",
        base_commit: { sha: COMMIT },
        merge_base_commit: { sha: COMMIT },
        ahead_by: 1,
        behind_by: 0,
        total_commits: 1,
        commits: [{ sha: "f".repeat(40) }],
        head_commit: { sha: "9".repeat(40) },
      },
      {
        status: "identical",
        base_commit: { sha: COMMIT },
        merge_base_commit: { sha: COMMIT },
        ahead_by: 0,
        behind_by: 0,
        total_commits: 0,
        commits: [{ sha: COMMIT }],
      },
    ];
    for (const authority of ["results", "benchmark"] as const) {
      const suffix = authority === "results" ? "staging-results" : "main";
      const repository = authority === "results"
        ? "leanprover/lean-eval-submissions"
        : "leanprover/lean-eval";
      for (const comparison of invalidComparisons) {
        const upstream = vi.fn<typeof fetch>((input) => {
          const url = inputUrl(input);
          if (url.endsWith("/installation")) return Promise.resolve(Response.json({ id: 793 }));
          if (url.endsWith("/access_tokens")) {
            return Promise.resolve(Response.json({
              token: "ghs_invalid-comparison-token",
              expires_at: new Date(NOW + 3_600_000).toISOString(),
            }));
          }
          return Promise.resolve(Response.json(comparison));
        });
        const response = await handleBrokerRequest(
          brokerRequest(
            authority,
            `https://api.github.com/repos/${repository}/compare/${COMMIT}...${suffix}`,
            { expectedCommit: COMMIT },
          ),
          environment(),
          upstream,
          NOW + 4,
        );
        expect(response.status).toBe(409);
      }
    }
  });

  it("pins the Results branch allowlist to the deployment environment", async () => {
    const upstream = vi.fn<typeof fetch>();
    const stagingOnProduction = await handleBrokerRequest(
      brokerRequest(
        "results",
        "https://api.github.com/repos/leanprover/lean-eval-submissions/branches/staging-results",
        { expectedCommit: COMMIT },
      ),
      { ...environment(), DEPLOYMENT_ENVIRONMENT: "production" },
      upstream,
      NOW,
    );
    expect(stagingOnProduction.status).toBe(403);
    const mainOnStaging = await handleBrokerRequest(
      brokerRequest(
        "results",
        "https://api.github.com/repos/leanprover/lean-eval-submissions/branches/main",
        { expectedCommit: COMMIT },
      ),
      environment(),
      upstream,
      NOW,
    );
    expect(mainOnStaging.status).toBe(403);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("rejects a hostile Results comparison and never follows credentialed redirects", async () => {
    const calls: string[] = [];
    const upstream = vi.fn<typeof fetch>((input, init) => {
      const url = inputUrl(input);
      calls.push(url);
      expect(init?.redirect).toBe("manual");
      if (url.endsWith("/installation")) return Promise.resolve(Response.json({ id: 790 }));
      if (url.endsWith("/access_tokens")) {
        return Promise.resolve(Response.json({
          token: "ghs_redirect-safe-token",
          expires_at: new Date(NOW + 3_600_000).toISOString(),
        }));
      }
      return Promise.resolve(new Response(null, {
        status: 302,
        headers: { location: "https://attacker.invalid/steal" },
      }));
    });
    const response = await handleBrokerRequest(
      brokerRequest(
        "results",
        "https://api.github.com/repos/leanprover/lean-eval-submissions/branches/staging-results",
        { expectedCommit: "b".repeat(40) },
      ),
      environment(),
      upstream,
      NOW + 1,
    );
    expect(response.status).toBe(302);
    expect(response.headers.get("location")).toBeNull();
    expect(calls).not.toContain("https://attacker.invalid/steal");
  });

  it("rejects a Results comparison whose merge base is not the client commit", async () => {
    const expected = "c".repeat(40);
    const upstream = vi.fn<typeof fetch>((input, init) => {
      const url = inputUrl(input);
      expect(init?.redirect).toBe("manual");
      if (url.endsWith("/installation")) return Promise.resolve(Response.json({ id: 791 }));
      if (url.endsWith("/access_tokens")) {
        return Promise.resolve(Response.json({
          token: "ghs_comparison-token",
          expires_at: new Date(NOW + 3_600_000).toISOString(),
        }));
      }
      return Promise.resolve(Response.json({
        status: "diverged",
        base_commit: { sha: expected },
        merge_base_commit: { sha: "d".repeat(40) },
        head_commit: { sha: "f".repeat(40) },
      }));
    });
    const response = await handleBrokerRequest(
      brokerRequest(
        "results",
        `https://api.github.com/repos/leanprover/lean-eval-submissions/compare/${expected}...staging-results`,
        { expectedCommit: expected },
      ),
      environment(),
      upstream,
      NOW + 2,
    );
    expect(response.status).toBe(409);
  });

  it("serializes an explicit audience, authority, and tag commit for the service binding", async () => {
    const bindingFetch = vi.fn<Pick<Fetcher, "fetch">["fetch"]>((_input, init) => {
      const payload = JSON.parse(bodyText(init?.body)) as Record<string, unknown>;
      expect(payload.audience).toBe("lean-eval-submission-server");
      expect(payload.authority).toBe("source");
      expect(payload.expected_commit).toBe(COMMIT);
      return Promise.resolve(Response.json({ ok: true }));
    });
    const proxied = githubBrokerFetch({ fetch: bindingFetch }, "source");
    const response = await proxied("https://api.github.com/repos/alice/proofs/git/tags/" + "b".repeat(40), {
      headers: { "x-lean-eval-expected-commit": COMMIT },
    });
    expect(response.status).toBe(200);
  });

  it("serializes a distinct legacy-source authority for exact commit admission", async () => {
    const bindingFetch = vi.fn<Pick<Fetcher, "fetch">["fetch"]>((_input, init) => {
      const payload = JSON.parse(bodyText(init?.body)) as Record<string, unknown>;
      expect(payload.audience).toBe("lean-eval-submission-server");
      expect(payload.authority).toBe("legacy_source");
      expect(payload.expected_commit).toBe(COMMIT);
      return Promise.resolve(Response.json({ sha: COMMIT }));
    });
    const proxied = githubBrokerFetch({ fetch: bindingFetch }, "legacy_source");
    const response = await proxied(
      `https://api.github.com/repos/alice/proofs/git/commits/${COMMIT}`,
      { headers: { "x-lean-eval-expected-commit": COMMIT } },
    );
    expect(response.status).toBe(200);
  });

  it("serializes the separate exact-commit Results authority", async () => {
    const bindingFetch = vi.fn<Pick<Fetcher, "fetch">["fetch"]>((_input, init) => {
      const payload = JSON.parse(bodyText(init?.body)) as Record<string, unknown>;
      expect(payload.authority).toBe("results");
      expect(payload.expected_commit).toBe(COMMIT);
      return Promise.resolve(Response.json({ ok: true }));
    });
    const proxied = githubBrokerFetch({ fetch: bindingFetch }, "results");
    const response = await proxied(
      `https://api.github.com/repos/leanprover/lean-eval-submissions/contents/results/alice.json?ref=${COMMIT}`,
      { headers: { "x-lean-eval-expected-commit": COMMIT } },
    );
    expect(response.status).toBe(200);
  });
});
