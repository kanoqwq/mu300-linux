#!/usr/bin/env python3
"""Build the DSP profile blobs the kernel loads from /lib/firmware, from the vendor's parameter XML.

    tools/vbc-profile/vbc-profile.py dsp_vbc.xml audio_structure.xml cvs.xml -o out/

The XML is vendor parameter data and does not live in this repo; point this at a copy you already have.
"""
import argparse
import pathlib
import sys
import xml.etree.ElementTree as ET

MAGIC = b"audio_profile"
HEADER_LEN = 24


def width_bits(t):
    """'u16' -> 16, 't12288' -> 12288, and '16' too, which three fields in audio_structure.xml spell that way."""
    s = t.lstrip("ut")
    if not s.isdigit():
        raise ValueError(f"unparsable type {t!r}")
    return int(s)


def num(s):
    return int(s, 0)


def fields(node, inherited=None):
    """Yield (byte_offset, word_bits, bit_offset, bit_count, value) for every field under a mode."""
    own = inherited
    if "id" in node.attrib and "t" in node.attrib:
        own = (num(node.get("id")), width_bits(node.get("t")))

    children = list(node)
    if children:
        for child in children:
            yield from fields(child, own)
        return

    if "val" not in node.attrib:
        return
    if own is None:
        raise ValueError(f"<{node.tag}> has a value but no id/t, and no ancestor supplies them")
    byte_off, word_bits = own
    yield (byte_off, word_bits,
           num(node.get("offset", "0")),
           num(node.get("bits", str(word_bits))),
           num(node.get("val")))


def parse(path):
    """Parse without a DTD, so neither external entities nor nested-entity expansion are on the table.

    These files come from a vendor firmware dump rather than from this repo, and a parameter table has no
    business declaring a doctype - refusing one costs nothing and removes the whole class of problem without
    pulling in defusedxml.
    """
    head = path.open("rb").read(4096)
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ValueError(f"{path.name}: has a DTD; parameter XML should not, refusing to parse it")
    return ET.parse(path).getroot()


def build(path):
    root = parse(path)
    num_mode = num(root.get("num_mode"))
    len_mode = num(root.get("struct_size"))

    modes = {}
    for el in root.iter():
        if "mode" not in el.attrib:
            continue
        idx = num(el.get("mode"))
        if idx in modes:
            raise ValueError(f"mode {idx} appears twice")
        modes[idx] = el
    missing = [i for i in range(num_mode) if i not in modes]
    if missing:
        raise ValueError(f"{path.name}: num_mode is {num_mode} but modes {missing[:5]} are missing")
    if len(modes) != num_mode:
        raise ValueError(f"{path.name}: {len(modes)} mode elements for num_mode {num_mode}")

    data = bytearray(num_mode * len_mode)
    written = overflow = misplaced = 0
    for i in range(num_mode):
        for byte_off, word_bits, bit_off, bits, val in fields(modes[i]):
            word_bytes = (word_bits + 7) // 8
            if byte_off + word_bytes > len(data):
                overflow += 1
                continue
            # the offsets are absolute, so which mode a field lands in is implied rather than stated:
            # checking it against the element's own mode= is a free test of that reading
            if byte_off // len_mode != i:
                misplaced += 1
            word = int.from_bytes(data[byte_off:byte_off + word_bytes], "little")
            mask = ((1 << bits) - 1) << bit_off
            word = (word & ~mask) | ((val << bit_off) & mask)
            data[byte_off:byte_off + word_bytes] = word.to_bytes(word_bytes, "little")
            written += 1

    if overflow:
        print(f"  {path.name}: {overflow} field(s) past the end of the array, skipped", file=sys.stderr)
    if misplaced:
        print(f"  {path.name}: {misplaced} field(s) landed outside the mode that declares them", file=sys.stderr)

    out = MAGIC.ljust(16, b"\0") + num_mode.to_bytes(4, "little") + len_mode.to_bytes(4, "little") + bytes(data)
    return root.tag, num_mode, len_mode, written, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xml", nargs="+", type=pathlib.Path)
    ap.add_argument("-o", "--out", type=pathlib.Path, default=pathlib.Path("."),
                    help="directory to write the blobs into (default: .)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    for path in args.xml:
        name, num_mode, len_mode, written, blob = build(path)
        dst = args.out / name
        dst.write_bytes(blob)
        print(f"{dst}: {num_mode} modes x {len_mode:#x} bytes, {written} fields, {len(blob)} bytes total")


if __name__ == "__main__":
    main()
