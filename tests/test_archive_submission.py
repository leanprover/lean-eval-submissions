"""Unit tests for scripts/archive_submission.py.

The script shells out to `age` for encryption and to the GitHub
Contents API for upload. Both are mocked here so the tests run without
network and without an age binary; an integration test that actually
encrypts + decrypts a fixture lives outside CI (manual decrypt drill,
see docs/audit-archive.md).
"""

from __future__ import annotations

import base64
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


def _make_source_tar(dir: pathlib.Path, *, size_padding: int = 0) -> pathlib.Path:
    """Build a small valid gzipped tar under `dir`."""
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
        "submission_ref": "0123456789abcdef0123456789abcdef01234567",
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


class EncryptTests(unittest.TestCase):
    def test_encrypt_writes_ciphertext_and_partial_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = _make_source_tar(tmp)
            metadata = _make_metadata(tmp)
            recipients = _make_recipients(tmp)
            out = tmp / "out"

            def fake_age(args, **kwargs):
                # Capture age args and emit a structurally valid ciphertext.
                self.assertEqual(args[0], "age")
                self.assertIn("--recipients-file", args)
                idx = args.index("--output")
                output_path = pathlib.Path(args[idx + 1])
                output_path.write_bytes(
                    b"age-encryption.org/v1\n-> X25519 fakefake\n--- fakemac\nfakebody"
                )
                return mock.Mock(returncode=0, stderr="", stdout="")

            with mock.patch.object(arch.subprocess, "run", side_effect=fake_age):
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
            # Ciphertext fields are NOT filled at encrypt time; push merges them.
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
            # Missing `submitted_by`, `model`, etc.
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
        # If `age` produces output that does not start with the v1
        # header, the script must not trust the file as ciphertext.
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


class PushTests(unittest.TestCase):
    def _partial_sidecar(self, dir: pathlib.Path) -> pathlib.Path:
        path = dir / "sidecar.partial.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "issue": 99,
            "submission_repo": "alice/proofs",
            "submission_ref": "0123456789abcdef0123456789abcdef01234567",
            "submission_kind": "github_repo",
            "submission_public": False,
            "submitter": "alice",
            "model": "Test Model",
            "size_bytes_plaintext_tar": 1234,
            "sha256_plaintext_tar": "a" * 64,
        }))
        return path

    def test_push_uploads_ciphertext_then_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = tmp / "source.tar.gz.age"
            ciphertext.write_bytes(b"age-encryption.org/v1\nfake")
            sidecar = self._partial_sidecar(tmp)
            results = tmp / "results.json"
            results.write_text(json.dumps({
                "problems": [
                    {"id": "two_plus_two", "succeeded": True, "attempted": True},
                    {"id": "halting_problem", "succeeded": False, "attempted": True},
                    {"id": "p_eq_np", "succeeded": False, "attempted": False},
                ]
            }))

            seen_requests: list[dict] = []

            def fake_urlopen(req, timeout=None):
                seen_requests.append({
                    "url": req.full_url,
                    "method": req.get_method(),
                    "headers": dict(req.header_items()),
                    "body": json.loads(req.data.decode("utf-8")),
                })
                return io.BytesIO(b'{"content": {"sha": "deadbeef"}}')

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                    "--results", str(results),
                    "--benchmark-commit", "f" * 40,
                    "--workflow-run-url", "https://example.invalid/run/1",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(len(seen_requests), 2)
            urls = [r["url"] for r in seen_requests]
            # Both PUTs go to lean-eval-audit under audit/YYYY/MM/{issue}-{ref8}.{tar.age,json}
            self.assertTrue(all("/repos/leanprover/lean-eval-audit/contents/audit/" in u for u in urls))
            self.assertTrue(any(u.endswith("-01234567.tar.age") for u in urls))
            self.assertTrue(any(u.endswith("-01234567.json") for u in urls))
            # Ciphertext is uploaded before the sidecar (worst-failure-state
            # invariant).
            self.assertTrue(urls[0].endswith(".tar.age"))
            self.assertTrue(urls[1].endswith(".json"))
            # Verify the sidecar uploaded as part of the second request
            # includes the merged evaluator verdict and ciphertext digest.
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
            # Bearer token is sent via header.
            self.assertEqual(seen_requests[0]["headers"]["Authorization"], "Bearer xxx")

    def test_push_refuses_overwrite(self) -> None:
        # The Contents API returns 422 when a file already exists at the
        # given path; the script must not silently overwrite — operator
        # has to investigate why two archives are colliding.
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = tmp / "source.tar.gz.age"
            ciphertext.write_bytes(b"age-encryption.org/v1\nfake")
            sidecar = self._partial_sidecar(tmp)

            err_body = json.dumps({
                "message": "Invalid request.\n\n\"sha\" wasn't supplied.",
                "errors": [{"message": 'file with path "audit/2026/05/99-01234567.tar.age" already exists'}],
            }).encode("utf-8")

            def fake_urlopen(req, timeout=None):
                raise urllib.error.HTTPError(
                    req.full_url, 422, "Unprocessable Entity", {}, io.BytesIO(err_body)
                )

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("already exists", str(ctx.exception).lower())

    def test_push_omits_verdict_when_results_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = tmp / "source.tar.gz.age"
            ciphertext.write_bytes(b"age-encryption.org/v1\nfake")
            sidecar = self._partial_sidecar(tmp)
            seen_bodies: list[dict] = []

            def fake_urlopen(req, timeout=None):
                seen_bodies.append(json.loads(req.data.decode("utf-8")))
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
            ciphertext = tmp / "source.tar.gz.age"
            ciphertext.write_bytes(b"age-encryption.org/v1\nfake")
            sidecar = self._partial_sidecar(tmp)
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": ""}, clear=False):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("ARCHIVER_TOKEN", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
