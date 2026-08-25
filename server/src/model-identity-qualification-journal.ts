import { DurableObject } from "cloudflare:workers";

const SCHEMA_VERSION = 2;
const ACTIVE_LEASE_ALARM_MS = 5 * 60 * 1000;
const SHA = /^[0-9a-f]{40}$/;
const RUN_ID = /^[1-9][0-9]{0,19}$/;
const JOURNAL_ID = /^mqj_[0-9a-f]{64}$/;

type Identity = Readonly<{ github_id: number; login: string }>;

export type QualificationJson =
  | null
  | boolean
  | number
  | string
  | readonly QualificationJson[]
  | Readonly<{ [key: string]: QualificationJson }>;

export type QualificationIntent = Readonly<{
  owner: Identity;
  cross_owner: Identity;
  maintainer: Identity;
}>;

export type QualificationAcquisition = Readonly<{
  schema_version: 2;
  run_id: string;
  run_attempt: 1;
  deployed_commit: string;
  initial_state_commit: string;
  initial_state_tree: string;
  intent: QualificationIntent;
}>;

export type QualificationStepReservation = Readonly<{
  run_id: string;
  run_attempt: 1;
  journal_id: string;
  expected_journal_revision: number;
  expected_state_commit: string;
  expected_state_tree: string;
  operation: string;
  operation_request: QualificationJson;
}>;

export type QualificationStepCompletion = Readonly<{
  reservation: QualificationStepReservation;
  state_commit: string;
  state_tree: string;
  receipt: QualificationJson;
}>;

type PendingStep = Readonly<{
  operation: string;
  operation_request: unknown;
  reserved_revision: number;
  expected_state_commit: string;
  expected_state_tree: string;
}>;

type Restoration = Readonly<{
  restoration_commit: string;
  restoration_parent_commit: string;
  restoration_parent_tree: string;
  restoration_tree: string;
}>;

type StoredJournal = Readonly<{
  acquisition: QualificationAcquisition;
  journal_id: string;
  journal_revision: number;
  current_state_commit: string;
  current_state_tree: string;
  lease_status: "active" | "restored";
  pending_step: PendingStep | null;
  recovery_nonce: string;
  restoration: Restoration | null;
}>;

export type QualificationRecoveryPlan = Readonly<{
  journal_id: string;
  recovery_nonce: string;
  journal_revision: number;
  initial_state_commit: string;
  initial_state_tree: string;
  current_state_commit: string;
  current_state_tree: string;
  intent: QualificationIntent;
  pending_step: PendingStep | null;
}>;

export type QualificationJournalStatus = Readonly<{
  schema_version: 2;
  status: "model_identity_qualification_journal";
  environment: "staging";
  deployed_commit: string;
  run_id: string;
  run_attempt: 1;
  journal_id: string;
  journal_revision: number;
  initial_state_commit: string;
  initial_state_tree: string;
  current_state_commit: string;
  current_state_tree: string;
  lease_status: "active" | "restored";
  lease_released: boolean;
  owner_api_enabled: false;
  maintainer_api_enabled: false;
  foreign_commit_observed: false;
  restoration_commit: string | null;
  restoration_parent_commit: string | null;
  restoration_parent_tree: string | null;
  restoration_tree: string | null;
  restoration_fast_forward: boolean;
  restoration_tree_equal: boolean;
}>;

type StoredRow = Readonly<{ body: string }>;

export type QualificationStepReservationOutcome =
  | Readonly<{ kind: "reserved"; journal: QualificationJournalStatus }>
  | Readonly<{ kind: "completed"; receipt_json: string }>;

function exactJson(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactFields(
  value: Record<string, unknown>,
  fields: readonly string[],
  label: string,
): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new TypeError(`${label} fields are invalid`);
  }
}

function identity(value: unknown, label: string): Identity {
  const input = record(value, label);
  exactFields(input, ["github_id", "login"], label);
  if (
    typeof input.github_id !== "number" ||
    !Number.isSafeInteger(input.github_id) ||
    input.github_id < 1 ||
    typeof input.login !== "string" ||
    !/^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/.test(input.login)
  ) {
    throw new TypeError(`${label} is invalid`);
  }
  return { github_id: input.github_id, login: input.login };
}

