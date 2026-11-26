"""Exercise the real rpcd action entry, ucode delegate and boot transaction on files.

Run in the retained ARM64 rootfs test container; never mount device block nodes.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zlib


def block(slot, linux):
    result = bytearray(32)
    result[:4] = ('_' + slot).encode() + b'\0\0'
    result[4:10] = b'BCAB\x01\x02'
    result[12], result[14] = ((0x2f, 0x9e) if slot == 'a' else (0x9e, 0x2f)) if linux else (
        (0x9f, 0x1e) if slot == 'a' else (0x1e, 0x9f))
    struct.pack_into('<I', result, 28, zlib.crc32(result[:28]))
    return bytes(result)


def check(repo):
    e5 = (repo / 'openwrt/luci-app-t551m').exists()
    package = repo / 'openwrt' / ('luci-app-t551m' if e5 else 'luci-app-mu300')
    runtime = package / 'root/usr/libexec/unisoc-modem'
    original = repo / ('rootfs/overlay/opt/e5/e5-next-boot' if e5 else 'rootfs/overlay/opt/mu300/bin/mu300-next-boot')
    cases = 0
    for slot in ('a', 'b'):
        with tempfile.TemporaryDirectory(prefix='boot-page-') as scratch:
            root = Path(scratch)
            run = root / ('run/e5linux' if e5 else 'run/mu300')
            run.mkdir(parents=True)
            conf = root / ('etc/e5linux/default-boot' if e5 else 'etc/mu300/default-boot')
            conf.parent.mkdir(parents=True)
            conf.write_text('linux\n')
            misc = root / 'misc'
            misc.write_bytes(b'P' * 4096)
            (run / 'misc-dev').write_text(str(misc))
            (run / 'linux-slot').write_text(slot)
            android = 'b' if slot == 'a' else 'a'
            linux_bc, android_bc = block(slot, True), block(android, False)
            (run / 'misc-bc-linux-trial.bin').write_bytes(linux_bc)
            (run / 'misc-bc-android.bin').write_bytes(android_bc)
            # Historical E5 filenames identify roles by contents, not suffix.
            (run / 'misc-bc-slot-b-trial.bin').write_bytes(linux_bc)
            (run / 'misc-bc-slot-a.bin').write_bytes(android_bc)
            (root / 'disk/.mu300').mkdir(parents=True)
            (root / 'cmdline').write_text('console=ttyS0')
            helper = root / "control's helper"
            code = original.read_text().replace('/run', str(root) + '/run')
            code = code.replace('/etc/mu300/', str(root) + '/etc/mu300/').replace('/etc/e5linux/', str(root) + '/etc/e5linux/')
            code = code.replace('/mnt/mu300-disk', str(root / 'disk')).replace('/proc/cmdline', str(root / 'cmdline'))
            helper.write_text(code)
            helper.chmod(0o755)
            dash = root / 'dash'
            dash.mkdir()
            for name in ('action', 'boot-temporary.uc'):
                shutil.copy(runtime / name, dash / name)
                (dash / name).chmod(0o755)
            stubs = root / 'bin'
            stubs.mkdir()
            for name, body in {
                'sync': 'exit 0',
                'reboot': 'echo forbidden-reboot >&2; exit 99',
                'poweroff': 'echo forbidden-poweroff >&2; exit 99',
            }.items():
                p = stubs / name
                p.write_text('#!/bin/sh\n' + body + '\n')
                p.chmod(0o755)
            pool = root / 'pool.sh'
            pool.write_text('mu300_sms_pool() { echo /tmp/boot-test-sms; }\n')
            env = dict(os.environ, BOOT_CONTROL_HELPER=str(helper), MU300_DASH_BIN=str(dash),
                       MU300_SMS_POOL_LIB=str(pool), MU300_DASH_DIR=str(root / 'dash-state'),
                       PATH=str(stubs) + ':' + os.environ['PATH'])

            def request():
                result = subprocess.run(['sh', str(package / 'root/usr/libexec/rpcd/mu300dash'), 'call', 'act'],
                    input=json.dumps({'op': 'os', 'arg': 'linux-temporary'}), env=env,
                    capture_output=True, text=True, timeout=15)
                assert result.returncode == 0, result.stderr
                try:
                    return json.loads(result.stdout)
                except ValueError:
                    detail = subprocess.run(['sh', str(dash / 'action'), 'os', 'linux-temporary'],
                        env=env, capture_output=True, text=True, timeout=15)
                    raise AssertionError((result.stdout, result.stderr, detail.stdout, detail.stderr))

            before = misc.read_bytes()
            reply = request()
            if reply['ok'] != 1:
                detail = subprocess.run([str(helper), 'android'], env=env, capture_output=True, text=True)
                raise AssertionError((reply, detail.stdout, detail.stderr))
            assert conf.read_text() == 'android\n'
            actual = misc.read_bytes()
            assert actual[:2048] == before[:2048] and actual[2080:] == before[2080:]
            assert actual[2048:2080] == android_bc
            # Missing helper and lock contention must surface as failures.
            env['BOOT_CONTROL_HELPER'] = str(root / 'missing')
            assert request()['ok'] == 0
            env['BOOT_CONTROL_HELPER'] = str(helper)
            lock = root / 'run/openwrt-default-boot.lock'
            with lock.open('a') as fd:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                reply = request()
                assert reply['ok'] == 0 and '正忙' in reply['error'], reply
            # A persistence failure restores both misc and the saved intention.
            conf.write_text('linux\n')
            misc.write_bytes(before)
            mv = stubs / 'mv'
            mv.write_text('#!/bin/sh\ncase "$2" in *.backup.*) ;; *) exit 1;; esac\nexec /bin/mv "$@"\n')
            mv.chmod(0o755)
            assert request()['ok'] == 0
            assert misc.read_bytes() == before and conf.read_text() == 'linux\n'
            mv.unlink()
            assert request()['ok'] == 1, 'A failed transaction must release its lock'
            cases += 1
    print(f'{repo.name}: actual RPC/action/ucode/boot helper; both slot roles, contention, failure rollback and retry passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, action='append', required=True)
    args = parser.parse_args()
    for repo in args.repo:
        check(repo)
