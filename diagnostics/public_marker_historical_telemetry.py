#!/usr/bin/env python3
"""One fixed, non-persisting historical metadata query; no runtime operations."""
from __future__ import annotations

import json
import math
import os
import pathlib
import sys
import urllib.error
import urllib.request

ACCOUNT = "a46b90978a1c29cc4795f30677e7e4b8"
SERVICE = "lean-eval-public-guard-a-match"
VERSION = "71d1eba9-a3eb-4361-ba60-e979fdc40999"
SOURCE_COMMIT = "c3e3251001e4a3248b08ff49d0b23aee941a64ac"
RUN = 44675007747
FROM_MS, TO_MS = 1795149120000, 1795149600000
MAX_ROWS, MAX_RESPONSE, MAX_REPORT = 256, 1024 * 1024, 64 * 1024
REPORT_NAME = "public-marker-historical-telemetry.json"
URL = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/workers/observability/telemetry/query"
QUERY_ID = "public-marker-44675007747-readonly"
EVENTS = {"file.write", "sandbox.exec", "sandbox.destroy", "sandbox.destroy.coalesced", "version.check"}
CODES = {"CONTAINER_UNAVAILABLE", "OPERATION_INTERRUPTED", "RPC_TRANSPORT_ERROR",
         "INTERNAL_ERROR", "PROCESS_READY_TIMEOUT", "SERVICE_NOT_RESPONDING"}
LABELS = {"ContainerControlConnection failed": "rpc_connection_failed",
          "ContainerControlConnection established": "rpc_connection_established",
          "ContainerControlConnection upgrade returned retryable status, retrying": "rpc_connection_retry",
          "container.startup": "container_startup"}
REASONS = {"input_transfer_failed", "command_rpc_failed", "command_failed",
           "command_output_invalid", "sandbox_destroy_failed", "unexpected_failure"}


class Rejected(ValueError):
    """A closed failure category, never a provider message."""


def query_body():
    return {"queryId": QUERY_ID, "timeframe": {"from": FROM_MS, "to": TO_MS},
            "dry": True, "view": "events", "limit": MAX_ROWS, "parameters": {
                "datasets": ["cloudflare-workers"], "filterCombination": "and",
                "filters": [{"key": "$metadata.service", "operation": "eq", "type": "string", "value": SERVICE},
                            {"key": "$workers.scriptVersion.id", "operation": "eq", "type": "string", "value": VERSION}]}}


def report(status):
    return {"schema_version": 1, "status": status, "historical_run": RUN,
            "account": ACCOUNT, "service": SERVICE, "worker_version": VERSION,
            "source_commit": SOURCE_COMMIT, "timeframe": {"from": FROM_MS, "to": TO_MS},
            "query_dry": True, "capture_proven": False, "runtime_actions": 0,
            "cause_established": False, "events": []}


def bounded_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise Rejected("schema_failure")
            value[key] = item
        return value

    def constant(_):
        raise Rejected("schema_failure")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise Rejected("schema_failure") from None
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > 20000:
            raise Rejected("schema_failure")
        if type(item) is float and not math.isfinite(item):
            raise Rejected("schema_failure")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def number(value, lower, upper, integer=False):
    if type(value) is int:
        return lower <= value <= upper
    return not integer and type(value) is float and math.isfinite(value) and lower <= value <= upper


def project_source(source):
    if isinstance(source, str):
        # The API explicitly permits string logs. Accept only a bounded JSON
        # object, never infer a cause from an arbitrary human message.
        if len(source.encode("utf-8")) > 16384:
            return None
        try:
            source = bounded_json(source)
        except Rejected:
            return None
    if not isinstance(source, dict):
        raise Rejected("schema_failure")
    event = source.get("event")
    if not isinstance(event, str) or event not in EVENTS:
        message = source.get("message")
        event = LABELS.get(message) if isinstance(message, str) else None
    if source.get("event") == "lean_eval_replay_executor_failure":
        if source.get("route") != "synthetic_acceptance" or source.get("reason") not in REASONS:
            return None
        return {"event": "synthetic_acceptance_failure", "reason": source["reason"]}
    if event is None:
        return None
    result = {"event": event}
    outcome = source.get("outcome")
    if isinstance(outcome, str) and outcome in {"success", "error", "stale_state_abort", "transient_error", "unrecognized_error"}:
        result["outcome"] = outcome
    component = source.get("component")
    if isinstance(component, str) and component in {"sandbox-do", "sandbox-client", "container-control", "container"}:
        result["component"] = component
    code = source.get("code")
    if isinstance(code, str) and code in CODES:
        result["code"] = code
    for key, low, high in (("durationMs", 0, 600000), ("attempt", 1, 1000),
                           ("status", 100, 599), ("exitCode", -255, 255)):
        value = source.get(key)
        if number(value, low, high, integer=key != "durationMs"):
            result[key] = value
    return result


