"""Read full E5/MU300 releases without mounting or unpacking a root tree."""
import base64
from contextlib import ExitStack
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
import zipfile

from .ota_engine import FORMAT, MAX_BYTES, canonical, digest, package_versions, protected, safe_name, validate_manifest


def patch_filename(old, new, source):
    """Human-readable save suggestion from release metadata, never an export gate."""
    def safe(text, limit=180):
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', str(text)).strip().rstrip('. ')
        return text.encode('utf-8')[:limit].decode('utf-8', 'ignore').rstrip('. ')

    def marker(meta):
        version = meta.get('image_version', '')
        date = re.search(r'(?:^|-)r?(\d{8}(?:\.\d{6})?)(?=-|$)', version)
        commit = re.search(r'(?:^|-)g([a-fA-F0-9]{7,64})(?=-|$)', version)
        if date and commit: return date[1]+'-g'+commit[1][:8].lower()

    start, end = marker(old), marker(new)
    if not start or not end:
        # A renamed release still gets a usable name even for legacy metadata.
        leaf = str(source).replace('\\', '/').rsplit('/', 1)[-1]
        stem = leaf[:-4] if leaf.lower().endswith('.zip') else leaf
        stem = safe(stem)
        return 'OpenWrt-patch'+('-'+stem if stem else '')+'.zip'
    models = {'e5':'E5', 't551m':'T551M', 't512n':'T512N', 'm8':'M8',
              'yuanhang41s':'Yuanhang41S', 'k1':'K1', 'u30air':'U30Air',
              'mu300':'MU300-F50-V50-U30Air', 'f50':'F50', 'v50':'V50'}
    profile = new.get('profile', '')
    model = models.get(profile, safe(profile, 32) or 'Device')
    version = safe(new.get('openwrt', ''), 24)
    previous = safe(old.get('openwrt', ''), 24)
    if previous and version and previous != version: version = previous+'-to-'+version
    kernel = re.match(r'\d+(?:\.\d+){1,3}', new.get('kernel_release', ''))
    parts = ['OpenWrt', 'patch', model]
    if version: parts.append(version)
    if kernel: parts.append('k'+kernel[0])
    parts += [start, 'to', end]
    return safe('-'.join(parts), 220)+'.zip'


