import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  canonicalStateDocument,
  clearResultOwnerContractProofCacheForTest,
  DISPATCH_OUTBOX_SCAN_LIMIT,
  DISPATCH_UPDATE_MAX_SUBREQUESTS,
  MODEL_IDENTITY_WRITE_MAX_SUBREQUESTS,
  type GitHubFetch,
  GitHubStateError,
  GitHubStateRepository,
  ResultIdentityCollisionError,
  StateEventConflictError,
  StateUpdateOutcomeUnknownError,
} from "../src/github-state";
import { modelAliasKey, modelIdentityId, type ModelIdentityView } from "../src/model-identity";
import type {
  StateEvent,
  WritableResultLifecycleEvent,
  WritableSubmissionLifecycleEvent,
  WritableStateEvent,
} from "../src/state-event";
import { validateStateEvent } from "../src/state-event";
import type { SubmissionView } from "../src/submission-view";
import {
  backfilledOverlay,
  claimedGuard,
  claimedOverlay,
  claimedSourceIndex,
  effectiveResultIdentityPath,
  effectiveResultIdentityReservation,
  initialResultReleaseStatusView,
  recordedGuard,
  type VerifiedLegacyResult,
} from "../src/result-owner";
import {
  challengeId,
  comparatorBindingSha256,
  decidedProblemRepairView,
  decidedRetractionView,
  decodeResultAmendmentView,
  initialResultAmendmentView,
  requestedProblemRepairView,
  requestedRetractionView,
  terminalRetractionView,
  type ComparatorEvidence,
} from "../src/result-amendment";
import {
  ScheduledSubrequestBudget,
  ScheduledSubrequestBudgetError,
} from "../src/scheduled-subrequest-budget";

const HEAD = "1".repeat(40);
const TREE = "2".repeat(40);
const NEW_TREE = "3".repeat(40);
const NEW_COMMIT = "4".repeat(40);
const RESULT_OWNER_CONTRACT_COMMIT = "b0a30e3a64aa5c05660040405b32135dea4b7f1d";
const RESULT_OWNER_CONTRACT_ROOT_ENTRIES = {
  "README.md": { mode: "100644", type: "blob", sha: "03049be51782ccf57c00ecd22a42b9c45458e3f1" },
  docs: { mode: "040000", type: "tree", sha: "ade8caefc912fc9f5440d7bd4957b390c2610eec" },
  schema: { mode: "040000", type: "tree", sha: "3226e2c20945a826fb5fe871abb888cde38d92af" },
  scripts: { mode: "040000", type: "tree", sha: "26e14a670fe61ccbdac3acc04b520573a7204c29" },
} as const;
const STAGING_RESULT_OWNER_CONTRACT_COMMIT = "f00055ed2ba9b4252f04e096d27aadd5beef0ed4";
const STAGING_RESULT_OWNER_CONTRACT_ROOT_ENTRIES = {
  "README.md": { mode: "100644", type: "blob", sha: "e546715f76d4c0977d6b940681b71f4324aff9b9" },
  docs: { mode: "040000", type: "tree", sha: "39e94ccf460181e98692da31a59ffb31a2a63b54" },
  schema: { mode: "040000", type: "tree", sha: "13d5853b5c1bf7227e90d697c86d42c7ce232102" },
  scripts: { mode: "040000", type: "tree", sha: "de1017d55eaf9740358ae38908c77c168a25e8de" },
} as const;
const MODEL_IDENTITY_CONTRACT_COMMIT = "b0a30e3a64aa5c05660040405b32135dea4b7f1d";
const MODEL_IDENTITY_CONTRACT_ROOT_ENTRIES = {
  "README.md": { mode: "100644", type: "blob", sha: "03049be51782ccf57c00ecd22a42b9c45458e3f1" },
  docs: { mode: "040000", type: "tree", sha: "ade8caefc912fc9f5440d7bd4957b390c2610eec" },
  schema: { mode: "040000", type: "tree", sha: "3226e2c20945a826fb5fe871abb888cde38d92af" },
  scripts: { mode: "040000", type: "tree", sha: "26e14a670fe61ccbdac3acc04b520573a7204c29" },
} as const;
const STAGING_MODEL_IDENTITY_CONTRACT_COMMIT = "f00055ed2ba9b4252f04e096d27aadd5beef0ed4";
const STAGING_MODEL_IDENTITY_CONTRACT_ROOT_ENTRIES = {
  "README.md": { mode: "100644", type: "blob", sha: "e546715f76d4c0977d6b940681b71f4324aff9b9" },
  docs: { mode: "040000", type: "tree", sha: "39e94ccf460181e98692da31a59ffb31a2a63b54" },
  schema: { mode: "040000", type: "tree", sha: "13d5853b5c1bf7227e90d697c86d42c7ce232102" },
  scripts: { mode: "040000", type: "tree", sha: "de1017d55eaf9740358ae38908c77c168a25e8de" },
} as const;

const EVENT: StateEvent = {
  schema_version: 1,
  event_id: "0198abcd-0000-7000-8000-000000000001",
  event_type: "system.initialized",
  occurred_at: "2026-08-20T06:07:08.000Z",
  subject_id: "state_staging",
  causation_event_id: null,
  actor: { kind: "system" },
  payload: { environment: "staging" },
};
const CANARY_EVIDENCE: StateEvent = {
  schema_version: 1,
  event_id: "0198abcd-0000-7000-8000-000000000002",
  event_type: "authentication.nonce_consumed",
  occurred_at: "2026-08-20T06:07:08.001Z",
  subject_id: "0198abcd-0000-7000-8000-000000000002",
  causation_event_id: null,
  actor: { kind: "system" },
  payload: {
    nonce_digest: "a".repeat(64),
    purpose: "submission",
    expires_at: "2026-08-20T06:17:08.000Z",
  },
};

const MODEL_REQUEST_ID = "0198abcd-0000-7000-8000-000000000020";
const MODEL_DECISION_ID = "0198abcd-0001-7000-8000-000000000021";
const MODEL_ALIAS_ID = "0198abcd-0002-7000-8000-000000000022";
const MODEL_RENAME_ID = "0198abcd-0003-7000-8000-000000000023";
const MODEL_ID = "mi1_5a3dd8d6aa12ca21b76357b40cb5b9414b7097acf2a7817318e6277a40deba33";
const MODEL_REQUEST: StateEvent = {
  schema_version: 1,
  event_id: MODEL_REQUEST_ID,
  event_type: "model_identity.requested",
  occurred_at: "2025-08-15T03:36:35.584Z",
  subject_id: MODEL_ID,
  causation_event_id: null,
  actor: { kind: "github", login: "alice" },
  payload: { display_name: "Model Alpha" },
};
const MODEL_DECISION: StateEvent = {
  schema_version: 1,
  event_id: MODEL_DECISION_ID,
  event_type: "model_identity.approved",
  occurred_at: "2025-08-15T03:36:35.585Z",
  subject_id: MODEL_ID,
  causation_event_id: MODEL_REQUEST_ID,
  actor: { kind: "system" },
  payload: { reviewer_login: "reviewer" },
};
const PENDING_MODEL_VIEW = {
  schema_version: 1,
  model_id: MODEL_ID,
  owner_login: "alice",
  requested_name: "Model Alpha",
  display_name: "Model Alpha",
  status: "pending",
  request_event_id: MODEL_REQUEST_ID,
  requested_at: MODEL_REQUEST.occurred_at,
  decision_event_id: null,
  decided_at: null,
  reviewer_login: null,
  rejection_reason: null,
  mutation_event_id: MODEL_REQUEST_ID,
  consolidated_into: null,
  resolved_model_id: null,
} as const;
const APPROVED_MODEL_VIEW = {
  ...PENDING_MODEL_VIEW,
  status: "approved",
  decision_event_id: MODEL_DECISION_ID,
  decided_at: MODEL_DECISION.occurred_at,
  reviewer_login: "reviewer",
  mutation_event_id: MODEL_DECISION_ID,
  resolved_model_id: MODEL_ID,
} as const;

function eventPath(eventId: string): string {
  return `events/${eventId.replaceAll("-", "").slice(0, 2)}/${eventId}.json`;
}

function modelViewPath(modelId = MODEL_ID): string {
  return `views/model-identities/${modelId.slice(4, 6)}/${modelId}.json`;
}

function modelImpactPath(modelId = MODEL_ID): string {
  return `views/model-identity-reverse-impacts/${modelId.slice(4, 6)}/${modelId}.json`;
}

function modelImpact(
  terminal: ModelIdentityView = APPROVED_MODEL_VIEW,
  extraMembers: readonly Record<string, unknown>[] = [],
) {
  const members = [{
    kind: "identity",
    model_id: terminal.model_id,
    mutation_event_id: terminal.mutation_event_id,
    view_path: modelViewPath(terminal.model_id),
  }, ...extraMembers].sort((left, right) =>
    String(left.view_path).localeCompare(String(right.view_path)));
  return {
    schema_version: 1,
    terminal_model_id: terminal.model_id,
    owner_login: terminal.owner_login,
    terminal_mutation_event_id: terminal.mutation_event_id,
    member_count: members.length,
    maximum_member_count: 32,
    members,
  } as const;
}

function approvedModelFixture(
  modelId: string,
  requestEventId: string,
  decisionEventId: string,
  displayName: string,
) {
  const request: StateEvent = {
    schema_version: 1,
    event_id: requestEventId,
    event_type: "model_identity.requested",
    occurred_at: "2025-08-15T03:36:35.574Z",
    subject_id: modelId,
    causation_event_id: null,
    actor: { kind: "github", login: "alice" },
    payload: { display_name: displayName },
  };
  const decision: StateEvent = {
    schema_version: 1,
    event_id: decisionEventId,
    event_type: "model_identity.approved",
    occurred_at: "2025-08-15T03:36:35.575Z",
    subject_id: modelId,
    causation_event_id: requestEventId,
    actor: { kind: "system" },
    payload: { reviewer_login: "reviewer" },
  };
  const view: ModelIdentityView = {
    schema_version: 1,
    model_id: modelId,
    owner_login: "alice",
    requested_name: displayName,
    display_name: displayName,
    status: "approved",
    request_event_id: requestEventId,
    requested_at: request.occurred_at,
    decision_event_id: decisionEventId,
    decided_at: decision.occurred_at,
    reviewer_login: "reviewer",
    rejection_reason: null,
    mutation_event_id: decisionEventId,
    consolidated_into: null,
    resolved_model_id: modelId,
  };
  return { decision, request, view };
}

const SUBMISSION_ID = "0198abcd-1111-7000-8000-000000000001";
const METADATA_ID = "0198abcd-1111-7000-8000-000000000002";
const RECEIVED: StateEvent = {
  schema_version: 1,
  event_id: SUBMISSION_ID,
  event_type: "submission.received",
  occurred_at: "2026-08-20T06:07:08.000Z",
  subject_id: SUBMISSION_ID,
  causation_event_id: null,
  actor: { kind: "github", login: "alice" },
  payload: {
    problem_id: "two_plus_two",
    statement_revision: 2,
    declared_model: "Example Model",
    source_repository: "alice/proofs",
    source_commit: "a".repeat(40),
    source_visibility: "private",
    publication_choice: "scheduled",
  },
};
const METADATA: StateEvent = {
  schema_version: 1,
  event_id: METADATA_ID,
  event_type: "submission.metadata_amended",
  occurred_at: "2026-08-20T06:07:08.001Z",
  subject_id: SUBMISSION_ID,
  causation_event_id: SUBMISSION_ID,
  actor: { kind: "github", login: "alice" },
  payload: { production_metadata: { web_access: false } },
};
const VIEW: SubmissionView = {
  schema_version: 1,
  submission_id: SUBMISSION_ID,
  owner_login: "alice",
  received_event_id: SUBMISSION_ID,
  mutation_event_id: METADATA_ID,
  metadata_event_id: METADATA_ID,
  publication_event_id: null,
  accepted_at: "2026-08-20T06:07:08.000Z",
  submission: {
    problem_id: "two_plus_two",
    problem_group: "formalization-evaluation",
    statement_revision: 2,
    declared_model: "Example Model",
    source_repository: "alice/proofs",
    source_commit: "a".repeat(40),
    source_visibility: "private",
    publication_choice: "scheduled",
    production_metadata: { web_access: false },
  },
  production_metadata: { web_access: false },
  publication_choice: "scheduled",
  archive: { status: "pending" },
  evaluation: { status: "pending" },
  result_id: null,
  dispatch: {
    status: "succeeded",
    attempts: 1,
    requested_at: "2026-08-20T06:07:08.000Z",
    updated_at: "2026-08-20T06:07:08.003Z",
    workflow_ref: `lean-eval-dispatch/${"b".repeat(40)}`,
    last_error_code: null,
  },
};
const ARCHIVE_EVENT: WritableSubmissionLifecycleEvent = {
  schema_version: 1,
  event_id: "0198abcd-1111-7000-8000-000000000003",
  event_type: "archive.completed",
  occurred_at: "2026-08-20T06:07:09.000Z",
  subject_id: SUBMISSION_ID,
  causation_event_id: SUBMISSION_ID,
  actor: { kind: "system" },
  payload: {
    archive_repository: "leanprover/lean-eval-audit",
    archive_commit: "c".repeat(40),
    archive_path: `archives/01/${SUBMISSION_ID}.tar.age`,
    archive_ciphertext_sha256: "d".repeat(64),
    encrypted: true,
  },
};
const VIEW_V2: SubmissionView = {
  ...VIEW,
  schema_version: 2,
  result_event_id: null,
  archive: {
    status: "completed",
    event_id: ARCHIVE_EVENT.event_id,
    occurred_at: ARCHIVE_EVENT.occurred_at,
    ...ARCHIVE_EVENT.payload,
  },
};
const EVALUATION_STARTED: WritableSubmissionLifecycleEvent = {
  schema_version: 1,
  event_id: "0198abcd-1111-7000-8000-000000000004",
  event_type: "evaluation.started",
  occurred_at: "2026-08-20T06:07:10.000Z",
  subject_id: SUBMISSION_ID,
  causation_event_id: ARCHIVE_EVENT.event_id,
  actor: { kind: "system" },
  payload: {
    attempt: 1,
    benchmark_repository: "leanprover/lean-eval",
    benchmark_commit: "e".repeat(40),
    toolchain: "leanprover/lean4:v4.32.0",
  },
};
const EVALUATION_ACCEPTED: WritableSubmissionLifecycleEvent = {
  schema_version: 1,
  event_id: "0198abcd-1111-7000-8000-000000000005",
  event_type: "evaluation.accepted",
  occurred_at: "2026-08-20T06:07:10.001Z",
  subject_id: SUBMISSION_ID,
  causation_event_id: EVALUATION_STARTED.event_id,
  actor: { kind: "system" },
  payload: { attempt: 1, evaluator_version: "f".repeat(40) },
};
const ACCEPTED_VIEW: SubmissionView = {
  ...VIEW_V2,
  evaluation: {
    status: "accepted",
    event_id: EVALUATION_ACCEPTED.event_id,
    occurred_at: EVALUATION_ACCEPTED.occurred_at,
    ...EVALUATION_STARTED.payload,
    ...EVALUATION_ACCEPTED.payload,
  },
};
const RESULT_ID = `r2_${"a".repeat(64)}`;
const RESULT_EVENT: WritableResultLifecycleEvent = {
  schema_version: 1,
  event_id: "0198abcd-1111-7000-8000-000000000006",
  event_type: "result.recorded",
  occurred_at: "2026-08-20T06:07:11.000Z",
  subject_id: RESULT_ID,
  causation_event_id: EVALUATION_ACCEPTED.event_id,
  actor: { kind: "system" },
  payload: {
    submission_id: SUBMISSION_ID,
    problem_id: "two_plus_two",
    statement_revision: 2,
    result_commit: "9".repeat(40),
    tree_digest: "8".repeat(64),
  },
};
const RELEASE_EVENT: WritableResultLifecycleEvent = {
  schema_version: 1,
  event_id: "0198abcd-1111-7000-8000-000000000007",
  event_type: "release.scheduled",
  occurred_at: "2026-08-20T06:07:11.001Z",
  subject_id: RESULT_ID,
  causation_event_id: RESULT_EVENT.event_id,
  actor: { kind: "system" },
  payload: { result_id: RESULT_ID, release_at: "2026-10-20T06:07:10.001Z" },
};
const RESULT_VIEW: SubmissionView = {
  ...ACCEPTED_VIEW,
  result_id: RESULT_ID,
  result_event_id: RESULT_EVENT.event_id,
};
const RESULT_AMENDMENT_VIEW = initialResultAmendmentView({
  resultId: RESULT_ID,
  ownerLogin: "alice",
  declaredModel: "Example Model",
  problemId: "two_plus_two",
  statementRevision: 2,
  authorityEventId: RESULT_EVENT.event_id,
  mutationEventId: RESULT_EVENT.event_id,
});
const RESULT_RELEASE_STATUS_VIEW = initialResultReleaseStatusView(
  RESULT_ID,
  RESULT_EVENT.event_id,
  RELEASE_EVENT.event_id,
);
const CLAIM_EVENT_ID = "0198abcd-2222-7000-8000-000000000001";
const BACKFILL_EVENT_ID = "0198abcd-2222-7000-8000-000000000002";
const LEGACY_RESULT: VerifiedLegacyResult = {
  resultId: `r2_${"1".repeat(64)}`,
  ownerLogin: "alice",
  baseResult: {
    declared_model: "Example Model",
    problem_id: "two_plus_two",
    statement_revision: 1,
    results_repository: "leanprover/lean-eval-submissions",
    results_commit: "a".repeat(40),
    results_path: "results/alice.json",
    canonical_record_sha256: "b".repeat(64),
  },
};
const LEGACY_AMENDMENT_VIEW = initialResultAmendmentView({
  resultId: LEGACY_RESULT.resultId,
  ownerLogin: LEGACY_RESULT.ownerLogin,
  declaredModel: LEGACY_RESULT.baseResult.declared_model,
  problemId: LEGACY_RESULT.baseResult.problem_id,
  statementRevision: LEGACY_RESULT.baseResult.statement_revision,
  authorityEventId: CLAIM_EVENT_ID,
  mutationEventId: CLAIM_EVENT_ID,
});
const LEGACY_RELEASE_STATUS_VIEW = initialResultReleaseStatusView(
  LEGACY_RESULT.resultId,
  CLAIM_EVENT_ID,
);
const REPAIR_REQUEST_ID = "0198abcd-2222-7000-8000-000000000003";
const REPAIR_DECISION_ID = "0198abcd-2222-7000-8000-000000000004";
const REPAIR_REQUESTED_AT = "2026-08-20T06:07:09.000Z";
const REPAIR_DECIDED_AT = "2026-08-20T06:07:10.000Z";

function pendingLegacyProblemRepair() {
  return requestedProblemRepairView(
    LEGACY_AMENDMENT_VIEW,
    REPAIR_REQUEST_ID,
    REPAIR_REQUESTED_AT,
    "two_plus_three",
    2,
    "wrong_problem_revision",
  );
}

function legacyProblemRepairRequestEvent(): StateEvent {
  return {
    schema_version: 1,
    event_id: REPAIR_REQUEST_ID,
    event_type: "result.problem_repair_requested",
    occurred_at: REPAIR_REQUESTED_AT,
    subject_id: LEGACY_RESULT.resultId,
    causation_event_id: CLAIM_EVENT_ID,
    actor: { kind: "github", login: "alice" },
    payload: {
      repair_revision: 1,
      corrected_problem_id: "two_plus_three",
      corrected_statement_revision: 2,
      reason_code: "wrong_problem_revision",
    },
  };
}

async function legacyComparatorEvidence(): Promise<ComparatorEvidence> {
  const evidenceWithoutBinding = {
    repository: "leanprover/lean-eval-submissions",
    commit: "a".repeat(40),
    path: "results/alice.json",
    blob_oid: "c".repeat(40),
    blob_sha256: "d".repeat(64),
    record_sha256: "b".repeat(64),
    verification_method: "github_commit_blob_v1" as const,
    evidence_result_id: LEGACY_RESULT.resultId,
    evidence_owner_login: "alice",
    evidence_declared_model: "Example Model",
    evidence_base_problem_group: "formalization-evaluation" as const,
    evidence_base_problem_id: "two_plus_two",
    evidence_base_statement_revision: 1,
    evidence_base_challenge_id: await challengeId("formalization-evaluation", "two_plus_two", 1),
    evidence_corrected_problem_group: "formalization-evaluation" as const,
    evidence_corrected_problem_id: "two_plus_three",
    evidence_corrected_statement_revision: 2,
    evidence_corrected_challenge_id: await challengeId("formalization-evaluation", "two_plus_three", 2),
  };
  return {
    ...evidenceWithoutBinding,
    binding_sha256: await comparatorBindingSha256(evidenceWithoutBinding),
  };
}

