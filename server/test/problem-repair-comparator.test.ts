import { describe, expect, it, vi } from "vitest";

import { GitHubProvider } from "../src/github-provider";
import { resultId } from "../src/result-owner";

const OWNER = "kim-em";
const RESULTS_COMMIT = "a".repeat(40);
const PROTECTED_HEAD = "b".repeat(40);
const BENCHMARK_COMMIT = "e".repeat(40);

function encodedContents(path: string, bytes: Uint8Array, sha?: string): Response {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return Response.json({
    type: "file",
    path,
    encoding: "base64",
    content: btoa(binary),
    size: bytes.byteLength,
    ...(sha === undefined ? {} : { sha }),
  });
}

async function blobOid(bytes: Uint8Array): Promise<string> {
  const header = new TextEncoder().encode(`blob ${String(bytes.byteLength)}\0`);
  const material = new Uint8Array(header.byteLength + bytes.byteLength);
  material.set(header);
  material.set(bytes, header.byteLength);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-1", material));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function manifest(problemId: string, group: string, revision: number): Uint8Array {
  return new TextEncoder().encode([
    `id = "${problemId}"`,
    `title = "${problemId}"`,
    `group = "${group}"`,
    "status = \"active\"",
    `statement_revision = ${String(revision)}`,
    "",
  ].join("\n"));
}

async function fixture(
  group = "formalization-evaluation",
  correctedGroup = group,
  options: Readonly<{
    duplicateRecord?: boolean;
    tamperBlobOid?: boolean;
    correctedRevision?: number;
    benchmarkStatus?: string;
    benchmarkCompareHead?: string;
    benchmarkBranchHead?: string;
    benchmarkAheadBy?: number;
    benchmarkTotalCommits?: number;
    benchmarkCommits?: readonly string[];
    benchmarkProtected?: boolean;
  }> = {},
): Promise<{
  provider: GitHubProvider;
  identifier: string;
  resultFetch: ReturnType<typeof vi.fn>;
  benchmarkFetch: ReturnType<typeof vi.fn>;
}> {
  const identifier = await resultId(OWNER, "Example Model", "two_plus_two", 1);
  const record = {
    accepted_at: "2026-08-20T06:07:08Z",
    benchmark_commit: BENCHMARK_COMMIT,
    declared_model: "Example Model",
    intake: { kind: "issue", issue_number: 1 },
    problem_id: "two_plus_two",
    production_metadata: {},
    result_id: identifier,
    statement_revision: 1,
    submission: { kind: "github_repo", public: true, ref: "d".repeat(40), repo: "kim-em/proofs" },
  };
  const resultBytes = new TextEncoder().encode(JSON.stringify({
    schema_version: 2,
    user: OWNER,
    results: options.duplicateRecord === true ? [record, record] : [record],
  }));
  const oid = await blobOid(resultBytes);
  const resultFetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = input instanceof Request ? input.url : input.toString();
    expect(new Headers(init?.headers).get("x-lean-eval-expected-commit")).toBe(RESULTS_COMMIT);
    expect(init?.redirect).toBe("manual");
    if (url.endsWith("/branches/main")) {
      return Promise.resolve(Response.json({ name: "main", protected: true, commit: { sha: PROTECTED_HEAD } }));
    }
    if (url.endsWith(`/compare/${RESULTS_COMMIT}...main`)) {
      return Promise.resolve(Response.json({
        status: "ahead",
        base_commit: { sha: RESULTS_COMMIT },
        merge_base_commit: { sha: RESULTS_COMMIT },
        ahead_by: 1,
        behind_by: 0,
        total_commits: 1,
        commits: [{ sha: PROTECTED_HEAD }],
      }));
    }
    if (url.includes(`/contents/results/${OWNER}.json?ref=${RESULTS_COMMIT}`)) {
      return Promise.resolve(encodedContents(
        `results/${OWNER}.json`,
        resultBytes,
        options.tamperBlobOid === true ? "f".repeat(40) : oid,
      ));
    }
    throw new Error(`unexpected result verification request: ${url}`);
  });
  const benchmarkStatus = options.benchmarkStatus ?? "ahead";
  const benchmarkBranchHead = options.benchmarkBranchHead ?? PROTECTED_HEAD;
  const benchmarkCommits = options.benchmarkCommits ?? (
    benchmarkStatus === "identical"
      ? []
      : [options.benchmarkCompareHead ?? PROTECTED_HEAD]
  );
  const benchmarkAheadBy = options.benchmarkAheadBy ?? (
    benchmarkStatus === "identical" ? 0 : benchmarkCommits.length
  );
  const benchmarkTotalCommits = options.benchmarkTotalCommits ?? benchmarkAheadBy;
  const benchmarkFetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = input instanceof Request ? input.url : input.toString();
    expect(init?.redirect).toBe("manual");
    expect(new Headers(init?.headers).get("x-lean-eval-expected-commit")).toBe(BENCHMARK_COMMIT);
    if (url.endsWith("/branches/main")) {
      return Promise.resolve(Response.json({
        name: "main",
        protected: options.benchmarkProtected ?? true,
        commit: { sha: benchmarkBranchHead },
      }));
    }
    if (url.endsWith(`/compare/${BENCHMARK_COMMIT}...main`)) {
      return Promise.resolve(Response.json({
        status: benchmarkStatus,
        base_commit: { sha: BENCHMARK_COMMIT },
        merge_base_commit: { sha: BENCHMARK_COMMIT },
        ahead_by: benchmarkAheadBy,
        behind_by: 0,
        total_commits: benchmarkTotalCommits,
        commits: benchmarkCommits.map((sha) => ({ sha })),
      }));
    }
    const path = url.includes("two_plus_two")
      ? "manifests/problems/two_plus_two.toml"
      : "manifests/problems/three_plus_three.toml";
    const problemId = path.includes("two_plus_two") ? "two_plus_two" : "three_plus_three";
    const revision = problemId === "two_plus_two" ? 1 : (options.correctedRevision ?? 2);
    return Promise.resolve(encodedContents(
      path,
      manifest(problemId, problemId === "two_plus_two" ? group : correctedGroup, revision),
    ));
  });
  return {
    provider: new GitHubProvider(
      undefined,
      undefined,
      undefined,
      undefined,
      resultFetch,
      "main",
      benchmarkFetch,
    ),
    identifier,
    resultFetch,
    benchmarkFetch,
  };
}

