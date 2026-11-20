# Historical marker telemetry, one fixed query

This temporary manual diagnostic reads existing telemetry for run44675007747;
it creates no Worker, Container, marker, State event or publication attempt.
It has no inputs and is admitted only on the upstream protected main branch,
using the existing `cloudflare-staging` token in the sole query process.

The [Cloudflare API reference](https://developers.cloudflare.com/api/resources/workers/subresources/observability/subresources/telemetry/methods/query/)
documents the query POST, inline ad-hoc parameters, `dry: true` to avoid storing
results, and `result.events.events` with timestamp, metadata, source and optional
Worker version enrichment. It lists **Workers Observability Write** as the
accepted API permission. This workflow does not create or widen credentials:
the existing deployment token might still receive403. That denial stops it.
The offline fixture follows that documented envelope without requiring an
undocumented `success: true` field. String source logs are accepted only as
bounded JSON objects; free text is ignored.
The [official TypeScript API types](https://github.com/cloudflare/cloudflare-typescript/blob/main/src/resources/workers/observability/telemetry.ts)
also distinguish top-level request `view` from the inline filter `parameters`;
the fixed request uses that shape rather than nesting `view` inside parameters.

The helper fixes account `a46b90978a1c29cc4795f30677e7e4b8`, service
`lean-eval-public-guard-a-match`, original version
`71d1eba9-a3eb-4361-ba60-e979fdc40999`, source
`c3e3251001e4a3248b08ff49d0b23aee941a64ac`, and the interval
2026-11-20T04:32:00Z inclusive to04:40:00Z exclusive. Both the query filters
and each returned row must match. The run must echo the exact inline query,
account, dry flag and timeframe. Missing version enrichment or unexpected query
normalization stops with a safe failure category rather than widening the read.

There is one HTTPS POST, no redirect, proxy, pagination or retry. The request
has a60-second socket limit inside a five-minute job. The response is limited to
1MiB,256 rows,16 nesting levels and20,000 JSON nodes; exported JSON is limited to
64KiB. Duplicate keys and nonfinite JSON numbers are refused. An HTTP error body
is closed without reading it. Only a newly constructed report is written;
provider bodies and exception messages never enter files or console output.
The token stays in the query process, is removed from its environment, is never
put in argv/files/outputs, and is not passed to checkout or artifact upload.
The helper clears inherited environment, spawns no children and uses stdlib
only. Process cleanup drops references; it does not revoke the existing token.

The projection allows fixed SDK event/outcome/component/code categories and
bounded numeric timing/status fields. Known exact SDK logger labels become
fixed output categories. It discards commands, paths, contexts, message/error
strings, stacks, arbitrary names, request/response bodies, keys and headers.
Typed errors are accepted only as discrete allowlisted codes, never extracted
from human messages. Synthetic-acceptance failure reasons are a closed enum.
No provider source fields or metadata IDs are dumped for schema discovery.

The sole artifact is `public-marker-historical-telemetry.json`, retained seven
days. Full row limits, missing total counts or truncated Worker events mark
possible truncation. The report never declares a root cause or complete capture.
The old receipt did not bind the marker nonce, so even original-version events
may not identify a unique marker instance. Exec/destroy events report completion
or failure; missing events do not locate a pending startup or prove capacity.

Historical capture remains **unproved**: the c3e campaign renderer copied only
staging variables, omitting the normal config's top-level `observability` block.
Empty results,403, expired logs, missing debug events or a schema refusal leave
the cause unknown and justify no runtime retry. Do not widen targets, enable
logging, change scopes or rerun the marker from this workflow.

This change's helper, workflow, test and documentation paths are excluded from
the current deployment and dispatch-ref promotion path filters, verified by an
offline test against both actual workflow files. It does not mint a tag or
alter environment policy. Before any future dispatch, review exact source,
source CI, an independent second opinion and a concrete single-query operational
packet binding the main SHA and existing token route. Standing completion
authority covers that reviewed metadata read; this source change is not itself
an execution receipt. Remove the temporary diagnostic when its purpose ends.
