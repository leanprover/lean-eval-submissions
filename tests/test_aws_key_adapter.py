from __future__ import annotations

import base64
import copy
import datetime as dt
import json
import pathlib
import subprocess
import unittest

from scripts.aws_key_adapter import (
    AwsAdapterError,
    AwsRootKeyAdapter,
    handle_unwrap_event,
    validate_wrap_request,
)
from scripts.key_capability_contract import archive_file_key_id, capability_digest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VECTOR = json.loads(
    (ROOT / "tests" / "fixtures" / "archive-key-contract-v1.json").read_text(
        encoding="utf-8"
    )
)
IDENTITY = b"# public key: fixture\nAGE-SECRET-KEY-PQ-1FIXTUREFIXTURE\n"
NOW = dt.datetime(2026, 8, 21, 12, 4, tzinfo=dt.timezone.utc)


class ConditionalFailure(Exception):
    def __init__(self) -> None:
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDynamo:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls: list[dict[str, object]] = []
        self.seen: set[str] = set()

    def put_item(self, **kwargs: object) -> None:
        self.order.append("consume")
        self.calls.append(kwargs)
        item = kwargs["Item"]
        assert isinstance(item, dict)
        digest = item["capability_digest"]
        assert isinstance(digest, dict)
        value = digest["S"]
        assert isinstance(value, str)
        if value in self.seen:
            raise ConditionalFailure()
        self.seen.add(value)


class FakeKms:
    def __init__(self, order: list[str], *, plaintext: object = IDENTITY) -> None:
        self.order = order
        self.plaintext = plaintext
        self.encrypt_calls: list[dict[str, object]] = []
        self.decrypt_calls: list[dict[str, object]] = []

    def encrypt(self, **kwargs: object) -> dict[str, bytes]:
        self.order.append("encrypt")
        self.encrypt_calls.append(kwargs)
        return {"CiphertextBlob": b"kms-wrapped-fixture"}

    def decrypt(self, **kwargs: object) -> dict[str, object]:
        self.order.append("decrypt")
        self.decrypt_calls.append(kwargs)
        return {"Plaintext": self.plaintext}


class FailingKms(FakeKms):
    def decrypt(self, **kwargs: object) -> dict[str, object]:
        self.order.append("decrypt")
        self.decrypt_calls.append(kwargs)
        raise RuntimeError("provider unavailable")


def unwrap_request() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "unwrap",
        "adapter": "aws-kms-v1",
        "envelope": copy.deepcopy(VECTOR["envelope"]),
        "capability": copy.deepcopy(VECTOR["capability"]),
        "expected_purpose": "lean-eval-replay",
        "expected_runner_nonce": VECTOR["capability"]["runner_nonce"],
    }


def wrap_request() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "wrap",
        "adapter": "aws-kms-v1",
        "context": copy.deepcopy(VECTOR["kms_encryption_context"]),
        "plaintext_identity_base64": base64.b64encode(IDENTITY).decode("ascii"),
    }


def file_key_envelope() -> dict[str, object]:
    submission_id = VECTOR["envelope"]["submission_id"]
    archive_digest = VECTOR["envelope"]["archive_ciphertext_sha256"]
    return {
        "schema_version": 2,
        "submission_id": submission_id,
        "archive_ciphertext_sha256": archive_digest,
        "data_key_id": archive_file_key_id(submission_id, archive_digest),
        "key_material_type": "age-file-key-v1",
        "adapter": "aws-kms-v1",
        "wrapped_key_material": base64.b64encode(b"kms-wrapped-fixture").decode(),
    }


def file_key_wrap_request() -> dict[str, object]:
    envelope = file_key_envelope()
    return {
        "schema_version": 2,
        "operation": "wrap",
        "adapter": "aws-kms-v1",
        "context": {
            "contract": "lean-eval-archive-key-v2",
            "submission_id": envelope["submission_id"],
            "archive_ciphertext_sha256": envelope["archive_ciphertext_sha256"],
            "data_key_id": envelope["data_key_id"],
            "key_material_type": "age-file-key-v1",
        },
        "key_material_type": "age-file-key-v1",
        "plaintext_key_material_base64": base64.b64encode(b"k" * 16).decode(),
    }


