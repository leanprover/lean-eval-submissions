import { describe, expect, it, vi } from "vitest";

import { browserPage, browserScript } from "../src/browser-ui";

type FakeEvent = Readonly<{ preventDefault: () => void }>;
type FakeListener = (event: FakeEvent) => unknown;

function fakeElement(value = "") {
  const attributes = new Map<string, string>();
  const listeners = new Map<string, FakeListener>();
  return {
    attributes,
    disabled: false,
    hidden: false,
    listeners,
    textContent: "",
    value,
    addEventListener(name: string, listener: FakeListener) {
      listeners.set(name, listener);
    },
    setAttribute(name: string, attributeValue: string) {
      attributes.set(name, attributeValue);
    },
  };
}

describe("browser intake page", () => {
  it("renders an enabled same-origin form without inline script", async () => {
    const response = browserPage("staging", true, true);
    const body = await response.text();
    expect(response.headers.get("content-security-policy")).toContain("script-src 'self'");
    expect(body).toContain("LeanEval staging intake");
    expect(body).toContain('href="/api/v1/oauth/start"');
    expect(body).toContain('id="submission-form"');
    expect(body).toContain('src="/intake.js?v=release-opt-out-v1"');
    expect(body).toContain('id="auth-status"');
    expect(body).toContain("GitHub sign-in is required before submission");
    expect(body).toContain('id="submit-button"');
    expect(body).toContain('id="submit-spinner"');
    expect(body).toContain('id="release-opt-out" hidden');
    expect(body).toContain('id="release-opt-out-button"');
    expect(body).toContain('aria-describedby="release-opt-out-help"');
    expect(body).toContain("Opt out of LeanEval source release");
    expect(body).toContain("Opting out changes its publication choice to withheld");
    expect(body).toContain('id="release-opt-out-status" role="status" aria-live="polite"');
    expect(body).toContain('button[aria-busy="true"] .spinner');
    expect(body).not.toContain("open-conjectures");
    expect(body).not.toContain('id="source_visibility"');
    expect(body).not.toContain('<option value="public">');
    expect(body).toContain('<option value="scheduled">scheduled release (default)</option>');
    expect(body.indexOf('value="scheduled"')).toBeLessThan(body.indexOf('value="withheld"'));
    expect(body).toContain("authorized to license the submitted source under the Apache License 2.0");
    expect(body).toContain("exactly two UTC calendar months after acceptance");
    expect(body).toContain("Choose withheld to opt out");
    expect(body).not.toContain("<script>");
  });

  it("does not render or advertise release opt-out while its API is disabled", async () => {
    const body = await browserPage("staging", true, false).text();
    expect(body).toContain('id="submission-form"');
    expect(body).not.toContain('id="release-opt-out"');
    expect(body).not.toContain("Opt out of LeanEval source release");
    expect(body).not.toContain("Opting out changes its publication choice to withheld");
  });

  it("does not expose the form while intake is disabled", async () => {
    const body = await browserPage("production", false, true).text();
    expect(body).toContain("production intake is currently disabled");
    expect(body).not.toContain('id="submission-form"');
    expect(body).not.toContain("/api/v1/oauth/start");
  });

  it("uses textContent for all API output", async () => {
    const response = browserScript();
    const script = await response.text();
    expect(response.headers.get("content-type")).toContain("text/javascript");
    expect(response.headers.get("cache-control")).toBe("no-store");
    // Compile without invoking the generated browser program so malformed string escaping fails this test.
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    expect(() => new Function(script)).not.toThrow();
    expect(script).toContain("result.textContent");
    expect(script).not.toContain("innerHTML");
    expect(script).toContain("sessionStorage.removeItem");
    expect(script.indexOf("if (saved)")).toBeLessThan(script.indexOf("const query ="));
    expect(script).toContain('query.get(name)');
    expect(script).toContain('query.get("oauth") === "success"');
    expect(script).toContain('authStatus.textContent = "Signed in with GitHub."');
    expect(script).toContain('oauthSignIn?.addEventListener("click"');
    expect(script).toContain('setSubmitting(true, "Preparing submission…")');
    expect(script).toContain('submitButton.setAttribute("aria-busy", String(submitting))');
    expect(script).toContain('setSubmitting(false, "Submit exact commit")');
    expect(script).toContain('credentials: "same-origin"');
    expect(script).toContain('method: "POST"');
    expect(script).toContain('"/publication-opt-out"');
    expect(script).not.toContain('"idempotency-key"');
    expect(script).not.toContain("uuidV7");
    expect(script).toContain('setReleaseOptOutPending(true, "Opting out of source release…")');
    expect(script).toContain('source_visibility: "private"');
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
      ["#release-opt-out", fakeElement()],
      ["#release-opt-out-button", fakeElement()],
      ["#release-opt-out-label", fakeElement("Opt out of LeanEval source release")],
      ["#release-opt-out-status", fakeElement()],
      ["#problem_id", fakeElement("two_plus_two")],
      ["#problem_group", fakeElement("formalization-evaluation")],
      ["#statement_revision", fakeElement("1")],
      ["#declared_model", fakeElement("Browser smoke")],
      ["#source_repository", fakeElement("example/private")],
      ["#source_commit", fakeElement("a".repeat(40))],
      ["#publication_choice", fakeElement("scheduled")],
      ["#production_metadata", fakeElement("{}")],
    ]);
    const stored = new Map<string, string>();
    const sessionStorage = {
      getItem: (key: string) => stored.get(key) ?? null,
      removeItem: (key: string) => stored.delete(key),
      setItem: (key: string, value: string) => stored.set(key, value),
    };
    const replaceState = vi.fn();
    const fetchMock = vi.fn((input: string) => Promise.resolve(
      input.endsWith("/submission-grants")
        ? Response.json({ grant: "signed-grant" }, { status: 201 })
        : Response.json({ submission_id: "019debcf-cb48-7000-8000-000000000001", status: "queued" }, { status: 202 }),
    ));
    // Execute the complete emitted browser program against a minimal DOM so its visible state transitions are tested.
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const run = new Function("document", "sessionStorage", "location", "history", "fetch", script);
    // The generated function intentionally receives browser-global fakes whose behavior is asserted below.
    // eslint-disable-next-line @typescript-eslint/no-unsafe-call
    run(
      { querySelector: (selector: string) => elements.get(selector) ?? null },
      sessionStorage,
      { href: "https://submit.test/?oauth=success", search: "?oauth=success", assign: vi.fn() },
      { replaceState },
      fetchMock,
    );

    expect(elements.get("#auth-status")?.textContent).toBe("Signed in with GitHub.");
    expect(elements.get("#oauth-sign-in")?.textContent).toBe("Refresh GitHub sign-in");
    expect(replaceState).toHaveBeenCalledWith(null, "", "/");

    const submit = elements.get("#submission-form")?.listeners.get("submit");
    expect(submit).toBeDefined();
    const pending = submit?.({ preventDefault: vi.fn() });
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

  it("reveals a same-origin release opt-out with accessible retry and success states", async () => {
    const script = await browserScript().text();
    const optOutPanel = fakeElement();
    optOutPanel.hidden = true;
    const elements = new Map([
      ["#submission-form", fakeElement()],
      ["#result", fakeElement()],
      ["#auth-status", fakeElement()],
      ["#oauth-sign-in", fakeElement()],
      ["#submit-button", fakeElement()],
      ["#submit-label", fakeElement("Submit exact commit")],
      ["#release-opt-out", optOutPanel],
      ["#release-opt-out-button", fakeElement()],
      ["#release-opt-out-label", fakeElement("Opt out of LeanEval source release")],
      ["#release-opt-out-status", fakeElement()],
      ["#problem_id", fakeElement("two_plus_two")],
      ["#problem_group", fakeElement("formalization-evaluation")],
      ["#statement_revision", fakeElement("1")],
      ["#declared_model", fakeElement("Browser smoke")],
      ["#source_repository", fakeElement("example/private")],
      ["#source_commit", fakeElement("a".repeat(40))],
      ["#publication_choice", fakeElement("scheduled")],
      ["#production_metadata", fakeElement("{}")],
    ]);
    const sessionStorage = {
      getItem: () => null,
      removeItem: vi.fn(),
      setItem: vi.fn(),
    };
    const submissionId = "019debcf-cb48-7000-8000-000000000001";
    let optOutAttempts = 0;
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>((input) => {
      if (input.endsWith("/submission-grants")) {
        return Promise.resolve(Response.json({ grant: "signed-grant" }, { status: 201 }));
      }
      if (input === "/api/v1/browser/submissions") {
        return Promise.resolve(Response.json({ submission_id: submissionId, status: "queued" }, { status: 202 }));
      }
      optOutAttempts += 1;
      return Promise.resolve(optOutAttempts === 1
        ? Response.json({ error: "state_unavailable" }, { status: 503 })
        : Response.json({ submission_id: submissionId, publication_choice: "withheld" }));
    });
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const run = new Function("document", "sessionStorage", "location", "history", "fetch", script);
    // eslint-disable-next-line @typescript-eslint/no-unsafe-call
    run(
      { querySelector: (selector: string) => elements.get(selector) ?? null },
      sessionStorage,
      { href: "https://submit.test/", search: "", assign: vi.fn() },
      { replaceState: vi.fn() },
      fetchMock,
    );

    const submit = elements.get("#submission-form")?.listeners.get("submit");
    await submit?.({ preventDefault: vi.fn() });
    expect(optOutPanel.hidden).toBe(false);
    expect(elements.get("#release-opt-out-status")?.textContent).toBe(
      "LeanEval automatic source release remains scheduled.",
    );

    const optOut = elements.get("#release-opt-out-button")?.listeners.get("click");
    const firstAttempt = optOut?.({ preventDefault: vi.fn() });
    expect(elements.get("#release-opt-out-button")?.disabled).toBe(true);
    expect(elements.get("#release-opt-out-button")?.attributes.get("aria-busy")).toBe("true");
    expect(elements.get("#release-opt-out-label")?.textContent).toBe("Opting out of source release…");
    expect(elements.get("#release-opt-out-status")?.textContent).toBe("Saving release opt-out…");
    await firstAttempt;
    expect(elements.get("#release-opt-out-button")?.disabled).toBe(false);
    expect(elements.get("#release-opt-out-button")?.attributes.get("aria-busy")).toBe("false");
    expect(elements.get("#release-opt-out-status")?.textContent).toContain("HTTP 503");

    const secondAttempt = optOut?.({ preventDefault: vi.fn() });
    await secondAttempt;
    expect(elements.get("#release-opt-out-button")?.disabled).toBe(true);
    expect(elements.get("#release-opt-out-button")?.attributes.get("aria-busy")).toBe("false");
    expect(elements.get("#release-opt-out-label")?.textContent).toBe("Automatic source release opted out");
    expect(elements.get("#release-opt-out-status")?.textContent).toBe(
      "LeanEval automatic source release is opted out while the publication choice remains withheld.",
    );

    const firstRequest = fetchMock.mock.calls[2];
    const retryRequest = fetchMock.mock.calls[3];
    expect(firstRequest?.[0]).toBe(`/api/v1/browser/submissions/${submissionId}/publication-opt-out`);
    expect(firstRequest?.[1]).toMatchObject({
      method: "POST",
      credentials: "same-origin",
    });
    expect(firstRequest?.[1]?.headers).toBeUndefined();
    expect(firstRequest?.[1]?.body).toBeUndefined();
    expect(retryRequest?.[1]?.headers).toBeUndefined();
  });
});
