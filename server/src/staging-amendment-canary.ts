import { ApiDecodeError, isUuidV7 } from "./api-contract";

export const STAGING_CANARY_STATE_REPOSITORY = "leanprover/lean-eval-state-staging";
export const STAGING_CANARY_OWNER = "kim-em";
export const STAGING_CANARY_REVIEWER = "kim-em";
export const STAGING_CANARY_RESULTS_COMMIT = "972178d59e2b3c5300baa728a1356f0d49dafb87";
export const STAGING_CANARY_PROBLEM = "list_append_singleton_length";
export const STAGING_CANARY_STATEMENT_REVISION = 1;

export const STAGING_CANARY_TARGETS = {
  apply: {
    resultId: "r2_99df81809318fd2673d82da042b451f77b55606c6b506beb4526828ee1e7079e",
    declaredModel: "Codex staging fixture",
    authorityEventId: "01a02cb1-5db5-7570-ae43-4974ba79cd20",
    candidateIdentityId: "eri1_362e69696a5c468d0482086b6eb3f24d68dea6b4795284a017096b092a800775",
  },
  reject: {
    resultId: "r2_3f28ce10fd9bad352dc29394254ec7c414b57269757c3488cd108bd544186423",
    declaredModel: "Codex release staging fixture",
    authorityEventId: "01a02cd6-e912-75b3-a5cf-86043b938b29",
    candidateIdentityId: "eri1_b1f3167cd78dcdcef990d5b09ae447bdf3e470f60236c6a2be2009a260a6127a",
  },
} as const;

export type StagingAmendmentCanaryOperation =
  | "request_apply"
  | "apply"
  | "request_reject"
  | "reject";

export type StagingAmendmentCanaryRequest = Readonly<{
  schema_version: 1;
  operation: StagingAmendmentCanaryOperation;
  deployed_commit: string;
  expected_state_commit: string;
  event_id: string;
  occurred_at: string;
  expected_request_event_id: string | null;
}>;

function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiDecodeError("staging amendment canary request must be an object");
  }
  return value as Record<string, unknown>;
}

export function decodeStagingAmendmentCanaryRequest(
  value: unknown,
): StagingAmendmentCanaryRequest {
  const data = object(value);
  const fields = [
    "deployed_commit",
    "event_id",
    "expected_request_event_id",
    "expected_state_commit",
    "occurred_at",
    "operation",
    "schema_version",
  ];
  if (JSON.stringify(Object.keys(data).sort()) !== JSON.stringify(fields)) {
    throw new ApiDecodeError("staging amendment canary request fields are not canonical");
  }
  const operations = new Set<unknown>([
    "request_apply", "apply", "request_reject", "reject",
  ]);
  const occurredAt = typeof data.occurred_at === "string" ? new Date(data.occurred_at) : null;
  const decision = data.operation === "apply" || data.operation === "reject";
  if (
    data.schema_version !== 1 ||
    !operations.has(data.operation) ||
    typeof data.deployed_commit !== "string" ||
    !/^[0-9a-f]{40}$/.test(data.deployed_commit) ||
    typeof data.expected_state_commit !== "string" ||
    !/^[0-9a-f]{40}$/.test(data.expected_state_commit) ||
    typeof data.event_id !== "string" ||
    !isUuidV7(data.event_id) ||
    occurredAt === null ||
    Number.isNaN(occurredAt.valueOf()) ||
    occurredAt.toISOString() !== data.occurred_at ||
    (decision
      ? typeof data.expected_request_event_id !== "string" ||
        !isUuidV7(data.expected_request_event_id)
      : data.expected_request_event_id !== null)
  ) {
    throw new ApiDecodeError("staging amendment canary request is not canonical");
  }
  return data as StagingAmendmentCanaryRequest;
}
