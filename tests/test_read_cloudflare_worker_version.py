from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "read_cloudflare_worker_version"
VERSION = "aa781479-2f38-4524-858f-cd072399a5b3"


class ReadCloudflareWorkerVersionTests(unittest.TestCase):
    def test_retries_not_found_and_atomically_records_exact_json(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = pathlib.Path(raw_directory)
            config = directory / "wrangler.jsonc"
            config.write_text("{}\n", encoding="utf-8")
            counter = directory / "counter"
            wrangler = directory / "wrangler"
            wrangler.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    test "$*" = "versions view {VERSION} --config {config} --env production --json"
                    count=0
                    if [ -f {counter} ]; then count=$(cat {counter}); fi
                    count=$((count + 1))
                    printf '%s' "$count" > {counter}
                    if [ "$count" -lt 3 ]; then exit 1; fi
                    printf '{{"id":"{VERSION}"}}\n'
                    """
                ),
                encoding="utf-8",
            )
            wrangler.chmod(wrangler.stat().st_mode | stat.S_IXUSR)
            sleep = directory / "sleep"
            sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            sleep.chmod(sleep.stat().st_mode | stat.S_IXUSR)
            output = directory / "version.json"
            environment = {**os.environ, "PATH": f"{directory}:{os.environ['PATH']}"}

            subprocess.run(
                [SCRIPT, wrangler, config, "production", VERSION, output],
                check=True,
                env=environment,
            )

            self.assertEqual(counter.read_text(encoding="utf-8"), "3")
            self.assertEqual(json.loads(output.read_bytes()), {"id": VERSION})
            self.assertEqual(list(directory.glob("version.json.tmp.*")), [])

    def test_rejects_invalid_version_before_invoking_wrangler(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = pathlib.Path(raw_directory)
            config = directory / "wrangler.jsonc"
            config.write_text("{}\n", encoding="utf-8")
            wrangler = directory / "wrangler"
            wrangler.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
            wrangler.chmod(wrangler.stat().st_mode | stat.S_IXUSR)
            outcome = subprocess.run(
                [SCRIPT, wrangler, config, "production", "not-a-version", directory / "out"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(outcome.returncode, 2)
            self.assertIn(b"version ID is invalid", outcome.stderr)


if __name__ == "__main__":
    unittest.main()
