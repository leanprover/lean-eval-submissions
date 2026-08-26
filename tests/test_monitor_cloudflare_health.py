import copy
import datetime as dt
import json
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from scripts import monitor_cloudflare_health as monitor
from scripts import monitor_github_readiness as github_monitor

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
NOW = dt.datetime(2026, 8, 24, 6, 0, tzinfo=dt.timezone.utc)


class CloudflareHealthMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.intake = monitor.load_object(ROOT / "server" / "wrangler.jsonc")
        cls.replay = monitor.load_object(ROOT / "server" / "wrangler.replay.jsonc")

    def responses(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        endpoints = monitor.tracked_endpoints(self.intake, self.replay)
        for environment in ("staging", "production"):
            intake, replay = monitor.expected_health(
                self.intake, self.replay, environment
            )
            result[endpoints[environment]["intake"]] = {
                **intake,
                "deployed_commit": COMMIT,
            }
            result[endpoints[environment]["replay"]] = {
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
        endpoints = monitor.tracked_endpoints(self.intake, self.replay)
        responses[endpoints["production"]["replay"]][
            "deployed_commit"
        ] = "b" * 40
        with self.assertRaisesRegex(monitor.MonitorError, "do not share one"):
            monitor.verify_snapshot(self.intake, self.replay, lambda url: responses[url])

        responses = self.responses()
        responses[endpoints["production"]["intake"]][
            "intake_effective_enabled"
        ] = True
        with self.assertRaisesRegex(monitor.MonitorError, "health differs"):
            monitor.verify_snapshot(self.intake, self.replay, lambda url: responses[url])

    def test_rejects_non_boolean_tracked_state(self) -> None:
        intake = json.loads(json.dumps(self.intake))
        intake["env"]["production"]["vars"]["INTAKE_ENABLED"] = "1"
        with self.assertRaisesRegex(monitor.MonitorError, "canonical boolean"):
            monitor.expected_health(intake, self.replay, "production")

    def test_rejects_lifecycle_health_drift_and_consolidation_enablement(self) -> None:
        responses = self.responses()
        endpoints = monitor.tracked_endpoints(self.intake, self.replay)
        responses[endpoints["production"]["intake"]][
            "release_opt_out_api_enabled"
        ] = True
        with self.assertRaisesRegex(monitor.MonitorError, "health differs"):
            monitor.verify_snapshot(self.intake, self.replay, lambda url: responses[url])

        intake = copy.deepcopy(self.intake)
        intake["env"]["production"]["vars"][
            "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED"
        ] = "true"
        with self.assertRaisesRegex(monitor.MonitorError, "must remain disabled"):
            monitor.expected_health(intake, self.replay, "production")

    def test_rejects_historical_replay_health_drift(self) -> None:
        responses = self.responses()
        endpoints = monitor.tracked_endpoints(self.intake, self.replay)
        responses[endpoints["production"]["replay"]][
            "historical_public_replay_enabled"
        ] = True
        with self.assertRaisesRegex(monitor.MonitorError, "health differs"):
            monitor.verify_snapshot(self.intake, self.replay, lambda url: responses[url])

    def test_rejects_incoherent_mode_or_tracked_lease_material(self) -> None:
        intake = copy.deepcopy(self.intake)
        intake["env"]["production"]["vars"]["INTAKE_ENABLEMENT_MODE"] = "durable"
        with self.assertRaisesRegex(monitor.MonitorError, "must be disabled"):
            monitor.expected_health(intake, self.replay, "production")

        intake = copy.deepcopy(self.intake)
        intake["env"]["production"]["vars"]["INTAKE_LEASE_EVENT_ID"] = (
            "0198abcd-1111-7000-8000-000000000001"
        )
        with self.assertRaisesRegex(monitor.MonitorError, "lease material"):
            monitor.expected_health(intake, self.replay, "production")

    def test_derives_unique_endpoints_from_tracked_worker_names(self) -> None:
        endpoints = monitor.tracked_endpoints(self.intake, self.replay)
        self.assertEqual(
            endpoints["staging"]["intake"],
            "https://lean-eval-submission-server-staging.lean-eval.workers.dev/healthz",
        )
        renamed = copy.deepcopy(self.intake)
        renamed["env"]["staging"]["name"] = "renamed-intake-staging"
        self.assertEqual(
            monitor.tracked_endpoints(renamed, self.replay)["staging"]["intake"],
            "https://renamed-intake-staging.lean-eval.workers.dev/healthz",
        )
        renamed["env"]["production"]["name"] = "renamed-intake-staging"
        with self.assertRaisesRegex(monitor.MonitorError, "not unique"):
            monitor.tracked_endpoints(renamed, self.replay)

    def test_rejects_invalid_endpoint_and_environment_configuration(self) -> None:
        for field, value in (
            ("name", "UPPERCASE"),
            ("workers_dev", False),
        ):
            with self.subTest(field=field):
                intake = copy.deepcopy(self.intake)
                intake["env"]["staging"][field] = value
                with self.assertRaisesRegex(monitor.MonitorError, "endpoint is invalid"):
                    monitor.tracked_endpoints(intake, self.replay)

        intake = copy.deepcopy(self.intake)
        intake["env"]["staging"]["vars"]["DEPLOYMENT_ENVIRONMENT"] = "production"
        with self.assertRaisesRegex(monitor.MonitorError, "endpoint is invalid"):
            monitor.expected_health(intake, self.replay, "staging")

    def test_rejects_invalid_digest_and_memory_contracts(self) -> None:
        for field, value, message in (
            ("REVIEWED_EXECUTION_PROFILE_DIGEST", "not-a-digest", "canonical digest"),
            ("REVIEWED_MEASUREMENT_CONFIG_DIGEST", "f" * 63, "canonical digest"),
            ("REVIEWED_VM_IMAGE_DIGEST", "f" * 64, "canonical digest"),
            ("STAGING_MEMORY_LIMIT_BYTES", "0", "positive decimal"),
            (
                "PRODUCTION_MEMORY_GATE_BYTES",
                str(monitor.MAX_SAFE_INTEGER + 1),
                "safe integer",
            ),
        ):
            with self.subTest(field=field):
                replay = copy.deepcopy(self.replay)
                replay["env"]["staging"]["vars"][field] = value
                with self.assertRaisesRegex(monitor.MonitorError, message):
                    monitor.expected_health(self.intake, replay, "staging")

    def test_verifies_service_identity_from_tracked_config(self) -> None:
        responses = self.responses()
        endpoints = monitor.tracked_endpoints(self.intake, self.replay)
        responses[endpoints["production"]["intake"]]["service"] = "wrong-service"
        with self.assertRaisesRegex(monitor.MonitorError, "health differs"):
            monitor.verify_snapshot(self.intake, self.replay, lambda url: responses[url])

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
        self.assertIn("permissions: {}", self.workflow)
        self.assertIn("actions: read", self.workflow)
        self.assertIn("contents: read", self.workflow)
        self.assertIn("issues: write", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn("github.ref == 'refs/heads/main'", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)
        endpoint_step = self.workflow.split(
            "- name: Verify config-bound public Worker health without a GitHub token",
            1,
        )[1].split("- name: Bind health to protected deployment evidence", 1)[0]
        self.assertNotIn("GH_TOKEN", endpoint_step)

    def test_alert_has_exact_bot_owned_identity_and_recovery(self) -> None:
        self.assertEqual(
            github_monitor.ISSUE_MARKER,
            "<!-- lean-eval-lifecycle-monitor-v1 -->",
        )
        self.assertIn("reconcile-issue", self.workflow)
        self.assertIn("if: always()", self.workflow)
        self.assertIn("continue-on-error: true", self.workflow)
        self.assertIn("exit 1", self.workflow)
        self.assertIn("needs.verify.outputs.status != 'suppressed'", self.workflow)

    def test_verifies_immutable_protected_main_commit(self) -> None:
        self.assertIn("verify-deployment", self.workflow)
        self.assertIn("--max-active-age-seconds 2700", self.workflow)
        self.assertIn("deployment_rollout_stuck", github_monitor.__dict__.get("__doc__", "") + pathlib.Path(github_monitor.__file__).read_text(encoding="utf-8"))


class FakeDeploymentClient:
    def __init__(self, runs: list[dict[str, object]], commit: str = COMMIT) -> None:
        self.runs = runs
        self.commit = commit

    def get(self, path: str) -> object:
        if "/actions/workflows/" in path:
            page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["page"][0])
            return {"workflow_runs": self.runs if page == 1 else []}
        if "/git/ref/tags/" in path:
            return {"object": {"type": "commit", "sha": self.commit}}
        if path.endswith("/branches/main"):
            return {
                "name": "main",
                "protected": True,
                "commit": {"sha": COMMIT},
            }
        if "/compare/" in path:
            return {"status": "ahead"}
        raise AssertionError(path)


def deployment_run(
    commit: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    started_at: str = "2026-08-24T05:55:00Z",
    run_id: int = 123,
) -> dict[str, object]:
    return {
        "id": run_id,
        "html_url": f"https://github.com/leanprover/lean-eval-submissions/actions/runs/{run_id}",
        "head_branch": "main",
        "head_sha": commit,
        "event": "push",
        "status": status,
        "conclusion": conclusion,
        "created_at": started_at,
        "run_started_at": started_at,
        "updated_at": started_at,
    }


class GitHubDeploymentMonitorTests(unittest.TestCase):
    def classify(
        self, runs: list[dict[str, object]], deployed_commit: str = COMMIT
    ) -> dict[str, object]:
        return github_monitor.classify_deployment(
            FakeDeploymentClient(runs),
            "leanprover/lean-eval-submissions",
            deployed_commit,
            now=NOW,
            max_active_age_seconds=2700,
        )

    def test_binds_to_latest_successful_tagged_main_deployment(self) -> None:
        report = self.classify([deployment_run(COMMIT)])
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["expected_commit"], COMMIT)

        report = self.classify([deployment_run(COMMIT)], "b" * 40)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "deployment_commit_not_latest_success")

    def test_fresh_active_deployment_suppresses_only_a_current_mismatch(self) -> None:
        runs = [
            deployment_run("b" * 40, status="in_progress", conclusion=None),
            deployment_run(COMMIT, started_at="2026-08-24T04:00:00Z", run_id=122),
        ]
        report = self.classify(runs, "c" * 40)
        self.assertEqual(report["status"], "suppressed")
        self.assertLessEqual(
            report["active_deployment"]["age_seconds"],
            report["active_deployment"]["maximum_age_seconds"],
        )

        coherent = self.classify(runs, COMMIT)
        self.assertEqual(coherent["status"], "suppressed")
        self.assertEqual(coherent["reason"], "deployment_rollout_active")

        runs[0]["created_at"] = "2026-08-24T04:00:00Z"
        runs[0]["run_started_at"] = "2026-08-24T05:59:00Z"
        queued_too_long = self.classify(runs, COMMIT)
        self.assertEqual(queued_too_long["status"], "failed")
        self.assertEqual(queued_too_long["reason"], "deployment_rollout_stuck")

    def test_selects_latest_success_by_completion_not_api_order(self) -> None:
        runs = [
            deployment_run(COMMIT, started_at="2026-08-24T04:00:00Z", run_id=122),
            deployment_run("b" * 40, started_at="2026-08-24T05:00:00Z", run_id=123),
        ]
        report = self.classify(runs)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["expected_commit"], "b" * 40)

        runs[0]["updated_at"] = runs[1]["updated_at"]
        with self.assertRaisesRegex(github_monitor.GitHubError, "ambiguous"):
            self.classify(runs)

    def test_requires_explicit_main_branch_protection(self) -> None:
        client = FakeDeploymentClient([deployment_run(COMMIT)])
        original = client.get

        def unprotected(path: str) -> object:
            if path.endswith("/branches/main"):
                return {
                    "name": "main",
                    "protected": False,
                    "commit": {"sha": COMMIT},
                }
            return original(path)

        client.get = unprotected  # type: ignore[method-assign]
        report = github_monitor.classify_deployment(
            client,
            "leanprover/lean-eval-submissions",
            COMMIT,
            now=NOW,
            max_active_age_seconds=2700,
        )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "deployment_main_not_protected")

    def test_stuck_deployment_fails_even_when_old_health_is_coherent(self) -> None:
        runs = [
            deployment_run(
                "b" * 40,
                status="in_progress",
                conclusion=None,
                started_at="2026-08-24T04:00:00Z",
            ),
            deployment_run(COMMIT, started_at="2026-08-24T03:00:00Z", run_id=122),
        ]
        report = self.classify(runs)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "deployment_rollout_stuck")

    def test_github_client_retries_only_bounded_safe_requests(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'
        response.__exit__.return_value = False
        sleeps: list[float] = []
        client = github_monitor.GitHubClient(
            "token", attempts=4, sleeper=sleeps.append
        )
        with mock.patch(
            "urllib.request.OpenerDirector.open",
            side_effect=[urllib.error.URLError("down"), response],
        ) as request:
            self.assertEqual(client.get("/test"), {"ok": True})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleeps, [1])

        with mock.patch(
            "urllib.request.OpenerDirector.open",
            side_effect=urllib.error.URLError("response lost"),
        ) as request, self.assertRaises(github_monitor.GitHubError):
            client.post("/test", {"value": 1})
        self.assertEqual(request.call_count, 1)

        redirect = urllib.error.HTTPError(
            "https://api.github.com/test", 302, "redirect", {}, None
        )
        with mock.patch(
            "urllib.request.OpenerDirector.open", side_effect=redirect
        ) as request, self.assertRaises(github_monitor.GitHubError):
            client.get("/test")
        self.assertEqual(request.call_count, 1)

        limited_response = mock.MagicMock()
        limited_response.__enter__.return_value.read.return_value = b"{}"
        limited_response.__exit__.return_value = False
        rate_limit = urllib.error.HTTPError(
            "https://api.github.com/test",
            403,
            "rate limited",
            {"Retry-After": "2"},
            None,
        )
        rate_sleeps: list[float] = []
        limited = github_monitor.GitHubClient("token", sleeper=rate_sleeps.append)
        with mock.patch(
            "urllib.request.OpenerDirector.open",
            side_effect=[rate_limit, limited_response],
        ):
            self.assertEqual(limited.get("/test"), {})
        self.assertEqual(rate_sleeps, [2])

        expired = github_monitor.GitHubClient("token", clock=lambda: 0)
        expired.deadline = -1
        with mock.patch("urllib.request.OpenerDirector.open") as request, self.assertRaisesRegex(
            github_monitor.GitHubError, "time budget"
        ):
            expired.get("/test")
        request.assert_not_called()


