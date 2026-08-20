# Encrypted submission replay

Replay is a recovery and audit path for one archived submission. It is not a
second intake path, a batch decryption service, or a place to give general
archive keys to CI. No replay workflow is enabled yet; the key service and
disposable-runner controls below are launch gates.

## Trust boundary

The existing chonk runner is the orchestrator. It does not decrypt submission
source and it does not execute that source. For each approved replay it creates
a fresh VM, registers that VM as a one-job ephemeral GitHub runner with labels
including `self-hosted`, `chonk`, `lean-eval-replay`, and a request nonce, waits
for the replay job, then destroys the VM in an `always()` cleanup path.

The replay VM may receive only:

- one encrypted archive, pinned by repository, object path, Git blob SHA, and
  ciphertext SHA-256;
- a short-lived, single-use capability authorizing unwrap of that archive's
  data key only;
- public benchmark and evaluator commits pinned by SHA;
- the ordinary ephemeral runner registration and job credentials.

It must not receive the archive identity/master key, Worker State token,
submission-fetch App credential, results-writer credential, release-publisher
credential, or access to another submission. The chonk host must not mount a
persistent workspace into the VM.

## Capability contract

The reviewed key service must sign or otherwise authenticate a capability with
all of these fields:

```json
{
  "schema_version": 1,
  "purpose": "lean-eval-replay",
  "request_id": "<globally unique replay request>",
  "submission_id": "<exact State subject>",
  "archive_repository": "leanprover/lean-eval-audit",
  "archive_path": "<exact ciphertext path>",
  "archive_sha256": "<64 lowercase hexadecimal characters>",
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
3. Chonk creates a fresh VM and registers a one-job ephemeral runner using a
   nonce-specific label. A persistent generic replay runner is a configuration
   error.
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
7. A separate `always()` teardown on chonk destroys the VM and verifies that
   the runner registration disappeared. Failure to confirm destruction is an
   incident and blocks another replay.

No source artifact crosses jobs. Fetch, decrypt, evaluate, and plaintext cleanup
therefore remain in the same disposable replay job, matching the existing
submission workflow's no-public-artifact invariant.

## Approval and launch gates

Replay remains unavailable until all gates have evidence linked from
`INFRASTRUCTURE.md`:

- threat-model review covers malicious Lean, malicious archive metadata,
  compromised VM, compromised chonk orchestrator, replay, confused deputy,
  lost provider responses, clock skew, and teardown failure;
- the key service enforces the exact one-use contract above and its root has a
  documented recovery quorum;
- chonk demonstrates nonce-specific disposable VM creation, runner
  deregistration, network egress policy, and no persistent mount;
- an end-to-end drill recovers one test archive, proves a second unwrap fails,
  proves a different archive fails, and verifies VM destruction;
- a restore drill proves the root can be recovered without copying it into CI;
- workflow review confirms no master identity or broad repository credential
  is available to the replay job or untrusted Lean;
- observability alerts on expired/reused capabilities, digest mismatch,
  teardown failure, and unexpected runner reuse.

Cloudflare Sandbox SDK is deliberately not used here. The project already has
a hardened self-hosted execution path and the security boundary depends on
trusted orchestration plus a fresh per-job VM, not on moving archive keys into
the internet-facing Worker platform.
