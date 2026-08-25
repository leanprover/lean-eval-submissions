import { afterEach, describe, expect, it, vi } from "vitest";

import collision from "../src/model-identity-qualification-collision";
import {
  signQualificationExecutorCapability,
  type QualificationExecutorCapability,
} from "../src/model-identity-qualification-capability";

const SECRET = "test-only-qualification-executor-capability-secret";
const DEPLOYED_COMMIT = "0".repeat(40) as "0000000000000000000000000000000000000000";
const REPOSITORY_PATH = "/repos/leanprover/lean-eval-state-staging";

function collisionEnv(): CollisionCloudflareEnv {
  return {
    DEPLOYED_COMMIT,
    DEPLOYMENT_ENVIRONMENT: "staging",
    GITHUB_STATE_TOKEN: "test-only-github-state-token-value-long-enough",
    MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET: SECRET,
    STATE_REPOSITORY: "leanprover/lean-eval-state-staging",
  };
}

async function capability(): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const value: QualificationExecutorCapability = {
    schema_version: 1,
    kind: "model_identity_qualification_executor",
    deployed_commit: DEPLOYED_COMMIT,
    run_id: "33000000001",
    run_attempt: 1,
    journal_id: `mqj_${"b".repeat(64)}`,
    journal_revision: 14,
    operation: "maximal_contention_measurement",
    plan_digest: "c".repeat(64),
    request_digest: "d".repeat(64),
    request_index: 0,
    issued_at: now,
    expires_at: now + 60,
  };
  return signQualificationExecutorCapability(SECRET, value);
}

function collisionRequest(
  token: string,
  mutationCommit: string,
  attempt: number,
): Request {
  return new Request("https://collision.invalid/internal/v1/github", {
    method: "POST",
    headers: {
      "x-lean-eval-cas-attempt": String(attempt),
      "x-lean-eval-qualification-capability": token,
      "x-lean-eval-upstream-accept": "application/vnd.github+json",
      "x-lean-eval-upstream-content-type": "application/json",
      "x-lean-eval-upstream-method": "PATCH",
      "x-lean-eval-upstream-path": "/git/refs/heads/main",
      "x-lean-eval-upstream-user-agent": "qualification-test",
      "x-lean-eval-upstream-x-github-api-version": "2022-11-28",
    },
    body: JSON.stringify({ sha: mutationCommit, force: false }),
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("model identity qualification collision service", () => {
  it("has no public route", async () => {
    const response = await collision.fetch(
      new Request("https://collision.invalid/"),
      collisionEnv(),
    );
    expect(response.status).toBe(404);
  });

  it("creates seven genuine same-tree non-fast-forwards and forwards attempt eight", async () => {
    type Commit = { message: string; tree: string; parent: string };
    const commits = new Map<string, Commit>();
    let head = "1".repeat(40);
    commits.set(head, { message: "seed", tree: "2".repeat(40), parent: "3".repeat(40) });
    let contenderNumber = 0;
    const originalPatchStatuses: number[] = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const request = new Request(input, init);
      const url = new URL(request.url);
      const path = url.pathname.slice(REPOSITORY_PATH.length);
      const method = request.method.toUpperCase();
      const rawBody = await request.text();
      const body = rawBody === "" ? {} : JSON.parse(rawBody) as Record<string, unknown>;
      if (method === "GET" && path.startsWith("/git/commits/")) {
        const sha = path.slice("/git/commits/".length);
        const commit = commits.get(sha);
        if (commit === undefined) return Response.json({ error: "missing" }, { status: 404 });
        return Response.json({
          sha,
          message: commit.message,
          tree: { sha: commit.tree },
          parents: [{ sha: commit.parent }],
        });
      }
      if (method === "POST" && path === "/git/commits") {
        const parents = body.parents;
        if (!Array.isArray(parents) || typeof parents[0] !== "string") {
          return Response.json({ error: "invalid" }, { status: 422 });
        }
        contenderNumber += 1;
        const sha = contenderNumber.toString(16).padStart(40, "a").slice(-40);
        commits.set(sha, {
          message: String(body.message),
          tree: String(body.tree),
          parent: parents[0],
        });
        return Response.json({ sha }, { status: 201 });
      }
      if (method === "PATCH" && path === "/git/refs/heads/main") {
        const sha = body.sha;
        const commit = typeof sha === "string" ? commits.get(sha) : undefined;
        if (commit === undefined || body.force !== false || commit.parent !== head) {
          originalPatchStatuses.push(422);
          return Response.json({ message: "Update is not a fast forward" }, { status: 422 });
        }
        head = sha as string;
        const isContender = commit.message.startsWith("Model identity qualification collision");
        if (!isContender) originalPatchStatuses.push(200);
        return Response.json({ ref: "refs/heads/main", object: { sha: head } });
      }
      return Response.json({ error: "unexpected" }, { status: 500 });
    });
    vi.stubGlobal("fetch", fetcher);

    const token = await capability();
    for (let attempt = 1; attempt <= 8; attempt += 1) {
      const parent = head;
      const mutation = attempt.toString(16).padStart(40, "f").slice(-40);
      commits.set(mutation, {
        message: `mutation ${String(attempt)}`,
        tree: attempt.toString(16).padStart(40, "4").slice(-40),
        parent,
      });
      const response = await collision.fetch(
        collisionRequest(token, mutation, attempt),
        collisionEnv(),
      );
      expect(response.status).toBe(attempt <= 7 ? 422 : 200);
      if (attempt <= 7) {
        const contender = commits.get(head);
        expect(contender).toMatchObject({
          parent,
          tree: commits.get(parent)?.tree,
        });
        expect(contender?.message).toContain(`attempt ${String(attempt)}`);
      } else {
        expect(head).toBe(mutation);
      }
    }
    expect(contenderNumber).toBe(7);
    expect(originalPatchStatuses).toEqual([422, 422, 422, 422, 422, 422, 422, 200]);
  });
});
