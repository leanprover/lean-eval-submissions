import { describe, expect, it } from "vitest";

import { historicalReceiptObjectName } from "../src/replay-sandbox";

describe("historical replay sandbox identity", () => {
  it("allows four total attempts and rejects attempt five", () => {
    const replayTaskId = `rt1_${"a".repeat(64)}`;
    expect(historicalReceiptObjectName(replayTaskId, 4)).toMatch(/-4$/u);
    expect(() => historicalReceiptObjectName(replayTaskId, 5)).toThrow(
      "historical cleanup identity is invalid",
    );
  });
});
