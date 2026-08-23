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

    def test_staging_only_enablement_uses_the_frozen_review(self) -> None:
        for environment in ("staging", "production"):
            selected = WRANGLER["env"][environment]
            self.assertEqual(
                selected["vars"]["REPLAY_ENABLED"],
                "true" if environment == "staging" else "false",
            )
            self.assertEqual(
                selected["vars"]["REVIEWED_EXECUTION_PROFILE_DIGEST"],
                CONFIGURATION["execution_profile_digest"],
            )
            self.assertEqual(
                selected["vars"]["REVIEWED_MEASUREMENT_CONFIG_DIGEST"],
                CONFIGURATION["measurement_config_digest"],
            )
            self.assertEqual(
                selected["vars"]["REVIEWED_VM_IMAGE_DIGEST"],
                CONFIGURATION["registry_manifest_digest"],
            )

    def test_deployment_smokes_bind_both_frozen_digests(self) -> None:
        for field in ("execution_profile_digest", "measurement_config_digest"):
            self.assertEqual(DEPLOY.count(CONFIGURATION[field]), 2)


if __name__ == "__main__":
    unittest.main()
