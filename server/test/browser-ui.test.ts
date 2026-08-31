import { describe, expect, it, vi } from "vitest";

import { browserPage, browserScript, releasePage, releaseScript } from "../src/browser-ui";

type FakeEvent = Readonly<{ preventDefault: () => void }>;
type FakeListener = (event: FakeEvent) => unknown;

function fakeElement(value = "") {
  const attributes = new Map<string, string>();
  const listeners = new Map<string, FakeListener>();
  return {
    attributes,
    disabled: false,
    listeners,
    textContent: "",
    value,
    addEventListener(name: string, listener: FakeListener) {
      listeners.set(name, listener);
    },
    setAttribute(name: string, attributeValue: string) {
      attributes.set(name, attributeValue);
    },
    reportValidity() {
      return true;
    },
  };
}

function fakeSessionStorage() {
  const stored = new Map<string, string>();
  return {
    stored,
    storage: {
      getItem: (key: string) => stored.get(key) ?? null,
      removeItem: (key: string) => stored.delete(key),
      setItem: (key: string, value: string) => stored.set(key, value),
    },
  };
}

describe("browser intake page", () => {
  it("renders only new-submission controls on the enabled root page", async () => {
    const response = browserPage("staging", true, true);
    const body = await response.text();
    expect(response.headers.get("content-security-policy")).toContain("script-src 'self'");
    expect(body).toContain("LeanEval staging intake");
    expect(body).toContain('href="/api/v1/oauth/start"');
    expect(body).toContain('id="submission-form"');
    expect(body).toContain('src="/intake.js?v=intake-v2"');
    expect(body).toContain('id="auth-status"');
    expect(body).toContain("GitHub sign-in is required");
    expect(body).toContain('id="submit-button"');
    expect(body).toContain('id="submit-spinner"');
    expect(body).toContain('href="/release/"');
    expect(body).not.toContain('id="release-opt-in-form"');
    expect(body).not.toContain('id="release-opt-in-submission-id"');
    expect(body).not.toContain('id="release-opt-in-button"');
    expect(body).not.toContain("Schedule a private submission");
    expect(body).not.toContain("open-conjectures");
    expect(body).not.toContain('id="source_visibility"');
    expect(body).not.toContain('<option value="public">');
    expect(body).toContain('<option value="scheduled">scheduled release (default)</option>');
    expect(body.indexOf('value="scheduled"')).toBeLessThan(body.indexOf('value="withheld"'));
    expect(body).toContain("authorized to license the submitted source under the Apache License 2.0");
    expect(body).toContain("exactly two UTC calendar months after acceptance");
    expect(body).toContain("Choose private to keep accepted source withheld");
    expect(body.toLowerCase()).not.toContain("opt out");
    expect(body).not.toContain("<script>");
  });

  it("does not link to release scheduling while its API is disabled", async () => {
    const body = await browserPage("staging", true, false).text();
    expect(body).toContain('id="submission-form"');
    expect(body).not.toContain('href="/release/"');
    expect(body).not.toContain("Schedule release for an existing private submission");
  });

  it("keeps release scheduling separate when intake is disabled", async () => {
    const body = await browserPage("production", false, true).text();
    expect(body).toContain("production intake is currently disabled");
    expect(body).not.toContain('id="submission-form"');
    expect(body).not.toContain('id="oauth-sign-in"');
    expect(body).not.toContain("/intake.js");
    expect(body).toContain('href="/release/"');
  });

  it("does not expose controls when both gates are disabled", async () => {
    const body = await browserPage("production", false, false).text();
    expect(body).not.toContain('id="submission-form"');
    expect(body).not.toContain("/api/v1/oauth/start");
    expect(body).not.toContain("/intake.js");
    expect(body).not.toContain('href="/release/"');
  });

  it("uses textContent for intake output and contains no later-release operation", async () => {
    const response = browserScript();
    const script = await response.text();
    expect(response.headers.get("content-type")).toContain("text/javascript");
    expect(response.headers.get("cache-control")).toBe("no-store");
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    expect(() => new Function(script)).not.toThrow();
    expect(script).toContain("result.textContent");
    expect(script).not.toContain("innerHTML");
    expect(script).toContain("sessionStorage.removeItem");
    expect(script.indexOf("if (saved)")).toBeLessThan(script.indexOf("const query ="));
    expect(script).toContain('query.get(name)');
    expect(script).toContain('query.get("oauth") === "success"');
    expect(script).toContain('authStatus.textContent = "Signed in with GitHub."');
    expect(script).toContain('oauthSignIn.addEventListener("click"');
    expect(script).toContain('setSubmitting(true, "Preparing submission…")');
    expect(script).toContain('submitButton.setAttribute("aria-busy", String(submitting))');
    expect(script).toContain('setSubmitting(false, "Submit exact commit")');
    expect(script).toContain('source_visibility: "private"');
    expect(script).not.toContain("publication-opt-in");
    expect(script).not.toContain("publication-opt-out");
    expect(script).not.toContain('query.get("submission_id")');
    expect(script).not.toContain('query.get("source_visibility")');
  });

  it("shows OAuth success and keeps the submit button visibly busy until queuing finishes", async () => {
    const script = await browserScript().text();
    const elements = new Map([
      ["#submission-form", fakeElement()],
      ["#result", fakeElement()],
      ["#auth-status", fakeElement()],
      ["#oauth-sign-in", fakeElement()],
      ["#submit-button", fakeElement()],
      ["#submit-label", fakeElement("Submit exact commit")],
      ["#problem_id", fakeElement("two_plus_two")],
      ["#problem_group", fakeElement("formalization-evaluation")],
      ["#statement_revision", fakeElement("1")],
      ["#declared_model", fakeElement("Browser smoke")],
      ["#source_repository", fakeElement("example/private")],
      ["#source_commit", fakeElement("a".repeat(40))],
      ["#publication_choice", fakeElement("scheduled")],
      ["#production_metadata", fakeElement("{}")],
    ]);
    const { storage } = fakeSessionStorage();
    const replaceState = vi.fn();
    const fetchMock = vi.fn((input: string) => Promise.resolve(
      input.endsWith("/submission-grants")
        ? Response.json({ grant: "signed-grant" }, { status: 201 })
        : Response.json({ submission_id: "019debcf-cb48-7000-8000-000000000001", status: "queued" }, { status: 202 }),
    ));
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const run = new Function("document", "sessionStorage", "location", "history", "fetch", script);
    // eslint-disable-next-line @typescript-eslint/no-unsafe-call
    run(
      { querySelector: (selector: string) => elements.get(selector) ?? null },
      storage,
      { href: "https://submit.test/?oauth=success", search: "?oauth=success", assign: vi.fn() },
      { replaceState },
      fetchMock,
    );

    expect(elements.get("#auth-status")?.textContent).toBe("Signed in with GitHub.");
    expect(elements.get("#oauth-sign-in")?.textContent).toBe("Refresh GitHub sign-in");
    expect(replaceState).toHaveBeenCalledWith(null, "", "/");
    const pending = elements.get("#submission-form")?.listeners.get("submit")?.({ preventDefault: vi.fn() });
    expect(elements.get("#submit-button")?.disabled).toBe(true);
    expect(elements.get("#submit-button")?.attributes.get("aria-busy")).toBe("true");
    expect(elements.get("#submit-label")?.textContent).toBe("Preparing submission…");
    await pending;
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(elements.get("#submit-button")?.disabled).toBe(true);
    expect(elements.get("#submit-button")?.attributes.get("aria-busy")).toBe("false");
    expect(elements.get("#submit-label")?.textContent).toBe("Submission queued");
    expect(elements.get("#result")?.textContent).toContain('"status": "queued"');
  });
});

