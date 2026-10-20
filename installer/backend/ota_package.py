"""Read full E5/MU300 releases without mounting or unpacking a root tree."""
import base64
from contextlib import ExitStack
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
import zipfile

from .ota_engine import FORMAT, MAX_BYTES, OFFLINE_ONLY, canonical, digest, package_versions, protected, safe_name, validate_manifest


class Release:
    def __init__(self, source, progress=lambda *_: None):
        self.source = Path(source)
        self.progress = progress
        self.stack = ExitStack()
        self.entries = {}
        self.packages = {}

    def __enter__(self):
        try:
            self.load()
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        self.stack.close()

    def load(self):
        if self.source.is_dir():
            self.prefix = ''
            self.files = None
        else:
            self.files = self.stack.enter_context(zipfile.ZipFile(self.source))
            names = self.files.namelist()
            if len(names) != len(set(names)):
                raise ValueError('Duplicate files in full package')
            matches = [n[:-len('files/device-profile.json')] for n in names if n.endswith('files/device-profile.json')]
            matches += [n[:-len('desktop/manifest.json')] for n in names if n.endswith('desktop/manifest.json')]
            if len(matches) != 1:
                raise ValueError('Select a single complete release ZIP')
            self.prefix = matches[0]
        if self.exists('desktop/manifest.json'):
            self.family = 'mu300'
            self.meta = self.json('desktop/release.json')
            self.package = self.json('desktop/manifest.json')
            mapping = self.package.get('magisk_layout', {})
            rootfs = mapping.get('mu300-openwrt.tar.gz', 'mu300-openwrt.tar.gz')
            self.boot_name = mapping.get('boot-linux.img', 'boot-linux.img')
            checks = {**self.package['magisk'], **self.package['desktop']}
            self.check(rootfs, checks[rootfs])
            self.check(self.boot_name, checks[self.boot_name])
            if 'desktop/release.json' in checks:
                self.check('desktop/release.json', checks['desktop/release.json'])
            source = self.stack.enter_context(self.open(rootfs))
            tar = self.stack.enter_context(tarfile.open(fileobj=source, mode='r|gz'))
            # Sequential tar reading avoids extracting a root tree or retaining a
            # second compressed copy. Only metadata and hashes stay in memory.
            for node in tar:
                name = node.name.removeprefix('./').rstrip('/')
                if name in ('', '.'):
                    continue
                safe_name(name)
                if name in self.entries:
                    raise ValueError('Duplicate rootfs path: ' + name)
                d = dict(mode=node.mode, uid=node.uid, gid=node.gid, xattrs={})
                for k, v in node.pax_headers.items():
                    if k.startswith('SCHILY.xattr.'):
                        d['xattrs'][k[13:]] = base64.b64encode(v.encode('utf-8', 'surrogateescape')).decode()
                if node.isdir():
                    d.update(kind='dir')
                elif node.issym():
                    d.update(kind='symlink', target=node.linkname)
                elif node.isfile():
                    stream = tar.extractfile(node)
                    if name == 'lib/apk/db/installed':
                        raw = stream.read(32 << 20)
                        if len(raw) != node.size:
                            raise ValueError('Oversized package database')
                        self.packages = package_versions(raw)
                        d.update(kind='file', size=node.size, sha256=hashlib.sha256(raw).hexdigest())
                    else:
                        d.update(kind='file', size=node.size, sha256=digest(stream))
                elif node.islnk():
                    target = node.linkname.removeprefix('./')
                    if target not in self.entries or self.entries[target]['kind'] != 'file':
                        raise ValueError('Unsupported forward tar hardlink: ' + name)
                    d = dict(self.entries[target])
                else:
                    continue
                self.entries[name] = d
                self.progress('index', name)
            self.rootfs_name = rootfs
        else:
            self.family = 'e5'
            self.meta = self.json('files/RELEASE.json')
            self.package = self.json('files/device-profile.json')
            checks = {}
            with self.open('files/SHA256SUMS') as stream:
                for line in stream.read().decode().splitlines():
                    h, n = line.split(None, 1)
                    checks[n.strip().removeprefix('*')] = h
            self.boot_name = 'files/boot.img'
            self.rootfs_name = 'files/openwrt.ext4.gz'
            self.check(self.rootfs_name, checks[self.rootfs_name])
            self.check(self.boot_name, checks[self.boot_name])
            for name in ('files/RELEASE.json', 'files/device-profile.json'):
                if name in checks:
                    self.check(name, checks[name])
            # One temporary decompressed image, closed/unlinked on every exit.
            # This is read-only userspace parsing, not a loop mount or WSL copy.
            scratch = self.source if self.source.is_dir() else self.source.parent
            image = self.stack.enter_context(tempfile.TemporaryFile(dir=scratch))
            with self.open(self.rootfs_name) as compressed, gzip.GzipFile(fileobj=compressed) as stream:
                total = 0
                for block in iter(lambda: stream.read(4 << 20), b''):
                    total += len(block)
                    if total > 16 << 30:
                        raise ValueError('Root image exceeds 16 GiB')
                    if shutil.disk_usage(scratch).free < len(block) + (64 << 20):
                        raise ValueError('Insufficient temporary disk space')
                    image.write(block)
            image.seek(0)
            from dissect.extfs import ExtFS
            self.extfs = ExtFS(image)
            seen = set()

            def walk(node, prefix=''):
                if node.inum in seen:
                    raise ValueError('Cyclic ext4 directory')
                seen.add(node.inum)
                for child in node.iterdir():
                    if child.filename in ('.', '..'):
                        continue
                    name = safe_name(prefix + child.filename)
                    d = dict(mode=int(stat.S_IMODE(child.inode.i_mode)), uid=int(child.inode.i_uid),
                             gid=int(child.inode.i_gid),
                             xattrs={a.name: base64.b64encode(a.value).decode() for a in child.xattr})
                    if child.filetype == stat.S_IFDIR:
                        d.update(kind='dir')
                    elif child.filetype == stat.S_IFLNK:
                        d.update(kind='symlink', target=child.link)
                    elif child.filetype == stat.S_IFREG:
                        with child.open() as f:
                            d.update(kind='file', size=child.size, sha256=digest(f))
                            if name == 'lib/apk/db/installed':
                                f.seek(0)
                                self.packages = package_versions(f.read(32 << 20))
                    else:
                        continue
                    self.entries[name] = d
                    self.progress('index', name)
                    if d['kind'] == 'dir':
                        walk(child, name + '/')
                seen.remove(node.inum)
            walk(self.extfs.root)
        for k in ('image_version', 'profile', 'kernel_release', 'openwrt'):
            if not isinstance(self.meta.get(k), str):
                raise ValueError('Full release has no versioned ' + k)
    def exists(self, name):
        return self.prefix + name in self.files.namelist() if self.files else (self.source / name).is_file()

    def open(self, name):
        safe_name(name)
        if self.files:
            return self.files.open(self.prefix + name)
        path = (self.source / name).resolve()
        if not path.is_relative_to(self.source.resolve()):
            raise ValueError('Package path escapes root')
        return path.open('rb')

    def json(self, name):
        with self.open(name) as f:
            return json.load(f)

    def check(self, name, wanted):
        with self.open(name) as stream:
            if digest(stream) != wanted:
                raise ValueError('Full package checksum mismatch: ' + name)

    def write_blobs(self, archive, wanted):
        pending = set(wanted)
        if hasattr(self, 'extfs'):
            for name, d in self.entries.items():
                if d.get('sha256') in pending:
                    with self.extfs.get(name).open() as source, archive.open('blobs/' + d['sha256'], 'w') as dest:
                        shutil.copyfileobj(source, dest, 1 << 20)
                    pending.remove(d['sha256'])
        else:
            with self.open(self.rootfs_name) as source, tarfile.open(fileobj=source, mode='r|gz') as tar:
                for node in tar:
                    name = node.name.removeprefix('./').rstrip('/')
                    d = self.entries.get(name, {})
                    if d.get('sha256') not in pending or not node.isfile():
                        continue
                    with tar.extractfile(node) as stream, archive.open('blobs/' + d['sha256'], 'w') as dest:
                        shutil.copyfileobj(stream, dest, 1 << 20)
                    pending.remove(d['sha256'])
        if pending:
            raise ValueError('Cannot find payload content: ' + ', '.join(pending))


