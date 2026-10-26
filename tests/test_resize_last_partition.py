"""tools/resize-last-partition.py, the tool both installers make room with (install.sh's offer_repartition and
install.ps1's OfferRepartition), against the tables of fakegpt.py: the factory 64 GB layout and the one of issue #32,
where userdata fills the disk. It may change one thing, the last sector of userdata, and it refuses every table it
does not expect before it writes a file."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fakegpt

TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'resize-last-partition.py'
DISK = fakegpt.DISK64
LAST = fakegpt.USABLE_LAST64
FIRST = fakegpt.USERDATA_FIRST


class Resize(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def table(self, name):
        fakegpt.write(str(self.tmp / 'gpt'), name)
        return (self.tmp / 'gpt.head').read_bytes(), (self.tmp / 'gpt.tail').read_bytes()

    def tool(self, *args, dump='gpt'):
        return subprocess.run([sys.executable, str(TOOL), f'{self.tmp / dump}.head,{self.tmp / dump}.tail',
                               '--disk-sectors', str(DISK), '--name', 'userdata', *args],
                              capture_output=True, text=True)

    def shown(self, r):
        return dict(l.split('=', 1) for l in r.stdout.splitlines() if l.startswith(('LAST_', 'USABLE_')))

    def test_show_the_factory_table(self):
        self.table('factory64')
        r = self.tool('--show')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.shown(r), {'LAST_NAME': 'userdata', 'LAST_FIRST': str(FIRST),
                                         'LAST_END': str(54218752 - 1), 'USABLE_END': str(LAST)})

    def test_show_userdata_filling_the_disk(self):
        """Issue #32: nothing is free behind userdata, and all of it is there to share."""
        self.table('vendor64')
        r = self.tool('--show')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.shown(r)['LAST_END'], self.shown(r)['USABLE_END'])
        self.assertEqual(int(self.shown(r)['USABLE_END']) - FIRST + 1, 109879296)      # 52.4 GiB

    def test_shrinking_changes_the_end_of_userdata_and_nothing_else(self):
        head, tail = self.table('vendor64')
        new_end = LAST - (4 << 21)                                                    # 4 GiB for Linux
        r = self.tool('--end-sector', str(new_end), '--out', str(self.tmp / 'new'))
        self.assertEqual(r.returncode, 0, r.stderr)
        nhead, ntail = (self.tmp / 'new.head').read_bytes(), (self.tmp / 'new.tail').read_bytes()
        self.assertEqual((len(nhead), len(ntail)), (len(head), len(tail)))
        index = len(fakegpt.BEFORE)                                                   # userdata's entry
        end_field = range(index * 128 + 40, index * 128 + 48)
        crcs = set(range(16, 20)) | set(range(88, 92))                                # header CRC, entries CRC
        allowed = ({2 * 512 + i for i in end_field} | {512 + i for i in crcs},        # head: header at LBA 1, entries at 2
                   set(end_field) | {32 * 512 + i for i in crcs})                     # tail: entries, then the header
        for old, new, ok in ((head, nhead, allowed[0]), (tail, ntail, allowed[1])):
            changed = {i for i in range(len(old)) if old[i] != new[i]}
            self.assertTrue(changed)
            self.assertLessEqual(changed, ok)
        (self.tmp / 'gpt.head').write_bytes(nhead)
        (self.tmp / 'gpt.tail').write_bytes(ntail)
        after = self.shown(self.tool('--show'))
        self.assertEqual((after['LAST_FIRST'], after['LAST_END'], after['USABLE_END']),
                         (str(FIRST), str(new_end), str(LAST)))
        # and back: the table it started from, byte for byte (what uninstall.sh's grow-back does)
        r = self.tool('--fill', '--out', str(self.tmp / 'back'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(((self.tmp / 'back.head').read_bytes(), (self.tmp / 'back.tail').read_bytes()), (head, tail))

    def refused(self, r, why):
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(why, r.stderr)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ['gpt.head', 'gpt.tail'])

    def test_a_partition_behind_userdata_is_refused(self):
        self.table('nvlast64')
        self.refused(self.tool('--show'), "the last partition is 'l_fixnv2_a', not 'userdata'")
        self.refused(self.tool('--end-sector', str(LAST - (4 << 21)), '--out', str(self.tmp / 'new')), 'refusing')

    def test_a_table_that_does_not_check_is_refused(self):
        head, tail = self.table('vendor64')
        for name, data, at, why in (('gpt.head', head, 2 * 512 + 40, 'primary entry array CRC'),
                                    ('gpt.tail', tail, 40, 'backup entry array CRC'),
                                    ('gpt.head', head, 512 + 40, 'primary header: header CRC'),
                                    ('gpt.tail', tail, 32 * 512, 'backup header: not a GPT header')):
            bad = bytearray(data)
            bad[at] ^= 1
            (self.tmp / name).write_bytes(bytes(bad))
            self.refused(self.tool('--end-sector', str(LAST - (4 << 21)), '--out', str(self.tmp / 'new')), why)
            (self.tmp / name).write_bytes(data)
        r = subprocess.run([sys.executable, str(TOOL), f'{self.tmp}/gpt.head,{self.tmp}/gpt.tail', '--disk-sectors',
                            str(DISK - 8), '--name', 'userdata', '--show'], capture_output=True, text=True)
        self.refused(r, 'the headers are not where a GPT puts them')

    def test_an_end_outside_userdata_is_refused(self):
        self.table('vendor64')
        self.refused(self.tool('--end-sector', str(LAST + 1), '--out', str(self.tmp / 'new')), 'past the end')
        self.refused(self.tool('--end-sector', str(FIRST), '--out', str(self.tmp / 'new')), 'would leave userdata empty')


if __name__ == '__main__':
    unittest.main()
