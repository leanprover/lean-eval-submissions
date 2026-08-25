import type { GitHubFetch } from "./github-state";

const STATE_REPOSITORY = "leanprover/lean-eval-state-staging";
const STATE_BRANCH = "main";
const SHA = /^[0-9a-f]{40}$/;
const JOURNAL_ID = /^mqj_[0-9a-f]{64}$/;
const RECOVERY_NONCE = /^[0-9a-f]{64}$/;
const MAX_RESPONSE_BYTES = 256 * 1024;

export type QualificationStateSnapshot = Readonly<{
  head_commit: string;
  head_tree: string;
}>;

export type QualificationStateRestoration = Readonly<{
  restoration_commit: string;
  restoration_parent_commit: string;
  restoration_parent_tree: string;
  restoration_tree: string;
  ref_head: string;
  fast_forward: true;
  tree_equal: true;
}>;

export type QualificationStateMutation = Readonly<{
  state_commit: string;
  state_tree: string;
  parent_commit: string;
}>;

export type QualificationStateConfig = Readonly<{
  repository: string;
  token: string;
  userAgent: string;
}>;

export type QualificationRestorationRequest = Readonly<{
  journalId: string;
  recoveryNonce: string;
  expectedHead: string;
  expectedTree: string;
  initialTree: string;
}>;

export type QualificationMutationRequest = Readonly<{
  expectedParent: string;
  expectedMessage: string;
  expectedDocuments: Readonly<Record<string, unknown>>;
}>;

export class QualificationStateError extends Error {
  constructor(readonly reason: "foreign_state_movement" | "provider_unavailable") {
    super(reason);
  }
}

type CommitIdentity = Readonly<{
  sha: string;
  tree: string;
  parents: readonly string[];
  message: string;
}>;

function object(value: unknown, label: string): Record<string, unknown> {
  void label;
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new QualificationStateError("provider_unavailable");
  }
  return value as Record<string, unknown>;
}

function nested(value: unknown, keys: readonly string[]): unknown {
  let current = value;
  for (const key of keys) current = object(current, keys.join("."))[key];
  return current;
}

function sha(value: unknown): string {
  if (typeof value !== "string" || !SHA.test(value)) {
    throw new QualificationStateError("provider_unavailable");
  }
  return value;
}

async function responseJson(response: Response): Promise<unknown> {
  const encoded = new Uint8Array(await response.arrayBuffer());
  if (encoded.byteLength > MAX_RESPONSE_BYTES) {
    throw new QualificationStateError("provider_unavailable");
  }
  try {
    return JSON.parse(new TextDecoder().decode(encoded)) as unknown;
  } catch {
    throw new QualificationStateError("provider_unavailable");
  }
}

async function call(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
  path: string,
  init: RequestInit = {},
): Promise<unknown> {
  const response = await fetcher(
    `https://api.github.com/repos/${config.repository}${path}`,
    {
      ...init,
      redirect: "manual",
      headers: {
        accept: "application/vnd.github+json",
        authorization: `Bearer ${config.token}`,
        "user-agent": config.userAgent,
        "x-github-api-version": "2022-11-28",
        ...Object.fromEntries(new Headers(init.headers).entries()),
      },
      signal: init.signal ?? AbortSignal.timeout(10_000),
    },
  );
  if (!response.ok || response.status < 200 || response.status >= 300) {
    throw new QualificationStateError("provider_unavailable");
  }
  return responseJson(response);
}

function configValid(config: QualificationStateConfig): void {
  if (
    config.repository !== STATE_REPOSITORY ||
    new TextEncoder().encode(config.token).byteLength < 32 ||
    !/^lean-eval-model-identity-qualification\/[0-9]+$/.test(config.userAgent)
  ) {
    throw new TypeError("qualification State configuration is invalid");
  }
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (value !== null && typeof value === "object") {
    const source = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(source).sort().map((key) => [key, sortJson(source[key])]),
    );
  }
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "number" ||
    typeof value === "string"
  ) return value;
  throw new TypeError("qualification expected State document is invalid");
}

