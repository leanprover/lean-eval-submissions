import { HistoricalPublicExecutorContractError } from "./historical-public-executor-contract";

const PROFILE_DOMAIN = "lean-eval-replay-execution-profile-v1\0";
const PROFILE_FIELDS = ["schema_version", "runner_profile", "vm_image_digest", "toolchain",
  "go_toolchain", "rust_toolchain", "cpu_model", "architecture", "kernel_release",
  "cache_state", "measurement_command", "components"];

function record(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)
    || Object.keys(value).sort().join("\0") !== [...fields].sort().join("\0")) {
    throw new HistoricalPublicExecutorContractError("execution profile fields are not closed");
  }
  return value as Record<string, unknown>;
}

function string(value: unknown): string {
  if (typeof value !== "string" || value.length === 0 || value.length > 1024 || value.includes("\0")) {
    throw new HistoricalPublicExecutorContractError("execution profile string is invalid");
  }
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(++index);
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        throw new HistoricalPublicExecutorContractError("execution profile Unicode is invalid");
      }
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      throw new HistoricalPublicExecutorContractError("execution profile Unicode is invalid");
    }
  }
  return value;
}

function compact(value: unknown): string {
  if (typeof value === "string") return JSON.stringify(string(value));
  if (typeof value === "number" && Number.isSafeInteger(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(compact).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object).sort().map((key) => `${JSON.stringify(string(key))}:${compact(object[key])}`).join(",")}}`;
  }
  throw new HistoricalPublicExecutorContractError("execution profile value is invalid");
}

export async function canonicalReviewedPublicProfile(
  value: unknown, reviewedDigest: string, reviewedImage: string,
): Promise<string> {
  const profile = record(value, PROFILE_FIELDS);
  for (const field of ["runner_profile", "vm_image_digest", "toolchain", "go_toolchain",
    "rust_toolchain", "cpu_model", "architecture", "kernel_release", "cache_state"]) string(profile[field]);
  if (profile.schema_version !== 1 || profile.vm_image_digest !== reviewedImage
    || !/^sha256:[0-9a-f]{64}$/.test(reviewedImage)
    || !/^leanprover\/lean4:v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta)[0-9]+)?$/.test(profile.toolchain as string)
    || !/^go[0-9]+\.[0-9]+\.[0-9]+$/.test(profile.go_toolchain as string)
    || !/^rustc-[0-9]+\.[0-9]+\.[0-9]+$/.test(profile.rust_toolchain as string)
    || !["x86_64", "aarch64"].includes(profile.architecture as string)
    || profile.cpu_model === "unavailable"
    || !["cold", "warm"].includes(profile.cache_state as string)
    || !Array.isArray(profile.measurement_command) || profile.measurement_command.length === 0) {
    throw new HistoricalPublicExecutorContractError("execution profile is invalid");
  }
  for (const argument of profile.measurement_command) string(argument);
  const components = record(profile.components, ["comparator", "landrun", "lean4export", "nanoda"]);
  for (const component of Object.values(components)) {
    const item = record(component, ["repository", "commit"]);
    if (!/^(?!\.{1,2}\/)(?![^/]+\/\.{1,2}$)[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(string(item.repository))
      || !/^[0-9a-f]{40}$/.test(string(item.commit))) {
      throw new HistoricalPublicExecutorContractError("execution profile component is invalid");
    }
  }
  const canonical = compact(profile);
  if (new TextEncoder().encode(canonical).length > 16 * 1024) {
    throw new HistoricalPublicExecutorContractError("execution profile exceeds the size limit");
  }
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(PROFILE_DOMAIN + canonical)));
  const digest = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  if (digest !== reviewedDigest) throw new HistoricalPublicExecutorContractError("execution profile digest differs");
  // The Worker must write these exact bytes, not a second serialization.
  return canonical;
}
