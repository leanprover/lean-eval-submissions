from __future__ import annotations

import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
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
    sys.modules[spec.name] = module
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

    def mutation(self):
        bounded = self.fixture["bounded_acceptance"]
        source = self.fixture["source"]
        return self.driver.FixtureMutation(
            gist_id="a" * 20,
            filename=bounded["fixture_gist_file"],
            repository=source["repository"],
            commit=source["commit"],
            tag="lean-eval/019debcf-cb48-7000-8000-000000000001",
            prior_file_present=False,
            prior_file_content=None,
            prior_gist_head="1" * 40,
        )

    def gist_response(
        self, mutation, head: str, content: str | None
    ) -> dict[str, object]:
        files = (
            {}
            if content is None
            else {mutation.filename: {"truncated": False, "content": content}}
        )
        return {
            "id": mutation.gist_id,
            "public": False,
            "owner": {"login": "kim-em"},
            "git_pull_url": f"https://gist.github.com/{mutation.gist_id}.git",
            "history": [{"version": head}],
            "files": files,
        }

    def test_watchdog_arms_from_all_false_before_enable_and_has_cleanup_margin(
        self,
    ) -> None:
        self.assertIn("environment: cloudflare-staging", self.watchdog)
        self.assertNotIn("cloudflare-production", self.watchdog)
        self.assertIn("timeout-minutes: 125", self.watchdog)
        initial = self.watchdog.index("Verify exact tag and initial all-false")
        install = self.watchdog.index("npm ci")
        armed = self.watchdog.index("Arm all-false recovery")
        enabled = self.watchdog.index("Wait for the packet-bound enabled state")
        hold = self.watchdog.index("Hold the bounded acceptance window")
        self.assertLess(initial, install)
        self.assertLess(install, armed)
        self.assertLess(armed, enabled)
        self.assertLess(enabled, hold)
        self.assertIn(
            "if: always() && steps.armed.outputs.ready == 'true'", self.watchdog
        )
        self.assertIn(
            "activation_timeout_minutes must be an integer from 5 through 15",
            self.watchdog,
        )
        self.assertIn(
            "window_minutes must be an integer from 15 through 90", self.watchdog
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

    def test_fixture_is_canonical_closed_and_hash_pinned(self) -> None:
        bounded = self.fixture["bounded_acceptance"]
        self.assertEqual(
            set(bounded),
            {
                "submission_base_url",
                "state_repository",
                "state_branch",
                "state_contract_commit",
                "state_script_sha256",
                "results_repository",
                "results_branch",
                "release_repository",
                "release_workflow",
                "release_ref",
                "release_commit",
                "release_run_name_prefix",
                "fixture_gist_file",
                "retire_after",
            },
        )
        self.assertEqual(
            bounded["release_commit"], "4f3d4cdd11d41e93294ba7821899923375ba360f"
        )
        self.assertEqual(bounded["release_ref"], "main")
        self.assertEqual(
            bounded["state_contract_commit"], "8ae11456f0a439f91ec5822ec36adb93b76b0d96"
        )
        self.assertEqual(len(bounded["state_script_sha256"]), 10)
        self.assertEqual(
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            self.driver.EXPECTED_FIXTURE_SHA256,
        )
        self.assertEqual(
            FIXTURE.read_bytes(),
            (json.dumps(self.fixture, indent=2, sort_keys=False) + "\n").encode(),
        )

    def test_cli_accepts_only_visible_browser_submission_identity(self) -> None:
        help_text = self.driver.parser().format_help()
        self.assertNotIn("--fixture", help_text)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.driver.parser().parse_args(
                [
                    "run",
                    "--expected-commit",
                    "b" * 40,
                    "--gist-id",
                    "a" * 20,
                    "--browser-submission-id",
                    "019debcf-cb48-7000-8000-000000000001",
                    "--browser-result-id",
                    "x",
                ]
            )
        self.assertNotIn("args.browser_result_id", self.driver_text)
        self.assertNotIn("confirm-external-mutations", self.driver_text)
        self.assertIn("browser, browser_result = wait_submission", self.driver_text)

    def test_target_bound_pause_never_prints_challenge(self) -> None:
        mutation = self.mutation()
        output = io.StringIO()
        phrase = (
            f"CONFIRM GIST {mutation.gist_id}/{mutation.filename}@{mutation.prior_gist_head} AND TAG "
            f"{mutation.repository}/refs/tags/{mutation.tag}@{mutation.commit} WITH EXACT CLEANUP"
        )
        with (
            mock.patch("builtins.input", return_value=phrase),
            contextlib.redirect_stdout(output),
        ):
            self.driver.require_target_bound_confirmation(mutation)
        self.assertIn(mutation.describe_targets(), output.getvalue())
        self.assertNotIn("signed-secret", output.getvalue())
        with (
            mock.patch("builtins.input", return_value="NO"),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(self.driver.AcceptanceError, "confirmation"),
        ):
            self.driver.require_target_bound_confirmation(mutation)

    def test_external_404_must_be_exact_and_authenticated(self) -> None:
        def completed(returncode, stdout="", stderr=""):
            return subprocess.CompletedProcess([], returncode, stdout, stderr)

        with mock.patch.object(
            self.driver.subprocess,
            "run",
            return_value=completed(1, stderr="gh: Not Found (HTTP 404)\n"),
        ):
            self.assertIsNone(self.driver.gh_json_or_authenticated_404(["target"]))
        for diagnostic in ("HTTP 404", "gh: Forbidden (HTTP 403)", "network down"):
            with (
                mock.patch.object(
                    self.driver.subprocess,
                    "run",
                    return_value=completed(1, stderr=diagnostic),
                ),
                self.assertRaises(self.driver.AcceptanceError),
            ):
                self.driver.gh_json_or_authenticated_404(["target"])

    def test_unexpected_api_status_never_echoes_secret_response_payload(self) -> None:
        secret = "signed-session-token-must-not-leak"
        response = io.BytesIO(
            json.dumps({"session_token": secret, "challenge": "also-secret"}).encode()
        )
        response.status = 200
        with (
            mock.patch.object(
                self.driver.urllib.request, "urlopen", return_value=response
            ),
            self.assertRaises(self.driver.AcceptanceError) as raised,
        ):
            self.driver.Api("https://submit.test").request(
                "POST", "/api/v1/agent/submissions", {}, expected=202
            )
        diagnostic = str(raised.exception)
        self.assertNotIn(secret, diagnostic)
        self.assertNotIn("also-secret", diagnostic)
        self.assertIn("response redacted", diagnostic)

    def test_exact_tag_response_and_idempotent_owned_cleanup(self) -> None:
        mutation = self.mutation()
        response = {
            "ref": f"refs/tags/{mutation.tag}",
            "node_id": "node",
            "url": "https://api.github.test/ref",
            "object": {
                "sha": mutation.commit,
                "type": "commit",
                "url": "https://api.github.test/commit",
            },
        }
        self.driver.verify_exact_tag_response(
            response, mutation.repository, mutation.tag, mutation.commit
        )
        with self.assertRaises(self.driver.AcceptanceError):
            self.driver.verify_exact_tag_response(
                {**response, "unexpected": True},
                mutation.repository,
                mutation.tag,
                mutation.commit,
            )
        with (
            mock.patch.object(self.driver, "restore_gist") as restore,
            mock.patch.object(self.driver, "remove_created_tag") as remove,
        ):
            self.driver.cleanup_fixture_mutation(mutation, remove_tag=False)
            restore.assert_called_once_with(mutation)
            remove.assert_not_called()
        with mock.patch.object(self.driver, "gh_json") as github:
            self.driver.restore_gist(mutation)
            self.driver.remove_created_tag(mutation)
        github.assert_not_called()

    def test_gist_transport_is_tmpfs_atomic_and_has_no_patch_or_debug_env(self) -> None:
        self.assertIn('["ls-remote", "--symref", "origin", "HEAD"]', self.driver_text)
        self.assertIn('f"--force-with-lease={branch}:', self.driver_text)
        self.assertIn("expected_branch=mutation.gist_branch", self.driver_text)
        self.assertIn('dir="/dev/shm"', self.driver_text)
        self.assertIn('!= "tmpfs"', self.driver_text)
        self.assertIn("MAX_GIST_FILES = 16", self.driver_text)
        self.assertIn("MAX_GIST_BYTES = 1024 * 1024", self.driver_text)
        self.assertIn("timeout=60", self.driver_text)
        self.assertIn('"GIT_CONFIG_GLOBAL": "/dev/null"', self.driver_text)
        self.assertIn('"GH_PROMPT_DISABLED": "1"', self.driver_text)
        self.assertNotIn('method="PATCH"', self.driver_text)
        for debug_name in (
            "GH_DEBUG",
            "GIT_TRACE",
            "GIT_CURL_VERBOSE",
            "SSH_AUTH_SOCK",
            "ACTIONS_STEP_DEBUG",
            "RUNNER_DEBUG",
        ):
            self.assertNotIn(f'"{debug_name}":', self.driver_text)

    def test_gist_symbolic_head_accepts_only_snapshot_bound_main_or_master(
        self,
    ) -> None:
        expected_head = "1" * 40
        environment = {"PATH": "/usr/bin"}
        for branch in ("refs/heads/main", "refs/heads/master"):
            with mock.patch.object(
                self.driver,
                "secret_git",
                return_value=f"ref: {branch}\tHEAD\n{expected_head}\tHEAD",
            ):
                self.assertEqual(
                    self.driver.gist_remote_branch(
                        ROOT, environment, expected_head
                    ),
                    branch,
                )
                self.assertEqual(
                    self.driver.gist_remote_branch(
                        ROOT,
                        environment,
                        expected_head,
                        expected_branch=branch,
                    ),
                    branch,
                )

        rejected = (
            f"ref: refs/heads/topic\tHEAD\n{expected_head}\tHEAD",
            f"ref: refs/heads/main\tHEAD\n{'2' * 40}\tHEAD",
            f"ref: refs/heads/main\tHEAD\n{expected_head}\tHEAD\nextra",
            "",
        )
        for response in rejected:
            with (
                self.subTest(response=response),
                mock.patch.object(self.driver, "secret_git", return_value=response),
                self.assertRaises(self.driver.AcceptanceError),
            ):
                self.driver.gist_remote_branch(ROOT, environment, expected_head)

        with (
            mock.patch.object(
                self.driver,
                "secret_git",
                return_value=(
                    f"ref: refs/heads/main\tHEAD\n{expected_head}\tHEAD"
                ),
            ),
            self.assertRaisesRegex(self.driver.AcceptanceError, "changed during CAS"),
        ):
            self.driver.gist_remote_branch(
                ROOT,
                environment,
                expected_head,
                expected_branch="refs/heads/master",
            )

    def test_gist_write_threads_discovered_branch_into_exact_lease(self) -> None:
        mutation = self.mutation()
        challenge = "signed-secret"
        written_head = "2" * 40
        mutation.written_file_content = challenge
        mutation.written_file_sha256 = hashlib.sha256(challenge.encode()).hexdigest()
        temporary = mock.Mock()
        commands: list[list[str]] = []

        def secret_git(args, **_kwargs):
            commands.append(args)
            return written_head if args == ["rev-parse", "HEAD"] else ""

        with tempfile.TemporaryDirectory() as root:
            repository = pathlib.Path(root)
            changed = self.gist_response(mutation, written_head, challenge)
            with (
                mock.patch.object(
                    self.driver,
                    "prepare_gist_git",
                    return_value=(
                        temporary,
                        repository,
                        {"PATH": "/usr/bin"},
                        "refs/heads/main",
                    ),
                ),
                mock.patch.object(
                    self.driver, "secret_git", side_effect=secret_git
                ),
                mock.patch.object(self.driver, "gh_json", return_value=changed),
            ):
                self.driver.gist_cas_write(mutation)

        self.assertEqual(mutation.gist_branch, "refs/heads/main")
        self.assertTrue(mutation.gist_changed)
        self.assertIn(
            [
                "push",
                "--quiet",
                f"--force-with-lease=refs/heads/main:{mutation.prior_gist_head}",
                "origin",
                f"{written_head}:refs/heads/main",
            ],
            commands,
        )
        temporary.cleanup.assert_called_once_with()

    def test_lost_gist_write_response_reconciles_only_exact_written_state(
        self,
    ) -> None:
        mutation = self.mutation()
        challenge = "signed-secret"
        written_head = "2" * 40
        prior = self.gist_response(mutation, mutation.prior_gist_head, None)
        exact_written = self.gist_response(mutation, written_head, challenge)
        inexact_written = self.gist_response(mutation, written_head, "other")

        for current, should_restore in (
            (exact_written, True),
            (inexact_written, False),
        ):
            restored: list[object] = []
            temporary = mock.Mock()

            def secret_git(args, **_kwargs):
                if args == ["rev-parse", "HEAD"]:
                    return written_head
                if args[0] == "push":
                    raise self.driver.AcceptanceError("lost Gist push response")
                return ""

            def restore(lease):
                self.assertTrue(lease.gist_changed)
                self.assertEqual(lease.gist_branch, "refs/heads/main")
                self.assertEqual(lease.written_gist_head, written_head)
                self.assertEqual(lease.written_file_content, challenge)
                restored.append(lease)
                lease.gist_changed = False

            with tempfile.TemporaryDirectory() as root:
                repository = pathlib.Path(root)
                diagnostics = io.StringIO()
                with (
                    self.subTest(should_restore=should_restore),
                    mock.patch.object(
                        self.driver,
                        "gh_json",
                        side_effect=(prior, current),
                    ),
                    mock.patch.object(
                        self.driver,
                        "gh_json_or_authenticated_404",
                        side_effect=(None, None),
                    ),
                    mock.patch.object(
                        self.driver,
                        "prepare_gist_git",
                        return_value=(
                            temporary,
                            repository,
                            {"PATH": "/usr/bin"},
                            "refs/heads/main",
                        ),
                    ),
                    mock.patch.object(
                        self.driver, "secret_git", side_effect=secret_git
                    ),
                    mock.patch.object(self.driver, "restore_gist", side_effect=restore),
                    mock.patch.object(self.driver, "require_target_bound_confirmation"),
                    contextlib.redirect_stderr(diagnostics),
                    self.assertRaisesRegex(
                        self.driver.AcceptanceError, "lost Gist push response"
                    ),
                ):
                    self.driver.apply_exact_proof_and_tag(
                        self.fixture, mutation.gist_id, challenge, mutation.tag
                    )

            self.assertEqual(bool(restored), should_restore)
            if should_restore:
                self.assertNotIn("External cleanup still required", diagnostics.getvalue())
            else:
                self.assertIn("unrecognized value", diagnostics.getvalue())
            temporary.cleanup.assert_called_once_with()

    def test_main_branch_restore_uses_exact_lease_and_destination(self) -> None:
        mutation = self.mutation()
        challenge = "signed-secret"
        mutation.gist_changed = True
        mutation.gist_branch = "refs/heads/main"
        mutation.written_gist_head = "2" * 40
        mutation.written_file_content = challenge
        mutation.written_file_sha256 = hashlib.sha256(challenge.encode()).hexdigest()
        changed = self.gist_response(mutation, mutation.written_gist_head, challenge)
        restored = self.gist_response(mutation, mutation.prior_gist_head, None)
        temporary = mock.Mock()
        commands: list[list[str]] = []

        def secret_git(args, **_kwargs):
            commands.append(args)
            if args == ["rev-parse", f"{mutation.written_gist_head}^"]:
                return mutation.prior_gist_head
            return ""

        with tempfile.TemporaryDirectory() as root:
            repository = pathlib.Path(root)
            (repository / mutation.filename).write_text(challenge, encoding="utf-8")
            with (
                mock.patch.object(
                    self.driver, "gh_json", side_effect=(changed, restored)
                ),
                mock.patch.object(
                    self.driver,
                    "prepare_gist_git",
                    return_value=(
                        temporary,
                        repository,
                        {"PATH": "/usr/bin"},
                        "refs/heads/main",
                    ),
                ) as prepare,
                mock.patch.object(
                    self.driver, "secret_git", side_effect=secret_git
                ),
            ):
                self.driver.restore_gist(mutation)

        prepare.assert_called_once_with(
            mutation.gist_id,
            "2" * 40,
            depth=2,
            expected_branch="refs/heads/main",
        )
        self.assertIn(
            [
                "push",
                "--quiet",
                "--force-with-lease=refs/heads/main:" + "2" * 40,
                "origin",
                f"{mutation.prior_gist_head}:refs/heads/main",
            ],
            commands,
        )
        self.assertFalse(mutation.gist_changed)
        self.assertIsNone(mutation.gist_branch)
        temporary.cleanup.assert_called_once_with()

    def test_gist_snapshot_bounds_every_file_before_git_transport(self) -> None:
        mutation = self.mutation()
        response = self.gist_response(mutation, mutation.prior_gist_head, None)
        response["files"] = {
            f"file-{index}": {"truncated": False, "content": "x"}
            for index in range(self.driver.MAX_GIST_FILES + 1)
        }
        with self.assertRaisesRegex(self.driver.AcceptanceError, "file count"):
            self.driver.gist_git_snapshot(response, mutation.gist_id, mutation.filename)
        response["files"] = {
            "other": {
                "truncated": False,
                "content": "x" * (self.driver.MAX_GIST_BYTES + 1),
            }
        }
        with self.assertRaisesRegex(self.driver.AcceptanceError, "content size"):
            self.driver.gist_git_snapshot(response, mutation.gist_id, mutation.filename)
        response["files"] = {"other": {"truncated": True, "content": "redacted"}}
        with self.assertRaisesRegex(self.driver.AcceptanceError, "unsupported"):
            self.driver.gist_git_snapshot(response, mutation.gist_id, mutation.filename)

    def test_secret_git_environment_drops_debug_and_unrelated_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config = root / "gh"
            config.mkdir()
            with mock.patch.dict(
                self.driver.os.environ,
                {
                    "GH_CONFIG_DIR": str(config),
                    "GH_DEBUG": "api",
                    "GIT_TRACE": "1",
                    "GIT_CURL_VERBOSE": "1",
                    "AWS_ACCESS_KEY_ID": "must-not-cross",
                    "SSH_AUTH_SOCK": "/must-not-cross",
                },
                clear=False,
            ):
                environment = self.driver.secret_git_environment(root)
        self.assertEqual(
            set(environment),
            {
                "PATH",
                "HOME",
                "GH_CONFIG_DIR",
                "GH_PROMPT_DISABLED",
                "GIT_CONFIG_GLOBAL",
                "GIT_CONFIG_NOSYSTEM",
                "GIT_TERMINAL_PROMPT",
                "LANG",
                "LC_ALL",
                "NO_COLOR",
            },
        )
        self.assertNotIn("must-not-cross", json.dumps(environment))

    def test_secret_git_failure_redacts_subprocess_output(self) -> None:
        challenge = "signed-challenge-must-not-leak"
        failed = subprocess.CompletedProcess(
            [], 1, stdout=challenge, stderr=f"fatal: {challenge}"
        )
        with (
            mock.patch.object(self.driver.subprocess, "run", return_value=failed),
            self.assertRaises(self.driver.AcceptanceError) as raised,
        ):
            self.driver.secret_git(
                ["push"], cwd=ROOT, env={"PATH": "/usr/bin"}, label="CAS"
            )
        self.assertNotIn(challenge, str(raised.exception))
        self.assertIn("diagnostics redacted", str(raised.exception))

        with (
            mock.patch.object(
                self.driver.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["git"], 60, output=challenge),
            ),
            self.assertRaises(self.driver.AcceptanceError) as timed_out,
        ):
            self.driver.secret_git(
                ["push"], cwd=ROOT, env={"PATH": "/usr/bin"}, label="CAS"
            )
        self.assertNotIn(challenge, str(timed_out.exception))
        self.assertIn("diagnostics redacted", str(timed_out.exception))

    def test_cleanup_attempts_exact_tag_removal_even_if_gist_restore_fails(
        self,
    ) -> None:
        mutation = self.mutation()
        mutation.gist_changed = True
        mutation.tag_created = True
        with (
            mock.patch.object(
                self.driver,
                "restore_gist",
                side_effect=self.driver.AcceptanceError("restore failed"),
            ),
            mock.patch.object(self.driver, "remove_created_tag") as remove,
            self.assertRaisesRegex(self.driver.AcceptanceError, "restore failed"),
        ):
            self.driver.cleanup_fixture_mutation(mutation, remove_tag=True)
        remove.assert_called_once_with(mutation)

    def test_partial_tag_response_loss_restores_gist_but_refuses_unproved_deletion(
        self,
    ) -> None:
        mutation = self.mutation()
        challenge = "signed-secret"
        written_head = "2" * 40
        initial = self.gist_response(mutation, mutation.prior_gist_head, None)
        changed = self.gist_response(mutation, written_head, challenge)
        tag_response = {
            "ref": f"refs/tags/{mutation.tag}",
            "node_id": "node",
            "url": "https://api.github.test/ref",
            "object": {
                "sha": mutation.commit,
                "type": "commit",
                "url": "https://api.github.test/commit",
            },
        }
        tag_posts = 0

        def github(args, *, method="GET", fields=None):
            nonlocal tag_posts
            if method == "POST":
                tag_posts += 1
                raise self.driver.AcceptanceError("lost POST response")
            return initial if tag_posts == 0 else changed

        def gist_write(lease):
            lease.written_gist_head = written_head
            lease.gist_changed = True

        def gist_restore(lease):
            lease.gist_changed = False

        tag_reads = iter((None, tag_response))
        diagnostics = io.StringIO()
        with (
            mock.patch.object(self.driver, "gh_json", side_effect=github),
            mock.patch.object(
                self.driver,
                "gh_json_or_authenticated_404",
                side_effect=lambda _args: next(tag_reads),
            ),
            mock.patch.object(self.driver, "gist_cas_write", side_effect=gist_write),
            mock.patch.object(self.driver, "restore_gist", side_effect=gist_restore),
            mock.patch.object(self.driver, "require_target_bound_confirmation"),
            contextlib.redirect_stderr(diagnostics),
            self.assertRaisesRegex(self.driver.AcceptanceError, "lost POST"),
        ):
            self.driver.apply_exact_proof_and_tag(
                self.fixture, mutation.gist_id, challenge, mutation.tag
            )
        self.assertEqual(tag_posts, 1)
        self.assertIn("refusing unproved deletion", diagnostics.getvalue())
        self.assertIn(mutation.describe_targets(), diagnostics.getvalue())

    def test_gist_restore_refuses_intervening_edit_or_deletion(self) -> None:
        mutation = self.mutation()
        mutation.gist_changed = True
        mutation.written_gist_head = "2" * 40
        mutation.gist_branch = "refs/heads/main"
        mutation.written_file_content = "signed-secret"
        mutation.written_file_sha256 = hashlib.sha256(b"signed-secret").hexdigest()
        intervening = self.gist_response(mutation, "3" * 40, "operator edit")
        with (
            mock.patch.object(
                self.driver, "gh_json", return_value=intervening
            ) as github,
            self.assertRaisesRegex(self.driver.AcceptanceError, "refusing CAS restore"),
        ):
            self.driver.restore_gist(mutation)
        github.assert_called_once_with([f"gists/{mutation.gist_id}"])

    def test_lost_restore_response_reconciles_only_exact_prior_state(self) -> None:
        mutation = self.mutation()
        mutation.gist_changed = True
        mutation.written_gist_head = "2" * 40
        mutation.gist_branch = "refs/heads/main"
        mutation.written_file_content = "signed-secret"
        mutation.written_file_sha256 = hashlib.sha256(b"signed-secret").hexdigest()
        mutation.gist_restore_started = True
        prior = self.gist_response(mutation, mutation.prior_gist_head, None)
        with mock.patch.object(self.driver, "gh_json", return_value=prior) as github:
            self.driver.restore_gist(mutation)
        github.assert_called_once_with([f"gists/{mutation.gist_id}"])
        self.assertFalse(mutation.gist_changed)
        self.assertIsNone(mutation.gist_branch)
        self.assertIsNone(mutation.written_file_content)

    def test_unknown_headless_post_outcome_never_permits_cleanup(self) -> None:
        self.assertTrue(
            self.driver.fixture_cleanup_is_proved_safe(
                headless_post_started=False, headless_terminal=False
            )
        )
        self.assertFalse(
            self.driver.fixture_cleanup_is_proved_safe(
                headless_post_started=True, headless_terminal=False
            )
        )
        self.assertTrue(
            self.driver.fixture_cleanup_is_proved_safe(
                headless_post_started=True, headless_terminal=True
            )
        )

    def test_terminal_status_derives_result_and_rejects_wrong_dispatch(self) -> None:
        submission_id = self.driver.event_id()
        expected = {
            **self.fixture["headless_submission"],
            "source_repository": self.fixture["source"]["repository"],
            "source_commit": self.fixture["source"]["commit"],
        }
        result_id = "r2_" + "d" * 64
        body = {
            "submission_id": submission_id,
            "owner": "kim-em",
            "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "submission": expected,
            "production_metadata": expected["production_metadata"],
            "publication_choice": expected["publication_choice"],
            "archive": {"status": "completed"},
            "evaluation": {"status": "accepted"},
            "result_id": result_id,
            "dispatch": {
                "status": "succeeded",
                "workflow_ref": "lean-eval-dispatch/" + "b" * 40,
            },
        }
        self.assertEqual(
            self.driver.validate_new_submission(
                body, submission_id, expected, "b" * 40
            ),
            result_id,
        )
        body["dispatch"]["workflow_ref"] = "lean-eval-dispatch/" + "c" * 40
        with self.assertRaisesRegex(self.driver.AcceptanceError, "exact candidate"):
            self.driver.validate_new_submission(body, submission_id, expected, "b" * 40)

    def test_terminal_failure_cannot_authorize_cleanup_before_exact_binding(
        self,
    ) -> None:
        submission_id = self.driver.event_id()
        expected = {
            **self.fixture["headless_submission"],
            "source_repository": self.fixture["source"]["repository"],
            "source_commit": self.fixture["source"]["commit"],
        }
        body = {
            "submission_id": submission_id,
            "owner": "kim-em",
            "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "submission": {**expected, "source_commit": "f" * 40},
            "production_metadata": expected["production_metadata"],
            "publication_choice": expected["publication_choice"],
            "archive": {"status": "completed"},
            "evaluation": {"status": "rejected"},
            "result_id": None,
            "dispatch": {
                "status": "succeeded",
                "workflow_ref": "lean-eval-dispatch/" + "b" * 40,
            },
        }
        api = mock.Mock()
        api.request.return_value = body
        with self.assertRaisesRegex(self.driver.AcceptanceError, "payload/source"):
            self.driver.wait_submission(
                api, "token", submission_id, expected, "b" * 40, timeout=1
            )
        body["submission"] = expected
        with self.assertRaises(self.driver.SubmissionTerminalError):
            self.driver.wait_submission(
                api, "token", submission_id, expected, "b" * 40, timeout=1
            )

    def test_preflight_is_read_only_and_checks_candidate_identity(self) -> None:
        bounded = self.fixture["bounded_acceptance"]

        def github_response(args, *, method="GET", fields=None):
            self.assertEqual(method, "GET")
            self.assertIsNone(fields)
            endpoint = args[0]
            if endpoint == "user":
                return {"login": "kim-em"}
            if endpoint.startswith("gists/"):
                return {
                    "id": "a" * 20,
                    "owner": {"login": "kim-em"},
                    "public": False,
                    "git_pull_url": "https://gist.github.com/" + "a" * 20 + ".git",
                    "history": [{"version": "1" * 40}],
                    "files": {},
                }
            if endpoint == "repos/" + self.fixture["source"]["repository"]:
                return {
                    "full_name": self.fixture["source"]["repository"],
                    "private": True,
                    "default_branch": "main",
                }
            if endpoint.endswith(
                "/branches/" + self.fixture["source"]["fixture_branch"]
            ):
                return {
                    "name": self.fixture["source"]["fixture_branch"],
                    "protected": False,
                    "commit": {"sha": self.fixture["source"]["commit"]},
                }
            if endpoint.endswith("/commits/" + self.fixture["source"]["commit"]):
                return {"sha": self.fixture["source"]["commit"]}
            if "/commits/" + bounded["release_ref"] in endpoint:
                return {"sha": bounded["release_commit"]}
            if "/branches/" in endpoint:
                return {"protected": True}
            self.fail(endpoint)

        with (
            mock.patch.object(self.driver, "verify_candidate_checkout") as checkout,
            mock.patch.object(self.driver, "gh_json", side_effect=github_response),
            mock.patch.object(self.driver, "health", return_value=False),
        ):
            self.driver.fixture_preflight(self.fixture, "a" * 20, "b" * 40)
        checkout.assert_called_once_with("b" * 40, self.fixture)

    def test_release_jobs_require_the_exact_successful_untruncated_lane(self) -> None:
        run_id = 123456
        commit = "4" * 40
        jobs = [
            {
                "id": index,
                "name": name,
                "run_id": run_id,
                "run_attempt": 1,
                "head_sha": commit,
                "status": "completed",
                "conclusion": "success",
            }
            for index, name in enumerate(
                ("authorize-manual", "prepare-one", "unwrap-one"), start=1
            )
        ]
        with mock.patch.object(
            self.driver,
            "gh_json",
            return_value={"total_count": 3, "jobs": jobs},
        ) as github:
            self.driver.verify_exact_release_jobs(
                "leanprover/lean-eval-releases", run_id, commit
            )
        github.assert_called_once_with(
            [
                f"repos/leanprover/lean-eval-releases/actions/runs/{run_id}/jobs",
                "-f",
                "filter=latest",
                "-f",
                "per_page=100",
                "-f",
                "page=1",
            ]
        )

    def test_release_jobs_reject_skips_missing_extra_ambiguous_or_truncated(
        self,
    ) -> None:
        run_id = 123456
        commit = "4" * 40

        def exact_jobs() -> list[dict[str, object]]:
            return [
                {
                    "id": index,
                    "name": name,
                    "run_id": run_id,
                    "run_attempt": 1,
                    "head_sha": commit,
                    "status": "completed",
                    "conclusion": "success",
                }
                for index, name in enumerate(
                    ("authorize-manual", "prepare-one", "unwrap-one"), start=1
                )
            ]

        cases: dict[str, dict[str, object]] = {}
        skipped = exact_jobs()
        skipped[2]["conclusion"] = "skipped"
        cases["skipped unwrap"] = {"total_count": 3, "jobs": skipped}
        cases["missing unwrap"] = {"total_count": 2, "jobs": exact_jobs()[:2]}
        cases["extra job"] = {
            "total_count": 4,
            "jobs": [*exact_jobs(), {**exact_jobs()[0], "name": "unexpected"}],
        }
        ambiguous = exact_jobs()
        ambiguous[2]["name"] = "prepare-one"
        cases["duplicate name"] = {"total_count": 3, "jobs": ambiguous}
        cases["truncated page"] = {"total_count": 4, "jobs": exact_jobs()}
        wrong_run = exact_jobs()
        wrong_run[2]["run_id"] = run_id + 1
        cases["wrong run"] = {"total_count": 3, "jobs": wrong_run}
        wrong_commit = exact_jobs()
        wrong_commit[2]["head_sha"] = "5" * 40
        cases["wrong commit"] = {"total_count": 3, "jobs": wrong_commit}
        rerun = exact_jobs()
        rerun[2]["run_attempt"] = 2
        cases["rerun attempt"] = {"total_count": 3, "jobs": rerun}
        duplicate_id = exact_jobs()
        duplicate_id[2]["id"] = duplicate_id[1]["id"]
        cases["duplicate id"] = {"total_count": 3, "jobs": duplicate_id}
        for label, response in cases.items():
            with (
                self.subTest(label=label),
                mock.patch.object(self.driver, "gh_json", return_value=response),
                self.assertRaises(self.driver.AcceptanceError),
            ):
                self.driver.verify_exact_release_jobs(
                    "leanprover/lean-eval-releases", run_id, commit
                )

    def test_driver_binds_payload_status_dispatch_and_unique_release_run(self) -> None:
        for required in (
            'evaluation.get("status") == "accepted"',
            'dispatch.get("workflow_ref") != f"lean-eval-dispatch/{expected_commit}"',
            'body.get("submission") != expected_submission',
            'expected_title=bounded["release_run_name_prefix"] + headless_id',
            "f\"expected_release_commit={bounded['release_commit']}\"",
            'item.get("display_title") == expected_title',
            "verify_exact_release_jobs(",
            "fixture_cleanup_is_proved_safe(",
            "cleanup_fixture_mutation(lease, remove_tag=True)",
            "Headless acceptance outcome may be committed",
        ):
            self.assertIn(required, self.driver_text)
        self.assertNotIn(
            'evaluation.get("status") in {"accepted", "rejected"}', self.driver_text
        )

    def test_state_assertions_use_reviewed_hashes_and_closed_environment(self) -> None:
        for required in (
            '"merge-base",',
            '"--is-ancestor",',
            'scripts != bounded["state_script_sha256"]',
            '"PYTHONDONTWRITEBYTECODE": "1"',
            '"PYTHONHASHSEED": "0"',
            '"-I",',
            "operator-free-home",
            "State assertion scripts modified their checkout",
        ):
            self.assertIn(required, self.driver_text)
        self.assertNotIn('"--depth=1"', self.driver_text)

    def test_disabled_route_evidence_and_retirement_are_explicit(self) -> None:
        self.assertIn("assert_disabled_routes(", self.driver_text)
        self.assertIn("denial_fixture=fixture", self.driver_text)
        self.assertIn('"/api/v1/agent/challenges"', self.driver_text)
        self.assertIn(
            "state_before_intake_probe = state_branch_commit", self.driver_text
        )
        self.assertIn("disabled intake probe changed staging State", self.driver_text)
        self.assertIn(
            'f"/api/v1/model-identities/{model_id}/decisions"', self.driver_text
        )
        self.assertIn(
            'f"/api/v1/results/{browser_result}/problem-repairs/decisions"',
            self.driver_text,
        )
        self.assertIn("does not verify the public entry page", self.runbook)
        self.assertIn("ordinary process memory", self.runbook)
        self.assertIn("nonsecret rollback targets", self.runbook)
        self.assertIn("workflow-level", self.runbook)
        self.assertIn("success alone is insufficient", self.runbook)
        self.assertIn(
            "must be deleted with the staging fixture after one accepted\nrun",
            self.runbook,
        )
        for retired in (
            ".github/workflows/bounded-staging-lifecycle-watchdog.yml",
            ".github/workflows/set-staging-lifecycle-smoke.yml",
            "configuration/staging-lifecycle-smoke-v1.json",
            "scripts/run_bounded_staging_lifecycle.py",
            "tests/test_bounded_staging_lifecycle_acceptance.py",
            "tests/test_staging_lifecycle_smoke.py",
        ):
            self.assertIn(retired, self.runbook)
        self.assertEqual(
            self.fixture["bounded_acceptance"]["retire_after"],
            "one accepted bounded staging lifecycle run",
        )

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
