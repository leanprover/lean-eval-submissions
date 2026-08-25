# Historical public-evidence legacy adjudications

The ordinary public-evidence resolver deliberately uses one generic issue/run/comment timing contract. Five accepted requests predate that contract or have immutable historical metadata which the generic parser cannot safely infer. `configuration/public-replay-legacy-adjudications-v1.json` is the closed, reviewed exception registry for only those requests.

Each entry binds the exact request and source, issue and body hashes, successful workflow run, `record` job interval, result-introducing commit and blob, and result comment. The edited issue also binds the complete GitHub `userContentEdits` history. The model-name correction binds the exact commit and proves that its before/after result documents differ only by that bucket rename. The two older runs bind their exact delayed run intervals.

The resolver applies an entry only when its request ID and issue repository match. It re-fetches and validates every registry field against current or immutable public GitHub evidence, validates the historical result records against the request, and then performs the same ordinary source probe as every other request. Any changed, missing, expanded, ambiguous, rate-limited, or permission-bound evidence fails closed; it never becomes a source-unavailable verdict merely because the exception cannot be verified.

The registry does not change the generic discovery windows, issue parser, pass-line parser, source probe, or classification rules. Its SHA-256 is included in every newly generated sanitized shard, and offline validation requires the exact registry bytes whenever an adjudicated candidate is present. Older evidence without adjudications remains valid under schema version 1.

Registry mode is explicit. A newly generated shard set and aggregate must all bind the canonical registry bytes; supplying the registry while omitting its digest from a shard, or supplying a registry-bound aggregate without the registry to a downstream validator, fails closed. The no-registry validation path exists only for immutable evidence and aggregates created before this registry. The replay-plan workflow selects that compatibility path only after checking the reviewed aggregate's exact file digest; it cannot silently downgrade a new aggregate.

The merged deployment filters exclude both
`scripts/resolve_public_replay_github_evidence.py` and
`scripts/aggregate_public_replay_github_evidence.py` from both Worker-deployment
trigger blocks. Deployment-workflow tests preserve those exclusions, so later
evidence-only changes cannot silently redeploy the runtime.

The historical edit API is the one remaining API-stability dependency: GitHub GraphQL must continue exposing the complete bounded `userContentEdits` list and revision bodies. If that surface changes or the edit count grows beyond 20, the resolver returns indeterminate evidence and requires a new review; it does not fall back to the current issue body.
