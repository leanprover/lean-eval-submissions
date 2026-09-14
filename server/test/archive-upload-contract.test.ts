import { describe, expect, it } from "vitest";

import {
  ArchiveUploadContractError,
  MAX_PART_BYTES,
  readArchiveUploadFinalizeRequest,
  readArchiveUploadPartHeader,
  sameArchiveUploadIdentity,
} from "../src/archive-upload-contract";

const NONCE = "1".repeat(64);
const ARCHIVE_DIGEST = "2".repeat(64);
const PART_DIGEST = "3".repeat(64);

/** Headers for a single-part upload of `archiveBytes`, overridable field by field. */
function partHeaders(overrides: Record<string, string> = {}): Record<string, string> {
  return {
    "content-type": "application/octet-stream",
    "x-lean-eval-upload-kind": "authoritative-archive",
    "x-lean-eval-runner-nonce": NONCE,
    "x-lean-eval-archive-sha256": ARCHIVE_DIGEST,
    "x-lean-eval-archive-bytes": "1024",
    "x-lean-eval-part-count": "1",
    "x-lean-eval-part-index": "0",
    "x-lean-eval-part-sha256": PART_DIGEST,
    "x-lean-eval-part-bytes": "1024",
    ...overrides,
  };
}

function partRequest(overrides: Record<string, string> = {}): Request {
  return new Request("https://example.test/api/v1/replay/archive-part", {
    method: "POST",
    headers: partHeaders(overrides),
    body: new Uint8Array(4),
  });
}

function finalizeBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: 1,
    upload_kind: "authoritative-archive",
    runner_nonce: NONCE,
    archive_sha256: ARCHIVE_DIGEST,
    archive_bytes: 1024,
    part_count: 1,
    parts: [{ index: 0, sha256: PART_DIGEST, bytes: 1024 }],
    ...overrides,
  };
}

function finalizeRequest(overrides: Record<string, unknown> = {}): Request {
  return new Request("https://example.test/api/v1/replay/archive-finalize", {
    method: "POST",
    body: JSON.stringify(finalizeBody(overrides)),
  });
}

