"""Inactive stock replay contracts for preparation/tests, never runtime admission.

No active controller imports this module. Validating a proposed contract proves
bindings against caller-supplied trusted Git bytes, not image qualification,
derived-workspace correctness, source classification, or authorization to replay.
This module performs no network, Git, subprocess, State or filesystem writes.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from functools import lru_cache
from typing import Any, Mapping

try:
    from .replay_orchestrator import canonical_archive_path, canonical_json_bytes, config_digest, replay_task_id
    from .results_schema import validate_v2, ResultsSchemaError
except ImportError:
    from replay_orchestrator import canonical_archive_path, canonical_json_bytes, config_digest, replay_task_id
    from results_schema import validate_v2, ResultsSchemaError

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIGURATION_DOMAIN = "lean-eval-benchmark-configuration-lock-v1"
PROFILE_DOMAIN = "lean-eval-replay-execution-profile-v1"
MEASUREMENT_DOMAIN = "lean-eval-replay-measurement-config-v1"
SCHEMAS = ("benchmark-configuration-lock-v1.schema.json", "replay-execution-profile-v3.schema.json",
           "replay-measurement-config-v2.schema.json", "replay-execution-request-v3.schema.json")
STOCK_COMPONENTS = {
    "leanprover/lean4:v4.35.0-rc3": ("470d5ce1400764999581fd26d5d72b00d990b0f4", "4c544ed4099c8227f07d5de77ad1e69fb0740a27"),
    "leanprover/lean4:v4.35.0": ("eb6cafb7290e8bd5f2937522d8d804f58f02bafc", "3a2407216ee84a75f9e1aead6803d0578be06ae7"),
    "leanprover/lean4:v4.36.0-rc2": ("b35cb10cf2ced51a2fd1880cfdbf75c71ab7a35a", "4c544ed4099c8227f07d5de77ad1e69fb0740a27"),
}
# Four static configurations in the unchanged five visibility rows of matrixa8bd.
SELECTIONS = {
    ("208b6b22d396f28771dba56414b19f47cae5f2a7", "leanprover/lean4:v4.35.0-rc3"): ("47b79175d09dd3d0c195d5f363a078df60e3cd6e", "."),
    ("85a88c37f80170d9f08651dc63ce6b81a8a48cb9", "leanprover/lean4:v4.35.0-rc3"): ("67084340a111b47741b508fa8cecde5f94301356", "."),
    ("8955681b9914d7ebc7d286414843021b7d6c9de1", "leanprover/lean4:v4.35.0"): ("e1d860fb72dc13bf5d15ed0fb29c49043aa3440b", "toolchains/v4.35.0"),
    ("8955681b9914d7ebc7d286414843021b7d6c9de1", "leanprover/lean4:v4.36.0-rc2"): ("e1d860fb72dc13bf5d15ed0fb29c49043aa3440b", "toolchains/v4.36.0-rc2"),
}


class StockContractError(ValueError):
    """A proposed inactive stock contract is not closed or consistently bound."""


def _require(condition: bool, label: str) -> None:
    if not condition:
        raise StockContractError(label)


def _bounded(value: Any) -> None:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        _require(depth <= 20 and nodes <= 20000, "contract exceeds structural limits")
        if isinstance(current, dict):
            _require(all(isinstance(key, str) for key in current), "contract key is not text")
            pending.extend((item, depth + 1) for pair in current.items() for item in pair)
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
        elif isinstance(current, str):
            _require(len(current) <= 4096 and all(ord(char) >= 32 and not 0xD800 <= ord(char) <= 0xDFFF for char in current), "contract text is invalid")
        elif type(current) is int:
            _require(0 <= current <= 9007199254740991, "contract integer is not safe")
        else:
            _require(current is None or type(current) is bool, "contract value type is invalid")
    _require(len(canonical_json_bytes(value)) <= 65536, "contract exceeds byte limit")


@lru_cache(maxsize=1)
def _validators() -> dict[str, Any]:
    import jsonschema
    import referencing

    registry = referencing.Registry()
    schemas = {}
    for filename in SCHEMAS:
        document = json.loads((ROOT / "schemas" / filename).read_bytes())
        jsonschema.Draft202012Validator.check_schema(document)
        registry = registry.with_resource(document["$id"], referencing.Resource.from_contents(document))
        schemas[filename] = document
    return {name: jsonschema.Draft202012Validator(document, registry=registry)
            for name, document in schemas.items()}


def _schema(value: Any, name: str) -> None:
    import jsonschema

    _bounded(value)
    try:
        _validators()[name].validate(value)
    except jsonschema.ValidationError:
        raise StockContractError("stock contract schema mismatch") from None


def _blob(binding: Mapping[str, str], raw: bytes) -> None:
    _require(type(raw) is bytes and len(raw) <= 1024 * 1024, "benchmark blob is unavailable or oversized")
    digest = hashlib.sha256(raw).hexdigest()
    git_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    _require(binding["sha256"] == digest and binding["git_blob"] == git_blob, "benchmark blob binding differs")


def _json(raw: bytes) -> Any:
    def pairs(items):
        output = {}
        for key, value in items:
            _require(key not in output, "duplicate configuration authority JSON key")
            output[key] = value
        return output

    def constant(_):
        raise StockContractError("nonfinite configuration authority JSON")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise StockContractError("configuration authority is not unambiguous JSON") from None
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        _require(depth <= 20 and nodes <= 50000, "configuration authority exceeds structural limits")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def _configuration_identity(value: Mapping[str, Any]) -> str:
    selected = SELECTIONS.get((value["benchmark_commit"], value["toolchain"]))
    _require(selected is not None, "benchmark/toolchain is outside the bounded stock matrix")
    tree, directory = selected
    _require(value["benchmark_tree"] == tree, "benchmark tree differs")
    _require(value["selection"] == {"kind": "root" if directory == "." else "toolchain_directory", "path": directory}, "selected configuration differs")
    return directory


def validate_configuration_lock(value: Any, benchmark_files: Mapping[str, bytes]) -> dict[str, Any]:
    """Check lock against bytes independently read from its exact trusted Git tree.

    The caller must establish Git origin/commit/tree and regular-file modes before
    supplying benchmark_files. This is not a Git provenance verifier or generator.
    """
    _schema(value, SCHEMAS[0])
    directory = _configuration_identity(value)

    def file(binding, expected_path=None):
        path = binding["path"] if expected_path is None else expected_path
        if expected_path is not None and "path" in binding:
            _require(binding["path"] == path, "benchmark source path differs")
        _require(path in benchmark_files, "required benchmark blob is absent")
        _blob(binding, benchmark_files[path])

    for name, binding in value["selected_files"].items():
        path = name if directory == "." else directory + "/" + name
        file(binding, path)
    pin_path = "lean-toolchain" if directory == "." else directory + "/lean-toolchain"
    _require(benchmark_files[pin_path] == (value["toolchain"] + "\n").encode(), "selected toolchain pin differs")
    if directory == ".":
        _require(value["supported_toolchains"] is None and value["prepare_toolchain"] is None, "root configuration cannot claim a selection registry")
    else:
        _require(value["supported_toolchains"] is not None and value["prepare_toolchain"] is not None, "toolchain directory requires bound original selectors")
        file(value["supported_toolchains"], "supported-toolchains.json")
        file(value["prepare_toolchain"], "scripts/prepare_toolchain.py")
        try:
            registry = _json(benchmark_files["supported-toolchains.json"])
            selection = registry["configurations"][value["toolchain"]]
        except (ValueError, KeyError, TypeError):
            raise StockContractError("benchmark selection registry is invalid") from None
        _require(selection == directory, "receipt toolchain does not select the bound directory")
    file(value["workspace_driver"], "templates/WorkspaceTest.lean")
    sources = value["generator_source_binding"]["sources"]
    paths = [item["path"] for item in sources]
    _require(paths == sorted(set(paths)), "generator sources must be unique and sorted")
    for binding in sources:
        file(binding)
    return value


def configuration_digest(value: Any) -> str:
    _schema(value, SCHEMAS[0])
    _configuration_identity(value)
    return config_digest(CONFIGURATION_DOMAIN, value)


def validate_stock_profile(value: Any, configuration: dict[str, Any]) -> dict[str, Any]:
    _schema(value, SCHEMAS[1])
    _schema(configuration, SCHEMAS[0])
    _require(value["toolchain"] == configuration["toolchain"], "profile/configuration toolchains differ")
    _require(value["benchmark_configuration_digest"] == configuration_digest(configuration), "profile configuration digest differs")
    lean, nanoda = STOCK_COMPONENTS[value["toolchain"]]
    for name in ("lean", "lake", "leanexport", "leanchecker"):
        _require(value["components"][name] == {"repository": "leanprover/lean4", "commit": lean}, "bundled stock component differs")
    _require(value["components"]["nanoda"] == {"repository": "ammkrn/nanoda_lib", "commit": nanoda}, "bundled NanoDa family differs")
    _require(value["components"]["bubblewrap"]["repository"] == "containers/bubblewrap", "stock sandbox component differs")
    version = value["toolchain"].split(":v")[1]
    _require(value["release_artifact"]["name"] == f"lean-{version}-linux.tar.zst", "stock release artifact family differs")
    _require(value["cpu_model"] != "unavailable", "stock runtime CPU identity is unavailable")
    return value


def validate_stock_measurement(value: Any) -> dict[str, Any]:
    _schema(value, SCHEMAS[2])
    return value


def stock_digests(profile: Any, measurement: Any, configuration: Any) -> tuple[str, str]:
    validate_stock_profile(profile, configuration)
    validate_stock_measurement(measurement)
    return config_digest(PROFILE_DOMAIN, profile), config_digest(MEASUREMENT_DOMAIN, measurement)


def validate_stock_request(value: Any, *, benchmark_files: Mapping[str, bytes],
                           authority_raw: bytes, authority_commit: str, authority_path: str) -> dict[str, Any]:
    """Planning-only configuration provenance, never historical task authority.

    The trusted Git reader supplies either an actual receipt, or an original
    Results file for a root-only benchmark. Root provenance does not manufacture
    missing receipt/run fields or establish new workflow/State replay authority.
    """
    _schema(value, SCHEMAS[3])
    benchmark = value["benchmark"]
    configuration = validate_configuration_lock(benchmark["configuration_lock"], benchmark_files)
    _require(benchmark["repository"] == configuration["benchmark_repository"] and benchmark["commit"] == configuration["benchmark_commit"]
             and benchmark["toolchain"] == configuration["toolchain"], "request benchmark configuration differs")
    profile_digest, measurement_digest = stock_digests(value["execution_profile"], value["measurement_config"], configuration)
    _require(value["execution_profile_digest"] == profile_digest and value["measurement_config_digest"] == measurement_digest, "stock request digest differs")
    result = value["result"]
    _require(value["replay_task_id"] == replay_task_id(result["result_id"], measurement_digest), "stock replay identity differs")
    _require(configuration["problem_statement_revisions"].get(result["problem_id"]) == result["statement_revision"], "stock statement revision differs")
    source = value["source"]
    capability = {"fetch_method": source["fetch_method"], "archive_capacity_class": source["archive_capacity_class"]}
    _require(value["execution_profile"]["source_capability"] == capability, "stock source capability differs")
    phase = "public_https_only" if source["fetch_method"] == "git" else "controller_pinned_archive_only"
    _require(value["network"] == {"fetch_phase": phase, "untrusted_execution_phase": "disabled"}, "stock source/network union differs")
    if source["fetch_method"] == "archive":
        _require(source["archive"]["submission_id"] == result["submission_id"], "archive/result submission differs")
        _require(source["archive"]["archive_path"] == canonical_archive_path(result["submission_id"]), "archive path is not canonical")
    else:
        _require(source["commit"] == result["commit"], "public source/result commit differs")
    locator = benchmark["configuration_authority"]
    _require(locator["commit"] == authority_commit and locator["path"] == authority_path, "configuration authority locator differs")
    _require(type(authority_raw) is bytes and len(authority_raw) <= 2 * 1024 * 1024, "configuration authority is unavailable or oversized")
    _require(hashlib.sha256(authority_raw).hexdigest() == locator["sha256"], "configuration authority bytes differ")
    authority = _json(authority_raw)
    if locator["kind"] == "verification_receipt_v1":
        _require(configuration["selection"]["kind"] == "toolchain_directory", "bounded root configuration requires original Git provenance")
        _require(len(authority_raw) <= 16384 and authority_path == f"verification/{result['result_id']}.json", "verification receipt locator differs")
        _bounded(authority)
        expected = {"schema_version": 1, "benchmark_commit": benchmark["commit"], "result_id": result["result_id"],
                    "run_id": locator["run_id"], "run_attempt": locator["run_attempt"], "toolchain": benchmark["toolchain"]}
        _require(authority == expected and all(type(authority[key]) is int for key in ("schema_version", "run_id", "run_attempt")), "verification receipt selection differs")
    else:
        _require(configuration["selection"]["kind"] == "root", "root Git provenance cannot select a toolchain directory")
        _require(locator["root_toolchain"] == configuration["selected_files"]["lean-toolchain"], "original root pin differs")
        try:
            document = validate_v2(authority)
        except ResultsSchemaError:
            raise StockContractError("original Results document is invalid") from None
        _require(authority_path == f"results/{document['user']}.json", "original Results owner/path differs")
        selected = [item for item in document["results"] if item["result_id"] == result["result_id"]]
        _require(len(selected) == 1, "original Result selection is absent or ambiguous")
        record = selected[0]
        _require(hashlib.sha256(canonical_json_bytes(record)).hexdigest() == locator["entry_sha256"], "original Result entry differs")
        _require(record["benchmark_commit"] == benchmark["commit"] and record["problem_id"] == result["problem_id"]
                 and record["statement_revision"] == result["statement_revision"] and record["submission"]["ref"] == result["commit"], "original Result identity differs")
        _require(record["submission"]["public"] == (source["visibility"] == "public"), "original source visibility differs")
        if source["fetch_method"] == "git":
            _require(record["submission"]["repo"] == source["repository"] and record["submission"]["kind"] == "github_repo", "original public source differs")
        if record["intake"]["kind"] == "server":
            _require(record["intake"]["submission_id"] == result["submission_id"], "original server submission differs")
    return value
