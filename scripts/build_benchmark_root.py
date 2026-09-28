#!/usr/bin/env python3
"""
Build the benchmark (lean-eval) root in the trusted phase of evaluation,
downloading TauCeti's build outputs instead of compiling them.

Usage: python scripts/build_benchmark_root.py <benchmark-root>

A benchmark whose `lake-manifest.json` pins no TauCeti package (every
lean-eval commit before TauCeti was added) gets a plain `lake build`.

Otherwise the benchmark must ship `scripts/fetch_dependency_caches.sh`,
which checks that the pinned TauCeti was built with the benchmark's Lean
toolchain and Mathlib, fetches TauCeti's Lake cache mappings and prints the
Lake settings that make builds download TauCeti's outputs. Those settings
apply only to the builds run here: they are not written to `$GITHUB_ENV`,
so nothing later in the job, trusted or not, reads the artifact cache or
reaches its service. Both builds below run with LAKE_RESTORE_ARTIFACTS=true,
which copies every downloaded output into the shared
`.lake/packages/<pkg>/.lake/build` tree:

  1. `lake build TauCeti`: the whole TauCeti library, so a submission may
     import any TauCeti module, not only those the benchmark imports.
  2. `lake build`: the benchmark root itself.

Later workspace builds, including comparator's sandboxed build (whose
environment is reduced to PATH, HOME and LEAN_ABORT_ON_PANIC), then find
TauCeti up to date on disk and need neither the artifact cache nor the
network.

TauCeti must never be compiled here. A cache miss makes Lake report
`Built TauCeti.<module>`; the first such line stops the build and fails,
instead of silently compiling TauCeti for half an hour.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

FETCH_SCRIPT = pathlib.Path("scripts") / "fetch_dependency_caches.sh"
TAUCETI = "TauCeti"
# Lake prints `<icon> [i/n] <verb> <caption>` for each finished job; the verb
# is `Built` (or `Building` if the job failed) only when it compiled.
COMPILED_TAUCETI = re.compile(r"\bBuil(?:t|ding) TauCeti\b")
EXPORT_KEY = re.compile(r"LAKE_[A-Z_]+")
REQUIRED_EXPORTS = {"LAKE_CONFIG", "LAKE_ARTIFACT_CACHE"}


class BuildRootError(Exception):
    """The benchmark root could not be built without compiling TauCeti."""


def pins_tauceti(root: pathlib.Path) -> bool:
    manifest = root / "lake-manifest.json"
    try:
        packages = json.loads(manifest.read_text(encoding="utf-8"))["packages"]
        names = {
            package["name"].removeprefix("«").removesuffix("»")
            for package in packages
        }
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise BuildRootError(f"cannot read {manifest}: {exc}") from exc
    return TAUCETI in names


def parse_exports(stdout: str) -> dict[str, str]:
    """Parse the single `export K=V ...` line the fetch script prints."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1 or not lines[0].startswith("export "):
        raise BuildRootError(
            f"{FETCH_SCRIPT} printed no single `export` line; got {stdout!r}"
        )
    exports: dict[str, str] = {}
    for word in shlex.split(lines[0])[1:]:
        key, sep, value = word.partition("=")
        if not sep or not EXPORT_KEY.fullmatch(key):
            raise BuildRootError(f"unexpected export {word!r} from {FETCH_SCRIPT}")
        exports[key] = value
    missing = REQUIRED_EXPORTS - set(exports)
    if missing or exports["LAKE_ARTIFACT_CACHE"] != "true":
        raise BuildRootError(
            f"{FETCH_SCRIPT} did not enable TauCeti's artifact cache: {exports!r}"
        )
    return exports


def fetch_cache_settings(root: pathlib.Path) -> dict[str, str]:
    if not (root / FETCH_SCRIPT).is_file():
        raise BuildRootError(
            f"{root / 'lake-manifest.json'} pins TauCeti but {root / FETCH_SCRIPT} "
            "is missing, so TauCeti's build outputs cannot be downloaded and it "
            "would be compiled. Refusing."
        )
    env = dict(os.environ)
    # Make the script print its settings instead of appending them to
    # $GITHUB_ENV, which would expose them to every later step.
    env.pop("GITHUB_ENV", None)
    result = subprocess.run(
        ["bash", str(FETCH_SCRIPT), "."],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BuildRootError(f"{FETCH_SCRIPT} failed with exit code {result.returncode}")
    return parse_exports(result.stdout)


def lake_build(
    root: pathlib.Path,
    targets: list[str],
    env: dict[str, str],
    *,
    forbid_tauceti_compile: bool,
) -> None:
    args = ["lake", "build", *targets]
    print(f"--- {' '.join(args)} ---", flush=True)
    process = subprocess.Popen(
        args,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    assert process.stdout is not None
    compiled: str | None = None
    for line in process.stdout:
        sys.stdout.write(line)
        if forbid_tauceti_compile and COMPILED_TAUCETI.search(line):
            compiled = line.strip()
            process.terminate()
            break
    process.stdout.close()
    returncode = process.wait()
    sys.stdout.flush()
    if compiled is not None:
        raise BuildRootError(
            f"`{' '.join(args)}` compiled TauCeti instead of downloading it "
            f"({compiled!r}). TauCeti's published cache does not cover this "
            "build; check that the benchmark pins exactly the toolchain and "
            "Mathlib of its TauCeti commit."
        )
    if returncode != 0:
        raise BuildRootError(f"`{' '.join(args)}` failed with exit code {returncode}")


def build_benchmark_root(root: pathlib.Path) -> None:
    if not pins_tauceti(root):
        lake_build(root, [], dict(os.environ), forbid_tauceti_compile=False)
        return
    env = dict(os.environ)
    env.pop("GITHUB_ENV", None)
    env.update(fetch_cache_settings(root))
    env["LAKE_RESTORE_ARTIFACTS"] = "true"
    lake_build(root, [TAUCETI], env, forbid_tauceti_compile=True)
    lake_build(root, [], env, forbid_tauceti_compile=True)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        build_benchmark_root(pathlib.Path(argv[0]).resolve())
    except BuildRootError as exc:
        print(f"::error::build_benchmark_root: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
