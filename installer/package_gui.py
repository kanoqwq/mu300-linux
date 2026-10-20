"""Attach a fingerprinted portable desktop build to an existing package."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()


def sources():
    paths = [HERE/n for n in ('backend_entry.py','build.py','package_gui.py','platform-tools.json','platform-tools-macos.json','GUIDE.md','ota-tool.py','stage-ota.py','build-ota.py','ota-requirements.txt')]
    paths += list((HERE/'backend').glob('*.py'))
    paths += [HERE.parent/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py']
    paths += [p for p in (HERE/'ui/src').rglob('*') if p.is_file()]
    paths += [HERE/'ui'/n for n in ('package.json','package-lock.json','vite.config.ts','tailwind.config.js','postcss.config.js','tsconfig.json','index.html','diff.html','ota.html','rust-toolchain.toml')]
    paths += [HERE/'ui/src-tauri'/n for n in ('Cargo.toml','Cargo.lock','build.rs','tauri.conf.json','src/main.rs','capabilities/default.json','icons/icon.ico','icons/icon.icns','icons/icon.png')]
    # Native Windows and macOS checkouts must identify the same source revision.
    text = {'.py','.ts','.tsx','.json','.toml','.rs','.js','.html','.md','.css','.lock','.txt'}
    return {os.path.relpath(p,HERE).replace('\\','/'): hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            if p.suffix in text else digest(p) for p in sorted(paths)}


def validate_artifact(artifact, platform, source):
    artifact = Path(artifact).resolve()
    metadata = artifact/'build.json'
    if not metadata.is_file():
        raise ValueError('Portable GUI build missing: '+str(metadata))
    build = json.loads(metadata.read_text(encoding='utf-8'))
    if build.get('protocol') != 1 or build.get('platform') != platform:
        raise ValueError('Portable GUI platform/protocol mismatch: expected '+platform)
    if build.get('source') != source:
        raise ValueError('Portable GUI build is stale: '+platform+'; rebuild installer/build.py')
    files = build.get('files', {})
    required = {'OpenWrt-Installer.exe','tools/adb.exe','tools/AdbWinApi.dll',
                'tools/AdbWinUsbApi.dll','tools/backend/openwrt-backend.exe'} if platform == 'windows-x64' else {
                'OpenWrt-Installer.app/Contents/MacOS/openwrt-installer',
                'OpenWrt-Installer.app/Contents/Info.plist','OpenWrt-Installer.app/Contents/Resources/icon.icns',
                'tools/macos/adb','tools/macos/backend/openwrt-backend'}
    if not required.issubset(files):
        raise ValueError('Portable GUI build is incomplete: '+', '.join(sorted(required-set(files))))
    for name, wanted in files.items():
        allowed = (name == 'OpenWrt-Installer.exe' or name.startswith('tools/') and not name.startswith('tools/macos/')) if platform == 'windows-x64' else (
            name.startswith('OpenWrt-Installer.app/') or name.startswith('tools/macos/'))
        path = (artifact/name).resolve()
        if (not allowed or '\\' in name or not path.is_relative_to(artifact) or not path.is_file()
                or digest(path) != wanted):
            raise ValueError('Portable build checksum/path mismatch: '+name)
    actual = {p.relative_to(artifact).as_posix() for p in artifact.rglob('*') if p.is_file() and p != metadata}
    if actual != set(files):
        raise ValueError('Portable build contains unrecorded files: '+platform)
    return metadata


def replace_tree(source, target, package):
    target = Path(target)
    if not target.resolve().is_relative_to(Path(package).resolve()):
        raise ValueError('Unsafe portable runtime destination: '+str(target))
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def attach(package, artifact=None, required=True):
    package = Path(package)
    artifact = Path(artifact or os.environ.get('OPENWRT_GUI_ARTIFACT', HERE/'artifacts/windows'))
    metadata = artifact/'build.json'
    if not metadata.is_file():
        if required:
            raise ValueError('Portable GUI build missing; run python installer/build.py or set OPENWRT_GUI_ARTIFACT')
        return False
    source = sources()
    validate_artifact(artifact, 'windows-x64', source)
    mac = os.environ.get('OPENWRT_GUI_MACOS_ARTIFACT')
    default_mac = HERE/'artifacts/macos-arm64'
    if not mac and (default_mac/'build.json').is_file():
        mac = default_mac
    if not mac:
        raise ValueError('Apple Silicon GUI build missing; build/download installer/artifacts/macos-arm64 or set OPENWRT_GUI_MACOS_ARTIFACT. Release packages require both Windows and macOS installers.')
    mac = Path(mac)
    # Validate both platforms before changing the destination package.
    mac_metadata = validate_artifact(mac, 'macos-arm64', source) if mac else None
    shutil.copy2(artifact/'OpenWrt-Installer.exe', package/'OpenWrt-Installer.exe')
    (package/'tools').mkdir(exist_ok=True)
    for path in (artifact/'tools').iterdir():
        if path.is_dir():
            replace_tree(path, package/'tools'/path.name, package)
        else:
            shutil.copy2(path, package/'tools'/path.name)
    if mac:
        # PyInstaller framework links are flattened for Windows ZIP extractors.
        replace_tree(mac/'OpenWrt-Installer.app', package/'OpenWrt-Installer.app', package)
        replace_tree(mac/'tools/macos', package/'tools/macos', package)
    docs = package/'docs'
    docs.mkdir(exist_ok=True)
    shutil.copy2(HERE/'GUIDE.md', docs/'GUI-GUIDE.md')
    shutil.copy2(HERE/'PROTOCOL.md', docs/'INSTALLER-PROTOCOL.md')
    shutil.copy2(HERE/'README.md', docs/'GUI-BUILD.md')
    shutil.copy2(HERE/'VERIFICATION.md', docs/'GUI-VERIFICATION.md')
    if (HERE/'screenshots').is_dir():
        shutil.copytree(HERE/'screenshots', docs/'screenshots', dirs_exist_ok=True)
    (package/'START-HERE.md').write_text((HERE/'GUIDE.md').read_text(encoding='utf-8'),encoding='utf-8',newline='\n')
    shutil.copy2(metadata, docs/'GUI-BUILD.json')
    if mac_metadata:
        shutil.copy2(mac_metadata, docs/'GUI-BUILD-macos.json')
    return True


def size_report(package, archive=None):
    package = Path(package)
    gui = package/'OpenWrt-Installer.exe'
    windows = gui.stat().st_size if gui.is_file() else 0
    macos = sum(p.stat().st_size for p in (package/'OpenWrt-Installer.app').rglob('*') if p.is_file())
    tools = sum(p.stat().st_size for name in ('tools','scripts','desktop') for p in (package/name).rglob('*') if p.is_file())
    payload_roots = [package/'payload'] if (package/'payload').is_dir() else [package/'files']
    payload = sum(p.stat().st_size for root in payload_roots for p in root.rglob('*') if p.is_file())
    return {'gui_bytes':windows+macos, 'gui_windows_bytes':windows, 'gui_macos_bytes':macos, 'backend_and_tools_bytes':tools,
            'firmware_payload_bytes':payload, 'archive_bytes':Path(archive).stat().st_size if archive else None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--artifact', type=Path)
    parser.add_argument('--archive', type=Path)
    a = parser.parse_args()
    attach(a.package,a.artifact)
    print(json.dumps(size_report(a.package,a.archive),indent=2))
