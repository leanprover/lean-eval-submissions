#!/usr/bin/env python3
"""One source-free marker with finite receipt-DO evidence; never run public Source."""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import tarfile
import time

SPEC = importlib.util.spec_from_file_location("fixed_marker_campaign", pathlib.Path(__file__).with_name("check.py"))
campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign)
ROOT = campaign.ROOT
WORKFLOW = ".github/workflows/public-marker-phase-staging.yml"
SERVICE = "lean-eval-public-guard-a-match"
CASE = "a-match"
REPORT = "finite-marker-safe-receipt.json"
OPERATIONS = ("sandbox_handle", "write_ciphertext", "write_identity", "write_expectation", "exec_acceptance", "validate_evidence", "destroy")
CODES = {"CONTAINER_UNAVAILABLE", "OPERATION_INTERRUPTED", "RPC_TRANSPORT_ERROR", "INTERNAL_ERROR", "PROCESS_READY_TIMEOUT", "SERVICE_NOT_RESPONDING", "unclassified"}
STATES = {"prepared", "active", "terminal", "fenced", "expired"}
OUTCOMES = {"not_attempted", "pending", "succeeded", "failed"}


def require(condition):
    if not condition:
        raise ValueError("finite marker refused")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(domain, value):
    return hashlib.sha256((domain + "\0" + value).encode()).hexdigest()


def admission():
    sha = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None)
    require(os.environ.get("GITHUB_REPOSITORY") == "leanprover/lean-eval-submissions"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_REF") == "refs/tags/lean-eval-dispatch/" + sha
            and os.environ.get("GITHUB_WORKFLOW_REF") == "leanprover/lean-eval-submissions/" + WORKFLOW + "@refs/tags/lean-eval-dispatch/" + sha
            and os.environ.get("GITHUB_WORKFLOW_SHA") == sha
            and os.environ.get("GITHUB_REF_PROTECTED") == "true"
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and re.fullmatch(r"[1-9][0-9]{0,19}", os.environ.get("GITHUB_RUN_ID", "")) is not None)
    return sha


def locations():
    temp = pathlib.Path(os.environ.get("RUNNER_TEMP", ""))
    require(temp.is_absolute() and temp.is_dir() and not temp.is_symlink())
    directory = temp / "finite-marker-phase"
    require(not directory.is_symlink())
    return directory, temp / REPORT


def write(path, value):
    require(not path.is_symlink())
    path.write_text(canonical(value) + "\n")
    path.chmod(0o600)


def report_path():
    return locations()[1]


def update_report(**fields):
    path = report_path()
    report = json.loads(path.read_bytes())
    report.update(fields)
    raw = canonical(report).encode()
    require(len(raw) <= 16384)
    write(path, report)


