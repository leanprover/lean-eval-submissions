from __future__ import annotations

import hashlib
import json
import pathlib
import unittest

from scripts.historical_replay_controller import validate_qualification


ROOT = pathlib.Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "configuration/historical-public-replay-profile-matrix-v1.json"
PROFILE_DIRECTORY = ROOT / "evidence/public-replay/profiles"
MISSING_BENCHMARK_COMMIT = "9921ef5c57b8d9eaa31b64a7e2d68cf53a388c66"


class HistoricalPublicProfileFreezeTests(unittest.TestCase):
    def test_current_matrix_has_exactly_34_frozen_profiles(self) -> None:
        matrix_raw = MATRIX_PATH.read_bytes()
        matrix = json.loads(matrix_raw)
        matrix_sha256 = hashlib.sha256(matrix_raw).hexdigest()
        entries = {entry["benchmark_commit"]: entry for entry in matrix["images"]}
        self.assertEqual(len(entries), matrix["image_count"])

        profiles: dict[str, pathlib.Path] = {}
        for path in sorted(PROFILE_DIRECTORY.glob("*.json")):
            raw = path.read_bytes()
            profile = json.loads(raw)
            if (
                profile.get("plan_sha256") != matrix["plan_sha256"]
                or profile.get("profile_matrix_sha256") != matrix_sha256
            ):
                continue

            validate_qualification(profile, raw)
            self.assertEqual(path.stem, profile["execution_profile_digest"])
            commit = profile["benchmark_commit"]
            self.assertIn(commit, entries)
            self.assertNotIn(commit, profiles)

            entry = entries[commit]
            self.assertEqual(profile["benchmark_tree"], entry["benchmark_tree"])
            execution_profile = profile["execution_profile"]
            for field, expected in entry["profile_lock"].items():
                if field not in {"benchmark_commit", "benchmark_repository"}:
                    self.assertEqual(execution_profile[field], expected)
            profiles[commit] = path

        self.assertEqual(len(profiles), 34)
        self.assertEqual(set(entries) - set(profiles), {MISSING_BENCHMARK_COMMIT})


if __name__ == "__main__":
    unittest.main()
