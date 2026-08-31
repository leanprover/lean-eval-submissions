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

const PAGE_STYLE = `
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
    nav { border-top: 1px solid currentColor; margin-top: 2rem; padding-top: 1rem; }
    pre { overflow-wrap: anywhere; white-space: pre-wrap; }
`;

function page(title: string, status: string, controls: string, scriptPath?: string): Response {
  const body = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title)}</title>
  <style>${PAGE_STYLE}</style>
</head>
<body>
  <h1>${escapeHtml(title)}</h1>
  ${status}
  ${controls}
  ${scriptPath === undefined ? "" : `<script src="${escapeHtml(scriptPath)}" defer></script>`}
</body>
</html>`;
  return new Response(body, { headers: PAGE_HEADERS });
}

export function browserPage(
  environment: "staging" | "production",
  intakeEnabled: boolean,
  releaseOptInEnabled: boolean,
): Response {
  const title = environment === "staging" ? "LeanEval staging intake" : "LeanEval submissions";
  const status = intakeEnabled
    ? `<p class="notice">${escapeHtml(environment)} intake is enabled. Sign in with GitHub, review every field, and submit.</p>`
    : `<p class="notice disabled">${escapeHtml(environment)} intake is currently disabled.</p>`;
  const authentication = intakeEnabled ? `
    <p><a id="oauth-sign-in" class="button" href="/api/v1/oauth/start">Sign in with GitHub</a></p>
    <p id="auth-status" class="auth-status" role="status" aria-live="polite">GitHub sign-in is required.</p>
  ` : "";
  const submissionForm = intakeEnabled ? `
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
          <option value="withheld">keep accepted source private</option>
        </select>
        <small>Choosing scheduled release confirms that you are authorized to license the submitted source under the Apache License 2.0. Accepted source is released under that license exactly two UTC calendar months after acceptance. Choose private to keep accepted source withheld; you may schedule it later.</small>
      </label>
      <label>Production metadata JSON <textarea id="production_metadata" name="production_metadata" rows="5">{}</textarea></label>
      <button id="submit-button" type="submit" aria-busy="false">
        <span id="submit-label">Submit exact commit</span>
        <span id="submit-spinner" class="spinner" aria-hidden="true"></span>
      </button>
    </form>
    <pre id="result" role="status" aria-live="polite"></pre>
  ` : "";
  const releaseLink = releaseOptInEnabled ? `
    <nav aria-label="Submission actions">
      <a href="/release/">Schedule release for an existing private submission</a>
    </nav>
  ` : "";
  return page(title, status, authentication + submissionForm + releaseLink, intakeEnabled ? "/intake.js?v=intake-v2" : undefined);
}

export function releasePage(
  environment: "staging" | "production",
  releaseOptInEnabled: boolean,
): Response {
  const title = environment === "staging" ? "Schedule staging source release" : "Schedule LeanEval source release";
  const status = releaseOptInEnabled
    ? `<p class="notice">Sign in with GitHub to schedule release for a private ${escapeHtml(environment)} submission.</p>`
    : `<p class="notice disabled">Source release scheduling is currently unavailable.</p>`;
  const controls = releaseOptInEnabled ? `
    <p><a id="oauth-sign-in" class="button" href="/api/v1/oauth/start?return_to=%2Frelease%2F">Sign in with GitHub</a></p>
    <p id="auth-status" class="auth-status" role="status" aria-live="polite">GitHub sign-in is required.</p>
    <p id="release-opt-in-help">If you previously chose to keep accepted source private, scheduling confirms that you are authorized to license the accepted source under the Apache License 2.0 and irreversibly schedules it for release exactly two UTC calendar months after acceptance.</p>
    <form id="release-opt-in-form">
      <label>Private submission ID <input id="release-opt-in-submission-id" name="submission_id" required pattern="[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}" placeholder="UUID from the submission receipt"></label>
      <button id="release-opt-in-button" type="submit" aria-busy="false" aria-describedby="release-opt-in-help">
        <span id="release-opt-in-label">Schedule source release</span>
        <span class="spinner" aria-hidden="true"></span>
      </button>
    </form>
    <p id="release-opt-in-status" role="status" aria-live="polite"></p>
  ` : "";
  return page(
    title,
    status,
    `${controls}<nav aria-label="Submission actions"><a href="/">Submit a new exact commit</a></nav>`,
    releaseOptInEnabled ? "/release.js?v=release-opt-in-v2" : undefined,
  );
}

export function browserScript(): Response {
  const script = `"use strict";
