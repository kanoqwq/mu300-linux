"""Run the actual startup wait against delayed regular-file node fixtures."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'POSIX startup script')
class StorageDiscovery(unittest.TestCase):
    def run_wait(self, delay, mode='part', missing=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'mode').write_text(mode+'\n')
            source = (TOP/'devices/u30pro/boot-wait.sh').read_text()
            # Fixture nodes are ordinary files. The production -b checks stay
            # in the shipped script; this test never creates/opens block nodes.
            source = source.replace('/etc/mu300-desktop-root-mode', str(root/'mode'))
            source = source.replace('/usr/sbin/u30pro-nodes --populate-nodes', 'populate')
            source = source.replace('/dev/', str(root)+'/').replace('[ -b ', '[ -f ')
            script = '''count=0; sleeps=0
populate() {
    count=$((count + 1))
    if [ "$count" -ge '''+str(delay)+''' ]; then
        touch "'''+str(root)+'''/misc"
        '''+(':' if missing else 'touch "'+str(root)+'/root"')+'''
    fi
}
mu300_storage_disk() { echo mmcblk0; }
mu300_storage_part() {
    [ "$2" = mmcblk0 ] || return 1
    case $1 in misc) echo misc;; mu300root|userdata) echo root;; esac
}
sleep() { sleeps=$((sleeps + 1)); }
'''+source+'''
u30pro_wait_storage; result=$?
echo "$result $count $sleeps"
'''
            result = subprocess.run(['sh', '-c', script], capture_output=True, text=True, check=True)
            return tuple(map(int, result.stdout.split()))

    def test_ready_device_adds_no_delay(self):
        self.assertEqual(self.run_wait(1), (0, 1, 0))

    def test_delayed_mmc_and_aliases_are_refreshed(self):
        for mode in ('part', 'prepare', 'install-part', 'data-image'):
            with self.subTest(mode=mode):
                self.assertEqual(self.run_wait(4, mode), (0, 4, 3))

    def test_missing_root_times_out_without_selecting_another_disk(self):
        self.assertEqual(self.run_wait(1, missing=True), (1, 100, 100))

    def test_invalid_mode_is_rejected(self):
        self.assertEqual(self.run_wait(1, 'unknown')[0], 1)

    def test_usb_helper_does_not_change_http_configuration(self):
        text = (TOP/'devices/u30pro/native/ui/native-control/overlay/usr/sbin/u30pro-usb').read_text()
        self.assertNotIn('uhttpd.', text)

    def test_explicit_usb_profile_runs_without_uci_in_initramfs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root/'config'
            (config/'usb_gadget').mkdir(parents=True)
            (root/'udc/dummy').mkdir(parents=True)
            (root/'run').mkdir()
            (root/'mounts').write_text('configfs '+str(config)+' configfs rw 0 0\n')
            source = (TOP/'devices/u30pro/native/ui/native-control/overlay/usr/sbin/u30pro-usb').read_text()
            source = source.replace('/sys/kernel/config', str(config)).replace('/sys/class/udc', str(root/'udc'))
            source = source.replace('/proc/mounts', str(root/'mounts')).replace('/run/', str(root/'run')+'/')
            helpers = '''mkdir() {
    command mkdir "$@" || return
    for p in "$@"; do
        case "$p" in
            */usb_gadget/u30pro) command mkdir -p "$p/functions" "$p/configs" "$p/os_desc";;
            */functions/ncm.usb0) echo usb0 > "$p/ifname";;
        esac
    done
}
ip() { :; }
uci() { echo 'unexpected UCI invocation' >&2; return 99; }
set -- start ncm
'''
            result = subprocess.run(['sh', '-c', helpers+source], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('unexpected UCI', result.stderr)
            self.assertEqual((config/'usb_gadget/u30pro/UDC').read_text(), 'dummy')
            self.assertEqual((root/'run/u30pro-usb-profile').read_text(), 'ncm\n')


if __name__ == '__main__':
    unittest.main()
