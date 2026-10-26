#!/usr/bin/env python3
"""The manifest of a root filesystem image: one line for every file it holds.

  tools/image-manifest.py make ROOTFS.tar.gz [IMAGE TAG]   print the manifest of a published image
  tools/image-manifest.py check ROOTFS.tar.gz              the manifest the image carries (etc/mu300/image-manifest.gz)
                                                           is the one of its own files; exit 1 and the differences if not
  tools/image-manifest.py legacy RELEASE_DIR OUT_DIR       for a release from before images carried one: the manifests
                                                           of its four images as OUT_DIR/<image>/<tag>.gz
  tools/image-manifest.py assets RELEASE_DIR OUT_DIR       the same manifests under the names a release publishes them
                                                           with: OUT_DIR/mu300-<...>-rootfs.manifest.gz
  tools/image-manifest.py union OUT.gz MANIFEST.gz...      the manifests of one image in many releases as one file
                                                           (rootfs/overlay/opt/mu300/lib/image-manifests/<image>.gz)
  tools/image-manifest.py extract UNION.gz TAG             one release's manifest out of such a file

An update needs to know which files of the installed system came with its image and which are the user's: a path the
old image did not have was put there by the user (or by software they installed), one it had with other content was
changed by them (mu300-update, the carry block; docs/FINDINGS.md 85). Images from the first release with a four-field tag on carry their own
manifest, written when they are built (mu300-carry manifest, the same lines); for the releases before that the
manifests made here ship in the images.

The format, one entry per line, sorted by path (bytewise):
  TYPE MODE UID GID SIZE HASH PATH
  TYPE  f file (a hard link is a file), d directory, l symbolic link, c b character and block device, p FIFO
  MODE  octal, permission bits only    SIZE  bytes of a file, length of a link's target, else 0
  HASH  sha256 of a file's content, of a link's target, else -
  PATH  relative to the root, no leading ./ ; a backslash is written \\\\ and a newline \\n
under a first line "mu300-image-manifest 1 IMAGE TAG" (IMAGE: ubuntu-24.04, ubuntu-26.04, openwrt, openwrt-luci).
"""
import gzip
import hashlib
import io
import os
import sys
import tarfile

SELF = 'etc/mu300/image-manifest.gz'
IMAGES = {'mu300-ubuntu-rootfs.tar.gz': 'ubuntu-24.04', 'mu300-ubuntu-26.04-rootfs.tar.gz': 'ubuntu-26.04',
          'mu300-openwrt-rootfs.tar.gz': 'openwrt', 'mu300-openwrt-luci-rootfs.tar.gz': 'openwrt-luci'}
TYPES = {tarfile.REGTYPE: 'f', tarfile.AREGTYPE: 'f', tarfile.LNKTYPE: 'f', tarfile.DIRTYPE: 'd', tarfile.SYMTYPE: 'l',
         tarfile.CHRTYPE: 'c', tarfile.BLKTYPE: 'b', tarfile.FIFOTYPE: 'p'}


def escape(path):
    return path.replace('\\', '\\\\').replace('\n', '\\n')


def norm(name):
    """a member's name as a path relative to the root; '' for the root itself"""
    name = name.rstrip('/')
    while name.startswith('./'):
        name = name[2:]
    return '' if name == '.' else name


def entries(tarball):
    """{path: line} for every member of TARBALL but the root and the image's own manifest"""
    out, files = {}, {}
    with tarfile.open(tarball, errorlevel=1) as t:
        for ti in t:
            path = norm(ti.name)
            if not path or path == SELF:
                continue
            if path.startswith('/') or '..' in path.split('/'):
                raise SystemExit(f'{tarball}: a member is outside the root: {ti.name!r}')
            kind = TYPES.get(ti.type)
            if kind is None:
                raise SystemExit(f'{tarball}: {ti.name!r} is of a type a manifest cannot say ({ti.type!r})')
            size, digest = 0, '-'
            if ti.type == tarfile.LNKTYPE:
                # a second name of a file that came earlier in the archive
                size, digest = files[norm(ti.linkname)]
            elif kind == 'f':
                h = hashlib.sha256()
                f = t.extractfile(ti)
                for block in iter(lambda: f.read(1 << 20), b''):
                    h.update(block)
                size, digest = ti.size, h.hexdigest()
                files[path] = (size, digest)
            elif kind == 'l':
                target = ti.linkname.encode('utf-8', 'surrogateescape')
                size, digest = len(target), hashlib.sha256(target).hexdigest()
            mode = 0o777 if kind == 'l' else ti.mode & 0o7777
            out[path] = f'{kind} {mode:o} {ti.uid} {ti.gid} {size} {digest} {escape(path)}'
    return out


def manifest(tarball, image, tag):
    e = entries(tarball)
    lines = [f'mu300-image-manifest 1 {image} {tag}']
    lines += [e[p] for p in sorted(e, key=lambda p: escape(p).encode('utf-8', 'surrogateescape'))]
    return '\n'.join(lines) + '\n'


def image_of(tarball):
    name = os.path.basename(tarball)
    if name not in IMAGES:
        raise SystemExit(f'{name} is not the name of a release image: pass IMAGE and TAG')
    return IMAGES[name]


