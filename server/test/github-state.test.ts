import { describe, expect, it, vi } from "vitest";

import {
  type GitHubFetch,
  GitHubStateRepository,
  StateEventConflictError,
  StateUpdateOutcomeUnknownError,
} from "../src/github-state";
import type { StateEvent } from "../src/state-event";

const HEAD = "1".repeat(40);
const TREE = "2".repeat(40);
const NEW_TREE = "3".repeat(40);
const NEW_COMMIT = "4".repeat(40);

const EVENT: StateEvent = {
  schema_version: 1,
  event_id: "a".repeat(64),
  event_type: "system.initialized",
  occurred_at: "2026-08-20T06:07:08.000Z",
  subject_id: "state_staging",
  actor: { kind: "system" },
  payload: { environment: "staging" },
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
      path: `events/aa/${"a".repeat(64)}.json`,
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
      path: `events/aa/${"a".repeat(64)}.json`,
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
      path: `events/aa/${"a".repeat(64)}.json`,
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
      path: `events/aa/${"a".repeat(64)}.json`,
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
