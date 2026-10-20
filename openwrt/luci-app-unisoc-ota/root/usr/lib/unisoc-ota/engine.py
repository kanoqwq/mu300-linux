#!/usr/bin/env python3
"""Unisoc file hotfix protocol 1. Standard-library-only device/desktop verifier.

No package supplied programs, no root extraction, no configuration replacement.
The transaction journal is durable before its first mutation. Recovery is also
run before network/modem startup by the packaged init service.
"""
import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time
import zipfile

FORMAT = 'unisoc-file-ota-1'
MAX_MANIFEST = 16 << 20
MAX_BYTES = 2 << 30
MAX_FILES = 50000
STATE = 'etc/unisoc-ota'
HEX = re.compile(r'[0-9a-f]{64}')
# Persistent state, board firmware/NV and package-manager ownership are never
# overwritten by an arbitrary file update. New packages require a full installer.
PROTECTED = ('lib/firmware', 'lib/apk', 'usr/lib/opkg', 'usr/lib/apk',
             'lib/modules', 'bin/busybox', 'sbin/init', 'sbin/procd',
             'lib/ld-musl-aarch64.so.1', 'lib/libc.so',
             'usr/lib/unisoc-ota', 'usr/libexec/unisoc-ota',
             'usr/share/unisoc-ota', 'etc/init.d/unisoc-ota',
             'opt/e5/vendor', 'opt/e5/firmware', 'opt/e5/nv', 'opt/e5/android',
             'opt/mu300/vendor', 'opt/mu300/firmware', 'opt/mu300/nv', 'etc/uci-defaults')
OFFLINE_ONLY = ('lib/modules', 'bin/busybox', 'sbin/init', 'sbin/procd',
                'lib/ld-musl-aarch64.so.1', 'lib/libc.so', 'usr/lib/unisoc-ota',
                'usr/libexec/unisoc-ota', 'usr/libexec/rpcd/unisoc-ota', 'etc/init.d/unisoc-ota')


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(stream):
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(1 << 20), b''):
        h.update(block)
    return h.hexdigest()


