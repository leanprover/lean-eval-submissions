import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  canonicalStateDocument,
  clearResultOwnerContractProofCacheForTest,
  type GitHubFetch,
  GitHubStateError,
  GitHubStateRepository,
  ResultIdentityCollisionError,
  StateEventConflictError,
  StateUpdateOutcomeUnknownError,
} from "../src/github-state";
import type {
  StateEvent,
  WritableResultLifecycleEvent,
  WritableSubmissionLifecycleEvent,
  WritableStateEvent,
} from "../src/state-event";
import type { SubmissionView } from "../src/submission-view";
import {
  backfilledOverlay,
  claimedGuard,
  claimedOverlay,
  claimedSourceIndex,
  initialResultReleaseStatusView,
  recordedGuard,
  type VerifiedLegacyResult,
} from "../src/result-owner";
import {
  decodeResultAmendmentView,
  initialResultAmendmentView,
  requestedProblemRepairView,
  requestedRetractionView,
} from "../src/result-amendment";

const HEAD = "1".repeat(40);
const TREE = "2".repeat(40);
const NEW_TREE = "3".repeat(40);
const NEW_COMMIT = "4".repeat(40);
const RESULT_OWNER_CONTRACT_COMMIT = "163e9314c881493e08d23baf35ff40456f9c2331";
const RESULT_OWNER_CONTRACT_BLOBS = {
  "docs/result-amendment-lifecycle.md": "6ef59628f12820a4af64ff9bff4fb174d1749684",
  "docs/result-owner-operational-indexes.md": "2f784609f9117caf74cb7042e9ea45732925d77b",
  "schema/result-amendment-view-v1.schema.json": "20282df2b419466f32998b93b49c55b107ed6f35",
  "schema/result-amendments-v1.schema.json": "440d5039d1cef4bb055579b94cca928d36f66c96",
  "schema/result-identity-guard-v1.schema.json": "1620b6d8aed37f652958ac86e311c00578edc8b4",
  "schema/result-overlay-view-v1.schema.json": "1b50a92a76891bd21e0b67f7f40ab9c86d50beed",
  "schema/result-overlays-v1.schema.json": "41d4078133d6854bf8de839873a3f58e9ba1afd1",
  "schema/result-release-status-view-v1.schema.json": "7f115230736e5d45074e8172f6fe4e5ee1992021",
  "schema/result-source-record-index-v1.schema.json": "4543225e0833af00913e436185532a769debebc1",
  "schema/state-event-v1.schema.json": "5b670204c86c440b56afd81f62bd097e3b399be7",
  "scripts/materialize_state.py": "f7985b70b6409616ac2020a2be2337eca13c640d",
  "scripts/result_amendments.py": "61b44743c73d152fa92c489ac9228d16f0b694fd",
  "scripts/result_owner_indexes.py": "c07c29a81eb2ca5058563a8411c26f9358bde3e4",
  "scripts/result_release_status.py": "27bae3e6faa9275463a1440483512e23bfda2f6e",
  "scripts/validate_state.py": "0b4c876475fcc9c9d5cf6269c800509530673bb4",
} as const;

const EVENT: StateEvent = {
  schema_version: 1,
  event_id: "0198abcd-0000-7000-8000-000000000001",
  event_type: "system.initialized",
  occurred_at: "2026-08-20T06:07:08.000Z",
  subject_id: "state_staging",
  causation_event_id: null,
  actor: { kind: "system" },
  payload: { environment: "staging" },
};
const CANARY_EVIDENCE: StateEvent = {
  schema_version: 1,
  event_id: "0198abcd-0000-7000-8000-000000000002",
  event_type: "authentication.nonce_consumed",
  occurred_at: "2026-08-20T06:07:08.001Z",
  subject_id: "0198abcd-0000-7000-8000-000000000002",
  causation_event_id: null,
  actor: { kind: "system" },
  payload: {
    nonce_digest: "a".repeat(64),
    purpose: "submission",
    expires_at: "2026-08-20T06:17:08.000Z",
  },
};

