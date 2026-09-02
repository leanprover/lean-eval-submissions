from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from scripts.worker_intake_configuration import (
    IntakeConfigurationError,
    read_intake_state,
)


def configuration(state: object = "false", environment: str = "production") -> dict:
    return {
        "env": {
            environment: {
                "vars": {
                    "DEPLOYMENT_ENVIRONMENT": environment,
                    "INTAKE_ENABLED": state,
                    "INTAKE_ENABLEMENT_MODE": (
                        "durable" if state == "true" else "disabled"
                    ),
                }
            }
        }
    }


class WorkerIntakeConfigurationTests(unittest.TestCase):
    def write(self, root: pathlib.Path, value: object) -> pathlib.Path:
        path = root / "wrangler.jsonc"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_reads_only_canonical_string_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for state in ("false", "true"):
                with self.subTest(state=state):
                    path = self.write(root, configuration(state))
                    self.assertEqual(read_intake_state(path, "production"), state)

            staging = self.write(root, configuration("false", "staging"))
            self.assertEqual(read_intake_state(staging, "staging"), "false")

    def test_tracked_configuration_reports_reviewed_states(self) -> None:
        tracked = pathlib.Path(__file__).resolve().parent.parent / "server/wrangler.jsonc"
        expected = {"staging": "false", "production": "true"}
        for environment, state in expected.items():
            with self.subTest(environment=environment):
                self.assertEqual(read_intake_state(tracked, environment), state)

    def test_rejects_missing_mismatched_or_non_string_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for value in (
                configuration(environment="staging"),
                configuration("enabled"),
                configuration(False),
                configuration([]),
                {"env": {"production": {"vars": {"INTAKE_ENABLED": "false"}}}},
                {
                    "env": {
                        "production": {
                            "vars": {
                                "DEPLOYMENT_ENVIRONMENT": "production",
                                "INTAKE_ENABLED": "false",
                                "INTAKE_ENABLEMENT_MODE": "durable",
                            }
                        }
                    }
                },
            ):
                with self.subTest(value=value):
                    path = self.write(root, value)
                    with self.assertRaises(IntakeConfigurationError):
                        read_intake_state(path, "production")

    def test_rejects_duplicate_keys_and_nonstandard_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            duplicate = root / "duplicate.jsonc"
            duplicate.write_text(
                '{"env":{"production":{"vars":{'
                '"DEPLOYMENT_ENVIRONMENT":"production",'
                '"INTAKE_ENABLEMENT_MODE":"disabled",'
                '"INTAKE_ENABLED":"false","INTAKE_ENABLED":"true"}}}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(IntakeConfigurationError, "duplicate key"):
                read_intake_state(duplicate, "production")

            nonstandard = root / "nonstandard.jsonc"
            nonstandard.write_text(
                '{"extra":NaN,"env":{"production":{"vars":{'
                '"DEPLOYMENT_ENVIRONMENT":"production",'
                '"INTAKE_ENABLED":"false",'
                '"INTAKE_ENABLEMENT_MODE":"disabled"}}}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(IntakeConfigurationError, "non-standard"):
                read_intake_state(nonstandard, "production")

    def test_rejects_malformed_oversize_and_symlink_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            malformed = root / "malformed.jsonc"
            malformed.write_text("not json", encoding="utf-8")
            with self.assertRaises(IntakeConfigurationError):
                read_intake_state(malformed, "production")

            empty = root / "empty.jsonc"
            empty.write_bytes(b"")
            with self.assertRaisesRegex(IntakeConfigurationError, "configuration is empty"):
                read_intake_state(empty, "production")

            oversize = root / "oversize.jsonc"
            oversize.write_bytes(b" " * (1024 * 1024 + 1))
            with self.assertRaises(IntakeConfigurationError):
                read_intake_state(oversize, "production")

            target = self.write(root, configuration())
            link = root / "linked.jsonc"
            link.symlink_to(target)
            with self.assertRaises(IntakeConfigurationError):
                read_intake_state(link, "production")

    def test_rejects_unregistered_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(pathlib.Path(temporary), configuration())
            with self.assertRaises(IntakeConfigurationError):
                read_intake_state(path, "preview")

    def test_rejects_tracked_lease_material(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            value = configuration("true")
            value["env"]["production"]["vars"]["INTAKE_LEASE_EXPIRES_AT"] = "2000000000"
            path = self.write(root, value)
            with self.assertRaisesRegex(IntakeConfigurationError, "lease variables"):
                read_intake_state(path, "production")


if __name__ == "__main__":
    unittest.main()
