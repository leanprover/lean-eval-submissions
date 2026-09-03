# Encrypted submission replay

Replay is a recovery and audit path for one archived submission. It is not a
second intake path, a batch decryption service, or a place to give general
archive keys to CI. The enabled workflows are a credential-free manual smoke
for an already-public historical source, a synthetic source-free staging
acceptance, and a protected accepted-archive staging acceptance. The last path
selects one accepted private submission from staging State, consumes one replay
unwrap, verifies its exact plaintext digest and safe tar shape only inside a
fresh network-disabled Sandbox, proves reuse refusal, and discards the source.
None has State/result/archive/release write authority. No authoritative
queue-consuming replay workflow is enabled.

Dispatch the accepted-archive staging acceptance only from an immutable tag
created by the reviewed deployment workflow, never from `main`:

```bash
commit=<reviewed-40-character-commit>
gh workflow run accepted-archive-replay-staging.yml \
  --repo leanprover/lean-eval-submissions \
  --ref "lean-eval-dispatch/$commit" \
  -f submission_id=<accepted-staging-submission-uuidv7> \
  -f confirm_staging_acceptance=true
```

The workflow rejects any other ref before reading State or assuming AWS
authority. The Worker independently verifies the GitHub OIDC token's protected
environment and exact tag-to-commit binding.

The Wave 2 public planner, historical smoke, verdict contract, and disposable-
VM operator sequence are documented in
[`replay-orchestrator.md`](replay-orchestrator.md). Cloudflare Sandbox is the
selected initial private execution backend; the stable controller contract is
provider-neutral.

Resolved legacy public evidence advances through the blocked, source-free seed
contract in
[`historical-public-replay-plan.md`](historical-public-replay-plan.md). It is
not queue authority and cannot be handed to a replay runner until the separate
legacy-result State contract is reviewed.

## Trust boundary

The orchestrator is a Lean-Eval-owned controller behind a provider-neutral
disposable-executor interface. It does not reuse another project's runner. For
each approved replay it creates a fresh Cloudflare Sandbox from the reviewed
image, assigns a unique request nonce, starts exactly one fixed background
command, and destroys the Sandbox after an authenticated terminal poll.
Cloudflare is not part of the stable request, State, archive, capability, or
verdict contracts.

The disposable executor may receive only:

- one encrypted archive, pinned by repository, Git commit, canonical object
  path, and ciphertext SHA-256;
- the plaintext per-archive age identity returned by a short-lived, single-use
  unwrap immediately before handoff; never a root or shared archive key;
- public benchmark and evaluator commits pinned by SHA;
- public, non-secret execution configuration.

It must not receive a master key, Worker State token, submission-fetch App
credential, results-writer credential, release-publisher credential, AWS
credential, GitHub OIDC token, Cloudflare credential, or access to another
submission. The controller must not mount a persistent workspace into it.

## Capability contract

The machine-readable provider-neutral envelope and claims are defined in
[`key-capability-contract.md`](key-capability-contract.md). Shape/binding
validation is deliberately separate from the adapter's atomic one-use store;
no validator command can unwrap an identity.

The reviewed key service must sign or otherwise authenticate a capability with
all of these fields:

```json
{
  "schema_version": 1,
  "purpose": "lean-eval-replay",
  "request_id": "<globally unique replay request>",
  "submission_id": "<exact State subject>",
  "archive_repository": "leanprover/lean-eval-audit",
  "archive_commit": "<40 lowercase hexadecimal characters>",
  "archive_path": "archives/<first two UUID hex>/<submission UUID>.tar.age",
  "archive_ciphertext_sha256": "<SHA-256 of the exact encrypted blob bytes>",
  "data_key_id": "<exact envelope key identifier>",
  "runner_nonce": "<exact disposable runner nonce>",
  "issued_at": "<canonical UTC milliseconds>",
  "expires_at": "<canonical UTC milliseconds; minutes, not hours>",
  "max_uses": 1
}
```

The unwrap decision must fail closed unless every field matches the request,
the capability is unexpired and unused, and the runner nonce matches the fresh
executor request. The key service records issuance, success/refusal, capability digest,
submission, runner nonce, and destruction acknowledgement without recording a
key or plaintext. A lost response cannot make a capability reusable.

The archive format must use envelope encryption: one random data key per
submission archive, authenticated metadata binding the submission and archive
digest, and a hardware- or KMS-protected root that is never exported. The
current shared age identity does not meet the per-submission capability
boundary and is not sufficient for automated replay.

