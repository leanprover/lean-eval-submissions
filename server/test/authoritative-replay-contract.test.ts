import { describe, expect, it } from "vitest";

import {
  readAuthoritativeReplayRequest,
  validateReplayVerdict,
} from "../src/authoritative-replay-contract";

const PROFILE_DIGEST = "4".repeat(64);
const MEASUREMENT_DIGEST = "5".repeat(64);
const VM_IMAGE_DIGEST = `sha256:${"6".repeat(64)}`;

async function fixture(): Promise<Record<string, unknown>> {
  const ciphertextBase64 = btoa("age-encryption.org/v1\nauthoritative-fixture");
  const bytes = Uint8Array.from(atob(ciphertextBase64), (character) => character.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const archiveDigest = [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
  const submissionId = "01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584";
  return {
    schema_version: 1,
    runner_nonce: "1".repeat(64),
    request: {
      replay_task_id: `rt1_${"2".repeat(64)}`,
      attempt: 1,
      execution_profile_digest: PROFILE_DIGEST,
      measurement_config_digest: MEASUREMENT_DIGEST,
      execution_profile: { vm_image_digest: VM_IMAGE_DIGEST },
      source: {
        visibility: "private",
        archive: {
          submission_id: submissionId,
          archive_ciphertext_sha256: archiveDigest,
        },
      },
      result: { submission_id: submissionId },
    },
    archive_expectation: {
      schema_version: 1,
      submission_id: submissionId,
      archive_ciphertext_sha256: archiveDigest,
      plaintext_tar_sha256: "3".repeat(64),
      plaintext_tar_size: 712,
    },
    ciphertext_base64: ciphertextBase64,
    plaintext_identity_base64: btoa("AGE-SECRET-KEY-1FIXTURE"),
  };
}

function verdict(input: Awaited<ReturnType<typeof readAuthoritativeReplayRequest>>): Record<string, unknown> {
  return {
    schema_version: 1,
    replay_task_id: input.request.replay_task_id,
    attempt: input.request.attempt,
    execution_outcome: "completed",
    checker_outcome: "accepted",
    failure_reason: null,
    statistics: {
      checker_wall_time_ms: 10,
      checker_retired_instructions: { status: "measured", value: 20 },
      build_wall_time_ms: 30,
      build_retired_instructions: { status: "unavailable", reason: "counter_permission_denied" },
      lines_of_code: 2,
      file_count: 1,
    },
  };
}

describe("authoritative replay boundary contract", () => {
  it("binds the reviewed profile, encrypted archive, and one execution", async () => {
    const input = await readAuthoritativeReplayRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(await fixture()),
    }), PROFILE_DIGEST, MEASUREMENT_DIGEST, VM_IMAGE_DIGEST);
    expect(input.request.execution_profile_digest).toBe(PROFILE_DIGEST);
    expect(validateReplayVerdict(verdict(input), input).checker_outcome).toBe("accepted");
    const zeroCounter = verdict(input);
    const statistics = zeroCounter.statistics as Record<string, unknown>;
    statistics.checker_retired_instructions = { status: "measured", value: 0 };
    expect(validateReplayVerdict(zeroCounter, input).checker_outcome).toBe("accepted");
  });

  it("rejects unreviewed profiles and verdict identity drift", async () => {
    await expect(readAuthoritativeReplayRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(await fixture()),
    }), "9".repeat(64), MEASUREMENT_DIGEST, VM_IMAGE_DIGEST)).rejects.toThrow("not reviewed");
    const input = await readAuthoritativeReplayRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(await fixture()),
    }), PROFILE_DIGEST, MEASUREMENT_DIGEST, VM_IMAGE_DIGEST);
    expect(() => validateReplayVerdict({ ...verdict(input), attempt: 2 }, input))
      .toThrow("does not match");
  });

  it("rejects placeholder reviewed digests before reading the request", async () => {
    const request = () => new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify({}),
    });
    await expect(readAuthoritativeReplayRequest(
      request(), "0".repeat(64), MEASUREMENT_DIGEST, VM_IMAGE_DIGEST,
    )).rejects.toThrow("not configured");
    await expect(readAuthoritativeReplayRequest(
      request(), PROFILE_DIGEST, "0".repeat(64), VM_IMAGE_DIGEST,
    )).rejects.toThrow("not configured");
    await expect(readAuthoritativeReplayRequest(
      request(), PROFILE_DIGEST, MEASUREMENT_DIGEST, `sha256:${"0".repeat(64)}`,
    )).rejects.toThrow("not configured");
  });

  it("rejects a profile whose VM image differs from the reviewed manifest", async () => {
    await expect(readAuthoritativeReplayRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(await fixture()),
    }), PROFILE_DIGEST, MEASUREMENT_DIGEST, `sha256:${"7".repeat(64)}`))
      .rejects.toThrow("VM image is not reviewed");
  });

  it("keeps decline, crash, and timeout outcomes distinct", async () => {
    const input = await readAuthoritativeReplayRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(await fixture()),
    }), PROFILE_DIGEST, MEASUREMENT_DIGEST, VM_IMAGE_DIGEST);
    expect(validateReplayVerdict({
      ...verdict(input),
      checker_outcome: "declined",
    }, input).checker_outcome).toBe("declined");
    for (const execution_outcome of ["crashed", "timed_out"] as const) {
      expect(validateReplayVerdict({
        ...verdict(input),
        execution_outcome,
        checker_outcome: null,
      }, input).execution_outcome).toBe(execution_outcome);
    }
    expect(() => validateReplayVerdict({
      ...verdict(input),
      execution_outcome: "crashed",
      checker_outcome: "rejected",
    }, input)).toThrow("does not match");
  });
});
