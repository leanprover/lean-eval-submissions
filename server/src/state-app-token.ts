import { appJwt, type GitHubApp } from "./github-broker";
import { GitHubStateError, type GitHubFetch } from "./github-state";

const API = "https://api.github.com";

// An installation token draws on the State App's own rate limit, so State
// stays available when a maintainer's personal allowance is exhausted. Only
// the token string and its expiry outlive a request.
const cache = new Map<string, Readonly<{ token: string; expiresAt: number }>>();

async function call(fetcher: GitHubFetch, jwt: string, path: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetcher(`${API}${path}`, {
    ...init,
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${jwt}`,
      "content-type": "application/json",
      "user-agent": "lean-eval-submission-worker",
      "x-github-api-version": "2022-11-28",
    },
    redirect: "manual",
  });
  if (!response.ok) {
    throw new GitHubStateError(503, `State App ${path}: ${String(response.status)} ${(await response.text()).slice(0, 300)}`);
  }
  return response.json<unknown>();
}

/**
 * A State credential minted from the environment's State App: Contents write
 * on exactly `repository`, renewed a few minutes before GitHub expires it.
 */
export function stateAppToken(
  app: GitHubApp,
  repository: string,
  fetcher: GitHubFetch,
  now: () => number = Date.now,
): () => Promise<string> {
  return async () => {
    const key = `${app.appId}:${repository.toLowerCase()}`;
    const cached = cache.get(key);
    if (cached && cached.expiresAt - now() > 5 * 60 * 1000) return cached.token;
    const jwt = await appJwt(app, now());
    const installation = await call(fetcher, jwt, `/repos/${repository}/installation`) as { id?: unknown };
    if (!Number.isSafeInteger(installation.id)) throw new GitHubStateError(502, "State App installation ID was invalid");
    const minted = await call(fetcher, jwt, `/app/installations/${String(installation.id)}/access_tokens`, {
      method: "POST",
      body: JSON.stringify({
        repositories: [repository.split("/")[1]],
        permissions: { contents: "write", metadata: "read" },
      }),
    }) as { token?: unknown; expires_at?: unknown };
    const expiresAt = Date.parse(String(minted.expires_at));
    if (typeof minted.token !== "string" || !Number.isFinite(expiresAt)) {
      throw new GitHubStateError(502, "State App token response was invalid");
    }
    cache.set(key, { token: minted.token, expiresAt });
    return minted.token;
  };
}
