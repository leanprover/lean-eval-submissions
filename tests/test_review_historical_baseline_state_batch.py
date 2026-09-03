from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import review_historical_baseline_state_batch as review  # noqa: E402
from prepare_historical_baseline_state_batch import (  # noqa: E402
    BaselineBatchError,
    canonical,
)


def identity(number: int) -> str:
    return f"01900000-0000-7000-8000-{number:012x}"


def result_identity(number: int) -> str:
    return f"r2_{number:064x}"


def task_identity(number: int) -> str:
    return f"rt1_{number:064x}"


class ReviewHistoricalBaselineStateBatchTests(unittest.TestCase):
    def git(self, root: pathlib.Path, *arguments: str) -> str:
        return subprocess.run(
            [shutil.which("git") or "git", "-C", str(root), *arguments],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def initialize_repository(self, root: pathlib.Path) -> str:
        self.git(root, "init", "--initial-branch=main")
        self.git(root, "config", "user.name", "test")
        self.git(root, "config", "user.email", "test@example.com")
        return ""

    def write_state_modules(self, state: pathlib.Path) -> None:
        scripts = state / "scripts"
        scripts.mkdir()
        (scripts / "validate_state.py").write_text(
            """import json, pathlib
def load_environment(root,indexed,repository):
  return json.loads(pathlib.Path(root,'state.json').read_text())['environment']
def load_tree(root):
  events=[json.loads(p.read_text()) for p in sorted(pathlib.Path(root,'events').glob('*/*.json'))]
  return 'production',events
def validate_event_data(event,label):
  if 'event_id' not in event: raise ValueError(label)
def validate_semantics(events,environment):
  ids=[e['event_id'] for e in events]
  if len(ids)!=len(set(ids)): raise ValueError('duplicate')
""",
            encoding="utf-8",
        )
        (scripts / "materialize_state.py").write_text(
            """import json, pathlib
def materialize(environment,events):
  lanes={'public':[],'private':[]}
  qualifications={e['event_id']:e['event_type'].startswith('historical_archive_result.') for e in events}
  operational={}
  for event in events:
    if event['event_type']=='replay.enqueued':
      lane='private' if qualifications.get(event['causation_event_id'],False) else 'public'
      lanes[lane].append({'replay_task_id':event['subject_id']})
    if event['event_type'].endswith('replay_authorized'):
      identity='eri1_'+event['subject_id'].removeprefix('r2_')
      operational[f'views/effective-result-identities/{identity[5:7]}/{identity}.json']={'event_id':event['event_id']}
  base={'schema_version':1,'environment':environment,'source_event_count':len(events),'source_digest':'0'*64}
  return {'historical-public-replay-queue.json':{**base,'tasks':lanes['public']},'historical-private-replay-queue.json':{**base,'tasks':lanes['private']},**operational}
def write_views(views,output,check):
  for name,value in views.items():
    path=pathlib.Path(output,name); path.parent.mkdir(parents=True,exist_ok=True)
    expected=json.dumps(value,ensure_ascii=True,indent=2,sort_keys=True)+'\\n'
    if check:
      if path.read_text()!=expected: raise ValueError('mismatch')
    else: path.write_text(expected)
""",
            encoding="utf-8",
        )
        (scripts / "public_projection.py").write_text(
            """def project_public_state_v6(environment,events,commit):
  series=[{'replay_task_id':e['subject_id']} for e in events if e['event_type']=='replay.enqueued']
  unavailable=[{'result_id':'r2_'+'f'*64,'source_visibility':'public'}]
  return {'schema_version':6,'environment':environment,'source_state_commit':commit,'historical_replay_series':series,'historical_replay_unavailability':unavailable}
""",
            encoding="utf-8",
        )
        (scripts / "state.py").write_text(
            """#!/usr/bin/env python3
import argparse, pathlib
from validate_state import load_tree, validate_semantics
p=argparse.ArgumentParser(); p.add_argument('--root',type=pathlib.Path); p.add_argument('command'); a=p.parse_args()
environment,events=load_tree(a.root); validate_semantics(events,environment)
""",
            encoding="utf-8",
        )

    def event(self, number: int, event_type: str, subject: str, cause: str | None) -> dict[str, object]:
        return {
            "schema_version": 1,
            "event_id": identity(number),
            "event_type": event_type,
            "occurred_at": f"2026-09-03T00:00:00.{number:03d}Z",
            "subject_id": subject,
            "causation_event_id": cause,
            "actor": {"kind": "system"},
            "payload": {"result_id": subject if subject.startswith("r2_") else result_identity(number // 3)},
        }

    def candidate_events(self) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        for lane, start, number in (("public", 1, 1), ("private", 4, 2)):
            prefix = "historical_result" if lane == "public" else "historical_archive_result"
            result = result_identity(number)
            task = task_identity(number)
            authority = self.event(start, f"{prefix}.replay_authorized", result, None)
            qualification = self.event(
                start + 1,
                f"{prefix}.replay_profile_qualified",
                result,
                str(authority["event_id"]),
            )
            enqueue = self.event(
                start + 2,
                "replay.enqueued",
                task,
                str(qualification["event_id"]),
            )
            enqueue["payload"] = {"result_id": result}
            events.extend((authority, qualification, enqueue))
        return events

    def make_fixture(self, directory: str) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path, str]:
        root = pathlib.Path(directory)
        submissions = root / "submissions"
        state = root / "state"
        candidate = root / "candidate"
        submissions.mkdir()
        state.mkdir()
        candidate.mkdir()
        self.initialize_repository(submissions)
        self.initialize_repository(state)
        self.git(
            submissions,
            "remote",
            "add",
            "origin",
            "https://github.com/leanprover/lean-eval-submissions.git",
        )
        self.git(
            state,
            "remote",
            "add",
            "origin",
            "https://github.com/leanprover/lean-eval-state.git",
        )
        for relative in review.IMPLEMENTATION_PATHS:
            source = ROOT / relative
            target = submissions / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        self.git(submissions, "add", ".")
        self.git(submissions, "commit", "-m", "implementation")
        implementation = self.git(submissions, "rev-parse", "HEAD")
        self.write_state_modules(state)
        (state / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        (state / "state.json").write_text('{"environment":"production"}\n', encoding="utf-8")
        baseline = self.event(0, "baseline", "baseline", None)
        baseline_path = state / "events" / "01" / f"{identity(0)}.json"
        baseline_path.parent.mkdir(parents=True)
        baseline_path.write_bytes(canonical(baseline))
        self.git(state, "add", ".")
        self.git(state, "commit", "-m", "baseline")
        parent = self.git(state, "rev-parse", "HEAD")
        descriptors = []
        for event in self.candidate_events():
            event_id = str(event["event_id"])
            relative = f"events/{event_id.replace('-', '')[:2]}/{event_id}.json"
            path = candidate / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = canonical(event)
            path.write_bytes(raw)
            descriptors.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
        expectation = {
            "schema_version": 1,
            "kind": "historical_baseline_state_batch_expectation",
            "environment": "production",
            "lanes": {
                "public": {
                    "authority_event_type": "historical_result.replay_authorized",
                    "qualification_event_type": "historical_result.replay_profile_qualified",
                    "enqueue_event_type": "replay.enqueued",
                    "event_count": 3,
                    "task_count": 1,
                },
                "private": {
                    "authority_event_type": "historical_archive_result.replay_authorized",
                    "qualification_event_type": "historical_archive_result.replay_profile_qualified",
                    "enqueue_event_type": "replay.enqueued",
                    "event_count": 3,
                    "task_count": 1,
                },
            },
            "reviewed_unavailability_counts": {"public": 1, "private": 0, "total": 1},
            "total_event_count": 6,
            "total_task_count": 2,
        }
        expectation_path = submissions / "configuration/historical-baseline-state-batch-v1.json"
        expectation_path.write_bytes(canonical(expectation))
        self.git(submissions, "add", ".")
        self.git(submissions, "commit", "--amend", "--no-edit")
        implementation = self.git(submissions, "rev-parse", "HEAD")
        manifest = {
            "schema_version": 1,
            "kind": "historical_baseline_state_append_candidate",
            "activation_status": "reviewed_but_not_appended",
            "state": {"expected_head": parent},
            "audit": {
                "repository": review.AUDIT_REPOSITORY,
                "expected_head": "a" * 40,
                "expected_tree": "b" * 40,
            },
            "event_files": sorted(descriptors, key=lambda item: item["path"]),
        }
        manifest_path = candidate / "historical-baseline-state-append-candidate.json"
        manifest_path.write_bytes(canonical(manifest))
        return submissions, state, candidate, expectation_path, implementation

    def stage(self, directory: str) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        submissions, state, candidate, expectation, implementation = self.make_fixture(directory)
        output = pathlib.Path(directory) / "binding.json"
        args = type(
            "Args",
            (),
            {
                "state_root": state,
                "submissions_root": submissions,
                "state_parent": self.git(state, "rev-parse", "HEAD"),
                "audit_head": "a" * 40,
                "audit_tree": "b" * 40,
                "implementation_commit": implementation,
                "expectation": expectation,
                "candidate_root": candidate,
                "manifest": candidate / "historical-baseline-state-append-candidate.json",
                "output": output,
            },
        )()
        review.stage(args)
        return submissions, state, output

    def test_stage_emits_source_free_closed_binding_and_one_create_only_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            submissions, state, output = self.stage(directory)
            binding = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(binding["kind"], review.SUMMARY_KIND)
            self.assertEqual(binding["state"]["parent"], self.git(state, "rev-parse", "HEAD^"))
            self.assertEqual(binding["state"]["candidate_commit"], self.git(state, "rev-parse", "HEAD"))
            self.assertEqual(binding["candidate"]["event_count"], 6)
            self.assertEqual(binding["candidate"]["lanes"]["public"]["task_count"], 1)
            self.assertEqual(binding["candidate"]["lanes"]["private"]["task_count"], 1)
            self.assertEqual(
                binding["candidate"]["reviewed_unavailability_counts"],
                {"public": 1, "private": 0, "total": 1},
            )
            self.assertNotIn("event_ids", output.read_text(encoding="utf-8"))
            self.assertNotIn(result_identity(1), output.read_text(encoding="utf-8"))
            self.assertNotIn(task_identity(1), output.read_text(encoding="utf-8"))
            changed = self.git(state, "diff", "--name-status", "HEAD^", "HEAD").splitlines()
            self.assertEqual(len(changed), 8)
            self.assertEqual(
                sum(line.startswith("A\tevents/") for line in changed), 6
            )
            self.assertEqual(
                sum(
                    line.startswith("A\tviews/effective-result-identities/")
                    for line in changed
                ),
                2,
            )
            self.assertEqual(
                binding["candidate"]["operational_view_addition_count"], 2
            )
            self.assertEqual(self.git(submissions, "status", "--porcelain"), "")

    def test_verify_rederives_binding_and_rejects_digest_or_tree_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            submissions, state, output = self.stage(directory)
            args = type(
                "Args",
                (),
                {"state_root": state, "submissions_root": submissions, "binding": output},
            )()
            review.verify(args)
            binding = json.loads(output.read_text(encoding="utf-8"))
            binding["candidate"]["event_set_sha256"] = "0" * 64
            output.write_bytes(canonical(binding))
            with self.assertRaisesRegex(BaselineBatchError, "differs"):
                review.verify(args)

    def test_verify_rejects_changed_promotion_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            submissions, state, output = self.stage(directory)
            helper = submissions / "scripts/review_historical_baseline_state_batch.py"
            helper.write_text(helper.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            self.git(submissions, "add", str(helper))
            self.git(submissions, "commit", "-m", "change promotion helper")
            args = type(
                "Args",
                (),
                {"state_root": state, "submissions_root": submissions, "binding": output},
            )()
            with self.assertRaisesRegex(BaselineBatchError, "changed after staging"):
                review.verify(args)

    def test_verify_allows_packet_only_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            submissions, state, output = self.stage(directory)
            binding = submissions / "configuration/historical-baseline-state-promotion-v1.json"
            binding.write_bytes(output.read_bytes())
            self.git(submissions, "add", str(binding))
            self.git(submissions, "commit", "-m", "bind staged State candidate")
            args = type(
                "Args",
                (),
                {"state_root": state, "submissions_root": submissions, "binding": binding},
            )()
            review.verify(args)

    def test_execution_packet_binds_the_state_review_mechanism(self) -> None:
        packet = (
            ROOT / "docs/historical-migration-replay-execution-packet.md"
        ).read_text(encoding="utf-8")
        implementation = "b6f8c8834213a26a19ba1e8c7440db30ad0c05f2"
        self.assertIn(f"submissions commit\n`{implementation}`", packet)
        for relative in (
            ".github/workflows/append-historical-baseline-state.yml",
            "configuration/historical-baseline-state-batch-v1.json",
            "scripts/prepare_historical_baseline_state_batch.py",
            "scripts/review_historical_baseline_state_batch.py",
        ):
            raw = subprocess.run(
                ["git", "-C", ROOT, "show", f"{implementation}:{relative}"],
                check=True,
                capture_output=True,
            ).stdout
            digest = hashlib.sha256(raw).hexdigest()
            self.assertIn(f"`{relative}`, SHA-256 `{digest}`", packet)


if __name__ == "__main__":
    unittest.main()
