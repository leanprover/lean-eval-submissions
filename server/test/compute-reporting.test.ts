import { describe, expect, it, vi } from "vitest";
import fixture from "./fixtures/acceptance-measurement-v1.json";
import { decodeAcceptanceMeasurement, verifyMeasurementIdentity } from "../src/acceptance-measurement";
import { decodeProductionMetadata } from "../src/api-contract";
import { handleRequest, type RuntimeEnv, type StateAccess } from "../src/app";
import { signToken } from "../src/auth";
import { GitHubProvider } from "../src/github-provider";
import { metadataPage, metadataScript } from "../src/browser-ui";
import { validateStateEvent, type WritableStateEvent } from "../src/state-event";

const secret = "test-secret-with-at-least-thirty-two-bytes";
const now = Date.parse("2026-10-22T08:00:00.000Z");
const env: RuntimeEnv = { AUTH_TOKEN_SECRET: secret, DEPLOYMENT_ENVIRONMENT: "staging", DEPLOYED_COMMIT: "a".repeat(40), INTAKE_ENABLED: "false", INTAKE_ENABLEMENT_MODE: "durable", STATE_REPOSITORY: "leanprover/lean-eval-state-staging", LIFECYCLE_CALLBACK_TOKEN: secret, LEGACY_RESULT_OWNER_API_ENABLED: "true", RESULT_OWNER_STATE_CONTRACT_COMMIT: "84a67577279884f5ca9b8ab92db990295443d09a", API_RATE_LIMITER: { limit: () => Promise.resolve({ success: true }) } };
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
    const state = { appendEvent, assertResultOwnerContract: () => Promise.resolve("e5ca38ef7c2427ee1b06f152b77477b47e119494") } as unknown as StateAccess;
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
  it("serves a safe editor with accounting guidance", async () => {
    expect(await metadataPage().text()).toContain("Estimated API-equivalent cost");
    const script = await metadataScript().text();
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    expect(() => new Function(script)).not.toThrow();
    expect(script).not.toContain("innerHTML");
    expect(script).toContain('"if-match": revision');
    expect(script).toContain("context.idempotency_key");
  });
});
