const PAGE_HEADERS = {
  "cache-control": "no-store",
  "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self' https://github.com; base-uri 'none'; frame-ancestors 'none'",
  "content-type": "text/html; charset=utf-8",
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
} as const;

const SCRIPT_HEADERS = {
  "cache-control": "no-store",
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

const BROWSER_FIELD_NAMES = [
  "problem_id",
  "problem_group",
  "statement_revision",
  "declared_model",
  "source_repository",
  "source_commit",
  "publication_choice",
  "production_metadata",
] as const;

export function browserPage(
  environment: "staging" | "production",
  intakeEnabled: boolean,
  releaseOptOutEnabled: boolean,
): Response {
  const title = environment === "staging" ? "LeanEval staging intake" : "LeanEval submissions";
  const status = intakeEnabled
    ? `<p class="notice">${escapeHtml(environment)} intake is enabled. Sign in with GitHub, review every field, and submit.</p>`
    : `<p class="notice disabled">${escapeHtml(environment)} intake is currently disabled.</p>`;
  const form = intakeEnabled ? `
    <p><a id="oauth-sign-in" class="button" href="/api/v1/oauth/start">Sign in with GitHub</a></p>
    <p id="auth-status" class="auth-status" role="status" aria-live="polite">GitHub sign-in is required before submission.</p>
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
        <small>Choosing scheduled release confirms that you are authorized to license the submitted source under the Apache License 2.0. Accepted source is released under that license exactly two UTC calendar months after acceptance. Choose withheld to opt out.</small>
      </label>
      <label>Production metadata JSON <textarea id="production_metadata" name="production_metadata" rows="5">{}</textarea></label>
      <button id="submit-button" type="submit" aria-busy="false">
        <span id="submit-label">Submit exact commit</span>
        <span id="submit-spinner" class="spinner" aria-hidden="true"></span>
      </button>
    </form>
    <pre id="result" role="status" aria-live="polite"></pre>
    ${releaseOptOutEnabled ? `<section id="release-opt-out" hidden>
      <h2>Release choice</h2>
      <p id="release-opt-out-help">This submission is scheduled for LeanEval automatic source release if accepted. Opting out changes its publication choice to withheld.</p>
      <button id="release-opt-out-button" type="button" aria-busy="false" aria-describedby="release-opt-out-help">
        <span id="release-opt-out-label">Opt out of LeanEval source release</span>
        <span class="spinner" aria-hidden="true"></span>
      </button>
      <p id="release-opt-out-status" role="status" aria-live="polite"></p>
    </section>` : ""}
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
    button { display: inline-flex; align-items: center; gap: .55rem; }
    button:disabled { cursor: not-allowed; opacity: .75; }
    button[aria-busy="true"]:disabled { cursor: wait; }
    .auth-status { font-weight: 600; }
    .spinner { display: none; width: .9rem; height: .9rem; border: .16rem solid currentColor; border-right-color: transparent; border-radius: 50%; animation: spin .7s linear infinite; }
    button[aria-busy="true"] .spinner { display: inline-block; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 1.5s; } }
    .notice { border-left: .3rem solid #2d7; padding: .75rem 1rem; }
    .disabled { border-left-color: #d75; }
    pre { overflow-wrap: anywhere; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>${escapeHtml(title)}</h1>
  ${status}
  ${form}
  ${intakeEnabled ? '<script src="/intake.js?v=release-opt-out-v1" defer></script>' : ""}
</body>
</html>`;
  return new Response(body, { headers: PAGE_HEADERS });
}

export function browserScript(): Response {
  const script = `"use strict";
const form = document.querySelector("#submission-form");
const result = document.querySelector("#result");
const authStatus = document.querySelector("#auth-status");
const oauthSignIn = document.querySelector("#oauth-sign-in");
const submitButton = document.querySelector("#submit-button");
const submitLabel = document.querySelector("#submit-label");
const releaseOptOut = document.querySelector("#release-opt-out");
const releaseOptOutButton = document.querySelector("#release-opt-out-button");
const releaseOptOutLabel = document.querySelector("#release-opt-out-label");
const releaseOptOutStatus = document.querySelector("#release-opt-out-status");
const fieldNames = ${JSON.stringify(BROWSER_FIELD_NAMES)};
const authExpiryKey = "lean-eval-github-session-expires-at";
let releaseOptOutSubmissionId = null;
const saved = sessionStorage.getItem("lean-eval-pending-submission");
if (saved) {
  try {
    const values = JSON.parse(saved);
    for (const name of fieldNames) {
      const element = document.querySelector("#" + name);
      if (element && typeof values[name] === "string") element.value = values[name];
    }
  } catch { sessionStorage.removeItem("lean-eval-pending-submission"); }
}
const query = new URLSearchParams(location.search);
for (const name of fieldNames) {
  const element = document.querySelector("#" + name);
  const value = query.get(name);
  if (element && value !== null) element.value = value;
}
if (query.get("oauth") === "success") {
  sessionStorage.setItem(authExpiryKey, String(Date.now() + 55 * 60 * 1000));
  const cleanUrl = new URL(location.href);
  cleanUrl.searchParams.delete("oauth");
  history.replaceState(null, "", cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
}
const authExpiresAt = Number(sessionStorage.getItem(authExpiryKey));
if (Number.isFinite(authExpiresAt) && authExpiresAt > Date.now()) {
  authStatus.textContent = "Signed in with GitHub.";
  oauthSignIn.textContent = "Refresh GitHub sign-in";
} else {
  sessionStorage.removeItem(authExpiryKey);
}
const currentValues = () => Object.fromEntries(fieldNames.map((name) => [name, document.querySelector("#" + name)?.value ?? ""]));
const saveCurrentValues = () => sessionStorage.setItem("lean-eval-pending-submission", JSON.stringify(currentValues()));
const setSubmitting = (submitting, label) => {
  submitButton.disabled = submitting;
  submitButton.setAttribute("aria-busy", String(submitting));
  submitLabel.textContent = label;
};
const setReleaseOptOutPending = (pending, label) => {
  releaseOptOutButton.disabled = pending;
  releaseOptOutButton.setAttribute("aria-busy", String(pending));
  releaseOptOutLabel.textContent = label;
};
oauthSignIn?.addEventListener("click", () => {
  saveCurrentValues();
  authStatus.textContent = "Opening GitHub sign-in…";
});
form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  setSubmitting(true, "Preparing submission…");
  result.textContent = "Preparing submission…";
  const values = currentValues();
  sessionStorage.setItem("lean-eval-pending-submission", JSON.stringify(values));
  try {
    const metadata = JSON.parse(values.production_metadata);
    const grantResponse = await fetch("/api/v1/browser/submission-grants", { method: "POST" });
    if (grantResponse.status === 401) {
      sessionStorage.removeItem(authExpiryKey);
      authStatus.textContent = "GitHub sign-in is required; redirecting…";
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
    setSubmitting(false, "Submission queued");
    submitButton.disabled = true;
    if (
      values.publication_choice === "scheduled" &&
      typeof body.submission_id === "string" &&
      releaseOptOut !== null &&
      releaseOptOutStatus !== null
    ) {
      releaseOptOutSubmissionId = body.submission_id;
      releaseOptOut.hidden = false;
      releaseOptOutStatus.textContent = "LeanEval automatic source release remains scheduled.";
    }
  } catch (error) {
    result.textContent += String.fromCharCode(10) + (error instanceof Error ? error.message : "Submission failed");
    setSubmitting(false, "Submit exact commit");
  }
});
releaseOptOutButton?.addEventListener("click", async () => {
  if (releaseOptOutSubmissionId === null) return;
  setReleaseOptOutPending(true, "Opting out of source release…");
  releaseOptOutStatus.textContent = "Saving release opt-out…";
  try {
    const response = await fetch("/api/v1/browser/submissions/" + encodeURIComponent(releaseOptOutSubmissionId) + "/publication-opt-out", {
      method: "POST",
      credentials: "same-origin",
    });
    const body = await response.json();
    if (!response.ok || body.publication_choice !== "withheld") {
      throw new Error("Release opt-out was rejected with HTTP " + response.status);
    }
    setReleaseOptOutPending(false, "Automatic source release opted out");
    releaseOptOutButton.disabled = true;
    releaseOptOutStatus.textContent = "LeanEval automatic source release is opted out while the publication choice remains withheld.";
  } catch (error) {
    setReleaseOptOutPending(false, "Opt out of LeanEval source release");
    releaseOptOutStatus.textContent = error instanceof Error ? error.message : "Release opt-out failed";
  }
});`;
  return new Response(script, { headers: SCRIPT_HEADERS });
}
