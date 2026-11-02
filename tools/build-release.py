#!/usr/bin/env python3
"""Logged, workspace-locked U30 Pro source builds. Never flash a device.

This port deliberately does not invoke the inherited MU300 image assembler.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import hashlib
import zipfile
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import time

TOP = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def verify_archive(path, require_diff=False, require_android=False):
    with zipfile.ZipFile(path) as archive:
        if require_android:
            runpy.run_path(str(TOP/'installer/package_android.py'))['verify_archive'](archive)
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip():
            raise ValueError('ZIP duplicate entries or CRC failure: ' + str(path))
        manifest = json.loads(archive.read('desktop/manifest.json'))
        shared_root = manifest['magisk_layout']['mu300-openwrt.tar.gz']
        shared_boot = manifest['magisk_layout']['boot-linux.img']
        if (names.count(shared_root) != 1 or names.count(shared_boot) != 1
                or sum(n.endswith('boot-linux.img') for n in names) != 1
                or sum(n.endswith(('openwrt.ext4.gz', 'rootfs.tar.gz', 'mu300-openwrt.tar.gz'))
                       for n in names) != 1):
            raise ValueError('Expected one boot and one system image')
        expected = dict(manifest['magisk'])
        for name, wanted in manifest['desktop'].items():
            if name in expected and expected[name] != wanted:
                raise ValueError('Conflicting checksum entry')
            expected[name] = wanted
        if set(expected) != {n for n in names if not n.endswith('/') and n != 'desktop/manifest.json'}:
            raise ValueError('Incomplete ZIP checksums')
        for name, wanted in expected.items():
            with archive.open(name) as stream:
                h = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1 << 20), b''):
                    h.update(chunk)
            if h.hexdigest() != wanted:
                raise ValueError('ZIP checksum mismatch: ' + name)
        if require_diff:
            tool=json.loads(archive.read('diff-tool.json'))
            required={'OpenWrt-Diff-Studio.exe','OpenWrt-Diff-Studio.py',
                      'OpenWrt-Diff-Studio.app/Contents/MacOS/openwrt-installer',
                      'tools/backend/openwrt-backend.exe','tools/macos/backend/openwrt-backend'}
            if tool.get('protocol')!=1 or not required.issubset(tool.get('files',{})):
                raise ValueError('Diff Studio entries/runtime missing')
            for name,wanted in tool['files'].items():
                if (name not in required and not name.startswith(('backend/','tools/backend/',
                        'tools/macos/backend/','OpenWrt-Diff-Studio.app/'))) or expected.get(name)!=wanted:
                    raise ValueError('Diff Studio scope/checksum mismatch: '+name)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    # Windows readers of a DrvFS status file can briefly deny its replacement.
    # Keep publication atomic; retry sharing/access contention for at most 1 s.
    for attempt in range(21):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 20:
                raise
            time.sleep(0.05)


@contextmanager
def workspace_lock(workspace):
    import fcntl
    directory = workspace / 'locks'
    directory.mkdir(parents=True, exist_ok=True)
    # A sudo packaging run must not make the next user's compiler task require
    # write access to a root-owned lock file. Linux flock works on a read FD.
    descriptor = os.open(directory / 'openwrt-release.lock', os.O_RDONLY | os.O_CREAT, 0o644)
    with os.fdopen(descriptor, 'rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another build/package task owns this workspace') from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Task:
    def __init__(self, workspace, label):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,60}', label):
            raise ValueError('Invalid task label')
        self.workspace = workspace
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        self.id = stamp + '-' + label + '-' + str(os.getpid())
        self.logs = TOP / 'out/build-logs' / self.id
        self.logs.mkdir(parents=True)
        self.data = {'format': 1, 'task': self.id, 'pid': os.getpid(), 'state': 'running',
                     'workspace': str(workspace), 'logs': str(self.logs), 'steps': []}
        self.save()

    def save(self, **fields):
        self.data.update(fields)
        write_json(self.logs / 'status.json', self.data)
        write_json(TOP / 'out/build-state/latest.json', self.data)

    def run(self, label, command, extra_env=None):
        log = self.logs / (label + '.log')
        step = {'stage': label, 'command': list(map(str, command)), 'log': str(log),
                'started': time.time(), 'state': 'running'}
        self.data['steps'].append(step); self.save(stage=label)
        temp_parent = self.workspace / 'tmp'
        temp_parent.mkdir(parents=True, exist_ok=True)
        print(label + ': ' + str(log), flush=True)
        try:
            with tempfile.TemporaryDirectory(prefix='u30pro-release-', dir=temp_parent) as temp:
                env = dict(os.environ, TMPDIR=temp, PYTHONDONTWRITEBYTECODE='1')
                env.update(extra_env or {})
                with log.open('wb') as stream:
                    subprocess.run(list(map(str, command)), cwd=TOP, env=env,
                                   stdout=stream, stderr=subprocess.STDOUT, check=True)
        except BaseException:
            step.update(state='failed', finished=time.time()); self.save(state='failed')
            raise
        step.update(state='complete', finished=time.time()); self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'check', 'run', 'status', 'package'))
    parser.add_argument('--profiles', nargs='+', choices=('all', 'u30pro-6.12'), default=['all'])
    parser.add_argument('--workspace', type=Path, default=Path('/home/kano/openwrt-build'))
    parser.add_argument('--config', type=Path, default=TOP / 'tools/build-layout.json')
    parser.add_argument('--label', default='build')
    args, command = parser.parse_known_args()
    if command and command[0] == '--':
        command = command[1:]
    if command and args.action != 'run':
        parser.error('Unexpected extra arguments')
    if os.name == 'nt':
        parser.error('Run in WSL; see docs/LOCAL-BUILD.zh-CN.md')
    native = runpy.run_path(str(TOP / 'tools/u30pro-build.py'))
    native['dispatch_release'](args, command, args.workspace.expanduser().resolve(), ['u30pro-6.12'],
                               workspace_lock=workspace_lock, task_type=Task)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        sys.exit('Build stopped: ' + str(error))