class Release:
    def __init__(self, source, progress=lambda *_: None):
        self.source = Path(source)
        self.progress = progress
        self.stack = ExitStack()
        self.entries = {}
        self.packages = {}
        self.package_db = b''

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
                        self.package_db = raw
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
                                self.package_db = f.read(32 << 20)
                                self.packages = package_versions(self.package_db)
                    else:
                        continue
                    self.entries[name] = d
                    self.progress('index', name)
                    if d['kind'] == 'dir':
                        walk(child, name + '/')
                seen.remove(node.inum)
            walk(self.extfs.root)
        with self.open(self.boot_name) as stream:
            self.boot_digest = digest(stream)
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
    for k in ('profile',):
        if old.meta[k] != new.meta[k]:
            raise ValueError('Incompatible full releases: ' + k)
    from .ota_runtime import build, bootstrap
    runtime, busybox, support = build(new)
    # The exported updater must also install the protocol engine capable of
    # accepting its future patches, including on the very first migration.
    if busybox and 'usr/bin/python3' in runtime:
        for name in ('engine.py', 'boot.py', 'rpc.py', 'proxy.sh'):
            new.entries['usr/lib/unisoc-ota/'+name] = runtime['ota/'+name]
        new.entries['usr/libexec/rpcd/unisoc-ota'] = dict(runtime['ota/proxy.sh'], mode=0o755)
        for name, destination in (
            ('unisoc-ota.js', 'www/luci-static/resources/view/system/unisoc-ota.js'),
            ('errors.js', 'www/luci-static/resources/unisoc-ota/errors.js'),
            ('unisoc-ota.json', 'usr/share/rpcd/acl.d/unisoc-ota.json')):
            new.entries[destination] = runtime['ota/'+name]
        for name in ('usr/lib/unisoc-ota', 'usr/libexec', 'usr/libexec/rpcd'):
            new.entries.setdefault(name, dict(kind='dir', mode=0o755, uid=0, gid=0, xattrs={}))
    operations, preserved, blockers = [], [], []
    for name in sorted(set(old.entries) | set(new.entries)):
        before, after = old.entries.get(name), new.entries.get(name)
        if before == after:
            continue
        merge = None
        if name in ('etc/e5/release.json', 'etc/mu300/release.json'):
            merge = 'release'
        elif name in ('etc/passwd', 'etc/shadow', 'etc/group', 'etc/gshadow'):
            merge = 'accounts'
        elif name == 'etc/apk/world':
            merge = 'world'
        elif name == 'lib/apk/db/installed':
            merge = 'apk-db'
        elif name == 'lib/apk/db/scripts.tar':
            merge = 'apk-scripts'
        elif name == 'lib/apk/db/triggers':
            merge = 'apk-triggers'
        elif protected(name):
            # Config defaults may be introduced, but never overwrite a device's
            # existing value or re-create an already-consumed uci-default script.
            if name.startswith('etc/') and before is None and after and after['kind'] != 'dir' and not name.startswith(('etc/unisoc-ota/', 'etc/uci-defaults/')):
                merge = 'default'
            else:
                preserved.append(name)
                continue
        if before and before['kind'] == 'dir' and not after:
            preserved.append(name)
            continue
        op = dict(path=name, before=before, after=after)
        if merge: op['merge'] = merge
        operations.append(op)
    # The release database is merged by package name on the device. User-added
    # packages, ownership records, world selections and data are never replaced
    # with the build machine's package database.
    m = dict(format=FORMAT, family=new.family, profile=new.meta['profile'],
             base_version=old.meta['image_version'], target_version=new.meta['image_version'],
             kernel_release=new.meta['kernel_release'], operations=operations,
             preserved=preserved, mode='differential', base_packages=old.packages,
             target_packages=new.packages)
    old_boot = old.boot_digest
    with new.open(new.boot_name) as stream:
        content = stream.read(128 << 20)
    new_boot = hashlib.sha256(content).hexdigest()
    extra = {}
    if old_boot != new_boot:
        m['boot'] = dict(sha256=new_boot, size=len(content))
        extra[new_boot] = content
    if busybox and 'usr/bin/python3' in runtime:
        m['runtime'] = runtime
        m['busybox'] = busybox
        script = bootstrap(runtime, busybox)
        h = hashlib.sha256(script).hexdigest()
        m['bootstrap'] = dict(sha256=h, size=len(script))
        support[h] = script
        extra.update(support)
    # Missing runtime only matters to actual bootstrap, not to generation of
    # synthetic/test releases. Real full releases contain static BusyBox/Python.
    try:
        validate_manifest(m)
    except ValueError as e:
        blockers.append(str(e))
    blobs = {o['after']['sha256']: o['after']['size'] for o in operations
             if o['after'] and o['after']['kind'] == 'file'}
    for desc in m.get('runtime', {}).values():
        if desc['kind'] == 'file': blobs[desc['sha256']] = desc['size']
    for key in ('boot', 'busybox', 'bootstrap'):
        if key in m: blobs[m[key]['sha256']] = m[key]['size']
    return dict(manifest=m, blockers=blockers, can_export=not blockers,
                suggested_name=patch_filename(old.meta, new.meta, new.source),
                payload_bytes=sum(blobs.values()), files=len(operations),
                added=sum(o['before'] is None for o in operations),
                removed=sum(o['after'] is None for o in operations)), extra


def analyze(old_path, new_path, output=None, progress=lambda *_: None):
    # Old image is released before decompressing the new one. Only the previous
    # index survives; no pair of multi-GiB roots is retained.
    with Release(old_path, progress) as old:
        pass
    with Release(new_path, progress) as new:
        result, extra = compare(old, new)
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
                    for desc in result['manifest'].get('runtime', {}).values():
                        if desc['kind'] == 'file': wanted.add(desc['sha256'])
                    for h, content in extra.items():
                        archive.writestr('blobs/'+h, content)
                    new.write_blobs(archive, wanted-set(extra))
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
