from __future__ import annotations

import importlib.util
import json
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts" / "run_bounded_staging_lifecycle.py"
WATCHDOG = ROOT / ".github" / "workflows" / "bounded-staging-lifecycle-watchdog.yml"
FIXTURE = ROOT / "configuration" / "staging-lifecycle-smoke-v1.json"
RUNBOOK = ROOT / "docs" / "overhaul-rollout-runbook.md"


def load_driver():
    spec = importlib.util.spec_from_file_location("bounded_staging_driver", DRIVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BoundedStagingLifecycleAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.driver_text = DRIVER.read_text(encoding="utf-8")
        cls.watchdog = WATCHDOG.read_text(encoding="utf-8")
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.runbook = RUNBOOK.read_text(encoding="utf-8")
        cls.driver = load_driver()

    def test_watchdog_is_manual_exact_tag_staging_only_and_all_false(self) -> None:
        self.assertIn("workflow_dispatch:", self.watchdog)
        self.assertIn("environment: cloudflare-staging", self.watchdog)
        self.assertNotIn("cloudflare-production", self.watchdog)
        self.assertNotIn("AWS_", self.watchdog)
        self.assertIn("refs/tags/lean-eval-dispatch/$EXPECTED_COMMIT", self.watchdog)
        self.assertIn(
            "window_minutes must be an integer from 15 through 90", self.watchdog
        )
        self.assertIn(
            "if: always() && steps.armed.outputs.ready == 'true'", self.watchdog
        )
        for variable in (
            "INTAKE_ENABLED",
            "LEGACY_RESULT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_OWNER_API_ENABLED",
            "RESULT_AMENDMENT_MAINTAINER_API_ENABLED",
            "MODEL_IDENTITY_OWNER_API_ENABLED",
            "MODEL_IDENTITY_MAINTAINER_API_ENABLED",
            "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED",
            "RELEASE_OPT_OUT_API_ENABLED",
        ):
            self.assertIn(f'--var "{variable}:false"', self.watchdog)
        self.assertIn(
            "watchdog could not verify all-false staging recovery", self.watchdog
        )

    def test_driver_has_closed_authority_and_no_secret_output_channel(self) -> None:
        bounded = self.fixture["bounded_acceptance"]
        self.assertEqual(
            set(bounded),
            {
                "submission_base_url",
                "state_repository",
                "state_branch",
                "results_repository",
                "results_branch",
                "release_repository",
                "release_workflow",
                "release_ref",
                "release_commit",
                "fixture_gist_file",
                "external_mutation_confirmation",
                "retire_after",
            },
        )
        self.assertEqual(
            bounded["release_workflow"], "credentialed-release-staging-smoke.yml"
        )
        self.assertRegex(bounded["release_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(bounded["release_ref"], "main")
        self.assertEqual(bounded["fixture_gist_file"], "lean-eval-proof.txt")
        self.assertEqual(
            bounded["external_mutation_confirmation"],
            "APPROVE_EXACT_STAGING_FIXTURE_GIST_AND_TAG",
        )
        self.assertNotIn("upload-artifact", self.driver_text)
        self.assertNotIn("GITHUB_STEP_SUMMARY", self.driver_text)
        self.assertNotIn("print(challenge", self.driver_text)
        self.assertNotIn("print(token", self.driver_text)
        self.assertIn(
            "generated source tag already exists; refusing to move or reuse it",
            self.driver_text,
        )
        self.assertIn("proof gist must be secret", self.driver_text)
        self.assertIn(
            "gh must be authenticated as the exact fixture owner", self.driver_text
        )
        self.assertIn("finally:\n            # The Actions watchdog", self.driver_text)
        self.assertIn("dispatch_disable(args.expected_commit)", self.driver_text)

    def test_wrong_external_confirmation_performs_no_write(self) -> None:
        with (
            mock.patch.object(self.driver, "gh_json") as github,
            self.assertRaisesRegex(self.driver.AcceptanceError, "confirmation"),
        ):
            self.driver.update_exact_proof_and_tag(
                self.fixture,
                "a" * 20,
                "signed-secret",
                "lean-eval/019debcf-cb48-7000-8000-000000000001",
                "NO",
            )
        github.assert_not_called()

    def test_preflight_uses_only_read_only_github_calls(self) -> None:
        bounded = self.fixture["bounded_acceptance"]

        def github_response(args, *, method="GET", fields=None):
            self.assertEqual(method, "GET")
            self.assertIsNone(fields)
            endpoint = args[0]
            if endpoint == "user":
                return {"login": "kim-em"}
            if endpoint.startswith("gists/"):
                return {"owner": {"login": "kim-em"}, "public": False}
            if endpoint.endswith("/commits/" + self.fixture["source"]["commit"]):
                return {"sha": self.fixture["source"]["commit"]}
            if endpoint.endswith("/commits/main"):
                return {"sha": bounded["release_commit"]}
            if "/branches/" in endpoint:
                return {"protected": True}
            self.fail(f"unexpected GitHub preflight request: {endpoint}")

        with (
            mock.patch.object(
                self.driver, "gh_json", side_effect=github_response
            ) as github,
            mock.patch.object(self.driver, "health", return_value=False) as health,
        ):
            self.driver.fixture_preflight(
                self.fixture,
                "a" * 20,
                "b" * 40,
            )
        self.assertGreaterEqual(github.call_count, 7)
        health.assert_called_once()

    def test_driver_covers_only_completion_plan_launch_cases(self) -> None:
        for route in (
            "/api/v1/agent/challenges",
            "/api/v1/agent/submissions",
            "/api/v1/results/claims",
            "/metadata",
            "/problem-repairs",
            "/decisions",
            "/api/v1/model-identities",
            "/aliases",
            "/name",
        ):
            self.assertIn(route, self.driver_text)
        self.assertNotIn("/consolidations", self.driver_text)
        self.assertNotIn("disproof", self.driver_text.lower())
        self.assertNotIn("formal-conjectures", self.driver_text.lower())
        self.assertIn(
            'archive.get("archive_repository") != "leanprover/lean-eval-audit"',
            self.driver_text,
        )
        self.assertIn('sidecar.get("schema_version") != 3', self.driver_text)
        self.assertIn('publication_choice"] != "withheld"', self.driver_text)
        self.assertIn("release-queue.json", self.driver_text)
        self.assertIn("staging Result leaked source material", self.driver_text)

    def test_fixture_and_runbook_require_retirement_and_no_devtools(self) -> None:
        self.assertEqual(
            self.fixture["bounded_acceptance"]["retire_after"],
            "one accepted bounded staging lifecycle run",
        )
        self.assertIn(
            "must be deleted with the staging fixture after one accepted\nrun",
            self.runbook,
        )
        self.assertIn("no browser cookie, token, or DevTools value", self.runbook)
        self.assertIn("read-only and must report zero", self.runbook)
        self.assertIn("publication-disabled", self.runbook)

    def test_local_uuid7_idempotency_keys_are_canonical_and_not_future_dated(
        self,
    ) -> None:
        import time

        before = int(time.time() * 1000)
        value = self.driver.event_id()
        after = int(time.time() * 1000)
        self.assertRegex(value, self.driver.UUID7)
        timestamp = int(value[:8] + value[9:13], 16)
        self.assertGreaterEqual(timestamp, before)
        self.assertLessEqual(timestamp, after)


if __name__ == "__main__":
    unittest.main()
