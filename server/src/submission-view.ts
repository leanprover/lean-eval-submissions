import {
  decodeProductionMetadata,
  decodeSubmissionInput,
  isUuidV7,
  type ProductionMetadata,
  type PublicationChoice,
  type SubmissionInput,
} from "./api-contract";

const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const EVENT_REF = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const WORKFLOW_REF = /^lean-eval-dispatch\/[0-9a-f]{40}$/;
const REASON = /^[a-z][a-z0-9_]{1,63}$/;
const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const COMMIT = /^[0-9a-f]{40}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const RESULT_ID = /^r2_[0-9a-f]{64}$/;
const TOOLCHAIN = /^leanprover\/lean4:v[0-9]+\.[0-9]+\.[0-9]+$/;
const DISPATCH_FAILURE_REASONS = new Set([
  "dispatch_credential_rejected",
  "dispatch_provider_unavailable",
  "dispatch_ref_conflict",
  "dispatch_workflow_not_found",
]);

export type DispatchStatus = "failed" | "pending" | "succeeded";

type PendingSummary = Readonly<{ status: "pending" }>;
export type ArchiveSummary =
  | PendingSummary
  | Readonly<{
      status: "completed";
      event_id: string;
      occurred_at: string;
      archive_repository: string;
      archive_commit: string;
      archive_path: string;
      archive_ciphertext_sha256: string;
      encrypted: true;
    }>
  | Readonly<{
      status: "failed";
      event_id: string;
      occurred_at: string;
      reason_code: string;
      retryable: boolean;
    }>;
type EvaluationBase = Readonly<{
  event_id: string;
  occurred_at: string;
  attempt: number;
  benchmark_repository: string;
  benchmark_commit: string;
  toolchain: string;
}>;
export type EvaluationSummary =
  | PendingSummary
  | (EvaluationBase & Readonly<{ status: "running" }>)
  | (EvaluationBase & Readonly<{ status: "accepted"; evaluator_version: string }>)
  | (EvaluationBase & Readonly<{ status: "rejected"; reason_code: string }>)
  | (EvaluationBase & Readonly<{ status: "failed"; reason_code: string; retryable: boolean }>);

type SubmissionViewCommon = Readonly<{
  submission_id: string;
  owner_login: string;
  received_event_id: string;
  mutation_event_id: string;
  metadata_event_id: string;
  publication_event_id: string | null;
  accepted_at: string;
  submission: SubmissionInput;
  production_metadata: ProductionMetadata;
  publication_choice: PublicationChoice;
  dispatch: Readonly<{
    status: DispatchStatus;
    attempts: number;
    requested_at: string;
    updated_at: string;
    workflow_ref: string;
    last_error_code: string | null;
  }>;
}>;
export type SubmissionView =
  | (SubmissionViewCommon & Readonly<{
      schema_version: 1;
      archive: PendingSummary;
      evaluation: PendingSummary;
      result_id: null;
    }>)
  | (SubmissionViewCommon & Readonly<{
      schema_version: 2;
      archive: ArchiveSummary;
      evaluation: EvaluationSummary;
      result_id: string | null;
      result_event_id: string | null;
    }>);

export type DispatchOutbox = Readonly<{
  schema_version: 1;
  submission_id: string;
  owner_login: string;
  submission: SubmissionInput;
  attempts: number;
  next_attempt_at: string;
  workflow_ref: string;
}>;

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exact(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new TypeError(`${label} fields do not match schema version 1`);
  }
}

function timestamp(value: unknown, label: string): asserts value is string {
  if (typeof value !== "string" || value.startsWith("0000-")) throw new TypeError(`${label} is invalid`);
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf()) || parsed.toISOString() !== value) throw new TypeError(`${label} is invalid`);
}

function safeAttempts(value: unknown): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > 32) {
    throw new TypeError("dispatch attempts must be an integer from zero through 32");
  }
}

function positive(value: unknown, label: string): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new TypeError(`${label} must be a positive safe integer`);
  }
}

function eventMarker(value: Record<string, unknown>, label: string): void {
  if (typeof value.event_id !== "string" || !EVENT_REF.test(value.event_id)) {
    throw new TypeError(`${label} event_id is invalid`);
  }
  timestamp(value.occurred_at, `${label} occurred_at`);
}

