#!/usr/bin/env python3
"""Trusted best-effort acceptance phase timer; never changes the proof exit code."""

from __future__ import annotations
import argparse
import importlib.machinery
import importlib.util
import pathlib
import sys

LOADER = importlib.machinery.SourceFileLoader(
    "replay_measure",
    str(pathlib.Path(__file__).parents[1] / "server/replay-image/replay-measure"),
)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
measure = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(measure)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics-path", type=pathlib.Path, required=True)
    parser.add_argument("--landrun", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    # The explicit separator precedes the comparator's --phase arguments.
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    measure.LANDRUN = args.landrun
    measure.METRICS_PATH = args.metrics_path
    phase, command = measure.parse_invocation(command)
    # Counter failures must not suppress a build. open_counter already converts
    # unsupported/denied PMUs into a reason and leaves wall-clock measurement on.
    returncode, wall, counter = measure.run_measured(command)
    try:
        measure.save_metrics(
            measure.aggregate(measure.load_metrics(), phase, wall, counter, returncode)
        )
    except (measure.MeasurementError, OSError):
        # Invalidation is best effort too: a read-only or full filesystem must
        # not turn a successfully checked proof into a rejection. Collection
        # also requires every expected invocation, so stale partials are refused.
        try:
            args.metrics_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            args.metrics_path.with_suffix(".failed").touch()
        except OSError:
            pass
        print("acceptance measurement unavailable", file=sys.stderr)
    return 128 - returncode if returncode < 0 else returncode


if __name__ == "__main__":
    raise SystemExit(main())
