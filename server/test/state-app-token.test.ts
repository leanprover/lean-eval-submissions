import { describe, expect, it, vi } from "vitest";
import type { GitHubFetch } from "../src/github-state";
import { stateAppToken } from "../src/state-app-token";
import { stateAppEnv, stateAppTokenReplies } from "./state-app";

describe("State App token", () => {
  it("mints a Contents-write token for the State repository only and reuses it", async () => {
    const env = await stateAppEnv();
    const replies = stateAppTokenReplies();
    const fetcher = vi.fn<GitHubFetch>(() => Promise.resolve(replies.shift() ?? Response.error()));
    const token = stateAppToken(
      { appId: env.STATE_APP_ID, privateKey: env.STATE_APP_PRIVATE_KEY },
      "leanprover/lean-eval-state",
      fetcher,
    );

    expect(await token()).toBe("ghs_state-installation-token");
    expect(await token()).toBe("ghs_state-installation-token");
    expect(fetcher).toHaveBeenCalledTimes(2);
    const [lookup, exchange] = fetcher.mock.calls;
    expect(lookup?.[0]).toBe("https://api.github.com/repos/leanprover/lean-eval-state/installation");
    expect(exchange?.[0]).toBe("https://api.github.com/app/installations/77/access_tokens");
    expect(JSON.parse(exchange?.[1]?.body as string)).toEqual({
      repositories: ["lean-eval-state"],
      permissions: { contents: "write", metadata: "read" },
    });
  });

  it("renews a token that is about to expire", async () => {
    const env = await stateAppEnv();
    const soon = new Date(Date.now() + 60_000).toISOString();
    const replies = [
      Response.json({ id: 77 }),
      Response.json({ token: "ghs_expiring", expires_at: soon }),
      ...stateAppTokenReplies(),
    ];
    const fetcher = vi.fn<GitHubFetch>(() => Promise.resolve(replies.shift() ?? Response.error()));
    const token = stateAppToken(
      { appId: env.STATE_APP_ID, privateKey: env.STATE_APP_PRIVATE_KEY },
      "leanprover/lean-eval-state-staging",
      fetcher,
    );

    expect(await token()).toBe("ghs_expiring");
    expect(await token()).toBe("ghs_state-installation-token");
    expect(fetcher).toHaveBeenCalledTimes(4);
  });
});
