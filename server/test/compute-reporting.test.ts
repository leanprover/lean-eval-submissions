import { describe, expect, it, vi } from "vitest";
import fixture from "./fixtures/acceptance-measurement-v1.json";
import { decodeAcceptanceMeasurement, verifyMeasurementIdentity } from "../src/acceptance-measurement";
import { decodeProductionMetadata } from "../src/api-contract";
import { handleRequest, type RuntimeEnv, type StateAccess } from "../src/app";
import { signToken } from "../src/auth";
import { GitHubProvider } from "../src/github-provider";
import { metadataScript } from "../src/browser-ui";
import { validateStateEvent, type WritableStateEvent } from "../src/state-event";

const secret = "test-secret-with-at-least-thirty-two-bytes";
const now = Date.parse("2026-10-22T08:00:00.000Z");
const env: RuntimeEnv = { AUTH_TOKEN_SECRET: secret, DEPLOYMENT_ENVIRONMENT: "staging", DEPLOYED_COMMIT: "a".repeat(40), INTAKE_ENABLED: "false", INTAKE_ENABLEMENT_MODE: "durable", STATE_REPOSITORY: "leanprover/lean-eval-state-staging", LIFECYCLE_CALLBACK_TOKEN: secret, LEGACY_RESULT_OWNER_API_ENABLED: "true", RESULT_OWNER_STATE_CONTRACT_COMMIT: "9d041099a972c65d430c2c9eb977f762dba92772", API_RATE_LIMITER: { limit: () => Promise.resolve({ success: true }) } };
const lifecycle = { waitUntil: () => undefined };
function provider() {
  const instance = new GitHubProvider();
  const verify = vi.spyOn(instance, "verifyLegacyResult").mockResolvedValue({ resultId: fixture.result_id, ownerLogin: fixture.owner_login, baseResult: { declared_model: fixture.declared_model, problem_id: fixture.problem_id, statement_revision: 1, results_repository: "leanprover/lean-eval-submissions", results_commit: fixture.results_commit, results_path: "results/kim-em.json", canonical_record_sha256: "a".repeat(64) }, metadataContext: { productionMetadata: { harness: "Original harness", input_tokens: 12 }, benchmarkCommit: fixture.benchmark_commit, submissionId: null } });
  return { instance, verify };
}
async function authorization() {
  return "Bearer " + await signToken(secret, { kind: "browser_session", login: "kim-em", github_id: 1, issued_at: now / 1000, expires_at: now / 1000 + 3600 });
}

