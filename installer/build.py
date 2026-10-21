"""Build the portable Tauri release on Windows x64 or Apple Silicon macOS."""
import argparse
import json
import os
import platform
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

from package_gui import sources, digest

HERE = Path(__file__).resolve().parent


def run(*args, cwd=HERE):
    subprocess.run(list(map(str,args)),cwd=cwd,check=True)


def native_platform(system=None, machine=None):
    system = sys.platform if system is None else system
    machine = platform.machine() if machine is None else machine
    if system == 'darwin' and machine == 'arm64':
        return 'macos-arm64'
    if system == 'win32' and machine.lower() in ('amd64', 'x86_64'):
        return 'windows-x64'
    raise ValueError('Build natively on Windows x64 or Apple Silicon macOS (Rust 1.90, Node and Python required).')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--skip-ui', action='store_true')
    p.add_argument('--adb-dir', type=Path, help='optional pre-fetched platform-tools directory')
    a = p.parse_args()
    target = native_platform()
    mac = target == 'macos-arm64'
    if mac:
        os.environ.setdefault('MACOSX_DEPLOYMENT_TARGET', '11.0')
    before = sources()
    npm = 'npm' if mac else 'npm.cmd'
    if not a.skip_ui:
        run(npm,'ci','--no-audit','--no-fund','--registry=https://registry.npmjs.org',cwd=HERE/'ui')
        run(npm,'run','tauri','--','build','--no-bundle',cwd=HERE/'ui')
    work = HERE.parent/('work/installer-backend-macos' if mac else 'work/installer-backend')
    run(sys.executable,'-m','PyInstaller','--noconfirm','--clean','--noupx','--optimize','2','--onedir','--console',
        '--name','openwrt-backend','--distpath',work/'dist','--workpath',work/'build','--specpath',work,
        '--hidden-import','lz4.block','--hidden-import','xml.etree.ElementTree','--hidden-import','xml.parsers.expat',
        '--hidden-import','tarfile','--hidden-import','gzip','--hidden-import','zlib','--hidden-import','lzma',
        '--hidden-import','bz2','--hidden-import','http.server','--hidden-import','decimal',
        '--add-data', str(HERE.parent/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py')+os.pathsep+'.',
        '--add-data', str(HERE.parent/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/boot.py')+os.pathsep+'.',
        '--add-data', str(HERE.parent/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/rpc.py')+os.pathsep+'.',
        '--add-data', str(HERE.parent/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/proxy.sh')+os.pathsep+'.',
        '--add-data', str(HERE.parent/'openwrt/luci-app-unisoc-ota/htdocs/luci-static/resources/view/system/unisoc-ota.js')+os.pathsep+'.',
        '--add-data', str(HERE.parent/'openwrt/luci-app-unisoc-ota/htdocs/luci-static/resources/unisoc-ota/errors.js')+os.pathsep+'.',
        '--add-data', str(HERE.parent/'openwrt/luci-app-unisoc-ota/root/usr/share/rpcd/acl.d/unisoc-ota.json')+os.pathsep+'.',
        '--add-data', str(HERE/'ota-native/lz4raw-aarch64.so')+os.pathsep+'.',
        '--collect-all','paramiko','--collect-all','dissect.extfs',HERE/'backend_entry.py')
    artifact = HERE/('artifacts/macos-arm64' if mac else 'artifacts/windows')
    artifact.mkdir(parents=True,exist_ok=True)
    tools = artifact/('tools/macos' if mac else 'tools')
    tools.mkdir(parents=True,exist_ok=True)
    names = ('adb','NOTICE.txt') if mac else ('adb.exe','AdbWinApi.dll','AdbWinUsbApi.dll','NOTICE.txt')
    if a.adb_dir:
        for name in names:
            shutil.copy2(a.adb_dir/name, tools/('ADB-NOTICE.txt' if name=='NOTICE.txt' else name))
    else:
        dependency = json.loads((HERE/('platform-tools-macos.json' if mac else 'platform-tools.json')).read_text(encoding='utf-8'))
        cache = HERE.parent/'work/installer-platform-tools'/('platform-tools-macos-pinned.zip' if mac else 'platform-tools-pinned.zip')
        cache.parent.mkdir(parents=True,exist_ok=True)
        if not cache.is_file() or digest(cache) != dependency['sha256']:
            urllib.request.urlretrieve(dependency['url'],cache)
        if digest(cache) != dependency['sha256']:
            raise ValueError('Platform-tools download checksum mismatch')
        with zipfile.ZipFile(cache) as archive:
            for name in names:
                (tools/('ADB-NOTICE.txt' if name=='NOTICE.txt' else name)).write_bytes(archive.read('platform-tools/'+name))
    if mac:
        (tools/'adb').chmod(0o755)
        app = artifact/'OpenWrt-Installer.app'
        executable = app/'Contents/MacOS/openwrt-installer'
        executable.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(HERE/'ui/src-tauri/target/release/openwrt-installer',executable)
        executable.chmod(0o755)
        (app/'Contents/Resources').mkdir(exist_ok=True)
        shutil.copy2(HERE/'ui/src-tauri/icons/icon.icns',app/'Contents/Resources/icon.icns')
        config = json.loads((HERE/'ui/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
        info = {'CFBundleExecutable':'openwrt-installer','CFBundleIdentifier':config['identifier'],
                'CFBundleName':'OpenWrt Installer','CFBundleDisplayName':'OpenWrt Installer',
                'CFBundleIconFile':'icon.icns',
                'CFBundlePackageType':'APPL','CFBundleShortVersionString':config['version'],'CFBundleVersion':config['version'],
                'LSMinimumSystemVersion':'11.0','NSHighResolutionCapable':True,
                'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True}}
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        run('codesign','--force','--deep','--sign','-',app)
        run('codesign','--verify','--deep','--strict',app)
        architectures = subprocess.check_output(['lipo','-archs',str(executable)],text=True).strip()
        if architectures != 'arm64': raise ValueError('Expected native Apple Silicon GUI: '+architectures)
    else:
        shutil.copy2(HERE/'ui/src-tauri/target/release/openwrt-installer.exe',artifact/'OpenWrt-Installer.exe')
    destination = tools/'backend'
    # Use a clean build directory so removed dependencies cannot survive a rebuild.
    if destination.exists():
        resolved = destination.resolve()
        if not resolved.is_relative_to(artifact.resolve()): raise ValueError('Invalid artifact target')
        shutil.rmtree(resolved)
    shutil.copytree(work/'dist/openwrt-backend',destination)
    subprocess.run([str(tools/('adb' if mac else 'adb.exe')),'version'],check=True,stdout=subprocess.DEVNULL)
    if sources() != before:
        raise ValueError('Source changed during compilation; rebuild before packaging')
    files = {p.relative_to(artifact).as_posix():digest(p) for p in artifact.rglob('*') if p.is_file() and p != artifact/'build.json'}
    (artifact/'build.json').write_text(json.dumps({'protocol':1,'platform':target,'source':before,'files':files},indent=2)+'\n',encoding='utf-8')
    print(artifact)


if __name__ == '__main__':
    main()
