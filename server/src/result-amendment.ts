const RESULT_ID = /^r2_[0-9a-f]{64}$/;
const EVENT_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const PROBLEM = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const REASON = /^[a-z][a-z0-9_]{1,63}$/;
const REPOSITORY = /^(?!\.{1,2}\/)(?![^/]+\/\.{1,2}$)[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const COMMIT = /^[0-9a-f]{40}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const CHALLENGE_ID = /^ch1_[0-9a-f]{64}$/;
const TIMESTAMP =
  /^(?!0000-)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$/;

export type RetractionState = Readonly<{
  revision: number;
  status: "pending" | "approved" | "rejected" | "retracted";
  request_event_id: string | null;
  requested_at: string | null;
  decision_event_id: string | null;
  decided_at: string | null;
  retraction_event_id: string | null;
  retracted_at: string | null;
  reviewer_login: string | null;
  reason_code: string | null;
  release_disposition: "not_published" | "removal_required" | "already_removed" | null;
  overridden: boolean;
}>;

export type ProblemRepairState = Readonly<{
  revision: number;
  status: "pending" | "applied" | "rejected";
  request_event_id: string;
  requested_at: string;
  corrected_problem_id: string;
  corrected_statement_revision: number;
  decision_event_id: string | null;
  decided_at: string | null;
  reviewer_login: string | null;
  reason_code: string | null;
  comparator_evidence: ComparatorEvidence | null;
}>;

export type ComparatorEvidence = Readonly<{
  repository: string;
  commit: string;
  path: string;
  blob_oid: string;
  blob_sha256: string;
  record_sha256: string;
  binding_sha256: string;
  verification_method: "github_commit_blob_v1";
  evidence_result_id: string;
  evidence_owner_login: string;
  evidence_declared_model: string;
  evidence_base_problem_group: "formalization-evaluation" | "software-verification" | "open-conjectures";
  evidence_base_problem_id: string;
  evidence_base_statement_revision: number;
  evidence_base_challenge_id: string;
  evidence_corrected_problem_group: "formalization-evaluation" | "software-verification" | "open-conjectures";
  evidence_corrected_problem_id: string;
  evidence_corrected_statement_revision: number;
  evidence_corrected_challenge_id: string;
}>;

export type ResultAmendmentView = Readonly<{
  schema_version: 1;
  result_id: string;
  owner_login: string;
  declared_model: string;
  authority_event_id: string;
  base_problem_id: string;
  base_statement_revision: number;
  effective_problem_id: string;
  effective_statement_revision: number;
  mutation_event_id: string;
  problem_repair: ProblemRepairState | null;
  applied_problem_repair: ProblemRepairState | null;
  retraction: RetractionState | null;
  leaderboard_eligible: boolean;
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

function positive(value: unknown, label: string): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new TypeError(`${label} must be a positive safe integer`);
  }
}

function nullablePattern(value: unknown, pattern: RegExp, label: string): void {
  if (value !== null && (typeof value !== "string" || !pattern.test(value))) {
    throw new TypeError(`${label} is invalid`);
  }
}

function canonicalTimestamp(value: unknown, label: string): void {
  if (typeof value !== "string" || !TIMESTAMP.test(value) || new Date(value).toISOString() !== value) {
    throw new TypeError(`${label} is invalid`);
  }
}

function hasControlCharacter(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code <= 0x1f || code === 0x7f) return true;
  }
  return false;
}

function safePath(value: string): boolean {
  if (value.length === 0 || value.startsWith("/") || value.includes("//") || value.includes("\\")) {
    return false;
  }
  return !hasControlCharacter(value) && !value.split("/").some((component) => component === "." || component === "..");
}

