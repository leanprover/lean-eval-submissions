from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from prepare_historical_final_delta_activation import (
    ActivationError,
    build,
    canonical,
    verify_crosswalk_blob,
)
from prepare_historical_final_delta_state import (
    document_bytes,
    expectation,
    load_final_expectation,
)
from test_prepare_historical_final_delta_packet import Fixture


class FinalDeltaStateTests(unittest.TestCase):
    def test_substituted_crosswalk_commit_and_blob_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
            subprocess.run(
                ["git", "-C", root, "config", "user.name", "test"], check=True
            )
            subprocess.run(
                ["git", "-C", root, "config", "user.email", "test@example.com"],
                check=True,
            )
            raw = canonical({"crosswalk": 1})
            (root / "crosswalk.json").write_bytes(raw)
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                ["git", "-C", root, "commit", "-q", "-m", "crosswalk"],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", root, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            binding = {
                "commit": commit,
                "path": "crosswalk.json",
                "sha256": __import__("hashlib").sha256(raw).hexdigest(),
            }
            verify_crosswalk_blob(root, commit, binding)
            with self.assertRaisesRegex(ActivationError, "locator"):
                verify_crosswalk_blob(root, "f" * 40, binding)
            binding["sha256"] = "0" * 64
            with self.assertRaisesRegex(ActivationError, "differs"):
                verify_crosswalk_blob(root, commit, binding)

    def test_missing_profiles_emit_only_conditional_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(pathlib.Path(temporary))
            packet = fixture.build()
            raw = canonical(packet)
            requirements, public_plan, private_plan = build(
                preparation=packet,
                preparation_raw=raw,
                preparation_commit="a" * 40,
                preparation_path="evidence/historical-replay/final-delta-preparations/"
                + __import__("hashlib").sha256(raw).hexdigest()
                + ".json",
                crosswalk_commit=packet["classification_inputs"]["private_crosswalk"][
                    "commit"
                ],
                public={},
                private={},
                benchmarks={},
            )
        self.assertEqual(requirements["activation_status"], "blocked")
        self.assertEqual(len(requirements["missing"]), 2)
        self.assertIsNone(public_plan)
        self.assertIsNone(private_plan)
        self.assertIn("one-shot", requirements["conditional_action"])

    def test_activation_rejects_substituted_crosswalk_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            packet = Fixture(pathlib.Path(temporary)).build()
            raw = canonical(packet)
            with self.assertRaisesRegex(ActivationError, "crosswalk commit"):
                build(
                    preparation=packet,
                    preparation_raw=raw,
                    preparation_commit="a" * 40,
                    preparation_path="evidence/historical-replay/final-delta-preparations/"
                    + __import__("hashlib").sha256(raw).hexdigest()
                    + ".json",
                    crosswalk_commit="b" * 40,
                    public={},
                    private={},
                    benchmarks={},
                )

    def test_dynamic_expectation_accepts_unavailable_and_zero_task_lane(self) -> None:
        public_plan = {
            "entries": [
                {"disposition": "unavailable"},
                {"disposition": "replayable"},
            ]
        }
        private_plan = {"entries": [{"classification": "archive_not_found"}]}
        value = expectation(public_plan, private_plan)
        self.assertEqual(value["total_event_count"], 5)
        self.assertEqual(value["total_task_count"], 1)
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "expectation.json"
            path.write_bytes(document_bytes(value))
            self.assertEqual(load_final_expectation(path), value)


if __name__ == "__main__":
    unittest.main()
