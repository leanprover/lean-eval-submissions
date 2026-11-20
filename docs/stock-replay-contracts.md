# Inactive stock replay contracts

The four new schemas and `scripts/stock_replay_contract.py` are preparation-only
contracts for the four benchmark/toolchain configurations in the reviewed final
delta matrix. They admit no replay task. No controller, Worker, runner, workflow,
qualification reader or image recipe imports the module or accepts its versions.
Existing profile1/request1 and finite archive profile2/request2 keep measurement1
and their unchanged schemas, digest domains and runtime behavior.

The new family is profile3 + measurement2 + request3, with a configuration-lock1.
A lock binds the original benchmark commit/tree, literal root or toolchain-directory
selection, selected file Git-blob/SHA256 pairs, original registry/selector/driver
bytes, generator source bindings and a separate derived-output manifest digest.
The caller must independently prove the repository/commit/tree, regular-file
modes and provenance of supplied bytes. Checking their hashes is not a Git-object
or deterministic-generation proof. The validator does not run a generator or
verify the supplied derived-output digest.

Configuration provenance is a closed union. Multi-toolchain selections use an
actual version1 verification receipt with exactly its existing Result ID,
benchmark commit, literal toolchain and run ID/attempt. The receipt does not sign
selected-directory/file hashes; those are derived from bound benchmark bytes.
Root-only selections use the original Results v2 file's Git commit/path/SHA256,
canonical selected-record SHA256 and root toolchain blob. No receipt or run fields
are invented for those records. Original Results/source provenance is not proof
of original workflow execution, present classification, archive authority or
permission to enqueue. Those independently verified authorities remain rollout
gates for both branches. No private historical records are included in fixtures.

Profile3 binds the exact bundled stock Lean/Lake/exporter/native-checker commit,
bundled ammkrn NanoDa family, real bubblewrap source, release/binary bytes,
configuration digest, image/platform fields and one explicit source capability:

- Public Git: `fetch_method="git"`, `archive_capacity_class=null`; no archive fallback.
- Public or private archive: `fetch_method="archive"`, `archive_capacity_class="legacy_10mib"`.

Request3 must match that capability, source visibility, canonical archive path,
submission identity and network policy. Large archive capacity is unsupported.
Future baked static image locks, external qualifications, reviewed Worker
capacity settings and runtime extraction/size checks must bind the same selector
before admission. The benchmark configuration lock describes benchmark bytes;
the hashed execution profile describes source capacity. Neither certifies an
image, arbitrary binary hash or bubblewrap build by itself. Existing archive
file-key envelopes keep their UUID/ciphertext purpose; they do not become a
checker-family identifier or require re-encryption just for profile3.

Measurement2 declares Solution build/export plus bundled NanoDa/native Lean
checker order. Its positive safe-integer limits are prospective bytes, not
approved production limits. Its digest changes the task ID under the existing
formula; it grants no reset/reconfiguration authority for an earlier task. The
fixture uses synthetic limits and an unqualified synthetic CPU/image/build.

Digest domains retain the existing profile/measurement v1 namespaces, with
strict version/family validation before hashing:

```
C = SHA256("lean-eval-benchmark-configuration-lock-v1" || NUL || canonical(lock1))
P = SHA256("lean-eval-replay-execution-profile-v1" || NUL || canonical(profile3))
M = SHA256("lean-eval-replay-measurement-config-v1" || NUL || canonical(measurement2))
T = "rt1_" || SHA256("lean-eval-replay-task-v1" || NUL || ResultID || NUL || M)
```

`tests/fixtures/stock-replay-contract-v1.json` is a synthetic parity vector, not
accepted qualification or genuine historical Git bytes. Tests pin independent
canonical UTF-8 hashes (including Unicode), legacy schema bytes, all four bounded
configuration identities, provenance/source cross-products and active Python
reader rejection. TypeScript/runtime support is deliberately absent; consumers
must add equivalent strict vectors before any later activation.

The adapter, trace reducer, baked lock, image/Worker qualification and admission
are separate work. Stock compare/parser/I/O exceptions share exit1: successful
exports, no kernels and driver1 cannot establish mathematical rejection. Future
reduction must use the trusted per-problem termination record bound to the one
problem/generation and conservatively return existing `failed/verdict_invalid`
for that undistinguished prefix. No verdict code is implemented here. The future
measurement journal must bind request/task/attempt/profile/nonce and a fresh
inode/generation, preserve all comparator descriptors/argv/isolation, and require
both checker records. Component smokes for all stock families pass; image/provider
namespace, authoritative measurements and replay admission remain unqualified.

Offline check (with the existing pinned JSON Schema requirements installed):

```
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s tests -p test_stock_replay_contract.py -v
```
