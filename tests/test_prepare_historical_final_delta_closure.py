from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from prepare_historical_final_delta_closure import (
    TEMPORARY_WORKFLOWS,
    ClosureError,
    _candidate_inventory,
    build_activation,
    build_terminal,
    canonical,
    digest_set,
    locator,
)


class ClosureFixture:
    public_result = "r2_" + "1" * 64
    private_result = "r2_" + "2" * 64
    public_task = "rt1_" + "3" * 64

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "test"], check=True)
        subprocess.run(
            ["git", "-C", root, "config", "user.email", "test@example.com"], check=True
        )
        scripts = root / "scripts"
        scripts.mkdir()
        (scripts / "state.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        (root / "state.json").write_text(
            '{"environment":"production"}\n', encoding="utf-8"
        )
        self.commit("candidate")
        self.candidate = self.head()
        self.candidate_tree = self.tree()

    def commit(self, message: str) -> None:
        subprocess.run(["git", "-C", self.root, "add", "."], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-q", "-m", message], check=True
        )

    def head(self) -> str:
        return subprocess.run(
            ["git", "-C", self.root, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def tree(self) -> str:
        return subprocess.run(
            ["git", "-C", self.root, "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def event(self, identity: str, value: dict) -> None:
        path = self.root / "events" / identity[:2] / f"{identity}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(value))

    def terminal(
        self, *, result_id: str | None = None, include_terminal: bool = True
    ) -> str:
        enqueue_id = "01a00000-0000-7000-8000-000000000001"
        self.event(
            enqueue_id,
            {
                "event_id": enqueue_id,
                "event_type": "replay.enqueued",
                "subject_id": self.public_task,
                "payload": {"result_id": result_id or self.public_result},
            },
        )
        if include_terminal:
            terminal_id = "01a00000-0000-7000-8000-000000000002"
            self.event(
                terminal_id,
                {
                    "event_id": terminal_id,
                    "event_type": "replay.accepted",
                    "subject_id": self.public_task,
                    "payload": {},
                },
            )
        self.commit("terminal")
        return self.head()

    def activation(self) -> dict:
        prep = {
            "kind": "historical_final_delta_preparation_packet",
            "cutoff": {
                "baseline_inventory": {
                    "source_commit": "a" * 40,
                    "results_store_sha256": "a" * 64,
                    "inventory_sha256": "b" * 64,
                    "result_count": 1,
                },
                "current_inventory": {
                    "source_commit": "b" * 40,
                    "results_store_sha256": "c" * 64,
                    "inventory_sha256": "d" * 64,
                    "result_count": 3,
                },
                "delta_sha256": "e" * 64,
                "delta_counts": {
                    "result_count": 2,
                    "public_source_probe_pending": 1,
                    "private_archive_migration_pending": 1,
                },
            },
            "entries": [
                {"result_id": self.public_result},
                {"result_id": self.private_result},
            ],
        }
        public = {
            "kind": "historical_final_delta_public_replay_plan",
            "activation_status": "ready",
            "entries": [{"result_id": self.public_result, "disposition": "replayable"}],
        }
        private = {
            "kind": "historical_private_replay_plan",
            "entries": [
                {
                    "result_id": self.private_result,
                    "classification": "archive_not_found",
                }
            ],
        }
        promotion = {
            "kind": "historical_final_delta_state_promotion_binding",
            "review_branch": "historical-final-delta-state-v1",
            "audit": {
                "repository": "leanprover/lean-eval-audit",
                "head": "c" * 40,
                "tree": "d" * 40,
            },
            "state": {
                "repository": "leanprover/lean-eval-state",
                "parent": "a" * 40,
                "parent_tree": "b" * 40,
                "candidate_commit": self.candidate,
                "candidate_tree": self.candidate_tree,
            },
            "candidate": {
                "lanes": {
                    "public": {
                        "task_count": 1,
                        "task_id_set_sha256": digest_set([self.public_task]),
                        "result_id_set_sha256": digest_set([self.public_result]),
                    },
                    "private": {
                        "task_count": 0,
                        "task_id_set_sha256": digest_set([]),
                        "result_id_set_sha256": digest_set([]),
                    },
                }
            },
        }
        commit = "f" * 40
        bound = lambda path: {
            "repository": "leanprover/lean-eval-submissions",
            "commit": commit,
            "path": path,
            "sha256": "0" * 64,
        }
        return build_activation(
            preparation=prep,
            preparation_locator=bound("prep"),
            public_plan=public,
            public_locator=bound("public"),
            private_plan=private,
            private_locator=bound("private"),
            promotion=promotion,
            promotion_locator=bound("promotion"),
            candidate={
                "public": {
                    "task_ids": [self.public_task],
                    "replayable_result_ids": [self.public_result],
                    "unavailable_result_ids": [],
                },
                "private": {
                    "task_ids": [],
                    "replayable_result_ids": [],
                    "unavailable_result_ids": [self.private_result],
                },
            },
        )

    def absence(self) -> dict:
        return {
            "kind": "historical_final_delta_executor_absence",
            "status": "verified_absent",
            "schema_version": 1,
            "checked_at": "2026-09-03T12:00:00Z",
            "submissions_commit": "f" * 40,
            "authority": {
                "repository": "leanprover/lean-eval-submissions",
                "implementation_commit": "f" * 40,
                "workflow_path": ".github/workflows/historical-final-delta-activation.yml",
                "workflow_run_id": 1,
            },
            "audit": {
                "repository": "leanprover/lean-eval-audit",
                "head": "c" * 40,
                "tree": "d" * 40,
            },
            "state": {
                "repository": "leanprover/lean-eval-state",
                "head": self.head(),
                "tree": self.tree(),
            },
            "controller_variables": {
                "HISTORICAL_PRIVATE_REPLAY_CONTROLLER_ENABLED": "absent",
                "HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED": "absent",
            },
            "review_branches": {
                "audit": {
                    "name": "historical-final-delta-archive-rewrap-v1",
                    "status": "absent",
                },
                "state": {
                    "name": "historical-final-delta-state-v1",
                    "status": "absent",
                },
            },
            "executors": {
                "public": {"status": "no_running_task", "state_recovery_kind": "none"},
                "private": {
                    "worker_prefix": "hpr-",
                    "worker_count": 0,
                    "application_prefix": "le-hpr-",
                    "application_count": 0,
                    "inventory_sha256": "1" * 64,
                },
            },
            "readbacks": {
                "github_variables_sha256": "2" * 64,
                "github_review_refs_sha256": "3" * 64,
                "cloudflare_worker_services_sha256": "4" * 64,
                "cloudflare_container_applications_sha256": "5" * 64,
            },
            "temporary_workflows": TEMPORARY_WORKFLOWS,
        }

    def terminal_readback(self, head: str) -> dict:
        return {
            "schema_version": 1,
            "kind": "historical_final_delta_terminal_live_readback",
            "checked_at": "2026-09-03T12:01:00Z",
            "audit": {
                "repository": "leanprover/lean-eval-audit",
                "head": "c" * 40,
                "tree": "d" * 40,
            },
            "state": {
                "repository": "leanprover/lean-eval-state",
                "head": head,
                "tree": subprocess.run(
                    ["git", "-C", self.root, "rev-parse", f"{head}^{{tree}}"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            },
            "controller_variables": {
                "HISTORICAL_PRIVATE_REPLAY_CONTROLLER_ENABLED": "absent",
                "HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED": "absent",
            },
            "review_branches": {
                "audit": {
                    "name": "historical-final-delta-archive-rewrap-v1",
                    "status": "absent",
                },
                "state": {
                    "name": "historical-final-delta-state-v1",
                    "status": "absent",
                },
            },
            "queues": {"private": 0, "public": 0},
            "recovery": {"private": "none", "public": "none"},
            "executors": {
                "private_application_count": 0,
                "private_worker_count": 0,
                "public_running_task_count": 0,
            },
            "readbacks": {
                "cloudflare_container_applications_sha256": "1" * 64,
                "cloudflare_worker_services_sha256": "2" * 64,
                "github_review_refs_sha256": "3" * 64,
                "github_variables_sha256": "4" * 64,
            },
        }

    def build_terminal(self, activation: dict, absence: dict, head: str) -> dict:
        dummy = {
            "repository": "leanprover/lean-eval-submissions",
            "commit": "f" * 40,
            "path": "x",
            "sha256": "0" * 64,
        }
        absence = json.loads(json.dumps(absence))
        absence["state"] = {
            "repository": "leanprover/lean-eval-state",
            "head": head,
            "tree": subprocess.run(
                ["git", "-C", self.root, "rev-parse", f"{head}^{{tree}}"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
        }
        absence = json.loads(json.dumps(absence))
        absence["state"] = {
            "repository": "leanprover/lean-eval-state",
            "head": head,
            "tree": subprocess.run(
                ["git", "-C", self.root, "rev-parse", f"{head}^{{tree}}"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
        }
        return build_terminal(
            activation=activation,
            activation_locator=dummy,
            absence=absence,
            absence_locator=dummy,
            terminal_readback=self.terminal_readback(head),
            state_root=self.root,
            state_head=head,
            audit_head="c" * 40,
            audit_tree="d" * 40,
        )


class FinalDeltaClosureTests(unittest.TestCase):
    def test_candidate_inventory_rejects_every_out_of_scope_diff_status(self) -> None:
        for tamper in ("modify", "delete", "add"):
            with (
                self.subTest(tamper=tamper),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = pathlib.Path(temporary)
                subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
                subprocess.run(
                    ["git", "-C", root, "config", "user.name", "test"], check=True
                )
                subprocess.run(
                    ["git", "-C", root, "config", "user.email", "test@example.com"],
                    check=True,
                )
                (root / "state.json").write_text(
                    '{"environment":"production"}\n', encoding="utf-8"
                )
                (root / "tracked.txt").write_text("original\n", encoding="utf-8")
                subprocess.run(["git", "-C", root, "add", "."], check=True)
                subprocess.run(
                    ["git", "-C", root, "commit", "-q", "-m", "parent"], check=True
                )
                parent = subprocess.run(
                    ["git", "-C", root, "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                qualification_id = "01a00000-0000-7000-8000-000000000001"
                enqueue_id = "01a00000-0000-7000-8000-000000000002"
                for event_id, value in (
                    (
                        qualification_id,
                        {
                            "event_id": qualification_id,
                            "event_type": "historical_result.replay_profile_qualified",
                            "subject_id": "r2_" + "1" * 64,
                        },
                    ),
                    (
                        enqueue_id,
                        {
                            "event_id": enqueue_id,
                            "event_type": "replay.enqueued",
                            "subject_id": "rt1_" + "2" * 64,
                            "causation_event_id": qualification_id,
                            "payload": {"result_id": "r2_" + "1" * 64},
                        },
                    ),
                ):
                    path = root / "events" / "01" / f"{event_id}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(canonical(value))
                if tamper == "modify":
                    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
                elif tamper == "delete":
                    (root / "tracked.txt").unlink()
                else:
                    (root / "extra.txt").write_text("extra\n", encoding="utf-8")
                subprocess.run(["git", "-C", root, "add", "-A"], check=True)
                subprocess.run(
                    ["git", "-C", root, "commit", "-q", "-m", "candidate"],
                    check=True,
                )
                candidate = subprocess.run(
                    ["git", "-C", root, "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                tree = subprocess.run(
                    ["git", "-C", root, "rev-parse", "HEAD^{tree}"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                with self.assertRaisesRegex(ClosureError, "create-only"):
                    _candidate_inventory(
                        root,
                        {
                            "state": {
                                "parent": parent,
                                "candidate_commit": candidate,
                                "candidate_tree": tree,
                            }
                        },
                    )

    def test_terminal_reconciles_exact_accepted_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ClosureFixture(pathlib.Path(temporary))
            terminal = fixture.build_terminal(
                fixture.activation(), fixture.absence(), fixture.terminal()
            )
            for name, value in (
                (
                    "historical-final-delta-activation-v1.schema.json",
                    fixture.activation(),
                ),
                (
                    "historical-final-delta-executor-absence-v1.schema.json",
                    fixture.absence(),
                ),
                ("historical-final-delta-terminal-v1.schema.json", terminal),
            ):
                schema = json.loads(
                    (ROOT / "schemas" / name).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    list(
                        Draft202012Validator(
                            schema, format_checker=FormatChecker()
                        ).iter_errors(value)
                    ),
                    [],
                )
        self.assertEqual(terminal["accepted_result_count"], 2)
        self.assertEqual(terminal["terminal_result_count"], 2)

    def test_tampered_terminal_result_set_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ClosureFixture(pathlib.Path(temporary))
            head = fixture.terminal(result_id="r2_" + "9" * 64)
            with self.assertRaisesRegex(ClosureError, "Results differ"):
                fixture.build_terminal(fixture.activation(), fixture.absence(), head)

    def test_nonancestor_terminal_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ClosureFixture(pathlib.Path(temporary))
            activation = fixture.activation()
            subprocess.run(
                ["git", "-C", fixture.root, "checkout", "-q", "--orphan", "other"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", fixture.root, "rm", "-q", "-rf", "."], check=True
            )
            (fixture.root / "scripts").mkdir()
            (fixture.root / "scripts" / "state.py").write_text(
                "raise SystemExit(0)\n", encoding="utf-8"
            )
            fixture.commit("other")
            with self.assertRaisesRegex(ClosureError, "not an ancestor"):
                fixture.build_terminal(activation, fixture.absence(), fixture.head())

    def test_absence_branch_or_path_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ClosureFixture(pathlib.Path(temporary))
            head = fixture.terminal()
            for mutate in (
                lambda proof: proof["review_branches"]["audit"].update(name="wrong"),
                lambda proof: proof.update(
                    temporary_workflows=TEMPORARY_WORKFLOWS[:-1]
                ),
            ):
                proof = json.loads(json.dumps(fixture.absence()))
                mutate(proof)
                with self.assertRaisesRegex(ClosureError, "absence proof"):
                    fixture.build_terminal(fixture.activation(), proof, head)

    def test_absence_run_authority_or_readback_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ClosureFixture(pathlib.Path(temporary))
            head = fixture.terminal()
            for mutate in (
                lambda proof: proof["authority"].update(workflow_run_id=0),
                lambda proof: proof["authority"].update(workflow_path="wrong"),
                lambda proof: proof["readbacks"].update(
                    github_variables_sha256="not-a-digest"
                ),
            ):
                proof = fixture.absence()
                mutate(proof)
                with self.assertRaisesRegex(ClosureError, "absence proof"):
                    fixture.build_terminal(fixture.activation(), proof, head)

    def test_terminal_rejects_invalid_or_nonabsent_live_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ClosureFixture(pathlib.Path(temporary))
            head = fixture.terminal()
            activation = fixture.activation()
            absence = fixture.absence()
            readback = fixture.terminal_readback(head)
            for mutate in (
                lambda value: value.update(checked_at="not-a-time"),
                lambda value: value["executors"].update(private_worker_count=1),
                lambda value: value["controller_variables"].update(
                    HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED="true"
                ),
            ):
                changed = json.loads(json.dumps(readback))
                mutate(changed)
                dummy = {
                    "repository": "leanprover/lean-eval-submissions",
                    "commit": "f" * 40,
                    "path": "x",
                    "sha256": "0" * 64,
                }
                with self.assertRaisesRegex(ClosureError, "live executor readback"):
                    build_terminal(
                        activation=activation,
                        activation_locator=dummy,
                        absence={
                            **absence,
                            "state": {
                                "repository": "leanprover/lean-eval-state",
                                "head": head,
                                "tree": fixture.tree(),
                            },
                        },
                        absence_locator=dummy,
                        terminal_readback=changed,
                        state_root=fixture.root,
                        state_head=head,
                        audit_head="c" * 40,
                        audit_tree="d" * 40,
                    )

    def test_content_addressed_locator_mismatch_is_rejected(self) -> None:
        raw = canonical({"x": 1})
        with self.assertRaisesRegex(ClosureError, "locator"):
            locator(
                commit="a" * 40,
                path="evidence/public-replay/plans/" + "0" * 64 + ".json",
                raw=raw,
                prefix="evidence/public-replay/plans",
            )


if __name__ == "__main__":
    unittest.main()
