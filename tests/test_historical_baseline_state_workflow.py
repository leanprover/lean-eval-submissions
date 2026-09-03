from __future__ import annotations

import ast
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/append-historical-baseline-state.yml").read_text(
    encoding="utf-8"
)
REVIEW_HELPER = (
    ROOT / "scripts/review_historical_baseline_state_batch.py"
).read_text(encoding="utf-8")


class HistoricalBaselineStateWorkflowTests(unittest.TestCase):
    def test_dispatch_is_one_fixed_serialized_three_operation_protocol(self) -> None:
        self.assertIn("workflow_dispatch:", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)
        self.assertIn("type: choice", WORKFLOW)
        self.assertIn("          - stage", WORKFLOW)
        self.assertIn("          - promote", WORKFLOW)
        self.assertIn("          - discard-stale", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertIn("environment: replay-production", WORKFLOW)
        self.assertNotIn("branch_name:", WORKFLOW)
        self.assertNotIn("review_branch:", WORKFLOW)
        self.assertGreaterEqual(WORKFLOW.count("historical-baseline-state-v1"), 4)
        for confirmation in (
            "stage-reviewed-historical-baseline",
            "promote-reviewed-historical-baseline",
            "discard-stale-historical-baseline",
        ):
            self.assertIn(confirmation, WORKFLOW)

    def test_independent_commit_and_digest_bind_before_state_checkout(self) -> None:
        guard = WORKFLOW.index(
            "Validate the independent review binding before selecting a State ref"
        )
        checkout = WORKFLOW.index("Check out current production State")
        fetch = WORKFLOW.index("git -C state fetch --no-tags origin")
        self.assertLess(guard, checkout)
        self.assertLess(checkout, fetch)
        self.assertIn("expected_staged_state_commit:", WORKFLOW)
        self.assertIn("expected_binding_sha256:", WORKFLOW)
        self.assertIn('[[ "$EXPECTED_STAGED_STATE_COMMIT" =~ ^[0-9a-f]{40}$ ]]', WORKFLOW)
        self.assertIn('[[ "$EXPECTED_BINDING_SHA256" =~ ^[0-9a-f]{64}$ ]]', WORKFLOW)
        guarded = WORKFLOW[guard:checkout]
        self.assertIn('sha256sum "$binding"', guarded)
        self.assertIn(".state.candidate_commit", guarded)
        self.assertIn('"$EXPECTED_STAGED_STATE_COMMIT"', guarded)
        self.assertIn('git rev-parse "$GITHUB_SHA:$binding"', guarded)

    def test_exact_implementation_closure_is_verified_before_pip_or_python(self) -> None:
        guard = WORKFLOW.index(
            "Validate the independent review binding before selecting a State ref"
        )
        checkout = WORKFLOW.index("Check out current production State")
        install = WORKFLOW.index("Install exact State validation dependency")
        guarded = WORKFLOW[guard:checkout]
        self.assertLess(guard, install)
        self.assertNotIn("python ", guarded)
        self.assertIn(".implementation.repository", guarded)
        self.assertIn('git merge-base --is-ancestor "$implementation_commit"', guarded)
        self.assertIn("expected_paths=(", guarded)
        self.assertIn("mapfile -t bound_paths", guarded)
        self.assertIn('test "${#bound_paths[@]}" -eq "${#expected_paths[@]}"', guarded)
        self.assertIn('git ls-files --error-unmatch -- "$path"', guarded)
        self.assertIn('git rev-parse "$GITHUB_SHA:$path"', guarded)
        self.assertIn('git rev-parse "$implementation_commit:$path"', guarded)
        self.assertIn('sha256sum "$path"', guarded)
        expected_paths = (
            ".github/workflows/append-historical-baseline-state.yml",
            "configuration/historical-baseline-state-batch-v1.json",
            "requirements-jsonschema-workflow.txt",
            "schemas/historical-private-profile-qualification-v1.schema.json",
            "schemas/historical-private-replay-plan-v1.schema.json",
            "schemas/historical-public-profile-qualification-v1.schema.json",
            "schemas/replay-execution-profile-v1.schema.json",
            "scripts/build_result_receipt.py",
            "scripts/classify_historical_private_archives.py",
            "scripts/historical_public_runner.py",
            "scripts/historical_replay_controller.py",
            "scripts/inventory_historical_replay.py",
            "scripts/key_capability_contract.py",
            "scripts/migrate_archive_envelopes.py",
            "scripts/prepare_historical_baseline_state_batch.py",
            "scripts/prepare_historical_private_replay.py",
            "scripts/prepare_historical_public_authority.py",
            "scripts/replay_orchestrator.py",
            "scripts/results_schema.py",
            "scripts/review_historical_baseline_state_batch.py",
        )
        for path in expected_paths:
            self.assertIn(f"            {path}\n", guarded)
        helper_tree = ast.parse(REVIEW_HELPER)
        helper_paths = next(
            ast.literal_eval(node.value)
            for node in helper_tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "IMPLEMENTATION_PATHS"
                for target in node.targets
            )
        )
        self.assertEqual(tuple(sorted(helper_paths)), expected_paths)
        from tests.test_review_historical_baseline_state_batch import (
            ReviewHistoricalBaselineStateBatchTests,
        )

        fixture = ReviewHistoricalBaselineStateBatchTests()
        with tempfile.TemporaryDirectory() as directory:
            _, _, output = fixture.stage(directory)
            staged_binding = json.loads(output.read_text(encoding="utf-8"))
            staged_paths = tuple(
                sorted(item["path"] for item in staged_binding["implementation"]["blobs"])
            )
            self.assertEqual(staged_paths, expected_paths)

    def test_stage_binds_exact_heads_builds_2439_and_creates_only_fixed_branch(self) -> None:
        stage_bind = WORKFLOW.index("Bind the exact stage parents")
        construct = WORKFLOW.index("prepare_historical_public_authority.py finalize-batch")
        private = WORKFLOW.index("prepare_historical_private_replay.py state-events")
        close = WORKFLOW.index("prepare_historical_baseline_state_batch.py", private)
        review = WORKFLOW.index("review_historical_baseline_state_batch.py stage")
        push = WORKFLOW.index("Push only the absent fixed review branch")
        self.assertLess(stage_bind, construct)
        self.assertLess(construct, private)
        self.assertLess(private, close)
        self.assertLess(close, review)
        self.assertLess(review, push)
        self.assertIn("existing_review=$(git -C state ls-remote", WORKFLOW)
        self.assertIn('test -z "$existing_review"', WORKFLOW)
        self.assertNotIn('test -z "$(git -C state ls-remote', WORKFLOW)
        self.assertNotIn(
            "test ! -e configuration/historical-baseline-state-promotion-v1.json",
            WORKFLOW,
        )
        self.assertIn('--audit-head "$EXPECTED_AUDIT_HEAD"', WORKFLOW)
        self.assertIn('--audit-tree "$audit_tree"', WORKFLOW)
        self.assertGreaterEqual(WORKFLOW.count("2439"), 3)
        self.assertIn("review_historical_baseline_state_batch.py verify", WORKFLOW)
        self.assertIn('"HEAD:refs/heads/$REVIEW_BRANCH"', WORKFLOW)
        self.assertIn('"--force-with-lease=refs/heads/$REVIEW_BRANCH:"', WORKFLOW)
        stage_push = WORKFLOW[push : WORKFLOW.index("Remove review scratch", push)]
        self.assertIn(
            '"--force-with-lease=refs/heads/$REVIEW_BRANCH:" || true',
            stage_push,
        )
        self.assertIn("readback_review=$(git -C state ls-remote --exit-code", stage_push)
        self.assertIn('test "$readback_review" = "$candidate"', stage_push)
        self.assertNotIn("publish_replay_state_batch", WORKFLOW)

    def test_stage_logs_then_uploads_binding_before_branch_mutation(self) -> None:
        summary = WORKFLOW.index("Record the compact source-free review summary")
        upload = WORKFLOW.index("Upload the source-free promotion binding")
        push = WORKFLOW.index("Push only the absent fixed review branch")
        self.assertLess(summary, upload)
        self.assertLess(upload, push)
        self.assertIn("$GITHUB_STEP_SUMMARY", WORKFLOW)
        self.assertIn("candidate source: excluded", WORKFLOW)
        self.assertIn("actions/upload-artifact@043fb46d", WORKFLOW)
        self.assertIn("retention-days: 1", WORKFLOW)
        artifact = WORKFLOW[upload:push]
        self.assertNotIn("combined-candidate", artifact)
        self.assertNotIn("public-candidate", artifact)
        self.assertNotIn("private-candidate", artifact)

    def test_promote_rederives_then_nonforce_cas_pushes_and_reads_back_tree(self) -> None:
        verify = WORKFLOW.index("Fully rederive the committed promotion binding")
        promote = WORKFLOW.index("Fast-forward protected State")
        delete = WORKFLOW.index(
            'git -C state push "$remote" ":refs/heads/$REVIEW_BRANCH"', promote
        )
        readback_tree = WORKFLOW.index(
            'rev-parse "$EXPECTED_STAGED_STATE_COMMIT^{tree}"', promote
        )
        self.assertLess(verify, promote)
        self.assertLess(readback_tree, delete)
        promote_block = WORKFLOW[promote : WORKFLOW.index("Delete only a stale", promote)]
        self.assertIn('"$EXPECTED_STAGED_STATE_COMMIT:refs/heads/main"', promote_block)
        main_push = promote_block.split(
            '"$EXPECTED_STAGED_STATE_COMMIT:refs/heads/main"', 1
        )[1].split("else", 1)[0]
        self.assertNotIn("--force", main_push)
        self.assertGreaterEqual(
            promote_block.count("merge-base --is-ancestor"), 3
        )
        self.assertIn(
            '"--force-with-lease=refs/heads/$REVIEW_BRANCH:$EXPECTED_STAGED_STATE_COMMIT"',
            promote_block,
        )
        delete_command = WORKFLOW[delete : delete + 300]
        self.assertIn("|| true", delete_command)
        self.assertIn("remaining_review=$(git -C state ls-remote", promote_block)
        self.assertIn('test -z "$remaining_review"', promote_block)
        self.assertIn(
            'rev-parse "$EXPECTED_STAGED_STATE_COMMIT^{tree}"', promote_block
        )
        audit_recheck = WORKFLOW[
            WORKFLOW.index("Recheck exact audit authority immediately before promotion") : promote
        ]
        self.assertIn('merge-base --is-ancestor "$audit_head" "$current"', audit_recheck)
        self.assertIn('rev-parse "$audit_head^{tree}"', audit_recheck)

    def test_discard_fully_verifies_staleness_and_preserves_main(self) -> None:
        verify = WORKFLOW.index("Fully rederive the committed promotion binding")
        discard = WORKFLOW.index("Delete only a stale exact review lease")
        self.assertLess(verify, discard)
        block = WORKFLOW[discard : WORKFLOW.index("Record the compact", discard)]
        self.assertIn('test "$main_before" != "$parent"', block)
        self.assertIn('refs/heads/$REVIEW_BRANCH" | cut -f1)', block)
        self.assertIn(
            '"--force-with-lease=refs/heads/$REVIEW_BRANCH:$EXPECTED_STAGED_STATE_COMMIT"',
            block,
        )
        delete = block.index(
            'git -C state push "$remote" ":refs/heads/$REVIEW_BRANCH"'
        )
        self.assertIn("|| true", block[delete : delete + 300])
        self.assertIn("remaining_review=$(git -C state ls-remote", block)
        self.assertIn('test -z "$remaining_review"', block)
        self.assertIn('"$main_before"', block)

    def test_state_write_key_is_ram_backed_unset_and_trap_removed(self) -> None:
        secret = "STATE_WRITE_KEY: ${{ secrets.PRODUCTION_STATE_WRITE_KEY }}"
        self.assertEqual(WORKFLOW.count(secret), 3)
        self.assertEqual(
            WORKFLOW.count("mktemp /dev/shm/lean-eval-baseline-state-writer."), 3
        )
        self.assertEqual(WORKFLOW.count("unset STATE_WRITE_KEY GIT_SSH_COMMAND"), 3)
        self.assertGreaterEqual(WORKFLOW.count("trap cleanup EXIT"), 7)
        self.assertGreaterEqual(WORKFLOW.count("StrictHostKeyChecking=yes"), 3)
        self.assertGreaterEqual(WORKFLOW.count("AAAAC3NzaC1lZDI1NTE5AAAAIOM"), 3)
        cleanup = WORKFLOW[WORKFLOW.index("Remove review scratch") :]
        self.assertIn("rm -rf -- state audit public-state-contract", cleanup)

    def test_private_remote_reads_have_explicit_ephemeral_credentials(self) -> None:
        stage = WORKFLOW[
            WORKFLOW.index("Bind the exact stage parents") :
            WORKFLOW.index("Validate committed binding before fetching")
        ]
        self.assertIn("AUDIT_READ_KEY: ${{ secrets.AUDIT_READ_KEY }}", stage)
        self.assertIn(
            "PRODUCTION_STATE_READ_KEY: ${{ secrets.PRODUCTION_STATE_READ_KEY }}",
            stage,
        )
        self.assertIn('GIT_SSH_COMMAND="$state_ssh" git -C state ls-remote', stage)
        self.assertIn('GIT_SSH_COMMAND="$audit_ssh" git -C audit ls-remote', stage)
        self.assertIn("unset PRODUCTION_STATE_READ_KEY AUDIT_READ_KEY", stage)

        state = WORKFLOW[
            WORKFLOW.index("Validate committed binding before fetching") :
            WORKFLOW.index("Validate the bound immutable audit authority")
        ]
        self.assertIn(
            "PRODUCTION_STATE_READ_KEY: ${{ secrets.PRODUCTION_STATE_READ_KEY }}",
            state,
        )
        self.assertIn("export GIT_SSH_COMMAND", state)
        self.assertIn("git -C state ls-remote", state)
        self.assertIn("git -C state fetch", state)
        self.assertIn("unset PRODUCTION_STATE_READ_KEY GIT_SSH_COMMAND", state)

        for start, end in (
            ("Validate the bound immutable audit authority", "Normalize exact offline"),
            ("Recheck exact audit authority", "Fast-forward protected State"),
        ):
            audit = WORKFLOW[WORKFLOW.index(start) : WORKFLOW.index(end)]
            self.assertIn("AUDIT_READ_KEY: ${{ secrets.AUDIT_READ_KEY }}", audit)
            self.assertIn("export GIT_SSH_COMMAND", audit)
            self.assertIn("git -C audit fetch", audit)
            self.assertIn("unset AUDIT_READ_KEY GIT_SSH_COMMAND", audit)

        cleanup = WORKFLOW[WORKFLOW.index("Remove review scratch") :]
        for pattern in (
            "lean-eval-baseline-state-reader.*",
            "lean-eval-baseline-audit-reader.*",
            "lean-eval-baseline-read-known-hosts.*",
        ):
            self.assertIn(pattern, cleanup)


if __name__ == "__main__":
    unittest.main()
