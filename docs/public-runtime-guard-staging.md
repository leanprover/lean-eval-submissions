# Finite PUBLIC runtime guard staging

This temporary compatibility check runs four fixed cases, then must be removed
with the replay campaign. It creates no State events, builds no images and
changes no qualification or production service. Dispatch requires the normal
CI-gated protected `lean-eval-dispatch/<exact SHA>` tag and `replay-staging`
environment. There are no workflow inputs. Do not run it until the exact source,
packet, credentials and temporary names have been checked.

The two existing tiny `two_plus_two` fixtures cover both remaining immutable
PUBLIC image-source families. Each family runs once with its qualified profile
and once with only the expected kernel changed to an explicitly artificial
staging value. Negative digests are temporary configuration, never qualification
evidence. Both fixtures use official Lean 4:v4.30.0-rc2 and the unchanged nanoda
checker, full 330-minute runner cap and 12 GiB memory cap.

| Family | Qualified profile | Image source | Benchmark |
| --- | --- | --- | --- |
| a | `7d4027e5022973640bf768facd4ac56762cc327c542c560b5cc34aa59fcd771e` | `6a92b4acee1c50a661cac949e3092eb64e012157` | `11081d345a580a0f3c46699240f28e4f41fbf9fe` |
| b | `51c052d354ad46c5b77b7282e80c48a95d765f606c9395bd585a0028407c9823` | `7eb77aa8c2ef7f4d598c77240ea9effbb248dce2` | `6ee592acc40eac8d1a7102dafed368b341b3cd42` |

The helper closes source/request/result identities to the existing reviewed plan
`d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e`,
matrix `a674707eea7a9556576c8dcbe57bcf6b4f44362d2bdfd47895fb7c783554f39c`
and runner contract
`6d341a642dfd6aa9092228269da6761000bf0818128ce3f35cb259bd8fb2303f`.
Anonymous exact-commit public fetches and the existing source adapters produce
the real handoffs; no arbitrary source, image, command or profile is accepted.

The serial cases are `a-match`, `a-mismatch`, `b-match`, `b-mismatch`. Each owns
only Worker `lean-eval-public-guard-<case>`, application `le-pg-<case>` and its
newly created ReplaySandbox/ReplayTerminalReceipt namespace pair. Names must be
absent before deployment; an existing target is a collision, never adopted.
A failed case or cleanup stops the remaining queued cases. No production pilot
Worker, receipt or nonce is used.

After deployment the existing strict rollout and ownership checks bind the
active Worker version, health commit, image/application version, ownership tag
and exact namespace script/class. A disposable synthetic age marker then calls
the existing staging-acceptance route. It must report network blocked and exact
sandbox destruction, plus architecture/kernel/CPU matching the frozen profile.
Any observed drift stops before submitting the public fixture. An unknown
observation outcome retains the target for diagnosis instead of inferring
sandbox destruction.

Each public execution reserves cleanup before start and uses the existing full
profile/handoff request. A positive case must validate an accepted nanoda
verdict with destruction confirmed. A negative case must return exactly the
existing typed `command_failed/runtime_profile_mismatch` response and confirm
cleanup. The reviewed Worker emits that detail only for matching process command,
failed status, exit 42, empty stdout and exact stderr. Thus staging tests SDK
command preservation and multiline execution without introducing an inspection
or arbitrary execution route. Untouched source-base64 ordering is separately
proved by the constant-command tests, not by a remote filesystem claim.

Fixture preparation is bounded 5 minutes, deployment 27 minutes, execution 20 minutes
and resource cleanup 6 minutes in a 70-minute case job. The tiny-fixture status
window is 15 minutes; missing it is an incomplete staging check, never a fabricated
runner timeout. These orchestration limits do not reduce the full runner cap.
Every HTTP exchange uses the existing bounded, no-redirect transport. Cloudflare
credentials occur only in deployment/cleanup; public execution has only staging
OIDC and no State, AWS or private-source authority.

Cleanup first requires exact sandbox destruction. An interrupted public start
can retry only the existing exact idempotent cleanup route. An ambiguous synthetic
observation has no such recovery receipt and requires operator diagnosis. Before
retirement, the helper rechecks original full resource ownership. It deploys a
fixed 503-only stub, deleting exactly the two recorded DO classes, and checks
active version/etag, fixed health commit/tag, complete variable bindings, empty
container runtime and absence of the original namespace IDs. It then verifies
the captured application ID/version/date before deleting that application,
checks absence, rechecks the stub before deleting the Worker, and checks final
Worker and namespace absence. There is no force-delete or wildcard adoption.

Any ownership drift, incomplete deployment or failed cleanup stops operations
and retains a source-free target receipt for an operator. The helper deliberately
does not guess how to resume a partially retired service or delete unknown
resources. The only uploaded artifact is `receipt.json`: source-free identities,
observed runtime, outcome and cleanup state. Requests, archives, age identities,
tokens and generated configuration are never uploaded.

The namespace inventory follows Cloudflare's [documented paginated API](https://developers.cloudflare.com/api/resources/durable_objects/subresources/namespaces/methods/list/).
No staging result retroactively attests the completed old pilot or grants State
qualification authority. Production guard activation and any public chain need
a separate fresh readiness packet after these cases succeed.
