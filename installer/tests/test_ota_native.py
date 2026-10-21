"""Check the tiny decoder's provenance and bounds using temporary host builds."""
import ctypes
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

NATIVE = Path(__file__).resolve().parents[1]/'ota-native'


class NativeArtifactTests(unittest.TestCase):
    def test_arm64_binary_matches_recorded_source(self):
        meta = json.loads((NATIVE/'build.json').read_text())
        binary = (NATIVE/'lz4raw-aarch64.so').read_bytes()
        self.assertEqual(hashlib.sha256(binary).hexdigest(), meta['binary_sha256'])
        self.assertEqual(hashlib.sha256((NATIVE/'lz4raw.c').read_bytes()).hexdigest(), meta['source_sha256'])
        self.assertEqual(binary[:5], b'\x7fELF\x02')
        self.assertEqual(int.from_bytes(binary[18:20], 'little'), 183)

    @unittest.skipUnless(sys.platform == 'linux' and shutil.which('cc'), 'Linux host C decoder regression')
    def test_valid_overlapping_and_malformed_blocks_are_bounded(self):
        with tempfile.TemporaryDirectory(prefix='ota-decoder-') as directory:
            output = Path(directory)/'decoder.so'
            subprocess.run(['cc', '-shared', '-fPIC', '-fno-builtin', '-nostdlib', '-O2',
                            '-o', str(output), str(NATIVE/'lz4raw.c')], check=True)
            library = ctypes.CDLL(str(output)); decode = library.unisoc_lz4_decode
            decode.argtypes = (ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
            decode.restype = ctypes.c_int
            def run(raw, capacity=64):
                buffer = ctypes.create_string_buffer(capacity)
                size = decode(raw, len(raw), buffer, capacity)
                self.assertTrue(-1 <= size <= capacity)
                return size, buffer.raw[:max(size, 0)]
            self.assertEqual(run(b'\x50hello'), (5, b'hello'))
            self.assertEqual(run(b'\x11a\x01\x00'), (6, b'aaaaaa'))
            self.assertEqual(run(b'\x50hello', 4)[0], -1)
            for data in (b'\xf0', b'\x00\x00\x00', b'\x00\x01\x00', b'\x11a\x01'):
                self.assertEqual(run(data)[0], -1)
            rng = random.Random(20261021)
            for _ in range(4000):
                run(bytes(rng.randrange(256) for _ in range(rng.randrange(33))))
