"""Round-trip and corruption tests for multipart archive storage."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import archive_chunks as chunks
import archive_submission as archive


ARCHIVE_PATH = "archives/01/0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f.tar.age"
SOURCE = b"age-encryption.org/v1\n" + bytes(range(128))
DIGEST = hashlib.sha256(SOURCE).hexdigest()


class ArchiveChunksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)
        self.source = self.root / "source.age"
        self.source.write_bytes(SOURCE)
        self.blobs = {}
        self.limit = mock.patch.object(chunks, "SINGLE_BLOB_MAX_BYTES", 80)
        self.part = mock.patch.object(chunks, "PART_BYTES", 64)

    def store(self) -> None:
        with self.limit, self.part:
            chunks.store_archive(self.source, ARCHIVE_PATH, DIGEST, self.blobs.__setitem__)

    def read(self) -> bytes:
        with self.limit, self.part:
            return b"".join(chunks.archive_bytes(ARCHIVE_PATH, DIGEST, self.blobs.__getitem__))

    def test_small_archives_keep_original_bytes(self) -> None:
        chunks.store_archive(self.source, ARCHIVE_PATH, DIGEST, self.blobs.__setitem__)
        self.assertEqual(self.blobs, {ARCHIVE_PATH: SOURCE})
        self.assertEqual(b"".join(chunks.archive_bytes(ARCHIVE_PATH, DIGEST, self.blobs.__getitem__)), SOURCE)

    def test_large_archive_streams_parts_before_manifest(self) -> None:
        self.store()
        self.assertEqual(list(self.blobs)[-1], ARCHIVE_PATH)
        manifest = json.loads(self.blobs[ARCHIVE_PATH])
        self.assertEqual(manifest["size_bytes"], len(SOURCE))
        self.assertTrue(all(len(v) <= 64 for p, v in self.blobs.items() if p != ARCHIVE_PATH))
        self.assertEqual(self.read(), SOURCE)

    def test_modified_part_and_wrong_whole_digest_are_rejected(self) -> None:
        self.store()
        path = chunks.part_path(ARCHIVE_PATH, DIGEST, 1)
        self.blobs[path] = b"x" * len(self.blobs[path])
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.read()

    def test_missing_part_cannot_produce_a_completed_archive(self) -> None:
        self.store()
        del self.blobs[chunks.part_path(ARCHIVE_PATH, DIGEST, 1)]
        with self.assertRaises(KeyError):
            self.read()

    def test_failed_upload_never_publishes_a_manifest(self) -> None:
        def put(path: str, content: bytes) -> None:
            if path.endswith("/0001"):
                raise OSError("upload interrupted")
            self.blobs[path] = content
        with self.limit, self.part, self.assertRaises(OSError):
            chunks.store_archive(self.source, ARCHIVE_PATH, DIGEST, put)
        self.assertNotIn(ARCHIVE_PATH, self.blobs)

    def test_server_verifies_every_part_at_the_same_commit(self) -> None:
        self.store()
        commit = "a" * 40
        def read(**kwargs):
            self.assertEqual(kwargs["archive_commit"], commit)
            return self.blobs[kwargs["archive_path"]]
        with self.limit, self.part, mock.patch.object(
            archive, "_read_archive_blob_at_commit", side_effect=read,
        ) as reader:
            archive._verify_ciphertext_at_commit(
                audit_repo="leanprover/lean-eval-audit", token="token",
                archive_commit=commit, archive_path=ARCHIVE_PATH, expected_sha256=DIGEST,
            )
        self.assertEqual(reader.call_count, len(self.blobs))

    def test_reordered_parts_fail_the_whole_ciphertext_digest(self) -> None:
        self.store()
        manifest = json.loads(self.blobs[ARCHIVE_PATH])
        first = chunks.part_path(ARCHIVE_PATH, DIGEST, 0)
        second = chunks.part_path(ARCHIVE_PATH, DIGEST, 1)
        self.blobs[first], self.blobs[second] = self.blobs[second], self.blobs[first]
        manifest["parts"][0], manifest["parts"][1] = manifest["parts"][1], manifest["parts"][0]
        self.blobs[ARCHIVE_PATH] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, "reassembled"):
            self.read()

    def test_manifest_cannot_select_other_paths_or_unbounded_parts(self) -> None:
        self.store()
        manifest = json.loads(self.blobs[ARCHIVE_PATH])
        manifest["parts"][0]["path"] = "other/submission"
        self.blobs[ARCHIVE_PATH] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.read()

    def test_restore_from_git_is_atomic_and_checks_immutable_bytes(self) -> None:
        self.store()
        repo = self.root / "audit"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for path, data in self.blobs.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run([
            "git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-qm", "archive",
        ], check=True)
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        output = self.root / "restored.age"
        with self.limit, self.part:
            archive.main([
                "restore", "--audit-root", str(repo), "--commit", commit,
                "--archive-path", ARCHIVE_PATH, "--sha256", DIGEST, "--output", str(output),
            ])
        self.assertEqual(output.read_bytes(), SOURCE)
        with self.limit, self.part, self.assertRaises(SystemExit):
            archive.main([
                "restore", "--audit-root", str(repo), "--commit", commit,
                "--archive-path", ARCHIVE_PATH, "--sha256", DIGEST, "--output", str(output),
            ])
        self.assertEqual(output.read_bytes(), SOURCE)

    def test_sparse_partial_clone_fetches_parts_without_retaining_them(self) -> None:
        self.store()
        repo = self.root / "origin"
        repo.mkdir()
        def git(root, *args):
            return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)
        git(repo, "init", "-q")
        git(repo, "config", "uploadpack.allowFilter", "true")
        for path, data in self.blobs.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        git(repo, "add", ".")
        git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "archive")
        clone = self.root / "partial"
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", repo.as_uri(), str(clone)], check=True)
        git(clone, "sparse-checkout", "set", "--no-cone", "/*", "!**/*.tar.age.parts/")
        git(clone, "checkout", "-q")
        root = clone / ARCHIVE_PATH
        self.assertTrue(root.is_file())
        self.assertFalse(pathlib.Path(str(root) + ".parts").exists())
        before = sorted(p.relative_to(clone).as_posix() for p in (clone / ".git/objects").rglob("*") if p.is_file())
        with self.limit, self.part:
            self.assertEqual(b"".join(chunks.path_archive_bytes(root, DIGEST)), SOURCE)
        after = sorted(p.relative_to(clone).as_posix() for p in (clone / ".git/objects").rglob("*") if p.is_file())
        self.assertEqual(before, after)

    def test_corrupt_restore_leaves_no_output_or_temporary_file(self) -> None:
        self.store()
        self.blobs[chunks.part_path(ARCHIVE_PATH, DIGEST, 1)] = b"corrupt"
        output = self.root / "output" / "restore.age"
        with self.limit, self.part, mock.patch.object(
            archive, "git_blob", side_effect=lambda root, commit, path: self.blobs[path],
        ), self.assertRaises(SystemExit):
            archive.main([
                "restore", "--audit-root", str(self.root), "--commit", "a" * 40,
                "--archive-path", ARCHIVE_PATH, "--sha256", DIGEST, "--output", str(output),
            ])
        self.assertEqual(list(output.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
