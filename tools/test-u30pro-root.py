#!/usr/bin/env python3
"""Target-root offline checks in an isolated container. No hardware mounts."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import shutil

TOP=Path(__file__).resolve().parents[1]
ROOT=Path('/home/kano/openwrt-build/package-work/u30pro-release/root')


def run(command):
    return subprocess.check_output(['docker','run','--rm','--platform','linux/arm64',
        '-v',str(ROOT)+':/target','--entrypoint','/bin/sh','mu300-openwrt-base:25.12.5',
        '-ec',command],text=True)


def main():
    if os.environ.get('U30PRO_BUILD_LOCKED')!='1':raise ValueError('Use build-release.py run')
    # These are production inputs, never files recovered from a hotfixed device.
    # Compare the assembled root too: source-only syntax tests miss stale staging.
    inputs={
        'opt/mu300/bin/mu300-modemd':'rootfs/overlay/opt/mu300/bin/mu300-modemd',
        'opt/mu300/bin/mu300-at':'rootfs/overlay/opt/mu300/bin/mu300-at',
        'opt/mu300/bin/mu300-dual-radio':'rootfs/overlay/opt/mu300/bin/mu300-dual-radio',
        'opt/mu300/bin/mu300-sim-env':'rootfs/overlay/opt/mu300/bin/mu300-sim-env',
        'opt/mu300/bin/mu300-smsd':'rootfs/overlay/opt/mu300/bin/mu300-smsd',
        'opt/mu300/bin/openwrt-wifi-config':'rootfs/overlay/opt/mu300/bin/openwrt-wifi-config',
        'usr/share/ucode/mu300/native-channel.uc':'rootfs/overlay/usr/share/ucode/mu300/native-channel.uc',
        'usr/share/ucode/mu300/carrier.uc':'rootfs/overlay/usr/share/ucode/mu300/carrier.uc',
        'lib/netifd/proto/mu300cell.sh':'openwrt/overlay/lib/netifd/proto/mu300cell.sh',
        'lib/netifd/proto/mu300cell-v6.sh':'openwrt/overlay/lib/netifd/proto/mu300cell-v6.sh',
        'usr/share/u30pro/wallpaper.uc':'devices/u30pro/native/ui/native-control/overlay/usr/share/u30pro/wallpaper.uc',
        'www/luci-static/resources/view/system/u30pro-wallpaper.js':'devices/u30pro/native/ui/native-control/overlay/www/luci-static/resources/view/system/u30pro-wallpaper.js',
        'usr/libexec/unisoc-modem/cpu-voltage.uc':'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/cpu-voltage.uc',
        'usr/libexec/unisoc-modem/action':'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/action',
        'usr/share/ucode/mu300/dash.uc':'openwrt/luci-app-mu300/root/usr/share/ucode/mu300/dash.uc',
        'www/luci-static/resources/view/mu300/cpu.js':'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/cpu.js',
    }
    # Every native adapter and its menu/catalog files must reach the root too.
    overlay=TOP/'devices/u30pro/overlay'
    for path in overlay.rglob('*'):
        if path.is_file() and not path.is_symlink():
            inputs[str(path.relative_to(overlay))]=str(path.relative_to(TOP))
    for target,source in inputs.items():
        expected=(TOP/source).read_bytes()
        if expected.startswith(b'#!'):expected=expected.replace(b'\r\n',b'\n')
        assert (ROOT/target).read_bytes()==expected,'Prepared root missed current source: '+source
    print('Current hotfix sources/native adapters/catalogs match prepared root: '+str(len(inputs)))
    scripts=['/opt/mu300/bin/mu300-modemd','/usr/sbin/u30pro-ui-feed',
        '/usr/share/ucode/mu300/dash.uc','/usr/libexec/unisoc-modem/charge.uc',
        '/opt/mu300/bin/wifi-client','/usr/share/ucode/mu300/wifi-native.uc',
        '/usr/share/u30pro/wallpaper.uc',
        '/usr/libexec/unisoc-modem/cpu-voltage.uc']
    for script in scripts:
        first=(ROOT/script.lstrip('/')).read_bytes().split(b'\n',1)[0]
        if first.startswith(b'#!'):assert b'\r' not in first,script+' has a Windows shebang'
        module = {'/usr/share/ucode/mu300/dash.uc': 'mu300.dash',
                  '/usr/share/ucode/mu300/wifi-native.uc': 'mu300.wifi-native'}.get(script)
        source = "-e \"import * as checked from '"+module+"';\"" if module else script
        run('chroot /target /usr/bin/ucode -c -o /tmp/u30pro-syntax '+source+'; rm -f /target/tmp/u30pro-syntax')
    # No-argument entry exits with usage before any DRM/input access. This
    # checks actual ELF relocation/library resolution, including non-PIE ELF.
    run('set +e; output=$(chroot /target /opt/u30pro/bin/u30pro-panel 2>&1); rc=$?; '
        'set -e; [ "$rc" = 2 ]; case "$output" in *"Usage: u30pro-panel"*) ;; *) printf "%s\\n" "$output"; exit 1;; esac')
    for service in ('mu300-vendor','mu300-hw','mu300-atd','mu300-charge','u30pro-panel','u30pro-watchdog','u30pro-factory','mu300-cpu-driver'):
        run('chroot /target /bin/sh -n /etc/init.d/'+service)
    assert 'iproute2' in run('chroot /target /sbin/ip -V')
    run('chroot /target /bin/sh -n /opt/mu300/bin/cpu-voltage-platform')
    assert (ROOT/'etc/rc.d/S14u30pro-panel').is_symlink()
    assert not (ROOT/'etc/rc.d/S95u30pro-panel').is_symlink()
    assert (ROOT/'etc/rc.d/S08mu300-cpu-driver').is_symlink()
    version=(ROOT/'lib/modules').iterdir()
    modules=next(version)
    assert (modules/'sprd-cpufreq-v2.ko').is_file()
    assert (modules/'u30pro-sprd-scufreq.ko').is_file()
    assert 'voltage_offset_uv:array of int' in subprocess.check_output(['modinfo','-F','parmtype',str(modules/'sprd-cpufreq-v2.ko')],text=True)
    for fixture in ('cpu_voltage_fixture.sh','u30pro_cpu_voltage.sh'):
        path=ROOT/'tmp'/fixture
        try:
            path.write_bytes((TOP/'tests'/fixture).read_bytes())
            print(run('chroot /target /bin/sh /tmp/'+fixture+' /usr/libexec/unisoc-modem/cpu-voltage.uc'),end='')
        finally:path.unlink(missing_ok=True)
    paths=(ROOT/'opt/mu300/vendor/u30pro/modem/map.tsv').read_text().splitlines()
    assert paths and all(row.split()[1]=='/dev/block/by-name/'+row.split()[0] for row in paths)
    assert not (ROOT/'etc/rc.d/S19u30pro-atd').exists()
    assert not (ROOT/'etc/rc.d/S09mu300-vendor').exists()
    assert not (ROOT/'etc/rc.d/S15mu300-wifi-client').exists()
    assert (ROOT/'etc/rc.d/S08u30pro-factory').is_symlink()
    assert (ROOT/'etc/rc.d/S13mu300-vendor').is_symlink()
    assert 'mu300cellv6' not in (ROOT/'etc/config/network').read_text()
    assert '/dev/stty_nr0' in (ROOT/'opt/mu300/lib/modemd-service.sh').read_text()
    # Native dashboard on target Python/ucode, with private sysfs/netlink/ubus
    # fixtures. Copy only the small sources this fixture actually exercises.
    snapshot=ROOT/'tmp/u30pro-snapshot-test'
    snapshot.mkdir(exist_ok=False)
    try:
        names=['tests/helpers.py','tests/native_dashboard_fixture.py','tests/test_native_dashboard.py',
               'openwrt/luci-app-mu300/root/usr/share/ucode/mu300/dash.uc',
               'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/hotspot.uc',
               'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/apn.uc']
        for name in names:
            dest=snapshot/name;dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes((TOP/name).read_bytes())
        print(run('chroot /target /bin/sh -ec "cd /tmp/u30pro-snapshot-test/tests; '
                  'python3 -m unittest test_native_dashboard.NativeSnapshot.test_u30pro_uses_native_thermal_names '
                  'test_native_dashboard.NativeSnapshot.test_u30pro_fgu_and_charger_have_distinct_roles '
                  'test_native_dashboard.NativeSnapshot.test_wan_autostart_distinguishes_user_stop_from_reconnect '
                  'test_native_dashboard.NativeSnapshot.test_full_snapshot_and_public_apn"'),end='')
    finally:
        shutil.rmtree(snapshot)
    # Repeated snapshots must not exhaust ubusd in the persistent screen feed.
    subprocess.run([sys.executable,str(TOP/'tests/u30pro_bus_lifetime.py')],check=True)
    subprocess.run([sys.executable,str(TOP/'tests/u30pro_display_handoff.py')],check=True)
    subprocess.run([sys.executable,str(TOP/'tests/u30pro_screen_bandwidth.py')],check=True)
    subprocess.run([sys.executable,str(TOP/'tests/u30pro_power_menu.py')],check=True)
    # Execute the real target ucode/UCI module with an isolated configuration
    # and mocked ubus, never attach a physical wireless interface.
    for source in ('u30pro_wifi_native.uc','u30pro_channels.uc','u30pro_carrier.uc'):
        testfile=ROOT/'tmp'/source
        try:
            testfile.write_bytes((TOP/'tests'/source).read_bytes())
            print(run('chroot /target /usr/bin/ucode /tmp/'+source),end='')
        finally:
            testfile.unlink(missing_ok=True)
            shutil.rmtree(ROOT/'tmp/u30pro-wifi-native-fixture',ignore_errors=True)
    # Charge policy uses a synthetic battery/charger, never host /sys.
    test=ROOT/'tmp/u30pro-charge-test';test.mkdir(exist_ok=False)
    try:
        (test/'config').mkdir();(test/'battery/u30pro-battery').mkdir(parents=True)
        (test/'battery/u30pro-charger-master').mkdir()
        for name,value in {'present':'1','capacity':'85','status':'Charging'}.items():
            (test/'battery/u30pro-battery'/name).write_text(value)
        (test/'battery/u30pro-charger-master/charge_behaviour').write_text('auto')
        (test/'config/mu300-charge').write_text("config policy 'main'\noption enabled '1'\noption start '70'\noption stop '80'\n")
        env='MU300_CHARGE_DEVICE=u30pro MU300_CHARGE_SYS=/tmp/u30pro-charge-test/battery MU300_CHARGE_CONFIG=/tmp/u30pro-charge-test/config MU300_CHARGE_STATE=/tmp/u30pro-charge-test/state.json '
        reply=run('chroot /target /usr/bin/env '+env+' /usr/bin/ucode /usr/libexec/unisoc-modem/charge.uc apply')
        value=json.loads(reply)
        assert value['ok'] and value['supported'],value
        assert (test/'battery/u30pro-charger-master/charge_behaviour').read_text()=='inhibit-charge'
        (test/'battery/u30pro-battery/capacity').write_text('65')
        run('chroot /target /usr/bin/env '+env+' /usr/bin/ucode /usr/libexec/unisoc-modem/charge.uc apply')
        assert (test/'battery/u30pro-charger-master/charge_behaviour').read_text()=='auto'
    finally:
        shutil.rmtree(test)
    print('Target ucode compile, panel libraries, services, logical NV map and native charge hysteresis: PASS; no hardware access')


if __name__=='__main__':main()
