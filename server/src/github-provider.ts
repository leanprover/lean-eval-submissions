import {
  canonicalJson,
  RESULTS_REPOSITORY,
  resultId,
  sha256Hex as canonicalSha256Hex,
  type VerifiedLegacyResult,
} from "./result-owner";
import {
  challengeId,
  comparatorBindingSha256,
  type ComparatorEvidence,
  type ProblemGroup,
} from "./result-amendment";

const API = "https://api.github.com";
const OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token";
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const COMMIT = /^[0-9a-f]{40}$/;
const DISPATCH_REF = /^lean-eval-dispatch\/([0-9a-f]{40})$/;
const RESULT_ID = /^r2_[0-9a-f]{64}$/;
const MAX_RESULT_FILE_BYTES = 2 * 1024 * 1024;
const MAX_LEGACY_METADATA_BYTES = 32 * 1024;
const MAX_LEGACY_METADATA_DEPTH = 16;
const MAX_LEGACY_METADATA_NODES = 256;
const MAX_LEGACY_METADATA_CONTAINER_ITEMS = 128;
const MAX_LEGACY_METADATA_STRING_BYTES = 16 * 1024;
const MAX_LEGACY_METADATA_KEY_BYTES = 256;
const RESULT_ID_DOMAIN = "lean-eval-result-v2\0";
const RESULT_TREE_DOMAIN = "lean-eval-result-tree-v1\0";
const BENCHMARK_REPOSITORY = "leanprover/lean-eval";
const MAX_MANIFEST_BYTES = 64 * 1024;
// GitHub caps an unpaginated compare at 250 commits. Reject a truncated proof.
const GITHUB_COMPARE_DEFAULT_COMMIT_LIMIT = 250;
export type ResultsProtectedBranch = "main" | "staging-results";

export type ProblemRepairComparatorRequest = Readonly<{
  resultsCommit: string;
  resultId: string;
  ownerLogin: string;
  declaredModel: string;
  baseProblemId: string;
  baseStatementRevision: number;
  correctedProblemId: string;
  correctedStatementRevision: number;
}>;

export type ProviderFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export type GitHubIdentity = Readonly<{ id: number; login: string }>;
export type GitHubRepository = Readonly<{
  fullName: string;
  private: boolean;
}>;
export type VerifiedResult = Readonly<{
  resultId: string;
  treeDigest: string;
}>;

function exactKeys(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new GitHubProviderError(409, `${label} fields were invalid`);
  }
}

function containsControlCharacter(value: string): boolean {
  for (const character of value) {
    const point = character.codePointAt(0) ?? 0;
    if (point <= 0x1f || point === 0x7f) return true;
  }
  return false;
}

function isSecondPrecisionUtcTimestamp(value: string): boolean {
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/.test(value)) return false;
  const parsed = new Date(value);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString() === value.replace("Z", ".000Z");
}

function isIsoCalendarDate(value: string): boolean {
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

async function gitBlobOid(bytes: Uint8Array): Promise<string> {
  const header = new TextEncoder().encode(`blob ${String(bytes.byteLength)}\0`);
  const material = new Uint8Array(header.byteLength + bytes.byteLength);
  material.set(header);
  material.set(bytes, header.byteLength);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-1", material));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function inlineContentBytes(
  data: Record<string, unknown>,
  path: string,
  maxBytes: number,
  label: string,
): Uint8Array {
  if (
    data.type !== "file" ||
    data.path !== path ||
    data.encoding !== "base64" ||
    typeof data.content !== "string" ||
    typeof data.size !== "number" ||
    !Number.isSafeInteger(data.size) ||
    data.size < 1 ||
    data.size > maxBytes
  ) {
    throw new GitHubProviderError(502, `${label} response fields were invalid`);
  }
  let bytes: Uint8Array;
  try {
    const binary = atob(data.content.replaceAll(/\s/g, ""));
    bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    throw new GitHubProviderError(502, `${label} was not valid base64`);
  }
  if (bytes.byteLength !== data.size) {
    throw new GitHubProviderError(502, `${label} size disagreed with GitHub`);
  }
  return bytes;
}

function manifestField(text: string, field: "id" | "group"): string {
  const expression = new RegExp(`^${field} = "([A-Za-z0-9_-]+)"$`, "gmu");
  const matches = [...text.matchAll(expression)];
  if (matches.length !== 1 || matches[0]?.[1] === undefined) {
    throw new GitHubProviderError(409, `benchmark manifest ${field} was missing or ambiguous`);
  }
  return matches[0][1];
}

function manifestRevision(text: string): number {
  const matches = [...text.matchAll(/^statement_revision = ([1-9][0-9]*)$/gmu)];
  const raw = matches[0]?.[1];
  if (matches.length !== 1 || raw === undefined) {
    throw new GitHubProviderError(409, "benchmark manifest statement revision was missing or ambiguous");
  }
  const revision = Number(raw);
  if (!Number.isSafeInteger(revision)) {
    throw new GitHubProviderError(409, "benchmark manifest statement revision was invalid");
  }
  return revision;
}

function assertBoundedLegacyMetadata(value: Record<string, unknown>): void {
  const stack: { value: unknown; depth: number }[] = [{ value, depth: 0 }];
  let nodes = 0;
  while (stack.length > 0) {
    const current = stack.pop();
    if (current === undefined) break;
    nodes += 1;
    if (nodes > MAX_LEGACY_METADATA_NODES || current.depth > MAX_LEGACY_METADATA_DEPTH) {
      throw new GitHubProviderError(409, "legacy result production metadata exceeded its structural bound");
    }
    if (typeof current.value === "string") {
      if (new TextEncoder().encode(current.value).byteLength > MAX_LEGACY_METADATA_STRING_BYTES) {
        throw new GitHubProviderError(409, "legacy result production metadata string exceeded its byte bound");
      }
      continue;
    }
    if (Array.isArray(current.value)) {
      if (current.value.length > MAX_LEGACY_METADATA_CONTAINER_ITEMS) {
        throw new GitHubProviderError(409, "legacy result production metadata array exceeded its item bound");
      }
      for (const item of current.value) stack.push({ value: item, depth: current.depth + 1 });
      continue;
    }
    if (current.value !== null && typeof current.value === "object") {
      const entries = Object.entries(current.value as Record<string, unknown>);
      if (entries.length > MAX_LEGACY_METADATA_CONTAINER_ITEMS) {
        throw new GitHubProviderError(409, "legacy result production metadata object exceeded its field bound");
      }
      for (const [key, item] of entries) {
        if (new TextEncoder().encode(key).byteLength > MAX_LEGACY_METADATA_KEY_BYTES) {
          throw new GitHubProviderError(409, "legacy result production metadata key exceeded its byte bound");
        }
        stack.push({ value: item, depth: current.depth + 1 });
      }
    }
  }
  let canonical: string;
  try {
    canonical = canonicalJson(value);
  } catch (caught) {
    if (caught instanceof TypeError) {
      throw new GitHubProviderError(409, "legacy result production metadata was not canonicalizable JSON");
    }
    throw caught;
  }
  if (new TextEncoder().encode(canonical).byteLength > MAX_LEGACY_METADATA_BYTES) {
    throw new GitHubProviderError(409, "legacy result production metadata exceeded its canonical byte bound");
  }
}

export class GitHubProviderError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(`GitHub provider ${String(status)}: ${message}`);
    this.name = "GitHubProviderError";
    this.status = status;
  }
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new GitHubProviderError(502, `${label} was not an object`);
  }
  return value as Record<string, unknown>;
}

