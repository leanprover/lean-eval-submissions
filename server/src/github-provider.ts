const API = "https://api.github.com";
const OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token";
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const COMMIT = /^[0-9a-f]{40}$/;
const DISPATCH_REF = /^lean-eval-dispatch\/([0-9a-f]{40})$/;
const RESULT_ID = /^r2_[0-9a-f]{64}$/;
const MAX_RESULT_FILE_BYTES = 2 * 1024 * 1024;
const RESULT_ID_DOMAIN = "lean-eval-result-v2\0";
const RESULT_TREE_DOMAIN = "lean-eval-result-tree-v1\0";

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

  constructor(
    fetcher: ProviderFetch = fetch,
    verificationToken?: string,
    verificationFetcher?: ProviderFetch,
    dispatchFetcher?: ProviderFetch,
    resultFetcher?: ProviderFetch,
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
      { headers, signal: AbortSignal.timeout(5000) },
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
