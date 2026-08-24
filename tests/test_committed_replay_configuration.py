from __future__ import annotations

import json
import pathlib
import unittest

from scripts.replay_orchestrator import config_digest


ROOT = pathlib.Path(__file__).parents[1]
CONFIGURATION = json.loads(
    (ROOT / "configuration/authoritative-replay-staging-v1.json").read_text(
        encoding="utf-8"
    )
)
WRANGLER = json.loads(
    (ROOT / "server/wrangler.replay.jsonc").read_text(encoding="utf-8")
)
DEPLOY = (ROOT / ".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")


class CommittedReplayConfigurationTests(unittest.TestCase):
    def test_frozen_digests_are_canonical(self) -> None:
        self.assertEqual(
            CONFIGURATION["execution_profile_digest"],
            config_digest(
                "lean-eval-replay-execution-profile-v1",
                CONFIGURATION["execution_profile"],
            ),
        )
        self.assertEqual(
            CONFIGURATION["measurement_config_digest"],
            config_digest(
                "lean-eval-replay-measurement-config-v1",
                CONFIGURATION["measurement_config"],
            ),
        )

    def test_both_disabled_environments_await_the_background_protocol_freeze(
        self,
    ) -> None:
        active_manifests = set()
        for environment in ("staging", "production"):
            selected = WRANGLER["env"][environment]
            self.assertEqual(selected["vars"]["REPLAY_ENABLED"], "false")
            self.assertEqual(
                selected["vars"]["REVIEWED_EXECUTION_PROFILE_DIGEST"],
                "0" * 64,
            )
            self.assertEqual(
                selected["vars"]["REVIEWED_MEASUREMENT_CONFIG_DIGEST"],
                "0" * 64,
            )
            active_manifests.add(selected["vars"]["REVIEWED_VM_IMAGE_DIGEST"])
        self.assertEqual(len(active_manifests), 1)
        active_manifest = active_manifests.pop()
        self.assertRegex(active_manifest, r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(active_manifest, "sha256:" + "0" * 64)
        self.assertNotEqual(
            active_manifest,
            CONFIGURATION["registry_manifest_digest"],
        )

    def test_disabled_deployment_smokes_require_unfrozen_digests(self) -> None:
        self.assertEqual(DEPLOY.count("0" * 64), 4)


if __name__ == "__main__":
    unittest.main()
