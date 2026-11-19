from __future__ import annotations

import ast
import copy
import os
import pathlib
import re
import subprocess
import tempfile
import textwrap
import unittest

from tests import test_historical_replay_controller as controller_tests
from scripts.historical_replay_controller import (
    _historical_replay_states, started_event, terminal_event,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/historical-authoritative-replay.yml").read_text()


def step(name: str) -> str:
    return WORKFLOW.split(f"      - name: {name}\n", 1)[1].split("\n      - ", 1)[0]


def condition(block: str, values: dict, *, cancelled: bool = False) -> bool:
    """Evaluate the actual small boolean expressions, not a copied policy."""
    expression = re.search(r"^\s+if:\s*(?:>-\s*)?(\$\{\{.*?\}\}|[^\n]+)", block, re.M | re.S).group(1)
    expression = expression.removeprefix(">-").strip().removeprefix("${{").removesuffix("}}").strip()
    expression = " ".join(expression.split()).replace("cancelled()", repr(cancelled))
    for name, value in sorted(values.items(), key=lambda item: -len(item[0])):
        expression = expression.replace(name, repr(value))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression).strip()
    tree = ast.parse(expression, mode="eval")
    allowed = (ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Constant,
               ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq, ast.Gt, ast.Name, ast.Load)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise AssertionError(f"unexpected workflow condition: {expression}")
    return bool(eval(compile(tree, "<workflow-condition>", "eval"),
                     {"__builtins__": {}}, {"true": True, "false": False}))


