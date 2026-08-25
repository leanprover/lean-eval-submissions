import { describe, expect, it } from "vitest";

import {
  makeAgentChallenge,
  makeAgentSession,
  signToken,
  verifyToken,
  verifyUserSession,
  type AgentSession,
  type BrowserSession,
} from "../src/auth";

const NOW = 1_800_000_000;
const SECRET = "test-only-agent-session-signing-secret";

function challenge() {
  return makeAgentChallenge({
    login: "owner",
    source_repository: "owner/private-proof",
    source_commit: "a".repeat(40),
    gist_id: "abcde",
  }, NOW);
}

describe("source-bound agent sessions", () => {
  it("retains the exact secret-gist/tag/source-commit proof in the signed session", async () => {
    const session = makeAgentSession({ id: 42, login: "owner" }, challenge(), NOW);
    const token = await signToken(SECRET, session);
    await expect(verifyToken<AgentSession>(SECRET, token, "agent_session", NOW))
      .resolves.toEqual(session);
    await expect(verifyUserSession(SECRET, token, NOW)).resolves.toEqual(session);
    expect(session).toMatchObject({
      kind: "agent_session",
      github_id: 42,
      login: "owner",
      source_repository: "owner/private-proof",
      source_commit: "a".repeat(40),
      proof_kind: "secret_gist_tag_source_commit_v1",
    });
    await expect(verifyToken<BrowserSession>(SECRET, token, "browser_session", NOW))
      .rejects.toThrow(/fields|purpose/);
  });

  it("still accepts a separately issued OAuth browser session", async () => {
    const session: BrowserSession = {
      kind: "browser_session",
      login: "owner",
      github_id: 42,
      issued_at: NOW,
      expires_at: NOW + 3600,
    };
    const token = await signToken(SECRET, session);
    await expect(verifyUserSession(SECRET, token, NOW)).resolves.toEqual(session);
  });

  it("rejects identity drift and malformed source bindings", async () => {
    expect(() => makeAgentSession({ id: 42, login: "other-owner" }, challenge(), NOW))
      .toThrow(/identity/);
    const malformed: AgentSession = {
      ...makeAgentSession({ id: 42, login: "owner" }, challenge(), NOW),
      source_commit: "A".repeat(40),
    };
    await expect(verifyToken<AgentSession>(
      SECRET,
      await signToken(SECRET, malformed),
      "agent_session",
      NOW,
    )).rejects.toThrow(/source proof/);
  });
});
