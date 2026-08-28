from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import tempfile
import unittest

from scripts import verify_production_capabilities_disabled as verifier

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProductionCapabilitiesDisabledTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.intake = json.loads(
            (ROOT / verifier.INTAKE_PATH).read_text(encoding="utf-8")
        )
        cls.replay = json.loads(
            (ROOT / verifier.REPLAY_PATH).read_text(encoding="utf-8")
        )

    def test_accepts_exact_disabled_production_without_restricting_staging(
        self,
    ) -> None:
        intake = copy.deepcopy(self.intake)
        replay = copy.deepcopy(self.replay)
        intake["env"]["staging"]["vars"]["PROMOTION_CANARY_ENABLED"] = "true"
        replay["env"]["staging"]["vars"]["STAGING_ACCEPTANCE_ENABLED"] = "true"
        self.assertEqual(
            verifier.verify_disabled_configs(intake, replay),
            {
                "intake": verifier.INTAKE_CAPABILITIES,
                "replay": verifier.REPLAY_CAPABILITIES,
            },
        )

    def test_rejects_every_production_boolean_capability_if_true(self) -> None:
        cases = (
            ("intake", name)
            for name in verifier.INTAKE_CAPABILITIES
            if name.endswith("_ENABLED")
        )
        replay_cases = (("replay", name) for name in verifier.REPLAY_CAPABILITIES)
        for component, name in (*cases, *replay_cases):
            with self.subTest(component=component, name=name):
                intake = copy.deepcopy(self.intake)
                replay = copy.deepcopy(self.replay)
                config = intake if component == "intake" else replay
                config["env"]["production"]["vars"][name] = "true"
                with self.assertRaisesRegex(
                    verifier.VerificationError, "not exactly disabled"
                ):
                    verifier.verify_disabled_configs(intake, replay)

    def test_rejects_durable_mode_new_capability_and_lease_material(self) -> None:
        for mutation in ("mode", "new", "lease"):
            with self.subTest(mutation=mutation):
                intake = copy.deepcopy(self.intake)
                variables = intake["env"]["production"]["vars"]
                if mutation == "mode":
                    variables["INTAKE_ENABLEMENT_MODE"] = "durable"
                elif mutation == "new":
                    variables["NEW_PRODUCTION_API_ENABLED"] = "false"
                else:
                    variables["INTAKE_LEASE_EVENT_ID"] = "event"
                with self.assertRaises(verifier.VerificationError):
                    verifier.verify_disabled_configs(intake, self.replay)

    def test_materializes_only_blobs_from_the_exact_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            repository = root / "repository"
            output = root / "output"
            repository.mkdir()
            output.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "fixture"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "config",
                    "user.email",
                    "fixture@example.com",
                ],
                check=True,
            )
            for relative, value in (
                (verifier.INTAKE_PATH, self.intake),
                (verifier.REPLAY_PATH, self.replay),
            ):
                path = repository / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "server"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "commit", "-q", "-m", "fixture"],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            report = verifier.materialize(repository, commit, output)
            self.assertEqual(report["expected_commit"], commit)
            self.assertEqual(
                (output / "intake.jsonc").read_text(encoding="utf-8"),
                json.dumps(self.intake),
            )
            with self.assertRaises(verifier.VerificationError):
                verifier.read_exact_configs(repository, "a" * 40)


if __name__ == "__main__":
    unittest.main()
