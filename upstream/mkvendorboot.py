#!/usr/bin/env python3
"""Rebuild a vendor_boot (header v4) image with a different DTB for mainline bring-up on the MU300.

The DTB section of Unisoc vendor_boot is an Android DT table (magic 0xd7b7ab1e) with one entry; LK selects the
entry by the DTB's sprd,sc-id property. The vendor ramdisk, the ramdisk table and the header fields are kept;
the AVB footer layout (hash descriptor copied, sizes updated) matches boot/build-boot-image.py.

usage: mkvendorboot.py --stock vendor_boot.img --dtb new.dtb --out vendor_boot-new.img
"""
import argparse, struct, sys

DT_TABLE_MAGIC = 0xd7b7ab1e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stock', required=True)
    ap.add_argument('--dtb', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    b = open(a.stock, 'rb').read()
    if b[:8] != b'VNDRBOOT' or struct.unpack_from('<I', b, 8)[0] != 4:
        sys.exit('not a vendor_boot v4 image')
    page, = struct.unpack_from('<I', b, 12)
    vr_size, = struct.unpack_from('<I', b, 24)
    hsize, dtb_size = struct.unpack_from('<II', b, 2096)
    rt_size, rt_num, rt_esize, bc_size = struct.unpack_from('<IIII', b, 2112)
    al = lambda x: (x + page - 1) // page * page
    o_vr = al(hsize); o_dtb = o_vr + al(vr_size); o_tab = o_dtb + al(dtb_size); o_bc = o_tab + al(rt_size)

    old = b[o_dtb:o_dtb + dtb_size]
    magic, total, h_size, e_size, e_count, e_off, t_page, t_ver = struct.unpack_from('>8I', old, 0)
    if magic != DT_TABLE_MAGIC or e_count < 1:
        sys.exit('unexpected DTB section format')
    entry = bytearray(old[e_off:e_off + e_size])            # dt_size, dt_offset, id, rev, custom[4]

    dtb = open(a.dtb, 'rb').read()
    if dtb[:4] != bytes.fromhex('d00dfeed'):
        sys.exit('--dtb is not a flattened device tree')
    hdr_len = h_size
    new_e_off = hdr_len
    dt_off = new_e_off + e_size
    struct.pack_into('>II', entry, 0, len(dtb), dt_off)
    table = bytearray(struct.pack('>8I', DT_TABLE_MAGIC, dt_off + len(dtb), hdr_len, e_size, 1, new_e_off, t_page, t_ver))
    table += b'\0' * (hdr_len - len(table))
    table += entry + dtb

    hdr = bytearray(b[:o_vr])
    struct.pack_into('<I', hdr, 2100, len(table))
    body = bytes(hdr) + b[o_vr:o_dtb] + bytes(table) + b'\0' * (al(len(table)) - len(table)) \
        + b[o_tab:o_tab + al(rt_size)] + b[o_bc:o_bc + al(bc_size)]
    orig_size = len(body)
    avb_off, avb_size = struct.unpack_from('>QQ', b, len(b) - 44)
    body += b[avb_off:avb_off + avb_size]
    body += b'\0' * (len(b) - 64 - len(body))
    footer = bytearray(b[-64:])
    struct.pack_into('>QQQ', footer, 12, orig_size, orig_size, avb_size)
    out = body + bytes(footer)
    assert len(out) == len(b)
    open(a.out, 'wb').write(out)
    print(f'{a.out}: dtb table {len(table)} bytes (was {dtb_size}), entry id/rev kept')


if __name__ == '__main__':
    main()
