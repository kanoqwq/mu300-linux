#!/usr/bin/env python3
"""Assemble standalone Diff Studio using the existing fingerprinted Tauri build.

Compile installer/build.py once on the native platform, then run this tool.
No second npm install, Rust target tree or Python runtime copy is built here.
"""
import argparse
from pathlib import Path
import shutil
import json
from package_gui import digest
from package_gui import sources, validate_artifact
from build import native_platform


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifact', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--refresh', action='store_true', help='refresh an unchanged owned Windows tool directory')
    a = p.parse_args()
    installer = Path(__file__).resolve().parent
    platform = native_platform()
    mac = platform == 'macos-arm64'
    artifact = a.artifact or installer / ('artifacts/macos-arm64' if mac else 'artifacts/windows')
    validate_artifact(artifact, platform, sources())
    if a.output.exists():
        if not a.refresh or mac:
            raise ValueError('Select a new output directory (Windows --refresh requires an unchanged owned directory)')
        recorded=json.loads((a.output/'diff-tool.json').read_text(encoding='utf-8'))
        expected={'OpenWrt-Diff-Studio.exe','OpenWrt-Diff-Studio.py'}
        expected.update('backend/'+n for n in ('__init__.py','ota_engine.py','ota_package.py','ota_desktop.py','ota_bundle.py','ota_runtime.py','boot.py','rpc.py','proxy.sh','lz4raw-aarch64.so','errors.js','unisoc-ota.js','unisoc-ota.json'))
        expected.update(path.relative_to(artifact).as_posix() for path in (artifact/'tools/backend').rglob('*') if path.is_file())
        actual={path.relative_to(a.output).as_posix() for path in a.output.rglob('*') if path.is_file() and path.name!='diff-tool.json'}
        if recorded.get('protocol')!=1 or set(recorded.get('files',{}))!=expected or actual!=expected:
            raise ValueError('Tool file layout changed; use a new directory')
        for name,want in recorded['files'].items():
            source=(a.output/name).resolve()
            if not source.is_relative_to(a.output.resolve()) or digest(source)!=want:
                raise ValueError('Tool directory has local changes; use a new directory')
    else:
        a.output.mkdir(parents=True)
    try:
        if mac:
            import plistlib
            import subprocess
            app = a.output / 'OpenWrt-Diff-Studio.app'
            shutil.copytree(artifact / 'OpenWrt-Installer.app', app)
            old = app / 'Contents/MacOS/openwrt-installer'
            old.rename(old.with_name('openwrt-diff-studio'))
            info = app / 'Contents/Info.plist'
            metadata = plistlib.loads(info.read_bytes())
            metadata.update(CFBundleExecutable='openwrt-diff-studio', CFBundleName='OpenWrt Diff Studio',
                            CFBundleDisplayName='OpenWrt Diff Studio', CFBundleIdentifier='org.openwrt.diff-studio')
            info.write_bytes(plistlib.dumps(metadata))
            subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
            dest = a.output / 'tools/macos/backend'
            shutil.copytree(artifact / 'tools/macos/backend', dest)
        else:
            shutil.copy2(artifact / 'OpenWrt-Installer.exe', a.output / 'OpenWrt-Diff-Studio.exe')
            shutil.copytree(artifact / 'tools/backend', a.output / 'tools/backend', dirs_exist_ok=a.refresh)
        shutil.copy2(installer / 'ota-tool.py', a.output / 'OpenWrt-Diff-Studio.py')
        (a.output / 'backend').mkdir(exist_ok=a.refresh)
        for name in ('__init__.py', 'ota_engine.py', 'ota_package.py', 'ota_desktop.py', 'ota_bundle.py', 'ota_runtime.py', 'boot.py', 'rpc.py', 'proxy.sh', 'lz4raw-aarch64.so', 'errors.js', 'unisoc-ota.js', 'unisoc-ota.json'):
            source = installer.parent/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py' if name=='ota_engine.py' else installer.parent/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota'/name if name in ('boot.py','rpc.py','proxy.sh','lz4raw-aarch64.so','errors.js','unisoc-ota.js','unisoc-ota.json') else installer/'backend'/name
            if name == 'lz4raw-aarch64.so': source = installer/'ota-native'/name
            extra = {'errors.js':'htdocs/luci-static/resources/unisoc-ota/errors.js','unisoc-ota.js':'htdocs/luci-static/resources/view/system/unisoc-ota.js','unisoc-ota.json':'root/usr/share/rpcd/acl.d/unisoc-ota.json'}
            if name in extra: source = installer.parent/'openwrt/luci-app-unisoc-ota'/extra[name]
            shutil.copy2(source, a.output / 'backend' / name)
        files = {path.relative_to(a.output).as_posix(): digest(path)
                 for path in a.output.rglob('*') if path.is_file() and path.name!='diff-tool.json'}
        (a.output / 'diff-tool.json').write_text(json.dumps(dict(protocol=1, platform=platform, files=files,
                                                               source=sources()), indent=2) + '\n')
    except BaseException:
        # Retain failed output for inspection, never recursively clean user paths.
        raise


if __name__ == '__main__':
    main()
