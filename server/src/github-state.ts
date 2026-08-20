import { stateEventPath, type StateEvent, validateStateEvent } from "./state-event";

const API = "https://api.github.com";
const STATE_BRANCH = "main";
const MAX_WRITE_ATTEMPTS = 8;
const SHA = /^[0-9a-f]{40}$/i;
const GITHUB_TIMEOUT_MS = 5000;

export type GitHubFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

const defaultGitHubFetch: GitHubFetch = (input, init) =>
  fetch(input, {
    ...init,
    signal: init?.signal ?? AbortSignal.timeout(GITHUB_TIMEOUT_MS),
  });

type GitHubStateConfig = Readonly<{
  repository: string;
  token: string;
  userAgent: string;
}>;

type BranchSnapshot = Readonly<{
  headSha: string;
  treeSha: string;
}>;

export class GitHubStateError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(`GitHub State ${String(status)}: ${message}`);
    this.name = "GitHubStateError";
    this.status = status;
  }
}

export class StateUpdateOutcomeUnknownError extends Error {
  constructor() {
    super("GitHub State reference update outcome is unknown");
    this.name = "StateUpdateOutcomeUnknownError";
  }
}

export class StateEventConflictError extends Error {
  constructor(path: string) {
    super(`immutable State event already exists at ${path}`);
    this.name = "StateEventConflictError";
  }
}

function headers(config: GitHubStateConfig): Headers {
  return new Headers({
    accept: "application/vnd.github+json",
    authorization: `Bearer ${config.token}`,
    "user-agent": config.userAgent,
    "x-github-api-version": "2022-11-28",
  });
}

function repoPath(config: GitHubStateConfig, path: string): string {
  return `${API}/repos/${config.repository}${path}`;
}

async function responseError(response: Response): Promise<GitHubStateError> {
  return new GitHubStateError(response.status, (await response.text()).slice(0, 300));
}

async function jsonCall(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  path: string,
  init: RequestInit = {},
): Promise<unknown> {
  const requestHeaders = headers(config);
  new Headers(init.headers).forEach((value, key) => requestHeaders.set(key, value));
  const response = await fetcher(repoPath(config, path), {
    ...init,
    headers: requestHeaders,
  });
  if (response.status === 404) return null;
  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return true;
  return response.json<unknown>();
}

function object(value: unknown, description: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new GitHubStateError(502, `${description} was not an object`);
  }
  return value as Record<string, unknown>;
}

function requiredSha(value: unknown, description: string): string {
  if (typeof value !== "string" || !SHA.test(value)) {
    throw new GitHubStateError(502, `${description} was not a commit SHA`);
  }
  return value;
}

function nested(value: unknown, keys: readonly string[]): unknown {
  let cursor = value;
  for (const key of keys) cursor = object(cursor, keys.join("."))[key];
  return cursor;
}

async function branchSnapshot(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
): Promise<BranchSnapshot> {
  const ref = await jsonCall(config, fetcher, `/git/ref/heads/${STATE_BRANCH}`);
  const headSha = requiredSha(nested(ref, ["object", "sha"]), "State branch head");
  const commit = await jsonCall(config, fetcher, `/git/commits/${headSha}`);
  const treeSha = requiredSha(nested(commit, ["tree", "sha"]), "State commit tree");
  return { headSha, treeSha };
}

function decodeInlineJson(value: unknown, path: string): unknown {
  const data = object(value, `${path} contents response`);
  if (data.encoding !== "base64" || typeof data.content !== "string") {
    throw new GitHubStateError(502, `${path} did not contain inline base64 JSON`);
  }
  try {
    const binary = atob(data.content.replaceAll("\n", ""));
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes)) as unknown;
  } catch (error) {
    throw new GitHubStateError(502, `${path} contained invalid JSON: ${String(error)}`);
  }
}

