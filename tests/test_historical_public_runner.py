from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from historical_public_runner import (
    HistoricalPublicRunnerError,
    _git_source_tree_oid,
    _load_authoritative_runtime,
    _validate_handoff_json_schema,
    _write_git_archive,
    build_handoff,
    canonical_document_bytes,
    extract_source_archive,
    load_canonical_json,
    sha256_bytes,
    sha256_file,
    validate_contract,
    validate_handoff,
    validate_historical_verdict,
    validate_runner_inputs,
)

PLAN_PATH = (
    ROOT
    / "evidence/public-replay/plans"
    / "d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e.json"
)
MATRIX_PATH = ROOT / "configuration/historical-public-replay-profile-matrix-v1.json"
CONTRACT_PATH = ROOT / "configuration/historical-public-runner-v1.json"
HANDOFF_SCHEMA = ROOT / "schemas/historical-public-runner-handoff-v1.schema.json"
VERDICT_SCHEMA = ROOT / "schemas/historical-public-runner-verdict-v1.schema.json"
SOURCE_CONTENT = b"theorem example : True := by trivial\n"


def load(path: pathlib.Path) -> tuple[dict[str, object], bytes]:
    return load_canonical_json(path, path.name)


def write_canonical(path: pathlib.Path, value: dict[str, object]) -> None:
    path.write_bytes(canonical_document_bytes(value))


def write_source_archive(path: pathlib.Path, *, symlink: bool = False) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        root = tarfile.TarInfo("source")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        archive.addfile(root)
        member = tarfile.TarInfo("source/Submission.lean")
        if symlink:
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
            archive.addfile(member)
        else:
            member.size = len(SOURCE_CONTENT)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(SOURCE_CONTENT))
    path.write_bytes(gzip.compress(raw.getvalue(), mtime=0))


def fixture_tree_oid() -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(b"blob " + str(len(SOURCE_CONTENT)).encode("ascii") + b"\0")
    digest.update(SOURCE_CONTENT)
    return _git_source_tree_oid({("Submission.lean",): ("100644", digest.digest())})


class HistoricalPublicRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan, self.plan_raw = load(PLAN_PATH)
        self.matrix, self.matrix_raw = load(MATRIX_PATH)
        self.contract, self.contract_raw = load(CONTRACT_PATH)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        # This entry's lean-toolchain object includes the canonical final newline,
        # allowing the baked-marker boundary to be exercised without a network fetch.
        self.entry = next(
            entry
            for entry in self.matrix["images"]
            if hashlib.sha256((entry["toolchain"] + "\n").encode()).hexdigest()
            == entry["lean_toolchain_blob_sha256"]
        )
        self.request = next(
            request
            for request in self.plan["requests"]
            if request["benchmark"]["commit"] == self.entry["benchmark_commit"]
        )
        self.result = self.request["results"][0]
        self.archive = self.root / "historical-public-source.tar.gz"
        write_source_archive(self.archive)
        self.handoff = self.make_handoff()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_handoff(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "historical_public_runner_handoff",
            "contract": "historical_public_runner_v1",
            "contract_sha256": sha256_bytes(self.contract_raw),
            "plan_sha256": sha256_bytes(self.plan_raw),
            "profile_matrix_sha256": sha256_bytes(self.matrix_raw),
            "request_id": self.request["request_id"],
            "source": {
                "repository": self.request["source"]["repository"],
                "commit": self.request["source"]["commit"],
                "tree": fixture_tree_oid(),
                "visibility": "public",
                "archive_format": "git_archive_tar_gzip_v1",
                "archive_member_prefix": "source",
                "archive_sha256": sha256_file(self.archive),
                "archive_size_bytes": self.archive.stat().st_size,
            },
            "benchmark": {
                "repository": "leanprover/lean-eval",
                "commit": self.entry["benchmark_commit"],
                "tree": self.entry["benchmark_tree"],
                "toolchain": self.entry["toolchain"],
                "lean_toolchain_blob_sha256": self.entry["lean_toolchain_blob_sha256"],
            },
            "result": {
                "result_id": self.result["result_id"],
                "problem_id": self.result["problem_id"],
                "statement_revision": self.result["statement_revision"],
                "results_repository": self.result["results_repository"],
                "results_commit": self.result["results_commit"],
                "result_tree_digest": self.result["result_tree_digest"],
            },
            "profile": {
                "matrix_entry_sha256": sha256_bytes(
                    canonical_document_bytes(self.entry)
                ),
                "qualification_status": "unqualified",
                "profile_lock": self.entry["profile_lock"],
            },
            "checker": "nanoda",
            "network": self.contract["network"],
            "untrusted_environment": {},
        }

    def baked_benchmark(self) -> pathlib.Path:
        benchmark = self.root / "benchmark"
        benchmark.mkdir(exist_ok=True)
        (benchmark / ".lean-eval-commit").write_text(
            self.entry["benchmark_commit"] + "\n", encoding="ascii"
        )
        (benchmark / ".lean-eval-tree").write_text(
            self.entry["benchmark_tree"] + "\n", encoding="ascii"
        )
        (benchmark / "lean-toolchain").write_text(
            self.entry["toolchain"] + "\n", encoding="ascii"
        )
        return benchmark

    def runner_paths(self) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        handoff_path = self.root / "historical-public-request.json"
        matrix_path = self.root / "matrix.json"
        contract_path = self.root / "contract.json"
        write_canonical(handoff_path, self.handoff)
        write_canonical(matrix_path, self.matrix)
        write_canonical(contract_path, self.contract)
        return handoff_path, matrix_path, contract_path

    def test_contract_and_handoff_are_closed_and_do_not_claim_qualification(
        self,
    ) -> None:
        self.assertIs(validate_contract(self.contract), self.contract)
        self.assertIs(validate_handoff(self.handoff, self.contract), self.handoff)
        serialized = json.dumps(self.handoff, sort_keys=True).lower()
        for forbidden in (
            "submission_id",
            "archive_repository",
            "archive_commit",
            "archive_path",
            "archive_ciphertext",
            "private_key",
            "encrypted",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(self.handoff["profile"]["qualification_status"], "unqualified")

    def test_controller_binds_exact_plan_matrix_git_trees_and_source_archive(
        self,
    ) -> None:
        output_archive = self.root / "controller-source.tar.gz"

        def git_output(
            _repository: pathlib.Path, *arguments: str, **_kwargs: object
        ) -> bytes:
            if arguments[:2] == (
                "show",
                f"{self.entry['benchmark_commit']}:lean-toolchain",
            ):
                return (self.entry["toolchain"] + "\n").encode()
            raise AssertionError(arguments)

        def write_archive(
            _repository: pathlib.Path,
            _commit: str,
            destination: pathlib.Path,
            _maximum: int,
            _maximum_tar: int,
        ) -> tuple[int, str]:
            shutil.copyfile(self.archive, destination)
            return destination.stat().st_size, sha256_file(destination)

        with (
            mock.patch(
                "historical_public_runner._require_checkout_identity",
                side_effect=[fixture_tree_oid(), self.entry["benchmark_tree"]],
            ),
            mock.patch("historical_public_runner._git", side_effect=git_output),
            mock.patch(
                "historical_public_runner._write_git_archive",
                side_effect=write_archive,
            ),
        ):
            handoff = build_handoff(
                plan=self.plan,
                plan_raw=self.plan_raw,
                matrix=self.matrix,
                matrix_raw=self.matrix_raw,
                contract=self.contract,
                contract_raw=self.contract_raw,
                request_id=self.request["request_id"],
                result_id=self.result["result_id"],
                source_repository=self.root / "source",
                benchmark_repository=self.root / "benchmark-fetch",
                source_archive=output_archive,
            )
        self.assertEqual(handoff, self.handoff)
        self.assertEqual(handoff["benchmark"]["tree"], self.entry["benchmark_tree"])
        self.assertEqual(
            handoff["profile_matrix_sha256"], sha256_bytes(self.matrix_raw)
        )

    def test_controller_rejects_matrix_plan_tree_and_problem_drift(self) -> None:
        changes = {
            "plan digest": lambda matrix: matrix.__setitem__("plan_sha256", "0" * 64),
            "qualified claim": lambda matrix: matrix.__setitem__(
                "qualification_status", "qualified"
            ),
            "problem coverage": lambda matrix: next(
                entry
                for entry in matrix["images"]
                if entry["benchmark_commit"] == self.entry["benchmark_commit"]
            )["problem_ids"].remove(self.result["problem_id"]),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                matrix = copy.deepcopy(self.matrix)
                change(matrix)
                with self.assertRaises(HistoricalPublicRunnerError):
                    build_handoff(
                        plan=self.plan,
                        plan_raw=self.plan_raw,
                        matrix=matrix,
                        matrix_raw=canonical_document_bytes(matrix),
                        contract=self.contract,
                        contract_raw=self.contract_raw,
                        request_id=self.request["request_id"],
                        result_id=self.result["result_id"],
                        source_repository=self.root / "source",
                        benchmark_repository=self.root / "benchmark-fetch",
                        source_archive=self.root / f"{label}.tar.gz",
                    )

    def test_network_disabled_runner_revalidates_every_controller_binding(self) -> None:
        handoff_path, matrix_path, contract_path = self.runner_paths()
        scratch = self.root / "scratch"
        handoff, source_root = validate_runner_inputs(
            handoff_path=handoff_path,
            source_archive=self.archive,
            contract_path=contract_path,
            matrix_path=matrix_path,
            benchmark_root=self.baked_benchmark(),
            scratch=scratch,
        )
        self.assertEqual(handoff, self.handoff)
        self.assertEqual(
            (source_root / "Submission.lean").read_text(encoding="utf-8"),
            "theorem example : True := by trivial\n",
        )

    def test_runner_rejects_archive_matrix_and_baked_tree_tampering(self) -> None:
        for label in (
            "archive",
            "matrix",
            "baked tree",
            "source tree",
            "plan digest",
        ):
            with self.subTest(label=label):
                self.handoff = self.make_handoff()
                handoff_path, matrix_path, contract_path = self.runner_paths()
                benchmark = self.baked_benchmark()
                archive = self.archive
                if label == "archive":
                    archive = self.root / "tampered.tar.gz"
                    archive.write_bytes(self.archive.read_bytes() + b"tamper")
                elif label == "matrix":
                    matrix = copy.deepcopy(self.matrix)
                    matrix["images"][0]["workspace_count"] += 1
                    write_canonical(matrix_path, matrix)
                else:
                    if label == "baked tree":
                        (benchmark / ".lean-eval-tree").write_text("f" * 40 + "\n")
                    elif label == "source tree":
                        self.handoff["source"]["tree"] = "f" * 40
                        write_canonical(handoff_path, self.handoff)
                    else:
                        self.handoff["plan_sha256"] = "f" * 64
                        write_canonical(handoff_path, self.handoff)
                with self.assertRaises(HistoricalPublicRunnerError):
                    validate_runner_inputs(
                        handoff_path=handoff_path,
                        source_archive=archive,
                        contract_path=contract_path,
                        matrix_path=matrix_path,
                        benchmark_root=benchmark,
                        scratch=self.root / f"scratch-{label}",
                    )

    def test_runner_rejects_links_and_traversal_before_source_use(self) -> None:
        unsafe = self.root / "unsafe.tar.gz"
        write_source_archive(unsafe, symlink=True)
        with self.assertRaisesRegex(HistoricalPublicRunnerError, "unsafe member"):
            extract_source_archive(unsafe, self.root / "unsafe-output", self.contract)
        runner = (ROOT / "scripts/historical_public_runner.py").read_text(
            encoding="utf-8"
        )
        execution = runner.split("def execute_fixed_runner", 1)[1]
        self.assertLess(
            execution.index("runtime.network_probe()"),
            execution.index("runtime.source_statistics"),
        )

    def test_streaming_archive_limits_fail_before_extraction_completes(self) -> None:
        member_limited = copy.deepcopy(self.contract)
        member_limited["source_archive"]["maximum_members"] = 1
        member_destination = self.root / "member-limited"
        with self.assertRaisesRegex(HistoricalPublicRunnerError, "member count"):
            extract_source_archive(self.archive, member_destination, member_limited)
        self.assertFalse(member_destination.exists())

        size_limited = copy.deepcopy(self.contract)
        size_limited["source_archive"]["maximum_expanded_bytes"] = 1
        size_destination = self.root / "size-limited"
        with self.assertRaisesRegex(HistoricalPublicRunnerError, "expands too far"):
            extract_source_archive(self.archive, size_destination, size_limited)
        self.assertFalse(size_destination.exists())

    def test_actual_git_archive_is_deterministic_and_source_prefixed(self) -> None:
        repository = self.root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "--quiet", repository], check=True)
        subprocess.run(
            ["git", "-C", repository, "config", "user.name", "Test"], check=True
        )
        subprocess.run(
            ["git", "-C", repository, "config", "user.email", "test@example.com"],
            check=True,
        )
        (repository / "Submission.lean").write_text("example : True := trivial\n")
        subprocess.run(["git", "-C", repository, "add", "Submission.lean"], check=True)
        subprocess.run(
            ["git", "-C", repository, "commit", "--quiet", "-m", "fixture"], check=True
        )
        commit = subprocess.check_output(
            ["git", "-C", repository, "rev-parse", "HEAD"], text=True
        ).strip()
        tree = subprocess.check_output(
            ["git", "-C", repository, "rev-parse", f"{commit}^{{tree}}"], text=True
        ).strip()
        first = self.root / "first.tar.gz"
        second = self.root / "second.tar.gz"
        _write_git_archive(repository, commit, first, 1024 * 1024, 2 * 1024 * 1024)
        _write_git_archive(repository, commit, second, 1024 * 1024, 2 * 1024 * 1024)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        source = extract_source_archive(
            first,
            self.root / "git-output",
            self.contract,
            tree,
        )
        self.assertTrue((source / "Submission.lean").is_file())

        (repository / ".gitattributes").write_text(
            "Submission.lean export-ignore\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", repository, "add", ".gitattributes"], check=True)
        subprocess.run(
            ["git", "-C", repository, "commit", "--quiet", "-m", "attributes"],
            check=True,
        )
        transformed_commit = subprocess.check_output(
            ["git", "-C", repository, "rev-parse", "HEAD"], text=True
        ).strip()
        transformed_tree = subprocess.check_output(
            [
                "git",
                "-C",
                repository,
                "rev-parse",
                f"{transformed_commit}^{{tree}}",
            ],
            text=True,
        ).strip()
        transformed = self.root / "transformed.tar.gz"
        _write_git_archive(
            repository,
            transformed_commit,
            transformed,
            1024 * 1024,
            2 * 1024 * 1024,
        )
        with self.assertRaisesRegex(
            HistoricalPublicRunnerError, "differs from its Git tree"
        ):
            extract_source_archive(
                transformed,
                self.root / "transformed-output",
                self.contract,
                transformed_tree,
            )

    def test_repository_dot_segments_fail_closed(self) -> None:
        for repository in ("../source", "owner/..", "./source", "owner/."):
            with self.subTest(repository=repository):
                handoff = copy.deepcopy(self.handoff)
                handoff["source"]["repository"] = repository
                with self.assertRaisesRegex(HistoricalPublicRunnerError, "repository"):
                    validate_handoff(handoff, self.contract)

    def test_controller_stops_an_oversized_compressed_archive_while_streaming(
        self,
    ) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = io.BytesIO(
                    b"".join(
                        hashlib.sha256(str(index).encode()).digest()
                        for index in range(65_536)
                    )
                )
                self.returncode: int | None = None
                self.killed = False

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:
                self.killed = True
                self.returncode = -9

            def wait(self) -> int:
                if self.returncode is None:
                    self.returncode = 0
                return self.returncode

        process = FakeProcess()
        destination = self.root / "oversized.tar.gz"
        with (
            mock.patch(
                "historical_public_runner.subprocess.Popen", return_value=process
            ),
            self.assertRaisesRegex(
                HistoricalPublicRunnerError, "compressed size limit"
            ),
        ):
            _write_git_archive(
                self.root,
                "a" * 40,
                destination,
                1024,
                4 * 1024 * 1024,
            )
        self.assertTrue(process.killed)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".oversized.tar.gz.*")), [])

    def test_controller_stops_an_oversized_tar_stream_before_compressing_it(
        self,
    ) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = io.BytesIO(b"x" * 8192)
                self.returncode: int | None = None
                self.killed = False

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:
                self.killed = True
                self.returncode = -9

            def wait(self) -> int:
                if self.returncode is None:
                    self.returncode = 0
                return self.returncode

        process = FakeProcess()
        destination = self.root / "oversized-tar.tar.gz"
        with (
            mock.patch(
                "historical_public_runner.subprocess.Popen", return_value=process
            ),
            self.assertRaisesRegex(HistoricalPublicRunnerError, "tar stream limit"),
        ):
            _write_git_archive(
                self.root,
                "a" * 40,
                destination,
                1024 * 1024,
                1024,
            )
        self.assertTrue(process.killed)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".oversized-tar.tar.gz.*")), [])

    def test_extensionless_authoritative_runtime_is_loadable(self) -> None:
        runtime = _load_authoritative_runtime(
            ROOT / "server" / "replay-image" / "replay-authoritative"
        )
        self.assertTrue(callable(runtime.network_probe))
        self.assertTrue(callable(runtime.build_verdict))

    def test_workflow_is_non_deploying_and_uses_a_separate_networkless_job(
        self,
    ) -> None:
        workflow = (
            ROOT / ".github/workflows/historical-public-runner-contract.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("controller-prepare:", workflow)
        self.assertIn("network-disabled-contract-check:", workflow)
        self.assertIn("docker run --rm --network none --read-only", workflow)
        self.assertIn(
            "BENCHMARK_COMMIT: ${{ steps.selection.outputs.benchmark_commit }}",
            workflow,
        )
        self.assertIn("git merge-base --is-ancestor", workflow)
        self.assertIn("--memory 2g --pids-limit 256 --cpus 2", workflow)
        self.assertIn("retention-days: 1", workflow)
        for forbidden in (
            "id-token: write",
            "packages: write",
            "STATE_WRITE_KEY",
            "wrangler deploy",
            "docker push",
            "replay.started",
        ):
            self.assertNotIn(forbidden, workflow)

        dockerignore = (
            (ROOT / "Dockerfile.historical-public-runner-contract.dockerignore")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        for required in (
            "!Dockerfile.historical-public-runner-contract",
            "!scripts/historical_public_runner.py",
            "!configuration/historical-public-replay-profile-matrix-v1.json",
            "!configuration/historical-public-runner-v1.json",
        ):
            self.assertIn(required, dockerignore)

    def test_runner_does_not_weaken_the_shared_private_request_contract(self) -> None:
        runner = (ROOT / "server/replay-image/historical-public-runner").read_text(
            encoding="utf-8"
        )
        shared_schema = (
            ROOT / "schemas/replay-execution-request-v1.schema.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn("submission_id", runner)
        self.assertNotIn("archive", runner)
        self.assertIn('"submission_id"', shared_schema)
        self.assertIn('"encrypted": { "const": true }', shared_schema)


class HistoricalPublicRunnerSchemaTests(unittest.TestCase):
    @unittest.skipUnless(
        __import__("importlib").util.find_spec("jsonschema") is not None,
        "jsonschema is installed by required CI",
    )
    def test_schemas_are_closed_and_accept_canonical_examples(self) -> None:
        import jsonschema
        from referencing import Registry, Resource

        matrix_schema = json.loads(
            (
                ROOT / "schemas/historical-public-replay-profile-matrix-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        handoff_schema = json.loads(HANDOFF_SCHEMA.read_text(encoding="utf-8"))
        registry = Registry().with_resource(
            matrix_schema["$id"], Resource.from_contents(matrix_schema)
        )
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
        entry = matrix["images"][0]
        request = next(
            item
            for item in plan["requests"]
            if item["benchmark"]["commit"] == entry["benchmark_commit"]
        )
        result = request["results"][0]
        example = {
            "schema_version": 1,
            "kind": "historical_public_runner_handoff",
            "contract": "historical_public_runner_v1",
            "contract_sha256": "a" * 64,
            "plan_sha256": matrix["plan_sha256"],
            "profile_matrix_sha256": "b" * 64,
            "request_id": request["request_id"],
            "source": {
                "repository": request["source"]["repository"],
                "commit": request["source"]["commit"],
                "tree": "c" * 40,
                "visibility": "public",
                "archive_format": "git_archive_tar_gzip_v1",
                "archive_member_prefix": "source",
                "archive_sha256": "d" * 64,
                "archive_size_bytes": 1,
            },
            "benchmark": {
                "repository": "leanprover/lean-eval",
                "commit": entry["benchmark_commit"],
                "tree": entry["benchmark_tree"],
                "toolchain": entry["toolchain"],
                "lean_toolchain_blob_sha256": entry["lean_toolchain_blob_sha256"],
            },
            "result": {
                "result_id": result["result_id"],
                "problem_id": result["problem_id"],
                "statement_revision": result["statement_revision"],
                "results_repository": "leanprover/lean-eval-submissions",
                "results_commit": result["results_commit"],
                "result_tree_digest": result["result_tree_digest"],
            },
            "profile": {
                "matrix_entry_sha256": "e" * 64,
                "qualification_status": "unqualified",
                "profile_lock": entry["profile_lock"],
            },
            "checker": "nanoda",
            "network": contract["network"],
            "untrusted_environment": {},
        }
        jsonschema.Draft202012Validator(handoff_schema, registry=registry).validate(
            example
        )
        _validate_handoff_json_schema(example, ROOT)
        changed = copy.deepcopy(example)
        changed["submission_id"] = "0198abcd-0000-7000-8000-000000000001"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(handoff_schema, registry=registry).validate(
                changed
            )

        verdict_schema = json.loads(VERDICT_SCHEMA.read_text(encoding="utf-8"))
        verdict = {
            "schema_version": 1,
            "request_id": request["request_id"],
            "result_id": result["result_id"],
            "execution_outcome": "completed",
            "checker_outcome": "accepted",
            "failure_reason": None,
            "statistics": {
                "checker_wall_time_ms": 1,
                "checker_retired_instructions": {"status": "measured", "value": 1},
                "build_wall_time_ms": 1,
                "build_retired_instructions": {
                    "status": "unavailable",
                    "reason": "counter_not_supported",
                },
                "lines_of_code": 1,
                "file_count": 1,
            },
        }
        jsonschema.Draft202012Validator(verdict_schema).validate(verdict)
        self.assertEqual(validate_historical_verdict(verdict), verdict)
        invalid_verdict = copy.deepcopy(verdict)
        invalid_verdict["execution_outcome"] = "crashed"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(verdict_schema).validate(invalid_verdict)


if __name__ == "__main__":
    unittest.main()
