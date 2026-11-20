import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  dependencies: undefined as undefined | {
    sandbox(runtime: never, nonce: string): Promise<unknown>;
    afterAuthoritativeStart(sandbox: never, deadline: number): Promise<void>;
  },
  configure: vi.fn<(...args: unknown[]) => Promise<void>>(),
  setSleepAfter: vi.fn<(...args: unknown[]) => Promise<void>>(),
  factory: vi.fn(),
}));
vi.mock("../src/replay-app", () => ({
  handleReplayRequest: (_request: Request, _env: unknown, dependencies: typeof mocks.dependencies) => {
    mocks.dependencies = dependencies;
    return Response.json({ captured: true });
  },
}));
vi.mock("../src/replay-sandbox", async (original) => ({
  ...await original<object>(),
  replaySandbox: (...args: unknown[]) => {
    mocks.factory(...args);
    return { configure: mocks.configure, setSleepAfter: mocks.setSleepAfter };
  },
}));
import entry from "../src/historical-private-replay-entry";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.configure.mockResolvedValue();
  mocks.setSleepAfter.mockResolvedValue();
});

async function route(path: string) {
  await entry.fetch(new Request(`https://example.test${path}`, { method: "POST" }), {} as never);
  if (!mocks.dependencies) throw new Error("dependencies missing");
  return mocks.dependencies;
}

describe("bounded private async handoff lifetime", () => {
  it("start factory cannot asynchronously reapply the prewarm idle default", async () => {
    const dependencies = await route("/api/v1/replay");
    await dependencies.sandbox({} as never, "a".repeat(64));
    expect(mocks.factory.mock.calls[0]?.[3]).toBe(null);
    expect(mocks.configure).toHaveBeenCalledWith({
      keepAlive: false,
      containerTimeouts: { instanceGetTimeoutMS: 120_000, portReadyTimeoutMS: 600_000 },
    });
    expect(mocks.setSleepAfter).not.toHaveBeenCalled();
  });

  it.each(["/api/v1/replay/status", "/api/v1/replay/archive-part",
    "/api/v1/historical-private-replay/prewarm"])("%s restores the finite fifteen minute policy", async (path) => {
    const dependencies = await route(path);
    await dependencies.sandbox({} as never, "a".repeat(64));
    expect(mocks.factory.mock.calls[0]?.[3]).toBe("15m");
    expect(mocks.configure).toHaveBeenCalledWith(expect.objectContaining({
      sleepAfter: "15m", keepAlive: false,
    }));
  });

  it("post-start hook awaits finite duration capped by original claim, never seven-hour cleanup", async () => {
    const dependencies = await route("/api/v1/replay");
    const now = Date.now();
    const clock = vi.spyOn(Date, "now").mockReturnValue(now);
    const sandbox = { setSleepAfter: mocks.setSleepAfter };
    await dependencies.afterAuthoritativeStart(sandbox as never, now + 7 * 3600_000);
    expect(mocks.setSleepAfter).toHaveBeenLastCalledWith(21600);
    clock.mockReturnValue(now + 58 * 60_000);
    await dependencies.afterAuthoritativeStart(sandbox as never, now + 7 * 3600_000);
    expect(mocks.setSleepAfter).toHaveBeenLastCalledWith(18120);
    clock.mockRestore();
  });

  it("lifetime configuration failure is observable, never swallowed", async () => {
    const dependencies = await route("/api/v1/replay");
    mocks.setSleepAfter.mockRejectedValue(new Error("storage unavailable"));
    await expect(dependencies.afterAuthoritativeStart({ setSleepAfter: mocks.setSleepAfter } as never,
      Date.now() + 7 * 3600_000)).rejects.toThrow("storage unavailable");
  });
});
