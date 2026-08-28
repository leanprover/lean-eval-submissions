import { describe, expect, it } from "vitest";

import {
  canonicalHistoricalPublicHandoff,
  historicalPublicExecutorVerdict,
  readHistoricalPublicExecutorRequest,
  readHistoricalPublicExecutorStatusRequest,
} from "../src/historical-public-executor-contract";

const EXECUTION_DIGEST = "4".repeat(64);
const MEASUREMENT_DIGEST = "5".repeat(64);
const VM_IMAGE_DIGEST = `sha256:${"6".repeat(64)}`;

async function sha256(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", value);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function fixture(): Promise<Record<string, unknown>> {
  const sourceArchive = new TextEncoder().encode("historical public source archive");
  const archiveDigest = await sha256(sourceArchive);
  const handoff = {
    schema_version: 1,
    kind: "historical_public_runner_handoff",
    contract: "historical_public_runner_v1",
    contract_sha256: "7".repeat(64),
    plan_sha256: "8".repeat(64),
    profile_matrix_sha256: "9".repeat(64),
    request_id: `prr_${"a".repeat(64)}`,
    source: {
      repository: "example/source",
      commit: "b".repeat(40),
      tree: "c".repeat(40),
      visibility: "public",
      archive_format: "git_archive_tar_gzip_v1",
      archive_member_prefix: "source",
      archive_sha256: archiveDigest,
      archive_size_bytes: sourceArchive.byteLength,
    },
    benchmark: {},
    result: { result_id: `r2_${"d".repeat(64)}` },
    profile: {},
    checker: "nanoda",
    network: {},
    untrusted_environment: {},
  };
  const handoffDigest = await sha256(
    new TextEncoder().encode(canonicalHistoricalPublicHandoff(handoff)),
  );
  return {
    schema_version: 1,
    runner_nonce: "1".repeat(64),
    replay_task_id: `rt1_${"2".repeat(64)}`,
    attempt: 3,
    handoff_sha256: handoffDigest,
    source_archive_sha256: archiveDigest,
    execution_profile_digest: EXECUTION_DIGEST,
    measurement_config_digest: MEASUREMENT_DIGEST,
    vm_image_digest: VM_IMAGE_DIGEST,
    handoff,
    source_archive_base64: btoa(
      String.fromCharCode(...sourceArchive),
    ),
  };
}

function runnerVerdict(body: Record<string, unknown>): Record<string, unknown> {
  const handoff = body.handoff as Record<string, unknown>;
  const result = handoff.result as Record<string, unknown>;
  return {
    schema_version: 1,
    request_id: handoff.request_id,
    result_id: result.result_id,
    execution_outcome: "completed",
    checker_outcome: "accepted",
    failure_reason: null,
    statistics: {
      checker_wall_time_ms: 10,
      checker_retired_instructions: { status: "measured", value: 20 },
      build_wall_time_ms: 30,
      build_retired_instructions: {
        status: "unavailable",
        reason: "counter_not_supported",
      },
      lines_of_code: 2,
      file_count: 1,
    },
  };
}

describe("historical public executor boundary", () => {
  it("binds an exact attempt, handoff, archive, and reviewed runtime", async () => {
    const body = await fixture();
    const input = await readHistoricalPublicExecutorRequest(new Request(
      "https://example.test",
      { method: "POST", body: JSON.stringify(body) },
    ), EXECUTION_DIGEST, MEASUREMENT_DIGEST, VM_IMAGE_DIGEST);
    const verdict = historicalPublicExecutorVerdict(input, runnerVerdict(body));
    expect(verdict).toMatchObject({
      contract: "historical_public_executor_v1",
      replay_task_id: body.replay_task_id,
      attempt: 3,
      runner_nonce: body.runner_nonce,
      handoff_sha256: body.handoff_sha256,
      source_archive_sha256: body.source_archive_sha256,
      execution_profile_digest: EXECUTION_DIGEST,
      measurement_config_digest: MEASUREMENT_DIGEST,
      vm_image_digest: VM_IMAGE_DIGEST,
      destruction: "confirmed",
    });
  });

  it("rejects handoff, archive, reviewed-runtime, and verdict drift", async () => {
    for (const mutate of [
      (value: Record<string, unknown>) => { value.handoff_sha256 = "0".repeat(64); },
      (value: Record<string, unknown>) => { value.source_archive_sha256 = "0".repeat(64); },
      (value: Record<string, unknown>) => { value.attempt = 0; },
      (value: Record<string, unknown>) => { value.attempt = 5; },
    ]) {
      const value = await fixture();
      mutate(value);
      await expect(readHistoricalPublicExecutorRequest(new Request(
        "https://example.test",
        { method: "POST", body: JSON.stringify(value) },
      ), EXECUTION_DIGEST, MEASUREMENT_DIGEST, VM_IMAGE_DIGEST)).rejects.toThrow();
    }
    await expect(readHistoricalPublicExecutorRequest(new Request(
      "https://example.test",
      { method: "POST", body: JSON.stringify(await fixture()) },
    ), "f".repeat(64), MEASUREMENT_DIGEST, VM_IMAGE_DIGEST)).rejects.toThrow();

    const body = await fixture();
    const input = await readHistoricalPublicExecutorRequest(new Request(
      "https://example.test",
      { method: "POST", body: JSON.stringify(body) },
    ), EXECUTION_DIGEST, MEASUREMENT_DIGEST, VM_IMAGE_DIGEST);
    expect(() => historicalPublicExecutorVerdict(input, {
      ...runnerVerdict(body),
      result_id: `r2_${"0".repeat(64)}`,
    })).toThrow("differs");
  });

  it("polls only the same complete immutable execution identity", async () => {
    const body = await fixture();
    const status = Object.fromEntries(Object.entries(body).filter(
      ([field]) => field !== "handoff" && field !== "source_archive_base64",
    ));
    await expect(readHistoricalPublicExecutorStatusRequest(new Request(
      "https://example.test",
      { method: "POST", body: JSON.stringify(status) },
    ), EXECUTION_DIGEST, MEASUREMENT_DIGEST, VM_IMAGE_DIGEST)).resolves.toEqual(status);
    await expect(readHistoricalPublicExecutorStatusRequest(new Request(
      "https://example.test",
      { method: "POST", body: JSON.stringify({ ...status, handoff_sha256: "0".repeat(64) }) },
    ), EXECUTION_DIGEST, MEASUREMENT_DIGEST, VM_IMAGE_DIGEST)).resolves.toMatchObject({
      handoff_sha256: "0".repeat(64),
    });
  });

  it("rejects placeholder reviewed identities before parsing input", async () => {
    const request = () => new Request("https://example.test", {
      method: "POST",
      body: "{}",
    });
    await expect(readHistoricalPublicExecutorRequest(
      request(), "0".repeat(64), MEASUREMENT_DIGEST, VM_IMAGE_DIGEST,
    )).rejects.toThrow("not configured");
    await expect(readHistoricalPublicExecutorRequest(
      request(), EXECUTION_DIGEST, "0".repeat(64), VM_IMAGE_DIGEST,
    )).rejects.toThrow("not configured");
    await expect(readHistoricalPublicExecutorRequest(
      request(), EXECUTION_DIGEST, MEASUREMENT_DIGEST, `sha256:${"0".repeat(64)}`,
    )).rejects.toThrow("not configured");
  });
});
