"""tools/po2lmo.py against catalogs that LuCI's own po2lmo produced (tests/fixtures/po2lmo, from kanoqwq's fork)."""
import subprocess, sys, tempfile, unittest
from pathlib import Path
from helpers import TOP

FIX = TOP / 'tests' / 'fixtures' / 'po2lmo'
sys.path.insert(0, str(TOP / 'tools'))
import po2lmo  # noqa: E402


class Po2Lmo(unittest.TestCase):
    def test_byte_for_byte(self):
        for lang in ('tr', 'en'):
            want = (FIX / f'mu300.{lang}.lmo').read_bytes()
            self.assertEqual(po2lmo.compile_po((FIX / f'mu300.{lang}.po').read_text(encoding='utf-8')), want, lang)

    def test_identity_and_empty_are_left_out(self):
        # LuCI's po2lmo writes no file at all when no value is left (offset 0: it unlinks the output).
        out = po2lmo.compile_po('msgid "A"\nmsgstr "A"\n\nmsgid "B"\nmsgstr ""\n')
        self.assertEqual(out, b'')

    def test_escapes_and_continued_lines(self):
        # Only \" and \\ are unescaped (po2lmo.c extract_string); \n stays the two characters backslash, n.
        out = po2lmo.compile_po('msgid ""\n"a\\"b"\nmsgstr "x\\\\y"\n"\\n"\n')
        value = b'x\\y\\n'
        self.assertEqual(out[:8], value + b'\0\0\0')
        key = 'a"b'.encode()
        self.assertEqual(out[8:24], b''.join(n.to_bytes(4, 'big') for n in
                                             (po2lmo.sfh_hash(key), 1, 0, len(value))))
        self.assertEqual(out[24:], (8).to_bytes(4, 'big'))

    def test_sfh_hash_signed_tail(self):
        # Tail bytes are signed chars: a UTF-8 byte >= 0x80 is negative there.
        self.assertEqual(po2lmo.sfh_hash(b''), 0)
        self.assertNotEqual(po2lmo.sfh_hash(b'\xc3'), po2lmo.sfh_hash(b'\x43'))

    def test_refuses_plural_and_context(self):
        for bad in ('msgctxt "x"\nmsgid "a"\nmsgstr "b"\n', 'msgid "a"\nmsgid_plural "as"\nmsgstr[0] "b"\n'):
            with self.assertRaises(ValueError):
                po2lmo.compile_po(bad)

    def test_refuses_malformed(self):
        for bad in ('msgid "a\nmsgstr "b"\n', 'msgstr "b"\n', 'msgid "a"\nmsgstr "b"\nmsgid "a"\nmsgstr "c"\n',
                    'msgid "a"\n', 'bogus "a"\n', 'msgid "a" x\nmsgstr "b"\n'):
            with self.assertRaises(ValueError, msg=bad):
                po2lmo.compile_po(bad)

    def test_cli(self):
        r = subprocess.run([sys.executable, str(TOP / 'tools/po2lmo.py'), str(FIX / 'mu300.tr.po'), '/dev/stdout'],
                           capture_output=True)
        self.assertEqual(r.stdout, (FIX / 'mu300.tr.lmo').read_bytes())

    def test_cli_empty_catalog_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            po, lmo = Path(d) / 'x.po', Path(d) / 'x.lmo'
            po.write_text('msgid "A"\nmsgstr "A"\n', encoding='utf-8')
            lmo.write_bytes(b'stale')
            r = subprocess.run([sys.executable, str(TOP / 'tools/po2lmo.py'), str(po), str(lmo)], capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(lmo.exists())

    def test_cli_refusal_exits_1(self):
        with tempfile.TemporaryDirectory() as d:
            po = Path(d) / 'x.po'
            po.write_text('msgctxt "x"\nmsgid "a"\nmsgstr "b"\n', encoding='utf-8')
            r = subprocess.run([sys.executable, str(TOP / 'tools/po2lmo.py'), str(po), str(Path(d) / 'x.lmo')],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn('msgctxt', r.stderr)
            self.assertFalse((Path(d) / 'x.lmo').exists())


if __name__ == '__main__':
    unittest.main()
