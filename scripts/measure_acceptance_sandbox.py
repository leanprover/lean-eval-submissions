#!/usr/bin/env python3
"""Transparent trusted bubblewrap adapter for the toolchain's Lake comparator."""

from __future__ import annotations
import os
import pathlib
import sys
from measure_acceptance_phase import main as measure_phase


def phase_for(arguments, tools, solution):
    try:
        command = arguments[arguments.index("--") + 1 :]
    except ValueError:
        return None
    if not command:
        return None
    executable = str(pathlib.Path(command[0]).resolve())
    if executable == tools["lake"] and command[1:] == ["build", solution]:
        return "build"
    if executable == tools["leanexport"] and command[1:2] == [solution]:
        return "build"
    if executable in (tools["nanoda_bin"], tools["leanchecker"]):
        return "checker"
    return None


def install(metrics_dir, workspaces, solutions, sandbox, lean_prefix):
    tools = {
        name: str((lean_prefix / "bin" / name).resolve())
        for name in ("lake", "leanexport", "nanoda_bin", "leanchecker")
    }
    wrapper = metrics_dir / "bwrap"
    configuration = (
        str(sandbox),
        str(metrics_dir),
        str(workspaces.resolve()),
        solutions,
        tools,
    )
    wrapper.write_text(
        "#!"
        + sys.executable
        + "\nimport os, sys\n"
        + "try:\n    sys.path.insert(0, "
        + repr(str(pathlib.Path(__file__).parent))
        + ")\n    from measure_acceptance_sandbox import run\n"
        + "except Exception:\n    os.execv("
        + repr(str(sandbox))
        + ", ["
        + repr(str(sandbox))
        + ", *sys.argv[1:]])\n"
        + "raise SystemExit(run(*"
        + repr(configuration)
        + ", sys.argv[1:]))\n"
    )
    wrapper.chmod(0o500)
    return wrapper


def run(sandbox, metrics_dir, workspaces, solutions, tools, arguments):
    try:
        cwd = pathlib.Path.cwd().resolve()
        phase = (
            phase_for(arguments, tools, solutions.get(cwd.name))
            if (cwd.parent == pathlib.Path(workspaces) and cwd.name in solutions)
            else None
        )
    except Exception:
        phase = None
    if phase is None:
        os.execv(sandbox, [sandbox, *arguments])
    return measure_phase(
        [
            "--metrics-path",
            str(pathlib.Path(metrics_dir) / (cwd.name + ".json")),
            "--landrun",
            sandbox,
            "--",
            "--phase",
            phase,
            "--",
            sandbox,
            *arguments,
        ]
    )
