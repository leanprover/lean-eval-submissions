from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_archive_envelopes as migration  # noqa: E402
from key_capability_contract import archive_key_id  # noqa: E402


SOURCE_COMMIT = "a" * 40
LEGACY_PLAINTEXT = b"one exact historical source tar"


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


def sidecar(schema: int, ciphertext: bytes, *, submission_id: str | None = None) -> dict[str, object]:
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


def write_pair(root: pathlib.Path, relative: str, value: dict[str, object], ciphertext: bytes) -> None:
    cipher_path = root.joinpath(*relative.split("/"))
    cipher_path.parent.mkdir(parents=True, exist_ok=True)
    cipher_path.write_bytes(ciphertext)
    sidecar_path = cipher_path.with_suffix("").with_suffix(".json")
    sidecar_path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
            by_schema = {entry["source_schema_version"]: entry for entry in first["entries"]}
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

    def test_workflow_is_manual_dry_by_default_and_stages_only_an_orphan_branch(self) -> None:
        workflow = (
            ROOT / ".github/workflows/migrate-archive-envelopes.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("environment: archive-migration-production", workflow)
        self.assertIn("secrets.AUDIT_MIGRATION_READ_KEY", workflow)
        self.assertIn("secrets.LEGACY_ARCHIVE_IDENTITY", workflow)
        self.assertIn("vars.AWS_WRAP_ROLE_ARN", workflow)
        self.assertIn("test \"$CONFIRMATION\" = stage-envelope-migration", workflow)
        self.assertIn("git -C audit switch --orphan archive-envelope-migration-v1", workflow)
        self.assertIn("HEAD:refs/heads/archive-envelope-migration-v1", workflow)
        self.assertNotIn("HEAD:main", workflow)
        self.assertNotIn("--force", workflow)
        self.assertNotIn("upload-artifact", workflow)
        steps = named_workflow_steps(workflow)
        confirmation = workflow.index("Require explicit apply confirmation")
        dependencies = workflow.index("Install hash-locked adapter dependencies")
        aws = workflow.index("Assume only the production Encrypt role")
        migration = workflow.index("Re-encrypt every planned object into a clean tree")
        authority_gone = workflow.index(
            "Prove decrypt and wrap authority is gone before audit write authority"
        )
        writer = workflow.index("Mint audit-repository-only migration writer")
        writer_checkout = workflow.index(
            "Re-check out the exact audit source with branch-staging authority"
        )
        push = workflow.index("Push only an isolated orphan review branch")
        self.assertLess(confirmation, dependencies)
        self.assertLess(dependencies, aws)
        self.assertLess(aws, migration)
        self.assertLess(migration, authority_gone)
        self.assertLess(authority_gone, writer)
        self.assertLess(writer, writer_checkout)
        self.assertLess(writer_checkout, push)
        self.assertIn('test ! -e /dev/shm/lean-eval-legacy-archive-identity', workflow)
        self.assertIn('test -z "${AWS_ACCESS_KEY_ID:-}"', workflow)
        self.assertIn("rm -rf audit\n          test ! -e audit", workflow)
        migration_step = steps["Re-encrypt every planned object into a clean tree"]
        self.assertLess(
            migration_step.index("trap cleanup EXIT"),
            migration_step.index("printf '%s' \"$LEGACY_ARCHIVE_IDENTITY\""),
        )
        self.assertLess(
            migration_step.index(
                "rm -f /dev/shm/lean-eval-legacy-archive-identity"
            ),
            migration_step.index("echo 'AWS_ACCESS_KEY_ID='"),
        )
        writer_checkout_step = steps[
            "Re-check out the exact audit source with branch-staging authority"
        ]
        self.assertIn("persist-credentials: false", writer_checkout_step)
        self.assertNotIn("persist-credentials: true", writer_checkout_step)
        push_step = steps["Push only an isolated orphan review branch"]
        self.assertIn(
            "AUDIT_TOKEN: ${{ steps.audit_token.outputs.token }}", push_step
        )
        self.assertIn("trap cleanup_writer EXIT", push_step)
        self.assertIn(
            "test -n \"$AUDIT_TOKEN\"\n"
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
            "          GIT_CONFIG_VALUE_0=\"AUTHORIZATION: basic $audit_basic_auth\" \\\n"
            "            git -C audit push origin "
            "HEAD:refs/heads/archive-envelope-migration-v1",
            push_step,
        )
        self.assertNotIn("export GIT_CONFIG_", push_step)
        self.assertIn('unset AUDIT_TOKEN audit_token audit_basic_auth', push_step)
        self.assertNotIn("git -C audit config --local", push_step)
        self.assertNotIn("http.https://github.com/.extraheader ||", push_step)
        post_authority = workflow[authority_gone:]
        self.assertGreaterEqual(
            post_authority.count('ACTIONS_ID_TOKEN_REQUEST_TOKEN: ""'), 3
        )
        self.assertGreaterEqual(post_authority.count('AWS_ACCESS_KEY_ID: ""'), 3)

    def test_migrate_one_preserves_plaintext_evidence_and_rebinds_ciphertext(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source"
            output = root / "output"
            self.fixture(source)
            plan = migration.build_plan(source, SOURCE_COMMIT)
            entry = next(item for item in plan["entries"] if item["source_schema_version"] == 1)
            identity = root / "legacy-identity"
            identity.write_text("not read by the test\n", encoding="utf-8")
            adapter = root / "adapter"
            adapter.write_text("not executed by the test\n", encoding="utf-8")

            def fake_decrypt(_identity: pathlib.Path, _ciphertext: pathlib.Path, plaintext: pathlib.Path) -> None:
                plaintext.write_bytes(LEGACY_PLAINTEXT)

            def fake_create(**kwargs: object) -> tuple[pathlib.Path, pathlib.Path]:
                output_dir = pathlib.Path(kwargs["output_dir"])
                submission_id = str(kwargs["submission_id"])
                output_dir.mkdir()
                ciphertext = output_dir / "source.tar.gz.age"
                ciphertext.write_bytes(b"fresh-per-submission-ciphertext")
                envelope_path = output_dir / "archive-key-envelope.json"
                envelope_path.write_text(
                    json.dumps(envelope(submission_id, ciphertext.read_bytes())) + "\n",
                    encoding="utf-8",
                )
                return ciphertext, envelope_path

            with (
                mock.patch.object(migration, "_run_age_decrypt", side_effect=fake_decrypt),
                mock.patch.object(migration, "create_archive_envelope", side_effect=fake_create),
            ):
                migration.migrate_one(
                    plan,
                    source,
                    output,
                    identity,
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
            self.assertNotIn("issue", value)

    def test_complete_output_validation_rejects_legacy_or_missing_objects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source"
            output = root / "output"
            self.fixture(source)
            plan = migration.build_plan(source, SOURCE_COMMIT)
            migration.seed_retained(plan, source, output)
            with self.assertRaisesRegex(migration.MigrationError, "incomplete"):
                migration.validate_output(plan, output)
            for entry in plan["entries"]:
                ciphertext = output.joinpath(*entry["target_path"].split("/"))
                ciphertext.parent.mkdir(parents=True, exist_ok=True)
                content = ("fresh-" + entry["submission_id"]).encode()
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
                    key_envelope=envelope(entry["submission_id"], content),
                )
                ciphertext.with_suffix("").with_suffix(".json").write_text(
                    json.dumps(value) + "\n", encoding="utf-8"
                )
            report = migration.validate_output(plan, output)
            self.assertEqual(report["migration_count"], 2)
            self.assertEqual(report["legacy_ciphertexts_retained"], 0)


if __name__ == "__main__":
    unittest.main()
