#!/usr/bin/env python3
"""Shrink or grow the last GPT partition, to make room for the Linux region on small eMMC variants.

    resize-last-partition.py GPT_DUMP --name userdata --end-sector N   [--out NEW_GPT]
    resize-last-partition.py GPT_DUMP --name userdata --fill           [--out NEW_GPT]
    resize-last-partition.py GPT_DUMP --name userdata --show

GPT_DUMP is the first 34 sectors of the disk plus the last 33, as taken by install.sh; --out writes the same
two pieces back with one partition entry changed, for the caller to write to the device.

Only the **last** partition can be resized, and the tool refuses if the named one is not it. That is the whole
safety argument: no other entry moves, so every other partition keeps its offset and the AVB descriptors and
the boot chain stay valid. It is also why this is not a general partition editor - moving anything else on
these devices breaks the boot chain and there is no recovery path.

Everything is checked before anything is written: both GPT signatures, both header CRCs, the entries CRC
against the array, that the two headers agree about the layout, and that the new end lands inside the usable
area and leaves the partition non-empty. A failure at any point exits non-zero having written nothing.
"""
import argparse
import binascii
import struct
import sys

SECTOR = 512
SIG = b'EFI PART'


class GPT:
    def __init__(self, head: bytes, tail: bytes, disk_sectors: int):
        self.head = bytearray(head)          # LBA 0 .. 33
        self.tail = bytearray(tail)          # the last 33 LBAs
        self.disk_sectors = disk_sectors
        self._parse()

    def _hdr(self, buf, off):
        (sig, rev, hsize, hcrc, _r, cur, bak, first, last, guid,
         entries_lba, nents, esize, ecrc) = struct.unpack_from('<8sIIII QQQQ16s QIII', buf, off)
        return dict(sig=sig, hsize=hsize, hcrc=hcrc, cur=cur, bak=bak, first=first, last=last,
                    entries_lba=entries_lba, nents=nents, esize=esize, ecrc=ecrc, off=off)

    def _check_hdr(self, buf, off, what):
        h = self._hdr(buf, off)
        if h['sig'] != SIG:
            sys.exit(f'{what}: not a GPT header (signature {h["sig"]!r})')
        raw = bytearray(buf[off:off + h['hsize']])
        struct.pack_into('<I', raw, 16, 0)
        if binascii.crc32(bytes(raw)) & 0xffffffff != h['hcrc']:
            sys.exit(f'{what}: header CRC does not match; refusing to touch this table')
        return h

    def _parse(self):
        self.ph = self._check_hdr(self.head, SECTOR, 'primary header')
        # the backup header sits in the last sector of the disk, which is the last sector of our tail
        self.bh_off = len(self.tail) - SECTOR
        self.bh = self._check_hdr(self.tail, self.bh_off, 'backup header')
        for k in ('first', 'last', 'nents', 'esize'):
            if self.ph[k] != self.bh[k]:
                sys.exit(f'the two GPT headers disagree about {k} ({self.ph[k]} vs {self.bh[k]})')
        if self.ph['cur'] != 1 or self.bh['cur'] != self.disk_sectors - 1:
            sys.exit('the headers are not where a GPT puts them; refusing')

        self.esize = self.ph['esize']
        self.nents = self.ph['nents']
        self.earray_bytes = self.nents * self.esize
        self.p_entries_off = self.ph['entries_lba'] * SECTOR
        if self.p_entries_off + self.earray_bytes > len(self.head):
            sys.exit('the entry array does not fit in the dump; take more sectors')
        # the backup array ends where the backup header begins
        self.b_entries_off = self.bh_off - self.earray_bytes
        if self.b_entries_off < 0:
            sys.exit('the backup entry array does not fit in the dump; take more sectors')
        for off, what in ((self.p_entries_off, 'primary'), (None, 'backup')):
            buf = self.head if off is not None else self.tail
            o = off if off is not None else self.b_entries_off
            crc = binascii.crc32(bytes(buf[o:o + self.earray_bytes])) & 0xffffffff
            want = self.ph['ecrc'] if what == 'primary' else self.bh['ecrc']
            if crc != want:
                sys.exit(f'{what} entry array CRC does not match; refusing to touch this table')

    def entries(self):
        out = []
        for i in range(self.nents):
            o = self.p_entries_off + i * self.esize
            tguid = bytes(self.head[o:o + 16])
            if tguid == b'\0' * 16:
                continue
            first, last = struct.unpack_from('<QQ', self.head, o + 32)
            name = bytes(self.head[o + 56:o + 128]).decode('utf-16-le', 'replace').rstrip('\0')
            out.append(dict(index=i, first=first, last=last, name=name))
        return out

    def find_last(self):
        es = self.entries()
        if not es:
            sys.exit('no partitions in this table')
        return max(es, key=lambda e: e['last'])

    def set_end(self, index, new_last):
        for buf, base in ((self.head, self.p_entries_off), (self.tail, self.b_entries_off)):
            struct.pack_into('<Q', buf, base + index * self.esize + 40, new_last)
        # entry array CRC, then each header's own CRC
        ecrc = binascii.crc32(bytes(self.head[self.p_entries_off:self.p_entries_off + self.earray_bytes]))
        ecrc &= 0xffffffff
        bcrc = binascii.crc32(bytes(self.tail[self.b_entries_off:self.b_entries_off + self.earray_bytes]))
        bcrc &= 0xffffffff
        if ecrc != bcrc:
            sys.exit('internal error: the two entry arrays differ after the edit')
        for buf, off, hsize in ((self.head, SECTOR, self.ph['hsize']), (self.tail, self.bh_off, self.bh['hsize'])):
            struct.pack_into('<I', buf, off + 88, ecrc)
            struct.pack_into('<I', buf, off + 16, 0)
            crc = binascii.crc32(bytes(buf[off:off + hsize])) & 0xffffffff
            struct.pack_into('<I', buf, off + 16, crc)