function decodeComparatorEvidence(value: unknown, label: string): ComparatorEvidence | null {
  if (value === null) return null;
  const data = object(value, label);
  exact(data, [
    "binding_sha256", "blob_oid", "blob_sha256", "commit",
    "evidence_base_challenge_id", "evidence_base_problem_group", "evidence_base_problem_id",
    "evidence_base_statement_revision", "evidence_corrected_challenge_id",
    "evidence_corrected_problem_group", "evidence_corrected_problem_id",
    "evidence_corrected_statement_revision", "evidence_declared_model", "evidence_owner_login",
    "evidence_result_id", "path", "record_sha256", "repository", "verification_method",
  ], label);
  positive(data.evidence_base_statement_revision, `${label}.evidence_base_statement_revision`);
  positive(data.evidence_corrected_statement_revision, `${label}.evidence_corrected_statement_revision`);
  const groups = new Set(["formalization-evaluation", "software-verification", "open-conjectures"]);
  if (
    typeof data.repository !== "string" || !REPOSITORY.test(data.repository) ||
    typeof data.commit !== "string" || !COMMIT.test(data.commit) ||
    typeof data.path !== "string" || !safePath(data.path) ||
    typeof data.blob_oid !== "string" || !COMMIT.test(data.blob_oid) ||
    typeof data.blob_sha256 !== "string" || !DIGEST.test(data.blob_sha256) ||
    typeof data.record_sha256 !== "string" || !DIGEST.test(data.record_sha256) ||
    typeof data.binding_sha256 !== "string" || !DIGEST.test(data.binding_sha256) ||
    data.verification_method !== "github_commit_blob_v1" ||
    typeof data.evidence_result_id !== "string" || !RESULT_ID.test(data.evidence_result_id) ||
    typeof data.evidence_owner_login !== "string" || !LOGIN.test(data.evidence_owner_login) ||
    typeof data.evidence_declared_model !== "string" || data.evidence_declared_model.length === 0 ||
    hasControlCharacter(data.evidence_declared_model) ||
    new TextEncoder().encode(data.evidence_declared_model).byteLength > 256 ||
    !groups.has(String(data.evidence_base_problem_group)) ||
    typeof data.evidence_base_problem_id !== "string" || !PROBLEM.test(data.evidence_base_problem_id) ||
    typeof data.evidence_base_challenge_id !== "string" || !CHALLENGE_ID.test(data.evidence_base_challenge_id) ||
    !groups.has(String(data.evidence_corrected_problem_group)) ||
    data.evidence_corrected_problem_group !== data.evidence_base_problem_group ||
    typeof data.evidence_corrected_problem_id !== "string" || !PROBLEM.test(data.evidence_corrected_problem_id) ||
    typeof data.evidence_corrected_challenge_id !== "string" || !CHALLENGE_ID.test(data.evidence_corrected_challenge_id)
  ) {
    throw new TypeError(`${label} values are invalid`);
  }
  return data as ComparatorEvidence;
}

function decodeRepair(value: unknown, label: string): ProblemRepairState | null {
  if (value === null) return null;
  const data = object(value, label);
  exact(data, [
    "comparator_evidence", "corrected_problem_id", "corrected_statement_revision",
    "decided_at", "decision_event_id", "reason_code", "request_event_id",
    "requested_at", "reviewer_login", "revision", "status",
  ], label);
  positive(data.revision, `${label}.revision`);
  positive(data.corrected_statement_revision, `${label}.corrected_statement_revision`);
  if (
    !new Set(["pending", "applied", "rejected"]).has(String(data.status)) ||
    typeof data.request_event_id !== "string" || !EVENT_ID.test(data.request_event_id) ||
    typeof data.corrected_problem_id !== "string" || !PROBLEM.test(data.corrected_problem_id)
  ) {
    throw new TypeError(`${label} values are invalid`);
  }
  canonicalTimestamp(data.requested_at, `${label}.requested_at`);
  nullablePattern(data.decision_event_id, EVENT_ID, `${label}.decision_event_id`);
  nullablePattern(data.decided_at, TIMESTAMP, `${label}.decided_at`);
  nullablePattern(data.reviewer_login, LOGIN, `${label}.reviewer_login`);
  nullablePattern(data.reason_code, REASON, `${label}.reason_code`);
  const comparatorEvidence = decodeComparatorEvidence(data.comparator_evidence, `${label}.comparator_evidence`);
  if (data.status === "pending") {
    if (
      data.decision_event_id !== null || data.decided_at !== null ||
      data.reviewer_login !== null || data.reason_code === null ||
      comparatorEvidence !== null
    ) {
      throw new TypeError(`${label} pending state contains decision fields`);
    }
  } else if (
    data.decision_event_id === null || data.decided_at === null ||
    data.reviewer_login === null ||
    (data.status === "applied" && (data.reason_code !== null || comparatorEvidence === null)) ||
    (data.status === "rejected" && (data.reason_code === null || comparatorEvidence !== null))
  ) {
    throw new TypeError(`${label} decided state is inconsistent`);
  } else if (data.status !== "applied" && comparatorEvidence !== null) {
    throw new TypeError(`${label} non-applied state contains comparator evidence`);
  }
  return { ...data, comparator_evidence: comparatorEvidence } as ProblemRepairState;
}

