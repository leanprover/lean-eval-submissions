#!/usr/bin/env python3
"""Exercise measured acceptance through real Lean, Landrun, export, and nanoda."""

from __future__ import annotations
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from collect_acceptance_metrics import collect


def smoke(comparator, landrun, exporter, checker):
    with tempfile.TemporaryDirectory(prefix="lean-eval-compute-smoke-") as directory:
        root = pathlib.Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        metrics = root / "metrics"
        metrics.mkdir()
        (workspace / "lean-toolchain").write_text("leanprover/lean4:v4.34.0\n")
        (workspace / "lakefile.toml").write_text(
            'name = "compute_smoke"\n\n[[lean_lib]]\nname = "Challenge"\n\n[[lean_lib]]\nname = "Solution"\n\n[[lean_lib]]\nname = "RejectedSolution"\n'
        )
        for name in ("Challenge", "Solution"):
            (workspace / (name + ".lean")).write_text(
                "theorem target : (2 : Nat) + 2 = 4 := by rfl\n"
            )
        (workspace / "RejectedSolution.lean").write_text(
            "theorem target : (2 : Nat) + 2 = 5 := by rfl\n"
        )
        environment = os.environ.copy()
        environment.update(
            COMPARATOR_LANDRUN=str(landrun),
            COMPARATOR_LEAN4EXPORT=str(exporter),
            COMPARATOR_NANODA=str(checker),
        )
        prefix = subprocess.run(
            ["lean", "--print-prefix"],
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        environment["PATH"] = (
            str(pathlib.Path(prefix) / "bin") + os.pathsep + environment["PATH"]
        )
        subprocess.run(
            ["lake", "update"],
            cwd=workspace,
            env=environment,
            check=True,
            capture_output=True,
        )
        config = {
            "challenge_module": "Challenge",
            "solution_module": "Solution",
            "theorem_names": ["target"],
            "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
            "enable_nanoda": True,
        }
        for name, expected in (("Solution", 0), ("RejectedSolution", 1)):
            config["solution_module"] = name
            config["measurement_command"] = [
                sys.executable,
                str(pathlib.Path(__file__).with_name("measure_acceptance_phase.py")),
                "--metrics-path",
                str(metrics / (name + ".json")),
                "--landrun",
                str(landrun),
                "--",
            ]
            (workspace / "config.json").write_text(json.dumps(config))
            result = subprocess.run(
                ["lake", "env", str(comparator), "config.json"],
                cwd=workspace,
                env=environment,
                capture_output=True,
                text=True,
            )
            if (result.returncode == 0) != (expected == 0):
                raise ValueError(
                    "comparator verdict changed: " + result.stdout + result.stderr
                )
            measured = collect(metrics, [name])[name]
            if name == "Solution" and measured["status"] != "available":
                raise ValueError(
                    "accepted fixture did not produce complete measurements"
                )
            if name == "RejectedSolution" and measured["status"] != "failed":
                raise ValueError("rejected fixture published partial totals")
            print(
                json.dumps(
                    {
                        "fixture": name,
                        "accepted": result.returncode == 0,
                        "measurement": measured,
                    },
                    sort_keys=True,
                )
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("comparator", "landrun", "exporter", "checker"):
        parser.add_argument("--" + name, type=pathlib.Path, required=True)
    args = parser.parse_args()
    smoke(
        *(
            getattr(args, name).resolve()
            for name in ("comparator", "landrun", "exporter", "checker")
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