const SUBMISSION_ID = "0198abcd-1111-7000-8000-000000000001";
const METADATA_ID = "0198abcd-1111-7000-8000-000000000002";
const RECEIVED: StateEvent = {
  schema_version: 1,
  event_id: SUBMISSION_ID,
  event_type: "submission.received",
  occurred_at: "2026-08-20T06:07:08.000Z",
  subject_id: SUBMISSION_ID,
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
};
const METADATA: StateEvent = {
  schema_version: 1,
  event_id: METADATA_ID,
  event_type: "submission.metadata_amended",
  occurred_at: "2026-08-20T06:07:08.001Z",
  subject_id: SUBMISSION_ID,
  causation_event_id: SUBMISSION_ID,
  actor: { kind: "github", login: "alice" },
  payload: { production_metadata: { web_access: false } },
};
const VIEW: SubmissionView = {
  schema_version: 1,
  submission_id: SUBMISSION_ID,
  owner_login: "alice",
  received_event_id: SUBMISSION_ID,
  mutation_event_id: METADATA_ID,
  metadata_event_id: METADATA_ID,
  publication_event_id: null,
  accepted_at: "2026-08-20T06:07:08.000Z",
  submission: {
    problem_id: "two_plus_two",
    problem_group: "formalization-evaluation",
    statement_revision: 2,
    declared_model: "Example Model",
    source_repository: "alice/proofs",
    source_commit: "a".repeat(40),
    source_visibility: "private",
    publication_choice: "scheduled",
    production_metadata: { web_access: false },
  },
  production_metadata: { web_access: false },
  publication_choice: "scheduled",
  archive: { status: "pending" },
  evaluation: { status: "pending" },
  result_id: null,
  dispatch: {
    status: "succeeded",
    attempts: 1,
    requested_at: "2026-08-20T06:07:08.000Z",
    updated_at: "2026-08-20T06:07:08.003Z",
    workflow_ref: `lean-eval-dispatch/${"b".repeat(40)}`,
    last_error_code: null,
  },
};
const ARCHIVE_EVENT: WritableSubmissionLifecycleEvent = {
  schema_version: 1,
  event_id: "0198abcd-1111-7000-8000-000000000003",
  event_type: "archive.completed",
  occurred_at: "2026-08-20T06:07:09.000Z",
  subject_id: SUBMISSION_ID,
  causation_event_id: SUBMISSION_ID,
  actor: { kind: "system" },
  payload: {
    archive_repository: "leanprover/lean-eval-audit",
    archive_commit: "c".repeat(40),
    archive_path: `archives/01/${SUBMISSION_ID}.tar.age`,
    archive_ciphertext_sha256: "d".repeat(64),
    encrypted: true,
  },
};
const VIEW_V2: SubmissionView = {
  ...VIEW,
  schema_version: 2,
  result_event_id: null,
  archive: {
    status: "completed",
    event_id: ARCHIVE_EVENT.event_id,
    occurred_at: ARCHIVE_EVENT.occurred_at,
    ...ARCHIVE_EVENT.payload,
  },
};
const EVALUATION_STARTED: WritableSubmissionLifecycleEvent = {
  schema_version: 1,
  event_id: "0198abcd-1111-7000-8000-000000000004",
  event_type: "evaluation.started",
  occurred_at: "2026-08-20T06:07:10.000Z",
  subject_id: SUBMISSION_ID,
  causation_event_id: ARCHIVE_EVENT.event_id,
  actor: { kind: "system" },
  payload: {
    attempt: 1,
    benchmark_repository: "leanprover/lean-eval",
    benchmark_commit: "e".repeat(40),
    toolchain: "leanprover/lean4:v4.32.0",
  },
};
const EVALUATION_ACCEPTED: WritableSubmissionLifecycleEvent = {
  schema_version: 1,
  event_id: "0198abcd-1111-7000-8000-000000000005",
  event_type: "evaluation.accepted",
  occurred_at: "2026-08-20T06:07:10.001Z",
  subject_id: SUBMISSION_ID,
  causation_event_id: EVALUATION_STARTED.event_id,
  actor: { kind: "system" },
  payload: { attempt: 1, evaluator_version: "f".repeat(40) },
};
const ACCEPTED_VIEW: SubmissionView = {
  ...VIEW_V2,
  evaluation: {
    status: "accepted",
    event_id: EVALUATION_ACCEPTED.event_id,
    occurred_at: EVALUATION_ACCEPTED.occurred_at,
    ...EVALUATION_STARTED.payload,
    ...EVALUATION_ACCEPTED.payload,
  },
};
const RESULT_ID = `r2_${"a".repeat(64)}`;
const RESULT_EVENT: WritableResultLifecycleEvent = {
  schema_version: 1,
  event_id: "0198abcd-1111-7000-8000-000000000006",
  event_type: "result.recorded",
  occurred_at: "2026-08-20T06:07:11.000Z",
  subject_id: RESULT_ID,
  causation_event_id: EVALUATION_ACCEPTED.event_id,
  actor: { kind: "system" },
  payload: {
    submission_id: SUBMISSION_ID,
    problem_id: "two_plus_two",
    statement_revision: 2,
    result_commit: "9".repeat(40),
    tree_digest: "8".repeat(64),
  },
};
const RELEASE_EVENT: WritableResultLifecycleEvent = {
  schema_version: 1,
  event_id: "0198abcd-1111-7000-8000-000000000007",
  event_type: "release.scheduled",
  occurred_at: "2026-08-20T06:07:11.001Z",
  subject_id: RESULT_ID,
  causation_event_id: RESULT_EVENT.event_id,
  actor: { kind: "system" },
  payload: { result_id: RESULT_ID, release_at: "2026-10-20T06:07:10.001Z" },
};
const RESULT_VIEW: SubmissionView = {
  ...ACCEPTED_VIEW,
  result_id: RESULT_ID,
  result_event_id: RESULT_EVENT.event_id,
};
const RESULT_AMENDMENT_VIEW = initialResultAmendmentView({
  resultId: RESULT_ID,
  ownerLogin: "alice",
  declaredModel: "Example Model",
  problemId: "two_plus_two",
  statementRevision: 2,
  authorityEventId: RESULT_EVENT.event_id,
  mutationEventId: RESULT_EVENT.event_id,
});
const RESULT_RELEASE_STATUS_VIEW = initialResultReleaseStatusView(
  RESULT_ID,
  RESULT_EVENT.event_id,
  RELEASE_EVENT.event_id,
);
const CLAIM_EVENT_ID = "0198abcd-2222-7000-8000-000000000001";
const BACKFILL_EVENT_ID = "0198abcd-2222-7000-8000-000000000002";
const LEGACY_RESULT: VerifiedLegacyResult = {
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
const LEGACY_AMENDMENT_VIEW = initialResultAmendmentView({
  resultId: LEGACY_RESULT.resultId,
  ownerLogin: LEGACY_RESULT.ownerLogin,
  declaredModel: LEGACY_RESULT.baseResult.declared_model,
  problemId: LEGACY_RESULT.baseResult.problem_id,
  statementRevision: LEGACY_RESULT.baseResult.statement_revision,
  authorityEventId: CLAIM_EVENT_ID,
  mutationEventId: CLAIM_EVENT_ID,
});
const LEGACY_RELEASE_STATUS_VIEW = initialResultReleaseStatusView(
  LEGACY_RESULT.resultId,
  CLAIM_EVENT_ID,
);

function legacyClaimEvent(occurredAt = "2026-08-24T08:00:00.000Z"): StateEvent {
  return {
    schema_version: 1,
    event_id: CLAIM_EVENT_ID,
    event_type: "result.claimed",
    occurred_at: occurredAt,
    subject_id: LEGACY_RESULT.resultId,
    causation_event_id: null,
    actor: { kind: "github", login: "alice" },
    payload: LEGACY_RESULT.baseResult,
  };
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

function contents(value: unknown): Response {
  return json({ encoding: "base64", content: btoa(`${JSON.stringify(value)}\n`) });
}

function resultOwnerContractProofResponses(
  changedPath?: keyof typeof RESULT_OWNER_CONTRACT_BLOBS,
): Response[] {
  return [
    json({
      status: "ahead",
      merge_base_commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
    }),
    ...Object.entries(RESULT_OWNER_CONTRACT_BLOBS).map(([path, sha]) =>
      json({ type: "file", path, sha: path === changedPath ? "0".repeat(40) : sha })),
  ];
}

function sequence(responses: readonly (Response | Error)[]) {
  const queue = [...responses];
  return vi.fn<GitHubFetch>(() => {
    const response = queue.shift();
    if (!response) throw new Error("unexpected GitHub request");
    if (response instanceof Error) return Promise.reject(response);
    return Promise.resolve(response);
  });
}

function repository(fetcher: GitHubFetch): GitHubStateRepository {
  return new GitHubStateRepository(
    { repository: "leanprover/state", token: "secret", userAgent: "test" },
    fetcher,
  );
}

function productionRepository(fetcher: GitHubFetch): GitHubStateRepository {
  return new GitHubStateRepository(
    { repository: "leanprover/lean-eval-state", token: "secret", userAgent: "test" },
    fetcher,
  );
}

function fetchUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof Request ? input.url : input.href;
}

describe("atomic Git State append", () => {
  beforeEach(() => clearResultOwnerContractProofCacheForTest());

  it("emits the exact Python-compatible canonical State document bytes", () => {
    expect(canonicalStateDocument({ z: "😀", a: { y: "β", x: true } })).toBe(
      "{\n  \"a\": {\n    \"x\": true,\n    \"y\": \"\\u03b2\"\n  },\n  \"z\": \"\\ud83d\\ude00\"\n}\n",
    );
  });

  it("gates result-owner writes on protected-main ancestry and exact reviewed blobs", async () => {
    const valid = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
    ]);
    await expect(repository(valid).assertResultOwnerContract()).resolves.toBe(HEAD);

    clearResultOwnerContractProofCacheForTest();
    const diverged = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      json({ status: "diverged", merge_base_commit: { sha: "0".repeat(40) } }),
    ]);
    await expect(repository(diverged).assertResultOwnerContract()).rejects.toMatchObject({ status: 503 });

    clearResultOwnerContractProofCacheForTest();
    const changed = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses("schema/result-identity-guard-v1.schema.json"),
    ]);
    await expect(repository(changed).assertResultOwnerContract()).rejects.toMatchObject({ status: 503 });
  });

  it("reuses content-addressed contract proofs across repository instances", async () => {
    const first = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
    ]);
    await expect(repository(first).assertResultOwnerContract()).resolves.toBe(HEAD);

    const second = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
    ]);
    await expect(repository(second).assertResultOwnerContract()).resolves.toBe(HEAD);
    expect(first).toHaveBeenCalledTimes(2 + 1 + Object.keys(RESULT_OWNER_CONTRACT_BLOBS).length);
    expect(second).toHaveBeenCalledTimes(2);
  });

  it("bounds the cross-request contract-proof cache and evicts the oldest key", async () => {
    const fetcher = vi.fn<GitHubFetch>((input) => {
      const url = new URL(input instanceof Request ? input.url : input.toString());
      if (url.pathname.endsWith("/git/ref/heads/main")) {
        return Promise.resolve(json({ object: { sha: HEAD } }));
      }
      if (url.pathname.endsWith(`/git/commits/${HEAD}`)) {
        return Promise.resolve(json({ tree: { sha: TREE } }));
      }
      if (url.pathname.endsWith(`/compare/${RESULT_OWNER_CONTRACT_COMMIT}...${HEAD}`)) {
        return Promise.resolve(json({
          status: "ahead",
          merge_base_commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
        }));
      }
      const marker = "/contents/";
      const index = url.pathname.indexOf(marker);
      const path = index < 0 ? "" : decodeURI(url.pathname.slice(index + marker.length));
      const sha = (RESULT_OWNER_CONTRACT_BLOBS as Readonly<Record<string, string>>)[path];
      if (sha !== undefined) return Promise.resolve(json({ type: "file", path, sha }));
      throw new Error(`unexpected contract proof request: ${url.toString()}`);
    });
    for (let index = 0; index < 65; index += 1) {
      const candidate = new GitHubStateRepository(
        { repository: `leanprover/state-${String(index)}`, token: "secret", userAgent: "test" },
        fetcher,
      );
      await candidate.assertResultOwnerContract();
    }
    await new GitHubStateRepository(
      { repository: "leanprover/state-0", token: "secret", userAgent: "test" },
      fetcher,
    ).assertResultOwnerContract();
    const comparisons = fetcher.mock.calls.filter(([input]) =>
      (input instanceof Request ? input.url : input.toString()).includes("/compare/"));
    expect(comparisons).toHaveLength(66);
  });

  it("proves write authority with a non-forced same-commit ref update", async () => {
    const fetcher = sequence([
      json({ permissions: { push: true } }),
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      json({ ref: "refs/heads/main", object: { sha: HEAD } }),
    ]);
    await expect(repository(fetcher).assertWritable()).resolves.toBe(HEAD);
    const [, init] = fetcher.mock.calls[3] ?? [];
    expect(init?.method).toBe("PATCH");
    if (typeof init?.body !== "string") throw new TypeError("write probe body must be JSON");
    expect(JSON.parse(init.body)).toEqual({ force: false, sha: HEAD });
  });

  it("binds production write authority to protected main and exact contract blobs", async () => {
    const fetcher = sequence([
      json({ permissions: { push: true } }),
      json({ object: { sha: RESULT_OWNER_CONTRACT_COMMIT } }),
      json({ tree: { sha: TREE } }),
      json({
        name: "main",
        protected: true,
        commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
      ...Object.entries(RESULT_OWNER_CONTRACT_BLOBS).map(([path, sha]) =>
        json({ type: "file", path, sha })),
      json({
        ref: "refs/heads/main",
        object: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
      json({
        name: "main",
        protected: true,
        commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
    ]);
    await expect(
      productionRepository(fetcher).assertProductionQualifiedWritable(),
    ).resolves.toBe(RESULT_OWNER_CONTRACT_COMMIT);
    const [, init] = fetcher.mock.calls.find(([, request]) => request?.method === "PATCH") ?? [];
    expect(init?.method).toBe("PATCH");
  });

  it("rejects protection or head drift after the production write probe", async () => {
    const fetcher = sequence([
      json({ permissions: { push: true } }),
      json({ object: { sha: RESULT_OWNER_CONTRACT_COMMIT } }),
      json({ tree: { sha: TREE } }),
      json({
        name: "main",
        protected: true,
        commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
      ...Object.entries(RESULT_OWNER_CONTRACT_BLOBS).map(([path, sha]) =>
        json({ type: "file", path, sha })),
      json({
        ref: "refs/heads/main",
        object: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
      json({
        name: "main",
        protected: false,
        commit: { sha: "f".repeat(40) },
      }),
    ]);
    await expect(
      productionRepository(fetcher).assertProductionQualifiedWritable(),
    ).rejects.toMatchObject({ status: 503 });
    expect(fetcher.mock.calls.some(([, request]) => request?.method === "PATCH")).toBe(true);
  });

  it("refuses unprotected, stale, or wrong-repository production proofs", async () => {
    const unprotected = sequence([
      json({ permissions: { push: true } }),
      json({ object: { sha: RESULT_OWNER_CONTRACT_COMMIT } }),
      json({ tree: { sha: TREE } }),
      json({
        name: "main",
        protected: false,
        commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
      ...Object.entries(RESULT_OWNER_CONTRACT_BLOBS).map(([path, sha]) =>
        json({ type: "file", path, sha })),
    ]);
    await expect(
      productionRepository(unprotected).assertProductionQualifiedWritable(),
    ).rejects.toMatchObject({ status: 503 });

    const stale = sequence([
      json({ permissions: { push: true } }),
      json({ object: { sha: RESULT_OWNER_CONTRACT_COMMIT } }),
      json({ tree: { sha: TREE } }),
      json({
        name: "main",
        protected: true,
        commit: { sha: "f".repeat(40) },
      }),
      ...Object.entries(RESULT_OWNER_CONTRACT_BLOBS).map(([path, sha]) =>
        json({ type: "file", path, sha })),
    ]);
    await expect(
      productionRepository(stale).assertProductionQualifiedWritable(),
    ).rejects.toMatchObject({ status: 503 });

    await expect(
      repository(sequence([])).assertProductionQualifiedWritable(),
    ).rejects.toMatchObject({ status: 503 });
  });

  it("proves real CAS contention before appending durable canary evidence through the retrying writer", async () => {
    const winnerCommit = "a".repeat(40);
    const contenderTree = "9".repeat(40);
    const contenderCommit = "b".repeat(40);
    const evidenceTree = "c".repeat(40);
    const evidenceCommit = "d".repeat(40);
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: winnerCommit }, 201),
      json({ sha: contenderTree }, 201),
      json({ sha: contenderCommit }, 201),
      json({ ref: "refs/heads/main", object: { sha: winnerCommit } }),
      json({ message: "not a fast forward" }, 422),
      json({ object: { sha: winnerCommit } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: evidenceTree }, 201),
      json({ sha: evidenceCommit }, 201),
      json({ object: { sha: evidenceCommit } }),
    ]);
    await expect(repository(fetcher).provePromotionCanaryContention(CANARY_EVIDENCE))
      .resolves.toEqual({
        proofRecorded: true,
        idempotent: false,
        commit: evidenceCommit,
        created: true,
      });
    const winnerUpdate = fetcher.mock.calls[6]?.[1];
    const contenderUpdate = fetcher.mock.calls[7]?.[1];
    const retryUpdate = fetcher.mock.calls[13]?.[1];
    expect(winnerUpdate?.method).toBe("PATCH");
    expect(contenderUpdate?.method).toBe("PATCH");
    expect(retryUpdate?.method).toBe("PATCH");
    if (
      typeof winnerUpdate?.body !== "string" ||
      typeof contenderUpdate?.body !== "string" ||
      typeof retryUpdate?.body !== "string"
    ) {
      throw new TypeError("canary ref update bodies must be JSON text");
    }
    expect(JSON.parse(winnerUpdate.body)).toEqual({ force: false, sha: winnerCommit });
    expect(JSON.parse(contenderUpdate.body)).toEqual({
      force: false,
      sha: contenderCommit,
    });
    expect(JSON.parse(retryUpdate.body)).toEqual({
      force: false,
      sha: evidenceCommit,
    });
    const winnerRequestBody = fetcher.mock.calls[3]?.[1]?.body;
    const contenderRequestBody = fetcher.mock.calls[5]?.[1]?.body;
    if (typeof winnerRequestBody !== "string") {
      throw new TypeError("canary winner commit body must be JSON text");
    }
    expect(JSON.parse(winnerRequestBody)).toEqual({
      message: `Promotion canary CAS winner ${CANARY_EVIDENCE.event_id}`,
      parents: [HEAD],
      tree: TREE,
    });
    const callUrls = fetcher.mock.calls.map((call) => {
      const input = call[0];
      return typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
    });
    expect(callUrls[3]).toMatch(/\/git\/commits$/u);
    expect(callUrls.filter((url) => url.endsWith("/git/trees"))).toHaveLength(2);
    if (typeof contenderRequestBody !== "string") {
      throw new TypeError("canary contender commit body must be JSON text");
    }
    const contenderCommitBody = JSON.parse(contenderRequestBody) as {
      message: string;
      parents: string[];
      tree: string;
    };
    expect(contenderCommitBody).toEqual({
      message: `Promotion canary CAS contender ${CANARY_EVIDENCE.event_id}`,
      parents: [HEAD],
      tree: contenderTree,
    });
  });

  it("reuses exact immutable contention evidence without creating another contender", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(CANARY_EVIDENCE),
    ]);
    await expect(repository(fetcher).provePromotionCanaryContention(CANARY_EVIDENCE))
      .resolves.toEqual({
        proofRecorded: true,
        idempotent: true,
        commit: HEAD,
        created: false,
      });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("target-reads one submission view and only its referenced immutable events", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(VIEW),
      contents(RECEIVED),
      contents(METADATA),
    ]);
    await expect(repository(fetcher).readSubmission(SUBMISSION_ID)).resolves.toEqual(VIEW);
    const urls = fetcher.mock.calls.map((call) => {
      const input = call[0];
      return typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    });
    expect(urls).toHaveLength(5);
    expect(urls.some((url) => url.includes("recursive=1") || url.includes("/git/blobs/"))).toBe(false);
    expect(urls.filter((url) => url.includes("/contents/events/"))).toHaveLength(2);
  });

  it("rejects an owner submission mutation that predates its causal head", async () => {
    const event: WritableStateEvent = {
      schema_version: 1,
      event_id: "0198abcd-0000-7000-8000-000000000001",
      event_type: "submission.metadata_amended",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: SUBMISSION_ID,
      causation_event_id: METADATA_ID,
      actor: { kind: "github", login: "alice" },
      payload: { production_metadata: { web_access: true } },
    };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(VIEW),
      contents(RECEIVED),
      contents(METADATA),
    ]);
    await expect(repository(fetcher).appendSubmissionMutation(
      event,
      METADATA_ID,
      {
        ...VIEW,
        mutation_event_id: event.event_id,
        metadata_event_id: event.event_id,
        production_metadata: { web_access: true },
        submission: {
          ...VIEW.submission,
          production_metadata: { web_access: true },
        },
      },
    )).rejects.toBeInstanceOf(StateEventConflictError);
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("fails closed when a targeted view disagrees with a referenced event", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents({ ...VIEW, owner_login: "mallory" }),
      contents(RECEIVED),
      contents(METADATA),
    ]);
    await expect(repository(fetcher).readSubmission(SUBMISSION_ID)).rejects.toMatchObject({ status: 502 });
  });

  it("target-reads and authenticates lifecycle-aware summaries", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(VIEW_V2),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
    ]);
    await expect(repository(fetcher).readSubmission(SUBMISSION_ID)).resolves.toEqual(VIEW_V2);

    const tampered = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents({ ...VIEW_V2, archive: { ...VIEW_V2.archive, archive_commit: "e".repeat(40) } }),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
    ]);
    await expect(repository(tampered).readSubmission(SUBMISSION_ID)).rejects.toMatchObject({ status: 502 });
  });

  it("publishes a create-only event with a non-forced ref update", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);

    await expect(repository(fetcher).appendEvent(EVENT)).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      path: `events/01/${EVENT.event_id}.json`,
    });

    const calls = fetcher.mock.calls;
    expect(calls).toHaveLength(6);
    const treeRequestBody = calls[3]?.[1]?.body;
    if (typeof treeRequestBody !== "string") throw new TypeError("tree body was not text");
    const treeBody = JSON.parse(treeRequestBody) as {
      tree: { path: string; content: string }[];
    };
    expect(treeBody.tree[0]?.path).toContain(EVENT.event_id);
    expect(JSON.parse(treeBody.tree[0]?.content ?? "null")).toEqual(EVENT);
    const updateRequestBody = calls[5]?.[1]?.body;
    if (typeof updateRequestBody !== "string") throw new TypeError("update body was not text");
    expect(JSON.parse(updateRequestBody)).toEqual({ sha: NEW_COMMIT, force: false });
  });

  it("publishes a bound event only at the exact expected State head", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).appendEventAtHead(EVENT, HEAD)).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      path: `events/01/${EVENT.event_id}.json`,
    });
  });

  it("rejects a bound event when State moved before its first append", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
    ]);
    await expect(repository(fetcher).appendEventAtHead(EVENT, "9".repeat(40)))
      .rejects.toBeInstanceOf(GitHubStateError);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("recovers a bound append after a collision committed the exact event", async () => {
    const movedHead = "5".repeat(40);
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ message: "conflict" }, 409),
      json({ object: { sha: movedHead } }),
      json({ tree: { sha: "6".repeat(40) } }),
      contents(EVENT),
    ]);
    await expect(repository(fetcher).appendEventAtHead(EVENT, HEAD)).resolves.toEqual({
      commit: movedHead,
      created: false,
      path: `events/01/${EVENT.event_id}.json`,
    });
  });

  it("atomically appends lifecycle events with the matching lifecycle-aware submission view", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(VIEW),
      contents(RECEIVED),
      contents(METADATA),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).appendSubmissionLifecycle(
      [ARCHIVE_EVENT],
      SUBMISSION_ID,
      VIEW_V2,
    )).resolves.toEqual({ commit: NEW_COMMIT, created: true, view: VIEW_V2 });
    const treeRequest = fetcher.mock.calls[6]?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${ARCHIVE_EVENT.event_id}.json`,
      `views/submissions/01/${SUBMISSION_ID}.json`,
    ]);
  });

  it("atomically records a result, release schedule, and submission view", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(ACCEPTED_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(EVALUATION_STARTED),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).resolves.toEqual({ commit: NEW_COMMIT, created: true, view: RESULT_VIEW });
    const treeRequest = fetcher.mock.calls.find(([, init]) =>
      init?.method === "POST" && (typeof init.body === "string") && init.body.includes("base_tree"))?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${RESULT_EVENT.event_id}.json`,
      `events/01/${RELEASE_EVENT.event_id}.json`,
      `views/submissions/01/${SUBMISSION_ID}.json`,
      `views/result-identities/aa/${RESULT_ID}.json`,
      `views/result-amendments/aa/${RESULT_ID}.json`,
      `views/result-release-status/aa/${RESULT_ID}.json`,
    ]);
    expect(JSON.parse(tree.tree[4]?.content ?? "null")).toEqual(RESULT_AMENDMENT_VIEW);
    expect(JSON.parse(tree.tree[5]?.content ?? "null")).toEqual(RESULT_RELEASE_STATUS_VIEW);
    expect(fetcher.mock.calls.map(([input]) => input instanceof Request
      ? input.url
      : typeof input === "string"
        ? input
        : input.toString()).join("\n")).toContain(
          `/compare/${RESULT_OWNER_CONTRACT_COMMIT}...${HEAD}`,
        );
    expect(fetcher.mock.calls.every(([, init]) => init?.redirect === "manual")).toBe(true);
  });

  it("records an open-conjecture result with an exact not-scheduled release status", async () => {
    const accepted = {
      ...ACCEPTED_VIEW,
      submission: {
        ...ACCEPTED_VIEW.submission,
        problem_group: "open-conjectures" as const,
      },
    };
    const resultView = { ...accepted, result_id: RESULT_ID, result_event_id: RESULT_EVENT.event_id };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(accepted),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(EVALUATION_STARTED),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).recordAcceptedResult(
      [RESULT_EVENT],
      EVALUATION_ACCEPTED.event_id,
      resultView,
    )).resolves.toEqual({ commit: NEW_COMMIT, created: true, view: resultView });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${RESULT_EVENT.event_id}.json`,
      `views/submissions/01/${SUBMISSION_ID}.json`,
      `views/result-identities/aa/${RESULT_ID}.json`,
      `views/result-amendments/aa/${RESULT_ID}.json`,
      `views/result-release-status/aa/${RESULT_ID}.json`,
    ]);
    expect(JSON.parse(tree.tree[4]?.content ?? "null")).toEqual({
      schema_version: 1,
      result_id: RESULT_ID,
      authority_event_id: RESULT_EVENT.event_id,
      status: "not_scheduled",
      release_event_id: null,
    });
  });

  it("keeps the first result-identity authority and reports claimed and recorded collisions distinctly", async () => {
    const prefix = () => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(ACCEPTED_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(EVALUATION_STARTED),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
    ];
    const claimed = sequence([
      ...prefix(),
      contents(claimedGuard(RESULT_ID, CLAIM_EVENT_ID)),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
    ]);
    await expect(repository(claimed).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).rejects.toMatchObject({
      name: "ResultIdentityCollisionError",
      existingKind: "claimed",
    } satisfies Partial<ResultIdentityCollisionError>);

    clearResultOwnerContractProofCacheForTest();
    const recorded = sequence([
      ...prefix(),
      contents(recordedGuard(
        RESULT_ID,
        "0198abcd-1111-7000-8000-000000000009",
      )),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
    ]);
    await expect(repository(recorded).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).rejects.toMatchObject({
      name: "ResultIdentityCollisionError",
      existingKind: "recorded",
    } satisfies Partial<ResultIdentityCollisionError>);
    expect([...claimed.mock.calls, ...recorded.mock.calls].some((call) =>
      call[1]?.method === "POST")).toBe(false);
  });

  it("accepts only an exact same-authority record replay and refuses a missing guard", async () => {
    const prefix = (
      guard: Response,
      amendment: unknown = RESULT_AMENDMENT_VIEW,
      releaseStatus: unknown = RESULT_RELEASE_STATUS_VIEW,
    ) => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(RESULT_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(RESULT_EVENT),
      contents(EVALUATION_STARTED),
      contents(RESULT_EVENT),
      contents(RELEASE_EVENT),
      guard,
      contents(amendment),
      contents(releaseStatus),
    ];
    const exact = sequence([
      ...prefix(contents(recordedGuard(RESULT_ID, RESULT_EVENT.event_id))),
    ]);
    await expect(repository(exact).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).resolves.toEqual({ commit: HEAD, created: false, view: RESULT_VIEW });

    clearResultOwnerContractProofCacheForTest();
    const laterEventId = "0198abcd-1111-7000-8000-000000000009";
    const evolved = sequence([
      ...prefix(
        contents(recordedGuard(RESULT_ID, RESULT_EVENT.event_id)),
        { ...RESULT_AMENDMENT_VIEW, mutation_event_id: laterEventId },
        {
          ...RESULT_RELEASE_STATUS_VIEW,
          status: "published",
          release_event_id: laterEventId,
        },
      ),
    ]);
    await expect(repository(evolved).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).resolves.toEqual({ commit: HEAD, created: false, view: RESULT_VIEW });

    clearResultOwnerContractProofCacheForTest();
    const missingGuard = sequence([
      ...prefix(new Response(null, { status: 404 })),
    ]);
    await expect(repository(missingGuard).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).rejects.toBeInstanceOf(StateEventConflictError);
  });

  it("atomically claims a verified legacy record and all five private indexes", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2025-08-15T03:36:44.322Z",
      verified: LEGACY_RESULT,
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      authorityEventId: CLAIM_EVENT_ID,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${CLAIM_EVENT_ID}.json`,
      `views/result-identities/11/${LEGACY_RESULT.resultId}.json`,
      `views/result-overlays/11/${LEGACY_RESULT.resultId}.json`,
      "views/result-source-records/34/src1_34ef08a904550548d360cc62407a77a7e5e8dfe9184c8d472e4f4266ffc3f826.json",
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
      `views/result-release-status/11/${LEGACY_RESULT.resultId}.json`,
    ]);
    expect(tree.tree.every((entry) => entry.content.endsWith("\n"))).toBe(true);
    expect(tree.tree[1]?.content).toContain('"authority_event_id"');
    expect(JSON.parse(tree.tree[4]?.content ?? "null")).toEqual(LEGACY_AMENDMENT_VIEW);
    expect(JSON.parse(tree.tree[5]?.content ?? "null")).toEqual(LEGACY_RELEASE_STATUS_VIEW);
  });

  it("restarts the complete claim preflight after a stale-base CAS collision", async () => {
    const nextHead = "5".repeat(40);
    const nextTree = "6".repeat(40);
    const finalTree = "7".repeat(40);
    const finalCommit = "8".repeat(40);
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ message: "not a fast-forward" }, 409),
      json({ object: { sha: nextHead } }),
      json({ tree: { sha: nextTree } }),
      ...resultOwnerContractProofResponses(),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      json({ sha: finalTree }, 201),
      json({ sha: finalCommit }, 201),
      json({ object: { sha: finalCommit } }),
    ]);
    await expect(repository(fetcher).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2025-08-15T03:36:44.322Z",
      verified: LEGACY_RESULT,
    })).resolves.toMatchObject({ commit: finalCommit, created: true });
    const updates = fetcher.mock.calls.filter((call) => call[1]?.method === "PATCH");
    expect(updates).toHaveLength(2);
  });

  it("rejects a new legacy claim whose idempotency key is stale for its request clock", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
    ]);
    await expect(repository(fetcher).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2026-08-24T08:00:00.000Z",
      verified: LEGACY_RESULT,
    })).rejects.toMatchObject({
      status: 409,
      message: "Idempotency-Key does not match the current request clock",
    });
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("rejects a claim colliding with a recorded identity guard", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(recordedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
    ]);
    await expect(repository(fetcher).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2026-08-24T08:00:00.000Z",
      verified: LEGACY_RESULT,
    })).rejects.toMatchObject({
      name: "ResultIdentityCollisionError",
      existingKind: "recorded",
    } satisfies Partial<ResultIdentityCollisionError>);
    expect(fetcher).toHaveBeenCalledTimes(
      2 + 1 + Object.keys(RESULT_OWNER_CONTRACT_BLOBS).length + 6,
    );
  });

  it("accepts a later exact same-key claim retry but rejects a forged source binding", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z");
    const source = await claimedSourceIndex(LEGACY_RESULT, CLAIM_EVENT_ID);
    const claimEvent = {
      schema_version: 1,
      event_id: CLAIM_EVENT_ID,
      event_type: "result.claimed",
      occurred_at: "2026-08-24T08:00:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: LEGACY_RESULT.baseResult,
    } as const;
    const retractionRequestId = "0198abcd-2222-7000-8000-000000000003";
    const retractionDecisionId = "0198abcd-2222-7000-8000-000000000004";
    const requested = requestedRetractionView(
      LEGACY_AMENDMENT_VIEW,
      retractionRequestId,
      "2026-08-24T08:01:00.000Z",
      "owner_requested_withdrawal",
    );
    const evolvedAmendment = decodeResultAmendmentView({
      ...requested,
      mutation_event_id: retractionDecisionId,
      retraction: {
        ...requested.retraction,
        status: "rejected",
        decision_event_id: retractionDecisionId,
        decided_at: "2026-08-24T08:02:00.000Z",
        reviewer_login: "maintainer",
        reason_code: "request_not_confirmed",
      },
    });
    const exact = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(overlay),
      contents(evolvedAmendment),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(source),
      contents(claimEvent),
      contents(claimEvent),
    ]);
    await expect(repository(exact).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2026-08-24T09:00:00.000Z",
      verified: LEGACY_RESULT,
    })).resolves.toMatchObject({ created: false, authorityEventId: CLAIM_EVENT_ID });

    clearResultOwnerContractProofCacheForTest();
    const otherEventId = "0198abcd-0000-7000-8000-000000000009";
    const occupied = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(overlay),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(source),
      contents({ ...claimEvent, event_id: otherEventId }),
    ]);
    await expect(repository(occupied).claimLegacyResult({
      eventId: otherEventId,
      occurredAt: "2026-08-24T08:00:00.000Z",
      verified: LEGACY_RESULT,
    })).rejects.toBeInstanceOf(StateEventConflictError);

    clearResultOwnerContractProofCacheForTest();
    const forged = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(overlay),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents({ ...source, result_id: `r2_${"2".repeat(64)}` }),
      contents(claimEvent),
      contents(claimEvent),
    ]);
    await expect(repository(forged).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2026-08-24T08:00:00.000Z",
      verified: LEGACY_RESULT,
    })).rejects.toBeInstanceOf(StateEventConflictError);
  });

  it("keeps the first claim canonical when the same record is re-claimed at another reachable commit", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z");
    const source = await claimedSourceIndex(LEGACY_RESULT, CLAIM_EVENT_ID);
    const claimEvent = {
      schema_version: 1,
      event_id: CLAIM_EVENT_ID,
      event_type: "result.claimed",
      occurred_at: "2026-08-24T08:00:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: LEGACY_RESULT.baseResult,
    } as const;
    const newer = {
      ...LEGACY_RESULT,
      baseResult: { ...LEGACY_RESULT.baseResult, results_commit: "c".repeat(40) },
    };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(overlay),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      contents(source),
      contents(claimEvent),
    ]);
    await expect(repository(fetcher).claimLegacyResult({
      eventId: "0198abcd-0000-7000-8000-000000000009",
      occurredAt: "2026-08-24T08:05:00.000Z",
      verified: newer,
    })).resolves.toEqual({
      commit: HEAD,
      created: false,
      resultId: LEGACY_RESULT.resultId,
      authorityEventId: CLAIM_EVENT_ID,
    });
    expect(fetcher.mock.calls.some((call) => call[1]?.method === "POST")).toBe(false);
  });

  it("serializes metadata backfills through the current overlay mutation head", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z");
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents(legacyClaimEvent()),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: BACKFILL_EVENT_ID,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${BACKFILL_EVENT_ID}.json`,
      `views/result-overlays/11/${LEGACY_RESULT.resultId}.json`,
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
    ]);
    expect(JSON.parse(tree.tree[0]?.content ?? "null")).toMatchObject({
      causation_event_id: CLAIM_EVENT_ID,
      payload: { production_metadata: { web_access: false } },
    });
    expect(JSON.parse(tree.tree[1]?.content ?? "null")).toMatchObject({
      mutation_event_id: BACKFILL_EVENT_ID,
      metadata: { web_access: { event_id: BACKFILL_EVENT_ID, provenance: "backfilled", value: false } },
    });
    expect(JSON.parse(tree.tree[2]?.content ?? "null")).toEqual({
      ...LEGACY_AMENDMENT_VIEW,
      mutation_event_id: BACKFILL_EVENT_ID,
    });
  });

  it("rejects a metadata backfill after the release starts", async () => {
    const releaseEventId = "0198abcd-2222-7000-8000-000000000006";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z")),
      contents({
        ...LEGACY_RELEASE_STATUS_VIEW,
        status: "running",
        release_event_id: releaseEventId,
      }),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents({
        schema_version: 1,
        event_id: releaseEventId,
        event_type: "release.started",
        occurred_at: "2026-08-24T08:00:30.000Z",
        subject_id: LEGACY_RESULT.resultId,
        causation_event_id: CLAIM_EVENT_ID,
        actor: { kind: "system" },
        payload: { attempt: 1 },
      }),
      contents(legacyClaimEvent()),
    ]);
    await expect(repository(fetcher).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).rejects.toMatchObject({ status: 409 });
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("makes a same-key backfill replay idempotent and a changed body conflicting", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const claimed = claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z");
    const overlay = {
      ...claimed,
      mutation_event_id: BACKFILL_EVENT_ID,
      metadata: {
        web_access: {
          value: false,
          provenance: "backfilled",
          event_id: BACKFILL_EVENT_ID,
          recorded_at: "2026-08-24T08:01:00.000Z",
        },
      },
    } as const;
    const event = {
      schema_version: 1,
      event_id: BACKFILL_EVENT_ID,
      event_type: "result.metadata_backfilled",
      occurred_at: "2026-08-24T08:01:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { production_metadata: { web_access: false } },
    } as const;
    const backfilledAmendment = decodeResultAmendmentView({
      ...LEGACY_AMENDMENT_VIEW,
      mutation_event_id: BACKFILL_EVENT_ID,
    });
    const exact = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(backfilledAmendment),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(legacyClaimEvent()),
      contents(event),
    ]);
    await expect(repository(exact).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).resolves.toMatchObject({ created: false, mutationEventId: BACKFILL_EVENT_ID });

    clearResultOwnerContractProofCacheForTest();
    const changed = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(backfilledAmendment),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(legacyClaimEvent()),
      contents(event),
    ]);
    await expect(repository(changed).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: true },
    })).rejects.toBeInstanceOf(StateEventConflictError);

    clearResultOwnerContractProofCacheForTest();
    const forgedProvenance = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(backfilledAmendment),
      contents({
        ...overlay,
        metadata: {
          web_access: {
            ...overlay.metadata.web_access,
            recorded_at: "2026-08-24T08:02:00.000Z",
          },
        },
      }),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(legacyClaimEvent()),
      contents(event),
    ]);
    await expect(repository(forgedProvenance).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).rejects.toBeInstanceOf(StateEventConflictError);
  });

  it("serializes a later metadata backfill through a rejected retraction decision", async () => {
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const decisionId = "0198abcd-2222-7000-8000-000000000004";
    const backfillId = "0198abcd-2222-7000-8000-000000000005";
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-24T08:00:00.000Z",
    );
    const requested = requestedRetractionView(
      initialResultAmendmentView({
        resultId: LEGACY_RESULT.resultId,
        ownerLogin: "alice",
        declaredModel: LEGACY_RESULT.baseResult.declared_model,
        authorityEventId: CLAIM_EVENT_ID,
        mutationEventId: CLAIM_EVENT_ID,
        problemId: LEGACY_RESULT.baseResult.problem_id,
        statementRevision: LEGACY_RESULT.baseResult.statement_revision,
      }),
      requestId,
      "2026-08-24T08:01:00.000Z",
      "owner_requested_withdrawal",
    );
    const rejected = decodeResultAmendmentView({
      ...requested,
      mutation_event_id: decisionId,
      retraction: {
        ...requested.retraction,
        status: "rejected",
        decision_event_id: decisionId,
        decided_at: "2026-08-24T08:02:00.000Z",
        reviewer_login: "maintainer",
        reason_code: "request_not_confirmed",
      },
    });
    const decision: StateEvent = {
      schema_version: 1,
      event_id: decisionId,
      event_type: "result.retraction_decided",
      occurred_at: "2026-08-24T08:02:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: requestId,
      actor: { kind: "system" },
      payload: {
        retraction_revision: 1,
        reviewer_login: "maintainer",
        decision: "reject",
        reason_code: "request_not_confirmed",
      },
    };
    const request: StateEvent = {
      schema_version: 1,
      event_id: requestId,
      event_type: "result.retraction_requested",
      occurred_at: "2026-08-24T08:01:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: {
        retraction_revision: 1,
        reason_code: "owner_requested_withdrawal",
      },
    };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(rejected),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents(decision),
      contents(request),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).backfillLegacyResultMetadata({
      eventId: backfillId,
      occurredAt: "2026-08-24T08:03:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).resolves.toMatchObject({ created: true, mutationEventId: backfillId });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${backfillId}.json`,
      `views/result-overlays/11/${LEGACY_RESULT.resultId}.json`,
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
    ]);
    expect(JSON.parse(tree.tree[0]?.content ?? "null")).toMatchObject({
      causation_event_id: decisionId,
    });
    expect(JSON.parse(tree.tree[2]?.content ?? "null")).toMatchObject({
      mutation_event_id: backfillId,
      retraction: { status: "rejected" },
    });

    clearResultOwnerContractProofCacheForTest();
    const replay = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(rejected),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(request),
      contents(legacyClaimEvent()),
      contents(decision),
      contents(request),
    ]);
    await expect(repository(replay).requestResultRetraction({
      eventId: requestId,
      occurredAt: "2026-08-24T08:09:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    })).resolves.toEqual({
      commit: HEAD,
      created: false,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: requestId,
      retractionRevision: 1,
    });
    expect(replay.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const forgedHistory = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(rejected),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents(decision),
      contents({ ...request, actor: { kind: "github", login: "mallory" } }),
    ]);
    await expect(repository(forgedHistory).backfillLegacyResultMetadata({
      eventId: backfillId,
      occurredAt: "2026-08-24T08:03:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(forgedHistory.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const crossTypeDecision = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(rejected),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents({
        schema_version: 1,
        event_id: decisionId,
        event_type: "result.metadata_backfilled",
        occurred_at: "2026-08-24T08:02:00.000Z",
        subject_id: LEGACY_RESULT.resultId,
        causation_event_id: requestId,
        actor: { kind: "github", login: "alice" },
        payload: { production_metadata: { notes: "wrong event family" } },
      }),
      contents(request),
    ]);
    await expect(repository(crossTypeDecision).backfillLegacyResultMetadata({
      eventId: backfillId,
      occurredAt: "2026-08-24T08:03:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(crossTypeDecision.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("replays an older exact backfill after a later mutation using per-field provenance", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const claimed = claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z");
    const first = backfilledOverlay(
      claimed,
      BACKFILL_EVENT_ID,
      "2026-08-24T08:01:00.000Z",
      { web_access: false },
    );
    const laterEventId = "0198abcd-0000-7000-8000-000000000003";
    const current = backfilledOverlay(
      first,
      laterEventId,
      "2026-08-24T08:02:00.000Z",
      { notes: "later mutation" },
    );
    const firstEvent = {
      schema_version: 1,
      event_id: BACKFILL_EVENT_ID,
      event_type: "result.metadata_backfilled",
      occurred_at: "2026-08-24T08:01:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { production_metadata: { web_access: false } },
    } as const;
    const laterEvent: StateEvent = {
      ...firstEvent,
      event_id: laterEventId,
      occurred_at: "2026-08-24T08:02:00.000Z",
      causation_event_id: BACKFILL_EVENT_ID,
      payload: { production_metadata: { notes: "later mutation" } },
    };
    const currentAmendment = decodeResultAmendmentView({
      ...LEGACY_AMENDMENT_VIEW,
      mutation_event_id: laterEventId,
    });
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(currentAmendment),
      contents(current),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(firstEvent),
      contents(legacyClaimEvent()),
      contents(laterEvent),
    ]);
    await expect(repository(fetcher).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).resolves.toEqual({
      commit: HEAD,
      created: false,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: BACKFILL_EVENT_ID,
    });
  });

  it("hides a legacy claim from a different authenticated owner", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z")),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents(legacyClaimEvent()),
    ]);
    await expect(repository(fetcher).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "mallory",
      productionMetadata: { web_access: false },
    })).rejects.toMatchObject({ status: 404 });
  });

  it("refuses to overwrite an existing event", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents({ ...EVENT, occurred_at: "2026-08-21T06:07:08.000Z" }),
    ]);
    await expect(repository(fetcher).appendEvent(EVENT)).rejects.toBeInstanceOf(
      StateEventConflictError,
    );
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("treats a structurally identical existing event as an idempotent success", async () => {
    const reordered = {
      payload: EVENT.payload,
      actor: EVENT.actor,
      causation_event_id: EVENT.causation_event_id,
      subject_id: EVENT.subject_id,
      occurred_at: EVENT.occurred_at,
      event_type: EVENT.event_type,
      event_id: EVENT.event_id,
      schema_version: EVENT.schema_version,
    };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(reordered),
    ]);
    await expect(repository(fetcher).appendEvent(EVENT)).resolves.toEqual({
      commit: HEAD,
      created: false,
      path: `events/01/${EVENT.event_id}.json`,
    });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("retries the complete compare-and-swap after a ref collision", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ message: "conflict" }, 409),
      json({ object: { sha: "5".repeat(40) } }),
      json({ tree: { sha: "6".repeat(40) } }),
      new Response(null, { status: 404 }),
      json({ sha: "7".repeat(40) }, 201),
      json({ sha: "8".repeat(40) }, 201),
      json({ object: { sha: "8".repeat(40) } }),
    ]);

    await expect(repository(fetcher).appendEvent(EVENT)).resolves.toEqual({
      commit: "8".repeat(40),
      created: true,
      path: `events/01/${EVENT.event_id}.json`,
    });
    expect(fetcher).toHaveBeenCalledTimes(12);
  });

  it("recognizes an applied commit after an ambiguous ref update", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ message: "upstream unavailable" }, 500),
      json({ message: "conflict" }, 409),
      json({ object: { sha: NEW_COMMIT } }),
    ]);

    await expect(repository(fetcher).appendEvent(EVENT)).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      path: `events/01/${EVENT.event_id}.json`,
    });
  });

  it("fails closed when an ambiguous update cannot be resolved", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      new Error("connection reset"),
      new Error("connection reset"),
      new Error("GitHub unavailable"),
    ]);

    await expect(repository(fetcher).appendEvent(EVENT)).rejects.toBeInstanceOf(
      StateUpdateOutcomeUnknownError,
    );
  });

  it("rejects malformed existing event contents", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(null),
    ]);
    await expect(repository(fetcher).appendEvent(EVENT)).rejects.toBeInstanceOf(
      StateEventConflictError,
    );
  });

  it("atomically requests a legacy-result retraction and writes only its targeted private view", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority: StateEvent = {
      schema_version: 1,
      event_id: CLAIM_EVENT_ID,
      event_type: "result.claimed",
      occurred_at: "2026-08-20T06:07:08.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: LEGACY_RESULT.baseResult,
    };
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(authority),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).requestResultRetraction({
      eventId: requestId,
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: requestId,
      retractionRevision: 1,
    });
    const treeCall = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST");
    expect(treeCall).toBeDefined();
    const treeRequest = treeCall?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const body = JSON.parse(treeRequest) as {
      tree: { path: string; content: string }[];
    };
    expect(body.tree.map((entry) => entry.path)).toEqual([
      `events/01/${requestId}.json`,
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
    ]);
    expect(treeRequest).not.toContain("views/result-release-status/");
    const event = JSON.parse(body.tree[0]?.content ?? "null") as Record<string, unknown>;
    expect(event).toMatchObject({
      event_type: "result.retraction_requested",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { retraction_revision: 1, reason_code: "owner_requested_withdrawal" },
    });
  });

  it("atomically requests a problem repair without changing the targeted release status", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(authority),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).requestResultProblemRepair({
      eventId: requestId,
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 2,
      reasonCode: "wrong_problem_revision",
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: requestId,
      repairRevision: 1,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${requestId}.json`,
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
    ]);
    expect(treeRequest).not.toContain("views/result-release-status/");
    expect(JSON.parse(tree.tree[0]?.content ?? "null")).toMatchObject({
      event_type: "result.problem_repair_requested",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: {
        repair_revision: 1,
        corrected_problem_id: "two_plus_three",
        corrected_statement_revision: 2,
        reason_code: "wrong_problem_revision",
      },
    });
    expect(JSON.parse(tree.tree[1]?.content ?? "null")).toMatchObject({
      mutation_event_id: requestId,
      problem_repair: {
        revision: 1,
        status: "pending",
        request_event_id: requestId,
      },
    });
  });

  it("rejects problem repair after release starts, publishes, or is removed", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const releaseEventId = "0198abcd-2222-7000-8000-000000000002";
    for (const status of ["running", "published", "removed"] as const) {
      clearResultOwnerContractProofCacheForTest();
      const fetcher = sequence([
        json({ object: { sha: HEAD } }),
        json({ tree: { sha: TREE } }),
        ...resultOwnerContractProofResponses(),
        contents(guard),
        contents(LEGACY_AMENDMENT_VIEW),
        contents(overlay),
        contents({
          ...LEGACY_RELEASE_STATUS_VIEW,
          status,
          release_event_id: releaseEventId,
        }),
        new Response(null, { status: 404 }),
        contents(authority),
        contents({
          schema_version: 1,
          event_id: releaseEventId,
          event_type: {
            running: "release.started",
            published: "release.published",
            removed: "release.removed",
          }[status],
          occurred_at: "2026-08-20T06:07:08.500Z",
          subject_id: LEGACY_RESULT.resultId,
          causation_event_id: CLAIM_EVENT_ID,
          actor: { kind: "system" },
          payload: status === "running"
            ? { attempt: 1 }
            : status === "published"
              ? {
                  attempt: 1,
                  repository_commit: "9".repeat(40),
                  tree_digest: "8".repeat(64),
                  path: "releases/test",
                }
              : {},
        }),
        contents(authority),
      ]);
      await expect(repository(fetcher).requestResultProblemRepair({
        eventId: "0198abcd-2222-7000-8000-000000000003",
        occurredAt: "2026-08-20T06:07:09.000Z",
        resultId: LEGACY_RESULT.resultId,
        ownerLogin: "alice",
        correctedProblemId: "two_plus_three",
        correctedStatementRevision: 2,
        reasonCode: "wrong_problem_revision",
      })).rejects.toMatchObject({ status: 409 });
      expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    }
  });

  it("rejects a release-status view without its exact immutable marker", async () => {
    const releaseEventId = "0198abcd-2222-7000-8000-000000000002";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-20T06:07:08.000Z")),
      contents({
        ...LEGACY_RELEASE_STATUS_VIEW,
        status: "running",
        release_event_id: releaseEventId,
      }),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent("2026-08-20T06:07:08.000Z")),
      contents({
        schema_version: 1,
        event_id: releaseEventId,
        event_type: "release.published",
        occurred_at: "2026-08-20T06:07:08.500Z",
        subject_id: LEGACY_RESULT.resultId,
        causation_event_id: CLAIM_EVENT_ID,
        actor: { kind: "system" },
        payload: {
          attempt: 1,
          repository_commit: "9".repeat(40),
          tree_digest: "8".repeat(64),
          path: "releases/test",
        },
      }),
    ]);
    await expect(repository(fetcher).requestResultProblemRepair({
      eventId: "0198abcd-2222-7000-8000-000000000003",
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 2,
      reasonCode: "wrong_problem_revision",
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("makes an exact problem-repair replay idempotent and rejects changed material", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const event = {
      schema_version: 1,
      event_id: requestId,
      event_type: "result.problem_repair_requested",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: {
        repair_revision: 1,
        corrected_problem_id: "two_plus_three",
        corrected_statement_revision: 2,
        reason_code: "wrong_problem_revision",
      },
    } as const;
    const pending = requestedProblemRepairView(
      LEGACY_AMENDMENT_VIEW,
      requestId,
      event.occurred_at,
      event.payload.corrected_problem_id,
      event.payload.corrected_statement_revision,
      event.payload.reason_code,
    );
    const responses = () => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(pending),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(authority),
      contents(event),
    ];
    const exact = sequence(responses());
    await expect(repository(exact).requestResultProblemRepair({
      eventId: requestId,
      occurredAt: "2026-08-20T06:08:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 2,
      reasonCode: "wrong_problem_revision",
    })).resolves.toMatchObject({ created: false, repairRevision: 1 });
    expect(exact.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const changed = sequence(responses());
    await expect(repository(changed).requestResultProblemRepair({
      eventId: requestId,
      occurredAt: "2026-08-20T06:08:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      correctedProblemId: "different_problem",
      correctedStatementRevision: 2,
      reasonCode: "wrong_problem_revision",
    })).rejects.toBeInstanceOf(StateEventConflictError);
  });

  it("derives modern result retraction authority from the recorded submission view", async () => {
    const requestId = "0198abcd-1111-7000-8000-000000000008";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(recordedGuard(RESULT_ID, RESULT_EVENT.event_id)),
      contents(RESULT_AMENDMENT_VIEW),
      new Response(null, { status: 404 }),
      contents(RESULT_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(RESULT_EVENT),
      contents(RESULT_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(RESULT_EVENT),
      contents(EVALUATION_STARTED),
      contents(RELEASE_EVENT),
      contents(RESULT_EVENT),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).requestResultRetraction({
      eventId: requestId,
      occurredAt: "2026-08-20T06:07:12.000Z",
      resultId: RESULT_ID,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: RESULT_ID,
      mutationEventId: requestId,
      retractionRevision: 1,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${requestId}.json`,
      `views/result-amendments/aa/${RESULT_ID}.json`,
    ]);
    expect(JSON.parse(tree.tree[0]?.content ?? "null")).toMatchObject({
      event_type: "result.retraction_requested",
      subject_id: RESULT_ID,
      causation_event_id: RESULT_EVENT.event_id,
      actor: { kind: "github", login: "alice" },
    });
  });

  it("requests a modern problem repair from an exact not-scheduled release view", async () => {
    const requestId = "0198abcd-1111-7000-8000-000000000008";
    const notScheduled = initialResultReleaseStatusView(
      RESULT_ID,
      RESULT_EVENT.event_id,
    );
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(recordedGuard(RESULT_ID, RESULT_EVENT.event_id)),
      contents(RESULT_AMENDMENT_VIEW),
      new Response(null, { status: 404 }),
      contents(notScheduled),
      new Response(null, { status: 404 }),
      contents(RESULT_EVENT),
      contents(RESULT_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(RESULT_EVENT),
      contents(EVALUATION_STARTED),
      contents(RESULT_EVENT),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).requestResultProblemRepair({
      eventId: requestId,
      occurredAt: "2026-08-20T06:07:12.000Z",
      resultId: RESULT_ID,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 3,
      reasonCode: "wrong_problem_revision",
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: RESULT_ID,
      mutationEventId: requestId,
      repairRevision: 1,
    });
    expect(notScheduled).toMatchObject({
      status: "not_scheduled",
      release_event_id: null,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${requestId}.json`,
      `views/result-amendments/aa/${RESULT_ID}.json`,
    ]);
    expect(treeRequest).not.toContain("views/result-release-status/");
  });

  it("rejects a modern result whose targeted release-status view is missing", async () => {
    clearResultOwnerContractProofCacheForTest();
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(recordedGuard(RESULT_ID, RESULT_EVENT.event_id)),
      contents(RESULT_AMENDMENT_VIEW),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      contents(RESULT_EVENT),
      contents(RESULT_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(RESULT_EVENT),
      contents(EVALUATION_STARTED),
    ]);
    await expect(repository(fetcher).requestResultProblemRepair({
      eventId: "0198abcd-1111-7000-8000-000000000008",
      occurredAt: "2026-08-20T06:07:12.000Z",
      resultId: RESULT_ID,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 3,
      reasonCode: "wrong_problem_revision",
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("makes an exact retraction replay idempotent and rejects changed request material", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority: StateEvent = {
      schema_version: 1,
      event_id: CLAIM_EVENT_ID,
      event_type: "result.claimed",
      occurred_at: "2026-08-20T06:07:08.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: LEGACY_RESULT.baseResult,
    };
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const event: StateEvent = {
      schema_version: 1,
      event_id: requestId,
      event_type: "result.retraction_requested",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { retraction_revision: 1, reason_code: "owner_requested_withdrawal" },
    };
    const initial = initialResultAmendmentView({
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      declaredModel: LEGACY_RESULT.baseResult.declared_model,
      authorityEventId: CLAIM_EVENT_ID,
      mutationEventId: CLAIM_EVENT_ID,
      problemId: LEGACY_RESULT.baseResult.problem_id,
      statementRevision: LEGACY_RESULT.baseResult.statement_revision,
    });
    const pending = requestedRetractionView(
      initial,
      requestId,
      event.occurred_at,
      "owner_requested_withdrawal",
    );
    const responses = () => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(pending),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(authority),
      contents(event),
    ];
    const exact = sequence(responses());
    await expect(repository(exact).requestResultRetraction({
      eventId: requestId,
      occurredAt: "2026-08-20T06:08:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    })).resolves.toMatchObject({ created: false, retractionRevision: 1 });
    expect(exact.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const changed = sequence(responses());
    await expect(repository(changed).requestResultRetraction({
      eventId: requestId,
      occurredAt: "2026-08-20T06:08:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      reasonCode: "different_reason",
    })).rejects.toBeInstanceOf(StateEventConflictError);
  });

  it("conceals a result owned by someone else and rejects a stale causal UUID", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority: StateEvent = {
      schema_version: 1,
      event_id: CLAIM_EVENT_ID,
      event_type: "result.claimed",
      occurred_at: "2026-08-20T06:07:08.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: LEGACY_RESULT.baseResult,
    };
    const baseResponses = () => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(authority),
    ];
    const hidden = sequence(baseResponses());
    await expect(repository(hidden).requestResultRetraction({
      eventId: "0198abcd-2222-7000-8000-000000000003",
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "mallory",
      reasonCode: "owner_requested_withdrawal",
    })).rejects.toMatchObject({ status: 404 });

    clearResultOwnerContractProofCacheForTest();
    const stale = sequence(baseResponses());
    await expect(repository(stale).requestResultRetraction({
      eventId: "0198abcd-2222-7000-8000-000000000000",
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    })).rejects.toMatchObject({ status: 409 });
  });
});
