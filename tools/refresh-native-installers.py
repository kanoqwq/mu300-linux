"""Refresh native installer/Diff Studio files without touching firmware payloads."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


def digest(value):
    if isinstance(value,bytes):
        return hashlib.sha256(value).hexdigest()
    h=hashlib.sha256()
    with Path(value).open('rb') as stream:
        for block in iter(lambda:stream.read(1<<20),b''):h.update(block)
    return h.hexdigest()


def write(destination,value):
    if isinstance(value,bytes):destination.write(value)
    else:
        with Path(value).open('rb') as stream:shutil.copyfileobj(stream,destination,1<<20)


def native_name(name):
    path=PurePosixPath(name)
    return (not path.is_absolute() and '..' not in path.parts and '\\' not in name and
        (name in ('OpenWrt-Installer.exe','OpenWrt-Diff-Studio.exe','OpenWrt-Diff-Studio.py') or
         name.startswith(('tools/','backend/','OpenWrt-Installer.app/','OpenWrt-Diff-Studio.app/'))))


def replacements(original,studio):
    studio=Path(studio).resolve()
    documents=('docs/GUI-BUILD.json','docs/GUI-BUILD-macos.json','diff-tool.json')
    previous=set();current=set();source=None
    for name in documents:
        old=json.loads(original.read(name));new=json.loads((studio/name).read_text())
        if new.get('protocol')!=1 or not isinstance(new.get('files'),dict):
            raise ValueError('Invalid native installer inventory: '+name)
        if source is not None and new.get('source')!=source:
            raise ValueError('Native installer and Diff Studio sources disagree')
        source=new['source']
        previous.update(old['files']);current.update(new['files'])
    if not all(native_name(name) for name in previous|current):
        raise ValueError('Native inventory contains a non-installer path')
    required={'OpenWrt-Installer.exe','OpenWrt-Diff-Studio.exe',
        'tools/backend/openwrt-backend.exe','tools/macos/backend/openwrt-backend',
        'OpenWrt-Installer.app/Contents/MacOS/openwrt-installer',
        'OpenWrt-Diff-Studio.app/Contents/MacOS/openwrt-installer'}
    if not required.issubset(current):
        raise ValueError('Native installer refresh requires both platforms and Diff Studio')
    docs={'START-HERE.md','docs/GUI-GUIDE.md','docs/INSTALLER-PROTOCOL.md',
        'docs/GUI-BUILD.md','docs/GUI-VERIFICATION.md','docs/DIFF-STUDIO.md',*documents}
    patches={}
    for name in sorted(current|docs):
        path=(studio/name).resolve()
        if not path.is_relative_to(studio) or not path.is_file():
            raise ValueError('Native refresh input is missing or outside staging: '+name)
        patches[name]=path
    return patches,previous-current


def mode(name):
    return 0o100755 if (name.startswith(('OpenWrt-Installer.app/Contents/MacOS/',
        'OpenWrt-Diff-Studio.app/Contents/MacOS/')) or
        name in ('tools/macos/adb','tools/macos/backend/openwrt-backend')) else 0o100644
