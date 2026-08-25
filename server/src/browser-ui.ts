const PAGE_HEADERS = {
  "cache-control": "no-store",
  "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self' https://github.com; base-uri 'none'; frame-ancestors 'none'",
  "content-type": "text/html; charset=utf-8",
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
} as const;

const SCRIPT_HEADERS = {
  "cache-control": "public, max-age=300",
  "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
  "content-type": "text/javascript; charset=utf-8",
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
} as const;

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function browserPage(environment: "staging" | "production", intakeEnabled: boolean): Response {
  const title = environment === "staging" ? "LeanEval staging intake" : "LeanEval submissions";
  const status = intakeEnabled
    ? `<p class="notice">${escapeHtml(environment)} intake is enabled. Sign in with GitHub, review every field, and submit.</p>`
    : `<p class="notice disabled">${escapeHtml(environment)} intake is currently disabled.</p>`;
  const form = intakeEnabled ? `
    <p><a class="button" href="/api/v1/oauth/start">Sign in with GitHub</a></p>
    <form id="submission-form">
      <label>Problem ID <input id="problem_id" name="problem_id" required pattern="[a-z][a-z0-9_]*"></label>
      <label>Problem group
        <select id="problem_group" name="problem_group">
          <option value="formalization-evaluation">formalization-evaluation</option>
          <option value="software-verification">software-verification</option>
        </select>
      </label>
      <label>Statement revision <input id="statement_revision" name="statement_revision" required type="number" min="1" step="1" value="1"></label>
      <label>Declared model <input id="declared_model" name="declared_model" required maxlength="256"></label>
      <label>Source repository <input id="source_repository" name="source_repository" required placeholder="owner/repository"></label>
      <label>Exact source commit <input id="source_commit" name="source_commit" required pattern="[0-9a-f]{40}" minlength="40" maxlength="40"></label>
      <label>Publication choice
        <select id="publication_choice" name="publication_choice">
          <option value="scheduled">scheduled release (default)</option>
          <option value="withheld">withheld (opt out)</option>
        </select>
        <small>Accepted source is scheduled for release after two calendar months unless you opt out.</small>
      </label>
      <label>Production metadata JSON <textarea id="production_metadata" name="production_metadata" rows="5">{}</textarea></label>
      <button type="submit">Submit exact commit</button>
    </form>
    <pre id="result" role="status" aria-live="polite"></pre>
  ` : "";
  const body = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title)}</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { max-width: 48rem; margin: 3rem auto; padding: 0 1rem; line-height: 1.45; }
    form { display: grid; gap: 1rem; }
    label { display: grid; gap: .35rem; font-weight: 600; }
    input, select, textarea, button, .button { box-sizing: border-box; font: inherit; padding: .65rem; }
    textarea { font-family: ui-monospace, monospace; }
    button, .button { cursor: pointer; width: fit-content; }
    .notice { border-left: .3rem solid #2d7; padding: .75rem 1rem; }
    .disabled { border-left-color: #d75; }
    pre { overflow-wrap: anywhere; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>${escapeHtml(title)}</h1>
  ${status}
  ${form}
  ${intakeEnabled ? '<script src="/intake.js" defer></script>' : ""}
</body>
</html>`;
  return new Response(body, { headers: PAGE_HEADERS });
}

export function browserScript(): Response {
  const script = `"use strict";
const form = document.querySelector("#submission-form");
const result = document.querySelector("#result");
const fieldNames = ["problem_id", "problem_group", "statement_revision", "declared_model", "source_repository", "source_commit", "publication_choice", "production_metadata"];
const saved = sessionStorage.getItem("lean-eval-pending-submission");
const query = new URLSearchParams(location.search);
for (const name of fieldNames) {
  const element = document.querySelector("#" + name);
  const value = query.get(name);
  if (element && value !== null) element.value = value;
}
if (saved) {
  try {
    const values = JSON.parse(saved);
    for (const name of fieldNames) {
      const element = document.querySelector("#" + name);
      if (element && typeof values[name] === "string") element.value = values[name];
    }
  } catch { sessionStorage.removeItem("lean-eval-pending-submission"); }
}
form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  result.textContent = "Preparing submission…";
  const values = Object.fromEntries(fieldNames.map((name) => [name, document.querySelector("#" + name)?.value ?? ""]));
  sessionStorage.setItem("lean-eval-pending-submission", JSON.stringify(values));
  try {
    const metadata = JSON.parse(values.production_metadata);
    const grantResponse = await fetch("/api/v1/browser/submission-grants", { method: "POST" });
    if (grantResponse.status === 401) {
      location.assign("/api/v1/oauth/start");
      return;
    }
    const grantBody = await grantResponse.json();
    if (!grantResponse.ok || typeof grantBody.grant !== "string") throw new Error(JSON.stringify(grantBody));
    const response = await fetch("/api/v1/browser/submissions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        grant: grantBody.grant,
        submission: {
          problem_id: values.problem_id,
          problem_group: values.problem_group,
          statement_revision: Number(values.statement_revision),
          declared_model: values.declared_model,
          source_repository: values.source_repository,
          source_commit: values.source_commit,
          source_visibility: "private",
          publication_choice: values.publication_choice,
          production_metadata: metadata,
        },
      }),
    });
    const body = await response.json();
    result.textContent = JSON.stringify(body, null, 2);
    if (!response.ok) throw new Error("Submission was rejected with HTTP " + response.status);
    sessionStorage.removeItem("lean-eval-pending-submission");
  } catch (error) {
    result.textContent += "\n" + (error instanceof Error ? error.message : "Submission failed");
  }
});`;
  return new Response(script, { headers: SCRIPT_HEADERS });
}