function qualificationJson(value: unknown): QualificationJson {
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "string" ||
    (typeof value === "number" && Number.isFinite(value) && Number.isSafeInteger(value))
  ) {
    return value;
  }
  if (Array.isArray(value)) return value.map(qualificationJson);
  if (typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, member]) => [key, qualificationJson(member)]),
    );
  }
  throw new TypeError("qualification JSON value is invalid");
}

function canonicalAcquisition(value: unknown): QualificationAcquisition {
  const input = record(value, "qualification acquisition");
  exactFields(input, [
    "schema_version", "run_id", "run_attempt", "deployed_commit",
    "initial_state_commit", "initial_state_tree", "intent",
  ], "qualification acquisition");
  const rawIntent = record(input.intent, "qualification intent");
  exactFields(rawIntent, ["owner", "cross_owner", "maintainer"], "qualification intent");
  if (
    input.schema_version !== SCHEMA_VERSION ||
    typeof input.run_id !== "string" ||
    !RUN_ID.test(input.run_id) ||
    input.run_attempt !== 1 ||
    typeof input.deployed_commit !== "string" ||
    !SHA.test(input.deployed_commit) ||
    typeof input.initial_state_commit !== "string" ||
    !SHA.test(input.initial_state_commit) ||
    typeof input.initial_state_tree !== "string" ||
    !SHA.test(input.initial_state_tree)
  ) {
    throw new TypeError("qualification acquisition is invalid");
  }
  const intent = {
    owner: identity(rawIntent.owner, "qualification owner"),
    cross_owner: identity(rawIntent.cross_owner, "qualification cross-owner"),
    maintainer: identity(rawIntent.maintainer, "qualification maintainer"),
  };
  const identities = [intent.owner, intent.cross_owner, intent.maintainer];
  if (
    new Set(identities.map((item) => item.github_id)).size !== identities.length ||
    new Set(identities.map((item) => item.login)).size !== identities.length
  ) {
    throw new TypeError("qualification identities are not distinct");
  }
  return {
    schema_version: SCHEMA_VERSION,
    run_id: input.run_id,
    run_attempt: 1,
    deployed_commit: input.deployed_commit,
    initial_state_commit: input.initial_state_commit,
    initial_state_tree: input.initial_state_tree,
    intent,
  };
}

function canonicalReservation(value: unknown): QualificationStepReservation {
  const input = record(value, "qualification step reservation");
  exactFields(input, [
    "run_id", "run_attempt", "journal_id", "expected_journal_revision",
    "expected_state_commit", "expected_state_tree", "operation", "operation_request",
  ], "qualification step reservation");
  if (
    typeof input.run_id !== "string" ||
    !RUN_ID.test(input.run_id) ||
    input.run_attempt !== 1 ||
    typeof input.journal_id !== "string" ||
    !JOURNAL_ID.test(input.journal_id) ||
    typeof input.expected_journal_revision !== "number" ||
    !Number.isSafeInteger(input.expected_journal_revision) ||
    input.expected_journal_revision < 1 ||
    typeof input.expected_state_commit !== "string" ||
    !SHA.test(input.expected_state_commit) ||
    typeof input.expected_state_tree !== "string" ||
    !SHA.test(input.expected_state_tree) ||
    typeof input.operation !== "string" ||
    !/^[a-z][a-z0-9_]{0,63}$/.test(input.operation)
  ) {
    throw new TypeError("qualification step reservation is invalid");
  }
  return {
    run_id: input.run_id,
    run_attempt: 1,
    journal_id: input.journal_id,
    expected_journal_revision: input.expected_journal_revision,
    expected_state_commit: input.expected_state_commit,
    expected_state_tree: input.expected_state_tree,
    operation: input.operation,
    operation_request: qualificationJson(input.operation_request),
  };
}

