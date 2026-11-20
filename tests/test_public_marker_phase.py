from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("finite_marker", ROOT / "runtime-guard-staging/marker.py")
marker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(marker)
SHA = "a" * 40
VERSION = "71d1eba9-a3eb-4361-ba60-e979fdc40999"
SECRET = "AGE-SECRET-KEY-secret-sentinel-never-for-output"


class FiniteMarkerPhaseTests(unittest.TestCase):
    def fixture(self, temp):
        root = pathlib.Path(temp)
        (root / "server").mkdir()
        profile_path = root / f"evidence/public-replay/profiles/{marker.campaign.FAMILIES['a'][0]}.json"
        profile_path.parent.mkdir(parents=True)
        profile_path.write_bytes((ROOT / profile_path.relative_to(root)).read_bytes())
        env = {"RUNNER_TEMP": temp, "GITHUB_SHA": SHA, "GITHUB_REPOSITORY": "leanprover/lean-eval-submissions",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/tags/lean-eval-dispatch/" + SHA,
               "GITHUB_REF_PROTECTED": "true", "GITHUB_RUN_ID": "44690000000", "GITHUB_RUN_ATTEMPT": "1",
               "GITHUB_WORKFLOW_REF": "leanprover/lean-eval-submissions/" + marker.WORKFLOW + "@refs/tags/lean-eval-dispatch/" + SHA,
               "GITHUB_WORKFLOW_SHA": SHA}
        return root, env

    def prepared(self, root):
        directory = root / "finite-marker-phase"
        marker.prepare(directory)
        receipt = marker.campaign.load(directory / "receipt.json")
        receipt.update(created=True, worker_version=VERSION, namespaces={"REPLAY_SANDBOX": "1" * 32, "REPLAY_TERMINAL_RECEIPT": "2" * 32},
                       health={"status": "ok"}, application_id="00000000-0000-4000-8000-000000000000", application_version=1,
                       application_created_at="2026-11-20T07:00:00Z")
        marker.write(directory / "receipt.json", receipt)
        return directory

    def observation(self, directory):
        return {"schema_version": 1, "request_id": "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
                "runner_nonce": marker.campaign.load(directory / "nonce.json")["runner_nonce"],
                "archive_ciphertext_sha256": "b" * 64, "marker_sha256": "c" * 64,
                "ciphertext_base64": "Zml4dHVyZQ==", "plaintext_identity_base64": SECRET}

    def journal(self, scope, expected, state="prepared", events=None):
        if state == "terminal" and events is None:
            events = []
            for operation in marker.OPERATIONS:
                events.extend([{"operation": operation, "state": "started"}, {"operation": operation, "state": "succeeded", "elapsed_ms": 0}])
        return {"schema_version": 1, "binding": expected, "start_before": scope["start_before"], "retain_until": scope["retain_until"],
                "state": state, "claimed": state != "prepared", "events": events or [],
                "operation_outcome": "succeeded" if state == "terminal" else "pending" if state == "active" else "not_attempted",
                "destruction_outcome": "succeeded" if state == "terminal" else "not_attempted"}

    @staticmethod
    def envelope(journal):
        return {"schema_version": 1, "journal": journal, "provider_boundary": "unknown", "configuration_awaited": False}

    def test_source_free_preparation_exact_config_and_tag_workflow_admission(self):
        with tempfile.TemporaryDirectory() as temp:
            root, env = self.fixture(temp)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(marker, "ROOT", root), \
                 mock.patch.object(marker.campaign, "prepare", side_effect=AssertionError("Source prepare forbidden")), \
                 mock.patch.object(marker.campaign, "run_case", side_effect=AssertionError("Source run forbidden")):
                directory = self.prepared(root)
                config = marker.campaign.load(directory / "config.json")
                self.assertEqual(config["observability"], {"enabled": False})
                self.assertEqual(config["version_metadata"], {"binding": "CF_VERSION_METADATA"})
                self.assertEqual(config["vars"]["REPLAY_ENABLED"], "false")
                self.assertEqual(config["vars"]["HISTORICAL_PUBLIC_REPLAY_ENABLED"], "false")
                self.assertEqual(config["vars"]["SANDBOX_TRANSPORT"], "rpc")
                self.assertEqual(config["containers"][0]["max_instances"], 1)
                rendered = marker.campaign.load(root / "server/.public-guard-a-match.json")
                self.assertEqual(rendered["env"]["staging"]["observability"], {"enabled": False})
                self.assertEqual(set(p.name for p in directory.iterdir()), {"nonce.json", "config.json", "profile.json", "receipt.json"})
                raw_nonce = marker.campaign.load(directory / "nonce.json")["runner_nonce"]
                self.assertNotIn(raw_nonce, marker.report_path().read_text())
                self.assertEqual((directory / "nonce.json").stat().st_mode & 0o777, 0o600)
                for key, value in (("GITHUB_REF", "refs/heads/main"), ("GITHUB_WORKFLOW_SHA", "b" * 40),
                                   ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_WORKFLOW_REF", "other"),
                                   ("GITHUB_REF_PROTECTED", "false"), ("GITHUB_REPOSITORY", "other")):
                    with mock.patch.dict(os.environ, {key: value}), self.assertRaises(ValueError):
                        marker.admission()

    def test_shared_canonical_nonce_vector_and_actual_truncated_name_commitment(self):
        self.assertEqual(marker.digest("lean-eval-marker-nonce-v1", "1" * 64),
                         "95952b662fc203ba46e3d5b6d12860af1be51d426d5da05d2134524de78d2042")
        self.assertEqual(marker.digest("lean-eval-marker-sandbox-v1", "r-" + ("1" * 64)[:61]),
                         marker.digest("lean-eval-marker-sandbox-v1", "r-" + ("1" * 61 + "222")[:61]))
        scope = {"schema_version": 1, "case": "a-match", "service": marker.SERVICE, "workflow": marker.WORKFLOW,
                 "commit": "a" * 40, "run_id": "44690000000", "run_attempt": "1", "start_before": 1787396100000,
                 "retain_until": 1787397000000, "nonce_sha256": marker.digest("lean-eval-marker-nonce-v1", "1" * 64),
                 "sandbox_name_sha256": marker.digest("lean-eval-marker-sandbox-v1", "r-" + "1" * 61),
                 "execution_profile_digest": "b" * 64, "measurement_config_digest": "c" * 64, "image_digest": "sha256:" + "d" * 64}
        observation = {"request_id": "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f", "marker_sha256": "e" * 64,
                       "archive_ciphertext_sha256": hashlib.sha256(b"age-encryption.org/v1\nfixture").hexdigest()}
        self.assertEqual(marker.binding(scope, VERSION, observation), {"schema_version": 1,
            "scope_sha256": "1180d606eb8888de0f2a10fdec4b1eea827c19428fc3cbd05f9c153ba2c56942", "worker_version": VERSION,
            "marker_instance_id": "122ec2b7558bd169dab2bffe6771c090c5d612240aff4146b34bd15df686bb55",
            "request_commitment": "e569308e04b5ac5a33fafa1d1ce98c62f2016a5bebef22ac3095bd03efcdf64c"})

    def test_proof_readback_precedes_one_post_300_seconds_and_success_never_continues_to_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root, env = self.fixture(temp)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(marker, "ROOT", root):
                directory = self.prepared(root)
                observation = self.observation(directory)
                scope = marker.campaign.load(marker.report_path())["scope"]
                expected = marker.binding(scope, VERSION, observation)
                prepared = self.journal(scope, expected)
                terminal = self.journal(scope, expected, "terminal")
                profile = marker.campaign.load(directory / "profile.json")
                paths = []
                def post(path, body, token, timeout=5, checkpoint=None):
                    paths.append((path, timeout))
                    if path == "":
                        self.assertTrue(marker.campaign.load(marker.report_path())["phase_capture_proven"])
                        checkpoint({"stage": "endpoint_request", "state": "pending", "timeout_seconds": timeout})
                        checkpoint({"stage": "endpoint_request", "state": "responded", "timeout_seconds": timeout, "elapsed_ms": 123})
                        return 200, {"schema_version": 1, "marker_instance_id": expected["marker_instance_id"],
                                     **{key: profile[key] for key in ("architecture", "kernel_release", "cpu_model")},
                                     "network_policy": "disabled", "network_probe": "blocked", "destruction": "confirmed"}
                    return 200, self.envelope(terminal if len(paths) == 4 else prepared)
                with mock.patch.object(marker, "marker_body", return_value=observation), mock.patch.object(marker, "token", return_value=SECRET), \
                     mock.patch.object(marker, "post", side_effect=post), mock.patch.object(marker.campaign, "endpoint_health"), \
                     mock.patch.object(marker.campaign, "run_case", side_effect=AssertionError("Source forbidden")):
                    marker.run(directory)
                self.assertEqual(paths, [("/phase-proof", 5), ("/phase-status", 5), ("", 300), ("/phase-status", 5)])
                report = marker.campaign.load(marker.report_path())
                self.assertEqual(report["marker_verdict"], "succeeded")
                self.assertTrue(report["phase_capture_proven"])
                self.assertNotIn(SECRET, marker.report_path().read_text())
                self.assertNotIn(marker.campaign.load(directory / "nonce.json")["runner_nonce"], marker.report_path().read_text())

    def test_failed_proof_never_sends_marker_and_pending_timeout_is_not_capacity_or_retry(self):
        for fail_proof in (False, True):
            with self.subTest(fail_proof=fail_proof), tempfile.TemporaryDirectory() as temp:
                root, env = self.fixture(temp)
                with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(marker, "ROOT", root):
                    directory = self.prepared(root)
                    observation = self.observation(directory)
                    scope = marker.campaign.load(marker.report_path())["scope"]
                    expected = marker.binding(scope, VERSION, observation)
                    paths = []
                    def post(path, body, token, timeout=5, checkpoint=None):
                        paths.append(path)
                        if path == "":
                            checkpoint({"stage": "endpoint_request", "state": "pending", "timeout_seconds": timeout})
                            raise marker.campaign.TransportError(SECRET)
                        if fail_proof:
                            return 403, {"error": SECRET}
                        state = "active" if "" in paths else "prepared"
                        return 200, self.envelope(self.journal(scope, expected, state,
                            [{"operation": "sandbox_handle", "state": "started"},
                             {"operation": "sandbox_handle", "state": "succeeded", "elapsed_ms": 0},
                             {"operation": "write_ciphertext", "state": "started"}] if state == "active" else []))
                    with mock.patch.object(marker, "marker_body", return_value=observation), mock.patch.object(marker, "token", return_value=SECRET), \
                         mock.patch.object(marker, "post", side_effect=post), mock.patch.object(marker.campaign, "endpoint_health"), \
                         mock.patch.object(marker.time, "sleep"), self.assertRaises(ValueError):
                        marker.run(directory)
                    self.assertEqual(paths.count(""), 0 if fail_proof else 1)
                    self.assertLessEqual(paths.count("/phase-status"), 4)  # one proof read + at most three observations
                    if not fail_proof:
                        self.assertEqual(paths.count("/phase-status"), 4)
                    report = marker.campaign.load(marker.report_path())
                    self.assertEqual(report["provider_boundary"], "unknown")
                    self.assertEqual(report["marker_verdict"], "not_attempted" if fail_proof else "incomplete")
                    self.assertNotIn(SECRET, marker.report_path().read_text())

    def test_safe_journal_refuses_extra_fields_target_mismatch_and_secret_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            root, env = self.fixture(temp)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(marker, "ROOT", root):
                directory = self.prepared(root)
                scope = marker.campaign.load(marker.report_path())["scope"]
                expected = marker.binding(scope, VERSION, self.observation(directory))
                original = self.envelope(self.journal(scope, expected))
                bad = []
                extra = copy.deepcopy(original)
                extra["journal"]["raw_message"] = SECRET
                bad.append(extra)
                target = copy.deepcopy(original)
                target["journal"]["binding"]["worker_version"] = SECRET
                bad.append(target)
                event = copy.deepcopy(original)
                event["journal"]["events"] = [{"operation": "exec_acceptance", "state": "failed", "elapsed_ms": 1, "code": SECRET}]
                bad.append(event)
                incomplete = copy.deepcopy(original)
                incomplete["journal"].update(state="terminal", claimed=True, operation_outcome="succeeded", destruction_outcome="succeeded")
                bad.append(incomplete)
                for value in bad:
                    with self.assertRaises(ValueError):
                        marker.safe_journal(value, expected)

    def test_identity_creation_failure_and_retirement_failure_still_allow_safe_scratch_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            root, env = self.fixture(temp)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(marker, "ROOT", root):
                directory = self.prepared(root)
                def command(*args, **kwargs):
                    (directory / "marker-identity.age").write_text(SECRET)
                    raise ValueError(SECRET)
                with mock.patch.object(marker.campaign, "command", side_effect=command), self.assertRaises(ValueError):
                    marker.marker_body(directory)
                self.assertFalse((directory / "marker-identity.age").exists())
                def cleanup(case, path):
                    receipt = marker.campaign.load(path / "receipt.json")
                    receipt.update(retirement_phase="disabled", disabled_worker_version=VERSION)
                    marker.write(path / "receipt.json", receipt)
                    raise ValueError(SECRET)
                with mock.patch.object(marker.campaign, "cleanup", side_effect=cleanup), self.assertRaises(ValueError):
                    marker.owned_operation("cleanup", directory)
                report = marker.campaign.load(marker.report_path())
                self.assertEqual(report["control_plane_cleanup"], "unconfirmed")
                self.assertEqual(report["owned_target"]["disabled_worker_version"], VERSION)
                (root / "finite-marker-tools").mkdir()
                (root / "finite-marker-tools/age").write_text("tool fixture")
                (root / "finite-marker-age.tar.gz").write_bytes(b"tool fixture")
                marker.scrub(directory)
                self.assertFalse(directory.exists())
                self.assertFalse((root / "finite-marker-tools").exists())
                self.assertFalse((root / "finite-marker-age.tar.gz").exists())
                self.assertEqual(marker.campaign.load(marker.report_path())["scratch_cleanup"], "confirmed")
                self.assertNotIn(SECRET, marker.report_path().read_text())

    def test_main_errors_are_closed_and_execution_rejects_deployment_or_key_authority(self):
        for key in ("CLOUDFLARE_API_TOKEN", "STATE_WRITE_KEY", "LEGACY_ARCHIVE_IDENTITY", "AWS_ACCESS_KEY_ID"):
            with mock.patch.dict(os.environ, {key: SECRET}), self.assertRaises(ValueError):
                marker.execution_environment()
        output = io.StringIO()
        with mock.patch.object(marker, "admission", side_effect=ValueError(SECRET)), \
             mock.patch("sys.argv", ["marker.py", "run"]), redirect_stdout(output):
            self.assertEqual(marker.main(), 1)
        self.assertNotIn(SECRET, output.getvalue())

    def test_version_binding_is_explicit_and_does_not_allow_extra_binding_for_old_configs(self):
        binding = {"type": "version_metadata", "name": "CF_VERSION_METADATA"}
        parsed, _ = marker.campaign._binding_inventory([binding], "fixture")
        old = {"vars": {}, "durable_objects": {"bindings": []}}
        self.assertNotEqual(parsed, marker.campaign._expected_inventory(old))
        self.assertEqual(parsed, marker.campaign._expected_inventory({**old, "version_metadata": {"binding": "CF_VERSION_METADATA"}}))
        for value in ({**binding, "name": "other"}, {**binding, "secret": SECRET}):
            with self.assertRaises(ValueError):
                marker.campaign._binding_inventory([value], "fixture")

    def test_workflow_is_fixed_no_source_and_fences_retires_scrubs_after_failure(self):
        workflow = (ROOT / marker.WORKFLOW).read_text()
        self.assertNotIn("inputs:", workflow)
        self.assertNotIn("matrix:", workflow)
        self.assertNotIn("STATE_", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("check.py run", workflow)
        self.assertNotIn("check.py prepare", workflow)
        self.assertEqual(workflow.count("secrets.CLOUDFLARE_API_TOKEN"), 2)
        for name in ("Fence the journal", "Retire only exact", "Remove private scratch", "Retain only"):
            section = workflow.split("      - name: " + name, 1)[1].split("      - name:", 1)[0]
            self.assertIn("if: always()", section)
        self.assertLess(workflow.index("marker.py fence"), workflow.index("marker.py cleanup"))
        self.assertLess(workflow.index("marker.py cleanup"), workflow.index("marker.py scrub"))
        self.assertIn("group: finite-public-runtime-guard-staging", workflow)
        self.assertIn("refs/tags/lean-eval-dispatch/{0}", workflow)
        self.assertIn("github.run_attempt == 1", workflow)


if __name__ == "__main__":
    unittest.main()
