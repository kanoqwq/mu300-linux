"""Boot-v4 differential OTA support, including dynamic Android/Linux slot roles.

Only Linux boot is written. Preserve its command line, slot bindings and boot
diagnostic area. A recovery hook runs before switch_root after an interrupted
root transaction. No external LZ4 package is required on the device.
"""
import hashlib
import os
from pathlib import Path
import stat
import struct

MAGIC = bytes.fromhex('02214c18')
HOOK = '''
# unisoc-ota-recovery-v2
if [ -s /newroot/etc/unisoc-ota/journal.json ] && grep -Eq '"status":"(applying|rolling_back)"' /newroot/etc/unisoc-ota/journal.json; then
    touch /run/stay
    kill ${persist_job:-} ${timer_job:-} $(jobs -p) 2>/dev/null || :
    ota_ram=/run/unisoc-ota-recovery
    mkdir -p "$ota_ram"
    mount -t tmpfs -o mode=700 tmpfs "$ota_ram" || exec /bin/sh
    cp -a /newroot/etc/unisoc-ota/runtime/. "$ota_ram/" || exec /bin/sh
    mkdir -p "$ota_ram/target" "$ota_ram/dev" "$ota_ram/proc"
    mount --bind /newroot "$ota_ram/target" || exec /bin/sh
    mount --bind /dev "$ota_ram/dev" || exec /bin/sh
    mount -t proc proc "$ota_ram/proc" || exec /bin/sh
    chroot "$ota_ram" /usr/bin/python3 /ota/engine.py recover --root /target || exec /bin/sh
    sync
    reboot -f
    exec /bin/sh
fi
'''


_native_decode = None


