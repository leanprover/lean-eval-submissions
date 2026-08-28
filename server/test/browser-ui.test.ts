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
    const response = browserPage("staging", true);
    const body = await response.text();
    expect(response.headers.get("content-security-policy")).toContain("script-src 'self'");
    expect(body).toContain("LeanEval staging intake");
    expect(body).toContain('href="/api/v1/oauth/start"');
    expect(body).toContain('id="submission-form"');
    expect(body).toContain('src="/intake.js?v=auth-spinner-v1"');
    expect(body).toContain('id="auth-status"');
    expect(body).toContain("GitHub sign-in is required before submission");
    expect(body).toContain('id="submit-button"');
    expect(body).toContain('id="submit-spinner"');
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

  it("does not expose the form while intake is disabled", async () => {
    const body = await browserPage("production", false).text();
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
});