function canonicalStateDocument(value: unknown): string {
  return `${JSON.stringify(sortJson(value), null, 2)
    .replace(/[\u0080-\uffff]/g, (character) =>
      `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`)}\n`;
}

async function gitBlobSha(content: Uint8Array): Promise<string> {
  const prefix = new TextEncoder().encode(`blob ${String(content.byteLength)}\0`);
  const bytes = new Uint8Array(prefix.byteLength + content.byteLength);
  bytes.set(prefix);
  bytes.set(content, prefix.byteLength);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-1", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function decodeBase64(value: unknown): Uint8Array {
  if (typeof value !== "string" || value.length > MAX_RESPONSE_BYTES * 2) {
    throw new QualificationStateError("provider_unavailable");
  }
  try {
    const binary = atob(value.replaceAll(/\s/g, ""));
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    throw new QualificationStateError("provider_unavailable");
  }
}

async function verifyExactDocument(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
  commit: string,
  path: string,
  expected: unknown,
): Promise<void> {
  if (
    path.startsWith("/") ||
    path.includes("..") ||
    !/^[a-z0-9][a-z0-9./_-]{0,511}$/.test(path)
  ) throw new TypeError("qualification expected State path is invalid");
  const query = new URLSearchParams({ ref: commit });
  const value = object(
    await call(config, fetcher, `/contents/${encodeURI(path)}?${query.toString()}`),
    "qualification State document",
  );
  const bytes = decodeBase64(value.content);
  const expectedBytes = new TextEncoder().encode(canonicalStateDocument(expected));
  if (
    value.type !== "file" ||
    value.path !== path ||
    value.encoding !== "base64" ||
    typeof value.sha !== "string" ||
    !SHA.test(value.sha) ||
    value.sha !== await gitBlobSha(expectedBytes) ||
    bytes.byteLength !== expectedBytes.byteLength ||
    !crypto.subtle.timingSafeEqual(bytes, expectedBytes)
  ) throw new QualificationStateError("foreign_state_movement");
}

function restorationMessage(request: QualificationRestorationRequest): string {
  return `Restore model identity qualification ${request.journalId} nonce ${request.recoveryNonce}`;
}

async function commitIdentity(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
  commit: string,
): Promise<CommitIdentity> {
  const value = object(
    await call(config, fetcher, `/git/commits/${commit}`),
    "qualification State commit",
  );
  if (!Array.isArray(value.parents)) {
    throw new QualificationStateError("provider_unavailable");
  }
  return {
    sha: sha(value.sha),
    tree: sha(nested(value, ["tree", "sha"])),
    parents: value.parents.map((parent) => sha(object(parent, "commit parent").sha)),
    message: typeof value.message === "string"
      ? value.message
      : (() => { throw new QualificationStateError("provider_unavailable"); })(),
  };
}

async function protectedSnapshot(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
): Promise<QualificationStateSnapshot> {
  const ref = object(
    await call(config, fetcher, `/git/ref/heads/${STATE_BRANCH}`),
    "qualification State ref",
  );
  const head = sha(nested(ref, ["object", "sha"]));
  const [commit, branch] = await Promise.all([
    commitIdentity(config, fetcher, head),
    call(config, fetcher, `/branches/${STATE_BRANCH}`),
  ]);
  const protectedBranch = object(branch, "qualification protected State branch");
  if (
    commit.sha !== head ||
    protectedBranch.name !== STATE_BRANCH ||
    protectedBranch.protected !== true ||
    nested(protectedBranch, ["commit", "sha"]) !== head
  ) {
    throw new QualificationStateError("provider_unavailable");
  }
  return { head_commit: head, head_tree: commit.tree };
}

export async function qualificationStateSnapshot(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
): Promise<QualificationStateSnapshot> {
  configValid(config);
  return protectedSnapshot(config, fetcher);
}