def block_decode(data):
    global _native_decode
    if _native_decode is None:
        helper = Path(__file__).with_name('lz4raw-aarch64.so')
        if helper.is_file():
            try:
                import ctypes
            except ImportError:
                # E5's deliberately small Python distribution omits ctypes.
                # Valid boot data still decodes using the standard library;
                # the native decoder is an acceleration, not a dependency.
                _native_decode = block_decode_python
                return _native_decode(data)
            try:
                library = ctypes.CDLL(str(helper))
            except OSError:
                _native_decode = block_decode_python
                return _native_decode(data)
            fn = library.unisoc_lz4_decode
            fn.argtypes = (ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
            fn.restype = ctypes.c_int
            def decode(block):
                buffer = ctypes.create_string_buffer(8 << 20)
                size = fn(block, len(block), buffer, 8 << 20)
                if size < 0: raise ValueError('Invalid LZ4 block')
                return ctypes.string_at(buffer, size)
            _native_decode = decode
        else:
            try:
                import lz4.block
                _native_decode = lambda block: lz4.block.decompress(block, uncompressed_size=8 << 20)
            except ImportError:
                _native_decode = block_decode_python
    return _native_decode(data)


def block_decode_python(data):
    out = bytearray(); at = 0
    while at < len(data):
        token = data[at]; at += 1
        length = token >> 4
        if length == 15:
            while True:
                extra = data[at]; at += 1; length += extra
                if extra != 255: break
        if at + length > len(data): raise ValueError('Truncated LZ4 literals')
        out.extend(data[at:at+length]); at += length
        if at == len(data): break
        offset = int.from_bytes(data[at:at+2], 'little'); at += 2
        if not offset or offset > len(out): raise ValueError('Invalid LZ4 offset')
        length = (token & 15) + 4
        if token & 15 == 15:
            while True:
                extra = data[at]; at += 1; length += extra
                if extra != 255: break
        if len(out) + length > 8 << 20: raise ValueError('Oversized LZ4 block')
        start = len(out)-offset
        pattern = bytes(out[start:start+min(offset, length)])
        out.extend((pattern*((length+len(pattern)-1)//len(pattern)))[:length])
    return bytes(out)


def archives(ram):
    at = 0
    while at < len(ram):
        if ram[at:at+4] != MAGIC: raise ValueError('Unsupported boot ramdisk compression')
        at += 4; out = bytearray()
        while at < len(ram) and ram[at:at+4] != MAGIC:
            if at + 4 > len(ram): raise ValueError('Truncated LZ4 header')
            size = struct.unpack_from('<I', ram, at)[0]; at += 4
            if not size: break
            if size > 9 << 20 or at + size > len(ram): raise ValueError('Invalid LZ4 block')
            out.extend(block_decode(bytes(ram[at:at+size]))); at += size
            if len(out) > 192 << 20: raise ValueError('Oversized boot ramdisk')
        yield bytes(out)


def entries(data):
    at = 0
    while at < len(data):
        if data[at:at+6] not in (b'070701', b'070702') or at+110 > len(data):
            if not data[at:].strip(b'\0'): return
            raise ValueError('Invalid boot CPIO')
        fields = [int(data[at+6+i*8:at+14+i*8], 16) for i in range(13)]
        size, namesize = fields[6], fields[11]; end = at+110+namesize
        if not namesize or end > len(data) or data[end-1] != 0: raise ValueError('Invalid CPIO name')
        name = data[at+110:end-1].decode(); at = (end+3)&~3
        if at+size > len(data): raise ValueError('Truncated CPIO file')
        if name == 'TRAILER!!!': return
        yield name.removeprefix('./'), data[at:at+size], fields[1]
        at = (at+size+3)&~3


def overlay(files):
    def record(name, data, mode, ino):
        name = name.encode()+b'\0'
        fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name), 0]
        header = b'070701'+b''.join(f'{n:08x}'.encode() for n in fields)+name
        return header+b'\0'*(-len(header)%4)+data+b'\0'*(-len(data)%4)
    directories = set()
    for name in files:
        parts = name.split('/')
        directories.update('/'.join(parts[:i]) for i in range(1, len(parts)))
    data = b''; ino = 1
    for name in sorted(directories, key=lambda n:(n.count('/'), n)):
        data += record(name, b'', stat.S_IFDIR|0o755, ino); ino += 1
    for name, value in files.items():
        content, mode = value
        data += record(name, content, mode, ino); ino += 1
    data += record('TRAILER!!!', b'', 0, ino)
    compressed = bytearray(MAGIC)
    for at in range(0, len(data), 8 << 20):
        chunk = data[at:at+(8 << 20)]; length = len(chunk)
        block = bytearray([min(length, 15) << 4])
        if length >= 15:
            extra = length-15
            block.extend(b'\xff'*(extra//255)); block.append(extra%255)
        block.extend(chunk)
        compressed.extend(struct.pack('<I', len(block))+block)
    return bytes(compressed)


def _layout(header, footer, length, family):
    if (family not in ('e5', 'mu300') or length != 64 << 20 or header[:8] != b'ANDROID!' or
            len(header) < 4096 or len(footer) != 64 or struct.unpack_from('<I', header, 40)[0] != 4 or footer[:4] != b'AVBf'):
        raise ValueError('Invalid boot v4 image')
    kernel, size = struct.unpack_from('<II', header, 8)
    start = 4096+((kernel+4095)&~4095)
    original, avb, avb_size = struct.unpack_from('>QQQ', footer, 12)
    limit = (56 if family == 'e5' else 48) << 20
    if not kernel or not size or start+size > avb or avb+avb_size > limit or original != avb:
        raise ValueError('Invalid boot payload bounds')
    return start, size, avb, avb_size, limit


def layout(image, family):
    return _layout(image[:4096], image[-64:], len(image), family)


def check_file(path, family):
    with Path(path).open('rb') as stream:
        stream.seek(0, os.SEEK_END); length = stream.tell()
        stream.seek(0); header = stream.read(4096)
        stream.seek(-64, os.SEEK_END); footer = stream.read(64)
    return _layout(header, footer, length, family)


def _frames(ram):
    at = 0
    while at < len(ram):
        if ram[at:at+4] != MAGIC: raise ValueError('Unsupported boot ramdisk compression')
        at += 4; blocks = []
        while at < len(ram) and ram[at:at+4] != MAGIC:
            if at+4 > len(ram): raise ValueError('Truncated LZ4 header')
            size = struct.unpack_from('<I', ram, at)[0]; at += 4
            if not size: break
            if size > 9 << 20 or at+size > len(ram): raise ValueError('Invalid LZ4 block')
            blocks.append((at, size)); at += size
        yield blocks


class _BlockStream:
    def __init__(self, blocks):
        self.blocks = iter(blocks); self.buffer = memoryview(b''); self.at = 0; self.position = 0

    def take(self, length, keep=True, optional=False):
        remaining = length; result = []
        while remaining:
            if self.at == len(self.buffer):
                try: self.buffer = memoryview(next(self.blocks)); self.at = 0
                except StopIteration:
                    if optional and remaining == length: return None
                    raise ValueError('Truncated CPIO stream')
            count = min(remaining, len(self.buffer)-self.at)
            if keep: result.append(bytes(self.buffer[self.at:self.at+count]))
            self.at += count; self.position += count; remaining -= count
        return b''.join(result) if keep else None


def metadata(image, family, wanted):
    start, size, *_ = layout(image, family)
    ram = memoryview(image)[start:start+size]; result = {}
    try:
        for blocks in _frames(ram):
            stream = _BlockStream(block_decode(bytes(ram[at:at+size])) for at,size in blocks)
            while True:
                header = stream.take(110, optional=True)
                if header is None or not header.strip(b'\0'): break
                if header[:6] not in (b'070701', b'070702'): raise ValueError('Invalid boot CPIO')
                fields = [int(header[6+i*8:14+i*8], 16) for i in range(13)]
                length, namesize = fields[6], fields[11]
                if not 0 < namesize <= 4096: raise ValueError('Invalid CPIO filename')
                raw = stream.take(namesize)
                if raw[-1] != 0: raise ValueError('Invalid CPIO filename')
                name = raw[:-1].decode().removeprefix('./')
                stream.take((-stream.position)%4, keep=False)
                if name == 'TRAILER!!!': break
                if wanted(name): result[name] = (stream.take(length), fields[1])
                else: stream.take(length, keep=False)
                stream.take((-stream.position)%4, keep=False)
    finally:
        ram.release()
    return result


def _role(name):
    return name.startswith('etc/misc-bc-') or name in (
        'etc/e5/android-slot', 'etc/e5/linux-slot', 'etc/e5/modem-slot',
        'etc/mu300/android-slot', 'etc/mu300/linux-slot', 'etc/linux-slot',
        'etc/android-slot', 'lib/firmware/android-firmware-slot')


class Binder:
    """Retain only boot metadata; skip module payloads a decoded block at a time."""
    def __init__(self, current, family):
        self.current = current; self.family = family
        self.fields = metadata(current, family, lambda n: n == 'init' or _role(n))
        if 'init' not in self.fields: raise ValueError('Boot init missing')
        self.init = self.fields['init'][0].decode().replace('\r\n', '\n')
        self.needs_guard = '# unisoc-ota-recovery-v2' not in self.init
        self.roles = {n:v for n,v in self.fields.items() if _role(n)}

    def parts(self, image, original=False):
        start, size, avb, avb_size, limit = layout(image, self.family)
        if original:
            if not self.needs_guard: return None
            init = self.init
        else:
            values = metadata(image, self.family, lambda n:n == 'init')
            if 'init' not in values: raise ValueError('Boot init missing')
            init = values['init'][0].decode().replace('\r\n', '\n')
        if '# unisoc-ota-recovery-v2' not in init:
            anchor = '    log "stage=switch-root root='
            if init.count(anchor) != 1: raise ValueError('Cannot locate boot recovery hook')
            init = init.replace(anchor, HOOK+'\n'+anchor)
        roles = dict(self.roles); roles['init'] = (init.encode(), stat.S_IFREG|0o755)
        extra = overlay(roles)
        header = bytearray(image[:4096]); struct.pack_into('<I', header, 12, size+len(extra))
        header[44:1580] = self.current[44:1580]
        new_avb = (start+size+len(extra)+4095)&~4095
        if new_avb+avb_size > limit: raise ValueError('OTA hook overlaps boot diagnostics')
        footer = bytearray(image[-64:]); struct.pack_into('>QQQ', footer, 12, new_avb, new_avb, avb_size)
        return start+size, extra, header, footer, new_avb, bytes(image[avb:avb+avb_size])

    def prepare_file(self, path, original=False):
        import mmap
        # The staged raw image is rebound in place. It is never the device's
        # boot partition. Only small metadata/overlay bytes are held in RAM.
        with Path(path).open('r+b') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as image:
            parts = self.parts(image, original)
            if parts is None: return False
            end, extra, header, footer, avb, avb_data = parts
            stream.seek(end); stream.write(extra); stream.write(b'\0'*(avb-end-len(extra)))
            stream.write(avb_data)
            stream.seek(0); stream.write(header)
            stream.seek(len(image)-64); stream.write(footer)
            stream.flush(); os.fsync(stream.fileno())
        return True


def bind(image, current, family):
    """Convenience byte-buffer API for desktop/tests; device uses prepare_file."""
    binder = Binder(current, family)
    parts = binder.parts(image, image is current or image == current)
    if parts is None: return current
    end, extra, header, footer, avb, avb_data = parts
    result = bytearray(image)
    result[:4096] = header; result[end:end+len(extra)] = extra
    result[end+len(extra):avb] = b'\0'*(avb-end-len(extra))
    result[avb:avb+len(avb_data)] = avb_data
    limit = (56 if family == 'e5' else 48) << 20
    result[limit:-64] = current[limit:-64]; result[-64:] = footer
    return result


def target(root, family):
    root = Path(root); slots = set()
    for name in (f'etc/{family}/linux-slot', f'run/{family}/linux-slot', 'run/e5linux/linux-slot'):
        path = root/name
        if path.is_file():
            value = path.read_text().strip()
            if value in ('a', 'b'): slots.add(value)
    controls = ('run/e5linux/misc-bc-slot-b-trial.bin', 'etc/e5linux/misc-bc-slot-b-trial.bin') if family == 'e5' else (
        'run/mu300/misc-bc-linux-trial.bin', 'run/mu300/misc-bc-slot-b-trial.bin')
    for name in controls:
        path = root/name
        if path.is_file():
            with path.open('rb') as f: value = f.read(2)
            if value in (b'_a', b'_b'): slots.add(value[1:].decode())
    if len(slots) != 1: raise ValueError('Cannot unambiguously identify Linux boot slot')
    slot = slots.pop()
    for name in (f'etc/{family}/android-slot', f'run/{family}/android-slot'):
        path = root/name
        if path.is_file() and path.read_text().strip() == slot:
            raise ValueError('Android and Linux boot slot declarations conflict')
    def device(path):
        resolved = path.resolve()
        if resolved.is_relative_to(root): return resolved
        if resolved.is_relative_to('/dev'):
            mapped = root/'dev'/resolved.relative_to('/dev')
            if mapped.exists(): return mapped
        raise ValueError('Boot device alias escapes /dev')
    for prefix in ('dev/block/by-name', 'dev/disk/by-partlabel'):
        path = root/prefix/('boot_'+slot)
        if path.exists(): return device(path)
    # OpenWrt may only expose by-name links under /dev/block/platform/...
    for path in (root/'dev').glob('block/platform/**/by-name/boot_'+slot):
        if path.exists(): return device(path)
    for path in (root/'sys/class/block').glob('*/uevent'):
        if ('PARTNAME=boot_'+slot) in path.read_text().splitlines():
            candidate = root/'dev'/path.parent.name
            if candidate.exists(): return candidate
    raise ValueError('Linux boot partition is unavailable')


def write(path, image, family):
    *_, limit = layout(image, family)
    view = memoryview(image)
    try:
        with Path(path).open('r+b', buffering=0) as stream:
            for at in range(0, limit, 1 << 20):
                chunk = view[at:min(at+(1 << 20),limit)]
                while chunk:
                    count = stream.write(chunk)
                    if not count: raise OSError('Short boot write')
                    chunk = chunk[count:]
                del chunk
            stream.seek(len(image)-64); stream.write(view[-64:]); os.fsync(stream.fileno())
            stream.seek(0)
            for at in range(0, limit, 1 << 20):
                if stream.read(min(1 << 20, limit-at)) != view[at:min(at+(1 << 20), limit)]:
                    raise ValueError('Boot readback mismatch')
            stream.seek(len(image)-64)
            if stream.read(64) != view[-64:]: raise ValueError('Boot footer readback mismatch')
    finally:
        if 'chunk' in locals(): chunk.release()
        view.release()


def write_file(device, image_path, family):
    import mmap
    with Path(image_path).open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as image:
        write(device, image, family)


def signature(path, family):
    *_, limit = check_file(path, family)
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        remaining = limit
        while remaining:
            block = stream.read(min(1 << 20, remaining))
            if not block: raise ValueError('Truncated boot partition')
            result.update(block); remaining -= len(block)
        stream.seek(-64, os.SEEK_END); result.update(stream.read(64))
    return result.hexdigest()
