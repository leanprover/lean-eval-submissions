import { AuthError, type UserSession } from "./auth";

const LOGIN = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/;
const MAX_MAINTAINERS = 16;

export type MaintainerIdentity = Readonly<{
  github_id: number;
  login: string;
}>;

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

export function decodeMaintainerIdentities(value: string | undefined): readonly MaintainerIdentity[] {
  if (value === undefined || new TextEncoder().encode(value).byteLength > 2048) {
    throw new TypeError("maintainer identity configuration is missing or too large");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch (error) {
    throw new TypeError("maintainer identity configuration is not JSON", { cause: error });
  }
  if (!Array.isArray(parsed) || parsed.length > MAX_MAINTAINERS) {
    throw new TypeError("maintainer identity configuration is not a bounded array");
  }
  const identities = parsed.map((raw, index): MaintainerIdentity => {
    const identity = object(raw, `maintainer identity ${String(index)}`);
    const fields = Object.keys(identity).sort();
    if (fields.length !== 2 || fields[0] !== "github_id" || fields[1] !== "login") {
      throw new TypeError(`maintainer identity ${String(index)} has unknown or missing fields`);
    }
    if (
      typeof identity.github_id !== "number" ||
      !Number.isSafeInteger(identity.github_id) ||
      identity.github_id < 1 ||
      typeof identity.login !== "string" ||
      !LOGIN.test(identity.login)
    ) {
      throw new TypeError(`maintainer identity ${String(index)} is invalid`);
    }
    return { github_id: identity.github_id, login: identity.login };
  });
  if (
    new Set(identities.map((identity) => identity.github_id)).size !== identities.length ||
    new Set(identities.map((identity) => identity.login)).size !== identities.length
  ) {
    throw new TypeError("maintainer identities must have unique IDs and logins");
  }
  return identities;
}

export function authenticateMaintainer(
  configured: string | undefined,
  session: UserSession,
): MaintainerIdentity {
  const identities = decodeMaintainerIdentities(configured);
  const identity = identities.find((candidate) => candidate.github_id === session.github_id);
  if (identity?.login !== session.login) {
    throw new AuthError("authenticated GitHub identity is not an approved maintainer");
  }
  return identity;
}
