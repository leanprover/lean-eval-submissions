# Archive-key envelope and capability contract

This contract freezes the provider-neutral archive-key boundary. The dedicated
AWS account and environment-specific stacks now implement the initial adapter.
Production archive Wrap authority is connected and qualified; production replay
authority remains disconnected until its readiness packet is complete. The
schemas and validator are:

- [`archive-key-envelope-v1.schema.json`](../schemas/archive-key-envelope-v1.schema.json)
- [`archive-key-envelope-v2.schema.json`](../schemas/archive-key-envelope-v2.schema.json)
- [`unwrap-capability-v1.schema.json`](../schemas/unwrap-capability-v1.schema.json)
- [`key_capability_contract.py`](../scripts/key_capability_contract.py)
- [`archive_envelope.py`](../scripts/archive_envelope.py)
- [`archive-key-contract-v1.json`](../tests/fixtures/archive-key-contract-v1.json)

## Stable envelope

Each new server archive receives a fresh native age identity. The archive is a
normal age format-version-1 ciphertext encrypted only to its corresponding
recipient. The small private identity is wrapped by the configured root-key adapter. The
envelope records the submission ID, exact ciphertext digest, age recipient,
provider-neutral `ak1_…` key identity, adapter name, and opaque wrapped bytes.

`data_key_id` is domain-separated SHA-256 over the submission UUID and age
recipient. It therefore remains the same when the identical private identity
is rewrapped by a later provider. AWS account IDs, regions, ARNs, SDK types,
and grant IDs do not enter the envelope, archive path, State, result ID, replay
ID, or capability claims.

Every root-key adapter binds this exact non-secret context:

```text
contract = lean-eval-archive-key-v1
submission_id = <UUIDv7>
archive_ciphertext_sha256 = <64 lowercase hex>
data_key_id = <ak1_ plus 64 lowercase hex>
age_recipient_sha256 = SHA256(ASCII(age recipient))
```

The initial AWS adapter uses a symmetric KMS key and supplies the context to
both `Encrypt` and `Decrypt`. AWS documents that symmetric KMS encryption
context is authenticated data and must match exactly on decrypt. The age
identity must be checked against KMS's 4,096-byte symmetric plaintext limit.
The initial writer should explicitly request age's recommended post-quantum
hybrid identity (`age-keygen -pq`) rather than depend on age's future default:

- <https://docs.aws.amazon.com/kms/latest/APIReference/API_Encrypt.html>
- <https://docs.aws.amazon.com/kms/latest/developerguide/encrypt_context.html>
- <https://github.com/FiloSottile/age/blob/main/doc/age-keygen.1.ronn>

Historical archives whose ciphertext must remain byte-for-byte stable use the
small version-2 envelope variant. It wraps the existing 16-byte age file key,
labels it `age-file-key-v1`, and derives `ak2_…` from the submission UUID,
ciphertext digest, and material type. Its exact adapter context is:

```text
contract = lean-eval-archive-key-v2
submission_id = <UUIDv7>
archive_ciphertext_sha256 = <64 lowercase hex>
data_key_id = <ak2_ plus 64 lowercase hex>
key_material_type = age-file-key-v1
```

The v2 variant is only for compatible historical rewrap. New intake continues
to use the unchanged version-1 native-identity envelope.

The infrastructure template expresses v2 as separate, exact-context policy
statements: Encrypt only on the production migration role and Decrypt only on
the unwrap function role. Standing authorization covers deployment of those
reviewed statements through the exact historical execution packet. That packet
is separate from the already qualified production live-intake Wrap boundary
and the release-controller trust-repair procedure.

## Trusted archive writer

`archive_envelope.py` implements the provider-neutral preparation half of this
contract. It:

1. validates the submission UUID, adapter name, source, and fresh output path;
2. creates exactly one native age identity, requesting `age-keygen -pq` by
   default;
3. encrypts the source as a standard age format-version-1 ciphertext;
4. computes the digest and stable `ak1_…` identity;
5. sends the private identity only over the configured adapter's stdin; and
6. atomically publishes a new directory containing only
   `source.tar.gz.age` and `archive-key-envelope.json`.

The writer removes cloud and GitHub credentials from the `age` and
`age-keygen` environments. It deliberately does not remove credentials from
the adapter environment, because the adapter is the only process allowed to
use the provider. Adapter stderr is never repeated: even a faulty adapter
cannot make the writer print the private identity through diagnostics. The
writer rejects an adapter that directly returns the plaintext identity.

The adapter executable receives the argument `wrap` and one compact JSON
object on stdin with exact fields:

```json
{
  "adapter": "aws-kms-v1",
  "context": {
    "age_recipient_sha256": "<64 lowercase hex>",
    "archive_ciphertext_sha256": "<64 lowercase hex>",
    "contract": "lean-eval-archive-key-v1",
    "data_key_id": "ak1_<64 lowercase hex>",
    "submission_id": "<UUIDv7>"
  },
  "operation": "wrap",
  "plaintext_identity_base64": "<canonical base64>",
  "schema_version": 1
}
```

