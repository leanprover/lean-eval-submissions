from __future__ import annotations

import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[1]
IMAGE_TAG = "48525d13562f99fc8f24d8467ec3855005474195"
IMAGE_DIGEST = "sha256:dd790c0c84eabac20c48e827a825809ea5a35e3baefd03c40609f9fdca80f6fc"
IMAGE_REFERENCE = (
    "registry.cloudflare.com/a46b90978a1c29cc4795f30677e7e4b8/"
    f"lean-eval-authoritative:{IMAGE_TAG}"
)
SCRIPT = ROOT / "scripts" / "verify_authoritative_replay_image_reference"
DEPLOY = ROOT / ".github" / "workflows" / "deploy-worker.yml"
WRANGLER = json.loads(
    (ROOT / "server" / "wrangler.replay.jsonc").read_text(encoding="utf-8")
)


class AuthoritativeReplayImageReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SCRIPT.read_text(encoding="utf-8")
        cls.deploy = DEPLOY.read_text(encoding="utf-8")

    def test_reference_is_immutable_nonplaceholder_and_environment_bounded(self) -> None:
        self.assertIn(r"registry\.cloudflare\.com", self.script)
        self.assertIn("lean-eval-authoritative", self.script)
        self.assertIn("[0-9a-f]{40}", self.script)
        self.assertIn('digest == "sha256:" + "0" * 64', self.script)
        self.assertIn('!= staging ] && [ "$environment" != production', self.script)

    def test_registry_head_is_bound_to_the_reviewed_manifest(self) -> None:
        self.assertIn("registry.cloudflare.com/v2/$account_id", self.script)
        self.assertIn("ocker-[Cc]ontent-[Dd]igest", self.script)
        self.assertIn("timeout 60s npx", self.script)
        self.assertIn("--max-time 30", self.script)
        self.assertIn('if [ "$actual_digest" != "$expected_digest" ]', self.script)
        self.assertIn('if [ "$account_id" != "$CLOUDFLARE_ACCOUNT_ID" ]', self.script)
        self.assertNotIn("containers push", self.script)
        self.assertEqual(self.script.count("::add-mask::"), 2)
        self.assertIn("unset CLOUDFLARE_API_TOKEN", self.script)
        self.assertIn('shred --remove "$credentials"', self.script)

    def test_both_deployments_verify_before_wrangler_deploy(self) -> None:
        self.assertEqual(
            self.deploy.count("verify_authoritative_replay_image_reference"), 2
        )
        for environment in ("staging", "production"):
            verification = self.deploy.index(
                f"--config wrangler.replay.jsonc --environment {environment}"
            )
            deployment = self.deploy.index(
                f"wrangler deploy --config wrangler.replay.jsonc --env {environment}"
            )
            broker = self.deploy.index(
                f"wrangler deploy --config wrangler.broker.jsonc --env {environment}"
            )
            self.assertLess(verification, broker)
            self.assertLess(verification, deployment)
            self.assertLess(deployment, broker)

    def test_staging_and_production_use_one_reviewed_manifest(self) -> None:
        staging = WRANGLER["env"]["staging"]
        production = WRANGLER["env"]["production"]
        self.assertEqual(
            staging["containers"][0]["image"], production["containers"][0]["image"]
        )
        self.assertEqual(
            staging["vars"]["REVIEWED_VM_IMAGE_DIGEST"],
            production["vars"]["REVIEWED_VM_IMAGE_DIGEST"],
        )
        self.assertEqual(
            staging["containers"][0]["image"],
            IMAGE_REFERENCE,
        )
        self.assertEqual(staging["vars"]["REVIEWED_VM_IMAGE_DIGEST"], IMAGE_DIGEST)

    def test_deployment_smokes_expect_the_reviewed_manifest(self) -> None:
        self.assertEqual(
            self.deploy.count(
                f'"reviewed_vm_image_digest": "{IMAGE_DIGEST}"'
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
