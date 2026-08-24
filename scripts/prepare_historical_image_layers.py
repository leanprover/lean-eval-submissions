#!/usr/bin/env python3
"""Separate and canonicalize the large historical replay image layers."""

from __future__ import annotations

import argparse
import os
import pathlib


CANONICAL_MTIME_NS = 0


class LayerPreparationError(ValueError):
    """The staged runtime tree cannot be represented by the layer contract."""


def _canonicalize_tree(root: pathlib.Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise LayerPreparationError(f"runtime layer is not one real directory: {root}")

    for directory, child_directories, files in os.walk(root, followlinks=False):
        directory_path = pathlib.Path(directory)
        for name in child_directories + files:
            path = directory_path / name
            os.utime(
                path,
                ns=(CANONICAL_MTIME_NS, CANONICAL_MTIME_NS),
                follow_symlinks=False,
            )
        os.utime(
            directory_path,
            ns=(CANONICAL_MTIME_NS, CANONICAL_MTIME_NS),
            follow_symlinks=False,
        )


def prepare_runtime_layers(runtime_root: pathlib.Path) -> None:
    if not runtime_root.is_dir() or runtime_root.is_symlink():
        raise LayerPreparationError("runtime root must be one real directory")

    benchmark = runtime_root / "benchmark"
    packages = benchmark / ".lake" / "packages"
    package_layer = runtime_root / "benchmark-packages"
    required_layers = (
        benchmark,
        runtime_root / "bin",
        runtime_root / "home",
        runtime_root / "profile",
    )
    if any(not path.is_dir() or path.is_symlink() for path in required_layers):
        raise LayerPreparationError("runtime tree is missing one required real directory")
    if not packages.is_dir() or packages.is_symlink():
        raise LayerPreparationError("benchmark package store must be one real directory")
    if package_layer.exists() or package_layer.is_symlink():
        raise LayerPreparationError("benchmark package layer already exists")

    packages.rename(package_layer)
    if packages.exists() or packages.is_symlink():
        raise LayerPreparationError("benchmark package store was not separated")
    # The profile-specific benchmark layer creates the complete destination
    # directory chain first. The package COPY can then contain only canonical
    # source metadata and package content, without implicit parent directories.
    packages.mkdir()

    expected_layers = {
        "benchmark",
        "benchmark-packages",
        "bin",
        "home",
        "profile",
    }
    observed_layers = {path.name for path in runtime_root.iterdir()}
    if observed_layers != expected_layers:
        raise LayerPreparationError("runtime root entries are not the closed layer set")
    for layer in sorted(runtime_root.iterdir()):
        _canonicalize_tree(layer)
    os.utime(
        runtime_root,
        ns=(CANONICAL_MTIME_NS, CANONICAL_MTIME_NS),
        follow_symlinks=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        prepare_runtime_layers(args.runtime_root)
    except LayerPreparationError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
