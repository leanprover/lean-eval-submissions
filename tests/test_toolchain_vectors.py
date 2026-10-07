"""Every toolchain contract in this repository agrees with the shared vectors.

`schemas/toolchain-vectors-v1.json` mirrors `schema/toolchain-vectors-v1.json`
in leanprover/lean-eval-state; CI diffs the two. The Worker checks the same
file in `server/test/toolchain-vectors.test.ts`.
"""
from __future__ import annotations

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "schemas" / "toolchain-vectors-v1.json").read_text(encoding="utf-8"))
# Schemas that describe live evaluation and replay records. Historical replay
# schemas and the public replay smoke evidence are bound to specific past
# toolchains and are not part of this contract.
LIVE_SCHEMAS = (
    "evaluation-completion-v1.schema.json",
    "replay-queue-v1.schema.json",
    "replay-execution-request-v1.schema.json",
    "replay-execution-profile-v1.schema.json",
)


def _patterns(document: object, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(document, dict):
        pattern = document.get("pattern")
        if isinstance(pattern, str) and "lean4:v" in pattern:
            found.append((path, pattern))
        for key, value in document.items():
            found.extend(_patterns(value, f"{path}/{key}"))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            found.extend(_patterns(value, f"{path}[{index}]"))
    return found


class ToolchainVectorTests(unittest.TestCase):
    def test_vectors_are_well_formed(self) -> None:
        self.assertEqual(VECTORS["schema_version"], 1)
        self.assertTrue(VECTORS["accepted"] and VECTORS["rejected"])
        self.assertFalse(set(VECTORS["accepted"]) & set(VECTORS["rejected"]))

    def _check(self, label: str, accepts) -> None:
        for toolchain in VECTORS["accepted"]:
            self.assertTrue(accepts(toolchain), f"{label} rejects {toolchain!r}")
        for toolchain in VECTORS["rejected"]:
            self.assertFalse(accepts(toolchain), f"{label} accepts {toolchain!r}")

    def test_completion_builder_pattern(self) -> None:
        from scripts.build_evaluation_completion import TOOLCHAIN

        self._check("build_evaluation_completion.TOOLCHAIN", lambda t: TOOLCHAIN.fullmatch(t) is not None)

    def test_replay_orchestrator_patterns(self) -> None:
        from scripts.replay_orchestrator import HISTORICAL_TOOLCHAIN, TOOLCHAIN

        self._check("replay_orchestrator.TOOLCHAIN", lambda t: TOOLCHAIN.fullmatch(t) is not None)
        self._check(
            "replay_orchestrator.HISTORICAL_TOOLCHAIN",
            lambda t: HISTORICAL_TOOLCHAIN.fullmatch(t) is not None,
        )

    def test_live_schema_patterns(self) -> None:
        checked = 0
        for name in LIVE_SCHEMAS:
            document = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            patterns = _patterns(document, name)
            self.assertTrue(patterns, f"{name} carries no toolchain pattern")
            for path, pattern in patterns:
                compiled = re.compile(pattern)
                self._check(path, lambda t, c=compiled: c.fullmatch(t) is not None)
                checked += 1
        self.assertGreaterEqual(checked, len(LIVE_SCHEMAS))


if __name__ == "__main__":
    unittest.main()
