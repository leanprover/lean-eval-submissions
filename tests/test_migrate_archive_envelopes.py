from __future__ import annotations

import base64
import copy
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_archive_envelopes as migration
from key_capability_contract import archive_file_key_id, archive_key_id

SOURCE_COMMIT = "a" * 40
LEGACY_PLAINTEXT = b"one exact historical source tar"


def git(root: pathlib.Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def committed_remote(root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, str]:
    remote = root / "remote.git"
    writer = root / "writer"
    selected = root / "selected"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(writer)],
        check=True,
        capture_output=True,
    )
    git(writer, "config", "user.name", "Migration Test")
    git(writer, "config", "user.email", "migration@example.com")
    archive = writer / "nested/archive.tar.age"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"ciphertext")
    (writer / "nested/archive.json").write_text("{}\n", encoding="utf-8")
    git(writer, "add", ".")
    git(writer, "commit", "-m", "source")
    source_commit = git(writer, "rev-parse", "HEAD")
    git(writer, "remote", "add", "origin", str(remote))
    git(writer, "push", "-u", "origin", "main")
    subprocess.run(
        ["git", "clone", "--no-checkout", str(remote), str(selected)],
        check=True,
        capture_output=True,
    )
    git(selected, "checkout", "--detach", source_commit)
    return writer, selected, source_commit


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def envelope(submission_id: str, ciphertext: bytes) -> dict[str, object]:
    recipient = "age1" + "q" * 40
    return {
        "schema_version": 1,
        "submission_id": submission_id,
        "archive_ciphertext_sha256": digest(ciphertext),
        "data_key_id": archive_key_id(submission_id, recipient),
        "age_recipient": recipient,
        "adapter": "aws-kms-v1",
        "wrapped_identity": base64.b64encode(b"provider-wrapped-identity").decode(),
    }


def sidecar(
    schema: int, ciphertext: bytes, *, submission_id: str | None = None
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": schema,
        "submission_repo": "example/private-source",
        "submission_ref": "b" * 40,
        "submission_kind": "github_repo",
        "submission_public": False,
        "submitter": "example",
        "model": "Example Model",
        "size_bytes_plaintext_tar": len(LEGACY_PLAINTEXT),
        "sha256_plaintext_tar": digest(LEGACY_PLAINTEXT),
        "size_bytes_ciphertext": len(ciphertext),
        "sha256_ciphertext": digest(ciphertext),
        "archived_at": "2026-05-25T16:30:00Z",
        "benchmark_commit": "c" * 40,
        "archiver_workflow_run": (
            "https://github.com/leanprover/lean-eval-submissions/actions/runs/123"
        ),
    }
    if schema == 1:
        value["issue"] = 1
    else:
        assert submission_id is not None
        value["submission_id"] = submission_id
    if schema == 3:
        value["key_envelope"] = envelope(submission_id, ciphertext)
    return value


def write_pair(
    root: pathlib.Path, relative: str, value: dict[str, object], ciphertext: bytes
) -> None:
    cipher_path = root.joinpath(*relative.split("/"))
    cipher_path.parent.mkdir(parents=True, exist_ok=True)
    cipher_path.write_bytes(ciphertext)
    sidecar_path = cipher_path.with_suffix("").with_suffix(".json")
    sidecar_path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def named_workflow_steps(workflow: str) -> dict[str, str]:
    """Return complete step blocks keyed by their exact YAML name."""
    marker = "      - "
    starts = [
        offset
        for offset in range(len(workflow))
        if workflow.startswith(marker, offset)
        and (offset == 0 or workflow[offset - 1] == "\n")
    ]
    steps: dict[str, str] = {}
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(workflow)
        block = workflow[start:end]
        first_line = block.splitlines()[0]
        prefix = "      - name: "
        if first_line.startswith(prefix):
            steps[first_line.removeprefix(prefix)] = block
    return steps