async function readPathAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  path: string,
  commit: string,
): Promise<{ found: false } | { found: true; value: unknown }> {
  const query = new URLSearchParams({ ref: commit });
  const value = await jsonCall(
    config,
    fetcher,
    `/contents/${encodeURI(path)}?${query.toString()}`,
  );
  return value === null
    ? { found: false }
    : { found: true, value: decodeInlineJson(value, path) };
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`;
  }
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "number" ||
    typeof value === "string"
  ) {
    return JSON.stringify(value);
  }
  throw new TypeError("State event is not JSON serializable");
}

async function createCommit(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  snapshot: BranchSnapshot,
  path: string,
  event: StateEvent,
): Promise<string> {
  const tree = await jsonCall(config, fetcher, "/git/trees", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      base_tree: snapshot.treeSha,
      tree: [
        {
          path,
          mode: "100644",
          type: "blob",
          content: `${JSON.stringify(event, null, 2)}\n`,
        },
      ],
    }),
  });
  const treeSha = requiredSha(object(tree, "created State tree").sha, "created State tree");
  const commit = await jsonCall(config, fetcher, "/git/commits", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      message: `Record ${event.event_type} ${event.event_id}`,
      parents: [snapshot.headSha],
      tree: treeSha,
    }),
  });
  return requiredSha(object(commit, "created State commit").sha, "created State commit");
}

async function isCommitReachable(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  commit: string,
): Promise<boolean> {
  const ref = await jsonCall(config, fetcher, `/git/ref/heads/${STATE_BRANCH}`);
  const head = requiredSha(nested(ref, ["object", "sha"]), "State branch head");
  if (head === commit) return true;
  const comparison = await jsonCall(config, fetcher, `/compare/${commit}...${head}`);
  const data = object(comparison, "State comparison");
  return (
    (data.status === "ahead" || data.status === "identical") &&
    nested(data, ["merge_base_commit", "sha"]) === commit
  );
}

async function updateReference(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  commit: string,
): Promise<"applied" | "collision"> {
  const requestHeaders = headers(config);
  requestHeaders.set("content-type", "application/json");
  const request: RequestInit = {
    method: "PATCH",
    headers: requestHeaders,
    body: JSON.stringify({ sha: commit, force: false }),
  };
  let response: Response | null = null;
  let uncertain = false;
  try {
    response = await fetcher(repoPath(config, `/git/refs/heads/${STATE_BRANCH}`), request);
  } catch {
    uncertain = true;
  }
  if (response !== null && response.status >= 500) uncertain = true;
  if (uncertain) {
    try {
      response = await fetcher(repoPath(config, `/git/refs/heads/${STATE_BRANCH}`), request);
    } catch {
      response = null;
    }
  }
  if (response?.ok) return "applied";
  if (uncertain) {
    try {
      if (await isCommitReachable(config, fetcher, commit)) return "applied";
    } catch {
      throw new StateUpdateOutcomeUnknownError();
    }
  }
  if (response?.status === 409) return "collision";
  if (response?.status === 422) {
    const detail = (await response.text()).slice(0, 300);
    if (/not a fast.?forward/i.test(detail)) return "collision";
    throw new GitHubStateError(response.status, detail || "State reference update rejected");
  }
  if (response === null) throw new StateUpdateOutcomeUnknownError();
  throw await responseError(response);
}

function pause(attempt: number): Promise<void> {
  const spread = crypto.getRandomValues(new Uint8Array(1))[0] ?? 0;
  const delay = Math.round(Math.min(2 ** attempt * 80, 800) * (0.5 + spread / 255));
  return new Promise((resolve) => setTimeout(resolve, delay));
}

export class GitHubStateRepository {
  readonly #config: GitHubStateConfig;
  readonly #fetcher: GitHubFetch;

  constructor(config: GitHubStateConfig, fetcher: GitHubFetch = defaultGitHubFetch) {
    if (!/^[A-Za-z\d_.-]+\/[A-Za-z\d_.-]+$/.test(config.repository)) {
      throw new TypeError("State repository must be an owner/name pair");
    }
    this.#config = config;
    this.#fetcher = fetcher;
  }

  async assertAvailable(): Promise<void> {
    const repository = object(
      await jsonCall(this.#config, this.#fetcher, ""),
      "State repository",
    );
    const permissions = object(repository.permissions, "State repository permissions");
    if (permissions.push !== true) {
      throw new GitHubStateError(403, "State credential does not have push permission");
    }
    await branchSnapshot(this.#config, this.#fetcher);
  }

  async appendEvent(event: StateEvent): Promise<{ commit: string; path: string }> {
    validateStateEvent(event);
    const path = stateEventPath(event);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const existing = await readPathAt(this.#config, this.#fetcher, path, snapshot.headSha);
      if (existing.found) {
        try {
          validateStateEvent(existing.value);
        } catch {
          throw new StateEventConflictError(path);
        }
        if (canonicalJson(existing.value) === canonicalJson(event)) {
          return { commit: snapshot.headSha, path };
        }
        throw new StateEventConflictError(path);
      }
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        path,
        event,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, path };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new GitHubStateError(409, "State branch kept changing underneath the append");
      }
      await pause(attempt);
    }
    throw new Error("unreachable State append attempt");
  }
}
