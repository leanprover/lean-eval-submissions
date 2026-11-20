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
import uuid

SCRIPT_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from historical_public_runner import canonical_document_bytes
from historical_replay_controller import validate_executor_verdict
from replay_orchestrator import config_digest, replay_task_id
from reserve_historical_public_cleanup import TransportError, request_http
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


class CommandFailure(ValueError):
    pass


class RetirementPending(ValueError):
    pass


class TransientRpcError(ValueError):
    pass


def remaining_timeout(deadline, maximum):
    if deadline is None:
        return maximum
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TransientRpcError("bounded readback deadline expired")
    return min(maximum, remaining)


def command(*argv, cwd=ROOT, timeout=180):
    try:
        return subprocess.check_output(argv, cwd=cwd, timeout=timeout, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        detail = ""
        if "wrangler" in argv:
            detail = error.stderr.decode("utf-8", errors="replace")
            for key in ("CLOUDFLARE_API_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
                value = os.environ.get(key)
                if value:
                    detail = detail.replace(value, "[redacted]")
            detail = re.sub(r"(?:AGE-SECRET-KEY-|eyJ)[A-Za-z0-9_.+-]+", "[redacted]", detail)
            detail = re.sub(r"[A-Za-z0-9_+/=-]{128,}", "[redacted]", detail)
            detail = re.sub(r"[^\x20-\x7e\n\t]", "", detail)[:4096]
        raise CommandFailure(f"{pathlib.Path(argv[0]).name} failed with exit {error.returncode}; {detail}") from None


def cf(path, method="GET", allow_missing=False, timeout=30):
    if os.environ.get("CLOUDFLARE_ACCOUNT_ID") != ACCOUNT:
        raise ValueError("unexpected Cloudflare account")
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    r = request_http(method, f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/{path}", {"Authorization": "Bearer " + token}, None, timeout)
    if allow_missing and r.status == 404:
        return None
    if r.status in (429, 500, 502, 503, 504):
        raise TransientRpcError("Cloudflare control-plane response temporarily unavailable")
    if r.status != 200:
        raise ValueError(f"Cloudflare {method} returned HTTP {r.status}")
    if method == "DELETE" and not r.body:
        return {"success": True}
    data = json.loads(r.body)
    if data.get("success") is not True:
        raise ValueError("Cloudflare response did not confirm success")
    return data


def namespaces(deadline=None):
    rows, total = [], None
    for page in range(1, 21):
        data = cf(f"workers/durable_objects/namespaces?per_page=100&page={page}", timeout=remaining_timeout(deadline, 30))
        info, batch = data["result_info"], data["result"]
        count = info["total_count"]
        if (type(count) is not int or not 0 <= count <= 2000 or info["page"] != page
                or info["per_page"] != 100 or info["count"] != len(batch)
                or (total is not None and total != count)):
            raise ValueError("namespace inventory is truncated or changing")
        total = count
        rows.extend(batch)
        if len({row["id"] for row in rows}) != len(rows) or len(rows) > total:
            raise ValueError("namespace inventory is duplicated")
        if len(rows) == total:
            return rows
        if len(batch) != 100:
            raise ValueError("namespace inventory is incomplete")
    raise ValueError("namespace inventory exceeds its closed limit")


def applications(timeout=180):
    try:
        return json.loads(command("npx", "--prefix", "server", "wrangler", "containers", "list", "--json", timeout=timeout))
    except (CommandFailure, subprocess.TimeoutExpired):
        raise TransientRpcError("container inventory temporarily unavailable") from None


def application_details(config, namespace_id, deadline):
    """Resolve only the named summary to its account- and namespace-bound detail."""
    rows = applications(timeout=remaining_timeout(deadline, 60))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("container summary inventory is invalid")
    name = config["containers"][0]["name"]
    matches = [row for row in rows if row.get("name") == name]
    if len(matches) > 1:
        raise ValueError("container application identity is ambiguous")
    if not matches:
        return []  # Absence must come from a successful, fresh inventory read.
    summary = matches[0]
    identity = summary.get("id")
    try:
        if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
            raise ValueError
    except ValueError:
        raise ValueError("container summary identity is invalid") from None
    try:
        detail = json.loads(command("npx", "--prefix", "server", "wrangler", "containers", "info", identity, "--json", timeout=remaining_timeout(deadline, 60)))
    except (CommandFailure, subprocess.TimeoutExpired):
        # An info failure, including disappearance between list and info, is
        # never absence or deletion authority. Retry the bounded inventory read.
        raise TransientRpcError("container detail temporarily unavailable") from None
    if not isinstance(detail, dict):
        raise ValueError("container application detail is invalid")
    if any(detail.get(key) != summary.get(key) for key in ("id", "name", "version", "created_at")):
        raise ValueError("container summary and detail identities disagree")
    if (detail.get("account_id") != ACCOUNT
            or detail.get("durable_objects") != {"namespace_id": namespace_id}):
        raise ValueError("container application account or namespace differs")
    configuration = detail.get("configuration")
    if not isinstance(configuration, dict) or summary.get("image") != configuration.get("image"):
        raise ValueError("container summary and detail images disagree")
    return [detail]  # Preserve the full provider record for the strict verifier.


def wait_applications(deadline, config, namespace_id):
    for retry in range(10):
        try:
            return application_details(config, namespace_id, deadline)
        except (TransientRpcError, TransportError):
            if retry == 9:
                raise ValueError("container inventory did not converge") from None
            time.sleep(remaining_timeout(deadline, 3))


def http(url, body=None, oidc=False, timeout_budget=None):
    headers = {}
    if oidc:
        token_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=lean-eval-replay-staging"
        token = request_http("GET", token_url, {"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]}, None, 30)
        if token.status in (429, 500, 502, 503, 504):
            raise TransientRpcError("staging OIDC mint temporarily unavailable")
        if token.status != 200:
            raise ValueError("staging OIDC mint failed")
        headers["Authorization"] = "Bearer " + json.loads(token.body)["value"]
    raw = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    if raw is not None:
        headers["Content-Type"] = "application/json"
    timeout = 30 if body is None else 300 if url.endswith(("/staging-acceptance", "/historical-public-replay")) else 60
    if timeout_budget is not None:
        timeout = min(timeout, timeout_budget)
    r = request_http("GET" if body is None else "POST", url, headers, raw, timeout)
    try:
        return r.status, json.loads(r.body)
    except (ValueError, UnicodeError):
        if r.status in (429, 500, 502, 503, 504):
            return r.status, {"error": "transport_response_invalid"}
        raise ValueError("executor response is not JSON") from None


def endpoint_health(url, receipt):
    status, health = http(url + "/healthz")
    if status in (429, 500, 502, 503, 504) and health.get("status") != "retired":
        raise TransientRpcError("temporary endpoint health unavailable")
    if status != 200 or health != receipt["health"]:
        raise ValueError("temporary endpoint health identity changed")


def render(case, profile, commit, task):
    worker = "lean-eval-public-guard-" + case
    image = f"registry.cloudflare.com/{ACCOUNT}/lean-eval-historical-public-v1:{profile['benchmark_commit']}-{profile['image_source_commit']}@{profile['registry_manifest_digest']}"
    reviewed = copy.deepcopy(profile["execution_profile"])
    if case.endswith("mismatch"):
        reviewed["kernel_release"] = "intentional-staging-guard-mismatch"
    variables = dict(load(ROOT / "server/wrangler.replay.jsonc")["env"]["staging"]["vars"])
    variables.update(DEPLOYED_COMMIT=commit, HISTORICAL_PUBLIC_REPLAY_ENABLED="true",
                     GITHUB_OIDC_ENVIRONMENT="cloudflare-staging",
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


def ownership(config, deadline=None):
    deadline = time.monotonic() + 180 if deadline is None else deadline
    def read(path):
        return cf(path, timeout=remaining_timeout(deadline, 30))
    worker = config["name"]
    service = read(f"workers/services/{worker}")
    settings = read(f"workers/scripts/{worker}/settings")
    deployments = read(f"workers/scripts/{worker}/deployments")
    version, _ = _active_version(service, deployments, worker)
    version_data = read(f"workers/scripts/{worker}/versions/{version}")
    status, health = http(f"https://{worker}.lean-eval.workers.dev/healthz", timeout_budget=remaining_timeout(deadline, 30))
    if status in (429, 500, 502, 503, 504) and not (isinstance(health, dict) and health.get("status") == "retired"):
        raise TransientRpcError("owned staging health temporarily unavailable")
    if status != 200:
        raise ValueError("owned staging health unavailable")
    actual_version, ids = _verify_worker(config, service, settings, deployments, version_data, health)
    inventory = {n["id"]: n for n in namespaces(deadline)}
    for binding in config["durable_objects"]["bindings"]:
        row = inventory.get(ids[binding["name"]])
        if row is None:
            raise TransientRpcError("created namespace listing has not converged")
        if row["script"] != worker or row["class"] != binding["class_name"]:
            raise ValueError("bound namespace belongs to a different script or class")
    app = _verify_application(config, application_details(config, ids["REPLAY_SANDBOX"], deadline))
    if app is None:
        raise ValueError("owned staging container is missing")
    return {"worker_version": actual_version, "namespaces": ids, "health": health, **app}


def wait_ownership(config, deadline=None, *, allow_propagation=True):
    deadline = time.monotonic() + 180 if deadline is None else deadline
    for retry in range(10):
        try:
            return ownership(config, deadline)
        except (TransientRpcError, TransportError, CommandFailure, subprocess.TimeoutExpired, KeyError):
            pass
        except ValueError:
            if not allow_propagation:
                raise  # Established ownership drift must never be retried/adopted.
        if retry == 9 or time.monotonic() >= deadline:
            raise ValueError("created ownership readback did not converge; controlled recovery required") from None
        time.sleep(min(5, remaining_timeout(deadline, 5)))


def deploy(case, directory):
    config, receipt = load(directory / "config.json"), load(directory / "receipt.json")
    if cf(f"workers/services/{config['name']}", allow_missing=True) is not None or any(a["name"] == receipt["application"] for a in applications(timeout=60)) or any(n["script"] == config["name"] for n in namespaces(time.monotonic() + 60)):
        raise ValueError("temporary resource name collision; refuse adoption")
    receipt["created"] = True
    save(directory / "receipt.json", receipt)
    command("npx", "wrangler", "deploy", "--config", f".public-guard-{case}.json", "--env", "staging", cwd=ROOT / "server")
    receipt.update(wait_ownership(config))
    save(directory / "receipt.json", receipt)
    # These resources were absent before deployment and the exact new namespace,
    # application and immutable image are now verified. There is no previous
    # application image to roll out. run_case must establish execution readiness
    # through the existing source-free marker before sending any public Source.
    # Application-wide provisioning counters do not establish that readiness.


def run_case(case, directory):
    if os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("STATE_WRITE_KEY"):
        raise ValueError("deployment or State writer credential reached execution")
    config, receipt, request = load(directory / "config.json"), load(directory / "receipt.json"), load(directory / "request.json")
    required = {"worker_version", "namespaces", "health", "application_id", "application_version", "application_created_at"}
    if not receipt.get("created") or not required.issubset(receipt) or any(not receipt[k] for k in required):
        raise ValueError("exact ownership was not established")
    url = "https://" + config["name"] + ".lean-eval.workers.dev"
    # The existing synthetic route reports actual host identity and destroys
    # its source-free marker sandbox. No private archive or AWS is involved.
    identity = directory / "marker-identity.age"
    command("age-keygen", "-o", str(identity), timeout=30)
    recipient = command("age-keygen", "-y", str(identity), timeout=30).decode().strip()
    marker = secrets.token_hex(32).encode()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        member = tarfile.TarInfo("marker.bin")
        member.size, member.mode = len(marker), 0o600
        tar.addfile(member, io.BytesIO(marker))
    encrypted = subprocess.run(["age", "-r", recipient], input=archive.getvalue(), capture_output=True, check=True, timeout=30).stdout
    observation = {"schema_version": 1, "request_id": "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f", "runner_nonce": secrets.token_hex(32), "archive_ciphertext_sha256": hashlib.sha256(encrypted).hexdigest(), "ciphertext_base64": base64.b64encode(encrypted).decode(), "plaintext_identity_base64": base64.b64encode(identity.read_bytes()).decode(), "marker_sha256": hashlib.sha256(marker).hexdigest()}
    identity.unlink()
    endpoint_health(url, receipt)
    receipt["safe_to_delete"] = False
    receipt["phase"] = "runtime-observation"
    receipt["outcome"] = "incomplete"
    receipt.pop("runtime_readiness", None)
    save(directory / "receipt.json", receipt)
    status, evidence = http(url + "/api/v1/staging-acceptance", observation, True)
    refusal = {401: {"error": "unauthorized"}, 400: {"error": "invalid_request"},
               503: {"error": "staging_acceptance_disabled"}}
    if status in refusal and evidence == refusal[status]:
        receipt.update(safe_to_delete=True, phase="observation-refused", refusal_status=status)
        save(directory / "receipt.json", receipt)
        raise ValueError("staging observation refused before sandbox creation")
    expected = {"schema_version": 1, "service": "lean-eval-replay-executor", "environment": "staging", "request_id": observation["request_id"], "runner_nonce": observation["runner_nonce"], "archive_ciphertext_sha256": observation["archive_ciphertext_sha256"], "marker_sha256": observation["marker_sha256"], "network_policy": "disabled", "network_probe": "blocked", "destruction": "confirmed", "staging_memory_limit_bytes": 12 * 1024**3, "production_memory_gate_bytes": 12 * 1024**3}
    if status != 200 or set(evidence) != set(expected) | {"architecture", "kernel_release", "cpu_model"} or any(evidence[k] != v for k, v in expected.items()):
        raise ValueError("runtime observation or destruction is unconfirmed")
    receipt["safe_to_delete"] = True
    receipt["observation_http_destruction"] = "confirmed"
    receipt["observed_runtime"] = {k: evidence[k] for k in ("architecture", "kernel_release", "cpu_model")}
    save(directory / "receipt.json", receipt)
    if any(evidence[k] != load(directory / "profile.json")[k] for k in receipt["observed_runtime"]):
        raise ValueError("observed runtime differs from the frozen profile; stop")
    receipt["runtime_readiness"] = "source-free-marker-confirmed"
    save(directory / "receipt.json", receipt)
    binding = {k: request[k] for k in ("schema_version", "replay_task_id", "attempt")}
    endpoint_health(url, receipt)
    status, response = http(url + "/api/v1/historical-public-replay/cleanup-reservation", binding, True)
    if status != 200 or response != {**binding, "status": "reserved"}:
        raise ValueError("cleanup reservation unconfirmed")
    status_request = {k: request[k] for k in ("schema_version", "runner_nonce", "replay_task_id", "attempt", "handoff_sha256", "source_archive_sha256", "execution_profile_digest", "measurement_config_digest", "vm_image_digest")}
    endpoint_health(url, receipt)
    receipt["phase"], receipt["safe_to_delete"] = "guarded-replay", False
    save(directory / "receipt.json", receipt)
    try:
        status, response = http(url + "/api/v1/historical-public-replay", request, True)
        if status != 202 or response != {**binding, "status": "running"}:
            raise ValueError("guarded executor start did not confirm running")
        deadline = time.monotonic() + (300 if case.endswith("mismatch") else 3600)
        rpc_failures = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                receipt["outcome"] = "incomplete"
                save(directory / "receipt.json", receipt)
                raise ValueError("staging controller deadline reached; incomplete")
            time.sleep(min(15, remaining))
            try:
                endpoint_health(url, receipt)
                status, response = http(url + "/api/v1/historical-public-replay/status", status_request, True)
            except (TransportError, TransientRpcError):
                rpc_failures += 1
                if rpc_failures > 2:
                    raise ValueError("staging status RPC did not recover") from None
                continue
            transient = (status in (502, 503, 504) and response == {"error": "transport_response_invalid"}
                         or status == 500 and response.get("error") == "executor_failed"
                         and response.get("reason") == "command_rpc_failed")
            if transient:
                rpc_failures += 1
                if rpc_failures > 2:
                    raise ValueError("staging status RPC did not recover")
                continue
            rpc_failures = 0
            if status == 202:
                if response != {**binding, "status": "running"}:
                    raise ValueError("guarded status identity differs")
                continue
            break
        if case.endswith("mismatch"):
            if status != 500 or response != {"error": "executor_failed", "reason": "command_failed", "detail": "runtime_profile_mismatch"}:
                raise ValueError("negative guard did not produce the exact failure")
            outcome = "expected-runtime-mismatch"
        else:
            if status != 200 or validate_executor_verdict(request, response)["checker_outcome"] != "accepted":
                raise ValueError("matching tiny fixture was not accepted")
            outcome = "accepted-nanoda"
    except Exception:
        receipt["outcome"] = "incomplete"
        save(directory / "receipt.json", receipt)
        raise
    finally:
        try:
            endpoint_health(url, receipt)
            status, response = http(url + "/api/v1/historical-public-replay/cleanup", binding, True)
            if status != 200 or response != {**binding, "destruction": "confirmed"}:
                raise ValueError("exact sandbox destruction is unconfirmed")
            receipt["safe_to_delete"] = True
            receipt["public_http_destruction"] = "confirmed"
        except Exception:
            receipt["outcome"] = "incomplete"
            raise
        finally:
            save(directory / "receipt.json", receipt)
    receipt["outcome"] = outcome
    save(directory / "receipt.json", receipt)


def retired_identity(config, receipt, original=None, deadline=None):
    """Verify either fixed no-container phase; never infer identity from names."""
    def read(path):
        return cf(path, timeout=remaining_timeout(deadline, 30))
    worker = config["name"]
    service, deployments = read(f"workers/services/{worker}"), read(f"workers/scripts/{worker}/deployments")
    version, _ = _active_version(service, deployments, worker)
    settings = read(f"workers/scripts/{worker}/settings")["result"]
    active = read(f"workers/scripts/{worker}/versions/{version}")["result"]
    inventory, bound_ids = _binding_inventory(settings["bindings"], "retirement settings")
    resources = active["resources"]
    version_inventory, version_ids = _binding_inventory(resources["bindings"], "retirement version")
    expected_ids = receipt["namespaces"] if config["durable_objects"]["bindings"] else {}
    expected_inventory = _expected_inventory(config)
    if original is not None:
        old_inventory = _expected_inventory(original)
        if old_inventory != expected_inventory and (inventory == old_inventory or version_inventory == old_inventory):
            known = {receipt["worker_version"], receipt.get("disabled_worker_version")}
            target = version_inventory == expected_inventory and version_ids == expected_ids
            if version not in known and not target:
                raise ValueError("unexpected Worker version during retirement")
            raise RetirementPending("known previous Worker view still visible")
    if (inventory != expected_inventory or bound_ids != expected_ids or version_ids != expected_ids
            or version_inventory != inventory or active["id"] != version
            or settings["compatibility_date"] != config["compatibility_date"]
            or sorted(settings["compatibility_flags"]) != sorted(config["compatibility_flags"])
            or resources["script"]["etag"] != service["result"]["default_environment"]["script"]["etag"]
            or resources["script_runtime"].get("containers", []) != []
            or resources["script_runtime"]["migration_tag"] != config["migrations"][-1]["tag"]):
        raise ValueError("retirement Worker settings or active code identity differ")
    status, body = http(f"https://{worker}.lean-eval.workers.dev/healthz", timeout_budget=remaining_timeout(deadline, 30))
    if status in (404, 502, 504) or response_is_transient(status, body):
        raise RetirementPending("retirement edge response still unavailable")
    disabled_health = {**receipt["health"], "replay_enabled": False,
                       "historical_public_replay_enabled": False, "staging_acceptance_enabled": False}
    final = not expected_ids
    wanted = {"status": "retired", "commit": receipt["commit"], "ownership": config["vars"]["EXECUTOR_OWNERSHIP_TAG"]} if final else disabled_health
    if status != (503 if final else 200) or body != wanted:
        if original is not None and status == 200 and body in (receipt["health"], disabled_health):
            raise RetirementPending("known previous health view still visible")
        raise ValueError("retirement Worker code/commit/tag is unconfirmed")
    if final and not namespaces_absent(receipt, worker, deadline):
        raise RetirementPending("recorded owned namespace deletion still propagating")
    return version


def namespaces_absent(receipt, worker, deadline=None):
    remaining = [n for n in namespaces(deadline) if n["id"] in receipt["namespaces"].values() or n["script"] == worker]
    for row in remaining:
        expected = {receipt["namespaces"]["REPLAY_SANDBOX"]: "ReplaySandbox",
                    receipt["namespaces"]["REPLAY_TERMINAL_RECEIPT"]: "ReplayTerminalReceipt"}
        if row["id"] not in expected or row["script"] != worker or row["class"] != expected[row["id"]]:
            raise ValueError("unexpected namespace identity during retirement")
    return not remaining


def response_is_transient(status, body):
    return status in (502, 503, 504) and body == {"error": "transport_response_invalid"}


def wait_retired(config, original, receipt, deadline=None):
    for retry in range(10):
        try:
            return retired_identity(config, receipt, original, deadline)
        except (RetirementPending, TransportError, TransientRpcError):
            if retry == 9:
                raise ValueError("retirement readback did not converge") from None
            time.sleep(remaining_timeout(deadline, 3))


def cleanup(case, directory):
    config, receipt = load(directory / "config.json"), load(directory / "receipt.json")
    if not receipt["created"]:
        return
    deadline = time.monotonic() + 540  # Leave one minute inside the step for receipts.
    required = {"worker_version", "namespaces", "health", "application_id", "application_version", "application_created_at"}
    if not required.issubset(receipt):
        receipt.update(recovery_required="creation-ownership-unrecorded", outcome="incomplete")
        save(directory / "receipt.json", receipt)
        raise ValueError("resources created but ownership never recorded; controlled recovery required")
    receipt["cleanup_mode"] = "http-confirmed-or-not-started" if receipt["safe_to_delete"] else "owned-control-plane-fence"
    if not receipt["safe_to_delete"]:
        receipt["outcome"] = "incomplete"
    save(directory / "receipt.json", receipt)
    disabled = copy.deepcopy(config)
    disabled["containers"] = []
    disabled["vars"].update(REPLAY_ENABLED="false", HISTORICAL_PUBLIC_REPLAY_ENABLED="false", STAGING_ACCEPTANCE_ENABLED="false")
    stub = copy.deepcopy(disabled)
    stub.update(main="../runtime-guard-staging/retired.js", durable_objects={"bindings": []})
    stub["migrations"].append({"tag": "v3-retired", "deleted_classes": ["ReplaySandbox", "ReplayTerminalReceipt"]})
    def deploy_config(rendered, suffix):
        path = ROOT / f"server/.public-guard-{suffix}-{case}.json"
        save(path, {**rendered, "env": {"staging": rendered}})
        command("npx", "wrangler", "deploy", "--config", path.name, "--env", "staging", cwd=ROOT / "server", timeout=remaining_timeout(deadline, 120))
    phase = receipt.get("retirement_phase", "original")
    if phase == "original":
        current = wait_ownership(config, deadline, allow_propagation=False)
        if any(current[k] != receipt[k] for k in current):
            raise ValueError("resource ownership changed; refuse deletion")
        receipt["retirement_phase"] = "disabling"
        save(directory / "receipt.json", receipt)
        try:
            deploy_config(disabled, "disabled")
        except (CommandFailure, subprocess.TimeoutExpired) as error:
            # CommandFailure is already bounded/redacted by command(). Only the
            # subsequent exact readback can prove whether deployment applied.
            print(str(error)[:4096], file=sys.stderr)
        receipt["disabled_worker_version"] = wait_retired(disabled, config, receipt, deadline)
        receipt["retirement_phase"] = "disabled"
        save(directory / "receipt.json", receipt)
        phase = "disabled"
    if phase in ("disabling", "retiring-classes"):
        raise ValueError("retirement version was not recorded; controlled recovery required")
    if phase in ("disabled", "application-absent"):
        disabled_version = receipt["disabled_worker_version"]
        if wait_retired(disabled, None, receipt, deadline) != disabled_version:
            raise ValueError("disabled Worker changed before application deletion")
        # Official order: remove Container config, delete app/instances, THEN class.
        for retry in range(10):
            app = _verify_application(config, wait_applications(deadline, config, receipt["namespaces"]["REPLAY_SANDBOX"]))
            if app is None:
                break
            if app != {k: receipt[k] for k in app}:
                raise ValueError("container identity changed before deletion")
            if retry == 9:
                raise ValueError("owned container remains after deletion")
            if wait_retired(disabled, None, receipt, deadline) != disabled_version:
                raise ValueError("disabled Worker changed before application deletion")
            receipt["application_delete_requested"] = True
            save(directory / "receipt.json", receipt)
            try:
                command("npx", "--prefix", "server", "wrangler", "containers", "delete", receipt["application_id"], timeout=remaining_timeout(deadline, 60))
            except (CommandFailure, subprocess.TimeoutExpired):
                pass
            time.sleep(remaining_timeout(deadline, 3))
        receipt["retirement_phase"] = "application-absent"
        save(directory / "receipt.json", receipt)
        if wait_retired(disabled, None, receipt, deadline) != disabled_version:
            raise ValueError("disabled Worker changed before class deletion")
        receipt["retirement_phase"] = "retiring-classes"
        save(directory / "receipt.json", receipt)
        try:
            deploy_config(stub, "retired")
        except (CommandFailure, subprocess.TimeoutExpired) as error:
            print(str(error)[:4096], file=sys.stderr)
        receipt["stub_version"] = wait_retired(stub, disabled, receipt, deadline)
        receipt["retirement_phase"] = "retired"
        save(directory / "receipt.json", receipt)
    if receipt.get("retirement_phase") != "retired":
        raise ValueError("unknown retirement checkpoint; controlled recovery required")
    if _verify_application(config, wait_applications(deadline, config, receipt["namespaces"]["REPLAY_SANDBOX"])) is not None:
        raise ValueError("application absence changed before Worker deletion")
    worker, stub_version = config["name"], receipt["stub_version"]
    for retry in range(5):
        try:
            if cf(f"workers/services/{worker}", allow_missing=True, timeout=remaining_timeout(deadline, 30)) is None:
                break
            if retry == 4:
                raise ValueError("temporary Worker remains")
            if wait_retired(stub, None, receipt, deadline) != stub_version:
                raise ValueError("retired Worker changed before deletion")
            cf(f"workers/services/{worker}", method="DELETE", allow_missing=True, timeout=remaining_timeout(deadline, 30))
        except (TransportError, TransientRpcError):
            if retry == 4:
                raise ValueError("temporary Worker absence did not converge") from None
        time.sleep(remaining_timeout(deadline, 5))
    for retry in range(10):
        try:
            if namespaces_absent(receipt, worker, deadline):
                break
        except (TransportError, TransientRpcError):
            pass
        if retry == 9:
            raise ValueError("temporary namespace absence did not converge")
        time.sleep(remaining_timeout(deadline, 3))
    if _verify_application(config, wait_applications(deadline, config, receipt["namespaces"]["REPLAY_SANDBOX"])) is not None:
        raise ValueError("temporary application reappeared; refuse adoption")
    receipt.update(cleanup="confirmed", control_plane_cleanup="confirmed")
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
        detail = str(error) if type(error) in (ValueError, CommandFailure) else "retain exact target receipt for diagnosis"
        print("public runtime staging stopped: " + type(error).__name__ + "; " + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