def safe_name(name):
    if (not isinstance(name, str) or not name or len(name) > 2048 or
            '\\' in name or '\0' in name or any(ord(c) < 32 for c in name) or
            name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('Unsafe path: ' + repr(name))
    return name


def protected(name):
    safe_name(name)
    if name.split('/')[0] not in ('bin', 'sbin', 'lib', 'lib64', 'usr', 'opt', 'etc', 'www'):
        return True
    if name.startswith('etc/') and not name.startswith(('etc/init.d/', 'etc/hotplug.d/', 'etc/uci-defaults/')):
        return True
    return any(name == p or name.startswith(p + '/') for p in PROTECTED)


def package_versions(raw):
    """Only installed package identity/dependencies; never rewrite APK metadata."""
    result = {}
    for paragraph in raw.decode('utf-8').split('\n\n'):
        fields = {}
        for line in paragraph.splitlines():
            if len(line) > 2 and line[1] == ':':
                fields[line[0]] = line[2:]
        if 'P' in fields:
            result[fields['P']] = {key:fields.get(key, '') for key in ('V', 'A', 'D')}
    return result


def descriptor(path):
    try:
        s = path.lstat()
    except FileNotFoundError:
        return None
    result = {'mode': stat.S_IMODE(s.st_mode), 'uid': s.st_uid, 'gid': s.st_gid}
    if stat.S_ISLNK(s.st_mode):
        result.update(kind='symlink', target=os.readlink(path))
    elif stat.S_ISREG(s.st_mode):
        with path.open('rb') as stream:
            result.update(kind='file', sha256=digest(stream), size=s.st_size)
    elif stat.S_ISDIR(s.st_mode):
        result.update(kind='dir')
    else:
        raise ValueError('Unsupported special file: ' + str(path))
    result['xattrs'] = {k: base64.b64encode(os.getxattr(path, k, follow_symlinks=False)).decode()
                        for k in os.listxattr(path, follow_symlinks=False)}
    return result


def validate_descriptor(value):
    if value is None:
        return
    if not isinstance(value, dict) or value.get('kind') not in ('file', 'symlink', 'dir'):
        raise ValueError('Invalid file descriptor')
    if any(type(value.get(k)) is not int or not 0 <= value[k] <= limit
           for k, limit in (('mode', 0o7777), ('uid', 65535), ('gid', 65535))):
        raise ValueError('Invalid file permissions/ownership')
    if value['kind'] == 'file' and (not HEX.fullmatch(value.get('sha256', '')) or
            type(value.get('size')) is not int or not 0 <= value['size'] <= MAX_BYTES):
        raise ValueError('Invalid file hash/size')
    if value['kind'] == 'symlink' and (not isinstance(value.get('target'), str) or
            not value['target'] or len(value['target']) > 4096 or '\0' in value['target']):
        raise ValueError('Invalid symbolic link')
    for name, encoded in value.get('xattrs', {}).items():
        if not isinstance(name, str) or not name or '\0' in name or len(base64.b64decode(encoded, validate=True)) > 65536:
            raise ValueError('Invalid extended attribute')


def validate_manifest(m):
    if not isinstance(m, dict) or m.get('format') != FORMAT:
        raise ValueError('Unsupported OTA format')
    if m.get('family') not in ('e5', 'mu300') or not isinstance(m.get('profile'), str):
        raise ValueError('Invalid OTA family/profile')
    for k in ('base_version', 'target_version', 'kernel_release'):
        if not isinstance(m.get(k), str) or not re.fullmatch(r'[A-Za-z0-9._+\-]{1,180}', m[k]):
            raise ValueError('Invalid ' + k)
    if m['base_version'] == m['target_version']:
        raise ValueError('Release versions are identical')
    ops = m.get('operations')
    if not isinstance(ops, list) or not 0 < len(ops) <= MAX_FILES:
        raise ValueError('Empty or excessive OTA operations')
    seen = set()
    for op in ops:
        name = safe_name(op['path'])
        if name in seen or protected(name):
            raise ValueError('Duplicate/protected OTA path: ' + name)
        seen.add(name)
        before, after = op.get('before'), op.get('after')
        validate_descriptor(before)
        validate_descriptor(after)
        if before == after or before is None and after is None:
            raise ValueError('Redundant operation')
        # Directory removal or type changes could remove user-owned descendants.
        if before and before['kind'] == 'dir' and (not after or after['kind'] != 'dir'):
            raise ValueError('Directory removal/type change is not supported: ' + name)
        if before and after and before['kind'] != after['kind']:
            raise ValueError('File type changes require full installation: ' + name)
    for name in seen:
        for parent in PurePosixPath(name).parents:
            p = str(parent)
            if p in seen:
                op = next(o for o in ops if o['path'] == p)
                if not op['after'] or op['after']['kind'] != 'dir':
                    raise ValueError('Operation below a non-directory: ' + name)
    if m.get('boot') is not None:
        raise ValueError('Kernel/boot updates require the full installer in protocol 1')
    return m


class Patch:
    def __init__(self, path):
        self.archive = zipfile.ZipFile(path)
        try:
            names = self.archive.namelist()
            if len(names) != len(set(names)) or len(names) > MAX_FILES + 1:
                raise ValueError('Duplicate/excessive ZIP entries')
            info = self.archive.getinfo('manifest.json')
            if info.file_size > MAX_MANIFEST:
                raise ValueError('OTA manifest too large')
            self.manifest = validate_manifest(json.loads(self.archive.read(info)))
            self.id = hashlib.sha256(canonical(self.manifest)).hexdigest()
            blobs = {o['after']['sha256']: o['after']['size'] for o in self.manifest['operations']
                     if o['after'] and o['after']['kind'] == 'file'}
            if set(names) != {'manifest.json', *('blobs/' + h for h in blobs)}:
                raise ValueError('Unexpected or missing archive entries')
            if sum(blobs.values()) > MAX_BYTES:
                raise ValueError('OTA exceeds expanded size limit')
            for h, size in blobs.items():
                info = self.archive.getinfo('blobs/' + h)
                if info.file_size != size or info.flag_bits & 1:
                    raise ValueError('Invalid encrypted blob or length')
                with self.archive.open(info) as stream:
                    if digest(stream) != h:
                        raise ValueError('OTA content checksum mismatch: ' + h)
        except BaseException:
            self.archive.close()
            raise

    def close(self):
        self.archive.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    tmp = path.with_suffix('.new')
    with tmp.open('wb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    sync_dir(path.parent)


class Engine:
    def __init__(self, root='/'):
        self.root = Path(root).resolve()
        self.state = self.path(STATE)

    def path(self, name):
        safe_name(name)
        path = self.root / name
        # Check parents, not resolve(): /bin's legitimate links are also rejected
        # rather than following an unreviewed alias into a different file tree.
        for parent in path.parents:
            if parent == self.root:
                break
            if parent.is_symlink():
                raise ValueError('Symbolic link parent: ' + str(parent))
        return path

    @contextlib.contextmanager
    def lock(self):
        import fcntl
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.state.is_symlink():
            raise ValueError('Unsafe OTA state directory')
        with contextlib.ExitStack() as stack:
            stream = stack.enter_context((self.state / 'lock').open('a'))
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.root == Path('/'):
                if (self.root / '.e5-update/request').exists() or (self.root / '.e5-update/request.ready').exists():
                    raise ValueError('A full system update is pending')
                paths = ['run/e5-part-update.lock', 'run/e5-upgrade/lock'] if (self.root / 'etc/e5/release.json').exists() else ['run/mu300-update/lock']
                for name in paths:
                    target = self.path(name)
                    target.parent.mkdir(mode=0o700, exist_ok=True)
                    if target.is_symlink() or target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
                        raise ValueError('Unsafe shared upgrade lock')
                    held = stack.enter_context(target.open('a'))
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def identity(self):
        current = self.state / 'current.json'
        if current.exists():
            return json.loads(current.read_text())
        for family in ('e5', 'mu300'):
            path = self.root / ('etc/' + family + '/release.json')
            if path.is_file():
                m = json.loads(path.read_text())
                return dict(family=family, profile=m['profile'], version=m['image_version'],
                            kernel_release=m['kernel_release'])
        raise ValueError('Missing release identity; use full installer once')

    def status(self):
        journal = self.state / 'journal.json'
        return dict(identity=self.identity(), transaction=json.loads(journal.read_text()) if journal.exists() else None)

    def inspect(self, patch):
        m = patch.manifest
        identity = self.identity()
        for key, expected in (('family', m['family']), ('profile', m['profile']),
                              ('version', m['base_version']), ('kernel_release', m['kernel_release'])):
            if identity.get(key) != expected:
                raise ValueError('Device/base mismatch: ' + key)
        if self.root == Path('/') and os.uname().release != m['kernel_release']:
            raise ValueError('Running kernel differs from release identity')
        conflicts, backup, incoming = [], 0, 0
        package_conflicts = []
        db = self.root / 'lib/apk/db/installed'
        installed = package_versions(db.read_bytes()) if db.is_file() else {}
        owned = {'luci-app-t551m', 'luci-app-mu300'}
        for name, expected in m.get('base_packages', {}).items():
            actual = installed.get(name)
            if name in owned and actual:
                actual = dict(actual, V=expected['V'])
            if actual != expected:
                package_conflicts.append(name)
        for op in m['operations']:
            actual = descriptor(self.path(op['path']))
            if actual != op['before']:
                conflicts.append(op['path'])
            if actual and actual['kind'] == 'file':
                backup += actual['size']
            if op['after'] and op['after']['kind'] == 'file':
                incoming += op['after']['size']
        required = backup + incoming + (16 << 20)
        free = shutil.disk_usage(self.root).free
        return dict(id=patch.id, manifest=m, conflicts=conflicts, package_conflicts=package_conflicts,
                    required_bytes=required, free_bytes=free,
                    applicable=not conflicts and not package_conflicts and free >= required)

    def install_descriptor(self, name, value, source=None):
        path = self.path(name)
        if value is None:
            if path.exists() or path.is_symlink():
                path.unlink()
                sync_dir(path.parent)
            return
        # All new parents have explicit directory operations; never invent root
        # ownership/mode for an existing user tree.
        if not path.parent.is_dir():
            raise ValueError('Missing parent directory: ' + name)
        if value['kind'] == 'dir':
            path.mkdir(exist_ok=True)
        else:
            tmp = path.with_name('.' + path.name + '.unisoc-ota-new')
            if tmp.exists() or tmp.is_symlink():
                raise ValueError('Temporary path already exists: ' + str(tmp))
            try:
                if value['kind'] == 'symlink':
                    os.symlink(value['target'], tmp)
                else:
                    with tmp.open('xb') as stream:
                        shutil.copyfileobj(source, stream, 1 << 20)
                        stream.flush()
                        os.fsync(stream.fileno())
                if value['kind'] != 'symlink':
                    os.chmod(tmp, value['mode'])
                os.chown(tmp, value['uid'], value['gid'], follow_symlinks=False)
                for key, encoded in value.get('xattrs', {}).items():
                    os.setxattr(tmp, key, base64.b64decode(encoded), follow_symlinks=False)
                if descriptor(tmp) != value:
                    raise ValueError('Temporary file verification failed: ' + name)
                os.replace(tmp, path)
            finally:
                if tmp.exists() or tmp.is_symlink():
                    tmp.unlink()
        if value['kind'] == 'dir':
            os.chmod(path, value['mode'])
            os.chown(path, value['uid'], value['gid'])
            wanted = value.get('xattrs', {})
            for key in os.listxattr(path, follow_symlinks=False):
                if key not in wanted:
                    os.removexattr(path, key, follow_symlinks=False)
            for key, encoded in wanted.items():
                os.setxattr(path, key, base64.b64decode(encoded), follow_symlinks=False)
        sync_dir(path.parent)

    def recover(self):
        journal = self.state / 'journal.json'
        if not journal.exists():
            return {'status': 'idle'}
        data = json.loads(journal.read_text())
        if data['status'] not in ('applying', 'rolling_back'):
            return data
        data['status'] = 'rolling_back'
        atomic_json(journal, data)
        operations = json.loads((self.state / data['backup'] / 'plan.json').read_text())
        for i in reversed(range(data['started'])):
            op = operations[i]
            old = op['before']
            path = self.path(op['path'])
            tmp = path.with_name('.' + path.name + '.unisoc-ota-new')
            if tmp.exists() or tmp.is_symlink():
                tmp.unlink()
                sync_dir(tmp.parent)
            if old is None and op['after']['kind'] == 'dir':
                # Only our empty new directory may disappear; keep user files.
                if path.exists():
                    path.rmdir()
                    sync_dir(path.parent)
            elif old and old['kind'] == 'file':
                backup = self.state / data['backup'] / str(i)
                with backup.open('rb') as stream:
                    if digest(stream) != old['sha256']:
                        raise ValueError('Rollback backup corrupt; do not reboot')
                    stream.seek(0)
                    self.install_descriptor(op['path'], old, stream)
            else:
                self.install_descriptor(op['path'], old)
        previous = data.get('previous_identity')
        if previous:
            atomic_json(self.state / 'current.json', previous)
        elif (self.state / 'current.json').exists():
            (self.state / 'current.json').unlink()
            sync_dir(self.state)
        data['status'] = 'rolled_back'
        atomic_json(journal, data)
        return data

    def apply(self, patch, progress=lambda *_: None):
        with self.lock():
            self.recover()
            check = self.inspect(patch)
            if not check['applicable']:
                raise ValueError('Conflicting files/packages or insufficient space: ' + ', '.join((check['conflicts'] + check['package_conflicts'])[:20]))
            journal = self.state / 'journal.json'
            # Preserve the preceding transaction's recovery data until the new
            # journal is durable. A crash during backup must not invalidate it.
            backup = self.state / ('backup-' + str(time.time_ns()))
            backup.mkdir(mode=0o700)
            ops = sorted(patch.manifest['operations'], key=lambda o: (o['path'].count('/'), o['path']))
            data = dict(id=patch.id, status='preparing', started=0, total=len(ops), backup=backup.name,
                        previous_identity=json.loads((self.state / 'current.json').read_text())
                        if (self.state / 'current.json').exists() else None,
                        target_version=patch.manifest['target_version'])
            # The large immutable plan is written once, not rewritten for every
            # file. A compact count journal keeps update writes O(n), not O(n²).
            atomic_json(backup / 'plan.json', ops)
            for i, op in enumerate(ops):
                if op['before'] and op['before']['kind'] == 'file':
                    with self.path(op['path']).open('rb') as src, (backup / str(i)).open('xb') as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                        dst.flush()
                        os.fsync(dst.fileno())
                    with (backup / str(i)).open('rb') as verified:
                        if digest(verified) != op['before']['sha256']:
                            raise ValueError('Backup verification failed: ' + op['path'])
            sync_dir(backup)
            data['status'] = 'applying'
            atomic_json(journal, data)
            try:
                for i, op in enumerate(ops):
                    # Recheck at commit time; a concurrent user edit is a conflict.
                    if descriptor(self.path(op['path'])) != op['before']:
                        raise ValueError('File changed during update: ' + op['path'])
                    data['started'] = i + 1
                    atomic_json(journal, data)
                    value = op['after']
                    with contextlib.ExitStack() as stack:
                        source = stack.enter_context(patch.archive.open('blobs/' + value['sha256'])) if value and value['kind'] == 'file' else None
                        self.install_descriptor(op['path'], value, source)
                    if descriptor(self.path(op['path'])) != value:
                        raise ValueError('Written file verification failed: ' + op['path'])
                    progress(i + 1, len(ops), op['path'])
                identity = dict(family=patch.manifest['family'], profile=patch.manifest['profile'],
                                version=patch.manifest['target_version'], kernel_release=patch.manifest['kernel_release'])
                atomic_json(self.state / 'current.json', identity)
                data['status'] = 'complete'
                atomic_json(journal, data)
            except BaseException:
                self.recover()
                raise
            # Only the latest transaction is exposed for manual rollback.
            # Remove our older numbered recovery directories after the new
            # committed journal is durable, never during backup preparation.
            for previous in self.state.glob('backup-*'):
                if previous != backup and re.fullmatch(r'backup-[0-9]+', previous.name) and previous.is_dir() and not previous.is_symlink():
                    with contextlib.suppress(OSError):
                        shutil.rmtree(previous)
            return dict(status='complete', version=identity['version'], reboot_required=True, id=patch.id)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('inspect', 'apply', 'recover', 'status', 'rollback'))
    p.add_argument('package', nargs='?')
    p.add_argument('--root', default='/')
    args = p.parse_args()
    engine = Engine(args.root)
    if args.command in ('recover', 'rollback'):
        with engine.lock():
            if args.command == 'rollback':
                journal = engine.state / 'journal.json'
                data = json.loads(journal.read_text())
                if data['status'] != 'complete':
                    raise ValueError('No completed update to roll back')
                # Never clobber changes made since the hotfix was applied.
                for op in json.loads((engine.state / data['backup'] / 'plan.json').read_text()):
                    if descriptor(engine.path(op['path'])) != op['after']:
                        raise ValueError('Rollback conflict: ' + op['path'])
                data['status'] = 'rolling_back'
                atomic_json(journal, data)
            result = engine.recover()
    elif args.command == 'status':
        result = engine.status()
    else:
        with Patch(args.package) as patch:
            result = engine.apply(patch) if args.command == 'apply' else engine.inspect(patch)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': str(error)}))
        sys.exit(1)