def gib(sectors):
    return f'{sectors * SECTOR / 1073741824:.2f} GiB'


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('dump', help='head.bin,tail.bin or a single file holding head+tail')
    ap.add_argument('--head-sectors', type=int, default=34)
    ap.add_argument('--tail-sectors', type=int, default=33)
    ap.add_argument('--disk-sectors', type=int, required=True)
    ap.add_argument('--name', required=True, help='the partition to resize; must be the last one')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--show', action='store_true')
    g.add_argument('--end-sector', type=int, help='new last sector, inclusive')
    g.add_argument('--fill', action='store_true', help='grow it back to the end of the usable area')
    ap.add_argument('--out', help='write the modified head and tail here as OUT.head / OUT.tail')
    a = ap.parse_args()

    head_path, tail_path = a.dump.split(',', 1)
    head = open(head_path, 'rb').read()
    tail = open(tail_path, 'rb').read()
    if len(head) < a.head_sectors * SECTOR or len(tail) < a.tail_sectors * SECTOR:
        sys.exit('the dump is shorter than the sector counts given')

    g = GPT(head, tail, a.disk_sectors)
    last = g.find_last()
    if last['name'] != a.name:
        sys.exit(f'the last partition is {last["name"]!r}, not {a.name!r}: refusing '
                 '(only the last one can be resized without moving anything else)')

    usable_end = g.ph['last']
    if a.show:
        print(f'disk            {a.disk_sectors} sectors ({gib(a.disk_sectors)})')
        print(f'usable          {g.ph["first"]}..{usable_end}')
        print(f'last partition  {last["name"]}  {last["first"]}..{last["last"]}  ({gib(last["last"] - last["first"] + 1)})')
        print(f'free after it   {gib(usable_end - last["last"])}')
        print(f'LAST_NAME={last["name"]}')
        print(f'LAST_FIRST={last["first"]}')
        print(f'LAST_END={last["last"]}')
        print(f'USABLE_END={usable_end}')
        return

    new_end = usable_end if a.fill else a.end_sector
    if new_end > usable_end:
        sys.exit(f'{new_end} is past the end of the usable area ({usable_end})')
    if new_end <= last['first']:
        sys.exit(f'{new_end} would leave {a.name} empty (it starts at {last["first"]})')

    g.set_end(last['index'], new_end)
    print(f'{a.name}: {last["first"]}..{last["last"]} -> {last["first"]}..{new_end} '
          f'({gib(last["last"] - last["first"] + 1)} -> {gib(new_end - last["first"] + 1)}), '
          f'free after it {gib(usable_end - new_end)}')

    if a.out:
        open(a.out + '.head', 'wb').write(bytes(g.head))
        open(a.out + '.tail', 'wb').write(bytes(g.tail))
        # re-parse what we are about to hand over, so a caller never writes a table we cannot read back
        GPT(bytes(g.head), bytes(g.tail), a.disk_sectors)
        print(f'wrote {a.out}.head and {a.out}.tail (verified)')


if __name__ == '__main__':
    main()
