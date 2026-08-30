from __future__ import annotations

import copy
import hashlib
import io
import json
import pathlib
import runpy
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

import jsonschema
from referencing import Registry, Resource

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import historical_private_image_qualification as qualification
from prepare_historical_private_replay import canonical
from replay_orchestrator import config_digest

MATRIX = ROOT / "configuration/historical-private-replay-image-matrix-v1.json"
SCHEMA = ROOT / "schemas/historical-private-profile-qualification-v1.schema.json"
WORKFLOW = ROOT / ".github/workflows/historical-private-image-qualification.yml"
PROBE = runpy.run_path(ROOT / "scripts/run_historical_private_cloudflare_probe")


class HistoricalPrivateImageQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix_raw = MATRIX.read_bytes()
        cls.matrix = json.loads(cls.matrix_raw)
        cls.entry = cls.matrix["images"][0]
        cls.source_commit = "1" * 40
        cls.candidate = qualification.select_candidate(
            MATRIX,
            cls.entry["benchmark_commit"],
            ROOT,
            cls.source_commit,
        )

    def receipt(self, manifest: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": qualification.RECEIPT_KIND,
            "registry_manifest_digest": manifest,
            "benchmark_commit": self.entry["benchmark_commit"],
            "runner_entrypoint": "/opt/lean-eval/replay-authoritative",
            "archive_expectation_schema_version": 2,
            "key_material_type": "age-file-key-v1",
            "network_probe": "blocked",
            "status": "passed",
            "architecture": "x86_64",
            "kernel_release": "6.18.36-cloudflare-firecracker-2026.6.17",
            "cpu_model": "AMD EPYC",
        }

    def test_selector_accepts_exactly_the_closed_63_entry_matrix(self) -> None:
        images = qualification.validate_matrix(self.matrix, self.matrix_raw)
        self.assertEqual(len(images), 63)
        self.assertEqual(
            hashlib.sha256(self.matrix_raw).hexdigest(), qualification.MATRIX_SHA256
        )
        self.assertEqual(self.candidate["benchmark_commit"], self.entry["benchmark_commit"])
        self.assertEqual(
            self.candidate["matrix_entry_sha256"],
            hashlib.sha256(canonical(self.entry)).hexdigest(),
        )
        self.assertEqual(
            self.candidate["profile_lock"]["runner_profile"],
            "cloudflare-sandbox-standard-4-v1",
        )

    def test_selector_binds_the_complete_final_source_blob_closure(self) -> None:
        self.assertEqual(
            set(self.candidate["source_blobs"]), set(qualification.SOURCE_PATHS)
        )
        for name, relative in qualification.SOURCE_PATHS.items():
            blob = self.candidate["source_blobs"][name]
            self.assertEqual(blob["path"], relative)
            self.assertEqual(
                blob["sha256"], hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            )

    def test_matrix_change_or_unknown_commit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            changed = copy.deepcopy(self.matrix)
            changed["images"][0]["workspace_count"] += 1
            path = pathlib.Path(raw) / "matrix.json"
            path.write_bytes(canonical(changed))
            with self.assertRaisesRegex(
                qualification.QualificationError, "matrix identity changed"
            ):
                qualification.select_candidate(
                    path, self.entry["benchmark_commit"], ROOT, self.source_commit
                )
        with self.assertRaisesRegex(
            qualification.QualificationError, "does not select exactly one"
        ):
            qualification.select_candidate(MATRIX, "0" * 40, ROOT, self.source_commit)

    def test_existing_immutable_tag_resumes_only_for_the_exact_image(self) -> None:
        labels = {
            "org.lean-eval.image-family": qualification.IMAGE_FAMILY,
            "org.lean-eval.image-matrix-sha256": qualification.MATRIX_SHA256,
            "org.lean-eval.image-source-commit": self.source_commit,
            "org.lean-eval.benchmark-commit": self.entry["benchmark_commit"],
            "org.lean-eval.image-source-closure-sha256": hashlib.sha256(
                canonical(self.candidate)
            ).hexdigest(),
        }
        config_raw = json.dumps(
            {"config": {"Labels": labels}}, separators=(",", ":")
        ).encode()
        manifest_raw = json.dumps(
            {
                "schemaVersion": 2,
                "config": {
                    "digest": "sha256:" + hashlib.sha256(config_raw).hexdigest()
                },
                "layers": [],
            },
            separators=(",", ":"),
        ).encode()
        digest = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            manifest = root / "manifest.json"
            config = root / "config.json"
            manifest.write_bytes(manifest_raw)
            config.write_bytes(config_raw)
            self.assertEqual(
                qualification.validate_registry_image(
                    self.candidate, digest, manifest, config
                ),
                digest,
            )
            changed = json.loads(config_raw)
            changed["config"]["Labels"][
                "org.lean-eval.image-source-commit"
            ] = "0" * 40
            changed_raw = json.dumps(changed, separators=(",", ":")).encode()
            config.write_bytes(changed_raw)
            with self.assertRaisesRegex(
                qualification.QualificationError,
                "manifest does not bind|labels differ",
            ):
                qualification.validate_registry_image(
                    self.candidate, digest, manifest, config
                )

    def test_renderer_produces_the_final_private_profile_schema_shape(self) -> None:
        manifest = "sha256:" + "2" * 64
        workflow_sha = hashlib.sha256(WORKFLOW.read_bytes()).hexdigest()
        execution_digest, profile = qualification.render_qualification(
            self.candidate,
            self.receipt(manifest),
            manifest,
            "3" * 40,
            workflow_sha,
            1234,
            2,
        )
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        plan_schema = json.loads(
            (ROOT / "schemas/historical-private-replay-plan-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        registry = Registry().with_resources(
            [
                (value["$id"], Resource.from_contents(value))
                for value in (schema, plan_schema)
            ]
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema, registry=registry).validate(profile)
        self.assertEqual(set(profile), set(schema["required"]))
        self.assertEqual(profile["qualification_status"], "qualified")
        self.assertEqual(profile["registry_manifest_digest"], manifest)
        self.assertEqual(profile["execution_profile"]["vm_image_digest"], manifest)
        self.assertEqual(
            profile["qualification"]["private_archive_probe"],
            {
                "archive_expectation_schema_version": 2,
                "key_material_type": "age-file-key-v1",
                "runner_entrypoint": "/opt/lean-eval/replay-authoritative",
                "status": "passed",
            },
        )
        self.assertEqual(profile["qualification"]["network_probe"], "blocked")
        self.assertEqual(
            execution_digest,
            config_digest(
                "lean-eval-replay-execution-profile-v1",
                profile["execution_profile"],
            ),
        )
        self.assertEqual(profile["execution_profile_digest"], execution_digest)
        self.assertEqual(
            profile["measurement_config_digest"],
            config_digest(
                "lean-eval-replay-measurement-config-v1",
                profile["measurement_config"],
            ),
        )
        self.assertEqual(canonical(profile), canonical(json.loads(canonical(profile))))

    def test_renderer_refuses_non_v2_or_non_blocked_receipts(self) -> None:
        manifest = "sha256:" + "2" * 64
        for field, value in (
            ("archive_expectation_schema_version", 1),
            ("key_material_type", "age-identity-v1"),
            ("network_probe", "available"),
            ("status", "failed"),
        ):
            with self.subTest(field=field):
                receipt = self.receipt(manifest)
                receipt[field] = value
                with self.assertRaisesRegex(
                    qualification.QualificationError, "passing exact probe"
                ):
                    qualification.render_qualification(
                        self.candidate,
                        receipt,
                        manifest,
                        "3" * 40,
                        "4" * 64,
                        1234,
                        1,
                    )

    def test_probe_archive_and_disposable_config_are_closed_to_one_run(self) -> None:
        manifest = "sha256:" + "2" * 64
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            archive = root / "source.tar.gz"
            qualification.create_probe_archive(self.candidate, archive)
            ciphertext = root / "source.tar.gz.age"
            ciphertext.write_bytes(b"age-encryption.org/v1\nfixture")
            key = root / "file-key"
            key.write_bytes(b"0123456789abcdef")
            output = root / "closed"
            output.mkdir()
            context = qualification.prepare_probe_inputs(
                self.candidate,
                archive,
                ciphertext,
                key,
                manifest,
                123456,
                2,
                output,
            )
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {"context.json", "request.json", "status.json", "wrangler.json"},
            )
            config = json.loads((output / "wrangler.json").read_bytes())
            request = json.loads((output / "request.json").read_bytes())
            self.assertEqual(config["name"], "lean-eval-private-q-123456-2")
            self.assertEqual(context["container_application_name"], "le-q-123456-2")
            self.assertEqual(
                config["containers"][0]["name"], context["container_application_name"]
            )
            self.assertEqual(config["workers_dev"], True)
            self.assertEqual(config["containers"][0]["max_instances"], 1)
            self.assertEqual(config["containers"][0]["ssh"], {"enabled": False})
            self.assertEqual(
                config["containers"][0]["image"],
                f"registry.cloudflare.com/{qualification.CLOUDFLARE_ACCOUNT_ID}/"
                f"lean-eval-authoritative@{manifest}",
            )
            self.assertEqual(config["vars"]["QUALIFICATION_RUN_ID"], "123456")
            self.assertEqual(config["vars"]["QUALIFICATION_RUN_ATTEMPT"], "2")
            self.assertEqual(
                config["vars"]["EXPECTED_RUNNER_NONCE"], context["runner_nonce"]
            )
            self.assertEqual(
                config["vars"]["EXPECTED_REPLAY_TASK_ID"], context["replay_task_id"]
            )
            self.assertEqual(config["vars"]["EXPECTED_REPLAY_ATTEMPT"], "1")
            self.assertEqual(
                config["vars"]["EXPECTED_QUALIFICATION_REQUEST_SHA256"],
                hashlib.sha256(canonical(request)).hexdigest(),
            )
            self.assertEqual(request["schema_version"], 2)
            self.assertEqual(request["key_material_type"], "age-file-key-v1")
            self.assertEqual(
                request["archive_expectation"]["schema_version"], 2
            )
            self.assertEqual(
                request["request"]["network"]["untrusted_execution_phase"],
                "disabled",
            )

    def test_probe_health_retries_only_transient_readiness_failures(self) -> None:
        commit = "1" * 40
        for status in (404, 429, 500, 503):
            with self.subTest(status=status):
                unavailable = urllib.error.HTTPError(
                    "https://qualifier.example/healthz",
                    status,
                    "not ready",
                    {},
                    None,
                )
                ready = io.BytesIO(
                    canonical(
                        {
                            "status": "ok",
                            "environment": "private-qualification",
                            "deployed_commit": commit,
                            "replay_enabled": True,
                        }
                    )
                )
                with (
                    mock.patch.object(
                        PROBE["urllib"].request,
                        "urlopen",
                        side_effect=[unavailable, ready],
                    ) as urlopen,
                    mock.patch.object(PROBE["time"], "sleep") as sleep,
                ):
                    PROBE["_health"]("https://qualifier.example", commit)
                self.assertEqual(urlopen.call_count, 2)
                sleep.assert_called_once_with(PROBE["HEALTH_RETRY_SECONDS"])

    def test_probe_uses_fixed_identity_for_every_worker_request(self) -> None:
        commit = "1" * 40
        health = io.BytesIO(
            canonical(
                {
                    "status": "ok",
                    "environment": "private-qualification",
                    "deployed_commit": commit,
                    "replay_enabled": True,
                }
            )
        )
        posted = io.BytesIO(canonical({"status": "reserved"}))
        posted.status = 200
        with (
            mock.patch.object(
                PROBE["urllib"].request,
                "urlopen",
                side_effect=[health, posted],
            ) as urlopen,
            mock.patch.object(PROBE["time"], "sleep"),
            mock.patch.dict(
                PROBE["_post"].__globals__, {"_oidc_token": lambda: "token"}
            ),
        ):
            PROBE["_health"]("https://qualifier.example", commit)
            self.assertEqual(
                PROBE["_post"]("https://qualifier.example/reserve", {"value": 1}),
                (200, {"status": "reserved"}),
            )
        self.assertEqual(urlopen.call_count, 2)
        for call in urlopen.call_args_list:
            request = call.args[0]
            self.assertEqual(
                request.get_header("User-agent"),
                PROBE["QUALIFICATION_USER_AGENT"],
            )

    def test_probe_health_retries_transport_failures(self) -> None:
        commit = "1" * 40
        for unavailable in (TimeoutError(), urllib.error.URLError("unavailable")):
            with self.subTest(error=type(unavailable).__name__):
                ready = io.BytesIO(
                    canonical(
                        {
                            "status": "ok",
                            "environment": "private-qualification",
                            "deployed_commit": commit,
                            "replay_enabled": True,
                        }
                    )
                )
                with (
                    mock.patch.object(
                        PROBE["urllib"].request,
                        "urlopen",
                        side_effect=[unavailable, ready],
                    ) as urlopen,
                    mock.patch.object(PROBE["time"], "sleep") as sleep,
                ):
                    PROBE["_health"]("https://qualifier.example", commit)
                self.assertEqual(urlopen.call_count, 2)
                sleep.assert_called_once_with(PROBE["HEALTH_RETRY_SECONDS"])

    def test_probe_health_fails_after_its_bounded_attempts(self) -> None:
        errors = [
            urllib.error.HTTPError(
                "https://qualifier.example/healthz", 404, "not ready", {}, None
            )
            for _ in range(PROBE["HEALTH_ATTEMPTS"])
        ]
        with (
            mock.patch.object(PROBE["urllib"].request, "urlopen", side_effect=errors),
            mock.patch.object(PROBE["time"], "sleep") as sleep,
            self.assertRaisesRegex(
                qualification.QualificationError,
                "did not converge after 25 attempts; last failure: http_404",
            ),
        ):
            PROBE["_health"]("https://qualifier.example", "1" * 40)
        self.assertEqual(sleep.call_count, PROBE["HEALTH_ATTEMPTS"] - 1)

    def test_probe_health_does_not_retry_permanent_http_failure(self) -> None:
        forbidden = urllib.error.HTTPError(
            "https://qualifier.example/healthz", 403, "forbidden", {}, None
        )
        with (
            mock.patch.object(
                PROBE["urllib"].request, "urlopen", side_effect=forbidden
            ) as urlopen,
            mock.patch.object(PROBE["time"], "sleep") as sleep,
            self.assertRaisesRegex(
                qualification.QualificationError, "permanent HTTP 403"
            ),
        ):
            PROBE["_health"]("https://qualifier.example", "1" * 40)
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_probe_health_does_not_retry_non_json_success(self) -> None:
        with (
            mock.patch.object(
                PROBE["urllib"].request,
                "urlopen",
                return_value=io.BytesIO(b"not json"),
            ) as urlopen,
            mock.patch.object(PROBE["time"], "sleep") as sleep,
            self.assertRaisesRegex(
                qualification.QualificationError, "non-JSON content"
            ),
        ):
            PROBE["_health"]("https://qualifier.example", "1" * 40)
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_probe_health_does_not_retry_an_identity_mismatch(self) -> None:
        wrong = io.BytesIO(
            canonical(
                {
                    "status": "ok",
                    "environment": "private-qualification",
                    "deployed_commit": "2" * 40,
                    "replay_enabled": True,
                }
            )
        )
        with (
            mock.patch.object(PROBE["urllib"].request, "urlopen", return_value=wrong),
            mock.patch.object(PROBE["time"], "sleep") as sleep,
            self.assertRaises(qualification.QualificationError) as raised,
        ):
            PROBE["_health"]("https://qualifier.example", "1" * 40)
        self.assertEqual(
            str(raised.exception),
            "qualification executor health identity mismatch: deployed_commit",
        )
        self.assertNotIn("2" * 40, str(raised.exception))
        sleep.assert_not_called()

    def test_workflow_is_manual_single_entry_and_disposable(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertNotIn("strategy:", workflow)
        self.assertIn("environment: cloudflare-production", workflow)
        self.assertIn("confirm_registry_publication_and_qualification", workflow)
        self.assertIn("confirm_temporary_cloudflare_resource_creation", workflow)
        self.assertIn("publish-and-qualify-one-private-image", workflow)
        self.assertIn("benchmark commit does not select exactly one of 63 images", (
            ROOT / "scripts/historical_private_image_qualification.py"
        ).read_text(encoding="utf-8"))
        gate = workflow.index('test -x "$EXECUTOR"')
        build = workflow.index("docker build --progress=plain")
        publish = workflow.index("wrangler containers push")
        self.assertLess(gate, build)
        self.assertLess(gate, publish)
        executor = ROOT / "scripts/run_historical_private_cloudflare_probe"
        self.assertTrue(executor.exists())
        self.assertIn("cleanup-only", executor.read_text(encoding="utf-8"))
        self.assertIn("private-qualification/reserve", executor.read_text(encoding="utf-8"))
        self.assertIn("wrangler delete", workflow)
        self.assertIn("wrangler containers delete", workflow)
        self.assertIn("container_application_name", workflow)
        deletion = workflow.split(
            "- name: Delete and verify absence of the disposable Worker", 1
        )[1].split("- name: Render one canonical", 1)[0]
        self.assertIn("if: always() && steps.deploy.outcome != 'skipped'", deletion)
        self.assertNotIn("steps.sandbox_cleanup", deletion)
        self.assertIn("container-applications-final.json", deletion)
        self.assertNotIn("lean-eval-replay-executor.lean-eval.workers.dev", workflow)
        self.assertIn('test "$GITHUB_REF_TYPE" = tag', workflow)
        self.assertIn(
            'test "$GITHUB_REF" = "refs/tags/lean-eval-dispatch/$GITHUB_SHA"',
            workflow,
        )
        self.assertIn('test "$remote_commit" = "$GITHUB_SHA"', workflow)
        self.assertIn("[.commit.sha, .protected] | @tsv", workflow)
        self.assertIn(
            'git merge-base --is-ancestor "$GITHUB_SHA" "$main_commit"', workflow
        )
        self.assertLess(
            workflow.index('test "$GITHUB_REF_TYPE" = tag'),
            workflow.index("CLOUDFLARE_API_TOKEN: ${{ secrets."),
        )
        self.assertIn("validate-registry-image", workflow)
        self.assertIn("Exact immutable tag exists; verifying it", workflow)
        publication = workflow.split(
            "- name: Publish once and resolve the immutable registry digest", 1
        )[1].split("- name: Prepare one synthetic schema-v2 file-key probe", 1)[0]
        self.assertEqual(publication.count("refresh_registry_credentials"), 3)
        self.assertLess(
            publication.index('npx --prefix server wrangler containers push "$IMAGE"'),
            publication.rindex("refresh_registry_credentials"),
        )
        self.assertLess(
            publication.rindex("refresh_registry_credentials"),
            publication.rindex('"$manifest_url" --dump-header'),
        )
        self.assertNotIn(
            "immutable registry tag already exists; refusing overwrite", workflow
        )
        self.assertIn("container-applications-deployed.json", workflow)
        self.assertIn("$matches[0].image == $image", workflow)

    def test_workflow_scopes_cloudflare_and_review_write_authority(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(workflow.count("CLOUDFLARE_ACCOUNT_ID: ${{ secrets."), 3)
        self.assertEqual(workflow.count("CLOUDFLARE_API_TOKEN: ${{ secrets."), 3)
        self.assertIn('test -z "${CLOUDFLARE_ACCOUNT_ID:-}"', workflow)
        self.assertIn('test -z "${CLOUDFLARE_API_TOKEN:-}"', workflow)
        self.assertIn("create-probe-archive", workflow)
        self.assertIn("prepare-probe", workflow)
        self.assertIn("--cleanup-only", workflow)
        self.assertIn("stage-isolated-review-branch:", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("historical-private-profile-review-${{ github.sha }}", workflow)
        self.assertIn(
            'git push origin "HEAD:refs/heads/$REVIEW_BRANCH"', workflow
        )
        self.assertNotIn("lean-eval-state", workflow)

    def test_teardown_never_deletes_an_unowned_colliding_application(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        deploy = workflow.split(
            "- name: Create only the isolated per-run qualifier Worker", 1
        )[1].split("- name: Run exactly one official-entrypoint", 1)[0]
        worker_absent = deploy.index('test "$code" = 404')
        application_absent = deploy.index(
            '"$RUNNER_TEMP/container-applications-before.json\")" = 0'
        )
        ownership_marker = deploy.index(
            ': > "$RUNNER_TEMP/qualifier-deploy-attempted"'
        )
        self.assertLess(worker_absent, ownership_marker)
        self.assertLess(application_absent, ownership_marker)

        deletion = workflow.split(
            "- name: Delete and verify absence of the disposable Worker", 1
        )[1].split("- name: Render one canonical", 1)[0]
        unowned, owned = deletion.split(
            '          npx --prefix server wrangler delete "$worker"', 1
        )
        self.assertIn(
            'if ! test -f "$RUNNER_TEMP/qualifier-deploy-attempted"; then',
            unowned,
        )
        self.assertIn('test "$code" = 404', unowned)
        self.assertIn("container-applications-final.json", unowned)
        self.assertIn("exit 0\n          fi", unowned)
        self.assertNotIn("wrangler containers delete", unowned)
        self.assertIn("wrangler containers delete", owned)


if __name__ == "__main__":
    unittest.main()
