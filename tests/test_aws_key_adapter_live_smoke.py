from __future__ import annotations

import base64
import datetime as dt
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT / "scripts"))

from aws_key_adapter_live_smoke import (  # noqa: E402
    LiveSmokeError,
    build_unwrap_request,
    prepare_source,
    validate_artifact,
    validate_reuse_failure,
    validate_unwrap_response,
    verify_decrypted,
)
from key_capability_contract import archive_key_id, capability_digest  # noqa: E402


IDENTITY = b"# created by age-keygen\nAGE-SECRET-KEY-1STAGINGSMOKEFIXTURE\n"
RECIPIENT = "age1" + "q" * 58
COMMIT = "1" * 40


class AwsKeyAdapterLiveSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.source = self.root / "source.tar.gz"
        self.artifact = self.root / "artifact"
        prepare_source(self.source, self.artifact / "expectation.json")
        archive = self.artifact / "archive"
        archive.mkdir()
        ciphertext = archive / "source.tar.gz.age"
        ciphertext.write_bytes(b"age-encryption.org/v1\nfixture-ciphertext")
        import hashlib

        envelope = {
            "schema_version": 1,
            "submission_id": "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
            "archive_ciphertext_sha256": hashlib.sha256(ciphertext.read_bytes()).hexdigest(),
            "data_key_id": archive_key_id(
                "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f", RECIPIENT
            ),
            "age_recipient": RECIPIENT,
            "adapter": "aws-kms-v1",
            "wrapped_identity": base64.b64encode(b"kms-ciphertext").decode("ascii"),
        }
        (archive / "archive-key-envelope.json").write_text(
            json.dumps(envelope), encoding="utf-8"
        )
        self.envelope = envelope

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_source_and_artifact_are_strict_and_source_free(self) -> None:
        validate_artifact(self.artifact)
        self.assertEqual(
            {path.name for path in self.artifact.iterdir()},
            {"archive", "expectation.json"},
        )
        rendered = b"".join(
            path.read_bytes() for path in (self.artifact / "archive").iterdir()
        )
        self.assertNotIn(IDENTITY, rendered)
        verify_decrypted(self.source, self.artifact / "expectation.json")

    def test_artifact_rejects_digest_drift_and_extra_files(self) -> None:
        ciphertext = self.artifact / "archive" / "source.tar.gz.age"
        original = ciphertext.read_bytes()
        ciphertext.write_bytes(original + b"drift")
        with self.assertRaisesRegex(LiveSmokeError, "digest"):
            validate_artifact(self.artifact)

        ciphertext.write_bytes(original)
        (self.artifact / "source.tar.gz").write_bytes(b"plaintext")
        with self.assertRaisesRegex(LiveSmokeError, "unexpected"):
            validate_artifact(self.artifact)

    def test_build_request_is_bound_and_short_lived(self) -> None:
        output = self.root / "request.json"
        now = dt.datetime(2026, 8, 21, 12, 0, tzinfo=dt.timezone.utc)
        request = build_unwrap_request(self.artifact, COMMIT, output, now=now)
        capability = request["capability"]
        self.assertEqual(capability["purpose"], "lean-eval-replay")
        self.assertEqual(capability["max_uses"], 1)
        self.assertEqual(capability["archive_commit"], COMMIT)
        self.assertEqual(capability["archive_ciphertext_sha256"], self.envelope["archive_ciphertext_sha256"])
        self.assertEqual(capability["expires_at"], "2026-08-21T12:05:00.000Z")
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def _request_and_response(self) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        request_path = self.root / "request.json"
        request = build_unwrap_request(
            self.artifact,
            COMMIT,
            request_path,
            now=dt.datetime.now(dt.timezone.utc),
        )
        metadata_path = self.root / "metadata.json"
        metadata_path.write_text(json.dumps({"StatusCode": 200, "ExecutedVersion": "1"}))
        response_path = self.root / "response.json"
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "adapter": "aws-kms-v1",
                    "request_id": request["capability"]["request_id"],
                    "data_key_id": request["envelope"]["data_key_id"],
                    "capability_digest": capability_digest(request["capability"]),
                    "plaintext_identity_base64": base64.b64encode(IDENTITY).decode("ascii"),
                }
            )
        )
        return request_path, metadata_path, response_path

    def test_valid_response_writes_only_private_identity(self) -> None:
        request, metadata, response = self._request_and_response()
        identity = self.root / "identity.age"
        summary = validate_unwrap_response(request, metadata, response, identity)
        self.assertEqual(identity.read_bytes(), IDENTITY)
        self.assertEqual(identity.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(summary), {"request_id", "capability_digest"})

    def test_response_rejects_wrong_binding_and_function_error(self) -> None:
        request, metadata, response = self._request_and_response()
        value = json.loads(response.read_text())
        value["data_key_id"] = "ak1_" + "0" * 64
        response.write_text(json.dumps(value))
        with self.assertRaisesRegex(LiveSmokeError, "data_key_id"):
            validate_unwrap_response(request, metadata, response, self.root / "identity.age")

        metadata.write_text(json.dumps({"StatusCode": 200, "FunctionError": "Unhandled"}))
        with self.assertRaisesRegex(LiveSmokeError, "successfully"):
            validate_unwrap_response(request, metadata, response, self.root / "identity-b.age")

    def test_reuse_must_fail_without_identity(self) -> None:
        metadata = self.root / "reuse-metadata.json"
        response = self.root / "reuse-response.json"
        metadata.write_text(json.dumps({"StatusCode": 200, "FunctionError": "Unhandled"}))
        response.write_text(
            json.dumps(
                {
                    "errorMessage": "capability has already been consumed",
                    "errorType": "AwsAdapterError",
                }
            )
        )
        validate_reuse_failure(metadata, response)

        changed = json.loads(response.read_text())
        changed["plaintext_identity_base64"] = base64.b64encode(IDENTITY).decode("ascii")
        response.write_text(json.dumps(changed))
        with self.assertRaisesRegex(LiveSmokeError, "exposed"):
            validate_reuse_failure(metadata, response)


if __name__ == "__main__":
    unittest.main()
