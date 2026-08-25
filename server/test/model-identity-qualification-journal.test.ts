import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import type {
  QualificationAcquisition,
  QualificationStepReservation,
} from "../src/model-identity-qualification-journal";

const COMMIT = "a".repeat(40);
const TREE = "b".repeat(40);
const NEXT_COMMIT = "c".repeat(40);
const NEXT_TREE = "d".repeat(40);

function acquisition(runId: string): QualificationAcquisition {
  return {
    schema_version: 2,
    run_id: runId,
    run_attempt: 1,
    deployed_commit: "e".repeat(40),
    initial_state_commit: COMMIT,
    initial_state_tree: TREE,
    intent: {
      owner: { github_id: 1, login: "owner" },
      cross_owner: { github_id: 2, login: "cross-owner" },
      maintainer: { github_id: 3, login: "maintainer" },
    },
  };
}

function journal(name: string) {
  const namespace = env.MODEL_IDENTITY_QUALIFICATION_JOURNAL;
  if (namespace === undefined) throw new Error("qualification journal binding is unavailable");
  return namespace.getByName(name);
}

describe("model identity qualification durable journal", () => {
  it("acquires one exact staging lease and idempotently returns the same journal", async () => {
    const stub = journal(`acquire-${crypto.randomUUID()}`);
    const first = await stub.acquire(acquisition("1001"));
    const retry = await stub.acquire(acquisition("1001"));
    expect(retry).toEqual(first);
    expect(first).toMatchObject({
      schema_version: 2,
      environment: "staging",
      run_id: "1001",
      run_attempt: 1,
      journal_revision: 1,
      current_state_commit: COMMIT,
      current_state_tree: TREE,
      lease_status: "active",
      lease_released: false,
      owner_api_enabled: false,
      maintainer_api_enabled: false,
    });
    expect(first.journal_id).toMatch(/^mqj_[0-9a-f]{64}$/);
    expect(JSON.stringify(first)).not.toContain("recovery_nonce");
    const recovery = JSON.parse(await stub.readRecoveryPlan("1001")) as {
      recovery_nonce: string;
      journal_id: string;
      current_state_commit: string;
      current_state_tree: string;
      intent: { owner: { github_id: number; login: string } };
      pending_step: null;
    };
    expect(recovery.recovery_nonce).toMatch(/^[0-9a-f]{64}$/);
    expect(recovery).toMatchObject({
      journal_id: first.journal_id,
      current_state_commit: COMMIT,
      current_state_tree: TREE,
      intent: { owner: { github_id: 1, login: "owner" } },
      pending_step: null,
    });

    await runInDurableObject(stub, async (instance) => {
      await expect(instance.acquire({
        ...acquisition("1001"),
        initial_state_tree: "f".repeat(40),
      })).rejects.toThrow("conflicts with its durable journal");
      await expect(instance.acquire(acquisition("1002")))
        .rejects.toThrow("another model identity qualification holds the staging lease");
    });
  });

  it("persists a step reservation before mutation and an exact retry receipt after it", async () => {
    const stub = journal(`step-${crypto.randomUUID()}`);
    const acquired = await stub.acquire(acquisition("2001"));
    const reservation: QualificationStepReservation = {
      run_id: "2001",
      run_attempt: 1,
      journal_id: acquired.journal_id,
      expected_journal_revision: 1,
      expected_state_commit: COMMIT,
      expected_state_tree: TREE,
      operation: "owner_request",
      operation_request: { event_id: "0198abcd-0000-7000-8000-000000000001" },
    };
    await expect(stub.reserveStep(reservation)).resolves.toEqual({
      kind: "reserved",
      journal: acquired,
    });
    await runInDurableObject(stub, async (instance) => {
      await expect(instance.reserveStep({ ...reservation, operation: "identity_rename" }))
        .rejects.toThrow("another pending step");
    });

    const receipt = { operation: "owner_request", status: "verified" };
    await expect(stub.completeStep({
      reservation,
      state_commit: NEXT_COMMIT,
      state_tree: NEXT_TREE,
      receipt,
    })).resolves.toBe(JSON.stringify(receipt));
    await expect(stub.reserveStep(reservation)).resolves.toEqual({
      kind: "completed",
      receipt_json: JSON.stringify(receipt),
    });
    await runInDurableObject(stub, async (instance) => {
      await expect(instance.completeStep({
        reservation,
        state_commit: NEXT_COMMIT,
        state_tree: "f".repeat(40),
        receipt,
      })).rejects.toThrow("conflicts with its durable receipt");
    });
    expect(await stub.readStatus("2001")).toMatchObject({
      journal_revision: 2,
      current_state_commit: NEXT_COMMIT,
      current_state_tree: NEXT_TREE,
    });
  });

  it("rejects foreign State movement and records only an exact fast-forward restoration", async () => {
    const stub = journal(`restore-${crypto.randomUUID()}`);
    const acquired = await stub.acquire(acquisition("3001"));
    const reservation: QualificationStepReservation = {
      run_id: "3001",
      run_attempt: 1,
      journal_id: acquired.journal_id,
      expected_journal_revision: 1,
      expected_state_commit: "f".repeat(40),
      expected_state_tree: TREE,
      operation: "owner_request",
      operation_request: {},
    };
    await runInDurableObject(stub, async (instance) => {
      await expect(instance.reserveStep(reservation))
        .rejects.toThrow("does not match the active durable journal");
    });

    const restoration = {
      restoration_commit: NEXT_COMMIT,
      restoration_parent_commit: COMMIT,
      restoration_parent_tree: TREE,
      restoration_tree: TREE,
    };
    const restored = await stub.completeRestoration("3001", 1, restoration);
    expect(restored).toMatchObject({
      journal_revision: 2,
      current_state_commit: NEXT_COMMIT,
      current_state_tree: TREE,
      lease_status: "restored",
      lease_released: true,
      restoration_fast_forward: true,
      restoration_tree_equal: true,
    });
    await expect(stub.completeRestoration("3001", 1, restoration)).resolves.toEqual(restored);
    await runInDurableObject(stub, async (instance) => {
      await expect(instance.completeRestoration("3001", 1, {
        ...restoration,
        restoration_commit: "f".repeat(40),
      })).rejects.toThrow("conflicts with its durable receipt");
    });

    await expect(stub.acquire(acquisition("3002"))).resolves.toMatchObject({
      run_id: "3002",
      lease_status: "active",
    });
  });
});