describe("chunked archive upload contract", () => {
  it("reads one part's identity without consuming its body", () => {
    const request = partRequest();
    const header = readArchiveUploadPartHeader(request);
    expect(header.upload_kind).toBe("authoritative-archive");
    expect(header.part_index).toBe(0);
    expect(header.archive_bytes).toBe(1024);
    // The caller streams the body; reading it here would leave nothing to write.
    expect(request.bodyUsed).toBe(false);
  });

  it("requires a raw octet-stream body", () => {
    expect(() => readArchiveUploadPartHeader(partRequest({ "content-type": "application/json" })))
      .toThrow("application/octet-stream");
    expect(() => readArchiveUploadPartHeader(partRequest({ "content-encoding": "gzip" })))
      .toThrow("content-encoded");
  });

  it("accepts transport headers but rejects unknown application headers", () => {
    // Cloudflare and proxies add their own headers, so only the x-lean-eval-*
    // namespace is held to an exact set.
    expect(() => readArchiveUploadPartHeader(partRequest({ "x-request-id": "abc" })))
      .not.toThrow();
    expect(() => readArchiveUploadPartHeader(partRequest({ "x-lean-eval-extra": "1" })))
      .toThrow("upload headers are not canonical");
  });

  it("derives the part count from the archive size rather than trusting it", () => {
    // Two parts are declared but one part covers the archive: a shorter upload
    // must not be able to finalize as complete.
    expect(() => readArchiveUploadPartHeader(partRequest({ "x-lean-eval-part-count": "2" })))
      .toThrow("part count does not match");
    const twoParts = partHeaders({
      "x-lean-eval-archive-bytes": String(MAX_PART_BYTES + 10),
      "x-lean-eval-part-count": "2",
      "x-lean-eval-part-index": "1",
      "x-lean-eval-part-bytes": "10",
    });
    expect(readArchiveUploadPartHeader(new Request("https://example.test", {
      method: "POST",
      headers: twoParts,
      body: new Uint8Array(4),
    })).part_bytes).toBe(10);
  });

  it("pins every part's length to its position in the archive", () => {
    // A non-final part is always exactly one part long, so a short one is
    // rejected before any byte reaches the Sandbox.
    const shortFirst = partHeaders({
      "x-lean-eval-archive-bytes": String(MAX_PART_BYTES + 10),
      "x-lean-eval-part-count": "2",
      "x-lean-eval-part-index": "0",
      "x-lean-eval-part-bytes": "5",
    });
    expect(() => readArchiveUploadPartHeader(new Request("https://example.test", {
      method: "POST",
      headers: shortFirst,
      body: new Uint8Array(4),
    }))).toThrow("part size does not match its position");
  });

  it("rejects header integers a Number() coercion would accept", () => {
    for (const value of [" 1", "+1", "1e3", "0x1", "1.0", ""]) {
      expect(() => readArchiveUploadPartHeader(partRequest({ "x-lean-eval-part-index": value })))
        .toThrow("part-index header is invalid");
    }
  });

  it("rejects a malformed digest or unknown upload kind", () => {
    expect(() => readArchiveUploadPartHeader(partRequest({ "x-lean-eval-part-sha256": "nope" })))
      .toThrow("part_sha256 header is invalid");
    expect(() => readArchiveUploadPartHeader(partRequest({ "x-lean-eval-upload-kind": "other" })))
      .toThrow("upload_kind header is invalid");
  });

  it("accepts a complete finalize manifest", async () => {
    const finalize = await readArchiveUploadFinalizeRequest(finalizeRequest());
    expect(finalize.part_count).toBe(1);
    expect(finalize.parts[0]?.sha256).toBe(PART_DIGEST);
    expect(sameArchiveUploadIdentity(finalize, {
      schema_version: 1,
      upload_kind: "authoritative-archive",
      runner_nonce: NONCE,
      archive_sha256: ARCHIVE_DIGEST,
      archive_bytes: 1024,
      part_count: 1,
    })).toBe(true);
  });

  it("requires parts in index order and refuses a reordered manifest", async () => {
    const twoPartBody = finalizeBody({
      archive_bytes: MAX_PART_BYTES + 10,
      part_count: 2,
      parts: [
        { index: 1, sha256: PART_DIGEST, bytes: 10 },
        { index: 0, sha256: PART_DIGEST, bytes: MAX_PART_BYTES },
      ],
    });
    await expect(readArchiveUploadFinalizeRequest(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(twoPartBody),
    }))).rejects.toThrow("parts must be listed in index order");
  });

  it("refuses a manifest whose parts do not add up", async () => {
    await expect(readArchiveUploadFinalizeRequest(finalizeRequest({
      parts: [{ index: 0, sha256: PART_DIGEST, bytes: 512 }],
    }))).rejects.toThrow("part 0 bytes is invalid");
    await expect(readArchiveUploadFinalizeRequest(finalizeRequest({ part_count: 2 })))
      .rejects.toThrow("part count does not match");
  });

  it("refuses unknown or missing finalize fields", async () => {
    await expect(readArchiveUploadFinalizeRequest(finalizeRequest({ extra: 1 })))
      .rejects.toThrow("fields are not canonical");
    await expect(readArchiveUploadFinalizeRequest(new Request("https://example.test", {
      method: "POST",
      body: "not json",
    }))).rejects.toThrow("not one UTF-8 JSON object");
  });

  it("is an ArchiveUploadContractError so routes can map it to 400", () => {
    expect(() => readArchiveUploadPartHeader(partRequest({ "x-lean-eval-upload-kind": "other" })))
      .toThrow(ArchiveUploadContractError);
  });
});