describe("problem repair comparator verification", () => {
  it("binds the exact protected Results blob, unique record, and benchmark manifests", async () => {
    const { provider, identifier, resultFetch, benchmarkFetch } = await fixture();
    const evidence = await provider.verifyProblemRepairComparator({
      resultsCommit: RESULTS_COMMIT,
      resultId: identifier,
      ownerLogin: OWNER,
      declaredModel: "Example Model",
      baseProblemId: "two_plus_two",
      baseStatementRevision: 1,
      correctedProblemId: "three_plus_three",
      correctedStatementRevision: 2,
    });
    expect(evidence).toMatchObject({
      repository: "leanprover/lean-eval-submissions",
      commit: RESULTS_COMMIT,
      path: `results/${OWNER}.json`,
      verification_method: "github_commit_blob_v1",
      evidence_result_id: identifier,
      evidence_owner_login: OWNER,
      evidence_declared_model: "Example Model",
      evidence_base_problem_group: "formalization-evaluation",
      evidence_base_problem_id: "two_plus_two",
      evidence_base_statement_revision: 1,
      evidence_base_challenge_id: "ch1_6b96093e822f811a31d09ed4d35b44f3135e5170cf1ea84a59f87eb09aa20cf7",
      evidence_corrected_problem_group: "formalization-evaluation",
      evidence_corrected_problem_id: "three_plus_three",
      evidence_corrected_statement_revision: 2,
      evidence_corrected_challenge_id: "ch1_2ee792b9940091b30b893826d3d60cd36378bbd2aabd5743a18ab2bc3d46c5fb",
    });
    expect(evidence.blob_oid).toMatch(/^[0-9a-f]{40}$/u);
    expect(evidence.blob_sha256).toMatch(/^[0-9a-f]{64}$/u);
    expect(evidence.record_sha256).toMatch(/^[0-9a-f]{64}$/u);
    expect(evidence.binding_sha256).toMatch(/^[0-9a-f]{64}$/u);
    expect(resultFetch).toHaveBeenCalledTimes(4);
    expect(benchmarkFetch).toHaveBeenCalledTimes(4);
  });

  it("accepts the live identical benchmark shape by inferring the base as its terminal head", async () => {
    const { provider, identifier } = await fixture(
      "formalization-evaluation",
      "formalization-evaluation",
      {
        benchmarkStatus: "identical",
        benchmarkBranchHead: BENCHMARK_COMMIT,
      },
    );
    await expect(provider.verifyProblemRepairComparator({
      resultsCommit: RESULTS_COMMIT,
      resultId: identifier,
      ownerLogin: OWNER,
      declaredModel: "Example Model",
      baseProblemId: "two_plus_two",
      baseStatementRevision: 1,
      correctedProblemId: "three_plus_three",
      correctedStatementRevision: 2,
    })).resolves.toMatchObject({ evidence_result_id: identifier });
  });

  it("rejects empty, count-incoherent, truncated, or branch-mismatched benchmark comparisons", async () => {
    for (const options of [
      { benchmarkCommits: [] },
      {
        benchmarkAheadBy: 2,
        benchmarkTotalCommits: 1,
        benchmarkCommits: [PROTECTED_HEAD],
      },
      {
        benchmarkAheadBy: 251,
        benchmarkTotalCommits: 251,
        benchmarkCommits: [PROTECTED_HEAD],
      },
      { benchmarkCommits: ["c".repeat(40)] },
      {
        benchmarkStatus: "identical",
        benchmarkBranchHead: PROTECTED_HEAD,
      },
    ]) {
      const { provider, identifier } = await fixture(
        "formalization-evaluation",
        "formalization-evaluation",
        options,
      );
      await expect(provider.verifyProblemRepairComparator({
        resultsCommit: RESULTS_COMMIT,
        resultId: identifier,
        ownerLogin: OWNER,
        declaredModel: "Example Model",
        baseProblemId: "two_plus_two",
        baseStatementRevision: 1,
        correctedProblemId: "three_plus_three",
        correctedStatementRevision: 2,
      })).rejects.toBeInstanceOf(Error);
    }
  });

  it("rejects a cross-group correction before producing evidence", async () => {
    const { provider, identifier } = await fixture(
      "formalization-evaluation",
      "software-verification",
    );
    await expect(provider.verifyProblemRepairComparator({
      resultsCommit: RESULTS_COMMIT,
      resultId: identifier,
      ownerLogin: OWNER,
      declaredModel: "Example Model",
      baseProblemId: "two_plus_two",
      baseStatementRevision: 1,
      correctedProblemId: "three_plus_three",
      correctedStatementRevision: 2,
    })).rejects.toThrow(/cannot change the benchmark group/u);
  });

  it("rejects an unprotected, unmerged, or moving benchmark commit proof", async () => {
    for (const options of [
      { benchmarkProtected: false },
      { benchmarkStatus: "diverged" },
      { benchmarkCompareHead: "c".repeat(40) },
    ]) {
      const { provider, identifier } = await fixture(
        "formalization-evaluation",
        "formalization-evaluation",
        options,
      );
      await expect(provider.verifyProblemRepairComparator({
        resultsCommit: RESULTS_COMMIT,
        resultId: identifier,
        ownerLogin: OWNER,
        declaredModel: "Example Model",
        baseProblemId: "two_plus_two",
        baseStatementRevision: 1,
        correctedProblemId: "three_plus_three",
        correctedStatementRevision: 2,
      })).rejects.toBeInstanceOf(Error);
    }
  });

  it("rejects duplicate comparator records and a blob OID that disagrees with bytes", async () => {
    for (const options of [{ duplicateRecord: true }, { tamperBlobOid: true }]) {
      const { provider, identifier } = await fixture(
        "formalization-evaluation",
        "formalization-evaluation",
        options,
      );
      await expect(provider.verifyProblemRepairComparator({
        resultsCommit: RESULTS_COMMIT,
        resultId: identifier,
        ownerLogin: OWNER,
        declaredModel: "Example Model",
        baseProblemId: "two_plus_two",
        baseStatementRevision: 1,
        correctedProblemId: "three_plus_three",
        correctedStatementRevision: 2,
      })).rejects.toThrow(/exactly one requested identity|blob OID/u);
    }
  });

  it("rejects benchmark manifest revision drift", async () => {
    const { provider, identifier } = await fixture(
      "formalization-evaluation",
      "formalization-evaluation",
      { correctedRevision: 3 },
    );
    await expect(provider.verifyProblemRepairComparator({
      resultsCommit: RESULTS_COMMIT,
      resultId: identifier,
      ownerLogin: OWNER,
      declaredModel: "Example Model",
      baseProblemId: "two_plus_two",
      baseStatementRevision: 1,
      correctedProblemId: "three_plus_three",
      correctedStatementRevision: 2,
    })).rejects.toThrow(/manifest did not bind/u);
  });
});