async function legacyBaseReservation() {
  return effectiveResultIdentityReservation({
    ownerLogin: LEGACY_RESULT.ownerLogin,
    declaredModel: LEGACY_RESULT.baseResult.declared_model,
    problemId: LEGACY_RESULT.baseResult.problem_id,
    statementRevision: LEGACY_RESULT.baseResult.statement_revision,
    resultId: LEGACY_RESULT.resultId,
    reservationEventId: CLAIM_EVENT_ID,
    reservationKind: "result_authority",
  });
}

async function legacyRepairReservation(
  resultId = LEGACY_RESULT.resultId,
  reservationEventId = REPAIR_DECISION_ID,
) {
  return effectiveResultIdentityReservation({
    ownerLogin: LEGACY_RESULT.ownerLogin,
    declaredModel: LEGACY_RESULT.baseResult.declared_model,
    problemId: "two_plus_three",
    statementRevision: 2,
    resultId,
    reservationEventId,
    reservationKind: "problem_repair",
  });
}

function legacyClaimEvent(occurredAt = "2026-08-24T08:00:00.000Z"): StateEvent {
  return {
    schema_version: 1,
    event_id: CLAIM_EVENT_ID,
    event_type: "result.claimed",
    occurred_at: occurredAt,
    subject_id: LEGACY_RESULT.resultId,
    causation_event_id: null,
    actor: { kind: "github", login: "alice" },
    payload: LEGACY_RESULT.baseResult,
  };
}

function startedReleaseEvent(eventId: string): StateEvent {
  return {
    schema_version: 1,
    event_id: eventId,
    event_type: "release.started",
    occurred_at: "2026-08-20T06:07:08.500Z",
    subject_id: LEGACY_RESULT.resultId,
    causation_event_id: CLAIM_EVENT_ID,
    actor: { kind: "system" },
    payload: { attempt: 1 },
  };
}

function publishedReleaseEvent(
  eventId: string,
  startedEventId = "0198abcd-2222-7000-8000-000000000004",
): StateEvent {
  return {
    schema_version: 1,
    event_id: eventId,
    event_type: "release.published",
    occurred_at: "2026-08-20T06:07:09.000Z",
    subject_id: LEGACY_RESULT.resultId,
    causation_event_id: startedEventId,
    actor: { kind: "system" },
    payload: {
      attempt: 1,
      repository_commit: "9".repeat(40),
      tree_digest: "8".repeat(64),
      path: `releases/2026/08/${LEGACY_RESULT.resultId}`,
    },
  };
}

function removedReleaseEvent(
  eventId: string,
  publishedEventId: string,
  payloadOverrides: Readonly<Record<string, unknown>> = {},
): Record<string, unknown> {
  return {
    schema_version: 1,
    event_id: eventId,
    event_type: "release.removed",
    occurred_at: "2026-08-20T06:07:10.000Z",
    subject_id: LEGACY_RESULT.resultId,
    causation_event_id: publishedEventId,
    actor: { kind: "system" },
    payload: {
      incident_id: "0198abcd-2222-7000-8000-000000000099",
      classification: "erroneous_publication",
      published_state_event_repository: "leanprover/lean-eval-state",
      published_state_event_commit: "1".repeat(40),
      published_state_event_path:
        `events/${publishedEventId.replaceAll("-", "").slice(0, 2)}/${publishedEventId}.json`,
      published_state_event_blob: "2".repeat(40),
      published_state_event_sha256: "3".repeat(64),
      published_repository_commit: "9".repeat(40),
      published_repository_tree: "4".repeat(40),
      published_release_tree_sha256: "8".repeat(64),
      release_path: `releases/2026/08/${LEGACY_RESULT.resultId}`,
      bundle_path: "sources/0198abcd-2222-7000-8000-000000000098.tar.gz",
      bundle_sha256: "5".repeat(64),
      bundle_disposition: "delete",
      shared_release_paths: [],
      evidence_repository: "leanprover/lean-eval-audit",
      evidence_commit: "6".repeat(40),
      evidence_path: "incidents/0198abcd-2222-7000-8000-000000000099.json",
      evidence_blob: "7".repeat(40),
      evidence_sha256: "a".repeat(64),
      removal_repository_commit: "b".repeat(40),
      removal_repository_tree: "c".repeat(40),
      ...payloadOverrides,
    },
  };
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

type ContentsMetadata = "exact" | "missing-path" | "missing-type" | "wrong-path";
type ContentsFixture = Readonly<{
  kind: "contents";
  metadata: ContentsMetadata;
  value: unknown;
}>;
type QueuedResponse = Response | Error | ContentsFixture;

function contents(value: unknown): ContentsFixture {
  return { kind: "contents", metadata: "exact", value };
}

function contentsResponse(
  value: unknown,
  path: string,
  metadata: ContentsMetadata = "exact",
): Response {
  const body: Record<string, unknown> = {
    encoding: "base64",
    content: btoa(`${JSON.stringify(value)}\n`),
  };
  if (metadata !== "missing-type") body.type = "file";
  if (metadata !== "missing-path") {
    body.path = metadata === "wrong-path" ? `${path}.wrong` : path;
  }
  return json(body);
}

function outbox(index: number, shard = "01") {
  const submissionId = `0198abcd-1111-7000-8000-${index.toString(16).padStart(10, "0")}${shard}`;
  return {
    schema_version: 1,
    submission_id: submissionId,
    owner_login: VIEW.owner_login,
    submission: VIEW.submission,
    attempts: 0,
    next_attempt_at: VIEW.accepted_at,
    workflow_ref: VIEW.dispatch.workflow_ref,
  } as const;
}

function resultOwnerContractProofResponses(
  changedPath?: keyof typeof RESULT_OWNER_CONTRACT_ROOT_ENTRIES,
  treeSha = TREE,
): Response[] {
  return [
    json({
      status: "ahead",
      merge_base_commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
    }),
    resultOwnerContractRootTreeResponse(changedPath, treeSha),
  ];
}

function resultOwnerContractRootTreeResponse(
  changedPath?: keyof typeof RESULT_OWNER_CONTRACT_ROOT_ENTRIES,
  treeSha = TREE,
): Response {
  return json({
    sha: treeSha,
    truncated: false,
    tree: Object.entries(RESULT_OWNER_CONTRACT_ROOT_ENTRIES).map(([path, entry]) => ({
      path,
      mode: entry.mode,
      type: entry.type,
      sha: path === changedPath ? "0".repeat(40) : entry.sha,
    })),
  });
}

function modelIdentityContractProofResponses(treeSha = TREE): Response[] {
  return [
    json({ name: "main", protected: true, commit: { sha: HEAD } }),
    json({
      status: "ahead",
      merge_base_commit: { sha: MODEL_IDENTITY_CONTRACT_COMMIT },
    }),
    modelIdentityContractRootTreeResponse(treeSha),
  ];
}

function modelIdentityContractRootTreeResponse(
  treeSha = TREE,
  rootEntries: Readonly<Record<string, Readonly<{ mode: string; type: string; sha: string }>>> =
    MODEL_IDENTITY_CONTRACT_ROOT_ENTRIES,
): Response {
  return json({
    sha: treeSha,
    truncated: false,
    tree: Object.entries(rootEntries).map(([path, entry]) => ({
      path,
      mode: entry.mode,
      type: entry.type,
      sha: entry.sha,
    })),
  });
}

function modelWriterFetcher(
  documents: Readonly<Record<string, unknown>>,
  referenceStatus = 200,
) {
  const treeBodies: Record<string, unknown>[] = [];
  const fetcher = vi.fn<GitHubFetch>((input, init) => {
    const url = new URL(fetchUrl(input));
    const method = init?.method ?? "GET";
    if (url.pathname.endsWith("/git/ref/heads/main") && method === "GET") {
      return Promise.resolve(json({ object: { sha: HEAD } }));
    }
    if (url.pathname.endsWith(`/git/commits/${HEAD}`) && method === "GET") {
      return Promise.resolve(json({ tree: { sha: TREE } }));
    }
    if (url.pathname.endsWith("/branches/main") && method === "GET") {
      return Promise.resolve(json({ name: "main", protected: true, commit: { sha: HEAD } }));
    }
    if (url.pathname.includes(`/compare/${MODEL_IDENTITY_CONTRACT_COMMIT}...${HEAD}`)) {
      return Promise.resolve(json({ status: "ahead", merge_base_commit: { sha: MODEL_IDENTITY_CONTRACT_COMMIT } }));
    }
    if (url.pathname.endsWith(`/git/trees/${TREE}`) && method === "GET") {
      return Promise.resolve(modelIdentityContractRootTreeResponse());
    }
    const contentsMarker = "/contents/";
    const contentsIndex = url.pathname.indexOf(contentsMarker);
    if (contentsIndex !== -1 && method === "GET") {
      const path = decodeURI(url.pathname.slice(contentsIndex + contentsMarker.length));
      const document = documents[path];
      return Promise.resolve(Object.hasOwn(documents, path)
        ? document instanceof Response ? document.clone() : contentsResponse(document, path)
        : json({ message: "not found" }, 404));
    }
    if (url.pathname.endsWith("/git/trees") && method === "POST") {
      if (typeof init?.body !== "string") throw new TypeError("tree request body was not JSON");
      treeBodies.push(JSON.parse(init.body) as Record<string, unknown>);
      return Promise.resolve(json({ sha: NEW_TREE }, 201));
    }
    if (url.pathname.endsWith("/git/commits") && method === "POST") {
      return Promise.resolve(json({ sha: NEW_COMMIT }, 201));
    }
    if (url.pathname.endsWith("/git/refs/heads/main") && method === "PATCH") {
      return Promise.resolve(referenceStatus === 200
        ? json({ object: { sha: NEW_COMMIT } })
        : json({ message: "not a fast-forward" }, referenceStatus));
    }
    throw new Error(`unexpected GitHub request: ${method} ${url.pathname}`);
  });
  return { fetcher, treeBodies };
}

function sequence(responses: readonly QueuedResponse[]) {
  const queue = [...responses];
  return vi.fn<GitHubFetch>((input) => {
    const response = queue.shift();
    if (!response) throw new Error("unexpected GitHub request");
    if (response instanceof Error) return Promise.reject(response);
    if (!(response instanceof Response)) {
      const url = new URL(fetchUrl(input));
      const marker = "/contents/";
      const index = url.pathname.indexOf(marker);
      if (index === -1) throw new Error("contents fixture was not used for a contents request");
      const path = decodeURI(url.pathname.slice(index + marker.length));
      return Promise.resolve(contentsResponse(response.value, path, response.metadata));
    }
    return Promise.resolve(response);
  });
}

function repository(fetcher: GitHubFetch): GitHubStateRepository {
  return new GitHubStateRepository(
    { repository: "leanprover/lean-eval-state", token: "secret", userAgent: "test" },
    fetcher,
  );
}

function productionRepository(fetcher: GitHubFetch): GitHubStateRepository {
  return new GitHubStateRepository(
    { repository: "leanprover/lean-eval-state", token: "secret", userAgent: "test" },
    fetcher,
  );
}

function stagingRepository(fetcher: GitHubFetch): GitHubStateRepository {
  return new GitHubStateRepository(
    { repository: "leanprover/lean-eval-state-staging", token: "secret", userAgent: "test" },
    fetcher,
  );
}

function fetchUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof Request ? input.url : input.href;
}

