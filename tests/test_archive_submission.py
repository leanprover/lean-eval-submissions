"""Unit tests for scripts/archive_submission.py.

The script shells out to `age` for encryption and to native Git for archive
upload. Both are mocked where appropriate so the tests run without network
and without an age binary; an integration test that actually encrypts +
decrypts a fixture lives outside CI (manual decrypt drill, see
docs/audit-archive.md).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import archive_submission as arch  # noqa: E402
from key_capability_contract import archive_key_id  # noqa: E402


VALID_REF = "0123456789abcdef0123456789abcdef01234567"
VALID_PLAINTEXT_SHA = "a" * 64
VALID_SUBMISSION_ID = "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f"
VALID_RECIPIENT = "age1" + "q" * 60


def _make_envelope(
    directory: pathlib.Path,
    ciphertext: pathlib.Path,
    *,
    submission_id: str = VALID_SUBMISSION_ID,
    digest: str | None = None,
) -> pathlib.Path:
    ciphertext_digest = digest or hashlib.sha256(ciphertext.read_bytes()).hexdigest()
    envelope = {
        "schema_version": 1,
        "submission_id": submission_id,
        "archive_ciphertext_sha256": ciphertext_digest,
        "data_key_id": archive_key_id(submission_id, VALID_RECIPIENT),
        "age_recipient": VALID_RECIPIENT,
        "adapter": "aws-kms-v1",
        "wrapped_identity": base64.b64encode(b"wrapped identity").decode("ascii"),
    }
    path = directory / "archive-key-envelope.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    return path


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


def _make_server_metadata(dir: pathlib.Path, **overrides) -> pathlib.Path:
    metadata = {
        "submission_id": VALID_SUBMISSION_ID,
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
    """Write a structurally valid age format-version-1 ciphertext."""
    idx = args.index("--output")
    output_path = pathlib.Path(args[idx + 1])
    output_path.write_bytes(
        b"age-encryption.org/v1\n-> X25519 fakefake\n--- fakemac\nfakebody"
    )
    return mock.Mock(returncode=0, stderr="", stdout="")


class EncryptTests(unittest.TestCase):
    def test_encrypt_server_submission_writes_v2_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            with mock.patch.object(arch.subprocess, "run", side_effect=_fake_age):
                rc = arch.main([
                    "encrypt",
                    "--source-tar", str(_make_source_tar(tmp)),
                    "--metadata", str(_make_server_metadata(tmp)),
                    "--recipients", str(_make_recipients(tmp)),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertEqual(rc, 0)
            sidecar = json.loads((tmp / "out" / "sidecar.partial.json").read_text())
            self.assertEqual(sidecar["schema_version"], 2)
            self.assertEqual(sidecar["submission_id"], VALID_SUBMISSION_ID)
            self.assertNotIn("issue", sidecar)

    def test_encrypt_rejects_non_uuidv7_submission_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "encrypt",
                    "--source-tar", str(_make_source_tar(tmp)),
                    "--metadata", str(_make_server_metadata(tmp, submission_id="not-v7")),
                    "--recipients", str(_make_recipients(tmp)),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertIn("UUIDv7", str(ctx.exception))

    def test_encrypt_rejects_ambiguous_server_and_issue_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "encrypt",
                    "--source-tar", str(_make_source_tar(tmp)),
                    "--metadata", str(_make_server_metadata(tmp, issue_number=99)),
                    "--recipients", str(_make_recipients(tmp)),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertIn("must not contain both", str(ctx.exception))

    def test_encrypt_rejects_boolean_issue_number(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "encrypt",
                    "--source-tar", str(_make_source_tar(tmp)),
                    "--metadata", str(_make_metadata(tmp, issue_number=True)),
                    "--recipients", str(_make_recipients(tmp)),
                    "--output-dir", str(tmp / "out"),
                ])
            self.assertIn("must be int", str(ctx.exception))

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

    def test_encrypt_preserves_publication_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source_tar = _make_source_tar(tmp)
            metadata = _make_metadata(
                tmp,
                solution_publication_status="planned",
                solution_publication_date="2027-01-15",
            )
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
            sidecar = json.loads((out / "sidecar.partial.json").read_text())
            self.assertEqual(sidecar["solution_publication_status"], "planned")
            self.assertEqual(sidecar["solution_publication_date"], "2027-01-15")

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


class PrepareEnvelopeSidecarTests(unittest.TestCase):
    def test_prepares_schema_version_3_sidecar_bound_to_ciphertext(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source = _make_source_tar(tmp)
            metadata = _make_server_metadata(tmp)
            ciphertext = tmp / "source.tar.gz.age"
            ciphertext.write_bytes(b"age-encryption.org/v1\nfixture")
            envelope = _make_envelope(tmp, ciphertext)
            output = tmp / "sidecar.partial.json"

            rc = arch.main([
                "prepare-envelope-sidecar",
                "--source-tar", str(source),
                "--metadata", str(metadata),
                "--ciphertext", str(ciphertext),
                "--envelope", str(envelope),
                "--output", str(output),
            ])

            self.assertEqual(rc, 0)
            sidecar = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(sidecar["schema_version"], 3)
            self.assertEqual(sidecar["submission_id"], VALID_SUBMISSION_ID)
            self.assertEqual(
                sidecar["key_envelope"]["archive_ciphertext_sha256"],
                hashlib.sha256(ciphertext.read_bytes()).hexdigest(),
            )

    def test_rejects_envelope_for_another_submission(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source = _make_source_tar(tmp)
            metadata = _make_server_metadata(tmp)
            ciphertext = tmp / "source.tar.gz.age"
            ciphertext.write_bytes(b"age-encryption.org/v1\nfixture")
            other = "0198c4ee-7d2d-7b35-9d20-cd5db8aa9a6f"
            envelope = _make_envelope(tmp, ciphertext, submission_id=other)

            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "prepare-envelope-sidecar",
                    "--source-tar", str(source),
                    "--metadata", str(metadata),
                    "--ciphertext", str(ciphertext),
                    "--envelope", str(envelope),
                    "--output", str(tmp / "sidecar.partial.json"),
                ])
            self.assertIn("different submission", str(ctx.exception))

    def test_rejects_envelope_with_wrong_ciphertext_digest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            source = _make_source_tar(tmp)
            metadata = _make_server_metadata(tmp)
            ciphertext = tmp / "source.tar.gz.age"
            ciphertext.write_bytes(b"age-encryption.org/v1\nfixture")
            envelope = _make_envelope(tmp, ciphertext, digest="f" * 64)

            with self.assertRaises(SystemExit) as ctx:
                arch.main([
                    "prepare-envelope-sidecar",
                    "--source-tar", str(source),
                    "--metadata", str(metadata),
                    "--ciphertext", str(ciphertext),
                    "--envelope", str(envelope),
                    "--output", str(tmp / "sidecar.partial.json"),
                ])
            self.assertIn("digest does not match", str(ctx.exception))


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

    def _server_sidecar(self, dir: pathlib.Path, **overrides) -> pathlib.Path:
        sidecar = {
            "schema_version": 2,
            "submission_id": VALID_SUBMISSION_ID,
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
            "schema_version": 1,
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
    def _server_sidecar_meta(*, sha: str = "abc123", **overrides) -> bytes:
        sidecar = {
            "schema_version": 2,
            "submission_id": VALID_SUBMISSION_ID,
            "submitter": "alice",
            "submission_repo": "alice/proofs",
            "submission_ref": VALID_REF,
            "submission_kind": "github_repo",
            "submission_public": False,
            "model": "Test Model",
            "size_bytes_plaintext_tar": 1234,
            "sha256_plaintext_tar": VALID_PLAINTEXT_SHA,
            "sha256_ciphertext": hashlib.sha256(
                b"age-encryption.org/v1\nexisting"
            ).hexdigest(),
            "size_bytes_ciphertext": len(b"age-encryption.org/v1\nexisting"),
            "archived_at": "2026-08-20T00:00:00Z",
        }
        sidecar.update(overrides)
        body = json.dumps(sidecar).encode("utf-8")
        return json.dumps({
            "sha": sha,
            "encoding": "base64",
            "content": base64.b64encode(body).decode("ascii"),
        }).encode("utf-8")

    @staticmethod
    def _committed_blob_responses(body: bytes) -> tuple[bytes, bytes]:
        """Contents metadata and Git-blob responses for immutable verification."""
        blob_sha = arch._git_blob_sha(body)
        contents = json.dumps({
            "type": "file",
            "sha": blob_sha,
            "size": len(body),
            # Deliberately include a misleading JSON `content` field. The
            # verifier must resolve and decode the Git blob, not hash this
            # Contents-API envelope as the live workflow did before the fix.
            "encoding": "base64",
            "content": base64.b64encode(b"not the blob").decode("ascii"),
        }).encode("utf-8")
        encoded = base64.b64encode(body).decode("ascii")
        blob = json.dumps({
            "sha": blob_sha,
            "size": len(body),
            "encoding": "base64",
            # GitHub may wrap base64 output; exercise whitespace removal.
            "content": encoded[:12] + "\n" + encoded[12:],
        }).encode("utf-8")
        return contents, blob

    @staticmethod
    def _not_found(url: str) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            url, 404, "Not Found", {}, io.BytesIO(b'{"message":"Not Found"}')
        )

    def test_push_commits_ciphertext_and_sidecar_atomically_from_summary(self) -> None:
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

            pushes: list[dict] = []

            def fake_urlopen(req, timeout=None):
                raise self._not_found(req.full_url)

            def fake_push(**kwargs):
                pushes.append(kwargs)
                return "f" * 40

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen), \
                 mock.patch.object(arch, "_push_git_archive", side_effect=fake_push):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                    "--summary", str(summary),
                    "--benchmark-commit", "f" * 40,
                    "--workflow-run-url", "https://example.invalid/run/1",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(len(pushes), 1)
            pushed = pushes[0]
            self.assertEqual(pushed["ciphertext"], ciphertext)
            self.assertTrue(
                pushed["ciphertext_remote"].endswith(
                    "alice-99-01234567.tar.age"
                )
            )
            self.assertTrue(
                pushed["sidecar_remote"].endswith("alice-99-01234567.json")
            )
            uploaded_sidecar = json.loads(pushed["sidecar_bytes"])
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
            self.assertEqual(pushed["token"], "xxx")

    def test_push_server_submission_emits_state_locator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            locator = tmp / "archive-locator.json"
            completion = tmp / "archive-completion.json"
            committed = b"age-encryption.org/v1\nfake"

            def fake_urlopen(req, timeout=None):
                raise self._not_found(req.full_url)

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen), \
                 mock.patch.object(arch, "_push_git_archive", return_value="f" * 40), \
                 mock.patch.object(arch, "_verify_ciphertext_at_commit"):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(self._ciphertext(tmp)),
                    "--sidecar", str(self._server_sidecar(tmp)),
                    "--locator-output", str(locator),
                    "--completion-output", str(completion),
                ])

            self.assertEqual(rc, 0)
            expected_base = f"archives/01/{VALID_SUBMISSION_ID}"
            value = json.loads(locator.read_text())
            self.assertEqual(value, {
                "schema_version": 1,
                "submission_id": VALID_SUBMISSION_ID,
                "archive_repository": "leanprover/lean-eval-audit",
                "archive_commit": "f" * 40,
                "archive_path": expected_base + ".tar.age",
                "archive_ciphertext_sha256": hashlib.sha256(
                    b"age-encryption.org/v1\nfake"
                ).hexdigest(),
                "encrypted": True,
            })
            completion_value = json.loads(completion.read_text())
            self.assertEqual(completion_value["schema_version"], 1)
            self.assertRegex(
                completion_value["occurred_at"],
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000Z$",
            )
            self.assertEqual(completion_value["locator"], value)

    def test_push_server_submission_requires_locator_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(self._ciphertext(tmp)),
                        "--sidecar", str(self._server_sidecar(tmp)),
                    ])
            self.assertIn(
                "--locator-output and --completion-output are required",
                str(ctx.exception),
            )

    def test_push_rejects_unknown_partial_sidecar_field(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(self._ciphertext(tmp)),
                        "--sidecar", str(self._partial_sidecar(tmp, injected="value")),
                    ])
            self.assertIn("unknown fields", str(ctx.exception))

    def test_push_server_refuses_locator_when_committed_ciphertext_differs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            locator = tmp / "archive-locator.json"
            completion = tmp / "archive-completion.json"

            def fake_urlopen(req, timeout=None):
                raise self._not_found(req.full_url)

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen), \
                 mock.patch.object(arch, "_push_git_archive", return_value="f" * 40), \
                 mock.patch.object(
                     arch,
                     "_verify_ciphertext_at_commit",
                     side_effect=SystemExit(
                         "archive commit does not contain the ciphertext recorded "
                         "by its sidecar"
                     ),
                 ):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(self._ciphertext(tmp)),
                        "--sidecar", str(self._server_sidecar(tmp)),
                        "--locator-output", str(locator),
                        "--completion-output", str(completion),
                    ])
            self.assertIn("does not contain the ciphertext", str(ctx.exception))
            self.assertFalse(locator.exists())

    def test_push_server_idempotent_replay_uses_existing_archive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            locator = tmp / "archive-locator.json"
            completion = tmp / "archive-completion.json"
            calls: list[tuple[str, str]] = []
            existing_ciphertext = b"age-encryption.org/v1\nexisting"

            def fake_urlopen(req, timeout=None):
                method, url = req.get_method(), req.full_url
                calls.append((method, url))
                if "/commits?" in url:
                    return io.BytesIO(json.dumps([{"sha": "e" * 40}]).encode("utf-8"))
                if method == "GET" and url.endswith(".json"):
                    return io.BytesIO(self._server_sidecar_meta())
                raise AssertionError(f"unexpected {method} {url}")

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen), \
                 mock.patch.object(arch, "_verify_ciphertext_at_commit"):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(self._ciphertext(tmp)),
                    "--sidecar", str(self._server_sidecar(tmp)),
                    "--locator-output", str(locator),
                    "--completion-output", str(completion),
                ])

            self.assertEqual(rc, 0)
            self.assertFalse(any(method == "PUT" for method, _ in calls))
            value = json.loads(locator.read_text())
            self.assertEqual(value["archive_commit"], "e" * 40)
            self.assertEqual(
                value["archive_ciphertext_sha256"],
                hashlib.sha256(b"age-encryption.org/v1\nexisting").hexdigest(),
            )
            self.assertEqual(
                json.loads(completion.read_text())["locator"],
                value,
            )

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

    def test_push_uses_native_git_instead_of_contents_api(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp)
            pushes: list[dict] = []
            methods: list[str] = []

            def fake_urlopen(req, timeout=None):
                methods.append(req.get_method())
                raise self._not_found(req.full_url)

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen), \
                 mock.patch.object(
                     arch,
                     "_push_git_archive",
                     side_effect=lambda **kwargs: pushes.append(kwargs) or "f" * 40,
                 ):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(methods, ["GET"])
            self.assertEqual(len(pushes), 1)

    def test_push_commits_ciphertext_and_sidecar_in_one_operation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp, b"age-encryption.org/v1\nnewbytes")
            sidecar = self._partial_sidecar(tmp)
            pushes: list[dict] = []

            def fake_urlopen(req, timeout=None):
                raise self._not_found(req.full_url)

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen), \
                 mock.patch.object(
                     arch,
                     "_push_git_archive",
                     side_effect=lambda **kwargs: pushes.append(kwargs) or "f" * 40,
                 ):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(len(pushes), 1)
            self.assertEqual(pushes[0]["ciphertext"], ciphertext)
            self.assertTrue(pushes[0]["sidecar_bytes"].endswith(b"\n"))

    def test_push_omits_verdict_when_summary_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp)
            pushes: list[dict] = []

            def fake_urlopen(req, timeout=None):
                raise self._not_found(req.full_url)

            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}, clear=False), \
                 mock.patch.object(arch.urllib.request, "urlopen", side_effect=fake_urlopen), \
                 mock.patch.object(
                     arch,
                     "_push_git_archive",
                     side_effect=lambda **kwargs: pushes.append(kwargs) or "f" * 40,
                 ):
                rc = arch.main([
                    "push",
                    "--ciphertext", str(ciphertext),
                    "--sidecar", str(sidecar),
                ])
            self.assertEqual(rc, 0)
            uploaded = json.loads(pushes[0]["sidecar_bytes"])
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

    def test_push_rejects_boolean_sidecar_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ciphertext = self._ciphertext(tmp)
            sidecar = self._partial_sidecar(tmp, schema_version=True)
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(ciphertext),
                        "--sidecar", str(sidecar),
                    ])
            self.assertIn("schema_version", str(ctx.exception))

    def test_push_rejects_malformed_audit_repository(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            with mock.patch.dict(arch.os.environ, {"ARCHIVER_TOKEN": "xxx"}):
                with self.assertRaises(SystemExit) as ctx:
                    arch.main([
                        "push",
                        "--ciphertext", str(self._ciphertext(tmp)),
                        "--sidecar", str(self._partial_sidecar(tmp)),
                        "--audit-repo", "owner/repo/extra",
                    ])
            self.assertIn("--audit-repo", str(ctx.exception))

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


class NativeGitArchiveTests(unittest.TestCase):
    @staticmethod
    def _bare_audit_repo(root: pathlib.Path) -> pathlib.Path:
        seed = root / "seed"
        bare = root / "audit.git"
        subprocess.run(["git", "init", "--quiet", str(seed)], check=True)
        subprocess.run(
            ["git", "-C", str(seed), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(seed), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        (seed / "README.md").write_text("audit\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(seed), "add", "README.md"], check=True)
        subprocess.run(
            ["git", "-C", str(seed), "commit", "--quiet", "-m", "initial"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(seed), "branch", "-M", "main"],
            check=True,
        )
        subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)
        subprocess.run(
            ["git", "-C", str(seed), "remote", "add", "origin", str(bare)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(seed), "push", "--quiet", "origin", "main"],
            check=True,
        )
        subprocess.run(
            ["git", "--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
        )
        return bare

    def test_native_git_push_is_atomic_and_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            bare = self._bare_audit_repo(root)
            ciphertext = root / "source.tar.gz.age"
            ciphertext_bytes = b"age-encryption.org/v1\nnative-git-fixture"
            ciphertext.write_bytes(ciphertext_bytes)
            sidecar_bytes = b'{"schema_version": 2}\n'
            ciphertext_remote = "archives/01/submission.tar.age"
            sidecar_remote = "archives/01/submission.json"

            with mock.patch.object(arch, "_audit_git_url", return_value=str(bare)):
                commit = arch._push_git_archive(
                    audit_repo="leanprover/lean-eval-audit",
                    token="test-token",
                    ciphertext=ciphertext,
                    sidecar_bytes=sidecar_bytes,
                    ciphertext_remote=ciphertext_remote,
                    sidecar_remote=sidecar_remote,
                    sidecar={"submission_id": VALID_SUBMISSION_ID},
                    message="archive: test",
                )
                arch._verify_ciphertext_at_commit(
                    audit_repo="leanprover/lean-eval-audit",
                    token="test-token",
                    archive_commit=commit,
                    archive_path=ciphertext_remote,
                    expected_sha256=hashlib.sha256(ciphertext_bytes).hexdigest(),
                )

            archived_ciphertext = subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(bare),
                    "show",
                    f"{commit}:{ciphertext_remote}",
                ],
                check=True,
                capture_output=True,
            ).stdout
            archived_sidecar = subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(bare),
                    "show",
                    f"{commit}:{sidecar_remote}",
                ],
                check=True,
                capture_output=True,
            ).stdout
            self.assertEqual(archived_ciphertext, ciphertext_bytes)
            self.assertEqual(archived_sidecar, sidecar_bytes)


class GitBlobShaTests(unittest.TestCase):
    def test_archive_locator_schema_is_strict_v1(self) -> None:
        schema = json.loads(
            (REPO_ROOT / "schemas" / "archive-locator-v1.schema.json").read_text()
        )
        self.assertEqual(schema["properties"]["schema_version"], {"const": 1})
        self.assertFalse(schema["additionalProperties"])

    def test_archive_completion_schema_wraps_locator_strictly(self) -> None:
        schema = json.loads(
            (REPO_ROOT / "schemas" / "archive-completion-v1.schema.json").read_text()
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["locator"]["$ref"], "archive-locator-v1.schema.json")
        self.assertEqual(set(schema["required"]), set(schema["properties"]))

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