class FakeIssueClient:
    def __init__(self, issues: list[dict[str, object]]) -> None:
        self.issues = issues
        self.comments: dict[int, list[dict[str, object]]] = {}
        self.events: list[str] = []

    def get(self, path: str) -> object:
        split = urllib.parse.urlsplit(path)
        page = int(urllib.parse.parse_qs(split.query).get("page", ["1"])[0])
        if split.path.endswith("/issues"):
            start = (page - 1) * 100
            return self.issues[start : start + 100]
        if split.path.endswith("/comments"):
            number = int(split.path.split("/")[-2])
            start = (page - 1) * 100
            return self.comments.get(number, [])[start : start + 100]
        raise AssertionError(path)

    def post(self, path: str, payload: dict[str, object]) -> object:
        if path.endswith("/issues"):
            number = max((int(issue["number"]) for issue in self.issues), default=0) + 1
            issue = {
                "number": number,
                "title": payload["title"],
                "body": payload["body"],
                "state": "open",
                "user": {"login": github_monitor.BOT_LOGIN},
            }
            self.issues.append(issue)
            return issue
        if path.endswith("/comments"):
            number = int(path.split("/")[-2])
            comment = {
                "body": payload["body"],
                "user": {"login": github_monitor.BOT_LOGIN},
            }
            self.comments.setdefault(number, []).append(comment)
            self.events.append(f"comment:{number}")
            return comment
        raise AssertionError(path)

    def patch(self, path: str, payload: dict[str, object]) -> object:
        number = int(path.split("/")[-1])
        issue = next(issue for issue in self.issues if issue["number"] == number)
        issue["state"] = payload["state"]
        self.events.append(f"state:{number}:{payload['state']}")
        return issue