function decodeRetraction(value: unknown): RetractionState | null {
  if (value === null) return null;
  const data = object(value, "result amendment retraction");
  exact(data, [
    "decided_at", "decision_event_id", "overridden", "reason_code",
    "release_disposition", "request_event_id", "requested_at", "retracted_at",
    "retraction_event_id", "reviewer_login", "revision", "status",
  ], "result amendment retraction");
  positive(data.revision, "result amendment retraction.revision");
  if (
    !new Set(["pending", "approved", "rejected", "retracted"]).has(String(data.status)) ||
    typeof data.overridden !== "boolean"
  ) {
    throw new TypeError("result amendment retraction values are invalid");
  }
  for (const [field, pattern] of [
    ["request_event_id", EVENT_ID], ["requested_at", TIMESTAMP],
    ["decision_event_id", EVENT_ID], ["decided_at", TIMESTAMP],
    ["retraction_event_id", EVENT_ID], ["retracted_at", TIMESTAMP],
    ["reviewer_login", LOGIN], ["reason_code", REASON],
  ] as const) nullablePattern(data[field], pattern, `result amendment retraction.${field}`);
  if (
    data.release_disposition !== null &&
    (typeof data.release_disposition !== "string" ||
      !new Set(["not_published", "removal_required", "already_removed"]).has(data.release_disposition))
  ) {
    throw new TypeError("result amendment retraction release_disposition is invalid");
  }
  const hasOwnerRequest = data.request_event_id !== null && data.requested_at !== null;
  const hasDecision = data.decision_event_id !== null && data.decided_at !== null &&
    data.reviewer_login !== null;
  const hasTerminal = data.retraction_event_id !== null && data.retracted_at !== null &&
    data.release_disposition !== null;
  const hasAnyDecision = data.decision_event_id !== null || data.decided_at !== null ||
    data.reviewer_login !== null;
  const hasAnyTerminal = data.retraction_event_id !== null || data.retracted_at !== null ||
    data.release_disposition !== null;
  if (data.request_event_id === null !== (data.requested_at === null)) {
    throw new TypeError("result amendment retraction owner request is incomplete");
  }
  if (
    data.status === "pending" &&
    (!hasOwnerRequest || hasAnyDecision || hasAnyTerminal || data.reason_code === null || data.overridden)
  ) {
    throw new TypeError("pending retraction state is inconsistent");
  }
  if (
    (data.status === "approved" || data.status === "rejected") &&
    (!hasDecision || hasAnyTerminal || data.reason_code === null ||
      (data.overridden ? hasOwnerRequest : !hasOwnerRequest) ||
      (data.status === "rejected" && data.overridden))
  ) {
    throw new TypeError("decided retraction state is inconsistent");
  }
  if (
    data.status === "retracted" &&
    (!hasDecision || !hasTerminal || data.reason_code === null ||
      (data.overridden ? hasOwnerRequest : !hasOwnerRequest))
  ) {
    throw new TypeError("terminal retraction state is inconsistent");
  }
  return data as RetractionState;
}

export function resultAmendmentPath(identifier: string): string {
  if (!RESULT_ID.test(identifier)) throw new TypeError("result identity is invalid");
  return `views/result-amendments/${identifier.slice(3, 5)}/${identifier}.json`;
}

