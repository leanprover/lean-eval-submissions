import { describe, expect, it, vi } from "vitest";
import type { GitHubFetch } from "../src/github-state";
import { stateAppToken } from "../src/state-app-token";
import { stateAppEnv, stateAppTokenReplies } from "./state-app";

async function app() {
  const env = await stateAppEnv();
  return { appId: env.STATE_APP_ID, privateKey: env.STATE_APP_PRIVATE_KEY };
}

function replying(replies: Response[]) {
  return vi.fn<GitHubFetch>(() => Promise.resolve(replies.shift() ?? Response.error()));
}

describe("State App token", () => {
  it("mints a Contents-write token for the State repository only, once per client", async () => {
    const fetcher = replying(stateAppTokenReplies());
    const token = stateAppToken(await app(), "leanprover/lean-eval-state", fetcher);

    const [first, second] = await Promise.all([token(), token()]);
    expect([first, second]).toEqual(["ghs_state-installation-token", "ghs_state-installation-token"]);
    expect(fetcher).toHaveBeenCalledTimes(2);
    const [lookup, exchange] = fetcher.mock.calls;
    expect(lookup?.[0]).toBe("https://api.github.com/repos/leanprover/lean-eval-state/installation");
    expect(exchange?.[0]).toBe("https://api.github.com/app/installations/77/access_tokens");
    expect(JSON.parse(exchange?.[1]?.body as string)).toEqual({
      repositories: ["lean-eval-state"],
      permissions: { contents: "write", metadata: "read" },
    });
  });

  it("reuses a cached token across clients and renews one about to expire", async () => {
    const credentials = await app();
    const repository = "leanprover/lean-eval-state-staging";
    const soon = new Date(Date.now() + 60_000).toISOString();
    const fetcher = replying([
      Response.json({ id: 77 }),
      Response.json({ token: "ghs_expiring", expires_at: soon, permissions: { contents: "write" } }),
      ...stateAppTokenReplies(),
    ]);

    expect(await stateAppToken(credentials, repository, fetcher)()).toBe("ghs_expiring");
    expect(await stateAppToken(credentials, repository, fetcher)()).toBe("ghs_state-installation-token");
    expect(await stateAppToken(credentials, repository, fetcher)()).toBe("ghs_state-installation-token");
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("refuses a token GitHub did not grant Contents write", async () => {
    const fetcher = replying([
      Response.json({ id: 77 }),
      Response.json({
        token: "ghs_read-only",
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        permissions: { contents: "read" },
      }),
    ]);
    await expect(stateAppToken(await app(), "leanprover/lean-eval-state", fetcher)())
      .rejects.toMatchObject({ status: 403 });
  });

  it("reports an unusable key as unavailable State", async () => {
    const token = stateAppToken(
      { appId: "1", privateKey: "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----" },
      "leanprover/lean-eval-state",
      replying([]),
    );
    await expect(token()).rejects.toMatchObject({ name: "GitHubStateError", status: 503 });
  });
});
