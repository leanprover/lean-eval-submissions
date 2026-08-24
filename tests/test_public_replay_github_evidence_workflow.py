import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "public-replay-github-evidence.yml"
).read_text(encoding="utf-8")


class PublicReplayGitHubEvidenceWorkflowTests(unittest.TestCase):
    def test_is_manual_source_free_and_exact_tag_bound(self) -> None:
        self.assertIn("workflow_dispatch:", WORKFLOW)
        self.assertNotIn("pull_request:", WORKFLOW)
        self.assertNotIn("push:", WORKFLOW)
        self.assertIn('test "$GITHUB_SHA" = "$EXPECTED_COMMIT"', WORKFLOW)
        self.assertIn(
            'test "$GITHUB_REF" = "refs/tags/lean-eval-dispatch/$EXPECTED_COMMIT"',
            WORKFLOW,
        )
        self.assertIn('persist-credentials: false', WORKFLOW)
        self.assertIn(
            'git merge-base --is-ancestor "$EXPECTED_COMMIT" origin/main', WORKFLOW
        )
        self.assertNotIn("id-token: write", WORKFLOW)
        self.assertIn(
            'test "$GITHUB_REPOSITORY" = "leanprover/lean-eval-submissions"',
            WORKFLOW,
        )

    def test_has_only_read_permissions_and_no_secret_inputs(self) -> None:
        permissions = WORKFLOW.split("permissions:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(
            permissions,
            "  actions: read\n  contents: read\n  issues: read",
        )
        self.assertNotIn("secrets.", WORKFLOW)
        self.assertNotIn("AWS_", WORKFLOW)
        self.assertNotIn("CLOUDFLARE_", WORKFLOW)
        self.assertNotIn("--api-root", WORKFLOW)

    def test_tokens_are_scoped_to_their_bounded_consumer_steps(self) -> None:
        self.assertEqual(WORKFLOW.count("GITHUB_TOKEN:"), 1)
        self.assertEqual(WORKFLOW.count("GH_TOKEN:"), 1)
        reachability_step = WORKFLOW.split(
            "      - name: Require the selected commit to be reachable", 1
        )[1].split("\n      - ", 1)[0]
        self.assertIn("GH_TOKEN:", reachability_step)
        self.assertIn("gh api", reachability_step)
        token_step = WORKFLOW.split(
            "      - name: Resolve only bounded public GitHub evidence", 1
        )[1].split("\n      - name:", 1)[0]
        self.assertIn("GITHUB_TOKEN:", token_step)
        self.assertIn("resolve_public_replay_github_evidence.py", token_step)
        self.assertNotIn("curl ", token_step)
        self.assertNotIn("gh ", token_step)
        self.assertNotIn("git ", token_step)

    def test_recomputes_and_binds_both_input_artifacts(self) -> None:
        self.assertEqual(WORKFLOW.count("inventory_historical_replay.py"), 1)
        self.assertEqual(WORKFLOW.count("prepare_public_replay_resolution.py"), 2)
        self.assertIn('cmp "$requests" "$recomputed"', WORKFLOW)
        for name in (
            "EXPECTED_INVENTORY_SHA256",
            "EXPECTED_REQUESTS_SHA256",
            "EXPECTED_RESULTS_STORE_SHA256",
            "EXPECTED_RESULT_COUNT",
            "EXPECTED_PUBLIC_RESULT_COUNT",
            "EXPECTED_REQUEST_COUNT",
            "EXPECTED_SHARD_COUNT",
            "EXPECTED_SHARD_INDEX",
            "EXPECTED_SHARD_REQUEST_COUNT",
            "EXPECTED_SHARD_RESULT_COUNT",
        ):
            self.assertIn(name, WORKFLOW)

    def test_executes_one_reviewed_rate_bounded_shard(self) -> None:
        self.assertIn('--shard-index "$EXPECTED_SHARD_INDEX"', WORKFLOW)
        self.assertIn('--shard-count "$EXPECTED_SHARD_COUNT"', WORKFLOW)
        self.assertIn(
            '"shard_request_count": '
            'int(os.environ["EXPECTED_SHARD_REQUEST_COUNT"])',
            WORKFLOW,
        )
        self.assertIn("validate_evidence(", WORKFLOW)
        self.assertIn("registry_raw", WORKFLOW)
        self.assertNotIn("strategy:\n      matrix:", WORKFLOW)

    def test_verifies_live_main_is_still_protected(self) -> None:
        self.assertIn('"repos/$GITHUB_REPOSITORY/branches/main"', WORKFLOW)
        self.assertIn("[.name, .commit.sha, .protected]", WORKFLOW)
        self.assertIn("$'\\ttrue'", WORKFLOW)
        self.assertIn("timeout 30s git ls-remote", WORKFLOW)
        self.assertIn('"$tag_ref^{}"', WORKFLOW)

    def test_uploads_only_the_sanitized_projection(self) -> None:
        upload = WORKFLOW.split(
            "      - name: Upload only the sanitized public evidence projection", 1
        )[1].split("\n      - name:", 1)[0]
        self.assertIn("${{ steps.evidence.outputs.evidence }}", upload)
        self.assertNotIn("requests.outputs.requests", upload)
        self.assertNotRegex(upload, re.compile(r"inventory\.json|requests\.json"))
        self.assertIn("missing evidence remains pending", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
