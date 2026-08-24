from __future__ import annotations

import importlib.util
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare_intake_enablement_lease",
    ROOT / "scripts" / "prepare_intake_enablement_lease.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LeaseTests(unittest.TestCase):
    def test_exact_controller_bound_material(self) -> None:
        with mock.patch.object(MODULE.secrets, "randbits", side_effect=[0x123, 0x456]):
            bindings, smoke = MODULE.prepare(
                controller_commit="a" * 40,
                controller_run_attempt="2",
                controller_run_id="123",
                state_commit="b" * 40,
                target_commit="a" * 40,
                now=1_800_000_000,
                nonce="c" * 64,
            )
        self.assertEqual(bindings["INTAKE_ENABLEMENT_MODE"], "leased")
        self.assertEqual(bindings["INTAKE_LEASE_EXPIRES_AT"], "1800000900")
        self.assertEqual(smoke["controller_run_attempt"], "2")
        self.assertEqual(smoke["controller_run_id"], "123")
        self.assertEqual(smoke["state_commit"], "b" * 40)
        self.assertRegex(str(smoke["event_id"]), r"^[0-9a-f-]{36}$")
        self.assertNotEqual(bindings["INTAKE_LEASE_NONCE_DIGEST"], smoke["nonce"])

    def test_rejects_cross_commit_or_noncanonical_controller(self) -> None:
        arguments = dict(
            controller_commit="a" * 40,
            controller_run_attempt="1",
            controller_run_id="1",
            state_commit="b" * 40,
            target_commit="a" * 40,
            now=1_800_000_000,
            nonce="c" * 64,
        )
        for name, value in (
            ("target_commit", "d" * 40),
            ("controller_run_id", "01"),
            ("controller_run_attempt", "0"),
            ("state_commit", "B" * 40),
            ("nonce", "secret"),
        ):
            with self.subTest(name=name):
                with self.assertRaises(MODULE.LeaseError):
                    MODULE.prepare(**{**arguments, name: value})

    def test_secret_output_is_exclusive_and_private_from_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "lease.json"
            MODULE._write_new(path, '{"nonce":"secret"}\n')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"nonce":"secret"}\n')
            with self.assertRaises(MODULE.LeaseError):
                MODULE._write_new(path, "replacement")

            target = pathlib.Path(temporary) / "target"
            target.write_text("preserve", encoding="utf-8")
            link = pathlib.Path(temporary) / "link"
            link.symlink_to(target)
            with self.assertRaises(MODULE.LeaseError):
                MODULE._write_new(link, "replacement")
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
