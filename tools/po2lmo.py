#!/usr/bin/env python3
"""LuCI .lmo catalog from a .po file, byte for byte as LuCI's po2lmo (luci-base/src/po2lmo.c, lib/lmo.c, openwrt-25.12).

    python3 tools/po2lmo.py IN.po OUT.lmo

Format: the values, each padded with NUL to a multiple of 4; then the index, one (key_id, val_id, offset, length)
record of big-endian u32 per value, sorted by key_id; then the big-endian u32 offset of the index. key_id is
sfh_hash(msgid); val_id is the plural number + 1, so 1 here. An entry whose msgstr is empty, or hashes the same as
its msgid, is left out. When no value is left, po2lmo writes no file at all (it unlinks the output): compile_po()
returns b'' and the command removes OUT.

As in po2lmo.c, a string keeps its escapes except \\" and \\\\ (\\n stays a backslash and an n). Unlike po2lmo.c, this
refuses what it does not need: msgctxt, msgid_plural, msgstr[n], duplicate msgids and malformed lines.
"""
import os
import sys

LINE_MAX = 4095    # po2lmo.c reads lines with fgets into char[4096]
KEY_MAX = 4095     # and builds the key with snprintf into char[4096]
M32 = 0xffffffff


def sfh_hash(data: bytes) -> int:
    """Paul Hsieh's SuperFastHash as in LuCI's lib/lmo.c, with the initial hash = len(data)."""
    n = len(data)
    if n == 0:
        return 0
    h = n
    rem, i = n & 3, 0

    def get16(j):
        return data[j] | (data[j + 1] << 8)

    def schar(b):
        return b - 256 if b >= 128 else b

    for _ in range(n >> 2):
        h = (h + get16(i)) & M32
        tmp = ((get16(i + 2) << 11) ^ h) & M32
        h = ((h << 16) ^ tmp) & M32
        i += 4
        h = (h + (h >> 11)) & M32
    if rem == 3:
        h = (h + get16(i)) & M32
        h ^= (h << 16) & M32
        h ^= (schar(data[i + 2]) << 18) & M32
        h = (h + (h >> 11)) & M32
    elif rem == 2:
        h = (h + get16(i)) & M32
        h ^= (h << 11) & M32
        h = (h + (h >> 17)) & M32
    elif rem == 1:
        h = (h + schar(data[i])) & M32
        h ^= (h << 10) & M32
        h = (h + (h >> 1)) & M32
    h ^= (h << 3) & M32
    h = (h + (h >> 5)) & M32
    h ^= (h << 4) & M32
    h = (h + (h >> 17)) & M32
    h ^= (h << 25) & M32
    h = (h + (h >> 6)) & M32
    return h


def _string(rest: bytes, lineno: int) -> bytes:
    """The quoted string that makes up `rest`, unescaped as po2lmo.c's extract_string does."""
    def bad(why):
        raise ValueError(f'line {lineno}: {why}')
    if not rest.startswith(b'"'):
        bad('expected a quoted string')
    out, esc = bytearray(), False
    for j in range(1, len(rest)):
        c = rest[j]
        if esc:
            if c not in b'"\\':
                out.append(0x5c)
            out.append(c)
            esc = False
        elif c == 0x5c:
            esc = True
        elif c == 0x22:
            if rest[j + 1:].strip():
                bad('text after the closing quote')
            return bytes(out)
        else:
            out.append(c)
    bad('unterminated string')


def _plural_forms(header: bytes):
    """The Plural-Forms value of a header msgstr, found as po2lmo.c finds it (fields ended by a literal \\n)."""
    start, esc = 0, False
    for p in range(len(header)):
        if esc:
            if header[p] == ord('n'):
                field = header[start:p - 1]
                if field[:14].lower() == b'plural-forms: ':
                    return field[14:]
                start = p + 1
            esc = False
        elif header[p] == 0x5c:
            esc = True
    return None


def compile_po(text: str) -> bytes:
    """The .lmo bytes for the .po `text`; b'' when no value is left. ValueError on what it refuses."""
    entries = []     # (msgid, msgstr, lineno)
    cur = None       # [msgid, msgstr or None, lineno]
    field = None     # 0 = msgid, 1 = msgstr: where a continued line goes

    def finish():
        if cur is not None:
            if cur[1] is None:
                raise ValueError(f'line {cur[2]}: msgid without msgstr')
            entries.append(tuple(cur))

    for lineno, line in enumerate(text.encode('utf-8').split(b'\n'), 1):
        if len(line) + 1 > LINE_MAX:
            raise ValueError(f'line {lineno}: longer than LuCI\'s po2lmo reads ({LINE_MAX} bytes)')
        s = line.strip()
        if not s or s.startswith(b'#'):
            continue
        if s.startswith(b'msgctxt'):
            raise ValueError(f'line {lineno}: msgctxt is not supported')
        if s.startswith(b'msgid_plural') or s.startswith(b'msgstr['):
            raise ValueError(f'line {lineno}: plural forms are not supported')
        if s.startswith(b'msgid '):
            finish()
            cur, field = [_string(s[6:].lstrip(), lineno), None, lineno], 0
        elif s.startswith(b'msgstr '):
            if cur is None or cur[1] is not None:
                raise ValueError(f'line {lineno}: msgstr without its msgid')
            cur[1], field = _string(s[7:].lstrip(), lineno), 1
        elif s.startswith(b'"'):
            if cur is None:
                raise ValueError(f'line {lineno}: continued string outside an entry')
            cur[field] += _string(s, lineno)
        else:
            raise ValueError(f'line {lineno}: not a .po line')
    finish()

    values, index, offset, seen = bytearray(), [], 0, {}

    def add(key_id, val_id, value):
        nonlocal offset
        values.extend(value + b'\0' * (-len(value) % 4))
        index.append((key_id, val_id, offset, len(value)))
        offset += len(value) + (-len(value) % 4)

    for msgid, msgstr, lineno in entries:
        if not msgstr:
            continue
        if not msgid:                              # the header: only a Plural-Forms field is kept, under key 0
            pf = _plural_forms(msgstr)
            if pf is not None:
                add(0, 0, pf)
            continue
        if len(msgid) > KEY_MAX:
            raise ValueError(f'line {lineno}: msgid longer than LuCI\'s po2lmo keys ({KEY_MAX} bytes)')
        key_id = sfh_hash(msgid)
        if key_id == sfh_hash(msgstr):
            continue
        if key_id in seen:                         # qsort is not stable: equal keys would come out in any order
            raise ValueError(f'line {lineno}: msgid duplicates (or hashes as) the one on line {seen[key_id]}')
        seen[key_id] = lineno
        add(key_id, 1, msgstr)

    if offset == 0:
        return b''
    index.sort(key=lambda e: e[0])
    return bytes(values) + b''.join(n.to_bytes(4, 'big') for e in index for n in e) + offset.to_bytes(4, 'big')


def main(argv):
    if len(argv) != 3:
        print(f'Usage: {argv[0]} input.po output.lmo', file=sys.stderr)
        return 1
    try:
        with open(argv[1], encoding='utf-8') as f:
            lmo = compile_po(f.read())
    except (OSError, UnicodeDecodeError, ValueError) as e:
        print(f'Error: {argv[1]}: {e}', file=sys.stderr)
        return 1
    if not lmo:                                    # as po2lmo: an empty catalog is no file
        if os.path.isfile(argv[2]):
            os.unlink(argv[2])
        return 0
    with open(argv[2], 'wb') as f:
        f.write(lmo)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
