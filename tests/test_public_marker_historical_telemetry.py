from __future__ import annotations

import copy
import datetime as dt
import fnmatch
import importlib.util
import io
import json
import os
import pathlib
import re
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = "diagnostics/public_marker_historical_telemetry.py"
WORKFLOW = ".github/workflows/public-marker-historical-telemetry.yml"
SPEC = importlib.util.spec_from_file_location("marker_telemetry", ROOT / HELPER)
telemetry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(telemetry)
SECRET = "secret-sentinel-AGE-SECRET-KEY-not-for-output"


def fixture(source=None):
    # Concrete values in the documented Cloudflare query response shape:
    # result.run + result.events.{count,events}; no synthetic success envelope.
    return {"errors": [], "messages": [{"message": "Successful request"}], "result": {
        "run": {"id": "query-run", "accountId": telemetry.ACCOUNT, "dry": True,
                "granularity": 0, "query": {"id": telemetry.QUERY_ID, "adhoc": True,
                    "created": "2026-11-20T05:00:00Z", "createdBy": "user", "description": "",
                    "name": "diagnostic", "parameters": telemetry.query_body()["parameters"],
                    "updated": "2026-11-20T05:00:00Z", "updatedBy": "user"},
                "status": "COMPLETED", "timeframe": telemetry.query_body()["timeframe"], "userId": "user"},
        "statistics": {"bytes_read": 100, "elapsed": 0.1, "rows_read": 1},
        "events": {"count": 1, "events": [{"$metadata": {"id": "event", "account": telemetry.ACCOUNT,
            "service": telemetry.SERVICE, "message": SECRET, "error": SECRET, "url": SECRET},
            "dataset": "cloudflare-workers", "timestamp": telemetry.FROM_MS,
            "$workers": {"eventType": "rpc", "scriptName": telemetry.SERVICE,
                         "scriptVersion": {"id": telemetry.VERSION, "message": SECRET}, "truncated": False},
            "source": source if source is not None else {"event": "sandbox.exec", "outcome": "error",
                "component": "sandbox-do", "durationMs": 300001, "code": "CONTAINER_UNAVAILABLE",
                "message": SECRET, "command": SECRET, "errorMessage": SECRET,
                "context": {"originalMessage": SECRET, "body": SECRET},
                "error": {"message": SECRET, "stack": SECRET, "name": SECRET}}}]}}}


def raw(value):
    return json.dumps(value).encode()


class Response:
    status = 200

    def __init__(self, body):
        self.body, self.reads, self.closed = body, [], False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self, limit):
        self.reads.append(limit)
        return self.body[:limit]


