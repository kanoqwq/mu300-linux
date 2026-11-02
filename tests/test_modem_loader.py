"""Relocated/variant ELF fixtures and real-stock oracle; no hardware access."""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
SOURCE = TOP / 'tools/modem-loader/modem-loader-patch.c'


def compiler():
    return shutil.which('cc') or shutil.which('gcc')


def build_host(output):
    subprocess.run([compiler(), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                    str(SOURCE), '-o', str(output)], check=True, capture_output=True)


def fixture(*, base=0x20000, code_file=0x1000, code_va=0x4000, saved=21,
            text=11, word=12, byte=13, result=14, stack=48, nop=False, copies=1,
            frame=False):
    """An independently assembled tiny ELF with file offsets != virtual addresses.

    Only the loader ABI is modeled; this is not a runnable/proprietary firmware.
    """
    data = bytearray(code_file + 0x3000)
    code_va += base
    context_va = base + 0x9000  # BSS: no bytes stored in the file
    struct.pack_into('<16sHHIQQQIHHHHHH', data, 0,
                     b'\x7fELF\x02\x01\x01' + bytes(9), 3, 183, 1, code_va,
                     64, 0, 0, 64, 56, 3, 0, 0, 0)
    for i, row in enumerate(((1, 4, 0, base, base, 0x1000, 0x2000, 0x1000),
                             (1, 5, code_file, code_va, code_va, 0x3000, 0x3000, 0x1000),
                             (1, 6, 0, context_va, context_va, 0, 0x1000, 0x1000))):
        struct.pack_into('<IIQQQQQQ', data, 64+i*56, *row)
    strings = {}
    pos = 0x400
    for s in ('modem', '/dev/sctl_pm', 'modem_has_been_boot', '/proc/cmdline',
              'modem_ctrl_parse_cmdline', 'load_modem_img'):
        # Read-only strings reside in the first PT_LOAD, offset != VA.
        strings[s] = base + pos
        raw = s.encode() + b'\0'
        data[pos:pos+len(raw)] = raw
        pos += len(raw) + 7

    def off(va):
        return code_file + va-code_va

    def emit(at, words):
        for i, w in enumerate(words):
            struct.pack_into('<I', data, off(at)+i*4, w)

    def adr(rd, pc, target):
        delta = ((target & ~4095) - (pc & ~4095)) // 4096
        return 0x90000000 | ((delta & 3) << 29) | (((delta >> 2) & 0x7ffff) << 5) | rd

    def add(rd, rn, imm):
        return 0x91000000 | (imm << 10) | (rn << 5) | rd

    def call(pc, target):
        return 0x94000000 | (((target-pc)//4) & 0x3ffffff)

    def refs(at, names):
        words = []
        for name in names:
            pc = at + 4*len(words)
            words += [adr(0, pc, strings[name]), add(0, 0, strings[name] & 4095)]
        emit(at, words + [0xd65f03c0])

    pm, parser, load, io = [code_va+x for x in (0x800, 0xa00, 0xc00, 0x1100)]
    refs(pm, ('/dev/sctl_pm', 'modem_has_been_boot'))
    refs(parser, ('/proc/cmdline', 'modem_ctrl_parse_cmdline'))
    emit(io, [0xd65f03c0])
    words = [0x2a0003e0 | saved,
             adr(0, load+4, strings['load_modem_img']),
             add(0, 0, strings['load_modem_img'] & 4095)]
    block_offsets = []
    for state in (0, 1):
        for part in range(3):
            pc = load + 4*len(words)
            block_offsets.append(off(pc))
            context = context_va + part*0x100
            if part < 2:
                words += [0x36000000 | ((5 if part else 0) << 19) | (5 << 5) | saved]
            else:
                words += [0x52800148, 0x6a08001f | (saved << 5), 0x540000a0]
            pc = load + 4*len(words)
            words += [adr(1, pc, context), 0x52800000 | (state << 5),
                      add(1, 1, context & 4095), call(pc+12, io)]
    emit(load, words + [0xd65f03c0])
    patches = []
    gates = []
    for copy in range(copies):
        gate = code_va + copy*0x100
        words = [None, 0x2a0003e0 | saved, None, add(0, 0, strings['modem'] & 4095),
                 add(1, 31, stack), None,
                 0xd2800000 | (0x6873 << 5) | text,
                 0xf9400000 | ((stack//8) << 10) | (31 << 5) | word,
                 0xf2a00000 | (0x7475 << 5) | text,
                 0x39400000 | ((stack+8) << 10) | (31 << 5) | byte,
                 0xf2c00000 | (0x6f64 << 5) | text,
                 0xf2e00000 | (0x6e77 << 5) | text,
                 0xca000000 | (text << 16) | (word << 5) | word,
                 0xaa000000 | (byte << 16) | (word << 5) | word,
                 0xf100001f | (word << 5), 0x1a9f07e0 | result,
                 0x2a000000 | (saved << 16) | (result << 5) | result,
                 None, 0x528007e0, None]
        if frame:
            words[4] = 0xd10003a1 | (stack << 10)
            words[7] = 0xf8400000 | ((-stack & 511) << 12) | (29 << 5) | word
            words[9] = 0x38400000 | ((8-stack & 511) << 12) | (29 << 5) | byte
        stride = 8 if nop else 4
        pcs = [gate+i*stride for i in range(20)]
        words[0] = call(pcs[0], pm)
        words[2] = adr(0, pcs[2], strings['modem'])
        words[5] = call(pcs[5], parser)
        words[17] = 0x35000000 | (((pcs[19]+4-pcs[17])//4) << 5) | result
        words[19] = call(pcs[19], load)
        gates.append([off(pc) for pc in pcs])
        for pc, word_value in zip(pcs, words):
            emit(pc, [word_value] + ([0xd503201f] if nop else []))
        patches += [(off(pcs[1]), 0x2a1f03e0 | saved), (off(pcs[18]), 0x52800440)]
    return data, patches, gates, block_offsets


def thumb_fixture(base=0x10000):
    """Thumb gate, independent flag dispatch and ARM PLT/dynamic memcmp binding."""
    data = bytearray(0x4000)
    code, context = base+0x4000, base+0x9000
    struct.pack_into('<16sHHIIIIIHHHHHH', data, 0, b'\x7fELF\x01\x01\x01'+bytes(9),
                     3, 40, 1, code|1, 52, 0, 0, 52, 32, 4, 0, 0, 0)
    for i, row in enumerate(((1, 0, base, base, 0x1000, 0x1000, 4, 0x1000),
                             (1, 0x1000, code, code, 0x3000, 0x3000, 5, 0x1000),
                             (1, 0, context, context, 0, 0x1000, 6, 0x1000),
                             (2, 0x200, base+0x200, base+0x200, 64, 64, 4, 4))):
        struct.pack_into('<IIIIIIII', data, 52+i*32, *row)
    strings = {}
    pos = 0x400
    for s in ('modem', 'shutdown', '/dev/sctl_pm', 'modem_has_been_boot',
              '/proc/cmdline', 'modem_ctrl_parse_cmdline', 'load_modem_img'):
        strings[s] = base+pos
        raw = s.encode()+b'\0'; data[pos:pos+len(raw)] = raw; pos += len(raw)+3
    pm, parser, load, io, dummy, plt = [code+x for x in (0x800, 0xa00, 0xc00, 0x1400, 0x1500, 0x1600)]
    got = context+0x20
    for i, row in enumerate(((2, 8), (5, base+0x700), (6, base+0x900), (10, 8),
                             (11, 16), (20, 17), (23, base+0x800), (0, 0))):
        struct.pack_into('<II', data, 0x200+i*8, *row)
    data[0x700:0x708] = b'\0memcmp\0'
    struct.pack_into('<II', data, 0x800, got, (1 << 8)|22)
    struct.pack_into('<I', data, 0x910, 1)

    def off(va): return 0x1000+va-code

    def half(pc, w): struct.pack_into('<H', data, off(pc), w)

    def call(pc, target, exchange=False):
        v = (target-((pc+4)&~3 if exchange else pc+4)) & 0x1ffffff
        s, i1, i2 = (v >> 24)&1, (v >> 23)&1, (v >> 22)&1
        a = 0xf000 | (s << 10) | ((v >> 12)&1023)
        b = (0xc000 if exchange else 0xd000) | ((1 ^ i1 ^ s) << 13) | ((1 ^ i2 ^ s) << 11) | ((v >> 1)&2047)
        return a | (b << 16)

    def reference(pc, plus_pc, reg, value, pool):
        distance = pool-((pc+4)&~3)
        assert 0 <= distance <= 1020 and distance % 4 == 0
        half(pc, 0x4800 | (reg << 8) | (distance//4))
        half(plus_pc, 0x4478 | reg)
        struct.pack_into('<I', data, off(pool), (value-plus_pc-4)&0xffffffff)

    for at, names in ((pm, ('/dev/sctl_pm', 'modem_has_been_boot')),
                      (parser, ('/proc/cmdline', 'modem_ctrl_parse_cmdline'))):
        for i, s in enumerate(names): reference(at+i*4, at+i*4+2, 0, strings[s], at+0x100+i*4)
        half(at+8, 0xbd10)
    half(io, 0xbd10); half(dummy, 0xbd10)
    pc = load
    gate_words = []

    def emit(w, width=2):
        nonlocal pc
        at = pc
        if code <= at < code+0x100: gate_words.append(off(at))
        struct.pack_into('<I' if width == 4 else '<H', data, off(pc), w)
        pc += width
        return at

    emit(0x4606)  # original flags = r0
    emit(0x040af026, 4)  # effective r4 = r6 & ~10
    at = pc; emit(call(at, dummy), 4)
    emit(0x2800); emit(0xbf08); emit(0x4634)
    at = pc; emit(0); plus = pc; emit(0)
    reference(at, plus, 5, strings['load_modem_img'], load+0x200)
    for state in (0, 1):
        for part in range(3):
            if part == 2: emit(0x0f0af014, 4); emit(0xd004)
            elif state:
                emit(0x07e0 if part == 0 else 0x06a0)
                emit(0xd004 if part == 0 else 0xd504)
            elif part == 0:
                emit(0x0601f004, 4)
                at = pc; emit(call(at, dummy), 4)
                emit(0xb126)
            else: emit(0x0520f014, 4); emit(0xd004)
            at = pc; emit(0); emit(0x2000|state); plus = pc; emit(0)
            reference(at, plus, 1, context+part*0x100, load+0x204+(state*3+part)*4)
            at = pc; emit(call(at, io), 4)
    emit(0xbd70)
    struct.pack_into('<III', data, off(plt), 0xe28fc600, 0xe28cca03,
                     0xe5bcf000 | (got-plt-8-0x3000))
    pc = code
    at = pc; emit(call(at, pm), 4)
    gate = off(emit(0x4604))
    ldr_modem = emit(0); emit(0xad04); emit(0x4629); add_modem = emit(0)
    at = pc; emit(call(at, parser), 4)
    ldr_shutdown = emit(0); emit(0x4628); emit(0x2209); add_shutdown = emit(0)
    at = pc; emit(call(at, plt, exchange=True), 4)
    emit(0x4320); emit(0xd102); mask = off(emit(0x203f))
    at = pc; emit(call(at, load), 4)
    reference(ldr_modem, add_modem, 0, strings['modem'], code+0x100)
    reference(ldr_shutdown, add_shutdown, 1, strings['shutdown'], code+0x104)
    return data, ((gate, 0x2400), (mask, 0x2022)), gate_words


@unittest.skipUnless(compiler(), 'host C compiler unavailable')
class LoaderABI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='mu300-loader-')
        cls.root = Path(cls.temp.name)
        cls.helper = cls.root/'helper'
        build_host(cls.helper)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_loader(self, data, action='patch', destination=None):
        source = self.root/'source'
        source.write_bytes(data)
        dest = destination or self.root/'ram'
        cmd = [str(self.helper), action, str(source)]
        if action == 'patch':
            cmd += [str(dest)]
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        self.assertEqual(source.read_bytes(), data, 'stock input was modified')
        return result, dest

    def accepted(self, data, patches):
        expected = bytearray(data)
        for at, word in patches:
            struct.pack_into('<I', expected, at, word)
        result, dest = self.run_loader(data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dest.read_bytes(), expected)
        return expected

    def rejected(self, data):
        dest = self.root/'preserved'
        dest.write_bytes(b'existing output must survive failed inspection')
        result, _ = self.run_loader(data, destination=dest)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(dest.read_bytes(), b'existing output must survive failed inspection')

    def test_relocations_registers_stack_and_nops(self):
        for options in ({}, {'base': 0x400000, 'code_file': 0x2000, 'code_va': 0x6000},
                        {'frame': True, 'stack': 24},
                        {'saved': 27, 'text': 8, 'word': 10, 'byte': 11, 'result': 9,
                         'stack': 80, 'nop': True}):
            with self.subTest(options=options):
                data, patches, _, _ = fixture(**options)
                self.accepted(data, patches)

    def test_unrelated_version_bytes_do_not_change_acceptance(self):
        data, patches, _, _ = fixture()
        self.accepted(data, patches)
        for version in (b'U30Air-test-build-1', b'U30Air-different-build-2027'):
            data[0x700:0x700+len(version)] = version
            self.accepted(data, patches)

    def test_thumb_relocation_and_dynamic_compare_binding(self):
        for base in (0x10000, 0x300000):
            data, patches, _ = thumb_fixture(base)
            expected = bytearray(data)
            for at, word in patches: struct.pack_into('<H', expected, at, word)
            result, dest = self.run_loader(data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(dest.read_bytes(), expected)
            data[0x701:0x707] = b'strcmp'
            self.rejected(data)  # Gate must use a verified 9-byte memcmp, not another import.

    def test_thumb_gate_and_dynamic_binding_mutations_rejected(self):
        data, _, gate = thumb_fixture()
        for at in gate:
            with self.subTest(instruction=hex(at)):
                broken = bytearray(data)
                struct.pack_into('<H', broken, at, 0xbe00)  # BKPT
                self.rejected(broken)
        for at in (0x200, 0x800, 0x804, 0x910):
            broken = bytearray(data)
            struct.pack_into('<I', broken, at, 0xffffffff)
            self.rejected(broken)

    def test_inspect_does_not_create_output(self):
        data, _, _, _ = fixture()
        output = self.root/'not-created'
        output.unlink(missing_ok=True)
        result, _ = self.run_loader(data, action='inspect', destination=output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(output.exists())

    def test_every_gate_instruction_is_checked(self):
        data, _, gates, _ = fixture()
        for index, at in enumerate(gates[0]):
            with self.subTest(instruction=index):
                broken = bytearray(data)
                struct.pack_into('<I', broken, at, 0xd4200000)  # BRK is not this ABI
                self.rejected(broken)

    def test_dispatch_bits_masks_context_and_stop_start_are_checked(self):
        data, _, _, blocks = fixture()
        for index, at in enumerate(blocks):
            with self.subTest(block=index):
                broken = bytearray(data)
                struct.pack_into('<I', broken, at, 0xd503201f)
                self.rejected(broken)
        # Stop and start must use the same context and ioctl helper.
        for at in (blocks[3]+12, blocks[3]+16):
            broken = bytearray(data)
            word = struct.unpack_from('<I', broken, at)[0]
            struct.pack_into('<I', broken, at, word ^ 0x400)
            self.rejected(broken)

    def test_ambiguity_and_missing_anchors_rejected(self):
        self.rejected(fixture(copies=2)[0])
        data = fixture()[0]
        for anchor in (b'/dev/sctl_pm', b'/proc/cmdline', b'load_modem_img'):
            broken = data.replace(anchor, b'x'*len(anchor))
            self.rejected(broken)

    def test_malformed_headers_segments_and_truncation(self):
        data = fixture()[0]
        for length in (0, 63, 65, 100, 4096):
            self.rejected(data[:length])
        for at, raw in ((5, b'\x02'), (18, b'\x3e\x00'), (54, b'\xff\xff'),
                        (32, b'\xff'*8), (64+56+32, b'\xff'*8),
                        (64+56+16, struct.pack('<Q', 0x20000))):
            broken = bytearray(data)
            broken[at:at+len(raw)] = raw
            self.rejected(broken)

    def test_source_output_alias_and_symlink_rejected(self):
        data = fixture()[0]
        self.assertNotEqual(self.run_loader(data, destination=self.root/'source')[0].returncode, 0)
        target = self.root/'symlink-target'
        target.write_bytes(b'keep me')
        link = self.root/'symlink'
        link.unlink(missing_ok=True)
        link.symlink_to(target)
        self.assertNotEqual(self.run_loader(data, destination=link)[0].returncode, 0)
        self.assertEqual(target.read_bytes(), b'keep me')

    def test_actual_stock_matches_preexisting_oracle(self):
        path = os.environ.get('MU300_COLD_LOADER_TEST_FILE')
        if not path:
            self.skipTest('proprietary stock fixture not distributed')
        data = Path(path).read_bytes()
        expected = self.accepted(data, [(0xcf0c, 0x2a1f03f3), (0xcf50, 0x52800440)])
        self.assertEqual(hashlib.sha256(expected).hexdigest(),
                         '9e3490f222a312e649dd5273df216b87ca9880a621ea68a0a73df0aa6b37c2ae')


if __name__ == '__main__':
    unittest.main()
