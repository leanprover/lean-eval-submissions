import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "historical-public-replay-plan.yml"
).read_text(encoding="utf-8")


class HistoricalPublicReplayPlanWorkflowTests(unittest.TestCase):
    def test_dispatch_is_immutable_and_source_free(self) -> None:
        self.assertIn("refs/tags/lean-eval-dispatch/$EXPECTED_COMMIT", WORKFLOW)
        self.assertIn("git merge-base --is-ancestor", WORKFLOW)
        self.assertIn("^evidence/historical-public-replay-github-evidence-", WORKFLOW)
        self.assertIn("persist-credentials: false", WORKFLOW)
        self.assertNotIn("AWS_", WORKFLOW)
        self.assertNotIn("INTAKE_ENABLED", WORKFLOW)
        self.assertNotIn("REPLAY_ENABLED", WORKFLOW)

    def test_plan_is_recomputed_and_remains_blocked(self) -> None:
        self.assertEqual(WORKFLOW.count("prepare_public_replay_plan.py"), 1)
        self.assertIn('for output in "$plan" "$recomputed"', WORKFLOW)
        self.assertIn('cmp "$plan" "$recomputed"', WORKFLOW)
        self.assertIn('value["activation_status"] == "blocked"', WORKFLOW)
        self.assertIn("legacy_public_result_replay_authority_v1", WORKFLOW)
        self.assertIn('has("legacy_adjudication_registry_sha256")', WORKFLOW)
        self.assertEqual(WORKFLOW.count('"${legacy_registry_args[@]}"'), 2)
        self.assertEqual(
            WORKFLOW.count(
                "configuration/public-replay-legacy-adjudications-v1.json"
            ),
            1,
        )

    def test_only_source_free_plan_artifacts_are_uploaded(self) -> None:
        upload = WORKFLOW.split("Upload source-free blocked replay seed plan", 1)[1]
        self.assertIn("${{ env.plan_path }}", upload)
        self.assertIn("${{ env.toolchain_path }}", upload)
        self.assertNotIn("results-source", upload)
        self.assertNotIn("benchmark-history", upload)


if __name__ == "__main__":
    unittest.main()
