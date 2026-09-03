import type { GitHubFetch } from "./github-state";
import type { ProviderFetch } from "./github-provider";

const BROKER_ENDPOINT = "https://github-broker.internal/v1/proxy";
const MAX_UPSTREAM_BODY_BYTES = 256 * 1024;

export type BrokerAuthority = "source" | "legacy_source" | "dispatch" | "results" | "benchmark";

type BrokerRequest = Readonly<{
  schema_version: 1;
  audience: "lean-eval-submission-server";
  authority: BrokerAuthority;
  method: string;
  url: string;
  body: string | null;
  expected_commit: string | null;
}>;

function requestBody(init: RequestInit | undefined): string | null {
  if (init?.body === undefined || init.body === null) return null;
  if (typeof init.body !== "string") {
    throw new TypeError("GitHub broker accepts only bounded JSON request bodies");
  }
  if (new TextEncoder().encode(init.body).byteLength > MAX_UPSTREAM_BODY_BYTES) {
    throw new TypeError("GitHub broker request body is too large");
  }
  return init.body;
}

function requestUrl(input: RequestInfo | URL): string {
  if (input instanceof Request) return input.url;
  return String(input);
}

function requestMethod(input: RequestInfo | URL, init: RequestInit | undefined): string {
  return (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
}

function expectedCommit(input: RequestInfo | URL, init: RequestInit | undefined): string | null {
  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
  return headers.get("x-lean-eval-expected-commit");
}

export function githubBrokerFetch(
  broker: Pick<Fetcher, "fetch">,
  authority: BrokerAuthority,
): GitHubFetch & ProviderFetch {
  return async (input, init) => {
    const payload: BrokerRequest = {
      schema_version: 1,
      audience: "lean-eval-submission-server",
      authority,
      method: requestMethod(input, init),
      url: requestUrl(input),
      body: requestBody(init),
      expected_commit: expectedCommit(input, init),
    };
    const brokerInit: RequestInit = {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    };
    if (init?.signal !== undefined) brokerInit.signal = init.signal;
    return broker.fetch(BROKER_ENDPOINT, brokerInit);
  };
}
