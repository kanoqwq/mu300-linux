#!/usr/bin/env python3
"""Read-only Android v4 / LP / newc tooling. No device or flash operations."""
import argparse
import hashlib
import io
import json
import re
import stat
import struct
import tarfile
import zlib
from pathlib import Path, PurePosixPath
import lz4.block


def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(n, size=4096):
    return (n + size - 1) // size * size


def checked(data, off, size):
    if off < 0 or size < 0 or off + size > len(data):
        raise ValueError('out-of-bounds image extent')
    return data[off:off + size]


def avb_footer(data):
    """Read framing only. This does NOT verify RSA or authenticate payloads."""
    if len(data) < 64 or data[-64:-60] != b'AVBf':
        raise ValueError('missing original AVB footer')
    major, minor, original_size, offset, size = struct.unpack_from('>IIQQQ', data, len(data) - 60)
    if major != 1 or original_size > offset or offset + size > len(data) - 64 or size < 256:
        raise ValueError('invalid original AVB footer extent')
    metadata = checked(data, offset, size)
    if metadata[:4] != b'AVB0':
        raise ValueError('invalid original vbmeta magic')
    return {'original_size': original_size, 'offset': offset, 'size': size,
            'metadata': metadata, 'footer': data[-64:]}


def retain_avb_container(template, payload):
    """Keep the stock vbmeta/key structure for existing Root/TOS bypass paths.

    The old descriptor digest does NOT authenticate the new payload. Retaining
    its container avoids assuming that a verifier bypass also bypasses format
    parsing. No new signature, key, unlock state or verification flag is created.
    """
    original = avb_footer(template)
    payload += bytes(-len(payload) % 4096)
    offset = len(payload)
    body = payload + original['metadata']
    if len(body) + 64 > len(template):
        raise ValueError('payload and retained vbmeta exceed partition')
    footer = bytearray(original['footer'])
    struct.pack_into('>QQQ', footer, 12, len(payload), offset, original['size'])
    image = body + bytes(len(template) - 64 - len(body)) + bytes(footer)
    if avb_footer(image)['metadata'] != original['metadata']:
        raise ValueError('retained AVB metadata mismatch')
    return image


