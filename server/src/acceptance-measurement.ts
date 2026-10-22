import { ApiDecodeError } from "./api-contract";
import { resultId } from "./result-owner";

import schema from "../../schemas/acceptance-measurement-v1.schema.json";

type Spec = { readonly type?: string | readonly string[]; readonly const?: unknown; readonly enum?: readonly unknown[]; readonly required?: readonly string[]; readonly properties?: Readonly<Record<string, Spec>>; readonly pattern?: string; readonly minLength?: number; readonly maxLength?: number; readonly minimum?: number; readonly maximum?: number };
function check(value: unknown, spec: Spec, label: string): void {
  const fail = (): never => { throw new ApiDecodeError(label + " is invalid"); };
  if ("const" in spec && value !== spec.const) fail();
  if (spec.enum !== undefined && !spec.enum.includes(value)) fail();
  const types = typeof spec.type === "string" ? [spec.type] : spec.type;
  const kind = value === null ? "null" : typeof value === "number" && Number.isSafeInteger(value) ? "integer" : typeof value;
  if (types !== undefined && !types.includes(kind)) fail();
  if (value === null) return;
  if (typeof value === "object") {
    if (Array.isArray(value)) fail();
    const record = value as Record<string, unknown>;
    const required = spec.required ?? [];
    if (Object.keys(record).length !== required.length || required.some(k => !(k in record))) fail();
    for (const key of required) check(record[key], spec.properties?.[key] ?? {}, label + "." + key);
  } else if (typeof value === "string") {
    if (Array.from(value).length < (spec.minLength ?? 0) || Array.from(value).length > (spec.maxLength ?? 1000)) fail();
    if (spec.pattern !== undefined && (new RegExp(spec.pattern, "u")).exec(value)?.[0] !== value) fail();
  } else if (typeof value === "number" && (!Number.isSafeInteger(value) || value < (spec.minimum ?? 0) || value > (spec.maximum ?? Number.MAX_SAFE_INTEGER))) fail();
}

export type AcceptanceMeasurement = Readonly<{
  schema_version: 1; kind: "acceptance-build"; result_id: string; owner_login: string; declared_model: string;
  problem_id: string; statement_revision: number; benchmark_commit: string; results_commit: string; toolchain: string;
  series: string; attempt: number; workflow_run_id: number; workflow_commit: string; measured_at: string;
  runner_class: string; architecture: string; cache_state: "solution-cold-dependencies-cached";
  components: Readonly<Record<"toolchain" | "bubblewrap_sha256", string>>;
  status: "available" | "failed"; reason_code: null | "measurement_invalid" | "measurement_unavailable";
  build_wall_time_ms: number | null; build_retired_instructions: number | null; build_retired_instructions_unavailable_reason: string | null;
  checker_wall_time_ms: number | null; checker_retired_instructions: number | null; checker_retired_instructions_unavailable_reason: string | null;
}>;

export function decodeAcceptanceMeasurement(value: unknown): AcceptanceMeasurement {
  check(value, schema, "measurement");
  const m = value as AcceptanceMeasurement;
  if (!Number.isFinite(Date.parse(m.measured_at)) || new Date(m.measured_at).toISOString() !== m.measured_at) throw new ApiDecodeError("measurement timestamp is invalid");
  const available = m.status === "available";
  if (available !== (m.reason_code === null)) throw new ApiDecodeError("measurement status is incoherent");
  for (const phase of ["build", "checker"] as const) {
    const elapsed = m[`${phase}_wall_time_ms`];
    const counter = m[`${phase}_retired_instructions`];
    const reason = m[`${phase}_retired_instructions_unavailable_reason`];
    if (available !== (elapsed !== null) || (counter === null) === (reason === null) || (!available && counter !== null)) throw new ApiDecodeError("measurement phase is incoherent");
  }
  return m;
}

export async function verifyMeasurementIdentity(m: AcceptanceMeasurement): Promise<void> {
  if (m.result_id !== await resultId(m.owner_login, m.declared_model, m.problem_id, m.statement_revision)) throw new ApiDecodeError("measurement identity disagrees");
  const fields = { architecture: m.architecture, cache_state: m.cache_state, components: Object.fromEntries(Object.entries(m.components).sort(([a], [b]) => a.localeCompare(b))), kind: m.kind, runner_class: m.runner_class, toolchain: m.toolchain };
  const bytes = new TextEncoder().encode(JSON.stringify(fields));
  const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(b => b.toString(16).padStart(2, "0")).join("");
  if (digest !== m.series) throw new ApiDecodeError("measurement series disagrees");
}
