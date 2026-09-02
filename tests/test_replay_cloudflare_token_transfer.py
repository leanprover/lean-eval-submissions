import base64
import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import unittest
import zipfile
from unittest import mock

from nacl.public import PrivateKey, SealedBox


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "replay_cloudflare_token_transfer.py"
SPEC = importlib.util.spec_from_file_location("token_transfer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
TRANSFER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRANSFER)


class ReplayCloudflareTokenTransferTests(unittest.TestCase):
    def test_dump_hardening_sets_and_verifies_both_kernel_controls(self):
        prctl = mock.Mock(side_effect=[0, 0])
        libc = mock.Mock(prctl=prctl)
        with mock.patch.object(TRANSFER.sys, "platform", "linux"), mock.patch.object(
            TRANSFER.resource, "setrlimit"
        ) as setrlimit, mock.patch.object(
            TRANSFER.resource, "getrlimit", return_value=(0, 0)
        ), mock.patch.object(TRANSFER.ctypes, "CDLL", return_value=libc):
            TRANSFER.harden_process_against_dumps()
        setrlimit.assert_called_once_with(TRANSFER.resource.RLIMIT_CORE, (0, 0))
        self.assertEqual(
            prctl.call_args_list,
            [
                mock.call(TRANSFER.PR_SET_DUMPABLE, 0, 0, 0, 0),
                mock.call(TRANSFER.PR_GET_DUMPABLE, 0, 0, 0, 0),
            ],
        )

    def test_dump_hardening_fails_closed_on_rlimit_failure(self):
        with mock.patch.object(TRANSFER.sys, "platform", "linux"), mock.patch.object(
            TRANSFER.resource, "setrlimit", side_effect=OSError
        ), mock.patch.object(TRANSFER.ctypes, "CDLL") as load_libc:
            with self.assertRaisesRegex(TRANSFER.TransferError, "disable core dumps"):
                TRANSFER.harden_process_against_dumps()
        load_libc.assert_not_called()

    def test_dump_hardening_fails_closed_on_prctl_failure(self):
        prctl = mock.Mock(side_effect=[-1, 1])
        with mock.patch.object(TRANSFER.sys, "platform", "linux"), mock.patch.object(
            TRANSFER.resource, "setrlimit"
        ), mock.patch.object(
            TRANSFER.resource, "getrlimit", return_value=(0, 0)
        ), mock.patch.object(
            TRANSFER.ctypes, "CDLL", return_value=mock.Mock(prctl=prctl)
        ):
            with self.assertRaisesRegex(TRANSFER.TransferError, "dumpability"):
                TRANSFER.harden_process_against_dumps()

    def test_cli_hardens_before_parsing_arguments(self):
        order = []
        parsed = mock.Mock(function=lambda unused: order.append("function"))
        parser = mock.Mock()
        parser.parse_args.side_effect = lambda: order.append("parse") or parsed
        with mock.patch.object(
            TRANSFER, "harden_process_against_dumps", side_effect=lambda: order.append("harden")
        ), mock.patch.object(TRANSFER, "parser", return_value=parser):
            TRANSFER.main()
        self.assertEqual(order, ["harden", "parse", "function"])

    def test_rendered_workflow_has_one_protected_source_and_only_ciphertext_artifact(self):
        recipient = b"synthetic-public-der"
        operation = "replay-token-transfer-" + "1" * 32
        workflow = TRANSFER.render_workflow(operation, recipient)
        self.assertEqual(workflow.count("environment: cloudflare-production"), 1)
        self.assertEqual(workflow.count("environment: replay-production"), 1)
        self.assertIn("inputs.mode == 'export'", workflow)
        self.assertIn("inputs.mode == 'verify'", workflow)
        self.assertIn('test "$GITHUB_REF" = refs/heads/main', workflow)
        self.assertIn('test "$GITHUB_REF_PROTECTED" = true', workflow)
        self.assertIn("rsa_padding_mode:oaep", workflow)
        self.assertIn("rsa_oaep_md:sha256", workflow)
        self.assertIn("$SOURCE_TOKEN\" | openssl", workflow)
        self.assertNotIn("echo \"$SOURCE_TOKEN", workflow)
        self.assertIn("retention-days: 1", workflow)
        self.assertIn("ciphertext.bin", workflow)
        self.assertIn("manifest.json", workflow)
        self.assertNotIn("actions/checkout", workflow)
        self.assertIn(base64.b64encode(recipient).decode("ascii"), workflow)
        self.assertIn(hashlib.sha256(recipient).hexdigest(), workflow)

    def test_source_run_requires_exact_job_conclusions(self):
        head = "a" * 40
        run = {
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": head,
            "status": "completed",
            "conclusion": "success",
            "path": TRANSFER.WORKFLOW_PATH,
            "repository": {"full_name": TRANSFER.REPOSITORY},
        }
        jobs = {
            "jobs": [
                {"name": "export-encrypted-token", "conclusion": "success"},
                {"name": "verify-target-token", "conclusion": "skipped"},
            ]
        }
        with mock.patch.object(TRANSFER, "gh_json", side_effect=[run, jobs]):
            TRANSFER.require_run(9, head)

    def test_artifact_reader_requires_exact_bounded_members(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("ciphertext.bin", b"ciphertext")
            zipped.writestr("manifest.json", b'{"schema_version":1}')
        with mock.patch.object(
            TRANSFER,
            "gh_json",
            return_value={"artifacts": [{"name": "op", "id": 17, "expired": False}]},
        ), mock.patch.object(
            TRANSFER,
            "run",
            return_value=subprocess.CompletedProcess([], 0, archive.getvalue(), b""),
        ):
            artifact_id, ciphertext, manifest = TRANSFER.download_artifact(5, "op")
        self.assertEqual(artifact_id, 17)
        self.assertEqual(ciphertext, b"ciphertext")
        self.assertEqual(manifest, {"schema_version": 1})

    def test_artifact_reader_rejects_extra_member(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("ciphertext.bin", b"ciphertext")
            zipped.writestr("manifest.json", b"{}")
            zipped.writestr("extra", b"no")
        with mock.patch.object(
            TRANSFER,
            "gh_json",
            return_value={"artifacts": [{"name": "op", "id": 17, "expired": False}]},
        ), mock.patch.object(
            TRANSFER,
            "run",
            return_value=subprocess.CompletedProcess([], 0, archive.getvalue(), b""),
        ):
            with self.assertRaisesRegex(TRANSFER.TransferError, "inventory is not exact"):
                TRANSFER.download_artifact(5, "op")

    def test_target_request_is_sealed_in_process_and_plaintext_is_zeroed(self):
        private_key = PrivateKey.generate()
        plaintext = bytearray(b"A" * 40)
        request = TRANSFER.seal_target_secret_request(
            plaintext, "github-key-id", bytes(private_key.public_key)
        )
        self.assertEqual(plaintext, bytearray(40))
        body = json.loads(request)
        sealed = base64.b64decode(body["encrypted_value"], validate=True)
        self.assertEqual(SealedBox(private_key).decrypt(sealed), b"A" * 40)
        self.assertEqual(body["key_id"], "github-key-id")
        self.assertNotIn(b"A" * 40, request)

    def test_receive_refuses_to_replace_existing_target_secret(self):
        private_key = pathlib.Path("/dev/shm/test-replay-transfer-refusal")
        private_key.write_bytes(b"not-needed")
        private_key.chmod(0o600)
        self.addCleanup(private_key.unlink, missing_ok=True)
        inventories = {
            TRANSFER.SOURCE_ENVIRONMENT: TRANSFER.SOURCE_SECRETS,
            TRANSFER.TARGET_ENVIRONMENT: TRANSFER.TARGET_SECRETS_AFTER,
        }
        arguments = mock.Mock(
            operation="replay-token-transfer-" + "2" * 32,
            private_key=private_key,
            run_id=1,
            expected_head="3" * 40,
        )
        with mock.patch.object(TRANSFER, "require_environment"), mock.patch.object(
            TRANSFER, "secret_names", side_effect=lambda environment: inventories[environment]
        ), mock.patch.object(TRANSFER, "require_run") as require_run:
            with self.assertRaisesRegex(TRANSFER.TransferError, "refusing to replace"):
                TRANSFER.receive(arguments)
        require_run.assert_not_called()

    def test_receive_gives_child_only_github_sealed_ciphertext(self):
        operation = "replay-token-transfer-" + "5" * 32
        head = "6" * 40
        private_key = pathlib.Path("/dev/shm/test-replay-transfer-stream")
        key = TRANSFER.rsa.generate_private_key(public_exponent=65537, key_size=4096)
        private_key.write_bytes(
            key.private_bytes(
                encoding=TRANSFER.serialization.Encoding.PEM,
                format=TRANSFER.serialization.PrivateFormat.PKCS8,
                encryption_algorithm=TRANSFER.serialization.NoEncryption(),
            )
        )
        private_key.chmod(0o600)
        self.addCleanup(private_key.unlink, missing_ok=True)
        recipient = key.public_key().public_bytes(
            encoding=TRANSFER.serialization.Encoding.DER,
            format=TRANSFER.serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        token = b"A" * 40
        github_private_key = PrivateKey.generate()
        ciphertext = key.public_key().encrypt(
            token,
            TRANSFER.padding.OAEP(
                mgf=TRANSFER.padding.MGF1(algorithm=TRANSFER.hashes.SHA256()),
                algorithm=TRANSFER.hashes.SHA256(),
                label=None,
            ),
        )
        manifest = {
            "schema_version": 1,
            "operation": operation,
            "repository": TRANSFER.REPOSITORY,
            "workflow_sha": head,
            "recipient_sha256": hashlib.sha256(recipient).hexdigest(),
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
        }
        inventories = iter(
            [
                TRANSFER.SOURCE_SECRETS,
                TRANSFER.TARGET_SECRETS_BEFORE,
                TRANSFER.TARGET_SECRETS_AFTER,
            ]
        )
        calls = []
        lifecycle = []

        def fake_run(arguments, *, input_bytes=None, capture=True):
            calls.append((arguments, input_bytes, capture))
            if arguments[:4] == ["gh", "api", "--method", "PUT"]:
                lifecycle.append("install")
                return subprocess.CompletedProcess(arguments, 0, b"", b"")
            raise AssertionError(arguments)

        arguments = mock.Mock(
            operation=operation,
            private_key=private_key,
            run_id=7,
            expected_head=head,
        )
        output = io.StringIO()
        with mock.patch.object(TRANSFER, "require_environment"), mock.patch.object(
            TRANSFER, "secret_names", side_effect=lambda unused: next(inventories)
        ), mock.patch.object(
            TRANSFER,
            "gh_json",
            return_value={"commit": {"sha": head}, "protected": True},
        ), mock.patch.object(TRANSFER, "require_run"), mock.patch.object(
            TRANSFER, "download_artifact", return_value=(17, ciphertext, manifest)
        ), mock.patch.object(
            TRANSFER,
            "target_environment_public_key",
            return_value=("github-key-id", bytes(github_private_key.public_key)),
        ), mock.patch.object(TRANSFER, "public_der", return_value=recipient), mock.patch.object(
            TRANSFER, "run", side_effect=fake_run
        ), mock.patch.object(
            TRANSFER,
            "dispatch_target_verification",
            side_effect=lambda unused: lifecycle.append("dispatch") or 19,
        ), mock.patch.object(
            TRANSFER,
            "wait_for_target_verification",
            side_effect=lambda unused_run, unused_head: lifecycle.append("verify"),
        ) as wait_verify, mock.patch.object(
            TRANSFER,
            "delete_artifact",
            side_effect=lambda unused: lifecycle.append("destroy-artifact"),
        ) as delete_artifact, mock.patch(
            "sys.stdout", output
        ):
            TRANSFER.receive(arguments)
        secret_set = [
            call
            for call in calls
            if call[0][:4] == ["gh", "api", "--method", "PUT"]
        ]
        self.assertEqual(len(secret_set), 1)
        request = json.loads(secret_set[0][1])
        sealed = base64.b64decode(request["encrypted_value"], validate=True)
        self.assertEqual(SealedBox(github_private_key).decrypt(sealed), token)
        self.assertNotIn(token, secret_set[0][1])
        self.assertNotIn(token.decode(), " ".join(secret_set[0][0]))
        self.assertNotIn(token.decode(), output.getvalue())
        delete_artifact.assert_called_once_with(17)
        self.assertFalse(private_key.exists())
        wait_verify.assert_called_once_with(19, head)
        self.assertEqual(
            lifecycle,
            ["install", "destroy-artifact", "dispatch", "verify"],
        )

    def test_source_contains_no_persistent_plaintext_token_write(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("write_bytes(token", source)
        self.assertNotIn("write_text(token", source)
        self.assertNotIn("--body", source)
        self.assertNotIn("gh\",\n                \"secret", source)
        self.assertNotIn("input_bytes=token", source)
        self.assertIn("input_bytes=request", source)
        self.assertIn("SealedBox(PublicKey(public_key))", source)
        self.assertIn("/dev/shm", source)


if __name__ == "__main__":
    unittest.main()