class HistoricalMarkerTelemetryTests(unittest.TestCase):
    def test_exact_non_persisting_request_and_one_http_call(self):
        response = Response(raw(fixture()))
        with mock.patch.object(telemetry.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            result = telemetry.query_once(SECRET)
        build.assert_called_once()
        proxy, redirect = build.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertIsNone(redirect.redirect_request(None, None, 302, SECRET, {}, "https://foreign.invalid"))
        build.return_value.open.assert_called_once()
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.cloudflare.com/client/v4/accounts/a46b90978a1c29cc4795f30677e7e4b8/workers/observability/telemetry/query")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {
            "queryId": "public-marker-44675007747-readonly", "dry": True, "view": "events", "limit": 256,
            "timeframe": {"from": 1795149120000, "to": 1795149600000},
            "parameters": {"datasets": ["cloudflare-workers"], "filterCombination": "and",
                "filters": [{"key": "$metadata.service", "operation": "eq", "type": "string", "value": "lean-eval-public-guard-a-match"},
                            {"key": "$workers.scriptVersion.id", "operation": "eq", "type": "string", "value": "71d1eba9-a3eb-4361-ba60-e979fdc40999"}]}})
        self.assertEqual(request.get_header("Authorization"), "Bearer " + SECRET)
        self.assertEqual(build.return_value.open.call_args.kwargs, {"timeout": 60})
        self.assertEqual(response.reads, [1024 * 1024 + 1])
        self.assertTrue(response.closed)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(dt.datetime.fromtimestamp(telemetry.FROM_MS / 1000, dt.timezone.utc).isoformat(), "2026-11-20T04:32:00+00:00")

    def test_documented_response_projects_only_positive_fields(self):
        result = telemetry.project(raw(fixture()))
        self.assertEqual(result["events"], [{"timestamp": telemetry.FROM_MS, "event": "sandbox.exec",
            "outcome": "error", "component": "sandbox-do", "durationMs": 300001, "code": "CONTAINER_UNAVAILABLE"}])
        self.assertFalse(result["capture_proven"])
        self.assertFalse(result["cause_established"])
        self.assertFalse(result["possibly_truncated"])
        self.assertEqual(result["rows_selected"], 1)
        self.assertNotIn(SECRET, telemetry.encode_report(result).decode())

    def test_string_source_is_bounded_json_not_free_text_error_parsing(self):
        for source, events in ((json.dumps({"event": "sandbox.destroy", "outcome": "success"}), 1),
                               (SECRET + " CONTAINER_UNAVAILABLE", 0), ("x" * 16385, 0)):
            with self.subTest(events=events):
                result = telemetry.project(raw(fixture(source)))
                self.assertEqual(len(result["events"]), events)
                self.assertNotIn(SECRET, json.dumps(result))

    def test_fixed_logger_labels_and_executor_reason_without_context(self):
        for message, label in telemetry.LABELS.items():
            result = telemetry.project(raw(fixture({"message": message, "error": SECRET, "attempt": 2})))
            self.assertEqual(result["events"][0]["event"], label)
            self.assertNotIn(message, json.dumps(result))
        result = telemetry.project(raw(fixture({"event": "lean_eval_replay_executor_failure", "route": "synthetic_acceptance",
            "reason": "input_transfer_failed", "detail": SECRET})))
        self.assertEqual(result["events"][0]["reason"], "input_transfer_failed")
        self.assertNotIn(SECRET, json.dumps(result))
        result = telemetry.project(raw(fixture({"event": "lean_eval_replay_executor_failure", "route": "synthetic_acceptance", "reason": SECRET})))
        self.assertEqual(result["rows_ignored"], 1)

    def test_unknown_codes_names_and_invalid_numbers_are_not_exported(self):
        source = {"event": "file.write", "code": SECRET, "component": SECRET, "outcome": SECRET,
                  "durationMs": -1, "attempt": True, "status": 600, "exitCode": 256}
        self.assertEqual(telemetry.project(raw(fixture(source)))["events"],
                         [{"timestamp": telemetry.FROM_MS, "event": "file.write"}])
        for code in telemetry.CODES:
            self.assertEqual(telemetry.project(raw(fixture({"event": "sandbox.exec", "code": code})))["events"][0]["code"], code)

    def test_all_query_and_row_target_mismatches_fail_closed(self):
        for path, value in ((('run', 'accountId'), SECRET), (('run', 'dry'), False),
                            (('run', 'timeframe', 'from'), telemetry.FROM_MS - 1),
                            (('run', 'query', 'adhoc'), False), (('run', 'query', 'parameters', 'filters'), [])):
            data = fixture()
            target = data['result']
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaisesRegex(telemetry.Rejected, '^target_mismatch$'):
                telemetry.project(raw(data))
        for section, key, value in (("$metadata", "service", SECRET), ("$metadata", "account", SECRET),
                                    ("$workers", "scriptName", SECRET), ("$workers", "scriptVersion", {"id": SECRET}),
                                    (None, "timestamp", telemetry.TO_MS), (None, "timestamp", telemetry.FROM_MS - 1)):
            data = fixture()
            row = data['result']['events']['events'][0]
            (row if section is None else row[section])[key] = value
            with self.subTest(section=section, key=key), self.assertRaisesRegex(telemetry.Rejected, '^target_mismatch$'):
                telemetry.project(raw(data))

    def test_no_schema_fallback_for_missing_version_or_other_response_views(self):
        bad = [[], {"success": True, "result": []}, {"errors": [], "messages": [], "result": {"events": []}}]
        data = fixture()
        data['result']['events'] = data['result']['events']['events']
        bad.append(data)
        data = fixture()
        del data['result']['events']['events'][0]['$workers']['scriptVersion']
        bad.append(data)
        for value in bad:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(telemetry.Rejected):
                telemetry.project(raw(value))
        for value in (b'{"errors":[],"errors":[]}', b'{"x":NaN}', b'{"x":1e999}', b'[' * 1100):
            with self.assertRaises(telemetry.Rejected):
                telemetry.project(value)
        with self.assertRaises(telemetry.Rejected):
            telemetry.project(raw({"nested": [[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]}))

    def test_limits_empty_results_and_truncation_do_not_claim_completeness(self):
        data = fixture()
        data['result']['events'] = {"count": 0, "events": []}
        result = telemetry.project(raw(data))
        self.assertEqual(result['events'], [])
        self.assertFalse(result['cause_established'])
        for count in (None, 257):
            data = fixture()
            if count is None:
                del data['result']['events']['count']
            else:
                data['result']['events']['count'] = count
            self.assertTrue(telemetry.project(raw(data))['possibly_truncated'])
        data = fixture()
        data['result']['events']['events'][0]['$workers']['truncated'] = True
        self.assertTrue(telemetry.project(raw(data))['possibly_truncated'])
        data = fixture()
        data['result']['events']['events'] *= 256
        data['result']['events']['count'] = 256
        self.assertTrue(telemetry.project(raw(data))['possibly_truncated'])
        data['result']['events']['events'].append(copy.deepcopy(data['result']['events']['events'][0]))
        with self.assertRaisesRegex(telemetry.Rejected, '^response_limit$'):
            telemetry.project(raw(data))
        with self.assertRaisesRegex(telemetry.Rejected, '^response_limit$'):
            telemetry.project(b' ' * (telemetry.MAX_RESPONSE + 1))
        with self.assertRaisesRegex(telemetry.Rejected, '^report_limit$'):
            telemetry.encode_report({"events": ['x' * telemetry.MAX_REPORT]})

    def test_denial_redirect_rate_limit_and_transport_errors_never_read_or_dump(self):
        for status, category in ((401, 'denied'), (403, 'denied'), (302, 'http_failure'), (429, 'rate_limited'), (500, 'http_failure')):
            body = mock.Mock()
            error = urllib.error.HTTPError(telemetry.URL, status, SECRET, {'secret': SECRET}, body)
            with mock.patch.object(telemetry.urllib.request, 'build_opener') as build:
                build.return_value.open.side_effect = error
                result = telemetry.query_once(SECRET)
            build.return_value.open.assert_called_once()
            body.read.assert_not_called()
            body.close.assert_called_once()
            self.assertEqual(result['status'], category)
            self.assertNotIn(SECRET, json.dumps(result))
        with mock.patch.object(telemetry.urllib.request, 'build_opener') as build:
            build.return_value.open.side_effect = urllib.error.URLError(SECRET)
            self.assertEqual(telemetry.query_once(SECRET)['status'], 'transport_failure')

    def test_success_http_with_bad_schema_or_unfinished_query_does_not_retry(self):
        for body, category in ((b'not json ' + SECRET.encode(), 'schema_failure'),
                               (b' ' * (telemetry.MAX_RESPONSE + 1), 'response_limit')):
            with mock.patch.object(telemetry.urllib.request, 'build_opener') as build:
                build.return_value.open.return_value = Response(body)
                result = telemetry.query_once(SECRET)
            build.return_value.open.assert_called_once()
            self.assertEqual(result['status'], category)
            self.assertNotIn(SECRET, json.dumps(result))
        data = fixture()
        data['result']['run']['status'] = 'STARTED'
        with self.assertRaisesRegex(telemetry.Rejected, '^query_incomplete$'):
            telemetry.project(raw(data))
        response = Response(SECRET.encode())
        response.status = 403
        with mock.patch.object(telemetry.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = response
            self.assertEqual(telemetry.query_once(SECRET)['status'], 'denied')
        self.assertEqual(response.reads, [])
        data = fixture()
        data['errors'] = [{'message': SECRET}]
        with mock.patch.object(telemetry.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = Response(raw(data))
            result = telemetry.query_once(SECRET)
        self.assertEqual(result['status'], 'query_failure')
        self.assertNotIn(SECRET, json.dumps(result))

    def test_main_clears_environment_and_exports_only_fixed_report(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {'RUNNER_TEMP': temp, 'CLOUDFLARE_API_TOKEN': SECRET, 'OTHER_SECRET': SECRET,
                   'GITHUB_REPOSITORY': 'leanprover/lean-eval-submissions', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                   'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_PROTECTED': 'true'}
            def query(token):
                self.assertEqual(token, SECRET)
                self.assertEqual(dict(os.environ), {})
                return telemetry.project(raw(fixture()))
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(telemetry.os.environ, env, clear=True), mock.patch.object(telemetry.sys, 'argv', ['helper']), mock.patch.object(telemetry, 'query_once', side_effect=query) as request, redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(telemetry.main(), 0)
            request.assert_called_once()
            files = list(pathlib.Path(temp).iterdir())
            self.assertEqual([p.name for p in files], [telemetry.REPORT_NAME])
            self.assertLessEqual(files[0].stat().st_size, 65536)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            self.assertNotIn(SECRET, files[0].read_text() + out.getvalue() + err.getvalue())

    def test_wrong_admission_never_queries_and_existing_report_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {'RUNNER_TEMP': temp, 'CLOUDFLARE_API_TOKEN': SECRET, 'GITHUB_REPOSITORY': 'foreign',
                   'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_PROTECTED': 'true'}
            with mock.patch.dict(telemetry.os.environ, env, clear=True), mock.patch.object(telemetry.sys, 'argv', ['helper']), mock.patch.object(telemetry, 'query_once') as request, redirect_stdout(io.StringIO()):
                self.assertEqual(telemetry.main(), 1)
            request.assert_not_called()
            self.assertEqual(list(pathlib.Path(temp).iterdir()), [])
            env['GITHUB_REPOSITORY'] = 'leanprover/lean-eval-submissions'
            path = pathlib.Path(temp) / telemetry.REPORT_NAME
            path.write_text('earlier report')
            with mock.patch.dict(telemetry.os.environ, env, clear=True), mock.patch.object(telemetry.sys, 'argv', ['helper']), mock.patch.object(telemetry, 'query_once') as request, redirect_stdout(io.StringIO()):
                self.assertEqual(telemetry.main(), 1)
            request.assert_not_called()
            self.assertEqual(path.read_text(), 'earlier report')

    def test_every_admission_field_and_extra_arguments_are_required_before_query(self):
        with tempfile.TemporaryDirectory() as temp:
            valid = {'RUNNER_TEMP': temp, 'CLOUDFLARE_API_TOKEN': SECRET,
                     'GITHUB_REPOSITORY': 'leanprover/lean-eval-submissions', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                     'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_PROTECTED': 'true'}
            for key, value in (('GITHUB_REPOSITORY', 'foreign'), ('GITHUB_EVENT_NAME', 'pull_request'),
                               ('GITHUB_REF', 'refs/heads/feature'), ('GITHUB_REF_PROTECTED', 'false'),
                               ('RUNNER_TEMP', 'relative')):
                with self.subTest(key=key), mock.patch.dict(telemetry.os.environ, {**valid, key: value}, clear=True), mock.patch.object(telemetry.sys, 'argv', ['helper']), mock.patch.object(telemetry, 'query_once') as request, redirect_stdout(io.StringIO()):
                    self.assertEqual(telemetry.main(), 1)
                request.assert_not_called()
            with mock.patch.dict(telemetry.os.environ, valid, clear=True), mock.patch.object(telemetry.sys, 'argv', ['helper', '--other-service']), mock.patch.object(telemetry, 'query_once') as request, redirect_stdout(io.StringIO()):
                self.assertEqual(telemetry.main(), 1)
            request.assert_not_called()

    def test_workflow_has_one_secret_process_no_oidc_or_input_authority(self):
        text = (ROOT / WORKFLOW).read_text()
        self.assertIn('on:\n  workflow_dispatch:\n', text)
        self.assertNotIn('inputs:', text)
        self.assertNotIn('schedule:', text)
        self.assertIn('permissions: {}', text)
        self.assertIn('contents: read', text)
        self.assertIn("github.ref == 'refs/heads/main' && github.ref_protected == true", text)
        self.assertIn("github.repository == 'leanprover/lean-eval-submissions'", text)
        self.assertIn('environment: cloudflare-staging', text)
        self.assertEqual(text.count('secrets.'), 1)
        self.assertEqual(text.count('env:'), 1)
        self.assertIn('shell: python -I -B {0}', text)
        self.assertIn('ref: ${{ github.sha }}', text)
        self.assertIn('persist-credentials: false', text)
        self.assertIn('path: ${{ runner.temp }}/' + telemetry.REPORT_NAME, text)
        for forbidden in ('id-token:', 'contents: write', 'actions: write', 'STATE_', 'wrangler', 'npm ', 'pip ', 'upload-artifact@v', 'checkout@v'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(re.findall(r'uses: ([^\n]+)', text), [
            'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97',
            'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a'])

    def test_all_new_files_excluded_from_actual_deployment_and_mint_filters(self):
        paths = (HELPER, WORKFLOW, 'tests/test_public_marker_historical_telemetry.py', 'docs/public-marker-historical-telemetry.md')
        for workflow in ('.github/workflows/deploy-worker.yml', '.github/workflows/promote-workflow-dispatch-ref.yml'):
            header = (ROOT / workflow).read_text().split('\npermissions:', 1)[0]
            patterns = re.findall(r"      - '([^']+)'", header)
            self.assertTrue(patterns)
            for path in paths:
                matched = False
                for pattern in patterns:
                    if fnmatch.fnmatchcase(path, pattern.lstrip('!')):
                        matched = not pattern.startswith('!')
                self.assertFalse(matched, (workflow, path))


if __name__ == '__main__':
    unittest.main()
