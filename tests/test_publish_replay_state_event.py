from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "publish_replay_state_event"


class PublishReplayStateEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_writer_is_environment_exact_non_force_and_parent_exact(self) -> None:
        self.assertIn('!= "$expected_head"', self.text)
        self.assertIn('--environment', self.text)
        self.assertIn('!= "$state_environment"', self.text)
        self.assertIn("lean-eval-state-staging.git", self.text)
        self.assertIn("lean-eval-state.git", self.text)
        self.assertIn('staging) remote=', self.text)
        self.assertIn('production) remote=', self.text)
        self.assertIn("HEAD:refs/heads/main", self.text)
        self.assertNotIn("--force", self.text)
        self.assertNotIn("+HEAD:", self.text)

    def test_writer_validates_and_stages_only_the_new_event(self) -> None:
        self.assertIn('scripts/state.py" --root "$state_root" append', self.text)
        self.assertIn('scripts/state.py" --root "$state_root" validate', self.text)
        self.assertIn("diff --cached --name-status", self.text)
        self.assertIn("expected_change=$(printf 'A\\t%s'", self.text)

    def test_writer_uses_isolated_pinned_ssh_material_and_scrubs_it(self) -> None:
        self.assertIn("GitHub's published Ed25519 host key", self.text)
        self.assertIn("StrictHostKeyChecking=yes", self.text)
        self.assertIn("IdentitiesOnly=yes", self.text)
        self.assertIn("ConnectTimeout=15", self.text)
        self.assertIn("ServerAliveInterval=15", self.text)
        self.assertIn("ServerAliveCountMax=3", self.text)
        self.assertIn('shred --remove "$key_file"', self.text)
        self.assertIn('unset STATE_WRITE_KEY', self.text)

    def test_unknown_push_outcome_is_resolved_against_remote_head(self) -> None:
        self.assertIn("push --porcelain", self.text)
        self.assertIn("HEAD:refs/heads/main >&2", self.text)
        self.assertIn('git -C "$state_root" fetch', self.text)
        self.assertIn('"+refs/heads/main:$reconciled_ref"', self.text)
        self.assertIn(
            'merge-base --is-ancestor "$new_commit" "$remote_head"', self.text
        )
        self.assertIn('merge --ff-only "$remote_head"', self.text)
        self.assertIn("printf '%s\\n' \"$remote_head\"", self.text)

    def run_git(self, *arguments: str, cwd: pathlib.Path | None = None) -> str:
        return subprocess.run(
            [shutil.which("git") or "git", *arguments],
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def make_fixture(
        self, root: pathlib.Path
    ) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, str]:
        remote = root / "remote.git"
        seed = root / "seed"
        publish = root / "publish"
        self.run_git("init", "--bare", str(remote))
        self.run_git("init", "--initial-branch=main", str(seed))
        self.run_git("config", "user.name", "test", cwd=seed)
        self.run_git("config", "user.email", "test@example.com", cwd=seed)
        (seed / "scripts").mkdir()
        state_program = seed / "scripts/state.py"
        state_program.write_text(
            """#!/usr/bin/env python3
import json, pathlib, shutil, sys
args = sys.argv[1:]
root = pathlib.Path(args[args.index('--root') + 1])
command = args[args.index('--root') + 2]
if command == 'append':
    source = pathlib.Path(args[-1])
    value = json.loads(source.read_text())
    event_id = value['event_id']
    relative = pathlib.Path('events') / event_id[:2] / f'{event_id}.json'
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(relative.as_posix())
elif command != 'validate':
    raise SystemExit(2)
""",
            encoding="utf-8",
        )
        state_program.chmod(0o755)
        (seed / "state.json").write_text(
            '{"environment":"staging"}\n', encoding="utf-8"
        )
        self.run_git("add", ".", cwd=seed)
        self.run_git("commit", "-m", "initial", cwd=seed)
        self.run_git("remote", "add", "origin", remote.as_uri(), cwd=seed)
        self.run_git("push", "origin", "main", cwd=seed)
        self.run_git("clone", "--branch", "main", remote.as_uri(), str(publish))
        expected = self.run_git("rev-parse", "HEAD", cwd=publish)
        event = root / "event.json"
        event.write_text(
            """{"actor":{"kind":"system"},"causation_event_id":null,"event_id":"01900000-0000-7000-8000-000000000001","event_type":"test.event","occurred_at":"2026-08-28T00:00:00.000Z","payload":{},"schema_version":1,"subject_id":"test"}
""",
            encoding="utf-8",
        )
        script = root / "publish-state-event"
        script.write_text(
            self.text.replace(
                "staging) remote=git@github.com:leanprover/lean-eval-state-staging.git ;;",
                f"staging) remote={remote.as_uri()} ;;",
            ),
            encoding="utf-8",
        )
        script.chmod(0o755)
        return publish, event, script, expected

    def run_lost_response_case(
        self, mode: str
    ) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = pathlib.Path(temporary.name)
        publish, event, script, expected = self.make_fixture(root)
        wrapper_directory = root / "bin"
        wrapper_directory.mkdir()
        wrapper = wrapper_directory / "git"
        wrapper.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" push --porcelain "* ]]; then
  if [ "$TEST_PUSH_MODE" = descendant ]; then
    "$REAL_GIT" "$@"
  fi
  "$REAL_GIT" clone --quiet --branch main "$TEST_REMOTE" "$TEST_CONCURRENT"
  "$REAL_GIT" -C "$TEST_CONCURRENT" config user.name concurrent
  "$REAL_GIT" -C "$TEST_CONCURRENT" config user.email concurrent@example.com
  printf '%s\n' unrelated > "$TEST_CONCURRENT/unrelated"
  "$REAL_GIT" -C "$TEST_CONCURRENT" add unrelated
  "$REAL_GIT" -C "$TEST_CONCURRENT" commit --quiet -m unrelated
  "$REAL_GIT" -C "$TEST_CONCURRENT" push --quiet origin main
  exit 1
fi
exec "$REAL_GIT" "$@"
""",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        environment = {
            **os.environ,
            "PATH": f"{wrapper_directory}:{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
            "TEST_PUSH_MODE": mode,
            "TEST_REMOTE": (root / "remote.git").as_uri(),
            "TEST_CONCURRENT": str(root / "concurrent"),
            "STATE_WRITE_KEY": "test-only-key",
            "RUNNER_TEMP": str(root),
        }
        result = subprocess.run(
            [
                str(script),
                "--environment",
                "staging",
                "--root",
                str(publish),
                "--event",
                str(event),
                "--expected-head",
                expected,
                "--message",
                "test append",
            ],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        return result, publish, root / "remote.git"

    def test_lost_response_with_unrelated_descendant_is_reconciled(self) -> None:
        result, publish, remote = self.run_lost_response_case("descendant")
        self.assertEqual(result.returncode, 0, result.stderr)
        remote_head = self.run_git("rev-parse", "refs/heads/main", cwd=remote)
        self.assertEqual(result.stdout.strip(), remote_head)
        self.assertEqual(self.run_git("rev-parse", "HEAD", cwd=publish), remote_head)
        self.assertTrue((publish / "unrelated").is_file())

    def test_true_nonancestor_after_unknown_push_outcome_fails(self) -> None:
        result, _, _ = self.run_lost_response_case("nonancestor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("was not reconciled", result.stderr)


if __name__ == "__main__":
    unittest.main()
