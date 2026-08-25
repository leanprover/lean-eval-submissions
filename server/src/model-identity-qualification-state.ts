import type { GitHubFetch } from "./github-state";
import type { QualificationFixtureManifest } from "./model-identity-qualification-fixture";

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

export type QualificationStateMutationPrefix = QualificationStateMutation & Readonly<{
  applied_mutations: number;
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

export type QualificationExpectedMutation = Readonly<{
  expectedMessage: string;
  expectedDocuments: Readonly<Record<string, unknown>>;
  expectedDeletedPaths: readonly string[];
  expectedTreeUnchanged: boolean;
}>;

export type QualificationMutationSequenceRequest = Readonly<{
  expectedParent: string;
  expectedMutations: readonly QualificationExpectedMutation[];
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

function statePathValid(path: string): boolean {
  return !path.startsWith("/") &&
    !path.includes("..") &&
    /^[a-z0-9][a-z0-9./_-]{0,511}$/.test(path);
}

async function verifyExactDiff(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
  parent: string,
  commit: CommitIdentity,
  expected: QualificationExpectedMutation,
): Promise<void> {
  const documents = Object.entries(expected.expectedDocuments);
  const paths = [
    ...documents.map(([path]) => path),
    ...expected.expectedDeletedPaths,
  ];
  if (
    expected.expectedMessage.length < 1 ||
    new TextEncoder().encode(expected.expectedMessage).byteLength > 512 ||
    (expected.expectedTreeUnchanged ? paths.length !== 0 : paths.length < 1) ||
    paths.length > 128 ||
    paths.some((path) => !statePathValid(path)) ||
    new Set(paths).size !== paths.length
  ) throw new TypeError("qualification expected State diff is invalid");

  if (expected.expectedTreeUnchanged) {
    const parentIdentity = await commitIdentity(config, fetcher, parent);
    if (commit.tree !== parentIdentity.tree) {
      throw new QualificationStateError("foreign_state_movement");
    }
  }

  const comparison = object(
    await call(config, fetcher, `/compare/${parent}...${commit.sha}`),
    "qualification State comparison",
  );
  if (!Array.isArray(comparison.commits) || !Array.isArray(comparison.files)) {
    throw new QualificationStateError("provider_unavailable");
  }
  const commits = comparison.commits.map((value) =>
    sha(object(value, "qualification comparison commit").sha));
  const files = comparison.files.map((value) =>
    object(value, "qualification comparison file"));
  if (
    comparison.status !== "ahead" ||
    comparison.ahead_by !== 1 ||
    comparison.behind_by !== 0 ||
    comparison.total_commits !== 1 ||
    nested(comparison, ["merge_base_commit", "sha"]) !== parent ||
    commits.length !== 1 ||
    commits[0] !== commit.sha ||
    files.length !== paths.length
  ) throw new QualificationStateError("foreign_state_movement");

  const expectedDocumentShas = new Map<string, string>();
  for (const [path, value] of documents) {
    const bytes = new TextEncoder().encode(canonicalStateDocument(value));
    expectedDocumentShas.set(path, await gitBlobSha(bytes));
  }
  const deleted = new Set(expected.expectedDeletedPaths);
  const seen = new Set<string>();
  for (const file of files) {
    const path = file.filename;
    if (typeof path !== "string" || seen.has(path)) {
      throw new QualificationStateError("foreign_state_movement");
    }
    seen.add(path);
    if (deleted.has(path)) {
      if (file.status !== "removed") {
        throw new QualificationStateError("foreign_state_movement");
      }
      continue;
    }
    const expectedSha = expectedDocumentShas.get(path);
    if (
      expectedSha === undefined ||
      !new Set(["added", "modified"]).has(String(file.status)) ||
      file.sha !== expectedSha ||
      ("previous_filename" in file)
    ) throw new QualificationStateError("foreign_state_movement");
  }
  if (paths.some((path) => !seen.has(path))) {
    throw new QualificationStateError("foreign_state_movement");
  }
  await Promise.all(documents.map(([path, value]) =>
    verifyExactDocument(config, fetcher, commit.sha, path, value)));
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

export async function verifyQualificationFixtureAtState(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
  manifest: QualificationFixtureManifest,
): Promise<QualificationStateSnapshot> {
  configValid(config);
  const before = await protectedSnapshot(config, fetcher);
  if (
    before.head_commit !== manifest.state.seed_commit ||
    before.head_tree !== manifest.state.seed_tree
  ) throw new QualificationStateError("foreign_state_movement");
  const [seed, parent] = await Promise.all([
    commitIdentity(config, fetcher, manifest.state.seed_commit),
    commitIdentity(config, fetcher, manifest.state.seed_parent_commit),
  ]);
  if (
    seed.tree !== manifest.state.seed_tree ||
    seed.parents.length !== 1 ||
    seed.parents[0] !== manifest.state.seed_parent_commit ||
    seed.message !== manifest.state.seed_commit_message ||
    parent.tree !== manifest.state.seed_parent_tree
  ) throw new QualificationStateError("foreign_state_movement");

  const comparison = object(await call(
    config,
    fetcher,
    `/compare/${manifest.state.seed_parent_commit}...${manifest.state.seed_commit}`,
  ), "qualification fixture seed comparison");
  if (!Array.isArray(comparison.commits) || !Array.isArray(comparison.files)) {
    throw new QualificationStateError("provider_unavailable");
  }
  const files = comparison.files.map((value) =>
    object(value, "qualification fixture seed file"));
  const documents = new Map(manifest.documents.map((document) => [
    document.path,
    document,
  ]));
  if (
    comparison.status !== "ahead" ||
    comparison.ahead_by !== 1 ||
    comparison.behind_by !== 0 ||
    comparison.total_commits !== 1 ||
    nested(comparison, ["merge_base_commit", "sha"]) !==
      manifest.state.seed_parent_commit ||
    comparison.commits.length !== 1 ||
    object(comparison.commits[0], "qualification fixture seed commit").sha !==
      manifest.state.seed_commit ||
    files.length !== manifest.document_count
  ) throw new QualificationStateError("foreign_state_movement");
  const seen = new Set<string>();
  for (const file of files) {
    const path = file.filename;
    const document = typeof path === "string" ? documents.get(path) : undefined;
    if (
      document === undefined ||
      seen.has(path as string) ||
      file.status !== "added" ||
      file.sha !== document.git_blob_sha1 ||
      ("previous_filename" in file)
    ) throw new QualificationStateError("foreign_state_movement");
    seen.add(path as string);
  }
  if (seen.size !== documents.size) {
    throw new QualificationStateError("foreign_state_movement");
  }

  if (manifest.state.contract_commit !== manifest.state.seed_parent_commit) {
    const ancestry = object(await call(
      config,
      fetcher,
      `/compare/${manifest.state.contract_commit}...${manifest.state.seed_parent_commit}`,
    ), "qualification fixture contract ancestry");
    if (
      ancestry.status !== "ahead" ||
      nested(ancestry, ["merge_base_commit", "sha"]) !==
        manifest.state.contract_commit
    ) throw new QualificationStateError("foreign_state_movement");
  }

  for (let offset = 0; offset < manifest.documents.length; offset += 6) {
    await Promise.all(manifest.documents.slice(offset, offset + 6).map(
      (document) => verifyExactDocument(
        config,
        fetcher,
        manifest.state.seed_commit,
        document.path,
        document.value,
      ),
    ));
  }
  const after = await protectedSnapshot(config, fetcher);
  if (
    after.head_commit !== before.head_commit ||
    after.head_tree !== before.head_tree
  ) throw new QualificationStateError("foreign_state_movement");
  return after;
}

export async function qualificationStateMutation(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
  request: QualificationMutationRequest,
): Promise<QualificationStateMutation> {
  return qualificationStateMutationSequence(config, fetcher, {
    expectedParent: request.expectedParent,
    expectedMutations: [{
      expectedMessage: request.expectedMessage,
      expectedDocuments: request.expectedDocuments,
      expectedDeletedPaths: [],
      expectedTreeUnchanged: false,
    }],
  });
}

export async function qualificationStateMutationSequence(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
  request: QualificationMutationSequenceRequest,
): Promise<QualificationStateMutation> {
  const prefix = await qualificationStateMutationPrefix(config, fetcher, request);
  if (prefix.applied_mutations !== request.expectedMutations.length) {
    throw new QualificationStateError("foreign_state_movement");
  }
  return {
    state_commit: prefix.state_commit,
    state_tree: prefix.state_tree,
    parent_commit: prefix.parent_commit,
  };
}

export async function qualificationStateMutationPrefix(
  config: QualificationStateConfig,
  fetcher: GitHubFetch,
  request: QualificationMutationSequenceRequest,
): Promise<QualificationStateMutationPrefix> {
  configValid(config);
  if (
    !SHA.test(request.expectedParent) ||
    request.expectedMutations.length < 1 ||
    request.expectedMutations.length > 128
  ) {
    throw new TypeError("qualification mutation sequence request is invalid");
  }
  const snapshot = await protectedSnapshot(config, fetcher);
  const commits: CommitIdentity[] = [];
  let cursor = snapshot.head_commit;
  while (
    cursor !== request.expectedParent &&
    commits.length < request.expectedMutations.length
  ) {
    const commit = await commitIdentity(config, fetcher, cursor);
    if (commit.parents.length !== 1) {
      throw new QualificationStateError("foreign_state_movement");
    }
    commits.push(commit);
    cursor = commit.parents[0] ?? "";
  }
  commits.reverse();
  if (
    cursor !== request.expectedParent ||
    commits.length < 1 ||
    commits.at(-1)?.sha !== snapshot.head_commit ||
    commits.at(-1)?.tree !== snapshot.head_tree
  ) throw new QualificationStateError("foreign_state_movement");
  let parent = request.expectedParent;
  for (const [index, commit] of commits.entries()) {
    const expected = request.expectedMutations[index];
    if (
      expected === undefined ||
      commit.parents[0] !== parent ||
      commit.message !== expected.expectedMessage
    ) throw new QualificationStateError("foreign_state_movement");
    await verifyExactDiff(config, fetcher, parent, commit, expected);
    parent = commit.sha;
  }
  return {
    state_commit: snapshot.head_commit,
    state_tree: snapshot.head_tree,
    parent_commit: request.expectedParent,
    applied_mutations: commits.length,
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
