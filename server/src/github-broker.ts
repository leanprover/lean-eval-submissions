const GITHUB_API = "https://api.github.com";
const MAX_BROKER_REQUEST_BYTES = 512 * 1024;
const MAX_GITHUB_ERROR_BYTES = 4096;
const APP_ID = /^[1-9][0-9]{0,15}$/;
// GitHub accepts either the numeric App ID or App client ID as a JWT issuer.
// Bound the current compact 20-character form without assuming an undocumented
// prefix, while retaining GitHub's documented legacy Iv1.<16 hex> example.
const APP_CLIENT_ID = /^(?:[A-Za-z0-9]{20}|Iv1\.[0-9a-fA-F]{16})$/;
const SOURCE_APP_ID = "4666604";
const LEGACY_SOURCE_APP_ID = "Iv23liLATwL7VxAK37uX";
const DISPATCH_APP_ID = "4666633";
const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const COMMIT = /^[0-9a-f]{40}$/;
const DISPATCH_REF = /^lean-eval-dispatch\/([0-9a-f]{40})$/;
// GitHub caps an unpaginated compare at 250 commits. Reject a truncated proof.
const GITHUB_COMPARE_DEFAULT_COMMIT_LIMIT = 250;

type Authority = "source" | "legacy_source" | "dispatch" | "results" | "benchmark";
type BrokerRequest = Readonly<{
  schema_version: 1;
  audience: "lean-eval-submission-server";
  authority: Authority;
  method: string;
  url: string;
  body: string | null;
  expected_commit: string | null;
}>;

export type BrokerRuntimeEnv = Omit<
  BrokerCloudflareEnv,
  | "DEPLOYED_COMMIT"
  | "DEPLOYMENT_ENVIRONMENT"
  | "DISPATCH_REPOSITORY"
  | "DISPATCH_WORKFLOW"
  | "DISPATCH_APP_ID"
  | "DISPATCH_APP_PRIVATE_KEY"
  | "SOURCE_APP_ID"
  | "SOURCE_APP_PRIVATE_KEY"
  | "LEGACY_SOURCE_APP_ID"
  | "LEGACY_SOURCE_APP_PRIVATE_KEY"
> & Readonly<{
  DEPLOYED_COMMIT: string;
  DEPLOYMENT_ENVIRONMENT: "staging" | "production";
  DISPATCH_REPOSITORY: string;
  DISPATCH_WORKFLOW: string;
  DISPATCH_APP_ID?: string;
  DISPATCH_APP_PRIVATE_KEY?: string;
  SOURCE_APP_ID?: string;
  SOURCE_APP_PRIVATE_KEY?: string;
  LEGACY_SOURCE_APP_ID?: string;
  LEGACY_SOURCE_APP_PRIVATE_KEY?: string;
}>;

type GitHubApp = Readonly<{ appId: string; privateKey: string }>;
type CachedToken = Readonly<{ token: string; expiresAt: number }>;

class BrokerError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "BrokerError";
    this.status = status;
  }
}

// This is application-level credential caching, not request state. Only a
// completed token string and its expiry are retained; no Request, Response,
// Promise, contributor content, or body crosses invocations.
const tokenCache = new Map<string, CachedToken>();

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new BrokerError(502, `${label} was not an object`);
  }
  return value as Record<string, unknown>;
}

function comparisonTerminalSha(
  data: Record<string, unknown>,
  expectedBase: string,
  label: string,
  failure: string,
): string {
  const base = object(data.base_commit, `${label} base commit`);
  const mergeBase = object(data.merge_base_commit, `${label} merge base`);
  const commits = data.commits;
  const aheadBy = data.ahead_by;
  const behindBy = data.behind_by;
  const totalCommits = data.total_commits;
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
    throw new BrokerError(409, failure);
  }
  const commitShas = commits.map((value, index) => {
    const commit = object(value, `${label} commit ${String(index)}`);
    if (typeof commit.sha !== "string" || !COMMIT.test(commit.sha)) {
      throw new BrokerError(409, failure);
    }
    return commit.sha;
  });
  let terminalSha: string;
  if (data.status === "identical") {
    if (aheadBy !== 0 || behindBy !== 0 || totalCommits !== 0 || commitShas.length !== 0) {
      throw new BrokerError(409, failure);
    }
    terminalSha = expectedBase;
  } else if (data.status === "ahead") {
    if (
      behindBy !== 0 ||
      aheadBy < 1 ||
      totalCommits !== aheadBy ||
      totalCommits > GITHUB_COMPARE_DEFAULT_COMMIT_LIMIT ||
      commitShas.length !== totalCommits
    ) {
      throw new BrokerError(409, failure);
    }
    const terminal = commitShas.at(-1);
    if (terminal === undefined) throw new BrokerError(409, failure);
    terminalSha = terminal;
  } else {
    throw new BrokerError(409, failure);
  }
  if (data.head_commit !== undefined && data.head_commit !== null) {
    const head = object(data.head_commit, `${label} head commit`);
    if (head.sha !== terminalSha) throw new BrokerError(409, failure);
  }
  return terminalSha;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new BrokerError(400, `${label} fields were invalid`);
  }
}