def prepare(directory):
    require(not directory.exists() and not report_path().exists())
    directory.mkdir(mode=0o700)
    profile = campaign.load(ROOT / f"evidence/public-replay/profiles/{campaign.FAMILIES['a'][0]}.json")
    sha = admission()
    # This reads frozen profile metadata only; never call campaign.prepare().
    task = campaign.replay_task_id(campaign.FAMILIES['a'][2], profile["measurement_config_digest"])
    config, expected = campaign.render(CASE, profile, sha, task)
    nonce = secrets.token_hex(32)
    start_before = int(time.time() * 1000) + 15 * 60_000
    scope = {"schema_version": 1, "case": CASE, "service": SERVICE, "workflow": WORKFLOW,
             "commit": sha, "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": "1",
             "start_before": start_before, "retain_until": start_before + 15 * 60_000,
             "nonce_sha256": digest("lean-eval-marker-nonce-v1", nonce),
             "sandbox_name_sha256": digest("lean-eval-marker-sandbox-v1", "r-" + nonce[:61]),
             "execution_profile_digest": config["vars"]["REVIEWED_EXECUTION_PROFILE_DIGEST"],
             "measurement_config_digest": profile["measurement_config_digest"], "image_digest": profile["registry_manifest_digest"]}
    config.update(observability={"enabled": False}, version_metadata={"binding": "CF_VERSION_METADATA"})
    config["vars"].update(REPLAY_ENABLED="false", HISTORICAL_PUBLIC_REPLAY_ENABLED="false",
                         STAGING_ACCEPTANCE_ENABLED="true", MARKER_PHASE_SCOPE=canonical(scope))
    write(directory / "nonce.json", {"runner_nonce": nonce})
    write(directory / "config.json", config)
    write(directory / "profile.json", expected)
    write(directory / "receipt.json", {"case": CASE, "worker": SERVICE,
          "application": config["containers"][0]["name"], "commit": sha,
          "created": False, "safe_to_delete": True})
    write(ROOT / "server/.public-guard-a-match.json", {**config, "env": {"staging": config}})
    write(report_path(), {"schema_version": 1, "scope": scope, "provider_boundary": "unknown",
          "configuration_awaited": False, "marker_post_attempted": False, "phase_capture_proven": False,
          "marker_verdict": "not_attempted", "journal_fenced": False,
          "control_plane_cleanup": "not_attempted", "scratch_cleanup": "not_attempted",
          "intended_timeouts_ms": {"client": 300000, "command": 120000, "instance_get": 120000, "port_ready": 180000}})


