from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote_archive_envelope_migration as promotion


def git(root: pathlib.Path, *args: str, input_bytes: bytes | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_bytes,
        check=True,
        capture_output=True,
    )
    return completed.stdout.decode().strip()


def write(path: pathlib.Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


class PromotionFixture:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.remote = root / "audit.git"
        self.writer = root / "writer"
        self.operator = root / "operator"
        subprocess.run(
            ["git", "init", "--bare", str(self.remote)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "init", "-b", "main", str(self.writer)],
            check=True,
            capture_output=True,
        )
        git(self.writer, "config", "user.name", "Promotion Test")
        git(self.writer, "config", "user.email", "promotion@example.com")
        write(self.writer / "legacy/source.tar.age", b"unchanged-ciphertext")
        write(self.writer / "legacy/source.json", b'{"schema_version":1}\n')
        write(self.writer / "README.md", b"source\n")
        git(self.writer, "add", ".")
        git(self.writer, "commit", "-m", "source")
        self.source = git(self.writer, "rev-parse", "HEAD")
        git(self.writer, "remote", "add", "origin", str(self.remote))
        git(self.writer, "push", "-u", "origin", "main")
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")

        git(self.writer, "switch", "-c", "archive-file-key-rewrap-v1")
        git(self.writer, "rm", "legacy/source.tar.age", "legacy/source.json")
        write(self.writer / "archives/01/result.tar.age", b"unchanged-ciphertext")
        write(self.writer / "archives/01/result.json", b'{"schema_version":3}\n')
        git(self.writer, "add", ".")
        git(self.writer, "commit", "-m", "staged migration")
        self.staged = git(self.writer, "rev-parse", "HEAD")
        self.staged_tree = git(self.writer, "rev-parse", "HEAD^{tree}")
        git(self.writer, "push", "origin", "HEAD:archive-file-key-rewrap-v1")

        git(self.writer, "switch", "main")
        write(self.writer / "unrelated.txt", b"concurrent intake\n")
        git(self.writer, "add", ".")
        git(self.writer, "commit", "-m", "unrelated intake")
        self.main = git(self.writer, "rev-parse", "HEAD")
        git(self.writer, "push", "origin", "main")

        subprocess.run(
            ["git", "clone", str(self.remote), str(self.operator)],
            check=True,
            capture_output=True,
        )
        self.patch = subprocess.run(
            [
                "git",
                "-C",
                str(self.writer),
                "diff",
                "--binary",
                "--full-index",
                "--no-renames",
                self.source,
                self.staged,
                "--",
            ],
            check=True,
            capture_output=True,
        ).stdout
        self.patch_sha256 = hashlib.sha256(self.patch).hexdigest()
        self.plan = {
            "source_commit": self.source,
            "inventory_digest": "d" * 64,
            "migration_count": 1,
            "retained_count": 0,
            "retained": [],
            "entries": [
                {
                    "source_path": "legacy/source.tar.age",
                    "target_path": "archives/01/result.tar.age",
                }
            ],
        }

    @contextmanager
    def bindings(self) -> Iterator[None]:
        with (
            mock.patch.object(
                promotion.migration, "AUDIT_ORIGINS", {str(self.remote)}
            ),
            mock.patch.object(
                promotion.migration, "_require_canonical_selection"
            ),
            mock.patch.object(promotion, "SOURCE_COMMIT", self.source),
            mock.patch.object(promotion, "INVENTORY_DIGEST", "d" * 64),
            mock.patch.object(promotion, "MIGRATION_COUNT", 1),
        ):
            yield


class ArchiveEnvelopePromotionTests(unittest.TestCase):
    def derive(self, fixture: PromotionFixture):
        output_patch = fixture.root / "derived.patch"
        with fixture.bindings():
            report = promotion.derive_binding(
                fixture.operator,
                fixture.plan,
                fixture.staged,
                fixture.staged_tree,
                fixture.patch_sha256,
                fixture.main,
                output_patch,
            )
        return report, output_patch.read_bytes()

    def test_derives_exact_patch_and_rebased_result_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(pathlib.Path(directory))
            report, patch = self.derive(fixture)
            self.assertEqual(patch, fixture.patch)
            self.assertEqual(report["staged_commit"], fixture.staged)
            self.assertEqual(report["staged_tree"], fixture.staged_tree)
            self.assertEqual(report["patch_sha256"], fixture.patch_sha256)
            self.assertEqual(report["audit_main_commit"], fixture.main)
            self.assertEqual(report["migration_touched_path_count"], 4)
            self.assertEqual(report["overlap_count"], 0)
            self.assertRegex(report["result_tree"], promotion.COMMIT)

    def test_execution_packet_binds_the_exact_helper(self) -> None:
        helper_digest = hashlib.sha256(
            (ROOT / "scripts/promote_archive_envelope_migration.py").read_bytes()
        ).hexdigest()
        packet = (
            ROOT / "docs/historical-migration-replay-execution-packet.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Migration promotion helper | "
            "`scripts/promote_archive_envelope_migration.py`, SHA-256 "
            f"`{helper_digest}`",
            packet,
        )

    def test_rejects_staged_content_outside_exact_migration_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(pathlib.Path(directory))
            git(fixture.writer, "switch", "archive-file-key-rewrap-v1")
            write(fixture.writer / "unexpected.txt", b"not migration\n")
            git(fixture.writer, "add", "unexpected.txt")
            git(fixture.writer, "commit", "--amend", "--no-edit")
            staged = git(fixture.writer, "rev-parse", "HEAD")
            tree = git(fixture.writer, "rev-parse", "HEAD^{tree}")
            git(
                fixture.writer,
                "push",
                "--force",
                "origin",
                "HEAD:archive-file-key-rewrap-v1",
            )
            patch = subprocess.run(
                [
                    "git",
                    "-C",
                    str(fixture.writer),
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-renames",
                    fixture.source,
                    staged,
                    "--",
                ],
                check=True,
                capture_output=True,
            ).stdout
            with (
                fixture.bindings(),
                self.assertRaisesRegex(
                    promotion.PromotionError, "exactly the migration-touched paths"
                ),
            ):
                promotion.derive_binding(
                    fixture.operator,
                    fixture.plan,
                    staged,
                    tree,
                    hashlib.sha256(patch).hexdigest(),
                    fixture.main,
                    pathlib.Path(directory) / "unexpected.patch",
                )

    def test_rejects_intervening_overlap_without_disclosing_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(pathlib.Path(directory))
            git(fixture.writer, "switch", "main")
            write(fixture.writer / "legacy/source.json", b'{"drift":true}\n')
            git(fixture.writer, "add", "legacy/source.json")
            git(fixture.writer, "commit", "-m", "overlap")
            main = git(fixture.writer, "rev-parse", "HEAD")
            git(fixture.writer, "push", "origin", "main")
            with (
                fixture.bindings(),
                self.assertRaisesRegex(
                    promotion.PromotionError,
                    r"overlaps 1 migration paths \(path-set digest [0-9a-f]{64}\)",
                ) as caught,
            ):
                promotion.derive_binding(
                    fixture.operator,
                    fixture.plan,
                    fixture.staged,
                    fixture.staged_tree,
                    fixture.patch_sha256,
                    main,
                    pathlib.Path(directory) / "overlap.patch",
                )
            self.assertNotIn("legacy/source.json", str(caught.exception))
            self.assertFalse((pathlib.Path(directory) / "overlap.patch").exists())

    def test_prepares_one_clean_candidate_from_an_unchanged_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = PromotionFixture(root)
            report, patch = self.derive(fixture)
            binding = root / "binding.json"
            binding.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            patch_path = root / "migration.patch"
            patch_path.write_bytes(patch)
            output = root / "candidate"
            binding_digest = hashlib.sha256(binding.read_bytes()).hexdigest()
            with fixture.bindings():
                candidate = promotion.prepare_candidate(
                    fixture.operator,
                    fixture.plan,
                    binding,
                    binding_digest,
                    patch_path,
                    output,
                    "2026-08-30T16:00:00Z",
                )
            self.assertEqual(candidate["promotion_parent"], fixture.main)
            self.assertEqual(candidate["promotion_tree"], report["result_tree"])
            self.assertEqual(git(output, "status", "--porcelain"), "")
            self.assertEqual(git(output, "rev-parse", "HEAD"), candidate["promotion_commit"])

    def test_prepare_rejects_a_changed_binding_digest_before_creating_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = PromotionFixture(root)
            report, patch = self.derive(fixture)
            binding = root / "binding.json"
            binding.write_text(json.dumps(report) + "\n", encoding="utf-8")
            patch_path = root / "migration.patch"
            patch_path.write_bytes(patch)
            output = root / "candidate"
            with self.assertRaisesRegex(promotion.PromotionError, "digest changed"):
                promotion.prepare_candidate(
                    fixture.operator,
                    fixture.plan,
                    binding,
                    "0" * 64,
                    patch_path,
                    output,
                    "2026-08-30T16:00:00Z",
                )
            self.assertFalse(output.exists())

    def test_readback_requires_exact_remote_main_commit_and_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = PromotionFixture(root)
            tree = git(fixture.writer, "rev-parse", f"{fixture.main}^{{tree}}")
            candidate = {
                "schema_version": 1,
                "kind": "historical_archive_envelope_promotion_candidate",
                "binding_sha256": "a" * 64,
                "promotion_branch": promotion.PROMOTION_BRANCH,
                "promotion_commit": fixture.main,
                "promotion_parent": fixture.source,
                "promotion_tree": tree,
                "commit_timestamp": "2026-08-30T16:00:00Z",
            }
            candidate_path = root / "candidate.json"
            candidate_path.write_text(
                json.dumps(candidate, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            candidate_digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
            with fixture.bindings():
                report = promotion.readback(
                    fixture.operator,
                    candidate_path,
                    candidate_digest,
                    fixture.main,
                )
            self.assertTrue(report["matches_promoted_result_tree"])
            candidate["promotion_tree"] = "0" * 40
            candidate_path.write_text(
                json.dumps(candidate, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            changed_digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
            with (
                fixture.bindings(),
                self.assertRaisesRegex(promotion.PromotionError, "differs"),
            ):
                promotion.readback(
                    fixture.operator,
                    candidate_path,
                    changed_digest,
                    fixture.main,
                )


if __name__ == "__main__":
    unittest.main()
