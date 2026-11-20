# Finite PUBLIC runtime guard staging

This temporary compatibility check runs four fixed cases and must be removed
with the replay campaign. It writes no State, builds no images and changes no
qualification or production configuration. Its helper lives outside both
`scripts/` and `server/`; its changed paths trigger general CI and the immutable
dispatch promoter, not a normal service deployment.

Dispatch requires the CI-gated protected `lean-eval-dispatch/<exact SHA>` tag.
The existing `cloudflare-staging` environment supplies its existing deployment
token only to trusted deployment/cleanup steps. No token is copied or added.
Execution receives staging OIDC, with no Cloudflare, State, AWS or private-source
credentials. Read-only operator preflight must verify account, subdomain, name
absence and the actual namespace-list parser using an identified operator
credential. The environment token is first exercised by the workflow's
pre-creation reads; its delete and migration permissions are first exercised
in the first case's cleanup. Account-level Worker and Container permissions are needed;
this is not a claim of per-script token restriction.

The environment currently permits protected branches, including main, but does
not permit this tag. Before dispatch, the operator must snapshot its policy and
rules and all protected branch names, temporarily preserve those branches by
exact name and permit only the exact approved immutable tag. Restore the
original protected-branches-only policy as soon as the run terminates, including
failure. Control-plane recovery uses the identified operator credential and
does not keep the tag window open. No wildcard tag rule is allowed. This policy
change requires its own bound operational packet and review; the workflow does
not modify it. Existing production authentication and other environments stay
outside this operation.

Generic staging authentication checks configured `cloudflare-staging`, the
existing staging audience, and a protected immutable tag matching its commit.
It does not restrict the workflow filename or compare caller SHA to deployed
SHA. Any workflow admitted by that environment and immutable-ref policy has
that staging authority. The fixed client independently checks complete endpoint
health identity immediately before every POST. Temporary environment policy
must remain bound to the approved tag while the run performs cleanup. This admission
also permits other matching workflows at that tag, including `set-staging-intake.yml`
(which changes normal staging intake) and read-only `verify-state-writer.yml`;
only this campaign is to be dispatched during the window. The image publisher
can enter the environment but refuses a tag in its main-only check. The normal
deployment's staging jobs also require main. Other main staging deployments
and code merges must be held during the temporary policy operation.

The two existing `two_plus_two` fixtures cover both remaining immutable PUBLIC
image-source families. Each family runs with its qualified profile and with
only the expected kernel changed to an artificial staging value. Negative
digests are configuration, never qualification evidence. Both fixtures use
official Lean 4:v4.30.0-rc2 and unchanged nanoda, 330-minute runner and 12 GiB caps.

| Family | Qualified profile | Image source | Benchmark |
| --- | --- | --- | --- |
| a | `7d4027e5022973640bf768facd4ac56762cc327c542c560b5cc34aa59fcd771e` | `6a92b4acee1c50a661cac949e3092eb64e012157` | `11081d345a580a0f3c46699240f28e4f41fbf9fe` |
| b | `51c052d354ad46c5b77b7282e80c48a95d765f606c9395bd585a0028407c9823` | `7eb77aa8c2ef7f4d598c77240ea9effbb248dce2` | `6ee592acc40eac8d1a7102dafed368b341b3cd42` |

The helper closes request/result/source identities to existing plan
`d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e`,
matrix `a674707eea7a9556576c8dcbe57bcf6b4f44362d2bdfd47895fb7c783554f39c`
and contract
`6d341a642dfd6aa9092228269da6761000bf0818128ce3f35cb259bd8fb2303f`.
Exact public fetches and existing adapters prepare the real archives/handoffs.
The task ID is the existing result/config-derived ID, isolated from production
by new Worker namespaces; each nonce is fresh. There are no workflow inputs.

Cases `a-match`, `a-mismatch`, `b-match`, `b-mismatch` run serially. Each owns only
Worker `lean-eval-public-guard-<case>`, application `le-pg-<case>` and its new
ReplaySandbox/ReplayTerminalReceipt namespaces. Existing names are collisions,
never adopted. Any failure stops later cases. Pinned Wrangler and its rollout
helper run from `server/`, using the installed locked package.

Wrangler's application list contains summaries, not full configuration. Ownership
resolves the single exact-name summary by its canonical UUID using `containers
info`, checks matching ID/name/version/creation time/image, account and the
Worker's verified ReplaySandbox namespace, then passes the unmodified detailed
record to the strict configuration verifier. List and detail share the existing
readback deadline. A detail lookup failure is retryable; only a fresh successful
list without the named application proves absence. Name collisions are checked
from the list before deployment and are never adopted.