class CreateResponseLossClient(FakeIssueClient):
    def __init__(self) -> None:
        super().__init__([])
        self.lost = False

    def post(self, path: str, payload: dict[str, object]) -> object:
        result = super().post(path, payload)
        if path.endswith("/issues") and not self.lost:
            self.lost = True
            raise github_monitor.GitHubError("response lost", retryable=True)
        return result


class DelayedCreateResponseLossClient(CreateResponseLossClient):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_reads = 0
        self.issue_posts = 0

    def get(self, path: str) -> object:
        if urllib.parse.urlsplit(path).path.endswith("/issues") and self.hidden_reads:
            self.hidden_reads -= 1
            return []
        return super().get(path)

    def post(self, path: str, payload: dict[str, object]) -> object:
        if path.endswith("/issues"):
            self.issue_posts += 1
        try:
            return super().post(path, payload)
        except github_monitor.GitHubError:
            self.hidden_reads = 1
            raise


class DelayedCommentResponseLossClient(FakeIssueClient):
    def __init__(self) -> None:
        super().__init__([monitor_issue(1, "closed")])
        self.hidden_comment_reads = 0
        self.comment_posts = 0
        self.lost = False

    def get(self, path: str) -> object:
        if (
            urllib.parse.urlsplit(path).path.endswith("/comments")
            and self.hidden_comment_reads
        ):
            self.hidden_comment_reads -= 1
            return []
        return super().get(path)

    def post(self, path: str, payload: dict[str, object]) -> object:
        result = super().post(path, payload)
        if path.endswith("/comments"):
            self.comment_posts += 1
            if not self.lost:
                self.lost = True
                self.hidden_comment_reads = 1
                raise github_monitor.GitHubError("response lost", retryable=True)
        return result


