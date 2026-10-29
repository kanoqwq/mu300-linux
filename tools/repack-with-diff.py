#!/usr/bin/env python3
"""Stream a verified MU300 ZIP, adding Diff Studio without extracting rootfs."""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import zipfile

TOP=Path(__file__).resolve().parents[1]


def repack(source,target,studio,revision,repair_installer=False,refresh_native=False):
    if target.exists():raise ValueError('Target release already exists')
    tool=json.loads((studio/'diff-tool.json').read_text())
    patches={}
    with zipfile.ZipFile(source) as old:
        native=runpy.run_path(str(TOP/'tools/refresh-native-installers.py'))
        removed=set()
        if refresh_native:
            patches,removed=native['replacements'](old,studio)
        manifest=json.loads(old.read('desktop/manifest.json'))
        checks={**manifest['magisk'],**manifest['desktop']}
        for name,wanted in tool['files'].items():
            if refresh_native:continue
            if name in checks:
                if checks[name]!=wanted:raise ValueError('Existing native runtime differs: '+name)
            else:patches[name]=(studio/name).read_bytes()
        for name in ('diff-tool.json','docs/DIFF-STUDIO.md'):patches[name]=(studio/name).read_bytes()
        if not refresh_native:patches['START-HERE.md']=old.read('START-HERE.md')+(
            '\n## Diff Studio / 差异更新工具\n\n'
            'Windows: **OpenWrt-Diff-Studio.exe**. Apple Silicon: **OpenWrt-Diff-Studio.app**.\n\n'
            '完整解压后打开；与安装器共用 tools 目录，请勿单独移动程序。'
            '使用说明见 [DIFF-STUDIO.md](docs/DIFF-STUDIO.md)。\n').encode()
        if repair_installer:
            # The external Python entry is loaded by both native hosts. This
            # repair changes no firmware or native runtime; do not rebuild them.
            patches['START-HERE.md']=old.read('START-HERE.md')
            entry=(TOP/'desktop/installer.py').read_bytes()
            for name in ('OpenWrt-Installer.py','desktop/installer.py'):
                if name not in manifest['desktop']:
                    raise ValueError('Missing installer entry: '+name)
                patches[name]=entry
            manifest['source']['installer.py']=hashlib.sha256(entry).hexdigest()
        payload={n:checks[n] for n in manifest['magisk_layout'].values()}
        patches['desktop/TOOLS-REVISION.json']=(json.dumps({'format':1,'kind':'native-installer-refresh' if refresh_native else 'installer-entry-repair' if repair_installer else 'tools-only',
            'source_commit':revision['source_commit'],'source_fingerprint':revision['source_fingerprint'],
            'firmware_unchanged':payload,'base_archive':source.name},indent=2)+'\n').encode()
        for name in removed:manifest['desktop'].pop(name,None)
        manifest['desktop'].update({n:native['digest'](v) for n,v in patches.items()})
        patches['desktop/manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
        with zipfile.ZipFile(target,'x') as output:
            for member in old.infolist():
                if member.filename in removed:continue
                with output.open(copy.copy(member),'w',force_zip64=True) as dst:
                    if member.filename in patches:native['write'](dst,patches[member.filename])
                    else:
                        with old.open(member) as src:
                            for block in iter(lambda:src.read(1<<20),b''):dst.write(block)
            for name,data in patches.items():
                if name in old.namelist():continue
                info=zipfile.ZipInfo(name);info.create_system=3
                info.external_attr=native['mode'](name)<<16;info.compress_type=zipfile.ZIP_DEFLATED
                with output.open(info,'w',force_zip64=True) as dst:native['write'](dst,data)
    return payload