After strict rollout and ownership verification, the existing synthetic
staging-acceptance route must report actual architecture/kernel/CPU, network
blocked and exact marker-sandbox destruction. Observed drift stops before the
public fixture. Observation and replay may use different hosts: the replay's
own guard independently verifies its actual host against the bound profile.
The artificial negative kernel is not inferred from the first host alone.

A positive case must validate an accepted nanoda verdict. A negative case must
produce exactly `command_failed/runtime_profile_mismatch`. The reviewed Worker
emits this only for matching process command, failed status, exit 42, empty
stdout and exact stderr. This tests SDK command preservation and multiline
execution without an inspection or arbitrary execution route. Source-base64
ordering is proved separately by constant-command tests. Successful cases also
require actual observation and public cleanup destruction receipts, then full
resource absence.

A tiny proof does not bound Sandbox restoration: the first real pilot took
49m17s in its executor step. Positive polling therefore allows 60 minutes;
negative polling allows 5. Observation/start requests allow 300 seconds plus
fresh OIDC; status/cleanup use bounded calls. At most two consecutive transient
status RPC failures are tolerated, never deterministic guard failures.
Controller expiry records `incomplete`, not mathematical `timed_out` or success.
The runner's 330-minute limit is unchanged.

Each case has a 140-minute job ceiling: dependencies 5, source preparation 5,
deployment 27, execution 80, cleanup 10 minutes, with 13 minutes for Actions
setup/tail. Four serial cases cost at most 560 job-minutes, with one Sandbox at
a time. Ambiguous transport stays distinct from typed pre-Sandbox refusals;
only exact known 400/401/503 refusal bodies establish that no Sandbox began.
Tool diagnostics are bounded and redact credentials; requests/keys are not logged.
Health, OIDC mint and status transport failures share the same two-failure poll
allowance. A terminal poll failure records incomplete. Deployment uses bounded
read-only ownership capture before waiting for rollout, so rollout failure
retains IDs; an unrecorded creation has an explicit controlled-recovery notice.
The deploy phase bounds preflight to 2.5 minutes, CLI to 3, ownership to 3 and rollout
to 15, leaving 3.5 minutes within its 27-minute cap. Cleanup has no OIDC credential;
HTTP destruction is attempted only by the execution step without a CF token.

Cleanup follows the [documented Container retirement order](https://developers.cloudflare.com/containers/guides/migrate-to-durable-object-scheduling-policy/#move-the-application).
First it verifies complete original Worker/app/namespace ownership. It deploys
the same entrypoint with execution flags false and `containers: []`, keeping
both old DO classes, bindings and migrations. Removing Container configuration
does not delete the app. Exact disabled code/health/tag/version and namespace
IDs are verified before deleting the recorded app; the API deletion cancels its
instances. App absence is required before a fixed 503-only stub deletes exactly
the recorded DO classes/bindings. Only then can namespace and Worker absence be
proved. No exports, runtime implementation or scheduling policy is changed.

Known propagation and transient failures have bounded retries, including the
first cleanup ownership read, container inventories, every stub recheck, Worker
re-delete and final namespace listing. App absence has ten bounded observations.
Strict cleanup retries transient edge health failures but immediately refuses
actual identity drift or an already-retired Worker. Deployment diagnostics remain
bounded and credential-redacted before exact readback. Foreign identities
are refused. Successful checkpoints record the disabled/stub version, allowing
known intermediate recovery; unrecorded versions require controlled recovery.
The whole cleanup has a shared nine-minute deadline inside its ten-minute step,
including both deploys, app deletion and all readbacks. There is no force delete.
The exact current SDK/provider sequence still needs its first live verification;
any actual constraint stops for root without broadening deletion authority.

For these owned synthetic/public fixtures only, ambiguous observation or a
staging timeout may use that exact ownership-verified control-plane fence and
delete sequence. This is cancellation, not an HTTP destruction receipt or a
successful case. The receipt records `incomplete`, cleanup mode and final
control-plane absence separately. It never invents a verdict, qualification or
erasure acknowledgement. This exception changes no private/production cleanup
contract. Unknown ownership, unknown stub identity or an API constraint stops
and retains the source-free target for controlled recovery.

Only `receipt.json` is uploaded: source-free identities, runtime, outcome and
cleanup state. Requests, archives, age identities, tokens and config are not.
Cloudflare's [namespace inventory API](https://developers.cloudflare.com/api/resources/durable_objects/subresources/namespaces/methods/list/)
is paginated using `total_count`, not optional `total_pages`. Live preflight must
exercise this exact helper against the account and validate its paging fields
and 64 KiB response cap before any resource creation. No staging result
retroactively attests the old pilot or grants State qualification authority.
A production public chain needs its own fresh State/source readiness packet.
