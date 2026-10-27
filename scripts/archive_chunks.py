"""Bounded Git blobs for large age archives, with end-to-end digest checks.

Small archives remain ordinary age files. Large archives place a manifest at
the same canonical path and encrypted parts beside it. A State locator's digest
always names the reassembled ciphertext, independently of its storage format.
"""

from __future__ import annotations

import hashlib
import json
import base64
import os
import pathlib
import shlex
import subprocess
import tempfile
from collections.abc import Callable, Iterator


SINGLE_BLOB_MAX_BYTES = 100 * 1024 * 1024
PART_BYTES = 50_000_000
MAX_CIPHERTEXT_BYTES = 10_010_000_000  # 10 GB source plus age framing.
FORMAT = "lean-eval-chunked-age-v1"
MAX_MANIFEST_BYTES = 64 * 1024

# GitHub's published Ed25519 host key (https://api.github.com/meta).
GITHUB_HOST_KEY = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"


def git_blob(root: pathlib.Path, commit: str, path: str) -> bytes:
    """Read a blob from a partial audit clone without retaining downloaded parts.

    The caller supplies read authority only for this trusted read. Temporary
    objects prevent a complete inventory from filling the runner with all
    historical large archives. Existing repository objects remain read-only.
    """
    objects = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-path", "objects"],
        text=True,
    ).strip()
    with tempfile.TemporaryDirectory(prefix="archive-read-") as directory:
        temporary = pathlib.Path(directory)
        (temporary / "objects").mkdir()
        environment = os.environ.copy()
        environment["GIT_OBJECT_DIRECTORY"] = str(temporary / "objects")
        environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = objects
        token = environment.pop("ARCHIVE_READ_TOKEN", "")
        key = environment.pop("AUDIT_READ_KEY", "")
        if token:
            count = int(environment.get("GIT_CONFIG_COUNT", "0"))
            settings = [
                ("http.https://github.com/.extraheader", "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()),
                ("url.https://github.com/.insteadOf", "git@github.com:"),
            ]
            for index, (name, value) in enumerate(settings, count):
                environment[f"GIT_CONFIG_KEY_{index}"] = name
                environment[f"GIT_CONFIG_VALUE_{index}"] = value
            environment["GIT_CONFIG_COUNT"] = str(count + len(settings))
        elif key:
            identity = temporary / "identity"
            identity.write_text(key.rstrip() + "\n")
            identity.chmod(0o600)
            hosts = temporary / "known_hosts"
            hosts.write_text(GITHUB_HOST_KEY)
            environment["GIT_SSH_COMMAND"] = (
                f"ssh -i {shlex.quote(str(identity))} -o IdentitiesOnly=yes "
                f"-o UserKnownHostsFile={shlex.quote(str(hosts))} "
                "-o StrictHostKeyChecking=yes -o HostKeyAlgorithms=ssh-ed25519"
            )
        result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "blob", f"{commit}:{path}"],
            env=environment, capture_output=True, check=False,
        )
        if result.returncode:
            raise ValueError(f"cannot read immutable archive blob {commit}:{path}")
        return result.stdout


def path_archive_bytes(path: pathlib.Path, expected_digest: str) -> Iterator[bytes]:
    """Read either format from a full or sparse audit checkout."""
    path = path.absolute()
    def read(relative: str) -> bytes:
        target = path.parent / relative
        if target.is_symlink():
            raise ValueError("archive data must not be a symlink")
        if target.is_file():
            return target.read_bytes()
        root = pathlib.Path(subprocess.check_output(
            ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"], text=True,
        ).strip())
        return git_blob(root, "HEAD", target.relative_to(root).as_posix())
    yield from archive_bytes(path.name, expected_digest, read)


def archive_size(path: pathlib.Path, expected_digest: str) -> int:
    """Verify the entire ciphertext and return its logical size."""
    return sum(len(data) for data in path_archive_bytes(path, expected_digest))


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
