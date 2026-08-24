const COMMIT = /^[0-9a-f]{40}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const RUN = /^[1-9][0-9]*$/;
const UUID_V7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export const INTAKE_LEASE_MAX_SECONDS = 900;

export type IntakeLeaseEnvironment = Readonly<{
  DEPLOYED_COMMIT: string;
  DEPLOYMENT_ENVIRONMENT: "staging" | "production";
  INTAKE_ENABLED: string;
  INTAKE_ENABLEMENT_MODE?: string;
  INTAKE_LEASE_CONTROLLER_COMMIT?: string;
  INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT?: string;
  INTAKE_LEASE_CONTROLLER_RUN_ID?: string;
  INTAKE_LEASE_EVENT_ID?: string;
  INTAKE_LEASE_EXPIRES_AT?: string;
  INTAKE_LEASE_ISSUED_AT?: string;
  INTAKE_LEASE_NONCE_DIGEST?: string;
  INTAKE_LEASE_STATE_COMMIT?: string;
  INTAKE_LEASE_TARGET_COMMIT?: string;
}>;

export type IntakeEnablement = Readonly<{
  configured: boolean;
  effective: boolean;
  mode: "disabled" | "durable" | "leased" | "invalid";
  leaseExpiresAt: number | null;
}>;

function closedLease(env: IntakeLeaseEnvironment): boolean {
  return [
    env.INTAKE_LEASE_CONTROLLER_COMMIT,
    env.INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT,
    env.INTAKE_LEASE_CONTROLLER_RUN_ID,
    env.INTAKE_LEASE_EVENT_ID,
    env.INTAKE_LEASE_EXPIRES_AT,
    env.INTAKE_LEASE_ISSUED_AT,
    env.INTAKE_LEASE_NONCE_DIGEST,
    env.INTAKE_LEASE_STATE_COMMIT,
    env.INTAKE_LEASE_TARGET_COMMIT,
  ].every((value) => value === undefined || value === "");
}

export function intakeEnablement(
  env: IntakeLeaseEnvironment,
  nowMilliseconds: number,
): IntakeEnablement {
  const configured = env.INTAKE_ENABLED === "true";
  const configuredCanonical = configured || env.INTAKE_ENABLED === "false";
  const mode = env.INTAKE_ENABLEMENT_MODE;
  if (!configuredCanonical || !Number.isFinite(nowMilliseconds)) {
    return { configured, effective: false, mode: "invalid", leaseExpiresAt: null };
  }
  if (!configured) {
    return mode === "disabled" && closedLease(env)
      ? { configured: false, effective: false, mode: "disabled", leaseExpiresAt: null }
      : { configured: false, effective: false, mode: "invalid", leaseExpiresAt: null };
  }
  if (mode === "durable") {
    return closedLease(env)
      ? { configured: true, effective: true, mode: "durable", leaseExpiresAt: null }
      : { configured: true, effective: false, mode: "invalid", leaseExpiresAt: null };
  }
  const issuedAt = Number(env.INTAKE_LEASE_ISSUED_AT);
  const expiresAt = Number(env.INTAKE_LEASE_EXPIRES_AT);
  const canonicalEpoch = (raw: string | undefined, value: number): boolean =>
    raw !== undefined && /^(?:1[0-9]{9}|[2-8][0-9]{9})$/.test(raw) && Number.isSafeInteger(value);
  const validLease =
    mode === "leased" &&
    env.DEPLOYMENT_ENVIRONMENT === "production" &&
    COMMIT.test(env.DEPLOYED_COMMIT) &&
    env.INTAKE_LEASE_TARGET_COMMIT === env.DEPLOYED_COMMIT &&
    COMMIT.test(env.INTAKE_LEASE_CONTROLLER_COMMIT ?? "") &&
    env.INTAKE_LEASE_CONTROLLER_COMMIT === env.INTAKE_LEASE_TARGET_COMMIT &&
    RUN.test(env.INTAKE_LEASE_CONTROLLER_RUN_ID ?? "") &&
    RUN.test(env.INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT ?? "") &&
    COMMIT.test(env.INTAKE_LEASE_STATE_COMMIT ?? "") &&
    DIGEST.test(env.INTAKE_LEASE_NONCE_DIGEST ?? "") &&
    UUID_V7.test(env.INTAKE_LEASE_EVENT_ID ?? "") &&
    canonicalEpoch(env.INTAKE_LEASE_ISSUED_AT, issuedAt) &&
    canonicalEpoch(env.INTAKE_LEASE_EXPIRES_AT, expiresAt) &&
    expiresAt > issuedAt &&
    expiresAt - issuedAt <= INTAKE_LEASE_MAX_SECONDS;
  if (!validLease) {
    return { configured: true, effective: false, mode: "invalid", leaseExpiresAt: null };
  }
  const now = Math.floor(nowMilliseconds / 1000);
  return {
    configured: true,
    effective: now >= issuedAt && now < expiresAt,
    mode: "leased",
    leaseExpiresAt: expiresAt,
  };
}