describe("compute reporting", () => {
  it("shares the Python receipt format and validates identity and series", async () => {
    const receipt = decodeAcceptanceMeasurement(fixture);
    await expect(verifyMeasurementIdentity(receipt)).resolves.toBeUndefined();
    await expect(verifyMeasurementIdentity({ ...receipt, architecture: "aarch64" })).rejects.toThrow("series");
    expect(() => decodeAcceptanceMeasurement({ ...fixture, build_wall_time_ms: null })).toThrow();
    expect(() => decodeAcceptanceMeasurement({ ...fixture, checker_retired_instructions: 0 })).toThrow();
    expect(() => decodeAcceptanceMeasurement({ ...fixture, source_code: "private" })).toThrow();
  });
  it("authenticates the recorder before touching result or State APIs", async () => {
    const p = provider();
    const response = await handleRequest(new Request("https://submit.test/api/v1/results/" + fixture.result_id + "/measurements", { method: "POST", body: JSON.stringify(fixture) }), env, lifecycle, { provider: p.instance });
    expect(response.status).toBe(401);
    expect(p.verify).not.toHaveBeenCalled();
  });
  it("publishes a result-bound measurement idempotently while intake is disabled", async () => {
    const events = new Map<string, WritableStateEvent>();
    const appendEvent = vi.fn((event: WritableStateEvent) => {
      validateStateEvent(event);
      const created = !events.has(event.event_id); events.set(event.event_id, event);
      return Promise.resolve({ created });
    });
    const state = { appendEvent, assertResultOwnerContract: () => Promise.resolve("d7d67f5b609735286355c6be460d85c203aac64c") } as unknown as StateAccess;
    const p = provider();
    for (const status of [201, 200]) {
      const response = await handleRequest(new Request("https://submit.test/api/v1/results/" + fixture.result_id + "/measurements", { method: "POST", headers: { authorization: "Bearer " + secret, "content-type": "application/json" }, body: JSON.stringify(fixture) }), env, lifecycle, { provider: p.instance, state });
      expect(response.status).toBe(status);
    }
    expect(events.size).toBe(1);
    expect([...events.values()][0]).toMatchObject({ actor: { kind: "system" }, payload: { measurement: fixture } });
    expect(p.verify).toHaveBeenCalledWith(fixture.owner_login, fixture.results_commit, fixture.result_id, true);
  });
  it("prepopulates the accepted metadata and owner overlay with an ETag", async () => {
    const p = provider();
    const readMetadataForOwner = vi.fn(() => Promise.resolve({ metadata: { notes: "Added later", input_tokens: 20 }, revision: "0198abcd-0000-7000-8000-000000000009", submissionId: null }));
    const state = { readMetadataForOwner } as unknown as StateAccess;
    const response = await handleRequest(new Request("https://submit.test/api/v1/results/" + fixture.result_id + "/metadata?results_commit=" + fixture.results_commit, { headers: { authorization: await authorization() } }), env, lifecycle, { provider: p.instance, state, now: () => now });
    expect(response.status).toBe(200);
    expect(response.headers.get("etag")).toBe('"0198abcd-0000-7000-8000-000000000009"');
    expect(await response.json()).toMatchObject({ production_metadata: { harness: "Original harness", notes: "Added later", input_tokens: 20 }, claim_required: false });
    expect(readMetadataForOwner).toHaveBeenCalledWith(fixture.result_id, "kim-em");
  });
  it("rejects a stale metadata edit before writing", async () => {
    const appendSubmissionMutation = vi.fn();
    const state = { readSubmission: () => Promise.resolve({ owner_login: "kim-em", mutation_event_id: "0198abcd-0000-7000-8000-000000000009" }), appendSubmissionMutation } as unknown as StateAccess;
    const response = await handleRequest(new Request("https://submit.test/api/v1/submissions/0198abcd-0000-7000-8000-000000000002/metadata", { method: "PATCH", headers: { authorization: await authorization(), "if-match": '"stale"', "idempotency-key": "0198abcd-0000-7000-8000-000000000001" }, body: JSON.stringify({ production_metadata: { input_tokens: 50 } }) }), { ...env, INTAKE_ENABLED: "true" }, lifecycle, { state, now: () => now });
    expect(response.status).toBe(412);
    expect(appendSubmissionMutation).not.toHaveBeenCalled();
  });
  it("accepts zero cached tokens and rejects a subset larger than input", () => {
    expect(decodeProductionMetadata({ input_tokens: 0, cached_input_tokens: 0 })).toEqual({ input_tokens: 0, cached_input_tokens: 0 });
    expect(() => decodeProductionMetadata({ input_tokens: 5, cached_input_tokens: 6 })).toThrow();
  });
  it("does not restore fields removed from server-owned metadata", async () => {
    const p = provider();
    const state = { readMetadataForOwner: () => Promise.resolve({ metadata: { notes: "Kept" }, revision: "revision", submissionId: "0198abcd-0000-7000-8000-000000000002" }) } as unknown as StateAccess;
    const response = await handleRequest(new Request("https://submit.test/api/v1/results/" + fixture.result_id + "/metadata?results_commit=" + fixture.results_commit, { headers: { authorization: await authorization() } }), env, lifecycle, { provider: p.instance, state, now: () => now });
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ production_metadata: { notes: "Kept" } });
  });
});

function element(value = "") {
  const listeners = new Map<string, (event: { preventDefault: () => void }) => Promise<void> | void>();
  return { value, disabled: false, textContent: "", listeners,
    addEventListener: (name: string, listener: (event: { preventDefault: () => void }) => Promise<void> | void) => { listeners.set(name, listener); },
  };
}
async function editor(fetcher: (url: string, init?: RequestInit) => Promise<Response>, storage = new Map<string, string>(), search = "?result_id=" + fixture.result_id + "&results_commit=" + fixture.results_commit) {
  const elements = new Map(["metadata-form", "metadata-save", "metadata-status", "metadata-sign-in", "production_metadata", "meta_input_tokens", "meta_notes", "meta_prompt", "meta_harness"].map(id => ["#" + id, element(id === "production_metadata" ? "{}" : "")]));
  const get = (selector: string) => {
    const found = elements.get(selector);
    if (found === undefined) throw new Error("Missing element: " + selector);
    return found;
  };
  const script = await metadataScript().text();
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const run = new Function("document", "sessionStorage", "location", "fetch", script);
  // eslint-disable-next-line @typescript-eslint/no-unsafe-call
  run({ querySelector: (selector: string) => elements.get(selector) ?? null }, {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, value); },
    removeItem: (key: string) => { storage.delete(key); },
  }, { search }, fetcher);
  await vi.waitFor(() => expect(get("#metadata-status").textContent).not.toBe(""));
  const submit = get("#metadata-form").listeners.get("submit");
  if (submit === undefined) throw new Error("Missing submit listener");
  return { get, submit: () => submit({ preventDefault: () => undefined }) };
}
type EditorContext = { production_metadata: Record<string, unknown>; revision: string; submission_id: string | null; claim_required: boolean };
const initialContext: EditorContext = { production_metadata: { input_tokens: 100, notes: "Original", prompt: "Remove me", harness: "  Custom harness  " }, revision: "r1", submission_id: "0198abcd-0000-7000-8000-000000000002", claim_required: false };
function contextResponse(value: EditorContext, key: number) {
  return Response.json({ ...value, idempotency_key: "0198abcd-0000-7000-8000-" + String(key).padStart(12, "0") }, { headers: { etag: '"' + value.revision + '"' } });
}
function requestBody(init?: RequestInit): { production_metadata: Record<string, unknown> } {
  if (typeof init?.body !== "string") throw new Error("Expected a JSON request body");
  return JSON.parse(init.body) as { production_metadata: Record<string, unknown> };
}

