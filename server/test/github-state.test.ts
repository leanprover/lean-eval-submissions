import { describe, expect, it, vi } from "vitest";

import {
  type GitHubFetch,
  GitHubStateRepository,
  StateEventConflictError,
  StateUpdateOutcomeUnknownError,
} from "../src/github-state";
import type {
  StateEvent,
  WritableResultLifecycleEvent,
  WritableSubmissionLifecycleEvent,
} from "../src/state-event";
import type { SubmissionView } from "../src/submission-view";

const HEAD = "1".repeat(40);
const TREE = "2".repeat(40);
const NEW_TREE = "3".repeat(40);
const NEW_COMMIT = "4".repeat(40);

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

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

function contents(value: unknown): Response {
  return json({ encoding: "base64", content: btoa(`${JSON.stringify(value)}\n`) });
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

describe("atomic Git State append", () => {
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
        collisionObserved: true,
        retryApplied: true,
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
        collisionObserved: true,
        retryApplied: true,
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
      contents(ACCEPTED_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(EVALUATION_STARTED),
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
    const treeRequest = fetcher.mock.calls[10]?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${RESULT_EVENT.event_id}.json`,
      `events/01/${RELEASE_EVENT.event_id}.json`,
      `views/submissions/01/${SUBMISSION_ID}.json`,
    ]);
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
});
