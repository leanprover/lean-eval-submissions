#!/usr/bin/env python3
"""Minimal AWS implementation of the provider-neutral root-key boundary.

The command-line surface supports wrapping only. Unwrap is exposed solely as a
direct Lambda handler whose caller must already have narrowly scoped
``lambda:InvokeFunction`` authority; the disposable runner never receives AWS
credentials or direct KMS access.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
from typing import Any

try:
    from .key_capability_contract import (
        ADAPTER,
        AGE_FILE_KEY_MATERIAL_TYPE,
        ARCHIVE_KEY_ID,
        BASE64,
        DIGEST,
        UUID7,
        ContractError,
        authorize_once,
        file_key_envelope_binding_context,
        kms_encryption_context,
        validate_age_identity_bytes,
        validate_binding,
    )
except ImportError:
    from key_capability_contract import (  # type: ignore[no-redef]
        ADAPTER,
        AGE_FILE_KEY_MATERIAL_TYPE,
        ARCHIVE_KEY_ID,
        BASE64,
        DIGEST,
        UUID7,
        ContractError,
        authorize_once,
        file_key_envelope_binding_context,
        kms_encryption_context,
        validate_age_identity_bytes,
        validate_binding,
    )


MAX_REQUEST_BYTES = 32_768
MAX_KMS_BLOB_BYTES = 16_384
WRAP_V1_FIELDS = {
    "schema_version",
    "operation",
    "adapter",
    "context",
    "plaintext_identity_base64",
}
WRAP_V2_FIELDS = {
    "schema_version",
    "operation",
    "adapter",
    "context",
    "key_material_type",
    "plaintext_key_material_base64",
}
CONTEXT_V1_FIELDS = {
    "contract",
    "submission_id",
    "archive_ciphertext_sha256",
    "data_key_id",
    "age_recipient_sha256",
}
CONTEXT_V2_FIELDS = {
    "contract",
    "submission_id",
    "archive_ciphertext_sha256",
    "data_key_id",
    "key_material_type",
}
UNWRAP_FIELDS = {
    "schema_version",
    "operation",
    "adapter",
    "envelope",
    "capability",
    "expected_purpose",
    "expected_runner_nonce",
}


class AwsAdapterError(ValueError):
    """The AWS adapter refused malformed input or an unsafe provider result."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AwsAdapterError(f"{label} must be an object with string keys")
    return value


