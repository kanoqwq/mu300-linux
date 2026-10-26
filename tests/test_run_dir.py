"""/run/mu300-at: lib/run-dir.sh, the one check the daemons that keep a
directory under the world-writable /run use."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

RUN_DIR_LIB = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'lib' / 'run-dir.sh'
ATD = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-atd'


class RunDirectory(unittest.TestCase):
    """/run is world-writable on the images, so /run/mu300-at may be a directory somebody
    else made first. lib/run-dir.sh is the shared directory ownership check."""

    def call(self, fn, path, uid, pre=''):
        return subprocess.run(['sh', '-c', '. "$0"; %s\n%s "$1" "$2" test' % (pre, fn), str(RUN_DIR_LIB), str(path),
                               str(uid)], capture_output=True, text=True)

    def ours(self, path, uid):
        return self.call('run_dir_ours', path, uid).returncode == 0

    def test_only_the_owners_own_directory(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mine = d / 'mine'
            mine.mkdir(mode=0o700)
            self.assertTrue(self.ours(mine, os.getuid()))
            mine.chmod(0o710)                      # the voice group may enter
            self.assertTrue(self.ours(mine, os.getuid()))
            self.assertFalse(self.ours(mine, os.getuid() + 1))        # somebody else's
            for mode in (0o770, 0o707, 0o777, 0o1777):
                mine.chmod(mode)
                self.assertFalse(self.ours(mine, os.getuid()), oct(mode))
            mine.chmod(0o700)
            (d / 'link').symlink_to(mine)
            self.assertFalse(self.ours(d / 'link', os.getuid()))
            (d / 'file').write_text('')
            self.assertFalse(self.ours(d / 'file', os.getuid()))
            self.assertFalse(self.ours(d / 'missing', os.getuid()))

    def test_taken_created_recovered_and_never_through_a_symlink(self):
        import os
        me = os.getuid()
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # missing: created, the owner's alone
            r = self.call('run_dir_take', d / 'new', me)
            self.assertEqual((r.returncode, r.stderr), (0, ''))
            self.assertEqual((d / 'new').stat().st_mode & 0o777, 0o700)
            # ours already, with what is in it: untouched and silent
            (d / 'new' / 'cmd').write_text('x')
            r = self.call('run_dir_take', d / 'new', me)
            self.assertEqual((r.returncode, r.stderr, (d / 'new' / 'cmd').read_text()), (0, '', 'x'))
            # ours, but others may write: that is taken away, the contents stay
            (d / 'new').chmod(0o777)
            r = self.call('run_dir_take', d / 'new', me)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('taken away', r.stderr)
            self.assertEqual(((d / 'new').stat().st_mode & 0o777, (d / 'new' / 'cmd').exists()), (0o755, True))
            # a symlink to somewhere else: replaced by a real directory, and nothing is made or removed behind it
            target = d / 'elsewhere'
            target.mkdir()
            (target / 'keep').write_text('k')
            (d / 'link').symlink_to(target)
            r = self.call('run_dir_take', d / 'link', me)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('put out of the way', r.stderr)
            self.assertFalse((d / 'link').is_symlink())
            self.assertTrue((d / 'link').is_dir())
            self.assertEqual(sorted(p.name for p in target.iterdir()), ['keep'])
            # a dangling symlink (mkdir -p would make its target): the same
            (d / 'dangling').symlink_to(d / 'nowhere')
            r = self.call('run_dir_take', d / 'dangling', me)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((d / 'nowhere').exists())
            self.assertTrue((d / 'dangling').is_dir() and not (d / 'dangling').is_symlink())
            # a file of that name: replaced
            (d / 'file').write_text('')
            self.assertEqual(self.call('run_dir_take', d / 'file', me).returncode, 0)
            self.assertTrue((d / 'file').is_dir())
            # somebody who is not the owner asked for (a member of the voice group): the check alone, with the reason
            r = self.call('run_dir_take', d / 'new', me + 1)
            self.assertEqual(r.returncode, 1)
            self.assertIn("test: %s is missing or not the daemon's own directory" % (d / 'new'), r.stderr)
            self.assertTrue((d / 'new' / 'cmd').exists())
            r = self.call('run_dir_take', d / 'never', me + 1)
            self.assertEqual(r.returncode, 1)
            self.assertFalse((d / 'never').exists())      # and it does not make it: that would be the squat itself

    # the owner asked for is "somebody else" (uid + 1) and id says we are him: every directory this test makes is
    # then a foreign one, which a test that is not root cannot make otherwise
    AS_OTHER = 'id() { echo %d; }'

    def test_a_foreign_directory_is_moved_aside_whole_never_deleted(self):
        import os
        other = os.getuid() + 1
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'at').mkdir()
            (d / 'at' / 'sub').mkdir()
            (d / 'at' / 'sub' / 'file').write_text('theirs')
            r = self.call('run_dir_take', d / 'at', other, self.AS_OTHER % other)
            aside = [p for p in d.iterdir() if p.name.startswith('.at.foreign.')]
            self.assertEqual(len(aside), 1, r.stderr)
            self.assertEqual(aside[0].stat().st_mode & 0o777, 0o700)
            self.assertEqual((aside[0] / 'at' / 'sub' / 'file').read_text(), 'theirs')
            self.assertIn('it is in %s now' % aside[0], r.stderr)
            # and a new, empty one is in its place (ours here, so "still not the daemon's own" for uid + 1)
            self.assertEqual(list((d / 'at').iterdir()), [])
            # an empty foreign directory simply goes
            (d / 'empty').mkdir()
            self.call('run_dir_take', d / 'empty', other, self.AS_OTHER % other)
            self.assertEqual([p.name for p in d.iterdir() if p.name.startswith('.empty.')], [])
        src = RUN_DIR_LIB.read_text()
        code = '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('#'))
        self.assertNotRegex(code, r'rm\s+-[a-zA-Z]*[rR]')          # nothing recursive, anywhere
        self.assertNotIn('mkdir -p', code)

    def test_an_entry_swapped_between_the_look_and_the_removal(self):
        """Whoever squatted the name can still write its parent: after the helper has looked at his entry he puts
        a symlink to something of root's there instead. Whatever the helper then does is done to the symlink, never
        to what it points at - for each kind of entry it may have seen first."""
        import os
        other = os.getuid() + 1
        # ls is how the helper looks: the second look is the one it acts on, and the swap comes right after it
        swap = (self.AS_OTHER % other) + (
            '\nls() {\n'
            '    command ls "$@"; _r=$?\n'
            '    echo x >> "$SWAP/looks"\n'
            '    if [ "$(wc -l < "$SWAP/looks")" -eq 2 ]; then mv "$SWAP/at" "$SWAP/was"; '
            'ln -s "$SWAP/precious" "$SWAP/at"; fi\n'
            '    return $_r\n'
            '}')
        for kind in ('empty directory', 'directory with files', 'file'):
            with self.subTest(seen=kind), tempfile.TemporaryDirectory() as d:
                d = Path(d)
                precious = d / 'precious'
                (precious / 'deep').mkdir(parents=True)
                (precious / 'deep' / 'keep').write_text('k')
                (precious / 'cmd').write_text('c')
                if kind == 'file':
                    (d / 'at').write_text('')
                else:
                    (d / 'at').mkdir()
                    if kind == 'directory with files':
                        (d / 'at' / 'x').write_text('x')
                env = dict(os.environ, SWAP=str(d))
                r = subprocess.run(['sh', '-c', '. "$0"; %s\nrun_dir_take "$1" "$2" test' % swap, str(RUN_DIR_LIB),
                                    str(d / 'at'), str(other)], capture_output=True, text=True, env=env)
                self.assertEqual((d / 'looks').read_text().count('x') >= 2, True, r.stderr)
                # what the symlink pointed at is whole: nothing removed in it, nothing made in it, not moved
                self.assertEqual(sorted(str(p.relative_to(precious)) for p in precious.rglob('*')),
                                 ['cmd', 'deep', 'deep/keep'], r.stderr)
                self.assertEqual((precious / 'deep' / 'keep').read_text(), 'k')
                # and the name is never left as a symlink that the daemon would go on to write through
                self.assertFalse(r.returncode == 0 and (d / 'at').is_symlink(), r.stderr)

    def test_mu300_atd_uses_the_helper_before_it_writes(self):
        atd = ATD.read_text()
        check = atd.index('run_dir_take "$DIR"')
        for first_use in ('mkdir "$LOCK"', 'mkfifo -m 600 "$CMD"', 'mkdir -p "$URCDIR"'):
            self.assertLess(check, atd.index(first_use), first_use)
        self.assertNotIn('run_dir_ours() {', atd)       # no copy of its own


if __name__ == '__main__':
    unittest.main()
