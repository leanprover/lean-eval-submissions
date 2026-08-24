import { describe, expect, it } from "vitest";

import canonicalizationVector from "./fixtures/result-owner-canonicalization-vectors-v1.json";
import { GitHubProvider } from "../src/github-provider";
import {
  backfilledOverlay,
  canonicalJson,
  claimedOverlay,
  decodeInitialResultAmendmentView,
  decodeResultReleaseStatusView,
  decodeResultIdentityGuard,
  decodeResultOverlay,
  decodeSourceRecordIndex,
  initialResultAmendmentView,
  initialResultReleaseStatusView,
  metadataAlreadyEqual,
  resultAmendmentPath,
  resultId,
  resultReleaseStatusPath,
  sha256Hex,
  sourceRecordId,
  type VerifiedLegacyResult,
} from "../src/result-owner";

function resultContents(document: unknown): Response {
  const encoded = new TextEncoder().encode(JSON.stringify(document));
  let binary = "";
  for (const byte of encoded) binary += String.fromCharCode(byte);
  return Response.json({
    type: "file",
    path: "results/alice.json",
    encoding: "base64",
    content: btoa(binary),
    size: encoded.byteLength,
  });
}

function reachableResultFetcher(
  contents: () => Response,
  commit = "e".repeat(40),
  branch: "main" | "staging-results" = "main",
): (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> {
  return (input, init) => {
    const url = input instanceof Request ? input.url : input.toString();
    expect(new Headers(init?.headers).get("x-lean-eval-expected-commit")).toBe(commit);
    expect(init?.redirect).toBe("manual");
    if (url.endsWith(`/branches/${branch}`)) {
      return Promise.resolve(Response.json({ name: branch, protected: true, commit: { sha: "f".repeat(40) } }));
    }
    if (url.endsWith(`/compare/${commit}...${branch}`)) {
      return Promise.resolve(Response.json({
        status: "ahead",
        base_commit: { sha: commit },
        merge_base_commit: { sha: commit },
        head_commit: { sha: "f".repeat(40) },
      }));
    }
    if (url === `https://api.github.com/repos/leanprover/lean-eval-submissions/contents/results/alice.json?ref=${commit}`) {
      return Promise.resolve(contents());
    }
    throw new Error(`unexpected Results verification request: ${url}`);
  };
}

const EVENT_ID = "0198abcd-0000-7000-8000-000000000001";
const VERIFIED: VerifiedLegacyResult = {
  resultId: `r2_${"1".repeat(64)}`,
  ownerLogin: "alice",
  baseResult: {
    declared_model: "Example Model",
    problem_id: "two_plus_two",
    statement_revision: 1,
    results_repository: "leanprover/lean-eval-submissions",
    results_commit: "a".repeat(40),
    results_path: "results/alice.json",
    canonical_record_sha256: "b".repeat(64),
  },
};

describe("legacy result owner contracts", () => {
  it("matches the cross-language result and source-record vectors", async () => {
    await expect(resultId("kim-em", "Example Model", "two_plus_two", 1)).resolves.toBe(
      "r2_80f02f892fb0b90474675aa0b572252a8758faf74b95400521e9da724583931f",
    );
    await expect(sourceRecordId({
      ...VERIFIED.baseResult,
      results_commit: "a".repeat(40),
      results_path: "results/alice.json",
      canonical_record_sha256: "b".repeat(64),
    })).resolves.toBe(
      "src1_34ef08a904550548d360cc62407a77a7e5e8dfe9184c8d472e4f4266ffc3f826",
    );
  });

  it("uses RFC 8785 ordering and rejects unpaired Unicode surrogates", () => {
    expect(canonicalJson({ z: "β", a: [1, true] })).toBe('{"a":[1,true],"z":"β"}');
    expect(() => canonicalJson({ value: "\ud800" })).toThrow(/surrogate/u);
  });

  it("freezes the language-neutral RFC 8785 floating-point vector", async () => {
    expect(canonicalizationVector.schema_version).toBe(1);
    expect(canonicalJson(canonicalizationVector.value)).toBe(
      canonicalizationVector.canonical_json,
    );
    await expect(sha256Hex(canonicalizationVector.canonical_json)).resolves.toBe(
      canonicalizationVector.sha256,
    );
  });

  it("builds field-provenanced replacement overlays without changing the base", () => {
    const claimed = claimedOverlay(VERIFIED, EVENT_ID, "2026-08-24T08:00:00.000Z");
    const mutation = "0198abcd-0000-7000-8000-000000000002";
    const backfilled = backfilledOverlay(
      claimed,
      mutation,
      "2026-08-24T08:01:00.000Z",
      { web_access: false, credit_identity: "Alice" },
    );
    expect(backfilled.base_result).toEqual(claimed.base_result);
    expect(backfilled.mutation_event_id).toBe(mutation);
    expect(backfilled.metadata.web_access).toEqual({
      value: false,
      provenance: "backfilled",
      event_id: mutation,
      recorded_at: "2026-08-24T08:01:00.000Z",
    });
    expect(metadataAlreadyEqual(backfilled, { web_access: false })).toBe(true);
    expect(metadataAlreadyEqual(backfilled, { web_access: true })).toBe(false);
  });

  it("matches the State materializers for initial amendment and release-status views", () => {
    const amendment = initialResultAmendmentView({
      resultId: VERIFIED.resultId,
      ownerLogin: VERIFIED.ownerLogin,
      declaredModel: VERIFIED.baseResult.declared_model,
      problemId: VERIFIED.baseResult.problem_id,
      statementRevision: VERIFIED.baseResult.statement_revision,
      authorityEventId: EVENT_ID,
    });
    expect(resultAmendmentPath(VERIFIED.resultId)).toBe(
      `views/result-amendments/11/${VERIFIED.resultId}.json`,
    );
    expect(amendment).toEqual({
      schema_version: 1,
      result_id: VERIFIED.resultId,
      owner_login: "alice",
      declared_model: "Example Model",
      authority_event_id: EVENT_ID,
      base_problem_id: "two_plus_two",
      base_statement_revision: 1,
      effective_problem_id: "two_plus_two",
      effective_statement_revision: 1,
      mutation_event_id: EVENT_ID,
      problem_repair: null,
      applied_problem_repair: null,
      retraction: null,
      leaderboard_eligible: true,
    });
    expect(resultReleaseStatusPath(VERIFIED.resultId)).toBe(
      `views/result-release-status/11/${VERIFIED.resultId}.json`,
    );
    expect(initialResultReleaseStatusView(VERIFIED.resultId, EVENT_ID)).toEqual({
      schema_version: 1,
      result_id: VERIFIED.resultId,
      authority_event_id: EVENT_ID,
      status: "not_scheduled",
      release_event_id: null,
    });
    const schedule = "0198abcd-0000-7000-8000-000000000002";
    expect(initialResultReleaseStatusView(VERIFIED.resultId, EVENT_ID, schedule)).toEqual({
      schema_version: 1,
      result_id: VERIFIED.resultId,
      authority_event_id: EVENT_ID,
      status: "scheduled",
      release_event_id: schedule,
    });
  });

  it("rejects non-initial amendment views and incoherent release markers", () => {
    const amendment = initialResultAmendmentView({
      resultId: VERIFIED.resultId,
      ownerLogin: VERIFIED.ownerLogin,
      declaredModel: VERIFIED.baseResult.declared_model,
      problemId: VERIFIED.baseResult.problem_id,
      statementRevision: VERIFIED.baseResult.statement_revision,
      authorityEventId: EVENT_ID,
    });
    expect(() => decodeInitialResultAmendmentView({
      ...amendment,
      mutation_event_id: "0198abcd-0000-7000-8000-000000000002",
    })).toThrow(/initial result amendment view values/u);
    expect(() => decodeResultReleaseStatusView({
      ...initialResultReleaseStatusView(VERIFIED.resultId, EVENT_ID),
      status: "published",
    })).toThrow(/release-status view values/u);
  });

  it("rejects unknown, forged, and cross-bound operational documents", () => {
    expect(() => decodeResultIdentityGuard({
      schema_version: 1,
      result_id: VERIFIED.resultId,
      record_kind: "claimed",
      authority_event_id: EVENT_ID,
      forged: true,
    })).toThrow(/fields/u);
    expect(() => decodeResultOverlay({
      ...claimedOverlay(VERIFIED, EVENT_ID, "2026-08-24T08:00:00.000Z"),
      owner_login: "Alice",
    })).toThrow(/values/u);
    expect(() => decodeSourceRecordIndex({
      schema_version: 1,
      source_record_id: `src1_${"2".repeat(64)}`,
      result_id: VERIFIED.resultId,
      owner_login: "alice",
      claim_event_id: EVENT_ID,
      results_repository: "another/repository",
      results_commit: "a".repeat(40),
      results_path: "results/alice.json",
      canonical_record_sha256: "b".repeat(64),
    })).toThrow(/source binding/u);
  });

  it("fetches one exact owner-derived record and binds its full canonical digest", async () => {
    const identifier = await resultId("alice", "Example Model", "two_plus_two", 1);
    const record = {
      result_id: identifier,
      problem_id: "two_plus_two",
      statement_revision: 1,
      declared_model: "Example Model",
      accepted_at: "2024-01-02T03:04:05Z",
      benchmark_commit: "c".repeat(40),
      intake: { kind: "issue", issue_number: 42 },
      submission: {
        kind: "github_repo",
        repo: "alice/proof",
        ref: "d".repeat(40),
        public: false,
      },
      production_metadata: {
        solution_publication_status: "planned",
        solution_publication_date: "2026-10-01",
      },
    };
    const provider = new GitHubProvider(
      undefined,
      undefined,
      undefined,
      undefined,
      reachableResultFetcher(() => resultContents({ schema_version: 2, user: "Alice", results: [record] })),
      "main",
    );
    const verified = await provider.verifyLegacyResult("alice", "e".repeat(40), identifier);
    expect(verified).toEqual({
      resultId: identifier,
      ownerLogin: "alice",
      baseResult: {
        declared_model: "Example Model",
        problem_id: "two_plus_two",
        statement_revision: 1,
        results_repository: "leanprover/lean-eval-submissions",
        results_commit: "e".repeat(40),
        results_path: "results/alice.json",
        canonical_record_sha256: await sha256Hex(canonicalJson(record)),
      },
    });
    expect(JSON.stringify(verified)).not.toContain("alice/proof");
    expect(JSON.stringify(verified)).not.toContain("solution_publication_status");
  });

  it("requires merge-base ancestry to the exact protected environment branch", async () => {
    const commit = "e".repeat(40);
    const calls: string[] = [];
    const resultFetcher = (input: RequestInfo | URL): Promise<Response> => {
      const url = input instanceof Request ? input.url : input.toString();
      calls.push(url);
      if (url.endsWith("/branches/staging-results")) {
        return Promise.resolve(Response.json({
          name: "staging-results",
          protected: true,
          commit: { sha: "f".repeat(40) },
        }));
      }
      if (url.endsWith(`/compare/${commit}...staging-results`)) {
        return Promise.resolve(Response.json({
          status: "diverged",
          base_commit: { sha: commit },
          merge_base_commit: { sha: "d".repeat(40) },
          head_commit: { sha: "f".repeat(40) },
        }));
      }
      throw new Error("unreachable content read");
    };
    const provider = new GitHubProvider(
      undefined,
      undefined,
      undefined,
      undefined,
      resultFetcher,
      "staging-results",
    );
    await expect(provider.verifyLegacyResult("alice", commit, `r2_${"1".repeat(64)}`))
      .rejects.toThrow(/not an ancestor/u);
    expect(calls).toHaveLength(2);
  });

  it("reports a protected Results head race as retryable provider unavailability", async () => {
    const commit = "e".repeat(40);
    const resultFetcher = (input: RequestInfo | URL): Promise<Response> => {
      const url = input instanceof Request ? input.url : input.toString();
      if (url.endsWith("/branches/staging-results")) {
        return Promise.resolve(Response.json({
          name: "staging-results",
          protected: true,
          commit: { sha: "f".repeat(40) },
        }));
      }
      if (url.endsWith(`/compare/${commit}...staging-results`)) {
        return Promise.resolve(Response.json({
          status: "ahead",
          base_commit: { sha: commit },
          merge_base_commit: { sha: commit },
          head_commit: { sha: "9".repeat(40) },
        }));
      }
      throw new Error("content must not be read after a branch-head race");
    };
    const provider = new GitHubProvider(
      undefined,
      undefined,
      undefined,
      undefined,
      resultFetcher,
      "staging-results",
    );
    await expect(provider.verifyLegacyResult("alice", commit, `r2_${"1".repeat(64)}`))
      .rejects.toMatchObject({ status: 503 });
  });

  it("maps raw surrogate content to a bounded proof failure", async () => {
    const identifier = `r2_${"1".repeat(64)}`;
    const record = {
      result_id: identifier,
      problem_id: "two_plus_two",
      statement_revision: 1,
      declared_model: "\ud800",
      accepted_at: "2024-01-02T03:04:05Z",
      benchmark_commit: "c".repeat(40),
      intake: { kind: "issue", issue_number: 42 },
      submission: { kind: "gist", repo: "alice/abcdef", ref: "d".repeat(40), public: true },
      production_metadata: { solution_publication_status: "private" },
    };
    const provider = new GitHubProvider(
      undefined,
      undefined,
      undefined,
      undefined,
      reachableResultFetcher(() => resultContents({ schema_version: 2, user: "alice", results: [record] })),
      "main",
    );
    await expect(provider.verifyLegacyResult("alice", "e".repeat(40), identifier)).rejects.toMatchObject({
      status: 409,
    });
  });

  it("rejects a forged tuple and an owner-mismatched Results envelope", async () => {
    const identifier = await resultId("alice", "Example Model", "two_plus_two", 1);
    const baseRecord = {
      result_id: identifier,
      problem_id: "two_plus_two",
      statement_revision: 1,
      declared_model: "Forged Model",
      accepted_at: "2024-01-02T03:04:05Z",
      benchmark_commit: "c".repeat(40),
      intake: { kind: "issue", issue_number: 42 },
      submission: { kind: "gist", repo: "alice/abcdef", ref: "d".repeat(40), public: true },
      production_metadata: { solution_publication_status: "published", solution_publication_date: "2024-02-01" },
    };
    const forged = new GitHubProvider(undefined, undefined, undefined, undefined, reachableResultFetcher(() =>
      resultContents({ schema_version: 2, user: "alice", results: [baseRecord] })), "main");
    await expect(forged.verifyLegacyResult("alice", "e".repeat(40), identifier))
      .rejects.toThrow(/identity/u);

    const wrongOwner = new GitHubProvider(undefined, undefined, undefined, undefined, reachableResultFetcher(() =>
      resultContents({ schema_version: 2, user: "mallory", results: [baseRecord] })), "main");
    await expect(wrongOwner.verifyLegacyResult("alice", "e".repeat(40), identifier))
      .rejects.toThrow(/owner/u);
  });

  it("rejects normalized calendar dates and duplicate immutable source tuples", async () => {
    const identifier = await resultId("alice", "Example Model", "two_plus_two", 1);
    const record = {
      result_id: identifier,
      problem_id: "two_plus_two",
      statement_revision: 1,
      declared_model: "Example Model",
      accepted_at: "2024-01-02T03:04:05Z",
      benchmark_commit: "c".repeat(40),
      intake: { kind: "issue", issue_number: 42 },
      submission: { kind: "github_repo", repo: "alice/proof", ref: "d".repeat(40), public: true },
      production_metadata: { solution_publication_status: "published", solution_publication_date: "2024-02-01" },
    };
    const invalidTimestamp = new GitHubProvider(undefined, undefined, undefined, undefined, reachableResultFetcher(() =>
      resultContents({
        schema_version: 2,
        user: "alice",
        results: [{ ...record, accepted_at: "2024-02-30T03:04:05Z" }],
      })), "main");
    await expect(invalidTimestamp.verifyLegacyResult("alice", "e".repeat(40), identifier))
      .rejects.toThrow(/acceptance/u);

    const invalidPublication = new GitHubProvider(undefined, undefined, undefined, undefined, reachableResultFetcher(() =>
      resultContents({
        schema_version: 2,
        user: "alice",
        results: [{
          ...record,
          production_metadata: {
            solution_publication_status: "published",
            solution_publication_date: "2024-02-30",
          },
        }],
      })), "main");
    await expect(invalidPublication.verifyLegacyResult("alice", "e".repeat(40), identifier))
      .rejects.toThrow(/publication date/u);

    const duplicateTuple = new GitHubProvider(undefined, undefined, undefined, undefined, reachableResultFetcher(() =>
      resultContents({
        schema_version: 2,
        user: "alice",
        results: [record, { ...record, result_id: `r2_${"f".repeat(64)}` }],
      })), "main");
    await expect(duplicateTuple.verifyLegacyResult("alice", "e".repeat(40), identifier))
      .rejects.toThrow(/unique immutable identity tuple/u);
  });

  it("bounds grandfathered production metadata before canonicalization", async () => {
    const identifier = await resultId("alice", "Example Model", "two_plus_two", 1);
    const record = {
      result_id: identifier,
      problem_id: "two_plus_two",
      statement_revision: 1,
      declared_model: "Example Model",
      accepted_at: "2024-01-02T03:04:05Z",
      benchmark_commit: "c".repeat(40),
      intake: { kind: "issue", issue_number: 42 },
      submission: {
        kind: "github_repo",
        repo: "alice/proof",
        ref: "d".repeat(40),
        public: false,
      },
      production_metadata: {
        solution_publication_status: "private",
        grandfathered: "x".repeat(16 * 1024 + 1),
      },
    };
    const provider = new GitHubProvider(
      undefined,
      undefined,
      undefined,
      undefined,
      reachableResultFetcher(() => resultContents({
        schema_version: 2,
        user: "alice",
        results: [record],
      })),
      "main",
    );
    await expect(provider.verifyLegacyResult("alice", "e".repeat(40), identifier))
      .rejects.toThrow(/byte bound/u);
  });
});