const form = document.querySelector("#submission-form");
const result = document.querySelector("#result");
const authStatus = document.querySelector("#auth-status");
const oauthSignIn = document.querySelector("#oauth-sign-in");
const submitButton = document.querySelector("#submit-button");
const submitLabel = document.querySelector("#submit-label");
const fieldNames = ${JSON.stringify(BROWSER_FIELD_NAMES)};
const authExpiryKey = "lean-eval-github-session-expires-at";
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
oauthSignIn.addEventListener("click", () => {
  saveCurrentValues();
  authStatus.textContent = "Opening GitHub sign-in…";
});
form.addEventListener("submit", async (event) => {
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
  } catch (error) {
    result.textContent += String.fromCharCode(10) + (error instanceof Error ? error.message : "Submission failed");
    setSubmitting(false, "Submit exact commit");
  }
});`;
  return new Response(script, { headers: SCRIPT_HEADERS });
}

export function releaseScript(): Response {
  const script = `"use strict";
const authStatus = document.querySelector("#auth-status");
const oauthSignIn = document.querySelector("#oauth-sign-in");
const releaseOptInForm = document.querySelector("#release-opt-in-form");
const releaseOptInSubmission = document.querySelector("#release-opt-in-submission-id");
const releaseOptInButton = document.querySelector("#release-opt-in-button");
const releaseOptInLabel = document.querySelector("#release-opt-in-label");
const releaseOptInStatus = document.querySelector("#release-opt-in-status");
const authExpiryKey = "lean-eval-github-session-expires-at";
const pendingReleaseOptInKey = "lean-eval-pending-publication-opt-in";
const savedReleaseOptIn = sessionStorage.getItem(pendingReleaseOptInKey);
if (savedReleaseOptIn !== null) releaseOptInSubmission.value = savedReleaseOptIn;
const query = new URLSearchParams(location.search);
const querySubmissionId = query.get("submission_id");
if (querySubmissionId !== null) {
  releaseOptInSubmission.value = querySubmissionId;
  sessionStorage.setItem(pendingReleaseOptInKey, querySubmissionId);
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
const setReleaseOptInPending = (pending, label) => {
  releaseOptInButton.disabled = pending;
  releaseOptInButton.setAttribute("aria-busy", String(pending));
  releaseOptInLabel.textContent = label;
};
oauthSignIn.addEventListener("click", () => {
  const releaseOptInSubmissionId = releaseOptInSubmission.value.trim();
  if (releaseOptInSubmissionId) sessionStorage.setItem(pendingReleaseOptInKey, releaseOptInSubmissionId);
  authStatus.textContent = "Opening GitHub sign-in…";
});
releaseOptInForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const releaseOptInSubmissionId = releaseOptInSubmission.value.trim();
  if (!releaseOptInSubmissionId || !releaseOptInForm.reportValidity()) return;
  sessionStorage.setItem(pendingReleaseOptInKey, releaseOptInSubmissionId);
  setReleaseOptInPending(true, "Scheduling source release…");
  releaseOptInStatus.textContent = "Saving release choice…";
  try {
    const response = await fetch("/api/v1/browser/submissions/" + encodeURIComponent(releaseOptInSubmissionId) + "/publication-opt-in", {
      method: "POST",
      credentials: "same-origin",
    });
    const body = await response.json();
    if (!response.ok || body.publication_choice !== "scheduled") {
      throw new Error("Release scheduling was rejected with HTTP " + response.status);
    }
    sessionStorage.removeItem(pendingReleaseOptInKey);
    setReleaseOptInPending(false, "Source release scheduled");
    releaseOptInButton.disabled = true;
    releaseOptInStatus.textContent = "LeanEval source release is now scheduled and cannot be changed back to private.";
  } catch (error) {
    setReleaseOptInPending(false, "Schedule source release");
    releaseOptInStatus.textContent = error instanceof Error ? error.message : "Release scheduling failed";
  }
});`;
  return new Response(script, { headers: SCRIPT_HEADERS });
}
