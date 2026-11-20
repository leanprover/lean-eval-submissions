#!/usr/bin/env python3
"""Finite four-case PUBLIC guard compatibility check; remove after the campaign."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import tarfile
import time

SCRIPT_DIRECTORY = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from historical_public_runner import canonical_document_bytes
from historical_replay_controller import validate_executor_verdict
from replay_orchestrator import config_digest, replay_task_id
from reserve_historical_public_cleanup import request_http
from verify_historical_private_executor_ownership import (
    _active_version, _binding_inventory, _expected_inventory,
    _verify_application, _verify_worker,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCOUNT = "a46b90978a1c29cc4795f30677e7e4b8"
PLAN = "d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e"
MATRIX = "a674707eea7a9556576c8dcbe57bcf6b4f44362d2bdfd47895fb7c783554f39c"
CONTRACT = "6d341a642dfd6aa9092228269da6761000bf0818128ce3f35cb259bd8fb2303f"
FAMILIES = {
    "a": ("7d4027e5022973640bf768facd4ac56762cc327c542c560b5cc34aa59fcd771e", "prr_36423560b14a93479a87f049cc4b8cc163641cec55e39ed9a03fe7fda7f913f7", "r2_74fce01acef5afc2b1c0cbff8f342e79b456db79dd596e1a0792f7cbe775088a"),
    "b": ("51c052d354ad46c5b77b7282e80c48a95d765f606c9395bd585a0028407c9823", "prr_3261de806aba22c956ecf604cd53c4096f16ab43bf314e02e7749871da85feb1", "r2_ade7940aba2fd61d0526b858cce932efc743af8034b0d8b75a914289eb6aabe1"),
}
CASES = ("a-match", "a-mismatch", "b-match", "b-mismatch")


def load(path):
    return json.loads(pathlib.Path(path).read_bytes())


def save(path, value):
    pathlib.Path(path).write_bytes(canonical_document_bytes(value))


def command(*argv, cwd=ROOT):
    return subprocess.check_output(argv, cwd=cwd, timeout=180, stderr=subprocess.PIPE)


def cf(path, method="GET", allow_missing=False):
    if os.environ.get("CLOUDFLARE_ACCOUNT_ID") != ACCOUNT:
        raise ValueError("unexpected Cloudflare account")
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    r = request_http(method, f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/{path}", {"Authorization": "Bearer " + token}, None, 30)
    if allow_missing and r.status == 404:
        return None
    if r.status != 200:
        raise ValueError(f"Cloudflare {method} returned HTTP {r.status}")
    data = json.loads(r.body)
    if data.get("success") is not True:
        raise ValueError("Cloudflare response did not confirm success")
    return data


def namespaces():
    rows = []
    for page in range(1, 21):
        data = cf(f"workers/durable_objects/namespaces?per_page=100&page={page}")
        info = data["result_info"]
        if info["total_pages"] > 20 or info["page"] != page:
            raise ValueError("namespace inventory is truncated")
        rows.extend(data["result"])
        if page >= info["total_pages"]:
            return rows
    raise ValueError("namespace inventory is incomplete")


def applications():
    return json.loads(command("npx", "--prefix", "server", "wrangler", "containers", "list", "--json"))


def http(url, body=None, oidc=False):
    headers = {}
    if oidc:
        token_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=lean-eval-replay-staging"
        token = request_http("GET", token_url, {"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]}, None, 30)
        if token.status != 200:
            raise ValueError("staging OIDC mint failed")
        headers["Authorization"] = "Bearer " + json.loads(token.body)["value"]
    raw = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    if raw is not None:
        headers["Content-Type"] = "application/json"
    r = request_http("GET" if body is None else "POST", url, headers, raw, 120)
    return r.status, json.loads(r.body)


def render(case, profile, commit, task):
    worker = "lean-eval-public-guard-" + case
    image = f"registry.cloudflare.com/{ACCOUNT}/lean-eval-historical-public-v1:{profile['benchmark_commit']}-{profile['image_source_commit']}@{profile['registry_manifest_digest']}"
    reviewed = copy.deepcopy(profile["execution_profile"])
    if case.endswith("mismatch"):
        reviewed["kernel_release"] = "intentional-staging-guard-mismatch"
    variables = dict(load(ROOT / "server/wrangler.replay.jsonc")["env"]["staging"]["vars"])
    variables.update(DEPLOYED_COMMIT=commit, HISTORICAL_PUBLIC_REPLAY_ENABLED="true",
                     REVIEWED_EXECUTION_PROFILE_DIGEST=config_digest("lean-eval-replay-execution-profile-v1", reviewed),
                     REVIEWED_MEASUREMENT_CONFIG_DIGEST=profile["measurement_config_digest"],
                     REVIEWED_VM_IMAGE_DIGEST=profile["registry_manifest_digest"],
                     EXPECTED_REPLAY_TASK_ID=task, EXPECTED_REPLAY_ATTEMPT="1",
                     EXECUTOR_OWNERSHIP_TAG=hashlib.sha256((commit + case).encode()).hexdigest())
    config = {"name": worker, "main": "src/replay-entry.ts", "compatibility_date": "2026-08-22",
              "compatibility_flags": ["nodejs_compat"], "workers_dev": True, "preview_urls": False,
              "containers": [{"name": "le-pg-" + case, "class_name": "ReplaySandbox", "image": image,
                              "instance_type": "standard-4", "max_instances": 1, "ssh": {"enabled": False}}],
              "durable_objects": {"bindings": [{"name": "REPLAY_SANDBOX", "class_name": "ReplaySandbox"}, {"name": "REPLAY_TERMINAL_RECEIPT", "class_name": "ReplayTerminalReceipt"}]},
              "migrations": [{"tag": "v1", "new_sqlite_classes": ["ReplaySandbox"]}, {"tag": "v2", "new_sqlite_classes": ["ReplayTerminalReceipt"]}], "vars": variables}
    return config, reviewed


def prepare(case, directory):
    digest, request_id, result_id = FAMILIES[case[0]]
    qualification = load(ROOT / f"evidence/public-replay/profiles/{digest}.json")
    authority = load(ROOT / f"evidence/public-replay/plans/{PLAN}.json")
    selected = next(r for r in authority["requests"] if r["request_id"] == request_id)
    source = selected["source"]
    for name, url, commit in (("source", ("https://gist.github.com/" if source["kind"] == "gist" else "https://github.com/") + source["repository"] + ".git", source["commit"]), ("benchmark", "https://github.com/leanprover/lean-eval.git", qualification["benchmark_commit"])):
        path = directory / name
        command("git", "init", "--quiet", str(path))
        command("git", "-C", str(path), "remote", "add", "origin", url)
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "-c", "credential.helper=", "-c", "http.extraheader=", "-C", str(path), "fetch", "--quiet", "--depth=1", "origin", commit], env=env, timeout=120, check=True)
        command("git", "-C", str(path), "checkout", "--quiet", "--detach", commit)
    archive, handoff = directory / "source.tar.gz", directory / "handoff.json"
    common = ["--plan", f"evidence/public-replay/plans/{PLAN}.json", "--profile-matrix", "configuration/historical-public-replay-profile-matrix-v1.json", "--contract", "configuration/historical-public-runner-v1.json", "--expected-plan-sha256", PLAN, "--expected-profile-matrix-sha256", MATRIX, "--expected-contract-sha256", CONTRACT, "--request-id", request_id, "--result-id", result_id, "--benchmark-repository", str(directory / "benchmark"), "--source-archive", str(archive)]
    if case[0] == "a":
        command(sys.executable, "scripts/historical_public_gist_source_adapter.py", "prepare", *common, "--gist-checkout", str(directory / "source"), "--handoff", str(handoff), "--output", str(directory / "adapter.json"))
    else:
        command(sys.executable, "scripts/historical_public_runner.py", "prepare", *common, "--source-repository", str(directory / "source"), "--output", str(handoff))
    task = replay_task_id(result_id, qualification["measurement_config_digest"])
    commit = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid staging source commit")
    config, profile = render(case, qualification, commit, task)
    save(directory / "config.json", config)
    save(ROOT / f"server/.public-guard-{case}.json", {**config, "env": {"staging": config}})
    body = {"schema_version": 1, "runner_nonce": secrets.token_hex(32), "replay_task_id": task, "attempt": 1,
            "handoff_sha256": hashlib.sha256(handoff.read_bytes()).hexdigest(), "source_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "execution_profile_digest": config["vars"]["REVIEWED_EXECUTION_PROFILE_DIGEST"], "measurement_config_digest": qualification["measurement_config_digest"], "vm_image_digest": qualification["registry_manifest_digest"], "execution_profile": profile, "handoff": load(handoff), "source_archive_base64": base64.b64encode(archive.read_bytes()).decode()}
    save(directory / "request.json", body)
    save(directory / "profile.json", qualification["execution_profile"])
    save(directory / "receipt.json", {"case": case, "worker": config["name"], "application": config["containers"][0]["name"], "commit": commit, "safe_to_delete": True, "created": False, "qualified_profile_digest": digest, "configured_profile_digest": body["execution_profile_digest"], "vm_image_digest": body["vm_image_digest"]})


def ownership(config):
    worker = config["name"]
    service = cf(f"workers/services/{worker}")
    settings = cf(f"workers/scripts/{worker}/settings")
    deployments = cf(f"workers/scripts/{worker}/deployments")
    version, _ = _active_version(service, deployments, worker)
    version_data = cf(f"workers/scripts/{worker}/versions/{version}")
    status, health = http(f"https://{worker}.lean-eval.workers.dev/healthz")
    if status != 200:
        raise ValueError("owned staging health unavailable")
    actual_version, ids = _verify_worker(config, service, settings, deployments, version_data, health)
    inventory = {n["id"]: n for n in namespaces()}
    for binding in config["durable_objects"]["bindings"]:
        row = inventory[ids[binding["name"]]]
        if row["script"] != worker or row["class"] != binding["class_name"]:
            raise ValueError("bound namespace belongs to a different script or class")
    app = _verify_application(config, applications())
    if app is None:
        raise ValueError("owned staging container is missing")
    return {"worker_version": actual_version, "namespaces": ids, **app}


def deploy(case, directory):
    config, receipt = load(directory / "config.json"), load(directory / "receipt.json")
    if cf(f"workers/services/{config['name']}", allow_missing=True) is not None or any(a["name"] == receipt["application"] for a in applications()) or any(n["script"] == config["name"] for n in namespaces()):
        raise ValueError("temporary resource name collision; refuse adoption")
    receipt["created"] = True
    save(directory / "receipt.json", receipt)
    command("npx", "wrangler", "deploy", "--config", f".public-guard-{case}.json", "--env", "staging", cwd=ROOT / "server")
    subprocess.run([sys.executable, "scripts/wait_replay_container_rollout.py", "--config", f"server/.public-guard-{case}.json", "--environment", "staging", "--image-family", "historical-public", "--application", receipt["application"]], cwd=ROOT, check=True, timeout=1500)
    receipt.update(ownership(config))
    save(directory / "receipt.json", receipt)


def run_case(case, directory):
    if os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("STATE_WRITE_KEY"):
        raise ValueError("deployment or State writer credential reached execution")
    config, receipt, request = load(directory / "config.json"), load(directory / "receipt.json"), load(directory / "request.json")
    if not receipt.get("worker_version"):
        raise ValueError("exact ownership was not established")
    url = "https://" + config["name"] + ".lean-eval.workers.dev"
    # The existing synthetic route reports actual host identity and destroys
    # its source-free marker sandbox. No private archive or AWS is involved.
    identity = directory / "marker-identity.age"
    command("age-keygen", "-o", str(identity))
    recipient = command("age-keygen", "-y", str(identity)).decode().strip()
    marker = secrets.token_hex(32).encode()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        member = tarfile.TarInfo("marker.bin")
        member.size, member.mode = len(marker), 0o600
        tar.addfile(member, io.BytesIO(marker))
    encrypted = subprocess.run(["age", "-r", recipient], input=archive.getvalue(), capture_output=True, check=True, timeout=30).stdout
    observation = {"schema_version": 1, "request_id": "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f", "runner_nonce": secrets.token_hex(32), "archive_ciphertext_sha256": hashlib.sha256(encrypted).hexdigest(), "ciphertext_base64": base64.b64encode(encrypted).decode(), "plaintext_identity_base64": base64.b64encode(identity.read_bytes()).decode(), "marker_sha256": hashlib.sha256(marker).hexdigest()}
    identity.unlink()
    receipt["safe_to_delete"] = False
    receipt["phase"] = "runtime-observation"
    save(directory / "receipt.json", receipt)
    status, evidence = http(url + "/api/v1/staging-acceptance", observation, True)
    expected = {"schema_version": 1, "service": "lean-eval-replay-executor", "environment": "staging", "request_id": observation["request_id"], "runner_nonce": observation["runner_nonce"], "archive_ciphertext_sha256": observation["archive_ciphertext_sha256"], "marker_sha256": observation["marker_sha256"], "network_policy": "disabled", "network_probe": "blocked", "destruction": "confirmed", "staging_memory_limit_bytes": 12 * 1024**3, "production_memory_gate_bytes": 12 * 1024**3}
    if status != 200 or set(evidence) != set(expected) | {"architecture", "kernel_release", "cpu_model"} or any(evidence[k] != v for k, v in expected.items()):
        raise ValueError("runtime observation or destruction is unconfirmed")
    receipt["safe_to_delete"] = True
    receipt["observed_runtime"] = {k: evidence[k] for k in ("architecture", "kernel_release", "cpu_model")}
    save(directory / "receipt.json", receipt)
    if any(evidence[k] != load(directory / "profile.json")[k] for k in receipt["observed_runtime"]):
        raise ValueError("observed runtime differs from the frozen profile; stop")
    binding = {k: request[k] for k in ("schema_version", "replay_task_id", "attempt")}
    status, response = http(url + "/api/v1/historical-public-replay/cleanup-reservation", binding, True)
    if status != 200 or response != {**binding, "status": "reserved"}:
        raise ValueError("cleanup reservation unconfirmed")
    status_request = {k: request[k] for k in ("schema_version", "runner_nonce", "replay_task_id", "attempt", "handoff_sha256", "source_archive_sha256", "execution_profile_digest", "measurement_config_digest", "vm_image_digest")}
    receipt["phase"], receipt["safe_to_delete"] = "guarded-replay", False
    save(directory / "receipt.json", receipt)
    try:
        status, response = http(url + "/api/v1/historical-public-replay", request, True)
        if status != 202 or response != {**binding, "status": "running"}:
            raise ValueError("guarded executor start did not confirm running")
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            time.sleep(15)
            status, response = http(url + "/api/v1/historical-public-replay/status", status_request, True)
            if status != 202:
                break
            if response != {**binding, "status": "running"}:
                raise ValueError("guarded status identity differs")
        if case.endswith("mismatch"):
            if status != 500 or response != {"error": "executor_failed", "reason": "command_failed", "detail": "runtime_profile_mismatch"}:
                raise ValueError("negative guard did not produce the exact failure")
            receipt["outcome"] = "expected-runtime-mismatch"
        else:
            if status != 200 or validate_executor_verdict(request, response)["checker_outcome"] != "accepted":
                raise ValueError("matching tiny fixture was not accepted")
            receipt["outcome"] = "accepted-nanoda"
    finally:
        status, response = http(url + "/api/v1/historical-public-replay/cleanup", binding, True)
        if status != 200 or response != {**binding, "destruction": "confirmed"}:
            raise ValueError("exact sandbox destruction is unconfirmed")
        receipt["safe_to_delete"] = True
        save(directory / "receipt.json", receipt)


def retired_identity(config, receipt):
    """Read back the fixed stub, active version and emptied namespace scope."""
    worker = config["name"]
    service, deployments = cf(f"workers/services/{worker}"), cf(f"workers/scripts/{worker}/deployments")
    version, _ = _active_version(service, deployments, worker)
    settings = cf(f"workers/scripts/{worker}/settings")["result"]
    active = cf(f"workers/scripts/{worker}/versions/{version}")["result"]
    inventory, bound_ids = _binding_inventory(settings["bindings"], "retired settings")
    resources = active["resources"]
    version_inventory, version_ids = _binding_inventory(resources["bindings"], "retired version")
    if (inventory != _expected_inventory(config) or bound_ids or version_ids
            or version_inventory != inventory or active["id"] != version
            or settings["compatibility_date"] != config["compatibility_date"]
            or sorted(settings["compatibility_flags"]) != sorted(config["compatibility_flags"])
            or resources["script"]["etag"] != service["result"]["default_environment"]["script"]["etag"]
            or resources["script_runtime"].get("containers", []) != []
            or resources["script_runtime"]["migration_tag"] != "v3-retired"):
        raise ValueError("retired Worker settings or active code identity differ")
    status, body = http(f"https://{worker}.lean-eval.workers.dev/healthz")
    if status != 503 or body != {"status": "retired", "commit": receipt["commit"], "ownership": config["vars"]["EXECUTOR_OWNERSHIP_TAG"]}:
        raise ValueError("retired Worker code/commit/tag is unconfirmed")
    if any(n["id"] in receipt["namespaces"].values() or n["script"] == worker for n in namespaces()):
        raise ValueError("owned namespaces have not disappeared; stop cleanup")
    return version


def cleanup(case, directory):
    config, receipt = load(directory / "config.json"), load(directory / "receipt.json")
    if not receipt["created"]:
        return
    if not receipt["safe_to_delete"] and receipt.get("phase") == "guarded-replay":
        # An interrupted start/status may have left a live exact reserved sandbox.
        # The existing idempotent cleanup route is the only destruction authority.
        request = load(directory / "request.json")
        binding = {k: request[k] for k in ("schema_version", "replay_task_id", "attempt")}
        status, response = http("https://" + config["name"] + ".lean-eval.workers.dev/api/v1/historical-public-replay/cleanup", binding, True)
        if status == 200 and response == {**binding, "destruction": "confirmed"}:
            receipt["safe_to_delete"] = True
            save(directory / "receipt.json", receipt)
    if not receipt["safe_to_delete"]:
        raise ValueError("retain exact target receipt: sandbox destruction is unconfirmed")
    current = ownership(config)
    if any(current[k] != receipt[k] for k in current):
        raise ValueError("resource ownership changed; refuse deletion")
    stub = copy.deepcopy(config)
    stub.update(main="../runtime-guard-staging/retired.js", containers=[], durable_objects={"bindings": []})
    stub["migrations"].append({"tag": "v3-retired", "deleted_classes": ["ReplaySandbox", "ReplayTerminalReceipt"]})
    stub["vars"].update(REPLAY_ENABLED="false", HISTORICAL_PUBLIC_REPLAY_ENABLED="false", STAGING_ACCEPTANCE_ENABLED="false")
    path = ROOT / f"server/.public-guard-retired-{case}.json"
    save(path, {**stub, "env": {"staging": stub}})
    # Retire only the previously captured, freshly reverified namespace pair.
    command("npx", "wrangler", "deploy", "--config", path.name, "--env", "staging", cwd=ROOT / "server")
    stub_version = retired_identity(stub, receipt)
    app = _verify_application(config, applications())
    if app is not None:
        if app != {k: receipt[k] for k in app}:
            raise ValueError("container identity changed before deletion")
        if retired_identity(stub, receipt) != stub_version:
            raise ValueError("retired Worker changed before container deletion")
        command("npx", "--prefix", "server", "wrangler", "containers", "delete", receipt["application_id"])
    for retry in range(10):
        if _verify_application(config, applications()) is None:
            break
        if retry == 9:
            raise ValueError("owned container remains after deletion")
        time.sleep(3)
    if retired_identity(stub, receipt) != stub_version:
        raise ValueError("retired Worker version changed before deletion")
    worker = config["name"]
    cf(f"workers/services/{worker}", method="DELETE")
    for retry in range(4):
        if cf(f"workers/services/{worker}", allow_missing=True) is None:
            break
        if retry == 3:
            raise ValueError("temporary Worker remains")
        time.sleep(5)
    if any(n["id"] in receipt["namespaces"].values() or n["script"] == worker for n in namespaces()):
        raise ValueError("temporary namespace remains")
    receipt.update(cleanup="confirmed", stub_version=stub_version)
    save(directory / "receipt.json", receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "deploy", "run", "cleanup"))
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--directory", type=pathlib.Path, required=True)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    try:
        {"prepare": prepare, "deploy": deploy, "run": run_case, "cleanup": cleanup}[args.operation](args.case, args.directory)
    except Exception as error:
        print("public runtime staging stopped: " + type(error).__name__ + "; retain exact target receipt for diagnosis", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
