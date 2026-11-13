from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FLOATING_IMAGE = re.compile(r"\bubuntu-latest\b")
PYTHON_PIN = re.compile(r"python-version:\s*'([^']+)'")


def workflow_lines() -> list[tuple[str, int, str]]:
    return [
        (path.name, number, line)
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
    ]


class RunnerImagePinTests(unittest.TestCase):
    # The workflows pin an exact Python patch release, and actions/setup-python
    # only publishes builds for some (Python, Ubuntu) pairs: for example 3.11.10
    # has no Ubuntu 26.04 build. When ubuntu-latest moved to 26.04, every job
    # that installed 3.11.10 failed. Pin the runner image explicitly, and bump
    # it together with the Python pin after checking
    # https://github.com/actions/python-versions/blob/main/versions-manifest.json.

    def test_no_workflow_uses_a_floating_runner_image(self) -> None:
        offenders = [
            f"{name}:{number}: {line.strip()}"
            for name, number, line in workflow_lines()
            if FLOATING_IMAGE.search(line)
        ]
        self.assertEqual(offenders, [])

    def test_workflows_share_one_python_pin(self) -> None:
        pins = {
            match.group(1)
            for _, _, line in workflow_lines()
            if (match := PYTHON_PIN.search(line))
        }
        self.assertEqual(len(pins), 1, pins)


if __name__ == "__main__":
    unittest.main()
