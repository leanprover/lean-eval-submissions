import { describe, expect, it } from "vitest";

import {
  readArchiveAcceptanceRequest,
  validateArchiveEvidence,
} from "../src/replay-archive-contract";

async function digestBase64(encoded: string): Promise<string> {
  const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function fixture(): Promise<Record<string, unknown>> {
  const ciphertext = btoa("age-encryption.org/v1\naccepted-fixture");
  return {
    schema_version: 1,
    request_id: "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
    runner_nonce: "1".repeat(64),
    submission_id: "01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584",
    archive_ciphertext_sha256: await digestBase64(ciphertext),
    plaintext_tar_sha256: "2".repeat(64),
    plaintext_tar_size: 712,
    ciphertext_base64: ciphertext,
    plaintext_identity_base64: btoa("AGE-SECRET-KEY-1FIXTURE"),
  };
}

describe("accepted archive replay boundary contract", () => {
  it("accepts one submission-bound encrypted archive", async () => {
    const value = await fixture();
    await expect(readArchiveAcceptanceRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(value),
    }))).resolves.toEqual(value);
  });

  it("rejects digest drift and invalid plaintext bounds", async () => {
    const drift = { ...(await fixture()), archive_ciphertext_sha256: "0".repeat(64) };
    await expect(readArchiveAcceptanceRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(drift),
    }))).rejects.toThrow("digest does not match");
    const oversized = { ...(await fixture()), plaintext_tar_size: 10 * 1024 * 1024 + 1 };
    await expect(readArchiveAcceptanceRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(oversized),
    }))).rejects.toThrow("plaintext_tar_size");
  });

  it("requires evidence bound to every nonsecret archive field", async () => {
    const request = await readArchiveAcceptanceRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(await fixture()),
    }));
    const evidence = {
      schema_version: 1,
      submission_id: request.submission_id,
      archive_ciphertext_sha256: request.archive_ciphertext_sha256,
      plaintext_tar_sha256: request.plaintext_tar_sha256,
      plaintext_tar_size: request.plaintext_tar_size,
      network_probe: "blocked",
      architecture: "x86_64",
      kernel_release: "fixture-kernel",
      cpu_model: "fixture-cpu",
    };
    expect(validateArchiveEvidence(evidence, request)).toEqual(evidence);
    expect(() => validateArchiveEvidence({ ...evidence, plaintext_tar_size: 711 }, request))
      .toThrow("does not match request");
  });
});
