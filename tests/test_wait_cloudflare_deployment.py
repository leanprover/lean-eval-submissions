from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wait_cloudflare_deployment as waiter

VERSION = "aa781479-2f38-4524-858f-cd072399a5b3"
OLD_VERSION = "bb781479-2f38-4524-858f-cd072399a5b3"


class WaitCloudflareDeploymentTests(unittest.TestCase):
    def test_retries_stale_status_then_records_only_exact_deployed_version(self) -> None:
        stale = {"versions": [{"version_id": OLD_VERSION, "percentage": 100}]}
        exact = {"versions": [{"version_id": VERSION, "percentage": 100}]}
        replies = [
            subprocess.CompletedProcess([], 0, json.dumps(status))
            for status in (stale, exact)
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "status.json"
            with patch.object(waiter.subprocess, "run", side_effect=replies) as run:
                with patch.object(waiter.time, "sleep") as sleep:
                    waiter.wait_for_deployment("wrangler", Path("config"), "production", VERSION, output)
            self.assertEqual(json.loads(output.read_text()), exact)
            self.assertEqual(run.call_count, 2)
            sleep.assert_called_once_with(5)

    def test_never_accepts_persistent_stale_or_split_traffic(self) -> None:
        for status in (
            {"versions": [{"version_id": OLD_VERSION, "percentage": 100}]},
            {"versions": [{"version_id": VERSION, "percentage": 90},
                          {"version_id": OLD_VERSION, "percentage": 10}]},
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "status.json"
                result = subprocess.CompletedProcess([], 0, json.dumps(status))
                with patch.object(waiter.subprocess, "run", return_value=result):
                    with patch.object(waiter.time, "sleep"):
                        with self.assertRaisesRegex(ValueError, "did not converge"):
                            waiter.wait_for_deployment("wrangler", Path("config"), "production", VERSION, output)
                self.assertFalse(output.exists())

    def test_requires_unique_exact_acknowledged_deployment_identity(self) -> None:
        record = {"type": "deploy", "version": 1, "version_id": VERSION,
                  "worker_name": "lean-eval-submission-server", "wrangler_environment": "production"}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "deploy.jsonl"
            output.write_text(json.dumps(record) + "\n")
            self.assertEqual(waiter.deployed_version(output, "production", "lean-eval-submission-server"), VERSION)
            for records in ([], [record, record], [{**record, "version_id": None}],
                            [{**record, "wrangler_environment": "staging"}],
                            [{**record, "worker_name": "another-worker"}]):
                with self.subTest(records=records):
                    output.write_text("".join(json.dumps(item) + "\n" for item in records))
                    with self.assertRaises(ValueError):
                        waiter.deployed_version(output, "production", "lean-eval-submission-server")


if __name__ == "__main__":
    unittest.main()
