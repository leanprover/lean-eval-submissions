from __future__ import annotations

import pathlib
import json
import os
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github/workflows/historical-replay-two-lane-driver.yml"
).read_text(encoding="utf-8")
PUBLIC = (
    ROOT / ".github/workflows/historical-authoritative-replay.yml"
).read_text(encoding="utf-8")
PRIVATE = (
    ROOT / ".github/workflows/historical-private-replay.yml"
).read_text(encoding="utf-8")


def shell_step(workflow: str, name: str) -> str:
    section = workflow.split(f"- name: {name}", 1)[1]
    body = section.split("run: |\n", 1)[1]
    lines: list[str] = []
    for line in body.splitlines():
        if line and not line.startswith("          "):
            break
        lines.append(line[10:] if line else line)
    return "\n".join(lines) + "\n"


def exercise_dispatch(
    script: str,
    *,
    counter: str,
    value: int,
    baseline: str = "1" * 40,
    head: str = "1" * 40,
    comparison: dict | None = None,
) -> tuple[list[str], dict]:
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        fake_gh = root / "gh"
        fake_gh.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail
                if [[ "$*" == *"/branches/main"* ]]; then
                  printf '%s\\n' "$FAKE_BRANCH"
                  exit 0
                fi
                if [[ "$*" == *"/compare/"* ]]; then
                  cat "$FAKE_COMPARISON"
                  exit 0
                fi
                printf '%s\\n' "$@" > "$FAKE_ARGS"
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = --input ]; then
                    cp "$2" "$FAKE_PAYLOAD"
                    exit 0
                  fi
                  shift
                done
                exit 1
                """
            ),
            encoding="utf-8",
        )
        fake_gh.chmod(0o700)
        args = root / "args"
        payload = root / "payload.json"
        comparison_path = root / "comparison.json"
        comparison_path.write_text(
            json.dumps(comparison if comparison is not None else {}),
            encoding="utf-8",
        )
        environment = {
            **os.environ,
            "PATH": f"{root}:{os.environ['PATH']}",
            "GH_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "leanprover/lean-eval-submissions",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_PROTECTED": "true",
            "GITHUB_SHA": "1" * 40,
            "FAKE_BRANCH": f"{head}\ttrue",
            "FAKE_ARGS": str(args),
            "FAKE_COMPARISON": str(comparison_path),
            "FAKE_PAYLOAD": str(payload),
            "REVIEWED_IMPLEMENTATION_COMMIT": baseline,
            counter: str(value),
        }
        completed = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        return args.read_text(encoding="utf-8").splitlines(), json.loads(
            payload.read_text(encoding="utf-8")
        )


class HistoricalReplayTwoLaneDriverTests(unittest.TestCase):
    def test_driver_is_manual_temporary_and_secret_free(self) -> None:
        self.assertIn("workflow_dispatch:", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)
        self.assertIn("Temporary retained-baseline machinery", WORKFLOW)
        self.assertIn("permissions:\n  contents: none", WORKFLOW)
        self.assertNotIn("secrets.", WORKFLOW)
        self.assertNotIn("environment:", WORKFLOW)
        self.assertNotIn("id-token: write", WORKFLOW)
        self.assertNotIn("actions/checkout", WORKFLOW)

    def test_driver_starts_exactly_two_independent_lanes(self) -> None:
        self.assertEqual(WORKFLOW.count("permissions:\n      actions: write"), 2)
        self.assertEqual(WORKFLOW.count("      contents: read"), 2)
        self.assertEqual(WORKFLOW.count("actions/workflows/"), 2)
        self.assertEqual(
            WORKFLOW.count(
                "actions/workflows/historical-authoritative-replay.yml/dispatches"
            ),
            1,
        )
        self.assertEqual(
            WORKFLOW.count(
                "actions/workflows/historical-private-replay.yml/dispatches"
            ),
            1,
        )
        self.assertIn("start-public-lane:", WORKFLOW)
        self.assertIn("start-private-lane:", WORKFLOW)

    def test_each_lane_is_exact_main_only_and_hard_bounded(self) -> None:
        self.assertEqual(
            WORKFLOW.count(
                'test "$GITHUB_REPOSITORY" = leanprover/lean-eval-submissions'
            ),
            2,
        )
        self.assertEqual(
            WORKFLOW.count('test "$GITHUB_REF" = refs/heads/main'), 2
        )
        self.assertEqual(WORKFLOW.count('test "$GITHUB_REF_PROTECTED" = true'), 2)
        self.assertEqual(WORKFLOW.count('test "$RUN_BUDGET" -ge 1'), 2)
        self.assertEqual(WORKFLOW.count('test "$RUN_BUDGET" -le 1024'), 2)
        self.assertEqual(WORKFLOW.count('test "$branch" = "$GITHUB_SHA"'), 2)
        self.assertEqual(WORKFLOW.count('replenish_lane: "true"'), 2)

    def test_driver_payloads_and_successor_decrements_execute_exactly(self) -> None:
        cases = (
            (
                WORKFLOW,
                "Dispatch the first bounded public-lane run",
                "RUN_BUDGET",
                256,
                "historical-authoritative-replay.yml",
                "confirm_authoritative_replay",
                "256",
            ),
            (
                WORKFLOW,
                "Dispatch the first bounded private-lane run",
                "RUN_BUDGET",
                768,
                "historical-private-replay.yml",
                "confirm_historical_private_replay",
                "768",
            ),
            (
                PUBLIC,
                "Dispatch exactly one bounded public-lane successor",
                "REMAINING_RUNS",
                7,
                "historical-authoritative-replay.yml",
                "confirm_authoritative_replay",
                "6",
            ),
            (
                PRIVATE,
                "Dispatch exactly one bounded private-lane successor",
                "REMAINING_RUNS",
                7,
                "historical-private-replay.yml",
                "confirm_historical_private_replay",
                "6",
            ),
        )
        for workflow, step, counter, value, target, confirmation, expected in cases:
            with self.subTest(step=step):
                args, payload = exercise_dispatch(
                    shell_step(workflow, step), counter=counter, value=value
                )
                self.assertIn(
                    "repos/leanprover/lean-eval-submissions/actions/workflows/"
                    f"{target}/dispatches",
                    args,
                )
                self.assertEqual(payload["ref"], "main")
                self.assertEqual(payload["inputs"][confirmation], "true")
                self.assertEqual(payload["inputs"]["replenish_lane"], "true")
                self.assertEqual(payload["inputs"]["remaining_runs"], expected)
                self.assertEqual(
                    payload["inputs"]["reviewed_implementation_commit"],
                    "1" * 40,
                )

    def test_successor_accepts_only_complete_results_only_descendants(self) -> None:
        baseline = "1" * 40
        head = "2" * 40
        comparison = {
            "status": "ahead",
            "base_commit": {"sha": baseline},
            "merge_base_commit": {"sha": baseline},
            "head_commit": {"sha": head},
            "ahead_by": 1,
            "total_commits": 1,
            "commits": [{}],
            "files": [{"filename": "results/alice.json", "status": "added"}],
        }
        script = shell_step(
            PUBLIC, "Dispatch exactly one bounded public-lane successor"
        )
        _, payload = exercise_dispatch(
            script,
            counter="REMAINING_RUNS",
            value=7,
            baseline=baseline,
            head=head,
            comparison=comparison,
        )
        self.assertEqual(
            payload["inputs"]["reviewed_implementation_commit"], baseline
        )

        comparison["files"][0]["filename"] = "scripts/changed.py"
        with self.assertRaises(AssertionError):
            exercise_dispatch(
                script,
                counter="REMAINING_RUNS",
                value=7,
                baseline=baseline,
                head=head,
                comparison=comparison,
            )


if __name__ == "__main__":
    unittest.main()