describe("atomic Git State append", () => {
  beforeEach(() => clearResultOwnerContractProofCacheForTest());

  it("exports the exact scheduled dispatch CAS subrequest ceiling", () => {
    expect(DISPATCH_UPDATE_MAX_SUBREQUESTS).toBe(144);
  });

  it("exports a conservative bounded synchronous model-identity CAS ceiling", () => {
    const perAttempt = 5 + 32 + 2 + 5 + 2 + 4;
    expect(MODEL_IDENTITY_WRITE_MAX_SUBREQUESTS).toBe(8 * perAttempt);
    expect(MODEL_IDENTITY_WRITE_MAX_SUBREQUESTS).toBe(400);
    expect(MODEL_IDENTITY_WRITE_MAX_SUBREQUESTS).toBeLessThanOrEqual(400);
  });

  it("atomically creates a derived model identity request and targeted view", async () => {
    const eventId = "0198abcd-0000-7000-8000-000000000020";
    const modelId = await modelIdentityId(eventId);
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...modelIdentityContractProofResponses(),
      json({ message: "not found" }, 404),
      json({ message: "not found" }, 404),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).requestModelIdentity({
      eventId,
      occurredAt: "2025-08-15T03:36:35.584Z",
      ownerLogin: "alice",
      displayName: "Model Alpha",
    })).resolves.toEqual({ commit: NEW_COMMIT, created: true, modelId });
    const treeRequest = fetcher.mock.calls[7]?.[1];
    if (typeof treeRequest?.body !== "string") throw new TypeError("tree request body was not JSON");
    const treeBody = JSON.parse(treeRequest.body) as { tree: { path: string; content: string }[] };
    expect(treeBody.tree.map((entry) => entry.path)).toEqual([
      `events/01/${eventId}.json`,
      `views/model-identities/${modelId.slice(4, 6)}/${modelId}.json`,
    ]);
    expect(treeBody.tree[0]?.content).toContain('"event_type": "model_identity.requested"');
    expect(treeBody.tree[1]?.content).toContain('"owner_login": "alice"');
  });

  it("idempotently reuses only the exact immutable model request event and view", async () => {
    const retry = modelWriterFetcher({
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [modelViewPath()]: PENDING_MODEL_VIEW,
    });
    await expect(repository(retry.fetcher).requestModelIdentity({
      eventId: MODEL_REQUEST_ID,
      occurredAt: "2025-08-15T03:36:35.590Z",
      ownerLogin: "alice",
      displayName: "Model Alpha",
    })).resolves.toEqual({ commit: HEAD, created: false, modelId: MODEL_ID });
    expect(retry.treeBodies).toHaveLength(0);
  });

  it("rejects cross-endpoint Idempotency-Key reuse before overwriting its immutable event", async () => {
    const aliasEvent: StateEvent = {
      schema_version: 1,
      event_id: MODEL_ALIAS_ID,
      event_type: "model_identity.alias_assigned",
      occurred_at: "2025-08-15T03:36:35.586Z",
      subject_id: MODEL_ID,
      causation_event_id: MODEL_DECISION_ID,
      actor: { kind: "github", login: "alice" },
      payload: { alias: "Legacy Model" },
    };
    const collision = modelWriterFetcher({
      [eventPath(MODEL_ALIAS_ID)]: aliasEvent,
    });
    await expect(repository(collision.fetcher).requestModelIdentity({
      eventId: MODEL_ALIAS_ID,
      occurredAt: "2025-08-15T03:36:35.590Z",
      ownerLogin: "alice",
      displayName: "Different Model",
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(collision.treeBodies).toHaveLength(0);
  });

  it("fails model writes closed on wrong ancestry or changed protected source blobs", async () => {
    const request = {
      eventId: MODEL_REQUEST_ID,
      occurredAt: MODEL_REQUEST.occurred_at,
      ownerLogin: "alice",
      displayName: "Model Alpha",
    } as const;
    const wrongAncestry = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      json({ name: "main", protected: true, commit: { sha: HEAD } }),
      json({ status: "diverged", merge_base_commit: { sha: "0".repeat(40) } }),
    ]);
    await expect(repository(wrongAncestry).requestModelIdentity(request))
      .rejects.toMatchObject({ status: 503 });
    expect(wrongAncestry.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const unprotected = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      json({ name: "main", protected: false, commit: { sha: HEAD } }),
    ]);
    await expect(repository(unprotected).requestModelIdentity(request))
      .rejects.toMatchObject({ status: 503 });
    expect(unprotected.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const changedScripts = {
      ...MODEL_IDENTITY_CONTRACT_ROOT_ENTRIES,
      scripts: { ...MODEL_IDENTITY_CONTRACT_ROOT_ENTRIES.scripts, sha: "0".repeat(40) },
    };
    const changedSource = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      json({ name: "main", protected: true, commit: { sha: HEAD } }),
      json({
        status: "ahead",
        merge_base_commit: { sha: MODEL_IDENTITY_CONTRACT_COMMIT },
      }),
      modelIdentityContractRootTreeResponse(TREE, changedScripts),
    ]);
    await expect(repository(changedSource).requestModelIdentity(request))
      .rejects.toMatchObject({ status: 503 });
    expect(changedSource.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("binds staging model writes to the byte-exact portable reverse-impact contract", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      json({ name: "main", protected: true, commit: { sha: HEAD } }),
      json({
        status: "ahead",
        merge_base_commit: { sha: STAGING_MODEL_IDENTITY_CONTRACT_COMMIT },
      }),
      modelIdentityContractRootTreeResponse(
        TREE,
        STAGING_MODEL_IDENTITY_CONTRACT_ROOT_ENTRIES,
      ),
      json({ message: "not found" }, 404),
      json({ message: "not found" }, 404),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(stagingRepository(fetcher).requestModelIdentity({
      eventId: MODEL_REQUEST_ID,
      occurredAt: MODEL_REQUEST.occurred_at,
      ownerLogin: "alice",
      displayName: "Model Alpha",
    })).resolves.toMatchObject({ created: true, commit: NEW_COMMIT });
    expect(fetcher.mock.calls.map(([input]) => fetchUrl(input)).join("\n")).toContain(
      `/compare/${STAGING_MODEL_IDENTITY_CONTRACT_COMMIT}...${HEAD}`,
    );
  });

  it("writes and idempotently reuses the exact model decision event and view", async () => {
    const initial = modelWriterFetcher({
      [modelViewPath()]: PENDING_MODEL_VIEW,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
    });
    await expect(repository(initial.fetcher).decideModelIdentity({
      eventId: MODEL_DECISION_ID,
      occurredAt: MODEL_DECISION.occurred_at,
      modelId: MODEL_ID,
      reviewerLogin: "reviewer",
      decision: "approve",
      reasonCode: null,
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      modelId: MODEL_ID,
      status: "approved",
    });
    const written = initial.treeBodies[0] as { tree: { path: string; content: string }[] };
    expect(written.tree.map((entry) => entry.path)).toEqual([
      eventPath(MODEL_DECISION_ID),
      modelViewPath(),
      modelImpactPath(),
    ]);
    expect(written.tree[0]?.content).toContain('"actor": {\n    "kind": "system"');
    expect(written.tree[1]?.content).toContain('"status": "approved"');

    clearResultOwnerContractProofCacheForTest();
    const retry = modelWriterFetcher({
      [modelViewPath()]: APPROVED_MODEL_VIEW,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [modelImpactPath()]: modelImpact(),
    });
    await expect(repository(retry.fetcher).decideModelIdentity({
      eventId: MODEL_DECISION_ID,
      occurredAt: MODEL_DECISION.occurred_at,
      modelId: MODEL_ID,
      reviewerLogin: "reviewer",
      decision: "approve",
      reasonCode: null,
    })).resolves.toMatchObject({ created: false, modelId: MODEL_ID, status: "approved" });
    expect(retry.treeBodies).toHaveLength(0);
  });

  it("writes and idempotently reuses a permanent owner-scoped alias", async () => {
    const alias = "Legacy Model";
    const aliasKey = await modelAliasKey("alice", alias);
    const aliasPath = `views/model-aliases/${aliasKey.slice(4, 6)}/${aliasKey}.json`;
    const initial = modelWriterFetcher({
      [modelViewPath()]: APPROVED_MODEL_VIEW,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [modelImpactPath()]: modelImpact(),
    });
    await expect(repository(initial.fetcher).assignModelAlias({
      eventId: MODEL_ALIAS_ID,
      occurredAt: "2025-08-15T03:36:35.586Z",
      modelId: MODEL_ID,
      ownerLogin: "alice",
      alias,
    })).resolves.toEqual({ commit: NEW_COMMIT, created: true, modelId: MODEL_ID, aliasKey });
    const written = initial.treeBodies[0] as { tree: { path: string; content: string }[] };
    expect(written.tree.map((entry) => entry.path)).toEqual([
      eventPath(MODEL_ALIAS_ID),
      modelViewPath(),
      aliasPath,
      modelImpactPath(),
    ]);
    expect(written.tree[2]?.content).toContain(`"alias_key": "${aliasKey}"`);

    clearResultOwnerContractProofCacheForTest();
    const aliasEvent: StateEvent = {
      schema_version: 1,
      event_id: MODEL_ALIAS_ID,
      event_type: "model_identity.alias_assigned",
      occurred_at: "2025-08-15T03:36:35.586Z",
      subject_id: MODEL_ID,
      causation_event_id: MODEL_DECISION_ID,
      actor: { kind: "github", login: "alice" },
      payload: { alias },
    };
    const aliasView = {
      schema_version: 1,
      alias_key: aliasKey,
      owner_login: "alice",
      alias,
      model_id: MODEL_ID,
      assignment_event_id: MODEL_ALIAS_ID,
      assigned_at: aliasEvent.occurred_at,
      resolved_model_id: MODEL_ID,
    } as const;
    const aliasTerminal = { ...APPROVED_MODEL_VIEW, mutation_event_id: MODEL_ALIAS_ID } as const;
    const retry = modelWriterFetcher({
      [modelViewPath()]: aliasTerminal,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [eventPath(MODEL_ALIAS_ID)]: aliasEvent,
      [aliasPath]: aliasView,
      [modelImpactPath()]: modelImpact(aliasTerminal, [{
        kind: "alias",
        alias_key: aliasKey,
        assignment_event_id: MODEL_ALIAS_ID,
        model_id: MODEL_ID,
        view_path: aliasPath,
      }]),
    });
    await expect(repository(retry.fetcher).assignModelAlias({
      eventId: MODEL_ALIAS_ID,
      occurredAt: aliasEvent.occurred_at,
      modelId: MODEL_ID,
      ownerLogin: "alice",
      alias,
    })).resolves.toMatchObject({ created: false, modelId: MODEL_ID, aliasKey });
    expect(retry.treeBodies).toHaveLength(0);
  });

  it("writes and idempotently reuses an owner-derived rename", async () => {
    const initial = modelWriterFetcher({
      [modelViewPath()]: APPROVED_MODEL_VIEW,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [modelImpactPath()]: modelImpact(),
    });
    const request = {
      eventId: MODEL_RENAME_ID,
      occurredAt: "2025-08-15T03:36:35.587Z",
      modelId: MODEL_ID,
      ownerLogin: "alice",
      displayName: "Model Beta",
    } as const;
    await expect(repository(initial.fetcher).renameModelIdentity(request)).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      modelId: MODEL_ID,
    });
    const written = initial.treeBodies[0] as { tree: { path: string; content: string }[] };
    expect(written.tree.map((entry) => entry.path)).toEqual([
      eventPath(MODEL_RENAME_ID),
      modelViewPath(),
      modelImpactPath(),
    ]);
    expect(written.tree[1]?.content).toContain('"display_name": "Model Beta"');

    clearResultOwnerContractProofCacheForTest();
    const renameEvent: StateEvent = {
      schema_version: 1,
      event_id: MODEL_RENAME_ID,
      event_type: "model_identity.renamed",
      occurred_at: request.occurredAt,
      subject_id: MODEL_ID,
      causation_event_id: MODEL_DECISION_ID,
      actor: { kind: "github", login: "alice" },
      payload: { display_name: request.displayName },
    };
    const renamedTerminal = {
        ...APPROVED_MODEL_VIEW,
        display_name: request.displayName,
        mutation_event_id: MODEL_RENAME_ID,
      } as const;
    const retry = modelWriterFetcher({
      [modelViewPath()]: renamedTerminal,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [eventPath(MODEL_RENAME_ID)]: renameEvent,
      [modelImpactPath()]: modelImpact(renamedTerminal),
    });
    await expect(repository(retry.fetcher).renameModelIdentity(request)).resolves.toMatchObject({
      created: false,
      modelId: MODEL_ID,
    });
    expect(retry.treeBodies).toHaveLength(0);
  });

  it("atomically rematerializes the complete source component during consolidation", async () => {
    const targetRequestId = "0198abcd-0010-7000-8000-000000000030";
    const targetModelId = await modelIdentityId(targetRequestId);
    const targetDecisionId = "0198abcd-0011-7000-8000-000000000031";
    const target = approvedModelFixture(
      targetModelId,
      targetRequestId,
      targetDecisionId,
      "Target Model",
    );
    const predecessorRequestId = "0198abcd-0020-7000-8000-000000000040";
    const predecessorModelId = await modelIdentityId(predecessorRequestId);
    const predecessorDecisionId = "0198abcd-0021-7000-8000-000000000041";
    const predecessorConsolidationId = "0198abcd-0022-7000-8000-000000000042";
    const consolidationId = "0198abcd-0004-7000-8000-000000000024";
    const alias = "Legacy Model";
    const aliasKey = await modelAliasKey("alice", alias);
    const aliasPath = `views/model-aliases/${aliasKey.slice(4, 6)}/${aliasKey}.json`;
    const sourceTerminal = { ...APPROVED_MODEL_VIEW, mutation_event_id: MODEL_ALIAS_ID } as const;
    const aliasEvent: StateEvent = {
      schema_version: 1,
      event_id: MODEL_ALIAS_ID,
      event_type: "model_identity.alias_assigned",
      occurred_at: "2025-08-15T03:36:35.586Z",
      subject_id: MODEL_ID,
      causation_event_id: MODEL_DECISION_ID,
      actor: { kind: "github", login: "alice" },
      payload: { alias },
    };
    const aliasView = {
      schema_version: 1,
      alias_key: aliasKey,
      owner_login: "alice",
      alias,
      model_id: MODEL_ID,
      assignment_event_id: MODEL_ALIAS_ID,
      assigned_at: aliasEvent.occurred_at,
      resolved_model_id: MODEL_ID,
    } as const;
    const predecessor = {
      schema_version: 1,
      model_id: predecessorModelId,
      owner_login: "alice",
      requested_name: "Predecessor",
      display_name: "Predecessor",
      status: "consolidated",
      request_event_id: predecessorRequestId,
      requested_at: "2025-08-15T03:36:35.570Z",
      decision_event_id: predecessorDecisionId,
      decided_at: "2025-08-15T03:36:35.571Z",
      reviewer_login: "reviewer",
      rejection_reason: null,
      mutation_event_id: predecessorConsolidationId,
      consolidated_into: MODEL_ID,
      resolved_model_id: MODEL_ID,
    } as const;
    const sourceImpact = modelImpact(sourceTerminal, [{
      kind: "alias",
      alias_key: aliasKey,
      assignment_event_id: MODEL_ALIAS_ID,
      model_id: MODEL_ID,
      view_path: aliasPath,
    }, {
      kind: "identity",
      model_id: predecessorModelId,
      mutation_event_id: predecessorConsolidationId,
      view_path: modelViewPath(predecessorModelId),
    }]);
    const targetImpact = {
      schema_version: 1,
      terminal_model_id: targetModelId,
      owner_login: "alice",
      terminal_mutation_event_id: targetDecisionId,
      member_count: 1,
      maximum_member_count: 32,
      members: [{
        kind: "identity",
        model_id: targetModelId,
        mutation_event_id: targetDecisionId,
        view_path: modelViewPath(targetModelId),
      }],
    } as const;
    const initial = modelWriterFetcher({
      [modelImpactPath()]: sourceImpact,
      [modelImpactPath(targetModelId)]: targetImpact,
      [modelViewPath()]: sourceTerminal,
      [modelViewPath(targetModelId)]: target.view,
      [modelViewPath(predecessorModelId)]: predecessor,
      [aliasPath]: aliasView,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [eventPath(MODEL_ALIAS_ID)]: aliasEvent,
      [eventPath(targetRequestId)]: target.request,
      [eventPath(targetDecisionId)]: target.decision,
    });
    const request = {
      eventId: consolidationId,
      occurredAt: "2025-08-15T03:36:35.588Z",
      modelId: MODEL_ID,
      targetModelId,
      ownerLogin: "alice",
    } as const;
    await expect(repository(initial.fetcher).consolidateModelIdentity(request)).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      modelId: MODEL_ID,
      targetModelId,
    });
    const written = initial.treeBodies[0] as {
      tree: { path: string; content?: string; sha?: null }[];
    };
    expect(written.tree[0]?.path).toBe(eventPath(consolidationId));
    const writes = new Map(written.tree.slice(1).map((entry) => [entry.path, entry]));
    expect(JSON.parse(writes.get(modelViewPath())?.content ?? "null")).toMatchObject({
      status: "consolidated",
      consolidated_into: targetModelId,
      resolved_model_id: targetModelId,
      mutation_event_id: consolidationId,
    });
    expect(JSON.parse(writes.get(modelViewPath(predecessorModelId))?.content ?? "null"))
      .toMatchObject({ consolidated_into: MODEL_ID, resolved_model_id: targetModelId });
    expect(JSON.parse(writes.get(aliasPath)?.content ?? "null"))
      .toMatchObject({ model_id: MODEL_ID, resolved_model_id: targetModelId });
    expect(writes.get(modelImpactPath())).toMatchObject({ sha: null });
    const mergedImpact = JSON.parse(
      writes.get(modelImpactPath(targetModelId))?.content ?? "null",
    ) as { member_count: number; members: { view_path: string }[] };
    expect(mergedImpact.member_count).toBe(4);
    expect(mergedImpact.members.map((member) => member.view_path)).toEqual(
      [...mergedImpact.members.map((member) => member.view_path)].sort(),
    );

    clearResultOwnerContractProofCacheForTest();
    const consolidatedSource = {
      ...sourceTerminal,
      status: "consolidated",
      mutation_event_id: consolidationId,
      consolidated_into: targetModelId,
      resolved_model_id: targetModelId,
    } as const;
    const consolidationEvent: StateEvent = {
      schema_version: 1,
      event_id: consolidationId,
      event_type: "model_identity.consolidated",
      occurred_at: request.occurredAt,
      subject_id: MODEL_ID,
      causation_event_id: MODEL_ALIAS_ID,
      actor: { kind: "github", login: "alice" },
      payload: { target_model_id: targetModelId },
    };
    const retryImpact: unknown = JSON.parse(
      writes.get(modelImpactPath(targetModelId))?.content ?? "null",
    );
    const retry = modelWriterFetcher({
      [eventPath(consolidationId)]: consolidationEvent,
      [eventPath(MODEL_ALIAS_ID)]: aliasEvent,
      [eventPath(targetRequestId)]: target.request,
      [eventPath(targetDecisionId)]: target.decision,
      [modelViewPath()]: consolidatedSource,
      [modelViewPath(targetModelId)]: target.view,
      [modelViewPath(predecessorModelId)]: {
        ...predecessor,
        resolved_model_id: targetModelId,
      },
      [aliasPath]: { ...aliasView, resolved_model_id: targetModelId },
      [modelImpactPath(targetModelId)]: retryImpact,
    });
    await expect(repository(retry.fetcher).consolidateModelIdentity(request)).resolves
      .toMatchObject({ created: false, modelId: MODEL_ID, targetModelId });
    expect(retry.treeBodies).toHaveLength(0);

    clearResultOwnerContractProofCacheForTest();
    const corruptCausation = modelWriterFetcher({
      [eventPath(consolidationId)]: {
        ...consolidationEvent,
        causation_event_id: MODEL_REQUEST_ID,
      },
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(targetRequestId)]: target.request,
      [eventPath(targetDecisionId)]: target.decision,
      [modelViewPath()]: consolidatedSource,
      [modelViewPath(targetModelId)]: target.view,
      [modelViewPath(predecessorModelId)]: {
        ...predecessor,
        resolved_model_id: targetModelId,
      },
      [aliasPath]: { ...aliasView, resolved_model_id: targetModelId },
      [modelImpactPath(targetModelId)]: retryImpact,
    });
    await expect(repository(corruptCausation.fetcher).consolidateModelIdentity(request))
      .rejects.toBeInstanceOf(StateEventConflictError);
    expect(corruptCausation.treeBodies).toHaveLength(0);

    clearResultOwnerContractProofCacheForTest();
    const corruptReplay = modelWriterFetcher({
      [eventPath(consolidationId)]: consolidationEvent,
      [eventPath(MODEL_ALIAS_ID)]: aliasEvent,
      [eventPath(targetRequestId)]: target.request,
      [eventPath(targetDecisionId)]: target.decision,
      [modelViewPath()]: consolidatedSource,
      [modelViewPath(targetModelId)]: target.view,
      [modelViewPath(predecessorModelId)]: predecessor,
      [aliasPath]: { ...aliasView, resolved_model_id: targetModelId },
      [modelImpactPath(targetModelId)]: retryImpact,
    });
    await expect(repository(corruptReplay.fetcher).consolidateModelIdentity(request))
      .rejects.toBeInstanceOf(StateEventConflictError);
    expect(corruptReplay.treeBodies).toHaveLength(0);
  });

  it("rejects an exact consolidation replay whose predecessor is later in append authority", async () => {
    const consolidationId = "0198abcd-0001-7000-8000-000000000024";
    const targetRequestId = "0198abcd-0010-7000-8000-000000000030";
    const targetModelId = await modelIdentityId(targetRequestId);
    const targetDecisionId = "0198abcd-0011-7000-8000-000000000031";
    const target = approvedModelFixture(
      targetModelId,
      targetRequestId,
      targetDecisionId,
      "Target Model",
    );
    const alias = "Legacy Model";
    const aliasKey = await modelAliasKey("alice", alias);
    const aliasPath = `views/model-aliases/${aliasKey.slice(4, 6)}/${aliasKey}.json`;
    const aliasEvent: StateEvent = {
      schema_version: 1,
      event_id: MODEL_ALIAS_ID,
      event_type: "model_identity.alias_assigned",
      occurred_at: "2025-08-15T03:36:35.586Z",
      subject_id: MODEL_ID,
      causation_event_id: MODEL_DECISION_ID,
      actor: { kind: "github", login: "alice" },
      payload: { alias },
    };
    const source = {
      ...APPROVED_MODEL_VIEW,
      status: "consolidated",
      mutation_event_id: consolidationId,
      consolidated_into: targetModelId,
      resolved_model_id: targetModelId,
    } as const;
    const consolidationEvent: StateEvent = {
      schema_version: 1,
      event_id: consolidationId,
      event_type: "model_identity.consolidated",
      occurred_at: "2025-08-15T03:36:35.588Z",
      subject_id: MODEL_ID,
      causation_event_id: MODEL_ALIAS_ID,
      actor: { kind: "github", login: "alice" },
      payload: { target_model_id: targetModelId },
    };
    const terminalImpact = modelImpact(target.view, [{
      kind: "identity",
      model_id: MODEL_ID,
      mutation_event_id: consolidationId,
      view_path: modelViewPath(),
    }, {
      kind: "alias",
      alias_key: aliasKey,
      assignment_event_id: MODEL_ALIAS_ID,
      model_id: MODEL_ID,
      view_path: aliasPath,
    }]);
    const replay = modelWriterFetcher({
      [eventPath(consolidationId)]: consolidationEvent,
      [eventPath(MODEL_ALIAS_ID)]: aliasEvent,
      [eventPath(targetRequestId)]: target.request,
      [eventPath(targetDecisionId)]: target.decision,
      [modelViewPath()]: source,
      [modelViewPath(targetModelId)]: target.view,
      [aliasPath]: {
        schema_version: 1,
        alias_key: aliasKey,
        owner_login: "alice",
        alias,
        model_id: MODEL_ID,
        assignment_event_id: MODEL_ALIAS_ID,
        assigned_at: aliasEvent.occurred_at,
        resolved_model_id: targetModelId,
      },
      [modelImpactPath(targetModelId)]: terminalImpact,
    });
    await expect(repository(replay.fetcher).consolidateModelIdentity({
      eventId: consolidationId,
      occurredAt: consolidationEvent.occurred_at,
      modelId: MODEL_ID,
      targetModelId,
      ownerLogin: "alice",
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(replay.treeBodies).toHaveLength(0);
  });

  it("rejects corrupt target members and terminal mutation events before consolidation", async () => {
    const targetRequestId = "0198abcd-0010-7000-8000-000000000030";
    const targetModelId = await modelIdentityId(targetRequestId);
    const targetDecisionId = "0198abcd-0011-7000-8000-000000000031";
    const target = approvedModelFixture(
      targetModelId,
      targetRequestId,
      targetDecisionId,
      "Target Model",
    );
    const targetImpact = modelImpact(target.view);
    const request = {
      eventId: "0198abcd-0004-7000-8000-000000000024",
      occurredAt: "2025-08-15T03:36:35.588Z",
      modelId: MODEL_ID,
      targetModelId,
      ownerLogin: "alice",
    } as const;
    const common = {
      [modelImpactPath()]: modelImpact(),
      [modelImpactPath(targetModelId)]: targetImpact,
      [modelViewPath()]: APPROVED_MODEL_VIEW,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [eventPath(targetRequestId)]: target.request,
    };

    const corruptMember = modelWriterFetcher({
      ...common,
      [modelViewPath(targetModelId)]: { ...target.view, owner_login: "mallory" },
      [eventPath(targetDecisionId)]: target.decision,
    });
    await expect(repository(corruptMember.fetcher).consolidateModelIdentity(request))
      .rejects.toBeInstanceOf(StateEventConflictError);
    expect(corruptMember.treeBodies).toHaveLength(0);

    clearResultOwnerContractProofCacheForTest();
    const corruptMutation = modelWriterFetcher({
      ...common,
      [modelViewPath(targetModelId)]: target.view,
      [eventPath(targetDecisionId)]: {
        ...target.decision,
        payload: { reviewer_login: "different-reviewer" },
      },
    });
    await expect(repository(corruptMutation.fetcher).consolidateModelIdentity(request))
      .rejects.toBeInstanceOf(StateEventConflictError);
    expect(corruptMutation.treeBodies).toHaveLength(0);

    clearResultOwnerContractProofCacheForTest();
    const corruptApprovalCause = modelWriterFetcher({
      ...common,
      [modelViewPath(targetModelId)]: target.view,
      [eventPath(targetDecisionId)]: {
        ...target.decision,
        causation_event_id: MODEL_REQUEST_ID,
      },
    });
    await expect(repository(corruptApprovalCause.fetcher).consolidateModelIdentity(request))
      .rejects.toBeInstanceOf(StateEventConflictError);
    expect(corruptApprovalCause.treeBodies).toHaveLength(0);
  });

  it("rejects consolidation creation when a terminal predecessor is later in append authority", async () => {
    const targetRequestId = "0198abcd-0012-7000-8000-000000000032";
    const targetModelId = await modelIdentityId(targetRequestId);
    const targetDecisionId = "0198abcd-0011-7000-8000-000000000031";
    const target = approvedModelFixture(
      targetModelId,
      targetRequestId,
      targetDecisionId,
      "Target Model",
    );
    const invalid = modelWriterFetcher({
      [modelImpactPath()]: modelImpact(),
      [modelImpactPath(targetModelId)]: modelImpact(target.view),
      [modelViewPath()]: APPROVED_MODEL_VIEW,
      [modelViewPath(targetModelId)]: target.view,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [eventPath(targetRequestId)]: target.request,
      [eventPath(targetDecisionId)]: target.decision,
    });
    await expect(repository(invalid.fetcher).consolidateModelIdentity({
      eventId: "0198abcd-0004-7000-8000-000000000024",
      occurredAt: "2025-08-15T03:36:35.588Z",
      modelId: MODEL_ID,
      targetModelId,
      ownerLogin: "alice",
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(invalid.treeBodies).toHaveLength(0);
  });

  it("rejects alias and rename terminal events that skip their current predecessor", async () => {
    const targetRequestId = "0198abcd-0010-7000-8000-000000000030";
    const targetModelId = await modelIdentityId(targetRequestId);
    const targetDecisionId = "0198abcd-0011-7000-8000-000000000031";
    const target = approvedModelFixture(
      targetModelId,
      targetRequestId,
      targetDecisionId,
      "Target Model",
    );
    const request = {
      eventId: "0198abcd-0004-7000-8000-000000000024",
      occurredAt: "2025-08-15T03:36:35.588Z",
      modelId: MODEL_ID,
      targetModelId,
      ownerLogin: "alice",
    } as const;
    const common = {
      [modelImpactPath()]: modelImpact(),
      [modelViewPath()]: APPROVED_MODEL_VIEW,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [eventPath(targetRequestId)]: target.request,
    };

    const targetAliasId = "0198abcd-0012-7000-8000-000000000032";
    const targetAlias = "Target Alias";
    const targetAliasKey = await modelAliasKey("alice", targetAlias);
    const targetAliasPath = `views/model-aliases/${targetAliasKey.slice(4, 6)}/${targetAliasKey}.json`;
    const targetAliasView = {
      schema_version: 1,
      alias_key: targetAliasKey,
      owner_login: "alice",
      alias: targetAlias,
      model_id: targetModelId,
      assignment_event_id: targetAliasId,
      assigned_at: "2025-08-15T03:36:35.576Z",
      resolved_model_id: targetModelId,
    } as const;
    const aliasedTarget = { ...target.view, mutation_event_id: targetAliasId } as const;
    const badAliasCause = modelWriterFetcher({
      ...common,
      [modelImpactPath(targetModelId)]: modelImpact(aliasedTarget, [{
        kind: "alias",
        alias_key: targetAliasKey,
        assignment_event_id: targetAliasId,
        model_id: targetModelId,
        view_path: targetAliasPath,
      }]),
      [modelViewPath(targetModelId)]: aliasedTarget,
      [targetAliasPath]: targetAliasView,
      [eventPath(targetAliasId)]: {
        schema_version: 1,
        event_id: targetAliasId,
        event_type: "model_identity.alias_assigned",
        occurred_at: targetAliasView.assigned_at,
        subject_id: targetModelId,
        causation_event_id: targetRequestId,
        actor: { kind: "github", login: "alice" },
        payload: { alias: targetAlias },
      },
    });
    await expect(repository(badAliasCause.fetcher).consolidateModelIdentity(request))
      .rejects.toBeInstanceOf(StateEventConflictError);
    expect(badAliasCause.treeBodies).toHaveLength(0);

    clearResultOwnerContractProofCacheForTest();
    const targetRenameId = "0198abcd-0013-7000-8000-000000000033";
    const renamedTarget = {
      ...target.view,
      display_name: "Renamed Target",
      mutation_event_id: targetRenameId,
    } as const;
    const badRenameCause = modelWriterFetcher({
      ...common,
      [modelImpactPath(targetModelId)]: modelImpact(renamedTarget),
      [modelViewPath(targetModelId)]: renamedTarget,
      [eventPath(targetRenameId)]: {
        schema_version: 1,
        event_id: targetRenameId,
        event_type: "model_identity.renamed",
        occurred_at: "2025-08-15T03:36:35.577Z",
        subject_id: targetModelId,
        causation_event_id: targetRequestId,
        actor: { kind: "github", login: "alice" },
        payload: { display_name: renamedTarget.display_name },
      },
    });
    await expect(repository(badRenameCause.fetcher).consolidateModelIdentity(request))
      .rejects.toBeInstanceOf(StateEventConflictError);
    expect(badRenameCause.treeBodies).toHaveLength(0);
  });

  it("fails consolidation closed before reads or writes when the component union exceeds 32", async () => {
    const targetRequestId = "0198abcd-0010-7000-8000-000000000030";
    const targetModelId = await modelIdentityId(targetRequestId);
    const aliases = Array.from({ length: 30 }, (_, index) => {
      const aliasKey = `ma1_${index.toString(16).padStart(64, "0")}`;
      return {
        kind: "alias" as const,
        alias_key: aliasKey,
        assignment_event_id: MODEL_ALIAS_ID,
        model_id: MODEL_ID,
        view_path: `views/model-aliases/${aliasKey.slice(4, 6)}/${aliasKey}.json`,
      };
    });
    const sourceImpact = modelImpact(APPROVED_MODEL_VIEW, aliases);
    const targetAliasKey = `ma1_${"f".repeat(64)}`;
    const targetMembers = [{
      kind: "alias",
      alias_key: targetAliasKey,
      assignment_event_id: MODEL_ALIAS_ID,
      model_id: targetModelId,
      view_path: `views/model-aliases/${targetAliasKey.slice(4, 6)}/${targetAliasKey}.json`,
    }, {
      kind: "identity",
      model_id: targetModelId,
      mutation_event_id: "0198abcd-0011-7000-8000-000000000031",
      view_path: modelViewPath(targetModelId),
    }].sort((left, right) => left.view_path.localeCompare(right.view_path));
    const targetImpact = {
      schema_version: 1,
      terminal_model_id: targetModelId,
      owner_login: "alice",
      terminal_mutation_event_id: "0198abcd-0011-7000-8000-000000000031",
      member_count: 2,
      maximum_member_count: 32,
      members: targetMembers,
    };
    const collision = modelWriterFetcher({
      [modelImpactPath()]: sourceImpact,
      [modelImpactPath(targetModelId)]: targetImpact,
    });
    await expect(repository(collision.fetcher).consolidateModelIdentity({
      eventId: "0198abcd-0004-7000-8000-000000000024",
      occurredAt: "2025-08-15T03:36:35.588Z",
      modelId: MODEL_ID,
      targetModelId,
      ownerLogin: "alice",
    })).rejects.toMatchObject({ status: 409 });
    expect(collision.treeBodies).toHaveLength(0);
    const documentReads = collision.fetcher.mock.calls.filter(([input]) =>
      fetchUrl(input).includes("/contents/"));
    expect(documentReads).toHaveLength(3);
  });

  it("rejects a symlink-shaped reverse-impact document before any State write", async () => {
    const targetRequestId = "0198abcd-0010-7000-8000-000000000030";
    const targetModelId = await modelIdentityId(targetRequestId);
    const targetDecisionId = "0198abcd-0011-7000-8000-000000000031";
    const targetImpact = {
      schema_version: 1,
      terminal_model_id: targetModelId,
      owner_login: "alice",
      terminal_mutation_event_id: targetDecisionId,
      member_count: 1,
      maximum_member_count: 32,
      members: [{
        kind: "identity",
        model_id: targetModelId,
        mutation_event_id: targetDecisionId,
        view_path: modelViewPath(targetModelId),
      }],
    } as const;
    const malformed = modelWriterFetcher({
      [modelImpactPath()]: json({
        type: "symlink",
        path: modelImpactPath(),
        target: "../../schema/model-identity-view-v1.schema.json",
      }),
      [modelImpactPath(targetModelId)]: targetImpact,
    });
    await expect(repository(malformed.fetcher).consolidateModelIdentity({
      eventId: "0198abcd-0004-7000-8000-000000000024",
      occurredAt: "2025-08-15T03:36:35.588Z",
      modelId: MODEL_ID,
      targetModelId,
      ownerLogin: "alice",
    })).rejects.toMatchObject({ status: 502 });
    expect(malformed.treeBodies).toHaveLength(0);
  });

  it.each(["missing-type", "missing-path", "wrong-path"] as const)(
    "rejects a contents response with %s metadata before any State write",
    async (metadata) => {
      const targetRequestId = "0198abcd-0010-7000-8000-000000000030";
      const targetModelId = await modelIdentityId(targetRequestId);
      const targetDecisionId = "0198abcd-0011-7000-8000-000000000031";
      const target = approvedModelFixture(
        targetModelId,
        targetRequestId,
        targetDecisionId,
        "Target Model",
      );
      const sourceImpactPath = modelImpactPath();
      const malformed = modelWriterFetcher({
        [sourceImpactPath]: contentsResponse(modelImpact(), sourceImpactPath, metadata),
        [modelImpactPath(targetModelId)]: modelImpact(target.view),
      });
      await expect(repository(malformed.fetcher).consolidateModelIdentity({
        eventId: "0198abcd-0004-7000-8000-000000000024",
        occurredAt: "2025-08-15T03:36:35.588Z",
        modelId: MODEL_ID,
        targetModelId,
        ownerLogin: "alice",
      })).rejects.toMatchObject({ status: 502 });
      expect(malformed.treeBodies).toHaveLength(0);
      expect(malformed.fetcher.mock.calls.some(([input]) =>
        fetchUrl(input).includes(`/contents/${modelViewPath()}`))).toBe(false);
    },
  );

  it("restarts the bounded consolidation transaction after eight hostile CAS collisions", async () => {
    const targetRequestId = "0198abcd-0010-7000-8000-000000000030";
    const targetModelId = await modelIdentityId(targetRequestId);
    const targetDecisionId = "0198abcd-0011-7000-8000-000000000031";
    const target = approvedModelFixture(
      targetModelId,
      targetRequestId,
      targetDecisionId,
      "Target Model",
    );
    const targetImpact = {
      schema_version: 1,
      terminal_model_id: targetModelId,
      owner_login: "alice",
      terminal_mutation_event_id: targetDecisionId,
      member_count: 1,
      maximum_member_count: 32,
      members: [{
        kind: "identity",
        model_id: targetModelId,
        mutation_event_id: targetDecisionId,
        view_path: modelViewPath(targetModelId),
      }],
    } as const;
    const collision = modelWriterFetcher({
      [modelImpactPath()]: modelImpact(),
      [modelImpactPath(targetModelId)]: targetImpact,
      [modelViewPath()]: APPROVED_MODEL_VIEW,
      [modelViewPath(targetModelId)]: target.view,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [eventPath(targetRequestId)]: target.request,
      [eventPath(targetDecisionId)]: target.decision,
    }, 409);
    vi.useFakeTimers();
    try {
      const outcome = repository(collision.fetcher).consolidateModelIdentity({
        eventId: "0198abcd-0004-7000-8000-000000000024",
        occurredAt: "2025-08-15T03:36:35.588Z",
        modelId: MODEL_ID,
        targetModelId,
        ownerLogin: "alice",
      });
      const rejected = expect(outcome).rejects.toMatchObject({ status: 409 });
      await vi.runAllTimersAsync();
      await rejected;
    } finally {
      vi.useRealTimers();
    }
    const referenceWrites = collision.fetcher.mock.calls.filter(([, init]) =>
      init?.method === "PATCH");
    expect(referenceWrites).toHaveLength(8);
    const protectedBranchReads = collision.fetcher.mock.calls.filter(([input]) =>
      fetchUrl(input).endsWith("/branches/main"));
    expect(protectedBranchReads).toHaveLength(8);
    expect(collision.fetcher.mock.calls.length).toBeLessThanOrEqual(
      MODEL_IDENTITY_WRITE_MAX_SUBREQUESTS,
    );
  });

  it("fails owner drift and permanent alias collision closed before writing", async () => {
    const alias = "Legacy Model";
    const aliasKey = await modelAliasKey("alice", alias);
    const aliasPath = `views/model-aliases/${aliasKey.slice(4, 6)}/${aliasKey}.json`;
    const ownerDrift = modelWriterFetcher({
      [modelViewPath()]: APPROVED_MODEL_VIEW,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [modelImpactPath()]: modelImpact(),
    });
    await expect(repository(ownerDrift.fetcher).assignModelAlias({
      eventId: MODEL_ALIAS_ID,
      occurredAt: "2025-08-15T03:36:35.586Z",
      modelId: MODEL_ID,
      ownerLogin: "bob",
      alias,
    })).rejects.toMatchObject({ status: 404 });
    expect(ownerDrift.treeBodies).toHaveLength(0);

    clearResultOwnerContractProofCacheForTest();
    const collision = modelWriterFetcher({
      [modelViewPath()]: APPROVED_MODEL_VIEW,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [modelImpactPath()]: modelImpact(),
      [aliasPath]: {
        schema_version: 1,
        alias_key: aliasKey,
        owner_login: "alice",
        alias,
        model_id: `mi1_${"f".repeat(64)}`,
        assignment_event_id: "0198abcd-0001-7000-8000-000000000099",
        assigned_at: "2025-08-15T03:36:35.585Z",
        resolved_model_id: `mi1_${"f".repeat(64)}`,
      },
    });
    await expect(repository(collision.fetcher).assignModelAlias({
      eventId: MODEL_ALIAS_ID,
      occurredAt: "2025-08-15T03:36:35.586Z",
      modelId: MODEL_ID,
      ownerLogin: "alice",
      alias,
    })).rejects.toMatchObject({ status: 409 });
    expect(collision.treeBodies).toHaveLength(0);
  });

  it("bounds repeated model rename collisions by the advertised ceiling", async () => {
    const collision = modelWriterFetcher({
      [modelViewPath()]: APPROVED_MODEL_VIEW,
      [eventPath(MODEL_REQUEST_ID)]: MODEL_REQUEST,
      [eventPath(MODEL_DECISION_ID)]: MODEL_DECISION,
      [modelImpactPath()]: modelImpact(),
    }, 409);
    vi.useFakeTimers();
    try {
      const outcome = repository(collision.fetcher).renameModelIdentity({
        eventId: MODEL_RENAME_ID,
        occurredAt: "2025-08-15T03:36:35.587Z",
        modelId: MODEL_ID,
        ownerLogin: "alice",
        displayName: "Model Beta",
      });
      const rejected = expect(outcome).rejects.toMatchObject({ status: 409 });
      await vi.runAllTimersAsync();
      await rejected;
    } finally {
      vi.useRealTimers();
    }
    const referenceWrites = collision.fetcher.mock.calls.filter(([, init]) => init?.method === "PATCH");
    expect(referenceWrites).toHaveLength(9);
    expect(collision.fetcher.mock.calls.length).toBeLessThanOrEqual(MODEL_IDENTITY_WRITE_MAX_SUBREQUESTS);
  });

  it("binds the dispatch CAS ceiling to nine maximal ambiguous collisions", async () => {
    const publication: StateEvent = {
      schema_version: 1,
      event_id: "0198abcd-1111-7000-8000-000000000008",
      event_type: "submission.publication_changed",
      occurred_at: "2026-08-20T06:07:12.000Z",
      subject_id: SUBMISSION_ID,
      causation_event_id: METADATA_ID,
      actor: { kind: "github", login: "alice" },
      payload: { publication_choice: "scheduled" },
    };
    const current: SubmissionView = {
      ...RESULT_VIEW,
      mutation_event_id: publication.event_id,
      publication_event_id: publication.event_id,
      dispatch: {
        ...RESULT_VIEW.dispatch,
        status: "failed",
        attempts: 0,
        last_error_code: "dispatch_provider_unavailable",
      },
    };
    const next: SubmissionView = {
      ...current,
      dispatch: {
        ...current.dispatch,
        status: "succeeded",
        attempts: 1,
        last_error_code: null,
      },
    };
    const responses: QueuedResponse[] = [];
    for (let attempt = 0; attempt < 9; attempt += 1) {
      responses.push(
        json({ object: { sha: HEAD } }),
        json({ tree: { sha: TREE } }),
        contents(current),
        contents(RECEIVED),
        contents(METADATA),
        contents(publication),
        contents(ARCHIVE_EVENT),
        contents(EVALUATION_ACCEPTED),
        contents(RESULT_EVENT),
        contents(EVALUATION_STARTED),
        json({ sha: NEW_TREE }, 201),
        json({ sha: NEW_COMMIT }, 201),
        json({ message: "temporary failure" }, 500),
        json({ message: "not a fast-forward" }, 409),
        json({ object: { sha: HEAD } }),
        json({ status: "diverged", merge_base_commit: { sha: HEAD } }),
      );
    }
    const fetcher = sequence(responses);
    vi.useFakeTimers();
    try {
      const outcome = repository(fetcher).updateDispatch(next, 0, null);
      const rejected = expect(outcome).rejects.toMatchObject({ status: 409 });
      await vi.runAllTimersAsync();
      await rejected;
    } finally {
      vi.useRealTimers();
    }
    expect(fetcher).toHaveBeenCalledTimes(DISPATCH_UPDATE_MAX_SUBREQUESTS);
  });

  it("preserves scheduled budget exhaustion through reference-update recovery", async () => {
    const current: SubmissionView = {
      ...VIEW,
      dispatch: {
        ...VIEW.dispatch,
        status: "failed",
        attempts: 0,
        last_error_code: "dispatch_provider_unavailable",
      },
    };
    const next: SubmissionView = {
      ...current,
      dispatch: {
        ...current.dispatch,
        status: "succeeded",
        attempts: 1,
        last_error_code: null,
      },
    };
    const upstream = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(current),
      contents(RECEIVED),
      contents(METADATA),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
    ]);
    const budget = new ScheduledSubrequestBudget(7);

    await expect(repository(budget.wrap(upstream)).updateDispatch(next, 0, null))
      .rejects.toBeInstanceOf(ScheduledSubrequestBudgetError);
    expect(upstream).toHaveBeenCalledTimes(7);
    expect(budget.remaining).toBe(0);
  });

  it("emits the exact Python-compatible canonical State document bytes", () => {
    expect(canonicalStateDocument({ z: "😀", a: { y: "β", x: true } })).toBe(
      "{\n  \"a\": {\n    \"x\": true,\n    \"y\": \"\\u03b2\"\n  },\n  \"z\": \"\\ud83d\\ude00\"\n}\n",
    );
    expect(() => canonicalStateDocument({ payload: { omitted: undefined } })).toThrow(
      /not JSON serializable/u,
    );
  });

  it("reads only one bounded rotating dispatch-outbox window", async () => {
    const outboxes = Array.from({ length: DISPATCH_OUTBOX_SCAN_LIMIT + 3 }, (_, index) =>
      outbox(index));
    const directory = "views/dispatch-outbox/01";
    const listed = [...outboxes].reverse().map((entry) => ({
      type: "file",
      path: `${directory}/${entry.submission_id}.json`,
    }));
    const selected = [...outboxes.slice(DISPATCH_OUTBOX_SCAN_LIMIT), ...outboxes.slice(0, DISPATCH_OUTBOX_SCAN_LIMIT - 3)];
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      json(listed),
      ...selected.map(contents),
    ]);

    await expect(repository(fetcher).listDispatchOutbox(
      "01",
      DISPATCH_OUTBOX_SCAN_LIMIT,
      DISPATCH_OUTBOX_SCAN_LIMIT,
    )).resolves.toEqual(selected);
    expect(fetcher).toHaveBeenCalledTimes(3 + DISPATCH_OUTBOX_SCAN_LIMIT);
    const selectedUrls = fetcher.mock.calls.slice(3).map(([input]) => fetchUrl(input));
    selected.forEach((entry, index) => {
      expect(selectedUrls[index]).toContain(`${directory}/${entry.submission_id}.json`);
    });
  });

  it("rejects an oversized dispatch-outbox scan before reading State", async () => {
    const fetcher = sequence([]);
    await expect(repository(fetcher).listDispatchOutbox(
      "01",
      0,
      DISPATCH_OUTBOX_SCAN_LIMIT + 1,
    )).rejects.toThrow(/scan limit/u);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("gates result-owner writes on ancestry and exact reviewed contract subtrees", async () => {
    const valid = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
    ]);
    await expect(repository(valid).assertResultOwnerContract()).resolves.toBe(HEAD);

    clearResultOwnerContractProofCacheForTest();
    const diverged = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      json({ status: "diverged", merge_base_commit: { sha: "0".repeat(40) } }),
    ]);
    await expect(repository(diverged).assertResultOwnerContract()).rejects.toMatchObject({ status: 503 });

    clearResultOwnerContractProofCacheForTest();
    const changed = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses("schema"),
    ]);
    await expect(repository(changed).assertResultOwnerContract()).rejects.toMatchObject({ status: 503 });

    const entries = Object.entries(RESULT_OWNER_CONTRACT_ROOT_ENTRIES).map(([path, entry]) => ({
      path,
      mode: entry.mode,
      type: entry.type,
      sha: entry.sha,
    }));
    const hostileTrees = [
      entries.filter((entry) => entry.path !== "docs"),
      [...entries, { ...entries[1] }],
      entries.map((entry) => entry.path === "docs"
        ? { ...entry, mode: "100644", type: "blob" }
        : entry),
    ];
    for (const tree of hostileTrees) {
      clearResultOwnerContractProofCacheForTest();
      const hostile = sequence([
        json({ object: { sha: HEAD } }),
        json({ tree: { sha: TREE } }),
        json({
          status: "ahead",
          merge_base_commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
        }),
        json({ sha: TREE, truncated: false, tree }),
      ]);
      await expect(repository(hostile).assertResultOwnerContract()).rejects.toMatchObject({
        status: 503,
      });
    }
  });

  it("reuses content-addressed contract proofs across repository instances", async () => {
    const first = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
    ]);
    await expect(repository(first).assertResultOwnerContract()).resolves.toBe(HEAD);

    const second = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
    ]);
    await expect(repository(second).assertResultOwnerContract()).resolves.toBe(HEAD);
    expect(first).toHaveBeenCalledTimes(4);
    expect(second).toHaveBeenCalledTimes(2);
  });

  it("binds the migrated staging contract and its exact root subtrees", async () => {
    const fetcher = sequence([
      json({ object: { sha: STAGING_RESULT_OWNER_CONTRACT_COMMIT } }),
      json({ tree: { sha: TREE } }),
      json({
        sha: TREE,
        truncated: false,
        tree: Object.entries(STAGING_RESULT_OWNER_CONTRACT_ROOT_ENTRIES).map(
          ([path, entry]) => ({ path, ...entry }),
        ),
      }),
    ]);
    await expect(stagingRepository(fetcher).assertResultOwnerContract()).resolves.toBe(
      STAGING_RESULT_OWNER_CONTRACT_COMMIT,
    );
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("bounds the cross-request contract-proof cache and evicts the oldest key", async () => {
    const heads: string[] = [];
    for (let index = 0; index < 65; index += 1) {
      const head = index.toString(16).padStart(40, "0");
      heads.push(head);
      const fetcher = sequence([
        json({ object: { sha: head } }),
        json({ tree: { sha: TREE } }),
        json({ status: "ahead", merge_base_commit: { sha: RESULT_OWNER_CONTRACT_COMMIT } }),
        resultOwnerContractRootTreeResponse(),
      ]);
      await repository(fetcher).assertResultOwnerContract();
      expect(fetcher).toHaveBeenCalledTimes(4);
    }
    const evicted = sequence([
      json({ object: { sha: heads[0] } }),
      json({ tree: { sha: TREE } }),
      json({ status: "ahead", merge_base_commit: { sha: RESULT_OWNER_CONTRACT_COMMIT } }),
      resultOwnerContractRootTreeResponse(),
    ]);
    await repository(evicted).assertResultOwnerContract();
    expect(evicted).toHaveBeenCalledTimes(4);
  });

  it("proves write authority with a non-forced same-commit ref update", async () => {
    const fetcher = sequence([
      json({ permissions: { push: true } }),
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      json({ ref: "refs/heads/main", object: { sha: HEAD } }),
    ]);
    await expect(repository(fetcher).assertWritable()).resolves.toBe(HEAD);
    const [, init] = fetcher.mock.calls[3] ?? [];
    expect(init?.method).toBe("PATCH");
    if (typeof init?.body !== "string") throw new TypeError("write probe body must be JSON");
    expect(JSON.parse(init.body)).toEqual({ force: false, sha: HEAD });
  });

  it("binds production write authority to protected main and exact contract subtrees", async () => {
    const fetcher = sequence([
      json({ permissions: { push: true } }),
      json({ object: { sha: RESULT_OWNER_CONTRACT_COMMIT } }),
      json({ tree: { sha: TREE } }),
      json({
        name: "main",
        protected: true,
        commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
      resultOwnerContractRootTreeResponse(),
      json({
        ref: "refs/heads/main",
        object: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
      json({
        name: "main",
        protected: true,
        commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
    ]);
    await expect(
      productionRepository(fetcher).assertProductionQualifiedWritable(),
    ).resolves.toBe(RESULT_OWNER_CONTRACT_COMMIT);
    const [, init] = fetcher.mock.calls.find(([, request]) => request?.method === "PATCH") ?? [];
    expect(init?.method).toBe("PATCH");
  });

  it("rejects protection or head drift after the production write probe", async () => {
    const fetcher = sequence([
      json({ permissions: { push: true } }),
      json({ object: { sha: RESULT_OWNER_CONTRACT_COMMIT } }),
      json({ tree: { sha: TREE } }),
      json({
        name: "main",
        protected: true,
        commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
      resultOwnerContractRootTreeResponse(),
      json({
        ref: "refs/heads/main",
        object: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
      json({
        name: "main",
        protected: false,
        commit: { sha: "f".repeat(40) },
      }),
    ]);
    await expect(
      productionRepository(fetcher).assertProductionQualifiedWritable(),
    ).rejects.toMatchObject({ status: 503 });
    expect(fetcher.mock.calls.some(([, request]) => request?.method === "PATCH")).toBe(true);
  });

  it("refuses unprotected, stale, or wrong-repository production proofs", async () => {
    const unprotected = sequence([
      json({ permissions: { push: true } }),
      json({ object: { sha: RESULT_OWNER_CONTRACT_COMMIT } }),
      json({ tree: { sha: TREE } }),
      json({
        name: "main",
        protected: false,
        commit: { sha: RESULT_OWNER_CONTRACT_COMMIT },
      }),
      resultOwnerContractRootTreeResponse(),
    ]);
    await expect(
      productionRepository(unprotected).assertProductionQualifiedWritable(),
    ).rejects.toMatchObject({ status: 503 });

    const stale = sequence([
      json({ permissions: { push: true } }),
      json({ object: { sha: RESULT_OWNER_CONTRACT_COMMIT } }),
      json({ tree: { sha: TREE } }),
      json({
        name: "main",
        protected: true,
        commit: { sha: "f".repeat(40) },
      }),
      resultOwnerContractRootTreeResponse(),
    ]);
    await expect(
      productionRepository(stale).assertProductionQualifiedWritable(),
    ).rejects.toMatchObject({ status: 503 });

    await expect(
      stagingRepository(sequence([])).assertProductionQualifiedWritable(),
    ).rejects.toMatchObject({ status: 503 });
  });

  it("proves real CAS contention before appending durable canary evidence through the retrying writer", async () => {
    const winnerCommit = "a".repeat(40);
    const contenderTree = "9".repeat(40);
    const contenderCommit = "b".repeat(40);
    const evidenceTree = "c".repeat(40);
    const evidenceCommit = "d".repeat(40);
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: winnerCommit }, 201),
      json({ sha: contenderTree }, 201),
      json({ sha: contenderCommit }, 201),
      json({ ref: "refs/heads/main", object: { sha: winnerCommit } }),
      json({ message: "not a fast forward" }, 422),
      json({ object: { sha: winnerCommit } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: evidenceTree }, 201),
      json({ sha: evidenceCommit }, 201),
      json({ object: { sha: evidenceCommit } }),
    ]);
    await expect(repository(fetcher).provePromotionCanaryContention(CANARY_EVIDENCE))
      .resolves.toEqual({
        proofRecorded: true,
        idempotent: false,
        commit: evidenceCommit,
        created: true,
      });
    const winnerUpdate = fetcher.mock.calls[6]?.[1];
    const contenderUpdate = fetcher.mock.calls[7]?.[1];
    const retryUpdate = fetcher.mock.calls[13]?.[1];
    expect(winnerUpdate?.method).toBe("PATCH");
    expect(contenderUpdate?.method).toBe("PATCH");
    expect(retryUpdate?.method).toBe("PATCH");
    if (
      typeof winnerUpdate?.body !== "string" ||
      typeof contenderUpdate?.body !== "string" ||
      typeof retryUpdate?.body !== "string"
    ) {
      throw new TypeError("canary ref update bodies must be JSON text");
    }
    expect(JSON.parse(winnerUpdate.body)).toEqual({ force: false, sha: winnerCommit });
    expect(JSON.parse(contenderUpdate.body)).toEqual({
      force: false,
      sha: contenderCommit,
    });
    expect(JSON.parse(retryUpdate.body)).toEqual({
      force: false,
      sha: evidenceCommit,
    });
    const winnerRequestBody = fetcher.mock.calls[3]?.[1]?.body;
    const contenderRequestBody = fetcher.mock.calls[5]?.[1]?.body;
    if (typeof winnerRequestBody !== "string") {
      throw new TypeError("canary winner commit body must be JSON text");
    }
    expect(JSON.parse(winnerRequestBody)).toEqual({
      message: `Promotion canary CAS winner ${CANARY_EVIDENCE.event_id}`,
      parents: [HEAD],
      tree: TREE,
    });
    const callUrls = fetcher.mock.calls.map((call) => {
      const input = call[0];
      return typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
    });
    expect(callUrls[3]).toMatch(/\/git\/commits$/u);
    expect(callUrls.filter((url) => url.endsWith("/git/trees"))).toHaveLength(2);
    if (typeof contenderRequestBody !== "string") {
      throw new TypeError("canary contender commit body must be JSON text");
    }
    const contenderCommitBody = JSON.parse(contenderRequestBody) as {
      message: string;
      parents: string[];
      tree: string;
    };
    expect(contenderCommitBody).toEqual({
      message: `Promotion canary CAS contender ${CANARY_EVIDENCE.event_id}`,
      parents: [HEAD],
      tree: contenderTree,
    });
  });

  it("reuses exact immutable contention evidence without creating another contender", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(CANARY_EVIDENCE),
    ]);
    await expect(repository(fetcher).provePromotionCanaryContention(CANARY_EVIDENCE))
      .resolves.toEqual({
        proofRecorded: true,
        idempotent: true,
        commit: HEAD,
        created: false,
      });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("target-reads one submission view and only its referenced immutable events", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(VIEW),
      contents(RECEIVED),
      contents(METADATA),
    ]);
    await expect(repository(fetcher).readSubmission(SUBMISSION_ID)).resolves.toEqual(VIEW);
    const urls = fetcher.mock.calls.map((call) => {
      const input = call[0];
      return typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    });
    expect(urls).toHaveLength(5);
    expect(urls.some((url) => url.includes("recursive=1") || url.includes("/git/blobs/"))).toBe(false);
    expect(urls.filter((url) => url.includes("/contents/events/"))).toHaveLength(2);
  });

  it("rejects an owner submission mutation that predates its causal head", async () => {
    const event: WritableStateEvent = {
      schema_version: 1,
      event_id: "0198abcd-0000-7000-8000-000000000001",
      event_type: "submission.metadata_amended",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: SUBMISSION_ID,
      causation_event_id: METADATA_ID,
      actor: { kind: "github", login: "alice" },
      payload: { production_metadata: { web_access: true } },
    };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(VIEW),
      contents(RECEIVED),
      contents(METADATA),
    ]);
    await expect(repository(fetcher).appendSubmissionMutation(
      event,
      METADATA_ID,
      {
        ...VIEW,
        mutation_event_id: event.event_id,
        metadata_event_id: event.event_id,
        production_metadata: { web_access: true },
        submission: {
          ...VIEW.submission,
          production_metadata: { web_access: true },
        },
      },
    )).rejects.toBeInstanceOf(StateEventConflictError);
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("fails closed when a targeted view disagrees with a referenced event", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents({ ...VIEW, owner_login: "mallory" }),
      contents(RECEIVED),
      contents(METADATA),
    ]);
    await expect(repository(fetcher).readSubmission(SUBMISSION_ID)).rejects.toMatchObject({ status: 502 });
  });

  it("target-reads and authenticates lifecycle-aware summaries", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(VIEW_V2),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
    ]);
    await expect(repository(fetcher).readSubmission(SUBMISSION_ID)).resolves.toEqual(VIEW_V2);

    const tampered = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents({ ...VIEW_V2, archive: { ...VIEW_V2.archive, archive_commit: "e".repeat(40) } }),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
    ]);
    await expect(repository(tampered).readSubmission(SUBMISSION_ID)).rejects.toMatchObject({ status: 502 });
  });

  it("publishes a create-only event with a non-forced ref update", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);

    await expect(repository(fetcher).appendEvent(EVENT)).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      path: `events/01/${EVENT.event_id}.json`,
    });

    const calls = fetcher.mock.calls;
    expect(calls).toHaveLength(6);
    const treeRequestBody = calls[3]?.[1]?.body;
    if (typeof treeRequestBody !== "string") throw new TypeError("tree body was not text");
    const treeBody = JSON.parse(treeRequestBody) as {
      tree: { path: string; content: string }[];
    };
    expect(treeBody.tree[0]?.path).toContain(EVENT.event_id);
    expect(JSON.parse(treeBody.tree[0]?.content ?? "null")).toEqual(EVENT);
    const updateRequestBody = calls[5]?.[1]?.body;
    if (typeof updateRequestBody !== "string") throw new TypeError("update body was not text");
    expect(JSON.parse(updateRequestBody)).toEqual({ sha: NEW_COMMIT, force: false });
  });

  it("publishes a bound event only at the exact expected State head", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).appendEventAtHead(EVENT, HEAD)).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      path: `events/01/${EVENT.event_id}.json`,
    });
  });

  it("rejects a bound event when State moved before its first append", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
    ]);
    await expect(repository(fetcher).appendEventAtHead(EVENT, "9".repeat(40)))
      .rejects.toBeInstanceOf(GitHubStateError);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("recovers a bound append after a collision committed the exact event", async () => {
    const movedHead = "5".repeat(40);
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ message: "conflict" }, 409),
      json({ object: { sha: movedHead } }),
      json({ tree: { sha: "6".repeat(40) } }),
      contents(EVENT),
    ]);
    await expect(repository(fetcher).appendEventAtHead(EVENT, HEAD)).resolves.toEqual({
      commit: movedHead,
      created: false,
      path: `events/01/${EVENT.event_id}.json`,
    });
  });

  it("atomically appends lifecycle events with the matching lifecycle-aware submission view", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(VIEW),
      contents(RECEIVED),
      contents(METADATA),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).appendSubmissionLifecycle(
      [ARCHIVE_EVENT],
      SUBMISSION_ID,
      VIEW_V2,
    )).resolves.toEqual({ commit: NEW_COMMIT, created: true, view: VIEW_V2 });
    const treeRequest = fetcher.mock.calls[6]?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${ARCHIVE_EVENT.event_id}.json`,
      `views/submissions/01/${SUBMISSION_ID}.json`,
    ]);
  });

  it("atomically records a result, release schedule, and submission view", async () => {
    const reservation = await effectiveResultIdentityReservation({
      ownerLogin: "alice",
      declaredModel: "Example Model",
      problemId: "two_plus_two",
      statementRevision: 2,
      resultId: RESULT_ID,
      reservationEventId: RESULT_EVENT.event_id,
      reservationKind: "result_authority",
    });
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(ACCEPTED_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(EVALUATION_STARTED),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).resolves.toEqual({ commit: NEW_COMMIT, created: true, view: RESULT_VIEW });
    const treeRequest = fetcher.mock.calls.find(([, init]) =>
      init?.method === "POST" && (typeof init.body === "string") && init.body.includes("base_tree"))?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${RESULT_EVENT.event_id}.json`,
      `events/01/${RELEASE_EVENT.event_id}.json`,
      `views/submissions/01/${SUBMISSION_ID}.json`,
      `views/result-identities/aa/${RESULT_ID}.json`,
      `views/result-amendments/aa/${RESULT_ID}.json`,
      `views/result-release-status/aa/${RESULT_ID}.json`,
      effectiveResultIdentityPath(reservation.effective_result_identity_id),
    ]);
    expect(JSON.parse(tree.tree[4]?.content ?? "null")).toEqual(RESULT_AMENDMENT_VIEW);
    expect(JSON.parse(tree.tree[5]?.content ?? "null")).toEqual(RESULT_RELEASE_STATUS_VIEW);
    expect(fetcher.mock.calls.map(([input]) => input instanceof Request
      ? input.url
      : typeof input === "string"
        ? input
        : input.toString()).join("\n")).toContain(
          `/compare/${RESULT_OWNER_CONTRACT_COMMIT}...${HEAD}`,
        );
    expect(fetcher.mock.calls.every(([, init]) => init?.redirect === "manual")).toBe(true);
  });

  it("records an open-conjecture result with an exact not-scheduled release status", async () => {
    const reservation = await effectiveResultIdentityReservation({
      ownerLogin: "alice",
      declaredModel: "Example Model",
      problemId: "two_plus_two",
      statementRevision: 2,
      resultId: RESULT_ID,
      reservationEventId: RESULT_EVENT.event_id,
      reservationKind: "result_authority",
    });
    const accepted = {
      ...ACCEPTED_VIEW,
      submission: {
        ...ACCEPTED_VIEW.submission,
        problem_group: "open-conjectures" as const,
      },
    };
    const resultView = { ...accepted, result_id: RESULT_ID, result_event_id: RESULT_EVENT.event_id };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(accepted),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(EVALUATION_STARTED),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).recordAcceptedResult(
      [RESULT_EVENT],
      EVALUATION_ACCEPTED.event_id,
      resultView,
    )).resolves.toEqual({ commit: NEW_COMMIT, created: true, view: resultView });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${RESULT_EVENT.event_id}.json`,
      `views/submissions/01/${SUBMISSION_ID}.json`,
      `views/result-identities/aa/${RESULT_ID}.json`,
      `views/result-amendments/aa/${RESULT_ID}.json`,
      `views/result-release-status/aa/${RESULT_ID}.json`,
      effectiveResultIdentityPath(reservation.effective_result_identity_id),
    ]);
    expect(JSON.parse(tree.tree[4]?.content ?? "null")).toEqual({
      schema_version: 2,
      result_id: RESULT_ID,
      authority_event_id: RESULT_EVENT.event_id,
      status: "not_scheduled",
      release_revision: 0,
      release_event_id: null,
      supersedes_release_event_id: null,
    });
  });

  it("keeps the first result-identity authority and reports claimed and recorded collisions distinctly", async () => {
    const prefix = () => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(ACCEPTED_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(EVALUATION_STARTED),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
    ];
    const claimed = sequence([
      ...prefix(),
      contents(claimedGuard(RESULT_ID, CLAIM_EVENT_ID)),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
    ]);
    await expect(repository(claimed).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).rejects.toMatchObject({
      name: "ResultIdentityCollisionError",
      existingKind: "claimed",
    } satisfies Partial<ResultIdentityCollisionError>);

    clearResultOwnerContractProofCacheForTest();
    const recorded = sequence([
      ...prefix(),
      contents(recordedGuard(
        RESULT_ID,
        "0198abcd-1111-7000-8000-000000000009",
      )),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
    ]);
    await expect(repository(recorded).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).rejects.toMatchObject({
      name: "ResultIdentityCollisionError",
      existingKind: "recorded",
    } satisfies Partial<ResultIdentityCollisionError>);
    expect([...claimed.mock.calls, ...recorded.mock.calls].some((call) =>
      call[1]?.method === "POST")).toBe(false);
  });

  it("accepts only an exact same-authority record replay and refuses a missing guard", async () => {
    const reservation = await effectiveResultIdentityReservation({
      ownerLogin: "alice",
      declaredModel: "Example Model",
      problemId: "two_plus_two",
      statementRevision: 2,
      resultId: RESULT_ID,
      reservationEventId: RESULT_EVENT.event_id,
      reservationKind: "result_authority",
    });
    const prefix = (
      guard: QueuedResponse,
      amendment: unknown = RESULT_AMENDMENT_VIEW,
      releaseStatus: unknown = RESULT_RELEASE_STATUS_VIEW,
    ) => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(RESULT_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(RESULT_EVENT),
      contents(EVALUATION_STARTED),
      contents(RESULT_EVENT),
      contents(RELEASE_EVENT),
      guard,
      contents(amendment),
      contents(releaseStatus),
      contents(reservation),
    ];
    const exact = sequence([
      ...prefix(contents(recordedGuard(RESULT_ID, RESULT_EVENT.event_id))),
    ]);
    await expect(repository(exact).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).resolves.toEqual({ commit: HEAD, created: false, view: RESULT_VIEW });

    clearResultOwnerContractProofCacheForTest();
    const laterEventId = "0198abcd-1111-7000-8000-000000000009";
    const evolved = sequence([
      ...prefix(
        contents(recordedGuard(RESULT_ID, RESULT_EVENT.event_id)),
        { ...RESULT_AMENDMENT_VIEW, mutation_event_id: laterEventId },
        {
          ...RESULT_RELEASE_STATUS_VIEW,
          status: "published",
          release_revision: 2,
          release_event_id: laterEventId,
          supersedes_release_event_id: RELEASE_EVENT.event_id,
        },
      ),
    ]);
    await expect(repository(evolved).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).resolves.toEqual({ commit: HEAD, created: false, view: RESULT_VIEW });

    clearResultOwnerContractProofCacheForTest();
    const missingGuard = sequence([
      ...prefix(new Response(null, { status: 404 })),
    ]);
    await expect(repository(missingGuard).recordAcceptedResult(
      [RESULT_EVENT, RELEASE_EVENT],
      EVALUATION_ACCEPTED.event_id,
      RESULT_VIEW,
    )).rejects.toBeInstanceOf(StateEventConflictError);
  });

  it("atomically claims a verified legacy record and all six private indexes", async () => {
    const reservation = await effectiveResultIdentityReservation({
      ownerLogin: LEGACY_RESULT.ownerLogin,
      declaredModel: LEGACY_RESULT.baseResult.declared_model,
      problemId: LEGACY_RESULT.baseResult.problem_id,
      statementRevision: LEGACY_RESULT.baseResult.statement_revision,
      resultId: LEGACY_RESULT.resultId,
      reservationEventId: CLAIM_EVENT_ID,
      reservationKind: "result_authority",
    });
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2025-08-15T03:36:44.322Z",
      verified: LEGACY_RESULT,
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      authorityEventId: CLAIM_EVENT_ID,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${CLAIM_EVENT_ID}.json`,
      `views/result-identities/11/${LEGACY_RESULT.resultId}.json`,
      `views/result-overlays/11/${LEGACY_RESULT.resultId}.json`,
      "views/result-source-records/34/src1_34ef08a904550548d360cc62407a77a7e5e8dfe9184c8d472e4f4266ffc3f826.json",
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
      `views/result-release-status/11/${LEGACY_RESULT.resultId}.json`,
      effectiveResultIdentityPath(reservation.effective_result_identity_id),
    ]);
    expect(tree.tree.every((entry) => entry.content.endsWith("\n"))).toBe(true);
    expect(tree.tree[1]?.content).toContain('"authority_event_id"');
    expect(JSON.parse(tree.tree[4]?.content ?? "null")).toEqual(LEGACY_AMENDMENT_VIEW);
    expect(JSON.parse(tree.tree[5]?.content ?? "null")).toEqual(LEGACY_RELEASE_STATUS_VIEW);
  });

  it("restarts the complete claim preflight after a stale-base CAS collision", async () => {
    const nextHead = "5".repeat(40);
    const nextTree = "6".repeat(40);
    const finalTree = "7".repeat(40);
    const finalCommit = "8".repeat(40);
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ message: "not a fast-forward" }, 409),
      json({ object: { sha: nextHead } }),
      json({ tree: { sha: nextTree } }),
      ...resultOwnerContractProofResponses(undefined, nextTree),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      json({ sha: finalTree }, 201),
      json({ sha: finalCommit }, 201),
      json({ object: { sha: finalCommit } }),
    ]);
    await expect(repository(fetcher).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2025-08-15T03:36:44.322Z",
      verified: LEGACY_RESULT,
    })).resolves.toMatchObject({ commit: finalCommit, created: true });
    const updates = fetcher.mock.calls.filter((call) => call[1]?.method === "PATCH");
    expect(updates).toHaveLength(2);
  });

  it("rejects a new legacy claim whose idempotency key is stale for its request clock", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
    ]);
    await expect(repository(fetcher).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2026-08-24T08:00:00.000Z",
      verified: LEGACY_RESULT,
    })).rejects.toMatchObject({
      status: 409,
      message: "Idempotency-Key does not match the current request clock",
    });
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("rejects a claim colliding with a recorded identity guard", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(recordedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
    ]);
    await expect(repository(fetcher).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2026-08-24T08:00:00.000Z",
      verified: LEGACY_RESULT,
    })).rejects.toMatchObject({
      name: "ResultIdentityCollisionError",
      existingKind: "recorded",
    } satisfies Partial<ResultIdentityCollisionError>);
    expect(fetcher).toHaveBeenCalledTimes(
      2 + 2 + 7,
    );
  });

  it("accepts a later exact same-key claim retry but rejects a forged source binding", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z");
    const source = await claimedSourceIndex(LEGACY_RESULT, CLAIM_EVENT_ID);
    const reservation = await legacyBaseReservation();
    const claimEvent = {
      schema_version: 1,
      event_id: CLAIM_EVENT_ID,
      event_type: "result.claimed",
      occurred_at: "2026-08-24T08:00:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: LEGACY_RESULT.baseResult,
    } as const;
    const retractionRequestId = "0198abcd-2222-7000-8000-000000000003";
    const retractionDecisionId = "0198abcd-2222-7000-8000-000000000004";
    const requested = requestedRetractionView(
      LEGACY_AMENDMENT_VIEW,
      retractionRequestId,
      "2026-08-24T08:01:00.000Z",
      "owner_requested_withdrawal",
    );
    const evolvedAmendment = decodeResultAmendmentView({
      ...requested,
      mutation_event_id: retractionDecisionId,
      retraction: {
        ...requested.retraction,
        status: "rejected",
        decision_event_id: retractionDecisionId,
        decided_at: "2026-08-24T08:02:00.000Z",
        reviewer_login: "maintainer",
        reason_code: "request_not_confirmed",
      },
    });
    const retractionRequestEvent: StateEvent = {
      schema_version: 1,
      event_id: retractionRequestId,
      event_type: "result.retraction_requested",
      occurred_at: "2026-08-24T08:01:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { retraction_revision: 1, reason_code: "owner_requested_withdrawal" },
    };
    const retractionDecisionEvent: StateEvent = {
      schema_version: 1,
      event_id: retractionDecisionId,
      event_type: "result.retraction_decided",
      occurred_at: "2026-08-24T08:02:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: retractionRequestId,
      actor: { kind: "system" },
      payload: {
        retraction_revision: 1,
        reviewer_login: "maintainer",
        decision: "reject",
        reason_code: "request_not_confirmed",
      },
    };
    const exact = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(overlay),
      contents(evolvedAmendment),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(source),
      contents(claimEvent),
      contents(reservation),
      contents(claimEvent),
      contents(retractionDecisionEvent),
      contents(retractionRequestEvent),
    ]);
    await expect(repository(exact).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2026-08-24T09:00:00.000Z",
      verified: LEGACY_RESULT,
    })).resolves.toMatchObject({ created: false, authorityEventId: CLAIM_EVENT_ID });

    clearResultOwnerContractProofCacheForTest();
    const otherEventId = "0198abcd-0000-7000-8000-000000000009";
    const occupied = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(overlay),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(source),
      contents({ ...claimEvent, event_id: otherEventId }),
      contents(reservation),
    ]);
    await expect(repository(occupied).claimLegacyResult({
      eventId: otherEventId,
      occurredAt: "2026-08-24T08:00:00.000Z",
      verified: LEGACY_RESULT,
    })).rejects.toBeInstanceOf(StateEventConflictError);

    clearResultOwnerContractProofCacheForTest();
    const forged = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(overlay),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents({ ...source, result_id: `r2_${"2".repeat(64)}` }),
      contents(claimEvent),
      contents(reservation),
      contents(claimEvent),
    ]);
    await expect(repository(forged).claimLegacyResult({
      eventId: CLAIM_EVENT_ID,
      occurredAt: "2026-08-24T08:00:00.000Z",
      verified: LEGACY_RESULT,
    })).rejects.toBeInstanceOf(StateEventConflictError);
  });

  it("keeps the first claim canonical when the same record is re-claimed at another reachable commit", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z");
    const source = await claimedSourceIndex(LEGACY_RESULT, CLAIM_EVENT_ID);
    const reservation = await legacyBaseReservation();
    const claimEvent = {
      schema_version: 1,
      event_id: CLAIM_EVENT_ID,
      event_type: "result.claimed",
      occurred_at: "2026-08-24T08:00:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: LEGACY_RESULT.baseResult,
    } as const;
    const newer = {
      ...LEGACY_RESULT,
      baseResult: { ...LEGACY_RESULT.baseResult, results_commit: "c".repeat(40) },
    };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(overlay),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      contents(reservation),
      contents(source),
      contents(claimEvent),
    ]);
    await expect(repository(fetcher).claimLegacyResult({
      eventId: "0198abcd-0000-7000-8000-000000000009",
      occurredAt: "2026-08-24T08:05:00.000Z",
      verified: newer,
    })).resolves.toEqual({
      commit: HEAD,
      created: false,
      resultId: LEGACY_RESULT.resultId,
      authorityEventId: CLAIM_EVENT_ID,
    });
    expect(fetcher.mock.calls.some((call) => call[1]?.method === "POST")).toBe(false);
  });

  it("serializes metadata backfills through the current overlay mutation head", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z");
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents(legacyClaimEvent()),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: BACKFILL_EVENT_ID,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${BACKFILL_EVENT_ID}.json`,
      `views/result-overlays/11/${LEGACY_RESULT.resultId}.json`,
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
    ]);
    expect(JSON.parse(tree.tree[0]?.content ?? "null")).toMatchObject({
      causation_event_id: CLAIM_EVENT_ID,
      payload: { production_metadata: { web_access: false } },
    });
    expect(JSON.parse(tree.tree[1]?.content ?? "null")).toMatchObject({
      mutation_event_id: BACKFILL_EVENT_ID,
      metadata: { web_access: { event_id: BACKFILL_EVENT_ID, provenance: "backfilled", value: false } },
    });
    expect(JSON.parse(tree.tree[2]?.content ?? "null")).toEqual({
      ...LEGACY_AMENDMENT_VIEW,
      mutation_event_id: BACKFILL_EVENT_ID,
    });
  });

  it("rejects a metadata backfill after the release starts", async () => {
    const releaseEventId = "0198abcd-2222-7000-8000-000000000006";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z")),
      contents({
        ...LEGACY_RELEASE_STATUS_VIEW,
        status: "running",
        release_revision: 1,
        release_event_id: releaseEventId,
        supersedes_release_event_id: null,
      }),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents({
        schema_version: 1,
        event_id: releaseEventId,
        event_type: "release.started",
        occurred_at: "2026-08-24T08:00:30.000Z",
        subject_id: LEGACY_RESULT.resultId,
        causation_event_id: CLAIM_EVENT_ID,
        actor: { kind: "system" },
        payload: { attempt: 1 },
      }),
      contents(legacyClaimEvent()),
    ]);
    await expect(repository(fetcher).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).rejects.toMatchObject({ status: 409 });
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("makes a same-key backfill replay idempotent and a changed body conflicting", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const claimed = claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z");
    const overlay = {
      ...claimed,
      mutation_event_id: BACKFILL_EVENT_ID,
      metadata: {
        web_access: {
          value: false,
          provenance: "backfilled",
          event_id: BACKFILL_EVENT_ID,
          recorded_at: "2026-08-24T08:01:00.000Z",
        },
      },
    } as const;
    const event = {
      schema_version: 1,
      event_id: BACKFILL_EVENT_ID,
      event_type: "result.metadata_backfilled",
      occurred_at: "2026-08-24T08:01:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { production_metadata: { web_access: false } },
    } as const;
    const backfilledAmendment = decodeResultAmendmentView({
      ...LEGACY_AMENDMENT_VIEW,
      mutation_event_id: BACKFILL_EVENT_ID,
    });
    const exact = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(backfilledAmendment),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(legacyClaimEvent()),
      contents(event),
    ]);
    await expect(repository(exact).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).resolves.toMatchObject({ created: false, mutationEventId: BACKFILL_EVENT_ID });

    clearResultOwnerContractProofCacheForTest();
    const changed = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(backfilledAmendment),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(legacyClaimEvent()),
      contents(event),
    ]);
    await expect(repository(changed).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: true },
    })).rejects.toBeInstanceOf(StateEventConflictError);

    clearResultOwnerContractProofCacheForTest();
    const forgedProvenance = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(backfilledAmendment),
      contents({
        ...overlay,
        metadata: {
          web_access: {
            ...overlay.metadata.web_access,
            recorded_at: "2026-08-24T08:02:00.000Z",
          },
        },
      }),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(legacyClaimEvent()),
      contents(event),
    ]);
    await expect(repository(forgedProvenance).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).rejects.toBeInstanceOf(StateEventConflictError);
  });

  it("serializes a later metadata backfill through a rejected retraction decision", async () => {
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const decisionId = "0198abcd-2222-7000-8000-000000000004";
    const backfillId = "0198abcd-2222-7000-8000-000000000005";
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-24T08:00:00.000Z",
    );
    const requested = requestedRetractionView(
      initialResultAmendmentView({
        resultId: LEGACY_RESULT.resultId,
        ownerLogin: "alice",
        declaredModel: LEGACY_RESULT.baseResult.declared_model,
        authorityEventId: CLAIM_EVENT_ID,
        mutationEventId: CLAIM_EVENT_ID,
        problemId: LEGACY_RESULT.baseResult.problem_id,
        statementRevision: LEGACY_RESULT.baseResult.statement_revision,
      }),
      requestId,
      "2026-08-24T08:01:00.000Z",
      "owner_requested_withdrawal",
    );
    const rejected = decodeResultAmendmentView({
      ...requested,
      mutation_event_id: decisionId,
      retraction: {
        ...requested.retraction,
        status: "rejected",
        decision_event_id: decisionId,
        decided_at: "2026-08-24T08:02:00.000Z",
        reviewer_login: "maintainer",
        reason_code: "request_not_confirmed",
      },
    });
    const decision: StateEvent = {
      schema_version: 1,
      event_id: decisionId,
      event_type: "result.retraction_decided",
      occurred_at: "2026-08-24T08:02:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: requestId,
      actor: { kind: "system" },
      payload: {
        retraction_revision: 1,
        reviewer_login: "maintainer",
        decision: "reject",
        reason_code: "request_not_confirmed",
      },
    };
    const request: StateEvent = {
      schema_version: 1,
      event_id: requestId,
      event_type: "result.retraction_requested",
      occurred_at: "2026-08-24T08:01:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: {
        retraction_revision: 1,
        reason_code: "owner_requested_withdrawal",
      },
    };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(rejected),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents(decision),
      contents(request),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).backfillLegacyResultMetadata({
      eventId: backfillId,
      occurredAt: "2026-08-24T08:03:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).resolves.toMatchObject({ created: true, mutationEventId: backfillId });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${backfillId}.json`,
      `views/result-overlays/11/${LEGACY_RESULT.resultId}.json`,
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
    ]);
    expect(JSON.parse(tree.tree[0]?.content ?? "null")).toMatchObject({
      causation_event_id: decisionId,
    });
    expect(JSON.parse(tree.tree[2]?.content ?? "null")).toMatchObject({
      mutation_event_id: backfillId,
      retraction: { status: "rejected" },
    });

    clearResultOwnerContractProofCacheForTest();
    const replay = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(rejected),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(request),
      contents(legacyClaimEvent()),
      contents(decision),
      contents(request),
    ]);
    await expect(repository(replay).requestResultRetraction({
      eventId: requestId,
      occurredAt: "2026-08-24T08:09:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    })).resolves.toEqual({
      commit: HEAD,
      created: false,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: requestId,
      retractionRevision: 1,
    });
    expect(replay.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const forgedHistory = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(rejected),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents(decision),
      contents({ ...request, actor: { kind: "github", login: "mallory" } }),
    ]);
    await expect(repository(forgedHistory).backfillLegacyResultMetadata({
      eventId: backfillId,
      occurredAt: "2026-08-24T08:03:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(forgedHistory.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const crossTypeDecision = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(rejected),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents({
        schema_version: 1,
        event_id: decisionId,
        event_type: "result.metadata_backfilled",
        occurred_at: "2026-08-24T08:02:00.000Z",
        subject_id: LEGACY_RESULT.resultId,
        causation_event_id: requestId,
        actor: { kind: "github", login: "alice" },
        payload: { production_metadata: { notes: "wrong event family" } },
      }),
      contents(request),
    ]);
    await expect(repository(crossTypeDecision).backfillLegacyResultMetadata({
      eventId: backfillId,
      occurredAt: "2026-08-24T08:03:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(crossTypeDecision.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("replays an older exact backfill after a later mutation using per-field provenance", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const claimed = claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z");
    const first = backfilledOverlay(
      claimed,
      BACKFILL_EVENT_ID,
      "2026-08-24T08:01:00.000Z",
      { web_access: false },
    );
    const laterEventId = "0198abcd-0000-7000-8000-000000000003";
    const current = backfilledOverlay(
      first,
      laterEventId,
      "2026-08-24T08:02:00.000Z",
      { notes: "later mutation" },
    );
    const firstEvent = {
      schema_version: 1,
      event_id: BACKFILL_EVENT_ID,
      event_type: "result.metadata_backfilled",
      occurred_at: "2026-08-24T08:01:00.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { production_metadata: { web_access: false } },
    } as const;
    const laterEvent: StateEvent = {
      ...firstEvent,
      event_id: laterEventId,
      occurred_at: "2026-08-24T08:02:00.000Z",
      causation_event_id: BACKFILL_EVENT_ID,
      payload: { production_metadata: { notes: "later mutation" } },
    };
    const currentAmendment = decodeResultAmendmentView({
      ...LEGACY_AMENDMENT_VIEW,
      mutation_event_id: laterEventId,
    });
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(currentAmendment),
      contents(current),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(firstEvent),
      contents(legacyClaimEvent()),
      contents(laterEvent),
    ]);
    await expect(repository(fetcher).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      productionMetadata: { web_access: false },
    })).resolves.toEqual({
      commit: HEAD,
      created: false,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: BACKFILL_EVENT_ID,
    });
  });

  it("hides a legacy claim from a different authenticated owner", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-24T08:00:00.000Z")),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent()),
      contents(legacyClaimEvent()),
    ]);
    await expect(repository(fetcher).backfillLegacyResultMetadata({
      eventId: BACKFILL_EVENT_ID,
      occurredAt: "2026-08-24T08:01:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "mallory",
      productionMetadata: { web_access: false },
    })).rejects.toMatchObject({ status: 404 });
  });

  it("refuses to overwrite an existing event", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents({ ...EVENT, occurred_at: "2026-08-21T06:07:08.000Z" }),
    ]);
    await expect(repository(fetcher).appendEvent(EVENT)).rejects.toBeInstanceOf(
      StateEventConflictError,
    );
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("treats a structurally identical existing event as an idempotent success", async () => {
    const reordered = {
      payload: EVENT.payload,
      actor: EVENT.actor,
      causation_event_id: EVENT.causation_event_id,
      subject_id: EVENT.subject_id,
      occurred_at: EVENT.occurred_at,
      event_type: EVENT.event_type,
      event_id: EVENT.event_id,
      schema_version: EVENT.schema_version,
    };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(reordered),
    ]);
    await expect(repository(fetcher).appendEvent(EVENT)).resolves.toEqual({
      commit: HEAD,
      created: false,
      path: `events/01/${EVENT.event_id}.json`,
    });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("retries the complete compare-and-swap after a ref collision", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ message: "conflict" }, 409),
      json({ object: { sha: "5".repeat(40) } }),
      json({ tree: { sha: "6".repeat(40) } }),
      new Response(null, { status: 404 }),
      json({ sha: "7".repeat(40) }, 201),
      json({ sha: "8".repeat(40) }, 201),
      json({ object: { sha: "8".repeat(40) } }),
    ]);

    await expect(repository(fetcher).appendEvent(EVENT)).resolves.toEqual({
      commit: "8".repeat(40),
      created: true,
      path: `events/01/${EVENT.event_id}.json`,
    });
    expect(fetcher).toHaveBeenCalledTimes(12);
  });

  it("recognizes an applied commit after an ambiguous ref update", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ message: "upstream unavailable" }, 500),
      json({ message: "conflict" }, 409),
      json({ object: { sha: NEW_COMMIT } }),
    ]);

    await expect(repository(fetcher).appendEvent(EVENT)).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      path: `events/01/${EVENT.event_id}.json`,
    });
  });

  it("fails closed when an ambiguous update cannot be resolved", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      new Error("connection reset"),
      new Error("connection reset"),
      new Error("GitHub unavailable"),
    ]);

    await expect(repository(fetcher).appendEvent(EVENT)).rejects.toBeInstanceOf(
      StateUpdateOutcomeUnknownError,
    );
  });

  it("rejects malformed existing event contents", async () => {
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      contents(null),
    ]);
    await expect(repository(fetcher).appendEvent(EVENT)).rejects.toBeInstanceOf(
      StateEventConflictError,
    );
  });

  it("atomically requests a legacy-result retraction and writes only its targeted private view", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority: StateEvent = {
      schema_version: 1,
      event_id: CLAIM_EVENT_ID,
      event_type: "result.claimed",
      occurred_at: "2026-08-20T06:07:08.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: LEGACY_RESULT.baseResult,
    };
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(authority),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).requestResultRetraction({
      eventId: requestId,
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: requestId,
      retractionRevision: 1,
    });
    const treeCall = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST");
    expect(treeCall).toBeDefined();
    const treeRequest = treeCall?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const body = JSON.parse(treeRequest) as {
      tree: { path: string; content: string }[];
    };
    expect(body.tree.map((entry) => entry.path)).toEqual([
      `events/01/${requestId}.json`,
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
    ]);
    expect(treeRequest).not.toContain("views/result-release-status/");
    const event = JSON.parse(body.tree[0]?.content ?? "null") as Record<string, unknown>;
    expect(event).toMatchObject({
      event_type: "result.retraction_requested",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { retraction_revision: 1, reason_code: "owner_requested_withdrawal" },
    });
  });

  it("records an authenticated maintainer retraction decision as one causal State mutation", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const decisionId = "0198abcd-2222-7000-8000-000000000004";
    const requestEvent: StateEvent = {
      schema_version: 1,
      event_id: requestId,
      event_type: "result.retraction_requested",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { retraction_revision: 1, reason_code: "owner_requested_withdrawal" },
    };
    const pending = requestedRetractionView(
      LEGACY_AMENDMENT_VIEW,
      requestId,
      requestEvent.occurred_at,
      "owner_requested_withdrawal",
    );
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(pending),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(requestEvent),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    const decision = {
      eventId: decisionId,
      occurredAt: "2026-08-20T06:07:10.000Z",
      resultId: LEGACY_RESULT.resultId,
      reviewerLogin: "maintainer",
      decision: "approve" as const,
      reasonCode: "owner_request_verified",
    };
    await expect(repository(fetcher).decideResultRetraction(decision)).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: decisionId,
      retractionRevision: 1,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    const event: unknown = JSON.parse(tree.tree[0]?.content ?? "null");
    validateStateEvent(event);
    const decided = decodeResultAmendmentView(JSON.parse(tree.tree[1]?.content ?? "null") as unknown);
    expect(event).toMatchObject({
      event_id: decisionId,
      event_type: "result.retraction_decided",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: requestId,
      actor: { kind: "system" },
      payload: {
        retraction_revision: 1,
        reviewer_login: "maintainer",
        decision: "approve",
        reason_code: "owner_request_verified",
      },
    });
    expect(decided).toMatchObject({
      mutation_event_id: decisionId,
      retraction: { status: "approved", decision_event_id: decisionId },
    });

    clearResultOwnerContractProofCacheForTest();
    const replay = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(decided),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(authority),
      contents(event),
      contents(requestEvent),
    ]);
    await expect(repository(replay).decideResultRetraction(decision)).resolves.toMatchObject({
      created: false,
      retractionRevision: 1,
    });
    expect(replay.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const changed = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(decided),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(authority),
      contents(event),
      contents(requestEvent),
    ]);
    await expect(repository(changed).decideResultRetraction({
      ...decision,
      reasonCode: "changed_review_reason",
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(changed.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("records a maintainer override without impersonating an owner", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const overrideId = "0198abcd-2222-7000-8000-000000000003";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(authority),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    const override = {
      eventId: overrideId,
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      reviewerLogin: "maintainer",
      reasonCode: "owner_account_unavailable",
    };
    await expect(repository(fetcher).overrideResultRetraction(override)).resolves.toMatchObject({
      created: true,
      retractionRevision: 1,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { content: string }[] };
    const event: unknown = JSON.parse(tree.tree[0]?.content ?? "null");
    validateStateEvent(event);
    const overridden = decodeResultAmendmentView(JSON.parse(tree.tree[1]?.content ?? "null") as unknown);
    expect(event).toMatchObject({
      event_type: "result.retraction_overridden",
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "system" },
      payload: {
        retraction_revision: 1,
        reviewer_login: "maintainer",
        reason_code: "owner_account_unavailable",
      },
    });
    expect(JSON.stringify(event)).not.toContain('"login":"alice"');

    clearResultOwnerContractProofCacheForTest();
    const replay = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(overridden),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(authority),
      contents(event),
      contents(authority),
    ]);
    await expect(repository(replay).overrideResultRetraction(override)).resolves.toMatchObject({
      created: false,
      retractionRevision: 1,
    });
    expect(replay.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("round-trips a maintainer override after a rejected owner request", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const requestId = "0198abcd-2222-7000-8000-000000000010";
    const rejectionId = "0198abcd-2222-7000-8000-000000000011";
    const overrideId = "0198abcd-2222-7000-8000-000000000012";
    const requestEvent: StateEvent = {
      schema_version: 1,
      event_id: requestId,
      event_type: "result.retraction_requested",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { retraction_revision: 1, reason_code: "owner_requested_withdrawal" },
    };
    const pending = requestedRetractionView(
      LEGACY_AMENDMENT_VIEW,
      requestId,
      requestEvent.occurred_at,
      "owner_requested_withdrawal",
    );
    const rejected = decidedRetractionView(
      pending,
      rejectionId,
      "2026-08-20T06:07:10.000Z",
      "first-maintainer",
      "reject",
      "request_not_verified",
    );
    const rejectionEvent: StateEvent = {
      schema_version: 1,
      event_id: rejectionId,
      event_type: "result.retraction_decided",
      occurred_at: "2026-08-20T06:07:10.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: requestId,
      actor: { kind: "system" },
      payload: {
        retraction_revision: 1,
        reviewer_login: "first-maintainer",
        decision: "reject",
        reason_code: "request_not_verified",
      },
    };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(rejected),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(rejectionEvent),
      contents(requestEvent),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    const override = {
      eventId: overrideId,
      occurredAt: "2026-08-20T06:07:11.000Z",
      resultId: LEGACY_RESULT.resultId,
      reviewerLogin: "second-maintainer",
      reasonCode: "owner_account_unavailable",
    };
    await expect(repository(fetcher).overrideResultRetraction(override)).resolves.toMatchObject({
      created: true,
      retractionRevision: 2,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { content: string }[] };
    const event = JSON.parse(tree.tree[0]?.content ?? "null") as StateEvent;
    const overridden = decodeResultAmendmentView(
      JSON.parse(tree.tree[1]?.content ?? "null") as unknown,
    );
    expect(event).toMatchObject({
      event_type: "result.retraction_overridden",
      causation_event_id: rejectionId,
      payload: { retraction_revision: 2, reviewer_login: "second-maintainer" },
    });

    clearResultOwnerContractProofCacheForTest();
    const replay = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(overridden),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(authority),
      contents(event),
      contents(rejectionEvent),
    ]);
    await expect(repository(replay).overrideResultRetraction(override)).resolves.toMatchObject({
      created: false,
      retractionRevision: 2,
    });
    expect(replay.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("derives terminal retraction disposition from the exact release view and blocks running releases", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const decisionId = "0198abcd-2222-7000-8000-000000000004";
    const terminalId = "0198abcd-2222-7000-8000-000000000005";
    const pending = requestedRetractionView(
      LEGACY_AMENDMENT_VIEW,
      requestId,
      "2026-08-20T06:07:09.000Z",
      "owner_requested_withdrawal",
    );
    const approved = decidedRetractionView(
      pending,
      decisionId,
      "2026-08-20T06:07:10.000Z",
      "maintainer",
      "approve",
      "owner_request_verified",
    );
    const decisionEvent: StateEvent = {
      schema_version: 1,
      event_id: decisionId,
      event_type: "result.retraction_decided",
      occurred_at: "2026-08-20T06:07:10.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: requestId,
      actor: { kind: "system" },
      payload: {
        retraction_revision: 1,
        reviewer_login: "maintainer",
        decision: "approve",
        reason_code: "owner_request_verified",
      },
    };
    const requestEvent: StateEvent = {
      schema_version: 1,
      event_id: requestId,
      event_type: "result.retraction_requested",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { retraction_revision: 1, reason_code: "owner_requested_withdrawal" },
    };
    const releaseEventId = "0198abcd-2222-7000-8000-000000000002";
    const releaseEvent = (status: "running" | "published"): StateEvent => ({
      schema_version: 1,
      event_id: releaseEventId,
      event_type: status === "running" ? "release.started" : "release.published",
      occurred_at: "2026-08-20T06:07:08.500Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "system" },
      payload: status === "running"
        ? { attempt: 1 }
        : {
            attempt: 1,
            repository_commit: "9".repeat(40),
            tree_digest: "8".repeat(64),
            path: "releases/test",
          },
    });
    const responses = (status: "running" | "published") => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(approved),
      contents(overlay),
      contents({
        ...LEGACY_RELEASE_STATUS_VIEW,
        status,
        release_revision: 1,
        release_event_id: releaseEventId,
        supersedes_release_event_id: null,
      }),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(releaseEvent(status)),
      contents(decisionEvent),
      contents(requestEvent),
    ];
    const running = sequence(responses("running"));
    await expect(repository(running).finalizeResultRetraction({
      eventId: terminalId,
      occurredAt: "2026-08-20T06:07:11.000Z",
      resultId: LEGACY_RESULT.resultId,
      maintainerLogin: "another-maintainer",
    })).rejects.toMatchObject({ status: 409 });
    expect(running.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const published = sequence([
      ...responses("published"),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(published).finalizeResultRetraction({
      eventId: terminalId,
      occurredAt: "2026-08-20T06:07:11.000Z",
      resultId: LEGACY_RESULT.resultId,
      maintainerLogin: "another-maintainer",
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: terminalId,
      releaseDisposition: "removal_required",
    });
    const treeRequest = published.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { content: string }[] };
    const terminalEvent: unknown = JSON.parse(tree.tree[0]?.content ?? "null");
    validateStateEvent(terminalEvent);
    const terminalView = decodeResultAmendmentView(JSON.parse(tree.tree[1]?.content ?? "null") as unknown);
    expect(terminalEvent).toMatchObject({
      event_type: "result.retracted",
      causation_event_id: decisionId,
      payload: {
        release_disposition: "removal_required",
        reviewer_login: "maintainer",
      },
    });
    expect(terminalView).toEqual(terminalRetractionView(
      approved,
      terminalId,
      "2026-08-20T06:07:11.000Z",
      "maintainer",
      "owner_request_verified",
      "removal_required",
    ));

    clearResultOwnerContractProofCacheForTest();
    const replay = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(terminalView),
      contents(overlay),
      contents({
        ...LEGACY_RELEASE_STATUS_VIEW,
        status: "published",
        release_revision: 1,
        release_event_id: releaseEventId,
        supersedes_release_event_id: null,
      }),
      contents(terminalEvent),
      contents(authority),
      contents(releaseEvent("published")),
      contents(terminalEvent),
      contents(requestEvent),
      contents(decisionEvent),
    ]);
    await expect(repository(replay).finalizeResultRetraction({
      eventId: terminalId,
      occurredAt: "2026-08-20T06:08:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      maintainerLogin: "another-maintainer",
    })).resolves.toMatchObject({ created: false, releaseDisposition: "removal_required" });
    expect(replay.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("atomically requests a problem repair without changing the targeted release status", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(authority),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).requestResultProblemRepair({
      eventId: requestId,
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 2,
      reasonCode: "wrong_problem_revision",
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: requestId,
      repairRevision: 1,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${requestId}.json`,
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
    ]);
    expect(treeRequest).not.toContain("views/result-release-status/");
    expect(JSON.parse(tree.tree[0]?.content ?? "null")).toMatchObject({
      event_type: "result.problem_repair_requested",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: {
        repair_revision: 1,
        corrected_problem_id: "two_plus_three",
        corrected_statement_revision: 2,
        reason_code: "wrong_problem_revision",
      },
    });
    expect(JSON.parse(tree.tree[1]?.content ?? "null")).toMatchObject({
      mutation_event_id: requestId,
      problem_repair: {
        revision: 1,
        status: "pending",
        request_event_id: requestId,
      },
    });
  });

  it("rejects an unregistered removed event smuggled in as a release predecessor", async () => {
    const predecessorId = "0198abcd-2222-7000-8000-000000000006";
    const currentId = "0198abcd-2222-7000-8000-000000000007";
    const current: StateEvent = {
      schema_version: 1,
      event_id: currentId,
      event_type: "release.published",
      occurred_at: "2026-08-20T06:07:10.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: predecessorId,
      actor: { kind: "system" },
      payload: {
        attempt: 1,
        repository_commit: "9".repeat(40),
        tree_digest: "8".repeat(64),
        path: "releases/test",
      },
    };
    const smuggledPredecessor = {
      schema_version: 1,
      event_id: predecessorId,
      event_type: "release.removed",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "system" },
      payload: {},
    };
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(claimedOverlay(
        LEGACY_RESULT,
        CLAIM_EVENT_ID,
        "2026-08-20T06:07:08.000Z",
      )),
      contents({
        ...LEGACY_RELEASE_STATUS_VIEW,
        status: "published",
        release_revision: 2,
        release_event_id: currentId,
        supersedes_release_event_id: predecessorId,
      }),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent("2026-08-20T06:07:08.000Z")),
      contents(current),
      contents(smuggledPredecessor),
    ]);
    await expect(repository(fetcher).requestResultProblemRepair({
      eventId: "0198abcd-2222-7000-8000-000000000008",
      occurredAt: "2026-08-20T06:07:11.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 2,
      reasonCode: "wrong_problem_revision",
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("rejects malformed current release-removal evidence before trusting its barrier", async () => {
    const publishedEventId = "0198abcd-2222-7000-8000-000000000005";
    const removalEventId = "0198abcd-2222-7000-8000-000000000006";
    const valid = removedReleaseEvent(removalEventId, publishedEventId);
    const validPayload = valid.payload as Record<string, unknown>;
    const hostile = [
      { ...valid, payload: {} },
      { ...valid, payload: { ...validPayload, unexpected: true } },
      { ...valid, payload: { ...validPayload, classification: "unreviewed" } },
      { ...valid, payload: { ...validPayload, published_state_event_repository: "example/state" } },
      { ...valid, payload: { ...validPayload, evidence_repository: "example/audit" } },
      { ...valid, payload: { ...validPayload, evidence_commit: "not-a-commit" } },
      { ...valid, payload: { ...validPayload, evidence_sha256: "not-a-digest" } },
      { ...valid, payload: { ...validPayload, evidence_path: "../private.json" } },
      { ...valid, subject_id: `r2_${"f".repeat(64)}` },
      { ...valid, payload: { ...validPayload, release_path: `releases/2026/08/r2_${"f".repeat(64)}` } },
      { ...valid, payload: { ...validPayload, published_state_event_path: "events/00/wrong.json" } },
      { ...valid, payload: { ...validPayload, bundle_disposition: "retain_shared" } },
      {
        ...valid,
        payload: {
          ...validPayload,
          bundle_disposition: "retain_shared",
          shared_release_paths: [
            `releases/2026/08/r2_${"e".repeat(64)}`,
            `releases/2026/08/r2_${"e".repeat(64)}`,
          ],
        },
      },
      {
        ...valid,
        payload: {
          ...validPayload,
          bundle_disposition: "retain_shared",
          shared_release_paths: [validPayload.release_path],
        },
      },
    ];
    for (const event of hostile) {
      clearResultOwnerContractProofCacheForTest();
      const fetcher = sequence([
        json({ object: { sha: HEAD } }),
        json({ tree: { sha: TREE } }),
        ...resultOwnerContractProofResponses(),
        contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
        contents(LEGACY_AMENDMENT_VIEW),
        contents(claimedOverlay(
          LEGACY_RESULT,
          CLAIM_EVENT_ID,
          "2026-08-20T06:07:08.000Z",
        )),
        contents({
          ...LEGACY_RELEASE_STATUS_VIEW,
          status: "removed",
          release_revision: 2,
          release_event_id: removalEventId,
          supersedes_release_event_id: publishedEventId,
        }),
        new Response(null, { status: 404 }),
        contents(legacyClaimEvent("2026-08-20T06:07:08.000Z")),
        contents(event),
      ]);
      await expect(repository(fetcher).requestResultProblemRepair({
        eventId: "0198abcd-2222-7000-8000-000000000008",
        occurredAt: "2026-08-20T06:07:11.000Z",
        resultId: LEGACY_RESULT.resultId,
        ownerLogin: "alice",
        correctedProblemId: "two_plus_three",
        correctedStatementRevision: 2,
        reasonCode: "wrong_problem_revision",
      })).rejects.toBeInstanceOf(StateEventConflictError);
      expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    }
  });

  it("rejects incoherent release-removal predecessor bindings", async () => {
    const startedEventId = "0198abcd-2222-7000-8000-000000000004";
    const publishedEventId = "0198abcd-2222-7000-8000-000000000005";
    const removalEventId = "0198abcd-2222-7000-8000-000000000006";
    const published = publishedReleaseEvent(publishedEventId, startedEventId);
    const publishedPayload = published.payload as Record<string, unknown>;
    const started = startedReleaseEvent(startedEventId);
    const cases = [
      {
        statusRevision: 1,
        supersedes: null,
        removal: removedReleaseEvent(removalEventId, publishedEventId),
        predecessor: published,
        started,
      },
      {
        statusRevision: 2,
        supersedes: publishedEventId,
        removal: removedReleaseEvent(
          removalEventId,
          "0198abcd-2222-7000-8000-000000000003",
        ),
        predecessor: published,
        started,
      },
      {
        statusRevision: 2,
        supersedes: publishedEventId,
        removal: removedReleaseEvent(removalEventId, publishedEventId),
        predecessor: {
          ...published,
          event_type: "release.failed",
          payload: { attempt: 1, reason_code: "failed", retryable: true },
        },
        started,
      },
      {
        statusRevision: 2,
        supersedes: publishedEventId,
        removal: removedReleaseEvent(removalEventId, publishedEventId),
        predecessor: { ...published, subject_id: `r2_${"f".repeat(64)}` },
        started,
      },
      ...[
        { published_repository_commit: "d".repeat(40) },
        { tree_digest: "d".repeat(64) },
        { path: `releases/2026/08/r2_${"f".repeat(64)}` },
      ].map((payloadOverride) => ({
        statusRevision: 2,
        supersedes: publishedEventId,
        removal: removedReleaseEvent(removalEventId, publishedEventId),
        predecessor: { ...published, payload: { ...publishedPayload, ...payloadOverride } },
        started,
      })),
      {
        statusRevision: 2,
        supersedes: publishedEventId,
        removal: removedReleaseEvent(removalEventId, publishedEventId),
        predecessor: published,
        started: {
          ...started,
          event_type: "release.failed",
          payload: { attempt: 1, reason_code: "failed", retryable: true },
        },
      },
      {
        statusRevision: 2,
        supersedes: publishedEventId,
        removal: removedReleaseEvent(removalEventId, publishedEventId),
        predecessor: published,
        started: { ...started, subject_id: `r2_${"f".repeat(64)}` },
      },
      {
        statusRevision: 2,
        supersedes: publishedEventId,
        removal: removedReleaseEvent(removalEventId, publishedEventId),
        predecessor: published,
        started: { ...started, payload: { attempt: 2 } },
      },
    ];
    for (const hostile of cases) {
      clearResultOwnerContractProofCacheForTest();
      const fetcher = sequence([
        json({ object: { sha: HEAD } }),
        json({ tree: { sha: TREE } }),
        ...resultOwnerContractProofResponses(),
        contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
        contents(LEGACY_AMENDMENT_VIEW),
        contents(claimedOverlay(
          LEGACY_RESULT,
          CLAIM_EVENT_ID,
          "2026-08-20T06:07:08.000Z",
        )),
        contents({
          ...LEGACY_RELEASE_STATUS_VIEW,
          status: "removed",
          release_revision: hostile.statusRevision,
          release_event_id: removalEventId,
          supersedes_release_event_id: hostile.supersedes,
        }),
        new Response(null, { status: 404 }),
        contents(legacyClaimEvent("2026-08-20T06:07:08.000Z")),
        contents(hostile.removal),
        contents(hostile.predecessor),
        contents(hostile.started),
      ]);
      await expect(repository(fetcher).requestResultProblemRepair({
        eventId: "0198abcd-2222-7000-8000-000000000008",
        occurredAt: "2026-08-20T06:07:11.000Z",
        resultId: LEGACY_RESULT.resultId,
        ownerLogin: "alice",
        correctedProblemId: "two_plus_three",
        correctedStatementRevision: 2,
        reasonCode: "wrong_problem_revision",
      })).rejects.toBeInstanceOf(StateEventConflictError);
      expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    }
  });

  it("rejects problem repair after release starts, publishes, or is removed", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    for (const status of ["running", "published", "removed"] as const) {
      clearResultOwnerContractProofCacheForTest();
      const publishedEventId = "0198abcd-2222-7000-8000-000000000005";
      const releaseEventId = status === "removed"
        ? "0198abcd-2222-7000-8000-000000000006"
        : "0198abcd-2222-7000-8000-000000000002";
      const fetcher = sequence([
        json({ object: { sha: HEAD } }),
        json({ tree: { sha: TREE } }),
        ...resultOwnerContractProofResponses(),
        contents(guard),
        contents(LEGACY_AMENDMENT_VIEW),
        contents(overlay),
        contents({
          ...LEGACY_RELEASE_STATUS_VIEW,
          status,
          release_revision: status === "removed" ? 2 : 1,
          release_event_id: releaseEventId,
          supersedes_release_event_id: status === "removed" ? publishedEventId : null,
        }),
        new Response(null, { status: 404 }),
        contents(authority),
        contents(status === "removed"
          ? removedReleaseEvent(releaseEventId, publishedEventId)
          : {
              schema_version: 1,
              event_id: releaseEventId,
              event_type: status === "running" ? "release.started" : "release.published",
              occurred_at: "2026-08-20T06:07:08.500Z",
              subject_id: LEGACY_RESULT.resultId,
              causation_event_id: CLAIM_EVENT_ID,
              actor: { kind: "system" },
              payload: status === "running"
                ? { attempt: 1 }
                : {
                    attempt: 1,
                    repository_commit: "9".repeat(40),
                    tree_digest: "8".repeat(64),
                    path: "releases/test",
                  },
            }),
        ...(status === "removed"
          ? [
              contents(publishedReleaseEvent(publishedEventId)),
              contents(startedReleaseEvent("0198abcd-2222-7000-8000-000000000004")),
            ]
          : []),
        contents(authority),
      ]);
      await expect(repository(fetcher).requestResultProblemRepair({
        eventId: "0198abcd-2222-7000-8000-000000000003",
        occurredAt: "2026-08-20T06:07:09.000Z",
        resultId: LEGACY_RESULT.resultId,
        ownerLogin: "alice",
        correctedProblemId: "two_plus_three",
        correctedStatementRevision: 2,
        reasonCode: "wrong_problem_revision",
      })).rejects.toMatchObject({ status: 409 });
      expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    }
  });

  it("rejects a release-status view without its exact immutable marker", async () => {
    const releaseEventId = "0198abcd-2222-7000-8000-000000000002";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID)),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(claimedOverlay(LEGACY_RESULT, CLAIM_EVENT_ID, "2026-08-20T06:07:08.000Z")),
      contents({
        ...LEGACY_RELEASE_STATUS_VIEW,
        status: "running",
        release_revision: 1,
        release_event_id: releaseEventId,
        supersedes_release_event_id: null,
      }),
      new Response(null, { status: 404 }),
      contents(legacyClaimEvent("2026-08-20T06:07:08.000Z")),
      contents({
        schema_version: 1,
        event_id: releaseEventId,
        event_type: "release.published",
        occurred_at: "2026-08-20T06:07:08.500Z",
        subject_id: LEGACY_RESULT.resultId,
        causation_event_id: CLAIM_EVENT_ID,
        actor: { kind: "system" },
        payload: {
          attempt: 1,
          repository_commit: "9".repeat(40),
          tree_digest: "8".repeat(64),
          path: "releases/test",
        },
      }),
    ]);
    await expect(repository(fetcher).requestResultProblemRepair({
      eventId: "0198abcd-2222-7000-8000-000000000003",
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 2,
      reasonCode: "wrong_problem_revision",
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("makes an exact problem-repair replay idempotent and rejects changed material", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const event = {
      schema_version: 1,
      event_id: requestId,
      event_type: "result.problem_repair_requested",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: {
        repair_revision: 1,
        corrected_problem_id: "two_plus_three",
        corrected_statement_revision: 2,
        reason_code: "wrong_problem_revision",
      },
    } as const;
    const pending = requestedProblemRepairView(
      LEGACY_AMENDMENT_VIEW,
      requestId,
      event.occurred_at,
      event.payload.corrected_problem_id,
      event.payload.corrected_statement_revision,
      event.payload.reason_code,
    );
    const responses = () => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(pending),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(authority),
      contents(event),
    ];
    const exact = sequence(responses());
    await expect(repository(exact).requestResultProblemRepair({
      eventId: requestId,
      occurredAt: "2026-08-20T06:08:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 2,
      reasonCode: "wrong_problem_revision",
    })).resolves.toMatchObject({ created: false, repairRevision: 1 });
    expect(exact.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const changed = sequence(responses());
    await expect(repository(changed).requestResultProblemRepair({
      eventId: requestId,
      occurredAt: "2026-08-20T06:08:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      correctedProblemId: "different_problem",
      correctedStatementRevision: 2,
      reasonCode: "wrong_problem_revision",
    })).rejects.toBeInstanceOf(StateEventConflictError);
  });

  it("applies and replays one maintainer problem-repair decision with bound comparator evidence", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const pending = pendingLegacyProblemRepair();
    const requestEvent = legacyProblemRepairRequestEvent();
    const evidence = await legacyComparatorEvidence();
    const reservation = await legacyRepairReservation();
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(pending),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(requestEvent),
      new Response(null, { status: 404 }),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    const decision = {
      eventId: REPAIR_DECISION_ID,
      occurredAt: REPAIR_DECIDED_AT,
      resultId: LEGACY_RESULT.resultId,
      reviewerLogin: "maintainer",
      decision: "apply" as const,
      reasonCode: null,
      comparatorEvidence: evidence,
    };
    await expect(repository(fetcher).decideResultProblemRepair(decision)).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: LEGACY_RESULT.resultId,
      mutationEventId: REPAIR_DECISION_ID,
      repairRevision: 1,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    const event: unknown = JSON.parse(tree.tree[0]?.content ?? "null");
    validateStateEvent(event);
    const decided = decodeResultAmendmentView(JSON.parse(tree.tree[1]?.content ?? "null") as unknown);
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${REPAIR_DECISION_ID}.json`,
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
      effectiveResultIdentityPath(reservation.effective_result_identity_id),
    ]);
    expect(event).toMatchObject({
      event_type: "result.problem_repaired",
      causation_event_id: REPAIR_REQUEST_ID,
      actor: { kind: "system" },
      payload: {
        repair_revision: 1,
        reviewer_login: "maintainer",
        comparator_binding_sha256: evidence.binding_sha256,
        evidence_base_challenge_id: evidence.evidence_base_challenge_id,
        evidence_corrected_challenge_id: evidence.evidence_corrected_challenge_id,
      },
    });
    expect(decided).toEqual(decidedProblemRepairView(
      pending,
      REPAIR_DECISION_ID,
      REPAIR_DECIDED_AT,
      "maintainer",
      "apply",
      null,
      evidence,
    ));

    clearResultOwnerContractProofCacheForTest();
    const replay = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(decided),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(authority),
      contents(event),
      contents(requestEvent),
      contents(reservation),
      contents(event),
    ]);
    await expect(repository(replay).decideResultProblemRepair(decision)).resolves.toMatchObject({
      created: false,
      repairRevision: 1,
    });
    expect(replay.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const changed = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(decided),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(authority),
      contents(event),
      contents(requestEvent),
    ]);
    await expect(repository(changed).decideResultProblemRepair({
      ...decision,
      comparatorEvidence: { ...evidence, blob_sha256: "e".repeat(64) },
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(changed.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("allows a same-result historical identity revisit without rewriting its reservation", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const pending = pendingLegacyProblemRepair();
    const requestEvent = legacyProblemRepairRequestEvent();
    const evidence = await legacyComparatorEvidence();
    const priorDecisionId = "0198abcd-2222-7000-8000-000000000002";
    const priorEvent: StateEvent = {
      schema_version: 1,
      event_id: priorDecisionId,
      event_type: "result.problem_repaired",
      occurred_at: "2026-08-20T06:07:08.900Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: "0198abcd-2222-7000-8000-000000000001",
      actor: { kind: "system" },
      payload: {
        repair_revision: 1,
        corrected_problem_id: "two_plus_three",
        corrected_statement_revision: 2,
        reviewer_login: "maintainer",
        comparator_repository: evidence.repository,
        comparator_commit: evidence.commit,
        comparator_path: evidence.path,
        comparator_blob_oid: evidence.blob_oid,
        comparator_blob_sha256: evidence.blob_sha256,
        comparator_record_sha256: evidence.record_sha256,
        comparator_binding_sha256: evidence.binding_sha256,
        comparator_verification_method: evidence.verification_method,
        evidence_result_id: evidence.evidence_result_id,
        evidence_owner_login: evidence.evidence_owner_login,
        evidence_declared_model: evidence.evidence_declared_model,
        evidence_base_problem_group: evidence.evidence_base_problem_group,
        evidence_base_problem_id: evidence.evidence_base_problem_id,
        evidence_base_statement_revision: evidence.evidence_base_statement_revision,
        evidence_base_challenge_id: evidence.evidence_base_challenge_id,
        evidence_corrected_problem_group: evidence.evidence_corrected_problem_group,
        evidence_corrected_problem_id: evidence.evidence_corrected_problem_id,
        evidence_corrected_statement_revision: evidence.evidence_corrected_statement_revision,
        evidence_corrected_challenge_id: evidence.evidence_corrected_challenge_id,
      },
    };
    validateStateEvent(priorEvent);
    const reservation = await legacyRepairReservation(
      LEGACY_RESULT.resultId,
      priorDecisionId,
    );
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(pending),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(requestEvent),
      contents(reservation),
      contents(priorEvent),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).decideResultProblemRepair({
      eventId: REPAIR_DECISION_ID,
      occurredAt: REPAIR_DECIDED_AT,
      resultId: LEGACY_RESULT.resultId,
      reviewerLogin: "maintainer",
      decision: "apply",
      reasonCode: null,
      comparatorEvidence: evidence,
    })).resolves.toMatchObject({ created: true, repairRevision: 1 });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${REPAIR_DECISION_ID}.json`,
      `views/result-amendments/11/${LEGACY_RESULT.resultId}.json`,
    ]);
  });

  it("records a problem-repair rejection without comparator evidence", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const pending = pendingLegacyProblemRepair();
    const requestEvent = legacyProblemRepairRequestEvent();
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(pending),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(requestEvent),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).decideResultProblemRepair({
      eventId: REPAIR_DECISION_ID,
      occurredAt: REPAIR_DECIDED_AT,
      resultId: LEGACY_RESULT.resultId,
      reviewerLogin: "maintainer",
      decision: "reject",
      reasonCode: "insufficient_comparator_evidence",
      comparatorEvidence: null,
    })).resolves.toMatchObject({ created: true, repairRevision: 1 });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { content: string }[] };
    expect(tree.tree).toHaveLength(2);
    const writes: unknown[] = tree.tree.map((entry) => JSON.parse(entry.content) as unknown);
    expect(writes[0]).toMatchObject({
      event_type: "result.problem_repair_rejected",
      causation_event_id: REPAIR_REQUEST_ID,
      payload: {
        reviewer_login: "maintainer",
        reason_code: "insufficient_comparator_evidence",
      },
    });
    expect(writes[1]).toMatchObject({
      problem_repair: {
        status: "rejected",
        comparator_evidence: null,
      },
    });
  });

  it("recomputes every derived comparator field before any State write", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const pending = pendingLegacyProblemRepair();
    const requestEvent = legacyProblemRepairRequestEvent();
    const evidence = await legacyComparatorEvidence();
    for (const corrupted of [
      { ...evidence, evidence_base_challenge_id: `ch1_${"0".repeat(64)}` },
      { ...evidence, evidence_corrected_challenge_id: `ch1_${"0".repeat(64)}` },
      { ...evidence, binding_sha256: "0".repeat(64) },
    ]) {
      clearResultOwnerContractProofCacheForTest();
      const fetcher = sequence([
        json({ object: { sha: HEAD } }),
        json({ tree: { sha: TREE } }),
        ...resultOwnerContractProofResponses(),
        contents(guard),
        contents(pending),
        contents(overlay),
        contents(LEGACY_RELEASE_STATUS_VIEW),
        new Response(null, { status: 404 }),
        contents(authority),
        contents(requestEvent),
      ]);
      await expect(repository(fetcher).decideResultProblemRepair({
        eventId: REPAIR_DECISION_ID,
        occurredAt: REPAIR_DECIDED_AT,
        resultId: LEGACY_RESULT.resultId,
        reviewerLogin: "maintainer",
        decision: "apply",
        reasonCode: null,
        comparatorEvidence: corrupted,
      })).rejects.toMatchObject({ status: 409 });
      expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    }
  });

  it("rejects effective-identity collisions and release barriers before any State write", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority = legacyClaimEvent("2026-08-20T06:07:08.000Z");
    const pending = pendingLegacyProblemRepair();
    const requestEvent = legacyProblemRepairRequestEvent();
    const evidence = await legacyComparatorEvidence();
    const collisionResultId = `r2_${"2".repeat(64)}`;
    const collisionReservation = await legacyRepairReservation(
      collisionResultId,
      "0198abcd-2222-7000-8000-000000000002",
    );
    const collisionFetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(pending),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(requestEvent),
      contents(collisionReservation),
    ]);
    const decision = {
      eventId: REPAIR_DECISION_ID,
      occurredAt: REPAIR_DECIDED_AT,
      resultId: LEGACY_RESULT.resultId,
      reviewerLogin: "maintainer",
      decision: "apply" as const,
      reasonCode: null,
      comparatorEvidence: evidence,
    };
    await expect(repository(collisionFetcher).decideResultProblemRepair(decision)).rejects.toMatchObject({ status: 409 });
    expect(collisionFetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    for (const status of ["running", "published", "removed"] as const) {
      clearResultOwnerContractProofCacheForTest();
      const publishedEventId = "0198abcd-2222-7000-8000-000000000005";
      const releaseEventId = status === "removed"
        ? "0198abcd-2222-7000-8000-000000000006"
        : "0198abcd-2222-7000-8000-000000000002";
      const releaseBarrier = {
        ...LEGACY_RELEASE_STATUS_VIEW,
        status,
        release_revision: status === "removed" ? 2 : 1,
        release_event_id: releaseEventId,
        supersedes_release_event_id: status === "removed" ? publishedEventId : null,
      };
      const fetcher = sequence([
        json({ object: { sha: HEAD } }),
        json({ tree: { sha: TREE } }),
        ...resultOwnerContractProofResponses(),
        contents(guard),
        contents(pending),
        contents(overlay),
        contents(releaseBarrier),
        new Response(null, { status: 404 }),
        contents(authority),
        contents(status === "removed"
          ? removedReleaseEvent(releaseEventId, publishedEventId)
          : {
              schema_version: 1,
              event_id: releaseEventId,
              event_type: status === "running" ? "release.started" : "release.published",
              occurred_at: "2026-08-20T06:07:08.500Z",
              subject_id: LEGACY_RESULT.resultId,
              causation_event_id: CLAIM_EVENT_ID,
              actor: { kind: "system" },
              payload: status === "running"
                ? { attempt: 1 }
                : {
                    attempt: 1,
                    repository_commit: "9".repeat(40),
                    tree_digest: "8".repeat(64),
                    path: "releases/test",
                  },
            }),
        ...(status === "removed"
          ? [
              contents(publishedReleaseEvent(publishedEventId)),
              contents(startedReleaseEvent("0198abcd-2222-7000-8000-000000000004")),
            ]
          : []),
        contents(requestEvent),
      ]);
      await expect(repository(fetcher).decideResultProblemRepair(decision)).rejects.toMatchObject({ status: 409 });
      expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    }
  });

  it("derives modern result retraction authority from the recorded submission view", async () => {
    const requestId = "0198abcd-1111-7000-8000-000000000008";
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(recordedGuard(RESULT_ID, RESULT_EVENT.event_id)),
      contents(RESULT_AMENDMENT_VIEW),
      new Response(null, { status: 404 }),
      contents(RESULT_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(RESULT_EVENT),
      contents(RESULT_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(RESULT_EVENT),
      contents(EVALUATION_STARTED),
      contents(RELEASE_EVENT),
      contents(RESULT_EVENT),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).requestResultRetraction({
      eventId: requestId,
      occurredAt: "2026-08-20T06:07:12.000Z",
      resultId: RESULT_ID,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: RESULT_ID,
      mutationEventId: requestId,
      retractionRevision: 1,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string; content: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${requestId}.json`,
      `views/result-amendments/aa/${RESULT_ID}.json`,
    ]);
    expect(JSON.parse(tree.tree[0]?.content ?? "null")).toMatchObject({
      event_type: "result.retraction_requested",
      subject_id: RESULT_ID,
      causation_event_id: RESULT_EVENT.event_id,
      actor: { kind: "github", login: "alice" },
    });
  });

  it("requests a modern problem repair from an exact not-scheduled release view", async () => {
    const requestId = "0198abcd-1111-7000-8000-000000000008";
    const notScheduled = initialResultReleaseStatusView(
      RESULT_ID,
      RESULT_EVENT.event_id,
    );
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(recordedGuard(RESULT_ID, RESULT_EVENT.event_id)),
      contents(RESULT_AMENDMENT_VIEW),
      new Response(null, { status: 404 }),
      contents(notScheduled),
      new Response(null, { status: 404 }),
      contents(RESULT_EVENT),
      contents(RESULT_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(RESULT_EVENT),
      contents(EVALUATION_STARTED),
      contents(RESULT_EVENT),
      json({ sha: NEW_TREE }, 201),
      json({ sha: NEW_COMMIT }, 201),
      json({ object: { sha: NEW_COMMIT } }),
    ]);
    await expect(repository(fetcher).requestResultProblemRepair({
      eventId: requestId,
      occurredAt: "2026-08-20T06:07:12.000Z",
      resultId: RESULT_ID,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 3,
      reasonCode: "wrong_problem_revision",
    })).resolves.toEqual({
      commit: NEW_COMMIT,
      created: true,
      resultId: RESULT_ID,
      mutationEventId: requestId,
      repairRevision: 1,
    });
    expect(notScheduled).toMatchObject({
      status: "not_scheduled",
      release_event_id: null,
    });
    const treeRequest = fetcher.mock.calls.find(([input, init]) =>
      fetchUrl(input).endsWith("/git/trees") && init?.method === "POST")?.[1]?.body;
    if (typeof treeRequest !== "string") throw new TypeError("tree body was not text");
    const tree = JSON.parse(treeRequest) as { tree: { path: string }[] };
    expect(tree.tree.map((entry) => entry.path)).toEqual([
      `events/01/${requestId}.json`,
      `views/result-amendments/aa/${RESULT_ID}.json`,
    ]);
    expect(treeRequest).not.toContain("views/result-release-status/");
  });

  it("rejects a modern result whose targeted release-status view is missing", async () => {
    clearResultOwnerContractProofCacheForTest();
    const fetcher = sequence([
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(recordedGuard(RESULT_ID, RESULT_EVENT.event_id)),
      contents(RESULT_AMENDMENT_VIEW),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      new Response(null, { status: 404 }),
      contents(RESULT_EVENT),
      contents(RESULT_VIEW),
      contents(RECEIVED),
      contents(METADATA),
      contents(ARCHIVE_EVENT),
      contents(EVALUATION_ACCEPTED),
      contents(RESULT_EVENT),
      contents(EVALUATION_STARTED),
    ]);
    await expect(repository(fetcher).requestResultProblemRepair({
      eventId: "0198abcd-1111-7000-8000-000000000008",
      occurredAt: "2026-08-20T06:07:12.000Z",
      resultId: RESULT_ID,
      ownerLogin: "alice",
      correctedProblemId: "two_plus_three",
      correctedStatementRevision: 3,
      reasonCode: "wrong_problem_revision",
    })).rejects.toBeInstanceOf(StateEventConflictError);
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("makes an exact retraction replay idempotent and rejects changed request material", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority: StateEvent = {
      schema_version: 1,
      event_id: CLAIM_EVENT_ID,
      event_type: "result.claimed",
      occurred_at: "2026-08-20T06:07:08.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: LEGACY_RESULT.baseResult,
    };
    const requestId = "0198abcd-2222-7000-8000-000000000003";
    const event: StateEvent = {
      schema_version: 1,
      event_id: requestId,
      event_type: "result.retraction_requested",
      occurred_at: "2026-08-20T06:07:09.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: CLAIM_EVENT_ID,
      actor: { kind: "github", login: "alice" },
      payload: { retraction_revision: 1, reason_code: "owner_requested_withdrawal" },
    };
    const initial = initialResultAmendmentView({
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      declaredModel: LEGACY_RESULT.baseResult.declared_model,
      authorityEventId: CLAIM_EVENT_ID,
      mutationEventId: CLAIM_EVENT_ID,
      problemId: LEGACY_RESULT.baseResult.problem_id,
      statementRevision: LEGACY_RESULT.baseResult.statement_revision,
    });
    const pending = requestedRetractionView(
      initial,
      requestId,
      event.occurred_at,
      "owner_requested_withdrawal",
    );
    const responses = () => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(pending),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      contents(event),
      contents(authority),
      contents(event),
    ];
    const exact = sequence(responses());
    await expect(repository(exact).requestResultRetraction({
      eventId: requestId,
      occurredAt: "2026-08-20T06:08:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    })).resolves.toMatchObject({ created: false, retractionRevision: 1 });
    expect(exact.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    clearResultOwnerContractProofCacheForTest();
    const changed = sequence(responses());
    await expect(repository(changed).requestResultRetraction({
      eventId: requestId,
      occurredAt: "2026-08-20T06:08:00.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      reasonCode: "different_reason",
    })).rejects.toBeInstanceOf(StateEventConflictError);
  });

  it("conceals a result owned by someone else and rejects a stale causal UUID", async () => {
    const guard = claimedGuard(LEGACY_RESULT.resultId, CLAIM_EVENT_ID);
    const overlay = claimedOverlay(
      LEGACY_RESULT,
      CLAIM_EVENT_ID,
      "2026-08-20T06:07:08.000Z",
    );
    const authority: StateEvent = {
      schema_version: 1,
      event_id: CLAIM_EVENT_ID,
      event_type: "result.claimed",
      occurred_at: "2026-08-20T06:07:08.000Z",
      subject_id: LEGACY_RESULT.resultId,
      causation_event_id: null,
      actor: { kind: "github", login: "alice" },
      payload: LEGACY_RESULT.baseResult,
    };
    const baseResponses = () => [
      json({ object: { sha: HEAD } }),
      json({ tree: { sha: TREE } }),
      ...resultOwnerContractProofResponses(),
      contents(guard),
      contents(LEGACY_AMENDMENT_VIEW),
      contents(overlay),
      contents(LEGACY_RELEASE_STATUS_VIEW),
      new Response(null, { status: 404 }),
      contents(authority),
      contents(authority),
    ];
    const hidden = sequence(baseResponses());
    await expect(repository(hidden).requestResultRetraction({
      eventId: "0198abcd-2222-7000-8000-000000000003",
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "mallory",
      reasonCode: "owner_requested_withdrawal",
    })).rejects.toMatchObject({ status: 404 });

    clearResultOwnerContractProofCacheForTest();
    const stale = sequence(baseResponses());
    await expect(repository(stale).requestResultRetraction({
      eventId: "0198abcd-2222-7000-8000-000000000000",
      occurredAt: "2026-08-20T06:07:09.000Z",
      resultId: LEGACY_RESULT.resultId,
      ownerLogin: "alice",
      reasonCode: "owner_requested_withdrawal",
    })).rejects.toMatchObject({ status: 409 });
  });
});
