#!/usr/bin/env python3
"""Source-built U30 Pro release stages. No hardware access or device dumps.

Storage/install policy is the maintained MU300 container layout. Only hardware
and the native panel come from the U30 Pro reference. One owned root staging.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import runpy
import gzip
import struct
import time
import zipfile
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile

TOP = Path(__file__).resolve().parents[1]
NATIVE = TOP / 'devices/u30pro/native'
WORK = Path('/home/kano/openwrt-build')
STAGE = WORK / 'package-work/u30pro-release'
ROOT = STAGE / 'root'
OWNER = 'u30pro-mu300-release-v1'
BASE = WORK / 'cache/u30pro-root/openwrt-25.12.5-armsr-armv8-rootfs.tar.gz'
BASE_SHA = '493336e176fdc1d6a377763e87a58c5854c24f51e83e586b209783e4bb648d42'
BOARD = WORK / 'package-work/u30pro-board'
RUNTIME = WORK / 'artifacts/u30pro-runtime'
KERNEL = WORK / 'objects/u30pro/release'
LAYOUT = json.loads((TOP / 'tools/build-layout.json').read_text())
PACKAGES = ('luci', 'luci-i18n-base-zh-cn', 'luci-i18n-firewall-zh-cn',
            'luci-i18n-package-manager-zh-cn', 'bash', 'ip-full', 'iw-full',
            'iwinfo', 'wireless-regdb', 'wifi-scripts', 'wpad-basic-mbedtls', 'openssl-util',
            'curl', 'ca-bundle', 'msmtp', 'flock', 'ttyd', 'luci-app-ttyd',
            'luci-i18n-ttyd-zh-cn', 'python3', 'python3-ctypes', 'python3-openssl',
            'rpcd-mod-ucode', 'ucode-mod-fs', 'ucode-mod-uci', 'ucode-mod-ubus',
            'ucode-mod-uloop', 'ucode-mod-socket', 'ucode-mod-rtnl',
            'ucode-mod-nl80211', 'e2fsprogs', 'resize2fs', 'f2fs-tools',
            'f2fsck', 'losetup', 'blkid', 'i2c-tools', 'gpiod-tools')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def owned():
    if STAGE.is_symlink() or not STAGE.resolve().is_relative_to(WORK.resolve()):
        raise ValueError('Invalid U30 Pro staging location')
    if not (STAGE / '.owner').is_file() or (STAGE / '.owner').read_text().strip() != OWNER:
        raise ValueError('Unowned U30 Pro staging')


def enable_service(root, name):
    """Enable the tracked service once; retire only links owned by that service."""
    service = root / 'etc/init.d' / name
    if not service.is_file():
        raise ValueError('Required service is missing: ' + name)
    service.chmod(0o755)
    text = service.read_text()
    rc = root / 'etc/rc.d'
    rc.mkdir(exist_ok=True)
    wanted = []
    for tag, key in (('S', 'START'), ('K', 'STOP')):
        match = re.search(r'^' + key + r'=[\'"]?([0-9]+)', text, re.M)
        if match:
            wanted.append(tag + f'{int(match[1]):02}' + name)
    target = '../init.d/' + name
    # A refreshed prepared root must not keep both S95 and S14 for the panel.
    for old in rc.glob('[SK][0-9][0-9]' + name):
        if old.name not in wanted and old.is_symlink() and os.readlink(old) == target:
            old.unlink()
    for item in wanted:
        link = rc / item
        if link.is_symlink() and os.readlink(link) == target:
            continue
        if link.exists() or link.is_symlink():
            raise ValueError('Conflicting service entry: ' + str(link))
        link.symlink_to(target)


def bootstrap():
    if STAGE.exists():
        raise ValueError('U30 Pro staging exists; inspect it, do not extract over a prepared root')
    if not BASE.is_file() or digest(BASE) != BASE_SHA:
        raise ValueError('Missing/invalid registered official OpenWrt base; nothing extracted')
    STAGE.mkdir(parents=True)
    (STAGE / '.owner').write_text(OWNER + '\n')
    ROOT.mkdir()
    with tarfile.open(BASE) as archive:
        members = archive.getmembers()
        # Regular content before links; never traverse a previously extracted link.
        for links in (False, True):
            for member in members:
                name = PurePosixPath(member.name.removeprefix('./'))
                if str(name) in ('', '.'):
                    continue
                if name.is_absolute() or '..' in name.parts or '\\' in str(name):
                    raise ValueError('Unsafe official base path')
                if member.issym() != links:
                    continue
                target = ROOT / str(name)
                if not target.parent.resolve().is_relative_to(ROOT.resolve()):
                    raise ValueError('Base path escaped staging')
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    target.mkdir(exist_ok=True)
                elif member.issym():
                    target.symlink_to(member.linkname)
                elif member.isfile() or member.islnk():
                    target.write_bytes(archive.extractfile(member).read())
                    target.chmod(member.mode)
    (STAGE / 'state.json').write_text(json.dumps({'base': BASE_SHA, 'step': 'base-extracted'}))
    print('Official OpenWrt root prepared: ' + str(ROOT))


def dependencies():
    owned()
    command = ['docker', 'run', '--rm', '--platform', 'linux/arm64',
               '-v', str(ROOT) + ':/target', '--entrypoint', '/usr/bin/apk',
               'mu300-openwrt-base:25.12.5', '--root', '/target', '--timeout', '60']
    subprocess.run(command + ['update'], check=True)
    subprocess.run(command + ['add', '--scripts=no', '--commit-hooks=no', *PACKAGES], check=True)
    # Materialize only package user declarations; never start target services.
    subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/arm64',
                    '-v', str(ROOT) + ':/target', '--entrypoint', '/bin/sh',
                    'mu300-openwrt-base:25.12.5', '-ec',
                    'export IPKG_INSTROOT=/target; root=/target; . /target/lib/functions.sh; '
                    'for f in /target/lib/apk/packages/*.rusers; do [ ! -f "$f" ] || add_group_and_user "$f"; done'], check=True)
    (STAGE / 'state.json').write_text(json.dumps({'base': BASE_SHA, 'step': 'dependencies-installed'}))


def copy_tree(source, target):
    if not source.is_dir():
        raise ValueError('Missing source overlay: ' + str(source))
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        destination = target / relative
        if not destination.parent.resolve().is_relative_to(target.resolve()):
            raise ValueError('Source overlay escaped staging: ' + str(relative))
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_symlink():
            destination.unlink(missing_ok=True)
            destination.symlink_to(os.readlink(path))
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink():
                destination.unlink()
            data = path.read_bytes()
            # Windows-edited scripts must have a Linux interpreter line.
            # Compiling ucode alone does not detect an executable '#!...\r'.
            if data.startswith(b'#!'):data=data.replace(b'\r\n',b'\n')
            destination.write_bytes(data)
            executable = data.startswith(b'#!') or relative.parts[0] in ('bin', 'sbin')
            destination.chmod(0o755 if executable else 0o644)


def sources(refresh=False):
    owned()
    state = json.loads((STAGE / 'state.json').read_text())
    allowed=('packaged',) if refresh else ('dependencies-installed', 'sources-installed', 'root-ready')
    if state['step'] not in allowed:
        raise ValueError('Finish target dependency installation before applying sources')
    if refresh:
        if (ROOT/'etc/mu300/platform').read_text().strip()!='u30pro':
            raise ValueError('Retained root is not the registered U30 Pro profile')
        release=Path(state['release'])
        if not release.is_file() or digest(release)!=state['sha256']:
            raise ValueError('Previous verified release receipt is unavailable')
    # Reuse software, configuration and netifd protocol implementation, not any
    # MU300 board blobs or its 6.18/7.2 kernel.
    copy_tree(TOP / 'rootfs/overlay/opt/mu300', ROOT / 'opt/mu300')
    # U30 Pro uses its native CP loader, not the inherited SC2355/UMS9620
    # binary patch adapter (which used to carry firmware-build hash profiles).
    (ROOT / 'opt/mu300/bin/mu300-modem-coldboot').unlink(missing_ok=True)
    copy_tree(TOP / 'rootfs/overlay/usr/share/ucode/mu300', ROOT / 'usr/share/ucode/mu300')
    copy_tree(TOP / 'openwrt/overlay', ROOT)
    plugin = TOP / 'openwrt/luci-app-mu300'
    copy_tree(plugin / 'root', ROOT)
    copy_tree(plugin / 'htdocs', ROOT / 'www')
    translations = ROOT / 'usr/lib/lua/luci/i18n'
    translations.mkdir(parents=True, exist_ok=True)
    for p in (plugin / 'lmo').glob('*.lmo'):
        shutil.copyfile(p, translations / p.name)
    copy_tree(TOP / 'devices/u30pro/overlay', ROOT)
    # Native screen extensions keep their public RPC/module names. Only their
    # persistent directory and hardware actions are adapted, not the protocol.
    native_files = {
        'ui/native-control/overlay/usr/share/u30pro/wallpaper.uc':'usr/share/u30pro/wallpaper.uc',
        'ui/native-control/overlay/usr/libexec/rpcd/u30pro-wallpaper':'usr/libexec/rpcd/u30pro-wallpaper',
        'ui/native-control/overlay/usr/share/rpcd/acl.d/u30pro-wallpaper.json':'usr/share/rpcd/acl.d/u30pro-wallpaper.json',
        'ui/native-control/overlay/www/luci-static/resources/view/system/u30pro-wallpaper.js':'www/luci-static/resources/view/system/u30pro-wallpaper.js',
        'ui/native-control/overlay/www/luci-static/resources/u30pro/kit.js':'www/luci-static/resources/u30pro/kit.js',
        'ui/native-control/overlay/www/luci-static/resources/u30pro/kit.css':'www/luci-static/resources/u30pro/kit.css',
        'feed/packages/u30pro-feed/files/etc/apk/keys/u30pro-feed.pem':'etc/apk/keys/u30pro-feed.pem',
        'feed/packages/u30pro-feed-immortalwrt/files/etc/apk/keys/immortalwrt-25.12.pem':'etc/apk/keys/immortalwrt-25.12.pem',
        'feed/packages/u30pro-feed-immortalwrt/files/etc/apk/repositories.d/immortalwrt.list':'etc/apk/repositories.d/immortalwrt.list',
    }
    for source,destination in native_files.items():
        path=ROOT/destination;path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(NATIVE/source,path)
        path.chmod(0o755 if path.read_bytes().startswith(b'#!') else 0o644)
    # Shared modem service has one C transport and one ucode policy owner.
    transport = Path(LAYOUT['at_transport'])
    checker = importlib.util.spec_from_file_location('io_artifact', TOP / 'tools/atd-io/artifact.py')
    module = importlib.util.module_from_spec(checker)
    checker.loader.exec_module(module)
    module.check(transport)
    shutil.copyfile(transport, ROOT / 'opt/mu300/bin/mu300-atd-io')
    (ROOT / 'opt/mu300/bin/mu300-atd-io').chmod(0o755)
    runtime = json.loads((RUNTIME / 'runtime.json').read_text())
    for name, row in runtime['files'].items():
        path = RUNTIME / name
        if digest(path) != row['sha256']:
            raise ValueError('Hardware runtime changed: ' + name)
        destination = ROOT / ('opt/u30pro/bin' if name == 'u30pro-panel' else 'opt/mu300/bin' if name == 'sml-voltage-check' else 'usr/sbin') / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        destination.chmod(0o755)
    board = json.loads((BOARD / 'board-inputs.json').read_text())
    for name, row in board['files'].items():
        if not name.startswith(('etc/u30pro/modem/', 'odm/firmware/aic8800/')):
            continue
        path = BOARD / name
        if digest(path) != row['sha256']:
            raise ValueError('Hardware input changed: ' + name)
        relative = ('opt/mu300/vendor/u30pro/modem/'+name.removeprefix('etc/u30pro/modem/')
                    if name.startswith('etc/u30pro/modem/') else
                    'opt/mu300/vendor/u30pro/firmware/aic8800/'+name.removeprefix('odm/firmware/aic8800/'))
        destination = ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    usb = NATIVE / 'ui/native-control/overlay/usr/sbin/u30pro-usb'
    shutil.copyfile(usb, ROOT / 'usr/sbin/u30pro-usb')
    (ROOT / 'usr/sbin/u30pro-usb').chmod(0o755)
    # Install exactly the modules selected by this kernel's modules.order.
    report = json.loads((KERNEL / 'u30pro-build.json').read_text())
    if digest(KERNEL / 'arch/arm64/boot/Image') != report['image_sha256'] or digest(KERNEL / '.config') != report['config_sha256']:
        raise ValueError('Kernel/Image/config receipt mismatch')
    module_root = ROOT / 'lib/modules' / report['version']
    module_root.mkdir(parents=True, exist_ok=True)
    old_order=set((module_root/'modules.order').read_text().splitlines()) if (module_root/'modules.order').is_file() else set()
    seen = set()
    for name in (KERNEL / 'modules.order').read_text().splitlines():
        name = name.strip()
        # Linux 6.12 records composite .o names in modules.order; the loadable
        # artifact is .ko. Never ship intermediate relocation objects as modules.
        if name.endswith('.o'):
            (module_root / Path(name).name).unlink(missing_ok=True)
            name = name[:-2] + '.ko'
        if '..' in PurePosixPath(name).parts or name.startswith('/'):
            raise ValueError('Unsafe module output name')
        if Path(name).name in seen:
            raise ValueError('Flattened kernel module collision: '+name)
        seen.add(Path(name).name)
        metadata = subprocess.check_output(['modinfo','-F','vermagic',str(KERNEL/name)],text=True)
        if metadata.split()[0] != report['version']:
            raise ValueError('Kernel module vermagic mismatch: '+name)
        shutil.copyfile(KERNEL / name, module_root / Path(name).name)
    for name in ('modules.builtin', 'modules.builtin.modinfo'):
        shutil.copyfile(KERNEL / name, module_root / name)
    (module_root/'modules.order').write_text(''.join(name+'\n' for name in sorted(seen)))
    for name in old_order-seen:
        if Path(name).name!=name or not name.endswith('.ko'):
            raise ValueError('Invalid previously owned module name')
        (module_root/name).unlink(missing_ok=True)
    # Same MU300 root assembly rule: distro armsr modules are for another
    # kernel build. Keep package metadata, but never ship those unused .ko's.
    for directory in (ROOT/'lib/modules').iterdir():
        if directory==module_root:continue
        if directory.is_symlink() or directory.resolve().parent!=(ROOT/'lib/modules').resolve():
            raise ValueError('Unexpected module root')
        if not re.fullmatch(r'6\.12\.94',directory.name):
            raise ValueError('Unregistered distribution module directory: '+directory.name)
        shutil.rmtree(directory)
    # Firmware follows MU300's protected per-device vendor namespace. Keep the
    # hardware driver's original firmware path through a relative link only.
    link=ROOT/'odm/firmware/aic8800'
    if link.is_dir() and not link.is_symlink():
        if any(p.name not in {Path(n).name for n in board['files'] if n.startswith('odm/firmware/aic8800/')} for p in link.iterdir()):
            raise ValueError('Unowned files in old firmware staging')
        shutil.rmtree(link)
    if not link.is_symlink():link.symlink_to('../../opt/mu300/vendor/u30pro/firmware/aic8800')
    old=ROOT/'etc/u30pro/modem'
    if old.is_dir() and not old.is_symlink():shutil.rmtree(old)
    mapfile = ROOT / 'opt/mu300/vendor/u30pro/modem/map.tsv'
    logical = [row.split()[0] for row in mapfile.read_text().splitlines() if row.strip()]
    if any(not re.fullmatch(r'[A-Za-z0-9_]+', name) for name in logical):
        raise ValueError('Invalid logical modem partition map')
    mapfile.write_text(''.join(name + ' /dev/block/by-name/' + name + '\n' for name in logical))
    config = ROOT / 'etc/config/u30pro_native_control'
    config.write_text("config control 'settings'\n\toption home_short_action 'page-next'\n\toption home_long_action 'none'\n\toption wifi_admin '1'\n")
    (STAGE / 'state.json').write_text(json.dumps({'base': BASE_SHA, 'step': 'sources-installed'}))
    print('MU300 root sources and U30 Pro hardware installed: ' + str(ROOT))


def refresh_sources():
    """Incrementally refresh the owned retained root after verified assembly."""
    sources(refresh=True)


def target(command):
    return subprocess.check_output(['docker', 'run', '--rm', '--platform', 'linux/arm64',
        '-v', str(ROOT) + ':/target', '--entrypoint', '/bin/sh',
        'mu300-openwrt-base:25.12.5', '-ec', command], text=True)


def apply_alternatives(root):
    """Apply package-declared command priorities without running service scripts."""
    chosen={}
    for manifest in sorted((root/'lib/apk/packages').glob('*.alternatives')):
        for row in manifest.read_text().split():
            priority,link,destination=row.split(':',2)
            if not priority.isdigit() or any(not n.startswith('/') or '..' in PurePosixPath(n).parts for n in (link,destination)):
                raise ValueError('Invalid package alternative: '+str(manifest))
            previous=chosen.get(link)
            candidate=(int(priority),destination)
            if previous is None or candidate[0]>previous[0]:chosen[link]=candidate
            elif candidate[0]==previous[0] and candidate[1]!=previous[1]:
                raise ValueError('Conflicting package alternative: '+link)
    for link,(_,destination) in chosen.items():
        path=root/link.lstrip('/'); target=root/destination.lstrip('/')
        if not target.is_file():raise ValueError('Missing alternative target: '+destination)
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.parent.resolve().is_relative_to(root.resolve()):raise ValueError('Alternative escaped root: '+link)
        if path==target:raise ValueError('Self-referencing package alternative: '+link)
        path.unlink(missing_ok=True)
        path.symlink_to(os.path.relpath(target,path.parent))


def finish():
    owned()
    if json.loads((STAGE/'state.json').read_text())['step'] != 'sources-installed':
        raise ValueError('Apply current sources before root finalization')
    bb = ROOT / 'opt/mu300/bin/busybox'
    shutil.copyfile(LAYOUT['busybox'], bb); bb.chmod(0o755)
    applets = target('/target/opt/mu300/bin/busybox --list').splitlines()
    bindir = ROOT / 'opt/mu300/busybox-bin'; bindir.mkdir(exist_ok=True)
    for applet in applets:
        if not re.fullmatch(r'[A-Za-z0-9_\[.\]-]+', applet):
            raise ValueError('Invalid busybox applet')
        link = bindir / applet
        if not link.is_symlink(): link.symlink_to('../bin/busybox')
        # Fill absent target tools, without replacing package-owned programs.
        if not any((ROOT / folder / applet).exists() or (ROOT / folder / applet).is_symlink()
                   for folder in ('bin', 'sbin', 'usr/bin', 'usr/sbin')):
            (ROOT / 'bin' / applet).symlink_to('/opt/mu300/bin/busybox')
    etc = ROOT / 'etc/mu300'; etc.mkdir(exist_ok=True)
    (etc/'hotspot.conf').write_text('SSID=U30Pro-OpenWrt\nPSK=openwrt1234\nBAND=5\nCHANNEL=36\nCOUNTRY=CN\n')
    (etc/'default-boot').write_text('android\n')
    (ROOT/'etc/inittab').write_text('::sysinit:/etc/init.d/rcS S boot\n::shutdown:/etc/init.d/rcS K shutdown\nttyGS0::askfirst:/usr/libexec/login.sh\n')
    password = subprocess.check_output(['openssl','passwd','-6','-salt','u30pro','root'], text=True).strip()
    shadow = ROOT/'etc/shadow'
    shadow.write_text(re.sub(r'^root:[^:]*:', 'root:' + password + ':', shadow.read_text(), flags=re.M))
    shadow.chmod(0o600)
    defaults = ROOT/'etc/uci-defaults/99-u30pro'
    defaults.write_text("#!/bin/sh\nuci set system.@system[0].hostname='U30Pro'\nuci set system.@system[0].zonename='Asia/Shanghai'\nuci set system.@system[0].timezone='CST-8'\nuci set unisoc_modem.main.at_port='/dev/stty_nr2'\nuci set unisoc_modem.main.sim_slots='2'\nuci set unisoc_modem.main.platform_defaults='u30pro'\nuci set luci.main.lang='zh_cn'\nuci commit system\nuci commit unisoc_modem\nuci commit luci\nexit 0\n")
    defaults.chmod(0o755)
    target("zone=$(uci -c /target/etc/config show firewall | sed -n \"s/^firewall\\.\\([^.=]*\\)\\.name='wan'$/\\1/p\"); "
           "[ -n \"$zone\" ]; uci -c /target/etc/config -q del_list firewall.$zone.network=wwan || true; "
           "uci -c /target/etc/config add_list firewall.$zone.network=wwan; uci -c /target/etc/config commit firewall")
    # Explicit service ownership. Never enable old MU300 Android chroots,
    # SC2355 LED/key/audio/DWC3 handlers, nor the reference celld/atd.
    selected = ('boot','sysfixtime','system','sysctl','log','urandom_seed','urngd',
        'network','dnsmasq','odhcpd','firewall','dropbear','rpcd','uhttpd','wpad',
        'cron','sysntpd','done','umount','u30pro-watchdog','u30pro-factory','mu300-cpu-driver','mu300-vendor','mu300-hw',
        'mu300-atd','mu300-smsd','mu300-network-recovery','mu300-accounts','mu300-user-packages','ttyd',
        'mu300-ndp','mu300-charge','u30pro-panel','mu300-post','unisoc-modem-ui',
        'unisoc-cpu','unisoc-cpu-cores','unisoc-network-performance','unisoc-sms-forward',
        'unisoc-traffic','unisoc-ttl')
    rc = ROOT/'etc/rc.d'; rc.mkdir(exist_ok=True)
    for name in ('S09mu300-vendor', 'S15mu300-wifi-client'):
        old=rc/name
        if old.is_symlink() and os.readlink(old)=='../init.d/'+name[3:]:old.unlink()
    for name in selected:
        enable_service(ROOT, name)
    apply_alternatives(ROOT)
    report = json.loads((KERNEL/'u30pro-build.json').read_text())
    subprocess.run(['depmod','-b',str(ROOT),'-F',str(KERNEL/'System.map'),report['version']],check=True)
    sys.path.insert(0,str(TOP/'installer'))
    runpy.run_path(str(TOP/'installer/stage-ota.py'))['stage'](ROOT)
    theme=Path(LAYOUT['theme'])
    subprocess.run(['docker','run','--rm','--platform','linux/arm64','-v',str(ROOT)+':/target',
        '-v',str(theme)+':/theme.apk:ro','--entrypoint','/usr/bin/apk',
        'mu300-openwrt-base:25.12.5','--root','/target','--no-network','add','--allow-untrusted',
        '--scripts=no','--commit-hooks=no','/theme.apk'],check=True)
    defaults.write_text(defaults.read_text().replace('exit 0\n',
        "uci set luci.main.mediaurlbase='/luci-static/aurora'\nuci commit luci\nexit 0\n"))
    patched=STAGE/'shared-patches.json'
    applied=json.loads(patched.read_text()) if patched.is_file() else {}
    for name, relative in (('fw4-sipa-offload.patch','usr/share/ucode/fw4.uc'),
                           ('wifi-scripts-no-dfs.patch','usr/share/ucode/wifi/hostapd.uc')):
        source=TOP/'openwrt/patches'/name; file=ROOT/relative
        before=applied.get(name)
        if before:
            if before!={'patch':digest(source),'result':digest(file)}:
                raise ValueError('Shared root patch source/result changed: '+name)
        else:
            subprocess.run(['patch','--batch','--fuzz=0','-d',str(ROOT),'-p1','-i',str(source)],check=True)
            applied[name]={'patch':digest(source),'result':digest(file)}
            patched.write_text(json.dumps(applied,indent=2)+'\n')
    baseline=target('MU300_LIB=1; export MU300_LIB; . /target/opt/mu300/bin/mu300-update; upk_manual /target openwrt')
    if not baseline.strip():raise ValueError('Image package baseline is empty')
    (etc/'image-packages').write_text(baseline)
    (ROOT/'etc/.mu300-accounts-from-image').touch()
    # This native board does not use the SC2355 firmware-SAE bridge.
    bad = [p for p in ROOT.rglob('*') if p.is_file() and p.name in ('xray','sing-box','singbox','openvpn')]
    if bad: raise ValueError('Unexpected bundled VPN engine')
    (STAGE/'state.json').write_text(json.dumps({'base':BASE_SHA,'step':'root-ready'}))
    print('U30 Pro root ready; MU300 services, native display/CP/USB, no reference updater')


def canonical_boot():
    owned()
    check = runpy.run_path(str(TOP/'tools/u30pro-build.py'))
    check['check_sources']()
    _, paths = check['load_layout'](TOP/'tools/build-layout.json', WORK)
    check['check_inputs'](paths)
    report = json.loads((KERNEL/'u30pro-build.json').read_text())
    image = KERNEL/'arch/arm64/boot/Image'
    if digest(image) != report['image_sha256'] or digest(KERNEL/'.config') != report['config_sha256']:
        raise ValueError('Kernel receipt mismatch')
    wrapped = STAGE/'Image.wrapped'
    if wrapped.is_file():
        proof = json.loads(wrapped.with_suffix('.manifest.json').read_text())
        if proof['input_sha256'] != digest(image) or proof['output_sha256'] != digest(wrapped):
            raise ValueError('Wrapped Image is stale; inspect owned staging')
    else:
        subprocess.run([sys.executable,str(NATIVE/'kernel/ums9632/wrap_image.py'),
                        '--image',str(image),'--out',str(wrapped)],check=True)
    fw = runpy.run_path(str(NATIVE/'tools/firmware.py'))
    builder = runpy.run_path(str(TOP/'boot/build-boot-image.py'))
    files = {}
    for name, path in {
        'init':TOP/'devices/u30pro/init', 'bin/busybox':Path(LAYOUT['busybox']),
        'etc/u30pro-boot-wait.sh':TOP/'devices/u30pro/boot-wait.sh',
        'usr/sbin/u30pro-nodes':RUNTIME/'u30pro-nodes',
        'usr/sbin/u30pro-usb':NATIVE/'ui/native-control/overlay/usr/sbin/u30pro-usb'}.items():
        files[name] = (path.read_bytes().replace(b'\r\n',b'\n') if name != 'bin/busybox' and path.read_bytes().startswith(b'#!') else path.read_bytes(), stat.S_IFREG|0o755)
    ram = builder['lz4_legacy'](builder['cpio_archive']({'dev','proc','sys','run','tmp','disk','newroot'},files))
    stock = (paths['stock_capture']/'boot.img').read_bytes()
    header = bytearray(stock[:4096]); kernel = wrapped.read_bytes()
    struct.pack_into('<II',header,8,len(kernel),len(ram))
    struct.pack_into('<I',header,1580,0)
    header[44:1580] = b'loglevel=5\0'.ljust(1536,b'\0')
    result = fw['retain_avb_container'](stock,bytes(header)+kernel+bytes(-len(kernel)%4096)+ram)
    if len(result) != 64<<20 or 4096+len(kernel)+len(ram)>48<<20:
        raise ValueError('Canonical boot does not fit the MU300 boot ABI')
    (STAGE/'boot-linux.img').write_bytes(result)
    print('Canonical MU300 boot built with wrapped U30 Pro Image; device/slot binding is deferred to installation')


def boot_smoke(package_root):
    import zlib
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    builder=load('native_package_repacker',package_root/'desktop/boot_image.py')
    native=load('native_package_containers',package_root/'desktop/native_u30pro.py')
    parser=load('native_package_metadata',TOP/'installer/runtime/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/boot.py')
    base=(package_root/'payload/boot-linux.img').read_bytes()
    stock=Path(LAYOUT['stock_capture'])
    templates={name:(stock/(name+'.img')).read_bytes() for name in ('boot','vendor_boot','init_boot')}
    proof=[]
    for slot,mode in (('a','part'),('b','part'),('a','prepare'),('b','install-part')):
        live=bytearray(32);live[:4]=('_'+slot).encode()+b'\0\0';live[4:8]=b'BCAB';live[8]=1;live[9]=2
        live[12]=0x8f;live[14]=0x8e
        if slot=='b':live[12],live[14]=live[14],live[12]
        struct.pack_into('<I',live,28,zlib.crc32(live[:28]))
        image=builder.build(base,package_root/'desktop/files/boot',mode,bytes(live),slot,
            prepare='CID:mmcblk0p99:1:2:3' if mode=='prepare' else None,
            identity='CID:mmcblk0p99:1:2:3' if mode=='install-part' else None)
        metadata=parser.metadata(image,'mu300',lambda n:n in ('init','etc/mu300-android-slot','etc/mu300-desktop-root-mode','etc/u30pro-boot-wait.sh'))
        assert metadata['etc/mu300-android-slot'][0]==(slot+'\n').encode()
        assert metadata['etc/mu300-desktop-root-mode'][0]==(mode+'\n').encode()
        assert b'U30PRO-MU300 storage bootstrap' in metadata['init'][0]
        assert metadata['etc/u30pro-boot-wait.sh'][0] == (TOP/'devices/u30pro/boot-wait.sh').read_bytes().replace(b'\r\n', b'\n')
        outputs=native.images(package_root,image,templates)
        proof.append({'android_slot':slot,'linux_slot':'b' if slot=='a' else 'a','mode':mode,
            'images':{name:hashlib.sha256(data).hexdigest() for name,data in outputs.items()}})
        print('Packaged boot/stock-DTB/slot smoke: '+slot+' -> '+mode,flush=True)
    (package_root/'docs/BOOT-SMOKE.json').write_text(json.dumps({'device_writes':False,'cases':proof},indent=2)+'\n')


def package():
    owned()
    if json.loads((STAGE/'state.json').read_text())['step'] != 'root-ready':
        raise ValueError('Root is not finalized')
    package_root = STAGE/'package'
    if package_root.exists():
        raise ValueError('Package staging already exists; inspect it before clean-staging')
    # Validate source-pinned shared installers before assembling anything.
    sys.path.insert(0,str(TOP/'installer'))
    gui = runpy.run_path(str(TOP/'installer/package_gui.py'))
    artifacts = Path(LAYOUT['installer_artifacts'])
    for platform,name in (('windows-x64','windows'),('macos-arm64','macos-arm64')):
        gui['validate_artifact'](artifacts/name,platform,gui['sources']())
    os.environ.update(OPENWRT_GUI_ARTIFACT=str(artifacts/'windows'),
        OPENWRT_GUI_MACOS_ARTIFACT=str(artifacts/'macos-arm64'),
        OPENWRT_GUI_ANDROID_ARTIFACT=str(artifacts/'android'))
    runpy.run_path(str(TOP/'installer/package_android.py'))['artifact'](artifacts/'android')
    canonical_boot()
    report = json.loads((KERNEL/'u30pro-build.json').read_text())
    release = runpy.run_path(str(TOP/'tools/release-identity.py'))['identify'](TOP,'u30pro',report['version'])
    release['preview'] = 1
    release['storage_layout'] = 'mu300root-container'
    release['hardware_reference'] = json.loads((TOP/'devices/u30pro/profile.json').read_text())['hardware_reference']['commit']
    (ROOT/'etc/mu300/release.json').write_text(json.dumps(release,indent=2)+'\n')
    (ROOT/'etc/mu300/image-version').write_text(release['image_version']+'\n')
    (ROOT/'etc/unisoc-ota/current.json').write_text(json.dumps({'family':'mu300','profile':'u30pro',
        'version':release['image_version'],'kernel_release':report['version']})+'\n')
    package_root.mkdir()
    # Only one root archive and one canonical boot in the distribution.
    with tarfile.open(package_root/'mu300-openwrt.tar.gz','w:gz',compresslevel=3,dereference=False) as archive:
        for path in sorted(ROOT.iterdir()): archive.add(path,arcname=path.name)
    shutil.copyfile(STAGE/'boot-linux.img',package_root/'boot-linux.img')
    shutil.copyfile(LAYOUT['busybox'],package_root/'busybox')
    files = {
        'module.prop':TOP/'android/magisk/mu300-openwrt-tf/module.prop',
        'locale.sh':TOP/'android/magisk/mu300-openwrt-tf/locale.sh',
        'switch.sh':TOP/'android/magisk/mu300-linux-switch/switch.sh',
        'bootchain.sh':TOP/'android/magisk/mu300-linux-switch/bootchain.sh'}
    for name in ('android-install.sh','android-bootchain.sh','mu300-vendor-from-device.sh',
                 'tf-storage.sh','tf-stage.sh','tf-install-worker.sh'):
        files[name]=TOP/'tools'/name
    for name,path in files.items(): shutil.copyfile(path,package_root/name)
    shutil.copyfile(ROOT/'opt/mu300/vendor/u30pro/modem/map.tsv',package_root/'u30pro-map.tsv')
    (package_root/'module.prop').write_text('id=u30pro_openwrt_preview\nname=U30 Pro OpenWrt preview (desktop installer)\nversion='+release['image_version']+'\nversionCode='+str(release['build_epoch'])+'\nauthor=minikano\ndescription=Extract and use OpenWrt Installer; no direct Magisk flashing in preview 1\n')
    # A Magisk shell cannot construct U30 Pro's three boot containers. Do not
    # let the legacy MU300 single-boot worker flash an incomplete chain.
    (package_root/'customize.sh').write_text("#!/system/bin/sh\nabort 'U30 Pro preview: extract ZIP and use OpenWrt Installer; Magisk flashing is not supported in preview 1.'\n")
    runpy.run_path(str(TOP/'tools/tf-payload-manifest.py'))['manifest'](package_root)
    runpy.run_path(str(TOP/'desktop/package.py'))['assemble'](package_root,Path(LAYOUT['maintenance']),report['version'])
    (package_root/'desktop/release.json').write_text(json.dumps(release,indent=2)+'\n')
    (package_root/'START-HERE.md').write_text((TOP/'devices/u30pro/START-HERE.md').read_text(),encoding='utf-8')
    shutil.copyfile(STAGE/'Image.manifest.json',package_root/'docs/KERNEL-WRAPPER.json')
    shutil.copyfile(KERNEL/'u30pro-build.json',package_root/'docs/KERNEL-BUILD.json')
    shutil.copyfile(RUNTIME/'runtime.json',package_root/'docs/HARDWARE-RUNTIME.json')
    boot_smoke(package_root)
    manifest_path=package_root/'desktop/manifest.json'
    manifest=json.loads(manifest_path.read_text())
    for path in package_root.rglob('*'):
        if path.is_file() and path!=manifest_path:
            name=path.relative_to(package_root).as_posix()
            if name not in manifest['magisk']: manifest['desktop'][name]=digest(path)
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    # The shared package checker must accept the assembled MU300-family layout.
    from backend.package import inspect_package
    inspect_package(package_root)
    output_dir=TOP/'release'; output_dir.mkdir(exist_ok=True)
    output=output_dir/(release['package_name']+'-preview1.zip')
    if output.exists(): raise ValueError('Existing release will not be overwritten')
    from archive_tools import write_member
    candidate=output.with_suffix('.zip.pending')
    with zipfile.ZipFile(candidate,'x',zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
        for path in sorted(package_root.rglob('*')):
            if path.is_file(): write_member(archive,path,path.relative_to(package_root).as_posix())
    runpy.run_path(str(TOP/'tools/build-release.py'))['verify_archive'](
        candidate,require_diff=True,require_android=True)
    candidate.rename(output)
    sha=digest(output)
    Path(str(output)+'.sha256').write_text(sha+'  '+output.name+'\n')
    (STAGE/'state.json').write_text(json.dumps({'base':BASE_SHA,'step':'packaged','release':str(output),'sha256':sha}))
    if package_root.is_symlink() or package_root.resolve().parent != STAGE.resolve():
        raise ValueError('Package staging cleanup escaped its owner')
    shutil.rmtree(package_root)
    print(json.dumps({'release':str(output),'sha256':sha,'bytes':output.stat().st_size,'hardware_tested':False}))


def clean_staging():
    owned()
    path=STAGE/'package'
    if path.is_symlink() or path.resolve().parent!=STAGE.resolve():
        raise ValueError('Refusing unowned package staging')
    if path.exists():shutil.rmtree(path)
    for name in ('Image.wrapped','Image.manifest.json','boot-linux.img'):
        (STAGE/name).unlink(missing_ok=True)
    print('Removed owned package assembly staging only; prepared root, inputs and releases retained')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=('bootstrap', 'dependencies', 'sources', 'refresh-sources', 'finish', 'canonical_boot', 'package','clean-staging'))
    args = p.parse_args()
    if os.environ.get('U30PRO_BUILD_LOCKED') != '1':
        raise SystemExit('Use tools/build-release.py run')
    globals()[args.stage.replace('-','_')]()
