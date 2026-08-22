import { describe, expect, it } from "vitest";

import {
  readAcceptanceRequest,
  validateSandboxEvidence,
} from "../src/replay-contract";

async function digestBase64(encoded: string): Promise<string> {
  const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function fixture(): Promise<Record<string, unknown>> {
  const ciphertext = btoa("age-encryption.org/v1\nfixture");
  return {
    schema_version: 1,
    request_id: "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
    runner_nonce: "1".repeat(64),
    archive_ciphertext_sha256: await digestBase64(ciphertext),
    ciphertext_base64: ciphertext,
    plaintext_identity_base64: btoa("AGE-SECRET-KEY-1FIXTURE"),
    marker_sha256: "2".repeat(64),
  };
}

describe("private replay staging contract", () => {
  it("accepts one canonical digest-bound request", async () => {
    const value = await fixture();
    const request = new Request("https://example.test/api/v1/staging-acceptance", {
      method: "POST",
      body: JSON.stringify(value),
    });
    await expect(readAcceptanceRequest(request)).resolves.toEqual(value);
  });

  it("rejects digest drift, extra fields, and oversized declared input", async () => {
    const drift = { ...(await fixture()), archive_ciphertext_sha256: "0".repeat(64) };
    await expect(readAcceptanceRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(drift),
    }))).rejects.toThrow("digest does not match");

    const extra = { ...(await fixture()), secret: "surprise" };
    await expect(readAcceptanceRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(extra),
    }))).rejects.toThrow("fields are not canonical");

    await expect(readAcceptanceRequest(new Request("https://example.test", {
      method: "POST",
      headers: { "content-length": "999999" },
      body: "{}",
    }))).rejects.toThrow("size limit");
  });

  it("requires exact source-free sandbox evidence", async () => {
    const value = await fixture();
    const request = await readAcceptanceRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(value),
    }));
    const evidence = {
      schema_version: 1,
      archive_ciphertext_sha256: request.archive_ciphertext_sha256,
      marker_sha256: request.marker_sha256,
      network_probe: "blocked",
      architecture: "x86_64",
      kernel_release: "fixture-kernel",
      cpu_model: "fixture-cpu",
    };
    expect(validateSandboxEvidence(evidence, request)).toEqual(evidence);
    expect(() => validateSandboxEvidence({ ...evidence, network_probe: "reachable" }, request))
      .toThrow("disabled network");
  });
});
