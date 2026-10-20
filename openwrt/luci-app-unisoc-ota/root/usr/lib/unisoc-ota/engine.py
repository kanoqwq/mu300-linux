#!/usr/bin/env python3
"""Unisoc differential OTA protocol 2. Standard-library-only device/desktop verifier.

An isolated RAM interpreter supports core, dependency and updater migration.
The transaction journal is durable before its first mutation. Recovery is also
run before network/modem startup by the packaged init service.
"""
import argparse
import io
import tarfile
import subprocess
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

FORMAT = 'unisoc-diff-ota-2'
MAX_MANIFEST = 16 << 20
MAX_BYTES = 2 << 30
MAX_FILES = 50000
STATE = 'etc/unisoc-ota'
HEX = re.compile(r'[0-9a-f]{64}')
# Persistent state, board firmware/NV and package-manager ownership are never
# overwritten by release diffs. APK metadata uses explicit per-package merging.
PROTECTED = ('lib/firmware', 'lib/apk', 'usr/lib/opkg', 'usr/lib/apk',
             'opt/e5/vendor', 'opt/e5/firmware', 'opt/e5/nv', 'opt/e5/android',
             'opt/mu300/vendor', 'opt/mu300/firmware', 'opt/mu300/nv',
             'etc/unisoc-ota', 'etc/uci-defaults')
# Retained as an empty API alias for older desktop imports, not a scope gate.
OFFLINE_ONLY = ()


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


def package_records(raw):
    result = {}
    for paragraph in raw.decode('utf-8').split('\n\n'):
        name = next((line[2:] for line in paragraph.splitlines() if line.startswith('P:')), None)
        if name: result[name] = paragraph
    return result


def package_paths(record):
    directory = ''; paths = set()
    for line in record.splitlines():
        if line.startswith('F:'): directory = line[2:]
        elif line.startswith('R:'): paths.add(directory+'/'+line[2:] if directory else line[2:])
    return paths


def merge_scripts(current, target):
    members = {}
    for raw in (current, target):
        if not raw: continue
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for node in archive:
                safe_name(node.name)
                if not node.isfile(): raise ValueError('Invalid APK script record')
                members[node.name] = (node, archive.extractfile(node).read())
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as archive:
        for name in sorted(members):
            node, data = members[name]; archive.addfile(node, io.BytesIO(data))
    return out.getvalue()


