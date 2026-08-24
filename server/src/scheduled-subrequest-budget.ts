type SubrequestFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export const SCHEDULED_SUBREQUEST_LIMIT = 400;

export class ScheduledSubrequestBudgetError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ScheduledSubrequestBudgetError";
  }
}

export class ScheduledSubrequestBudget {
  readonly #limit: number;
  #used = 0;

  constructor(limit = SCHEDULED_SUBREQUEST_LIMIT) {
    if (!Number.isSafeInteger(limit) || limit < 1) {
      throw new TypeError("scheduled subrequest limit must be a positive safe integer");
    }
    this.#limit = limit;
  }

  get remaining(): number {
    return this.#limit - this.#used;
  }

  requireRemaining(count: number): void {
    if (!Number.isSafeInteger(count) || count < 0) {
      throw new TypeError("scheduled subrequest reservation must be a non-negative safe integer");
    }
    if (this.remaining < count) {
      throw new ScheduledSubrequestBudgetError("scheduled dispatch subrequest reserve is exhausted");
    }
  }

  take(): void {
    this.requireRemaining(1);
    this.#used += 1;
  }

  wrap(fetcher: SubrequestFetch): SubrequestFetch {
    return (input, init) => {
      this.take();
      return fetcher(input, init);
    };
  }
}
