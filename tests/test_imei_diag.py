"""imei-diag and the collector's fallback to it (FINDINGS 220).

Some U30 Air firmware answers AT+CGSN with fifteen zeros -- on both SIM slots, and through the vendor's own
miscserver path -- while the modem's NV still holds both IMEIs. The vendor's backend reads them over the diag port;
`imei-diag` makes the same read (no write) and `cell` asks it only when AT+CGSN gave nothing usable. The answers
here are the bytes the modem sends, with the test's own IMEI in place of the unit's."""
import json
import shutil
import unittest

from helpers import ShellTest, TOP

MODEM = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec' / 'unisoc-modem'
DIAG = MODEM / 'imei-diag'
CELL = MODEM / 'cell'


def answer(imei):
    """What the modem sends for one IMEI request: the flag, the four-zero header, the 74 00 5E 01 marker, the block
    (least significant nibble first, an a before the first digit), zero padding, a CRC and the flag."""
    digits = 'a' + imei
    block = ''.join(digits[i + 1] + digits[i] for i in range(0, 16, 2))
    return bytes.fromhex('7e' + '00000000' + '74005e01' + block + '00' * 16 + '3bc9' + '7e')


class ImeiDiag(ShellTest):
    """The reader itself: the decode, and the cases that must print nothing instead of a wrong IMEI."""

    IMEI1 = '123456789012345'
    IMEI2 = '987654321098765'

    def read(self, shell, slot, data, name):
        path = self.tmp / name
        path.write_bytes(data)
        r = self.script(shell, DIAG, slot, MU300_DIAG_DEV=path)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_both_slots_decode(self):
        for shell in self.each_shell():
            self.assertEqual(self.read(shell, 0, answer(self.IMEI1), 'a'), self.IMEI1)
            self.assertEqual(self.read(shell, 1, answer(self.IMEI2), 'b'), self.IMEI2)

    def test_nothing_usable_prints_nothing(self):
        for shell in self.each_shell():
            self.assertEqual(self.read(shell, 0, b'', 'c'), '')
            self.assertEqual(self.read(shell, 0, b'\x7e\x00\x00\x00\x00\x7e', 'd'), '')  # no marker
            self.assertEqual(self.read(shell, 0, bytes.fromhex('7e' + '00000000' + '74005e01' + '00' * 16 + '3bc9' + '7e'),
                                                 'e'), '')  # no value after the marker

    def test_missing_port_prints_nothing(self):
        for shell in self.each_shell():
            r = self.script(shell, DIAG, 0, MU300_DIAG_DEV=self.tmp / 'does-not-exist')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, ''), r.stderr)

    def test_the_images_busybox_reads_where_there_is_one(self):
        # OpenWrt's busybox has no od and no timeout: imei-diag takes them from the image's full busybox
        bb = self.tmp / 'busybox'
        log = self.tmp / 'applets'
        bb.write_text('#!/bin/sh\necho "$1" >> %s\nexec "$@"\n' % log)
        bb.chmod(0o755)
        for shell in self.each_shell():
            log.write_text('')
            path = self.tmp / 'f'
            path.write_bytes(answer(self.IMEI1))
            r = self.script(shell, DIAG, 0, MU300_DIAG_DEV=path, MU300_BUSYBOX=bb)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, self.IMEI1), r.stderr)
            self.assertEqual(log.read_text().split(), ['od'])

    def test_usage(self):
        for shell in self.each_shell():
            r = self.script(shell, DIAG, 7, MU300_DIAG_DEV=self.tmp / 'x')
            self.assertEqual(r.returncode, 2)
            self.assertIn('usage', r.stderr)


