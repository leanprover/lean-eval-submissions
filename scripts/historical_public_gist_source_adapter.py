#!/usr/bin/env python3
"""Prepare a runner handoff from one exact reviewed public Gist checkout.

The adapter never clones or fetches.  Its caller must provide a detached local
checkout of the reviewed Gist commit and a local benchmark checkout.  The
adapter verifies both identities before producing the existing, bounded
historical-public runner archive and handoff.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

SCRIPT_DIRECTORY = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from historical_public_runner import (
    COMMIT,
    DIGEST,
    HistoricalPublicRunnerError,
    _build_handoff_with_source_identity,
    _git_environment,
    _validate_handoff_json_schema,
    _validate_json_schema,
    _write_exclusive_json,
    canonical_document_bytes,
    load_canonical_json,
    sha256_bytes,
    validate_contract,
    validate_handoff,
)

ADAPTER_CONTRACT = "historical_public_gist_source_adapter_v1"
GIST_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
GIST_ID = re.compile(r"[0-9a-f]+\Z")


class HistoricalPublicGistSourceError(HistoricalPublicRunnerError):
    """The local checkout does not match the exact reviewed public Gist."""


def _run_git(
    repository: pathlib.Path,
    *arguments: str,
    accepted_returncodes: tuple[int, ...] = (0,),
    maximum: int = 4096,
) -> tuple[int, bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            env=_git_environment(),
        )
    except OSError as error:
        raise HistoricalPublicGistSourceError(
            "reviewed Gist checkout is unavailable"
        ) from error
    if result.returncode not in accepted_returncodes:
        raise HistoricalPublicGistSourceError(
            f"reviewed Gist Git identity is unavailable: {' '.join(arguments)}"
        )
    if len(result.stdout) > maximum:
        raise HistoricalPublicGistSourceError(
            "reviewed Gist Git identity exceeds its size limit"
        )
    return result.returncode, result.stdout


def _gist_identity(source: dict[str, Any]) -> tuple[str, str, str]:
    if set(source) != {"kind", "repository", "commit", "visibility"}:
        raise HistoricalPublicGistSourceError(
            "reviewed Gist source fields are not closed"
        )
    if source["kind"] != "gist" or source["visibility"] != "public":
        raise HistoricalPublicGistSourceError("reviewed source is not a public Gist")
    repository = source["repository"]
    if not isinstance(repository, str) or repository.count("/") != 1:
        raise HistoricalPublicGistSourceError("reviewed Gist owner and id are invalid")
    owner, gist_id = repository.split("/", 1)
    if GIST_OWNER.fullmatch(owner) is None or GIST_ID.fullmatch(gist_id) is None:
        raise HistoricalPublicGistSourceError("reviewed Gist owner and id are invalid")
    commit = source["commit"]
    if not isinstance(commit, str) or COMMIT.fullmatch(commit) is None:
        raise HistoricalPublicGistSourceError("reviewed Gist commit is invalid")
    return owner, gist_id, commit


def verify_gist_checkout(repository: pathlib.Path, source: dict[str, Any]) -> str:
    """Return the exact commit tree after fail-closed local identity checks."""
    owner, gist_id, commit = _gist_identity(source)
    if repository.is_symlink():
        raise HistoricalPublicGistSourceError(
            "reviewed Gist checkout is not a real directory"
        )
    try:
        checkout = repository.resolve(strict=True)
    except OSError as error:
        raise HistoricalPublicGistSourceError(
            "reviewed Gist checkout is unavailable"
        ) from error
    if not checkout.is_dir() or checkout.is_symlink():
        raise HistoricalPublicGistSourceError(
            "reviewed Gist checkout is not a real directory"
        )
    _, top_level_raw = _run_git(checkout, "rev-parse", "--show-toplevel")
    try:
        top_level = pathlib.Path(top_level_raw.decode("utf-8").strip()).resolve(
            strict=True
        )
    except (OSError, UnicodeError) as error:
        raise HistoricalPublicGistSourceError(
            "reviewed Gist checkout root is invalid"
        ) from error
    if top_level != checkout:
        raise HistoricalPublicGistSourceError(
            "reviewed Gist checkout path is not its Git root"
        )

    _, remotes_raw = _run_git(checkout, "remote")
    _, origin_urls_raw = _run_git(checkout, "config", "--get-all", "remote.origin.url")
    expected_remote = f"https://gist.github.com/{owner}/{gist_id}.git"
    try:
        remotes = remotes_raw.decode("utf-8").splitlines()
        origin_urls = origin_urls_raw.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise HistoricalPublicGistSourceError(
            "reviewed Gist remote identity is invalid"
        ) from error
    if remotes != ["origin"] or origin_urls != [expected_remote]:
        raise HistoricalPublicGistSourceError(
            "reviewed Gist owner, id, or remote identity differs"
        )

    _, head_raw = _run_git(checkout, "rev-parse", "HEAD^{commit}")
    _, commit_raw = _run_git(checkout, "rev-parse", f"{commit}^{{commit}}")
    _, head_tree_raw = _run_git(checkout, "rev-parse", "HEAD^{tree}")
    _, commit_tree_raw = _run_git(checkout, "rev-parse", f"{commit}^{{tree}}")
    try:
        head = head_raw.decode("ascii").strip()
        resolved_commit = commit_raw.decode("ascii").strip()
        head_tree = head_tree_raw.decode("ascii").strip()
        commit_tree = commit_tree_raw.decode("ascii").strip()
    except UnicodeError as error:
        raise HistoricalPublicGistSourceError(
            "reviewed Gist commit or tree identity is invalid"
        ) from error
    if (
        head != commit
        or resolved_commit != commit
        or head_tree != commit_tree
        or COMMIT.fullmatch(commit_tree) is None
    ):
        raise HistoricalPublicGistSourceError(
            "reviewed Gist HEAD, commit, or tree identity differs"
        )

    symbolic_status, _ = _run_git(
        checkout,
        "symbolic-ref",
        "-q",
        "HEAD",
        accepted_returncodes=(0, 1),
    )
    if symbolic_status != 1:
        raise HistoricalPublicGistSourceError("reviewed Gist checkout is not detached")
    _, status = _run_git(
        checkout,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        maximum=1024 * 1024,
    )
    if status:
        raise HistoricalPublicGistSourceError("reviewed Gist checkout is not clean")
    return commit_tree


def validate_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "kind",
        "contract",
        "request_id",
        "result_id",
        "source",
        "archive",
        "handoff_sha256",
    }:
        raise HistoricalPublicGistSourceError(
            "Gist adapter receipt fields are not closed"
        )
    source = value.get("source")
    archive = value.get("archive")
    if not isinstance(source, dict) or set(source) != {
        "kind",
        "repository",
        "owner",
        "gist_id",
        "remote_url",
        "commit",
        "tree",
        "checkout_clean",
        "checkout_detached",
    }:
        raise HistoricalPublicGistSourceError(
            "Gist adapter receipt source fields are not closed"
        )
    if not isinstance(archive, dict) or set(archive) != {
        "format",
        "member_prefix",
        "sha256",
        "size_bytes",
    }:
        raise HistoricalPublicGistSourceError(
            "Gist adapter receipt archive fields are not closed"
        )
    owner, gist_id, commit = _gist_identity(
        {
            "kind": source.get("kind"),
            "repository": source.get("repository"),
            "commit": source.get("commit"),
            "visibility": "public",
        }
    )
    if (
        value["schema_version"] != 1
        or value["kind"] != "historical_public_gist_source_adapter_receipt"
        or value["contract"] != ADAPTER_CONTRACT
        or source["owner"] != owner
        or source["gist_id"] != gist_id
        or source["remote_url"] != f"https://gist.github.com/{owner}/{gist_id}.git"
        or source["commit"] != commit
        or source["checkout_clean"] is not True
        or source["checkout_detached"] is not True
        or not isinstance(source["tree"], str)
        or COMMIT.fullmatch(source["tree"]) is None
        or archive["format"] != "git_archive_tar_gzip_v1"
        or archive["member_prefix"] != "source"
        or not isinstance(archive["sha256"], str)
        or DIGEST.fullmatch(archive["sha256"]) is None
        or type(archive["size_bytes"]) is not int
        or not 1 <= archive["size_bytes"] <= 50 * 1024 * 1024
        or not isinstance(value["handoff_sha256"], str)
        or DIGEST.fullmatch(value["handoff_sha256"]) is None
    ):
        raise HistoricalPublicGistSourceError("Gist adapter receipt is invalid")
    for name in ("request_id", "result_id"):
        item = value[name]
        prefix = "prr_" if name == "request_id" else "r2_"
        if (
            not isinstance(item, str)
            or re.fullmatch(prefix + r"[0-9a-f]{64}", item) is None
        ):
            raise HistoricalPublicGistSourceError(f"Gist adapter {name} is invalid")
    return value


def build_gist_handoff(
    *,
    plan: dict[str, Any],
    plan_raw: bytes,
    matrix: dict[str, Any],
    matrix_raw: bytes,
    contract: dict[str, Any],
    contract_raw: bytes,
    request_id: str,
    result_id: str,
    gist_checkout: pathlib.Path,
    benchmark_repository: pathlib.Path,
    source_archive: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    handoff = _build_handoff_with_source_identity(
        plan=plan,
        plan_raw=plan_raw,
        matrix=matrix,
        matrix_raw=matrix_raw,
        contract=contract,
        contract_raw=contract_raw,
        request_id=request_id,
        result_id=result_id,
        source_repository=gist_checkout,
        benchmark_repository=benchmark_repository,
        source_archive=source_archive,
        source_kind="gist",
        source_identity_validator=verify_gist_checkout,
    )
    validate_handoff(handoff, contract)
    repository = handoff["source"]["repository"]
    owner, gist_id = repository.split("/", 1)
    receipt = {
        "schema_version": 1,
        "kind": "historical_public_gist_source_adapter_receipt",
        "contract": ADAPTER_CONTRACT,
        "request_id": handoff["request_id"],
        "result_id": handoff["result"]["result_id"],
        "source": {
            "kind": "gist",
            "repository": repository,
            "owner": owner,
            "gist_id": gist_id,
            "remote_url": f"https://gist.github.com/{owner}/{gist_id}.git",
            "commit": handoff["source"]["commit"],
            "tree": handoff["source"]["tree"],
            "checkout_clean": True,
            "checkout_detached": True,
        },
        "archive": {
            "format": handoff["source"]["archive_format"],
            "member_prefix": handoff["source"]["archive_member_prefix"],
            "sha256": handoff["source"]["archive_sha256"],
            "size_bytes": handoff["source"]["archive_size_bytes"],
        },
        "handoff_sha256": sha256_bytes(canonical_document_bytes(handoff)),
    }
    return handoff, validate_receipt(receipt)


def _prepare(args: argparse.Namespace, root: pathlib.Path) -> None:
    plan, plan_raw = load_canonical_json(args.plan, "historical public replay plan")
    matrix, matrix_raw = load_canonical_json(
        args.profile_matrix, "historical public profile matrix"
    )
    contract, contract_raw = load_canonical_json(
        args.contract, "historical public runner contract", 128 * 1024
    )
    for actual, expected, label in (
        (sha256_bytes(plan_raw), args.expected_plan_sha256, "plan"),
        (
            sha256_bytes(matrix_raw),
            args.expected_profile_matrix_sha256,
            "profile matrix",
        ),
        (sha256_bytes(contract_raw), args.expected_contract_sha256, "runner contract"),
    ):
        if DIGEST.fullmatch(expected) is None or actual != expected:
            raise HistoricalPublicGistSourceError(f"exact {label} digest differs")
    _validate_json_schema(
        plan, root / "schemas/historical-public-replay-plan-v1.schema.json", "plan"
    )
    _validate_json_schema(
        matrix,
        root / "schemas/historical-public-replay-profile-matrix-v1.schema.json",
        "profile matrix",
    )
    validate_contract(contract)
    for output, label in (
        (args.source_archive, "source archive"),
        (args.handoff, "handoff"),
        (args.output, "adapter receipt"),
    ):
        if output.exists() or output.is_symlink():
            raise HistoricalPublicGistSourceError(f"{label} output already exists")

    archive_created = False
    handoff_created = False
    receipt_created = False
    try:
        handoff, receipt = build_gist_handoff(
            plan=plan,
            plan_raw=plan_raw,
            matrix=matrix,
            matrix_raw=matrix_raw,
            contract=contract,
            contract_raw=contract_raw,
            request_id=args.request_id,
            result_id=args.result_id,
            gist_checkout=args.gist_checkout,
            benchmark_repository=args.benchmark_repository,
            source_archive=args.source_archive,
        )
        archive_created = args.source_archive.exists()
        _validate_handoff_json_schema(handoff, root)
        _validate_json_schema(
            receipt,
            root / "schemas/historical-public-gist-source-adapter-v1.schema.json",
            "Gist adapter receipt",
        )
        _write_exclusive_json(args.handoff, handoff)
        handoff_created = True
        _write_exclusive_json(args.output, receipt)
        receipt_created = True
    except Exception:
        archive_created = (
            args.source_archive.is_file() and not args.source_archive.is_symlink()
        )
        if receipt_created:
            args.output.unlink(missing_ok=True)
        if handoff_created:
            args.handoff.unlink(missing_ok=True)
        if archive_created:
            args.source_archive.unlink(missing_ok=True)
        raise


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    subparsers = command.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--plan", required=True, type=pathlib.Path)
    prepare.add_argument("--profile-matrix", required=True, type=pathlib.Path)
    prepare.add_argument("--contract", required=True, type=pathlib.Path)
    prepare.add_argument("--expected-plan-sha256", required=True)
    prepare.add_argument("--expected-profile-matrix-sha256", required=True)
    prepare.add_argument("--expected-contract-sha256", required=True)
    prepare.add_argument("--request-id", required=True)
    prepare.add_argument("--result-id", required=True)
    prepare.add_argument("--gist-checkout", required=True, type=pathlib.Path)
    prepare.add_argument("--benchmark-repository", required=True, type=pathlib.Path)
    prepare.add_argument("--source-archive", required=True, type=pathlib.Path)
    prepare.add_argument("--handoff", required=True, type=pathlib.Path)
    prepare.add_argument("--output", required=True, type=pathlib.Path)
    return command


def main() -> int:
    args = parser().parse_args()
    root = pathlib.Path(__file__).parents[1]
    try:
        _prepare(args, root)
    except (
        HistoricalPublicRunnerError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        print(f"historical-public-gist-source-adapter: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
