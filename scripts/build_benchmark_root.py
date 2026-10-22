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

  1. `lake -v --no-build build TauCeti`: the whole TauCeti library, so a submission may
     import any TauCeti module, not only those the benchmark imports.
  2. `lake build`: the benchmark root itself.

Later workspace builds, including comparator's sandboxed build (whose
environment is reduced to PATH, HOME and LEAN_ABORT_ON_PANIC), then find
TauCeti up to date on disk and need neither the artifact cache nor the
network.

TauCeti must never be compiled here. Cache-only materialization prevents Lake
from falling back to compilation, and verbose logs retain the underlying
artifact-download errors. Individual downloads have bounded retries and atomic
publication through lake_cache_curl.py. The compilation guard also remains on
the subsequent root build.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time

FETCH_SCRIPT = pathlib.Path("scripts") / "fetch_dependency_caches.sh"
TAUCETI = "TauCeti"
# Ordinary builds report `Built` or `Building` for compilation. Cache-only
# failures can also say `Building` without ever running the compiler.
COMPILED_TAUCETI = re.compile(r"\bBuil(?:t|ding) TauCeti\b")
BUILT_TAUCETI = re.compile(r"\bBuilt TauCeti\b")
EXPORT_KEY = re.compile(r"LAKE_[A-Z_]+")
REQUIRED_EXPORTS = {"LAKE_CONFIG", "LAKE_ARTIFACT_CACHE"}
CACHE_CURL = pathlib.Path(__file__).resolve().with_name("lake_cache_curl.py")
CACHE_RESTORE_ATTEMPTS = 2


class BuildRootError(Exception):
    """The benchmark root could not be built without compiling TauCeti."""


class CacheRestoreError(BuildRootError):
    """Cache-only dependency materialization failed without compiling anything."""


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
        if not sep or (key != "CURL" and not EXPORT_KEY.fullmatch(key)):
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
    env["CURL"] = str(CACHE_CURL)
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
    cache_only: bool = False,
) -> None:
    args = ["lake", *(["-v", "--no-build"] if cache_only else []), "build", *targets]
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
    compile_pattern = BUILT_TAUCETI if cache_only else COMPILED_TAUCETI
    for line in process.stdout:
        sys.stdout.write(line)
        # --no-build labels an out-of-date target "Building" even though its
        # compiler was never run. Retain those targets' verbose diagnostics.
        if forbid_tauceti_compile and compile_pattern.search(line):
            compiled = line.strip()
            process.terminate()
            break
    process.stdout.close()
    returncode = process.wait()
    sys.stdout.flush()
    if compiled is not None:
        raise BuildRootError(
            f"`{' '.join(args)}` compiled TauCeti instead of downloading it "
            f"({compiled!r}). Inspect the cache download diagnostics and verify "
            "the benchmark's toolchain and Mathlib match its TauCeti commit."
        )
    if returncode != 0:
        if cache_only:
            raise CacheRestoreError(
                f"`{' '.join(args)}` could not restore the published dependency cache "
                f"after bounded download retries (exit code {returncode}). "
                "No dependency compilation was permitted. Inspect the verbose "
                "diagnostics above for unavailable artifacts or out-of-date "
                "dependencies, and check the pinned toolchain, Mathlib and cache coverage."
            )
        raise BuildRootError(f"`{' '.join(args)}` failed with exit code {returncode}")


def build_benchmark_root(root: pathlib.Path) -> None:
    if not pins_tauceti(root):
        lake_build(root, [], dict(os.environ), forbid_tauceti_compile=False)
        return
    env = dict(os.environ)
    env.pop("GITHUB_ENV", None)
    env.update(fetch_cache_settings(root))
    env["CURL"] = str(CACHE_CURL)
    env["LAKE_RESTORE_ARTIFACTS"] = "true"
    for attempt in range(1, CACHE_RESTORE_ATTEMPTS + 1):
        try:
            lake_build(root, [TAUCETI], env, forbid_tauceti_compile=True, cache_only=True)
            break
        except CacheRestoreError:
            if attempt == CACHE_RESTORE_ATTEMPTS:
                raise
            print(
                f"::notice::Cache-only restoration failed (attempt {attempt} of "
                f"{CACHE_RESTORE_ATTEMPTS}); retrying without compiling dependencies.",
                flush=True,
            )
            time.sleep(5)
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