async function boundedJson(request: Request): Promise<unknown> {
  const declared = request.headers.get("content-length");
  if (declared !== null && Number(declared) > MAX_BROKER_REQUEST_BYTES) {
    throw new BrokerError(413, "broker request was too large");
  }
  if (request.body === null) throw new BrokerError(400, "broker request body was missing");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const readResult: unknown = await reader.read();
    const item = object(readResult, "broker request stream result");
    if (item.done === true) break;
    if (!(item.value instanceof Uint8Array)) throw new BrokerError(400, "broker request stream was invalid");
    total += item.value.byteLength;
    if (total > MAX_BROKER_REQUEST_BYTES) {
      await reader.cancel();
      throw new BrokerError(413, "broker request was too large");
    }
    chunks.push(item.value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes)) as unknown;
  } catch {
    throw new BrokerError(400, "broker request was not valid UTF-8 JSON");
  }
}

function decodeRequest(value: unknown): BrokerRequest {
  const data = object(value, "broker request");
  exactKeys(
    data,
    ["schema_version", "audience", "authority", "method", "url", "body", "expected_commit"],
    "broker request",
  );
  if (
    data.schema_version !== 1 ||
    data.audience !== "lean-eval-submission-server" ||
    (data.authority !== "source" && data.authority !== "legacy_source" && data.authority !== "dispatch" &&
      data.authority !== "results" && data.authority !== "benchmark") ||
    (data.method !== "GET" && data.method !== "POST" && data.method !== "PATCH") ||
    typeof data.url !== "string" ||
    (data.body !== null && typeof data.body !== "string") ||
    (data.expected_commit !== null && (typeof data.expected_commit !== "string" || !COMMIT.test(data.expected_commit)))
  ) {
    throw new BrokerError(400, "broker request values were invalid");
  }
  return {
    schema_version: 1,
    audience: "lean-eval-submission-server",
    authority: data.authority,
    method: data.method,
    url: data.url,
    body: data.body,
    expected_commit: data.expected_commit,
  };
}

function repositoryFromPath(pathname: string): { repository: string; suffix: string } {
  const match = /^\/repos\/([^/]+\/[^/]+)(\/.*)?$/.exec(pathname);
  if (!match?.[1] || !REPOSITORY.test(match[1])) {
    throw new BrokerError(403, "GitHub request did not target one repository");
  }
  return { repository: match[1], suffix: match[2] ?? "" };
}

function assertSourceRequest(request: BrokerRequest, url: URL): string {
  if (request.method !== "GET" || request.body !== null) {
    throw new BrokerError(403, "source authority is read-only");
  }
  if (url.pathname.startsWith("/gists/")) {
    throw new BrokerError(501, "gist proof must use the anonymous exact-ID verifier");
  }
  const { repository, suffix } = repositoryFromPath(url.pathname);
  const metadata = suffix === "";
  const commit = /^\/git\/commits\/[0-9a-f]{40}$/.test(suffix);
  const tagRef = /^\/git\/ref\/tags\/lean-eval%2F[0-9a-f-]{36}$/i.test(suffix);
  const annotatedTag = /^\/git\/tags\/[0-9a-f]{40}$/.test(suffix);
  if (!metadata && !commit && !tagRef && !annotatedTag) {
    throw new BrokerError(403, "source operation was not allowlisted");
  }
  if ((commit || tagRef || annotatedTag) !== (request.expected_commit !== null)) {
    throw new BrokerError(400, "source object request lacked an immutable commit proof");
  }
  if (commit && suffix !== `/git/commits/${request.expected_commit ?? ""}`) {
    throw new BrokerError(403, "source commit path did not match its immutable proof");
  }
  if (request.authority === "legacy_source" && (tagRef || annotatedTag)) {
    throw new BrokerError(403, "legacy source authority is limited to repository and commit verification");
  }
  return repository;
}

