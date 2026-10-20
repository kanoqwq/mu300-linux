"""One payload for web import plus the existing native desktop runtime."""
import os
from pathlib import Path
import json
import hashlib
import zipfile


def export_bundle(patch, output, runtime):
    """Use the generator's own fingerprinted runtime, including offline exports."""
    runtime = Path(runtime)
    build = json.loads((runtime / 'diff-tool.json').read_text())
    if build.get('protocol') != 1 or not build.get('files'):
        raise ValueError('Missing Diff Studio runtime manifest; run installer/build-ota.py')
    for name, expected in build['files'].items():
        source = (runtime / name).resolve()
        if not source.is_relative_to(runtime.resolve()) or not source.is_file():
            raise ValueError('Missing or unsafe native runtime file')
        with source.open('rb') as stream:
            from .ota_engine import digest
            if digest(stream) != expected:
                raise ValueError('Native runtime checksum mismatch: ' + name)
    output = Path(output)
    if output.exists():
        raise ValueError('Output already exists')
    tmp = output.with_suffix(output.suffix + '.building')
    if tmp.exists():
        raise ValueError('Output staging exists')
    try:
        with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(patch, 'update.ota')
            for name in build['files']:
                if name == 'OpenWrt-Diff-Studio.exe':
                    dest = 'OpenWrt-Updater.exe'
                elif name.startswith('OpenWrt-Diff-Studio.app/'):
                    dest = name.replace('OpenWrt-Diff-Studio.app/', 'OpenWrt-Updater.app/', 1)
                elif name == 'OpenWrt-Diff-Studio.py':
                    dest = 'OpenWrt-Updater.py'
                elif name.startswith(('tools/', 'backend/')):
                    dest = name
                else:
                    continue
                z.write(runtime / name, dest)
            z.writestr('README.md', '# OpenWrt differential OTA\n\n'
                        'Web: import update.ota in System > System patches.\n'
                        'Desktop: extract the complete folder and open OpenWrt-Updater.exe / .app.\n'
                        'Python: python OpenWrt-Updater.py update update.ota --host DEVICE_IP '
                        '(requires paramiko).\n\n'
                        '网页：系统 → 系统补丁，导入 update.ota。\n'
                        '电脑：完整解压后打开 OpenWrt-Updater.exe。\n'
                        '仅使用可信补丁；先核对目标版本、机型和冲突检查。\n'
                        '更新后请重启；网页支持撤销上次补丁。\n'
                        '旧系统没有 OTA 插件／Python：使用电脑更新程序首次更新，完成后即可在网页导入后续补丁。\n'
                        'Old system without OTA/Python: use the desktop updater once; subsequent patches can be imported in LuCI.\n')
        os.rename(tmp, output)
    finally:
        if tmp.exists():
            tmp.unlink()
