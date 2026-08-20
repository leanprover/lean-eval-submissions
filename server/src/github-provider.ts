const API = "https://api.github.com";
const OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token";
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const COMMIT = /^[0-9a-f]{40}$/;
const DISPATCH_REF = /^lean-eval-dispatch\/([0-9a-f]{40})$/;

export type ProviderFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export type GitHubIdentity = Readonly<{ id: number; login: string }>;
export type GitHubRepository = Readonly<{
  fullName: string;
  private: boolean;
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

export class GitHubProvider {
  readonly #fetcher: ProviderFetch;
  readonly #verificationToken: string | undefined;
  readonly #verificationFetcher: ProviderFetch | undefined;
  readonly #dispatchFetcher: ProviderFetch | undefined;

  constructor(
    fetcher: ProviderFetch = fetch,
    verificationToken?: string,
    verificationFetcher?: ProviderFetch,
    dispatchFetcher?: ProviderFetch,
  ) {
    this.#fetcher = fetcher;
    this.#verificationToken = verificationToken;
    this.#verificationFetcher = verificationFetcher;
    this.#dispatchFetcher = dispatchFetcher;
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
    if (!this.#verificationToken && !this.#verificationFetcher) {
      throw new GitHubProviderError(503, "source verification credential is not configured");
    }
    const response = await (this.#verificationFetcher ?? this.#fetcher)(`${API}/gists/${gistId}`, {
      headers: providerHeaders(this.#verificationToken),
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
}

export function buildDispatchRequest(
  repository: string,
  workflow: string,
  workflowRef: string,
  submissionId: string,
  login: string,
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
        archive_sidecar_schema: "2",
      },
    }),
  });
}