function decodeDispatchBody(body: string | null, expectedRepository: string, expectedWorkflow: string): void {
  if (body === null) throw new BrokerError(400, "dispatch body was missing");
  let parsed: unknown;
  try {
    parsed = JSON.parse(body) as unknown;
  } catch {
    throw new BrokerError(400, "dispatch body was not JSON");
  }
  const data = object(parsed, "dispatch body");
  exactKeys(data, ["ref", "inputs"], "dispatch body");
  const inputs = object(data.inputs, "dispatch inputs");
  const commit = typeof data.ref === "string" ? DISPATCH_REF.exec(data.ref)?.[1] : undefined;
  if (!commit || inputs.workflow_commit !== commit) {
    throw new BrokerError(403, "dispatch did not bind the immutable workflow commit");
  }
  if (
    inputs.archive_state_callback_required !== "true" ||
    (inputs.callback_environment !== "staging" && inputs.callback_environment !== "production")
  ) {
    throw new BrokerError(403, "dispatch did not bind the reviewed State callback");
  }
  if (expectedRepository !== "leanprover/lean-eval-submissions" || expectedWorkflow !== "submission.yml") {
    throw new BrokerError(503, "broker dispatch configuration was not the reviewed target");
  }
}

function decodePromotionCanaryDispatchBody(body: string | null, env: BrokerRuntimeEnv): void {
  if (body === null) throw new BrokerError(400, "promotion canary dispatch body was missing");
  let parsed: unknown;
  try {
    parsed = JSON.parse(body) as unknown;
  } catch {
    throw new BrokerError(400, "promotion canary dispatch body was not JSON");
  }
  const data = object(parsed, "promotion canary dispatch body");
  exactKeys(data, ["ref", "inputs"], "promotion canary dispatch body");
  const inputs = object(data.inputs, "promotion canary dispatch inputs");
  exactKeys(
    inputs,
    ["controller_run_attempt", "controller_run_id", "submission_id", "workflow_commit"],
    "promotion canary dispatch inputs",
  );
  const commit = typeof data.ref === "string" ? DISPATCH_REF.exec(data.ref)?.[1] : undefined;
  if (
    env.DEPLOYMENT_ENVIRONMENT !== "staging" ||
    env.DISPATCH_REPOSITORY !== "leanprover/lean-eval-submissions" ||
    !commit ||
    inputs.workflow_commit !== commit ||
    typeof inputs.submission_id !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{10}ca$/.test(inputs.submission_id) ||
    typeof inputs.controller_run_id !== "string" ||
    !/^[1-9][0-9]{0,19}$/.test(inputs.controller_run_id) ||
    typeof inputs.controller_run_attempt !== "string" ||
    !/^[1-9][0-9]{0,5}$/.test(inputs.controller_run_attempt)
  ) {
    throw new BrokerError(403, "promotion canary dispatch was not exactly allowlisted");
  }
}

function assertDispatchRequest(request: BrokerRequest, url: URL, env: BrokerRuntimeEnv): string {
  const { repository, suffix } = repositoryFromPath(url.pathname);
  const expectedSuffix = `/actions/workflows/${env.DISPATCH_WORKFLOW}/dispatches`;
  const canarySuffix = "/actions/workflows/promotion-canary.yml/dispatches";
  if (
    request.method !== "POST" ||
    repository.toLowerCase() !== env.DISPATCH_REPOSITORY.toLowerCase() ||
    (suffix !== expectedSuffix && suffix !== canarySuffix) ||
    request.expected_commit !== null
  ) {
    throw new BrokerError(403, "dispatch operation was not allowlisted");
  }
  if (suffix === canarySuffix) decodePromotionCanaryDispatchBody(request.body, env);
  else decodeDispatchBody(request.body, env.DISPATCH_REPOSITORY, env.DISPATCH_WORKFLOW);
  return repository;
}

