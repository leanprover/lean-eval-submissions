import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "custodian_legacy_archive_identity.sh"
EXPECTED_FINGERPRINT = "SHA256:4unwBywJxfq9LsOjygB+/NRHaXdBhvxKP+a3EEpqjoE"


class CustodianLegacyArchiveIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "state"
        self.capture = self.root / "capture"
        self.identity = self.root / "do-not-print-this-path"
        self.identity_value = "matching-test-private-material\n"
        self.identity.write_text(self.identity_value, encoding="utf-8")
        self._write_executable(
            "ssh-keygen",
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ " $* " == *' -y '* ]]; then
              cat "${{@: -1}}"
            elif [[ " $* " == *' -lf '* ]]; then
              value=$(cat)
              if [[ "$value" == matching-test-private-material ]]; then
                printf '2048 {EXPECTED_FINGERPRINT} no-comment (RSA)\\n'
              else
                printf '2048 SHA256:wrong no-comment (RSA)\\n'
              fi
            else
              exit 2
            fi
            """,
        )
        self._write_executable(
            "gh",
            """
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ "$1 $2" == 'auth status' ]]; then
              exit 0
            fi
            if [[ "$1" == api && "$2" == */environments/archive-migration-production ]]; then
              if [[ -s "$FAKE_GH_ENVIRONMENT_FAILURE" ]]; then exit 1; fi
              printf 'true\\n'
              exit 0
            fi
            if [[ "$1" == api && "$2" == */variables/AWS_WRAP_ROLE_ARN ]]; then
              if [[ -s "$FAKE_GH_ROLE_MISMATCH" ]]; then
                printf 'AWS_WRAP_ROLE_ARN=arn:aws:iam::161072922960:role/wrong\\n'
              else
                printf 'AWS_WRAP_ROLE_ARN=arn:aws:iam::161072922960:role/lean-eval-archive-migration-wrap-production\\n'
              fi
              exit 0
            fi
            if [[ " $* " == *' api --paginate --slurp '* &&
                  " $* " == *'/environments/archive-migration-production/secrets?per_page=100'* ]]; then
              if [[ " $* " == *' --jq '* ]]; then exit 2; fi
              if [[ -s "$FAKE_GH_READBACK_FAILURE" && -s "$FAKE_GH_STATE" ]]; then
                exit 1
              fi
              if [[ -s "$FAKE_GH_STATE" ]]; then
                if [[ -s "$FAKE_GH_EXTRA_SECRET" ]]; then
                  printf '[{"secrets":[{"name":"AUDIT_MIGRATION_READ_KEY"},{"name":"EXTRA"},{"name":"LEGACY_ARCHIVE_IDENTITY"}]}]\\n'
                else
                  printf '[{"secrets":[{"name":"AUDIT_MIGRATION_READ_KEY"},{"name":"LEGACY_ARCHIVE_IDENTITY"}]}]\\n'
                fi
              else
                if [[ -s "$FAKE_GH_EXTRA_SECRET" ]]; then
                  printf '[{"secrets":[{"name":"AUDIT_MIGRATION_READ_KEY"},{"name":"EXTRA"}]}]\\n'
                else
                  printf '[{"secrets":[{"name":"AUDIT_MIGRATION_READ_KEY"}]}]\\n'
                fi
              fi
              exit 0
            fi
            if [[ "$1 $2 $3" == 'secret set LEGACY_ARCHIVE_IDENTITY' ]]; then
              cat > "$FAKE_GH_CAPTURE"
              printf 'installed\\n' > "$FAKE_GH_STATE"
              if [[ -s "$FAKE_GH_SET_FAILURE" ]]; then exit 1; fi
              exit 0
            fi
            if [[ "$1 $2 $3" == 'secret delete LEGACY_ARCHIVE_IDENTITY' ]]; then
              : > "$FAKE_GH_STATE"
              exit 0
            fi
            exit 2
            """,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_executable(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _run(
        self,
        mode: str,
        identity: pathlib.Path | None = None,
        *,
        environment_failure: bool = False,
        extra_secret: bool = False,
        readback_failure: bool = False,
        role_mismatch: bool = False,
        set_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment.update(
            {
                "PATH": f"{self.bin}:{environment['PATH']}",
                "FAKE_GH_STATE": str(self.state),
                "FAKE_GH_CAPTURE": str(self.capture),
                "FAKE_GH_ENVIRONMENT_FAILURE": str(self.root / "environment-failure"),
                "FAKE_GH_EXTRA_SECRET": str(self.root / "extra-secret"),
                "FAKE_GH_READBACK_FAILURE": str(self.root / "readback-failure"),
                "FAKE_GH_ROLE_MISMATCH": str(self.root / "role-mismatch"),
                "FAKE_GH_SET_FAILURE": str(self.root / "set-failure"),
            }
        )
        if environment_failure:
            (self.root / "environment-failure").write_text("1", encoding="utf-8")
        if extra_secret:
            (self.root / "extra-secret").write_text("1", encoding="utf-8")
        if readback_failure:
            (self.root / "readback-failure").write_text("1", encoding="utf-8")
        if role_mismatch:
            (self.root / "role-mismatch").write_text("1", encoding="utf-8")
        if set_failure:
            (self.root / "set-failure").write_text("1", encoding="utf-8")
        supplied = f"{identity}\n" if identity is not None else ""
        return subprocess.run(
            [str(SCRIPT), mode],
            input=supplied,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

    def test_install_verifies_and_streams_without_disclosure(self) -> None:
        result = self._run("install", self.identity)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "LEGACY_ARCHIVE_IDENTITY_INSTALLED\n")
        self.assertEqual(self.capture.read_text(encoding="utf-8"), self.identity_value)
        combined = result.stdout + result.stderr
        self.assertNotIn(str(self.identity), combined)
        self.assertNotIn(self.identity_value.strip(), combined)

    def test_mismatch_never_calls_secret_set(self) -> None:
        self.identity.write_text("wrong-test-private-material\n", encoding="utf-8")
        result = self._run("install", self.identity)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fingerprint mismatch", result.stderr)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.capture.exists())
        self.assertNotIn(str(self.identity), result.stdout + result.stderr)

    def test_real_rsa_key_is_read_through_the_bound_descriptor(self) -> None:
        real_ssh_keygen = shutil.which("ssh-keygen")
        self.assertIsNotNone(real_ssh_keygen)
        generated = self.root / "generated-key"
        subprocess.run(
            [
                str(real_ssh_keygen),
                "-q",
                "-t",
                "rsa",
                "-b",
                "2048",
                "-N",
                "",
                "-f",
                str(generated),
            ],
            check=True,
        )
        fake_ssh_keygen = self.bin / "ssh-keygen"
        fake_ssh_keygen.unlink()
        fake_ssh_keygen.symlink_to(str(real_ssh_keygen))

        result = self._run("install", generated)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fingerprint mismatch", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_existing_secret_is_never_replaced(self) -> None:
        self.state.write_text("installed\n", encoding="utf-8")
        result = self._run("install", self.identity)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to replace it", result.stderr)
        self.assertFalse(self.capture.exists())
        combined = result.stdout + result.stderr
        self.assertNotIn(str(self.identity), combined)
        self.assertNotIn(self.identity_value.strip(), combined)

    def test_wrong_migration_role_refuses_install_before_identity_input(self) -> None:
        result = self._run("install", role_mismatch=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not the dedicated migration role", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_unexpected_secret_inventory_refuses_install(self) -> None:
        result = self._run("install", extra_secret=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret inventory is not ready", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_remove_deletes_and_verifies_without_identity_input(self) -> None:
        self.state.write_text("installed\n", encoding="utf-8")
        result = self._run("remove")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "LEGACY_ARCHIVE_IDENTITY_REMOVED\n")
        self.assertEqual(self.state.read_text(encoding="utf-8"), "")

    def test_remove_remains_available_after_environment_policy_drift(self) -> None:
        self.state.write_text("installed\n", encoding="utf-8")
        result = self._run("remove", environment_failure=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "LEGACY_ARCHIVE_IDENTITY_REMOVED\n")

    def test_ambiguous_readback_requires_removal_before_proceeding(self) -> None:
        result = self._run("install", self.identity, readback_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("INSTALLATION_STATE_INDETERMINATE", result.stderr)
        self.assertTrue(self.state.exists())

    def test_nonzero_set_after_creation_requires_removal_before_proceeding(self) -> None:
        result = self._run("install", self.identity, set_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("INSTALLATION_STATE_INDETERMINATE", result.stderr)
        self.assertTrue(self.state.exists())

    def test_script_has_no_temporary_secret_storage_or_path_argument(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("mktemp", source)
        self.assertNotIn("--body", source)
        self.assertIn('<&"$upload_fd"', source)
        self.assertIn('"/proc/self/fd/$upload_fd"', source)
        self.assertIn("--paginate --slurp", source)
        self.assertNotIn("--slurp --jq", source)
        self.assertIn("[[ $# == 1 ]]", source)


if __name__ == "__main__":
    unittest.main()
