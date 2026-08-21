from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib
import subprocess
import tempfile
import unittest

from scripts.key_capability_contract import (
    ContractError,
    authorize_once,
    archive_key_id,
    capability_digest,
    kms_encryption_context,
    validate_binding,
    validate_capability,
    validate_envelope,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]
VECTOR_PATH = ROOT / "tests" / "fixtures" / "archive-key-contract-v1.json"


class KeyCapabilityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vector = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
        self.envelope = self.vector["envelope"]
        self.capability = self.vector["capability"]

    def test_language_neutral_vector(self) -> None:
        self.assertEqual(validate_envelope(self.envelope), self.envelope)
        self.assertEqual(validate_capability(self.capability), self.capability)
        self.assertEqual(kms_encryption_context(self.envelope), self.vector["kms_encryption_context"])
        self.assertEqual(
            archive_key_id(self.envelope["submission_id"], self.envelope["age_recipient"]),
            self.envelope["data_key_id"],
        )
        self.assertEqual(capability_digest(self.capability), self.vector["capability_digest"])

    def test_authorize_once_consumes_before_provider_use(self) -> None:
        class Store:
            def __init__(self) -> None:
                self.used: set[str] = set()

            def consume(self, digest: str, expires_at: str) -> bool:
                self.asserted_expiry = expires_at
                if digest in self.used:
                    return False
                self.used.add(digest)
                return True

        store = Store()
        kwargs = {
            "expected_purpose": "lean-eval-replay",
            "expected_runner_nonce": self.capability["runner_nonce"],
            "now": dt.datetime(2026, 8, 21, 12, 4, tzinfo=dt.timezone.utc),
            "store": store,
        }
        _, _, digest = authorize_once(self.envelope, self.capability, **kwargs)
        self.assertEqual(digest, self.vector["capability_digest"])
        self.assertEqual(store.asserted_expiry, self.capability["expires_at"])
        with self.assertRaisesRegex(ContractError, "already been consumed"):
            authorize_once(self.envelope, self.capability, **kwargs)

    def test_binding_accepts_exact_unexpired_runner(self) -> None:
        envelope, capability = validate_binding(
            self.envelope,
            self.capability,
            expected_purpose="lean-eval-replay",
            expected_runner_nonce=self.capability["runner_nonce"],
            now=dt.datetime(2026, 8, 21, 12, 4, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(envelope, self.envelope)
        self.assertEqual(capability, self.capability)

    def test_binding_rejects_cross_archive_use(self) -> None:
        changed = copy.deepcopy(self.capability)
        changed["archive_ciphertext_sha256"] = "d" * 64
        with self.assertRaisesRegex(ContractError, "archive_ciphertext_sha256"):
            validate_binding(
                self.envelope,
                changed,
                expected_purpose="lean-eval-replay",
                expected_runner_nonce=changed["runner_nonce"],
                now=dt.datetime(2026, 8, 21, 12, 4, tzinfo=dt.timezone.utc),
            )

    def test_binding_rejects_wrong_runner_and_purpose(self) -> None:
        now = dt.datetime(2026, 8, 21, 12, 4, tzinfo=dt.timezone.utc)
        with self.assertRaisesRegex(ContractError, "different runner"):
            validate_binding(
                self.envelope,
                self.capability,
                expected_purpose="lean-eval-replay",
                expected_runner_nonce="d" * 64,
                now=now,
            )
        with self.assertRaisesRegex(ContractError, "purpose"):
            validate_binding(
                self.envelope,
                self.capability,
                expected_purpose="lean-eval-release",
                expected_runner_nonce=self.capability["runner_nonce"],
                now=now,
            )

    def test_binding_rejects_expired_capability(self) -> None:
        with self.assertRaisesRegex(ContractError, "expired"):
            validate_binding(
                self.envelope,
                self.capability,
                expected_purpose="lean-eval-replay",
                expected_runner_nonce=self.capability["runner_nonce"],
                now=dt.datetime(2026, 8, 21, 12, 5, 0, 1000, tzinfo=dt.timezone.utc),
            )
        with self.assertRaisesRegex(ContractError, "not yet valid"):
            validate_binding(
                self.envelope,
                self.capability,
                expected_purpose="lean-eval-replay",
                expected_runner_nonce=self.capability["runner_nonce"],
                now=dt.datetime(2026, 8, 21, 11, 59, 59, tzinfo=dt.timezone.utc),
            )

    def test_capability_lifetime_and_one_use_are_frozen(self) -> None:
        changed = copy.deepcopy(self.capability)
        changed["expires_at"] = "2026-08-21T12:10:00.001Z"
        with self.assertRaisesRegex(ContractError, "at most ten minutes"):
            validate_capability(changed)
        changed = copy.deepcopy(self.capability)
        changed["max_uses"] = 2
        with self.assertRaisesRegex(ContractError, "integer 1"):
            validate_capability(changed)

    def test_envelope_rejects_adapter_or_key_substitution(self) -> None:
        changed = copy.deepcopy(self.envelope)
        changed["adapter"] = "AWS"
        with self.assertRaisesRegex(ContractError, "adapter"):
            validate_envelope(changed)
        changed = copy.deepcopy(self.envelope)
        changed["data_key_id"] = "ak1_" + "0" * 64
        with self.assertRaisesRegex(ContractError, "does not match"):
            validate_envelope(changed)

    def test_exact_fields_reject_raw_key_material(self) -> None:
        for value, validator in (
            (self.envelope, validate_envelope),
            (self.capability, validate_capability),
        ):
            for forbidden in ("plaintext_identity", "kms_key_arn", "master_key", "private_key"):
                changed = copy.deepcopy(value)
                changed[forbidden] = "secret"
                with self.assertRaisesRegex(ContractError, "extra"):
                    validator(changed)

    def test_cli_outputs_only_nonsecret_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            envelope_path = root / "envelope.json"
            capability_path = root / "capability.json"
            envelope_path.write_text(json.dumps(self.envelope), encoding="utf-8")
            capability_path.write_text(json.dumps(self.capability), encoding="utf-8")
            proc = subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts" / "key_capability_contract.py"),
                    "validate-binding",
                    "--envelope",
                    str(envelope_path),
                    "--capability",
                    str(capability_path),
                    "--purpose",
                    "lean-eval-replay",
                    "--runner-nonce",
                    self.capability["runner_nonce"],
                    "--now",
                    "2026-08-21T12:04:00.000Z",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = json.loads(proc.stdout)
        self.assertEqual(
            set(output),
            {"valid", "request_id", "data_key_id", "capability_digest"},
        )


if __name__ == "__main__":
    unittest.main()
