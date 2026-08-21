#!/usr/bin/env python3
"""Create one age archive and provider-neutral wrapped-identity envelope.

This trusted preparation tool never unwraps. It sends the fresh age identity
only to the configured adapter process over stdin, ignores adapter stderr, and
writes only ciphertext plus the strict public envelope.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
from typing import Any

try:
    from .key_capability_contract import (
        ADAPTER,
        AGE_RECIPIENT,
        BASE64,
        ContractError,
        archive_key_id,
        canonical_archive_path,
        envelope_binding_context,
        validate_age_identity_bytes,
        validate_envelope,
    )
except ImportError:
    from key_capability_contract import (  # type: ignore[no-redef]
        ADAPTER,
        AGE_RECIPIENT,
        BASE64,
        ContractError,
        archive_key_id,
        canonical_archive_path,
        envelope_binding_context,
        validate_age_identity_bytes,
        validate_envelope,
    )


MAX_ADAPTER_STDOUT_BYTES = 32768
ADAPTER_RESPONSE_FIELDS = {"schema_version", "adapter", "wrapped_identity"}


class EnvelopeError(ValueError):
    """Envelope preparation failed before any safe output was committed."""


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_environment() -> dict[str, str]:
    """Keep cloud/provider credentials out of age and age-keygen."""
    environment = {"PATH": os.environ.get("PATH", "")}
    for name in ("LANG", "LC_ALL", "TZ"):
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def _run_age(command: list[str], *, label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            env=_tool_environment(),
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        raise EnvelopeError(f"{label} could not run") from error
    if result.returncode != 0:
        raise EnvelopeError(f"{label} failed with exit code {result.returncode}")
    return result


def _read_identity(identity_path: pathlib.Path) -> bytes:
    try:
        mode = stat.S_IMODE(identity_path.stat().st_mode)
        identity = identity_path.read_bytes()
    except OSError as error:
        raise EnvelopeError("age-keygen did not create a readable identity") from error
    if mode & 0o077:
        raise EnvelopeError("age identity permissions expose group or other access")
    try:
        return validate_age_identity_bytes(identity)
    except ContractError as error:
        raise EnvelopeError(str(error)) from error


def _adapter_wrap(
    *,
    adapter_executable: pathlib.Path,
    adapter_name: str,
    identity: bytes,
    context: dict[str, str],
) -> str:
    if ADAPTER.fullmatch(adapter_name) is None:
        raise EnvelopeError("adapter name is not canonical")
    request = {
        "schema_version": 1,
        "operation": "wrap",
        "adapter": adapter_name,
        "context": context,
        "plaintext_identity_base64": base64.b64encode(identity).decode("ascii"),
    }
    try:
        result = subprocess.run(
            [str(adapter_executable), "wrap"],
            input=json.dumps(request, separators=(",", ":"), sort_keys=True),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        raise EnvelopeError("root-key adapter could not run") from error
    if result.returncode != 0:
        # Adapter stderr is intentionally not repeated: a faulty adapter might
        # include the plaintext identity in its diagnostics.
        raise EnvelopeError(f"root-key adapter failed with exit code {result.returncode}")
    if len(result.stdout.encode("utf-8")) > MAX_ADAPTER_STDOUT_BYTES:
        raise EnvelopeError("root-key adapter response exceeds the size limit")
    try:
        response: Any = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise EnvelopeError("root-key adapter did not return one JSON object") from error
    if not isinstance(response, dict) or set(response) != ADAPTER_RESPONSE_FIELDS:
        raise EnvelopeError("root-key adapter response fields are not canonical")
    if response["schema_version"] != 1 or isinstance(response["schema_version"], bool):
        raise EnvelopeError("root-key adapter response schema_version must be integer 1")
    if response["adapter"] != adapter_name:
        raise EnvelopeError("root-key adapter response names a different adapter")
    wrapped = response["wrapped_identity"]
    if not isinstance(wrapped, str) or BASE64.fullmatch(wrapped) is None:
        raise EnvelopeError("root-key adapter response is not canonical base64")
    return wrapped


def create_archive_envelope(
    *,
    source_tar: pathlib.Path,
    submission_id: str,
    output_dir: pathlib.Path,
    adapter_executable: pathlib.Path,
    adapter_name: str,
    age_executable: str = "age",
    age_keygen_executable: str = "age-keygen",
    post_quantum: bool = True,
) -> tuple[pathlib.Path, pathlib.Path]:
    if not source_tar.is_file() or source_tar.is_symlink():
        raise EnvelopeError("source tar must be one regular file")
    # Validate every caller-controlled identifier before creating secret
    # material or invoking either external tool.
    canonical_archive_path(submission_id)
    if ADAPTER.fullmatch(adapter_name) is None:
        raise EnvelopeError("adapter name is not canonical")
    if not adapter_executable.is_file() or adapter_executable.is_symlink():
        raise EnvelopeError("adapter executable must be one regular file")
    if output_dir.exists() or output_dir.is_symlink():
        raise EnvelopeError("output directory must not already exist")
    output_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ciphertext_output = output_dir / "source.tar.gz.age"
    envelope_output = output_dir / "archive-key-envelope.json"

    with tempfile.TemporaryDirectory(prefix="lean-eval-secret-") as raw_secret:
        secret_dir = pathlib.Path(raw_secret)
        identity_path = secret_dir / "identity.age"
        keygen = [age_keygen_executable]
        if post_quantum:
            keygen.append("-pq")
        keygen.extend(["--output", str(identity_path)])
        _run_age(keygen, label="age-keygen")
        identity = _read_identity(identity_path)

        recipient_result = _run_age(
            [age_keygen_executable, "-y", str(identity_path)],
            label="age recipient derivation",
        )
        recipient_lines = [
            line.strip()
            for line in recipient_result.stdout.splitlines()
            if line.strip()
        ]
        if len(recipient_lines) != 1 or AGE_RECIPIENT.fullmatch(recipient_lines[0]) is None:
            raise EnvelopeError("age-keygen returned a noncanonical recipient")
        recipient = recipient_lines[0]

        staging_raw = tempfile.mkdtemp(
            prefix=f".{output_dir.name}-",
            dir=output_dir.parent,
        )
        staging = pathlib.Path(staging_raw)
        try:
            ciphertext_temp = staging / ciphertext_output.name
            _run_age(
                [
                    age_executable,
                    "--encrypt",
                    "--recipient",
                    recipient,
                    "--output",
                    str(ciphertext_temp),
                    str(source_tar),
                ],
                label="age encryption",
            )
            try:
                with ciphertext_temp.open("rb") as ciphertext:
                    header = ciphertext.read(32)
            except OSError as error:
                raise EnvelopeError("age did not create a readable ciphertext") from error
            if not header.startswith(b"age-encryption.org/v1\n"):
                raise EnvelopeError("age output does not have the v1 ciphertext header")

            archive_digest = _sha256(ciphertext_temp)
            data_key_identity = archive_key_id(submission_id, recipient)
            context = envelope_binding_context(
                submission_id,
                archive_digest,
                data_key_identity,
                recipient,
            )
            wrapped = _adapter_wrap(
                adapter_executable=adapter_executable,
                adapter_name=adapter_name,
                identity=identity,
                context=context,
            )
            if wrapped == base64.b64encode(identity).decode("ascii"):
                raise EnvelopeError("root-key adapter returned the plaintext identity")
            envelope = validate_envelope({
                "schema_version": 1,
                "submission_id": submission_id,
                "archive_ciphertext_sha256": archive_digest,
                "data_key_id": data_key_identity,
                "age_recipient": recipient,
                "adapter": adapter_name,
                "wrapped_identity": wrapped,
            })
            envelope_temp = staging / envelope_output.name
            envelope_temp.write_text(
                json.dumps(envelope, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            # Renaming the complete directory makes publication all-or-nothing:
            # callers never observe only one of the two required artifacts.
            os.replace(staging, output_dir)
        finally:
            if staging.exists():
                for child in staging.iterdir():
                    child.unlink()
                staging.rmdir()
    return ciphertext_output, envelope_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tar", required=True, type=pathlib.Path)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--adapter-executable", required=True, type=pathlib.Path)
    parser.add_argument("--adapter-name", required=True)
    parser.add_argument("--age-executable", default="age")
    parser.add_argument("--age-keygen-executable", default="age-keygen")
    parser.add_argument("--classic-age-key", action="store_true")
    args = parser.parse_args(argv)
    try:
        ciphertext, envelope = create_archive_envelope(
            source_tar=args.source_tar,
            submission_id=args.submission_id,
            output_dir=args.output_dir,
            adapter_executable=args.adapter_executable,
            adapter_name=args.adapter_name,
            age_executable=args.age_executable,
            age_keygen_executable=args.age_keygen_executable,
            post_quantum=not args.classic_age_key,
        )
    except (ContractError, EnvelopeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps({
        "ciphertext": str(ciphertext),
        "envelope": str(envelope),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