function providerHeaders(token?: string): Headers {
  const headers = new Headers({
    accept: "application/vnd.github+json",
    "user-agent": "lean-eval-submission-worker",
    "x-github-api-version": "2022-11-28",
  });
  if (token) headers.set("authorization", `Bearer ${token}`);
  return headers;
}

async function error(response: Response): Promise<GitHubProviderError> {
  return new GitHubProviderError(response.status, (await response.text()).slice(0, 300));
}

async function jsonResponse(response: Response, label: string): Promise<Record<string, unknown>> {
  if (!response.ok) throw await error(response);
  try {
    return object(await response.json<unknown>(), label);
  } catch (caught) {
    if (caught instanceof GitHubProviderError) throw caught;
    throw new GitHubProviderError(502, `${label} was not valid JSON`);
  }
}

function comparisonTerminalSha(
  comparison: Record<string, unknown>,
  expectedBase: string,
  label: string,
  failure: string,
): string {
  const base = object(comparison.base_commit, `${label} base commit`);
  const mergeBase = object(comparison.merge_base_commit, `${label} merge base`);
  const commits = comparison.commits;
  const aheadBy = comparison.ahead_by;
  const behindBy = comparison.behind_by;
  const totalCommits = comparison.total_commits;
  if (
    base.sha !== expectedBase ||
    mergeBase.sha !== expectedBase ||
    !Array.isArray(commits) ||
    typeof aheadBy !== "number" ||
    !Number.isSafeInteger(aheadBy) ||
    typeof behindBy !== "number" ||
    !Number.isSafeInteger(behindBy) ||
    typeof totalCommits !== "number" ||
    !Number.isSafeInteger(totalCommits)
  ) {
    throw new GitHubProviderError(409, failure);
  }
  const commitShas = commits.map((value, index) => {
    const commit = object(value, `${label} commit ${String(index)}`);
    if (typeof commit.sha !== "string" || !COMMIT.test(commit.sha)) {
      throw new GitHubProviderError(409, failure);
    }
    return commit.sha;
  });
  let terminalSha: string;
  if (comparison.status === "identical") {
    if (aheadBy !== 0 || behindBy !== 0 || totalCommits !== 0 || commitShas.length !== 0) {
      throw new GitHubProviderError(409, failure);
    }
    terminalSha = expectedBase;
  } else if (comparison.status === "ahead") {
    if (
      behindBy !== 0 ||
      aheadBy < 1 ||
      totalCommits !== aheadBy ||
      totalCommits > GITHUB_COMPARE_DEFAULT_COMMIT_LIMIT ||
      commitShas.length !== totalCommits
    ) {
      throw new GitHubProviderError(409, failure);
    }
    const terminal = commitShas.at(-1);
    if (terminal === undefined) {
      throw new GitHubProviderError(409, failure);
    }
    terminalSha = terminal;
  } else {
    throw new GitHubProviderError(409, failure);
  }
  if (comparison.head_commit !== undefined && comparison.head_commit !== null) {
    const head = object(comparison.head_commit, `${label} head commit`);
    if (head.sha !== terminalSha) {
      throw new GitHubProviderError(409, failure);
    }
  }
  return terminalSha;
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function expectedResultId(
  login: string,
  declaredModel: string,
  problemId: string,
  statementRevision: number,
): Promise<string> {
  const identity = JSON.stringify([login.toLowerCase(), declaredModel, problemId, statementRevision]);
  return `r2_${await sha256Hex(new TextEncoder().encode(RESULT_ID_DOMAIN + identity))}`;
}

async function resultTreeDigest(path: string, contents: Uint8Array): Promise<string> {
  const fileDigest = await sha256Hex(contents);
  const entry = JSON.stringify([{ path, sha256: fileDigest, size: contents.byteLength }]);
  return sha256Hex(new TextEncoder().encode(RESULT_TREE_DOMAIN + entry));
}

export class GitHubProvider {
  readonly #fetcher: ProviderFetch;
  readonly #verificationToken: string | undefined;
  readonly #verificationFetcher: ProviderFetch | undefined;
  readonly #dispatchFetcher: ProviderFetch | undefined;
  readonly #resultFetcher: ProviderFetch | undefined;
  readonly #resultsProtectedBranch: ResultsProtectedBranch | undefined;
  readonly #benchmarkFetcher: ProviderFetch | undefined;
  readonly #legacyVerificationFetcher: ProviderFetch | undefined;

  constructor(
    fetcher: ProviderFetch = fetch,
    verificationToken?: string,
    verificationFetcher?: ProviderFetch,
    dispatchFetcher?: ProviderFetch,
    resultFetcher?: ProviderFetch,
    resultsProtectedBranch?: ResultsProtectedBranch,
    benchmarkFetcher?: ProviderFetch,
    legacyVerificationFetcher?: ProviderFetch,
  ) {
    // A Worker runtime fetch function must be invoked without rebinding its
    // receiver. Calling a function-valued private field as `this.#fetcher()`
    // supplies the provider instance as `this`, which workerd rejects for the
    // native global fetch with an illegal-invocation TypeError.
    this.#fetcher = (input, init) => fetcher(input, init);
    this.#verificationToken = verificationToken;
    this.#verificationFetcher = verificationFetcher === undefined
      ? undefined
      : (input, init) => verificationFetcher(input, init);
    this.#dispatchFetcher = dispatchFetcher === undefined
      ? undefined
      : (input, init) => dispatchFetcher(input, init);
    this.#resultFetcher = resultFetcher === undefined
      ? undefined
      : (input, init) => resultFetcher(input, init);
    this.#resultsProtectedBranch = resultsProtectedBranch;
    this.#benchmarkFetcher = benchmarkFetcher === undefined
      ? undefined
      : (input, init) => benchmarkFetcher(input, init);
    this.#legacyVerificationFetcher = legacyVerificationFetcher === undefined
      ? undefined
      : (input, init) => legacyVerificationFetcher(input, init);
  }

  async exchangeOAuth(
    clientId: string,
    clientSecret: string,
    code: string,
    redirectUri: string,
  ): Promise<{ identity: GitHubIdentity }> {
    const response = await this.#fetcher(OAUTH_TOKEN_URL, {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": "lean-eval-submission-worker",
      },
      body: new URLSearchParams({ client_id: clientId, client_secret: clientSecret, code, redirect_uri: redirectUri }),
      redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    const tokenData = await jsonResponse(response, "OAuth token response");
    if (
      typeof tokenData.access_token !== "string" ||
      tokenData.access_token.length < 16 ||
      String(tokenData.token_type).toLowerCase() !== "bearer"
    ) {
      throw new GitHubProviderError(502, "OAuth token response fields were invalid");
    }
    // This local variable is the only lifetime of the browser token. It is
    // neither returned nor placed into an event, cache, log, or exception.
    const identityResponse = await this.#fetcher(`${API}/user`, {
      headers: providerHeaders(tokenData.access_token),
      redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    const identity = await jsonResponse(identityResponse, "authenticated user");
    if (
      typeof identity.id !== "number" ||
      !Number.isSafeInteger(identity.id) ||
      identity.id < 1 ||
      typeof identity.login !== "string" ||
      !LOGIN.test(identity.login.toLowerCase())
    ) {
      throw new GitHubProviderError(502, "authenticated user fields were invalid");
    }
    return { identity: { id: identity.id, login: identity.login.toLowerCase() } };
  }

  async repository(repository: string): Promise<GitHubRepository> {
    if (!this.#verificationToken && !this.#verificationFetcher) {
      throw new GitHubProviderError(503, "source verification credential is not configured");
    }
    const response = await (this.#verificationFetcher ?? this.#fetcher)(`${API}/repos/${repository}`, {
      headers: providerHeaders(this.#verificationToken),
      redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    const data = await jsonResponse(response, "repository response");
    if (typeof data.full_name !== "string" || typeof data.private !== "boolean") {
      throw new GitHubProviderError(502, "repository response fields were invalid");
    }
    if (data.full_name.toLowerCase() !== repository.toLowerCase()) {
      throw new GitHubProviderError(409, "repository identity changed");
    }
    return { fullName: data.full_name, private: data.private };
  }

  async #repositoryWithFetcher(
    repository: string,
    fetcher: ProviderFetch,
    label: string,
    token?: string,
  ): Promise<GitHubRepository> {
    const response = await fetcher(`${API}/repos/${repository}`, {
      headers: providerHeaders(token),
      redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    const data = await jsonResponse(response, `${label} repository response`);
    if (typeof data.full_name !== "string" || typeof data.private !== "boolean") {
      throw new GitHubProviderError(502, `${label} repository response fields were invalid`);
    }
    if (data.full_name.toLowerCase() !== repository.toLowerCase()) {
      throw new GitHubProviderError(409, `${label} repository identity changed`);
    }
    return { fullName: data.full_name, private: data.private };
  }

  async #verifyCommitWithFetcher(
    repository: string,
    expectedCommit: string,
    fetcher: ProviderFetch,
    label: string,
    token?: string,
  ): Promise<void> {
    if (!COMMIT.test(expectedCommit)) {
      throw new GitHubProviderError(400, "source commit proof was invalid");
    }
    const headers = providerHeaders(token);
    headers.set("x-lean-eval-expected-commit", expectedCommit);
    const response = await fetcher(`${API}/repos/${repository}/git/commits/${expectedCommit}`, {
      headers,
      redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    const data = await jsonResponse(response, `${label} commit response`);
    if (data.sha !== expectedCommit) {
      throw new GitHubProviderError(409, `${label} commit identity changed`);
    }
  }

  /**
   * Prove that both Apps needed by the server lifecycle can read the exact
   * repository and commit before any submission State is mutated.
   */
  async submissionRepository(repository: string, expectedCommit: string): Promise<GitHubRepository> {
    const sourceFetcher = this.#verificationFetcher ?? (this.#verificationToken ? this.#fetcher : undefined);
    const legacyFetcher = this.#legacyVerificationFetcher;
    if (sourceFetcher === undefined || legacyFetcher === undefined) {
      throw new GitHubProviderError(503, "both source verification credentials are required");
    }
    const verify = async (
      fetcher: ProviderFetch,
      label: string,
      token?: string,
    ): Promise<GitHubRepository> => {
      const identity = await this.#repositoryWithFetcher(repository, fetcher, label, token);
      await this.#verifyCommitWithFetcher(repository, expectedCommit, fetcher, label, token);
      return identity;
    };
    const [source, legacy] = await Promise.all([
      verify(sourceFetcher, "source reader", this.#verificationFetcher ? undefined : this.#verificationToken),
      verify(legacyFetcher, "workflow source reader"),
    ]);
    if (
      source.fullName.toLowerCase() !== legacy.fullName.toLowerCase() ||
      source.private !== legacy.private
    ) {
      throw new GitHubProviderError(409, "source Apps disagreed about repository identity or visibility");
    }
    return source;
  }

  async verifyTag(repository: string, tag: string, expectedCommit: string): Promise<void> {
    if (!this.#verificationToken && !this.#verificationFetcher) {
      throw new GitHubProviderError(503, "source verification credential is not configured");
    }
    if (!/^lean-eval\/[0-9a-f-]{36}$/.test(tag) || !COMMIT.test(expectedCommit)) {
      throw new GitHubProviderError(400, "tag proof fields are invalid");
    }
    const headers = providerHeaders(this.#verificationToken);
    headers.set("x-lean-eval-expected-commit", expectedCommit);
    const verifiedFetch = this.#verificationFetcher ?? this.#fetcher;
    const refResponse = await verifiedFetch(`${API}/repos/${repository}/git/ref/tags/${encodeURIComponent(tag)}`, {
      headers,
      redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    const ref = await jsonResponse(refResponse, "tag reference");
    const target = object(ref.object, "tag reference object");
    let commit: unknown = target.sha;
    if (target.type === "tag") {
      if (typeof target.sha !== "string" || !COMMIT.test(target.sha)) {
        throw new GitHubProviderError(502, "annotated tag object was invalid");
      }
      const tagResponse = await verifiedFetch(`${API}/repos/${repository}/git/tags/${target.sha}`, {
        headers,
        redirect: "manual",
        signal: AbortSignal.timeout(5000),
      });
      const annotated = await jsonResponse(tagResponse, "annotated tag");
      const annotatedTarget = object(annotated.object, "annotated tag object");
      if (annotatedTarget.type !== "commit") {
        throw new GitHubProviderError(409, "tag does not resolve directly to a commit");
      }
      commit = annotatedTarget.sha;
    } else if (target.type !== "commit") {
      throw new GitHubProviderError(409, "tag does not resolve to a commit");
    }
    if (commit !== expectedCommit) throw new GitHubProviderError(409, "tag moved or targets a different commit");
  }

  async verifySecretGist(gistId: string, login: string, challenge: string): Promise<GitHubIdentity> {
    // GitHub secret gists are unlisted rather than access-controlled. Fetching
    // the exact high-entropy gist ID anonymously avoids granting the source
    // reader (or the browser OAuth flow) unrelated user-level gist authority.
    const response = await this.#fetcher(`${API}/gists/${gistId}`, {
      headers: providerHeaders(),
      redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    const gist = await jsonResponse(response, "gist response");
    const owner = object(gist.owner, "gist owner");
    const files = object(gist.files, "gist files");
    const proof = object(files["lean-eval-proof.txt"], "gist proof file");
    if (
      gist.public !== false ||
      typeof owner.id !== "number" ||
      !Number.isSafeInteger(owner.id) ||
      owner.id < 1 ||
      typeof owner.login !== "string" ||
      owner.login.toLowerCase() !== login ||
      proof.truncated === true ||
      proof.content !== challenge
    ) {
      throw new GitHubProviderError(409, "secret gist proof does not match the challenge and owner");
    }
    return { id: owner.id, login };
  }

  async dispatch(token: string, request: Request): Promise<void> {
    if (!token && !this.#dispatchFetcher) {
      throw new GitHubProviderError(503, "workflow dispatch credential is not configured");
    }
    const headers = providerHeaders(token);
    headers.set("content-type", "application/json");
    const response = await (this.#dispatchFetcher ?? this.#fetcher)(request.url, {
      method: request.method,
      headers,
      body: await request.text(),
      redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    if (response.status !== 204) throw await error(response);
  }

  async verifyResult(
    completion: Readonly<{
      result_id: string;
      result_repository: string;
      result_commit: string;
      result_path: string;
      result_tree_digest: string;
    }>,
    expected: Readonly<{
      login: string;
      declaredModel: string;
      problemId: string;
      statementRevision: number;
    }>,
  ): Promise<VerifiedResult> {
    if (!this.#resultFetcher) {
      throw new GitHubProviderError(503, "result verification authority is not configured");
    }
    if (
      completion.result_repository !== "leanprover/lean-eval-submissions" ||
      !COMMIT.test(completion.result_commit) ||
      completion.result_path !== `results/${expected.login.toLowerCase()}.json` ||
      !RESULT_ID.test(completion.result_id)
    ) {
      throw new GitHubProviderError(409, "result locator does not match the accepted identity");
    }
    const query = new URLSearchParams({ ref: completion.result_commit });
    const headers = providerHeaders();
    headers.set("x-lean-eval-expected-commit", completion.result_commit);
    const response = await this.#resultFetcher(
      `${API}/repos/${completion.result_repository}/contents/${completion.result_path}?${query.toString()}`,
      { headers, redirect: "manual", signal: AbortSignal.timeout(5000) },
    );
    const data = await jsonResponse(response, "result contents response");
    if (
      data.type !== "file" ||
      data.path !== completion.result_path ||
      data.encoding !== "base64" ||
      typeof data.content !== "string" ||
      typeof data.size !== "number" ||
      !Number.isSafeInteger(data.size) ||
      data.size < 1 ||
      data.size > MAX_RESULT_FILE_BYTES
    ) {
      throw new GitHubProviderError(502, "result contents response fields were invalid");
    }
    let contents: Uint8Array;
    try {
      const compact = data.content.replaceAll(/\s/g, "");
      const binary = atob(compact);
      contents = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    } catch {
      throw new GitHubProviderError(502, "result contents were not valid base64");
    }
    if (contents.byteLength !== data.size) {
      throw new GitHubProviderError(502, "result contents size disagreed with GitHub");
    }
    const treeDigest = await resultTreeDigest(completion.result_path, contents);
    if (treeDigest !== completion.result_tree_digest) {
      throw new GitHubProviderError(409, "result tree digest did not match the exact commit");
    }
    let document: Record<string, unknown>;
    try {
      document = object(
        JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(contents)) as unknown,
        "result file",
      );
    } catch (caught) {
      if (caught instanceof GitHubProviderError) throw caught;
      throw new GitHubProviderError(502, "result file was not valid UTF-8 JSON");
    }
    if (
      document.schema_version !== 2 ||
      typeof document.user !== "string" ||
      document.user.toLowerCase() !== expected.login.toLowerCase() ||
      !Array.isArray(document.results) ||
      document.results.length > 4096
    ) {
      throw new GitHubProviderError(409, "result file envelope did not match the accepted identity");
    }
    const identifier = await expectedResultId(
      expected.login,
      expected.declaredModel,
      expected.problemId,
      expected.statementRevision,
    );
    if (completion.result_id !== identifier) {
      throw new GitHubProviderError(409, "result identifier did not match the accepted identity");
    }
    const matches = document.results.filter((value) => {
      if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
      return (value as Record<string, unknown>).result_id === identifier;
    });
    if (matches.length !== 1) {
      throw new GitHubProviderError(409, "result file did not contain exactly one accepted identity");
    }
    const record = matches[0] as Record<string, unknown>;
    if (
      record.problem_id !== expected.problemId ||
      record.statement_revision !== expected.statementRevision ||
      record.declared_model !== expected.declaredModel
    ) {
      throw new GitHubProviderError(409, "result record disagreed with the accepted identity");
    }
    return { resultId: identifier, treeDigest };
  }

  async verifyLegacyResult(
    ownerLogin: string,
    resultsCommit: string,
    requestedResultId: string,
  ): Promise<VerifiedLegacyResult> {
    if (!this.#resultFetcher || !this.#resultsProtectedBranch) {
      throw new GitHubProviderError(503, "result verification authority is not configured");
    }
    if (!LOGIN.test(ownerLogin) || !COMMIT.test(resultsCommit) || !RESULT_ID.test(requestedResultId)) {
      throw new GitHubProviderError(409, "legacy result request was invalid");
    }
    const resultsPath = `results/${ownerLogin}.json`;
    const headers = providerHeaders();
    headers.set("x-lean-eval-expected-commit", resultsCommit);
    const branchResponse = await this.#resultFetcher(
      `${API}/repos/${RESULTS_REPOSITORY}/branches/${this.#resultsProtectedBranch}`,
      { headers, redirect: "manual", signal: AbortSignal.timeout(5000) },
    );
    const branch = await jsonResponse(branchResponse, "protected Results branch response");
    const branchCommit = object(branch.commit, "protected Results branch commit");
    if (
      branch.name !== this.#resultsProtectedBranch ||
      branch.protected !== true ||
      typeof branchCommit.sha !== "string" ||
      !COMMIT.test(branchCommit.sha)
    ) {
      throw new GitHubProviderError(502, "protected Results branch response fields were invalid");
    }
    const compareResponse = await this.#resultFetcher(
      `${API}/repos/${RESULTS_REPOSITORY}/compare/${resultsCommit}...${this.#resultsProtectedBranch}`,
      { headers, redirect: "manual", signal: AbortSignal.timeout(5000) },
    );
    const comparison = await jsonResponse(compareResponse, "Results ancestry response");
    const comparisonHead = comparisonTerminalSha(
      comparison,
      resultsCommit,
      "Results ancestry response",
      "Results commit is not an ancestor of the protected environment branch",
    );
    if (comparisonHead !== branchCommit.sha) {
      throw new GitHubProviderError(503, "protected Results branch moved during ancestry verification");
    }
    const query = new URLSearchParams({ ref: resultsCommit });
    const response = await this.#resultFetcher(
      `${API}/repos/${RESULTS_REPOSITORY}/contents/${resultsPath}?${query.toString()}`,
      { headers, redirect: "manual", signal: AbortSignal.timeout(5000) },
    );
    const data = await jsonResponse(response, "legacy result contents response");
    if (
      data.type !== "file" ||
      data.path !== resultsPath ||
      data.encoding !== "base64" ||
      typeof data.content !== "string" ||
      typeof data.size !== "number" ||
      !Number.isSafeInteger(data.size) ||
      data.size < 1 ||
      data.size > MAX_RESULT_FILE_BYTES
    ) {
      throw new GitHubProviderError(502, "legacy result contents response fields were invalid");
    }
    let bytes: Uint8Array;
    try {
      const binary = atob(data.content.replaceAll(/\s/g, ""));
      bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    } catch {
      throw new GitHubProviderError(502, "legacy result contents were not valid base64");
    }
    if (bytes.byteLength !== data.size) {
      throw new GitHubProviderError(502, "legacy result contents size disagreed with GitHub");
    }
    let document: Record<string, unknown>;
    try {
      document = object(
        JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes)) as unknown,
        "legacy result file",
      );
    } catch (caught) {
      if (caught instanceof GitHubProviderError) throw caught;
      throw new GitHubProviderError(502, "legacy result file was not valid UTF-8 JSON");
    }
    exactKeys(document, ["results", "schema_version", "user"], "legacy result file");
    if (
      document.schema_version !== 2 ||
      typeof document.user !== "string" ||
      document.user.toLowerCase() !== ownerLogin ||
      !Array.isArray(document.results) ||
      document.results.length > 4096
    ) {
      throw new GitHubProviderError(409, "legacy result file did not belong to the authenticated owner");
    }
    const matches = document.results.filter((value) => {
      if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
      return (value as Record<string, unknown>).result_id === requestedResultId;
    });
    if (matches.length !== 1) {
      throw new GitHubProviderError(409, "legacy result file did not contain exactly one requested identity");
    }
    const record = object(matches[0], "legacy result record");
    exactKeys(record, [
      "accepted_at",
      "benchmark_commit",
      "declared_model",
      "intake",
      "problem_id",
      "production_metadata",
      "result_id",
      "statement_revision",
      "submission",
    ], "legacy result record");
    if (
      typeof record.declared_model !== "string" ||
      record.declared_model.length === 0 ||
      new TextEncoder().encode(record.declared_model).byteLength > 256 ||
      containsControlCharacter(record.declared_model) ||
      typeof record.problem_id !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(record.problem_id) ||
      typeof record.statement_revision !== "number" ||
      !Number.isSafeInteger(record.statement_revision) ||
      record.statement_revision < 1
    ) {
      throw new GitHubProviderError(409, "legacy result identity fields were invalid");
    }
    const tupleMatches = document.results.filter((value) => {
      if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
      const candidate = value as Record<string, unknown>;
      return candidate.declared_model === record.declared_model &&
        candidate.problem_id === record.problem_id &&
        candidate.statement_revision === record.statement_revision;
    });
    if (tupleMatches.length !== 1) {
      throw new GitHubProviderError(409, "legacy result file did not contain one unique immutable identity tuple");
    }
    let recomputedResultId: string;
    try {
      recomputedResultId = await resultId(
        ownerLogin,
        record.declared_model,
        record.problem_id,
        record.statement_revision,
      );
    } catch (caught) {
      if (caught instanceof TypeError) {
        throw new GitHubProviderError(409, "legacy result identity was not canonicalizable Unicode");
      }
      throw caught;
    }
    if (record.result_id !== recomputedResultId || requestedResultId !== recomputedResultId) {
      throw new GitHubProviderError(409, "legacy result identity did not match its schema-version-2 tuple");
    }
    if (
      typeof record.accepted_at !== "string" ||
      !isSecondPrecisionUtcTimestamp(record.accepted_at) ||
      typeof record.benchmark_commit !== "string" ||
      !COMMIT.test(record.benchmark_commit)
    ) {
      throw new GitHubProviderError(409, "legacy result acceptance fields were invalid");
    }
    const intake = object(record.intake, "legacy result intake");
    if (intake.kind === "issue") {
      exactKeys(intake, ["issue_number", "kind"], "legacy result issue intake");
      if (typeof intake.issue_number !== "number" || !Number.isSafeInteger(intake.issue_number) || intake.issue_number < 1) {
        throw new GitHubProviderError(409, "legacy result issue intake was invalid");
      }
    } else if (intake.kind === "server") {
      exactKeys(intake, ["kind", "submission_id"], "legacy result server intake");
      if (
        typeof intake.submission_id !== "string" ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(intake.submission_id)
      ) {
        throw new GitHubProviderError(409, "legacy result server intake was invalid");
      }
    } else {
      throw new GitHubProviderError(409, "legacy result intake kind was invalid");
    }
    const submission = object(record.submission, "legacy result submission");
    exactKeys(submission, ["kind", "public", "ref", "repo"], "legacy result submission");
    if (
      (submission.kind !== "github_repo" && submission.kind !== "gist") ||
      typeof submission.repo !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9._-]+$/.test(submission.repo) ||
      typeof submission.ref !== "string" ||
      !COMMIT.test(submission.ref) ||
      typeof submission.public !== "boolean"
    ) {
      throw new GitHubProviderError(409, "legacy result submission was invalid");
    }
    const production = object(record.production_metadata, "legacy result production metadata");
    assertBoundedLegacyMetadata(production);
    const description = production.production_description;
    if (
      description !== undefined &&
      (typeof description !== "string" ||
        description.trim().length === 0 ||
        description.includes("\0") ||
        Array.from(description).length > 4000)
    ) {
      throw new GitHubProviderError(409, "legacy result production description was invalid");
    }
    const publicationStatus = production.solution_publication_status;
    const publicationDate = production.solution_publication_date;
    if (
      publicationStatus !== undefined &&
      publicationStatus !== "private" &&
      publicationStatus !== "planned" &&
      publicationStatus !== "published"
    ) {
      throw new GitHubProviderError(409, "legacy result publication status was invalid");
    }
    if (
      (publicationStatus === "published" && !submission.public) ||
      ((publicationStatus === "private" || publicationStatus === "planned") && submission.public)
    ) {
      throw new GitHubProviderError(409, "legacy result publication status disagreed with source visibility");
    }
    if (publicationStatus === "planned" || publicationStatus === "published") {
      if (
        typeof publicationDate !== "string" ||
        !isIsoCalendarDate(publicationDate)
      ) {
        throw new GitHubProviderError(409, "legacy result publication date was invalid");
      }
    } else if (publicationDate !== undefined) {
      throw new GitHubProviderError(409, "legacy result publication date was not allowed");
    }
    let canonicalRecordSha256: string;
    try {
      canonicalRecordSha256 = await canonicalSha256Hex(canonicalJson(record));
    } catch (caught) {
      if (caught instanceof TypeError) {
        throw new GitHubProviderError(409, "legacy result record was not canonicalizable Unicode");
      }
      throw caught;
    }
    return {
      resultId: recomputedResultId,
      ownerLogin,
      baseResult: {
        declared_model: record.declared_model,
        problem_id: record.problem_id,
        statement_revision: record.statement_revision,
        results_repository: RESULTS_REPOSITORY,
        results_commit: resultsCommit,
        results_path: resultsPath,
        canonical_record_sha256: canonicalRecordSha256,
      },
    };
  }

  async verifyProblemRepairComparator(
    request: ProblemRepairComparatorRequest,
  ): Promise<ComparatorEvidence> {
    if (
      !LOGIN.test(request.ownerLogin) ||
      !COMMIT.test(request.resultsCommit) ||
      !RESULT_ID.test(request.resultId) ||
      request.declaredModel.length === 0 ||
      new TextEncoder().encode(request.declaredModel).byteLength > 256 ||
      containsControlCharacter(request.declaredModel) ||
      !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(request.baseProblemId) ||
      !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(request.correctedProblemId) ||
      !Number.isSafeInteger(request.baseStatementRevision) ||
      request.baseStatementRevision < 1 ||
      !Number.isSafeInteger(request.correctedStatementRevision) ||
      request.correctedStatementRevision < 1
    ) {
      throw new GitHubProviderError(409, "problem repair comparator request was invalid");
    }
    if (!this.#resultFetcher || !this.#resultsProtectedBranch || !this.#benchmarkFetcher) {
      throw new GitHubProviderError(503, "result and benchmark verification authority is not configured");
    }
    const benchmarkFetcher = this.#benchmarkFetcher;
    const verified = await this.verifyLegacyResult(
      request.ownerLogin,
      request.resultsCommit,
      request.resultId,
    );
    if (
      verified.baseResult.declared_model !== request.declaredModel ||
      verified.baseResult.problem_id !== request.baseProblemId ||
      verified.baseResult.statement_revision !== request.baseStatementRevision
    ) {
      throw new GitHubProviderError(409, "comparator record disagreed with the immutable base result");
    }

    const resultsPath = `results/${request.ownerLogin}.json`;
    const resultHeaders = providerHeaders();
    resultHeaders.set("x-lean-eval-expected-commit", request.resultsCommit);
    const resultQuery = new URLSearchParams({ ref: request.resultsCommit });
    const resultResponse = await this.#resultFetcher(
      `${API}/repos/${RESULTS_REPOSITORY}/contents/${resultsPath}?${resultQuery.toString()}`,
      { headers: resultHeaders, redirect: "manual", signal: AbortSignal.timeout(5000) },
    );
    const resultData = await jsonResponse(resultResponse, "problem repair comparator contents response");
    const resultBytes = inlineContentBytes(
      resultData,
      resultsPath,
      MAX_RESULT_FILE_BYTES,
      "problem repair comparator contents",
    );
    if (typeof resultData.sha !== "string" || !COMMIT.test(resultData.sha)) {
      throw new GitHubProviderError(502, "problem repair comparator blob OID was invalid");
    }
    const blobOid = await gitBlobOid(resultBytes);
    if (blobOid !== resultData.sha) {
      throw new GitHubProviderError(409, "problem repair comparator blob OID disagreed with its bytes");
    }
    let document: Record<string, unknown>;
    try {
      document = object(
        JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(resultBytes)) as unknown,
        "problem repair comparator file",
      );
    } catch (error) {
      if (error instanceof GitHubProviderError) throw error;
      throw new GitHubProviderError(502, "problem repair comparator file was not valid UTF-8 JSON");
    }
    if (!Array.isArray(document.results)) {
      throw new GitHubProviderError(409, "problem repair comparator file had no results array");
    }
    const matches = document.results.filter((value) =>
      value !== null && typeof value === "object" && !Array.isArray(value) &&
      (value as Record<string, unknown>).result_id === request.resultId);
    if (matches.length !== 1) {
      throw new GitHubProviderError(409, "problem repair comparator file did not contain one exact result");
    }
    const record = object(matches[0], "problem repair comparator record");
    const recordSha256 = await canonicalSha256Hex(canonicalJson(record));
    if (
      recordSha256 !== verified.baseResult.canonical_record_sha256 ||
      typeof record.benchmark_commit !== "string" ||
      !COMMIT.test(record.benchmark_commit)
    ) {
      throw new GitHubProviderError(409, "problem repair comparator record binding was invalid");
    }

    const benchmarkCommit = record.benchmark_commit;
    const benchmarkHeaders = providerHeaders();
    benchmarkHeaders.set("x-lean-eval-expected-commit", benchmarkCommit);
    const benchmarkBranchResponse = await benchmarkFetcher(
      `${API}/repos/${BENCHMARK_REPOSITORY}/branches/main`,
      { headers: benchmarkHeaders, redirect: "manual", signal: AbortSignal.timeout(5000) },
    );
    const benchmarkBranch = await jsonResponse(
      benchmarkBranchResponse,
      "protected benchmark branch response",
    );
    const benchmarkBranchCommit = object(
      benchmarkBranch.commit,
      "protected benchmark branch commit",
    );
    if (
      benchmarkBranch.name !== "main" ||
      benchmarkBranch.protected !== true ||
      typeof benchmarkBranchCommit.sha !== "string" ||
      !COMMIT.test(benchmarkBranchCommit.sha)
    ) {
      throw new GitHubProviderError(502, "protected benchmark branch response fields were invalid");
    }
    const benchmarkCompareResponse = await benchmarkFetcher(
      `${API}/repos/${BENCHMARK_REPOSITORY}/compare/${benchmarkCommit}...main`,
      { headers: benchmarkHeaders, redirect: "manual", signal: AbortSignal.timeout(5000) },
    );
    const benchmarkComparison = await jsonResponse(
      benchmarkCompareResponse,
      "benchmark ancestry response",
    );
    const benchmarkHead = comparisonTerminalSha(
      benchmarkComparison,
      benchmarkCommit,
      "benchmark ancestry response",
      "benchmark commit is not an ancestor of protected main",
    );
    if (benchmarkHead !== benchmarkBranchCommit.sha) {
      throw new GitHubProviderError(503, "protected benchmark branch moved during ancestry verification");
    }

    const readManifest = async (
      problemId: string,
      expectedRevision: number,
    ): Promise<ProblemGroup> => {
      const path = `manifests/problems/${problemId}.toml`;
      const query = new URLSearchParams({ ref: benchmarkCommit });
      const response = await benchmarkFetcher(
        `${API}/repos/${BENCHMARK_REPOSITORY}/contents/${path}?${query.toString()}`,
        { headers: benchmarkHeaders, redirect: "manual", signal: AbortSignal.timeout(5000) },
      );
      const data = await jsonResponse(response, "benchmark manifest contents response");
      const bytes = inlineContentBytes(data, path, MAX_MANIFEST_BYTES, "benchmark manifest contents");
      let text: string;
      try {
        text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes);
      } catch {
        throw new GitHubProviderError(502, "benchmark manifest was not valid UTF-8");
      }
      const manifestId = manifestField(text, "id");
      const group = manifestField(text, "group");
      if (
        manifestId !== problemId ||
        manifestRevision(text) !== expectedRevision ||
        !new Set<string>([
          "formalization-evaluation",
          "software-verification",
          "open-conjectures",
        ]).has(group)
      ) {
        throw new GitHubProviderError(409, "benchmark manifest did not bind the requested problem tuple");
      }
      return group as ProblemGroup;
    };

    const [baseGroup, correctedGroup] = await Promise.all([
      readManifest(request.baseProblemId, request.baseStatementRevision),
      readManifest(request.correctedProblemId, request.correctedStatementRevision),
    ]);
    if (baseGroup !== correctedGroup) {
      throw new GitHubProviderError(409, "problem repair cannot change the benchmark group");
    }
    const evidenceWithoutBinding: Omit<ComparatorEvidence, "binding_sha256"> = {
      repository: RESULTS_REPOSITORY,
      commit: request.resultsCommit,
      path: resultsPath,
      blob_oid: blobOid,
      blob_sha256: await canonicalSha256Hex(resultBytes),
      record_sha256: recordSha256,
      verification_method: "github_commit_blob_v1",
      evidence_result_id: request.resultId,
      evidence_owner_login: request.ownerLogin,
      evidence_declared_model: request.declaredModel,
      evidence_base_problem_group: baseGroup,
      evidence_base_problem_id: request.baseProblemId,
      evidence_base_statement_revision: request.baseStatementRevision,
      evidence_base_challenge_id: await challengeId(
        baseGroup,
        request.baseProblemId,
        request.baseStatementRevision,
      ),
      evidence_corrected_problem_group: correctedGroup,
      evidence_corrected_problem_id: request.correctedProblemId,
      evidence_corrected_statement_revision: request.correctedStatementRevision,
      evidence_corrected_challenge_id: await challengeId(
        correctedGroup,
        request.correctedProblemId,
        request.correctedStatementRevision,
      ),
    };
    return {
      ...evidenceWithoutBinding,
      binding_sha256: await comparatorBindingSha256(evidenceWithoutBinding),
    };
  }
}

