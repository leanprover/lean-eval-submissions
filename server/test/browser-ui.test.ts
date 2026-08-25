import { describe, expect, it } from "vitest";

import { browserPage, browserScript } from "../src/browser-ui";

describe("browser intake page", () => {
  it("renders an enabled same-origin form without inline script", async () => {
    const response = browserPage("staging", true);
    const body = await response.text();
    expect(response.headers.get("content-security-policy")).toContain("script-src 'self'");
    expect(body).toContain("LeanEval staging intake");
    expect(body).toContain('href="/api/v1/oauth/start"');
    expect(body).toContain('id="submission-form"');
    expect(body).toContain('src="/intake.js"');
    expect(body).not.toContain("open-conjectures");
    expect(body).not.toContain('id="source_visibility"');
    expect(body).not.toContain('<option value="public">');
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
    expect(script).toContain("result.textContent");
    expect(script).not.toContain("innerHTML");
    expect(script).toContain("sessionStorage.removeItem");
    expect(script).toContain('source_visibility: "private"');
    expect(script).not.toContain('query.get("source_visibility")');
  });
});
