import { describe, expect, it } from "vitest";

import {
  INTAKE_LEASE_MAX_SECONDS,
  intakeEnablement,
  type IntakeLeaseEnvironment,
} from "../src/intake-enablement";

const ISSUED = 1_777_777_777;
const EXPIRES = ISSUED + INTAKE_LEASE_MAX_SECONDS;
const LEASED = {
  DEPLOYED_COMMIT: "a".repeat(40),
  DEPLOYMENT_ENVIRONMENT: "production",
  INTAKE_ENABLED: "true",
  INTAKE_ENABLEMENT_MODE: "leased",
  INTAKE_LEASE_CONTROLLER_COMMIT: "a".repeat(40),
  INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT: "2",
  INTAKE_LEASE_CONTROLLER_RUN_ID: "123456",
  INTAKE_LEASE_EVENT_ID: "0198abcd-1111-7000-8000-000000000001",
  INTAKE_LEASE_EXPIRES_AT: String(EXPIRES),
  INTAKE_LEASE_ISSUED_AT: String(ISSUED),
  INTAKE_LEASE_NONCE_DIGEST: "c".repeat(64),
  INTAKE_LEASE_STATE_COMMIT: "d".repeat(40),
  INTAKE_LEASE_TARGET_COMMIT: "a".repeat(40),
} satisfies IntakeLeaseEnvironment;

describe("Worker-enforced intake enablement", () => {
  it("is effective only inside the exact finite lease interval", () => {
    expect(intakeEnablement(LEASED, (ISSUED - 1) * 1000).effective).toBe(false);
    expect(intakeEnablement(LEASED, ISSUED * 1000)).toMatchObject({
      configured: true,
      effective: true,
      mode: "leased",
      leaseExpiresAt: EXPIRES,
    });
    expect(intakeEnablement(LEASED, EXPIRES * 1000 - 1).effective).toBe(true);
    expect(intakeEnablement(LEASED, EXPIRES * 1000).effective).toBe(false);
  });

  it("fails closed for malformed, overlong, staging, or cross-bound lease material", () => {
    const invalid: IntakeLeaseEnvironment[] = [
      { ...LEASED, INTAKE_ENABLED: "TRUE" },
      { ...LEASED, INTAKE_ENABLEMENT_MODE: "durable" },
      { ...LEASED, DEPLOYMENT_ENVIRONMENT: "staging" },
      { ...LEASED, INTAKE_LEASE_TARGET_COMMIT: "e".repeat(40) },
      { ...LEASED, INTAKE_LEASE_CONTROLLER_COMMIT: "e".repeat(40) },
      { ...LEASED, INTAKE_LEASE_CONTROLLER_RUN_ATTEMPT: "0" },
      { ...LEASED, INTAKE_LEASE_NONCE_DIGEST: "not-a-digest" },
      { ...LEASED, INTAKE_LEASE_EXPIRES_AT: String(EXPIRES + 1) },
      { ...LEASED, INTAKE_LEASE_ISSUED_AT: `${String(ISSUED)}.0` },
    ];
    for (const environment of invalid) {
      expect(intakeEnablement(environment, ISSUED * 1000)).toMatchObject({
        effective: false,
        mode: "invalid",
      });
    }
  });

  it("allows durable enablement only with no residual lease bindings", () => {
    const durable = {
      DEPLOYED_COMMIT: "a".repeat(40),
      DEPLOYMENT_ENVIRONMENT: "production",
      INTAKE_ENABLED: "true",
      INTAKE_ENABLEMENT_MODE: "durable",
    } satisfies IntakeLeaseEnvironment;
    expect(intakeEnablement(durable, ISSUED * 1000)).toEqual({
      configured: true,
      effective: true,
      mode: "durable",
      leaseExpiresAt: null,
    });
    expect(intakeEnablement({ ...durable, INTAKE_LEASE_EVENT_ID: LEASED.INTAKE_LEASE_EVENT_ID }, ISSUED * 1000))
      .toMatchObject({ effective: false, mode: "invalid" });
  });

  it("requires the closed canonical disabled configuration", () => {
    const disabled = {
      DEPLOYED_COMMIT: "development",
      DEPLOYMENT_ENVIRONMENT: "staging",
      INTAKE_ENABLED: "false",
      INTAKE_ENABLEMENT_MODE: "disabled",
    } satisfies IntakeLeaseEnvironment;
    expect(intakeEnablement(disabled, ISSUED * 1000)).toEqual({
      configured: false,
      effective: false,
      mode: "disabled",
      leaseExpiresAt: null,
    });
    expect(intakeEnablement({ ...disabled, INTAKE_LEASE_EXPIRES_AT: String(EXPIRES) }, ISSUED * 1000))
      .toMatchObject({ effective: false, mode: "invalid" });
  });
});
