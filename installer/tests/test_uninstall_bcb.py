"""BCB command handoff: real byte edits on temporary regular files only."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.state import RecoveryRequired
from backend.uninstall import uninstall_bcb, bcb_handoff


def message(command=b'bootonce-bootloader'):
    raw=bytearray(2048);raw[:len(command)]=command
    raw[32:40]=b'previous'  # status/reserved bytes are not a boot request
    raw[1000:1016]=b'VENDOR DATA KEPT'
    return bytes(raw)


class BootMessage(unittest.TestCase):
    def test_empty_c_strings_and_erased_command_are_not_recovery(self):
        for raw in (bytes(2048),b'\0ignored trailing command bytes!'+bytes(2016),b'\xff'*2048):
            self.assertEqual(uninstall_bcb(raw),raw)

    def test_only_superseded_bootloader_command_is_cleared(self):
        raw=message();after=uninstall_bcb(raw)
        self.assertEqual(after[:32],bytes(32));self.assertEqual(after[32:],raw[32:])
        self.assertEqual(uninstall_bcb(after),after)

    def test_recovery_other_commands_and_ota_payloads_remain_protected(self):
        for command in (b'boot-recovery',b'boot-fastboot',b'unknown'):
            with self.subTest(command=command),self.assertRaises(RecoveryRequired):uninstall_bcb(message(command))
        for offset,text in ((64,b'recovery\n--wipe_data\n'),(832,b'1/3')):
            raw=bytearray(message());raw[offset:offset+len(text)]=text
            with self.subTest(offset=offset),self.assertRaises(RecoveryRequired):uninstall_bcb(bytes(raw))

    def test_short_or_forged_handoff_is_rejected(self):
        with self.assertRaises(RecoveryRequired):uninstall_bcb(bytes(32))
        with self.assertRaises(RecoveryRequired):bcb_handoff(message(),bytes(2048))


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('sh'),
                     'requires Linux/Android dd fsync semantics; temporary regular files only')
class RealHandoff(unittest.TestCase):
    def test_command_only_write_preserves_ab_control_and_every_other_byte(self):
        before=message();after=uninstall_bcb(before)
        guard,update=bcb_handoff(before,after)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'misc.bin'
            tail=b'AB CONTROL AND VENDOR TAIL'.ljust(2048,b'Z')
            path.write_bytes(before+tail)
            script=('set -eu; '+guard+update).replace('/dev/block/by-name/misc',str(path))
            for _ in range(2):
                result=subprocess.run(['sh','-c',script],capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(path.read_bytes(),after+tail)

    def test_changed_recovery_or_vendor_bytes_are_never_overwritten(self):
        before=message();_,update=bcb_handoff(before,uninstall_bcb(before))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'misc.bin'
            for changed in (message(b'boot-recovery'),before[:1100]+b'X'+before[1101:]):
                path.write_bytes(changed+bytes(2048))
                script=('set -eu; '+update).replace('/dev/block/by-name/misc',str(path))
                result=subprocess.run(['sh','-c',script],capture_output=True,text=True,timeout=5)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(path.read_bytes(),changed+bytes(2048))


if __name__=='__main__':unittest.main()
