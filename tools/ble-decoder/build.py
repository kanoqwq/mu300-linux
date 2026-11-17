#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reproducible static ARM64 decoder helper and source receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

SOURCE=Path(__file__).resolve().parent
def fingerprint():
    h=hashlib.sha256()
    for p in sorted(SOURCE.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':
            h.update(p.relative_to(SOURCE).as_posix().encode()+b'\0'+p.read_bytes()+b'\0')
    return h.hexdigest()

def check(folder):
    folder=Path(folder);record=json.loads((folder/'BUILD.json').read_text())
    binary=folder/'unisoc-ble-decode'
    if record.get('source_fingerprint')!=fingerprint() or hashlib.sha256(binary.read_bytes()).hexdigest()!=record.get('sha256'):
        raise ValueError('Bluetooth decoder input is stale; rebuild with tools/ble-decoder/build.py')
    raw=binary.read_bytes()
    if raw[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(raw[18:20],'little')!=183:
        raise ValueError('Bluetooth decoder must be a 64-bit little-endian ARM64 ELF')
    if hashlib.sha256((folder/'SOURCE.tar.gz').read_bytes()).hexdigest()!=record.get('source_archive_sha256'):
        raise ValueError('Bluetooth decoder corresponding source archive is missing or changed')
    return record

def build(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    try:
        check(folder);print('Bluetooth decoder: verified source receipt');return
    except (OSError,ValueError):pass
    compiler=shutil.which('aarch64-linux-gnu-g++')
    if not compiler:raise ValueError('Install the ARM64 C++ cross compiler (aarch64-linux-gnu-g++)')
    output=folder/'unisoc-ble-decode';temporary=folder/'unisoc-ble-decode.new'
    vendor=SOURCE/'vendor/theengs/src'
    subprocess.run([compiler,'-std=c++11','-Os','-static','-fno-rtti','-fno-exceptions',
        '-ffunction-sections','-fdata-sections','-Wl,--gc-sections',
        '-I'+str(vendor),'-I'+str(vendor/'arduino_json/src'),str(SOURCE/'main.cpp'),str(vendor/'decoder.cpp'),
        '-o',str(temporary)],check=True)
    subprocess.run(['aarch64-linux-gnu-strip',str(temporary)],check=True)
    temporary.replace(output)
    with tarfile.open(folder/'SOURCE.tar.gz','w:gz') as archive:
        for p in sorted(SOURCE.rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':
                info=archive.gettarinfo(str(p),arcname='bluetooth-decoder/'+p.relative_to(SOURCE).as_posix())
                info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
                with p.open('rb') as stream:archive.addfile(info,stream)
    (folder/'BUILD.json').write_text(json.dumps({'format':1,'source_fingerprint':fingerprint(),
        'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
        'source_archive_sha256':hashlib.sha256((folder/'SOURCE.tar.gz').read_bytes()).hexdigest(),
        'upstream':'Theengs Decoder 2.5.0'},indent=2)+'\n')
    check(folder);print('Bluetooth decoder built and verified:',output)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();check(args.output) if args.check else build(args.output)
