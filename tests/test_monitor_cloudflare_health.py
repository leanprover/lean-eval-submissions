import json
import pathlib
import tempfile
import unittest

from scripts import monitor_cloudflare_health as monitor


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMIT = "a" * 40


class CloudflareHealthMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.intake = monitor.load_object(ROOT / "server" / "wrangler.jsonc")
        cls.replay = monitor.load_object(ROOT / "server" / "wrangler.replay.jsonc")

    def responses(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for environment in ("staging", "production"):
            intake, replay = monitor.expected_health(
                self.intake, self.replay, environment
            )
            result[monitor.ENDPOINTS[environment]["intake"]] = {
                **intake,
                "deployed_commit": COMMIT,
            }
            result[monitor.ENDPOINTS[environment]["replay"]] = {
                **replay,
                "deployed_commit": COMMIT,
            }
        return result

    def test_accepts_one_exact_commit_coherent_snapshot(self) -> None:
        responses = self.responses()
        report = monitor.verify_snapshot(
            self.intake, self.replay, lambda url: responses[url]
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["deployed_commit"], COMMIT)
        self.assertEqual(set(report["observations"]), {"staging", "production"})

    def test_rejects_mixed_commits_or_wrong_enablement(self) -> None:
        responses = self.responses()
        responses[monitor.ENDPOINTS["production"]["replay"]][
            "deployed_commit"
        ] = "b" * 40
        with self.assertRaisesRegex(monitor.MonitorError, "do not share one"):
            monitor.verify_snapshot(self.intake, self.replay, lambda url: responses[url])

        responses = self.responses()
        responses[monitor.ENDPOINTS["production"]["intake"]][
            "intake_enabled"
        ] = True
        with self.assertRaisesRegex(monitor.MonitorError, "health differs"):
            monitor.verify_snapshot(self.intake, self.replay, lambda url: responses[url])

    def test_rejects_non_boolean_tracked_state(self) -> None:
        intake = json.loads(json.dumps(self.intake))
        intake["env"]["production"]["vars"]["INTAKE_ENABLED"] = "1"
        with self.assertRaisesRegex(monitor.MonitorError, "not boolean"):
            monitor.expected_health(intake, self.replay, "production")

    def test_report_creation_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "report.json"
            monitor.write_exclusive(output, {"status": "ready"})
            with self.assertRaises(FileExistsError):
                monitor.write_exclusive(output, {"status": "changed"})
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")), {"status": "ready"}
            )


class CloudflareHealthMonitorWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (
            ROOT / ".github" / "workflows" / "lifecycle-readiness-monitor.yml"
        ).read_text(encoding="utf-8")

    def test_is_scheduled_source_free_and_bounded(self) -> None:
        self.assertIn("cron: '7,22,37,52 * * * *'", self.workflow)
        self.assertIn("timeout-minutes: 10", self.workflow)
        self.assertIn("contents: read", self.workflow)
        self.assertIn("issues: write", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)

    def test_alert_has_exact_bot_owned_identity_and_recovery(self) -> None:
        self.assertIn("<!-- lean-eval-lifecycle-monitor-v1 -->", self.workflow)
        self.assertIn('.author.login == "github-actions[bot]"', self.workflow)
        self.assertIn("if: always()", self.workflow)
        self.assertIn("continue-on-error: true", self.workflow)
        self.assertIn("gh issue reopen", self.workflow)
        self.assertIn("gh issue close", self.workflow)
        self.assertIn("exit 1", self.workflow)

    def test_verifies_immutable_protected_main_commit(self) -> None:
        self.assertIn('dispatch_ref="lean-eval-dispatch/$deployed_commit"', self.workflow)
        self.assertIn("does not have its exact immutable dispatch tag", self.workflow)
        self.assertIn("compare/$deployed_commit...main", self.workflow)
        self.assertIn("not reachable from protected main", self.workflow)