class LostPostWithoutVisibilityClient(FakeIssueClient):
    def __init__(self) -> None:
        super().__init__([])
        self.issue_posts = 0

    def post(self, path: str, payload: dict[str, object]) -> object:
        if path.endswith("/issues"):
            self.issue_posts += 1
            raise github_monitor.GitHubError("response lost", retryable=True)
        return super().post(path, payload)


def monitor_issue(number: int, state: str = "open") -> dict[str, object]:
    return {
        "number": number,
        "title": github_monitor.ISSUE_TITLE,
        "body": github_monitor.ISSUE_MARKER,
        "state": state,
        "user": {"login": github_monitor.BOT_LOGIN},
    }


class GitHubIssueMonitorTests(unittest.TestCase):
    def test_paginates_and_closes_duplicate_marker_issues(self) -> None:
        filler = [
            {
                "number": number,
                "title": "submission",
                "body": "",
                "state": "closed",
                "user": {"login": "someone"},
            }
            for number in range(1, 101)
        ]
        client = FakeIssueClient(filler + [monitor_issue(101), monitor_issue(102)])
        result = github_monitor.reconcile_issue(
            client,
            "leanprover/lean-eval-submissions",
            "ready",
            "https://github.com/leanprover/lean-eval-submissions/actions/runs/999",
            "@kim-em",
            "999",
        )
        self.assertEqual(result["canonical_issue"], 101)
        self.assertEqual(result["duplicates_closed"], 1)
        self.assertEqual(client.issues[100]["state"], "closed")
        self.assertEqual(client.issues[101]["state"], "closed")

    def test_creates_once_and_reuses_marker_issue(self) -> None:
        client = FakeIssueClient([])
        first = github_monitor.reconcile_issue(
            client,
            "leanprover/lean-eval-submissions",
            "failed",
            "https://github.com/leanprover/lean-eval-submissions/actions/runs/998",
            "@kim-em",
            "998",
        )
        second = github_monitor.reconcile_issue(
            client,
            "leanprover/lean-eval-submissions",
            "failed",
            "https://github.com/leanprover/lean-eval-submissions/actions/runs/999",
            "@kim-em",
            "999",
        )
        self.assertEqual(first["canonical_issue"], second["canonical_issue"])
        self.assertEqual(len(client.issues), 1)

    def test_issue_creation_response_loss_does_not_duplicate(self) -> None:
        client = CreateResponseLossClient()
        result = github_monitor.reconcile_issue(
            client,
            "leanprover/lean-eval-submissions",
            "failed",
            "https://github.com/leanprover/lean-eval-submissions/actions/runs/997",
            "@kim-em",
            "997",
        )
        self.assertEqual(result["canonical_issue"], 1)
        self.assertEqual(len(client.issues), 1)

        delayed = DelayedCreateResponseLossClient()
        with mock.patch("scripts.monitor_github_readiness.time.sleep"):
            delayed_result = github_monitor.reconcile_issue(
                delayed,
                "leanprover/lean-eval-submissions",
                "failed",
                "https://github.com/leanprover/lean-eval-submissions/actions/runs/996",
                "@kim-em",
                "996",
            )
        self.assertEqual(delayed_result["canonical_issue"], 1)
        self.assertEqual(delayed.issue_posts, 1)
        self.assertEqual(len(delayed.issues), 1)

        invisible = LostPostWithoutVisibilityClient()
        with mock.patch(
            "scripts.monitor_github_readiness.time.sleep"
        ), self.assertRaisesRegex(github_monitor.GitHubError, "response lost"):
            github_monitor.reconcile_issue(
                invisible,
                "leanprover/lean-eval-submissions",
                "failed",
                "https://github.com/leanprover/lean-eval-submissions/actions/runs/994",
                "@kim-em",
                "994",
            )
        self.assertEqual(invisible.issue_posts, 1)

    def test_rejects_mismatched_run_url_and_id(self) -> None:
        with self.assertRaisesRegex(github_monitor.GitHubError, "run URL"):
            github_monitor.reconcile_issue(
                FakeIssueClient([]),
                "leanprover/lean-eval-submissions",
                "ready",
                "https://github.com/leanprover/lean-eval-submissions/actions/runs/993",
                "@kim-em",
                "994",
            )

    def test_closed_incident_records_failure_before_reopening(self) -> None:
        client = FakeIssueClient([monitor_issue(1, "closed")])
        client.comments[1] = [
            {
                "body": "<!-- lean-eval-lifecycle-monitor-run-999-failed -->",
                "user": {"login": "untrusted-user"},
            }
        ]
        github_monitor.reconcile_issue(
            client,
            "leanprover/lean-eval-submissions",
            "failed",
            "https://github.com/leanprover/lean-eval-submissions/actions/runs/999",
            "@kim-em",
            "999",
        )
        self.assertEqual(client.events, ["comment:1", "state:1:open"])
        self.assertEqual(len(client.comments[1]), 2)

        delayed = DelayedCommentResponseLossClient()
        with mock.patch("scripts.monitor_github_readiness.time.sleep"):
            github_monitor.reconcile_issue(
                delayed,
                "leanprover/lean-eval-submissions",
                "failed",
                "https://github.com/leanprover/lean-eval-submissions/actions/runs/995",
                "@kim-em",
                "995",
            )
        self.assertEqual(delayed.comment_posts, 1)
        self.assertEqual(len(delayed.comments[1]), 1)
        self.assertEqual(delayed.issues[0]["state"], "open")
