"""Closed ordinary State source admission, including old rollback target isolation."""
from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import validate_cloudflare_rollback as rollback

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD = "e2dd6314f04df175966ff105709b855053949d97"
NEW = "351b1c409290516bfa379fc857908908e1d08932"


class OrdinaryStateSourceTests(unittest.TestCase):
    def qualification(self):
        return json.loads((ROOT / ".audit/cloudflare-rollback-qualification-v1.json").read_text())

    def root_tree(self, anchor, tree="2" * 40):
        entry = rollback.REVIEWED_STATE_SOURCE_CONTRACTS[anchor]
        return {"sha": tree, "truncated": False, "tree": [
            {"path": path, **value} for path, value in entry["root_entries"].items()
        ]}

    def test_new_target_selects_both_exact_roots_and_rejects_unknown_or_mixed(self):
        head, tree = "a" * 40, "2" * 40
        for anchor in (OLD, NEW):
            self.assertEqual(rollback.select_declared_state_contract(
                self.qualification(), ROOT, {"commit": head, "protected": True},
                {"commit": head, "tree": tree}, self.root_tree(anchor, tree),
            ), anchor)
        for corruption in ("mixed", "mode", "duplicate", "truncated"):
            with self.subTest(corruption=corruption):
                current = self.root_tree(NEW)
                if corruption == "mixed":
                    current["tree"][-1]["sha"] = rollback.REVIEWED_STATE_SOURCE_CONTRACTS[OLD]["root_entries"]["scripts"]["sha"]
                elif corruption == "mode":
                    current["tree"][-1]["mode"] = "100644"
                elif corruption == "duplicate":
                    current["tree"].append(copy.deepcopy(current["tree"][0]))
                else:
                    current["truncated"] = True
                with self.assertRaises(rollback.RollbackValidationError):
                    rollback.select_declared_state_contract(
                        self.qualification(), ROOT, {"commit": head, "protected": True},
                        {"commit": head, "tree": tree}, current,
                    )

    def test_unknown_map_or_changed_capability_floor_cannot_qualify(self):
        for corruption in ("extra", "digest", "floor"):
            q = self.qualification()
            if corruption == "extra":
                q["supported_state_contracts"]["f" * 40] = copy.deepcopy(q["supported_state_contracts"][NEW])
            elif corruption == "digest":
                q["supported_state_contracts"][NEW]["event_schema_sha256"] = "0" * 64
            else:
                q["state_main_commit"] = NEW
            with self.subTest(corruption=corruption), self.assertRaises(rollback.RollbackValidationError):
                rollback._validate_qualification_header(q, ROOT)

    def test_legacy_target_never_inherits_the_new_contract(self):
        q = self.qualification()
        q.pop("supported_state_contracts")
        self.assertEqual(set(rollback._qualification_state_contracts(q)), {OLD})
        with self.assertRaisesRegex(rollback.RollbackValidationError, "does not support"):
            rollback._validate_descendant_qualification(
                q, ROOT, {"commit": NEW, "protected": True}, {},
                {"commit": NEW, "tree": "2" * 40}, {}, {}, {}, {},
            )

    def test_promotion_readiness_uses_the_target_map_and_keeps_exact_legacy_mode(self):
        for anchor in (OLD, NEW):
            entry = rollback.REVIEWED_STATE_SOURCE_CONTRACTS[anchor]
            body = {
                "environment": "production", "intake_configured_enabled": False,
                "intake_effective_enabled": False, "intake_enabled": False,
                "intake_enablement_mode": "disabled", "intake_lease_expires_at": None,
                "state_branch_protected": True, "state_commit": "a" * 40,
                "state_contract_commit": anchor, "state_contract_verified": True,
                "state_event_schema_sha256": entry["event_schema_sha256"],
                "status": "state_writer_ready",
            }
            with tempfile.TemporaryDirectory() as temporary:
                response = pathlib.Path(temporary) / "response.json"
                response.write_text(json.dumps(body))
                args = ["validator", "production-state-readiness", "--response", str(response),
                        "--http-status", "200", "--qualification", str(ROOT / ".audit/cloudflare-rollback-qualification-v1.json"),
                        "--target-root", str(ROOT)]
                output = io.StringIO()
                with mock.patch.object(sys, "argv", args), contextlib.redirect_stdout(output):
                    self.assertEqual(rollback.main(), 0)
                self.assertEqual(output.getvalue(), "a" * 40 + "\n")
                body["state_event_schema_sha256"] = "0" * 64
                response.write_text(json.dumps(body))
                with mock.patch.object(sys, "argv", args), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(rollback.main(), 1)


class ExactStateSourceIntegrationTests(unittest.TestCase):
    def test_old_new_actual_code_schema_and_target_admission(self):
        source = os.environ.get("LEAN_EVAL_PINNED_STATE_ROOT")
        if source is None:
            self.skipTest("set LEAN_EVAL_PINNED_STATE_ROOT for exact reviewed source integration")
        q = OrdinaryStateSourceTests().qualification()
        state = pathlib.Path(source)
        def git(*args):
            return subprocess.check_output(["git", "-C", str(state), *args], stderr=subprocess.PIPE, timeout=30)
        for anchor in (OLD, NEW):
            tree = git("rev-parse", anchor + "^{tree}").decode().strip()
            entries = []
            for row in git("ls-tree", anchor, "--", "README.md", "docs", "schema", "scripts").decode().splitlines():
                mode, kind, sha, path = row.split()
                entries.append({"mode": mode, "type": kind, "sha": sha, "path": path})
            roots = {"sha": tree, "truncated": False, "tree": entries}
            schema = git("show", anchor + ":schema/state-event-v1.schema.json")
            schema_blob = git("rev-parse", anchor + ":schema/state-event-v1.schema.json").decode().strip()
            proof = {"content": base64.b64encode(schema).decode(), "encoding": "base64",
                     "path": "schema/state-event-v1.schema.json", "sha": schema_blob, "type": "file",
                     "url": f"https://api.github.com/repos/leanprover/lean-eval-state/contents/schema/state-event-v1.schema.json?ref={anchor}"}
            actual = rollback._validate_descendant_qualification(
                q, ROOT, {"commit": anchor, "protected": True},
                {"base_commit": anchor, "merge_base_commit": anchor, "status": "identical",
                 "url": f"https://api.github.com/repos/leanprover/lean-eval-state/compare/{anchor}...{anchor}"},
                {"commit": anchor, "tree": tree}, {"commit": anchor, "tree": tree},
                roots, roots, proof,
            )
            self.assertEqual(actual["contract_commit"], anchor)
            self.assertEqual(actual["sha256"], rollback.REVIEWED_STATE_SOURCE_CONTRACTS[anchor]["event_schema_sha256"])
