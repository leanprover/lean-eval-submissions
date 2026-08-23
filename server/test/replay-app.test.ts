import { describe, expect, it } from "vitest";

import { handleReplayRequest, type ReplayRuntimeEnv } from "../src/replay-app";

async function input(): Promise<Record<string, unknown>> {
  const ciphertext = btoa("age-encryption.org/v1\nfixture");
  const bytes = Uint8Array.from(atob(ciphertext), (character) => character.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return {
    schema_version: 1,
    request_id: "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
    runner_nonce: "1".repeat(64),
    archive_ciphertext_sha256: [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0")).join(""),
    ciphertext_base64: ciphertext,
    plaintext_identity_base64: btoa("AGE-SECRET-KEY-1FIXTURE"),
    marker_sha256: "2".repeat(64),
  };
}

async function archiveInput(): Promise<Record<string, unknown>> {
  const ciphertext = btoa("age-encryption.org/v1\naccepted-fixture");
  const bytes = Uint8Array.from(atob(ciphertext), (character) => character.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return {
    schema_version: 1,
    request_id: "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
    runner_nonce: "1".repeat(64),
    submission_id: "01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584",
    archive_ciphertext_sha256: [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0")).join(""),
    plaintext_tar_sha256: "2".repeat(64),
    plaintext_tar_size: 712,
    ciphertext_base64: ciphertext,
    plaintext_identity_base64: btoa("AGE-SECRET-KEY-1FIXTURE"),
  };
}

const ENV = {
  DEPLOYED_COMMIT: "a".repeat(40),
  DEPLOYMENT_ENVIRONMENT: "staging",
  REPLAY_ENABLED: "false",
  STAGING_ACCEPTANCE_ENABLED: "true",
  STAGING_MEMORY_LIMIT_BYTES: "12884901888",
  PRODUCTION_MEMORY_GATE_BYTES: "12884901888",
  GITHUB_OIDC_AUDIENCE: "lean-eval-replay-staging",
  GITHUB_OIDC_ENVIRONMENT: "replay-staging",
} as ReplayRuntimeEnv;

describe("Cloudflare replay executor", () => {
  it("keeps production execution disabled in public health", async () => {
    const response = await handleReplayRequest(new Request("https://example.test/healthz"), ENV);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      replay_enabled: false,
      staging_acceptance_enabled: true,
      staging_memory_limit_bytes: 12_884_901_888,
      production_memory_gate_bytes: 12_884_901_888,
    });
  });

  it("runs a fixed command and confirms destruction before returning evidence", async () => {
    const body = await input();
    const writes: string[] = [];
    const commands: string[] = [];
    let destroyed = false;
    const response = await handleReplayRequest(new Request("https://example.test/api/v1/staging-acceptance", {
      method: "POST",
      body: JSON.stringify(body),
    }), ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path) => {
          writes.push(path);
          return Promise.resolve({ success: true, path, timestamp: "fixture" });
        },
        exec: (command) => {
          commands.push(command);
          return Promise.resolve({
            success: true,
            exitCode: 0,
            stdout: JSON.stringify({
              schema_version: 1,
              archive_ciphertext_sha256: body.archive_ciphertext_sha256,
              marker_sha256: body.marker_sha256,
              network_probe: "blocked",
              architecture: "x86_64",
              kernel_release: "fixture-kernel",
              cpu_model: "fixture-cpu",
            }),
            stderr: "",
            command,
            duration: 1,
            timestamp: "fixture",
          });
        },
        destroy: () => {
          destroyed = true;
          return Promise.resolve();
        },
      }),
    });
    expect(response.status).toBe(200);
    expect(commands).toEqual(["/opt/lean-eval/replay-staging-acceptance"]);
    expect(writes).toEqual([
      "/workspace/archive.tar.gz.age.b64",
      "/workspace/identity.age.b64",
      "/workspace/expectation.json",
    ]);
    expect(destroyed).toBe(true);
    expect(await response.json()).toMatchObject({ destruction: "confirmed", network_policy: "disabled" });
  });

  it("attests one accepted archive with a separate fixed command", async () => {
    const body = await archiveInput();
    const writes: string[] = [];
    const commands: string[] = [];
    let destroyed = false;
    const response = await handleReplayRequest(new Request(
      "https://example.test/api/v1/staging-archive-acceptance",
      { method: "POST", body: JSON.stringify(body) },
    ), ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path) => {
          writes.push(path);
          return Promise.resolve({ success: true, path, timestamp: "fixture" });
        },
        exec: (command) => {
          commands.push(command);
          return Promise.resolve({
            success: true,
            exitCode: 0,
            stdout: JSON.stringify({
              schema_version: 1,
              submission_id: body.submission_id,
              archive_ciphertext_sha256: body.archive_ciphertext_sha256,
              plaintext_tar_sha256: body.plaintext_tar_sha256,
              plaintext_tar_size: body.plaintext_tar_size,
              network_probe: "blocked",
              architecture: "x86_64",
              kernel_release: "fixture-kernel",
              cpu_model: "fixture-cpu",
            }),
            stderr: "",
            command,
            duration: 1,
            timestamp: "fixture",
          });
        },
        destroy: () => {
          destroyed = true;
          return Promise.resolve();
        },
      }),
    });
    expect(response.status).toBe(200);
    expect(commands).toEqual(["/opt/lean-eval/replay-archive-acceptance"]);
    expect(writes).toEqual([
      "/workspace/archive.tar.gz.age.b64",
      "/workspace/identity.age.b64",
      "/workspace/archive-expectation.json",
    ]);
    expect(destroyed).toBe(true);
    expect(await response.json()).toMatchObject({
      submission_id: body.submission_id,
      destruction: "confirmed",
      network_policy: "disabled",
    });
  });

  it("destroys the sandbox on execution failure without exposing diagnostics", async () => {
    let destroyed = false;
    const response = await handleReplayRequest(new Request("https://example.test/api/v1/staging-acceptance", {
      method: "POST",
      body: JSON.stringify(await input()),
    }), ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
        exec: () => Promise.reject(new Error("private identity fixture")),
        destroy: () => {
          destroyed = true;
          return Promise.resolve();
        },
      }),
    });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "executor_failed" });
    expect(destroyed).toBe(true);
  });
});
