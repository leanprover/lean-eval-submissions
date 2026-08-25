from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest

import jsonschema

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from historical_public_gist_source_adapter import (
    HistoricalPublicGistSourceError,
    build_gist_handoff,
    validate_receipt,
    verify_gist_checkout,
)
from historical_public_runner import (
    canonical_document_bytes,
    load_canonical_json,
    sha256_bytes,
    validate_contract,
    validate_handoff,
)

PLAN_PATH = (
    ROOT
    / "evidence/public-replay/plans"
    / "d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e.json"
)
MATRIX_PATH = ROOT / "configuration/historical-public-replay-profile-matrix-v1.json"
CONTRACT_PATH = ROOT / "configuration/historical-public-runner-v1.json"
RECEIPT_SCHEMA = ROOT / "schemas/historical-public-gist-source-adapter-v1.schema.json"


def git(repository: pathlib.Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def initialize_repository(
    path: pathlib.Path, remote: str, files: dict[str, str]
) -> tuple[str, str]:
    path.mkdir()
    git(path, "init", "--quiet")
    git(path, "config", "user.name", "Historical adapter test")
    git(path, "config", "user.email", "adapter@example.invalid")
    for name, content in files.items():
        destination = path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "fixture")
    commit = git(path, "rev-parse", "HEAD^{commit}")
    tree = git(path, "rev-parse", "HEAD^{tree}")
    git(path, "remote", "add", "origin", remote)
    git(path, "checkout", "--quiet", "--detach", commit)
    return commit, tree


class HistoricalPublicGistSourceAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.owner = "reviewed-owner"
        self.gist_id = "a1b2c3d4"
        self.gist = self.root / "gist"
        self.gist_commit, self.gist_tree = initialize_repository(
            self.gist,
            f"https://gist.github.com/{self.owner}/{self.gist_id}.git",
            {
                "Submission.lean": "theorem example : True := by trivial\n",
                "nested/README.md": "reviewed public source\n",
            },
        )
        self.benchmark = self.root / "benchmark"
        toolchain = "leanprover/lean4:v4.30.0-rc2"
        self.benchmark_commit, self.benchmark_tree = initialize_repository(
            self.benchmark,
            "https://github.com/leanprover/lean-eval.git",
            {"lean-toolchain": toolchain + "\n"},
        )
        self.plan, _ = load_canonical_json(PLAN_PATH, "fixture plan")
        self.matrix, _ = load_canonical_json(MATRIX_PATH, "fixture matrix")
        self.contract, self.contract_raw = load_canonical_json(
            CONTRACT_PATH, "fixture contract"
        )
        validate_contract(self.contract)

        original_request = next(
            request
            for request in self.plan["requests"]
            if request["benchmark"]["commit"]
            == self.matrix["images"][0]["benchmark_commit"]
        )
        self.request = original_request
        self.result = self.request["results"][0]
        original_benchmark_commit = self.request["benchmark"]["commit"]
        entry = next(
            item
            for item in self.matrix["images"]
            if item["benchmark_commit"] == original_benchmark_commit
        )
        self.request["source"] = {
            "kind": "gist",
            "repository": f"{self.owner}/{self.gist_id}",
            "commit": self.gist_commit,
            "visibility": "public",
        }
        toolchain_raw = (toolchain + "\n").encode("ascii")
        self.request["benchmark"].update(
            commit=self.benchmark_commit,
            toolchain=toolchain,
            lean_toolchain_blob_sha256=hashlib.sha256(toolchain_raw).hexdigest(),
        )
        entry.update(
            benchmark_commit=self.benchmark_commit,
            benchmark_tree=self.benchmark_tree,
            toolchain=toolchain,
            lean_toolchain_blob_sha256=hashlib.sha256(toolchain_raw).hexdigest(),
        )
        entry["profile_lock"].update(
            benchmark_commit=self.benchmark_commit,
            toolchain=toolchain,
        )
        self.plan_raw = canonical_document_bytes(self.plan)
        self.matrix["plan_sha256"] = sha256_bytes(self.plan_raw)
        self.matrix_raw = canonical_document_bytes(self.matrix)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def source(self) -> dict[str, object]:
        return self.request["source"]

    def build(self, label: str) -> tuple[dict[str, object], dict[str, object]]:
        return build_gist_handoff(
            plan=self.plan,
            plan_raw=self.plan_raw,
            matrix=self.matrix,
            matrix_raw=self.matrix_raw,
            contract=self.contract,
            contract_raw=self.contract_raw,
            request_id=self.request["request_id"],
            result_id=self.result["result_id"],
            gist_checkout=self.gist,
            benchmark_repository=self.benchmark,
            source_archive=self.root / f"{label}.tar.gz",
        )

    def test_exact_checkout_emits_deterministic_existing_runner_handoff(self) -> None:
        first_handoff, first_receipt = self.build("first")
        second_handoff, second_receipt = self.build("second")
        first_archive = self.root / "first.tar.gz"
        second_archive = self.root / "second.tar.gz"

        self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
        self.assertEqual(first_handoff, second_handoff)
        self.assertEqual(first_receipt, second_receipt)
        self.assertEqual(first_handoff["source"]["tree"], self.gist_tree)
        self.assertEqual(
            first_handoff["source"]["archive_format"],
            "git_archive_tar_gzip_v1",
        )
        self.assertEqual(first_handoff["source"]["archive_member_prefix"], "source")
        self.assertEqual(validate_handoff(first_handoff, self.contract), first_handoff)
        self.assertEqual(validate_receipt(first_receipt), first_receipt)
        jsonschema.Draft202012Validator(
            json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
        ).validate(first_receipt)
        with tarfile.open(first_archive, "r:gz") as archive:
            names = archive.getnames()
        self.assertTrue(names)
        self.assertTrue(
            all(name == "source" or name.startswith("source/") for name in names)
        )
        rendered = json.dumps(first_handoff, sort_keys=True).lower()
        for forbidden in (
            "submission_id",
            "archive_repository",
            "archive_commit",
            "archive_ciphertext",
            "encrypted",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_checkout_identity_is_exact_detached_and_clean(self) -> None:
        self.assertEqual(verify_gist_checkout(self.gist, self.source), self.gist_tree)

        wrong_owner = copy.deepcopy(self.source)
        wrong_owner["repository"] = f"other-owner/{self.gist_id}"
        with self.assertRaisesRegex(HistoricalPublicGistSourceError, "remote identity"):
            verify_gist_checkout(self.gist, wrong_owner)

        git(self.gist, "switch", "--quiet", "-c", "attached")
        with self.assertRaisesRegex(HistoricalPublicGistSourceError, "not detached"):
            verify_gist_checkout(self.gist, self.source)
        git(self.gist, "checkout", "--quiet", "--detach", self.gist_commit)

        untracked = self.gist / "unreviewed.txt"
        untracked.write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(HistoricalPublicGistSourceError, "not clean"):
            verify_gist_checkout(self.gist, self.source)
        untracked.unlink()

        git(self.gist, "remote", "add", "mirror", "https://example.invalid/gist.git")
        with self.assertRaisesRegex(HistoricalPublicGistSourceError, "remote identity"):
            verify_gist_checkout(self.gist, self.source)
        git(self.gist, "remote", "remove", "mirror")

        git(self.gist, "switch", "--quiet", "-c", "drift")
        (self.gist / "Submission.lean").write_text(
            "theorem drift : True := by trivial\n", encoding="utf-8"
        )
        git(self.gist, "add", "Submission.lean")
        git(self.gist, "commit", "--quiet", "-m", "unreviewed drift")
        drift_commit = git(self.gist, "rev-parse", "HEAD^{commit}")
        git(self.gist, "checkout", "--quiet", "--detach", drift_commit)
        with self.assertRaisesRegex(
            HistoricalPublicGistSourceError, "HEAD, commit, or tree"
        ):
            verify_gist_checkout(self.gist, self.source)
        git(self.gist, "checkout", "--quiet", "--detach", self.gist_commit)

        git(
            self.gist,
            "remote",
            "set-url",
            "origin",
            f"https://gist.github.com/{self.gist_id}.git",
        )
        with self.assertRaisesRegex(HistoricalPublicGistSourceError, "remote identity"):
            verify_gist_checkout(self.gist, self.source)

    def test_cli_is_create_only_and_emits_closed_receipt(self) -> None:
        plan_path = self.root / "plan.json"
        matrix_path = self.root / "matrix.json"
        contract_path = self.root / "contract.json"
        plan_path.write_bytes(self.plan_raw)
        matrix_path.write_bytes(self.matrix_raw)
        contract_path.write_bytes(self.contract_raw)
        archive_path = self.root / "cli-source.tar.gz"
        handoff_path = self.root / "cli-handoff.json"
        receipt_path = self.root / "cli-receipt.json"
        command = [
            sys.executable,
            str(ROOT / "scripts/historical_public_gist_source_adapter.py"),
            "prepare",
            "--plan",
            str(plan_path),
            "--profile-matrix",
            str(matrix_path),
            "--contract",
            str(contract_path),
            "--expected-plan-sha256",
            sha256_bytes(self.plan_raw),
            "--expected-profile-matrix-sha256",
            sha256_bytes(self.matrix_raw),
            "--expected-contract-sha256",
            sha256_bytes(self.contract_raw),
            "--request-id",
            self.request["request_id"],
            "--result-id",
            self.result["result_id"],
            "--gist-checkout",
            str(self.gist),
            "--benchmark-repository",
            str(self.benchmark),
            "--source-archive",
            str(archive_path),
            "--handoff",
            str(handoff_path),
            "--output",
            str(receipt_path),
        ]
        first = subprocess.run(command, check=False, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        originals = {
            path: path.read_bytes()
            for path in (archive_path, handoff_path, receipt_path)
        }
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        validate_handoff(handoff, self.contract)
        validate_receipt(receipt)
        for path in (handoff_path, receipt_path):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        second = subprocess.run(command, check=False, capture_output=True, text=True)
        self.assertEqual(second.returncode, 1)
        self.assertEqual(
            {path: path.read_bytes() for path in originals},
            originals,
        )

    def test_closed_receipt_rejects_extra_fields(self) -> None:
        _, receipt = self.build("closed")
        changed = copy.deepcopy(receipt)
        changed["source"]["unexpected"] = True
        with self.assertRaisesRegex(HistoricalPublicGistSourceError, "not closed"):
            validate_receipt(changed)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(
                json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
            ).validate(changed)


if __name__ == "__main__":
    unittest.main()