function decodeArchiveSummary(value: unknown, version: 1 | 2, submissionId: string): ArchiveSummary {
  const archive = object(value, "submission view archive");
  if (archive.status === "pending") {
    exact(archive, ["status"], "submission view archive");
    return { status: "pending" };
  }
  if (version === 1) {
    throw new TypeError("submission view archive schema version 1 supports only pending");
  }
  if (archive.status === "completed") {
    exact(archive, [
      "archive_ciphertext_sha256", "archive_commit", "archive_path", "archive_repository",
      "encrypted", "event_id", "occurred_at", "status",
    ], "submission view archive");
    eventMarker(archive, "submission view archive");
    if (
      typeof archive.archive_repository !== "string" || !REPOSITORY.test(archive.archive_repository) ||
      typeof archive.archive_commit !== "string" || !COMMIT.test(archive.archive_commit) ||
      typeof archive.archive_ciphertext_sha256 !== "string" || !DIGEST.test(archive.archive_ciphertext_sha256) ||
      archive.archive_path !== `archives/${submissionId.replaceAll("-", "").slice(0, 2)}/${submissionId}.tar.age` ||
      archive.encrypted !== true
    ) {
      throw new TypeError("submission view archive completion is invalid");
    }
    return archive as ArchiveSummary;
  }
  if (archive.status === "failed") {
    exact(archive, ["event_id", "occurred_at", "reason_code", "retryable", "status"], "submission view archive");
    eventMarker(archive, "submission view archive");
    if (typeof archive.reason_code !== "string" || !REASON.test(archive.reason_code) || typeof archive.retryable !== "boolean") {
      throw new TypeError("submission view archive failure is invalid");
    }
    return archive as ArchiveSummary;
  }
  throw new TypeError("submission view archive status is invalid");
}

function decodeEvaluationSummary(value: unknown, version: 1 | 2): EvaluationSummary {
  const evaluation = object(value, "submission view evaluation");
  if (evaluation.status === "pending") {
    exact(evaluation, ["status"], "submission view evaluation");
    return { status: "pending" };
  }
  if (version === 1) {
    throw new TypeError("submission view evaluation schema version 1 supports only pending");
  }
  const terminal = evaluation.status;
  const fields = [
    "attempt", "benchmark_commit", "benchmark_repository", "event_id", "occurred_at", "status", "toolchain",
  ];
  if (terminal === "accepted") fields.push("evaluator_version");
  else if (terminal === "rejected") fields.push("reason_code");
  else if (terminal === "failed") fields.push("reason_code", "retryable");
  else if (terminal !== "running") throw new TypeError("submission view evaluation status is invalid");
  exact(evaluation, fields, "submission view evaluation");
  eventMarker(evaluation, "submission view evaluation");
  positive(evaluation.attempt, "submission view evaluation attempt");
  if (
    typeof evaluation.benchmark_repository !== "string" || !REPOSITORY.test(evaluation.benchmark_repository) ||
    typeof evaluation.benchmark_commit !== "string" || !COMMIT.test(evaluation.benchmark_commit) ||
    typeof evaluation.toolchain !== "string" || !TOOLCHAIN.test(evaluation.toolchain)
  ) {
    throw new TypeError("submission view evaluation pins are invalid");
  }
  if (terminal === "accepted" && (typeof evaluation.evaluator_version !== "string" || evaluation.evaluator_version.length === 0)) {
    throw new TypeError("submission view evaluator version is invalid");
  }
  if ((terminal === "rejected" || terminal === "failed") && (typeof evaluation.reason_code !== "string" || !REASON.test(evaluation.reason_code))) {
    throw new TypeError("submission view evaluation reason is invalid");
  }
  if (terminal === "failed" && typeof evaluation.retryable !== "boolean") {
    throw new TypeError("submission view evaluation retryability is invalid");
  }
  return evaluation as EvaluationSummary;
}

export function submissionViewPath(submissionId: string): string {
  if (!isUuidV7(submissionId)) throw new TypeError("submission view id must be a canonical lowercase UUIDv7");
  return `views/submissions/${submissionId.replaceAll("-", "").slice(0, 2)}/${submissionId}.json`;
}

export function dispatchOutboxPath(submissionId: string): string {
  if (!isUuidV7(submissionId)) throw new TypeError("dispatch outbox id must be a canonical lowercase UUIDv7");
  return `views/dispatch-outbox/${submissionId.replaceAll("-", "").slice(-2)}/${submissionId}.json`;
}

