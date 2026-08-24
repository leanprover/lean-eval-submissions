#!/usr/bin/env python3
"""Qualify one attempt-bound export with Mathgraph's structured result.

The controller phase consumes only reviewed public Git objects.  The execution
phase runs in the distinct staging probe image with networking disabled, emits
one source-free attestation, and has no State, Results, queue, AWS, or release
interface.  The committed fixture deliberately blocks execution while the
structured Mathgraph producer is an unmerged upstream pull request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Any

from historical_public_runner import (
    HistoricalPublicRunnerError,
    _historical_verdict,
    _load_authoritative_runtime,
    load_canonical_json,
    sha256_file,
    validate_historical_verdict,
    validate_runner_inputs,
)
from kernel_runner_wire_contract import (
    MAX_EXPORT_BYTES,
    KernelRunnerWireError,
    validate_solution_export,
)

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
ATTEMPT_ID = re.compile(r"kpa1_[0-9a-f]{64}\Z")
MAX_JSON_BYTES = 1024 * 1024
MAX_STREAM_BYTES = 16 * 1024 * 1024
PROVISIONAL_STATUS = "provisional_unmerged_pull_request"
BLOCKED_STATUS = "blocked_on_unmerged_upstream"
READY_STATUS = "ready_for_staging_probe"

FIXTURE_FIELDS = {
    "schema_version", "kind", "qualification_status", "candidate", "target", "runner"
}
ATTEMPT_FIELDS = {
    "schema_version", "kind", "attempt_id", "nonce", "fixture_sha256",
    "handoff_sha256", "source_archive_sha256", "source_archive_size_bytes",
    "runner_source_commit", "runner_image_id", "candidate_binary_sha256",
}


class StructuredAcceptedProbeError(ValueError):
    """A staging probe input or source-free output is not exact."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise StructuredAcceptedProbeError(f"{label} fields changed")
    return value