class AwsKeyAdapterTests(unittest.TestCase):
    def adapter(
        self,
        *,
        kms: FakeKms | None = None,
    ) -> tuple[AwsRootKeyAdapter, FakeKms, FakeDynamo, list[str]]:
        order: list[str] = []
        dynamo = FakeDynamo(order)
        selected_kms = kms or FakeKms(order)
        # A supplied fake may have been created with a different list.
        selected_kms.order = order
        adapter = AwsRootKeyAdapter(
            kms_client=selected_kms,
            dynamodb_client=dynamo,
            kms_key_id="arn:aws:kms:us-east-2:111122223333:key/test",
            table_name="lean-eval-capability-consumption",
            adapter_name="aws-kms-v1",
        )
        return adapter, selected_kms, dynamo, order

    def test_wrap_uses_exact_kms_context_and_returns_only_opaque_bytes(self) -> None:
        adapter, kms, dynamo, order = self.adapter()
        response = adapter.wrap(wrap_request())
        self.assertEqual(order, ["encrypt"])
        self.assertEqual(dynamo.calls, [])
        self.assertEqual(
            kms.encrypt_calls,
            [{
                "KeyId": "arn:aws:kms:us-east-2:111122223333:key/test",
                "Plaintext": IDENTITY,
                "EncryptionContext": VECTOR["kms_encryption_context"],
                "EncryptionAlgorithm": "SYMMETRIC_DEFAULT",
            }],
        )
        self.assertEqual(set(response), {"schema_version", "adapter", "wrapped_identity"})
        self.assertEqual(base64.b64decode(response["wrapped_identity"]), b"kms-wrapped-fixture")
        self.assertNotIn("arn:aws", json.dumps(response))

    def test_unwrap_consumes_once_before_kms_decrypt(self) -> None:
        adapter, kms, dynamo, order = self.adapter()
        response = adapter.unwrap(unwrap_request(), now=NOW)
        self.assertEqual(order, ["consume", "decrypt"])
        self.assertEqual(
            set(response),
            {
                "schema_version",
                "adapter",
                "request_id",
                "data_key_id",
                "capability_digest",
                "plaintext_identity_base64",
            },
        )
        self.assertEqual(
            base64.b64decode(response["plaintext_identity_base64"]), IDENTITY
        )
        self.assertEqual(response["capability_digest"], VECTOR["capability_digest"])
        self.assertEqual(
            dynamo.calls[0],
            {
                "TableName": "lean-eval-capability-consumption",
                "Item": {
                    "capability_digest": {"S": VECTOR["capability_digest"]},
                    "expires_at_epoch": {"N": "1787313900"},
                },
                "ConditionExpression": "attribute_not_exists(#digest)",
                "ExpressionAttributeNames": {"#digest": "capability_digest"},
            },
        )
        self.assertEqual(
            kms.decrypt_calls[0],
            {
                "KeyId": "arn:aws:kms:us-east-2:111122223333:key/test",
                "CiphertextBlob": base64.b64decode(
                    VECTOR["envelope"]["wrapped_identity"]
                ),
                "EncryptionContext": VECTOR["kms_encryption_context"],
                "EncryptionAlgorithm": "SYMMETRIC_DEFAULT",
            },
        )

        with self.assertRaisesRegex(AwsAdapterError, "already been consumed"):
            adapter.unwrap(unwrap_request(), now=NOW)
        self.assertEqual(order, ["consume", "decrypt", "consume"])
        self.assertEqual(len(kms.decrypt_calls), 1)

    def test_file_key_wrap_and_unwrap_use_the_versioned_exact_contract(self) -> None:
        adapter, kms, _, order = self.adapter(kms=FakeKms([], plaintext=b"k" * 16))
        wrapped = adapter.wrap(file_key_wrap_request())
        self.assertEqual(
            wrapped,
            {
                "schema_version": 2,
                "adapter": "aws-kms-v1",
                "wrapped_key_material": base64.b64encode(
                    b"kms-wrapped-fixture"
                ).decode(),
            },
        )
        request = unwrap_request()
        request["envelope"] = file_key_envelope()
        request["capability"]["data_key_id"] = request["envelope"]["data_key_id"]
        response = adapter.unwrap(request, now=NOW)
        self.assertEqual(order, ["encrypt", "consume", "decrypt"])
        self.assertEqual(response["schema_version"], 2)
        self.assertEqual(response["key_material_type"], "age-file-key-v1")
        self.assertEqual(
            base64.b64decode(response["plaintext_key_material_base64"]), b"k" * 16
        )
        self.assertEqual(
            response["capability_digest"], capability_digest(request["capability"])
        )
        self.assertEqual(
            kms.decrypt_calls[0]["EncryptionContext"],
            file_key_wrap_request()["context"],
        )

    def test_file_key_wrap_rejects_any_non_16_byte_material(self) -> None:
        for material in (b"", b"short", b"k" * 17):
            request = file_key_wrap_request()
            request["plaintext_key_material_base64"] = base64.b64encode(
                material
            ).decode()
            with self.assertRaisesRegex(
                AwsAdapterError, "16 bytes|empty|invalid format"
            ):
                validate_wrap_request(request, expected_adapter="aws-kms-v1")

    def test_invalid_provider_file_key_is_consumed_and_not_returned(self) -> None:
        adapter, _, _, order = self.adapter(kms=FakeKms([], plaintext=b"short"))
        request = unwrap_request()
        request["envelope"] = file_key_envelope()
        request["capability"]["data_key_id"] = request["envelope"]["data_key_id"]
        with self.assertRaisesRegex(AwsAdapterError, "invalid age file key"):
            adapter.unwrap(request, now=NOW)
        self.assertEqual(order, ["consume", "decrypt"])
        with self.assertRaisesRegex(AwsAdapterError, "already been consumed"):
            adapter.unwrap(request, now=NOW)
        self.assertEqual(order, ["consume", "decrypt", "consume"])

    def test_provider_failure_remains_fail_closed(self) -> None:
        failing = FailingKms([])
        adapter, _, _, order = self.adapter(kms=failing)
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            adapter.unwrap(unwrap_request(), now=NOW)
        self.assertEqual(order, ["consume", "decrypt"])
        with self.assertRaisesRegex(AwsAdapterError, "already been consumed"):
            adapter.unwrap(unwrap_request(), now=NOW)
        self.assertEqual(order, ["consume", "decrypt", "consume"])

    def test_rejects_expiry_or_binding_before_consumption(self) -> None:
        adapter, kms, dynamo, order = self.adapter()
        expired = dt.datetime(2026, 8, 21, 12, 6, tzinfo=dt.timezone.utc)
        with self.assertRaisesRegex(AwsAdapterError, "expired"):
            adapter.unwrap(unwrap_request(), now=expired)
        changed = unwrap_request()
        changed["expected_runner_nonce"] = "d" * 64
        with self.assertRaisesRegex(AwsAdapterError, "different runner"):
            adapter.unwrap(changed, now=NOW)
        self.assertEqual(order, [])
        self.assertEqual(dynamo.calls, [])
        self.assertEqual(kms.decrypt_calls, [])

    def test_invalid_provider_plaintext_is_consumed_and_not_returned(self) -> None:
        adapter, _, _, order = self.adapter(
            kms=FakeKms([], plaintext=b"not an age key")
        )
        with self.assertRaisesRegex(AwsAdapterError, "invalid age identity"):
            adapter.unwrap(unwrap_request(), now=NOW)
        self.assertEqual(order, ["consume", "decrypt"])

    def test_wrap_request_rejects_unknown_fields_and_plaintext_passthrough(
        self,
    ) -> None:
        changed = wrap_request()
        changed["kms_key_arn"] = "forbidden"
        with self.assertRaisesRegex(AwsAdapterError, "extra"):
            validate_wrap_request(changed, expected_adapter="aws-kms-v1")
        changed = wrap_request()
        changed["plaintext_identity_base64"] = "Zh=="
        with self.assertRaisesRegex(AwsAdapterError, "canonical base64"):
            validate_wrap_request(changed, expected_adapter="aws-kms-v1")

    def test_direct_handler_uses_injected_provider_boundary(self) -> None:
        order: list[str] = []
        response = handle_unwrap_event(
            unwrap_request(),
            kms_client=FakeKms(order),
            dynamodb_client=FakeDynamo(order),
            kms_key_id="key-id",
            table_name="one-use",
            adapter_name="aws-kms-v1",
            now=NOW,
        )
        self.assertEqual(order, ["consume", "decrypt"])
        self.assertEqual(response["request_id"], VECTOR["capability"]["request_id"])

    def test_command_line_has_no_unwrap_operation(self) -> None:
        process = subprocess.run(
            ["python3", str(ROOT / "scripts" / "aws_key_adapter.py"), "unwrap"],
            input="{}",
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 2)
        self.assertNotIn("Plaintext", process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()
