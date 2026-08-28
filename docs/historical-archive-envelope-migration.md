# Historical archive file-key rewrap

Historical migration is limited to the 439 unique schema-version-1 archives
bound to accepted private results by the canonical crosswalk at
[`dfdcbc0d…json`](../evidence/historical-replay/private-crosswalks/dfdcbc0da3a3526f8a26e6a69cefa41cbcd92de7608752193b742fcd92b00a67.json).
It does not migrate unbound archives, the legacy schema-version-2 object, or
existing schema-version-3 objects.

For each selected archive, the migration:

1. rederives the full archive plan from audit commit
   `ad356e7bc5a2d650d9902ac3f6d352a0164360bc`;
2. verifies the canonical crosswalk digest, audit inventory digest, 639 bound
   result rows, and 439 unique schema-version-1 plan-entry digests;
3. uses the pinned `filippo.io/age` v1.3.1 detached-header APIs to recover only
   the archive's 16-byte age file key;
4. wraps that file key with the version-2 provider adapter context;
5. copies the original `.tar.age` bytes to the stable UUIDv7 path; and
6. writes a schema-version-3 sidecar containing a version-2
   `age-file-key-v1` envelope.

The final validator independently rereads every pinned source object and
sidecar. It requires equal ciphertext SHA-256 and size, performs a bounded
chunk-by-chunk comparison of source and target ciphertext, and requires the
target sidecar to contain exactly the source's permitted preserved metadata
plus the intended schema-version-3 envelope fields. The plaintext digest and
size and stable submission ID must also remain unchanged. Migration never
decrypts an archive payload and never validates plaintext outside the replay
sandbox.

The protected workflow is manual and dry by default. Apply requires the exact
audit commit, exact reviewed workflow commit, selected-plan digest, count
`439`, confirmation text
`stage-envelope-migration`, the historical RSA identity, the Encrypt-only
migration role, and audit-repository credentials. It stages a normal review
branch named `archive-file-key-rewrap-v1`; it does not update audit `main`.
Before dependency installation or credential exposure, apply proves that the
review branch is absent, fetches current audit `main`, and refuses any drift in
the 439 selected source/sidecar or target/sidecar paths. A failed run is
retryable only while the review branch is still absent; an existing branch or
an inconclusive remote lookup is a hard stop.
The reviewed template must first be applied, at a separate infrastructure
approval gate, to every stack that must wrap or replay these archives. It adds
only exact-context v2 Encrypt authority to the production migration role,
which has no v1 Encrypt path, and exact-context v2 Decrypt authority to the
relevant unwrap function role. It does not change either live v1 statement.
Applying this template is distinct from connecting the production live-intake
Wrap role and from approving release
controller trust. The repository workflow does not apply the stack or enable
production capability.

Before apply, prepare the paired retirement changes. After the reviewed audit
branch is promoted, remove the one-shot workflow, migration-only environment
variable and credentials, and the production migration Encrypt role and stack
output. Retain v2 Decrypt in the replay unwrap role and retain the schema-3
file-key replay implementation. The live archiver App credentials are shared
with ordinary archive workflows and are not migration-only retirement targets.

The legacy RSA stanza remains in each unchanged age header. After promotion,
the custodian must destroy the historical RSA identity. Exact ciphertext
preservation cannot revoke access held by an already-copied old private key.

Replay unwraps either envelope variant through the same single-use capability
boundary. Version 1 supplies a native age identity. Version 2 supplies exactly
16 bytes of `age-file-key-v1` material. Only the disposable replay sandbox can
turn either material into archive plaintext, and it validates the recorded
plaintext digest and size before extracting the archive.

The protected State contract permits `release.scheduled` only as a child of a
current `result.recorded` event. Historical archive replay authority and replay
verdicts cannot enter that release queue. The v2 envelope is therefore a replay
contract in the current scope; making historical Results releasable later would
require a separately reviewed State change and matching v2 support in the
release controller.