def _match(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise StructuredAcceptedProbeError(f"{label} is invalid")
    return value


def _load(path: pathlib.Path, *, maximum: int = MAX_JSON_BYTES) -> tuple[Any, bytes]:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > maximum:
            raise StructuredAcceptedProbeError(f"{path.name} exceeds its size limit")
        value = json.loads(raw.decode("utf-8"))
    except StructuredAcceptedProbeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StructuredAcceptedProbeError(f"{path.name} is invalid") from error
    if canonical(value) != raw:
        raise StructuredAcceptedProbeError(f"{path.name} is not canonical")
    return value, raw


def _write_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(canonical(value))
    except OSError as error:
        raise StructuredAcceptedProbeError("output must be create-only") from error


def validate_fixture(value: Any) -> dict[str, Any]:
    fixture = _object(value, FIXTURE_FIELDS, "probe fixture")
    if (
        type(fixture["schema_version"]) is not int
        or fixture["schema_version"] != 1
        or fixture["kind"] != "kernel_structured_accepted_probe_fixture"
        or fixture["qualification_status"] not in {BLOCKED_STATUS, READY_STATUS}
    ):
        raise StructuredAcceptedProbeError("probe fixture identity changed")
    candidate = _object(
        fixture["candidate"],
        {
            "repository", "build_repository", "pull_request", "pull_request_head_commit",
            "commit", "source_tree",
            "binary", "protocol", "protocol_schema_sha256", "protocol_vectors_sha256",
            "upstream_status",
        },
        "candidate",
    )
    if (
        candidate["repository"] != "metalogiclabs/mathgraph-lean-kernel"
        or candidate["pull_request"] != 51
        or candidate["binary"] != "sokonanoda"
        or candidate["protocol"] != "sokonanoda_result_v1"
        or candidate["upstream_status"]
        not in {PROVISIONAL_STATUS, "merged_upstream_exact_commit"}
    ):
        raise StructuredAcceptedProbeError("candidate identity changed")
    for field in ("pull_request_head_commit", "commit", "source_tree"):
        _match(COMMIT, candidate[field], f"candidate.{field}")
    for field in ("protocol_schema_sha256", "protocol_vectors_sha256"):
        _match(DIGEST, candidate[field], f"candidate.{field}")
    if not isinstance(candidate["build_repository"], str):
        raise StructuredAcceptedProbeError("candidate build repository is invalid")
    if fixture["qualification_status"] == BLOCKED_STATUS:
        if (
            candidate
            != {
                "repository": "metalogiclabs/mathgraph-lean-kernel",
                "build_repository": "kim-em/mathgraph-lean-kernel",
                "pull_request": 51,
                "pull_request_head_commit": "400ab9c1cc4fba03f7a3f95f4604b5cef4e23a44",
                "commit": "400ab9c1cc4fba03f7a3f95f4604b5cef4e23a44",
                "source_tree": "05ebd1a6a4bba6d38729e51f38680186288f4ac3",
                "binary": "sokonanoda",
                "protocol": "sokonanoda_result_v1",
                "protocol_schema_sha256": "b96b99526a143ae39a9e8d058f80337f34d3e7c153e9e1878d2c29d9a56767d9",
                "protocol_vectors_sha256": "0712d67d88c65f10742ede70d0697360a0fc22b5ff79197f19050ae5e2812f4d",
                "upstream_status": PROVISIONAL_STATUS,
            }
        ):
            raise StructuredAcceptedProbeError("provisional candidate pin changed")
    elif (
        candidate["upstream_status"] != "merged_upstream_exact_commit"
        or candidate["build_repository"] != candidate["repository"]
    ):
        raise StructuredAcceptedProbeError("runnable candidate is not upstream-owned")

    target = _object(
        fixture["target"],
        {
            "historical_plan_sha256", "profile_matrix_sha256",
            "historical_smoke_fixture_sha256", "request_id", "result_id",
            "source_repository", "source_commit", "source_tree",
            "benchmark_repository", "benchmark_commit", "toolchain", "problem_id",
            "statement_revision", "benchmark_configuration_sha256",
        },
        "target",
    )
    expected_target = {
        "historical_plan_sha256": "2b00c9651f5c3f43d44e0306a8368947a4a950ab3dd1e8c9b1f283fc82101942",
        "profile_matrix_sha256": "aad9132f729ef9f429532900d1e50b665330721fa9360699328c47bdfb2aedfc",
        "historical_smoke_fixture_sha256": "9568054991db378206e67e92c8094b177f8ef138096357eff7c03f8107c5d72c",
        "request_id": "prr_632ee5cddf6bb19fe0ffd786c0c0985825bc9a01e155a1c7d237946cc405e422",
        "result_id": "r2_c4e178fbb6cdafcb8f2146245adf02a709a60836f022e8a3d75d72c84b472b60",
        "source_repository": "KitaKen1/lean-eval-two-plus-two",
        "source_commit": "a7cf16eead7f54a3eae4099c154eb80b0f520c92",
        "source_tree": "b2ec72251e306391171598b21835d58ebde84757",
        "benchmark_repository": "leanprover/lean-eval",
        "benchmark_commit": "3f3786f3b4d9a4b64a5859b3036aca190cd25613",
        "toolchain": "leanprover/lean4:v4.32.2",
        "problem_id": "two_plus_two",
        "statement_revision": 1,
        "benchmark_configuration_sha256": "dd9f6978ec36ba027cddda161e49abb435a219bf4766e115b7f81fdc69f70f4e",
    }
    if type(target["statement_revision"]) is not int or target != expected_target:
        raise StructuredAcceptedProbeError("accepted public result target changed")
    runner = _object(
        fixture["runner"],
        {"environment", "network", "credentials", "architecture", "source_handoff"},
        "runner",
    )
    if runner != {
        "environment": "replay-staging",
        "network": "disabled",
        "credentials": "absent",
        "architecture": "x86_64",
        "source_handoff": "source_free_attestation_only",
    }:
        raise StructuredAcceptedProbeError("runner boundary changed")
    return fixture


def validate_fixture_sources(
    fixture: dict[str, Any],
    *,
    plan_path: pathlib.Path,
    matrix_path: pathlib.Path,
    smoke_path: pathlib.Path,
) -> None:
    target = fixture["target"]
    plan_raw = plan_path.read_bytes()
    matrix_raw = matrix_path.read_bytes()
    smoke_raw = smoke_path.read_bytes()
    for raw, expected, label in (
        (plan_raw, target["historical_plan_sha256"], "historical plan"),
        (matrix_raw, target["profile_matrix_sha256"], "profile matrix"),
        (smoke_raw, target["historical_smoke_fixture_sha256"], "smoke fixture"),
    ):
        if hashlib.sha256(raw).hexdigest() != expected:
            raise StructuredAcceptedProbeError(f"{label} bytes changed")
    try:
        plan = json.loads(plan_raw)
        matrix = json.loads(matrix_raw)
        smoke = json.loads(smoke_raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise StructuredAcceptedProbeError("fixture source JSON is invalid") from error
    requests = [item for item in plan["requests"] if item.get("request_id") == target["request_id"]]
    if len(requests) != 1:
        raise StructuredAcceptedProbeError("target request is not unique in the plan")
    request = requests[0]
    results = [item for item in request["results"] if item.get("result_id") == target["result_id"]]
    if len(results) != 1:
        raise StructuredAcceptedProbeError("target result is not unique in the plan")
    result = results[0]
    if (
        request["source"] != {
            "kind": "github_repo", "visibility": "public",
            "repository": target["source_repository"], "commit": target["source_commit"],
        }
        or request["benchmark"]["repository"] != target["benchmark_repository"]
        or request["benchmark"]["commit"] != target["benchmark_commit"]
        or request["benchmark"]["toolchain"] != target["toolchain"]
        or result["problem_id"] != target["problem_id"]
        or result["statement_revision"] != target["statement_revision"]
    ):
        raise StructuredAcceptedProbeError("plan target binding changed")
    entries = [item for item in matrix["images"] if item.get("benchmark_commit") == target["benchmark_commit"]]
    if (
        len(entries) != 1
        or target["problem_id"] not in entries[0]["problem_ids"]
        or entries[0]["toolchain"] != target["toolchain"]
        or entries[0]["qualification_status"] != "unqualified"
    ):
        raise StructuredAcceptedProbeError("matrix target binding changed")
    if (
        smoke.get("source", {}).get("repository") != target["source_repository"]
        or smoke.get("source", {}).get("commit") != target["source_commit"]
        or smoke.get("benchmark", {}).get("commit") != target["benchmark_commit"]
        or smoke.get("problem_id") != target["problem_id"]
        or smoke.get("statement_revision") != target["statement_revision"]
    ):
        raise StructuredAcceptedProbeError("accepted smoke target changed")


def require_runnable(fixture: dict[str, Any]) -> None:
    if fixture["qualification_status"] != READY_STATUS:
        raise StructuredAcceptedProbeError(
            "execution blocked: Mathgraph PR #51 is not merged and the source pin is provisional"
        )


def _attempt_without_id(
    fixture_raw: bytes,
    handoff_raw: bytes,
    source_archive: pathlib.Path,
    *,
    nonce: str,
    runner_source_commit: str,
    runner_image_id: str,
    candidate_binary_sha256: str,
) -> dict[str, Any]:
    _match(DIGEST, nonce, "attempt nonce")
    _match(COMMIT, runner_source_commit, "runner source commit")
    _match(IMAGE_ID, runner_image_id, "runner image id")
    _match(DIGEST, candidate_binary_sha256, "candidate binary digest")
    return {
        "schema_version": 1,
        "kind": "kernel_structured_accepted_probe_attempt",
        "nonce": nonce,
        "fixture_sha256": hashlib.sha256(fixture_raw).hexdigest(),
        "handoff_sha256": hashlib.sha256(handoff_raw).hexdigest(),
        "source_archive_sha256": sha256_file(source_archive),
        "source_archive_size_bytes": source_archive.stat().st_size,
        "runner_source_commit": runner_source_commit,
        "runner_image_id": runner_image_id,
        "candidate_binary_sha256": candidate_binary_sha256,
    }


def build_attempt(
    fixture_raw: bytes,
    handoff_raw: bytes,
    source_archive: pathlib.Path,
    **identity: str,
) -> dict[str, Any]:
    base = _attempt_without_id(fixture_raw, handoff_raw, source_archive, **identity)
    return {**base, "attempt_id": "kpa1_" + digest(base)}


def validate_attempt(value: Any) -> dict[str, Any]:
    attempt = _object(value, ATTEMPT_FIELDS, "probe attempt")
    if (
        type(attempt["schema_version"]) is not int
        or attempt["schema_version"] != 1
        or attempt["kind"] != "kernel_structured_accepted_probe_attempt"
    ):
        raise StructuredAcceptedProbeError("probe attempt identity changed")
    _match(ATTEMPT_ID, attempt["attempt_id"], "attempt id")
    for field in (
        "nonce", "fixture_sha256", "handoff_sha256", "source_archive_sha256",
        "candidate_binary_sha256",
    ):
        _match(DIGEST, attempt[field], f"attempt.{field}")
    _match(COMMIT, attempt["runner_source_commit"], "attempt.runner_source_commit")
    _match(IMAGE_ID, attempt["runner_image_id"], "attempt.runner_image_id")
    if type(attempt["source_archive_size_bytes"]) is not int or not 1 <= attempt["source_archive_size_bytes"] <= 52_428_800:
        raise StructuredAcceptedProbeError("attempt source archive size is invalid")
    base = {field: attempt[field] for field in ATTEMPT_FIELDS if field != "attempt_id"}
    if attempt["attempt_id"] != "kpa1_" + digest(base):
        raise StructuredAcceptedProbeError("attempt id does not bind its exact inputs")
    return attempt


def _read_regular(path: pathlib.Path, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise StructuredAcceptedProbeError(f"{label} is not a bounded exclusive regular file")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, min(1024 * 1024, maximum + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise StructuredAcceptedProbeError(f"{label} exceeds its size limit")
        after = os.fstat(descriptor)
    except OSError as error:
        raise StructuredAcceptedProbeError(f"{label} cannot be read") from error
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    def identity(item: os.stat_result) -> tuple[int, ...]:
        return (
            item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_size,
            item.st_mtime_ns, item.st_ctime_ns,
        )
    if identity(before) != identity(after) or total != before.st_size:
        raise StructuredAcceptedProbeError(f"{label} changed while it was read")
    return b"".join(chunks)


def _export_metadata(raw: bytes) -> dict[str, Any]:
    try:
        first = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
        meta = first["meta"]
        exporter = meta["exporter"]
        lean = meta["lean"]
        format_value = meta["format"]
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise StructuredAcceptedProbeError("solution export metadata is invalid") from error
    if (
        not isinstance(exporter, dict) or set(exporter) != {"name", "version"}
        or not isinstance(lean, dict) or set(lean) != {"version", "githash"}
        or not isinstance(format_value, dict) or set(format_value) != {"version"}
    ):
        raise StructuredAcceptedProbeError("solution export metadata fields changed")
    return {"exporter": exporter, "lean": lean, "format": format_value}


def _run_candidate(
    executable: pathlib.Path,
    configuration: pathlib.Path,
    result_path: pathlib.Path,
    stdout_path: pathlib.Path,
    stderr_path: pathlib.Path,
    timeout_seconds: float,
) -> tuple[int, bool, int]:
    started = time.monotonic_ns()
    sandbox_argv = [
        "/usr/local/bin/landrun",
        "--rox", "/opt/lean-eval/bin/sokonanoda",
        "--ro", "/run/lean-eval/solution-export.ndjson",
        "--ro", "/run/lean-eval/nanoda-config.json",
        "--rox", "/lib",
        "--rox", "/lib64",
        "--rox", "/usr/lib",
        "--ro", "/etc/ld.so.cache",
        "--rw", str(result_path.parent),
        "--",
        str(executable), "--result-file", str(result_path), str(configuration),
    ]

    def limits() -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_STREAM_BYTES, MAX_STREAM_BYTES))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))

    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(
            sandbox_argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env={},
            preexec_fn=limits,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    if process.returncode is None:
        raise StructuredAcceptedProbeError("candidate did not terminate")
    return process.returncode, timed_out, (time.monotonic_ns() - started) // 1_000_000


def _validate_structured_result(raw: bytes, returncode: int) -> dict[str, Any]:
    expected = b'{"schema_version":1,"protocol":"sokonanoda_result_v1","outcome":"accepted","reason_code":"checked"}\n'
    if returncode != 0 or raw != expected:
        raise StructuredAcceptedProbeError("candidate did not emit the exact accepted exit/result pair")
    return json.loads(raw)


def _assert_no_credentials() -> None:
    prefixes = ("AWS_", "CLOUDFLARE_", "GOOGLE_", "AZURE_", "GITHUB_TOKEN", "GH_TOKEN")
    if any(key.startswith(prefixes) for key in os.environ):
        raise StructuredAcceptedProbeError("credential-shaped environment reached the probe")


def execute_probe(
    *,
    fixture_path: pathlib.Path,
    attempt_path: pathlib.Path,
    input_root: pathlib.Path,
    install: pathlib.Path,
    workspace: pathlib.Path,
) -> dict[str, Any]:
    os.umask(0o077)
    fixture_value, fixture_raw = _load(fixture_path)
    fixture = validate_fixture(fixture_value)
    require_runnable(fixture)
    attempt_value, attempt_raw = _load(attempt_path)
    attempt = validate_attempt(attempt_value)
    handoff_path = input_root / "historical-public-request.json"
    archive_path = input_root / "historical-public-source.tar.gz"
    handoff_value, handoff_raw = _load(handoff_path)
    if (
        hashlib.sha256(fixture_raw).hexdigest() != attempt["fixture_sha256"]
        or hashlib.sha256(handoff_raw).hexdigest() != attempt["handoff_sha256"]
        or sha256_file(archive_path) != attempt["source_archive_sha256"]
        or archive_path.stat().st_size != attempt["source_archive_size_bytes"]
    ):
        raise StructuredAcceptedProbeError("attempt inputs differ from their binding")
    candidate = install / "bin" / "sokonanoda"
    if sha256_file(candidate) != attempt["candidate_binary_sha256"]:
        raise StructuredAcceptedProbeError("candidate binary differs from the attempt")
    target = fixture["target"]
    if (
        handoff_value.get("request_id") != target["request_id"]
        or handoff_value.get("result", {}).get("result_id") != target["result_id"]
        or handoff_value.get("source", {}).get("repository") != target["source_repository"]
        or handoff_value.get("source", {}).get("commit") != target["source_commit"]
        or handoff_value.get("source", {}).get("tree") != target["source_tree"]
        or handoff_value.get("benchmark", {}).get("commit") != target["benchmark_commit"]
        or handoff_value.get("result", {}).get("problem_id") != target["problem_id"]
    ):
        raise StructuredAcceptedProbeError("handoff differs from the fixed accepted target")

    runtime = _load_authoritative_runtime(install / "replay-authoritative")
    scratch = workspace / "historical-public"
    output = workspace / "historical-public-output"
    metrics = pathlib.Path("/run/lean-eval/metrics.json")
    solution_export = pathlib.Path("/run/lean-eval/solution-export.ndjson")
    candidate_config = pathlib.Path("/run/lean-eval/nanoda-config.json")
    candidate_output = pathlib.Path("/run/lean-eval/kernel-output")
    candidate_result = candidate_output / "sokonanoda-result.json"
    candidate_streams = workspace / "kernel-probe-streams"
    candidate_stdout = candidate_streams / "sokonanoda.stdout"
    candidate_stderr = candidate_streams / "sokonanoda.stderr"
    benchmark_config_path = install / "benchmark" / "generated" / target["problem_id"] / "config.json"
    try:
        _assert_no_credentials()
        handoff, source_root = validate_runner_inputs(
            handoff_path=handoff_path,
            source_archive=archive_path,
            contract_path=install / "historical-public-runner-v1.json",
            matrix_path=install / "historical-public-replay-profile-matrix-v1.json",
            benchmark_root=install / "benchmark",
            scratch=scratch,
        )
        contract, _ = load_canonical_json(
            install / "historical-public-runner-v1.json",
            "historical public runner contract",
            128 * 1024,
        )
        runtime.network_probe()
        file_count, lines_of_code = runtime.source_statistics(
            source_root, target["problem_id"]
        )
        for path in (
            metrics, solution_export, candidate_config, candidate_result,
            candidate_stdout, candidate_stderr,
        ):
            path.unlink(missing_ok=True)
        candidate_output.mkdir(mode=0o700, parents=False, exist_ok=False)
        candidate_streams.mkdir(mode=0o700, parents=False, exist_ok=False)
        returncode, timed_out = runtime.run_process_group(
            [
                sys.executable,
                str(install / "evaluate_submission.py"),
                "--source-dir", str(source_root),
                "--generated-root", str(install / "benchmark" / "generated"),
                "--manifest-dir", str(install / "benchmark" / "manifests" / "problems"),
                "--output-dir", str(output),
                "--repo-root", str(install / "benchmark"),
                "--shared-packages", str(install / "benchmark" / ".lake" / "packages"),
                "--problem-id", target["problem_id"],
                "--statement-revision", str(target["statement_revision"]),
                "--measurement-command-json", json.dumps(contract["measurement_command"], separators=(",", ":")),
                "--authoritative-checker", "nanoda",
                "--preprimed-workspaces",
            ],
            {
                "PATH": f"{install}/bin:{install}/home/.elan/bin:/usr/local/bin:/usr/bin:/bin",
                "HOME": str(install / "home"),
                "COMPARATOR_BIN": str(install / "bin" / "comparator"),
                "COMPARATOR_LANDRUN": "/usr/local/bin/landrun",
            },
            contract["wall_time_limit_ms"] / 1000,
        )
        metrics_value = runtime.load_metrics_after_execution()
        if timed_out or returncode != 0:
            raise StructuredAcceptedProbeError("accepted-target evaluator did not complete")
        evaluator_results = runtime.load_json(output / "results.json", "evaluator results", 64 * 1024)
        historical_verdict = _historical_verdict(
            runtime,
            handoff,
            evaluator_results=evaluator_results,
            metrics_value=metrics_value,
            file_count=file_count,
            lines_of_code=lines_of_code,
        )
        if historical_verdict["execution_outcome"] != "completed" or historical_verdict["checker_outcome"] != "accepted":
            raise StructuredAcceptedProbeError("fixed public result was not reproduced as accepted")

        export_raw = _read_regular(solution_export, MAX_EXPORT_BYTES, "solution export")
        export_meta = _export_metadata(export_raw)
        export_sha256 = hashlib.sha256(export_raw).hexdigest()
        export_identity = validate_solution_export(
            export_raw,
            input_sha256=export_sha256,
            exporter=export_meta["exporter"],
            lean=export_meta["lean"],
            format_version=export_meta["format"]["version"],
        )
        benchmark_config_raw = _read_regular(
            benchmark_config_path, 1024 * 1024, "benchmark configuration"
        )
        if hashlib.sha256(benchmark_config_raw).hexdigest() != target["benchmark_configuration_sha256"]:
            raise StructuredAcceptedProbeError("benchmark configuration bytes changed")
        benchmark_config = json.loads(benchmark_config_raw)
        permitted_axioms = benchmark_config.get("permitted_axioms")
        if permitted_axioms != ["propext", "Quot.sound", "Classical.choice"]:
            raise StructuredAcceptedProbeError("benchmark permitted axioms are invalid")
        checker_configuration = {
            "export_file_path": str(solution_export),
            "nat_extension": True,
            "permitted_axioms": sorted(permitted_axioms),
            "string_extension": True,
            "unpermitted_axiom_hard_error": True,
            "use_stdin": False,
        }
        config_raw = json.dumps(checker_configuration, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        descriptor = os.open(candidate_config, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "wb") as config_output:
            config_output.write(config_raw)
        # The kernel must not retain access to the public source merely because
        # this qualification started from a public result. Landrun below grants
        # only its binary/runtime, exact export/config, and result directory.
        shutil.rmtree(scratch)
        shutil.rmtree(output)
        candidate_returncode, candidate_timed_out, wall_time_ms = _run_candidate(
            candidate, candidate_config, candidate_result, candidate_stdout,
            candidate_stderr, 900,
        )
        if candidate_timed_out:
            raise StructuredAcceptedProbeError("structured candidate timed out")
        result_raw = _read_regular(candidate_result, 4096, "structured result")
        structured_result = _validate_structured_result(result_raw, candidate_returncode)
        stdout_raw = _read_regular(candidate_stdout, MAX_STREAM_BYTES, "candidate stdout")
        stderr_raw = _read_regular(candidate_stderr, MAX_STREAM_BYTES, "candidate stderr")
        evidence = {
            "schema_version": 1,
            "kind": "kernel_structured_accepted_probe_attestation",
            "qualification_status": "provisional",
            "scope": "staging_only",
            "attempt_id": attempt["attempt_id"],
            "attempt_sha256": hashlib.sha256(attempt_raw).hexdigest(),
            "runner": {
                "source_commit": attempt["runner_source_commit"],
                "image_id": attempt["runner_image_id"],
                "environment": "replay-staging",
                "architecture": platform.machine(),
                "kernel_release": platform.release(),
                "network": "disabled_active_probe",
                "credentials": "absent",
                "source_free_handoff": True,
            },
            "target": {
                "request_id": target["request_id"],
                "result_id": target["result_id"],
                "problem_id": target["problem_id"],
                "statement_revision": target["statement_revision"],
                "source_repository": target["source_repository"],
                "source_commit": target["source_commit"],
                "source_tree": target["source_tree"],
                "source_archive_sha256": attempt["source_archive_sha256"],
                "benchmark_repository": target["benchmark_repository"],
                "benchmark_commit": target["benchmark_commit"],
                "benchmark_configuration_sha256": target["benchmark_configuration_sha256"],
            },
            "pipeline": {
                "verdict": historical_verdict,
                "verdict_sha256": digest(historical_verdict),
                "results_sha256": digest(evaluator_results),
                "metrics_sha256": digest(metrics_value),
            },
            "export": {
                **export_identity,
                "source_free": True,
                "capture": "same_evaluator_process_before_cleanup",
            },
            "candidate": {
                "repository": fixture["candidate"]["repository"],
                "commit": fixture["candidate"]["commit"],
                "source_tree": fixture["candidate"]["source_tree"],
                "binary_sha256": attempt["candidate_binary_sha256"],
                "protocol": fixture["candidate"]["protocol"],
                "argv": [str(candidate), "--result-file", str(candidate_result), str(candidate_config)],
                "sandbox_argv": [
                    "/usr/local/bin/landrun",
                    "--rox", "/opt/lean-eval/bin/sokonanoda",
                    "--ro", "/run/lean-eval/solution-export.ndjson",
                    "--ro", "/run/lean-eval/nanoda-config.json",
                    "--rox", "/lib", "--rox", "/lib64", "--rox", "/usr/lib",
                    "--ro", "/etc/ld.so.cache",
                    "--rw", "/run/lean-eval/kernel-output",
                    "--", "/opt/lean-eval/bin/sokonanoda", "--result-file",
                    "/run/lean-eval/kernel-output/sokonanoda-result.json",
                    "/run/lean-eval/nanoda-config.json",
                ],
                "filesystem_policy": "closed_kernel_inputs_v1",
                "resource_limits": {
                    "wall_timeout_ms": 900_000,
                    "maximum_output_file_bytes": MAX_STREAM_BYTES,
                    "maximum_open_files": 128,
                    "core_dump_bytes": 0,
                },
                "environment": {},
                "configuration_sha256": hashlib.sha256(config_raw).hexdigest(),
                "result_sha256": hashlib.sha256(result_raw).hexdigest(),
                "result": structured_result,
                "termination": {"exit_code": candidate_returncode, "timed_out": False},
                "statistics": {
                    "wall_time_ms": wall_time_ms,
                    "stdout_size_bytes": len(stdout_raw),
                    "stdout_sha256": hashlib.sha256(stdout_raw).hexdigest(),
                    "stderr_size_bytes": len(stderr_raw),
                    "stderr_sha256": hashlib.sha256(stderr_raw).hexdigest(),
                },
            },
            "outcome": "accepted",
        }
        validate_attestation(evidence, fixture, attempt)
        return evidence
    finally:
        for path in (
            metrics, solution_export, candidate_config, candidate_result,
            candidate_stdout, candidate_stderr,
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(output, ignore_errors=True)
        shutil.rmtree(candidate_output, ignore_errors=True)
        shutil.rmtree(candidate_streams, ignore_errors=True)


def validate_attestation(
    value: Any, fixture: dict[str, Any], attempt: dict[str, Any]
) -> dict[str, Any]:
    evidence = _object(
        value,
        {
            "schema_version", "kind", "qualification_status", "scope", "attempt_id",
            "attempt_sha256", "runner", "target", "pipeline", "export", "candidate",
            "outcome",
        },
        "probe attestation",
    )
    if (
        type(evidence["schema_version"]) is not int
        or evidence["schema_version"] != 1
        or evidence["kind"] != "kernel_structured_accepted_probe_attestation"
        or evidence["qualification_status"] != "provisional"
        or evidence["scope"] != "staging_only"
        or evidence["attempt_id"] != attempt["attempt_id"]
        or evidence["outcome"] != "accepted"
    ):
        raise StructuredAcceptedProbeError("probe attestation identity changed")
    if evidence["attempt_sha256"] != digest(attempt):
        raise StructuredAcceptedProbeError("attestation does not bind the exact attempt")
    runner = _object(
        evidence["runner"],
        {
            "source_commit", "image_id", "environment", "architecture", "kernel_release",
            "network", "credentials", "source_free_handoff",
        },
        "attested runner",
    )
    if (
        runner["source_commit"] != attempt["runner_source_commit"]
        or runner["image_id"] != attempt["runner_image_id"]
        or runner["environment"] != "replay-staging"
        or runner["architecture"] != "x86_64"
        or not isinstance(runner["kernel_release"], str)
        or not 1 <= len(runner["kernel_release"]) <= 256
        or any(character.isspace() for character in runner["kernel_release"])
        or runner["network"] != "disabled_active_probe"
        or runner["credentials"] != "absent"
        or runner["source_free_handoff"] is not True
    ):
        raise StructuredAcceptedProbeError("attested runner boundary changed")
    target = _object(
        evidence["target"],
        {
            "request_id", "result_id", "problem_id", "statement_revision",
            "source_repository", "source_commit", "source_tree",
            "source_archive_sha256", "benchmark_repository", "benchmark_commit",
            "benchmark_configuration_sha256",
        },
        "attested target",
    )
    fixed = fixture["target"]
    if any(target.get(field) != fixed[field] for field in (
        "request_id", "result_id", "problem_id", "statement_revision",
        "source_repository", "source_commit", "source_tree", "benchmark_repository",
        "benchmark_commit", "benchmark_configuration_sha256",
    )) or target.get("source_archive_sha256") != attempt["source_archive_sha256"]:
        raise StructuredAcceptedProbeError("attested target changed")
    pipeline = _object(
        evidence["pipeline"],
        {"verdict", "verdict_sha256", "results_sha256", "metrics_sha256"},
        "attested pipeline",
    )
    verdict = validate_historical_verdict(pipeline["verdict"])
    if (
        verdict["request_id"] != fixed["request_id"]
        or verdict["result_id"] != fixed["result_id"]
        or verdict["execution_outcome"] != "completed"
        or verdict["checker_outcome"] != "accepted"
        or pipeline["verdict_sha256"] != digest(pipeline["verdict"])
    ):
        raise StructuredAcceptedProbeError("pipeline did not attest acceptance")
    for field in ("verdict_sha256", "results_sha256", "metrics_sha256"):
        _match(DIGEST, pipeline[field], f"pipeline.{field}")
    export = _object(
        evidence["export"],
        {
            "exporter", "lean", "format", "line_count", "size_bytes", "sha256",
            "source_free", "capture",
        },
        "attested export",
    )
    if (
        export.get("source_free") is not True
        or export.get("capture") != "same_evaluator_process_before_cleanup"
        or type(export.get("size_bytes")) is not int
        or not 1 <= export["size_bytes"] <= MAX_EXPORT_BYTES
        or type(export.get("line_count")) is not int
        or not 1 <= export["line_count"] <= 2_000_000
    ):
        raise StructuredAcceptedProbeError("export attestation changed")
    _match(DIGEST, export.get("sha256"), "attested export digest")
    exporter = _object(export["exporter"], {"name", "version"}, "attested exporter")
    lean = _object(export["lean"], {"version", "githash"}, "attested Lean")
    format_value = _object(
        export["format"], {"version"}, "attested export format"
    )
    if (
        exporter["name"] != "lean4export"
        or not isinstance(exporter["version"], str)
        or not 1 <= len(exporter["version"]) <= 64
        or not isinstance(lean["version"], str)
        or not 1 <= len(lean["version"]) <= 64
        or format_value["version"] != "3.1.0"
    ):
        raise StructuredAcceptedProbeError("export identity changed")
    _match(COMMIT, lean["githash"], "attested Lean githash")
    candidate = _object(
        evidence["candidate"],
        {
            "repository", "commit", "source_tree", "binary_sha256", "protocol",
            "argv", "sandbox_argv", "filesystem_policy", "environment",
            "resource_limits", "configuration_sha256", "result_sha256",
            "result", "termination", "statistics",
        },
        "attested candidate",
    )
    if (
        candidate.get("repository") != fixture["candidate"]["repository"]
        or candidate.get("commit") != fixture["candidate"]["commit"]
        or candidate.get("source_tree") != fixture["candidate"]["source_tree"]
        or candidate.get("binary_sha256") != attempt["candidate_binary_sha256"]
        or candidate.get("protocol") != "sokonanoda_result_v1"
        or candidate.get("argv") != [
            "/opt/lean-eval/bin/sokonanoda", "--result-file",
            "/run/lean-eval/kernel-output/sokonanoda-result.json",
            "/run/lean-eval/nanoda-config.json",
        ]
        or candidate.get("sandbox_argv") != [
            "/usr/local/bin/landrun",
            "--rox", "/opt/lean-eval/bin/sokonanoda",
            "--ro", "/run/lean-eval/solution-export.ndjson",
            "--ro", "/run/lean-eval/nanoda-config.json",
            "--rox", "/lib", "--rox", "/lib64", "--rox", "/usr/lib",
            "--ro", "/etc/ld.so.cache",
            "--rw", "/run/lean-eval/kernel-output",
            "--", "/opt/lean-eval/bin/sokonanoda", "--result-file",
            "/run/lean-eval/kernel-output/sokonanoda-result.json",
            "/run/lean-eval/nanoda-config.json",
        ]
        or candidate.get("filesystem_policy") != "closed_kernel_inputs_v1"
        or candidate.get("resource_limits") != {
            "wall_timeout_ms": 900_000,
            "maximum_output_file_bytes": MAX_STREAM_BYTES,
            "maximum_open_files": 128,
            "core_dump_bytes": 0,
        }
        or candidate.get("environment") != {}
        or candidate.get("result") != {
            "schema_version": 1, "protocol": "sokonanoda_result_v1",
            "outcome": "accepted", "reason_code": "checked",
        }
        or candidate.get("termination") != {"exit_code": 0, "timed_out": False}
    ):
        raise StructuredAcceptedProbeError("structured candidate attestation changed")
    for field in ("configuration_sha256", "result_sha256"):
        _match(DIGEST, candidate.get(field), f"candidate.{field}")
    statistics = candidate.get("statistics")
    if not isinstance(statistics, dict) or set(statistics) != {
        "wall_time_ms", "stdout_size_bytes", "stdout_sha256",
        "stderr_size_bytes", "stderr_sha256",
    }:
        raise StructuredAcceptedProbeError("candidate statistics changed")
    for field in ("stdout_sha256", "stderr_sha256"):
        _match(DIGEST, statistics[field], f"candidate statistics {field}")
    for field in ("wall_time_ms", "stdout_size_bytes", "stderr_size_bytes"):
        if type(statistics[field]) is not int or statistics[field] < 0:
            raise StructuredAcceptedProbeError(f"candidate statistics {field} is invalid")
    if (
        statistics["wall_time_ms"] > 900_000
        or statistics["stdout_size_bytes"] > MAX_STREAM_BYTES
        or statistics["stderr_size_bytes"] > MAX_STREAM_BYTES
    ):
        raise StructuredAcceptedProbeError("candidate statistics exceed their limits")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-fixture")
    validate.add_argument("--fixture", required=True, type=pathlib.Path)
    validate.add_argument("--plan", required=True, type=pathlib.Path)
    validate.add_argument("--matrix", required=True, type=pathlib.Path)
    validate.add_argument("--smoke", required=True, type=pathlib.Path)
    runnable = commands.add_parser("require-runnable")
    runnable.add_argument("--fixture", required=True, type=pathlib.Path)
    prepare = commands.add_parser("prepare-attempt")
    prepare.add_argument("--fixture", required=True, type=pathlib.Path)
    prepare.add_argument("--handoff", required=True, type=pathlib.Path)
    prepare.add_argument("--source-archive", required=True, type=pathlib.Path)
    prepare.add_argument("--nonce", required=True)
    prepare.add_argument("--runner-source-commit", required=True)
    prepare.add_argument("--runner-image-id", required=True)
    prepare.add_argument("--candidate-binary-sha256", required=True)
    prepare.add_argument("--output", required=True, type=pathlib.Path)
    run = commands.add_parser("run")
    run.add_argument("--fixture", required=True, type=pathlib.Path)
    run.add_argument("--attempt", required=True, type=pathlib.Path)
    run.add_argument("--input-root", required=True, type=pathlib.Path)
    run.add_argument("--install", required=True, type=pathlib.Path)
    run.add_argument("--workspace", required=True, type=pathlib.Path)
    check = commands.add_parser("validate-attestation")
    check.add_argument("--fixture", required=True, type=pathlib.Path)
    check.add_argument("--attempt", required=True, type=pathlib.Path)
    check.add_argument("--attestation", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        fixture_value, fixture_raw = _load(args.fixture)
        fixture = validate_fixture(fixture_value)
        if args.command == "validate-fixture":
            validate_fixture_sources(
                fixture, plan_path=args.plan, matrix_path=args.matrix, smoke_path=args.smoke
            )
        elif args.command == "require-runnable":
            require_runnable(fixture)
        elif args.command == "prepare-attempt":
            require_runnable(fixture)
            _handoff_value, handoff_raw = _load(args.handoff)
            value = build_attempt(
                fixture_raw, handoff_raw, args.source_archive,
                nonce=args.nonce, runner_source_commit=args.runner_source_commit,
                runner_image_id=args.runner_image_id,
                candidate_binary_sha256=args.candidate_binary_sha256,
            )
            _write_exclusive(args.output, value)
        elif args.command == "run":
            value = execute_probe(
                fixture_path=args.fixture, attempt_path=args.attempt,
                input_root=args.input_root, install=args.install, workspace=args.workspace,
            )
            sys.stdout.buffer.write(canonical(value))
        else:
            attempt, _ = _load(args.attempt)
            attestation, _ = _load(args.attestation)
            validate_attestation(attestation, fixture, validate_attempt(attempt))
    except (
        StructuredAcceptedProbeError, HistoricalPublicRunnerError,
        KernelRunnerWireError, OSError, UnicodeError, json.JSONDecodeError,
        KeyError, TypeError, ValueError,
    ) as error:
        print(f"kernel-structured-accepted-probe: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
