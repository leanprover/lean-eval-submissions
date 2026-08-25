import { describe, expect, it } from "vitest";

import {
  qualificationFixtureDigest,
  reviewedQualificationFixtureManifest,
  verifyQualificationFixtureManifest,
} from "../src/model-identity-qualification-fixture";

describe("model identity qualification fixture arming", () => {
  it("is source-unarmed until a reviewed live manifest and digest are committed", async () => {
    await expect(reviewedQualificationFixtureManifest()).resolves.toBeNull();
  });

  it("uses a key-order-independent domain-separated digest", async () => {
    await expect(qualificationFixtureDigest({ beta: 2, alpha: 1 })).resolves
      .toBe(await qualificationFixtureDigest({ alpha: 1, beta: 2 }));
    await expect(qualificationFixtureDigest({ alpha: 1 })).resolves.not
      .toBe(await qualificationFixtureDigest({ alpha: 2 }));
  });

  it("rejects source-test-only material at the live runtime verifier", async () => {
    const sourceTestOnly = {
      schema_version: 1,
      kind: "model_identity_qualification_test_fixture",
      evidence_class: "source_test_only",
    };
    await expect(verifyQualificationFixtureManifest(
      sourceTestOnly,
      await qualificationFixtureDigest(sourceTestOnly),
    )).rejects.toThrow("fields are invalid");
  });
});