export async function qualificationStateMutation(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
  request: QualificationMutationRequest,
): Promise<QualificationStateMutation> {
  configValid(config);
  if (
    !SHA.test(request.expectedParent) ||
    request.expectedMessage.length < 1 ||
    new TextEncoder().encode(request.expectedMessage).byteLength > 512 ||
    Object.keys(request.expectedDocuments).length < 1 ||
    Object.keys(request.expectedDocuments).length > 128
  ) {
    throw new TypeError("qualification mutation request is invalid");
  }
  const snapshot = await protectedSnapshot(config, fetcher);
  const commit = await commitIdentity(config, fetcher, snapshot.head_commit);
  if (
    commit.sha === request.expectedParent ||
    commit.tree !== snapshot.head_tree ||
    commit.parents.length !== 1 ||
    commit.parents[0] !== request.expectedParent ||
    commit.message !== request.expectedMessage
  ) {
    throw new QualificationStateError("foreign_state_movement");
  }
  await Promise.all(Object.entries(request.expectedDocuments).map(
    ([path, expected]) => verifyExactDocument(
      config,
      fetcher,
      commit.sha,
      path,
      expected,
    ),
  ));
  return {
    state_commit: commit.sha,
    state_tree: commit.tree,
    parent_commit: request.expectedParent,
  };
}

function requestValid(request: QualificationRestorationRequest): void {
  if (
    !JOURNAL_ID.test(request.journalId) ||
    !RECOVERY_NONCE.test(request.recoveryNonce) ||
    !SHA.test(request.expectedHead) ||
    !SHA.test(request.expectedTree) ||
    !SHA.test(request.initialTree)
  ) {
    throw new TypeError("qualification restoration request is invalid");
  }
}

function exactAuditCommit(
  commit: CommitIdentity,
  request: QualificationRestorationRequest,
): boolean {
  return commit.tree === request.initialTree &&
    commit.parents.length === 1 &&
    commit.parents[0] === request.expectedHead &&
    commit.message === restorationMessage(request);
}

export async function restoreQualificationState(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
  request: QualificationRestorationRequest,
): Promise<QualificationStateRestoration> {
  configValid(config);
  requestValid(request);
  const before = await protectedSnapshot(config, fetcher);
  if (before.head_commit !== request.expectedHead || before.head_tree !== request.expectedTree) {
    const possibleRetry = await commitIdentity(config, fetcher, before.head_commit);
    if (!exactAuditCommit(possibleRetry, request)) {
      throw new QualificationStateError("foreign_state_movement");
    }
    return {
      restoration_commit: before.head_commit,
      restoration_parent_commit: request.expectedHead,
      restoration_parent_tree: request.expectedTree,
      restoration_tree: request.initialTree,
      ref_head: before.head_commit,
      fast_forward: true,
      tree_equal: true,
    };
  }

  const created = object(await call(config, fetcher, "/git/commits", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      message: restorationMessage(request),
      tree: request.initialTree,
      parents: [request.expectedHead],
    }),
  }), "qualification restoration commit");
  const restorationCommit = sha(created.sha);
  const createdIdentity = await commitIdentity(config, fetcher, restorationCommit);
  if (!exactAuditCommit(createdIdentity, request)) {
    throw new QualificationStateError("provider_unavailable");
  }

  const updated = object(await call(config, fetcher, `/git/refs/heads/${STATE_BRANCH}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ sha: restorationCommit, force: false }),
  }), "qualification restoration ref");
  if (
    updated.ref !== `refs/heads/${STATE_BRANCH}` ||
    nested(updated, ["object", "sha"]) !== restorationCommit
  ) {
    throw new QualificationStateError("provider_unavailable");
  }
  const after = await protectedSnapshot(config, fetcher);
  if (after.head_commit !== restorationCommit || after.head_tree !== request.initialTree) {
    throw new QualificationStateError("foreign_state_movement");
  }
  return {
    restoration_commit: restorationCommit,
    restoration_parent_commit: request.expectedHead,
    restoration_parent_tree: request.expectedTree,
    restoration_tree: request.initialTree,
    ref_head: restorationCommit,
    fast_forward: true,
    tree_equal: true,
  };
}