def project(raw):
    if len(raw) > MAX_RESPONSE:
        raise Rejected("response_limit")
    data = bounded_json(raw)
    if (not isinstance(data, dict) or not {"errors", "messages", "result"} <= data.keys()
            or not data.keys() <= {"errors", "messages", "result", "success"}
            or not isinstance(data["errors"], list) or not isinstance(data["messages"], list)):
        raise Rejected("schema_failure")
    if data["errors"] or "success" in data and data["success"] is not True:
        raise Rejected("query_failure")
    result = data["result"]
    if not isinstance(result, dict) or not isinstance(result.get("run"), dict):
        raise Rejected("schema_failure")
    run = result["run"]
    query = run.get("query")
    expected = query_body()
    if (run.get("accountId") != ACCOUNT or run.get("dry") is not True
            or run.get("timeframe") != expected["timeframe"] or not isinstance(query, dict)
            or query.get("id") != QUERY_ID or query.get("adhoc") is not True
            or query.get("parameters") != expected["parameters"]):
        raise Rejected("target_mismatch")
    if run.get("status") != "COMPLETED":
        raise Rejected("query_incomplete")
    # Documented response: result.events is an object, whose events is a list.
    # No list/result fallback and no pagination or follow-up query.
    container = result.get("events")
    if not isinstance(container, dict) or not isinstance(container.get("events"), list):
        raise Rejected("schema_failure")
    rows = container["events"]
    if len(rows) > MAX_ROWS:
        raise Rejected("response_limit")
    count = container.get("count")
    if count is not None and not number(count, len(rows), 10**12):
        raise Rejected("schema_failure")
    output = report("queried")
    output.update(rows_seen=len(rows), rows_ignored=0,
                  possibly_truncated=count is None or count > len(rows) or len(rows) == MAX_ROWS)
    for row in rows:
        if not isinstance(row, dict):
            raise Rejected("schema_failure")
        metadata, workers = row.get("$metadata"), row.get("$workers")
        timestamp = row.get("timestamp")
        if (not isinstance(metadata, dict) or not isinstance(workers, dict)
                or not isinstance(workers.get("scriptVersion"), dict)
                or not isinstance(metadata.get("id"), str) or row.get("dataset") != "cloudflare-workers"):
            raise Rejected("schema_failure")
        if (metadata.get("service") != SERVICE or metadata.get("account", ACCOUNT) != ACCOUNT
                or workers.get("scriptName") != SERVICE or workers["scriptVersion"].get("id") != VERSION
                or not number(timestamp, FROM_MS, TO_MS - 1, integer=True)):
            raise Rejected("target_mismatch")
        if workers.get("truncated") is True:
            output["possibly_truncated"] = True
        item = project_source(row.get("source"))
        if item is None:
            output["rows_ignored"] += 1
        else:
            output["events"].append({"timestamp": timestamp, **item})
    output["rows_selected"] = len(output["events"])
    return output


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def query_once(token):
    request = urllib.request.Request(URL, data=json.dumps(query_body(), separators=(",", ":")).encode(),
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                                     method="POST")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=60) as response:
            if response.status != 200:
                return http_failure(response.status)
            return project(response.read(MAX_RESPONSE + 1))
    except urllib.error.HTTPError as error:
        # Never read an error body, even to print an apparently harmless message.
        status = error.code
        error.close()
        return http_failure(status)
    except (OSError, TimeoutError, urllib.error.URLError):
        return report("transport_failure")
    except Rejected as error:
        return report(error.args[0])
    except Exception:
        return report("schema_failure")


def http_failure(status):
    output = report("denied" if status in (401, 403) else "rate_limited" if status == 429 else "http_failure")
    if type(status) is int and 100 <= status <= 599:
        output["http_status"] = status
    return output


def encode_report(output):
    raw = (json.dumps(output, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(raw) > MAX_REPORT:
        raise Rejected("report_limit")
    return raw


def main():
    os.umask(0o077)
    token = os.environ.pop("CLOUDFLARE_API_TOKEN", "")
    directory = os.environ.get("RUNNER_TEMP", "")
    admitted = (len(sys.argv) == 1 and os.environ.get("GITHUB_REPOSITORY") == "leanprover/lean-eval-submissions"
                and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                and os.environ.get("GITHUB_REF") == "refs/heads/main"
                and os.environ.get("GITHUB_REF_PROTECTED") == "true")
    os.environ.clear()
    try:
        root = pathlib.Path(directory)
        if not admitted or not root.is_absolute() or root.is_symlink() or not root.is_dir():
            print("Historical telemetry admission refused.")
            return 1
        path = root / REPORT_NAME
        if path.exists() or path.is_symlink():
            print("Historical telemetry report already exists; query refused.")
            return 1
        output = query_once(token) if token else report("credential_unavailable")
        token = ""
        try:
            encoded = encode_report(output)
        except Rejected:
            output = report("report_limit")
            encoded = encode_report(output)
        # Never overwrite a symlink or an earlier report from another invocation.
        with path.open("xb") as target:
            target.write(encoded)
        print("Historical telemetry safe report written.")
        return 0 if output["status"] == "queried" else 1
    except Exception:
        print("Historical telemetry diagnostic stopped.")
        return 1
    finally:
        token = ""


if __name__ == "__main__":
    raise SystemExit(main())
