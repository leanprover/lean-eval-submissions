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


const PRODUCTION_FIELDS = ["input_tokens", "cached_input_tokens", "output_tokens", "wall_time_seconds", "cost_usd", "billing_mode", "component_models", "harness", "human_involvement", "web_access", "prompt", "notes"] as const;
function productionFields(): string {
  return `
<fieldset><legend>How this proof was produced (optional, self-reported)</legend>
<p>Figures should cover this problem, including unsuccessful attempts. Leave unknown values blank. Put totals shared across several problems in notes, rather than these numeric fields. Cached input is part of total input. For subscriptions, estimate the API-equivalent cost, not the subscription price; explain pricing and accounting assumptions in notes.</p>
<label>Total input tokens <input id="meta_input_tokens" name="meta_input_tokens" type="number" min="0" max="9007199254740991" step="1"></label>
<label>Cached input tokens (included in total input) <input id="meta_cached_input_tokens" name="meta_cached_input_tokens" type="number" min="0" max="9007199254740991" step="1"></label>
<label>Output tokens <input id="meta_output_tokens" name="meta_output_tokens" type="number" min="0" max="9007199254740991" step="1"></label>
<label>Time producing this proof (seconds) <input id="meta_wall_time_seconds" name="meta_wall_time_seconds" type="number" min="0" max="31536000" step="any"></label>
<label>Estimated API-equivalent cost (USD) <input id="meta_cost_usd" name="meta_cost_usd" type="number" min="0" max="1000000" step="any"></label>
<label>How you paid <select id="meta_billing_mode" name="meta_billing_mode"><option value="">Not reported</option><option value="api">API</option><option value="subscription">Subscription</option><option value="unknown">Unknown</option></select></label>
<label>Models and versions (comma separated) <input id="meta_component_models" name="meta_component_models" type="text" maxlength="4112"></label>
<label>Harness or orchestrator <input id="meta_harness" name="meta_harness" type="text" maxlength="1024"></label>
<label>Human involvement <input id="meta_human_involvement" name="meta_human_involvement" type="text" maxlength="1024"></label>
<label>Web access during production <select id="meta_web_access" name="meta_web_access"><option value="">Not reported</option><option value="true">Yes</option><option value="false">No</option></select></label>
<label>Prompt or link to prompts <input id="meta_prompt" name="meta_prompt" type="text" maxlength="8192"></label>
<label>Production notes and accounting assumptions <input id="meta_notes" name="meta_notes" type="text" maxlength="4096"></label>
</fieldset><input id="production_metadata" name="production_metadata" type="hidden" value="{}">
`;
}

const PRODUCTION_SCRIPT = `
const productionFields = ["input_tokens", "cached_input_tokens", "output_tokens", "wall_time_seconds", "cost_usd", "billing_mode", "component_models", "harness", "human_involvement", "web_access", "prompt", "notes"];
let productionBase = {};
function showProductionMetadata(value) {
  productionBase = value;
  for (const name of productionFields) {
    const element = document.querySelector("#meta_" + name);
    if (!element) continue;
    const item = value[name];
    element.value = item === undefined ? "" : Array.isArray(item) ? item.join(", ") : String(item);
  }
}
function readProductionMetadata() {
  const hidden = document.querySelector("#production_metadata");
  const original = hidden ? JSON.parse(hidden.value || "{}") : productionBase;
  const metadata = { ...original };
  for (const name of productionFields) {
    const element = document.querySelector("#meta_" + name);
    if (!element) continue;
    const value = element.value.trim();
    if (value === "") { delete metadata[name]; continue; }
    if (["input_tokens", "cached_input_tokens", "output_tokens", "wall_time_seconds", "cost_usd"].includes(name)) {
      const number = Number(value);
      if (!Number.isFinite(number) || number < 0 || (name.endsWith("tokens") && !Number.isSafeInteger(number))) throw new Error("Enter a valid nonnegative number for " + name);
      metadata[name] = number;
    } else if (name === "web_access") metadata[name] = value === "true";
    else if (name === "component_models") metadata[name] = value.split(",").map(x => x.trim()).filter(Boolean);
    else metadata[name] = value;
  }
  if ((metadata.cached_input_tokens ?? 0) > (metadata.input_tokens ?? Number.MAX_SAFE_INTEGER)) throw new Error("Cached input cannot exceed total input tokens.");
  return metadata;
}
`;

