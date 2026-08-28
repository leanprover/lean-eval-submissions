#!/usr/bin/env python3
"""Plan one archived historical private replay without lifecycle synthesis.

This offline controller consumes State's distinct schema-version-1 private
historical queue, proves exact reviewed plan and profile Git blobs, and adapts
one archive locator to the existing credentialed replay primitives. It performs
no network, Git write, State write, infrastructure, or executor operation.
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

SCRIPT_DIRECTORY = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from historical_replay_controller import (  # noqa: E402
    COMMIT,
    DIGEST,
    LOGIN,
    MAX_REPLAY_ATTEMPTS,
    PROBLEM,
    REPLAY_ID,
    RESULTS_PATH,
    RESULT_ID,
    TOOLCHAIN,
    UUID7,
    HistoricalReplayControllerError,
    _causal_uuid7,
    _event_time,
    _fields,
    _git_blob,
    _integer,
    _load_canonical,
    _load_state_canonical,
    _match,
    _object,
    _parse_canonical,
    _reject_duplicate_pairs,
    _reject_nonfinite_constant,
    _timestamp,
    _write,
    canonical_bytes,
    current_historical_running,
    recover_running as recover_historical_running,
    sha256_bytes,
    state_canonical_bytes,
)
from archive_submission import _validate_sidecar  # noqa: E402
from key_capability_contract import validate_envelope  # noqa: E402
from prepare_historical_private_replay import canonical_compact, entry_sha256  # noqa: E402
from replay_controller import (  # noqa: E402
    _write_bytes,
    build_executor_request as build_private_executor_request,
    failure_verdict as build_private_failure_verdict,
    prepare_unwrap as prepare_private_unwrap,
    terminal_event as build_private_terminal_event,
    unwrap_identity,
    validate_executor_response as validate_private_executor_response,
)
from replay_orchestrator import (  # noqa: E402
    FAILURE_REASONS,
    RETRYABLE_FAILURES,
    ReplayError,
    canonical_archive_path,
    config_digest,
    replay_task_id,
    validate_execution_plan as validate_private_execution_plan,
    validate_execution_profile,
    validate_measurement_config,
)
from results_schema import ResultsSchemaError  # noqa: E402
from results_schema import result_id as stable_result_id  # noqa: E402

HistoricalPrivateReplayControllerError = HistoricalReplayControllerError
AUTHORITY_EVENT_TYPE = "historical_archive_result.replay_authorized"
AUTHORITY_PATH = re.compile(r"evidence/private-replay/plans/[0-9a-f]{64}\.json\Z")
PROFILE_PATH = re.compile(r"evidence/private-replay/profiles/[0-9a-f]{64}\.json\Z")
RECONFIGURATION_PATH = re.compile(
    r"evidence/private-replay/reconfigurations/[0-9a-f]{64}\.json\Z"
)
ACCOUNT_ID = re.compile(r"[0-9a-f]{32}\Z")

TASK_FIELDS = {
    "replay_task_id", "result_id", "historical_accepted_at", "owner_login",
    "declared_model", "problem_id", "statement_revision", "results_repository",
    "results_commit", "results_path", "result_file_sha256", "result_tree_digest",
    "source_visibility", "crosswalk_repository", "crosswalk_commit", "crosswalk_path",
    "crosswalk_sha256", "crosswalk_entry_sha256", "archive_plan_entry_sha256",
    "archive_submission_id", "archive_schema_version", "archive_repository",
    "archive_commit", "archive_path", "archive_sidecar_path",
    "archive_ciphertext_sha256", "archive_sidecar_sha256",
    "archive_key_envelope_sha256", "archive_plaintext_tar_sha256",
    "archive_plaintext_tar_size", "benchmark_repository", "benchmark_commit",
    "toolchain", "lean_toolchain_blob_sha256", "workflow_run_identity_sha256",
    "authority_repository", "authority_commit", "authority_path", "authority_sha256",
    "authority_entry_sha256", "authority_event_id", "authorized_at",
    "qualification_repository", "qualification_commit", "qualification_path",
    "qualification_sha256", "qualification_event_id", "qualified_at", "checker",
    "measurement_config_digest", "execution_profile_digest", "status", "attempt",
    "event_id", "occurred_at",
}
RECONFIGURATION_FIELDS = {
    "reconfiguration_event_id", "reconfigured_at", "superseded_qualification_event_id",
    "reconfiguration_repository", "reconfiguration_commit", "reconfiguration_path",
    "reconfiguration_sha256",
}
PLAN_ENTRY_FIELDS = {
    "result_id", "historical_accepted_at", "owner_login", "declared_model",
    "problem_id", "statement_revision", "benchmark_commit", "results_path",
    "result_file_sha256", "result_tree_digest", "crosswalk_entry_sha256",
    "classification", "archive_submission_id", "archive_plan_entry_sha256",
    "replay_profile_status", "execution_profile_digest",
}
PROFILE_CORE_FIELDS = {
    "benchmark_commit", "benchmark_tree", "toolchain", "lean_toolchain_blob_sha256",
    "checker", "measurement_config_digest", "measurement_config", "execution_profile",
    "execution_profile_digest",
}
PROFILE_FIELDS = PROFILE_CORE_FIELDS | {
    "schema_version", "kind", "qualification_status", "image_family",
    "registry_repository", "registry_manifest_digest", "image_source_repository",
    "image_source_commit", "source_blobs", "qualification",
}
SOURCE_BLOB_PATHS = {
    "dockerfile": "Dockerfile.historical-private-replay",
    "dockerignore": "Dockerfile.historical-private-replay.dockerignore",
    "profile_matrix": "configuration/historical-private-replay-image-matrix-v1.json",
    "evaluator": "scripts/evaluate_submission.py",
    "orchestrator": "scripts/replay_orchestrator.py",
    "layer_preparation": "scripts/prepare_historical_image_layers.py",
    "runtime_helper": "server/replay-image/replay-authoritative",
    "measurement_helper": "server/replay-image/replay-measure",
    "comparator_patch": "server/replay-image/comparator-71b52-phase-metrics.patch",
    "age_file_key_go_mod": "server/age-file-key/go.mod",
    "age_file_key_go_sum": "server/age-file-key/go.sum",
    "age_file_key_main": "server/age-file-key/main.go",
}
QUALIFICATION_FIELDS = {
    "workflow_repository", "workflow_commit", "workflow_path", "workflow_sha256",
    "workflow_run_id", "workflow_run_attempt", "private_archive_probe", "network_probe",
}
QUALIFICATION_WORKFLOW_PATH = ".github/workflows/historical-private-image-qualification.yml"
STATE_QUEUE_PATH = "state-views/historical-private-replay-queue.json"
STATE_MINIMUM_COMMITS = {
    "production": "3dcf596b696b9f1f11de2e3c6127664fd0504884",
    "staging": "23852beaeb059c88caf043d22dad19b211c377b2",
}
REPOSITORY_REMOTES = {
    "leanprover/lean-eval-submissions": {
        "https://github.com/leanprover/lean-eval-submissions",
        "https://github.com/leanprover/lean-eval-submissions.git",
    },
    "leanprover/lean-eval-state": {
        "git@github.com:leanprover/lean-eval-state.git",
        "https://github.com/leanprover/lean-eval-state.git",
    },
    "leanprover/lean-eval-state-staging": {
        "git@github.com:leanprover/lean-eval-state-staging.git",
        "https://github.com/leanprover/lean-eval-state-staging.git",
    },
    "leanprover/lean-eval-audit": {
        "git@github.com:leanprover/lean-eval-audit.git",
        "https://github.com/leanprover/lean-eval-audit.git",
    },
}
MAX_ARCHIVE_BYTES = 11 * 1024 * 1024
MAX_STATE_EXPORT_BYTES = 512 * 1024 * 1024


def _git_text(root: pathlib.Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalPrivateReplayControllerError(
            "exact protected Git checkout proof failed"
        ) from error


def _verify_checkout(
    root: pathlib.Path,
    repository: str,
    *,
    minimum_commit: str | None = None,
) -> str:
    try:
        checkout = pathlib.Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve()
    except OSError as error:
        raise HistoricalPrivateReplayControllerError("Git checkout is unavailable") from error
    if checkout != root.resolve() or repository not in REPOSITORY_REMOTES:
        raise HistoricalPrivateReplayControllerError("Git checkout root or repository is invalid")
    remotes = _git_text(root, "config", "--get-all", "remote.origin.url").splitlines()
    if len(remotes) != 1 or remotes[0] not in REPOSITORY_REMOTES[repository]:
        raise HistoricalPrivateReplayControllerError("Git origin identity differs")
    if _git_text(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise HistoricalPrivateReplayControllerError("protected Git checkout is not clean")
    head = _match(COMMIT, _git_text(root, "rev-parse", "HEAD"), "protected Git HEAD")
    upstream = _match(
        COMMIT,
        _git_text(root, "rev-parse", "refs/remotes/origin/main"),
        "protected upstream main",
    )
    if head != upstream:
        raise HistoricalPrivateReplayControllerError(
            "protected Git HEAD differs from origin/main"
        )
    if minimum_commit is not None:
        _match(COMMIT, minimum_commit, "minimum protected commit")
        try:
            subprocess.run(
                ["git", "-C", str(root), "merge-base", "--is-ancestor", minimum_commit, head],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise HistoricalPrivateReplayControllerError(
                "protected Git HEAD lacks the required ancestry"
            ) from error
    return head


def refresh_protected_state(state_root: pathlib.Path) -> str:
    """Fetch and fast-forward one clean protected State checkout.

    The State writer pushes through an explicit authenticated URL, which does
    not update ``refs/remotes/origin/main``.  Consequently this function first
    permits a clean local HEAD that is ahead of the stale tracking ref, then
    fetches the configured read-only origin and proves that HEAD is protected
    ancestry of the newly observed remote main before fast-forwarding.
    """

    environment = _state_environment(state_root)
    repository = _state_repository(environment)
    minimum = STATE_MINIMUM_COMMITS[environment]
    try:
        checkout = pathlib.Path(
            _git_text(state_root, "rev-parse", "--show-toplevel")
        ).resolve()
    except OSError as error:
        raise HistoricalPrivateReplayControllerError(
            "State Git checkout is unavailable"
        ) from error
    remotes = _git_text(
        state_root, "config", "--get-all", "remote.origin.url"
    ).splitlines()
    if (
        checkout != state_root.resolve()
        or len(remotes) != 1
        or remotes[0] not in REPOSITORY_REMOTES[repository]
        or _git_text(state_root, "status", "--porcelain=v1", "--untracked-files=all")
    ):
        raise HistoricalPrivateReplayControllerError(
            "protected State checkout identity or cleanliness differs"
        )
    local_head = _match(
        COMMIT, _git_text(state_root, "rev-parse", "HEAD"), "local State HEAD"
    )
    try:
        subprocess.run(
            [
                "git", "-C", str(state_root), "merge-base", "--is-ancestor",
                minimum, local_head,
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git", "-C", str(state_root), "fetch", "--no-tags", "origin",
                "refs/heads/main:refs/remotes/origin/main",
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        remote_head = _match(
            COMMIT,
            _git_text(state_root, "rev-parse", "refs/remotes/origin/main"),
            "fresh protected State main",
        )
        subprocess.run(
            [
                "git", "-C", str(state_root), "merge-base", "--is-ancestor",
                local_head, remote_head,
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git", "-C", str(state_root), "merge", "--ff-only",
                "refs/remotes/origin/main",
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HistoricalPrivateReplayControllerError(
            "protected State refresh did not prove one forward-only main history"
        ) from error
    return _verify_checkout(state_root, repository, minimum_commit=minimum)


def _verify_ancestor_of_upstream(root: pathlib.Path, commit: str, label: str) -> None:
    _match(COMMIT, commit, label)
    try:
        subprocess.run(
            [
                "git", "-C", str(root), "merge-base", "--is-ancestor",
                commit, "refs/remotes/origin/main",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalPrivateReplayControllerError(
            f"{label} is not protected upstream ancestry"
        ) from error


def _state_environment(state_root: pathlib.Path) -> str:
    try:
        state_value = json.loads((state_root / "state.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise HistoricalPrivateReplayControllerError("State identity is unavailable") from error
    environment = state_value.get("environment") if isinstance(state_value, dict) else None
    if environment not in {"staging", "production"}:
        raise HistoricalPrivateReplayControllerError("State environment is invalid")
    return environment


def _materialize_state_queue(
    state_root: pathlib.Path, environment: str
) -> tuple[dict[str, Any], bytes]:
    if _state_environment(state_root) != environment:
        raise HistoricalPrivateReplayControllerError(
            "historical State environment changed across exact history"
        )
    state_script = state_root / "scripts/state.py"
    if state_script.is_symlink() or not state_script.is_file():
        raise HistoricalPrivateReplayControllerError("protected State program is unavailable")
    with tempfile.TemporaryDirectory() as directory:
        views = pathlib.Path(directory) / "state-views"
        commands = (
            [sys.executable, str(state_script), "--root", str(state_root), "validate"],
            [
                sys.executable, str(state_script), "--root", str(state_root),
                "materialize", "--output", str(views),
            ],
        )
        try:
            for command in commands:
                subprocess.run(
                    command,
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=300,
                )
        except (OSError, subprocess.SubprocessError) as error:
            raise HistoricalPrivateReplayControllerError(
                "exact protected State validation/materialization failed"
            ) from error
        queue_path = views / pathlib.PurePosixPath(STATE_QUEUE_PATH).name
        queue, raw = _load_state_canonical(queue_path, "materialized historical private queue")
    validate_queue(queue)
    if queue["environment"] != environment or state_canonical_bytes(queue) != raw:
        raise HistoricalPrivateReplayControllerError(
            "materialized historical private queue differs from protected State"
        )
    return queue, raw


def _export_exact_commit(
    repository_root: pathlib.Path, commit: str, destination: pathlib.Path
) -> None:
    """Export regular Git blobs without trusting archive paths or symlinks."""

    try:
        listing = subprocess.run(
            ["git", "-C", str(repository_root), "ls-tree", "-rz", "--full-tree", commit],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalPrivateReplayControllerError(
            "exact historical State tree is unavailable"
        ) from error
    total = 0
    for record in listing.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_type, _ = metadata.decode("ascii").split(" ")
            path = encoded_path.decode("utf-8")
        except (ValueError, UnicodeError) as error:
            raise HistoricalPrivateReplayControllerError(
                "historical State tree entry is malformed"
            ) from error
        pure = pathlib.PurePosixPath(path)
        if (
            object_type != "blob"
            or mode not in {"100644", "100755"}
            or pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise HistoricalPrivateReplayControllerError(
                "historical State tree contains an unsafe entry"
            )
        raw = _git_blob(repository_root, commit, path)
        total += len(raw)
        if total > MAX_STATE_EXPORT_BYTES:
            raise HistoricalPrivateReplayControllerError(
                "historical State tree exceeds its export boundary"
            )
        target = destination.joinpath(*pure.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_bytes(target, raw)
        target.chmod(0o700 if mode == "100755" else 0o600)


def load_state_queue_at_commit(
    state_root: pathlib.Path,
    commit: str,
    expected_repository: str,
) -> tuple[dict[str, Any], bytes, str]:
    """Rederive a queue from one protected ancestor with its own exact code."""

    environment = _state_environment(state_root)
    repository = _state_repository(environment)
    if repository != expected_repository:
        raise HistoricalPrivateReplayControllerError(
            "historical plan State repository differs from protected checkout"
        )
    minimum = STATE_MINIMUM_COMMITS[environment]
    current_head = _verify_checkout(state_root, repository, minimum_commit=minimum)
    exact_commit = _match(COMMIT, commit, "historical plan State head")
    for ancestor, descendant in (
        (minimum, exact_commit),
        (exact_commit, current_head),
    ):
        try:
            subprocess.run(
                [
                    "git", "-C", str(state_root), "merge-base", "--is-ancestor",
                    ancestor, descendant,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise HistoricalPrivateReplayControllerError(
                "historical plan State head lacks protected ancestry"
            ) from error
    with tempfile.TemporaryDirectory() as directory:
        exported = pathlib.Path(directory) / "state"
        exported.mkdir()
        _export_exact_commit(state_root, exact_commit, exported)
        queue, raw = _materialize_state_queue(exported, environment)
    return queue, raw, current_head


def load_state_queue(
    state_root: pathlib.Path,
) -> tuple[dict[str, Any], bytes, str]:
    environment = _state_environment(state_root)
    repository = _state_repository(environment)
    head = _verify_checkout(
        state_root,
        repository,
        minimum_commit=STATE_MINIMUM_COMMITS[environment],
    )
    queue, raw = _materialize_state_queue(state_root, environment)
    return queue, raw, head


def _parse_json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_constant,
        )
    except (UnicodeError, json.JSONDecodeError, HistoricalPrivateReplayControllerError) as error:
        raise HistoricalPrivateReplayControllerError(
            f"{label} is not one strict UTF-8 JSON object"
        ) from error
    return _object(value, label)


def _model(value: Any, label: str) -> str:
    try:
        size = len(value.encode("utf-8")) if isinstance(value, str) else 0
    except UnicodeEncodeError as error:
        raise HistoricalPrivateReplayControllerError(f"{label} is invalid") from error
    if (
        not isinstance(value, str) or not value or size > 256
        or any(ord(character) <= 0x1F or ord(character) == 0x7F for character in value)
    ):
        raise HistoricalPrivateReplayControllerError(f"{label} is invalid")
    return value


def _validate_task(value: Any, index: int) -> dict[str, Any]:
    label = f"historical private queue tasks[{index}]"
    task = _object(value, label)
    expected = set(TASK_FIELDS)
    if task.get("status") == "failed":
        expected |= {"reason_code", "retryable"}
    if "reconfiguration_event_id" in task:
        expected |= RECONFIGURATION_FIELDS
    _fields(task, expected, label)

    result = _match(RESULT_ID, task["result_id"], f"{label}.result_id")
    owner = _match(LOGIN, task["owner_login"], f"{label}.owner_login")
    model = _model(task["declared_model"], f"{label}.declared_model")
    problem = _match(PROBLEM, task["problem_id"], f"{label}.problem_id")
    revision = _integer(task["statement_revision"], f"{label}.statement_revision", 1)
    try:
        expected_result = stable_result_id(owner, model, problem, revision)
    except ResultsSchemaError as error:
        raise HistoricalPrivateReplayControllerError(
            f"{label}.result identity is invalid"
        ) from error
    if result != expected_result:
        raise HistoricalPrivateReplayControllerError(
            f"{label}.result_id differs from its identity"
        )
    replay = _match(REPLAY_ID, task["replay_task_id"], f"{label}.replay_task_id")
    submission = _match(
        UUID7, task["archive_submission_id"], f"{label}.archive_submission_id"
    )
    _timestamp(task["historical_accepted_at"], f"{label}.historical_accepted_at", milliseconds=False)
    for field in ("authorized_at", "qualified_at", "occurred_at"):
        _timestamp(task[field], f"{label}.{field}")
    for field in ("authority_event_id", "qualification_event_id", "event_id"):
        _match(UUID7, task[field], f"{label}.{field}")

    fixed = {
        "results_repository": "leanprover/lean-eval-submissions",
        "crosswalk_repository": "leanprover/lean-eval-submissions",
        "authority_repository": "leanprover/lean-eval-submissions",
        "qualification_repository": "leanprover/lean-eval-submissions",
        "archive_repository": "leanprover/lean-eval-audit",
        "benchmark_repository": "leanprover/lean-eval",
        "source_visibility": "private",
        "archive_schema_version": 3,
        "checker": "nanoda",
    }
    if any(task[field] != expected_value for field, expected_value in fixed.items()):
        raise HistoricalPrivateReplayControllerError(
            f"{label} crosses the archived-private replay boundary"
        )
    for field in (
        "results_commit", "crosswalk_commit", "archive_commit", "benchmark_commit",
        "authority_commit", "qualification_commit",
    ):
        _match(COMMIT, task[field], f"{label}.{field}")
    for field in (
        "result_file_sha256", "result_tree_digest", "crosswalk_sha256",
        "crosswalk_entry_sha256", "archive_plan_entry_sha256",
        "archive_ciphertext_sha256", "archive_sidecar_sha256",
        "archive_key_envelope_sha256", "archive_plaintext_tar_sha256",
        "lean_toolchain_blob_sha256", "workflow_run_identity_sha256",
        "authority_sha256", "authority_entry_sha256", "qualification_sha256",
        "measurement_config_digest", "execution_profile_digest",
    ):
        _match(DIGEST, task[field], f"{label}.{field}")
    _match(TOOLCHAIN, task["toolchain"], f"{label}.toolchain")
    results_path = _match(RESULTS_PATH, task["results_path"], f"{label}.results_path")
    if results_path != f"results/{owner}.json":
        raise HistoricalPrivateReplayControllerError(f"{label}.results_path differs from owner")
    if task["crosswalk_path"] != (
        "evidence/historical-replay/private-crosswalks/"
        f"{task['crosswalk_sha256']}.json"
    ):
        raise HistoricalPrivateReplayControllerError(f"{label}.crosswalk_path differs from digest")
    authority_path = _match(AUTHORITY_PATH, task["authority_path"], f"{label}.authority_path")
    if authority_path != f"evidence/private-replay/plans/{task['authority_sha256']}.json":
        raise HistoricalPrivateReplayControllerError(f"{label}.authority_path differs from digest")
    profile_path = _match(PROFILE_PATH, task["qualification_path"], f"{label}.qualification_path")
    if profile_path != f"evidence/private-replay/profiles/{task['execution_profile_digest']}.json":
        raise HistoricalPrivateReplayControllerError(f"{label}.qualification_path differs from profile")
    archive_path = canonical_archive_path(submission)
    if (
        task["archive_path"] != archive_path
        or task["archive_sidecar_path"] != archive_path.removesuffix(".tar.age") + ".json"
    ):
        raise HistoricalPrivateReplayControllerError(f"{label}.archive paths differ from UUID")
    size = _integer(task["archive_plaintext_tar_size"], f"{label}.archive_plaintext_tar_size", 1)
    if size > 10 * 1024 * 1024:
        raise HistoricalPrivateReplayControllerError(f"{label}.archive exceeds executor limit")
    attempt = _integer(task["attempt"], f"{label}.attempt")
    if attempt >= MAX_REPLAY_ATTEMPTS:
        raise HistoricalPrivateReplayControllerError(f"{label}.attempt has no remaining bounded retry")
    if task["status"] == "queued":
        if attempt != 0 and "reconfiguration_event_id" not in task:
            raise HistoricalPrivateReplayControllerError(f"{label} initial attempt is not zero")
    elif task["status"] == "failed":
        if attempt < 1 or task["retryable"] is not True or task["reason_code"] not in RETRYABLE_FAILURES:
            raise HistoricalPrivateReplayControllerError(f"{label} failed task is not retryable")
    else:
        raise HistoricalPrivateReplayControllerError(f"{label}.status is not queueable")
    if replay != replay_task_id(result, task["measurement_config_digest"]):
        raise HistoricalPrivateReplayControllerError(f"{label}.replay_task_id differs from identity")
    if "reconfiguration_event_id" in task:
        for field in ("reconfiguration_event_id", "superseded_qualification_event_id"):
            _match(UUID7, task[field], f"{label}.{field}")
        _timestamp(task["reconfigured_at"], f"{label}.reconfigured_at")
        if task["reconfiguration_repository"] != "leanprover/lean-eval-submissions":
            raise HistoricalPrivateReplayControllerError(f"{label}.reconfiguration repository is invalid")
        _match(COMMIT, task["reconfiguration_commit"], f"{label}.reconfiguration_commit")
        path = _match(RECONFIGURATION_PATH, task["reconfiguration_path"], f"{label}.reconfiguration_path")
        digest = _match(DIGEST, task["reconfiguration_sha256"], f"{label}.reconfiguration_sha256")
        if path != f"evidence/private-replay/reconfigurations/{digest}.json":
            raise HistoricalPrivateReplayControllerError(f"{label}.reconfiguration path differs")
    return task


def validate_queue(value: Any) -> dict[str, Any]:
    queue = _object(value, "historical private replay queue")
    _fields(
        queue,
        {"schema_version", "environment", "source_event_count", "source_digest", "tasks"},
        "historical private replay queue",
    )
    if queue["schema_version"] != 1 or queue["environment"] not in {"staging", "production"}:
        raise HistoricalPrivateReplayControllerError("historical private queue identity is invalid")
    _integer(queue["source_event_count"], "historical private source_event_count", 1)
    _match(DIGEST, queue["source_digest"], "historical private source_digest")
    if not isinstance(queue["tasks"], list):
        raise HistoricalPrivateReplayControllerError("historical private queue tasks must be an array")
    tasks = [_validate_task(task, index) for index, task in enumerate(queue["tasks"])]
    identities = [task["replay_task_id"] for task in tasks]
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise HistoricalPrivateReplayControllerError("historical private queue tasks are not unique and sorted")
    return queue


def _validate_profile(value: Any, task: dict[str, Any]) -> dict[str, Any]:
    profile = _object(value, "private replay profile")
    _fields(profile, PROFILE_FIELDS, "private replay profile")
    try:
        validate_execution_profile(profile["execution_profile"])
        validate_measurement_config(profile["measurement_config"])
    except ReplayError as error:
        raise HistoricalPrivateReplayControllerError(
            "private replay profile configuration is invalid"
        ) from error
    expected_execution = config_digest(
        "lean-eval-replay-execution-profile-v1", profile["execution_profile"]
    )
    expected_measurement = config_digest(
        "lean-eval-replay-measurement-config-v1", profile["measurement_config"]
    )
    if (
        profile["schema_version"] != 1
        or profile["kind"] != "historical_private_replay_profile_qualification"
        or profile["qualification_status"] != "qualified"
        or profile["image_family"] != "lean-eval-authoritative-private-replay-v1"
        or profile["registry_repository"] != "lean-eval-authoritative"
        or profile["image_source_repository"] != "leanprover/lean-eval-submissions"
        or profile["registry_manifest_digest"]
        != profile["execution_profile"]["vm_image_digest"]
        or profile["execution_profile_digest"] != expected_execution
        or profile["measurement_config_digest"] != expected_measurement
        or profile["execution_profile_digest"] != task["execution_profile_digest"]
        or profile["measurement_config_digest"] != task["measurement_config_digest"]
        or profile["benchmark_commit"] != task["benchmark_commit"]
        or profile["toolchain"] != task["toolchain"]
        or profile["lean_toolchain_blob_sha256"] != task["lean_toolchain_blob_sha256"]
        or profile["execution_profile"]["toolchain"] != task["toolchain"]
        or profile["checker"] != "nanoda"
    ):
        raise HistoricalPrivateReplayControllerError(
            "private replay profile differs from the queued task"
        )
    _match(COMMIT, profile["benchmark_tree"], "private replay profile benchmark_tree")
    _match(COMMIT, profile["image_source_commit"], "private replay image source commit")
    blobs = _object(profile["source_blobs"], "private replay source blobs")
    _fields(blobs, set(SOURCE_BLOB_PATHS), "private replay source blobs")
    for name, expected_path in SOURCE_BLOB_PATHS.items():
        blob = _object(blobs[name], f"private replay source_blobs.{name}")
        _fields(blob, {"path", "sha256"}, f"private replay source_blobs.{name}")
        if blob["path"] != expected_path:
            raise HistoricalPrivateReplayControllerError(
                f"private replay source_blobs.{name} path is invalid"
            )
        _match(DIGEST, blob["sha256"], f"private replay source_blobs.{name}.sha256")
    qualification = _object(profile["qualification"], "private replay qualification")
    _fields(qualification, QUALIFICATION_FIELDS, "private replay qualification")
    if (
        qualification["workflow_repository"] != "leanprover/lean-eval-submissions"
        or qualification["workflow_path"] != QUALIFICATION_WORKFLOW_PATH
        or qualification["private_archive_probe"]
        != {
            "archive_expectation_schema_version": 2,
            "key_material_type": "age-file-key-v1",
            "runner_entrypoint": "/opt/lean-eval/replay-authoritative",
            "status": "passed",
        }
        or qualification["network_probe"] != "blocked"
    ):
        raise HistoricalPrivateReplayControllerError(
            "private replay qualification outcome is invalid"
        )
    _match(COMMIT, qualification["workflow_commit"], "private replay workflow commit")
    _match(DIGEST, qualification["workflow_sha256"], "private replay workflow sha256")
    _integer(qualification["workflow_run_id"], "private replay workflow run_id", 1)
    _integer(qualification["workflow_run_attempt"], "private replay workflow run_attempt", 1)
    return profile


def _verify_ancestor(repository_root: pathlib.Path, ancestor: str, descendant: str) -> None:
    try:
        subprocess.run(
            ["git", "-C", str(repository_root), "merge-base", "--is-ancestor", ancestor, descendant],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalPrivateReplayControllerError(
            "private replay provenance ancestry is invalid"
        ) from error


def _verify_profile_provenance(
    repository_root: pathlib.Path, task: dict[str, Any], profile: dict[str, Any]
) -> None:
    image_commit = profile["image_source_commit"]
    workflow_commit = profile["qualification"]["workflow_commit"]
    profile_commit = task["qualification_commit"]
    _verify_ancestor(repository_root, image_commit, profile_commit)
    _verify_ancestor(repository_root, workflow_commit, profile_commit)
    _verify_ancestor(repository_root, profile_commit, task["authority_commit"])
    for name, binding in profile["source_blobs"].items():
        if sha256_bytes(_git_blob(repository_root, image_commit, binding["path"])) != binding["sha256"]:
            raise HistoricalPrivateReplayControllerError(
                f"private replay source blob {name} differs from qualification"
            )
    workflow = profile["qualification"]
    if sha256_bytes(
        _git_blob(repository_root, workflow_commit, workflow["workflow_path"])
    ) != workflow["workflow_sha256"]:
        raise HistoricalPrivateReplayControllerError(
            "private replay qualification workflow differs from exact Git blob"
        )
    try:
        public_paths = subprocess.run(
            [
                "git", "-C", str(repository_root), "ls-tree", "-r", "--name-only",
                task["authority_commit"], "--", "evidence/public-replay/profiles",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalPrivateReplayControllerError(
            "committed public replay profile inventory is unavailable"
        ) from error
    manifest = profile["registry_manifest_digest"]
    for path in public_paths:
        public = _parse_canonical(
            _git_blob(repository_root, task["authority_commit"], path),
            "committed public replay profile",
        )
        if public.get("registry_manifest_digest") == manifest:
            raise HistoricalPrivateReplayControllerError(
                "private replay image is also a committed public replay image"
            )


def _validate_authority(value: Any, task: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    authority = _object(value, "private replay authority plan")
    _fields(
        authority,
        {
            "schema_version", "kind", "results", "crosswalk", "classification_counts",
            "replay_readiness_counts", "profiles", "entries",
        },
        "private replay authority plan",
    )
    if authority["schema_version"] != 1 or authority["kind"] != "historical_private_replay_plan":
        raise HistoricalPrivateReplayControllerError("private replay authority identity is invalid")
    results = _object(authority["results"], "private replay authority results")
    crosswalk = _object(authority["crosswalk"], "private replay authority crosswalk")
    if (
        results.get("repository") != task["results_repository"]
        or results.get("commit") != task["results_commit"]
        or crosswalk != {
            "repository": task["crosswalk_repository"],
            "commit": task["crosswalk_commit"],
            "path": task["crosswalk_path"],
            "sha256": task["crosswalk_sha256"],
        }
    ):
        raise HistoricalPrivateReplayControllerError("private replay authority roots differ")
    _match(DIGEST, results.get("store_sha256"), "private replay results store_sha256")
    entries = authority["entries"]
    if not isinstance(entries, list) or not entries:
        raise HistoricalPrivateReplayControllerError("private replay authority entries are invalid")
    identities = [entry.get("result_id") for entry in entries if isinstance(entry, dict)]
    if len(identities) != len(entries) or identities != sorted(set(identities)):
        raise HistoricalPrivateReplayControllerError("private replay authority entries are not sorted")
    selected = [entry for entry in entries if entry.get("result_id") == task["result_id"]]
    if len(selected) != 1:
        raise HistoricalPrivateReplayControllerError("private replay authority selection is ambiguous")
    entry = _object(selected[0], "private replay authority entry")
    _fields(entry, PLAN_ENTRY_FIELDS, "private replay authority entry")
    expected_entry = {
        "result_id": task["result_id"],
        "historical_accepted_at": task["historical_accepted_at"],
        "owner_login": task["owner_login"],
        "declared_model": task["declared_model"],
        "problem_id": task["problem_id"],
        "statement_revision": task["statement_revision"],
        "benchmark_commit": task["benchmark_commit"],
        "results_path": task["results_path"],
        "result_file_sha256": task["result_file_sha256"],
        "result_tree_digest": task["result_tree_digest"],
        "crosswalk_entry_sha256": task["crosswalk_entry_sha256"],
        "classification": "bound",
        "archive_submission_id": task["archive_submission_id"],
        "archive_plan_entry_sha256": task["archive_plan_entry_sha256"],
        "replay_profile_status": "profile_qualified",
        "execution_profile_digest": task["execution_profile_digest"],
    }
    if entry != expected_entry or entry_sha256(entry) != task["authority_entry_sha256"]:
        raise HistoricalPrivateReplayControllerError("private replay authority entry differs")
    profiles = _object(authority["profiles"], "private replay authority profiles")
    expected_profile = {
        key: copy.deepcopy(profile[key])
        for key in PROFILE_CORE_FIELDS
        if key != "execution_profile_digest"
    }
    expected_profile["private_profile"] = {
        "repository": task["qualification_repository"],
        "commit": task["qualification_commit"],
        "path": task["qualification_path"],
        "sha256": task["qualification_sha256"],
    }
    if profiles.get(task["execution_profile_digest"]) != expected_profile:
        raise HistoricalPrivateReplayControllerError("private replay authority profile differs")
    return authority


def load_reviewed_inputs(
    repository_root: pathlib.Path, task_value: Any
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes]:
    task = _validate_task(task_value, 0)
    _verify_checkout(repository_root, "leanprover/lean-eval-submissions")
    for field in ("authority_commit", "qualification_commit"):
        _verify_ancestor_of_upstream(repository_root, task[field], field)
    authority_raw = _git_blob(repository_root, task["authority_commit"], task["authority_path"])
    profile_raw = _git_blob(repository_root, task["qualification_commit"], task["qualification_path"])
    if sha256_bytes(authority_raw) != task["authority_sha256"]:
        raise HistoricalPrivateReplayControllerError("private authority Git blob differs from locator")
    if sha256_bytes(profile_raw) != task["qualification_sha256"]:
        raise HistoricalPrivateReplayControllerError("private profile Git blob differs from digest")
    authority = _parse_canonical(authority_raw, "exact private authority Git blob")
    profile = _parse_canonical(profile_raw, "exact private profile Git blob")
    profile = _validate_profile(profile, task)
    _verify_ancestor_of_upstream(
        repository_root, profile["image_source_commit"], "image_source_commit"
    )
    _verify_ancestor_of_upstream(
        repository_root,
        profile["qualification"]["workflow_commit"],
        "qualification.workflow_commit",
    )
    _verify_profile_provenance(repository_root, task, profile)
    _validate_authority(authority, task, profile)
    return authority, authority_raw, profile, profile_raw


def load_archive_inputs(
    audit_root: pathlib.Path, task_value: Any
) -> tuple[dict[str, Any], bytes, bytes, dict[str, Any]]:
    """Load and prove one immutable audit archive binding from protected Git."""

    task = _validate_task(task_value, 0)
    _verify_checkout(audit_root, "leanprover/lean-eval-audit")
    _verify_ancestor_of_upstream(audit_root, task["archive_commit"], "archive_commit")
    sidecar_raw = _git_blob(
        audit_root, task["archive_commit"], task["archive_sidecar_path"]
    )
    ciphertext = _git_blob(audit_root, task["archive_commit"], task["archive_path"])
    if not ciphertext or len(ciphertext) > MAX_ARCHIVE_BYTES:
        raise HistoricalPrivateReplayControllerError(
            "exact historical private ciphertext exceeds its size limit"
        )
    sidecar = _parse_json_object(sidecar_raw, "exact historical private sidecar Git blob")
    try:
        _validate_sidecar(sidecar, finalized=True)
        envelope = validate_envelope(sidecar.get("key_envelope"))
    except (SystemExit, ValueError) as error:
        raise HistoricalPrivateReplayControllerError(
            "exact historical private sidecar is invalid"
        ) from error
    ciphertext_sha256 = sha256_bytes(ciphertext)
    envelope_sha256 = sha256_bytes(canonical_compact(envelope))
    expected = {
        "submission_id": task["archive_submission_id"],
        "schema_version": task["archive_schema_version"],
        "benchmark_commit": task["benchmark_commit"],
        "sha256_ciphertext": task["archive_ciphertext_sha256"],
        "sha256_plaintext_tar": task["archive_plaintext_tar_sha256"],
        "size_bytes_plaintext_tar": task["archive_plaintext_tar_size"],
    }
    if (
        any(sidecar.get(field) != value for field, value in expected.items())
        or sidecar.get("size_bytes_ciphertext") != len(ciphertext)
        or ciphertext_sha256 != task["archive_ciphertext_sha256"]
        or sha256_bytes(sidecar_raw) != task["archive_sidecar_sha256"]
        or envelope_sha256 != task["archive_key_envelope_sha256"]
        or envelope["submission_id"] != task["archive_submission_id"]
        or envelope["archive_ciphertext_sha256"] != ciphertext_sha256
        or envelope.get("schema_version") != 2
    ):
        raise HistoricalPrivateReplayControllerError(
            "audit archive, sidecar, envelope, and queue task differ"
        )
    binding = {
        "repository": task["archive_repository"],
        "commit": task["archive_commit"],
        "archive_path": task["archive_path"],
        "sidecar_path": task["archive_sidecar_path"],
        "ciphertext_sha256": ciphertext_sha256,
        "sidecar_sha256": sha256_bytes(sidecar_raw),
        "key_envelope_sha256": envelope_sha256,
        "plaintext_tar_sha256": sidecar["sha256_plaintext_tar"],
        "plaintext_tar_size": sidecar["size_bytes_plaintext_tar"],
    }
    return sidecar, sidecar_raw, ciphertext, binding


def _execution_plan(task: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    request = {
        "schema_version": 1,
        "replay_task_id": task["replay_task_id"],
        "attempt": task["attempt"] + 1,
        "source": {
            "visibility": "private",
            "archive": {
                "schema_version": 1,
                "submission_id": task["archive_submission_id"],
                "archive_repository": task["archive_repository"],
                "archive_commit": task["archive_commit"],
                "archive_path": task["archive_path"],
                "archive_ciphertext_sha256": task["archive_ciphertext_sha256"],
                "encrypted": True,
            },
        },
        "benchmark": {
            "repository": task["benchmark_repository"],
            "commit": task["benchmark_commit"],
            "toolchain": task["toolchain"],
        },
        "result": {
            "result_id": task["result_id"],
            "submission_id": task["archive_submission_id"],
            "problem_id": task["problem_id"],
            "statement_revision": task["statement_revision"],
            "commit": task["results_commit"],
            "tree_digest": task["result_tree_digest"],
        },
        "checker": "nanoda",
        "execution_profile_digest": task["execution_profile_digest"],
        "measurement_config_digest": task["measurement_config_digest"],
        "execution_profile": profile["execution_profile"],
        "measurement_config": profile["measurement_config"],
        "network": {
            "fetch_phase": "controller_pinned_archive_only",
            "untrusted_execution_phase": "disabled",
        },
        "untrusted_environment": {},
    }
    value = {
        "schema_version": 1,
        "kind": "execution",
        "started_transition": {
            "event_type": "replay.started",
            "subject_id": task["replay_task_id"],
            "causation_event_id": task["event_id"],
            "payload": {
                "attempt": task["attempt"] + 1,
                "runner_profile": profile["execution_profile"]["runner_profile"],
            },
        },
        "request": request,
    }
    try:
        return validate_private_execution_plan(value)
    except ReplayError as error:
        raise HistoricalPrivateReplayControllerError("private executor adaptation is invalid") from error


def _state_repository(environment: str) -> str:
    return (
        "leanprover/lean-eval-state-staging"
        if environment == "staging"
        else "leanprover/lean-eval-state"
    )


def _plan_next(
    queue_value: Any,
    queue_raw: bytes,
    state_head: str,
    authority_value: Any | None = None,
    authority_raw: bytes | None = None,
    profile_value: Any | None = None,
    profile_raw: bytes | None = None,
    archive_binding_value: Any | None = None,
) -> dict[str, Any]:
    queue = validate_queue(queue_value)
    if not isinstance(queue_raw, bytes) or state_canonical_bytes(queue) != queue_raw:
        raise HistoricalPrivateReplayControllerError(
            "historical private queue raw bytes differ from its canonical value"
        )
    state = {
        "repository": _state_repository(queue["environment"]),
        "expected_head": _match(COMMIT, state_head, "expected State head"),
        "queue_environment": queue["environment"],
        "queue_source_event_count": queue["source_event_count"],
        "queue_source_digest": queue["source_digest"],
    }
    if not queue["tasks"]:
        return {"schema_version": 1, "kind": "empty", "state": state}
    if any(
        value is None
        for value in (
            authority_value, authority_raw, profile_value, profile_raw,
            archive_binding_value,
        )
    ):
        raise HistoricalPrivateReplayControllerError("queued task requires exact reviewed inputs")
    task = queue["tasks"][0]
    assert authority_raw is not None and profile_raw is not None
    if (
        sha256_bytes(authority_raw) != task["authority_sha256"]
        or sha256_bytes(profile_raw) != task["qualification_sha256"]
        or canonical_bytes(authority_value) != authority_raw
        or canonical_bytes(profile_value) != profile_raw
    ):
        raise HistoricalPrivateReplayControllerError("reviewed private inputs differ from queue")
    profile = _validate_profile(profile_value, task)
    _validate_authority(authority_value, task, profile)
    state["task_sha256"] = sha256_bytes(state_canonical_bytes(task))
    return {
        "schema_version": 1,
        "kind": "historical_private_execution",
        "state": state,
        "task": task,
        "archive_binding": _validate_archive_binding(archive_binding_value, task),
        "execution_plan": _execution_plan(task, profile),
    }


def plan_from_checkouts(
    state_root: pathlib.Path,
    repository_root: pathlib.Path,
    audit_root: pathlib.Path,
) -> dict[str, Any]:
    """Plan the first exact protected queue task from canonical checkouts."""

    queue, queue_raw, state_head = load_state_queue(state_root)
    if not queue["tasks"]:
        return _plan_next(queue, queue_raw, state_head)
    task = queue["tasks"][0]
    authority, authority_raw, profile, profile_raw = load_reviewed_inputs(
        repository_root, task
    )
    _, _, _, archive_binding = load_archive_inputs(audit_root, task)
    return _plan_next(
        queue,
        queue_raw,
        state_head,
        authority,
        authority_raw,
        profile,
        profile_raw,
        archive_binding,
    )


def _validate_archive_binding(value: Any, task: dict[str, Any]) -> dict[str, Any]:
    binding = _object(value, "historical private archive binding")
    _fields(
        binding,
        {
            "repository", "commit", "archive_path", "sidecar_path",
            "ciphertext_sha256", "sidecar_sha256", "key_envelope_sha256",
            "plaintext_tar_sha256", "plaintext_tar_size",
        },
        "historical private archive binding",
    )
    expected = {
        "repository": task["archive_repository"],
        "commit": task["archive_commit"],
        "archive_path": task["archive_path"],
        "sidecar_path": task["archive_sidecar_path"],
        "ciphertext_sha256": task["archive_ciphertext_sha256"],
        "sidecar_sha256": task["archive_sidecar_sha256"],
        "key_envelope_sha256": task["archive_key_envelope_sha256"],
        "plaintext_tar_sha256": task["archive_plaintext_tar_sha256"],
        "plaintext_tar_size": task["archive_plaintext_tar_size"],
    }
    if binding != expected:
        raise HistoricalPrivateReplayControllerError(
            "historical private archive binding differs from queue"
        )
    return binding


def validate_execution_plan(value: Any) -> dict[str, Any]:
    plan = _object(value, "historical private execution plan")
    _fields(
        plan,
        {
            "schema_version", "kind", "state", "task", "archive_binding",
            "execution_plan",
        },
        "historical private execution plan",
    )
    if plan["schema_version"] != 1 or plan["kind"] != "historical_private_execution":
        raise HistoricalPrivateReplayControllerError("private execution plan identity is invalid")
    task = _validate_task(plan["task"], 0)
    _validate_archive_binding(plan["archive_binding"], task)
    state = _object(plan["state"], "private plan State binding")
    _fields(
        state,
        {"repository", "expected_head", "queue_environment", "queue_source_event_count",
         "queue_source_digest", "task_sha256"},
        "private plan State binding",
    )
    if (
        state["queue_environment"] not in {"staging", "production"}
        or state["repository"] != _state_repository(state["queue_environment"])
        or state["task_sha256"] != sha256_bytes(state_canonical_bytes(task))
    ):
        raise HistoricalPrivateReplayControllerError("private plan State binding is invalid")
    _match(COMMIT, state["expected_head"], "expected State head")
    _integer(state["queue_source_event_count"], "queue event count", 1)
    _match(DIGEST, state["queue_source_digest"], "queue source digest")
    try:
        execution = validate_private_execution_plan(plan["execution_plan"])
    except ReplayError as error:
        raise HistoricalPrivateReplayControllerError("private executor plan is invalid") from error
    expected = _execution_plan(
        task,
        {
            "execution_profile": execution["request"]["execution_profile"],
            "measurement_config": execution["request"]["measurement_config"],
        },
    )
    if execution != expected:
        raise HistoricalPrivateReplayControllerError("private executor plan differs from queue")
    return plan


def _validate_plan_against_queue(
    plan_value: Any,
    queue_value: Any,
    queue_raw: bytes,
    state_head: str,
) -> dict[str, Any]:
    plan = validate_execution_plan(plan_value)
    queue = validate_queue(queue_value)
    if state_canonical_bytes(queue) != queue_raw:
        raise HistoricalPrivateReplayControllerError(
            "live historical private queue raw bytes differ from its value"
        )
    task = queue["tasks"][0] if queue["tasks"] else None
    expected_state = {
        "repository": _state_repository(queue["environment"]),
        "expected_head": state_head,
        "queue_environment": queue["environment"],
        "queue_source_event_count": queue["source_event_count"],
        "queue_source_digest": queue["source_digest"],
        **({} if task is None else {"task_sha256": sha256_bytes(state_canonical_bytes(task))}),
    }
    if task is None or task != plan["task"] or plan["state"] != expected_state:
        raise HistoricalPrivateReplayControllerError("private plan is not the next exact live queue task")
    return plan


def validate_plan_against_state(
    plan_value: Any, state_root: pathlib.Path
) -> dict[str, Any]:
    queue, queue_raw, state_head = load_state_queue(state_root)
    return _validate_plan_against_queue(plan_value, queue, queue_raw, state_head)


def rebind_plan_to_current_state(
    plan_value: Any, state_root: pathlib.Path
) -> dict[str, Any]:
    """Rebind only State CAS metadata after proving the exact task unchanged."""

    plan = validate_execution_plan(plan_value)
    historical_queue, historical_raw, _ = load_state_queue_at_commit(
        state_root,
        plan["state"]["expected_head"],
        plan["state"]["repository"],
    )
    _validate_plan_against_queue(
        plan,
        historical_queue,
        historical_raw,
        plan["state"]["expected_head"],
    )
    queue, queue_raw, state_head = load_state_queue(state_root)
    task = queue["tasks"][0] if queue["tasks"] else None
    if task != plan["task"]:
        raise HistoricalPrivateReplayControllerError(
            "exact queued private replay changed while State advanced"
        )
    rebound = copy.deepcopy(plan)
    rebound["state"] = {
        "repository": _state_repository(queue["environment"]),
        "expected_head": state_head,
        "queue_environment": queue["environment"],
        "queue_source_event_count": queue["source_event_count"],
        "queue_source_digest": queue["source_digest"],
        "task_sha256": sha256_bytes(state_canonical_bytes(task)),
    }
    return _validate_plan_against_queue(rebound, queue, queue_raw, state_head)


def _state_append_candidate(event_value: Any, environment: str, expected_head: str) -> dict[str, Any]:
    event = _object(event_value, "State event candidate")
    if environment not in {"staging", "production"}:
        raise HistoricalPrivateReplayControllerError("State candidate environment is invalid")
    return {
        "schema_version": 1,
        "kind": "state_append_candidate",
        "state_repository": _state_repository(environment),
        "expected_head": _match(COMMIT, expected_head, "State append expected head"),
        "event": event,
    }


def started_candidate(
    plan_value: Any,
    state_root: pathlib.Path,
    trusted_now: str,
    *,
    random_bytes: bytes | None = None,
) -> dict[str, Any]:
    plan = validate_plan_against_state(plan_value, state_root)
    transition = plan["execution_plan"]["started_transition"]
    occurred = _event_time(trusted_now, plan["task"]["occurred_at"])
    event = {
        "schema_version": 1,
        "event_id": _causal_uuid7(occurred, transition["causation_event_id"], random_bytes),
        "event_type": "replay.started",
        "occurred_at": occurred.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "subject_id": transition["subject_id"],
        "causation_event_id": transition["causation_event_id"],
        "actor": {"kind": "system"},
        "payload": transition["payload"],
    }
    return _state_append_candidate(
        event, plan["state"]["queue_environment"], plan["state"]["expected_head"]
    )


def validate_started_history(
    plan_value: Any,
    started_candidate_value: Any,
    state_root: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Prove a post-start action belongs to the exact pre-start queue history."""

    plan = validate_execution_plan(plan_value)
    queue, queue_raw, current_head = load_state_queue_at_commit(
        state_root,
        plan["state"]["expected_head"],
        plan["state"]["repository"],
    )
    _validate_plan_against_queue(
        plan, queue, queue_raw, plan["state"]["expected_head"]
    )
    started = _object(started_candidate_value, "started State append candidate")
    _fields(
        started,
        {"schema_version", "kind", "state_repository", "expected_head", "event"},
        "started State append candidate",
    )
    transition = plan["execution_plan"]["started_transition"]
    event = _object(started["event"], "started State event")
    if (
        started["schema_version"] != 1
        or started["kind"] != "state_append_candidate"
        or started["state_repository"] != plan["state"]["repository"]
        or started["expected_head"] != plan["state"]["expected_head"]
        or event.get("event_type") != "replay.started"
        or event.get("subject_id") != transition["subject_id"]
        or event.get("causation_event_id") != transition["causation_event_id"]
        or event.get("actor") != {"kind": "system"}
        or event.get("payload") != transition["payload"]
    ):
        raise HistoricalPrivateReplayControllerError(
            "started candidate differs from exact historical plan"
        )
    verified_head = _verify_state_event(state_root, event)
    if verified_head != current_head:
        raise HistoricalPrivateReplayControllerError(
            "protected State HEAD changed during history validation"
        )
    running = current_historical_running(
        state_root / "events",
        state_validated=True,
        authority_event_type=AUTHORITY_EVENT_TYPE,
    )
    expected_attempt = plan["task"]["attempt"] + 1
    if (
        len(running) != 1
        or running[0].get("replay_task_id") != plan["task"]["replay_task_id"]
        or running[0].get("status") != "running"
        or running[0].get("attempt") != expected_attempt
        or running[0].get("event") != event
        or event.get("event_id") != started["event"].get("event_id")
        or event.get("payload") != {
            "attempt": expected_attempt,
            "runner_profile": plan["execution_plan"]["request"][
                "execution_profile"
            ]["runner_profile"],
        }
    ):
        raise HistoricalPrivateReplayControllerError(
            "supplied start is not the unique current running private replay"
        )
    final_head = _verify_checkout(
        state_root,
        plan["state"]["repository"],
        minimum_commit=STATE_MINIMUM_COMMITS[plan["state"]["queue_environment"]],
    )
    if final_head != current_head:
        raise HistoricalPrivateReplayControllerError(
            "protected State HEAD changed during running-task validation"
        )
    return plan, started, current_head


