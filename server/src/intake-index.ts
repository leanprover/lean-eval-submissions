import type { SubmissionInput } from "./api-contract";
import type { SubmissionView } from "./submission-view";

const CLAIM = /^ic1_[0-9a-f]{64}$/;
const OWNER = /^io1_[0-9a-f]{64}$/;
const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const REPOSITORY = /^[a-z0-9_.-]+\/[a-z0-9_.-]+$/;
const COMMIT = /^[0-9a-f]{40}$/;
const PROBLEM = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const UUID_V7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const CLAIM_DOMAIN = "lean-eval-intake-identity-v1\0";
const OWNER_DOMAIN = "lean-eval-intake-owner-v1\0";

export const ACTIVE_SUBMISSION_LIMIT = 4;
export const INCOMPLETE_SUBMISSION_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export type IntakeClaim = Readonly<{
  schema_version: 1;
  claim_id: string;
  owner_login: string;
  source_repository: string;
  source_commit: string;
  problem_id: string;
  statement_revision: number;
  submission_id: string;
}>;

export type IntakeOwner = Readonly<{
  schema_version: 1;
  owner_id: string;
  owner_login: string;
  submission_ids: readonly string[];
}>;

async function digest(domain: string, value: unknown): Promise<string> {
  const bytes = new Uint8Array(await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(domain + JSON.stringify(value)),
  ));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function intakeClaim(
  ownerLogin: string,
  submission: SubmissionInput,
  submissionId: string,
): Promise<IntakeClaim> {
  const identity = [
    ownerLogin.toLowerCase(),
    submission.source_repository.toLowerCase(),
    submission.source_commit,
    submission.problem_id,
    submission.statement_revision,
  ];
  return {
    schema_version: 1,
    claim_id: `ic1_${await digest(CLAIM_DOMAIN, identity)}`,
    owner_login: identity[0] as string,
    source_repository: identity[1] as string,
    source_commit: identity[2] as string,
    problem_id: identity[3] as string,
    statement_revision: identity[4] as number,
    submission_id: submissionId,
  };
}

export async function intakeOwner(ownerLogin: string, submissionIds: readonly string[]): Promise<IntakeOwner> {
  const login = ownerLogin.toLowerCase();
  return {
    schema_version: 1,
    owner_id: `io1_${await digest(OWNER_DOMAIN, login)}`,
    owner_login: login,
    submission_ids: [...new Set(submissionIds)].sort(),
  };
}

export function intakeClaimPath(claimId: string): string {
  if (!CLAIM.test(claimId)) throw new TypeError("intake claim id is invalid");
  return `views/intake-claims/${claimId.slice(4, 6)}/${claimId}.json`;
}

export function intakeOwnerPath(ownerId: string): string {
  if (!OWNER.test(ownerId)) throw new TypeError("intake owner id is invalid");
  return `views/intake-owners/${ownerId.slice(4, 6)}/${ownerId}.json`;
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

export function decodeIntakeClaim(value: unknown): IntakeClaim {
  const data = record(value, "intake claim");
  const expected = [
    "claim_id", "owner_login", "problem_id", "schema_version", "source_commit",
    "source_repository", "statement_revision", "submission_id",
  ];
  if (Object.keys(data).sort().join(",") !== expected.join(",") ||
    data.schema_version !== 1 || typeof data.claim_id !== "string" || !CLAIM.test(data.claim_id) ||
    typeof data.owner_login !== "string" || !LOGIN.test(data.owner_login) ||
    typeof data.source_repository !== "string" || !REPOSITORY.test(data.source_repository) ||
    typeof data.source_commit !== "string" || !COMMIT.test(data.source_commit) ||
    typeof data.problem_id !== "string" || !PROBLEM.test(data.problem_id) ||
    typeof data.statement_revision !== "number" || !Number.isSafeInteger(data.statement_revision) || data.statement_revision < 1 ||
    typeof data.submission_id !== "string" || !UUID_V7.test(data.submission_id)) {
    throw new TypeError("intake claim is invalid");
  }
  return data as IntakeClaim;
}

export function decodeIntakeOwner(value: unknown): IntakeOwner {
  const data = record(value, "intake owner");
  if (Object.keys(data).sort().join(",") !== "owner_id,owner_login,schema_version,submission_ids" ||
    data.schema_version !== 1 || typeof data.owner_id !== "string" || !OWNER.test(data.owner_id) ||
    typeof data.owner_login !== "string" || !LOGIN.test(data.owner_login) ||
    !Array.isArray(data.submission_ids) || data.submission_ids.length > 4096 ||
    data.submission_ids.some((id) => typeof id !== "string" || !UUID_V7.test(id)) ||
    data.submission_ids.join(",") !== [...new Set(data.submission_ids)].sort().join(",")) {
    throw new TypeError("intake owner is invalid");
  }
  return data as IntakeOwner;
}

export function submissionIsActive(view: SubmissionView, nowMilliseconds: number): boolean {
  if (nowMilliseconds - Date.parse(view.accepted_at) > INCOMPLETE_SUBMISSION_MAX_AGE_MS) return false;
  if (view.schema_version === 3 || view.result_id !== null) return false;
  if (view.archive.status === "failed" && !view.archive.retryable) return false;
  if (view.evaluation.status === "rejected") return false;
  if (view.evaluation.status === "failed" && !view.evaluation.retryable) return false;
  if (view.dispatch.status === "failed" && view.dispatch.attempts >= 32) return false;
  return true;
}