export function buildDispatchRequest(
  repository: string,
  workflow: string,
  workflowRef: string,
  submissionId: string,
  login: string,
  callbackEnvironment: "staging" | "production",
  submission: Readonly<{
    problem_id: string;
    problem_group: string;
    statement_revision: number;
    declared_model: string;
    source_repository: string;
    source_commit: string;
    source_visibility: string;
    publication_choice: string;
    production_metadata: Readonly<Record<string, unknown>>;
  }>,
): Request {
  const workflowCommit = DISPATCH_REF.exec(workflowRef)?.[1];
  if (!workflowCommit) {
    throw new TypeError("dispatch workflow ref must be an immutable commit-named tag");
  }
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) {
    throw new TypeError("dispatch repository is invalid");
  }
  if (!/^[A-Za-z0-9_.-]+\.ya?ml$/.test(workflow)) throw new TypeError("dispatch workflow is invalid");
  return new Request(`${API}/repos/${repository}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ref: workflowRef,
      inputs: {
        workflow_commit: workflowCommit,
        submission_id: submissionId,
        submitted_by: login,
        problem_id: submission.problem_id,
        problem_group: submission.problem_group,
        statement_revision: String(submission.statement_revision),
        declared_model: submission.declared_model,
        source_repository: submission.source_repository,
        source_commit: submission.source_commit,
        source_visibility: submission.source_visibility,
        publication_choice: submission.publication_choice,
        production_metadata_json: JSON.stringify(submission.production_metadata),
        archive_locator_required: "true",
        archive_sidecar_schema: "3",
        archive_state_callback_required: "true",
        callback_environment: callbackEnvironment,
      },
    }),
  });
}

export function buildPromotionCanaryDispatchRequest(
  repository: string,
  workflowRef: string,
  submissionId: string,
  runId: string,
  runAttempt: string,
): Request {
  const workflowCommit = DISPATCH_REF.exec(workflowRef)?.[1];
  if (!workflowCommit) {
    throw new TypeError("promotion canary workflow ref must be an immutable commit-named tag");
  }
  if (repository !== "leanprover/lean-eval-submissions") {
    throw new TypeError("promotion canary dispatch repository is invalid");
  }
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{10}ca$/.test(submissionId)) {
    throw new TypeError("promotion canary submission identity is invalid");
  }
  if (!/^[1-9][0-9]{0,19}$/.test(runId) || !/^[1-9][0-9]{0,5}$/.test(runAttempt)) {
    throw new TypeError("promotion canary run identity is invalid");
  }
  return new Request(
    `${API}/repos/${repository}/actions/workflows/promotion-canary.yml/dispatches`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ref: workflowRef,
        inputs: {
          workflow_commit: workflowCommit,
          submission_id: submissionId,
          controller_run_id: runId,
          controller_run_attempt: runAttempt,
        },
      }),
    },
  );
}