def compare(old, new):
    for name in ('family',):
        if getattr(old, name) != getattr(new, name):
            raise ValueError('Packages belong to different device families')
    for k in ('profile', 'openwrt'):
        if old.meta[k] != new.meta[k]:
            raise ValueError('Incompatible full releases: ' + k)
    operations, preserved, blockers = [], [], []
    if ('usr/lib/unisoc-ota/engine.py' not in old.entries and
            'usr/lib/unisoc-ota/engine.py' in new.entries):
        blockers.append('First-time OTA installation: the old release has no OTA engine; bootstrap migration is not implemented')
    for name in sorted(set(old.entries) | set(new.entries)):
        before, after = old.entries.get(name), new.entries.get(name)
        if before == after:
            continue
        if any(name == p or name.startswith(p + '/') for p in OFFLINE_ONLY):
            blockers.append('Core runtime/module/updater changed; use the full installer: ' + name)
            continue
        if protected(name):
            preserved.append(name)
            continue
        if before and before['kind'] == 'dir' and not after:
            # Keeping an unused directory preserves user-installed contents.
            preserved.append(name)
            continue
        operations.append(dict(path=name, before=before, after=after))
    # Do not silently install files of new/removed APK packages without running
    # package ownership/dependency/script handling. Same package set hotfixes are
    # explicitly tracked as file overrides, leaving user packages untouched.
    for name in ('lib/apk/db/installed', 'usr/lib/opkg/status'):
        a, b = old.entries.get(name), new.entries.get(name)
        if (a is None) != (b is None):
            blockers.append('Package database format changed')
    owned = {'luci-app-t551m', 'luci-app-mu300'}
    if set(old.packages) != set(new.packages):
        added = sorted(set(new.packages) - set(old.packages))
        removed = sorted(set(old.packages) - set(new.packages))
        blockers.append('Installed package set changed; dependency migration is not implemented: '
                        f'{len(added)} added, {len(removed)} removed; '
                        + ', '.join((added + removed)[:8]))
    for name in sorted(set(old.packages) & set(new.packages)):
        a, b = old.packages[name], new.packages[name]
        if name in owned:
            a = dict(a, V=b['V'])
        if a != b:
            blockers.append('Package ABI/dependencies changed: ' + name)
    m = dict(format=FORMAT, family=new.family, profile=new.meta['profile'],
             base_version=old.meta['image_version'], target_version=new.meta['image_version'],
             kernel_release=new.meta['kernel_release'], operations=operations,
             preserved=preserved, mode='file-hotfix', base_packages=old.packages)
    if operations:
        try:
            validate_manifest(m)
        except ValueError as e:
            blockers.append(str(e))
    else:
        blockers.append('No hotfixable system files changed')
    blobs = {o['after']['sha256']: o['after']['size'] for o in operations
             if o['after'] and o['after']['kind'] == 'file'}
    return dict(manifest=m, blockers=blockers, can_export=not blockers,
                payload_bytes=sum(blobs.values()), files=len(operations),
                added=sum(o['before'] is None for o in operations),
                removed=sum(o['after'] is None for o in operations))


def analyze(old_path, new_path, output=None, progress=lambda *_: None):
    # Old image is released before decompressing the new one. Only the previous
    # index survives; no pair of multi-GiB roots is retained.
    with Release(old_path, progress) as old:
        pass
    with Release(new_path, progress) as new:
        result = compare(old, new)
        if output:
            if result['blockers']:
                raise ValueError('; '.join(result['blockers'][:12]))
            output = Path(output)
            if output.exists():
                raise ValueError('Output already exists; select a new filename')
            tmp = output.with_suffix(output.suffix + '.building')
            if tmp.exists():
                raise ValueError('Output staging exists')
            try:
                with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                    archive.writestr('manifest.json', canonical(result['manifest']))
                    wanted = {o['after']['sha256'] for o in result['manifest']['operations']
                              if o['after'] and o['after']['kind'] == 'file'}
                    new.write_blobs(archive, wanted)
                from .ota_engine import Patch
                with Patch(tmp):
                    pass
                os.rename(tmp, output)
                result['output'] = str(output)
                result['archive_bytes'] = output.stat().st_size
            finally:
                if tmp.exists():
                    tmp.unlink()
        return result
