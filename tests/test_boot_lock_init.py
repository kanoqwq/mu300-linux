"""Initramfs lock/backstop policy with fake misc and disk files."""

import re
import struct
import zlib

from helpers import TOP, ShellTest


INIT = (TOP / "boot" / "init").read_text()


def policy_functions():
    match = re.search(r"# --- boot-lock/boot-tries begin\n(.*?)# --- boot-lock/boot-tries end", INIT, re.S)
    if not match:
        raise AssertionError("boot/init has no testable boot-lock/boot-tries block")
    return match.group(1)


class BootLockInit(ShellTest):
    def setUp(self):
        super().setUp()
        self.disk = self.tmp / "disk"
        self.state = self.disk / ".mu300"
        self.state.mkdir(parents=True)
        self.root = self.disk / "openwrt"
        (self.root / "etc/mu300").mkdir(parents=True)
        (self.root / "etc/mu300/default-boot").write_text("linux\n")
        (self.state / "boot-attempts").write_text("5\n")
        self.misc = self.tmp / "misc"
        self.set_misc(successful=False)
        self.stub("sync", "exit 0")

    def set_misc(self, successful, slot="b"):
        block = bytearray(32)
        block[:2] = ("_" + slot).encode()
        block[4:8] = b"BCAB"
        block[8:10] = bytes((1, 2))
        block[12] = (0xEF if successful else 0x6F) if slot == "a" else 0x9E
        block[14] = (0xEF if successful else 0x6F) if slot == "b" else 0x9E
        block[28:] = struct.pack("<I", zlib.crc32(block[:28]))
        self.misc.write_bytes(bytes(2048) + block + bytes(2016))
        self.slot = slot

    def boot(self, shell):
        code = ("set -eu\nlog() { :; }\n"
                "misc=$TEST_MISC; linux_slot=$TEST_SLOT\n"
                + policy_functions()
                + '\nif boot_tries_exceeded "$TEST_ROOT" "$TEST_DISK"; then '
                  'echo android; else echo linux; fi\n')
        result = self.sh(shell, code, TEST_MISC=self.misc, TEST_SLOT=self.slot,
                         TEST_ROOT=self.root, TEST_DISK=self.disk)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def locked(self, shell):
        code = ("set -eu\n"
                "misc=$TEST_MISC; linux_slot=$TEST_SLOT\n"
                + policy_functions()
                + "\nif lk_locked; then echo yes; else echo no; fi\n")
        result = self.sh(shell, code, TEST_MISC=self.misc, TEST_SLOT=self.slot)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_unlocked_five_failures_then_android(self):
        for shell in self.each_shell():
            (self.state / "boot-tries").write_text("0\n")
            self.set_misc(successful=False)
            for count in range(1, 6):
                self.assertEqual(self.boot(shell), "linux", count)
            self.assertEqual(self.boot(shell), "android")

    def test_locked_slot_never_counts_or_falls_back(self):
        for shell in self.each_shell():
            for slot in ("a", "b"):
                (self.state / "boot-tries").write_text("0\n")
                (self.state / "boot-lock").touch()
                self.set_misc(successful=True, slot=slot)
                self.assertEqual(self.locked(shell), "yes")
                for _ in range(8):
                    self.assertEqual(self.boot(shell), "linux")
                self.assertEqual((self.state / "boot-tries").read_text().strip(), "0")

    def test_trial_counts_failures_despite_saved_lock_request(self):
        for shell in self.each_shell():
            (self.state / "boot-tries").write_text("0\n")
            (self.state / "boot-lock").touch()
            self.set_misc(successful=False)
            self.assertEqual(self.locked(shell), "no")
            for _ in range(5):
                self.assertEqual(self.boot(shell), "linux")
            self.assertEqual(self.boot(shell), "android")

    def test_misc_bit_keeps_rootfs_unavailable_rescue_without_disk_marker(self):
        for shell in self.each_shell():
            self.set_misc(successful=True)
            self.assertEqual(self.locked(shell), "yes")
            self.set_misc(successful=False)
            self.assertEqual(self.locked(shell), "no")

    def test_rootfs_unavailable_keeps_locked_usb_rescue(self):
        start = INIT.index("if lk_locked; then", INIT.index('log "stage=rootfs-unavailable'))
        fragment = INIT[start:INIT.index("ifconfig usb0", start)].replace("/run/", "$TEST_RUN/")
        code = ("set -eu\n"
                "misc=$TEST_MISC; linux_slot=$TEST_SLOT; root_target=mmc\n"
                "log() { :; }\nrestore_android() { echo restored; }\n"
                + policy_functions() + "\n" + fragment + "\necho rescue\n")
        for index, shell in enumerate(self.each_shell()):
            rescue = self.tmp / f"locked-rescue-{index}"
            rescue.mkdir()
            self.set_misc(successful=True)
            result = self.sh(shell, code, TEST_MISC=self.misc, TEST_SLOT=self.slot, TEST_RUN=rescue)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "rescue")
            self.assertTrue((rescue / "stay").exists())
            self.assertIn(str(self.misc), (rescue / "to-android").read_text())

            rescue = self.tmp / f"unlocked-rescue-{index}"
            rescue.mkdir()
            self.set_misc(successful=False)
            result = self.sh(shell, code, TEST_MISC=self.misc, TEST_SLOT=self.slot, TEST_RUN=rescue)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["restored", "rescue"])
            self.assertFalse((rescue / "stay").exists())