def tag_of(tarball):
    with tarfile.open(tarball) as t:
        for ti in t:
            if norm(ti.name) == 'etc/mu300/image-version':
                return t.extractfile(ti).read().decode().strip()
    raise SystemExit(f'{tarball} has no etc/mu300/image-version')


def gz(text):
    """gzip without a name or a time in it: the same manifest is the same file"""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0, compresslevel=9) as f:
        f.write(text.encode('utf-8', 'surrogateescape'))
    return buf.getvalue()


def ranges(ix):
    """[0, 1, 2, 5] as "0-2,5" """
    out, start, prev = [], ix[0], ix[0]
    for x in ix[1:] + [None]:
        if x != prev + 1:
            out.append(f'{start}-{prev}' if prev != start else f'{start}')
            start = x
        prev = x
    return ','.join(out)


def union(manifests):
    """The manifests of one image in many releases as one text: two releases differ in a few dozen lines, so every
    distinct line is written once, with the releases that have it in front (their places in the "tags" line)."""
    heads = [m.split('\n', 1)[0].split(' ') for m in manifests]
    images = {h[2] for h in heads}
    if len(images) != 1 or any(h[:2] != ['mu300-image-manifest', '1'] for h in heads):
        raise SystemExit(f'not manifests of one image: {sorted(images)}')
    order = sorted(range(len(manifests)), key=lambda i: heads[i][3])
    tags = [heads[i][3] for i in order]
    if len(set(tags)) != len(tags):
        raise SystemExit('two manifests of one release')
    lines = {}
    for n, i in enumerate(order):
        for line in manifests[i].split('\n')[1:]:
            if line:
                lines.setdefault(line, []).append(n)
    body = sorted((line.split(' ', 6)[6].encode('utf-8', 'surrogateescape'), line, ix) for line, ix in lines.items())
    return '\n'.join([f'mu300-image-manifests 1 {images.pop()}', 'tags ' + ' '.join(tags)] +
                     [f'{ranges(ix)} {line}' for _, line, ix in body]) + '\n'


def extract(text, tag):
    """the manifest of release TAG out of a union (what mu300-update's carry block does with awk)"""
    head, tags, *body = text.split('\n')
    tags = tags.split(' ')[1:]
    if tag not in tags:
        raise SystemExit(f'no manifest of {tag} in it')
    n = tags.index(tag)
    out = [f'mu300-image-manifest 1 {head.split(" ")[2]} {tag}']
    for line in body:
        if not line:
            continue
        where, rest = line.split(' ', 1)
        for r in where.split(','):
            a, _, b = r.partition('-')
            if int(a) <= n <= int(b or a):
                out.append(rest)
    return '\n'.join(out) + '\n'


def main(argv):
    if len(argv) in (3, 5) and argv[1] == 'make':
        image, tag = (argv[3], argv[4]) if len(argv) == 5 else (image_of(argv[2]), tag_of(argv[2]))
        sys.stdout.write(manifest(argv[2], image, tag))
    elif len(argv) == 3 and argv[1] == 'check':
        with tarfile.open(argv[2]) as t:
            carried = None
            for ti in t:
                if norm(ti.name) == SELF:
                    carried = gzip.decompress(t.extractfile(ti).read()).decode('utf-8', 'surrogateescape')
        if carried is None:
            raise SystemExit(f'{argv[2]} carries no {SELF}')
        image, tag = carried.split('\n', 1)[0].split(' ')[2:4]
        want = manifest(argv[2], image, tag)
        if want != carried:
            a, b = set(want.splitlines()), set(carried.splitlines())
            for line in sorted(a - b)[:20]:
                print('only in the image:   ', line)
            for line in sorted(b - a)[:20]:
                print('only in the manifest:', line)
            raise SystemExit(f'{argv[2]}: the manifest it carries is not the one of its files')
        print(f'{os.path.basename(argv[2])}: {image} {tag}, {len(want.splitlines()) - 1} entries, as the image carries it')
    elif len(argv) == 4 and argv[1] in ('legacy', 'assets'):
        for name, image in IMAGES.items():
            tarball = os.path.join(argv[2], name)
            if not os.path.exists(tarball):
                print(f'{argv[2]}: no {name}', file=sys.stderr)
                continue
            tag = tag_of(tarball)
            out = os.path.join(argv[3], image, tag + '.gz')
            if argv[1] == 'assets':
                out = os.path.join(argv[3], name[:-len('.tar.gz')] + '.manifest.gz')
            os.makedirs(os.path.dirname(out), exist_ok=True)
            data = gz(manifest(tarball, image, tag))
            with open(out, 'wb') as f:
                f.write(data)
            print(f'{out}: {len(data)} bytes')
    elif len(argv) >= 4 and argv[1] == 'union':
        data = gz(union([gzip.open(p, 'rt', errors='surrogateescape').read() for p in argv[3:]]))
        with open(argv[2], 'wb') as f:
            f.write(data)
        print(f'{argv[2]}: {len(argv) - 3} manifests, {len(data)} bytes')
    elif len(argv) == 4 and argv[1] == 'extract':
        sys.stdout.write(extract(gzip.open(argv[2], 'rt', errors='surrogateescape').read(), argv[3]))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