describe("production metadata editor behavior", () => {
  it("sends the current full server metadata and keeps a deleted field absent after refresh", async () => {
    let current = structuredClone(initialContext), issued = 0;
    const patches: RequestInit[] = [];
    const fetcher = vi.fn((_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        patches.push(init);
        current = { ...current, production_metadata: requestBody(init).production_metadata, revision: "r2" };
        return Promise.resolve(Response.json({ status: "amended" }));
      }
      return Promise.resolve(contextResponse(current, ++issued));
    });
    const { get, submit } = await editor(fetcher);
    get("#meta_prompt").value = "";
    get("#meta_notes").value = "Updated";
    await submit();
    expect(requestBody(patches[0])).toEqual({ production_metadata: { input_tokens: 100, notes: "Updated", harness: "  Custom harness  " } });
    expect(patches[0]?.headers).toMatchObject({ "if-match": '"r1"', "idempotency-key": "0198abcd-0000-7000-8000-000000000002" });
    expect(get("#meta_prompt").value).toBe("");
    expect(get("#metadata-status").textContent).toContain("Saved");
  });
  it("sends only changed legacy fields so unchanged figures keep their provenance", async () => {
    const patches: RequestInit[] = [];
    let issued = 0;
    const fetcher = (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") { patches.push(init); return Promise.resolve(Response.json({ status: "backfilled" })); }
      return Promise.resolve(contextResponse({ ...initialContext, submission_id: null }, ++issued));
    };
    const { get, submit } = await editor(fetcher);
    get("#meta_notes").value = "Updated";
    await submit();
    expect(requestBody(patches[0])).toEqual({ production_metadata: { notes: "Updated" } });
  });
  it("refreshes the server-clock key before an unclaimed legacy save and again after claiming", async () => {
    let claimed = false, issued = 0;
    const writes: { url: string; init: RequestInit }[] = [];
    const fetcher = (url: string, init?: RequestInit) => {
      if (init?.method) {
        writes.push({ url, init });
        if (url.endsWith("/claims")) claimed = true;
        return Promise.resolve(Response.json({ status: "ok" }));
      }
      return Promise.resolve(contextResponse({ ...initialContext, submission_id: null, claim_required: !claimed, revision: claimed ? "claimed" : "unclaimed" }, ++issued));
    };
    const { get, submit } = await editor(fetcher);
    get("#meta_notes").value = "Updated after a long edit";
    await submit();
    expect(writes[0]?.url).toBe("/api/v1/results/claims");
    expect(writes[0]?.init.headers).toMatchObject({ "idempotency-key": "0198abcd-0000-7000-8000-000000000002" });
    expect(writes[1]?.init.headers).toMatchObject({ "if-match": '"claimed"', "idempotency-key": "0198abcd-0000-7000-8000-000000000003" });
  });
  it("does not overwrite an edit made while the legacy result is being claimed", async () => {
    let claimed = false, issued = 0;
    const writes: RequestInit[] = [];
    const fetcher = (_url: string, init?: RequestInit) => {
      if (init?.method) { writes.push(init); claimed = true; return Promise.resolve(Response.json({ status: "claimed" })); }
      const production_metadata = claimed ? { ...initialContext.production_metadata, notes: "Concurrent edit" } : initialContext.production_metadata;
      return Promise.resolve(contextResponse({ ...initialContext, production_metadata, submission_id: null, claim_required: !claimed, revision: claimed ? "claimed" : "unclaimed" }, ++issued));
    };
    const { get, submit } = await editor(fetcher);
    get("#meta_notes").value = "My draft";
    await submit();
    expect(writes).toHaveLength(1);
    expect(get("#meta_notes").value).toBe("My draft");
    expect(get("#metadata-status").textContent).toContain("changed while claiming");
  });
  it("keeps a stale draft in the form and does not send a mutation", async () => {
    let reads = 0;
    const fetcher = vi.fn(() => Promise.resolve(contextResponse({ ...initialContext, revision: ++reads === 1 ? "r1" : "r2" }, reads)));
    const { get, submit } = await editor(fetcher);
    get("#meta_notes").value = "My draft";
    await submit();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(get("#meta_notes").value).toBe("My draft");
    expect(get("#metadata-status").textContent).toContain("result changed");
  });
  it("restores the result target after GitHub sign-in", async () => {
    const storage = new Map<string, string>();
    const unauthenticated = await editor(() => Promise.resolve(Response.json({}, { status: 401 })), storage);
    expect(unauthenticated.get("#metadata-status").textContent).toContain("Sign in");
    const fetcher = vi.fn(() => Promise.resolve(contextResponse(initialContext, 1)));
    const signedIn = await editor(fetcher, storage, "?oauth=success");
    expect(fetcher).toHaveBeenCalledWith("/api/v1/results/" + fixture.result_id + "/metadata?results_commit=" + fixture.results_commit);
    expect(signedIn.get("#meta_notes").value).toBe("Original");
  });
});
