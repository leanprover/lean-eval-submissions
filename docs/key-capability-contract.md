# Archive-key envelope and capability contract

This contract freezes the provider-neutral boundary selected by D6. It does
not enable decryption or provision AWS. The schemas and validator are:

- [`archive-key-envelope-v1.schema.json`](../schemas/archive-key-envelope-v1.schema.json)
- [`unwrap-capability-v1.schema.json`](../schemas/unwrap-capability-v1.schema.json)
- [`key_capability_contract.py`](../scripts/key_capability_contract.py)
- [`archive-key-contract-v1.json`](../tests/fixtures/archive-key-contract-v1.json)

## Stable envelope

Each new server archive receives a fresh age X25519 identity. The archive is a
normal age v1 ciphertext encrypted only to its corresponding recipient. The
small private identity is wrapped by the configured root-key adapter. The
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
identity is far below KMS's 4,096-byte symmetric plaintext limit:

- <https://docs.aws.amazon.com/kms/latest/APIReference/API_Encrypt.html>
- <https://docs.aws.amazon.com/kms/latest/developerguide/encrypt_context.html>

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

Before new production intake, a reviewed adapter must:

1. create and wrap one fresh age identity per archive;
2. persist the exact envelope alongside the ciphertext;
3. authenticate issuance from the protected replay/release controller;
4. bind unwrap to the intended fresh runner identity and nonce;
5. consume once before KMS decrypt and fail closed on an ambiguous response;
6. return only the one age identity, never the KMS key or another envelope;
7. record the non-secret request/capability digest and outcome;
8. pass the second-use, cross-archive, expiry, and provider-rewrap tests.

GitHub Actions may use OIDC rather than a long-lived AWS secret. The AWS trust
policy must match the exact repository and protected environment subject; an
unrestricted GitHub OIDC subject is forbidden. See GitHub's current AWS OIDC
guidance: <https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws>.