class HistoricalPublicPreparationAccountingTests(unittest.TestCase):
    def test_continue_on_error_failure_runs_accounting_but_never_executor(self) -> None:
        prepare = step("Fetch exact public source and benchmark and build the runner handoff")
        started = step("Append replay.started only after public preparation and capacity checks")
        execute = step("Invoke the exact executor without Cloudflare or State write authority")
        failure = step("Fail a started attempt explicitly if orchestration did not finish")
        self.assertIn("continue-on-error: true", prepare)
        self.assertIn("timeout-minutes: 5", prepare)
        values = {"steps.prepare.outcome": "failure", "steps.prepare.outputs.ready": "",
                  "steps.started.outputs.appended": "true", "steps.execute.outputs.ready": "",
                  "steps.terminal.outputs.appended": ""}
        self.assertTrue(condition(started, values))
        self.assertFalse(condition(execute, values))
        self.assertTrue(condition(failure, values))
        # An operator cancellation is never converted into a charged attempt.
        self.assertFalse(condition(started, values, cancelled=True))
        self.assertFalse(condition(failure, values, cancelled=True))
        self.assertIn('if [ "$PREPARE_OUTCOME" = success ]; then', started)
        self.assertIn('echo source_fetch_failed > "$RUNNER_TEMP/failure-reason"', started)

    def test_deferred_outcome_is_eligible_only_for_a_bounded_nonbusy_successor(self) -> None:
        values = {"needs.replay-one.result": "success", "inputs.replenish_lane": True,
                  "inputs.remaining_runs": 2, "needs.replay-one.outputs.preflight_kind": "none",
                  "needs.replay-one.outputs.plan_kind": "deferred"}
        replenish = WORKFLOW.split("  replenish-public-lane:\n", 1)[1]
        self.assertTrue(condition(replenish, values))
        for name, value in (("inputs.remaining_runs", 1),
                            ("needs.replay-one.outputs.preflight_kind", "busy"),
                            ("needs.replay-one.result", "failure")):
            self.assertFalse(condition(replenish, {**values, name: value}))
        self.assertFalse(condition(replenish, values, cancelled=True))
        self.assertIn('next_runs=$(( REMAINING_RUNS - 1 ))', replenish)
        self.assertIn("steps.started.outputs.deferred == 'true' && 'deferred'", WORKFLOW)
        execute = step("Invoke the exact executor without Cloudflare or State write authority")
        self.assertFalse(condition(execute, {"steps.started.outputs.appended": "",
                                            "steps.prepare.outputs.ready": "true"}))

    def test_four_preparation_failures_form_terminal_bounded_state_history(self) -> None:
        fixture = controller_tests.HistoricalReplayRecoveryTests()
        fixture.setUp()
        events = fixture.events()[:-1]  # Authoritative root/authority/qualification/enqueue.
        model = fixture.fixture
        queue = copy.deepcopy(model.queue)
        task_id = model.task["replay_task_id"]
        for attempt in range(1, 5):
            plan = model.plan(queue)
            started = started_event(
                plan, queue, f"2026-08-25T01:00:{attempt:02d}.000Z",
                random_bytes=bytes([attempt]) * 10,
            )
            failed = terminal_event(
                plan, started, f"2026-08-25T01:00:{attempt:02d}.001Z",
                failure_reason="source_fetch_failed", random_bytes=bytes([attempt + 4]) * 10,
            )
            events.extend((started, failed))
            states = _historical_replay_states(events, {model.task["result_id"]})
            self.assertEqual(states[task_id]["status"], "failed")
            self.assertEqual(states[task_id]["attempt"], attempt)
            self.assertEqual(failed["payload"]["retryable"], attempt < 4)
            queue["tasks"][0].update(
                attempt=attempt, status="failed", reason_code="source_fetch_failed",
                retryable=attempt < 4, event_id=failed["event_id"],
                occurred_at=failed["occurred_at"],
            )
        self.assertEqual(len([e for e in events if e["event_type"] == "replay.started"]), 4)
        self.assertFalse(events[-1]["payload"]["retryable"])

    def test_first_cas_budget_refusal_has_no_start_publication(self) -> None:
        self.assert_deferred_cas(first_status=3, rejected_pushes=0)

    def test_second_cas_budget_refusal_has_no_start_publication(self) -> None:
        self.assert_deferred_cas(first_status=0, rejected_pushes=1)

    def assert_deferred_cas(self, *, first_status: int, rejected_pushes: int) -> None:
        block = step("Append replay.started only after public preparation and capacity checks")
        body = textwrap.dedent(block.split("        run: |\n", 1)[1])
        # Exercise the actual shell control flow: first CAS transport rejects
        # without writing State; after refresh, capacity is exhausted on retry.
        prelude = r'''
python() {
  if [ "$1" = scripts/historical_replay_job_budget.py ]; then
    count=$(cat "$RUNNER_TEMP/budget-count" 2>/dev/null || echo 0)
    count=$((count + 1)); echo "$count" > "$RUNNER_TEMP/budget-count"
    if [ "$count" -eq 1 ]; then return "$BUDGET_FIRST_STATUS"; else return 3; fi
  fi
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --output ]; then shift; : > "$1"; break; fi
    shift
  done
}
git() {
  case "$*" in *rev-parse*) printf '%040d\n' 1 ;; esac
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "scripts").mkdir()
            publish = root / "scripts/publish_replay_state_event"
            publish.write_text('#!/bin/sh\necho rejected >> "$RUNNER_TEMP/pushes"\nexit 1\n')
            publish.chmod(0o755)
            output = root / "outputs"
            result = subprocess.run(
                ["bash", "-c", prelude + body], cwd=root,
                env={**os.environ, "RUNNER_TEMP": directory, "GITHUB_OUTPUT": str(output),
                     "PREPARE_OUTCOME": "success", "HISTORICAL_REPLAY_JOB_STARTED_EPOCH": "1",
                     "BUDGET_FIRST_STATUS": str(first_status)},
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), "deferred=true\n")
            self.assertEqual((root / "pushes").read_text() if (root / "pushes").exists() else "",
                             "rejected\n" * rejected_pushes)
            self.assertEqual((root / "budget-count").read_text(), f"{1 + rejected_pushes}\n")
            self.assertFalse((root / "failure-reason").exists())


if __name__ == "__main__":
    unittest.main()
