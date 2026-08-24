import { describe, expect, it } from "vitest";

import { AuthError, type BrowserSession } from "../src/auth";
import {
  authenticateMaintainer,
  decodeMaintainerIdentities,
} from "../src/maintainer";

const SESSION: BrowserSession = {
  kind: "browser_session",
  github_id: 477956,
  login: "kim-em",
  issued_at: 1,
  expires_at: 2,
};

describe("maintainer identity boundary", () => {
  it("requires the stable GitHub ID and canonical login to match one reviewed pair", () => {
    const configured = JSON.stringify([
      { github_id: 477956, login: "kim-em" },
      { github_id: 123, login: "other-maintainer" },
    ]);
    expect(authenticateMaintainer(configured, SESSION)).toEqual({
      github_id: 477956,
      login: "kim-em",
    });
    expect(() => authenticateMaintainer(configured, { ...SESSION, login: "renamed" }))
      .toThrow(AuthError);
    expect(() => authenticateMaintainer(configured, { ...SESSION, github_id: 123 }))
      .toThrow(AuthError);
  });

  it("fails closed on absent, malformed, oversized, duplicate, or open configuration", () => {
    const invalid = [
      undefined,
      "not-json",
      "{}",
      JSON.stringify(Array.from({ length: 17 }, (_, github_id) => ({
        github_id: github_id + 1,
        login: `maintainer-${String(github_id)}`,
      }))),
      JSON.stringify([{ github_id: 1, login: "maintainer", role: "admin" }]),
      JSON.stringify([{ github_id: 0, login: "maintainer" }]),
      JSON.stringify([{ github_id: 1, login: "Maintainer" }]),
      JSON.stringify([
        { github_id: 1, login: "first" },
        { github_id: 1, login: "second" },
      ]),
      JSON.stringify([
        { github_id: 1, login: "same" },
        { github_id: 2, login: "same" },
      ]),
      `[${" ".repeat(2048)}]`,
    ];
    for (const configured of invalid) {
      expect(() => decodeMaintainerIdentities(configured)).toThrow(TypeError);
    }
  });

  it("accepts an empty reviewed set without authorizing anyone", () => {
    expect(decodeMaintainerIdentities("[]")).toEqual([]);
    expect(() => authenticateMaintainer("[]", SESSION)).toThrow(AuthError);
  });
});
