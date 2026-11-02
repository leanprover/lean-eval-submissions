#!/usr/bin/env python3
"""Exercise measured acceptance through the real toolchain Lake comparator."""

from __future__ import annotations
import argparse
import json
import os
import pathlib
import subprocess
import tempfile
from collect_acceptance_metrics import collect
from measure_acceptance_sandbox import install


def smoke(sandbox):
    prefix = pathlib.Path(
        subprocess.check_output(["lean", "--print-prefix"], text=True).strip()
    )
    with tempfile.TemporaryDirectory(prefix="lean-eval-compute-smoke-") as directory:
        root = pathlib.Path(directory)
        workspaces = root / "workspaces"
        metrics = root / "metrics"
        workspaces.mkdir()
        metrics.mkdir()
        wrapper = install(
            metrics,
            workspaces,
            {"Solution": "Solution", "RejectedSolution": "Solution"},
            sandbox,
            prefix,
        )
        environment = dict(os.environ, COMPARATOR_BWRAP=str(wrapper))
        for name, valid in (("Solution", True), ("RejectedSolution", False)):
            workspace = workspaces / name
            workspace.mkdir()
            (workspace / "lean-toolchain").write_text("leanprover/lean4:v4.35.0-rc3\n")
            (workspace / "lakefile.toml").write_text(
                'name = "compute_smoke"\n[[lean_lib]]\nname = "Challenge"\n[[lean_lib]]\nname = "Solution"\n'
            )
            (workspace / "Challenge.lean").write_text(
                "theorem target : (2 : Nat) + 2 = 4 := by rfl\n"
            )
            (workspace / "Solution.lean").write_text(
                "theorem target : (2 : Nat) + 2 = "
                + ("4" if valid else "5")
                + " := by rfl\n"
            )
            config = {
                "challenge_module": "Challenge",
                "solution_module": "Solution",
                "theorem_names": ["target"],
                "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
                "external_kernels": {"nanoda": [str(prefix / "bin/nanoda_bin")]},
            }
            (workspace / "config.json").write_text(json.dumps(config))
            subprocess.run(
                [str(prefix / "bin/lake"), "update"],
                cwd=workspace,
                env=environment,
                check=True,
                capture_output=True,
            )
            result = subprocess.run(
                [str(prefix / "bin/lake"), "comparator", "--config", "config.json"],
                cwd=workspace,
                env=environment,
                text=True,
                capture_output=True,
            )
            if (result.returncode == 0) != valid:
                raise ValueError(
                    "comparator verdict changed: " + result.stdout + result.stderr
                )
            measured = collect(metrics, [name])[name]
            if (measured["status"] == "available") != valid:
                raise ValueError(
                    "measurement incomplete or partial: "
                    + result.stdout
                    + result.stderr
                    + json.dumps(measured)
                    + "\nRaw metrics: "
                    + (
                        (metrics / (name + ".json")).read_text()
                        if (metrics / (name + ".json")).exists()
                        else "absent"
                    )
                )
            print(
                json.dumps(
                    {"fixture": name, "accepted": valid, "measurement": measured},
                    sort_keys=True,
                )
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", type=pathlib.Path, required=True)
    args = parser.parse_args()
    smoke(args.sandbox.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