def _fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise AwsAdapterError(
            f"{label} fields are not canonical; "
            f"missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise AwsAdapterError(f"{label} has invalid format")
    return value


def _canonical_base64(value: Any, label: str, maximum_bytes: int) -> bytes:
    raw = _match(BASE64, value, label)
    if len(raw) % 4 != 0:
        raise AwsAdapterError(f"{label} is not canonical base64")
    try:
        decoded = base64.b64decode(raw, validate=True)
    except ValueError as error:
        raise AwsAdapterError(f"{label} is not valid base64") from error
    if not decoded or len(decoded) > maximum_bytes:
        raise AwsAdapterError(f"{label} is empty or exceeds its size limit")
    if base64.b64encode(decoded).decode("ascii") != raw:
        raise AwsAdapterError(f"{label} is not canonical base64")
    return decoded


def validate_wrap_request(
    value: Any, *, expected_adapter: str
) -> tuple[dict[str, Any], bytes]:
    request = _object(value, "wrap request")
    schema_version = request.get("schema_version")
    if type(schema_version) is not int or schema_version not in {1, 2}:
        raise AwsAdapterError("wrap request schema_version must be integer 1 or 2")
    _fields(
        request,
        WRAP_V1_FIELDS if schema_version == 1 else WRAP_V2_FIELDS,
        "wrap request",
    )
    if request["operation"] != "wrap":
        raise AwsAdapterError("wrap request operation must be wrap")
    _match(ADAPTER, expected_adapter, "expected adapter")
    if request["adapter"] != expected_adapter:
        raise AwsAdapterError("wrap request names a different adapter")
    context = _object(request["context"], "wrap request context")
    _fields(
        context,
        CONTEXT_V1_FIELDS if schema_version == 1 else CONTEXT_V2_FIELDS,
        "wrap request context",
    )
    expected_contract = f"lean-eval-archive-key-v{schema_version}"
    if context["contract"] != expected_contract:
        raise AwsAdapterError("wrap request context has the wrong contract")
    _match(UUID7, context["submission_id"], "context.submission_id")
    _match(DIGEST, context["archive_ciphertext_sha256"], "context archive digest")
    _match(ARCHIVE_KEY_ID, context["data_key_id"], "context data_key_id")
    if schema_version == 1:
        _match(DIGEST, context["age_recipient_sha256"], "context recipient digest")
        identity = _canonical_base64(
            request["plaintext_identity_base64"],
            "plaintext_identity_base64",
            4096,
        )
        try:
            validate_age_identity_bytes(identity)
        except ContractError as error:
            raise AwsAdapterError(str(error)) from error
        return request, identity
    if (
        request["key_material_type"] != AGE_FILE_KEY_MATERIAL_TYPE
        or context["key_material_type"] != AGE_FILE_KEY_MATERIAL_TYPE
    ):
        raise AwsAdapterError("wrap request key material type is not registered")
    try:
        expected_context = file_key_envelope_binding_context(
            context["submission_id"],
            context["archive_ciphertext_sha256"],
            context["data_key_id"],
        )
    except ContractError as error:
        raise AwsAdapterError(str(error)) from error
    if context != expected_context:
        raise AwsAdapterError("wrap request file-key context is not canonical")
    key_material = _canonical_base64(
        request["plaintext_key_material_base64"],
        "plaintext_key_material_base64",
        16,
    )
    if len(key_material) != 16:
        raise AwsAdapterError("age file key must contain exactly 16 bytes")
    return request, key_material


def validate_unwrap_request(
    value: Any,
    *,
    expected_adapter: str,
    now: dt.datetime,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    request = _object(value, "unwrap request")
    _fields(request, UNWRAP_FIELDS, "unwrap request")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise AwsAdapterError("unwrap request schema_version must be integer 1")
    if request["operation"] != "unwrap":
        raise AwsAdapterError("unwrap request operation must be unwrap")
    _match(ADAPTER, expected_adapter, "expected adapter")
    if request["adapter"] != expected_adapter:
        raise AwsAdapterError("unwrap request names a different adapter")
    if not isinstance(request["expected_purpose"], str):
        raise AwsAdapterError("expected_purpose must be a string")
    _match(DIGEST, request["expected_runner_nonce"], "expected_runner_nonce")
    try:
        envelope, capability = validate_binding(
            request["envelope"],
            request["capability"],
            expected_purpose=request["expected_purpose"],
            expected_runner_nonce=request["expected_runner_nonce"],
            now=now,
        )
    except ContractError as error:
        raise AwsAdapterError(str(error)) from error
    if envelope["adapter"] != expected_adapter:
        raise AwsAdapterError("envelope names a different adapter")
    return request, envelope, capability


class DynamoOneUseStore:
    """Atomic capability consumption using one conditional DynamoDB PutItem."""

    def __init__(self, client: Any, table_name: str) -> None:
        if not isinstance(table_name, str) or not table_name:
            raise AwsAdapterError("DynamoDB table name is required")
        self.client = client
        self.table_name = table_name

    def consume(self, digest: str, expires_at: str) -> bool:
        expires = dt.datetime.fromisoformat(expires_at[:-1] + "+00:00")
        item = {
            "capability_digest": {"S": digest},
            "expires_at_epoch": {"N": str(int(expires.timestamp()))},
        }
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(#digest)",
                ExpressionAttributeNames={"#digest": "capability_digest"},
            )
        except Exception as error:
            response = getattr(error, "response", {})
            code = (
                response.get("Error", {}).get("Code")
                if isinstance(response, dict)
                else None
            )
            if code == "ConditionalCheckFailedException":
                return False
            raise
        return True


class AwsRootKeyAdapter:
    """KMS encryption plus consume-before-decrypt Lambda implementation."""

    def __init__(
        self,
        *,
        kms_client: Any,
        dynamodb_client: Any,
        kms_key_id: str,
        table_name: str,
        adapter_name: str,
    ) -> None:
        if not isinstance(kms_key_id, str) or not kms_key_id:
            raise AwsAdapterError("KMS key ID is required")
        _match(ADAPTER, adapter_name, "adapter name")
        self.kms = kms_client
        self.store = DynamoOneUseStore(dynamodb_client, table_name)
        self.kms_key_id = kms_key_id
        self.adapter_name = adapter_name

    def wrap(self, value: Any) -> dict[str, Any]:
        request, key_material = validate_wrap_request(
            value, expected_adapter=self.adapter_name
        )
        response = self.kms.encrypt(
            KeyId=self.kms_key_id,
            Plaintext=key_material,
            EncryptionContext=request["context"],
            EncryptionAlgorithm="SYMMETRIC_DEFAULT",
        )
        blob = response.get("CiphertextBlob") if isinstance(response, dict) else None
        if not isinstance(blob, bytes) or not blob or len(blob) > MAX_KMS_BLOB_BYTES:
            raise AwsAdapterError("KMS Encrypt returned an invalid ciphertext blob")
        if request["schema_version"] == 1:
            return {
                "schema_version": 1,
                "adapter": self.adapter_name,
                "wrapped_identity": base64.b64encode(blob).decode("ascii"),
            }
        return {
            "schema_version": 2,
            "adapter": self.adapter_name,
            "wrapped_key_material": base64.b64encode(blob).decode("ascii"),
        }

    def unwrap(self, value: Any, *, now: dt.datetime) -> dict[str, Any]:
        request, envelope, capability = validate_unwrap_request(
            value,
            expected_adapter=self.adapter_name,
            now=now,
        )
        try:
            _, _, digest = authorize_once(
                envelope,
                capability,
                expected_purpose=request["expected_purpose"],
                expected_runner_nonce=request["expected_runner_nonce"],
                now=now,
                store=self.store,
            )
        except ContractError as error:
            raise AwsAdapterError(str(error)) from error
        wrapped_field = (
            "wrapped_identity"
            if envelope["schema_version"] == 1
            else "wrapped_key_material"
        )
        ciphertext = _canonical_base64(
            envelope[wrapped_field],
            f"envelope.{wrapped_field}",
            MAX_KMS_BLOB_BYTES,
        )
        response = self.kms.decrypt(
            KeyId=self.kms_key_id,
            CiphertextBlob=ciphertext,
            EncryptionContext=kms_encryption_context(envelope),
            EncryptionAlgorithm="SYMMETRIC_DEFAULT",
        )
        plaintext = response.get("Plaintext") if isinstance(response, dict) else None
        common = {
            "adapter": self.adapter_name,
            "request_id": capability["request_id"],
            "data_key_id": envelope["data_key_id"],
            "capability_digest": digest,
        }
        if envelope["schema_version"] == 1:
            try:
                validated_identity = validate_age_identity_bytes(plaintext)
            except ContractError as error:
                # Consumption already succeeded. Fail closed; the controller may
                # issue a fresh capability after investigating the provider error.
                raise AwsAdapterError(
                    "KMS Decrypt returned an invalid age identity"
                ) from error
            return {
                "schema_version": 1,
                **common,
                "plaintext_identity_base64": base64.b64encode(
                    validated_identity
                ).decode("ascii"),
            }
        if not isinstance(plaintext, bytes) or len(plaintext) != 16:
            raise AwsAdapterError("KMS Decrypt returned an invalid age file key")
        return {
            "schema_version": 2,
            **common,
            "key_material_type": AGE_FILE_KEY_MATERIAL_TYPE,
            "plaintext_key_material_base64": base64.b64encode(plaintext).decode(
                "ascii"
            ),
        }


def handle_unwrap_event(
    event: Any,
    *,
    kms_client: Any,
    dynamodb_client: Any,
    kms_key_id: str,
    table_name: str,
    adapter_name: str,
    now: dt.datetime,
) -> dict[str, Any]:
    adapter = AwsRootKeyAdapter(
        kms_client=kms_client,
        dynamodb_client=dynamodb_client,
        kms_key_id=kms_key_id,
        table_name=table_name,
        adapter_name=adapter_name,
    )
    return adapter.unwrap(event, now=now)


def lambda_handler(event: Any, _context: Any) -> dict[str, Any]:
    """Direct synchronous Lambda entrypoint; never logs event or response."""
    import boto3  # type: ignore[import-not-found]

    return handle_unwrap_event(
        event,
        kms_client=boto3.client("kms"),
        dynamodb_client=boto3.client("dynamodb"),
        kms_key_id=os.environ["LEAN_EVAL_KMS_KEY_ID"],
        table_name=os.environ["LEAN_EVAL_CAPABILITY_TABLE"],
        adapter_name=os.environ.get("LEAN_EVAL_ADAPTER_NAME", "aws-kms-v1"),
        now=dt.datetime.now(dt.timezone.utc),
    )


def _read_request() -> Any:
    payload = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(payload) > MAX_REQUEST_BYTES:
        raise AwsAdapterError("request exceeds the size limit")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AwsAdapterError("request is not one UTF-8 JSON object") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["wrap"])
    args = parser.parse_args(argv)
    try:
        if args.operation != "wrap":
            raise AwsAdapterError("only wrap is available from the command line")
        import boto3  # type: ignore[import-not-found]

        adapter = AwsRootKeyAdapter(
            kms_client=boto3.client("kms"),
            # The wrapping role has no DynamoDB authority; the object is not
            # used by wrap and exists only to keep one adapter implementation.
            dynamodb_client=None,
            kms_key_id=os.environ["LEAN_EVAL_KMS_KEY_ID"],
            table_name="not-used-by-wrap",
            adapter_name=os.environ.get("LEAN_EVAL_ADAPTER_NAME", "aws-kms-v1"),
        )
        print(
            json.dumps(
                adapter.wrap(_read_request()), separators=(",", ":"), sort_keys=True
            )
        )
    except (AwsAdapterError, ContractError, KeyError, OSError) as error:
        # Never echo the request: it contains the private age identity.
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