async function journalId(acquisition: QualificationAcquisition): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(
      `lean-eval-model-identity-qualification-journal-v2\0${acquisition.run_id}\0${String(acquisition.run_attempt)}\0${acquisition.deployed_commit}`,
    ),
  );
  return `mqj_${[...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

function recoveryNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function status(journal: StoredJournal): QualificationJournalStatus {
  const restoration = journal.restoration;
  return {
    schema_version: SCHEMA_VERSION,
    status: "model_identity_qualification_journal",
    environment: "staging",
    deployed_commit: journal.acquisition.deployed_commit,
    run_id: journal.acquisition.run_id,
    run_attempt: journal.acquisition.run_attempt,
    journal_id: journal.journal_id,
    journal_revision: journal.journal_revision,
    initial_state_commit: journal.acquisition.initial_state_commit,
    initial_state_tree: journal.acquisition.initial_state_tree,
    current_state_commit: journal.current_state_commit,
    current_state_tree: journal.current_state_tree,
    lease_status: journal.lease_status,
    lease_released: journal.lease_status === "restored",
    owner_api_enabled: false,
    maintainer_api_enabled: false,
    foreign_commit_observed: false,
    restoration_commit: restoration?.restoration_commit ?? null,
    restoration_parent_commit: restoration?.restoration_parent_commit ?? null,
    restoration_parent_tree: restoration?.restoration_parent_tree ?? null,
    restoration_tree: restoration?.restoration_tree ?? null,
    restoration_fast_forward: restoration !== null,
    restoration_tree_equal:
      restoration?.restoration_tree === journal.acquisition.initial_state_tree,
  };
}

export class ModelIdentityQualificationJournal extends DurableObject {
  constructor(ctx: DurableObjectState, env: CloudflareEnv) {
    super(ctx, env);
    this.ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS qualification_journals (run_id TEXT PRIMARY KEY, body TEXT NOT NULL)",
    );
    this.ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS qualification_step_receipts (run_id TEXT NOT NULL, reserved_revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY (run_id, reserved_revision))",
    );
    this.ctx.storage.sql.exec(
      "CREATE UNIQUE INDEX IF NOT EXISTS one_active_qualification ON qualification_journals ((1)) WHERE json_extract(body, '$.lease_status') = 'active'",
    );
  }

  async acquire(value: unknown): Promise<QualificationJournalStatus> {
    const acquisition = canonicalAcquisition(value);
    const identifier = await journalId(acquisition);
    const next: StoredJournal = {
      acquisition,
      journal_id: identifier,
      journal_revision: 1,
      current_state_commit: acquisition.initial_state_commit,
      current_state_tree: acquisition.initial_state_tree,
      lease_status: "active",
      pending_step: null,
      recovery_nonce: recoveryNonce(),
      restoration: null,
    };
    return this.ctx.storage.transaction(async () => {
      const existing = this.read(acquisition.run_id);
      if (existing !== null) {
        if (!exactJson(existing.acquisition, acquisition)) {
          throw new Error("qualification acquisition conflicts with its durable journal");
        }
        return status(existing);
      }
      try {
        this.write(next);
      } catch {
        throw new Error("another model identity qualification holds the staging lease");
      }
      await this.ctx.storage.setAlarm(Date.now() + ACTIVE_LEASE_ALARM_MS);
      return status(next);
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async readStatus(runId: string): Promise<QualificationJournalStatus> {
    const journal = this.required(runId);
    return status(journal);
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async readRecoveryPlan(runId: string): Promise<string> {
    const journal = this.required(runId);
    if (journal.lease_status !== "active") {
      throw new Error("qualification journal no longer requires recovery");
    }
    const plan: QualificationRecoveryPlan = {
      journal_id: journal.journal_id,
      recovery_nonce: journal.recovery_nonce,
      journal_revision: journal.journal_revision,
      initial_state_commit: journal.acquisition.initial_state_commit,
      initial_state_tree: journal.acquisition.initial_state_tree,
      current_state_commit: journal.current_state_commit,
      current_state_tree: journal.current_state_tree,
      intent: journal.acquisition.intent,
      pending_step: journal.pending_step,
    };
    return JSON.stringify(plan);
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async reserveStep(
    value: unknown,
  ): Promise<QualificationStepReservationOutcome> {
    const reservation = canonicalReservation(value);
    return this.ctx.storage.transactionSync(() => {
      const journal = this.required(reservation.run_id);
      const completed = this.readCompletion(
        reservation.run_id,
        reservation.expected_journal_revision,
      );
      if (completed !== null) {
        if (!exactJson(completed.reservation, reservation)) {
          throw new Error("qualification step retry conflicts with its durable receipt");
        }
        return { kind: "completed", receipt_json: JSON.stringify(completed.receipt) };
      }
      this.assertReservation(journal, reservation);
      const pending: PendingStep = {
        operation: reservation.operation,
        operation_request: reservation.operation_request,
        reserved_revision: reservation.expected_journal_revision,
        expected_state_commit: reservation.expected_state_commit,
        expected_state_tree: reservation.expected_state_tree,
      };
      if (journal.pending_step !== null && !exactJson(journal.pending_step, pending)) {
        throw new Error("qualification journal already has another pending step");
      }
      if (journal.pending_step === null) this.write({ ...journal, pending_step: pending });
      return { kind: "reserved", journal: status(journal) };
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async completeStep(value: unknown): Promise<string> {
    const input = record(value, "qualification step completion");
    exactFields(input, ["reservation", "state_commit", "state_tree", "receipt"], "qualification step completion");
    const reservation = canonicalReservation(input.reservation);
    if (typeof input.state_commit !== "string" || typeof input.state_tree !== "string") {
      throw new TypeError("qualification step completion State identity is invalid");
    }
    const completion: QualificationStepCompletion = {
      reservation,
      state_commit: input.state_commit,
      state_tree: input.state_tree,
      receipt: qualificationJson(input.receipt),
    };
    if (!SHA.test(completion.state_commit) || !SHA.test(completion.state_tree)) {
      throw new TypeError("qualification step completion State identity is invalid");
    }
    return this.ctx.storage.transactionSync(() => {
      const journal = this.required(completion.reservation.run_id);
      const completed = this.readCompletion(
        completion.reservation.run_id,
        completion.reservation.expected_journal_revision,
      );
      if (completed !== null) {
        if (!exactJson(completed, completion)) {
          throw new Error("qualification step completion conflicts with its durable receipt");
        }
        return JSON.stringify(completed.receipt);
      }
      this.assertReservation(journal, completion.reservation);
      if (journal.pending_step === null) {
        throw new Error("qualification step was not durably reserved");
      }
      const expectedPending: PendingStep = {
        operation: completion.reservation.operation,
        operation_request: completion.reservation.operation_request,
        reserved_revision: completion.reservation.expected_journal_revision,
        expected_state_commit: completion.reservation.expected_state_commit,
        expected_state_tree: completion.reservation.expected_state_tree,
      };
      if (!exactJson(journal.pending_step, expectedPending)) {
        throw new Error("qualification step completion does not match its reservation");
      }
      const next: StoredJournal = {
        ...journal,
        journal_revision: journal.journal_revision + 1,
        current_state_commit: completion.state_commit,
        current_state_tree: completion.state_tree,
        pending_step: null,
      };
      this.write(next);
      this.ctx.storage.sql.exec(
        "INSERT INTO qualification_step_receipts (run_id, reserved_revision, body) VALUES (?, ?, ?)",
        completion.reservation.run_id,
        completion.reservation.expected_journal_revision,
        JSON.stringify(completion),
      );
      return JSON.stringify(completion.receipt);
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async abandonNonMutatingStep(
    runId: string,
    journalId: string,
    expectedRevision: number,
  ): Promise<QualificationJournalStatus> {
    return this.ctx.storage.transactionSync(() => {
      const journal = this.required(runId);
      if (
        journal.lease_status !== "active" ||
        journal.journal_id !== journalId ||
        journal.journal_revision !== expectedRevision ||
        journal.pending_step === null ||
        !new Set(["oauth_session_identity", "agent_session_identity"])
          .has(journal.pending_step.operation)
      ) {
        throw new Error("qualification pending step cannot be abandoned safely");
      }
      const next = { ...journal, pending_step: null };
      this.write(next);
      return status(next);
    });
  }

  // eslint-disable-next-line @typescript-eslint/require-await -- Durable Object RPC methods are asynchronous boundaries.
  async completeRestoration(
    runId: string,
    expectedRevision: number,
    restoration: Restoration,
  ): Promise<QualificationJournalStatus> {
    if (
      !SHA.test(restoration.restoration_commit) ||
      !SHA.test(restoration.restoration_parent_commit) ||
      !SHA.test(restoration.restoration_parent_tree) ||
      !SHA.test(restoration.restoration_tree)
    ) {
      throw new TypeError("qualification restoration identity is invalid");
    }
    return this.ctx.storage.transactionSync(() => {
      const journal = this.required(runId);
      if (journal.lease_status === "restored") {
        if (!exactJson(journal.restoration, restoration)) {
          throw new Error("qualification restoration conflicts with its durable receipt");
        }
        return status(journal);
      }
      if (
        journal.pending_step !== null ||
        journal.journal_revision !== expectedRevision ||
        restoration.restoration_parent_commit !== journal.current_state_commit ||
        restoration.restoration_parent_tree !== journal.current_state_tree ||
        restoration.restoration_tree !== journal.acquisition.initial_state_tree ||
        restoration.restoration_commit === restoration.restoration_parent_commit
      ) {
        throw new Error("qualification restoration does not match the active journal");
      }
      const next: StoredJournal = {
        ...journal,
        journal_revision: journal.journal_revision + 1,
        current_state_commit: restoration.restoration_commit,
        current_state_tree: restoration.restoration_tree,
        lease_status: "restored",
        restoration,
      };
      this.write(next);
      return status(next);
    });
  }

  override async alarm(): Promise<void> {
    const active = this.active();
    if (active === null) return;
    console.error(JSON.stringify({
      event: "model_identity_qualification_recovery_due",
      journal_id: active.journal_id,
      run_id: active.acquisition.run_id,
    }));
    await this.ctx.storage.setAlarm(Date.now() + ACTIVE_LEASE_ALARM_MS);
  }

  private assertReservation(
    journal: StoredJournal,
    reservation: QualificationStepReservation,
  ): void {
    if (
      journal.lease_status !== "active" ||
      journal.journal_id !== reservation.journal_id ||
      journal.journal_revision !== reservation.expected_journal_revision ||
      journal.current_state_commit !== reservation.expected_state_commit ||
      journal.current_state_tree !== reservation.expected_state_tree
    ) {
      throw new Error("qualification step does not match the active durable journal");
    }
  }

  private active(): StoredJournal | null {
    const row = this.ctx.storage.sql.exec<StoredRow>(
      "SELECT body FROM qualification_journals WHERE json_extract(body, '$.lease_status') = 'active' LIMIT 1",
    ).toArray()[0];
    return row === undefined ? null : JSON.parse(row.body) as StoredJournal;
  }

  private read(runId: string): StoredJournal | null {
    if (!RUN_ID.test(runId)) throw new TypeError("qualification run ID is invalid");
    const row = this.ctx.storage.sql.exec<StoredRow>(
      "SELECT body FROM qualification_journals WHERE run_id = ?",
      runId,
    ).toArray()[0];
    return row === undefined ? null : JSON.parse(row.body) as StoredJournal;
  }

  private required(runId: string): StoredJournal {
    const journal = this.read(runId);
    if (journal === null) throw new Error("qualification journal was not found");
    return journal;
  }

  private readCompletion(
    runId: string,
    reservedRevision: number,
  ): QualificationStepCompletion | null {
    const row = this.ctx.storage.sql.exec<StoredRow>(
      "SELECT body FROM qualification_step_receipts WHERE run_id = ? AND reserved_revision = ?",
      runId,
      reservedRevision,
    ).toArray()[0];
    return row === undefined ? null : JSON.parse(row.body) as QualificationStepCompletion;
  }

  private write(journal: StoredJournal): void {
    this.ctx.storage.sql.exec(
      "INSERT INTO qualification_journals (run_id, body) VALUES (?, ?) ON CONFLICT(run_id) DO UPDATE SET body = excluded.body",
      journal.acquisition.run_id,
      JSON.stringify(journal),
    );
  }
}
