import {
  stateEventPath,
  type StateEvent,
  type WritableResultLifecycleEvent,
  type WritableSubmissionLifecycleEvent,
  type WritableStateEvent,
  validateStateEvent,
} from "./state-event";
import {
  decodeDispatchOutbox,
  decodeSubmissionView,
  dispatchOutboxPath,
  latestLifecycleEventId,
  submissionViewPath,
  type DispatchOutbox,
  type SubmissionView,
} from "./submission-view";

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

type TreeWrite = Readonly<{ path: string; value: unknown }>;

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
  events: readonly WritableStateEvent[],
  writes: readonly TreeWrite[] = [],
  message?: string,
): Promise<string> {
  let treeSha = snapshot.treeSha;
  if (events.length > 0 || writes.length > 0) {
    const tree = await jsonCall(config, fetcher, "/git/trees", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        base_tree: snapshot.treeSha,
        tree: [
          ...events.map((event) => ({
            path: stateEventPath(event),
            mode: "100644",
            type: "blob",
            content: `${JSON.stringify(event, null, 2)}\n`,
          })),
          ...writes.map((write) => write.value === null
            ? { path: write.path, mode: "100644", type: "blob", sha: null }
            : { path: write.path, mode: "100644", type: "blob", content: `${JSON.stringify(write.value, null, 2)}\n` }),
        ],
      }),
    });
    treeSha = requiredSha(object(tree, "created State tree").sha, "created State tree");
  }
  const commit = await jsonCall(config, fetcher, "/git/commits", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      message: message ?? (events.length === 1
        ? `Record ${events[0]?.event_type ?? "State event"} ${events[0]?.event_id ?? ""}`
        : `Record atomic State event batch for ${events[0]?.subject_id ?? "unknown subject"}`),
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

async function readEventAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  eventId: string,
  commit: string,
): Promise<StateEvent> {
  const path = `events/${eventId.replaceAll("-", "").slice(0, 2)}/${eventId}.json`;
  const entry = await readPathAt(config, fetcher, path, commit);
  if (!entry.found) throw new GitHubStateError(502, `${path} referenced by submission view is missing`);
  try {
    validateStateEvent(entry.value);
  } catch (error) {
    throw new GitHubStateError(502, `${path} referenced by submission view is invalid: ${String(error)}`);
  }
  if (stateEventPath(entry.value) !== path) throw new GitHubStateError(502, `${path} event identity disagrees with its path`);
  return entry.value;
}