It must return exactly `schema_version`, the same `adapter`, and nonempty
canonical-base64 `wrapped_identity`. Provider diagnostics belong in the
adapter's own redacted audit channel, never in this response. There is no
unwrap operation in the writer.

Example (the output directory must not already exist):

```sh
python3 scripts/archive_envelope.py \
  --source-tar /tmp/fetch-out/source.tar.gz \
  --submission-id 0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f \
  --output-dir /tmp/archive-envelope \
  --adapter-executable /opt/lean-eval/aws-kms-adapter \
  --adapter-name aws-kms-v1
```

The reusable server archive workflow now invokes this tool before evaluation
and persists the ciphertext with a schema-version-3 sidecar. Staging has its
environment-specific Wrap role connected. The production
`archive-production` environment has its exact `AWS_WRAP_ROLE_ARN` installed
after the protected Wrap-only qualification. No production replay role is
connected, and the archive job cannot acquire Decrypt authority.

## Initial AWS adapter

`scripts/aws_key_adapter.py` is the initial provider implementation. Its local
command line exposes `wrap` only. That path calls KMS `Encrypt` with the exact
context above and is intended for a protected archive job whose OIDC role has
only `kms:Encrypt` on the one environment key.

Unwrap exists only as a synchronous Lambda handler. The protected replay or
release controller may assume a role with only `lambda:InvokeFunction` on the
published `live` alias. The function role alone has `dynamodb:PutItem` on the
one-use table and `kms:Decrypt` on the environment key. It validates the
envelope, purpose, runner nonce, binding, and ten-minute lifetime; conditionally
inserts the `uc1_…` digest with `attribute_not_exists`; and calls KMS only after
that insert succeeds. A repeated request never reaches KMS. A KMS or response
failure after consumption is fail-closed; the controller must investigate and
issue a fresh capability rather than replay an ambiguous one.

There is no Function URL or API Gateway. AWS IAM authenticates direct Lambda
invocation, and the untrusted VM receives neither the invoker role nor any KMS
or DynamoDB permission. The Lambda does not log request or response bodies.
DynamoDB TTL removes expired consume records eventually, but TTL is cleanup,
not authorization: the immutable capability timestamp is always validated
before the conditional write.

The linted SAM template at
`infrastructure/aws-key-adapter/template.yaml` creates separate staging or
production KMS keys, tables, functions, and roles from the same code. It binds
GitHub OIDC to exact protected environment subjects: archive/replay in
`lean-eval-submissions`, and release in `lean-eval-releases`. It grants no
wildcard KMS, DynamoDB, or Lambda workload authority.

The current reusable workflow puts the Encrypt-only OIDC role in a trusted
archive job, never in the job that runs untrusted Lean. The archive job
independently fetches the exact source commit, encrypts and persists it, and
finishes before evaluation. The evaluation job then fetches the same immutable
commit again with only its short-lived read token. Plaintext source does not
cross a job or artifact boundary, and `id-token: write` is absent from the
evaluation job.

## Single-use capability

The capability claims bind one purpose (`lean-eval-replay` or
`lean-eval-release`), one request, submission, immutable audit object and
digest, data-key identity, and disposable-runner nonce. The lifetime is
positive and no longer than ten minutes; `max_uses` is the integer `1`.

These claims are not themselves a bearer token. The provider adapter must
authenticate them, atomically change the capability record from unused to used
before decrypting, and refuse every repeat even if its first response was
lost. Only then may it call its root-key API. The validator deliberately has
no `unwrap` command so a caller cannot mistake shape validation for one-use
enforcement. `authorize_once` computes the domain-separated `uc1_…` digest and
calls the adapter's atomic `OneUseStore` before returning the validated KMS
context; a failed/repeated consume never reaches the provider unwrap.

AWS KMS grants can restrict `Decrypt` to an exact encryption context, but they
do not expire automatically. They are therefore defense in depth, not the
one-use store. AWS explicitly requires grants to be retired or revoked and
recommends `EncryptionContextEquals` where possible:

- <https://docs.aws.amazon.com/kms/latest/developerguide/grant-best-practices.html>
- <https://docs.aws.amazon.com/kms/latest/developerguide/grants.html>

## Launch boundary

Before new production intake, the retained launch checks must demonstrate:

1. the protected archive job can wrap one fresh identity with Encrypt-only
   authority;
2. the ciphertext and exact envelope are persisted together;
3. only the protected replay/release subjects can invoke unwrap;
4. unwrap is bound to the intended fresh runner identity and nonce;
5. a capability is consumed before KMS decrypt and ambiguous responses fail
   closed;
6. the controller receives only the one age identity, never the KMS key;
7. the untrusted runner has no AWS credential or provider API access; and
8. the bounded live staging binding and reuse-denial checks pass; and
9. the exact production archive subject can encrypt a synthetic key but cannot
   decrypt it, without creating a production submission or archive.

GitHub Actions may use OIDC rather than a long-lived AWS secret. The AWS trust
policy must match the exact repository and protected environment subject; an
unrestricted GitHub OIDC subject is forbidden. See GitHub's current AWS OIDC
guidance: <https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws>.