export function decodeResultAmendmentView(value: unknown): ResultAmendmentView {
  const data = object(value, "result amendment view");
  exact(data, [
    "applied_problem_repair", "authority_event_id", "base_problem_id",
    "base_statement_revision", "declared_model", "effective_problem_id",
    "effective_statement_revision", "leaderboard_eligible", "mutation_event_id",
    "owner_login", "problem_repair", "result_id", "retraction", "schema_version",
  ], "result amendment view");
  positive(data.base_statement_revision, "result amendment base_statement_revision");
  positive(data.effective_statement_revision, "result amendment effective_statement_revision");
  if (
    data.schema_version !== 1 || typeof data.result_id !== "string" || !RESULT_ID.test(data.result_id) ||
    typeof data.owner_login !== "string" || !LOGIN.test(data.owner_login) ||
    typeof data.declared_model !== "string" || data.declared_model.length === 0 ||
    hasControlCharacter(data.declared_model) ||
    new TextEncoder().encode(data.declared_model).byteLength > 256 ||
    typeof data.authority_event_id !== "string" || !EVENT_ID.test(data.authority_event_id) ||
    typeof data.mutation_event_id !== "string" || !EVENT_ID.test(data.mutation_event_id) ||
    typeof data.base_problem_id !== "string" || !PROBLEM.test(data.base_problem_id) ||
    typeof data.effective_problem_id !== "string" || !PROBLEM.test(data.effective_problem_id) ||
    typeof data.leaderboard_eligible !== "boolean"
  ) {
    throw new TypeError("result amendment view values are invalid");
  }
  const problemRepair = decodeRepair(data.problem_repair, "result amendment problem_repair");
  const appliedRepair = decodeRepair(data.applied_problem_repair, "result amendment applied_problem_repair");
  if (appliedRepair !== null && appliedRepair.status !== "applied") {
    throw new TypeError("applied problem repair is not applied");
  }
  const retraction = decodeRetraction(data.retraction);
  if (data.leaderboard_eligible !== (retraction?.status !== "retracted")) {
    throw new TypeError("result amendment leaderboard eligibility is inconsistent");
  }
  if (appliedRepair === null) {
    if (
      data.effective_problem_id !== data.base_problem_id ||
      data.effective_statement_revision !== data.base_statement_revision ||
      problemRepair?.status === "applied"
    ) {
      throw new TypeError("result amendment effective problem lacks an applied repair");
    }
  } else {
    const evidence = appliedRepair.comparator_evidence;
    if (
      evidence === null ||
      data.effective_problem_id !== appliedRepair.corrected_problem_id ||
      data.effective_statement_revision !== appliedRepair.corrected_statement_revision ||
      evidence.evidence_result_id !== data.result_id ||
      evidence.evidence_owner_login !== data.owner_login ||
      evidence.evidence_declared_model !== data.declared_model ||
      evidence.evidence_base_problem_id !== data.base_problem_id ||
      evidence.evidence_base_statement_revision !== data.base_statement_revision ||
      evidence.evidence_corrected_problem_id !== appliedRepair.corrected_problem_id ||
      evidence.evidence_corrected_statement_revision !== appliedRepair.corrected_statement_revision ||
      (problemRepair?.status === "applied" &&
        problemRepair.decision_event_id !== appliedRepair.decision_event_id)
    ) {
      throw new TypeError("result amendment applied repair evidence is inconsistent");
    }
  }
  return { ...data, problem_repair: problemRepair, applied_problem_repair: appliedRepair, retraction } as ResultAmendmentView;
}

export function initialResultAmendmentView(input: Readonly<{
  resultId: string;
  ownerLogin: string;
  declaredModel: string;
  authorityEventId: string;
  mutationEventId: string;
  problemId: string;
  statementRevision: number;
}>): ResultAmendmentView {
  return decodeResultAmendmentView({
    schema_version: 1,
    result_id: input.resultId,
    owner_login: input.ownerLogin,
    declared_model: input.declaredModel,
    authority_event_id: input.authorityEventId,
    base_problem_id: input.problemId,
    base_statement_revision: input.statementRevision,
    effective_problem_id: input.problemId,
    effective_statement_revision: input.statementRevision,
    mutation_event_id: input.mutationEventId,
    problem_repair: null,
    applied_problem_repair: null,
    retraction: null,
    leaderboard_eligible: true,
  });
}

export function requestedRetractionView(
  current: ResultAmendmentView,
  eventId: string,
  occurredAt: string,
  reasonCode: string,
): ResultAmendmentView {
  const revision = (current.retraction?.revision ?? 0) + 1;
  return decodeResultAmendmentView({
    ...current,
    mutation_event_id: eventId,
    retraction: {
      revision,
      status: "pending",
      request_event_id: eventId,
      requested_at: occurredAt,
      decision_event_id: null,
      decided_at: null,
      retraction_event_id: null,
      retracted_at: null,
      reviewer_login: null,
      reason_code: reasonCode,
      release_disposition: null,
      overridden: false,
    },
  });
}