def prepare_runtime(patch):
    """Prepare the very same isolated updater for browser and desktop entry."""
    if not patch.manifest.get('runtime'):
        raise ValueError('Patch has no portable runtime')
    home = Path('/tmp')/('unisoc-ota-'+patch.id)
    home.mkdir(mode=0o700, exist_ok=True)
    if home.is_symlink() or home.stat().st_uid != 0:
        raise ValueError('Unsafe RAM runtime directory')
    for key, name, mode in (('busybox', 'busybox', 0o700), ('bootstrap', 'bootstrap.sh', 0o600)):
        desc = patch.manifest[key]
        data = patch.archive.read('blobs/'+desc['sha256'])
        path = home/name
        if path.is_symlink(): raise ValueError('Unsafe RAM helper')
        path.write_bytes(data); path.chmod(mode)
    return home


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
    if not isinstance(ops, list) or not 0 <= len(ops) <= MAX_FILES or not ops and not m.get('boot'):
        raise ValueError('Empty or excessive OTA operations')
    seen = set()
    for op in ops:
        name = safe_name(op['path'])
        merge = op.get('merge')
        special = (merge == 'apk-db' and name == 'lib/apk/db/installed' or
                   merge == 'apk-scripts' and name == 'lib/apk/db/scripts.tar' or
                   merge == 'apk-triggers' and name == 'lib/apk/db/triggers' or
                   merge == 'release' and name in ('etc/e5/release.json', 'etc/mu300/release.json') or
                   merge == 'accounts' and name in ('etc/passwd', 'etc/shadow', 'etc/group', 'etc/gshadow') or
                   merge == 'world' and name == 'etc/apk/world' or
                   merge == 'default' and name.startswith('etc/') and not name.startswith(('etc/unisoc-ota/', 'etc/uci-defaults/')))
        if merge is not None and not special:
            raise ValueError('Invalid merge operation')
        if name in seen or protected(name) and not special:
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
        # File/symlink transitions are atomic; nonempty directories remain protected.
    for name in seen:
        for parent in PurePosixPath(name).parents:
            p = str(parent)
            if p in seen:
                op = next(o for o in ops if o['path'] == p)
                if not op['after'] or op['after']['kind'] != 'dir':
                    raise ValueError('Operation below a non-directory: ' + name)
    for key in ('boot', 'busybox', 'bootstrap'):
        if key in m:
            value = m[key]
            if not isinstance(value, dict) or not HEX.fullmatch(value.get('sha256', '')) or type(value.get('size')) is not int or not 0 < value['size'] <= MAX_BYTES:
                raise ValueError('Invalid '+key+' payload')
    for name, value in m.get('runtime', {}).items():
        safe_name(name)
        if name.split('/')[0] not in ('lib', 'lib64', 'usr', 'ota'):
            raise ValueError('Invalid runtime path')
        validate_descriptor(value)
        if value is None or value['kind'] == 'dir': raise ValueError('Invalid runtime entry')
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
            for d in self.manifest.get('runtime', {}).values():
                if d['kind'] == 'file': blobs[d['sha256']] = d['size']
            for key in ('boot', 'busybox', 'bootstrap'):
                if key in self.manifest:
                    d = self.manifest[key]; blobs[d['sha256']] = d['size']
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
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('UNISOC_OTA_ROOT', '/')).resolve()
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
            apk_lock = self.root/'lib/apk/db/lock'
            if apk_lock.parent.is_dir():
                held = stack.enter_context(apk_lock.open('a'))
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.root == Path('/') or os.environ.get('UNISOC_OTA_LIVE') == '1':
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
        stored = json.loads(current.read_text()) if current.exists() else None
        for family in ('e5', 'mu300'):
            path = self.root / ('etc/' + family + '/release.json')
            if path.is_file():
                m = json.loads(path.read_text())
                marker = hashlib.sha256(path.read_bytes()).hexdigest()
                if stored and stored.get('release_marker', marker) == marker:
                    return stored
                return dict(family=family, profile=m['profile'], version=m['image_version'],
                            kernel_release=m['kernel_release'])
        if stored: return stored
        raise ValueError('Missing release identity; use full installer once')

    def status(self):
        journal = self.state / 'journal.json'
        return dict(identity=self.identity(), transaction=json.loads(journal.read_text()) if journal.exists() else None)

    def inspect(self, patch):
        m = patch.manifest
        identity = self.identity()
        for key, expected in (('family', m['family']), ('profile', m['profile']),
                              ('version', m['base_version'])):
            if identity.get(key) != expected:
                raise ValueError('Device/base mismatch: ' + key)
        conflicts, backup, incoming = [], 0, 0
        ops, generated, preserved = self.plan(patch)
        for op in ops:
            actual = op['before']
            if actual and actual['kind'] == 'file': backup += actual['size']
            if op['after'] and op['after']['kind'] == 'file': incoming += op['after']['size']
        if m.get('boot'):
            from boot import target, layout
            path = target(self.root, m['family'])
            with path.open('rb') as stream: layout(stream.read(64 << 20), m['family'])
            backup += 64 << 20; incoming += m['boot']['size']
        required = backup + incoming + (32 << 20)
        required += sum(d.get('size', 0) for d in m.get('runtime', {}).values())
        free = shutil.disk_usage(self.root).free
        return dict(id=patch.id, manifest=m, conflicts=conflicts, package_conflicts=[],
                    preserved=preserved, required_bytes=required, free_bytes=free,
                    applicable=free >= required)

    def plan(self, patch):
        """Merge package ownership; replace release files, leave user state alone.

        A locally hotfixed system file is backed up as it actually is, not used
        as an excuse to reject the entire update. Extra installed packages and
        their owned files survive even when removed from the new base image.
        """
        m = patch.manifest
        installed_path = self.root/'lib/apk/db/installed'
        installed = package_records(installed_path.read_bytes()) if installed_path.is_file() else {}
        retained = set(installed)-set(m.get('base_packages', {}))
        retained.update(set(m.get('base_packages', {}))-set(m.get('target_packages', {})))
        owned = set()
        for name in retained:
            if name in installed: owned.update(package_paths(installed[name]))
        ops = []; generated = {}; preserved = []
        for raw in m['operations']:
            op = dict(raw); name = op['path']; actual = descriptor(self.path(name))
            after = op['after']; merge = op.get('merge')
            if merge == 'default' and actual is not None or name in owned and merge not in ('apk-db', 'apk-scripts'):
                preserved.append(name); continue
            if merge in ('apk-db', 'apk-scripts', 'apk-triggers', 'accounts', 'world'):
                if not after or after['kind'] != 'file': continue
                with patch.archive.open('blobs/'+after['sha256']) as stream: new = stream.read()
                current = self.path(name).read_bytes() if actual else b''
                if merge == 'apk-db':
                    records = package_records(current)
                    records.update(package_records(new))
                    new = ('\n\n'.join(records[n].rstrip('\n') for n in sorted(records))+'\n\n').encode()
                elif merge == 'apk-scripts':
                    new = merge_scripts(current, new)
                elif merge == 'apk-triggers':
                    new = ('\n'.join(dict.fromkeys(current.decode().splitlines()+new.decode().splitlines()))+'\n').encode()
                else:
                    separator = ':' if merge == 'accounts' else None
                    lines = current.decode().splitlines()
                    keys = {line.split(separator, 1)[0] if separator else re.split(r'[<>=~]', line, 1)[0] for line in lines}
                    for line in new.decode().splitlines():
                        key = line.split(separator, 1)[0] if separator else re.split(r'[<>=~]', line, 1)[0]
                        if key not in keys: lines.append(line); keys.add(key)
                        elif merge == 'world' and key in m.get('base_packages', {}):
                            lines = [line if re.split(r'[<>=~]', old, 1)[0] == key else old for old in lines]
                    new = ('\n'.join(lines)+'\n').encode()
                after = dict(after, size=len(new), sha256=hashlib.sha256(new).hexdigest())
                generated[after['sha256']] = new
            if actual and actual['kind'] == 'dir' and (not after or after['kind'] != 'dir'):
                raise ValueError('Cannot replace a user directory: '+name)
            if actual == after: continue
            # Package directories may already contain additional user data. Keep
            # their current permissions rather than rewriting a whole tree.
            if actual and after and actual['kind'] == after['kind'] == 'dir': continue
            op.update(before=actual, after=after)
            ops.append(op)
        return sorted(ops, key=lambda o:(o['path'].count('/'), o['path'])), generated, preserved

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
                    try:
                        path.rmdir()
                    except OSError as error:
                        import errno
                        if error.errno != errno.ENOTEMPTY: raise
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
        if data.get('boot'):
            from boot import write
            original = (self.state/data['backup']/'boot.img').read_bytes()
            if hashlib.sha256(original).hexdigest() != data['boot_sha256']:
                raise ValueError('Boot recovery backup checksum mismatch')
            write(self.path(data['boot']), original, data['boot_family'])
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
            # A boot payload updates the kernel identity; file-only deltas retain it.
            kernel_release = patch.manifest['kernel_release'] if patch.manifest.get('boot') else self.identity()['kernel_release']
            journal = self.state / 'journal.json'
            # Preserve the preceding transaction's recovery data until the new
            # journal is durable. A crash during backup must not invalidate it.
            backup = self.state / ('backup-' + str(time.time_ns()))
            backup.mkdir(mode=0o700)
            ops, generated, preserved = self.plan(patch)
            data = dict(id=patch.id, status='preparing', started=0, total=len(ops), backup=backup.name,
                        previous_identity=json.loads((self.state / 'current.json').read_text())
                        if (self.state / 'current.json').exists() else None,
                        target_version=patch.manifest['target_version'])
            # The large immutable plan is written once, not rewritten for every
            # file. A compact count journal keeps update writes O(n), not O(n虏).
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
            boot_images = None
            if patch.manifest.get('boot') or os.environ.get('UNISOC_OTA_LIVE') == '1':
                from boot import target, bind
                boot_path = target(self.root, patch.manifest['family'])
                with boot_path.open('rb') as stream: original = stream.read(64 << 20)
                with (backup/'boot.img').open('xb') as stream:
                    stream.write(original); stream.flush(); os.fsync(stream.fileno())
                incoming_boot = original
                if patch.manifest.get('boot'):
                    with patch.archive.open('blobs/'+patch.manifest['boot']['sha256']) as stream:
                        incoming_boot = stream.read()
                boot_images = (bind(original, original, patch.manifest['family']),
                               bind(incoming_boot, original, patch.manifest['family']))
                data['boot'] = str(boot_path.relative_to(self.root))
                data['boot_sha256'] = hashlib.sha256(original).hexdigest()
                data['boot_family'] = patch.manifest['family']
                del original, incoming_boot
            ram = os.environ.get('UNISOC_OTA_RAM')
            if ram:
                runtime = self.state/'runtime'
                runtime.mkdir(mode=0o700, exist_ok=True)
                for name in ('lib', 'lib64', 'usr', 'ota'):
                    source = Path(ram)/name
                    if source.is_dir(): shutil.copytree(source, runtime/name, dirs_exist_ok=True, symlinks=True)
                for path in runtime.rglob('*'):
                    if path.is_file() and not path.is_symlink():
                        with path.open('rb') as stream: os.fsync(stream.fileno())
                sync_dir(runtime)
            elif patch.manifest.get('boot') and (self.root == Path('/') or os.environ.get('UNISOC_OTA_LIVE') == '1'):
                raise ValueError('Boot updates must run from the isolated RAM runtime')
            sync_dir(backup)
            data['status'] = 'applying'
            atomic_json(journal, data)
            try:
                if boot_images:
                    from boot import write
                    write(self.root/data['boot'], boot_images[0], data['boot_family'])
                for i, op in enumerate(ops):
                    # Recheck at commit time; a concurrent user edit is a conflict.
                    if descriptor(self.path(op['path'])) != op['before']:
                        raise ValueError('File changed during update: ' + op['path'])
                    data['started'] = i + 1
                    atomic_json(journal, data)
                    value = op['after']
                    with contextlib.ExitStack() as stack:
                        source = (io.BytesIO(generated[value['sha256']]) if value['sha256'] in generated else stack.enter_context(patch.archive.open('blobs/' + value['sha256']))) if value and value['kind'] == 'file' else None
                        self.install_descriptor(op['path'], value, source)
                    if descriptor(self.path(op['path'])) != value:
                        raise ValueError('Written file verification failed: ' + op['path'])
                    progress(i + 1, len(ops), op['path'])
                if boot_images:
                    write(self.root/data['boot'], boot_images[1], data['boot_family'])
                identity = dict(family=patch.manifest['family'], profile=patch.manifest['profile'],
                                version=patch.manifest['target_version'], kernel_release=kernel_release)
                release_file = self.root/('etc/'+patch.manifest['family']+'/release.json')
                if release_file.is_file(): identity['release_marker'] = hashlib.sha256(release_file.read_bytes()).hexdigest()
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
    p.add_argument('--root')
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