class ArchiveEnvelopeMigrationTests(unittest.TestCase):
    def fixture(self, root: pathlib.Path) -> tuple[str, str]:
        schema2_id = "0198abcd-0000-7000-8000-000000000002"
        schema3_id = "0198abcd-0000-7000-8000-000000000003"
        write_pair(
            root,
            "audit/2026/05/example-1-bbbbbbbb.tar.age",
            sidecar(1, b"legacy-one"),
            b"legacy-one",
        )
        write_pair(
            root,
            f"archives/01/{schema2_id}.tar.age",
            sidecar(2, b"legacy-two", submission_id=schema2_id),
            b"legacy-two",
        )
        write_pair(
            root,
            f"archives/01/{schema3_id}.tar.age",
            sidecar(3, b"current-three", submission_id=schema3_id),
            b"current-three",
        )
        return schema2_id, schema3_id

    def test_inventory_is_complete_deterministic_and_uses_stable_uuidv7(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            schema2_id, schema3_id = self.fixture(root)
            first = migration.build_plan(root, SOURCE_COMMIT)
            second = migration.build_plan(root, SOURCE_COMMIT)
            self.assertEqual(first, second)
            self.assertEqual(first["migration_count"], 2)
            self.assertEqual(first["retained_count"], 1)
            self.assertEqual(first["retained"][0]["submission_id"], schema3_id)
            by_schema = {
                entry["source_schema_version"]: entry for entry in first["entries"]
            }
            self.assertEqual(by_schema[2]["submission_id"], schema2_id)
            self.assertRegex(by_schema[1]["submission_id"], migration.UUID7)
            self.assertEqual(
                by_schema[1]["target_path"],
                f"archives/01/{by_schema[1]['submission_id']}.tar.age",
            )
            self.assertRegex(first["inventory_digest"], migration.DIGEST)

    def test_inventory_rejects_changed_ciphertext(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.fixture(root)
            target = root / "audit/2026/05/example-1-bbbbbbbb.tar.age"
            target.write_bytes(b"tampered")
            with self.assertRaisesRegex(migration.MigrationError, "digest disagrees"):
                migration.build_plan(root, SOURCE_COMMIT)

    def test_workflow_is_manual_dry_by_default_and_stages_normal_review_branch(
        self,
    ) -> None:
        workflow = (ROOT / ".github/workflows/migrate-archive-envelopes.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("environment: archive-migration-production", workflow)
        self.assertIn("secrets.AUDIT_MIGRATION_READ_KEY", workflow)
        self.assertIn("secrets.LEGACY_ARCHIVE_IDENTITY", workflow)
        self.assertIn("vars.AWS_WRAP_ROLE_ARN", workflow)
        self.assertIn(
            "arn:aws:iam::161072922960:role/"
            "lean-eval-archive-migration-wrap-production",
            workflow,
        )
        self.assertIn('test "$CONFIRMATION" = stage-envelope-migration', workflow)
        self.assertIn('test "$actual_count" = 439', workflow)
        self.assertIn("--source-root audit", workflow)
        self.assertIn(
            "dfdcbc0da3a3526f8a26e6a69cefa41cbcd92de7608752193b742fcd92b00a67.json",
            workflow,
        )
        self.assertIn("git -C audit switch -c archive-file-key-rewrap-v1", workflow)
        self.assertIn("HEAD:refs/heads/archive-file-key-rewrap-v1", workflow)
        self.assertNotIn("switch --orphan", workflow)
        self.assertNotIn("HEAD:main", workflow)
        self.assertNotIn("--force", workflow)
        self.assertNotIn("upload-artifact", workflow)
        steps = named_workflow_steps(workflow)
        confirmation = workflow.index("Require explicit apply confirmation")
        dependencies = workflow.index("Install hash-locked adapter dependencies")
        aws = workflow.index("Assume only the production Encrypt role")
        migration = workflow.index(
            "Rewrap selected file keys and copy exact ciphertext bytes"
        )
        authority_gone = workflow.index(
            "Prove legacy decrypt material and AWS session are gone before audit write authority"
        )
        writer = workflow.index("Mint audit-repository-only migration writer")
        writer_checkout = workflow.index(
            "Re-check out the exact audit source with branch-staging authority"
        )
        push = workflow.index("Push only an isolated review branch")
        self.assertLess(confirmation, dependencies)
        self.assertLess(dependencies, aws)
        self.assertLess(aws, migration)
        self.assertLess(migration, authority_gone)
        self.assertLess(authority_gone, writer)
        self.assertLess(writer, writer_checkout)
        self.assertLess(writer_checkout, push)
        exact_role = steps["Require the exact dedicated migration role"]
        self.assertIn("AWS_WRAP_ROLE_ARN: ${{ vars.AWS_WRAP_ROLE_ARN }}", exact_role)
        self.assertIn(
            'test "$AWS_WRAP_ROLE_ARN" = \\\n'
            "            arn:aws:iam::161072922960:role/"
            "lean-eval-archive-migration-wrap-production",
            exact_role,
        )
        authority_step = steps[
            "Prove legacy decrypt material and AWS session are gone before audit write authority"
        ]
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN", authority_step)
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST_URL", authority_step)
        self.assertIn('AWS_ACCESS_KEY_ID: ""', authority_step)
        self.assertIn('AWS_SECRET_ACCESS_KEY: ""', authority_step)
        self.assertIn('AWS_SESSION_TOKEN: ""', authority_step)
        self.assertIn(
            "rm -f /dev/shm/lean-eval-legacy-archive-identity", authority_step
        )
        self.assertIn(
            "test ! -e /dev/shm/lean-eval-legacy-archive-identity", authority_step
        )
        self.assertIn('test -z "${AWS_ACCESS_KEY_ID:-}"', authority_step)
        self.assertIn("rm -rf -- audit", authority_step)
        self.assertIn("test ! -e audit || refuse", authority_step)
        self.assertIn("authority boundary refused:", authority_step)
        migration_step = steps[
            "Rewrap selected file keys and copy exact ciphertext bytes"
        ]
        self.assertLess(
            migration_step.index("trap cleanup EXIT"),
            migration_step.index("printf '%s' \"$LEGACY_ARCHIVE_IDENTITY\""),
        )
        self.assertLess(
            migration_step.index("rm -f /dev/shm/lean-eval-legacy-archive-identity"),
            migration_step.index("echo 'AWS_ACCESS_KEY_ID='"),
        )
        writer_checkout_step = steps[
            "Re-check out the exact audit source with branch-staging authority"
        ]
        self.assertIn("persist-credentials: false", writer_checkout_step)
        self.assertNotIn("persist-credentials: true", writer_checkout_step)
        push_step = steps["Push only an isolated review branch"]
        self.assertIn("AUDIT_TOKEN: ${{ steps.audit_token.outputs.token }}", push_step)
        self.assertIn("trap cleanup_writer EXIT", push_step)
        self.assertIn(
            'test -n "$AUDIT_TOKEN"\n'
            "          audit_token=$AUDIT_TOKEN\n"
            "          unset AUDIT_TOKEN\n"
            "          git -C audit switch",
            push_step,
        )
        self.assertLess(
            push_step.index("git -C audit commit"),
            push_step.index("audit_basic_auth=$(printf"),
        )
        self.assertIn("GIT_CONFIG_COUNT=1", push_step)
        self.assertIn(
            "GIT_CONFIG_KEY_0=http.https://github.com/.extraheader",
            push_step,
        )
        self.assertIn(
            "GIT_CONFIG_COUNT=1 \\\n"
            "          GIT_CONFIG_KEY_0=http.https://github.com/.extraheader \\\n"
            '          GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $audit_basic_auth" \\\n'
            "            git -C audit push origin "
            "HEAD:refs/heads/archive-file-key-rewrap-v1",
            push_step,
        )
        self.assertNotIn("export GIT_CONFIG_", push_step)
        self.assertIn("unset AUDIT_TOKEN audit_token audit_basic_auth", push_step)
        self.assertNotIn("git -C audit config --local", push_step)
        self.assertNotIn("http.https://github.com/.extraheader ||", push_step)
        post_authority = workflow[authority_gone:]
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN", post_authority)
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST_URL", post_authority)
        self.assertGreaterEqual(post_authority.count('AWS_ACCESS_KEY_ID: ""'), 3)

    def test_workflow_binds_apply_to_exact_protected_commit_and_preflights_early(
        self,
    ) -> None:
        workflow = (ROOT / ".github/workflows/migrate-archive-envelopes.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("expected_workflow_commit:", workflow)
        self.assertIn('test "$EXPECTED_WORKFLOW_COMMIT" = "$EVENT_SHA"', workflow)
        self.assertIn('test "$EVENT_REF_PROTECTED" = true', workflow)
        self.assertIn("preflight-audit", workflow)
        preflight_step = named_workflow_steps(workflow)[
            "Prove a fresh review branch and non-overlapping audit main"
        ]
        self.assertIn(
            "AUDIT_MIGRATION_READ_KEY: ${{ secrets.AUDIT_MIGRATION_READ_KEY }}",
            preflight_step,
        )
        self.assertIn("StrictHostKeyChecking=yes", preflight_step)
        self.assertIn("trap cleanup_read_key EXIT", preflight_step)
        self.assertLess(
            preflight_step.index("rm -f \"$read_key\" \"$known_hosts\""),
            preflight_step.index("python scripts/migrate_archive_envelopes.py"),
        )
        self.assertIn("cleanup_read_key\n          trap - EXIT", preflight_step)
        self.assertLess(
            workflow.index(
                "Prove a fresh review branch and non-overlapping audit main"
            ),
            workflow.index("Install hash-locked adapter dependencies"),
        )
        self.assertIn("count-archive-ciphertexts", workflow)
        self.assertNotIn(
            "ls-tree -r --name-only ${{ inputs.audit_commit }} '*.tar.age'",
            workflow,
        )
        push_step = named_workflow_steps(workflow)[
            "Push only an isolated review branch"
        ]
        self.assertIn("git -C audit rm --quiet --", push_step)
        self.assertIn("git -C audit commit --quiet", push_step)

    def test_archive_tree_count_finds_nested_ciphertexts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "repository"
            subprocess.run(
                ["git", "init", "-b", "main", str(root)],
                check=True,
                capture_output=True,
            )
            git(root, "config", "user.name", "Migration Test")
            git(root, "config", "user.email", "migration@example.com")
            for relative in ("top.tar.age", "nested/deeper/archive.tar.age"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"ciphertext")
            (root / "nested/not-an-archive.txt").write_text("x\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "nested archives")
            tree = git(root, "rev-parse", "HEAD^{tree}")
            self.assertEqual(migration.count_archive_ciphertexts_in_tree(root, tree), 2)

    def test_audit_preflight_is_retryable_until_review_branch_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer, selected, source_commit = committed_remote(pathlib.Path(directory))
            migration._require_remote_review_branch_absent(selected)
            migration._require_remote_review_branch_absent(selected)
            git(writer, "switch", "-c", "archive-file-key-rewrap-v1")
            git(writer, "push", "origin", "HEAD:archive-file-key-rewrap-v1")
            with self.assertRaisesRegex(
                migration.MigrationError, "review branch already exists"
            ):
                migration._require_remote_review_branch_absent(selected)
            self.assertRegex(source_commit, migration.COMMIT)

    def test_audit_preflight_does_not_treat_remote_failure_as_absence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, selected, _ = committed_remote(pathlib.Path(directory))
            git(selected, "remote", "set-url", "origin", str(selected / "missing.git"))
            with self.assertRaisesRegex(
                migration.MigrationError,
                "could not prove audit migration review branch absent",
            ):
                migration._require_remote_review_branch_absent(selected)

    def test_audit_preflight_rejects_selected_path_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer, selected, source_commit = committed_remote(pathlib.Path(directory))
            origin = git(selected, "remote", "get-url", "origin")
            plan = {
                "entries": [
                    {
                        "source_path": "nested/archive.tar.age",
                        "target_path": (
                            "archives/01/0198abcd-0000-7000-8000-000000000001.tar.age"
                        ),
                    }
                ]
            }
            (writer / "README.md").write_text("unrelated\n", encoding="utf-8")
            git(writer, "add", "README.md")
            git(writer, "commit", "-m", "unrelated drift")
            git(writer, "push", "origin", "main")
            with mock.patch.object(migration, "AUDIT_ORIGINS", {origin}):
                report = migration.preflight_audit_checkout(
                    selected, source_commit, plan
                )
            self.assertEqual(report["overlap_count"], 0)

            (writer / "nested/archive.json").write_text(
                '{"changed":true}\n', encoding="utf-8"
            )
            git(writer, "add", "nested/archive.json")
            git(writer, "commit", "-m", "overlapping drift")
            git(writer, "push", "origin", "main")
            with (
                mock.patch.object(migration, "AUDIT_ORIGINS", {origin}),
                self.assertRaisesRegex(
                    migration.MigrationError,
                    r"changed 1 migration-touched paths \(path-set digest [0-9a-f]{64}\)",
                ) as caught,
            ):
                migration.preflight_audit_checkout(selected, source_commit, plan)
            self.assertNotIn("nested/archive.json", str(caught.exception))

    def test_migrate_one_preserves_ciphertext_and_plaintext_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source"
            output = root / "output"
            self.fixture(source)
            plan = migration.build_plan(source, SOURCE_COMMIT)
            entry = next(
                item for item in plan["entries"] if item["source_schema_version"] == 1
            )
            identity = root / "legacy-identity"
            identity.write_text("not read by the test\n", encoding="utf-8")
            adapter = root / "adapter"
            adapter.write_text("not executed by the test\n", encoding="utf-8")

            helper = root / "age-file-key"
            helper.write_text("not executed by the test\n", encoding="utf-8")
            source_bytes = source.joinpath(
                *entry["source_path"].split("/")
            ).read_bytes()

            def fake_wrap(
                _adapter: pathlib.Path,
                submission_id: str,
                ciphertext_digest: str,
                file_key: bytes,
            ) -> dict[str, object]:
                self.assertEqual(file_key, b"k" * 16)
                return {
                    "schema_version": 2,
                    "submission_id": submission_id,
                    "archive_ciphertext_sha256": ciphertext_digest,
                    "data_key_id": archive_file_key_id(
                        submission_id, ciphertext_digest
                    ),
                    "key_material_type": "age-file-key-v1",
                    "adapter": "aws-kms-v1",
                    "wrapped_key_material": base64.b64encode(
                        b"wrapped-file-key"
                    ).decode(),
                }

            with (
                mock.patch.object(migration, "_require_canonical_selection"),
                mock.patch.object(
                    migration, "_extract_file_key", return_value=b"k" * 16
                ),
                mock.patch.object(migration, "_wrap_file_key", side_effect=fake_wrap),
            ):
                migration.migrate_one(
                    plan,
                    source,
                    output,
                    identity,
                    helper,
                    adapter,
                    entry["source_path"],
                )
            migrated_sidecar = output.joinpath(
                *entry["target_path"].removesuffix(".tar.age").split("/")
            ).with_suffix(".json")
            value = json.loads(migrated_sidecar.read_text(encoding="utf-8"))
            self.assertEqual(value["schema_version"], 3)
            self.assertEqual(value["submission_id"], entry["submission_id"])
            self.assertEqual(value["sha256_plaintext_tar"], digest(LEGACY_PLAINTEXT))
            migrated_ciphertext = migrated_sidecar.with_suffix(".tar.age")
            self.assertEqual(migrated_ciphertext.read_bytes(), source_bytes)
            self.assertEqual(
                value["sha256_ciphertext"], entry["source_ciphertext_sha256"]
            )
            self.assertEqual(value["key_envelope"]["schema_version"], 2)
            self.assertNotIn("issue", value)

    def test_wrap_rejects_plaintext_file_key_passthrough(self) -> None:
        file_key = b"k" * 16
        response = {
            "schema_version": 2,
            "adapter": "aws-kms-v1",
            "wrapped_key_material": base64.b64encode(file_key).decode("ascii"),
        }
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(response).encode("utf-8"),
        )
        with (
            mock.patch.object(migration.subprocess, "run", return_value=completed),
            self.assertRaisesRegex(
                migration.MigrationError, "returned the plaintext age file key"
            ),
        ):
            migration._wrap_file_key(
                ROOT / "adapter",
                "0198abcd-0000-7000-8000-000000000001",
                "d" * 64,
                file_key,
            )

    def test_complete_output_validation_rejects_legacy_or_missing_objects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source"
            output = root / "output"
            self.fixture(source)
            plan = migration.build_plan(source, SOURCE_COMMIT)
            plan["entries"] = [
                entry
                for entry in plan["entries"]
                if entry["source_schema_version"] == 1
            ]
            plan["retained"] = []
            plan["migration_count"] = len(plan["entries"])
            plan["retained_count"] = 0
            output.mkdir()
            with (
                mock.patch.object(migration, "_require_canonical_selection"),
                self.assertRaisesRegex(migration.MigrationError, "incomplete"),
            ):
                migration.validate_output(plan, source, output)
            for entry in plan["entries"]:
                ciphertext = output.joinpath(*entry["target_path"].split("/"))
                ciphertext.parent.mkdir(parents=True, exist_ok=True)
                source_ciphertext = source.joinpath(*entry["source_path"].split("/"))
                content = source_ciphertext.read_bytes()
                ciphertext.write_bytes(content)
                old_sidecar_path = source.joinpath(*entry["source_path"].split("/"))
                old_sidecar_path = old_sidecar_path.with_suffix("").with_suffix(".json")
                old = json.loads(old_sidecar_path.read_text(encoding="utf-8"))
                value = {
                    key: old[key]
                    for key in migration.PRESERVED_FIELDS
                    if key in old
                }
                value.update(
                    schema_version=3,
                    submission_id=entry["submission_id"],
                    sha256_ciphertext=digest(content),
                    size_bytes_ciphertext=len(content),
                    key_envelope={
                        "schema_version": 2,
                        "submission_id": entry["submission_id"],
                        "archive_ciphertext_sha256": digest(content),
                        "data_key_id": archive_file_key_id(
                            entry["submission_id"], digest(content)
                        ),
                        "key_material_type": "age-file-key-v1",
                        "adapter": "aws-kms-v1",
                        "wrapped_key_material": base64.b64encode(
                            b"wrapped-file-key"
                        ).decode(),
                    },
                )
                ciphertext.with_suffix("").with_suffix(".json").write_text(
                    json.dumps(value) + "\n", encoding="utf-8"
                )
            with mock.patch.object(migration, "_require_canonical_selection"):
                report = migration.validate_output(plan, source, output)
            self.assertEqual(report["migration_count"], 1)
            self.assertEqual(report["ciphertext_bytes_changed"], 0)

            sidecar_path = ciphertext.with_suffix("").with_suffix(".json")
            valid_sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            changed = copy.deepcopy(valid_sidecar)
            changed["key_envelope"] = envelope(entry["submission_id"], content)
            sidecar_path.write_text(json.dumps(changed) + "\n", encoding="utf-8")
            with (
                mock.patch.object(migration, "_require_canonical_selection"),
                self.assertRaisesRegex(migration.MigrationError, "file-key envelope"),
            ):
                migration.validate_output(plan, source, output)

            changed = copy.deepcopy(valid_sidecar)
            changed["unexpected_legacy_metadata"] = True
            sidecar_path.write_text(json.dumps(changed) + "\n", encoding="utf-8")
            with (
                mock.patch.object(migration, "_require_canonical_selection"),
                self.assertRaisesRegex(migration.MigrationError, "metadata changed"),
            ):
                migration.validate_output(plan, source, output)

    def test_chunkwise_ciphertext_comparison_detects_equal_size_difference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source.tar.age"
            migrated = root / "migrated.tar.age"
            source.write_bytes(b"abc" * (1024 * 1024))
            migrated.write_bytes(b"abd" + b"abc" * (1024 * 1024 - 1))
            self.assertEqual(source.stat().st_size, migrated.stat().st_size)
            self.assertFalse(migration._files_equal(source, migrated))


if __name__ == "__main__":
    unittest.main()
