from __future__ import annotations

import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]


def materialize_results_tree(commit: str, destination: pathlib.Path) -> pathlib.Path:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            commit,
            "--",
            "results",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    relative_paths = [
        pathlib.PurePosixPath(raw.decode("utf-8"))
        for raw in completed.stdout.split(b"\0")
        if raw
    ]
    if not relative_paths:
        raise AssertionError("frozen Results tree is empty")
    results_root = destination / "results"
    results_root.mkdir()
    for relative in relative_paths:
        if (
            len(relative.parts) != 2
            or relative.parts[0] != "results"
            or (relative.name != ".gitkeep" and relative.suffix != ".json")
        ):
            raise AssertionError("frozen Results tree has an unexpected path")
        blob = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{commit}:{relative.as_posix()}"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        (destination / pathlib.Path(*relative.parts)).write_bytes(blob)
    return results_root
