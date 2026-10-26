"""uci-defaults 92-mu300-uhttpd-key (FINDINGS 132): the TLS key pair the OpenWrt releases shipped goes, a key of the
user's stays. No real key is in the repository: the tests add the fingerprint of a scratch file to the list."""
import hashlib
import os
import re
import unittest

from helpers import TOP, ShellTest

OPENWRT = TOP / 'openwrt' / 'overlay'
SCRIPT = OPENWRT / 'etc' / 'uci-defaults' / '92-mu300-uhttpd-key'


def shipped_list():
    body = SCRIPT.read_text()
    block = body[body.index("shipped='\n") + len("shipped='\n"):]
    return block[:block.index("'\n")].splitlines()


class ShippedList(unittest.TestCase):
    def test_list_is_fingerprints_of_every_shipping_release(self):
        lines = shipped_list()
        rows = [line.split(' ') for line in lines]
        self.assertTrue(all(len(r) == 3 for r in rows), lines)
        sums = [r[0] for r in rows]
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{64}', s) for s in sums))
        self.assertEqual(len(sums), len(set(sums)), 'one key per image')
        # never the sha256 of an empty file (a key that came out empty would match every empty file)
        self.assertNotIn(hashlib.sha256(b'').hexdigest(), sums)
        # both images of each release from v2026.10.13 (the first with a key) to v2026.10.16.1
        tags = ['v2026.10.%d' % n for n in range(13, 28)] + ['v2026.10.16.1']
        self.assertEqual({(t, f) for t in tags for f in ('openwrt', 'openwrt-luci')}, {(r[1], r[2]) for r in rows})
        # v2026.10.16.1's plain OpenWrt key, as FINDINGS 132 quotes it (seen on F50-C and in the release tarball)
        self.assertTrue(any(s.startswith('ad78a3f7') and t == 'v2026.10.16.1' and f == 'openwrt' for s, t, f in rows))

    def test_runs_at_every_boot_of_a_new_image_and_removes_itself(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        body = SCRIPT.read_text()
        self.assertTrue(body.startswith('#!/bin/sh\n'))
        # uci-defaults delete a script that ends with 0: that is the marker that it ran on this image
        self.assertTrue(body.rstrip().endswith('exit 0'))
        self.assertNotIn('BEGIN', body)          # fingerprints only, never a key in the repository
        self.assertNotIn('PRIVATE KEY', body)


class ImageHasNoKey(unittest.TestCase):
    def test_build_removes_the_pair_and_the_audit_refuses_a_key(self):
        owrt = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertIn('rm -f $R/etc/uhttpd.key $R/etc/uhttpd.crt\n'
                      '[ ! -e $R/etc/uhttpd.key ] || { echo "the image holds a TLS key" >&2; exit 1; }', owrt)
        # the build script is one single-quoted sh -c argument: no apostrophe may be in it
        start = "mu300-$FLAVOUR-base:$VER /bin/sh -eu -c '"
        body = owrt[owrt.index(start) + len(start):owrt.rindex("ls -la /out/$OUT'")]
        self.assertNotIn("'", body)
        self.assertIn('rm -f $R/etc/uhttpd.key', body)
        rel = (TOP / 'tools' / 'make-release.sh').read_text()
        self.assertIn(r"-e '^etc/uhttpd\.key$'", rel)
        self.assertTrue(re.search(r'^etc/uhttpd\.key$', 'etc/uhttpd.key'))


class DropShipped(ShellTest):
    def setUp(self):
        super().setUp()
        self.etc = self.tmp / 'etc'
        (self.etc / 'ssl').mkdir(parents=True)
        self.uci_conf = self.tmp / 'uci-uhttpd'
        self.uci_conf.write_text("uhttpd.main=uhttpd\nuhttpd.main.cert='/etc/uhttpd.crt'\n"
                                 "uhttpd.main.key='/etc/uhttpd.key'\n")
        # uci: show prints the file, get one value from it
        self.stub('uci', '[ "$1" = -q ] && shift\n'
                         'case $1 in\n'
                         '    show) cat "$STUBLOG/uci-uhttpd" ;;\n'
                         '    get) v=$(grep "^$2=" "$STUBLOG/uci-uhttpd") || exit 1; v=${v#*=}; v=${v#\\\'}; '
                         'echo "${v%\\\'}" ;;\n'
                         '    *) exit 1 ;;\n'
                         'esac')
        self.stub('logger', 'echo "$*" >> "$STUBLOG/logger.log"')

    def pair(self, name, key, crt=b'certificate\n'):
        (self.etc / f'{name}.key').write_bytes(key)
        (self.etc / f'{name}.crt').write_bytes(crt)

    def run_defaults(self, shell, shipped=()):
        """The script with /etc in the scratch directory and SHIPPED (contents) added to its list."""
        body = SCRIPT.read_text()
        extra = ''.join(f'{hashlib.sha256(k).hexdigest()} vTEST openwrt\n' for k in shipped)
        body = body.replace("shipped='\n", "shipped='\n" + extra, 1).replace('/etc/', f'{self.etc}/')
        self.uci_conf.write_text(self.uci_conf.read_text().replace("'/etc/", f"'{self.etc}/"))
        r = self.sh(shell, body)
        self.assertEqual(0, r.returncode, r.stderr)
        return r

    def test_a_shipped_key_goes_with_its_certificate(self):
        for shell in self.each_shell():
            self.pair('uhttpd', b'the shipped key\n')
            self.run_defaults(shell, shipped=[b'the shipped key\n'])
            self.assertFalse((self.etc / 'uhttpd.key').exists())
            self.assertFalse((self.etc / 'uhttpd.crt').exists())
            self.assertIn('removed the TLS key', (self.tmp / 'logger.log').read_text())
            # again (the next image's first boot): nothing there, nothing to do
            self.run_defaults(shell, shipped=[b'the shipped key\n'])
            self.assertFalse((self.etc / 'uhttpd.key').exists())

    def test_a_key_of_the_users_is_never_touched(self):
        for shell in self.each_shell():
            self.pair('uhttpd', b'made on the device\n', b'its certificate\n')
            self.run_defaults(shell, shipped=[b'the shipped key\n', b'made on the device'])
            self.assertEqual(b'made on the device\n', (self.etc / 'uhttpd.key').read_bytes())
            self.assertEqual(b'its certificate\n', (self.etc / 'uhttpd.crt').read_bytes())
            self.assertFalse((self.tmp / 'logger.log').exists())

    def test_the_real_list_spares_any_other_key(self):
        for shell in self.each_shell():
            self.pair('uhttpd', b'made on the device\n')
            self.run_defaults(shell)
            self.assertTrue((self.etc / 'uhttpd.key').exists())
            self.assertTrue((self.etc / 'uhttpd.crt').exists())

    def test_an_empty_key_file_is_not_a_shipped_one(self):
        for shell in self.each_shell():
            self.pair('uhttpd', b'')
            self.run_defaults(shell)
            self.assertTrue((self.etc / 'uhttpd.key').exists())

    def test_the_key_uci_points_to(self):
        for shell in self.each_shell():
            self.uci_conf.write_text("uhttpd.main=uhttpd\nuhttpd.main.cert='/etc/ssl/lan.crt'\n"
                                     "uhttpd.main.key='/etc/ssl/lan.key'\nuhttpd.other=uhttpd\n"
                                     "uhttpd.other.key='/etc/ssl/other.key'\n")
            (self.etc / 'ssl' / 'lan.key').write_bytes(b'the shipped key\n')
            (self.etc / 'ssl' / 'lan.crt').write_bytes(b'the shipped certificate\n')
            (self.etc / 'ssl' / 'other.key').write_bytes(b'the user key\n')
            self.pair('uhttpd', b'made on the device\n')
            self.run_defaults(shell, shipped=[b'the shipped key\n'])
            self.assertFalse((self.etc / 'ssl' / 'lan.key').exists())
            self.assertFalse((self.etc / 'ssl' / 'lan.crt').exists())
            self.assertTrue((self.etc / 'ssl' / 'other.key').exists())
            # the default pair is the user's: kept, also as the certificate of a section without one
            self.assertTrue((self.etc / 'uhttpd.key').exists())
            self.assertTrue((self.etc / 'uhttpd.crt').exists())

    def test_no_uhttpd_no_key(self):
        for shell in self.each_shell():
            self.stub('uci', 'exit 1')
            self.run_defaults(shell, shipped=[b'the shipped key\n'])
            self.assertFalse((self.tmp / 'logger.log').exists())


if __name__ == '__main__':
    unittest.main()
