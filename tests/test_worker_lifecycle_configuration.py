from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from scripts.worker_lifecycle_configuration import (
    LAUNCH_FLAGS,
    LifecycleConfigurationError,
    read_lifecycle_state,
)


def configuration(
    state: object = "false", environment: str = "production"
) -> dict[str, object]:
    variables: dict[str, object] = {
        "DEPLOYMENT_ENVIRONMENT": environment,
        "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED": "false",
        "RELEASE_OPT_OUT_API_ENABLED": "false",
        "RESULT_AMENDMENT_MAINTAINERS": (
            '[{"github_id":477956,"login":"kim-em"}]' if state == "true" else "[]"
        ),
        "MODEL_IDENTITY_MAINTAINERS": (
            '[{"github_id":477956,"login":"kim-em"}]' if state == "true" else "[]"
        ),
    }
    variables.update({name: state for name in LAUNCH_FLAGS})
    return {"env": {environment: {"vars": variables}}}


class WorkerLifecycleConfigurationTests(unittest.TestCase):
    def write(self, root: pathlib.Path, value: object) -> pathlib.Path:
        path = root / "wrangler.jsonc"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_reads_bounded_disabled_and_enabled_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for state in ("false", "true"):
                with self.subTest(state=state):
                    path = self.write(root, configuration(state))
                    self.assertEqual(read_lifecycle_state(path, "production"), state)

    def test_tracked_configuration_has_reviewed_launch_state(self) -> None:
        tracked = pathlib.Path(__file__).resolve().parent.parent / "server/wrangler.jsonc"
        self.assertEqual(read_lifecycle_state(tracked, "staging"), "false")
        self.assertEqual(read_lifecycle_state(tracked, "production"), "false")

    def test_rejects_partial_launch_or_consolidation_enablement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            partial = configuration("true")
            partial["env"]["production"]["vars"][LAUNCH_FLAGS[0]] = "false"
            with self.assertRaisesRegex(LifecycleConfigurationError, "all be the same"):
                read_lifecycle_state(self.write(root, partial), "production")

            consolidation = configuration("false")
            consolidation["env"]["production"]["vars"][
                "MODEL_IDENTITY_CONSOLIDATION_API_ENABLED"
            ] = "true"
            with self.assertRaisesRegex(LifecycleConfigurationError, "must remain false"):
                read_lifecycle_state(self.write(root, consolidation), "production")

            opt_out = configuration("true")
            opt_out["env"]["production"]["vars"][
                "RELEASE_OPT_OUT_API_ENABLED"
            ] = "true"
            with self.assertRaisesRegex(LifecycleConfigurationError, "must remain false"):
                read_lifecycle_state(self.write(root, opt_out), "production")

    def test_rejects_wrong_maintainer_cardinality_or_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            cases = (
                ("true", "RESULT_AMENDMENT_MAINTAINERS", "[]"),
                ("false", "MODEL_IDENTITY_MAINTAINERS", '[{"github_id":1,"login":"a"}]'),
                ("true", "MODEL_IDENTITY_MAINTAINERS", '[{"github_id":true,"login":"a"}]'),
                ("true", "MODEL_IDENTITY_MAINTAINERS", '[{"github_id":1,"login":"A"}]'),
                ("true", "MODEL_IDENTITY_MAINTAINERS", '[ {"github_id":1,"login":"a"} ]'),
            )
            for state, name, value in cases:
                with self.subTest(state=state, name=name, value=value):
                    candidate = configuration(state)
                    candidate["env"]["production"]["vars"][name] = value
                    with self.assertRaises(LifecycleConfigurationError):
                        read_lifecycle_state(self.write(root, candidate), "production")

    def test_rejects_missing_wrong_environment_and_noncanonical_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            cases: tuple[object, ...] = (
                configuration(environment="staging"),
                {"env": {"production": {"vars": {}}}},
                configuration(True),
                configuration("enabled"),
            )
            for candidate in cases:
                with (
                    self.subTest(candidate=candidate),
                    self.assertRaises(LifecycleConfigurationError),
                ):
                    read_lifecycle_state(self.write(root, candidate), "production")

    def test_rejects_duplicate_keys_nonstandard_numbers_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            duplicate = root / "duplicate.jsonc"
            duplicate.write_text(
                '{"env":{"production":{"vars":{'
                '"DEPLOYMENT_ENVIRONMENT":"production",'
                '"LEGACY_RESULT_OWNER_API_ENABLED":"false",'
                '"LEGACY_RESULT_OWNER_API_ENABLED":"true"}}}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LifecycleConfigurationError, "duplicate key"):
                read_lifecycle_state(duplicate, "production")

            nonstandard = root / "nonstandard.jsonc"
            nonstandard.write_text('{"extra":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(LifecycleConfigurationError, "non-standard"):
                read_lifecycle_state(nonstandard, "production")

            target = self.write(root, configuration())
            link = root / "linked.jsonc"
            link.symlink_to(target)
            with self.assertRaisesRegex(LifecycleConfigurationError, "regular file"):
                read_lifecycle_state(link, "production")


if __name__ == "__main__":
    unittest.main()
