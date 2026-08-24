import { DurableObject } from "cloudflare:workers";

const ACTIVE_BINDING_KEY = "authoritative-active-binding:v1";
const RECEIPT_KEY = "authoritative-terminal-receipt:v1";

function retainedUntil(value: unknown): number {
  if (
    typeof value !== "object"
    || value === null
    || Array.isArray(value)
    || !Number.isSafeInteger(
      (value as Record<string, unknown>).retained_until_epoch_ms,
    )
  ) {
    throw new Error("durable replay state retention is invalid");
  }
  return (value as Record<string, unknown>).retained_until_epoch_ms as number;
}

function confirmedReceipt(value: unknown): unknown {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("terminal receipt is invalid");
  }
  return { ...value, destruction_state: "confirmed" };
}

export class ReplayTerminalReceipt extends DurableObject {
  async readBinding(): Promise<unknown> {
    const binding = await this.ctx.storage.get(ACTIVE_BINDING_KEY);
    return binding === undefined ? null : binding;
  }

  async claimBinding(binding: unknown): Promise<unknown> {
    const expiry = retainedUntil(binding);
    return this.ctx.storage.transaction(async (transaction) => {
      const existing = await transaction.get(ACTIVE_BINDING_KEY);
      if (existing !== undefined) return existing;
      await transaction.put(ACTIVE_BINDING_KEY, binding);
      await transaction.setAlarm(expiry);
      return binding;
    });
  }

  async readReceipt(): Promise<unknown> {
    const receipt = await this.ctx.storage.get(RECEIPT_KEY);
    if (receipt === undefined) return null;
    const expiry = typeof receipt === "object"
      && receipt !== null
      && !Array.isArray(receipt)
      ? (receipt as Record<string, unknown>).retained_until_epoch_ms
      : undefined;
    if (Number.isSafeInteger(expiry) && (expiry as number) <= Date.now()) {
      await this.ctx.storage.delete([ACTIVE_BINDING_KEY, RECEIPT_KEY]);
      return null;
    }
    return receipt;
  }

  async prepareReceipt(receipt: unknown): Promise<unknown> {
    const expiry = retainedUntil(receipt);
    return this.ctx.storage.transaction(async (transaction) => {
      const existing = await transaction.get(RECEIPT_KEY);
      if (existing !== undefined) return existing;
      await transaction.put(RECEIPT_KEY, receipt);
      await transaction.setAlarm(expiry);
      return receipt;
    });
  }

  async confirmReceipt(): Promise<unknown> {
    return this.ctx.storage.transaction(async (transaction) => {
      const existing = await transaction.get(RECEIPT_KEY);
      if (existing === undefined) throw new Error("terminal receipt is unavailable");
      if (
        typeof existing === "object"
        && existing !== null
        && !Array.isArray(existing)
        && (existing as Record<string, unknown>).destruction_state === "confirmed"
      ) {
        return existing;
      }
      const confirmed = confirmedReceipt(existing);
      await transaction.put(RECEIPT_KEY, confirmed);
      await transaction.setAlarm(retainedUntil(existing));
      return confirmed;
    });
  }

  override async alarm(): Promise<void> {
    await this.ctx.storage.delete([ACTIVE_BINDING_KEY, RECEIPT_KEY]);
  }
}