def safe_name(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name or not p.parts:
        raise ValueError('unsafe archive path: ' + name)
    return str(p)


def boot(data):
    if data[:8] != b'ANDROID!' or len(data) < 4096:
        raise ValueError('not an Android boot image')
    k, r = struct.unpack_from('<II', data, 8)
    ver = struct.unpack_from('<I', data, 40)[0]
    if ver != 4 or struct.unpack_from('<I', data, 20)[0] != 1584:
        raise ValueError('only Android boot header v4 supported')
    return checked(data, 4096, k), checked(data, 4096 + align(k), r)


def vendor_boot(data):
    if data[:8] != b'VNDRBOOT':
        raise ValueError('not vendor_boot')
    ver, page = struct.unpack_from('<II', data, 8)
    if ver != 4 or page != 4096:
        raise ValueError('only vendor_boot v4 / 4096 supported')
    ram_size = struct.unpack_from('<I', data, 24)[0]
    dt_size = struct.unpack_from('<I', data, 2100)[0]
    table_size, count, entry_size, bc_size = struct.unpack_from('<4I', data, 2112)
    dt_off = page + align(ram_size)
    table_off = dt_off + align(dt_size)
    if entry_size != 108 or table_size != count * entry_size or count != 1:
        raise ValueError('expected one vendor ramdisk; multi-fragment selection not implemented')
    size, off, kind = struct.unpack_from('<III', checked(data, table_off, table_size))
    if off or size != ram_size:
        raise ValueError('unexpected vendor ramdisk extent')
    return (checked(data, page, ram_size), checked(data, dt_off, dt_size),
            checked(data, table_off + align(table_size), bc_size))


def unlz4(data):
    if data[:4] != b'\x02\x21\x4c\x18':
        raise ValueError('expected LZ4 legacy frame')
    out = bytearray()
    off = 4
    while off < len(data):
        n = struct.unpack('<I', checked(data, off, 4))[0]
        off += 4
        if not n:
            if off != len(data):
                raise ValueError('trailing data after LZ4 terminator')
            break
        out.extend(lz4.block.decompress(checked(data, off, n), uncompressed_size=8 * 1024 * 1024))
        off += n
        if len(out) > 512 * 1024 * 1024:
            raise ValueError('ramdisk exceeds limit')
    return bytes(out)


def lz4_legacy(data):
    out = bytearray(b'\x02\x21\x4c\x18')
    for off in range(0, len(data), 8 * 1024 * 1024):
        block = lz4.block.compress(data[off:off + 8 * 1024 * 1024], store_size=False)
        out.extend(struct.pack('<I', len(block)))
        out.extend(block)
    return bytes(out)


def cpio_read(data):
    out = {}
    off = 0
    while off < len(data):
        hdr = checked(data, off, 110)
        if hdr[:6] != b'070701':
            raise ValueError('expected newc archive')
        fields = [int(hdr[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        size, namesize = fields[6], fields[11]
        rawname = checked(data, off + 110, namesize)
        if not rawname.endswith(b'\0'):
            raise ValueError('unterminated name')
        name = rawname[:-1].decode()
        off = align(off + 110 + namesize, 4)
        payload = checked(data, off, size)
        off = align(off + size, 4)
        if name == 'TRAILER!!!':
            if any(data[off:]):
                raise ValueError('unexpected concatenated CPIO archive')
            return out
        if name == '.':
            continue
        name = safe_name(name)
        if name in out:
            raise ValueError('duplicate CPIO name: ' + name)
        # Data-bearing regular hardlinks need inode resolution before conversion.
        if stat.S_ISREG(fields[1]) and fields[4] > 1:
            raise ValueError('CPIO hardlinks not supported')
        out[name] = (fields[1], payload)
    raise ValueError('missing CPIO trailer')


def cpio_write(files):
    files = dict(files)
    for name in list(files):
        safe_name(name)
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.':
                files.setdefault(str(parent), (stat.S_IFDIR | 0o755, b''))
    out = bytearray()
    entries = sorted(files.items(), key=lambda x: (x[0].count('/'), x[0]))
    entries.append(('TRAILER!!!', (0, b'')))
    for ino, (name, (mode, data)) in enumerate(entries, 1):
        nb = name.encode() + b'\0'
        fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nb), 0]
        out += b'070701' + ''.join('%08x' % x for x in fields).encode() + nb
        out += b'\0' * (-len(out) % 4)
        out += data
        out += b'\0' * (-len(out) % 4)
    return bytes(out)


def write_tar(path, files):
    with tarfile.open(path, 'w') as tf:
        for name, (mode, data) in sorted(files.items()):
            ti = tarfile.TarInfo(safe_name(name))
            ti.mode = stat.S_IMODE(mode)
            ti.uid = ti.gid = ti.mtime = 0
            if stat.S_ISDIR(mode):
                ti.type = tarfile.DIRTYPE
            elif stat.S_ISLNK(mode):
                ti.type = tarfile.SYMTYPE
                ti.linkname = data.decode()
            elif stat.S_ISREG(mode):
                ti.size = len(data)
            else:
                continue
            tf.addfile(ti, io.BytesIO(data) if ti.isreg() else None)


def logical_partitions(path):
    """Validate primary geometry and metadata; support raw single-device LP only."""
    total = path.stat().st_size
    with path.open('rb') as f:
        f.seek(4096)
        geo = f.read(4096)
        if geo[:4] != b'gDla':
            raise ValueError('expected raw Android super (not sparse)')
        size = struct.unpack_from('<I', geo, 4)[0]
        if size != 52:
            raise ValueError('unsupported LP geometry')
        temp = bytearray(geo[:size]); temp[8:40] = bytes(32)
        if hashlib.sha256(temp).digest() != geo[8:40]:
            raise ValueError('LP geometry checksum mismatch')
        f.seek(12288)
        hdr = f.read(256)
        magic, major, minor, hs = struct.unpack_from('<IHHI', hdr)
        if magic != 0x414C5030 or major != 10 or hs not in (128, 256):
            raise ValueError('unsupported LP header')
        temp = bytearray(hdr[:hs]); temp[12:44] = bytes(32)
        if hashlib.sha256(temp).digest() != hdr[12:44]:
            raise ValueError('LP header checksum mismatch')
        ts = struct.unpack_from('<I', hdr, 44)[0]
        maxmeta = struct.unpack_from('<I', geo, 40)[0]
        if hs + ts > maxmeta:
            raise ValueError('LP tables exceed metadata region')
        f.seek(12288 + hs); tables = f.read(ts)
        if hashlib.sha256(tables).digest() != hdr[48:80]:
            raise ValueError('LP table checksum mismatch')
        desc = [struct.unpack_from('<III', hdr, 80 + i * 12) for i in range(4)]
        for off, count, width in desc:
            checked(tables, off, count * width)
        po, pn, ps = desc[0]; eo, en, es = desc[1]
        if ps != 52 or es != 24 or desc[3][1] != 1:
            raise ValueError('unsupported LP table layout')
        result = {}
        for i in range(pn):
            off = po + i * ps
            name = safe_name(tables[off:off + 36].split(b'\0')[0].decode())
            attrs, first, count, group = struct.unpack_from('<4I', tables, off + 36)
            if first + count > en or name in result:
                raise ValueError('invalid LP partition')
            extents = []
            for j in range(first, first + count):
                sectors, kind, start, source = struct.unpack_from('<QIQI', tables, eo + j * es)
                if kind != 0 or source != 0 or (start + sectors) * 512 > total:
                    raise ValueError('unsupported/out-of-bounds LP extent')
                extents.append((start * 512, sectors * 512))
            result[name] = extents
        return result


def analyze(source, out, extract_super=False):
    if out.exists():
        raise ValueError('output exists; use a new directory')
    # Validate inputs before creating output.
    images = {n: (source / n).read_bytes() for n in ['boot_a', 'init_boot_a', 'vendor_boot_a']}
    kernel, br = boot(images['boot_a'])
    ik, generic = boot(images['init_boot_a'])
    if not kernel or br or ik or not generic:
        raise ValueError('expected split boot/init_boot layout')
    vendor, dt, bc = vendor_boot(images['vendor_boot_a'])
    vf = cpio_read(unlz4(vendor)); gf = cpio_read(unlz4(generic))
    marker = kernel.find(b'IKCFG_ST')
    if marker < 0:
        raise ValueError('missing embedded kernel config')
    cfg = zlib.decompress(kernel[marker + 8:], 31)
    parts = logical_partitions(source / 'super')
    version = re.search(rb'Linux version ([^\0\n]+)', kernel).group(1).decode()
    mods = {n: {'sha256': sha(d), 'vermagic': [v.decode() for v in re.findall(rb'vermagic=([^\0]+)', d)]}
            for n, (m, d) in vf.items() if n.endswith('.ko')}
    report = {'source': str(source.resolve()), 'kernel_version': version,
              'inputs': {n: {'bytes': len(d), 'sha256': sha(d)} for n, d in images.items()},
              'kernel_sha256': sha(kernel), 'modules': mods,
              'logical_partitions': {n: {'bytes': sum(s for o, s in ex), 'extents': ex} for n, ex in parts.items()},
              'bootconfig': bc.decode(errors='replace'),
              'status': 'offline analysis only; no device compatibility established'}
    out.mkdir(parents=True)
    for name, data in [('Image', kernel), ('kernel.config', cfg), ('vendor-dtb.img', dt), ('bootconfig', bc)]:
        (out / name).write_bytes(data)
    write_tar(out / 'vendor-ramdisk.tar', vf)
    write_tar(out / 'generic-ramdisk.tar', gf)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if extract_super:
        with (source / 'super').open('rb') as inp:
            for name in ['vendor_a', 'vendor_dlkm_a', 'system_dlkm_a', 'system_a', 'system_ext_a', 'odm_a']:
                if not parts.get(name):
                    raise ValueError('missing required partition ' + name)
                h = hashlib.sha256()
                with (out / (name + '.img')).open('wb') as dst:
                    for off, size in parts[name]:
                        inp.seek(off)
                        while size:
                            chunk = inp.read(min(size, 4 * 1024 * 1024))
                            if not chunk:
                                raise ValueError('short super read')
                            dst.write(chunk); h.update(chunk); size -= len(chunk)
                report['logical_partitions'][name]['sha256'] = h.hexdigest()
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'kernel': version, 'vendor_modules': len(mods), 'output': str(out)}, indent=2))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--extract-super', action='store_true')
    a = ap.parse_args()
    analyze(a.source, a.out, a.extract_super)
