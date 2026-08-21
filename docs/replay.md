# Encrypted submission replay

Replay is a recovery and audit path for one archived submission. It is not a
second intake path, a batch decryption service, or a place to give general
archive keys to CI. The only workflow is a credential-free manual smoke for an
already-public historical source; it has no State/result/archive/release write
authority. No private or authoritative queue-consuming replay workflow is
enabled; the key service and disposable-runner controls below are launch gates.

The Wave 2 public planner, historical smoke, verdict contract, and disposable-
VM operator sequence are documented in
[`replay-orchestrator.md`](replay-orchestrator.md). That foundation deliberately
leaves the private path nonterminally blocked and does not provision an
execution backend.

## Trust boundary

The orchestrator is a Lean-Eval-owned controller behind a provider-neutral
disposable-executor interface. It does not reuse another project's runner. For
each approved replay it creates a fresh isolated instance from a
pinned image, assigns a unique instance identity and request nonce, executes
exactly one request, and destroys the instance in an unconditional cleanup
path. Whether the eventual adapter uses a local hypervisor, hosted VM API,
sandbox service, or another mechanism is deliberately not part of the stable
request, State, or verdict contracts.

The replay VM may receive only:

- one encrypted archive, pinned by repository, Git commit, canonical object
  path, and ciphertext SHA-256;
- a short-lived, single-use capability authorizing unwrap of that archive's
  data key only;
- public benchmark and evaluator commits pinned by SHA;
- the ordinary ephemeral runner registration and job credentials.

It must not receive the archive identity/master key, Worker State token,
submission-fetch App credential, results-writer credential, release-publisher
credential, or access to another submission. The controller must not mount a
persistent workspace into the instance.

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
the capability is unexpired and unused, and the caller is the intended fresh
VM. The key service records issuance, success/refusal, capability digest,
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
3. The Lean Eval controller creates a fresh isolated instance with a
   nonce-specific identity. A persistent or shared generic replay runner is a
   configuration error.
4. The replay job fetches only pinned public code with
   `persist-credentials:false`, downloads the exact ciphertext, verifies its
   digest, consumes the one-use unwrap capability inside the VM, and decrypts
   onto an encrypted ephemeral filesystem.
5. Before untrusted Lean runs, all `.git` directories and workflow credentials
   are removed. Evaluation uses the same Comparator/landrun boundary and
   resource caps as normal intake. Plaintext, source-derived paths, and command
   output that could reproduce source are excluded from logs and artifacts.
6. The job publishes only the reviewed result/audit projection. It shreds the
   plaintext filesystem key and requests immediate VM shutdown.
7. An unconditional controller teardown destroys the instance and verifies
   that its execution identity disappeared. Failure to confirm destruction is
   an incident and blocks another replay.

No source artifact crosses jobs. Fetch, decrypt, evaluate, and plaintext cleanup
therefore remain in the same disposable replay job, matching the existing
submission workflow's no-public-artifact invariant.

## Approval and launch gates

Replay remains unavailable until all gates have evidence linked from
`INFRASTRUCTURE.md`:

- threat-model review covers malicious Lean, malicious archive metadata,
  compromised instance, compromised Lean Eval controller, replay, confused deputy,
  lost provider responses, clock skew, and teardown failure;
- the key service enforces the exact one-use contract above and its root has a
  documented no-provider-recovery risk acceptance;
- the selected backend demonstrates nonce-specific disposable instance creation,
  deregistration, network egress policy, and no persistent mount;
- an end-to-end drill recovers one test archive, proves a second unwrap fails,
  proves a different archive fails, and verifies VM destruction;
- workflow review confirms no master identity or broad repository credential
  is available to the replay job or untrusted Lean;
- observability alerts on expired/reused capabilities, digest mismatch,
  teardown failure, and unexpected runner reuse.

No execution provider is selected here. A future adapter must satisfy the same
fresh-instance, credential-isolation, network-isolation, teardown, and
attestation contract without changing the provider-neutral request or verdict
formats.
