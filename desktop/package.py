#!/usr/bin/env python3
"""Keep original Magisk deployment code in payload/, with a thin entry wrapper."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import runpy

TOP = Path(__file__).resolve().parents[1]

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1048576), b''): h.update(block)
    return h.hexdigest()

def original_files(stage):
    return {p.relative_to(stage).as_posix(): sha(p) for p in stage.rglob('*') if p.is_file()}

def assemble(stage, repair, kernel, exe=None):
    stage = Path(stage)
    if (stage/'desktop').exists(): raise ValueError('desktop already exists; use a clean Magisk staging tree')
    before = original_files(stage)
    for required in ('busybox', 'boot-linux.img', 'mu300-openwrt.tar.gz', 'payload.sha256'):
        if required not in before: raise ValueError('missing original Magisk member: '+required)
    layout={}
    for name in before:
        if name=='module.prop' or name.startswith('META-INF/'):
            layout[name]=name
        else:
            target='payload/'+name
            (stage/target).parent.mkdir(parents=True,exist_ok=True)
            (stage/name).rename(stage/target)
            layout[name]=target
    # Magisk still enters at customize.sh. Its deployment script remains
    # unchanged, executing with MODPATH pointing at its relocated payload.
    (stage/'customize.sh').write_text('''#!/system/bin/sh
MU300_DEPLOY_ROOT=$MODPATH
MODPATH=$MU300_DEPLOY_ROOT/payload
. "$MODPATH/customize.sh"
MODPATH=$MU300_DEPLOY_ROOT
unset MU300_DEPLOY_ROOT
''',newline='\n')
    dest = stage/'desktop'; assets = dest/'files/boot'; assets.mkdir(parents=True)
    for n in ('installer.py', 'binary_io.py', 'boot_control.py', 'update_control.py', 'boot_image.py', 'android-image.sh', 'android-write-part.sh', 'android-write-free.sh', 'android-loop.sh', 'local-update.sh', 'configure.sh', 'PORT-SOURCES.json'):
        shared={'binary_io.py':'binary_io.py','boot_control.py':'boot_image.py','update_control.py':'mu300_update.py'}
        shutil.copyfile(TOP/'installer/backend'/shared[n] if n in shared else TOP/'desktop'/n, dest/n)
    # Desktop fixes must apply even when augmenting an older Magisk archive;
    # its original scripts stay byte-for-byte unchanged.
    shutil.copyfile(TOP/'tools/android-bootchain.sh', dest/'android-bootchain.sh')
    shutil.copyfile(TOP/'installer/backend/windows_mtp.py', dest/'windows_mtp.py')
    shutil.copyfile(TOP/'installer/backend/storage.py', dest/'storage.py')
    shutil.copyfile(TOP/'installer/backend/gpt.py', dest/'gpt.py')
    shutil.copyfile(TOP/'rootfs/overlay/opt/mu300/lib/storage.sh', dest/'storage-lib.sh')
    shutil.copyfile(TOP/'rootfs/overlay/opt/mu300/lib/storage.sh', assets/'storage-lib.sh')
    shutil.copyfile(TOP/'android/magisk/mu300-linux-switch/bootchain.sh', dest/'bootchain.sh')
    for p in (TOP/'desktop/boot').iterdir():
        if p.is_file(): shutil.copyfile(p, assets/p.name)
    shutil.copyfile(TOP/'boot/init', assets/'original-init')
    shutil.copyfile(TOP/'boot/internal-ext4.sh', assets/'internal-ext4.sh')
    shutil.copyfile(TOP/'boot/build-boot-image.py', assets/'build-boot-image.py')
    shutil.copyfile(TOP/'rootfs/overlay/opt/mu300/bin/mu300-update', dest/'mu300-update-library.sh')
    spec=importlib.util.spec_from_file_location('repair_prepare',TOP/'desktop/prepare-tools.py')
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    (assets/'repair').mkdir(); mod.prepare(Path(repair),assets/'repair')
    (stage/'docs').mkdir()
    shutil.copyfile(TOP/'desktop/README.md',stage/'docs/DESKTOP-INSTALL.md')
    shutil.copyfile(TOP/'desktop/START-HERE.md',stage/'START-HERE.md')
    shutil.copyfile(TOP/'desktop/installer.py',stage/'OpenWrt-Installer.py')
    extras=stage/'extras/android';extras.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(TOP/'android/magisk/webui-plugins/mu300-system-switch.js',extras/'openwrt-system-switch-plugin.js')
    # Python source runs from the archive root, so resolve its support files there.
    if exe and not Path(exe).is_dir():
        fingerprint=json.loads(Path(str(exe)+'.json').read_text())
        current={n:sha(TOP/'desktop'/n) for n in ('installer.py','boot_image.py')}
        if fingerprint['source'] != current: raise ValueError('desktop EXE is stale; rebuild it')
        (stage/'tools/legacy').mkdir(parents=True,exist_ok=True)
        shutil.copyfile(exe,stage/'tools/legacy/installer.exe')
    actions={'start-installer':'','install-to-tf-card':'--tf','install-to-internal-storage':'--part',
             'install-to-userdata-image':'--data','reinstall-internal-system':'--reinstall-part',
             'resume-internal-install':'--resume','switch-to-openwrt':'--boot-openwrt','update-openwrt':'--update'}
    launchers=stage/'launchers';launchers.mkdir()
    for name,flags in actions.items():
        cmd='@echo off\r\ncd /d "%~dp0.."\r\nif exist "tools\\backend\\openwrt-backend.exe" (\r\n  "tools\\backend\\openwrt-backend.exe" --cli --package-root "%cd%" '+flags+' --no-pause %*\r\n) else (\r\n  py -3 "OpenWrt-Installer.py" '+flags+' --no-pause %*\r\n)\r\npause\r\n'
        (launchers/(name+'.cmd')).write_bytes(cmd.encode())
        (launchers/(name+'.sh')).write_text('#!/bin/sh\ncd "$(dirname "$0")/.." || exit 1\n'
            'if [ "$(uname -s)" = Darwin ] && [ -x ./tools/macos/backend/openwrt-backend ]; then\n'
            '  exec ./tools/macos/backend/openwrt-backend --cli --package-root "$PWD" '+flags+' "$@"\n'
            'fi\nexec python3 OpenWrt-Installer.py '+flags+' "$@"\n',newline='\n')
        (launchers/(name+'.sh')).chmod(0o755)
    adb_dir=os.environ.get('MU300_DESKTOP_ADB_DIR')
    if adb_dir:
        (stage/'tools').mkdir(exist_ok=True)
        for n in ('adb.exe','AdbWinApi.dll','AdbWinUsbApi.dll'):
            shutil.copyfile(Path(adb_dir)/n,stage/'tools'/n)
    if os.environ.get('OPENWRT_GUI_OPTIONAL') != '1':
        runpy.run_path(str(TOP/'installer/package_gui.py'))['attach'](stage,exe if exe and Path(exe).is_dir() else None)
        runpy.run_path(str(TOP/'tools/package-diff-studio.py'))['attach'](stage)
    # This guide is packaged per firmware; keep the shared GUI sources unchanged.
    guide = stage/'START-HERE.md'
    login = ('**MU300 / F50 / V50 / U30Air initial login:** username `root`, password `root`. '
             'Updates that preserve settings retain your existing password.\n\n'
             '**MU300 / F50 / V50 / U30Air 首次登录：**用户名 `root`，默认密码 `root`。'
             '保留配置更新继续使用原密码。\n\n')
    guide.write_text(login + guide.read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    after={n:sha(stage/layout[n]) for n in before}
    if after!=before: raise ValueError('original Magisk payload changed')
    relocated={layout[n]:h for n,h in before.items()}
    manifest={'format':2,'kernel':kernel,'magisk':relocated,'magisk_layout':layout,
              'desktop':{p.relative_to(stage).as_posix():sha(p) for p in stage.rglob('*') if p.is_file() and p.relative_to(stage).as_posix() not in relocated},
              'source':{n:sha(TOP/'desktop'/n) for n in ('installer.py','boot_image.py')}}
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--tools',type=Path,required=True)
    p.add_argument('--kernel',choices=('6.18','7.2'),required=True);p.add_argument('--exe',type=Path)
    a=p.parse_args();assemble(a.stage,a.tools,a.kernel,a.exe)