## Job sequence

1. An authorized operator dispatches a replay for one State submission ID and
   records the incident, audit, or recovery reason.
2. A trusted preparation job resolves the immutable State event, ciphertext
   object, digests, evaluator commit, benchmark commit, and authorization.
3. The Lean Eval controller performs a source-free prewarm before State start
   or archive unwrap. The prewarm atomically claims a durable, nonce-specific
   binding to the exact task, attempt, execution profile, measurement config,
   and image before its first Sandbox RPC, then creates the fresh Sandbox. The
   later source-bearing start refreshes and reuses that same nonce and binding;
   every start and status call must match it before the Sandbox is looked up.
   It starts one fixed background process. A duplicate matching start is
   idempotent; a differently bound duplicate fails closed. A persistent or
   shared generic replay executor is a configuration error.
4. The trusted controller verifies the exact ciphertext, consumes the one-use
   unwrap capability, drops AWS authority, and sends only that ciphertext, its
   per-archive identity, the nonce, and public expectations to the Sandbox.
   The Sandbox has public network access disabled and decrypts only inside its
   ephemeral filesystem.
5. Before untrusted Lean runs, all `.git` directories and workflow credentials
   are removed. Evaluation uses the same Comparator/landrun boundary and
   resource caps as normal intake. Plaintext, source-derived paths, and command
   output that could reproduce source are excluded from logs and artifacts.
6. Short status requests each use a fresh protected-environment OIDC token and
   bind the same nonce, task, attempt, execution profile, measurement config,
   and image. Transient client disconnects and control-plane RPC failures do
   not stop or duplicate the fixed process.
7. The image deletes encoded key/ciphertext inputs after decoding and removes
   plaintext, extracted source, metrics, and evaluator output in an
   unconditional `finally` path. The job publishes only the reviewed
   result/audit projection.
8. The terminal poll validates bounded process output and durably records the
   exact source-free HTTP status/body in a separate nonce-specific receipt
   Durable Object, bound to the nonce, task, attempt, execution profile,
   measurement configuration, and image. Receipt preparation is an atomic
   first-writer-wins operation, so concurrent terminal polls cannot replace the
   canonical result; confirmation never regresses to pending. It then destroys
   the Sandbox and durably marks destruction confirmed before returning that
   status/body. A lost response at any boundary replays the pending destroy or
   exact confirmed outcome; evaluator failures use the same protocol. The
   binding and receipt are written transactionally with their dedicated alarm
   and deleted after 24 hours. Terminal receipt retention starts when the
   terminal outcome is observed, beyond both the six-hour job bound and the
   seven-hour stale-runner recovery threshold.
9. Failure to confirm destruction fails the request and is retried within the
   controller deadline. If the controller disappears, the process cleanup
   still removes private material and the five-minute idle timeout stops the
   disposable Sandbox; State recovery records the lost runner before any retry.

No source artifact crosses jobs. Fetch, decrypt, evaluate, and plaintext cleanup
therefore remain in the same disposable replay job, matching the existing
submission workflow's no-public-artifact invariant.

## Readiness and launch gates

Replay remains unavailable until all gates have evidence linked from
`INFRASTRUCTURE.md`:

- threat-model review covers malicious Lean, malicious archive metadata,
  compromised Sandbox, compromised Lean Eval controller, replay, confused deputy,
  lost provider responses, clock skew, and teardown failure;
- the key service enforces the exact one-use contract above and its root has a
  documented no-provider-recovery risk acceptance;
- the selected backend demonstrates nonce-specific disposable instance creation,
  network egress denial, bounded capacity, and no persistent mount;
- one pre-enable staging acceptance test recovers one test archive, proves a
  second unwrap fails, proves a different archive fails, and verifies Sandbox
  destruction;
- workflow review confirms no master identity or broad repository credential
  is available to the replay job or untrusted Lean.

Cloudflare Sandbox is the initial provider. The reviewed staging and production
profile uses one 12 GiB `standard-4` instance at most; SSH and public network
access are disabled. This is the approved production capacity ceiling: work
that exceeds it is recorded as a resource-limit outcome, not retried on an
unreviewed larger profile. Production replay stays disabled until every
remaining gate has evidence. A future adapter must satisfy the same
fresh-instance, credential-isolation, network-isolation, teardown, and evidence
contract without changing the provider-neutral request or verdict formats.