export function decodeSubmissionView(value: unknown): SubmissionView {
  const view = object(value, "submission view");
  const expectedFields = [
    "accepted_at", "archive", "dispatch", "evaluation", "metadata_event_id", "mutation_event_id",
    "owner_login", "production_metadata", "publication_choice", "publication_event_id", "received_event_id",
    "result_id", "schema_version", "submission", "submission_id",
  ];
  if (view.schema_version === 2) expectedFields.push("result_event_id");
  exact(view, expectedFields, "submission view");
  if ((view.schema_version !== 1 && view.schema_version !== 2) || typeof view.submission_id !== "string" || !isUuidV7(view.submission_id)) {
    throw new TypeError("submission view identity is invalid");
  }
  if (typeof view.owner_login !== "string" || !LOGIN.test(view.owner_login)) throw new TypeError("submission view owner is invalid");
  for (const field of ["received_event_id", "mutation_event_id", "metadata_event_id"] as const) {
    if (typeof view[field] !== "string" || !EVENT_REF.test(view[field])) throw new TypeError(`submission view ${field} is invalid`);
  }
  if (view.publication_event_id !== null && (typeof view.publication_event_id !== "string" || !EVENT_REF.test(view.publication_event_id))) {
    throw new TypeError("submission view publication_event_id is invalid");
  }
  timestamp(view.accepted_at, "submission view accepted_at");
  const submission = decodeSubmissionInput(view.submission);
  const metadata = decodeProductionMetadata(view.production_metadata);
  if (view.publication_choice !== "scheduled" && view.publication_choice !== "withheld") {
    throw new TypeError("submission view publication choice is invalid");
  }
  const archive = decodeArchiveSummary(view.archive, view.schema_version, view.submission_id);
  const evaluation = decodeEvaluationSummary(view.evaluation, view.schema_version);
  if (view.schema_version === 1 && view.result_id !== null) {
    throw new TypeError("submission view schema version 1 result identity must be null");
  }
  if (view.schema_version === 2 && view.result_id !== null && (typeof view.result_id !== "string" || !RESULT_ID.test(view.result_id))) {
    throw new TypeError("submission view result identity is invalid");
  }
  if (
    view.schema_version === 2 &&
    view.result_event_id !== null &&
    (typeof view.result_event_id !== "string" || !EVENT_REF.test(view.result_event_id))
  ) {
    throw new TypeError("submission view result event identity is invalid");
  }
  if (view.schema_version === 2 && (view.result_id === null) !== (view.result_event_id === null)) {
    throw new TypeError("submission view result and event identities disagree");
  }
  const dispatch = object(view.dispatch, "submission view dispatch");
  exact(dispatch, ["attempts", "last_error_code", "requested_at", "status", "updated_at", "workflow_ref"], "submission view dispatch");
  if (!new Set(["failed", "pending", "succeeded"]).has(String(dispatch.status))) throw new TypeError("submission view dispatch status is invalid");
  safeAttempts(dispatch.attempts);
  timestamp(dispatch.requested_at, "submission view dispatch requested_at");
  timestamp(dispatch.updated_at, "submission view dispatch updated_at");
  if (typeof dispatch.workflow_ref !== "string" || !WORKFLOW_REF.test(dispatch.workflow_ref)) throw new TypeError("submission view workflow_ref is invalid");
  if (
    dispatch.last_error_code !== null &&
    (typeof dispatch.last_error_code !== "string" ||
      !REASON.test(dispatch.last_error_code) ||
      !DISPATCH_FAILURE_REASONS.has(dispatch.last_error_code))
  ) {
    throw new TypeError("submission view dispatch error is invalid");
  }
  if ((dispatch.status === "succeeded") !== (dispatch.last_error_code === null)) {
    if (dispatch.status !== "pending" || dispatch.last_error_code !== null) {
      throw new TypeError("submission view dispatch status and error disagree");
    }
  }
  if (submission.production_metadata !== metadata) {
    // Both values are decoded independently; compare their JSON representations below.
    if (JSON.stringify(submission.production_metadata) !== JSON.stringify(metadata)) {
      throw new TypeError("submission view production metadata does not match submission input");
    }
  }
  if (submission.publication_choice !== view.publication_choice) {
    throw new TypeError("submission view publication choice does not match submission input");
  }
  return {
    ...view,
    submission,
    production_metadata: metadata,
    archive,
    evaluation,
    dispatch: dispatch as SubmissionView["dispatch"],
  } as SubmissionView;
}

export function latestLifecycleEventId(view: SubmissionView): string {
  if (view.schema_version === 2 && view.result_event_id !== null) return view.result_event_id;
  if (view.evaluation.status !== "pending") return view.evaluation.event_id;
  if (view.archive.status !== "pending") return view.archive.event_id;
  return view.received_event_id;
}

export function decodeDispatchOutbox(value: unknown): DispatchOutbox {
  const outbox = object(value, "dispatch outbox");
  exact(outbox, ["attempts", "next_attempt_at", "owner_login", "schema_version", "submission", "submission_id", "workflow_ref"], "dispatch outbox");
  if (outbox.schema_version !== 1 || typeof outbox.submission_id !== "string" || !isUuidV7(outbox.submission_id)) {
    throw new TypeError("dispatch outbox identity is invalid");
  }
  if (typeof outbox.owner_login !== "string" || !LOGIN.test(outbox.owner_login)) throw new TypeError("dispatch outbox owner is invalid");
  safeAttempts(outbox.attempts);
  timestamp(outbox.next_attempt_at, "dispatch outbox next_attempt_at");
  if (typeof outbox.workflow_ref !== "string" || !WORKFLOW_REF.test(outbox.workflow_ref)) throw new TypeError("dispatch outbox workflow_ref is invalid");
  return { ...outbox, submission: decodeSubmissionInput(outbox.submission) } as DispatchOutbox;
}
