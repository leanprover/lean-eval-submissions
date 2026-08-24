import { ApiDecodeError, isUuidV7 } from "./api-contract";

export const STAGING_CANARY_STATE_REPOSITORY = "leanprover/lean-eval-state-staging";
export const STAGING_CANARY_OWNER = "kim-em";
export const STAGING_CANARY_REVIEWER = "kim-em";
export const STAGING_CANARY_RESULTS_COMMIT = "972178d59e2b3c5300baa728a1356f0d49dafb87";
export const STAGING_CANARY_PROBLEM = "list_append_singleton_length";
export const STAGING_CANARY_STATEMENT_REVISION = 1;

export const STAGING_CANARY_INTENTS = {
  request_apply: {
    eventId: "01a035b4-d6ce-7213-8dc6-6e140474e02e",
    occurredAt: "2026-08-24T21:37:19.054Z",
    expectedRequestEventId: null,
  },
  apply: {
    eventId: "01a035b4-d6cf-718a-b5af-c903c1b66336",
    occurredAt: "2026-08-24T21:37:19.055Z",
    expectedRequestEventId: "01a035b4-d6ce-7213-8dc6-6e140474e02e",
  },
  request_reject: {
    eventId: "01a035b4-d6d0-786d-bd03-5018f6ea4de6",
    occurredAt: "2026-08-24T21:37:19.056Z",
    expectedRequestEventId: null,
  },
  reject: {
    eventId: "01a035b4-d6d1-7f6f-b93f-29306171a7cf",
    occurredAt: "2026-08-24T21:37:19.057Z",
    expectedRequestEventId: "01a035b4-d6d0-786d-bd03-5018f6ea4de6",
  },
} as const;

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
    "expected_state_commit",
    "operation",
    "schema_version",
  ];
  if (JSON.stringify(Object.keys(data).sort()) !== JSON.stringify(fields)) {
    throw new ApiDecodeError("staging amendment canary request fields are not canonical");
  }
  const operations = new Set<unknown>([
    "request_apply", "apply", "request_reject", "reject",
  ]);
  if (
    data.schema_version !== 1 ||
    !operations.has(data.operation) ||
    typeof data.deployed_commit !== "string" ||
    !/^[0-9a-f]{40}$/.test(data.deployed_commit) ||
    typeof data.expected_state_commit !== "string" ||
    !/^[0-9a-f]{40}$/.test(data.expected_state_commit)
  ) {
    throw new ApiDecodeError("staging amendment canary request is not canonical");
  }
  return data as StagingAmendmentCanaryRequest;
}

const orderedIntents = Object.values(STAGING_CANARY_INTENTS);
for (const intent of orderedIntents) {
  if (
    !isUuidV7(intent.eventId) ||
    Date.parse(intent.occurredAt) !== Number.parseInt(intent.eventId.replaceAll("-", "").slice(0, 12), 16) ||
    (intent.expectedRequestEventId !== null && !isUuidV7(intent.expectedRequestEventId))
  ) {
    throw new Error("staging amendment canary intent is invalid");
  }
}
const orderedEventIds = orderedIntents.map((intent) => intent.eventId);
const orderedTimestamps = orderedIntents.map((intent) => Date.parse(intent.occurredAt));
if (
  JSON.stringify(orderedEventIds) !== JSON.stringify([...orderedEventIds].sort()) ||
  new Set(orderedEventIds).size !== orderedEventIds.length ||
  orderedTimestamps.some((timestamp, index) => {
    const previous = orderedTimestamps[index - 1];
    return previous !== undefined && timestamp <= previous;
  })
) {
  throw new Error("staging amendment canary intents are not strictly ordered");
}
