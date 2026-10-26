"""An update writes only what changed (docs/FINDINGS.md 131): mu300-carry same, tarfilter and tarlink
(tools/carry/mu300-carry.c, built here), mu300-update's delta block (the plan, the room, the unpack, a run that is cut
off), apply_one and the installers' loop (tools/android-install.sh) with an image that brings it, the previous system
kept for a rollback whole after the update and after what the new system does to its files, and the Magisk switch
module the computer's installer puts on the device."""
import gzip
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest

CC = shutil.which('cc') or shutil.which('gcc')
LINUX = sys.platform.startswith('linux')
BUSYBOX = shutil.which('busybox')
CARRY = None
_build = None


def setUpModule():
    global CARRY, _build
    if not (CC and LINUX):
        return
    _build = tempfile.mkdtemp(prefix='mu300-delta-build-')
    out = Path(_build) / 'mu300-carry'
    r = subprocess.run([CC, '-O2', '-Wall', '-Wextra', '-Werror', '-o', str(out), str(TOP / 'tools/carry/mu300-carry.c')],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('mu300-carry.c does not build without warnings:\n' + r.stderr)
    CARRY = out


def tearDownModule():
    if _build:
        shutil.rmtree(_build, ignore_errors=True)


def carry(*args, stdin=None, binary=False):
    return subprocess.run([str(CARRY)] + [str(a) for a in args], input=stdin, capture_output=True, text=not binary)


def elf(tag, size=3000):
    """binary content: a NUL byte in it"""
    body = (tag.encode() * (size // max(1, len(tag)) + 1))[:size]
    return b'\x7fELF\x02\x01\x01\x00' + body


LONG = 'usr/share/long/' + 'a-very-long-directory-name-' * 4 + '/' + 'file-' * 30 + 'end.bin'
# an image: binaries in the trees that are linked and in the ones that never are, text, a hard link, a long name
V1 = {
    'sbin/init': ('f', '#!/bin/sh\n', 0o755),
    'usr/bin/prog': ('f', elf('prog'), 0o755),
    'usr/bin/changed': ('f', elf('changed v1'), 0o755),
    'usr/bin/hard1': ('f', elf('hard'), 0o755),
    'usr/lib/libbig.so': ('f', elf('big', 200000), 0o644),
    'usr/lib/python3/mod.py': ('f', 'print("text: never linked")\n', 0o644),
    'usr/lib/locale/locale-archive': ('f', elf('locale'), 0o644),
    'usr/local/bin/tool': ('f', elf('local'), 0o755),
    'lib/modules/1.0/x.ko': ('f', elf('module'), 0o644),
    'opt/mu300/bin/thing': ('f', elf('thing'), 0o755),
    'etc/binary.db': ('f', elf('etc'), 0o644),
    'var/lib/state.bin': ('f', elf('var'), 0o644),
    'usr/share/empty': ('f', b'', 0o644),
    LONG: ('f', elf('long'), 0o644),
    'usr/bin/link-to-prog': ('l', 'prog'),
    'etc/mu300/image-version': ('f', 'v1\n', 0o644),
    'opt/mu300/lib/path-commands': ('f', 'mu300-update\n', 0o644),
}
V2 = dict(V1)
V2.update({
    'usr/bin/changed': ('f', elf('changed v2'), 0o755),
    'usr/bin/new-in-v2': ('f', elf('new'), 0o755),
    'etc/mu300/image-version': ('f', 'v2\n', 0o644),
})
LINKED = {'usr/bin/prog', 'usr/bin/hard1', 'usr/bin/hard2', 'usr/lib/libbig.so', 'lib/modules/1.0/x.ko', 'opt/mu300/bin/thing',
          LONG}
# the parts of the image itself that the delta block needs (busybox, the helper, the updater) are binaries or text too
PARTS = {'usr/bin/busybox', 'opt/mu300/bin/mu300-carry'}


def build(root, spec):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for path, v in spec.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        if v[0] == 'l':
            os.symlink(v[1], p)
        else:
            p.write_bytes(v[1] if isinstance(v[1], bytes) else v[1].encode())
            p.chmod(v[2])
    if 'usr/bin/hard1' in spec:
        os.link(root / 'usr/bin/hard1', root / 'usr/bin/hard2')
    return root


def image(path, spec, tmp, tag, parts=True, fmt=tarfile.GNU_FORMAT):
    """a release image of SPEC (TAG) with its manifest and, with PARTS, what the delta block needs: this checkout's
    mu300-update, the helper built here, a busybox"""
    root = build(Path(tmp) / f'img-{tag}', spec)
    if parts:
        for f, src in (('opt/mu300/bin/mu300-update', BIN / 'mu300-update'), ('opt/mu300/bin/mu300-carry', CARRY),
                       ('usr/bin/busybox', BUSYBOX)):
            (root / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, root / f)
            (root / f).chmod(0o755)
    m = carry('manifest', root, 'ubuntu-24.04', tag)
    assert m.returncode == 0, m.stderr
    (root / 'etc/mu300/image-manifest.gz').write_bytes(gzip.compress(m.stdout.encode()))
    with tarfile.open(path, 'w:gz', format=fmt) as t:
        t.add(root, arcname='.')
    shutil.rmtree(root)
    return path


def tree(root):
    """every entry below ROOT with what an update must give it: kind, mode, owner, content or target"""
    out = {}
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(d, n)
            st = os.lstat(p)
            what = (os.readlink(p) if stat.S_ISLNK(st.st_mode) else Path(p).read_bytes() if stat.S_ISREG(st.st_mode)
                    else None)
            out[os.path.relpath(p, root)] = (stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid, what)
    return out


def snapshot(root):
    """TREE and the times: what the previous system kept for a rollback must keep, to the nanosecond"""
    out = {}
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(d, n)
            st = os.lstat(p)
            what = (os.readlink(p) if stat.S_ISLNK(st.st_mode) else Path(p).read_bytes() if stat.S_ISREG(st.st_mode)
                    else None)
            out[os.path.relpath(p, root)] = (st.st_mode, st.st_uid, st.st_gid, st.st_mtime_ns if not stat.S_ISDIR(st.st_mode) else 0, what)
    return out


def inode_shared(a, b):
    """the paths whose file in A is the same file (inode) as in B"""
    out = set()
    for d, dirs, files in os.walk(a):
        for n in files:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, a)
            q = os.path.join(b, rel)
            if os.path.lexists(q) and not os.path.islink(p) and os.lstat(p).st_ino == os.lstat(q).st_ino:
                out.add(rel)
    return out


def untar(tarball, dest, data=None):
    dest.mkdir(parents=True, exist_ok=True)
    if data is not None:
        r = subprocess.run(['tar', '-xpf', '-', '-C', str(dest)], input=data, capture_output=True)
    else:
        r = subprocess.run(['tar', '-xzpf', str(tarball), '-C', str(dest)], capture_output=True)
    assert r.returncode == 0, r.stderr
    return dest


@unittest.skipUnless(CC and LINUX, 'needs a C compiler on Linux')
class Helper(unittest.TestCase):
    """same, tarfilter, tarlink"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-delta-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def manifest(self, root):
        return carry('manifest', root, 'x', 'y').stdout.splitlines()[1:]

    def test_same_says_which_files_are_there_byte_for_byte_and_which_are_binaries(self):
        old = build(self.tmp / 'old', V1)
        new = build(self.tmp / 'new', V2)
        lines = [l for l in self.manifest(new) if l.startswith('f ')]
        r = carry('same', old, stdin='\n'.join(lines) + '\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        got = dict(l.split(' ', 1)[::-1] for l in r.stdout.splitlines())
        self.assertEqual(got['usr/bin/prog'], 'B')
        self.assertEqual(got['usr/lib/python3/mod.py'], 'T')
        self.assertEqual(got[LONG], 'B')
        self.assertNotIn('usr/bin/changed', got)        # another content
        self.assertNotIn('usr/bin/new-in-v2', got)      # not there
        # another mode, another size, a link on the way, an extended attribute: not the same file
        (old / 'usr/bin/prog').chmod(0o700)
        (old / 'usr/lib/libbig.so').write_bytes(elf('big', 200001))
        shutil.rmtree(old / 'opt/mu300/bin')
        os.symlink(str(new / 'opt/mu300/bin'), old / 'opt/mu300/bin')
        try:
            os.setxattr(old / 'lib/modules/1.0/x.ko', 'user.mine', b'1')
            xattr = True
        except OSError:
            xattr = False
        r = carry('same', old, stdin='\n'.join(lines) + '\n')
        got = dict(l.split(' ', 1)[::-1] for l in r.stdout.splitlines())
        for p in ('usr/bin/prog', 'usr/lib/libbig.so', 'opt/mu300/bin/thing') + (('lib/modules/1.0/x.ko',) if xattr else ()):
            self.assertNotIn(p, got, p)
        self.assertIn('usr/bin/hard1', got)

    def filtered(self, old, tarball, listed):
        (self.tmp / 'list').write_text(''.join(p + '\n' for p in listed))
        raw = gzip.decompress(Path(tarball).read_bytes())
        r = subprocess.run([str(CARRY), 'tarfilter', str(old), str(self.tmp / 'list') if listed != '*' else '*', str(self.tmp / 'log')],
                           input=raw, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_tarfilter_and_tarlink_give_the_tree_a_whole_unpack_gives(self):
        for fmt in (tarfile.GNU_FORMAT, tarfile.PAX_FORMAT, tarfile.USTAR_FORMAT):
            with self.subTest(format=fmt):
                if fmt == tarfile.USTAR_FORMAT:
                    spec = {k: v for k, v in V2.items() if k != LONG}
                    oldspec = {k: v for k, v in V1.items() if k != LONG}
                else:
                    spec, oldspec = V2, V1
                d = self.tmp / f'f{fmt}'
                old = build(d / 'old', oldspec)
                tb = d / 'v2.tar.gz'
                with tarfile.open(tb, 'w:gz', format=fmt) as t:
                    t.add(build(d / 'src', spec), arcname='.')
                full = untar(tb, d / 'full')
                listed = sorted(set(spec) & {'usr/bin/prog', 'usr/bin/hard1', 'usr/bin/changed', LONG, 'usr/lib/libbig.so'})
                out = self.filtered(old, tb, listed)
                # what was left out is not in the stream: the archive is smaller by the files' data
                self.assertLess(len(out), len(gzip.decompress(tb.read_bytes())) - 200000)
                new = untar(None, d / 'new', data=out)
                log = (self.tmp / 'log').read_text()
                self.assertRegex(log, r'(?m)^E \d+ \d+$')
                self.assertNotIn('D usr/bin/changed\n', log)
                r = subprocess.run([str(CARRY), 'tarlink', str(old), str(new)], stdin=open(self.tmp / 'log'), capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(tree(new), tree(full))
                shared = inode_shared(new, old)
                self.assertEqual(shared, set(listed) - {'usr/bin/changed'} | {'usr/bin/hard2'})
                # the second name of a file left out is a second name of the same file again
                self.assertEqual(os.lstat(new / 'usr/bin/hard1').st_ino, os.lstat(new / 'usr/bin/hard2').st_ino)
                # the directories keep the times tar gave them
                self.assertEqual(os.lstat(new / 'usr/bin').st_mtime_ns, os.lstat(full / 'usr/bin').st_mtime_ns)

    def test_a_file_that_differs_after_it_was_listed_is_written_as_it_is(self):
        # same size, other bytes - at the start, and after the first 64 KiB the filter compared already
        old = build(self.tmp / 'old', V1)
        big = bytearray(elf('big', 200000))
        big[70000] ^= 0xff
        (old / 'usr/lib/libbig.so').write_bytes(bytes(big))
        prog = bytearray(elf('prog'))
        prog[1] ^= 0xff
        (old / 'usr/bin/prog').write_bytes(bytes(prog))
        tb = self.tmp / 'v1.tar.gz'
        with tarfile.open(tb, 'w:gz') as t:
            t.add(build(self.tmp / 'src', V1), arcname='.')
        out = self.filtered(old, tb, ['usr/lib/libbig.so', 'usr/bin/prog', 'usr/bin/hard1'])
        new = untar(None, self.tmp / 'new', data=out)
        r = subprocess.run([str(CARRY), 'tarlink', str(old), str(new)], stdin=open(self.tmp / 'log'), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tree(new), tree(untar(tb, self.tmp / 'full')))
        self.assertEqual(inode_shared(new, old), {'usr/bin/hard1', 'usr/bin/hard2'})

    def test_every_binary_and_never_text(self):
        # "*": the installers' pack of the device's own files - every binary the old system has as it is, no text
        spec = {'lib/firmware/a.bin': ('f', elf('fw'), 0o644), 'lib/firmware/b.txt': ('f', 'text\n' * 300, 0o644),
                'lib/firmware/c.bin': ('f', elf('changed'), 0o644)}
        old = build(self.tmp / 'old', dict(spec, **{'lib/firmware/c.bin': ('f', elf('other!!'), 0o644)}))
        tb = self.tmp / 'v.tar.gz'
        with tarfile.open(tb, 'w:gz') as t:
            t.add(build(self.tmp / 'src', spec), arcname='.')
        new = untar(None, self.tmp / 'new', data=self.filtered(old, tb, '*'))
        r = subprocess.run([str(CARRY), 'tarlink', str(old), str(new)], stdin=open(self.tmp / 'log'), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tree(new), tree(untar(tb, self.tmp / 'full')))
        self.assertEqual(inode_shared(new, old), {'lib/firmware/a.bin'})

    def test_tarlink_wants_the_whole_archive(self):
        old = build(self.tmp / 'old', V1)
        new = build(self.tmp / 'new', {'usr/bin/x': ('f', 'x', 0o644)})
        (self.tmp / 'log').write_text('D usr/bin/prog\n')
        r = subprocess.run([str(CARRY), 'tarlink', str(old), str(new)], stdin=open(self.tmp / 'log'), capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('was not read to its end', r.stderr)
        # a stream cut in the middle: tarfilter fails, and writes no end into its log
        tb = self.tmp / 'v1.tar.gz'
        with tarfile.open(tb, 'w:gz') as t:
            t.add(build(self.tmp / 'src', V1), arcname='.')
        raw = gzip.decompress(tb.read_bytes())[:30000]
        (self.tmp / 'list').write_text('usr/bin/prog\n')
        r = subprocess.run([str(CARRY), 'tarfilter', str(old), str(self.tmp / 'list'), str(self.tmp / 'log')], input=raw, capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('E ', (self.tmp / 'log').read_text())

    def test_a_member_name_that_leaves_the_root_is_never_compared(self):
        old = build(self.tmp / 'old', {'x/secret': ('f', elf('s'), 0o600)})
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as t:
            data = elf('s')
            ti = tarfile.TarInfo('../old/x/secret')
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
        (self.tmp / 'list').write_text('')
        r = subprocess.run([str(CARRY), 'tarfilter', str(old / 'x'), '*', str(self.tmp / 'log')], input=buf.getvalue(), capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, buf.getvalue())
        self.assertNotIn('D ', (self.tmp / 'log').read_text())


@unittest.skipUnless(CC and LINUX and BUSYBOX, 'needs a C compiler and busybox on Linux')
class Block(ShellTest):
    """mu300-update's delta block, and apply_one with it"""

    def setUp(self):
        super().setUp()
        self.disk = self.tmp / 'disk'
        self.root = self.tmp / 'root'
        (self.root / 'run/mu300').mkdir(parents=True)

    def fresh(self, new=V2, parts=True):
        shutil.rmtree(self.disk, ignore_errors=True)
        (self.disk / '.mu300-update').mkdir(parents=True)
        v1 = image(self.tmp / 'v1.tar.gz', V1, self.tmp, 'v1')
        self.old = untar(v1, self.disk / 'ubuntu')
        # the user's own: a file in /etc and data, as an update carries them
        build(self.old, {'etc/mine.conf': ('f', 'mine\n', 0o600), 'var/lib/app/db': ('f', elf('db'), 0o644)})
        image(self.disk / '.mu300-update/mu300-ubuntu-rootfs.tar.gz', new, self.tmp, 'v2', parts=parts)

    def up(self, shell, code, **env):
        e = dict(MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root)
        e.update(env)
        return self.sh(shell, f'. "{BIN}/mu300-update"; is_root() {{ false; }}; carry_need() {{ echo 0; }}; {code}', **e)

    def test_the_plan_counts_what_is_written(self):
        for shell in self.each_shell():
            self.fresh()
            w = self.tmp / 'w'
            shutil.rmtree(w, ignore_errors=True)
            r = self.up(shell, f'delta_tools "$MU300_DISK/.mu300-update/mu300-ubuntu-rootfs.tar.gz" "{w}" && delta_ready "{w}" ubuntu && '
                               f'delta_plan "$MU300_DISK/ubuntu" "{w}/lines" "{w}" "{w}/carry"')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            kb, linked = map(int, r.stdout.split())
            got = set((w / 'link').read_text().split('\n')) - {''}
            # the binaries of the program trees that did not change; the image's own parts are binaries too
            self.assertEqual(got - PARTS, LINKED)
            self.assertNotIn('usr/bin/changed', got)
            for p in ('usr/lib/python3/mod.py', 'usr/local/bin/tool', 'etc/binary.db', 'var/lib/state.bin', 'usr/lib/locale/locale-archive'):
                self.assertNotIn(p, got, p)
            self.assertGreater(linked, 200)        # libbig.so alone is 196 KiB
            # what is written: the files not linked (4 KiB blocks), a block for each directory
            m = [l.split(' ', 6) for l in (w / 'lines').read_text().splitlines()]
            want = sum((int(l[4]) + 4095) // 4096 * 4 for l in m if l[0] == 'f' and l[6] not in got) + \
                sum(4 for l in m if l[0] == 'd' or (l[0] == 'l' and int(l[4]) > 59))
            self.assertEqual(kb, want)

    def test_apply_one_writes_only_what_changed_and_keeps_the_previous_system_whole(self):
        for shell in self.each_shell():
            self.fresh()
            before = snapshot(self.old)
            r = self.up(shell, 'apply_one ubuntu v2')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('are in this system as they are and are not written again', r.stdout)
            new, old = self.disk / 'ubuntu', self.disk / 'ubuntu.old'
            self.assertEqual(snapshot(old), before)
            self.assertEqual((new / 'usr/bin/changed').read_bytes(), elf('changed v2'))
            self.assertEqual((new / 'usr/bin/new-in-v2').read_bytes(), elf('new'))
            self.assertEqual((new / 'etc/mine.conf').read_text(), 'mine\n')
            shared = inode_shared(new, old)
            # only binaries of the program trees are shared with the previous system, and the device's own files
            self.assertTrue(LINKED <= shared, LINKED - shared)
            for p in shared:
                self.assertRegex(p, r'^(usr/(?!local/)|bin/|sbin/|lib/|opt/mu300/)', p)
                self.assertIn(b'\0', (new / p).read_bytes(), p)
            for d in ('ubuntu.new', '.mu300-delta-ubuntu'):
                self.assertFalse((self.disk / d).exists(), d)
            self.assertIn('shares', r.stdout)
            # what the new system then does to its files: a package manager replaces them (a new file renamed over),
            # an editor writes into a text file, a program into its data, a chmod on /etc - the previous system kept
            # for a rollback sees none of it
            tmpf = new / 'usr/bin/.prog.dpkg-new'
            tmpf.write_bytes(elf('upgraded'))
            os.rename(tmpf, new / 'usr/bin/prog')
            with open(new / 'usr/lib/python3/mod.py', 'r+b') as f:
                f.write(b'# edited in place')
            with open(new / 'etc/binary.db', 'r+b') as f:
                f.write(b'in place')
            with open(new / 'var/lib/app/db', 'ab') as f:
                f.write(b'more')
            (new / 'etc/mine.conf').chmod(0o644)
            self.assertEqual(snapshot(old), before)
            # and the rollback brings the previous system back exactly
            r = self.up(shell, 'rollback ubuntu')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(snapshot(self.disk / 'ubuntu'), before)

    def test_an_image_without_the_parts_is_unpacked_whole(self):
        for shell in self.each_shell():
            self.fresh(parts=False)
            r = self.up(shell, 'apply_one ubuntu v2')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn('not written again', r.stdout)
            self.assertEqual(inode_shared(self.disk / 'ubuntu', self.disk / 'ubuntu.old') - {'var/lib/app/db'}, set())
            self.fresh()
            r = self.up(shell, 'apply_one ubuntu v2', MU300_DELTA=0)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn('not written again', r.stdout)

    def test_not_enough_room_changes_nothing(self):
        for shell in self.each_shell():
            self.fresh()
            before = snapshot(self.old)
            self.stub('df', 'printf "Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/x 900000 899000 1000 99%% /x\\n"')
            r = self.up(shell, 'apply_one ubuntu v2')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('not enough free space on the Linux partition', r.stderr)
            self.assertIn('Nothing was changed', r.stderr)
            self.assertEqual(snapshot(self.disk / 'ubuntu'), before)
            self.assertEqual(sorted(p.name for p in self.disk.iterdir()), ['ubuntu'])

    def test_a_failed_unpack_leaves_nothing_behind(self):
        for shell in self.each_shell():
            self.fresh()
            # the image cut after its parts (they come first): the plan is made, the unpack fails
            tb = self.disk / '.mu300-update/mu300-ubuntu-rootfs.tar.gz'
            raw = gzip.decompress(tb.read_bytes())
            tb.write_bytes(gzip.compress(raw[:len(raw) - 4096 * 3]))
            before = snapshot(self.old)
            r = self.up(shell, 'apply_one ubuntu v2')
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(snapshot(self.disk / 'ubuntu'), before)
            self.assertFalse((self.disk / 'ubuntu.new').exists())
            self.assertFalse((self.disk / '.mu300-delta-ubuntu').exists())

    def test_what_a_run_that_was_cut_off_left_goes(self):
        for shell in self.each_shell():
            self.fresh()
            for d in ('ubuntu.new/usr/bin', '.mu300-delta-openwrt/root', 'openwrt.new'):
                (self.disk / d).mkdir(parents=True)
            (self.disk / 'ubuntu.new.carry-log').write_text('x')
            r = self.up(shell, 'delta_clean "$MU300_DISK"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('ubuntu: removed ubuntu.new, left by an update that did not finish', r.stdout)
            self.assertEqual(sorted(p.name for p in self.disk.iterdir()), ['.mu300-update', 'ubuntu'])
            # an apply killed in the middle: its EXIT trap removes what it was making
            (self.disk / 'ubuntu.new').mkdir()
            (self.disk / '.mu300-delta-ubuntu').mkdir()
            r = self.up(shell, 'UNPACKING="$MU300_DISK/ubuntu.new $MU300_DISK/.mu300-delta-ubuntu $MU300_DISK/ubuntu"; unpack_abort')
            self.assertEqual(sorted(p.name for p in self.disk.iterdir()), ['.mu300-update', 'ubuntu'])
        src = (BIN / 'mu300-update').read_text()
        self.assertIn("trap 'unpack_abort; keepalive_stop; resume_data; lock_drop' EXIT", src)
        apply = src[src.index('\napply() {'):src.index('\nclean_broken() {')]
        self.assertLess(apply.index('delta_clean "$DISK"'), apply.index('update_need "$tag" $todo'))
        self.assertLess(apply.index('update_need "$tag" $todo'), apply.index('fetch_verified "$tag" "$(rootfs_asset "$os")"'))

    def test_the_estimate_before_the_download(self):
        for shell in self.each_shell():
            self.fresh()
            w = self.tmp / 'w'
            shutil.rmtree(w, ignore_errors=True)
            w.mkdir()
            (w / 'm.gz').write_bytes(subprocess.run(['tar', '-xzOf', str(self.disk / '.mu300-update/mu300-ubuntu-rootfs.tar.gz'),
                                                     './etc/mu300/image-manifest.gz'], capture_output=True).stdout)
            # the running system's helper: with "same" exact; with "sum" only (the releases before this) every file
            # that is there byte for byte counts, text too; none at all: the whole image
            bins = self.tmp / 'bins'
            bins.mkdir(exist_ok=True)
            shutil.copy(CARRY, bins / 'mu300-carry')
            res = {}
            for kind in ('same', 'sum', 'none'):
                if kind == 'sum':
                    (bins / 'mu300-carry').unlink()
                    (bins / 'mu300-carry').write_text(f'#!/bin/sh\n[ "$1" = features ] && exit 1\nexec "{CARRY}" "$@"\n')
                    (bins / 'mu300-carry').chmod(0o755)
                if kind == 'none':
                    (bins / 'mu300-carry').unlink()
                r = self.up(shell, f'delta_manifest "{w}/m.gz" v2 ubuntu "{w}/lines" && delta_estimate "$MU300_DISK/ubuntu" "{w}/lines" "{w}"',
                            MU300_BIN=bins)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                res[kind] = int(r.stdout.split()[0])
            self.assertLess(res['sum'], res['same'])
            self.assertLess(res['same'], res['none'])
            # a manifest of another release or image is not taken
            r = self.up(shell, f'delta_manifest "{w}/m.gz" v3 ubuntu "{w}/x" || echo refused; delta_manifest "{w}/m.gz" v2 openwrt "{w}/x" || echo refused')
            self.assertEqual(r.stdout, 'refused\nrefused\n')

    def test_the_device_files_are_not_counted_as_a_copy(self):
        # carry_need: what a rollback copy of the user's files takes - the device's own files (firmware, Android's
        # userspace, OpenWrt's modules) are second names now, never copied (205 MiB on an F50)
        old = build(self.disk / 'openwrt', {'sbin/init': ('f', '#!/bin/sh\n', 0o755), 'etc/mu300/image-version': ('f', 'v1\n', 0o644)})
        m = carry('manifest', old, 'openwrt', 'v1')
        (old / 'etc/mu300/image-manifest.gz').write_bytes(gzip.compress(m.stdout.encode()))
        build(old, {'opt/mu300/android/vendor.bin': ('f', elf('v', 4 << 20), 0o644), 'lib/firmware/fw.bin': ('f', elf('f', 2 << 20), 0o644),
                    'root/mine.bin': ('f', elf('m', 1 << 20), 0o644)})
        for shell in self.each_shell():
            r = self.sh(shell, f'. "{BIN}/mu300-update"; carry_need openwrt', MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN)
            self.assertEqual(r.returncode, 0, r.stderr)
            kb = int(r.stdout)
            self.assertGreater(kb, 32768 + 1024)
            self.assertLess(kb, 32768 + 1024 + 300)

    def test_the_size_of_a_download(self):
        # asset_kb: from a directory (apply --from) the file itself; from a mirror the server's answer to a HEAD
        src = build(self.tmp / 'src', {'mu300-kernel.tar.gz': ('f', b'x' * 300000, 0o644)})
        for shell in self.each_shell():
            r = self.up(shell, f'MU300_FROM="{src}"; FROM_KIND=dir; asset_kb v2 mu300-kernel.tar.gz')
            self.assertEqual(r.stdout.strip(), str(300000 // 1024 + 1), r.stderr)
            self.stub('curl', 'printf "HTTP/1.1 302 Found\\r\\nContent-Length: 0\\r\\n\\r\\nHTTP/1.1 200 OK\\r\\nContent-Length: 20267807\\r\\n\\r\\n"')
            r = self.up(shell, 'ASSET_JSON=; asset_kb v2 mu300-kernel-6.18.tar.gz', MU300_RELEASE_URL='http://mirror/v2')
            self.assertEqual(r.stdout.strip(), str(20267807 // 1024 + 1), r.stderr)

    def test_modules_are_never_written_into(self):
        src = (BIN / 'mu300-update').read_text()
        fn = src[src.index('kernel_modules_into_systems() {'):src.index('boot_update() {')]
        self.assertIn('rm -f "$DISK/$_os/lib/modules/$_krel/$_f"; cp "$1/$_f"', fn)
        self.assertIn('rm -f "$_d"/*.ko && mod_link "$1/modules" "$_d"', fn)
        # a set gets more names, never a file written into: removed first wherever a link or a copy goes
        ml = src[src.index('mod_link() {'):src.index('\n}\n', src.index('mod_link() {'))]
        self.assertLess(ml.index('rm -f "$2"/*.ko'), ml.index('cp "$1"/*.ko'))


@unittest.skipUnless(CC and LINUX and BUSYBOX, 'needs a C compiler and busybox on Linux')
class Installers(ShellTest):
    """the installers' loop (tools/android-install.sh) with an image that brings the delta block"""

    def setUp(self):
        super().setUp()
        src = (TOP / 'tools/android-install.sh').read_text()
        self.blocks = ''.join(re.search(rf'# --- {b} begin\n(.*?)# --- {b} end', src, re.S).group(1)
                              for b in ('stage', 'user-packages', 'delta', 'install-os'))
        self.T = self.tmp / 'T'
        self.M = self.T / 'mu300root'
        sh = [s for s in self.shells if 'busybox' not in s[0]]
        if shutil.which('mksh'):
            sh.append([shutil.which('mksh')])
        self.shells = sh

    def fresh(self, vendor=True):
        shutil.rmtree(self.T, ignore_errors=True)
        self.M.mkdir(parents=True)
        v1 = image(self.tmp / 'v1.tar.gz', V1, self.tmp, 'v1')
        self.old = untar(v1, self.M / 'ubuntu')
        build(self.old, {'etc/mine.conf': ('f', 'mine\n', 0o600), 'lib/firmware/fw.bin': ('f', elf('fw'), 0o644),
                         'lib/firmware/fw.txt': ('f', 'fw text\n', 0o644)})
        image(self.T / 'mu300-ubuntu.tar.gz', V2, self.tmp, 'v2')
        if vendor:
            with tarfile.open(self.T / 'mu300-vendor-ubuntu.tar.gz', 'w:gz') as t:
                t.add(build(self.tmp / 'vendor', {'lib/firmware/fw.bin': ('f', elf('fw'), 0o644),
                                                  'lib/firmware/fw.txt': ('f', 'fw text\n', 0o644)}), arcname='.')
        self.inodes = {p: os.lstat(self.old / p).st_ino for p in ('usr/bin/prog', 'usr/lib/libbig.so', 'lib/firmware/fw.bin',
                                                                   'lib/firmware/fw.txt', 'usr/lib/python3/mod.py')}

    def install(self, shell, **env):
        e = dict(OSES='ubuntu', UPDATE='1', PWHASH='', DEFAULT_LINUX='1', BOOT_OS='ubuntu', WIPE_LEGACY='0')
        e.update(env)
        pre = ('set -e\numask 022\nsay() { echo "[device] $*"; }\nsd_unmark() { :; }\nextra_keep_vpn() { :; }\nssid=; psk=\n'
               f'T="{self.T}"; M="{self.M}"\n')
        return self.sh(shell, pre + self.blocks, **e)

    def test_the_update_links_what_did_not_change(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.install(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('are in the installed ubuntu as they are and are not written again', r.stdout)
            new = self.M / 'ubuntu'
            self.assertEqual((new / 'etc/mu300/image-version').read_text(), 'v2\n')
            self.assertEqual((new / 'usr/bin/changed').read_bytes(), elf('changed v2'))
            self.assertEqual((new / 'etc/mine.conf').read_text(), 'mine\n')
            # the binaries that did not change are the installed files themselves (the old system went: one name);
            # text, and what changed, was written
            for p in ('usr/bin/prog', 'usr/lib/libbig.so', 'lib/firmware/fw.bin'):
                self.assertEqual(os.lstat(new / p).st_ino, self.inodes[p], p)
            for p in ('lib/firmware/fw.txt', 'usr/lib/python3/mod.py'):
                self.assertNotEqual(os.lstat(new / p).st_ino, self.inodes[p], p)
            self.assertEqual(sorted(p.name for p in self.M.iterdir()), ['.mu300', 'ubuntu'])

    def test_not_enough_room_is_said_before_anything_is_written(self):
        for shell in self.each_shell():
            self.fresh()
            before = snapshot(self.old)
            self.stub('df', 'printf "Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/x 900000 899000 1000 99%% /x\\n"')
            r = self.install(shell)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('[device] not enough room on the Linux partition', r.stdout)
            self.assertIn('[device] nothing was changed', r.stdout)
            self.assertEqual(snapshot(self.old), before)
            self.assertEqual(sorted(p.name for p in self.M.iterdir()), ['ubuntu'])

    def test_the_whole_image_when_asked_with_its_room_counted(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.install(shell, MU300_DELTA=0)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn('not written again', r.stdout)
            self.assertNotEqual(os.lstat(self.M / 'ubuntu/usr/bin/prog').st_ino, self.inodes['usr/bin/prog'])
            self.fresh()
            self.stub('df', 'printf "Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/x 900000 899000 1000 99%% /x\\n"')
            r = self.install(shell, MU300_DELTA=0)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('[device] not enough room on the Linux partition', r.stdout)
            (self.stubs / 'df').unlink()

    def test_a_failed_unpack_and_a_cut_off_run_leave_nothing(self):
        for shell in self.each_shell():
            self.fresh(vendor=False)
            # left by an earlier run that was cut off: goes first
            (self.M / 'ubuntu.new/usr').mkdir(parents=True)
            (self.M / '.mu300-delta-openwrt').mkdir()
            tb = self.T / 'mu300-ubuntu.tar.gz'
            raw = gzip.decompress(tb.read_bytes())
            tb.write_bytes(gzip.compress(raw[:len(raw) - 4096 * 3]))
            before = snapshot(self.old)
            r = self.install(shell)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('[device] ubuntu: removed ubuntu.new, left by an installation that did not finish', r.stdout)
            self.assertIn('[device] ubuntu: unpacking the new system failed; ubuntu was not replaced', r.stdout)
            self.assertEqual(snapshot(self.old), before)
            self.assertEqual(sorted(p.name for p in self.M.iterdir()), ['ubuntu'])


@unittest.skipUnless(LINUX or sys.platform == 'darwin', 'needs a POSIX shell')
class SwitchModule(ShellTest):
    """the computer's installer puts the release's own switch module on the device, as its Magisk zip does"""

    def run_it(self, **env):
        self.stub('adb', 'case "$*" in\n'
                         '  *"magisk -v"*) echo 27.0 ;;\n'
                         '  push*) cp "$2" "$STUBLOG/pushed-$(basename "$3")" ;;\n'
                         '  *"echo yes"*) echo yes ;;\n'
                         'esac\nexit 0')
        r = self.sh(self.shells[0], f'sh "{TOP}/tools/install-magisk-module.sh"', **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return (self.tmp / 'pushed-module.prop').read_text()

    def test_the_release_names_the_module(self):
        prop = self.run_it(MU300_MODULE_TAG='v2026.10.16.80', MU300_MODULE_SYSTEM='openwrt ', MU300_MODULE_KERNEL='6.18')
        self.assertIn('version=v2026.10.16.80\n', prop)
        self.assertIn('versionCode=2026101680\n', prop)
        self.assertIn('Installed openwrt with kernel 6.18 (v2026.10.16.80)', prop)
        self.assertIn('id=mu300_linux_switch\n', prop)
        # the zips' own, for the same release
        zips = (TOP / 'tools/make-magisk-zips.sh').read_text()
        self.assertIn('"$I/module.prop.in" > "$S/module.prop"', zips)
        # a build of the checkout: the generic module
        prop = self.run_it()
        self.assertEqual(prop, (TOP / 'android/magisk/mu300-linux-switch/module.prop').read_text())

    def test_install_sh_and_install_ps1_pass_the_release(self):
        sh = (TOP / 'install.sh').read_text()
        self.assertIn('MU300_MODULE_TAG="${RELEASE:-}" MU300_MODULE_KERNEL="$KERNEL"', sh)
        ps = (TOP / 'install.ps1').read_text()
        self.assertIn("installer\\module.prop.in", ps)
        self.assertIn('PushUnix $PropSrc "$MTmp/module.prop"', ps)


if __name__ == '__main__':
    unittest.main()
