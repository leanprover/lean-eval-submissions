"""Unit tests for scripts/archive_submission.py.

The script shells out to `age` for encryption and to the GitHub
Contents API for upload. Both are mocked here so the tests run without
network and without an age binary; an integration test that actually
encrypts + decrypts a fixture lives outside CI (manual decrypt drill,
see docs/audit-archive.md).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import archive_submission as arch  # noqa: E402


VALID_REF = "0123456789abcdef0123456789abcdef01234567"
VALID_PLAINTEXT_SHA = "a" * 64


def _make_source_tar(dir: pathlib.Path, *, size_padding: int = 0) -> pathlib.Path:
    src = dir / "src"
    src.mkdir()
    (src / "Submission.lean").write_text("-- proof\n" * (1 + size_padding))
    tar = dir / "source.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        tf.add(src, arcname="src")
    return tar


def _make_metadata(dir: pathlib.Path, **overrides) -> pathlib.Path:
    metadata = {
        "issue_number": 99,
        "submission_ref": VALID_REF,
        "submission_repo": "alice/proofs",
        "submission_kind": "github_repo",
        "submission_public": False,
        "submitted_by": "alice",
        "model": "Test Model",
        "source_url": "https://github.com/alice/proofs",
    }
    metadata.update(overrides)
    path = dir / "metadata.json"
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True))
    return path


def _make_recipients(dir: pathlib.Path) -> pathlib.Path:
    path = dir / "recipients.txt"
    path.write_text(
        "# comment line\n"
        "\n"
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINKUKk+pAoleaA9jwH4/r6Rt31b5Aet3KExKKhuTkZb1 test\n"
    )
    return path


def _fake_age(args, **kwargs):
    """subprocess.run side_effect that writes a structurally valid v1 ciphertext."""
    idx = args.index("--output")
    output_path = pathlib.Path(args[idx + 1])
    output_path.write_bytes(
        b"age-encryption.org/v1\n-> X25519 fakefake\n--- fakemac\nfakebody"
    )
    return mock.Mock(returncode=0, stderr="", stdout="")


class EncryptTests(unittest.TestCase):
    def test_encrypt_writes_ciphertext_and_partial_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = _make_source_tar(tmp)
            metadata = _make_metadata(tmp)
            recipients = _make_recipients(tmp)
            out = tmp / "out"
            with mock.patch.object(arch.subprocess, "run", side_effect=_fake_age):
                rc = arch.main([
                    "encrypt",
                    "--source-tar", str(source_tar),
                    "--metadata", str(metadata),
                    "--recipients", str(recipients),
                    "--output-dir", str(out),
                ])
            self.assertEqual(rc, 0)
            self.assertTrue((out / "source.tar.gz.age").is_file())
            sidecar = json.loads((out / "sidecar.partial.json").read_text())
            self.assertEqual(sidecar["schema_version"], 1)
            self.assertEqual(sidecar["issue"], 99)
            self.assertEqual(sidecar["submission_repo"], "alice/proofs")
            self.assertEqual(sidecar["submitter"], "alice")
            self.assertEqual(sidecar["submission_public"], False)
            self.assertIn("sha256_plaintext_tar", sidecar)
            self.assertEqual(len(sidecar["sha256_plaintext_tar"]), 64)
            self.assertNotIn("sha256_ciphertext", sidecar)
            self.assertNotIn("archived_at", sidecar)
            self.assertNotIn("evaluator_verdict", sidecar)

    def test_encrypt_rejects_oversize_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = tmp / "source.tar.gz"
            source_tar.write_bytes(b"x" * (arch.SIZE_CAP_BYTES + 1))
            metadata = _make_metadata(tmp)
            recipients = _make_recipients(tmp)
            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "encrypt",
                    "--source-tar", str(source_tar),
                    "--metadata", str(metadata),
                    "--recipients", str(recipients),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertIn("over the", str(ctx.exception))

    def test_encrypt_rejects_empty_recipients(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = _make_source_tar(tmp)
            metadata = _make_metadata(tmp)
            recipients = tmp / "recipients.txt"
            recipients.write_text("# only comments\n\n")
            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "encrypt",
                    "--source-tar", str(source_tar),
                    "--metadata", str(metadata),
                    "--recipients", str(recipients),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertIn("empty", str(ctx.exception).lower())

    def test_encrypt_rejects_missing_metadata_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = _make_source_tar(tmp)
            recipients = _make_recipients(tmp)
            metadata = tmp / "metadata.json"
            metadata.write_text(json.dumps({"issue_number": 1}))
            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "encrypt",
                    "--source-tar", str(source_tar),
                    "--metadata", str(metadata),
                    "--recipients", str(recipients),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertIn("missing", str(ctx.exception).lower())

    def test_encrypt_rejects_bogus_age_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = _make_source_tar(tmp)
            metadata = _make_metadata(tmp)
            recipients = _make_recipients(tmp)

            def fake_age(args, **kwargs):
                idx = args.index("--output")
                pathlib.Path(args[idx + 1]).write_bytes(b"not-age-encrypted")
                return mock.Mock(returncode=0, stderr="", stdout="")

            with mock.patch.object(arch.subprocess, "run", side_effect=fake_age):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "encrypt",
                        "--source-tar", str(source_tar),
                        "--metadata", str(metadata),
                        "--recipients", str(recipients),
                        "--output-dir", str(tmp / "out"),
                    ])
            self.assertIn("header", str(ctx.exception).lower())

    def test_encrypt_rejects_string_submission_public(self) -> None:
        # `bool("false") is True` — without strict typing, encrypt would
        # silently record a private submission as public in the sidecar.
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = _make_source_tar(tmp)
            recipients = _make_recipients(tmp)
            metadata = _make_metadata(tmp, submission_public="false")
            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "encrypt",
                    "--source-tar", str(source_tar),
                    "--metadata", str(metadata),
                    "--recipients", str(recipients),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertIn("submission_public", str(ctx.exception))

    def test_encrypt_rejects_non_string_model(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = _make_source_tar(tmp)
            recipients = _make_recipients(tmp)
            metadata = _make_metadata(tmp, model=42)
            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "encrypt",
                    "--source-tar", str(source_tar),
                    "--metadata", str(metadata),
                    "--recipients", str(recipients),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertIn("'model'", str(ctx.exception))

    def test_encrypt_rejects_malformed_submission_ref(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = _make_source_tar(tmp)
            recipients = _make_recipients(tmp)
            metadata = _make_metadata(tmp, submission_ref="not-a-sha")
            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "encrypt",
                    "--source-tar", str(source_tar),
                    "--metadata", str(metadata),
                    "--recipients", str(recipients),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertIn("40-char", str(ctx.exception))

    def test_encrypt_rejects_unknown_submission_kind(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = _make_source_tar(tmp)
            recipients = _make_recipients(tmp)
            metadata = _make_metadata(tmp, submission_kind="tarball")
            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "encrypt",
                    "--source-tar", str(source_tar),
                    "--metadata", str(metadata),
                    "--recipients", str(recipients),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertIn("submission_kind", str(ctx.exception))


class PushTests(unittest.TestCase):
    def _partial_sidecar(self, dir: pathlib.Path, **overrides) -> pathlib.Path:
        sidecar = {
            "schema_version": 1,
            "issue": 99,
            "submission_repo": "alice/proofs",
            "submission_ref": VALID_REF,
            "submission_kind": "github_repo",
            "submission_public": False,
            "submitter": "alice",
            "model": "Test Model",
            "size_bytes_plaintext_tar": 1234,
            "sha256_plaintext_tar": VALID_PLAINTEXT_SHA,
        }
        sidecar.update(overrides)
        path = dir / "sidecar.partial.json"
        path.write_text(json.dumps(sidecar))
        return path

    def _ciphertext(self, dir: pathlib.Path, body: bytes = b"age-encryption.org/v1\nfake") -> pathlib.Path:
        path = dir / "source.tar.gz.age"
        path.write_bytes(body)
        return path

    def test_push_uploads_ciphertext_then_sidecar_from_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp)
            summary = tmp / "summary.json"
            # Real shape from evaluate_submission.py: per-problem records
            # live under summary["run_eval"]["problems"].
            summary.write_text(json.dumps({
                "run_eval": {
                    "problems": [
                        {"id": "two_plus_two", "succeeded": True, "attempted": True},
                        {"id": "halting_problem", "succeeded": False, "attempted": True},
                        {"id": "p_eq_np", "succeeded": False, "attempted": False},
                    ],
                },
                "overlay_records": [],
            }))

            seen_requests: list[dict] = []

            def fake_urlopen(req, timeout=None):
                seen_requests.append({
                    "url": req.full_url,
                    "method": req.get_method(),
                    "body": json.loads(req.data.decode("utf-8")) if req.data else None,
                    "headers": dict(req.header_items()),
                })
                return io.BytesIO(b'{"content": {"sha": "deadbeef"}}')

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                    "--summary", str(summary),
                    "--benchmark-commit", "f" * 40,
                    "--workflow-run-url", "https://example.invalid/run/1",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(len(seen_requests), 2)
            urls = [r["url"] for r in seen_requests]
            self.assertTrue(all("/repos/leanprover/lean-eval-audit/contents/audit/" in u for u in urls))
            self.assertTrue(any(u.endswith("-01234567.tar.age") for u in urls))
            self.assertTrue(any(u.endswith("-01234567.json") for u in urls))
            self.assertTrue(urls[0].endswith(".tar.age"))
            self.assertTrue(urls[1].endswith(".json"))
            sidecar_body = seen_requests[1]["body"]
            uploaded_sidecar = json.loads(
                base64.b64decode(sidecar_body["content"]).decode("utf-8")
            )
            self.assertEqual(uploaded_sidecar["evaluator_verdict"], {
                "two_plus_two": "pass",
                "halting_problem": "fail",
                "p_eq_np": "skipped",
            })
            self.assertEqual(uploaded_sidecar["problem_ids"],
                             ["halting_problem", "p_eq_np", "two_plus_two"])
            self.assertEqual(len(uploaded_sidecar["sha256_ciphertext"]), 64)
            self.assertEqual(uploaded_sidecar["benchmark_commit"], "f" * 40)
            self.assertIn("archived_at", uploaded_sidecar)
            self.assertEqual(seen_requests[0]["headers"]["Authorization"], "Bearer xxx")

    def test_push_idempotent_when_existing_matches(self) -> None:
        # Workflow rerun after a partial failure: ciphertext is already
        # uploaded with matching bytes. Push must complete successfully
        # without trying to overwrite.
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext_bytes = b"age-encryption.org/v1\nfakebody"
            ciphertext = self._ciphertext(tmp, ciphertext_bytes)
            sidecar = self._partial_sidecar(tmp)
            expected_blob_sha = arch._git_blob_sha(ciphertext_bytes)

            already_exists_body = json.dumps({
                "message": "Invalid request.",
                "errors": [{"message": 'file already exists'}],
            }).encode("utf-8")

            call_log: list[tuple[str, str]] = []

            def fake_urlopen(req, timeout=None):
                method = req.get_method()
                url = req.full_url
                call_log.append((method, url))
                if method == "PUT" and url.endswith(".tar.age"):
                    raise urllib.error.HTTPError(
                        url, 422, "Unprocessable Entity", {}, io.BytesIO(already_exists_body)
                    )
                if method == "GET" and url.endswith(".tar.age"):
                    return io.BytesIO(json.dumps({"sha": expected_blob_sha}).encode())
                # Sidecar PUT succeeds.
                return io.BytesIO(b'{"content": {"sha": "deadbeef"}}')

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                ])
            self.assertEqual(rc, 0)
            # Ciphertext PUT (422) → GET (verify match) → sidecar PUT.
            self.assertEqual(call_log[0][0], "PUT")
            self.assertEqual(call_log[1][0], "GET")
            self.assertEqual(call_log[2][0], "PUT")
            self.assertTrue(call_log[2][1].endswith(".json"))

    def test_push_fails_when_existing_ciphertext_differs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp, b"age-encryption.org/v1\nlocal")
            sidecar = self._partial_sidecar(tmp)
            wrong_sha = arch._git_blob_sha(b"different bytes")

            already_exists_body = json.dumps({
                "errors": [{"message": "file already exists"}],
            }).encode("utf-8")

            def fake_urlopen(req, timeout=None):
                if req.get_method() == "PUT":
                    raise urllib.error.HTTPError(
                        req.full_url, 422, "Unprocessable Entity", {},
                        io.BytesIO(already_exists_body),
                    )
                return io.BytesIO(json.dumps({"sha": wrong_sha}).encode())

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("different content", str(ctx.exception).lower())

    def test_push_omits_verdict_when_summary_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp)
            seen_bodies: list[dict] = []

            def fake_urlopen(req, timeout=None):
                seen_bodies.append(json.loads(req.data.decode("utf-8")) if req.data else {})
                return io.BytesIO(b'{}')

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                ])
            self.assertEqual(rc, 0)
            uploaded = json.loads(base64.b64decode(seen_bodies[1]["content"]).decode("utf-8"))
            self.assertNotIn("evaluator_verdict", uploaded)
            self.assertNotIn("problem_ids", uploaded)

    def test_push_rejects_empty_archiver_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp)
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": ""}, clear=False):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("ARCHIVER_TOKEN", str(ctx.exception))

    def test_push_validates_sidecar_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp, schema_version=99)
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("schema_version", str(ctx.exception))

    def test_push_validates_sidecar_submission_ref(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp, submission_ref="../../../etc/passwd")
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("submission_ref", str(ctx.exception))

    def test_push_validates_sidecar_submission_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp, submission_repo="../weird")
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("submission_repo", str(ctx.exception))

    def test_push_validates_sidecar_submission_public_type(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp, submission_public="false")
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("submission_public", str(ctx.exception))

    def test_push_validates_benchmark_commit_format(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp)
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                        "--benchmark-commit", "not-a-sha",
                    ])
            self.assertIn("benchmark-commit", str(ctx.exception))


class GitBlobShaTests(unittest.TestCase):
    def test_matches_git_hash_object(self) -> None:
        # Reference value computed with `git hash-object` for the same
        # content. If this drifts, the idempotency check is comparing
        # against the wrong digest.
        self.assertEqual(
            arch._git_blob_sha(b"hello\n"),
            "ce013625030ba8dba906f756967f9e9ca394464a",
        )
        self.assertEqual(
            arch._git_blob_sha(b""),
            "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
        )


if __name__ == "__main__":
    unittest.main()