describe("browser release page", () => {
  it("renders the irreversible action only on its dedicated enabled page", async () => {
    const response = releasePage("production", true);
    const body = await response.text();
    expect(body).toContain("Schedule LeanEval source release");
    expect(body).toContain('href="/api/v1/oauth/start?return_to=%2Frelease%2F"');
    expect(body).toContain('id="release-opt-in-form"');
    expect(body).toContain('id="release-opt-in-submission-id"');
    expect(body).toContain('id="release-opt-in-button"');
    expect(body).toContain('aria-describedby="release-opt-in-help"');
    expect(body).toContain(
      "scheduling confirms that you are authorized to license the accepted source under the Apache License 2.0",
    );
    expect(body).toContain(
      "irreversibly schedules it for release exactly two UTC calendar months after acceptance",
    );
    expect(body).toContain('id="release-opt-in-status" role="status" aria-live="polite"');
    expect(body).toContain('button[aria-busy="true"] .spinner');
    expect(body).toContain('src="/release.js?v=release-opt-in-v2"');
    expect(body).toContain('href="/"');
    expect(body).not.toContain('id="submission-form"');
    expect(body.toLowerCase()).not.toContain("opt out");
  });

  it("fails closed without authentication, form, or script when disabled", async () => {
    const body = await releasePage("production", false).text();
    expect(body).toContain("Source release scheduling is currently unavailable");
    expect(body).not.toContain("/api/v1/oauth/start");
    expect(body).not.toContain('id="release-opt-in-form"');
    expect(body).not.toContain("/release.js");
    expect(body).toContain('href="/"');
  });

  it("contains only the one-way same-origin operation", async () => {
    const response = releaseScript();
    const script = await response.text();
    expect(response.headers.get("content-type")).toContain("text/javascript");
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    expect(() => new Function(script)).not.toThrow();
    expect(script).not.toContain("innerHTML");
    expect(script).toContain('query.get("submission_id")');
    expect(script).toContain('query.get("oauth") === "success"');
    expect(script).toContain('"lean-eval-pending-publication-opt-in"');
    expect(script).toContain('credentials: "same-origin"');
    expect(script).toContain('method: "POST"');
    expect(script).toContain('"/publication-opt-in"');
    expect(script).not.toContain('"/publication-opt-out"');
    expect(script).not.toContain('"idempotency-key"');
    expect(script).not.toContain("uuidV7");
    expect(script).toContain('setReleaseOptInPending(true, "Scheduling source release…")');
  });

  it("preserves the receipt across OAuth and has accessible retry and success states", async () => {
    const script = await releaseScript().text();
    const elements = new Map([
      ["#auth-status", fakeElement()],
      ["#oauth-sign-in", fakeElement()],
      ["#release-opt-in-form", fakeElement()],
      ["#release-opt-in-submission-id", fakeElement()],
      ["#release-opt-in-button", fakeElement()],
      ["#release-opt-in-label", fakeElement("Schedule source release")],
      ["#release-opt-in-status", fakeElement()],
    ]);
    const { storage, stored } = fakeSessionStorage();
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    let optInAttempts = 0;
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(() => {
      optInAttempts += 1;
      return Promise.resolve(optInAttempts === 1
        ? Response.json({ error: "state_unavailable" }, { status: 503 })
        : Response.json({ submission_id: submissionId, publication_choice: "scheduled" }));
    });
    const replaceState = vi.fn();
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const run = new Function("document", "sessionStorage", "location", "history", "fetch", script);
    // eslint-disable-next-line @typescript-eslint/no-unsafe-call
    run(
      { querySelector: (selector: string) => elements.get(selector) ?? null },
      storage,
      {
        href: `https://submit.test/release/?oauth=success&submission_id=${submissionId}`,
        search: `?oauth=success&submission_id=${submissionId}`,
      },
      { replaceState },
      fetchMock,
    );

    expect(elements.get("#auth-status")?.textContent).toBe("Signed in with GitHub.");
    expect(elements.get("#release-opt-in-submission-id")?.value).toBe(submissionId);
    expect(stored.get("lean-eval-pending-publication-opt-in")).toBe(submissionId);
    expect(replaceState).toHaveBeenCalledWith(null, "", `/release/?submission_id=${submissionId}`);

    const optIn = elements.get("#release-opt-in-form")?.listeners.get("submit");
    const firstAttempt = optIn?.({ preventDefault: vi.fn() });
    expect(elements.get("#release-opt-in-button")?.disabled).toBe(true);
    expect(elements.get("#release-opt-in-button")?.attributes.get("aria-busy")).toBe("true");
    expect(elements.get("#release-opt-in-label")?.textContent).toBe("Scheduling source release…");
    expect(elements.get("#release-opt-in-status")?.textContent).toBe("Saving release choice…");
    await firstAttempt;
    expect(elements.get("#release-opt-in-button")?.disabled).toBe(false);
    expect(elements.get("#release-opt-in-button")?.attributes.get("aria-busy")).toBe("false");
    expect(elements.get("#release-opt-in-status")?.textContent).toContain("HTTP 503");
    expect(stored.get("lean-eval-pending-publication-opt-in")).toBe(submissionId);

    await optIn?.({ preventDefault: vi.fn() });
    expect(elements.get("#release-opt-in-button")?.disabled).toBe(true);
    expect(elements.get("#release-opt-in-button")?.attributes.get("aria-busy")).toBe("false");
    expect(elements.get("#release-opt-in-label")?.textContent).toBe("Source release scheduled");
    expect(elements.get("#release-opt-in-status")?.textContent).toBe(
      "LeanEval source release is now scheduled and cannot be changed back to private.",
    );
    expect(stored.has("lean-eval-pending-publication-opt-in")).toBe(false);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`/api/v1/browser/submissions/${submissionId}/publication-opt-in`);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "POST", credentials: "same-origin" });
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toBeUndefined();
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBeUndefined();
  });
});
