"""Run no-card probes through the actual frozen backend, with simulated ADB only."""
import json
from pathlib import Path
import subprocess
import sys

def child(package):
    if not getattr(sys, 'frozen', False):
        raise AssertionError('Run this check through the frozen backend')
    from backend.adapter import Adapter
    from backend.service import plan_install
    mu = (package / 'desktop/manifest.json').is_file()
    meta = {'path': str(package), 'adapter': 'mu300' if mu else 'e5'}
    a = Adapter(meta)
    model = 'MU300' if mu else a.legacy.package_profile()['android_models'][0]
    calls = []

    class Transport:
        su = 'su'

        def devices(self):
            return [{'serial': 'test-device', 'state': 'device'}]

        def properties(self):
            return {'ro.product.model': model, 'ro.product.device': 'F50', 'ro.board.platform': 'ums9620', 'ro.boot.slot_suffix': '_a', 'ro.boot.verifiedbootstate': 'orange', 'ro.crypto.state': 'unencrypted', 'ro.crypto.type': '', 'sys.boot_completed': '1'}

        def root_status(self):
            return 'ready'

        def shell(self, script, root=False):
            if script == 'cat /proc/sys/kernel/random/boot_id':
                return 'test-boot'
            if script == 'settings get secure user_setup_complete':
                return '1'
            if script.startswith('cards=""; for p in /sys/class/block/mmcblk*;'):
                return ''
            if 'echo NODE=$n' in script:
                return 'NODE=mmcblk1p16\nCID=' + 'b' * 32 + '\nSTART=2048\nSIZE=33554432\nDISK_SIZE=134217728\nFREE=12582912\nSOURCE=/dev/block/mmcblk1p16\nROOT=mmcblk1p20\n'
            if script == 'cat /sys/class/block/mmcblk1p20/start /sys/class/block/mmcblk1p20/size':
                return '33556480 8388608'
            raise AssertionError('Unexpected simulated read')

    def missing_card():
        calls.append('sd')
        raise ValueError('expected one SD card, found 0 (none): is a card in the slot?')
    a.adb = Transport()
    a.bind = lambda _serial: None
    a.read_gpt = lambda _storage: {'empty': [1], 'safe_gaps': []}
    a.legacy.device_sd = missing_card
    # UFS discovery stages its small read-only identity executable before
    # running the storage probe. Keep this transport simulated as well.
    def helper_push(local, remote, label):
        if Path(local).name != 'e5-ufs-id' or '/e5-ufs-id-' not in remote:
            raise AssertionError('Unexpected helper transfer')
    def helper_command(label, command):
        if not command.startswith('chmod 755 ') or '/e5-ufs-id-' not in command:
            raise AssertionError('Unexpected helper command')
        return ''
    if not mu:
        a.legacy.push = helper_push
        a.legacy.su_checked = helper_command
    for method in ('part', 'reinstall', 'image', 'boot'):
        device = a.probe('test-device', method)
        if not (device['existing'] and device['root']):
            raise AssertionError('Frozen check failed')
    plan = plan_install(meta, device, {'method': 'reinstall', 'grow': True})
    if not (plan['method'] == 'reinstall' and plan['target'] == 'mmcblk1p20' and (plan['openwrt_bytes'] == 4 << 30) and (not plan['wipe'])):
        raise AssertionError('Frozen check failed')
    if not not calls:
        raise AssertionError('Frozen check failed')
    try:
        target = a.probe('test-device', 'tf')
        plan_install(meta, target, {'method': 'tf'})
    except ValueError:
        pass
    else:
        raise AssertionError('Missing card must block TF installation')
    print(json.dumps({'package': package.name, 'frozen_backend': True, 'simulated_transport': True, 'non_tf_probes': ['part', 'reinstall', 'image', 'boot'], 'reinstall_target': plan['target'], 'reinstall_bytes': plan['openwrt_bytes'], 'tf_missing_card_rejected': True, 'hardware_access': False}))
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child(Path(sys.argv[2]).resolve())
    else:
        import argparse
        p = argparse.ArgumentParser(description=__doc__)
        p.add_argument('packages', nargs='+', type=Path)
        p.add_argument('--report', type=Path)
        args = p.parse_args()
        rows = []
        for package in args.packages:
            result = subprocess.run([str(package.resolve() / 'tools/backend/openwrt-backend.exe'), '--run-tool', str(Path(__file__).resolve()), '--child', str(package.resolve())], capture_output=True, timeout=30)
            if result.returncode:
                raise RuntimeError(result.stderr.decode('utf-8'))
            rows.append(json.loads(result.stdout.decode('utf-8')))
            print(package.name + ': actual frozen backend method isolation passed', flush=True)
        if args.report:
            args.report.write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