def binding(scope, version, observation):
    require(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", version) is not None)
    scope_hash = digest("lean-eval-marker-scope-v1", canonical(scope))
    instance = digest("lean-eval-marker-instance-v1", canonical({"scope_sha256": scope_hash,
        "worker_version": version, "nonce_sha256": scope["nonce_sha256"], "sandbox_name_sha256": scope["sandbox_name_sha256"]}))
    request = digest("lean-eval-marker-request-v1", canonical({"marker_instance_id": instance,
        **{key: observation[key] for key in ("request_id", "archive_ciphertext_sha256", "marker_sha256")}}))
    return {"schema_version": 1, "scope_sha256": scope_hash, "worker_version": version,
            "marker_instance_id": instance, "request_commitment": request}


def safe_journal(value, expected):
    require(isinstance(value, dict) and set(value) == {"schema_version", "journal", "provider_boundary", "configuration_awaited"}
            and type(value["schema_version"]) is int and value["schema_version"] == 1 and value["provider_boundary"] == "unknown"
            and value["configuration_awaited"] is False)
    journal = value["journal"]
    require(isinstance(journal, dict) and set(journal) == {"schema_version", "binding", "start_before", "retain_until", "state", "claimed", "events", "operation_outcome", "destruction_outcome"})
    scope = campaign.load(report_path())["scope"]
    require(type(journal["schema_version"]) is int and journal["schema_version"] == 1 and journal["binding"] == expected
            and journal["start_before"] == scope["start_before"] and journal["retain_until"] == scope["retain_until"]
            and journal["state"] in STATES and type(journal["claimed"]) is bool
            and journal["operation_outcome"] in OUTCOMES and journal["destruction_outcome"] in OUTCOMES
            and isinstance(journal["events"], list) and len(journal["events"]) <= 20)
    events, completed, pending, seen_starts = [], [], set(), set()
    operation_outcome, destruction_outcome = "pending" if journal["claimed"] else "not_attempted", "not_attempted"
    for event in journal["events"]:
        require(isinstance(event, dict) and set(event) <= {"operation", "state", "elapsed_ms", "code"}
                and event.get("operation") in OPERATIONS and event.get("state") in {"started", "succeeded", "failed"})
        if event["state"] == "started":
            require(set(event) == {"operation", "state"} and event["operation"] not in seen_starts)
            if event["operation"] != "destroy":
                require(not pending and operation_outcome == "pending" and destruction_outcome == "not_attempted"
                        and len(completed) < len(OPERATIONS) - 1 and event["operation"] == OPERATIONS[len(completed)])
            else:
                require(destruction_outcome == "not_attempted" and any(item["operation"] == "sandbox_handle" for item in events))
                destruction_outcome = "pending"
            # Cleanup is independent of the one sequential application call.
            # Its completion must not erase an already-issued late append.
            seen_starts.add(event["operation"])
            pending.add(event["operation"])
        else:
            require(type(event.get("elapsed_ms")) is int and 0 <= event["elapsed_ms"] <= 600000
                    and ("code" not in event or event["state"] == "failed" and event["code"] in CODES)
                    and event["operation"] in pending)
            pending.remove(event["operation"])
            if event["operation"] == "destroy":
                destruction_outcome = event["state"]
            else:
                completed.append(event["operation"])
                if event["state"] == "failed":
                    operation_outcome = "failed"
                elif event["operation"] == "validate_evidence":
                    operation_outcome = "succeeded"
        events.append({key: event[key] for key in ("operation", "state", "elapsed_ms", "code") if key in event})
    require(journal["claimed"] or not events)
    if journal["state"] != "expired":
        require(journal["operation_outcome"] == operation_outcome and journal["destruction_outcome"] == destruction_outcome)
    if journal["state"] == "terminal":
        require(operation_outcome in {"succeeded", "failed"} and destruction_outcome != "pending")
    projected = {key: journal[key] for key in ("start_before", "retain_until", "state", "claimed", "operation_outcome", "destruction_outcome")}
    projected.update(schema_version=1, binding=dict(expected))
    projected["events"] = events
    require(len(canonical(projected).encode()) <= 8192)
    return projected


def token():
    response = campaign.diagnostic_request("GET", os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=lean-eval-replay-staging",
        {"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]}, None, 30, None, "oidc_mint")
    require(response.status == 200)
    value = json.loads(response.body)["value"]
    require(isinstance(value, str) and 0 < len(value) <= 8192)
    return value


def post(path, body, credential, timeout=5, checkpoint=None):
    require(path in {"", "/phase-proof", "/phase-status", "/phase-fence"})
    response = campaign.diagnostic_request("POST", f"https://{SERVICE}.lean-eval.workers.dev/api/v1/staging-acceptance" + path,
        {"Authorization": "Bearer " + credential, "Content-Type": "application/json"},
        canonical(body).encode(), timeout, checkpoint, "endpoint_request")
    require(len(response.body) <= 16384)
    return response.status, json.loads(response.body)


def execution_environment():
    require(not any(os.environ.get(key) for key in ("CLOUDFLARE_API_TOKEN", "STATE_WRITE_KEY",
        "LEGACY_ARCHIVE_IDENTITY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")))


def marker_body(directory):
    nonce = campaign.load(directory / "nonce.json")["runner_nonce"]
    require(isinstance(nonce, str) and re.fullmatch(r"[0-9a-f]{64}", nonce) is not None)
    identity = directory / "marker-identity.age"
    require(not identity.exists() and not identity.is_symlink())
    try:
        campaign.command("age-keygen", "-o", str(identity), timeout=30)
        recipient = campaign.command("age-keygen", "-y", str(identity), timeout=30).decode().strip()
        marker = secrets.token_hex(32).encode()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            member = tarfile.TarInfo("marker.bin")
            member.size, member.mode = len(marker), 0o600
            tar.addfile(member, io.BytesIO(marker))
        encrypted = subprocess.run(["age", "-r", recipient], input=archive.getvalue(), capture_output=True, check=True, timeout=30).stdout
        return {"schema_version": 1, "request_id": "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f", "runner_nonce": nonce,
                "archive_ciphertext_sha256": hashlib.sha256(encrypted).hexdigest(), "ciphertext_base64": base64.b64encode(encrypted).decode(),
                "plaintext_identity_base64": base64.b64encode(identity.read_bytes()).decode(), "marker_sha256": hashlib.sha256(marker).hexdigest()}
    finally:
        identity.unlink(missing_ok=True)


def run(directory):
    execution_environment()
    receipt, config = campaign.load(directory / "receipt.json"), campaign.load(directory / "config.json")
    require(receipt.get("created") is True and all(receipt.get(key) for key in ("worker_version", "namespaces", "health", "application_id")))
    campaign.endpoint_health(f"https://{SERVICE}.lean-eval.workers.dev", receipt)
    scope = json.loads(config["vars"]["MARKER_PHASE_SCOPE"])
    require(int(time.time() * 1000) < scope["start_before"])
    observation = marker_body(directory)
    expected = binding(scope, receipt["worker_version"], observation)
    write(directory / "binding.json", expected)
    update_report(binding=expected)
    credential = token()
    request = {"request_commitment": expected["request_commitment"], "schema_version": 1}
    try:
        status, value = post("/phase-proof", request, credential)
        require(status == 200)
        proof = safe_journal(value, expected)
        require(proof["state"] == "prepared" and not proof["claimed"] and not proof["events"])
        status, value = post("/phase-status", request, credential)
        require(status == 200 and safe_journal(value, expected) == proof)
        update_report(phase_capture_proven=True, journal=proof)
        receipt.update(safe_to_delete=False, phase="runtime-observation", outcome="incomplete")
        write(directory / "receipt.json", receipt)
        update_report(marker_post_attempted=True, marker_verdict="incomplete")
        def checkpoint(value):
            # These are constructed by the existing positive client projector.
            require(set(value) <= {"stage", "state", "timeout_seconds", "elapsed_ms", "cause", "timed_out"})
            require(value["stage"] == "endpoint_request" and value["state"] in {"pending", "responded", "failed"}
                    and value["timeout_seconds"] == 300
                    and ("elapsed_ms" not in value or type(value["elapsed_ms"]) is int and 0 <= value["elapsed_ms"] <= 600000)
                    and ("cause" not in value or value["cause"] in {"timeout", "connection_reset", "dns", "tls", "unknown_transport"})
                    and ("timed_out" not in value or type(value["timed_out"]) is bool))
            update_report(marker_http=value)
        matched = False
        try:
            status, value = post("", observation, credential, 300, checkpoint)
            profile = campaign.load(directory / "profile.json")
            if status == 200:
                require(isinstance(value, dict) and set(value) == {"schema_version", "marker_instance_id", "architecture", "kernel_release", "cpu_model", "network_policy", "network_probe", "destruction"})
                matched = (value["schema_version"] == 1 and value["marker_instance_id"] == expected["marker_instance_id"]
                    and value["network_policy"] == "disabled" and value["network_probe"] == "blocked"
                    and value["destruction"] == "confirmed" and all(value[k] == profile[k] for k in ("architecture", "kernel_release", "cpu_model")))
        except (campaign.TransportError, ValueError, KeyError, TypeError):
            pass
        deadline = time.monotonic() + 90
        journal = proof
        for attempt in range(3):
            if attempt:
                time.sleep(min(30, max(0, deadline - time.monotonic())))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                status, value = post("/phase-status", request, credential, min(5, remaining))
                require(status == 200)
                journal = safe_journal(value, expected)
                update_report(journal=journal)
            except (campaign.TransportError, ValueError, KeyError, TypeError):
                break
            if journal["state"] in {"terminal", "fenced", "expired"}:
                break
        passed = matched and journal["state"] == "terminal" and journal["operation_outcome"] == "succeeded" and journal["destruction_outcome"] == "succeeded"
        receipt["safe_to_delete"] = bool(passed)
        if passed:
            receipt["observation_http_destruction"] = "confirmed"
        write(directory / "receipt.json", receipt)
        update_report(marker_verdict="succeeded" if passed else "incomplete", profile_matched=matched)
        # This process always stops here, even on success. No public Source.
        require(passed)
    finally:
        observation.clear()
        credential = ""


def fence(directory):
    execution_environment()
    if not (directory / "binding.json").exists():
        return
    expected = campaign.load(directory / "binding.json")
    credential = token()
    try:
        status, value = post("/phase-fence", {"request_commitment": expected["request_commitment"], "schema_version": 1}, credential)
        require(status == 200)
        journal = safe_journal(value, expected)
        require(journal["state"] == "fenced")
        update_report(journal_fenced=True, journal=journal)
    finally:
        credential = ""


def owned_operation(operation, directory):
    # Existing ownership/retirement logic stays unchanged. Never export its
    # console/provider diagnostics; keep only validated cleanup categories.
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        if operation == "deploy":
            campaign.deploy(CASE, directory)
            receipt = campaign.load(directory / "receipt.json")
            update_report(owned_target=owned_target(receipt))
        elif (directory / "receipt.json").exists():
            try:
                campaign.cleanup(CASE, directory)
            finally:
                receipt = campaign.load(directory / "receipt.json")
                fields = {"control_plane_cleanup": "confirmed" if receipt.get("control_plane_cleanup") == "confirmed"
                          else "unconfirmed" if receipt.get("created") else "not_created"}
                if all(key in receipt for key in ("worker_version", "namespaces", "application_id", "application_version", "application_created_at")):
                    fields["owned_target"] = owned_target(receipt)
                fields["resource_creation_recorded"] = receipt.get("created") is True
                if report_path().exists():
                    update_report(**fields)


def owned_target(receipt):
    # The existing ownership verifier proved these exact provider identities.
    # Keep enough for controlled recovery even if retirement fails and private
    # scratch is scrubbed; never export the raw health/config/provider objects.
    version = receipt["worker_version"]
    require(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", version) is not None)
    namespaces = receipt["namespaces"]
    require(isinstance(namespaces, dict) and set(namespaces) == {"REPLAY_SANDBOX", "REPLAY_TERMINAL_RECEIPT"}
            and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None for value in namespaces.values()))
    require(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", receipt["application_id"]) is not None
            and type(receipt["application_version"]) is int and receipt["application_version"] > 0
            and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z", receipt["application_created_at"]) is not None)
    result = {"worker": SERVICE, "worker_version": version, "namespace_ids": namespaces,
              **{key: receipt[key] for key in ("application_id", "application_version", "application_created_at")}}
    if "retirement_phase" in receipt:
        require(receipt["retirement_phase"] in {"original", "disabling", "disabled", "application-absent", "retiring-classes", "retired"})
        result["retirement_phase"] = receipt["retirement_phase"]
    for key in ("disabled_worker_version", "stub_version"):
        if key in receipt:
            require(isinstance(receipt[key], str) and re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", receipt[key]) is not None)
            result[key] = receipt[key]
    return result


def scrub(directory):
    if directory.exists():
        shutil.rmtree(directory)
    generated = [ROOT / f"server/.public-guard-{suffix}a-match.json" for suffix in ("", "disabled-", "retired-")]
    for path in generated:
        path.unlink(missing_ok=True)
    tools = report_path().parent / "finite-marker-tools"
    archive = report_path().parent / "finite-marker-age.tar.gz"
    if tools.exists():
        shutil.rmtree(tools)
    archive.unlink(missing_ok=True)
    require(not directory.exists() and not tools.exists() and not tools.is_symlink() and not archive.exists()
            and all(not path.exists() and not path.is_symlink() for path in generated))
    if report_path().exists():
        update_report(scratch_cleanup="confirmed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "deploy", "run", "fence", "cleanup", "scrub"))
    args = parser.parse_args()
    os.umask(0o077)
    try:
        admission()
        directory, _ = locations()
        if args.operation == "prepare":
            prepare(directory)
        elif args.operation in {"deploy", "cleanup"}:
            owned_operation(args.operation, directory)
        else:
            {"run": run, "fence": fence, "scrub": scrub}[args.operation](directory)
        print("Finite marker operation recorded.")
        return 0
    except Exception:
        print("Finite marker operation stopped; retain the safe receipt.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
