import { describe, expect, it } from "vitest";

import { GitHubProvider } from "../src/github-provider";
import {
  backfilledOverlay,
  canonicalJson,
  claimedOverlay,
  decodeResultIdentityGuard,
  decodeResultOverlay,
  decodeSourceRecordIndex,
  metadataAlreadyEqual,
  resultId,
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
    const resultFetcher = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = input instanceof Request
        ? input.url
        : typeof input === "string"
          ? input
          : input.toString();
      expect(url).toBe(
        `https://api.github.com/repos/leanprover/lean-eval-submissions/contents/results/alice.json?ref=${"e".repeat(40)}`,
      );
      expect(new Headers(init?.headers).get("x-lean-eval-expected-commit")).toBe("e".repeat(40));
      return Promise.resolve(resultContents({ schema_version: 2, user: "Alice", results: [record] }));
    };
    const provider = new GitHubProvider(undefined, undefined, undefined, undefined, resultFetcher);
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
    const forged = new GitHubProvider(undefined, undefined, undefined, undefined, () =>
      Promise.resolve(resultContents({ schema_version: 2, user: "alice", results: [baseRecord] })));
    await expect(forged.verifyLegacyResult("alice", "e".repeat(40), identifier))
      .rejects.toThrow(/identity/u);

    const wrongOwner = new GitHubProvider(undefined, undefined, undefined, undefined, () =>
      Promise.resolve(resultContents({ schema_version: 2, user: "mallory", results: [baseRecord] })));
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
    const invalidTimestamp = new GitHubProvider(undefined, undefined, undefined, undefined, () =>
      Promise.resolve(resultContents({
        schema_version: 2,
        user: "alice",
        results: [{ ...record, accepted_at: "2024-02-30T03:04:05Z" }],
      })));
    await expect(invalidTimestamp.verifyLegacyResult("alice", "e".repeat(40), identifier))
      .rejects.toThrow(/acceptance/u);

    const invalidPublication = new GitHubProvider(undefined, undefined, undefined, undefined, () =>
      Promise.resolve(resultContents({
        schema_version: 2,
        user: "alice",
        results: [{
          ...record,
          production_metadata: {
            solution_publication_status: "published",
            solution_publication_date: "2024-02-30",
          },
        }],
      })));
    await expect(invalidPublication.verifyLegacyResult("alice", "e".repeat(40), identifier))
      .rejects.toThrow(/publication date/u);

    const duplicateTuple = new GitHubProvider(undefined, undefined, undefined, undefined, () =>
      Promise.resolve(resultContents({
        schema_version: 2,
        user: "alice",
        results: [record, { ...record, result_id: `r2_${"f".repeat(64)}` }],
      })));
    await expect(duplicateTuple.verifyLegacyResult("alice", "e".repeat(40), identifier))
      .rejects.toThrow(/unique immutable identity tuple/u);
  });
});