class CellUsesDiag(ShellTest):
    """The collector: AT+CGSN's fifteen zeros are replaced by the diag read, and a working AT+CGSN is not."""

    IMEI_AT = '867530012345678'
    IMEI_DIAG = '123456789012345'

    def setUp(self):
        super().setUp()
        self.stub('uci', 'exit 1')
        self.stub('fake-at', r"""printf '%s\n' "$*" >> "$STUBLOG/at"
case "$*" in
  *AT+CFUN?*)     printf '+CFUN: 1\nOK\n' ;;
  *AT+CEREG?*)    printf '+CEREG: 2,1,"1A2B","01234567",7\nOK\n' ;;
  *AT+C5GREG*)    printf '+C5GREG: 2,1,"1A2B","01234567",7\nOK\n' ;;
  *AT+CESQ*)      printf '+CESQ: 99,99,255,255,20,50\nOK\n' ;;
  *AT+CGSN*)      [ -e "$STUBLOG/cgsn-lead0" ] && { printf '012345678901237\nOK\n'; exit 0; }
                  [ -e "$STUBLOG/cgsn-zeros" ] || { printf '867530012345678\nOK\n'; exit 0; }
                  printf '000000000000000\nOK\n' ;;
  *AT+CIMI*)      printf '286016012345678\nOK\n' ;;
  *AT+CCID*)      printf '+CCID: 8990011234567890123\nOK\n' ;;
  *)              printf 'OK\n' ;;
esac""")
        self.diag = self.tmp / 'diag'
        self.diag.write_bytes(answer(self.IMEI_DIAG))

    def collect(self, shell, tag):
        run, ident = self.tmp / f'run-{tag}', self.tmp / f'ident-{tag}'
        for d in (run, ident):
            shutil.rmtree(d, ignore_errors=True)
        r = self.script(shell, CELL, 'full', MU300_AT=self.stubs / 'fake-at', MU300_IMEI_DIAG=DIAG,
                        MU300_DIAG_DEV=self.diag, MU300_DASH_DIR=run, MU300_DASH_IDENT_DIR=ident,
                        MU300_DASH_POOL_DIR=self.tmp / f'pool-{tag}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout + r.stderr, '')
        return json.loads((run / 'cell.json').read_text())['ident'], ident

    def test_zeros_are_read_over_diag(self):
        (self.tmp / 'cgsn-zeros').write_text('')
        for shell in self.each_shell():
            ident, cache = self.collect(shell, 'zero')
            self.assertEqual(ident['imei'], self.IMEI_DIAG)
            # the panel shows both module IMEIs; the fixture answers the same one for both slots
            self.assertEqual((ident['imei1'], ident['imei2']), (self.IMEI_DIAG, self.IMEI_DIAG))
            self.assertEqual(ident['slot'], 0)
            # a zero IMEI is no complete identity: the real one goes to the 6 h cache
            self.assertEqual(json.loads((cache / 'sim.json').read_text())['imei'], self.IMEI_DIAG)

    def test_a_working_at_cgsn_is_kept(self):
        for shell in self.each_shell():
            ident, _ = self.collect(shell, 'at')
            self.assertEqual(ident['imei'], self.IMEI_AT)
            self.assertEqual((ident['imei1'], ident['imei2']), (self.IMEI_DIAG, self.IMEI_DIAG))

    def test_an_imei_with_a_leading_zero_is_a_complete_identity(self):
        """Reporting bodies 00-01 give real IMEIs a leading 0: only fifteen zeros are no identity, so such an IMEI
        is cached for the 6 h like any other and AT+CGSN is not asked again on the next round."""
        (self.tmp / 'cgsn-lead0').write_text('')
        for shell in self.each_shell():
            run, ident = self.tmp / 'run-l0', self.tmp / 'ident-l0'
            for d in (run, ident):
                shutil.rmtree(d, ignore_errors=True)
            (self.tmp / 'at').unlink(missing_ok=True)
            env = dict(MU300_AT=self.stubs / 'fake-at', MU300_IMEI_DIAG=DIAG, MU300_DIAG_DEV=self.diag,
                       MU300_DASH_DIR=run, MU300_DASH_IDENT_DIR=ident, MU300_DASH_POOL_DIR=self.tmp / 'pool-l0')
            for _ in range(2):
                r = self.script(shell, CELL, 'full', **env)
                self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads((run / 'cell.json').read_text())['ident']['imei'], '012345678901237')
            self.assertEqual(1, (self.tmp / 'at').read_text().count('AT+CGSN'))
