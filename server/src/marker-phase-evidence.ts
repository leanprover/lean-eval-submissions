import type { ReplayAcceptanceRequest } from "./replay-contract";
import { replaySandboxId } from "./replay-sandbox";

export const MARKER_WORKFLOW = ".github/workflows/public-marker-phase-staging.yml";
export const MARKER_PHASE_KEY = "finite-marker-phase:v1";
export const MARKER_SERVICE = "lean-eval-public-guard-a-match";
export const MARKER_ROUTE = "/api/v1/staging-acceptance";
export const MARKER_PHASE_ROUTES = new Set([
  `${MARKER_ROUTE}/phase-proof`, `${MARKER_ROUTE}/phase-status`, `${MARKER_ROUTE}/phase-fence`,
]);
const DIGEST = /^[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const VERSION = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const APPLICATION_OPERATIONS = [
  "sandbox_handle", "write_ciphertext", "write_identity", "write_expectation",
  "exec_acceptance", "validate_evidence",
] as const;
export type MarkerOperation = typeof APPLICATION_OPERATIONS[number] | "destroy";
const CODES = new Set([
  "CONTAINER_UNAVAILABLE", "OPERATION_INTERRUPTED", "RPC_TRANSPORT_ERROR",
  "INTERNAL_ERROR", "PROCESS_READY_TIMEOUT", "SERVICE_NOT_RESPONDING", "unclassified",
]);
export type MarkerPhaseEnvironment = {
  MARKER_PHASE_SCOPE?: string;
  CF_VERSION_METADATA?: { id: string };
  DEPLOYED_COMMIT: string;
  DEPLOYMENT_ENVIRONMENT: string;
  REPLAY_ENABLED: string;
  HISTORICAL_PUBLIC_REPLAY_ENABLED: string;
  STAGING_ACCEPTANCE_ENABLED: string;
};
export type MarkerScope = {
  schema_version: 1;
  case: "a-match";
  service: typeof MARKER_SERVICE;
  workflow: typeof MARKER_WORKFLOW;
  commit: string;
  run_id: string;
  run_attempt: "1";
  start_before: number;
  retain_until: number;
  nonce_sha256: string;
  sandbox_name_sha256: string;
  execution_profile_digest: string;
  measurement_config_digest: string;
  image_digest: string;
};
export type MarkerBinding = {
  schema_version: 1;
  scope_sha256: string;
  worker_version: string;
  marker_instance_id: string;
  request_commitment: string;
};
export type MarkerEvent = {
  operation: MarkerOperation;
  state: "started" | "succeeded" | "failed";
  elapsed_ms?: number;
  code?: string;
};
export type MarkerOutcome = "not_attempted" | "pending" | "succeeded" | "failed";
export type MarkerJournal = {
  schema_version: 1;
  binding: MarkerBinding;
  start_before: number;
  retain_until: number;
  state: "prepared" | "active" | "terminal" | "fenced" | "expired";
  claimed: boolean;
  events: MarkerEvent[];
  operation_outcome: MarkerOutcome;
  destruction_outcome: MarkerOutcome;
};
export type MarkerPhaseStore = {
  prepareMarkerPhase(binding: MarkerBinding): Promise<MarkerJournal>;
  readMarkerPhase(binding: MarkerBinding): Promise<MarkerJournal>;
  claimMarkerPhase(binding: MarkerBinding): Promise<MarkerJournal>;
  appendMarkerPhase(binding: MarkerBinding, event: MarkerEvent): Promise<MarkerJournal>;
  finishMarkerPhase(binding: MarkerBinding): Promise<MarkerJournal>;
  fenceMarkerPhase(binding: MarkerBinding): Promise<MarkerJournal>;
};

export class MarkerPhaseError extends Error {
  constructor() { super("marker_phase_refused"); }
}
function requireValue(condition: boolean): asserts condition {
  if (!condition) throw new MarkerPhaseError();
}
function object(value: unknown): Record<string, unknown> {
  requireValue(typeof value === "object" && value !== null && !Array.isArray(value));
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, fields: string[]): void {
  requireValue(Object.keys(value).sort().join("\0") === fields.sort().join("\0"));
}
export function markerCanonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(markerCanonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const source = value as Record<string, unknown>;
    return `{${Object.keys(source).sort().map((key) => `${JSON.stringify(key)}:${markerCanonical(source[key])}`).join(",")}}`;
  }
  requireValue(value === null || typeof value === "string" || typeof value === "boolean"
    || typeof value === "number" && Number.isFinite(value));
  return JSON.stringify(value);
}
export async function markerHash(domain: string, value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${domain}\0${value}`));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
export function readMarkerScope(env: MarkerPhaseEnvironment, now = Date.now()): MarkerScope {
  requireValue(env.DEPLOYMENT_ENVIRONMENT === "staging" && env.REPLAY_ENABLED === "false"
    && env.HISTORICAL_PUBLIC_REPLAY_ENABLED === "false" && env.STAGING_ACCEPTANCE_ENABLED === "true");
  const raw = env.MARKER_PHASE_SCOPE;
  requireValue(typeof raw === "string" && raw.length <= 2048);
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { throw new MarkerPhaseError(); }
  const scope = object(parsed);
  exact(scope, ["schema_version", "case", "service", "workflow", "commit", "run_id", "run_attempt",
    "start_before", "retain_until", "nonce_sha256", "sandbox_name_sha256", "execution_profile_digest",
    "measurement_config_digest", "image_digest"]);
  requireValue(raw === markerCanonical(scope) && scope.schema_version === 1 && scope.case === "a-match"
    && scope.service === MARKER_SERVICE && scope.workflow === MARKER_WORKFLOW
    && typeof scope.commit === "string" && COMMIT.test(scope.commit) && scope.commit === env.DEPLOYED_COMMIT
    && typeof scope.run_id === "string" && /^[1-9][0-9]{0,19}$/.test(scope.run_id) && scope.run_attempt === "1"
    && typeof scope.start_before === "number" && Number.isSafeInteger(scope.start_before)
    && typeof scope.retain_until === "number" && Number.isSafeInteger(scope.retain_until)
    && scope.start_before <= now + 15 * 60_000 && scope.retain_until === scope.start_before + 15 * 60_000
    && now < scope.retain_until);
  for (const key of ["nonce_sha256", "sandbox_name_sha256", "execution_profile_digest", "measurement_config_digest"]) {
    requireValue(typeof scope[key] === "string" && DIGEST.test(scope[key]));
  }
  requireValue(typeof scope.image_digest === "string" && /^sha256:[0-9a-f]{64}$/.test(scope.image_digest));
  return scope as MarkerScope;
}
export async function markerBinding(env: MarkerPhaseEnvironment, requestCommitment: unknown): Promise<MarkerBinding> {
  const scope = readMarkerScope(env);
  requireValue(typeof requestCommitment === "string" && DIGEST.test(requestCommitment));
  const version = env.CF_VERSION_METADATA?.id;
  requireValue(typeof version === "string" && VERSION.test(version));
  const scopeHash = await markerHash("lean-eval-marker-scope-v1", markerCanonical(scope));
  return {
    schema_version: 1, scope_sha256: scopeHash, worker_version: version,
    marker_instance_id: await markerHash("lean-eval-marker-instance-v1",
      markerCanonical({ scope_sha256: scopeHash, worker_version: version,
        nonce_sha256: scope.nonce_sha256, sandbox_name_sha256: scope.sandbox_name_sha256 })),
    request_commitment: requestCommitment,
  };
}
export async function markerRequestBinding(env: MarkerPhaseEnvironment, input: ReplayAcceptanceRequest): Promise<MarkerBinding> {
  const scope = readMarkerScope(env);
  requireValue(Date.now() < scope.start_before);
  requireValue(await markerHash("lean-eval-marker-nonce-v1", input.runner_nonce) === scope.nonce_sha256
    && await markerHash("lean-eval-marker-sandbox-v1", replaySandboxId(input.runner_nonce)) === scope.sandbox_name_sha256);
  const provisional = await markerBinding(env, "0".repeat(64));
  const commitment = await markerHash("lean-eval-marker-request-v1", markerCanonical({
    marker_instance_id: provisional.marker_instance_id, request_id: input.request_id,
    archive_ciphertext_sha256: input.archive_ciphertext_sha256, marker_sha256: input.marker_sha256,
  }));
  return { ...provisional, request_commitment: commitment };
}
export async function readMarkerPhaseRequest(request: Request): Promise<string> {
  const body = request.body as ReadableStream<Uint8Array> | null;
  const reader = body?.getReader();
  requireValue(reader !== undefined);
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    let next = await reader.read();
    while (!next.done) {
      size += next.value.byteLength;
      requireValue(size <= 1024);
      chunks.push(next.value);
      next = await reader.read();
    }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const raw = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes);
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { throw new MarkerPhaseError(); }
  const value = object(parsed);
  exact(value, ["schema_version", "request_commitment"]);
  requireValue(markerCanonical(value) === raw && value.schema_version === 1
    && typeof value.request_commitment === "string" && DIGEST.test(value.request_commitment));
  return value.request_commitment;
}
export async function markerJournalName(env: MarkerPhaseEnvironment): Promise<string> {
  const scope = readMarkerScope(env);
  return `marker-phase-${await markerHash("lean-eval-marker-journal-v1", markerCanonical({
    scope_sha256: await markerHash("lean-eval-marker-scope-v1", markerCanonical(scope)),
    sandbox_name_sha256: scope.sandbox_name_sha256,
  }))}`;
}
export async function validateMarkerBinding(env: MarkerPhaseEnvironment, value: MarkerBinding): Promise<void> {
  requireValue(markerCanonical(value) === markerCanonical(await markerBinding(env, value.request_commitment)));
}
export function newMarkerJournal(binding: MarkerBinding, scope: MarkerScope): MarkerJournal {
  requireValue(Date.now() < scope.start_before);
  return { schema_version: 1, binding, start_before: scope.start_before, retain_until: scope.retain_until,
    state: "prepared", claimed: false, events: [], operation_outcome: "not_attempted", destruction_outcome: "not_attempted" };
}
export function requireMarkerJournal(value: MarkerJournal | undefined, binding: MarkerBinding): MarkerJournal {
  requireValue(value !== undefined && markerCanonical(value.binding) === markerCanonical(binding)
    && Date.now() < value.retain_until && value.state !== "expired");
  return structuredClone(value);
}
export function claimMarkerJournal(journal: MarkerJournal): MarkerJournal {
  requireValue(journal.state === "prepared" && !journal.claimed && Date.now() < journal.start_before);
  return { ...journal, state: "active", claimed: true, operation_outcome: "pending" };
}
export function appendMarkerJournal(journal: MarkerJournal, event: MarkerEvent): MarkerJournal {
  requireValue(journal.state === "active" && journal.claimed);
  const raw = object(event);
  requireValue(Object.keys(raw).every((key) => ["operation", "state", "elapsed_ms", "code"].includes(key))
    && ["started", "succeeded", "failed"].includes(event.state)
    && ([...APPLICATION_OPERATIONS, "destroy"] as string[]).includes(event.operation));
  if (event.state === "started") requireValue(event.elapsed_ms === undefined && event.code === undefined);
  else requireValue(typeof event.elapsed_ms === "number" && Number.isSafeInteger(event.elapsed_ms)
    && event.elapsed_ms >= 0 && event.elapsed_ms <= 600_000
    && (event.code === undefined || event.state === "failed" && CODES.has(event.code)));
  const sameOperation = journal.events.filter((item) => item.operation === event.operation);
  const identical = sameOperation.find((item) => item.state === event.state);
  if (identical !== undefined) {
    requireValue(markerCanonical(identical) === markerCanonical(event));
    return journal;
  }
  if (event.state === "started") {
    requireValue(sameOperation.length === 0);
    if (event.operation === "destroy") {
      requireValue(journal.events.some((item) => item.operation === "sandbox_handle"));
    }
    if (event.operation !== "destroy") {
      const position = APPLICATION_OPERATIONS.indexOf(event.operation);
      requireValue(journal.operation_outcome === "pending" && journal.destruction_outcome === "not_attempted"
        && journal.events.filter((item) => item.operation !== "destroy").length === position * 2
        && journal.events.every((item) => item.state !== "failed"));
    }
  } else requireValue(sameOperation.length === 1 && sameOperation[0]?.state === "started");
  journal.events.push({ ...event });
  requireValue(journal.events.length <= 20 && new TextEncoder().encode(markerCanonical(journal)).length <= 8192);
  if (event.operation === "destroy") journal.destruction_outcome = event.state === "started" ? "pending" : event.state;
  else if (event.state === "failed") journal.operation_outcome = "failed";
  else if (event.operation === "validate_evidence" && event.state === "succeeded") journal.operation_outcome = "succeeded";
  return journal;
}
export function finishMarkerJournal(journal: MarkerJournal): MarkerJournal {
  requireValue(journal.state === "active" && journal.operation_outcome !== "pending"
    && journal.destruction_outcome !== "pending");
  return { ...journal, state: "terminal" };
}
export function markerErrorCode(error: unknown): string {
  try {
    if (typeof error === "object" && error !== null && "code" in error
      && typeof error.code === "string" && CODES.has(error.code)) return error.code;
  } catch { /* Hostile getters never become diagnostics. */ }
  return "unclassified";
}
export class MarkerRecorder {
  private overhead = 0;
  constructor(private readonly store: MarkerPhaseStore, private readonly binding: MarkerBinding,
    private readonly clock: () => number = () => performance.now()) {}
  async call<T>(action: () => Promise<T>): Promise<T> {
    const began = this.clock();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      requireValue(this.overhead < 10_000);
      return await Promise.race([action(), new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new MarkerPhaseError()), Math.min(2000, 10_000 - this.overhead));
      })]);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      this.overhead += this.duration(began);
    }
  }
  private duration(start: number): number {
    const value = this.clock() - start;
    requireValue(Number.isFinite(value) && value >= 0 && value <= 600_000);
    return Math.floor(value);
  }
  async operation<T>(operation: MarkerOperation, action: () => Promise<T>, cleanup = false): Promise<T> {
    const started: MarkerEvent = { operation, state: "started" };
    if (cleanup) {
      try { await this.call(() => this.store.appendMarkerPhase(this.binding, started)); } catch { /* Destruction must still run. */ }
    } else await this.call(() => this.store.appendMarkerPhase(this.binding, started));
    const began = this.clock();
    let value: T;
    try { value = await action(); } catch (error) {
      try {
        await this.call(() => this.store.appendMarkerPhase(this.binding, {
          operation, state: "failed", elapsed_ms: this.duration(began), code: markerErrorCode(error),
        }));
      } catch { /* Preserve the runtime failure and always run destruction. */ }
      throw error;
    }
    await this.call(() => this.store.appendMarkerPhase(this.binding, {
      operation, state: "succeeded", elapsed_ms: this.duration(began),
    }));
    return value;
  }
}
