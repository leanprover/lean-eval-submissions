from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/publish_replay_state_batch"


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()


class PublishReplayStateBatchTests(unittest.TestCase):
    def run_git(self, *arguments: str, cwd: pathlib.Path | None = None) -> str:
        return subprocess.run(
            [shutil.which("git") or "git", *arguments],
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def test_two_lane_candidate_is_one_create_only_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            remote = root / "remote.git"
            seed = root / "seed"
            publish = root / "publish"
            candidate = root / "candidate"
            self.run_git("init", "--bare", str(remote))
            self.run_git("init", "--initial-branch=main", str(seed))
            self.run_git("config", "user.name", "test", cwd=seed)
            self.run_git("config", "user.email", "test@example.com", cwd=seed)
            (seed / "scripts").mkdir()
            (seed / "scripts/state.py").write_text(
                """#!/usr/bin/env python3
import json, pathlib, shutil, sys
args=sys.argv[1:]
root=pathlib.Path(args[args.index('--root')+1])
command=args[args.index('--root')+2]
if command=='append':
  source=pathlib.Path(args[-1]); value=json.loads(source.read_text()); identity=value['event_id']
  relative=pathlib.Path('events')/identity.replace('-','')[:2]/f'{identity}.json'
  target=root/relative; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,target)
  print(relative.as_posix())
elif command!='validate': raise SystemExit(2)
""",
                encoding="utf-8",
            )
            (seed / "state.json").write_text(
                '{"environment":"production"}\n', encoding="utf-8"
            )
            self.run_git("add", ".", cwd=seed)
            self.run_git("commit", "-m", "initial", cwd=seed)
            self.run_git("remote", "add", "origin", remote.as_uri(), cwd=seed)
            self.run_git("push", "origin", "main", cwd=seed)
            self.run_git("clone", "--branch", "main", remote.as_uri(), str(publish))
            expected_head = self.run_git("rev-parse", "HEAD", cwd=publish)

            descriptors = []
            lane_descriptors: dict[str, list[dict[str, str]]] = {"public": [], "private": []}
            for index, lane in enumerate(("public", "private"), start=1):
                identity = f"01900000-0000-7000-8000-{index:012x}"
                event = {
                    "schema_version": 1,
                    "event_id": identity,
                    "event_type": "test.event",
                    "occurred_at": f"2026-08-28T00:00:00.00{index}Z",
                    "subject_id": lane,
                    "causation_event_id": None,
                    "actor": {"kind": "system"},
                    "payload": {},
                }
                relative = f"events/{identity.replace('-', '')[:2]}/{identity}.json"
                target = candidate / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                raw = canonical(event)
                target.write_bytes(raw)
                descriptor = {"path": relative, "sha256": hashlib.sha256(raw).hexdigest()}
                descriptors.append(descriptor)
                lane_descriptors[lane].append(descriptor)
            descriptors.sort(key=lambda item: item["path"])
            manifest = {
                "schema_version": 1,
                "kind": "historical_baseline_state_append_candidate",
                "activation_status": "reviewed_but_not_appended",
                "state": {
                    "repository": "leanprover/lean-eval-state",
                    "expected_head": expected_head,
                },
                "expectation": {"total_event_count": 2, "total_task_count": 2},
                "lanes": {
                    lane: {"event_files": lane_descriptors[lane], "task_count": 1}
                    for lane in ("public", "private")
                },
                "event_files": descriptors,
                "event_set_sha256": hashlib.sha256(canonical(descriptors)).hexdigest(),
            }
            manifest_path = candidate / "historical-baseline-state-append-candidate.json"
            manifest_path.write_bytes(canonical(manifest))
            script = root / "publish-batch"
            script.write_text(
                SCRIPT.read_text(encoding="utf-8").replace(
                    "git@github.com:leanprover/lean-eval-state.git", remote.as_uri()
                ),
                encoding="utf-8",
            )
            script.chmod(0o755)
            result = subprocess.run(
                [
                    str(script),
                    "--root",
                    str(publish),
                    "--candidate-root",
                    str(candidate),
                    "--manifest",
                    str(manifest_path),
                    "--expected-head",
                    expected_head,
                    "--message",
                    "append batch",
                ],
                env={**os.environ, "STATE_WRITE_KEY": "test", "RUNNER_TEMP": str(root)},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            new_head = self.run_git("rev-parse", "refs/heads/main", cwd=remote)
            self.assertEqual(result.stdout.strip(), new_head)
            changed = self.run_git("diff", "--name-only", f"{expected_head}..{new_head}", cwd=remote)
            self.assertEqual(changed.splitlines(), [item["path"] for item in descriptors])


if __name__ == "__main__":
    unittest.main()
