"""Create a new GUI distribution from an existing validated firmware package."""
import argparse
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import zipfile

from backend.package import inspect_package
from package_gui import attach, digest, size_report
from archive_tools import write_member

TOP=Path(__file__).resolve().parents[1]


def refresh(package):
    package=Path(package)
    if (package/'desktop/manifest.json').is_file():
        shutil.copy2(TOP/'desktop/installer.py',package/'OpenWrt-Installer.py')
        shutil.copy2(TOP/'desktop/installer.py',package/'desktop/installer.py')
        for name in ('android-write-part.sh','android-write-free.sh'):
            shutil.copy2(TOP/'desktop'/name,package/'desktop'/name)
        shutil.copy2(TOP/'installer/backend/binary_io.py',package/'desktop/binary_io.py')
        shutil.copy2(TOP/'installer/backend/boot_image.py',package/'desktop/boot_control.py')
        shutil.copy2(TOP/'installer/backend/mu300_update.py',package/'desktop/update_control.py')
        shutil.copy2(TOP/'installer/backend/windows_mtp.py',package/'desktop/windows_mtp.py')
        attach(package)
        manifest_path=package/'desktop/manifest.json'
        manifest=json.loads(manifest_path.read_text())
        manifest['source']={name:digest(TOP/'desktop'/name) for name in ('installer.py','boot_image.py')}
        manifest['desktop']={p.relative_to(package).as_posix():digest(p) for p in package.rglob('*')
                             if p.is_file() and p!=manifest_path and p.relative_to(package).as_posix() not in manifest['magisk']}
        manifest_path.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
        # Existing CLI shortcuts must use the bundled CLI interpreter, not the GUI.
        for cmd in (package/'launchers').glob('*.cmd'):
            s=cmd.read_text(encoding='utf-8')
            s=s.replace('if exist "OpenWrt-Installer.exe" (','if exist "tools\\backend\\openwrt-backend.exe" (').replace('  "OpenWrt-Installer.exe" ','  "tools\\backend\\openwrt-backend.exe" --cli --package-root "%cd%" ')
            cmd.write_bytes(s.replace('\r\n','\n').replace('\n','\r\n').encode('utf-8'))
        for launcher in (package/'launchers').glob('*.sh'):
            text = launcher.read_text(encoding='utf-8')
            if 'tools/macos/backend/openwrt-backend' not in text:
                text = re.sub(r'^exec python3 OpenWrt-Installer.py (.*)$',
                    lambda m: 'if [ "$(uname -s)" = Darwin ] && [ -x ./tools/macos/backend/openwrt-backend ]; then\n'
                    '  exec ./tools/macos/backend/openwrt-backend --cli --package-root "$PWD" '+m[1]+'\n'
                    'fi\n'+m[0], text, flags=re.M)
                launcher.write_text(text, encoding='utf-8', newline='\n')
        # Launchers changed after attachment, so include their final bytes.
        manifest['desktop'].update({p.relative_to(package).as_posix():digest(p) for p in (package/'launchers').iterdir() if p.is_file()})
        manifest_path.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
    else:
        runpy.run_path(str(TOP/'openwrt/bundle/install-entrypoints.py'))['assemble'](package)
        shutil.copy2(TOP/'tools/e5-update.py',package/'scripts/tools/e5-update.py')
        sums=package/'files/SHA256SUMS'
        sums.write_text(''.join(digest(p)+'  '+p.relative_to(package).as_posix()+'\n' for p in sorted(package.rglob('*')) if p.is_file() and p!=sums and '__pycache__' not in p.parts),encoding='utf-8',newline='\n')


def archive(package, target):
    if target.exists(): raise ValueError('Archive exists; choose a new release path')
    partial=target.with_suffix('.zip.partial')
    with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(package.rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                write_member(z,p,p.relative_to(package).as_posix())
    with zipfile.ZipFile(partial) as z:
        bad=z.testzip()
        if bad:raise ValueError('ZIP CRC failure: '+bad)
        roots=[n for n in z.namelist() if n.endswith(('openwrt.ext4.gz','mu300-openwrt.tar.gz'))]
        if len(roots)!=1:raise ValueError('Expected exactly one system payload')
    partial.replace(target)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('destination',type=Path)
    p.add_argument('--no-zip',action='store_true');p.add_argument('--zip-only',action='store_true')
    a=p.parse_args()
    if a.zip_only:
        package=a.source.resolve();target=a.destination.resolve();archive(package,target)
    else:
        if a.destination.exists():raise ValueError('Destination exists; never overwrite an existing release')
        before=inspect_package(a.source)
        shutil.copytree(a.source,a.destination,ignore=shutil.ignore_patterns('__pycache__'))
        package=a.destination.resolve();refresh(package)
        after=inspect_package(package)
        if before['payloads']!=after['payloads']:raise ValueError('Device firmware payload changed')
        target=package.parent/(package.name+'.zip')
        if a.no_zip:
            print(package);return
        archive(package,target)
    report=size_report(package,target)
    report['package']=str(package);report['sha256']=digest(target)
    target.with_suffix('.size.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    target.with_suffix('.zip.sha256').write_text(report['sha256']+'  '+target.name+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
