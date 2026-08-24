import { describe, expect, it, vi } from "vitest";

import {
  ScheduledSubrequestBudget,
  ScheduledSubrequestBudgetError,
} from "../src/scheduled-subrequest-budget";

describe("scheduled subrequest budget", () => {
  it("refuses the first request beyond its exact invocation bound", async () => {
    const upstream = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })));
    const budget = new ScheduledSubrequestBudget(3);
    const fetcher = budget.wrap(upstream);

    await fetcher("https://example.test/one");
    await fetcher("https://example.test/two");
    await fetcher("https://example.test/three");
    expect(() => fetcher("https://example.test/four")).toThrow(ScheduledSubrequestBudgetError);
    expect(upstream).toHaveBeenCalledTimes(3);
    expect(budget.remaining).toBe(0);
  });

  it("checks a completion reserve without consuming it", () => {
    const budget = new ScheduledSubrequestBudget(4);
    budget.take();
    budget.requireRemaining(3);
    expect(budget.remaining).toBe(3);
    expect(() => budget.requireRemaining(4)).toThrow(ScheduledSubrequestBudgetError);
  });
});