def terminal_candidate(
    plan_value: Any,
    started_candidate_value: Any,
    verdict_value: Any,
    state_root: pathlib.Path,
    trusted_now: str,
    *,
    random_bytes: bytes | None = None,
) -> dict[str, Any]:
    plan, started, state_head = validate_started_history(
        plan_value, started_candidate_value, state_root
    )
    event = build_private_terminal_event(
        plan["execution_plan"],
        verdict_value,
        started["event"],
        trusted_now,
        random_bytes=random_bytes,
    )
    return _state_append_candidate(
        event, plan["state"]["queue_environment"], state_head
    )


def current_running_proof(
    plan_value: Any,
    started_candidate_value: Any,
    state_root: pathlib.Path,
) -> dict[str, Any]:
    """Return a source-free proof of the one currently running exact task."""

    plan, started, state_head = validate_started_history(
        plan_value, started_candidate_value, state_root
    )
    return {
        "schema_version": 1,
        "kind": "historical_private_current_running_proof",
        "state_repository": plan["state"]["repository"],
        "state_head": state_head,
        "replay_task_id": plan["task"]["replay_task_id"],
        "attempt": plan["task"]["attempt"] + 1,
        "started_event_id": started["event"]["event_id"],
    }


def terminal_committed_proof(
    plan_value: Any,
    started_candidate_value: Any,
    terminal_candidate_value: Any,
    state_root: pathlib.Path,
) -> dict[str, Any]:
    """Prove one exact terminal candidate is in fresh protected State."""

    plan = validate_execution_plan(plan_value)
    started = _object(started_candidate_value, "started State append candidate")
    terminal = _object(terminal_candidate_value, "terminal State append candidate")
    _fields(
        terminal,
        {"schema_version", "kind", "state_repository", "expected_head", "event"},
        "terminal State append candidate",
    )
    event = _object(terminal["event"], "terminal State event")
    started_event = _object(started.get("event"), "started State event")
    expected_attempt = plan["task"]["attempt"] + 1
    if (
        terminal["schema_version"] != 1
        or terminal["kind"] != "state_append_candidate"
        or terminal["state_repository"] != plan["state"]["repository"]
        or not isinstance(terminal.get("expected_head"), str)
        or COMMIT.fullmatch(terminal["expected_head"]) is None
        or event.get("event_type") not in {"replay.accepted", "replay.failed"}
        or event.get("subject_id") != plan["task"]["replay_task_id"]
        or event.get("causation_event_id") != started_event.get("event_id")
        or event.get("actor") != {"kind": "system"}
        or not isinstance(event.get("payload"), dict)
        or event["payload"].get("attempt") != expected_attempt
    ):
        raise HistoricalPrivateReplayControllerError(
            "terminal candidate differs from the exact started private replay"
        )
    head = _verify_state_event(state_root, event)
    try:
        subprocess.run(
            [
                "git", "-C", str(state_root), "merge-base", "--is-ancestor",
                terminal["expected_head"], head,
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise HistoricalPrivateReplayControllerError(
            "terminal CAS parent is not protected State ancestry"
        ) from error
    running = current_historical_running(
        state_root / "events",
        state_validated=True,
        authority_event_type=AUTHORITY_EVENT_TYPE,
    )
    if any(
        item.get("replay_task_id") == plan["task"]["replay_task_id"]
        and item.get("attempt") == expected_attempt
        for item in running
    ):
        raise HistoricalPrivateReplayControllerError(
            "terminal private replay remains current-running"
        )
    return {
        "schema_version": 1,
        "kind": "historical_private_terminal_committed_proof",
        "state_repository": plan["state"]["repository"],
        "state_head": head,
        "terminal_event_id": event["event_id"],
        "replay_task_id": plan["task"]["replay_task_id"],
        "attempt": expected_attempt,
    }


def _verify_state_event(state_root: pathlib.Path, event_value: Any) -> str:
    event = _object(event_value, "committed State event")
    event_id = _match(UUID7, event.get("event_id"), "committed State event_id")
    _, _, head = load_state_queue(state_root)
    path = f"events/{event_id[:2]}/{event_id}.json"
    raw = _git_blob(state_root, head, path)
    if raw != state_canonical_bytes(event):
        raise HistoricalPrivateReplayControllerError(
            "started event differs from exact protected State HEAD"
        )
    return head


def prepare_unwrap(
    plan_value: Any,
    state_root: pathlib.Path,
    started_candidate_value: Any,
    audit_root: pathlib.Path,
    trusted_now: str,
    *,
    request_random: bytes | None = None,
    runner_nonce: str | None = None,
) -> dict[str, Any]:
    plan, _, _ = validate_started_history(
        plan_value, started_candidate_value, state_root
    )
    sidecar, _, ciphertext, binding = load_archive_inputs(audit_root, plan["task"])
    if binding != plan["archive_binding"]:
        raise HistoricalPrivateReplayControllerError(
            "live protected audit archive differs from execution plan"
        )
    with tempfile.TemporaryDirectory() as directory:
        ciphertext_path = pathlib.Path(directory) / "archive.tar.age"
        _write_bytes(ciphertext_path, ciphertext)
        return prepare_private_unwrap(
            plan["execution_plan"], sidecar, ciphertext_path, trusted_now,
            request_random=request_random, runner_nonce=runner_nonce,
        )


def build_executor_request(
    plan_value: Any,
    state_root: pathlib.Path,
    started_candidate_value: Any,
    audit_root: pathlib.Path,
    unwrap_value: Any,
    identity_path: pathlib.Path,
) -> dict[str, Any]:
    plan, _, _ = validate_started_history(
        plan_value, started_candidate_value, state_root
    )
    sidecar, _, ciphertext, binding = load_archive_inputs(audit_root, plan["task"])
    if binding != plan["archive_binding"]:
        raise HistoricalPrivateReplayControllerError(
            "live protected audit archive differs from execution plan"
        )
    with tempfile.TemporaryDirectory() as directory:
        ciphertext_path = pathlib.Path(directory) / "archive.tar.age"
        _write_bytes(ciphertext_path, ciphertext)
        return build_private_executor_request(
            plan["execution_plan"], sidecar, ciphertext_path, unwrap_value, identity_path
        )


def validate_executor_response(response_value: Any, plan_value: Any) -> dict[str, Any]:
    plan = validate_execution_plan(plan_value)
    return validate_private_executor_response(response_value, plan["execution_plan"])


def render_executor_config(
    plan_value: Any,
    repository_root: pathlib.Path,
    account_id: str,
    source_commit: str,
) -> dict[str, Any]:
    """Render one task-scoped, digest-pinned private replay Worker."""

    plan = validate_execution_plan(plan_value)
    _, _, profile, _ = load_reviewed_inputs(repository_root, plan["task"])
    _match(ACCOUNT_ID, account_id, "Cloudflare account id")
    _match(COMMIT, source_commit, "historical private executor source commit")
    _verify_ancestor_of_upstream(repository_root, source_commit, "executor source commit")
    task = plan["task"]
    manifest = profile["registry_manifest_digest"]
    if (
        profile["registry_repository"] != "lean-eval-authoritative"
        or manifest != plan["execution_plan"]["request"]["execution_profile"][
            "vm_image_digest"
        ]
    ):
        raise HistoricalPrivateReplayControllerError(
            "private executor image differs from the qualified plan"
        )
    attempt = task["attempt"] + 1
    if attempt > MAX_REPLAY_ATTEMPTS:
        raise HistoricalPrivateReplayControllerError(
            "historical private replay exceeds the attempt limit"
        )
    worker_name = f"hpr-{task['replay_task_id'][4:60]}-{attempt}"
    container_application_name = (
        f"le-hpr-{task['replay_task_id'][4:26]}-{attempt}"
    )
    image = (
        f"registry.cloudflare.com/{account_id}/"
        f"{profile['registry_repository']}@{manifest}"
    )
    ownership_tag = sha256_bytes(
        canonical_bytes(
            {
                "schema_version": 1,
                "kind": "historical_private_executor_ownership",
                "source_commit": source_commit,
                "replay_task_id": task["replay_task_id"],
                "attempt": attempt,
                "execution_profile_digest": task["execution_profile_digest"],
                "measurement_config_digest": task["measurement_config_digest"],
                "registry_manifest_digest": manifest,
            }
        )
    )
    return {
        "$schema": "node_modules/wrangler/config-schema.json",
        "name": worker_name,
        "main": str(
            (repository_root / "server/src/historical-private-replay-entry.ts").resolve()
        ),
        "account_id": account_id,
        "compatibility_date": "2026-08-22",
        "compatibility_flags": ["nodejs_compat"],
        "workers_dev": True,
        "preview_urls": False,
        "observability": {"enabled": False},
        "containers": [
            {
                "name": container_application_name,
                "class_name": "ReplaySandbox",
                "image": image,
                "instance_type": "standard-4",
                "max_instances": 1,
                "ssh": {"enabled": False},
            }
        ],
        "durable_objects": {
            "bindings": [
                {"name": "REPLAY_SANDBOX", "class_name": "ReplaySandbox"},
                {
                    "name": "REPLAY_TERMINAL_RECEIPT",
                    "class_name": "ReplayTerminalReceipt",
                },
            ]
        },
        "migrations": [
            {
                "tag": "v1",
                "new_sqlite_classes": ["ReplaySandbox", "ReplayTerminalReceipt"],
            }
        ],
        "vars": {
            "DEPLOYED_COMMIT": source_commit,
            "DEPLOYMENT_ENVIRONMENT": "historical-private-replay",
            "REPLAY_ENABLED": "true",
            "HISTORICAL_PUBLIC_REPLAY_ENABLED": "false",
            "STAGING_ACCEPTANCE_ENABLED": "false",
            "GITHUB_OIDC_AUDIENCE": "lean-eval-historical-private-replay",
            "GITHUB_OIDC_ENVIRONMENT": "replay-production",
            "STAGING_MEMORY_LIMIT_BYTES": str(12 * 1024**3),
            "PRODUCTION_MEMORY_GATE_BYTES": str(12 * 1024**3),
            "REVIEWED_EXECUTION_PROFILE_DIGEST": task[
                "execution_profile_digest"
            ],
            "REVIEWED_MEASUREMENT_CONFIG_DIGEST": task[
                "measurement_config_digest"
            ],
            "REVIEWED_VM_IMAGE_DIGEST": manifest,
            "EXPECTED_REPLAY_TASK_ID": task["replay_task_id"],
            "EXPECTED_REPLAY_ATTEMPT": str(attempt),
            "EXECUTOR_OWNERSHIP_TAG": ownership_tag,
            "SANDBOX_TRANSPORT": "rpc",
        },
    }


def recover_running(
    state_root: pathlib.Path,
    trusted_now: str,
    *,
    cleanup_confirmation_value: Any | None = None,
    random_bytes: bytes | None = None,
) -> dict[str, Any]:
    queue, _, state_head = load_state_queue(state_root)
    recovered = recover_historical_running(
        state_root / "events",
        trusted_now,
        state_validated=True,
        cleanup_confirmation_value=cleanup_confirmation_value,
        random_bytes=random_bytes,
        authority_event_type=AUTHORITY_EVENT_TYPE,
    )
    if recovered["kind"] != "failed":
        return recovered
    return {
        "schema_version": 1,
        "kind": "failed",
        "append": _state_append_candidate(
            recovered["event"], queue["environment"], state_head
        ),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--state-root", required=True, type=pathlib.Path)
    plan.add_argument("--repository-root", required=True, type=pathlib.Path)
    plan.add_argument("--audit-root", required=True, type=pathlib.Path)
    plan.add_argument("--output", required=True, type=pathlib.Path)
    started = commands.add_parser("started-candidate")
    started.add_argument("--plan", required=True, type=pathlib.Path)
    started.add_argument("--state-root", required=True, type=pathlib.Path)
    started.add_argument("--trusted-now", required=True)
    started.add_argument("--output", required=True, type=pathlib.Path)
    rebind = commands.add_parser("refresh-rebind-plan")
    rebind.add_argument("--plan", required=True, type=pathlib.Path)
    rebind.add_argument("--state-root", required=True, type=pathlib.Path)
    rebind.add_argument("--output", required=True, type=pathlib.Path)
    refresh = commands.add_parser("refresh-state")
    refresh.add_argument("--state-root", required=True, type=pathlib.Path)
    refresh.add_argument("--output", required=True, type=pathlib.Path)
    proof = commands.add_parser("prove-running")
    proof.add_argument("--plan", required=True, type=pathlib.Path)
    proof.add_argument("--started-candidate", required=True, type=pathlib.Path)
    proof.add_argument("--state-root", required=True, type=pathlib.Path)
    proof.add_argument("--output", required=True, type=pathlib.Path)
    refresh_proof = commands.add_parser("refresh-prove-running")
    refresh_proof.add_argument("--plan", required=True, type=pathlib.Path)
    refresh_proof.add_argument(
        "--started-candidate", required=True, type=pathlib.Path
    )
    refresh_proof.add_argument("--state-root", required=True, type=pathlib.Path)
    refresh_proof.add_argument("--output", required=True, type=pathlib.Path)
    terminal = commands.add_parser("terminal-candidate")
    terminal.add_argument("--plan", required=True, type=pathlib.Path)
    terminal.add_argument("--started-candidate", required=True, type=pathlib.Path)
    terminal.add_argument("--verdict", type=pathlib.Path)
    terminal.add_argument("--failure-reason", choices=sorted(FAILURE_REASONS))
    terminal.add_argument("--state-root", required=True, type=pathlib.Path)
    terminal.add_argument("--trusted-now", required=True)
    terminal.add_argument("--output", required=True, type=pathlib.Path)
    verify_terminal = commands.add_parser("refresh-verify-terminal")
    verify_terminal.add_argument("--plan", required=True, type=pathlib.Path)
    verify_terminal.add_argument(
        "--started-candidate", required=True, type=pathlib.Path
    )
    verify_terminal.add_argument(
        "--terminal-candidate", required=True, type=pathlib.Path
    )
    verify_terminal.add_argument("--state-root", required=True, type=pathlib.Path)
    verify_terminal.add_argument("--output", required=True, type=pathlib.Path)
    unwrap = commands.add_parser("prepare-unwrap")
    for name in ("plan", "state-root", "started-candidate", "audit-root", "output"):
        unwrap.add_argument(f"--{name}", required=True, type=pathlib.Path)
    unwrap.add_argument("--trusted-now", required=True)
    executor = commands.add_parser("build-executor-request")
    for name in (
        "plan", "state-root", "started-candidate", "audit-root", "unwrap",
        "identity", "output",
    ):
        executor.add_argument(f"--{name}", required=True, type=pathlib.Path)
    identity = commands.add_parser("unwrap-identity")
    for name in ("request", "response", "metadata", "output"):
        identity.add_argument(f"--{name}", required=True, type=pathlib.Path)
    response = commands.add_parser("validate-response")
    response.add_argument("--plan", required=True, type=pathlib.Path)
    response.add_argument("--response", required=True, type=pathlib.Path)
    response.add_argument("--verdict-output", required=True, type=pathlib.Path)
    render = commands.add_parser("render-executor-config")
    render.add_argument("--plan", required=True, type=pathlib.Path)
    render.add_argument("--repository-root", required=True, type=pathlib.Path)
    render.add_argument("--account-id", required=True)
    render.add_argument("--source-commit", required=True)
    render.add_argument("--output", required=True, type=pathlib.Path)
    recovery = commands.add_parser("recover")
    recovery.add_argument("--state-root", required=True, type=pathlib.Path)
    recovery.add_argument("--trusted-now", required=True)
    recovery.add_argument("--cleanup-confirmation", type=pathlib.Path)
    recovery.add_argument("--output", required=True, type=pathlib.Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "plan":
            _write(
                args.output,
                plan_from_checkouts(
                    args.state_root, args.repository_root, args.audit_root
                ),
            )
        elif args.command == "started-candidate":
            plan, _ = _load_canonical(args.plan, "historical private plan")
            _write(
                args.output,
                started_candidate(plan, args.state_root, args.trusted_now),
                state_canonical_bytes,
            )
        elif args.command == "refresh-rebind-plan":
            plan, _ = _load_canonical(args.plan, "historical private plan")
            refresh_protected_state(args.state_root)
            _write(
                args.output,
                rebind_plan_to_current_state(plan, args.state_root),
            )
        elif args.command == "refresh-state":
            head = refresh_protected_state(args.state_root)
            queue, _, verified_head = load_state_queue(args.state_root)
            if verified_head != head:
                raise HistoricalPrivateReplayControllerError(
                    "protected State changed during refreshed proof"
                )
            _write(
                args.output,
                {
                    "schema_version": 1,
                    "kind": "historical_private_refreshed_state_proof",
                    "state_repository": _state_repository(queue["environment"]),
                    "state_head": head,
                },
            )
        elif args.command == "prove-running":
            plan, _ = _load_canonical(args.plan, "historical private plan")
            started, _ = _load_state_canonical(
                args.started_candidate, "started append candidate"
            )
            _write(
                args.output,
                current_running_proof(plan, started, args.state_root),
            )
        elif args.command == "refresh-prove-running":
            plan, _ = _load_canonical(args.plan, "historical private plan")
            started, _ = _load_state_canonical(
                args.started_candidate, "started append candidate"
            )
            refresh_protected_state(args.state_root)
            _write(
                args.output,
                current_running_proof(plan, started, args.state_root),
            )
        elif args.command == "terminal-candidate":
            plan, _ = _load_canonical(args.plan, "historical private plan")
            started, _ = _load_state_canonical(args.started_candidate, "started append candidate")
            if (args.verdict is None) == (args.failure_reason is None):
                raise HistoricalPrivateReplayControllerError(
                    "terminal requires exactly one verdict or failure reason"
                )
            verdict = (
                _load_canonical(args.verdict, "private replay verdict")[0]
                if args.verdict is not None
                else build_private_failure_verdict(
                    validate_execution_plan(plan)["execution_plan"], args.failure_reason
                )
            )
            _write(
                args.output,
                terminal_candidate(
                    plan, started, verdict, args.state_root, args.trusted_now
                ),
                state_canonical_bytes,
            )
        elif args.command == "refresh-verify-terminal":
            plan, _ = _load_canonical(args.plan, "historical private plan")
            started, _ = _load_state_canonical(
                args.started_candidate, "started append candidate"
            )
            terminal_value, _ = _load_state_canonical(
                args.terminal_candidate, "terminal append candidate"
            )
            refresh_protected_state(args.state_root)
            _write(
                args.output,
                terminal_committed_proof(
                    plan, started, terminal_value, args.state_root
                ),
            )
        elif args.command == "prepare-unwrap":
            plan, _ = _load_canonical(args.plan, "historical private plan")
            started, _ = _load_state_canonical(
                args.started_candidate, "started append candidate"
            )
            _write(
                args.output,
                prepare_unwrap(
                    plan, args.state_root, started, args.audit_root, args.trusted_now
                ),
            )
        elif args.command == "build-executor-request":
            plan, _ = _load_canonical(args.plan, "historical private plan")
            started, _ = _load_state_canonical(
                args.started_candidate, "started append candidate"
            )
            unwrap, _ = _load_canonical(args.unwrap, "private unwrap request")
            _write(
                args.output,
                build_executor_request(
                    plan, args.state_root, started, args.audit_root, unwrap, args.identity
                ),
            )
        elif args.command == "unwrap-identity":
            request, _ = _load_canonical(args.request, "private unwrap request")
            response, _ = _load_canonical(args.response, "private unwrap response")
            metadata, _ = _load_canonical(args.metadata, "Lambda invocation metadata")
            _write_bytes(args.output, unwrap_identity(request, response, metadata))
        elif args.command == "validate-response":
            plan, _ = _load_canonical(args.plan, "historical private plan")
            response, _ = _load_canonical(args.response, "private executor response")
            _write(args.verdict_output, validate_executor_response(response, plan))
        elif args.command == "render-executor-config":
            plan, _ = _load_canonical(args.plan, "historical private plan")
            _write(
                args.output,
                render_executor_config(
                    plan,
                    args.repository_root,
                    args.account_id,
                    args.source_commit,
                ),
            )
        else:
            confirmation = (
                None
                if args.cleanup_confirmation is None
                else _load_canonical(args.cleanup_confirmation, "cleanup confirmation")[0]
            )
            _write(
                args.output,
                recover_running(
                    args.state_root, args.trusted_now,
                    cleanup_confirmation_value=confirmation,
                ),
            )
    except (HistoricalPrivateReplayControllerError, OSError) as error:
        print(f"historical-private-replay-controller: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