const BROWSER_FIELD_NAMES = [
  "problem_id",
  "declared_model",
  "source_repository",
  "source_commit",
  "publication_choice",
  "production_metadata",
  ...PRODUCTION_FIELDS.map(name => "meta_" + name),
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
    .prerequisite { border: 1px solid currentColor; padding: 1rem; }
    .prerequisite h2 { margin-top: 0; }
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
    <section class="prerequisite" aria-labelledby="repository-access">
      <h2 id="repository-access">Required repository access</h2>
      <p>Install both read-only GitHub Apps on only the repository you intend to submit:</p>
      <ol>
        <li><a href="https://github.com/apps/lean-eval-source-reader/installations/new" target="_blank" rel="noopener noreferrer">Install LeanEval Source Reader</a> for intake verification.</li>
        <li><a href="https://github.com/apps/lean-eval-bot/installations/new" target="_blank" rel="noopener noreferrer">Install lean-eval-bot</a> for the archive and evaluation workflow.</li>
      </ol>
      <p>The server checks that both Apps can read the named repository and exact commit before accepting or dispatching the submission.</p>
    </section>
    <form id="submission-form">
      <label>Problem ID <input id="problem_id" name="problem_id" required pattern="[A-Za-z0-9][A-Za-z0-9_\\-]{0,127}" maxlength="128"></label>
      <small>LeanEval resolves the problem group and current statement revision from the live catalog. The problem must currently be open for submissions.</small>
      <label>Declared model <input id="declared_model" name="declared_model" required maxlength="256"></label>
      <label>Source repository <input id="source_repository" name="source_repository" required placeholder="owner/repository"></label>
      <label>Exact source commit <input id="source_commit" name="source_commit" required pattern="[0-9a-f]{40}" minlength="40" maxlength="40"></label>
      <label>Publication choice
        <select id="publication_choice" name="publication_choice">
          <option value="scheduled">scheduled release (recommended default)</option>
          <option value="withheld">keep accepted source private</option>
        </select>
        <small>Scheduled release is the recommended default because it lets the community inspect and reuse accepted solutions. Choosing it confirms that you are authorized to license the submitted source under the Apache License 2.0. Accepted source is released under that license exactly two UTC calendar months after acceptance. Public repositories must use scheduled release. Keep accepted source private only for a private repository and a specific reason not to publish it; the public result will show the solution as withheld, and you may irreversibly schedule release later.</small>
      </label>
      ${productionFields()}
      <section class="prerequisite" aria-labelledby="submission-terms">
        <h2 id="submission-terms">Submission terms</h2>
        <ul>
          <li>You are authorized to submit this source and the metadata above.</li>
          <li>LeanEval may privately fetch, archive, build, and execute the exact commit.</li>
          <li>The encrypted source archive may be retained indefinitely, including when evaluation rejects or fails.</li>
          <li>The result and submitted metadata may be displayed publicly; do not include secrets.</li>
          <li>If scheduled, accepted source will be published under Apache License 2.0 exactly two UTC calendar months after acceptance.</li>
          <li>If initially withheld, a later decision to schedule publication is irreversible.</li>
        </ul>
        <label><span><input id="terms_accepted" name="terms_accepted" type="checkbox" required> I accept the current LeanEval submission terms.</span></label>
      </section>
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
  return page(title, status, authentication + submissionForm + releaseLink, intakeEnabled ? "/intake.js?v=intake-v4" : undefined);
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
  const script = PRODUCTION_SCRIPT + `"use strict";
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
    const terms = document.querySelector("#terms_accepted");
    if (terms) terms.checked = values.terms_accepted === true;
  } catch { sessionStorage.removeItem("lean-eval-pending-submission"); }
}
const query = new URLSearchParams(location.search);
for (const name of fieldNames) {
  const element = document.querySelector("#" + name);
  const value = query.get(name);
  if (element && value !== null) element.value = value;
}
const prefilled = document.querySelector("#production_metadata");
if (!saved && prefilled && prefilled.value !== "{}") showProductionMetadata(JSON.parse(prefilled.value));
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
const currentValues = () => ({
  ...Object.fromEntries(fieldNames.map((name) => [name, document.querySelector("#" + name)?.value ?? ""])),
  terms_accepted: document.querySelector("#terms_accepted")?.checked === true,
});
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
    const metadata = readProductionMetadata();
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
          schema_version: 2,
          problem_id: values.problem_id,
          declared_model: values.declared_model,
          source_repository: values.source_repository,
          source_commit: values.source_commit,
          publication_choice: values.publication_choice,
          production_metadata: metadata,
          terms_version: "lean-eval-intake-terms-v1",
          terms_accepted: values.terms_accepted,
        },
      }),
    });
    const body = await response.json();
    result.textContent = JSON.stringify(body, null, 2);
    if (!response.ok) {
      const fieldByError = {
        problem_not_open_for_submission: "problem_id",
        public_source_cannot_be_withheld: "publication_choice",
      };
      const field = Object.hasOwn(fieldByError, body.error)
        ? document.querySelector("#" + fieldByError[body.error])
        : null;
      if (field) {
        field.setCustomValidity(body.error.replaceAll("_", " "));
        field.reportValidity();
        field.setCustomValidity("");
      }
      throw new Error("Submission was rejected with HTTP " + response.status);
    }
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


export function metadataPage(): Response {
  return page("Edit production information", "<p>Only the submitter can add or correct this self-reported information. Changes and their dates remain in the public history.</p>", `
    <a id="metadata-sign-in" href="/api/v1/oauth/start?return_to=%2Fmetadata%2F">Sign in with GitHub</a>
    <form id="metadata-form">${productionFields()}<button id="metadata-save" disabled>Save production information</button></form>
    <p id="metadata-status" role="status" aria-live="polite">Loading your result…</p>
  `, "/metadata.js");
}

export function metadataScript(): Response {
  const script = PRODUCTION_SCRIPT + `
const query = new URLSearchParams(location.search);
const savedTarget = sessionStorage.getItem("lean-eval-metadata-target");
const target = query.has("result_id") ? { result_id: query.get("result_id"), results_commit: query.get("results_commit") } : savedTarget ? JSON.parse(savedTarget) : {};
sessionStorage.setItem("lean-eval-metadata-target", JSON.stringify(target));
const status = document.querySelector("#metadata-status");
const save = document.querySelector("#metadata-save");
let context;
let etag;
const endpoint = "/api/v1/results/" + encodeURIComponent(target.result_id || "") + "/metadata";
const allowed = [...productionFields, "credit_identity"];
async function load() {
  save.disabled = true;
  const response = await fetch(endpoint + "?results_commit=" + encodeURIComponent(target.results_commit || ""));
  if (response.status === 401) { status.textContent = "Sign in with the GitHub account that submitted this proof."; return; }
  if (!response.ok) throw new Error("Could not load the result (" + response.status + "). Check the account and result link.");
  context = await response.json(); etag = response.headers.get("etag");
  const metadata = Object.fromEntries(Object.entries(context.production_metadata).filter(([key]) => allowed.includes(key)));
  document.querySelector("#production_metadata").value = JSON.stringify(metadata);
  showProductionMetadata(metadata);
  const pending = sessionStorage.getItem("lean-eval-pending-metadata");
  if (pending) {
    const draft = JSON.parse(pending);
    if (draft.result_id === target.result_id && draft.revision === context.revision) showProductionMetadata(draft.metadata);
    sessionStorage.removeItem("lean-eval-pending-metadata");
  }
  status.textContent = "Ready to edit. Blank fields are unknown; existing unrelated information is preserved.";
  save.disabled = false;
}
document.querySelector("#metadata-sign-in").addEventListener("click", () => {
  if (context) sessionStorage.setItem("lean-eval-pending-metadata", JSON.stringify({ result_id: target.result_id, revision: context.revision, metadata: readProductionMetadata() }));
});
async function write(url, method, payload, revision) {
  const response = await fetch(url, { method, headers: { "content-type": "application/json", "idempotency-key": context.idempotency_key, ...(revision ? { "if-match": revision } : {}) }, body: JSON.stringify(payload) });
  if (response.status === 409 || response.status === 412) throw new Error("This result changed. Reload before saving; your edits have not been applied.");
  if (!response.ok) throw new Error("Could not save (" + response.status + "). Your edits remain in the form.");
  return response.json();
}
document.querySelector("#metadata-form").addEventListener("submit", async event => {
  event.preventDefault(); if (!context) return; save.disabled = true;
  try {
    const metadata = readProductionMetadata();
    if (!Object.keys(metadata).length) throw new Error("Enter at least one field. Existing metadata cannot all be cleared.");
    if (context.claim_required) {
      const claim = await write("/api/v1/results/claims", "POST", { result_id: target.result_id, results_commit: target.results_commit });
      void claim;
      const refreshed = await fetch(endpoint + "?results_commit=" + encodeURIComponent(target.results_commit));
      if (!refreshed.ok) throw new Error("Claim succeeded; reload before saving the metadata.");
      context = await refreshed.json(); etag = refreshed.headers.get("etag");
    }
    // Legacy backfill is field-wise and cannot remove old entries. New submission
    // amendments replace the full current metadata object, preserving unedited fields.
    const url = context.submission_id ? "/api/v1/submissions/" + context.submission_id + "/metadata" : endpoint;
    if (!context.submission_id && Object.keys(context.production_metadata).some(key => allowed.includes(key) && !(key in metadata))) throw new Error("Existing historical fields cannot be cleared; replace their values instead.");
    await write(url, "PATCH", { production_metadata: metadata }, etag);
    await load();
    status.textContent = "Saved. The public site will show the update after its next refresh.";
  } catch (error) { status.textContent = error instanceof Error ? error.message : String(error); }
  finally { save.disabled = false; }
});
load().catch(error => { status.textContent = error instanceof Error ? error.message : String(error); });
`;
  return new Response(script, { headers: SCRIPT_HEADERS });
}
