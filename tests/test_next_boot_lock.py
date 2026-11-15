"""Boot-lock transactions against a fake 32-byte misc block, never a device."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

from helpers import BIN


SCRIPT = BIN / "mu300-next-boot"
OFFSET = 2048


def boot_control(suffix, slot_a, slot_b):
    block = bytearray(32)
    block[:2] = ("_" + suffix).encode()
    block[4:8] = b"BCAB"
    block[8:10] = bytes((1, 2))
    block[12], block[14] = slot_a, slot_b
    block[28:] = struct.pack("<I", zlib.crc32(block[:28]))
    return bytes(block)


class NextBootLock(unittest.TestCase):
    def setUp(self):
        if not shutil.which("bash"):
            self.skipTest("bash is unavailable")
        temp = tempfile.TemporaryDirectory(prefix="mu300-boot-lock-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.disk = self.root / "disk"
        self.state = self.disk / ".mu300"
        self.state.mkdir(parents=True)
        self.conf = self.root / "default-boot"
        self.conf.write_text("linux\n")
        self.misc = self.root / "misc"
        self.prefix = b"P" * OFFSET
        self.suffix = b"S" * 2016
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        for name in ("flock", "sync"):
            stub = self.stubs / name
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
        # BSD wc pads its output; the target's BusyBox wc does not.
        wc = self.stubs / "wc"
        wc.write_text("#!/bin/sh\n/usr/bin/wc \"$@\" | awk '{ print $1 }'\n")
        wc.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("MU300_")}
        self.env.update(
            MU300_CONF=str(self.conf),
            MU300_RUN=str(self.run_dir),
            MU300_DISK=str(self.disk),
            MU300_MISC=str(self.misc),
            MU300_LOCK=str(self.run_dir / "next-boot.lock"),
            TEST_MISC=str(self.misc),
            PATH=str(self.stubs) + os.pathsep + os.environ.get("PATH", os.defpath),
        )
        self.configure_slot("b")

    def configure_slot(self, linux_slot):
        self.linux_slot = linux_slot
        android_slot = "a" if linux_slot == "b" else "b"
        linux = boot_control(linux_slot, 0x2F if linux_slot == "a" else 0x9E,
                             0x2F if linux_slot == "b" else 0x9E)
        android = boot_control(android_slot, 0x9F if android_slot == "a" else 0x1E,
                               0x9F if android_slot == "b" else 0x1E)
        self.linux_trial, self.android = linux, android
        (self.run_dir / "linux-slot").write_text(linux_slot + "\n")
        (self.run_dir / "misc-bc-linux-trial.bin").write_bytes(linux)
        (self.run_dir / "misc-bc-android.bin").write_bytes(android)
        current = bytearray(linux)
        current[self.info_offset()] = 0x6F
        current[28:] = struct.pack("<I", zlib.crc32(current[:28]))
        self.misc.write_bytes(self.prefix + current + self.suffix)
        (self.state / "boot-attempts").write_text("5\n")
        (self.state / "boot-tries").write_text("0\n")

    def info_offset(self):
        return 12 if self.linux_slot == "a" else 14

    def command(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args], env=self.env,
                              capture_output=True, text=True, timeout=15)

    def succeed(self, *args):
        result = self.command(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_misc_bounds()
        return result

    def block(self):
        return self.misc.read_bytes()[OFFSET:OFFSET + 32]

    def assert_misc_bounds(self):
        data = self.misc.read_bytes()
        self.assertEqual(data[:OFFSET], self.prefix)
        self.assertEqual(data[OFFSET + 32:], self.suffix)
        self.assertEqual(struct.unpack("<I", data[OFFSET + 28:OFFSET + 32])[0],
                         zlib.crc32(data[OFFSET:OFFSET + 28]))

    def assert_linux_info(self, value):
        self.assertEqual(self.block()[self.info_offset()], value)
        self.assert_misc_bounds()

    def fail_misc_writes(self):
        real_dd = shutil.which("dd")
        self.assertIsNotNone(real_dd)
        stub = self.stubs / "dd"
        stub.write_text("#!/bin/sh\nfor arg in \"$@\"; do\n"
                        "    [ \"$arg\" = \"of=$TEST_MISC\" ] && exit 1\n"
                        f"done\nexec '{real_dd}' \"$@\"\n")
        stub.chmod(0o755)

    def test_lock_and_unlock_both_slots(self):
        for slot in ("b", "a"):
            with self.subTest(linux_slot=slot):
                if slot == "a":
                    self.configure_slot(slot)
                self.succeed("lock")
                self.assert_linux_info(0xEF)
                self.assertTrue((self.state / "boot-lock").exists())
                self.assertIn("locked: yes", self.succeed("status").stdout)
                self.succeed("unlock")
                self.assert_linux_info(0x6F)
                self.assertFalse((self.state / "boot-lock").exists())
                self.assertIn("locked: no", self.succeed("status").stdout)

    def test_trial_temporarily_unlocks_and_rearm_restores_lock(self):
        self.succeed("lock")
        self.succeed("--trial")
        self.assert_linux_info(0x6F)
        self.assertTrue((self.state / "boot-lock").exists())
        self.assertIn("locked: no", self.succeed("status").stdout)
        self.succeed("--rearm")
        self.assert_linux_info(0xEF)
        self.assertTrue((self.state / "boot-lock").exists())

    def test_attempts_change_preserves_boot_image_trial(self):
        self.succeed("lock")
        self.succeed("--trial")
        self.succeed("attempts", "4")
        self.assertEqual((self.state / "boot-attempts").read_text(), "4\n")
        self.assert_linux_info(0x5F)
        self.assertTrue((self.state / "boot-lock").exists())

    def test_manual_android_keeps_the_lock_request(self):
        self.succeed("lock")
        self.succeed("android")
        self.assertEqual(self.block(), self.android)
        self.assertTrue((self.state / "boot-lock").exists())
        self.succeed("linux")
        self.assert_linux_info(0xEF)

    def test_trial_with_android_default_does_not_arm_linux(self):
        self.conf.write_text("android\n")
        before = self.misc.read_bytes()
        self.succeed("--trial")
        self.assertEqual(self.misc.read_bytes(), before)
        self.assertEqual(self.conf.read_text(), "android\n")

    def test_lock_from_android_default_commits_both_states(self):
        self.conf.write_text("android\n")
        self.misc.write_bytes(self.prefix + self.android + self.suffix)
        self.succeed("lock")
        self.assertEqual(self.conf.read_text(), "linux\n")
        self.assert_linux_info(0xEF)
        self.assertTrue((self.state / "boot-lock").exists())

    def test_lock_refuses_conflicting_slot_metadata_without_writing(self):
        wrong = boot_control("a", 0x2F, 0x9E)
        (self.run_dir / "misc-bc-linux-trial.bin").write_bytes(wrong)
        before = self.misc.read_bytes()
        result = self.command("lock")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.misc.read_bytes(), before)
        self.assertFalse((self.state / "boot-lock").exists())

    def test_failed_lock_write_does_not_leave_a_lock_request(self):
        self.fail_misc_writes()
        before = self.misc.read_bytes()
        result = self.command("lock")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.misc.read_bytes(), before)
        self.assertFalse((self.state / "boot-lock").exists())

    def test_failed_sync_after_marker_creation_rolls_back_marker(self):
        (self.stubs / "sync").write_text("#!/bin/sh\nexit 1\n")
        before = self.misc.read_bytes()
        result = self.command("lock")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.misc.read_bytes(), before)
        self.assertFalse((self.state / "boot-lock").exists())

    def test_failed_lock_from_android_keeps_android_default(self):
        self.conf.write_text("android\n")
        self.misc.write_bytes(self.prefix + self.android + self.suffix)
        self.fail_misc_writes()
        before = self.misc.read_bytes()
        result = self.command("lock")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.misc.read_bytes(), before)
        self.assertEqual(self.conf.read_text(), "android\n")
        self.assertFalse((self.state / "boot-lock").exists())

    def test_failed_unlock_write_keeps_the_lock_request(self):
        self.succeed("lock")
        before = self.misc.read_bytes()
        self.fail_misc_writes()
        result = self.command("unlock")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.misc.read_bytes(), before)
        self.assertTrue((self.state / "boot-lock").exists())


if __name__ == "__main__":
    unittest.main()
