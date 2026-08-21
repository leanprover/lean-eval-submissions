from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest

from scripts.archive_envelope import EnvelopeError, create_archive_envelope
from scripts.key_capability_contract import (
    envelope_binding_context,
    validate_envelope,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]
SUBMISSION_ID = "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f"
RECIPIENT = "age1" + "q" * 60
IDENTITY = b"# public key: test\nAGE-SECRET-KEY-PQ-1TESTTESTTEST\n"


def _write_executable(path: pathlib.Path, source: str) -> pathlib.Path:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(0o700)
    return path


class ArchiveEnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.stack.name)
        self.source = self.root / "source.tar.gz"
        self.source.write_bytes(b"deterministic tar fixture")
        self.keygen_log = self.root / "keygen-log.jsonl"
        self.age_log = self.root / "age-log.json"
        self.adapter_log = self.root / "adapter-log.json"

    def tearDown(self) -> None:
        self.stack.cleanup()

    def _keygen(
        self,
        *,
        identity: bytes = IDENTITY,
        mode: int = 0o600,
        recipient: str = RECIPIENT,
    ) -> pathlib.Path:
        return _write_executable(
            self.root / "fake-age-keygen",
            f"""
            #!/usr/bin/env python3
            import json
            import os
            import pathlib
            import sys

            args = sys.argv[1:]
            with pathlib.Path({str(self.keygen_log)!r}).open("a", encoding="utf-8") as log:
                log.write(json.dumps(args) + "\\n")
            if "-y" in args:
                print({recipient!r})
                raise SystemExit(0)
            output = pathlib.Path(args[args.index("--output") + 1])
            output.write_bytes({identity!r})
            output.chmod({mode})
            """,
        )

    def _age(self, *, valid_header: bool = True) -> pathlib.Path:
        header = b"age-encryption.org/v1\n" if valid_header else b"not-age\n"
        return _write_executable(
            self.root / "fake-age",
            f"""
            #!/usr/bin/env python3
            import json
            import os
            import pathlib
            import sys

            args = sys.argv[1:]
            pathlib.Path({str(self.age_log)!r}).write_text(json.dumps({{
                "args": args,
                "has_aws_secret": "AWS_SECRET_ACCESS_KEY" in os.environ,
                "has_github_token": "GITHUB_TOKEN" in os.environ,
            }}), encoding="utf-8")
            output = pathlib.Path(args[args.index("--output") + 1])
            source = pathlib.Path(args[-1])
            output.write_bytes({header!r} + source.read_bytes())
            """,
        )

    def _adapter(self, mode: str = "success") -> pathlib.Path:
        return _write_executable(
            self.root / f"adapter-{mode}",
            f"""
            #!/usr/bin/env python3
            import base64
            import json
            import pathlib
            import sys

            request = json.load(sys.stdin)
            pathlib.Path({str(self.adapter_log)!r}).write_text(
                json.dumps(request, sort_keys=True), encoding="utf-8"
            )
            identity = base64.b64decode(request["plaintext_identity_base64"])
            mode = {mode!r}
            if mode == "fail-leaking-stderr":
                sys.stderr.buffer.write(identity)
                raise SystemExit(9)
            if mode == "extra-field":
                response = {{
                    "schema_version": 1,
                    "adapter": request["adapter"],
                    "wrapped_identity": base64.b64encode(b"wrapped:" + identity).decode(),
                    "provider": "forbidden",
                }}
            elif mode == "plaintext":
                response = {{
                    "schema_version": 1,
                    "adapter": request["adapter"],
                    "wrapped_identity": request["plaintext_identity_base64"],
                }}
            else:
                response = {{
                    "schema_version": 1,
                    "adapter": request["adapter"],
                    "wrapped_identity": base64.b64encode(b"wrapped:" + identity).decode(),
                }}
            print(json.dumps(response))
            """,
        )

    def _create(
        self,
        *,
        output_name: str = "out",
        keygen: pathlib.Path | None = None,
        age: pathlib.Path | None = None,
        adapter: pathlib.Path | None = None,
        post_quantum: bool = True,
    ) -> tuple[pathlib.Path, pathlib.Path]:
        return create_archive_envelope(
            source_tar=self.source,
            submission_id=SUBMISSION_ID,
            output_dir=self.root / output_name,
            adapter_executable=adapter or self._adapter(),
            adapter_name="aws-kms-v1",
            age_executable=str(age or self._age()),
            age_keygen_executable=str(keygen or self._keygen()),
            post_quantum=post_quantum,
        )

    def test_creates_bound_archive_and_envelope_without_exporting_identity(self) -> None:
        old_aws = os.environ.get("AWS_SECRET_ACCESS_KEY")
        old_github = os.environ.get("GITHUB_TOKEN")
        os.environ["AWS_SECRET_ACCESS_KEY"] = "must-not-reach-age"
        os.environ["GITHUB_TOKEN"] = "must-not-reach-age"
        try:
            ciphertext_path, envelope_path = self._create()
        finally:
            if old_aws is None:
                os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
            else:
                os.environ["AWS_SECRET_ACCESS_KEY"] = old_aws
            if old_github is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = old_github

        self.assertEqual(
            ciphertext_path.read_bytes(),
            b"age-encryption.org/v1\n" + self.source.read_bytes(),
        )
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        self.assertEqual(validate_envelope(envelope), envelope)
        self.assertEqual(
            envelope["archive_ciphertext_sha256"],
            hashlib.sha256(ciphertext_path.read_bytes()).hexdigest(),
        )
        request = json.loads(self.adapter_log.read_text(encoding="utf-8"))
        self.assertEqual(
            set(request),
            {"schema_version", "operation", "adapter", "context", "plaintext_identity_base64"},
        )
        self.assertEqual(base64.b64decode(request["plaintext_identity_base64"]), IDENTITY)
        self.assertEqual(
            request["context"],
            envelope_binding_context(
                SUBMISSION_ID,
                envelope["archive_ciphertext_sha256"],
                envelope["data_key_id"],
                envelope["age_recipient"],
            ),
        )
        self.assertEqual({path.name for path in ciphertext_path.parent.iterdir()}, {
            "source.tar.gz.age",
            "archive-key-envelope.json",
        })
        for artifact in ciphertext_path.parent.iterdir():
            self.assertNotIn(IDENTITY, artifact.read_bytes())

        keygen_calls = [json.loads(line) for line in self.keygen_log.read_text().splitlines()]
        self.assertIn("-pq", keygen_calls[0])
        self.assertEqual(keygen_calls[1][0], "-y")
        tool_log = json.loads(self.age_log.read_text())
        self.assertFalse(tool_log["has_aws_secret"])
        self.assertFalse(tool_log["has_github_token"])

    def test_classic_key_mode_omits_post_quantum_flag(self) -> None:
        self._create(post_quantum=False)
        first_call = json.loads(self.keygen_log.read_text().splitlines()[0])
        self.assertNotIn("-pq", first_call)

    @unittest.skipUnless(
        shutil.which("age") and shutil.which("age-keygen"),
        "age tools are not installed",
    )
    def test_real_age_post_quantum_ciphertext(self) -> None:
        ciphertext_path, envelope_path = create_archive_envelope(
            source_tar=self.source,
            submission_id=SUBMISSION_ID,
            output_dir=self.root / "real-age",
            adapter_executable=self._adapter(),
            adapter_name="aws-kms-v1",
        )
        self.assertTrue(ciphertext_path.read_bytes().startswith(b"age-encryption.org/v1\n"))
        self.assertEqual(
            validate_envelope(json.loads(envelope_path.read_text(encoding="utf-8")))[
                "submission_id"
            ],
            SUBMISSION_ID,
        )

    def test_adapter_diagnostics_cannot_echo_identity(self) -> None:
        with self.assertRaisesRegex(EnvelopeError, "failed with exit code 9") as raised:
            self._create(adapter=self._adapter("fail-leaking-stderr"))
        self.assertNotIn(IDENTITY.decode("ascii"), str(raised.exception))
        self.assertFalse((self.root / "out").exists())

    def test_adapter_cannot_add_fields_or_return_plaintext(self) -> None:
        for mode, message in (
            ("extra-field", "fields are not canonical"),
            ("plaintext", "returned the plaintext identity"),
        ):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(EnvelopeError, message):
                    self._create(output_name=f"out-{mode}", adapter=self._adapter(mode))
                self.assertFalse((self.root / f"out-{mode}").exists())

    def test_invalid_identity_never_publishes_partial_output(self) -> None:
        cases = (
            (IDENTITY, 0o644, "permissions"),
            (b"AGE-SECRET-KEY-1ONE\nAGE-SECRET-KEY-1TWO\n", 0o600, "exactly one"),
            (b"AGE-SECRET-KEY-1" + b"A" * 5000, 0o600, "exceeds"),
        )
        for index, (identity, mode, message) in enumerate(cases):
            with self.subTest(message=message):
                with self.assertRaisesRegex(EnvelopeError, message):
                    self._create(
                        output_name=f"invalid-{index}",
                        keygen=self._keygen(identity=identity, mode=mode),
                    )
                self.assertFalse((self.root / f"invalid-{index}").exists())

    def test_invalid_inputs_run_before_secret_generation(self) -> None:
        adapter = self._adapter()
        with self.assertRaisesRegex(ValueError, "submission_id"):
            create_archive_envelope(
                source_tar=self.source,
                submission_id="not-a-uuid",
                output_dir=self.root / "out",
                adapter_executable=adapter,
                adapter_name="aws-kms-v1",
                age_executable=str(self._age()),
                age_keygen_executable=str(self._keygen()),
            )
        self.assertFalse(self.keygen_log.exists())
        self.assertFalse((self.root / "out").exists())

    def test_refuses_existing_or_symlink_output(self) -> None:
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaisesRegex(EnvelopeError, "must not already exist"):
            self._create(output_name="existing")
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(EnvelopeError, "must not already exist"):
            self._create(output_name="link")

    def test_cli_reports_only_artifact_paths(self) -> None:
        output = self.root / "cli-out"
        process = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts" / "archive_envelope.py"),
                "--source-tar",
                str(self.source),
                "--submission-id",
                SUBMISSION_ID,
                "--output-dir",
                str(output),
                "--adapter-executable",
                str(self._adapter()),
                "--adapter-name",
                "aws-kms-v1",
                "--age-executable",
                str(self._age()),
                "--age-keygen-executable",
                str(self._keygen()),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(set(result), {"ciphertext", "envelope"})
        self.assertNotIn("SECRET", process.stdout)
        self.assertNotIn("identity", process.stdout.lower())


if __name__ == "__main__":
    unittest.main()
