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
const DISPATCH_FAILURE_REASONS = new Set([
  "dispatch_credential_rejected",
  "dispatch_provider_unavailable",
  "dispatch_ref_conflict",
  "dispatch_workflow_not_found",
]);

export type DispatchStatus = "failed" | "pending" | "succeeded";

export type SubmissionView = Readonly<{
  schema_version: 1;
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
  archive: Readonly<{ status: "pending" }>;
  evaluation: Readonly<{ status: "pending" }>;
  result_id: null;
  dispatch: Readonly<{
    status: DispatchStatus;
    attempts: number;
    requested_at: string;
    updated_at: string;
    workflow_ref: string;
    last_error_code: string | null;
  }>;
}>;

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
  exact(view, [
    "accepted_at", "archive", "dispatch", "evaluation", "metadata_event_id", "mutation_event_id",
    "owner_login", "production_metadata", "publication_choice", "publication_event_id", "received_event_id",
    "result_id", "schema_version", "submission", "submission_id",
  ], "submission view");
  if (view.schema_version !== 1 || typeof view.submission_id !== "string" || !isUuidV7(view.submission_id)) {
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
  const archive = object(view.archive, "submission view archive");
  exact(archive, ["status"], "submission view archive");
  if (archive.status !== "pending") throw new TypeError("unsupported submission view archive status");
  const evaluation = object(view.evaluation, "submission view evaluation");
  exact(evaluation, ["status"], "submission view evaluation");
  if (evaluation.status !== "pending") throw new TypeError("unsupported submission view evaluation status");
  if (view.result_id !== null) throw new TypeError("unsupported submission view result identity");
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
    archive: { status: "pending" },
    evaluation: { status: "pending" },
    dispatch: dispatch as SubmissionView["dispatch"],
  } as SubmissionView;
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
