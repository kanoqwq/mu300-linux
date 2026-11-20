"""Actual rpcd + uhttpd button saves, with private UCI and no device access."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time

TOP = Path(__file__).resolve().parents[1]
REL = 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/'
FIELDS = ('long', 'long_command', 'double', 'double_command', 'power_double', 'power_double_command')


def command(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=30)
    except subprocess.CalledProcessError as error:
        if args[:2] == ('docker', 'run'):print(error.stderr, flush=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path, help='Existing prepared OpenWrt runtime, mounted read-only')
    parser.add_argument('--legacy-ref', help='Optionally reproduce the old rejection before testing the fix')
    args = parser.parse_args()
    assert (args.root/'usr/bin/python3').is_file(), 'Select the existing prepared runtime'
    with tempfile.TemporaryDirectory(prefix='button-http-') as folder:
        fixture = Path(folder)
        for name in ('config', 'helpers', 'bin', 'device/etc/mu300'):
            (fixture/name).mkdir(parents=True)
        for name in ('buttons', 'buttons.py'):
            p = fixture/'helpers'/name
            p.write_bytes((TOP/REL/name).read_bytes().replace(b'\r\n', b'\n')); p.chmod(0o755)
        (fixture/'sms-pool.sh').write_bytes((TOP/'rootfs/overlay/opt/mu300/lib/sms-pool.sh').read_bytes())
        fixed = (fixture/'helpers/buttons.py').read_bytes()
        if args.legacy_ref:
            legacy = command('git', '-c', 'safe.directory='+str(TOP), '-C', str(TOP), 'show', args.legacy_ref+':'+REL+'buttons.py')
            (fixture/'helpers/buttons.py').write_text(legacy)
        rpc = fixture/'mu300dash'
        rpc.write_bytes((TOP/'openwrt/luci-app-mu300/root/usr/libexec/rpcd/mu300dash').read_bytes().replace(b'\r\n', b'\n'))
        rpc.chmod(0o755)
        device = fixture/'bin/mu300-device'
        device.write_text('#!/bin/sh\ncat /fixture/device/etc/mu300/model\n'); device.chmod(0o755)
        (fixture/'device/etc/mu300/model').write_text('f50pro\n')
        (fixture/'config/unisoc_modem').write_text("config modem 'main'\n option keep 'user-value'\n")
        (fixture/'config/u30pro_native_control').write_text("config control 'settings'\n option keep 'screen-value'\n")
        # Existing non-secret test credential: password acl-fixture.
        password = '$6$Os1iACkvDWoDLwJz$uf32/I6y1SM4NbpK1GIuPt.epBDcIuxBP/tHhj7T9Ab34WVkzXIRW1p95tZzvnQLtJOO2/SJrztfW7y3XPTyG1'
        config = "config rpcd\n option socket '/var/run/ubus/ubus.sock'\n"
        for user in ('root', 'reader'):
            config += "config login\n option username '%s'\n option password '%s'\n list read 'luci-base'\n list read 'luci-app-mu300'\n" % (user, password)
            if user == 'root':
                config += " list write 'luci-base'\n list write 'luci-app-mu300'\n"
        (fixture/'config/rpcd').write_text(config)
        startup = '''set -eu
mkdir -p /var/run/ubus /tmp/.uci
export MU300_SYSROOT=/fixture/device MU300_BIN=/fixture/bin MU300_DASH_BIN=/fixture/helpers MU300_SMS_POOL_LIB=/fixture/sms-pool.sh MU300_SMS_POOL=/tmp/sms-fixture
ubusd >/tmp/ubusd.log 2>&1 &
while [ ! -S /var/run/ubus/ubus.sock ]; do sleep 1; done
rpcd >/tmp/rpcd.log 2>&1 &
uhttpd -f -p 127.0.0.1:18080 -u /ubus -h /www >/tmp/http.log 2>&1 &
exec sleep 300
'''
        docker = ['docker', 'run', '-d', '--pull=never', '--network=none', '--platform=linux/arm64',
                  '--mount', f'type=bind,src={args.root.resolve()}/usr,dst=/usr,readonly',
                  '--mount', f'type=bind,src={args.root.resolve()}/lib,dst=/lib,readonly',
                  '--mount', f'type=bind,src={fixture},dst=/fixture',
                  '--mount', f'type=bind,src={fixture}/config,dst=/etc/config',
                  '--mount', f'type=bind,src={rpc},dst=/usr/libexec/rpcd/mu300dash,readonly',
                  '--entrypoint', '/bin/sh', 'mu300-openwrt-base:25.12.5', '-ec', startup]
        container = command(*docker).strip()

        def execute(*argv):
            return command('docker', 'exec', container, *argv)

        def call(sid, obj, method, params):
            body = json.dumps(dict(jsonrpc='2.0', id=1, method='call', params=[sid, obj, method, params]))
            result = json.loads(execute('uclient-fetch', '-q', '-O', '-', '--header=Content-Type: application/json',
                                        '--post-data='+body, 'http://127.0.0.1:18080/ubus'))
            return result.get('result', result)

        def login(user):
            result = call('0'*32, 'session', 'login', dict(username=user, password='acl-fixture'))
            assert result[0] == 0, result
            return result[1]['ubus_rpc_session']

        try:
            for attempt in range(40):
                try:
                    sid = login('root'); break
                except (subprocess.SubprocessError, ValueError, KeyError, AssertionError):
                    if attempt == 39: raise
                    time.sleep(.1)
            if args.legacy_ref:
                old = call(sid, 'mu300dash', 'button_set', {'short': 'wifi-band'})
                assert isinstance(old,list) and old[0] == 0 and old[1] == {'ok': 0, 'error': 'invalid_button_settings'}, old
                print('Reproduced legacy save rejection over authenticated HTTP.', flush=True)
                (fixture/'helpers/buttons.py').write_bytes(fixed)
            for model in ('f50pro', 'u30pro', 'u30air', 'v50'):
                (fixture/'device/etc/mu300/model').write_text(model+'\n')
                state = call(sid, 'mu300dash', 'button_get', {})[1]
                values = {key: state[key] for key in FIELDS}
                values.update(long='wifi-band', double='custom', double_command='logger button-fixture')
                result = call(sid, 'mu300dash', 'button_set', values)
                assert result[0] == 0 and result[1]['ok'] == 1, (model, result)
                read = call(login('root'), 'mu300dash', 'button_get', {})[1]
                assert all(read[key] == value for key, value in values.items()), (model, read)
                assert not any('ubus_rpc_session' in p.read_text() for p in (fixture/'config').iterdir() if p.is_file())
            assert 'user-value' in (fixture/'config/unisoc_modem').read_text()
            assert 'screen-value' in (fixture/'config/u30pro_native_control').read_text()
            for unauthorized in ('0'*32, login('reader')):
                denied = call(unauthorized, 'mu300dash', 'button_set', {'short': 'none'})
                assert denied == [6] or isinstance(denied, dict) and denied.get('error', {}).get('code') == -32002, denied
            print('PASS four models: real HTTP/rpcd saves, fresh-session readback, private UCI persistence, unrelated settings preserved, anonymous/read-only writes denied')
        except Exception:
            for name in ('ubusd.log', 'rpcd.log', 'http.log'):
                path = fixture/name
                if path.exists():print(name+': '+path.read_text(errors='replace')[-3000:], flush=True)
            subprocess.run(['docker', 'logs', container], capture_output=False, timeout=10)
            raise
        finally:
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True, timeout=15)


if __name__ == '__main__':
    main()
