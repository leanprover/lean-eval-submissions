from __future__ import annotations

import copy
import pathlib
import subprocess
import tempfile
import unittest

from tests.test_historical_replay_controller import Fixture
from scripts.historical_public_preparation_identity import execution_identity

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/historical-authoritative-replay.yml").read_text()
PREPARATION = WORKFLOW.split("  prepare-executor:\n", 1)[1].split("  replay-one:\n", 1)[0]
EXECUTION = WORKFLOW.split("  replay-one:\n", 1)[1].split("  replenish-public-lane:\n", 1)[0]


class HistoricalPublicDeployPreparationTests(unittest.TestCase):
    def test_unrelated_state_appends_preserve_identity_but_every_execution_binding_is_exact(self) -> None:
        fixture = Fixture()
        original = fixture.plan()
        updated = copy.deepcopy(original)
        updated["queue"]["queue_source_digest"] = "a" * 64
        updated["queue"]["queue_source_event_count"] += 2
        self.assertEqual(execution_identity(updated), execution_identity(original))
        for field, value in (("kernel_release", "wrong"), ("vm_image_digest", "sha256:" + "1" * 64)):
            changed = copy.deepcopy(original)
            changed["execution_profile"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                execution_identity(changed)
        changed = copy.deepcopy(original)
        changed["task"]["source_commit"] = "a" * 40
        with self.assertRaises(ValueError):
            execution_identity(changed)
        # A separately valid later attempt has a different prepared identity.
        retry = copy.deepcopy(fixture.queue)
        retry["tasks"][0].update(status="failed", attempt=1, reason_code="runner_lost", retryable=True)
        self.assertNotEqual(execution_identity(fixture.plan(retry)), execution_identity(original))

    def test_preparation_cannot_start_recover_or_execute_a_task(self) -> None:
        self.assertIn("timeout-minutes: 30", PREPARATION)
        self.assertIn("permissions:\n      contents: read", PREPARATION)
        for forbidden in ("id-token: write", "PRODUCTION_STATE_WRITE_KEY", "publish_replay_state_event",
                          "state-event started", "build-executor-request", "cleanup-reservation",
                          "historical_public_runner.py prepare", "/status"):
            self.assertNotIn(forbidden, PREPARATION)
        self.assertIn("historical_replay_controller.py recover", PREPARATION)
        self.assertIn("if: steps.preflight.outputs.kind == 'none'", PREPARATION)
        self.assertIn("if: steps.plan.outputs.kind == 'execution'", PREPARATION)
        self.assertIn("render-executor-config", PREPARATION)
        self.assertIn("wait_replay_container_rollout.py", PREPARATION)
        self.assertIn("historical executor health differs from the exact plan", PREPARATION)
        self.assertNotIn("actions/upload-artifact", PREPARATION)

    def test_execution_replans_and_checks_identity_and_health_without_cold_deployment(self) -> None:
        self.assertIn("needs: prepare-executor", EXECUTION)
        self.assertIn("timeout-minutes: 360", EXECUTION)
        self.assertIn("HISTORICAL_REPLAY_JOB_STARTED_EPOCH", EXECUTION)
        self.assertNotIn("wrangler deploy", EXECUTION)
        self.assertNotIn("render-executor-config", EXECUTION)
        self.assertNotIn("wait_replay_container_rollout.py", EXECUTION)
        self.assertNotIn("CLOUDFLARE_API_TOKEN: ${{ secrets", EXECUTION)
        self.assertNotIn("actions/setup-node", EXECUTION)
        self.assertNotIn("npm --prefix server ci", EXECUTION)
        self.assertNotIn("Install one temporary read-only State refresh identity", PREPARATION)
        self.assertIn("Validate State and identify only one stale running attempt", EXECUTION)
        self.assertIn("Publish one destruction-confirmed stale-runner recovery and stop", EXECUTION)
        self.assertIn("Report a still-running controller and stop", EXECUTION)
        planning = EXECUTION.index("Plan the exact next qualified public task")
        identity = EXECUTION.index("Require the exact prepared executor identity")
        health = EXECUTION.index("historical executor health differs from the exact plan")
        reserve = EXECUTION.index("Reserve exact cleanup identity")
        self.assertLess(planning, identity)
        self.assertLess(identity, health)
        self.assertLess(health, reserve)
        self.assertIn('test "$PREPARED_COMMIT" = "$GITHUB_SHA"', EXECUTION)
        self.assertIn('test "$actual" = "$PREPARED_IDENTITY"', EXECUTION)
        self.assertIn("--phase before-start", EXECUTION)
        self.assertIn("--phase before-execute", EXECUTION)

    def test_execution_guard_accepts_only_complete_results_descendants(self) -> None:
        import json
        import os
        import textwrap
        block = EXECUTION.split("      - name: Require exact protected main and the explicit controller gate\n", 1)[1].split("      - uses:", 1)[0]
        body = textwrap.dedent(block.split("        run: |\n", 1)[1])
        base, head = "1" * 40, "2" * 40
        valid = {"status": "ahead", "base_commit": {"sha": base}, "merge_base_commit": {"sha": base},
                 "commits": [{"sha": head}], "ahead_by": 1, "total_commits": 1,
                 "files": [{"filename": "results/alice.json", "status": "modified"},
                           {"filename": "verification/r2_" + "a" * 64 + ".json", "status": "added"}]}
        cases = [(base, {}, True), (head, valid, True)]
        for patch in ({"status": "diverged"},
                      {"files": [{"filename": "server/src/replay-app.ts", "status": "modified"}]},
                      {"files": [{"filename": "results/alice.json", "status": "removed"}]},
                      {"ahead_by": 2}, {"merge_base_commit": {"sha": "3" * 40}}):
            cases.append((head, {**valid, **patch}, False))
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            gh = root / "gh"
            gh.write_text('#!/bin/sh\ncase "$*" in *branches/main*) cat "$FAKE_BRANCH" ;; *compare/*) cat "$FAKE_COMPARE" ;; *) exit 23 ;; esac\n')
            gh.chmod(0o700)
            for branch, comparison, expected in cases:
                (root / "branch").write_text(branch + "\ttrue\n")
                (root / "comparison").write_text(json.dumps(comparison))
                result = subprocess.run(["bash", "-c", body], cwd=ROOT, capture_output=True, text=True,
                    env={**os.environ, "PATH": str(root) + ":" + os.environ["PATH"], "GITHUB_ENV": str(root / "env"),
                         "FAKE_BRANCH": str(root / "branch"), "FAKE_COMPARE": str(root / "comparison"),
                         "GITHUB_SHA": base, "GITHUB_REPOSITORY": "leanprover/lean-eval-submissions",
                         "GITHUB_REF": "refs/heads/main", "GITHUB_REF_PROTECTED": "true",
                         "HISTORICAL_CONTROLLER_ENABLED": "true", "CONSECUTIVE_DEFERRALS": "0",
                         "DEFERRED_TASK_ID": "", "REPLENISH_LANE": "false", "REVIEWED_IMPLEMENTATION_COMMIT": base})
                self.assertEqual(result.returncode == 0, expected, result.stderr)
        self.assertIn('test "$branch" = "$GITHUB_SHA"', PREPARATION)

    def test_runtime_guard_failure_notice_is_source_free_and_stops_execution(self) -> None:
        import json
        import os
        import textwrap
        fragment = EXECUTION.split('              if [ "$poll_status" = 500 ]; then\n', 1)[1].split('              mv "$RUNNER_TEMP/executor-poll-response.json"', 1)[0]
        script = textwrap.dedent('              if [ "$poll_status" = 500 ]; then\n' + fragment) + '\necho continued\n'
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for detail in ("runtime_profile_mismatch", "runtime_profile_guard_failed", "private_source"):
                response = {"error": "executor_failed", "reason": "command_failed", "detail": detail}
                (root / "executor-poll-response.json").write_text(json.dumps(response))
                output = root / "outputs"
                summary = root / "summary"
                output.unlink(missing_ok=True)
                summary.unlink(missing_ok=True)
                result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=ROOT,
                                        env={**os.environ, "RUNNER_TEMP": directory, "GITHUB_OUTPUT": str(output),
                                             "GITHUB_STEP_SUMMARY": str(summary), "poll_status": "500"},
                                        capture_output=True, text=True)
                if detail == "private_source":
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "continued\n")
                    self.assertFalse(output.exists())
                    self.assertNotIn(detail, result.stdout + result.stderr)
                else:
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("::notice title=Public runtime guard stopped::" + detail, result.stdout)
                    self.assertNotIn("continued", result.stdout)
                    self.assertEqual(output.read_text(), "runtime_guard_failed=true\n")
                    self.assertIn("No checker verdict", summary.read_text())
        self.assertIn("needs.replay-one.outputs.runtime_guard_failed != 'true'", WORKFLOW)
        self.assertNotIn("execution_profile_permanently_unavailable", fragment)

    def test_small_prepared_identity_check_rejects_changed_task_and_code_before_reservation(self) -> None:
        block = EXECUTION.split("      - name: Require the exact prepared executor identity without redeployment\n", 1)[1].split("      - name:", 1)[0]
        import textwrap
        body = textwrap.dedent(block.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for actual, prepared, code, expected in (("a" * 64, "a" * 64, "b" * 40, 0),
                                                      ("a" * 64, "c" * 64, "b" * 40, 1),
                                                      ("a" * 64, "a" * 64, "c" * 40, 1)):
                import os
                output = root / "outputs"
                output.unlink(missing_ok=True)
                result = subprocess.run(["bash", "-c", 'python() { echo "$ACTUAL_IDENTITY"; }\n' + body],
                                        env={**os.environ, "GITHUB_OUTPUT": str(output), "RUNNER_TEMP": directory,
                                             "GITHUB_SHA": "b" * 40, "PREPARED_COMMIT": code,
                                             "PREPARED_PLAN_KIND": "execution", "PREPARED_IDENTITY": prepared,
                                             "ACTUAL_IDENTITY": actual}, text=True, capture_output=True)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(output.read_text() if output.exists() else "", "deployed=true\n" if expected == 0 else "")


if __name__ == "__main__":
    unittest.main()