async function readSubmissionAt(
  config: GitHubStateConfig,
  fetcher: GitHubFetch,
  submissionId: string,
  commit: string,
): Promise<SubmissionView | null> {
  const path = submissionViewPath(submissionId);
  const entry = await readPathAt(config, fetcher, path, commit);
  if (!entry.found) return null;
  let view: SubmissionView;
  try {
    view = decodeSubmissionView(entry.value);
  } catch (error) {
    throw new GitHubStateError(502, `${path} is invalid: ${String(error)}`);
  }
  if (view.submission_id !== submissionId) throw new GitHubStateError(502, `${path} has the wrong submission identity`);
  const archiveEventId = view.schema_version === 2 && view.archive.status !== "pending"
    ? view.archive.event_id
    : null;
  const evaluationEventId = view.schema_version === 2 && view.evaluation.status !== "pending"
    ? view.evaluation.event_id
    : null;
  const eventIds = new Set([
    view.received_event_id,
    view.metadata_event_id,
    ...(view.publication_event_id === null ? [] : [view.publication_event_id]),
    ...(archiveEventId === null ? [] : [archiveEventId]),
    ...(evaluationEventId === null ? [] : [evaluationEventId]),
    ...(view.schema_version === 2 && view.result_event_id !== null ? [view.result_event_id] : []),
  ]);
  const events = await Promise.all([...eventIds].map((eventId) => readEventAt(config, fetcher, eventId, commit)));
  if (view.schema_version === 2 && evaluationEventId !== null && !new Set(["pending", "running"]).has(view.evaluation.status)) {
    const terminal = events.find((event) => event.event_id === evaluationEventId);
    const terminalCause = terminal?.causation_event_id;
    if (terminalCause === null || terminalCause === undefined) {
      throw new GitHubStateError(502, `${path} evaluation terminal event has no cause`);
    }
    if (!eventIds.has(terminalCause)) {
      eventIds.add(terminalCause);
      events.push(await readEventAt(config, fetcher, terminalCause, commit));
    }
  }
  const received = events.find((event) => event.event_id === view.received_event_id);
  if (
    received?.event_type !== "submission.received" ||
    received.subject_id !== submissionId ||
    received.actor.login !== view.owner_login
  ) {
    throw new GitHubStateError(502, `${path} does not match its submission.received event`);
  }
  const expectedReceived = {
    problem_id: view.submission.problem_id,
    statement_revision: view.submission.statement_revision,
    declared_model: view.submission.declared_model,
    source_repository: view.submission.source_repository,
    source_commit: view.submission.source_commit,
    source_visibility: view.submission.source_visibility,
    publication_choice: received.payload.publication_choice,
  };
  if (canonicalJson(received.payload) !== canonicalJson(expectedReceived)) {
    throw new GitHubStateError(502, `${path} submission input does not match its received event`);
  }
  const metadata = events.find((event) => event.event_id === view.metadata_event_id);
  if (
    metadata?.event_type !== "submission.metadata_amended" ||
    metadata.subject_id !== submissionId ||
    metadata.actor.login !== view.owner_login ||
    canonicalJson(metadata.payload.production_metadata) !== canonicalJson(view.production_metadata)
  ) {
    throw new GitHubStateError(502, `${path} does not match its metadata event`);
  }
  if (view.publication_event_id === null) {
    if (view.publication_choice !== received.payload.publication_choice) {
      throw new GitHubStateError(502, `${path} publication choice does not match intake`);
    }
  } else {
    const publication = events.find((event) => event.event_id === view.publication_event_id);
    if (
      publication?.event_type !== "submission.publication_changed" ||
      publication.subject_id !== submissionId ||
      publication.actor.login !== view.owner_login ||
      publication.payload.publication_choice !== view.publication_choice
    ) {
      throw new GitHubStateError(502, `${path} does not match its publication event`);
    }
  }
  if (!eventIds.has(view.mutation_event_id)) {
    throw new GitHubStateError(502, `${path} mutation head is not a referenced owner mutation`);
  }
  if (view.schema_version === 2) {
    if (view.archive.status !== "pending") {
      const archive = events.find((event) => event.event_id === archiveEventId);
      const expectedArchive = archive === undefined ? null : {
        status: archive.event_type.split(".", 2)[1],
        event_id: archive.event_id,
        occurred_at: archive.occurred_at,
        ...archive.payload,
      };
      if (
        archive?.subject_id !== submissionId ||
        !archive.event_type.startsWith("archive.") ||
        canonicalJson(expectedArchive) !== canonicalJson(view.archive)
      ) {
        throw new GitHubStateError(502, `${path} archive summary does not match its lifecycle event`);
      }
    }
    if (view.evaluation.status !== "pending") {
      const current = events.find((event) => event.event_id === evaluationEventId);
      const started = current?.event_type === "evaluation.started"
        ? current
        : events.find((event) => event.event_id === current?.causation_event_id);
      const expectedEvaluation = current === undefined || started?.event_type !== "evaluation.started"
        ? null
        : {
            status: current.event_type === "evaluation.started" ? "running" : current.event_type.split(".", 2)[1],
            event_id: current.event_id,
            occurred_at: current.occurred_at,
            ...started.payload,
            ...(current.event_type === "evaluation.started" ? {} : current.payload),
          };
      if (
        current?.subject_id !== submissionId ||
        started?.subject_id !== submissionId ||
        !current.event_type.startsWith("evaluation.") ||
        canonicalJson(expectedEvaluation) !== canonicalJson(view.evaluation)
      ) {
        throw new GitHubStateError(502, `${path} evaluation summary does not match its lifecycle events`);
      }
    }
    if (view.result_event_id !== null) {
      const result = events.find((event) => event.event_id === view.result_event_id);
      if (
        result?.event_type !== "result.recorded" ||
        result.subject_id !== view.result_id ||
        result.payload.submission_id !== submissionId
      ) {
        throw new GitHubStateError(502, `${path} result identity does not match its lifecycle event`);
      }
    }
  }
  return view;
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

  async #authorizedSnapshot(): Promise<BranchSnapshot> {
    const repository = object(
      await jsonCall(this.#config, this.#fetcher, ""),
      "State repository",
    );
    const permissions = object(repository.permissions, "State repository permissions");
    if (permissions.push !== true) {
      throw new GitHubStateError(403, "State credential does not have push permission");
    }
    return branchSnapshot(this.#config, this.#fetcher);
  }

  async assertAvailable(): Promise<void> {
    await this.#authorizedSnapshot();
  }

  async assertWritable(): Promise<string> {
    const snapshot = await this.#authorizedSnapshot();
    if ((await updateReference(this.#config, this.#fetcher, snapshot.headSha)) !== "applied") {
      throw new GitHubStateError(409, "State branch rejected a same-commit write probe");
    }
    return snapshot.headSha;
  }

  async provePromotionCanaryContention(event: WritableStateEvent): Promise<{
    proofRecorded: boolean;
    idempotent: boolean;
    commit: string;
    created: boolean;
  }> {
    validateStateEvent(event);
    if (event.event_type !== "authentication.nonce_consumed") {
      throw new TypeError("promotion canary evidence must be an authentication.nonce_consumed event");
    }
    const path = stateEventPath(event);
    const snapshot = await branchSnapshot(this.#config, this.#fetcher);
    const existing = await readPathAt(this.#config, this.#fetcher, path, snapshot.headSha);
    if (existing.found) {
      try {
        validateStateEvent(existing.value);
      } catch {
        throw new StateEventConflictError(path);
      }
      if (canonicalJson(existing.value) !== canonicalJson(event)) {
        throw new StateEventConflictError(path);
      }
      return {
        proofRecorded: true,
        idempotent: true,
        commit: snapshot.headSha,
        created: false,
      };
    }

    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const current = attempt === 0
        ? snapshot
        : await branchSnapshot(this.#config, this.#fetcher);
      const winner = await createCommit(
        this.#config,
        this.#fetcher,
        current,
        [],
        [],
        `Promotion canary CAS winner ${event.event_id}`,
      );
      const contender = await createCommit(
        this.#config,
        this.#fetcher,
        current,
        [event],
        [],
        `Promotion canary CAS contender ${event.event_id}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, winner)) !== "applied") {
        if (attempt === MAX_WRITE_ATTEMPTS) {
          throw new GitHubStateError(409, "State branch kept changing before the canary collision");
        }
        await pause(attempt);
        continue;
      }
      if ((await updateReference(this.#config, this.#fetcher, contender)) !== "collision") {
        throw new GitHubStateError(502, "promotion canary competing commit did not collide");
      }
      const outcome = await this.appendEvent(event);
      return {
        proofRecorded: true,
        idempotent: !outcome.created,
        commit: outcome.commit,
        created: outcome.created,
      };
    }
    throw new Error("unreachable promotion canary CAS attempt");
  }

  async readSubmission(submissionId: string): Promise<SubmissionView | null> {
    const snapshot = await branchSnapshot(this.#config, this.#fetcher);
    return readSubmissionAt(this.#config, this.#fetcher, submissionId, snapshot.headSha);
  }

  async acceptSubmission(
    events: readonly WritableStateEvent[],
    view: SubmissionView,
    outbox: DispatchOutbox,
  ): Promise<{ commit: string; created: boolean; view: SubmissionView }> {
    if (events.length !== 3) throw new TypeError("submission acceptance requires exactly three State events");
    for (const event of events) validateStateEvent(event);
    const decodedView = decodeSubmissionView(view);
    const decodedOutbox = decodeDispatchOutbox(outbox);
    if (decodedView.submission_id !== decodedOutbox.submission_id || decodedView.submission_id !== events[1]?.subject_id) {
      throw new TypeError("submission acceptance identities disagree");
    }
    const paths = events.map(stateEventPath);
    const viewPath = submissionViewPath(view.submission_id);
    const outboxPath = dispatchOutboxPath(view.submission_id);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const current = await readSubmissionAt(this.#config, this.#fetcher, view.submission_id, snapshot.headSha);
      if (current !== null) return { commit: snapshot.headSha, created: false, view: current };
      const existing = await Promise.all(
        [...paths, outboxPath].map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)),
      );
      if (existing.some((entry) => entry.found)) {
        throw new StateEventConflictError(paths[existing.findIndex((entry) => entry.found)] ?? viewPath);
      }
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        events,
        [
          { path: viewPath, value: decodedView },
          { path: outboxPath, value: decodedOutbox },
        ],
        `Accept submission ${view.submission_id} and enqueue dispatch`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, created: true, view: decodedView };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) throw new GitHubStateError(409, "State branch kept changing during submission acceptance");
      await pause(attempt);
    }
    throw new Error("unreachable submission acceptance attempt");
  }

  async appendSubmissionMutation(
    event: WritableStateEvent,
    expectedMutationEventId: string,
    nextView: SubmissionView,
  ): Promise<{ commit: string; created: boolean; view: SubmissionView }> {
    validateStateEvent(event);
    if (event.event_type !== "submission.metadata_amended" && event.event_type !== "submission.publication_changed") {
      throw new TypeError("only owner submission mutations may update a submission view");
    }
    const decodedView = decodeSubmissionView(nextView);
    const path = stateEventPath(event);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const current = await readSubmissionAt(this.#config, this.#fetcher, event.subject_id, snapshot.headSha);
      if (current === null) throw new GitHubStateError(404, "submission view does not exist");
      if (current.mutation_event_id === event.event_id) {
        const existing = await readPathAt(this.#config, this.#fetcher, path, snapshot.headSha);
        if (!existing.found || canonicalJson(existing.value) !== canonicalJson(event) || canonicalJson(current) !== canonicalJson(decodedView)) {
          throw new StateEventConflictError(path);
        }
        return { commit: snapshot.headSha, created: false, view: current };
      }
      if (current.mutation_event_id !== expectedMutationEventId) throw new StateEventConflictError(path);
      const existing = await readPathAt(this.#config, this.#fetcher, path, snapshot.headSha);
      if (existing.found) throw new StateEventConflictError(path);
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [event],
        [{ path: submissionViewPath(event.subject_id), value: decodedView }],
        `Record owner mutation ${event.event_id} and refresh submission view`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, created: true, view: decodedView };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) throw new GitHubStateError(409, "State branch kept changing during owner mutation");
      await pause(attempt);
    }
    throw new Error("unreachable owner mutation attempt");
  }

  async updateDispatch(
    nextView: SubmissionView,
    expectedAttempts: number,
    nextOutbox: DispatchOutbox | null,
  ): Promise<{ commit: string; view: SubmissionView }> {
    const decodedView = decodeSubmissionView(nextView);
    const decodedOutbox = nextOutbox === null ? null : decodeDispatchOutbox(nextOutbox);
    if (decodedOutbox !== null && decodedOutbox.submission_id !== decodedView.submission_id) {
      throw new TypeError("dispatch view and outbox identities disagree");
    }
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const current = await readSubmissionAt(this.#config, this.#fetcher, nextView.submission_id, snapshot.headSha);
      if (current === null) throw new GitHubStateError(404, "submission view does not exist");
      if (current.dispatch.status === "succeeded") return { commit: snapshot.headSha, view: current };
      if (current.dispatch.attempts !== expectedAttempts) throw new StateEventConflictError(submissionViewPath(nextView.submission_id));
      const outboxPath = dispatchOutboxPath(nextView.submission_id);
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        [],
        [
          { path: submissionViewPath(nextView.submission_id), value: decodedView },
          { path: outboxPath, value: decodedOutbox },
        ],
        `Record dispatch ${decodedView.dispatch.status} for ${nextView.submission_id}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, view: decodedView };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) throw new GitHubStateError(409, "State branch kept changing during dispatch update");
      await pause(attempt);
    }
    throw new Error("unreachable dispatch update attempt");
  }

  async listDispatchOutbox(shard: string): Promise<readonly DispatchOutbox[]> {
    if (!/^[0-9a-f]{2}$/.test(shard)) throw new TypeError("dispatch outbox shard must be two lowercase hexadecimal digits");
    const snapshot = await branchSnapshot(this.#config, this.#fetcher);
    const directory = `views/dispatch-outbox/${shard}`;
    const query = new URLSearchParams({ ref: snapshot.headSha });
    const listing = await jsonCall(this.#config, this.#fetcher, `/contents/${directory}?${query.toString()}`);
    if (listing === null) return [];
    if (!Array.isArray(listing) || listing.length > 1000) {
      throw new GitHubStateError(503, "dispatch outbox shard is invalid or exceeds its hard bound");
    }
    const paths = listing.map((raw) => {
      const entry = object(raw, "dispatch outbox directory entry");
      if (entry.type !== "file" || typeof entry.path !== "string" || !new RegExp(`^${directory}/[0-9a-f-]{36}\\.json$`).test(entry.path)) {
        throw new GitHubStateError(502, "dispatch outbox directory contains an unexpected entry");
      }
      return entry.path;
    });
    return Promise.all(paths.map(async (path) => {
      const entry = await readPathAt(this.#config, this.#fetcher, path, snapshot.headSha);
      if (!entry.found) throw new GitHubStateError(502, `${path} disappeared from the pinned State snapshot`);
      let outbox: DispatchOutbox;
      try {
        outbox = decodeDispatchOutbox(entry.value);
      } catch (error) {
        throw new GitHubStateError(502, `${path} is invalid: ${String(error)}`);
      }
      if (dispatchOutboxPath(outbox.submission_id) !== path) throw new GitHubStateError(502, `${path} has the wrong submission identity`);
      return outbox;
    }));
  }

  async appendSubmissionLifecycle(
    events: readonly WritableSubmissionLifecycleEvent[],
    expectedLifecycleEventId: string,
    nextView: SubmissionView,
  ): Promise<{ commit: string; created: boolean; view: SubmissionView }> {
    if (events.length === 0 || events.length > 4) {
      throw new TypeError("submission lifecycle batch must contain between one and four events");
    }
    for (const event of events) validateStateEvent(event);
    const decodedView = decodeSubmissionView(nextView);
    if (decodedView.schema_version !== 2) {
      throw new TypeError("submission lifecycle requires submission-view schema version 2");
    }
    if (events.some((event) => event.subject_id !== decodedView.submission_id)) {
      throw new TypeError("submission lifecycle event subjects disagree with the view");
    }
    for (let index = 0; index < events.length; index += 1) {
      const expectedCause = index === 0 ? expectedLifecycleEventId : events[index - 1]?.event_id;
      if (events[index]?.causation_event_id !== expectedCause) {
        throw new TypeError("submission lifecycle batch does not form one causal chain");
      }
    }
    const lastEventId = events.at(-1)?.event_id;
    if (!lastEventId || latestLifecycleEventId(decodedView) !== lastEventId) {
      throw new TypeError("submission lifecycle view does not name the batch terminal event");
    }
    const paths = events.map(stateEventPath);
    if (new Set(paths).size !== paths.length) throw new TypeError("submission lifecycle event paths must be unique");
    const viewPath = submissionViewPath(decodedView.submission_id);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const current = await readSubmissionAt(this.#config, this.#fetcher, decodedView.submission_id, snapshot.headSha);
      if (current === null) throw new GitHubStateError(404, "submission view does not exist");
      if (latestLifecycleEventId(current) === lastEventId) {
        const existing = await Promise.all(paths.map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)));
        if (
          existing.some((entry, index) => {
            if (!entry.found) return true;
            return canonicalJson(entry.value) !== canonicalJson(events[index]);
          }) ||
          canonicalJson(current) !== canonicalJson(decodedView)
        ) {
          throw new StateEventConflictError(viewPath);
        }
        return { commit: snapshot.headSha, created: false, view: current };
      }
      if (latestLifecycleEventId(current) !== expectedLifecycleEventId) {
        throw new StateEventConflictError(viewPath);
      }
      if (current.mutation_event_id !== decodedView.mutation_event_id) {
        throw new StateEventConflictError(viewPath);
      }
      const existing = await Promise.all(paths.map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)));
      if (existing.some((entry) => entry.found)) {
        throw new StateEventConflictError(paths[existing.findIndex((entry) => entry.found)] ?? viewPath);
      }
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        events,
        [{ path: viewPath, value: decodedView }],
        `Record lifecycle through ${lastEventId} for ${decodedView.submission_id}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, created: true, view: decodedView };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new GitHubStateError(409, "State branch kept changing during lifecycle update");
      }
      await pause(attempt);
    }
    throw new Error("unreachable submission lifecycle update");
  }

  async recordAcceptedResult(
    events: readonly WritableResultLifecycleEvent[],
    expectedLifecycleEventId: string,
    nextView: SubmissionView,
  ): Promise<{ commit: string; created: boolean; view: SubmissionView }> {
    if (events.length < 1 || events.length > 2) {
      throw new TypeError("result lifecycle batch must contain result recording and optional release schedule");
    }
    for (const event of events) validateStateEvent(event);
    const result = events[0];
    if (result?.event_type !== "result.recorded") {
      throw new TypeError("result lifecycle batch must begin with result.recorded");
    }
    const decodedView = decodeSubmissionView(nextView);
    if (
      decodedView.schema_version !== 2 ||
      decodedView.result_id !== result.subject_id ||
      decodedView.result_event_id !== result.event_id ||
      result.causation_event_id !== expectedLifecycleEventId ||
      result.payload.submission_id !== decodedView.submission_id ||
      result.payload.problem_id !== decodedView.submission.problem_id ||
      result.payload.statement_revision !== decodedView.submission.statement_revision
    ) {
      throw new TypeError("result lifecycle identities disagree with the submission view");
    }
    const release = events[1];
    const releaseRequired =
      decodedView.submission.problem_group !== "open-conjectures" &&
      decodedView.publication_choice === "scheduled";
    if (releaseRequired !== (release !== undefined)) {
      throw new TypeError("result lifecycle release scheduling disagrees with problem policy");
    }
    if (
      release !== undefined &&
      (release.event_type !== "release.scheduled" ||
        release.subject_id !== result.subject_id ||
        release.payload.result_id !== result.subject_id ||
        release.causation_event_id !== result.event_id ||
        Date.parse(release.occurred_at) <= Date.parse(result.occurred_at))
    ) {
      throw new TypeError("release schedule does not follow its recorded result");
    }
    const paths = events.map(stateEventPath);
    const viewPath = submissionViewPath(decodedView.submission_id);
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const current = await readSubmissionAt(
        this.#config,
        this.#fetcher,
        decodedView.submission_id,
        snapshot.headSha,
      );
      if (current === null) throw new GitHubStateError(404, "submission view does not exist");
      if (current.schema_version === 2 && current.result_event_id === result.event_id) {
        const existing = await Promise.all(
          paths.map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)),
        );
        if (
          existing.some((entry, index) =>
            !entry.found || canonicalJson(entry.value) !== canonicalJson(events[index])) ||
          canonicalJson(current) !== canonicalJson(decodedView)
        ) {
          throw new StateEventConflictError(viewPath);
        }
        return { commit: snapshot.headSha, created: false, view: current };
      }
      if (
        (current.schema_version === 2 && current.result_event_id !== null) ||
        latestLifecycleEventId(current) !== expectedLifecycleEventId ||
        current.mutation_event_id !== decodedView.mutation_event_id
      ) {
        throw new StateEventConflictError(viewPath);
      }
      const existing = await Promise.all(
        paths.map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)),
      );
      if (existing.some((entry) => entry.found)) {
        throw new StateEventConflictError(paths[existing.findIndex((entry) => entry.found)] ?? viewPath);
      }
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        events,
        [{ path: viewPath, value: decodedView }],
        `Record accepted result ${result.subject_id} for ${decodedView.submission_id}`,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, created: true, view: decodedView };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new GitHubStateError(409, "State branch kept changing during result recording");
      }
      await pause(attempt);
    }
    throw new Error("unreachable result recording attempt");
  }

  async appendEvents(events: readonly WritableStateEvent[]): Promise<{
    commit: string;
    paths: readonly string[];
    created: boolean;
  }> {
    if (events.length === 0 || events.length > 4) {
      throw new TypeError("State append batch must contain between one and four events");
    }
    for (const event of events) validateStateEvent(event);
    const paths = events.map(stateEventPath);
    if (new Set(paths).size !== paths.length) throw new TypeError("State append paths must be unique");
    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt += 1) {
      const snapshot = await branchSnapshot(this.#config, this.#fetcher);
      const existing = await Promise.all(
        paths.map((path) => readPathAt(this.#config, this.#fetcher, path, snapshot.headSha)),
      );
      const found = existing.filter((entry) => entry.found).length;
      if (found > 0) {
        if (found !== events.length) throw new StateEventConflictError(paths[existing.findIndex((entry) => entry.found)] ?? paths[0] ?? "events");
        for (let index = 0; index < events.length; index += 1) {
          const entry = existing[index];
          const event = events[index];
          if (!entry?.found || !event) throw new Error("unreachable State batch comparison");
          try {
            validateStateEvent(entry.value);
          } catch {
            throw new StateEventConflictError(paths[index] ?? "events");
          }
          if (canonicalJson(entry.value) !== canonicalJson(event)) {
            throw new StateEventConflictError(paths[index] ?? "events");
          }
        }
        return { commit: snapshot.headSha, paths, created: false };
      }
      const commit = await createCommit(
        this.#config,
        this.#fetcher,
        snapshot,
        events,
      );
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, paths, created: true };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new GitHubStateError(409, "State branch kept changing underneath the append");
      }
      await pause(attempt);
    }
    throw new Error("unreachable State append attempt");
  }

  async appendEvent(event: WritableStateEvent): Promise<{ commit: string; path: string; created: boolean }> {
    const outcome = await this.appendEvents([event]);
    const path = outcome.paths[0];
    if (!path) throw new Error("unreachable empty State append outcome");
    return { commit: outcome.commit, path, created: outcome.created };
  }

  async appendEventAtHead(
    event: WritableStateEvent,
    expectedHead: string,
  ): Promise<{ commit: string; path: string; created: boolean }> {
    validateStateEvent(event);
    if (!SHA.test(expectedHead)) throw new TypeError("expected State head must be a commit SHA");
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
        if (canonicalJson(existing.value) !== canonicalJson(event)) {
          throw new StateEventConflictError(path);
        }
        return { commit: snapshot.headSha, path, created: false };
      }
      if (snapshot.headSha !== expectedHead) {
        throw new GitHubStateError(409, "State moved before the bound event append");
      }
      const commit = await createCommit(this.#config, this.#fetcher, snapshot, [event]);
      if ((await updateReference(this.#config, this.#fetcher, commit)) === "applied") {
        return { commit, path, created: true };
      }
      if (attempt === MAX_WRITE_ATTEMPTS) {
        throw new GitHubStateError(409, "State branch kept changing underneath the bound append");
      }
      await pause(attempt);
    }
    throw new Error("unreachable bound State append attempt");
  }
}
