"""An update keeps everything the user made (docs/FINDINGS.md 85): tools/carry/mu300-carry.c, built here, and the carry
block of mu300-update, run on small fake systems - an old image with its manifest, what a user did to it, a new
image - under each shell. tools/image-manifest.py against the helper's own manifest. On real release images the same
is measured by tests/update-experiment (in a container, as root: owners, capabilities, devices)."""
import gzip
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest

CC = shutil.which('cc') or shutil.which('gcc')
LINUX = sys.platform.startswith('linux')
CARRY = None
_build = None
TOOL = TOP / 'tools' / 'image-manifest.py'


def setUpModule():
    global CARRY, _build
    if not (CC and LINUX):
        return
    _build = tempfile.mkdtemp(prefix='mu300-carry-build-')
    out = Path(_build) / 'mu300-carry'
    r = subprocess.run([CC, '-O2', '-Wall', '-Wextra', '-Werror', '-o', str(out), str(TOP / 'tools/carry/mu300-carry.c')],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('mu300-carry.c does not build without warnings:\n' + r.stderr)
    CARRY = out


def tearDownModule():
    if _build:
        shutil.rmtree(_build, ignore_errors=True)


def build(root, spec):
    """ROOT from SPEC: {path: content | ('f', content, mode) | ('l', target) | ('d', mode)}; a path ending in / is a
    directory"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for path, v in spec.items():
        p = root / path.rstrip('/')
        p.parent.mkdir(parents=True, exist_ok=True)
        if path.endswith('/') or (isinstance(v, tuple) and v[0] == 'd'):
            p.mkdir(exist_ok=True)
            if isinstance(v, tuple) and len(v) > 1:
                p.chmod(v[1])
        elif isinstance(v, tuple) and v[0] == 'l':
            os.symlink(v[1], p)
        else:
            content, mode = (v[1], v[2]) if isinstance(v, tuple) else (v, 0o644)
            p.write_bytes(content if isinstance(content, bytes) else content.encode())
            p.chmod(mode)
    return root


def carry(*args, stdin=None):
    return subprocess.run([str(CARRY)] + [str(a) for a in args], input=stdin, capture_output=True, text=True)


def snapshot(root):
    """everything below ROOT that an update must not touch: names, kinds, content, modes, times"""
    out = {}
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(d, n)
            st = os.lstat(p)
            what = os.readlink(p) if stat.S_ISLNK(st.st_mode) else Path(p).read_bytes() if stat.S_ISREG(st.st_mode) else None
            out[os.path.relpath(p, root)] = (st.st_mode, st.st_mtime_ns, st.st_nlink if stat.S_ISDIR(st.st_mode) else 0, what)
    return out


@unittest.skipUnless(CC and LINUX, 'needs a C compiler on Linux')
class Helper(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-test-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_copy_keeps_everything_of_a_file(self):
        src = build(self.tmp / 'src', {'d/file': ('f', 'data\n', 0o640), 'd/exec': ('f', '#!/bin/sh\n', 0o4755),
                                       'd/none': ('f', 'x', 0o000 if os.geteuid() == 0 else 0o400), 'link': ('l', 'd/file'), 'dangling': ('l', '/no/where'),
                                       'new\nline': 'n', 'back\\slash': 'b', ' space ': 's', '-dash': 'd', 'empty/': None})
        os.mkfifo(src / 'fifo', 0o640)
        os.link(src / 'd/file', src / 'second-name')
        with open(src / 'sparse', 'wb') as f:
            f.truncate(64 << 20)
            f.seek(32 << 20)
            f.write(b'in the middle')
        os.utime(src / 'd/file', ns=(981173106123456789, 981173106123456789))
        os.utime(src / 'link', ns=(10 ** 18, 10 ** 18), follow_symlinks=False)
        os.utime(src / 'd', ns=(981173106000000001, 981173106000000001))
        scan = carry('scan', src)
        self.assertEqual(scan.returncode, 0, scan.stderr)
        # a newline and a backslash in a name are written \n and \\: one line per entry whatever the name
        self.assertEqual(len(scan.stdout.splitlines()), len(snapshot(src)))
        self.assertIn(' new\\nline\n', scan.stdout)
        self.assertIn(' back\\\\slash\n', scan.stdout)
        paths = '\n'.join(sorted(' '.join(l.split(' ')[8:]) for l in scan.stdout.splitlines())) + '\n'
        for mode in ('copy', 'link'):
            dst = self.tmp / mode
            dst.mkdir()
            r = carry(mode, src, dst, stdin=paths)
            self.assertEqual(r.returncode, 0, r.stderr)
            for name in ('d/file', 'd/exec', 'd/none', 'link', 'dangling', 'new\nline', 'back\\slash', ' space ', '-dash', 'fifo',
                         'sparse', 'd', 'empty', 'second-name'):
                a, b = os.lstat(src / name), os.lstat(dst / name)
                self.assertEqual((a.st_mode, a.st_mtime_ns), (b.st_mode, b.st_mtime_ns), (mode, name))
                if stat.S_ISREG(a.st_mode):
                    self.assertEqual((src / name).read_bytes(), (dst / name).read_bytes(), name)
                if stat.S_ISLNK(a.st_mode):
                    self.assertEqual(os.readlink(src / name), os.readlink(dst / name))
            # two names of one file stay one file, and a hole stays a hole
            self.assertEqual(os.lstat(dst / 'd/file').st_ino, os.lstat(dst / 'second-name').st_ino)
            self.assertLess(os.lstat(dst / 'sparse').st_blocks * 512, 1 << 20)
            # by link nothing is copied: the file is the same file
            same = os.lstat(dst / 'd/exec').st_ino == os.lstat(src / 'd/exec').st_ino
            self.assertEqual(same, mode == 'link')

    def test_many_names_of_many_files(self):
        # a tree of hard links (backups): every pair stays a pair, whichever name comes first
        src = self.tmp / 'src'
        (src / 'a').mkdir(parents=True)
        (src / 'b').mkdir()
        for i in range(3000):
            (src / 'a' / str(i)).write_text(str(i))
            os.link(src / 'a' / str(i), src / 'b' / str(i))
        (self.tmp / 'dst').mkdir()
        paths = '\n'.join(['a', 'b'] + [f'{d}/{i}' for i in range(3000) for d in ('ab' if i % 2 else 'ba')]) + '\n'
        r = carry('copy', src, self.tmp / 'dst', stdin=paths)
        self.assertEqual(r.returncode, 0, r.stderr)
        for i in (0, 1, 1499, 2999):
            a, b = os.lstat(self.tmp / 'dst/a' / str(i)), os.lstat(self.tmp / 'dst/b' / str(i))
            self.assertEqual((a.st_ino, a.st_nlink), (b.st_ino, 2))
            self.assertEqual((self.tmp / 'dst/b' / str(i)).read_text(), str(i))
        self.assertEqual(len({os.lstat(self.tmp / 'dst/a' / str(i)).st_ino for i in range(3000)}), 3000)

    def test_extended_attributes(self):
        src = build(self.tmp / 'src', {'f': 'x', 'd/': None})
        try:
            os.setxattr(src / 'f', 'user.mu300', b'kept')
            os.setxattr(src / 'd', 'user.mu300', b'dir')
        except OSError:
            self.skipTest('no extended attributes on this filesystem')
        (self.tmp / 'dst').mkdir()
        self.assertEqual(carry('copy', src, self.tmp / 'dst', stdin='f\nd\n').returncode, 0)
        self.assertEqual(os.getxattr(self.tmp / 'dst/f', 'user.mu300'), b'kept')
        self.assertEqual(os.getxattr(self.tmp / 'dst/d', 'user.mu300'), b'dir')

    def test_never_writes_through_a_link_and_never_leaves_the_roots(self):
        src = build(self.tmp / 'src', {'a/f': 'user', 'top': 'user'})
        dst = build(self.tmp / 'dst', {'a': ('l', str(self.tmp / 'outside')), 'top': ('l', str(self.tmp / 'outside/top'))})
        (self.tmp / 'outside').mkdir()
        r = carry('copy', src, dst, stdin='a/f\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('never written through', r.stderr)
        self.assertEqual(os.listdir(self.tmp / 'outside'), [])
        # a link at the path itself is replaced, not followed
        self.assertEqual(carry('copy', src, dst, stdin='top\n').returncode, 0)
        self.assertEqual((dst / 'top').read_text(), 'user')
        self.assertFalse((dst / 'top').is_symlink())
        self.assertEqual(os.listdir(self.tmp / 'outside'), [])
        for bad in ('../x\n', '/etc/passwd\n', 'a/../../x\n', 'a//f\n', './a\n', '\n'):
            r = carry('copy', src, dst, stdin=bad)
            self.assertNotEqual(r.returncode, 0, bad)

    def test_a_directory_in_the_way_is_an_error_and_what_is_gone_is_passed_over(self):
        src = build(self.tmp / 'src', {'x': 'file'})
        dst = build(self.tmp / 'dst', {'x/keep': 'of the image'})
        r = carry('copy', src, dst, stdin='x\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((dst / 'x/keep').read_text(), 'of the image')
        r = carry('copy', src, self.tmp / 'dst', stdin='not/there\nx2\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('2 gone meanwhile', r.stdout)

    def test_missing_directories_are_made_like_the_ones_they_come_from(self):
        src = build(self.tmp / 'src', {'a/b/c/f': 'x'})
        (src / 'a/b').chmod(0o750)
        os.utime(src / 'a/b', ns=(10 ** 18, 10 ** 18))
        (self.tmp / 'dst').mkdir()
        self.assertEqual(carry('copy', src, self.tmp / 'dst', stdin='a/b/c/f\n').returncode, 0)
        st = os.lstat(self.tmp / 'dst/a/b')
        self.assertEqual((stat.S_IMODE(st.st_mode), st.st_mtime_ns), (0o750, 10 ** 18))

    def test_scan_stays_on_the_filesystem_and_sum_hashes(self):
        src = build(self.tmp / 'src', {'f': 'abc', 'l': ('l', 'abc'), 'd/': None})
        r = carry('sum', src, stdin='f\nl\nd\nnone\n')
        self.assertEqual(r.stdout.splitlines(), [
            'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad f',
            'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad l', '- d', '! none'])
        # another filesystem is mounted on /dev/shm: the directory is said (m) and not entered
        if os.path.ismount('/dev') and os.path.ismount('/dev/shm'):
            open('/dev/shm/mu300-test-carry', 'w').close()
            try:
                r = carry('scan', '/dev')
            finally:
                os.unlink('/dev/shm/mu300-test-carry')
            self.assertRegex(r.stdout, r'(?m)^m \d+ \d+ \d+ \d+ \d+ \d+ \d+ shm$')
            self.assertNotIn('mu300-test-carry', r.stdout)

    def test_manifest_is_the_tools_manifest_of_the_tarball(self):
        src = build(self.tmp / 'src', {'etc/mu300/image-version': 'v1\n', 'bin/sh': ('f', '#!', 0o755), 'lib': ('l', 'usr/lib'),
                                       'usr/lib/x\\x2dy.slice': 'unit', 'usr/lib/näme': 'u', 'tmp/': ('d', 0o1777),
                                       'etc/mu300/image-manifest.gz': b'not part of it'})
        os.link(src / 'bin/sh', src / 'bin/ash')
        m = carry('manifest', src, 'openwrt', 'v1')
        self.assertEqual(m.returncode, 0, m.stderr)
        self.assertEqual(m.stdout.splitlines()[0], 'mu300-image-manifest 1 openwrt v1')
        self.assertNotIn('image-manifest.gz', m.stdout)
        self.assertIn(' usr/lib/x\\\\x2dy.slice\n', m.stdout)
        tar = self.tmp / 'mu300-openwrt-rootfs.tar.gz'
        subprocess.run(['tar', '--numeric-owner', '-czf', str(tar), '-C', str(src), '.'], check=True)
        t = subprocess.run([sys.executable, str(TOOL), 'make', str(tar)], capture_output=True, text=True)
        self.assertEqual(t.returncode, 0, t.stderr)
        self.assertEqual(t.stdout, m.stdout)
        # the same manifest under the name a release publishes it with
        a = subprocess.run([sys.executable, str(TOOL), 'assets', str(self.tmp), str(self.tmp / 'assets')], capture_output=True, text=True)
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(gzip.decompress((self.tmp / 'assets/mu300-openwrt-rootfs.manifest.gz').read_bytes()).decode(), m.stdout)
        # and the check make-release.sh runs: the manifest an image carries is the one of its files
        (src / 'etc/mu300/image-manifest.gz').write_bytes(gzip.compress(m.stdout.encode()))
        subprocess.run(['tar', '--numeric-owner', '-czf', str(tar), '-C', str(src), '.'], check=True)
        self.assertEqual(subprocess.run([sys.executable, str(TOOL), 'check', str(tar)], capture_output=True).returncode, 0)
        (src / 'bin/sh').write_text('changed after the manifest')
        subprocess.run(['tar', '--numeric-owner', '-czf', str(tar), '-C', str(src), '.'], check=True)
        r = subprocess.run([sys.executable, str(TOOL), 'check', str(tar)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('bin/sh', r.stdout)


class Tool(unittest.TestCase):
    def test_union_gives_every_release_back(self):
        # the manifests of the releases before the first release with a four-field tag ship as one file per image: every distinct line once
        sys.path.insert(0, str(TOP / 'tools'))
        try:
            import importlib
            im = importlib.import_module('image-manifest')
        finally:
            sys.path.pop(0)
        a = 'mu300-image-manifest 1 openwrt v2026.10.01\nd 755 0 0 0 - etc\nf 644 0 0 1 aa etc/a\nf 644 0 0 1 bb etc/b b\n'
        b = 'mu300-image-manifest 1 openwrt v2026.10.02\nd 755 0 0 0 - etc\nf 644 0 0 1 cc etc/a\nf 644 0 0 1 bb etc/b b\n'
        c = 'mu300-image-manifest 1 openwrt v2026.10.03\nd 755 0 0 0 - etc\nf 644 0 0 1 aa etc/a\n'
        u = im.union([b, c, a])
        self.assertEqual(u.splitlines()[:2], ['mu300-image-manifests 1 openwrt', 'tags v2026.10.01 v2026.10.02 v2026.10.03'])
        self.assertIn('0,2 f 644 0 0 1 aa etc/a', u)
        self.assertIn('0-1 f 644 0 0 1 bb etc/b b', u)
        for m in (a, b, c):
            self.assertEqual(im.extract(u, m.split()[3]), m)
        with self.assertRaises(SystemExit):
            im.extract(u, 'v2026.10.04')
        with self.assertRaises(SystemExit):
            im.union([a, a.replace('openwrt', 'ubuntu-24.04')])

    def test_the_shipped_unions_are_whole(self):
        # one per image, each naming the releases it holds; a release's manifest comes out with the image's own files
        d = TOP / 'rootfs/overlay/opt/mu300/lib/image-manifests'
        self.assertEqual(sorted(p.name for p in d.iterdir()), ['openwrt-luci.gz', 'openwrt.gz', 'ubuntu-24.04.gz', 'ubuntu-26.04.gz'])
        for p in d.iterdir():
            lines = gzip.open(p, 'rt', errors='surrogateescape').read().split('\n')
            self.assertEqual(lines[0], f'mu300-image-manifests 1 {p.name[:-3]}')
            tags = lines[1].split(' ')[1:]
            # (every release before the carry block: one published before this is merged must be added, or a device on
            # it updates the old way - tools/image-manifest.py legacy and union, FINDINGS 85)
            self.assertIn('v2026.10.27', tags)
            self.assertEqual(tags, sorted(tags))
            body = [l for l in lines[2:] if l]
            self.assertTrue(all(re.match(r'^[0-9,-]+ [fdlcbp] [0-7]+ \d+ \d+ \d+ ([0-9a-f]{64}|-) .', l) for l in body), p.name)
            last = [l for l in body if re.match(rf'^([0-9,-]*[,-])?{len(tags) - 1} ', l)]
            self.assertTrue(any(l.endswith(' opt/mu300/bin/mu300-update') for l in last), p.name)


OLD_IMAGE = {
    'sbin/init': ('f', '#!/bin/sh\n', 0o755),
    'usr/lib/os-release': 'VERSION_ID="24.04"\nVERSION_CODENAME=noble\n',
    'etc/mu300/image-version': 'v1\n',
    'etc/hostname': 'mu300\n',
    'etc/ssh/sshd_config': 'Port 22\n',
    'etc/sysctl.conf': '# defaults\n',
    'etc/unchanged.conf': 'as shipped\n',
    'etc/dropped-by-v2.conf': 'old release\n',
    'etc/motd': 'welcome\n',
    'etc/overlay.conf': 'of the project\n',
    'etc/resolv.conf': ('l', '../run/resolv.conf'),
    'etc/issue.net': 'Ubuntu\n',
    'etc/skel/': None,
    'etc/skel/.profile': 'skel\n',
    'etc/ld.so.cache': 'old cache',
    'etc/systemd/system/multi-user.target.wants/mu300-telnetd.service': ('l', '/etc/systemd/system/mu300-telnetd.service'),
    'etc/systemd/system/mu300-telnetd.service': '[Service]\n',
    'etc/systemd/system/mu300-wifi.service': '[Service]\nExecStart=/opt/mu300/bin/mu300-wifi\n',
    'etc/uci-defaults/90-mu300': 'runs once and removes itself\n',
    'etc/pam.d/common-auth': 'auth required pam_unix.so\n',
    'etc/udev/rules.d/70-mu300-wcn.rules': '# the rules of v1\n',
    'etc/mu300/firewall.nft': '# the rules of the image\n',
    'opt/mu300/bin/mu300-wifi': ('f', '#!/bin/sh\necho v1\n', 0o755),
    'opt/mu300/lib/path-commands': 'mu300-update mu300-os\n',
    'usr/bin/tool': ('f', 'tool v1', 0o755),
    'usr/lib/systemd/system/ssh.service': '[Unit]\n',
    'usr/share/doc/readme': 'doc\n',
    'usr/local/bin/mu300-os': ('l', '/opt/mu300/bin/mu300-os'),
    'home/ubuntu/.bashrc': '# bashrc\n',
    'var/log/wtmp': '',
    'var/lib/dpkg/status': 'Package: base\nStatus: install ok installed\n',
    'tmp/': ('d', 0o1777),
    'proc/': None, 'run/': None, 'var/cache/': None, 'srv/': None, 'root/': None,
}
NEW_IMAGE = dict(OLD_IMAGE)
NEW_IMAGE.update({
    'etc/mu300/image-version': 'v2\n',
    'etc/sysctl.conf': '# defaults of v2\n',
    'etc/ld.so.cache': 'new cache',
    'etc/new-in-v2.conf': 'v2\n',
    'opt/mu300/bin/mu300-wifi': ('f', '#!/bin/sh\necho v2\n', 0o755),
    'etc/udev/rules.d/70-mu300-wcn.rules': '# the rules of v2\n',
    'usr/bin/tool': ('f', 'tool v2', 0o755),
    'var/lib/dpkg/status': 'Package: base\nStatus: install ok installed\n\nPackage: newpkg\nStatus: install ok installed\n',
})
del NEW_IMAGE['etc/dropped-by-v2.conf']


@unittest.skipUnless(CC and LINUX, 'needs a C compiler on Linux')
class CarryCase(ShellTest):
    """carry_system on a fake old system (an old image with its manifest, then what the user did) and a fake new image"""

    def setUp(self):
        super().setUp()
        self.n = 0

    def systems(self, user, old_image=OLD_IMAGE, new_image=NEW_IMAGE, manifest='own', image='ubuntu-24.04'):
        """(old, new): fresh for every run. USER(old) does to the installed system what a user did."""
        self.n += 1
        base = self.tmp / f'run{self.n}'
        old = build(base / 'disk/sys', old_image)
        new = build(base / 'disk/sys.new', new_image)
        m = carry('manifest', old, image, 'v1')
        self.assertEqual(m.returncode, 0, m.stderr)
        if manifest == 'own':
            (old / 'etc/mu300/image-manifest.gz').write_bytes(gzip.compress(m.stdout.encode()))
        elif manifest == 'union':
            d = new / 'opt/mu300/lib/image-manifests'
            d.mkdir(parents=True)
            lines = m.stdout.splitlines()[1:]
            other = ['f 644 0 0 1 ' + 'a' * 64 + ' etc/only-in-v0']
            text = '\n'.join([f'mu300-image-manifests 1 {image}', 'tags v0 v1'] + ['0 ' + l for l in other] +
                             ['0-1 ' + l for l in lines[:3]] + ['1 ' + l for l in lines[3:]]) + '\n'
            (d / f'{image}.gz').write_bytes(gzip.compress(text.encode()))
        user(old)
        return old, new

    def run_carry(self, shell, old, new, os_name='ubuntu', mode='copy', **env):
        return self.sh(shell, f'. "{BIN}/mu300-update"; carry_system "{old}" "{new}" {os_name} {mode}; echo "rc=$?"',
                       MU300_LIB=1, MU300_CARRY=CARRY, **env)

    def each(self, user, check, **kw):
        for shell in self.each_shell():
            old, new = self.systems(user, **{k: v for k, v in kw.items() if k in ('old_image', 'new_image', 'manifest', 'image')})
            before = snapshot(old)
            r = self.run_carry(shell, old, new, **{k: v for k, v in kw.items() if k in ('os_name', 'mode')})
            self.assertIn('rc=0\n', r.stdout, r.stdout + r.stderr)
            self.assertEqual(snapshot(old), before, 'the old system was written to')
            self.assertFalse((new / '.mu300-carry').exists() or (new / '.mu300-image').exists())
            report = (new / 'etc/mu300/update-report').read_text()
            check(old, new, report, r)


@unittest.skipUnless(CC and LINUX, 'needs a C compiler on Linux')
class Carry(CarryCase):
    def test_what_the_user_made_goes_wherever_it_is(self):
        def user(old):
            build(old, {'etc/netplan/60-user.yaml': ('f', 'network: {}\n', 0o600), 'etc/wireguard/wg0.conf': ('f', 'key', 0o600),
                        'etc/systemd/system/myapp.service': '[Service]\n',
                        'etc/systemd/system/multi-user.target.wants/myapp.service': ('l', '/etc/systemd/system/myapp.service'),
                        'var/spool/cron/crontabs/ubuntu': ('f', '* * * * * true\n', 0o600),
                        'var/lib/docker/volumes/db/_data/ibdata1': b'\x00\x01\x02', 'var/www/html/index.html': '<h1>mine</h1>',
                        'opt/myapp/bin/run': ('f', '#!/bin/sh\n', 0o755), 'usr/share/myfonts/x.ttf': b'font',
                        'usr/local/bin/mytool': ('f', '#!/bin/sh\n', 0o755), 'swapfile': ('f', b'\0' * 4096, 0o600),
                        'srv/odd/new\nline': 'n', 'srv/odd/back\\slash': 'b', 'srv/odd/ sp ace ': 's', 'srv/odd/-dash': 'd',
                        'srv/odd/$(touch pwned)': 'x', 'srv/odd/gl*b?[x]': 'g', 'srv/empty/': None,
                        'home/ubuntu/.ssh/authorized_keys': ('f', 'ssh-ed25519 AAAA\n', 0o600), 'root/.bash_history': 'ls\n',
                        'opt/mu300/bin/my-own-script': ('f', '#!/bin/sh\n', 0o755), 'mnt/data/': None})
            (old / 'home/ubuntu/.ssh').chmod(0o700)
            os.utime(old / 'srv/odd/-dash', ns=(981173106123456789, 981173106123456789))

        def check(old, new, report, r):
            for p, st in snapshot(old).items():
                if p.startswith(('etc/netplan', 'etc/wireguard', 'var/spool', 'var/lib/docker', 'var/www', 'opt/myapp', 'usr/share/myfonts',
                                 'usr/local/bin/mytool', 'swapfile', 'srv/', 'home/ubuntu/.ssh', 'root/.bash', 'mnt/data',
                                 'opt/mu300/bin/my-own', 'etc/systemd/system/myapp', 'etc/systemd/system/multi-user.target.wants/myapp')):
                    got = snapshot(new).get(p)
                    self.assertIsNotNone(got, p)
                    if not stat.S_ISDIR(st[0]):
                        self.assertEqual(got, st, p)        # kind, mode, time to the nanosecond, content
            self.assertFalse((self.tmp / 'pwned').exists() or Path('pwned').exists())
            # nothing was in the way and nothing changed for the user: the report has no list
            self.assertIn('Everything you added to the system was carried over', report)
            self.assertNotIn('update-kept', report)
            self.assertFalse((new / 'etc/mu300/update-kept').exists())
            self.assertFalse((new / 'etc/mu300/update-report.new').exists())
            # the new image is the new image
            self.assertEqual((new / 'usr/bin/tool').read_text(), 'tool v2')
            self.assertEqual((new / 'etc/mu300/image-version').read_text(), 'v2\n')
            self.assertEqual((new / 'etc/new-in-v2.conf').read_text(), 'v2\n')
            self.assertFalse((new / 'etc/dropped-by-v2.conf').exists())
        self.each(user, check)

    def test_a_changed_image_file_under_etc_is_the_users(self):
        def user(old):
            (old / 'etc/ssh/sshd_config').write_text('Port 2222\n')             # the release did not change it
            (old / 'etc/sysctl.conf').write_text('# defaults\nvm.swappiness=10\n')   # the release changed it too
            (old / 'etc/hostname').write_text('myrouter\n')
            (old / 'etc/dropped-by-v2.conf').write_text('mine now\n')           # the release no longer has it
            (old / 'home/ubuntu/.bashrc').write_text('# bashrc\nalias l=ls\n')
            (old / 'var/log/wtmp').write_text('logins')

        def check(old, new, report, r):
            self.assertEqual((new / 'etc/ssh/sshd_config').read_text(), 'Port 2222\n')
            self.assertEqual((new / 'etc/sysctl.conf').read_text(), '# defaults\nvm.swappiness=10\n')
            self.assertEqual((new / 'etc/hostname').read_text(), 'myrouter\n')
            self.assertEqual((new / 'etc/dropped-by-v2.conf').read_text(), 'mine now\n')
            self.assertEqual((new / 'home/ubuntu/.bashrc').read_text(), '# bashrc\nalias l=ls\n')
            self.assertEqual((new / 'var/log/wtmp').read_text(), 'logins')
            # both changed it: the release's version is set aside and the report says so; the others are not in it
            self.assertEqual((new / 'etc/mu300/update-kept/v1/image/etc/sysctl.conf').read_text(), '# defaults of v2\n')
            self.assertRegex(report, r'Yours is in use where the new release has another version.*\n  /etc/sysctl\.conf\n')
            self.assertIn('new release no longer has this file:\n  /etc/dropped-by-v2.conf\n', report)
            self.assertNotIn('sshd_config', report)
            self.assertNotIn('hostname', report)
            self.assertEqual(os.listdir(new / 'etc/mu300/update-kept/v1/image/etc'), ['sysctl.conf'])
            self.assertTrue((new / 'etc/mu300/update-report.new').exists())
            self.assertEqual(stat.S_IMODE((new / 'etc/mu300/update-kept').stat().st_mode), 0o700)
            # what the user did not touch is the release's
            self.assertEqual((new / 'etc/unchanged.conf').read_text(), 'as shipped\n')
        self.each(user, check)

    def test_programs_are_the_releases_and_the_users_version_is_set_aside(self):
        def user(old):
            (old / 'usr/bin/tool').write_text('tool v1, patched by hand')                # outside /etc
            (old / 'usr/lib/systemd/system/ssh.service').write_text('[Unit]\n# edited\n')    # ... and unchanged by v2
            (old / 'opt/mu300/bin/mu300-wifi').write_text('#!/bin/sh\necho hacked\n')    # the project's own
            (old / 'etc/systemd/system/mu300-wifi.service').write_text('[Service]\nExecStart=/bin/true\n')
            (old / 'usr/local/bin/mu300-os').unlink()
            (old / 'usr/local/bin/mu300-os').write_text('#!/bin/sh\n# my wrapper\n')     # a command the images link
            (old / 'etc/pam.d/common-auth').write_text('auth required pam_unix.so\nauth required pam_extra.so\n')
            # what the project put under /etc as a setting stays the user's when they changed it
            (old / 'etc/udev/rules.d/70-mu300-wcn.rules').write_text('# the rules of v1, edited\n')
            (old / 'etc/mu300/firewall.nft').write_text('# my rules\n')
            (old / 'etc/overlay.conf').write_text('of the project, with a line of mine\n')

        def check(old, new, report, r):
            kept = new / 'etc/mu300/update-kept/v1/user'
            self.assertEqual((new / 'etc/udev/rules.d/70-mu300-wcn.rules').read_text(), '# the rules of v1, edited\n')
            self.assertEqual((new / 'etc/mu300/update-kept/v1/image/etc/udev/rules.d/70-mu300-wcn.rules').read_text(), '# the rules of v2\n')
            self.assertEqual((new / 'etc/mu300/firewall.nft').read_text(), '# my rules\n')
            self.assertEqual((new / 'etc/overlay.conf').read_text(), 'of the project, with a line of mine\n')
            self.assertEqual((new / 'usr/bin/tool').read_text(), 'tool v2')
            self.assertEqual((kept / 'usr/bin/tool').read_text(), 'tool v1, patched by hand')
            self.assertEqual((new / 'usr/lib/systemd/system/ssh.service').read_text(), '[Unit]\n')
            self.assertEqual((kept / 'usr/lib/systemd/system/ssh.service').read_text(), '[Unit]\n# edited\n')
            self.assertEqual((new / 'opt/mu300/bin/mu300-wifi').read_text(), '#!/bin/sh\necho v2\n')
            self.assertEqual((kept / 'opt/mu300/bin/mu300-wifi').read_text(), '#!/bin/sh\necho hacked\n')
            self.assertEqual((new / 'etc/systemd/system/mu300-wifi.service').read_text(), OLD_IMAGE['etc/systemd/system/mu300-wifi.service'])
            self.assertTrue((new / 'usr/local/bin/mu300-os').is_symlink())
            self.assertEqual((kept / 'usr/local/bin/mu300-os').read_text(), '#!/bin/sh\n# my wrapper\n')
            self.assertEqual((new / 'etc/pam.d/common-auth').read_text(), 'auth required pam_unix.so\n')
            self.assertIn('pam_extra', (kept / 'etc/pam.d/common-auth').read_text())
            for p in ('/usr/bin/tool', '/usr/lib/systemd/system/ssh.service', '/opt/mu300/bin/mu300-wifi',
                      '/etc/systemd/system/mu300-wifi.service', '/usr/local/bin/mu300-os', '/etc/pam.d/common-auth'):
                self.assertRegex(report, r'The version of the release is in use; yours is in /etc/mu300/update-kept/v1/user:\n(  /.*\n)*  '
                                 + re.escape(p) + '\n')
        self.each(user, check)

    def test_what_the_user_deleted(self):
        def user(old):
            for p in ('etc/motd',                                   # a file under /etc: stays deleted
                      'etc/overlay.conf',                           # also one the project put there
                      'etc/systemd/system/multi-user.target.wants/mu300-telnetd.service',     # a service disabled: stays
                      'etc/uci-defaults/90-mu300',                  # removed itself when it ran: must run again
                      'home/ubuntu/.bashrc',                        # the user's own place: stays deleted
                      'usr/share/doc/readme',                       # a program's file: back, without a word
                      'opt/mu300/bin/mu300-wifi'):                  # the project's own: back
                (old / p).unlink()
            shutil.rmtree(old / 'etc/skel')                         # a whole directory

        def check(old, new, report, r):
            for p in ('etc/motd', 'etc/overlay.conf', 'etc/systemd/system/multi-user.target.wants/mu300-telnetd.service',
                      'home/ubuntu/.bashrc', 'etc/skel'):
                self.assertFalse(os.path.lexists(new / p), p)
                self.assertRegex(report, r'Deleted again, as you had deleted them:\n(  /.*\n)*  /' + re.escape(p) + '\n')
            for p in ('etc/uci-defaults/90-mu300', 'usr/share/doc/readme', 'opt/mu300/bin/mu300-wifi'):
                self.assertTrue((new / p).exists(), p)
            self.assertRegex(report, r'Back, though you had deleted them.*\n(  /.*\n)*  /opt/mu300/bin/mu300-wifi\n')
            self.assertNotIn('uci-defaults', report)
            self.assertNotIn('readme', report)
            self.assertNotIn('.profile', report)                    # what was in a deleted directory is not listed again
        self.each(user, check)

    def test_another_kind_of_file_than_the_image_had(self):
        def user(old):
            (old / 'etc/issue.net').unlink()
            build(old, {'etc/issue.net/note': 'a directory where the image has a file\n'})
            (old / 'etc/resolv.conf').unlink()
            (old / 'etc/resolv.conf').write_text('nameserver 9.9.9.9\n')        # a file where the image has a link
            shutil.rmtree(old / 'etc/skel')
            os.symlink('/srv/skel', old / 'etc/skel')                           # a link where the image has a directory

        def check(old, new, report, r):
            self.assertEqual((new / 'etc/issue.net/note').read_text(), 'a directory where the image has a file\n')
            self.assertEqual((new / 'etc/resolv.conf').read_text(), 'nameserver 9.9.9.9\n')
            self.assertFalse((new / 'etc/resolv.conf').is_symlink())
            self.assertEqual(os.readlink(new / 'etc/skel'), '/srv/skel')
            self.assertNotIn('.profile', report)
        self.each(user, check)

    def test_where_the_release_put_something_else(self):
        # the user has a directory with files where the new release has a file, and a file where it has a directory
        new_image = dict(NEW_IMAGE)
        new_image.update({'usr/share/thing': 'a file in v2', 'usr/share/other/of-v2': 'v2', 'etc/app.conf': 'a default of v2\n',
                          'usr/share/doc': ('l', '/usr/share/elsewhere')})
        del new_image['usr/share/doc/readme']

        def user(old):
            build(old, {'usr/share/thing/mine': 'user', 'usr/share/thing/sub/more': 'user', 'usr/share/other': 'user file',
                        'etc/app.conf': 'the setting of the user\n', 'usr/share/doc/notes': 'in a directory of the image'})

        def check(old, new, report, r):
            kept = new / 'etc/mu300/update-kept/v1'
            # outside /etc the release's is in use, and everything of the user's is set aside
            self.assertEqual((new / 'usr/share/thing').read_text(), 'a file in v2')
            self.assertEqual((kept / 'user/usr/share/thing/mine').read_text(), 'user')
            self.assertEqual((kept / 'user/usr/share/thing/sub/more').read_text(), 'user')
            self.assertEqual((new / 'usr/share/other/of-v2').read_text(), 'v2')
            self.assertEqual((kept / 'user/usr/share/other').read_text(), 'user file')
            # a directory of the image that the release made a link: nothing is written through it
            self.assertTrue((new / 'usr/share/doc').is_symlink())
            self.assertEqual((kept / 'user/usr/share/doc/notes').read_text(), 'in a directory of the image')
            self.assertRegex(report, r'The release has a file or a link where this directory was.*\n  /usr/share/doc\n')
            # under /etc the user's is in use, and the release's is set aside
            self.assertEqual((new / 'etc/app.conf').read_text(), 'the setting of the user\n')
            self.assertEqual((kept / 'image/etc/app.conf').read_text(), 'a default of v2\n')
            self.assertIn('  /usr/share/thing\n', report)
            self.assertIn('  /usr/share/other\n', report)
            self.assertIn('  /etc/app.conf\n', report)
        self.each(user, check, new_image=new_image)

    def test_never_carried(self):
        def user(old):
            build(old, {'tmp/scratch': 'x', 'var/cache/apt/archives/x.deb': 'x', 'proc/1/cmdline': 'x', 'run/lock/x': 'x',
                        'usr/lib/modules/6.18.1/extra/old.ko': 'ko', 'usr/lib/python3/dist-packages/__pycache__/x.pyc': 'pyc',
                        'var/lib/dpkg/info/base.list': '/.\n', 'var/lib/apt/extended_states': '', 'etc/alternatives/editor': ('l', '/usr/bin/vi'),
                        'etc/mu300/user-packages/wanted': 'stale\n'})
            (old / 'etc/ld.so.cache').write_text('a cache of the old libraries')
            (old / 'var/lib/dpkg/status').write_text('Package: base\nStatus: install ok installed\n\nPackage: mine\nStatus: install ok installed\n')
            s = socket.socket(socket.AF_UNIX)
            cwd = os.getcwd()
            os.chdir(old / 'srv')
            s.bind('sock')
            os.chdir(cwd)
            s.close()

        def check(old, new, report, r):
            for p in ('tmp/scratch', 'var/cache/apt', 'proc/1', 'run/lock', 'usr/lib/modules/6.18.1', 'srv/sock',
                      'usr/lib/python3/dist-packages/__pycache__', 'var/lib/dpkg/info/base.list', 'etc/alternatives/editor'):
                self.assertFalse(os.path.lexists(new / p), p)
            self.assertEqual((new / 'etc/ld.so.cache').read_text(), 'new cache')
            self.assertEqual((new / 'var/lib/dpkg/status').read_text(), NEW_IMAGE['var/lib/dpkg/status'])
            self.assertFalse((new / 'etc/mu300/update-kept').exists())
            # the package mine, in no image, is written down to be installed again; the record of the update before
            # is not carried as a file, but what it still waited for stays wanted (upk_record)
            self.assertEqual((new / 'etc/mu300/user-packages/wanted').read_text(), 'mine\nstale\n')
            self.assertIn('Packages installed on the device:\n  mine', report)
        self.each(user, check)

    def test_packages_are_the_package_managers(self):
        md5 = '5d41402abc4b2a76b9719d911017c592'      # of "hello"

        def user(old):
            # a package the user installed: its program is not carried (it is installed again), its changed
            # configuration and its data are; a file a package upgrade changed is not a change of the user's
            build(old, {'usr/bin/userpkg': ('f', 'elf', 0o755), 'usr/share/userpkg/data': 'pkg', 'etc/userpkg/userpkg.conf': 'option=changed\n',
                        'var/lib/userpkg/state.db': b'\x00\x01', 'etc/init.d/userpkg': ('f', '#!/bin/sh\n', 0o755),
                        'var/lib/dpkg/info/userpkg.list': '/.\n/usr\n/usr/bin\n/usr/bin/userpkg\n/usr/share/userpkg\n/usr/share/userpkg/data\n'
                                                          '/etc/userpkg\n/etc/userpkg/userpkg.conf\n/etc/init.d/userpkg\n',
                        'var/lib/dpkg/info/base.list': '/.\n/usr/bin/tool\n/etc/motd\n',
                        'var/lib/dpkg/info/base.md5sums': f'{md5}  usr/bin/tool\n'})
            (old / 'var/lib/dpkg/status').write_text(
                'Package: base\nStatus: install ok installed\n\nPackage: userpkg\nStatus: install ok installed\n'
                'Conffiles:\n /etc/userpkg/userpkg.conf d41d8cd98f00b204e9800998ecf8427e\nDescription: x\n')
            (old / 'usr/bin/tool').write_text('hello')      # as dpkg's own sums have it: upgraded with apt

        def check(old, new, report, r):
            self.assertFalse((new / 'usr/bin/userpkg').exists())
            self.assertFalse((new / 'usr/share/userpkg').exists())
            self.assertFalse((new / 'etc/init.d/userpkg').exists())
            self.assertEqual((new / 'etc/userpkg/userpkg.conf').read_text(), 'option=changed\n')
            self.assertEqual((new / 'var/lib/userpkg/state.db').read_bytes(), b'\x00\x01')
            self.assertEqual((new / 'usr/bin/tool').read_text(), 'tool v2')
            self.assertFalse((new / 'etc/mu300/update-kept').exists())
            self.assertIn('3 files of packages are not carried', report)
            self.assertEqual((new / 'etc/mu300/user-packages/wanted').read_text(), 'userpkg\n')
        self.each(user, check)

    def test_a_package_the_user_removed_is_back_with_its_files(self):
        new_image = dict(NEW_IMAGE)
        new_image.update({'var/lib/dpkg/info/extra.list': '/.\n/etc/motd\n/usr/share/doc/readme\n',
                          'var/lib/dpkg/status': 'Package: base\nStatus: install ok installed\n\nPackage: extra\nStatus: install ok installed\n'})

        def user(old):
            (old / 'etc/motd').unlink()
            (old / 'usr/share/doc/readme').unlink()

        def check(old, new, report, r):
            self.assertTrue((new / 'etc/motd').exists())
            self.assertIn('Packages you had removed are part of the image, and back (their services are not enabled):\n  extra\n', report)
            self.assertNotIn('Deleted again', report)
        self.each(user, check, new_image=new_image)

    def test_accounts(self):
        old_image = dict(OLD_IMAGE)
        old_image.update({'etc/passwd': 'root:x:0:0::/root:/bin/sh\nubuntu:x:1000:1000::/home/ubuntu:/bin/bash\nbt:x:105:105::/:/bin/false\n',
                          'etc/group': 'root:x:0:\nubuntu:x:1000:\nbt:x:105:\n', 'etc/shadow': 'root:*:1::::::\nubuntu:$6$image:1::::::\nbt:!:1::::::\n',
                          'etc/gshadow': 'root:*::\nubuntu:!::\nbt:!::\n', 'etc/subuid': 'ubuntu:100000:65536\n'})
        new_image = dict(NEW_IMAGE)
        # the new release numbers bt differently, has an account vnd the old one did not, and uses 990 for it
        new_image.update({'etc/passwd': 'root:x:0:0::/root:/bin/sh\nubuntu:x:1000:1000::/home/ubuntu:/bin/bash\nbt:x:988:988::/:/bin/false\nvnd:x:990:990::/:/bin/false\n',
                          'etc/group': 'root:x:0:\nubuntu:x:1000:\nbt:x:988:\nvnd:x:990:\n',
                          'etc/shadow': 'root:*:1::::::\nubuntu:$6$image:1::::::\nbt:!:1::::::\nvnd:!:1::::::\n',
                          'etc/gshadow': 'root:*::\nubuntu:!::\nbt:!::\nvnd:!::\n', 'etc/subuid': 'ubuntu:100000:65536\n'})

        def user(old):
            with open(old / 'etc/passwd', 'a') as f:
                f.write('kaan:x:1001:1001::/home/kaan:/bin/bash\nmyapp:x:777:777::/var/lib/myapp:/bin/false\nclash:x:990:990::/:/bin/false\n')
            with open(old / 'etc/group', 'a') as f:
                f.write('kaan:x:1001:\nmyapp:x:777:\nclash:x:990:\n')
            with open(old / 'etc/gshadow', 'a') as f:
                f.write('kaan:!::\nmyapp:!::\nclash:!::\n')
            (old / 'etc/shadow').write_text('root:*:1::::::\nubuntu:$6$mine:2::::::\nbt:!:1::::::\nkaan:$6$kaan:2::::::\nmyapp:!:2::::::\nclash:!:2::::::\n')
            with open(old / 'etc/subuid', 'a') as f:
                f.write('kaan:165536:65536\n')

        def check(old, new, report, r):
            passwd = (new / 'etc/passwd').read_text().splitlines()
            shadow = (new / 'etc/shadow').read_text().splitlines()
            # the users of the device and their passwords (merge_accounts), the release's own accounts
            self.assertIn('kaan:x:1001:1001::/home/kaan:/bin/bash', passwd)
            self.assertIn('ubuntu:$6$mine:2::::::', shadow)
            self.assertIn('kaan:$6$kaan:2::::::', shadow)
            self.assertIn('vnd:x:990:990::/:/bin/false', passwd)
            self.assertIn('bt:x:988:988::/:/bin/false', passwd)
            # a system account added on the device comes along under its number
            self.assertIn('myapp:x:777:777::/var/lib/myapp:/bin/false', passwd)
            self.assertIn('myapp:!:2::::::', shadow)
            self.assertIn('myapp:x:777:', (new / 'etc/group').read_text().splitlines())
            self.assertIn('myapp:!::', (new / 'etc/gshadow').read_text().splitlines())
            # one whose number the new system gave away gets the highest free one, for user and group
            self.assertIn('clash:x:999:999::/:/bin/false', passwd)
            self.assertIn('clash:x:999:', (new / 'etc/group').read_text().splitlines())
            self.assertIn('clash:!:2::::::', shadow)
            self.assertEqual(len(passwd), len(set(l.split(':')[0] for l in passwd)))
            self.assertEqual((new / 'etc/subuid').read_text(), 'ubuntu:100000:65536\nkaan:165536:65536\n')
            self.assertNotIn('could not be carried', report)
        self.each(user, check, old_image=old_image, new_image=new_image)
        # what the accounts own follows the number: the map mu300-carry gets
        for shell in self.each_shell():
            old, new = self.systems(user, old_image=old_image, new_image=new_image)
            (new / '.mu300-carry').mkdir()
            r = self.sh(shell, f'. "{BIN}/mu300-update"; CARRY_T="{new}/.mu300-carry"; carry_idmap "{old}" "{new}" > "$CARRY_T/idmap"; '
                               f'carry_accounts_plan "{old}" "{new}"; sort "$CARRY_T/idmap"', MU300_LIB=1)
            self.assertEqual(r.stdout.splitlines(), ['g 105 988', 'g 990 999', 'u 105 988', 'u 990 999'], r.stderr)

    @unittest.skipUnless(os.geteuid() == 0, 'owners need root')
    def test_owners_follow_the_accounts(self):
        old_image = dict(OLD_IMAGE)
        old_image.update({'etc/passwd': 'root:x:0:0::/root:/bin/sh\nbt:x:105:105::/:/bin/false\n', 'etc/group': 'root:x:0:\nbt:x:105:\n',
                          'etc/shadow': 'root:*:1::::::\nbt:!:1::::::\n'})
        new_image = dict(NEW_IMAGE)
        new_image.update({'etc/passwd': 'root:x:0:0::/root:/bin/sh\nbt:x:988:988::/:/bin/false\n', 'etc/group': 'root:x:0:\nbt:x:988:\n',
                          'etc/shadow': 'root:*:1::::::\nbt:!:1::::::\n'})

        def user(old):
            build(old, {'var/lib/bt/keys': 'k', 'srv/mine': 'm'})
            os.chown(old / 'var/lib/bt/keys', 105, 105)
            os.chown(old / 'srv/mine', 4242, 4343)

        def check(old, new, report, r):
            st = os.lstat(new / 'var/lib/bt/keys')
            self.assertEqual((st.st_uid, st.st_gid), (988, 988))
            st = os.lstat(new / 'srv/mine')
            self.assertEqual((st.st_uid, st.st_gid), (4242, 4343))
        self.each(user, check, old_image=old_image, new_image=new_image)

    def test_the_manifest_of_an_older_release_comes_from_the_new_image(self):
        # a system from before images carried their manifest: the new image has it, by release
        def user(old):
            (old / 'etc/hostname').write_text('myrouter\n')
            build(old, {'etc/mine.conf': 'm'})
            (old / 'etc/motd').unlink()

        def check(old, new, report, r):
            self.assertEqual((new / 'etc/hostname').read_text(), 'myrouter\n')
            self.assertEqual((new / 'etc/mine.conf').read_text(), 'm')
            self.assertFalse((new / 'etc/motd').exists())           # only a manifest can tell a deletion
            self.assertNotIn('no list of its files', report)
        self.each(user, check, manifest='union')

    def test_a_manifest_of_another_release_is_not_used(self):
        # the old system says v1, the manifest in it says another release (a system updated by an updater from
        # before the first release with a four-field tag kept the old file): not trusted, and the update goes on as without one
        def user(old):
            m = gzip.decompress((old / 'etc/mu300/image-manifest.gz').read_bytes()).decode().replace(' v1\n', ' v0\n', 1)
            (old / 'etc/mu300/image-manifest.gz').write_bytes(gzip.compress(m.encode()))
            build(old, {'etc/mine.conf': 'm'})

        def check(old, new, report, r):
            self.assertIn('no list of the files of v1 here', r.stdout)
            self.assertIn('has no list of its files here', report)
            self.assertEqual((new / 'etc/mine.conf').read_text(), 'm')
        self.each(user, check)

    def test_only_the_manifest_of_this_image_and_this_release_is_used(self):
        # What is checked is what is used: the release the old system says it is, read once, picks the manifest; the
        # file is copied once into the working directory and only that copy is read. A manifest that names another
        # image or release, that is a link, or that has a line which is no manifest line (a path out of the system)
        # is refused whole, and the update goes on as without one - nothing is decided by it.
        def other_image(old):
            m = gzip.decompress((old / 'etc/mu300/image-manifest.gz').read_bytes()).decode().replace(' ubuntu-24.04 v1\n', ' openwrt v1\n', 1)
            (old / 'etc/mu300/image-manifest.gz').write_bytes(gzip.compress(m.encode()))

        def a_link(old):
            (old / 'etc/mu300/image-manifest.gz').rename(old / 'srv/elsewhere.gz')
            os.symlink('/srv/elsewhere.gz', old / 'etc/mu300/image-manifest.gz')
            os.symlink(str(old / 'srv/elsewhere.gz'), old / 'etc/mu300/also-a-link')

        def a_bad_line(old):
            m = gzip.decompress((old / 'etc/mu300/image-manifest.gz').read_bytes()).decode() + 'f 644 0 0 1 aa ../../etc/shadow\n'
            (old / 'etc/mu300/image-manifest.gz').write_bytes(gzip.compress(m.encode()))

        def not_gzip(old):
            (old / 'etc/mu300/image-manifest.gz').write_bytes(b'mu300-image-manifest 1 ubuntu-24.04 v1\n')

        def check(old, new, report, r):
            self.assertIn('no list of the files of v1 here', r.stdout)
            self.assertIn('has no list of its files here', report)
            self.assertTrue((new / 'etc/motd').exists())        # a manifest would have said: deleted by the user
        for user in (other_image, a_link, a_bad_line, not_gzip):
            def with_deletion(old, user=user):
                user(old)
                (old / 'etc/motd').unlink()
            with self.subTest(user.__name__):
                self.each(with_deletion, check)
        # the same for the manifests the new image brings: one file per image, and the file must say which
        for shell in self.each_shell():
            old, new = self.systems(lambda old: (old / 'etc/motd').unlink(), manifest='union')
            u = new / 'opt/mu300/lib/image-manifests/ubuntu-24.04.gz'
            u.write_bytes(gzip.compress(gzip.decompress(u.read_bytes()).replace(b'mu300-image-manifests 1 ubuntu-24.04', b'mu300-image-manifests 1 openwrt', 1)))
            r = self.run_carry(shell, old, new)
            self.assertIn('rc=0\n', r.stdout, r.stderr)
            self.assertIn('no list of the files of v1 here', r.stdout)
        src = (BIN / 'mu300-update').read_text()
        fn = src[src.index('carry_manifest() {'):src.index('# carry_manifest_ok FILE')]
        self.assertEqual(fn.count('"$_cm"'), 6)                 # each of the two files: is it one, not a link, copied once
        self.assertNotIn('gzip -dc "$_cm"', fn)
        self.assertNotIn('image-version', fn)                   # the release is the one the caller read

    def test_what_names_the_users_files_is_roots_alone(self):
        # the report, the directory of what was set aside (also one that came along from an earlier update with
        # another mode), and a file in it keeps the mode it had
        def user(old):
            build(old, {'etc/mu300/update-kept/v0/user/etc/x': 'from an earlier update\n', 'etc/mu300/update-report': 'earlier\n'})
            (old / 'etc/mu300/update-kept').chmod(0o755)
            (old / 'etc/mu300/update-report').chmod(0o644)
            (old / 'usr/bin/tool').write_text('a secret patched in')
            (old / 'usr/bin/tool').chmod(0o600)

        def check(old, new, report, r):
            mode = lambda p: stat.S_IMODE(os.lstat(new / p).st_mode)
            self.assertEqual(mode('etc/mu300/update-report'), 0o600)
            self.assertEqual(mode('etc/mu300/update-kept'), 0o700)
            self.assertEqual((new / 'etc/mu300/update-kept/v0/user/etc/x').read_text(), 'from an earlier update\n')
            self.assertEqual(mode('etc/mu300/update-kept/v1/user/usr/bin/tool'), 0o600)
            self.assertEqual(mode('usr/bin/tool'), 0o600)       # and the release's file in its place is no wider
            self.assertFalse((new / '.mu300-carry').exists())
        self.each(user, check)
        src = (BIN / 'mu300-update').read_text()
        # the working directory, which holds the lists of the user's files while the update runs
        self.assertIn('( umask 077; mkdir "$CARRY_T" ) && chmod 700 "$CARRY_T"', src)
        self.assertIn('( umask 077; cat "$CARRY_T/report" > "$_cr" )', src)
        # the installers pass the block's output on from a file on the Linux filesystem, never from /data/local/tmp
        ai = (TOP / 'tools/android-install.sh').read_text()
        self.assertIn('clog=$M/$os.new.carry-log', ai)
        self.assertNotIn('$T/mu300-carry', ai)

    def test_without_a_manifest(self):
        def user(old):
            build(old, {'etc/netplan/60-user.yaml': 'network: {}\n', 'var/www/html/index.html': '<h1>mine</h1>', 'srv/data': 'd',
                        'opt/mu300/bin/of-an-old-release': 'x'})
            (old / 'etc/hostname').write_text('myrouter\n')                 # a place an update always kept
            (old / 'etc/sysctl.conf').write_text('# defaults\nvm.swappiness=10\n')     # what protects the device
            (old / 'etc/issue.net').write_text('Ubuntu, mine\n')
            (old / 'etc/motd').unlink()

        def check(old, new, report, r):
            self.assertIn('no list of the files of v1 here', r.stdout)
            kept = new / 'etc/mu300/update-kept/v1/user'
            # what the new image does not have is carried, the places of the old list are the user's
            self.assertEqual((new / 'etc/netplan/60-user.yaml').read_text(), 'network: {}\n')
            self.assertEqual((new / 'var/www/html/index.html').read_text(), '<h1>mine</h1>')
            self.assertEqual((new / 'srv/data').read_text(), 'd')
            self.assertEqual((new / 'etc/hostname').read_text(), 'myrouter\n')
            # a file that differs from the new image's: the new one is in use, the old one is set aside - the user's
            # change and the old release's own file alike, they cannot be told apart
            self.assertEqual((new / 'etc/sysctl.conf').read_text(), '# defaults\nvm.swappiness=10\n')
            self.assertEqual((new / 'etc/issue.net').read_text(), 'Ubuntu\n')
            self.assertEqual((kept / 'etc/issue.net').read_text(), 'Ubuntu, mine\n')
            self.assertEqual((kept / 'usr/bin/tool').read_text(), 'tool v1')
            self.assertEqual((kept / 'etc/dropped-by-v2.conf').read_text() if (kept / 'etc/dropped-by-v2.conf').exists()
                             else (new / 'etc/dropped-by-v2.conf').read_text(), 'old release\n')
            # the project's own are never brought back into place
            self.assertFalse((new / 'opt/mu300/bin/of-an-old-release').exists())
            self.assertEqual((kept / 'opt/mu300/bin/of-an-old-release').read_text(), 'x')
            self.assertEqual((new / 'opt/mu300/bin/mu300-wifi').read_text(), '#!/bin/sh\necho v2\n')
            self.assertTrue((new / 'etc/motd').exists())                    # a deletion cannot be told
            self.assertIn('has no list of its files here', report)
            self.assertIn('  /etc/issue.net\n', report)
            self.assertTrue((new / 'etc/mu300/update-report.new').exists())
        self.each(user, check, manifest='none')

    def test_by_link_nothing_is_copied(self):
        def user(old):
            build(old, {'srv/big': 'data', 'etc/mine.conf': 'm'})
            (old / 'usr/bin/tool').write_text('patched')

        def check(old, new, report, r):
            self.assertEqual(os.lstat(new / 'srv/big').st_ino, os.lstat(old / 'srv/big').st_ino)
            self.assertEqual(os.lstat(new / 'etc/mu300/update-kept/v1/user/usr/bin/tool').st_ino, os.lstat(old / 'usr/bin/tool').st_ino)
            self.assertIn('the previous system is not kept', report)
        self.each(user, check, mode='link')

    def test_not_enough_room_changes_nothing(self):
        def user(old):
            build(old, {'srv/big': 'x' * 100000})
        self.stub('df', 'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"; echo "/dev/x 100000 80000 20000 80% /"')
        for shell in self.each_shell():
            old, new = self.systems(user)
            before = snapshot(old)
            r = self.run_carry(shell, old, new)
            self.assertIn('rc=3\n', r.stdout, r.stdout + r.stderr)
            self.assertRegex(r.stderr, r'not enough room .*: \d+ MB needed, 19 MB free')
            self.assertEqual(snapshot(old), before)
            self.assertFalse((new / 'srv/big').exists())
            self.assertFalse((new / '.mu300-carry').exists())
            # by link no room is needed for the user's files
            old, new = self.systems(user)
            r = self.run_carry(shell, old, new, mode='link')
            self.assertIn('rc=0\n', r.stdout, r.stdout + r.stderr)

    def test_the_report_stays_and_is_shown_once(self):
        def user(old):
            build(old, {'etc/mu300/update-report': '==== sys: v0 -> v1 ====\nan earlier update\n'})
            (old / 'usr/bin/tool').write_text('patched')

        def check(old, new, report, r):
            self.assertRegex(report, r'(?s)^==== ubuntu: v1 -> v2, .*/usr/bin/tool.*==== sys: v0 -> v1 ====\nan earlier update\n$')
            self.assertEqual(stat.S_IMODE((new / 'etc/mu300/update-report').stat().st_mode), 0o600)
            self.assertIn('==== ubuntu: v1 -> v2', r.stdout)                # shown when the update runs
        self.each(user, check)
        for shell in self.each_shell():
            root = self.tmp / 'root'
            shutil.rmtree(root, ignore_errors=True)
            build(root, {'etc/mu300/update-report.new': '', 'run/motd.d/': None})
            r = self.sh(shell, f'. "{BIN}/mu300-update"; latest_release() {{ :; }}; notice; ls "{root}/run/motd.d"',
                        MU300_LIB=1, MU300_SYSROOT=root, MU300_MOTD_DIR=root / 'run/motd.d', MU300_NOTICE=root / 'run/note')
            self.assertIn('61-mu300-update-report', r.stdout, r.stderr)
            self.assertIn('mu300-update report', (root / 'run/motd.d/61-mu300-update-report').read_text())
            # mu300-update report shows it, and the note goes
            (root / 'etc/mu300/update-report').write_text('==== ubuntu: v1 -> v2 ====\nthe report\n')
            r = self.sh(shell, f'. "{BIN}/mu300-update"; report; ls "{root}/run/motd.d" "{root}/etc/mu300"',
                        MU300_LIB=1, MU300_SYSROOT=root, MU300_MOTD_DIR=root / 'run/motd.d')
            self.assertIn('==== ubuntu: v1 -> v2 ====\nthe report\n', r.stdout)
            self.assertNotIn('61-mu300-update-report', r.stdout)
            self.assertNotIn('update-report.new', r.stdout)
            r = self.sh(shell, f'. "{BIN}/mu300-update"; latest_release() {{ :; }}; notice; ls "{root}/run/motd.d"',
                        MU300_LIB=1, MU300_SYSROOT=root, MU300_MOTD_DIR=root / 'run/motd.d', MU300_NOTICE=root / 'run/note')
            self.assertNotIn('61-mu300-update-report', r.stdout)

    def test_openwrt(self):
        old_image = {'bin/busybox': ('f', '#!', 0o755), 'etc/mu300/image-version': 'v1\n', 'etc/config/network': "config interface 'lan'\n",
                     'etc/config/system': "config system\n", 'etc/rc.d/S50cron': ('l', '../init.d/cron'), 'etc/init.d/cron': ('f', '#!/bin/sh\n', 0o755),
                     'etc/init.d/dnsmasq': ('f', '#!/bin/sh\n# v1\n', 0o755), 'etc/banner': 'OpenWrt\n', 'etc/uci-defaults/90-mu300': 'once\n',
                     'www/index.html': '<html>', 'etc/apk/world': 'base\n', 'lib/modules/5.4/a.ko': 'ko', 'etc/sysctl.conf': '# d\n',
                     'lib/apk/db/installed': 'P:base\nF:etc\nR:banner\nR:sysctl.conf\nF:etc/init.d\nR:dnsmasq\nF:www\nR:index.html\n',
                     'lib/apk/packages/base.conffiles': '/etc/sysctl.conf\n/etc/config/\n', 'opt/mu300/lib/path-commands': 'mu300-update\n'}
        new_image = dict(old_image)
        new_image.update({'etc/mu300/image-version': 'v2\n', 'etc/init.d/dnsmasq': ('f', '#!/bin/sh\n# v2\n', 0o755), 'etc/config/sqm': 'config queue\n'})

        def user(old):
            (old / 'etc/config/network').write_text("config interface 'lan'\n\toption ipaddr '10.0.0.1'\n")
            build(old, {'etc/config/custom': 'config x\n', 'etc/crontabs/root': '0 4 * * * reboot\n', 'www/mysite/index.html': '<h1>',
                        'etc/hotplug.d/iface/99-user': ('f', '#!/bin/sh\n', 0o755), 'lib/modules/6.99/hand.ko': 'mine',
                        'etc/config/userpkg': "config main\n", 'usr/bin/userpkg': ('f', '#!', 0o755)})
            (old / 'etc/sysctl.conf').write_text('# d\nnet.ipv4.ip_forward=1\n')         # a configuration file of a package
            (old / 'etc/init.d/dnsmasq').write_text('#!/bin/sh\n# v1, edited\n')         # a script of a package
            (old / 'www/index.html').write_text('<html><!-- mine -->')
            (old / 'etc/rc.d/S50cron').unlink()                                          # a service disabled
            (old / 'etc/uci-defaults/90-mu300').unlink()                                 # ran, and removed itself
            (old / 'etc/banner').unlink()
            with open(old / 'etc/apk/world', 'a') as f:
                f.write('userpkg\n')
            with open(old / 'lib/apk/db/installed', 'a') as f:
                f.write('\nP:userpkg\nF:etc/config\nR:userpkg\nF:usr/bin\nR:userpkg\n')

        def check(old, new, report, r):
            kept = new / 'etc/mu300/update-kept/v1/user'
            self.assertIn("ipaddr '10.0.0.1'", (new / 'etc/config/network').read_text())
            self.assertEqual((new / 'etc/config/custom').read_text(), 'config x\n')
            self.assertEqual((new / 'etc/config/sqm').read_text(), 'config queue\n')         # the new package's file
            self.assertEqual((new / 'etc/crontabs/root').read_text(), '0 4 * * * reboot\n')
            self.assertEqual((new / 'www/mysite/index.html').read_text(), '<h1>')
            self.assertTrue((new / 'etc/hotplug.d/iface/99-user').exists())
            self.assertEqual((new / 'lib/modules/6.99/hand.ko').read_text(), 'mine')        # modules built by hand stay
            self.assertEqual((new / 'etc/sysctl.conf').read_text(), '# d\nnet.ipv4.ip_forward=1\n')
            # a script of a package and a page of the web interface are the release's; the edits are set aside
            self.assertEqual((new / 'etc/init.d/dnsmasq').read_text(), '#!/bin/sh\n# v2\n')
            self.assertEqual((kept / 'etc/init.d/dnsmasq').read_text(), '#!/bin/sh\n# v1, edited\n')
            self.assertEqual((new / 'www/index.html').read_text(), '<html>')
            self.assertEqual((kept / 'www/index.html').read_text(), '<html><!-- mine -->')
            self.assertFalse(os.path.lexists(new / 'etc/rc.d/S50cron'))
            self.assertTrue((new / 'etc/uci-defaults/90-mu300').exists())
            self.assertFalse((new / 'etc/banner').exists())
            # the package added on the device: installed again at the first boot, its UCI file set aside until then
            self.assertFalse((new / 'usr/bin/userpkg').exists())
            self.assertEqual((new / 'etc/mu300/user-packages/wanted').read_text(), 'userpkg\n')
            self.assertEqual((new / 'etc/mu300/orphaned-config/etc/config/userpkg').read_text(), 'config main\n')
            self.assertFalse((new / 'etc/config/userpkg').exists())
            self.assertIn('/etc/config/userpkg -> /etc/mu300/orphaned-config/etc/config/userpkg', report)
        self.each(user, check, old_image=old_image, new_image=new_image, image='openwrt', os_name='openwrt')

    def test_with_busybox_for_every_command(self):
        # as on OpenWrt, and as the update runs it: by the image's static busybox
        bb = shutil.which('busybox')
        if not bb:
            self.skipTest('no busybox')
        d = self.tmp / 'applets'
        d.mkdir()
        for a in subprocess.run([bb, '--list'], capture_output=True, text=True).stdout.split():
            if a not in ('busybox', 'sh'):
                os.symlink(bb, d / a)

        def user(old):
            build(old, {'etc/mine.conf': 'm', 'srv/new\nline': 'n', 'var/lib/app/data': 'd'})
            (old / 'etc/sysctl.conf').write_text('# mine\n')
            (old / 'usr/bin/tool').write_text('patched')
            (old / 'etc/motd').unlink()
        old, new = self.systems(user)
        r = self.sh([bb, 'sh'], f'. "{BIN}/mu300-update"; carry_system "{old}" "{new}" ubuntu copy; echo "rc=$?"',
                    MU300_LIB=1, MU300_CARRY=CARRY, PATH=f'{d}')
        self.assertIn('rc=0\n', r.stdout, r.stdout + r.stderr)
        self.assertEqual(r.stderr, '')
        self.assertEqual((new / 'etc/mine.conf').read_text(), 'm')
        self.assertEqual((new / 'srv/new\nline').read_text(), 'n')
        self.assertEqual((new / 'etc/sysctl.conf').read_text(), '# mine\n')
        self.assertEqual((new / 'etc/mu300/update-kept/v1/image/etc/sysctl.conf').read_text(), '# defaults of v2\n')
        self.assertEqual((new / 'etc/mu300/update-kept/v1/user/usr/bin/tool').read_text(), 'patched')
        self.assertFalse((new / 'etc/motd').exists())


SEC = {
    'etc/passwd': 'root:x:0:0::/root:/bin/sh\nubuntu:x:1000:1000::/home/ubuntu:/bin/bash\n',
    'etc/group': 'root:x:0:\nubuntu:x:1000:\n',
    'etc/shadow': 'root:*:1::::::\nubuntu:$6$image:1::::::\n',
    'etc/ssh/sshd_config.d/10-mu300.conf': 'PasswordAuthentication yes\n',
    'etc/sysctl.d/99-mu300-console.conf': 'kernel.printk = 4\n',
    'etc/sudoers': 'root ALL=(ALL) ALL\n',
    'etc/sudoers.d/90-image': 'ubuntu ALL=(ALL) NOPASSWD: ALL\n',
    'etc/mu300/firewall.nft': '# the rules of the image\n',
    'etc/config/firewall': "config defaults\n\toption input 'ACCEPT'\n",
    'etc/secret.conf': 'a setting\n',
    'usr/lib/systemd/system/telnet.service': '[Service]\n',
    'etc/systemd/system/multi-user.target.wants/telnet.service': ('l', '/usr/lib/systemd/system/telnet.service'),
    'opt/mu300/bin/mu300-telnetd': ('f', '#!/bin/sh\n', 0o755),
}
SEC_OLD = dict(OLD_IMAGE, **SEC)
# the new release has every one of them again, and changed most
SEC_NEW = dict(NEW_IMAGE, **SEC)
SEC_NEW.update({
    'etc/ssh/sshd_config.d/10-mu300.conf': 'PasswordAuthentication yes\n# v2\n',
    'etc/sysctl.d/99-mu300-console.conf': 'kernel.printk = 4\n# v2\n',
    'etc/sudoers': 'root ALL=(ALL) ALL\n# v2\n',
    'etc/mu300/firewall.nft': '# the rules of the image, v2\n',
    'etc/config/firewall': "config defaults\n\toption input 'ACCEPT'\n# v2\n",
    'etc/systemd/system/mu300-telnetd.service': '[Service]\n# v2\n',
    'etc/mu300/adb': 'on\n',
    'etc/sudoers.d/91-of-v2': 'ubuntu ALL=(ALL) NOPASSWD: /usr/bin/new\n',
})


@unittest.skipUnless(CC and LINUX, 'needs a C compiler on Linux')
class NeverLessProtected(CarryCase):
    """An update never leaves the device less protected than the user left it: everything the user tightened is
    planted in the old system, and the new one must be at least as tight - with the old image's manifest, and without
    one. (main's test_update asserts the adb state for the list of kept places; that update is still there, and
    still tested there.)"""

    @staticmethod
    def tighten(old):
        build(old, {'etc/mu300/adb': 'off\n', 'etc/mu300/adb_auth': 'keys\n', 'etc/mu300/adb_keys': ('f', 'QAAAAkey me@pc\n', 0o600),
                    'etc/mu300/vpn.conf': ('f', 'ENABLE=1\nKILL_SWITCH=1\n', 0o600), 'etc/mu300/mobile-data.conf': ('f', 'APN=x\nPASS=y\n', 0o600),
                    'etc/dropbear/authorized_keys': ('f', 'ssh-ed25519 AAAA only-this-one\n', 0o600),
                    'home/ubuntu/.ssh/authorized_keys': ('f', 'ssh-ed25519 AAAA only-this-one\n', 0o600),
                    'etc/nftables.d/10-user.nft': 'drop everything\n'})
        (old / 'etc/ssh/sshd_config.d/10-mu300.conf').write_text('PasswordAuthentication no\n')
        (old / 'etc/ssh/sshd_config').write_text('Port 22\nPermitRootLogin no\n')
        (old / 'etc/sysctl.d/99-mu300-console.conf').write_text('kernel.printk = 4\nkernel.dmesg_restrict = 1\n')
        (old / 'etc/sudoers').write_text('root ALL=(ALL) ALL\nDefaults timestamp_timeout=0\n')
        (old / 'etc/sudoers.d/90-image').unlink()                     # no sudo without a password
        (old / 'etc/mu300/firewall.nft').write_text('# my rules: drop\n')
        (old / 'etc/config/firewall').write_text("config defaults\n\toption input 'REJECT'\n")
        # services turned off: disabled (the link is gone), and masked (a link to /dev/null over the project's unit)
        (old / 'etc/systemd/system/multi-user.target.wants/telnet.service').unlink()
        (old / 'etc/systemd/system/multi-user.target.wants/mu300-telnetd.service').unlink()
        (old / 'etc/systemd/system/mu300-telnetd.service').unlink()
        os.symlink('/dev/null', old / 'etc/systemd/system/mu300-telnetd.service')
        # modes narrowed: a setting, a program of the release the user edited, a program of the project
        (old / 'etc/secret.conf').chmod(0o600)
        (old / 'usr/bin/tool').write_text('tool v1, patched')
        (old / 'usr/bin/tool').chmod(0o700)
        (old / 'opt/mu300/bin/mu300-telnetd').chmod(0o600)
        # the default account deleted, root locked
        (old / 'etc/passwd').write_text('root:x:0:0::/root:/bin/sh\n')
        (old / 'etc/group').write_text('root:x:0:\n')
        (old / 'etc/shadow').write_text('root:!locked:2::::::\n')

    def check(self, old, new, report, r):
        mode = lambda p: stat.S_IMODE(os.lstat(new / p).st_mode)
        # adb stays off and limited to its keys
        self.assertEqual((new / 'etc/mu300/adb').read_text(), 'off\n')
        self.assertEqual((new / 'etc/mu300/adb_auth').read_text(), 'keys\n')
        self.assertEqual(((new / 'etc/mu300/adb_keys').read_text(), mode('etc/mu300/adb_keys')), ('QAAAAkey me@pc\n', 0o600))
        # the VPN's kill switch, the settings with a password in them, with their modes
        self.assertEqual(((new / 'etc/mu300/vpn.conf').read_text(), mode('etc/mu300/vpn.conf')), ('ENABLE=1\nKILL_SWITCH=1\n', 0o600))
        self.assertEqual(mode('etc/mu300/mobile-data.conf'), 0o600)
        # ssh: the settings, also the one in the project's own drop-in, and the keys that may log in
        self.assertEqual((new / 'etc/ssh/sshd_config.d/10-mu300.conf').read_text(), 'PasswordAuthentication no\n')
        self.assertEqual((new / 'etc/ssh/sshd_config').read_text(), 'Port 22\nPermitRootLogin no\n')
        for p in ('etc/dropbear/authorized_keys', 'home/ubuntu/.ssh/authorized_keys'):
            self.assertEqual(((new / p).read_text(), mode(p)), ('ssh-ed25519 AAAA only-this-one\n', 0o600))
        # the firewall, sysctl, sudo
        self.assertEqual((new / 'etc/mu300/firewall.nft').read_text(), '# my rules: drop\n')
        self.assertIn("'REJECT'", (new / 'etc/config/firewall').read_text())
        self.assertEqual((new / 'etc/nftables.d/10-user.nft').read_text(), 'drop everything\n')
        self.assertIn('dmesg_restrict = 1', (new / 'etc/sysctl.d/99-mu300-console.conf').read_text())
        self.assertIn('timestamp_timeout=0', (new / 'etc/sudoers').read_text())
        self.assertFalse((new / 'etc/sudoers.d/90-image').exists())
        # services that were off are off: no enable link, the mask in place over the release's unit
        for p in ('etc/systemd/system/multi-user.target.wants/telnet.service', 'etc/systemd/system/multi-user.target.wants/mu300-telnetd.service'):
            self.assertFalse(os.path.lexists(new / p), p)
        self.assertEqual(os.readlink(new / 'etc/systemd/system/mu300-telnetd.service'), '/dev/null')
        # modes stay narrow, also where the release's content is the one in use - and that case is in the report
        self.assertEqual(mode('etc/secret.conf'), 0o600)
        self.assertEqual(((new / 'usr/bin/tool').read_text(), mode('usr/bin/tool')), ('tool v2', 0o700))
        self.assertEqual(mode('opt/mu300/bin/mu300-telnetd'), 0o600)
        self.assertIn('  /usr/bin/tool\n', report)
        # the account that was deleted does not come back with the image's password, and root stays locked
        shadow = (new / 'etc/shadow').read_text().splitlines()
        self.assertIn('root:!locked:2::::::', shadow)
        self.assertIn('ubuntu:!$6$image:1::::::', shadow)
        self.assertRegex(report, r'Accounts of the release that the previous system did not have are locked.*\n  ubuntu\n')

    def test_with_the_manifest(self):
        def check(old, new, report, r):
            self.check(old, new, report, r)
            # what the release added is taken (the old system never had it, and nobody removed it)
            self.assertTrue((new / 'etc/sudoers.d/91-of-v2').exists())
        self.each(self.tighten, check, old_image=SEC_OLD, new_image=SEC_NEW)

    def test_without_a_manifest(self):
        def check(old, new, report, r):
            self.check(old, new, report, r)
            self.assertIn('has no list of its files here', report)
            # what cannot be told is decided for the tighter side, and said
            self.assertFalse((new / 'etc/sudoers.d/91-of-v2').exists())
            self.assertRegex(report, r'Not taken from the new release.*\n(  /.*\n)*  /etc/sudoers\.d/91-of-v2\n')
            self.assertRegex(report, r'Left off, as in the previous system.*\n(  /.*\n)*  /etc/systemd/system/multi-user\.target\.wants/telnet\.service\n')
        self.each(self.tighten, check, old_image=SEC_OLD, new_image=SEC_NEW, manifest='none')

    def test_a_removed_package_is_back_without_its_service(self):
        # the user removed the telnet package; the image has it. Its files are back, its service is not enabled.
        new_image = dict(SEC_NEW)
        new_image.update({'var/lib/dpkg/info/telnetd.list': '/.\n/usr/lib/systemd/system/telnet.service\n',
                          'var/lib/dpkg/status': 'Package: base\nStatus: install ok installed\n\nPackage: telnetd\nStatus: install ok installed\n'})

        def user(old):
            (old / 'usr/lib/systemd/system/telnet.service').unlink()
            (old / 'etc/systemd/system/multi-user.target.wants/telnet.service').unlink()

        def check(old, new, report, r):
            self.assertTrue((new / 'usr/lib/systemd/system/telnet.service').exists())
            self.assertFalse(os.path.lexists(new / 'etc/systemd/system/multi-user.target.wants/telnet.service'))
            self.assertRegex(report, r'Packages you had removed are part of the image, and back \(their services are not enabled\):\n  telnetd\n')
        self.each(user, check, old_image=SEC_OLD, new_image=new_image)


@unittest.skipUnless(CC and LINUX, 'needs a C compiler on Linux')
class Owners(CarryCase):
    """A file belongs to an account, not to a number: old and new systems whose passwd and group disagree. The files
    of the fake old system belong to ME (who runs the tests; as root, a number given to them), so what the old and
    the new passwd say about that number is the case; as root the owners in the new system are looked at too."""

    ME = os.getuid() or 4242

    def images(self, old_names, new_names):
        """the images with passwd and group made of (name, number) pairs"""
        def files(names):
            return {'etc/passwd': ''.join(f'{n}:x:{i}:{i}::/:/bin/sh\n' for n, i in names),
                    'etc/group': ''.join(f'{n}:x:{i}:\n' for n, i in names),
                    'etc/shadow': ''.join(f'{n}:!:1::::::\n' for n, i in names)}
        return dict(OLD_IMAGE, **files(old_names)), dict(NEW_IMAGE, **files(new_names))

    def user(self, extra_accounts=()):
        def user(old):
            build(old, {'var/lib/svc/private': ('f', 'secret\n', 0o600), 'var/lib/svc/tool': ('f', '#!/bin/sh\n', 0o4755),
                        'var/lib/svc/sgid/': ('d', 0o2770), 'srv/plain': 'x'})
            os.link(old / 'var/lib/svc/private', old / 'var/lib/svc/second-name')
            if os.getuid() == 0:
                for p in ('var/lib/svc', 'var/lib/svc/private', 'var/lib/svc/tool', 'var/lib/svc/sgid', 'srv/plain'):
                    os.chown(old / p, self.ME, self.ME)
                (old / 'var/lib/svc/tool').chmod(0o4755)        # (a chown takes the bit off)
                (old / 'var/lib/svc/sgid').chmod(0o2770)
            for name, number in extra_accounts:
                with open(old / 'etc/passwd', 'a') as f:
                    f.write(f'{name}:x:{number}:{number}::/:/bin/sh\n')
                with open(old / 'etc/group', 'a') as f:
                    f.write(f'{name}:x:{number}:\n')
                with open(old / 'etc/shadow', 'a') as f:
                    f.write(f'{name}:$6$x:1::::::\n')
        return user

    def owner(self, path):
        st = os.lstat(path)
        return st.st_uid, st.st_gid

    def passwd(self, new):
        return {l.split(':')[0]: int(l.split(':')[2]) for l in (new / 'etc/passwd').read_text().splitlines()}

    def test_an_account_the_new_image_numbers_differently(self):
        # svc is ME in the old image and ME + 1 in the new one: its files go to the name. What gives a file the
        # rights of its owner does not travel to another number: the set-id bits come off, and the report says so.
        me = self.ME
        old_image, new_image = self.images([('root', 0), ('svc', me)], [('root', 0), ('svc', me + 1)])

        def check(old, new, report, r):
            self.assertEqual(self.passwd(new)['svc'], me + 1)
            self.assertEqual((new / 'var/lib/svc/private').read_text(), 'secret\n')
            self.assertEqual(stat.S_IMODE(os.lstat(new / 'var/lib/svc/private').st_mode), 0o600)
            self.assertEqual(os.lstat(new / 'var/lib/svc/private').st_ino, os.lstat(new / 'var/lib/svc/second-name').st_ino)
            self.assertEqual(stat.S_IMODE(os.lstat(new / 'var/lib/svc/tool').st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(os.lstat(new / 'var/lib/svc/sgid').st_mode), 0o770)
            self.assertRegex(report, r'Set-user-id and set-group-id bits and capabilities were taken off.*\n(  /.*\n)*  /var/lib/svc/tool\n')
            self.assertIn('  /var/lib/svc/sgid\n', report)
            if os.getuid() == 0:
                for p in ('var/lib/svc', 'var/lib/svc/private', 'var/lib/svc/tool', 'var/lib/svc/sgid', 'srv/plain'):
                    self.assertEqual(self.owner(new / p), (me + 1, me + 1), p)
        self.each(self.user(), check, old_image=old_image, new_image=new_image)

    def test_the_same_number_keeps_everything(self):
        me = self.ME
        old_image, new_image = self.images([('root', 0), ('svc', me)], [('root', 0), ('svc', me)])

        def check(old, new, report, r):
            self.assertEqual(stat.S_IMODE(os.lstat(new / 'var/lib/svc/tool').st_mode), 0o4755)
            self.assertEqual(stat.S_IMODE(os.lstat(new / 'var/lib/svc/sgid').st_mode), 0o2770)
            self.assertNotIn('were taken off', report)
        self.each(self.user(), check, old_image=old_image, new_image=new_image)

    def test_an_account_added_on_the_device_whose_number_the_new_image_took(self):
        # the user added mine with ME's number; the new image has another account under it. mine comes along under
        # a free number - a user from 1000 on, a system account below - and never shares one
        me = self.ME
        old_image, new_image = self.images([('root', 0)], [('root', 0), ('taken', me), ('alsotaken', 500)])

        def check(old, new, report, r):
            pw = self.passwd(new)
            self.assertEqual(pw['taken'], me)
            self.assertNotEqual(pw['mine'], me)
            self.assertEqual(pw['mine'] >= 1000, me >= 1000)
            self.assertEqual(pw['service'], 999)                    # the system account added on the device, of 500
            self.assertEqual(len(set(pw.values())), len(pw))
            lines = (new / 'etc/passwd').read_text().splitlines()
            self.assertIn(f'mine:x:{pw["mine"]}:{pw["mine"]}::/:/bin/sh', lines)     # its group moved with it
            self.assertIn(f'mine:x:{pw["mine"]}:', (new / 'etc/group').read_text().splitlines())
            self.assertIn('mine:$6$x:1::::::', (new / 'etc/shadow').read_text().splitlines())
            self.assertEqual((new / 'var/lib/svc/private').read_text(), 'secret\n')
            self.assertEqual(stat.S_IMODE(os.lstat(new / 'var/lib/svc/tool').st_mode), 0o755)
            if os.getuid() == 0:
                self.assertEqual(self.owner(new / 'var/lib/svc/private'), (pw['mine'], pw['mine']))
        self.each(self.user([('mine', me), ('service', 500)]), check, old_image=old_image, new_image=new_image)

    def test_a_number_without_a_name_never_becomes_another_account(self):
        # nobody is ME in the old system (the account was removed, the files stayed), and the new image has an
        # account with that number: the files would be that account's. They are not put in place, but set aside
        # where only root gets, and the report names them.
        me = self.ME
        old_image, new_image = self.images([('root', 0)], [('root', 0), ('stranger', me)])

        def check(old, new, report, r):
            kept = new / 'etc/mu300/update-kept/v1/user'
            for p in ('var/lib/svc/private', 'var/lib/svc/tool', 'srv/plain'):
                self.assertFalse(os.path.lexists(new / p), p)
            self.assertFalse((new / 'var/lib/svc').exists())
            self.assertEqual((kept / 'var/lib/svc/private').read_text(), 'secret\n')
            self.assertEqual((kept / 'srv/plain').read_text(), 'x')
            self.assertEqual(stat.S_IMODE(os.lstat(new / 'etc/mu300/update-kept').st_mode), 0o700)
            self.assertRegex(report, r'Owned by a number that no account had in the previous system.*\n(  /.*\n)*  /srv/plain\n')
            self.assertIn('  /var/lib/svc\n', report)
        if os.getuid() == 0:
            self.each(self.user(), check, old_image=old_image, new_image=new_image)
            self.each(self.user(), check, old_image=old_image, new_image=new_image, manifest='none')
        else:
            # (not root: the files of the images are ME's as well, so only what the user made is looked at)
            for shell in self.each_shell():
                old, new = self.systems(self.user(), old_image=old_image, new_image=new_image)
                r = self.run_carry(shell, old, new)
                self.assertIn('rc=0\n', r.stdout, r.stdout + r.stderr)
                check(old, new, (new / 'etc/mu300/update-report').read_text(), r)

    def test_a_number_without_a_name_that_is_free_stays_the_number(self):
        me = self.ME
        old_image, new_image = self.images([('root', 0)], [('root', 0), ('other', me + 7)])

        def check(old, new, report, r):
            self.assertEqual((new / 'var/lib/svc/private').read_text(), 'secret\n')
            self.assertEqual(stat.S_IMODE(os.lstat(new / 'var/lib/svc/tool').st_mode), 0o4755)
            self.assertNotIn('Owned by a number', report)
            if os.getuid() == 0:
                self.assertEqual(self.owner(new / 'var/lib/svc/private'), (me, me))
        self.each(self.user(), check, old_image=old_image, new_image=new_image)

    def test_the_accounts_are_checked_against_the_plan(self):
        # the files were carried for the numbers the plan gave the accounts: a new system whose passwd says
        # otherwise in the end is not put in place
        src = (BIN / 'mu300-update').read_text()
        fn = src[src.index('carry_system() {'):src.index('# report: what the last updates wrote')]
        self.assertLess(fn.index('carry_accounts_plan "$_co" "$_cn"'), fn.index('carry_plan 3 '))
        self.assertLess(fn.index('carry_plan 3 '), fn.index('"$CARRY_BIN" "$_cmode" "$_co" "$_cn" "$CARRY_T/idmap"'))
        self.assertIn('the accounts of the new system are not the ones the files were carried for', src)
        old_image, new_image = self.images([('root', 0), ('svc', 300)], [('root', 0), ('svc', 301)])
        for shell in self.each_shell():
            old, new = self.systems(lambda old: None, old_image=old_image, new_image=new_image)
            # merge_accounts stands in for anything that would leave another number than the plan's
            r = self.sh(shell, f'. "{BIN}/mu300-update"; merge_accounts() {{ sed -i "s/^svc:x:301:/svc:x:302:/" "$2/etc/passwd"; }}; '
                               f'carry_system "{old}" "{new}" ubuntu copy; echo "rc=$?"', MU300_LIB=1, MU300_CARRY=CARRY)
            self.assertIn('rc=1\n', r.stdout, r.stdout + r.stderr)
            self.assertIn('svc: 302 in the new system, the plan was 301', r.stderr)


def image_tar(path, spec, tmp):
    """a release image from SPEC with what carries everything over in it: this checkout's mu300-update, the helper
    built here, a busybox, and its manifest"""
    root = build(tmp / 'image-root', spec)
    for f, src in (('opt/mu300/bin/mu300-update', BIN / 'mu300-update'), ('opt/mu300/bin/mu300-carry', CARRY),
                   ('usr/bin/busybox', shutil.which('busybox'))):
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, root / f)
        (root / f).chmod(0o755)
    subprocess.run(['tar', '-czf', str(path), '-C', str(root), '.'], check=True)
    shutil.rmtree(root)


@unittest.skipUnless(CC and LINUX and shutil.which('busybox'), 'needs a C compiler and busybox on Linux')
class Apply(ShellTest):
    """apply_one and the installers' update with an image that brings the carry block: run from the image, by its busybox"""

    def setUp(self):
        super().setUp()
        self.disk = self.tmp / 'disk'
        self.root = self.tmp / 'root'
        (self.root / 'run/mu300').mkdir(parents=True)
        self.fresh()

    def fresh(self, with_engine=True):
        """the Linux partition before an update: the old system with what a user did to it, and the new image
        downloaded (with what carries everything over in it, or as an image built without)"""
        shutil.rmtree(self.disk, ignore_errors=True)
        self.old = build(self.disk / 'ubuntu', OLD_IMAGE)
        m = carry('manifest', self.old, 'ubuntu-24.04', 'v1')
        (self.old / 'etc/mu300/image-manifest.gz').write_bytes(gzip.compress(m.stdout.encode()))
        build(self.old, {'etc/wireguard/wg0.conf': ('f', 'key', 0o600), 'var/lib/app/db': 'data', 'opt/app/run': ('f', '#!', 0o755)})
        (self.old / 'etc/sysctl.conf').write_text('# mine\n')
        (self.disk / '.mu300-update').mkdir()
        tar = self.disk / '.mu300-update/mu300-ubuntu-rootfs.tar.gz'
        if with_engine:
            image_tar(tar, NEW_IMAGE, self.tmp)
        else:
            root = build(self.tmp / 'plain', NEW_IMAGE)
            subprocess.run(['tar', '-czf', str(tar), '-C', str(root), '.'], check=True)
            shutil.rmtree(root)

    def up(self, shell, code, **env):
        return self.sh(shell, f'. "{BIN}/mu300-update"; is_root() {{ false; }}; {code}', MU300_LIB=1, MU300_DISK=self.disk,
                       MU300_BIN=BIN, MU300_SYSROOT=self.root, **env)

    def test_apply_one_carries_everything_and_keeps_the_old_system_whole(self):
        for shell in self.each_shell():
            self.fresh()
            before = snapshot(self.old)
            r = self.up(shell, 'apply_one ubuntu v2')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            new = self.disk / 'ubuntu'
            self.assertEqual((new / 'etc/wireguard/wg0.conf').read_text(), 'key')
            self.assertEqual((new / 'var/lib/app/db').read_text(), 'data')
            self.assertEqual((new / 'opt/app/run').read_text(), '#!')
            self.assertEqual((new / 'etc/sysctl.conf').read_text(), '# mine\n')
            self.assertEqual((new / 'etc/mu300/image-version').read_text(), 'v2\n')
            self.assertIn('ubuntu: updated v1 -> v2', r.stdout)
            self.assertIn('Everything you added to the system was carried over', r.stdout)
            # the rollback copy is the old system as it was, to the last attribute
            self.assertEqual(snapshot(self.disk / 'ubuntu.old'), before)
            self.assertFalse((self.disk / 'ubuntu.new').exists())

    def test_the_old_way_stays_reachable(self):
        for shell in self.each_shell():
            # asked for: only the list of places, as before the first release with a four-field tag
            self.fresh()
            r = self.up(shell, 'apply_one ubuntu v2', MU300_UPDATE_KEEP='list')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            new = self.disk / 'ubuntu'
            self.assertFalse((new / 'etc/wireguard').exists())
            self.assertFalse((new / 'var/lib/app').exists())
            self.assertEqual((new / 'etc/hostname').read_text(), 'mu300\n')
            self.assertNotIn('carried over', r.stdout)
            self.assertTrue((self.disk / 'ubuntu.old/etc/wireguard/wg0.conf').exists())
            # and for an image that does not bring the helper, with a line that says so
            self.fresh(with_engine=False)
            r = self.up(shell, 'apply_one ubuntu v2')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('does not bring what carries everything over', r.stdout)
            self.assertFalse((self.disk / 'ubuntu/etc/wireguard').exists())

    def test_no_rollback_keeps_no_copy(self):
        for shell in self.each_shell():
            self.fresh()
            (self.disk / 'ubuntu.old').mkdir()              # the copy an earlier update kept goes too
            ino = os.lstat(self.old / 'var/lib/app/db').st_ino
            r = self.up(shell, 'NO_ROLLBACK=; CARRY_MODE=link; apply_one ubuntu v2; echo "none:$NO_ROLLBACK"')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(os.lstat(self.disk / 'ubuntu/var/lib/app/db').st_ino, ino)
            self.assertFalse((self.disk / 'ubuntu.old').exists())
            self.assertTrue((self.disk / 'ubuntu.broken/sbin/init').exists())
            self.assertIn('none: ubuntu', r.stdout)
            # no rollback is offered, and the next apply removes what is left
            r = self.up(shell, 'mounted_disk() { :; }; rollback ubuntu')
            self.assertIn('ubuntu: no previous version kept', r.stdout)
            r = self.up(shell, 'clean_broken')
            self.assertIn('ubuntu: removed the copy', r.stdout)
            self.assertFalse((self.disk / 'ubuntu.broken').exists())
            # the files are the new system's alone now
            self.assertEqual(os.lstat(self.disk / 'ubuntu/var/lib/app/db').st_nlink, 1)
            self.assertEqual((self.disk / 'ubuntu/var/lib/app/db').read_text(), 'data')

    def test_no_room_stops_before_anything_changes(self):
        self.stub('df', 'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"; echo "/dev/x 100000 99000 1000 99% /"')
        for shell in self.each_shell():
            self.fresh()
            before = snapshot(self.old)
            r = self.up(shell, 'apply_one ubuntu v2')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('nothing was changed', r.stderr)
            self.assertIn('mu300-update apply --no-rollback', r.stderr)
            self.assertRegex(r.stderr, r'\d+ MB needed, 0 MB free')
            self.assertEqual(snapshot(self.disk / 'ubuntu'), before)
            self.assertFalse((self.disk / 'ubuntu.new').exists())
            self.assertFalse((self.disk / 'ubuntu.old').exists())

    def test_the_room_an_update_wants_counts_what_the_carry_copies(self):
        # one rule (space_needed) for apply's refusal and the panel's figures, and in it what the carry block copies
        # beside the kept previous version: the old system less its image's files, as the image's manifest has them
        self.stub('curl', 'exit 7')
        self.stub('wget', 'exit 4')
        (self.old / 'var/lib/app/big').write_bytes(b'x' * (8 << 20))
        for shell in self.each_shell():
            r = self.up(shell, 'carry_need ubuntu; CARRY_MODE=link carry_need ubuntu; MU300_UPDATE_KEEP=list carry_need ubuntu; '
                               'carry_need openwrt; space_needed 2; space_needed 2 5')
            need, *rest = [int(x) for x in r.stdout.split()]
            self.assertEqual(rest, [0, 0, 0, 3000000, 3000005], r.stderr)
            self.assertGreaterEqual(need, 8192 + 32768)                         # the 8 MiB the user added, and the margin
            self.assertLess(need, 8192 * 21 // 20 + 32768 + 1024)                 # and not the image's own files
            # apply: with room for the downloads and the images but not for the copy of the user's files, it stops
            # before anything is fetched; --no-rollback copies nothing and needs no room for them
            df = 'df() { echo "Filesystem 1K-blocks Used Available Use% Mounted"; echo "/dev/x 9 9 $FREE 9% /"; }; '
            # (a MB either way: what du says of a file just written changes once the filesystem has placed it)
            r = self.up(shell, df + 'mounted_disk() { :; }; apply_options; apply $APPLY_ARGS', MU300_RELEASE='v2',
                        FREE=str(1000000 + 1000000 + need - 1024), MU300_FETCH_DELAY=0)
            self.assertNotEqual(r.returncode, 0)
            self.assertRegex(r.stderr, r'not enough free space on the Linux partition: (\d+) MB needed')
            self.assertAlmostEqual(int(re.search(r'(\d+) MB needed', r.stderr).group(1)), (2000000 + need) // 1024, delta=1)
            self.assertIn('mu300-update apply --no-rollback', r.stderr)
            self.assertTrue((self.disk / '.mu300-update/mu300-ubuntu-rootfs.tar.gz').exists())
            for args, free in (('--no-rollback', 1000000 + 1000000 + 1), ('', 1000000 + 1000000 + need + 1024)):
                r = self.up(shell, df + f'mounted_disk() {{ :; }}; apply_options {args}; apply $APPLY_ARGS', MU300_RELEASE='v2',
                            FREE=str(free), MU300_FETCH_DELAY=0)
                self.assertNotIn('not enough free space', r.stderr, args)
                self.assertIn('==> downloading v2', r.stdout, args)
            # without the manifest of the old system's image nothing is known before the new image is here: the
            # carry block counts it then (test_no_room_stops_before_anything_changes)
            (self.old / 'etc/mu300/image-manifest.gz').rename(self.tmp / 'manifest.gz')
            self.assertEqual(self.up(shell, 'carry_need ubuntu').stdout.split(), ['0'])
            (self.tmp / 'manifest.gz').rename(self.old / 'etc/mu300/image-manifest.gz')

    def test_never_back_to_an_older_release_by_itself(self):
        self.stub('curl', 'echo "network used" >&2; exit 7')
        self.stub('wget', 'echo "network used" >&2; exit 7')
        (self.old / 'etc/mu300/image-version').write_text('v2026.10.26\n')
        for shell in self.each_shell():
            # (the rule of the panel's update, downgrade_ok: one rule and one word for every source)
            for env in ({}, {'MU300_DOWNGRADE': '1'}, {'MU300_DOWNGRADE': 'yes'}):     # the zip's setting is no word here
                r = self.up(shell, 'mounted_disk() { :; }; apply_options; apply $APPLY_ARGS', MU300_RELEASE='v2026.10.23', **env)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn('ubuntu is v2026.10.26 and v2026.10.23 is older', r.stderr)
                self.assertIn('needs --downgrade, as the older release\'s updater may not carry over everything', r.stderr)
                self.assertIn('nothing was changed', r.stderr)
                self.assertNotIn('network used', r.stderr)             # refused before anything is fetched
            # asked for (apply --downgrade), a test release, or a system that does not say what it is: not held back
            for args, version in (('--downgrade', 'v2026.10.26\n'), ('', 'v2026.10.23\n'), ('', 'dev\n'), ('', 'v2026.10.20\n')):
                (self.old / 'etc/mu300/image-version').write_text(version)
                r = self.up(shell, f'mounted_disk() {{ :; }}; apply_options {args}; apply $APPLY_ARGS', MU300_RELEASE='v2026.10.23',
                            MU300_FETCH_DELAY=0)
                self.assertNotIn('is older', r.stderr, (args, version))
            (self.old / 'etc/mu300/image-version').write_text('v2026.10.26\n')


@unittest.skipUnless(CC and LINUX and shutil.which('busybox'), 'needs a C compiler and busybox on Linux')
class Installers(ShellTest):
    """The update of install.sh, install.ps1 and the Magisk zip: the install loop of tools/android-install.sh (cut out
    between its markers, as tests/test_android_install.py does) on a fake mounted Linux filesystem, with an image
    that brings the carry block."""

    def setUp(self):
        super().setUp()
        src = (TOP / 'tools/android-install.sh').read_text()
        self.blocks = ''.join(re.search(rf'# --- {b} begin\n(.*?)# --- {b} end', src, re.S).group(1)
                              for b in ('stage', 'user-packages', 'install-os'))
        self.T = self.tmp / 'T'
        self.M = self.T / 'mu300root'

    def fresh(self, spec=NEW_IMAGE):
        shutil.rmtree(self.T, ignore_errors=True)
        self.old = build(self.M / 'ubuntu', OLD_IMAGE)
        m = carry('manifest', self.old, 'ubuntu-24.04', 'v1')
        (self.old / 'etc/mu300/image-manifest.gz').write_bytes(gzip.compress(m.stdout.encode()))
        build(self.old, {'etc/wireguard/wg0.conf': ('f', 'key', 0o600), 'var/lib/app/db': 'data', 'opt/app/run': ('f', '#!', 0o755),
                         'etc/shadow': 'root:*:1::::::\nubuntu:$6$mine:2::::::\n', 'etc/passwd': 'root:x:0:0::/root:/bin/sh\nubuntu:x:1000:1000::/home/ubuntu:/bin/sh\n'})
        (self.old / 'usr/bin/tool').write_text('patched')
        # another system and the extras beside it: not this update's to touch
        build(self.M, {'openwrt/etc/config/network': 'mine\n', 'openwrt/root/file': 'x', 'extra/vpn/bin/mu300-vpn': ('f', '#!', 0o755),
                       'extra/vpn/VERSION': '2.0\n', '.mu300/boot-attempts': '5\n'})
        spec = dict(spec)
        spec.update({'etc/shadow': 'root:*:1::::::\nubuntu:$6$image:1::::::\n', 'etc/passwd': 'root:x:0:0::/root:/bin/sh\nubuntu:x:1000:1000::/home/ubuntu:/bin/sh\n'})
        image_tar(self.T / 'mu300-ubuntu.tar.gz', spec, self.tmp)

    def install(self, shell, **env):
        e = dict(OSES='ubuntu', UPDATE='1', PWHASH='', DEFAULT_LINUX='1', BOOT_OS='ubuntu', WIPE_LEGACY='0')
        e.update(env)
        pre = ('set -e\numask 022\nsay() { echo "[device] $*"; }\nsd_unmark() { :; }\nextra_keep_vpn() { :; }\nssid=; psk=\n'
               f'T="{self.T}"; M="{self.M}"\n')
        return self.sh(shell, pre + self.blocks, **e)

    def test_everything_is_carried_and_the_rest_of_the_partition_is_left_alone(self):
        for shell in self.each_shell():
            self.fresh()
            beside = {d: snapshot(self.M / d) for d in ('openwrt', 'extra', '.mu300')}
            ino = os.lstat(self.old / 'var/lib/app/db').st_ino
            r = self.install(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            new = self.M / 'ubuntu'
            self.assertEqual((new / 'etc/mu300/image-version').read_text(), 'v2\n')
            self.assertEqual((new / 'etc/wireguard/wg0.conf').read_text(), 'key')
            self.assertEqual((new / 'opt/app/run').read_text(), '#!')
            # by link: the old system is not kept on this path, so its files became the new one's, with no copy
            st = os.lstat(new / 'var/lib/app/db')
            self.assertEqual((st.st_ino, st.st_nlink), (ino, 1))
            self.assertEqual((new / 'usr/bin/tool').read_text(), 'tool v2')
            self.assertEqual((new / 'etc/mu300/update-kept/v1/user/usr/bin/tool').read_text(), 'patched')
            # the device's password stays; the output says what happened, line by line
            self.assertIn('ubuntu:$6$mine:2::::::', (new / 'etc/shadow').read_text())
            self.assertIn('[device] carrying everything of yours in the previous ubuntu over', r.stdout)
            self.assertIn('[device] Everything you added to the system was carried over', r.stdout)
            self.assertIn('[device] kept the accounts and passwords of the previous ubuntu', r.stdout)
            for d in ('ubuntu.new', 'ubuntu.broken', 'ubuntu.old', 'ubuntu/.mu300-carried', 'ubuntu/.mu300-carry'):
                self.assertFalse((self.M / d).exists(), d)
            self.assertEqual(sorted(p.name for p in self.T.iterdir()), ['mu300root'])
            self.assertFalse((self.M / 'ubuntu.new.carry-log').exists())
            # the other system, the extras and the boot settings are as they were
            for d, before in beside.items():
                after = snapshot(self.M / d)
                after.pop('boot-os', None)
                self.assertEqual(after, before, d)
            # a password the installer was given goes in after the accounts were carried over
            self.fresh()
            r = self.install(shell, PWHASH='$6$new$hash')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('ubuntu:$6$new$hash:', (self.M / 'ubuntu/etc/shadow').read_text())

    def test_the_old_way_when_asked_or_when_the_image_cannot(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.install(shell, MU300_UPDATE_KEEP='list')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse((self.M / 'ubuntu/etc/wireguard').exists())
            self.assertNotIn('carrying everything', r.stdout)
            self.assertIn('[device] kept from the previous ubuntu:', r.stdout)
            # an image without the helper: said, and the list of places is kept
            self.fresh()
            root = build(self.tmp / 'plain', NEW_IMAGE)
            subprocess.run(['tar', '-czf', str(self.T / 'mu300-ubuntu.tar.gz'), '-C', str(root), '.'], check=True)
            shutil.rmtree(root)
            r = self.install(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('does not bring what carries everything over', r.stdout)
            self.assertFalse((self.M / 'ubuntu/etc/wireguard').exists())

    def test_a_failed_carry_replaces_nothing(self):
        # the carry block ends without saying it did the work (here: its helper cannot run): the old system stays
        spec = dict(NEW_IMAGE)
        for shell in self.each_shell():
            self.fresh(spec)
            before = snapshot(self.old)
            # break the helper of the image after it was checked to be there: an update-report in the way as a directory
            build(self.old, {'etc/mu300/update-report/x': 'a directory where the report goes'})
            before = snapshot(self.old)
            r = self.install(shell)
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn('carrying your files over failed; ubuntu was not replaced', r.stdout)
            self.assertNotIn('MU300-INSTALL-OK', r.stdout)
            self.assertEqual(snapshot(self.M / 'ubuntu'), before)
            self.assertFalse((self.M / 'ubuntu.new').exists())

    def test_a_run_that_was_cut_off_is_picked_up(self):
        # the old system had been moved aside and the new one was not in place yet: the next run finds the old one
        # (its files may be the only names of the user's data) and updates it
        for shell in self.each_shell():
            self.fresh()
            (self.M / 'ubuntu').rename(self.M / 'ubuntu.broken')
            build(self.M / 'ubuntu.new', {'half': 'unpacked'})
            r = self.install(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('the system it was replacing is back in place', r.stdout)
            self.assertEqual((self.M / 'ubuntu/var/lib/app/db').read_text(), 'data')
            self.assertEqual((self.M / 'ubuntu/etc/mu300/image-version').read_text(), 'v2\n')
            self.assertFalse((self.M / 'ubuntu.broken').exists())
            self.assertFalse((self.M / 'ubuntu/half').exists())


class Wiring(unittest.TestCase):
    """What must hold for an update to carry everything over at all: the images get the helper, the manifests and
    the list of the overlay's files, a release is not published without them, and the installers' update runs the
    same carry block, from the new image."""

    def test_the_images_get_what_carries_everything_over(self):
        assemble = (TOP / 'rootfs/assemble.sh').read_text()
        owrt = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertIn('if [ -e /carry ]; then install -m755 /carry $R/opt/mu300/bin/mu300-carry; fi', assemble)
        self.assertIn('[ -e /in/carry ] && { cp /in/carry $R/opt/mu300/bin/mu300-carry;', owrt)
        for text in (assemble, owrt):
            self.assertIn('/opt/mu300/bin/mu300-carry manifest $R', text)
            # the manifest is made last: nothing in the image changes after it
            self.assertLess(text.index('mu300-carry manifest $R'), text.rindex('cd $R && tar '))
            after = text[text.index('image-manifest.gz $R/etc/mu300/image-manifest.gz'):text.rindex('cd $R && tar ')]
            self.assertEqual(after.count('\n'), 2, after)
        # each image has the old releases' manifests of its own system only
        self.assertIn('rm -f $R/opt/mu300/lib/image-manifests/openwrt*.gz', assemble)
        self.assertIn('[ "$m" = "$R/opt/mu300/lib/image-manifests/$MU300_SYSTEM.gz" ] || rm -f "$m"', owrt)
        self.assertIn('-e MU300_SYSTEM="$SYSTEM"', owrt)

    def test_a_release_is_not_published_without_them(self):
        rel = (TOP / 'tools/make-release.sh').read_text()
        self.assertIn('gcc -O2 -static -o /o/carry/mu300-carry /src/carry/mu300-carry.c', rel)
        self.assertIn('-v "$IN/tools/carry/mu300-carry":/carry:ro', rel)
        audit = rel[rel.index('echo "==> audit"'):rel.index('audit failed, nothing published')]
        for s in ('opt/mu300/bin/mu300-carry', 'etc/mu300/image-manifest.gz', 'image-manifest.py" check "$D/$a.tar.gz" || fail=1',
                  'mu300-openwrt-luci-rootfs:opt/mu300/bin/busybox:openwrt-luci'):
            self.assertIn(s, audit)
        self.assertIn('*.manifest.gz mu300-update', rel)                    # in SHA256SUMS
        self.assertEqual(rel.count('"$D"/*.manifest.gz "$D/mu300-update" "$D/mu300-release" "$D/SHA256SUMS" $SIG'), 2)     # uploaded
        inst = (TOP / 'install.sh').read_text()
        self.assertIn('gcc -O2 -static -o /o/carry/mu300-carry /src/carry/mu300-carry.c', inst)
        self.assertIn('-v "$WORK/tools/carry/mu300-carry":/carry:ro', inst)

    def test_the_installers_run_the_new_images_carry_block(self):
        ai = (TOP / 'tools/android-install.sh').read_text()
        self.assertIn("MU300_LIB='' $cbb sh $M/$os.new/opt/mu300/bin/mu300-update carry $M/$os $M/$os.new $os link", ai)
        # and a run that does not say it did the work at its end is a failed one, whatever its status
        self.assertIn('if [ $crc = 0 ] && [ ! -e $M/$os.new/.mu300-carried ]; then crc=1; fi', ai)
        # nothing of the block is copied there: the image's own runs
        self.assertNotIn('carry_plan', ai)
        self.assertNotIn('carry_system', ai)
        upd = (BIN / 'mu300-update').read_text()
        self.assertIn('MU300_LIB=\'\' "$(carry_bb "$2")" sh "$2/opt/mu300/bin/mu300-update" carry "$1" "$2" "$3" "$4" || return', upd)
        self.assertIn('[ -e "$2/.mu300-carried" ] || return 1', upd)
        # carry runs without the root check and without the lock, which the run that called it holds
        main = upd[upd.index('[ -z "${MU300_LIB:-}" ] || return 0'):]
        self.assertLess(main.index('    carry) shift; carry_system "$@"; exit $? ;;'), main.index('[ "$(id -u)" = 0 ]'))
        # (and so does the installers' count of what an update writes, docs/FINDINGS.md 131)
        self.assertLess(main.index('    delta-room) shift; delta_room "$@"; exit $? ;;'), main.index('[ "$(id -u)" = 0 ]'))
        self.assertNotIn('carry', main[main.index('apply|boot|kernel|rollback'):main.index('lock_take')])


if __name__ == '__main__':
    unittest.main()
