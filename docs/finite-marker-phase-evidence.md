# One finite source-free marker with durable phase evidence

The temporary [marker workflow](../.github/workflows/public-marker-phase-staging.yml)
collects evidence for one synthetic marker and stops. It never calls the existing
campaign's Source preparation or guarded replay runner. Its config forces both
replay flags false; when its finite scope is present, the Worker permits only
health and the exact marker/proof/status/fence routes. Staging archive upload and
all other execution routes return404 in this mode. Normal configurations omit
the scope and preserve their existing behavior.

This source feature does not authorize a deployment or runtime attempt. The old
historical telemetry query ended in403 and is closed; these future records cannot
diagnose that old request. Do not rerun unchanged code, enlarge timeouts or enable
debug/provider logging as a fallback.

`runtime-guard-staging/marker.py prepare` reads only the frozen a-match image and
profile metadata. It generates a private runner nonce and config commitments,
without fetching Source, making a source archive, using State/AWS, or creating a
legacy identity. The synthetic marker's disposable age identity is generated only
for the actual marker request, kept in private scratch/memory, and unlinked even
if generation or encryption fails. Neither its contents nor the raw nonce enter
the public report.

The finite scope binds the repository workflow, exact protected immutable source
tag, source/workflow SHA, run ID, attempt1, fixed service/case, image/profile,
nonce commitment and absolute expiry. Authentication verifies the GitHub OIDC
signature and existing repository/issuer/audience/environment protections, then
requires those exact workflow/run/attempt/source claims. Another workflow, later
source, main ref, rerun or expired scope is refused. This feature adds no tag
minting, environment-policy adjustment or main/tag bypass.

The actual Sandbox key remains `r-` plus the first61 nonce characters. Separate
domain-separated commitments cover that actual key and the complete64-character
nonce. A deliberately colliding prefix with another suffix fails before the
Sandbox factory. Scope, actual Worker version metadata, instance commitment and
request commitment bind the journal; version metadata is explicitly admitted by
the strict ownership verifier only when the rendered config requests it. Shared
Python/TypeScript digest vectors exercise the same canonical bytes.

Before the marker POST, a fixed proof request persists a bounded journal in a
separately named object in the **existing** `ReplayTerminalReceipt` namespace.
A distinct inert status read must return that exact prepared binding. Failure
prevents the marker. The server atomically consumes one claim before obtaining a
Sandbox; another start or proof cannot reopen that claim. Status/proof/fence
requests can initialize receipt storage but never obtain a Sandbox or create a
Container. Reads do not renew retention. This is durable phase capture, not proof
that Workers Logs captured anything. Observability is explicitly disabled in the
rendered staging config; no broad SDK logs, tail or new telemetry service exists.

The source path records fixed started/succeeded/failed boundaries for Sandbox
handle acquisition, the three fixed file writes, the acceptance exec, evidence
validation and destruction. Records contain fixed enums, local monotonic elapsed
milliseconds and, on failure, an allowlisted discrete SDK code. No command,
argv/path, error/message/stack/context, content, token, arbitrary metadata or
source field list is exported. Unknown codes remain unclassified. The journal
has at most20 events and8192 encoded bytes; the public report has a16384-byte cap.
The client checks the event sequence and independently rederives outcomes.

`started` records the application boundary immediately before the SDK call; it
does not prove that the provider admitted the request. No SDK lifecycle hooks,
transport changes or explicit configure calls are added. Intended startup
settings remain120000/180000ms, with the existing configured SDK retry budget
calculation potentially330000ms; background application of those settings is not
proved (`configuration_awaited:false`). The marker client still waits300seconds
and acceptance exec retains120000ms. A pending first write or typed
`RPC_TRANSPORT_ERROR` does not locate capacity/startup failure: the report keeps
`provider_boundary:unknown` and the finite route stops.

Evidence RPC waits are bounded to2seconds each and10seconds cumulative overhead.
Failed capture prevents the next noncleanup side effect. Once a handle exists,
destruction is attempted even if a journal write fails. Operation and destruction
outcomes are separate, including double failure; unknown thrown values cannot
manufacture destruction confirmation. Durations use a local monotonic clock,
never subtraction across isolates or requests. Invalid clocks fail closed.

After the single marker POST, at most three inert status reads observe that same
attempt within a90-second deadline, with at most30seconds between reads and a
5-second socket limit each. No second marker is sent. The always-run fence closes
the journal before retirement, and its safe snapshot preserves the last positive
phases. Storage updates serialized after a fence refuse. A pre-fence admitted
SDK call can still be in flight or acknowledge late: fencing metadata is not
cancellation or proof of resource absence. The existing owned control-plane
retirement remains mandatory.

New starts/proofs expire within15minutes of preparation. The journal has fixed
30-minute retention, without renewal, and its alarm preserves a spent/expired
storage-only tombstone. Marker alarms return before unrelated replay/private
cleanup code and never obtain a Sandbox. Expiry/fencing cannot recreate an
execution claim. The namespace is retired with the temporary owned resources.

Cleanup reuses the existing exact-owned lifecycle: disable execution/remove
Container config, remove the bound application, retire the bound classes and
namespace, remove the Worker, and verify absence. SDK destruction, control-plane
cleanup, client transport result, phase capture, profile match and scratch
removal remain separate fields. If retirement fails, the report keeps bounded
ownership IDs and recorded retirement versions for controlled recovery; private
scratch is still removed. No failed cleanup becomes permission to adopt or
delete a different resource. Only the single positively projected report is
uploaded, for7days, after scrub. The report may remain inconclusive even when all
owned resources have been removed.

Before any operation require exact-source CI and independent source/operational
review, a fixed one-attempt packet, existing protected tag/environment admission,
fresh prior-resource absence, exact new ownership/version/image, available
cleanup authority and the positive journal persist/read proof. Missing evidence,
authorization, binding, cleanup or provider cause stops this route. Success also
stops here: a later guarded compatibility operation retains all its existing
runtime-profile, disabled-network, destruction and Source admission gates.
Remove this temporary workflow and finite feature after its purpose ends.
