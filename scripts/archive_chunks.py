"""Bounded Git blobs for large age archives, with end-to-end digest checks.

Small archives remain ordinary age files. Large archives place a manifest at
the same canonical path and encrypted parts beside it. A State locator's digest
always names the reassembled ciphertext, independently of its storage format.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections.abc import Callable, Iterator


SINGLE_BLOB_MAX_BYTES = 100 * 1024 * 1024
PART_BYTES = 50_000_000
MAX_CIPHERTEXT_BYTES = 10_010_000_000  # 10 GB source plus age framing.
FORMAT = "lean-eval-chunked-age-v1"
MAX_MANIFEST_BYTES = 64 * 1024


def part_path(archive_path: str, digest: str, index: int) -> str:
    return f"{archive_path}.parts/{digest}/{index:04d}"


def store_archive(
    source: pathlib.Path,
    archive_path: str,
    digest: str,
    put: Callable[[str, bytes], object],
) -> None:
    size = source.stat().st_size
    if not 0 < size <= MAX_CIPHERTEXT_BYTES:
        raise ValueError("ciphertext exceeds the archive storage limit")
    if size <= SINGLE_BLOB_MAX_BYTES:
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("ciphertext changed before upload")
        put(archive_path, content)
        return
    parts = []
    whole = hashlib.sha256()
    with source.open("rb") as stream:
        for index, content in enumerate(iter(lambda: stream.read(PART_BYTES), b"")):
            whole.update(content)
            parts.append({"size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
            put(part_path(archive_path, digest, index), content)
    if whole.hexdigest() != digest or sum(p["size_bytes"] for p in parts) != size:
        raise ValueError("ciphertext changed during upload")
    manifest = {
        "format": FORMAT,
        "size_bytes": size,
        "sha256": digest,
        "part_size_bytes": PART_BYTES,
        "parts": parts,
    }
    put(archive_path, (json.dumps(manifest, sort_keys=True) + "\n").encode())


def archive_bytes(
    archive_path: str,
    expected_digest: str,
    read: Callable[[str], bytes],
) -> Iterator[bytes]:
    """Yield verified pieces and reject missing, reordered, or altered parts."""
    content = read(archive_path)
    if len(content) > max(SINGLE_BLOB_MAX_BYTES, MAX_MANIFEST_BYTES):
        raise ValueError("archive root exceeds the single-blob limit")
    if hashlib.sha256(content).hexdigest() == expected_digest:
        if len(content) > SINGLE_BLOB_MAX_BYTES:
            raise ValueError("single archive exceeds the Git blob limit")
        yield content
        return
    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("archive manifest exceeds its size limit")
    try:
        manifest = json.loads(content)
    except (ValueError, UnicodeError) as error:
        raise ValueError("archive ciphertext digest mismatch") from error
    if not isinstance(manifest, dict) or set(manifest) != {
        "format", "size_bytes", "sha256", "part_size_bytes", "parts"
    }:
        raise ValueError("invalid archive manifest fields")
    size, stride, parts = manifest["size_bytes"], manifest["part_size_bytes"], manifest["parts"]
    if (
        manifest["format"] != FORMAT or manifest["sha256"] != expected_digest
        or type(size) is not int or not SINGLE_BLOB_MAX_BYTES < size <= MAX_CIPHERTEXT_BYTES
        or type(stride) is not int or not 0 < stride <= PART_BYTES
        or not isinstance(parts, list) or len(parts) != (size + stride - 1) // stride
        or len(parts) > (MAX_CIPHERTEXT_BYTES + PART_BYTES - 1) // PART_BYTES
    ):
        raise ValueError("invalid archive manifest identity or bounds")
    whole = hashlib.sha256()
    for index, part in enumerate(parts):
        expected_size = min(stride, size - index * stride)
        if (
            not isinstance(part, dict) or set(part) != {"size_bytes", "sha256"}
            or type(part["size_bytes"]) is not int or part["size_bytes"] != expected_size
            or not isinstance(part["sha256"], str) or len(part["sha256"]) != 64
        ):
            raise ValueError("invalid archive part metadata")
        data = read(part_path(archive_path, expected_digest, index))
        if len(data) != expected_size or hashlib.sha256(data).hexdigest() != part["sha256"]:
            raise ValueError(f"archive part {index} size or digest mismatch")
        whole.update(data)
        yield data
    if whole.hexdigest() != expected_digest:
        raise ValueError("reassembled archive ciphertext digest mismatch")