function assertResultsRequest(request: BrokerRequest, url: URL, env: BrokerRuntimeEnv): string {
  const { repository, suffix } = repositoryFromPath(url.pathname);
  const protectedBranch = env.DEPLOYMENT_ENVIRONMENT === "production" ? "main" : "staging-results";
  const contentRead =
    /^\/contents\/results\/[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?\.json$/.test(suffix) &&
    url.searchParams.size === 1 &&
    url.searchParams.get("ref") === request.expected_commit;
  const branchRead = suffix === `/branches/${protectedBranch}` && url.searchParams.size === 0;
  const compareRead = suffix === `/compare/${request.expected_commit ?? ""}...${protectedBranch}` &&
    url.searchParams.size === 0;
  if (
    request.method !== "GET" ||
    request.body !== null ||
    request.expected_commit === null ||
    repository.toLowerCase() !== env.DISPATCH_REPOSITORY.toLowerCase() ||
    (!contentRead && !branchRead && !compareRead)
  ) {
    throw new BrokerError(403, "result verification operation was not allowlisted");
  }
  return repository;
}

function assertBenchmarkRequest(request: BrokerRequest, url: URL): string {
  const { repository, suffix } = repositoryFromPath(url.pathname);
  const contentRead = /^\/contents\/manifests\/problems\/[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.toml$/.test(suffix) &&
    url.searchParams.size === 1 &&
    url.searchParams.get("ref") === request.expected_commit;
  const branchRead = suffix === "/branches/main" && url.searchParams.size === 0;
  const compareRead = suffix === `/compare/${request.expected_commit ?? ""}...main` &&
    url.searchParams.size === 0;
  if (
    request.method !== "GET" ||
    request.body !== null ||
    request.expected_commit === null ||
    repository.toLowerCase() !== "leanprover/lean-eval" ||
    (!contentRead && !branchRead && !compareRead)
  ) {
    throw new BrokerError(403, "benchmark verification operation was not allowlisted");
  }
  return repository;
}

function appFor(authority: Authority, env: BrokerRuntimeEnv): GitHubApp {
  const dispatchAuthority = authority === "dispatch" || authority === "results";
  const legacySourceAuthority = authority === "legacy_source";
  const appId = dispatchAuthority
    ? env.DISPATCH_APP_ID
    : legacySourceAuthority
      ? env.LEGACY_SOURCE_APP_ID
      : env.SOURCE_APP_ID;
  const privateKey = dispatchAuthority
    ? env.DISPATCH_APP_PRIVATE_KEY
    : legacySourceAuthority
      ? env.LEGACY_SOURCE_APP_PRIVATE_KEY
      : env.SOURCE_APP_PRIVATE_KEY;
  const expectedAppId = dispatchAuthority
    ? DISPATCH_APP_ID
    : legacySourceAuthority
      ? LEGACY_SOURCE_APP_ID
      : SOURCE_APP_ID;
  if (appId === undefined || privateKey === undefined ||
    appId !== expectedAppId ||
    (legacySourceAuthority ? !APP_ID.test(appId) && !APP_CLIENT_ID.test(appId) : !APP_ID.test(appId))) {
    const label = dispatchAuthority ? "dispatch" : legacySourceAuthority ? "legacy source" : "source";
    throw new BrokerError(503, `${label} GitHub App is not configured`);
  }
  return { appId, privateKey };
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function utf8Base64Url(value: unknown): string {
  return base64Url(new TextEncoder().encode(JSON.stringify(value)));
}

function derLength(length: number): Uint8Array {
  if (length < 128) return Uint8Array.of(length);
  const bytes: number[] = [];
  for (let value = length; value > 0; value >>>= 8) bytes.unshift(value & 0xff);
  return Uint8Array.of(0x80 | bytes.length, ...bytes);
}

function der(tag: number, value: Uint8Array): Uint8Array {
  const length = derLength(value.byteLength);
  const encoded = new Uint8Array(1 + length.byteLength + value.byteLength);
  encoded[0] = tag;
  encoded.set(length, 1);
  encoded.set(value, 1 + length.byteLength);
  return encoded;
}

function concat(...values: readonly Uint8Array[]): Uint8Array {
  const encoded = new Uint8Array(values.reduce((total, value) => total + value.byteLength, 0));
  let offset = 0;
  for (const value of values) {
    encoded.set(value, offset);
    offset += value.byteLength;
  }
  return encoded;
}

function pemPrivateKey(pem: string): Uint8Array {
  const pkcs8 = pem.includes("-----BEGIN PRIVATE KEY-----");
  const pkcs1 = pem.includes("-----BEGIN RSA PRIVATE KEY-----");
  if (!pkcs8 && !pkcs1) throw new BrokerError(503, "GitHub App private key format was invalid");
  const body = pem.replace(/-----BEGIN (?:RSA )?PRIVATE KEY-----|-----END (?:RSA )?PRIVATE KEY-----|\s/g, "");
  let decoded: Uint8Array;
  try {
    decoded = Uint8Array.from(atob(body), (character) => character.charCodeAt(0));
  } catch {
    throw new BrokerError(503, "GitHub App private key encoding was invalid");
  }
  if (pkcs8) return decoded;
  const version = Uint8Array.of(0x02, 0x01, 0x00);
  const rsaAlgorithm = Uint8Array.of(
    0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00,
  );
  return der(0x30, concat(version, rsaAlgorithm, der(0x04, decoded)));
}

async function appJwt(app: GitHubApp, nowMs: number): Promise<string> {
  const issuedAt = Math.floor(nowMs / 1000) - 60;
  const unsigned = `${utf8Base64Url({ alg: "RS256", typ: "JWT" })}.${utf8Base64Url({
    iat: issuedAt,
    exp: issuedAt + 9 * 60,
    iss: app.appId,
  })}`;
  let key: CryptoKey;
  try {
    key = await crypto.subtle.importKey(
      "pkcs8",
      pemPrivateKey(app.privateKey),
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["sign"],
    );
  } catch {
    throw new BrokerError(503, "GitHub App private key could not be imported");
  }
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(unsigned));
  return `${unsigned}.${base64Url(new Uint8Array(signature))}`;
}

function githubHeaders(token: string): Headers {
  return new Headers({
    accept: "application/vnd.github+json",
    authorization: `Bearer ${token}`,
    "user-agent": "lean-eval-github-broker",
    "x-github-api-version": "2022-11-28",
  });
}

function anonymousGitHubHeaders(): Headers {
  return new Headers({
    accept: "application/vnd.github+json",
    "user-agent": "lean-eval-github-broker",
    "x-github-api-version": "2022-11-28",
  });
}

async function boundedError(response: Response): Promise<string> {
  const text = await response.text();
  return text.slice(0, MAX_GITHUB_ERROR_BYTES);
}

async function installationToken(
  authority: Authority,
  repository: string,
  env: BrokerRuntimeEnv,
  fetcher: typeof fetch,
  nowMs: number,
): Promise<string> {
  const app = appFor(authority, env);
  const key = `${authority}:${app.appId}:${repository.toLowerCase()}`;
  const cached = tokenCache.get(key);
  if (cached && cached.expiresAt - nowMs > 2 * 60 * 1000) return cached.token;
  const jwt = await appJwt(app, nowMs);
  const installationResponse = await fetcher(`${GITHUB_API}/repos/${repository}/installation`, {
    headers: githubHeaders(jwt),
    redirect: "manual",
    signal: AbortSignal.timeout(5000),
  });
  if (!installationResponse.ok) {
    throw new BrokerError(installationResponse.status, `GitHub App installation lookup failed: ${await boundedError(installationResponse)}`);
  }
  const installation = object(await installationResponse.json<unknown>(), "GitHub App installation");
  if (!Number.isSafeInteger(installation.id) || Number(installation.id) < 1) {
    throw new BrokerError(502, "GitHub App installation ID was invalid");
  }
  const permissions = authority === "dispatch"
    ? { actions: "write", contents: "read", metadata: "read" }
    : { contents: "read", metadata: "read" };
  const tokenResponse = await fetcher(`${GITHUB_API}/app/installations/${String(installation.id)}/access_tokens`, {
    method: "POST",
    headers: new Headers({ ...Object.fromEntries(githubHeaders(jwt)), "content-type": "application/json" }),
    body: JSON.stringify({ repositories: [repository.split("/")[1]], permissions }),
    redirect: "manual",
    signal: AbortSignal.timeout(5000),
  });
  if (!tokenResponse.ok) {
    throw new BrokerError(tokenResponse.status, `GitHub App token exchange failed: ${await boundedError(tokenResponse)}`);
  }
  const tokenData = object(await tokenResponse.json<unknown>(), "GitHub App token");
  if (
    typeof tokenData.token !== "string" ||
    tokenData.token.length < 16 ||
    tokenData.token.length > 1024 ||
    typeof tokenData.expires_at !== "string"
  ) {
    throw new BrokerError(502, "GitHub App token response was invalid");
  }
  const expiresAt = Date.parse(tokenData.expires_at);
  if (!Number.isFinite(expiresAt) || expiresAt <= nowMs || expiresAt > nowMs + 65 * 60 * 1000) {
    throw new BrokerError(502, "GitHub App token expiry was invalid");
  }
  if (tokenCache.size >= 64) tokenCache.delete(tokenCache.keys().next().value ?? "");
  tokenCache.set(key, { token: tokenData.token, expiresAt });
  return tokenData.token;
}

function safeResponse(response: Response): Response {
  const headers = new Headers();
  for (const name of ["content-type", "retry-after", "x-github-request-id", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"]) {
    const value = response.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  headers.set("cache-control", "no-store");
  return new Response(response.body, { status: response.status, headers });
}

async function validateSourceResponse(response: Response, request: BrokerRequest, url: URL, repository: string): Promise<void> {
  if (!response.ok) return;
  let data: Record<string, unknown>;
  try {
    data = object(await response.clone().json<unknown>(), "GitHub source response");
  } catch (error) {
    if (error instanceof BrokerError) throw error;
    throw new BrokerError(502, "GitHub source response was not valid JSON");
  }
  const suffix = repositoryFromPath(url.pathname).suffix;
  if (suffix === "") {
    if (typeof data.full_name !== "string" || data.full_name.toLowerCase() !== repository.toLowerCase() || typeof data.private !== "boolean") {
      throw new BrokerError(502, "GitHub repository identity response was invalid");
    }
    return;
  }
  const expected = request.expected_commit;
  if (expected === null) throw new BrokerError(500, "validated source response lacked its commit proof");
  if (suffix.startsWith("/git/commits/")) {
    if (data.sha !== expected) throw new BrokerError(409, "source commit identity changed");
    return;
  }
  const target = object(data.object, "GitHub tag target");
  if (suffix.startsWith("/git/ref/tags/")) {
    if (target.type === "commit" && target.sha !== expected) throw new BrokerError(409, "source tag moved");
    if (target.type !== "commit" && target.type !== "tag") throw new BrokerError(409, "source tag target was invalid");
    return;
  }
  if (target.type !== "commit" || target.sha !== expected) {
    throw new BrokerError(409, "annotated source tag moved");
  }
}

async function validateResultsResponse(
  response: Response,
  request: BrokerRequest,
  url: URL,
  env: BrokerRuntimeEnv,
): Promise<void> {
  if (!response.ok) return;
  let data: Record<string, unknown>;
  try {
    data = object(await response.clone().json<unknown>(), "GitHub result response");
  } catch (error) {
    if (error instanceof BrokerError) throw error;
    throw new BrokerError(502, "GitHub result response was not valid JSON");
  }
  const suffix = repositoryFromPath(url.pathname).suffix;
  const protectedBranch = env.DEPLOYMENT_ENVIRONMENT === "production" ? "main" : "staging-results";
  if (suffix === `/branches/${protectedBranch}`) {
    const commit = object(data.commit, "protected Results branch commit");
    if (data.name !== protectedBranch || data.protected !== true || typeof commit.sha !== "string" || !COMMIT.test(commit.sha)) {
      throw new BrokerError(502, "protected Results branch response fields were invalid");
    }
    return;
  }
  if (suffix === `/compare/${request.expected_commit ?? ""}...${protectedBranch}`) {
    comparisonTerminalSha(
      data,
      request.expected_commit ?? "",
      "Results comparison",
      "Results commit did not descend to the protected environment branch",
    );
    return;
  }
  const expectedPath = suffix.slice("/contents/".length);
  if (
    data.type !== "file" ||
    data.path !== expectedPath ||
    data.encoding !== "base64" ||
    typeof data.content !== "string" ||
    data.content.length > 3 * 1024 * 1024 ||
    typeof data.size !== "number" ||
    !Number.isSafeInteger(data.size) ||
    data.size < 1 ||
    data.size > 2 * 1024 * 1024
  ) {
    throw new BrokerError(502, "GitHub result response fields were invalid");
  }
}

async function validateBenchmarkResponse(
  response: Response,
  request: BrokerRequest,
  url: URL,
): Promise<void> {
  if (!response.ok) return;
  let data: Record<string, unknown>;
  try {
    data = object(await response.clone().json<unknown>(), "GitHub benchmark response");
  } catch (error) {
    if (error instanceof BrokerError) throw error;
    throw new BrokerError(502, "GitHub benchmark response was not valid JSON");
  }
  const suffix = repositoryFromPath(url.pathname).suffix;
  if (suffix === "/branches/main") {
    const commit = object(data.commit, "protected benchmark branch commit");
    if (data.name !== "main" || data.protected !== true ||
      typeof commit.sha !== "string" || !COMMIT.test(commit.sha)) {
      throw new BrokerError(502, "protected benchmark branch response fields were invalid");
    }
    return;
  }
  if (suffix === `/compare/${request.expected_commit ?? ""}...main`) {
    comparisonTerminalSha(
      data,
      request.expected_commit ?? "",
      "benchmark comparison",
      "benchmark commit did not descend to protected main",
    );
    return;
  }
  const expectedPath = suffix.slice("/contents/".length);
  if (
    data.type !== "file" ||
    data.path !== expectedPath ||
    data.encoding !== "base64" ||
    typeof data.content !== "string" ||
    data.content.length > 128 * 1024 ||
    typeof data.size !== "number" ||
    !Number.isSafeInteger(data.size) ||
    data.size < 1 ||
    data.size > 64 * 1024
  ) {
    throw new BrokerError(502, "GitHub benchmark response fields were invalid");
  }
}

async function proxy(
  brokerRequest: BrokerRequest,
  env: BrokerRuntimeEnv,
  fetcher: typeof fetch,
  nowMs: number,
): Promise<Response> {
  let url: URL;
  try {
    url = new URL(brokerRequest.url);
  } catch {
    throw new BrokerError(400, "GitHub URL was invalid");
  }
  if (url.origin !== GITHUB_API || url.username || url.password || url.hash) {
    throw new BrokerError(403, "broker can reach only the GitHub API origin");
  }
  const repository = brokerRequest.authority === "source" || brokerRequest.authority === "legacy_source"
    ? assertSourceRequest(brokerRequest, url)
    : brokerRequest.authority === "dispatch"
      ? assertDispatchRequest(brokerRequest, url, env)
      : brokerRequest.authority === "results"
        ? assertResultsRequest(brokerRequest, url, env)
        : assertBenchmarkRequest(brokerRequest, url);
  const headers = brokerRequest.authority === "benchmark"
    ? anonymousGitHubHeaders()
    : githubHeaders(await installationToken(brokerRequest.authority, repository, env, fetcher, nowMs));
  if (brokerRequest.body !== null) headers.set("content-type", "application/json");
  const response = await fetcher(url, {
    method: brokerRequest.method,
    headers,
    body: brokerRequest.body,
    redirect: "manual",
    signal: AbortSignal.timeout(5000),
  });
  if (brokerRequest.authority === "source" || brokerRequest.authority === "legacy_source") {
    await validateSourceResponse(response, brokerRequest, url, repository);
  } else if (brokerRequest.authority === "results") {
    await validateResultsResponse(response, brokerRequest, url, env);
  } else if (brokerRequest.authority === "benchmark") {
    await validateBenchmarkResponse(response, brokerRequest, url);
  }
  return safeResponse(response);
}

function jsonError(status: number, message: string): Response {
  return Response.json(
    { error: status >= 500 ? "broker_unavailable" : "broker_request_rejected", detail: message.slice(0, 300) },
    { status, headers: { "cache-control": "no-store", "content-type": "application/json; charset=utf-8" } },
  );
}

export async function handleBrokerRequest(
  request: Request,
  env: BrokerRuntimeEnv,
  fetcher: typeof fetch = fetch,
  nowMs: number = Date.now(),
): Promise<Response> {
  try {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/v1/proxy") {
      throw new BrokerError(404, "broker route was not found");
    }
    if (request.headers.get("content-type")?.split(";", 1)[0] !== "application/json") {
      throw new BrokerError(415, "broker requires application/json");
    }
    return await proxy(decodeRequest(await boundedJson(request)), env, fetcher, nowMs);
  } catch (error) {
    const status = error instanceof BrokerError ? error.status : 500;
    console.error(JSON.stringify({
      event: "github_broker_request_failed",
      deployed_commit: env.DEPLOYED_COMMIT,
      environment: env.DEPLOYMENT_ENVIRONMENT,
      status,
      error_name: error instanceof Error ? error.name : "unknown",
    }));
    return jsonError(status, error instanceof BrokerError ? error.message : "internal broker error");
  }
}
