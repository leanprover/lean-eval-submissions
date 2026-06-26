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

    @staticmethod
    def _sidecar_meta(*, sha: str = "abc123", **identity_overrides) -> bytes:
        """A Contents-API GET response for an existing sidecar file.

        The decoded sidecar defaults to the identity written by
        `_partial_sidecar`; pass keyword overrides (e.g. ``submitter=`` or
        ``sha256_plaintext_tar=``) to simulate a colliding source.
        """
        identity = {
            "submitter": "alice",
            "issue": 99,
            "submission_repo": "alice/proofs",
            "submission_ref": VALID_REF,
            "sha256_plaintext_tar": VALID_PLAINTEXT_SHA,
        }
        identity.update(identity_overrides)
        body = json.dumps(identity).encode("utf-8")
        return json.dumps({
            "sha": sha,
            "encoding": "base64",
            "content": base64.b64encode(body).decode("ascii"),
        }).encode("utf-8")

    @staticmethod
    def _not_found(url: str) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            url, 404, "Not Found", {}, io.BytesIO(b'{"message":"Not Found"}')
        )

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

            puts: list[dict] = []

            def fake_urlopen(req, timeout=None):
                # Nothing archived yet: every existence GET is a 404.
                if req.get_method() == "GET":
                    raise self._not_found(req.full_url)
                puts.append({
                    "url": req.full_url,
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
            # A brand-new submission creates exactly two files: ciphertext
            # then sidecar. No `sha` is supplied, since neither exists.
            self.assertEqual(len(puts), 2)
            urls = [r["url"] for r in puts]
            self.assertTrue(all("/repos/leanprover/lean-eval-audit/contents/audit/" in u for u in urls))
            self.assertTrue(urls[0].endswith("alice-99-01234567.tar.age"))
            self.assertTrue(urls[1].endswith("alice-99-01234567.json"))
            self.assertNotIn("sha", puts[0]["body"])
            self.assertNotIn("sha", puts[1]["body"])
            uploaded_sidecar = json.loads(
                base64.b64decode(puts[1]["body"]["content"]).decode("utf-8")
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
            self.assertEqual(puts[0]["headers"]["Authorization"], "Bearer xxx")

    def test_push_idempotent_on_reeval_same_source(self) -> None:
        # Re-evaluating an already-archived submission. The sidecar exists for
        # the same identity (submitter, issue, repo, ref) but records a
        # DIFFERENT plaintext digest: gzip/tar packaging is not reproducible,
        # so re-fetching the same git ref yields different tar bytes for
        # identical content. The push must still be a no-op keyed on the
        # immutable ref, and must NOT overwrite the first copy.
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp, b"age-encryption.org/v1\nfreshly-rekeyed")
            sidecar = self._partial_sidecar(tmp)

            calls: list[tuple[str, str]] = []

            def fake_urlopen(req, timeout=None):
                method = req.get_method()
                calls.append((method, req.full_url))
                if method == "GET" and req.full_url.endswith(".json"):
                    # Same identity, different (non-reproducible) tar digest.
                    return io.BytesIO(self._sidecar_meta(sha256_plaintext_tar="b" * 64))
                raise AssertionError(f"unexpected {method} {req.full_url}")

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                ])
            self.assertEqual(rc, 0)
            # Exactly one call: the sidecar existence GET. No PUT.
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "GET")
            self.assertTrue(calls[0][1].endswith(".json"))
            self.assertFalse(any(m == "PUT" for m, _ in calls))

    def test_push_fails_on_source_collision(self) -> None:
        # A *different* source already occupies this audit path: same
        # submitter/issue/ref8 (so the same path) but a different source repo.
        # That is a genuine collision — hard fail, no PUT. A differing
        # plaintext digest alone is NOT a collision (see the idempotent test);
        # only a differing identity field is.
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp)

            def fake_urlopen(req, timeout=None):
                if req.get_method() == "GET" and req.full_url.endswith(".json"):
                    return io.BytesIO(self._sidecar_meta(submission_repo="mallory/proofs"))
                raise AssertionError("must not PUT on a colliding archive")

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("colliding archive", str(ctx.exception).lower())

    def test_push_fails_on_identity_collision_different_submitter(self) -> None:
        # Stale/misplaced sidecar at the same path but a different submitter.
        # A mismatch in any identity field must be flagged as a collision
        # rather than treated as a re-archive no-op.
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp)

            def fake_urlopen(req, timeout=None):
                if req.get_method() == "GET" and req.full_url.endswith(".json"):
                    # Same path, different submitter.
                    return io.BytesIO(self._sidecar_meta(submitter="mallory"))
                raise AssertionError("must not PUT on an identity collision")

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("colliding archive", str(ctx.exception).lower())

    def test_push_fails_fast_on_non_sha_422(self) -> None:
        # A 422 whose body is NOT the "sha wasn't supplied" conflict is a real
        # validation failure (e.g. content too large). It must fail fast with
        # the response body, not be retried as a sha race and reported as
        # exhausted retries.
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp)
            validation_body = json.dumps(
                {"message": "content is too large"}
            ).encode("utf-8")
            puts = 0

            def fake_urlopen(req, timeout=None):
                nonlocal puts
                if req.get_method() == "GET":
                    raise self._not_found(req.full_url)
                puts += 1
                raise urllib.error.HTTPError(
                    req.full_url, 422, "Unprocessable Entity", {},
                    io.BytesIO(validation_body),
                )

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("content is too large", str(ctx.exception))
            self.assertEqual(puts, 1)  # failed immediately, no retries

    def test_push_updates_orphan_ciphertext_when_sidecar_absent(self) -> None:
        # A prior run uploaded the ciphertext then crashed before the
        # sidecar. The rerun finds no sidecar (not a no-op) and must update
        # the orphan ciphertext in place by supplying its sha, rather than
        # failing the unconditional create with `"sha" wasn't supplied`.
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp, b"age-encryption.org/v1\nnewbytes")
            sidecar = self._partial_sidecar(tmp)
            orphan_meta = json.dumps({"sha": "orphansha"}).encode("utf-8")

            calls: list[dict] = []

            def fake_urlopen(req, timeout=None):
                method, url = req.get_method(), req.full_url
                calls.append({
                    "method": method, "url": url,
                    "body": json.loads(req.data.decode("utf-8")) if req.data else None,
                })
                if method == "GET" and url.endswith(".json"):
                    raise self._not_found(url)          # no prior sidecar
                if method == "GET" and url.endswith(".tar.age"):
                    return io.BytesIO(orphan_meta)        # orphan ciphertext present
                return io.BytesIO(b'{"content": {"sha": "x"}}')

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                ])
            self.assertEqual(rc, 0)
            ct_put = next(c for c in calls
                          if c["method"] == "PUT" and c["url"].endswith(".tar.age"))
            # The orphan's sha is supplied so the PUT updates it in place.
            self.assertEqual(ct_put["body"].get("sha"), "orphansha")

    def test_push_omits_verdict_when_summary_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp)
            put_bodies: list[dict] = []

            def fake_urlopen(req, timeout=None):
                if req.get_method() == "GET":
                    raise self._not_found(req.full_url)
                put_bodies.append(json.loads(req.data.decode("utf-8")) if req.data else {})
                return io.BytesIO(b'{"content": {"sha": "x"}}')

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                ])
            self.assertEqual(rc, 0)
            # put_bodies[0] = ciphertext, [1] = sidecar.
            uploaded = json.loads(base64.b64decode(put_bodies[1]["content"]).decode("utf-8"))
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
