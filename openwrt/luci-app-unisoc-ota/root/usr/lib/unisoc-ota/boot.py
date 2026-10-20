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
            import ctypes
            library = ctypes.CDLL(str(helper))
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


def layout(image, family):
    if (family not in ('e5', 'mu300') or len(image) != 64 << 20 or image[:8] != b'ANDROID!' or
            struct.unpack_from('<I', image, 40)[0] != 4 or image[-64:-60] != b'AVBf'):
        raise ValueError('Invalid boot v4 image')
    kernel, size = struct.unpack_from('<II', image, 8)
    start = 4096+((kernel+4095)&~4095)
    original, avb, avb_size = struct.unpack_from('>QQQ', image, len(image)-52)
    limit = (56 if family == 'e5' else 48) << 20
    if not kernel or not size or start+size > avb or avb+avb_size > limit or original != avb:
        raise ValueError('Invalid boot payload bounds')
    return start, size, avb, avb_size, limit


def bind(image, current, family):
    start, size, avb, avb_size, limit = layout(image, family)
    old_start, old_size, *_ = layout(current, family)
    roles = {}; init = None
    for archive in archives(current[old_start:old_start+old_size]):
        for name, content, mode in entries(archive):
            if (name.startswith('etc/misc-bc-') or name in ('etc/e5/android-slot', 'etc/e5/linux-slot',
                    'etc/e5/modem-slot', 'etc/mu300/android-slot', 'etc/mu300/linux-slot',
                    'etc/linux-slot', 'etc/android-slot', 'lib/firmware/android-firmware-slot')):
                roles[name] = (content, mode)
    for archive in archives(image[start:start+size]):
        for name, content, mode in entries(archive):
            if name == 'init': init = content.decode().replace('\r\n', '\n')
    if not init: raise ValueError('Boot init missing')
    if image == current and '# unisoc-ota-recovery-v2' in init:
        return current
    if '# unisoc-ota-recovery-v2' not in init:
        anchor = '    log "stage=switch-root root='
        if init.count(anchor) != 1: raise ValueError('Cannot locate boot recovery hook')
        init = init.replace(anchor, HOOK+'\n'+anchor)
    roles['init'] = (init.encode(), stat.S_IFREG|0o755)
    ram = image[start:start+size]+overlay(roles)
    header = bytearray(image[:4096]); struct.pack_into('<I', header, 12, len(ram))
    # Keep the installed root locator and model's explicit boot parameters.
    header[44:1580] = current[44:1580]
    new_avb = (start+len(ram)+4095)&~4095
    if new_avb+avb_size > limit: raise ValueError('OTA hook overlaps boot diagnostics')
    footer = bytearray(image[-64:]); struct.pack_into('>QQQ', footer, 12, new_avb, new_avb, avb_size)
    # Diagnostic bytes belong to the device, not to the release image.
    result = bytearray(len(image))
    result[:4096] = header
    result[4096:start] = memoryview(image)[4096:start]
    result[start:start+len(ram)] = ram
    result[new_avb:new_avb+avb_size] = memoryview(image)[avb:avb+avb_size]
    result[limit:-64] = memoryview(current)[limit:-64]
    result[-64:] = footer
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
    with Path(path).open('r+b', buffering=0) as stream:
        stream.write(view[:limit]); stream.seek(len(image)-64); stream.write(view[-64:]); os.fsync(stream.fileno())
        stream.seek(0)
        for at in range(0, limit, 1 << 20):
            if stream.read(min(1 << 20, limit-at)) != view[at:min(at+(1 << 20), limit)]:
                raise ValueError('Boot readback mismatch')
        stream.seek(len(image)-64)
        if stream.read(64) != view[-64:]: raise ValueError('Boot footer readback mismatch')
