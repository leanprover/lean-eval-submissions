"""Every toolchain contract in this repository agrees with the shared vectors.

`contracts/toolchain-vectors-v1.json` is the canonical copy: this repository is
public, so leanprover/lean-eval-state (private) mirrors it with a drift check
in its CI, and leanprover/lean-eval checks its pinned toolchain against it. The
Worker binds to the same file in `server/test/toolchain-vectors.test.ts`.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
# The historical replay scripts import their siblings by bare name, as they
# run from scripts/ on the command line; the modules themselves are imported
# through the `scripts` package like every other test does, so each script
# stays a single module instance across the suite.
sys.path.insert(0, str(ROOT / "scripts"))
VECTORS = json.loads((ROOT / "contracts" / "toolchain-vectors-v1.json").read_text(encoding="utf-8"))
# Schemas whose toolchain patterns state the general grammar. The public replay
# smoke evidence (`public-replay-smoke-evidence-v1`) is deliberately bound to a
# fixed release toolchain and is not part of this contract.
BOUND_SCHEMAS = (
    "evaluation-completion-v1.schema.json",
    "replay-queue-v1.schema.json",
    "replay-execution-request-v1.schema.json",
    "replay-execution-profile-v1.schema.json",
    "historical-public-replay-toolchains-v1.schema.json",
    "historical-public-replay-profile-matrix-v1.schema.json",
    "historical-public-replay-plan-v1.schema.json",
    "historical-public-runner-handoff-v1.schema.json",
    "historical-private-profile-qualification-v1.schema.json",
    "historical-private-replay-plan-v1.schema.json",
)
# Scripts with their own toolchain pattern. `public_replay_smoke.py` is bound
# to the fixed smoke toolchain like its evidence schema.
BOUND_SCRIPTS = (
    ("scripts.build_evaluation_completion", "TOOLCHAIN"),
    ("scripts.replay_orchestrator", "TOOLCHAIN"),
    ("scripts.replay_orchestrator", "HISTORICAL_TOOLCHAIN"),
    ("scripts.build_public_replay_toolchain_registry", "TOOLCHAIN"),
    ("scripts.prepare_public_replay_plan", "TOOLCHAIN"),
    ("scripts.historical_replay_controller", "TOOLCHAIN"),
    ("scripts.historical_public_runner", "TOOLCHAIN"),
)


def schema_matches(pattern: str, value: str) -> bool:
    """Evaluate a JSON Schema pattern the way the Worker and the schemas mean it.

    JSON Schema patterns are ECMA-262 regular expressions where `$` is the end
    of input. Python's `re.search` lets `$` match before a trailing newline, so
    it is replaced by `\Z` here; the vectors include a trailing-newline string
    precisely to pin that down.
    """
    assert pattern.startswith("^") and pattern.endswith("$"), pattern
    return re.search(pattern[:-1] + r"\Z", value) is not None


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

    def test_script_patterns(self) -> None:
        for module_name, attribute in BOUND_SCRIPTS:
            pattern = getattr(importlib.import_module(module_name), attribute)
            with self.subTest(script=f"{module_name}.{attribute}"):
                self._check(f"{module_name}.{attribute}", lambda t, p=pattern: p.fullmatch(t) is not None)

    def test_schema_patterns(self) -> None:
        for name in BOUND_SCHEMAS:
            document = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            patterns = _patterns(document, name)
            self.assertTrue(patterns, f"{name} carries no toolchain pattern")
            for path, pattern in patterns:
                with self.subTest(schema=path):
                    self._check(path, lambda t, p=pattern: schema_matches(p, t))


if __name__ == "__main__":
    unittest.main()
